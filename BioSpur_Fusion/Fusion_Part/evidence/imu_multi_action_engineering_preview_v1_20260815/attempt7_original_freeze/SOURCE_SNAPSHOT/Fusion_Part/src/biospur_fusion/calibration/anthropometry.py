"""Versioned, fail-closed subject anthropometry for centerline calibration."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from biospur_fusion.calibration.articulated_batch import Geometry


REQUIRED_SCALARS = (
    "upper_arm_L_m", "upper_arm_R_m", "forearm_L_m", "forearm_R_m",
    "thigh_L_m", "thigh_R_m", "shank_L_m", "shank_R_m",
    "biacromial_width_m", "hip_width_m", "hip_vertical_offset_m", "c7_to_pelvis_m",
    "foot_length_L_m", "foot_length_R_m", "ankle_height_L_m", "ankle_height_R_m",
)
REQUIRED_OFFSETS = (
    "BSF31CC_C7", "BSFC2CC_PELVIS", "BSFAA61_ELBOW_L", "BSF1120_ELBOW_R",
    "BSFB165_WRIST_L", "BSFEC35_WRIST_R", "BSF44AD_KNEE_L", "BSF3C79_KNEE_R",
    "BSF6C53_ANKLE_L", "BSF8BC4_ANKLE_R",
)


@dataclass(frozen=True)
class Anthropometry:
    source_sha256: str
    scalars_m: dict[str, float]
    scalar_sigma_m: dict[str, float]
    offsets_segment_m: dict[str, np.ndarray]
    offset_sigma_m: dict[str, float]
    shoe_condition: str

    def geometry(self) -> Geometry:
        v = self.scalars_m
        return Geometry(
            torso_separation_m=v["c7_to_pelvis_m"],
            shoulder_half_width_m=v["biacromial_width_m"] / 2.0,
            shoulder_height_m=0.0,
            hip_half_width_m=v["hip_width_m"] / 2.0,
            hip_vertical_m=v["hip_vertical_offset_m"],
            upper_arm_L_m=v["upper_arm_L_m"], forearm_L_m=v["forearm_L_m"],
            upper_arm_R_m=v["upper_arm_R_m"], forearm_R_m=v["forearm_R_m"],
            thigh_L_m=v["thigh_L_m"], shank_L_m=v["shank_L_m"],
            thigh_R_m=v["thigh_R_m"], shank_R_m=v["shank_R_m"],
        )


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_anthropometry(path: Path) -> tuple[Anthropometry | None, dict]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    missing = []; invalid = []
    if payload.get("schema") != "biospur-subject-anthropometry-v1":
        invalid.append("schema")
    shoe = payload.get("shoe_condition", {})
    if shoe.get("status") == "UNKNOWN" or not shoe.get("description"):
        missing.append("shoe_condition")
    scalars = {}; scalar_sigma = {}
    for name in REQUIRED_SCALARS:
        row = payload.get("measurements", {}).get(name)
        if not row or row.get("status") != "MEASURED" or row.get("value") is None or row.get("uncertainty") is None:
            missing.append(f"measurements.{name}"); continue
        value = row["value"]; sigma = row["uncertainty"]
        positive_required = name != "hip_vertical_offset_m"
        if (row.get("units") != "m" or not isinstance(value, (int, float))
                or not np.isfinite(value) or (positive_required and not value > 0)):
            invalid.append(f"measurements.{name}.value"); continue
        if not isinstance(sigma, (int, float)) or not sigma > 0:
            invalid.append(f"measurements.{name}.uncertainty"); continue
        if not row.get("landmark_definition") or not row.get("measurement_method"):
            invalid.append(f"measurements.{name}.provenance"); continue
        scalars[name] = float(value); scalar_sigma[name] = float(sigma)
    offsets = {}; offset_sigma = {}
    for name in REQUIRED_OFFSETS:
        row = payload.get("sensor_to_landmark_offsets", {}).get(name)
        if not row or row.get("status") != "MEASURED" or row.get("value") is None or row.get("uncertainty") is None:
            missing.append(f"sensor_to_landmark_offsets.{name}"); continue
        value = np.asarray(row["value"], float); sigma = row["uncertainty"]
        if value.shape != (3,) or not np.isfinite(value).all() or row.get("units") != "m":
            invalid.append(f"sensor_to_landmark_offsets.{name}.value"); continue
        if not isinstance(sigma, (int, float)) or not sigma > 0:
            invalid.append(f"sensor_to_landmark_offsets.{name}.uncertainty"); continue
        if not row.get("landmark_definition") or not row.get("measurement_method"):
            invalid.append(f"sensor_to_landmark_offsets.{name}.provenance"); continue
        offsets[name] = value; offset_sigma[name] = float(sigma)
    audit = {
        "schema": "biospur-anthropometry-validation-v1", "path": str(path.resolve()),
        "sha256": sha256(path), "missing": sorted(missing), "invalid": sorted(invalid),
        "complete": not missing and not invalid,
        "capture_derived_body_dimensions_allowed": False,
    }
    if missing or invalid:
        return None, audit
    return Anthropometry(audit["sha256"], scalars, scalar_sigma, offsets, offset_sigma,
                         str(shoe["status"])), audit
