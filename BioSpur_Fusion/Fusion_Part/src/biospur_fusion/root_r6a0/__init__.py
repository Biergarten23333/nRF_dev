"""Root-R6A0 whole-body articulated shadow-only information backbone."""

from .body import BodyModel, KeyframeState, StaticCalibration, load_body_model
from .contracts import (
    ActivationState,
    AuthorityProposal,
    AuthorityScope,
    CalibrationSlot,
    CalibrationStatus,
    CapabilityLevel,
    CapabilityState,
    EvidenceRecord,
    FactorProposal,
    FaultDomain,
    GraphSpec,
    HealthHypothesis,
    Informativeness,
    MeasurementHealth,
    ServiceDOF,
    StateBlock,
)

__all__ = [
    "ActivationState",
    "AuthorityProposal",
    "AuthorityScope",
    "BodyModel",
    "CalibrationSlot",
    "CalibrationStatus",
    "CapabilityLevel",
    "CapabilityState",
    "EvidenceRecord",
    "FactorProposal",
    "FaultDomain",
    "GraphSpec",
    "HealthHypothesis",
    "Informativeness",
    "KeyframeState",
    "MeasurementHealth",
    "ServiceDOF",
    "StateBlock",
    "StaticCalibration",
    "load_body_model",
]
