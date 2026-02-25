"""Contract tests for workspace pipeline orchestration."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from core.workspace_manifest import (
    CompdbSpec,
    Neo4jWorkspaceConfig,
    QdrantWorkspaceConfig,
    RepoSpec,
    WorkspaceManifest,
)
from graphrag.scip_parser import ScipParseResult
from run_workspace_pipeline import _final_status, _merge_parse_results, execute_workspace_pipeline


class _FakeNeo4jDriver:
    def close(self) -> None:
        return None


class TestWorkspacePipelineContracts(unittest.TestCase):
    def _manifest(self) -> WorkspaceManifest:
        return WorkspaceManifest(
            workspace_name="demo",
            repo_cache_dir="output/workspace_repos",
            index_dir="output/workspace_scip",
            entities_dir="output/workspace_entities",
            qdrant=QdrantWorkspaceConfig(recreate_collection=False, collection_name="code_embeddings"),
            neo4j=Neo4jWorkspaceConfig(recreate_graph=False),
            repos=[
                RepoSpec(
                    repo_name="repo-a",
                    git_url="https://example/repo-a.git",
                    ref="main",
                    token_env="TOKEN_A",
                    compdb_paths=[CompdbSpec(path="build/compile_commands.json")],
                ),
                RepoSpec(
                    repo_name="repo-b",
                    git_url="https://example/repo-b.git",
                    ref="main",
                    token_env="TOKEN_B",
                    compdb_paths=[CompdbSpec(path="build/compile_commands.json")],
                ),
            ],
        )

    def test_merge_parse_results(self) -> None:
        r1 = ScipParseResult([], [], 2, 1, 3, 4)
        r2 = ScipParseResult([], [], 5, 2, 6, 7)
        merged = _merge_parse_results([r1, r2])
        self.assertEqual(merged.document_count, 7)
        self.assertEqual(merged.external_symbol_count, 3)
        self.assertEqual(merged.dropped_symbol_count, 9)
        self.assertEqual(merged.dropped_reference_count, 11)

    def test_final_status(self) -> None:
        self.assertEqual(_final_status(2, 0), "failed")
        self.assertEqual(_final_status(2, 1), "partial_success")
        self.assertEqual(_final_status(2, 2), "success")

    @patch("run_workspace_pipeline.validate_startup_config")
    @patch("run_workspace_pipeline.get_qdrant_client")
    @patch("run_workspace_pipeline.init_collection")
    @patch("run_workspace_pipeline.get_neo4j_driver")
    @patch("run_workspace_pipeline.init_graph_schema")
    @patch("run_workspace_pipeline._process_repo")
    @patch("run_workspace_pipeline.build_workspace_symbol_catalog")
    @patch("run_workspace_pipeline.ingest_workspace_graph")
    def test_partial_success_repo_failures_continue(
        self,
        mock_ingest_graph,
        mock_build_catalog,
        mock_process_repo,
        mock_init_schema,
        mock_get_neo4j,
        mock_init_collection,
        mock_get_qdrant,
        mock_validate,
    ) -> None:
        del mock_validate, mock_init_collection, mock_get_qdrant, mock_init_schema
        mock_get_neo4j.return_value = _FakeNeo4jDriver()
        parse = ScipParseResult([], [], 1, 0)
        mock_process_repo.side_effect = [
            ({"repo_name": "repo-a", "status": "success"}, parse),
            RuntimeError("fetch failed"),
        ]
        mock_build_catalog.return_value = type("Catalog", (), {"conflicts": []})()
        mock_ingest_graph.return_value = type(
            "Stats",
            (),
            {"to_slo_report": lambda self: {"nodes_created": 0, "edges_created": 0}},
        )()

        result = execute_workspace_pipeline(
            manifest=self._manifest(),
            jobs=None,
            strict_config=False,
            update_submodules=False,
            continue_on_repo_error=True,
        )

        self.assertEqual(result["status"], "partial_success")
        self.assertEqual(len(result["repos"]), 2)
        self.assertEqual(mock_ingest_graph.call_count, 1)

    @patch("run_workspace_pipeline.validate_startup_config")
    @patch("run_workspace_pipeline.get_qdrant_client")
    @patch("run_workspace_pipeline.init_collection")
    @patch("run_workspace_pipeline.get_neo4j_driver")
    @patch("run_workspace_pipeline.init_graph_schema")
    @patch("run_workspace_pipeline._process_repo")
    @patch("run_workspace_pipeline.build_workspace_symbol_catalog")
    @patch("run_workspace_pipeline.ingest_workspace_graph")
    def test_graph_ingest_called_once_globally(
        self,
        mock_ingest_graph,
        mock_build_catalog,
        mock_process_repo,
        mock_init_schema,
        mock_get_neo4j,
        mock_init_collection,
        mock_get_qdrant,
        mock_validate,
    ) -> None:
        del mock_validate, mock_init_collection, mock_get_qdrant, mock_init_schema
        mock_get_neo4j.return_value = _FakeNeo4jDriver()
        parse = ScipParseResult([], [], 1, 0)
        mock_process_repo.side_effect = [
            ({"repo_name": "repo-a", "status": "success"}, parse),
            ({"repo_name": "repo-b", "status": "success"}, parse),
        ]
        mock_build_catalog.return_value = type("Catalog", (), {"conflicts": []})()
        mock_ingest_graph.return_value = type(
            "Stats",
            (),
            {"to_slo_report": lambda self: {"nodes_created": 1, "edges_created": 1}},
        )()

        result = execute_workspace_pipeline(
            manifest=self._manifest(),
            jobs=None,
            strict_config=False,
            update_submodules=False,
            continue_on_repo_error=True,
        )

        self.assertEqual(result["status"], "success")
        self.assertEqual(mock_ingest_graph.call_count, 1)


if __name__ == "__main__":
    unittest.main()
