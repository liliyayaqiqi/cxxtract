"""Git source synchronization helpers for workspace pipeline."""

from __future__ import annotations

import base64
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
    timeout_s: int = 1800,
    auth_mode: str = "bearer",
    capture_output: bool = False,
) -> subprocess.CompletedProcess[str]:
    """Run git command with auth header and sanitized error handling."""
    if auth_mode == "basic":
        raw = f"oauth2:{token}".encode("utf-8")
        auth_value = "Basic " + base64.b64encode(raw).decode("ascii")
    else:
        auth_value = f"Bearer {token}"

    cmd = ["git", "-c", f"http.extraheader=Authorization: {auth_value}", *args]
    logger.info("Running git command: %s", " ".join(args))
    try:
        env = dict(os.environ)
        # Never block on interactive credential prompts.
        env.setdefault("GIT_TERMINAL_PROMPT", "0")
        env.setdefault("GIT_ASKPASS", "true")
        # Avoid massive LFS transfers during indexing checkout.
        env.setdefault("GIT_LFS_SKIP_SMUDGE", "1")
        return subprocess.run(
            cmd,
            cwd=cwd,
            check=True,
            text=True,
            capture_output=capture_output,
            timeout=timeout_s,
            env=env,
            stdin=subprocess.DEVNULL,
        )
    except subprocess.CalledProcessError as exc:
        msg = (
            f"git command failed (exit={exc.returncode}) for args={args}. "
            "See command output above for details."
        )
        raise RuntimeError(msg) from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            f"git command timed out after {timeout_s}s for args={args}"
        ) from exc


def _try_auth_probe(git_url: str, token: str, auth_mode: str) -> bool:
    """Try auth mode without noisy stderr; return True on success."""
    try:
        _git_run(
            args=["ls-remote", "--heads", git_url],
            token=token,
            timeout_s=30,
            auth_mode=auth_mode,
            capture_output=True,
        )
        return True
    except Exception:
        return False


def _resolve_auth_mode(git_url: str, token: str) -> str:
    """Resolve working auth mode for remote by probing ls-remote."""
    logger.info("Probing git auth mode with Basic PAT for remote: %s", git_url)
    if _try_auth_probe(git_url, token, "basic"):
        logger.info("Auth probe succeeded with Basic PAT mode.")
        return "basic"

    logger.info("Basic PAT auth probe failed, retrying with Bearer mode.")
    if _try_auth_probe(git_url, token, "bearer"):
        logger.info("Auth probe succeeded with Bearer mode.")
        return "bearer"

    raise RuntimeError(
        "Unable to authenticate remote with either Basic PAT or Bearer mode. "
        "Verify token env, token scope, and repo URL."
    )


def checkout_ref(
    repo_dir: str,
    ref: str,
    token: str,
    auth_mode: str = "bearer",
) -> str:
    """Checkout a specific git ref (branch/tag/SHA) and return commit SHA."""
    _git_run(
        args=["-C", repo_dir, "checkout", "--detach", ref],
        token=token,
        auth_mode=auth_mode,
    )
    result = _git_run(
        args=["-C", repo_dir, "rev-parse", "HEAD"],
        token=token,
        auth_mode=auth_mode,
        capture_output=True,
    )
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

    logger.info(
        "Sync repo start: repo=%s git_url=%s ref=%s cache_root=%s",
        spec.repo_name,
        spec.git_url,
        spec.ref,
        cache_root,
    )
    auth_mode = _resolve_auth_mode(spec.git_url, token)
    logger.info("Resolved git auth mode: repo=%s mode=%s", spec.repo_name, auth_mode)

    cloned = False
    if not (repo_dir / ".git").is_dir():
        if repo_dir.exists() and any(repo_dir.iterdir()):
            raise RuntimeError(f"Repo target exists and is not empty: {repo_dir}")
        logger.info("Cloning repo '%s' from remote", spec.repo_name)
        _git_run(
            args=[
                "clone",
                "--progress",
                "--no-checkout",
                "--filter=blob:none",
                spec.git_url,
                str(repo_dir),
            ],
            token=token,
            auth_mode=auth_mode,
        )
        cloned = True
    else:
        logger.info("Fetching updates for repo '%s'", spec.repo_name)
        _git_run(
            args=[
                "-C",
                str(repo_dir),
                "fetch",
                "--all",
                "--tags",
                "--prune",
                "--progress",
            ],
            token=token,
            auth_mode=auth_mode,
        )

    logger.info("Checking out ref: repo=%s ref=%s", spec.repo_name, spec.ref)
    _git_run(
        args=["-C", str(repo_dir), "checkout", "--detach", spec.ref],
        token=token,
        auth_mode=auth_mode,
    )
    commit_sha = _git_run(
        args=["-C", str(repo_dir), "rev-parse", "HEAD"],
        token=token,
        auth_mode=auth_mode,
        capture_output=True,
    ).stdout.strip()
    if not commit_sha:
        raise RuntimeError(f"Failed to resolve HEAD commit in {repo_dir}")

    if update_submodules:
        logger.info("Updating submodules: repo=%s", spec.repo_name)
        _git_run(
            args=["-C", str(repo_dir), "submodule", "update", "--init", "--recursive"],
            token=token,
            auth_mode=auth_mode,
        )

    logger.info(
        "Sync repo complete: repo=%s commit=%s cloned=%s",
        spec.repo_name,
        commit_sha,
        cloned,
    )
    return RepoCheckoutResult(
        repo_name=spec.repo_name,
        repo_dir=str(repo_dir),
        ref=spec.ref,
        commit_sha=commit_sha,
        cloned=cloned,
    )
