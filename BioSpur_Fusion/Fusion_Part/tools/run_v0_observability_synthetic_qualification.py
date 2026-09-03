#!/usr/bin/env python3
"""Run and preserve the observability-first Milestone-B synthetic gate."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import sys
from typing import Any

import numpy as np
from scipy.spatial.transform import Rotation

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from biospur_fusion.v0.synthetic_qualification import (  # noqa: E402
    action_ablations,
    array_hash,
    b3_information_gain,
    b4_information_gain,
    b5_information_gain,
    directional_jv_check,
    maximum_profile_disagreement,
    multidimensional_action_causality,
    product_state_error,
    rank_lineage,
    recovery_record,
    sign_handedness_alternatives,
)
from biospur_fusion.v0.unified_calibration import (  # noqa: E402
    B5_ACTION_JOINTS,
    B5_JOINT_EDGES,
    B5_LEVER_ENDPOINTS,
    FULL_DIMENSION,
    POSE_NUISANCE_DIMENSION,
    PRODUCT_DIMENSION,
    UnifiedCalibrationObjective,
    _b5_lever_matrix,
    decode_full,
    fit_multistart,
    production_jacobian,
    qualified_initializations,
)
from biospur_fusion.v0.unified_synthetic import (  # noqa: E402
    B3_ACTIONS,
    B3_HIP_CIRCUMDUCTION,
    B3_KNEE_LEFT,
    B3_KNEE_RIGHT,
    B3_TRUNK_LATERAL,
    B4_EN_BLOC,
    B4_NEGATIVE_CONTROLS,
    B5_NEGATIVE_CONTROLS,
    generate_unified_case,
)


CONTRACT_PATH = ROOT / "config/biospur_fusion_v0_observability_first/SYNTHETIC_QUALIFICATION_CONTRACT.json"
CAUSALITY_CONTRACT_PATH = ROOT / "config/biospur_fusion_v0_observability_first/B5_MULTIDIMENSIONAL_CAUSALITY_CONTRACT.json"
FROZEN_CAUSALITY_CONTRACT_SHA256 = "effd01e1b00118bbfb781f797446177a4cddd87e711156591f2b8421ef8698b3"
CAUSALITY_SELECTOR_CORRECTION_PATH = ROOT / "config/biospur_fusion_v0_observability_first/B5_CAUSALITY_SELECTOR_CORRECTION.json"
FROZEN_CAUSALITY_SELECTOR_CORRECTION_SHA256 = "f08db6a020158339fb2466f7d34001fd7ab1098f8211925c6c2b36bca31d8698"


def jsonable(value: Any) -> Any:
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, (np.integer, np.floating)):
        return value.item()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(item) for item in value]
    return value


def dump(path: Path, value: Any) -> None:
    path.write_text(json.dumps(jsonable(value), indent=2, sort_keys=True) + "\n")


def _principal_direction(values: np.ndarray) -> np.ndarray:
    _, _, vh = np.linalg.svd(np.asarray(values, dtype=float), full_matrices=False)
    return vh[0] / max(float(np.linalg.norm(vh[0])), 1e-12)


def _truth_world_omega(
    objective: UnifiedCalibrationObjective,
    metadata: dict[str, Any],
) -> np.ndarray:
    """Truth-side synthetic challenge audit only; never estimator input."""

    world = np.asarray(metadata["world_segment_rotation"], dtype=float)
    world_omega = np.zeros((len(world), len(objective.obs.node_order), 3))
    for row in range(1, len(world)):
        dt = (objective.obs.time_ns[row] - objective.obs.time_ns[row - 1]) / 1e9
        local = Rotation.from_matrix(
            np.einsum("sji,sjk->sik", world[row - 1], world[row])
        ).as_rotvec() / dt
        world_omega[row] = np.einsum("sij,sj->si", world[row - 1], local)
    return world_omega


def b3_excitation_audit(
    objective: UnifiedCalibrationObjective,
    metadata: dict[str, Any],
    gates: dict[str, Any],
) -> dict[str, Any]:
    """Truth-side eligibility of the synthetic test motion, never the fit.

    Synthetic truth is permitted only to prove that the generated challenge
    actually contains the requested excitation.  These values do not enter
    objective construction, initialization, rank, residuals, or recovery.
    Evaluating eligibility through a fitted profile would circularly make a
    wrong basin look like a deficient protocol.
    """

    world = np.asarray(metadata["world_segment_rotation"], dtype=float)
    world_omega = _truth_world_omega(objective, metadata)

    def omega(segment: str, rows: np.ndarray) -> np.ndarray:
        return world_omega[rows, objective.segment_index[segment]]

    hip = {}
    for joint in ("hip_L", "hip_R"):
        parent, child = ("pelvis", "thigh_L" if joint.endswith("L") else "thigh_R")
        rows = objective._rows(B3_HIP_CIRCUMDUCTION, (parent, child))
        relative = omega(child, rows) - omega(parent, rows)
        singular = np.linalg.svd(relative, compute_uv=False)
        hip[joint] = {
            "relative_omega_singular_values": singular,
            "second_to_first_singular_ratio": float(singular[1] / max(singular[0], 1e-15)),
            "third_to_second_singular_ratio": float(singular[2] / max(singular[1], 1e-15)),
        }

    knees = {}
    for action, joint in ((B3_KNEE_LEFT, "knee_L"), (B3_KNEE_RIGHT, "knee_R")):
        parent, child = ("thigh_L", "shank_L") if joint.endswith("L") else ("thigh_R", "shank_R")
        rows = objective._rows(action, (parent, child))
        midpoint = objective.obs.windows[action][0] + (objective.obs.windows[action][1] - objective.obs.windows[action][0]) // 2
        flex_rows = rows[objective.obs.time_ns[rows] <= midpoint]
        axial_rows = rows[objective.obs.time_ns[rows] > midpoint]
        flex = omega(child, flex_rows) - omega(parent, flex_rows)
        axial = omega(child, axial_rows) - omega(parent, axial_rows)
        flex_axis = _principal_direction(flex)
        axial_axis = _principal_direction(axial)
        angle = math.degrees(math.acos(float(np.clip(abs(flex_axis @ axial_axis), -1.0, 1.0))))
        thigh_gyro = np.linalg.norm(omega(parent, rows), axis=1)
        shank_gyro = np.linalg.norm(omega(child, rows), axis=1)
        episode = objective.obs.r3d_actions[action]["EPISODE_PHASE_ROWS"]
        rest_rows = np.asarray(
            episode["PRE_REST"] + episode["POST_REST"], dtype=int,
        )
        rest_rows = rest_rows[
            objective.obs.valid[rest_rows, objective.segment_index[parent]]
            & objective.obs.valid[rest_rows, objective.segment_index[child]]
        ]
        thigh_rest = np.linalg.norm(omega(parent, rest_rows), axis=1)
        shank_rest = np.linalg.norm(omega(child, rest_rows), axis=1)
        thigh_q90 = float(np.quantile(thigh_gyro, 0.90))
        shank_q90 = float(np.quantile(shank_gyro, 0.90))
        thigh_noise_q90 = float(np.quantile(thigh_rest, 0.90))
        shank_noise_q90 = float(np.quantile(shank_rest, 0.90))
        thigh_excess = math.sqrt(max(thigh_q90 * thigh_q90 - thigh_noise_q90 * thigh_noise_q90, 0.0))
        shank_excess = math.sqrt(max(shank_q90 * shank_q90 - shank_noise_q90 * shank_noise_q90, 0.0))
        knees[joint] = {
            "flexion_to_tibial_axial_principal_angle_deg": angle,
            "thigh_activity_q90_rad_s": thigh_q90,
            "shank_activity_q90_rad_s": shank_q90,
            "thigh_rest_noise_q90_rad_s": thigh_noise_q90,
            "shank_rest_noise_q90_rad_s": shank_noise_q90,
            "noise_floor_corrected_thigh_activity_rad_s": thigh_excess,
            "noise_floor_corrected_shank_activity_rad_s": shank_excess,
            "stationary_thigh_activity_ratio": float(thigh_excess / max(shank_excess, 1e-15)),
            "stationary_thigh_activity_ratio_definition": "sqrt(max(q90_active^2-q90_same_rest^2,0)) thigh/shank",
        }

    rows = objective._rows(B3_TRUNK_LATERAL, ("pelvis", "torso"))
    start, stop = objective.obs.windows[B3_TRUNK_LATERAL]
    fraction = (objective.obs.time_ns[rows] - start) / max(stop - start, 1)
    sign = np.zeros(len(rows))
    sign[(fraction > 0.15) & (fraction < 0.30)] = 1.0
    sign[(fraction >= 0.30) & (fraction < 0.45)] = -1.0
    sign[(fraction > 0.55) & (fraction < 0.70)] = -1.0
    sign[(fraction >= 0.70) & (fraction < 0.85)] = 1.0
    keep = sign != 0.0
    relative = omega("torso", rows[keep]) - omega("pelvis", rows[keep])
    axis = world[rows[keep], objective.segment_index["pelvis"], :, 1]
    signed_projection = sign[keep] * np.einsum("ni,ni->n", relative, axis)
    activity = np.linalg.norm(relative, axis=1)
    active = activity > float(objective.contract["measurement_covariance"]["bilateral_activation_scale_rad_s"])
    trunk_agreement = float(np.mean(signed_projection[active] > 0.0)) if np.any(active) else 0.0

    minimum_hip_ratio = float(gates["minimum_b3_two_axis_singular_ratio"])
    maximum_thigh_ratio = float(gates["maximum_b3_stationary_thigh_activity_ratio"])
    minimum_knee_angle = float(gates["minimum_b3_noncollinear_axis_angle_deg"])
    minimum_label_agreement = float(gates["minimum_b3_labelled_direction_agreement_fraction"])
    phase_and_closure = metadata["b3_episode_audit"]
    passed = (
        all(item["second_to_first_singular_ratio"] >= minimum_hip_ratio for item in hip.values())
        and all(item["stationary_thigh_activity_ratio"] <= maximum_thigh_ratio for item in knees.values())
        and all(item["flexion_to_tibial_axial_principal_angle_deg"] >= minimum_knee_angle for item in knees.values())
        and trunk_agreement >= minimum_label_agreement
        and all(item["complete_rest_transition_action_return_same_rest"] for item in phase_and_closure.values())
    )
    return {
        "eligibility_lineage": "SYNTHETIC_TRUTH_SIDE_TEST_CASE_KINEMATICS_ONLY",
        "estimator_firewall": "not used by objective, initialization, rank, residual evaluation, or recovery",
        "hip_circumduction": hip,
        "seated_knee_flexion_axial": knees,
        "trunk_lateral": {
            "active_labelled_row_count": int(np.sum(active)),
            "signed_direction_agreement_fraction": trunk_agreement,
        },
        "episode_phase_and_same_rest": phase_and_closure,
        "label_distinctions": metadata["b3_label_distinctions"],
        "pass": passed,
    }


def b4_excitation_audit(
    objective: UnifiedCalibrationObjective,
    metadata: dict[str, Any],
    gates: dict[str, Any],
) -> dict[str, Any]:
    """Truth-side proof that the synthetic B4 challenge is eligible."""

    world_omega = _truth_world_omega(objective, metadata)
    phase_rows = objective.obs.r3d_actions[B4_EN_BLOC]["B4_COMMON_RATE_PHASE_ROWS"]
    selected = np.concatenate([
        np.asarray(phase_rows[phase], dtype=int)
        for phase in ("COMMON_ROTATION_AXIS_A", "COMMON_ROTATION_AXIS_B")
    ])
    pelvis_index = objective.segment_index["pelvis"]
    pelvis = world_omega[selected, pelvis_index]
    singular = np.linalg.svd(pelvis, compute_uv=False)
    common_q90 = float(np.quantile(np.linalg.norm(pelvis, axis=1), 0.90))
    relative = {}
    for segment in objective.segment_index:
        if segment == "pelvis":
            continue
        values = world_omega[selected, objective.segment_index[segment]] - pelvis
        q90 = float(np.quantile(np.linalg.norm(values, axis=1), 0.90))
        relative[segment] = {
            "relative_to_pelvis_q90_rad_s": q90,
            "relative_to_common_q90_ratio": q90 / max(common_q90, 1e-15),
        }
    valid = bool(np.all(objective.obs.valid[selected]))
    second_ratio = float(singular[1] / max(singular[0], 1e-15))
    episode = metadata["b4_episode_audit"]
    passed = (
        common_q90 >= float(gates["minimum_b4_common_rate_q90_rad_s"])
        and second_ratio >= float(gates["minimum_b4_two_axis_singular_ratio"])
        and all(
            item["relative_to_common_q90_ratio"]
            <= float(gates["maximum_b4_segment_relative_to_pelvis_q90_ratio"])
            for item in relative.values()
        )
        and valid
        and episode["complete_rest_transition_action_return_same_rest"]
    )
    return {
        "eligibility_lineage": "SYNTHETIC_TRUTH_SIDE_TEST_CASE_KINEMATICS_ONLY",
        "estimator_firewall": "truth axes and commanded motion are absent from objective, initialization, rank, residual evaluation, and recovery",
        "negative_control": metadata["b4_negative_control"],
        "phase_row_counts": metadata["b4_phase_row_counts"],
        "common_rate_singular_values": singular,
        "common_rate_second_to_first_singular_ratio": second_ratio,
        "common_rate_q90_rad_s": common_q90,
        "per_segment_rigidity": relative,
        "all_ten_segments_valid": valid,
        "episode_and_same_rest": episode,
        "pass": passed,
    }


def b4_negative_control_audit(contract: dict[str, Any]) -> dict[str, Any]:
    """Require all declared B4 failure modes to fail closed."""

    seed = int(contract["synthetic_cases"]["b4_negative_control_seed"])
    records = []
    for control in B4_NEGATIVE_CONTROLS:
        observation, truth, metadata = generate_unified_case(
            contract,
            seed,
            noisy=control == "noisy_near_zero_rates",
            b4_control=control,
        )
        objective = UnifiedCalibrationObjective(observation, contract)
        audit = b4_excitation_audit(objective, metadata, contract["recovery_gates"])
        blocks = [block for block in objective.blocks(truth, True) if block.action == B4_EN_BLOC]
        cost = float(sum(
            np.sum(np.sqrt(1.0 + block.values * block.values) - 1.0)
            for block in blocks
        ))
        maximum_rigidity_ratio = max(
            item["relative_to_common_q90_ratio"]
            for item in audit["per_segment_rigidity"].values()
        )
        if control in ("segment_articulation", "timing_mismatch"):
            mechanism_detected = (
                maximum_rigidity_ratio
                > float(contract["recovery_gates"]["maximum_b4_segment_relative_to_pelvis_q90_ratio"])
                and cost >= float(contract["recovery_gates"]["minimum_b4_negative_control_factor_cost"])
            )
        elif control == "insufficient_second_axis":
            mechanism_detected = (
                audit["common_rate_second_to_first_singular_ratio"]
                < float(contract["recovery_gates"]["minimum_b4_two_axis_singular_ratio"])
            )
        else:
            mechanism_detected = (
                audit["common_rate_q90_rad_s"]
                < float(contract["recovery_gates"]["minimum_b4_common_rate_q90_rad_s"])
            )
        records.append({
            "control": control,
            "expected_rejection": True,
            "eligibility_pass": audit["pass"],
            "mechanism_detected": bool(mechanism_detected),
            "b4_measurement_factor_robust_cost_at_truth": cost,
            "maximum_segment_rigidity_ratio": maximum_rigidity_ratio,
            "common_rate_second_to_first_singular_ratio": audit["common_rate_second_to_first_singular_ratio"],
            "common_rate_q90_rad_s": audit["common_rate_q90_rad_s"],
        })
    return {
        "records": records,
        "pass": all(not item["eligibility_pass"] and item["mechanism_detected"] for item in records),
    }


def _b5_best_lag(parent: np.ndarray, child: np.ndarray, maximum: int = 5) -> int:
    scores = []
    for lag in range(-maximum, maximum + 1):
        if lag < 0:
            first, second = parent[-lag:], child[:lag]
        elif lag > 0:
            first, second = parent[:-lag], child[lag:]
        else:
            first, second = parent, child
        scores.append((float(np.mean((first - second) ** 2)), lag))
    return int(min(scores)[1])


def b5_excitation_audit(
    objective: UnifiedCalibrationObjective,
    truth: np.ndarray,
    metadata: dict[str, Any],
    gates: dict[str, Any],
) -> dict[str, Any]:
    """Truth-side challenge audit, firewalled from fitting and rank claims."""

    product, nuisance = decode_full(truth)
    per_edge = {}
    all_identifiable = []
    dynamic_values = []
    lags = []
    for joint, (parent, child) in B5_JOINT_EDGES.items():
        designs = []
        for action, joints in B5_ACTION_JOINTS.items():
            if joint not in joints:
                continue
            rows = objective._rows(action, (parent, child))
            rows = rows[(rows > 0) & (rows + 1 < len(objective.obs.time_ns))]
            if not len(rows):
                continue
            rp = objective.corrected_rotation(product, parent, rows)
            rc = objective.corrected_rotation(product, child, rows)
            wp = objective.corrected_omega(product, parent, rows)
            wc = objective.corrected_omega(product, child, rows)
            ap = objective.corrected_alpha(product, parent, rows)
            ac = objective.corrected_alpha(product, child, rows)
            mp = _b5_lever_matrix(rp, wp, ap)
            mc = _b5_lever_matrix(rc, wc, ac)
            designs.append(np.concatenate((mp, -mc), axis=2).reshape(-1, 6))
            rpw = np.einsum("nij,j->ni", rp, nuisance["lever_arms"][(joint, parent)])
            rcw = np.einsum("nij,j->ni", rc, nuisance["lever_arms"][(joint, child)])
            parent_joint = (
                objective.corrected_specific_force(product, parent, rows)
                + np.cross(ap, rpw) + np.cross(wp, np.cross(wp, rpw))
            )
            child_joint = (
                objective.corrected_specific_force(product, child, rows)
                + np.cross(ac, rcw) + np.cross(wc, np.cross(wc, rcw))
            )
            dynamic_values.append(parent_joint - np.median(parent_joint, axis=0))
            lags.append(_b5_best_lag(parent_joint, child_joint))
        design = np.concatenate(designs)
        singular = np.linalg.svd(design, compute_uv=False)
        threshold = max(
            float(gates["b5_lever_design_noise_floor_singular_value"]),
            float(singular[0]) * 1e-7,
        )
        rank = int(np.sum(singular > threshold))
        identifiable = singular[:rank]
        all_identifiable.extend(identifiable.tolist())
        per_edge[joint] = {
            "rank": rank,
            "nullity": 6 - rank,
            "singular_values": singular,
        }
    lever_rank = int(sum(item["rank"] for item in per_edge.values()))
    lever_nullity = 3 * len(B5_LEVER_ENDPOINTS) - lever_rank
    identifiable_ratio = (
        float(min(all_identifiable) / max(all_identifiable))
        if all_identifiable else 0.0
    )
    dynamic = np.concatenate(dynamic_values)
    dynamic_q90 = float(np.quantile(np.linalg.norm(dynamic, axis=1), 0.90))

    selected_rows = np.unique(np.concatenate([
        objective._rows(action, tuple({segment for joint in joints for segment in B5_JOINT_EDGES[joint]}))
        for action, joints in B5_ACTION_JOINTS.items()
    ]))
    selected_rows = selected_rows[(selected_rows > 0) & (selected_rows + 1 < len(objective.obs.time_ns))]
    dt = np.median(np.diff(objective.obs.time_ns)).item() / 1e9
    gyro_second = (
        objective.obs.gyro_rad_s[selected_rows + 1]
        - 2.0 * objective.obs.gyro_rad_s[selected_rows]
        + objective.obs.gyro_rad_s[selected_rows - 1]
    ) / max(dt, 1e-12)
    gyro_noise = float(np.quantile(np.linalg.norm(gyro_second, axis=2), 0.50))
    blocks = [
        block for block in objective.blocks(truth, False)
        if block.factor.startswith("b5_joint_center_specific_force_closure")
    ]
    robust_cost = float(sum(
        np.sum(np.sqrt(1.0 + block.values * block.values) - 1.0)
        for block in blocks
    ))
    scalar_count = int(sum(len(block.values) for block in blocks))
    cost_per_scalar = robust_cost / max(scalar_count, 1)
    maximum_lag = int(max(abs(value) for value in lags))
    passed = (
        dynamic_q90 >= float(gates["minimum_b5_dynamic_joint_acceleration_q90_mps2"])
        and lever_rank == int(gates["required_b5_measured_lever_subspace_rank"])
        and lever_nullity == int(gates["expected_b5_lever_nuisance_gauge_nullity"])
        and identifiable_ratio >= float(gates["minimum_b5_lever_design_second_to_first_ratio"])
        and maximum_lag <= int(gates["maximum_b5_timing_lag_samples"])
        and gyro_noise <= float(gates["maximum_b5_gyro_difference_noise_rad_s2"])
        and cost_per_scalar <= float(gates["maximum_b5_nominal_robust_cost_per_scalar"])
    )
    return {
        "eligibility_lineage": "SYNTHETIC_TRUTH_SIDE_TEST_CASE_KINEMATICS_ONLY",
        "estimator_firewall": "truth levers and generated translations are absent from objective initialization inputs, rank selection, and recovery gates",
        "negative_control": metadata["b5_negative_control"],
        "dynamic_joint_acceleration_q90_mps2": dynamic_q90,
        "profiled_lever_measurement_rank": lever_rank,
        "lever_nuisance_gauge_nullity": lever_nullity,
        "minimum_to_maximum_identifiable_lever_singular_ratio": identifiable_ratio,
        "per_edge_lever_design": per_edge,
        "maximum_best_timing_lag_samples": maximum_lag,
        "gyro_second_difference_noise_rad_s2": gyro_noise,
        "b5_measurement_factor_robust_cost": robust_cost,
        "b5_measurement_factor_robust_cost_per_scalar": cost_per_scalar,
        "pass": passed,
    }


def b5_negative_control_audit(contract: dict[str, Any]) -> dict[str, Any]:
    seed = int(contract["synthetic_cases"]["b5_negative_control_seed"])
    gates = contract["recovery_gates"]
    records = []
    for control in B5_NEGATIVE_CONTROLS:
        observation, truth, metadata = generate_unified_case(
            contract,
            seed,
            noisy=False,
            b5_control=control,
        )
        objective = UnifiedCalibrationObjective(observation, contract)
        audit = b5_excitation_audit(objective, truth, metadata, gates)
        if control == "timing_offset":
            mechanism = (
                audit["maximum_best_timing_lag_samples"] > int(gates["maximum_b5_timing_lag_samples"])
                or audit["b5_measurement_factor_robust_cost_per_scalar"]
                > float(gates["maximum_b5_nominal_robust_cost_per_scalar"])
            )
        elif control == "low_dynamics":
            mechanism = audit["dynamic_joint_acceleration_q90_mps2"] < float(
                gates["minimum_b5_dynamic_joint_acceleration_q90_mps2"]
            )
        elif control == "single_axis_lever_arm_degeneracy":
            mechanism = (
                audit["profiled_lever_measurement_rank"]
                <= int(gates["maximum_b5_single_axis_control_lever_rank"])
                or audit["minimum_to_maximum_identifiable_lever_singular_ratio"]
                < float(gates["minimum_b5_lever_design_second_to_first_ratio"])
            )
        elif control == "articulation_eligibility_failure":
            mechanism = audit["b5_measurement_factor_robust_cost_per_scalar"] > float(
                gates["maximum_b5_nominal_robust_cost_per_scalar"]
            )
        else:
            mechanism = audit["gyro_second_difference_noise_rad_s2"] > float(
                gates["maximum_b5_gyro_difference_noise_rad_s2"]
            )
        records.append({
            "control": control,
            "expected_rejection": True,
            "eligibility_pass": audit["pass"],
            "mechanism_detected": bool(mechanism),
            "audit": audit,
        })
    return {
        "records": records,
        "pass": all(not item["eligibility_pass"] and item["mechanism_detected"] for item in records),
    }


def write_b5_operator_recapture_package(output: Path, contract: dict[str, Any]) -> None:
    """Emit the independent-session package only after a complete audited B5 pass."""

    phases = [
        {"phase": "PRE_REST", "duration_s": 0.6},
        {"phase": "TRANSITION_TO_ACTION", "duration_s": 0.5},
        {"phase": "FORMAL_ACTION", "duration_s": 2.8},
        {"phase": "TRANSITION_TO_POST_REST", "duration_s": 0.5},
        {"phase": "POST_REST", "duration_s": 0.6},
    ]
    protocol = {
        "schema": "biospur-pure-imu-v0-b5-independent-session-recapture-v1",
        "authorization_basis": "PASS_MILESTONE_B5_AUDITED_SYNTHETIC_QUALIFICATION",
        "session_independence": {
            "capture1_and_capture2_are_separate_donnings": True,
            "one_profile_estimated_once_per_capture": True,
            "parameters_may_cross_sessions": False,
            "capture3_may_repair_capture1_or_capture2": False,
        },
        "common_episode_contract": {
            "total_duration_s": 5.0,
            "phases": phases,
            "same_rest_required": True,
            "retry_entire_episode_if_recovery_or_eligibility_fails": True,
            "six_axis_stream_required": True,
            "accelerometer_and_gyro_share_the_same_common_time_rows": True,
            "b5_joint_center_closure": {
                "minimum_dynamic_joint_acceleration_q90_mps2": contract["recovery_gates"]["minimum_b5_dynamic_joint_acceleration_q90_mps2"],
                "required_measured_lever_subspace_rank": contract["recovery_gates"]["required_b5_measured_lever_subspace_rank"],
                "expected_uncredited_lever_gauge_nullity": contract["recovery_gates"]["expected_b5_lever_nuisance_gauge_nullity"],
                "maximum_timing_lag_samples": contract["recovery_gates"]["maximum_b5_timing_lag_samples"],
                "maximum_gyro_difference_noise_rad_s2": contract["recovery_gates"]["maximum_b5_gyro_difference_noise_rad_s2"],
                "maximum_robust_cost_per_scalar": contract["recovery_gates"]["maximum_b5_nominal_robust_cost_per_scalar"],
                "lever_bounds_are_optimizer_guards_not_observability_evidence": True,
            },
        },
        "episodes_per_independent_session": [
            {
                "id": B3_HIP_CIRCUMDUCTION,
                "operator_instruction": "Hold the pelvis facing forward and as still as possible. Move both thighs simultaneously through one lateral flexion/extension excursion that returns to neutral, then one forward-axis abduction/adduction excursion that returns to the identical neutral stance.",
                "not_equivalent_to": "03_pelvis_hula_circle",
                "machine_eligibility": {
                    "minimum_each_hip_second_to_first_relative_omega_singular_ratio": contract["recovery_gates"]["minimum_b3_two_axis_singular_ratio"],
                },
            },
            {
                "id": B3_KNEE_LEFT,
                "operator_instruction": "Sit with the left thigh supported and motionless. Flex and extend the left knee back to neutral, then keep the thigh still and perform a small deliberate inward/outward tibial axial rotation before returning to the identical neutral rest.",
                "not_equivalent_to": "10_knee_left_seated",
                "machine_eligibility": {
                    "maximum_stationary_thigh_activity_ratio": contract["recovery_gates"]["maximum_b3_stationary_thigh_activity_ratio"],
                    "minimum_flexion_to_axial_principal_angle_deg": contract["recovery_gates"]["minimum_b3_noncollinear_axis_angle_deg"],
                },
            },
            {
                "id": B3_KNEE_RIGHT,
                "operator_instruction": "Sit with the right thigh supported and motionless. Flex and extend the right knee back to neutral, then keep the thigh still and perform a small deliberate inward/outward tibial axial rotation before returning to the identical neutral rest.",
                "not_equivalent_to": "11_knee_right_seated",
                "machine_eligibility": {
                    "maximum_stationary_thigh_activity_ratio": contract["recovery_gates"]["maximum_b3_stationary_thigh_activity_ratio"],
                    "minimum_flexion_to_axial_principal_angle_deg": contract["recovery_gates"]["minimum_b3_noncollinear_axis_angle_deg"],
                },
            },
            {
                "id": B3_TRUNK_LATERAL,
                "operator_instruction": "Keep the pelvis facing forward. Bend the trunk to the labelled LEFT and return to neutral; then bend to the labelled RIGHT and return to the identical neutral rest. Do not substitute flexion or axial rotation.",
                "not_equivalent_to": "14_trunk_flex_extend or 15_trunk_axial_rotation",
                "machine_eligibility": {
                    "minimum_signed_direction_agreement_fraction": contract["recovery_gates"]["minimum_b3_labelled_direction_agreement_fraction"],
                },
            },
            {
                "id": B4_EN_BLOC,
                "operator_instruction": "Use an approved full-body support that keeps pelvis, torso, both upper arms, both forearms, both thighs, and both shanks mutually braced. A trained operator moves the supported assembly gently through one comfortable rotation, returns through neutral, then a second clearly non-collinear comfortable rotation, and returns to the identical rest. Do not target a navigation direction or prescribed angle.",
                "safety_interlocks": [
                    "No unsupported standing, self-tilting, improvised swivel chair, or forced joint restraint.",
                    "A trained operator controls the support; the participant must be able to stop immediately.",
                    "Stop for pain, dizziness, nausea, loss of support, strap migration, or any segment articulation.",
                    "Use only equipment-approved comfortable range and speed; no angle or rate target overrides safety.",
                ],
                "machine_eligibility": {
                    "minimum_common_rate_q90_rad_s": contract["recovery_gates"]["minimum_b4_common_rate_q90_rad_s"],
                    "minimum_common_rate_second_to_first_singular_ratio": contract["recovery_gates"]["minimum_b4_two_axis_singular_ratio"],
                    "maximum_each_segment_relative_to_pelvis_q90_ratio": contract["recovery_gates"]["maximum_b4_segment_relative_to_pelvis_q90_ratio"],
                    "all_ten_segments_valid": True,
                    "same_rest_required": True,
                },
            },
        ],
        "forbidden_substitutions": [
            "late different stable pose as post-rest",
            "existing pelvis-hula label without measured bilateral two-axis thigh motion",
            "existing seated-knee label without stationary thigh and tibial axial phase",
            "profile transfer between Capture1 and Capture2",
            "Capture3 repair of an earlier donning",
            "single-axis rigid rotation",
            "near-zero rocking whose apparent second axis is sensor noise",
            "segment articulation or timing mismatch accepted as rigid motion",
            "low-dynamic articulated motion credited with B5 joint-center rank",
            "single-axis lever-arm excitation credited as full B5 eligibility",
            "accelerometer timing offset or differentiated-gyro noise accepted as joint-center closure",
        ],
    }
    dump(output / "B5_OPERATOR_RECAPTURE_PROTOCOL.json", protocol)
    dump(output / "B5_INDEPENDENT_SESSION_NOTICE.json", {
        "schema": "biospur-pure-imu-v0-b5-independent-session-notice-v1",
        "authorization_basis": "PASS_MILESTONE_B5_AUDITED_SYNTHETIC_QUALIFICATION",
        "capture1": {"independent_donning": True, "profile_estimated_once": True},
        "capture2": {"independent_donning": True, "profile_estimated_once": True},
        "cross_session_parameter_transfer": False,
        "capture3_repair": False,
        "required_protocol": "B5_OPERATOR_RECAPTURE_PROTOCOL.json",
        "failed_episode_action": "repeat the complete episode; never splice phases or use a late different rest",
    })
    notice = "# BioSpur Pure-IMU V0 — B5 independent-session recapture notice\n\n"
    notice += "Milestone B5 passed the audited multidimensional causality contract together with all unchanged recovery, rank, sign, weak-motion, Jv, negative-control, truth-firewall, and replay gates. A physical recapture is authorized only under the attached machine-readable protocol and safety interlocks.\n\n"
    notice += "Capture1 and Capture2 are independent donnings. Complete every listed episode separately in each session; estimate one profile once per session; never transfer parameters between sessions. Each episode is a complete 5.0 s rest→transition→action→return→same-rest sequence. A failed eligibility or recovery check requires repeating the entire episode.\n\n"
    notice += "The B4 episode still requires approved full-body support, two measured non-collinear common-rate directions, and machine-verified rigidity of all ten segments. Every articulated episode additionally requires the common-time six-axis stream and must pass B5 joint-center excitation, timing, noise, and robust-closure eligibility. Follow `B5_OPERATOR_RECAPTURE_PROTOCOL.json`; do not improvise the support or substitute a single-axis turn.\n"
    (output / "OPERATOR_RECAPTURE_NOTICE.md").write_text(notice)


def run_case(
    contract: dict[str, Any],
    seed: int,
    noisy: bool,
    output: Path,
    initializers: np.ndarray | None = None,
) -> dict[str, Any]:
    observation, truth, metadata = generate_unified_case(contract, seed, noisy=noisy)
    objective = UnifiedCalibrationObjective(observation, contract)
    if initializers is None:
        starts = qualified_initializations(observation, contract, seed + (1000 if noisy else 0))
        initializer = np.stack(starts)
    else:
        initializer = np.asarray(initializers, dtype=float)
        starts = [value.copy() for value in initializer]
    fits = fit_multistart(objective, starts, contract, seed + (1000 if noisy else 0))
    best = min(fits, key=lambda item: item["cost"])
    recoveries = [recovery_record(objective, item["x"], metadata["segment_directions"]) for item in fits]
    best_recovery = recovery_record(objective, best["x"], metadata["segment_directions"])
    b3_audit = b3_excitation_audit(objective, metadata, contract["recovery_gates"])
    b4_audit = b4_excitation_audit(objective, metadata, contract["recovery_gates"])
    b5_audit = b5_excitation_audit(objective, truth, metadata, contract["recovery_gates"])
    costs = np.asarray([item["cost"] for item in fits])
    np.savez_compressed(output / f"CASE_{seed}_{'NOISY' if noisy else 'NOISE_FREE'}.npz", truth=truth, initializer=initializer, fits=np.stack([item["x"] for item in fits]))
    return {
        "seed": seed,
        "noisy": noisy,
        "observation_signature": observation.signature(),
        "truth_firewall": metadata["truth_firewall"],
        "complete_episode_phases_present": metadata["complete_episode_phases_present"],
        "b3_excitation_and_episode_audit": b3_audit,
        "b4_excitation_and_episode_audit": b4_audit,
        "b5_joint_center_excitation_and_eligibility_audit": b5_audit,
        "initializer_sha256": array_hash(initializer),
        "fits": [{key: value for key, value in item.items() if key != "x"} for item in fits],
        "best_start": best["start"],
        "best_cost": best["cost"],
        "best_recovery": best_recovery,
        "best_product_state_error": product_state_error(truth, best["x"]),
        "per_start_recovery": recoveries,
        "per_start_product_state_error": [
            product_state_error(truth, item["x"]) for item in fits
        ],
        "maximum_multistart_direct_fk_disagreement_deg": maximum_profile_disagreement(objective, [item["x"] for item in fits]),
        "multistart_cost_ratio": float(np.max(costs) / max(np.min(costs), 1e-12)),
        "best_product": best["x"],
        "objective": objective,
        "truth": truth,
        "metadata": metadata,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--reuse-cases", type=Path)
    parser.add_argument("--reuse-initializers", type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    contract = json.loads(CONTRACT_PATH.read_text())
    causality_bytes = CAUSALITY_CONTRACT_PATH.read_bytes()
    causality_sha256 = hashlib.sha256(causality_bytes).hexdigest()
    if causality_sha256 != FROZEN_CAUSALITY_CONTRACT_SHA256:
        raise RuntimeError("B5 causality classification changed after the independent gate-design audit")
    causality_contract = json.loads(causality_bytes)
    correction_bytes = CAUSALITY_SELECTOR_CORRECTION_PATH.read_bytes()
    correction_sha256 = hashlib.sha256(correction_bytes).hexdigest()
    if correction_sha256 != FROZEN_CAUSALITY_SELECTOR_CORRECTION_SHA256:
        raise RuntimeError("B5 causality selector correction changed after its audit addendum")
    selector_correction = json.loads(correction_bytes)
    if selector_correction["applies_to_contract_sha256"] != causality_sha256:
        raise RuntimeError("B5 selector correction does not apply to the frozen causality contract")
    corrected_causality_contract = json.loads(json.dumps(causality_contract))
    corrected_family = next(
        family for family in corrected_causality_contract["families"]
        if family["family"] == selector_correction["family"]
    )
    if corrected_family["target_coordinate_blocks"] != selector_correction["machine_selector_correction"]["previous_target_coordinate_blocks"]:
        raise RuntimeError("B5 selector correction precondition no longer matches the base contract")
    corrected_family["target_coordinate_blocks"] = selector_correction["machine_selector_correction"]["corrected_target_coordinate_blocks"]
    opened = [
        str(CONTRACT_PATH),
        str(CAUSALITY_CONTRACT_PATH),
        str(CAUSALITY_SELECTOR_CORRECTION_PATH),
        "EXACT_TIME_RESOLVED_UNIFIED_SYNTHETIC",
    ]
    if args.reuse_cases:
        opened.append(str(args.reuse_cases / "SYNTHETIC_QUALIFICATION.json"))
    if args.reuse_initializers:
        prior_audit = json.loads((args.reuse_initializers / "DATA_ACCESS_AUDIT.json").read_text())
        if (
            prior_audit.get("real_capture_payload_opened")
            or prior_audit.get("h_series_payload_opened")
            or prior_audit.get("sealed_inputs_opened")
        ):
            raise RuntimeError("initializer source is contaminated by sealed input access")
        opened.append(str(args.reuse_initializers / "DATA_ACCESS_AUDIT.json"))
        opened.append("DETERMINISTIC_SYNTHETIC_INITIALIZER_ARRAYS_ONLY")
    dump(args.output / "DATA_ACCESS_AUDIT.json", {
        "opened": opened,
        "sealed_inputs_opened": [],
        "sealed": contract["sealed_inputs"],
        "real_capture_payload_opened": False,
        "h_series_payload_opened": False,
    })
    seeds = [int(value) for value in contract["synthetic_cases"]["multiple_truth_seeds"]]
    cases = []
    if args.reuse_cases and args.reuse_initializers:
        raise ValueError("reuse-cases and reuse-initializers are mutually exclusive")
    if args.reuse_cases:
        previous = json.loads((args.reuse_cases / "SYNTHETIC_QUALIFICATION.json").read_text())
        for saved in previous["cases"]:
            seed, noisy = int(saved["seed"]), bool(saved["noisy"])
            observation, truth, metadata = generate_unified_case(contract, seed, noisy=noisy)
            filename = f"CASE_{seed}_{'NOISY' if noisy else 'NOISE_FREE'}.npz"
            archive = np.load(args.reuse_cases / filename)
            fits = np.asarray(archive["fits"])
            best_product = fits[int(saved["best_start"])]
            shutil.copy2(args.reuse_cases / filename, args.output / filename)
            case = dict(saved)
            objective = UnifiedCalibrationObjective(observation, contract)
            case.update({
                "b3_excitation_and_episode_audit": b3_excitation_audit(
                    objective, metadata, contract["recovery_gates"],
                ),
                "b4_excitation_and_episode_audit": b4_excitation_audit(
                    objective, metadata, contract["recovery_gates"],
                ),
                "b5_joint_center_excitation_and_eligibility_audit": b5_excitation_audit(
                    objective, truth, metadata, contract["recovery_gates"],
                ),
                "best_product_state_error": product_state_error(truth, best_product),
                "per_start_product_state_error": [
                    product_state_error(truth, value) for value in fits
                ],
                "best_product": best_product,
                "objective": objective,
                "truth": truth,
                "metadata": metadata,
            })
            cases.append(case)
            print(f"REUSED_CASE seed={seed} noisy={noisy} cost={case['best_cost']:.9g} rmse={case['best_recovery']['segment_direction_rmse_deg']:.6g}", flush=True)
    else:
        for seed in seeds:
            for noisy in (False, True):
                reused_initializers = None
                if args.reuse_initializers:
                    filename = f"CASE_{seed}_{'NOISY' if noisy else 'NOISE_FREE'}.npz"
                    with np.load(args.reuse_initializers / filename) as archive:
                        reused_initializers = np.asarray(archive["initializer"], dtype=float)
                case = run_case(contract, seed, noisy, args.output, reused_initializers)
                cases.append(case)
                print(f"CASE seed={seed} noisy={noisy} cost={case['best_cost']:.9g} rmse={case['best_recovery']['segment_direction_rmse_deg']:.6g}", flush=True)

    reference = next(case for case in cases if not case["noisy"])
    objective = reference.pop("objective")
    truth = reference.pop("truth")
    metadata = reference.pop("metadata")
    for case in cases[1:]:
        case.pop("objective"); case.pop("truth"); case.pop("metadata")
    lineage = rank_lineage(objective, truth)
    jacobian_data = production_jacobian(objective, truth, False)
    jv = directional_jv_check(objective, truth, jacobian_data)
    ablations = action_ablations(objective, lineage)
    uncorrected_causality_dry_run = multidimensional_action_causality(
        objective,
        lineage,
        causality_contract,
        truth,
    )
    audited_causality = multidimensional_action_causality(
        objective,
        lineage,
        corrected_causality_contract,
        truth,
    )
    b3_gain = b3_information_gain(objective, lineage)
    b4_gain = b4_information_gain(objective, lineage)
    b5_gain = b5_information_gain(objective, lineage)
    b4_negative_controls = b4_negative_control_audit(contract)
    b5_negative_controls = b5_negative_control_audit(contract)
    signs = sign_handedness_alternatives(objective, reference["best_product"])

    weak_observation, weak_truth, _ = generate_unified_case(
        contract, int(contract["synthetic_cases"]["weak_off_axis_seed"]), noisy=False, weak_motion=True,
    )
    weak_objective = UnifiedCalibrationObjective(weak_observation, contract)
    weak_lineage = rank_lineage(weak_objective, weak_truth)
    normal_combined = lineage["stages"]["ALL_MEASUREMENT_AND_SOFT_PROTOCOL"]
    weak_combined = weak_lineage["stages"]["ALL_MEASUREMENT_AND_SOFT_PROTOCOL"]
    normal_combined_singular = np.asarray(normal_combined["bottom_product_singular_values"])
    weak_combined_singular = np.asarray(weak_combined["bottom_product_singular_values"])
    combined_weak_ratio = float(normal_combined_singular[-1] / max(weak_combined_singular[-1], 1e-15))
    normal_raw = lineage["stages"]["RAW_PLUS_MEASURED_NEUTRAL_ZERO_DEFINITIONS"]
    weak_raw = weak_lineage["stages"]["RAW_PLUS_MEASURED_NEUTRAL_ZERO_DEFINITIONS"]
    normal_raw_singular = np.asarray(normal_raw["bottom_product_singular_values"])
    weak_raw_singular = np.asarray(weak_raw["bottom_product_singular_values"])
    normal_raw_minimum = float(normal_raw_singular[-(normal_raw["profiled_product_nullity"] + 1)])
    weak_raw_minimum = float(weak_raw_singular[-(weak_raw["profiled_product_nullity"] + 1)])
    raw_weak_ratio = normal_raw_minimum / max(weak_raw_minimum, 1e-15)

    first = recovery_record(objective, reference["best_product"], metadata["segment_directions"])
    second = recovery_record(objective, reference["best_product"], metadata["segment_directions"])
    double_replay = {
        "first_sha256": first["aligned_direction_sha256"],
        "second_sha256": second["aligned_direction_sha256"],
        "byte_identical": first["aligned_direction_sha256"] == second["aligned_direction_sha256"],
    }
    firewall_observation, _, changed_metadata = generate_unified_case(contract, seeds[0], noisy=False)
    changed_metadata["segment_directions"] = np.zeros_like(changed_metadata["segment_directions"])
    changed_metadata["world_segment_rotation"] = np.zeros_like(changed_metadata["world_segment_rotation"])
    changed_metadata["b5_sensor_position"] = np.zeros_like(changed_metadata["b5_sensor_position"])
    observation_signature = objective.obs.signature()
    truth_firewall = {
        "truth_absent_from_observation": True,
        "baseline_observation_signature": observation_signature,
        "regenerated_observation_signature": firewall_observation.signature(),
        "metadata_mutation_cannot_enter_objective_api": True,
        "pass": observation_signature == firewall_observation.signature(),
    }

    lineage.pop("jacobian"); lineage.pop("blocks")
    weak_lineage.pop("jacobian"); weak_lineage.pop("blocks")
    gates = contract["recovery_gates"]
    case_pass = all(
        case["best_recovery"]["segment_direction_rmse_deg"]
        <= float(gates["maximum_noisy_segment_direction_rmse_deg" if case["noisy"] else "maximum_noise_free_segment_direction_rmse_deg"])
        and case["maximum_multistart_direct_fk_disagreement_deg"] <= float(gates["maximum_multistart_direct_fk_disagreement_deg"])
        and case["multistart_cost_ratio"] <= float(gates["maximum_multistart_product_cost_ratio"])
        and all(item["finite"] for item in case["fits"])
        for case in cases
    )
    b3_motion_pass = all(case["b3_excitation_and_episode_audit"]["pass"] for case in cases)
    b4_motion_pass = all(case["b4_excitation_and_episode_audit"]["pass"] for case in cases)
    b5_motion_pass = all(case["b5_joint_center_excitation_and_eligibility_audit"]["pass"] for case in cases)
    raw_stage = lineage["stages"]["RAW_TIME_RESOLVED_MEASUREMENT"]
    combined_stage = lineage["stages"]["ALL_MEASUREMENT_AND_SOFT_PROTOCOL"]
    rank_pass = (
        raw_stage["heading_conditioned_on_all_other_coordinates"]["rank"] == 9
        and combined_stage["profiled_product_rank"] == 55
        and not lineage["parameter_only_priors_add_rank"]
        and lineage["b5_lever_arm_measurement_subspace"]["rank"]
        == int(gates["required_b5_measured_lever_subspace_rank"])
        and lineage["b5_lever_arm_measurement_subspace"]["nullity"]
        == int(gates["expected_b5_lever_nuisance_gauge_nullity"])
    )
    sign_pass = all(
        item["delta_cost"] > float(gates["minimum_sign_handedness_alternative_delta_cost"])
        for item in signs
    )
    legacy_ablation_pass = all(
        item["rank_loss"] > 0
        or item["heading_rank_loss"] > 0
        or item["smallest_singular_loss_fraction"]
        >= float(gates["minimum_action_ablation_smallest_singular_loss_fraction"])
        for item in ablations
    )
    audited_causality_pass = bool(audited_causality["pass"])
    weak_pass = (
        raw_weak_ratio >= float(gates["minimum_weak_raw_information_inflation_ratio"])
        or weak_raw["profiled_product_rank"] < normal_raw["profiled_product_rank"]
    )
    dump(args.output / "LEGACY_UNIVERSAL_2PCT_RESULT.json", {
        "schema": "biospur-pure-imu-v0-b5-legacy-universal-ablation-v1",
        "terminal_outcome": (
            "PASS_LEGACY_UNIVERSAL_2PCT_ACTION_ABLATION"
            if legacy_ablation_pass
            else "FAIL_LEGACY_UNIVERSAL_2PCT_ACTION_ABLATION"
        ),
        "acceptance_authority_after_gate_design_audit": False,
        "minimum_loss_fraction": gates["minimum_action_ablation_smallest_singular_loss_fraction"],
        "action_ablations": ablations,
        "interpretation": "Preserved diagnostic only. Failure does not establish non-causality in a redundant objective.",
    })
    pass_all = case_pass and rank_pass and sign_pass and audited_causality_pass and weak_pass and b3_motion_pass and b4_motion_pass and b5_motion_pass and b4_negative_controls["pass"] and b5_negative_controls["pass"] and double_replay["byte_identical"] and truth_firewall["pass"] and jv["relative_error"] <= float(gates["maximum_directional_jv_relative_error"])
    report = {
        "schema": "biospur-pure-imu-v0-synthetic-qualification-v1",
        "terminal_outcome": "PASS_MILESTONE_B_SYNTHETIC_QUALIFICATION" if pass_all else "FAIL_MILESTONE_B_SYNTHETIC_QUALIFICATION",
        "real_data_authorized_by_this_report": False,
        "cases": cases,
        "rank_lineage": lineage,
        "action_ablations": ablations,
        "legacy_universal_2pct_action_ablation": {
            "pass": legacy_ablation_pass,
            "acceptance_authority_after_gate_design_audit": False,
            "preserved_result": "LEGACY_UNIVERSAL_2PCT_RESULT.json",
        },
        "multidimensional_action_causality": audited_causality,
        "prequalification_uncorrected_selector_dry_run": {
            "acceptance_authority": False,
            "result": uncorrected_causality_dry_run,
            "reason_preserved": "STATIC_TPOSE machine selector omitted SENSOR_LONGITUDINAL_AXIS although the frozen residual-target prose included it",
        },
        "multidimensional_causality_contract": {
            "path": str(CAUSALITY_CONTRACT_PATH),
            "sha256": causality_sha256,
            "selector_correction_path": str(CAUSALITY_SELECTOR_CORRECTION_PATH),
            "selector_correction_sha256": correction_sha256,
            "frozen_before_qualification_replay": True,
        },
        "b3_information_gain": b3_gain,
        "b4_information_gain": b4_gain,
        "b5_information_gain": b5_gain,
        "b4_negative_controls": b4_negative_controls,
        "b5_negative_controls": b5_negative_controls,
        "sign_handedness_alternatives": signs,
        "weak_motion": {
            "lineage": weak_lineage,
            "combined_soft_supported_normal_to_weak_minimum_singular_ratio": combined_weak_ratio,
            "raw_plus_zero_normal_smallest_identifiable_singular": normal_raw_minimum,
            "raw_plus_zero_weak_smallest_identifiable_singular": weak_raw_minimum,
            "raw_plus_zero_normal_to_weak_information_ratio": raw_weak_ratio,
            "soft_terms_mask_raw_weakness": raw_weak_ratio > combined_weak_ratio,
        },
        "directional_jv": jv,
        "double_replay": double_replay,
        "truth_firewall": truth_firewall,
        "gates": {"case_recovery": case_pass, "rank_lineage": rank_pass, "sign_handedness": sign_pass, "action_ablation": legacy_ablation_pass, "legacy_universal_2pct_action_ablation": legacy_ablation_pass, "multidimensional_action_causality": audited_causality_pass, "weak_motion": weak_pass, "b3_motion_eligibility": b3_motion_pass, "b4_motion_eligibility": b4_motion_pass, "b5_motion_eligibility": b5_motion_pass, "b4_negative_controls": b4_negative_controls["pass"], "b5_negative_controls": b5_negative_controls["pass"], "directional_jv": jv["relative_error"] <= float(gates["maximum_directional_jv_relative_error"]), "double_replay": double_replay["byte_identical"], "truth_firewall": truth_firewall["pass"]},
    }
    dump(args.output / "SYNTHETIC_QUALIFICATION.json", report)
    if pass_all:
        write_b5_operator_recapture_package(args.output, contract)
    dump(args.output / "RUN_MANIFEST.json", {
        "pid": os.getpid(),
        "contract": str(CONTRACT_PATH),
        "multidimensional_causality_contract": str(CAUSALITY_CONTRACT_PATH),
        "multidimensional_causality_contract_sha256": causality_sha256,
        "multidimensional_causality_selector_correction": str(CAUSALITY_SELECTOR_CORRECTION_PATH),
        "multidimensional_causality_selector_correction_sha256": correction_sha256,
        "reused_case_artifacts_from": args.reuse_cases,
        "reused_deterministic_initializers_from": args.reuse_initializers,
        "terminal_outcome": report["terminal_outcome"],
    })
    print(report["terminal_outcome"], flush=True)
    return 0 if pass_all else 2


if __name__ == "__main__":
    raise SystemExit(main())
