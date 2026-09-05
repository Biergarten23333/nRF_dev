"""Capture-calibrated articulated IK and FK for the C2 skeleton."""

from .model import (
    HingeJoint,
    fit_articulated_model,
    hinge_coordinate_deg,
)
from .orientation_ik import (
    apply_orientation_constrained_ik,
    project_hinge_corrections,
    reconstruct_distal_orientation,
    solve_hinge_flexion_deg,
)

__all__ = [
    "HingeJoint",
    "fit_articulated_model",
    "hinge_coordinate_deg",
    "reconstruct_distal_orientation",
    "apply_orientation_constrained_ik",
    "project_hinge_corrections",
    "solve_hinge_flexion_deg",
]
