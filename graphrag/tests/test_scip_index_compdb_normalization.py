"""Tests for compile_commands.json host normalization in scip_index."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from graphrag.scip_index import (
    _detect_incompatible_windows_toolchain,
    _rewrite_compdb_for_host,
)


class TestCompdbNormalization(unittest.TestCase):
    def test_rewrite_windows_compdb_paths_for_posix(self) -> None:
        payload = [
            {
                "directory": "F:/webrtc_m89_mi/out/debug",
                "file": "F:/webrtc_m89_mi/api/audio/audio_frame.cc",
                "command": "clang++ -IF:/webrtc_m89_mi src.cc",
            }
        ]
        with tempfile.TemporaryDirectory() as tmp:
            source_root = Path(tmp) / "repo_webrtc"
            source_root.mkdir(parents=True, exist_ok=True)
            expected_file = source_root / "api" / "audio" / "audio_frame.cc"
            expected_file.parent.mkdir(parents=True, exist_ok=True)
            expected_file.write_text("// test", encoding="utf-8")
            compdb = Path(tmp) / "compile_commands.json"
            compdb.write_text(json.dumps(payload), encoding="utf-8")

            rewritten_path = _rewrite_compdb_for_host(str(compdb), str(source_root))
            rewritten = json.loads(Path(rewritten_path).read_text(encoding="utf-8"))

            self.assertNotEqual(rewritten_path, str(compdb))
            self.assertEqual(
                rewritten[0]["directory"],
                str(source_root / "out" / "debug"),
            )
            self.assertEqual(
                rewritten[0]["file"],
                str(expected_file),
            )
            self.assertIn(str(source_root), rewritten[0]["command"])

    def test_rewrite_windows_paths_strips_nonexistent_top_level_segment(self) -> None:
        payload = [
            {
                "directory": "F:/another_repo/out/debug",
                "file": "F:/nxg_cloud/rtc_engine/rtc_apps/common/source/common/utils/thread_util.cpp",
            }
        ]
        with tempfile.TemporaryDirectory() as tmp:
            source_root = Path(tmp) / "repo_project_cloud"
            target = source_root / "rtc_engine" / "rtc_apps" / "common" / "source" / "common" / "utils" / "thread_util.cpp"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("// test", encoding="utf-8")

            compdb = Path(tmp) / "compile_commands.json"
            compdb.write_text(json.dumps(payload), encoding="utf-8")

            rewritten_path = _rewrite_compdb_for_host(str(compdb), str(source_root))
            rewritten = json.loads(Path(rewritten_path).read_text(encoding="utf-8"))
            self.assertEqual(rewritten[0]["file"], str(target))

    def test_rewrite_drops_missing_file_entries(self) -> None:
        payload = [
            {
                "directory": "F:/repo/out/debug",
                "file": "F:/repo/src/exists.cc",
            },
            {
                "directory": "F:/repo/out/debug",
                "file": "F:/repo/src/missing.cc",
            },
        ]
        with tempfile.TemporaryDirectory() as tmp:
            source_root = Path(tmp) / "repo"
            existing = source_root / "src" / "exists.cc"
            existing.parent.mkdir(parents=True, exist_ok=True)
            existing.write_text("// exists", encoding="utf-8")

            compdb = Path(tmp) / "compile_commands.json"
            compdb.write_text(json.dumps(payload), encoding="utf-8")

            rewritten_path = _rewrite_compdb_for_host(str(compdb), str(source_root))
            rewritten = json.loads(Path(rewritten_path).read_text(encoding="utf-8"))
            self.assertEqual(len(rewritten), 1)
            self.assertEqual(rewritten[0]["file"], str(existing))

    def test_rewrite_rebases_relative_files_to_project_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source_root = Path(tmp) / "repo_webrtc"
            (source_root / "out" / "debug").mkdir(parents=True, exist_ok=True)
            target = source_root / "video" / "quality_threshold.cc"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("// exists", encoding="utf-8")

            payload = [
                {
                    "directory": str(source_root / "out" / "debug"),
                    "file": "../../video/quality_threshold.cc",
                }
            ]
            compdb = Path(tmp) / "compile_commands.json"
            compdb.write_text(json.dumps(payload), encoding="utf-8")

            rewritten_path = _rewrite_compdb_for_host(str(compdb), str(source_root))
            rewritten = json.loads(Path(rewritten_path).read_text(encoding="utf-8"))
            self.assertEqual(len(rewritten), 1)
            self.assertEqual(rewritten[0]["file"], str(target))

    def test_detect_incompatible_windows_toolchain(self) -> None:
        payload = [
            {"command": "C:\\\\VS\\\\cl.exe /c a.cc", "file": "a.cc", "directory": "C:/repo"}
            for _ in range(20)
        ]
        with tempfile.TemporaryDirectory() as tmp:
            compdb = Path(tmp) / "compile_commands.json"
            compdb.write_text(json.dumps(payload), encoding="utf-8")
            msg = _detect_incompatible_windows_toolchain(str(compdb))
            if os.name == "nt":
                self.assertIsNone(msg)
            else:
                self.assertIsNotNone(msg)


if __name__ == "__main__":
    unittest.main()
