"""Capture1 profile calibration and minimal shared full-SO(3) IK/FK."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

import numpy as np

from biospur_fusion.root_r6a0.body import KeyframeState, StaticCalibration
from biospur_fusion.root_r6a0.contracts import CalibrationSlot, CalibrationStatus
from biospur_fusion.root_r6a0.math3d import so3_exp, so3_log
from biospur_fusion.root_r6a2a.shadow import corrected_body_model

from .contracts import HARDWARE_FAMILY, IDENTITY, NODES, WINDOWS, assert_profile_boundary, sha256_file
from .frontend import AttitudeTimeline
from .math3d import proper_mean, rotation_angle


@dataclass(frozen=True)
class ResampledWindow:
    time_ns: np.ndarray
    segment_rotation: Mapping[str, np.ndarray]
    segment_gyro: Mapping[str, np.ndarray]
    node_bias: Mapping[str, np.ndarray]
    node_bias_sigma: Mapping[str, np.ndarray]
    node_rest: Mapping[str, np.ndarray]
    segment_degraded: Mapping[str, np.ndarray]
    boundary: np.ndarray


@dataclass(frozen=True)
class SharedIkResult:
    time_ns: np.ndarray
    segment_names: tuple[str, ...]
    joint_names: tuple[str, ...]
    segment_rotation: np.ndarray
    segment_position: np.ndarray
    joint_rotvec: np.ndarray
    joint_rate_rad_s: np.ndarray
    segment_confidence: np.ndarray
    segment_sigma_rad: np.ndarray
    observation_residual_rad: np.ndarray
    canonical_fk_closure_max: float
    audit: dict[str, Any]


def _profile_audits_without_runtime(value: Any) -> Any:
    """Return deterministic audit provenance suitable for a frozen profile."""
    if isinstance(value, Mapping):
        return {
            str(key): _profile_audits_without_runtime(item)
            for key, item in value.items() if key != "runtime_s"
        }
    if isinstance(value, (list, tuple)):
        return [_profile_audits_without_runtime(item) for item in value]
    return value


def _profile_source_rows(path: Path) -> dict[str, Mapping[str, Any]]:
    import json
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return {row["slot_id"]: row for row in payload["slots"]}


def load_imu_only_orientation_evidence(candidate: Path, axis_table: Path) -> dict[str, Any]:
    """Extract only Layer-B rotation and R4 functional-axis fields.

    Translation, joint-centre, metric, UWB, and Layer-C coordinates are never
    returned by this adapter.
    """
    import json
    candidate = Path(candidate).resolve(); axis_table = Path(axis_table).resolve()
    payload = json.loads(candidate.read_text(encoding="utf-8"))
    slots = _profile_source_rows(candidate)
    extrinsics = {}
    for node in NODES:
        row = slots[f"imu_extrinsic:{node}"]
        extrinsics[node] = {
            "rotvec_segment_from_sensor": [float(x) for x in row["value"][:3]],
            "one_sigma_rad": [float(x) for x in row["uncertainty"]["one_sigma"][:3]],
            "provenance": (
                "R6A2B_LAYER_B_IMU_GRAVITY_AND_FUNCTIONAL_DIRECTIONS_ONLY; "
                "R4A_SOURCE_AUDIT_CONFIRMS_NO_UWB_ROUTE_TO_EXTRINSIC_ROTATION"
            ),
            "source_slot": f"imu_extrinsic:{node}",
            "source_frame": row["source_frame"],
            "target_frame": row["target_frame"],
        }
    functional = {}
    comparison = json.loads(axis_table.read_text(encoding="utf-8"))
    comparison_by_joint = {row["joint"]: row for row in comparison["rows"]}
    for joint, row in payload["functional_axis_profile"].items():
        functional[joint] = {
            "axis_parent_segment_session_reference": [float(x) for x in row["axis_parent_segment_session_reference"]],
            "weighted_rms_dispersion_deg": float(comparison_by_joint[joint]["repaired_weighted_rms_dispersion_deg"]),
            "weighted_q95_dispersion_deg": float(comparison_by_joint[joint]["weighted_q95_dispersion_deg"]),
            "principal_axis_uncertainty_q95_deg": float(comparison_by_joint[joint]["principal_axis_uncertainty_q95_deg"]),
            "provenance": "R6A2B_R4_NATIVE_TIME_IMU_ONLY_FUNCTIONAL_AXIS",
        }
    return {
        "extrinsic_rotation": extrinsics,
        "functional_axes": functional,
        "sources": {
            "r4_candidate": str(candidate), "r4_candidate_sha256": sha256_file(candidate),
            "r4_axis_table": str(axis_table), "r4_axis_table_sha256": sha256_file(axis_table),
        },
        "excluded_fields": [
            "extrinsic_translation", "joint_parent_centres", "joint_child_centres",
            "tag_levers", "anchor_geometry", "ranges", "T_N_V4", "Layer_C_static_coordinates",
        ],
    }


def common_grid(frontends: Mapping[str, AttitudeTimeline], rate_hz: int) -> np.ndarray:
    start = max(int(value.time_ns[0]) for value in frontends.values())
    stop = min(int(value.time_ns[-1]) for value in frontends.values())
    step = int(round(1e9 / rate_hz))
    first = ((start + step - 1) // step) * step
    last = (stop // step) * step
    if last <= first:
        raise RuntimeError("no common ten-node output grid")
    return np.arange(first, last + 1, step, dtype=np.int64)


def _resample_one(frontend: AttitudeTimeline, target: np.ndarray) -> tuple[dict[str, np.ndarray], np.ndarray]:
    source = frontend.time_ns
    right = np.searchsorted(source, target, side="left")
    valid = (right > 0) & (right < len(source))
    exact = (right < len(source)) & (source[np.clip(right, 0, len(source) - 1)] == target)
    left = np.maximum(0, right - 1)
    valid |= exact
    valid &= frontend.interval_id[left] == frontend.interval_id[np.minimum(right, len(source) - 1)]
    rotation = np.full((len(target), 3, 3), np.nan); gyro = np.full((len(target), 3), np.nan)
    bias = np.full((len(target), 3), np.nan); sigma = np.full(len(target), np.nan)
    rest = np.zeros(len(target), bool)
    boundary = np.full(len(target), "CONTINUOUS", dtype="U24")
    for i in np.flatnonzero(valid):
        r = min(int(right[i]), len(source) - 1); l = int(left[i])
        if source[r] == target[i]:
            fraction = 1.0
        else:
            fraction = float(target[i] - source[l]) / float(source[r] - source[l])
        rotation[i] = frontend.rotation_world_sensor[l] @ so3_exp(
            fraction * so3_log(frontend.rotation_world_sensor[l].T @ frontend.rotation_world_sensor[r])
        )
        gyro[i] = (1 - fraction) * frontend.gyro_rad_s[l] + fraction * frontend.gyro_rad_s[r]
        bias[i] = (1 - fraction) * frontend.gyro_bias_rad_s[l] + fraction * frontend.gyro_bias_rad_s[r]
        sigma[i] = (1 - fraction) * frontend.bias_sigma_rad_s[l] + fraction * frontend.bias_sigma_rad_s[r]
        rest[i] = bool(frontend.rest_detected[l] and frontend.rest_detected[r])
        if frontend.interval_id[l] != frontend.interval_id[max(0, l - 1)]:
            boundary[i] = "POST_GAP_OR_BOOT"
    # A missing node must not stop every unaffected body chain.  During a
    # local gap, expose an explicit degraded zero-order hold; do not integrate
    # or interpolate across the invalid interval.  The high uncertainty and
    # false rest flag make the held observation weak in shared IK.
    if not np.all(valid):
        valid_index = np.flatnonzero(valid)
        if not len(valid_index):
            raise RuntimeError("node has no valid sample on the common output grid")
        for i in np.flatnonzero(~valid):
            prior = valid_index[valid_index < i]
            source_i = int(prior[-1] if len(prior) else valid_index[0])
            rotation[i] = rotation[source_i]
            gyro[i] = 0.0
            bias[i] = bias[source_i]
            sigma[i] = np.pi
            rest[i] = False
            boundary[i] = "DEGRADED_HOLD_GAP"
    return {"rotation": rotation, "gyro": gyro, "bias": bias, "sigma": sigma, "rest": rest, "boundary": boundary}, valid


def resample_window(
    frontends: Mapping[str, AttitudeTimeline], profile_evidence: Mapping[str, Any], rate_hz: int,
    *, identity: Mapping[str, str] = IDENTITY,
) -> ResampledWindow:
    grid = common_grid(frontends, rate_hz)
    per_node = {}
    for node in NODES:
        values, valid = _resample_one(frontends[node], grid)
        per_node[node] = values | {"valid": valid}
    if len(grid) < 3:
        raise RuntimeError("gap-safe common output grid is empty")
    segment_rotation = {}; segment_gyro = {}; node_bias = {}; node_sigma = {}; node_rest = {}
    segment_degraded = {}
    boundary = np.full(len(grid), "CONTINUOUS", dtype="U24"); boundary[0] = "WINDOW_START"
    for node in NODES:
        values = per_node[node]
        extrinsic = so3_exp(np.asarray(
            profile_evidence["extrinsic_rotation"][node]["rotvec_segment_from_sensor"], float
        ))
        segment = identity[node]
        segment_rotation[segment] = np.einsum("nij,jk->nik", values["rotation"], extrinsic.T)
        segment_gyro[segment] = (extrinsic @ values["gyro"].T).T
        node_bias[node] = values["bias"]; node_sigma[node] = values["sigma"]; node_rest[node] = values["rest"]
        segment_degraded[segment] = ~values["valid"]
        boundary[values["boundary"] != "CONTINUOUS"] = "NODE_GAP_OR_BOOT"
    return ResampledWindow(
        grid, segment_rotation, segment_gyro, node_bias, node_sigma, node_rest,
        segment_degraded, boundary,
    )


def _best_world_z_gauge(target: np.ndarray, observed: np.ndarray) -> float:
    """Return the SO(2) world-z rotation that best maps observed to target."""
    product = np.asarray(observed, float) @ np.asarray(target, float).T
    return float(np.arctan2(
        product[0, 1] - product[1, 0],
        product[0, 0] + product[1, 1],
    ))


def initialize_common_action_display_yaw(
    source: ResampledWindow,
    frame_contract: Mapping[str, Any],
) -> tuple[ResampledWindow, dict[str, Any]]:
    """Initialize exactly one action display yaw without relative refitting."""
    segments = tuple(source.segment_rotation)
    root_segment = str(frame_contract["common_yaw_root_segment"])
    if root_segment not in source.segment_rotation:
        raise ValueError("common-yaw root segment is absent from the fixed profile")
    frame_count = min(
        len(source.time_ns), int(frame_contract["dynamic_initialization_frame_count"]),
    )
    if frame_count < 1:
        raise RuntimeError("action has no frame for common display-yaw initialization")
    root_mean = proper_mean(source.segment_rotation[root_segment][:frame_count])
    target = so3_exp(np.asarray(frame_contract["root_display_reference_rotvec"], float))
    yaw = _best_world_z_gauge(target, root_mean)
    transform = so3_exp(np.array([0.0, 0.0, yaw]))
    rotation = {
        segment: np.einsum("ij,njk->nik", transform, source.segment_rotation[segment])
        for segment in segments
    }
    initialized = ResampledWindow(
        source.time_ns, rotation, source.segment_gyro,
        source.node_bias, source.node_bias_sigma, source.node_rest,
        source.segment_degraded, source.boundary,
    )
    audit = {
        "schema": "biospur-fusion-v0-common-action-display-yaw-initialization-v1",
        "capture_id": frame_contract["capture_id"],
        "dynamic_initialization_frame_count": frame_count,
        "common_world_z_display_yaw_rad": yaw,
        "same_transform_applied_to_all_segment_count": len(segments),
        "per_segment_yaw_parameters_fitted": 0,
        "per_segment_extrinsics_refitted": False,
        "joint_rest_refitted": False,
        "action_specific_pose_template_used": False,
        "state_propagated_from_other_action": False,
        "vqf_samples_or_dynamics_modified": False,
        "coordinate_change_only": True,
        "selected_action_payload_only": True,
        "inter_action_payload_read": False,
        "relative_heading_observed_by_common_yaw": False,
        "remaining_unobservable_yaw_dimensions": 1,
        "remaining_yaw_scope": "ONE_COMMON_CAPTURE_FRAME_YAW_NOT_NORTH",
    }
    return initialized, audit


def hard_reference_pose_fk_gate(
    arrays: Mapping[str, np.ndarray],
    pose_kind: str,
    contract: Mapping[str, Any],
) -> dict[str, Any]:
    """Evaluate non-vacuous posture geometry from canonical FK state."""
    if pose_kind not in {"NEUTRAL_STANDING", "STRAIGHT_HORIZONTAL_TPOSE"}:
        raise ValueError(f"unknown hard FK reference pose {pose_kind}")
    names = tuple(str(value) for value in arrays["segment_names"])
    index = {name: i for i, name in enumerate(names)}
    required = {
        "pelvis", "torso", "upper_arm_left", "forearm_left",
        "upper_arm_right", "forearm_right", "thigh_left", "shank_left",
        "thigh_right", "shank_right",
    }
    if set(index) != required:
        raise ValueError("hard FK posture gate requires the exact ten-segment state")
    position = np.asarray(arrays["segment_position"], float)
    rotation = np.asarray(arrays["segment_rotation"], float)
    if not np.isfinite(position).all() or not np.isfinite(rotation).all():
        raise ValueError("hard FK posture gate received non-finite state")

    def distal(segment: str, length: float) -> np.ndarray:
        return np.einsum(
            "nij,j->ni", rotation[:, index[segment]], np.array([0.0, 0.0, -length]),
        )

    def unit(value: np.ndarray) -> np.ndarray:
        return value / np.maximum(np.linalg.norm(value, axis=1, keepdims=True), np.finfo(float).eps)

    vectors = {}
    for side in ("left", "right"):
        shoulder = position[:, index[f"upper_arm_{side}"]]
        elbow = position[:, index[f"forearm_{side}"]]
        wrist = elbow + distal(f"forearm_{side}", 0.26)
        hip = position[:, index[f"thigh_{side}"]]
        knee = position[:, index[f"shank_{side}"]]
        ankle = knee + distal(f"shank_{side}", 0.42)
        vectors[f"upper_arm_{side}"] = unit(elbow - shoulder)
        vectors[f"forearm_{side}"] = unit(wrist - elbow)
        vectors[f"thigh_{side}"] = unit(knee - hip)
        vectors[f"shank_{side}"] = unit(ankle - knee)

    down = np.array([0.0, 0.0, -1.0]); up = -down
    upright_limit = float(contract["upright_axis_max_angle_deg"])
    limb_vertical_limit = float(contract["neutral_limb_vertical_max_angle_deg"])
    horizontal_limit = float(contract["tpose_arm_horizontal_max_angle_deg"])
    straight_limit = float(contract["arm_chain_straight_max_angle_deg"])
    lateral_limit = float(contract["tpose_arm_lateral_max_angle_deg"])

    def angle_to(vector: np.ndarray, target: np.ndarray) -> np.ndarray:
        target_value = np.asarray(target, float)
        dot = (
            np.einsum("ni,ni->n", vector, target_value)
            if target_value.ndim == 2 else vector @ target_value
        )
        return np.degrees(np.arccos(np.clip(dot, -1.0, 1.0)))

    torso_up = np.einsum("nij,j->ni", rotation[:, index["torso"]], up)
    pelvis_up = np.einsum("nij,j->ni", rotation[:, index["pelvis"]], up)
    upright_q95 = float(np.quantile(np.r_[
        angle_to(torso_up, up), angle_to(pelvis_up, up),
    ], 0.95))
    arm_straight = np.r_[
        angle_to(vectors["upper_arm_left"], vectors["forearm_left"]),
        angle_to(vectors["upper_arm_right"], vectors["forearm_right"]),
    ]
    arm_straight_q95 = float(np.quantile(arm_straight, 0.95))
    leg_vertical = np.concatenate([
        angle_to(vectors[name], down)
        for name in ("thigh_left", "shank_left", "thigh_right", "shank_right")
    ])
    leg_vertical_q95 = float(np.quantile(leg_vertical, 0.95))
    checks = {
        "torso_and_pelvis_upright": upright_q95 <= upright_limit,
        "legs_down": leg_vertical_q95 <= limb_vertical_limit,
        "arms_straight": arm_straight_q95 <= straight_limit,
    }
    metrics: dict[str, Any] = {
        "torso_pelvis_upright_angle_q95_deg": upright_q95,
        "leg_vertical_angle_q95_deg": leg_vertical_q95,
        "arm_chain_bend_q95_deg": arm_straight_q95,
    }
    if pose_kind == "NEUTRAL_STANDING":
        arm_vertical = np.concatenate([
            angle_to(vectors[name], down)
            for name in (
                "upper_arm_left", "forearm_left", "upper_arm_right", "forearm_right",
            )
        ])
        arm_vertical_q95 = float(np.quantile(arm_vertical, 0.95))
        left_median = float(np.median(np.r_[
            angle_to(vectors["upper_arm_left"], down),
            angle_to(vectors["forearm_left"], down),
        ]))
        right_median = float(np.median(np.r_[
            angle_to(vectors["upper_arm_right"], down),
            angle_to(vectors["forearm_right"], down),
        ]))
        asymmetry = abs(left_median - right_median)
        checks.update({
            "arms_down": arm_vertical_q95 <= limb_vertical_limit,
            "bilateral_arm_elevation_symmetric": asymmetry
            <= float(contract["neutral_bilateral_arm_asymmetry_max_deg"]),
        })
        metrics.update({
            "arm_vertical_angle_q95_deg": arm_vertical_q95,
            "left_right_arm_vertical_median_difference_deg": asymmetry,
        })
    else:
        arm_values = [
            vectors["upper_arm_left"], vectors["forearm_left"],
            vectors["upper_arm_right"], vectors["forearm_right"],
        ]
        horizontal = np.concatenate([
            np.degrees(np.arcsin(np.clip(np.abs(value[:, 2]), 0.0, 1.0)))
            for value in arm_values
        ])
        horizontal_q95 = float(np.quantile(horizontal, 0.95))
        lateral = np.concatenate([
            angle_to(vectors["upper_arm_left"], np.array([-1.0, 0.0, 0.0])),
            angle_to(vectors["forearm_left"], np.array([-1.0, 0.0, 0.0])),
            angle_to(vectors["upper_arm_right"], np.array([1.0, 0.0, 0.0])),
            angle_to(vectors["forearm_right"], np.array([1.0, 0.0, 0.0])),
        ])
        lateral_q95 = float(np.quantile(lateral, 0.95))
        checks.update({
            "arms_horizontal": horizontal_q95 <= horizontal_limit,
            "arms_extend_to_correct_opposite_sides": lateral_q95 <= lateral_limit,
        })
        metrics.update({
            "arm_horizontal_deviation_q95_deg": horizontal_q95,
            "arm_lateral_direction_error_q95_deg": lateral_q95,
        })
    passed = bool(all(checks.values()))
    return {
        "schema": "biospur-fusion-v0-hard-reference-pose-fk-gate-v1",
        "pose_kind": pose_kind,
        "HARD_FK_STATE_GATE": "PASS" if passed else "FAIL",
        "checks": checks, "metrics": metrics,
        "thresholds": {key: float(value) for key, value in contract.items()},
        "canonical_fk_segment_position_consumed": True,
        "canonical_fk_segment_rotation_consumed": True,
        "joint_coordinate_magnitude_used_as_posture_evidence": False,
        "fk_closure_confidence_or_self_report_may_override_failure": False,
    }


def build_session_profile(
    initial: ResampledWindow,
    evidence: Mapping[str, Any],
    config_sha256: str,
    ledger_sha256: str,
    frontend_audits: Mapping[str, Any],
) -> dict[str, Any]:
    model = corrected_body_model(
        Path(__file__).resolve().parents[3],
        identity_mapping=IDENTITY,
        identity_provenance="LEGACY_V0_PROFILE_EXPLICIT_IDENTITY",
    )
    joint_reference = {}
    for joint in model.joints:
        relative = np.einsum(
            "nji,njk->nik", initial.segment_rotation[joint.parent], initial.segment_rotation[joint.child]
        )
        mean = proper_mean(relative)
        joint_reference[joint.joint_id] = {
            "rotvec_parent_from_child_session_neutral": so3_log(mean).tolist(),
            "convention": "mean initial_still attempt 2 is zero joint coordinate",
            "clinical_zero": False,
        }
    bias = {}
    tail_start = int(initial.time_ns[-1] - 2_000_000_000)
    tail = initial.time_ns >= tail_start
    for node in NODES:
        bias[node] = {
            "gyro_bias_rad_s": np.median(initial.node_bias[node][tail], axis=0).tolist(),
            "bias_sigma_rad_s": float(np.median(initial.node_bias_sigma[node][tail])),
            "rest_fraction_initial_still2": float(np.mean(initial.node_rest[node])),
            "source": "VQF_2_0_1_INITIAL_STILL_ATTEMPT_2",
        }
    profile = {
        "schema": "biospur-fusion-v0-session-profile-v1",
        "biospur_fusion_version": "V0",
        "profile_version": "capture1-session-v0.1",
        "profile_kind": "DEVELOPMENT_CAPTURE1_IMU_ONLY_BODY_RELATIVE",
        "frozen": True,
        "config_sha256": config_sha256,
        "ledger_sha256": ledger_sha256,
        "identity": IDENTITY,
        "hardware_family": HARDWARE_FAMILY,
        "physical_axis_binding": {
            "COMMON_NINE": {
                "representative": "+X,+Y,+Z raw-register gauge",
                "ambiguity": "24 proper signed permutations remained equivalent under the IMU objective",
                "physical_evidence": "+Z skinward, -Z outward, -Y downward neutral",
            },
            "BSF31CC_DISTINCT": {
                "representative": "+X,+Y,+Z raw-register gauge",
                "ambiguity": "separate 24-permutation family gauge retained",
                "physical_evidence": "distinct board; -Z outward and observed -Y downward",
            },
        },
        "calibration_windows": {
            label: {"start_global_time_ns": start, "stop_global_time_ns_exclusive": stop, "role": "CALIBRATION"}
            for label, start, stop in WINDOWS
        },
        "attitude_frontend": {
            "selected": "VQF_2_0_1_NATIVE_TIME_HYBRID",
            "native_time_expression": "(global_time_ns[i]-global_time_ns[i-1])/1e9",
            "magnetometer_used": False,
            "frontend_audits": _profile_audits_without_runtime(frontend_audits),
            "runtime_telemetry_excluded_from_frozen_profile": True,
        },
        "sensor_to_segment_rotation": evidence["extrinsic_rotation"],
        "functional_axes": evidence["functional_axes"],
        "joint_session_reference": joint_reference,
        "bias_rest": bias,
        "display_geometry": {
            "qualified_metric": False,
            "mode": "DISPLAY_ONLY_NON_METRIC_PROXY",
            "source": "explicit V0 display convention; no UWB-derived Layer-C value",
        },
        "uwb_isolation": {
            "imu_only": True,
            "profile_fields": [
                "VQF_bias_rest", "Layer_B_extrinsic_rotation", "R4_functional_axis",
                "Capture1_neutral_relative_rotation", "display_only_geometry",
            ],
            "excluded_fields": evidence["excluded_fields"],
            "beacon_common_time_allowed": True,
        },
        "root_position_mode": "ROOT_POSITION_DISPLAY_GAUGE_FIXED",
        "global_yaw_claim": "UNOBSERVABLE_COMMON_GAUGE_MAY_DRIFT_NOT_NORTH",
        "metric_skeleton_claim": "NO_DISPLAY_ONLY_NON_METRIC_GEOMETRY",
        "skin_slip_active_state": False,
        "skin_slip_handling": "ROBUST_OR_UNMODELLED",
        "held_out_golf_boxing_accessed": False,
        "sources": evidence["sources"],
    }
    assert_profile_boundary(profile)
    return profile


def _known_slot(slot_id: str, kind: str, owner: str, value: np.ndarray, sigma: float, provenance: str) -> CalibrationSlot:
    vector = tuple(float(x) for x in np.asarray(value, float))
    covariance = tuple(tuple(float(sigma * sigma if i == j else 0.0) for j in range(len(vector))) for i in range(len(vector)))
    return CalibrationSlot(slot_id, kind, owner, CalibrationStatus.VERIFIED_INPUT, vector, covariance, provenance)


def display_static_calibration(model, profile: Mapping[str, Any], geometry: Mapping[str, Any]) -> StaticCalibration:
    slots = {
        "world_model_gauge": _known_slot(
            "world_model_gauge", "display_gauge", "whole_body", np.zeros(6), 0.0,
            "ROOT_POSITION_DISPLAY_GAUGE_FIXED_NOT_MEASURED",
        )
    }
    for joint in model.joints:
        row = geometry[joint.joint_id]
        slots[joint.parent_offset_slot] = _known_slot(
            joint.parent_offset_slot, "display_joint_offset", joint.parent,
            np.asarray(row["parent"], float), 1.0, "DISPLAY_ONLY_NON_METRIC_PROXY",
        )
        slots[joint.child_offset_slot] = _known_slot(
            joint.child_offset_slot, "display_joint_offset", joint.child,
            np.asarray(row["child"], float), 1.0, "DISPLAY_ONLY_NON_METRIC_PROXY",
        )
        reference = profile["joint_session_reference"][joint.joint_id]
        if profile.get("profile_schema") in {
            "biospur-fusion-v0-capture-bound-profile-v2",
            "biospur-fusion-v0-capture-bound-profile-v3",
        }:
            if reference.get("capture_id") != profile.get("capture_id"):
                raise ValueError(
                    f"{joint.joint_id}: joint-reference provenance does not match profile capture"
                )
            if not reference.get("source_action") or not reference.get("source_window_binding_sha256"):
                raise ValueError(f"{joint.joint_id}: exact calibration-window provenance is absent")
            provenance = str(reference.get("provenance", ""))
            required_tokens = (
                str(profile["capture_id"]), str(reference["source_action"]),
                str(reference["source_window_binding_sha256"]),
            )
            if any(token not in provenance for token in required_tokens):
                raise ValueError(f"{joint.joint_id}: joint-reference provenance is not exact")
        else:
            legacy_windows = profile.get("calibration_windows", {})
            source_action = "initial_still2" if "initial_still2" in legacy_windows else "UNKNOWN"
            source_binding = hashlib.sha256(json.dumps(
                legacy_windows.get(source_action, {}), sort_keys=True, separators=(",", ":"),
            ).encode()).hexdigest()
            provenance = (
                "LEGACY_PROFILE_DERIVED_REFERENCE:"
                f"{profile.get('profile_kind', 'UNKNOWN')}:{source_action}:{source_binding}"
            )
        rest = reference["rotvec_parent_from_child_session_neutral"]
        slots[joint.rest_rotation_slot] = _known_slot(
            joint.rest_rotation_slot, "session_relative_joint_reference", joint.joint_id,
            np.asarray(rest, float), np.pi, provenance,
        )
    return StaticCalibration(slots)


def _direct_configuration(model, static: StaticCalibration, observed: Mapping[str, np.ndarray]) -> np.ndarray:
    root = np.asarray(observed[model.root_segment], float)
    values = [so3_log(root)]
    for joint in model.joints:
        rest = so3_exp(static.vector(joint.rest_rotation_slot, 3))
        relative = observed[joint.parent].T @ observed[joint.child]
        values.append(so3_log(rest.T @ relative))
    return np.concatenate(values)


def _orientation_fk(model, static: StaticCalibration, configuration: np.ndarray) -> dict[str, np.ndarray]:
    rotations = {model.root_segment: so3_exp(configuration[:3])}
    for index, joint in enumerate(model.joints):
        q = configuration[3 + 3 * index:6 + 3 * index]
        rest = so3_exp(static.vector(joint.rest_rotation_slot, 3))
        rotations[joint.child] = rotations[joint.parent] @ rest @ so3_exp(q)
    return rotations


def run_shared_ik(
    model,
    static: StaticCalibration,
    time_ns: np.ndarray,
    observed_rotation: Mapping[str, np.ndarray],
    node_bias_sigma: Mapping[str, np.ndarray],
    profile: Mapping[str, Any],
    settings: Mapping[str, Any],
    *,
    axis_enabled: bool = True,
    segment_observation_confidence: Mapping[str, np.ndarray] | None = None,
    segment_heading_evidence_confidence: Mapping[str, np.ndarray] | None = None,
) -> SharedIkResult:
    """Solve minimal generalized-coordinate orientation IK and execute canonical FK.

    Every joint remains a three-vector in SO(3). Soft temporal, robust, neutral,
    and uncertainty-derived functional-axis terms modify the generalized state
    before the one canonical BodyModel FK produces the skeleton.
    """
    times = np.asarray(time_ns, np.int64)
    segment_names = tuple(model.segments); joint_names = tuple(model.joint_ids)
    profile_identity = profile.get("identity")
    if not isinstance(profile_identity, Mapping) or set(profile_identity) != set(NODES):
        raise ValueError("profile lacks a complete capture-bound node identity map")
    if set(profile_identity.values()) != set(model.segments):
        raise ValueError("profile node identity is not a body-segment bijection")
    node_for_segment = {segment: node for node, segment in profile_identity.items()}
    count = len(times); dimension = 3 + 3 * len(joint_names)
    direct = np.empty((count, dimension))
    for i in range(count):
        direct[i] = _direct_configuration(model, static, {name: observed_rotation[name][i] for name in segment_names})
    solved = np.empty_like(direct); solved[0] = direct[0]
    dt = np.r_[np.nan, np.diff(times) * 1e-9]
    tau = float(settings["temporal_tau_s"])
    robust_scale = np.deg2rad(float(settings["robust_rotation_scale_deg"]))
    axis_max = float(settings["functional_axis_max_weight"])
    reference_weight = float(settings["neutral_reference_weight"])
    functional = profile["functional_axes"]
    if segment_observation_confidence is None:
        segment_observation_confidence = {
            segment: np.ones(count, float) for segment in segment_names
        }
    if segment_heading_evidence_confidence is None:
        # Backward-compatible control behavior: existing always-on qmt callers
        # historically used one scalar for both solve weighting and heading
        # uncertainty. QMT_OFF now supplies the two contracts explicitly.
        segment_heading_evidence_confidence = segment_observation_confidence
    observation_weight = np.empty((count, 1 + len(joint_names)))
    root_node = node_for_segment[model.root_segment]
    observation_weight[:, 0] = np.asarray(segment_observation_confidence[model.root_segment], float) / (
        1.0 + np.asarray(node_bias_sigma[root_node], float)
    )
    for j, joint in enumerate(model.joints, start=1):
        parent_node = node_for_segment[joint.parent]; child_node = node_for_segment[joint.child]
        heading_weight = np.minimum(
            np.asarray(segment_observation_confidence[joint.parent], float),
            np.asarray(segment_observation_confidence[joint.child], float),
        )
        bias_weight = 1.0 / (
            1.0 + np.asarray(node_bias_sigma[parent_node], float)
            + np.asarray(node_bias_sigma[child_node], float)
        )
        observation_weight[:, j] = heading_weight * bias_weight
    observation_weight = np.clip(observation_weight, 0.0, 1.0)
    for i in range(1, count):
        alpha_time = 1.0 - np.exp(-float(dt[i]) / tau)
        for block in range(1 + len(joint_names)):
            start = 3 * block; stop = start + 3
            previous = so3_exp(solved[i - 1, start:stop])
            target = so3_exp(direct[i, start:stop])
            error = so3_log(previous.T @ target)
            robust = 1.0 / np.sqrt(1.0 + (np.linalg.norm(error) / robust_scale) ** 2)
            update = alpha_time * robust * observation_weight[i, block] * error
            current = previous @ so3_exp(update)
            value = so3_log(current)
            if block > 0:
                joint = joint_names[block - 1]
                value *= 1.0 / (1.0 + reference_weight)
                if axis_enabled and joint in functional:
                    axis = np.asarray(functional[joint]["axis_parent_segment_session_reference"], float)
                    axis /= np.linalg.norm(axis)
                    dispersion = np.deg2rad(float(functional[joint]["weighted_rms_dispersion_deg"]))
                    weight = axis_max * max(0.0, np.cos(dispersion)) ** 2
                    parallel = axis * float(axis @ value)
                    perpendicular = value - parallel
                    value = parallel + perpendicular / (1.0 + weight)
            solved[i, start:stop] = value
    segment_rotation = np.empty((count, len(segment_names), 3, 3))
    segment_position = np.empty((count, len(segment_names), 3))
    joint_value = solved[:, 3:].reshape(count, len(joint_names), 3)
    joint_rate = np.zeros_like(joint_value)
    joint_rate[1:] = np.asarray([
        [so3_log(so3_exp(joint_value[i - 1, j]).T @ so3_exp(joint_value[i, j])) / dt[i]
         for j in range(len(joint_names))]
        for i in range(1, count)
    ])
    observation_residual = np.empty((count, len(segment_names)))
    confidence = np.empty((count, len(segment_names)))
    sigma = np.empty((count, len(segment_names)))
    closure = 0.0
    zero_joint_rate = {joint: np.zeros(3) for joint in joint_names}
    zero_node = {node: np.zeros(3) for node in NODES}
    covariance = np.zeros((9 + 6 * len(joint_names) + 6 * len(NODES),) * 2)
    for i in range(count):
        joints = {joint: joint_value[i, j].copy() for j, joint in enumerate(joint_names)}
        state = KeyframeState(
            time_s=float(times[i]) * 1e-9,
            root_translation_model_m=np.zeros(3),
            root_rotation_model_rotvec=solved[i, :3].copy(),
            root_velocity_model_mps=np.zeros(3),
            joint_rotvec=joints,
            joint_rate_rad_s={joint: joint_rate[i, j].copy() for j, joint in enumerate(joint_names)},
            gyro_bias_rad_s=zero_node,
            accel_bias_mps2=zero_node,
            covariance=covariance,
        )
        poses = model.segment_poses(state, static)
        lightweight = _orientation_fk(model, static, solved[i])
        for s, segment in enumerate(segment_names):
            segment_rotation[i, s] = poses[segment].rotation
            segment_position[i, s] = poses[segment].translation
            closure = max(closure, float(np.max(np.abs(poses[segment].rotation - lightweight[segment]))))
            observation_residual[i, s] = float(rotation_angle(poses[segment].rotation, observed_rotation[segment][i]))
            node = node_for_segment[segment]
            extrinsic_sigma = np.asarray(profile["sensor_to_segment_rotation"][node]["one_sigma_rad"], float)
            heading_sigma = (
                1.0 - float(segment_heading_evidence_confidence[segment][i])
            ) * np.deg2rad(15.0)
            sigma[i, s] = float(
                np.sqrt(np.mean(extrinsic_sigma ** 2)) + heading_sigma
                + node_bias_sigma[node][i] * max(0.0, (times[i] - times[0]) * 1e-9)
            )
            confidence[i, s] = float(1.0 / (1.0 + sigma[i, s] + observation_residual[i, s]))
    audit = {
        "schema": "biospur-fusion-v0-shared-orientation-ik-v1",
        "full_so3_joint_coordinates": True,
        "shoulder_hip_dof": 3,
        "elbow_knee_dof": 3,
        "hard_hinges": False,
        "soft_functional_axis_enabled": axis_enabled,
        "temporal_tau_s": tau,
        "robust_rotation_scale_deg": float(settings["robust_rotation_scale_deg"]),
        "neutral_reference_weight": reference_weight,
        "functional_axis_max_weight": axis_max if axis_enabled else 0.0,
        "attitude_observation_and_bias_confidence_weighted_in_solve": True,
        "heading_evidence_confidence_used_for_uncertainty_only": True,
        "attitude_observation_confidence_min": float(min(
            np.min(np.asarray(segment_observation_confidence[name], float))
            for name in segment_names
        )),
        "attitude_observation_confidence_max": float(max(
            np.max(np.asarray(segment_observation_confidence[name], float))
            for name in segment_names
        )),
        "heading_evidence_confidence_min": float(min(
            np.min(np.asarray(segment_heading_evidence_confidence[name], float))
            for name in segment_names
        )),
        "heading_evidence_confidence_max": float(max(
            np.max(np.asarray(segment_heading_evidence_confidence[name], float))
            for name in segment_names
        )),
        "observation_weight_min": float(np.min(observation_weight)),
        "observation_weight_median": float(np.median(observation_weight)),
        "canonical_body_model_executed": True,
        "canonical_fk_closure_max_abs": closure,
        "root_translation": "DISPLAY_GAUGE_FIXED_ZERO",
        "observation_residual_rms_deg": float(np.degrees(np.sqrt(np.mean(observation_residual ** 2)))),
        "finite": bool(np.isfinite(segment_rotation).all() and np.isfinite(segment_position).all()),
    }
    return SharedIkResult(
        times, segment_names, joint_names, segment_rotation, segment_position,
        joint_value, joint_rate, confidence, sigma, observation_residual, closure, audit,
    )
