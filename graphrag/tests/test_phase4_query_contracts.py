"""Phase 4 tests: query API contract stabilization and result metadata."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from graphrag.query import (
    AffectedEntity,
    calculate_blast_radius,
    fetch_qdrant_documents_for_affected_entities,
    fetch_qdrant_documents_for_identity_keys,
    get_entity_neighbors,
    get_inheritance_tree,
)


class _FakeResult:
    def __init__(self, records: list[dict]):
        self._records = records

    def __iter__(self):
        return iter(self._records)

    def single(self):
        if not self._records:
            return None
        return self._records[0]


class _FakeSession:
    def __init__(self, shared_responses: list[list[dict]], calls: list[dict]):
        self._shared_responses = shared_responses
        self._calls = calls

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return None

    def run(self, query, **params):
        query_text = getattr(query, "text", str(query))
        query_timeout = getattr(query, "timeout", None)
        self._calls.append(
            {"query": query_text, "params": params, "timeout": query_timeout}
        )
        if not self._shared_responses:
            raise AssertionError("No queued fake response for run() call")
        return _FakeResult(self._shared_responses.pop(0))


class _FakeDriver:
    def __init__(self, responses: list[list[dict]]):
        self._responses = list(responses)
        self.calls: list[dict] = []

    def session(self):
        return _FakeSession(self._responses, self.calls)


class TestPhase4BlastRadiusContract(unittest.TestCase):
    def test_missing_root_returns_typed_status(self) -> None:
        driver = _FakeDriver(responses=[[]])
        result = calculate_blast_radius("repo::f.cpp::Function::run", driver)
        self.assertEqual(result.metadata.status, "missing_root")
        self.assertEqual(result.total_count, 0)

    def test_ambiguous_global_uri_returns_candidates(self) -> None:
        driver = _FakeDriver(
            responses=[
                [
                    {
                        "node_id": "n1",
                        "identity_key": "repo::f.cpp::Function::run::sig_a1",
                        "global_uri": "repo::f.cpp::Function::run",
                    },
                    {
                        "node_id": "n2",
                        "identity_key": "repo::f.cpp::Function::run::sig_b2",
                        "global_uri": "repo::f.cpp::Function::run",
                    },
                ],
            ]
        )
        result = calculate_blast_radius("repo::f.cpp::Function::run", driver)
        self.assertEqual(result.metadata.status, "ambiguous_root")
        self.assertEqual(result.metadata.reason, "ambiguous_global_uri")
        self.assertEqual(
            result.metadata.ambiguous_candidates,
            [
                "repo::f.cpp::Function::run::sig_a1",
                "repo::f.cpp::Function::run::sig_b2",
            ],
        )
        self.assertEqual(result.total_count, 0)

    def test_empty_result_returns_typed_status(self) -> None:
        driver = _FakeDriver(
            responses=[
                [
                    {
                        "node_id": "root",
                        "identity_key": "repo::f.cpp::Function::run::sig_0",
                        "global_uri": "repo::f.cpp::Function::run",
                    }
                ],  # root lookup
                [],  # BFS expansion depth 1
            ]
        )
        result = calculate_blast_radius("repo::f.cpp::Function::run", driver)
        self.assertEqual(result.metadata.status, "empty_result")
        self.assertEqual(result.metadata.reason, "no_matching_paths")
        self.assertEqual(result.total_count, 0)

    def test_pagination_sets_next_cursor_and_parses_cursor(self) -> None:
        driver = _FakeDriver(
            responses=[
                [
                    {
                        "node_id": "root",
                        "identity_key": "repo::f.cpp::Function::run::sig_0",
                        "global_uri": "repo::f.cpp::Function::run",
                    }
                ],
                [
                    {
                        "node_id": "na",
                        "identity_key": "repo::a.cpp::Function::A",
                        "uri": "repo::a.cpp::Function::A",
                        "scip_symbol": "a",
                        "type": "Function",
                        "name": "A",
                        "file": "a.cpp",
                        "repo_name": "repo",
                    }
                ],
                [
                    {
                        "node_id": "nb",
                        "identity_key": "repo::b.cpp::Function::B",
                        "uri": "repo::b.cpp::Function::B",
                        "scip_symbol": "b",
                        "type": "Function",
                        "name": "B",
                        "file": "b.cpp",
                        "repo_name": "repo",
                    },
                    {
                        "node_id": "nc",
                        "identity_key": "repo::c.cpp::Function::C",
                        "uri": "repo::c.cpp::Function::C",
                        "scip_symbol": "c",
                        "type": "Function",
                        "name": "C",
                        "file": "c.cpp",
                        "repo_name": "repo",
                    },
                ],
                [
                    {"node_id": "na", "chain": ["CALLS"]},
                    {"node_id": "nb", "chain": ["CALLS", "CALLS"]},
                ],
            ]
        )
        first_page = calculate_blast_radius(
            "repo::f.cpp::Function::run",
            driver,
            max_results=2,
            max_depth=2,
        )
        self.assertEqual(first_page.total_count, 2)
        self.assertEqual(first_page.metadata.next_cursor, "2|repo::b.cpp::Function::B")
        self.assertEqual(first_page.affected_entities[0].relationship_chain, ["CALLS"])
        self.assertEqual(first_page.affected_entities[1].relationship_chain, ["CALLS", "CALLS"])
        self.assertIn("UNWIND $frontier", driver.calls[1]["query"])
        self.assertNotIn("MATCH path =", driver.calls[1]["query"])
        self.assertIn("shortestPath(", driver.calls[3]["query"])

        cursor_driver = _FakeDriver(
            responses=[
                [
                    {
                        "node_id": "root",
                        "identity_key": "repo::f.cpp::Function::run::sig_0",
                        "global_uri": "repo::f.cpp::Function::run",
                    }
                ],
                [
                    {
                        "node_id": "na",
                        "identity_key": "repo::a.cpp::Function::A",
                        "uri": "repo::a.cpp::Function::A",
                        "scip_symbol": "a",
                        "type": "Function",
                        "name": "A",
                        "file": "a.cpp",
                        "repo_name": "repo",
                    }
                ],
                [
                    {
                        "node_id": "nb",
                        "identity_key": "repo::b.cpp::Function::B",
                        "uri": "repo::b.cpp::Function::B",
                        "scip_symbol": "b",
                        "type": "Function",
                        "name": "B",
                        "file": "b.cpp",
                        "repo_name": "repo",
                    },
                    {
                        "node_id": "nc",
                        "identity_key": "repo::c.cpp::Function::C",
                        "uri": "repo::c.cpp::Function::C",
                        "scip_symbol": "c",
                        "type": "Function",
                        "name": "C",
                        "file": "c.cpp",
                        "repo_name": "repo",
                    },
                ],
                [
                    {"node_id": "nc", "chain": ["CALLS", "CALLS"]},
                ],
            ]
        )
        next_page = calculate_blast_radius(
            "repo::f.cpp::Function::run",
            cursor_driver,
            cursor=first_page.metadata.next_cursor,
            max_depth=2,
        )
        self.assertEqual(next_page.total_count, 1)
        self.assertEqual(next_page.affected_entities[0].identity_key, "repo::c.cpp::Function::C")

    def test_identity_key_selector_is_supported(self) -> None:
        driver = _FakeDriver(
            responses=[
                [
                    {
                        "node_id": "root",
                        "identity_key": "repo::f.cpp::Function::run::sig_deadbeef",
                        "global_uri": "repo::f.cpp::Function::run",
                    }
                ],
                [],
            ]
        )
        calculate_blast_radius(
            None,
            driver,
            identity_key="repo::f.cpp::Function::run::sig_deadbeef",
        )
        self.assertEqual(
            driver.calls[0]["params"]["entity_id"],
            "repo::f.cpp::Function::run::sig_deadbeef",
        )
        self.assertIn("start.identity_key = $entity_id", driver.calls[0]["query"])
        self.assertNotIn("LIMIT 1", driver.calls[0]["query"])

    def test_scip_symbol_selector_is_supported(self) -> None:
        driver = _FakeDriver(
            responses=[
                [
                    {
                        "node_id": "root",
                        "identity_key": "repo::f.cpp::Function::run::sig_deadbeef",
                        "global_uri": "repo::f.cpp::Function::run",
                    }
                ],
                [],
            ]
        )
        calculate_blast_radius(
            None,
            driver,
            scip_symbol="cxx . . $ YAML/add(aaaa).",
            owner_repo="repo",
        )
        self.assertEqual(driver.calls[0]["params"]["scip_symbol"], "cxx . . $ YAML/add(aaaa).")
        self.assertEqual(driver.calls[0]["params"]["owner_repo"], "repo")
        self.assertIn("start.scip_symbol = $scip_symbol", driver.calls[0]["query"])

    def test_invalid_cursor_raises(self) -> None:
        driver = _FakeDriver(responses=[])
        with self.assertRaises(ValueError):
            calculate_blast_radius(
                "repo::f.cpp::Function::run",
                driver,
                cursor="bad-cursor",
            )

    def test_invalid_relationship_type_raises(self) -> None:
        driver = _FakeDriver(responses=[])
        with self.assertRaises(ValueError):
            calculate_blast_radius(
                "repo::f.cpp::Function::run",
                driver,
                relationship_types=["BAD_REL"],
            )


class TestPhase4NeighborContract(unittest.TestCase):
    def test_neighbors_default_to_entity_only(self) -> None:
        driver = _FakeDriver(
            responses=[
                [{"node_id": "root", "identity_key": "repo::f.cpp::Function::run", "global_uri": "repo::f.cpp::Function::run"}],  # root
                [{"identity_key": "repo::a.cpp::Function::A", "uri": "repo::a.cpp::Function::A", "type": "Function", "relationship": "CALLS"}],
                [{"identity_key": "repo::b.h::Class::B", "uri": "repo::b.h::Class::B", "type": "Class", "relationship": "USES_TYPE"}],
            ]
        )
        result = get_entity_neighbors("repo::f.cpp::Function::run", driver)
        self.assertEqual(len(result["inbound"]), 1)
        self.assertEqual(len(result["outbound"]), 1)
        self.assertEqual(result["metadata"].status, "ok")
        self.assertIn("MATCH (src:Entity)-[r]->(start)", driver.calls[1]["query"])
        self.assertIn("MATCH (start)-[r]->(tgt:Entity)", driver.calls[2]["query"])

    def test_neighbors_can_include_non_entity_nodes(self) -> None:
        driver = _FakeDriver(
            responses=[
                [{"node_id": "root", "identity_key": "repo::f.cpp::Function::run", "global_uri": "repo::f.cpp::Function::run"}],
                [],
                [],
            ]
        )
        get_entity_neighbors(
            "repo::f.cpp::Function::run",
            driver,
            include_non_entity=True,
        )
        self.assertIn("MATCH (src)-[r]->(start)", driver.calls[1]["query"])
        self.assertIn("MATCH (start)-[r]->(tgt)", driver.calls[2]["query"])

    def test_neighbors_missing_root_returns_status(self) -> None:
        driver = _FakeDriver(responses=[[]])
        result = get_entity_neighbors("repo::f.cpp::Function::run", driver)
        self.assertEqual(result["metadata"].status, "missing_root")
        self.assertEqual(result["inbound"], [])
        self.assertEqual(result["outbound"], [])

    def test_neighbors_ambiguous_root_returns_candidates(self) -> None:
        driver = _FakeDriver(
            responses=[
                [
                    {
                        "node_id": "n1",
                        "identity_key": "repo::f.cpp::Function::run::sig_a1",
                        "global_uri": "repo::f.cpp::Function::run",
                    },
                    {
                        "node_id": "n2",
                        "identity_key": "repo::f.cpp::Function::run::sig_b2",
                        "global_uri": "repo::f.cpp::Function::run",
                    },
                ],
            ]
        )
        result = get_entity_neighbors("repo::f.cpp::Function::run", driver)
        self.assertEqual(result["metadata"].status, "ambiguous_root")
        self.assertEqual(
            result["metadata"].ambiguous_candidates,
            [
                "repo::f.cpp::Function::run::sig_a1",
                "repo::f.cpp::Function::run::sig_b2",
            ],
        )
        self.assertEqual(result["inbound"], [])
        self.assertEqual(result["outbound"], [])


class TestPhase4InheritanceContract(unittest.TestCase):
    def test_inheritance_missing_root_returns_status(self) -> None:
        driver = _FakeDriver(responses=[[]])
        result = get_inheritance_tree("repo::x.h::Class::Node", driver)
        self.assertEqual(result["metadata"].status, "missing_root")
        self.assertEqual(result["ancestors"], [])
        self.assertEqual(result["descendants"], [])

    def test_inheritance_queries_use_deterministic_order(self) -> None:
        driver = _FakeDriver(
            responses=[
                [{"node_id": "root", "identity_key": "repo::x.h::Class::Node", "global_uri": "repo::x.h::Class::Node"}],  # root
                [{"uri": "repo::a.h::Class::Base", "identity_key": "repo::a.h::Class::Base"}],  # ancestors
                [{"uri": "repo::z.h::Class::Derived", "identity_key": "repo::z.h::Class::Derived"}],  # descendants
            ]
        )
        result = get_inheritance_tree("repo::x.h::Class::Node", driver)
        self.assertEqual(result["metadata"].status, "ok")
        self.assertIn("ORDER BY depth ASC, identity_key ASC", driver.calls[1]["query"])
        self.assertIn("ORDER BY depth ASC, identity_key ASC", driver.calls[2]["query"])


class TestPhase4JoinContract(unittest.TestCase):
    @patch("ingestion.qdrant_loader.fetch_documents_by_identity_keys")
    def test_join_fetch_uses_identity_keys(self, mock_fetch) -> None:
        mock_fetch.return_value = {
            "repo::a.cpp::Function::A::sig_1": {"identity_key": "repo::a.cpp::Function::A::sig_1"}
        }
        result = fetch_qdrant_documents_for_identity_keys(
            identity_keys=["repo::a.cpp::Function::A::sig_1"],
            qdrant_client=object(),
            collection_name="code_entities",
        )
        self.assertIn("repo::a.cpp::Function::A::sig_1", result)
        kwargs = mock_fetch.call_args.kwargs
        self.assertEqual(
            kwargs["identity_keys"],
            ["repo::a.cpp::Function::A::sig_1"],
        )

    @patch("ingestion.qdrant_loader.fetch_documents_by_identity_keys")
    def test_join_fetch_from_affected_entities_uses_identity_key_not_global_uri(self, mock_fetch) -> None:
        mock_fetch.return_value = {}
        entities = [
            AffectedEntity(
                identity_key="repo::math.cpp::Function::add::sig_a1",
                global_uri="repo::math.cpp::Function::add",
                scip_symbol="cxx . . $ add(a1).",
                entity_type="Function",
                entity_name="add",
                file_path="math.cpp",
                depth=1,
                relationship_chain=["CALLS"],
            ),
            AffectedEntity(
                identity_key="repo::math.cpp::Function::add::sig_b2",
                global_uri="repo::math.cpp::Function::add",
                scip_symbol="cxx . . $ add(b2).",
                entity_type="Function",
                entity_name="add",
                file_path="math.cpp",
                depth=1,
                relationship_chain=["CALLS"],
            ),
        ]
        fetch_qdrant_documents_for_affected_entities(
            affected_entities=entities,
            qdrant_client=object(),
            collection_name="code_entities",
        )
        kwargs = mock_fetch.call_args.kwargs
        self.assertEqual(
            kwargs["identity_keys"],
            [
                "repo::math.cpp::Function::add::sig_a1",
                "repo::math.cpp::Function::add::sig_b2",
            ],
        )


if __name__ == "__main__":
    unittest.main()
