# Right Brain: Semantic GraphRAG Pipeline — Implementation Plan

## 1. Overview

The "Right Brain" processes `compile_commands.json` artifacts through `scip-clang`
to produce a `.scip` protobuf index, then parses that index to extract C++
relationships (inheritance, method calls, type usage) and ingests them into
Neo4j as a dependency graph. A blast-radius query function exposes the graph
for future MCP integration.

**Scope**: SCIP indexing -> SCIP parsing -> Symbol-to-URI mapping -> Neo4j ingestion -> Blast-radius query.

**Out of scope**: Chat, RAG synthesis, MCP server, Qdrant integration changes.

---

## 2. File Structure

```
graphrag/
├── __init__.py
├── config.py                # Neo4j connection constants, SCIP paths
├── scip_index.py            # Invoke scip-clang subprocess
├── proto/
│   ├── scip.proto           # Upstream SCIP proto schema (committed for reproducibility)
│   └── scip_pb2.py          # Generated protobuf bindings
├── scip_parser.py           # Parse index.scip into intermediate Python objects
├── symbol_mapper.py         # SCIP symbol -> Global URI conversion (THE BRIDGE)
├── neo4j_loader.py          # Neo4j connection, schema init, batch Cypher upserts
├── query.py                 # Blast-radius and dependency query functions
└── tests/
    ├── __init__.py
    ├── test_scip_parser.py
    ├── test_symbol_mapper.py
    ├── test_neo4j_loader.py
    └── test_query.py

run_graphrag.py              # Top-level CLI orchestrator (mirrors run_pipeline.py)
```

---

## 3. Dependencies

New packages to add to `requirements.txt`:

| Package | Version | Purpose |
|---------|---------|---------|
| protobuf | >=4.25.0 (already installed) | Parse SCIP protobuf index |
| neo4j | >=5.14.0 (already installed) | Neo4j Bolt driver |

No new packages required beyond what is already installed. The `scip.proto`
schema will be compiled to `graphrag/proto/scip_pb2.py` using `protoc`
during initial setup.

---

## 4. Module Design

### 4.1. `graphrag/config.py` — Configuration

All values read from environment variables and `docker-compose.yml`, following
the `agent.md` mandate.

| Constant | Type | Source | Default |
|----------|------|--------|---------|
| `NEO4J_URI` | `str` | Parsed from docker-compose `7687` port | `bolt://127.0.0.1:7687` |
| `NEO4J_USERNAME` | `str` | Parsed from `NEO4J_AUTH` env var | `neo4j` |
| `NEO4J_PASSWORD` | `str` | Parsed from `NEO4J_AUTH` env var | `testpassword123` |
| `SCIP_CLANG_PATH` | `str` | `os.getenv("SCIP_CLANG_PATH", "scip-clang")` | `scip-clang` |
| `DEFAULT_INDEX_OUTPUT` | `str` | — | `output/index.scip` |
| `NEO4J_BATCH_SIZE` | `int` | — | `500` |

### 4.2. `graphrag/scip_index.py` — SCIP Invocation

```python
def run_scip_clang(
    compdb_path: str,
    index_output_path: str = DEFAULT_INDEX_OUTPUT,
    jobs: int | None = None,
) -> str:
```

**Logic**:
1. Verify `compdb_path` exists (`compile_commands.json`).
2. Build subprocess command: `scip-clang --compdb-path <path> --index-output-path <output> [-j <jobs>]`.
3. Run `subprocess.run(...)` with `check=True`, capturing stdout/stderr.
4. Verify output file exists, log size.
5. Return the output path.

### 4.3. `graphrag/proto/scip_pb2.py` — Generated Protobuf Bindings

Generated once via:
```bash
curl -sL https://raw.githubusercontent.com/sourcegraph/scip/main/scip.proto -o graphrag/proto/scip.proto
protoc --python_out=graphrag/proto -Igraphrag/proto graphrag/proto/scip.proto
```

Consumed by `scip_parser.py`. The `.proto` source file is committed alongside
the generated `_pb2.py` for reproducibility.

### 4.4. `graphrag/scip_parser.py` — SCIP Index Parser

**Intermediate data models** (dataclasses):

```python
@dataclass
class ScipSymbolDef:
    """A symbol definition extracted from SCIP."""
    scip_symbol: str            # Raw SCIP symbol string
    file_path: str              # Document.relative_path
    kind: int                   # SymbolInformation.Kind enum value
    display_name: str           # SymbolInformation.display_name
    definition_range: tuple[int, int, int, int] | None
    relationships: list[ScipRelationship]

@dataclass
class ScipRelationship:
    """A relationship between two SCIP symbols."""
    target_symbol: str          # SCIP symbol of the related entity
    is_reference: bool
    is_implementation: bool
    is_type_definition: bool
    is_definition: bool

@dataclass
class ScipReference:
    """A reference occurrence (non-definition) found in a document."""
    scip_symbol: str            # Symbol being referenced
    file_path: str              # Document where the reference occurs
    enclosing_symbol: str | None  # Nearest enclosing definition scope
    role: str                   # "READ", "WRITE", "CALL", "REF"
    line: int                   # 0-indexed line number
```

**Key function**:

```python
def parse_scip_index(index_path: str, repo_name: str) -> ScipParseResult:
```

**Algorithm**:
1. Read binary file, deserialize into `scip_pb2.Index`.
2. For each `Document`:
   a. Build a **line-to-enclosing-definition** lookup from definition occurrences
      (using `enclosing_range` or positional heuristic).
   b. For each `SymbolInformation` in `doc.symbols`: skip locals, create `ScipSymbolDef`.
   c. For each `Occurrence` in `doc.occurrences`:
      - If `symbol_roles & 0x1` (Definition): record definition position.
      - Else: record as `ScipReference` with inferred role from bitfield.
      - Determine `enclosing_symbol` by looking up what definition scope this
        occurrence's line falls within.
3. Return `ScipParseResult` containing all defs and refs.

**Enclosing scope resolution** (for CALLS extraction):
- First pass: collect all definition occurrences with `enclosing_range` (or `range`)
  to build an interval map of `[start_line, end_line] -> defining_symbol`.
- Second pass: for each non-definition occurrence, look up which definition's
  range it falls inside -> `enclosing_symbol CALLS target_symbol`.

### 4.5. `graphrag/symbol_mapper.py` — The BRIDGE (Critical)

Translates SCIP's native symbol format into our Global URI format.

**SCIP symbol anatomy** (from real `scip-clang` output):
```
cxx . . $ YAML/GraphBuilderAdapter#OnSequenceStart(ff993a8f75aba5c3).
^scheme   ^pkg   ^--- descriptors ---^
```

Descriptors are chained with suffixes:
- `/` = Namespace, `#` = Type, `(disambiguator).` = Method, `.` = Term, `!` = Macro

**Mapping rules**:

| SCIP Descriptor | Suffix | Our EntityType | EntityName contribution |
|-----------------|--------|----------------|------------------------|
| Namespace | `/` | (not standalone) | Prepended with `::` separator |
| Type | `#` | `Class` / `Struct` (from Kind) | Appended as final name |
| Method | `(hash).` | `Function` | Appended as final name |
| Term | `.` | `Function` | Appended as final name |
| Macro | `!` | (skipped) | — |

**Edge cases**: backtick-escaped names, template names, file-scope symbols, locals, externals.

### 4.6. `graphrag/neo4j_loader.py` — Graph Ingestion

**Schema**:
```cypher
CREATE CONSTRAINT entity_uri IF NOT EXISTS FOR (e:Entity) REQUIRE e.global_uri IS UNIQUE;
CREATE INDEX entity_type_idx IF NOT EXISTS FOR (e:Entity) ON (e.entity_type);
CREATE INDEX entity_repo_idx IF NOT EXISTS FOR (e:Entity) ON (e.repo_name);
```

**Edge types**: INHERITS, OVERRIDES, CALLS, USES_TYPE, CONTAINS, DEFINED_IN

**3-phase batch ingestion**: MERGE nodes -> MERGE edges -> MERGE file nodes. All idempotent.

### 4.7. `graphrag/query.py` — Blast Radius

Upstream (what breaks) and downstream (what depends on) traversal using
variable-length path queries with configurable `max_depth`.

---

## 5. Mapping Examples

| SCIP Symbol | EntityType | EntityName | Global URI |
|-------------|-----------|------------|------------|
| `cxx . . $ YAML/GraphBuilderAdapter#` | Class | `YAML::GraphBuilderAdapter` | `yaml-cpp::src/contrib/graphbuilderadapter.h::Class::YAML::GraphBuilderAdapter` |
| `cxx . . $ YAML/GraphBuilderAdapter#OnSequenceStart(hash).` | Function | `YAML::GraphBuilderAdapter::OnSequenceStart` | `yaml-cpp::src/contrib/graphbuilderadapter.cpp::Function::YAML::GraphBuilderAdapter::OnSequenceStart` |
| `cxx . . $ YAML/EncodeBase64(hash).` | Function | `YAML::EncodeBase64` | `yaml-cpp::src/binary.cpp::Function::YAML::EncodeBase64` |

---

## 6. Neo4j Graph Schema

```
(:File)  <-[:DEFINED_IN]-  (:Entity:Class)
                                |  [:CONTAINS]   |  [:INHERITS]
                                v                v
                          (:Entity:Function)  (:Entity:Class)
                                |  [:CALLS]      |  [:OVERRIDES]
                                v                v
                          (:Entity:Function)  (:Entity:Function)
```

---

## 7. Execution Order

1. Generate protobuf bindings
2. `graphrag/config.py`
3. `graphrag/scip_index.py`
4. `graphrag/scip_parser.py` (models + parsing)
5. `graphrag/symbol_mapper.py` (**hardest module**)
6. `graphrag/neo4j_loader.py`
7. `graphrag/query.py`
8. `run_graphrag.py`
9. Tests (unit then integration)
10. End-to-end verification

---

## 8. Risk Register

| Risk | Mitigation |
|------|------------|
| SCIP hash collisions across overloads | Strip hashes; shared URI acceptable for blast-radius |
| `scip-clang` fails on incomplete compdb | Log warnings, continue with partial index |
| `enclosing_range` not always populated | Fallback: interval tree from definition ranges |
| Neo4j APOC unavailable | Entity-type-specific MERGE queries |
| Large repos -> millions of occurrences | Stream documents; batch Neo4j writes |
| External deps have no file_path | `file_path="<external>"`, `is_external=True` |
