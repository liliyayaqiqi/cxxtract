"""Run artifact helpers for operational reporting."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any


def write_run_report(
    report: dict[str, Any],
    run_id: str,
    output_dir: str = "output/run_reports",
) -> str:
    """Write a JSON run report and return its path."""
    os.makedirs(output_dir, exist_ok=True)
    payload = dict(report)
    payload.setdefault("run_id", run_id)
    payload.setdefault("timestamp_utc", datetime.now(timezone.utc).isoformat())
    path = os.path.join(output_dir, f"{run_id}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, sort_keys=True)
    return path
