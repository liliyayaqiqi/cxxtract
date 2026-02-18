"""
Tree-sitter parser initialization and file parsing utilities.

This module provides functions to initialize the C++ parser and parse source files.
"""

import logging
from typing import Tuple
import tree_sitter_cpp as tscpp
from tree_sitter import Language, Parser, Tree

# Configure logging
logger = logging.getLogger(__name__)

# Module-level language constant
CPP_LANGUAGE = Language(tscpp.language())


def create_parser() -> Parser:
    """Create and configure a tree-sitter parser for C++.
    
    Returns:
        A Parser instance configured with the C++ language.
    
    Example:
        >>> parser = create_parser()
        >>> tree = parser.parse(b"int main() { return 0; }")
    """
    parser = Parser(CPP_LANGUAGE)
    logger.debug("Created tree-sitter C++ parser")
    return parser


def parse_bytes(source: bytes) -> Tree:
    """Parse raw bytes of C++ source code.
    
    Args:
        source: UTF-8 encoded bytes of C++ source code.
        
    Returns:
        A Tree object representing the parsed AST.
        
    Raises:
        TypeError: If source is not bytes.
        
    Example:
        >>> tree = parse_bytes(b"void foo() {}")
        >>> tree.root_node.type
        'translation_unit'
    """
    if not isinstance(source, bytes):
        raise TypeError(f"Source must be bytes, got {type(source).__name__}")
    
    parser = create_parser()
    tree = parser.parse(source)
    
    if tree.root_node.has_error:
        logger.warning("Parsed tree contains syntax errors")
    
    logger.debug(f"Parsed {len(source)} bytes of C++ code")
    return tree


def parse_file(file_path: str) -> Tuple[Tree, bytes]:
    """Parse a C++ source file from disk.
    
    Args:
        file_path: Path to the .cpp, .cc, .h, or .hpp file.
        
    Returns:
        A tuple of (Tree, source_bytes) where:
        - Tree is the parsed AST
        - source_bytes is the raw file content as bytes
        
    Raises:
        FileNotFoundError: If the file does not exist.
        IOError: If the file cannot be read.
        
    Example:
        >>> tree, source = parse_file("example.cpp")
        >>> tree.root_node.type
        'translation_unit'
    """
    try:
        with open(file_path, "rb") as f:
            source_bytes = f.read()
    except FileNotFoundError:
        logger.error(f"File not found: {file_path}")
        raise
    except IOError as e:
        logger.error(f"Error reading file {file_path}: {e}")
        raise
    
    tree = parse_bytes(source_bytes)
    
    if tree.root_node.has_error:
        logger.warning(f"File {file_path} contains syntax errors")
    
    logger.info(f"Successfully parsed file: {file_path}")
    return tree, source_bytes
