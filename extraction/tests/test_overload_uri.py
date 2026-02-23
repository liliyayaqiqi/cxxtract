"""Regression tests for overload-safe identity with stable join URIs."""

from pathlib import Path

from extraction.extractor import extract_file
from extraction.parser import parse_bytes
from extraction.traversal import extract_entities_from_tree
from ingestion.qdrant_loader import generate_point_id


def test_overloaded_functions_share_join_uri_but_have_distinct_point_ids() -> None:
    fixtures_dir = Path(__file__).resolve().parent / "fixtures"
    file_path = fixtures_dir / "overloaded_functions.cpp"
    entities = extract_file(str(file_path), "overload_repo", str(fixtures_dir))

    overloads = [
        entity for entity in entities
        if entity.entity_type == "Function" and entity.entity_name == "add"
    ]
    assert len(overloads) == 2

    uris = [entity.global_uri for entity in overloads]
    assert len(set(uris)) == 1
    assert all(uri == "overload_repo::overloaded_functions.cpp::Function::add" for uri in uris)
    assert len({entity.function_sig_hash for entity in overloads}) == 2

    point_ids = [
        generate_point_id(entity.global_uri, function_sig_hash=entity.function_sig_hash)
        for entity in overloads
    ]
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
    assert len({entity.global_uri for entity in overloads}) == 1
    assert len({entity.function_sig_hash for entity in overloads}) == 2
