"""Observability-aware progressive pure-IMU V0 calibration.

This module keeps one state and one cumulative factor set per capture.  An
episode is never fitted as a standalone calibration.  Measurement residuals
use a soft-L1 M-estimator while Gaussian priors remain exact quadratic rows.

The implementation deliberately calls qmt's installed Olsson primitive only
through :mod:`raw6_heading`; no qmt source is copied here.  The joint-centre
rows are the integration-free rigid-body acceleration constraint used by Seel
et al.  Four pelvis/torso template dimensions remain latent coordinates.  A
real-subject run may attach explicit Gaussian engineering priors derived from
external surface measurements, but the data-only information audit remains
separate and still controls whether those coordinates may be published as
IMU-observed geometry.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
import math
import time
from typing import Any, Iterable, Mapping, Sequence

import numpy as np
from scipy.optimize import least_squares
from scipy.sparse import csr_matrix
from scipy.spatial.transform import Rotation

from .physical_graph import (
    HEADING_DIMENSION,
    LENGTH_INDICES,
    LIMB_SEGMENTS,
    SEGMENTS,
    STATE_DIMENSION as CORE_STATE_DIMENSION,
    TWO_JOINT_SEGMENTS,
    PhysicalGraphObjective,
    PhysicalGraphSpec,
    _full_circle_heading_profile,
    bounds as core_bounds,
    decode_state,
    evaluate_physical_b5,
    initialize_state,
    numerical_jacobian,
    real_subject_spec,
    structural_audit,
)
from .raw6_heading import (
    B5Block,
    EDGES,
    EdgeFactors,
    Raw6Episode,
    b5_blocks,
    circular_spread_deg,
    fit_edgewise,
    wrap,
)
from .math3d import rz


LATENT_NAMES = (
    "pelvis_hip_point_separation_m",
    "pelvis_hip_to_torso_span_m",
    "torso_axial_joint_span_m",
    "torso_shoulder_point_separation_m",
)
LATENT_DIMENSION = len(LATENT_NAMES)
STATE_DIMENSION = CORE_STATE_DIMENSION + LATENT_DIMENSION
LATENT_SLICE = slice(CORE_STATE_DIMENSION, STATE_DIMENSION)
LATENT_LOWER = np.array([0.10, 0.05, 0.19, 0.18], dtype=float)
LATENT_UPPER = np.array([0.45, 0.32, 0.64, 0.62], dtype=float)
LATENT_SEED = np.array([0.24, 0.12, 0.35, 0.36], dtype=float)
VERIFIED_REST_PHASES = ("VERIFIED_PRE_REST", "VERIFIED_POST_REST")
STANDING_REFERENCE_ACTIONS = ("00_initial_still", "17_final_still")
LOWER_LIMB_MINIMUM_LATERAL_ORDER_M = 0.02
LOWER_LIMB_TOPOLOGY_SIGMA_M = 0.02
STANDING_FRAME_MINIMUM_DOT = math.cos(math.radians(30.0))
STANDING_FRAME_DOT_SIGMA = 0.10
STANDING_CHAIN_MINIMUM_VERTICAL_DROP_M = 0.05
STANDING_CHAIN_VERTICAL_SIGMA_M = 0.05


@dataclass(frozen=True)
class ProgressiveGeometryPrior:
    """Explicit Gaussian prior for the four progressive geometry latents.

    The means are not anatomical truth.  They are a declared mapping from
    palpable surface measurements and broad display-scale closure to internal
    joint-template coordinates.  Their mapping uncertainty is intentionally
    wider than the raw tape repeat spread, and the rows stay outside the
    robust sensor loss.
    """

    mean_m: tuple[float, float, float, float]
    sigma_m: tuple[float, float, float, float]
    source: tuple[str, str, str, str]
    provenance_path: str

    def validate(self) -> None:
        mean = np.asarray(self.mean_m, dtype=float)
        sigma = np.asarray(self.sigma_m, dtype=float)
        if mean.shape != (LATENT_DIMENSION,) or sigma.shape != (LATENT_DIMENSION,):
            raise ValueError("progressive geometry prior has the wrong dimension")
        if not np.isfinite(mean).all() or not np.isfinite(sigma).all():
            raise ValueError("progressive geometry prior contains non-finite values")
        if np.any(sigma <= 0.0):
            raise ValueError("progressive geometry prior sigma must be positive")
        if np.any(mean <= LATENT_LOWER) or np.any(mean >= LATENT_UPPER):
            raise ValueError("progressive geometry prior mean must be inside bounds")
        if len(self.source) != LATENT_DIMENSION or not all(self.source):
            raise ValueError("progressive geometry prior lacks coordinate provenance")
        if not self.provenance_path:
            raise ValueError("progressive geometry prior lacks source path")

    def residual(self, latent: np.ndarray) -> np.ndarray:
        self.validate()
        latent = np.asarray(latent, dtype=float)
        if latent.shape != (LATENT_DIMENSION,):
            raise ValueError("progressive latent geometry has the wrong dimension")
        return (
            latent - np.asarray(self.mean_m, dtype=float)
        ) / np.asarray(self.sigma_m, dtype=float)

    def audit(self) -> dict[str, Any]:
        self.validate()
        return {
            "coordinate_order": list(LATENT_NAMES),
            "mean_m": list(self.mean_m),
            "sigma_m": list(self.sigma_m),
            "source": dict(zip(LATENT_NAMES, self.source, strict=True)),
            "provenance_path": self.provenance_path,
            "loss_class": "EXACT_GAUSSIAN_OUTSIDE_ROBUST_SENSOR_LOSS",
            "surface_measurements_relabelled_as_internal_truth": False,
        }


REAL_SUBJECT_PROGRESSIVE_GEOMETRY_PRIOR = ProgressiveGeometryPrior(
    mean_m=(0.24, 0.14, 0.415, 0.36),
    sigma_m=(0.06, 0.06, 0.08, 0.05),
    source=(
        "BICRISTAL_AND_BITROCHANTERIC_BREADTH_TO_BROAD_HIP_AXIS_PROXY",
        "BROAD_PELVIS_HIP_LINE_TO_TORSO_CONNECTION_ENGINEERING_PRIOR",
        "STATURE_LEG_CORDS_CHEST_TO_VERTEX_AND_CHEST_TO_ACROMION_SCALE_CLOSURE",
        "BIACROMIAL_BREADTH_TO_BROAD_SHOULDER_JOINT_POINT_PROXY",
    ),
    provenance_path=(
        "config/body_calibration_v4_1/"
        "v47_subject_surface_anthropometry_20260828.json"
    ),
)


class SolveBudgetExceeded(RuntimeError):
    """Raised by a residual wrapper when a declared wall budget expires."""


def soft_l1_measurement_rows(residual: np.ndarray) -> np.ndarray:
    """Pseudo-residual whose quadratic cost is exactly soft-L1.

    ``0.5 * sum(output**2) == sum(sqrt(1 + residual**2) - 1)``.
    Priors are never passed through this function.
    """

    residual = np.asarray(residual, dtype=float)
    magnitude = np.sqrt(2.0 * (np.sqrt(1.0 + residual * residual) - 1.0))
    return np.copysign(magnitude, residual)


def _materialize_spec(base: PhysicalGraphSpec, latent: np.ndarray) -> PhysicalGraphSpec:
    latent = np.asarray(latent, dtype=float)
    if latent.shape != (LATENT_DIMENSION,):
        raise ValueError("progressive latent geometry has the wrong dimension")
    return replace(
        base,
        pelvis_width_m=float(latent[0]),
        pelvis_height_m=float(latent[1]),
        pelvis_hip_vertical_offset_m=-0.5 * float(latent[1]),
        pelvis_torso_vertical_offset_m=0.5 * float(latent[1]),
        torso_prior_mean_m=float(latent[2]),
        torso_min_m=0.18,
        torso_max_m=0.65,
        torso_shoulder_width_m=float(latent[3]),
    )


def progressive_bounds(base: PhysicalGraphSpec) -> tuple[np.ndarray, np.ndarray]:
    low, high = core_bounds(_materialize_spec(base, LATENT_SEED))
    return np.r_[low, LATENT_LOWER], np.r_[high, LATENT_UPPER]


def progressive_state_layout() -> list[dict[str, Any]]:
    from .physical_graph import STATE_LAYOUT

    output = [dict(row) for row in STATE_LAYOUT]
    for index, name in enumerate(LATENT_NAMES, start=CORE_STATE_DIMENSION):
        output.append({
            "name": name,
            "start": index,
            "stop": index + 1,
            "prior": None,
            "publishability": "DATA_ONLY_OBSERVABILITY_REQUIRED",
        })
    return output


STATE_LAYOUT = progressive_state_layout()
COORDINATE_NAMES = tuple(
    name
    for row in STATE_LAYOUT
    for name in (
        [f"{row['name']}[{index - row['start']}]" for index in range(row["start"], row["stop"])]
        if row["stop"] - row["start"] > 1 else [row["name"]]
    )
)


def parameter_groups() -> dict[str, tuple[int, ...]]:
    groups: dict[str, tuple[int, ...]] = {
        "relative_headings": tuple(range(0, 9)),
        "pelvis_root_extrinsic": tuple(range(9, 15)),
        "torso_extrinsic": tuple(range(15, 21)),
        "latent_pelvis_torso_geometry": tuple(range(CORE_STATE_DIMENSION, STATE_DIMENSION)),
    }
    for row in STATE_LAYOUT:
        if not row["name"].startswith("physical_segment:"):
            continue
        segment = row["name"].split(":", 1)[1]
        groups[f"segment_geometry:{segment}"] = tuple(range(row["start"], row["stop"]))
    groups["tape_observed_lengths"] = tuple(LENGTH_INDICES.values())
    return groups


PARAMETER_GROUPS = parameter_groups()


class ProgressiveObjective:
    """One cumulative capture objective with separated loss classes."""

    def __init__(
        self,
        factors: Mapping[str, EdgeFactors],
        base_spec: PhysicalGraphSpec,
        geometry_prior: ProgressiveGeometryPrior | None = None,
    ):
        self.factors = {name: factors[name] for name, *_ in EDGES}
        self.base_spec = base_spec
        self.geometry_prior = geometry_prior
        if geometry_prior is not None:
            geometry_prior.validate()
        self.has_training_measurements = any(
            factor.b5_train for factor in self.factors.values()
        )
        self.core = PhysicalGraphObjective(self.factors, _materialize_spec(base_spec, LATENT_SEED))

    def _split(self, x: np.ndarray) -> tuple[np.ndarray, PhysicalGraphSpec]:
        x = np.asarray(x, dtype=float)
        if x.shape != (STATE_DIMENSION,):
            raise ValueError(f"expected {STATE_DIMENSION} progressive coordinates")
        return x[:CORE_STATE_DIMENSION], _materialize_spec(self.base_spec, x[LATENT_SLICE])

    def measurement_residual(self, x: np.ndarray) -> np.ndarray:
        if not self.has_training_measurements:
            return np.zeros(1, dtype=float)
        core, spec = self._split(x)
        self.core.spec = spec
        return self.core.measurement_residual(core)

    def topology_residual(self, x: np.ndarray) -> np.ndarray:
        """Reject mirrored/crossed leg branches on verified rest only.

        This is a body-topology constraint, not a labelled pose template: it
        does not prescribe a joint angle, a T-pose, a ground plane, or metric
        global position.  It only states that, in signal-verified rest, the
        left knee and left shank sensor remain laterally left of their right
        counterparts in the fitted pelvis frame.  For the explicitly recorded
        initial/final natural-standing references only, pelvis and torso
        anatomical up must also agree with gravity and their lateral axes must
        agree with one another.  This closes the loophole where pelvis lateral
        became nearly vertical and both thigh lines crossed despite ordered
        knees.  Action rows never enter.
        """

        core, spec = self._split(x)
        return lower_limb_topology_residual(self.factors, core, spec)

    def robust_measurement_residual(self, x: np.ndarray) -> np.ndarray:
        return soft_l1_measurement_rows(self.measurement_residual(x))

    def prior_residual(self, x: np.ndarray) -> np.ndarray:
        core, spec = self._split(x)
        self.core.spec = spec
        core_prior = self.core.prior_residual(core)
        if self.geometry_prior is None:
            return core_prior
        return np.r_[core_prior, self.geometry_prior.residual(x[LATENT_SLICE])]

    def residual(self, x: np.ndarray) -> np.ndarray:
        return np.r_[
            self.robust_measurement_residual(x),
            soft_l1_measurement_rows(self.topology_residual(x)),
            self.prior_residual(x),
        ]

    def costs(self, x: np.ndarray) -> dict[str, float]:
        measurement = self.measurement_residual(x)
        topology = self.topology_residual(x)
        prior = self.prior_residual(x)
        return {
            "robust_sensor_cost": float(np.sum(np.sqrt(1.0 + measurement * measurement) - 1.0)),
            "robust_verified_rest_topology_cost": float(np.sum(
                np.sqrt(1.0 + topology * topology) - 1.0
            )),
            "gaussian_prior_half_squared_cost": float(0.5 * prior @ prior),
            "total_cost": float(
                np.sum(np.sqrt(1.0 + measurement * measurement) - 1.0)
                + np.sum(np.sqrt(1.0 + topology * topology) - 1.0)
                + 0.5 * prior @ prior
            ),
        }


def _mean_rotation(matrices: np.ndarray) -> np.ndarray:
    matrices = np.asarray(matrices, dtype=float)
    if not len(matrices):
        raise ValueError("cannot average an empty verified-rest rotation set")
    return Rotation.from_matrix(matrices).mean().as_matrix()


def _rest_rotation_references(
    factors: Mapping[str, EdgeFactors],
) -> list[dict[str, Any]]:
    """Build action/phase rest references without assuming row alignment.

    Informative sparsification is edge-local, so corresponding blocks may
    retain different row indices.  A proper SO(3) mean per segment and phase
    avoids fabricating synchronization while preserving the rest topology.
    """

    edge_blocks = {
        edge: {(block.action, block.partition): block for block in factor.b5_train}
        for edge, factor in factors.items()
    }
    root = edge_blocks["pelvis_torso"]
    child_edge = {child: edge for edge, _, child, _ in EDGES}
    output: list[dict[str, Any]] = []
    for key, root_block in root.items():
        if root_block.sample_time_ns is None:
            continue
        if not all(key in edge_blocks[edge] for edge, *_ in EDGES):
            continue
        if any(
            edge_blocks[edge][key].sample_time_ns is None
            for edge, *_ in EDGES
        ):
            continue
        for phase in VERIFIED_REST_PHASES:
            rotations: dict[str, np.ndarray] = {}
            root_mask = root_block.phase == phase
            if not np.any(root_mask):
                continue
            rotations["pelvis"] = _mean_rotation(root_block.parent_rotation[root_mask])
            complete = True
            for segment in SEGMENTS[1:]:
                block = edge_blocks[child_edge[segment]][key]
                mask = block.phase == phase
                if not np.any(mask):
                    complete = False
                    break
                rotations[segment] = _mean_rotation(block.child_rotation[mask])
            if complete:
                output.append({
                    "action": key[0],
                    "partition": key[1],
                    "phase": phase,
                    "rotation_world_sensor": rotations,
                })
    return output


def _rest_lower_limb_lateral_coordinates(
    reference: Mapping[str, Any], core: np.ndarray, spec: PhysicalGraphSpec,
) -> dict[str, float]:
    decoded = decode_state(core, spec)
    rotations = {
        segment: rz(decoded["headings"][segment])
        @ np.asarray(reference["rotation_world_sensor"][segment], dtype=float)
        for segment in SEGMENTS
    }
    origins = {"pelvis": np.zeros(3, dtype=float)}
    joints: dict[str, np.ndarray] = {}
    for edge, parent, child, _ in EDGES:
        parent_lever, child_lever = decoded["edge_levers"][edge]
        joint = origins[parent] + rotations[parent] @ parent_lever
        joints[edge] = joint
        origins[child] = joint - rotations[child] @ child_lever
    pelvis_geometry = decoded["segment_geometry"]["pelvis"]
    torso_geometry = decoded["segment_geometry"]["torso"]
    pelvis_center = rotations["pelvis"] @ pelvis_geometry["center"]
    pelvis_frame = rotations["pelvis"] @ pelvis_geometry["frame"]
    torso_frame = rotations["torso"] @ torso_geometry["frame"]

    def lateral(point: np.ndarray) -> float:
        return float((pelvis_frame.T @ (point - pelvis_center))[0])

    torso_lateral_axis = torso_frame[:, 0]

    def torso_lateral(point: np.ndarray) -> float:
        return float(torso_lateral_axis @ point)

    ankle_left = (
        origins["shank_left"]
        + rotations["shank_left"]
        @ decoded["segment_geometry"]["shank_left"]["distal_proxy"]
    )
    ankle_right = (
        origins["shank_right"]
        + rotations["shank_right"]
        @ decoded["segment_geometry"]["shank_right"]["distal_proxy"]
    )

    return {
        "knee_left_m": lateral(joints["knee_left"]),
        "knee_right_m": lateral(joints["knee_right"]),
        "shank_sensor_left_m": lateral(origins["shank_left"]),
        "shank_sensor_right_m": lateral(origins["shank_right"]),
        "pelvis_up_world_z_dot": float(pelvis_frame[2, 2]),
        "torso_up_world_z_dot": float(torso_frame[2, 2]),
        "pelvis_torso_lateral_dot": float(pelvis_frame[:, 0] @ torso_frame[:, 0]),
        "hip_left_torso_lateral_m": torso_lateral(joints["hip_left"]),
        "hip_right_torso_lateral_m": torso_lateral(joints["hip_right"]),
        "knee_left_torso_lateral_m": torso_lateral(joints["knee_left"]),
        "knee_right_torso_lateral_m": torso_lateral(joints["knee_right"]),
        "shank_sensor_left_torso_lateral_m": torso_lateral(origins["shank_left"]),
        "shank_sensor_right_torso_lateral_m": torso_lateral(origins["shank_right"]),
        "ankle_left_torso_lateral_m": torso_lateral(ankle_left),
        "ankle_right_torso_lateral_m": torso_lateral(ankle_right),
        "hip_left_world_z_m": float(joints["hip_left"][2]),
        "hip_right_world_z_m": float(joints["hip_right"][2]),
        "knee_left_world_z_m": float(joints["knee_left"][2]),
        "knee_right_world_z_m": float(joints["knee_right"][2]),
        "ankle_left_world_z_m": float(ankle_left[2]),
        "ankle_right_world_z_m": float(ankle_right[2]),
    }


def lower_limb_topology_residual(
    factors: Mapping[str, EdgeFactors], core: np.ndarray, spec: PhysicalGraphSpec,
) -> np.ndarray:
    references = _rest_rotation_references(factors)
    residual = []
    for reference in references:
        row = _rest_lower_limb_lateral_coordinates(reference, core, spec)
        for left, right in (
            (row["knee_left_m"], row["knee_right_m"]),
            (row["shank_sensor_left_m"], row["shank_sensor_right_m"]),
        ):
            violation = LOWER_LIMB_MINIMUM_LATERAL_ORDER_M - (right - left)
            residual.append(max(0.0, violation) / LOWER_LIMB_TOPOLOGY_SIGMA_M)
        if reference["action"] in STANDING_REFERENCE_ACTIONS:
            for name in (
                "pelvis_up_world_z_dot",
                "torso_up_world_z_dot",
                "pelvis_torso_lateral_dot",
            ):
                violation = STANDING_FRAME_MINIMUM_DOT - row[name]
                residual.append(max(0.0, violation) / STANDING_FRAME_DOT_SIGMA)
            for left_name, right_name in (
                ("hip_left_torso_lateral_m", "hip_right_torso_lateral_m"),
                ("knee_left_torso_lateral_m", "knee_right_torso_lateral_m"),
                (
                    "shank_sensor_left_torso_lateral_m",
                    "shank_sensor_right_torso_lateral_m",
                ),
                ("ankle_left_torso_lateral_m", "ankle_right_torso_lateral_m"),
            ):
                violation = LOWER_LIMB_MINIMUM_LATERAL_ORDER_M - (
                    row[right_name] - row[left_name]
                )
                residual.append(max(0.0, violation) / LOWER_LIMB_TOPOLOGY_SIGMA_M)
            for side in ("left", "right"):
                for proximal, distal in (("hip", "knee"), ("knee", "ankle")):
                    vertical_drop = (
                        row[f"{proximal}_{side}_world_z_m"]
                        - row[f"{distal}_{side}_world_z_m"]
                    )
                    violation = STANDING_CHAIN_MINIMUM_VERTICAL_DROP_M - vertical_drop
                    residual.append(max(0.0, violation) / STANDING_CHAIN_VERTICAL_SIGMA_M)
    return np.asarray(residual or [0.0], dtype=float)


def lower_limb_topology_report(
    factors: Mapping[str, EdgeFactors], core: np.ndarray, spec: PhysicalGraphSpec,
) -> dict[str, Any]:
    rows = []
    for reference in _rest_rotation_references(factors):
        row = _rest_lower_limb_lateral_coordinates(reference, core, spec)
        knee_separation = row["knee_right_m"] - row["knee_left_m"]
        shank_separation = (
            row["shank_sensor_right_m"] - row["shank_sensor_left_m"]
        )
        standing_frame_applicable = reference["action"] in STANDING_REFERENCE_ACTIONS
        standing_chain_separations = {
            name: row[f"{name}_right_torso_lateral_m"]
            - row[f"{name}_left_torso_lateral_m"]
            for name in ("hip", "knee", "shank_sensor", "ankle")
        }
        standing_chain_pass = bool(
            not standing_frame_applicable
            or all(
                value >= LOWER_LIMB_MINIMUM_LATERAL_ORDER_M
                for value in standing_chain_separations.values()
            )
        )
        standing_vertical_drops = {
            f"{side}_{proximal}_to_{distal}": (
                row[f"{proximal}_{side}_world_z_m"]
                - row[f"{distal}_{side}_world_z_m"]
            )
            for side in ("left", "right")
            for proximal, distal in (("hip", "knee"), ("knee", "ankle"))
        }
        standing_vertical_pass = bool(
            not standing_frame_applicable
            or all(
                value >= STANDING_CHAIN_MINIMUM_VERTICAL_DROP_M
                for value in standing_vertical_drops.values()
            )
        )
        standing_frame_pass = bool(
            not standing_frame_applicable
            or (
                row["pelvis_up_world_z_dot"] >= STANDING_FRAME_MINIMUM_DOT
                and row["torso_up_world_z_dot"] >= STANDING_FRAME_MINIMUM_DOT
                and row["pelvis_torso_lateral_dot"] >= STANDING_FRAME_MINIMUM_DOT
            )
        )
        rows.append({
            "action": reference["action"],
            "phase": reference["phase"],
            **row,
            "knee_right_minus_left_m": knee_separation,
            "shank_sensor_right_minus_left_m": shank_separation,
            "standing_frame_applicable": standing_frame_applicable,
            "standing_chain_right_minus_left_m": standing_chain_separations,
            "standing_chain_pass": standing_chain_pass,
            "standing_vertical_drop_m": standing_vertical_drops,
            "standing_vertical_pass": standing_vertical_pass,
            "standing_frame_pass": standing_frame_pass,
            "pass": bool(
                knee_separation >= LOWER_LIMB_MINIMUM_LATERAL_ORDER_M
                and shank_separation >= LOWER_LIMB_MINIMUM_LATERAL_ORDER_M
                and standing_frame_pass
                and standing_chain_pass
                and standing_vertical_pass
            ),
        })
    return {
        "schema": "biospur-pure-imu-v0-verified-rest-lower-limb-topology-v2",
        "minimum_lateral_order_m": LOWER_LIMB_MINIMUM_LATERAL_ORDER_M,
        "standing_reference_actions": list(STANDING_REFERENCE_ACTIONS),
        "standing_frame_minimum_dot": STANDING_FRAME_MINIMUM_DOT,
        "standing_chain_minimum_vertical_drop_m": (
            STANDING_CHAIN_MINIMUM_VERTICAL_DROP_M
        ),
        "rows": rows,
        "reference_count": len(rows),
        "crossed_reference_count": sum(not row["pass"] for row in rows),
        "applicable": bool(rows),
        "pass": bool(not rows or all(row["pass"] for row in rows)),
        "signal_verified_rest_only": True,
        "action_pose_template_used": False,
        "ground_plane_used": False,
        "gravity_direction_used_for_natural_standing_tilt_only": True,
        "pelvis_global_translation_observed": False,
        "viewer_coordinates_used": False,
        "left_right_node_swap_used": False,
    }


def b5_only_factors(episodes: Sequence[Raw6Episode]) -> dict[str, EdgeFactors]:
    """Create integration-free Seel-style factors without qmt-derived priors."""

    train = [episode for episode in episodes if episode.partition == "IDENTIFICATION_TRAIN"]
    held = [episode for episode in episodes if episode.partition == "HELD_OUT_VALIDATION"]
    return {
        edge: EdgeFactors(
            edge, parent, child, kind,
            b5_blocks(edge, train, include_transitions=True),
            b5_blocks(edge, held, include_transitions=True),
            None, None, None, None,
        )
        for edge, parent, child, kind in EDGES
    }


def select_factor_actions(
    factors: Mapping[str, EdgeFactors], actions: Iterable[str],
) -> dict[str, EdgeFactors]:
    selected = set(actions)
    return {
        edge: EdgeFactors(
            factor.name, factor.parent, factor.child, factor.kind,
            tuple(block for block in factor.b5_train if block.action in selected),
            tuple(block for block in factor.b5_held_out if block.action in selected),
            None, None, None, None,
        )
        for edge, factor in factors.items()
    }


def factor_partition_identity(
    factors: Mapping[str, EdgeFactors], partition: str,
) -> dict[str, Any]:
    """Hash the exact ordered B5 factor rows in one train/validation partition."""

    if partition not in {"IDENTIFICATION_TRAIN", "HELD_OUT_VALIDATION"}:
        raise ValueError(f"unknown factor partition: {partition}")
    attribute_names = (
        "phase", "parent_rotation", "child_rotation", "parent_force",
        "child_force", "parent_kinematic", "child_kinematic", "sample_weight",
        "sample_time_ns",
    )
    overall = hashlib.sha256()
    overall.update(b"biospur-progressive-factor-identity-v1\0")
    overall.update(partition.encode() + b"\0")
    edge_hashes = {}
    ordered_actions = []
    block_count = 0
    row_count = 0
    for edge, *_ in EDGES:
        factor = factors[edge]
        blocks = (
            factor.b5_train if partition == "IDENTIFICATION_TRAIN"
            else factor.b5_held_out
        )
        edge_digest = hashlib.sha256()
        edge_digest.update(edge.encode() + b"\0")
        for block in blocks:
            block_count += 1
            row_count += len(block.phase)
            ordered_actions.append(block.action)
            for token in (block.action, block.partition):
                encoded = token.encode()
                edge_digest.update(len(encoded).to_bytes(8, "little"))
                edge_digest.update(encoded)
            for name in attribute_names:
                value = getattr(block, name)
                if value is None:
                    descriptor = f"{name}|NONE".encode()
                    edge_digest.update(len(descriptor).to_bytes(8, "little"))
                    edge_digest.update(descriptor)
                    continue
                array = np.ascontiguousarray(value)
                descriptor = f"{name}|{array.dtype.str}|{array.shape}".encode()
                edge_digest.update(len(descriptor).to_bytes(8, "little"))
                edge_digest.update(descriptor)
                edge_digest.update(array.tobytes(order="C"))
        edge_hashes[edge] = edge_digest.hexdigest()
        overall.update(edge.encode() + b"\0" + edge_digest.digest())
    return {
        "schema": "biospur-progressive-factor-identity-v1",
        "partition": partition,
        "sha256": overall.hexdigest(),
        "edge_sha256": edge_hashes,
        "block_count": block_count,
        "row_count": row_count,
        "ordered_action_by_edge_blocks": ordered_actions,
    }


def _block_slice(block: B5Block, keep: np.ndarray) -> B5Block:
    # Preserve the block's total squared influence after deterministic row
    # selection.  Without this normalization, sparsification silently makes
    # Gaussian priors stronger relative to sensor evidence.
    information_scale = math.sqrt(len(block.phase) / max(1, len(keep)))
    return B5Block(
        block.action, block.partition, block.phase[keep],
        block.parent_rotation[keep], block.child_rotation[keep],
        block.parent_force[keep], block.child_force[keep],
        block.parent_kinematic[keep], block.child_kinematic[keep],
        information_scale * block.sample_weight[keep],
        None if block.sample_time_ns is None else block.sample_time_ns[keep],
    )


def informative_sparsify(
    factors: Mapping[str, EdgeFactors], *, retain_fraction: float = 0.60,
) -> tuple[dict[str, EdgeFactors], dict[str, Any]]:
    """Select high-excitation rows while retaining every episode and phase."""

    if not 0.25 <= retain_fraction <= 1.0:
        raise ValueError("invalid informative retention fraction")
    audit: dict[str, Any] = {"edges": {}, "episode_connectivity_retained": True}
    output = {}
    for edge, factor in factors.items():
        edge_audit = []

        def select(blocks: Sequence[B5Block]) -> tuple[B5Block, ...]:
            selected_blocks = []
            for block in blocks:
                n = len(block.phase)
                target = max(5, int(math.ceil(retain_fraction * n)))
                score = (
                    np.linalg.norm(block.parent_kinematic, axis=(1, 2))
                    + np.linalg.norm(block.child_kinematic, axis=(1, 2))
                    + 0.25 * block.sample_weight
                )
                mandatory = {0, n - 1}
                for phase in np.unique(block.phase):
                    rows = np.flatnonzero(block.phase == phase)
                    if len(rows):
                        mandatory.add(int(rows[0])); mandatory.add(int(rows[-1]))
                ranked = list(np.argsort(score)[::-1])
                keep_set = set(mandatory)
                for index in ranked:
                    if len(keep_set) >= target:
                        break
                    keep_set.add(int(index))
                keep = np.asarray(sorted(keep_set), dtype=int)
                selected_blocks.append(_block_slice(block, keep))
                edge_audit.append({
                    "action": block.action,
                    "partition": block.partition,
                    "full_rows": n,
                    "retained_rows": int(len(keep)),
                    "phases_full": sorted(np.unique(block.phase).tolist()),
                    "phases_retained": sorted(np.unique(block.phase[keep]).tolist()),
                    "connectivity_retained": set(np.unique(block.phase)) == set(np.unique(block.phase[keep])),
                })
            return tuple(selected_blocks)

        output[edge] = EdgeFactors(
            factor.name, factor.parent, factor.child, factor.kind,
            select(factor.b5_train), select(factor.b5_held_out),
            None, None, None, None,
        )
        audit["edges"][edge] = edge_audit
    audit["episode_connectivity_retained"] = all(
        row["connectivity_retained"]
        for rows in audit["edges"].values() for row in rows
    )
    return output, audit


def initialize_progressive_state(
    factors: Mapping[str, EdgeFactors], base_spec: PhysicalGraphSpec,
    headings: np.ndarray | None = None,
) -> np.ndarray:
    if headings is None:
        edgewise = fit_edgewise(factors)
        headings = np.asarray([
            edgewise["accumulated_headings_rad"][segment] for segment in SEGMENTS[1:]
        ], dtype=float)
    core = initialize_state(
        factors, np.asarray(headings, dtype=float), _materialize_spec(base_spec, LATENT_SEED),
    )
    return np.r_[core, LATENT_SEED]


@dataclass
class _BudgetedResidual:
    function: Any
    wall_limit_s: float
    started: float
    evaluations: int = 0
    best_half_squared_cost: float = math.inf
    trace: list[dict[str, Any]] | None = None

    def __call__(self, x: np.ndarray) -> np.ndarray:
        elapsed = time.perf_counter() - self.started
        if elapsed > self.wall_limit_s:
            raise SolveBudgetExceeded(f"solve exceeded {self.wall_limit_s:.3f} s")
        value = self.function(x)
        self.evaluations += 1
        cost = float(0.5 * value @ value)
        improved = cost + 1e-12 < self.best_half_squared_cost
        if improved:
            self.best_half_squared_cost = cost
        if self.trace is not None and (improved or self.evaluations == 1 or self.evaluations % 25 == 0):
            self.trace.append({
                "residual_call": self.evaluations,
                "elapsed_s": elapsed,
                "half_squared_cost": cost,
                "new_best": improved,
            })
        return value


def _structural_sparsity(
    objective: ProgressiveObjective, x: np.ndarray,
    low: np.ndarray, high: np.ndarray,
) -> csr_matrix:
    probe = np.clip(
        x + 0.008 * np.sin(np.arange(STATE_DIMENSION) + 0.41),
        low + 1e-8, high - 1e-8,
    )
    first = numerical_jacobian(objective.residual, x, low, high)
    second = numerical_jacobian(objective.residual, probe, low, high)
    return csr_matrix((np.abs(first) > 1e-12) | (np.abs(second) > 1e-12))


def _solve_once(
    objective: ProgressiveObjective,
    x0: np.ndarray,
    low: np.ndarray,
    high: np.ndarray,
    sparsity: csr_matrix,
    *,
    label: str,
    max_nfev: int,
    wall_limit_s: float,
) -> dict[str, Any]:
    trace: list[dict[str, Any]] = []
    started = time.perf_counter()
    wrapped = _BudgetedResidual(objective.residual, wall_limit_s, started, trace=trace)
    try:
        result = least_squares(
            wrapped, np.clip(x0, low + 1e-8, high - 1e-8),
            bounds=(low, high), jac="2-point", jac_sparsity=sparsity,
            tr_solver="lsmr",
            tr_options={"atol": 1e-9, "btol": 1e-9, "maxiter": 240},
            loss="linear", x_scale="jac", max_nfev=max_nfev,
            xtol=1e-8, ftol=1e-8, gtol=1e-8,
        )
        finite = bool(np.isfinite(result.x).all() and np.isfinite(result.fun).all())
        return {
            "label": label,
            "state": result.x,
            "costs": objective.costs(result.x),
            "half_squared_transformed_residual_cost": float(result.cost),
            "optimality": float(result.optimality),
            "nfev": int(result.nfev),
            "residual_calls": wrapped.evaluations,
            "elapsed_s": time.perf_counter() - started,
            "success": bool(result.success),
            "finite": finite,
            "termination": str(result.message),
            "budget_exceeded": False,
            "trace": trace,
        }
    except SolveBudgetExceeded as error:
        return {
            "label": label, "state": None, "costs": None,
            "half_squared_transformed_residual_cost": None,
            "optimality": None, "nfev": None,
            "residual_calls": wrapped.evaluations,
            "elapsed_s": time.perf_counter() - started,
            "success": False, "finite": False,
            "termination": str(error), "budget_exceeded": True,
            "trace": trace,
        }


def _identifiable_subspace_pivot(
    objective: ProgressiveObjective, x: np.ndarray,
    low: np.ndarray, high: np.ndarray,
) -> dict[str, Any]:
    residual = objective.residual(x)
    jacobian = numerical_jacobian(objective.residual, x, low, high)
    u, singular, vt = np.linalg.svd(jacobian, full_matrices=False)
    threshold = max(1e-8, float(singular[0]) * 1e-6) if len(singular) else 1e-8
    keep = singular > threshold
    if not np.any(keep):
        return {"triggered": True, "accepted": False, "reason": "ZERO_IDENTIFIABLE_RANK"}
    step = -(vt[keep].T @ ((u[:, keep].T @ residual) / singular[keep]))
    baseline = float(0.5 * residual @ residual)
    candidates = []
    for scale in (1.0, 0.5, 0.25, 0.125):
        candidate = np.clip(x + scale * step, low + 1e-8, high - 1e-8)
        candidate[:HEADING_DIMENSION] = wrap(candidate[:HEADING_DIMENSION])
        cost = float(0.5 * objective.residual(candidate) @ objective.residual(candidate))
        candidates.append((cost, scale, candidate))
    cost, scale, candidate = min(candidates, key=lambda row: row[0])
    accepted = cost + 1e-9 < baseline
    return {
        "triggered": True,
        "method": "TRUNCATED_SVD_GAUSS_NEWTON_IDENTIFIABLE_SUBSPACE_LINE_SEARCH",
        "rank": int(np.count_nonzero(keep)),
        "threshold": threshold,
        "baseline_cost": baseline,
        "candidate_cost": cost,
        "accepted": accepted,
        "scale": scale,
        "state": candidate if accepted else x,
        "thresholds_relaxed": False,
    }


def solve_cumulative(
    factors: Mapping[str, EdgeFactors],
    base_spec: PhysicalGraphSpec,
    *,
    previous_state: np.ndarray | None,
    seed: int,
    geometry_prior: ProgressiveGeometryPrior | None = None,
    starts: int = 3,
    max_nfev: int = 55,
    wall_limit_s: float = 25.0,
    optimization_retain_fraction: float | None = None,
) -> dict[str, Any]:
    """Solve one cumulative prefix and compare warm incremental to cold batch."""

    if starts < 3:
        raise ValueError("progressive solve requires warm, cold, and broad starts")
    full_objective = ProgressiveObjective(factors, base_spec, geometry_prior)
    sparsification = None
    solver_factors = factors
    if optimization_retain_fraction is not None:
        solver_factors, sparsification = informative_sparsify(
            factors, retain_fraction=optimization_retain_fraction,
        )
        if not sparsification["episode_connectivity_retained"]:
            raise RuntimeError("optimization sparsification broke episode connectivity")
    objective = ProgressiveObjective(solver_factors, base_spec, geometry_prior)
    low, high = progressive_bounds(base_spec)
    base = initialize_progressive_state(
        factors, base_spec,
        None if previous_state is None else previous_state[:HEADING_DIMENSION],
    )
    if previous_state is None and geometry_prior is not None:
        base[LATENT_SLICE] = np.asarray(geometry_prior.mean_m, dtype=float)
    self_spec = _materialize_spec(base_spec, base[LATENT_SLICE])
    objective.core.spec = self_spec
    cold_headings, cold_profile = _full_circle_heading_profile(
        objective.core, base[:CORE_STATE_DIMENSION], base[:HEADING_DIMENSION],
    )
    cold = base.copy()
    cold[:HEADING_DIMENSION] = cold_headings
    raw_starts: list[tuple[str, np.ndarray, Mapping[str, Any] | None]] = [
        ("INCREMENTAL_WARM_START", previous_state.copy() if previous_state is not None else base.copy(), None),
        ("CUMULATIVE_COLD_FULL_CIRCLE_REFERENCE", cold, cold_profile),
    ]
    rng = np.random.default_rng(seed)
    for index in range(starts - 2):
        candidate = base.copy()
        candidate[:HEADING_DIMENSION] = rng.uniform(-math.pi, math.pi, HEADING_DIMENSION)
        objective.core.spec = _materialize_spec(base_spec, candidate[LATENT_SLICE])
        profiled_headings, profile = _full_circle_heading_profile(
            objective.core, candidate[:CORE_STATE_DIMENSION], candidate[:HEADING_DIMENSION],
        )
        candidate[:HEADING_DIMENSION] = profiled_headings
        candidate[HEADING_DIMENSION:CORE_STATE_DIMENSION] += rng.normal(
            0.0, 0.018, CORE_STATE_DIMENSION - HEADING_DIMENSION,
        )
        candidate[LATENT_SLICE] = rng.uniform(LATENT_LOWER, LATENT_UPPER)
        raw_starts.append((f"BROAD_FULL_CIRCLE_{index + 1}", candidate, profile))
    sparsity = _structural_sparsity(objective, base, low, high)
    fits = []
    for label, x0, profile in raw_starts:
        fit = _solve_once(
            objective, x0, low, high, sparsity,
            label=label, max_nfev=max_nfev, wall_limit_s=wall_limit_s,
        )
        fit["full_circle_profile"] = profile
        fits.append(fit)
    finite = [row for row in fits if row["finite"]]
    initializer_all_failed = not finite
    if initializer_all_failed and previous_state is None:
        raise RuntimeError("all bounded initial progressive starts failed")
    if initializer_all_failed:
        best = {
            "label": "PREVIOUS_PREFIX_STATE_AFTER_ALL_SPARSE_INITIALIZERS_FAILED",
            "state": np.asarray(previous_state, dtype=float),
            "costs": objective.costs(previous_state),
            "nfev": None,
            "optimality": None,
            "success": False,
        }
    else:
        best = min(finite, key=lambda row: row["costs"]["total_cost"])
    sparse_state = np.asarray(best["state"], dtype=float)
    state = sparse_state.copy()
    warm = fits[0]
    reference_candidates = [row for row in fits[1:] if row["finite"]]
    reference = min(
        reference_candidates or finite or [best],
        key=lambda row: row["costs"]["total_cost"],
    )
    stagnated = bool(
        (best["nfev"] is not None and best["nfev"] >= max_nfev)
        or (best["optimality"] is not None and best["optimality"] > 1e-3 and not best["success"])
    )
    pivot = {"triggered": False, "reason": "PRIMARY_SOLVE_NOT_STAGNATED"}
    if stagnated:
        pivot = _identifiable_subspace_pivot(objective, state, low, high)
        if pivot.get("accepted"):
            state = np.asarray(pivot["state"], dtype=float)
    full_reference_fit = None
    full_reference_pivot = None
    if optimization_retain_fraction is not None:
        full_sparsity = _structural_sparsity(full_objective, state, low, high)
        full_reference_fit = _solve_once(
            full_objective, state, low, high, full_sparsity,
            label="FULL_CUMULATIVE_BATCH_REFERENCE",
            max_nfev=min(18, max_nfev), wall_limit_s=wall_limit_s,
        )
        if full_reference_fit["finite"]:
            state = np.asarray(full_reference_fit["state"], dtype=float)
        else:
            full_reference_pivot = _identifiable_subspace_pivot(
                full_objective, state, low, high,
            )
            if full_reference_pivot.get("accepted"):
                state = np.asarray(full_reference_pivot["state"], dtype=float)
    headings = np.asarray([
        row["state"][:HEADING_DIMENSION] for row in finite
    ]) if finite else sparse_state[None, :HEADING_DIMENSION]
    spread = {
        segment: circular_spread_deg(headings[:, index])
        for index, segment in enumerate(SEGMENTS[1:])
    }
    warm_state = np.asarray(warm["state"], dtype=float) if warm["finite"] else None
    reference_state = np.asarray(reference["state"], dtype=float)
    sparse_comparison = {
        "incremental_start_finite": warm_state is not None,
        "reference_start": reference["label"],
        "heading_max_circular_difference_deg": (
            float(np.max(np.degrees(np.abs(wrap(
                warm_state[:HEADING_DIMENSION] - reference_state[:HEADING_DIMENSION]
            ))))) if warm_state is not None else None
        ),
        "normalized_full_state_difference": (
            float(np.linalg.norm(warm_state - reference_state) / math.sqrt(STATE_DIMENSION))
            if warm_state is not None else None
        ),
        "absolute_total_cost_difference": (
            abs(warm["costs"]["total_cost"] - reference["costs"]["total_cost"])
            if warm_state is not None else None
        ),
        "same_cumulative_factor_set": True,
    }
    full_comparison_source = warm_state if warm_state is not None else sparse_state
    full_incremental_completed = bool(
        warm_state is not None
        or (
            initializer_all_failed
            and full_reference_fit is not None
            and full_reference_fit["finite"]
        )
    )
    full_comparison = {
        "incremental_start_finite": full_incremental_completed,
        "reference_start": (
            "FULL_CUMULATIVE_BATCH_REFERENCE"
            if full_reference_fit is not None and full_reference_fit["finite"]
            else "FULL_CUMULATIVE_TRUNCATED_SVD_REFERENCE"
            if full_reference_pivot is not None else reference["label"]
        ),
        "heading_max_circular_difference_deg": float(np.max(np.degrees(np.abs(wrap(
            full_comparison_source[:HEADING_DIMENSION] - state[:HEADING_DIMENSION]
        ))))),
        "normalized_full_state_difference": float(
            np.linalg.norm(full_comparison_source - state) / math.sqrt(STATE_DIMENSION)
        ),
        "absolute_total_cost_difference": abs(
            full_objective.costs(full_comparison_source)["total_cost"]
            - full_objective.costs(state)["total_cost"]
        ),
        "same_cumulative_full_factor_set": True,
        "independent_sparse_batch_alternative_available": bool(reference_candidates),
        "sparse_result_only_used_as_numerical_initialization": (
            optimization_retain_fraction is not None
        ),
    }
    return {
        "state": state,
        "costs": full_objective.costs(state),
        "selected_start": (
            full_comparison["reference_start"]
            if optimization_retain_fraction is not None else best["label"]
        ),
        "starts": fits,
        "all_sparse_initializers_failed": initializer_all_failed,
        "continued_on_full_cumulative_objective_after_sparse_failure": bool(
            initializer_all_failed
            and full_reference_fit is not None
            and full_reference_fit["finite"]
        ),
        "finite_start_count": len(finite),
        "finite_cold_or_broad_start_count": sum(
            row["finite"] and row["label"] != "INCREMENTAL_WARM_START"
            for row in fits
        ),
        "multistart_heading_spread_deg": spread,
        "multistart_max_heading_spread_deg": float(max(spread.values())),
        "incremental_vs_cumulative_reference": full_comparison,
        "sparse_incremental_vs_sparse_batch": sparse_comparison,
        "full_cumulative_reference_solve": (
            {key: value for key, value in full_reference_fit.items() if key != "state"}
            if full_reference_fit is not None else None
        ),
        "full_cumulative_reference_pivot": (
            {key: value for key, value in full_reference_pivot.items() if key != "state"}
            if full_reference_pivot is not None else None
        ),
        "optimization_sparsification": {
            "enabled": optimization_retain_fraction is not None,
            "retain_fraction": optimization_retain_fraction,
            "audit": sparsification,
            "full_factor_batch_reference_executed": optimization_retain_fraction is not None,
        },
        "algorithm_pivot": {key: value for key, value in pivot.items() if key != "state"},
        "limits": {
            "start_count": starts,
            "maximum_function_evaluations_per_start": max_nfev,
            "wall_limit_seconds_per_start": wall_limit_s,
            "thresholds_relaxed": False,
        },
        "progressive_geometry_prior": (
            None if geometry_prior is None else geometry_prior.audit()
        ),
        "sparsity_shape": list(sparsity.shape),
        "sparsity_nonzeros": int(sparsity.nnz),
    }


def _information_block(jacobian: np.ndarray, indices: Sequence[int]) -> dict[str, Any]:
    block = jacobian[:, np.asarray(indices, dtype=int)]
    singular = np.linalg.svd(block, compute_uv=False)
    threshold = max(1e-8, float(singular[0]) * 1e-6) if len(singular) else 1e-8
    information = block.T @ block
    covariance = np.linalg.pinv(information, rcond=1e-10)
    sigma = np.sqrt(np.maximum(0.0, np.diag(covariance)))
    return {
        "dimension": len(indices),
        "rank": int(np.count_nonzero(singular > threshold)),
        "trace_information": float(np.trace(information)),
        "log1p_information": float(np.sum(np.log1p(np.maximum(0.0, singular * singular)))),
        "singular_values": singular.tolist(),
        "threshold": threshold,
        "data_only_coordinate_sigma_residual_scale": sigma.tolist(),
    }


def information_snapshot(
    cumulative_factors: Mapping[str, EdgeFactors],
    episode_factors: Mapping[str, EdgeFactors],
    base_spec: PhysicalGraphSpec,
    state: np.ndarray,
    geometry_prior: ProgressiveGeometryPrior | None = None,
) -> dict[str, Any]:
    low, high = progressive_bounds(base_spec)
    cumulative = ProgressiveObjective(cumulative_factors, base_spec, geometry_prior)
    episode = ProgressiveObjective(episode_factors, base_spec, geometry_prior)
    measurement_jacobian = numerical_jacobian(
        cumulative.robust_measurement_residual, state, low, high,
    )
    prior_jacobian = numerical_jacobian(cumulative.prior_residual, state, low, high)
    episode_jacobian = numerical_jacobian(
        episode.robust_measurement_residual, state, low, high,
    )
    singular = np.linalg.svd(measurement_jacobian, compute_uv=False)
    threshold = max(1e-8, float(singular[0]) * 1e-6) if len(singular) else 1e-8
    u, _, vt = np.linalg.svd(measurement_jacobian, full_matrices=False)
    del u
    hessian = measurement_jacobian.T @ measurement_jacobian + prior_jacobian.T @ prior_jacobian
    covariance = np.linalg.pinv(hessian, rcond=1e-10)
    posterior_sigma = np.sqrt(np.maximum(0.0, np.diag(covariance)))
    null_rows = []
    for vector in vt[-min(3, len(vt)):]:
        dominant = np.argsort(np.abs(vector))[-5:][::-1]
        null_rows.append([
            {"coordinate": COORDINATE_NAMES[index], "coefficient": float(vector[index])}
            for index in dominant
        ])
    groups = {}
    for name, indices in PARAMETER_GROUPS.items():
        cumulative_row = _information_block(measurement_jacobian, indices)
        episode_row = _information_block(episode_jacobian, indices)
        groups[name] = {
            "cumulative": cumulative_row,
            "episode_contribution": episode_row,
            "marginal_log1p_information_gain": episode_row["log1p_information"],
            "posterior_one_sigma": posterior_sigma[np.asarray(indices)].tolist(),
            "posterior_approximate_95pct_half_width": (
                1.96 * posterior_sigma[np.asarray(indices)]
            ).tolist(),
        }
    return {
        "measurement_jacobian_shape": list(measurement_jacobian.shape),
        "prior_jacobian_shape": list(prior_jacobian.shape),
        "gauge_reduced_effective_rank": int(np.count_nonzero(singular > threshold)),
        "gauge_reduced_nullity": int(STATE_DIMENSION - np.count_nonzero(singular > threshold)),
        "singular_values": singular.tolist(),
        "rank_threshold": threshold,
        "null_directions_dominant_coordinates": null_rows,
        "posterior_covariance_diagonal": np.diag(covariance).tolist(),
        "posterior_one_sigma": posterior_sigma.tolist(),
        "parameter_groups": groups,
        "gaussian_priors_in_measurement_information": False,
    }


def held_out_report(
    factors: Mapping[str, EdgeFactors], state: np.ndarray, base_spec: PhysicalGraphSpec,
) -> dict[str, Any]:
    spec = _materialize_spec(base_spec, state[LATENT_SLICE])
    decoded = decode_state(state[:CORE_STATE_DIMENSION], spec)
    topology = lower_limb_topology_report(
        factors, state[:CORE_STATE_DIMENSION], spec,
    )
    rows = {}
    all_pass = True
    named_conflicts = []
    conflict_edges = []
    for edge, *_ in EDGES:
        factor = factors[edge]
        parent, child = decoded["edge_levers"][edge]
        headings = np.asarray([decoded["headings"][segment] for segment in SEGMENTS[1:]])
        from .raw6_heading import headings_to_edges
        delta = headings_to_edges(headings)[edge]
        train = evaluate_physical_b5(factor, delta, parent, child, held_out=False)
        held = evaluate_physical_b5(factor, delta, parent, child, held_out=True)
        train_rms = train["physical_rms_mps2"]
        held_rms = held["physical_rms_mps2"]
        aggregate_passed = bool(
            held_rms is None
            or (held_rms <= 1.5 and held_rms <= 1.75 * max(train_rms or 0.0, 1e-12))
        )
        per_action_conflicts = []
        for action, action_row in held.get("per_action", {}).items():
            value = action_row["physical_rms_mps2"]
            if value > 1.5 or value > 1.75 * max(train_rms or 0.0, 1e-12):
                token = f"{edge}:{action}"
                per_action_conflicts.append(token)
                named_conflicts.append(token)
        passed = bool(aggregate_passed and not per_action_conflicts)
        if not passed:
            conflict_edges.append(edge)
        all_pass &= passed
        rows[edge] = {
            "train": train,
            "held_out": held,
            "held_out_to_train_ratio": (
                held_rms / max(train_rms, 1e-12)
                if held_rms is not None and train_rms is not None else None
            ),
            "aggregate_pass": aggregate_passed,
            "per_action_conflicts": per_action_conflicts,
            "pass": passed,
        }
    if not topology["pass"]:
        named_conflicts.append("verified_rest_lower_limb_topology")
    return {
        "edges": rows,
        "named_conflicts": sorted(named_conflicts),
        "conflict_edges": sorted(set(conflict_edges)),
        "verified_rest_lower_limb_topology": topology,
        "pass": bool(all_pass and topology["pass"]),
        "later_held_out_blocks_cannot_dilute_named_action_conflicts": True,
    }


def physical_state_report(
    state: np.ndarray, base_spec: PhysicalGraphSpec, information: Mapping[str, Any],
) -> dict[str, Any]:
    spec = _materialize_spec(base_spec, state[LATENT_SLICE])
    core = state[:CORE_STATE_DIMENSION]
    audit = structural_audit(core, spec)
    latent_info = information["parameter_groups"]["latent_pelvis_torso_geometry"]["cumulative"]
    latent = np.asarray(state[LATENT_SLICE], dtype=float)
    latent_at_lower = np.isclose(latent, LATENT_LOWER, atol=1e-6, rtol=0.0)
    latent_at_upper = np.isclose(latent, LATENT_UPPER, atol=1e-6, rtol=0.0)
    latent_sigma = np.asarray(
        information["parameter_groups"]["latent_pelvis_torso_geometry"]["posterior_one_sigma"],
        dtype=float,
    )
    latent_observable = bool(
        latent_info["rank"] == LATENT_DIMENSION
        and np.isfinite(latent_sigma).all()
        and not np.any(latent_at_lower | latent_at_upper)
    )
    length_rows = {}
    for segment, index in LENGTH_INDICES.items():
        group = information["parameter_groups"]["tape_observed_lengths"]["cumulative"]
        sigma = information["posterior_one_sigma"][index]
        length_rows[segment] = {
            "estimate_m": float(core[index]),
            "external_prior_mean_m": float(base_spec.segment_lengths_m[segment]),
            "external_prior_sigma_m": float(base_spec.segment_length_sigma_m[segment]),
            "posterior_one_sigma_m": float(sigma),
            "promoted_as_observable": bool(group["rank"] == len(LENGTH_INDICES) and sigma <= 0.03),
        }
    return {
        "structural_audit": audit,
        "latent_pelvis_torso_geometry": {
            name: float(state[CORE_STATE_DIMENSION + index])
            for index, name in enumerate(LATENT_NAMES)
        },
        "latent_geometry_coordinate_audit": {
            name: {
                "estimate_m": float(latent[index]),
                "lower_bound_m": float(LATENT_LOWER[index]),
                "upper_bound_m": float(LATENT_UPPER[index]),
                "posterior_one_sigma_m": float(latent_sigma[index]),
                "active_lower_bound": bool(latent_at_lower[index]),
                "active_upper_bound": bool(latent_at_upper[index]),
            }
            for index, name in enumerate(LATENT_NAMES)
        },
        "full_rank_alone_does_not_override_active_bounds": True,
        "latent_geometry_data_only_observable": latent_observable,
        "latent_geometry_role": (
            "ESTIMATED" if latent_observable else "UNCERTAIN_DIAGNOSTIC_NOT_PROMOTED"
        ),
        "bi_iliac_breadth_used": False,
        "invented_torso_prior_used": False,
        "lengths": length_rows,
        "all_lengths_promoted": all(row["promoted_as_observable"] for row in length_rows.values()),
    }


def readiness_snapshot(
    information: Mapping[str, Any], held_out: Mapping[str, Any],
    physical: Mapping[str, Any], previous_coverage: Mapping[str, float] | None,
) -> dict[str, Any]:
    coverage = {}
    readiness = {}
    for name, row in information["parameter_groups"].items():
        cumulative = row["cumulative"]
        rank_fraction = cumulative["rank"] / max(1, cumulative["dimension"])
        information_fraction = 1.0 - math.exp(
            -cumulative["log1p_information"] / max(1.0, 6.0 * cumulative["dimension"])
        )
        candidate = 100.0 * min(rank_fraction, information_fraction)
        coverage[name] = max(candidate, float((previous_coverage or {}).get(name, 0.0)))
        uncertainty = np.asarray(row["posterior_one_sigma"], dtype=float)
        finite_fraction = float(np.mean(np.isfinite(uncertainty))) if len(uncertainty) else 0.0
        readiness[name] = 100.0 * min(rank_fraction, information_fraction) * finite_fraction
    conflict_factor = 1.0 if held_out["pass"] else max(0.25, 1.0 - 0.12 * len(held_out["named_conflicts"]))
    physical_factor = 1.0 if physical["structural_audit"]["pass"] else 0.25
    overall_coverage = min(99.0, float(np.mean(list(coverage.values()))))
    overall_readiness = min(99.0, float(np.mean(list(readiness.values()))) * conflict_factor * physical_factor)
    return {
        "group_coverage_percent": coverage,
        "group_readiness_percent": readiness,
        "coverage_monotonic_by_definition": True,
        "readiness_can_fall": True,
        "overall_coverage_percent": overall_coverage,
        "overall_readiness_percent": overall_readiness,
        "held_out_conflict_factor": conflict_factor,
        "physical_consistency_factor": physical_factor,
        "one_hundred_percent_for_action_consumption": False,
    }


def classify_episode(
    information: Mapping[str, Any], held_out: Mapping[str, Any],
    previous_held_conflicts: Sequence[str],
) -> dict[str, Any]:
    gain = sum(
        row["marginal_log1p_information_gain"]
        for row in information["parameter_groups"].values()
    )
    new_conflicts = sorted(set(held_out["named_conflicts"]) - set(previous_held_conflicts))
    if new_conflicts:
        classification = "EXPOSES_INCONSISTENCY"
    elif gain <= 1e-6:
        classification = "REDUNDANT_OR_VALIDATION_ONLY"
    else:
        classification = "RAISES_INFORMATION"
    return {
        "classification": classification,
        "aggregate_marginal_log1p_information_gain": float(gain),
        "new_named_conflicts": new_conflicts,
    }


def final_gates(snapshot: Mapping[str, Any]) -> dict[str, bool]:
    info = snapshot["information"]
    solve = snapshot["solve"]
    physical = snapshot["physical"]
    held = snapshot["held_out"]
    heading_sigma = np.asarray(info["posterior_one_sigma"][:HEADING_DIMENSION])
    return {
        "measurement_rank_covers_nine_relative_headings": bool(
            info["parameter_groups"]["relative_headings"]["cumulative"]["rank"] == 9
        ),
        "relative_heading_posterior_sigma_le_15deg": bool(
            np.all(np.degrees(heading_sigma) <= 15.0)
        ),
        "broad_multistart_heading_spread_le_15deg": bool(
            solve["finite_start_count"] >= 2
            and solve["finite_cold_or_broad_start_count"] >= 1
            and solve["multistart_max_heading_spread_deg"] <= 15.0
        ),
        "incremental_matches_cumulative_reference": bool(
            solve["incremental_vs_cumulative_reference"]["incremental_start_finite"]
            and solve["incremental_vs_cumulative_reference"]["heading_max_circular_difference_deg"] is not None
            and solve["incremental_vs_cumulative_reference"]["heading_max_circular_difference_deg"] <= 3.0
        ),
        "held_out_prediction": bool(held["pass"]),
        "coherent_noncollapsed_physical_graph": bool(physical["structural_audit"]["pass"]),
        "latent_pelvis_torso_geometry_observable": bool(
            physical["latent_geometry_data_only_observable"]
        ),
        "tape_observed_lengths_observable_before_promotion": bool(
            physical["all_lengths_promoted"]
        ),
        "gaussian_priors_outside_robust_sensor_loss": True,
    }
