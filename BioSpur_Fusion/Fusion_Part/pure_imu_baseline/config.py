"""Frozen operational configuration shared by Capture 1/2/3."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

NODE_TO_SEGMENT = {
    "BSF31CC": "torso",
    "BSFC2CC": "pelvis",
    "BSFAA61": "upper_arm_left",
    "BSFEC35": "forearm_left",
    "BSF1120": "upper_arm_right",
    "BSFB165": "forearm_right",
    "BSF44AD": "thigh_left",
    "BSF6C53": "shank_left",
    "BSF3C79": "thigh_right",
    "BSF8BC4": "shank_right",
}
NODE_ORDER = tuple(NODE_TO_SEGMENT)
SEGMENT_ORDER = tuple(NODE_TO_SEGMENT.values())

PARENT_CHILD = (
    ("pelvis", "torso"),
    ("torso", "upper_arm_left"),
    ("upper_arm_left", "forearm_left"),
    ("torso", "upper_arm_right"),
    ("upper_arm_right", "forearm_right"),
    ("pelvis", "thigh_left"),
    ("thigh_left", "shank_left"),
    ("pelvis", "thigh_right"),
    ("thigh_right", "shank_right"),
)

# No usable subject measurements exist. These fixed, plausible values are an
# explicitly development-only avatar and are never fitted frame by frame.
GEOMETRY = {
    "schema": "biospur.fixed-development-skeleton.v1",
    "status": "FIXED_DEVELOPMENT_SKELETON_NOT_SUBJECT_ANTHROPOMETRY",
    "units": "m",
    "shoulder_width": 0.40,
    "hip_width": 0.30,
    "torso_length": 0.55,
    "upper_arm_left": 0.30,
    "upper_arm_right": 0.30,
    "forearm_left": 0.26,
    "forearm_right": 0.26,
    "thigh_left": 0.42,
    "thigh_right": 0.42,
    "shank_left": 0.43,
    "shank_right": 0.43,
}

@dataclass(frozen=True)
class CaptureSpec:
    capture_id: str
    raw_path: Path
    selection_mode: str
    selection_value: int
    calibration_duration_s: float
    pose_semantics: str
    epoch_label: str
    event_path: Path
    formal_status: str

CAPTURES = {
    "1": CaptureSpec(
        "v47_ten_node_body_calibration_20260814_093601",
        ROOT / "Fusion_Part/logs/v47_ten_node_body_calibration_20260814_093601/continuous_collector/fusion_host_raw.cobs.bin",
        "minimum_master_ms_for_record_selection_only", 327_059_947, 8.0,
        "natural standing, arms relaxed; labelled initial_still attempt 2",
        "DONNING_CAPTURE_1",
        ROOT / "Fusion_Part/logs/v47_ten_node_body_calibration_20260814_093601/ACTION_EVENTS.jsonl",
        "DEVELOPMENT_ONLY; FORMALLY_EXCLUDED_FROM_STRICT_THREE_CAPTURE_PROVENANCE",
    ),
    "2": CaptureSpec(
        "phase2_targeted_calibration_20260817t130918z_capture_2_with_joint_label_c8645eb2",
        ROOT / "Fusion_Part/datasets/phase2_calibration/phase2_targeted_calibration_20260817t130918z_capture_2_with_joint_label_c8645eb2/system/fusion_continuous/fusion_host_raw.cobs.bin",
        "exclusive_raw_byte_boundary", 213_952_702, 30.0,
        "natural standing with breathing and small postural adjustments; final accepted 00_initial_still attempt 2",
        "DONNING_CAPTURE_2",
        ROOT / "Fusion_Part/datasets/phase2_calibration/phase2_targeted_calibration_20260817t130918z_capture_2_with_joint_label_c8645eb2/system/fusion_continuous/COLLECTOR_EVENTS.jsonl",
        "ENGINEERING_DEVELOPMENT",
    ),
    "3": CaptureSpec(
        "phase3_upper_arm_twist_20260822T091236Z_upper_arm_20260822T091236Z",
        ROOT / "Fusion_Part/datasets/phase3_targeted_upper_arm/phase3_upper_arm_twist_20260822T091236Z_upper_arm_20260822T091236Z/system/fusion_continuous/fusion_host_raw.cobs.bin",
        "exclusive_raw_byte_boundary", 3_601_933, 20.0,
        "standing initial still, arms relaxed; labelled S00_INITIAL_STILL",
        "DONNING_CAPTURE_3",
        ROOT / "Fusion_Part/datasets/phase3_targeted_upper_arm/phase3_upper_arm_twist_20260822T091236Z_upper_arm_20260822T091236Z/ACTION_EVENTS.jsonl",
        "ENGINEERING_DEVELOPMENT",
    ),
}

REPLAY_RATE_HZ = 60.0
VIDEO_RATE_HZ = 20.0
NATIVE_RATE_HZ = 200.0
MAX_INTERPOLATION_GAP_S = 0.050
CALIBRATION_WINDOW_S = 2.0
