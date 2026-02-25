#!/usr/bin/env python3
"""End-to-end workspace indexing pipeline.

Git fetch -> checkout ref -> extraction/Qdrant + SCIP/Neo4j.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Optional

from core.git_source import sync_repo
from core.run_artifacts import write_run_report
from core.structured_logging import configure_structured_logging, phase_scope, set_run_id
from core.startup_config import resolve_strict_config_validation, validate_startup_config
from core.workspace_manifest import (
    RepoSpec,
    WorkspaceManifest,
    load_workspace_manifest,
    resolve_compdb_path,
)
from extraction.extractor import iter_extract_to_dict_list
from graphrag.neo4j_loader import (
    clear_repo_graph,
    get_neo4j_driver,
    ingest_workspace_graph,
    init_graph_schema,
)
from graphrag.scip_index import run_scip_clang
from graphrag.scip_parser import ScipParseResult, parse_scip_index
from graphrag.workspace_catalog import build_workspace_symbol_catalog
from ingestion.config import DEFAULT_COLLECTION_NAME
from ingestion.qdrant_loader import get_qdrant_client, ingest_from_jsonl, init_collection

logger = logging.getLogger(__name__)


def _merge_parse_results(results: list[ScipParseResult]) -> ScipParseResult:
    """Merge multiple SCIP parse results into one logical repo parse result."""
    symbols = []
    references = []
    doc_count = 0
    external_count = 0
    dropped_syms = 0
    dropped_refs = 0
    for res in results:
        symbols.extend(res.symbols)
        references.extend(res.references)
        doc_count += res.document_count
        external_count += res.external_symbol_count
        dropped_syms += res.dropped_symbol_count
        dropped_refs += res.dropped_reference_count
    return ScipParseResult(
        symbols=symbols,
        references=references,
        document_count=doc_count,
        external_symbol_count=external_count,
        dropped_symbol_count=dropped_syms,
        dropped_reference_count=dropped_refs,
    )


def _final_status(total_enabled: int, succeeded: int) -> str:
    if succeeded <= 0:
        return "failed"
    if succeeded < total_enabled:
        return "partial_success"
    return "success"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Workspace pipeline: git sync + extraction/Qdrant + SCIP/Neo4j",
    )
    parser.add_argument(
        "--manifest-path",
        required=True,
        help="Path to workspace manifest YAML/JSON.",
    )
    parser.add_argument(
        "--update-submodules",
        action="store_true",
        default=False,
        help="Initialize/update git submodules for each repository.",
    )
    parser.add_argument(
        "--jobs",
        type=int,
        default=None,
        help="Parallel jobs for scip-clang.",
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
    parser.add_argument(
        "--continue-on-repo-error",
        action="store_true",
        default=True,
        help="Continue processing other repos when one repo fails (default: true).",
    )
    parser.add_argument(
        "--fail-fast",
        action="store_false",
        dest="continue_on_repo_error",
        help="Abort immediately on first repo failure.",
    )
    return parser.parse_args()


def _write_entities_jsonl(source_root: Path, repo_root: Path, repo_name: str, out_file: Path) -> int:
    out_file.parent.mkdir(parents=True, exist_ok=True)
    line_count = 0
    with out_file.open("w", encoding="utf-8") as f:
        for entity in iter_extract_to_dict_list(
            source=str(source_root),
            repo_name=repo_name,
            repo_root=str(repo_root),
            continue_on_error=True,
        ):
            f.write(json.dumps(entity, ensure_ascii=False) + "\n")
            line_count += 1
    return line_count


def _process_repo(
    *,
    spec: RepoSpec,
    manifest: WorkspaceManifest,
    jobs: Optional[int],
    update_submodules: bool,
    qdrant_client: Any,
    qdrant_collection: str,
) -> tuple[dict[str, Any], Optional[ScipParseResult]]:
    """Process one repository and return report + optional graph parse result."""
    repo_report: dict[str, Any] = {
        "repo_name": spec.repo_name,
        "status": "failed",
        "run_vector": spec.run_vector,
        "run_graph": spec.run_graph,
    }

    checkout = sync_repo(
        spec=spec,
        cache_root=manifest.repo_cache_dir,
        update_submodules=update_submodules,
    )
    repo_dir = Path(checkout.repo_dir)
    source_root = (repo_dir / spec.source_subdir).resolve()
    if not source_root.is_dir():
        raise FileNotFoundError(
            f"source_subdir does not exist for repo '{spec.repo_name}': {source_root}"
        )

    repo_report["checkout"] = {
        "repo_dir": str(repo_dir),
        "source_root": str(source_root),
        "ref": checkout.ref,
        "commit_sha": checkout.commit_sha,
        "cloned": checkout.cloned,
    }

    if spec.run_vector:
        entities_file = Path(manifest.entities_dir).resolve() / f"{spec.repo_name}.jsonl"
        with phase_scope(f"{spec.repo_name}_extract"):
            lines = _write_entities_jsonl(
                source_root=source_root,
                repo_root=repo_dir,
                repo_name=spec.repo_name,
                out_file=entities_file,
            )
        repo_report["vector"] = {
            "entities_file": str(entities_file),
            "entities_serialized": lines,
        }
        if lines > 0:
            with phase_scope(f"{spec.repo_name}_qdrant_ingest"):
                stats = ingest_from_jsonl(
                    file_path=str(entities_file),
                    client=qdrant_client,
                    collection_name=qdrant_collection,
                )
            repo_report["vector"]["qdrant"] = stats.to_slo_report()
        else:
            repo_report["vector"]["qdrant"] = {"status": "skipped_no_entities"}

    parse_result: Optional[ScipParseResult] = None
    if spec.run_graph:
        parse_results: list[ScipParseResult] = []
        index_root = Path(manifest.index_dir).resolve() / spec.repo_name
        index_root.mkdir(parents=True, exist_ok=True)
        compdb_reports: list[dict[str, Any]] = []
        for idx, compdb in enumerate(spec.compdb_paths):
            compdb_path = resolve_compdb_path(repo_dir, compdb).resolve()
            if not compdb_path.is_file():
                raise FileNotFoundError(
                    f"compile_commands.json not found for repo '{spec.repo_name}': {compdb_path}"
                )
            index_path = index_root / f"index_{idx}.scip"
            with phase_scope(f"{spec.repo_name}_scip_index_{idx}"):
                generated = run_scip_clang(
                    compdb_path=str(compdb_path),
                    index_output_path=str(index_path),
                    jobs=jobs,
                    project_root=str(source_root),
                )
            with phase_scope(f"{spec.repo_name}_scip_parse_{idx}"):
                parsed = parse_scip_index(generated, spec.repo_name)
            parse_results.append(parsed)
            compdb_reports.append(
                {
                    "compdb_path": str(compdb_path),
                    "index_path": generated,
                    "symbol_count": len(parsed.symbols),
                    "reference_count": len(parsed.references),
                }
            )
        parse_result = _merge_parse_results(parse_results)
        repo_report["graph"] = {
            "compdbs": compdb_reports,
            "merged_symbol_count": len(parse_result.symbols),
            "merged_reference_count": len(parse_result.references),
        }

    repo_report["status"] = "success"
    return repo_report, parse_result


def execute_workspace_pipeline(
    *,
    manifest: WorkspaceManifest,
    jobs: Optional[int],
    strict_config: bool,
    update_submodules: bool,
    continue_on_repo_error: bool,
) -> dict[str, Any]:
    """Execute end-to-end pipeline from manifest."""
    validate_startup_config(
        compose_path="infra_context/docker-compose.yml",
        required_services=("qdrant", "neo4j"),
        strict=strict_config,
    )

    enabled_repos = [r for r in manifest.repos if r.enabled]
    run_report: dict[str, Any] = {
        "workspace_name": manifest.workspace_name,
        "repos": [],
        "workspace_conflicts": [],
        "status": "failed",
    }

    qdrant_client = None
    qdrant_collection = manifest.qdrant.collection_name or DEFAULT_COLLECTION_NAME
    if any(r.run_vector for r in enabled_repos):
        qdrant_client = get_qdrant_client(strict_config=strict_config)
        init_collection(
            qdrant_client,
            collection_name=qdrant_collection,
            recreate=manifest.qdrant.recreate_collection,
        )

    neo4j_driver = None
    if any(r.run_graph for r in enabled_repos):
        neo4j_driver = get_neo4j_driver()
        init_graph_schema(neo4j_driver)
        if manifest.neo4j.recreate_graph:
            for repo in enabled_repos:
                clear_repo_graph(neo4j_driver, repo.repo_name)

    succeeded = 0
    graph_inputs: list[tuple[str, ScipParseResult]] = []
    for spec in enabled_repos:
        try:
            repo_report, parse_result = _process_repo(
                spec=spec,
                manifest=manifest,
                jobs=jobs,
                update_submodules=update_submodules,
                qdrant_client=qdrant_client,
                qdrant_collection=qdrant_collection,
            )
            run_report["repos"].append(repo_report)
            succeeded += 1
            if parse_result is not None:
                graph_inputs.append((spec.repo_name, parse_result))
        except Exception as exc:
            run_report["repos"].append(
                {
                    "repo_name": spec.repo_name,
                    "status": "failed",
                    "error": str(exc),
                }
            )
            logger.error("Repo processing failed: repo=%s error=%s", spec.repo_name, exc, exc_info=True)
            if not continue_on_repo_error:
                break

    if graph_inputs and neo4j_driver is not None:
        with phase_scope("workspace_graph_ingest"):
            catalog = build_workspace_symbol_catalog(graph_inputs)
            graph_stats = ingest_workspace_graph(
                repo_parse_results=graph_inputs,
                driver=neo4j_driver,
                workspace_catalog=catalog,
            )
        run_report["graph_ingestion"] = graph_stats.to_slo_report()
        run_report["workspace_conflicts"] = [
            {
                "scip_symbol": c.scip_symbol,
                "owner_repo": c.owner_repo,
                "candidate_repos": c.candidate_repos,
                "reason": c.reason,
            }
            for c in catalog.conflicts
        ]

    if neo4j_driver is not None:
        neo4j_driver.close()

    run_report["status"] = _final_status(len(enabled_repos), succeeded)
    return run_report


def main() -> None:
    configure_structured_logging(level=logging.INFO)
    args = parse_args()
    run_id = set_run_id()
    os.environ["STRICT_CONFIG_VALIDATION"] = "true" if args.strict_config else "false"

    run_report = {
        "run_id": run_id,
        "pipeline": "workspace_pipeline",
        "status": "failed",
    }
    try:
        manifest = load_workspace_manifest(args.manifest_path)
        result = execute_workspace_pipeline(
            manifest=manifest,
            jobs=args.jobs,
            strict_config=args.strict_config,
            update_submodules=args.update_submodules,
            continue_on_repo_error=args.continue_on_repo_error,
        )
        run_report.update(result)
        report_path = write_run_report(run_report, run_id)
        logger.info("Run report written: %s", report_path)
        if run_report["status"] == "failed":
            sys.exit(1)
    except Exception as exc:
        run_report["error"] = str(exc)
        report_path = write_run_report(run_report, run_id)
        logger.info("Run report written: %s", report_path)
        logger.error("Workspace pipeline failed: %s", exc, exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
