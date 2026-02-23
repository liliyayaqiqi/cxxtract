"""Core shared contracts and utilities."""

from core.uri_contract import (
    GLOBAL_URI_SEPARATOR,
    create_global_uri,
    normalize_cpp_entity_name,
    parse_global_uri,
)

__all__ = [
    "GLOBAL_URI_SEPARATOR",
    "create_global_uri",
    "normalize_cpp_entity_name",
    "parse_global_uri",
]

