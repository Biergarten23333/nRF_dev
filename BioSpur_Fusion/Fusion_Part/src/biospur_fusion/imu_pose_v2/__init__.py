"""Continuous-session, operator-mapped IMU articulated-pose core."""

from .types import CalibrationBundle, ImuObservation, PoseTick

__all__ = ["CalibrationBundle", "ImuObservation", "PoseTick"]
"""Phase 3-R2 continuous-session, operator-mapped IMU pose core."""

from .calibration import apply_calibration, fit_joint_calibration, validate_mapping
from .estimator import ContinuousArticulatedEstimator, EstimatorConfig
from .frontend import ContinuousNodeFrontend, FrontendConfig

__all__ = [
    "ContinuousArticulatedEstimator", "ContinuousNodeFrontend", "EstimatorConfig",
    "FrontendConfig", "apply_calibration", "fit_joint_calibration", "validate_mapping",
]
