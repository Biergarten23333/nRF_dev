"""Frozen Stage 2 review configuration."""
from __future__ import annotations

from pathlib import Path

STAGE1_ROOT = Path("/tmp/biospur_pure_imu_baseline_c123_20260823T091031Z")
CAPTURES = ("1", "2", "3")
REQUIRED_C2_TIMES_S = (10.10, 1130.45, 1198.35)

# Recorded before Stage 2 implementation. The repository is intentionally very
# dirty; the compact digest preserves that state without copying 106k entries.
PRE_IMPLEMENTATION_REPOSITORY_STATE = {
    "head": "5bd0a3aa5803921d39f1e7d317762a8f6d7bc3bb",
    "git_status_porcelain_v1_z_sha256": "ff3d62b622a8567d5d94995eae1c4c5f0c67462e8061cf789bef8f883de2fe6d",
    "git_status_entry_count": 106508,
    "stage1_scope_status": ["?? pure_imu_baseline/"],
}

# Joint-space topology. Each tuple is (metric name, proximal joint, distal
# joint, display class). Geometry remains the immutable Stage 1 geometry.
BONES = (
    ("torso_length", "pelvis", "torso_top", "core"),
    ("shoulder_width", "shoulder_left", "shoulder_right", "core"),
    ("upper_arm_left", "shoulder_left", "elbow_left", "left"),
    ("forearm_left", "elbow_left", "wrist_left", "left"),
    ("upper_arm_right", "shoulder_right", "elbow_right", "right"),
    ("forearm_right", "elbow_right", "wrist_right", "right"),
    ("hip_width", "hip_left", "hip_right", "pelvis"),
    ("thigh_left", "hip_left", "knee_left", "left"),
    ("shank_left", "knee_left", "ankle_left", "left"),
    ("thigh_right", "hip_right", "knee_right", "right"),
    ("shank_right", "knee_right", "ankle_right", "right"),
)

SEGMENT_ORIGIN_JOINT = {
    "torso": "pelvis",
    "pelvis": "pelvis",
    "upper_arm_left": "shoulder_left",
    "forearm_left": "elbow_left",
    "upper_arm_right": "shoulder_right",
    "forearm_right": "elbow_right",
    "thigh_left": "hip_left",
    "shank_left": "knee_left",
    "thigh_right": "hip_right",
    "shank_right": "knee_right",
}

CALIBRATION_WINDOWS_S = {
    "1": (1.50, 3.50),
    "2": (25.25, 27.25),
    "3": (16.75, 18.75),
}

CAMERA_PROJECTIONS = {
    "WORLD_FIXED_FRONT": (1, 2),  # screen axes: global +Y, +Z
    "WORLD_FIXED_SIDE": (0, 2),   # screen axes: global +X, +Z
    "WORLD_FIXED_TOP": (1, 0),    # screen axes: global +Y, +X
}

VIEWER_PAYLOAD_CHUNK_CHARS = 1_048_576
