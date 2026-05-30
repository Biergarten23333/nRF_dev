#!/usr/bin/env python3
"""Run the current CPU-only AutoPos layout evaluation pipeline."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


STEPS = [
    ("inventory raw captures", ["scripts/inventory_raw_captures.py"]),
    ("build layout database", ["scripts/build_layout_db.py"]),
    ("extract layout features", ["scripts/extract_layout_features.py"]),
    ("bind DOP summaries", ["scripts/bind_dop_summaries.py"]),
    ("validate OptiTrack correlations", ["scripts/validate_optitrack_correlations.py"]),
    ("stratified OptiTrack analysis", ["scripts/stratified_optitrack_analysis.py"]),
    ("rank layouts", ["scripts/rank_layouts.py"]),
    ("score layouts v2", ["scripts/score_layouts_v2.py"]),
    ("score sensitivity analysis", ["scripts/score_sensitivity_analysis.py"]),
    ("build ML candidate table", ["scripts/build_ml_candidate_table.py"]),
    ("generate Bewertung report", ["scripts/generate_bewertung_report.py"]),
]


def main() -> int:
    env = os.environ.copy()
    # This pipeline should remain CPU-only. Future GPU tasks require explicit
    # user confirmation and must not use GPU1.
    env["CUDA_VISIBLE_DEVICES"] = ""

    for label, cmd in STEPS:
        print(f"\n== {label} ==", flush=True)
        result = subprocess.run([sys.executable, *cmd], cwd=ROOT, env=env, check=False)
        if result.returncode != 0:
            print(f"step failed: {label}", file=sys.stderr)
            return result.returncode
    print("\nCPU pipeline complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
