"""
Configuration constants for C++ AST extraction.

Defines the tree-sitter node type strings used for entity extraction.
"""

from typing import Set

# Target entity types we extract as standalone entities
TARGET_ENTITY_TYPES: Set[str] = {
    "class_specifier",
    "struct_specifier",
    "function_definition",
}

# Template wrapper node type
TEMPLATE_WRAPPER: str = "template_declaration"

# Namespace definition node type
NAMESPACE_NODE: str = "namespace_definition"

# Comment node type (includes //, /* */, /** */)
COMMENT_NODE: str = "comment"

# Container types whose children we scan
CONTAINER_TYPES: Set[str] = {
    "translation_unit",      # File root
    "declaration_list",      # Namespace body
    "field_declaration_list", # Class/struct body
}

# Wrapper types that should be treated as transparent
TRANSPARENT_WRAPPERS: Set[str] = {
    "linkage_specification",  # extern "C" { ... }
}

# Preprocessor directives that may contain code we need to traverse
PREPROCESSOR_CONTAINERS: Set[str] = {
    "preproc_ifdef",
    "preproc_ifndef",
    "preproc_if",
    "preproc_elif",
}

# Declaration node type (classes/structs can be wrapped in this)
DECLARATION_NODE: str = "declaration"

# Doxygen comment prefixes
DOXYGEN_PREFIXES: tuple = (
    "/**",
    "///",
    "//!",
    "/*!",
)

# C++ file extensions
CPP_EXTENSIONS: Set[str] = {
    ".cpp",
    ".cc",
    ".cxx",
    ".c",
    ".h",
    ".hpp",
    ".hxx",
}

# Entity type mapping (node type -> Entity type string)
ENTITY_TYPE_MAP: dict = {
    "class_specifier": "Class",
    "struct_specifier": "Struct",
    "function_definition": "Function",
}

# Extraction policy defaults
DEFAULT_INCLUDE_DECLARATIONS: bool = False
DEFAULT_EXTERN_C_DECLARATIONS: bool = False
