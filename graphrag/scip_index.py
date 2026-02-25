"""
SCIP indexing via scip-clang subprocess invocation.

Wraps the scip-clang binary to generate .scip protobuf index files
from compile_commands.json artifacts.
"""

import logging
import json
import os
import re
import subprocess
import tempfile
from typing import Optional

from graphrag.config import SCIP_CLANG_PATH, DEFAULT_INDEX_OUTPUT
from graphrag.proto import scip_pb2

logger = logging.getLogger(__name__)

_COMMON_BUILD_DIR_NAMES = {
    "build",
    "cmake-build-debug",
    "cmake-build-release",
    "cmake-build-relwithdebinfo",
    "cmake-build-minsizerel",
    "out",
}


_WINDOWS_ABS_RE = re.compile(r"^[A-Za-z]:[\\/]")


def _is_windows_abs(path: str) -> bool:
    return bool(_WINDOWS_ABS_RE.match(path))


def _normalize_windows_path(path: str) -> str:
    return path.replace("\\", "/")


def _common_windows_anchor(paths: list[str]) -> str:
    """Return common root anchor like 'F:/repo_name' for Windows paths."""
    if not paths:
        return ""
    split_paths = [_normalize_windows_path(p).split("/") for p in paths]
    common = split_paths[0]
    for parts in split_paths[1:]:
        i = 0
        while i < len(common) and i < len(parts) and common[i].lower() == parts[i].lower():
            i += 1
        common = common[:i]
        if not common:
            break
    # Prefer at least drive + top-level repo dir when available.
    if len(common) >= 2:
        return "/".join(common[:2])
    return "/".join(common)


def _map_windows_path_to_project(path: str, project_root: str, anchor: str) -> str:
    norm = _normalize_windows_path(path)
    if anchor and norm.lower().startswith((anchor + "/").lower()):
        rel = norm[len(anchor):].lstrip("/")
    elif ":/" in norm:
        rel = norm.split(":/", 1)[1].lstrip("/")
    else:
        rel = norm.lstrip("/")
    rel_parts = [p for p in rel.split("/") if p]
    candidate = os.path.abspath(os.path.join(project_root, *rel_parts))
    if os.path.exists(candidate):
        return candidate

    # Common case for Windows compdb from a different checkout layout:
    # mapped path includes an extra top-level repo folder segment that does
    # not exist under project_root (e.g. nxg_cloud/rtc_engine/...).
    if len(rel_parts) >= 2:
        trimmed = os.path.abspath(os.path.join(project_root, *rel_parts[1:]))
        if os.path.exists(trimmed):
            return trimmed

    return candidate


def _rewrite_compdb_for_host(compdb_path: str, project_root: str) -> str:
    """Rewrite Windows absolute paths in compile_commands.json for POSIX hosts.

    Returns the original path when no rewrite is needed; otherwise writes a
    temporary normalized JSON file and returns its path.
    """
    if os.name == "nt":
        return compdb_path

    with open(compdb_path, "r", encoding="utf-8") as f:
        payload = json.load(f)

    if not isinstance(payload, list):
        return compdb_path

    windows_paths: list[str] = []
    for entry in payload:
        if not isinstance(entry, dict):
            continue
        directory = str(entry.get("directory", ""))
        file_path = str(entry.get("file", ""))
        if _is_windows_abs(directory):
            windows_paths.append(directory)
        if _is_windows_abs(file_path):
            windows_paths.append(file_path)

    anchor = _common_windows_anchor(windows_paths)
    if windows_paths:
        logger.warning(
            "Detected Windows paths in compile_commands.json; rewriting for host. "
            "anchor=%s project_root=%s",
            anchor or "<none>",
            project_root,
        )

    rewritten = []
    dropped_missing_files = 0
    changed = False
    for entry in payload:
        if not isinstance(entry, dict):
            rewritten.append(entry)
            continue
        e = dict(entry)

        directory = str(e.get("directory", ""))
        if _is_windows_abs(directory):
            e["directory"] = _map_windows_path_to_project(directory, project_root, anchor)
            changed = True

        file_path = str(e.get("file", ""))
        if _is_windows_abs(file_path):
            e["file"] = _map_windows_path_to_project(file_path, project_root, anchor)
            changed = True

        command = e.get("command")
        if isinstance(command, str) and anchor:
            updated = command.replace(_normalize_windows_path(anchor), project_root).replace(anchor, project_root)
            if updated != command:
                changed = True
            e["command"] = updated

        args = e.get("arguments")
        if isinstance(args, list) and anchor:
            normalized_args: list[str] = []
            for arg in args:
                text = str(arg)
                text = text.replace(_normalize_windows_path(anchor), project_root).replace(anchor, project_root)
                normalized_args.append(text)
            if normalized_args != args:
                changed = True
            e["arguments"] = normalized_args

        file_value = str(e.get("file", ""))
        if file_value:
            if os.path.isabs(file_value):
                file_abs = os.path.abspath(file_value)
            else:
                directory = str(e.get("directory", project_root)) or project_root
                directory_abs = (
                    os.path.abspath(directory)
                    if os.path.isabs(directory)
                    else os.path.abspath(os.path.join(project_root, directory))
                )
                file_abs = os.path.abspath(os.path.join(directory_abs, file_value))

                if not os.path.exists(file_abs):
                    rebased_rel = file_value.replace("\\", "/")
                    while rebased_rel.startswith("../") or rebased_rel.startswith("./"):
                        if rebased_rel.startswith("../"):
                            rebased_rel = rebased_rel[3:]
                        else:
                            rebased_rel = rebased_rel[2:]
                    rebased = os.path.abspath(os.path.join(project_root, rebased_rel))
                    if os.path.exists(rebased):
                        file_abs = rebased
            if not os.path.exists(file_abs):
                dropped_missing_files += 1
                changed = True
                continue
            if file_abs != file_value:
                changed = True
            e["file"] = file_abs

        rewritten.append(e)

    if not changed:
        return compdb_path

    with tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".json",
        prefix="compile_commands.normalized.",
        delete=False,
        encoding="utf-8",
    ) as tmp:
        json.dump(rewritten, tmp, ensure_ascii=False, indent=2)
        normalized_path = tmp.name

    logger.info("Using normalized compilation database: %s", normalized_path)
    if dropped_missing_files:
        logger.warning(
            "Dropped %s compile_commands entries because source files were not found on disk.",
            dropped_missing_files,
        )
    return normalized_path


def _detect_incompatible_windows_toolchain(compdb_path: str) -> Optional[str]:
    """Return diagnostic message when compdb likely cannot run on this host."""
    if os.name == "nt":
        return None

    try:
        with open(compdb_path, "r", encoding="utf-8") as f:
            payload = json.load(f)
    except Exception:
        return None

    if not isinstance(payload, list) or not payload:
        return None

    sample = payload[: min(len(payload), 200)]
    windows_driver_hits = 0
    for entry in sample:
        if not isinstance(entry, dict):
            continue
        cmd = (entry.get("command") or " ".join(entry.get("arguments", []))).lower()
        if "cl.exe" in cmd or "clang-cl.exe" in cmd:
            windows_driver_hits += 1

    if windows_driver_hits == 0:
        return None
    if windows_driver_hits < max(5, int(len(sample) * 0.6)):
        return None

    return (
        "compile_commands.json appears to use Windows compiler drivers "
        f"(detected {windows_driver_hits}/{len(sample)} entries with cl.exe/clang-cl.exe) "
        "while running on non-Windows host. This usually yields empty SCIP output. "
        "Use a Linux-compatible compile database or run indexing on Windows."
    )


def _infer_project_root_from_compdb(compdb_path: str) -> str:
    """Infer project root from compile_commands.json path."""
    compdb_abs = os.path.abspath(compdb_path)
    compdb_dir = os.path.dirname(compdb_abs)
    parent_name = os.path.basename(compdb_dir)
    if parent_name in _COMMON_BUILD_DIR_NAMES:
        return os.path.dirname(compdb_dir)
    return compdb_dir


def run_scip_clang(
    compdb_path: str,
    index_output_path: str = DEFAULT_INDEX_OUTPUT,
    jobs: Optional[int] = None,
    log_level: str = "info",
    project_root: Optional[str] = None,
) -> str:
    """Invoke scip-clang to generate a SCIP index from compile_commands.json.
    
    Args:
        compdb_path: Path to compile_commands.json file.
        index_output_path: Output path for the .scip index file.
        jobs: Number of parallel indexing processes (default: CPU count).
        log_level: SCIP log level (debug, info, warning, error).
        project_root: Working directory for scip-clang. If None, defaults
            to the directory containing compile_commands.json.
        
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

    if project_root is None:
        project_root = _infer_project_root_from_compdb(compdb_path)
    project_root = os.path.abspath(project_root)

    # Build scip-clang command
    compdb_path_for_host = _rewrite_compdb_for_host(compdb_path, project_root)
    incompat = _detect_incompatible_windows_toolchain(compdb_path_for_host)
    if incompat:
        raise RuntimeError(incompat)
    cmd = [
        SCIP_CLANG_PATH,
        "--compdb-path", compdb_path_for_host,
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
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            cwd=project_root,
            bufsize=1,
        )

        collected: list[str] = []
        if proc.stdout is not None:
            for raw in proc.stdout:
                line = raw.rstrip("\n")
                if not line:
                    continue
                collected.append(line)
                logger.info("[scip-clang] %s", line)

        returncode = proc.wait()
        if returncode != 0:
            logger.error("scip-clang failed with exit code %s", returncode)
            if collected:
                logger.error("scip-clang output (tail): %s", "\n".join(collected[-20:]))
            raise subprocess.CalledProcessError(returncode, cmd, output="\n".join(collected))

    except subprocess.CalledProcessError:
        raise
    except OSError as e:
        logger.error("Failed to start scip-clang: %s", e)
        raise
    
    # Verify output file was created
    if not os.path.isfile(index_output_path):
        raise FileNotFoundError(
            f"scip-clang completed but output file not found: {index_output_path}"
        )

    # Guard against metadata-only indexes that contain no documents/symbols.
    with open(index_output_path, "rb") as f:
        index = scip_pb2.Index()
        index.ParseFromString(f.read())
    if len(index.documents) == 0 and len(index.external_symbols) == 0:
        raise RuntimeError(
            "SCIP index contains zero documents and zero external symbols. "
            "This usually means compile commands were not executable on this host "
            "(for example Windows cl.exe/clang-cl compile database on Linux)."
        )
    
    file_size = os.path.getsize(index_output_path)
    logger.info(
        f"SCIP index generated: {index_output_path} ({file_size / 1024 / 1024:.2f} MB)"
    )
    
    return os.path.abspath(index_output_path)
