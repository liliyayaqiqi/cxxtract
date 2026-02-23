#!/usr/bin/env python3
"""Phase 0 baseline metrics snapshot for extraction, ingestion, and graph ingest."""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from extraction.extractor import extract_directory
from graphrag.neo4j_loader import ingest_graph
from graphrag.scip_parser import parse_scip_index
from graphrag.tests.fixtures.build_scip_fixtures import BASIC_FIXTURE, build_all_fixtures
from ingestion.embedding import generate_mock_embeddings
from ingestion.qdrant_loader import ingest_entities


@dataclass
class SnapshotSection:
    """Metrics section."""

    duration_seconds: float
    throughput_per_second: float
    unit: str
    count: int
    extra: dict[str, Any]


class _FakeQdrantClient:
    """Minimal Qdrant client stub used for offline ingestion throughput tests."""

    def __init__(self) -> None:
        self.upserts: int = 0
        self.points: int = 0

    def upsert(self, collection_name: str, points: list[Any]) -> None:
        self.upserts += 1
        self.points += len(points)


class _FakeResult:
    """Neo4j result stub."""

    def __init__(self, count: int = 0) -> None:
        self._count = count

    def single(self) -> dict[str, int]:
        return {"count": self._count}


class _FakeSession:
    """Neo4j session stub."""

    def __enter__(self) -> "_FakeSession":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def run(self, query: str, **params: Any) -> _FakeResult:
        if "UNWIND $nodes AS n" in query:
            return _FakeResult(len(params.get("nodes", [])))
        if "UNWIND $edges AS e" in query:
            return _FakeResult(len(params.get("edges", [])))
        return _FakeResult(0)


class _FakeNeo4jDriver:
    """Neo4j driver stub."""

    def session(self) -> _FakeSession:
        return _FakeSession()

    def close(self) -> None:
        return None


def _write_markdown(path: Path, metrics: dict[str, Any]) -> None:
    """Write metrics in Markdown form for human-readable baseline snapshots."""
    extraction = metrics["extraction"]
    ingestion = metrics["ingestion"]
    graph = metrics["graph_ingestion"]

    lines = [
        "# Phase 0 Baseline Metrics",
        "",
        f"- Generated at (UTC): `{metrics['generated_at_utc']}`",
        f"- Source fixtures: `{metrics['source_fixtures_dir']}`",
        f"- SCIP fixture: `{metrics['scip_fixture_path']}`",
        "",
        "| Stage | Count | Duration (s) | Throughput |",
        "|---|---:|---:|---:|",
        (
            f"| Extraction | {extraction['count']} files | "
            f"{extraction['duration_seconds']:.6f} | "
            f"{extraction['throughput_per_second']:.2f} files/s |"
        ),
        (
            f"| Ingestion | {ingestion['count']} entities | "
            f"{ingestion['duration_seconds']:.6f} | "
            f"{ingestion['throughput_per_second']:.2f} entities/s |"
        ),
        (
            f"| Graph Ingestion | {graph['count']} edges | "
            f"{graph['duration_seconds']:.6f} | "
            f"{graph['throughput_per_second']:.2f} edges/s |"
        ),
        "",
        "## Notes",
        "",
        "- This snapshot runs in offline mode using in-process stubs for Qdrant and Neo4j calls.",
        "- It is intended for *relative* regression tracking between commits.",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_snapshot(metrics_json: Path, metrics_md: Path) -> dict[str, Any]:
    """Run the baseline benchmark and persist outputs."""
    build_all_fixtures(force=False)

    root = Path(__file__).resolve().parents[1]
    source_fixtures_dir = root / "extraction" / "tests" / "fixtures"
    scip_fixture_path = BASIC_FIXTURE

    # Extraction benchmark (files/sec)
    t0 = time.perf_counter()
    entities, extraction_stats = extract_directory(
        directory=str(source_fixtures_dir),
        repo_name="phase0-baseline",
    )
    extraction_duration = time.perf_counter() - t0
    extraction_files = extraction_stats.files_processed

    # Ingestion benchmark (entities/sec)
    qdrant_client = _FakeQdrantClient()
    entity_dicts = [entity.to_dict() for entity in entities]
    t1 = time.perf_counter()
    ingestion_stats = ingest_entities(
        entities=entity_dicts,
        client=qdrant_client,
        embed_fn=generate_mock_embeddings,
        dimension=256,
        batch_size=64,
        collection_name="phase0-benchmark",
    )
    ingestion_duration = time.perf_counter() - t1

    # Graph ingest benchmark (edges/sec)
    parse_result = parse_scip_index(str(scip_fixture_path), repo_name="phase0-baseline")
    fake_driver = _FakeNeo4jDriver()
    t2 = time.perf_counter()
    graph_stats = ingest_graph(
        parse_result=parse_result,
        driver=fake_driver,
        repo_name="phase0-baseline",
        batch_size=64,
    )
    graph_duration = time.perf_counter() - t2

    data: dict[str, Any] = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_fixtures_dir": str(source_fixtures_dir),
        "scip_fixture_path": str(scip_fixture_path),
        "extraction": asdict(
            SnapshotSection(
                duration_seconds=extraction_duration,
                throughput_per_second=(extraction_files / extraction_duration)
                if extraction_duration > 0
                else 0.0,
                unit="files_per_second",
                count=extraction_files,
                extra={
                    "entities_extracted": extraction_stats.entities_extracted,
                    "files_failed": extraction_stats.files_failed,
                },
            )
        ),
        "ingestion": asdict(
            SnapshotSection(
                duration_seconds=ingestion_duration,
                throughput_per_second=(ingestion_stats.points_uploaded / ingestion_duration)
                if ingestion_duration > 0
                else 0.0,
                unit="entities_per_second",
                count=ingestion_stats.points_uploaded,
                extra={
                    "batches_sent": ingestion_stats.batches_sent,
                    "errors": ingestion_stats.errors,
                },
            )
        ),
        "graph_ingestion": asdict(
            SnapshotSection(
                duration_seconds=graph_duration,
                throughput_per_second=(graph_stats.edges_created / graph_duration)
                if graph_duration > 0
                else 0.0,
                unit="edges_per_second",
                count=graph_stats.edges_created,
                extra={
                    "nodes_created": graph_stats.nodes_created,
                    "batches_sent": graph_stats.batches_sent,
                    "errors": graph_stats.errors,
                },
            )
        ),
    }

    metrics_json.parent.mkdir(parents=True, exist_ok=True)
    metrics_json.write_text(json.dumps(data, indent=2), encoding="utf-8")
    _write_markdown(metrics_md, data)

    return data


def main() -> None:
    """CLI entrypoint."""
    parser = argparse.ArgumentParser(description="Run Phase 0 baseline snapshot.")
    parser.add_argument(
        "--metrics-json",
        default="plan/phase0_baseline_metrics.json",
        help="Path to write baseline metrics JSON.",
    )
    parser.add_argument(
        "--metrics-md",
        default="plan/phase0_baseline_metrics.md",
        help="Path to write baseline metrics Markdown report.",
    )
    args = parser.parse_args()

    output = run_snapshot(
        metrics_json=Path(args.metrics_json),
        metrics_md=Path(args.metrics_md),
    )

    print("Phase 0 baseline snapshot complete.")
    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
