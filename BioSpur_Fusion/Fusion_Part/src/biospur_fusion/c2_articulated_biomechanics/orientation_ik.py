"""Analytic orientation IK for the four C2 hinge joints.

The native C2 stream measures every segment orientation at 200 Hz.  For an
elbow or knee, the angle between the proximal and distal long axes observes
the flexion magnitude without relying on the independently drifting absolute
headings.  The capture-calibrated functional hinge axis supplies the missing
anatomical bend plane.  FK then reconstructs the distal direction, while the
measured rotation about that distal long axis is retained.
"""

from __future__ import annotations

from typing import Any, Mapping

import numpy as np
from scipy.spatial.transform import Rotation

from .model import DOWN, HingeJoint, _minimal_alignment, _rotation, _wxyz


def _unsigned_bend_deg(parent: Rotation, child: Rotation) -> np.ndarray:
    parent_down = parent.apply(DOWN)
    child_down = child.apply(DOWN)
    return np.degrees(np.arccos(np.clip(
        np.sum(parent_down * child_down, axis=1), -1.0, 1.0
    )))


def reconstruct_distal_orientation(
    parent_q_wxyz: np.ndarray,
    child_q_wxyz: np.ndarray,
    flexion_deg: np.ndarray,
    joint: HingeJoint,
) -> tuple[np.ndarray, dict[str, Any]]:
    """Set the hinge direction while retaining distal axial twist."""

    parent = _rotation(parent_q_wxyz)
    child = _rotation(child_q_wxyz)
    flexion = np.asarray(flexion_deg, dtype=float)
    if flexion.shape != (len(parent_q_wxyz),):
        raise ValueError("one flexion coordinate is required per frame")
    parent_down = parent.apply(DOWN)
    child_down = child.apply(DOWN)
    positive_axis_local = joint.positive_sign * np.asarray(joint.parent_axis)
    axis_world = parent.apply(np.repeat(
        positive_axis_local[None, :], len(flexion), axis=0
    ))
    target_down = Rotation.from_rotvec(
        axis_world * np.radians(flexion)[:, None]
    ).apply(parent_down)
    direction_correction = _minimal_alignment(child_down, target_down)
    corrected = direction_correction * child
    correction_deg = np.degrees(direction_correction.magnitude())
    residual = np.degrees(np.arccos(np.clip(
        np.sum(corrected.apply(DOWN) * target_down, axis=1), -1.0, 1.0
    )))
    return _wxyz(corrected), {
        "direction_correction_rms_deg": float(np.sqrt(np.mean(
            correction_deg * correction_deg
        ))),
        "direction_correction_p95_deg": float(np.quantile(correction_deg, 0.95)),
        "direction_correction_maximum_deg": float(np.max(correction_deg)),
        "fk_direction_residual_maximum_deg": float(np.max(residual)),
        "distal_axial_twist_policy": "preserved by shortest direction alignment",
    }


def solve_hinge_flexion_deg(
    parent_q_wxyz: np.ndarray,
    child_q_wxyz: np.ndarray,
    joint: HingeJoint,
) -> tuple[np.ndarray, dict[str, Any]]:
    """Project a measured segment pair onto its anatomical hinge ROM."""

    observed = _unsigned_bend_deg(
        _rotation(parent_q_wxyz), _rotation(child_q_wxyz)
    )
    flexion = np.clip(observed, joint.minimum_deg, joint.maximum_deg)
    return flexion, {
        "frame_count": len(flexion),
        "observed_minimum_deg": float(np.min(observed)),
        "observed_maximum_deg": float(np.max(observed)),
        "flexion_minimum_deg": float(np.min(flexion)),
        "flexion_maximum_deg": float(np.max(flexion)),
        "below_rom_count": int(np.sum(observed < joint.minimum_deg)),
        "above_rom_count": int(np.sum(observed > joint.maximum_deg)),
        "source": "native-200-Hz proximal/distal segment long-axis angle",
    }


def apply_orientation_constrained_ik(
    trajectory: Mapping[str, Any],
    model: Mapping[str, HingeJoint],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Apply analytic hinge IK and return a renderer-compatible trajectory."""

    corrected: dict[str, Any] = {"trajectory": {}}
    metrics: dict[str, Any] = {}
    for episode, segments in trajectory["trajectory"].items():
        corrected["trajectory"][episode] = {
            segment: {
                field: np.array(value, copy=True)
                for field, value in row.items()
            }
            for segment, row in segments.items()
        }
        metrics[episode] = {}
        for name, joint in model.items():
            parent_row = corrected["trajectory"][episode][joint.parent]
            child_row = corrected["trajectory"][episode][joint.child]
            flexion, solve_metrics = solve_hinge_flexion_deg(
                parent_row["quat_world_segment_wxyz"],
                child_row["quat_world_segment_wxyz"],
                joint,
            )
            child_corrected, reconstruction_metrics = (
                reconstruct_distal_orientation(
                    parent_row["quat_world_segment_wxyz"],
                    child_row["quat_world_segment_wxyz"],
                    flexion,
                    joint,
                )
            )
            child_row["quat_world_segment_wxyz"] = child_corrected
            metrics[episode][name] = {
                **solve_metrics,
                **reconstruction_metrics,
                "flexion_deg": flexion,
            }
    if "output_coordinate_convention" in trajectory:
        corrected["output_coordinate_convention"] = {
            key: np.array(value, copy=True)
            if isinstance(value, np.ndarray)
            else value
            for key, value in trajectory["output_coordinate_convention"].items()
        }
    return corrected, metrics
