# Future Improvements Plan - Architecture Review and Design Draft

## 1. Project Purpose (Current Understanding)
This repository is building a dual-store code intelligence platform for C++:

1. Layer 1 (Extraction + Vector):
- Parse C/C++ code with Tree-sitter.
- Extract structured entities (Class/Struct/Function) with stable Global URIs.
- Generate embeddings and ingest to Qdrant for semantic retrieval.

2. Layer 2 (Semantic Graph):
- Parse SCIP indexes from compiler-aware indexing.
- Build dependency graph in Neo4j (CALLS, INHERITS, USES_TYPE, etc.).
- Support deterministic blast-radius traversal.

3. Layer 3 (Future MCP/API):
- Expose typed retrieval and impact-analysis tools to agents.

The implementation already has strong breadth coverage and a good module split (`extraction`, `ingestion`, `graphrag`), and currently passes tests (`159 passed, 21 skipped` locally).

## 2. Executive Assessment
Overall maturity: **functional prototype with good test volume, but not yet production-safe for scale/cross-repo correctness**.

Main risks today:
- Identity and symbol classification inconsistencies between Tree-sitter and SCIP layers.
- Graph semantic inaccuracies (cross-repo stubs, edge typing, enclosing scope attribution).
- Non-streaming memory patterns in extraction/ingestion despite pipeline comments claiming OOM-safe operation.
- Operational robustness gaps (fail-open config parsing, limited retry semantics, weak observability contracts).

## 3. Detailed Review Comments

## 3.1 Functionality Findings
| ID | Severity | Area | Observation | Impact |
|---|---|---|---|---|
| F-01 | High | Symbol classification | `graphrag/symbol_mapper.py` marks symbols as external based only on namespace not being in monitored list (`is_external = first_ns not in MONITORED_NAMESPACES`), making `stub` classification effectively unreachable for monitored cross-repo symbols. | Cross-repo dependency bridging can silently fail; graph cannot reliably represent external sibling repos. |
| F-02 | High | SCIP kind mapping | Hardcoded `SCIP_KIND_*` constants in `graphrag/symbol_mapper.py` do not match the checked-in `scip.proto` enum values (e.g., Class/Struct/Function IDs). | Wrong entity typing/dropping when non-zero kinds are emitted by indexers; portability risk across toolchains. |
| F-03 | High | Edge semantics | In `graphrag/neo4j_loader.py`, `is_implementation` edges are mapped to `INHERITS` vs `OVERRIDES` via fragile string heuristic (`"#" in symbol and "()" not in symbol`). | Incorrect edge types degrade blast-radius trust and make architectural impact analysis unreliable. |
| F-04 | Medium | Enclosing scope attribution | `_build_enclosing_scope_map` in `graphrag/scip_parser.py` claims inner scopes should win, but uses `if line not in scope_map`, so first symbol wins. | CALLS edges can be assigned to wrong caller scope, especially for nested definitions. |
| F-05 | Medium | Name normalization | `extract_function_name` fallback in `extraction/traversal.py` can leak non-canonical names (spaces/qualifier artifacts). | URI drift between extraction and graph layers, lower join quality. |
| F-06 | Medium | Comment normalization | `clean_doxygen_comment` handles delimiters with single-branch `elif`; single-line `/** ... */` can retain trailing markers. | Lower-quality payload/doc text and retrieval relevance. |
| F-07 | Medium | Extraction policy consistency | `extern "C"` declaration branch in `extraction/traversal.py` extracts declarations while regular non-definition declarations are skipped. | Semantics differ by context; inconsistent corpus shape and noisy embeddings. |
| F-08 | Low | Metrics integrity | `ExtractionStats.parse_errors` exists in `extraction/extractor.py` but is not actually tracked/incremented. | Misleading operational metrics and weak failure diagnostics. |

## 3.2 Performance Findings
| ID | Severity | Area | Observation | Impact |
|---|---|---|---|---|
| P-01 | High | Extraction memory | `run_pipeline.py` phase 1 calls `extract_to_dict_list`, materializing all entities in memory before writing JSONL. | Large repos can still hit memory pressure/OOM despite staged pipeline design intent. |
| P-02 | High | JSONL ingestion memory | `ingestion/ingest_from_jsonl` reads whole file into memory list before upsert batching. | High memory footprint for large datasets; prevents true streaming ingestion. |
| P-03 | Medium | SCIP parse complexity | `parse_scip_index` scans all occurrences for each symbol to find definition range (nested loops). | Potential quadratic behavior on large indexes. |
| P-04 | Medium | Duplicate graph work | Node/edge batches are not deduplicated prior to MERGE-heavy writes. | Extra DB round-trips/CPU, slower ingest at scale. |
| P-05 | Medium | Embedding batch sizing | Batch size is count-based, not token/byte-budget aware. | Real embedding calls can fail unpredictably for long code chunks. |
| P-06 | Low | Parser reuse | Parser instances are created per parse call (`parse_bytes`). | Small per-file overhead; accumulates on large codebases. |

## 3.3 Robustness Findings
| ID | Severity | Area | Observation | Impact |
|---|---|---|---|---|
| R-01 | High | Config safety | Qdrant/Neo4j config loaders often fall back silently to defaults on parse failures. | Misconfiguration can remain hidden and connect to wrong targets. |
| R-02 | Medium | Retry model | Batch upserts to Qdrant/Neo4j do not consistently retry with bounded backoff/idempotent replay metadata. | Transient failures can cause partial ingestion and manual recovery. |
| R-03 | Medium | Query API hygiene | `get_entity_neighbors` outbound query in `graphrag/query.py` targets any node type, including `File`, returning null-ish entity fields. | API consumers get heterogeneous/incomplete payloads unexpectedly. |
| R-04 | Medium | Test portability | `graphrag/tests/test_integration.py` depends on hardcoded absolute local path for index file. | CI portability and team reproducibility are limited. |
| R-05 | Medium | Observability | No structured ingestion run IDs, no per-phase success/failure counters persisted, and limited invariant checks. | Difficult incident triage and weak rollback confidence. |
| R-06 | Low | Script ergonomics | Top-level verification scripts (`test_real_search.py`, `test_torture_parser.py`) are ad-hoc and not consistently production-style. | Operational drift and fragile manual workflows. |

## 3.4 What Is Strong Already
1. Clean module boundaries with clear Layer 1/Layer 2 responsibilities.
2. Good amount of unit and integration tests across extraction/ingestion/graph.
3. Deterministic point IDs via UUIDv5 for Qdrant idempotency.
4. Useful payload indexing in Qdrant collection initialization.
5. Retry handling for embedding network calls is directionally correct.

## 4. Improvement Coding Plan (Design Only, No Implementation Yet)

## 4.1 Plan Principles
1. **Identity first**: URI and symbol contracts must be deterministic and cross-layer consistent.
2. **Streaming first**: avoid whole-dataset materialization in every phase.
3. **Fail-fast configs**: explicit validation with environment-aware startup checks.
4. **Typed semantics over heuristics**: derive graph edges from protocol semantics, not string guesses.
5. **Measurable rollout**: every phase has invariants and acceptance criteria.

## 4.2 Phase 0 - Baseline and Quality Gates
Duration: 2-3 days

Goals:
- Lock current behavior before refactor.
- Add regression harness around known risk areas.

Design tasks:
1. Create a "contract tests" suite for URI equivalence between extraction and SCIP mapping.
2. Add representative SCIP fixture set (small synthetic `.scip`) for deterministic parser tests.
3. Add metrics snapshot script for:
- extraction throughput (files/sec)
- ingestion throughput (entities/sec)
- graph ingest throughput (edges/sec)

Acceptance criteria:
- Contract tests fail on current known bug scenarios.
- Baseline performance and correctness numbers recorded for comparison.

## 4.3 Phase 1 - Identity and Extraction Correctness Hardening
Duration: 1-1.5 weeks

Goals:
- Stabilize canonical naming/URI generation.
- Align extraction output with explicit policy.

Design tasks:
1. Introduce shared URI contract module (`core/uri_contract.py`) used by both extraction and GraphRAG mapping.
2. Add canonical name normalizer for function/entity names (whitespace, qualifiers, destructor/operator formatting).
3. Refactor Doxygen cleaning into delimiter-safe parser logic for single-line and multiline blocks.
4. Make declaration extraction policy explicit and configurable:
- `include_declarations=false` default
- optional `extern_c_declarations=true` mode if needed
5. Track real parse error metrics (`parse_errors`) by AST error-node inspection.

Acceptance criteria:
- Identical symbol names from repeated runs.
- No stray comment delimiters in extracted `docstring`.
- Policy-based declaration handling verified by tests.

## 4.4 Phase 2 - Streaming and Ingestion Scalability
Duration: 1 week

Goals:
- Remove large in-memory buffers.
- Make ingestion resilient under large codebases.

Design tasks:
1. Replace `extract_to_dict_list` usage in pipeline with streaming iterator interface:
- `iter_extract_entities(source_dir, repo_name)`
- write JSONL incrementally
2. Replace `ingest_from_jsonl` full-load pattern with streaming batch reader:
- `iter_jsonl_entities(file, batch_size)`
3. Add embedding request budgeting by estimated tokens/chars per batch (not only entity count).
4. Add idempotent retry wrappers for Qdrant upsert batches with bounded backoff and dead-letter logging.
5. Add preflight vector-dimension check against existing collection config when `recreate=False`.

Acceptance criteria:
- Peak memory remains bounded with large JSONL input.
- Transient Qdrant failures recover without manual restart.
- Batch-level failure reports include batch ID + URI range.

## 4.5 Phase 3 - Graph Semantics and Cross-Repo Robustness
Duration: 1.5-2 weeks

Goals:
- Fix semantic correctness in SCIP parsing and Neo4j edge modeling.
- Enable reliable cross-repo stubs.

Design tasks:
1. Replace hardcoded SCIP kind constants with generated enum references from `scip_pb2`.
2. Redesign symbol classification API to accept context:
- `classify_symbol(symbol, kind, is_local_definition)`
- allow true `stub` for monitored namespaces not defined in current index.
3. Rebuild enclosing scope attribution with innermost-range precedence (interval-based, not first-line winner).
4. Replace heuristic INHERITS/OVERRIDES mapping with protocol-aware rules:
- use symbol kind + relationship flags
- validate source and target entity types
5. Deduplicate nodes/edges before DB writes to reduce MERGE churn.
6. Introduce ingestion invariant checks:
- no `CALLS` from `File` nodes
- no impossible edge type pairs

Acceptance criteria:
- Cross-file and cross-repo edge targets resolve correctly in deterministic tests.
- Inheritance/override edge precision improves on fixture validation.
- Graph ingest runtime improves under same dataset.

## 4.6 Phase 4 - Query Layer and API Contract Stabilization
Duration: 4-5 days

Goals:
- Ensure query outputs are reliable for MCP/API consumers.

Design tasks:
1. Restrict neighbor queries to `Entity` nodes unless explicitly requested.
2. Add optional filters in blast-radius query:
- `repo_name`
- allowed relationship types
- max result count/pagination cursor
3. Add query timeout and deterministic ordering strategy.
4. Return typed result metadata (e.g., missing-root vs empty-result).

Acceptance criteria:
- No null entity fields from neighbor APIs by default.
- Blast-radius calls remain bounded and predictable on dense graphs.

## 4.7 Phase 5 - Operational Hardening and CI
Duration: 1 week

Goals:
- Production-grade confidence and portability.

Design tasks:
1. Add startup config validator (strict mode): fail if docker-compose parsing fails unexpectedly.
2. Move absolute-path integration fixtures to configurable env/test resource discovery.
3. Split test matrix:
- fast unit tests (default)
- optional integration tests with service/profile markers
4. Add structured run logs with correlation IDs across phases.
5. Define SLO-style ingestion reports:
- success rate
- retry counts
- skipped/dropped entity counts by reason

Acceptance criteria:
- CI runs are portable across machines.
- Integration suite is opt-in but reproducible.
- Run artifacts are sufficient for postmortem/root-cause analysis.

## 5. Suggested Implementation Order (Risk-Weighted)
1. **Phase 0 + Phase 1** first: identity correctness before performance tuning.
2. **Phase 3** next: graph semantic correctness is highest downstream risk.
3. **Phase 2** after semantics stabilize: optimize streaming and throughput.
4. **Phase 4/5** to finalize API and operational readiness.

## 6. Test Expansion Plan (Required)
1. Add regression tests for:
- symbol kind mappings using real `scip_pb2` enum values
- cross-repo stub creation cases
- enclosing scope attribution in nested symbol scenarios
- INHERITS vs OVERRIDES edge correctness
2. Add scale tests:
- large JSONL streaming ingestion memory ceiling
- large SCIP parse throughput benchmark
3. Add contract test:
- same entity resolved from extraction and SCIP maps to same Global URI

## 7. Definition of Done for "Production-Ready"
1. URI contract tests pass across extraction + SCIP + graph.
2. Full pipeline runs in streaming mode without whole-dataset in-memory buffering.
3. Graph edge semantics validated on deterministic fixtures with target precision thresholds.
4. Integration tests are environment-portable (no machine-specific absolute paths).
5. Observability includes per-phase counters, failures by reason, and reproducible run artifacts.

---

This design intentionally prioritizes correctness and contract stability before optimization-heavy refactors, because incorrect graph semantics and unstable URIs are the highest-risk failure modes for downstream AI agents.
