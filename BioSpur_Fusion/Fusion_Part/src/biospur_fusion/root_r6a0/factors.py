"""Executable factor residuals and Jacobian paths over the shared BodyModel."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import numpy as np

from .body import BodyModel, KeyframeState, StaticCalibration
from .contracts import (
    ActivationState,
    AuthorityScope,
    EvidenceRecord,
    FactorProposal,
    FaultDomain,
    ServiceDOF,
)
from .math3d import central_jacobian, so3_exp, so3_log


def _stamp(time_s: float) -> str:
    return f"{time_s:.9f}"


def _root_blocks(time_s: float) -> tuple[str, str]:
    stamp = _stamp(time_s)
    return f"kf:{stamp}:root_pose", f"kf:{stamp}:root_velocity"


@dataclass(frozen=True)
class GaugePriorFactor:
    model: BodyModel
    target_translation_m: np.ndarray
    target_rotvec: np.ndarray
    sigma_translation_m: float
    sigma_rotation_rad: float
    time_s: float

    @property
    def proposal(self) -> FactorProposal:
        return FactorProposal(
            f"gauge_prior:{_stamp(self.time_s)}", "gauge_prior", ("PRIOR:STATIC_GAUGE",), frozenset(),
            self.time_s, self.time_s, (_root_blocks(self.time_s)[0],), 6,
            "KNOWN_SYNTHETIC_GAUGE_COVARIANCE", (FaultDomain.ANCHOR_MAP_FRAME,),
            ActivationState.ACTIVE_SYNTHETIC, AuthorityScope.PROBE_ONLY,
            (ServiceDOF.GLOBAL_POSITION, ServiceDOF.GLOBAL_YAW),
        )

    def residual(self, state: KeyframeState) -> np.ndarray:
        translation = (np.asarray(state.root_translation_model_m) - np.asarray(self.target_translation_m)) / self.sigma_translation_m
        rotation = (np.asarray(state.root_rotation_model_rotvec) - np.asarray(self.target_rotvec)) / self.sigma_rotation_rad
        return np.concatenate((translation, rotation))

    def jacobian_configuration(self, state: KeyframeState) -> np.ndarray:
        return central_jacobian(
            lambda vector: self.residual(state.with_configuration(vector, self.model.joint_ids)),
            state.configuration_vector(self.model.joint_ids),
        )


@dataclass(frozen=True)
class StatePriorFactor:
    target_velocity_mps: np.ndarray
    sigma_velocity_mps: float
    time_s: float

    @property
    def proposal(self) -> FactorProposal:
        return FactorProposal(
            f"state_prior:{_stamp(self.time_s)}", "state_prior", ("PRIOR:ROOT_VELOCITY",), frozenset(),
            self.time_s, self.time_s, (_root_blocks(self.time_s)[1],), 3,
            "KNOWN_SYNTHETIC_STATE_PRIOR", (FaultDomain.SHARED_SOFTWARE_MODEL,),
            ActivationState.ACTIVE_SYNTHETIC, AuthorityScope.PROBE_ONLY,
            (ServiceDOF.GLOBAL_POSITION,),
        )

    def residual(self, state: KeyframeState) -> np.ndarray:
        return (np.asarray(state.root_velocity_model_mps) - np.asarray(self.target_velocity_mps)) / self.sigma_velocity_mps


@dataclass(frozen=True)
class KinematicConsistencyFactor:
    model: BodyModel
    calibration: StaticCalibration
    time_s: float
    sigma_m: float = 1e-5

    @property
    def proposal(self) -> FactorProposal:
        blocks = [_root_blocks(self.time_s)[0]]
        blocks.extend(f"kf:{_stamp(self.time_s)}:joint:{joint}" for joint in self.model.joint_ids)
        return FactorProposal(
            f"fk_consistency:{_stamp(self.time_s)}", "fk_fixed_bone_consistency",
            (f"STRUCTURE:FK:{_stamp(self.time_s)}",), frozenset(), self.time_s, self.time_s,
            tuple(blocks), 3 * len(self.model.joints), "CALIBRATED_GEOMETRY_UNCERTAINTY",
            (FaultDomain.BODY_GEOMETRY, FaultDomain.SHARED_SOFTWARE_MODEL),
            ActivationState.ACTIVE_SYNTHETIC, AuthorityScope.PROBE_ONLY,
            (ServiceDOF.BODY_RELATIVE_POSE, ServiceDOF.JOINT_ANGLES),
        )

    def residual(self, state: KeyframeState) -> np.ndarray:
        return self.model.kinematic_residuals(state, self.calibration) / self.sigma_m

    def jacobian_configuration(self, state: KeyframeState) -> np.ndarray:
        return central_jacobian(
            lambda vector: self.residual(state.with_configuration(vector, self.model.joint_ids)),
            state.configuration_vector(self.model.joint_ids),
        )


@dataclass(frozen=True)
class SoftJointFeasibilityFactor:
    model: BodyModel
    joint_id: str
    time_s: float
    sigma_rad: np.ndarray

    @property
    def proposal(self) -> FactorProposal:
        block = f"kf:{_stamp(self.time_s)}:joint:{self.joint_id}"
        return FactorProposal(
            f"soft_joint:{self.joint_id}:{_stamp(self.time_s)}", "soft_joint_feasibility",
            (f"ANATOMY:SOFT:{self.joint_id}",), frozenset(), self.time_s, self.time_s,
            (block,), 3, "SOFT_ANATOMY_SANDBOX_PRIOR_NOT_CLINICAL_TRUTH",
            (FaultDomain.BODY_GEOMETRY, FaultDomain.SHARED_SOFTWARE_MODEL),
            ActivationState.ACTIVE_SYNTHETIC, AuthorityScope.PROBE_ONLY,
            (ServiceDOF.JOINT_ANGLES,),
        )

    def residual(self, state: KeyframeState) -> np.ndarray:
        return np.asarray(state.joint_rotvec[self.joint_id], float) / np.asarray(self.sigma_rad, float)

    def jacobian_configuration(self, state: KeyframeState) -> np.ndarray:
        return central_jacobian(
            lambda vector: self.residual(state.with_configuration(vector, self.model.joint_ids)),
            state.configuration_vector(self.model.joint_ids),
        )


@dataclass(frozen=True)
class RawUwbRangeFactor:
    model: BodyModel
    calibration: StaticCalibration
    evidence: EvidenceRecord
    tag_id: str
    anchor_id: int
    measured_range_m: float
    sigma_m: float
    activation_state: ActivationState

    @property
    def proposal(self) -> FactorProposal:
        blocks = list(self.model.dependency_blocks("tag", self.tag_id, self.evidence.measurement_time_s))
        blocks.extend((f"calibration:anchor_position:{self.anchor_id}", f"calibration:anchor_delay:{self.anchor_id}"))
        authority = AuthorityScope.LOCAL_SEGMENT_ONLY if self.activation_state is ActivationState.SHADOW_ONLY else AuthorityScope.PROBE_ONLY
        return FactorProposal(
            f"raw_range:{self.evidence.event_uid}", "raw_uwb_range_true_event_time",
            (self.evidence.physical_event_uid,), self.evidence.raw_ancestry,
            self.evidence.measurement_time_s, self.evidence.availability_time_s, tuple(blocks), 1,
            self.evidence.covariance_provenance,
            (FaultDomain.SINGLE_EVENT, FaultDomain.TAG_ANCHOR_LINK, FaultDomain.TAG,
             FaultDomain.ANCHOR, FaultDomain.CLOCK_TIMING, FaultDomain.ANCHOR_MAP_FRAME),
            self.activation_state, authority,
            (ServiceDOF.GLOBAL_POSITION, ServiceDOF.GLOBAL_YAW, ServiceDOF.BODY_RELATIVE_POSE),
        )

    def predicted(self, state_at: Callable[[float], KeyframeState]) -> float:
        # Binding invariant: query physical measurement time, never arrival time.
        state = state_at(self.evidence.measurement_time_s)
        tag = self.model.tag_phase_centres(state, self.calibration)[self.tag_id]
        anchor_def = next(anchor for anchor in self.model.anchors if anchor.anchor_id == self.anchor_id)
        anchor = self.calibration.vector(anchor_def.position_slot, 3)
        delay = float(self.calibration.vector(anchor_def.delay_slot, 1)[0])
        return float(np.linalg.norm(anchor - tag) + delay)

    def residual(self, state_at: Callable[[float], KeyframeState]) -> np.ndarray:
        return np.asarray([(self.predicted(state_at) - self.measured_range_m) / self.sigma_m])

    def residual_state(self, state: KeyframeState) -> np.ndarray:
        if abs(state.time_s - self.evidence.measurement_time_s) > 1e-12:
            raise ValueError("single-state range evaluation must match exact measurement time")
        return self.residual(lambda _: state)

    def jacobian_configuration(self, state: KeyframeState) -> np.ndarray:
        return central_jacobian(
            lambda vector: self.residual_state(state.with_configuration(vector, self.model.joint_ids)),
            state.configuration_vector(self.model.joint_ids),
        )

    def phase_centre_jacobian(self, state: KeyframeState) -> np.ndarray:
        tag = self.model.tag_phase_centres(state, self.calibration)[self.tag_id]
        anchor_def = next(anchor for anchor in self.model.anchors if anchor.anchor_id == self.anchor_id)
        anchor = self.calibration.vector(anchor_def.position_slot, 3)
        delta = tag - anchor
        return (delta / np.linalg.norm(delta) / self.sigma_m)[None, :]


@dataclass(frozen=True)
class ImuOrientationFactor:
    model: BodyModel
    calibration: StaticCalibration
    evidence: EvidenceRecord
    imu_id: str
    measured_rotation_world_from_imu: np.ndarray
    sigma_rad: float
    activation_state: ActivationState

    @property
    def proposal(self) -> FactorProposal:
        blocks = self.model.dependency_blocks("imu", self.imu_id, self.evidence.measurement_time_s)
        return FactorProposal(
            f"imu_orientation:{self.evidence.event_uid}", "imu_orientation_through_fk_sensor_state",
            (self.evidence.physical_event_uid,), self.evidence.raw_ancestry,
            self.evidence.measurement_time_s, self.evidence.availability_time_s,
            blocks, 3, self.evidence.covariance_provenance,
            (FaultDomain.SINGLE_EVENT, FaultDomain.IMU_NODE, FaultDomain.CLOCK_TIMING,
             FaultDomain.BODY_GEOMETRY, FaultDomain.SHARED_SOFTWARE_MODEL),
            self.activation_state, AuthorityScope.INCREMENT_ONLY,
            (ServiceDOF.BODY_RELATIVE_POSE, ServiceDOF.JOINT_ANGLES, ServiceDOF.GLOBAL_YAW),
        )

    def residual_state(self, state: KeyframeState) -> np.ndarray:
        if abs(state.time_s - self.evidence.measurement_time_s) > 1e-12:
            raise ValueError("single-state IMU evaluation must match exact measurement time")
        predicted = self.model.imu_frames(state, self.calibration)[self.imu_id].rotation
        return so3_log(np.asarray(self.measured_rotation_world_from_imu).T @ predicted) / self.sigma_rad

    def jacobian_configuration(self, state: KeyframeState) -> np.ndarray:
        return central_jacobian(
            lambda vector: self.residual_state(state.with_configuration(vector, self.model.joint_ids)),
            state.configuration_vector(self.model.joint_ids),
        )


@dataclass(frozen=True)
class ImuPropagationFactor:
    model: BodyModel
    calibration: StaticCalibration
    imu_id: str
    previous_time_s: float
    current_time_s: float
    raw_delta_rotation: np.ndarray
    sigma_rad: float

    @property
    def proposal(self) -> FactorProposal:
        blocks = list(self.model.dependency_blocks("imu", self.imu_id, self.previous_time_s))
        blocks.extend(self.model.dependency_blocks("imu", self.imu_id, self.current_time_s))
        blocks.append(f"kf:{_stamp(self.previous_time_s)}:gyro_bias:{self.imu_id}")
        blocks.append(f"kf:{_stamp(self.current_time_s)}:gyro_bias:{self.imu_id}")
        return FactorProposal(
            f"imu_propagation:{self.imu_id}:{_stamp(self.previous_time_s)}:{_stamp(self.current_time_s)}",
            "imu_propagation_through_fk_sensor_states", (f"IMU_INTERVAL:{self.imu_id}:{_stamp(self.previous_time_s)}",),
            frozenset({f"IMU_INTERVAL:{self.imu_id}:{_stamp(self.previous_time_s)}"}),
            self.current_time_s, self.current_time_s, tuple(dict.fromkeys(blocks)), 3,
            "SYNTHETIC_GYRO_INTERVAL_COVARIANCE", (FaultDomain.IMU_NODE, FaultDomain.CLOCK_TIMING,
            FaultDomain.BODY_GEOMETRY), ActivationState.ACTIVE_SYNTHETIC, AuthorityScope.INCREMENT_ONLY,
            (ServiceDOF.BODY_RELATIVE_POSE, ServiceDOF.JOINT_ANGLES, ServiceDOF.GLOBAL_YAW),
        )

    def residual(self, previous: KeyframeState, current: KeyframeState) -> np.ndarray:
        dt = self.current_time_s - self.previous_time_s
        if dt <= 0:
            raise ValueError("IMU propagation interval must be positive")
        previous_rotation = self.model.imu_frames(previous, self.calibration)[self.imu_id].rotation
        current_rotation = self.model.imu_frames(current, self.calibration)[self.imu_id].rotation
        relative = previous_rotation.T @ current_rotation
        average_bias = 0.5 * (
            np.asarray(previous.gyro_bias_rad_s[self.imu_id]) + np.asarray(current.gyro_bias_rad_s[self.imu_id])
        )
        corrected = np.asarray(self.raw_delta_rotation) @ so3_exp(-average_bias * dt)
        return so3_log(corrected.T @ relative) / self.sigma_rad


@dataclass(frozen=True)
class RootTranslationPropagationFactor:
    imu_id: str
    previous_time_s: float
    current_time_s: float
    measured_acceleration_model_mps2: np.ndarray
    sigma_position_m: float
    sigma_velocity_mps: float

    @property
    def proposal(self) -> FactorProposal:
        first = _root_blocks(self.previous_time_s)
        second = _root_blocks(self.current_time_s)
        return FactorProposal(
            f"root_propagation:{self.imu_id}:{_stamp(self.previous_time_s)}:{_stamp(self.current_time_s)}",
            "imu_root_translation_propagation", (f"IMU_ACCEL_INTERVAL:{self.imu_id}:{_stamp(self.previous_time_s)}",),
            frozenset({f"IMU_ACCEL_INTERVAL:{self.imu_id}:{_stamp(self.previous_time_s)}"}),
            self.current_time_s, self.current_time_s, first + second + (
                f"kf:{_stamp(self.previous_time_s)}:accel_bias:{self.imu_id}",
                f"kf:{_stamp(self.current_time_s)}:accel_bias:{self.imu_id}",
            ), 6, "SYNTHETIC_ACCELERATION_INTERVAL_COVARIANCE",
            (FaultDomain.IMU_NODE, FaultDomain.CLOCK_TIMING), ActivationState.ACTIVE_SYNTHETIC,
            AuthorityScope.INCREMENT_ONLY, (ServiceDOF.GLOBAL_POSITION,),
        )

    def residual(self, previous: KeyframeState, current: KeyframeState) -> np.ndarray:
        dt = self.current_time_s - self.previous_time_s
        acceleration = np.asarray(self.measured_acceleration_model_mps2, float)
        predicted_position = (np.asarray(previous.root_translation_model_m)
                              + np.asarray(previous.root_velocity_model_mps) * dt
                              + 0.5 * acceleration * dt * dt)
        predicted_velocity = np.asarray(previous.root_velocity_model_mps) + acceleration * dt
        return np.concatenate((
            (np.asarray(current.root_translation_model_m) - predicted_position) / self.sigma_position_m,
            (np.asarray(current.root_velocity_model_mps) - predicted_velocity) / self.sigma_velocity_mps,
        ))


@dataclass(frozen=True)
class BiasEvolutionFactor:
    imu_id: str
    bias_kind: str
    previous_time_s: float
    current_time_s: float
    sigma_random_walk: float

    @property
    def proposal(self) -> FactorProposal:
        if self.bias_kind not in ("gyro", "accel"):
            raise ValueError("bias kind must be gyro or accel")
        role = "gyro_bias" if self.bias_kind == "gyro" else "accel_bias"
        blocks = (
            f"kf:{_stamp(self.previous_time_s)}:{role}:{self.imu_id}",
            f"kf:{_stamp(self.current_time_s)}:{role}:{self.imu_id}",
        )
        return FactorProposal(
            f"bias_evolution:{self.bias_kind}:{self.imu_id}:{_stamp(self.previous_time_s)}",
            f"independent_{self.bias_kind}_bias_evolution",
            (f"PROCESS:{self.bias_kind}:{self.imu_id}:{_stamp(self.previous_time_s)}",), frozenset(),
            self.current_time_s, self.current_time_s, blocks, 3,
            "SYNTHETIC_INDEPENDENT_BIAS_RANDOM_WALK", (FaultDomain.IMU_NODE,),
            ActivationState.ACTIVE_SYNTHETIC, AuthorityScope.INCREMENT_ONLY,
            (ServiceDOF.BODY_RELATIVE_POSE, ServiceDOF.JOINT_ANGLES),
        )

    def residual(self, previous: KeyframeState, current: KeyframeState) -> np.ndarray:
        if self.bias_kind == "gyro":
            first, second = previous.gyro_bias_rad_s[self.imu_id], current.gyro_bias_rad_s[self.imu_id]
        elif self.bias_kind == "accel":
            first, second = previous.accel_bias_mps2[self.imu_id], current.accel_bias_mps2[self.imu_id]
        else:
            raise ValueError("bias kind must be gyro or accel")
        return (np.asarray(second) - np.asarray(first)) / self.sigma_random_walk


def raw_range_value(model: BodyModel, calibration: StaticCalibration, state: KeyframeState,
                    tag_id: str, anchor_id: int) -> float:
    tag = model.tag_phase_centres(state, calibration)[tag_id]
    anchor_def = next(anchor for anchor in model.anchors if anchor.anchor_id == anchor_id)
    anchor = calibration.vector(anchor_def.position_slot, 3)
    delay = float(calibration.vector(anchor_def.delay_slot, 1)[0])
    return float(np.linalg.norm(anchor - tag) + delay)
