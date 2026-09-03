"""Independent analytic truth generator for the approved C2 3B machine tests.

This module intentionally does not import estimator contracts, residuals,
retractions, metrics, or gates.
"""

from __future__ import annotations

from dataclasses import dataclass
import math

import numpy as np
from scipy.spatial.transform import Rotation


SYNTHETIC_SEGMENTS = (
    "pelvis",
    "torso",
    "upper_arm_left",
    "forearm_left",
    "upper_arm_right",
    "forearm_right",
    "thigh_left",
    "shank_left",
    "thigh_right",
    "shank_right",
)
SYNTHETIC_EDGES = (
    ("pelvis_torso", "pelvis", "torso"),
    ("shoulder_left", "torso", "upper_arm_left"),
    ("elbow_left", "upper_arm_left", "forearm_left"),
    ("shoulder_right", "torso", "upper_arm_right"),
    ("elbow_right", "upper_arm_right", "forearm_right"),
    ("hip_left", "pelvis", "thigh_left"),
    ("knee_left", "thigh_left", "shank_left"),
    ("hip_right", "pelvis", "thigh_right"),
    ("knee_right", "thigh_right", "shank_right"),
)
SYNTHETIC_HINGES = ("elbow_left", "elbow_right", "knee_left", "knee_right")
PROXIMAL = ("pelvis", "torso", "upper_arm_left", "upper_arm_right", "thigh_left", "thigh_right")
DISTAL = ("forearm_left", "forearm_right", "shank_left", "shank_right")


@dataclass(frozen=True)
class SyntheticAxis:
    name: str
    parent: str
    child: str
    parent_axis: np.ndarray
    child_axis: np.ndarray


@dataclass(frozen=True)
class SyntheticCase:
    name: str
    time_s: np.ndarray
    truth: np.ndarray
    measured: np.ndarray
    segment_masks: np.ndarray
    estimator_axes: tuple[SyntheticAxis, ...]
    ideal_relative: np.ndarray | None = None
    nonideal_parent_transverse: np.ndarray | None = None


def _normalize(vector: np.ndarray) -> np.ndarray:
    norm = float(np.linalg.norm(vector))
    if not np.isfinite(norm) or norm <= 1e-12:
        raise ValueError("synthetic normalization failed")
    return np.asarray(vector, dtype=np.float64) / norm


def transverse(axis: np.ndarray) -> np.ndarray:
    axis = _normalize(axis)
    index = int(np.argmin(np.abs(axis)))
    basis = np.eye(3, dtype=np.float64)[index]
    return _normalize(basis - float(axis @ basis) * axis)


def _rot(axis: np.ndarray, angle: float) -> np.ndarray:
    return Rotation.from_rotvec(float(angle) * _normalize(axis)).as_matrix()


def _minimum_rotation(source: np.ndarray, target: np.ndarray) -> np.ndarray:
    source = _normalize(source)
    target = _normalize(target)
    cross = np.cross(source, target)
    cross_norm = float(np.linalg.norm(cross))
    dot = float(np.clip(source @ target, -1.0, 1.0))
    if cross_norm >= 1e-12:
        return _rot(cross / cross_norm, math.atan2(cross_norm, dot))
    if dot > 0.0:
        return np.eye(3, dtype=np.float64)
    return _rot(transverse(source), math.pi)


def base_axes() -> tuple[SyntheticAxis, ...]:
    edge = {name: (parent, child) for name, parent, child in SYNTHETIC_EDGES}
    result = []
    for k, name in enumerate(SYNTHETIC_HINGES):
        parent, child = edge[name]
        result.append(
            SyntheticAxis(
                name,
                parent,
                child,
                _normalize(np.array([1.0, 0.2 * ((-1.0) ** k), 0.1])),
                _normalize(np.array([0.9, -0.15, 0.2 * ((-1.0) ** k)])),
            )
        )
    return tuple(result)


def _dynamic_truth(
    count: int,
    truth_axes: tuple[SyntheticAxis, ...],
    *,
    nonideal: bool = False,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    time_s = np.arange(count, dtype=np.float64) / 20.0
    segment_index = {name: index for index, name in enumerate(SYNTHETIC_SEGMENTS)}
    hinge_by_name = {axis.name: (k, axis) for k, axis in enumerate(truth_axes)}
    truth = np.empty((count, len(SYNTHETIC_SEGMENTS), 3, 3), dtype=np.float64)
    ideal_relative = np.full((count, 4, 3, 3), np.nan, dtype=np.float64)
    transverse_axes = np.stack([transverse(axis.parent_axis) for axis in truth_axes])
    for n, time_value in enumerate(time_s):
        world: dict[str, np.ndarray] = {
            "pelvis": Rotation.from_rotvec(
                np.array(
                    [
                        0.18 * math.sin(0.31 * time_value),
                        0.12 * math.sin(0.23 * time_value + 0.4),
                        0.25 * math.sin(0.17 * time_value),
                    ],
                    dtype=np.float64,
                )
            ).as_matrix()
        }
        for edge_index, (name, parent, child) in enumerate(SYNTHETIC_EDGES):
            if name in hinge_by_name:
                hinge_index, axis = hinge_by_name[name]
                alpha = (0.7 + 0.05 * hinge_index) * math.sin(
                    2.0 * math.pi * (0.12 + 0.01 * hinge_index) * time_value
                    + 0.2 * hinge_index
                )
                q0 = _minimum_rotation(axis.child_axis, axis.parent_axis)
                ideal = _rot(axis.parent_axis, alpha) @ q0
                ideal_relative[n, hinge_index] = ideal
                if nonideal:
                    beta = math.radians(8.0) * math.sin(
                        2.0 * math.pi * 0.18 * time_value + 0.4 * hinge_index
                    )
                    relative = _rot(transverse_axes[hinge_index], beta) @ ideal
                else:
                    relative = ideal
            else:
                k = edge_index
                vector = np.array(
                    [
                        (0.15 + 0.02 * k)
                        * math.sin(2.0 * math.pi * (0.08 + 0.01 * k) * time_value + 0.31 * k),
                        (0.10 + 0.01 * k)
                        * math.sin(2.0 * math.pi * (0.11 + 0.007 * k) * time_value + 0.47 * k),
                        (0.08 + 0.005 * k)
                        * math.sin(2.0 * math.pi * (0.05 + 0.009 * k) * time_value + 0.19 * k),
                    ],
                    dtype=np.float64,
                )
                relative = Rotation.from_rotvec(vector).as_matrix()
            world[child] = world[parent] @ relative
        truth[n] = np.stack([world[name] for name in SYNTHETIC_SEGMENTS])
    return time_s, truth, ideal_relative


def _draw(seed: int, count: int) -> np.ndarray:
    generator = np.random.Generator(np.random.PCG64(seed))
    return generator.standard_normal((count, 10, 3), dtype=np.float64)


def _noise(draws: np.ndarray, sigma_deg: np.ndarray) -> np.ndarray:
    vectors = (math.pi / 180.0) * sigma_deg * draws
    return Rotation.from_rotvec(vectors.reshape(-1, 3)).as_matrix().reshape(
        vectors.shape[0], vectors.shape[1], 3, 3
    )


def make_case(name: str, *, seed_override: int | None = None) -> SyntheticCase:
    axes = base_axes()
    count = 201 if name in ("SYN07_STATIONARY_DEGENERACY", "SYN09_MONTE_CARLO_NOISE") else 401
    if name == "SYN08_FULL_CIRCLE_MULTISTART":
        raise ValueError("SYN08 has a dedicated fixture")

    truth_axes = axes
    nonideal = name == "SYN03_NONIDEAL_HUMAN_HINGE"
    if name == "SYN04_BILATERAL_VARIABILITY":
        modified = []
        for k, axis in enumerate(axes):
            side = 1.0 if k in (0, 2) else -1.0
            modified.append(
                SyntheticAxis(
                    axis.name,
                    axis.parent,
                    axis.child,
                    _rot(transverse(axis.parent_axis), side * math.radians(5.0)) @ axis.parent_axis,
                    _rot(transverse(axis.child_axis), side * math.radians(5.0)) @ axis.child_axis,
                )
            )
        truth_axes = tuple(modified)

    time_s, truth, ideal = _dynamic_truth(count, truth_axes, nonideal=nonideal)
    if name == "SYN07_STATIONARY_DEGENERACY":
        truth[:] = truth[0]
    measured = truth.copy()
    masks = np.ones((count, 10), dtype=bool)
    segment_index = {segment: index for index, segment in enumerate(SYNTHETIC_SEGMENTS)}
    axis_by_child = {axis.child: axis for axis in truth_axes}

    if name in ("SYN00_EXACT_DYNAMIC", "SYN03_NONIDEAL_HUMAN_HINGE", "SYN07_STATIONARY_DEGENERACY"):
        pass
    elif name in ("SYN01_DISTAL_TRANSVERSE_NOISE", "SYN09_MONTE_CARLO_NOISE"):
        seed = seed_override if seed_override is not None else 31001
        draws = _draw(seed, count)
        sigma = np.empty((1, 10, 3), dtype=np.float64)
        for segment, index in segment_index.items():
            sigma[0, index] = 1.0 if segment in DISTAL else 0.25
        noise = _noise(draws, sigma)
        for segment, index in segment_index.items():
            fixed = (
                _rot(transverse(axis_by_child[segment].child_axis), math.radians(6.0))
                if segment in DISTAL
                else np.eye(3)
            )
            measured[:, index] = truth[:, index] @ fixed @ noise[:, index]
    elif name == "SYN02_ANISOTROPIC_MOUNT_DRIFT":
        draws = _draw(31002, count)
        sigma = np.empty((1, 10, 3), dtype=np.float64)
        for segment, index in segment_index.items():
            sigma[0, index] = (1.0, 3.0, 6.0) if segment in DISTAL else (0.5, 0.5, 1.0)
        noise = _noise(draws, sigma)
        for segment, index in segment_index.items():
            mount_axis = _normalize(
                np.array([1.0 + 0.1 * index, 0.7 * ((-1.0) ** index), 0.3])
            )
            mount = _rot(mount_axis, math.radians(8.0 if segment in DISTAL else 5.0))
            if segment in DISTAL:
                drift_axis = transverse(mount_axis)
                drift = Rotation.from_rotvec(
                    np.outer(
                        np.radians(3.0)
                        * np.sin(2.0 * np.pi * time_s / 20.0 + 0.37 * index),
                        drift_axis,
                    )
                ).as_matrix()
            else:
                drift = np.broadcast_to(np.eye(3), (count, 3, 3))
            measured[:, index] = truth[:, index] @ mount @ drift @ noise[:, index]
    elif name == "SYN04_BILATERAL_VARIABILITY":
        draws = _draw(31004, count)
        noise = _noise(draws, np.ones((1, 10, 3), dtype=np.float64))
        measured = truth @ noise
    elif name == "SYN05_MOUNT_STEP":
        draws = _draw(31005, count)
        noise = _noise(draws, np.ones((1, 10, 3), dtype=np.float64))
        measured = truth @ noise
        index = segment_index["shank_left"]
        step = _rot(transverse(axis_by_child["shank_left"].child_axis), math.radians(10.0))
        measured[200:, index] = truth[200:, index] @ step @ noise[200:, index]
    elif name == "SYN06_MISSINGNESS":
        invalid = (np.arange(count) % 17 == 0) | (
            (np.arange(count) >= 160) & (np.arange(count) <= 179)
        )
        masks[invalid, :] = False
    else:
        raise KeyError(f"unknown synthetic case: {name}")

    return SyntheticCase(
        name,
        time_s,
        truth,
        measured,
        masks,
        axes,
        ideal if nonideal else None,
        np.stack([transverse(axis.parent_axis) for axis in axes]) if nonideal else None,
    )


def full_circle_fixture() -> tuple[np.ndarray, tuple[SyntheticAxis, ...], tuple[np.ndarray, ...]]:
    parent_axis = np.array([1.0, 0.0, 0.0], dtype=np.float64)
    child_axis = np.array([0.0, 1.0, 0.0], dtype=np.float64)
    child = _minimum_rotation(child_axis, parent_axis)
    measured = np.stack([np.eye(3, dtype=np.float64), child])
    axes = (SyntheticAxis("fixture_hinge", "pelvis", "child", parent_axis, child_axis),)
    start_axis = np.array([1.0, 2.0, 3.0], dtype=np.float64) / math.sqrt(14.0)
    starts = tuple(
        np.concatenate([np.zeros(3), angle * start_axis])
        for angle in (0.0, math.pi / 2, -math.pi / 2, math.pi, -math.pi, 3 * math.pi / 2, -3 * math.pi / 2, 2 * math.pi, -2 * math.pi)
    )
    return measured, axes, starts
