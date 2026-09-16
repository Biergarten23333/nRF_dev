"""Authoritative raw-to-SI conversion for mixed BioSpur IMU hardware.

Sensor electrical scale belongs here, not in individual estimators.  Board to
body calibration remains a separate, per-placement transform and must never be
silently folded into the sensor data-sheet scale.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Mapping

import numpy as np

G = 9.80665


@dataclass(frozen=True)
class ImuSensorProfile:
    model: str
    accel_mps2_per_lsb: float
    gyro_rad_s_per_lsb: float
    accel_full_scale_g: float
    gyro_full_scale_dps: float
    nominal_odr_hz: float
    scale_provenance: str

    def __post_init__(self) -> None:
        values = (
            self.accel_mps2_per_lsb,
            self.gyro_rad_s_per_lsb,
            self.accel_full_scale_g,
            self.gyro_full_scale_dps,
            self.nominal_odr_hz,
        )
        if not self.model or not self.scale_provenance:
            raise ValueError("IMU model and scale provenance are mandatory")
        if not np.isfinite(values).all() or min(values) <= 0.0:
            raise ValueError("IMU scale values must be finite and positive")

    def raw_to_si(
        self,
        acc_raw: np.ndarray,
        gyro_raw: np.ndarray,
        *,
        sensor_to_output: np.ndarray | None = None,
    ) -> tuple[np.ndarray, np.ndarray]:
        """Convert raw vectors and optionally apply a validated axis map."""
        accel = np.asarray(acc_raw, dtype=float)
        gyro = np.asarray(gyro_raw, dtype=float)
        if accel.shape[-1:] != (3,) or gyro.shape != accel.shape:
            raise ValueError("accelerometer and gyroscope arrays must end in three axes")
        accel = accel * self.accel_mps2_per_lsb
        gyro = gyro * self.gyro_rad_s_per_lsb
        if sensor_to_output is not None:
            matrix = validate_axis_map(sensor_to_output)
            accel = np.einsum("ij,...j->...i", matrix, accel)
            gyro = np.einsum("ij,...j->...i", matrix, gyro)
        return accel, gyro


JY61P_200HZ = ImuSensorProfile(
    model="JY61P_6AXIS",
    accel_mps2_per_lsb=G / 2048.0,
    gyro_rad_s_per_lsb=math.radians(1.0 / 16.384),
    accel_full_scale_g=16.0,
    gyro_full_scale_dps=2000.0,
    nominal_odr_hz=200.0,
    scale_provenance="JY61P_WIT_REGISTER_PROTOCOL_16G_2000DPS",
)

LSM6DSV32X_200HZ_HAODR = ImuSensorProfile(
    model="LSM6DSV32X",
    accel_mps2_per_lsb=0.000976 * G,
    gyro_rad_s_per_lsb=math.radians(0.070),
    accel_full_scale_g=32.0,
    gyro_full_scale_dps=2000.0,
    nominal_odr_hz=200.0,
    scale_provenance="LSM6DSV32X_DATASHEET_REV4_TABLE_3_32G_2000DPS",
)

# Per-unit sensor ownership.  Existing B306 Fusion units retain JY61P; the
# first B120/MAX3220 prototype is explicitly identified as LSM6DSV32X.
NODE_SENSOR_MODELS: Mapping[str, ImuSensorProfile] = {
    "BSF857B": LSM6DSV32X_200HZ_HAODR,
    "BSFEC35": JY61P_200HZ,
    "BSFB165": JY61P_200HZ,
    "BSFAA61": JY61P_200HZ,
    "BSF1120": JY61P_200HZ,
    "BSF31CC": JY61P_200HZ,
    "BSFC2CC": JY61P_200HZ,
    "BSF44AD": JY61P_200HZ,
    "BSF3C79": JY61P_200HZ,
    "BSF6C53": JY61P_200HZ,
    "BSF8BC4": JY61P_200HZ,
}


def profile_for_node(node_id: str) -> ImuSensorProfile:
    """Resolve a unit profile while preserving established B306 captures.

    B120 units must be explicitly listed above.  The compatibility default is
    intentionally limited to the pre-existing B306/JY61P fleet naming scheme.
    """
    normalized = str(node_id).strip().upper()
    try:
        return NODE_SENSOR_MODELS[normalized]
    except KeyError as error:
        raise KeyError(f"no IMU sensor profile for node {node_id!r}") from error


def validate_axis_map(matrix: np.ndarray) -> np.ndarray:
    """Accept only proper signed/permuted 3-D rotations.

    A reflection would invert handedness and corrupt angular-rate integration.
    General scale/cross-axis calibration is deliberately a different layer.
    """
    value = np.asarray(matrix, dtype=float)
    if value.shape != (3, 3) or not np.isfinite(value).all():
        raise ValueError("axis map must be a finite 3x3 matrix")
    if not np.allclose(value.T @ value, np.eye(3), atol=1e-12):
        raise ValueError("axis map must be orthogonal")
    if not np.isclose(np.linalg.det(value), 1.0, atol=1e-12):
        raise ValueError("axis map must preserve right-handed coordinates")
    return value.copy()
