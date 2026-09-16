#!/usr/bin/env python3
"""Isolated low-memory wrapper for a future, separately authorized U7E7 replay."""
from __future__ import annotations

import argparse
import gc
import json
from pathlib import Path
from typing import Any

import numpy as np

import run_c2_direct_body_shadow_ab_pilot as pose
import run_c2_owner_bound_async_worker_u7e6_action04 as replay
from biospur_fusion.c2_uwb_root_world.run_calibration import _clock_models


ROOT = Path(__file__).resolve().parents[1]
ACTION = "04_shoulder_left"
BLOCKED = ROOT / "logs/c2_owner_bound_async_worker_u7e6_action04_20260906T202300Z"
BLOCKED_SHA256 = "89dcedae8016423f1af1ab2512420d053186d2f707de391414b23b6f72ac1431"
BLOCKED_REVISION_003 = ROOT / "logs/c2_owner_bound_async_worker_u7e7_action04_revision_003_20260906T221500Z"
BLOCKED_REVISION_003_SHA256 = "7a9984a77b3dc8745bfa69d624ca5dac4a4e645eec523051043c4169ef20a5f8"
BLOCKED_REVISION_004 = ROOT / "logs/c2_owner_bound_async_worker_u7e7_action04_revision_004_20260906T233000Z"
BLOCKED_REVISION_004_SHA256 = "8cb643c049620c250fe1bf1adedf706089de7b63ec9945cdd220e4acef873098"
REFERENCE_PREFLIGHT = ROOT / "logs/c2_owner_bound_async_worker_u7e7_reference_promotion_preflight_20260907T001500Z"
REFERENCE_PREFLIGHT_SHA256 = "724e863f6d0a62ed356b30a06a9627d20e6f90b254f2c3011ea0ef946e8ae3ab"
CURRENT_CONTRACT_REFERENCE = REFERENCE_PREFLIGHT / "CURRENT_CONTRACT_REFERENCE.jsonl"
CURRENT_CONTRACT_REFERENCE_SHA256 = "7ae933e119e28f8c1fde319d316d4ce33e462b2852b53e65701008ab71c7de17"


def _bind_prior_failures() -> None:
    replay.SEALS = {
        **replay.SEALS,
        BLOCKED: BLOCKED_SHA256,
        BLOCKED_REVISION_003: BLOCKED_REVISION_003_SHA256,
        BLOCKED_REVISION_004: BLOCKED_REVISION_004_SHA256,
        REFERENCE_PREFLIGHT: REFERENCE_PREFLIGHT_SHA256,
    }
    replay.FILES = tuple(dict.fromkeys((*replay.FILES, Path(__file__).resolve())))
    replay.PRIOR_BLOCKED_NON_PROMOTED = True
    replay.CURRENT_CONTRACT_REFERENCE = CURRENT_CONTRACT_REFERENCE
    replay.CURRENT_CONTRACT_REFERENCE_SHA256 = CURRENT_CONTRACT_REFERENCE_SHA256
    replay.CURRENT_CONTRACT_REFERENCE_CLASS = "CURRENT_CONTRACT_DIAGNOSTIC_REFERENCE"
    replay.CURRENT_CONTRACT_REFERENCE_PROMOTED = True
    replay.HISTORICAL_NUMERIC_REFERENCE_STATUS = {
        "old_u3_numeric": "HISTORICAL_PRE_CONTEXT_TAIL_NON_PROMOTED",
        "old_u5b_numeric": "HISTORICAL_PRE_CONTEXT_TAIL_NON_PROMOTED",
    }


class _LazyActionTrajectory(dict):
    """Load only the selected action, after decoded-event release."""
    def __init__(self, archive: Path) -> None:
        super().__init__()
        self._archive = archive

    def __getitem__(self, key: str) -> Any:
        if key not in self:
            expected = f"{pose.EPISODES.index(ACTION):02d}"
            if key != expected:
                raise KeyError(key)
            value: dict[str, Any] = {}
            with np.load(self._archive, allow_pickle=False) as source:
                for segment in pose.SEGMENTS:
                    prefix = f"trajectory/{key}/{segment}"
                    value[segment] = {
                        "time_root_s": np.array(source[f"{prefix}/time_root_s"]),
                        "quat_world_segment_wxyz": np.array(source[f"{prefix}/quat_world_segment_wxyz"]),
                        "mask": np.array(source[f"{prefix}/mask"], dtype=bool),
                    }
            self[key] = value
        return super().__getitem__(key)


def _verified_action_pose_inputs():
    accepted_result = json.loads(pose.ACCEPTED_RESULT.read_text())
    base_report = json.loads(pose.BASE_REPORT.read_text())
    clock_document = json.loads(pose.CLOCK_TABLE.read_text())
    frontend_manifest = json.loads(pose.FRONTEND_MANIFEST.read_text())
    accepted_path = ROOT / accepted_result["calibration_trajectory"]["path"]
    expected = {
        accepted_path: accepted_result["calibration_trajectory"]["sha256"],
        ROOT / base_report["trajectory"]["path"]: base_report["trajectory"]["sha256"],
        pose.FRONTEND_ARCHIVE: base_report["frontend"]["archive_sha256"],
        pose.FRONTEND_MANIFEST: base_report["frontend"]["manifest_sha256"],
    }
    for path, digest in expected.items():
        if pose._sha256(path) != digest:
            raise RuntimeError(f"sealed pose input hash mismatch: {path}")
    if accepted_result.get("sample_rate_hz") != 200.0 or accepted_result.get("pose_interpolation") is not False or accepted_result.get("mechanism_pass") is not True:
        raise RuntimeError("accepted native200 pose qualification changed")
    if base_report.get("physical_time_windows_unchanged") is not True or base_report.get("grid_period_ns") != 5_000_000:
        raise RuntimeError("native200 source clock contract changed")
    clock_source = ROOT / "src/biospur_fusion/c2_uwb_root_world/beacon_clock.py"
    if clock_document.get("source_sha256") != pose._sha256(clock_source):
        raise RuntimeError("sealed clock source binding changed")
    pelvis_clock = _clock_models(pose.CLOCK_TABLE)[pose.PELVIS_NODE]
    key = f"{pose.EPISODES.index(ACTION):02d}"
    with np.load(pose.FRONTEND_ARCHIVE, allow_pickle=False) as frontend:
        values = {}
        for suffix in ("time_us", "derived_boot_epoch", "contiguous_span_id"):
            member = f"orientation/{key}/{pose.PELVIS_NODE}/{suffix}"
            value = np.array(frontend[member], copy=True)
            binding = frontend_manifest["array_bindings"][member]
            if list(value.shape) != binding["shape"] or str(value.dtype) != binding["dtype"] or pose._array_sha256(value) != binding["sha256"]:
                raise RuntimeError(f"frontend pose binding failed: {member}")
            values[suffix] = value
    if not np.all(values["derived_boot_epoch"].astype(np.int64) == int(pelvis_clock.boot_epoch)):
        raise RuntimeError("pelvis boot differs from clock owner")
    lazy = _LazyActionTrajectory(accepted_path)
    # Only the small clock/mask ownership arrays are read here. Quaternion pose
    # arrays remain lazy until after decoded events are released and collected.
    with np.load(accepted_path, allow_pickle=False) as source:
        times = np.array(source[f"trajectory/{key}/pelvis/time_root_s"], dtype=float)
        valid = np.logical_and.reduce([
            np.array(source[f"trajectory/{key}/{segment}/mask"], dtype=bool)
            for segment in pose.SEGMENTS
        ])
    owner = pose.DirectNative200Clock(
        action=ACTION,
        time_root_s=times,
        source_pelvis_timer_us=values["time_us"].astype(np.int64),
        source_contiguous_span_id=values["contiguous_span_id"].astype(np.int64),
        common_clock_a_ns_per_us=pelvis_clock.a_ns_per_us,
        common_clock_b_ns=pelvis_clock.b_ns,
        valid_mask=valid,
    )
    support = clock_document["models"][pose.PELVIS_NODE]
    if int(owner.timer_us[0]) < int(support["first_timer_us"]) or int(owner.timer_us[-1]) > int(support["last_timer_us"]):
        raise RuntimeError("pose lies outside clock support")
    return {"trajectory": lazy}, {ACTION: owner}, {
        "actions": {ACTION: {"samples": len(times), "first_timer_us": int(owner.timer_us[0]),
            "last_timer_us": int(owner.timer_us[-1]), "strict_floor": True}},
        "accepted_path": str(accepted_path.relative_to(ROOT)), "accepted_sha256": pose._sha256(accepted_path),
        "frontend_sha256": pose._sha256(pose.FRONTEND_ARCHIVE), "clock_table_sha256": pose._sha256(pose.CLOCK_TABLE),
        "raw_uwb_opened": False, "H01_H02_opened_or_hashed": False,
        "loaded_actions": [ACTION], "deferred_trajectory_materialization": True,
    }


_original_pelvis_imu = replay._pelvis_imu


def _pelvis_imu_release(events, *args, **kwargs):
    result = _original_pelvis_imu(events, *args, **kwargs)
    if hasattr(events, "clear"):
        events.clear()
    gc.collect()
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    _bind_prior_failures()
    replay._verified_pose_inputs = _verified_action_pose_inputs
    replay._pelvis_imu = _pelvis_imu_release
    identity = Path(__file__).resolve()
    command = replay._build_command(identity, args.output)
    return replay.main(authoritative_command=command, runner_identity=identity)


if __name__ == "__main__":
    raise SystemExit(main())
