"""Root-R6A2A synthetic fault-aware whole-body shadow integration."""

from .contracts import (
    COMMON_NINE,
    CORRECT_NODE_MAP,
    FAMILY_BSF31CC,
    FAMILY_COMMON_NINE,
    HealthState,
    UnifiedHardwareRegistry,
    build_contract_bundle,
)
from .qualification import run_qualification
from .shadow import IntegratedShadowEstimator, ScenarioSpec, run_scenario

__all__ = [
    "COMMON_NINE",
    "CORRECT_NODE_MAP",
    "FAMILY_BSF31CC",
    "FAMILY_COMMON_NINE",
    "HealthState",
    "IntegratedShadowEstimator",
    "ScenarioSpec",
    "UnifiedHardwareRegistry",
    "build_contract_bundle",
    "run_qualification",
    "run_scenario",
]
