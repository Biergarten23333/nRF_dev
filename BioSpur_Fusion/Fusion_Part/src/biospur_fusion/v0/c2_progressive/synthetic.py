"""Independent physical-oracle qualification for the replacement P2 mechanisms."""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
from typing import Any, Mapping
from unittest.mock import patch

import numpy as np
from scipy.spatial.transform import Rotation

from . import functional_geometry as functional_geometry_owner
from .architecture_guard import (
    C2ExecutionGuard,
    ClassAGuardViolation,
    run_owner_level_architecture_mutations,
)
from .functional_geometry import (
    EDGE_SPECS,
    AlignedPair,
    AxisEstimate,
    CenterEstimate,
    enumerate_hinge_sign_branches,
    estimate_hinge_axis_qmt,
    estimate_joint_center_pair_local,
    numeric_axis_centered_support_transform_gate,
    numeric_center_physical_time_ownership_gate,
    validate_center_covariance_contract,
)
from .geometry_posterior import (
    numeric_low_information_geometry_owner_gate,
    numeric_product_s2_antipodal_gate,
)
from .orientation import (
    ACC_SCALE,
    GYRO_SCALE,
    ContinuousVQFState,
    assess_factor_rows,
)
from .orientation_uncertainty import numeric_sparse_quantile_orientation_uncertainty_gate
from .quaternion_contract import numeric_round_trip_gate
from .range_reader import DecodedAction, IMU_DTYPE
from .progressive import ProgressiveCalibrationState
from .scientific_fk import numeric_physical_candidate_uncertainty_gate
from .segment_frames import (
    numeric_frame_covariance_rotation_gate,
    numeric_wear_direction_owner_gate,
)
from .timebase import PairAlignment, PersistentPairClockState, align_pair_by_gyro_energy


@dataclass(frozen=True)
class SyntheticCalibrationFixture:
    """Generator-only capture calibration state; never an estimator input."""

    parent_scale_cross_axis: np.ndarray
    child_scale_cross_axis: np.ndarray
    parent_gyro_offset_rads: np.ndarray
    child_gyro_offset_rads: np.ndarray
    parent_gyro_drift_rads2: np.ndarray
    child_gyro_drift_rads2: np.ndarray
    parent_world_from_sensor_at_motion_start: np.ndarray
    child_world_from_sensor_at_motion_start: np.ndarray
    ar1_rho: float
    accelerometer_noise_std_mps2: float
    gyroscope_noise_std_rads: float
    initial_still_noise_seed: int


@dataclass(frozen=True)
class SyntheticPair:
    time_s: np.ndarray
    parent_acc: np.ndarray
    child_acc: np.ndarray
    parent_gyro: np.ndarray
    child_gyro: np.ndarray
    parent_axis_sensor: np.ndarray
    child_axis_sensor: np.ndarray
    joint_to_parent_sensor_m: np.ndarray
    joint_to_child_sensor_m: np.ndarray
    nonideality_report: Mapping[str, Any]
    calibration_fixture: SyntheticCalibrationFixture | None = None


def _synthetic_imu_rows(
    *,
    acc_mps2: np.ndarray,
    gyro_rads: np.ndarray,
    time_s: np.ndarray,
) -> np.ndarray:
    """Encode one synthetic endpoint on the same JY61P lattice as production."""

    acc = np.asarray(acc_mps2, dtype=float)
    gyro = np.asarray(gyro_rads, dtype=float)
    time = np.asarray(time_s, dtype=float)
    if acc.shape != gyro.shape or acc.ndim != 2 or acc.shape[1] != 3:
        raise ValueError("synthetic IMU rows require equal Nx3 acc/gyro arrays")
    if time.shape != (len(acc),) or not np.isfinite(time).all():
        raise ValueError("synthetic IMU rows require one finite time per sample")
    output = np.zeros(len(acc), dtype=IMU_DTYPE)
    output["derived_boot_epoch"] = 0
    output["imu_sample_sequence"] = np.arange(len(acc), dtype=np.uint16)
    output["node_timer_us"] = np.rint(time * 1e6).astype(np.uint64) + 1_000_000
    output["acc_raw"] = np.clip(
        np.rint(acc / ACC_SCALE), -32760, 32760,
    ).astype(np.int16)
    output["gyro_raw"] = np.clip(
        np.rint(gyro / GYRO_SCALE), -32760, 32760,
    ).astype(np.int16)
    output["raw_sample_index"] = 0
    output["decode_acceptance_status"] = 1
    return output


def _synthetic_initial_stochastic_state(
    rows_by_node: Mapping[str, np.ndarray],
    *,
    gyro_bias_floor_rad2_s2: float,
) -> dict[str, Any]:
    """Build the production orientation owner's imperfect-rest input state.

    This builder does not estimate accelerometer bias: its mean remains a
    gravity-confounded diagnostic and its covariance remains nonzero.  The
    downstream capture-wide calibration posterior may subsequently update a
    broad accelerometer-bias/scale nuisance distribution from gravity-norm
    residuals without identifying the gravity vector or an exact correction.
    The gyro median is a capture-wide point estimate with nonzero covariance.
    """

    nodes: dict[str, Any] = {}
    for node, rows in rows_by_node.items():
        acc = np.asarray(rows["acc_raw"], dtype=float) * ACC_SCALE
        gyro = np.asarray(rows["gyro_raw"], dtype=float) * GYRO_SCALE
        if len(rows) < 2:
            raise ValueError("synthetic imperfect-rest prefix requires at least two rows")
        gyro_bias = np.median(gyro, axis=0)
        acc_covariance = np.cov(acc, rowvar=False, ddof=1)
        gyro_observation_covariance = np.cov(gyro, rowvar=False, ddof=1)
        gyro_bias_covariance = (
            gyro_observation_covariance / float(len(rows))
            + np.eye(3) * float(gyro_bias_floor_rad2_s2)
        )
        nodes[str(node)] = {
            "accelerometer_mean_mps2": np.mean(acc, axis=0).tolist(),
            "accelerometer_norm_mps2": float(np.linalg.norm(np.mean(acc, axis=0))),
            "accelerometer_observation_covariance_m2_s4": acc_covariance.tolist(),
            "accelerometer_quantization_variance_m2_s4": float(ACC_SCALE**2 / 12.0),
            "effective_rows": float(len(rows)),
            "gravity_sensor_unit": (
                np.mean(acc, axis=0) / max(
                    float(np.linalg.norm(np.mean(acc, axis=0))), np.finfo(float).eps,
                )
            ).tolist(),
            "gyro_bias_rad_s": gyro_bias.tolist(),
            "gyro_bias_covariance_rad2_s2": gyro_bias_covariance.tolist(),
            "gyro_observation_covariance_rad2_s2": gyro_observation_covariance.tolist(),
            "gyro_quantization_variance_rad2_s2": float(GYRO_SCALE**2 / 12.0),
            "rows": int(len(rows)),
            "segment": str(node),
            "status": "SYNTHETIC_IMPERFECT_REST_STOCHASTIC_ESTIMATE_NOT_TRUTH",
            "unique_accelerometer_code_rows": int(np.unique(rows["acc_raw"], axis=0).shape[0]),
            "unique_gyroscope_code_rows": int(np.unique(rows["gyro_raw"], axis=0).shape[0]),
        }
    return {
        "schema": "biospur-c2-synthetic-imperfect-rest-stochastic-state-v1",
        "nodes": nodes,
        "initial_stochastic_state_accelerometer_bias_point_estimated": False,
        "initial_stochastic_state_gravity_direction_is_diagnostic_only": True,
        "synthetic_truth_consumed_by_calibration_owner": False,
        "per_action_calibration_profile_created": False,
    }


def _synthetic_calibration_fixture_initial_still(
    fixture: SyntheticCalibrationFixture,
    *,
    duration_s: float,
    sample_period_s: float,
    gravity_mps2: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Generate a prior action from one fixed capture calibration fixture.

    The generator oracle uses its latent state only to make sensor rows.  The
    calibration owner receives only those rows; no latent matrix, offset,
    drift, mount, or truth field is passed to it.
    """

    dt = float(sample_period_s)
    time = np.arange(0.0, float(duration_s), dt)
    if len(time) < 2:
        raise ValueError("synthetic calibration initial still is too short")
    latent_time = time - float(duration_s)
    gravity_world = np.array([0.0, 0.0, float(gravity_mps2)])
    parent_gravity = fixture.parent_world_from_sensor_at_motion_start.T @ gravity_world
    child_gravity = fixture.child_world_from_sensor_at_motion_start.T @ gravity_world
    parent_acc = np.repeat(parent_gravity[None, :], len(time), axis=0)
    child_acc = np.repeat(child_gravity[None, :], len(time), axis=0)
    parent_acc = parent_acc @ fixture.parent_scale_cross_axis.T
    child_acc = child_acc @ fixture.child_scale_cross_axis.T
    parent_gyro = (
        fixture.parent_gyro_offset_rads[None, :]
        + latent_time[:, None] * fixture.parent_gyro_drift_rads2[None, :]
    )
    child_gyro = (
        fixture.child_gyro_offset_rads[None, :]
        + latent_time[:, None] * fixture.child_gyro_drift_rads2[None, :]
    )
    rng = np.random.default_rng(int(fixture.initial_still_noise_seed))
    parent_acc += _ar1_noise(
        rng,
        parent_acc.shape,
        fixture.accelerometer_noise_std_mps2,
        fixture.ar1_rho,
    )
    child_acc += _ar1_noise(
        rng,
        child_acc.shape,
        fixture.accelerometer_noise_std_mps2,
        fixture.ar1_rho,
    )
    parent_gyro += _ar1_noise(
        rng,
        parent_gyro.shape,
        fixture.gyroscope_noise_std_rads,
        fixture.ar1_rho,
    )
    child_gyro += _ar1_noise(
        rng,
        child_gyro.shape,
        fixture.gyroscope_noise_std_rads,
        fixture.ar1_rho,
    )
    return time, parent_acc, child_acc, parent_gyro, child_gyro


def _production_equivalent_synthetic_orientation_frontend(
    sample: SyntheticPair,
    *,
    settings: Mapping[str, Any],
) -> tuple[SyntheticPair, dict[str, Any]]:
    """Run one synthetic capture through the production continuous VQF owner.

    The generator-only latent calibration state makes an explicit prior still
    action, but is never passed to the owner.  Its duration reuses the already
    frozen calibration drift horizon and is not selected from a center/axis
    outcome.  One VQF object per endpoint is carried into motion without reset.
    Gravity-norm residuals update a broad, gravity-confounded calibration
    posterior.  Its predictive mixture mean is applied causally before the
    motion episode; no oracle calibration truth, gravity vector, or independent
    point-identifiability claim enters the owner.
    """

    dt = float(settings["timing"]["sample_period_s"])
    if sample.calibration_fixture is None:
        raise ValueError(
            "production-equivalent nonideal synthetic capture lacks a generator-only calibration fixture"
        )
    initial_duration_s = float(settings["synthetic"]["initial_still_duration_s"])
    if initial_duration_s <= 0.0:
        raise ValueError("synthetic initial-still fixture duration must be positive")
    (
        initial_time,
        initial_parent_acc,
        initial_child_acc,
        initial_parent_gyro,
        initial_child_gyro,
    ) = _synthetic_calibration_fixture_initial_still(
        sample.calibration_fixture,
        duration_s=initial_duration_s,
        sample_period_s=dt,
        gravity_mps2=float(settings["synthetic"]["generator_model"]["gravity_mps2"]),
    )
    prefix_rows = len(initial_time)
    parent_rows = _synthetic_imu_rows(
        acc_mps2=sample.parent_acc,
        gyro_rads=sample.parent_gyro,
        time_s=sample.time_s + initial_duration_s,
    )
    child_rows = _synthetic_imu_rows(
        acc_mps2=sample.child_acc,
        gyro_rads=sample.child_gyro,
        time_s=sample.time_s + initial_duration_s,
    )
    nodes = ("SYNTHETIC_THIGH_LEFT", "SYNTHETIC_SHANK_LEFT")
    initial_rows = {
        nodes[0]: _synthetic_imu_rows(
            acc_mps2=initial_parent_acc,
            gyro_rads=initial_parent_gyro,
            time_s=initial_time,
        ),
        nodes[1]: _synthetic_imu_rows(
            acc_mps2=initial_child_acc,
            gyro_rads=initial_child_gyro,
            time_s=initial_time,
        ),
    }
    motion_rows = {
        nodes[0]: parent_rows,
        nodes[1]: child_rows,
    }
    noise = settings["synthetic"]["estimator_input_noise"]
    initial_state = _synthetic_initial_stochastic_state(
        initial_rows,
        gyro_bias_floor_rad2_s2=float(noise["gyroscope_bias_sigma_rads"]) ** 2,
    )
    guard = _new_guard(settings)
    owner = ContinuousVQFState(
        initial_state,
        execution_guard=guard,
        sample_period_s=dt,
        unknown_boot_orientation_sigma_rad=float(
            settings["orientation"]["unknown_boot_orientation_sigma_rad"]
        ),
        unknown_unusable_episode_orientation_sigma_rad=float(
            settings["orientation"]["unknown_unusable_episode_orientation_sigma_rad"]
        ),
        calibration_settings=settings["calibration_posterior"],
    )
    initial = DecodedAction(
        action="00_initial_still",
        chronological_index=0,
        interval=(0, prefix_rows),
        rows_by_node=initial_rows,
        access_audit={"synthetic_only": True},
        decode_audit={"synthetic_only": True},
    )
    motion = DecodedAction(
        action="02_t_pose",
        chronological_index=1,
        interval=(prefix_rows, prefix_rows + len(sample.time_s)),
        rows_by_node=motion_rows,
        access_audit={"synthetic_only": True},
        decode_audit={"synthetic_only": True},
    )
    owner.process(initial)
    oriented = owner.process(motion)
    common_time_us, parent_keep, child_keep = np.intersect1d(
        oriented.time_us_by_node[nodes[0]],
        oriented.time_us_by_node[nodes[1]],
        assume_unique=True,
        return_indices=True,
    )
    if len(common_time_us) < 2:
        raise ValueError("production factor-row exclusion leaves no common synthetic motion")
    parent_bias_sigma = np.asarray(
        oriented.vqf_residual_bias_sigma_rad_s_by_node[nodes[0]], dtype=float,
    )[parent_keep]
    child_bias_sigma = np.asarray(
        oriented.vqf_residual_bias_sigma_rad_s_by_node[nodes[1]], dtype=float,
    )[child_keep]
    output = SyntheticPair(
        time_s=common_time_us.astype(float) * 1e-6,
        parent_acc=np.asarray(
            oriented.acc_mps2_by_node[nodes[0]][parent_keep], dtype=float,
        ),
        child_acc=np.asarray(
            oriented.acc_mps2_by_node[nodes[1]][child_keep], dtype=float,
        ),
        parent_gyro=np.asarray(
            oriented.gyro_rads_by_node[nodes[0]][parent_keep], dtype=float,
        ),
        child_gyro=np.asarray(
            oriented.gyro_rads_by_node[nodes[1]][child_keep], dtype=float,
        ),
        parent_axis_sensor=np.asarray(sample.parent_axis_sensor, dtype=float),
        child_axis_sensor=np.asarray(sample.child_axis_sensor, dtype=float),
        joint_to_parent_sensor_m=np.asarray(sample.joint_to_parent_sensor_m, dtype=float),
        joint_to_child_sensor_m=np.asarray(sample.joint_to_child_sensor_m, dtype=float),
        nonideality_report={
            **dict(sample.nonideality_report),
            "production_continuous_vqf_frontend_consumed": True,
            "synthetic_explicit_initial_still_rows": prefix_rows,
            "calibration_posterior_predictive_accelerometer_correction_applied": True,
            "oracle_accelerometer_scale_cross_axis_point_correction_applied": False,
            "capture_wide_calibration_posterior_predictive_mean_applied": True,
        },
    )
    audit = owner.audit()
    return output, {
        "schema": "biospur-c2-production-equivalent-synthetic-orientation-frontend-v1",
        "owner_call_path": [
            "orientation.ContinuousVQFState.process:00_initial_still",
            "orientation.ContinuousVQFState.process:02_t_pose",
        ],
        "prefix_design_owner": "EXPLICIT_SYNTHETIC_INITIAL_STILL_DURATION_NOT_COVARIANCE_HORIZON",
        "initial_still_duration_s": initial_duration_s,
        "prefix_rows": prefix_rows,
        "motion_rows": int(len(output.time_s)),
        "parent_motion_rows_before_common_time_intersection": int(
            len(oriented.time_us_by_node[nodes[0]])
        ),
        "child_motion_rows_before_common_time_intersection": int(
            len(oriented.time_us_by_node[nodes[1]])
        ),
        "common_time_intersection_does_not_fabricate_rows": True,
        "vqf_instances_per_node": int(audit["vqf_instances_per_node"]),
        "episode_reset_count": int(audit["episode_reset_count"]),
        "new_yaw_gauge_count": int(audit["new_yaw_gauge_count"]),
        "capture_wide_initial_bias_has_nonzero_covariance": bool(all(
            np.trace(np.asarray(row["gyro_bias_covariance_rad2_s2"], dtype=float)) > 0.0
            for row in initial_state["nodes"].values()
        )),
        "vqf_residual_bias_consumed": bool(all(
            oriented.audit["nodes"][node]["vqf_residual_bias_consumed_by_factor_gyro"]
            for node in nodes
        )),
        "vqf_bias_sigma_preserved": bool(
            len(parent_bias_sigma) == len(output.time_s)
            and len(child_bias_sigma) == len(output.time_s)
            and np.isfinite(parent_bias_sigma).all()
            and np.isfinite(child_bias_sigma).all()
        ),
        "parent_vqf_bias_sigma_rads_range": [
            float(np.min(parent_bias_sigma)), float(np.max(parent_bias_sigma)),
        ],
        "child_vqf_bias_sigma_rads_range": [
            float(np.min(child_bias_sigma)), float(np.max(child_bias_sigma)),
        ],
        "gravity_norm_informed_broad_calibration_posterior_updated": True,
        "gravity_vector_or_magnitude_point_estimated": False,
        "accelerometer_bias_or_scale_point_identifiability_claimed": False,
        "calibration_posterior_predictive_accelerometer_correction_applied": True,
        "oracle_accelerometer_scale_cross_axis_point_correction_applied": False,
        "capture_wide_calibration_posterior_predictive_mean_applied": True,
        "calibration_posterior_snapshot_sha256_by_node": {
            node: oriented.calibration_posterior_by_node[node]["semantic_sha256"]
            for node in nodes
        },
        "calibration_posterior_component_weights_by_node": {
            node: [
                float(row["weight"])
                for row in oriented.calibration_posterior_by_node[node]["branches"]
            ]
            for node in nodes
        },
        "calibration_posterior_actions_by_node": {
            node: list(
                oriented.calibration_posterior_by_node[node]["actions_consumed"]
            )
            for node in nodes
        },
        "remaining_calibration_nuisance_role": (
            "SHARED_CAPTURE_WIDE_SYSTEMATIC_POSTERIOR_INPUT_NOT_INDEPENDENT_ROWS"
        ),
        "per_action_calibration_profile_created": False,
        "synthetic_truth_consumed_by_calibration_owner": False,
        "real_payload_opened": False,
        "heldout_opened": False,
    }


def _ar1_noise(rng: np.random.Generator, shape: tuple[int, int], sigma: float, rho: float) -> np.ndarray:
    innovation = rng.normal(0.0, sigma * np.sqrt(max(0.0, 1.0 - rho**2)), size=shape)
    output = np.empty(shape, dtype=float)
    output[0] = innovation[0]
    for index in range(1, shape[0]):
        output[index] = rho * output[index - 1] + innovation[index]
    return output


def _angular_velocity(world_from_sensor: np.ndarray, dt: float) -> np.ndarray:
    delta = np.einsum("nji,njk->nik", world_from_sensor[:-1], world_from_sensor[1:])
    step = Rotation.from_matrix(delta).as_rotvec() / dt
    output = np.empty((len(world_from_sensor), 3), dtype=float)
    output[1:-1] = 0.5 * (step[:-1] + step[1:])
    output[0] = step[0]
    output[-1] = step[-1]
    return output


def generate_physical_pair(
    seed: int,
    *,
    model: Mapping[str, Any],
    duration_s: float = 10.0,
    sample_period_s: float = 0.005,
    excitation_scale: float = 1.0,
    nonideal: bool = True,
    wear_mode: str = "haar",
    parent_dimension_m: float = 0.48,
    child_dimension_m: float = 0.43,
    asymmetry_fraction: float = 0.0,
    imperfect_rest_return: bool = True,
    observation_level: float = 1.0,
) -> SyntheticPair:
    """Simulate two arbitrarily mounted sensors on a shared hinge joint.

    The generator constructs world-frame rigid-body motion and then produces
    local accelerometer and gyroscope measurements. It does not call or copy
    the estimator's norm residual, derivative filter, or QMT objective.
    """

    rng = np.random.default_rng(int(seed))
    base_amplitude = np.asarray(model["base_xyz_amplitude_rad"], dtype=float)
    base_frequency = np.asarray(model["base_xyz_frequency_rad_s"], dtype=float)
    base_phase = np.asarray(model["base_xyz_phase_rad"], dtype=float)
    relative_amplitude = np.asarray(model["relative_amplitude_rad"], dtype=float)
    relative_frequency = np.asarray(model["relative_frequency_rad_s"], dtype=float)
    relative_phase = np.asarray(model["relative_phase_rad"], dtype=float)
    time = np.arange(0.0, float(duration_s), float(sample_period_s))
    envelope = np.ones_like(time)
    if imperfect_rest_return:
        edge = min(
            len(time) // 8,
            int(round(float(model["rest_ramp_duration_s"]) / sample_period_s)),
        )
        ramp = np.linspace(float(model["rest_start_fraction"]), 1.0, max(edge, 1))
        envelope[:edge] = ramp
        envelope[-edge:] = ramp[::-1] * float(model["rest_final_multiplier"])
    base_angles = excitation_scale * envelope[:, None] * np.column_stack((
        base_amplitude[0] * np.sin(base_frequency[0] * time + base_phase[0]),
        base_amplitude[1] * np.sin(base_frequency[1] * time + base_phase[1]),
        base_amplitude[2] * np.cos(base_frequency[2] * time + base_phase[2]),
    ))
    relative = excitation_scale * envelope * (
        relative_amplitude[0] * np.sin(relative_frequency[0] * time + relative_phase[0])
        + relative_amplitude[1] * np.sin(relative_frequency[1] * time + relative_phase[1])
    )
    world_from_parent_segment = Rotation.from_euler("xyz", base_angles).as_matrix()
    hinge_frequency = np.asarray(model["nonideal_hinge_axis_frequency_rad_s"], dtype=float)
    nonideal_cross_axis = np.deg2rad(
        float(model["nonideal_hinge_cross_axis_deg"]) if nonideal else 0.0
    )
    relative_axes = np.column_stack((
        nonideal_cross_axis * np.sin(hinge_frequency[0] * time),
        np.ones_like(time),
        nonideal_cross_axis * np.cos(hinge_frequency[1] * time),
    ))
    relative_axes /= np.linalg.norm(relative_axes, axis=1, keepdims=True)
    relative_rotation = Rotation.from_rotvec(relative[:, None] * relative_axes).as_matrix()
    world_from_child_segment = np.einsum(
        "nij,njk->nik", world_from_parent_segment, relative_rotation,
    )
    if wear_mode == "near_uninformative_hemisphere":
        wear_z = float(model["near_uninformative_wear_z"])
        wear = np.array([1.0, 0.0, wear_z])
        wear /= np.linalg.norm(wear)
        seed_axis = np.array([0.0, 1.0, 0.0])
        first = seed_axis - wear * float(seed_axis @ wear)
        first /= np.linalg.norm(first)
        second = np.cross(wear, first)
        parent_segment_from_sensor = np.column_stack((first, second, wear)) @ Rotation.from_rotvec([
            0.0, 0.0, float(model["near_uninformative_parent_twist_rad"]),
        ]).as_matrix()
        child_segment_from_sensor = np.column_stack((-first, second, -wear)) @ Rotation.from_rotvec([
            0.0, 0.0, float(model["near_uninformative_child_twist_rad"]),
        ]).as_matrix()
    elif wear_mode == "haar":
        parent_segment_from_sensor = Rotation.random(random_state=rng).as_matrix()
        child_segment_from_sensor = Rotation.random(random_state=rng).as_matrix()
    else:
        raise ValueError(f"unknown synthetic wear mode {wear_mode}")
    parent_slip_axis = rng.normal(size=3)
    parent_slip_axis /= np.linalg.norm(parent_slip_axis)
    child_slip_axis = rng.normal(size=3)
    child_slip_axis /= np.linalg.norm(child_slip_axis)
    slip_frequency = np.asarray(model["strap_slip_frequency_rad_s"], dtype=float)
    slip_amplitude = np.deg2rad(
        float(model["strap_slip_peak_deg"]) if nonideal else 0.0
    )
    parent_slip = Rotation.from_rotvec(
        np.sin(slip_frequency[0] * time)[:, None] * slip_amplitude * parent_slip_axis,
    ).as_matrix()
    child_slip = Rotation.from_rotvec(
        np.sin(slip_frequency[1] * time + float(model["strap_slip_child_phase_rad"]))[:, None]
        * slip_amplitude * child_slip_axis,
    ).as_matrix()
    world_from_parent_sensor = np.einsum(
        "nij,jk,nkl->nil", world_from_parent_segment, parent_segment_from_sensor, parent_slip,
    )
    world_from_child_sensor = np.einsum(
        "nij,jk,nkl->nil", world_from_child_segment, child_segment_from_sensor, child_slip,
    )
    parent_axis = parent_segment_from_sensor.T @ np.array([0.0, 1.0, 0.0])
    child_axis = child_segment_from_sensor.T @ np.array([0.0, 1.0, 0.0])
    parent_direction = rng.normal(size=3)
    parent_direction /= np.linalg.norm(parent_direction)
    child_direction = rng.normal(size=3)
    child_direction /= np.linalg.norm(child_direction)
    dimension_fraction = float(model["joint_to_sensor_dimension_fraction"])
    parent_r = parent_direction * dimension_fraction * float(parent_dimension_m) * (1.0 + float(asymmetry_fraction))
    child_r = child_direction * dimension_fraction * float(child_dimension_m) * (1.0 - float(asymmetry_fraction))
    parent_gyro = _angular_velocity(world_from_parent_sensor, sample_period_s)
    child_gyro = _angular_velocity(world_from_child_sensor, sample_period_s)
    parent_alpha = np.gradient(parent_gyro, sample_period_s, axis=0, edge_order=2)
    child_alpha = np.gradient(child_gyro, sample_period_s, axis=0, edge_order=2)
    joint_amplitude = np.asarray(model["joint_specific_amplitude_mps2"], dtype=float)
    joint_frequency = np.asarray(model["joint_specific_frequency_rad_s"], dtype=float)
    joint_phase = np.asarray(model["joint_specific_phase_rad"], dtype=float)
    joint_specific_world = np.column_stack((
        joint_amplitude[0] * np.sin(joint_frequency[0] * time + joint_phase[0]),
        joint_amplitude[1] * np.cos(joint_frequency[1] * time + joint_phase[1]),
        float(model["gravity_mps2"])
        + joint_amplitude[2] * np.sin(joint_frequency[2] * time + joint_phase[2]),
    ))
    parent_joint_specific = np.einsum(
        "nji,nj->ni", world_from_parent_sensor, joint_specific_world,
    )
    child_joint_specific = np.einsum(
        "nji,nj->ni", world_from_child_sensor, joint_specific_world,
    )
    center_frequency = np.asarray(model["center_migration_frequency_rad_s"], dtype=float)
    center_migration = float(model["center_migration_peak_m"]) if nonideal else 0.0
    parent_r_nominal_time = parent_r + center_migration * np.column_stack((
        np.sin(center_frequency[0] * time), np.zeros_like(time),
        np.cos(center_frequency[1] * time),
    ))
    child_r_nominal_time = child_r + center_migration * np.column_stack((
        np.zeros_like(time), np.sin(center_frequency[2] * time),
        np.cos(center_frequency[3] * time),
    ))
    parent_r_time = np.einsum("nji,nj->ni", parent_slip, parent_r_nominal_time)
    child_r_time = np.einsum("nji,nj->ni", child_slip, child_r_nominal_time)
    parent_acc = (
        parent_joint_specific
        + np.cross(parent_alpha, parent_r_time)
        + np.cross(parent_gyro, np.cross(parent_gyro, parent_r_time))
    )
    child_acc = (
        child_joint_specific
        + np.cross(child_alpha, child_r_time)
        + np.cross(child_gyro, np.cross(child_gyro, child_r_time))
    )
    calibration_fixture = None
    if nonideal:
        scale_std = float(model["scale_cross_axis_std"]) * observation_level
        parent_scale = np.eye(3) + rng.normal(0.0, scale_std, size=(3, 3))
        child_scale = np.eye(3) + rng.normal(0.0, scale_std, size=(3, 3))
        parent_acc = parent_acc @ parent_scale.T
        child_acc = child_acc @ child_scale.T
        parent_gyro = parent_gyro @ parent_scale.T
        child_gyro = child_gyro @ child_scale.T
        slow_frequency = np.asarray(model["slow_artifact_frequency_rad_s"], dtype=float)
        slow_phase = np.asarray(model["slow_artifact_phase_rad"], dtype=float)
        slow_artifact = float(model["slow_artifact_amplitude_mps2"]) * np.column_stack((
            np.sin(slow_frequency[0] * time + slow_phase[0]),
            np.sin(slow_frequency[1] * time + slow_phase[1]),
            np.cos(slow_frequency[2] * time + slow_phase[2]),
        ))
        rho_interval = np.asarray(model["ar1_rho_uniform"], dtype=float)
        rho = float(rng.uniform(rho_interval[0], rho_interval[1]))
        acc_noise = float(model["accelerometer_noise_std_mps2"]) * observation_level
        parent_acc += observation_level * slow_artifact + _ar1_noise(rng, parent_acc.shape, acc_noise, rho)
        child_acc -= float(model["child_slow_artifact_multiplier"]) * observation_level * slow_artifact + _ar1_noise(rng, child_acc.shape, acc_noise, rho)
        drift_bound = float(model["gyro_linear_drift_bound_rads2"])
        parent_drift = rng.uniform(-drift_bound, drift_bound, size=3)
        child_drift = rng.uniform(-drift_bound, drift_bound, size=3)
        gyro_noise = float(model["gyro_white_noise_std_rads"]) * observation_level
        gyro_offset = float(model["gyro_static_offset_std_rads"]) * observation_level
        parent_gyro_noise = _ar1_noise(rng, parent_gyro.shape, gyro_noise, rho)
        parent_offset = rng.normal(0.0, gyro_offset, size=3)
        child_gyro_noise = _ar1_noise(rng, child_gyro.shape, gyro_noise, rho)
        child_offset = rng.normal(0.0, gyro_offset, size=3)
        parent_gyro += (
            parent_gyro_noise
            + parent_offset
            + time[:, None] * parent_drift
        )
        child_gyro += (
            child_gyro_noise
            + child_offset
            + time[:, None] * child_drift
        )
        calibration_fixture = SyntheticCalibrationFixture(
            parent_scale_cross_axis=parent_scale.copy(),
            child_scale_cross_axis=child_scale.copy(),
            parent_gyro_offset_rads=parent_offset.copy(),
            child_gyro_offset_rads=child_offset.copy(),
            parent_gyro_drift_rads2=parent_drift.copy(),
            child_gyro_drift_rads2=child_drift.copy(),
            parent_world_from_sensor_at_motion_start=world_from_parent_sensor[0].copy(),
            child_world_from_sensor_at_motion_start=world_from_child_sensor[0].copy(),
            ar1_rho=rho,
            accelerometer_noise_std_mps2=acc_noise,
            gyroscope_noise_std_rads=gyro_noise,
            initial_still_noise_seed=int(seed) ^ 0x43A1B5,
        )
        # Apply the actual JY61P output lattice after nonideal analog effects.
        parent_acc = np.rint(parent_acc / ACC_SCALE) * ACC_SCALE
        child_acc = np.rint(child_acc / ACC_SCALE) * ACC_SCALE
        parent_gyro = np.rint(parent_gyro / GYRO_SCALE) * GYRO_SCALE
        child_gyro = np.rint(child_gyro / GYRO_SCALE) * GYRO_SCALE
    return SyntheticPair(
        time_s=time,
        parent_acc=parent_acc,
        child_acc=child_acc,
        parent_gyro=parent_gyro,
        child_gyro=child_gyro,
        parent_axis_sensor=parent_axis,
        child_axis_sensor=child_axis,
        joint_to_parent_sensor_m=parent_r,
        joint_to_child_sensor_m=child_r,
        nonideality_report={
            "ar1_correlation": rho if nonideal else 0.0,
            "quantized_to_jy61p_lattice": bool(nonideal),
            "strap_slip_peak_deg": float(model["strap_slip_peak_deg"]) if nonideal else 0.0,
            "axis_migration_peak_deg": float(model["nonideal_hinge_cross_axis_deg"]) if nonideal else 0.0,
            "center_migration_peak_m": center_migration,
            "center_vector_rotated_into_current_slipped_sensor_frame": True,
            "nonideal_hinge": bool(nonideal),
            "bias_drift": bool(nonideal),
            "wear_mode": wear_mode,
            "near_uninformative_wear_dot_nominal": (
                float(wear_z / np.sqrt(1.0 + wear_z**2))
                if wear_mode == "near_uninformative_hemisphere" else None
            ),
            "parent_dimension_m": float(parent_dimension_m),
            "child_dimension_m": float(child_dimension_m),
            "asymmetry_fraction": float(asymmetry_fraction),
            "parent_joint_to_sensor_norm_m": float(np.linalg.norm(parent_r)),
            "child_joint_to_sensor_norm_m": float(np.linalg.norm(child_r)),
            "imperfect_rest_return": bool(imperfect_rest_return),
            "start_excitation_envelope": float(envelope[0]),
            "final_excitation_envelope": float(envelope[-1]),
            "time_varying_yaw_motion_consumed": True,
            "observation_level": float(observation_level),
            "input_path_consumed_by_estimator": True,
        },
        calibration_fixture=calibration_fixture,
    )


def _identity_alignment(rows: int, dt: float) -> PairAlignment:
    indices = np.arange(rows, dtype=int)
    return PairAlignment(
        parent_indices=indices,
        child_indices=indices,
        lag_samples=0,
        report={
            "schema": "biospur-c2-synthetic-known-zero-lag-v1",
            "sample_period_s": dt,
            "selected_lag_samples": 0,
            "lag_uncertainty_s": dt,
            "status": "ORACLE_ZERO_LAG",
        },
    )


def _synthetic_pair_provenance(
    *, action: str, seed: int | str, fixture: str,
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    provenance: dict[str, Any] = {
        "schema": "biospur-c2-synthetic-owner-produced-aligned-pair-v1",
        "owner": "synthetic",
        "action": str(action),
        "edge": "knee_left",
        "parent_node": "SYNTHETIC_THIGH_LEFT",
        "child_node": "SYNTHETIC_SHANK_LEFT",
        "seed_or_fixture_id": str(seed),
        "fixture": str(fixture),
        "result_independent_construction": True,
        "real_capture_rows_opened": False,
        "caller_pose_or_truth_fields_entered_estimator": False,
    }
    if extra is not None:
        provenance.update(dict(extra))
    return provenance


def _pair_with_gap_pattern(
    sample: SyntheticPair,
    dt: float,
    *,
    seed: int,
    minimum_gap_rows: int,
    maximum_gap_rows_inclusive: int,
) -> AlignedPair:
    """Remove rows and retain exact span boundaries; never concatenate them."""

    n = len(sample.time_s)
    rng = np.random.default_rng(int(seed))
    gap_start = int(rng.integers(n // 3, 2 * n // 3))
    gap_rows = int(rng.integers(
        int(minimum_gap_rows), int(maximum_gap_rows_inclusive) + 1,
    ))
    keep = np.ones(n, dtype=bool)
    keep[gap_start:gap_start + gap_rows] = False
    original = np.flatnonzero(keep)
    breaks = np.flatnonzero(np.diff(original) != 1) + 1
    boundaries = np.r_[0, breaks, len(original)]
    spans = tuple(slice(int(left), int(right)) for left, right in zip(boundaries[:-1], boundaries[1:]))
    return AlignedPair(
        edge="knee_left",
        action=f"SYNTHETIC_{seed}",
        parent_acc=sample.parent_acc[keep],
        child_acc=sample.child_acc[keep],
        parent_gyro=sample.parent_gyro[keep],
        child_gyro=sample.child_gyro[keep],
        parent_observed_time_s=sample.time_s[keep],
        child_observed_time_s=sample.time_s[keep],
        parent_boot_epoch=np.zeros(np.count_nonzero(keep), dtype=np.int64),
        child_boot_epoch=np.zeros(np.count_nonzero(keep), dtype=np.int64),
        alignment=_identity_alignment(int(np.count_nonzero(keep)), dt),
        contiguous_spans=spans,
        provenance=_synthetic_pair_provenance(
            action=f"SYNTHETIC_{seed}", seed=seed, fixture="REGISTERED_RANDOM_GAP_PATTERN",
            extra={"removed_gap_rows": gap_rows},
        ),
    )


def _pair_with_observation_conditions(
    sample: SyntheticPair,
    dt: float,
    *,
    seed: int,
    gap_rows: int,
    duplicate_rows: int,
    jitter_us: int,
    clipping_rows: int,
) -> tuple[AlignedPair, dict[str, Any]]:
    """Apply raw/timing conditions through the real factor-row owner."""

    rng = np.random.default_rng(int(seed))
    acc_parent = np.rint(sample.parent_acc / ACC_SCALE).astype(np.int64)
    acc_child = np.rint(sample.child_acc / ACC_SCALE).astype(np.int64)
    gyro_parent = np.rint(sample.parent_gyro / GYRO_SCALE).astype(np.int64)
    gyro_child = np.rint(sample.child_gyro / GYRO_SCALE).astype(np.int64)
    time_us = np.rint(sample.time_s * 1e6).astype(np.int64) + 1_000_000
    boot = np.zeros(len(time_us), dtype=np.int64)
    injected: dict[str, Any] = {
        "gap_rows": int(gap_rows), "duplicate_rows": int(duplicate_rows),
        "jitter_us": int(jitter_us), "clipping_rows": int(clipping_rows),
    }
    if gap_rows:
        start = len(time_us) // 2
        keep = np.ones(len(time_us), dtype=bool)
        keep[start:start + int(gap_rows)] = False
        time_us, boot = time_us[keep], boot[keep]
        acc_parent, acc_child = acc_parent[keep], acc_child[keep]
        gyro_parent, gyro_child = gyro_parent[keep], gyro_child[keep]
    if duplicate_rows:
        for _ in range(int(duplicate_rows)):
            index = len(time_us) // 3
            time_us = np.insert(time_us, index + 1, time_us[index])
            boot = np.insert(boot, index + 1, boot[index])
            acc_parent = np.insert(acc_parent, index + 1, acc_parent[index], axis=0)
            acc_child = np.insert(acc_child, index + 1, acc_child[index], axis=0)
            gyro_parent = np.insert(gyro_parent, index + 1, gyro_parent[index], axis=0)
            gyro_child = np.insert(gyro_child, index + 1, gyro_child[index], axis=0)
    if jitter_us:
        index = 2 * len(time_us) // 3
        time_us[index] += int(jitter_us)
    if clipping_rows:
        candidates = rng.choice(len(time_us), size=min(int(clipping_rows), len(time_us)), replace=False)
        acc_parent[candidates, 0] = 32767
        acc_child[candidates, 1] = -32768
        gyro_parent[candidates, 2] = 32767
        gyro_child[candidates, 0] = -32768
        injected["clipping_indices"] = sorted(int(value) for value in candidates)
    parent_quality = assess_factor_rows(time_us, boot, acc_parent, gyro_parent)
    child_quality = assess_factor_rows(time_us, boot, acc_child, gyro_child)
    retained = np.intersect1d(parent_quality.retained_indices, child_quality.retained_indices)
    if len(retained) < 50:
        raise ValueError("synthetic observation condition left too few estimator rows")
    retained_time = time_us[retained]
    retained_boot = boot[retained]
    breaks = (np.diff(retained_boot) != 0) | (np.diff(retained_time) != int(round(dt * 1e6)))
    boundaries = np.r_[0, np.flatnonzero(breaks) + 1, len(retained)]
    spans = tuple(
        slice(int(left), int(right)) for left, right in zip(boundaries[:-1], boundaries[1:])
        if right - left >= 25
    )
    keep = np.concatenate([np.arange(span.start, span.stop) for span in spans])
    retained = retained[keep]
    lengths = [span.stop - span.start for span in spans]
    packed_spans = []
    cursor = 0
    for length in lengths:
        packed_spans.append(slice(cursor, cursor + length))
        cursor += length
    inflation = max(parent_quality.covariance_inflation, child_quality.covariance_inflation)
    pair = AlignedPair(
        edge="knee_left", action=f"SYNTHETIC_{seed}",
        parent_acc=acc_parent[retained].astype(float) * ACC_SCALE,
        child_acc=acc_child[retained].astype(float) * ACC_SCALE,
        parent_gyro=gyro_parent[retained].astype(float) * GYRO_SCALE,
        child_gyro=gyro_child[retained].astype(float) * GYRO_SCALE,
        parent_observed_time_s=retained_time[keep].astype(float) * 1e-6,
        child_observed_time_s=retained_time[keep].astype(float) * 1e-6,
        parent_boot_epoch=retained_boot[keep].astype(np.int64),
        child_boot_epoch=retained_boot[keep].astype(np.int64),
        alignment=PairAlignment(
            parent_indices=retained.copy(), child_indices=retained.copy(), lag_samples=0,
            report={
                "schema": "biospur-c2-synthetic-factor-quality-alignment-v1",
                "sample_period_s": dt, "selected_lag_samples": 0,
                "lag_uncertainty_s": float(np.hypot(dt, abs(jitter_us) * 1e-6)),
                "status": "ORACLE_ZERO_LAG_WITH_OBSERVATION_MUTATIONS",
            },
        ),
        contiguous_spans=tuple(packed_spans),
        provenance=_synthetic_pair_provenance(
            action=f"SYNTHETIC_{seed}", seed=seed,
            fixture="REGISTERED_OBSERVATION_CONDITIONS",
            extra={"injected_conditions": dict(injected)},
        ),
    )
    return pair, {
        "coverage_class": "EXECUTED_OWNER_LEVEL",
        "owner_call_path": [
            "orientation.assess_factor_rows",
            "functional_geometry.estimate_hinge_axis_qmt",
            "functional_geometry.estimate_joint_center_pair_local",
        ],
        "injected": injected,
        "parent_quality": dict(parent_quality.report),
        "child_quality": dict(child_quality.report),
        "joint_covariance_inflation": inflation,
        "estimator_span_count": len(packed_spans),
        "estimator_span_lengths": lengths,
    }


def _new_guard(settings: Mapping[str, Any]) -> C2ExecutionGuard:
    guard = C2ExecutionGuard(settings)
    guard.begin_capture("C2")
    return guard


def _numeric_center_gap_eligibility_gate(
    settings: Mapping[str, Any],
) -> dict[str, Any]:
    """Prove a gap boundary cannot manufacture center-update eligibility."""

    fixture = settings["synthetic"]["mutation_fixtures"]["center_ideal_diagnostic"]
    sample = generate_physical_pair(
        int(fixture["seed"]),
        model=settings["synthetic"]["generator_model"],
        duration_s=float(fixture["duration_s"]),
        sample_period_s=float(fixture["sample_period_s"]),
        excitation_scale=float(fixture["excitation_scale"]),
        nonideal=bool(fixture["nonideal"]),
        wear_mode=str(fixture["wear_mode"]),
        parent_dimension_m=float(fixture["parent_dimension_m"]),
        child_dimension_m=float(fixture["child_dimension_m"]),
        asymmetry_fraction=float(fixture["asymmetry_fraction"]),
        imperfect_rest_return=bool(fixture["imperfect_rest_return"]),
        observation_level=float(fixture["observation_level"]),
    )
    dt = float(fixture["sample_period_s"])
    # Gap-free: 5820 raw rows -> 5800 retained derivative rows -> 29 blocks.
    gap_free_indices = np.arange(0, 5820, dtype=int)
    # Gapped: two spans with 3020 and 2820 raw rows and a skipped 20-row
    # physical gap -> 3000 + 2800 retained derivative rows -> the same 29
    # complete 200-row center blocks. The gap adds no evidence.
    first = np.arange(0, 3020, dtype=int)
    second = np.arange(3040, 5860, dtype=int)
    gapped_indices = np.r_[first, second]

    def make_pair(indices: np.ndarray, spans: tuple[slice, ...], action: str) -> AlignedPair:
        return AlignedPair(
            edge="knee_left", action=action,
            parent_acc=sample.parent_acc[indices],
            child_acc=sample.child_acc[indices],
            parent_gyro=sample.parent_gyro[indices],
            child_gyro=sample.child_gyro[indices],
            parent_observed_time_s=sample.time_s[indices],
            child_observed_time_s=sample.time_s[indices],
            parent_boot_epoch=np.zeros(len(indices), dtype=np.int64),
            child_boot_epoch=np.zeros(len(indices), dtype=np.int64),
            alignment=_identity_alignment(len(indices), dt),
            contiguous_spans=spans,
            provenance=_synthetic_pair_provenance(
                action=action, seed=int(fixture["seed"]),
                fixture="CENTER_GAP_DOES_NOT_CREATE_ELIGIBILITY",
                extra={
                    "source_index_half_open": (
                        [[0, 5820]] if action.endswith("GAP_FREE")
                        else [[0, 3020], [3040, 5860]]
                    ),
                },
            ),
        )

    gap_free_pair = make_pair(
        gap_free_indices, (slice(0, len(gap_free_indices)),), "CENTER_GAP_FREE",
    )
    gapped_pair = make_pair(
        gapped_indices,
        (slice(0, len(first)), slice(len(first), len(gapped_indices))),
        "CENTER_ONE_GAP",
    )
    noise = settings["synthetic"]["estimator_input_noise"]
    covariance = np.eye(3) * float(noise["accelerometer_sigma_mps2"]) ** 2
    gyro_covariance = np.eye(3) * float(noise["gyroscope_sigma_rads"]) ** 2
    gyro_bias_covariance = np.eye(3) * float(
        noise["gyroscope_bias_sigma_rads"]
    ) ** 2
    outputs = []
    for pair in (gap_free_pair, gapped_pair):
        center = estimate_joint_center_pair_local(
            "knee_left", "thigh_left", "shank_left", [pair],
            settings=settings["joint_center"],
            parent_acc_covariance=covariance,
            child_acc_covariance=covariance,
            parent_gyro_observation_covariance=gyro_covariance,
            child_gyro_observation_covariance=gyro_covariance,
            parent_gyro_bias_covariance=gyro_bias_covariance,
            child_gyro_bias_covariance=gyro_bias_covariance,
            execution_guard=_new_guard(settings),
        )
        outputs.append({
            "action": pair.action,
            "raw_rows": len(pair.parent_acc),
            "input_span_count": len(pair.contiguous_spans),
            "complete_center_blocks": int(center.report["contiguous_span_blocks"]),
            "rows_after_blockwise_cap": int(center.report["rows_after_blockwise_cap"]),
            "owner_update_eligible": bool(center.report["owner_update_eligible"]),
            "owner_update_mode": center.report["owner_update_mode"],
            "rows_differentiated_or_capped_across_gap": int(
                center.report["rows_differentiated_or_capped_across_gap"]
            ),
            "condition_number": float(center.report["condition_number"]),
            "multistart_basin_identifiable": bool(
                center.report["multistart_basin_identifiable"]
            ),
            "chronological_prefix_heldin_available": bool(
                center.report["chronological_prefix_heldin_stability"]["available"]
            ),
            "matched_gap_local_acc_gyro_alpha_preprocessing": bool(
                center.report["matched_gap_local_acc_gyro_alpha_preprocessing"]
            ),
            "p1_covariance_reduced_by_smoothing": bool(
                center.report[
                    "p1_accelerometer_or_gyro_covariance_reduced_by_smoothing"
                ]
            ),
            "cross_prefix_heldin_filter_support_rows": int(
                center.report["cross_prefix_heldin_filter_support_rows"]
            ),
            "cross_complete_block_filter_support_rows": int(
                center.report["cross_complete_block_filter_support_rows"]
            ),
            "filtered_rows_counted_as_more_information_than_raw_rows": bool(
                center.report[
                    "filtered_rows_counted_as_more_information_than_raw_rows"
                ]
            ),
            "serial_correlation_variance_envelope_multiplier": float(
                center.report[
                    "serial_correlation_variance_envelope_multiplier"
                ]
            ),
            "kernel_covariance_envelope_psd_dominates": bool(
                center.report["kernel_covariance_envelope_psd_dominates"]
            ),
            "retained_to_raw_complete_block_row_ratio": float(
                center.report["retained_to_raw_complete_block_row_ratio"]
            ),
            "pair_clock_offset_convention": str(
                center.report["pair_clock_offset_convention"]
            ),
            "systematic_clock_nuisance_jacobian": str(
                center.report["systematic_clock_nuisance_jacobian"]
            ),
            "unsigned_clock_magnitude_used_as_systematic_direction": bool(
                center.report[
                    "unsigned_acceleration_norm_magnitude_used_as_systematic_clock_direction"
                ]
            ),
            "signed_clock_gradient_negative_row_count": int(
                center.report["signed_clock_gradient_negative_row_count"]
            ),
            "signed_clock_gradient_positive_row_count": int(
                center.report["signed_clock_gradient_positive_row_count"]
            ),
            "signed_clock_gradient_cross_block_or_gap_derivative_count": int(
                center.report[
                    "signed_clock_gradient_cross_block_or_gap_derivative_count"
                ]
            ),
        })
    return {
        "coverage_class": "EXECUTED_OWNER_LEVEL",
        "owner_call_path": [
            "functional_geometry.estimate_joint_center_pair_local",
            "functional_geometry._center_terms",
            "functional_geometry._blockwise_uniform_cap",
        ],
        "fixture": dict(fixture),
        "gap_free": outputs[0],
        "one_gap": outputs[1],
        "pass": bool(
            outputs[0]["complete_center_blocks"] == 29
            and outputs[1]["complete_center_blocks"] == 29
            and outputs[0]["rows_after_blockwise_cap"]
            == outputs[1]["rows_after_blockwise_cap"]
            and outputs[0]["owner_update_eligible"]
            == outputs[1]["owner_update_eligible"]
            and outputs[0]["rows_differentiated_or_capped_across_gap"] == 0
            and outputs[1]["rows_differentiated_or_capped_across_gap"] == 0
            and outputs[0]["chronological_prefix_heldin_available"]
            and outputs[1]["chronological_prefix_heldin_available"]
            and outputs[0]["matched_gap_local_acc_gyro_alpha_preprocessing"]
            and outputs[1]["matched_gap_local_acc_gyro_alpha_preprocessing"]
            and not outputs[0]["p1_covariance_reduced_by_smoothing"]
            and not outputs[1]["p1_covariance_reduced_by_smoothing"]
            and outputs[0]["cross_prefix_heldin_filter_support_rows"] == 0
            and outputs[1]["cross_prefix_heldin_filter_support_rows"] == 0
            and outputs[0]["cross_complete_block_filter_support_rows"] == 0
            and outputs[1]["cross_complete_block_filter_support_rows"] == 0
            and not outputs[0][
                "filtered_rows_counted_as_more_information_than_raw_rows"
            ]
            and not outputs[1][
                "filtered_rows_counted_as_more_information_than_raw_rows"
            ]
            and outputs[0]["serial_correlation_variance_envelope_multiplier"] >= 1.0
            and outputs[1]["serial_correlation_variance_envelope_multiplier"] >= 1.0
            and outputs[0]["kernel_covariance_envelope_psd_dominates"]
            and outputs[1]["kernel_covariance_envelope_psd_dominates"]
            and outputs[0]["retained_to_raw_complete_block_row_ratio"] < 1.0
            and outputs[1]["retained_to_raw_complete_block_row_ratio"] < 1.0
            and not outputs[0]["unsigned_clock_magnitude_used_as_systematic_direction"]
            and not outputs[1]["unsigned_clock_magnitude_used_as_systematic_direction"]
            and outputs[0]["signed_clock_gradient_negative_row_count"] > 0
            and outputs[0]["signed_clock_gradient_positive_row_count"] > 0
            and outputs[1]["signed_clock_gradient_negative_row_count"] > 0
            and outputs[1]["signed_clock_gradient_positive_row_count"] > 0
            and outputs[0][
                "signed_clock_gradient_cross_block_or_gap_derivative_count"
            ] == 0
            and outputs[1][
                "signed_clock_gradient_cross_block_or_gap_derivative_count"
            ] == 0
        ),
    }


def _numeric_center_full_owner_physical_time_gate(
    settings: Mapping[str, Any],
) -> dict[str, Any]:
    """Exercise the complete center owner with known and unknown pair gaps."""

    design = settings["joint_center"]["physical_time_full_owner_mutation"]
    if design.get("schema") != "biospur-c2-center-physical-time-full-owner-mutation-settings-v1":
        raise RuntimeError("center full-owner physical-time mutation is not registered")
    fixture = settings["synthetic"]["mutation_fixtures"]["center_ideal_diagnostic"]
    sample = generate_physical_pair(
        int(fixture["seed"]),
        model=settings["synthetic"]["generator_model"],
        duration_s=float(fixture["duration_s"]),
        sample_period_s=float(fixture["sample_period_s"]),
        excitation_scale=float(fixture["excitation_scale"]),
        nonideal=bool(fixture["nonideal"]),
        wear_mode=str(fixture["wear_mode"]),
        parent_dimension_m=float(fixture["parent_dimension_m"]),
        child_dimension_m=float(fixture["child_dimension_m"]),
        asymmetry_fraction=float(fixture["asymmetry_fraction"]),
        imperfect_rest_return=bool(fixture["imperfect_rest_return"]),
        observation_level=float(fixture["observation_level"]),
    )
    dt = float(fixture["sample_period_s"])
    first_start, first_stop = (
        int(value) for value in design["first_pair_source_half_open_rows"]
    )
    second_start, second_stop = (
        int(value) for value in design["second_pair_source_half_open_rows"]
    )
    if not (0 <= first_start < first_stop <= second_start < second_stop <= len(sample.time_s)):
        raise RuntimeError("center physical-time full-owner source slices are invalid")

    def pair_for(
        start: int,
        stop: int,
        *,
        action: str,
        observed_time_s: np.ndarray,
        boot_epoch: int,
    ) -> AlignedPair:
        rows = stop - start
        if len(observed_time_s) != rows:
            raise AssertionError("full-owner physical-time fixture length changed")
        return AlignedPair(
            edge="knee_left",
            action=action,
            parent_acc=sample.parent_acc[start:stop],
            child_acc=sample.child_acc[start:stop],
            parent_gyro=sample.parent_gyro[start:stop],
            child_gyro=sample.child_gyro[start:stop],
            parent_observed_time_s=np.asarray(observed_time_s, dtype=float),
            child_observed_time_s=np.asarray(observed_time_s, dtype=float),
            parent_boot_epoch=np.full(rows, boot_epoch, dtype=np.int64),
            child_boot_epoch=np.full(rows, boot_epoch, dtype=np.int64),
            alignment=_identity_alignment(rows, dt),
            contiguous_spans=(slice(0, rows),),
            provenance=_synthetic_pair_provenance(
                action=action,
                seed=int(fixture["seed"]),
                fixture="REGISTERED_CENTER_FULL_OWNER_PHYSICAL_TIME",
                extra={
                    "source_half_open_rows": [start, stop],
                    "boot_epoch": int(boot_epoch),
                    "observed_time_s_sha256": functional_geometry_owner._array_sha256(
                        np.asarray(observed_time_s, dtype=float)
                    ),
                },
            ),
        )

    first_time = np.asarray(sample.time_s[first_start:first_stop], dtype=float)
    second_source_time = np.asarray(
        sample.time_s[second_start:second_stop], dtype=float,
    )

    noise = settings["synthetic"]["estimator_input_noise"]
    acc_covariance = np.eye(3) * float(noise["accelerometer_sigma_mps2"]) ** 2
    gyro_covariance = np.eye(3) * float(noise["gyroscope_sigma_rads"]) ** 2
    gyro_bias_covariance = np.eye(3) * float(
        noise["gyroscope_bias_sigma_rads"]
    ) ** 2

    def run_owner(label: str, second_time: np.ndarray, second_boot: int) -> CenterEstimate:
        first_pair = pair_for(
            first_start, first_stop,
            action=f"{label}_A",
            observed_time_s=first_time,
            boot_epoch=0,
        )
        second_pair = pair_for(
            second_start, second_stop,
            action=f"{label}_B",
            observed_time_s=second_time,
            boot_epoch=second_boot,
        )
        return estimate_joint_center_pair_local(
            "knee_left", "thigh_left", "shank_left",
            [first_pair, second_pair],
            settings=settings["joint_center"],
            parent_acc_covariance=acc_covariance,
            child_acc_covariance=acc_covariance,
            parent_gyro_observation_covariance=gyro_covariance,
            child_gyro_observation_covariance=gyro_covariance,
            parent_gyro_bias_covariance=gyro_bias_covariance,
            child_gyro_bias_covariance=gyro_bias_covariance,
            execution_guard=_new_guard(settings),
        )

    gap_values = [float(value) for value in design["same_boot_additional_gap_s"]]
    if len(gap_values) != 2 or not 0.0 < gap_values[0] < gap_values[1]:
        raise RuntimeError("center known-gap mutation bracket changed")
    known_results = []
    for index, gap_s in enumerate(gap_values):
        known_results.append(run_owner(
            f"KNOWN_GAP_{index}",
            second_source_time + gap_s,
            0,
        ))
    reset_time = (
        second_source_time
        - second_source_time[0]
        + float(design["unknown_reset_second_pair_time_origin_s"])
    )
    reset_result = run_owner("UNKNOWN_RESET", reset_time, 1)

    def physical_time_provenance(result: CenterEstimate) -> Mapping[str, Any]:
        return result.report["coherent_nuisance_refit_audit"][
            "physical_time_elapsed_and_drift_input_provenance"
        ]

    known_audit = []
    for gap_s, result in zip(gap_values, known_results, strict=True):
        provenance = physical_time_provenance(result)
        known_audit.append({
            "additional_gap_s": gap_s,
            "owner_update_eligible": bool(result.report["owner_update_eligible"]),
            "rows_after_blockwise_cap": int(result.report["rows_after_blockwise_cap"]),
            "physical_boot_safe_spans": result.report["physical_boot_safe_spans"],
            "unknown_boot_transition_pairs": result.report[
                "unknown_boot_transition_pairs"
            ],
            "parent_observed_physical_time_s_sha256_after_cap": result.report[
                "coherent_nuisance_refit_audit"
            ]["parent_observed_physical_time_s_sha256_after_cap"],
            "parent_centered_clipped_elapsed_s_sha256": provenance[
                "parent_centered_clipped_elapsed_s_sha256"
            ],
            "child_centered_clipped_elapsed_s_sha256": provenance[
                "child_centered_clipped_elapsed_s_sha256"
            ],
            "drift_stress_input_hashes": provenance[
                "drift_stress_input_hashes"
            ],
            "physical_time_provenance_diagnostic": provenance,
            "coherent_refit_component_count": len(result.report[
                "coherent_nuisance_refit_audit"
            ]["components"]),
            "rows_differentiated_or_capped_across_gap": int(
                result.report["rows_differentiated_or_capped_across_gap"]
            ),
        })
    sensor_arrays_hash = functional_geometry_owner._array_sha256(np.column_stack((
        sample.parent_acc[first_start:first_stop],
        sample.child_acc[first_start:first_stop],
        sample.parent_gyro[first_start:first_stop],
        sample.child_gyro[first_start:first_stop],
        sample.parent_acc[second_start:second_stop],
        sample.child_acc[second_start:second_stop],
        sample.parent_gyro[second_start:second_stop],
        sample.child_gyro[second_start:second_stop],
    )))
    known_drift_differs = bool(
        known_audit[0]["drift_stress_input_hashes"]
        != known_audit[1]["drift_stress_input_hashes"]
    )
    reset_unknown = list(reset_result.report["unknown_boot_transition_pairs"])
    return {
        "schema": "biospur-c2-center-physical-time-full-owner-gate-v1",
        "coverage_class": "EXECUTED_FULL_ESTIMATOR_OWNER_LEVEL",
        "owner_call_path": [
            "functional_geometry.AlignedPair",
            "functional_geometry.estimate_joint_center_pair_local",
            "functional_geometry._center_boot_safe_centered_elapsed",
            "geometry_posterior unknown floor remains separate runtime mutation",
        ],
        "registered_design": dict(design),
        "sensor_arrays_sha256_by_call": {
            "SHORT_KNOWN_GAP": sensor_arrays_hash,
            "LONG_KNOWN_GAP": sensor_arrays_hash,
            "UNKNOWN_CROSS_PAIR_RESET": sensor_arrays_hash,
        },
        "sensor_arrays_sha256_identical_across_all_three_calls": sensor_arrays_hash,
        "known_same_boot_gap_calls": known_audit,
        "equal_retained_rows": bool(
            known_audit[0]["rows_after_blockwise_cap"]
            == known_audit[1]["rows_after_blockwise_cap"]
        ),
        "known_gap_physical_time_hashes_distinct": bool(
            known_audit[0]["parent_observed_physical_time_s_sha256_after_cap"]
            != known_audit[1]["parent_observed_physical_time_s_sha256_after_cap"]
        ),
        "known_gap_drift_stress_hashes_distinct": known_drift_differs,
        "known_gap_cross_boundary_differentiation_count": [
            row["rows_differentiated_or_capped_across_gap"]
            for row in known_audit
        ],
        "unknown_reset": {
            "owner_update_eligible": bool(reset_result.report["owner_update_eligible"]),
            "status": str(reset_result.report["status"]),
            "events": reset_unknown,
            "physical_time_provenance_diagnostic": physical_time_provenance(
                reset_result
            ),
            "elapsed_continuity_invented": bool(any(
                row["elapsed_interval_invented"] for row in reset_unknown
            )),
            "progressive_unknown_interval_floor_consumption_proven_here": bool(
                reset_result.report[
                    "progressive_unknown_interval_floor_consumption_proven_in_this_owner"
                ]
            ),
        },
        "threshold_or_nuisance_change": False,
        "attempt_125_or_132_reused": False,
        "pass": bool(
            known_audit[0]["rows_after_blockwise_cap"]
            == known_audit[1]["rows_after_blockwise_cap"]
            and known_audit[0]["parent_observed_physical_time_s_sha256_after_cap"]
            != known_audit[1]["parent_observed_physical_time_s_sha256_after_cap"]
            and known_drift_differs
            and all(
                row["rows_differentiated_or_capped_across_gap"] == 0
                and not row["unknown_boot_transition_pairs"]
                and row["physical_time_provenance_diagnostic"]["role"]
                == "RESULT_INDEPENDENT_PROVENANCE_DIAGNOSTIC_ONLY"
                and row["physical_time_provenance_diagnostic"][
                    "available_when_nominal_factor_is_local_no_update"
                ]
                and not row["physical_time_provenance_diagnostic"][
                    "may_promote_owner_update"
                ]
                for row in known_audit
            )
            and not bool(reset_result.report["owner_update_eligible"])
            and bool(reset_unknown)
            and not any(row["elapsed_interval_invented"] for row in reset_unknown)
            and bool(physical_time_provenance(reset_result)[
                "unknown_boot_transition_local_no_update"
            ])
            and not bool(physical_time_provenance(reset_result)[
                "cross_epoch_elapsed_continuity_invented"
            ])
            and not bool(reset_result.report[
                "progressive_unknown_interval_floor_consumption_proven_in_this_owner"
            ])
        ),
    }


def _numeric_center_gyro_stochastic_sensitivity_gate(
    settings: Mapping[str, Any],
) -> dict[str, Any]:
    """Prove gyro and accelerometer calibration nuisance ownership at one point."""

    fixture = settings["synthetic"]["mutation_fixtures"]["center_ideal_diagnostic"]
    sample = generate_physical_pair(
        int(fixture["seed"]),
        model=settings["synthetic"]["generator_model"],
        duration_s=float(fixture["duration_s"]),
        sample_period_s=float(fixture["sample_period_s"]),
        excitation_scale=float(fixture["excitation_scale"]),
        nonideal=bool(fixture["nonideal"]),
        wear_mode=str(fixture["wear_mode"]),
        parent_dimension_m=float(fixture["parent_dimension_m"]),
        child_dimension_m=float(fixture["child_dimension_m"]),
        asymmetry_fraction=float(fixture["asymmetry_fraction"]),
        imperfect_rest_return=bool(fixture["imperfect_rest_return"]),
        observation_level=float(fixture["observation_level"]),
    )
    rows = 5820
    pair = AlignedPair(
        edge="knee_left",
        action="CENTER_GYRO_STOCHASTIC_SENSITIVITY",
        parent_acc=sample.parent_acc[:rows],
        child_acc=sample.child_acc[:rows],
        parent_gyro=sample.parent_gyro[:rows],
        child_gyro=sample.child_gyro[:rows],
        parent_observed_time_s=sample.time_s[:rows],
        child_observed_time_s=sample.time_s[:rows],
        parent_boot_epoch=np.zeros(rows, dtype=np.int64),
        child_boot_epoch=np.zeros(rows, dtype=np.int64),
        alignment=_identity_alignment(rows, float(fixture["sample_period_s"])),
        contiguous_spans=(slice(0, rows),),
        provenance=_synthetic_pair_provenance(
            action="CENTER_GYRO_STOCHASTIC_SENSITIVITY",
            seed=int(fixture["seed"]),
            fixture="CENTER_GYRO_STOCHASTIC_UNCERTAINTY_OMISSION",
        ),
    )
    noise = settings["synthetic"]["estimator_input_noise"]
    acc_covariance = np.eye(3) * float(noise["accelerometer_sigma_mps2"]) ** 2
    gyro_covariance = np.eye(3) * float(noise["gyroscope_sigma_rads"]) ** 2
    gyro_bias_covariance = np.eye(3) * float(
        noise["gyroscope_bias_sigma_rads"]
    ) ** 2
    multipliers = [
        float(value)
        for value in settings["joint_center"][
            "gyro_stochastic_covariance_sensitivity_multipliers"
        ]
    ]
    if multipliers != [0.25, 1.0, 4.0]:
        raise RuntimeError("center gyro stochastic sensitivity bracket changed")
    accelerometer_multipliers = [
        float(value)
        for value in settings["joint_center"][
            "accelerometer_calibration_covariance_sensitivity_multipliers"
        ]
    ]
    if accelerometer_multipliers != [0.25, 1.0, 4.0]:
        raise RuntimeError("center accelerometer calibration sensitivity bracket changed")

    def execute_owner(
        multiplier: float,
        *,
        accelerometer_multiplier: float = 1.0,
    ) -> dict[str, Any]:
        center_settings = dict(settings["joint_center"])
        root_multiplier = float(np.sqrt(accelerometer_multiplier))
        for key in (
            "accelerometer_unresolved_bias_sigma_mps2",
            "accelerometer_bias_drift_rate_sigma_mps3",
            "accelerometer_scale_cross_axis_fraction_sigma",
            "accelerometer_gyro_shared_scale_cross_axis_fraction_sigma",
        ):
            center_settings[key] = float(center_settings[key]) * root_multiplier
        center = estimate_joint_center_pair_local(
            "knee_left", "thigh_left", "shank_left", [pair],
            settings=center_settings,
            parent_acc_covariance=acc_covariance,
            child_acc_covariance=acc_covariance,
            parent_gyro_observation_covariance=gyro_covariance * multiplier,
            child_gyro_observation_covariance=gyro_covariance * multiplier,
            parent_gyro_bias_covariance=gyro_bias_covariance * multiplier,
            child_gyro_bias_covariance=gyro_bias_covariance * multiplier,
            execution_guard=_new_guard(settings),
        )
        return {
            "covariance_multiplier": multiplier,
            "accelerometer_calibration_covariance_multiplier": (
                accelerometer_multiplier
            ),
            "owner_update_eligible": bool(center.report["owner_update_eligible"]),
            "residual_sigma_rms_mps2": float(center.report[
                "selected_stochastic_residual_sigma_audit"
            ]["residual_sigma_rms_mps2"]),
            "information_trace_m2_inv": float(np.trace(np.asarray(
                center.report["gauge_reduced_robust_bread_information_m2_inv"],
                dtype=float,
            ))),
            "informed_covariance_trace_m2": float(np.trace(np.asarray(
                center.report["informed_observation_covariance_m2"], dtype=float,
            ))),
            "systematic_covariance_trace_m2": float(np.trace(np.asarray(
                center.report["total_systematic_covariance_m2"], dtype=float,
            ))),
            "gyro_bias_drift_systematic_covariance_trace_m2": float(np.trace(
                np.asarray(
                    center.report[
                        "gyro_bias_drift_systematic_covariance_m2"
                    ],
                    dtype=float,
                )
            )),
            "total_covariance_trace_m2": float(np.trace(center.covariance_m2)),
            "savgol_alpha_observation_noise_gain_s2_inv": float(
                center.report["savgol_alpha_observation_noise_gain_s2_inv"]
            ),
            "cluster_robust_complete_block_count": int(
                center.report["cluster_robust_complete_block_count"]
            ),
            "shared_nuisance_shrinks_as_episode_information": bool(
                center.report[
                    "shared_accelerometer_gyro_bias_drift_scale_cross_axis_and_clock_shrink_as_episode_information"
                ]
            ),
            "fixed_linearization_gyro_covariance_sensitivity": [
                dict(row) for row in center.report[
                    "fixed_linearization_gyro_covariance_sensitivity"
                ]
            ],
            "fixed_linearization_accelerometer_calibration_covariance_sensitivity": [
                dict(row) for row in center.report[
                    "fixed_linearization_accelerometer_calibration_covariance_sensitivity"
                ]
            ],
        }

    zeroed_gyro_covariance_mutation = execute_owner(0.0)
    outputs = [execute_owner(multiplier) for multiplier in multipliers]
    accelerometer_outputs = [
        execute_owner(1.0, accelerometer_multiplier=multiplier)
        for multiplier in accelerometer_multipliers
    ]
    zeroed_accelerometer_rejected = False
    zeroed_accelerometer_observed: str | None = None
    try:
        execute_owner(1.0, accelerometer_multiplier=0.0)
    except ValueError as exc:
        zeroed_accelerometer_observed = f"{type(exc).__name__}:{exc}"
        zeroed_accelerometer_rejected = (
            "accelerometer/gyro calibration nuisance settings are invalid"
            in str(exc)
        )
    primary_refit = next(
        row for row in outputs if row["covariance_multiplier"] == 1.0
    )
    fixed_rows = primary_refit["fixed_linearization_gyro_covariance_sensitivity"]
    if [row["gyro_covariance_multiplier"] for row in fixed_rows] != [
        0.0, 0.25, 1.0, 4.0,
    ]:
        raise RuntimeError("center fixed-linearization sensitivity bracket changed")
    accelerometer_primary_refit = next(
        row for row in accelerometer_outputs
        if row["accelerometer_calibration_covariance_multiplier"] == 1.0
    )
    fixed_accelerometer_rows = accelerometer_primary_refit[
        "fixed_linearization_accelerometer_calibration_covariance_sensitivity"
    ]
    if [
        row["accelerometer_calibration_covariance_multiplier"]
        for row in fixed_accelerometer_rows
    ] != [0.0, 0.25, 1.0, 4.0]:
        raise RuntimeError(
            "center fixed-point accelerometer calibration sensitivity bracket changed"
        )
    fixed_residual_sigmas = [row["residual_sigma_rms_mps2"] for row in fixed_rows]
    fixed_information_traces = [
        row["robust_bread_information_trace_m2_inv"] for row in fixed_rows
    ]
    fixed_systematic_traces = [
        row["systematic_covariance_trace_m2"] for row in fixed_rows
    ]
    fixed_total_traces = [
        row["total_model_covariance_trace_m2"] for row in fixed_rows
    ]
    thresholds = settings["synthetic"]["qualification_thresholds"]
    minimum_information_response_fraction = float(
        thresholds["center_gyro_minimum_information_response_fraction"]
    )
    minimum_zeroed_response_fraction = float(
        thresholds["center_gyro_minimum_zeroed_covariance_response_fraction"]
    )
    covariance_monotonic_tolerance = float(
        thresholds["center_gyro_covariance_monotonic_absolute_tolerance_m2"]
    )
    information_strictly_responds = bool(
        all(
            later < earlier
            for earlier, later in zip(
                fixed_information_traces[:-1], fixed_information_traces[1:]
            )
        )
        and fixed_information_traces[-1]
        <= fixed_information_traces[0]
        * (1.0 - minimum_information_response_fraction)
    )
    systematic_nonshrinking = bool(all(
        later + covariance_monotonic_tolerance >= earlier
        for earlier, later in zip(
            fixed_systematic_traces[:-1], fixed_systematic_traces[1:]
        )
    ))
    total_nonshrinking = bool(all(
        later + covariance_monotonic_tolerance >= earlier
        for earlier, later in zip(fixed_total_traces[:-1], fixed_total_traces[1:])
    ))
    zeroed_covariance_rejected_as_equivalent = bool(
        zeroed_gyro_covariance_mutation["residual_sigma_rms_mps2"]
        <= outputs[0]["residual_sigma_rms_mps2"]
        * (1.0 - minimum_zeroed_response_fraction)
        and zeroed_gyro_covariance_mutation["information_trace_m2_inv"]
        >= outputs[0]["information_trace_m2_inv"]
        * (1.0 + minimum_zeroed_response_fraction)
        and fixed_systematic_traces[0] + covariance_monotonic_tolerance
        <= fixed_systematic_traces[1]
        and fixed_total_traces[0] + covariance_monotonic_tolerance
        <= fixed_total_traces[1]
        and zeroed_gyro_covariance_mutation[
            "gyro_bias_drift_systematic_covariance_trace_m2"
        ] == 0.0
        and outputs[1][
            "gyro_bias_drift_systematic_covariance_trace_m2"
        ] > 0.0
    )
    fixed_accelerometer_residual_sigmas = [
        row["residual_sigma_rms_mps2"] for row in fixed_accelerometer_rows
    ]
    fixed_accelerometer_information_traces = [
        row["robust_bread_information_trace_m2_inv"]
        for row in fixed_accelerometer_rows
    ]
    fixed_accelerometer_systematic_traces = [
        row["systematic_covariance_trace_m2"]
        for row in fixed_accelerometer_rows
    ]
    fixed_accelerometer_total_traces = [
        row["total_model_covariance_trace_m2"]
        for row in fixed_accelerometer_rows
    ]
    all_fixed_rows = [*fixed_rows, *fixed_accelerometer_rows]
    variance_source_separation_pass = bool(
        all(
            row[
                "independent_filtered_observation_variance_mean_m2ps4"
            ] > 0.0
            and row["shared_calibration_marginal_variance_mean_m2ps4"] >= 0.0
            and row["white_observation_noise_sigma_multiplier"]
            == float(settings["joint_center"]["noise_sigma_multiplier"])
            and row[
                "white_observation_serial_correlation_variance_envelope_multiplier"
            ] > 1.0
            and row["shared_calibration_noise_sigma_multiplier"]
            == float(settings["joint_center"][
                "shared_calibration_white_noise_multiplier"
            ])
            == 1.0
            and row[
                "shared_calibration_serial_correlation_variance_envelope_multiplier"
            ]
            == float(settings["joint_center"][
                "shared_calibration_serial_correlation_variance_envelope_multiplier"
            ])
            == 1.0
            and row[
                "shared_calibration_marginal_used_only_for_robust_row_standardization"
            ]
            and not row[
                "shared_calibration_covariance_added_to_repeatable_episode_information"
            ]
            and not row[
                "shared_calibration_white_noise_filter_envelope_applied"
            ]
            and not row[
                "shared_calibration_white_noise_sigma_multiplier_applied"
            ]
            for row in all_fixed_rows
        )
    )
    accelerometer_fixed_point_pass = bool(
        all(
            later > earlier
            for earlier, later in zip(
                fixed_accelerometer_residual_sigmas[:-1],
                fixed_accelerometer_residual_sigmas[1:],
            )
        )
        and all(
            later < earlier
            for earlier, later in zip(
                fixed_accelerometer_information_traces[:-1],
                fixed_accelerometer_information_traces[1:],
            )
        )
        and all(
            later + covariance_monotonic_tolerance >= earlier
            for earlier, later in zip(
                fixed_accelerometer_systematic_traces[:-1],
                fixed_accelerometer_systematic_traces[1:],
            )
        )
        and all(
            later + covariance_monotonic_tolerance >= earlier
            for earlier, later in zip(
                fixed_accelerometer_total_traces[:-1],
                fixed_accelerometer_total_traces[1:],
            )
        )
    )
    return {
        "coverage_class": "EXECUTED_OWNER_LEVEL",
        "owner_call_path": [
            "functional_geometry.estimate_joint_center_pair_local",
            "functional_geometry._center_stochastic_residual_sigma",
        ],
        "injected_gyro_observation_and_bias_covariance_multipliers": multipliers,
        "zeroed_gyro_observation_and_bias_covariance_owner_mutation": {
            "injected": {
                "parent_gyro_observation_covariance": "EXACT_ZERO_3X3",
                "child_gyro_observation_covariance": "EXACT_ZERO_3X3",
                "parent_gyro_bias_covariance": "EXACT_ZERO_3X3",
                "child_gyro_bias_covariance": "EXACT_ZERO_3X3",
            },
            "expected": (
                "MUST_NOT_BE_EQUIVALENT_TO_THE_LOWEST_NONZERO_REGISTERED_"
                "GYRO_STOCHASTIC_COVARIANCE;SIGNED_GYRO_BIAS_DRIFT_ALPHA_"
                "NUISANCE_MUST_ENTER_THE_NONSHRINKING_LOW_RANK_SYSTEMATIC"
            ),
            "observed": zeroed_gyro_covariance_mutation,
            "pass": zeroed_covariance_rejected_as_equivalent,
        },
        "formal_monotonicity_audit": {
            "comparison_role": (
                "FIXED_REGISTERED_PRIMARY_FIT_POINT_AND_NUISANCE_GRADIENTS;"
                "NO_REFIT_BETWEEN_COVARIANCE_LEVELS"
            ),
            "information_strictly_responds": information_strictly_responds,
            "systematic_covariance_nonshrinking": systematic_nonshrinking,
            "total_covariance_nonshrinking": total_nonshrinking,
            "minimum_information_response_fraction": minimum_information_response_fraction,
            "minimum_zeroed_covariance_response_fraction": minimum_zeroed_response_fraction,
            "covariance_monotonic_absolute_tolerance_m2": covariance_monotonic_tolerance,
        },
        "fixed_linearization_outputs": fixed_rows,
        "shared_calibration_white_noise_envelope_contamination_mutation": {
            "coverage_class": "EXECUTED_OWNER_LEVEL",
            "owner_call_path": [
                "functional_geometry.estimate_joint_center_pair_local",
                "functional_geometry._center_stochastic_residual_sigma",
                "functional_geometry.fixed_linearization_covariance_sensitivity",
            ],
            "injected_forbidden_mutation": (
                "MULTIPLY_COHERENT_ACC_GYRO_BIAS_DRIFT_SCALE_CROSS_AXIS_J_C_JT_"
                "BY_WHITE_NOISE_SIGMA_SQUARED_AND_SAVGOL_SERIAL_ENVELOPE"
            ),
            "injected": {
                "forbidden_mutation": (
                    "MULTIPLY_COHERENT_ACC_GYRO_BIAS_DRIFT_SCALE_CROSS_AXIS_J_C_JT_"
                    "BY_WHITE_NOISE_SIGMA_SQUARED_AND_SAVGOL_SERIAL_ENVELOPE"
                ),
            },
            "expected": (
                "WHITE_FILTER_ENVELOPE_ONLY_ON_INDEPENDENT_OBSERVATION_AND_"
                "DERIVATIVE_NOISE;SHARED_CALIBRATION_MULTIPLIERS_EXACTLY_ONE;"
                "ROWWISE_MARGINAL_STANDARDIZATION_ONLY;LOW_RANK_SYSTEMATIC_"
                "PUSHFORWARD_NOT_REPEATABLE_INFORMATION"
            ),
            "fixed_point_outputs": all_fixed_rows,
            "observed": {
                "fixed_point_outputs": all_fixed_rows,
                "variance_source_separation_pass": variance_source_separation_pass,
            },
            "pass": variance_source_separation_pass,
        },
        "accelerometer_calibration_nuisance_mutation": {
            "coverage_class": "EXECUTED_OWNER_LEVEL",
            "owner_call_path": [
                "functional_geometry.estimate_joint_center_pair_local",
                "functional_geometry._center_stochastic_residual_sigma",
            ],
            "injected": {
                "accelerometer_calibration_covariance_multipliers": [
                    0.0, 0.25, 1.0, 4.0,
                ],
                "zeroed_owner_configuration": True,
            },
            "expected": (
                "ZERO_ACCELEROMETER_BIAS_DRIFT_AND_INDEPENDENT_SHARED_SCALE_"
                "CROSS_AXIS_CONFIGURATION_IS_REJECTED;AT_ONE_FIXED_OWNER_POINT_"
                "RESIDUAL_SIGMA_INCREASES_INFORMATION_DECREASES_AND_SHARED_TOTAL_"
                "COVARIANCE_DO_NOT_SHRINK;NO_GRAVITY_BIAS_FIT_OR_PER_ACTION_PROFILE"
            ),
            "zeroed_owner_configuration_rejected": zeroed_accelerometer_rejected,
            "zeroed_owner_observed": zeroed_accelerometer_observed,
            "fixed_primary_parameter_and_jacobian_outputs": (
                fixed_accelerometer_rows
            ),
            "fixed_point_residual_sigma_strictly_increases": bool(all(
                later > earlier
                for earlier, later in zip(
                    fixed_accelerometer_residual_sigmas[:-1],
                    fixed_accelerometer_residual_sigmas[1:],
                )
            )),
            "fixed_point_information_strictly_decreases": bool(all(
                later < earlier
                for earlier, later in zip(
                    fixed_accelerometer_information_traces[:-1],
                    fixed_accelerometer_information_traces[1:],
                )
            )),
            "fixed_point_systematic_covariance_nonshrinking": bool(all(
                later + covariance_monotonic_tolerance >= earlier
                for earlier, later in zip(
                    fixed_accelerometer_systematic_traces[:-1],
                    fixed_accelerometer_systematic_traces[1:],
                )
            )),
            "fixed_point_total_covariance_nonshrinking": bool(all(
                later + covariance_monotonic_tolerance >= earlier
                for earlier, later in zip(
                    fixed_accelerometer_total_traces[:-1],
                    fixed_accelerometer_total_traces[1:],
                )
            )),
            "initial_still_gravity_or_bias_fitted": False,
            "per_action_profile_used": False,
            "end_to_end_refit_outputs": accelerometer_outputs,
            "end_to_end_refit_covariance_monotonicity_required": False,
            "observed": {
                "zeroed_owner_configuration_rejected": (
                    zeroed_accelerometer_rejected
                ),
                "zeroed_owner_observed": zeroed_accelerometer_observed,
                "fixed_primary_parameter_and_jacobian_outputs": (
                    fixed_accelerometer_rows
                ),
                "fixed_point_residual_sigma_strictly_increases": bool(all(
                    later > earlier
                    for earlier, later in zip(
                        fixed_accelerometer_residual_sigmas[:-1],
                        fixed_accelerometer_residual_sigmas[1:],
                    )
                )),
                "fixed_point_information_strictly_decreases": bool(all(
                    later < earlier
                    for earlier, later in zip(
                        fixed_accelerometer_information_traces[:-1],
                        fixed_accelerometer_information_traces[1:],
                    )
                )),
                "fixed_point_systematic_covariance_nonshrinking": bool(all(
                    later + covariance_monotonic_tolerance >= earlier
                    for earlier, later in zip(
                        fixed_accelerometer_systematic_traces[:-1],
                        fixed_accelerometer_systematic_traces[1:],
                    )
                )),
                "fixed_point_total_covariance_nonshrinking": bool(all(
                    later + covariance_monotonic_tolerance >= earlier
                    for earlier, later in zip(
                        fixed_accelerometer_total_traces[:-1],
                        fixed_accelerometer_total_traces[1:],
                    )
                )),
                "initial_still_gravity_or_bias_fitted": False,
                "per_action_profile_used": False,
            },
            "pass": bool(
                zeroed_accelerometer_rejected
                and accelerometer_fixed_point_pass
                and not any(
                    row["shared_nuisance_shrinks_as_episode_information"]
                    for row in accelerometer_outputs
                )
                and variance_source_separation_pass
            ),
        },
        "end_to_end_refit_sensitivity": {
            "outputs": outputs,
            "fit_point_and_basin_may_change": True,
            "posterior_covariance_monotonicity_required": False,
            "role": "SEPARATE_POINT_AND_BASIN_SENSITIVITY_DIAGNOSTIC_ONLY",
        },
        "outputs": outputs,
        "ordinary_low_information_or_wide_uncertainty_may_return_no_update": True,
        "pass": bool(
            all(
                later > earlier
                for earlier, later in zip(
                    fixed_residual_sigmas[:-1], fixed_residual_sigmas[1:]
                )
            )
            and information_strictly_responds
            and systematic_nonshrinking
            and total_nonshrinking
            and zeroed_covariance_rejected_as_equivalent
            and zeroed_accelerometer_rejected
            and accelerometer_fixed_point_pass
            and variance_source_separation_pass
            and all(row["savgol_alpha_observation_noise_gain_s2_inv"] > 0.0 for row in outputs)
            and all(row["cluster_robust_complete_block_count"] == 29 for row in outputs)
            and not any(row["shared_nuisance_shrinks_as_episode_information"] for row in outputs)
        ),
    }


def _numeric_axis_calibration_nuisance_gate(
    *,
    settings: Mapping[str, Any],
    pair: AlignedPair,
    primary_axis: AxisEstimate,
    acc_covariance: np.ndarray,
    gyro_covariance: np.ndarray,
    gyro_bias_covariance: np.ndarray,
) -> Mapping[str, Any]:
    """Exercise omission and half/primary/double axis nuisance ownership."""

    primary_settings = dict(settings["hinge_axis"])
    multipliers = [
        float(value) for value in primary_settings[
            "calibration_nuisance_covariance_sensitivity_multipliers"
        ]
    ]
    if multipliers != [0.25, 1.0, 4.0]:
        raise RuntimeError("axis calibration nuisance sensitivity bracket changed")

    def run(source_settings: Mapping[str, Any]) -> AxisEstimate:
        return estimate_hinge_axis_qmt(
            "knee_left", [pair], settings=source_settings,
            parent_acc_covariance=acc_covariance,
            child_acc_covariance=acc_covariance,
            parent_gyro_covariance=gyro_covariance,
            child_gyro_covariance=gyro_covariance,
            parent_gyro_bias_covariance=gyro_bias_covariance,
            child_gyro_bias_covariance=gyro_bias_covariance,
            execution_guard=_new_guard(settings),
        )

    sensitivity: list[dict[str, Any]] = []
    sensitivity_axes: dict[float, AxisEstimate] = {1.0: primary_axis}
    calibration_names = (
        "accelerometer_bias_drift",
        "accelerometer_scale_cross_axis",
        "accelerometer_gyro_shared_scale_cross_axis",
        "gyro_bias",
        "gyro_bias_drift",
        "gyro_scale_cross_axis",
    )

    def components(axis: AxisEstimate) -> dict[str, np.ndarray]:
        return {
            name: np.asarray(value, dtype=float)
            for name, value in axis.report[
                "systematic_component_tangent_covariances_rad2"
            ].items()
        }

    for multiplier in multipliers:
        if multiplier not in sensitivity_axes:
            candidate_settings = dict(primary_settings)
            candidate_settings["calibration_nuisance_covariance_multiplier"] = multiplier
            sensitivity_axes[multiplier] = run(candidate_settings)
        axis = sensitivity_axes[multiplier]
        component_rows = components(axis)
        sensitivity.append({
            "covariance_multiplier": multiplier,
            "calibration_systematic_trace_rad2": float(sum(
                np.trace(component_rows[name]) for name in calibration_names
            )),
            "total_systematic_trace_rad2": float(np.trace(np.asarray(
                axis.report["total_systematic_tangent_covariance_rad2"], dtype=float,
            ))),
            "owner_update_eligible": bool(axis.report["owner_update_eligible"]),
            "component_traces_rad2": {
                name: float(np.trace(component))
                for name, component in sorted(component_rows.items())
            },
        })

    omitted_acc_settings = dict(primary_settings)
    omitted_acc_settings["accelerometer_calibration_nuisance_multiplier"] = 0.0
    omitted_acc = run(omitted_acc_settings)
    omitted_gyro_settings = dict(primary_settings)
    omitted_gyro_settings["gyro_calibration_nuisance_multiplier"] = 0.0
    omitted_gyro = run(omitted_gyro_settings)
    primary_components = components(primary_axis)
    omitted_acc_components = components(omitted_acc)
    omitted_gyro_components = components(omitted_gyro)
    acc_specific = ("accelerometer_bias_drift", "accelerometer_scale_cross_axis")
    gyro_specific = ("gyro_bias", "gyro_bias_drift", "gyro_scale_cross_axis")
    primary_audits = primary_axis.report["axis_calibration_nuisance_audit"]
    expected_audits = {
        "observation_statistical",
        "accelerometer_bias_drift",
        "accelerometer_scale_cross_axis",
        "accelerometer_gyro_shared_scale_cross_axis",
        "gyro_bias",
        "gyro_bias_drift",
        "gyro_scale_cross_axis",
        "persistent_pair_clock",
    }
    linearization_rows = {
        name: audit["official_refit_linearization_validation"]
        for name, audit in primary_audits.items()
    }
    exact_score_hessian_gate = primary_axis.report["exact_score_hessian_audit"]
    primary_scale_validated = bool(
        exact_score_hessian_gate["pass"]
        and exact_score_hessian_gate[
            "every_registered_step_finite_full_rank_positive_curvature"
        ]
        and set(linearization_rows) == expected_audits
        and all(
            validation["all_rows_pass"]
            and any(
                row.get("nuisance_scale") == 1.0
                and row.get("required_for_owner_update")
                and row.get("pass")
                and row.get("informative_response")
                for row in validation["rows"]
            )
            for validation in linearization_rows.values()
        )
    )
    sensitivity_traces = [
        row["calibration_systematic_trace_rad2"] for row in sensitivity
    ]
    sensitivity_responds = bool(
        sensitivity_traces[0] < sensitivity_traces[1] < sensitivity_traces[2]
    )
    acc_omission_detected = bool(
        all(np.trace(primary_components[name]) > 0.0 for name in acc_specific)
        and all(np.trace(omitted_acc_components[name]) <= 1e-16 for name in acc_specific)
        and not omitted_acc.report["owner_update_eligible"]
    )
    gyro_omission_detected = bool(
        all(np.trace(primary_components[name]) > 0.0 for name in gyro_specific)
        and all(np.trace(omitted_gyro_components[name]) <= 1e-16 for name in gyro_specific)
        and not omitted_gyro.report["owner_update_eligible"]
    )
    common_mode_not_rows = bool(
        primary_axis.report["axis_calibration_nuisance_counted_as_independent_rows"] is False
        and all(
            audit["common_nuisance_draw_reused_coherently_across_rows"]
            and not audit["nuisance_rows_counted_as_independent_votes"]
            for audit in primary_audits.values()
        )
    )
    return {
        "schema": "biospur-c2-axis-calibration-nuisance-owner-gate-v1",
        "coverage_class": "EXECUTED_OWNER_LEVEL",
        "owner_call_path": [
            "functional_geometry.estimate_hinge_axis_qmt",
            "qmt.jointAxisEstHingeOlsson",
            "functional_geometry._qmt_official_fixed_point_gradient",
            "functional_geometry._axis_antithetic_implicit_covariance",
        ],
        "registered_sensitivity": sensitivity,
        "accelerometer_omission": {
            "injected_multiplier": 0.0,
            "component_traces_rad2": {
                name: float(np.trace(value))
                for name, value in omitted_acc_components.items()
            },
            "owner_update_eligible": bool(omitted_acc.report["owner_update_eligible"]),
            "pass": acc_omission_detected,
        },
        "gyro_omission": {
            "injected_multiplier": 0.0,
            "component_traces_rad2": {
                name: float(np.trace(value))
                for name, value in omitted_gyro_components.items()
            },
            "owner_update_eligible": bool(omitted_gyro.report["owner_update_eligible"]),
            "pass": gyro_omission_detected,
        },
        "common_mode_nuisance_not_independent_rows": common_mode_not_rows,
        "official_refit_linearization_by_component": linearization_rows,
        "exact_score_hessian_rank_curvature_and_step_gate": (
            exact_score_hessian_gate
        ),
        "primary_scale_official_refit_agreement": primary_scale_validated,
        "calibration_trace_strictly_responds_half_primary_double": sensitivity_responds,
        "failed_or_branch_switched_refits_retained_and_force_no_update": True,
        "pass": bool(
            acc_omission_detected
            and gyro_omission_detected
            and common_mode_not_rows
            and primary_scale_validated
            and sensitivity_responds
            and primary_axis.report["owner_update_eligible"]
        ),
    }


def _numeric_axis_exact_hessian_rejection_gate(
    *,
    settings: Mapping[str, Any],
    pair: AlignedPair,
    acc_covariance: np.ndarray,
    gyro_covariance: np.ndarray,
    gyro_bias_covariance: np.ndarray,
) -> Mapping[str, Any]:
    """Inject an indefinite exact-score chart through the real axis owner."""

    injected_hessian = np.diag([-1.0, 1.0, 2.0, 3.0])

    def indefinite_quadratic_cost(
        value: np.ndarray,
    ) -> tuple[float, np.ndarray, np.ndarray, np.ndarray, Mapping[str, Any]]:
        vector = np.asarray(value, dtype=float).reshape(4)
        return (
            float(0.5 * vector @ injected_hessian @ vector),
            (injected_hessian @ vector).reshape(4, 1),
            np.zeros(1, dtype=float),
            np.zeros((1, 4), dtype=float),
            {},
        )

    with patch.object(
        functional_geometry_owner,
        "_qmt_official_cost_closure",
        return_value=indefinite_quadratic_cost,
    ):
        detected_hessian, invalid_audit = (
            functional_geometry_owner._qmt_exact_score_hessian_audit(
                pair.parent_acc,
                pair.child_acc,
                pair.parent_gyro,
                pair.child_gyro,
                settings=settings["hinge_axis"],
                xhat=np.zeros(4, dtype=float),
            )
        )
    detector_caught_indefinite_chart = bool(
        np.allclose(detected_hessian, injected_hessian, atol=1e-10, rtol=1e-10)
        and not invalid_audit["pass"]
        and all(
            not row["positive_local_curvature"]
            and row["rank"] == 3
            and not row["pass"]
            for row in invalid_audit["step_results"]
        )
    )
    with patch.object(
        functional_geometry_owner,
        "_qmt_exact_score_hessian_audit",
        return_value=(detected_hessian, invalid_audit),
    ):
        mutated = estimate_hinge_axis_qmt(
            "knee_left", [pair], settings=settings["hinge_axis"],
            parent_acc_covariance=acc_covariance,
            child_acc_covariance=acc_covariance,
            parent_gyro_covariance=gyro_covariance,
            child_gyro_covariance=gyro_covariance,
            parent_gyro_bias_covariance=gyro_bias_covariance,
            child_gyro_bias_covariance=gyro_bias_covariance,
            execution_guard=_new_guard(settings),
        )
    audits = mutated.report["axis_calibration_nuisance_audit"]
    all_non_hessian_support_gates_satisfied = bool(
        mutated.report["block_selection_status"]
        == "NOISE_STANDARDIZED_THRESHOLD_MET"
        and mutated.report["effective_support_rows"]
        >= mutated.report["minimum_effective_support_rows"]
        and mutated.report["input_rows_after_selection_before_cap"]
        >= mutated.report["minimum_selected_observed_rows"]
        and mutated.report["canonical_jtj_informed_rank"] == 4
        and not mutated.report["initial_still_present"]
    )
    no_invalid_inverse = bool(all(
        row["successful_replicates"] == 0
        and not row["official_refit_linearization_validation"][
            "exact_score_hessian_eligible_for_inverse"
        ]
        and not row["official_refit_linearization_validation"][
            "singular_or_indefinite_exact_hessian_pseudoinverse_used"
        ]
        for row in audits.values()
    ))
    return {
        "schema": "biospur-c2-axis-exact-score-hessian-rejection-gate-v1",
        "coverage_class": "EXECUTED_OWNER_LEVEL",
        "owner_call_path": [
            "functional_geometry.estimate_hinge_axis_qmt",
            "functional_geometry._qmt_exact_score_hessian_audit",
            "functional_geometry._axis_antithetic_implicit_covariance",
        ],
        "injected_exact_score_hessian_eigenvalues": [-1.0, 1.0, 2.0, 3.0],
        "exact_score_hessian_detector_caught_indefinite_chart": (
            detector_caught_indefinite_chart
        ),
        "canonical_jtj_rank_remained_diagnostic": int(
            mutated.report["canonical_jtj_informed_rank"]
        ),
        "all_non_hessian_support_gates_satisfied": (
            all_non_hessian_support_gates_satisfied
        ),
        "owner_update_eligible": bool(mutated.report["owner_update_eligible"]),
        "owner_update_mode": mutated.report["owner_update_mode"],
        "invalid_exact_hessian_inverse_or_pseudoinverse_used": not no_invalid_inverse,
        "ordinary_local_no_update_returned": bool(
            mutated.report["owner_update_mode"].startswith("LOCAL_NO_UPDATE_")
        ),
        "pass": bool(
            not mutated.report["exact_score_hessian_audit"]["pass"]
            and detector_caught_indefinite_chart
            and not mutated.report["owner_update_eligible"]
            and mutated.report["owner_update_mode"].startswith("LOCAL_NO_UPDATE_")
            and all_non_hessian_support_gates_satisfied
            and no_invalid_inverse
        ),
    }


def _decoded_rows(
    count: int,
    *,
    start_us: int,
    all_clipped: bool = False,
    all_duplicate: bool = False,
    boot_transition: bool = False,
) -> np.ndarray:
    rows = np.zeros(int(count), dtype=IMU_DTYPE)
    if count == 0:
        return rows
    rows["node_timer_us"] = start_us + np.arange(count, dtype=np.uint64) * 5000
    rows["imu_sample_sequence"] = np.arange(count, dtype=np.uint16)
    rows["acc_raw"][:, 2] = 2048
    rows["gyro_raw"][:, 1] = np.rint(20.0 * np.sin(np.arange(count) * 0.07)).astype(np.int16)
    if all_clipped:
        rows["acc_raw"][:, 0] = 32767
        rows["gyro_raw"][:, 2] = -32768
    if all_duplicate:
        rows["node_timer_us"] = start_us
    if boot_transition and count > 1:
        midpoint = count // 2
        rows["derived_boot_epoch"][midpoint:] = 1
        rows["node_timer_us"][midpoint:] = start_us + np.arange(count - midpoint, dtype=np.uint64) * 5000
    return rows


def _orientation_continuation_cases(settings: Mapping[str, Any]) -> dict[str, Any]:
    """Prove unusable local episodes do not reset or terminate orientation."""

    actions = settings["execution_contract"]["chronological_actions"]
    initial = {
        "nodes": {
            "node": {
                "gyro_bias_rad_s": [0.001, -0.002, 0.0005],
                "gyro_bias_covariance_rad2_s2": (np.eye(3) * 1e-6).tolist(),
                "gyro_observation_covariance_rad2_s2": (np.eye(3) * 4e-6).tolist(),
            },
        },
    }
    specifications = {
        "ZERO_ROWS": _decoded_rows(0, start_us=1_000_000),
        "ONE_ROW": _decoded_rows(1, start_us=1_000_000),
        "ALL_CLIPPED": _decoded_rows(80, start_us=1_000_000, all_clipped=True),
        "ALL_DUPLICATE": _decoded_rows(80, start_us=1_000_000, all_duplicate=True),
        "BOOT_TRANSITION_UNUSABLE": _decoded_rows(
            80, start_us=1_000_000, all_clipped=True, boot_transition=True,
        ),
    }
    output: dict[str, Any] = {}
    for name, first_rows in specifications.items():
        guard = C2ExecutionGuard(settings)
        state = ContinuousVQFState(
            initial, execution_guard=guard, sample_period_s=0.005,
            unknown_boot_orientation_sigma_rad=float(
                settings["orientation"]["unknown_boot_orientation_sigma_rad"]
            ),
            unknown_unusable_episode_orientation_sigma_rad=float(
                settings["orientation"]["unknown_unusable_episode_orientation_sigma_rad"]
            ),
        )
        first = state.process(DecodedAction(
            action=actions[0], chronological_index=0, interval=(0, 0),
            rows_by_node={"node": first_rows}, access_audit={}, decode_audit={},
        ))
        second_rows = _decoded_rows(240, start_us=4_000_000)
        second = state.process(DecodedAction(
            action=actions[1], chronological_index=1, interval=(0, 0),
            rows_by_node={"node": second_rows}, access_audit={}, decode_audit={},
        ))
        first_audit = first.audit["nodes"]["node"]
        second_audit = second.audit["nodes"]["node"]
        output[name] = {
            "coverage_class": "EXECUTED_OWNER_LEVEL",
            "owner_call_path": [
                "orientation.ContinuousVQFState.process",
                "orientation.assess_factor_rows",
                "architecture_guard.C2ExecutionGuard.begin_episode",
            ],
            "injected": {
                "first_episode_rows": int(len(first_rows)),
                "all_clipped": name in {"ALL_CLIPPED", "BOOT_TRANSITION_UNUSABLE"},
                "all_duplicate": name == "ALL_DUPLICATE",
                "boot_transition": name == "BOOT_TRANSITION_UNUSABLE",
            },
            "expected": "FIRST_EPISODE_LOCAL_NO_UPDATE_WITHOUT_RESET_OR_TERMINATION;SECOND_EPISODE_CONTINUES",
            "observed": {
                "first_status": first_audit["status"],
                "first_output_rows": int(len(first.gyro_rads_by_node["node"])),
                "first_capture_terminated": bool(first_audit["capture_terminated"]),
                "first_vqf_reset": bool(first_audit["vqf_reset"]),
                "first_unknown_duration_invented": bool(first_audit["unknown_boot_duration_invented"]),
                "first_boot_floor_applied": bool(first_audit["boot_transition_conservative_floor_applied"]),
                "first_unknown_unusable_floor_applied": bool(first_audit["unknown_unusable_episode_floor_applied"]),
                "first_uncertainty_increment_trace_rad2": float(first_audit["uncertainty_increment_trace_rad2"]),
                "second_output_rows": int(len(second.gyro_rads_by_node["node"])),
                "second_one_capture_wide_vqf": bool(second_audit["one_capture_wide_vqf_instance"]),
            },
            "pass": bool(
                first_audit["status"] == "LOCAL_NO_UPDATE_INSUFFICIENT_USABLE_ROWS_CONTINUE_CAPTURE"
                and len(first.gyro_rads_by_node["node"]) == 0
                and not first_audit["capture_terminated"]
                and not first_audit["vqf_reset"]
                and not first_audit["unknown_boot_duration_invented"]
                and first_audit["uncertainty_increment_trace_rad2"] > 0.0
                and len(second.gyro_rads_by_node["node"]) == len(second_rows)
                and second_audit["one_capture_wide_vqf_instance"]
                and (name != "BOOT_TRANSITION_UNUSABLE" or first_audit["boot_transition_conservative_floor_applied"])
            ),
        }
    guard = C2ExecutionGuard(settings)
    state = ContinuousVQFState(
        initial, execution_guard=guard, sample_period_s=0.005,
        unknown_boot_orientation_sigma_rad=float(settings["orientation"]["unknown_boot_orientation_sigma_rad"]),
        unknown_unusable_episode_orientation_sigma_rad=float(
            settings["orientation"]["unknown_unusable_episode_orientation_sigma_rad"]
        ),
    )
    first_rows = _decoded_rows(240, start_us=1_000_000)
    second_rows = _decoded_rows(240, start_us=4_000_000)
    state.process(DecodedAction(
        action=actions[0], chronological_index=0, interval=(0, 0),
        rows_by_node={"node": first_rows}, access_audit={}, decode_audit={},
    ))
    second = state.process(DecodedAction(
        action=actions[1], chronological_index=1, interval=(0, 0),
        rows_by_node={"node": second_rows}, access_audit={}, decode_audit={},
    ))
    state_audit = state.audit()
    gap_events = [
        row for row in state_audit["events"]
        if row.get("cause") == "SEALED_INTER_ACTION_UNOBSERVED_INTERVAL"
    ]
    output["VALID_INTER_EPISODE_GAP"] = {
        "coverage_class": "EXECUTED_OWNER_LEVEL",
        "owner_call_path": ["orientation.ContinuousVQFState.process", "orientation.ContinuousVQFState._grow_gap"],
        "injected": {"first_end_us": int(first_rows["node_timer_us"][-1]), "second_start_us": int(second_rows["node_timer_us"][0])},
        "expected": "ONE_PERSISTENT_VQF;NO_UPDATE_COVARIANCE_GROWTH;NO_RESET_OR_FABRICATED_SAMPLES",
        "observed": {"events": gap_events, "state_audit": state_audit, "second_rows": len(second.gyro_rads_by_node["node"])},
        "pass": bool(
            len(gap_events) == 1
            and gap_events[0]["gap_s"] > 0.0
            and not gap_events[0]["vqf_reset"]
            and gap_events[0]["samples_fabricated"] == 0
            and state_audit["vqf_instances_per_node"] == 1
            and state_audit["episode_reset_count"] == 0
        ),
    }
    guard = C2ExecutionGuard(settings)
    state = ContinuousVQFState(
        initial, execution_guard=guard, sample_period_s=0.005,
        unknown_boot_orientation_sigma_rad=float(settings["orientation"]["unknown_boot_orientation_sigma_rad"]),
        unknown_unusable_episode_orientation_sigma_rad=float(
            settings["orientation"]["unknown_unusable_episode_orientation_sigma_rad"]
        ),
    )
    boot_rows = _decoded_rows(240, start_us=1_000_000, boot_transition=True)
    boot_action = state.process(DecodedAction(
        action=actions[0], chronological_index=0, interval=(0, 0),
        rows_by_node={"node": boot_rows}, access_audit={}, decode_audit={},
    ))
    boot_events = [
        row for row in state.audit()["events"]
        if row.get("event") == "WITHIN_EPISODE_DERIVED_BOOT_TRANSITION_UNKNOWN_DURATION"
    ]
    output["USABLE_BOOT_TRANSITION"] = {
        "coverage_class": "EXECUTED_OWNER_LEVEL",
        "owner_call_path": ["orientation.ContinuousVQFState.process", "orientation.assess_factor_rows"],
        "injected": {"usable_rows": len(boot_rows), "derived_boot_transitions": 1},
        "expected": "PRESERVE_USABLE_SPANS_AND_APPLY_BOOT_UNCERTAINTY_FLOOR_WITHOUT_DURATION_INVENTION",
        "observed": {
            "output_rows": len(boot_action.gyro_rads_by_node["node"]),
            "node_audit": boot_action.audit["nodes"]["node"],
            "events": boot_events,
        },
        "pass": bool(
            len(boot_action.gyro_rads_by_node["node"]) == len(boot_rows)
            and len(boot_events) == 1
            and not boot_events[0]["duration_invented"]
            and float(np.trace(np.asarray(boot_events[0]["increment_rad2"]))) > 0.0
            and not boot_events[0]["vqf_reset"]
        ),
    }
    return output


def _slice_pair(pair: AlignedPair, start: int, stop: int, *, action: str) -> AlignedPair:
    length = int(stop - start)
    return AlignedPair(
        edge=pair.edge, action=action,
        parent_acc=pair.parent_acc[start:stop], child_acc=pair.child_acc[start:stop],
        parent_gyro=pair.parent_gyro[start:stop], child_gyro=pair.child_gyro[start:stop],
        parent_observed_time_s=pair.parent_observed_time_s[start:stop],
        child_observed_time_s=pair.child_observed_time_s[start:stop],
        parent_boot_epoch=pair.parent_boot_epoch[start:stop],
        child_boot_epoch=pair.child_boot_epoch[start:stop],
        alignment=_identity_alignment(length, float(pair.alignment.report["sample_period_s"])),
        contiguous_spans=(slice(0, length),),
        provenance=_synthetic_pair_provenance(
            action=action,
            seed=pair.provenance.get("seed_or_fixture_id", "SYNTHETIC_SLICE"),
            fixture="RESULT_INDEPENDENT_CONTIGUOUS_SLICE",
            extra={"source_action": pair.action, "source_half_open_rows": [start, stop]},
        ),
    )


def _complete_block_prefix_pair(
    pair: AlignedPair,
    *,
    block_rows: int,
    block_count: int,
) -> AlignedPair:
    """Take a preregistered chronological prefix of complete gap-safe blocks."""

    blocks: list[np.ndarray] = []
    for span in pair.contiguous_spans:
        for start in range(int(span.start), int(span.stop), int(block_rows)):
            stop = min(int(span.stop), start + int(block_rows))
            if stop - start == int(block_rows):
                blocks.append(np.arange(start, stop, dtype=int))
    if len(blocks) < int(block_count):
        raise ValueError(
            f"support sensitivity requested {block_count} complete blocks but only "
            f"{len(blocks)} are available"
        )
    selected_blocks = blocks[:int(block_count)]
    selected = np.concatenate(selected_blocks)
    packed_spans = tuple(
        slice(index * int(block_rows), (index + 1) * int(block_rows))
        for index in range(int(block_count))
    )
    report = dict(pair.alignment.report)
    report.update({
        "support_information_subset": "CHRONOLOGICAL_PREFIX_OF_COMPLETE_GAP_SAFE_BLOCKS",
        "support_information_block_rows": int(block_rows),
        "support_information_block_count": int(block_count),
        "support_information_source_block_half_open": [
            [int(block[0]), int(block[-1]) + 1] for block in selected_blocks
        ],
        "rows_crossing_gap_or_boot_transition": 0,
    })
    return AlignedPair(
        edge=pair.edge,
        action=f"{pair.action}_SUPPORT_PREFIX_{block_count}_BLOCKS",
        parent_acc=pair.parent_acc[selected],
        child_acc=pair.child_acc[selected],
        parent_gyro=pair.parent_gyro[selected],
        child_gyro=pair.child_gyro[selected],
        parent_observed_time_s=pair.parent_observed_time_s[selected],
        child_observed_time_s=pair.child_observed_time_s[selected],
        parent_boot_epoch=pair.parent_boot_epoch[selected],
        child_boot_epoch=pair.child_boot_epoch[selected],
        alignment=PairAlignment(
            parent_indices=np.asarray(pair.alignment.parent_indices)[selected],
            child_indices=np.asarray(pair.alignment.child_indices)[selected],
            lag_samples=int(pair.alignment.lag_samples),
            report=report,
        ),
        contiguous_spans=packed_spans,
        provenance=_synthetic_pair_provenance(
            action=f"{pair.action}_SUPPORT_PREFIX_{block_count}_BLOCKS",
            seed=pair.provenance.get("seed_or_fixture_id", "SYNTHETIC_SUPPORT_PREFIX"),
            fixture="REGISTERED_COMPLETE_GAP_SAFE_BLOCK_PREFIX_SUPPORT_SENSITIVITY",
            extra={
                "source_action": pair.action,
                "block_rows": int(block_rows),
                "block_count": int(block_count),
                "source_block_half_open": [
                    [int(block[0]), int(block[-1]) + 1] for block in selected_blocks
                ],
                "rows_crossing_gap_or_boot_transition": 0,
            },
        ),
    )


def _product_s2_tangent_error(
    axis: AxisEstimate,
    parent_truth: np.ndarray,
    child_truth: np.ndarray,
) -> np.ndarray:
    """Map sign-invariant truth axes into the estimate's reported 4D chart."""

    output = []
    for estimate, truth, basis_key in (
        (axis.parent_axis_sensor, parent_truth, "parent_tangent_basis_sensor"),
        (axis.child_axis_sensor, child_truth, "child_tangent_basis_sensor"),
    ):
        base = np.asarray(estimate, dtype=float)
        target = np.asarray(truth, dtype=float)
        if float(base @ target) < 0.0:
            target = -target
        dot = float(np.clip(base @ target, -1.0, 1.0))
        angle = float(np.arccos(dot))
        transverse = target - dot * base
        transverse_norm = float(np.linalg.norm(transverse))
        if transverse_norm <= 1e-12:
            local = np.zeros(2, dtype=float) if angle <= 1e-12 else np.full(2, np.inf)
        else:
            tangent = transverse * (angle / transverse_norm)
            local = np.asarray(axis.report[basis_key], dtype=float).T @ tangent
        output.extend(local.tolist())
    return np.asarray(output, dtype=float)


def _numeric_center_coherent_nuisance_refit_gate(
    *,
    settings: Mapping[str, Any],
    pair: AlignedPair,
    primary_center: CenterEstimate,
    acc_covariance: np.ndarray,
    gyro_observation_covariance: np.ndarray,
    gyro_bias_covariance: np.ndarray,
) -> dict[str, Any]:
    """Exercise five coherent-refit mutations on the real center owner."""

    audit = primary_center.report["coherent_nuisance_refit_audit"]
    components = {str(row["component"]): row for row in audit["components"]}
    registered = settings["joint_center"]["coherent_nuisance_refit_audit"]
    registered_components = tuple(str(value) for value in registered["components"])
    if tuple(components) != registered_components:
        raise RuntimeError("center coherent-refit component closure changed")
    direction_count = int(registered["direction_count"])
    required_radius = float(registered["required_whitened_radius"])
    displacement_limit = float(
        registered["maximum_full_scale_candidate_displacement_m"]
    )
    midpoint_limit = float(
        registered["maximum_antithetic_ensemble_midpoint_shift_m"]
    )

    unit_span_rows = []
    midpoint_rows = []
    for component, component_row in components.items():
        directions = np.asarray([
            row["signed_refits"][0]["direction"]
            for row in component_row["direction_rows"]
        ], dtype=float)
        dimension = int(component_row["whitened_dimension"])
        radii = np.linalg.norm(directions, axis=1)
        span_rank = int(np.linalg.matrix_rank(directions[:dimension]))
        unit_span_rows.append({
            "component": component,
            "direction_count": len(directions),
            "whitened_dimension": dimension,
            "whitened_radii": radii.tolist(),
            "first_dimension_direction_rank": span_rank,
            "full_span_before_cycle": bool(
                len(directions) >= dimension and span_rank == dimension
            ),
            "unit_radius_exact": bool(np.allclose(
                radii, required_radius, atol=1e-12, rtol=0.0,
            )),
        })
        for direction_row in component_row["direction_rows"]:
            midpoint = direction_row["antithetic_midpoint_shift_m"]
            displacement = direction_row[
                "maximum_full_scale_candidate_displacement_m"
            ]
            eligible = bool(
                midpoint is not None
                and displacement is not None
                and float(midpoint) <= midpoint_limit
                and float(displacement) <= displacement_limit
                and all(
                    signed["solver_success"]
                    and not signed["boundary_guard_active"]
                    and not signed["branch_or_basin_switched"]
                    and signed["exception_type"] is None
                    for signed in direction_row["signed_refits"]
                )
            )
            midpoint_rows.append({
                "component": component,
                "direction_index": int(direction_row["direction_index"]),
                "observed_midpoint_shift_m": midpoint,
                "observed_maximum_displacement_m": displacement,
                "midpoint_limit_m": midpoint_limit,
                "displacement_limit_m": displacement_limit,
                "expected_direction_pass": eligible,
                "observed_direction_pass": bool(direction_row["pass"]),
                "exact_gate_agreement": bool(
                    eligible == bool(direction_row["pass"])
                ),
            })

    clock = components["PERSISTENT_PAIR_CLOCK"]
    clock_signed_rows = [
        signed
        for direction_row in clock["direction_rows"]
        for signed in direction_row["signed_refits"]
    ]
    retained_hashes = {
        str(row["retained_interior_indices_sha256"])
        for row in clock_signed_rows
    }
    retained_counts = {
        int(row["retained_interior_rows"]) for row in clock_signed_rows
    }
    guarded_counts = {
        int(row["guarded_endpoint_rows"]) for row in clock_signed_rows
    }
    clock_support_pass = bool(
        len(clock_signed_rows) == direction_count * 2
        and len(retained_hashes) == 1
        and len(retained_counts) == 1
        and len(guarded_counts) == 1
        and all(
            row["symmetric_interior_support_for_both_antithetic_signs"]
            and int(row["endpoint_clamped_or_repeated_rows"]) == 0
            and int(row["cross_block_or_gap_interpolation_count"]) == 0
            and row["gap_or_block_boundary_crossed"] is False
            for row in clock_signed_rows
        )
    )

    original_interp = np.interp

    def injected_clock_support_failure(*args: Any, **kwargs: Any) -> np.ndarray:
        del args, kwargs
        raise RuntimeError("INJECTED_ORDINARY_CLOCK_INTERPOLATION_SUPPORT_FAILURE")

    try:
        np.interp = injected_clock_support_failure  # type: ignore[assignment]
        failed_center = estimate_joint_center_pair_local(
            "knee_left", "thigh_left", "shank_left", [pair],
            settings=settings["joint_center"],
            parent_acc_covariance=acc_covariance,
            child_acc_covariance=acc_covariance,
            parent_gyro_observation_covariance=gyro_observation_covariance,
            child_gyro_observation_covariance=gyro_observation_covariance,
            parent_gyro_bias_covariance=gyro_bias_covariance,
            child_gyro_bias_covariance=gyro_bias_covariance,
            execution_guard=_new_guard(settings),
        )
    finally:
        np.interp = original_interp  # type: ignore[assignment]
    failed_audit = failed_center.report["coherent_nuisance_refit_audit"]
    failed_components = {
        str(row["component"]): row for row in failed_audit["components"]
    }
    failed_clock = failed_components["PERSISTENT_PAIR_CLOCK"]
    failed_clock_signed_rows = [
        signed
        for direction_row in failed_clock["direction_rows"]
        for signed in direction_row["signed_refits"]
    ]
    next_component = failed_components["HUMAN_WORN_CENTER_MIGRATION"]
    ordinary_failure_retained_pass = bool(
        len(failed_clock_signed_rows) == direction_count * 2
        and all(
            row["solver_success"] is False
            and row["perturbation_construction_completed"] is False
            and row["exception_type"] == "RuntimeError"
            and row["exception_message"]
            == "INJECTED_ORDINARY_CLOCK_INTERPOLATION_SUPPORT_FAILURE"
            for row in failed_clock_signed_rows
        )
        and failed_clock["pass"] is False
        and failed_audit["pass"] is False
        and failed_center.report["owner_update_eligible"] is False
        and failed_center.report["owner_update_mode"]
        == "LOCAL_NO_UPDATE_COHERENT_NUISANCE_REFIT_FRAGILITY"
        and len(next_component["direction_rows"]) == direction_count
    )
    fragility_no_promotion_pass = bool(
        audit["nominal_candidate_pre_audit_eligible"]
        and primary_center.report["owner_update_eligible"]
        == bool(audit["pass"])
        and (
            audit["pass"]
            or primary_center.report["owner_update_mode"]
            == "LOCAL_NO_UPDATE_COHERENT_NUISANCE_REFIT_FRAGILITY"
        )
    )
    midpoint_gate_pass = bool(
        midpoint_rows
        and all(row["exact_gate_agreement"] for row in midpoint_rows)
        and any(
            row["observed_midpoint_shift_m"] is not None
            and float(row["observed_midpoint_shift_m"]) > midpoint_limit
            and row["observed_direction_pass"] is False
            for row in midpoint_rows
        )
    )
    unit_span_pass = bool(
        audit["every_direction_has_unit_whitened_radius"]
        and all(
            row["direction_count"] == direction_count
            and row["unit_radius_exact"]
            and row["full_span_before_cycle"]
            for row in unit_span_rows
        )
    )
    return {
        "coverage_class": "EXECUTED_OWNER_LEVEL",
        "owner_call_path": [
            "functional_geometry.estimate_joint_center_pair_local",
            "functional_geometry.coherent_perturbed_data",
            "functional_geometry.run_one_trial",
        ],
        "fragility_no_promotion": {
            "pre_audit_eligible": bool(
                audit["nominal_candidate_pre_audit_eligible"]
            ),
            "coherent_audit_pass": bool(audit["pass"]),
            "final_owner_update_eligible": bool(
                primary_center.report["owner_update_eligible"]
            ),
            "owner_update_mode": str(primary_center.report["owner_update_mode"]),
            "pass": fragility_no_promotion_pass,
        },
        "antithetic_midpoint_gate": {
            "rows": midpoint_rows,
            "limit_m": midpoint_limit,
            "pass": midpoint_gate_pass,
        },
        "symmetric_clock_interior": {
            "signed_refit_count": len(clock_signed_rows),
            "retained_index_hashes": sorted(retained_hashes),
            "retained_row_counts": sorted(retained_counts),
            "guarded_endpoint_row_counts": sorted(guarded_counts),
            "endpoint_clamped_or_repeated_rows": sum(
                int(row["endpoint_clamped_or_repeated_rows"])
                for row in clock_signed_rows
            ),
            "cross_block_or_gap_interpolation_count": sum(
                int(row["cross_block_or_gap_interpolation_count"])
                for row in clock_signed_rows
            ),
            "pass": clock_support_pass,
        },
        "ordinary_construction_failure_retention": {
            "injected_exception": (
                "RuntimeError:INJECTED_ORDINARY_CLOCK_INTERPOLATION_SUPPORT_FAILURE"
            ),
            "retained_failed_signed_refit_count": len(failed_clock_signed_rows),
            "clock_component_pass": bool(failed_clock["pass"]),
            "final_owner_update_eligible": bool(
                failed_center.report["owner_update_eligible"]
            ),
            "next_component_direction_count": len(
                next_component["direction_rows"]
            ),
            "product_owner_state_restore_required": False,
            "mutation_fixture_performed_product_owner_state_restore": False,
            "mutation_fixture_restored_injected_numpy_callable_after_owner_return": True,
            "ordinary_failure_was_returned_as_local_no_update": True,
            "pass": ordinary_failure_retained_pass,
        },
        "unit_radius_full_span": {
            "rows": unit_span_rows,
            "direction_semantics": audit["direction_semantics"],
            "pass": unit_span_pass,
        },
        "attempt_125_or_external_artifact_counted_as_evidence": False,
        "pass": bool(
            fragility_no_promotion_pass
            and midpoint_gate_pass
            and clock_support_pass
            and ordinary_failure_retained_pass
            and unit_span_pass
        ),
    }


def _numeric_registered_center_coherent_nuisance_mutation_gate(
    settings: Mapping[str, Any],
) -> dict[str, Any]:
    """Build the frozen POSITIVE_02 fixture and execute the real mutation gate."""

    case_index = 2
    scenario = settings["synthetic"]["positive_scenarios"][case_index]
    seed = int(settings["synthetic"]["positive_seeds"][case_index])
    dt = float(settings["timing"]["sample_period_s"])
    sample = generate_physical_pair(
        seed,
        model=settings["synthetic"]["generator_model"],
        duration_s=float(scenario["duration_s"]),
        sample_period_s=dt,
        nonideal=bool(scenario["nonideal"]),
        wear_mode=str(scenario["wear_mode"]),
        parent_dimension_m=float(scenario["parent_dimension_m"]),
        child_dimension_m=float(scenario["child_dimension_m"]),
        asymmetry_fraction=float(scenario["asymmetry_fraction"]),
        excitation_scale=float(scenario["excitation_scale"]),
        observation_level=float(scenario["observation_level"]),
        imperfect_rest_return=bool(scenario["imperfect_rest_return"]),
    )
    pair, quality = _pair_with_observation_conditions(
        sample,
        dt,
        seed=seed,
        gap_rows=int(scenario["gap_rows"]),
        duplicate_rows=int(scenario["duplicate_rows"]),
        jitter_us=int(scenario["jitter_us"]),
        clipping_rows=int(scenario["clipping_rows"]),
    )
    noise = settings["synthetic"]["estimator_input_noise"]
    inflation = float(quality["joint_covariance_inflation"])
    acc_covariance = (
        np.eye(3) * float(noise["accelerometer_sigma_mps2"]) ** 2 * inflation
    )
    gyro_covariance = (
        np.eye(3) * float(noise["gyroscope_sigma_rads"]) ** 2 * inflation
    )
    gyro_bias_covariance = (
        np.eye(3) * float(noise["gyroscope_bias_sigma_rads"]) ** 2
    )
    primary = estimate_joint_center_pair_local(
        "knee_left", "thigh_left", "shank_left", [pair],
        settings=settings["joint_center"],
        parent_acc_covariance=acc_covariance,
        child_acc_covariance=acc_covariance,
        parent_gyro_observation_covariance=gyro_covariance,
        child_gyro_observation_covariance=gyro_covariance,
        parent_gyro_bias_covariance=gyro_bias_covariance,
        child_gyro_bias_covariance=gyro_bias_covariance,
        execution_guard=_new_guard(settings),
    )
    gate = _numeric_center_coherent_nuisance_refit_gate(
        settings=settings,
        pair=pair,
        primary_center=primary,
        acc_covariance=acc_covariance,
        gyro_observation_covariance=gyro_covariance,
        gyro_bias_covariance=gyro_bias_covariance,
    )
    return {
        **gate,
        "fixture_case_id": "POSITIVE_02",
        "fixture_seed": seed,
        "fixture_selected_before_this_gate_outcome": True,
    }


def _center_robust_f_scale_sensitivity(
    *,
    sample: SyntheticPair,
    pair: AlignedPair,
    primary_center: CenterEstimate,
    settings: Mapping[str, Any],
    acc_covariance: np.ndarray,
    gyro_observation_covariance: np.ndarray,
    gyro_bias_covariance: np.ndarray,
) -> dict[str, Any]:
    """Execute the registered robust-loss bracket through the real owner."""

    center_settings = settings["joint_center"]
    thresholds = settings["synthetic"]["qualification_thresholds"]
    primary_f_scale = float(center_settings["robust_f_scale_standardized"])
    registered = [
        float(value)
        for value in center_settings["robust_f_scale_sensitivity_standardized"]
    ]
    if primary_f_scale != 0.25 or registered != [0.125, 0.25, 0.5]:
        raise RuntimeError("center robust f-scale sensitivity is not the sealed bracket")
    truth = np.r_[
        sample.joint_to_parent_sensor_m,
        sample.joint_to_child_sensor_m,
    ]
    rows = []
    for f_scale in registered:
        sensitivity_settings = dict(center_settings)
        sensitivity_settings["robust_f_scale_standardized"] = f_scale
        estimate = (
            primary_center
            if f_scale == primary_f_scale
            else estimate_joint_center_pair_local(
                "knee_left", "thigh_left", "shank_left", [pair],
                settings=sensitivity_settings,
                parent_acc_covariance=acc_covariance,
                child_acc_covariance=acc_covariance,
                parent_gyro_observation_covariance=gyro_observation_covariance,
                child_gyro_observation_covariance=gyro_observation_covariance,
                parent_gyro_bias_covariance=gyro_bias_covariance,
                child_gyro_bias_covariance=gyro_bias_covariance,
                execution_guard=_new_guard(settings),
            )
        )
        value = np.r_[
            estimate.joint_to_parent_sensor_m,
            estimate.joint_to_child_sensor_m,
        ]
        error = value - truth
        total_covariance = np.asarray(estimate.covariance_m2, dtype=float)
        informed_covariance = np.asarray(
            estimate.report["informed_observation_covariance_m2"], dtype=float,
        )
        informed_basis = np.asarray(
            estimate.report["gauge_reduced_robust_bread_informed_basis"], dtype=float,
        )
        informed_projection_error = (
            float(np.linalg.norm(informed_basis.T @ error))
            if informed_basis.shape[1]
            else 0.0
        )
        total_sigma = np.sqrt(np.maximum(np.diag(total_covariance), 0.0))
        total_eigenvalues = np.linalg.eigvalsh(total_covariance)
        informed_eigenvalues = np.linalg.eigvalsh(informed_covariance)
        truth_mahalanobis_squared = float(
            error @ np.linalg.pinv(total_covariance, hermitian=True) @ error
        )
        rows.append({
            "robust_f_scale_standardized": f_scale,
            "is_preregistered_primary": f_scale == primary_f_scale,
            "owner_update_eligible": bool(estimate.report["owner_update_eligible"]),
            "owner_update_mode": str(estimate.report["owner_update_mode"]),
            "multistart_basin_identifiable": bool(
                estimate.report["multistart_basin_identifiable"]
            ),
            "boundary_candidate_competitive": bool(
                estimate.report["boundary_candidate_competitive"]
            ),
            "remote_interior_basin_competitive": bool(
                estimate.report["remote_interior_basin_competitive"]
            ),
            "chronological_prefix_heldin_pass": bool(
                estimate.report["chronological_prefix_heldin_stability"]["pass"]
            ),
            "gauge_reduced_rank": int(estimate.report["gauge_reduced_rank"]),
            "condition_number": float(estimate.report["condition_number"]),
            "parent_endpoint_error_m": float(np.linalg.norm(error[:3])),
            "child_endpoint_error_m": float(np.linalg.norm(error[3:])),
            "full_point_error_m": float(np.linalg.norm(error)),
            "informed_projection_error_m": informed_projection_error,
            "truth_mahalanobis_squared_total_covariance": truth_mahalanobis_squared,
            "coordinate_2sigma_coverage": (
                np.abs(error) <= 2.0 * total_sigma
            ).tolist(),
            "coordinate_2sigma_coverage_fraction": float(np.mean(
                np.abs(error) <= 2.0 * total_sigma
            )),
            "total_covariance_trace_m2": float(np.trace(total_covariance)),
            "informed_observation_covariance_trace_m2": float(
                np.trace(informed_covariance)
            ),
            "total_covariance_finite": bool(np.all(np.isfinite(total_covariance))),
            "informed_covariance_finite": bool(
                np.all(np.isfinite(informed_covariance))
            ),
            "total_covariance_symmetric": bool(np.allclose(
                total_covariance, total_covariance.T,
                atol=float(thresholds["covariance_symmetry_tolerance"]), rtol=0.0,
            )),
            "informed_covariance_symmetric": bool(np.allclose(
                informed_covariance, informed_covariance.T,
                atol=float(thresholds["covariance_symmetry_tolerance"]), rtol=0.0,
            )),
            "total_covariance_minimum_eigenvalue_m2": float(
                np.min(total_eigenvalues)
            ),
            "informed_covariance_minimum_eigenvalue_m2": float(
                np.min(informed_eigenvalues)
            ),
            "selected_center_m": value.tolist(),
        })
    primary = next(row for row in rows if row["is_preregistered_primary"])
    primary_value = np.asarray(primary["selected_center_m"], dtype=float)
    for row in rows:
        row["center_delta_from_preregistered_primary_m"] = float(np.linalg.norm(
            np.asarray(row["selected_center_m"], dtype=float) - primary_value
        ))
    total_traces = np.asarray([row["total_covariance_trace_m2"] for row in rows])
    informed_traces = np.asarray([
        row["informed_observation_covariance_trace_m2"] for row in rows
    ])
    trace_floor = np.finfo(float).eps
    total_trace_ratio = float(
        np.max(total_traces) / max(float(np.min(total_traces)), trace_floor)
    )
    informed_trace_ratio = float(
        np.max(informed_traces) / max(float(np.min(informed_traces)), trace_floor)
    )
    pass_gate = bool(
        all(
            row["owner_update_eligible"]
            and row["multistart_basin_identifiable"]
            and not row["boundary_candidate_competitive"]
            and not row["remote_interior_basin_competitive"]
            and row["chronological_prefix_heldin_pass"]
            and max(
                row["parent_endpoint_error_m"], row["child_endpoint_error_m"]
            ) <= float(thresholds[
                "center_robust_f_scale_maximum_endpoint_error_m"
            ])
            and row["informed_projection_error_m"] <= float(thresholds[
                "center_robust_f_scale_maximum_informed_projection_error_m"
            ])
            and row["truth_mahalanobis_squared_total_covariance"] <= float(
                thresholds[
                    "center_robust_f_scale_maximum_truth_mahalanobis_squared"
                ]
            )
            and row["center_delta_from_preregistered_primary_m"] <= float(
                thresholds["center_robust_f_scale_maximum_delta_from_primary_m"]
            )
            and row["total_covariance_finite"]
            and row["informed_covariance_finite"]
            and row["total_covariance_symmetric"]
            and row["informed_covariance_symmetric"]
            and row["total_covariance_minimum_eigenvalue_m2"] >= float(
                thresholds["covariance_minimum_eigenvalue_m2"]
            )
            and row["informed_covariance_minimum_eigenvalue_m2"] >= float(
                thresholds["covariance_minimum_eigenvalue_m2"]
            )
            for row in rows
        )
        and total_trace_ratio <= float(
            thresholds["center_robust_f_scale_maximum_covariance_trace_ratio"]
        )
        and informed_trace_ratio <= float(
            thresholds["center_robust_f_scale_maximum_covariance_trace_ratio"]
        )
    )
    return {
        "coverage_class": "EXECUTED_OWNER_LEVEL",
        "owner_call_path": [
            "functional_geometry.estimate_joint_center_pair_local"
        ],
        "registered_f_scales_standardized": registered,
        "preregistered_primary_f_scale_standardized": primary_f_scale,
        "primary_selected_from_observed_outcomes": False,
        "rows": rows,
        "total_covariance_trace_ratio": total_trace_ratio,
        "informed_covariance_trace_ratio": informed_trace_ratio,
        "thresholds": {
            key: float(thresholds[key])
            for key in (
                "center_robust_f_scale_maximum_endpoint_error_m",
                "center_robust_f_scale_maximum_informed_projection_error_m",
                "center_robust_f_scale_maximum_delta_from_primary_m",
                "center_robust_f_scale_maximum_truth_mahalanobis_squared",
                "center_robust_f_scale_maximum_covariance_trace_ratio",
            )
        },
        "pass": pass_gate,
    }


def _timing_kwargs(settings: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "sample_period_s": float(settings["sample_period_s"]),
        "maximum_lag_s": float(settings["maximum_lag_s"]),
        "smoothing_window_samples": int(settings["smoothing_window_samples"]),
        "minimum_overlap_s": float(settings["minimum_overlap_s"]),
    }


def _axis_error_deg(estimate: np.ndarray, truth: np.ndarray) -> float:
    estimate = np.asarray(estimate, dtype=float)
    truth = np.asarray(truth, dtype=float)
    return float(np.degrees(np.arccos(np.clip(abs(float(estimate @ truth)), -1.0, 1.0))))


def _representative_full_tree_geometry() -> tuple[
    dict[str, AxisEstimate], dict[str, CenterEstimate],
]:
    """Deterministic nondegenerate owner fixture for frame covariance gates."""

    points = {
        "pelvis": np.array([0.0, 0.0, 0.0]),
        "torso": np.array([0.0, 0.0, 0.40]),
        "upper_arm_left": np.array([-0.22, 0.0, 0.38]),
        "forearm_left": np.array([-0.50, 0.0, 0.34]),
        "upper_arm_right": np.array([0.22, 0.0, 0.38]),
        "forearm_right": np.array([0.50, 0.0, 0.34]),
        "thigh_left": np.array([-0.12, 0.0, -0.45]),
        "shank_left": np.array([-0.12, 0.0, -0.88]),
        "thigh_right": np.array([0.12, 0.0, -0.46]),
        "shank_right": np.array([0.12, 0.0, -0.91]),
    }
    joints = {
        "pelvis_torso": np.array([0.0, 0.0, 0.20]),
        "shoulder_left": np.array([-0.10, 0.0, 0.40]),
        "elbow_left": np.array([-0.36, 0.0, 0.36]),
        "shoulder_right": np.array([0.10, 0.0, 0.40]),
        "elbow_right": np.array([0.36, 0.0, 0.36]),
        "hip_left": np.array([-0.06, 0.0, -0.12]),
        "knee_left": np.array([-0.12, 0.0, -0.66]),
        "hip_right": np.array([0.06, 0.0, -0.12]),
        "knee_right": np.array([0.12, 0.0, -0.68]),
    }
    center_statistical = np.eye(6) * 4e-4
    center_systematic = np.eye(6) * 0.03**2
    centers = {
        edge: CenterEstimate(
            edge=edge,
            parent=parent,
            child=child,
            joint_to_parent_sensor_m=-(joints[edge] - points[parent]),
            joint_to_child_sensor_m=-(joints[edge] - points[child]),
            covariance_m2=center_statistical + center_systematic,
            report={
                "sandwich_covariance_m2": center_statistical.tolist(),
                "statistical_covariance_including_nullspace_prior_m2": center_statistical.tolist(),
                "human_worn_model_floor_m": 0.03,
                "accelerometer_bias_drift_systematic_covariance_m2": np.zeros((6, 6)).tolist(),
                "accelerometer_scale_cross_axis_systematic_covariance_m2": np.zeros((6, 6)).tolist(),
                "gyro_bias_systematic_covariance_m2": np.zeros((6, 6)).tolist(),
                "gyro_bias_drift_systematic_covariance_m2": np.zeros((6, 6)).tolist(),
                "gyro_scale_cross_axis_systematic_covariance_m2": np.zeros((6, 6)).tolist(),
                "accelerometer_gyro_shared_scale_cross_axis_systematic_covariance_m2": np.zeros((6, 6)).tolist(),
                "persistent_clock_systematic_covariance_m2": np.zeros((6, 6)).tolist(),
                "human_worn_systematic_covariance_m2": center_systematic.tolist(),
                "total_systematic_covariance_m2": center_systematic.tolist(),
                "owner_update_eligible": True,
                "owner_update_mode": "GAUGE_REDUCED_ROBUST_BREAD_INFORMED_SUBSPACE",
                "gauge_reduced_robust_bread_informed_basis": np.eye(6).tolist(),
            },
        )
        for edge, parent, child in EDGE_SPECS
    }
    tangent_basis = np.array([[1.0, 0.0], [0.0, 0.0], [0.0, 1.0]])
    statistical = np.eye(4) * np.deg2rad(2.0) ** 2
    systematic = np.eye(4) * np.deg2rad(5.0) ** 2
    axes = {
        edge: AxisEstimate(
            edge=edge,
            parent_axis_sensor=np.array([0.0, 1.0, 0.0]),
            child_axis_sensor=np.array([0.0, 1.0, 0.0]),
            tangent_covariance_rad2=statistical + systematic,
            report={
                "parent_tangent_basis_sensor": tangent_basis.tolist(),
                "child_tangent_basis_sensor": tangent_basis.tolist(),
                "statistical_tangent_covariance_rad2": statistical.tolist(),
                "total_systematic_tangent_covariance_rad2": systematic.tolist(),
                "systematic_component_tangent_covariances_rad2": {
                    "human_worn": systematic.tolist(),
                },
                "systematic_human_worn_tangent_covariance_rad2": systematic.tolist(),
                "owner_update_eligible": True,
                "owner_update_mode": "PRODUCT_S2_HESSIAN_INFORMED_UPDATE",
            },
        )
        for edge in ("elbow_left", "elbow_right", "knee_left", "knee_right")
    }
    return axes, centers


def _runtime_pair_binding(pair: AlignedPair) -> Mapping[str, Any]:
    """Return the immutable runtime binding used to prove pair reuse."""

    provenance = dict(pair.provenance)
    token = str(provenance.get("runtime_owner_token", ""))
    payload = provenance.get("owner_binding_payload")
    if not token or not isinstance(payload, Mapping):
        raise RuntimeError("post-QMT fixture pair lacks its runtime owner binding")
    payload_sha256 = sha256(
        json.dumps(
            dict(payload), sort_keys=True, separators=(",", ":"), allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()
    return {
        "runtime_owner_token": token,
        "owner_binding_payload_sha256": payload_sha256,
    }


def _align_exact_post_qmt_fixture_pairs(
    runtime: Any,
) -> tuple[dict[str, AlignedPair], Mapping[str, Any]]:
    """Align every rooted-tree edge once while the public API permits it."""

    required_stage = "CURRENT_EPISODE_LOCAL_FACTORS"
    if str(runtime.stage) != required_stage:
        raise RuntimeError("post-QMT fixture pairs must be aligned before geometry/frame updates")
    pair_by_edge: dict[str, AlignedPair] = {}
    call_order: list[str] = []
    binding_by_edge: dict[str, Mapping[str, Any]] = {}
    for edge, _, _ in EDGE_SPECS:
        if edge in pair_by_edge:
            raise RuntimeError("sealed EDGE_SPECS contains a duplicate edge")
        pair = runtime.align_current_pair(edge=edge)
        if pair.edge != edge:
            raise RuntimeError("runtime aligned pair edge differs from sealed EDGE_SPECS")
        pair_by_edge[edge] = pair
        call_order.append(edge)
        binding_by_edge[edge] = _runtime_pair_binding(pair)
    expected_order = [edge for edge, _, _ in EDGE_SPECS]
    if call_order != expected_order or len(pair_by_edge) != len(EDGE_SPECS):
        raise RuntimeError("post-QMT fixture did not align the exact rooted-tree edge order once")
    return pair_by_edge, {
        "alignment_stage": required_stage,
        "public_alignment_api": "C2PipelineRuntime.align_current_pair",
        "edge_call_order": call_order,
        "call_count_by_edge": {edge: call_order.count(edge) for edge in call_order},
        "binding_by_edge": binding_by_edge,
        "private_alignment_owner_called_by_fixture": False,
    }


def _audit_post_frame_pair_identity_reuse(
    pre_frame_pairs: Mapping[str, AlignedPair],
    qmt_consumed_pairs: Mapping[str, AlignedPair],
) -> Mapping[str, Any]:
    """Prove QMT consumed the exact pre-frame pair objects and bindings."""

    expected_order = [edge for edge, _, _ in EDGE_SPECS]
    if list(pre_frame_pairs) != expected_order or list(qmt_consumed_pairs) != expected_order:
        raise RuntimeError("post-frame pair reuse audit differs from sealed EDGE_SPECS order")
    rows: dict[str, Mapping[str, Any]] = {}
    for edge in expected_order:
        before = pre_frame_pairs[edge]
        consumed = qmt_consumed_pairs[edge]
        before_binding = _runtime_pair_binding(before)
        consumed_binding = _runtime_pair_binding(consumed)
        rows[edge] = {
            "same_python_object": consumed is before,
            "pre_frame_binding": before_binding,
            "qmt_binding": consumed_binding,
            "binding_equal": consumed_binding == before_binding,
        }
    return {
        "edge_order": expected_order,
        "rows": rows,
        "all_edges_same_object_and_binding": all(
            row["same_python_object"] and row["binding_equal"]
            for row in rows.values()
        ),
        "realignment_or_reingestion_count": 0,
    }


def _post_qmt_runtime_physical_owner_gate(
    settings: Mapping[str, Any],
    *,
    prefit_registry_seal_path: str | Path,
    initial_stochastic_state: Mapping[str, Any],
    axes: Mapping[str, AxisEstimate],
    centers: Mapping[str, CenterEstimate],
) -> Mapping[str, Any]:
    """Run real QMT, rooted propagation, token issuance, and physical FK."""

    from .pipeline_runtime import C2PipelineRuntime

    scenario = settings["synthetic"]["post_qmt_runtime_positive"]
    actions = tuple(settings["execution_contract"]["chronological_actions"])
    nodes = tuple(str(node) for node in initial_stochastic_state["nodes"])
    runtime = C2PipelineRuntime(
        settings,
        initial_stochastic_state,
        prefit_registry_seal_path=prefit_registry_seal_path,
        execution_role="SYNTHETIC_QUALIFICATION",
    )
    sample_period_us = int(scenario["sample_period_us"])
    for action_index, action in enumerate(actions):
        row_count = int(
            scenario["long_action_rows"]
            if action_index == int(scenario["chronological_index"])
            else scenario["other_action_rows"]
        )
        sample_index = np.arange(row_count, dtype=float)
        phase = 2.0 * np.pi * sample_index / max(row_count - 1, 1)
        timer_start = 1_000_000 + action_index * 20_000_000
        rows_by_node: dict[str, np.ndarray] = {}
        for node_index, node in enumerate(nodes):
            node_phase = phase + 0.07 * node_index
            rows = np.zeros(row_count, dtype=IMU_DTYPE)
            rows["derived_boot_epoch"] = 0
            rows["imu_sample_sequence"] = (
                np.arange(row_count, dtype=np.uint32) + action_index * 4096
            ).astype(np.uint16)
            rows["node_timer_us"] = (
                timer_start
                + np.arange(row_count, dtype=np.uint64) * sample_period_us
            )
            acc_amplitude = float(scenario["accelerometer_motion_amplitude_raw"])
            rows["acc_raw"][:, 0] = np.rint(acc_amplitude * np.sin(1.3 * node_phase)).astype(np.int16)
            rows["acc_raw"][:, 1] = np.rint(acc_amplitude * np.cos(0.7 * node_phase)).astype(np.int16)
            rows["acc_raw"][:, 2] = (
                int(scenario["accelerometer_z_raw"])
                + np.rint(0.5 * acc_amplitude * np.sin(0.4 * node_phase)).astype(np.int16)
            )
            primary = float(scenario["gyroscope_motion_amplitude_raw"])
            secondary = float(scenario["gyroscope_secondary_amplitude_raw"])
            rows["gyro_raw"][:, 0] = np.rint(primary * np.sin(0.9 * node_phase)).astype(np.int16)
            rows["gyro_raw"][:, 1] = np.rint(secondary * np.cos(1.1 * node_phase)).astype(np.int16)
            rows["gyro_raw"][:, 2] = np.rint(0.6 * primary * np.sin(0.5 * node_phase)).astype(np.int16)
            rows["raw_start_offset"] = (
                action_index * 10_000_000
                + node_index * 100_000
                + np.arange(row_count, dtype=np.uint64) * 16
            )
            rows["raw_end_offset"] = rows["raw_start_offset"] + 16
            rows["raw_sample_index"] = np.arange(row_count, dtype=np.uint64).astype(np.uint8)
            rows["decode_acceptance_status"] = 1
            rows_by_node[node] = rows
        runtime.ingest_orientation_episode(DecodedAction(
            action=action,
            chronological_index=action_index,
            interval=(action_index * 1000, action_index * 1000 + 999),
            rows_by_node=rows_by_node,
            access_audit={
                "reader_session_id": "SYNTHETIC_POST_QMT_OWNER_PATH",
                "action": action,
                "chronological_index": action_index,
            },
            decode_audit={"synthetic_post_qmt_owner_path": True},
        ))
    runtime.finish_orientation_and_begin_calibration()
    for estimate in centers.values():
        runtime._geometry_owner.ingest_center(
            estimate,
            chronological_index=-1,
            action="SYNTHETIC_POST_QMT_FULL_TREE_SEED",
            reference_time_s=0.0,
        )
    for estimate in axes.values():
        runtime._geometry_owner.ingest_axis(
            estimate,
            chronological_index=-1,
            action="SYNTHETIC_POST_QMT_FULL_TREE_SEED",
            reference_time_s=0.0,
        )

    index = int(scenario["chronological_index"])
    action = actions[index]
    span_reports: dict[str, list[Mapping[str, Any]]] = {}
    binding_audit: Mapping[str, Any] | None = None
    assessment_count = 0
    with runtime.calibration_episode_transaction(index, action):
        runtime.score_current_prequential()
        pair_by_edge, pre_frame_pair_audit = _align_exact_post_qmt_fixture_pairs(
            runtime
        )
        runtime.finish_current_geometry_update()
        branches = runtime.update_current_frame_branches()
        hard_support = runtime.current_heading_hard_support()
        supported = tuple(hard_support["branch_ids"])
        if not supported or tuple(branch.branch_id for branch in branches) != runtime._branch_ids:
            raise RuntimeError("post-QMT synthetic owner path did not retain canonical branches")
        runtime.validate_heading_execution_branch_ids(
            supported,
            hard_support_token=str(hard_support["owner_token"]),
        )
        observed_branch = supported[0]
        qmt_consumed_pairs: dict[str, AlignedPair] = {}
        for branch_id in supported:
            for edge, _, _ in EDGE_SPECS:
                pair = pair_by_edge[edge]
                if branch_id == observed_branch:
                    qmt_consumed_pairs[edge] = pair
                    reports = []
                    for span_index in range(len(pair.contiguous_spans)):
                        result = runtime.process_current_heading_span(
                            branch_id=branch_id,
                            pair=pair,
                            span_index=span_index,
                            hard_support_token=str(hard_support["owner_token"]),
                        )
                        reports.append(dict(result.report))
                    span_reports[edge] = reports
                else:
                    runtime.record_current_heading_no_update(
                        branch_id=branch_id,
                        edge=edge,
                        cause="SYNTHETIC_POST_QMT_NONSELECTED_BRANCH_PERSISTENT_NO_UPDATE",
                        hard_support_token=str(hard_support["owner_token"]),
                    )
        runtime.finish_current_heading()
        pair_reuse_audit = _audit_post_frame_pair_identity_reuse(
            pair_by_edge, qmt_consumed_pairs,
        )
        _, _, bindings, common_audit = runtime._owner_derived_physical_prefix_inputs()
        binding_audit = {
            "common": dict(common_audit),
            "branch_tokens": {
                branch_id: {
                    "runtime_owner_id": binding["runtime_owner_id"],
                    "chronological_index": binding["chronological_index"],
                    "action": binding["action"],
                    "runtime_owner_binding_payload_sha256": binding[
                        "runtime_owner_binding_payload_sha256"
                    ],
                    "runtime_owner_token_present": bool(binding["runtime_owner_token"]),
                    "rooted_qmt_common_time_sha256": binding[
                        "rooted_qmt_trajectory_common_time_sha256"
                    ],
                }
                for branch_id, binding in bindings.items()
            },
        }
        assessments = runtime.assess_current_physical_candidates()
        assessment_count = len(assessments)
        runtime.commit_current_progressive()

    qmt_executed_by_edge = {
        edge: bool(reports and all(report["qmt_executed"] for report in reports))
        for edge, reports in span_reports.items()
    }
    pass_gate = bool(
        len(qmt_executed_by_edge) == len(EDGE_SPECS)
        and all(qmt_executed_by_edge.values())
        and binding_audit is not None
        and all(
            row["runtime_owner_token_present"]
            for row in binding_audit["branch_tokens"].values()
        )
        and all(
            count == 1
            for count in pre_frame_pair_audit["call_count_by_edge"].values()
        )
        and pair_reuse_audit["all_edges_same_object_and_binding"]
        and pair_reuse_audit["realignment_or_reingestion_count"] == 0
        and assessment_count > 0
        and len(runtime._progress_snapshots) == 1
    )
    return {
        "schema": "biospur-c2-synthetic-runtime-post-qmt-physical-owner-gate-v1",
        "coverage_class": "EXECUTED_OWNER_LEVEL",
        "owner_call_path": list(scenario["required_owner_path"]),
        "qmt_observed_branch": observed_branch,
        "qmt_executed_by_edge": qmt_executed_by_edge,
        "qmt_span_report_summary": {
            edge: [{
                "official_callable": report["official_callable"],
                "qmt_executed": report["qmt_executed"],
                "persistent_filter_effective_update_count": report[
                    "persistent_filter_effective_update_count"
                ],
            } for report in reports]
            for edge, reports in span_reports.items()
        },
        "binding_audit": binding_audit,
        "pre_frame_pair_alignment_audit": pre_frame_pair_audit,
        "post_frame_pair_reuse_audit": pair_reuse_audit,
        "physical_assessment_count": assessment_count,
        "progressive_prefix_count": len(runtime._progress_snapshots),
        "manual_source_label_or_self_hash_counted_as_owner_evidence": False,
        "pass": pass_gate,
    }


_CENTER_LOW_INFORMATION_NO_UPDATE_MODE = (
    "LOCAL_NO_UPDATE_SOLVER_BOUNDARY_OR_BASIN_COMPETITION_"
    "INFORMATION_MAGNITUDE_OR_PREFIX_STABILITY"
)
_AXIS_LOW_INFORMATION_NO_UPDATE_MODE = (
    "LOCAL_NO_UPDATE_LOW_EXCITATION_OR_RANK_OR_EFFECTIVE_SUPPORT"
)


def _static_low_information_owner_evidence(
    report: Mapping[str, Any],
) -> dict[str, Any]:
    prefix = report.get("chronological_prefix_heldin_stability", {})
    return {
        "owner_update_eligible": report.get("owner_update_eligible"),
        "status": report.get("status"),
        "owner_update_mode": report.get("owner_update_mode"),
        "information_condition_eligible": report.get(
            "information_condition_eligible"
        ),
        "minimum_informed_information_eigenvalue_m2_inv": report.get(
            "minimum_informed_information_eigenvalue_m2_inv"
        ),
        "required_minimum_informed_information_eigenvalue_m2_inv": report.get(
            "required_minimum_informed_information_eigenvalue_m2_inv"
        ),
        "maximum_informed_observation_sigma_m": report.get(
            "maximum_informed_observation_sigma_m"
        ),
        "required_maximum_informed_observation_sigma_m": report.get(
            "required_maximum_informed_observation_sigma_m"
        ),
        "multistart_basin_identifiable": report.get(
            "multistart_basin_identifiable"
        ),
        "prefix_stability_pass": prefix.get("pass"),
        "prefix_boundary_competitive": prefix.get(
            "prefix_boundary_competitive"
        ),
        "boundary_candidate_competitive": report.get(
            "boundary_candidate_competitive"
        ),
        "rank_and_condition_number_are_diagnostic_not_the_pass_predicate": True,
        "diagnostic_gauge_reduced_rank": report.get("gauge_reduced_rank"),
        "diagnostic_condition_number": report.get("condition_number"),
    }


def _static_low_information_owner_no_false_pass(
    report: Mapping[str, Any],
) -> bool:
    evidence = _static_low_information_owner_evidence(report)
    minimum_information = evidence[
        "minimum_informed_information_eigenvalue_m2_inv"
    ]
    required_information = evidence[
        "required_minimum_informed_information_eigenvalue_m2_inv"
    ]
    maximum_sigma = evidence["maximum_informed_observation_sigma_m"]
    required_maximum_sigma = evidence[
        "required_maximum_informed_observation_sigma_m"
    ]
    magnitude_values_present = all(
        isinstance(value, (int, float))
        for value in (
            minimum_information,
            required_information,
            maximum_sigma,
            required_maximum_sigma,
        )
    )
    return bool(
        evidence["owner_update_eligible"] is False
        and evidence["status"] == "LOW_INFORMATION_OR_NUMERICAL_GUARD_CANDIDATE"
        and evidence["owner_update_mode"] == _CENTER_LOW_INFORMATION_NO_UPDATE_MODE
        and evidence["information_condition_eligible"] is False
        and magnitude_values_present
        and float(minimum_information) < float(required_information)
        and float(maximum_sigma) > float(required_maximum_sigma)
        and evidence["multistart_basin_identifiable"] is False
        and evidence["prefix_stability_pass"] is False
        and evidence["prefix_boundary_competitive"] is True
        and evidence["boundary_candidate_competitive"] is True
    )


def _near_axis_owner_evidence(report: Mapping[str, Any]) -> dict[str, Any]:
    nuisance_audit = report.get("axis_calibration_nuisance_audit", {})
    component_validations = {
        str(name): {
            "all_rows_pass": component.get(
                "official_refit_linearization_validation", {}
            ).get("all_rows_pass"),
            "required_scales": component.get(
                "official_refit_linearization_validation", {}
            ).get("required_scales"),
        }
        for name, component in nuisance_audit.items()
        if isinstance(component, Mapping)
    }
    failed_required_components = sorted(
        name
        for name, validation in component_validations.items()
        if validation["required_scales"]
        and validation["all_rows_pass"] is False
    )
    exact_hessian_audit = report.get("exact_score_hessian_audit", {})
    return {
        "owner_update_eligible": report.get("owner_update_eligible"),
        "owner_update_mode": report.get("owner_update_mode"),
        "axis_calibration_nuisance_push_forward_complete": report.get(
            "axis_calibration_nuisance_push_forward_complete"
        ),
        "required_official_refit_component_validations": component_validations,
        "failed_required_official_refit_components": failed_required_components,
        "at_least_one_required_official_refit_component_failed": bool(
            failed_required_components
        ),
        "exact_score_hessian_pass": exact_hessian_audit.get("pass"),
        "effective_support_rows": report.get("effective_support_rows"),
        "minimum_effective_support_rows": report.get(
            "minimum_effective_support_rows"
        ),
        "hessian_informed_rank": report.get("hessian_informed_rank"),
        "local_tangent_parameter_dimension": report.get(
            "local_tangent_parameter_dimension"
        ),
        "block_selection_status": report.get("block_selection_status"),
        "selection_hessian_support_and_rank_success_cannot_override_"
        "incomplete_nuisance_propagation": True,
    }


def _near_axis_owner_no_false_pass(report: Mapping[str, Any]) -> bool:
    evidence = _near_axis_owner_evidence(report)
    effective_support = evidence["effective_support_rows"]
    minimum_support = evidence["minimum_effective_support_rows"]
    support_values_present = all(
        isinstance(value, (int, float))
        for value in (effective_support, minimum_support)
    )
    return bool(
        evidence["owner_update_eligible"] is False
        and evidence["owner_update_mode"] == _AXIS_LOW_INFORMATION_NO_UPDATE_MODE
        and evidence["axis_calibration_nuisance_push_forward_complete"] is False
        and evidence[
            "at_least_one_required_official_refit_component_failed"
        ]
        and evidence["block_selection_status"]
        == "NOISE_STANDARDIZED_THRESHOLD_MET"
        and evidence["exact_score_hessian_pass"] is True
        and support_values_present
        and float(effective_support) >= float(minimum_support)
        and evidence["hessian_informed_rank"]
        == evidence["local_tangent_parameter_dimension"]
    )


def run_qualification(
    settings: Mapping[str, Any],
    *,
    prefit_registry_seal_path: str | Path,
) -> dict[str, Any]:
    dt = float(settings["timing"]["sample_period_s"])
    synthetic_settings = settings["synthetic"]
    generator_model = synthetic_settings["generator_model"]
    estimator_noise = synthetic_settings["estimator_input_noise"]
    fixtures = synthetic_settings["mutation_fixtures"]
    acc_covariance = np.eye(3) * float(
        estimator_noise["accelerometer_sigma_mps2"]
    ) ** 2
    gyro_covariance = np.eye(3) * float(
        estimator_noise["gyroscope_sigma_rads"]
    ) ** 2
    gyro_bias_covariance = np.eye(3) * float(
        estimator_noise["gyroscope_bias_sigma_rads"]
    ) ** 2
    positives = []
    coordinate_covered = []
    positive_centers: list[CenterEstimate] = []
    positive_covariance_inflations: list[float] = []
    scenario_templates = tuple(settings["synthetic"]["positive_scenarios"])
    seeds = list(settings["synthetic"]["positive_seeds"])
    if len(seeds) != len(scenario_templates):
        raise ValueError("sealed positive seeds must match sealed positive scenarios")
    positive_pairs = []
    for case_index, (seed, scenario) in enumerate(zip(seeds, scenario_templates, strict=True)):
        sample = generate_physical_pair(
            int(seed), model=generator_model,
            duration_s=float(scenario["duration_s"]), sample_period_s=dt,
            nonideal=bool(scenario["nonideal"]),
            wear_mode=str(scenario["wear_mode"]),
            parent_dimension_m=float(scenario["parent_dimension_m"]),
            child_dimension_m=float(scenario["child_dimension_m"]),
            asymmetry_fraction=float(scenario["asymmetry_fraction"]),
            excitation_scale=float(scenario["excitation_scale"]),
            observation_level=float(scenario["observation_level"]),
            imperfect_rest_return=bool(scenario["imperfect_rest_return"]),
        )
        frontend_sample, frontend_audit = (
            _production_equivalent_synthetic_orientation_frontend(
                sample,
                settings=settings,
            )
        )
        pair, quality = _pair_with_observation_conditions(
            frontend_sample, dt, seed=int(seed), gap_rows=int(scenario["gap_rows"]),
            duplicate_rows=int(scenario["duplicate_rows"]), jitter_us=int(scenario["jitter_us"]),
            clipping_rows=int(scenario["clipping_rows"]),
        )
        quality["owner_call_path"] = [
            *frontend_audit["owner_call_path"],
            *quality["owner_call_path"],
        ]
        quality["production_equivalent_orientation_frontend"] = frontend_audit
        positive_pairs.append(pair)
        inflation = float(quality["joint_covariance_inflation"])
        guard = _new_guard(settings)
        center = estimate_joint_center_pair_local(
            "knee_left", "thigh_left", "shank_left", [pair],
            settings=settings["joint_center"],
            parent_acc_covariance=acc_covariance * inflation,
            child_acc_covariance=acc_covariance * inflation,
            parent_gyro_observation_covariance=gyro_covariance * inflation,
            child_gyro_observation_covariance=gyro_covariance * inflation,
            parent_gyro_bias_covariance=gyro_bias_covariance,
            child_gyro_bias_covariance=gyro_bias_covariance,
            execution_guard=guard,
        )
        positive_centers.append(center)
        positive_covariance_inflations.append(inflation)
        center_robust_f_scale_sensitivity = _center_robust_f_scale_sensitivity(
            sample=sample,
            pair=pair,
            primary_center=center,
            settings=settings,
            acc_covariance=acc_covariance * inflation,
            gyro_observation_covariance=gyro_covariance * inflation,
            gyro_bias_covariance=gyro_bias_covariance,
        )
        axis = estimate_hinge_axis_qmt(
            "knee_left", [pair], settings=settings["hinge_axis"],
            parent_acc_covariance=acc_covariance * inflation,
            child_acc_covariance=acc_covariance * inflation,
            parent_gyro_covariance=gyro_covariance * inflation,
            child_gyro_covariance=gyro_covariance * inflation,
            parent_gyro_bias_covariance=gyro_bias_covariance,
            child_gyro_bias_covariance=gyro_bias_covariance,
            execution_guard=guard,
        )
        threshold_neighborhood_eligibility = []
        for support_floor in settings["hinge_axis"]["effective_support_sensitivity_rows"]:
            sensitivity_settings = dict(settings["hinge_axis"])
            sensitivity_settings["minimum_effective_support_rows"] = float(support_floor)
            sensitivity_axis = (
                axis
                if float(support_floor)
                == float(settings["hinge_axis"]["minimum_effective_support_rows"])
                else estimate_hinge_axis_qmt(
                    "knee_left", [pair], settings=sensitivity_settings,
                    parent_acc_covariance=acc_covariance * inflation,
                    child_acc_covariance=acc_covariance * inflation,
                    parent_gyro_covariance=gyro_covariance * inflation,
                    child_gyro_covariance=gyro_covariance * inflation,
                    parent_gyro_bias_covariance=gyro_bias_covariance,
                    child_gyro_bias_covariance=gyro_bias_covariance,
                    execution_guard=_new_guard(settings),
                )
            )
            threshold_neighborhood_eligibility.append({
                "minimum_effective_support_rows": float(support_floor),
                "observed_effective_support_rows": float(
                    sensitivity_axis.report["effective_support_rows"]
                ),
                "owner_update_eligible": bool(
                    sensitivity_axis.report["owner_update_eligible"]
                ),
                "purpose": "ELIGIBILITY_ONLY_NOT_ESTIMATE_OR_UNCERTAINTY_STABILITY",
            })
        threshold_neighborhood_eligibility_pass = bool(all(
            row["owner_update_eligible"] for row in threshold_neighborhood_eligibility
        ))

        support_information_sensitivity = []
        for block_count, support_floor in zip(
            settings["hinge_axis"]["support_information_block_counts"],
            settings["hinge_axis"]["support_information_effective_support_threshold_rows"],
            strict=True,
        ):
            information_pair = _complete_block_prefix_pair(
                pair,
                block_rows=int(settings["hinge_axis"]["selection_block_rows"]),
                block_count=int(block_count),
            )
            information_settings = dict(settings["hinge_axis"])
            information_settings["minimum_effective_support_rows"] = float(support_floor)
            information_axis = estimate_hinge_axis_qmt(
                "knee_left", [information_pair], settings=information_settings,
                parent_acc_covariance=acc_covariance * inflation,
                child_acc_covariance=acc_covariance * inflation,
                parent_gyro_covariance=gyro_covariance * inflation,
                child_gyro_covariance=gyro_covariance * inflation,
                parent_gyro_bias_covariance=gyro_bias_covariance,
                child_gyro_bias_covariance=gyro_bias_covariance,
                execution_guard=_new_guard(settings),
            )
            total_covariance = np.asarray(
                information_axis.tangent_covariance_rad2, dtype=float,
            )
            statistical_covariance = np.asarray(
                information_axis.report["statistical_tangent_covariance_rad2"],
                dtype=float,
            )
            tangent_error = _product_s2_tangent_error(
                information_axis,
                sample.parent_axis_sensor,
                sample.child_axis_sensor,
            )
            truth_mahalanobis_squared = float(
                tangent_error
                @ np.linalg.pinv(total_covariance, hermitian=True)
                @ tangent_error
            )
            effective_rows = float(information_axis.report["effective_support_rows"])
            statistical_trace = float(np.trace(statistical_covariance))
            support_information_sensitivity.append({
                "block_count": int(block_count),
                "block_rows": int(settings["hinge_axis"]["selection_block_rows"]),
                "registered_effective_support_threshold_rows": float(support_floor),
                "observed_effective_support_rows": effective_rows,
                "selected_observed_rows": int(
                    information_axis.report["input_rows_after_selection_before_cap"]
                ),
                "owner_update_eligible": bool(
                    information_axis.report["owner_update_eligible"]
                ),
                "parent_axis_sensor": information_axis.parent_axis_sensor.tolist(),
                "child_axis_sensor": information_axis.child_axis_sensor.tolist(),
                "parent_axis_truth_error_deg": _axis_error_deg(
                    information_axis.parent_axis_sensor, sample.parent_axis_sensor,
                ),
                "child_axis_truth_error_deg": _axis_error_deg(
                    information_axis.child_axis_sensor, sample.child_axis_sensor,
                ),
                "total_tangent_covariance_rad2": total_covariance.tolist(),
                "statistical_tangent_covariance_rad2": statistical_covariance.tolist(),
                "statistical_covariance_trace_rad2": statistical_trace,
                "effective_support_times_statistical_covariance_trace": (
                    effective_rows * statistical_trace
                ),
                "truth_tangent_error": tangent_error.tolist(),
                "truth_mahalanobis_squared_total_covariance": truth_mahalanobis_squared,
                "total_covariance_finite": bool(np.all(np.isfinite(total_covariance))),
                "statistical_covariance_finite": bool(
                    np.all(np.isfinite(statistical_covariance))
                ),
                "total_covariance_symmetric": bool(np.allclose(
                    total_covariance, total_covariance.T,
                    atol=float(settings["synthetic"]["qualification_thresholds"][
                        "axis_covariance_symmetry_tolerance_rad2"
                    ]),
                    rtol=0.0,
                )),
                "statistical_covariance_symmetric": bool(np.allclose(
                    statistical_covariance, statistical_covariance.T,
                    atol=float(settings["synthetic"]["qualification_thresholds"][
                        "axis_covariance_symmetry_tolerance_rad2"
                    ]),
                    rtol=0.0,
                )),
                "total_covariance_minimum_eigenvalue_rad2": float(
                    np.min(np.linalg.eigvalsh(total_covariance))
                ),
                "statistical_covariance_minimum_eigenvalue_rad2": float(
                    np.min(np.linalg.eigvalsh(statistical_covariance))
                ),
                "gap_safe_source_block_half_open": information_pair.provenance[
                    "source_block_half_open"
                ],
                "rows_crossing_gap_or_boot_transition": int(
                    information_axis.report["rows_crossing_gap_or_boot_transition"]
                ),
            })
        highest_support = support_information_sensitivity[-1]
        for row in support_information_sensitivity:
            row["parent_axis_delta_from_highest_support_deg"] = _axis_error_deg(
                np.asarray(row["parent_axis_sensor"]),
                np.asarray(highest_support["parent_axis_sensor"]),
            )
            row["child_axis_delta_from_highest_support_deg"] = _axis_error_deg(
                np.asarray(row["child_axis_sensor"]),
                np.asarray(highest_support["child_axis_sensor"]),
            )
        normalized_traces = np.asarray([
            row["effective_support_times_statistical_covariance_trace"]
            for row in support_information_sensitivity
        ])
        normalized_trace_ratio = float(
            np.max(normalized_traces)
            / max(float(np.min(normalized_traces)), np.finfo(float).eps)
        )
        statistical_traces = [
            row["statistical_covariance_trace_rad2"]
            for row in support_information_sensitivity
        ]
        support_information_sensitivity_pass = bool(
            all(
                row["owner_update_eligible"]
                and row["total_covariance_finite"]
                and row["statistical_covariance_finite"]
                and row["total_covariance_symmetric"]
                and row["statistical_covariance_symmetric"]
                and row["total_covariance_minimum_eigenvalue_rad2"]
                >= float(settings["synthetic"]["qualification_thresholds"][
                    "axis_covariance_minimum_eigenvalue_rad2"
                ])
                and row["statistical_covariance_minimum_eigenvalue_rad2"]
                >= float(settings["synthetic"]["qualification_thresholds"][
                    "axis_covariance_minimum_eigenvalue_rad2"
                ])
                and max(
                    row["parent_axis_truth_error_deg"],
                    row["child_axis_truth_error_deg"],
                ) <= float(settings["synthetic"]["qualification_thresholds"][
                    "axis_support_information_maximum_truth_error_deg"
                ])
                and row["truth_mahalanobis_squared_total_covariance"]
                <= float(settings["synthetic"]["qualification_thresholds"][
                    "axis_support_information_maximum_truth_mahalanobis_squared"
                ])
                and max(
                    row["parent_axis_delta_from_highest_support_deg"],
                    row["child_axis_delta_from_highest_support_deg"],
                ) <= float(settings["synthetic"]["qualification_thresholds"][
                    "axis_support_information_maximum_axis_delta_deg"
                ])
                and row["rows_crossing_gap_or_boot_transition"] == 0
                for row in support_information_sensitivity
            )
            and normalized_trace_ratio
            <= float(settings["synthetic"]["qualification_thresholds"][
                "axis_support_information_maximum_normalized_covariance_ratio"
            ])
            and all(
                later <= earlier * float(settings["synthetic"]["qualification_thresholds"][
                    "axis_support_information_maximum_covariance_increase_factor"
                ])
                for earlier, later in zip(
                    statistical_traces[:-1], statistical_traces[1:], strict=True,
                )
            )
        )
        center_truth = np.r_[sample.joint_to_parent_sensor_m, sample.joint_to_child_sensor_m]
        center_value = np.r_[center.joint_to_parent_sensor_m, center.joint_to_child_sensor_m]
        sigma = np.sqrt(np.maximum(np.diag(center.covariance_m2), 0.0))
        covered = np.abs(center_value - center_truth) <= 2.0 * sigma
        coordinate_covered.extend(covered.tolist())
        covariance_symmetric = bool(np.allclose(
            center.covariance_m2, center.covariance_m2.T,
            atol=float(settings["synthetic"]["qualification_thresholds"]["covariance_symmetry_tolerance"]),
        ))
        covariance_psd = bool(np.min(np.linalg.eigvalsh(center.covariance_m2)) >= float(
            settings["synthetic"]["qualification_thresholds"]["covariance_minimum_eigenvalue_m2"]
        ))
        positives.append({
            "case_id": f"POSITIVE_{case_index:02d}",
            "coverage_class": "EXECUTED_OWNER_LEVEL",
            "owner_call_path": quality["owner_call_path"],
            "scenario": dict(scenario),
            "seed": int(seed),
            "parent_axis_error_deg": _axis_error_deg(axis.parent_axis_sensor, sample.parent_axis_sensor),
            "child_axis_error_deg": _axis_error_deg(axis.child_axis_sensor, sample.child_axis_sensor),
            "parent_center_error_m": float(np.linalg.norm(center.joint_to_parent_sensor_m - sample.joint_to_parent_sensor_m)),
            "child_center_error_m": float(np.linalg.norm(center.joint_to_child_sensor_m - sample.joint_to_child_sensor_m)),
            "center_rank": int(center.report["gauge_reduced_rank"]),
            "center_condition_number": float(center.report["condition_number"]),
            "center_residual_rms_mps2": float(center.report["residual_rms_mps2"]),
            "axis_spread_deg": float(axis.report["max_sign_invariant_multistart_spread_deg"]),
            "axis_owner_update_eligible": bool(axis.report["owner_update_eligible"]),
            "axis_threshold_neighborhood_eligibility": threshold_neighborhood_eligibility,
            "axis_threshold_neighborhood_eligibility_pass": (
                threshold_neighborhood_eligibility_pass
            ),
            "axis_support_information_sensitivity": support_information_sensitivity,
            "axis_support_information_normalized_covariance_ratio": normalized_trace_ratio,
            "axis_support_information_sensitivity_pass": (
                support_information_sensitivity_pass
            ),
            "center_owner_update_eligible": bool(center.report["owner_update_eligible"]),
            "center_robust_f_scale_sensitivity": center_robust_f_scale_sensitivity,
            "center_robust_f_scale_sensitivity_pass": bool(
                center_robust_f_scale_sensitivity["pass"]
            ),
            "axis_tangent_covariance_shape": list(axis.tangent_covariance_rad2.shape),
            "axis_clock_lag_derivative_audit": dict(axis.report["clock_lag_derivative_audit"]),
            "center_covariance_shape": list(center.covariance_m2.shape),
            "center_covariance_units": center.report["covariance_units"],
            "center_covariance_symmetric": covariance_symmetric,
            "center_covariance_psd": covariance_psd,
            "center_coordinate_2sigma_coverage": covered.tolist(),
            "gap_safe_center_rows": center.report["rows_differentiated_or_capped_across_gap"] == 0,
            "gap_safe_axis_rows": axis.report["rows_crossing_gap_or_boot_transition"] == 0,
            "nonidealities": dict(sample.nonideality_report),
            "observation_quality": quality,
            "execution_guard_audit": guard.audit(),
        })

    initial_still_pair = _slice_pair(
        positive_pairs[0], 0, len(positive_pairs[0].parent_acc),
        action="00_initial_still",
    )
    initial_still_axis = estimate_hinge_axis_qmt(
        "knee_left", [initial_still_pair], settings=settings["hinge_axis"],
        parent_acc_covariance=acc_covariance,
        child_acc_covariance=acc_covariance,
        parent_gyro_covariance=gyro_covariance,
        child_gyro_covariance=gyro_covariance,
        parent_gyro_bias_covariance=gyro_bias_covariance,
        child_gyro_bias_covariance=gyro_bias_covariance,
        execution_guard=_new_guard(settings),
    )
    underdimensioned_settings = dict(settings["hinge_axis"])
    underdimensioned_settings["minimum_effective_support_rows"] = 2.7
    try:
        estimate_hinge_axis_qmt(
            "knee_left", [positive_pairs[0]], settings=underdimensioned_settings,
            parent_acc_covariance=acc_covariance,
            child_acc_covariance=acc_covariance,
            parent_gyro_covariance=gyro_covariance,
            child_gyro_covariance=gyro_covariance,
            parent_gyro_bias_covariance=gyro_bias_covariance,
            child_gyro_bias_covariance=gyro_bias_covariance,
            execution_guard=_new_guard(settings),
        )
        underdimensioned_support_caught = False
        underdimensioned_support_observed = None
    except ValueError as exc:
        underdimensioned_support_caught = "effective-support floor" in str(exc)
        underdimensioned_support_observed = str(exc)

    order_source = positive_pairs[0]
    third = len(order_source.parent_acc) // 3
    order_blocks = [
        _slice_pair(order_source, index * third, (index + 1) * third, action=f"ORDER_BLOCK_{index}")
        for index in range(3)
    ]
    order_permutations = ((0, 1, 2), (2, 0, 1), (1, 2, 0))
    order_results = []
    for permutation in order_permutations:
        guard = _new_guard(settings)
        ordered_pairs = [order_blocks[index] for index in permutation]
        center = estimate_joint_center_pair_local(
            "knee_left", "thigh_left", "shank_left", ordered_pairs,
            settings=settings["joint_center"], parent_acc_covariance=acc_covariance,
            child_acc_covariance=acc_covariance,
            parent_gyro_observation_covariance=gyro_covariance,
            child_gyro_observation_covariance=gyro_covariance,
            parent_gyro_bias_covariance=gyro_bias_covariance,
            child_gyro_bias_covariance=gyro_bias_covariance,
            execution_guard=guard,
        )
        axis = estimate_hinge_axis_qmt(
            "knee_left", ordered_pairs, settings=settings["hinge_axis"],
            parent_acc_covariance=acc_covariance, child_acc_covariance=acc_covariance,
            parent_gyro_covariance=gyro_covariance, child_gyro_covariance=gyro_covariance,
            parent_gyro_bias_covariance=gyro_bias_covariance,
            child_gyro_bias_covariance=gyro_bias_covariance,
            execution_guard=guard,
        )
        order_results.append({
            "permutation": list(permutation),
            "center": np.r_[center.joint_to_parent_sensor_m, center.joint_to_child_sensor_m],
            "parent_axis": axis.parent_axis_sensor,
            "child_axis": axis.child_axis_sensor,
            "center_residual_rms_mps2": center.report["residual_rms_mps2"],
            "axis_cost": axis.report["selected_cost"],
        })
    reference_order = order_results[0]
    order_sensitivity = {
        "coverage_class": "EXECUTED_OWNER_LEVEL",
        "owner_call_path": [
            "functional_geometry.estimate_joint_center_pair_local",
            "functional_geometry.estimate_hinge_axis_qmt",
        ],
        "world": "SAME_SYNTHETIC_WORLD_SPLIT_INTO_THREE_EPISODES",
        "result_independent_permutations": [row["permutation"] for row in order_results],
        "maximum_center_difference_m": float(max(
            np.linalg.norm(row["center"] - reference_order["center"]) for row in order_results
        )),
        "maximum_parent_axis_difference_deg": float(max(
            _axis_error_deg(row["parent_axis"], reference_order["parent_axis"]) for row in order_results
        )),
        "maximum_child_axis_difference_deg": float(max(
            _axis_error_deg(row["child_axis"], reference_order["child_axis"]) for row in order_results
        )),
        "observations": order_results,
    }
    order_sensitivity["pass"] = bool(
        order_sensitivity["maximum_center_difference_m"]
        <= float(settings["synthetic"]["qualification_thresholds"]["order_sensitivity_center_m"])
        and max(
            order_sensitivity["maximum_parent_axis_difference_deg"],
            order_sensitivity["maximum_child_axis_difference_deg"],
        ) <= float(settings["synthetic"]["qualification_thresholds"]["order_sensitivity_axis_deg"])
    )

    degenerate_fixture = fixtures["degenerate_prefix"]
    degenerate_sample = generate_physical_pair(
        int(degenerate_fixture["seed"]), model=generator_model,
        duration_s=float(degenerate_fixture["duration_s"]), sample_period_s=dt,
        excitation_scale=float(degenerate_fixture["excitation_scale"]),
        nonideal=bool(degenerate_fixture["nonideal"]),
        imperfect_rest_return=bool(degenerate_fixture["imperfect_rest_return"]),
    )
    prefix_pair = _pair_with_gap_pattern(
        degenerate_sample, dt, seed=int(degenerate_fixture["seed"]),
        minimum_gap_rows=int(degenerate_fixture["random_gap_min_rows"]),
        maximum_gap_rows_inclusive=int(
            degenerate_fixture["random_gap_max_rows_inclusive"]
        ),
    )
    prefix_guard = _new_guard(settings)
    prefix_center = estimate_joint_center_pair_local(
        "knee_left", "thigh_left", "shank_left", [prefix_pair],
        settings=settings["joint_center"], parent_acc_covariance=acc_covariance,
        child_acc_covariance=acc_covariance,
        parent_gyro_observation_covariance=gyro_covariance,
        child_gyro_observation_covariance=gyro_covariance,
        parent_gyro_bias_covariance=gyro_bias_covariance,
        child_gyro_bias_covariance=gyro_bias_covariance,
        execution_guard=prefix_guard,
    )
    try:
        prefix_axis = estimate_hinge_axis_qmt(
            "knee_left", [prefix_pair], settings=settings["hinge_axis"],
            parent_acc_covariance=acc_covariance, child_acc_covariance=acc_covariance,
            parent_gyro_covariance=gyro_covariance, child_gyro_covariance=gyro_covariance,
            parent_gyro_bias_covariance=gyro_bias_covariance,
            child_gyro_bias_covariance=gyro_bias_covariance,
            execution_guard=prefix_guard,
        )
        prefix_axis_covariance_trace = float(np.trace(prefix_axis.tangent_covariance_rad2))
        prefix_axis_effective_support = float(prefix_axis.report["effective_support_rows"])
        prefix_axis_outcome: Mapping[str, Any] = dict(prefix_axis.report)
    except (ValueError, RuntimeError, np.linalg.LinAlgError, FloatingPointError) as exc:
        prefix_axis_covariance_trace = None
        prefix_axis_effective_support = 0.0
        prefix_axis_outcome = {"qualified_low_information_exception": f"{type(exc).__name__}:{exc}"}
    full_guard = _new_guard(settings)
    full_center = estimate_joint_center_pair_local(
        "knee_left", "thigh_left", "shank_left", [positive_pairs[1]],
        settings=settings["joint_center"], parent_acc_covariance=acc_covariance,
        child_acc_covariance=acc_covariance,
        parent_gyro_observation_covariance=gyro_covariance,
        child_gyro_observation_covariance=gyro_covariance,
        parent_gyro_bias_covariance=gyro_bias_covariance,
        child_gyro_bias_covariance=gyro_bias_covariance,
        execution_guard=full_guard,
    )
    full_axis = estimate_hinge_axis_qmt(
        "knee_left", [positive_pairs[1]], settings=settings["hinge_axis"],
        parent_acc_covariance=acc_covariance, child_acc_covariance=acc_covariance,
        parent_gyro_covariance=gyro_covariance, child_gyro_covariance=gyro_covariance,
        parent_gyro_bias_covariance=gyro_bias_covariance,
        child_gyro_bias_covariance=gyro_bias_covariance,
        execution_guard=full_guard,
    )
    prefix_rank = int(prefix_center.report["gauge_reduced_rank"])
    prefix_data_information = np.asarray(
        prefix_center.report["gauge_reduced_robust_bread_information_m2_inv"], dtype=float,
    )
    full_data_information = np.asarray(
        full_center.report["gauge_reduced_robust_bread_information_m2_inv"], dtype=float,
    )
    progressive_guard = _new_guard(settings)
    progressive_state = ProgressiveCalibrationState(
        6, execution_guard=progressive_guard,
        initial_sigma=float(settings["progressive"]["initial_sigma"]),
        branch_count=int(settings["progressive"]["synthetic_branch_count"]),
        chronological_actions=settings["execution_contract"]["chronological_actions"],
        rank_relative_tolerance=float(settings["progressive"]["rank_relative_tolerance"]),
        fresh_absolute_tolerance=float(settings["progressive"]["fresh_absolute_tolerance"]),
        fresh_relative_tolerance=float(settings["progressive"]["fresh_relative_tolerance"]),
    )
    prefix_snapshot = progressive_state.ingest_episode(
        chronological_index=0, action=settings["execution_contract"]["chronological_actions"][0],
        observation=np.r_[prefix_center.joint_to_parent_sensor_m, prefix_center.joint_to_child_sensor_m],
        observation_covariance=prefix_center.covariance_m2,
        data_information=prefix_data_information,
        branch_log_likelihood=[0.0, 0.0], physical_validity=0.25,
    )
    full_snapshot = progressive_state.ingest_episode(
        chronological_index=1, action=settings["execution_contract"]["chronological_actions"][1],
        observation=np.r_[full_center.joint_to_parent_sensor_m, full_center.joint_to_child_sensor_m],
        observation_covariance=full_center.covariance_m2,
        data_information=full_data_information,
        branch_log_likelihood=[0.0, 0.0], physical_validity=1.0,
    )
    fresh_prefix_recompute = progressive_state.fresh_recompute_and_compare()
    progressive_audit = progressive_state.audit()
    degenerate_prefix = {
        "coverage_class": "EXECUTED_OWNER_LEVEL",
        "owner_call_path": [
            "functional_geometry.estimate_joint_center_pair_local",
            "functional_geometry.estimate_hinge_axis_qmt",
            "progressive.ProgressiveCalibrationState.ingest_episode",
        ],
        "prefix_rows": len(prefix_pair.parent_acc),
        "full_rows": len(positive_pairs[1].parent_acc),
        "prefix_center_covariance_trace_m2": float(np.trace(prefix_center.covariance_m2)),
        "full_center_covariance_trace_m2": float(np.trace(full_center.covariance_m2)),
        "prefix_axis_covariance_trace_rad2": prefix_axis_covariance_trace,
        "full_axis_covariance_trace_rad2": float(np.trace(full_axis.tangent_covariance_rad2)),
        "prefix_effective_support_rows": prefix_axis_effective_support,
        "full_effective_support_rows": float(full_axis.report["effective_support_rows"]),
        "prefix_axis_outcome": prefix_axis_outcome,
        "prefix_progressive_uncertainty_trace": prefix_snapshot.uncertainty_trace,
        "full_progressive_uncertainty_trace": full_snapshot.uncertainty_trace,
        "prefix_prediction_nll_scored_before_ingest": prefix_snapshot.prediction_nll_before_ingest,
        "full_prediction_nll_scored_before_ingest": full_snapshot.prediction_nll_before_ingest,
        "progressive_audit": progressive_audit,
        "fresh_prefix_recompute": fresh_prefix_recompute,
        "early_false_completion": any(
            row.callable == "C2ExecutionGuard.claim_initial_still_completion"
            and bool(row.detail.get("complete", False))
            for row in progressive_guard.events
        ),
    }
    degenerate_prefix["pass"] = bool(
        degenerate_prefix["full_effective_support_rows"] > degenerate_prefix["prefix_effective_support_rows"]
        and degenerate_prefix["full_progressive_uncertainty_trace"] < degenerate_prefix["prefix_progressive_uncertainty_trace"]
        and progressive_audit["prediction_scored_before_each_ingest"]
        and progressive_audit["prefix_refits"] == 0
        and prefix_snapshot.data_information_rank < full_snapshot.data_information_rank
        and prefix_snapshot.data_information_rank == prefix_rank
        and fresh_prefix_recompute["pass"]
        and not degenerate_prefix["early_false_completion"]
    )

    timing_cases = []
    maximum_injected = max(abs(int(value)) for value in settings["synthetic"]["timing_lag_samples"])
    timing_fixture = fixtures["timing"]
    n = int(timing_fixture["signal_rows"])
    source_time = np.arange(n + 2 * maximum_injected + 20) * dt
    source = np.column_stack((
        np.sin(0.7 * source_time) + 0.3 * np.sin(2.1 * source_time),
        np.cos(1.1 * source_time + 0.2),
        0.5 * np.sin(1.7 * source_time - 0.4),
    ))
    origin = maximum_injected + 5
    for lag in settings["synthetic"]["timing_lag_samples"]:
        lag = int(lag)
        parent_signal = source[origin:origin + n]
        child_signal = source[origin - lag:origin - lag + n]
        aligned = align_pair_by_gyro_energy(parent_signal, child_signal, **_timing_kwargs(settings["timing"]))
        expected = -lag
        timing_cases.append({
            "oracle": "NONCYCLIC_TWO_SLICES_FROM_LONGER_ANALYTIC_SIGNAL",
            "injected_child_delay_samples": lag,
            "expected_selected_lag_samples": expected,
            "observed_selected_lag_samples": int(aligned.lag_samples),
            "absolute_error_samples": abs(int(aligned.lag_samples) - expected),
            "status": aligned.report["status"],
        })

    low_fixture = fixtures["low_information"]
    low = generate_physical_pair(
        int(low_fixture["seed"]), model=generator_model, sample_period_s=dt,
        duration_s=float(low_fixture["duration_s"]),
        excitation_scale=float(low_fixture["excitation_scale"]),
        nonideal=bool(low_fixture["nonideal"]),
        imperfect_rest_return=bool(low_fixture["imperfect_rest_return"]),
    )
    low_pair = _pair_with_gap_pattern(
        low, dt, seed=int(low_fixture["seed"]),
        minimum_gap_rows=int(low_fixture["random_gap_min_rows"]),
        maximum_gap_rows_inclusive=int(low_fixture["random_gap_max_rows_inclusive"]),
    )
    low_guard = _new_guard(settings)
    low_center = estimate_joint_center_pair_local(
        "knee_left", "thigh_left", "shank_left", [low_pair],
        settings=settings["joint_center"],
        parent_acc_covariance=acc_covariance,
        child_acc_covariance=acc_covariance,
        parent_gyro_observation_covariance=gyro_covariance,
        child_gyro_observation_covariance=gyro_covariance,
        parent_gyro_bias_covariance=gyro_bias_covariance,
        child_gyro_bias_covariance=gyro_bias_covariance,
        execution_guard=low_guard,
    )
    try:
        low_axis = estimate_hinge_axis_qmt(
            "knee_left", [low_pair], settings=settings["hinge_axis"],
            parent_acc_covariance=acc_covariance,
            child_acc_covariance=acc_covariance,
            parent_gyro_covariance=gyro_covariance,
            child_gyro_covariance=gyro_covariance,
            parent_gyro_bias_covariance=gyro_bias_covariance,
            child_gyro_bias_covariance=gyro_bias_covariance,
            execution_guard=low_guard,
        )
        low_axis_report: Mapping[str, Any] = low_axis.report
    except (ValueError, np.linalg.LinAlgError, FloatingPointError) as exc:
        low_axis_report = {"qualified_exception": f"{type(exc).__name__}:{exc}"}
    wrong_fixture = fixtures["wrong_mapping"]
    strong = generate_physical_pair(
        int(wrong_fixture["parent_seed"]), model=generator_model,
        duration_s=float(wrong_fixture["duration_s"]), sample_period_s=dt,
        excitation_scale=float(wrong_fixture["excitation_scale"]),
        nonideal=bool(wrong_fixture["nonideal"]),
        imperfect_rest_return=bool(wrong_fixture["imperfect_rest_return"]),
    )
    other = generate_physical_pair(
        int(wrong_fixture["child_seed"]), model=generator_model,
        duration_s=float(wrong_fixture["duration_s"]), sample_period_s=dt,
        excitation_scale=float(wrong_fixture["excitation_scale"]),
        nonideal=bool(wrong_fixture["nonideal"]),
        imperfect_rest_return=bool(wrong_fixture["imperfect_rest_return"]),
    )
    wrong_pair = AlignedPair(
        edge="knee_left", action="WRONG_MAPPING",
        parent_acc=strong.parent_acc, child_acc=other.child_acc,
        parent_gyro=strong.parent_gyro, child_gyro=other.child_gyro,
        parent_observed_time_s=strong.time_s,
        child_observed_time_s=other.time_s,
        parent_boot_epoch=np.zeros(len(strong.time_s), dtype=np.int64),
        child_boot_epoch=np.zeros(len(other.time_s), dtype=np.int64),
        alignment=_identity_alignment(min(len(strong.time_s), len(other.time_s)), dt),
        contiguous_spans=(slice(0, min(len(strong.time_s), len(other.time_s))),),
        provenance=_synthetic_pair_provenance(
            action="WRONG_MAPPING", seed="WRONG_MAPPING",
            fixture="MANDATORY_WRONG_NODE_MAPPING_NEGATIVE",
            extra={"parent_seed": int(wrong_fixture["parent_seed"]),
                   "child_seed": int(wrong_fixture["child_seed"])},
        ),
    )
    wrong_guard = _new_guard(settings)
    wrong_center = estimate_joint_center_pair_local(
        "knee_left", "thigh_left", "shank_left", [wrong_pair],
        settings=settings["joint_center"],
        parent_acc_covariance=acc_covariance,
        child_acc_covariance=acc_covariance,
        parent_gyro_observation_covariance=gyro_covariance,
        child_gyro_observation_covariance=gyro_covariance,
        parent_gyro_bias_covariance=gyro_bias_covariance,
        child_gyro_bias_covariance=gyro_bias_covariance,
        execution_guard=wrong_guard,
    )
    thresholds = settings["synthetic"]["qualification_thresholds"]
    axis_errors = np.asarray([
        max(row["parent_axis_error_deg"], row["child_axis_error_deg"]) for row in positives
    ])
    center_errors = np.asarray([
        max(row["parent_center_error_m"], row["child_center_error_m"]) for row in positives
    ])
    case_pass = np.asarray([
        max(row["parent_axis_error_deg"], row["child_axis_error_deg"]) <= float(thresholds["axis_error_80pct_deg"])
        and max(row["parent_center_error_m"], row["child_center_error_m"]) <= float(thresholds["center_error_80pct_m"])
        and row["center_rank"] == 6
        and row["center_owner_update_eligible"]
        and row["center_robust_f_scale_sensitivity_pass"]
        and row["axis_owner_update_eligible"]
        and row["axis_threshold_neighborhood_eligibility_pass"]
        and row["axis_support_information_sensitivity_pass"]
        and row["center_covariance_symmetric"]
        and row["center_covariance_psd"]
        and row["gap_safe_center_rows"]
        and row["gap_safe_axis_rows"]
        for row in positives
    ], dtype=bool)
    for row, passed in zip(positives, case_pass, strict=True):
        row["case_pass"] = bool(passed)
    coverage_fraction = float(np.mean(coordinate_covered))
    positive_pass = bool(
        float(np.mean(case_pass)) >= float(thresholds["positive_case_required_fraction"])
        and float(np.quantile(axis_errors, 0.8)) <= float(thresholds["axis_error_80pct_deg"])
        and float(np.quantile(center_errors, 0.8)) <= float(thresholds["center_error_80pct_m"])
        and coverage_fraction >= float(thresholds["coordinate_2sigma_coverage_fraction"])
    )
    timing_pass = all(
        row["absolute_error_samples"] <= int(thresholds["maximum_timing_error_samples"])
        for row in timing_cases
    )
    median_positive_residual = float(np.median([row["center_residual_rms_mps2"] for row in positives]))

    outside_shift = (
        int(round(settings["timing"]["maximum_lag_s"] / dt))
        + int(timing_fixture["outside_support_extra_shift_samples"])
    )
    outside_source = np.column_stack((
        np.sin(0.63 * np.arange(n + 2 * outside_shift) * dt),
        np.cos(1.37 * np.arange(n + 2 * outside_shift) * dt),
        np.sin(2.03 * np.arange(n + 2 * outside_shift) * dt),
    ))
    outside = align_pair_by_gyro_energy(
        outside_source[outside_shift:outside_shift + n],
        outside_source[:n],
        **_timing_kwargs(settings["timing"]),
    )
    time_us = np.arange(1000, 1000 + 12 * 5000, 5000, dtype=np.int64)
    mutated_time = time_us.copy()
    mutated_time[4] = mutated_time[3]
    mutated_time[7:] += 5000
    mutated_time[9] += 700
    quality_mutation = assess_factor_rows(
        mutated_time, np.zeros_like(mutated_time),
        np.zeros((len(mutated_time), 3), dtype=np.int16),
        np.zeros((len(mutated_time), 3), dtype=np.int16),
    )
    clock = PersistentPairClockState(
        maximum_abs_drift_ppm=float(settings["timing"]["maximum_abs_drift_ppm"]),
        jitter_floor_s=float(settings["timing"]["jitter_floor_s"]),
    )
    clock_fixture = fixtures["persistent_clock"]
    drift_reports = []
    for index in range(int(clock_fixture["observation_count"])):
        reference_time_s = float(clock_fixture["reference_spacing_s"]) * index
        drift_reports.append(clock.observe(
            edge="knee_left", action=f"A{index}", chronological_index=index,
            reference_time_s=reference_time_s,
            observed_offset_s=(
                float(clock_fixture["initial_offset_s"])
                + float(clock_fixture["true_drift_ppm"]) * 1e-6 * reference_time_s
                + (-1) ** index * float(clock_fixture["alternating_jitter_s"])
            ),
            observation_sigma_s=float(clock_fixture["observation_sigma_s"]),
        ))
    excessive_clock = PersistentPairClockState(
        maximum_abs_drift_ppm=float(settings["timing"]["maximum_abs_drift_ppm"]),
        jitter_floor_s=float(settings["timing"]["jitter_floor_s"]),
    )
    excessive_fixture = fixtures["excessive_clock"]
    excessive_reports = []
    for index in range(int(excessive_fixture["observation_count"])):
        reference_time_s = float(excessive_fixture["reference_spacing_s"]) * index
        excessive_reports.append(excessive_clock.observe(
            edge="knee_left", action=f"E{index}", chronological_index=index,
            reference_time_s=reference_time_s,
            observed_offset_s=float(excessive_fixture["drift_fraction"]) * reference_time_s,
            observation_sigma_s=float(excessive_fixture["observation_sigma_s"]),
        ))
    order_guard = _new_guard(settings)
    try:
        order_guard.begin_episode(0, settings["execution_contract"]["chronological_actions"][1])
        order_caught = False
        order_observed = None
    except ClassAGuardViolation as exc:
        order_caught = exc.code == "ACTION_ORDER_PERMUTATION"
        order_observed = exc.code
    representative_axis = AxisEstimate(
        edge="knee_left", parent_axis_sensor=np.array([0.0, 1.0, 0.0]),
        child_axis_sensor=np.array([0.0, 1.0, 0.0]),
        tangent_covariance_rad2=np.eye(4), report={},
    )
    branch_guard = _new_guard(settings)
    branch_rows = enumerate_hinge_sign_branches({edge: representative_axis for edge in (
        "elbow_left", "elbow_right", "knee_left", "knee_right",
    )}, execution_guard=branch_guard)
    quaternion_gate = numeric_round_trip_gate()
    orientation_continuation = _orientation_continuation_cases(settings)
    workspace = Path(str(settings["execution_contract"]["canonical_workspace"])).resolve()
    initial_relative = Path(
        str(settings["execution_contract"]["initial_stochastic_state_relative_path"])
    )
    initial_path = (workspace / initial_relative).resolve()
    initial_path.relative_to(workspace)
    initial_stochastic_state = json.loads(initial_path.read_text(encoding="utf-8"))
    sparse_orientation_uncertainty_gate = (
        numeric_sparse_quantile_orientation_uncertainty_gate(
            settings, initial_stochastic_state,
        )
    )
    architecture = run_owner_level_architecture_mutations(
        settings,
        prefit_registry_seal_path=prefit_registry_seal_path,
        initial_stochastic_state=initial_stochastic_state,
    )
    low_information_geometry_gate = numeric_low_information_geometry_owner_gate(
        settings["geometry_progressive"]
    )
    center_gap_eligibility_gate = _numeric_center_gap_eligibility_gate(settings)
    center_physical_time_gate = numeric_center_physical_time_ownership_gate()
    center_full_owner_physical_time_gate = (
        _numeric_center_full_owner_physical_time_gate(settings)
    )
    center_gyro_stochastic_gate = _numeric_center_gyro_stochastic_sensitivity_gate(
        settings
    )
    center_coherent_nuisance_gate = _numeric_center_coherent_nuisance_refit_gate(
        settings=settings,
        pair=positive_pairs[2],
        primary_center=positive_centers[2],
        acc_covariance=(
            acc_covariance * positive_covariance_inflations[2]
        ),
        gyro_observation_covariance=(
            gyro_covariance * positive_covariance_inflations[2]
        ),
        gyro_bias_covariance=gyro_bias_covariance,
    )
    axis_centered_support_gate = numeric_axis_centered_support_transform_gate(
        settings["hinge_axis"]
    )
    axis_calibration_nuisance_gate = _numeric_axis_calibration_nuisance_gate(
        settings=settings,
        pair=positive_pairs[1],
        primary_axis=full_axis,
        acc_covariance=acc_covariance,
        gyro_covariance=gyro_covariance,
        gyro_bias_covariance=gyro_bias_covariance,
    )
    axis_exact_hessian_rejection_gate = (
        _numeric_axis_exact_hessian_rejection_gate(
            settings=settings,
            pair=positive_pairs[1],
            acc_covariance=acc_covariance,
            gyro_covariance=gyro_covariance,
            gyro_bias_covariance=gyro_bias_covariance,
        )
    )
    product_s2_antipodal_gate = numeric_product_s2_antipodal_gate(
        settings["geometry_progressive"]
    )
    full_tree_axes, full_tree_centers = _representative_full_tree_geometry()
    frame_covariance_gate = numeric_frame_covariance_rotation_gate(
        settings["segment_frames"],
        full_tree_axes,
        full_tree_centers,
        execution_guard=_new_guard(settings),
    )
    wear_direction_gate = numeric_wear_direction_owner_gate(
        settings["segment_frames"], execution_guard=_new_guard(settings),
    )
    physical_uncertainty_gate = numeric_physical_candidate_uncertainty_gate(settings)
    post_qmt_runtime_physical_gate = _post_qmt_runtime_physical_owner_gate(
        settings,
        prefit_registry_seal_path=prefit_registry_seal_path,
        initial_stochastic_state=initial_stochastic_state,
        axes=full_tree_axes,
        centers=full_tree_centers,
    )
    mutated_covariance_report = dict(full_center.report)
    mutated_covariance_report["standardized_covariance_multiplied_by_robust_sigma_squared"] = True
    try:
        validate_center_covariance_contract(full_center.covariance_m2, mutated_covariance_report)
        covariance_mutation_caught = False
        covariance_mutation_observed = None
    except ValueError as exc:
        covariance_mutation_caught = str(exc).startswith("COVARIANCE_UNIT_SCALE_MUTATION")
        covariance_mutation_observed = str(exc)
    gap_derivatives_safe = bool(all(
        row["axis_clock_lag_derivative_audit"]["cross_block_derivative_count"] == 0
        and row["axis_clock_lag_derivative_audit"]["parent_to_child_boundary_derivative_count"] == 0
        for row in positives
    ))
    continuation_pass = bool(all(row["pass"] for row in orientation_continuation.values()))
    static_low_information_observed = _static_low_information_owner_evidence(
        low_center.report
    )
    near_axis_observed = _near_axis_owner_evidence(low_axis_report)
    sensor_mutation_cases = {
        "STATIC_LOW_INFORMATION": {
            "coverage_class": "EXECUTED_OWNER_LEVEL",
            "owner_call_path": ["estimate_joint_center_pair_local"],
            "injected": {"excitation_scale": 0.001},
            "expected": (
                "EXPLICIT_CENTER_OWNER_LOCAL_NO_UPDATE_FROM_INFORMATION_MAGNITUDE_"
                "BASIN_AND_PREFIX_BOUNDARY_EVIDENCE_NOT_RANK_OR_CONDITION_ONLY"
            ),
            "observed": static_low_information_observed,
            "pass": bool(
                _static_low_information_owner_no_false_pass(low_center.report)
            ),
        },
        "NEAR_AXIS_ONLY": {
            "coverage_class": "EXECUTED_OWNER_LEVEL", "owner_call_path": ["estimate_hinge_axis_qmt"],
            "injected": {"excitation_scale": 0.001},
            "expected": (
                "EXPLICIT_AXIS_OWNER_LOCAL_NO_UPDATE_WHEN_REQUIRED_OFFICIAL_REFIT_"
                "NUISANCE_PROPAGATION_IS_INCOMPLETE_EVEN_IF_SELECTION_HESSIAN_"
                "SUPPORT_AND_RANK_PASS"
            ),
            "observed": near_axis_observed,
            "pass": bool(_near_axis_owner_no_false_pass(low_axis_report)),
        },
        "UNDERDIMENSIONED_HINGE_EFFECTIVE_SUPPORT_REJECTED": {
            "coverage_class": "EXECUTED_OWNER_LEVEL",
            "owner_call_path": ["functional_geometry.estimate_hinge_axis_qmt"],
            "injected": {
                "minimum_effective_support_rows": 2.7,
                "local_tangent_parameter_dimension": int(
                    settings["hinge_axis"]["local_tangent_parameter_dimension"]
                ),
            },
            "expected": "REJECT_BEFORE_QMT_FIT_AS_BELOW_REGISTERED_DIMENSION_OWNED_SENSITIVITY_BOUND",
            "observed": underdimensioned_support_observed,
            "pass": bool(underdimensioned_support_caught),
        },
        "INITIAL_STILL_HINGE_LOCAL_NO_UPDATE": {
            "coverage_class": "EXECUTED_OWNER_LEVEL",
            "owner_call_path": ["functional_geometry.estimate_hinge_axis_qmt"],
            "injected": {
                "action": "00_initial_still",
                "strong_motion_relabelled_to_prove_stage_rule_not_outcome_rule": True,
            },
            "expected": "OFFICIAL_QMT_DIAGNOSTIC_MAY_RUN_BUT_OWNER_UPDATE_REMAINS_LOCAL_NO_UPDATE",
            "observed": dict(initial_still_axis.report),
            "pass": bool(
                initial_still_axis.report["initial_still_present"]
                and not initial_still_axis.report[
                    "initial_still_functional_hinge_update_allowed"
                ]
                and not initial_still_axis.report["owner_update_eligible"]
                and initial_still_axis.report["owner_update_mode"]
                == "LOCAL_NO_UPDATE_LOW_EXCITATION_OR_RANK_OR_EFFECTIVE_SUPPORT"
            ),
        },
        "CENTER_GAP_DOES_NOT_CREATE_ELIGIBILITY": {
            "coverage_class": "EXECUTED_OWNER_LEVEL",
            "owner_call_path": center_gap_eligibility_gate["owner_call_path"],
            "injected": {
                "gap_free_source_index_half_open": [[0, 5820]],
                "gapped_source_index_half_open": [[0, 3020], [3040, 5860]],
                "equal_complete_center_blocks": 29,
            },
            "expected": "GAP_BOUNDARY_ADDS_ZERO_CENTER_BLOCKS_AND_CANNOT_CREATE_OWNER_ELIGIBILITY",
            "observed": center_gap_eligibility_gate,
            "pass": bool(center_gap_eligibility_gate["pass"]),
        },
        "CENTER_GYRO_STOCHASTIC_UNCERTAINTY_OMISSION": {
            "coverage_class": "EXECUTED_OWNER_LEVEL",
            "owner_call_path": center_gyro_stochastic_gate["owner_call_path"],
            "injected": {
                "gyro_observation_and_bias_covariance_multipliers": (
                    center_gyro_stochastic_gate[
                        "injected_gyro_observation_and_bias_covariance_multipliers"
                    ]
                )
            },
            "expected": (
                "ZEROED_GYRO_COVARIANCE_IS_NOT_EQUIVALENT;ROWWISE_RESIDUAL_SIGMA_"
                "INCREASES;ROBUST_INFORMATION_STRICTLY_DECREASES;SYSTEMATIC_AND_TOTAL_"
                "UNCERTAINTY_DO_NOT_REVERSE_SHRINK;ORDINARY_LOW_INFORMATION_NO_UPDATE_"
                "DOES_NOT_HARD_STOP"
            ),
            "observed": center_gyro_stochastic_gate,
            "pass": bool(center_gyro_stochastic_gate["pass"]),
        },
        "CENTER_ACCELEROMETER_CALIBRATION_NUISANCE_OMISSION": {
            **center_gyro_stochastic_gate[
                "accelerometer_calibration_nuisance_mutation"
            ],
            "expected": (
                "ZERO_ACCELEROMETER_BIAS_DRIFT_AND_INDEPENDENT_SHARED_SCALE_"
                "CROSS_AXIS_CONFIGURATION_IS_REJECTED;AT_ONE_FIXED_OWNER_POINT_"
                "RESIDUAL_SIGMA_INCREASES_INFORMATION_DECREASES_AND_SHARED_TOTAL_"
                "COVARIANCE_DO_NOT_SHRINK;NO_GRAVITY_BIAS_FIT_OR_PER_ACTION_PROFILE"
            ),
        },
        "CENTER_SHARED_CALIBRATION_WHITE_NOISE_ENVELOPE_CONTAMINATION": {
            **center_gyro_stochastic_gate[
                "shared_calibration_white_noise_envelope_contamination_mutation"
            ],
        },
        "CENTER_SIGNED_PAIR_CLOCK_NUISANCE": {
            "coverage_class": "EXECUTED_OWNER_LEVEL",
            "owner_call_path": center_gap_eligibility_gate["owner_call_path"],
            "injected": {
                "forbidden_mutation": (
                    "REPLACE_SIGNED_CHILD_AT_FIXED_PARENT_CORRECTED_RESIDUAL_"
                    "DERIVATIVE_WITH_UNSIGNED_PARENT_CHILD_ACC_NORM_MAGNITUDE"
                ),
                "gap_free_and_one_gap_owner_fixtures": True,
            },
            "expected": (
                "SIGNED_POSITIVE_AND_NEGATIVE_CLOCK_JACOBIAN_ROWS;ZERO_CROSS_"
                "BLOCK_OR_GAP_DERIVATIVES;UNSIGNED_SYSTEMATIC_DIRECTION_FALSE"
            ),
            "observed": {
                "gap_free": center_gap_eligibility_gate["gap_free"],
                "one_gap": center_gap_eligibility_gate["one_gap"],
            },
            "pass": bool(
                center_gap_eligibility_gate["pass"]
                and all(
                    row["pair_clock_offset_convention"]
                    == "POSITIVE_OFFSET_EVALUATES_CHILD_LATER_AT_FIXED_PARENT_PHYSICAL_TIME"
                    and row["systematic_clock_nuisance_jacobian"]
                    == "SIGNED_NEGATIVE_BLOCK_LOCAL_TIME_DERIVATIVE_OF_CORRECTED_CHILD_NORM"
                    and not row[
                        "unsigned_clock_magnitude_used_as_systematic_direction"
                    ]
                    and row["signed_clock_gradient_negative_row_count"] > 0
                    and row["signed_clock_gradient_positive_row_count"] > 0
                    and row[
                        "signed_clock_gradient_cross_block_or_gap_derivative_count"
                    ] == 0
                    for row in (
                        center_gap_eligibility_gate["gap_free"],
                        center_gap_eligibility_gate["one_gap"],
                    )
                )
            ),
        },
        "CENTER_PHYSICAL_TIME_SAME_BOOT_GAP_PRESERVED": {
            "coverage_class": "EXECUTED_OWNER_LEVEL",
            "coverage_detail": "EXECUTED_FULL_ESTIMATOR_OWNER_LEVEL",
            "owner_call_path": center_full_owner_physical_time_gate[
                "owner_call_path"
            ],
            "injected": {
                "equal_retained_row_count": True,
                "different_trustworthy_same_boot_physical_gap": True,
                "cap_and_permutation_applied": True,
            },
            "expected": (
                "KNOWN_SAME_BOOT_GAP_CHANGES_DRIFT_TIME_WHILE_CAP_AND_"
                "PERMUTATION_NEVER_SYNTHESIZE_OR_CHANGE_PHYSICAL_TIME_VALUES"
            ),
            "observed": {
                "helper_transform_gate": center_physical_time_gate,
                "full_estimator_owner_gate": center_full_owner_physical_time_gate,
            },
            "pass": bool(
                center_physical_time_gate["pass"]
                and center_full_owner_physical_time_gate["pass"]
                and center_physical_time_gate[
                    "different_known_same_boot_gap_changes_drift_time"
                ]
                and center_physical_time_gate[
                    "same_boot_cross_pair_boundary_is_trustworthy"
                ]
                and center_physical_time_gate["cap_did_not_synthesize_physical_time"]
                and center_physical_time_gate[
                    "permutation_did_not_change_physical_time_values"
                ]
                and center_full_owner_physical_time_gate["equal_retained_rows"]
                and center_full_owner_physical_time_gate[
                    "known_gap_physical_time_hashes_distinct"
                ]
                and center_full_owner_physical_time_gate[
                    "known_gap_drift_stress_hashes_distinct"
                ]
            ),
        },
        "CENTER_PHYSICAL_TIME_UNKNOWN_EPOCH_LOCAL_NO_UPDATE": {
            "coverage_class": "EXECUTED_OWNER_LEVEL",
            "coverage_detail": (
                "EXECUTED_FULL_ESTIMATOR_AND_RUNTIME_OWNER_LEVEL"
            ),
            "owner_call_path": [
                *center_full_owner_physical_time_gate["owner_call_path"],
                "architecture_guard.run_owner_level_architecture_mutations/"
                "PREQUENTIAL_UNKNOWN_RESET_FLOOR_OMITTED_OR_FABRICATED",
            ],
            "injected": {
                "cross_pair_epoch_transition": True,
                "same_epoch_nonmonotonic_timer_reset": True,
            },
            "expected": (
                "NO_ELAPSED_CONTINUITY_INVENTED_AND_LOCAL_CENTER_FACTOR_NO_UPDATE;"
                "PROGRESSIVE_FLOOR_REMAINS_SEPARATE_RUNTIME_MUTATION"
            ),
            "observed": {
                "helper_transform_gate": center_physical_time_gate,
                "full_estimator_owner_gate": center_full_owner_physical_time_gate,
                "runtime_preingest_floor_mutation": architecture["mutations"][
                    "PREQUENTIAL_UNKNOWN_RESET_FLOOR_OMITTED_OR_FABRICATED"
                ],
            },
            "pass": bool(
                center_physical_time_gate["pass"]
                and center_full_owner_physical_time_gate["pass"]
                and center_physical_time_gate[
                    "unknown_epoch_or_reset_local_no_update"
                ]
                and not center_physical_time_gate[
                    "progressive_unknown_interval_floor_consumption_proven"
                ]
                and not center_full_owner_physical_time_gate["unknown_reset"][
                    "owner_update_eligible"
                ]
                and not center_full_owner_physical_time_gate["unknown_reset"][
                    "elapsed_continuity_invented"
                ]
                and bool(architecture["mutations"][
                    "PREQUENTIAL_UNKNOWN_RESET_FLOOR_OMITTED_OR_FABRICATED"
                ]["caught"])
            ),
        },
        "CENTER_COHERENT_NUISANCE_REFIT_LOCAL_NO_UPDATE": {
            "coverage_class": "EXECUTED_OWNER_LEVEL",
            "owner_call_path": center_coherent_nuisance_gate["owner_call_path"],
            "injected": {
                "registered_components": list(settings["joint_center"][
                    "coherent_nuisance_refit_audit"
                ]["components"]),
                "full_scale_unit_mahalanobis_directions": True,
            },
            "expected": "ANY_FAILED_COMPONENT_OR_DIRECTION_FORCES_LOCAL_NO_UPDATE_AND_CANNOT_PROMOTE",
            "observed": center_coherent_nuisance_gate["fragility_no_promotion"],
            "pass": bool(center_coherent_nuisance_gate[
                "fragility_no_promotion"
            ]["pass"]),
        },
        "CENTER_COHERENT_NUISANCE_ANTITHETIC_MIDPOINT_GATE": {
            "coverage_class": "EXECUTED_OWNER_LEVEL",
            "owner_call_path": center_coherent_nuisance_gate["owner_call_path"],
            "injected": {
                "antithetic_signs": [-1, 1],
                "registered_midpoint_limit_m": float(settings["joint_center"][
                    "coherent_nuisance_refit_audit"
                ]["maximum_antithetic_ensemble_midpoint_shift_m"]),
            },
            "expected": "EVERY_DIRECTION_MIDPOINT_IS_GATED_AND_A_LIMIT_VIOLATION_CANNOT_PASS",
            "observed": center_coherent_nuisance_gate[
                "antithetic_midpoint_gate"
            ],
            "pass": bool(center_coherent_nuisance_gate[
                "antithetic_midpoint_gate"
            ]["pass"]),
        },
        "CENTER_CLOCK_REFIT_SYMMETRIC_INTERIOR_NO_CLAMP": {
            "coverage_class": "EXECUTED_OWNER_LEVEL",
            "owner_call_path": center_coherent_nuisance_gate["owner_call_path"],
            "injected": {
                "both_antithetic_signs_use_one_interior_mask": True,
                "forbidden_endpoint_clamp_or_repeat": True,
                "forbidden_cross_block_or_gap_interpolation": True,
            },
            "expected": "IDENTICAL_RETAINED_SUPPORT_ZERO_CLAMP_ZERO_CROSS_GAP",
            "observed": center_coherent_nuisance_gate[
                "symmetric_clock_interior"
            ],
            "pass": bool(center_coherent_nuisance_gate[
                "symmetric_clock_interior"
            ]["pass"]),
        },
        "CENTER_COHERENT_NUISANCE_ORDINARY_FAILURE_RETAINED": {
            "coverage_class": "EXECUTED_OWNER_LEVEL",
            "owner_call_path": center_coherent_nuisance_gate["owner_call_path"],
            "injected": {
                "ordinary_clock_interpolation_support_exception": (
                    "RuntimeError:INJECTED_ORDINARY_CLOCK_INTERPOLATION_SUPPORT_FAILURE"
                )
            },
            "expected": "ALL_SIGNED_FAILURES_RETAINED_LOCAL_NO_UPDATE_AND_NEXT_COMPONENT_CONTINUES",
            "observed": center_coherent_nuisance_gate[
                "ordinary_construction_failure_retention"
            ],
            "pass": bool(center_coherent_nuisance_gate[
                "ordinary_construction_failure_retention"
            ]["pass"]),
        },
        "CENTER_COHERENT_NUISANCE_UNIT_RADIUS_FULL_SPAN_COVERAGE": {
            "coverage_class": "EXECUTED_OWNER_LEVEL",
            "owner_call_path": center_coherent_nuisance_gate["owner_call_path"],
            "injected": {
                "direction_count": int(settings["joint_center"][
                    "coherent_nuisance_refit_audit"
                ]["direction_count"]),
                "required_whitened_radius": float(settings["joint_center"][
                    "coherent_nuisance_refit_audit"
                ]["required_whitened_radius"]),
            },
            "expected": "EVERY_DIRECTION_UNIT_RADIUS_AND_EACH_COMPONENT_DIMENSION_SPANNED_BEFORE_CYCLE",
            "observed": center_coherent_nuisance_gate["unit_radius_full_span"],
            "pass": bool(center_coherent_nuisance_gate[
                "unit_radius_full_span"
            ]["pass"]),
        },
        "AXIS_CENTERED_SUPPORT_STATIC_BIAS_INVARIANCE": {
            "coverage_class": "EXECUTED_OWNER_LEVEL",
            "owner_call_path": [
                "functional_geometry._axis_centered_selection_metrics",
                "functional_geometry.numeric_axis_centered_support_transform_gate",
            ],
            "injected": {
                "constant_accelerometer_offsets_mps2": [[4.0, -3.0, 12.0], [-2.0, 5.0, -8.0]],
                "constant_gyro_offsets_rads": [[1.2, -0.7, 0.4], [-0.6, 1.1, -0.3]],
            },
            "expected": "CENTERED_SUPPORT_SCORE_EFFECTIVE_ROWS_AND_CALIBRATION_MARGINAL_EXACTLY_INVARIANT",
            "observed": axis_centered_support_gate,
            "pass": bool(axis_centered_support_gate["constant_bias_invariance_pass"]),
        },
        "AXIS_CENTERED_SUPPORT_SCALE_GRAVITY_EXCLUSION": {
            "coverage_class": "EXECUTED_OWNER_LEVEL",
            "owner_call_path": [
                "functional_geometry._axis_centered_selection_metrics",
                "functional_geometry.numeric_axis_centered_support_transform_gate",
            ],
            "injected": {
                "large_removed_gravity_and_mean_offsets": True,
                "centered_accelerometer_and_gyro_signal_amplitude_multiplier": 2.0,
            },
            "expected": "REMOVED_GRAVITY_MEAN_HAS_ZERO_EFFECT_AND_CENTERED_SCALE_VARIANCE_RESPONDS_QUADRATICALLY",
            "observed": axis_centered_support_gate,
            "pass": bool(axis_centered_support_gate["centered_scale_quadratic_response_pass"]),
        },
        "AXIS_GYRO_CALIBRATION_NUISANCE_OMISSION": {
            "coverage_class": "EXECUTED_OWNER_LEVEL",
            "owner_call_path": axis_calibration_nuisance_gate["owner_call_path"],
            "injected": {
                "gyro_calibration_nuisance_multiplier": 0.0,
                "immutable_p1_gyro_bias_covariance_still_bound": True,
            },
            "expected": "ZERO_GYRO_BIAS_DRIFT_SCALE_COMPONENTS_AND_FORCED_LOCAL_NO_UPDATE",
            "observed": axis_calibration_nuisance_gate["gyro_omission"],
            "pass": bool(axis_calibration_nuisance_gate["gyro_omission"]["pass"]),
        },
        "AXIS_ACCELEROMETER_CALIBRATION_NUISANCE_OMISSION": {
            "coverage_class": "EXECUTED_OWNER_LEVEL",
            "owner_call_path": axis_calibration_nuisance_gate["owner_call_path"],
            "injected": {"accelerometer_calibration_nuisance_multiplier": 0.0},
            "expected": "ZERO_ACCELEROMETER_BIAS_DRIFT_SCALE_COMPONENTS_AND_FORCED_LOCAL_NO_UPDATE",
            "observed": axis_calibration_nuisance_gate["accelerometer_omission"],
            "pass": bool(
                axis_calibration_nuisance_gate["accelerometer_omission"]["pass"]
            ),
        },
        "AXIS_COMMON_MODE_NUISANCE_AS_INDEPENDENT_ROW_INFORMATION": {
            "coverage_class": "EXECUTED_OWNER_LEVEL",
            "owner_call_path": axis_calibration_nuisance_gate["owner_call_path"],
            "injected": {
                "forbidden_model": "COHERENT_CALIBRATION_DRAW_COUNTED_AS_PER_ROW_OR_PER_EPISODE_INFORMATION"
            },
            "expected": "ONE_COHERENT_DRAW_ACROSS_ROWS_AND_NONSHRINKING_NAMED_PROGRESSIVE_COMPONENTS",
            "observed": {
                "axis_gate": axis_calibration_nuisance_gate,
                "progressive_gate": low_information_geometry_gate,
            },
            "pass": bool(
                axis_calibration_nuisance_gate[
                    "common_mode_nuisance_not_independent_rows"
                ]
                and low_information_geometry_gate[
                    "axis_systematic_components_preserved_without_episode_shrink"
                ]
                and low_information_geometry_gate[
                    "axis_systematic_total_equals_component_sum"
                ]
            ),
        },
        "AXIS_IMPLICIT_LINEARIZATION_OFFICIAL_REFIT_DISAGREEMENT": {
            "coverage_class": "EXECUTED_OWNER_LEVEL",
            "owner_call_path": axis_calibration_nuisance_gate["owner_call_path"],
            "injected": {
                "registered_nuisance_scales": [0.02, 1.0],
                "mandatory_primary_scale": 1.0,
                "forbidden_mutation": "WRONG_2JTJ_SCALING_OR_SPHERICAL_TO_PRODUCT_S2_TRANSPORT",
            },
            "expected": "EVERY_NAMED_COMPONENT_PRIMARY_SCALE_MATCHES_ACTUAL_OFFICIAL_PLUS_MINUS_REFITS_OR_LOCAL_NO_UPDATE",
            "observed": axis_calibration_nuisance_gate[
                "official_refit_linearization_by_component"
            ],
            "pass": bool(
                axis_calibration_nuisance_gate[
                    "primary_scale_official_refit_agreement"
                ]
            ),
        },
        "AXIS_EXACT_SCORE_HESSIAN_SINGULAR_OR_INDEFINITE": {
            "coverage_class": "EXECUTED_OWNER_LEVEL",
            "owner_call_path": axis_exact_hessian_rejection_gate[
                "owner_call_path"
            ],
            "injected": {
                "exact_score_hessian_eigenvalues": [-1.0, 1.0, 2.0, 3.0],
                "forbidden_mutation": (
                    "CANONICAL_JTJ_RANK_MASKS_A_SINGULAR_OR_INDEFINITE_EXACT_"
                    "SCORE_CHART_AND_PINV_PRODUCES_COVARIANCE"
                ),
            },
            "expected": (
                "OWNER_LOCAL_NO_UPDATE_WITHOUT_INVERSE_OR_PSEUDOINVERSE_OF_"
                "THE_INVALID_EXACT_SCORE_HESSIAN"
            ),
            "observed": axis_exact_hessian_rejection_gate,
            "pass": bool(axis_exact_hessian_rejection_gate["pass"]),
        },
        "WRONG_NODE_MAPPING": {
            "coverage_class": "EXECUTED_OWNER_LEVEL", "owner_call_path": ["estimate_joint_center_pair_local"],
            "injected": dict(wrong_fixture), "expected": "RESIDUAL_INFLATION",
            "observed": {"residual_rms_mps2": wrong_center.report["residual_rms_mps2"], "positive_median": median_positive_residual},
            "pass": bool(
            wrong_center.report["residual_rms_mps2"]
            >= float(thresholds["wrong_mapping_residual_ratio"]) * median_positive_residual
            ),
        },
        "OUTSIDE_CLOCK_SUPPORT_NONCYCLIC": {
            "coverage_class": "EXECUTED_OWNER_LEVEL", "owner_call_path": ["align_pair_by_gyro_energy"],
            "injected": {"noncyclic_shift_samples": outside_shift}, "expected": "BOUNDARY_OR_LOW_INFORMATION",
            "observed": dict(outside.report), "pass": bool(
            outside.report["status"] != "INFORMATIVE_INTERIOR_PEAK"
            or abs(outside.lag_samples) == int(round(settings["timing"]["maximum_lag_s"] / dt))
            ),
        },
        "GAP_BRIDGE": {
            "coverage_class": "EXECUTED_OWNER_LEVEL",
            "owner_call_path": ["assess_factor_rows", "estimate_joint_center_pair_local", "_blockwise_angular_acceleration_rms", "estimate_hinge_axis_qmt"],
            "injected": {"positive_case_gap_rows": [row["scenario"]["gap_rows"] for row in positives]},
            "expected": "ZERO_CROSS_GAP_BLOCK_OR_PARENT_CHILD_DERIVATIVES",
            "observed": {"derivative_audits": [row["axis_clock_lag_derivative_audit"] for row in positives]},
            "pass": bool(gap_derivatives_safe and all(row["gap_safe_center_rows"] and row["gap_safe_axis_rows"] for row in positives)),
        },
        "DUPLICATE_TIMESTAMP": {
            "coverage_class": "EXECUTED_OWNER_LEVEL", "owner_call_path": ["assess_factor_rows", "ContinuousVQFState.process"],
            "injected": {"duplicate_timer_index": 4, "all_duplicate_episode": True},
            "expected": "EXCLUDE_OR_LOCAL_NO_UPDATE_THEN_CONTINUE",
            "observed": {"quality": dict(quality_mutation.report), "continuation": orientation_continuation["ALL_DUPLICATE"]},
            "pass": bool(
            quality_mutation.report["duplicate_or_nonmonotonic_rows_excluded"] >= 1
            and quality_mutation.report["contiguous_span_count"] >= 3
            and orientation_continuation["ALL_DUPLICATE"]["pass"]),
        },
        "CLIPPING": {
            "coverage_class": "EXECUTED_OWNER_LEVEL", "owner_call_path": ["assess_factor_rows", "ContinuousVQFState.process"],
            "injected": {"all_clipped_episode": True, "boot_transition_all_clipped": True},
            "expected": "LOCAL_NO_UPDATE_COVARIANCE_OR_BOOT_FLOOR_THEN_CONTINUE",
            "observed": {"all_clipped": orientation_continuation["ALL_CLIPPED"], "boot": orientation_continuation["BOOT_TRANSITION_UNUSABLE"]},
            "pass": bool(orientation_continuation["ALL_CLIPPED"]["pass"] and orientation_continuation["BOOT_TRANSITION_UNUSABLE"]["pass"]),
        },
        "EXCESSIVE_DRIFT": {
            "coverage_class": "EXECUTED_OWNER_LEVEL", "owner_call_path": ["PersistentPairClockState.observe"],
            "injected": {"drift_fraction": 0.01}, "expected": "BOUND_TO_SEALED_MAXIMUM_WITH_COVARIANCE",
            "observed": excessive_reports[-1], "pass": bool(abs(excessive_reports[-1]["state"]["drift_ppm"]) >= 1999.0),
        },
        "ACTION_ORDER_PERMUTATION": {
            "coverage_class": "EXECUTED_OWNER_LEVEL", "owner_call_path": ["C2ExecutionGuard.begin_episode", "ContinuousVQFState.process"],
            "injected": {"index": 0, "action": settings["execution_contract"]["chronological_actions"][1]},
            "expected": "ACTION_ORDER_PERMUTATION_REJECTION", "observed": order_observed, "pass": bool(order_caught),
        },
        "AXIS_SIGN_FULL_CIRCLE_BRANCH": {
            "coverage_class": "EXECUTED_OWNER_LEVEL", "owner_call_path": ["enumerate_hinge_sign_branches", "numeric_round_trip_gate"],
            "injected": {"sign_ambiguous_hinges": 4, "full_circle_quaternion": True},
            "expected": "RETAIN_ALL_16_BRANCHES_WITH_NORMALIZED_PRIOR",
            "observed": {"branch_count": len(branch_rows), "prior_sum": sum(row["prior_weight"] for row in branch_rows)},
            "pass": bool(
            len(branch_rows) == 16
            and abs(sum(row["prior_weight"] for row in branch_rows) - 1.0) <= 1e-12
            and quaternion_gate["includes_full_circle_case"]
            ),
        },
        "QUATERNION_ACTIVE_PASSIVE_MUTATION": {
            "coverage_class": "EXECUTED_OWNER_LEVEL", "owner_call_path": ["numeric_round_trip_gate", "qmt.rotate", "qmt.qinv"],
            "injected": {"known_active_quarter_turn_and_inverse": True}, "expected": "DIRECT_OFFICIAL_EQUALITY",
            "observed": quaternion_gate, "pass": bool(quaternion_gate["pass"]),
        },
        "COVARIANCE_UNIT_SCALE_MUTATION": {
            "coverage_class": "EXECUTED_OWNER_LEVEL", "owner_call_path": ["estimate_joint_center_pair_local", "validate_center_covariance_contract"],
            "injected": {"standardized_covariance_multiplied_by_robust_sigma_squared": True},
            "expected": "EXPLICIT_UNIT_CONTRACT_REJECTION", "observed": covariance_mutation_observed,
            "pass": bool(covariance_mutation_caught),
        },
        "ZERO_EXCITATION_AXIS_LOCAL_NO_UPDATE": {
            "coverage_class": "EXECUTED_OWNER_LEVEL",
            "owner_call_path": [
                "ProgressiveFunctionalGeometryOwner.ingest_axis",
                "ProgressiveFunctionalGeometryOwner.posterior_axes",
            ],
            "injected": {"first_axis_owner_update_eligible": False},
            "expected": "LOCAL_NO_UPDATE_WITH_NO_FALSE_STATE_THEN_LATER_EPISODE_CONTINUES",
            "observed": low_information_geometry_gate,
            "pass": bool(
                low_information_geometry_gate["first_rejected_axis_returned_none"]
                and low_information_geometry_gate["first_rejected_axis_state_absent"]
                and low_information_geometry_gate["later_informative_axis_episode_continued"]
            ),
        },
        "RANK_DEFICIENT_CENTER_NULLSPACE_NO_SHRINK": {
            "coverage_class": "EXECUTED_OWNER_LEVEL",
            "owner_call_path": [
                "ProgressiveFunctionalGeometryOwner.ingest_center",
                "ProgressiveFunctionalGeometryOwner.posterior_centers",
            ],
            "injected": {"center_informed_rank": 3, "state_dimension": 6},
            "expected": "ONLY_INFORMED_SUBSPACE_UPDATES_AND_NULLSPACE_RETAINS_BROAD_PRIOR",
            "observed": low_information_geometry_gate,
            "pass": bool(
                low_information_geometry_gate["partial_center_nullspace_not_falsely_shrunk"]
                and low_information_geometry_gate["rejected_rank_positive_center_state_absent"]
            ),
        },
        "FRAME_COVARIANCE_KNOWN_ROTATION": {
            "coverage_class": "EXECUTED_OWNER_LEVEL",
            "owner_call_path": [
                "SegmentFrameBranchOwner.build",
                "numeric_frame_covariance_rotation_gate",
            ],
            "injected": {"known_distinct_sensor_coordinate_rotation_per_segment": True},
            "expected": "FRAME_EQUIVARIANCE_AND_FULL_30D_COVARIANCE_INVARIANCE",
            "observed": frame_covariance_gate,
            "pass": bool(frame_covariance_gate["pass"]),
        },
        "PRODUCT_S2_ANTIPODAL_NO_ZERO_INNOVATION": {
            "coverage_class": "EXECUTED_OWNER_LEVEL",
            "owner_call_path": [
                "ProgressiveFunctionalGeometryOwner.ingest_axis",
                "geometry_posterior._s2_log",
            ],
            "injected": {"one_endpoint_exact_antipode": True},
            "expected": "UNCERTAINTY_INFLATING_LOCAL_NO_UPDATE_NEVER_ZERO_INNOVATION",
            "observed": product_s2_antipodal_gate,
            "pass": bool(product_s2_antipodal_gate["pass"]),
        },
        "WEAR_DIRECTION_SENSITIVITY": {
            "coverage_class": "EXECUTED_OWNER_LEVEL",
            "owner_call_path": [
                "SegmentFrameBranchOwner.evaluate_wear_mount_distribution",
                "segment_frames._validated_wear_authority",
            ],
            "injected": {
                "primary_mounts": True,
                "near_uninformative_hemisphere": True,
                "gross_wrong_hemisphere": True,
                "swapped_hardware_identity": True,
            },
            "expected": "BROAD_SOFT_SENSITIVITY_WITH_ONLY_UNCERTAINTY_QUALIFIED_GROSS_REJECTION",
            "observed": wear_direction_gate,
            "pass": bool(wear_direction_gate["pass"]),
        },
        "MARGINAL_CROSSING_UNCERTAINTY_COVERED": {
            "coverage_class": "EXECUTED_OWNER_LEVEL",
            "owner_call_path": ["ScientificForwardKinematicsOwner.assess_prefix_trajectory"],
            "injected": {"marginal_crossing_with_large_connection_uncertainty": True},
            "expected": "RETAIN_WITH_SOFT_NEGATIVE_LIKELIHOOD",
            "observed": physical_uncertainty_gate["marginal_uncertainty_covered_crossing"],
            "pass": bool(physical_uncertainty_gate["marginal_uncertainty_covered_crossing"]["pass"]),
        },
        "GROSS_SINGLE_PAIR_SUSTAINED_CROSSING": {
            "coverage_class": "EXECUTED_OWNER_LEVEL",
            "owner_call_path": ["ScientificForwardKinematicsOwner.assess_prefix_trajectory"],
            "injected": {"upper_arm_pair_only_sustained_gross_crossing": True},
            "expected": "HARD_REJECT_WITHOUT_DILUTION_BY_OTHER_BILATERAL_PAIRS",
            "observed": physical_uncertainty_gate["gross_sustained_single_pair_crossing"],
            "pass": bool(physical_uncertainty_gate["gross_sustained_single_pair_crossing"]["pass"]),
        },
        "LIMB_GRAVITY_HEMISPHERE_DIAGNOSTIC": {
            "coverage_class": "EXECUTED_OWNER_LEVEL",
            "owner_call_path": ["ScientificForwardKinematicsOwner.assess_prefix_trajectory"],
            "injected": {"limb_long_axis_opposite_world_hemisphere": True},
            "expected": "DIAGNOSTIC_ONLY_NOT_HARD_REJECTED",
            "observed": physical_uncertainty_gate["limb_opposite_gravity_hemisphere_is_diagnostic"],
            "pass": bool(physical_uncertainty_gate["limb_opposite_gravity_hemisphere_is_diagnostic"]["pass"]),
        },
        "AXIAL_GRAVITY_UNCERTAINTY_COVERED": {
            "coverage_class": "EXECUTED_OWNER_LEVEL",
            "owner_call_path": ["ScientificForwardKinematicsOwner.assess_prefix_trajectory"],
            "injected": {"axial_opposite_mean_with_35deg_orientation_sigma": True},
            "expected": "NO_HARD_REJECTION_WHEN_UNCERTAINTY_COVERS_CONTRADICTION",
            "observed": physical_uncertainty_gate["axial_gravity_uncertainty_covered"],
            "pass": bool(physical_uncertainty_gate["axial_gravity_uncertainty_covered"]["pass"]),
        },
        "AXIAL_GRAVITY_SUSTAINED_GROSS_CAUGHT": {
            "coverage_class": "EXECUTED_OWNER_LEVEL",
            "owner_call_path": ["ScientificForwardKinematicsOwner.assess_prefix_trajectory"],
            "injected": {"axial_sustained_opposite_hemisphere_low_uncertainty": True},
            "expected": "UNCERTAINTY_QUALIFIED_SUSTAINED_GROSS_REJECTION",
            "observed": physical_uncertainty_gate["axial_sustained_gravity_contradiction"],
            "pass": bool(physical_uncertainty_gate["axial_sustained_gravity_contradiction"]["pass"]),
        },
        "SPARSE_QUANTILE_ORIENTATION_UNCERTAINTY_COLLAPSE": {
            "coverage_class": "EXECUTED_OWNER_LEVEL",
            "owner_call_path": sparse_orientation_uncertainty_gate["owner_call_path"],
            "injected": {
                "full_sequence_selected_source_indices": sparse_orientation_uncertainty_gate[
                    "full_sequence_selected_source_indices"
                ],
                "heldout_local_source_indices": sparse_orientation_uncertainty_gate[
                    "heldout_local_source_indices"
                ],
                "forbidden_model": "THREE_SPARSE_ROWS_TREATED_AS_THREE_ADJACENT_5MS_SAMPLES",
            },
            "expected": (
                "FULL_RETAINED_SEQUENCE_CUMULATIVE_DURATION_AND_EXACT_SHARED_"
                "TRAINING_HELDOUT_OWNER_EQUIVALENCE"
            ),
            "observed": sparse_orientation_uncertainty_gate,
            "pass": bool(sparse_orientation_uncertainty_gate["pass"]),
        },
    }
    sensor_mutations = {name: bool(row["pass"]) for name, row in sensor_mutation_cases.items()}
    architecture_caught = {
        name: bool(row["caught"]) for name, row in architecture["mutations"].items()
    }
    expected_sensor = set(settings["synthetic"]["mandatory_sensor_and_numerical_mutations"])
    expected_architecture = set(settings["synthetic"]["mandatory_architecture_negative_mutations"])
    mutation_pass = bool(
        set(sensor_mutations) == expected_sensor
        and set(architecture_caught) == expected_architecture
        and all(sensor_mutations.values())
        and all(architecture_caught.values())
        and architecture["pass"]
        and continuation_pass
    )
    successful = [row for row in positives if row["case_pass"]]
    def evidence(rows: Sequence[Mapping[str, Any]], predicate: Any) -> list[dict[str, Any]]:
        return [
            {
                "case_id": row["case_id"], "coverage_class": row["coverage_class"],
                "owner_call_path": row["owner_call_path"],
                "predicate_observed": True,
            }
            for row in rows if predicate(row)
        ]

    dimension_execution: dict[str, list[dict[str, Any]]] = {
        "FULL_SO3_MOUNTS": evidence(successful, lambda row: row["nonidealities"]["wear_mode"] == "haar"),
        "NEAR_UNINFORMATIVE_WEAR": evidence(
            successful,
            lambda row: row["nonidealities"]["wear_mode"] == "near_uninformative_hemisphere"
            and abs(row["nonidealities"]["near_uninformative_wear_dot_nominal"]) < 0.02,
        ),
        "HUMAN_DIMENSIONS_ASYMMETRY": evidence(
            successful,
            lambda row: 0.39 <= row["nonidealities"]["child_dimension_m"] <= 0.47
            and 0.44 <= row["nonidealities"]["parent_dimension_m"] <= 0.52
            and abs(row["nonidealities"]["asymmetry_fraction"]) > 0.0
            and row["nonidealities"]["parent_joint_to_sensor_norm_m"] > 0.0
            and row["nonidealities"]["child_joint_to_sensor_norm_m"] > 0.0,
        ),
        "IMPERFECT_MOTION_REST": evidence(
            successful,
            lambda row: row["nonidealities"]["imperfect_rest_return"]
            and row["nonidealities"]["start_excitation_envelope"] > 0.0
            and row["nonidealities"]["final_excitation_envelope"] > 0.0,
        ),
        "SOFT_TISSUE_SLOW_SLIP": evidence(
            successful,
            lambda row: row["nonidealities"]["strap_slip_peak_deg"] > 0.0
            and row["nonidealities"]["observation_level"] > 0.0,
        ),
        "AXIS_CENTER_MIGRATION_NONIDEAL_HINGE": evidence(
            successful,
            lambda row: row["nonidealities"]["axis_migration_peak_deg"] > 0.0
            and row["nonidealities"]["center_migration_peak_m"] > 0.0
            and row["nonidealities"]["nonideal_hinge"],
        ),
        "BIAS_SCALE_CROSS_AXIS_NOISE_QUANTIZATION_CORRELATION": evidence(
            successful,
            lambda row: row["nonidealities"]["bias_drift"]
            and row["nonidealities"]["quantized_to_jy61p_lattice"]
            and row["nonidealities"]["ar1_correlation"] >= 0.0,
        ),
        "JITTER_CLIPPING_DROP_DUPLICATE_GAP": evidence(
            successful,
            lambda row: (
                row["scenario"]["jitter_us"] != 0
                and row["scenario"]["clipping_rows"] > 0
                and row["scenario"]["gap_rows"] > 0
                and row["scenario"]["duplicate_rows"] > 0
                and row["observation_quality"]["parent_quality"]["clipped_rows_excluded"] > 0
                and row["observation_quality"]["parent_quality"]["duplicate_or_nonmonotonic_rows_excluded"] > 0
                and row["observation_quality"]["parent_quality"]["gap_jitter_or_boot_boundaries"] > 0
                and row["observation_quality"]["joint_covariance_inflation"] > 1.0
            ),
        ),
        "TIME_VARYING_YAW": evidence(
            successful, lambda row: row["nonidealities"]["time_varying_yaw_motion_consumed"],
        ),
        "INTER_EPISODE_NO_UPDATE": [],
        "EPISODE_ORDER_PERMUTATIONS": [],
        "DEGENERATE_PREFIX": [],
        "FULL_CIRCLE_MULTIBRANCH": [],
        "POST_QMT_PHYSICAL_UNCERTAINTY_SENSITIVITY": [],
    }
    special_positive_cases = {
        "INTER_EPISODE_NO_UPDATE": orientation_continuation["VALID_INTER_EPISODE_GAP"],
        "EPISODE_ORDER_PERMUTATIONS": order_sensitivity,
        "DEGENERATE_PREFIX": degenerate_prefix,
        "FULL_CIRCLE_MULTIBRANCH": {
            "coverage_class": "EXECUTED_OWNER_LEVEL",
            "owner_call_path": ["enumerate_hinge_sign_branches", "numeric_round_trip_gate"],
            "pass": bool(sensor_mutation_cases["AXIS_SIGN_FULL_CIRCLE_BRANCH"]["pass"]),
        },
        "POST_QMT_PHYSICAL_UNCERTAINTY_SENSITIVITY": {
            "coverage_class": "EXECUTED_OWNER_LEVEL",
            "owner_call_path": post_qmt_runtime_physical_gate["owner_call_path"],
            "pass": bool(post_qmt_runtime_physical_gate["pass"]),
        },
    }
    for name, row in special_positive_cases.items():
        dimension_execution[name].append({
            "case_id": name,
            "coverage_class": row["coverage_class"],
            "owner_call_path": row["owner_call_path"],
            "pass": row["pass"], "predicate_observed": bool(row["pass"]),
        })
    registered_dimensions = set(settings["synthetic"]["positive_distribution_dimensions"])
    implemented_dimensions = set(dimension_execution)
    declarative_only = sorted(
        name for name, rows in dimension_execution.items()
        if not rows or not all(row.get("predicate_observed", False) for row in rows)
    )
    declarative_only.extend(sorted(registered_dimensions - implemented_dimensions))
    positive_distribution_execution_pass = bool(
        not declarative_only
        and registered_dimensions == implemented_dimensions
        and order_sensitivity["pass"]
        and degenerate_prefix["pass"]
        and orientation_continuation["VALID_INTER_EPISODE_GAP"]["pass"]
    )
    positive_distribution_audit = {
        "coverage_rule": "ONLY_EXECUTED_OWNER_LEVEL_CASES_COUNT;GENERATED_BUT_UNCONSUMED_OR_TEXT_ONLY_IS_FAIL",
        "dimension_execution": dimension_execution,
        "declarative_only_or_unconsumed": declarative_only,
        "executed_scenario_count": len(positives),
        "order_sensitivity": order_sensitivity,
        "degenerate_prefix": degenerate_prefix,
        "orientation_continuation": orientation_continuation,
        "pass": positive_distribution_execution_pass,
    }
    return {
        "schema": "biospur-c2-p2-independent-synthetic-qualification-v2",
        "oracle": "WORLD_FRAME_RIGID_BODY_GENERATOR_WITH_ARBITRARY_SENSOR_MOUNTS;NO_ESTIMATOR_OBJECTIVE_REUSE",
        "positive_distribution_dimensions": list(settings["synthetic"]["positive_distribution_dimensions"]),
        "registered_synthetic_design": {
            "positive_seeds": list(synthetic_settings["positive_seeds"]),
            "positive_scenarios": [dict(row) for row in scenario_templates],
            "generator_model": dict(generator_model),
            "estimator_input_noise": dict(estimator_noise),
            "mutation_fixtures": {
                name: dict(value) for name, value in fixtures.items()
            },
        },
        "positive_distribution_audit": positive_distribution_audit,
        "positive_cases": positives,
        "timing_cases": timing_cases,
        "persistent_clock_positive": {
            "final_recovered_drift_ppm": drift_reports[-1]["state"]["drift_ppm"],
            "true_drift_ppm": float(clock_fixture["true_drift_ppm"]),
            "observation_count": drift_reports[-1]["observation_count"],
        },
        "sensor_and_numerical_mutations": sensor_mutations,
        "sensor_and_numerical_mutation_ledger": sensor_mutation_cases,
        "architecture_mutations": architecture,
        "quaternion_round_trip": quaternion_gate,
        "post_qmt_runtime_physical_owner_gate": post_qmt_runtime_physical_gate,
        "summary": {
            "positive_case_pass_fraction": float(np.mean(case_pass)),
            "axis_error_80pct_deg": float(np.quantile(axis_errors, 0.8)),
            "center_error_80pct_m": float(np.quantile(center_errors, 0.8)),
            "center_coordinate_2sigma_coverage_fraction": coverage_fraction,
            "median_positive_center_residual_rms_mps2": median_positive_residual,
            "low_excitation_center_report": dict(low_center.report),
            "low_excitation_axis_report": dict(low_axis_report),
            "wrong_mapping_center_report": dict(wrong_center.report),
            "outside_support_clock_report": outside.report,
            "factor_row_quality_for_duplicate_gap_jitter_mutation": dict(quality_mutation.report),
        },
        "qualification_thresholds": dict(thresholds),
        "positive_pass": bool(positive_pass and positive_distribution_execution_pass),
        "timing_pass": bool(timing_pass),
        "mutation_pass": mutation_pass,
        "pass": bool(positive_pass and positive_distribution_execution_pass and timing_pass and mutation_pass),
        "real_capture_rows_opened": False,
        "median_only_acceptance": False,
    }
