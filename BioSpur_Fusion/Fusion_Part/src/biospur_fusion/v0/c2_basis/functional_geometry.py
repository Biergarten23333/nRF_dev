"""Global full-3D functional placement projected onto measured V0 geometry."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

import numpy as np

from biospur_fusion.v0.raw6_heading import EDGES

from .geometry import BodyGeometry


@dataclass(frozen=True)
class FunctionalPlacement:
    edge_levers_sensor: Mapping[str, tuple[np.ndarray, np.ndarray]]
    audit: Mapping[str, Any]


def _unit(value: np.ndarray, *, name: str) -> np.ndarray:
    value = np.asarray(value, dtype=float)
    norm = float(np.linalg.norm(value))
    if not np.isfinite(norm) or norm <= 1e-8:
        raise ValueError(f"{name}: functional connection direction is degenerate")
    return value / norm


def _set_endpoint(
    levers: dict[str, list[np.ndarray]], edge: str, endpoint: int, value: np.ndarray,
) -> None:
    levers[edge][endpoint] = np.asarray(value, dtype=float)


def project_functional_placement(
    raw_levers_by_edge: Mapping[str, np.ndarray],
    body_from_sensor_by_segment: Mapping[str, np.ndarray],
    geometry: BodyGeometry,
    *,
    maximum_length_mismatch_m: float,
) -> FunctionalPlacement:
    """Keep arbitrary surface offsets while fixing measured connection lengths.

    Joint-center vectors are estimated freely in sensor coordinates first.
    Projection changes only the measured scalar distances; it preserves each
    sensor's learned transverse/radial offset and does not invent internal hip
    centers or a torso length.
    """

    edge_names = {name for name, _, _, _ in EDGES}
    if set(raw_levers_by_edge) != edge_names:
        raise ValueError("functional placement lacks one or more graph edges")
    levers = {
        edge: [
            np.asarray(value[:3], dtype=float).copy(),
            np.asarray(value[3:], dtype=float).copy(),
        ]
        for edge, value in raw_levers_by_edge.items()
    }
    for edge, pair in levers.items():
        if any(value.shape != (3,) or not np.isfinite(value).all() for value in pair):
            raise ValueError(f"{edge}: functional lever is not finite 3D")

    segment_connections = {
        "upper_arm_left": (("shoulder_left", 1), ("elbow_left", 0)),
        "upper_arm_right": (("shoulder_right", 1), ("elbow_right", 0)),
        "thigh_left": (("hip_left", 1), ("knee_left", 0)),
        "thigh_right": (("hip_right", 1), ("knee_right", 0)),
    }
    segment_rows: dict[str, Any] = {}
    for segment, (proximal_ref, distal_ref) in segment_connections.items():
        proximal = levers[proximal_ref[0]][proximal_ref[1]]
        distal = levers[distal_ref[0]][distal_ref[1]]
        raw_length = float(np.linalg.norm(proximal - distal))
        measured = geometry.segments[segment]
        mismatch = abs(raw_length - measured.value_m)
        if mismatch > float(maximum_length_mismatch_m):
            raise RuntimeError(
                f"{segment}: full-3D functional length mismatch {mismatch:.4f} m"
            )
        direction = _unit(proximal - distal, name=segment)
        midpoint = 0.5 * (proximal + distal)
        projected_proximal = midpoint + 0.5 * measured.value_m * direction
        projected_distal = midpoint - 0.5 * measured.value_m * direction
        _set_endpoint(levers, *proximal_ref, projected_proximal)
        _set_endpoint(levers, *distal_ref, projected_distal)
        transform = np.asarray(body_from_sensor_by_segment[segment], dtype=float)
        midpoint_body = transform @ midpoint
        segment_rows[segment] = {
            "raw_functional_length_m": raw_length,
            "measured_fixed_length_m": measured.value_m,
            "measurement_interval_m": [measured.lower_m, measured.upper_m],
            "preprojection_mismatch_m": mismatch,
            "sensor_origin_from_bone_midpoint_body_m": (-midpoint_body).tolist(),
            "transverse_surface_offset_m": float(np.linalg.norm(midpoint_body[:2])),
            "axial_only_surface_offset_assumed": False,
        }

    torso_transform = np.asarray(body_from_sensor_by_segment["torso"], dtype=float)
    left_shoulder_body = torso_transform @ levers["shoulder_left"][0]
    right_shoulder_body = torso_transform @ levers["shoulder_right"][0]
    shoulder_vector = left_shoulder_body - right_shoulder_body
    raw_breadth = float(np.linalg.norm(shoulder_vector))
    if not (
        geometry.biacromial.lower_m - maximum_length_mismatch_m
        <= raw_breadth
        <= geometry.biacromial.upper_m + maximum_length_mismatch_m
    ):
        raise RuntimeError("functional shoulder centers conflict with biacromial measurement")
    shoulder_midpoint = 0.5 * (left_shoulder_body + right_shoulder_body)
    shoulder_direction = _unit(shoulder_vector, name="biacromial")
    shoulder_midpoint[2] = geometry.chest_to_acromion.value_m
    left_shoulder_body = (
        shoulder_midpoint + 0.5 * geometry.biacromial.value_m * shoulder_direction
    )
    right_shoulder_body = (
        shoulder_midpoint - 0.5 * geometry.biacromial.value_m * shoulder_direction
    )
    _set_endpoint(
        levers, "shoulder_left", 0, torso_transform.T @ left_shoulder_body,
    )
    _set_endpoint(
        levers, "shoulder_right", 0, torso_transform.T @ right_shoulder_body,
    )

    pelvis_transform = np.asarray(body_from_sensor_by_segment["pelvis"], dtype=float)
    pelvis_connection_body = pelvis_transform @ levers["pelvis_torso"][0]
    torso_connection_body = torso_transform @ levers["pelvis_torso"][1]
    sensor_displacement = pelvis_connection_body - torso_connection_body
    raw_sensor_distance = float(np.linalg.norm(sensor_displacement))
    mismatch = abs(raw_sensor_distance - geometry.pelvis_imu_to_chest_imu.value_m)
    if mismatch > float(maximum_length_mismatch_m):
        raise RuntimeError("functional trunk connection conflicts with 280 mm sensor distance")
    displacement_direction = _unit(sensor_displacement, name="pelvis_to_chest_sensor")
    torso_connection_body = (
        pelvis_connection_body
        - geometry.pelvis_imu_to_chest_imu.value_m * displacement_direction
    )
    _set_endpoint(
        levers, "pelvis_torso", 1, torso_transform.T @ torso_connection_body,
    )

    left_hip_body = pelvis_transform @ levers["hip_left"][0]
    right_hip_body = pelvis_transform @ levers["hip_right"][0]
    learned_hip_spacing = float(np.linalg.norm(left_hip_body - right_hip_body))
    if not 0.10 <= learned_hip_spacing <= 0.38:
        raise RuntimeError("functional internal hip centers violate broad human topology")

    output = {
        edge: (pair[0].copy(), pair[1].copy()) for edge, pair in levers.items()
    }
    return FunctionalPlacement(output, {
        "schema": "biospur-c2-full-3d-functional-placement-v1",
        "segment_surface_offsets": segment_rows,
        "shoulder_geometry": {
            "raw_biacromial_distance_m": raw_breadth,
            "fixed_measured_biacromial_distance_m": geometry.biacromial.value_m,
            "fixed_measured_chest_to_acromion_m": geometry.chest_to_acromion.value_m,
            "hard_bilateral_mirror_used": False,
        },
        "trunk_connection": {
            "raw_pelvis_to_chest_sensor_distance_m": raw_sensor_distance,
            "fixed_measured_distance_m": geometry.pelvis_imu_to_chest_imu.value_m,
            "invented_torso_length_used": False,
        },
        "hip_centers": {
            "learned_internal_spacing_m": learned_hip_spacing,
            "bicristal_or_bitrochanteric_used_as_internal_spacing": False,
            "invented_fixed_hip_vertical_offset_used": False,
        },
        "arbitrary_3d_sensor_to_joint_vectors_supported": True,
        "axial_only_offsets_used": False,
        "measured_lengths_enter_with_preprojection_mismatch_audit": True,
    })
