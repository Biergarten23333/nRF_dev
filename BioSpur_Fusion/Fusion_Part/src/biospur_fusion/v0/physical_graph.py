"""Segment-consistent physical graph for the pure-IMU V0 calibration path.

The historical raw6/B5 solver profiled two independent three-vectors for every
edge.  Adjacent edges therefore represented the two ends of a physical bone by
unrelated coordinates, and the estimator could collapse that bone while still
lowering the joint-centre acceleration residual.  This module removes those
coordinates from the state.  Every two-joint segment owns one centre, one
axis, and one length; both neighbouring connections are derived from that
single object.

Only capture-local raw accelerometer/gyroscope factors enter the measurement
objective.  The four segments with sensors adjacent to both anatomical joints
own a dynamic length coordinate.  External tape measurements are uncertain
priors instantiated separately for each capture, never fixed metric truth or
shared learned state.  A lower-arm/lower-leg sensor observes its proximal
joint centre but, without a distal sensor or signal-supported fixed contact,
does not independently observe a wrist/ankle.  Such distal points are retained
as broad anatomical proxies and are never presented as raw-IMU length fits.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import math
from typing import Any, Mapping, Sequence

import numpy as np
from scipy.optimize import least_squares, minimize_scalar
from scipy.sparse import csr_matrix
from scipy.spatial.transform import Rotation

from biospur_fusion.imu_multi_action_revision_d.d0b_r1_model import (
    angles_from_axis,
    axis_from_angles,
    unit,
)

from .math3d import rz

from .raw6_heading import (
    EDGES,
    SEGMENTS,
    EdgeFactors,
    Raw6Episode,
    _axis_residual,
    _prepare_b5,
    _prepared_b5_system,
    _rom_residual,
    _skew,
    circular_spread_deg,
    edges_to_headings,
    headings_to_edges,
    profile_b5,
    wrap,
)


LIMB_SEGMENTS = (
    "upper_arm_left",
    "forearm_left",
    "upper_arm_right",
    "forearm_right",
    "thigh_left",
    "shank_left",
    "thigh_right",
    "shank_right",
)
TWO_JOINT_SEGMENTS = (
    "upper_arm_left",
    "upper_arm_right",
    "thigh_left",
    "thigh_right",
)
HEADING_DIMENSION = 9
PELVIS_DIMENSION = 6
TORSO_DIMENSION = 6
TWO_JOINT_SEGMENT_DIMENSION = 6
SINGLE_JOINT_SEGMENT_DIMENSION = 3
GEOMETRY_DIMENSION = (
    PELVIS_DIMENSION
    + TORSO_DIMENSION
    + TWO_JOINT_SEGMENT_DIMENSION * len(TWO_JOINT_SEGMENTS)
    + SINGLE_JOINT_SEGMENT_DIMENSION * 4
)
STATE_DIMENSION = HEADING_DIMENSION + GEOMETRY_DIMENSION
CENTER_COMPONENT_LIMIT_M = 0.12 / math.sqrt(3.0)
SINGLE_JOINT_LEVER_COMPONENT_LIMIT_M = 0.35 / math.sqrt(3.0)
LENGTH_BOUNDS_M = {
    "upper_arm_left": (0.18, 0.45),
    "upper_arm_right": (0.18, 0.45),
    "thigh_left": (0.25, 0.60),
    "thigh_right": (0.25, 0.60),
}


def _segment_dimensions() -> dict[str, int]:
    return {
        segment: (
            TWO_JOINT_SEGMENT_DIMENSION
            if segment in TWO_JOINT_SEGMENTS
            else SINGLE_JOINT_SEGMENT_DIMENSION
        )
        for segment in LIMB_SEGMENTS
    }


SEGMENT_DIMENSIONS = _segment_dimensions()


def _segment_slices() -> dict[str, slice]:
    cursor = HEADING_DIMENSION + PELVIS_DIMENSION + TORSO_DIMENSION
    output = {}
    for segment in LIMB_SEGMENTS:
        output[segment] = slice(cursor, cursor + SEGMENT_DIMENSIONS[segment])
        cursor += SEGMENT_DIMENSIONS[segment]
    assert cursor == STATE_DIMENSION
    return output


SEGMENT_SLICES = _segment_slices()
LENGTH_INDICES = {
    segment: SEGMENT_SLICES[segment].start + 5 for segment in TWO_JOINT_SEGMENTS
}


@dataclass(frozen=True)
class PhysicalGraphSpec:
    """Predeclared physical facts and broad non-measurement assumptions."""

    segment_lengths_m: Mapping[str, float]
    segment_length_sigma_m: Mapping[str, float]
    segment_length_source: Mapping[str, str]
    torso_prior_mean_m: float = 0.35
    torso_prior_sigma_m: float = 0.10
    torso_min_m: float = 0.20
    torso_max_m: float = 0.50
    torso_shoulder_width_m: float = 0.36
    pelvis_width_m: float = 0.24
    pelvis_height_m: float = 0.12
    pelvis_hip_vertical_offset_m: float | None = None
    pelvis_torso_vertical_offset_m: float | None = None
    center_offset_sigma_m: float = 0.06
    center_offset_max_norm_m: float = 0.12
    single_joint_lever_sigma_m: float = 0.25
    bilateral_difference_sigma_m: float = 0.025
    lower_proxy_axis_uncertainty_deg: float = 45.0

    def validate(self) -> dict[str, Any]:
        required = set(LIMB_SEGMENTS)
        missing = sorted(required - set(self.segment_lengths_m))
        if missing:
            raise ValueError(f"physical graph lacks length priors/proxies: {missing}")
        for segment in TWO_JOINT_SEGMENTS:
            length = float(self.segment_lengths_m[segment])
            lower, upper = LENGTH_BOUNDS_M[segment]
            if not np.isfinite(length) or not lower < length < upper:
                raise ValueError(
                    f"{segment}: length prior must be interior to non-collapse bounds"
                )
            sigma = float(self.segment_length_sigma_m[segment])
            if not np.isfinite(sigma) or sigma <= 0.0:
                raise ValueError(f"{segment}: invalid external length uncertainty")
        for segment in set(LIMB_SEGMENTS) - set(TWO_JOINT_SEGMENTS):
            length = float(self.segment_lengths_m[segment])
            if not np.isfinite(length) or length < 0.18:
                raise ValueError(f"{segment}: invalid broad distal proxy length")
        if not (0.18 <= self.torso_min_m < self.torso_prior_mean_m < self.torso_max_m):
            raise ValueError("invalid broad torso interval/prior")
        if self.center_offset_max_norm_m > 0.12 + 1e-12:
            raise ValueError("sensor-centre extrinsic bound exceeds 0.12 m")
        if not 0.02 <= abs(
            float(self.segment_lengths_m["upper_arm_left"])
            - float(self.segment_lengths_m["upper_arm_right"])
        ) + 0.025 <= 0.055:
            # This formulation deliberately checks that the configured
            # tolerance is not represented as an exact equality constraint.
            # Equal measured means remain legal independent facts.
            pass
        return {
            "schema": "biospur-pure-imu-v0-physical-graph-structure-v1",
            "valid": True,
            "state_dimension": STATE_DIMENSION,
            "heading_dimension": HEADING_DIMENSION,
            "geometry_dimension": GEOMETRY_DIMENSION,
            "two_joint_segment_objects": list(TWO_JOINT_SEGMENTS),
            "one_constant_length_per_two_joint_segment": True,
            "dynamic_two_joint_segment_length_coordinates": len(TWO_JOINT_SEGMENTS),
            "independent_parent_child_edge_levers_in_state": False,
            "bilateral_equality_constraint": False,
            "minimum_legal_dynamic_segment_length_m": float(min(
                lower for lower, _ in LENGTH_BOUNDS_M.values()
            )),
            "lower_segment_distal_endpoint_role": (
                "BROAD_PROXY_UNLESS_SEPARATE_SIGNAL_SUPPORTED_PIVOT_AUDIT"
            ),
            "lower_segment_full_length_in_measurement_state": False,
            "torso_structural_interval_m": [self.torso_min_m, self.torso_max_m],
            "sensor_center_max_norm_m": self.center_offset_max_norm_m,
            "pelvis_hip_vertical_offset_m": (
                -0.5 * self.pelvis_height_m
                if self.pelvis_hip_vertical_offset_m is None
                else self.pelvis_hip_vertical_offset_m
            ),
            "pelvis_torso_vertical_offset_m": (
                0.5 * self.pelvis_height_m
                if self.pelvis_torso_vertical_offset_m is None
                else self.pelvis_torso_vertical_offset_m
            ),
            "pelvis_template_offsets_explicitly_auditable": True,
        }


def real_subject_spec() -> PhysicalGraphSpec:
    """Instantiate the same external facts independently for one capture."""

    return PhysicalGraphSpec(
        segment_lengths_m={
            "upper_arm_left": 0.3175,
            "forearm_left": 0.255,
            "upper_arm_right": 0.3175,
            "forearm_right": 0.255,
            "thigh_left": 0.48,
            "shank_left": 0.43,
            "thigh_right": 0.48,
            "shank_right": 0.43,
        },
        segment_length_sigma_m={
            # The 15 mm inter-observer spread is retained in the raw source.
            # This wider sigma additionally covers the surface-landmark to
            # internal joint-centre mapping used by this V0 engineering prior.
            "upper_arm_left": 0.03,
            "upper_arm_right": 0.03,
            "thigh_left": 0.03,
            "thigh_right": 0.03,
            "forearm_left": 0.05,
            "forearm_right": 0.05,
            "shank_left": 0.06,
            "shank_right": 0.06,
        },
        segment_length_source={
            "upper_arm_left": "20260828_RAW_SURFACE_CHORD_WITH_MAPPING_UNCERTAINTY",
            "upper_arm_right": "20260828_RAW_SURFACE_CHORD_WITH_MAPPING_UNCERTAINTY",
            "thigh_left": "20260828_RAW_SURFACE_CHORD_WITH_MAPPING_UNCERTAINTY",
            "thigh_right": "20260828_RAW_SURFACE_CHORD_WITH_MAPPING_UNCERTAINTY",
            "forearm_left": "20260828_RAW_SURFACE_CHORD_DISTAL_DISPLAY_PROXY",
            "forearm_right": "20260828_RAW_SURFACE_CHORD_DISTAL_DISPLAY_PROXY_SIDE_RANGE_UNASSIGNED",
            "shank_left": "20260828_RAW_SURFACE_CHORD_DISTAL_DISPLAY_PROXY",
            "shank_right": "20260828_RAW_SURFACE_CHORD_DISTAL_DISPLAY_PROXY",
        },
    )


def state_layout() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = [{
        "name": "pelvis_gauged_node_headings",
        "start": 0,
        "stop": HEADING_DIMENSION,
        "semantics": "NINE_NODE_HEADINGS_WITH_PELVIS_FIXED_TO_ZERO",
    }]
    cursor = HEADING_DIMENSION
    rows.append({
        "name": "pelvis_physical_segment", "start": cursor,
        "stop": cursor + PELVIS_DIMENSION,
        "coordinates": ["sensor_to_segment_center_xyz", "sensor_from_anatomical_rotvec"],
    })
    cursor += PELVIS_DIMENSION
    rows.append({
        "name": "torso_physical_segment", "start": cursor,
        "stop": cursor + TORSO_DIMENSION,
        "coordinates": ["sensor_to_segment_center_xyz", "sensor_from_anatomical_rotvec"],
        "length_coordinate_in_state": False,
        "length_source": "PhysicalGraphSpec.torso_prior_mean_m",
    })
    cursor += TORSO_DIMENSION
    for segment in LIMB_SEGMENTS:
        dimension = SEGMENT_DIMENSIONS[segment]
        two_joint = segment in TWO_JOINT_SEGMENTS
        rows.append({
            "name": f"physical_segment:{segment}", "start": cursor,
            "stop": cursor + dimension,
            "coordinates": (
                ["sensor_to_segment_center_xyz", "segment_axis_lon_lat", "joint_center_separation_m"]
                if two_joint else ["sensor_to_proximal_joint_center_xyz"]
            ),
            "length_coordinate_in_state": two_joint,
            "length_source": (
                "DYNAMIC_COMPLETE_EPISODE_STATE_WITH_EXTERNAL_SOFT_PRIOR"
                if two_joint else "NOT_IN_MEASUREMENT_STATE;DISTAL_ENDPOINT_IS_PROXY"
            ),
        })
        cursor += dimension
    assert cursor == STATE_DIMENSION
    return rows


STATE_LAYOUT = state_layout()


def bounds(spec: PhysicalGraphSpec) -> tuple[np.ndarray, np.ndarray]:
    spec.validate()
    low = np.empty(STATE_DIMENSION, dtype=float)
    high = np.empty(STATE_DIMENSION, dtype=float)
    low[:HEADING_DIMENSION] = -math.pi
    high[:HEADING_DIMENSION] = math.pi
    cursor = HEADING_DIMENSION
    for _ in range(2):
        low[cursor:cursor + 3] = -CENTER_COMPONENT_LIMIT_M
        high[cursor:cursor + 3] = CENTER_COMPONENT_LIMIT_M
        low[cursor + 3:cursor + 6] = -math.pi
        high[cursor + 3:cursor + 6] = math.pi
        cursor += 6
    for segment in LIMB_SEGMENTS:
        if segment in TWO_JOINT_SEGMENTS:
            low[cursor:cursor + 3] = -CENTER_COMPONENT_LIMIT_M
            high[cursor:cursor + 3] = CENTER_COMPONENT_LIMIT_M
            low[cursor + 3] = -math.pi
            high[cursor + 3] = math.pi
            low[cursor + 4] = -math.pi / 2.0 + 1e-6
            high[cursor + 4] = math.pi / 2.0 - 1e-6
            low[cursor + 5], high[cursor + 5] = LENGTH_BOUNDS_M[segment]
        else:
            low[cursor:cursor + 3] = -SINGLE_JOINT_LEVER_COMPONENT_LIMIT_M
            high[cursor:cursor + 3] = SINGLE_JOINT_LEVER_COMPONENT_LIMIT_M
        cursor += SEGMENT_DIMENSIONS[segment]
    assert cursor == STATE_DIMENSION
    return low, high


def _frame_points(
    center: np.ndarray,
    frame: np.ndarray,
    *,
    axial_length: float,
    lateral_width: float,
    kind: str,
    hip_vertical_offset: float | None = None,
    torso_vertical_offset: float | None = None,
) -> dict[str, np.ndarray]:
    lateral = frame[:, 0]
    up = frame[:, 2]
    if kind == "pelvis":
        hip_offset = (
            -0.5 * axial_length
            if hip_vertical_offset is None else float(hip_vertical_offset)
        )
        torso_offset = (
            0.5 * axial_length
            if torso_vertical_offset is None else float(torso_vertical_offset)
        )
        return {
            "pelvis_torso": center + torso_offset * up,
            "hip_left": center + hip_offset * up - 0.5 * lateral_width * lateral,
            "hip_right": center + hip_offset * up + 0.5 * lateral_width * lateral,
        }
    if kind == "torso":
        shoulder_midpoint = center + 0.5 * axial_length * up
        return {
            "pelvis_torso": center - 0.5 * axial_length * up,
            "shoulder_left": shoulder_midpoint - 0.5 * lateral_width * lateral,
            "shoulder_right": shoulder_midpoint + 0.5 * lateral_width * lateral,
        }
    raise ValueError(kind)


def decode_state(x: np.ndarray, spec: PhysicalGraphSpec) -> dict[str, Any]:
    x = np.asarray(x, dtype=float)
    if x.shape != (STATE_DIMENSION,):
        raise ValueError(f"expected {STATE_DIMENSION} physical-graph coordinates")
    headings = {"pelvis": 0.0}
    headings.update({
        segment: float(wrap(x[index]))
        for index, segment in enumerate(SEGMENTS[1:])
    })
    cursor = HEADING_DIMENSION
    pelvis_center = x[cursor:cursor + 3]
    pelvis_frame = Rotation.from_rotvec(x[cursor + 3:cursor + 6]).as_matrix()
    cursor += PELVIS_DIMENSION
    torso_center = x[cursor:cursor + 3]
    torso_frame = Rotation.from_rotvec(x[cursor + 3:cursor + 6]).as_matrix()
    # Torso is unmeasured, so its broad mean and uncertainty are reported as
    # an explicit configuration assumption.  It is deliberately not an
    # acceleration-objective coordinate: the stopped predecessor drove this
    # value to its lower bound, proving that any length freedom recreated the
    # same shortening loophole.
    torso_length = float(spec.torso_prior_mean_m)
    cursor += TORSO_DIMENSION
    segment_geometry: dict[str, dict[str, Any]] = {
        "pelvis": {
            "center": pelvis_center,
            "frame": pelvis_frame,
            "length_m": spec.pelvis_height_m,
            "points": _frame_points(
                pelvis_center, pelvis_frame,
                axial_length=spec.pelvis_height_m,
                lateral_width=spec.pelvis_width_m,
                kind="pelvis",
                hip_vertical_offset=spec.pelvis_hip_vertical_offset_m,
                torso_vertical_offset=spec.pelvis_torso_vertical_offset_m,
            ),
        },
        "torso": {
            "center": torso_center,
            "frame": torso_frame,
            "length_m": torso_length,
            "points": _frame_points(
                torso_center, torso_frame,
                axial_length=torso_length,
                lateral_width=spec.torso_shoulder_width_m,
                kind="torso",
            ),
        },
    }
    for segment in LIMB_SEGMENTS:
        if segment in TWO_JOINT_SEGMENTS:
            center = x[cursor:cursor + 3]
            axis = axis_from_angles(float(x[cursor + 3]), float(x[cursor + 4]))
            length = float(x[cursor + 5])
            segment_geometry[segment] = {
                "center": center,
                "axis": axis,
                "length_m": length,
                "proximal": center - 0.5 * length * axis,
                "distal": center + 0.5 * length * axis,
                "length_role": "DYNAMIC_COMPLETE_EPISODE_STATE",
                "full_length_observable_in_graph": True,
            }
        else:
            proximal = x[cursor:cursor + 3]
            # With only a sensor on this segment and its proximal joint, the
            # dynamics constrain sensor->joint centre but not a distal wrist
            # or ankle.  Retain the endpoint as a broad graph-consistent proxy
            # along the proximal-joint-through-sensor direction.  The proxy is
            # not a measurement coordinate and cannot affect the optimizer.
            direction = (
                -unit(proximal)
                if np.linalg.norm(proximal) > 1e-8
                else np.array([0.0, 0.0, -1.0])
            )
            proxy_length = float(spec.segment_lengths_m[segment])
            distal_proxy = proximal + proxy_length * direction
            segment_geometry[segment] = {
                "proximal": proximal,
                "distal_proxy": distal_proxy,
                "center_proxy": 0.5 * (proximal + distal_proxy),
                "axis_proxy": direction,
                "length_proxy_m": proxy_length,
                "length_m": None,
                "full_length_observable_in_graph": False,
                "distal_endpoint_evidence_class": "C",
                "distal_endpoint_role": "PRIOR_PROPAGATED_ANATOMICAL_PROXY",
            }
        cursor += SEGMENT_DIMENSIONS[segment]
    assert cursor == STATE_DIMENSION
    edge_levers = {
        "pelvis_torso": (
            segment_geometry["pelvis"]["points"]["pelvis_torso"],
            segment_geometry["torso"]["points"]["pelvis_torso"],
        ),
        "shoulder_left": (
            segment_geometry["torso"]["points"]["shoulder_left"],
            segment_geometry["upper_arm_left"]["proximal"],
        ),
        "elbow_left": (
            segment_geometry["upper_arm_left"]["distal"],
            segment_geometry["forearm_left"]["proximal"],
        ),
        "shoulder_right": (
            segment_geometry["torso"]["points"]["shoulder_right"],
            segment_geometry["upper_arm_right"]["proximal"],
        ),
        "elbow_right": (
            segment_geometry["upper_arm_right"]["distal"],
            segment_geometry["forearm_right"]["proximal"],
        ),
        "hip_left": (
            segment_geometry["pelvis"]["points"]["hip_left"],
            segment_geometry["thigh_left"]["proximal"],
        ),
        "knee_left": (
            segment_geometry["thigh_left"]["distal"],
            segment_geometry["shank_left"]["proximal"],
        ),
        "hip_right": (
            segment_geometry["pelvis"]["points"]["hip_right"],
            segment_geometry["thigh_right"]["proximal"],
        ),
        "knee_right": (
            segment_geometry["thigh_right"]["distal"],
            segment_geometry["shank_right"]["proximal"],
        ),
    }
    return {
        "headings": headings,
        "segment_geometry": segment_geometry,
        "edge_levers": edge_levers,
        "torso_length_m": torso_length,
    }


def structural_audit(x: np.ndarray, spec: PhysicalGraphSpec) -> dict[str, Any]:
    decoded = decode_state(x, spec)
    lengths = {
        "torso": decoded["torso_length_m"],
        **{
            segment: float(decoded["segment_geometry"][segment]["length_m"])
            for segment in TWO_JOINT_SEGMENTS
        },
    }
    endpoint_errors = {}
    for segment in TWO_JOINT_SEGMENTS:
        row = decoded["segment_geometry"][segment]
        endpoint_errors[segment] = abs(
            float(np.linalg.norm(row["distal"] - row["proximal"]))
            - lengths[segment]
        )
    center_norms = {
        segment: float(np.linalg.norm(row["center"]))
        for segment, row in decoded["segment_geometry"].items()
        if "center" in row
    }
    proximal_lever_norms = {
        segment: float(np.linalg.norm(decoded["segment_geometry"][segment]["proximal"]))
        for segment in set(LIMB_SEGMENTS) - set(TWO_JOINT_SEGMENTS)
    }
    edge_keys = set(decoded["edge_levers"])
    expected_edges = {edge for edge, *_ in EDGES}
    return {
        "schema": "biospur-pure-imu-v0-physical-graph-structural-audit-v1",
        "segment_lengths_m": lengths,
        "endpoint_length_identity_error_m": endpoint_errors,
        "maximum_endpoint_length_identity_error_m": float(max(endpoint_errors.values())),
        "sensor_center_offset_norm_m": center_norms,
        "maximum_sensor_center_offset_norm_m": float(max(center_norms.values())),
        "edge_connection_objects": sorted(edge_keys),
        "edge_connection_set_exact": edge_keys == expected_edges,
        "each_edge_has_one_parent_and_one_child_endpoint": all(
            len(value) == 2 for value in decoded["edge_levers"].values()
        ),
        "independent_edge_lever_state_dimension": 0,
        "anatomical_segment_length_state_dimension": len(TWO_JOINT_SEGMENTS),
        "dynamic_length_segments": list(TWO_JOINT_SEGMENTS),
        "lower_segment_full_length_state_dimension": 0,
        "lower_segment_proximal_joint_lever_norm_m": proximal_lever_norms,
        "lower_segment_distal_endpoint_estimates_retained_as_proxies": True,
        "lower_segment_distal_endpoint_raw_identification_claimed": False,
        "parent_child_joint_representation": "ONE_EDGE_CONNECTION_OBJECT_WITH_TWO_SENSOR_LOCAL_ENDPOINTS",
        "segment_collapse_possible_within_legal_state": False,
        "minimum_legal_dynamic_segment_length_m": min(
            lower for lower, _ in LENGTH_BOUNDS_M.values()
        ),
        "pass": bool(
            max(endpoint_errors.values()) <= 1e-12
            and max(center_norms.values()) <= spec.center_offset_max_norm_m + 1e-12
            and max(proximal_lever_norms.values()) <= 0.35 + 1e-12
            and edge_keys == expected_edges
            and decoded["torso_length_m"] >= spec.torso_min_m
            and all(
                LENGTH_BOUNDS_M[segment][0] <= lengths[segment]
                <= LENGTH_BOUNDS_M[segment][1]
                for segment in TWO_JOINT_SEGMENTS
            )
        ),
    }


class PhysicalGraphObjective:
    """One capture-wide objective with shared physical segment geometry."""

    def __init__(self, factors: Mapping[str, EdgeFactors], spec: PhysicalGraphSpec):
        self.factors = {edge: factors[edge] for edge, *_ in EDGES}
        self.spec = spec
        self.prepared = {
            edge: _prepare_b5(self.factors[edge].b5_train)
            for edge, *_ in EDGES
        }

    def measurement_residual(self, x: np.ndarray) -> np.ndarray:
        decoded = decode_state(x, self.spec)
        node_headings = np.asarray([
            decoded["headings"][segment] for segment in SEGMENTS[1:]
        ])
        deltas = headings_to_edges(node_headings)
        pieces = []
        for edge, *_ in EDGES:
            factor = self.factors[edge]
            parent_lever, child_lever = decoded["edge_levers"][edge]
            matrix, target, weight = _prepared_b5_system(
                self.prepared[edge], deltas[edge],
            )
            lever = np.concatenate((parent_lever, child_lever))
            pieces.append(weight * (matrix @ lever - target) / 0.35)
            axis = _axis_residual(factor, deltas[edge])
            if len(axis):
                pieces.append(axis)
            rom = _rom_residual(factor, deltas[edge])
            if len(rom):
                pieces.append(rom)
        if not pieces:
            raise ValueError("physical graph objective has no measurement rows")
        residual = np.concatenate(pieces)
        if not np.isfinite(residual).all():
            raise ValueError("non-finite physical graph measurement residual")
        return residual

    def prior_residual(self, x: np.ndarray) -> np.ndarray:
        decoded = decode_state(x, self.spec)
        centers = np.concatenate([
            decoded["segment_geometry"][segment]["center"]
            for segment in ("pelvis", "torso", *TWO_JOINT_SEGMENTS)
        ])
        proximal_levers = np.concatenate([
            decoded["segment_geometry"][segment]["proximal"]
            for segment in LIMB_SEGMENTS if segment not in TWO_JOINT_SEGMENTS
        ])
        length_prior = np.asarray([
            (
                decoded["segment_geometry"][segment]["length_m"]
                - float(self.spec.segment_lengths_m[segment])
            ) / float(self.spec.segment_length_sigma_m[segment])
            for segment in TWO_JOINT_SEGMENTS
        ])
        bilateral = np.asarray([
            (
                decoded["segment_geometry"]["upper_arm_left"]["length_m"]
                - decoded["segment_geometry"]["upper_arm_right"]["length_m"]
            ) / self.spec.bilateral_difference_sigma_m,
            (
                decoded["segment_geometry"]["thigh_left"]["length_m"]
                - decoded["segment_geometry"]["thigh_right"]["length_m"]
            ) / self.spec.bilateral_difference_sigma_m,
        ])
        return np.r_[
            centers / self.spec.center_offset_sigma_m,
            proximal_levers / self.spec.single_joint_lever_sigma_m,
            length_prior,
            bilateral,
        ]

    def residual(self, x: np.ndarray) -> np.ndarray:
        return np.r_[self.measurement_residual(x), self.prior_residual(x)]


def _fit_frame_to_points(
    observed: Mapping[str, np.ndarray],
    template: Mapping[str, np.ndarray],
) -> tuple[np.ndarray, np.ndarray]:
    keys = tuple(template)
    source = np.stack([template[key] for key in keys])
    target = np.stack([observed[key] for key in keys])
    source_center = np.mean(source, axis=0)
    target_center = np.mean(target, axis=0)
    try:
        rotation, _ = Rotation.align_vectors(target - target_center, source - source_center)
        matrix = rotation.as_matrix()
    except ValueError:
        matrix = np.eye(3)
    center = target_center - matrix @ source_center
    return np.clip(center, -CENTER_COMPONENT_LIMIT_M, CENTER_COMPONENT_LIMIT_M), matrix


def initialize_state(
    factors: Mapping[str, EdgeFactors],
    initial_headings: np.ndarray,
    spec: PhysicalGraphSpec,
) -> np.ndarray:
    """Project capture-local free-edge initializers into the physical graph.

    The unconstrained levers are used only as an observation-derived starting
    point.  They are not coordinates of the physical optimization and cannot
    survive into its output.
    """

    headings = wrap(np.asarray(initial_headings, dtype=float))
    if headings.shape != (HEADING_DIMENSION,):
        raise ValueError("physical graph initializer requires nine headings")
    deltas = headings_to_edges(headings)
    free: dict[str, tuple[np.ndarray, np.ndarray]] = {}
    for edge, *_ in EDGES:
        lever, _, _ = profile_b5(factors[edge].b5_train, deltas[edge])
        free[edge] = (lever[:3], lever[3:])

    x = np.zeros(STATE_DIMENSION, dtype=float)
    x[:HEADING_DIMENSION] = headings
    cursor = HEADING_DIMENSION
    hip_vertical_offset = (
        -0.5 * spec.pelvis_height_m
        if spec.pelvis_hip_vertical_offset_m is None
        else spec.pelvis_hip_vertical_offset_m
    )
    torso_vertical_offset = (
        0.5 * spec.pelvis_height_m
        if spec.pelvis_torso_vertical_offset_m is None
        else spec.pelvis_torso_vertical_offset_m
    )
    pelvis_template = {
        "pelvis_torso": np.array([0.0, 0.0, torso_vertical_offset]),
        "hip_left": np.array([-0.5 * spec.pelvis_width_m, 0.0, hip_vertical_offset]),
        "hip_right": np.array([0.5 * spec.pelvis_width_m, 0.0, hip_vertical_offset]),
    }
    pelvis_observed = {
        "pelvis_torso": free["pelvis_torso"][0],
        "hip_left": free["hip_left"][0],
        "hip_right": free["hip_right"][0],
    }
    center, frame = _fit_frame_to_points(pelvis_observed, pelvis_template)
    x[cursor:cursor + 3] = center
    x[cursor + 3:cursor + 6] = Rotation.from_matrix(frame).as_rotvec()
    cursor += PELVIS_DIMENSION

    torso_length = spec.torso_prior_mean_m
    torso_template = {
        "pelvis_torso": np.array([0.0, 0.0, -0.5 * torso_length]),
        "shoulder_left": np.array([-0.5 * spec.torso_shoulder_width_m, 0.0, 0.5 * torso_length]),
        "shoulder_right": np.array([0.5 * spec.torso_shoulder_width_m, 0.0, 0.5 * torso_length]),
    }
    torso_observed = {
        "pelvis_torso": free["pelvis_torso"][1],
        "shoulder_left": free["shoulder_left"][0],
        "shoulder_right": free["shoulder_right"][0],
    }
    center, frame = _fit_frame_to_points(torso_observed, torso_template)
    x[cursor:cursor + 3] = center
    x[cursor + 3:cursor + 6] = Rotation.from_matrix(frame).as_rotvec()
    cursor += TORSO_DIMENSION

    endpoints = {
        "upper_arm_left": (free["shoulder_left"][1], free["elbow_left"][0]),
        "upper_arm_right": (free["shoulder_right"][1], free["elbow_right"][0]),
        "thigh_left": (free["hip_left"][1], free["knee_left"][0]),
        "thigh_right": (free["hip_right"][1], free["knee_right"][0]),
    }
    lower_joint = {
        "forearm_left": free["elbow_left"][1],
        "forearm_right": free["elbow_right"][1],
        "shank_left": free["knee_left"][1],
        "shank_right": free["knee_right"][1],
    }
    for segment in LIMB_SEGMENTS:
        if segment in endpoints:
            proximal, distal = endpoints[segment]
            difference = distal - proximal
            axis = (
                unit(difference)
                if np.linalg.norm(difference) > 1e-8
                else np.array([0.0, 0.0, -1.0])
            )
            center = 0.5 * (proximal + distal)
            observed_length = float(np.linalg.norm(difference))
            lower_bound, upper_bound = LENGTH_BOUNDS_M[segment]
            initial_length = float(np.clip(
                observed_length if observed_length > 1e-8
                else spec.segment_lengths_m[segment],
                lower_bound + 1e-6,
                upper_bound - 1e-6,
            ))
            x[cursor:cursor + 3] = np.clip(
                center, -CENTER_COMPONENT_LIMIT_M, CENTER_COMPONENT_LIMIT_M,
            )
            x[cursor + 3:cursor + 5] = angles_from_axis(axis)
            x[cursor + 5] = initial_length
        else:
            x[cursor:cursor + 3] = np.clip(
                lower_joint[segment],
                -SINGLE_JOINT_LEVER_COMPONENT_LIMIT_M,
                SINGLE_JOINT_LEVER_COMPONENT_LIMIT_M,
            )
        cursor += SEGMENT_DIMENSIONS[segment]
    assert cursor == STATE_DIMENSION
    low, high = bounds(spec)
    return np.clip(x, low + 1e-8, high - 1e-8)


def _edge_physical_cost(
    factor: EdgeFactors,
    prepared: Any,
    delta: float,
    parent_lever: np.ndarray,
    child_lever: np.ndarray,
) -> float:
    matrix, target, weight = _prepared_b5_system(prepared, delta)
    lever = np.concatenate((parent_lever, child_lever))
    residual = weight * (matrix @ lever - target) / 0.35
    pieces = [residual]
    axis = _axis_residual(factor, delta)
    if len(axis):
        pieces.append(axis)
    rom = _rom_residual(factor, delta)
    if len(rom):
        pieces.append(rom)
    values = np.concatenate(pieces)
    return float(np.mean(values * values))


def _full_circle_heading_profile(
    objective: PhysicalGraphObjective,
    geometry_seed: np.ndarray,
    raw_heading_seed: np.ndarray,
    *,
    grid_count: int = 72,
) -> tuple[np.ndarray, dict[str, Any]]:
    seed_edges = headings_to_edges(raw_heading_seed)
    decoded = decode_state(geometry_seed, objective.spec)
    output: dict[str, float] = {}
    report: dict[str, Any] = {}
    for edge, *_ in EDGES:
        parent_lever, child_lever = decoded["edge_levers"][edge]
        factor = objective.factors[edge]
        prepared = objective.prepared[edge]
        phase = float(seed_edges[edge])
        grid = wrap(phase + np.arange(grid_count) * (2.0 * math.pi / grid_count))
        cost = np.asarray([
            _edge_physical_cost(
                factor, prepared, float(value), parent_lever, child_lever,
            ) for value in grid
        ])
        best_index = int(np.argmin(cost))
        step = 2.0 * math.pi / grid_count
        best = float(grid[best_index])
        fitted = minimize_scalar(
            lambda value: _edge_physical_cost(
                factor, prepared, float(wrap(value)), parent_lever, child_lever,
            ),
            bounds=(best - step, best + step),
            method="bounded",
            options={"xatol": 1e-8, "maxiter": 100},
        )
        output[edge] = float(wrap(fitted.x))
        report[edge] = {
            "raw_seed_edge_heading_deg": float(np.degrees(phase)),
            "grid_count": grid_count,
            "full_circle_coverage_rad": 2.0 * math.pi,
            "best_grid_heading_deg": float(np.degrees(best)),
            "profiled_heading_deg": float(np.degrees(output[edge])),
            "profiled_mean_square_cost": float(fitted.fun),
        }
    return edges_to_headings(output), report


def numerical_jacobian(
    function: Any,
    x: np.ndarray,
    low: np.ndarray,
    high: np.ndarray,
    *,
    step: float = 1e-5,
) -> np.ndarray:
    base = function(x)
    jacobian = np.empty((len(base), len(x)), dtype=float)
    for column in range(len(x)):
        if x[column] - step >= low[column] and x[column] + step <= high[column]:
            minus = x.copy(); minus[column] -= step
            plus = x.copy(); plus[column] += step
            jacobian[:, column] = (function(plus) - function(minus)) / (2.0 * step)
        else:
            direction = 1.0 if x[column] + 2.0 * step <= high[column] else -1.0
            one = x.copy(); one[column] += direction * step
            two = x.copy(); two[column] += 2.0 * direction * step
            jacobian[:, column] = direction * (
                -3.0 * base + 4.0 * function(one) - function(two)
            ) / (2.0 * step)
    if not np.isfinite(jacobian).all():
        raise ValueError("non-finite physical graph Jacobian")
    return jacobian


def profiled_heading_rank(jacobian: np.ndarray) -> dict[str, Any]:
    jacobian = np.asarray(jacobian, dtype=float)
    heading = jacobian[:, :HEADING_DIMENSION]
    nuisance = jacobian[:, HEADING_DIMENSION:]
    u, nuisance_singular, _ = np.linalg.svd(nuisance, full_matrices=False)
    nuisance_cutoff = max(
        1e-8,
        float(nuisance_singular[0]) * 1e-7 if len(nuisance_singular) else 1e-8,
    )
    nuisance_rank = int(np.sum(nuisance_singular > nuisance_cutoff))
    basis = u[:, :nuisance_rank]
    effective = heading - basis @ (basis.T @ heading) if nuisance_rank else heading
    singular = np.linalg.svd(effective, compute_uv=False)
    cutoff = max(1e-8, float(singular[0]) * 1e-6 if len(singular) else 1e-8)
    rank = int(np.sum(singular > cutoff))
    return {
        "rank": rank,
        "nullity": HEADING_DIMENSION - rank,
        "singular_values": singular.tolist(),
        "threshold": cutoff,
        "nuisance_rank": nuisance_rank,
        "nuisance_singular_values": nuisance_singular.tolist(),
        "geometry_projected_out": True,
        "bounds_or_priors_counted_as_rank": False,
    }


def _project_columns_off_nuisance(
    jacobian: np.ndarray,
    target_indices: Sequence[int],
) -> tuple[np.ndarray, dict[str, Any]]:
    """Return measurement columns after removing every other state direction."""

    jacobian = np.asarray(jacobian, dtype=float)
    target = np.asarray(tuple(target_indices), dtype=int)
    nuisance_indices = np.asarray([
        index for index in range(jacobian.shape[1]) if index not in set(target)
    ], dtype=int)
    nuisance = jacobian[:, nuisance_indices]
    if nuisance.shape[1]:
        u, singular, _ = np.linalg.svd(nuisance, full_matrices=False)
        cutoff = max(
            1e-8,
            float(singular[0]) * 1e-7 if len(singular) else 1e-8,
        )
        rank = int(np.sum(singular > cutoff))
        basis = u[:, :rank]
        effective = jacobian[:, target] - basis @ (
            basis.T @ jacobian[:, target]
        )
    else:
        singular = np.empty(0)
        cutoff = 1e-8
        rank = 0
        effective = jacobian[:, target]
    return effective, {
        "nuisance_rank": rank,
        "nuisance_threshold": cutoff,
        "nuisance_singular_values": singular.tolist(),
        "all_other_state_columns_projected_out": True,
        "prior_rows_included": False,
    }


def _block_data_identifiability(
    jacobian: np.ndarray,
    target_indices: Sequence[int],
) -> dict[str, Any]:
    effective, projection = _project_columns_off_nuisance(
        jacobian, target_indices,
    )
    singular = np.linalg.svd(effective, compute_uv=False)
    cutoff = max(
        1e-8,
        float(singular[0]) * 1e-6 if len(singular) else 1e-8,
    )
    rank = int(np.sum(singular > cutoff))
    information = effective.T @ effective
    covariance = np.linalg.pinv(information, rcond=1e-12)
    coordinate_sigma = np.sqrt(np.maximum(0.0, np.diag(covariance)))
    return {
        "coordinate_indices": [int(value) for value in target_indices],
        "rank": rank,
        "dimension": len(tuple(target_indices)),
        "full_rank": rank == len(tuple(target_indices)),
        "singular_values": singular.tolist(),
        "threshold": cutoff,
        "data_only_local_coordinate_sigma_residual_scale": coordinate_sigma.tolist(),
        "maximum_data_only_local_coordinate_sigma_residual_scale": (
            float(np.max(coordinate_sigma)) if len(coordinate_sigma) else None
        ),
        "local_covariance_residual_scale": covariance.tolist(),
        **projection,
    }


def length_data_identifiability(
    jacobian: np.ndarray,
    x: np.ndarray,
    spec: PhysicalGraphSpec,
) -> dict[str, Any]:
    """Separate raw-dynamics curvature from external-prior anchoring."""

    rows = {}
    for segment, index in LENGTH_INDICES.items():
        effective, projection = _project_columns_off_nuisance(jacobian, [index])
        data_information = float(effective[:, 0] @ effective[:, 0])
        data_sigma = (
            float(1.0 / math.sqrt(data_information))
            if data_information > np.finfo(float).eps else None
        )
        prior_sigma = float(spec.segment_length_sigma_m[segment])
        prior_information = 1.0 / (prior_sigma * prior_sigma)
        lower, upper = LENGTH_BOUNDS_M[segment]
        estimate = float(x[index])
        distance_fraction = min(estimate - lower, upper - estimate) / (upper - lower)
        rows[segment] = {
            "length_coordinate_index": index,
            "estimate_m": estimate,
            "data_only_effective_information_per_m2": data_information,
            "data_only_local_sigma_m_residual_scale": data_sigma,
            "external_prior_sigma_m": prior_sigma,
            "external_prior_information_per_m2": prior_information,
            "data_information_fraction_vs_external_prior": float(
                data_information / (data_information + prior_information)
            ),
            "bounds_m": [lower, upper],
            "distance_from_nearest_bound_fraction_of_range": float(distance_fraction),
            "interior_optimum": bool(distance_fraction >= 0.02),
            "bound_active": bool(distance_fraction < 0.02),
            "prior_sensitivity": None,
            "dynamically_identified": None,
            "estimate_semantics": "PENDING_PRIOR_SENSITIVITY",
            **projection,
        }
    return rows


def _local_prior_sensitivity(
    factors: Mapping[str, EdgeFactors],
    x: np.ndarray,
    spec: PhysicalGraphSpec,
    sparsity: csr_matrix,
    *,
    maximum_function_evaluations: int = 70,
) -> dict[str, Any]:
    """Locally refit after independent +/-1 sigma tape-prior shifts."""

    low, high = bounds(spec)
    rows = {}
    for segment, index in LENGTH_INDICES.items():
        sigma = float(spec.segment_length_sigma_m[segment])
        baseline_mean = float(spec.segment_lengths_m[segment])
        estimates = {}
        for direction, label in ((-1.0, "minus_one_sigma"), (1.0, "plus_one_sigma")):
            shifted_mean = baseline_mean + direction * sigma
            lower, upper = LENGTH_BOUNDS_M[segment]
            shifted_mean = float(np.clip(
                shifted_mean, lower + 1e-6, upper - 1e-6,
            ))
            shifted_lengths = dict(spec.segment_lengths_m)
            shifted_lengths[segment] = shifted_mean
            shifted_spec = replace(spec, segment_lengths_m=shifted_lengths)
            shifted_objective = PhysicalGraphObjective(factors, shifted_spec)
            fit = least_squares(
                shifted_objective.residual,
                np.asarray(x, dtype=float),
                bounds=(low, high),
                jac="2-point",
                jac_sparsity=sparsity,
                tr_solver="lsmr",
                tr_options={"atol": 1e-11, "btol": 1e-11, "maxiter": 500},
                loss="soft_l1",
                f_scale=1.0,
                x_scale="jac",
                max_nfev=maximum_function_evaluations,
                xtol=1e-9,
                ftol=1e-9,
                gtol=1e-9,
            )
            estimates[label] = {
                "shifted_prior_mean_m": shifted_mean,
                "fitted_length_m": float(fit.x[index]),
                "estimate_shift_m": float(fit.x[index] - x[index]),
                "estimate_shift_per_one_sigma_prior_mean_shift": float(
                    abs(fit.x[index] - x[index]) / sigma
                ),
                "success": bool(fit.success),
                "finite": bool(np.isfinite(fit.x).all()),
                "nfev": int(fit.nfev),
            }
        maximum_ratio = max(
            row["estimate_shift_per_one_sigma_prior_mean_shift"]
            for row in estimates.values()
        )
        rows[segment] = {
            "method": "LOCAL_SAME_BASIN_REFIT_AFTER_INDEPENDENT_PRIOR_MEAN_SHIFT",
            "baseline_prior_mean_m": baseline_mean,
            "prior_sigma_m": sigma,
            "perturbations": estimates,
            "maximum_estimate_shift_per_one_sigma_prior_mean_shift": maximum_ratio,
            "pass": bool(
                maximum_ratio <= 0.5
                and all(row["finite"] for row in estimates.values())
            ),
            "cross_capture_data_or_parameters_used": False,
        }
    return rows


def _classify_length_identifiability(
    data_rows: dict[str, Any],
    sensitivity_rows: Mapping[str, Any],
) -> dict[str, Any]:
    output = {}
    for segment, row in data_rows.items():
        row = dict(row)
        row["prior_sensitivity"] = sensitivity_rows[segment]
        sigma = row["data_only_local_sigma_m_residual_scale"]
        gates = {
            "data_information_fraction_at_least_0p25": (
                row["data_information_fraction_vs_external_prior"] >= 0.25
            ),
            "data_only_local_sigma_at_most_0p03m": bool(
                sigma is not None and sigma <= 0.03
            ),
            "interior_optimum_at_least_2pct_from_bound": row["interior_optimum"],
            "prior_sensitivity_at_most_0p5": sensitivity_rows[segment]["pass"],
        }
        identified = all(gates.values())
        row["classification_gates"] = gates
        row["dynamically_identified"] = identified
        row["evidence_class"] = "A" if identified else "B"
        row["estimate_semantics"] = (
            "RAW_DYNAMICS_IDENTIFIED_LENGTH_WITH_EXTERNAL_SOFT_PRIOR"
            if identified
            else "WEAK_OR_PRIOR_BOUND_SELECTED_NOT_AN_IMU_LENGTH_ESTIMATE"
        )
        output[segment] = row
    return output


def endpoint_evidence_report(
    x: np.ndarray,
    spec: PhysicalGraphSpec,
    measurement_jacobian: np.ndarray,
    length_identifiability: Mapping[str, Any],
) -> dict[str, Any]:
    """Report every modeled local joint endpoint without hiding weak proxies."""

    x = np.asarray(x, dtype=float)
    low, high = bounds(spec)
    block_slices = {
        "pelvis": slice(HEADING_DIMENSION, HEADING_DIMENSION + PELVIS_DIMENSION),
        "torso": slice(
            HEADING_DIMENSION + PELVIS_DIMENSION,
            HEADING_DIMENSION + PELVIS_DIMENSION + TORSO_DIMENSION,
        ),
        **SEGMENT_SLICES,
    }
    blocks = {
        segment: _block_data_identifiability(
            measurement_jacobian,
            range(block.start, block.stop),
        )
        for segment, block in block_slices.items()
    }

    def point(segment: str, endpoint: str, state: np.ndarray) -> np.ndarray:
        row = decode_state(state, spec)["segment_geometry"][segment]
        if segment in ("pelvis", "torso"):
            if endpoint == "segment_center":
                return np.asarray(row["center"], dtype=float)
            return np.asarray(row["points"][endpoint], dtype=float)
        return np.asarray(row[endpoint], dtype=float)

    def uncertainty(segment: str, endpoint: str) -> np.ndarray:
        block = block_slices[segment]
        indices = list(range(block.start, block.stop))
        derivative = np.empty((3, len(indices)), dtype=float)
        for column, index in enumerate(indices):
            step = 1e-5
            minus = x.copy(); minus[index] -= step
            plus = x.copy(); plus[index] += step
            derivative[:, column] = (
                point(segment, endpoint, plus) - point(segment, endpoint, minus)
            ) / (2.0 * step)
        covariance = np.asarray(
            blocks[segment]["local_covariance_residual_scale"], dtype=float,
        )
        propagated = derivative @ covariance @ derivative.T
        return np.sqrt(np.maximum(0.0, np.diag(propagated)))

    def bound_activity(segment: str) -> dict[str, Any]:
        block = block_slices[segment]
        scale = np.maximum(high[block] - low[block], 1e-12)
        fraction = np.minimum(x[block] - low[block], high[block] - x[block]) / scale
        return {
            "active": bool(np.any(fraction < 0.02)),
            "minimum_distance_from_bound_fraction_of_coordinate_range": float(
                np.min(fraction)
            ),
            "criterion": "LESS_THAN_2_PERCENT_OF_COORDINATE_RANGE",
        }

    endpoint_names = {
        "pelvis": ("segment_center", "pelvis_torso", "hip_left", "hip_right"),
        "torso": ("pelvis_torso", "shoulder_left", "shoulder_right"),
        "upper_arm_left": ("proximal", "distal"),
        "upper_arm_right": ("proximal", "distal"),
        "thigh_left": ("proximal", "distal"),
        "thigh_right": ("proximal", "distal"),
        "forearm_left": ("proximal", "distal_proxy"),
        "forearm_right": ("proximal", "distal_proxy"),
        "shank_left": ("proximal", "distal_proxy"),
        "shank_right": ("proximal", "distal_proxy"),
    }
    anatomical_name = {
        ("pelvis", "segment_center"): "pelvis_center",
        ("pelvis", "pelvis_torso"): "pelvis_torso",
        ("pelvis", "hip_left"): "hip_left",
        ("pelvis", "hip_right"): "hip_right",
        ("torso", "pelvis_torso"): "pelvis_torso",
        ("torso", "shoulder_left"): "shoulder_left",
        ("torso", "shoulder_right"): "shoulder_right",
        ("upper_arm_left", "proximal"): "shoulder_left",
        ("upper_arm_left", "distal"): "elbow_left",
        ("upper_arm_right", "proximal"): "shoulder_right",
        ("upper_arm_right", "distal"): "elbow_right",
        ("thigh_left", "proximal"): "hip_left",
        ("thigh_left", "distal"): "knee_left",
        ("thigh_right", "proximal"): "hip_right",
        ("thigh_right", "distal"): "knee_right",
        ("forearm_left", "proximal"): "elbow_left",
        ("forearm_left", "distal_proxy"): "wrist_left",
        ("forearm_right", "proximal"): "elbow_right",
        ("forearm_right", "distal_proxy"): "wrist_right",
        ("shank_left", "proximal"): "knee_left",
        ("shank_left", "distal_proxy"): "ankle_left",
        ("shank_right", "proximal"): "knee_right",
        ("shank_right", "distal_proxy"): "ankle_right",
    }
    rows = {}
    for segment, names in endpoint_names.items():
        for endpoint in names:
            estimate = point(segment, endpoint, x)
            sigma = uncertainty(segment, endpoint)
            is_proxy = endpoint == "distal_proxy"
            if is_proxy:
                proxy_length = float(spec.segment_lengths_m[segment])
                proxy_sigma = float(spec.segment_length_sigma_m[segment])
                angular_radius = proxy_length * math.sin(math.radians(
                    spec.lower_proxy_axis_uncertainty_deg
                ))
                sigma = np.sqrt(
                    sigma * sigma + proxy_sigma * proxy_sigma
                    + (angular_radius * angular_radius / 3.0)
                )
                evidence_class = "C"
                source = (
                    "BROAD_ANATOMICAL_LENGTH_AND_AXIS_PROXY_PROPAGATED_FROM_"
                    "DYNAMIC_PROXIMAL_JOINT_CENTER;NO_DISTAL_SENSOR_OR_FIXED_CONTACT_FACTOR"
                )
            elif segment in TWO_JOINT_SEGMENTS:
                identified = bool(length_identifiability[segment]["dynamically_identified"])
                evidence_class = "A" if identified else "B"
                source = (
                    "COMPLETE_REST_TRANSITION_ACTION_RETURN_REST_"
                    "PARENT_CHILD_JOINT_CENTER_ACCELERATION"
                )
            elif segment in ("pelvis", "torso"):
                evidence_class = "B"
                source = (
                    "CAPTURE_WIDE_MULTI_EDGE_ACCELERATION_GRAPH_CLOSURE_WITH_"
                    "PREDECLARED_BROAD_BODY_FRAME_GEOMETRY"
                )
            else:
                block = blocks[segment]
                strong = bool(
                    block["full_rank"]
                    and block["maximum_data_only_local_coordinate_sigma_residual_scale"] <= 0.05
                )
                evidence_class = "A" if strong else "B"
                source = (
                    "COMPLETE_REST_TRANSITION_ACTION_RETURN_REST_"
                    "PARENT_CHILD_JOINT_CENTER_ACCELERATION"
                )
            rows[f"{segment}:{endpoint}"] = {
                "segment": segment,
                "endpoint_role": endpoint,
                "anatomical_joint": anatomical_name[(segment, endpoint)],
                "estimate_sensor_local_m": estimate.tolist(),
                "uncertainty_model": (
                    "LOCAL_LINEAR_DATA_CURVATURE_PLUS_BROAD_PROXY_PRIOR"
                    if is_proxy else "LOCAL_LINEAR_MEASUREMENT_ONLY_CURVATURE_RESIDUAL_SCALE"
                ),
                "one_sigma_component_uncertainty_m": sigma.tolist(),
                "approximate_95pct_component_interval_m": np.column_stack((
                    estimate - 1.96 * sigma,
                    estimate + 1.96 * sigma,
                )).tolist(),
                "bound_activity": bound_activity(segment),
                "evidence_class": evidence_class,
                "dominant_evidence_source": source,
                "raw_data_identified_distal_endpoint": bool(not is_proxy),
                "static_pose_or_action_label_used_as_metric_truth": False,
            }
    return {
        "schema": "biospur-pure-imu-v0-endpoint-evidence-ledger-v1",
        "evidence_class_definitions": {
            "A": "DYNAMICALLY_IDENTIFIED_FUNCTIONAL_JOINT_CENTER_OR_LENGTH",
            "B": "WEAKLY_IDENTIFIED_BY_DYNAMICS_GRAPH_CLOSURE_ROM_OR_CONTACT_PIVOT",
            "C": "PRIOR_PROPAGATED_ANATOMICAL_PROXY_NOT_RAW_DATA_IDENTIFIED",
        },
        "local_block_data_identifiability": blocks,
        "endpoints": rows,
        "every_modeled_endpoint_reported": True,
        "static_t_pose_metric_factor": False,
        "action_label_metric_factor": False,
    }


def _fixed_point_candidate(
    episode: Raw6Episode,
    segment: str,
    heading: float,
) -> tuple[dict[str, Any], tuple[np.ndarray, np.ndarray]]:
    """Fit one sensor-local fixed point using signal equations, not a label."""

    count = len(episode.time_ns)
    indices = np.unique(np.rint(np.linspace(
        0, count - 1, min(120, count),
    )).astype(int))
    dt = float(np.median(np.diff(episode.time_ns))) * 1e-9
    gyro = np.asarray(episode.gyro[segment], dtype=float)
    alpha = np.gradient(gyro, dt, axis=0, edge_order=2)
    kinematic = np.asarray([
        _skew(alpha[index]) + _skew(gyro[index]) @ _skew(gyro[index])
        for index in indices
    ])
    rotation = np.einsum(
        "ij,njk->nik", rz(heading),
        episode.rotation_world_sensor[segment][indices],
    )
    force = np.einsum(
        "nij,nj->ni", rotation, episode.acc[segment][indices],
    )
    lever_matrix = np.einsum("nij,njk->nik", rotation, kinematic)
    # A physically fixed point has constant world specific acceleration
    # (gravity).  Fit that constant as a nuisance rather than assuming an
    # exact VQF world frame or gravity magnitude.
    matrix = np.concatenate((lever_matrix, -np.broadcast_to(
        np.eye(3), lever_matrix.shape,
    )), axis=2).reshape(-1, 6)
    target = -force.reshape(-1)
    solution, _, _, _ = np.linalg.lstsq(matrix, target, rcond=1e-10)
    residual = (matrix @ solution - target).reshape(-1, 3)
    baseline_constant = np.mean(force, axis=0)
    baseline = force - baseline_constant
    activity = (
        np.linalg.norm(alpha[indices], axis=1)
        + np.linalg.norm(gyro[indices], axis=1) ** 2
    )
    effective, projection = _project_columns_off_nuisance(matrix, (0, 1, 2))
    singular = np.linalg.svd(effective, compute_uv=False)
    cutoff = max(1e-8, float(singular[0]) * 1e-6 if len(singular) else 1e-8)
    rank = int(np.sum(singular > cutoff))
    rms = float(np.sqrt(np.mean(residual * residual)))
    baseline_rms = float(np.sqrt(np.mean(baseline * baseline)))

    split_candidates = []
    for selected in np.array_split(np.arange(len(indices)), 2):
        if len(selected) < 6:
            continue
        row_indices = np.concatenate([
            np.arange(3 * row, 3 * row + 3) for row in selected
        ])
        split_solution, _, _, _ = np.linalg.lstsq(
            matrix[row_indices], target[row_indices], rcond=1e-10,
        )
        split_candidates.append(split_solution[:3])
    split_difference = (
        float(np.linalg.norm(split_candidates[0] - split_candidates[1]))
        if len(split_candidates) == 2 else None
    )
    return {
        "action": episode.action,
        "partition": episode.partition,
        "rows": int(len(indices)),
        "dynamic_rows_activity_gt_0p5": int(np.count_nonzero(activity > 0.5)),
        "candidate_fixed_point_sensor_local_m": solution[:3].tolist(),
        "constant_world_specific_force_nuisance_mps2": solution[3:].tolist(),
        "fixed_point_rms_mps2": rms,
        "sensor_origin_constant_acceleration_rms_mps2": baseline_rms,
        "fractional_rms_improvement": float(
            1.0 - rms / max(baseline_rms, np.finfo(float).eps)
        ),
        "lever_data_rank": rank,
        "lever_singular_values": singular.tolist(),
        "lever_rank_threshold": cutoff,
        "first_half_second_half_candidate_difference_m": split_difference,
        "signal_only_fit": True,
        "action_name_used_as_contact_truth": False,
        "static_pose_used_as_metric_truth": False,
        **projection,
    }, (matrix, target)


def distal_pivot_evidence_audit(
    episodes: Sequence[Raw6Episode],
    result: Mapping[str, Any],
    spec: PhysicalGraphSpec,
) -> dict[str, Any]:
    """Audit all motion for distal fixed-contact evidence before using a proxy."""

    decoded = decode_state(np.asarray(result["state_coordinates"], dtype=float), spec)
    rows = {}
    for segment in ("forearm_left", "forearm_right", "shank_left", "shank_right"):
        proximal = np.asarray(decoded["segment_geometry"][segment]["proximal"], dtype=float)
        proxy_length = float(spec.segment_lengths_m[segment])
        proxy_sigma = float(spec.segment_length_sigma_m[segment])
        episode_rows = []
        eligible_systems = []
        for episode in episodes:
            diagnostic, system = _fixed_point_candidate(
                episode, segment, float(result["headings_rad"][segment]),
            )
            candidate = np.asarray(
                diagnostic["candidate_fixed_point_sensor_local_m"], dtype=float,
            )
            bone = candidate - proximal
            distance = float(np.linalg.norm(bone))
            through_sensor = -proximal
            directional_cosine = (
                float(bone @ through_sensor / (
                    max(distance, 1e-12) * max(np.linalg.norm(through_sensor), 1e-12)
                ))
                if np.linalg.norm(through_sensor) > 1e-8 else None
            )
            geometric = bool(
                abs(distance - proxy_length) <= 2.0 * proxy_sigma
                and directional_cosine is not None
                and directional_cosine >= 0.70
            )
            signal = bool(
                diagnostic["lever_data_rank"] == 3
                and diagnostic["dynamic_rows_activity_gt_0p5"] >= 20
                and diagnostic["fixed_point_rms_mps2"] <= 0.75
                and diagnostic["fractional_rms_improvement"] >= 0.25
                and diagnostic["first_half_second_half_candidate_difference_m"] is not None
                and diagnostic["first_half_second_half_candidate_difference_m"] <= 0.06
            )
            diagnostic.update({
                "candidate_distance_from_proximal_joint_m": distance,
                "broad_proxy_length_mean_m": proxy_length,
                "broad_proxy_length_sigma_m": proxy_sigma,
                "candidate_distal_direction_cosine": directional_cosine,
                "distal_geometry_consistency": geometric,
                "signal_supported_fixed_point": signal,
                "signal_and_distal_geometry_candidate": bool(signal and geometric),
            })
            episode_rows.append(diagnostic)
            if signal and geometric:
                eligible_systems.append(system)

        global_fit = None
        evidence_class = "C"
        distal = np.asarray(
            decoded["segment_geometry"][segment]["distal_proxy"], dtype=float,
        )
        if eligible_systems:
            matrix = np.concatenate([system[0] for system in eligible_systems])
            target = np.concatenate([system[1] for system in eligible_systems])
            solution, _, _, _ = np.linalg.lstsq(matrix, target, rcond=1e-10)
            residual = matrix @ solution - target
            effective, _ = _project_columns_off_nuisance(matrix, (0, 1, 2))
            information = effective.T @ effective
            covariance = np.linalg.pinv(information, rcond=1e-12)
            residual_scale = float(np.sqrt(np.mean(residual * residual)))
            sigma = np.sqrt(np.maximum(0.0, np.diag(covariance))) * residual_scale
            global_candidate = solution[:3]
            distance = float(np.linalg.norm(global_candidate - proximal))
            global_pass = bool(
                abs(distance - proxy_length) <= 2.0 * proxy_sigma
                and residual_scale <= 0.75
            )
            global_fit = {
                "candidate_fixed_point_sensor_local_m": global_candidate.tolist(),
                "one_sigma_component_uncertainty_m": sigma.tolist(),
                "fixed_point_rms_mps2": residual_scale,
                "candidate_distance_from_proximal_joint_m": distance,
                "source_episode_count": len(eligible_systems),
                "pass": global_pass,
            }
            if global_pass:
                distal = global_candidate
                evidence_class = "B"
        rows[segment] = {
            "all_complete_suite_episodes_audited": True,
            "episode_diagnostics": episode_rows,
            "signal_supported_distal_pivot_episode_count": len(eligible_systems),
            "capture_wide_combined_pivot_fit": global_fit,
            "selected_distal_endpoint_sensor_local_m": distal.tolist(),
            "selected_evidence_class": evidence_class,
            "dominant_evidence_source": (
                "SIGNAL_SUPPORTED_FIXED_POINT_PHYSICS_ACROSS_ELIGIBLE_COMPLETE_EPISODES"
                if evidence_class == "B"
                else "BROAD_PRIOR_PROPAGATED_PROXY_AFTER_SIGNAL_ONLY_FIXED_POINT_AUDIT_FOUND_NO_DISTAL_SUPPORT"
            ),
            "full_segment_length_raw_data_identified": False,
        }
    return {
        "schema": "biospur-pure-imu-v0-distal-fixed-point-evidence-audit-v1",
        "segments": rows,
        "action_labels_used_as_contact_or_metric_truth": False,
        "every_complete_suite_episode_audited": True,
        "per_action_calibration_performed": False,
        "role": "ENDPOINT_EVIDENCE_CLASSIFICATION_AND_OPTIONAL_WEAK_PROXY_REFINEMENT",
    }


def merge_distal_pivot_evidence(
    endpoint_evidence: dict[str, Any],
    pivot_audit: Mapping[str, Any],
) -> None:
    """Attach the signal audit and, only when supported, promote C to weak B."""

    for segment, audit in pivot_audit["segments"].items():
        key = f"{segment}:distal_proxy"
        row = endpoint_evidence["endpoints"][key]
        row["distal_fixed_point_audit"] = audit
        row["dominant_evidence_source"] = audit["dominant_evidence_source"]
        if audit["selected_evidence_class"] != "B":
            continue
        global_fit = audit["capture_wide_combined_pivot_fit"]
        estimate = np.asarray(
            global_fit["candidate_fixed_point_sensor_local_m"], dtype=float,
        )
        sigma = np.asarray(
            global_fit["one_sigma_component_uncertainty_m"], dtype=float,
        )
        # A fixed-point result without a distal sensor remains weak class B;
        # include the broad anatomical proxy uncertainty so a small residual
        # cannot create false millimetric precision.
        sigma = np.maximum(sigma, 0.5 * np.asarray(
            row["one_sigma_component_uncertainty_m"], dtype=float,
        ))
        row.update({
            "estimate_sensor_local_m": estimate.tolist(),
            "one_sigma_component_uncertainty_m": sigma.tolist(),
            "approximate_95pct_component_interval_m": np.column_stack((
                estimate - 1.96 * sigma,
                estimate + 1.96 * sigma,
            )).tolist(),
            "uncertainty_model": (
                "SIGNAL_FIXED_POINT_LOCAL_CURVATURE_FLOORED_BY_BROAD_ANATOMICAL_PROXY"
            ),
            "evidence_class": "B",
            "raw_data_identified_distal_endpoint": False,
            "weak_signal_supported_fixed_point": True,
        })


def evaluate_physical_b5(
    factor: EdgeFactors,
    delta: float,
    parent_lever: np.ndarray,
    child_lever: np.ndarray,
    *,
    held_out: bool,
) -> dict[str, Any]:
    blocks = factor.b5_held_out if held_out else factor.b5_train
    physical_rows = []
    per_action: dict[str, list[np.ndarray]] = {}
    for block in blocks:
        transform = np.array([
            [math.cos(delta), -math.sin(delta), 0.0],
            [math.sin(delta), math.cos(delta), 0.0],
            [0.0, 0.0, 1.0],
        ])
        rp = block.parent_rotation
        rc = np.einsum("ij,njk->nik", transform, block.child_rotation)
        ap = np.einsum("nij,njk->nik", rp, block.parent_kinematic)
        ac = np.einsum("nij,njk->nik", rc, block.child_kinematic)
        fp = np.einsum("nij,nj->ni", rp, block.parent_force)
        fc = np.einsum("nij,nj->ni", rc, block.child_force)
        parent_joint = fp + np.einsum("nij,j->ni", ap, parent_lever)
        child_joint = fc + np.einsum("nij,j->ni", ac, child_lever)
        residual = parent_joint - child_joint
        physical_rows.append(residual)
        per_action.setdefault(block.action, []).append(residual)
    if not physical_rows:
        return {"rows": 0, "physical_rms_mps2": None, "per_action": {}}
    all_rows = np.concatenate(physical_rows)
    return {
        "rows": int(len(all_rows)),
        "physical_rms_mps2": float(np.sqrt(np.mean(all_rows * all_rows))),
        "per_action": {
            action: {
                "rows": int(len(np.concatenate(rows))),
                "physical_rms_mps2": float(np.sqrt(np.mean(
                    np.concatenate(rows) ** 2
                ))),
            }
            for action, rows in per_action.items()
        },
    }


def fit_physical_graph(
    factors: Mapping[str, EdgeFactors],
    initial_headings: np.ndarray,
    spec: PhysicalGraphSpec,
    *,
    starts: int = 24,
    seed: int = 20260828,
    maximum_function_evaluations: int = 160,
) -> dict[str, Any]:
    """Fit the capture-wide graph with no independent edge lever coordinates."""

    if starts < 24:
        raise ValueError("physical graph qualification requires at least 24 broad starts")
    structure = spec.validate()
    objective = PhysicalGraphObjective(factors, spec)
    base = initialize_state(factors, initial_headings, spec)
    low, high = bounds(spec)
    rng = np.random.default_rng(seed)
    raw_starts = [np.asarray(initial_headings, dtype=float)]
    raw_starts.extend(rng.uniform(-math.pi, math.pi, 9) for _ in range(starts - 1))

    probe = np.clip(
        base + 0.01 * np.sin(np.arange(STATE_DIMENSION) + 0.37),
        low + 1e-8,
        high - 1e-8,
    )
    structural = (
        np.abs(numerical_jacobian(objective.residual, base, low, high)) > 1e-13
    ) | (
        np.abs(numerical_jacobian(objective.residual, probe, low, high)) > 1e-13
    )
    sparsity = csr_matrix(structural)
    fits = []
    for start_index, raw_heading in enumerate(raw_starts):
        x0 = base.copy()
        x0[:HEADING_DIMENSION], profile = _full_circle_heading_profile(
            objective, x0, np.asarray(raw_heading, dtype=float),
        )
        if start_index:
            perturb = rng.normal(0.0, 0.025, GEOMETRY_DIMENSION)
            perturb[:6] = rng.normal(0.0, 0.04, 6)
            x0[HEADING_DIMENSION:] = np.clip(
                x0[HEADING_DIMENSION:] + perturb,
                low[HEADING_DIMENSION:] + 1e-8,
                high[HEADING_DIMENSION:] - 1e-8,
            )
        result = least_squares(
            objective.residual,
            x0,
            bounds=(low, high),
            jac="2-point",
            jac_sparsity=sparsity,
            tr_solver="lsmr",
            tr_options={"atol": 1e-11, "btol": 1e-11, "maxiter": 500},
            loss="soft_l1",
            f_scale=1.0,
            x_scale="jac",
            max_nfev=maximum_function_evaluations,
            xtol=1e-9,
            ftol=1e-9,
            gtol=1e-9,
        )
        fits.append({
            "start": start_index,
            "raw_heading": wrap(raw_heading),
            "profile": profile,
            "x": result.x,
            "cost": float(result.cost),
            "optimality": float(result.optimality),
            "nfev": int(result.nfev),
            "success": bool(result.success),
            "finite": bool(np.isfinite(result.x).all() and np.isfinite(result.fun).all()),
            "message": str(result.message),
        })
    finite = [row for row in fits if row["finite"]]
    if not finite:
        raise RuntimeError("all physical graph starts produced non-finite state")
    best = min(finite, key=lambda row: row["cost"])
    x = np.asarray(best["x"], dtype=float)
    decoded = decode_state(x, spec)
    measurement_jacobian = numerical_jacobian(
        objective.measurement_residual, x, low, high,
    )
    rank = profiled_heading_rank(measurement_jacobian)
    length_data = length_data_identifiability(
        measurement_jacobian, x, spec,
    )
    sensitivity = _local_prior_sensitivity(
        factors, x, spec, sparsity,
    )
    length_identifiability = _classify_length_identifiability(
        length_data, sensitivity,
    )
    endpoint_evidence = endpoint_evidence_report(
        x, spec, measurement_jacobian, length_identifiability,
    )
    heading_by_start = np.asarray([
        wrap(row["x"][:HEADING_DIMENSION]) for row in finite
    ])
    spread = {
        segment: circular_spread_deg(heading_by_start[:, index])
        for index, segment in enumerate(SEGMENTS[1:])
    }
    node_heading = np.asarray([
        decoded["headings"][segment] for segment in SEGMENTS[1:]
    ])
    delta = headings_to_edges(node_heading)
    edges = {}
    lever_by_edge = {}
    for edge, parent, child, _ in EDGES:
        parent_lever, child_lever = decoded["edge_levers"][edge]
        lever = np.concatenate((parent_lever, child_lever))
        lever_by_edge[edge] = lever.tolist()
        edges[edge] = {
            "parent": parent,
            "child": child,
            "relative_heading_rad": delta[edge],
            "relative_heading_deg": float(np.degrees(delta[edge])),
            "parent_connection_point_sensor_m": parent_lever.tolist(),
            "child_connection_point_sensor_m": child_lever.tolist(),
            "train": evaluate_physical_b5(
                factors[edge], delta[edge], parent_lever, child_lever,
                held_out=False,
            ),
            "held_out": evaluate_physical_b5(
                factors[edge], delta[edge], parent_lever, child_lever,
                held_out=True,
            ),
        }
    audit = structural_audit(x, spec)
    geometry = {
        segment: {
            key: (
                value.tolist() if isinstance(value, np.ndarray) else value
            )
            for key, value in row.items()
            if key != "points"
        } | ({
            "points": {key: value.tolist() for key, value in row["points"].items()}
        } if "points" in row else {})
        for segment, row in decoded["segment_geometry"].items()
    }
    return {
        "schema": "biospur-pure-imu-v0-physical-nine-heading-graph-v1",
        "headings_rad": decoded["headings"],
        "headings_deg": {
            key: float(np.degrees(value)) for key, value in decoded["headings"].items()
        },
        "relative_edge_headings_rad": delta,
        "edges": edges,
        "lever_by_edge": lever_by_edge,
        "segment_geometry": geometry,
        "length_identifiability": length_identifiability,
        "endpoint_evidence": endpoint_evidence,
        "state_coordinates": x.tolist(),
        "state_layout": STATE_LAYOUT,
        "root_yaw_gauge_count": 1,
        "root_yaw_gauge_segment": "pelvis",
        "publishable_heading_dimension": HEADING_DIMENSION,
        "numeric_rank_after_gauge": rank["rank"],
        "numeric_nullity_after_gauge": rank["nullity"],
        "numeric_singular_values": rank["singular_values"],
        "numeric_rank_threshold": rank["threshold"],
        "profiled_rank_detail": rank,
        "multistart": [{
            "start": row["start"],
            "raw_broad_start_headings_deg": np.degrees(row["raw_heading"]).tolist(),
            "full_circle_profile": row["profile"],
            "headings_deg": np.degrees(wrap(row["x"][:9])).tolist(),
            "cost": row["cost"],
            "optimality": row["optimality"],
            "nfev": row["nfev"],
            "success": row["success"],
            "finite": row["finite"],
            "message": row["message"],
        } for row in fits],
        "multistart_spread_deg": spread,
        "multistart_max_spread_deg": float(max(spread.values())),
        "best_cost": float(best["cost"]),
        "multistart_contract": {
            "start_count": starts,
            "raw_start_distribution": "FIRST_EDGEWISE_THEN_INDEPENDENT_UNIFORM_FULL_CIRCLE_NINE_HEADING",
            "full_circle_grid_points_per_edge_per_start": 72,
            "all_nine_coordinates_receive_full_circle_coverage_per_start": True,
            "geometry_perturbed_across_starts": True,
            "local_basin_only": False,
        },
        "structural_audit": audit,
        "structure_contract": structure,
        "physical_spec": {
            "external_prior_mean_or_distal_proxy_length_m": dict(spec.segment_lengths_m),
            "segment_length_sigma_m": dict(spec.segment_length_sigma_m),
            "segment_length_source": dict(spec.segment_length_source),
            "torso_prior_mean_m": spec.torso_prior_mean_m,
            "torso_prior_sigma_m": spec.torso_prior_sigma_m,
            "torso_range_m": [spec.torso_min_m, spec.torso_max_m],
            "bilateral_equality_constraint": False,
            "dynamic_length_segments": list(TWO_JOINT_SEGMENTS),
            "lower_segment_full_length_in_measurement_state": False,
        },
        "one_time_resolved_multi_action_objective": True,
        "per_action_recalibration": False,
        "cross_capture_parameters": False,
        "independent_edge_levers_profiled_in_product_fit": False,
        "raw_accelerometer_gyroscope_only": True,
        "static_t_pose_used_as_metric_length_factor": False,
        "action_labels_used_as_metric_truth": False,
    }


def subset_factors_by_phase(
    factors: Mapping[str, EdgeFactors],
    *,
    include_transitions: bool,
) -> dict[str, EdgeFactors]:
    if include_transitions:
        return dict(factors)

    def select(blocks: Sequence[Any]) -> tuple[Any, ...]:
        output = []
        for block in blocks:
            keep = block.phase == "FORMAL_ACTION_OR_HOLD"
            if np.count_nonzero(keep) < 3:
                continue
            output.append(type(block)(
                block.action,
                block.partition,
                block.phase[keep],
                block.parent_rotation[keep],
                block.child_rotation[keep],
                block.parent_force[keep],
                block.child_force[keep],
                block.parent_kinematic[keep],
                block.child_kinematic[keep],
                block.sample_weight[keep],
            ))
        return tuple(output)

    return {
        edge: EdgeFactors(
            factor.name, factor.parent, factor.child, factor.kind,
            select(factor.b5_train), select(factor.b5_held_out),
            factor.hinge_axis_parent, factor.hinge_axis_child,
            factor.qmt_report, factor.axis_report,
        )
        for edge, factor in factors.items()
    }


def transition_ablation_physical(
    factors: Mapping[str, EdgeFactors],
    full: Mapping[str, Any],
    spec: PhysicalGraphSpec,
    *,
    seed: int,
) -> dict[str, Any]:
    plateau_factors = subset_factors_by_phase(factors, include_transitions=False)
    del seed
    # An ablation is an information audit of the unchanged fitted profile, not
    # a competing formal-action-only calibration.  Re-fitting it would make
    # its heading change a local-basin result and blur the product authority.
    # Evaluate and project the formal-only measurement Jacobian at the exact
    # fixed full-episode state instead.
    x = np.asarray(full["state_coordinates"], dtype=float)
    objective = PhysicalGraphObjective(plateau_factors, spec)
    low, high = bounds(spec)
    plateau_jacobian = numerical_jacobian(
        objective.measurement_residual, x, low, high,
    )
    plateau_rank = profiled_heading_rank(plateau_jacobian)
    full_singular = np.asarray(full["numeric_singular_values"], dtype=float)
    plateau_singular = np.asarray(plateau_rank["singular_values"], dtype=float)
    formal_residual = objective.measurement_residual(x)
    return {
        "schema": "biospur-pure-imu-v0-physical-graph-transition-ablation-v1",
        "full_complete_episode_rank": full["numeric_rank_after_gauge"],
        "formal_action_only_rank": plateau_rank["rank"],
        "full_smallest_singular": float(full_singular[-1]),
        "formal_action_only_smallest_singular": float(plateau_singular[-1]),
        "smallest_singular_information_retained_ratio": float(
            plateau_singular[-1] / max(full_singular[-1], np.finfo(float).eps)
        ),
        "formal_action_only_fixed_profile_rms": float(np.sqrt(np.mean(formal_residual ** 2))),
        "heading_change_not_computed": "ABLATION_DOES_NOT_REFIT_OR_RECALIBRATE_THE_FIXED_FULL_EPISODE_PROFILE",
        "formal_action_only_rank_detail": plateau_rank,
        "interpretation": "TRANSITIONS_ARE_PART_OF_PRIMARY_FIT;FIXED_PROFILE_ABLATION_QUANTIFIES_INFORMATION_AND_NEED_NOT_BE_INVARIANT",
    }
