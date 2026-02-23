"""Tests for backward-compatible URI contract parsing and function hashes."""

from core.uri_contract import (
    build_identity_key,
    create_global_uri,
    make_function_signature_hash,
    parse_global_uri,
)


def test_parse_global_uri_backward_compatible_old_format() -> None:
    uri = "repo::src/a.cpp::Function::foo"
    parsed = parse_global_uri(uri)
    assert parsed["repo_name"] == "repo"
    assert parsed["file_path"] == "src/a.cpp"
    assert parsed["entity_type"] == "Function"
    assert parsed["entity_name"] == "foo"
    assert "signature_hash" not in parsed


def test_parse_global_uri_new_function_format_with_signature_hash() -> None:
    uri = create_global_uri(
        repo_name="repo",
        file_path="src/a.cpp",
        entity_type="Function",
        entity_name="foo",
        function_signature="int foo(int x) const",
    )
    parsed = parse_global_uri(uri)
    assert parsed["entity_name"] == "foo"
    assert parsed["signature_hash"].startswith("sig_")


def test_make_function_signature_hash_is_stable() -> None:
    sig = "int foo( const std::string &x )"
    h1 = make_function_signature_hash(sig)
    h2 = make_function_signature_hash(sig)
    assert h1 == h2
    assert h1.startswith("sig_")


def test_build_identity_key_uses_sig_hash_when_present() -> None:
    key = build_identity_key("repo::a.cpp::Function::foo", "sig_deadbeef")
    assert key == "repo::a.cpp::Function::foo::sig_deadbeef"


def test_build_identity_key_falls_back_to_global_uri() -> None:
    key = build_identity_key("repo::a.cpp::Class::Foo")
    assert key == "repo::a.cpp::Class::Foo"
