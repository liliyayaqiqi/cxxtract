"""Regression tests for overload-safe Global URI generation."""

from pathlib import Path

from core.uri_contract import parse_global_uri
from extraction.extractor import extract_file
from extraction.parser import parse_bytes
from extraction.traversal import extract_entities_from_tree
from ingestion.qdrant_loader import generate_point_id


def test_overloaded_functions_have_distinct_global_uri() -> None:
    fixtures_dir = Path(__file__).resolve().parent / "fixtures"
    file_path = fixtures_dir / "overloaded_functions.cpp"
    entities = extract_file(str(file_path), "overload_repo", str(fixtures_dir))

    overloads = [
        entity for entity in entities
        if entity.entity_type == "Function" and entity.entity_name == "add"
    ]
    assert len(overloads) == 2

    uris = [entity.global_uri for entity in overloads]
    assert len(set(uris)) == 2
    for uri in uris:
        assert "::Function::add::sig_" in uri
        parsed = parse_global_uri(uri)
        assert parsed["entity_name"] == "add"
        assert "signature_hash" in parsed

    point_ids = [generate_point_id(uri) for uri in uris]
    assert len(set(point_ids)) == 2


def test_regression_guard_same_name_definitions_do_not_collide() -> None:
    source = b"""
int foo(int value) { return value; }
int foo(double value) { return (int)value; }
"""
    tree = parse_bytes(source)
    entities = extract_entities_from_tree(
        tree=tree,
        source_bytes=source,
        repo_name="guard_repo",
        file_path="guard.cpp",
    )
    overloads = [
        entity for entity in entities
        if entity.entity_type == "Function" and entity.entity_name == "foo"
    ]
    assert len(overloads) == 2
    assert len({entity.global_uri for entity in overloads}) == 2
