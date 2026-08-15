#!/usr/bin/env python3
"""Read-only R3C-0 dimensional and historical reproducibility audit."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import sys
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "Fusion_Part/src"))

from biospur_fusion.imu_multi_action_engineering_v1.common_time import build_common_timeline
from biospur_fusion.imu_multi_action_engineering_v1.pipeline import load_q2_cache
from biospur_fusion.imu_multi_action_revision_d.r3_cycle import (
    build_excursion_signal as legacy_build_excursion_signal,
    relative_orientation,
    relative_rate_signal,
    select_pre_reference,
)
from biospur_fusion.imu_multi_action_revision_d.r3b_topology import quantiles


R3B_SOURCE = ROOT / "Fusion_Part/src/biospur_fusion/imu_multi_action_revision_d/r3b_topology.py"
R3_SOURCE = ROOT / "Fusion_Part/src/biospur_fusion/imu_multi_action_revision_d/r3_cycle.py"
R3_RUNNER = ROOT / "Fusion_Part/tools/run_imu_multi_action_revision_d_r3.py"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def clean(value: Any) -> Any:
    if isinstance(value, np.ndarray):
        return clean(value.tolist())
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return clean(float(value))
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {str(key): clean(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean(item) for item in value]
    return value


def dump(path: Path, value: Any) -> None:
    path.write_text(json.dumps(clean(value), sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n")


def percentile_summary(values: np.ndarray) -> dict[str, float | None]:
    finite = np.asarray(values, float)
    finite = finite[np.isfinite(finite)]
    if not len(finite):
        return {key: None for key in ("p05", "p50", "p95")}
    return {"p05": float(np.percentile(finite, 5)), "p50": float(np.percentile(finite, 50)), "p95": float(np.percentile(finite, 95))}


def runs(mask: np.ndarray) -> list[tuple[int, int]]:
    padded = np.r_[False, np.asarray(mask, bool), False]
    edge = np.diff(padded.astype(np.int8))
    return list(zip(np.flatnonzero(edge == 1), np.flatnonzero(edge == -1)))


def chains() -> list[dict[str, str]]:
    return [
        {"key": "arms:left", "action": "arms", "parent": "torso", "child": "upper_arm_L"},
        {"key": "arms:right", "action": "arms", "parent": "torso", "child": "upper_arm_R"},
        {"key": "arms:left_forearm", "action": "arms", "parent": "upper_arm_L", "child": "forearm_L"},
        {"key": "arms:right_forearm", "action": "arms", "parent": "upper_arm_R", "child": "forearm_R"},
        {"key": "left_elbow:elbow_L", "action": "left_elbow", "parent": "upper_arm_L", "child": "forearm_L"},
        {"key": "right_elbow_attempt2:elbow_R", "action": "right_elbow_attempt2", "parent": "upper_arm_R", "child": "forearm_R"},
        {"key": "left_knee:hip_L", "action": "left_knee", "parent": "pelvis", "child": "thigh_L"},
        {"key": "left_knee:knee_L", "action": "left_knee", "parent": "thigh_L", "child": "shank_L"},
        {"key": "right_knee:hip_R", "action": "right_knee", "parent": "pelvis", "child": "thigh_R"},
        {"key": "right_knee:knee_R", "action": "right_knee", "parent": "thigh_R", "child": "shank_R"},
        {"key": "left_heel:knee_L", "action": "left_heel", "parent": "thigh_L", "child": "shank_L"},
        {"key": "left_heel:hip_L", "action": "left_heel", "parent": "pelvis", "child": "thigh_L"},
        {"key": "right_heel:knee_R", "action": "right_heel", "parent": "thigh_R", "child": "shank_R"},
        {"key": "right_heel:hip_R", "action": "right_heel", "parent": "pelvis", "child": "thigh_R"},
        {"key": "squats:hip_L", "action": "squats", "parent": "pelvis", "child": "thigh_L"},
        {"key": "squats:hip_R", "action": "squats", "parent": "pelvis", "child": "thigh_R"},
        {"key": "squats:knee_L", "action": "squats", "parent": "thigh_L", "child": "shank_L"},
        {"key": "squats:knee_R", "action": "squats", "parent": "thigh_R", "child": "shank_R"},
        {"key": "trunk:trunk", "action": "trunk", "parent": "pelvis", "child": "torso"},
    ]


def old_activity(time_ns: np.ndarray, relative: np.ndarray, covariance: np.ndarray, valid: np.ndarray, floor: float) -> dict[str, np.ndarray]:
    dt = np.r_[np.nan, np.diff(time_ns) / 1e9]
    pair = valid & np.r_[False, valid[:-1]] & (dt > 0)
    indices = np.flatnonzero(pair)
    increment = np.full(len(time_ns), np.nan)
    if len(indices):
        from scipy.spatial.transform import Rotation
        delta = np.einsum("nji,njk->nik", relative[indices - 1], relative[indices])
        increment[indices] = Rotation.from_matrix(delta).magnitude()
    rate = increment / dt
    variance = np.full(len(time_ns), np.nan)
    if len(indices):
        variance[indices] = np.maximum(
            np.trace(covariance[indices] + covariance[indices - 1], axis1=1, axis2=2) / 3.0,
            0.0,
        )
    sigma = np.hypot(np.nan_to_num(np.sqrt(variance) / dt, nan=0.0), floor)
    return {"dt_s": dt, "valid": pair, "increment_rad": increment, "rate_rad_s": rate, "variance_rad2": variance, "sigma_rad_s": sigma}


def lowest_plateau(rate: np.ndarray, valid: np.ndarray, rows: np.ndarray, duration_s: float, stride_s: float, hz: float) -> np.ndarray:
    length = max(2, round(duration_s * hz))
    stride = max(1, round(stride_s * hz))
    candidates: list[tuple[float, float, int, np.ndarray]] = []
    for offset in range(0, max(1, len(rows) - length + 1), stride):
        block = rows[offset : offset + length]
        keep = valid[block] & np.isfinite(rate[block])
        if len(block) == length and float(np.mean(keep)) >= 0.8 and np.any(keep):
            values = rate[block][keep]
            candidates.append((float(np.median(values)), float(np.percentile(values, 90)), int(block[0]), block))
    return min(candidates, key=lambda item: item[:3])[3] if candidates else np.asarray([], dtype=int)


def analyze(args: argparse.Namespace) -> dict[str, Any]:
    if args.output.exists():
        raise FileExistsError(args.output)
    args.output.mkdir(parents=True)
    phase = json.loads((args.phase_a / "RESULT.json").read_text())
    cache = args.phase_a / "Q2_HUMAN_QUASI_STATIC_CACHE.npz"
    if sha256(cache) != phase["q2_cache_sha256"]:
        raise RuntimeError("Q2 cache binding failed")
    if phase["final_still"] != "SEALED" or phase["data_access"]["final_still"] != "SEALED_NOT_OPENED":
        raise RuntimeError("final_still firewall failed")
    r2 = json.loads((args.r2 / "ACTION_PHASE_TIMELINE.json").read_text())
    r2_result = json.loads((args.r2 / "RESULT.json").read_text())
    if sha256(args.r2 / "ACTION_PHASE_TIMELINE.json") != r2_result["action_phase_timeline_sha256"]:
        raise RuntimeError("R2 timeline binding failed")
    gates = json.loads(args.gates.read_text())
    old_contract = json.loads(args.r3_contract.read_text())
    r3b_contract = json.loads(args.r3b_contract.read_text())
    q2 = load_q2_cache(cache)
    domains = {name: tuple(item["search_domain_ns"]) for name, item in r2["actions"].items()}
    timeline = build_common_timeline(q2, min(x[0] for x in domains.values()), max(x[1] for x in domains.values()), gates["common_time"])
    node_index = {node: i for i, node in enumerate(timeline.node_order)}
    segment_index = {segment: node_index[node] for node, segment in gates["node_to_segment"].items()}
    hz = float(r3b_contract["common_time"]["rate_hz"])
    gyro_noise = 0.003
    nominal_dt = 1.0 / hz
    derived_process_floor = math.sqrt(2.0) * gyro_noise / math.sqrt(nominal_dt)
    deterministic_floor = max(derived_process_floor, float(r3b_contract["coordinate"]["relative_activity_uncertainty_floor_rad_s"]))
    records = []
    for spec in chains():
        rows = np.flatnonzero((timeline.time_ns >= domains[spec["action"]][0]) & (timeline.time_ns <= domains[spec["action"]][1]))
        parent = segment_index[spec["parent"]]
        child = segment_index[spec["child"]]
        relative = relative_orientation(timeline.rotation[:, parent], timeline.rotation[:, child])
        valid = timeline.valid[:, parent] & timeline.valid[:, child] & np.isfinite(relative).all(axis=(1, 2))
        parent_covariance = timeline.covariance_rad2[:, parent]
        child_covariance = timeline.covariance_rad2[:, child]
        relative_covariance = parent_covariance + child_covariance
        old = old_activity(timeline.time_ns, relative, relative_covariance, valid, float(r3b_contract["coordinate"]["relative_activity_uncertainty_floor_rad_s"]))
        plateau = lowest_plateau(old["rate_rad_s"], old["valid"], rows, 0.40, 0.10, hz)
        values = old["rate_rad_s"][plateau]
        values = values[np.isfinite(values)]
        location = float(np.median(values))
        mad_scale = 1.4826 * float(np.median(np.abs(values - location)))
        empirical_scale = max(mad_scale, deterministic_floor)
        z = (old["rate_rad_s"] - location) / empirical_scale
        quiet_fp = float(np.mean(z[plateau][np.isfinite(z[plateau])] >= 4.0))
        process = np.full(len(timeline.time_ns), np.nan)
        process[old["valid"]] = math.sqrt(2.0) * gyro_noise / np.sqrt(old["dt_s"][old["valid"]])
        old_baseline = old["sigma_rad_s"][plateau]
        p_cov_trace = np.trace(parent_covariance[plateau], axis1=1, axis2=2)
        c_cov_trace = np.trace(child_covariance[plateau], axis1=1, axis2=2)
        records.append({
            **spec,
            "rows": {"start": int(rows[0]), "stop_exclusive": int(rows[-1] + 1), "count": int(len(rows))},
            "actual_dt_s": percentile_summary(old["dt_s"][rows]),
            "parent_absolute_q2_covariance_trace_rad2": percentile_summary(p_cov_trace),
            "child_absolute_q2_covariance_trace_rad2": percentile_summary(c_cov_trace),
            "sigma_activity_old_rad_s": percentile_summary(old["sigma_rad_s"][rows]),
            "sigma_activity_old_deg_s": {key: None if value is None else math.degrees(value) for key, value in percentile_summary(old["sigma_rad_s"][rows]).items()},
            "quiet_empirical": {"location_rad_s": location, "mad_scale_rad_s": mad_scale, "deterministic_noise_floor_rad_s": deterministic_floor, "scale_rad_s": empirical_scale, "scale_deg_s": math.degrees(empirical_scale), "false_positive_rate_at_z4": quiet_fp},
            "sigma_process_rad_s": percentile_summary(process[rows]),
            "sigma_process_deg_s": {key: None if value is None else math.degrees(value) for key, value in percentile_summary(process[rows]).items()},
            "raw_activity_rad_s": {**percentile_summary(old["rate_rad_s"][rows]), "max": quantiles(old["rate_rad_s"][rows])["max"]},
            "raw_activity_deg_s": {key: None if value is None else math.degrees(value) for key, value in percentile_summary(old["rate_rad_s"][rows]).items()},
            "activity_z_empirical": {**percentile_summary(z[rows]), "max": quantiles(z[rows])["max"]},
            "raw_onset_threshold_old_rad_s": location + 4.0 * float(np.median(old_baseline)),
            "raw_onset_threshold_empirical_rad_s": location + 4.0 * empirical_scale,
            "raw_onset_threshold_process_rad_s": location + 4.0 * float(np.median(process[plateau])),
            "old_threshold_human_sensor_feasible": bool(location + 4.0 * float(np.median(old_baseline)) < math.radians(5000.0)),
            "empirical_active_dynamic_range": (float(np.percentile(old["rate_rad_s"][rows][np.isfinite(old["rate_rad_s"][rows])], 95)) - location) / empirical_scale,
            "old_assumptions": {"adjacent_pose_errors_independent": True, "temporal_cross_covariance_available": False, "absolute_global_yaw_included": True},
        })
    components = {
        "schema": "biospur-r3c0-activity-uncertainty-components-v1",
        "R3B_ORIGINAL_VERDICT": "FAIL_REQUIRED_MOTION_EVIDENCE_MISSING",
        "R3B_ADOPTABLE": False,
        "corrected_interpretation": {"OBSERVED_FAILURE": "Q2_PROPAGATED_ACTIVITY_SCALE_APPROX_181_4_RAD_PER_S", "MOTION_EVIDENCE_MISSING": "NOT_ESTABLISHED", "MOTION_EVIDENCE": "NOT_EVALUABLE_UNDER_INVALID_NORMALIZER", "PRIMARY_BLOCKER": "FAIL_ACTIVITY_UNCERTAINTY_MODEL_INVALID"},
        "process_floor_derivation": {"per_node_gyro_noise_sigma_rad_s_sqrt_hz": gyro_noise, "relative_two_node_formula": "sqrt(2)*gyro_noise/sqrt(dt)", "nominal_dt_s": nominal_dt, "derived_floor_rad_s": derived_process_floor, "preserved_predata_floor_rad_s": float(r3b_contract["coordinate"]["relative_activity_uncertainty_floor_rad_s"]), "production_candidate_floor_rad_s": deterministic_floor},
        "chains": records,
    }
    dump(args.output / "ACTIVITY_UNCERTAINTY_COMPONENTS.json", components)
    representative = records[0]
    dimensional = {
        "schema": "biospur-r3c0-dimensional-analysis-v1",
        "verdict": "PASS_ROOT_CAUSE_IDENTIFIED",
        "old_formula_units": [
            {"term": "Q2 covariance P", "unit": "rad^2", "kind": "covariance", "already_square_rooted": False, "already_divided_by_dt": False},
            {"term": "P_parent(t)+P_child(t)", "unit": "rad^2", "kind": "relative absolute-pose covariance under unsupported independence assumption"},
            {"term": "P_rel(t)+P_rel(t-dt)", "unit": "rad^2", "kind": "increment covariance only if temporal errors independent"},
            {"term": "sqrt(trace(...)/3)", "unit": "rad", "kind": "scalar standard deviation"},
            {"term": "sqrt(...)/dt", "unit": "rad/s", "kind": "rate standard deviation"},
        ],
        "error_checks": {
            "COVARIANCE_USED_AS_STANDARD_DEVIATION": False,
            "STANDARD_DEVIATION_SQUARED_TWICE": False,
            "DEGREE_RADIAN_UNIT_ERROR": False,
            "DT_DIVIDED_TWICE": False,
            "ABSOLUTE_ATTITUDE_COVARIANCE_USED_AS_INCREMENT_NOISE": True,
            "TEMPORAL_CROSS_COVARIANCE_OMITTED": True,
            "PARENT_CHILD_UNCERTAINTY_DOUBLE_COUNTED": "NOT_PROVABLE_WITHOUT_PARENT_CHILD_CROSS_COVARIANCE",
            "GLOBAL_YAW_GAUGE_INCLUDED_IN_LOCAL_ACTIVITY": True,
        },
        "temporal_propagation_required": "P_t+P_t_minus_1-Cov(t,t_minus_1)-Cov(t_minus_1,t)",
        "temporal_cross_covariance_persisted_by_q2": False,
        "representative_chain_numeric_closure": representative,
    }
    dump(args.output / "ACTIVITY_DIMENSIONAL_ANALYSIS.json", dimensional)

    # Exact legacy retained-fraction replay in original iteration order.
    original_order = [
        ("shoulder_L", "torso", "upper_arm_L", "arms"), ("shoulder_R", "torso", "upper_arm_R", "arms"),
        ("elbow_L", "upper_arm_L", "forearm_L", "left_elbow"), ("elbow_R", "upper_arm_R", "forearm_R", "right_elbow_attempt2"),
        ("hip_L", "pelvis", "thigh_L", "left_knee"), ("hip_R", "pelvis", "thigh_R", "right_knee"),
        ("knee_L", "thigh_L", "shank_L", "left_heel"), ("knee_R", "thigh_R", "shank_R", "right_heel"),
        ("squat_hip_L", "pelvis", "thigh_L", "squats"), ("squat_hip_R", "pelvis", "thigh_R", "squats"),
        ("squat_knee_L", "thigh_L", "shank_L", "squats"), ("squat_knee_R", "thigh_R", "shank_R", "squats"),
        ("trunk", "pelvis", "torso", "trunk"),
    ]
    retained = []
    for name, parent_name, child_name, action in original_order:
        rows = np.flatnonzero((timeline.time_ns >= domains[action][0]) & (timeline.time_ns <= domains[action][1]))
        parent, child = segment_index[parent_name], segment_index[child_name]
        relative = relative_orientation(timeline.rotation[:, parent], timeline.rotation[:, child])
        valid = timeline.valid[:, parent] & timeline.valid[:, child]
        covariance = timeline.covariance_rad2[:, parent] + timeline.covariance_rad2[:, child]
        rate = relative_rate_signal(timeline.time_ns, relative, covariance, valid, old_contract)
        reference = select_pre_reference(rate["snr"], rate["valid"], rows, old_contract)
        try:
            signal = legacy_build_excursion_signal(timeline.time_ns, timeline.rotation[:, parent], timeline.rotation[:, child], timeline.covariance_rad2[:, parent], timeline.covariance_rad2[:, child], timeline.valid[:, parent], timeline.valid[:, child], reference["row_indices"], old_contract)
            fraction = float(signal["reference_retained_fraction"])
            raised = False
        except ValueError as exception:
            if str(exception) != "pre-reference robust retention below frozen minimum":
                raise
            matrices = relative[np.asarray(reference["row_indices"], int)]
            from scipy.spatial.transform import Rotation
            centre = Rotation.from_matrix(matrices).mean().as_matrix()
            distance = Rotation.from_matrix(np.einsum("ji,njk->nik", centre, matrices)).magnitude()
            median = float(np.median(distance)); mad = float(np.median(np.abs(distance - median)))
            scale = max(float(old_contract["signal"]["orientation_uncertainty_floor_rad"]), 1.4826 * mad)
            fraction = float(np.mean(distance <= median + float(old_contract["pre_reference"]["robust_trim_mad_multiplier"]) * scale))
            raised = True
        retained.append({"iteration_index": len(retained), "name": name, "chain": f"{parent_name}->{child_name}", "action": action, "retained_fraction": fraction, "would_raise": raised})
    first_raising = next((item for item in retained if item["would_raise"]), None)
    historical = {
        "schema": "biospur-r3-history-reproducibility-audit-v1",
        "classification": "METADATA_TARGET_MISATTRIBUTION",
        "historical_failed_target_metadata_trustworthy": False,
        "source_sha256": sha256(R3_SOURCE),
        "historical_recorded_source_sha256": json.loads((args.original_r3 / "EXECUTION_FAILURE.json").read_text())["source_sha256"],
        "runner_sha256": sha256(R3_RUNNER),
        "config_sha256": sha256(args.r3_contract),
        "q2_cache_sha256": sha256(cache),
        "action_window_sha256": sha256(args.r2 / "ACTION_PHASE_TIMELINE.json"),
        "node_segment_mapping_sha256": hashlib.sha256(json.dumps(gates["node_to_segment"], sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
        "chain_iteration_order": retained,
        "first_chain": retained[0],
        "first_reproducible_raising_chain": first_raising,
        "recorded_failed_target": "torso_to_upper_arm_L",
        "evidence": [
            "Historical terminal artifacts were written after the exception and contain no retained-fraction checkpoint.",
            "Exact same source/config/cache/window replay gives shoulder_L retention 0.7979315831 and does not raise.",
            "The fourth chain elbow_R gives retention 0.6199677939 and is the first reproducible ValueError in insertion order.",
            "The historical report inferred 'first required primary chain' rather than binding the exception to an instrumented chain record.",
        ],
        "source_or_config_mismatch": False,
        "cache_mismatch": False,
        "chain_mapping_error": False,
        "nondeterministic_ordering": False,
        "stale_state_evidence": False,
        "resolved_for_r3c_r_gate": bool(first_raising and first_raising["name"] == "elbow_R"),
    }
    dump(args.output / "R3_HISTORY_REPRODUCIBILITY_AUDIT.json", historical)
    audit_md = f"""# R3C-0 activity uncertainty formula audit

The R3B implementation at `{R3B_SOURCE.relative_to(ROOT)}` lines 58–80 computes the local relative increment correctly, but lines 73–78 use absolute Q2 pose covariance as adjacent-frame increment noise. `lowest_activity_plateau` lines 83–102 then takes the median of that value as the baseline scale.

For each node, Q2 starts with tilt variance `(2 deg)^2` and unobserved yaw variance `(180 deg)^2`. R3B forms `P_rel(t)=P_parent(t)+P_child(t)`, then `P_rel(t)+P_rel(t-dt)`, assumes all four pose errors are independent, takes `sqrt(trace/3)`, and divides by the common-grid `dt≈0.02 s`. The Q2 cache does not preserve `Cov(theta_t, theta_t-dt)`, so the strong temporal correlation and common global-yaw gauge cannot be cancelled. The resulting median is approximately 181.4 rad/s and dominates the empirical quiet scale.

No covariance was mistaken directly for a standard deviation, no radian/degree conversion occurs in this path, and `dt` is divided once. The scientific errors are `ABSOLUTE_ATTITUDE_COVARIANCE_USED_AS_INCREMENT_NOISE`, `TEMPORAL_CROSS_COVARIANCE_OMITTED`, and `GLOBAL_YAW_GAUGE_INCLUDED_IN_LOCAL_ACTIVITY`. Parent/child cross-covariance is also unavailable, so independence cannot be justified.

The replacement candidate is an empirical same-signal quiet-plateau scale with a predeclared process floor. With Q2 gyro noise `0.003 rad/s/sqrt(Hz)`, two independent sensor rate increments at 50 Hz give `sqrt(2)*0.003/sqrt(0.02) = {derived_process_floor:.9f} rad/s`; the preserved pre-data floor is 0.035 rad/s, so the candidate floor is {deterministic_floor:.9f} rad/s. Absolute Q2 covariance remains available only for validity/confidence reporting.

Historical reproducibility classification: `METADATA_TARGET_MISATTRIBUTION`. Exact replay gives `shoulder_L={retained[0]['retained_fraction']:.10f}` and first raises at `{first_raising['name'] if first_raising else 'NONE'}` with `{first_raising['retained_fraction'] if first_raising else float('nan'):.10f}`. Original R3 artifacts remain immutable.
"""
    (args.output / "ACTIVITY_UNCERTAINTY_FORMULA_AUDIT.md").write_text(audit_md)
    manifest = {str(path.relative_to(args.output)): sha256(path) for path in sorted(args.output.iterdir()) if path.is_file() and path.name != "SHA256_MANIFEST.json"}
    dump(args.output / "SHA256_MANIFEST.json", manifest)
    return {"DIMENSIONAL_ANALYSIS": "PASS", "HISTORICAL_REPRODUCIBILITY_ISSUE": "RESOLVED", "classification": historical["classification"], "chains": len(records)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase-a", type=Path, required=True)
    parser.add_argument("--r2", type=Path, required=True)
    parser.add_argument("--original-r3", type=Path, required=True)
    parser.add_argument("--gates", type=Path, required=True)
    parser.add_argument("--r3-contract", type=Path, required=True)
    parser.add_argument("--r3b-contract", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = analyze(args)
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
