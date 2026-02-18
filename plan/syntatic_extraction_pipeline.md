# Layer 1: Extraction Engine — Implementation Plan

## 1. File Structure

```
extraction/
├── __init__.py                  # Package marker; re-exports public API
├── config.py                    # Constants, node-type sets, URI format helpers
├── parser.py                    # tree-sitter initialization and file parsing
├── traversal.py                 # AST traversal logic + comment correlation
├── models.py                    # Dataclass definitions for extracted entities
├── extractor.py                 # High-level orchestrator (single-file + batch)
└── tests/
    ├── __init__.py
    ├── test_parser.py           # Unit tests for parser initialization/parsing
    ├── test_traversal.py        # Unit tests for AST node extraction logic
    ├── test_extractor.py        # Integration tests (parse real .cpp fixtures)
    └── fixtures/
        ├── sample_class.cpp     # Fixture: class with Doxygen, methods, inheritance
        ├── sample_functions.cpp # Fixture: global functions, templates, namespaces
        └── sample_mixed.h       # Fixture: header with classes + free functions
```

## 2. AST Node Types to Target

| Entity We Extract | tree-sitter Node Type(s) | Field to Read Name From |
|---|---|---|
| C++ Class | `class_specifier` | `child_by_field_name("name")` → `type_identifier` |
| C++ Struct | `struct_specifier` | `child_by_field_name("name")` → `type_identifier` |
| Global Function | `function_definition` (at `translation_unit` or `declaration_list` scope) | `child_by_field_name("declarator")` → `function_declarator` → `child_by_field_name("declarator")` → `identifier` |
| Doxygen Comment | `comment` (where text starts with `/**`, `///`, `//!`, or `/*!`) | `node.text` |
| Template Wrapper | `template_declaration` | Unwrap to inner `function_definition` or `class_specifier` |
| Namespace | `namespace_definition` | `child_by_field_name("name")` → `namespace_identifier` (used for URI context, not as a standalone entity) |

### Key Node-Type Constants (for `config.py`)

```python
TARGET_ENTITY_TYPES = {"class_specifier", "struct_specifier", "function_definition"}
TEMPLATE_WRAPPER    = "template_declaration"
NAMESPACE_NODE      = "namespace_definition"
COMMENT_NODE        = "comment"
CONTAINER_TYPES     = {"translation_unit", "declaration_list", "field_declaration_list"}
```

## 3. Data Model (`models.py`)

A single dataclass `ExtractedEntity` representing one extraction result:

| Field | Type | Description |
|---|---|---|
| `global_uri` | `str` | `RepoName::FilePath::EntityType::EntityName` |
| `repo_name` | `str` | Explicitly provided repo name |
| `file_path` | `str` | Relative path from repo root |
| `entity_type` | `str` | One of: `Class`, `Struct`, `Function` |
| `entity_name` | `str` | Qualified name (e.g., `MyNamespace::MyClass`) |
| `docstring` | `str \| None` | Concatenated Doxygen comment text, or `None` |
| `code_text` | `str` | Full source text of the entity (including template prefix if any) |
| `start_line` | `int` | 1-indexed start line in the file |
| `end_line` | `int` | 1-indexed end line in the file |
| `is_templated` | `bool` | Whether the entity is wrapped in `template_declaration` |

The `global_uri` is assembled as:
```
{repo_name}::{file_path}::{entity_type}::{entity_name}
```

## 4. Step-by-Step Execution Plan

### Step 1: Initialize Parser (`parser.py`)

1. Import `tree_sitter_cpp` and `tree_sitter.Language`, `tree_sitter.Parser`.
2. Create a module-level `CPP_LANGUAGE = Language(tscpp.language())`.
3. Create a function `create_parser() -> Parser` that returns a new `Parser` configured with `CPP_LANGUAGE`.
4. Create a function `parse_bytes(source: bytes) -> Tree` that parses raw bytes and returns the `Tree`.
5. Create a function `parse_file(file_path: str) -> Tuple[Tree, bytes]` that reads the file in binary mode, parses it, and returns both the tree and the source bytes (needed for text extraction later).

### Step 2: AST Traversal & Entity Extraction (`traversal.py`)

This is the core logic. It operates on a parsed `Tree` and `source_bytes`.

#### Step 2a: Walk Top-Level and Namespace-Scoped Nodes

1. Start at `tree.root_node` (type: `translation_unit`).
2. Iterate over `root_node.children`.
3. For each child:
   - If `node.type` is in `TARGET_ENTITY_TYPES` → extract it (Step 2c).
   - If `node.type == "template_declaration"` → unwrap: find the inner child whose type is in `TARGET_ENTITY_TYPES`, extract it, but use the `template_declaration` node as the outer node for comment search and code text range.
   - If `node.type == "namespace_definition"` → recurse into `node.child_by_field_name("body")` (a `declaration_list`), pushing the namespace name onto a `namespace_stack: List[str]` for URI qualification.
   - If `node.type == "declaration"` → check if its `type` field is a `class_specifier` or `struct_specifier` (classes are often wrapped in `declaration` nodes at the top level). If so, extract the inner specifier.
   - Otherwise → skip (includes `#include`, `using`, etc.).

#### Step 2b: Collect Preceding Doxygen Comments

For a given target node (the entity or its `template_declaration` wrapper):

1. Walk `prev_named_sibling` backward.
2. While the sibling's type is `"comment"`:
   - Check adjacency: `expected_row - sibling.end_point.row <= 1` (no blank-line gap).
   - Collect the comment node.
   - Update `expected_row = sibling.start_point.row`.
   - Move to the next `prev_named_sibling`.
3. Reverse the collected list (source order).
4. Filter to only Doxygen-style comments (text starts with `/**`, `///`, `//!`, `/*!`). If none are Doxygen-style, still include all adjacent comments (they may be informal documentation).
5. Concatenate their text into a single `docstring` string.

#### Step 2c: Extract Entity Name

**For `function_definition`:**
1. `declarator = node.child_by_field_name("declarator")` → should be `function_declarator`.
2. `name_node = declarator.child_by_field_name("declarator")` → could be `identifier`, `qualified_identifier`, `destructor_name`, or `field_identifier`.
3. Decode `name_node.text` to get the name string.
4. Prepend the namespace stack: `"::".join(namespace_stack + [name])`.

**For `class_specifier` / `struct_specifier`:**
1. `name_node = node.child_by_field_name("name")` → `type_identifier`.
2. Decode `name_node.text`.
3. Prepend the namespace stack.

**Entity type mapping:**
- `class_specifier` → `"Class"`
- `struct_specifier` → `"Struct"`
- `function_definition` → `"Function"`

#### Step 2d: Extract Code Text

1. Determine the outermost node for the code range:
   - If wrapped in `template_declaration`, use that node's byte range.
   - Otherwise, use the entity node's byte range.
2. Extract: `source_bytes[outer_node.start_byte : outer_node.end_byte].decode("utf-8")`.

#### Step 2e: Build the `ExtractedEntity`

Assemble all fields including the `global_uri`:
```
global_uri = f"{repo_name}::{file_path}::{entity_type}::{entity_name}"
```

### Step 3: High-Level Orchestrator (`extractor.py`)

Two public functions:

**`extract_file(file_path: str, repo_name: str, repo_root: str) -> List[ExtractedEntity]`**
1. Compute `relative_path = os.path.relpath(file_path, repo_root)`.
2. Call `parse_file(file_path)` → `(tree, source_bytes)`.
3. Call the traversal logic from Step 2 with `tree`, `source_bytes`, `repo_name`, `relative_path`.
4. Return the list of `ExtractedEntity`.

**`extract_directory(dir_path: str, repo_name: str) -> List[ExtractedEntity]`**
1. Recursively discover all `*.cpp`, `*.cc`, `*.cxx`, `*.h`, `*.hpp`, `*.hxx` files under `dir_path`.
2. For each file, call `extract_file(file_path, repo_name, repo_root=dir_path)`.
3. Aggregate and return all entities.
4. Log progress: file count, entity count, any parse errors.

### Step 4: Error Handling & Edge Cases

| Edge Case | Handling |
|---|---|
| Unparseable file (tree has errors) | Log a warning with file path and error node positions. Still extract whatever nodes parsed correctly (`has_error` check on root). |
| Anonymous class/struct (no name) | Skip extraction. Log at `DEBUG` level. |
| `extern "C" { ... }` blocks | Treat `linkage_specification` as a transparent wrapper — recurse into its `body` (a `declaration_list`). |
| Nested classes | Currently extracted as part of the outer class chunk (per the "class as one chunk" decision). Not extracted as separate entities. |
| Forward declarations (`class Foo;`) | These appear as `declaration` with no body. Skip them (no `field_declaration_list` body to extract). |
| Macros (`#define`, `#ifdef`) | Preprocessor nodes (`preproc_def`, `preproc_ifdef`, etc.) are skipped. tree-sitter parses them as-is without expansion. |
| Operator overloads | `function_definition` with `declarator` containing `operator_name`. Name extracted normally (e.g., `operator==`). |
| Constructor/destructor | Aliased to `function_definition` by tree-sitter-cpp. `grammar_name` distinguishes them, but `type` is `function_definition`. Name will be the class name or `~ClassName`. |

### Step 5: Output Format (Ready for Qdrant)

Each `ExtractedEntity` serializes to a dict suitable for the embedding pipeline:

```python
{
    "global_uri": "webrtc::src/video/encoder.cpp::Function::EncodeFrame",
    "repo_name": "webrtc",
    "file_path": "src/video/encoder.cpp",
    "entity_type": "Function",
    "entity_name": "EncodeFrame",
    "docstring": "/// Encodes a single video frame.\n/// Returns encoded bytes or nullptr on failure.",
    "code_text": "/// Encodes a single video frame.\n/// Returns encoded bytes or nullptr on failure.\nstd::unique_ptr<EncodedFrame> EncodeFrame(const VideoFrame& frame) {\n  ...\n}",
    "start_line": 42,
    "end_line": 78,
    "is_templated": false
}
```

The `code_text` field includes both the docstring and the code body concatenated — this is the text block that will be embedded as a single vector in Qdrant. The `global_uri` becomes the Qdrant point ID (or payload key) linking it back to Neo4j.

### Step 6: Dependencies (`requirements.txt` additions)

```
tree-sitter>=0.21.0
tree-sitter-cpp>=0.21.0
```

These are the only new dependencies. The rest (`pyyaml`, `qdrant-client`, `neo4j`) are already installed from the infrastructure phase.

### Step 7: Test Plan

| Test | File | What It Verifies |
|---|---|---|
| Parser init | `test_parser.py` | `create_parser()` returns a valid `Parser`; `parse_bytes()` returns a tree with `root_node.type == "translation_unit"` |
| Global function extraction | `test_traversal.py` | Fixture with `void foo() {}` → produces entity with `entity_type="Function"`, `entity_name="foo"` |
| Class extraction | `test_traversal.py` | Fixture with `class Foo { ... };` → produces entity with `entity_type="Class"`, `entity_name="Foo"`, `code_text` contains full body |
| Doxygen association | `test_traversal.py` | `/** doc */ void bar() {}` → entity has `docstring` containing `"doc"` |
| Namespace qualification | `test_traversal.py` | `namespace ns { void baz() {} }` → `entity_name == "ns::baz"` |
| Template handling | `test_traversal.py` | `template<typename T> void f(T x) {}` → entity has `is_templated=True`, `code_text` includes `template<...>` |
| URI format | `test_extractor.py` | Verify URI matches `repo::path::Type::Name` exactly |
| Batch directory scan | `test_extractor.py` | Point at `fixtures/` dir → returns entities from all fixture files |
| Error resilience | `test_parser.py` | Feed syntactically broken C++ → parser still returns a tree (partial), no crash |
```