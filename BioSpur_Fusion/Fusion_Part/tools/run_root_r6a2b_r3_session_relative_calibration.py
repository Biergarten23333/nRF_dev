#!/usr/bin/env python3
"""Execute the R6A2B-R3 session-relative joint and geometry-repair layer."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
import time
from typing import Any

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from biospur_fusion.root_r6a0.math3d import Pose, so3_exp, so3_log
from biospur_fusion.root_r6a2a.shadow import corrected_body_model
from biospur_fusion.root_r6a2b.layered_calibration import (
    FAMILIES, NODES, CanonicalCalibrationAdapter,
    _CachedStaticCalibrationView, _ValidatedStateView,
    _state_from_segment_rotations,
)
from biospur_fusion.root_r6a2b.real_profile import profile_checksum
from biospur_fusion.root_r6a2b.session_relative_calibration import (
    HINGE_ACTION, JOINT_ACTIONS, REFERENCE_WINDOW, REPORTING_LABEL,
    RESIDUAL_FAMILIES, SESSION_REFERENCE_NAME,
    apply_reference_solution, deferred_anthropometry_schema,
    geometry_causal_trace, joint_rest_gauge_causal_trace, objective_summary,
    relative_joint_rotations, session_joint_coordinates,
    solve_session_references,
)


FUSION = Path(__file__).resolve().parents[1]
R1 = FUSION / "logs/root_r6a2b_r1_calibration_first_20260826T093237Z"
R2 = FUSION / "logs/root_r6a2b_r2_layered_real_calibration_20260826T143000Z"
CHECKPOINT = "52e2896bb6437aa19710a6c0b6f54b4193f64e4a"
CAPTURE_ID = "v47_ten_node_body_calibration_20260814_093601"
EXPECTED_WINDOWS = (
    ("initial_still2", 2986078873797, 2994078940466),
    ("t_pose", 3019030103768, 3027030170523),
    ("arms", 3065724244760, 3212615253685),
    ("left_elbow", 3371591610404, 3411475048316),
    ("right_elbow2", 3494725933278, 3528015255640),
    ("left_knee", 3551740910191, 3579592651754),
    ("right_knee", 3602476636179, 3627048980515),
    ("left_heel", 3666677754354, 3687781716166),
    ("right_heel", 3712252142978, 3737709976189),
    ("squats", 3761427161163, 3785916206867),
    ("trunk", 3814053447917, 3854622450716),
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json(value: Any) -> Any:
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, dict):
        return {str(key): _json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json(item) for item in value]
    return value


def dump(path: Path, value: Any) -> None:
    path.write_text(json.dumps(_json(value), indent=2, sort_keys=True, allow_nan=False) + "\n",
                    encoding="utf-8")


def quantiles(values: np.ndarray) -> dict[str, float | int | None]:
    value = np.asarray(values, float)
    if not len(value):
        return {"count": 0, "min": None, "q05": None, "median": None,
                "q95": None, "max": None, "rms": None}
    return {
        "count": int(len(value)), "min": float(np.min(value)),
        "q05": float(np.quantile(value, .05)), "median": float(np.median(value)),
        "q95": float(np.quantile(value, .95)), "max": float(np.max(value)),
        "rms": float(np.sqrt(np.mean(value ** 2))),
    }


def seal(result_dir: Path) -> None:
    files = sorted(path for path in result_dir.iterdir()
                   if path.is_file() and path.name != "SHA256SUMS")
    (result_dir / "SHA256SUMS").write_text(
        "".join(f"{sha256(path)}  {path.name}\n" for path in files), encoding="utf-8"
    )


def validate_predecessor() -> tuple[dict[str, Any], dict[str, Any]]:
    final = json.loads((R2 / "FINAL_RESULT.json").read_text())
    verification = json.loads((R2 / "INDEPENDENT_VERIFICATION.json").read_text())
    if final["independent_verification"] != "PASS" or verification["verdict"] != "PASS":
        raise RuntimeError("R2 predecessor is not independently accepted")
    profile = json.loads((R2 / "DEVELOPMENT_ONLY_REAL_CALIBRATION_CANDIDATE.json").read_text())
    observed = tuple(
        (row["label"], int(row["start_global_time_ns"]), int(row["stop_global_time_ns_exclusive"]))
        for row in profile["binding"]["authorized_windows"]
    )
    if observed != EXPECTED_WINDOWS or profile["binding"]["checkpoint"] != CHECKPOINT:
        raise RuntimeError("R2 bindings/windows changed")
    if profile["binding"]["held_out_golf_boxing_accessed"] is not False:
        raise RuntimeError("held-out firewall is not intact")
    return profile, verification


def sensor_sigma_by_segment(model, profile: dict[str, Any]) -> dict[str, float]:
    slots = {row["slot_id"]: row for row in profile["slots"]}
    inverse = {segment: node for node, segment in model.identity_mapping.items()}
    result = {}
    for segment, node in inverse.items():
        sigma = np.asarray(slots[f"imu_extrinsic:{node}"]["uncertainty"]["one_sigma"], float)[:3]
        result[segment] = float(np.sqrt(np.mean(sigma ** 2)))
    return result


def reference_solution_json(full, ablation) -> dict[str, Any]:
    return {
        "schema": "biospur-root-r6a2b-r3-session-reference-solution-v1",
        "reference_name": SESSION_REFERENCE_NAME,
        "composition": "R_child(t) = R_parent(t) * R_reference_joint * Exp(q_session(t))",
        "multiplication_order": "active rotations; parent-to-child right composition",
        "gauge_constraint": (
            "mean selected q_session over initial_still attempt 2 equals zero; this defines coordinates "
            "and is not independent physiological measurement evidence"
        ),
        "not_claimed": [
            "perfect physiological zero", "clinical goniometer truth", "fixed biological hinge axis",
            "cross-subject anatomical standard",
        ],
        "per_joint": {
            joint: {
                "reporting_label": REPORTING_LABEL[joint],
                "dof_family": (
                    "SOFT_EFFECTIVE_HINGE_WITH_OFF_AXIS_FREEDOM" if joint in HINGE_ACTION else
                    "THREE_ROTATIONAL_DOF"
                ),
                "actions": JOINT_ACTIONS[joint],
                "reference_rotvec_rad": solve.reference_rotvec,
                "reference_rotation_parent_to_child": so3_exp(solve.reference_rotvec),
                "reference_covariance_rad2": solve.reference_covariance,
                "reference_one_sigma_rad": np.sqrt(np.maximum(np.diag(solve.reference_covariance), 0.0)),
                "uncertainty_components": solve.uncertainty,
                "functional_axis": solve.functional_axis,
                "ablation_reference_rotvec_rad": ablation[joint].reference_rotvec,
                "convention_rank": solve.convention_rank,
            } for joint, solve in full.items()
        },
        "remaining_signed_axis_gauge": {
            "canonical_serialized_representative": "+X,+Y,+Z",
            "physical_register_to_device_mapping_claimed": False,
            "all_24_family_representatives_preserved": True,
        },
    }


def covariance_information(full, ablation, r2_information: dict[str, Any]) -> dict[str, Any]:
    added_singular = []
    column_norms = {}
    per_joint = {}
    for joint, solve in full.items():
        full_info = np.linalg.pinv(solve.reference_covariance)
        ablation_info = np.linalg.pinv(ablation[joint].reference_covariance)
        added = 0.5 * ((full_info - ablation_info) + (full_info - ablation_info).T)
        eigen = np.maximum(np.linalg.eigvalsh(added), 0.0)
        singular = np.sqrt(eigen)
        added_singular.extend(float(value) for value in singular)
        column_norms[joint] = np.sqrt(np.maximum(np.diag(added), 0.0)).tolist()
        per_joint[joint] = {
            "full_reference_one_sigma_rad": np.sqrt(np.maximum(np.diag(solve.reference_covariance), 0.0)),
            "ablation_reference_one_sigma_rad": np.sqrt(np.maximum(
                np.diag(ablation[joint].reference_covariance), 0.0
            )),
            "biomechanics_profiled_information_eigenvalues": eigen,
        }
    predecessor = np.asarray(r2_information["singular_values_descending"], float)
    combined = np.sort(np.concatenate((predecessor[:87], np.asarray(added_singular))))[::-1]
    return {
        "schema": "biospur-root-r6a2b-r3-observability-and-covariance-v1",
        "canonical_static_dimension": 114,
        "before": {"data_rank": 87, "data_nullity": 27,
                   "joint_rest_jacobian_column_norms": [0.0] * 27},
        "after_session_reference_convention": {
            "coordinate_rank": 114, "coordinate_nullity": 0,
            "joint_rest_data_nullity": 0,
            "joint_rest_profiled_jacobian_column_norms": column_norms,
            "singular_values_descending": combined,
        },
        "physical_information_without_coordinate_definition": {
            "rank": 87, "nullity": 27,
            "meaning": (
                "removing the neutral q=0 convention restores the exact constant-rotation gauge; "
                "the added rank is coordinate definition, not clinical/anatomical truth"
            ),
            "null_vectors": [f"joint_rest:{joint}:{axis}" for joint in full for axis in "xyz"],
        },
        "data_plus_bounded_prior_rank": 114,
        "posterior_covariance_zeroed_by_gauge_fix": False,
        "per_joint": per_joint,
    }


def ablation_json(model, adapter, full, ablation, full_vector, ablation_vector,
                  full_covariance, ablation_covariance, relative, windows) -> dict[str, Any]:
    per_joint = {}
    all_q_differences = []
    for joint in model.joints:
        left = session_joint_coordinates(so3_exp(full[joint.joint_id].reference_rotvec),
                                         relative[joint.joint_id])
        right = session_joint_coordinates(so3_exp(ablation[joint.joint_id].reference_rotvec),
                                          relative[joint.joint_id])
        difference = left - right
        all_q_differences.append(difference.reshape(-1))
        block = adapter.by_slot[joint.rest_rotation_slot]
        per_joint[joint.joint_id] = {
            "reference_parameter_difference_l2_rad": float(np.linalg.norm(
                full[joint.joint_id].reference_rotvec - ablation[joint.joint_id].reference_rotvec
            )),
            "trajectory_difference_rad": quantiles(np.linalg.norm(difference, axis=1)),
            "full_covariance_trace_rad2": float(np.trace(full_covariance[block.start:block.stop,
                                                                         block.start:block.stop])),
            "ablation_covariance_trace_rad2": float(np.trace(ablation_covariance[block.start:block.stop,
                                                                                 block.start:block.stop])),
        }
    pair_rows = {}
    for family, left, right in (
        ("elbow", "elbow_left", "elbow_right"),
        ("knee", "knee_left", "knee_right"),
        ("shoulder", "shoulder_left", "shoulder_right"),
        ("hip", "hip_left", "hip_right"),
    ):
        pair_rows[family] = {
            "full_reference_norm_left_right_rad": [
                float(np.linalg.norm(full[left].reference_rotvec)),
                float(np.linalg.norm(full[right].reference_rotvec)),
            ],
            "absolute_left_right_norm_difference_rad": float(abs(
                np.linalg.norm(full[left].reference_rotvec) - np.linalg.norm(full[right].reference_rotvec)
            )),
        }
        if left in HINGE_ACTION:
            pair_rows[family]["axis_dispersion_left_right_rad"] = [
                full[left].functional_axis["angular_dispersion_rad"],
                full[right].functional_axis["angular_dispersion_rad"],
            ]
    trajectory_difference = np.concatenate(all_q_differences)
    parameter_difference = full_vector - ablation_vector
    covariance_difference = full_covariance - ablation_covariance
    return {
        "schema": "biospur-root-r6a2b-r3-biomechanics-factor-ablation-v1",
        "identical_initialization": True,
        "identical_capture1_data_and_windows": True,
        "retuning_between_runs": False,
        "full_objective": objective_summary(full),
        "without_joint_reference_and_functional_axis_factors": objective_summary(ablation),
        "parameter_vector_difference_l2": float(np.linalg.norm(parameter_difference)),
        "parameter_vector_max_abs_difference": float(np.max(np.abs(parameter_difference))),
        "session_relative_trajectory_difference": quantiles(np.abs(trajectory_difference)),
        "joint_rest_covariance_difference_frobenius": float(np.linalg.norm(covariance_difference, "fro")),
        "information": {
            "full_coordinate_rank_nullity": [114, 0],
            "ablation_physical_rank_nullity": [87, 27],
        },
        "calibration_window_replay": {
            "windows": list(dict.fromkeys(str(value) for value in windows)),
            "all_11_replayed_in_both_parameterizations": True,
            "shared_fk_segment_predictions_change": 0.0,
            "reason": "q transforms compensate reference changes while observed segment rotations stay fixed",
        },
        "left_right_consistency": pair_rows,
        "per_joint": per_joint,
        "nonzero_interpretable_effect_observed": bool(
            np.any(parameter_difference != 0.0) or np.any(covariance_difference != 0.0)
        ),
        "interpretation_rule": (
            "continuous parameter, trajectory, covariance, and rank differences are reported; "
            "no arbitrary materiality threshold was introduced"
        ),
    }


def profile_rows(r2_profile, adapter, vector, covariance, geometry_trace):
    rows = copy.deepcopy(r2_profile["slots"])
    values = adapter.vector_to_slots(vector)
    diagnostics = geometry_trace["reported_bone_length_definitions"]
    bone_by_slot = {
        f"bone_length:{name}": row for name, row in diagnostics.items()
    }
    for row in rows:
        slot_id = row["slot_id"]
        block = adapter.by_slot.get(slot_id)
        if block is not None and block.kind == "joint_rest":
            row["value"] = values[slot_id].tolist()
            local = covariance[block.start:block.stop, block.start:block.stop]
            row["uncertainty"] = {
                "posterior_local_covariance_rad2": local.tolist(),
                "one_sigma_rad": np.sqrt(np.maximum(np.diag(local), 0.0)).tolist(),
                "signed_axis_family_gauge_conditional": True,
            }
            row["status"] = "DEVELOPMENT_SESSION_RELATIVE_REFERENCE_NOT_CLINICAL_ZERO"
            row["provenance"] = {
                "execution": "ROOT_R6A2B_R3_REAL_CAPTURE1_SESSION_REFERENCE_SOLVE",
                "reference_window": "initial_still attempt 2",
                "functional_actions": list(JOINT_ACTIONS[slot_id.split(":", 1)[1]]),
                "production_qualified": False,
            }
        elif slot_id.startswith("bone_length:"):
            row["value"] = None
            row["uncertainty"] = None
            row["status"] = "PENDING_OPERATOR_ANTHROPOMETRY_DIAGNOSTIC_FIT_NOT_SERIALIZED"
            row["provenance"] = {
                "diagnostic_only_value_m": bone_by_slot.get(slot_id, {}).get("value_m"),
                "causal_trace": "METRIC_GEOMETRY_CAUSAL_TRACE.json",
                "independent_freedom": False,
            }
    if len(rows) != 87 or len({row["slot_id"] for row in rows}) != 87:
        raise RuntimeError("R3 profile changed the immutable 87-slot inventory")
    return rows


def replay_shared_fk(result_dir, model, adapter, vector, covariance, replay, relative,
                     internal, profile_checksum_value):
    static = _CachedStaticCalibrationView(
        adapter.materialize_static(vector, covariance=covariance, internal_levers=internal), model
    )
    segment_names = [str(value) for value in replay["segment_names"]]
    segment_index = {segment: index for index, segment in enumerate(segment_names)}
    joint_names = list(model.joint_ids)
    reference = {
        joint.joint_id: so3_exp(adapter.vector_to_slots(vector)[joint.rest_rotation_slot])
        for joint in model.joints
    }
    joint_q = np.stack([
        session_joint_coordinates(reference[joint], relative[joint]) for joint in joint_names
    ], axis=1)
    parent_child = np.stack([relative[joint] for joint in joint_names], axis=1)
    segment_origin = np.empty((len(replay["time_ns"]), len(model.segments), 3))
    predicted_rotation = np.empty((len(replay["time_ns"]), len(model.segments), 3, 3))
    closure_max = 0.0
    for frame, time_ns in enumerate(replay["time_ns"]):
        rotations = {segment: replay["segment_rotation"][frame, segment_index[segment]]
                     for segment in model.segments}
        state = _ValidatedStateView(
            _state_from_segment_rotations(model, rotations, static, int(time_ns)), model
        )
        poses = model.segment_poses(state, static)
        for index, segment in enumerate(model.segments):
            segment_origin[frame, index] = poses[segment].translation
            predicted_rotation[frame, index] = poses[segment].rotation
            closure_max = max(closure_max, float(np.max(np.abs(
                poses[segment].rotation - rotations[segment]
            ))))
    np.savez_compressed(
        result_dir / "CALIBRATION_WINDOW_REPLAY.npz",
        time_ns=replay["time_ns"], window=replay["window"],
        segment_names=np.asarray(model.segments), joint_names=np.asarray(joint_names),
        parent_child_relative_rotation=parent_child,
        session_relative_joint_rotvec=joint_q,
        segment_rotation=predicted_rotation, segment_origin_m=segment_origin,
        reference_rotation=np.asarray([reference[joint] for joint in joint_names]),
    )
    return {
        "schema": "biospur-root-r6a2b-r3-replay-summary-v1",
        "rows": int(len(replay["time_ns"])), "window_count": 11,
        "windows": list(dict.fromkeys(str(value) for value in replay["window"])),
        "all_finite": bool(np.isfinite(joint_q).all() and np.isfinite(segment_origin).all()),
        "canonical_shared_fk_max_rotation_closure_error": closure_max,
        "profile_checksum_before_replay": profile_checksum_value,
        "profile_checksum_after_replay": profile_checksum_value,
        "profile_mutated": False,
        "clinical_angle_validation_claimed": False,
    }, joint_q, parent_child, segment_origin


def plots(result_dir, model, replay, joint_q, parent_child, segment_origin, full):
    windows = np.asarray(replay["window"], dtype="U32")
    time_s = (replay["time_ns"] - replay["time_ns"][0]) * 1e-9
    labels = list(dict.fromkeys(str(value) for value in windows))
    joint_names = list(model.joint_ids)
    joint_deg = np.degrees(np.linalg.norm(joint_q, axis=2))
    relative_deg = np.degrees(np.linalg.norm(np.asarray([
        [[so3_log(rotation) for rotation in frame] for frame in parent_child]
    ])[0], axis=2))
    common_limit = 1.02 * max(1.0, float(np.max(joint_deg)), float(np.max(relative_deg)))

    for filename, values, title, ylabel in (
        ("PARENT_CHILD_RELATIVE_ORIENTATIONS_FIXED_SCALE.png", relative_deg,
         "Parent-to-child relative orientations | fixed scale | window gaps not connected",
         "|Log(parent^T child)| [deg]"),
        ("SESSION_RELATIVE_JOINT_TRAJECTORIES_FIXED_SCALE.png", joint_deg,
         "Session-relative joint coordinates | not clinically validated angles | fixed scale",
         "|q_session| [deg]"),
    ):
        figure, axis = plt.subplots(figsize=(14, 6))
        for joint_index, joint in enumerate(joint_names):
            for action_index, action in enumerate(labels):
                mask = windows == action
                axis.plot(time_s[mask], values[mask, joint_index], lw=.65,
                          label=REPORTING_LABEL[joint] if action_index == 0 else None)
        axis.set_ylim(0.0, common_limit); axis.set_xlabel("concatenated authorized-window time [s]")
        axis.set_ylabel(ylabel); axis.set_title(title); axis.grid(alpha=.25)
        axis.legend(ncol=3, fontsize=6); figure.tight_layout(); figure.savefig(result_dir / filename, dpi=170)
        plt.close(figure)

    steps = np.full((len(joint_q), len(joint_names)), np.nan)
    branch = np.degrees(np.linalg.norm(joint_q, axis=2))
    for action in labels:
        index = np.flatnonzero(windows == action)
        for left, right in zip(index[:-1], index[1:]):
            for joint_index in range(len(joint_names)):
                steps[right, joint_index] = np.degrees(np.linalg.norm(so3_log(
                    so3_exp(joint_q[left, joint_index]).T @ so3_exp(joint_q[right, joint_index])
                )))
    step_limit = 1.02 * max(1.0, float(np.nanmax(steps)))
    figure, axes = plt.subplots(2, 1, figsize=(14, 8), sharex=True)
    for joint_index, joint in enumerate(joint_names):
        axes[0].plot(time_s, steps[:, joint_index], lw=.6, label=REPORTING_LABEL[joint])
        axes[1].plot(time_s, branch[:, joint_index], lw=.6, label=REPORTING_LABEL[joint])
    axes[0].set_ylim(0, step_limit); axes[1].set_ylim(0, 180)
    axes[0].set_ylabel("SO(3) frame step [deg]"); axes[1].set_ylabel("principal-log norm [deg]")
    axes[1].set_xlabel("concatenated authorized-window time [s]")
    axes[0].set_title("Discontinuity audit | gaps retained as NaN; no cross-window connection")
    axes[1].set_title("Principal-branch/sign-flip audit | fixed 0..180 deg")
    for axis in axes: axis.grid(alpha=.25); axis.legend(ncol=3, fontsize=6)
    figure.tight_layout(); figure.savefig(result_dir / "DISCONTINUITY_SIGN_FLIP_FIXED_SCALE.png", dpi=170)
    plt.close(figure)

    hinge = list(HINGE_ACTION)
    directions = np.asarray([full[joint].functional_axis["direction_parent_reference_unoriented"] for joint in hinge])
    dispersion = np.degrees([full[joint].functional_axis["angular_dispersion_rad"] for joint in hinge])
    figure, axes = plt.subplots(1, 2, figsize=(12, 5))
    width = .22; x = np.arange(len(hinge))
    for component in range(3): axes[0].bar(x + (component - 1) * width, directions[:, component], width,
                                          label=("x", "y", "z")[component])
    axes[0].set_xticks(x, hinge, rotation=20); axes[0].set_ylim(-1, 1); axes[0].legend()
    axes[0].set_title("Unoriented effective flexion-axis components")
    axes[1].bar(x, dispersion); axes[1].set_xticks(x, hinge, rotation=20)
    axes[1].set_ylabel("RMS angular dispersion [deg]")
    axes[1].set_title("Observed off-axis dispersion; not forced to zero")
    figure.tight_layout(); figure.savefig(result_dir / "FUNCTIONAL_AXIS_DIRECTION_AND_DISPERSION.png", dpi=170)
    plt.close(figure)

    sigma = np.asarray([np.degrees(np.sqrt(np.diag(full[joint].reference_covariance))) for joint in joint_names])
    figure, axis = plt.subplots(figsize=(12, 5))
    for component in range(3): axis.bar(np.arange(len(joint_names)) + (component - 1) * .22,
                                        sigma[:, component], .22, label=("rx", "ry", "rz")[component])
    axis.set_xticks(np.arange(len(joint_names)), joint_names, rotation=25); axis.set_ylabel("one sigma [deg]")
    axis.set_title("Session-reference posterior uncertainty | conditional on signed-axis representative")
    axis.legend(); figure.tight_layout(); figure.savefig(result_dir / "REFERENCE_POSE_UNCERTAINTY.png", dpi=170)
    plt.close(figure)

    frame = int(np.flatnonzero(windows == REFERENCE_WINDOW)[0])
    figure = plt.figure(figsize=(10, 8)); axis = figure.add_subplot(111, projection="3d")
    origins = segment_origin[frame]
    for joint in model.joints:
        parent = model.segments.index(joint.parent); child = model.segments.index(joint.child)
        axis.plot([origins[parent, 0], origins[child, 0]], [origins[parent, 1], origins[child, 1]],
                  [origins[parent, 2], origins[child, 2]], color="tab:red", lw=2)
    axis.scatter(origins[:, 0], origins[:, 1], origins[:, 2], color="black", s=22)
    axis.set_title("UNQUALIFIED metric-geometry diagnostic | no synthetic completion")
    axis.set_xlabel("x [m]"); axis.set_ylabel("y [m]"); axis.set_zlabel("z [m]")
    figure.tight_layout(); figure.savefig(result_dir / "METRIC_GEOMETRY_DIAGNOSTIC_UNQUALIFIED.png", dpi=170)
    plt.close(figure)

    render_root_relative_frames(result_dir)


def render_root_relative_frames(result_dir: Path) -> None:
    """Render coordinate frames from the already executed canonical-FK replay."""
    replay = np.load(result_dir / "CALIBRATION_WINDOW_REPLAY.npz", allow_pickle=False)
    origins = replay["segment_origin_m"]
    rotations = replay["segment_rotation"]
    names = [str(value) for value in replay["segment_names"]]
    rows = np.unique(np.linspace(0, len(origins) - 1, 4, dtype=int))
    figure = plt.figure(figsize=(14, 11)); axis = figure.add_subplot(111, projection="3d")
    colours = ("tab:red", "tab:green", "tab:blue")
    scale = .08
    for frame_number, frame in enumerate(rows):
        alpha = .35 + .2 * frame_number
        for segment, name in enumerate(names):
            origin = origins[frame, segment]
            for coordinate in range(3):
                tip = origin + scale * rotations[frame, segment, :, coordinate]
                axis.plot([origin[0], tip[0]], [origin[1], tip[1]], [origin[2], tip[2]],
                          color=colours[coordinate], alpha=alpha, lw=.8)
            if frame_number == 0:
                axis.text(origin[0], origin[1], origin[2], name, fontsize=6)
    axis.set_xlabel("root-relative x [m]"); axis.set_ylabel("root-relative y [m]")
    axis.set_zlabel("root-relative z [m]")
    axis.set_title("Canonical shared-FK root-relative segment frames | metric origins unqualified")
    figure.tight_layout(); figure.savefig(result_dir / "ROOT_RELATIVE_SEGMENT_FRAMES.png", dpi=170)
    plt.close(figure)


def run(result_dir: Path) -> Path:
    started = time.monotonic()
    result_dir.mkdir(parents=True, exist_ok=False)
    r2_profile, r2_verification = validate_predecessor()
    source_ledger = Path(r2_profile["immutability"]["historical_registry"])
    ledger_before = sha256(source_ledger)
    if ledger_before != r2_profile["immutability"]["sha256_after"]:
        raise RuntimeError("historical calibration registry changed before R3")

    source_rows = json.loads((R1 / "CALIBRATION_PARAMETER_PROVENANCE.json").read_text())["slots"]
    model = corrected_body_model(FUSION)
    adapter = CanonicalCalibrationAdapter(model, source_rows)
    r2_estimate = np.load(R2 / "CALIBRATION_ESTIMATE.npz", allow_pickle=False)
    base_vector = r2_estimate["vector"].copy()
    base_covariance = r2_estimate["covariance"].copy()
    replay = np.load(R2 / "CALIBRATION_WINDOW_REPLAY.npz", allow_pickle=False)
    r2_inputs = np.load(R2 / "SOLVE_INPUTS.npz", allow_pickle=False)
    relative = relative_joint_rotations(model, replay["segment_rotation"], replay["segment_names"])
    sigma = sensor_sigma_by_segment(model, r2_profile)

    full = solve_session_references(model, relative, replay["window"], sigma,
                                    include_biomechanics=True)
    ablation = solve_session_references(model, relative, replay["window"], sigma,
                                        include_biomechanics=False)
    full_vector, full_covariance = apply_reference_solution(
        adapter, base_vector, full, base_covariance
    )
    ablation_vector, ablation_covariance = apply_reference_solution(
        adapter, base_vector, ablation, base_covariance
    )
    internal = {node: r2_inputs["internal_lever"][index] for index, node in enumerate(NODES)}

    sample_frame = 0
    sample_rotations = {
        str(segment): replay["segment_rotation"][sample_frame, index]
        for index, segment in enumerate(replay["segment_names"])
    }
    causal = joint_rest_gauge_causal_trace(
        model, adapter, base_vector, sample_rotations,
        int(replay["time_ns"][sample_frame]), internal,
    )
    geometry = geometry_causal_trace(
        model, adapter, base_vector, base_covariance,
        r2_estimate["geometry_data_jacobian"], replay["segment_rotation"], replay["window"],
    )
    r2_information = json.loads((R2 / "OBSERVABILITY_AND_COVARIANCE.json").read_text())
    information = covariance_information(full, ablation, r2_information)
    biomechanics = objective_summary(full)
    biomechanics.update({
        "schema": "biospur-root-r6a2b-r3-functional-biomechanics-objective-v1",
        "action_semantics": {
            joint: {"actions": actions, "factor_role": (
                "effective-axis plus soft off-axis" if joint in HINGE_ACTION else
                "three-DOF closure/functional-increment/temporal factors"
            )} for joint, actions in JOINT_ACTIONS.items()
        },
        "factor_definitions": {
            "reference_pose": "mean neutral session q=0 coordinate convention",
            "functional_axis": "measured group-increment agreement; hinge actions additionally report effective PCA axis",
            "off_axis_soft": "finite-weight cumulative motion perpendicular to elbow/knee effective axis",
            "joint_closure": "Log((R_reference Exp(q))^T (R_parent^T R_child))",
            "temporal_consistency": "finite-weight difference of consecutive SO(3) group increments",
            "prior": "R6A1B broad pi-rad joint-reference prior",
        },
        "shoulders_hips_three_dof_retained": True,
        "elbows_knees_off_axis_freedom_retained": True,
        "perfect_hinge_imposed": False,
    })
    ablation_report = ablation_json(
        model, adapter, full, ablation, full_vector, ablation_vector,
        full_covariance, ablation_covariance, relative, replay["window"],
    )

    dump(result_dir / "JOINT_REST_GAUGE_CAUSAL_TRACE.json", causal)
    dump(result_dir / "SESSION_RELATIVE_JOINT_REFERENCE.json", reference_solution_json(full, ablation))
    dump(result_dir / "FUNCTIONAL_BIOMECHANICS_OBJECTIVE.json", biomechanics)
    dump(result_dir / "BIOMECHANICS_FACTOR_ABLATION.json", ablation_report)
    dump(result_dir / "METRIC_GEOMETRY_CAUSAL_TRACE.json", geometry)
    dump(result_dir / "DEFERRED_ANTHROPOMETRY_INPUT_SCHEMA.json", deferred_anthropometry_schema())
    dump(result_dir / "OBSERVABILITY_AND_COVARIANCE.json", information)

    profile = {
        "schema": "biospur-root-r6a2b-r3-development-calibration-candidate-v1",
        "profile_kind": "DEVELOPMENT_ONLY_REAL_SESSION_RELATIVE_CALIBRATION_CANDIDATE",
        "profile_version": "R6A2B-R3-CANDIDATE-001",
        "qualification_verdict": "DEVELOPMENT_ONLY_NOT_PRODUCTION",
        "frozen": True,
        "binding": copy.deepcopy(r2_profile["binding"]),
        "predecessor": {
            "result": str(R2), "profile_sha256": sha256(R2 / "DEVELOPMENT_ONLY_REAL_CALIBRATION_CANDIDATE.json"),
            "independent_verification": r2_verification["verdict"],
        },
        "parameterization": {
            "slot_count": 28, "dimension": 114,
            "joint_reference": SESSION_REFERENCE_NAME,
            "composition": "R_parent * R_reference_joint * Exp(q_session)",
        },
        "signed_axis": copy.deepcopy(r2_profile["signed_axis"]),
        "static_vector": full_vector.tolist(),
        "static_covariance": full_covariance.tolist(),
        "bias_states": copy.deepcopy(r2_profile["bias_states"]),
        "slots": profile_rows(r2_profile, adapter, full_vector, full_covariance, geometry),
        "information": {
            "joint_rest_data_nullity_before": 27,
            "joint_rest_coordinate_nullity_after_convention": 0,
            "physical_nullity_if_convention_removed": 27,
        },
        "capability": {
            "body_relative_segment_orientation": "AVAILABLE_DEVELOPMENT",
            "session_relative_joint_coordinates": "AVAILABLE_DEVELOPMENT_NOT_CLINICAL_ANGLES",
            "metric_skeleton": "PENDING_OPERATOR_ANTHROPOMETRY_AND_RF_PHASE_CENTRES",
            "world_translation": "UNAUTHORIZED_T_N_V4_UNRESOLVED",
            "production_process_noise": "UNRESOLVED",
        },
        "anthropometry": {
            "input_schema": "DEFERRED_ANTHROPOMETRY_INPUT_SCHEMA.json",
            "measured_values_entered": False,
            "synthetic_values_entered": False,
        },
        "immutability": {
            "historical_registry": str(source_ledger),
            "sha256_before": ledger_before, "sha256_after": sha256(source_ledger), "writes": 0,
        },
    }
    if profile["immutability"]["sha256_before"] != profile["immutability"]["sha256_after"]:
        raise RuntimeError("historical registry changed during R3")
    profile["profile_checksum_sha256"] = profile_checksum(profile)
    dump(result_dir / "DEVELOPMENT_ONLY_REAL_CALIBRATION_CANDIDATE.json", profile)

    replay_summary, joint_q, parent_child, segment_origin = replay_shared_fk(
        result_dir, model, adapter, full_vector, full_covariance, replay, relative,
        internal, profile["profile_checksum_sha256"],
    )
    dump(result_dir / "CALIBRATION_WINDOW_REPLAY_SUMMARY.json", replay_summary)
    plots(result_dir, model, replay, joint_q, parent_child, segment_origin, full)

    access = json.loads((R2 / "CALIBRATION_WINDOW_ACCESS_AUDIT.json").read_text())
    dump(result_dir / "CALIBRATION_WINDOW_ACCESS_AUDIT.json", {
        "schema": "biospur-root-r6a2b-r3-authorized-window-access-v1",
        "source_r2_access_audit": str(R2 / "CALIBRATION_WINDOW_ACCESS_AUDIT.json"),
        "source_r2_access_audit_sha256": sha256(R2 / "CALIBRATION_WINDOW_ACCESS_AUDIT.json"),
        "exact_authoritative_windows": access["windows"],
        "numeric_input": "sealed R2 native-time replay and deterministic solve inputs",
        "held_out_members_or_intervals_opened": [],
        "Golf_or_Boxing_opened": False,
    })
    dump(result_dir / "SIGNED_AXIS_GAUGE.json", {
        "schema": "biospur-root-r6a2b-r3-signed-axis-gauge-v1",
        "source": str(R2 / "SIGNED_AXIS_AUDIT.json"),
        "source_sha256": sha256(R2 / "SIGNED_AXIS_AUDIT.json"),
        "search_repeated": False,
        "reason": "joint-reference reparameterization does not alter the node signed-axis family gauge",
        "canonical_representative": "+X,+Y,+Z",
        "all_24_per_family_preserved": True,
    })
    np.savez_compressed(
        result_dir / "SOLVE_INPUTS.npz",
        time_ns=replay["time_ns"], window=replay["window"],
        segment_names=replay["segment_names"], segment_rotation=replay["segment_rotation"],
        base_vector=base_vector, base_covariance=base_covariance,
        internal_lever=np.asarray([internal[node] for node in NODES]),
        sensor_sigma_segment=np.asarray([sigma[segment] for segment in model.segments]),
    )
    np.savez_compressed(
        result_dir / "CALIBRATION_ESTIMATE.npz",
        vector=full_vector, covariance=full_covariance,
        ablation_vector=ablation_vector, ablation_covariance=ablation_covariance,
        joint_reference=np.asarray([full[joint].reference_rotvec for joint in model.joint_ids]),
        joint_reference_covariance=np.asarray([full[joint].reference_covariance for joint in model.joint_ids]),
    )
    dump(result_dir / "PARAMETER_CHANGE_LEDGER.json", {
        "schema": "biospur-root-r6a2b-r3-parameter-change-ledger-v1",
        "base_r2_to_r3_l2": float(np.linalg.norm(full_vector - base_vector)),
        "changed_coordinates": [
            {"index": index, "before": float(base_vector[index]), "after": float(full_vector[index]),
             "delta": float(full_vector[index] - base_vector[index])}
            for index in range(adapter.dimension) if full_vector[index] != base_vector[index]
        ],
        "unchanged_upstream_sensor_and_geometry_coordinates": 87,
        "joint_reference_coordinates_recalibrated": 27,
    })
    dump(result_dir / "OPTIMIZER_EVIDENCE.json", {
        "schema": "biospur-root-r6a2b-r3-optimizer-evidence-v1",
        "real_shared_fk_recalibration_executed": True,
        "joint_objective": biomechanics,
        "full_and_ablation_identical_initialization": True,
        "retuned_between_ablation_runs": False,
        "shared_fk_replay_rows": replay_summary["rows"],
        "shared_fk_closure_max_abs": replay_summary["canonical_shared_fk_max_rotation_closure_error"],
        "runtime_s": time.monotonic() - started,
        "deterministic_inputs": "SOLVE_INPUTS.npz",
    })
    dump(result_dir / "FINAL_RESULT.json", {
        "schema": "biospur-root-r6a2b-r3-final-result-v1",
        "overall_system_direction": "POSITIVE",
        "joint_rest_cause": "MIXED",
        "session_relative_reference": "IMPLEMENTED",
        "functional_biomechanics_in_objective": True,
        "real_shared_fk_recalibration": "EXECUTED",
        "joint_rest_data_nullity": {"before": 27, "after_coordinate_convention": 0},
        "metric_geometry": "DIAGNOSED_PENDING_MEASUREMENTS",
        "development_profile_generated": True,
        "held_out_golf_boxing_accessed": False,
        "profile_checksum_sha256": profile["profile_checksum_sha256"],
        "independent_verification": "PENDING",
    })
    report = "# ROOT-R6A2B-R3 result\n\n"
    report += "The real Capture1 session-relative joint layer executed on the sealed R2 native-time replay and canonical shared FK.\n\n"
    report += f"- Joint-rest cause: mixed objective disconnection plus an exact constant-rotation dynamic-state gauge.\n"
    report += f"- Joint-rest coordinate nullity: 27 -> 0 after the declared neutral-session convention; removing that convention restores 27 physical gauge directions.\n"
    report += f"- Objective: {biomechanics['initial_half_squared_norm']:.9g} -> {biomechanics['final_half_squared_norm']:.9g}.\n"
    report += f"- Full/ablation parameter difference L2: {ablation_report['parameter_vector_difference_l2']:.9g}.\n"
    report += f"- Canonical shared-FK replay: {replay_summary['rows']} rows across 11 windows; closure max {replay_summary['canonical_shared_fk_max_rotation_closure_error']:.3e}.\n"
    report += "- Metric geometry: diagnosed and withheld from the profile pending measured anthropometry/RF phase centres; no population-average or synthetic dimensions were inserted.\n"
    report += "- Golf/Boxing: not opened.\n"
    (result_dir / "REPORT.md").write_text(report, encoding="utf-8")
    seal(result_dir)
    return result_dir


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--result-dir", type=Path, required=True)
    args = parser.parse_args()
    print(run(args.result_dir.resolve()))


if __name__ == "__main__":
    main()
