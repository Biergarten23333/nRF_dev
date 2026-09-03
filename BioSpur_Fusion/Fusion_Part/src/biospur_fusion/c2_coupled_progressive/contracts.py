"""Fail-closed contracts for the clean C2 coupled-progressive implementation."""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[3]
BASE_CONFIG = ROOT / "config/c2_coupled_progressive_v1/config.json"
AMENDMENT_001 = ROOT / "config/c2_coupled_progressive_v1/AMENDMENT_001_REMOVE_TORSO_SCALAR.json"
RUN_DIR = ROOT / "logs/c2_coupled_progressive_20260831_082131"

NODE_TO_SEGMENT = {
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
SEGMENT_TO_NODE = {segment: node for node, segment in NODE_TO_SEGMENT.items()}


@dataclass(frozen=True)
class Edge:
    name: str
    parent: str
    child: str
    joint_kind: str


EDGES = (
    Edge("pelvis_torso", "pelvis", "torso", "connection"),
    Edge("shoulder_left", "torso", "upper_arm_left", "connection"),
    Edge("elbow_left", "upper_arm_left", "forearm_left", "hinge"),
    Edge("shoulder_right", "torso", "upper_arm_right", "connection"),
    Edge("elbow_right", "upper_arm_right", "forearm_right", "hinge"),
    Edge("hip_left", "pelvis", "thigh_left", "connection"),
    Edge("knee_left", "thigh_left", "shank_left", "hinge"),
    Edge("hip_right", "pelvis", "thigh_right", "connection"),
    Edge("knee_right", "thigh_right", "shank_right", "hinge"),
)
HINGE_EDGES = tuple(edge for edge in EDGES if edge.joint_kind == "hinge")
CONNECTION_EDGES = tuple(edge for edge in EDGES if edge.joint_kind == "connection")

EPISODES = (
    "00_initial_still", "02_t_pose", "03_pelvis_hula_circle", "04_shoulder_left",
    "05_shoulder_right", "06_elbow_left", "07_elbow_right", "08_hip_left",
    "09_hip_right", "10_knee_left_seated", "11_knee_right_seated",
    "12_heel_raise_left", "13_heel_raise_right", "14_trunk_flex_extend",
    "15_trunk_axial_rotation", "16_squat", "17_final_still",
    "18_heel_to_butt_left", "19_heel_to_butt_right",
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"object JSON required: {path}")
    return value


def load_effective_config() -> dict[str, Any]:
    """Apply the immutable append-only correction without mutating sealed history."""

    base = _json(BASE_CONFIG)
    amendment = _json(AMENDMENT_001)
    run_amendment = _json(RUN_DIR / "RUN_START_AMENDMENT_001.json")
    if amendment.get("append_only") is not True:
        raise RuntimeError("effective config amendment is not append-only")
    binding = run_amendment["effective_config_amendment"]
    if binding["sha256"] != sha256(AMENDMENT_001):
        raise RuntimeError("effective config amendment hash changed")
    if run_amendment["sealed_base_config"]["sha256"] != sha256(BASE_CONFIG):
        raise RuntimeError("sealed base config hash changed")
    effective = copy.deepcopy(base)
    removed = effective["proxy_geometry"].pop("pelvis_to_acromion_line_proxy_m", None)
    if not isinstance(removed, dict) or removed.get("nominal") != 0.425:
        raise RuntimeError("historical torso defect not found exactly once")
    torso_change = amendment["effective_changes"][1]["value"]
    hip_change = amendment["effective_changes"][2]["value"]
    effective["proxy_geometry"]["torso_display_geometry"] = copy.deepcopy(torso_change)
    effective["proxy_geometry"]["trochanter_proxy_span_m"].update(copy.deepcopy(hip_change))
    effective["effective_amendments"] = [{
        "path": str(AMENDMENT_001.relative_to(ROOT)), "sha256": sha256(AMENDMENT_001),
    }]
    assert_effective_config(effective)
    return effective


def assert_effective_config(config: dict[str, Any]) -> None:
    architecture = config["architecture"]
    if config["node_to_segment"] != NODE_TO_SEGMENT:
        raise RuntimeError("node mapping conflicts with authoritative C2 map")
    if [tuple(row) for row in config["edges"]] != [(e.parent, e.child) for e in EDGES]:
        raise RuntimeError("nine-edge tree changed")
    if architecture["generic_window_evaluator_accepts_action_name"] is not False:
        raise RuntimeError("action-label factor routing re-entered")
    if architecture["persistent_posterior_instances"] != 1 or architecture["prefix_batch_refits"] is not False:
        raise RuntimeError("persistent posterior ownership changed")
    if not architecture["time_varying_quat2corr_consumed"] or not architecture["time_varying_delta_filt_consumed"]:
        raise RuntimeError("time-varying QMT output disabled")
    if architecture["sensor_origin_is_joint_or_display_point"] is not False:
        raise RuntimeError("sensor origin promoted to anatomy")
    proxy = config["proxy_geometry"]
    if "pelvis_to_acromion_line_proxy_m" in proxy:
        raise RuntimeError("forbidden 0.425 m torso scalar remains effective")
    torso = proxy["torso_display_geometry"]
    if torso["status"] != "UNOBSERVED_NO_SINGLE_REPRESENTATIVE" or torso["calibration_consumer"]:
        raise RuntimeError("unobserved torso geometry was promoted")
    hip = proxy["trochanter_proxy_span_m"]
    if hip["internal_hip_center_spacing"] or hip["may_define_display_hip_joint"]:
        raise RuntimeError("surface trochanter span became hip-center spacing")


def tree_is_connected() -> bool:
    children = {edge.child for edge in EDGES}
    parents = {edge.parent for edge in EDGES}
    return len(EDGES) == 9 and "pelvis" in parents - children and len(children) == 9
