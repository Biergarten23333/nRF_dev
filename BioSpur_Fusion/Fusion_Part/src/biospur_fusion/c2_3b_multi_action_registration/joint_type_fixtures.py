"""Approved tiny joint-type fixtures composed from OpenSim, QMT, and IMT."""

from __future__ import annotations

from contextlib import redirect_stdout
from dataclasses import dataclass
import hashlib
import io
import json
import math
from pathlib import Path
import time
from typing import Any, Iterable

import numpy as np
import qmt

from .joint_type_runtime import PinnedImtRuntime, RuntimeBound
from .synthetic_axis_stage import (
    DEPENDENT_COORDINATES,
    DT,
    FRAME_TOLERANCE,
    NODE_ORDER,
    ROOT_TRANSLATIONS,
    STATE_TOLERANCE,
    TRUTH_LINE_TOLERANCE,
    OfficialSyntheticState,
    SeedParameters,
    _canonical_plane,
    _checkpoint_seed_parameters,
    _coordinate_values,
    _edge_specs,
    _linear_acceleration,
    _line_angle,
    _matrix,
    _quantize_decode,
    _rng,
    _rotation_about,
    _rotation_distance,
    _seed_parameters,
    _vector,
)


class CountedOfficialState(OfficialSyntheticState):
    """The R5 lifecycle without an unowned setup AssemblySolver call."""

    def __init__(self, osim: Any, model: Any, enabled: tuple[str, ...]) -> None:
        self.osim = osim
        self.model = model
        self.state = model.initSystem()
        self.coordinates = model.updCoordinateSet()
        self.enabled = enabled
        self.reference_order = enabled + ROOT_TRANSLATIONS
        self.defaults = {
            self.coordinates.get(index).getName():
            float(self.coordinates.get(index).getDefaultValue())
            for index in range(self.coordinates.getSize())
        }
        if len(self.defaults) != self.coordinates.getSize():
            raise ValueError("duplicate official coordinate name")
        for name in self.reference_order + DEPENDENT_COORDINATES:
            if name not in self.defaults:
                raise ValueError(f"required official coordinate absent: {name}")
        if abs(float(model.get_assembly_accuracy()) - 1e-9) > np.finfo(float).eps:
            raise ValueError("official model assembly_accuracy changed")
        writable = set(self.reference_order)
        for name in self.defaults:
            if name in DEPENDENT_COORDINATES:
                continue
            self.coordinates.get(name).setLocked(self.state, name not in writable)
        self._assert_policy()


def _canonical_json_hash(value: Any) -> str:
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class StateSnapshot:
    key: str
    explicit_values: tuple[tuple[str, float], ...]
    audit: dict[str, Any]
    transforms: dict[str, tuple[np.ndarray, np.ndarray]]


class OfficialStateCache:
    """Evaluate exact explicit states once and cache Position body transforms."""

    def __init__(
        self,
        owner: CountedOfficialState,
        body_names: Iterable[str],
        dependency_hashes: dict[str, str],
        deadline: float,
    ) -> None:
        self.owner = owner
        self.body_names = tuple(body_names)
        self.dependency_hashes = dependency_hashes
        self.deadline = deadline
        self.rows: dict[str, StateSnapshot] = {}
        self.assembly_calls = 0
        self.realize_position_calls = 0
        self.cache_hits = 0

    def key(self, explicit_values: Iterable[tuple[str, float]]) -> str:
        pairs = tuple((name, float(value)) for name, value in explicit_values)
        payload = {
            **self.dependency_hashes,
            "coordinate_name_value_pairs_binary64": [
                [name, np.float64(value).tobytes().hex()] for name, value in pairs
            ],
            "constraint_policy": "R5_BOTH_KNEE_COUPLERS_ENFORCED",
            "realization_stage": "Position",
        }
        return _canonical_json_hash(payload)

    def evaluate(
        self,
        explicit_values: Iterable[tuple[str, float]],
        *,
        allow_cache: bool = False,
    ) -> StateSnapshot:
        if time.monotonic() > self.deadline:
            raise RuntimeBound("OFFICIAL_STATE_RUNTIME_BOUND")
        pairs = tuple((name, float(value)) for name, value in explicit_values)
        key = self.key(pairs)
        if key in self.rows:
            if not allow_cache:
                raise RuntimeBound("UNDECLARED_OFFICIAL_STATE_DUPLICATE")
            self.cache_hits += 1
            return self.rows[key]
        values = dict(pairs)
        audit = self.owner.apply(values)
        self.assembly_calls += 1
        self.realize_position_calls += 1
        if not audit["pass"]:
            raise RuntimeBound(f"OFFICIAL_STATE_ASSERTION: {audit}")
        transforms: dict[str, tuple[np.ndarray, np.ndarray]] = {}
        for body_name in self.body_names:
            transform = self.owner.model.getBodySet().get(body_name).getTransformInGround(
                self.owner.state
            )
            transforms[body_name] = (_matrix(transform.R()), _vector(transform.p()))
        snapshot = StateSnapshot(key, pairs, audit, transforms)
        self.rows[key] = snapshot
        return snapshot


def _angular_velocity_dt(rotation: np.ndarray, dt: float) -> np.ndarray:
    result = np.empty((len(rotation), 3), dtype=np.float64)
    result[0] = np.asarray(
        qmt.quatToRotVec(qmt.quatFromRotMat(rotation[0].T @ rotation[1])),
        dtype=np.float64,
    ) / dt
    result[-1] = np.asarray(
        qmt.quatToRotVec(qmt.quatFromRotMat(rotation[-2].T @ rotation[-1])),
        dtype=np.float64,
    ) / dt
    relative = np.transpose(rotation[:-2], (0, 2, 1)) @ rotation[2:]
    result[1:-1] = np.asarray(
        qmt.quatToRotVec(qmt.quatFromRotMat(relative)), dtype=np.float64,
    ) / (2 * dt)
    return result


def _linear_acceleration_dt(point: np.ndarray, dt: float) -> np.ndarray:
    result = np.empty_like(point)
    result[1:-1] = (point[2:] - 2 * point[1:-1] + point[:-2]) / dt**2
    result[0] = (point[2] - 2 * point[1] + point[0]) / dt**2
    result[-1] = (point[-1] - 2 * point[-2] + point[-3]) / dt**2
    return result


def _legacy_state_hash() -> str:
    name, keys, position, has_gauss, cached = np.random.get_state()
    payload = (
        name.encode("ascii") + np.asarray(keys, dtype="<u4").tobytes()
        + int(position).to_bytes(8, "little")
        + int(has_gauss).to_bytes(1, "little")
        + np.float64(cached).tobytes()
    )
    return hashlib.sha256(payload).hexdigest()


def _qmt_termination(native: dict[str, Any]) -> dict[str, Any]:
    xtraj = np.asarray(native["xtraj"], dtype=np.float64)
    ftraj = np.asarray(native["ftraj"], dtype=np.float64)
    if xtraj.shape != (4, 300):
        raise RuntimeBound("QMT_TERMINATION_RECONSTRUCTION: xtraj shape")
    finite_col = np.all(np.isfinite(xtraj), axis=0)
    nan_col = np.all(np.isnan(xtraj), axis=0)
    if not np.all(finite_col | nan_col):
        raise RuntimeBound("QMT_TERMINATION_RECONSTRUCTION: partial column")
    step_count = int(np.sum(finite_col))
    expected = np.arange(300) < step_count
    if not np.array_equal(finite_col, expected) or not 2 <= step_count <= 299:
        raise RuntimeBound("QMT_TERMINATION_RECONSTRUCTION: column pattern")
    if ftraj.shape != (step_count + 1, 1) or not np.all(np.isfinite(ftraj)):
        raise RuntimeBound("QMT_TERMINATION_RECONSTRUCTION: ftraj")
    previous = float(ftraj[step_count - 2, 0])
    final_update = float(ftraj[step_count - 1, 0])
    recomputed = float(ftraj[step_count, 0])
    diff = abs(previous - final_update)
    hit_max = step_count == 299
    tol_satisfied = diff <= 1e-5
    classification = {
        (False, True): "TOL_BEFORE_MAX",
        (True, False): "MAX_STEPS_ONLY",
        (True, True): "MAX_STEPS_AND_TOL_SAME_FINAL_ITERATION",
        (False, False): "QMT_TERMINATION_RECONSTRUCTION",
    }[(hit_max, tol_satisfied)]
    if classification == "QMT_TERMINATION_RECONSTRUCTION":
        raise RuntimeBound(classification)
    hessian = np.asarray(native["Hessian"], dtype=np.float64)
    return {
        "step_count": step_count,
        "finite_col": finite_col.tolist(),
        "leading_finite_count": step_count,
        "trailing_nan_count": 300 - step_count,
        "ftraj_length": int(len(ftraj)),
        "prefinal_update_cost_previous": previous,
        "prefinal_update_cost_final": final_update,
        "stopping_diff": diff,
        "terminal_recomputed_cost": recomputed,
        "terminal_recompute_delta_report_only": abs(recomputed - final_update),
        "hit_max": hit_max,
        "tol_satisfied": tol_satisfied,
        "reconstructed_termination": classification,
        "f0": float(np.asarray(native["f0"]).reshape(-1)[0]),
        "f": float(np.asarray(native["f"]).reshape(-1)[0]),
        "hessian_finite": bool(np.all(np.isfinite(hessian))),
        "hessian_rank": int(np.linalg.matrix_rank(hessian)),
    }


def generate_jtf01(
    cache: OfficialStateCache,
    model: Any,
    approved: dict[str, Any],
    r2_synthetic: dict[str, Any],
    r2_static: dict[str, Any],
    r2_mechanism: dict[str, Any],
    deadline: float,
) -> tuple[dict[str, np.ndarray], SeedParameters, dict[str, Any]]:
    started = time.monotonic()
    edge = next(spec for spec in _edge_specs(r2_mechanism, r2_static) if spec.name == "elbow_left")
    early = _checkpoint_seed_parameters(101, approved["donning"])
    shim = type("EarlyParameters", (), early)()
    episode = edge.episode
    body_map = r2_static["body_by_segment"]
    parent_body = body_map[edge.parent]
    child_body = body_map[edge.child]
    body_rotation = {
        edge.parent: np.empty((1601, 3, 3), dtype=np.float64),
        edge.child: np.empty((1601, 3, 3), dtype=np.float64),
    }
    body_origin = {
        edge.parent: np.empty((1601, 3), dtype=np.float64),
        edge.child: np.empty((1601, 3), dtype=np.float64),
    }
    audits: list[dict[str, Any]] = []
    for row in range(1601):
        if time.monotonic() > deadline:
            raise RuntimeBound("JTF01_RUNTIME_BOUND")
        values = _coordinate_values(r2_synthetic, shim, episode, row * DT)
        pairs = tuple((name, values[name]) for name in values)
        snapshot = cache.evaluate(pairs)
        audits.append(snapshot.audit)
        for segment, body in ((edge.parent, parent_body), (edge.child, child_body)):
            body_rotation[segment][row], body_origin[segment][row] = snapshot.transforms[body]
        if row == 0:
            parameters = _seed_parameters(
                101, model, cache.owner.state, r2_synthetic, r2_static, approved["donning"]
            )
            if (
                not np.array_equal(parameters.coordinate_phase, early["coordinate_phase"])
                or parameters.phase_tx != early["phase_tx"]
                or parameters.phase_ty != early["phase_ty"]
                or parameters.phase_tz != early["phase_tz"]
            ):
                raise RuntimeBound("R5_RNG_REPLAY_MISMATCH")
    assert isinstance(parameters, SeedParameters)
    official_rotation = {name: value.copy() for name, value in body_rotation.items()}
    oop_rows = r2_synthetic["model_truth"]["off_model_human_variability"]["perturbations"]
    oop_by_body = {
        item[1]: (np.asarray(item[2], dtype=np.float64), float(item[3]), float(item[4]), index)
        for index, item in enumerate(oop_rows)
    }
    for segment, body in ((edge.parent, parent_body), (edge.child, child_body)):
        if body not in oop_by_body:
            continue
        axis, amplitude_deg, frequency, phase_index = oop_by_body[body]
        for row in range(1601):
            angle = math.radians(amplitude_deg) * math.sin(
                2 * math.pi * frequency * row * DT + float(parameters.oop_phase[phase_index])
            )
            body_rotation[segment][row] = body_rotation[segment][row] @ _rotation_about(axis, angle)
    signals: dict[str, dict[str, np.ndarray]] = {}
    saturated = False
    frame_inverse = 0.0
    frame_quaternion = 0.0
    frame_axis = 0.0
    for segment in (edge.parent, edge.child):
        node_index = NODE_ORDER.index(segment)
        mount = parameters.mounts_base[segment]
        sensor_rotation = body_rotation[segment] @ mount
        sensor_point = body_origin[segment] + np.einsum(
            "nij,j->ni", body_rotation[segment], parameters.locations[segment]
        )
        force_o = _linear_acceleration(sensor_point) - np.array([0.0, -9.80665, 0.0])
        ideal_acc = np.einsum("nji,nj->ni", sensor_rotation, force_o)
        ideal_gyr = _angular_velocity_dt(sensor_rotation, DT)
        first = episode * 6401
        acc = (
            ideal_acc + parameters.acc_bias[node_index, :, first:first + 1601].T
            + parameters.acc_noise[episode, node_index, :1601]
        )
        gyr = (
            ideal_gyr + parameters.gyro_bias[node_index, :, first:first + 1601].T
            + parameters.gyro_noise[episode, node_index, :1601]
        )
        acc, acc_saturated = _quantize_decode(acc, 9.80665 / 2048.0)
        gyr, gyr_saturated = _quantize_decode(gyr, math.radians(1.0 / 16.384))
        saturated = saturated or acc_saturated or gyr_saturated
        signals[segment] = {
            "acc": np.ascontiguousarray(acc[:1600]),
            "gyr": np.ascontiguousarray(gyr[:1600]),
        }
        for row in range(1601):
            r_ob = official_rotation[segment][row]
            r_os = r_ob @ mount
            recovered = r_ob.T @ r_os
            quaternion = np.asarray(qmt.quatFromRotMat(r_os), dtype=np.float64)
            frame_inverse = max(frame_inverse, float(np.linalg.norm(recovered - mount)))
            frame_quaternion = max(
                frame_quaternion,
                float(np.linalg.norm(np.asarray(qmt.quatToRotMat(quaternion)) - r_os)),
            )
            target = edge.parent_target if segment == edge.parent else edge.child_target
            frame_axis = max(
                frame_axis,
                float(np.linalg.norm(np.asarray(qmt.rotate(quaternion, mount.T @ target)) - r_ob @ target)),
            )
    hinge = [
        math.acos(float(np.clip(abs(
            (official_rotation[edge.parent][row] @ edge.parent_target)
            @ (official_rotation[edge.child][row] @ edge.child_target)
        ), 0.0, 1.0)))
        for row in range(1601)
    ]
    metadata = {
        "generation_wall_s": time.monotonic() - started,
        "state_rows": 1601,
        "state_pass": all(row["pass"] for row in audits),
        "maximum_requested_error": max(float(row["requested_error"]) for row in audits),
        "maximum_locked_error": max(float(row["locked_error"]) for row in audits),
        "maximum_coupling_error": max(float(row["coupling_error"]) for row in audits),
        "frame_inverse_fro_max": frame_inverse,
        "frame_quaternion_fro_max": frame_quaternion,
        "frame_axis_l2_max": frame_axis,
        "hinge_relation_max_rad": max(hinge),
        "saturated": saturated,
    }
    return {
        "acc1": signals[edge.parent]["acc"],
        "gyr1": signals[edge.parent]["gyr"],
        "acc2": signals[edge.child]["acc"],
        "gyr2": signals[edge.child]["gyr"],
    }, parameters, metadata


def run_jtf01(
    inputs: dict[str, np.ndarray],
    parameters: SeedParameters,
    metadata: dict[str, Any],
    r2_static: dict[str, Any],
    deadline: float,
) -> dict[str, Any]:
    if time.monotonic() > deadline:
        raise RuntimeBound("JTF01_RUNTIME_BOUND")
    np.random.seed(101)
    rng_before = _legacy_state_hash()
    settings = {
        "w0": 50.0,
        "wa": 0.1414213562373095,
        "wg": 7.0710678118654755,
        "useSampleSelection": False,
        "x0": np.asarray([0, 0, 0, 0], dtype=np.float64),
        "tol": 1e-5,
        "maxSteps": 299,
        "alpha": 0.4,
        "beta": 0.5,
    }
    started = time.monotonic()
    with redirect_stdout(io.StringIO()) as captured:
        result = qmt.jointAxisEstHingeOlsson(
            inputs["acc1"], inputs["acc2"], inputs["gyr1"], inputs["gyr2"],
            estSettings=settings, debug=True, plot=False,
        )
    qmt_wall = time.monotonic() - started
    rng_after = _legacy_state_hash()
    if time.monotonic() > deadline:
        raise RuntimeBound("JTF01_RUNTIME_BOUND")
    parent = np.asarray(result[0], dtype=np.float64)
    child = np.asarray(result[1], dtype=np.float64)
    if parent.shape != (3, 1) or child.shape != (3, 1):
        raise RuntimeBound("JTF01_AXIS_SHAPE")
    debug = result[2]
    native = debug["optimVarsAxis"]
    terminations = {
        "chosen": _qmt_termination(native),
        "flip": _qmt_termination(native["flip"]),
    }
    axis_row = r2_static["functional_axis_body_targets"]["rows"]["elbow_left"]
    truth_parent = parameters.mounts_base["upper_arm_left"].T @ np.asarray(
        axis_row["parent_body_target"], dtype=np.float64
    )
    truth_child = parameters.mounts_base["forearm_left"].T @ np.asarray(
        axis_row["child_body_target"], dtype=np.float64
    )
    truth_errors = [
        _line_angle(parent[:, 0], truth_parent),
        _line_angle(child[:, 0], truth_child),
    ]
    unit_errors = [abs(float(np.linalg.norm(parent)) - 1), abs(float(np.linalg.norm(child)) - 1)]
    total_wall = metadata["generation_wall_s"] + qmt_wall
    pass_value = bool(
        metadata["state_pass"]
        and metadata["frame_inverse_fro_max"] <= FRAME_TOLERANCE
        and metadata["frame_quaternion_fro_max"] <= FRAME_TOLERANCE
        and metadata["frame_axis_l2_max"] <= FRAME_TOLERANCE
        and metadata["hinge_relation_max_rad"] <= TRUTH_LINE_TOLERANCE
        and not metadata["saturated"]
        and max(unit_errors) <= 1e-10
        and max(truth_errors) <= TRUTH_LINE_TOLERANCE
        and total_wall <= 50.0
    )
    return {
        "id": "JTF01_PIN_ELBOW_LEFT",
        "pass": pass_value,
        "first_gate": None if pass_value else "JTF01_PIN_ELBOW_TRUTH_OR_RUNTIME",
        "dimensions": {"official_state_rows": 1601, "input_rows": 1600, "axis_parameters": 4},
        "calls": {"qmt_public": 1, "qmt_internal_gauss_newton_branches": 2},
        "generation": metadata,
        "qmt_wall_s": qmt_wall,
        "wall_s": total_wall,
        "wall_limit_s": 50.0,
        "parent_axis_S": parent[:, 0].tolist(),
        "child_axis_S": child[:, 0].tolist(),
        "parent_truth_axis_S": truth_parent.tolist(),
        "child_truth_axis_S": truth_child.tolist(),
        "truth_line_error_rad": truth_errors,
        "truth_line_bound_rad": TRUTH_LINE_TOLERANCE,
        "unit_error": unit_errors,
        "rng_state_sha256_before": rng_before,
        "rng_state_sha256_after": rng_after,
        "captured_stdout_sha256": hashlib.sha256(captured.getvalue().encode()).hexdigest(),
        "terminations": terminations,
        "debug_fields": sorted(debug.keys()),
    }


def _joint_point_in_body(model: Any, state: Any, body: str, frame_path: str) -> np.ndarray:
    body_transform = model.getBodySet().get(body).getTransformInGround(state)
    frame_transform = model.getComponent(frame_path).getTransformInGround(state)
    r_ob = _matrix(body_transform.R())
    return r_ob.T @ (_vector(frame_transform.p()) - _vector(body_transform.p()))


def generate_official_control_and_center(
    cache: OfficialStateCache,
    model: Any,
    parameters: SeedParameters,
    r2_synthetic: dict[str, Any],
    knee_contract: dict[str, Any],
    deadline: float,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, np.ndarray], dict[str, np.ndarray]]:
    back = knee_contract["positive_control"]
    back_snapshots: list[StateSnapshot] = []
    back_started = time.monotonic()
    for values in back["states_rad"]:
        pairs = tuple(zip(back["coordinate_order"], values, strict=True))
        back_snapshots.append(cache.evaluate(pairs))
    back_generation_wall = time.monotonic() - back_started

    knee = knee_contract["negative_witness"]
    knee_started = time.monotonic()
    knee_snapshots = [
        cache.evaluate(((knee["coordinate"], value),)) for value in knee["q_rad"]
    ]
    knee_generation_wall = time.monotonic() - knee_started

    # JTF03 must consume the JTF02-owned control rows, never execute them again.
    control_replay = [
        cache.evaluate(tuple(zip(back["coordinate_order"], values, strict=True)), allow_cache=True)
        for values in back["states_rad"]
    ]
    if [row.key for row in control_replay] != [row.key for row in back_snapshots]:
        raise RuntimeBound("OFFICIAL_STATE_CACHE_REUSE")

    center_started = time.monotonic()
    rotations = {"pelvis": np.empty((1001, 3, 3)), "torso": np.empty((1001, 3, 3))}
    origins = {"pelvis": np.empty((1001, 3)), "torso": np.empty((1001, 3))}
    audits: list[dict[str, Any]] = []
    joint_points: dict[str, np.ndarray] = {}
    for index in range(1001):
        if time.monotonic() > deadline:
            raise RuntimeBound("JTF04_RUNTIME_BOUND")
        t = index * 0.01
        values = _coordinate_values(r2_synthetic, parameters, 13, t)
        snapshot = cache.evaluate(tuple((name, values[name]) for name in values))
        audits.append(snapshot.audit)
        for body in ("pelvis", "torso"):
            rotations[body][index], origins[body][index] = snapshot.transforms[body]
        if index == 0:
            joint_points = {
                "pelvis": _joint_point_in_body(model, cache.owner.state, "pelvis", "/jointset/back/pelvis_offset"),
                "torso": _joint_point_in_body(model, cache.owner.state, "torso", "/jointset/back/torso_offset"),
            }
    center_generation_wall = time.monotonic() - center_started
    imu: dict[str, np.ndarray] = {}
    truth: dict[str, np.ndarray] = {}
    for label, body in (("1", "pelvis"), ("2", "torso")):
        mount = parameters.mounts_base[body]
        r_os = rotations[body] @ mount
        point = origins[body] + np.einsum(
            "nij,j->ni", rotations[body], parameters.locations[body]
        )
        acceleration = _linear_acceleration_dt(point, 0.01)
        force_o = acceleration - np.array([0.0, -9.80665, 0.0])
        imu[f"acc{label}"] = np.ascontiguousarray(
            np.einsum("nji,nj->ni", r_os, force_o), dtype=np.float64
        )
        imu[f"gyr{label}"] = np.ascontiguousarray(
            _angular_velocity_dt(r_os, 0.01), dtype=np.float64
        )
        truth[f"r{label}"] = mount.T @ (
            parameters.locations[body] - joint_points[body]
        )
    return (
        {
            "snapshots": back_snapshots,
            "generation_wall_s": back_generation_wall,
        },
        {
            "control_snapshots": control_replay,
            "knee_snapshots": knee_snapshots,
            "generation_wall_s": knee_generation_wall,
        },
        imu,
        {
            **truth,
            "generation_wall_s": np.asarray(center_generation_wall),
            "state_pass": np.asarray(all(row["pass"] for row in audits)),
            "maximum_requested_error": np.asarray(max(float(row["requested_error"]) for row in audits)),
            "maximum_locked_error": np.asarray(max(float(row["locked_error"]) for row in audits)),
            "maximum_coupling_error": np.asarray(max(float(row["coupling_error"]) for row in audits)),
        },
    )


def _constant_point(
    snapshots: list[StateSnapshot], parent: str, child: str,
) -> dict[str, Any]:
    blocks = []
    targets = []
    for row in snapshots:
        r_op, p_op = row.transforms[parent]
        r_oc, p_oc = row.transforms[child]
        blocks.append(np.column_stack((r_op, -r_oc)))
        targets.append(p_oc - p_op)
    a = np.vstack(blocks)
    b = np.concatenate(targets)
    u, singular, vt = np.linalg.svd(a, full_matrices=False)
    tau_s = max(a.shape) * np.finfo(np.float64).eps * singular[0]
    rank = int(np.sum(singular > tau_s))
    inverse = np.zeros_like(singular)
    inverse[singular > tau_s] = 1.0 / singular[singular > tau_s]
    x_hat = vt.T @ (inverse * (u.T @ b))
    residual = np.asarray([
        np.linalg.norm(block @ x_hat - target)
        for block, target in zip(blocks, targets, strict=True)
    ])
    condition = float(singular[0] / singular[-1]) if rank == 6 else math.inf
    eps = np.finfo(np.float64).eps
    tau_r = (
        4096 * eps * max(a.shape)
        * max(1.0, float(np.linalg.norm(b, np.inf)), float(np.linalg.norm(a, np.inf) * np.linalg.norm(x_hat, np.inf)))
        * max(1.0, condition)
    )
    return {
        "rank": rank,
        "rank_tolerance": float(tau_s),
        "singular_values": singular.tolist(),
        "condition": condition,
        "solution_m": x_hat.tolist(),
        "residual_per_state_m": residual.tolist(),
        "maximum_residual_m": float(np.max(residual)),
        "numerical_tolerance_m": float(tau_r),
    }


def run_jtf02_jtf03(
    jtf02_cache: dict[str, Any],
    jtf03_cache: dict[str, Any],
    official_mechanics: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    joint_rows = official_mechanics["joints"]
    back_joint = next(row for row in joint_rows if row["name"] == "back")
    translations = [
        row for row in back_joint["mechanics"]["axes"] if row["slot"].startswith("translation")
    ]
    translation_zero = all(
        row["function"]["type"] == "MultiplierFunction"
        and row["function"]["inner"]["type"] == "Constant"
        and float(row["function"]["inner"]["value"]) == 0.0
        for row in translations
    )
    back = _constant_point(jtf02_cache["snapshots"], "pelvis", "torso")
    jtf02_pass = bool(
        translation_zero and back["rank"] == 6
        and back["maximum_residual_m"] <= back["numerical_tolerance_m"]
    )
    jtf02 = {
        "id": "JTF02_BACK_CUSTOMJOINT_3DOF",
        "pass": jtf02_pass,
        "first_gate": None if jtf02_pass else "FIXED_CENTER_POSITIVE_CONTROL",
        "decision": "REJECT_FIXED_LINE",
        "official_state_rows": 9,
        "official_calls": 9,
        "generation_wall_s": jtf02_cache["generation_wall_s"],
        "translation_functions_exact_zero": translation_zero,
        "constant_point_control": back,
    }
    control_keys = [row.key for row in jtf03_cache["control_snapshots"]]
    owner_keys = [row.key for row in jtf02_cache["snapshots"]]
    knee = _constant_point(jtf03_cache["knee_snapshots"], "femur_l", "tibia_l")
    jtf03_pass = bool(
        control_keys == owner_keys
        and back["rank"] == 6
        and back["maximum_residual_m"] <= back["numerical_tolerance_m"]
        and knee["rank"] == 6
        and knee["maximum_residual_m"] > knee["numerical_tolerance_m"]
    )
    jtf03 = {
        "id": "JTF03_WALKER_KNEE_LEFT_CONSTANT_POINT",
        "pass": jtf03_pass,
        "first_gate": None if jtf03_pass else "WALKER_KNEE_FIXED_CENTER_NOT_REJECTED",
        "official_new_state_rows": 13,
        "official_calls": 13,
        "control_cache_hits": 9,
        "control_cache_keys_equal": control_keys == owner_keys,
        "generation_wall_s": jtf03_cache["generation_wall_s"],
        "back_positive_control": back,
        "walker_knee": knee,
        "decision": "REJECT_FIXED_AXIS_AND_FIXED_CENTER",
    }
    return jtf02, jtf03


def write_imt_input(path: Path, inputs: dict[str, np.ndarray]) -> None:
    np.savez(path, **{key: np.ascontiguousarray(value, dtype=np.float64) for key, value in inputs.items()})


def run_rtp01(
    runtime: PinnedImtRuntime,
    input_path: Path,
    stage_deadline: float,
) -> dict[str, Any]:
    started = time.monotonic()
    call = runtime.call_imt(input_path, stage_deadline)
    elapsed = time.monotonic() - started
    pass_value = bool(
        call["public_vs_third_parent_m"] <= 1e-12
        and call["public_vs_third_child_m"] <= 1e-12
        and all(call["array_c_contiguous"].values())
        and set(call["array_dtypes"].values()) == {"float64"}
        and elapsed <= 25.0
    )
    return {
        "id": "RTP01_IMT_100_ROW_TIMING_PREFIX",
        "pass": pass_value,
        "first_gate": None if pass_value else "IMT_SOURCE_OR_API",
        "dimensions": {"rows": 100, "arrays": 4, "shape": [100, 3], "unknowns": 6},
        "calls": {"imt_public": 1, "imt_internal_bfgs": 3, "imt_private_bfgs": 3},
        "wall_s": elapsed,
        "wall_limit_s": 25.0,
        "call": call,
    }


def _vector_metrics(estimate: np.ndarray, truth: np.ndarray) -> dict[str, Any]:
    if not np.linalg.norm(truth) or not np.linalg.norm(estimate):
        return {"finite": False, "directed_angle_rad": math.inf, "absolute_error_m": math.inf, "relative_error": math.inf}
    return {
        "finite": bool(np.all(np.isfinite(estimate))),
        "directed_angle_rad": math.acos(float(np.clip(
            estimate @ truth / (np.linalg.norm(estimate) * np.linalg.norm(truth)), -1.0, 1.0
        ))),
        "absolute_error_m": float(np.linalg.norm(estimate - truth)),
        "relative_error": float(np.linalg.norm(estimate - truth) / np.linalg.norm(truth)),
    }


def run_jtf04(
    runtime: PinnedImtRuntime,
    input_path: Path,
    truth: dict[str, np.ndarray],
    sensitivity: dict[str, Any],
    deadline: float,
) -> dict[str, Any]:
    started = time.monotonic()
    calls = [runtime.call_imt(input_path, deadline), runtime.call_imt(input_path, deadline)]
    repeats = {
        "parent_m": float(np.linalg.norm(
            np.asarray(calls[0]["public_parent_m"]) - np.asarray(calls[1]["public_parent_m"])
        )),
        "child_m": float(np.linalg.norm(
            np.asarray(calls[0]["public_child_m"]) - np.asarray(calls[1]["public_child_m"])
        )),
    }
    metrics = {
        "parent": _vector_metrics(np.asarray(calls[0]["public_parent_m"]), truth["r1"]),
        "child": _vector_metrics(np.asarray(calls[0]["public_child_m"]), truth["r2"]),
    }
    profile_rows = []
    for profile in sensitivity["all_must_pass_sensitivity_family"]:
        passed = all(
            row["absolute_error_m"] <= profile["absolute_error_max_m"]
            and row["relative_error"] <= profile["relative_error_max"]
            for row in metrics.values()
        )
        profile_rows.append({**profile, "pass": passed})
    pass_value = bool(
        repeats["parent_m"] <= 1e-12
        and repeats["child_m"] <= 1e-12
        and all(row["finite"] and row["directed_angle_rad"] <= TRUTH_LINE_TOLERANCE for row in metrics.values())
        and all(row["pass"] for row in profile_rows)
        and all(
            call["public_vs_third_parent_m"] <= 1e-12
            and call["public_vs_third_child_m"] <= 1e-12
            for call in calls
        )
        and time.monotonic() <= deadline
    )
    return {
        "id": "JTF04_SEEL_FIXED_CENTER_POSITIVE",
        "pass": pass_value,
        "first_gate": None if pass_value else "SEEL_FIXED_CENTER_TRUTH",
        "dimensions": {"official_state_rows": 1001, "input_rows": 1001, "unknowns": 6},
        "calls": {"imt_public": 2, "imt_internal_bfgs": 6, "imt_private_bfgs": 6},
        "wall_s": float(truth["generation_wall_s"]) + time.monotonic() - started,
        "wall_limit_s": 60.0,
        "state_pass": bool(truth["state_pass"]),
        "state_errors": {
            "requested": float(truth["maximum_requested_error"]),
            "locked": float(truth["maximum_locked_error"]),
            "coupling": float(truth["maximum_coupling_error"]),
        },
        "truth_vectors_m": {"parent": truth["r1"].tolist(), "child": truth["r2"].tolist()},
        "repeatability": repeats,
        "truth_metrics": metrics,
        "sensitivity_profiles": profile_rows,
        "public_runs": calls,
    }


def run_jtf05() -> dict[str, Any]:
    acc1 = np.ascontiguousarray(np.tile([0.0, 9.80665, 0.0], (1001, 1)), dtype=np.float64)
    acc2 = acc1.copy()
    gyr1 = np.ascontiguousarray(np.zeros((1001, 3)), dtype=np.float64)
    gyr2 = gyr1.copy()
    excitation = max(
        float(np.ptp(gyr1, axis=0).max()), float(np.ptp(gyr2, axis=0).max())
    )
    passed = excitation == 0.0
    return {
        "id": "JTF05_SEEL_STATIONARY_DEGENERACY",
        "pass": passed,
        "first_gate": "SEEL_INSUFFICIENT_EXCITATION",
        "dimensions": {"rows": 1001, "would_be_unknowns": 6},
        "maximum_gyroscope_span_rad_s": excitation,
        "imt_calls": 0,
        "scipy_calls": 0,
        "output_vector": None,
    }


def _skew(vector: np.ndarray) -> np.ndarray:
    x, y, z = vector
    return np.array([[0.0, -z, y], [z, 0.0, -x], [-y, x, 0.0]])


def run_jtf06() -> dict[str, Any]:
    t = np.arange(1001, dtype=np.float64) / 100.0
    gyr1 = np.ascontiguousarray(np.column_stack((
        0.7 * np.sin(2 * math.pi * 0.31 * t),
        0.5 * np.cos(2 * math.pi * 0.23 * t + 0.2),
        0.4 * np.sin(2 * math.pi * 0.17 * t - 0.3),
    )))
    acc1 = np.ascontiguousarray(np.column_stack((
        1.1 * np.sin(2 * math.pi * 0.19 * t),
        9.80665 + 0.8 * np.cos(2 * math.pi * 0.29 * t),
        0.6 * np.sin(2 * math.pi * 0.37 * t + 0.4),
    )))
    gyr2 = gyr1.copy()
    acc2 = acc1.copy()
    gyrdot = np.empty_like(gyr1)
    gyrdot[1:-1] = (gyr1[2:] - gyr1[:-2]) / 0.02
    gyrdot[0] = gyrdot[1]
    gyrdot[-1] = gyrdot[-2]
    design = np.empty((1001, 6), dtype=np.float64)
    gamma_rows: list[np.ndarray] = []
    for index in range(1001):
        gamma = _skew(gyr1[index]) @ _skew(gyr1[index]) + _skew(gyrdot[index])
        gamma_rows.append(gamma)
        direction = acc1[index] / np.linalg.norm(acc1[index])
        left = -direction @ gamma
        design[index] = np.concatenate((left, -left))
    singular = np.linalg.svd(design, compute_uv=False)
    tau = max(design.shape) * np.finfo(np.float64).eps * singular[0]
    rank = int(np.sum(singular > tau))
    nullspace = np.vstack((np.eye(3), np.eye(3))) / math.sqrt(2)
    null_action = float(np.linalg.norm(design @ nullspace))
    null_bound = 256 * np.finfo(np.float64).eps * max(1.0, float(np.linalg.norm(design)))

    def residual(x: np.ndarray) -> np.ndarray:
        first = np.asarray([
            np.linalg.norm(acc1[index] - gamma_rows[index] @ x[:3])
            for index in range(1001)
        ])
        second = np.asarray([
            np.linalg.norm(acc2[index] - gamma_rows[index] @ x[3:])
            for index in range(1001)
        ])
        return first - second

    finite_rows = []
    for h in (1e-4, 1e-5, 1e-6):
        jacobian = np.column_stack([
            (residual(np.eye(6)[column] * h) - residual(-np.eye(6)[column] * h)) / (2 * h)
            for column in range(6)
        ])
        values = np.linalg.svd(jacobian, compute_uv=False)
        threshold = max(jacobian.shape) * np.finfo(np.float64).eps * values[0]
        finite_rows.append({
            "h_m": h,
            "finite": bool(np.all(np.isfinite(jacobian))),
            "rank": int(np.sum(values > threshold)),
            "singular_values": values.tolist(),
        })
    hashes = {
        "acc1": hashlib.sha256(acc1.tobytes()).hexdigest(),
        "acc2": hashlib.sha256(acc2.tobytes()).hexdigest(),
        "gyr1": hashlib.sha256(gyr1.tobytes()).hexdigest(),
        "gyr2": hashlib.sha256(gyr2.tobytes()).hexdigest(),
    }
    passed = bool(
        hashes["acc1"] == hashes["acc2"]
        and hashes["gyr1"] == hashes["gyr2"]
        and rank <= 3
        and null_action <= null_bound
        and all(row["finite"] and row["rank"] <= 3 for row in finite_rows)
    )
    return {
        "id": "JTF06_SEEL_IDENTICAL_PAIR_NULLSPACE",
        "pass": passed,
        "first_gate": "SEEL_IDENTICAL_PAIR_COMMON_LEVER_NULLSPACE",
        "dimensions": {"rows": 1001, "unknowns": 6, "analytic_jacobian_shape": [1001, 6]},
        "array_sha256": hashes,
        "analytic_rank": rank,
        "analytic_singular_values": singular.tolist(),
        "rank_tolerance": float(tau),
        "null_action_fro": null_action,
        "null_action_bound": null_bound,
        "central_difference_report_only": finite_rows,
        "imt_calls": 0,
        "scipy_calls": 0,
        "output_vector": None,
    }
