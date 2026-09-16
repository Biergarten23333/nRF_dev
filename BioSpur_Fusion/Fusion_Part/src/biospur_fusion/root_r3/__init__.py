"""Strict-causal weak-absolute-reference common-root experiment.

This package is intentionally isolated from product runners.  Importing it has
no side effects and cannot change the frozen M1 or any product default.
"""

from .estimator import (
    AuthoritativeBaselineReconstructionError,
    CausalDelayedRootFilter,
    PreparedRootCurrentConstraint,
    PreparedRootCausalState,
    PreparedRootPositionVelocityTransaction,
    PreparedRootPositionRejectionTransaction,
    PreparedRootImuTransaction,
    PreparedRootImuVelocityTransaction,
    RootFilterConfig,
    RootTranslationEdgeMode,
)
from .models import (
    AdditiveRootConstraint,
    BoundedTargetRootConstraint,
    FrameBindingStatus,
    ImuSample,
    PositionObservation,
    RootOutput,
    RootState,
    SystemMode,
)

__all__ = [
    "AdditiveRootConstraint",
    "AuthoritativeBaselineReconstructionError",
    "BoundedTargetRootConstraint",
    "CausalDelayedRootFilter",
    "PreparedRootCurrentConstraint",
    "PreparedRootCausalState",
    "PreparedRootPositionVelocityTransaction",
    "PreparedRootPositionRejectionTransaction",
    "PreparedRootImuTransaction",
    "PreparedRootImuVelocityTransaction",
    "FrameBindingStatus",
    "ImuSample",
    "PositionObservation",
    "RootFilterConfig",
    "RootTranslationEdgeMode",
    "RootOutput",
    "RootState",
    "SystemMode",
]
