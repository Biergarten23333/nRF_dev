#!/usr/bin/env python3
"""Run V4 twice without touching V3 or held-out payloads before freeze."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve(); ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT/"Fusion_Part/src"))
from biospur_fusion.pipeline.offline_v4 import dump, run, sha  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--capture", type=Path, default=ROOT/"Fusion_Part/logs/v47_ten_node_body_calibration_20260814_093601")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(); out = args.out or args.capture/"analysis_body_fusion_v4"
    first = run(args.capture, out, ROOT); replay = out/"deterministic_replay_b"
    second = run(args.capture, replay, ROOT)
    names = ("V3_IMMUTABILITY_AUDIT.json", "ANTHROPOMETRY_INPUT_AUDIT.json",
             "PREDECLARED_GATE_MANIFEST.json", "CALIBRATION_FREEZE_MANIFEST.json",
             "HELDOUT_VALIDATION.json", "ANIMATION_GATE.json", "PROVENANCE.json", "REPORT.md",
             "calibration/ANTHROPOMETRY_VALIDATION.json", "calibration/PREDECLARED_PHYSICAL_GATES.json",
             "calibration/CENTERLINE_CALIBRATION_RESULT.json")
    hashes = {name: {"run_a": sha(out/name), "run_b": sha(replay/name)} for name in names}
    deterministic = first == second and all(row["run_a"] == row["run_b"] for row in hashes.values())
    dump(out/"DETERMINISTIC_REPLAY.json", {"pass": deterministic, "run_a": first,
                                             "run_b": second, "artifact_hashes": hashes})
    print(json.dumps({"result": first, "deterministic": deterministic, "output": str(out)}, sort_keys=True))
    return 0 if deterministic else 2


if __name__ == "__main__": raise SystemExit(main())
