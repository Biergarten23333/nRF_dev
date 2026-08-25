"""Native-time IMU preintegration with explicit failure boundaries.

The preintegrator consumes consecutive accepted samples on each B306 common
clock.  It does not resample, assume a nominal rate, align different nodes, or
inject gravity.  Accelerometer inputs are specific force in the sensor frame;
gravity belongs to the downstream navigation/body factor.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Mapping, Sequence

import numpy as np

G = 9.80665


def skew(vector: np.ndarray) -> np.ndarray:
    """Return the cross-product matrix for a finite three-vector."""
    x, y, z = np.asarray(vector, dtype=float)
    return np.array([[0.0, -z, y], [z, 0.0, -x], [-y, x, 0.0]], dtype=float)


def so3_exp(rotvec: np.ndarray) -> np.ndarray:
    """Exponential map with stable small-angle coefficients."""
    value = np.asarray(rotvec, dtype=float)
    theta = float(np.linalg.norm(value))
    cross = skew(value)
    if theta < 1e-8:
        a = 1.0 - theta * theta / 6.0 + theta**4 / 120.0
        b = 0.5 - theta * theta / 24.0 + theta**4 / 720.0
    else:
        a = np.sin(theta) / theta
        b = (1.0 - np.cos(theta)) / (theta * theta)
    return np.eye(3) + a * cross + b * (cross @ cross)


def so3_log(rotation: np.ndarray) -> np.ndarray:
    """Principal logarithm for a proper rotation matrix."""
    value = np.asarray(rotation, dtype=float)
    cosine = float(np.clip((np.trace(value) - 1.0) * 0.5, -1.0, 1.0))
    theta = float(np.arccos(cosine))
    vee = np.array(
        [value[2, 1] - value[1, 2], value[0, 2] - value[2, 0], value[1, 0] - value[0, 1]],
        dtype=float,
    )
    if theta < 1e-8:
        return 0.5 * vee
    if np.pi - theta < 1e-5:
        # The diagonal branch avoids division by sin(theta) near pi.
        axis = np.sqrt(np.maximum((np.diag(value) + 1.0) * 0.5, 0.0))
        largest = int(np.argmax(axis))
        if axis[largest] > 1e-10:
            for index in range(3):
                if index != largest:
                    axis[index] = np.copysign(axis[index], value[index, largest] + value[largest, index])
        norm = float(np.linalg.norm(axis))
        return theta * axis / norm
    return theta / (2.0 * np.sin(theta)) * vee


def so3_right_jacobian(rotvec: np.ndarray) -> np.ndarray:
    """Right Jacobian of SO(3), stable at zero."""
    value = np.asarray(rotvec, dtype=float)
    theta = float(np.linalg.norm(value))
    cross = skew(value)
    if theta < 1e-8:
        a = 0.5 - theta * theta / 24.0 + theta**4 / 720.0
        b = 1.0 / 6.0 - theta * theta / 120.0 + theta**4 / 5040.0
    else:
        a = (1.0 - np.cos(theta)) / (theta * theta)
        b = (theta - np.sin(theta)) / (theta**3)
    return np.eye(3) - a * cross + b * (cross @ cross)


class PreintegrationStatus(str, Enum):
    VALID = "VALID"
    INSUFFICIENT_SAMPLES = "INSUFFICIENT_SAMPLES"
    INVALID_SAMPLE_STATUS = "INVALID_SAMPLE_STATUS"
    NODE_ID_CHANGE = "NODE_ID_CHANGE"
    UNKNOWN_NOISE_PROFILE = "UNKNOWN_NOISE_PROFILE"
    DUPLICATE_TIMESTAMP = "DUPLICATE_TIMESTAMP"
    TIME_REVERSAL = "TIME_REVERSAL"
    GAP_EXCEEDS_ENVELOPE = "GAP_EXCEEDS_ENVELOPE"
    BOOT_EPOCH_CHANGE = "BOOT_EPOCH_CHANGE"
    SATURATION = "SATURATION"
    NONFINITE = "NONFINITE"


@dataclass(frozen=True)
class ImuSample:
    """One decoded sample on its authoritative B306 global time axis."""

    node_id: str
    global_time_ns: int
    boot_epoch: int
    accel_mps2: np.ndarray
    gyro_rad_s: np.ndarray
    accepted: bool = True
    acc_raw: tuple[int, int, int] | None = None
    gyro_raw: tuple[int, int, int] | None = None

    def __post_init__(self) -> None:
        accel = np.asarray(self.accel_mps2, dtype=float)
        gyro = np.asarray(self.gyro_rad_s, dtype=float)
        if accel.shape != (3,) or gyro.shape != (3,):
            raise ValueError("IMU vectors must have shape (3,)")
        object.__setattr__(self, "accel_mps2", accel.copy())
        object.__setattr__(self, "gyro_rad_s", gyro.copy())
        if self.acc_raw is not None and len(self.acc_raw) != 3:
            raise ValueError("acc_raw must have length three")
        if self.gyro_raw is not None and len(self.gyro_raw) != 3:
            raise ValueError("gyro_raw must have length three")


@dataclass(frozen=True)
class NoiseParameters:
    """Continuous-time densities used by one physical node.

    White-noise units are measurement-unit/sqrt(Hz).  Bias random walks are
    bias-unit/sqrt(s).  A provenance label is mandatory so synthetic parameters
    cannot silently masquerade as measured sensor noise.
    """

    accel_white_noise_density_mps2_sqrt_hz: float
    gyro_white_noise_density_rad_s_sqrt_hz: float
    accel_bias_random_walk_mps2_s_sqrt_s: float
    gyro_bias_random_walk_rad_s2_sqrt_s: float
    provenance: str

    def __post_init__(self) -> None:
        values = (
            self.accel_white_noise_density_mps2_sqrt_hz,
            self.gyro_white_noise_density_rad_s_sqrt_hz,
            self.accel_bias_random_walk_mps2_s_sqrt_s,
            self.gyro_bias_random_walk_rad_s2_sqrt_s,
        )
        if not self.provenance:
            raise ValueError("noise provenance is mandatory")
        if not np.isfinite(values).all() or min(values) < 0.0:
            raise ValueError("noise densities must be finite and nonnegative")


@dataclass(frozen=True)
class PreintegratorConfig:
    """Time/saturation envelope; no nominal sample period is represented."""

    max_gap_s: float = 0.020
    missing_sample_threshold_s: float = 0.0075
    raw_saturation_min: int = -32768
    raw_saturation_max: int = 32767
    accel_saturation_mps2: float = np.inf
    gyro_saturation_rad_s: float = np.inf

    def __post_init__(self) -> None:
        if not (0.0 < self.missing_sample_threshold_s <= self.max_gap_s):
            raise ValueError("gap thresholds must satisfy 0 < missing <= max")
        if self.raw_saturation_min >= self.raw_saturation_max:
            raise ValueError("invalid raw saturation bounds")
        if self.accel_saturation_mps2 <= 0.0 or self.gyro_saturation_rad_s <= 0.0:
            raise ValueError("physical saturation limits must be positive")


@dataclass(frozen=True)
class BiasCorrectedDelta:
    delta_rotation: np.ndarray
    delta_velocity: np.ndarray
    delta_position: np.ndarray


@dataclass(frozen=True)
class PreintegratedInterval:
    """Bias-linearized delta and 15-state covariance for one node interval.

    Covariance ordering is ``rotation, velocity, position, gyro_bias,
    accel_bias`` with three coordinates per block.
    """

    node_id: str
    status: PreintegrationStatus
    reason: str
    delta_rotation: np.ndarray
    delta_velocity: np.ndarray
    delta_position: np.ndarray
    jacobian_rotation_gyro_bias: np.ndarray
    jacobian_velocity_gyro_bias: np.ndarray
    jacobian_velocity_accel_bias: np.ndarray
    jacobian_position_gyro_bias: np.ndarray
    jacobian_position_accel_bias: np.ndarray
    covariance: np.ndarray
    start_time_ns: int | None
    end_time_ns: int | None
    sample_count: int
    interval_count: int
    duration_s: float
    boot_epoch: int | None
    bounded_gap_count: int
    max_dt_s: float
    reference_gyro_bias_rad_s: np.ndarray
    reference_accel_bias_mps2: np.ndarray
    noise_provenance: str | None

    @property
    def valid(self) -> bool:
        return self.status is PreintegrationStatus.VALID

    def bias_corrected(
        self, gyro_bias_rad_s: np.ndarray, accel_bias_mps2: np.ndarray
    ) -> BiasCorrectedDelta:
        """Apply the stored first-order bias Jacobians at a new bias point."""
        if not self.valid:
            raise ValueError(f"cannot bias-correct {self.status.value} interval")
        delta_bg = np.asarray(gyro_bias_rad_s, dtype=float) - self.reference_gyro_bias_rad_s
        delta_ba = np.asarray(accel_bias_mps2, dtype=float) - self.reference_accel_bias_mps2
        if delta_bg.shape != (3,) or delta_ba.shape != (3,):
            raise ValueError("bias vectors must have shape (3,)")
        return BiasCorrectedDelta(
            self.delta_rotation @ so3_exp(self.jacobian_rotation_gyro_bias @ delta_bg),
            self.delta_velocity
            + self.jacobian_velocity_gyro_bias @ delta_bg
            + self.jacobian_velocity_accel_bias @ delta_ba,
            self.delta_position
            + self.jacobian_position_gyro_bias @ delta_bg
            + self.jacobian_position_accel_bias @ delta_ba,
        )


def _zero_result(
    node_id: str,
    status: PreintegrationStatus,
    reason: str,
    samples: Sequence[ImuSample],
    gyro_bias: np.ndarray,
    accel_bias: np.ndarray,
    noise_provenance: str | None = None,
) -> PreintegratedInterval:
    start = int(samples[0].global_time_ns) if samples else None
    end = int(samples[-1].global_time_ns) if samples else None
    boot = int(samples[0].boot_epoch) if samples else None
    return PreintegratedInterval(
        node_id=node_id,
        status=status,
        reason=reason,
        delta_rotation=np.eye(3),
        delta_velocity=np.zeros(3),
        delta_position=np.zeros(3),
        jacobian_rotation_gyro_bias=np.zeros((3, 3)),
        jacobian_velocity_gyro_bias=np.zeros((3, 3)),
        jacobian_velocity_accel_bias=np.zeros((3, 3)),
        jacobian_position_gyro_bias=np.zeros((3, 3)),
        jacobian_position_accel_bias=np.zeros((3, 3)),
        covariance=np.zeros((15, 15)),
        start_time_ns=start,
        end_time_ns=end,
        sample_count=len(samples),
        interval_count=max(0, len(samples) - 1),
        duration_s=0.0 if start is None or end is None else (end - start) * 1e-9,
        boot_epoch=boot,
        bounded_gap_count=0,
        max_dt_s=0.0,
        reference_gyro_bias_rad_s=gyro_bias.copy(),
        reference_accel_bias_mps2=accel_bias.copy(),
        noise_provenance=noise_provenance,
    )


class NativeTimePreintegrator:
    """Preintegrate independent node streams using their native timestamps."""

    def __init__(
        self,
        noise_by_node: Mapping[str, NoiseParameters],
        config: PreintegratorConfig | Mapping[str, PreintegratorConfig] | None = None,
    ) -> None:
        if not noise_by_node:
            raise ValueError("at least one per-node noise profile is required")
        self.noise_by_node = dict(noise_by_node)
        self.config = config or PreintegratorConfig()

    def _config_for(self, node_id: str) -> PreintegratorConfig:
        if isinstance(self.config, Mapping):
            if node_id not in self.config:
                raise KeyError(node_id)
            return self.config[node_id]
        return self.config

    def integrate(
        self,
        samples: Sequence[ImuSample],
        *,
        gyro_bias_rad_s: np.ndarray | None = None,
        accel_bias_mps2: np.ndarray | None = None,
    ) -> PreintegratedInterval:
        """Integrate in source order; invalid boundaries return typed status."""
        values = tuple(samples)
        node_id = values[0].node_id if values else ""
        gyro_bias = np.zeros(3) if gyro_bias_rad_s is None else np.asarray(gyro_bias_rad_s, dtype=float)
        accel_bias = np.zeros(3) if accel_bias_mps2 is None else np.asarray(accel_bias_mps2, dtype=float)
        if gyro_bias.shape != (3,) or accel_bias.shape != (3,):
            raise ValueError("bias vectors must have shape (3,)")
        if not np.isfinite(gyro_bias).all() or not np.isfinite(accel_bias).all():
            return _zero_result(node_id, PreintegrationStatus.NONFINITE, "nonfinite reference bias", values, gyro_bias, accel_bias)
        if len(values) < 2:
            return _zero_result(node_id, PreintegrationStatus.INSUFFICIENT_SAMPLES, "at least two samples required", values, gyro_bias, accel_bias)
        if any(sample.node_id != node_id for sample in values):
            return _zero_result(node_id, PreintegrationStatus.NODE_ID_CHANGE, "one interval cannot cross node identity", values, gyro_bias, accel_bias)
        if node_id not in self.noise_by_node:
            return _zero_result(node_id, PreintegrationStatus.UNKNOWN_NOISE_PROFILE, "missing per-node noise parameters", values, gyro_bias, accel_bias)
        noise = self.noise_by_node[node_id]
        try:
            config = self._config_for(node_id)
        except KeyError:
            return _zero_result(node_id, PreintegrationStatus.UNKNOWN_NOISE_PROFILE, "missing per-node integration envelope", values, gyro_bias, accel_bias, noise.provenance)
        if any(not sample.accepted for sample in values):
            return _zero_result(node_id, PreintegrationStatus.INVALID_SAMPLE_STATUS, "all input samples must be accepted ledger events", values, gyro_bias, accel_bias, noise.provenance)
        if any(sample.boot_epoch != values[0].boot_epoch for sample in values):
            return _zero_result(node_id, PreintegrationStatus.BOOT_EPOCH_CHANGE, "interval crosses boot epoch", values, gyro_bias, accel_bias, noise.provenance)
        if any(not np.isfinite(sample.accel_mps2).all() or not np.isfinite(sample.gyro_rad_s).all() for sample in values):
            return _zero_result(node_id, PreintegrationStatus.NONFINITE, "nonfinite IMU measurement", values, gyro_bias, accel_bias, noise.provenance)
        for sample in values:
            raw = (() if sample.acc_raw is None else sample.acc_raw) + (() if sample.gyro_raw is None else sample.gyro_raw)
            if any(value <= config.raw_saturation_min or value >= config.raw_saturation_max for value in raw):
                return _zero_result(node_id, PreintegrationStatus.SATURATION, "raw sensor rail reached", values, gyro_bias, accel_bias, noise.provenance)
            if (np.max(np.abs(sample.accel_mps2)) >= config.accel_saturation_mps2
                    or np.max(np.abs(sample.gyro_rad_s)) >= config.gyro_saturation_rad_s):
                return _zero_result(node_id, PreintegrationStatus.SATURATION, "configured physical sensor limit reached", values, gyro_bias, accel_bias, noise.provenance)

        delta_ns = np.diff(np.asarray([sample.global_time_ns for sample in values], dtype=np.int64))
        if np.any(delta_ns == 0):
            return _zero_result(node_id, PreintegrationStatus.DUPLICATE_TIMESTAMP, "duplicate native timestamp", values, gyro_bias, accel_bias, noise.provenance)
        if np.any(delta_ns < 0):
            return _zero_result(node_id, PreintegrationStatus.TIME_REVERSAL, "native time reversal", values, gyro_bias, accel_bias, noise.provenance)
        delta_s = delta_ns.astype(float) * 1e-9
        if np.any(delta_s > config.max_gap_s):
            return _zero_result(node_id, PreintegrationStatus.GAP_EXCEEDS_ENVELOPE, "gap exceeds configured envelope", values, gyro_bias, accel_bias, noise.provenance)

        rotation = np.eye(3)
        velocity = np.zeros(3)
        position = np.zeros(3)
        j_r_bg = np.zeros((3, 3))
        j_v_bg = np.zeros((3, 3))
        j_v_ba = np.zeros((3, 3))
        j_p_bg = np.zeros((3, 3))
        j_p_ba = np.zeros((3, 3))
        covariance = np.zeros((15, 15))

        for sample, dt in zip(values[:-1], delta_s, strict=True):
            omega = sample.gyro_rad_s - gyro_bias
            specific_force = sample.accel_mps2 - accel_bias
            phi = omega * dt
            incremental_rotation = so3_exp(phi)
            right_jacobian = so3_right_jacobian(phi)

            old_rotation = rotation
            old_velocity = velocity
            old_j_r_bg = j_r_bg
            old_j_v_bg = j_v_bg
            old_j_v_ba = j_v_ba

            position = position + old_velocity * dt + 0.5 * (old_rotation @ specific_force) * dt**2
            velocity = velocity + (old_rotation @ specific_force) * dt
            rotation = old_rotation @ incremental_rotation
            j_p_bg = j_p_bg + old_j_v_bg * dt - 0.5 * old_rotation @ skew(specific_force) @ old_j_r_bg * dt**2
            j_p_ba = j_p_ba + old_j_v_ba * dt - 0.5 * old_rotation * dt**2
            j_v_bg = old_j_v_bg - old_rotation @ skew(specific_force) @ old_j_r_bg * dt
            j_v_ba = old_j_v_ba - old_rotation * dt
            j_r_bg = incremental_rotation.T @ old_j_r_bg - right_jacobian * dt

            transition = np.eye(15)
            transition[0:3, 0:3] = incremental_rotation.T
            transition[0:3, 9:12] = -right_jacobian * dt
            transition[3:6, 0:3] = -old_rotation @ skew(specific_force) * dt
            transition[3:6, 12:15] = -old_rotation * dt
            transition[6:9, 0:3] = -0.5 * old_rotation @ skew(specific_force) * dt**2
            transition[6:9, 3:6] = np.eye(3) * dt
            transition[6:9, 12:15] = -0.5 * old_rotation * dt**2
            noise_gain = np.zeros((15, 12))
            noise_gain[0:3, 0:3] = -right_jacobian * dt
            noise_gain[3:6, 3:6] = -old_rotation * dt
            noise_gain[6:9, 3:6] = -0.5 * old_rotation * dt**2
            noise_gain[9:12, 6:9] = np.eye(3)
            noise_gain[12:15, 9:12] = np.eye(3)
            driving = np.zeros((12, 12))
            driving[0:3, 0:3] = np.eye(3) * noise.gyro_white_noise_density_rad_s_sqrt_hz**2 / dt
            driving[3:6, 3:6] = np.eye(3) * noise.accel_white_noise_density_mps2_sqrt_hz**2 / dt
            driving[6:9, 6:9] = np.eye(3) * noise.gyro_bias_random_walk_rad_s2_sqrt_s**2 * dt
            driving[9:12, 9:12] = np.eye(3) * noise.accel_bias_random_walk_mps2_s_sqrt_s**2 * dt
            covariance = transition @ covariance @ transition.T + noise_gain @ driving @ noise_gain.T
            covariance = 0.5 * (covariance + covariance.T)

        return PreintegratedInterval(
            node_id=node_id,
            status=PreintegrationStatus.VALID,
            reason="consecutive accepted native-time samples",
            delta_rotation=rotation,
            delta_velocity=velocity,
            delta_position=position,
            jacobian_rotation_gyro_bias=j_r_bg,
            jacobian_velocity_gyro_bias=j_v_bg,
            jacobian_velocity_accel_bias=j_v_ba,
            jacobian_position_gyro_bias=j_p_bg,
            jacobian_position_accel_bias=j_p_ba,
            covariance=covariance,
            start_time_ns=int(values[0].global_time_ns),
            end_time_ns=int(values[-1].global_time_ns),
            sample_count=len(values),
            interval_count=len(values) - 1,
            duration_s=float(np.sum(delta_s)),
            boot_epoch=int(values[0].boot_epoch),
            bounded_gap_count=int(np.sum(delta_s > config.missing_sample_threshold_s)),
            max_dt_s=float(np.max(delta_s)),
            reference_gyro_bias_rad_s=gyro_bias.copy(),
            reference_accel_bias_mps2=accel_bias.copy(),
            noise_provenance=noise.provenance,
        )

    def integrate_async(
        self,
        streams: Mapping[str, Sequence[ImuSample]],
        *,
        gyro_bias_by_node: Mapping[str, np.ndarray] | None = None,
        accel_bias_by_node: Mapping[str, np.ndarray] | None = None,
    ) -> dict[str, PreintegratedInterval]:
        """Integrate ten (or any number of) asynchronous streams independently."""
        gyro_bias_by_node = gyro_bias_by_node or {}
        accel_bias_by_node = accel_bias_by_node or {}
        results: dict[str, PreintegratedInterval] = {}
        for node_id, samples in streams.items():
            if samples and samples[0].node_id != node_id:
                results[node_id] = _zero_result(
                    node_id,
                    PreintegrationStatus.NODE_ID_CHANGE,
                    "stream key does not match sample identity",
                    samples,
                    np.asarray(gyro_bias_by_node.get(node_id, np.zeros(3)), dtype=float),
                    np.asarray(accel_bias_by_node.get(node_id, np.zeros(3)), dtype=float),
                )
            else:
                results[node_id] = self.integrate(
                    samples,
                    gyro_bias_rad_s=gyro_bias_by_node.get(node_id),
                    accel_bias_mps2=accel_bias_by_node.get(node_id),
                )
        return results


def samples_from_typed_ledger(node_id: str, rows: np.ndarray) -> tuple[ImuSample, ...]:
    """Adapt production typed-ledger rows without sorting or nominal timing.

    The conversion is the documented JY61P range used by the existing ingest
    path: accelerometer raw/2048 g and gyroscope raw/16.384 deg/s.
    """
    required = {"global_time_ns", "boot_epoch", "status", "acc_raw", "gyro_raw"}
    names = set(rows.dtype.names or ())
    if not required <= names:
        raise ValueError(f"typed ledger lacks fields: {sorted(required - names)}")
    converted = []
    for row in rows:
        acc_raw = tuple(int(value) for value in row["acc_raw"])
        gyro_raw = tuple(int(value) for value in row["gyro_raw"])
        converted.append(
            ImuSample(
                node_id=node_id,
                global_time_ns=int(row["global_time_ns"]),
                boot_epoch=int(row["boot_epoch"]),
                accel_mps2=np.asarray(acc_raw, dtype=float) / 2048.0 * G,
                gyro_rad_s=np.deg2rad(np.asarray(gyro_raw, dtype=float) / 16.384),
                accepted=int(row["status"]) == 1,
                acc_raw=acc_raw,
                gyro_raw=gyro_raw,
            )
        )
    return tuple(converted)
