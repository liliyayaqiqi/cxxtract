# Phase 0 Baseline Metrics

- Generated at (UTC): `2026-02-23T03:41:00.542282+00:00`
- Source fixtures: `/Users/yaqi.li/testproject_opencode/extraction/tests/fixtures`
- SCIP fixture: `/Users/yaqi.li/testproject_opencode/graphrag/tests/fixtures/basic_graph.scip`

| Stage | Count | Duration (s) | Throughput |
|---|---:|---:|---:|
| Extraction | 13 files | 0.014656 | 887.03 files/s |
| Ingestion | 36 entities | 0.002086 | 17260.32 entities/s |
| Graph Ingestion | 2 edges | 0.000074 | 26921.16 edges/s |

## Notes

- This snapshot runs in offline mode using in-process stubs for Qdrant and Neo4j calls.
- It is intended for *relative* regression tracking between commits.
