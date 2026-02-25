#!/usr/bin/env python3
"""Workspace GraphRAG pipeline with automatic SCIP generation per repo.

Usage:
  python run_workspace_graphrag.py \
    --repo-spec repo_a=/abs/repo-a/src=/abs/compdbs/repo-a/compile_commands.json \
    --repo-spec repo_b=/abs/repo-b/src=/abs/compdbs/repo-b/compile_commands.json \
    --recreate-graph
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

from core.structured_logging import configure_structured_logging, phase_scope, set_run_id
from core.startup_config import resolve_strict_config_validation, validate_startup_config
from core.run_artifacts import write_run_report
from graphrag.neo4j_loader import (
    clear_repo_graph,
    get_neo4j_driver,
    ingest_workspace_graph,
    init_graph_schema,
)
from graphrag.scip_index import run_scip_clang
from graphrag.scip_parser import parse_scip_index
from graphrag.workspace_catalog import build_workspace_symbol_catalog

logger = logging.getLogger(__name__)


def _parse_repo_spec(entry: str) -> tuple[str, str, str]:
    """Parse '<repo_name>=<repo_source_path>=<compdb_path>' CLI entry."""
    parts = entry.split("=", 2)
    if len(parts) != 3:
        raise ValueError(
            f"Invalid --repo-spec '{entry}'. Expected format "
            "repo_name=/path/to/source/repo=/path/to/compile_commands.json"
        )
    repo_name, source_path, compdb_path = parts
    repo_name = repo_name.strip()
    source_path = source_path.strip()
    compdb_path = compdb_path.strip()
    if not repo_name or not source_path or not compdb_path:
        raise ValueError(
            f"Invalid --repo-spec '{entry}'. Repo name, source path, and compdb path are required."
        )
    return repo_name, source_path, compdb_path


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Workspace GraphRAG: auto SCIP generation + cross-repo Neo4j ingest",
    )
    parser.add_argument(
        "--repo-spec",
        action="append",
        required=True,
        help=(
            "Repository source + compile_commands.json mapping. Repeatable. "
            "Format: <repo_name>=<abs_or_rel_source_repo_path>=<abs_or_rel_compdb_path>"
        ),
    )
    parser.add_argument(
        "--index-dir",
        default="output/workspace_scip",
        help="Directory to write generated per-repo SCIP indexes.",
    )
    parser.add_argument(
        "--jobs",
        type=int,
        default=None,
        help="Parallel jobs for scip-clang.",
    )
    parser.add_argument(
        "--recreate-graph",
        action="store_true",
        default=False,
        help="Clear existing graph nodes for listed repos before ingesting.",
    )
    parser.add_argument(
        "--strict-config",
        action="store_true",
        default=resolve_strict_config_validation(default=False),
        help=(
            "Enable strict startup config validation. "
            "Fail fast on docker-compose parse/config errors."
        ),
    )
    return parser.parse_args()


def _build_index_path(index_dir: Path, repo_name: str) -> Path:
    """Return deterministic output path for repo SCIP index."""
    safe_repo = repo_name.replace("/", "_")
    return index_dir / f"{safe_repo}.scip"


def main() -> None:
    """Workspace orchestration entrypoint."""
    configure_structured_logging(level=logging.INFO)
    args = parse_args()
    run_id = set_run_id()

    validate_startup_config(
        compose_path="infra_context/docker-compose.yml",
        required_services=("neo4j",),
        strict=args.strict_config,
    )

    mappings = [_parse_repo_spec(e) for e in args.repo_spec]
    repo_names = [name for name, _, _ in mappings]
    if len(set(repo_names)) != len(repo_names):
        raise ValueError("Duplicate repo names in --repo-spec are not allowed.")

    index_dir = Path(args.index_dir).resolve()
    index_dir.mkdir(parents=True, exist_ok=True)

    run_report: dict[str, object] = {
        "run_id": run_id,
        "pipeline": "workspace_graphrag",
        "status": "failed",
        "repos": [],
        "strict_config": args.strict_config,
    }

    try:
        repo_parse_results: list[tuple[str, object]] = []
        repo_reports: list[dict[str, object]] = []

        with phase_scope("phase1_workspace_scip"):
            for repo_name, source_raw, compdb_raw in mappings:
                source_path = Path(source_raw).resolve()
                compdb_path = Path(compdb_raw).resolve()
                if not source_path.is_dir():
                    raise FileNotFoundError(f"Source repo path not found: {source_path}")
                if not compdb_path.is_file():
                    raise FileNotFoundError(f"compile_commands.json not found: {compdb_path}")

                index_path = _build_index_path(index_dir, repo_name)
                logger.info(
                    "Generating SCIP: repo=%s source=%s compdb=%s index=%s",
                    repo_name,
                    source_path,
                    compdb_path,
                    index_path,
                )
                generated_index = run_scip_clang(
                    compdb_path=str(compdb_path),
                    index_output_path=str(index_path),
                    jobs=args.jobs,
                    project_root=str(source_path),
                )
                parse_result = parse_scip_index(generated_index, repo_name)
                repo_parse_results.append((repo_name, parse_result))
                repo_reports.append(
                    {
                        "repo_name": repo_name,
                        "source_path": str(source_path),
                        "compdb_path": str(compdb_path),
                        "index_path": generated_index,
                        "symbol_count": len(parse_result.symbols),
                        "reference_count": len(parse_result.references),
                    }
                )

        with phase_scope("phase2_workspace_ingest"):
            driver = get_neo4j_driver()
            init_graph_schema(driver)
            if args.recreate_graph:
                for repo_name, _, _ in mappings:
                    clear_repo_graph(driver, repo_name)

            # Preserve CLI order as stable precedence for conflict fallback.
            catalog = build_workspace_symbol_catalog(repo_parse_results)
            ingest_stats = ingest_workspace_graph(
                repo_parse_results=repo_parse_results,
                driver=driver,
                workspace_catalog=catalog,
            )
            driver.close()

        run_report["repos"] = repo_reports
        run_report["workspace_conflicts"] = [
            {
                "scip_symbol": c.scip_symbol,
                "owner_repo": c.owner_repo,
                "candidate_repos": c.candidate_repos,
                "reason": c.reason,
            }
            for c in catalog.conflicts
        ]
        run_report["graph_ingestion"] = ingest_stats.to_slo_report()
        run_report["status"] = "success"

        logger.info(
            "Workspace ingestion summary: %s",
            json.dumps(
                {
                    "run_id": run_id,
                    "repos": len(repo_reports),
                    "conflicts": len(catalog.conflicts),
                    "graph_ingestion": ingest_stats.to_slo_report(),
                },
                sort_keys=True,
            ),
        )
        report_path = write_run_report(run_report, run_id)
        logger.info("Run report written: %s", report_path)

    except Exception as exc:
        run_report["error"] = str(exc)
        report_path = write_run_report(run_report, run_id)
        logger.info("Run report written: %s", report_path)
        logger.error("Workspace GraphRAG failed: %s", exc, exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
