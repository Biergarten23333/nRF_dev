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

from .model import (
    DOWN,
    HingeJoint,
    _minimal_alignment,
    _rotation,
    _wxyz,
    hinge_coordinate_deg,
)


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


def project_hinge_corrections(
    base_rotations_world: Mapping[str, np.ndarray],
    correction_rotvec: Mapping[str, np.ndarray],
    model: Mapping[str, HingeJoint],
) -> tuple[dict[str, np.ndarray], dict[str, Any]]:
    """Project one articulated update through the public hinge/ROM owner.

    Raw-range IK is allowed to propose small rotations for every segment, but
    it must not create a second joint convention.  This function applies those
    proposals, invokes the same analytic hinge reconstruction used by the
    native-200 trajectory, and converts the projected absolute orientations
    back to right-multiplicative segment corrections.
    """

    base = {
        segment: np.asarray(rotation, dtype=float).reshape(3, 3)
        for segment, rotation in base_rotations_world.items()
    }
    corrections = {
        segment: np.asarray(value, dtype=float).reshape(3).copy()
        for segment, value in correction_rotvec.items()
    }
    if set(base) != set(corrections):
        raise ValueError("base rotations and corrections must own the same segments")
    if any(
        not np.all(np.isfinite(value))
        for collection in (base, corrections)
        for value in collection.values()
    ):
        raise ValueError("hinge projection inputs must be finite")

    absolute = {
        segment: base[segment] @ Rotation.from_rotvec(corrections[segment]).as_matrix()
        for segment in base
    }
    def one_wxyz(matrix: np.ndarray) -> np.ndarray:
        quaternion = Rotation.from_matrix(matrix).as_quat()
        return np.r_[quaternion[3], quaternion[:3]][None, :]

    joint_metrics: dict[str, Any] = {}
    for name, joint in model.items():
        parent_q = one_wxyz(absolute[joint.parent])
        child_q = one_wxyz(absolute[joint.child])
        pre_signed = float(hinge_coordinate_deg(parent_q, child_q, joint)[0])
        flexion, solve_metrics = solve_hinge_flexion_deg(
            parent_q, child_q, joint
        )
        child_projected_q, reconstruction = reconstruct_distal_orientation(
            parent_q, child_q, flexion, joint
        )
        absolute[joint.child] = _rotation(child_projected_q).as_matrix()[0]
        corrections[joint.child] = Rotation.from_matrix(
            base[joint.child].T @ absolute[joint.child]
        ).as_rotvec()
        post_child_q = one_wxyz(absolute[joint.child])
        post_signed = float(
            hinge_coordinate_deg(parent_q, post_child_q, joint)[0]
        )
        tolerance_deg = 3e-6
        joint_metrics[name] = {
            "pre_projection_signed_deg": pre_signed,
            "post_projection_signed_deg": post_signed,
            "pre_projection_below_rom": bool(pre_signed < joint.minimum_deg),
            "pre_projection_above_rom": bool(pre_signed > joint.maximum_deg),
            "post_projection_inside_rom": bool(
                joint.minimum_deg - tolerance_deg
                <= post_signed
                <= joint.maximum_deg + tolerance_deg
            ),
            "flexion_deg": float(flexion[0]),
            "fk_direction_residual_deg": reconstruction[
                "fk_direction_residual_maximum_deg"
            ],
            "observed_unsigned_bend_deg": solve_metrics[
                "observed_maximum_deg"
            ],
        }
    all_inside = all(
        row["post_projection_inside_rom"] for row in joint_metrics.values()
    )
    maximum_residual = max(
        row["fk_direction_residual_deg"] for row in joint_metrics.values()
    )
    return corrections, {
        "joint": joint_metrics,
        "pre_projection_below_rom_count": sum(
            row["pre_projection_below_rom"] for row in joint_metrics.values()
        ),
        "pre_projection_above_rom_count": sum(
            row["pre_projection_above_rom"] for row in joint_metrics.values()
        ),
        "post_projection_all_inside_rom": all_inside,
        "fk_direction_residual_maximum_deg": maximum_residual,
    }
