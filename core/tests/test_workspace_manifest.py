"""Tests for workspace manifest parsing and validation."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from core.workspace_manifest import load_workspace_manifest, resolve_compdb_path, CompdbSpec


class TestWorkspaceManifest(unittest.TestCase):
    def _write_manifest(self, text: str, suffix: str = ".yml") -> str:
        handle = tempfile.NamedTemporaryFile(mode="w", suffix=suffix, delete=False)
        handle.write(text)
        handle.flush()
        handle.close()
        return handle.name

    def test_load_valid_manifest(self) -> None:
        path = self._write_manifest(
            """
workspace_name: demo
repos:
  - repo_name: repo-a
    git_url: https://gitlab.example.com/group/repo-a.git
    ref: main
    token_env: GITLAB_TOKEN
    compdb_paths:
      - build/compile_commands.json
"""
        )
        try:
            manifest = load_workspace_manifest(path)
            self.assertEqual(manifest.workspace_name, "demo")
            self.assertEqual(len(manifest.repos), 1)
            self.assertEqual(manifest.repos[0].repo_name, "repo-a")
            self.assertEqual(manifest.repos[0].compdb_paths[0].path, "build/compile_commands.json")
        finally:
            Path(path).unlink(missing_ok=True)

    def test_missing_token_env_raises(self) -> None:
        path = self._write_manifest(
            """
workspace_name: demo
repos:
  - repo_name: repo-a
    git_url: https://example/repo-a.git
    ref: main
    compdb_paths: [build/compile_commands.json]
"""
        )
        try:
            with self.assertRaises(ValueError):
                load_workspace_manifest(path)
        finally:
            Path(path).unlink(missing_ok=True)

    def test_duplicate_repo_names_raise(self) -> None:
        path = self._write_manifest(
            """
workspace_name: demo
repos:
  - repo_name: repo-a
    git_url: https://example/repo-a.git
    ref: main
    token_env: TOKEN_A
    compdb_paths: [build/compile_commands.json]
  - repo_name: repo-a
    git_url: https://example/repo-b.git
    ref: main
    token_env: TOKEN_B
    compdb_paths: [build/compile_commands.json]
"""
        )
        try:
            with self.assertRaises(ValueError):
                load_workspace_manifest(path)
        finally:
            Path(path).unlink(missing_ok=True)

    def test_empty_compdb_list_raises(self) -> None:
        path = self._write_manifest(
            """
workspace_name: demo
repos:
  - repo_name: repo-a
    git_url: https://example/repo-a.git
    ref: main
    token_env: TOKEN_A
    compdb_paths: []
"""
        )
        try:
            with self.assertRaises(ValueError):
                load_workspace_manifest(path)
        finally:
            Path(path).unlink(missing_ok=True)

    def test_resolve_relative_compdb(self) -> None:
        repo_root = Path("/tmp/work/repo-a")
        resolved = resolve_compdb_path(repo_root, CompdbSpec(path="build/compile_commands.json"))
        self.assertEqual(str(resolved), "/tmp/work/repo-a/build/compile_commands.json")


if __name__ == "__main__":
    unittest.main()
