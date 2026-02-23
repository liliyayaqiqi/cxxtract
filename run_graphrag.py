#!/usr/bin/env python3
"""
GraphRAG Pipeline Orchestrator — SCIP Indexing + Neo4j Graph Ingestion.

Three-phase pipeline:
1. SCIP Indexing: Run scip-clang to generate index.scip from compile_commands.json
2. Parse & Ingest: Parse SCIP index, map symbols to Global URIs, load into Neo4j
3. Verify: Run blast radius query on a sample entity

Usage:
    python run_graphrag.py --compdb-path build/compile_commands.json --repo-name my-project
    python run_graphrag.py --compdb-path build/compile_commands.json --repo-name yaml-cpp --skip-indexing
    python run_graphrag.py --compdb-path build/compile_commands.json --repo-name yaml-cpp --recreate-graph
"""

import argparse
import json
import logging
import os
import sys
import time

from core.structured_logging import (
    configure_structured_logging,
    phase_scope,
    set_run_id,
)
from core.startup_config import (
    resolve_strict_config_validation,
    validate_startup_config,
)
from core.run_artifacts import write_run_report

logger = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments.
    
    Returns:
        Parsed argument namespace.
    """
    parser = argparse.ArgumentParser(
        description="C++ GraphRAG Pipeline: SCIP Indexing + Neo4j Ingestion",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python run_graphrag.py --compdb-path build/compile_commands.json --repo-name my-project\n"
            "  python run_graphrag.py --compdb-path compile_commands.json --repo-name yaml-cpp --skip-indexing\n"
        ),
    )
    
    parser.add_argument(
        "--compdb-path",
        required=True,
        help="Path to compile_commands.json",
    )
    parser.add_argument(
        "--repo-name",
        required=True,
        help="Repository name for Global URI generation",
    )
    parser.add_argument(
        "--index-path",
        default="output/index.scip",
        help="Path to write/read SCIP index (default: output/index.scip)",
    )
    parser.add_argument(
        "--skip-indexing",
        action="store_true",
        default=False,
        help="Skip scip-clang invocation, reuse existing index.scip",
    )
    parser.add_argument(
        "--recreate-graph",
        action="store_true",
        default=False,
        help="Clear existing graph data for this repo before ingesting",
    )
    parser.add_argument(
        "--jobs",
        type=int,
        default=None,
        help="Number of parallel scip-clang processes (default: CPU count)",
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


def phase1_index(compdb_path: str, index_path: str, jobs: int | None) -> str:
    """Phase 1: Run scip-clang to generate SCIP index.
    
    Args:
        compdb_path: Path to compile_commands.json.
        index_path: Output path for index.scip.
        jobs: Parallel processes for scip-clang.
        
    Returns:
        Path to generated index.scip.
    """
    from graphrag.scip_index import run_scip_clang
    
    logger.info("=" * 80)
    logger.info(" PHASE 1: SCIP Indexing")
    logger.info("=" * 80)
    logger.info(f"Compile DB : {os.path.abspath(compdb_path)}")
    logger.info(f"Output index: {os.path.abspath(index_path)}")
    logger.info("")
    
    t0 = time.time()
    index_output = run_scip_clang(compdb_path, index_path, jobs)
    indexing_time = time.time() - t0
    
    logger.info(f"SCIP indexing completed in {indexing_time:.2f}s")
    logger.info("")
    
    return index_output


def phase2_ingest(index_path: str, repo_name: str, recreate: bool):
    """Phase 2: Parse SCIP index and ingest into Neo4j.
    
    Args:
        index_path: Path to index.scip file.
        repo_name: Repository name.
        recreate: Whether to clear existing graph first.
    """
    from graphrag.scip_parser import parse_scip_index
    from graphrag.neo4j_loader import (
        get_neo4j_driver,
        init_graph_schema,
        clear_repo_graph,
        ingest_graph,
    )
    
    logger.info("=" * 80)
    logger.info(" PHASE 2: Parse SCIP Index + Ingest into Neo4j")
    logger.info("=" * 80)
    logger.info(f"Index file : {os.path.abspath(index_path)}")
    logger.info(f"Repository : {repo_name}")
    logger.info(f"Recreate graph: {recreate}")
    logger.info("")
    
    if not os.path.isfile(index_path):
        raise FileNotFoundError(f"SCIP index not found: {index_path}")
    
    # Connect to Neo4j
    driver = get_neo4j_driver()
    
    # Initialize schema
    init_graph_schema(driver)
    
    # Clear repo if requested
    if recreate:
        clear_repo_graph(driver, repo_name)
    
    # Parse SCIP index
    logger.info("Parsing SCIP index...")
    t0 = time.time()
    parse_result = parse_scip_index(index_path, repo_name)
    parse_time = time.time() - t0
    logger.info(f"SCIP parsing completed in {parse_time:.2f}s")
    logger.info("")
    
    # Ingest into Neo4j
    logger.info("Ingesting graph into Neo4j...")
    t0 = time.time()
    stats = ingest_graph(parse_result, driver, repo_name)
    ingest_time = time.time() - t0
    
    logger.info(f"Graph ingestion completed in {ingest_time:.2f}s")
    logger.info(f"Final stats: {stats}")
    logger.info("Graph ingestion SLO report: %s", json.dumps(stats.to_slo_report(), sort_keys=True))
    logger.info(
        "SCIP parse drop report: %s",
        json.dumps(
            {
                "dropped_symbol_count": parse_result.dropped_symbol_count,
                "dropped_reference_count": parse_result.dropped_reference_count,
                "external_symbol_count": parse_result.external_symbol_count,
            },
            sort_keys=True,
        ),
    )
    logger.info("")
    
    driver.close()
    return parse_result, stats


def phase3_verify(repo_name: str) -> None:
    """Phase 3: Verify ingestion by running sample queries.
    
    Args:
        repo_name: Repository name.
    """
    from graphrag.neo4j_loader import get_neo4j_driver
    from graphrag.query import calculate_blast_radius
    
    logger.info("=" * 80)
    logger.info(" PHASE 3: Verification")
    logger.info("=" * 80)
    
    driver = get_neo4j_driver()
    
    # Get a sample entity to query
    with driver.session() as session:
        result = session.run(
            """
            MATCH (e:Entity {repo_name: $repo})
            WHERE e.entity_type = 'Class'
            RETURN e.global_uri AS uri
            LIMIT 1
            """,
            repo=repo_name,
        )
        
        record = result.single()
        if record is None:
            logger.warning("No entities found in graph, skipping verification")
            driver.close()
            return
        
        sample_uri = record["uri"]
    
    logger.info(f"Sample entity: {sample_uri}")
    logger.info("")
    
    # Calculate upstream blast radius
    logger.info("Calculating upstream blast radius (max_depth=3)...")
    blast_result = calculate_blast_radius(
        sample_uri,
        driver,
        max_depth=3,
        direction="upstream",
    )
    
    logger.info(f"Found {blast_result.total_count} affected entities:")
    for entity in blast_result.affected_entities[:10]:
        logger.info(
            f"  {entity.depth} hops: {entity.entity_name} "
            f"[{' -> '.join(entity.relationship_chain)}]"
        )
    
    if blast_result.total_count > 10:
        logger.info(f"  ... and {blast_result.total_count - 10} more")
    
    logger.info("")
    driver.close()


def main() -> None:
    """Main entry point for the GraphRAG pipeline."""
    
    configure_structured_logging(level=logging.INFO)
    
    args = parse_args()
    run_id = set_run_id()
    os.environ["STRICT_CONFIG_VALIDATION"] = "true" if args.strict_config else "false"
    validate_startup_config(
        compose_path="infra_context/docker-compose.yml",
        required_services=("neo4j",),
        strict=args.strict_config,
    )
    
    logger.info("")
    logger.info("*" * 80)
    logger.info(" C++ GraphRAG Pipeline: SCIP -> Neo4j Dependency Graph")
    logger.info(" Run ID: %s", run_id)
    logger.info("*" * 80)
    logger.info("")

    run_report = {
        "run_id": run_id,
        "pipeline": "graphrag",
        "repo_name": args.repo_name,
        "status": "failed",
        "strict_config": args.strict_config,
        "skip_indexing": args.skip_indexing,
    }
    
    try:
        # Phase 1: SCIP Indexing
        if not args.skip_indexing:
            with phase_scope("phase1_index"):
                index_path = phase1_index(args.compdb_path, args.index_path, args.jobs)
            run_report["index_path"] = index_path
        else:
            index_path = args.index_path
            logger.info("Skipping SCIP indexing (--skip-indexing)")
            logger.info(f"Using existing index: {index_path}")
            logger.info("")
            run_report["index_path"] = index_path
        
        # Phase 2: Parse + Ingest
        with phase_scope("phase2_ingest"):
            parse_result, graph_stats = phase2_ingest(
                index_path, args.repo_name, args.recreate_graph
            )
        run_report["scip_parse"] = {
            "document_count": parse_result.document_count,
            "symbol_count": len(parse_result.symbols),
            "reference_count": len(parse_result.references),
            "dropped_symbol_count": parse_result.dropped_symbol_count,
            "dropped_reference_count": parse_result.dropped_reference_count,
        }
        run_report["graph_ingestion"] = graph_stats.to_slo_report()
        
        # Phase 3: Verification
        with phase_scope("phase3_verify"):
            phase3_verify(args.repo_name)

        logger.info(
            "Pipeline SLO summary: %s",
            json.dumps(
                {
                    "run_id": run_id,
                    "scip_parse": {
                        "document_count": parse_result.document_count,
                        "symbol_count": len(parse_result.symbols),
                        "reference_count": len(parse_result.references),
                        "dropped_symbol_count": parse_result.dropped_symbol_count,
                        "dropped_reference_count": parse_result.dropped_reference_count,
                    },
                    "graph_ingestion": graph_stats.to_slo_report(),
                },
                sort_keys=True,
            ),
        )
        run_report["status"] = "success"
        
        logger.info("*" * 80)
        logger.info(" GraphRAG pipeline finished successfully")
        logger.info("*" * 80)
        report_path = write_run_report(run_report, run_id)
        logger.info("Run report written: %s", report_path)
        
    except FileNotFoundError as e:
        logger.error(f"File error: {e}")
        run_report["error"] = str(e)
        report_path = write_run_report(run_report, run_id)
        logger.info("Run report written: %s", report_path)
        sys.exit(1)
    except ConnectionError as e:
        logger.error(f"Neo4j connection error: {e}")
        run_report["error"] = str(e)
        report_path = write_run_report(run_report, run_id)
        logger.info("Run report written: %s", report_path)
        sys.exit(1)
    except Exception as e:
        logger.error(f"Pipeline failed: {e}", exc_info=True)
        run_report["error"] = str(e)
        report_path = write_run_report(run_report, run_id)
        logger.info("Run report written: %s", report_path)
        sys.exit(1)


if __name__ == "__main__":
    main()
