"""Tests for git source sync helpers."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.git_source import checkout_ref, sync_repo
from core.workspace_manifest import RepoSpec, CompdbSpec


class TestGitSource(unittest.TestCase):
    def test_checkout_ref_returns_commit(self) -> None:
        calls: list[list[str]] = []

        def _fake_run(cmd, cwd=None, check=None, text=None, capture_output=None, timeout=None, env=None, stdin=None):  # noqa: ANN001
            del cwd, check, text, capture_output, timeout, env, stdin
            calls.append(cmd)

            class _Result:
                stdout = "abcdef123456\n"
                stderr = ""

            return _Result()

        with patch("subprocess.run", side_effect=_fake_run):
            commit = checkout_ref("/tmp/repo", "main", token="tok")

        self.assertEqual(commit, "abcdef123456")
        self.assertEqual(len(calls), 2)
        self.assertIn("checkout", calls[0])
        self.assertIn("rev-parse", calls[1])

    def test_sync_repo_missing_token_env_raises(self) -> None:
        spec = RepoSpec(
            repo_name="repo-a",
            git_url="https://gitlab.example.com/group/repo-a.git",
            ref="main",
            token_env="MISSING_TOKEN",
            compdb_paths=[CompdbSpec(path="build/compile_commands.json")],
        )
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(ValueError):
                sync_repo(spec=spec, cache_root="/tmp/cache", update_submodules=False)

    def test_sync_repo_clone_then_checkout(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            spec = RepoSpec(
                repo_name="repo-a",
                git_url="https://gitlab.example.com/group/repo-a.git",
                ref="main",
                token_env="TOKEN_A",
                compdb_paths=[CompdbSpec(path="build/compile_commands.json")],
            )
            repo_dir = Path(tmp) / "repo-a"
            repo_dir.mkdir(parents=True, exist_ok=True)
            (repo_dir / ".git").mkdir(parents=True, exist_ok=True)

            def _fake_run(cmd, cwd=None, check=None, text=None, capture_output=None, timeout=None, env=None, stdin=None):  # noqa: ANN001
                del cwd, check, text, capture_output, timeout, env, stdin

                class _Result:
                    stdout = "deadbeef\n" if "rev-parse" in cmd else ""
                    stderr = ""

                return _Result()

            with patch.dict(os.environ, {"TOKEN_A": "secret"}, clear=True):
                with patch("subprocess.run", side_effect=_fake_run):
                    result = sync_repo(spec=spec, cache_root=tmp, update_submodules=True)

            self.assertEqual(result.repo_name, "repo-a")
            self.assertEqual(result.ref, "main")
            self.assertEqual(result.commit_sha, "deadbeef")


if __name__ == "__main__":
    unittest.main()
