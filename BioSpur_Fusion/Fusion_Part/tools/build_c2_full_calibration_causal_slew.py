#!/usr/bin/env python3
"""Apply one causal whole-session translation slew to the completed C2 A/B run."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = ROOT / "logs/c2_continuous_root_ab_full_20260908T000000Z"
MAXIMUM_ROOT_SPEED_MPS = 6.0


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1 << 20):
            digest.update(block)
    return digest.hexdigest()


def _metrics(position: np.ndarray, time_s: np.ndarray) -> dict[str, float | int]:
    displacement = np.linalg.norm(position - position[0], axis=1)
    dt = np.diff(time_s)
    step = np.linalg.norm(np.diff(position, axis=0), axis=1)
    speed = step / dt
    native = (dt >= 0.0045) & (dt <= 0.0055)
    return {
        "endpoint_displacement_m": float(displacement[-1]),
        "maximum_displacement_m": float(displacement.max()),
        "rms_displacement_m": float(np.sqrt(np.mean(displacement**2))),
        "maximum_speed_mps": float(speed.max()),
        "maximum_native200_step_m": float(step[native].max()),
        "p99_native200_step_m": float(np.quantile(step[native], 0.99)),
        "native200_step_count": int(native.sum()),
    }


def run(source: Path, output: Path) -> dict[str, object]:
    source = source.resolve()
    output = output.resolve()
    if ROOT not in output.parents or output.exists():
        raise ValueError("output must be a new directory under Fusion_Part")
    source_npz = source / "ROOT_AB_FULL.npz"
    source_result = source / "RESULT.json"
    document = json.loads(source_result.read_text())
    inventory = document["inventory"]
    if not (
        inventory["protocol_slots"] == 20
        and inventory["acquired_actions"] == 19
        and inventory["marker_only_actions"] == 1
        and document["branch_a"]["post_bootstrap_uwb_commits"] == 0
    ):
        raise RuntimeError("source is not the indivisible 00--19 A/B run")

    with np.load(source_npz, allow_pickle=False) as archive:
        time_s = np.asarray(archive["time_s"], dtype=float)
        root_a = np.asarray(archive["root_a_world_m"], dtype=float)
        root_b_target = np.asarray(archive["root_b_world_m"], dtype=float)
        velocity_a = np.asarray(archive["velocity_a_world_mps"], dtype=float)
        velocity_b = np.asarray(archive["velocity_b_world_mps"], dtype=float)
        anchors = np.asarray(archive["anchors_world_m"], dtype=float)

    if not (
        len(time_s) == len(root_a) == len(root_b_target)
        and np.all(np.diff(time_s) > 0.0)
        and np.isfinite(root_b_target).all()
    ):
        raise RuntimeError("source trajectory is invalid")

    # The accepted UWB posterior is the causal target.  Only its publication is
    # rate-limited: a large persistent correction is released over later 200 Hz
    # frames instead of appearing as a one-frame body teleport.
    root_b = np.empty_like(root_b_target)
    root_b[0] = root_b_target[0]
    limited = 0
    maximum_withheld = 0.0
    for index in range(1, len(time_s)):
        dt = float(time_s[index] - time_s[index - 1])
        delta = root_b_target[index] - root_b[index - 1]
        distance = float(np.linalg.norm(delta))
        maximum = MAXIMUM_ROOT_SPEED_MPS * dt
        if distance > maximum and distance > 0.0:
            delta *= maximum / distance
            limited += 1
        root_b[index] = root_b[index - 1] + delta
        maximum_withheld = max(
            maximum_withheld,
            float(np.linalg.norm(root_b_target[index] - root_b[index])),
        )

    output.mkdir(parents=False)
    np.savez_compressed(
        output / "ROOT_AB_FULL_CAUSAL_SLEW.npz",
        time_s=time_s,
        root_a_world_m=root_a,
        root_b_world_m=root_b,
        root_b_unslewed_target_world_m=root_b_target,
        velocity_a_world_mps=velocity_a,
        velocity_b_posterior_world_mps=velocity_b,
        anchors_world_m=anchors,
    )
    result: dict[str, object] = {
        "schema": "biospur.c2.full_calibration.root_ab.causal_slew.v1",
        "status": "FULL_00_TO_19_CAUSAL_ANTI_DRIFT_COMPLETE",
        "scientific_pass": False,
        "session": {
            "protocol_slots": 20,
            "acquired_actions": 19,
            "slot_01": "MARKER_ONLY_NOT_ACQUIRED",
            "state_resets": 0,
            "action_labels_used_by_algorithm": False,
            "samples": int(len(time_s)),
        },
        "branch_a_pure_imu": _metrics(root_a, time_s),
        "branch_b_imu_uwb_causal_slew": _metrics(root_b, time_s),
        "publication_guard": {
            "maximum_root_speed_mps": MAXIMUM_ROOT_SPEED_MPS,
            "maximum_native200_step_limit_m": MAXIMUM_ROOT_SPEED_MPS * 0.0055,
            "limited_frames": limited,
            "maximum_withheld_correction_m": maximum_withheld,
            "causal": True,
            "changes_accepted_uwb_target": False,
        },
        "source": {
            "result": str(source_result),
            "result_sha256": _sha256(source_result),
            "trajectory": str(source_npz),
            "trajectory_sha256": _sha256(source_npz),
        },
    }
    result_path = output / "RESULT.json"
    result_path.write_text(json.dumps(result, indent=2) + "\n")
    files = (output / "ROOT_AB_FULL_CAUSAL_SLEW.npz", result_path)
    (output / "SHA256SUMS").write_text(
        "".join(f"{_sha256(path)}  {path.name}\n" for path in files)
    )
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    print(json.dumps(run(arguments.source, arguments.output), indent=2))
