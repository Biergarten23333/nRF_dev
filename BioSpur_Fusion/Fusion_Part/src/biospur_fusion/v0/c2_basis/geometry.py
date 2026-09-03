"""Measured anthropometry and fixed-length C2 forward-kinematics geometry."""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Mapping

import numpy as np

from .contracts import ANTHROPOMETRY_REL, load_config


LIMBS = (
    "upper_arm_left", "forearm_left", "upper_arm_right", "forearm_right",
    "thigh_left", "shank_left", "thigh_right", "shank_right",
)


@dataclass(frozen=True)
class ScalarGeometry:
    value_m: float
    lower_m: float
    upper_m: float
    sigma_m: float
    source: str
    estimator_coordinate: bool = False

    def validate(self, name: str) -> None:
        if not (0.0 < self.lower_m <= self.value_m <= self.upper_m):
            raise ValueError(f"{name}: invalid geometry interval")
        if self.sigma_m <= 0.0:
            raise ValueError(f"{name}: invalid uncertainty")


@dataclass(frozen=True)
class BodyGeometry:
    segments: Mapping[str, ScalarGeometry]
    biacromial: ScalarGeometry
    chest_to_acromion: ScalarGeometry
    pelvis_imu_to_chest_imu: ScalarGeometry
    internal_hip_spacing: ScalarGeometry
    raw_measurements: Mapping[str, Any]
    provenance_path: Path

    def validate(self) -> dict[str, Any]:
        if set(self.segments) != set(LIMBS):
            raise ValueError("fixed body geometry lacks a limb segment")
        for name, row in self.segments.items():
            row.validate(name)
            if row.estimator_coordinate:
                raise ValueError(f"{name}: bone length entered estimator state")
        for name, row in (
            ("biacromial", self.biacromial),
            ("chest_to_acromion", self.chest_to_acromion),
            ("pelvis_imu_to_chest_imu", self.pelvis_imu_to_chest_imu),
            ("internal_hip_spacing", self.internal_hip_spacing),
        ):
            row.validate(name)
        if "NOT_DERIVED_FROM_BICRISTAL_OR_BITROCHANTERIC" not in self.internal_hip_spacing.source:
            raise ValueError("external pelvis breadth was relabelled as hip-center spacing")
        return {
            "fixed_bone_lengths": True,
            "bone_length_estimator_coordinates": 0,
            "bilateral_lengths_independent_records": True,
            "bilateral_equality_constraint": False,
            "internal_hip_spacing_is_measured_surface_breadth": False,
            "height_use": "BROAD_QA_ONLY",
        }

    def axial_offset_defaults(self) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Return nominal/lower/upper sensor distance above each distal joint."""

        config = load_config(self.provenance_path.parents[2])["geometry"]
        nominal = np.asarray(config["sensor_axial_offset_nominal_m"], dtype=float)
        lower = np.asarray(config["sensor_axial_offset_lower_m"], dtype=float)
        upper = np.asarray(config["sensor_axial_offset_upper_m"], dtype=float)
        return nominal, lower, upper

    def connection_points_body(
        self,
        axial_offsets_m: np.ndarray,
        *,
        hip_spacing_m: float | None = None,
    ) -> dict[str, tuple[np.ndarray, np.ndarray]]:
        """Connection vectors from each sensor origin in anatomical frames.

        Every limb uses the fixed measured length. The only limb placement
        coordinate is the bounded sensor distance above its distal landmark.
        """

        offsets = np.asarray(axial_offsets_m, dtype=float)
        nominal, lower, upper = self.axial_offset_defaults()
        if offsets.shape != nominal.shape or np.any(offsets < lower) or np.any(offsets > upper):
            raise ValueError("limb sensor axial offsets left their preregistered bounds")
        points: dict[str, tuple[np.ndarray, np.ndarray]] = {}
        for index, segment in enumerate(LIMBS):
            length = self.segments[segment].value_m
            distal = np.array([0.0, 0.0, -offsets[index]])
            proximal = np.array([0.0, 0.0, length - offsets[index]])
            points[segment] = (proximal, distal)
        hip = self.internal_hip_spacing.value_m if hip_spacing_m is None else float(hip_spacing_m)
        if not self.internal_hip_spacing.lower_m <= hip <= self.internal_hip_spacing.upper_m:
            raise ValueError("hip-center sensitivity value left its broad unmeasured interval")
        half_shoulder = 0.5 * self.biacromial.value_m
        half_hip = 0.5 * hip
        lumbar = 0.5 * self.pelvis_imu_to_chest_imu.value_m
        points["pelvis"] = (
            np.array([0.0, 0.0, lumbar]),
            np.array([0.0, -half_hip, -0.06]),
        )
        points["torso"] = (
            np.array([0.0, 0.0, -lumbar]),
            np.array([0.0, -half_shoulder, self.chest_to_acromion.value_m]),
        )
        return points

    def edge_points_body(
        self, axial_offsets_m: np.ndarray, *, hip_spacing_m: float | None = None,
    ) -> dict[str, tuple[np.ndarray, np.ndarray]]:
        points = self.connection_points_body(axial_offsets_m, hip_spacing_m=hip_spacing_m)
        shoulder = 0.5 * self.biacromial.value_m
        hip = 0.5 * (
            self.internal_hip_spacing.value_m if hip_spacing_m is None else hip_spacing_m
        )
        chest_up = self.chest_to_acromion.value_m
        lumbar = 0.5 * self.pelvis_imu_to_chest_imu.value_m
        return {
            "pelvis_torso": (np.array([0.0, 0.0, lumbar]), np.array([0.0, 0.0, -lumbar])),
            "shoulder_left": (np.array([0.0, shoulder, chest_up]), points["upper_arm_left"][0]),
            "elbow_left": (points["upper_arm_left"][1], points["forearm_left"][0]),
            "shoulder_right": (np.array([0.0, -shoulder, chest_up]), points["upper_arm_right"][0]),
            "elbow_right": (points["upper_arm_right"][1], points["forearm_right"][0]),
            "hip_left": (np.array([0.0, hip, -0.06]), points["thigh_left"][0]),
            "knee_left": (points["thigh_left"][1], points["shank_left"][0]),
            "hip_right": (np.array([0.0, -hip, -0.06]), points["thigh_right"][0]),
            "knee_right": (points["thigh_right"][1], points["shank_right"][0]),
        }


def _measurement_map(payload: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    return {str(row["measurement_id"]): row for row in payload["measurements"]}


def _values(row: Mapping[str, Any]) -> list[float]:
    values: list[float] = []
    for observation in row["observations"]:
        if "value_mm" in observation:
            values.append(float(observation["value_mm"]))
        elif "range_mm" in observation:
            values.extend(float(value) for value in observation["range_mm"])
    return values


def load_body_geometry(root: Path) -> BodyGeometry:
    root = Path(root)
    config = load_config(root)
    path = root / ANTHROPOMETRY_REL
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = _measurement_map(payload)
    required = {
        "left_upper_arm_surface_length": [310.0, 325.0],
        "right_upper_arm_surface_length": [310.0, 325.0],
        "left_forearm_surface_length": [245.0],
        "right_forearm_surface_length": [245.0],
        "forearm_surface_length_unassigned_second_observer": [260.0, 265.0],
        "left_thigh_surface_length": [480.0],
        "right_thigh_surface_length": [480.0],
        "left_shank_surface_length": [430.0],
        "right_shank_surface_length": [430.0],
        "biacromial_breadth": [400.0, 425.0],
        "chest_sensor_to_acromion_line_vertical_distance": [140.0, 150.0],
        "pelvis_sensor_to_chest_sensor_center_distance": [280.0],
        "bicristal_breadth": [335.0, 315.0],
        "bitrochanteric_breadth": [335.0],
    }
    for name, expected in required.items():
        if _values(rows[name]) != expected:
            raise ValueError(f"anthropometry reading changed: {name}")
    geometry_cfg = config["geometry"]

    def fixed(name: str, nominal: str, bounds_name: str, sigma: float, source: str) -> ScalarGeometry:
        lower, upper = geometry_cfg[bounds_name]
        return ScalarGeometry(float(geometry_cfg[nominal]), float(lower), float(upper), sigma, source)

    result = BodyGeometry(
        segments={
            "upper_arm_left": fixed("upper_arm_left", "upper_arm_nominal_m", "upper_arm_bounds_m", 0.02, "310_AND_325_MM_SURFACE_READINGS_PLUS_MAPPING_UNCERTAINTY"),
            "upper_arm_right": fixed("upper_arm_right", "upper_arm_nominal_m", "upper_arm_bounds_m", 0.02, "310_AND_325_MM_SURFACE_READINGS_PLUS_MAPPING_UNCERTAINTY"),
            "forearm_left": fixed("forearm_left", "forearm_nominal_m", "forearm_bounds_m", 0.025, "245_MM_PLUS_UNASSIGNED_260_TO_265_MM_SECOND_READING_WIDE_UNCERTAINTY"),
            "forearm_right": fixed("forearm_right", "forearm_nominal_m", "forearm_bounds_m", 0.025, "245_MM_PLUS_UNASSIGNED_260_TO_265_MM_SECOND_READING_WIDE_UNCERTAINTY"),
            "thigh_left": fixed("thigh_left", "thigh_nominal_m", "thigh_bounds_m", 0.02, "480_MM_SURFACE_READING"),
            "thigh_right": fixed("thigh_right", "thigh_nominal_m", "thigh_bounds_m", 0.02, "480_MM_SURFACE_READING"),
            "shank_left": fixed("shank_left", "shank_nominal_m", "shank_bounds_m", 0.02, "430_MM_SURFACE_READING"),
            "shank_right": fixed("shank_right", "shank_nominal_m", "shank_bounds_m", 0.02, "430_MM_SURFACE_READING"),
        },
        biacromial=ScalarGeometry(0.4125, 0.39, 0.435, 0.02, "400_AND_425_MM_BIACROMIAL_SURFACE_READINGS"),
        chest_to_acromion=ScalarGeometry(0.145, 0.13, 0.16, 0.015, "140_AND_150_MM_CHEST_SENSOR_TO_ACROMION_LINE"),
        pelvis_imu_to_chest_imu=ScalarGeometry(
            0.28,
            float(geometry_cfg["pelvis_imu_to_chest_imu_bounds_m"][0]),
            float(geometry_cfg["pelvis_imu_to_chest_imu_bounds_m"][1]),
            0.02,
            "280_MM_SENSOR_CENTER_TO_SENSOR_CENTER",
        ),
        internal_hip_spacing=ScalarGeometry(
            float(geometry_cfg["internal_hip_center_spacing_nominal_m"]),
            min(geometry_cfg["internal_hip_center_spacing_sensitivity_m"]),
            max(geometry_cfg["internal_hip_center_spacing_sensitivity_m"]),
            0.04,
            "UNMEASURED_ENGINEERING_SENSITIVITY_NOT_DERIVED_FROM_BICRISTAL_OR_BITROCHANTERIC",
        ),
        raw_measurements=payload,
        provenance_path=path,
    )
    result.validate()
    return result
