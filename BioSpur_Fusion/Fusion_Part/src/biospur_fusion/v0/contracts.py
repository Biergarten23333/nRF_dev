"""Fail-closed V0 identities, calibration authority, and product boundary."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping


V0_VERSION = "V0"
RELEASE_MODE_SCHEMA = "biospur-fusion-v0-release-mode-v1"
NODES = (
    "BSFEC35", "BSFB165", "BSFAA61", "BSF1120", "BSF31CC",
    "BSFC2CC", "BSF44AD", "BSF3C79", "BSF6C53", "BSF8BC4",
)
IDENTITY = {
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
COMMON_NINE = tuple(node for node in NODES if node != "BSF31CC")
HARDWARE_FAMILY = {node: "COMMON_NINE" for node in COMMON_NINE} | {"BSF31CC": "BSF31CC_DISTINCT"}

# Capture1 calibration authority. These are exact half-open intervals, never
# motion-statistic guesses. No ordinary or held-out action is present here.
WINDOWS = (
    ("initial_still2", 2986078873797, 2994078940466),
    ("t_pose", 3019030103768, 3027030170523),
    ("arms", 3065724244760, 3212615253685),
    ("left_elbow", 3371591610404, 3411475048316),
    ("right_elbow2", 3494725933278, 3528015255640),
    ("left_knee", 3551740910191, 3579592651754),
    ("right_knee", 3602476636179, 3627048980515),
    ("left_heel", 3666677754354, 3687781716166),
    ("right_heel", 3712252142978, 3737709976189),
    ("squats", 3761427161163, 3785916206867),
    ("trunk", 3814053447917, 3854622450716),
)
SUPERSEDED_INITIAL_STILL = (2924756071417, 2954756071417)
HELD_OUT_LABELS = frozenset({"golf", "golf_swing", "boxing"})


@dataclass(frozen=True)
class V0Config:
    path: Path
    payload: Mapping[str, Any]
    sha256: str

    def section(self, name: str) -> Mapping[str, Any]:
        value = self.payload[name]
        if not isinstance(value, Mapping):
            raise TypeError(f"config section {name} must be a mapping")
        return value


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def dump_json(path: Path, value: Any) -> None:
    Path(path).write_text(
        json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def load_config(path: Path) -> V0Config:
    path = Path(path).resolve()
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("biospur_fusion_version") != V0_VERSION:
        raise ValueError("configuration is not BioSpur Fusion V0")
    if payload.get("primary_mode") != "TEN_NODE_MAGNETOMETER_FREE_6AXIS_IMU":
        raise ValueError("V0 primary mode changed")
    if payload.get("uwb", {}) != {
        "beacon_timebase_used": True,
        "ranging_used": False,
        "position_aid_used": False,
        "calibration_residual_used": False,
        "pose_correction_used": False,
        "derived_spatial_parameters_used": False,
    }:
        raise ValueError("V0 UWB boundary changed")
    configured = tuple(
        (row["label"], int(row["start_global_time_ns"]), int(row["stop_global_time_ns_exclusive"]))
        for row in payload["capture1_windows"]
    )
    if configured != WINDOWS:
        raise ValueError("Capture1 calibration windows do not match authority")
    mapping = payload.get("identity")
    if mapping != IDENTITY or mapping.get("BSFC2CC") != "pelvis" or "BSFC22C" in mapping:
        raise ValueError("ten-node identity guard failed")
    return V0Config(path, payload, sha256_file(path))


def load_release_mode(path: Path) -> Mapping[str, Any]:
    """Load the product-mode overlay without changing calibration provenance."""
    path = Path(path).resolve()
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema") != RELEASE_MODE_SCHEMA:
        raise ValueError("release-mode schema changed")
    if payload.get("biospur_fusion_version") != V0_VERSION:
        raise ValueError("release mode is not BioSpur Fusion V0")
    if payload.get("selected_v0_mode") != "QMT_OFF":
        raise ValueError("conservative V0 release mode is not QMT_OFF")
    if payload.get("selected_variant") != "qmt_off":
        raise ValueError("QMT_OFF variant binding changed")
    if payload.get("selected_without_shared_ik_variant") != "qmt_off_no_shared_ik":
        raise ValueError("QMT_OFF no-IK binding changed")
    if payload.get("always_on_qmt_variant") != "always_on_qmt":
        raise ValueError("always-on qmt diagnostic binding changed")
    if payload.get("confidence_gated_qmt") != "NOT_JUSTIFIED":
        raise ValueError("unqualified confidence-gated qmt entered V0")
    if payload.get("heading_evidence_confidence_contract") != "ZERO_NO_INDEPENDENT_HEADING_OBSERVATION":
        raise ValueError("QMT_OFF heading-evidence contract is not conservative")
    if payload.get("missing_heading_uncertainty_term") != "PRESERVED_FOR_EVERY_SEGMENT":
        raise ValueError("QMT_OFF missing-heading uncertainty was erased")
    if payload.get("attitude_observation_weight_contract") != "ONE_WHEN_NONDEGRADED_ZERO_WHEN_DEGRADED":
        raise ValueError("QMT_OFF VQF attitude observation contract changed")
    if payload.get("attitude_and_heading_confidence") != "DECOUPLED":
        raise ValueError("attitude weight and heading evidence are conflated")
    return payload | {"path": str(path), "sha256": sha256_file(path)}


def assert_profile_boundary(profile: Mapping[str, Any]) -> None:
    if profile.get("biospur_fusion_version") != V0_VERSION:
        raise ValueError("profile is not V0")
    if profile.get("identity") != IDENTITY:
        raise ValueError("profile identity map changed")
    if profile.get("hardware_family") != HARDWARE_FAMILY:
        raise ValueError("profile hardware-family boundary changed")
    isolation = profile.get("uwb_isolation", {})
    forbidden = (
        "ranges", "anchor_geometry", "v4_transform", "lever_arms",
        "layer_c_geometry", "root_translation", "pose_correction",
    )
    if any(name in isolation.get("profile_fields", ()) for name in forbidden):
        raise ValueError("UWB-derived profile field entered V0")
    if not isolation.get("imu_only", False):
        raise ValueError("V0 profile is not declared IMU-only")
    labels = set(profile.get("calibration_windows", {}))
    if labels != {row[0] for row in WINDOWS}:
        raise ValueError("profile calibration lifecycle is incomplete")
    if profile.get("held_out_golf_boxing_accessed") is not False:
        raise ValueError("held-out access guard failed")
