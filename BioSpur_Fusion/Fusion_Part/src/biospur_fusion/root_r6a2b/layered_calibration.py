"""Layered Capture-1 development calibration on the canonical Root-R6A0 FK.

This module intentionally separates the 114 independent static coordinates
from fixed and derived views.  It is a development calibration path: it does
not qualify an RF phase centre, a V4-to-navigation transform, or production
covariance parameters.
"""
from __future__ import annotations

from dataclasses import dataclass
from itertools import permutations, product
from typing import Any, Mapping, Sequence

import numpy as np
from scipy.optimize import least_squares

from biospur_fusion.root_r6a0.body import BodyModel, KeyframeState, StaticCalibration
from biospur_fusion.root_r6a0.contracts import CalibrationSlot, CalibrationStatus
from biospur_fusion.root_r6a0.math3d import so3_exp, so3_log


NODES = (
    "BSF1120", "BSF31CC", "BSF3C79", "BSF44AD", "BSF6C53",
    "BSF8BC4", "BSFAA61", "BSFB165", "BSFC2CC", "BSFEC35",
)
COMMON_NINE = tuple(node for node in NODES if node != "BSF31CC")
FAMILIES = {
    **{node: "COMMON_NINE_V0_20_PCB17" for node in COMMON_NINE},
    "BSF31CC": "BSF31CC_V0_20_N5BL",
}
FUNCTIONAL_WINDOW = {
    "BSF1120": "right_elbow2", "BSF31CC": "trunk",
    "BSF3C79": "right_knee", "BSF44AD": "left_knee",
    "BSF6C53": "left_knee", "BSF8BC4": "right_knee",
    "BSFAA61": "left_elbow", "BSFB165": "right_elbow2",
    "BSFC2CC": "trunk", "BSFEC35": "left_elbow",
}


class _ValidatedStateView:
    """Immutable state validated once before repeated finite differences."""

    def __init__(self, state: KeyframeState, model: BodyModel):
        state.validate(model)
        for name in (
            "time_s", "root_translation_model_m", "root_rotation_model_rotvec",
            "root_velocity_model_mps", "joint_rotvec", "joint_rate_rad_s",
            "gyro_bias_rad_s", "accel_bias_mps2", "covariance",
        ):
            setattr(self, name, getattr(state, name))

    def validate(self, model: BodyModel) -> None:
        # The wrapped state is immutable and was validated in __init__.
        return None


class _CachedStaticCalibrationView:
    """Cache one objective evaluation's immutable slot vectors and poses."""

    def __init__(self, calibration: StaticCalibration, model: BodyModel):
        self._calibration = calibration
        self._vectors = {
            joint.parent_offset_slot: calibration.vector(joint.parent_offset_slot, 3)
            for joint in model.joints
        }
        self._vectors.update({
            joint.child_offset_slot: calibration.vector(joint.child_offset_slot, 3)
            for joint in model.joints
        })
        self._vectors.update({
            joint.rest_rotation_slot: calibration.vector(joint.rest_rotation_slot, 3)
            for joint in model.joints
        })
        self._poses = {"world_model_gauge": calibration.pose("world_model_gauge")}
        self._poses.update({sensor.extrinsic_slot: calibration.pose(sensor.extrinsic_slot)
                            for sensor in model.imus})

    def vector(self, slot_id: str, dimension: int) -> np.ndarray:
        value = self._vectors.get(slot_id)
        return self._calibration.vector(slot_id, dimension) if value is None else value

    def pose(self, slot_id: str):
        if slot_id in self._poses:
            return self._poses[slot_id]
        return self._calibration.pose(slot_id)


@dataclass(frozen=True)
class ParameterBlock:
    slot_id: str
    kind: str
    dimension: int
    start: int
    stop: int
    rotation: bool


def proper_signed_permutations() -> tuple[tuple[str, np.ndarray], ...]:
    """Return all 24 orientation-preserving register-to-device maps."""
    rows: list[tuple[str, np.ndarray]] = []
    eye = np.eye(3, dtype=int)
    axes = "XYZ"
    for order in permutations(range(3)):
        for signs in product((-1, 1), repeat=3):
            matrix = np.diag(signs) @ eye[list(order)]
            if round(float(np.linalg.det(matrix))) != 1:
                continue
            label = ",".join(
                f"{('+' if signs[index] > 0 else '-')}{axes[order[index]]}"
                for index in range(3)
            )
            rows.append((label, matrix.astype(float)))
    return tuple(rows)


def _covariance_tuple(covariance: np.ndarray) -> tuple[tuple[float, ...], ...]:
    return tuple(tuple(float(value) for value in row) for row in covariance)


class CanonicalCalibrationAdapter:
    """One-to-one adapter for the 28 authorized slots / 114 coordinates."""

    dimension = 114

    def __init__(self, model: BodyModel, source_rows: Sequence[Mapping[str, Any]]):
        self.model = model
        self.source_rows = {str(row["slot_id"]): dict(row) for row in source_rows}
        blocks: list[ParameterBlock] = []
        cursor = 0
        for node in NODES:
            blocks.append(ParameterBlock(f"imu_extrinsic:{node}", "imu_extrinsic", 6,
                                         cursor, cursor + 6, True))
            cursor += 6
        for joint in model.joints:
            blocks.append(ParameterBlock(joint.parent_offset_slot, "joint_parent", 3,
                                         cursor, cursor + 3, False))
            cursor += 3
        for joint in model.joints:
            blocks.append(ParameterBlock(joint.rest_rotation_slot, "joint_rest", 3,
                                         cursor, cursor + 3, True))
            cursor += 3
        if cursor != self.dimension or len(blocks) != 28:
            raise RuntimeError("canonical static parameter accounting changed")
        self.blocks = tuple(blocks)
        self.by_slot = {block.slot_id: block for block in blocks}
        if len(self.by_slot) != 28:
            raise RuntimeError("duplicate canonical calibration freedom")

        prior_sigma = np.empty(self.dimension, dtype=float)
        prior_sigma[:] = np.nan
        for block in self.blocks:
            if block.kind == "imu_extrinsic":
                # R6A1B authority-ledger orientation covariance is deliberately
                # broad; retain the existing session-bounded translation prior.
                prior_sigma[block.start:block.stop] = (np.pi, np.pi, np.pi, 0.05, 0.05, 0.05)
            elif block.kind == "joint_parent":
                prior_sigma[block.start:block.stop] = 0.2
            else:
                prior_sigma[block.start:block.stop] = np.pi
        if not np.isfinite(prior_sigma).all():
            raise RuntimeError("prior construction left a coordinate unowned")
        self.prior_mean = np.zeros(self.dimension)
        self.prior_sigma = prior_sigma
        self.prior_provenance = {
            "imu_extrinsic": (
                "R6A1B authority-ledger donning prior: rotation [pi,pi,pi] rad; "
                "existing R6A2B bounded translation [0.05,0.05,0.05] m"
            ),
            "joint_parent": "R6A1B authority-ledger covariance: 0.2 m one sigma per axis",
            "joint_rest": "R6A1B authority-ledger covariance: pi rad one sigma per axis",
            "prior_mean": "zero is the local-coordinate chart origin, not a physical measurement",
        }

    @property
    def slot_order(self) -> tuple[str, ...]:
        return tuple(block.slot_id for block in self.blocks)

    def ordering_json(self) -> list[dict[str, Any]]:
        return [
            {
                "slot_id": block.slot_id, "kind": block.kind,
                "dimension": block.dimension, "start_inclusive": block.start,
                "stop_exclusive": block.stop,
                "coordinate": (
                    "SO(3) right-local rotvec then R^3 translation"
                    if block.kind == "imu_extrinsic" else
                    "SO(3) right-local rotvec" if block.rotation else "R^3 local point (m)"
                ),
            }
            for block in self.blocks
        ]

    def vector_to_slots(self, vector: np.ndarray) -> dict[str, np.ndarray]:
        value = np.asarray(vector, dtype=float)
        if value.shape != (self.dimension,) or not np.isfinite(value).all():
            raise ValueError("canonical calibration vector must be finite with dimension 114")
        return {block.slot_id: value[block.start:block.stop].copy() for block in self.blocks}

    def slots_to_vector(self, slots: Mapping[str, Sequence[float]]) -> np.ndarray:
        if set(slots) != set(self.by_slot):
            raise ValueError("slot inventory does not equal the canonical 28-slot inventory")
        value = np.empty(self.dimension, dtype=float)
        for block in self.blocks:
            row = np.asarray(slots[block.slot_id], dtype=float)
            if row.shape != (block.dimension,) or not np.isfinite(row).all():
                raise ValueError(f"invalid value for {block.slot_id}")
            value[block.start:block.stop] = row
        return value

    def right_local_perturb(self, vector: np.ndarray, delta: np.ndarray) -> np.ndarray:
        """Apply SO(3) increments without treating rotation matrices as Euclidean."""
        base = np.asarray(vector, dtype=float)
        step = np.asarray(delta, dtype=float)
        if base.shape != (self.dimension,) or step.shape != (self.dimension,):
            raise ValueError("local perturbations use the complete 114-vector")
        result = base + step
        for block in self.blocks:
            if not block.rotation:
                continue
            length = 3
            current = base[block.start:block.start + length]
            increment = step[block.start:block.start + length]
            result[block.start:block.start + length] = so3_log(
                so3_exp(current) @ so3_exp(increment)
            )
        return result

    def rotation_coordinates(self) -> np.ndarray:
        indices: list[int] = []
        for block in self.blocks:
            if block.kind == "imu_extrinsic":
                indices.extend(range(block.start, block.start + 3))
            elif block.kind == "joint_rest":
                indices.extend(range(block.start, block.stop))
        return np.asarray(indices, dtype=int)

    def geometry_coordinates(self) -> np.ndarray:
        indices: list[int] = []
        for block in self.blocks:
            if block.kind == "imu_extrinsic":
                indices.extend(range(block.start + 3, block.stop))
            elif block.kind == "joint_parent":
                indices.extend(range(block.start, block.stop))
        return np.asarray(indices, dtype=int)

    def materialize_static(
        self,
        vector: np.ndarray,
        *,
        covariance: np.ndarray | None = None,
        internal_levers: Mapping[str, np.ndarray] | None = None,
    ) -> StaticCalibration:
        """Create the sole executable FK view; fixed/derived rows add no DOF."""
        values = self.vector_to_slots(vector)
        full_cov = np.diag(self.prior_sigma ** 2) if covariance is None else np.asarray(covariance, float)
        if full_cov.shape != (self.dimension, self.dimension):
            raise ValueError("static covariance must be 114 by 114")
        slots: dict[str, CalibrationSlot] = {}
        for slot_id, source in self.source_rows.items():
            block = self.by_slot.get(slot_id)
            if block is not None:
                slot_cov = full_cov[block.start:block.stop, block.start:block.stop]
                slots[slot_id] = CalibrationSlot(
                    slot_id, str(source["category"]), str(source["node/joint/anchor"]),
                    CalibrationStatus.VERIFIED_INPUT,
                    tuple(float(x) for x in values[slot_id]), _covariance_tuple(slot_cov),
                    "ROOT_R6A2B_R2_DEVELOPMENT_CAPTURE1_ESTIMATE",
                )
                continue
            authority = str(source["authority_class"])
            source_value = source.get("value")
            if authority == "FIX_BY_CONVENTION":
                fixed = np.zeros(int(source["dimension"]))
                slots[slot_id] = CalibrationSlot(
                    slot_id, str(source["category"]), str(source["node/joint/anchor"]),
                    CalibrationStatus.VERIFIED_INPUT, tuple(float(x) for x in fixed),
                    _covariance_tuple(np.zeros((len(fixed), len(fixed)))),
                    "ROOT_R6A2B_R2_ZERO_DOF_CONVENTION_VIEW",
                )
            elif source_value is not None:
                known = np.asarray(source_value, dtype=float)
                slots[slot_id] = CalibrationSlot(
                    slot_id, str(source["category"]), str(source["node/joint/anchor"]),
                    CalibrationStatus.VERIFIED_INPUT, tuple(float(x) for x in known),
                    _covariance_tuple(np.eye(len(known))),
                    "ROOT_R6A2B_R1_CAPTURE_BOUND_IMPORT_UNCHANGED",
                )
            else:
                slots[slot_id] = CalibrationSlot(
                    slot_id, str(source["category"]), str(source["node/joint/anchor"]),
                    CalibrationStatus.FROZEN_UNCERTAIN, None, None,
                    "ROOT_R6A2B_R2_UNRESOLVED_NONEXECUTABLE_VIEW",
                )

        # Derived tag levers are a bounded nominal view only.  They never enter
        # the optimizer and therefore do not duplicate extrinsic translation.
        if internal_levers is not None:
            for node in NODES:
                slot_id = f"tag_lever:{node}"
                extrinsic = values[f"imu_extrinsic:{node}"]
                derived = extrinsic[3:] + so3_exp(extrinsic[:3]) @ np.asarray(internal_levers[node], float)
                slots[slot_id] = CalibrationSlot(
                    slot_id, "tag_lever", node, CalibrationStatus.VERIFIED_INPUT,
                    tuple(float(x) for x in derived),
                    _covariance_tuple(np.eye(3) * 0.05 ** 2),
                    "ROOT_R6A2B_R2_DERIVED_BOUNDED_COMPONENT_REFERENCE_NOT_RF_QUALIFIED",
                )
        return StaticCalibration(slots)


def _rotation_from_two_directions(gravity: np.ndarray, axis: np.ndarray) -> np.ndarray:
    gravity = np.asarray(gravity, float)
    gravity /= np.linalg.norm(gravity)
    axis = np.asarray(axis, float) - gravity * float(gravity @ axis)
    axis /= np.linalg.norm(axis)
    candidates = []
    for sign in (-1.0, 1.0):
        x_axis = sign * axis
        y_axis = np.cross(gravity, x_axis)
        y_axis /= np.linalg.norm(y_axis)
        basis = np.column_stack((x_axis, y_axis, gravity))
        rotation = basis.T
        candidates.append((float(np.linalg.norm(so3_log(rotation))), rotation))
    return min(candidates, key=lambda item: item[0])[1]


def signed_axis_audit(
    gravity_by_node: Mapping[str, np.ndarray],
    functional_axis_by_node: Mapping[str, np.ndarray],
) -> dict[str, Any]:
    """Score all hypotheses; inertial data leave the family maps gauge-equivalent."""
    permutations_ = proper_signed_permutations()
    families = {
        "COMMON_NINE_V0_20_PCB17": COMMON_NINE,
        "BSF31CC_V0_20_N5BL": ("BSF31CC",),
    }
    result: dict[str, Any] = {}
    for family, nodes in families.items():
        rows = []
        for label, signed in permutations_:
            data_objective = 0.0
            prior_tiebreak = 0.0
            per_node = {}
            for node in nodes:
                gravity = signed @ np.asarray(gravity_by_node[node], float)
                axis = signed @ np.asarray(functional_axis_by_node[node], float)
                rotation = _rotation_from_two_directions(gravity, axis)
                gravity_unit = gravity / np.linalg.norm(gravity)
                axis_orthogonal = axis - gravity_unit * float(gravity_unit @ axis)
                axis_orthogonal /= np.linalg.norm(axis_orthogonal)
                gravity_residual = rotation @ gravity_unit - np.array([0.0, 0.0, 1.0])
                axis_residual = np.abs(rotation @ axis_orthogonal) - np.array([1.0, 0.0, 0.0])
                node_objective = 0.5 * float(gravity_residual @ gravity_residual + axis_residual @ axis_residual)
                data_objective += node_objective
                prior_tiebreak += 0.5 * float(so3_log(rotation) @ so3_log(rotation))
                per_node[node] = {
                    "data_objective": node_objective,
                    "prior_tiebreak": 0.5 * float(so3_log(rotation) @ so3_log(rotation)),
                }
            rows.append({
                "label": label, "matrix": signed.tolist(),
                "data_objective": data_objective,
                "unqualified_prior_tiebreak_objective": prior_tiebreak,
                "per_node": per_node,
            })
        minimum = min(row["data_objective"] for row in rows)
        numerical = np.finfo(float).eps * max(1.0, abs(minimum)) * 128.0
        surviving = [row["label"] for row in rows if abs(row["data_objective"] - minimum) <= numerical]
        ranked_prior = sorted(rows, key=lambda row: (row["unqualified_prior_tiebreak_objective"], row["label"]))
        result[family] = {
            "hypotheses": rows,
            "data_only_minimum": minimum,
            "data_only_surviving_labels": surviving,
            "data_only_evidence_margin": 0.0 if len(surviving) > 1 else (
                sorted(row["data_objective"] for row in rows)[1] - minimum
            ),
            "representative_for_coordinate_gauge": "+X,+Y,+Z",
            "unqualified_prior_tiebreak_winner": ranked_prior[0]["label"],
            "unqualified_prior_tiebreak_margin": (
                ranked_prior[1]["unqualified_prior_tiebreak_objective"]
                - ranked_prior[0]["unqualified_prior_tiebreak_objective"]
            ),
            "interpretation": (
                "All proper family maps can be absorbed by node-specific SO(3) extrinsics; "
                "the real inertial objective therefore retains the finite family-map ambiguity."
            ),
        }
    return result


def solve_rotation_layer(
    adapter: CanonicalCalibrationAdapter,
    gravity_by_node: Mapping[str, np.ndarray],
    functional_axis_by_node: Mapping[str, np.ndarray],
    signed_by_node: Mapping[str, np.ndarray],
) -> tuple[np.ndarray, dict[str, Any], np.ndarray]:
    coordinates = adapter.rotation_coordinates()
    x0 = np.zeros(len(coordinates))
    full_template = adapter.prior_mean.copy()

    def unpack(local: np.ndarray) -> np.ndarray:
        full = full_template.copy()
        full[coordinates] = local
        return full

    def data_residual(local: np.ndarray) -> np.ndarray:
        full = unpack(local)
        rows = []
        for node in NODES:
            block = adapter.by_slot[f"imu_extrinsic:{node}"]
            rotation = so3_exp(full[block.start:block.start + 3])
            signed = signed_by_node[node]
            gravity = signed @ np.asarray(gravity_by_node[node], float)
            gravity /= np.linalg.norm(gravity)
            axis = signed @ np.asarray(functional_axis_by_node[node], float)
            axis = axis - gravity * float(gravity @ axis)
            axis /= np.linalg.norm(axis)
            # PCA axes are unoriented.  Keep both signs in a differentiable
            # residual by selecting the closer branch at each evaluation.
            transformed = rotation @ axis
            axis_target = np.array([1.0, 0.0, 0.0])
            axis_row = min((transformed - axis_target, transformed + axis_target),
                           key=lambda value: float(value @ value))
            rows.extend((rotation @ gravity - np.array([0.0, 0.0, 1.0]), axis_row))
        # Joint rest is estimated in the same solve, but neutral-pose frame
        # co-definition and its broad prior leave it explicitly data-null.
        return np.concatenate(rows)

    prior_sigma = adapter.prior_sigma[coordinates]

    def objective(local: np.ndarray) -> np.ndarray:
        return np.concatenate((data_residual(local), local / prior_sigma))

    initial_residual = objective(x0)
    result = least_squares(
        objective, x0, method="trf", jac="3-point", x_scale="jac",
        ftol=1e-11, xtol=1e-11, gtol=1e-11, max_nfev=120,
    )
    full = unpack(result.x)
    data_jacobian = numerical_jacobian(data_residual, result.x)
    report = {
        "optimizer": "scipy.optimize.least_squares TRF, three-point numerical Jacobian",
        "status": int(result.status), "message": str(result.message),
        "success": bool(result.success), "function_evaluations": int(result.nfev),
        "jacobian_evaluations": None if result.njev is None else int(result.njev),
        "iteration_proxy": None if result.njev is None else int(result.njev),
        "initial_objective_half_squared_norm": 0.5 * float(initial_residual @ initial_residual),
        "final_objective_half_squared_norm": float(result.cost),
        "final_data_objective_half_squared_norm": 0.5 * float(data_residual(result.x) @ data_residual(result.x)),
        "optimality_inf_norm": float(result.optimality),
        "active_bound_count": int(np.sum(result.active_mask != 0)),
        "step_norm_from_prior": float(np.linalg.norm(result.x)),
    }
    return full, report, data_jacobian


def _state_from_segment_rotations(
    model: BodyModel,
    segment_rotations: Mapping[str, np.ndarray],
    static: StaticCalibration,
    time_ns: int,
) -> KeyframeState:
    root = np.asarray(segment_rotations[model.root_segment], float)
    joints = {}
    for joint in model.joints:
        parent = np.asarray(segment_rotations[joint.parent], float)
        child = np.asarray(segment_rotations[joint.child], float)
        rest = so3_exp(static.vector(joint.rest_rotation_slot, 3))
        joints[joint.joint_id] = so3_log(rest.T @ parent.T @ child)
    zeros_joint = {joint: np.zeros(3) for joint in model.joint_ids}
    zeros_node = {node: np.zeros(3) for node in model.imu_ids}
    dimension = 9 + 6 * len(model.joints) + 6 * len(model.imus)
    return KeyframeState(
        time_s=time_ns * 1e-9,
        root_translation_model_m=np.zeros(3),
        root_rotation_model_rotvec=so3_log(root),
        root_velocity_model_mps=np.zeros(3),
        joint_rotvec=joints,
        joint_rate_rad_s=zeros_joint,
        gyro_bias_rad_s=zeros_node,
        accel_bias_mps2=zeros_node,
        covariance=np.zeros((dimension, dimension)),
    )


def geometry_initialization_from_distances(
    adapter: CanonicalCalibrationAdapter,
    observed_xyz: np.ndarray,
) -> np.ndarray:
    """Classical MDS initializer from real UWB pairwise distances."""
    mean_distances = np.zeros((len(NODES), len(NODES)))
    for left in range(len(NODES)):
        for right in range(left + 1, len(NODES)):
            values = np.linalg.norm(observed_xyz[:, left] - observed_xyz[:, right], axis=1)
            mean_distances[left, right] = mean_distances[right, left] = float(np.mean(values))
    centering = np.eye(len(NODES)) - np.ones((len(NODES), len(NODES))) / len(NODES)
    gram = -0.5 * centering @ (mean_distances ** 2) @ centering
    eigenvalues, eigenvectors = np.linalg.eigh(0.5 * (gram + gram.T))
    order = np.argsort(eigenvalues)[::-1][:3]
    coordinates = eigenvectors[:, order] * np.sqrt(np.maximum(eigenvalues[order], 0.0))
    # Deterministic eigenvector sign gauge.
    for column in range(3):
        pivot = int(np.argmax(np.abs(coordinates[:, column])))
        if coordinates[pivot, column] < 0.0:
            coordinates[:, column] *= -1.0
    positions = {adapter.model.identity_mapping[node]: coordinates[index] for index, node in enumerate(NODES)}
    positions = {segment: value - positions[adapter.model.root_segment]
                 for segment, value in positions.items()}
    full = adapter.prior_mean.copy()
    for joint in adapter.model.joints:
        block = adapter.by_slot[joint.parent_offset_slot]
        full[block.start:block.stop] = positions[joint.child] - positions[joint.parent]
    return full


def solve_geometry_layer(
    adapter: CanonicalCalibrationAdapter,
    model: BodyModel,
    base_vector: np.ndarray,
    frame_times_ns: np.ndarray,
    segment_rotations: np.ndarray,
    observed_xyz: np.ndarray,
    observed_covariance: np.ndarray,
    internal_levers: Mapping[str, np.ndarray],
    internal_lever_sigma: Mapping[str, np.ndarray],
) -> tuple[np.ndarray, dict[str, Any], np.ndarray, list[dict[str, Any]]]:
    coordinates = adapter.geometry_coordinates()
    initial_full = np.asarray(base_vector, float).copy()
    mds = geometry_initialization_from_distances(adapter, observed_xyz)
    initial_full[coordinates] = mds[coordinates]
    x0 = initial_full[coordinates].copy()
    segment_index = {segment: index for index, segment in enumerate(model.segments)}
    pairs = tuple((left, right) for left in range(len(NODES)) for right in range(left + 1, len(NODES)))
    # Geometry coordinates do not include rotations or joint rests, so the
    # dynamic articulated states are invariant throughout this layer.  Validate
    # them once, then reuse the immutable views during finite differences.
    orientation_static = adapter.materialize_static(base_vector, internal_levers=internal_levers)
    states = []
    for frame in range(len(frame_times_ns)):
        rotations = {
            segment: segment_rotations[frame, index]
            for segment, index in segment_index.items()
        }
        states.append(_ValidatedStateView(
            _state_from_segment_rotations(
                model, rotations, orientation_static, int(frame_times_ns[frame])
            ),
            model,
        ))

    def direct_shared_fk_points(local: np.ndarray) -> np.ndarray:
        """Evaluate tag points through the canonical FK for one geometry vector."""
        full = np.asarray(base_vector, float).copy()
        full[coordinates] = local
        static = _CachedStaticCalibrationView(
            adapter.materialize_static(full, internal_levers=internal_levers), model
        )
        points = np.empty((len(frame_times_ns), len(NODES), 3))
        for frame, state in enumerate(states):
            imu_frames = model.imu_frames(state, static)
            points[frame] = np.asarray([
                imu_frames[node].translation + imu_frames[node].rotation @ internal_levers[node]
                for node in NODES
            ])
        return points

    # With rotations fixed in Layer C, canonical FK positions are affine in
    # IMU translations and joint-parent centres.  Compile that exact affine
    # operator by shared-FK evaluations once; this is not a second topology or
    # approximation.  A nontrivial deterministic point verifies equivalence.
    reference_points = direct_shared_fk_points(x0)
    affine_sensitivity = np.empty((*reference_points.shape, len(x0)))
    affine_step = 1e-4
    for column in range(len(x0)):
        candidate = x0.copy(); candidate[column] += affine_step
        affine_sensitivity[..., column] = (
            direct_shared_fk_points(candidate) - reference_points
        ) / affine_step

    def affine_points(local: np.ndarray) -> np.ndarray:
        return reference_points + np.tensordot(
            affine_sensitivity, np.asarray(local, float) - x0, axes=([3], [0])
        )

    check_local = x0 + 0.013 * np.sin(np.arange(len(x0), dtype=float) + 0.5)
    affine_verification_error = float(np.max(np.abs(
        affine_points(check_local) - direct_shared_fk_points(check_local)
    )))

    def unpack(local: np.ndarray) -> np.ndarray:
        full = np.asarray(base_vector, float).copy()
        full[coordinates] = local
        return full

    def data_residual(local: np.ndarray, metadata: bool = False):
        rows: list[float] = []
        detail: list[dict[str, Any]] = []
        predicted_all = affine_points(local)
        for frame in range(len(frame_times_ns)):
            predicted = predicted_all[frame]
            for left, right in pairs:
                observed_delta = observed_xyz[frame, left] - observed_xyz[frame, right]
                predicted_delta = predicted[left] - predicted[right]
                observed_distance = float(np.linalg.norm(observed_delta))
                predicted_distance = float(np.linalg.norm(predicted_delta))
                unit = observed_delta / max(observed_distance, np.finfo(float).tiny)
                variance = float(
                    unit @ observed_covariance[frame, left] @ unit
                    + unit @ observed_covariance[frame, right] @ unit
                    + np.sum(internal_lever_sigma[NODES[left]] ** 2)
                    + np.sum(internal_lever_sigma[NODES[right]] ** 2)
                )
                sigma = np.sqrt(max(variance, np.finfo(float).tiny))
                residual = (predicted_distance - observed_distance) / sigma
                rows.append(residual)
                if metadata:
                    detail.append({
                        "frame": frame, "time_ns": int(frame_times_ns[frame]),
                        "left_node": NODES[left], "right_node": NODES[right],
                        "observed_distance_m": observed_distance,
                        "predicted_distance_m": predicted_distance,
                        "sigma_m": sigma, "normalized_residual": residual,
                    })
        array = np.asarray(rows, dtype=float)
        return (array, detail) if metadata else array

    prior_sigma = adapter.prior_sigma[coordinates]

    def objective(local: np.ndarray) -> np.ndarray:
        return np.concatenate((data_residual(local), local / prior_sigma))

    initial_residual = objective(x0)
    lower = np.empty_like(x0)
    upper = np.empty_like(x0)
    lower[:30], upper[:30] = -0.5, 0.5
    lower[30:], upper[30:] = -1.5, 1.5
    result = least_squares(
        objective, x0, bounds=(lower, upper), method="trf", jac="2-point",
        x_scale="jac", ftol=1e-9, xtol=1e-9, gtol=1e-9, max_nfev=100,
    )
    full = unpack(result.x)
    data_final, detail = data_residual(result.x, metadata=True)
    data_jacobian = numerical_jacobian(data_residual, result.x, relative_step=2e-5)
    report = {
        "optimizer": "scipy.optimize.least_squares bounded TRF, two-point numerical Jacobian",
        "shared_fk_call": (
            "BodyModel.imu_frames -> BodyModel.segment_poses compiled an exact affine Layer-C "
            "operator; no alternative FK/topology"
        ),
        "shared_fk_affine_operator_evaluations": int(len(x0) + 2),
        "shared_fk_affine_equivalence_max_abs_m": affine_verification_error,
        "validation_cache": (
            "each immutable dynamic KeyframeState validated once; each objective calibration view "
            "materialized once; predictions still execute canonical shared FK"
        ),
        "status": int(result.status), "message": str(result.message),
        "success": bool(result.success), "function_evaluations": int(result.nfev),
        "jacobian_evaluations": None if result.njev is None else int(result.njev),
        "iteration_proxy": None if result.njev is None else int(result.njev),
        "initial_objective_half_squared_norm": 0.5 * float(initial_residual @ initial_residual),
        "final_objective_half_squared_norm": float(result.cost),
        "final_data_objective_half_squared_norm": 0.5 * float(data_final @ data_final),
        "optimality_inf_norm": float(result.optimality),
        "active_bound_count": int(np.sum(result.active_mask != 0)),
        "step_norm_from_data_derived_initializer": float(np.linalg.norm(result.x - x0)),
        "step_norm_from_zero_prior": float(np.linalg.norm(result.x)),
        "parameter_bounds": {
            "imu_translation_m": [-0.5, 0.5],
            "joint_parent_m": [-1.5, 1.5],
            "role": "numerical chart safety bounds, not qualification thresholds",
        },
    }
    return full, report, data_jacobian, detail


def numerical_jacobian(function, vector: np.ndarray, relative_step: float = 1e-6) -> np.ndarray:
    value = np.asarray(vector, dtype=float)
    baseline = np.asarray(function(value), dtype=float)
    jacobian = np.empty((len(baseline), len(value)), dtype=float)
    for column in range(len(value)):
        step = relative_step * max(1.0, abs(float(value[column])))
        plus = value.copy(); plus[column] += step
        minus = value.copy(); minus[column] -= step
        jacobian[:, column] = (np.asarray(function(plus)) - np.asarray(function(minus))) / (2.0 * step)
    return jacobian


def information_diagnostics(
    adapter: CanonicalCalibrationAdapter,
    rotation_jacobian: np.ndarray,
    geometry_jacobian: np.ndarray,
) -> tuple[dict[str, Any], np.ndarray]:
    full_jacobian = np.zeros((len(rotation_jacobian) + len(geometry_jacobian), adapter.dimension))
    rotation_coordinates = adapter.rotation_coordinates()
    geometry_coordinates = adapter.geometry_coordinates()
    full_jacobian[:len(rotation_jacobian), rotation_coordinates] = rotation_jacobian
    full_jacobian[len(rotation_jacobian):, geometry_coordinates] = geometry_jacobian
    _, singular_values, right = np.linalg.svd(full_jacobian, full_matrices=False)
    tolerance = max(full_jacobian.shape) * np.finfo(float).eps * singular_values[0]
    rank = int(np.sum(singular_values > tolerance))
    data_information = full_jacobian.T @ full_jacobian
    prior_information = np.diag(1.0 / adapter.prior_sigma ** 2)
    posterior_covariance = np.linalg.inv(data_information + prior_information)
    posterior_covariance = 0.5 * (posterior_covariance + posterior_covariance.T)
    null_directions = []
    for index in range(rank, len(singular_values)):
        direction = right[index]
        largest = np.argsort(np.abs(direction))[::-1][:8]
        null_directions.append({
            "singular_value": float(singular_values[index]),
            "largest_coordinates": [
                {"index": int(item), "label": coordinate_label(adapter, int(item)),
                 "coefficient": float(direction[item])}
                for item in largest
            ],
        })
    report = {
        "dimension": adapter.dimension,
        "data_residual_dimension": int(full_jacobian.shape[0]),
        "data_only_rank": rank,
        "data_only_nullity": adapter.dimension - rank,
        "rank_tolerance": float(tolerance),
        "rank_tolerance_provenance": "numpy default matrix-rank scale: max(shape)*machine_epsilon*sigma_max",
        "singular_values_descending": [float(value) for value in singular_values],
        "null_directions": null_directions,
        "data_plus_bounded_prior_rank": int(np.linalg.matrix_rank(data_information + prior_information)),
        "posterior_covariance_finite": bool(np.isfinite(posterior_covariance).all()),
        "posterior_covariance_symmetric": bool(np.allclose(posterior_covariance, posterior_covariance.T, atol=1e-12)),
        "posterior_covariance_min_eigenvalue": float(np.linalg.eigvalsh(posterior_covariance)[0]),
    }
    return report, posterior_covariance


def coordinate_label(adapter: CanonicalCalibrationAdapter, index: int) -> str:
    for block in adapter.blocks:
        if block.start <= index < block.stop:
            offset = index - block.start
            if block.kind == "imu_extrinsic":
                component = ("rx", "ry", "rz", "tx", "ty", "tz")[offset]
            else:
                component = ("x", "y", "z")[offset]
            return f"{block.slot_id}:{component}"
    raise IndexError(index)


def derive_bone_lengths(adapter: CanonicalCalibrationAdapter, vector: np.ndarray) -> dict[str, dict[str, Any]]:
    slots = adapter.vector_to_slots(vector)
    mapping = {
        "upper_arm_left": "joint_parent:elbow_left",
        "upper_arm_right": "joint_parent:elbow_right",
        "thigh_left": "joint_parent:knee_left",
        "thigh_right": "joint_parent:knee_right",
    }
    rows = {}
    for segment in (
        "forearm_left", "forearm_right", "shank_left", "shank_right",
        "thigh_left", "thigh_right", "upper_arm_left", "upper_arm_right",
    ):
        parent_slot = mapping.get(segment)
        if parent_slot is None:
            rows[segment] = {
                "value_m": None, "status": "AWAITING_DIRECT_DISTAL_LANDMARK",
                "independent_freedom": False,
            }
        else:
            rows[segment] = {
                "value_m": float(np.linalg.norm(slots[parent_slot])),
                "status": "DERIVED_FROM_ESTIMATED_JOINT_PARENT",
                "source_slot": parent_slot, "independent_freedom": False,
            }
    return rows
