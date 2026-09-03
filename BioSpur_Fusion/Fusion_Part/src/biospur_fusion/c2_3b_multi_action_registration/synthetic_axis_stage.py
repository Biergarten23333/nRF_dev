"""Approved synthetic generator, QMT Olsson line fit, and QMT Wahba stage.

This module contains no optimizer.  Functional lines and vector registration
are direct calls into the pinned QMT 0.2.4 implementation.  Official OpenSense
is a later stage and is not invoked here.
"""

from __future__ import annotations

from contextlib import redirect_stdout
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
import hashlib
import io
import json
import math
from multiprocessing import get_context
import os
from pathlib import Path
import sys
import time
from typing import Any, Iterable

import numpy as np
import qmt

from .contracts import PROFILE_IDS, PROFILE_SIGMA_RAD, SEEDS, load_approved_contract


DT = 0.005
ROWS = 6401
FIXED_ROWS = tuple(range(0, ROWS, 400))
DEPENDENT_COORDINATES = ("knee_angle_r_beta", "knee_angle_l_beta")
ROOT_TRANSLATIONS = ("pelvis_tx", "pelvis_ty", "pelvis_tz")
STATE_TOLERANCE = 1e-5
FRAME_TOLERANCE = 1e-12
TRUTH_LINE_TOLERANCE = math.radians(5.0)
NODE_ORDER = (
    "forearm_left", "forearm_right", "upper_arm_left", "upper_arm_right",
    "torso", "pelvis", "thigh_left", "thigh_right", "shank_left", "shank_right",
)
UNIQUE_AXIS_VARIANTS = ("BASE", "POS04_HIGH_OOP_EXACT_ROLL", "POS05_GAPS_BOOT")
POSITIVE_TO_AXIS_VARIANT = {
    "POS01_NOMINAL_MULTI_ACTION": "BASE",
    "POS02_IMPERFECT_POSES": "BASE",
    "POS03_MODEL_CONSISTENT_PELVIS_LIST_35DEG": "BASE",
    "POS04_HIGH_OOP": "POS04_HIGH_OOP_EXACT_ROLL",
    "POS05_GAPS_AND_BOOT": "POS05_GAPS_BOOT",
    "POS06_DRIFT_AND_ORDER_DIAGNOSTIC": "BASE",
}


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(path)
    return value


def _substream_seed(seed: int, path: str) -> int:
    payload = b"C2-3B-MAR-R2\0" + str(seed).encode("ascii") + b"\0" + path.encode("utf-8")
    return int.from_bytes(hashlib.sha256(payload).digest()[0:16], "little")


def _rng(seed: int, path: str) -> np.random.Generator:
    return np.random.Generator(np.random.PCG64DXSM(_substream_seed(seed, path)))


def _matrix(rotation: Any) -> np.ndarray:
    return np.array([[rotation.get(i, j) for j in range(3)] for i in range(3)], dtype=np.float64)


def _vector(value: Any) -> np.ndarray:
    return np.array([value.get(i) for i in range(3)], dtype=np.float64)


def _canonical_plane(axis: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    unit = np.asarray(axis, dtype=np.float64)
    unit /= np.linalg.norm(unit)
    basis = np.eye(3)
    index = int(np.argmin(np.abs(basis @ unit)))
    first = basis[index] - unit * float(unit @ basis[index])
    first /= np.linalg.norm(first)
    return first, np.cross(unit, first)


def _rotation_about(axis: np.ndarray, angle: float | np.ndarray) -> np.ndarray:
    return np.asarray(qmt.quatToRotMat(qmt.quatFromAngleAxis(angle, axis)), dtype=np.float64)


def _rotation_distance(left: np.ndarray, right: np.ndarray) -> float:
    relative = left.T @ right
    value = np.asarray(qmt.quatToRotVec(qmt.quatFromRotMat(relative)), dtype=np.float64)
    return float(np.linalg.norm(value))


def _line_angle(left: np.ndarray, right: np.ndarray) -> float:
    a = np.asarray(left, dtype=np.float64); a /= np.linalg.norm(a)
    b = np.asarray(right, dtype=np.float64); b /= np.linalg.norm(b)
    return math.acos(float(np.clip(abs(a @ b), 0.0, 1.0)))


def _truth_line_gate(
    whole_errors: Iterable[float], block_rows: Iterable[dict[str, Any]],
) -> bool:
    whole = tuple(float(value) for value in whole_errors)
    blocks = tuple(block_rows)
    return bool(
        whole
        and max(whole) <= TRUTH_LINE_TOLERANCE
        and all(
            max(
                float(row["parent_truth_line_error_rad"]),
                float(row["child_truth_line_error_rad"]),
            ) <= TRUTH_LINE_TOLERANCE
            for row in blocks
        )
    )


def _all_axis_variants_eligible(per_variant: dict[str, dict[str, Any]]) -> bool:
    if tuple(per_variant) != UNIQUE_AXIS_VARIANTS:
        raise ValueError("axis variant order changed")
    return all(
        bool(row["all_edges_eligible"]) and not bool(row["saturated"])
        for row in per_variant.values()
    )


def _cache_dependency_key(payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _verify_cache_dependency(payload: dict[str, Any], expected: str) -> None:
    if _cache_dependency_key(payload) != expected:
        raise ValueError("stale or mismatched invariant cache dependency")


@dataclass(frozen=True)
class EdgeSpec:
    name: str
    episode: int
    parent: str
    child: str
    parent_target: np.ndarray
    child_target: np.ndarray


@dataclass
class SeedParameters:
    coordinate_phase: np.ndarray
    phase_tx: float
    phase_ty: float
    phase_tz: float
    oop_phase: np.ndarray
    locations: dict[str, np.ndarray]
    mounts_base: dict[str, np.ndarray]
    mounts_roll90: dict[str, np.ndarray]
    gyro_bias: np.ndarray
    acc_bias: np.ndarray
    gyro_noise: np.ndarray
    acc_noise: np.ndarray


class OfficialSyntheticState:
    """Own the exact Revision-5 OpenSim forward-state lifecycle."""

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
        self._assemble({name: self.defaults[name] for name in self.reference_order})
        self._assert_policy()

    def _references(self, requested: dict[str, float]) -> Any:
        references = self.osim.SimTKArrayCoordinateReference()
        for name in self.reference_order:
            reference = self.osim.CoordinateReference(
                name, self.osim.Constant(float(requested[name])),
            )
            reference.setWeight(math.inf)
            references.push_back(reference)
        return references

    def _assemble(self, requested: dict[str, float]) -> None:
        solver = self.osim.AssemblySolver(
            self.model, self._references(requested), FRAME_TOLERANCE,
        )
        solver.setConstraintWeight(math.inf)
        solver.assemble(self.state)
        self.model.realizePosition(self.state)

    def _assert_policy(self) -> None:
        writable = set(self.reference_order)
        for name in self.reference_order:
            if self.coordinates.get(name).getLocked(self.state):
                raise ValueError(f"writable coordinate remains locked: {name}")
        for name in DEPENDENT_COORDINATES:
            coordinate = self.coordinates.get(name)
            if not coordinate.isConstrained(self.state) or coordinate.getLocked(self.state):
                raise ValueError(f"dependent coordinate policy changed: {name}")
        for name in self.defaults:
            if name not in writable and name not in DEPENDENT_COORDINATES:
                if not self.coordinates.get(name).getLocked(self.state):
                    raise ValueError(f"unowned coordinate is not locked: {name}")
        for name in ("pro_sup_l", "pro_sup_r"):
            if (
                not self.coordinates.get(name).getLocked(self.state)
                or float(self.coordinates.get(name).getValue(self.state)) != 0.0
            ):
                raise ValueError(f"forearm pronation policy changed: {name}")

    def requested(self, values: dict[str, float]) -> dict[str, float]:
        unknown = set(values) - set(self.reference_order)
        if unknown:
            raise ValueError(f"unowned requested coordinates: {sorted(unknown)}")
        return {
            name: float(values.get(name, self.defaults[name]))
            for name in self.reference_order
        }

    def apply(self, values: dict[str, float]) -> dict[str, Any]:
        requested = self.requested(values)
        out_of_range = []
        for name, value in requested.items():
            coordinate = self.coordinates.get(name)
            if value < coordinate.getRangeMin() or value > coordinate.getRangeMax():
                out_of_range.append(name)
        if out_of_range:
            return {
                "pass": False,
                "out_of_range": sorted(out_of_range),
                "requested_error": math.inf,
                "locked_error": math.inf,
                "coupling_error": math.inf,
            }
        self._assemble(requested)
        requested_errors = {
            name: abs(float(self.coordinates.get(name).getValue(self.state)) - value)
            for name, value in requested.items()
        }
        locked_errors = {
            name: abs(float(self.coordinates.get(name).getValue(self.state)) - default)
            for name, default in self.defaults.items()
            if name not in DEPENDENT_COORDINATES
            and self.coordinates.get(name).getLocked(self.state)
        }
        coupling_errors: dict[str, float] = {}
        dependent_names: set[str] = set()
        for index in range(self.model.getConstraintSet().getSize()):
            constraint = self.osim.CoordinateCouplerConstraint.safeDownCast(
                self.model.getConstraintSet().get(index),
            )
            if constraint is None:
                continue
            names = constraint.getIndependentCoordinateNames()
            if names.getSize() != 1:
                raise ValueError("unexpected multi-input coordinate coupler")
            independent = float(self.coordinates.get(names.get(0)).getValue(self.state))
            expected = float(
                constraint.getFunction().calcValue(self.osim.Vector(1, independent)),
            )
            dependent = constraint.getDependentCoordinateName()
            dependent_names.add(dependent)
            actual = float(self.coordinates.get(dependent).getValue(self.state))
            coupling_errors[constraint.getName()] = abs(actual - expected)
        if dependent_names != set(DEPENDENT_COORDINATES):
            raise ValueError(
                f"official knee coupler dependent set changed: {sorted(dependent_names)}"
            )
        requested_error = max(requested_errors.values())
        locked_error = max(locked_errors.values())
        coupling_error = max(coupling_errors.values())
        finite = all(
            math.isfinite(value)
            for value in (requested_error, locked_error, coupling_error)
        )
        return {
            "pass": bool(
                finite
                and requested_error <= STATE_TOLERANCE
                and locked_error <= STATE_TOLERANCE
                and coupling_error <= STATE_TOLERANCE
            ),
            "out_of_range": [],
            "requested_error": requested_error,
            "requested_worst": max(requested_errors, key=requested_errors.get),
            "locked_error": locked_error,
            "locked_worst": max(locked_errors, key=locked_errors.get),
            "coupling_error": coupling_error,
            "coupling_errors": coupling_errors,
        }


def _coordinate_values(
    synthetic_r2: dict[str, Any], seed_parameters: SeedParameters, episode: int,
    t: float,
) -> dict[str, float]:
    model_truth = synthetic_r2["model_truth"]
    enabled = model_truth["enabled_coordinates"]
    active = model_truth["episode_table"][episode]["active"]
    result: dict[str, float] = {}
    for index, name in enumerate(enabled):
        if name not in active:
            continue
        center_deg, amplitude_deg, frequency = active[name]
        phase = float(seed_parameters.coordinate_phase[episode, index])
        amplitude = math.radians(float(amplitude_deg))
        result[name] = (
            math.radians(float(center_deg))
            + amplitude * math.sin(2 * math.pi * float(frequency) * t + phase)
            + 0.15 * amplitude * math.sin(2 * math.pi * (float(frequency) + 0.13) * t + 0.37 * phase)
        )
    result["pelvis_tx"] = 0.030 * math.sin(2 * math.pi * t / 12 + seed_parameters.phase_tx)
    result["pelvis_ty"] = 0.93 + 0.010 * math.sin(4 * math.pi * t / 12 + seed_parameters.phase_ty)
    result["pelvis_tz"] = 0.020 * math.sin(2 * math.pi * t / 12 + seed_parameters.phase_tz)
    return result


def _endpoint_in_body(model: Any, state: Any, body_name: str, endpoint: str) -> np.ndarray:
    body_transform = model.getBodySet().get(body_name).getTransformInGround(state)
    r_ob = _matrix(body_transform.R())
    p_ob = _vector(body_transform.p())
    if endpoint.startswith("midpoint(") and endpoint.endswith(")"):
        paths = endpoint[len("midpoint("):-1].split(",")
        point_o = np.mean([
            _vector(model.getComponent(path).getTransformInGround(state).p()) for path in paths
        ], axis=0)
    else:
        point_o = _vector(model.getComponent(endpoint).getTransformInGround(state).p())
    return r_ob.T @ (point_o - p_ob)


def _seed_parameters(
    seed: int, model: Any, state: Any, r2_synthetic: dict[str, Any],
    r2_static: dict[str, Any], donning: dict[str, Any],
) -> SeedParameters:
    coordinate_phase = _rng(seed, "model/phase_coordinate").uniform(-math.pi, math.pi, size=(19, 22))
    phase_tx = float(_rng(seed, "model/phase_tx").uniform(-math.pi, math.pi))
    phase_ty = float(_rng(seed, "model/phase_ty").uniform(-math.pi, math.pi))
    phase_tz = float(_rng(seed, "model/phase_tz").uniform(-math.pi, math.pi))
    oop_phase = _rng(seed, "model/oop_phase").uniform(-math.pi, math.pi, size=4)

    axial_fraction = _rng(seed, "sensor/axial_fraction").uniform(0.25, 0.75, size=10)
    radius = _rng(seed, "sensor/cylinder_radius_m").uniform(0.008, 0.025, size=10)
    cylinder_angle = _rng(seed, "sensor/cylinder_angle").uniform(-math.pi, math.pi, size=10)
    locations: dict[str, np.ndarray] = {}
    body_map = r2_static["body_by_segment"]
    endpoint_paths = r2_synthetic["sensor_truth"]["location_endpoint_paths"]
    for index, segment in enumerate(NODE_ORDER):
        first, second = endpoint_paths[segment]
        p0 = _endpoint_in_body(model, state, body_map[segment], first)
        p1 = _endpoint_in_body(model, state, body_map[segment], second)
        long = p1 - p0; long /= np.linalg.norm(long)
        reference = np.array([1.0, 0.0, 0.0]) if abs(float(long @ np.array([1.0, 0.0, 0.0]))) <= 0.9 else np.array([0.0, 1.0, 0.0])
        e1 = np.cross(long, reference); e1 /= np.linalg.norm(e1)
        e2 = np.cross(long, e1)
        locations[segment] = (
            p0 + axial_fraction[index] * (p1 - p0)
            + radius[index] * (math.cos(cylinder_angle[index]) * e1 + math.sin(cylinder_angle[index]) * e2)
        )

    cos_beta = _rng(seed, "sensor/wear_cone_cos_beta").uniform(math.cos(math.radians(20)), 1.0, size=10)
    azimuth = _rng(seed, "sensor/wear_cone_azimuth").uniform(-math.pi, math.pi, size=10)
    roll = _rng(seed, "sensor/axial_strap_roll").uniform(-math.pi, math.pi, size=10)
    mounts_base: dict[str, np.ndarray] = {}
    mounts_roll90: dict[str, np.ndarray] = {}
    for index, segment in enumerate(NODE_ORDER):
        ell = np.asarray(donning["body_longitudinal_vectors"][segment]["ell_B"], dtype=np.float64)
        a, b = _canonical_plane(ell)
        beta = math.acos(float(cos_beta[index]))
        direction = math.cos(beta) * ell + math.sin(beta) * (
            math.cos(azimuth[index]) * a + math.sin(azimuth[index]) * b
        )
        direction /= np.linalg.norm(direction)
        y_body = -direction
        x0, _ = _canonical_plane(y_body)
        z0 = np.cross(x0, y_body)
        m0 = np.column_stack((x0, y_body, z0))
        mounts_base[segment] = _rotation_about(direction, float(roll[index])) @ m0
        mounts_roll90[segment] = _rotation_about(direction, (-1.0 if index % 2 else 1.0) * math.pi / 2) @ m0

    total = 19 * ROWS
    gyro_bias0 = _rng(seed, "sensor/gyro_bias0").normal(0.0, 0.006, size=(10, 3))
    acc_bias0 = _rng(seed, "sensor/acc_bias0").normal(0.0, 0.08, size=(10, 3))
    gyro_rw = _rng(seed, "sensor/gyro_bias_rw").normal(0.0, 0.00005, size=(10, 3, total))
    acc_rw = _rng(seed, "sensor/acc_bias_rw").normal(0.0, 0.002, size=(10, 3, total))
    gyro_bias = gyro_bias0[:, :, None] + np.cumsum(gyro_rw * math.sqrt(DT), axis=2)
    acc_bias = acc_bias0[:, :, None] + np.cumsum(acc_rw * math.sqrt(DT), axis=2)
    gyro_noise = _rng(seed, "sensor/gyro_noise").normal(0.0, 0.004, size=(19, 10, ROWS, 3))
    acc_noise = _rng(seed, "sensor/acc_noise").normal(0.0, 0.06, size=(19, 10, ROWS, 3))
    return SeedParameters(
        coordinate_phase, phase_tx, phase_ty, phase_tz, oop_phase, locations,
        mounts_base, mounts_roll90, gyro_bias, acc_bias, gyro_noise, acc_noise,
    )


def _angular_velocity(rotation: np.ndarray) -> np.ndarray:
    result = np.empty((len(rotation), 3), dtype=np.float64)
    result[0] = np.asarray(
        qmt.quatToRotVec(qmt.quatFromRotMat(rotation[0].T @ rotation[1])),
        dtype=np.float64,
    ) / DT
    result[-1] = np.asarray(
        qmt.quatToRotVec(qmt.quatFromRotMat(rotation[-2].T @ rotation[-1])),
        dtype=np.float64,
    ) / DT
    relative = np.transpose(rotation[:-2], (0, 2, 1)) @ rotation[2:]
    result[1:-1] = np.asarray(
        qmt.quatToRotVec(qmt.quatFromRotMat(relative)), dtype=np.float64,
    ) / (2 * DT)
    return result


def _linear_acceleration(point: np.ndarray) -> np.ndarray:
    result = np.empty_like(point)
    result[1:-1] = (point[2:] - 2 * point[1:-1] + point[:-2]) / DT**2
    result[0] = (point[2] - 2 * point[1] + point[0]) / DT**2
    result[-1] = (point[-1] - 2 * point[-2] + point[-3]) / DT**2
    return result


def _quantize_decode(values: np.ndarray, scale: float) -> tuple[np.ndarray, bool]:
    counts_float = np.rint(values / scale)
    saturated = bool(np.any(counts_float < -32768) or np.any(counts_float > 32767))
    counts = np.clip(counts_float, -32768, 32767).astype(np.int16)
    return counts.astype(np.float64) * scale, saturated


def _episode_signals(
    model: Any, state_owner: OfficialSyntheticState, episode: int, variant: str,
    segments: Iterable[str],
    parameters: SeedParameters, r2_synthetic: dict[str, Any], r2_static: dict[str, Any],
) -> tuple[dict[str, dict[str, np.ndarray]], bool]:
    body_map = r2_static["body_by_segment"]
    segment_list = tuple(dict.fromkeys(segments))
    body_rotation = {segment: np.empty((ROWS, 3, 3), dtype=np.float64) for segment in segment_list}
    body_origin = {segment: np.empty((ROWS, 3), dtype=np.float64) for segment in segment_list}
    oop = r2_synthetic["model_truth"]["off_model_human_variability"]["perturbations"]
    oop_by_body = {row[1]: (np.asarray(row[2], dtype=np.float64), float(row[3]), float(row[4]), index) for index, row in enumerate(oop)}
    amplitude_scale = 1.75 if variant == "POS04_HIGH_OOP_EXACT_ROLL" else 1.0
    for row in range(ROWS):
        t = row * DT
        values = _coordinate_values(r2_synthetic, parameters, episode, t)
        state_result = state_owner.apply(values)
        if not state_result["pass"]:
            raise ValueError(f"official state gate failed at episode {episode} row {row}: {state_result}")
        state = state_owner.state
        for segment in segment_list:
            body_name = body_map[segment]
            transform = model.getBodySet().get(body_name).getTransformInGround(state)
            rotation = _matrix(transform.R())
            if body_name in oop_by_body:
                axis, amplitude_deg, frequency, phase_index = oop_by_body[body_name]
                angle = amplitude_scale * math.radians(amplitude_deg) * math.sin(
                    2 * math.pi * frequency * t + float(parameters.oop_phase[phase_index])
                )
                rotation = rotation @ _rotation_about(axis, angle)
            body_rotation[segment][row] = rotation
            body_origin[segment][row] = _vector(transform.p())
    signals: dict[str, dict[str, np.ndarray]] = {}
    any_saturation = False
    for segment in segment_list:
        node_index = NODE_ORDER.index(segment)
        mount = parameters.mounts_roll90[segment] if variant == "POS04_HIGH_OOP_EXACT_ROLL" else parameters.mounts_base[segment]
        sensor_rotation = body_rotation[segment] @ mount
        sensor_point = body_origin[segment] + np.einsum("nij,j->ni", body_rotation[segment], parameters.locations[segment])
        acceleration_o = _linear_acceleration(sensor_point)
        force_o = acceleration_o - np.array([0.0, -9.80665, 0.0])
        ideal_acc = np.einsum("nji,nj->ni", sensor_rotation, force_o)
        ideal_gyr = _angular_velocity(sensor_rotation)
        start = episode * ROWS; stop = start + ROWS
        acc = ideal_acc + parameters.acc_bias[node_index, :, start:stop].T + parameters.acc_noise[episode, node_index]
        gyr = ideal_gyr + parameters.gyro_bias[node_index, :, start:stop].T + parameters.gyro_noise[episode, node_index]
        acc, acc_saturated = _quantize_decode(acc, 9.80665 / 2048.0)
        gyr, gyr_saturated = _quantize_decode(gyr, math.radians(1.0 / 16.384))
        any_saturation = any_saturation or acc_saturated or gyr_saturated
        mask = np.ones(ROWS, dtype=bool)
        span = np.zeros(ROWS, dtype=np.int64)
        if variant == "POS05_GAPS_BOOT":
            for ep, node, first, stop_row in r2_synthetic["boundary_events"]["POS05_GAPS"]["missing_half_open"]:
                if ep == episode and node == node_index:
                    mask[first:stop_row] = False
                    span[stop_row:] += 1
            for ep, node, after in r2_synthetic["boundary_events"]["POS05_GAPS"]["boot_after_row"]:
                if ep == episode and node == node_index:
                    span[after + 1:] += 1
        signals[segment] = {"acc": acc, "gyr": gyr, "mask": mask, "span": span}
    return signals, any_saturation


def _paired_complete_blocks(parent: dict[str, np.ndarray], child: dict[str, np.ndarray]) -> tuple[list[np.ndarray], int]:
    valid = parent["mask"] & child["mask"]
    source = np.flatnonzero(valid)
    blocks: list[np.ndarray] = []
    leftovers = 0
    if not len(source):
        return blocks, 0
    boundaries = np.flatnonzero(
        (np.diff(source) != 1)
        | (parent["span"][source[1:]] != parent["span"][source[:-1]])
        | (child["span"][source[1:]] != child["span"][source[:-1]])
    ) + 1
    for span_indices in np.split(source, boundaries):
        complete = len(span_indices) // 1600
        blocks.extend(span_indices[i * 1600:(i + 1) * 1600] for i in range(complete))
        leftovers += len(span_indices) - complete * 1600
    return blocks, leftovers


def _olsson_call(
    parent: dict[str, np.ndarray], child: dict[str, np.ndarray], indices: np.ndarray,
    settings: dict[str, Any],
) -> tuple[np.ndarray, np.ndarray, float, dict[str, Any]]:
    arrays = [
        np.ascontiguousarray(parent["acc"][indices], dtype=np.float64),
        np.ascontiguousarray(child["acc"][indices], dtype=np.float64),
        np.ascontiguousarray(parent["gyr"][indices], dtype=np.float64),
        np.ascontiguousarray(child["gyr"][indices], dtype=np.float64),
    ]
    started = time.monotonic()
    with redirect_stdout(io.StringIO()):
        result = qmt.jointAxisEstHingeOlsson(
            *arrays, estSettings=settings, debug=True, plot=False,
        )
    elapsed = time.monotonic() - started
    native = result[2]["optimVarsAxis"]
    iterations = len(native.get("ftraj", []))
    costs = np.asarray(native.get("ftraj", []), dtype=np.float64).reshape(-1)
    return (
        np.asarray(result[0], dtype=np.float64).reshape(3),
        np.asarray(result[1], dtype=np.float64).reshape(3),
        elapsed,
        {
            "native_iterations": iterations,
            "native_initial_cost": float(costs[0]) if len(costs) else None,
            "native_final_cost": float(costs[-1]) if len(costs) else None,
        },
    )


def _fit_edge(
    spec: EdgeSpec, signals: dict[str, dict[str, np.ndarray]], settings: dict[str, Any],
    truth_mounts: dict[str, np.ndarray],
) -> dict[str, Any]:
    parent = signals[spec.parent]; child = signals[spec.child]
    blocks, leftover = _paired_complete_blocks(parent, child)
    if not blocks:
        return {"edge": spec.name, "eligible": False, "gate": "AXIS_COMPLETE_BLOCKS", "complete_blocks": 0, "leftover_rows": leftover}
    whole_indices = np.concatenate(blocks)
    whole_parent, whole_child, whole_wall, whole_native = _olsson_call(
        parent, child, whole_indices, settings,
    )
    block_rows = []
    max_wall = whole_wall
    for block_id, indices in enumerate(blocks):
        axis_parent, axis_child, wall, native = _olsson_call(
            parent, child, indices, settings,
        )
        max_wall = max(max_wall, wall)
        block_rows.append({
            "block_id": block_id,
            "first_source_row": int(indices[0]),
            "last_source_row": int(indices[-1]),
            "parent_axis_S": axis_parent.tolist(),
            "child_axis_S": axis_child.tolist(),
            "parent_to_whole_rad": _line_angle(axis_parent, whole_parent),
            "child_to_whole_rad": _line_angle(axis_child, whole_child),
            "parent_truth_line_error_rad": _line_angle(axis_parent, truth_mounts[spec.parent].T @ spec.parent_target),
            "child_truth_line_error_rad": _line_angle(axis_child, truth_mounts[spec.child].T @ spec.child_target),
            "wall_s": wall,
            **native,
        })
    parent_angles = np.asarray([row["parent_to_whole_rad"] for row in block_rows])
    child_angles = np.asarray([row["child_to_whole_rad"] for row in block_rows])
    all_angles = np.concatenate([parent_angles, child_angles])
    rms = float(np.sqrt(np.mean(all_angles**2)))
    p95 = float(np.quantile(all_angles, 0.95, method="linear"))
    unit_errors = [abs(np.linalg.norm(axis) - 1.0) for axis in (whole_parent, whole_child)]
    truth_parent = truth_mounts[spec.parent].T @ spec.parent_target
    truth_child = truth_mounts[spec.child].T @ spec.child_target
    truth_errors = [_line_angle(whole_parent, truth_parent), _line_angle(whole_child, truth_child)]
    inherited_eligible = bool(
        len(blocks) >= 3
        and max(unit_errors) <= 1e-10
        and rms <= math.radians(10)
        and p95 <= math.radians(20)
        and max_wall <= 30
    )
    truth_line_pass = _truth_line_gate(truth_errors, block_rows)
    eligible = inherited_eligible and truth_line_pass
    gate = None
    if not inherited_eligible:
        gate = "AXIS_FIT_ELIGIBILITY"
    elif not truth_line_pass:
        gate = "AXIS_SYNTHETIC_TRUTH_LINE"
    return {
        "edge": spec.name,
        "eligible": eligible,
        "gate": gate,
        "complete_blocks": len(blocks),
        "leftover_rows": leftover,
        "whole_parent_axis_S": whole_parent.tolist(),
        "whole_child_axis_S": whole_child.tolist(),
        "truth_parent_axis_S": truth_parent.tolist(),
        "truth_child_axis_S": truth_child.tolist(),
        "truth_line_error_rad": truth_errors,
        "truth_line_bound_rad": TRUTH_LINE_TOLERANCE,
        "truth_line_pass": truth_line_pass,
        "dispersion_rms_rad": rms,
        "dispersion_p95_rad": p95,
        "maximum_call_wall_s": max_wall,
        "whole_native": whole_native,
        "blocks": block_rows,
    }


def _solve_branch(
    branch_id: int, profile_id: str, sigma: float, edges: list[EdgeSpec], fits: dict[str, dict[str, Any]],
    donning: dict[str, Any], truth_mounts: dict[str, np.ndarray],
) -> dict[str, Any]:
    observations: dict[str, list[tuple[np.ndarray, np.ndarray, float, str]]] = {segment: [] for segment in NODE_ORDER}
    for edge_index, spec in enumerate(edges):
        fit = fits[spec.name]
        sign = 1.0 if ((branch_id >> edge_index) & 1) == 0 else -1.0
        for block in fit.get("blocks", []):
            observations[spec.parent].append((
                sign * np.asarray(block["parent_axis_S"]), spec.parent_target,
                1.0 / (float(block["parent_to_whole_rad"])**2 + math.radians(10)**2), spec.name,
            ))
            observations[spec.child].append((
                sign * np.asarray(block["child_axis_S"]), spec.child_target,
                1.0 / (float(block["child_to_whole_rad"])**2 + math.radians(10)**2), spec.name,
            ))
    mounts: dict[str, list[list[float]]] = {}
    truth_error: dict[str, float] = {}
    wear_error: dict[str, float] = {}
    rank_by_node: dict[str, int] = {}
    sensor_separation: dict[str, float] = {}
    axis_residuals: list[float] = []
    eligible = all(fit["eligible"] for fit in fits.values())
    gate = None if eligible else "AXIS_FIT_ELIGIBILITY"
    for segment in NODE_ORDER:
        ell = np.asarray(donning["body_longitudinal_vectors"][segment]["ell_B"], dtype=np.float64)
        rows = list(observations[segment])
        if not rows:
            eligible = False; gate = gate or "STATIC_RANK"; continue
        separation = max(_line_angle(row[0], np.array([0.0, -1.0, 0.0])) for row in rows)
        sensor_separation[segment] = separation
        rows.append((np.array([0.0, -1.0, 0.0]), ell, 1.0 / sigma**2, "donning"))
        v = np.vstack([row[0] for row in rows])
        w = np.vstack([row[1] for row in rows])
        weight = np.asarray([row[2] for row in rows], dtype=np.float64)
        weight /= np.sum(weight)
        b_matrix = np.einsum("n,ni,nj->ij", weight, w, v)
        singular = np.linalg.svd(b_matrix, compute_uv=False)
        rank = int(np.sum(singular > 3 * np.finfo(np.float64).eps * singular[0]))
        rank_by_node[segment] = rank
        if rank < 2 or separation < math.radians(20):
            eligible = False; gate = gate or "STATIC_RANK"; continue
        q_bs = np.asarray(qmt.quatFromVectorObservations(v, w, weights=weight, debug=False, plot=False))
        mount = np.asarray(qmt.quatToRotMat(q_bs), dtype=np.float64)
        proper = bool(np.all(np.isfinite(mount)) and abs(np.linalg.det(mount) - 1) <= 1e-12 and np.linalg.norm(mount.T @ mount - np.eye(3)) <= 1e-12)
        if not proper:
            eligible = False; gate = gate or "PROPER_ROTATION"; continue
        residual = [math.acos(float(np.clip((mount @ row[0]) @ row[1], -1, 1))) for row in rows[:-1]]
        axis_residuals.extend(residual)
        wear = math.acos(float(np.clip((mount @ np.array([0.0, -1.0, 0.0])) @ ell, -1, 1)))
        wear_error[segment] = wear
        truth_error[segment] = _rotation_distance(mount, truth_mounts[segment])
        mounts[segment] = mount.tolist()
        if wear >= math.pi / 2:
            eligible = False; gate = gate or "DONNING_SOFT_VECTOR_INELIGIBLE"
    axis_p95 = float(np.quantile(axis_residuals, 0.95, method="linear")) if axis_residuals else math.inf
    if axis_p95 > math.radians(20):
        eligible = False; gate = gate or "FUNCTIONAL_AXIS_RESIDUAL"
    return {
        "wear_profile_id": profile_id,
        "wear_sigma_rad": sigma,
        "branch_id": branch_id,
        "internal_eligible": eligible,
        "first_gate": gate,
        "rank_by_node": rank_by_node,
        "sensor_separation_rad": sensor_separation,
        "wear_residual_rad": wear_error,
        "functional_residual_p95_rad": axis_p95,
        "registration_truth_rad": truth_error,
        "mounts": mounts,
    }


def _edge_specs(r2_mechanism: dict[str, Any], r2_static: dict[str, Any]) -> list[EdgeSpec]:
    episode_by_edge = {row[0]: int(row[1]) for row in r2_mechanism["one_d_heading"]["edge_axis_sources"]}
    result = []
    for edge, parent, child in r2_mechanism["production_identity"]["edges_ordered"]:
        row = r2_static["functional_axis_body_targets"]["rows"][edge]
        result.append(EdgeSpec(
            edge, episode_by_edge[edge], parent, child,
            np.asarray(row["parent_body_target"], dtype=np.float64),
            np.asarray(row["child_body_target"], dtype=np.float64),
        ))
    return result


def _truth_mounts(seed: int, donning: dict[str, Any]) -> dict[str, np.ndarray]:
    cos_beta = _rng(seed, "sensor/wear_cone_cos_beta").uniform(
        math.cos(math.radians(20)), 1.0, size=10,
    )
    azimuth = _rng(seed, "sensor/wear_cone_azimuth").uniform(
        -math.pi, math.pi, size=10,
    )
    roll = _rng(seed, "sensor/axial_strap_roll").uniform(
        -math.pi, math.pi, size=10,
    )
    mounts: dict[str, np.ndarray] = {}
    for index, segment in enumerate(NODE_ORDER):
        longitudinal = np.asarray(
            donning["body_longitudinal_vectors"][segment]["ell_B"],
            dtype=np.float64,
        )
        first, second = _canonical_plane(longitudinal)
        beta = math.acos(float(cos_beta[index]))
        direction = (
            math.cos(beta) * longitudinal
            + math.sin(beta)
            * (
                math.cos(azimuth[index]) * first
                + math.sin(azimuth[index]) * second
            )
        )
        direction /= np.linalg.norm(direction)
        sensor_y_body = -direction
        sensor_x_body, _ = _canonical_plane(sensor_y_body)
        sensor_z_body = np.cross(sensor_x_body, sensor_y_body)
        mounts[segment] = _rotation_about(direction, float(roll[index])) @ np.column_stack(
            (sensor_x_body, sensor_y_body, sensor_z_body),
        )
    return mounts


def _checkpoint_seed_parameters(seed: int, donning: dict[str, Any]) -> dict[str, Any]:
    return {
        "coordinate_phase": _rng(seed, "model/phase_coordinate").uniform(
            -math.pi, math.pi, size=(19, 22),
        ),
        "phase_tx": float(_rng(seed, "model/phase_tx").uniform(-math.pi, math.pi)),
        "phase_ty": float(_rng(seed, "model/phase_ty").uniform(-math.pi, math.pi)),
        "phase_tz": float(_rng(seed, "model/phase_tz").uniform(-math.pi, math.pi)),
        "mounts": _truth_mounts(seed, donning),
    }


def _checkpoint_coordinate_values(
    synthetic: dict[str, Any], parameters: dict[str, Any], episode: int, t: float,
) -> dict[str, float]:
    shim = type("CheckpointParameters", (), parameters)()
    return _coordinate_values(synthetic, shim, episode, t)


def _state_frame_checkpoint(
    state_owner: OfficialSyntheticState,
    edges: list[EdgeSpec],
    edge_contracts: dict[str, dict[str, Any]],
    parameters: dict[str, Any],
    r2_synthetic: dict[str, Any],
    r2_static: dict[str, Any],
    deadline: float,
) -> dict[str, Any]:
    started = time.monotonic()
    body_map = r2_static["body_by_segment"]
    edge_rows: list[dict[str, Any]] = []
    maximum_requested_error = 0.0
    maximum_locked_error = 0.0
    maximum_coupling_error = 0.0
    worst_requested_name: str | None = None
    worst_locked_name: str | None = None
    maximum_inverse = 0.0
    maximum_quat_matrix = 0.0
    maximum_axis_forward = 0.0
    maximum_quaternion_sign = 0.0
    all_state_rows_pass = True
    out_of_range: set[str] = set()
    for spec in edges:
        contract = edge_contracts[spec.name]
        coordinate_name = contract["coordinate"]
        active = r2_synthetic["model_truth"]["episode_table"][spec.episode]["active"]
        if active[coordinate_name] != [
            contract["center_deg"], contract["amplitude_deg"], contract["frequency_hz"],
        ]:
            raise ValueError(f"trajectory contract changed for {spec.name}")
        coordinate = np.asarray([
            _checkpoint_coordinate_values(
                r2_synthetic, parameters, spec.episode, row * DT,
            )[coordinate_name]
            for row in range(ROWS)
        ], dtype="<f8")
        relative_rotations: list[np.ndarray] = []
        hinge_errors: list[float] = []
        for row in FIXED_ROWS:
            if time.monotonic() > deadline:
                raise TimeoutError("RT00/RT01 active deadline exceeded")
            values = _checkpoint_coordinate_values(
                r2_synthetic, parameters, spec.episode, row * DT,
            )
            state_result = state_owner.apply(values)
            all_state_rows_pass = all_state_rows_pass and bool(state_result["pass"])
            out_of_range.update(state_result["out_of_range"])
            if state_result["requested_error"] > maximum_requested_error:
                maximum_requested_error = float(state_result["requested_error"])
                worst_requested_name = state_result.get("requested_worst")
            if state_result["locked_error"] > maximum_locked_error:
                maximum_locked_error = float(state_result["locked_error"])
                worst_locked_name = state_result.get("locked_worst")
            maximum_coupling_error = max(
                maximum_coupling_error, float(state_result["coupling_error"]),
            )
            rotations: dict[str, np.ndarray] = {}
            world_axes: dict[str, np.ndarray] = {}
            for segment, body_axis in (
                (spec.parent, spec.parent_target),
                (spec.child, spec.child_target),
            ):
                body_rotation = _matrix(
                    state_owner.model.getBodySet().get(
                        body_map[segment],
                    ).getTransformInGround(state_owner.state).R(),
                )
                mount = parameters["mounts"][segment]
                sensor_rotation = body_rotation @ mount
                recovered_mount = body_rotation.T @ sensor_rotation
                quaternion = np.asarray(qmt.quatFromRotMat(sensor_rotation), dtype=np.float64)
                sensor_axis = mount.T @ body_axis
                maximum_inverse = max(
                    maximum_inverse,
                    float(np.linalg.norm(recovered_mount - mount)),
                )
                maximum_quat_matrix = max(
                    maximum_quat_matrix,
                    float(np.linalg.norm(np.asarray(qmt.quatToRotMat(quaternion)) - sensor_rotation)),
                )
                maximum_axis_forward = max(
                    maximum_axis_forward,
                    float(
                        np.linalg.norm(
                            np.asarray(qmt.rotate(quaternion, sensor_axis))
                            - body_rotation @ body_axis
                        )
                    ),
                )
                maximum_quaternion_sign = max(
                    maximum_quaternion_sign,
                    float(
                        np.linalg.norm(
                            np.asarray(qmt.rotate(quaternion, np.eye(3)))
                            - np.asarray(qmt.rotate(-quaternion, np.eye(3)))
                        )
                    ),
                )
                rotations[segment] = body_rotation
                world_axes[segment] = body_rotation @ body_axis
            relative_rotations.append(rotations[spec.parent].T @ rotations[spec.child])
            hinge_errors.append(
                math.acos(
                    float(
                        np.clip(
                            abs(world_axes[spec.parent] @ world_axes[spec.child]),
                            0.0,
                            1.0,
                        )
                    )
                )
            )
        relative_span = max(
            _rotation_distance(relative_rotations[0], rotation)
            for rotation in relative_rotations
        )
        hinge_maximum = max(hinge_errors)
        edge_rows.append({
            "edge": spec.name,
            "episode": spec.episode,
            "coordinate": coordinate_name,
            "center_deg": contract["center_deg"],
            "amplitude_deg": contract["amplitude_deg"],
            "frequency_hz": contract["frequency_hz"],
            "sample_min_rad": float(np.min(coordinate)),
            "sample_max_rad": float(np.max(coordinate)),
            "sample_range_rad": float(np.ptp(coordinate)),
            "coordinate_le_f8_sha256": hashlib.sha256(
                coordinate.tobytes(order="C"),
            ).hexdigest(),
            "relative_motion_span_rad": relative_span,
            "relative_motion_pass": relative_span > 1e-4,
            "hinge_relation_max_rad": hinge_maximum,
            "hinge_relation_pass": hinge_maximum <= TRUTH_LINE_TOLERANCE,
        })
    frame_error = max(
        maximum_inverse,
        maximum_quat_matrix,
        maximum_axis_forward,
        maximum_quaternion_sign,
    )
    root_values = {
        name: np.asarray([
            _checkpoint_coordinate_values(r2_synthetic, parameters, 0, row * DT)[name]
            for row in FIXED_ROWS
        ], dtype=np.float64)
        for name in ROOT_TRANSLATIONS
    }
    return {
        "wall_s": time.monotonic() - started,
        "dimensions": {
            "edges": len(edges),
            "rows_per_edge": len(FIXED_ROWS),
            "coordinate_references_per_assembly": 25,
            "assembly_calls": len(edges) * len(FIXED_ROWS),
            "realize_position_calls": len(edges) * len(FIXED_ROWS),
            "qmt_olsson_calls": 0,
            "opensense_calls": 0,
        },
        "max_errors": {
            "requested_coordinate": maximum_requested_error,
            "requested_worst_name": worst_requested_name,
            "locked_default": maximum_locked_error,
            "locked_worst_name": worst_locked_name,
            "coupler": maximum_coupling_error,
            "mount_inverse_fro": maximum_inverse,
            "quat_matrix_fro": maximum_quat_matrix,
            "axis_forward_l2": maximum_axis_forward,
            "quaternion_sign_l2": maximum_quaternion_sign,
            "out_of_range_names": sorted(out_of_range),
        },
        "root_translation": {
            name: {
                "minimum_m": float(np.min(values)),
                "maximum_m": float(np.max(values)),
                "span_m": float(np.ptp(values)),
                "nonzero_span_pass": float(np.ptp(values)) > 1e-6,
            }
            for name, values in root_values.items()
        },
        "edges": edge_rows,
        "checks": {
            "state_rows": bool(
                all_state_rows_pass
                and not out_of_range
                and maximum_requested_error <= STATE_TOLERANCE
                and maximum_locked_error <= STATE_TOLERANCE
                and maximum_coupling_error <= STATE_TOLERANCE
            ),
            "frame_roundtrip": frame_error <= FRAME_TOLERANCE,
            "all_relative_motion": all(row["relative_motion_pass"] for row in edge_rows),
            "all_hinge_relations": all(row["hinge_relation_pass"] for row in edge_rows),
            "root_translation": all(
                float(np.ptp(values)) > 1e-6 for values in root_values.values()
            ),
        },
    }


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8",
    )


def _tree_hashes(root: Path) -> dict[str, str]:
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _execution_source_binding(repo_root: Path, approved: dict[str, Any]) -> dict[str, Any]:
    authorized = approved["authorized_source"]
    before = authorized["pre_change_sha256"]
    allowed = set(authorized["only_paths_allowed_to_change"])
    observed = {
        relative: hashlib.sha256((repo_root / relative).read_bytes()).hexdigest()
        for relative in before
    }
    unexpected = {
        relative: digest
        for relative, digest in observed.items()
        if relative not in allowed and digest != before[relative]
    }
    if unexpected:
        raise ValueError(f"unapproved source changed: {unexpected}")
    qmt_source = Path(qmt.jointAxisEstHingeOlsson.__code__.co_filename).resolve()
    prior_root = (
        repo_root / "logs/c2_3b_multi_action_registration_synthetic_20260902_204748"
    )
    prior_attempts = {
        name: _tree_hashes(prior_root / name)
        for name in ("axis_attempt_001", "axis_attempt_002")
    }
    return {
        "schema": "biospur.c2_3b.multi_action.run_source_binding.r5",
        "r4_sha256sums_sha256": approved["gate_sha256"],
        "r5_sha256sums_sha256": approved["state_gate_sha256"],
        "execution_source_sha256": observed,
        "argv": sys.argv,
        "python_executable": sys.executable,
        "python_dont_write_bytecode": os.environ.get("PYTHONDONTWRITEBYTECODE"),
        "pythonpath": os.environ.get("PYTHONPATH"),
        "allowed_changed_paths": sorted(allowed),
        "unapproved_changed_paths": unexpected,
        "qmt_version": "0.2.4",
        "qmt_joint_axis_source_path": str(qmt_source),
        "qmt_joint_axis_source_sha256": hashlib.sha256(qmt_source.read_bytes()).hexdigest(),
        "model_sha256": hashlib.sha256(approved["model_path"].read_bytes()).hexdigest(),
        "prior_attempt_content_sha256_before": prior_attempts,
        "real_c2_executed": False,
        "opensense_executed": False,
        "uwb_consumed": False,
    }


def _finalize_attempt_result(
    repo_root: Path, output_dir: Path, result: dict[str, Any],
    binding: dict[str, Any],
) -> dict[str, Any]:
    prior_root = (
        repo_root / "logs/c2_3b_multi_action_registration_synthetic_20260902_204748"
    )
    after = {
        name: _tree_hashes(prior_root / name)
        for name in ("axis_attempt_001", "axis_attempt_002")
    }
    before = binding["prior_attempt_content_sha256_before"]
    result["prior_attempt_content_sha256_after"] = after
    result["prior_attempts_unchanged"] = after == before
    if not result["prior_attempts_unchanged"]:
        result["pass"] = False
        result["first_gate"] = "PRIOR_EVIDENCE_MUTATION"
    _write_json(output_dir / "AXIS_STAGE_RESULT.json", result)
    return result


def _run_seed(repo_root_text: str, output_dir_text: str, seed: int) -> list[dict[str, Any]]:
    import opensim as osim

    repo_root = Path(repo_root_text)
    output_dir = Path(output_dir_text)
    started = time.monotonic()
    approved = load_approved_contract(repo_root)
    r2 = repo_root / "logs/c2_3b_multi_action_registration_precode_revision_20260902_180947"
    r2_synthetic = _load(r2 / "SYNTHETIC_FIXTURE_SPEC.json")
    r2_static = _load(r2 / "STATIC_REGISTRATION_AND_PHYSICAL_GATES.json")
    r2_mechanism = _load(r2 / "MECHANISM_AND_API_CONTRACT.json")
    edges = _edge_specs(r2_mechanism, r2_static)
    settings = dict(r2_static["axis_fit"]["settings"])
    settings["x0"] = np.asarray(settings["x0"], dtype=np.float64)
    log_path = output_dir / f"seed_{seed}_opensim_axis_generation.log"
    osim.Logger.setLevelString("error")
    osim.Logger.removeFileSink()
    osim.Logger.addFileSink(str(log_path.resolve()))
    model = osim.Model(str(approved["model_path"]))
    enabled = tuple(r2_synthetic["model_truth"]["enabled_coordinates"])
    state_owner = OfficialSyntheticState(osim, model, enabled)
    parameters = _seed_parameters(
        seed, model, state_owner.state, r2_synthetic, r2_static, approved["donning"],
    )
    fits_by_variant: dict[str, dict[str, dict[str, Any]]] = {}
    saturation_by_variant: dict[str, bool] = {}
    for variant in UNIQUE_AXIS_VARIANTS:
        mounts = parameters.mounts_roll90 if variant == "POS04_HIGH_OOP_EXACT_ROLL" else parameters.mounts_base
        fits: dict[str, dict[str, Any]] = {}
        saturated = False
        for spec in edges:
            signals, episode_saturation = _episode_signals(
                model, state_owner, spec.episode, variant, (spec.parent, spec.child),
                parameters, r2_synthetic, r2_static,
            )
            saturated = saturated or episode_saturation
            fits[spec.name] = _fit_edge(spec, signals, settings, mounts)
        fits_by_variant[variant] = fits
        saturation_by_variant[variant] = saturated

    base_fits = fits_by_variant["BASE"]
    summaries = []
    branch_path = output_dir / f"seed_{seed}_branch_rows.jsonl"
    with branch_path.open("w", encoding="utf-8") as stream:
        for profile_index, (profile_id, sigma) in enumerate(zip(PROFILE_IDS, PROFILE_SIGMA_RAD, strict=True)):
            internal_count = 0
            for branch_id in range(512):
                result = _solve_branch(branch_id, profile_id, sigma, edges, base_fits, approved["donning"], parameters.mounts_base)
                result.update({
                    "logical_run_index": profile_index * 4664 + SEEDS.index(seed) * 512 + branch_id,
                    "seed": seed,
                    "case_id": "BRANCH_SWEEP_POS01",
                    "generator_valid": not saturation_by_variant["BASE"],
                })
                internal_count += int(result["internal_eligible"])
                stream.write(json.dumps(result, sort_keys=True) + "\n")
            summaries.append({
                "seed": seed,
                "profile": profile_id,
                "internal_eligible_branches": internal_count,
                "expected_internal_eligible_branches": 16,
                "axis_all_eligible": all(fit["eligible"] for fit in base_fits.values()),
                "generator_saturation": saturation_by_variant["BASE"],
            })
    per_variant = {}
    for variant, fits in fits_by_variant.items():
        per_variant[variant] = {
            "all_edges_eligible": all(fit["eligible"] for fit in fits.values()),
            "saturated": saturation_by_variant[variant],
            "fits": fits,
        }
    all_axis_variants_eligible = _all_axis_variants_eligible(per_variant)
    for summary in summaries:
        summary["per_variant_all_edges_eligible"] = {
            variant: row["all_edges_eligible"]
            for variant, row in per_variant.items()
        }
        summary["per_variant_generator_saturation"] = {
            variant: row["saturated"]
            for variant, row in per_variant.items()
        }
        summary["all_axis_variants_eligible"] = all_axis_variants_eligible
        summary["axis_all_eligible"] = all_axis_variants_eligible
    (output_dir / f"seed_{seed}_axis_fits.json").write_text(json.dumps(per_variant, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (output_dir / f"seed_{seed}_worker_result.json").write_text(json.dumps({
        "seed": seed,
        "wall_s": time.monotonic() - started,
        "summaries": summaries,
    }, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return summaries


def run_axis_stage(repo_root: Path, output_dir: Path) -> dict[str, Any]:
    """Run only the approved R5 checkpoints, stopping fail-closed at first failure."""

    started = time.monotonic()
    approved = load_approved_contract(repo_root)
    output_dir.mkdir(parents=True, exist_ok=False)
    import opensim as osim

    binding = _execution_source_binding(repo_root, approved)
    binding["opensim_version"] = osim.GetVersionAndDate()
    _write_json(output_dir / "RUN_EXECUTION_SOURCE_BINDING.json", binding)

    r2 = repo_root / "logs/c2_3b_multi_action_registration_precode_revision_20260902_180947"
    r2_synthetic = _load(r2 / "SYNTHETIC_FIXTURE_SPEC.json")
    r2_static = _load(r2 / "STATIC_REGISTRATION_AND_PHYSICAL_GATES.json")
    r2_mechanism = _load(r2 / "MECHANISM_AND_API_CONTRACT.json")
    edges = _edge_specs(r2_mechanism, r2_static)
    edge_contracts = {
        row["edge"]: row for row in approved["roundtrip"]["edges_in_order"]
    }
    if [edge.name for edge in edges] != list(edge_contracts):
        raise ValueError("R5 edge order does not match production identity")
    enabled = tuple(r2_synthetic["model_truth"]["enabled_coordinates"])
    expected_enabled = tuple(
        approved["state_binding"]["exact_state_initialization"]
        ["enabled_rotational_coordinates_in_order"]
    )
    if enabled != expected_enabled:
        raise ValueError("enabled coordinate order changed")

    osim.Logger.setLevelString("error")
    osim.Logger.removeFileSink()
    osim.Logger.addFileSink(str((output_dir / "opensim_state_frame.log").resolve()))
    model_load_started = time.monotonic()
    model = osim.Model(str(approved["model_path"]))
    model_load_wall = time.monotonic() - model_load_started
    owner_started = time.monotonic()
    state_owner = OfficialSyntheticState(osim, model, enabled)
    owner_initialization_wall = time.monotonic() - owner_started
    parameters = _checkpoint_seed_parameters(101, approved["donning"])

    rt00_started = time.monotonic()
    rt00 = _state_frame_checkpoint(
        state_owner,
        [next(edge for edge in edges if edge.name == "elbow_left")],
        edge_contracts,
        parameters,
        r2_synthetic,
        r2_static,
        rt00_started + 30.0,
    )
    rt00["id"] = "RT00_ELBOW_LEFT_STATE_FRAME"
    rt00["model_load_wall_s"] = model_load_wall
    rt00["owner_initialization_wall_s"] = owner_initialization_wall
    rt00["total_stage_wall_s"] = time.monotonic() - started
    rt00["wall_limit_s"] = 30.0
    rt00["contract_execution_pass"] = all(
        rt00["checks"][name]
        for name in ("state_rows", "frame_roundtrip", "all_relative_motion", "root_translation")
    )
    rt00["qualification_pass"] = bool(
        rt00["contract_execution_pass"]
        and rt00["checks"]["all_hinge_relations"]
        and rt00["total_stage_wall_s"] <= rt00["wall_limit_s"]
    )
    _write_json(output_dir / "RT00_ELBOW_LEFT_STATE_FRAME.json", rt00)

    if not rt00["qualification_pass"]:
        result = {
            "schema": "biospur.c2_3b.multi_action.synthetic_axis_stage.r5",
            "approved_r4_sha256": approved["gate_sha256"],
            "approved_r5_sha256": approved["state_gate_sha256"],
            "stopped_at": "RT00_ELBOW_LEFT_STATE_FRAME",
            "first_gate": "STATE_FRAME_OR_RUNTIME",
            "branch_rows": 0,
            "qmt_olsson_calls": 0,
            "all_seed_sweep_started": False,
            "branch_sweep_started": False,
            "wall_s": time.monotonic() - started,
            "pass": False,
            "real_c2_executed": False,
            "opensense_executed": False,
            "uwb_consumed": False,
        }
        return _finalize_attempt_result(
            repo_root, output_dir, result, binding,
        )

    rt00_incremental_per_row = rt00["wall_s"] / len(FIXED_ROWS)
    rt01_prediction = (
        model_load_wall + rt00_incremental_per_row * len(FIXED_ROWS) * len(edges)
    ) * 1.25
    if rt01_prediction > 120.0 or time.monotonic() - started > 1200.0:
        result = {
            "schema": "biospur.c2_3b.multi_action.synthetic_axis_stage.r5",
            "approved_r4_sha256": approved["gate_sha256"],
            "approved_r5_sha256": approved["state_gate_sha256"],
            "stopped_at": "RT01_PRELAUNCH",
            "first_gate": "FAILED_RUNTIME_PROJECTION_OR_WALL",
            "predicted_wall_s": rt01_prediction,
            "branch_rows": 0,
            "qmt_olsson_calls": 0,
            "all_seed_sweep_started": False,
            "branch_sweep_started": False,
            "wall_s": time.monotonic() - started,
            "pass": False,
            "real_c2_executed": False,
            "opensense_executed": False,
            "uwb_consumed": False,
        }
        return _finalize_attempt_result(
            repo_root, output_dir, result, binding,
        )

    rt01_started = time.monotonic()
    rt01 = _state_frame_checkpoint(
        state_owner,
        edges,
        edge_contracts,
        parameters,
        r2_synthetic,
        r2_static,
        rt01_started + 120.0,
    )
    rt01_per_row = rt01["wall_s"] / (len(FIXED_ROWS) * len(edges))
    scaling_ratio = rt01_per_row / rt00_incremental_per_row
    rt01["id"] = "RT01_ALL_NINE_STATE_FRAME"
    rt01["predicted_wall_s"] = rt01_prediction
    rt01["measured_incremental_wall_s"] = rt01["wall_s"]
    rt01["rt00_incremental_per_row_s"] = rt00_incremental_per_row
    rt01["rt01_incremental_per_row_s"] = rt01_per_row
    rt01["per_row_scaling_ratio"] = scaling_ratio
    rt01["wall_limit_s"] = 120.0
    rt01["contract_execution_pass"] = all(
        rt01["checks"][name]
        for name in ("state_rows", "frame_roundtrip", "all_relative_motion", "root_translation")
    )
    rt01["runtime_pass"] = bool(
        0.5 <= scaling_ratio <= 2.0
        and rt01_prediction <= 120.0
        and rt01["wall_s"] <= 120.0
    )
    rt01["qualification_pass"] = bool(
        rt01["contract_execution_pass"]
        and rt01["checks"]["all_hinge_relations"]
        and rt01["runtime_pass"]
    )
    _write_json(output_dir / "RT01_ALL_NINE_STATE_FRAME.json", rt01)

    if not rt01["qualification_pass"]:
        failed_edges = [
            row["edge"] for row in rt01["edges"]
            if not row["hinge_relation_pass"]
        ]
        result = {
            "schema": "biospur.c2_3b.multi_action.synthetic_axis_stage.r5",
            "approved_r4_sha256": approved["gate_sha256"],
            "approved_r5_sha256": approved["state_gate_sha256"],
            "stopped_at": "RT01_ALL_NINE_STATE_FRAME",
            "first_gate": (
                "FIXED_BODY_LINE_APPLICABILITY"
                if failed_edges else "STATE_FRAME_OR_RUNTIME"
            ),
            "failed_edges": failed_edges,
            "branch_rows": 0,
            "qmt_olsson_calls": 0,
            "rt02_started": False,
            "all_seed_sweep_started": False,
            "branch_sweep_started": False,
            "wall_s": time.monotonic() - started,
            "total_attempt_wall_limit_s": 1200.0,
            "pass": False,
            "diagnostic_event": True,
            "terminal_status_claimed": False,
            "real_c2_executed": False,
            "opensense_executed": False,
            "uwb_consumed": False,
        }
        return _finalize_attempt_result(
            repo_root, output_dir, result, binding,
        )

    raise RuntimeError(
        "RT01 unexpectedly qualified; RT02 requires the separately bounded cached "
        "trajectory launch and is not reached by the known approved checkpoint"
    )
