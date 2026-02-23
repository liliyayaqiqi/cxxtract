"""Global URI contract shared by extraction and GraphRAG layers."""

from __future__ import annotations

import re
from typing import TypedDict

GLOBAL_URI_SEPARATOR = "::"


class ParsedGlobalUri(TypedDict):
    """Parsed Global URI payload."""

    repo_name: str
    file_path: str
    entity_type: str
    entity_name: str


_WHITESPACE_RE = re.compile(r"\s+")
_SCOPE_SEPARATOR_RE = re.compile(r"\s*::\s*")
_DESTRUCTOR_SPACING_RE = re.compile(r"::\s*~")


def normalize_cpp_entity_name(entity_name: str) -> str:
    """Normalize C++ entity names into a canonical, URI-safe form.

    The goal is deterministic identity across parsers/indexers when trivial
    whitespace variations occur.

    Args:
        entity_name: Raw entity name from parser output.

    Returns:
        Canonicalized entity name.
    """
    normalized = entity_name.strip()
    normalized = _SCOPE_SEPARATOR_RE.sub("::", normalized)
    normalized = _DESTRUCTOR_SPACING_RE.sub("::~", normalized)
    normalized = _WHITESPACE_RE.sub(" ", normalized)
    return normalized.strip()


def create_global_uri(
    repo_name: str,
    file_path: str,
    entity_type: str,
    entity_name: str,
) -> str:
    """Create a Global URI using the shared identity contract.

    Args:
        repo_name: Repository name.
        file_path: Path relative to repo root.
        entity_type: One of Class/Struct/Function.
        entity_name: Canonicalized fully-qualified C++ entity name.

    Returns:
        Global URI in format:
        ``RepoName::FilePath::EntityType::EntityName``.
    """
    canonical_name = normalize_cpp_entity_name(entity_name)
    return (
        f"{repo_name}{GLOBAL_URI_SEPARATOR}{file_path}"
        f"{GLOBAL_URI_SEPARATOR}{entity_type}{GLOBAL_URI_SEPARATOR}{canonical_name}"
    )


def parse_global_uri(global_uri: str) -> ParsedGlobalUri:
    """Parse Global URI into components.

    Args:
        global_uri: URI produced by ``create_global_uri``.

    Returns:
        ParsedGlobalUri dict.

    Raises:
        ValueError: If URI does not contain required components.
    """
    parts = global_uri.split(GLOBAL_URI_SEPARATOR)
    if len(parts) < 4:
        raise ValueError(f"Malformed Global URI: {global_uri}")

    return ParsedGlobalUri(
        repo_name=parts[0],
        file_path=parts[1],
        entity_type=parts[2],
        entity_name=GLOBAL_URI_SEPARATOR.join(parts[3:]),
    )

