#!/usr/bin/env python3
"""Replay sealed C2 H01/H02 on the native 5 ms IMU time grid.

This is deliberately a separate adapter around the hash-owned frozen replay.
It changes only the synchronization grid and viewer cadence; calibration,
orientation estimation, FK geometry, coordinate convention, and QA remain
owned by the frozen implementation.
"""

from __future__ import annotations

import json
from pathlib import Path

from tools import run_c2_hxx_frozen_replay as frozen


GRID_PERIOD_NS = 5_000_000
POSE_RATE_HZ = 1e9 / GRID_PERIOD_NS


def _write_json(path: Path, value: dict) -> None:
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def main() -> int:
    original_sync = frozen.synchronize_holdout_quaternions
    original_payload = frozen._interactive_payload

    def native_sync(*args, **kwargs):
        if "grid_period_ns" in kwargs:
            raise RuntimeError("native replay owns the synchronization period")
        return original_sync(*args, grid_period_ns=GRID_PERIOD_NS, **kwargs)

    def native_payload(trajectory, records):
        payload = original_payload(trajectory, records)
        payload["targetFps"] = POSE_RATE_HZ
        payload["nativePoseOutput"] = {
            "sampleRateHz": POSE_RATE_HZ,
            "gridPeriodNs": GRID_PERIOD_NS,
            "interpolatedPose": False,
        }
        return payload

    frozen.synchronize_holdout_quaternions = native_sync
    frozen._interactive_payload = native_payload
    result = frozen.main()

    # frozen.main has already parsed argv and produced the output. Recover the
    # output path without duplicating its argument contract.
    import argparse
    import sys

    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--calibration-run")
    parser.add_argument("--output", type=Path, required=True)
    args, _ = parser.parse_known_args(sys.argv[1:])
    output = args.output.resolve()
    report_path = output / "HXX_FROZEN_C2_REPLAY_REPORT.json"
    manifest_path = output / "INTERACTIVE_3D" / "MANIFEST.json"
    html_path = output / "INTERACTIVE_3D" / "c2_hxx_avatar_3d.html"

    report = frozen._json(report_path)
    report["native_pose_output"] = {
        "grid_period_ns": GRID_PERIOD_NS,
        "sample_rate_hz": POSE_RATE_HZ,
        "source": "RAW_200HZ_IMU_REPLAY_THROUGH_FROZEN_CALIBRATION_AND_FK",
        "pose_interpolation": False,
    }
    report["adapter"] = {
        "path": str(Path(__file__).resolve().relative_to(frozen.ROOT)),
        "sha256": frozen._sha256(Path(__file__).resolve()),
        "frozen_owner_path": str(
            Path(frozen.__file__).resolve().relative_to(frozen.ROOT)
        ),
        "frozen_owner_sha256": frozen._sha256(Path(frozen.__file__).resolve()),
    }
    _write_json(report_path, report)

    manifest = frozen._json(manifest_path)
    manifest["report_sha256"] = frozen._sha256(report_path)
    manifest["html_sha256"] = frozen._sha256(html_path)
    manifest["pose_sample_rate_hz"] = POSE_RATE_HZ
    manifest["pose_interpolation"] = False
    manifest["adapter"] = report["adapter"]
    _write_json(manifest_path, manifest)
    return result


if __name__ == "__main__":
    raise SystemExit(main())
