"""Strict-causal weak-absolute-reference common-root experiment.

This package is intentionally isolated from product runners.  Importing it has
no side effects and cannot change the frozen M1 or any product default.
"""

from .estimator import CausalDelayedRootFilter, RootFilterConfig
from .models import (
    FrameBindingStatus,
    ImuSample,
    PositionObservation,
    RootOutput,
    RootState,
    SystemMode,
)

__all__ = [
    "CausalDelayedRootFilter",
    "FrameBindingStatus",
    "ImuSample",
    "PositionObservation",
    "RootFilterConfig",
    "RootOutput",
    "RootState",
    "SystemMode",
]
