"""C2 geometric embedding and explicit sensor-binding evidence.

This boundary corrects the handedness of FK geometry, not the world frame of
the IMU. Sensor rotations, accelerations, anchors and wear-registered normals
must not be passed through its improper map. Sensor locations are separate
from drawing joints; unavailable metrology remains unavailable.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

import numpy as np

from biospur_fusion.c2_coupled_progressive.output_coordinates import (
    OUTPUT_COORDINATE_SCHEMA,
    apply_output_coordinate_convention,
)
from .antenna_los import horizontal_yaw_alignment


@dataclass(frozen=True)
class GeometricEmbedding:
    """One capture-wide FK-only map from the previous geometric embedding."""

    matrix: np.ndarray
    reflection_internal: np.ndarray
    heading_after_reflection: np.ndarray

    def vectors(self, value: np.ndarray) -> np.ndarray:
        """Apply to geometry vectors with trailing XYZ dimension only."""
        value = np.asarray(value, dtype=float)
        if value.shape[-1:] != (3,) or not np.isfinite(value).all():
            raise ValueError("geometry vectors must be finite with trailing XYZ")
        return value @ self.matrix.T

    def jacobian(self, value: np.ndarray) -> np.ndarray:
        """Push forward a point Jacobian, retaining its state coordinates."""
        value = np.asarray(value, dtype=float)
        if value.ndim < 2 or value.shape[-2] != 3 or not np.isfinite(value).all():
            raise ValueError("point Jacobian must have shape (..., 3, state)")
        return self.matrix @ value


def bind_frozen_parity(
    reflection_internal: np.ndarray,
    previous_world_from_internal: np.ndarray,
    initial_right_vector_previous: np.ndarray,
) -> GeometricEmbedding:
    """Embed frozen parity, then bind anatomical right to attested world -X.

    With prior geometry p_old=A p_internal, p_new=Y M p_internal.
    Thus the map consumed by a derived artifact is G=Y M A.T. Y is a
    proper yaw selected AFTER M; M is validated by its existing owner.
    This is not a coordinate transform for physical sensor observations.
    """
    a = np.asarray(previous_world_from_internal, dtype=float).reshape(3, 3)
    if (not np.isfinite(a).all() or
            not np.allclose(a.T @ a, np.eye(3), atol=1e-10) or
            not np.isclose(np.linalg.det(a), 1., atol=1e-10)):
        raise ValueError("previous internal alignment must be proper")
    m = np.asarray(reflection_internal, dtype=float).reshape(3, 3)
    convention = {"output_coordinate_convention": {
        "schema": OUTPUT_COORDINATE_SCHEMA,
        "matrix_world_output_from_internal": m,
    }}
    right_internal = a.T @ np.asarray(initial_right_vector_previous, float).reshape(3)
    right_reflected = apply_output_coordinate_convention(
        convention, {"right": right_internal})["right"]
    y = horizontal_yaw_alignment(right_reflected, np.array([-1., 0., 0.]))
    return GeometricEmbedding(y @ m @ a.T, m.copy(), y)


@dataclass(frozen=True)
class SensorBindingEvidence:
    """Metadata does not silently turn a landmark proxy into a measured tag."""

    node: str
    landmark: str
    offset_in_segment_m: tuple[float, float, float] | None
    covariance_m2: tuple[tuple[float, float, float], ...] | None
    provenance: str
    status: str = "UNMEASURED_SENSOR_TO_LANDMARK_OFFSET"

    def require_measured_offset(self) -> np.ndarray:
        if self.offset_in_segment_m is None or self.covariance_m2 is None:
            raise ValueError(f"{self.node}: physical sensor binding is unavailable")
        offset = np.asarray(self.offset_in_segment_m, float)
        covariance = np.asarray(self.covariance_m2, float)
        if (offset.shape != (3,) or covariance.shape != (3, 3) or
                not np.isfinite(offset).all() or not np.isfinite(covariance).all() or
                not np.allclose(covariance, covariance.T) or
                np.linalg.eigvalsh(covariance).min() <= 0):
            raise ValueError("sensor binding needs finite offset and positive covariance")
        return offset.copy()


def unavailable_sensor_bindings(sealed_identity: Mapping) -> dict[str, SensorBindingEvidence]:
    """Read actual donning landmarks without substituting joint positions."""
    if not sealed_identity.get("confirmed_after_capture"):
        raise ValueError("capture node identity has not been confirmed")
    rows = sealed_identity.get("rows", [])
    if not isinstance(rows, list) or not rows:
        raise ValueError("sealed identity must contain node rows")
    return {row["hardware_id"]: SensorBindingEvidence(
        node=row["hardware_id"], landmark=row["mount_landmark"],
        offset_in_segment_m=None, covariance_m2=None,
        provenance="C2_SEALED_NODE_TO_BODY_GROUND_TRUTH") for row in rows}


def engineering_tag_points(
    embedded_joints: Mapping[str, np.ndarray],
    torso_up_embedded: np.ndarray,
    chest_vertical_observations_m: np.ndarray,
) -> dict[str, np.ndarray]:
    """Explicit chest surface candidate; other sites remain landmark proxies.

    The observation spread is NOT a mapping covariance. Unknown anterior
    displacement, shoulder reference, mounting offsets and phase centres are
    not zero-uncertainty measurements and are not resolved by this function.
    """
    from .frozen_body_proxy import NODE_TO_PROXY_POINT

    up = np.asarray(torso_up_embedded, float)
    readings = np.asarray(chest_vertical_observations_m, float)
    if (up.shape[-1:] != (3,) or not np.isfinite(up).all() or
            not np.allclose(np.linalg.norm(up, axis=-1), 1., atol=1e-8)):
        raise ValueError("torso-up geometry direction must be unit")
    if readings.ndim != 1 or not len(readings) or not np.isfinite(readings).all() or np.any(readings <= 0):
        raise ValueError("positive measured chest vertical distances required")
    points = {node: np.asarray(embedded_joints[name], float).copy()
              for node, name in NODE_TO_PROXY_POINT.items()}
    points['BSF31CC'] -= float(np.mean(readings)) * up
    return points
