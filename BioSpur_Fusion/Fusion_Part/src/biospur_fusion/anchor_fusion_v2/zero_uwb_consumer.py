from __future__ import annotations

from biospur_fusion.articulated_v2 import ArticulatedImuEstimator, FrozenMappingBinding


def construct_zero_uwb(binding: FrozenMappingBinding, phase3_config: dict, *, range_enabled: bool = False):
    if range_enabled:
        raise ValueError("Phase 4 UWB fusion is not implemented")
    return ArticulatedImuEstimator(binding, phase3_config)


def additive_measurement_interface_capabilities() -> dict:
    return {"future_range_factor": "ADDITIVE_ONLY_NOT_IMPLEMENTED", "quaternion_overwrite": False, "hard_reset": False}
