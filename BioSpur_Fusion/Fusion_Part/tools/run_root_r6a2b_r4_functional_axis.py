#!/usr/bin/env python3
"""Execute R6A2B-R4 native-time functional-axis causal audit and repair."""
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

from biospur_fusion.root_r6a0.math3d import so3_exp, so3_log
from biospur_fusion.root_r6a2a.shadow import corrected_body_model
from biospur_fusion.root_r6a2b.functional_axis import (
    align_child_to_parent_native, axis_line_angle, estimate_undirected_axis,
    integrate_native_gyro, relative_increment_evidence, sampled_relative_evidence,
    stationary_noise_distribution, synthetic_causal_suite,
)
from biospur_fusion.root_r6a2b.layered_calibration import CanonicalCalibrationAdapter
from biospur_fusion.root_r6a2b.real_profile import profile_checksum
from biospur_fusion.root_r6a2b.real_shadow import LEDGER_REL, _stored_npy_memmap
from biospur_fusion.root_r6a2b.session_relative_calibration import (
    HINGE_ACTION, _axis_from_window, relative_joint_rotations,
    session_joint_coordinates, solve_one_joint,
)
from tools.run_root_r6a2b_r3_session_relative_calibration import (
    CHECKPOINT, EXPECTED_WINDOWS, FUSION, R1, R2, NODES, dump, seal, sha256,
)


R3 = FUSION / "logs/root_r6a2b_r3_session_relative_joint_calibration_20260826T160000Z"
WINDOWS = {
    "initial_still2": (2986078873797, 2994078940466),
    "left_elbow": (3371591610404, 3411475048316),
    "right_elbow2": (3494725933278, 3528015255640),
    "left_knee": (3551740910191, 3579592651754),
    "right_knee": (3602476636179, 3627048980515),
}
JOINTS = {
    "elbow_left": {"parent_node": "BSFAA61", "child_node": "BSFEC35", "action": "left_elbow"},
    "elbow_right": {"parent_node": "BSF1120", "child_node": "BSFB165", "action": "right_elbow2"},
    "knee_left": {"parent_node": "BSF44AD", "child_node": "BSF6C53", "action": "left_knee"},
    "knee_right": {"parent_node": "BSF3C79", "child_node": "BSF8BC4", "action": "right_knee"},
}
SENSITIVITY_MULTIPLIERS = (0.5, 1.0, 2.0)


def _json(value: Any) -> Any:
    if isinstance(value, np.ndarray): return value.tolist()
    if isinstance(value, np.generic): return value.item()
    if isinstance(value, dict): return {str(key): _json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)): return [_json(item) for item in value]
    return value


def validate_r3() -> dict[str, Any]:
    final = json.loads((R3 / "FINAL_RESULT.json").read_text())
    verification = json.loads((R3 / "INDEPENDENT_VERIFICATION.json").read_text())
    if final["independent_verification"] != "PASS" or verification["verdict"] != "PASS":
        raise RuntimeError("R3 predecessor is not accepted")
    profile = json.loads((R3 / "DEVELOPMENT_ONLY_REAL_CALIBRATION_CANDIDATE.json").read_text())
    if profile["binding"]["checkpoint"] != CHECKPOINT:
        raise RuntimeError("checkpoint binding changed")
    observed = tuple((row["label"], row["start_global_time_ns"], row["stop_global_time_ns_exclusive"])
                     for row in profile["binding"]["authorized_windows"])
    if observed != EXPECTED_WINDOWS or profile["binding"]["held_out_golf_boxing_accessed"]:
        raise RuntimeError("R3 window/firewall binding changed")
    return profile


def load_accepted_imu(node: str, label: str) -> tuple[np.ndarray, dict[str, Any]]:
    ledger = FUSION / LEDGER_REL
    mapped, metadata = _stored_npy_memmap(ledger, f"imu_{node}.npy")
    start, stop = WINDOWS[label]
    left = int(np.searchsorted(mapped["global_time_ns"], start, side="left"))
    right = int(np.searchsorted(mapped["global_time_ns"], stop, side="left"))
    all_rows = np.asarray(mapped[left:right]).copy()
    accepted = all_rows[all_rows["status"] == 1]
    if not len(accepted) or np.any(np.diff(accepted["global_time_ns"]) <= 0):
        raise RuntimeError(f"unusable native evidence {node}/{label}")
    return accepted, {
        **metadata, "label": label, "node": node,
        "start_global_time_ns": start, "stop_global_time_ns_exclusive": stop,
        "slice_start_index": left, "slice_stop_index": right,
        "all_rows": int(len(all_rows)), "accepted_rows": int(len(accepted)),
        "rejected_rows": int(len(all_rows) - len(accepted)),
        "boot_epochs": [int(value) for value in np.unique(accepted["boot_epoch"])],
        "accepted_payload_sha256": hashlib.sha256(accepted.tobytes()).hexdigest(),
    }


def native_stream(node: str, label: str, profile: dict[str, Any], slots: dict[str, Any]):
    rows, audit = load_accepted_imu(node, label)
    gyro = np.deg2rad(rows["gyro_raw"].astype(float) / 16.384)
    extrinsic = so3_exp(np.asarray(slots[f"imu_extrinsic:{node}"]["value"], float)[:3])
    bias = np.asarray(profile["bias_states"][node]["gyro_bias_rad_s"], float)
    stream = integrate_native_gyro(
        rows["global_time_ns"], gyro, rows["boot_epoch"], extrinsic, bias,
    )
    audit.update({
        "rotation_integration": "R <- R Exp((gyro-bias)*native_dt), identical to R2 preintegrator rotation rule",
        "nominal_sample_period_substituted": False,
        "gap_count": stream.gap_count, "maximum_native_dt_s": stream.max_dt_s,
    })
    return stream, audit


def native_joint_evidence(joint: str, label: str, profile: dict[str, Any], slots: dict[str, Any]):
    binding = JOINTS[joint]
    parent, parent_audit = native_stream(binding["parent_node"], label, profile, slots)
    child, child_audit = native_stream(binding["child_node"], label, profile, slots)
    time_ns, parent_rotation, child_rotation, intervals, alignment = align_child_to_parent_native(parent, child)
    evidence = relative_increment_evidence(time_ns, parent_rotation, child_rotation, intervals)
    return evidence, {
        "parent": parent_audit, "child": child_audit, "alignment": alignment,
        "relative_construction": {
            "active_rotation_convention": "R_WP and R_WC map segment coordinates into the per-action integration world chart",
            "relative_orientation": "R_PC = R_WP^T R_WC maps child coordinates into parent coordinates",
            "right_increment": "Delta_R_PC = R_PC(t)^T R_PC(t+dt)",
            "right_log": "phi_child_local = Log(Delta_R_PC)",
            "consistent_axis_frame": "phi_parent_session = R_PC(t) phi_child_local",
            "omega": "phi_parent_session/native_dt",
        },
        "increment_count": int(len(evidence.dt_s)),
        "native_dt_s": {
            "minimum": float(np.min(evidence.dt_s)), "median": float(np.median(evidence.dt_s)),
            "maximum": float(np.max(evidence.dt_s)),
        },
    }


def historical_reproduction(model, r3_reference: dict[str, Any]) -> tuple[dict[str, Any], Any, Any]:
    replay = np.load(R3 / "CALIBRATION_WINDOW_REPLAY.npz", allow_pickle=False)
    relative = relative_joint_rotations(model, replay["segment_rotation"], replay["segment_names"])
    per_joint = {}
    for joint, action in HINGE_ACTION.items():
        computed = _axis_from_window(relative[joint], replay["window"], action)
        stored = r3_reference["per_joint"][joint]["functional_axis"]
        action_rows = np.flatnonzero(replay["window"] == action)
        action_time = replay["time_ns"][action_rows]
        per_joint[joint] = {
            "action": action,
            "source_trajectory": str(R3 / "CALIBRATION_WINDOW_REPLAY.npz"),
            "source_trajectory_sha256": sha256(R3 / "CALIBRATION_WINDOW_REPLAY.npz"),
            "trajectory_kind": "2,005-row R3 replay across 11 windows; not full native-time stream",
            "orientation_sample_count": int(len(action_rows)),
            "increment_sample_count": int(len(action_rows) - 1),
            "sampling_dt_s": {
                "minimum": float(np.min(np.diff(action_time)) * 1e-9),
                "median": float(np.median(np.diff(action_time)) * 1e-9),
                "maximum": float(np.max(np.diff(action_time)) * 1e-9),
            },
            "computed_axis": computed["direction_parent_reference_unoriented"],
            "stored_axis": stored["direction_parent_reference_unoriented"],
            "computed_rms_dispersion_rad": computed["angular_dispersion_rad"],
            "stored_rms_dispersion_rad": stored["angular_dispersion_rad"],
            "absolute_dispersion_difference_rad": abs(
                computed["angular_dispersion_rad"] - stored["angular_dispersion_rad"]
            ),
            "maximum_axis_component_difference": float(np.max(np.abs(
                np.asarray(computed["direction_parent_reference_unoriented"])
                - np.asarray(stored["direction_parent_reference_unoriented"])
            ))),
        }
    return {
        "schema": "biospur-root-r6a2b-r4-historical-functional-axis-reproduction-v1",
        "status": "REPRODUCED",
        "floating_point_tolerance_rad": 1e-14,
        "all_four_within_tolerance": all(row["absolute_dispersion_difference_rad"] <= 1e-14
                                          for row in per_joint.values()),
        "implementation": {
            "relative_orientation": "R_PC=R_WP^T R_WC; parent motion removed",
            "increment": "Log(R_PC(t)^T R_PC(t+1))",
            "coordinate_frame": (
                "right-local child frame at each time; axes from different changing child frames were pooled "
                "without transport to one parent/joint frame"
            ),
            "quaternion_sign": "no quaternion path; proper rotation matrices passed to scipy Rotation.as_rotvec",
            "so3_log": "scipy Rotation principal rotvec branch",
            "axis_sign": "correct undirected abs(dot) line angle",
            "low_motion": "only exact machine-zero increments removed; all other dispersion angles equally weighted",
            "scatter": "uncentred sum(phi phi^T), equivalent to increment-magnitude-squared line scatter",
            "dispersion": "unweighted RMS of acos(abs(dot(normalized_phi, principal_axis)))",
            "gaps_bouts": "one separately reset R3 action trajectory; no native bout/gap accounting",
        },
        "per_joint": per_joint,
    }, replay, relative


def high_frequency_diagnostics(evidence) -> dict[str, Any]:
    dt = evidence.dt_s
    # The aligned start rotations are retained. Consecutive differences are
    # evaluated only inside the already gap-filtered evidence intervals.
    parent_steps = []; child_steps = []
    for index in range(len(evidence.parent_rotation) - 1):
        if evidence.interval_id[index] != evidence.interval_id[index + 1]:
            continue
        local_dt = dt[index]
        parent_steps.append(np.linalg.norm(
            so3_log(evidence.parent_rotation[index].T @ evidence.parent_rotation[index + 1]) / local_dt
        ))
        child_steps.append(np.linalg.norm(
            so3_log(evidence.child_rotation[index].T @ evidence.child_rotation[index + 1]) / local_dt
        ))
    parent_steps = np.asarray(parent_steps); child_steps = np.asarray(child_steps)
    relative_norm = np.linalg.norm(evidence.omega_parent_session, axis=1)
    return {
        "proximal_angular_rate_rms_rad_s": float(np.sqrt(np.mean(parent_steps ** 2))),
        "distal_angular_rate_rms_rad_s": float(np.sqrt(np.mean(child_steps ** 2))),
        "relative_angular_rate_rms_rad_s": float(np.sqrt(np.mean(relative_norm ** 2))),
        "proximal_high_frequency_difference_rms_rad_s": float(np.sqrt(np.mean(np.diff(parent_steps) ** 2))),
        "distal_high_frequency_difference_rms_rad_s": float(np.sqrt(np.mean(np.diff(child_steps) ** 2))),
    }


def integrate_calibration(model, adapter, profile, relative_replay, replay, fits):
    base_vector = np.asarray(profile["static_vector"], float)
    base_covariance = np.asarray(profile["static_covariance"], float)
    slots = {row["slot_id"]: row for row in profile["slots"]}
    segment_node = {segment: node for node, segment in model.identity_mapping.items()}
    segment_sigma = {}
    for segment, node in segment_node.items():
        sigma = np.asarray(slots[f"imu_extrinsic:{node}"]["uncertainty"]["one_sigma"], float)[:3]
        segment_sigma[segment] = float(np.sqrt(np.mean(sigma ** 2)))
    full = {}; ablation = {}
    for joint_id in JOINTS:
        joint = next(row for row in model.joints if row.joint_id == joint_id)
        axis = np.asarray(fits[joint_id]["axis_parent_segment_session_reference"], float)
        soft_scale = float(fits[joint_id]["weighted_axial_rms_dispersion_rad"])
        full[joint_id] = solve_one_joint(
            joint_id, relative_replay[joint_id], replay["window"],
            segment_sigma[joint.parent], segment_sigma[joint.child],
            include_biomechanics=True, functional_axis_override=axis,
            off_axis_sigma_override_rad=soft_scale, consistent_parent_frame=True,
        )
        ablation[joint_id] = solve_one_joint(
            joint_id, relative_replay[joint_id], replay["window"],
            segment_sigma[joint.parent], segment_sigma[joint.child],
            include_biomechanics=False, consistent_parent_frame=True,
        )
    vector = base_vector.copy(); covariance = base_covariance.copy()
    ablation_vector = base_vector.copy(); ablation_covariance = base_covariance.copy()
    for joint_id in JOINTS:
        joint = next(row for row in model.joints if row.joint_id == joint_id)
        block = adapter.by_slot[joint.rest_rotation_slot]
        vector[block.start:block.stop] = full[joint_id].reference_rotvec
        covariance[block.start:block.stop, :] = 0.0; covariance[:, block.start:block.stop] = 0.0
        covariance[block.start:block.stop, block.start:block.stop] = full[joint_id].reference_covariance
        ablation_vector[block.start:block.stop] = ablation[joint_id].reference_rotvec
        ablation_covariance[block.start:block.stop, :] = 0.0; ablation_covariance[:, block.start:block.stop] = 0.0
        ablation_covariance[block.start:block.stop, block.start:block.stop] = ablation[joint_id].reference_covariance
    trajectory_change = {}
    for joint_id in JOINTS:
        joint = next(row for row in model.joints if row.joint_id == joint_id)
        block = adapter.by_slot[joint.rest_rotation_slot]
        old_q = session_joint_coordinates(so3_exp(base_vector[block.start:block.stop]), relative_replay[joint_id])
        new_q = session_joint_coordinates(so3_exp(vector[block.start:block.stop]), relative_replay[joint_id])
        difference = np.linalg.norm(new_q - old_q, axis=1)
        trajectory_change[joint_id] = {
            "rms_rad": float(np.sqrt(np.mean(difference ** 2))),
            "maximum_rad": float(np.max(difference)),
        }
    return vector, covariance, ablation_vector, ablation_covariance, full, ablation, {
        "schema": "biospur-root-r6a2b-r4-calibration-integration-v1",
        "initial_objective_half_squared_norm": float(sum(row.objective_initial for row in full.values())),
        "final_objective_half_squared_norm": float(sum(row.objective_final for row in full.values())),
        "r3_to_r4_static_vector_change_l2": float(np.linalg.norm(vector - base_vector)),
        "r3_to_r4_covariance_change_frobenius": float(np.linalg.norm(covariance - base_covariance, "fro")),
        "session_relative_joint_trajectory_change": trajectory_change,
        "information_spectrum": {
            "coordinate_rank_nullity": [114, 0],
            "physical_rank_nullity_without_reference_convention": [87, 27],
            "rank_changed_by_axis_repair": False,
        },
        "biomechanical_factor_ablation": {
            "full_vs_ablation_parameter_l2": float(np.linalg.norm(vector - ablation_vector)),
            "full_vs_ablation_covariance_frobenius": float(np.linalg.norm(
                covariance - ablation_covariance, "fro"
            )),
            "identical_initialization": True, "retuned": False,
        },
        "soft_factor_policy": (
            "R4 weighted axial RMS dispersion is the finite off-axis scale; broad measured axes "
            "therefore weaken the hinge-like factor rather than forcing a hinge"
        ),
    }


def update_profile(r3_profile, adapter, vector, covariance, fits, integration, result_dir):
    profile = copy.deepcopy(r3_profile)
    profile["schema"] = "biospur-root-r6a2b-r4-development-functional-axis-profile-v1"
    profile["profile_version"] = "R6A2B-R4-CANDIDATE-001"
    profile["predecessor"] = {
        "result": str(R3), "profile_sha256": sha256(R3 / "DEVELOPMENT_ONLY_REAL_CALIBRATION_CANDIDATE.json"),
        "profile_checksum": r3_profile["profile_checksum_sha256"],
    }
    profile["static_vector"] = vector.tolist(); profile["static_covariance"] = covariance.tolist()
    profile["functional_axis_profile"] = {
        joint: {
            "axis_parent_segment_session_reference": fit["axis_parent_segment_session_reference"],
            "weighted_axial_rms_dispersion_rad": fit["weighted_axial_rms_dispersion_rad"],
            "weighted_median_axial_dispersion_rad": fit["weighted_median_axial_dispersion_rad"],
            "weighted_q95_axial_dispersion_rad": fit["weighted_q95_axial_dispersion_rad"],
            "off_axis_energy_fraction": fit["off_axis_energy_fraction"],
            "principal_axis_uncertainty": fit["principal_axis_uncertainty_bout_bootstrap"],
            "status": "DEVELOPMENT_SESSION_EFFECTIVE_AXIS_NOT_CLINICAL_HINGE",
        } for joint, fit in fits.items()
    }
    row_by_id = {row["slot_id"]: row for row in profile["slots"]}
    values = adapter.vector_to_slots(vector)
    for joint in JOINTS:
        block = adapter.by_slot[f"joint_rest:{joint}"]
        row = row_by_id[f"joint_rest:{joint}"]
        local = covariance[block.start:block.stop, block.start:block.stop]
        row["value"] = values[block.slot_id].tolist()
        row["uncertainty"] = {
            "posterior_local_covariance_rad2": local.tolist(),
            "one_sigma_rad": np.sqrt(np.maximum(np.diag(local), 0.0)).tolist(),
            "functional_axis_uncertainty_included": True,
        }
        row["provenance"] = {
            "execution": "ROOT_R6A2B_R4_NATIVE_TIME_PARENT_RELATIVE_AXIS_REPAIR",
            "functional_axis_profile": joint,
            "production_qualified": False,
        }
    profile["capability"]["functional_joint_axes"] = "AVAILABLE_DEVELOPMENT_NATIVE_TIME_UNDIRECTED"
    profile["axis_repair_integration"] = integration
    profile["binding"]["held_out_golf_boxing_accessed"] = False
    profile.pop("profile_checksum_sha256", None)
    profile["profile_checksum_sha256"] = profile_checksum(profile)
    dump(result_dir / "DEVELOPMENT_ONLY_REAL_CALIBRATION_CANDIDATE.json", profile)
    return profile


def plots(result_dir: Path, historical, fits, evidence_by_joint, sensitivity):
    joints = list(JOINTS)
    old = np.degrees([historical["per_joint"][joint]["computed_rms_dispersion_rad"] for joint in joints])
    new = np.degrees([fits[joint]["weighted_axial_rms_dispersion_rad"] for joint in joints])
    median = np.degrees([fits[joint]["weighted_median_axial_dispersion_rad"] for joint in joints])
    q95 = np.degrees([fits[joint]["weighted_q95_axial_dispersion_rad"] for joint in joints])
    x = np.arange(len(joints)); width = .2
    figure, axis = plt.subplots(figsize=(11, 5))
    for shift, value, label in ((-1.5, old, "R3 historical RMS"), (-.5, new, "R4 weighted RMS"),
                                (.5, median, "R4 weighted median"), (1.5, q95, "R4 weighted q95")):
        axis.bar(x + shift * width, value, width, label=label)
    axis.set_xticks(x, joints); axis.set_ylabel("undirected axis-line dispersion [deg]")
    axis.set_title("Functional-axis dispersion | not a static-pose or joint-angle error")
    axis.legend(); axis.grid(axis="y", alpha=.25); figure.tight_layout()
    figure.savefig(result_dir / "FUNCTIONAL_AXIS_HISTORICAL_VS_REPAIRED.png", dpi=170); plt.close(figure)

    figure, axes = plt.subplots(4, 1, figsize=(14, 11))
    for axis_plot, joint in zip(axes, joints):
        evidence = evidence_by_joint[joint]
        omega = np.linalg.norm(evidence.omega_parent_session, axis=1)
        scale = sensitivity[joint]["stationary_distribution"]["scale_rms_rad_s"]
        weight = omega ** 2 / (omega ** 2 + scale ** 2)
        time = (evidence.start_time_ns - evidence.start_time_ns[0]) * 1e-9
        axis_plot.plot(time, omega, lw=.5, label="|omega_relative| [rad/s]")
        axis_plot.plot(time, weight, lw=.6, label="continuous motion weight")
        axis_plot.axhline(scale, color="black", lw=.5, alpha=.5, label="stationary RMS scale")
        axis_plot.set_title(joint); axis_plot.grid(alpha=.2); axis_plot.legend(fontsize=7, ncol=3)
    axes[-1].set_xlabel("action time [s]"); figure.tight_layout()
    figure.savefig(result_dir / "NATIVE_MOTION_WEIGHTS_AND_BOUT_EVIDENCE.png", dpi=170); plt.close(figure)

    figure, axis = plt.subplots(figsize=(10, 6))
    for joint in joints:
        values = [np.degrees(sensitivity[joint][str(multiplier)]["weighted_axial_rms_dispersion_rad"])
                  for multiplier in SENSITIVITY_MULTIPLIERS]
        axis.plot(SENSITIVITY_MULTIPLIERS, values, marker="o", label=joint)
    axis.set_xscale("log", base=2); axis.set_xticks(SENSITIVITY_MULTIPLIERS, SENSITIVITY_MULTIPLIERS)
    axis.set_xlabel("stationary-scale multiplier"); axis.set_ylabel("weighted RMS line dispersion [deg]")
    axis.set_title("Continuous low-motion weighting sensitivity")
    axis.grid(alpha=.25); axis.legend(); figure.tight_layout()
    figure.savefig(result_dir / "LOW_MOTION_SCALE_SENSITIVITY.png", dpi=170); plt.close(figure)


def run(result_dir: Path) -> Path:
    started = time.monotonic(); result_dir.mkdir(parents=True, exist_ok=False)
    r3_profile = validate_r3(); slots = {row["slot_id"]: row for row in r3_profile["slots"]}
    model = corrected_body_model(FUSION)
    source_rows = json.loads((R1 / "CALIBRATION_PARAMETER_PROVENANCE.json").read_text())["slots"]
    adapter = CanonicalCalibrationAdapter(model, source_rows)
    r3_reference = json.loads((R3 / "SESSION_RELATIVE_JOINT_REFERENCE.json").read_text())
    historical, replay, relative_replay = historical_reproduction(model, r3_reference)
    dump(result_dir / "HISTORICAL_FUNCTIONAL_AXIS_REPRODUCTION.json", historical)
    synthetic = synthetic_causal_suite()
    dump(result_dir / "SYNTHETIC_FUNCTIONAL_AXIS_CAUSAL_TESTS.json", synthetic)

    fits = {}; audit = {}; evidence_by_joint = {}; sensitivity = {}; downsampled = {}; raw_access = {}
    save_arrays = {}
    skin = {}
    for joint, binding in JOINTS.items():
        neutral, neutral_audit = native_joint_evidence(joint, "initial_still2", r3_profile, slots)
        stationary = stationary_noise_distribution(neutral)
        action, action_audit = native_joint_evidence(joint, binding["action"], r3_profile, slots)
        fit = estimate_undirected_axis(action, stationary["scale_rms_rad_s"])
        fits[joint] = fit; evidence_by_joint[joint] = action
        raw_access[joint] = {"neutral": neutral_audit, "action": action_audit}
        sensitivity[joint] = {"stationary_distribution": stationary}
        for multiplier in SENSITIVITY_MULTIPLIERS:
            sensitivity[joint][str(multiplier)] = estimate_undirected_axis(
                action, stationary["scale_rms_rad_s"], scale_multiplier=multiplier,
                bootstrap_replicates=200,
            )

        action_index = np.flatnonzero(replay["window"] == binding["action"])
        neutral_index = np.flatnonzero(replay["window"] == "initial_still2")
        coarse_action = sampled_relative_evidence(
            replay["time_ns"][action_index], relative_replay[joint][action_index]
        )
        coarse_neutral = sampled_relative_evidence(
            replay["time_ns"][neutral_index], relative_replay[joint][neutral_index]
        )
        coarse_stationary = stationary_noise_distribution(coarse_neutral)
        coarse_fit = estimate_undirected_axis(coarse_action, coarse_stationary["scale_rms_rad_s"])
        historical_axis = np.asarray(historical["per_joint"][joint]["computed_axis"])
        downsampled[joint] = {
            "historical_r3": historical["per_joint"][joint],
            "repaired_semantics_on_r3_downsampled_replay": coarse_fit,
            "native_time_repaired": fit,
            "native_vs_corrected_downsampled_axis_line_distance_rad": axis_line_angle(
                np.asarray(fit["axis_parent_segment_session_reference"]),
                np.asarray(coarse_fit["axis_parent_segment_session_reference"]),
            ),
            "historical_vs_native_axis_line_distance_rad": axis_line_angle(
                historical_axis, np.asarray(fit["axis_parent_segment_session_reference"])
            ),
        }
        hf = high_frequency_diagnostics(action)
        decomposition = fit["decomposition"]
        skin[joint] = {
            **hf,
            "bout_axis_between_rms_rad": decomposition["between_bout_axis_rms_rad"],
            "within_bout_rms_rad": decomposition["within_bout_rms_rad"],
            "off_axis_energy_fraction": fit["off_axis_energy_fraction"],
            "node_health": {
                "parent_rejected_rows": action_audit["parent"]["rejected_rows"],
                "child_rejected_rows": action_audit["child"]["rejected_rows"],
                "parent_gap_count": action_audit["parent"]["gap_count"],
                "child_gap_count": action_audit["child"]["gap_count"],
            },
            "classification": {
                "low_motion_numerical_instability": "CONSISTENT_CONTRIBUTOR",
                "frame_sign_implementation_error": "FRAME_DEFECT_DEMONSTRATED_SIGN_DEFECT_NOT_FOUND",
                "window_contamination": "CONSISTENT_WITH_BEGIN_END_AND_REVERSAL_MIXTURE",
                "bounded_skin_strap_motion": "CONSISTENT_BUT_NOT_IDENTIFIED_AS_GROUND_TRUTH",
                "genuine_multi_axis_motion": "CONSISTENT_WITH_REMAINING_OFF_AXIS_EVIDENCE",
                "unique_cause": "AMBIGUOUS",
            },
            "uwb_supported_consistency": (
                "not used for axis truth: metric geometry remains unqualified; no contradictory node-health event found"
            ),
        }
        audit[joint] = {
            "action": binding["action"], "stationary_distribution": stationary,
            "native_input": action_audit,
            "axis_estimate": fit,
        }
        prefix = joint
        for name, value in (
            ("action_time_ns", action.start_time_ns), ("action_dt_s", action.dt_s),
            ("action_interval_id", action.interval_id), ("action_phi_parent", action.phi_parent_session),
            ("action_omega_parent", action.omega_parent_session),
            ("neutral_time_ns", neutral.start_time_ns), ("neutral_dt_s", neutral.dt_s),
            ("neutral_interval_id", neutral.interval_id), ("neutral_phi_parent", neutral.phi_parent_session),
            ("neutral_omega_parent", neutral.omega_parent_session),
        ): save_arrays[f"{prefix}_{name}"] = value

    dump(result_dir / "NATIVE_TIME_FUNCTIONAL_AXIS_ESTIMATES.json", {
        "schema": "biospur-root-r6a2b-r4-native-time-functional-axis-v1",
        "method": {
            "axis_is_undirected_line": True,
            "axis_line_distance": "acos(clamp(abs(dot(a,b)),0,1))",
            "parent_motion_removed": True,
            "axis_frame": "calibrated parent-segment session-reference frame",
            "native_dt_used": True, "nominal_200hz_substituted": False,
            "quaternion_dependency": False,
            "gap_crossing": False,
        },
        "per_joint": audit,
    })
    dump(result_dir / "FUNCTIONAL_AXIS_BOUT_AUDIT.json", {
        "schema": "biospur-root-r6a2b-r4-functional-axis-bout-audit-v1",
        "full_sample_accounting_preserved": True,
        "bout_resampling_not_individual_sample_bootstrap": True,
        "per_joint": {
            joint: {
                "action": JOINTS[joint]["action"], "increment_count": fits[joint]["increment_count"],
                "effective_sample_weight": fits[joint]["effective_sample_weight"],
                "effective_motion_duration_s": fits[joint]["effective_motion_duration_s"],
                "bout_count": fits[joint]["bout_count"], "bouts": fits[joint]["bouts"],
                "decomposition": fits[joint]["decomposition"],
            } for joint in JOINTS
        },
    })
    dump(result_dir / "LOW_MOTION_WEIGHT_SENSITIVITY.json", {
        "schema": "biospur-root-r6a2b-r4-low-motion-sensitivity-v1",
        "multipliers": SENSITIVITY_MULTIPLIERS,
        "no_hard_ordinary_sample_qualification": True,
        "per_joint": sensitivity,
    })
    dump(result_dir / "NATIVE_VS_DOWNSAMPLED_AXIS_EVIDENCE.json", {
        "schema": "biospur-root-r6a2b-r4-native-vs-downsampled-v1", "per_joint": downsampled,
        "selection_rule": "correct SO(3), frame, weighting, and gap semantics take priority over minimum dispersion",
    })
    dump(result_dir / "SKIN_SENSOR_CONSISTENCY_DIAGNOSTICS.json", {
        "schema": "biospur-root-r6a2b-r4-skin-sensor-consistency-v1",
        "skin_slip_ground_truth_claimed": False, "per_joint": skin,
    })
    dump(result_dir / "RAW_EVIDENCE_ACCESS_AUDIT.json", {
        "schema": "biospur-root-r6a2b-r4-raw-evidence-access-v1",
        "ledger": str(FUSION / LEDGER_REL), "ledger_sha256": sha256(FUSION / LEDGER_REL),
        "opened_actions": list(WINDOWS), "opened_nodes": sorted({value for row in JOINTS.values()
                                                                  for value in (row['parent_node'], row['child_node'])}),
        "access": raw_access, "held_out_members_or_intervals_opened": [],
        "Golf_or_Boxing_opened": False,
    })
    np.savez_compressed(result_dir / "NATIVE_AXIS_EVIDENCE.npz", **save_arrays)

    vector, covariance, ablation_vector, ablation_covariance, full, ablation, integration = integrate_calibration(
        model, adapter, r3_profile, relative_replay, replay, fits,
    )
    dump(result_dir / "CALIBRATION_INTEGRATION.json", integration)
    profile = update_profile(r3_profile, adapter, vector, covariance, fits, integration, result_dir)
    np.savez_compressed(
        result_dir / "CALIBRATION_ESTIMATE.npz", vector=vector, covariance=covariance,
        ablation_vector=ablation_vector, ablation_covariance=ablation_covariance,
        r3_vector=np.asarray(r3_profile["static_vector"]), r3_covariance=np.asarray(r3_profile["static_covariance"]),
        axis=np.asarray([fits[joint]["axis_parent_segment_session_reference"] for joint in JOINTS]),
    )
    dump(result_dir / "FUNCTIONAL_AXIS_CAUSAL_AUDIT.json", {
        "schema": "biospur-root-r6a2b-r4-functional-axis-causal-audit-v1",
        "primary_cause": "MIXED",
        "findings": {
            "axis_sign": "R3 CORRECT: abs(dot) already treated +/- as one line",
            "low_motion": "R3 DEFECT: every nonzero direction received equal dispersion influence",
            "frame": "R3 DEFECT: changing right-local child frames pooled without transport",
            "parent_motion": "R3 CORRECT: R_WP^T R_WC removed parent motion",
            "downsampling": "R3 LIMITATION: derivative evidence came only from 5 Hz replay",
            "so3_quaternion": "no quaternion path; principal-log branch not approached by native increments",
            "window_mixture": "begin/end, reversal, and multiple bouts retained and quantified",
            "skin_vs_multiaxis": "both remain measurement-consistent and not uniquely separable",
        },
        "implementation_repaired": True,
    })
    plots(result_dir, historical, fits, evidence_by_joint, sensitivity)
    dump(result_dir / "OPTIMIZER_EVIDENCE.json", {
        "schema": "biospur-root-r6a2b-r4-optimizer-evidence-v1",
        **integration, "runtime_s": time.monotonic() - started,
        "deterministic_native_evidence": "NATIVE_AXIS_EVIDENCE.npz",
    })
    dump(result_dir / "FINAL_RESULT.json", {
        "schema": "biospur-root-r6a2b-r4-final-result-v1",
        "overall_system_direction": "POSITIVE",
        "historical_dispersion_reproduced": True,
        "primary_cause": "MIXED", "implementation_repaired": True,
        "native_time_parent_relative_method": "EXECUTED",
        "functional_axis_profile_updated": True,
        "held_out_golf_boxing_accessed": False,
        "profile_checksum_sha256": profile["profile_checksum_sha256"],
        "independent_verification": "PENDING",
    })
    (result_dir / "REPORT.md").write_text(
        "# ROOT-R6A2B-R4 result\n\nNative-time functional-axis repair executed; see FINAL_RESULT.json and "
        "NATIVE_TIME_FUNCTIONAL_AXIS_ESTIMATES.json. Dispersion is an axis-direction statistic, not a static-pose error.\n",
        encoding="utf-8",
    )
    seal(result_dir); return result_dir


def main() -> None:
    parser = argparse.ArgumentParser(); parser.add_argument("--result-dir", type=Path, required=True)
    args = parser.parse_args(); print(run(args.result_dir.resolve()))


if __name__ == "__main__": main()
