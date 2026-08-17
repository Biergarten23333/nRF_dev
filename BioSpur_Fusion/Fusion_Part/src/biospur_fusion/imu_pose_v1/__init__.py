"""Operator-mapped, IMU-only articulated pose engineering baseline.

This package deliberately does not import the rejected ``articulated_v2``
implementation or any UWB consumer.
"""

from .estimator import CoupledPoseEstimator, EstimatorConfig
from .mapping import FrozenOperatorMapping
from .types import ImuSample, PoseFrame

__all__ = ["CoupledPoseEstimator", "EstimatorConfig", "FrozenOperatorMapping", "ImuSample", "PoseFrame"]
