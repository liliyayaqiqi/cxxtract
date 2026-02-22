"""
SCIP indexing via scip-clang subprocess invocation.

Wraps the scip-clang binary to generate .scip protobuf index files
from compile_commands.json artifacts.
"""

import logging
import os
import subprocess
from typing import Optional

from graphrag.config import SCIP_CLANG_PATH, DEFAULT_INDEX_OUTPUT

logger = logging.getLogger(__name__)


def run_scip_clang(
    compdb_path: str,
    index_output_path: str = DEFAULT_INDEX_OUTPUT,
    jobs: Optional[int] = None,
    log_level: str = "info",
) -> str:
    """Invoke scip-clang to generate a SCIP index from compile_commands.json.
    
    Args:
        compdb_path: Path to compile_commands.json file.
        index_output_path: Output path for the .scip index file.
        jobs: Number of parallel indexing processes (default: CPU count).
        log_level: SCIP log level (debug, info, warning, error).
        
    Returns:
        Absolute path to the generated .scip index file.
        
    Raises:
        FileNotFoundError: If compile_commands.json not found.
        subprocess.CalledProcessError: If scip-clang fails.
        
    Example:
        >>> index_path = run_scip_clang("build/compile_commands.json")
        >>> # Produces output/index.scip
    """
    # Verify compile_commands.json exists
    if not os.path.isfile(compdb_path):
        raise FileNotFoundError(
            f"Compilation database not found: {compdb_path}. "
            f"Generate it with your build system (CMake, Bear, etc.)"
        )
    
    # Ensure output directory exists
    os.makedirs(os.path.dirname(os.path.abspath(index_output_path)), exist_ok=True)
    
    # Build scip-clang command
    cmd = [
        SCIP_CLANG_PATH,
        "--compdb-path", compdb_path,
        "--index-output-path", index_output_path,
        "--log-level", log_level,
    ]
    
    if jobs is not None:
        cmd.extend(["-j", str(jobs)])
    
    logger.info(
        f"Running SCIP indexer: {SCIP_CLANG_PATH} on {compdb_path}"
    )
    logger.debug(f"Full command: {' '.join(cmd)}")
    
    try:
        result = subprocess.run(
            cmd,
            check=True,
            capture_output=True,
            text=True,
        )
        
        # Log scip-clang output
        if result.stdout:
            for line in result.stdout.strip().split("\n"):
                logger.info(f"[scip-clang] {line}")
        
        if result.stderr:
            for line in result.stderr.strip().split("\n"):
                logger.warning(f"[scip-clang stderr] {line}")
        
    except subprocess.CalledProcessError as e:
        logger.error(f"scip-clang failed with exit code {e.returncode}")
        if e.stdout:
            logger.error(f"stdout: {e.stdout}")
        if e.stderr:
            logger.error(f"stderr: {e.stderr}")
        raise
    
    # Verify output file was created
    if not os.path.isfile(index_output_path):
        raise FileNotFoundError(
            f"scip-clang completed but output file not found: {index_output_path}"
        )
    
    file_size = os.path.getsize(index_output_path)
    logger.info(
        f"SCIP index generated: {index_output_path} ({file_size / 1024 / 1024:.2f} MB)"
    )
    
    return os.path.abspath(index_output_path)
