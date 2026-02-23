#!/usr/bin/env python3
"""
Top-level pipeline orchestrator for C++ code extraction and Qdrant ingestion.

Decouples extraction and ingestion phases using a JSONL file as an
intermediate persistent layer to prevent OOM errors and ensure
blast-radius isolation.

Usage:
    python run_pipeline.py --source-dir /path/to/cpp/repo --repo-name my_project
    python run_pipeline.py --source-dir ./src --repo-name rtc-engine --recreate-collection
    python run_pipeline.py --source-dir ./src --repo-name rtc-engine --output-file out/dump.jsonl
"""

import argparse
import json
import logging
import os
import sys
import time

logger = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments.

    Returns:
        Parsed argument namespace.
    """
    parser = argparse.ArgumentParser(
        description="C++ Code Extraction & Qdrant Ingestion Pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python run_pipeline.py --source-dir ./src --repo-name rtc-engine\n"
            "  python run_pipeline.py --source-dir ./src --repo-name rtc-engine --recreate-collection\n"
        )
    )

    parser.add_argument(
        "--source-dir",
        required=True,
        help="Path to the C++ source directory to extract from."
    )
    parser.add_argument(
        "--repo-name",
        required=True,
        help="Name of the repository (used in Global URI generation)."
    )
    parser.add_argument(
        "--output-file",
        default="output/entities.jsonl",
        help="Path for the intermediate JSONL file. Default: output/entities.jsonl"
    )
    parser.add_argument(
        "--recreate-collection",
        action="store_true",
        default=False,
        help="If set, delete and recreate the Qdrant collection from scratch."
    )

    return parser.parse_args()


def phase1_extract(source_dir: str, repo_name: str, output_file: str) -> int:
    """Phase 1: Extract C++ entities and serialize to JSONL on disk.

    Args:
        source_dir: Path to the C++ source directory.
        repo_name: Repository name for URI generation.
        output_file: Path to write the JSONL output.

    Returns:
        Number of entities written to disk.

    Raises:
        FileNotFoundError: If source_dir does not exist.
    """
    from extraction.extractor import iter_extract_to_dict_list

    logger.info("=" * 80)
    logger.info(" PHASE 1: Extraction & Serialization to Disk")
    logger.info("=" * 80)

    if not os.path.isdir(source_dir):
        raise FileNotFoundError(f"Source directory not found: {source_dir}")

    logger.info(f"Source directory : {os.path.abspath(source_dir)}")
    logger.info(f"Repository name  : {repo_name}")
    logger.info(f"Output file      : {os.path.abspath(output_file)}")
    logger.info("")

    # --- Extract ---
    t0 = time.time()
    # --- Serialize to JSONL in streaming mode ---
    os.makedirs(os.path.dirname(os.path.abspath(output_file)), exist_ok=True)

    lines_written = 0
    with open(output_file, "w", encoding="utf-8") as f:
        for entity in iter_extract_to_dict_list(source_dir, repo_name):
            f.write(json.dumps(entity, ensure_ascii=False) + "\n")
            lines_written += 1

    extraction_time = time.time() - t0
    logger.info(
        "Extraction completed in %.2fs — %d entities found",
        extraction_time,
        lines_written,
    )

    logger.info(f"Wrote {lines_written} lines to {output_file}")
    logger.info("Phase 1 complete. Streaming extraction path used.")
    logger.info("")

    return lines_written


def phase2_ingest(output_file: str, recreate: bool) -> None:
    """Phase 2: Read JSONL from disk and ingest into Qdrant.

    Args:
        output_file: Path to the JSONL file written by Phase 1.
        recreate: Whether to drop and recreate the Qdrant collection.

    Raises:
        FileNotFoundError: If output_file does not exist.
        ConnectionError: If Qdrant is unreachable.
    """
    from ingestion.qdrant_loader import get_qdrant_client, init_collection, ingest_from_jsonl

    logger.info("=" * 80)
    logger.info(" PHASE 2: Ingestion from Disk into Qdrant")
    logger.info("=" * 80)

    if not os.path.isfile(output_file):
        raise FileNotFoundError(f"JSONL file not found: {output_file}")

    logger.info(f"Input file       : {os.path.abspath(output_file)}")
    logger.info(f"Recreate collection: {recreate}")
    logger.info("")

    # --- Connect ---
    client = get_qdrant_client()

    # --- Initialize collection ---
    init_collection(client, recreate=recreate)

    # --- Ingest strictly from disk ---
    t0 = time.time()
    stats = ingest_from_jsonl(file_path=output_file, client=client)
    ingestion_time = time.time() - t0

    logger.info("")
    logger.info(f"Ingestion completed in {ingestion_time:.2f}s")
    logger.info(f"Final stats: {stats}")
    logger.info("")


def main() -> None:
    """Main entry point for the pipeline."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )

    args = parse_args()

    logger.info("")
    logger.info("*" * 80)
    logger.info(" C++ Code Extraction & Qdrant Ingestion Pipeline")
    logger.info("*" * 80)
    logger.info("")

    try:
        lines = phase1_extract(args.source_dir, args.repo_name, args.output_file)

        if lines == 0:
            logger.warning("No entities extracted. Skipping ingestion phase.")
            sys.exit(0)

        phase2_ingest(args.output_file, args.recreate_collection)

        logger.info("*" * 80)
        logger.info(" Pipeline finished successfully.")
        logger.info("*" * 80)

    except FileNotFoundError as e:
        logger.error(f"File error: {e}")
        sys.exit(1)
    except ConnectionError as e:
        logger.error(f"Qdrant connection error: {e}")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Pipeline failed: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
