"""Static production leakage guards for Stage 3-R1."""
from __future__ import annotations

from pathlib import Path


FORBIDDEN_LITERALS = (
    "CAPTURE1", "CAPTURE2", "CAPTURE3", "10.10", "1130.45", "1198.35",
    "checkpoint", "final_still", "camera", "action_label",
)


def scan_production(paths: list[Path]) -> dict:
    result = {}
    for path in paths:
        text = path.read_text(encoding="utf-8")
        result[path.name] = [token for token in FORBIDDEN_LITERALS if token in text]
    return result
