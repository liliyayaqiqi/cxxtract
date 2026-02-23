"""Global URI contract shared by extraction and GraphRAG layers."""

from __future__ import annotations

import hashlib
import re
from typing import NotRequired, TypedDict

GLOBAL_URI_SEPARATOR = "::"


class ParsedGlobalUri(TypedDict):
    """Parsed Global URI payload."""

    repo_name: str
    file_path: str
    entity_type: str
    entity_name: str
    signature_hash: NotRequired[str]


_WHITESPACE_RE = re.compile(r"\s+")
_SCOPE_SEPARATOR_RE = re.compile(r"\s*::\s*")
_DESTRUCTOR_SPACING_RE = re.compile(r"::\s*~")
_SIG_TOKEN_RE = re.compile(r"^sig_[0-9a-f]{8,40}$")


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
    function_signature: str | None = None,
    function_sig_hash: str | None = None,
) -> str:
    """Create a Global URI using the shared identity contract.

    Args:
        repo_name: Repository name.
        file_path: Path relative to repo root.
        entity_type: One of Class/Struct/Function.
        entity_name: Canonicalized fully-qualified C++ entity name.
        function_signature: Optional raw function signature source text used
            to derive a stable discriminator.
        function_sig_hash: Optional precomputed signature hash token
            (e.g. ``sig_ab12cd34ef56``). If provided, takes precedence over
            ``function_signature``.

    Returns:
        Global URI in format:
        ``RepoName::FilePath::EntityType::EntityName``.
    """
    canonical_name = normalize_cpp_entity_name(entity_name)
    uri = (
        f"{repo_name}{GLOBAL_URI_SEPARATOR}{file_path}"
        f"{GLOBAL_URI_SEPARATOR}{entity_type}{GLOBAL_URI_SEPARATOR}{canonical_name}"
    )
    if entity_type == "Function":
        sig_token = function_sig_hash
        if sig_token is None and function_signature:
            sig_token = make_function_signature_hash(function_signature)
        if sig_token:
            uri = f"{uri}{GLOBAL_URI_SEPARATOR}{sig_token}"
    return uri


def make_function_signature_hash(
    signature_source: str,
    digest_length: int = 12,
) -> str:
    """Create a stable short hash token for function signature disambiguation."""
    canonical = normalize_cpp_entity_name(signature_source)
    canonical = _WHITESPACE_RE.sub(" ", canonical).strip()
    if not canonical:
        canonical = "<empty-signature>"
    length = max(8, min(digest_length, 40))
    digest = hashlib.sha1(canonical.encode("utf-8")).hexdigest()[:length]
    return f"sig_{digest}"


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

    entity_type = parts[2]
    entity_name_parts = parts[3:]
    signature_hash: str | None = None
    if entity_type == "Function" and len(parts) >= 5 and _SIG_TOKEN_RE.match(parts[-1]):
        signature_hash = parts[-1]
        entity_name_parts = parts[3:-1]

    payload = ParsedGlobalUri(
        repo_name=parts[0],
        file_path=parts[1],
        entity_type=entity_type,
        entity_name=GLOBAL_URI_SEPARATOR.join(entity_name_parts),
    )
    if signature_hash is not None:
        payload["signature_hash"] = signature_hash
    return payload
