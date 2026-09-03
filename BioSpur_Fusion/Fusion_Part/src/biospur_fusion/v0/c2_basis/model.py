"""Fixed-geometry joint-center objective and hard physical candidate gate."""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Mapping

import numpy as np
from scipy.spatial.transform import Rotation
from scipy.sparse import coo_matrix, csr_matrix, lil_matrix

from biospur_fusion.v0.math3d import rz
from biospur_fusion.v0.raw6_heading import EDGES, SEGMENTS

from .factors import FactorBundle
from .functional import FunctionalCandidate
from .geometry import BodyGeometry
from .staging import BoundedStage


HEADING_SEGMENTS = SEGMENTS[1:]
HEADING_INDEX = {segment: index for index, segment in enumerate(HEADING_SEGMENTS)}
STATE_DIMENSION = 9
STANDING_EDGE_LIMIT_DEG = {
    "pelvis_torso": 55.0,
    "shoulder_left": 100.0,
    "shoulder_right": 100.0,
    "elbow_left": 65.0,
    "elbow_right": 65.0,
    "hip_left": 70.0,
    "hip_right": 70.0,
    "knee_left": 65.0,
    "knee_right": 65.0,
}


@dataclass(frozen=True)
class _PreparedEdgeRows:
    parent_rotation: np.ndarray
    child_rotation: np.ndarray
    parent_force: np.ndarray
    child_force: np.ndarray
    parent_kinematic: np.ndarray
    child_kinematic: np.ndarray
    sample_weight: np.ndarray
    hinge_weight: np.ndarray


@dataclass(frozen=True)
class PreparedEdgeHeadingCost:
    """Fixed kinematic rows for an exact one-coordinate relative-yaw search."""

    edge: str
    parent_joint: np.ndarray
    child_joint_unheaded: np.ndarray
    sample_weight: np.ndarray
    parent_hinge_axis: np.ndarray
    child_hinge_axis_unheaded: np.ndarray
    hinge_weight: np.ndarray
    hinge_sigma_rad: float

    @staticmethod
    def _rotate_z(rows: np.ndarray, delta_rad: float) -> np.ndarray:
        if not len(rows):
            return rows
        cosine = math.cos(float(delta_rad))
        sine = math.sin(float(delta_rad))
        rotated = np.empty_like(rows)
        rotated[:, 0] = cosine * rows[:, 0] - sine * rows[:, 1]
        rotated[:, 1] = sine * rows[:, 0] + cosine * rows[:, 1]
        rotated[:, 2] = rows[:, 2]
        return rotated

    def residual(self, delta_rad: float) -> np.ndarray:
        child_joint = self._rotate_z(self.child_joint_unheaded, delta_rad)
        joint = (
            (self.parent_joint - child_joint) * self.sample_weight[:, None]
        ).reshape(-1)
        if not len(self.parent_hinge_axis):
            return joint
        child_axis = self._rotate_z(self.child_hinge_axis_unheaded, delta_rad)
        hinge = np.cross(self.parent_hinge_axis, child_axis)
        hinge = (hinge * self.hinge_weight[:, None] / self.hinge_sigma_rad).reshape(-1)
        return np.r_[joint, hinge]

    def soft_l1_cost(self, delta_rad: float) -> float:
        rows = self.residual(delta_rad)
        return float(np.sum(np.sqrt(1.0 + rows * rows) - 1.0))

    def audit(self) -> dict[str, Any]:
        return {
            "edge": self.edge,
            "joint_vector_rows": int(len(self.parent_joint)),
            "hinge_vector_rows": int(len(self.parent_hinge_axis)),
            "fixed_kinematic_rows_prepared_once": True,
            "candidate_coordinate": "RELATIVE_Z_YAW_ONLY",
            "measurement_rows_dropped": False,
            "hinge_axis_uncertainty_sigma_rad": self.hinge_sigma_rad,
            "exact_hinge_axis_assumed": False,
        }


def wrap(value: np.ndarray | float) -> np.ndarray:
    return (np.asarray(value, dtype=float) + np.pi) % (2.0 * np.pi) - np.pi


def summarize_held_out_covariance_blocks(
    blocks: list[Mapping[str, Any]], threshold: float,
) -> dict[str, Any]:
    """Gate every preregistered block; retain pooling only as a diagnostic."""

    individual = []
    pooled: dict[tuple[str, str], list[np.ndarray]] = {}
    for block in blocks:
        normalized = np.asarray(block["normalized"], dtype=float)
        nrmse = float(np.sqrt(np.mean(normalized * normalized)))
        individual.append({
            key: value for key, value in block.items() if key != "normalized"
        } | {
            "joint_center_nrmse": nrmse,
            "pass": nrmse <= float(threshold),
        })
        pooled.setdefault((str(block["edge"]), str(block["action"])), []).append(normalized)
    pooled_records = []
    for (edge, action), values in pooled.items():
        normalized = np.concatenate(values)
        nrmse = float(np.sqrt(np.mean(normalized * normalized)))
        pooled_records.append({
            "edge": edge,
            "action": action,
            "covariance_block_count": len(values),
            "scalar_rows": int(normalized.size),
            "joint_center_nrmse": nrmse,
            "pass": nrmse <= float(threshold),
            "secondary_diagnostic_not_acceptance_gate": True,
        })
    conflicts = sorted({
        f"{row['edge']}:{row['action']}:{row['covariance_block_id']}"
        for row in individual if not row["pass"]
    })
    pooled_conflicts = sorted({
        f"{row['edge']}:{row['action']}" for row in pooled_records if not row["pass"]
    })
    return {
        "threshold": float(threshold),
        "acceptance_unit": "INDIVIDUAL_PREREGISTERED_COVARIANCE_BLOCK",
        "records": individual,
        "individual_block_records": individual,
        "pooled_edge_action_records": pooled_records,
        "named_conflicts": conflicts,
        "pooled_named_conflicts_secondary": pooled_conflicts,
        "maximum_joint_center_nrmse": max(
            (row["joint_center_nrmse"] for row in individual), default=math.inf,
        ),
        "maximum_individual_block_nrmse": max(
            (row["joint_center_nrmse"] for row in individual), default=math.inf,
        ),
        "maximum_pooled_edge_action_nrmse_secondary": max(
            (row["joint_center_nrmse"] for row in pooled_records), default=math.inf,
        ),
        "pass": not conflicts,
        "pooled_result_may_not_override_individual_failure": True,
        "held_out_rows_used_to_fit": False,
    }


@dataclass(frozen=True)
class CalibrationState:
    headings_rad: np.ndarray

    def vector(self) -> np.ndarray:
        return self.headings_rad.copy()

    @classmethod
    def from_vector(cls, value: np.ndarray) -> "CalibrationState":
        value = np.asarray(value, dtype=float)
        if value.shape != (STATE_DIMENSION,):
            raise ValueError("C2 state has the wrong dimension")
        return cls(wrap(value))

    def heading(self, segment: str) -> float:
        return 0.0 if segment == "pelvis" else float(self.headings_rad[HEADING_INDEX[segment]])


class C2CalibrationObjective:
    def __init__(
        self,
        bundle: FactorBundle,
        geometry: BodyGeometry,
        functional: FunctionalCandidate,
        *,
        acceleration_sigma_mps2: float = 1.0,
    ) -> None:
        self.bundle = bundle
        self.geometry = geometry
        self.functional = functional
        self.acceleration_sigma_mps2 = float(acceleration_sigma_mps2)
        edge_names = {name for name, _, _, _ in EDGES}
        if set(functional.edge_levers_sensor) != edge_names:
            raise ValueError(
                "C2 objective requires a complete train-derived full-3D "
                "sensor-to-joint placement; axial-only/unrefined entry is disabled"
            )
        self._prepared = {
            partition: {
                edge_name: self._prepare_rows(
                    edge_name, getattr(bundle.edges[edge_name], partition)
                )
                for edge_name, _, _, _ in EDGES
            }
            for partition in ("train", "held_out")
        }

    def rebind_functional(self, functional: FunctionalCandidate) -> "C2CalibrationObjective":
        """Share immutable factor preparation across a bounded branch bank."""

        rebound = object.__new__(C2CalibrationObjective)
        rebound.bundle = self.bundle
        rebound.geometry = self.geometry
        rebound.functional = functional
        rebound.acceleration_sigma_mps2 = self.acceleration_sigma_mps2
        rebound._prepared = {
            partition: {
                edge_name: rebound._prepare_rows(
                    edge_name, getattr(bundle.edges[edge_name], partition)
                )
                for edge_name, _, _, _ in EDGES
            }
            for partition in ("train", "held_out")
        }
        return rebound

    def _prepare_rows(
        self, edge_name: str, blocks: tuple[Any, ...],
    ) -> _PreparedEdgeRows:
        def stack(name: str, shape: tuple[int, ...]) -> np.ndarray:
            values = [np.asarray(getattr(block, name)) for block in blocks]
            return np.concatenate(values, axis=0) if values else np.empty(shape)

        child_rotations = []
        estimate = self.functional.hinge_axes.get(edge_name)
        for block in blocks:
            trajectory = (
                estimate.heading_trajectories.get(block.action)
                if estimate is not None else None
            )
            if trajectory is None:
                child_rotations.append(np.asarray(block.child_rotation))
            else:
                if block.sample_time_ns is None:
                    raise ValueError(
                        f"{edge_name}:{block.action}: trajectory consumer lacks sample time"
                    )
                child_rotations.append(trajectory.rotations_at(block.sample_time_ns))

        return _PreparedEdgeRows(
            parent_rotation=stack("parent_rotation", (0, 3, 3)),
            child_rotation=(
                np.concatenate(child_rotations, axis=0)
                if child_rotations else np.empty((0, 3, 3))
            ),
            parent_force=stack("parent_force", (0, 3)),
            child_force=stack("child_force", (0, 3)),
            parent_kinematic=stack("parent_kinematic", (0, 3, 3)),
            child_kinematic=stack("child_kinematic", (0, 3, 3)),
            sample_weight=stack("sample_weight", (0,)),
            hinge_weight=np.concatenate([
                np.full(len(block.sample_weight), 1.0 / np.sqrt(len(block.sample_weight)))
                for block in blocks
            ]) if blocks else np.empty(0),
        )

    def bounds(self) -> tuple[np.ndarray, np.ndarray]:
        return np.full(9, -math.pi), np.full(9, math.pi)

    def initial_state(self, heading_seed: np.ndarray | None = None) -> CalibrationState:
        headings = np.zeros(9) if heading_seed is None else wrap(np.asarray(heading_seed, dtype=float))
        if headings.shape != (9,):
            raise ValueError("heading seed must have nine coordinates")
        return CalibrationState(headings)

    def _edge_levers_sensor(self) -> Mapping[str, tuple[np.ndarray, np.ndarray]]:
        """Return the owned arbitrary 3-D functional connection vectors."""

        return self.functional.edge_levers_sensor

    def _edge_joint_rows(
        self,
        edge_name: str,
        state: CalibrationState,
        partition: str,
        *,
        parent_heading_rad: float | None = None,
        child_heading_rad: float | None = None,
    ) -> np.ndarray:
        edge = self.bundle.edges[edge_name]
        block = self._prepared[partition][edge_name]
        if not len(block.sample_weight):
            return np.empty(0)
        parent_lever, child_lever = self._edge_levers_sensor()[edge_name]
        parent_heading = rz(
            state.heading(edge.parent) if parent_heading_rad is None else parent_heading_rad
        )
        child_heading = rz(
            state.heading(edge.child) if child_heading_rad is None else child_heading_rad
        )
        rp = np.einsum("ij,njk->nik", parent_heading, block.parent_rotation)
        rc = np.einsum("ij,njk->nik", child_heading, block.child_rotation)
        parent_joint_sensor = block.parent_force + np.einsum(
            "nij,j->ni", block.parent_kinematic, parent_lever,
        )
        child_joint_sensor = block.child_force + np.einsum(
            "nij,j->ni", block.child_kinematic, child_lever,
        )
        parent_joint = np.einsum("nij,nj->ni", rp, parent_joint_sensor)
        child_joint = np.einsum("nij,nj->ni", rc, child_joint_sensor)
        residual = (parent_joint - child_joint) / self.acceleration_sigma_mps2
        return (residual * block.sample_weight[:, None]).reshape(-1)

    def _measurement_rows(self, state: CalibrationState, partition: str) -> np.ndarray:
        rows = [
            self._edge_joint_rows(edge_name, state, partition)
            for edge_name, _, _, _ in EDGES
        ]
        return np.concatenate(rows) if rows else np.empty(0)

    def _hinge_axis_rows(self, state: CalibrationState, partition: str) -> np.ndarray:
        rows: list[np.ndarray] = []
        for edge_name, estimate in self.functional.hinge_axes.items():
            edge = self.bundle.edges[edge_name]
            block = self._prepared[partition][edge_name]
            if not len(block.sample_weight):
                continue
            parent_heading = rz(state.heading(edge.parent))
            child_heading = rz(state.heading(edge.child))
            parent_axis = np.einsum(
                "ij,njk,k->ni", parent_heading, block.parent_rotation,
                estimate.parent_axis_sensor,
            )
            child_axis = np.einsum(
                "ij,njk,k->ni", child_heading, block.child_rotation,
                estimate.child_axis_sensor,
            )
            # Hinge sign is resolved as a functional branch, not by an
            # action-label pose direction.
            parent_sign, child_sign = self.functional.axis_signs[edge_name]
            parent_axis *= float(parent_sign)
            child_axis *= float(child_sign)
            residual = np.cross(parent_axis, child_axis)
            sigma = float(estimate.qmt_axis_report["axis_uncertainty_sigma_rad"])
            rows.append(residual * block.hinge_weight[:, None] / sigma)
        return np.concatenate(rows).reshape(-1) if rows else np.empty(0)

    def edge_measurement_residual(
        self,
        edge_name: str,
        delta_rad: float,
        partition: str = "train",
    ) -> np.ndarray:
        """Evaluate one edge in its yaw-gauge-reduced parent frame."""

        state = CalibrationState(np.zeros(9))
        rows = self._edge_joint_rows(
            edge_name, state, partition,
            parent_heading_rad=0.0, child_heading_rad=float(delta_rad),
        )
        estimate = self.functional.hinge_axes.get(edge_name)
        if estimate is None:
            return rows
        edge = self.bundle.edges[edge_name]
        block = self._prepared[partition][edge_name]
        parent_axis = np.einsum(
            "nij,j->ni", block.parent_rotation, estimate.parent_axis_sensor,
        )
        child_axis = np.einsum(
            "ij,njk,k->ni", rz(float(delta_rad)), block.child_rotation,
            estimate.child_axis_sensor,
        )
        parent_sign, child_sign = self.functional.axis_signs[edge_name]
        hinge = np.cross(parent_axis * parent_sign, child_axis * child_sign)
        sigma = float(estimate.qmt_axis_report["axis_uncertainty_sigma_rad"])
        hinge = (hinge * block.hinge_weight[:, None] / sigma).reshape(-1)
        return np.r_[rows, hinge]

    @staticmethod
    def _sensor_soft_l1(rows: np.ndarray) -> np.ndarray:
        scale = np.sqrt(2.0 / (np.sqrt(1.0 + rows * rows) + 1.0))
        return rows * scale

    @staticmethod
    def _sensor_soft_l1_derivative(rows: np.ndarray) -> np.ndarray:
        root = np.sqrt(1.0 + rows * rows)
        scale = np.sqrt(2.0 / (root + 1.0))
        return scale * (1.0 - rows * rows / (2.0 * root * (root + 1.0)))

    @staticmethod
    def _z_rotation_derivative(rows: np.ndarray) -> np.ndarray:
        result = np.zeros_like(rows)
        result[:, 0] = -rows[:, 1]
        result[:, 1] = rows[:, 0]
        return result

    def residual_and_analytic_jacobian(
        self, value: np.ndarray,
    ) -> tuple[np.ndarray, csr_matrix]:
        """Evaluate the exact Stage-D residual and its sparse analytic Jacobian."""

        state = CalibrationState.from_vector(value)
        points = self.geometry.edge_points_body(state.axial_offsets_m)
        residual_parts: list[np.ndarray] = []
        jacobian_rows: list[np.ndarray] = []
        jacobian_columns: list[np.ndarray] = []
        jacobian_values: list[np.ndarray] = []
        cursor = 0

        def add_column(column: int, derivative: np.ndarray, robust_derivative: np.ndarray) -> None:
            flat = (derivative.reshape(-1) * robust_derivative).astype(float, copy=False)
            jacobian_rows.append(cursor + np.arange(len(flat), dtype=np.int64))
            jacobian_columns.append(np.full(len(flat), int(column), dtype=np.int16))
            jacobian_values.append(flat)

        for edge_name, parent, child, _ in EDGES:
            block = self._prepared["train"][edge_name]
            if not len(block.sample_weight):
                continue
            parent_body, child_body = points[edge_name]
            parent_lever = self.functional.body_from_sensor_by_segment[parent].T @ parent_body
            child_lever = self.functional.body_from_sensor_by_segment[child].T @ child_body
            parent_sensor = block.parent_force + np.einsum(
                "nij,j->ni", block.parent_kinematic, parent_lever,
            )
            child_sensor = block.child_force + np.einsum(
                "nij,j->ni", block.child_kinematic, child_lever,
            )
            parent_unheaded = np.einsum(
                "nij,nj->ni", block.parent_rotation, parent_sensor,
            ) / self.acceleration_sigma_mps2
            child_unheaded = np.einsum(
                "nij,nj->ni", block.child_rotation, child_sensor,
            ) / self.acceleration_sigma_mps2
            parent_joint = PreparedEdgeHeadingCost._rotate_z(
                parent_unheaded, state.heading(parent),
            )
            child_joint = PreparedEdgeHeadingCost._rotate_z(
                child_unheaded, state.heading(child),
            )
            weight = block.sample_weight[:, None]
            raw = ((parent_joint - child_joint) * weight).reshape(-1)
            robust_derivative = self._sensor_soft_l1_derivative(raw)
            residual_parts.append(self._sensor_soft_l1(raw))
            if parent != "pelvis":
                add_column(
                    HEADING_INDEX[parent],
                    self._z_rotation_derivative(parent_joint) * weight,
                    robust_derivative,
                )
            if child != "pelvis":
                add_column(
                    HEADING_INDEX[child],
                    -self._z_rotation_derivative(child_joint) * weight,
                    robust_derivative,
                )
            axial_derivative_body = np.array([0.0, 0.0, -1.0])
            if parent in OFFSET_INDEX:
                lever_derivative = (
                    self.functional.body_from_sensor_by_segment[parent].T
                    @ axial_derivative_body
                )
                sensor_derivative = np.einsum(
                    "nij,j->ni", block.parent_kinematic, lever_derivative,
                )
                unheaded_derivative = np.einsum(
                    "nij,nj->ni", block.parent_rotation, sensor_derivative,
                ) / self.acceleration_sigma_mps2
                world_derivative = PreparedEdgeHeadingCost._rotate_z(
                    unheaded_derivative, state.heading(parent),
                )
                add_column(
                    9 + OFFSET_INDEX[parent], world_derivative * weight,
                    robust_derivative,
                )
            if child in OFFSET_INDEX:
                lever_derivative = (
                    self.functional.body_from_sensor_by_segment[child].T
                    @ axial_derivative_body
                )
                sensor_derivative = np.einsum(
                    "nij,j->ni", block.child_kinematic, lever_derivative,
                )
                unheaded_derivative = np.einsum(
                    "nij,nj->ni", block.child_rotation, sensor_derivative,
                ) / self.acceleration_sigma_mps2
                world_derivative = PreparedEdgeHeadingCost._rotate_z(
                    unheaded_derivative, state.heading(child),
                )
                add_column(
                    9 + OFFSET_INDEX[child], -world_derivative * weight,
                    robust_derivative,
                )
            cursor += len(raw)

        for edge_name, estimate in self.functional.hinge_axes.items():
            edge = self.bundle.edges[edge_name]
            block = self._prepared["train"][edge_name]
            if not len(block.sample_weight):
                continue
            parent_sign, child_sign = self.functional.axis_signs[edge_name]
            parent_unheaded = np.einsum(
                "nij,j->ni", block.parent_rotation, estimate.parent_axis_sensor,
            ) * float(parent_sign)
            child_unheaded = np.einsum(
                "nij,j->ni", block.child_rotation, estimate.child_axis_sensor,
            ) * float(child_sign)
            parent_axis = PreparedEdgeHeadingCost._rotate_z(
                parent_unheaded, state.heading(edge.parent),
            )
            child_axis = PreparedEdgeHeadingCost._rotate_z(
                child_unheaded, state.heading(edge.child),
            )
            sigma = float(estimate.qmt_axis_report["axis_uncertainty_sigma_rad"])
            weight = block.hinge_weight[:, None] / sigma
            raw_matrix = np.cross(parent_axis, child_axis) * weight
            raw = raw_matrix.reshape(-1)
            robust_derivative = self._sensor_soft_l1_derivative(raw)
            residual_parts.append(self._sensor_soft_l1(raw))
            if edge.parent != "pelvis":
                add_column(
                    HEADING_INDEX[edge.parent],
                    np.cross(self._z_rotation_derivative(parent_axis), child_axis) * weight,
                    robust_derivative,
                )
            if edge.child != "pelvis":
                add_column(
                    HEADING_INDEX[edge.child],
                    np.cross(parent_axis, self._z_rotation_derivative(child_axis)) * weight,
                    robust_derivative,
                )
            cursor += len(raw)

        prior = self.offset_prior_residual(value)
        residual_parts.append(prior)
        prior_rows = cursor + np.arange(len(prior), dtype=np.int64)
        jacobian_rows.append(prior_rows)
        jacobian_columns.append(9 + np.arange(len(prior), dtype=np.int16))
        sigma = 0.5 * (self._upper_offsets - self._lower_offsets)
        jacobian_values.append(1.0 / sigma)
        cursor += len(prior)
        residual = np.concatenate(residual_parts)
        jacobian = coo_matrix(
            (
                np.concatenate(jacobian_values),
                (np.concatenate(jacobian_rows), np.concatenate(jacobian_columns)),
            ),
            shape=(cursor, STATE_DIMENSION),
        ).tocsr()
        if residual.shape != (cursor,) or not np.isfinite(residual).all():
            raise FloatingPointError("analytic Stage-D residual is non-finite")
        if not np.isfinite(jacobian.data).all():
            raise FloatingPointError("analytic Stage-D Jacobian is non-finite")
        return residual, jacobian

    def edge_soft_l1_cost(
        self, edge_name: str, delta_rad: float, axial_offsets_m: np.ndarray,
    ) -> float:
        rows = self.edge_measurement_residual(edge_name, delta_rad, axial_offsets_m)
        return float(np.sum(np.sqrt(1.0 + rows * rows) - 1.0))

    def prepare_edge_heading_cost(
        self, edge_name: str, axial_offsets_m: np.ndarray,
    ) -> PreparedEdgeHeadingCost:
        """Precompute delta-independent rows without changing the objective."""

        state = CalibrationState(np.zeros(9), np.asarray(axial_offsets_m, dtype=float))
        block = self._prepared["train"][edge_name]
        if not len(block.sample_weight):
            raise ValueError(f"{edge_name}: no training rows for heading preparation")
        parent_lever, child_lever = self._edge_levers_sensor(state)[edge_name]
        parent_joint_sensor = block.parent_force + np.einsum(
            "nij,j->ni", block.parent_kinematic, parent_lever,
        )
        child_joint_sensor = block.child_force + np.einsum(
            "nij,j->ni", block.child_kinematic, child_lever,
        )
        parent_joint = np.einsum(
            "nij,nj->ni", block.parent_rotation, parent_joint_sensor,
        ) / self.acceleration_sigma_mps2
        child_joint = np.einsum(
            "nij,nj->ni", block.child_rotation, child_joint_sensor,
        ) / self.acceleration_sigma_mps2
        estimate = self.functional.hinge_axes.get(edge_name)
        if estimate is None:
            parent_axis = np.empty((0, 3))
            child_axis = np.empty((0, 3))
            hinge_weight = np.empty(0)
        else:
            parent_sign, child_sign = self.functional.axis_signs[edge_name]
            parent_axis = np.einsum(
                "nij,j->ni", block.parent_rotation, estimate.parent_axis_sensor,
            ) * float(parent_sign)
            child_axis = np.einsum(
                "nij,j->ni", block.child_rotation, estimate.child_axis_sensor,
            ) * float(child_sign)
            hinge_weight = block.hinge_weight
        hinge_sigma_rad = (
            float(estimate.qmt_axis_report["axis_uncertainty_sigma_rad"])
            if estimate is not None else 1.0
        )
        if not np.isfinite(hinge_sigma_rad) or hinge_sigma_rad <= 0.0:
            raise ValueError(f"{edge_name}: hinge-axis uncertainty is invalid")
        return PreparedEdgeHeadingCost(
            edge=edge_name,
            parent_joint=parent_joint,
            child_joint_unheaded=child_joint,
            sample_weight=block.sample_weight,
            parent_hinge_axis=parent_axis,
            child_hinge_axis_unheaded=child_axis,
            hinge_weight=hinge_weight,
            hinge_sigma_rad=hinge_sigma_rad,
        )

    def edge_standing_relative_angle_deg(self, edge_name: str, delta_rad: float) -> float:
        """Standing manifold gate for one relative-heading edge candidate."""

        edge = self.bundle.edges[edge_name]
        blocks = [block for block in edge.train if block.action == "00_initial_still"]
        if not blocks:
            raise ValueError("initial still is absent from heading candidate gate")
        parent_rows = np.concatenate([block.parent_rotation for block in blocks])
        child_rows = np.concatenate([block.child_rotation for block in blocks])
        parent_sensor = Rotation.from_matrix(parent_rows).mean().as_matrix()
        child_sensor = Rotation.from_matrix(child_rows).mean().as_matrix()
        parent_body = parent_sensor @ self.functional.body_from_sensor_by_segment[edge.parent].T
        child_body = (
            rz(float(delta_rad)) @ child_sensor
            @ self.functional.body_from_sensor_by_segment[edge.child].T
        )
        return float(np.degrees(Rotation.from_matrix(parent_body.T @ child_body).magnitude()))

    def measurement_residual(self, value: np.ndarray, partition: str = "train") -> np.ndarray:
        state = CalibrationState.from_vector(value)
        return np.r_[
            self._measurement_rows(state, partition),
            self._hinge_axis_rows(state, partition),
        ]

    def offset_prior_residual(self, value: np.ndarray) -> np.ndarray:
        state = CalibrationState.from_vector(value)
        sigma = 0.5 * (self._upper_offsets - self._lower_offsets)
        return (state.axial_offsets_m - self._nominal_offsets) / sigma

    def residual(self, value: np.ndarray, *, include_offset_prior: bool = True) -> np.ndarray:
        measurement = self.measurement_residual(value, "train")
        # Soft-L1 is applied only to sensor rows. Physical feasibility is a
        # separate reject gate and cannot be bought off by this loss.
        robust = self._sensor_soft_l1(measurement)
        return np.r_[robust, self.offset_prior_residual(value)] if include_offset_prior else robust

    def residual_jacobian_sparsity(self) -> csr_matrix:
        """Exact state dependency pattern for colored finite differences."""

        measurement_rows = len(self.measurement_residual(self.initial_state().vector(), "train"))
        matrix = lil_matrix((measurement_rows + len(LIMBS), STATE_DIMENSION), dtype=np.int8)
        cursor = 0
        for edge_name, parent, child, _ in EDGES:
            count = 3 * len(self._prepared["train"][edge_name].sample_weight)
            columns = []
            if parent != "pelvis":
                columns.append(HEADING_INDEX[parent])
            if child != "pelvis":
                columns.append(HEADING_INDEX[child])
            if parent in OFFSET_INDEX:
                columns.append(9 + OFFSET_INDEX[parent])
            if child in OFFSET_INDEX:
                columns.append(9 + OFFSET_INDEX[child])
            if columns and count:
                matrix[cursor:cursor + count, columns] = 1
            cursor += count
        for edge_name, estimate in self.functional.hinge_axes.items():
            edge = self.bundle.edges[edge_name]
            count = 3 * len(self._prepared["train"][edge_name].sample_weight)
            columns = []
            if edge.parent != "pelvis":
                columns.append(HEADING_INDEX[edge.parent])
            if edge.child != "pelvis":
                columns.append(HEADING_INDEX[edge.child])
            if columns and count:
                matrix[cursor:cursor + count, columns] = 1
            cursor += count
        if cursor != measurement_rows:
            raise RuntimeError("C2 residual sparsity row accounting changed")
        for index in range(len(LIMBS)):
            matrix[measurement_rows + index, 9 + index] = 1
        return matrix.tocsr()

    def costs(
        self, value: np.ndarray, *, held_out_deadline: BoundedStage | None = None,
    ) -> dict[str, float]:
        measurement = self.measurement_residual(value, "train")
        held = (
            held_out_deadline.run(
                "held_out_measurement_residual",
                lambda: self.measurement_residual(value, "held_out"),
            )
            if held_out_deadline is not None
            else self.measurement_residual(value, "held_out")
        )
        prior = self.offset_prior_residual(value)
        return {
            "train_sensor_soft_l1": float(np.sum(np.sqrt(1.0 + measurement ** 2) - 1.0)),
            "held_out_sensor_soft_l1": float(np.sum(np.sqrt(1.0 + held ** 2) - 1.0)),
            "held_out_nrmse": float(np.sqrt(np.mean(held ** 2))),
            "offset_prior_half_squared": float(0.5 * prior @ prior),
        }

    def held_out_report(
        self,
        value: np.ndarray,
        threshold: float,
        *,
        deadline: BoundedStage | None = None,
    ) -> dict[str, Any]:
        state = CalibrationState.from_vector(value)
        levers = self._edge_levers_sensor(state)
        block_records: list[dict[str, Any]] = []
        for edge_name, parent, child, _ in EDGES:
            parent_lever, child_lever = levers[edge_name]
            parent_heading = rz(state.heading(parent))
            child_heading = rz(state.heading(child))
            for block in self.bundle.edges[edge_name].held_out:
                def evaluate_block() -> dict[str, Any]:
                    rp = np.einsum("ij,njk->nik", parent_heading, block.parent_rotation)
                    rc = np.einsum("ij,njk->nik", child_heading, block.child_rotation)
                    parent_joint = np.einsum(
                        "nij,nj->ni", rp,
                        block.parent_force + np.einsum(
                            "nij,j->ni", block.parent_kinematic, parent_lever,
                        ),
                    )
                    child_joint = np.einsum(
                        "nij,nj->ni", rc,
                        block.child_force + np.einsum(
                            "nij,j->ni", block.child_kinematic, child_lever,
                        ),
                    )
                    if block.information_sigma_mps2 is None:
                        raise ValueError(
                            "C2 held-out block lacks its preregistered covariance scale"
                        )
                    normalized = (
                        (parent_joint - child_joint)
                        / (self.acceleration_sigma_mps2 * block.information_sigma_mps2)
                    )
                    return {
                        "normalized": normalized,
                        "covariance_block_id": block.covariance_block_id,
                        "edge": edge_name,
                        "action": block.action,
                        "scalar_rows": int(normalized.size),
                        "effective_sample_size": float(block.effective_sample_size or 0.0),
                        "joint_center_sigma_mps2": block.information_sigma_mps2,
                    }

                label = f"{edge_name}:{block.covariance_block_id}"
                block_records.append(
                    deadline.run_quiet(label, evaluate_block)
                    if deadline is not None else evaluate_block()
                )
            if deadline is not None:
                deadline.checkpoint(
                    f"{edge_name}:held_out_blocks_complete",
                    block_count=len(self.bundle.edges[edge_name].held_out),
                )
        report = summarize_held_out_covariance_blocks(block_records, threshold)
        return {
            **report,
            "bounded_stage": (
                deadline.report() if deadline is not None
                else {"complete": True, "bounded": False}
            ),
        }

    def standing_kinematics(self, value: np.ndarray) -> dict[str, Any]:
        state = CalibrationState.from_vector(value)
        levers = self._edge_levers_sensor(state)
        mean_sensor_rotation: dict[str, np.ndarray] = {}
        for edge_name, parent, child, _ in EDGES:
            edge = self.bundle.edges[edge_name]
            blocks = [block for block in edge.train if block.action == "00_initial_still"]
            if not blocks:
                raise ValueError("initial still is absent from standing gate")
            block = blocks[0]
            phase_mask = np.isin(block.phase, ["VERIFIED_PRE_REST", "VERIFIED_POST_REST"])
            if not np.any(phase_mask):
                phase_mask = np.ones(len(block.phase), dtype=bool)
            if parent not in mean_sensor_rotation:
                mean_sensor_rotation[parent] = Rotation.from_matrix(
                    block.parent_rotation[phase_mask]
                ).mean().as_matrix()
            mean_sensor_rotation[child] = Rotation.from_matrix(
                block.child_rotation[phase_mask]
            ).mean().as_matrix()
        corrected_sensor = {
            segment: rz(state.heading(segment)) @ mean_sensor_rotation[segment]
            for segment in SEGMENTS
        }
        body_rotation = {
            segment: corrected_sensor[segment]
            @ self.functional.body_from_sensor_by_segment[segment].T
            for segment in SEGMENTS
        }
        sensor_origin = {"pelvis": np.zeros(3)}
        joints: dict[str, np.ndarray] = {}
        for edge_name, parent, child, _ in EDGES:
            parent_lever, child_lever = levers[edge_name]
            joint = sensor_origin[parent] + corrected_sensor[parent] @ parent_lever
            joints[edge_name] = joint
            sensor_origin[child] = joint - corrected_sensor[child] @ child_lever
        points = self.geometry.connection_points_body(state.axial_offsets_m)
        endpoints = {
            "wrist_left": sensor_origin["forearm_left"] + body_rotation["forearm_left"] @ points["forearm_left"][1],
            "wrist_right": sensor_origin["forearm_right"] + body_rotation["forearm_right"] @ points["forearm_right"][1],
            "ankle_left": sensor_origin["shank_left"] + body_rotation["shank_left"] @ points["shank_left"][1],
            "ankle_right": sensor_origin["shank_right"] + body_rotation["shank_right"] @ points["shank_right"][1],
        }
        return {
            "state": state,
            "sensor_origin": sensor_origin,
            "joints": joints,
            "endpoints": endpoints,
            "corrected_sensor_rotation": corrected_sensor,
            "body_rotation": body_rotation,
            "edge_levers_sensor": levers,
        }

    def feasibility_report(self, value: np.ndarray, config: Mapping[str, Any]) -> dict[str, Any]:
        kinematics = self.standing_kinematics(value)
        gate_cfg = config["hard_feasibility"]
        pelvis_rotation = kinematics["body_rotation"]["pelvis"]
        pelvis_origin = kinematics["sensor_origin"]["pelvis"]

        def pelvis_coordinate(point: np.ndarray) -> np.ndarray:
            return pelvis_rotation.T @ (point - pelvis_origin)

        points = {
            "hip_left": kinematics["joints"]["hip_left"],
            "hip_right": kinematics["joints"]["hip_right"],
            "knee_left": kinematics["joints"]["knee_left"],
            "knee_right": kinematics["joints"]["knee_right"],
            **kinematics["endpoints"],
        }
        local = {name: pelvis_coordinate(point) for name, point in points.items()}
        minimum_separation = float(gate_cfg["minimum_left_right_joint_separation_m"])
        lateral = {
            "hip": float(local["hip_left"][1] - local["hip_right"][1]),
            "knee": float(local["knee_left"][1] - local["knee_right"][1]),
            "ankle": float(local["ankle_left"][1] - local["ankle_right"][1]),
        }
        knee_split = float(abs(local["knee_left"][0] - local["knee_right"][0]))
        vertical_drop = {
            "left_hip_to_knee": float(points["hip_left"][2] - points["knee_left"][2]),
            "right_hip_to_knee": float(points["hip_right"][2] - points["knee_right"][2]),
            "left_knee_to_ankle": float(points["knee_left"][2] - points["ankle_left"][2]),
            "right_knee_to_ankle": float(points["knee_right"][2] - points["ankle_right"][2]),
        }
        determinants = {
            segment: float(np.linalg.det(rotation))
            for segment, rotation in kinematics["body_rotation"].items()
        }
        standing_relative_angle = {
            edge_name: float(np.degrees(Rotation.from_matrix(
                kinematics["body_rotation"][parent].T @ kinematics["body_rotation"][child]
            ).magnitude()))
            for edge_name, parent, child, _ in EDGES
        }
        gates = {
            "left_right_order": all(value >= minimum_separation for value in lateral.values()),
            "knee_front_back_split": knee_split <= float(gate_cfg["maximum_standing_knee_front_back_split_m"]),
            "proximal_distal_vertical_order": all(
                value >= float(gate_cfg["minimum_standing_proximal_distal_vertical_drop_m"])
                for value in vertical_drop.values()
            ),
            "proper_nonmirrored_rotations": all(
                value >= float(gate_cfg["minimum_rotation_determinant"])
                for value in determinants.values()
            ),
            "broad_standing_rom": all(
                standing_relative_angle[name] <= limit
                for name, limit in STANDING_EDGE_LIMIT_DEG.items()
            ),
            "wear_metadata_hemisphere": bool(self.functional.wear_report["hard_pass"]),
            "fixed_noncollapsed_geometry": all(
                self.geometry.segments[name].value_m >= self.geometry.segments[name].lower_m
                for name in self.geometry.segments
            ),
        }
        return {
            "gates": gates,
            "pass": all(gates.values()),
            "left_minus_right_lateral_m": lateral,
            "knee_front_back_split_m": knee_split,
            "vertical_drop_m": vertical_drop,
            "rotation_determinants": determinants,
            "standing_relative_angle_deg": standing_relative_angle,
            "candidate_gate_not_optimizer_loss": True,
            "kinematics": kinematics,
        }
