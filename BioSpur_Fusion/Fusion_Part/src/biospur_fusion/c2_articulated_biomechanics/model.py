"""Capture-calibrated hinge-model ownership for the articulated C2 skeleton.

The functional axes come from the existing QMT/Olsson calibration and are
projected perpendicular to each display segment's longitudinal axis.  The
matching registered action owns the positive flexion branch.  Dynamic IK is
implemented separately in :mod:`orientation_ik`; this module owns only the
model fit and joint-coordinate convention.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Mapping

import numpy as np
from scipy.spatial.transform import Rotation


DOWN = np.array([0.0, 0.0, -1.0])


@dataclass(frozen=True)
class HingeJoint:
    name: str
    parent: str
    child: str
    functional_episode: str
    parent_axis: tuple[float, float, float]
    child_axis: tuple[float, float, float]
    neutral_parent_from_child_xyzw: tuple[float, float, float, float]
    positive_sign: float
    minimum_deg: float
    maximum_deg: float
    neutral_frame_count: int
    functional_frame_count: int

    def to_json(self) -> dict[str, Any]:
        result = asdict(self)
        result["axis_source"] = "QMT/Olsson functional axis projected perpendicular to segment long axis"
        result["zero_source"] = "collinear parent/child segment long axes; episode 00 is the neutral validation set"
        result["sign_source"] = f"dominant excursion in {self.functional_episode}"
        result["rom_owner"] = (
            "Rajagopal/OpenSim coordinate envelope already present in the project; "
            "population-model prior, not subject-specific clinical ROM"
        )
        result["constraint_policy"] = (
            "estimate parent-bone axial heading from the observed bend plane; "
            "preserve endpoints and unsigned bend; cap only upper ROM"
        )
        return result


HINGE_SPECS = {
    # Rajagopal/OpenSim coordinate envelopes already present in the project:
    # elbow_flex_[lr] = [0, 2.618] rad; knee_angle_[lr] = [0, 2.0944] rad.
    # These are population-model priors, not subject-specific clinical ROM.
    # These are internal zero-based trajectory keys.  They correspond to the
    # user-facing actions 06, 07, 10 and 11 respectively.
    "elbow_left": ("upper_arm_left", "forearm_left", "05", 0.0, 150.0),
    "elbow_right": ("upper_arm_right", "forearm_right", "06", 0.0, 150.0),
    "knee_left": ("thigh_left", "shank_left", "09", 0.0, 120.0),
    "knee_right": ("thigh_right", "shank_right", "10", 0.0, 120.0),
}


def _rotation(quat_wxyz: np.ndarray) -> Rotation:
    quat = np.asarray(quat_wxyz, dtype=float)
    return Rotation.from_quat(np.c_[quat[..., 1:4], quat[..., 0]])


def _wxyz(rotation: Rotation) -> np.ndarray:
    quat = rotation.as_quat()
    return np.c_[quat[..., 3], quat[..., 0:3]]


def _unit_perpendicular(axis: np.ndarray) -> np.ndarray:
    result = np.asarray(axis, dtype=float) - float(np.dot(axis, DOWN)) * DOWN
    norm = float(np.linalg.norm(result))
    if norm < 1e-6:
        raise ValueError("functional hinge axis is parallel to segment long axis")
    return result / norm


def _relative(parent_q: np.ndarray, child_q: np.ndarray) -> Rotation:
    return _rotation(parent_q).inv() * _rotation(child_q)


def _long_axis_hinge_angle(relative: Rotation, axis: np.ndarray) -> np.ndarray:
    child_down_in_parent = relative.apply(DOWN)
    parent_down = np.repeat(DOWN[None, :], len(relative), axis=0)
    sine = np.cross(parent_down, child_down_in_parent) @ axis
    cosine = child_down_in_parent @ DOWN
    return np.arctan2(sine, cosine)


def _episode_rows(
    trajectory: Mapping[str, Any], episode: str, segment: str
) -> np.ndarray:
    return np.asarray(
        trajectory["trajectory"][episode][segment]["quat_world_segment_wxyz"],
        dtype=float,
    )


def fit_articulated_model(
    trajectory: Mapping[str, Any], calibration_report: Mapping[str, Any]
) -> dict[str, HingeJoint]:
    """Fit the four signed hinge manifolds from frozen calibration episodes."""

    result: dict[str, HingeJoint] = {}
    diagnostics = calibration_report["qmt_olsson_hinge_axes"]
    for name, (parent, child, action, minimum_deg, maximum_deg) in HINGE_SPECS.items():
        source = diagnostics[name]
        parent_axis = _unit_perpendicular(
            np.asarray(source["parent_axis_reset_segment"], dtype=float)
        )
        child_axis = _unit_perpendicular(
            np.asarray(source["child_axis_reset_segment"], dtype=float)
        )
        action_parent = _episode_rows(trajectory, action, parent)
        action_child = _episode_rows(trajectory, action, child)
        action_relative = _relative(action_parent, action_child)
        raw = _long_axis_hinge_angle(action_relative, parent_axis)
        low, high = np.quantile(raw, [0.05, 0.95])
        positive_sign = 1.0 if abs(high) >= abs(low) else -1.0
        result[name] = HingeJoint(
            name=name,
            parent=parent,
            child=child,
            functional_episode=action,
            parent_axis=tuple(float(value) for value in parent_axis),
            child_axis=tuple(float(value) for value in child_axis),
            neutral_parent_from_child_xyzw=tuple(
                float(value) for value in Rotation.identity().as_quat()
            ),
            positive_sign=positive_sign,
            minimum_deg=minimum_deg,
            maximum_deg=maximum_deg,
            neutral_frame_count=len(_episode_rows(trajectory, "00", parent)),
            functional_frame_count=len(raw),
        )
    return result


def hinge_coordinate_deg(
    parent_q: np.ndarray, child_q: np.ndarray, joint: HingeJoint
) -> np.ndarray:
    parent_axis = np.asarray(joint.parent_axis)
    relative = _relative(parent_q, child_q)
    return np.degrees(
        joint.positive_sign
        * _long_axis_hinge_angle(relative, parent_axis)
    )


def _minimal_alignment(source: np.ndarray, target: np.ndarray) -> Rotation:
    cross = np.cross(source, target)
    sine = np.linalg.norm(cross, axis=1)
    cosine = np.sum(source * target, axis=1)
    axis = np.zeros_like(cross)
    regular = sine > 1e-10
    axis[regular] = cross[regular] / sine[regular, None]
    opposite = (~regular) & (cosine < 0.0)
    if np.any(opposite):
        candidate = np.cross(source[opposite], np.array([1.0, 0.0, 0.0]))
        weak = np.linalg.norm(candidate, axis=1) < 1e-8
        candidate[weak] = np.cross(
            source[opposite][weak], np.array([0.0, 1.0, 0.0])
        )
        candidate /= np.linalg.norm(candidate, axis=1)[:, None]
        axis[opposite] = candidate
    angle = np.arctan2(sine, cosine)
    angle[opposite] = np.pi
    return Rotation.from_rotvec(axis * angle[:, None])
