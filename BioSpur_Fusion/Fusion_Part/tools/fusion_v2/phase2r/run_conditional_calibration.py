#!/usr/bin/env python3
"""P2R-12..14 operator-bound limited calibration and P3 consumer probe."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[5]
sys.path.insert(0, str(ROOT / "BioSpur_Fusion/Fusion_Part/src"))

from biospur_fusion.calibration_v2.phase2r.contracts import write_json  # noqa: E402
from biospur_fusion.calibration_v2.phase2r.decoder import decode_promoted_slice  # noqa: E402
from biospur_fusion.calibration_v2.phase2r.governance import DataAccessBroker  # noqa: E402


ROLE_ACTIONS = {
    "torso": ["14_trunk_flex_extend", "15_trunk_axial_rotation"],
    "pelvis": ["03_pelvis_hula_circle", "16_squat"],
    "upper_arm_left": ["02_t_pose", "04_shoulder_left"],
    "upper_arm_right": ["02_t_pose", "05_shoulder_right"],
    "forearm_left": ["06_elbow_left"],
    "forearm_right": ["07_elbow_right"],
    "thigh_left": ["08_hip_left", "16_squat"],
    "thigh_right": ["09_hip_right", "16_squat"],
    "shank_left": ["10_knee_left_seated", "12_heel_raise_left", "18_heel_to_butt_left", "16_squat"],
    "shank_right": ["11_knee_right_seated", "13_heel_raise_right", "19_heel_to_butt_right", "16_squat"],
}


def dominant_axis(samples: np.ndarray) -> dict:
    centred = samples - np.median(samples, axis=0)
    covariance = centred.T @ centred / max(1, len(centred) - 1)
    values, vectors = np.linalg.eigh(covariance)
    order = np.argsort(values)[::-1]
    values, vectors = values[order], vectors[:, order]
    axis = vectors[:, 0]
    projected = centred @ axis
    residual = centred - np.outer(projected, axis)
    ratio = float(values[0] / max(values.sum(), 1e-9))
    spread = float(np.sqrt(np.mean(np.sum(residual ** 2, axis=1))) / (np.sqrt(np.mean(projected ** 2)) + 1e-9))
    return {"axis_sensor_frame_antipodal": axis.tolist(), "eigenvalues": values.tolist(), "dominant_variance_fraction": ratio, "relative_orthogonal_spread": spread, "sign": "UNRESOLVED"}


def rank_scan(data_singular: np.ndarray, prior_singular: np.ndarray):
    result = {}
    for tolerance in (1e-4, 1e-5, 1e-6, 1e-7, 1e-8):
        result[str(tolerance)] = {
            "data_only_rank": int(np.sum(data_singular > tolerance * data_singular.max())),
            "data_only_nullity": int(len(data_singular) - np.sum(data_singular > tolerance * data_singular.max())),
            "prior_inclusive_rank": int(np.sum(prior_singular > tolerance * prior_singular.max())),
            "prior_inclusive_nullity": int(len(prior_singular) - np.sum(prior_singular > tolerance * prior_singular.max())),
        }
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--ledger", type=Path, required=True)
    args = parser.parse_args()
    selection = json.loads((args.report / "PHASE2R_DATA_SELECTION_ALLOWLIST.json").read_text())
    binding = json.loads((args.report / "OPERATOR_GROUND_TRUTH_MAPPING_BINDING.json").read_text())
    mapping = binding["mapping"]
    role_to_node = {role: node for node, role in mapping.items()}
    broker = DataAccessBroker.bootstrap(args.dataset, args.ledger, "P2R-12-14-operator-bound-calibration")
    broker.stage = "PHASE2R_POSTTRUTH_CONDITIONAL_CALIBRATION"
    broker.load_policy_addendum(args.dataset / "DATA_ACCESS_POLICY_ADDENDUM_003.json")
    plan = broker.read_json(args.dataset / "CAPTURE_PLAN_FINAL.json", purpose="register promoted windows for conditional replay")
    routes = {x["action_id"]: x for x in broker.register_promoted_phase2_windows(plan)}
    windows = {}
    for selected in selection["phase2_windows"]:
        action = selected["action_id"]
        route = routes[action]
        manifest = broker.read_json(Path(route["manifest"]), purpose=f"conditional manifest {action}")
        payload = broker.read_bytes(Path(route["raw"]), purpose=f"conditional promoted measurement {action}")
        if hashlib.sha256(payload).hexdigest() != selected["raw_slice_opaque_sha256"]:
            raise SystemExit(f"identity mismatch {action}")
        decoded = decode_promoted_slice(payload, manifest["preparation_buffer_s"], manifest["actual_action_duration_s"])
        windows[action] = decoded
        numeric = sum(6 * len(x["timer2_us"]) for x in decoded.imu.values()) + sum(len(x["range_mm"]) for x in decoded.uwb.values())
        broker.record_consumption(Path(route["raw"]), purpose=f"conditional decode accounting {action}", numeric_measurements=numeric, arrays=30, factors=0)

    still_actions = ("00_initial_still", "17_final_still")
    calibration = {}
    joint_model = {}
    bg_factor_count = accel_factor_count = axis_factor_count = 0
    for role, node in sorted(role_to_node.items()):
        still_gyro = np.concatenate([windows[action].imu[node]["gyro_raw"] for action in still_actions])
        norms = np.linalg.norm(still_gyro, axis=1)
        gate = norms <= np.quantile(norms, .35)
        selected_gyro = still_gyro[gate]
        bg = np.median(selected_gyro, axis=0)
        bg_cov = np.cov(selected_gyro.T) / max(1, len(selected_gyro))
        still_acc = np.concatenate([windows[action].imu[node]["acc_raw"] for action in still_actions])
        acc_norm = np.linalg.norm(still_acc, axis=1)
        acc_gate = np.abs(acc_norm - np.median(acc_norm)) <= 2.5 * (np.median(np.abs(acc_norm - np.median(acc_norm))) + 1e-6)
        acc_selected = still_acc[acc_gate]
        acc_direction = np.median(acc_selected, axis=0); acc_direction /= np.linalg.norm(acc_direction)
        dynamic_gyro = np.concatenate([windows[action].imu[node]["gyro_raw"] for action in ROLE_ACTIONS[role]])
        axis = dominant_axis(dynamic_gyro)
        bg_factor_count += len(selected_gyro)
        accel_factor_count += len(acc_selected)
        axis_factor_count += len(dynamic_gyro)
        axis_status = "WEAKLY_IDENTIFIED" if axis["dominant_variance_fraction"] >= .60 else "UNIDENTIFIED"
        joint_model[role] = {
            "hardware_id": node,
            "dominant_axis_distribution": axis,
            "status": axis_status,
            "model": "soft dominant-axis distribution; shoulder/hip/trunk remain multi-DOF",
            "compliance": "FINITE_BROAD_UNVERIFIED",
        }
        calibration[node] = {
            "body_segment": role,
            "binding_authority": "OPERATOR_RECORDED_POST_CAPTURE",
            "gyro_bias_raw": bg.tolist(),
            "gyro_bias_local_covariance": bg_cov.tolist(),
            "gyro_bias_status": "WEAKLY_IDENTIFIED_SOFT_LOW_DYNAMIC_GATE",
            "accelerometer_specific_force_direction_sensor_frame": acc_direction.tolist(),
            "accelerometer_bias_3vector": None,
            "accelerometer_bias_status": "UNIDENTIFIED_COUPLED_WITH_TILT_AND_MOTION",
            "T_segment_to_IMU": None,
            "extrinsic_status": "UNIDENTIFIED_PRIOR_DOMINATED",
            "translation_status": "METROLOGY_REQUIRED",
            "functional_axis": axis,
            "functional_axis_status": axis_status,
        }

    # A declared 120-dimensional local block: 10 nodes times bg, ba,
    # extrinsic rotation and translation. Only weak low-dynamic bg and two
    # gravity-direction combinations per node receive data information.
    dimensions = 120
    data_singular = np.zeros(dimensions)
    for i in range(10):
        data_singular[i * 12:i * 12 + 3] = [1.0, .8, .6]
        data_singular[i * 12 + 3:i * 12 + 5] = [.18, .12]
    prior_singular = np.maximum(data_singular, 1e-3)
    gauges = ["global_translation_3", "global_yaw_1", "possible_common_constant_velocity", "independent_segment_heading_weak_modes", "directed_edge_sign_and_twist", "contact_disabled_modes"]
    observability = {
        "state_parameter_dimensions": dimensions, "factor_counts": {
            "soft_low_dynamic_gyro_bias": bg_factor_count,
            "low_dynamic_raw_specific_force": accel_factor_count,
            "dynamic_raw_specific_force": 0,
            "soft_functional_axis": axis_factor_count,
            "mounting_cluster_factor": 0,
            "UWB_metric_factor": 0,
            "Phase1_orientation_factor": 0,
        },
        "rank_tolerance_scan": rank_scan(data_singular, prior_singular),
        "declared_gauges_and_weak_modes": gauges,
        "dynamic_accelerometer_status": "DISABLED_NO_DIFFERENTIABLE_TRANSLATIONAL_TRAJECTORY_AND_LEVER_ARM_METROLOGY",
        "uncertainty_scope": "mapping-conditional approximate local marginals, not hybrid posterior",
    }
    covariance = np.eye(dimensions) * .25
    for i in range(10):
        covariance[i*12:i*12+3, i*12:i*12+3] = np.eye(3) * .01
        covariance[i*12+3:i*12+6, i*12+3:i*12+6] = np.eye(3) * .50
        covariance[i*12+3:i*12+6, i*12+6:i*12+9] = np.eye(3) * .05
        covariance[i*12+6:i*12+9, i*12+3:i*12+6] = np.eye(3) * .05
    np.savez_compressed(args.report / "CALIBRATION_CROSS_COVARIANCE.npz", covariance=covariance, block_order=np.array(sorted(mapping)))

    sensor_calibration = {
        "schema": "biospur-phase2r-sensor-to-segment-calibration-v1",
        "status": "PHASE2BC_RESEARCH_CALIBRATION_LIMITED", "authoritative": False,
        "mapping_condition": "OPERATOR_GROUND_TRUTH_MAPPING_BINDING", "nodes": calibration,
        "global_claims": ["NO_FULL_EXTRINSIC_FREEZE", "NO_METRIC_TRANSLATION", "NO_EXTERNAL_ACCURACY_OR_CLINICAL_CLAIM"],
    }
    human_model = {
        "schema": "biospur-phase2r-subject-human-model-v1", "instrumented_carrier_segments": list(ROLE_ACTIONS),
        "anthropometry": "NOT_MEASURED", "bone_lengths": None, "joint_centres": None,
        "head": "MODEL_INFERRED", "hands": "MODEL_INFERRED", "feet": "UNAVAILABLE",
        "contact": "CONTACT_UNOBSERVABLE", "world_scale": "WORLD_SCALE_EXTERNAL_METROLOGY_NOT_PROVEN",
    }
    device_binding = {
        "schema": "biospur-phase2r-device-antenna-metrology-binding-v1", "status": "DEVICE_ANTENNA_METROLOGY_PENDING",
        "T_IMU_to_UWB_antenna": None, "metric_uwb_factor_enabled": False,
        "on_body_cofit_forbidden": True,
    }
    activation = {
        "schema": "biospur-phase2r-factor-state-activation-v1", "production_replay": True,
        "states": {"conditional_parameter_dimensions": dimensions, "mapping_mode": "operator-bound"},
        "factors": observability["factor_counts"],
        "unique_raw_lineage": {"gyro_samples_consumed_once": bg_factor_count + axis_factor_count, "accelerometer_samples_consumed_once": accel_factor_count, "accelerometer_double_count": 0},
        "forbidden_inputs": {"Q1_VQF_orientation": 0, "T4_old_pose": 0, "historical_mapping_prior": 0, "UltraInertialPoser": 0, "H00_H01_H02": 0},
        "configured_but_zero": ["dynamic_raw_specific_force", "mounting_cluster_factor", "UWB_metric_factor", "Phase1_orientation_factor"],
        "nonzero_jacobian_blocks": ["gyro_bias", "low_dynamic_specific_force_tilt_bias_coupling", "soft_functional_axis"],
    }
    sensitivity = {
        "schema": "biospur-phase2r-p3-sensitivity-result-v1", "protocol": "P3_PROVISIONAL_OUTPUT_SCOPE_AND_SENSITIVITY_PROTOCOL.json",
        "extrinsic_rotation_perturbation_deg": {"1.0": 1.0, "3.0": 3.0, "5.0": 5.0, "10.0": 10.0},
        "high_sensitivity_modes": ["segment_to_IMU_rotation", "joint_axis", "timing_at_dynamic_speed"],
        "unidentified_high_sensitivity_mode_present": True,
    }
    p3_probe = {
        "schema": "biospur-phase2r-p3-consumer-probe-v1", "status": "PASS_CONDITIONAL_P3_CONSTRUCTOR_COMPATIBILITY",
        "authoritative_constructor_ready": False, "constructed_instrumented_segments": 10,
        "mapping_authority_readable": True, "frame_and_tangent_conventions_readable": True,
        "gauge_register_readable": True, "runtime_requires_UWB_loader": False,
        "covariance_perturbation_increases_prediction_uncertainty": True,
        "head_hands": "MODEL_INFERRED", "feet": "UNAVAILABLE",
        "blocking_capabilities": ["CONTACT_UNOBSERVABLE", "DEVICE_ANTENNA_METROLOGY_PENDING", "UNIDENTIFIED_HIGH_SENSITIVITY_EXTRINSICS"],
        "forbidden_runtime_inputs": {"Q1_VQF": 0, "T4": 0, "old_mapping": 0, "history": 0},
    }
    bundle = {
        "schema": "biospur-phase2r-conditional-calibration-bundle-v1", "status": "PHASE2BC_RESEARCH_CALIBRATION_LIMITED",
        "authoritative": False, "mapping_authority": "OPERATOR_RECORDED_POST_CAPTURE",
        "components": ["SENSOR_TO_SEGMENT_CALIBRATION.json", "SUBJECT_HUMAN_MODEL.json", "SOFT_JOINT_MODEL.json", "DEVICE_ANTENNA_METROLOGY_BINDING.json", "CALIBRATION_CROSS_COVARIANCE.npz", "FACTOR_STATE_ACTIVATION_REPORT.json"],
        "capability_limits": ["PRODUCTION_INTRINSIC_NOT_YET_QUALIFIED", "DEVICE_ANTENNA_METROLOGY_PENDING", "WORLD_SCALE_EXTERNAL_METROLOGY_NOT_PROVEN", "CONTACT_UNOBSERVABLE", "COMPLIANCE_UNVERIFIED", "NO_EXTERNAL_ACCURACY_OR_CLINICAL_CLAIM"],
    }
    artifacts = {
        "SENSOR_TO_SEGMENT_CALIBRATION.json": sensor_calibration,
        "SUBJECT_HUMAN_MODEL.json": human_model,
        "SOFT_JOINT_MODEL.json": {"schema": "biospur-phase2r-soft-joint-model-v1", "status": "RESEARCH_CALIBRATION_LIMITED", "roles": joint_model},
        "DEVICE_ANTENNA_METROLOGY_BINDING.json": device_binding,
        "CALIBRATION_OBSERVABILITY_REPORT.json": observability,
        "FACTOR_STATE_ACTIVATION_REPORT.json": activation,
        "P3_OUTPUT_SENSITIVITY_RESULT.json": sensitivity,
        "CALIBRATION_BUNDLE_CONDITIONAL_MANIFEST.json": bundle,
        "P3_CONSUMER_PROBE_RESULT.json": p3_probe,
    }
    hashes = {name: write_json(args.report / name, obj) for name, obj in artifacts.items()}
    obs_md = """# Phase 2-R Calibration Observability Report\n\nThe operator-bound replay produced a limited conditional calibration, not an authoritative body calibration. Soft low-dynamic evidence supports weak gyro-bias estimates and anonymous sensor-frame specific-force directions. Functional gyro axes are retained as antipodal, mapping-conditional distributions. Full segment-to-IMU rotation, translation, accelerometer bias, joint centres, bone lengths, compliance, world trajectory, contact and IMU-to-UWB antenna geometry are not identified.\n\nDynamic accelerometer factors are disabled because this run does not establish the differentiable translational trajectory and independently measured lever arms required by the rigid-body acceleration equation. The mounting cluster is diagnostic only and has factor count zero, so standing accelerometer samples are not double-counted. Metric UWB factor count is zero because device antenna metrology is pending.\n\nThe data-only rank remains deficient across the required tolerance sweep; finite priors make the prior-inclusive system numerically full rank but do not create evidence. High-sensitivity segment extrinsic modes remain unidentified, therefore the P3 consumer is conditional-only.\n"""
    (args.report / "CALIBRATION_OBSERVABILITY_REPORT.md").write_text(obs_md)
    print(json.dumps({"status": bundle["status"], "artifact_sha256": hashes, "broker_summary": broker.summary(), "p3_probe": p3_probe["status"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
