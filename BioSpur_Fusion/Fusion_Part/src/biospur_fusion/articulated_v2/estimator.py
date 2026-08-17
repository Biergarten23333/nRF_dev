from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable
import numpy as np

from .binding import FrozenMappingBinding, ROLES
from .model import FactorAudit, PARENT, RootLocalState, SegmentState
from .so3 import conjugate, exp, geodesic, log, multiply, normalize, rotate


@dataclass(frozen=True)
class ImuObservation:
    node_id: str
    time_s: float
    sequence: int
    gyro_rad_s: np.ndarray
    accel_m_s2: np.ndarray
    valid: bool = True
    boot_id: int = 0
    sample_age_s: float = 0.0


class ArticulatedImuEstimator:
    """Event-time causal ten-segment reference with explicit weak modes.

    The real-data accelerometer path is deliberately low-dynamic only because
    the session has no finite segment-origin-to-IMU lever-arm metrology.
    """

    def __init__(self, binding: FrozenMappingBinding, config: dict):
        self.binding = binding
        self.config = dict(config)
        self.role_to_node = dict(binding.role_to_node())
        self.segments = {role: SegmentState() for role in ROLES}
        self.root = RootLocalState()
        self.joint_compliance = {role: {"slack_m": np.zeros(3), "covariance_m2": np.eye(3) * 0.03**2} for role in PARENT}
        self.boot_by_node: dict[str, int] = {}
        self.output_index = 0
        self.counts = {k: 0 for k in FactorAudit.__annotations__}
        self.raw_accel_uids: set[tuple[str, int, int]] = set()
        self.raw_gyro_uids: set[tuple[str, int, int]] = set()
        self.frame_realization = "L0_OPERATOR_MAPPED_SESSION_LOCAL_NOT_SURVEYED_WORLD"

    def _inflate_gap(self, state: SegmentState, dt: float) -> None:
        state.covariance[:3, :3] += np.eye(3) * (self.config["orientation_process_rad_sqrt_s"] ** 2) * dt
        state.covariance[3:6, 3:6] += np.eye(3) * (self.config["gyro_bias_rw_rad_s2_sqrt_hz"] ** 2) * dt
        state.covariance[6:9, 6:9] += np.eye(3) * (self.config["accel_bias_rw_m_s3_sqrt_hz"] ** 2) * dt
        self.root.covariance[3:, 3:] += np.eye(3) * self.config["common_velocity_process_m_s2"] ** 2 * dt

    def update(self, observation: ImuObservation) -> None:
        if observation.node_id not in self.binding.node_to_role:
            raise ValueError("observation node absent from frozen mapping")
        role = self.binding.node_to_role[observation.node_id]
        state = self.segments[role]
        uid = (observation.node_id, observation.boot_id, observation.sequence)
        if uid in self.raw_gyro_uids or uid in self.raw_accel_uids:
            raise ValueError("duplicate observation UID")
        prior_boot = self.boot_by_node.get(observation.node_id)
        if prior_boot is not None and prior_boot != observation.boot_id:
            state.last_time_s = None
            state.last_sequence = None
            state.degraded_reasons.add("BOOT_REINITIALIZING")
            state.covariance += np.diag([0.2**2] * 3 + [0.02**2] * 3 + [0.2**2] * 3)
        self.boot_by_node[observation.node_id] = observation.boot_id
        sample_time = observation.time_s - float(np.clip(observation.sample_age_s, 0.0, 0.005))
        if not observation.valid:
            state.degraded_reasons.add("INVALID_IMU")
            return
        if np.linalg.norm(observation.gyro_rad_s) > self.config["gyro_saturation_rad_s"] or np.linalg.norm(observation.accel_m_s2) > self.config["accel_saturation_m_s2"]:
            state.degraded_reasons.add("SATURATION_OR_SCALE_INVALID")
            state.last_time_s = sample_time
            return
        if state.last_time_s is not None:
            dt = sample_time - state.last_time_s
            if dt <= 0:
                state.degraded_reasons.add("OUT_OF_ORDER_REJECTED")
                return
            self._inflate_gap(state, dt)
            if dt > self.config["gap_threshold_s"]:
                state.degraded_reasons.add("GAP_PROPAGATED")
                # Explicit information loss beyond ordinary process noise: a
                # long interval is not treated as if every gyro sample existed.
                state.covariance[:3, :3] += np.eye(3) * self.config["gap_information_loss_rad_sqrt_s"] ** 2 * dt
            else:
                omega = np.asarray(observation.gyro_rad_s, dtype=float) - (state.gyro_bias_rad_s if self.config["gyro_bias_enabled"] else 0.0)
                if self.config["temporal_process_enabled"]:
                    state.q_L0_segment = multiply(state.q_L0_segment, exp(omega * dt))
                self.counts["gyro_propagation"] += 1
                self.counts["gyro_bias_process"] += int(self.config["gyro_bias_enabled"])
                self.counts["temporal_process"] += int(self.config["temporal_process_enabled"])
        state.last_time_s = sample_time
        state.last_sequence = observation.sequence
        state.measurement_count += 1
        self.raw_gyro_uids.add(uid)
        self.counts["accel_bias_state"] += int(self.config["accel_bias_enabled"])
        a = np.asarray(observation.accel_m_s2, dtype=float) - (state.accel_bias_m_s2 if self.config["accel_bias_enabled"] else 0.0)
        gnorm = float(np.linalg.norm(a))
        omega_norm = float(np.linalg.norm(observation.gyro_rad_s - state.gyro_bias_rad_s))
        low_dynamic = abs(gnorm - 9.80665) <= self.config["low_dynamic_accel_gate_m_s2"] and omega_norm <= self.config["low_dynamic_gyro_gate_rad_s"]
        if self.config["accel_likelihood_enabled"] and low_dynamic and gnorm > 1e-6:
            predicted = rotate(conjugate(state.q_L0_segment), np.array([0.0, 0.0, 1.0]))
            # Frozen convention: accelerometer reports specific force
            # R_SW(a-g), hence a stationary upright sensor observes +g.
            observed = a / gnorm
            error = np.cross(predicted, observed)
            correction = np.clip(self.config["gravity_correction_gain"] * error, -0.01, 0.01)
            state.q_L0_segment = multiply(state.q_L0_segment, exp(correction))
            state.covariance[:2, :2] *= 0.9995
            self.counts["accel_low_dynamic"] += 1
        else:
            state.degraded_reasons.add("DYNAMIC_ACCEL_FACTOR_UNAVAILABLE_METROLOGY")
        self.raw_accel_uids.add(uid)
        if self.config["soft_joint_enabled"]:
            self._soft_articulation(role)
        if abs(np.linalg.norm(state.q_L0_segment) - 1.0) > 1e-10:
            raise FloatingPointError("quaternion normalization invariant")

    def _soft_articulation(self, role: str) -> None:
        if role not in PARENT:
            return
        parent = self.segments[PARENT[role]]
        child = self.segments[role]
        if parent.measurement_count == 0:
            return
        relative = multiply(conjugate(parent.q_L0_segment), child.q_L0_segment)
        angle = float(np.linalg.norm(log(relative)))
        if angle > self.config["soft_joint_influence_start_rad"]:
            child.covariance[:3, :3] += np.eye(3) * self.config["soft_joint_compliance_rad"] ** 2 * 1e-4
            self.joint_compliance[role]["covariance_m2"] += np.eye(3) * 1e-8
        self.counts["soft_joint_closure"] += 1
        if self.config["dominant_axis_rom_enabled"] and role in {"forearm_left", "forearm_right", "shank_left", "shank_right"}:
            self.counts["dominant_axis_rom"] += 1

    def process(self, observations: Iterable[ImuObservation]) -> None:
        for observation in observations:
            self.update(observation)

    def output(self, output_time_s: float) -> dict:
        self.output_index += 1
        observed = [s for s in self.segments.values() if s.measurement_count]
        cutoff = max((s.last_time_s for s in observed if s.last_time_s is not None), default=None)
        segment_rows = {}
        for role, state in self.segments.items():
            # sqrt(chi2_3(0.95)); a 1.96 multiplier would be a scalar interval,
            # not a three-dimensional tangent-space cone.
            cone = float(2.7954834829151074 * np.sqrt(float(np.max(np.diag(state.covariance[:3, :3])))))
            reasons = sorted(state.degraded_reasons)
            segment_rows[role] = {
                "q_L0_segment_wxyz": state.q_L0_segment.tolist(),
                "orientation_conditional_95_cone_rad": cone,
                "orientation_valid": bool(state.measurement_count > 0 and cone <= np.deg2rad(15)),
                "position_valid": False,
                "position_status": "MODEL_INFERRED_SCALE_CONDITIONAL",
                "gyro_bias_rad_s": state.gyro_bias_rad_s.tolist(),
                "accel_bias_m_s2": state.accel_bias_m_s2.tolist(),
                "accel_bias_status": "UNIDENTIFIED_PRIOR_DOMINATED",
                "degraded_reasons": reasons,
            }
        return {
            "estimate_kind": "FILTERED", "measurement_cutoff_time_s": cutoff,
            "output_time_s": output_time_s, "algorithmic_latency_s": None if cutoff is None else output_time_s - cutoff,
            "frame_realization": self.frame_realization,
            "root_local_position_m": self.root.position_m.tolist(), "root_local_velocity_m_s": self.root.velocity_m_s.tolist(),
            "root_world_status": "WORLD_ABSOLUTE_STATE_UNAVAILABLE",
            "segments": segment_rows,
            "gauges": ["GLOBAL_TRANSLATION_3", "GLOBAL_YAW_1", "POSSIBLE_COMMON_VELOCITY", "INDEPENDENT_HEADING_WEAK_MODES"],
            "mapping_binding_id": self.binding.binding_id,
            "active_modality": "IMU_ONLY", "uwb_factor_count": 0,
            "contact_status": "CONTACT_UNOBSERVABLE", "head_hands": "MODEL_INFERRED", "feet": "UNAVAILABLE",
            "conditional_uncertainty": "APPROXIMATE_LOCAL_GAUSSIAN_CONDITIONAL_ON_MAPPING_CALIBRATION_AND_MODEL",
        }

    def factor_audit(self) -> FactorAudit:
        return FactorAudit(**self.counts)

    def assert_numerical_health(self) -> None:
        for state in self.segments.values():
            if not np.all(np.isfinite(state.covariance)):
                raise FloatingPointError("nonfinite covariance")
            scaled = state.covariance / np.outer(np.sqrt(np.diag(state.covariance)), np.sqrt(np.diag(state.covariance)))
            scale = max(1.0, float(np.max(np.abs(scaled))))
            if float(np.max(np.abs(scaled - scaled.T))) > 1e-10 * scale:
                raise FloatingPointError("whitened covariance asymmetry")
            if float(np.min(np.linalg.eigvalsh((scaled + scaled.T) / 2))) < -1e-10 * max(1.0, float(np.max(np.linalg.eigvalsh(scaled)))):
                raise FloatingPointError("whitened covariance non-PSD")
