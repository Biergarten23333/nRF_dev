#!/usr/bin/env python3
"""Verify a completed BioSpur Fusion V0 golden output without replaying it."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify(output: Path) -> dict[str, object]:
    output = output.resolve()
    required = {
        "FINAL_RESULT.json", "V0_SESSION_PROFILE.json", "V0_SESSION_PROFILE.sha256",
        "V0_STATE.npz", "V0_VIEWER.html", "UWB_ISOLATION_TEST.json",
        "DRIFT_AND_STABILITY.json", "SHA256SUMS", "REPORT.md",
    }
    missing = sorted(name for name in required if not (output / name).is_file())
    if missing:
        raise RuntimeError(f"missing V0 artifacts: {missing}")
    manifest = {}
    for line in (output / "SHA256SUMS").read_text(encoding="utf-8").splitlines():
        digest, name = line.split("  ", 1)
        manifest[name] = digest
    checksum_failures = {}
    for name, digest in manifest.items():
        path = output / name
        actual = _sha256(path) if path.is_file() else "MISSING"
        if actual != digest:
            checksum_failures[name] = {"expected": digest, "actual": actual}
    result = json.loads((output / "FINAL_RESULT.json").read_text(encoding="utf-8"))
    isolation = json.loads((output / "UWB_ISOLATION_TEST.json").read_text(encoding="utf-8"))
    stability = json.loads((output / "DRIFT_AND_STABILITY.json").read_text(encoding="utf-8"))
    profile_line = (output / "V0_SESSION_PROFILE.sha256").read_text(encoding="utf-8").split()[0]
    profile_ok = profile_line == _sha256(output / "V0_SESSION_PROFILE.json")
    with np.load(output / "V0_STATE.npz", allow_pickle=False) as state:
        state_ok = bool(
            len(state["global_time_ns"]) == result["execution"]["state_frames"]
            and state["segment_rotation"].shape[1:] == (10, 3, 3)
            and np.isfinite(state["segment_rotation"]).all()
            and np.isfinite(state["segment_sigma_rad"]).all()
            and len(np.unique(state["node_names"])) == 10
        )
    checks = {
        "manifest": not checksum_failures,
        "final_pass": result["pass"] is True,
        "classification_positive": result["classification"]["OVERALL_SYSTEM_DIRECTION"] == "POSITIVE",
        "profile_checksum": profile_ok,
        "state_shape_and_finiteness": state_ok,
        "actual_two_path_uwb_isolation": bool(
            isolation["pass"] and isolation["actual_full_replay_executions"] == 2
            and isolation["hostile_variant_runtime_adapter"]["spatial_payload_reached_runtime_adapter"]
            and isolation["state_digest_removed"] == isolation["state_digest_perturbed"]
        ),
        "drift_and_stability": stability["pass"] is True,
        "held_out_not_accessed": result["classification"]["HELD_OUT_GOLF_BOXING_ACCESSED"] == "NO",
    }
    return {
        "schema": "biospur-fusion-v0-golden-verification-v1",
        "output": str(output),
        "checks": checks,
        "checksum_failures": checksum_failures,
        "pass": all(checks.values()),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    report = verify(args.output)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["pass"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
