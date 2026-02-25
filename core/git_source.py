"""Git source synchronization helpers for workspace pipeline."""

from __future__ import annotations

import logging
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from core.workspace_manifest import RepoSpec

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RepoCheckoutResult:
    """Result of syncing and checking out a repository."""

    repo_name: str
    repo_dir: str
    ref: str
    commit_sha: str
    cloned: bool


def _git_run(
    *,
    args: list[str],
    token: str,
    cwd: str | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run git command with auth header and sanitized error handling."""
    cmd = [
        "git",
        "-c",
        f"http.extraheader=Authorization: Bearer {token}",
        *args,
    ]
    try:
        return subprocess.run(
            cmd,
            cwd=cwd,
            check=True,
            text=True,
            capture_output=True,
        )
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or "").strip()
        stdout = (exc.stdout or "").strip()
        msg = (
            f"git command failed (exit={exc.returncode}) for args={args}. "
            f"stdout={stdout[:400]} stderr={stderr[:400]}"
        )
        raise RuntimeError(msg) from exc


def checkout_ref(repo_dir: str, ref: str, token: str) -> str:
    """Checkout a specific git ref (branch/tag/SHA) and return commit SHA."""
    _git_run(args=["-C", repo_dir, "checkout", "--detach", ref], token=token)
    result = _git_run(args=["-C", repo_dir, "rev-parse", "HEAD"], token=token)
    commit = result.stdout.strip()
    if not commit:
        raise RuntimeError(f"Failed to resolve HEAD commit in {repo_dir}")
    return commit


def sync_repo(
    spec: RepoSpec,
    cache_root: str,
    update_submodules: bool = False,
) -> RepoCheckoutResult:
    """Clone/fetch repository and checkout requested ref."""
    token = os.getenv(spec.token_env, "").strip()
    if not token:
        raise ValueError(
            f"Missing token env '{spec.token_env}' for repo '{spec.repo_name}'"
        )

    root = Path(cache_root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    repo_dir = root / spec.repo_name

    cloned = False
    if not (repo_dir / ".git").is_dir():
        if repo_dir.exists() and any(repo_dir.iterdir()):
            raise RuntimeError(f"Repo target exists and is not empty: {repo_dir}")
        logger.info("Cloning repo '%s' from remote", spec.repo_name)
        _git_run(args=["clone", spec.git_url, str(repo_dir)], token=token)
        cloned = True
    else:
        logger.info("Fetching updates for repo '%s'", spec.repo_name)
        _git_run(args=["-C", str(repo_dir), "fetch", "--all", "--tags", "--prune"], token=token)

    commit_sha = checkout_ref(str(repo_dir), spec.ref, token=token)

    if update_submodules:
        _git_run(
            args=["-C", str(repo_dir), "submodule", "update", "--init", "--recursive"],
            token=token,
        )

    return RepoCheckoutResult(
        repo_name=spec.repo_name,
        repo_dir=str(repo_dir),
        ref=spec.ref,
        commit_sha=commit_sha,
        cloned=cloned,
    )
