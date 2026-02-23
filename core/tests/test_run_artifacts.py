"""Tests for run artifact writer."""

import json
import tempfile
import unittest
from pathlib import Path

from core.run_artifacts import write_run_report


class TestRunArtifacts(unittest.TestCase):
    def test_write_run_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = write_run_report(
                report={"status": "success", "value": 1},
                run_id="run-123",
                output_dir=tmpdir,
            )
            self.assertTrue(Path(path).is_file())
            payload = json.loads(Path(path).read_text(encoding="utf-8"))
            self.assertEqual(payload["run_id"], "run-123")
            self.assertEqual(payload["status"], "success")
            self.assertEqual(payload["value"], 1)
            self.assertIn("timestamp_utc", payload)


if __name__ == "__main__":
    unittest.main()
