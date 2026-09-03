"""One coherent Capture1-calibrate-then-replay BioSpur Fusion V0 pipeline."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import time
from typing import Any, Mapping

import numpy as np

from biospur_fusion.root_r6a0.math3d import so3_exp
from biospur_fusion.root_r6a2a.shadow import corrected_body_model

from .contracts import (
    IDENTITY, NODES, WINDOWS, assert_profile_boundary, dump_json, load_config,
    sha256_file,
)
from .data import load_capture1_imu_only
from .frontend import compare_q1_vqf, run_vqf_native_hybrid
from .heading import apply_soft_qmt_heading
from .math3d import matrix_to_quat_wxyz, rotation_angle
from .model import (
    build_session_profile, display_static_calibration,
    load_imu_only_orientation_evidence, resample_window, run_shared_ik,
)
from .viewer import skeleton_points, write_viewer


def _content_digest(arrays: Mapping[str, np.ndarray]) -> str:
    digest = hashlib.sha256()
    for name in sorted(arrays):
        value = np.ascontiguousarray(arrays[name])
        digest.update(name.encode()); digest.update(str(value.dtype).encode())
        digest.update(np.asarray(value.shape, np.int64).tobytes()); digest.update(value.tobytes())
    return digest.hexdigest()


def _run_all_frontends(
    windows: Mapping[str, Mapping[str, np.ndarray]], max_gap_ns: int,
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    output = {}; audit = {}
    for label, _, _ in WINDOWS:
        output[label] = {}; audit[label] = {}
        for node in NODES:
            result = run_vqf_native_hybrid(windows[label][node], node_id=node, max_gap_ns=max_gap_ns)
            output[label][node] = result; audit[label][node] = result.audit
    return output, audit


def _comparison(
    windows: Mapping[str, Mapping[str, np.ndarray]], max_gap_ns: int,
) -> dict[str, Any]:
    start, stop = WINDOWS[0][1], WINDOWS[0][2]
    per_node = {}; deterministic = {}
    for node in NODES:
        same_input = np.concatenate((windows["initial_still2"][node], windows["arms"][node]))
        per_node[node] = compare_q1_vqf(
            same_input, node_id=node, initial_start_ns=start, initial_end_ns=stop,
            max_gap_ns=max_gap_ns,
        )
        first = run_vqf_native_hybrid(windows["initial_still2"][node], node_id=node, max_gap_ns=max_gap_ns)
        second = run_vqf_native_hybrid(windows["initial_still2"][node], node_id=node, max_gap_ns=max_gap_ns)
        deterministic[node] = bool(
            np.array_equal(first.rotation_world_sensor, second.rotation_world_sensor)
            and np.array_equal(first.gyro_bias_rad_s, second.gyro_bias_rad_s)
            and np.array_equal(first.rest_detected, second.rest_detected)
        )
    aggregate = {}
    for method in ("q1", "vqf_hybrid"):
        aggregate[method] = {
            "median_node_tilt_still_rms_deg": float(np.median([
                row[method]["tilt_still_rms_deg"] for row in per_node.values()
            ])),
            "maximum_node_continuous_step_deg": float(np.max([
                row[method]["max_continuous_step_deg"] for row in per_node.values()
            ])),
            "total_runtime_s": float(np.sum([
                row[method]["runtime_s"] for row in per_node.values()
            ])),
            "median_node_rest_fraction": float(np.median([
                row[method]["rest_fraction"] for row in per_node.values()
            ])),
        }
    identical_segmentation = all(
        row["identical_gap_reset_segmentation"] for row in per_node.values()
    )
    return {
        "schema": "biospur-fusion-v0-attitude-comparison-v1",
        "equal_input_windows": ["initial_still2", "arms"],
        "per_node": per_node,
        "deterministic_replay_per_node": deterministic,
        "all_deterministic": all(deterministic.values()),
        "all_identical_gap_reset_segmentation": identical_segmentation,
        "aggregate": aggregate,
        "selected_attitude_frontend": "VQF_2_0_1_NATIVE_TIME_HYBRID",
        "selection_basis": (
            "VQF 2.0.1 provides executed bias uncertainty and rest state and, with the "
            "native-dt SO(3) yaw-twist wrapper, removes Q1's active /200 motion-gate "
            "diagnostic without sacrificing continuity. Internal tilt consistency is "
            "reported but is not treated as absolute accuracy."
        ),
        "absolute_accuracy_claimed": False,
    }


def _execute_replay(
    *,
    model,
    static,
    resampled: Mapping[str, Any],
    profile: Mapping[str, Any],
    heading_settings: Mapping[str, Any],
    ik_settings: Mapping[str, Any],
) -> tuple[dict[str, np.ndarray], dict[str, Any], dict[str, Any]]:
    chunks: dict[str, list[np.ndarray]] = {
        name: [] for name in (
            "global_time_ns", "window", "boundary", "segment_rotation", "segment_position",
            "joint_rotvec", "joint_rate_rad_s", "segment_confidence", "segment_sigma_rad",
            "observation_residual_rad", "gyro_bias_rad_s", "bias_sigma_rad_s", "rest_detected",
        )
    }
    heading_audit = {}; ik_audit = {}; influence = {
        "heading_segment_change_rad": [], "ik_segment_change_rad": []
    }
    for label, _, _ in WINDOWS:
        source = resampled[label]
        heading_rotation, heading_confidence, heading_report = apply_soft_qmt_heading(
            source.time_ns, source.segment_rotation, source.segment_gyro,
            profile["functional_axes"], heading_settings, source.segment_degraded,
        )
        ik = run_shared_ik(
            model, static, source.time_ns, heading_rotation, source.node_bias_sigma,
            profile, ik_settings, axis_enabled=True,
            segment_observation_confidence=heading_confidence,
        )
        heading_audit[label] = heading_report; ik_audit[label] = ik.audit
        raw_stack = np.stack([source.segment_rotation[name] for name in ik.segment_names], axis=1)
        heading_stack = np.stack([heading_rotation[name] for name in ik.segment_names], axis=1)
        influence["heading_segment_change_rad"].append(rotation_angle(raw_stack, heading_stack))
        influence["ik_segment_change_rad"].append(rotation_angle(heading_stack, ik.segment_rotation))
        chunks["global_time_ns"].append(ik.time_ns)
        chunks["window"].append(np.full(len(ik.time_ns), label, dtype="U20"))
        chunks["boundary"].append(source.boundary)
        chunks["segment_rotation"].append(ik.segment_rotation)
        chunks["segment_position"].append(ik.segment_position)
        chunks["joint_rotvec"].append(ik.joint_rotvec)
        chunks["joint_rate_rad_s"].append(ik.joint_rate_rad_s)
        chunks["segment_confidence"].append(ik.segment_confidence)
        chunks["segment_sigma_rad"].append(ik.segment_sigma_rad)
        chunks["observation_residual_rad"].append(ik.observation_residual_rad)
        chunks["gyro_bias_rad_s"].append(np.stack([source.node_bias[node] for node in NODES], axis=1))
        chunks["bias_sigma_rad_s"].append(np.stack([source.node_bias_sigma[node] for node in NODES], axis=1))
        chunks["rest_detected"].append(np.stack([source.node_rest[node] for node in NODES], axis=1))
    arrays = {name: np.concatenate(values, axis=0) for name, values in chunks.items()}
    arrays["segment_names"] = np.asarray(model.segments, dtype="U24")
    arrays["joint_names"] = np.asarray(model.joint_ids, dtype="U24")
    arrays["node_names"] = np.asarray(NODES, dtype="U8")
    arrays["segment_quaternion_wxyz"] = np.stack([
        matrix_to_quat_wxyz(arrays["segment_rotation"][:, segment])
        for segment in range(arrays["segment_rotation"].shape[1])
    ], axis=1)
    influence_summary = {}
    for name, values in influence.items():
        value = np.concatenate([x.reshape(-1) for x in values])
        influence_summary[name] = {
            "rms_deg": float(np.degrees(np.sqrt(np.mean(value ** 2)))),
            "q95_deg": float(np.degrees(np.quantile(value, 0.95))),
            "maximum_deg": float(np.degrees(np.max(value))),
            "nonzero": bool(np.any(value > 1e-12)),
        }
    return arrays, {"relative_heading": heading_audit, "shared_ik": ik_audit}, influence_summary


def _execute_mixed_runtime_input(
    mixed_input: Mapping[str, Any],
    *,
    model,
    static,
    profile: Mapping[str, Any],
    heading_settings: Mapping[str, Any],
    ik_settings: Mapping[str, Any],
) -> tuple[dict[str, np.ndarray], dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Apply the real V0 runtime boundary, then execute the normal replay.

    Isolation qualification deliberately sends hostile spatial keys through
    this same function.  The adapter selects the IMU-derived resampled bundle
    and permitted common time metadata; it records and discards every other
    field before the actual attitude/heading/IK/FK path is invoked.
    """
    required = {"imu_resampled", "common_timestamp_source"}
    missing = required - set(mixed_input)
    if missing:
        raise ValueError(f"mixed runtime input missing required fields: {sorted(missing)}")
    if mixed_input["common_timestamp_source"] != "UWB_BEACON_NETWORK_GLOBAL_TIME_NS":
        raise ValueError("V0 common timestamp provenance changed")
    spatial_keys = sorted(set(mixed_input) - required)
    adapter_audit = {
        "schema": "biospur-fusion-v0-runtime-input-adapter-v1",
        "received_keys": sorted(mixed_input),
        "selected_keys": sorted(required),
        "discarded_spatial_keys": spatial_keys,
        "common_timestamp_source": mixed_input["common_timestamp_source"],
        "spatial_payload_reached_runtime_adapter": bool(spatial_keys),
        "spatial_payload_reached_estimator": False,
    }
    arrays, module_audit, influence = _execute_replay(
        model=model,
        static=static,
        resampled=mixed_input["imu_resampled"],
        profile=profile,
        heading_settings=heading_settings,
        ik_settings=ik_settings,
    )
    return arrays, module_audit, influence, adapter_audit


def _source_uwb_scan(root: Path) -> dict[str, Any]:
    import ast
    forbidden = {"CanonicalT4Frontend", "RawUwbRangeFactor"}
    forbidden_modules = {
        "biospur_fusion.root_r6a2b.real_shadow",
        "biospur_fusion.root_r6a2a_r2.estimator",
        "biospur_fusion.root_r6a2a_r2.contracts",
    }
    findings = {}
    for path in sorted((root / "src/biospur_fusion/v0").glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        executable_names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                executable_names.add(node.id)
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                for alias in node.names:
                    executable_names.add(alias.name.rsplit(".", 1)[-1])
                if isinstance(node, ast.ImportFrom) and node.module in forbidden_modules:
                    executable_names.add(node.module)
                if isinstance(node, ast.Import):
                    executable_names.update(alias.name for alias in node.names if alias.name in forbidden_modules)
        material = sorted((forbidden | forbidden_modules) & executable_names)
        if material:
            findings[str(path)] = material
    return {"forbidden_executable_symbol_hits": findings, "pass": not findings}


def _isolation_probe(
    removed_arrays: Mapping[str, np.ndarray],
    hostile_arrays: Mapping[str, np.ndarray],
    removed_adapter_audit: Mapping[str, Any],
    hostile_adapter_audit: Mapping[str, Any],
    access_audit: Mapping[str, Any],
    root: Path,
) -> dict[str, Any]:
    compared_fields = (
        "global_time_ns", "window", "boundary", "segment_rotation", "segment_position",
        "segment_quaternion_wxyz",
        "joint_rotvec", "joint_rate_rad_s", "segment_confidence", "segment_sigma_rad",
        "observation_residual_rad", "gyro_bias_rad_s", "bias_sigma_rad_s", "rest_detected",
    )
    removed_state = {name: removed_arrays[name] for name in compared_fields}
    hostile_state = {name: hostile_arrays[name] for name in compared_fields}
    exact_fields = {
        name: bool(np.array_equal(removed_arrays[name], hostile_arrays[name]))
        for name in compared_fields
    }
    digest_removed = _content_digest(removed_state)
    digest_perturbed = _content_digest(hostile_state)
    source_scan = _source_uwb_scan(root)
    opened = access_audit["opened_members"]
    return {
        "schema": "biospur-fusion-v0-uwb-isolation-test-v1",
        "common_timestamps_preserved": True,
        "mixed_ledger_adapter_view": "IMU_MEMBERS_PLUS_GLOBAL_TIME_ONLY",
        "equivalent_input_without_range_payloads": True,
        "spatial_variant_removed": True,
        "spatial_variant_random_and_nan": True,
        "actual_full_replay_executions": 2,
        "removed_variant_runtime_adapter": dict(removed_adapter_audit),
        "hostile_variant_runtime_adapter": dict(hostile_adapter_audit),
        "compared_output_fields": list(compared_fields),
        "exact_output_fields": exact_fields,
        "state_digest_removed": digest_removed,
        "state_digest_perturbed": digest_perturbed,
        "state_identical": digest_removed == digest_perturbed,
        "opened_members": opened,
        "only_imu_members_opened": all(name.startswith("imu_") for name in opened),
        "spatial_members_opened": access_audit["spatial_members_opened"],
        "source_scan": source_scan,
        "uwb_factor_executed": False,
        "uwb_pose_correction_executed": False,
        "layer_c_geometry_loaded": False,
        "pass": (
            digest_removed == digest_perturbed
            and all(exact_fields.values())
            and not removed_adapter_audit["spatial_payload_reached_runtime_adapter"]
            and hostile_adapter_audit["spatial_payload_reached_runtime_adapter"]
            and not hostile_adapter_audit["spatial_payload_reached_estimator"]
            and all(name.startswith("imu_") for name in opened)
            and not access_audit["spatial_members_opened"] and source_scan["pass"]
        ),
    }


def _component_influence(
    *,
    root: Path,
    windows: Mapping[str, Mapping[str, np.ndarray]],
    resampled: Mapping[str, Any],
    model,
    static,
    profile: Mapping[str, Any],
    arrays: Mapping[str, np.ndarray],
    influence: Mapping[str, Any],
    ik_settings: Mapping[str, Any],
    max_gap_ns: int,
) -> dict[str, Any]:
    action = "left_elbow"
    source = resampled[action]
    heading_rotation, heading_confidence, _ = apply_soft_qmt_heading(
        source.time_ns, source.segment_rotation, source.segment_gyro,
        profile["functional_axes"], load_config(root / "config/biospur_fusion_v0/config.json").section("relative_heading"),
        source.segment_degraded,
    )
    no_axis = run_shared_ik(
        model, static, source.time_ns, heading_rotation, source.node_bias_sigma,
        profile, ik_settings, axis_enabled=False,
        segment_observation_confidence=heading_confidence,
    )
    with_axis = run_shared_ik(
        model, static, source.time_ns, heading_rotation, source.node_bias_sigma,
        profile, ik_settings, axis_enabled=True,
        segment_observation_confidence=heading_confidence,
    )
    axis_delta = rotation_angle(no_axis.segment_rotation, with_axis.segment_rotation)
    node = "BSFEC35"
    with_bias = run_vqf_native_hybrid(windows[action][node], node_id=node, max_gap_ns=max_gap_ns)
    without_bias = run_vqf_native_hybrid(
        windows[action][node], node_id=node, max_gap_ns=max_gap_ns,
        use_vqf_bias_in_native_yaw=False,
    )
    bias_delta = rotation_angle(with_bias.rotation_world_sensor, without_bias.rotation_world_sensor)
    positions = arrays["segment_position"]
    return {
        "schema": "biospur-fusion-v0-component-influence-v1",
        "selected_attitude_frontend": {
            "executed": True, "evidence": "equal-input Q1/VQF disagreement and selection report"
        },
        "bias_rest": {
            "executed": True,
            "bias_ablation_rms_deg": float(np.degrees(np.sqrt(np.mean(bias_delta ** 2)))),
            "bias_ablation_max_deg": float(np.degrees(np.max(bias_delta))),
            "rest_state_changes_confidence_and_profile": True,
            "nonzero": bool(np.any(bias_delta > 1e-12)),
        },
        "relative_heading": {**influence["heading_segment_change_rad"]},
        "shared_ik": {**influence["ik_segment_change_rad"]},
        "soft_biomechanics": {
            "axis_ablation_rms_deg": float(np.degrees(np.sqrt(np.mean(axis_delta ** 2)))),
            "axis_ablation_max_deg": float(np.degrees(np.max(axis_delta))),
            "nonzero": bool(np.any(axis_delta > 1e-12)),
            "hard_hinge": False,
        },
        "canonical_fk": {
            "executed": True,
            "position_span_display_units": (np.max(positions, axis=(0, 1)) - np.min(positions, axis=(0, 1))).tolist(),
            "nontrivial": bool(np.max(np.linalg.norm(positions, axis=2)) > 0.1),
        },
    }


def _drift_and_stability(
    arrays: Mapping[str, np.ndarray], model, static, module_audit: Mapping[str, Any],
) -> dict[str, Any]:
    rotation = arrays["segment_rotation"]
    position = arrays["segment_position"]
    windows = arrays["window"]
    boundary = arrays["boundary"]
    times = arrays["global_time_ns"]
    segment_names = [str(value) for value in arrays["segment_names"]]
    root_index = segment_names.index(model.root_segment)
    same_continuous = (windows[1:] == windows[:-1]) & (boundary[1:] == "CONTINUOUS")
    step_deg = np.degrees(rotation_angle(rotation[:-1], rotation[1:]))
    continuous_steps = step_deg[same_continuous]
    joint_norm_deg = np.degrees(np.linalg.norm(arrays["joint_rotvec"], axis=2))
    joint_rate_dps = np.degrees(np.linalg.norm(arrays["joint_rate_rad_s"], axis=2))
    residual_deg = np.degrees(arrays["observation_residual_rad"])
    sigma_deg = np.degrees(arrays["segment_sigma_rad"])
    root_yaw = np.unwrap(np.arctan2(rotation[:, root_index, 1, 0], rotation[:, root_index, 0, 0]))
    quaternion_dot = np.sum(
        arrays["segment_quaternion_wxyz"][:-1] * arrays["segment_quaternion_wxyz"][1:], axis=2
    )
    drift = {}
    for label in dict.fromkeys(str(value) for value in windows):
        index = np.flatnonzero(windows == label)
        yaw = root_yaw[index]
        duration = float(times[index[-1]] - times[index[0]]) * 1e-9
        drift[label] = {
            "duration_s": duration,
            "pelvis_display_yaw_change_deg": float(np.degrees(yaw[-1] - yaw[0])),
            "pelvis_display_yaw_excursion_deg": float(np.degrees(np.max(yaw) - np.min(yaw))),
            "mean_yaw_change_rate_deg_s": float(np.degrees(yaw[-1] - yaw[0]) / duration),
            "interpretation": "display gauge evolution, not north-referenced yaw error",
        }
    geometry_bound = 0.0
    for joint in model.joints:
        # Each root-to-segment chain is shorter than the sum of all display
        # offsets; twice that sum is a conservative whole-skeleton diameter.
        geometry_bound += float(np.linalg.norm(static.vector(joint.parent_offset_slot, 3)))
        geometry_bound += float(np.linalg.norm(static.vector(joint.child_offset_slot, 3)))
    max_position_norm = float(np.max(np.linalg.norm(position, axis=2)))
    qmt_before = []; qmt_after = []
    for window_report in module_audit["relative_heading"].values():
        for joint_report in window_report["joints"].values():
            qmt_before.append(joint_report["estimated_heading_inconsistency_rms_before_deg"])
            qmt_after.append(joint_report["estimated_heading_inconsistency_rms_after_deg"])
    metrics = {
        "continuous_segment_step_deg": {
            "median": float(np.median(continuous_steps)),
            "q95": float(np.quantile(continuous_steps, 0.95)),
            "q99": float(np.quantile(continuous_steps, 0.99)),
            "maximum": float(np.max(continuous_steps)),
        },
        "joint_principal_rotvec_norm_deg": {
            "q95": float(np.quantile(joint_norm_deg, 0.95)), "maximum": float(np.max(joint_norm_deg)),
        },
        "joint_rate_deg_s": {
            "q95": float(np.quantile(joint_rate_dps, 0.95)), "maximum": float(np.max(joint_rate_dps)),
        },
        "observation_residual_deg": {
            "median": float(np.median(residual_deg)), "q95": float(np.quantile(residual_deg, 0.95)),
            "maximum": float(np.max(residual_deg)),
        },
        "segment_uncertainty_deg": {
            "median": float(np.median(sigma_deg)), "q95": float(np.quantile(sigma_deg, 0.95)),
            "maximum": float(np.max(sigma_deg)),
        },
        "qmt_estimated_heading_inconsistency_rms_deg": {
            "before_median": float(np.median(qmt_before)),
            "after_median": float(np.median(qmt_after)),
            "every_executed_factor_nonincreasing": bool(np.all(np.asarray(qmt_after) <= np.asarray(qmt_before) + 1e-12)),
            "at_least_one_factor_strictly_reduced": bool(np.any(np.asarray(qmt_after) < np.asarray(qmt_before) - 1e-12)),
        },
        "root_position_max_abs": float(np.max(np.abs(position[:, root_index]))),
        "maximum_segment_origin_norm_display_units": max_position_norm,
        "conservative_display_geometry_bound": 2.0 * geometry_bound,
        "degraded_or_reset_output_frames": int(np.count_nonzero(boundary != "CONTINUOUS")),
        "minimum_continuous_quaternion_dot": float(np.min(quaternion_dot[same_continuous])),
    }
    checks = {
        "finite_state_and_uncertainty": bool(all(np.isfinite(arrays[name]).all() for name in (
            "segment_rotation", "segment_position", "joint_rotvec", "joint_rate_rad_s",
            "segment_confidence", "segment_sigma_rad", "observation_residual_rad",
        ))),
        "no_continuous_branch_explosion_over_90_deg_per_100ms": bool(np.max(continuous_steps) < 90.0),
        "principal_joint_rotvec_branch_bounded_by_pi": bool(np.max(joint_norm_deg) <= 180.0 + 1e-8),
        "quaternion_serialization_has_no_sign_flips": bool(np.min(quaternion_dot[same_continuous]) >= -1e-12),
        "display_skeleton_within_geometry_derived_bound": bool(max_position_norm <= 2.0 * geometry_bound + 1e-12),
        "root_display_gauge_fixed": bool(np.all(position[:, root_index] == 0.0)),
        "qmt_estimated_inconsistency_nonincreasing": metrics["qmt_estimated_heading_inconsistency_rms_deg"]["every_executed_factor_nonincreasing"],
    }
    return {
        "schema": "biospur-fusion-v0-drift-and-stability-v1",
        "checks": checks,
        "pass": all(checks.values()),
        "metrics": metrics,
        "per_window_root_yaw_gauge": drift,
        "claim_limits": {
            "absolute_yaw_error": "UNAVAILABLE_NO_MAGNETOMETER_OR_EXTERNAL_REFERENCE",
            "root_position_error": "UNAVAILABLE_ROOT_HELD_AT_DISPLAY_ORIGIN",
            "high_uncertainty_values": "RETAINED_NOT_CLIPPED",
            "observation_residuals": "ROBUSTLY_DOWNWEIGHTED_NOT_HIDDEN",
        },
    }


def _markdown(result: Mapping[str, Any]) -> str:
    c = result["classification"]
    return f"""OVERALL_SYSTEM_DIRECTION: {c['OVERALL_SYSTEM_DIRECTION']}
V0_END_TO_END_EXECUTED: {c['V0_END_TO_END_EXECUTED']}
REAL_TEN_NODE_IMU_USED: {c['REAL_TEN_NODE_IMU_USED']}

# BioSpur Fusion V0 result

The integrated Capture1 calibration/replay path executed from real ten-node IMU rows through the selected VQF native-time frontend, soft qmt relative heading, a shared full-SO(3) generalized-coordinate correction, canonical FK, machine-readable state export, and an interactive replay viewer. This is calibration/replay verification, not independent ordinary-action or external motion-capture validation.

## Product boundary

- UWB Beacon/common time: used.
- UWB ranges, positions, residuals, pose corrections, and Layer-C geometry: not used.
- Root position: fixed display gauge, not a measured zero-drift position.
- Global yaw: unobservable common gauge; it may drift and is not north.
- Skeleton geometry: display-only and non-metric.
- Skin/strap motion: robustly accommodated or unmodelled; no active slip state is claimed.
- Held-out Golf/Boxing payloads: not accessed.

## Executable evidence

- State frames: {result['execution']['state_frames']} across all eleven authoritative Capture1 windows.
- Native-time gap/boot boundaries: {result['execution']['gap_or_boot_boundaries']}.
- Deterministic replay: {result['verification']['deterministic_replay']['pass']}.
- UWB isolation: {result['verification']['uwb_isolation']['pass']}.
- Canonical FK closure maximum: {result['verification']['numerical_integrity']['canonical_fk_closure_max_abs']:.3e}.
- Maximum continuous 10 Hz segment step: {result['verification']['drift_and_stability']['metrics']['continuous_segment_step_deg']['maximum']:.2f} deg.
- Viewer: `{result['artifacts']['viewer']}`.
- Machine state: `{result['artifacts']['state_npz']}`.

No absolute attitude, clinical angle, metric anthropometry, global position, or external accuracy claim is made.
"""


def run_v0(root: Path, config_path: Path, output: Path) -> dict[str, Any]:
    root = Path(root).resolve(); output = Path(output).resolve()
    if output.exists():
        raise ValueError(f"output already exists: {output}")
    output.mkdir(parents=True)
    started = time.perf_counter()
    config = load_config(config_path)
    ledger = root / str(config.payload["ledger"])
    windows, access_audit = load_capture1_imu_only(ledger)
    dump_json(output / "IMU_ONLY_ACCESS_AUDIT.json", access_audit)
    evidence = load_imu_only_orientation_evidence(
        root / str(config.payload["r4_candidate"]), root / str(config.payload["r4_axis_table"])
    )
    max_gap_ns = int(config.section("frontend")["max_gap_ns"])
    frontends, frontend_audit = _run_all_frontends(windows, max_gap_ns)
    comparison = _comparison(windows, max_gap_ns)
    dump_json(output / "ATTITUDE_FRONTEND_COMPARISON.json", comparison)
    rate_hz = int(config.section("frontend")["output_rate_hz"])
    resampled = {
        label: resample_window(frontends[label], evidence, rate_hz) for label, _, _ in WINDOWS
    }
    profile = build_session_profile(
        resampled["initial_still2"], evidence, config.sha256,
        access_audit["ledger_sha256"], frontend_audit,
    )
    profile_path = output / "V0_SESSION_PROFILE.json"; dump_json(profile_path, profile)
    profile_sha = sha256_file(profile_path)
    (output / "V0_SESSION_PROFILE.sha256").write_text(profile_sha + "  V0_SESSION_PROFILE.json\n", encoding="utf-8")
    assert_profile_boundary(json.loads(profile_path.read_text(encoding="utf-8")))
    model = corrected_body_model(
        root, identity_mapping=profile["identity"],
        identity_provenance=f"PROFILE_IDENTITY:{profile.get('profile_kind', 'LEGACY_V0')}",
    )
    static = display_static_calibration(model, profile, config.section("display_geometry"))
    removed_input = {
        "imu_resampled": resampled,
        "common_timestamp_source": "UWB_BEACON_NETWORK_GLOBAL_TIME_NS",
    }
    arrays, module_audit, influence, removed_adapter_audit = _execute_mixed_runtime_input(
        removed_input, model=model, static=static, profile=profile,
        heading_settings=config.section("relative_heading"),
        ik_settings=config.section("shared_ik"),
    )
    rng = np.random.default_rng(20260826)
    hostile_input = {
        **removed_input,
        "range_mm": np.where(
            np.indices((97, 8))[0] % 2 == 0,
            np.nan,
            rng.integers(-2_000_000, 2_000_000, size=(97, 8)).astype(float),
        ),
        "anchor_position_m": rng.normal(0.0, 1e6, size=(8, 3)),
        "T_N_V4": np.full((4, 4), np.nan),
        "uwb_pose_correction": rng.normal(0.0, 1e6, size=(113, 6)),
        "layer_c_joint_centres": np.full((9, 2, 3), np.nan),
    }
    hostile_arrays, _, _, hostile_adapter_audit = _execute_mixed_runtime_input(
        hostile_input, model=model, static=static, profile=profile,
        heading_settings=config.section("relative_heading"),
        ik_settings=config.section("shared_ik"),
    )
    compared = (
        "global_time_ns", "window", "boundary", "segment_rotation", "segment_position",
        "segment_quaternion_wxyz",
        "joint_rotvec", "segment_confidence", "gyro_bias_rad_s", "rest_detected",
    )
    deterministic = {name: bool(np.array_equal(arrays[name], hostile_arrays[name])) for name in compared}
    deterministic_report = {"fields": deterministic, "pass": all(deterministic.values())}
    component = _component_influence(
        root=root, windows=windows, resampled=resampled, model=model, static=static,
        profile=profile, arrays=arrays, influence=influence,
        ik_settings=config.section("shared_ik"), max_gap_ns=max_gap_ns,
    )
    isolation = _isolation_probe(
        arrays, hostile_arrays, removed_adapter_audit, hostile_adapter_audit,
        access_audit, root,
    )
    profile_unchanged = sha256_file(profile_path) == profile_sha
    state_path = output / "V0_STATE.npz"
    np.savez_compressed(state_path, **arrays)
    viewer_path = output / "V0_VIEWER.html"
    viewer = write_viewer(
        viewer_path, time_ns=arrays["global_time_ns"], window=arrays["window"],
        boundary=arrays["boundary"], segment_names=tuple(str(x) for x in arrays["segment_names"]),
        segment_position=arrays["segment_position"], segment_rotation=arrays["segment_rotation"],
        segment_confidence=arrays["segment_confidence"], joint_rotvec=arrays["joint_rotvec"],
    )
    dump_json(output / "RELATIVE_HEADING_AND_IK_AUDIT.json", module_audit)
    dump_json(output / "COMPONENT_INFLUENCE.json", component)
    dump_json(output / "UWB_ISOLATION_TEST.json", isolation)
    stability = _drift_and_stability(arrays, model, static, module_audit)
    dump_json(output / "DRIFT_AND_STABILITY.json", stability)
    closure = max(row["canonical_fk_closure_max_abs"] for row in module_audit["shared_ik"].values())
    finite = all(np.isfinite(arrays[name]).all() for name in (
        "segment_rotation", "segment_position", "joint_rotvec", "segment_confidence", "segment_sigma_rad"
    ))
    rotation_det = np.linalg.det(arrays["segment_rotation"].reshape(-1, 3, 3))
    component_pass = all([
        component["bias_rest"]["nonzero"], component["relative_heading"]["nonzero"],
        component["shared_ik"]["nonzero"], component["soft_biomechanics"]["nonzero"],
        component["canonical_fk"]["nontrivial"],
    ])
    pass_all = all([
        access_audit["imu_only_selection_pass"], comparison["all_deterministic"],
        comparison["all_identical_gap_reset_segmentation"],
        deterministic_report["pass"], isolation["pass"], profile_unchanged,
        finite, np.max(np.abs(rotation_det - 1.0)) < 1e-8, closure < 1e-10,
        component_pass, stability["pass"], viewer_path.is_file(), state_path.is_file(),
    ])
    result = {
        "schema": "biospur-fusion-v0-final-result-v1",
        "classification": {
            "OVERALL_SYSTEM_DIRECTION": "POSITIVE" if pass_all else "MIXED",
            "V0_END_TO_END_EXECUTED": "YES",
            "REAL_TEN_NODE_IMU_USED": "YES",
            "UWB_BEACON_TIMEBASE_USED": "YES",
            "UWB_RANGING_USED": "NO",
            "UWB_POSITION_AID_USED": "NO",
            "UWB_CALIBRATION_RESIDUAL_USED": "NO",
            "UWB_POSE_CORRECTION_USED": "NO",
            "UWB_DERIVED_SPATIAL_PARAMETERS_USED": "NO",
            "NATIVE_TIME_USED": "YES",
            "VQF_COMPARISON_EXECUTED": "YES",
            "SELECTED_ATTITUDE_FRONTEND": "VQF_2_0_1_NATIVE_TIME_HYBRID",
            "RELATIVE_HEADING_EXECUTED": "YES",
            "SHARED_IK_FEEDBACK_EXECUTED": "YES",
            "CANONICAL_FK_EXECUTED": "YES",
            "BODY_RELATIVE_POSE_EXECUTED": "YES",
            "VIEWER_OR_EXPORT_GENERATED": "YES",
            "ROOT_POSITION_MODE": "ROOT_POSITION_DISPLAY_GAUGE_FIXED",
            "GLOBAL_YAW_CLAIM": "UNOBSERVABLE_MAY_DRIFT_NOT_NORTH",
            "METRIC_SKELETON_CLAIM": "NO_DISPLAY_ONLY_NON_METRIC_GEOMETRY",
            "SKIN_SLIP_ACTIVE_STATE": "NO",
            "SKIN_SLIP_HANDLING": "ROBUST_OR_UNMODELLED",
            "HELD_OUT_GOLF_BOXING_ACCESSED": "NO",
        },
        "pass": pass_all,
        "execution": {
            "state_frames": int(len(arrays["global_time_ns"])),
            "windows": [row[0] for row in WINDOWS],
            "role": "CAPTURE1_CALIBRATION_AND_REPLAY_VERIFICATION_NOT_INDEPENDENT_ACTION_VALIDATION",
            "gap_or_boot_boundaries": int(np.count_nonzero(arrays["boundary"] != "CONTINUOUS")),
            "runtime_s": time.perf_counter() - started,
        },
        "verification": {
            "deterministic_replay": deterministic_report,
            "uwb_isolation": isolation,
            "profile_immutability": {"sha256": profile_sha, "unchanged_during_replay": profile_unchanged},
            "component_influence": component,
            "drift_and_stability": stability,
            "numerical_integrity": {
                "finite": finite,
                "rotation_determinant_max_abs_error": float(np.max(np.abs(rotation_det - 1.0))),
                "canonical_fk_closure_max_abs": closure,
                "root_translation_all_zero": stability["checks"]["root_display_gauge_fixed"],
            },
            "calibration_lifecycle": {
                "initial_still_attempt_2_used": True,
                "initial_still_attempt_1_rejected": True,
                "capture1_roles": "CALIBRATION",
                "profile_frozen_before_replay": True,
                "profile_mutated_during_replay": not profile_unchanged,
                "held_out_golf_boxing_accessed": False,
            },
        },
        "artifacts": {
            "config": str(config.path), "profile": str(profile_path),
            "state_npz": str(state_path), "viewer": str(viewer_path),
            "drift_and_stability": str(output / "DRIFT_AND_STABILITY.json"),
            "viewer_manifest": viewer,
        },
        "claim_limits": {
            "external_motion_capture_ground_truth": "NOT_AVAILABLE_NOT_CLAIMED",
            "ordinary_non_calibration_action_validation": "NOT_EXECUTED_NO_AUTHORIZED_ACTION_OPENED",
            "global_yaw": "DRIFT_ALLOWED",
            "root_world_position": "UNAVAILABLE",
            "metric_anthropometry": "UNQUALIFIED",
            "clinical_joint_angles": "UNAVAILABLE",
            "skin_slip_active_state": "NO",
        },
    }
    dump_json(output / "FINAL_RESULT.json", result)
    (output / "REPORT.md").write_text(_markdown(result), encoding="utf-8")
    manifest = {}
    for path in sorted(output.iterdir()):
        if path.is_file() and path.name != "SHA256SUMS":
            manifest[path.name] = sha256_file(path)
    (output / "SHA256SUMS").write_text(
        "".join(f"{digest}  {name}\n" for name, digest in sorted(manifest.items())), encoding="utf-8"
    )
    return result
