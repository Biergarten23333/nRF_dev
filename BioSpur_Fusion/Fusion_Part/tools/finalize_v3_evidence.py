#!/usr/bin/env python3
"""Deterministically finalize report-only V3 verdict wording after replay."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")


def finalize(directory: Path) -> str:
    path = directory / "CALIBRATION_PHYSICAL_INTERPRETATION.json"
    value = json.loads(path.read_text())
    effects = [row["physical_effect"] for row in value["null_direction_physical_effects"]]
    verdict = ("FAIL_AXIAL_TWIST_UNAVAILABLE" if effects
               and all(effect == "segment axial twist only" for effect in effects)
               else "FAIL_SEGMENT_POSE_NULLSPACE")
    value["FULL_SEGMENT_POSE_CALIBRATION"]["verdict"] = verdict
    write_json(path, value)
    report = (directory / "REPORT.md").read_text()
    lines = report.splitlines()
    lines = [f"FULL_SEGMENT_POSE_CALIBRATION: `{verdict}`."
             if line.startswith("FULL_SEGMENT_POSE_CALIBRATION:") else line for line in lines]
    (directory / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return verdict


def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("output", type=Path)
    args = parser.parse_args(); replay = args.output / "deterministic_replay_b"
    a = finalize(args.output); b = finalize(replay)
    if a != b:
        raise RuntimeError("replay verdict divergence")
    audit = {
        "operation": "report-only verdict wording derived from already-computed finite null effects",
        "optimizer_rerun": False, "threshold_changed": False, "prior_changed": False,
        "bounds_changed": False, "heldout_opened": False, "animation_generated": False,
        "FULL_SEGMENT_POSE_CALIBRATION": a,
    }
    write_json(args.output / "POST_REPLAY_INTERPRETATION.json", audit)
    deterministic_path = args.output / "DETERMINISTIC_REPLAY.json"
    deterministic = json.loads(deterministic_path.read_text())
    for name in ("CALIBRATION_PHYSICAL_INTERPRETATION.json", "REPORT.md"):
        deterministic["content_hashes"][name] = {
            "run_a": sha(args.output / name), "run_b": sha(replay / name),
        }
    deterministic["pass"] = bool(deterministic["pass"] and all(
        row["run_a"] == row["run_b"] for row in deterministic["content_hashes"].values()))
    write_json(deterministic_path, deterministic)
    print(json.dumps({"verdict": a, "deterministic": deterministic["pass"]}, sort_keys=True))
    return 0 if deterministic["pass"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
