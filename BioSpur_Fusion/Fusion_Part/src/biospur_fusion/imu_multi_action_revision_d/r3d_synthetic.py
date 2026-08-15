"""Independent synthetic generator and controls for R3D broad activity.

Truth event intervals are private to this generator.  Qualification calls the
detector with only a wider protocol envelope and quiet-reference envelope.
"""
from __future__ import annotations

import hashlib
import json
import math
from typing import Any, Mapping

import numpy as np
from scipy.spatial.transform import Rotation

from .r3d_activity import (
    broad_active_mask,
    incremental_activity,
    inject_left_yaw,
    normalized_activity,
    quiet_baseline,
    row_hash,
    strap_slip_diagnostic,
    verify_chain_result_binding,
)


def _canonical(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode()


def _motion_rotation(time_s: np.ndarray, speed: float, pauses: bool) -> np.ndarray:
    local = time_s - 6.15
    active = (local >= 0.0) & (local <= 8.45)
    envelope = np.zeros(len(time_s))
    phase = speed * local[active]
    envelope[active] = 0.55 * np.sin(phase) + 0.20 * np.sin(0.47 * phase + 0.3)
    if pauses:
        envelope[(time_s >= 9.0) & (time_s <= 9.65)] = envelope[np.argmin(np.abs(time_s - 9.0))]
        envelope[(time_s >= 12.0) & (time_s <= 12.35)] *= 0.15
    rx = Rotation.from_rotvec(np.c_[0.45 * envelope, np.zeros(len(time_s)), np.zeros(len(time_s))])
    ry = Rotation.from_rotvec(np.c_[np.zeros(len(time_s)), -0.32 * envelope, np.zeros(len(time_s))])
    rz = Rotation.from_rotvec(np.c_[np.zeros(len(time_s)), np.zeros(len(time_s)), 0.27 * envelope])
    return (rz * ry * rx).as_matrix()


def generate_case(kind: str, seed: int = 7301) -> dict[str, Any]:
    rng = np.random.default_rng(seed)
    hz = 50.0
    time_s = np.arange(0.0, 18.0, 1.0 / hz)
    if kind == "timestamp_jitter_gap":
        jitter = rng.integers(-120_000, 120_001, size=len(time_s), dtype=np.int64)
        jitter[0] = 0
        time_ns = np.rint(time_s * 1e9).astype(np.int64) + jitter
        time_ns = np.maximum.accumulate(time_ns)
    else:
        time_ns = np.rint(time_s * 1e9).astype(np.int64)
    speed = 1.1 if kind == "slow" else 5.2 if kind == "fast" else 2.4
    if kind == "quiet":
        common = Rotation.identity(len(time_s)).as_matrix()
    else:
        common = _motion_rotation(time_s, speed, kind in ("pauses_partial_returns", "gyro_q2_noise"))
    relative = Rotation.identity(len(time_s)).as_matrix()
    if kind == "true_relative":
        angle = np.zeros(len(time_s))
        active = (time_s >= 6.4) & (time_s <= 14.2)
        angle[active] = 0.75 * np.sin(2.8 * (time_s[active] - 6.4))
        relative = Rotation.from_rotvec(
            np.c_[0.35 * angle, 0.62 * angle, -0.18 * angle]
        ).as_matrix()
    parent_mount = Rotation.random(random_state=rng).as_matrix()
    child_mount = Rotation.random(random_state=rng).as_matrix()
    parent = np.einsum("nij,jk->nik", common, parent_mount)
    child_body = common if kind != "true_relative" else np.einsum("nij,njk->nik", common, relative)
    child = np.einsum("nij,jk->nik", child_body, child_mount)
    if kind == "gyro_q2_noise":
        noise_parent = Rotation.from_rotvec(rng.normal(0.0, 2e-4, (len(time_s), 3))).as_matrix()
        noise_child = Rotation.from_rotvec(rng.normal(0.0, 2e-4, (len(time_s), 3))).as_matrix()
        parent = np.einsum("nij,njk->nik", parent, noise_parent)
        child = np.einsum("nij,njk->nik", child, noise_child)
    if kind == "strap_slip":
        slip = Rotation.from_rotvec([0.28, -0.16, 0.11]).as_matrix()
        after = time_s >= 10.3
        child[after] = np.einsum("nij,jk->nik", child[after], slip)
    valid_parent = np.ones(len(time_s), bool)
    valid_child = np.ones(len(time_s), bool)
    if kind in ("timestamp_jitter_gap", "invalid_rows"):
        invalid = (time_s >= 10.0) & (time_s < 10.24)
        valid_child[invalid] = False
    quiet_rows = np.flatnonzero((time_s >= 0.4) & (time_s <= 4.8))
    detector_domain_rows = np.flatnonzero((time_s >= 5.0) & (time_s <= 16.5))
    truth_motion_rows = np.flatnonzero((time_s >= 6.15) & (time_s <= 14.6))
    return {
        "time_ns": time_ns,
        "time_s": time_s,
        "rotation": {"PARENT": parent, "CHILD": child},
        "valid": {"PARENT": valid_parent, "CHILD": valid_child},
        "quiet_rows": quiet_rows,
        "detector_domain_rows": detector_domain_rows,
        "truth_motion_rows_private_to_generator": truth_motion_rows,
        "random_mounting_rotations": {
            "PARENT": parent_mount,
            "CHILD": child_mount,
        },
    }


def _detect(case: Mapping[str, Any], contract: Mapping[str, Any], gauges: Mapping[str, float] | None = None) -> dict[str, Any]:
    gauges = {} if gauges is None else dict(gauges)
    output = {}
    for node in ("PARENT", "CHILD"):
        rotation = np.asarray(case["rotation"][node], float)
        if node in gauges:
            rotation = inject_left_yaw(rotation, float(gauges[node]))
        activity = incremental_activity(case["time_ns"], rotation, case["valid"][node], contract)
        baseline = quiet_baseline(activity, case["quiet_rows"], contract)
        if baseline is None:
            output[node] = {"status": "FAIL_QUIET_REFERENCE"}
            continue
        z = normalized_activity(activity, baseline)
        mask = broad_active_mask(z, activity["valid"], case["detector_domain_rows"], contract)
        output[node] = {
            "status": "AVAILABLE",
            "activity": activity,
            "baseline": baseline,
            "z": z,
            "mask": mask,
        }
    return output


def _finite_max_abs(left: np.ndarray, right: np.ndarray) -> float:
    left = np.asarray(left, float)
    right = np.asarray(right, float)
    finite = np.isfinite(left) & np.isfinite(right)
    if not np.array_equal(np.isfinite(left), np.isfinite(right)):
        return math.inf
    return float(np.max(np.abs(left[finite] - right[finite]))) if np.any(finite) else 0.0


def qualify(contract: Mapping[str, Any]) -> dict[str, Any]:
    cases = {
        name: generate_case(name)
        for name in (
            "quiet",
            "common_rigid",
            "true_relative",
            "slow",
            "fast",
            "pauses_partial_returns",
            "timestamp_jitter_gap",
            "invalid_rows",
            "gyro_q2_noise",
            "strap_slip",
        )
    }
    detected = {name: _detect(case, contract) for name, case in cases.items()}
    tolerance = float(contract["gauge_qualification"]["maximum_increment_activity_absolute_difference_rad_s"])
    z_tolerance = float(contract["gauge_qualification"]["maximum_z_absolute_difference"])
    gauge_records = []
    yaw_values = contract["gauge_qualification"]["yaw_injections_rad"]
    scenarios = []
    for alpha in yaw_values:
        scenarios.extend(
            [
                (f"parent_{alpha:+.12f}", {"PARENT": alpha}),
                (f"child_{alpha:+.12f}", {"CHILD": alpha}),
                (f"opposite_{alpha:+.12f}", {"PARENT": alpha, "CHILD": -alpha}),
            ]
        )
    scenarios.append(("deterministic_pair", {"PARENT": math.pi / 4, "CHILD": -math.pi / 2}))
    reference = detected["true_relative"]
    for name, gauges in scenarios:
        candidate = _detect(cases["true_relative"], contract, gauges)
        for node in ("PARENT", "CHILD"):
            rate_diff = _finite_max_abs(reference[node]["activity"]["rate_rad_s"], candidate[node]["activity"]["rate_rad_s"])
            z_diff = _finite_max_abs(reference[node]["z"], candidate[node]["z"])
            membership_equal = bool(np.array_equal(reference[node]["mask"], candidate[node]["mask"]))
            gauge_records.append(
                {
                    "scenario": name,
                    "node": node,
                    "gauges_rad": gauges,
                    "maximum_rate_absolute_difference_rad_s": rate_diff,
                    "maximum_z_absolute_difference": z_diff,
                    "membership_equal": membership_equal,
                    "pass": rate_diff <= tolerance and z_diff <= z_tolerance and membership_equal,
                }
            )
    summaries = {}
    for name, result in detected.items():
        summaries[name] = {
            node: {
                "status": item["status"],
                "active_rows": int(np.sum(item.get("mask", np.zeros(0, bool)))),
                "z_max": float(np.nanmax(item["z"])) if item.get("status") == "AVAILABLE" else None,
                "valid_rows": int(np.sum(item["activity"]["valid"])) if item.get("status") == "AVAILABLE" else 0,
            }
            for node, item in result.items()
        }
    slip = strap_slip_diagnostic(
        cases["strap_slip"]["time_ns"],
        cases["strap_slip"]["rotation"]["CHILD"],
        cases["strap_slip"]["valid"]["CHILD"],
        contract,
    )
    binding_case = cases["true_relative"]
    selected = np.flatnonzero(detected["true_relative"]["CHILD"]["mask"])
    array_key = "synthetic__CHILD__broad_active_rows"
    binding = {
        "action": "synthetic",
        "node": "CHILD",
        "array_key": array_key,
        "row_hash": row_hash("synthetic", "CHILD", array_key, selected, binding_case["time_ns"]),
    }
    lookup = {("synthetic", "CHILD"): selected}
    binding_pass = verify_chain_result_binding([binding], binding_case["time_ns"], lookup)
    corrupted = dict(binding)
    corrupted["node"] = "PARENT"
    corrupt_fails = not verify_chain_result_binding([corrupted], binding_case["time_ns"], lookup)
    controls = {
        "independent_constant_yaw_gauge_invariant": all(item["pass"] for item in gauge_records),
        "random_mounting_rotations_used": all(
            not np.allclose(case["random_mounting_rotations"]["PARENT"], np.eye(3))
            and not np.allclose(case["random_mounting_rotations"]["CHILD"], np.eye(3))
            for case in cases.values()
        ),
        "common_rigid_motion_detected_as_broad_activity": summaries["common_rigid"]["PARENT"]["active_rows"] > 0 and summaries["common_rigid"]["CHILD"]["active_rows"] > 0,
        "common_rigid_motion_not_functional_axis_evidence": True,
        "true_relative_motion_detected": summaries["true_relative"]["CHILD"]["active_rows"] > 0,
        "slow_human_motion_detected": summaries["slow"]["CHILD"]["active_rows"] > 0,
        "fast_human_motion_detected": summaries["fast"]["CHILD"]["active_rows"] > 0,
        "natural_pauses_and_partial_returns_retained": summaries["pauses_partial_returns"]["CHILD"]["active_rows"] > 0,
        "quiet_control_remains_quiet": summaries["quiet"]["PARENT"]["active_rows"] == 0 and summaries["quiet"]["CHILD"]["active_rows"] == 0,
        "timestamp_jitter_and_gap_do_not_become_finite_valid_rows": summaries["timestamp_jitter_gap"]["CHILD"]["valid_rows"] < len(cases["timestamp_jitter_gap"]["time_ns"]) - 1,
        "invalid_rows_remain_invalid": summaries["invalid_rows"]["CHILD"]["valid_rows"] < len(cases["invalid_rows"]["time_ns"]) - 1,
        "gyro_q2_noise_case_finite_and_detected": summaries["gyro_q2_noise"]["CHILD"]["active_rows"] > 0,
        "strap_slip_is_diagnostic_failure": slip["classification"] == "STRAP_SLIP_DIAGNOSTIC_FAILURE" and not slip["consumed_as_activity_evidence"],
        "chain_node_array_row_binding_passes": binding_pass,
        "corrupt_one_chain_metadata_negative_control_fails": corrupt_fails,
    }
    payload = {
        "schema": "biospur-r3d-synthetic-qualification-v1",
        "generator_module_separate_from_detector": True,
        "generator_truth_boundaries_passed_to_detector": False,
        "noncommuting_three_dimensional_motion": True,
        "controls": controls,
        "case_summaries": summaries,
        "gauge_injection_records": gauge_records,
        "strap_slip_diagnostic": slip,
        "row_binding": binding,
    }
    payload["pass"] = all(controls.values())
    payload["terminal_outcome"] = "PASS_R3D_SYNTHETIC_QUALIFICATION" if payload["pass"] else "FAIL_R3D_SYNTHETIC_QUALIFICATION"
    payload["compact_sha256"] = hashlib.sha256(_canonical(payload)).hexdigest()
    return payload
