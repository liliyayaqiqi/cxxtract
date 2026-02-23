"""
Layer 1: Extraction Engine

Tree-sitter-based C++ source code parser and entity extractor.
Extracts classes, functions, and their associated Doxygen comments.
"""

from extraction.models import ExtractedEntity
from extraction.parser import create_parser, parse_file, parse_bytes, count_error_nodes
from extraction.traversal import extract_entities_from_tree
from extraction.extractor import (
    extract_file,
    extract_directory,
    extract_to_dict_list,
    discover_cpp_files,
    ExtractionStats,
)

__all__ = [
    # Data models
    "ExtractedEntity",
    "ExtractionStats",
    # Low-level parsing
    "create_parser",
    "parse_file",
    "parse_bytes",
    "count_error_nodes",
    # Mid-level extraction
    "extract_entities_from_tree",
    # High-level orchestration
    "extract_file",
    "extract_directory",
    "extract_to_dict_list",
    "discover_cpp_files",
]
