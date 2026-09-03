"""Fail-closed authority and lifecycle contracts for the C2 basis repair."""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Mapping

from biospur_fusion.v0.contracts import IDENTITY as ACTIVE_V0_IDENTITY
from biospur_fusion.v0.raw6_heading import EDGES, SEGMENTS


BASIS_VERSION = "c2-basis-v1"
CAPTURE_ID = "phase2_targeted_calibration_20260817t130918z_capture_2_with_joint_label_c8645eb2"
CAPTURE_REL = Path("datasets/phase2_calibration") / CAPTURE_ID
SEALED_IDENTITY_REL = CAPTURE_REL / "identity/SEALED_NODE_TO_BODY_GROUND_TRUTH.json"
WEAR_AMENDMENT_REL = CAPTURE_REL / "identity/POST_SEAL_WEAR_DIRECTION_AMENDMENT_004.json"
FRAME_SEMANTICS_REL = CAPTURE_REL / "identity/POST_SEAL_FRAME_SEMANTICS_AMENDMENT_005.json"
ANTHROPOMETRY_REL = Path("config/body_calibration_v4_1/v47_subject_surface_anthropometry_20260828.json")
CONFIG_REL = Path("config/biospur_fusion_v0_c2_basis/config_v1.json")

C2_IDENTITY = {
    "BSFEC35": "forearm_left",
    "BSFB165": "forearm_right",
    "BSFAA61": "upper_arm_left",
    "BSF1120": "upper_arm_right",
    "BSF31CC": "torso",
    "BSFC2CC": "pelvis",
    "BSF44AD": "thigh_left",
    "BSF3C79": "thigh_right",
    "BSF6C53": "shank_left",
    "BSF8BC4": "shank_right",
}

# These are metadata-selected accepted attempts. Every entry is a complete
# rest->transition->action->transition->rest episode and enters one persistent
# profile in this exact chronological order. Validation rows are blocked
# within episodes; there are no per-action profiles.
EPISODE_SELECTION = (
    ("00_initial_still", 2),
    ("02_t_pose", 3),
    ("03_pelvis_hula_circle", 2),
    ("04_shoulder_left", 3),
    ("05_shoulder_right", 3),
    ("06_elbow_left", 2),
    ("07_elbow_right", 1),
    ("08_hip_left", 1),
    ("09_hip_right", 1),
    ("10_knee_left_seated", 1),
    ("11_knee_right_seated", 1),
    ("12_heel_raise_left", 1),
    ("13_heel_raise_right", 1),
    ("14_trunk_flex_extend", 1),
    ("15_trunk_axial_rotation", 1),
    ("16_squat", 1),
    ("17_final_still", 1),
    ("18_heel_to_butt_left", 1),
    ("19_heel_to_butt_right", 1),
)

FORBIDDEN_PATH_TOKENS = (
    "capture1", "capture_1", "capture3", "capture_3", "/holdout/h",
    "boxing", "golf", "uwb", "candidate_lock", "freeze",
)


@dataclass(frozen=True)
class StageBudget:
    name: str
    max_iterations: int
    wall_limit_s: float
    multistarts: int = 1
    parallel_workers: int = 1


def load_config(root: Path) -> dict[str, Any]:
    payload = json.loads((Path(root) / CONFIG_REL).read_text(encoding="utf-8"))
    if payload.get("schema") != "biospur-pure-imu-v0-c2-basis-config-v1":
        raise ValueError("C2 basis config schema changed")
    if payload.get("capture") != "CAPTURE2_ONLY":
        raise ValueError("C2-only scope changed")
    if payload["geometry"].get("lengths_are_estimator_coordinates") is not False:
        raise ValueError("bone lengths entered the estimator state")
    return payload


def stage_budget(config: Mapping[str, Any], name: str) -> StageBudget:
    row = config["stages"][name]
    return StageBudget(
        name=name,
        max_iterations=int(row["max_iterations"]),
        wall_limit_s=float(row["wall_limit_s"]),
        multistarts=int(row.get("multistarts", 1)),
        parallel_workers=int(row.get("parallel_workers", 1)),
    )


def _identity_rows(payload: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    rows = {str(row["hardware_id"]): row for row in payload["rows"]}
    if len(rows) != 10:
        raise ValueError("sealed C2 identity must contain ten unique nodes")
    return rows


def load_c2_authority(root: Path) -> dict[str, Any]:
    """Load only the immutable C2 metadata authorities and fail on aliases."""

    root = Path(root)
    sealed_path = root / SEALED_IDENTITY_REL
    amendment_path = root / WEAR_AMENDMENT_REL
    frame_semantics_path = root / FRAME_SEMANTICS_REL
    sealed = json.loads(sealed_path.read_text(encoding="utf-8"))
    amendment = json.loads(amendment_path.read_text(encoding="utf-8"))
    frame_semantics = json.loads(frame_semantics_path.read_text(encoding="utf-8"))
    if sealed.get("schema") != "biospur-node-to-body-ground-truth-v1":
        raise ValueError("sealed C2 identity schema changed")
    rows = _identity_rows(sealed)
    observed = {node: str(row["body_segment"]) for node, row in rows.items()}
    if observed != C2_IDENTITY or ACTIVE_V0_IDENTITY != C2_IDENTITY:
        raise ValueError("C2 sealed identity conflicts with active V0 identity")
    if amendment.get("append_only") is not True:
        raise ValueError("wear direction authority is not append-only")
    if amendment.get("capture_session_id") != CAPTURE_ID:
        raise ValueError("wear direction amendment belongs to another capture")
    amendment_rows = {
        str(row["hardware_id"]): row for row in amendment.get("rows", [])
    }
    if {node: row.get("body_segment") for node, row in amendment_rows.items()} != C2_IDENTITY:
        raise ValueError("wear amendment identity differs from sealed C2 mapping")
    if amendment["uncertainty_contract"].get("not_exact_vectors") is not True:
        raise ValueError("qualitative direction was promoted to exact truth")
    for node in ("BSF6C53", "BSF8BC4"):
        if amendment_rows[node].get("mount_surface") != "lateral_shank_not_front":
            raise ValueError(f"{node}: lateral shank semantics changed")
        if "not front-mounted" not in rows[node]["mount_landmark"]:
            raise ValueError(f"{node}: sealed lateral shank landmark changed")
    if frame_semantics.get("append_only") is not True:
        raise ValueError("node-frame semantics authority is not append-only")
    if frame_semantics.get("capture_session_id") != CAPTURE_ID:
        raise ValueError("node-frame semantics belongs to another capture")
    comparison = frame_semantics.get("comparison_contract", {})
    if not comparison.get("transform_each_node_to_common_anatomical_frame_before_bilateral_comparison"):
        raise ValueError("node-wise frame transform contract changed")
    if not comparison.get("raw_left_right_xyz_equality_forbidden"):
        raise ValueError("raw bilateral xyz equality was enabled")
    return {
        "identity": dict(C2_IDENTITY),
        "sealed": sealed,
        "sealed_path": sealed_path,
        "amendment": amendment,
        "amendment_path": amendment_path,
        "frame_semantics": frame_semantics,
        "frame_semantics_path": frame_semantics_path,
        "wear_by_node": amendment_rows,
        "segments": tuple(SEGMENTS),
        "edges": tuple(EDGES),
    }


def assert_c2_path(path: Path, root: Path) -> None:
    relative = Path(path).resolve().relative_to(Path(root).resolve())
    lowered = "/" + relative.as_posix().lower()
    if any(token in lowered for token in FORBIDDEN_PATH_TOKENS):
        raise ValueError(f"forbidden non-C2 path: {relative}")
