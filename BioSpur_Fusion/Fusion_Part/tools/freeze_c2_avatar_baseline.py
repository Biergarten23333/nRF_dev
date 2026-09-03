#!/usr/bin/env python3
"""Seal the accepted Capture2 avatar baseline by exact content identity.

The seal does not copy raw evidence and never edits a historical run.  It binds
the canonical Capture2 payload, effective configuration, implementation,
calibration artifacts, Hxx diagnostic replay and the unified viewer.  Future
display layers must consume the frozen joint trajectory without modifying it.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from typing import Iterable


ROOT = Path(__file__).resolve().parents[1]
C2_ID = (
    "phase2_targeted_calibration_20260817t130918z_capture_2_"
    "with_joint_label_c8645eb2"
)
C2_ROOT = ROOT / "datasets/phase2_calibration" / C2_ID


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def record(path: Path, role: str) -> dict[str, object]:
    resolved = path.resolve()
    if ROOT not in resolved.parents:
        raise RuntimeError(f"freeze input escaped canonical workspace: {path}")
    if not resolved.is_file():
        raise FileNotFoundError(resolved)
    return {
        "path": str(resolved.relative_to(ROOT)),
        "role": role,
        "bytes": resolved.stat().st_size,
        "sha256": sha256(resolved),
    }


def unique_files(paths: Iterable[Path]) -> list[Path]:
    return sorted({path.resolve() for path in paths}, key=lambda path: str(path))


def write_new(path: Path, value: object) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    descriptor = os.open(path, flags, 0o444)
    try:
        payload = (
            value
            if isinstance(value, str)
            else json.dumps(value, indent=2, sort_keys=True) + "\n"
        )
        os.write(descriptor, payload.encode("utf-8"))
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--calibration-run", type=Path, required=True)
    parser.add_argument("--hxx-run", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    calibration_run = args.calibration_run.resolve()
    hxx_run = args.hxx_run.resolve()
    output = args.output.resolve()
    if any(ROOT not in path.parents for path in (calibration_run, hxx_run, output)):
        raise SystemExit("all freeze paths must remain under canonical Fusion_Part")
    output.mkdir(parents=True, exist_ok=False)

    artifact_paths = unique_files(
        list(calibration_run.rglob("*")) + list(hxx_run.rglob("*"))
    )
    artifact_paths = [path for path in artifact_paths if path.is_file()]
    source_paths = unique_files(
        list((ROOT / "src/biospur_fusion/c2_coupled_progressive").glob("*.py"))
        + [
            ROOT / "src/biospur_fusion/time/common_clock.py",
            ROOT / "tools/run_c2_pose_reset_avatar.py",
            ROOT / "tools/run_c2_hxx_frozen_replay.py",
            ROOT / "tools/build_c2_avatar_interactive.py",
            ROOT / "tests/v0/test_c2_pose_reset_avatar.py",
        ]
    )
    config_paths = unique_files(
        list((ROOT / "config/c2_coupled_progressive_v1").glob("*"))
        + [
            ROOT / "logs/c2_coupled_progressive_20260831_082131/"
            "RUN_START_AMENDMENT_001.json",
        ]
    )
    metadata_paths = unique_files(
        list((C2_ROOT / "subject").glob("*"))
        + list(C2_ROOT.glob("actions/*/ACTION_DEFINITION.json"))
        + list(C2_ROOT.glob("actions/*/rep_01/events/ACTION_EVENTS.jsonl"))
        + list(C2_ROOT.glob("actions/*/rep_01/manifest/CONTINUOUS_RANGE.json"))
        + list(C2_ROOT.glob("holdout/*/ACTION_DEFINITION.json"))
        + list(C2_ROOT.glob("holdout/*/rep_01/events/ACTION_EVENTS.jsonl"))
        + list(C2_ROOT.glob("holdout/*/rep_01/manifest/CONTINUOUS_RANGE.json"))
        + [
            C2_ROOT / "system/readiness/SYSTEM_READINESS_REPORT.json",
            C2_ROOT / "system/listeners/passive_5/summary.json",
        ]
    )
    payload_paths = [
        C2_ROOT / "system/fusion_continuous/fusion_host_raw.cobs.bin",
        C2_ROOT / "system/fusion_continuous/fusion_cdc.log",
    ]

    manifest = {
        "schema": "biospur-capture2-avatar-formal-freeze-v1",
        "status": "FORMAL_FREEZE_SEALED",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "capture_scope": "CAPTURE2_ONLY",
        "capture_id": C2_ID,
        "calibration_actions": 19,
        "holdout_actions": ["H01_boxing", "H02_golf"],
        "holdout_used_for_fit": False,
        "output_handedness": "ONE_FROZEN_CAPTURE_WIDE_3D_GLOBAL_REFLECTION",
        "accepted_viewer_episode_count": 21,
        "scientific_status": "RUNNABLE_DIAGNOSTIC_BASELINE_NOT_SCIENTIFIC_PASS",
        "frozen_ownership": {
            "orientation_and_calibration": "IMMUTABLE",
            "fixed_geometry_joint_trajectory": "IMMUTABLE",
            "output_coordinate_convention": "IMMUTABLE",
            "hxx_replay": "IMMUTABLE_DIAGNOSTIC_HOLDOUT",
            "future_body_mesh": (
                "DISPLAY_CONSUMER_ONLY_MAY_NOT_REFIT_REBASE_RETARGET_REPAIR_"
                "OR_CHANGE_FROZEN_JOINTS"
            ),
        },
        "canonical_payload": [record(path, "canonical_capture2_payload") for path in payload_paths],
        "capture_metadata": [record(path, "capture2_metadata") for path in metadata_paths],
        "effective_configuration": [record(path, "effective_configuration") for path in config_paths],
        "implementation": [record(path, "implementation_or_test") for path in source_paths],
        "accepted_artifacts": [record(path, "accepted_capture2_artifact") for path in artifact_paths],
    }
    manifest_path = output / "FORMAL_FREEZE_MANIFEST.json"
    write_new(manifest_path, manifest)
    manifest_sha = sha256(manifest_path)

    # Re-read every bound file before sealing so a concurrent mutation cannot
    # silently enter between inventory and seal creation.
    mismatches = []
    for group in (
        "canonical_payload",
        "capture_metadata",
        "effective_configuration",
        "implementation",
        "accepted_artifacts",
    ):
        for row in manifest[group]:
            path = ROOT / str(row["path"])
            observed = sha256(path)
            if observed != row["sha256"]:
                mismatches.append({
                    "path": row["path"],
                    "expected": row["sha256"],
                    "observed": observed,
                })
    if mismatches:
        raise RuntimeError(f"freeze inputs mutated during sealing: {mismatches}")
    seal = {
        "schema": "biospur-capture2-avatar-formal-freeze-seal-v1",
        "status": "FORMAL_FREEZE_SEALED",
        "manifest": str(manifest_path.relative_to(ROOT)),
        "manifest_sha256": manifest_sha,
        "verified_bound_file_count": sum(
            len(manifest[group])
            for group in (
                "canonical_payload",
                "capture_metadata",
                "effective_configuration",
                "implementation",
                "accepted_artifacts",
            )
        ),
        "verification_mismatches": [],
        "next_layer_contract": (
            "BODY_WHITE_MESH_IS_A_NEW_RENDER_ONLY_LAYER_OVER_FROZEN_JOINTS"
        ),
    }
    seal_path = output / "FORMAL_FREEZE_SEAL.json"
    write_new(seal_path, seal)
    write_new(output / "FORMAL_FREEZE_SEALED", "FORMAL_FREEZE_SEALED\n")
    print(json.dumps({
        "status": seal["status"],
        "output": str(output),
        "manifest_sha256": manifest_sha,
        "verified_bound_file_count": seal["verified_bound_file_count"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
