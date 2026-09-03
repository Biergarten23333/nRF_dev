"""Thin, official-only OpenSim/OpenSense adapter for frozen Capture2."""

from .adapter import (
    BODY_BY_SEGMENT,
    ENABLED_COORDINATES,
    IMU_FRAME_BY_SEGMENT,
    configure_official_model,
    run_official_imu_ik,
    write_frozen_orientation_sto,
)

__all__ = [
    "BODY_BY_SEGMENT",
    "ENABLED_COORDINATES",
    "IMU_FRAME_BY_SEGMENT",
    "configure_official_model",
    "run_official_imu_ik",
    "write_frozen_orientation_sto",
]
