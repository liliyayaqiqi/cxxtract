# Extraction Engine - Quick Reference

## Installation

```bash
pip install tree-sitter tree-sitter-cpp
```

## Quick Start

### Extract from a single file
```python
from extraction import extract_file

entities = extract_file("src/main.cpp", "my_project")
for entity in entities:
    print(f"{entity.entity_type}: {entity.entity_name}")
```

### Extract from entire repository
```python
from extraction import extract_directory

entities, stats = extract_directory("/path/to/repo", "my_project")
print(f"Extracted {len(entities)} entities from {stats.files_processed} files")
```

### Extract to JSON format
```python
from extraction import extract_to_dict_list
import json

entities = extract_to_dict_list("/path/to/repo", "my_project")
with open("entities.json", "w") as f:
    json.dump(entities, f, indent=2)
```

## API Reference

### High-Level Functions

#### `extract_file(file_path, repo_name, repo_root=None)`
Extract entities from a single C++ file.

**Returns:** `List[ExtractedEntity]`

#### `extract_directory(directory, repo_name, repo_root=None, continue_on_error=True)`
Extract entities from all C++ files in a directory tree.

**Returns:** `(List[ExtractedEntity], ExtractionStats)`

#### `extract_to_dict_list(source, repo_name, repo_root=None)`
Extract and convert to JSON-serializable dictionaries.

**Returns:** `List[Dict[str, Any]]`

### Mid-Level Functions

#### `extract_entities_from_tree(tree, source_bytes, repo_name, file_path)`
Extract entities from a parsed AST tree.

**Returns:** `List[ExtractedEntity]`

### Low-Level Functions

#### `parse_file(file_path)`
Parse a C++ file using tree-sitter.

**Returns:** `(Tree, bytes)`

#### `parse_bytes(source)`
Parse C++ source code from bytes.

**Returns:** `Tree`

## Data Models

### ExtractedEntity

```python
@dataclass
class ExtractedEntity:
    global_uri: str        # Unique identifier
    repo_name: str         # Repository name
    file_path: str         # Relative file path
    entity_type: str       # "Class", "Struct", or "Function"
    entity_name: str       # Fully qualified name
    docstring: str | None  # Doxygen comment or None
    code_text: str         # Full source code
    start_line: int        # 1-indexed start line
    end_line: int          # 1-indexed end line
    is_templated: bool     # Template detection
```

### ExtractionStats

```python
@dataclass
class ExtractionStats:
    files_processed: int
    files_failed: int
    entities_extracted: int
    parse_errors: int
```

## Supported Features

- ✅ C++ classes and structs
- ✅ Global functions and methods
- ✅ Doxygen comments (///, /**, //!, /*!)
- ✅ Namespace qualification
- ✅ Template detection
- ✅ Forward declaration filtering
- ✅ Preprocessor directive handling
- ✅ Error-resilient parsing
- ✅ Multi-file repository support

## Running Tests

```bash
# Run all tests
python -m pytest extraction/tests/ -v

# Run specific test module
python -m pytest extraction/tests/test_parser.py -v
python -m pytest extraction/tests/test_traversal.py -v
python -m pytest extraction/tests/test_extractor.py -v

# Run with coverage
python -m pytest extraction/tests/ --cov=extraction --cov-report=html
```

## Examples

See `EXTRACTION_SUMMARY.md` for comprehensive examples and use cases.
