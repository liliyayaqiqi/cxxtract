"""
High-level orchestrator for C++ entity extraction.

This module provides the main entry points for extracting entities from
single files or entire directory trees.
"""

import logging
import os
from pathlib import Path
from typing import List, Optional, Dict, Any

from extraction.config import CPP_EXTENSIONS
from extraction.models import ExtractedEntity
from extraction.parser import parse_file
from extraction.traversal import extract_entities_from_tree

logger = logging.getLogger(__name__)


class ExtractionStats:
    """Statistics for an extraction operation."""
    
    def __init__(self):
        self.files_processed = 0
        self.files_failed = 0
        self.entities_extracted = 0
        self.parse_errors = 0
        
    def to_dict(self) -> Dict[str, int]:
        """Convert stats to dictionary."""
        return {
            "files_processed": self.files_processed,
            "files_failed": self.files_failed,
            "entities_extracted": self.entities_extracted,
            "parse_errors": self.parse_errors,
        }
    
    def __str__(self) -> str:
        """String representation of stats."""
        return (
            f"ExtractionStats(processed={self.files_processed}, "
            f"failed={self.files_failed}, entities={self.entities_extracted}, "
            f"parse_errors={self.parse_errors})"
        )


def extract_file(
    file_path: str,
    repo_name: str,
    repo_root: Optional[str] = None
) -> List[ExtractedEntity]:
    """Extract all entities from a single C++ source file.
    
    Args:
        file_path: Absolute or relative path to the C++ file.
        repo_name: Repository name for URI generation.
        repo_root: Repository root directory. If None, uses file's parent directory.
        
    Returns:
        List of extracted entities from the file.
        
    Raises:
        FileNotFoundError: If the file does not exist.
        ValueError: If the file is not a C++ source file.
        
    Example:
        >>> entities = extract_file("src/main.cpp", "my_project", "/path/to/repo")
        >>> for entity in entities:
        ...     print(entity.global_uri)
    """
    file_path = os.path.abspath(file_path)
    
    # Verify file exists
    if not os.path.isfile(file_path):
        raise FileNotFoundError(f"File not found: {file_path}")
    
    # Verify it's a C++ file
    ext = os.path.splitext(file_path)[1]
    if ext not in CPP_EXTENSIONS:
        raise ValueError(
            f"File {file_path} is not a C++ source file. "
            f"Expected one of: {CPP_EXTENSIONS}"
        )
    
    # Determine repo root
    if repo_root is None:
        repo_root = os.path.dirname(file_path)
    else:
        repo_root = os.path.abspath(repo_root)
    
    # Compute relative path
    try:
        relative_path = os.path.relpath(file_path, repo_root)
    except ValueError:
        # On Windows, relpath fails if paths are on different drives
        logger.warning(
            f"Cannot compute relative path for {file_path} from {repo_root}. "
            f"Using absolute path."
        )
        relative_path = file_path
    
    logger.info(f"Extracting entities from {relative_path}")
    
    try:
        # Parse the file
        tree, source_bytes = parse_file(file_path)
        
        # Check for parse errors
        if tree.root_node.has_error:
            logger.warning(f"File {relative_path} contains syntax errors")
        
        # Extract entities
        entities = extract_entities_from_tree(
            tree, source_bytes, repo_name, relative_path
        )
        
        logger.info(f"Extracted {len(entities)} entities from {relative_path}")
        return entities
        
    except Exception as e:
        logger.error(f"Error extracting entities from {relative_path}: {e}")
        raise


def discover_cpp_files(directory: str) -> List[str]:
    """Recursively discover all C++ source files in a directory.
    
    Args:
        directory: Root directory to search.
        
    Returns:
        List of absolute paths to C++ files.
        
    Example:
        >>> files = discover_cpp_files("/path/to/repo")
        >>> len(files)
        42
    """
    cpp_files = []
    directory = os.path.abspath(directory)
    
    logger.info(f"Discovering C++ files in {directory}")
    
    for root, dirs, files in os.walk(directory):
        # Skip hidden directories and common build/cache directories
        dirs[:] = [d for d in dirs if not d.startswith('.') and d not in {
            'build', 'cmake-build-debug', 'cmake-build-release',
            'node_modules', 'venv', '__pycache__', 'dist', 'out'
        }]
        
        for file in files:
            ext = os.path.splitext(file)[1]
            if ext in CPP_EXTENSIONS:
                cpp_files.append(os.path.join(root, file))
    
    logger.info(f"Found {len(cpp_files)} C++ files")
    return sorted(cpp_files)


def extract_directory(
    directory: str,
    repo_name: str,
    repo_root: Optional[str] = None,
    continue_on_error: bool = True
) -> tuple[List[ExtractedEntity], ExtractionStats]:
    """Extract entities from all C++ files in a directory tree.
    
    Args:
        directory: Root directory to process.
        repo_name: Repository name for URI generation.
        repo_root: Repository root for computing relative paths. 
                   If None, uses the directory parameter.
        continue_on_error: If True, continue processing files even if some fail.
                          If False, raise exception on first error.
        
    Returns:
        A tuple of (entities, stats) where:
        - entities: List of all extracted entities
        - stats: ExtractionStats object with processing statistics
        
    Raises:
        FileNotFoundError: If directory does not exist.
        
    Example:
        >>> entities, stats = extract_directory("/path/to/repo", "my_project")
        >>> print(f"Extracted {stats.entities_extracted} entities from {stats.files_processed} files")
    """
    directory = os.path.abspath(directory)
    
    if not os.path.isdir(directory):
        raise FileNotFoundError(f"Directory not found: {directory}")
    
    # Use directory as repo_root if not specified
    if repo_root is None:
        repo_root = directory
    else:
        repo_root = os.path.abspath(repo_root)
    
    stats = ExtractionStats()
    all_entities = []
    
    # Discover all C++ files
    cpp_files = discover_cpp_files(directory)
    
    if not cpp_files:
        logger.warning(f"No C++ files found in {directory}")
        return all_entities, stats
    
    logger.info(f"Processing {len(cpp_files)} C++ files from {directory}")
    
    for file_path in cpp_files:
        try:
            entities = extract_file(file_path, repo_name, repo_root)
            all_entities.extend(entities)
            stats.files_processed += 1
            stats.entities_extracted += len(entities)
            
        except FileNotFoundError as e:
            logger.error(f"File not found: {e}")
            stats.files_failed += 1
            if not continue_on_error:
                raise
                
        except ValueError as e:
            logger.error(f"Invalid file: {e}")
            stats.files_failed += 1
            if not continue_on_error:
                raise
                
        except Exception as e:
            logger.error(f"Unexpected error processing {file_path}: {e}", exc_info=True)
            stats.files_failed += 1
            if not continue_on_error:
                raise
    
    logger.info(f"Extraction complete: {stats}")
    return all_entities, stats


def extract_to_dict_list(
    source: str,
    repo_name: str,
    repo_root: Optional[str] = None
) -> List[Dict[str, Any]]:
    """Extract entities and return as a list of dictionaries.
    
    This is a convenience function that automatically detects whether
    the source is a file or directory and returns results in dict format
    ready for JSON serialization or database insertion.
    
    Args:
        source: Path to a file or directory.
        repo_name: Repository name for URI generation.
        repo_root: Repository root directory.
        
    Returns:
        List of entity dictionaries.
        
    Example:
        >>> entities = extract_to_dict_list("src/", "my_project")
        >>> import json
        >>> json.dump(entities, open("entities.json", "w"), indent=2)
    """
    source = os.path.abspath(source)
    
    if os.path.isfile(source):
        entities = extract_file(source, repo_name, repo_root)
    elif os.path.isdir(source):
        entities, stats = extract_directory(source, repo_name, repo_root)
        logger.info(f"Extraction stats: {stats}")
    else:
        raise FileNotFoundError(f"Source not found: {source}")
    
    return [entity.to_dict() for entity in entities]
