"""V4.1 provenance-separated anthropometry and sensor-placement inputs.

The validator is deliberately fail closed.  Surface measurements, anatomical
derivations, PCB geometry, capture placement, and rendering metadata are never
silently substituted for one another.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

import numpy as np

from biospur_fusion.calibration.articulated_batch import Geometry


DIRECT_SOLVER_REQUIRED = (
    "acromion_to_lateral_epicondyle_L",
    "acromion_to_lateral_epicondyle_R",
    "lateral_epicondyle_to_wrist_styloid_midpoint_L",
    "lateral_epicondyle_to_wrist_styloid_midpoint_R",
    "greater_trochanter_to_knee_landmark_L",
    "greater_trochanter_to_knee_landmark_R",
    "knee_landmark_to_malleolar_midpoint_L",
    "knee_landmark_to_malleolar_midpoint_R",
    "biacromial_breadth",
    "ASIS_breadth",
    "C7_to_mid_PSIS",
)

DERIVED_SOLVER_REQUIRED = (
    "upper_arm_joint_centre_length_L",
    "upper_arm_joint_centre_length_R",
    "forearm_joint_centre_length_L",
    "forearm_joint_centre_length_R",
    "thigh_joint_centre_length_L",
    "thigh_joint_centre_length_R",
    "shank_joint_centre_length_L",
    "shank_joint_centre_length_R",
    "shoulder_joint_centre_width",
    "hip_joint_centre_width",
    "hip_joint_centre_vertical_offset",
    "C7_to_pelvis_reference",
)

RENDERING_REQUIRED = (
    "foot_length_L",
    "foot_length_R",
    "floor_to_malleolar_midpoint_L",
    "floor_to_malleolar_midpoint_R",
    "shoe_geometry",
)

NODE_TO_SEGMENT = {
    "BSFC2CC": "Pelvis",
    "BSF31CC": "Torso",
    "BSFAA61": "UpperArm_L",
    "BSFB165": "Forearm_L",
    "BSF1120": "UpperArm_R",
    "BSFEC35": "Forearm_R",
    "BSF44AD": "Thigh_L",
    "BSF6C53": "Shank_L",
    "BSF3C79": "Thigh_R",
    "BSF8BC4": "Shank_R",
}
SEGMENT_TO_NODE = {segment: node for node, segment in NODE_TO_SEGMENT.items()}
PLACEMENT_STATUSES = {
    "MEASURED_CAPTURE_DAY", "PHOTO_DERIVED", "CALIBRATION_ESTIMATED", "MISSING",
}


@dataclass(frozen=True)
class SensorPlacementV41:
    node: str
    segment: str
    landmark: str
    pcb_phase_centre_to_enclosure_m: np.ndarray
    capture_prior_m: np.ndarray
    capture_sigma_m: np.ndarray
    capture_lower_m: np.ndarray
    capture_upper_m: np.ndarray
    capture_status: str
    capture_source: str
    estimate_as_nuisance: bool

    @property
    def phase_centre_to_landmark_prior_m(self) -> np.ndarray:
        return self.pcb_phase_centre_to_enclosure_m + self.capture_prior_m


@dataclass(frozen=True)
class AnthropometryV41:
    source_sha256: str
    direct_surface_m: Mapping[str, float]
    direct_surface_sigma_m: Mapping[str, float]
    derived_joint_center_m: Mapping[str, float]
    derived_joint_center_sigma_m: Mapping[str, float]
    placements: Mapping[str, SensorPlacementV41]
    uncertainty_mode: str = "FIXED_INPUTS_NOT_PROPAGATED"

    def geometry(self) -> Geometry:
        value = self.derived_joint_center_m
        return Geometry(
            torso_separation_m=value["C7_to_pelvis_reference"],
            shoulder_half_width_m=value["shoulder_joint_centre_width"] / 2.0,
            shoulder_height_m=0.0,
            hip_half_width_m=value["hip_joint_centre_width"] / 2.0,
            hip_vertical_m=value["hip_joint_centre_vertical_offset"],
            upper_arm_L_m=value["upper_arm_joint_centre_length_L"],
            forearm_L_m=value["forearm_joint_centre_length_L"],
            upper_arm_R_m=value["upper_arm_joint_centre_length_R"],
            forearm_R_m=value["forearm_joint_centre_length_R"],
            thigh_L_m=value["thigh_joint_centre_length_L"],
            shank_L_m=value["shank_joint_centre_length_L"],
            thigh_R_m=value["thigh_joint_centre_length_R"],
            shank_R_m=value["shank_joint_centre_length_R"],
        )


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _finite_scalar(row: dict, key: str, *, signed: bool = False) -> float | None:
    value = row.get(key)
    if not isinstance(value, (int, float)) or not np.isfinite(value):
        return None
    if not signed and value <= 0:
        return None
    return float(value)


def _vector(row: dict, key: str) -> np.ndarray | None:
    value = row.get(key)
    if value is None:
        return None
    array = np.asarray(value, float)
    return array if array.shape == (3,) and np.isfinite(array).all() else None


def _validate_surface(payload: dict, missing: list[str], invalid: list[str]):
    values: dict[str, float] = {}
    sigmas: dict[str, float] = {}
    rows = payload.get("A_DIRECT_SURFACE_MEASUREMENT", {})
    for name in DIRECT_SOLVER_REQUIRED:
        path = f"A_DIRECT_SURFACE_MEASUREMENT.{name}"
        row = rows.get(name)
        if not row or row.get("status") != "MEASURED":
            missing.append(path)
            continue
        value = _finite_scalar(row, "raw_value")
        sigma = _finite_scalar(row, "uncertainty")
        if value is None or sigma is None or row.get("units") != "m":
            invalid.append(path)
            continue
        if not row.get("landmark_definition") or not row.get("measurement_method"):
            invalid.append(f"{path}.provenance")
            continue
        values[name] = value
        sigmas[name] = sigma
    return values, sigmas


def _validate_derived(payload: dict, direct: Mapping[str, float],
                      missing: list[str], invalid: list[str]):
    values: dict[str, float] = {}
    sigmas: dict[str, float] = {}
    rows = payload.get("B_DERIVED_JOINT_CENTER", {})
    for name in DERIVED_SOLVER_REQUIRED:
        path = f"B_DERIVED_JOINT_CENTER.{name}"
        row = rows.get(name)
        if not row or row.get("status") != "DERIVED":
            missing.append(path)
            continue
        value = _finite_scalar(row, "value", signed=name == "hip_joint_centre_vertical_offset")
        sigma = _finite_scalar(row, "uncertainty")
        refs = row.get("raw_measurement_refs")
        if value is None or sigma is None or row.get("units") != "m":
            invalid.append(path)
            continue
        if (not row.get("derivation_name") or not row.get("derivation_version")
                or not isinstance(refs, list) or not refs
                or any(ref not in direct for ref in refs)):
            invalid.append(f"{path}.derivation_provenance")
            continue
        values[name] = value
        sigmas[name] = sigma
    return values, sigmas


def _validate_transform(row: dict, path: str, *, capture: bool,
                        missing: list[str], invalid: list[str]):
    if not row or row.get("status") == "MISSING":
        missing.append(path)
        return None
    if row.get("status") not in PLACEMENT_STATUSES:
        invalid.append(f"{path}.status")
        return None
    value = _vector(row, "value")
    sigma = _vector(row, "uncertainty")
    if (value is None or sigma is None or np.any(sigma <= 0)
            or row.get("units") != "m" or not row.get("frame") or not row.get("source")):
        invalid.append(path)
        return None
    if not capture:
        return value
    lower = _vector(row, "lower_bound")
    upper = _vector(row, "upper_bound")
    if lower is None or upper is None or np.any(lower >= upper) or np.any(value <= lower) or np.any(value >= upper):
        invalid.append(f"{path}.bounds")
        return None
    if row.get("status") == "CALIBRATION_ESTIMATED" and not row.get("estimate_as_nuisance"):
        invalid.append(f"{path}.estimate_as_nuisance")
        return None
    return value, sigma, lower, upper


def _validate_placements(payload: dict, missing: list[str], invalid: list[str]):
    placements: dict[str, SensorPlacementV41] = {}
    rows = payload.get("C_SENSOR_PLACEMENT", {})
    for node, expected_segment in NODE_TO_SEGMENT.items():
        base = f"C_SENSOR_PLACEMENT.{node}"
        row = rows.get(node)
        if not row:
            missing.append(base)
            continue
        if row.get("segment") != expected_segment or not row.get("landmark"):
            invalid.append(f"{base}.identity")
            continue
        cad = _validate_transform(row.get("pcb_phase_centre_to_enclosure"),
                                  f"{base}.pcb_phase_centre_to_enclosure", capture=False,
                                  missing=missing, invalid=invalid)
        capture = _validate_transform(row.get("capture_enclosure_to_landmark"),
                                      f"{base}.capture_enclosure_to_landmark", capture=True,
                                      missing=missing, invalid=invalid)
        if cad is None or capture is None:
            continue
        value, sigma, lower, upper = capture
        capture_row = row["capture_enclosure_to_landmark"]
        placements[node] = SensorPlacementV41(
            node=node,
            segment=expected_segment,
            landmark=str(row["landmark"]),
            pcb_phase_centre_to_enclosure_m=cad,
            capture_prior_m=value,
            capture_sigma_m=sigma,
            capture_lower_m=lower,
            capture_upper_m=upper,
            capture_status=str(capture_row["status"]),
            capture_source=str(capture_row["source"]),
            estimate_as_nuisance=bool(capture_row.get("estimate_as_nuisance", False)),
        )
    return placements


def _rendering_audit(payload: dict) -> dict:
    missing: list[str] = []
    invalid: list[str] = []
    rows = payload.get("D_RENDERING_ONLY", {})
    for name in RENDERING_REQUIRED:
        path = f"D_RENDERING_ONLY.{name}"
        row = rows.get(name)
        if not row or row.get("status") != "MEASURED":
            missing.append(path)
            continue
        if (_finite_scalar(row, "raw_value") is None
                or _finite_scalar(row, "uncertainty") is None
                or row.get("units") != "m" or not row.get("shoe_condition")):
            invalid.append(path)
    return {
        "verdict": "PASS" if not missing and not invalid else "BLOCKED_SHOE_GEOMETRY_INCOMPLETE",
        "missing": sorted(missing),
        "invalid": sorted(invalid),
        "blocks_centerline_solver": False,
    }


def validate_anthropometry_v4_1(path: Path) -> tuple[AnthropometryV41 | None, dict]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    missing: list[str] = []
    invalid: list[str] = []
    if payload.get("schema") != "biospur-anthropometry-v4.1":
        invalid.append("schema")
    direct, direct_sigma = _validate_surface(payload, missing, invalid)
    derived, derived_sigma = _validate_derived(payload, direct, missing, invalid)
    placements = _validate_placements(payload, missing, invalid)
    rendering = _rendering_audit(payload)
    solver_complete = not missing and not invalid
    audit = {
        "schema": "biospur-anthropometry-validation-v4.1",
        "path": str(path.resolve()),
        "sha256": sha256(path),
        "provenance_classes": {
            "A": "DIRECT_SURFACE_MEASUREMENT",
            "B": "DERIVED_JOINT_CENTER",
            "C": "SENSOR_PLACEMENT",
            "D": "RENDERING_ONLY",
        },
        "solver_missing": sorted(missing),
        "solver_invalid": sorted(invalid),
        "solver_complete": solver_complete,
        "foot_rendering": rendering,
        "population_average_substitution_allowed": False,
        "capture_derived_body_dimensions_allowed": False,
        "sensor_offsets_estimated_as_bounded_nuisance": bool(
            placements and any(value.estimate_as_nuisance for value in placements.values())),
        "anthropometric_uncertainty": {
            "mode": "FIXED_INPUTS_NOT_PROPAGATED",
            "statement": "Calibration covariance conditions on anthropometric scalars and excludes their measurement and derivation uncertainty.",
        },
    }
    if not solver_complete:
        return None, audit
    return AnthropometryV41(
        source_sha256=audit["sha256"],
        direct_surface_m=direct,
        direct_surface_sigma_m=direct_sigma,
        derived_joint_center_m=derived,
        derived_joint_center_sigma_m=derived_sigma,
        placements=placements,
    ), audit
