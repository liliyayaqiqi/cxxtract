"""Build deterministic synthetic SCIP fixtures for Phase 0 tests."""

from __future__ import annotations

from pathlib import Path

from graphrag.proto import scip_pb2


FIXTURE_DIR = Path(__file__).resolve().parent
BASIC_FIXTURE = FIXTURE_DIR / "basic_graph.scip"
NESTED_FIXTURE = FIXTURE_DIR / "nested_scope.scip"


def _init_index() -> scip_pb2.Index:
    """Create an index with deterministic metadata."""
    index = scip_pb2.Index()
    index.metadata.version = scip_pb2.UnspecifiedProtocolVersion
    index.metadata.tool_info.name = "phase0-fixture-builder"
    index.metadata.tool_info.version = "1.0.0"
    index.metadata.project_root = "file:///phase0-fixtures"
    index.metadata.text_document_encoding = scip_pb2.UTF8
    return index


def _add_occurrence(
    document: scip_pb2.Document,
    symbol: str,
    range_values: list[int],
    symbol_roles: int,
    enclosing_range: list[int] | None = None,
) -> None:
    """Append an occurrence helper."""
    occurrence = document.occurrences.add()
    occurrence.symbol = symbol
    occurrence.range.extend(range_values)
    occurrence.symbol_roles = symbol_roles
    if enclosing_range:
        occurrence.enclosing_range.extend(enclosing_range)


def build_basic_fixture(path: Path = BASIC_FIXTURE) -> Path:
    """Build a simple class+method+function graph fixture."""
    index = _init_index()

    document = index.documents.add()
    document.language = "cpp"
    document.relative_path = "src/basic.cpp"
    document.position_encoding = scip_pb2.UTF8CodeUnitOffsetFromLineStart

    class_symbol = "cxx . . $ YAML/Widget#"
    method_symbol = "cxx . . $ YAML/Widget#encode(3f1c)."
    function_symbol = "cxx . . $ YAML/run(89ab)."

    class_info = document.symbols.add()
    class_info.symbol = class_symbol
    class_info.kind = 7  # SymbolInformation.Kind.Class
    class_info.display_name = "Widget"

    method_info = document.symbols.add()
    method_info.symbol = method_symbol
    method_info.kind = 26  # SymbolInformation.Kind.Method
    method_info.display_name = "encode"
    relation = method_info.relationships.add()
    relation.symbol = class_symbol
    relation.is_type_definition = True

    function_info = document.symbols.add()
    function_info.symbol = function_symbol
    function_info.kind = 17  # SymbolInformation.Kind.Function
    function_info.display_name = "run"

    # Definition occurrences (with deterministic enclosing ranges).
    _add_occurrence(
        document=document,
        symbol=class_symbol,
        range_values=[0, 0, 1, 1],
        symbol_roles=0x1,  # Definition
        enclosing_range=[0, 0, 1, 1],
    )
    _add_occurrence(
        document=document,
        symbol=method_symbol,
        range_values=[2, 0, 4, 1],
        symbol_roles=0x1,
        enclosing_range=[2, 0, 4, 1],
    )
    _add_occurrence(
        document=document,
        symbol=function_symbol,
        range_values=[6, 0, 8, 1],
        symbol_roles=0x1,
        enclosing_range=[6, 0, 8, 1],
    )

    # A reference to encode() from run().
    _add_occurrence(
        document=document,
        symbol=method_symbol,
        range_values=[7, 4, 7, 10],
        symbol_roles=0x8,  # ReadAccess
    )

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(index.SerializeToString())
    return path


def build_nested_scope_fixture(path: Path = NESTED_FIXTURE) -> Path:
    """Build a fixture with nested definitions for scope-attribution tests."""
    index = _init_index()

    document = index.documents.add()
    document.language = "cpp"
    document.relative_path = "src/nested.cpp"
    document.position_encoding = scip_pb2.UTF8CodeUnitOffsetFromLineStart

    outer_symbol = "cxx . . $ YAML/outer(aaaa)."
    inner_symbol = "cxx . . $ YAML/inner(bbbb)."
    helper_symbol = "cxx . . $ YAML/helper(cccc)."

    outer_info = document.symbols.add()
    outer_info.symbol = outer_symbol
    outer_info.kind = 17
    outer_info.display_name = "outer"

    inner_info = document.symbols.add()
    inner_info.symbol = inner_symbol
    inner_info.kind = 17
    inner_info.display_name = "inner"

    helper_info = document.symbols.add()
    helper_info.symbol = helper_symbol
    helper_info.kind = 17
    helper_info.display_name = "helper"

    # Intentionally add outer before inner so buggy "first-write-wins"
    # behavior can be reproduced deterministically.
    _add_occurrence(
        document=document,
        symbol=outer_symbol,
        range_values=[0, 0, 20, 1],
        symbol_roles=0x1,
        enclosing_range=[0, 0, 20, 1],
    )
    _add_occurrence(
        document=document,
        symbol=inner_symbol,
        range_values=[5, 0, 10, 1],
        symbol_roles=0x1,
        enclosing_range=[5, 0, 10, 1],
    )
    _add_occurrence(
        document=document,
        symbol=helper_symbol,
        range_values=[6, 2, 6, 8],
        symbol_roles=0x8,
    )

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(index.SerializeToString())
    return path


def build_all_fixtures(force: bool = False) -> dict[str, Path]:
    """Generate all synthetic fixtures, optionally skipping existing files."""
    outputs: dict[str, Path] = {}

    if force or not BASIC_FIXTURE.exists():
        outputs["basic_graph"] = build_basic_fixture(BASIC_FIXTURE)
    else:
        outputs["basic_graph"] = BASIC_FIXTURE

    if force or not NESTED_FIXTURE.exists():
        outputs["nested_scope"] = build_nested_scope_fixture(NESTED_FIXTURE)
    else:
        outputs["nested_scope"] = NESTED_FIXTURE

    return outputs


if __name__ == "__main__":
    generated = build_all_fixtures(force=True)
    for key, file_path in generated.items():
        print(f"{key}: {file_path}")
