"""Explicit gauge/nullspace and per-service observability summaries."""
from __future__ import annotations

from typing import Iterable

import numpy as np

from .authority import baseline_capability, capability_for_scenario
from .contracts import ServiceDOF


def local_information_rank(jacobian: np.ndarray, tolerance: float = 1e-9) -> int:
    singular = np.linalg.svd(np.atleast_2d(np.asarray(jacobian, float)), compute_uv=False)
    return int(np.sum(singular > tolerance * max(1.0, singular[0] if singular.size else 1.0)))


def scalar_range_rank(jacobian: np.ndarray) -> int:
    value = np.atleast_2d(np.asarray(jacobian, float))
    if value.shape[0] != 1:
        raise ValueError("scalar range Jacobian must have one residual row")
    rank = local_information_rank(value)
    if rank > 1:
        raise AssertionError("one scalar range added more than one local rank")
    return rank


def expected_nullspace(*, qualified_static_gauge: bool, raw_uwb_available: bool,
                       calibration_resolved: bool) -> tuple[str, ...]:
    directions = []
    if not qualified_static_gauge:
        directions.extend(("global_translation_x", "global_translation_y", "global_translation_z", "global_yaw"))
    if not raw_uwb_available and "global_yaw" not in directions:
        directions.append("global_yaw")
    if not raw_uwb_available and not qualified_static_gauge:
        directions.append("global_translation_velocity_drift")
    if not calibration_resolved:
        directions.extend((
            "tag_lever_vs_segment_pose_coupling",
            "imu_extrinsic_vs_joint_state_coupling",
            "joint_centre_vs_bone_geometry_coupling",
            "clock_offset_vs_motion_coupling",
        ))
    return tuple(dict.fromkeys(directions))


def capability_atlas() -> dict:
    baseline = baseline_capability()
    blackout = capability_for_scenario("all_uwb_blackout")
    return {
        "schema": "biospur.root_r6a0.observability_capability.v1",
        "principles": [
            "IK and FK enforce kinematic consistency but do not create global position or yaw observability.",
            "A scalar range contributes at most one local information rank.",
            "A derived anatomical point retains FK/calibration ancestry and is never labelled directly observed.",
            "Static world/model gauge and time-varying motion/drift occupy separate blocks.",
        ],
        "expected_real_nullspace": list(expected_nullspace(
            qualified_static_gauge=False, raw_uwb_available=True, calibration_resolved=False)),
        "all_uwb_blackout_nullspace": list(expected_nullspace(
            qualified_static_gauge=False, raw_uwb_available=False, calibration_resolved=False)),
        "baseline": {dof.value: {"level": value.level.value, "uncertainty_scale": value.uncertainty_scale,
                                  "ancestry": list(value.ancestry), "reason": value.reason}
                     for dof, value in baseline.items()},
        "all_uwb_blackout": {dof.value: {"level": value.level.value, "uncertainty_scale": value.uncertainty_scale,
                                          "ancestry": list(value.ancestry), "reason": value.reason}
                             for dof, value in blackout.items()},
        "derived_endpoint_rule": {
            ServiceDOF.DERIVED_WRIST_ANKLE_POSITIONS.value: "KINEMATICALLY_RECONSTRUCTED",
            "directly_observed": False,
        },
    }
