"""Root-R6A2A-R2 synthetic FDIR, bias, and uncertainty closure."""

from .contracts import (
    DegradedMode,
    EstimatorInput,
    EstimatorOptions,
    EstimatorOutput,
    EvaluationResult,
    FaultInjectionTruth,
    FaultWindow,
    ObservationStatus,
    ScenarioDefinition,
)

__all__ = [
    "DegradedMode", "EstimatorInput", "EstimatorOptions", "EstimatorOutput",
    "EvaluationResult", "FaultInjectionTruth", "FaultWindow",
    "ObservationStatus", "ScenarioDefinition",
]
