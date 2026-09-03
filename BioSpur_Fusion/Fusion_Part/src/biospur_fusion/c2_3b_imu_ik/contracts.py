"""Frozen, non-biological implementation contracts for C2 3B."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

import numpy as np


WORKSPACE = Path(__file__).resolve().parents[3]

SEGMENTS = (
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

EDGE_ROWS = (
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
EDGES = tuple(row[0] for row in EDGE_ROWS)
HINGES = ("elbow_left", "elbow_right", "knee_left", "knee_right")
DISTAL_SEGMENTS = (
    "forearm_left",
    "forearm_right",
    "shank_left",
    "shank_right",
)
PRIMARY_EPISODES = tuple(f"{index:02d}" for index in range(19))
HOLDOUT_EPISODES = ("H01_boxing", "H02_golf")
ALL_EPISODES = PRIMARY_EPISODES + HOLDOUT_EPISODES

POINT_NAMES = (
    "pelvis_center",
    "shoulder_mid",
    "shoulder_left",
    "shoulder_right",
    "hip_left",
    "hip_right",
    "elbow_left",
    "wrist_left",
    "elbow_right",
    "wrist_right",
    "knee_left",
    "ankle_left",
    "knee_right",
    "ankle_right",
)

LINK_ROWS = (
    ("torso", "pelvis_center", "shoulder_mid"),
    ("shoulder_bar", "shoulder_left", "shoulder_right"),
    ("hip_bar", "hip_left", "hip_right"),
    ("upper_arm_left", "shoulder_left", "elbow_left"),
    ("forearm_left", "elbow_left", "wrist_left"),
    ("upper_arm_right", "shoulder_right", "elbow_right"),
    ("forearm_right", "elbow_right", "wrist_right"),
    ("thigh_left", "hip_left", "knee_left"),
    ("shank_left", "knee_left", "ankle_left"),
    ("thigh_right", "hip_right", "knee_right"),
    ("shank_right", "knee_right", "ankle_right"),
)

CROSSING_LINK_PAIRS = (
    ("thigh_left", "thigh_right"),
    ("thigh_left", "shank_right"),
    ("shank_left", "thigh_right"),
    ("shank_left", "shank_right"),
)


@dataclass(frozen=True)
class AxisPair:
    name: str
    parent: str
    child: str
    parent_axis: np.ndarray
    child_axis: np.ndarray


@dataclass(frozen=True)
class Profile:
    name: str
    distal_weight: float
    axis_weight: float

    def weights(self) -> Mapping[str, float]:
        return MappingProxyType(
            {
                segment: self.distal_weight if segment in DISTAL_SEGMENTS else 1.0
                for segment in SEGMENTS
            }
        )


PROFILES = tuple(
    Profile(f"{weight_name}_projector_{axis_weight:g}", weight, axis_weight)
    for weight_name, weight in (
        ("uniform", 1.0),
        ("distal_w_0.5", 0.5),
        ("distal_w_0.25", 0.25),
    )
    for axis_weight in (0.25, 0.5, 1.0)
)
CENTRAL_PROFILE = next(
    profile
    for profile in PROFILES
    if profile.distal_weight == 1.0 and profile.axis_weight == 1.0
)

PROJECTOR_HUBER_CHORD = float(np.sin(np.deg2rad(10.0)))
CUT_LOCUS_DOT_TOL = 64.0 * np.finfo(np.float64).eps
SO3_LOG_CUT_TOL = 64.0 * np.finfo(np.float64).eps
ABSOLUTE_JACOBIAN_STEP_RAD = 1e-6
ABSOLUTE_JACOBIAN_REFINEMENT_STEP_RAD = 5e-7


@dataclass(frozen=True)
class EpisodeData:
    key: str
    time_s: np.ndarray
    matrices: np.ndarray
    segment_masks: np.ndarray
    full_body_valid: np.ndarray


@dataclass(frozen=True)
class DisplayGeometry:
    name: str
    torso_height_m: float
    hip_span_m: float
    shoulder_span_m: float
    segment_lengths_m: Mapping[str, float]


@dataclass(frozen=True)
class FrameSolution:
    matrices: np.ndarray
    valid: bool
    scipy_success: bool
    finite: bool
    proper: bool
    converged: bool
    message: str
    cost: float
    optimality: float
    nfev: int
    retractions: int
    final_step_norm_rad: float
    wall_s: float
    njev: int = 0
    derivative_mode: str = "unknown"
    jacobian_residual_evaluations: int = 0
    effective_step_min_rad: float = float("nan")
    effective_step_max_rad: float = float("nan")
    orientation_cost: float = float("nan")
    axis_cost: float = float("nan")
    pre_cut_finite: bool = False
    pre_cut_proper: bool = False
    cut_start: bool = False
    cut_final: bool = False
    retraction_trace: tuple[Mapping[str, object], ...] = ()


@dataclass(frozen=True)
class FinalizedFrameState:
    matrices: np.ndarray
    cut_final: bool
    pre_cut_finite: bool
    pre_cut_proper: bool
    finite: bool
    proper: bool
    converged: bool
    valid: bool
    message: str


@dataclass(frozen=True)
class EpisodeSolution:
    episode: str
    profile: str
    matrices: np.ndarray
    valid: np.ndarray
    frame_wall_s: np.ndarray
    nfev: np.ndarray
    costs: np.ndarray
    optimality: np.ndarray
    final_step_norm_rad: np.ndarray
    messages: tuple[str, ...]


RENDER_FRAMES = MappingProxyType(
    {
        "00": (70, 350, 630),
        "06": (70, 350, 630),
        "09": (70, 350, 630),
        "H01_boxing": (60, 300, 540),
        "H02_golf": (59, 299, 539),
    }
)

WORLD_UP_DISPLAY = np.array([0.0, 0.0, 1.0], dtype=np.float64)
DISPLAY_SCOPE = "ZERO_POSE_CHANGE_DISPLAY_PROXY_REGRESSION_ONLY"
SEGMENT_FRAME_SCOPE = "SEALED_C2_SEGMENT_FRAME_NOT_CERTIFIED_ANATOMICAL"
