#!/usr/bin/env python3
"""Causal audit of the unmeasured pelvis template and belt-to-pelvis anchor.

This diagnostic does not alter or replace the immutable R3 qualifications.  It
reopens only the frozen calibration episodes through the instrumented bounded
path, profiles the fixed pelvis template, profiles the belt extrinsic, and then
fits a diagnostic alternative in which both sensor-to-hip vectors are constant
capture-local functional joint centres on one rigid pelvis.

The user's approximate 0.38 m bi-iliac breadth is not an estimator factor and
is never interpreted as femoral-head spacing.  It is used only for a qualitative
external-envelope scale check after fitting.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import replace
import json
import math
from pathlib import Path
import sys
from typing import Any, Mapping, Sequence

import numpy as np
from scipy.optimize import least_squares
from scipy.sparse import csr_matrix

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))

import diagnose_pure_imu_v0_thigh_bounds as thigh_diagnostic
import run_pure_imu_v0_physical_graph as product
import run_pure_imu_v0_raw6_heading as legacy

from biospur_fusion.v0.contracts import dump_json, sha256_file
from biospur_fusion.v0.dual_capture import load_protocol
from biospur_fusion.v0.physical_graph import (
    CENTER_COMPONENT_LIMIT_M,
    HEADING_DIMENSION,
    LENGTH_INDICES,
    STATE_DIMENSION,
    PhysicalGraphObjective,
    _block_data_identifiability,
    bounds,
    decode_state,
    numerical_jacobian,
    real_subject_spec,
)
from biospur_fusion.v0.raw6_heading import (
    EDGES,
    SEGMENTS,
    _axis_residual,
    _prepare_b5,
    _prepared_b5_system,
    _rom_residual,
    build_edge_factors,
    headings_to_edges,
)


RUN_REL = Path("logs/pure_imu_v0_pelvis_hip_anchor_causal_audit_20260828T105108Z")
METADATA_SHA256 = "61694f587dbbd46a4dd17b07938036e620da7b046353ff698426ab0b0f4a483a"
PROVENANCE_SHA256 = "13975755014792556e98e3cf19ccbd914d8982018f89ccd2e0d946e80d80a906"
CONTRACT_SHA256 = "37fa29f315b764bd4a7e4d874c85f2611b04cd66e7d888977ca6e45c5f7074fe"
C1_PREFLIGHT_SHA256 = "cad13f02ae5b218c99855f99331bb5a202f64b32acc41cf87cff65b609fbf816"
R3_REL = Path("logs/pure_imu_v0_physical_graph_dynamic_lengths_r3_20260828T091019Z")
R3_SHA = {
    "CAPTURE1": "c8c97e5cba250b658ad2f02eaaa15df0c9240706e66dee85de3e0115ddb1d515",
    "CAPTURE2": "db640ff0e06abe871133c6136af1a32aad3beb3b752fa30e12fd834f9a81e0f1",
}
THIGH_DIAGNOSTIC_REL = Path(
    "logs/pure_imu_v0_thigh_bound_causal_diagnostic_20260828T101119Z/CAUSAL_DIAGNOSTIC.json"
)
THIGH_DIAGNOSTIC_SHA256 = "5925160819ef5f69a6ee42af9c4bdd43f875389ba03494a91847ff18e6330af0"
WIDTH_GRID_M = np.linspace(0.16, 0.36, 9)
HIP_VERTICAL_GRID_M = np.linspace(-0.14, -0.02, 7)
PROFILE_VARIANTS = ("R3_ORIGINAL", "GAUSSIAN_PRIOR_DIAGNOSTIC")
THIGHS = ("thigh_left", "thigh_right")
THIGH_HEADING_INDICES = {
    segment: SEGMENTS[1:].index(segment) for segment in THIGHS
}
LATENT_HIP_START = STATE_DIMENSION
LATENT_STATE_DIMENSION = STATE_DIMENSION + 6
RUN_DISABLED_BY_STAGE_GATE = True


def _jsonable(value: Any) -> Any:
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


def _assert_immutable(path: Path, expected_sha256: str) -> None:
    if not path.exists() or path.stat().st_mode & 0o222:
        raise RuntimeError(f"required immutable artifact absent or writable: {path}")
    if sha256_file(path) != expected_sha256:
        raise RuntimeError(f"required immutable artifact changed: {path}")


def _correlation(covariance: np.ndarray) -> np.ndarray:
    covariance = np.asarray(covariance, dtype=float)
    scale = np.sqrt(np.maximum(0.0, np.diag(covariance)))
    denominator = np.outer(scale, scale)
    output = np.zeros_like(covariance)
    np.divide(covariance, denominator, out=output, where=denominator > 1e-18)
    np.fill_diagonal(output, 1.0)
    return output


def _fit_full(
    objective: PhysicalGraphObjective,
    seed: np.ndarray,
    low: np.ndarray,
    high: np.ndarray,
    sparsity: csr_matrix,
    variant: str,
    *,
    max_nfev: int = 100,
) -> dict[str, Any]:
    fit = least_squares(
        lambda x: thigh_diagnostic._variant_residual(objective, x, variant),
        np.asarray(seed, dtype=float),
        bounds=(low, high),
        jac="2-point",
        jac_sparsity=sparsity,
        tr_solver="lsmr",
        tr_options={"atol": 1e-11, "btol": 1e-11, "maxiter": 500},
        loss="soft_l1" if variant == "R3_ORIGINAL" else "linear",
        f_scale=1.0,
        x_scale="jac",
        max_nfev=max_nfev,
        xtol=1e-9,
        ftol=1e-9,
        gtol=1e-9,
    )
    return {
        "state": fit.x,
        "cost": float(fit.cost),
        "success": bool(fit.success),
        "finite": bool(np.isfinite(fit.x).all()),
        "nfev": int(fit.nfev),
        "optimality": float(fit.optimality),
        "message": str(fit.message),
        "cost_decomposition": thigh_diagnostic._cost_decomposition(objective, fit.x),
    }


def _state_observables(state: np.ndarray) -> dict[str, Any]:
    return {
        "thigh_lengths_m": {
            segment: float(state[LENGTH_INDICES[segment]]) for segment in THIGHS
        },
        "thigh_headings_rad": {
            segment: float(state[THIGH_HEADING_INDICES[segment]]) for segment in THIGHS
        },
        "pelvis_center_sensor_local_m": state[HEADING_DIMENSION:HEADING_DIMENSION + 3].tolist(),
        "pelvis_sensor_from_anatomical_rotvec_rad": state[
            HEADING_DIMENSION + 3:HEADING_DIMENSION + 6
        ].tolist(),
    }


def _template_profile_chain(
    *,
    base_objective: PhysicalGraphObjective,
    seed: np.ndarray,
    low: np.ndarray,
    high: np.ndarray,
    sparsity: csr_matrix,
    parameter: str,
    values: Sequence[float],
    direction: str,
    variant: str,
) -> dict[str, Any]:
    ordered = list(values if direction == "LOW_TO_HIGH" else values[::-1])
    previous = np.asarray(seed, dtype=float).copy()
    points = []
    for value in ordered:
        if parameter == "pelvis_joint_center_width_m":
            spec = replace(base_objective.spec, pelvis_width_m=float(value))
        elif parameter == "hip_vertical_offset_m":
            spec = replace(
                base_objective.spec, pelvis_hip_vertical_offset_m=float(value),
            )
        else:
            raise ValueError(parameter)
        objective = PhysicalGraphObjective(base_objective.factors, spec)
        fit = _fit_full(
            objective, previous, low, high, sparsity, variant, max_nfev=90,
        )
        previous = fit["state"]
        points.append({
            "fixed_value": float(value),
            "direction": direction,
            "solver_cost": fit["cost"],
            "success": fit["success"],
            "finite": fit["finite"],
            "nfev": fit["nfev"],
            "optimality": fit["optimality"],
            "cost": fit["cost_decomposition"],
            "observables": _state_observables(fit["state"]),
            "state_coordinates": fit["state"].tolist(),
        })
        print(
            f"TEMPLATE_PROFILE {parameter} {variant} {direction} "
            f"value={value:.6f} cost={fit['cost']:.6f}", flush=True,
        )
    return {
        "parameter": parameter,
        "variant": variant,
        "direction": direction,
        "points": points,
    }


def _select_template_profile(chains: Sequence[Mapping[str, Any]], variant: str) -> dict[str, Any]:
    candidates: dict[float, list[Mapping[str, Any]]] = {}
    for chain in chains:
        for point in chain["points"]:
            candidates.setdefault(round(float(point["fixed_value"]), 9), []).append(point)
    key = (
        "original_total" if variant == "R3_ORIGINAL"
        else "measurement_robust_prior_gaussian_total"
    )
    points = []
    for value in sorted(candidates):
        rows = candidates[value]
        best = min(rows, key=lambda row: row["cost"][key])
        points.append(dict(best) | {
            "candidate_directions": [row["direction"] for row in rows],
            "selection_objective": key,
        })
    best = min(points, key=lambda row: row["cost"][key])
    return {
        "variant": variant,
        "points": points,
        "profile_minimum_value": best["fixed_value"],
        "profile_minimum_cost": best["cost"][key],
        "profile_minimum_observables": best["observables"],
        "continuation_directions": ["LOW_TO_HIGH", "HIGH_TO_LOW"],
        "nuisance_coordinate_count": STATE_DIMENSION,
        "local_profile_not_global_proof": True,
    }


def _fit_with_fixed_state_coordinate(
    objective: PhysicalGraphObjective,
    seed: np.ndarray,
    low: np.ndarray,
    high: np.ndarray,
    sparsity: csr_matrix,
    fixed_index: int,
    fixed_value: float,
) -> dict[str, Any]:
    free = np.asarray([index for index in range(STATE_DIMENSION) if index != fixed_index])
    reduced_sparsity = sparsity[:, free]

    def restore(values: np.ndarray) -> np.ndarray:
        state = np.empty(STATE_DIMENSION, dtype=float)
        state[free] = values
        state[fixed_index] = fixed_value
        return state

    fit = least_squares(
        lambda values: thigh_diagnostic._variant_residual(
            objective, restore(values), "GAUSSIAN_PRIOR_DIAGNOSTIC",
        ),
        np.asarray(seed, dtype=float)[free],
        bounds=(low[free], high[free]),
        jac="2-point",
        jac_sparsity=reduced_sparsity,
        tr_solver="lsmr",
        tr_options={"atol": 1e-11, "btol": 1e-11, "maxiter": 500},
        loss="linear",
        x_scale="jac",
        max_nfev=90,
        xtol=1e-9,
        ftol=1e-9,
        gtol=1e-9,
    )
    state = restore(fit.x)
    return {
        "state": state,
        "cost": float(fit.cost),
        "success": bool(fit.success),
        "finite": bool(np.isfinite(fit.x).all()),
        "nfev": int(fit.nfev),
        "optimality": float(fit.optimality),
        "cost_decomposition": thigh_diagnostic._cost_decomposition(objective, state),
    }


def _belt_profile(
    *, objective: PhysicalGraphObjective, seed: np.ndarray,
    low: np.ndarray, high: np.ndarray, sparsity: csr_matrix,
    coordinate_index: int, coordinate_name: str,
) -> dict[str, Any]:
    if coordinate_index < HEADING_DIMENSION + 3:
        values = np.linspace(low[coordinate_index], high[coordinate_index], 5)
    else:
        offsets = np.asarray([-0.35, -0.175, 0.0, 0.175, 0.35])
        values = np.clip(seed[coordinate_index] + offsets, low[coordinate_index], high[coordinate_index])
    previous = np.asarray(seed, dtype=float).copy()
    points = []
    for value in values:
        previous[coordinate_index] = float(value)
        fit = _fit_with_fixed_state_coordinate(
            objective, previous, low, high, sparsity,
            coordinate_index, float(value),
        )
        previous = fit["state"]
        points.append({
            "fixed_value": float(value),
            "solver_cost": fit["cost"],
            "success": fit["success"],
            "finite": fit["finite"],
            "nfev": fit["nfev"],
            "optimality": fit["optimality"],
            "cost": fit["cost_decomposition"],
            "observables": _state_observables(fit["state"]),
        })
        print(
            f"BELT_PROFILE {coordinate_name} value={value:.6f} cost={fit['cost']:.6f}",
            flush=True,
        )
    best = min(points, key=lambda row: row["cost"]["measurement_robust_prior_gaussian_total"])
    return {
        "coordinate_index": coordinate_index,
        "coordinate_name": coordinate_name,
        "parameterization_warning": (
            "ROTATION_VECTOR_COMPONENT_PROFILE_NOT_AN_INDEPENDENT_EULER_ANGLE"
            if "rotvec" in coordinate_name else None
        ),
        "points": points,
        "profile_minimum_value": best["fixed_value"],
        "profile_minimum_cost": best["cost"]["measurement_robust_prior_gaussian_total"],
        "profile_minimum_observables": best["observables"],
        "nuisance_coordinate_count": STATE_DIMENSION - 1,
    }


class LatentHipObjective:
    """Diagnostic graph with capture-local constant sensor-to-hip vectors."""

    def __init__(
        self, factors: Mapping[str, Any], base_spec: Any, *,
        width_prior_mean_m: float = 0.24,
        midpoint_z_prior_mean_m: float = -0.06,
    ):
        self.base = PhysicalGraphObjective(factors, base_spec)
        self.spec = base_spec
        self.width_prior_mean_m = float(width_prior_mean_m)
        self.midpoint_z_prior_mean_m = float(midpoint_z_prior_mean_m)
        self.prepared = {
            edge: _prepare_b5(self.base.factors[edge].b5_train)
            for edge, *_ in EDGES
        }

    @staticmethod
    def split(x: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        x = np.asarray(x, dtype=float)
        if x.shape != (LATENT_STATE_DIMENSION,):
            raise ValueError("unexpected latent-hip state dimension")
        return x[:STATE_DIMENSION], x[LATENT_HIP_START:LATENT_HIP_START + 3], x[LATENT_HIP_START + 3:]

    def measurement_residual(self, x: np.ndarray) -> np.ndarray:
        base, hip_left, hip_right = self.split(x)
        decoded = decode_state(base, self.spec)
        headings = np.asarray([decoded["headings"][segment] for segment in SEGMENTS[1:]])
        deltas = headings_to_edges(headings)
        pieces = []
        for edge, *_ in EDGES:
            parent, child = decoded["edge_levers"][edge]
            if edge == "hip_left":
                parent = hip_left
            elif edge == "hip_right":
                parent = hip_right
            matrix, target, weight = _prepared_b5_system(self.prepared[edge], deltas[edge])
            lever = np.concatenate((parent, child))
            pieces.append(weight * (matrix @ lever - target) / 0.35)
            axis = _axis_residual(self.base.factors[edge], deltas[edge])
            if len(axis):
                pieces.append(axis)
            rom = _rom_residual(self.base.factors[edge], deltas[edge])
            if len(rom):
                pieces.append(rom)
        return np.concatenate(pieces)

    def hip_prior_residual(self, x: np.ndarray) -> np.ndarray:
        base, hip_left, hip_right = self.split(x)
        pelvis = decode_state(base, self.spec)["segment_geometry"]["pelvis"]
        frame = np.asarray(pelvis["frame"], dtype=float)
        center = np.asarray(pelvis["center"], dtype=float)
        local_left = frame.T @ (hip_left - center)
        local_right = frame.T @ (hip_right - center)
        midpoint = 0.5 * (local_left + local_right)
        separation = local_right - local_left
        return np.r_[
            (midpoint - np.asarray([0.0, 0.0, self.midpoint_z_prior_mean_m]))
            / np.asarray([0.06, 0.08, 0.07]),
            (separation[0] - self.width_prior_mean_m) / 0.08,
            separation[1] / 0.06,
            separation[2] / 0.05,
        ]

    def prior_residual(self, x: np.ndarray) -> np.ndarray:
        base, _, _ = self.split(x)
        return np.r_[self.base.prior_residual(base), self.hip_prior_residual(x)]

    def hybrid_residual(self, x: np.ndarray) -> np.ndarray:
        return np.r_[
            thigh_diagnostic._soft_l1_pseudo_residual(self.measurement_residual(x)),
            self.prior_residual(x),
        ]


def _latent_bounds(spec: Any) -> tuple[np.ndarray, np.ndarray]:
    low, high = bounds(spec)
    return np.r_[low, np.full(6, -0.30)], np.r_[high, np.full(6, 0.30)]


def _latent_seed(base_state: np.ndarray, spec: Any) -> np.ndarray:
    decoded = decode_state(np.asarray(base_state, dtype=float), spec)
    return np.r_[
        base_state,
        decoded["segment_geometry"]["pelvis"]["points"]["hip_left"],
        decoded["segment_geometry"]["pelvis"]["points"]["hip_right"],
    ]


def _set_latent_anatomical_hips(
    seed: np.ndarray, spec: Any, *, width: float, midpoint_z: float,
    asymmetry: np.ndarray | None = None,
) -> np.ndarray:
    output = np.asarray(seed, dtype=float).copy()
    base = output[:STATE_DIMENSION]
    pelvis = decode_state(base, spec)["segment_geometry"]["pelvis"]
    center = np.asarray(pelvis["center"], dtype=float)
    frame = np.asarray(pelvis["frame"], dtype=float)
    left = np.asarray([-0.5 * width, 0.0, midpoint_z])
    right = np.asarray([0.5 * width, 0.0, midpoint_z])
    if asymmetry is not None:
        left += asymmetry[:3]
        right += asymmetry[3:]
    output[LATENT_HIP_START:LATENT_HIP_START + 3] = center + frame @ left
    output[LATENT_HIP_START + 3:] = center + frame @ right
    return np.clip(output, *_latent_bounds(spec))


def _latent_sparsity(
    objective: LatentHipObjective, seed: np.ndarray,
    low: np.ndarray, high: np.ndarray,
) -> csr_matrix:
    probe = np.clip(
        seed + 0.012 * np.sin(np.arange(LATENT_STATE_DIMENSION) + 0.43),
        low + 1e-8, high - 1e-8,
    )
    first = numerical_jacobian(objective.hybrid_residual, seed, low, high)
    second = numerical_jacobian(objective.hybrid_residual, probe, low, high)
    return csr_matrix((np.abs(first) > 1e-13) | (np.abs(second) > 1e-13))


def _fit_latent(
    objective: LatentHipObjective, seed: np.ndarray,
    low: np.ndarray, high: np.ndarray, sparsity: csr_matrix,
    *, max_nfev: int = 180,
) -> dict[str, Any]:
    fit = least_squares(
        objective.hybrid_residual,
        seed,
        bounds=(low, high),
        jac="2-point",
        jac_sparsity=sparsity,
        tr_solver="lsmr",
        tr_options={"atol": 1e-11, "btol": 1e-11, "maxiter": 700},
        loss="linear",
        x_scale="jac",
        max_nfev=max_nfev,
        xtol=1e-9,
        ftol=1e-9,
        gtol=1e-9,
    )
    measurement = objective.measurement_residual(fit.x)
    base_prior = objective.base.prior_residual(fit.x[:STATE_DIMENSION])
    hip_prior = objective.hip_prior_residual(fit.x)
    return {
        "state": fit.x,
        "cost": float(fit.cost),
        "success": bool(fit.success),
        "finite": bool(np.isfinite(fit.x).all()),
        "nfev": int(fit.nfev),
        "optimality": float(fit.optimality),
        "message": str(fit.message),
        "cost_decomposition": {
            "robust_measurement_soft_l1_cost": thigh_diagnostic._soft_l1_cost(measurement),
            "base_declared_prior_gaussian_cost": float(0.5 * base_prior @ base_prior),
            "latent_hip_broad_prior_gaussian_cost": float(0.5 * hip_prior @ hip_prior),
            "total": float(
                thigh_diagnostic._soft_l1_cost(measurement)
                + 0.5 * base_prior @ base_prior + 0.5 * hip_prior @ hip_prior
            ),
            "latent_hip_prior_standardized_residual": hip_prior.tolist(),
        },
    }


def _latent_geometry(objective: LatentHipObjective, state: np.ndarray) -> dict[str, Any]:
    base, hip_left, hip_right = objective.split(state)
    pelvis = decode_state(base, objective.spec)["segment_geometry"]["pelvis"]
    center = np.asarray(pelvis["center"], dtype=float)
    frame = np.asarray(pelvis["frame"], dtype=float)
    left_local = frame.T @ (hip_left - center)
    right_local = frame.T @ (hip_right - center)
    separation = right_local - left_local
    norm = float(np.linalg.norm(separation))
    return {
        "hip_left_sensor_local_m": hip_left.tolist(),
        "hip_right_sensor_local_m": hip_right.tolist(),
        "hip_left_pelvis_frame_m": left_local.tolist(),
        "hip_right_pelvis_frame_m": right_local.tolist(),
        "hip_midpoint_pelvis_frame_m": (0.5 * (left_local + right_local)).tolist(),
        "hip_separation_vector_pelvis_frame_m": separation.tolist(),
        "hip_center_distance_m": norm,
        "external_biiliac_breadth_m": 0.38,
        "inside_approximate_external_biiliac_bony_envelope": bool(norm < 0.38),
        "envelope_check_semantics": "QUALITATIVE_SCALE_QA_ONLY_NO_UNCERTAINTY_OR_ACCEPTANCE_GATE",
        "biiliac_to_hip_center_numeric_transform_applied": False,
    } | _state_observables(base)


def _latent_identifiability(
    objective: LatentHipObjective, state: np.ndarray,
    low: np.ndarray, high: np.ndarray,
) -> dict[str, Any]:
    jacobian = numerical_jacobian(
        objective.measurement_residual, state, low, high,
    )
    hip_indices = list(range(LATENT_HIP_START, LATENT_STATE_DIMENSION))
    joint_indices = hip_indices + [
        LENGTH_INDICES["thigh_left"], LENGTH_INDICES["thigh_right"],
        THIGH_HEADING_INDICES["thigh_left"], THIGH_HEADING_INDICES["thigh_right"],
    ]
    hip = _block_data_identifiability(jacobian, hip_indices)
    joint = _block_data_identifiability(jacobian, joint_indices)
    hip_sigma = np.asarray(hip["data_only_local_coordinate_sigma_residual_scale"])
    distance = np.minimum(
        state[hip_indices] - low[hip_indices], high[hip_indices] - state[hip_indices],
    ) / (high[hip_indices] - low[hip_indices])
    names = (
        "hip_left_sensor_x", "hip_left_sensor_y", "hip_left_sensor_z",
        "hip_right_sensor_x", "hip_right_sensor_y", "hip_right_sensor_z",
    )
    coordinates = {}
    for index, name in enumerate(names):
        interior = bool(distance[index] >= 0.02)
        if hip["full_rank"] and hip_sigma[index] <= 0.03 and interior:
            evidence = "A"
            semantics = "DYNAMICALLY_IDENTIFIED_FUNCTIONAL_HIP_COORDINATE"
        elif np.isfinite(hip_sigma[index]) and hip_sigma[index] <= 0.10 and interior:
            evidence = "B"
            semantics = "WEAKLY_IDENTIFIED_FUNCTIONAL_HIP_COORDINATE"
        else:
            evidence = "C"
            semantics = "PRIOR_OR_BOUND_DOMINATED_HIP_PROXY_COORDINATE"
        coordinates[name] = {
            "estimate_m": float(state[hip_indices[index]]),
            "data_only_local_sigma_m_residual_scale": float(hip_sigma[index]),
            "distance_from_nearest_bound_fraction": float(distance[index]),
            "bound_active": not interior,
            "evidence_class": evidence,
            "estimate_semantics": semantics,
        }
    covariance = np.asarray(joint["local_covariance_residual_scale"])
    names_joint = list(names) + [
        "thigh_left_length", "thigh_right_length",
        "thigh_left_heading", "thigh_right_heading",
    ]
    correlation = _correlation(covariance)
    tradeoffs = []
    for hip_index, hip_name in enumerate(names):
        for target_index in range(6, 10):
            tradeoffs.append({
                "hip_coordinate": hip_name,
                "target": names_joint[target_index],
                "local_measurement_only_correlation": float(correlation[hip_index, target_index]),
            })
    tradeoffs.sort(key=lambda row: abs(row["local_measurement_only_correlation"]), reverse=True)
    thighs = {}
    for position, segment in enumerate(THIGHS):
        sigma = float(joint["data_only_local_coordinate_sigma_residual_scale"][6 + position])
        estimate = float(state[LENGTH_INDICES[segment]])
        lower_bound, upper_bound = low[LENGTH_INDICES[segment]], high[LENGTH_INDICES[segment]]
        interior = min(estimate - lower_bound, upper_bound - estimate) / (upper_bound - lower_bound) >= 0.02
        hips_all_a = all(row["evidence_class"] == "A" for row in coordinates.values())
        dynamically_identified = bool(hips_all_a and sigma <= 0.03 and interior)
        thighs[segment] = {
            "estimate_m": estimate,
            "data_only_local_sigma_m_residual_scale_conditional_joint_block": sigma,
            "interior_optimum": bool(interior),
            "all_six_hip_coordinates_class_A": hips_all_a,
            "dynamically_identified_after_hip_audit": dynamically_identified,
            "estimate_semantics": (
                "RAW_DYNAMICS_IDENTIFIED_LENGTH_WITH_IDENTIFIED_HIP_ANCHORS"
                if dynamically_identified else
                "CONDITIONAL_OR_PRIOR_ANCHORED_LENGTH_NOT_A_STANDALONE_IMU_ESTIMATE"
            ),
        }
    return {
        "measurement_jacobian_shape": list(jacobian.shape),
        "latent_hip_block": hip,
        "combined_hip_thigh_length_heading_block": joint,
        "coordinate_evidence": coordinates,
        "thigh_length_identifiability_conditional_on_hip": thighs,
        "joint_target_coordinate_names": names_joint,
        "joint_target_measurement_only_correlation": correlation.tolist(),
        "strongest_absolute_hip_to_thigh_tradeoffs": tradeoffs[:16],
        "prior_rows_counted_as_data_information": False,
    }


def _prior_sensitivity(
    selected: np.ndarray, factors: Mapping[str, Any], spec: Any,
    low: np.ndarray, high: np.ndarray, sparsity: csr_matrix,
) -> dict[str, Any]:
    perturbations = {
        "width_minus_one_sigma": (0.16, -0.06),
        "width_plus_one_sigma": (0.32, -0.06),
        "vertical_minus_one_sigma": (0.24, -0.13),
        "vertical_plus_one_sigma": (0.24, 0.01),
    }
    output = {}
    baseline_hips = selected[LATENT_HIP_START:].copy()
    for name, (width, vertical) in perturbations.items():
        objective = LatentHipObjective(
            factors, spec, width_prior_mean_m=width,
            midpoint_z_prior_mean_m=vertical,
        )
        fit = _fit_latent(objective, selected, low, high, sparsity, max_nfev=120)
        output[name] = {
            "width_prior_mean_m": width,
            "midpoint_z_prior_mean_m": vertical,
            "fit_cost": fit["cost"],
            "success": fit["success"],
            "nfev": fit["nfev"],
            "hip_coordinate_shift_m": (fit["state"][LATENT_HIP_START:] - baseline_hips).tolist(),
            "geometry": _latent_geometry(objective, fit["state"]),
        }
    return {
        "method": "FULL_NUISANCE_REFIT_AFTER_PLUS_MINUS_ONE_BROAD_PRIOR_SIGMA_MEAN_SHIFT",
        "perturbations": output,
        "external_biiliac_breadth_used": False,
    }


def _fixed_template_covariance(
    objective: PhysicalGraphObjective, state: np.ndarray,
    low: np.ndarray, high: np.ndarray,
) -> dict[str, Any]:
    jacobian = numerical_jacobian(objective.measurement_residual, state, low, high)
    indices = list(range(HEADING_DIMENSION, HEADING_DIMENSION + 6)) + [
        LENGTH_INDICES["thigh_left"], LENGTH_INDICES["thigh_right"],
        THIGH_HEADING_INDICES["thigh_left"], THIGH_HEADING_INDICES["thigh_right"],
    ]
    block = _block_data_identifiability(jacobian, indices)
    names = [
        "pelvis_center_x", "pelvis_center_y", "pelvis_center_z",
        "pelvis_rotvec_x", "pelvis_rotvec_y", "pelvis_rotvec_z",
        "thigh_left_length", "thigh_right_length",
        "thigh_left_heading", "thigh_right_heading",
    ]
    covariance = np.asarray(block["local_covariance_residual_scale"])
    correlation = _correlation(covariance)
    tradeoffs = []
    for pelvis_index in range(6):
        for target_index in range(6, 10):
            tradeoffs.append({
                "pelvis_extrinsic_coordinate": names[pelvis_index],
                "target": names[target_index],
                "local_measurement_only_correlation": float(correlation[pelvis_index, target_index]),
            })
    tradeoffs.sort(key=lambda row: abs(row["local_measurement_only_correlation"]), reverse=True)
    return {
        "coordinate_names": names,
        "identifiability": block,
        "measurement_only_correlation": correlation.tolist(),
        "strongest_absolute_pelvis_to_thigh_tradeoffs": tradeoffs[:16],
    }


def _write_plot(path: Path, profiles: Mapping[str, Any]) -> None:
    import matplotlib
    matplotlib.use("Agg", force=True)
    import matplotlib.pyplot as plt

    figure, axes = plt.subplots(2, 2, figsize=(14, 9))
    for column, parameter in enumerate(("pelvis_joint_center_width_m", "hip_vertical_offset_m")):
        for row_index, capture in enumerate(("CAPTURE1", "CAPTURE2")):
            axis = axes[row_index, column]
            for variant, color in (("R3_ORIGINAL", "#8b3f3f"), ("GAUSSIAN_PRIOR_DIAGNOSTIC", "#287271")):
                rows = profiles[capture][parameter][variant]["points"]
                x = np.asarray([row["fixed_value"] for row in rows])
                key = "original_total" if variant == "R3_ORIGINAL" else "measurement_robust_prior_gaussian_total"
                y = np.asarray([row["cost"][key] for row in rows])
                axis.plot(x, y - np.min(y), "o-", color=color, label=variant)
            axis.grid(alpha=0.25)
            axis.set_title(f"{capture} · {parameter}")
            axis.set_ylabel("profile cost minus minimum")
            axis.set_xlabel("fixed template value [m]")
            axis.legend(fontsize=8)
    figure.suptitle("Pelvis-template nuisance-reoptimized profiles · diagnostic only")
    figure.tight_layout()
    figure.savefig(path, dpi=170)
    plt.close(figure)


def main() -> None:
    root = ROOT.resolve()
    run_dir = root / RUN_REL
    if RUN_DISABLED_BY_STAGE_GATE:
        raise RuntimeError(
            "STOPPED_BY_USER_STAGE_GATE_2026_08_28; do not restart the exhaustive "
            "pelvis profile. See STOPPED_RUN.json and REPORT.md in the run directory."
        )
    immutable = {
        run_dir / "METADATA_PRESELECTION.json": METADATA_SHA256,
        run_dir / "EXTERNAL_BONY_BREADTH_PROVENANCE.json": PROVENANCE_SHA256,
        run_dir / "PELVIS_HIP_AUDIT_CONTRACT.json": CONTRACT_SHA256,
        run_dir / "CAPTURE1_BOUNDED_ACCESS_PREFLIGHT.json": C1_PREFLIGHT_SHA256,
        root / THIGH_DIAGNOSTIC_REL: THIGH_DIAGNOSTIC_SHA256,
    }
    for path, expected in immutable.items():
        _assert_immutable(path, expected)

    protocol = load_protocol(root)
    selection = legacy._selection(root)
    spec = real_subject_spec()
    thigh_result = json.loads((root / THIGH_DIAGNOSTIC_REL).read_text(encoding="utf-8"))
    r3 = {}
    factors = {}
    objectives = {}
    hybrid_states = {}
    access = {}
    low, high = bounds(spec)

    for capture in ("CAPTURE1", "CAPTURE2"):
        r3_path = root / R3_REL / f"{capture}_RESULT.json"
        _assert_immutable(r3_path, R3_SHA[capture])
        r3[capture] = json.loads(r3_path.read_text(encoding="utf-8"))
        episodes, binding = product._load_capture(
            root, run_dir, capture, protocol["captures"][capture],
            selection["captures"][capture], access_attempt=1,
            preselection_sha256=METADATA_SHA256,
        )
        access_path = run_dir / f"{capture}_PAYLOAD_ACCESS_AUDIT.json"
        if access_path.exists():
            raise FileExistsError(access_path)
        dump_json(access_path, binding); access_path.chmod(0o444)
        access_gates = product._access_gates(binding)
        if not all(access_gates.values()):
            raise RuntimeError(f"{capture}: bounded access gate failed: {access_gates}")
        qmt_actions = legacy._qmt_intended_action_map(selection["captures"][capture])
        factors[capture], factor_audit = build_edge_factors(
            episodes, qmt_intended_actions=qmt_actions,
        )
        objectives[capture] = PhysicalGraphObjective(factors[capture], spec)
        hybrid_states[capture] = np.asarray(
            thigh_result["gaussian_prior_full_fit_diagnostic"][capture]["state_coordinates"],
            dtype=float,
        )
        access[capture] = {
            "artifact": str(access_path),
            "sha256": sha256_file(access_path),
            "gates": access_gates,
            "factor_audit": factor_audit,
            "cross_capture_payload_or_parameter_used": False,
        }
        print(f"PELVIS_AUDIT {capture} bounded factor construction complete", flush=True)

    access_checkpoint = run_dir / "ACCESS_AND_FACTOR_CHECKPOINT.json"
    dump_json(access_checkpoint, _jsonable({
        "schema": "biospur-pure-imu-v0-pelvis-audit-access-factor-checkpoint-v1",
        "access": access,
        "capture_parameter_sharing": False,
    })); access_checkpoint.chmod(0o444)

    sparsity = {}
    for capture in ("CAPTURE1", "CAPTURE2"):
        sparsity[capture] = thigh_diagnostic._structural_sparsity(
            objectives[capture], hybrid_states[capture], low, high,
            "GAUSSIAN_PRIOR_DIAGNOSTIC",
        )
        print(f"PELVIS_AUDIT {capture} structural sparsity complete", flush=True)

    profile_tasks = []
    with ThreadPoolExecutor(max_workers=8) as pool:
        for capture in ("CAPTURE1", "CAPTURE2"):
            r3_state = np.asarray(r3[capture]["physical_graph"]["state_coordinates"], dtype=float)
            for parameter, values in (
                ("pelvis_joint_center_width_m", WIDTH_GRID_M),
                ("hip_vertical_offset_m", HIP_VERTICAL_GRID_M),
            ):
                for variant in PROFILE_VARIANTS:
                    seed = r3_state if variant == "R3_ORIGINAL" else hybrid_states[capture]
                    for direction in ("LOW_TO_HIGH", "HIGH_TO_LOW"):
                        future = pool.submit(
                            _template_profile_chain,
                            base_objective=objectives[capture], seed=seed,
                            low=low, high=high, sparsity=sparsity[capture],
                            parameter=parameter, values=values,
                            direction=direction, variant=variant,
                        )
                        profile_tasks.append((future, capture))
        profile_chains = [(capture, future.result()) for future, capture in profile_tasks]

    profiles: dict[str, Any] = {}
    for capture in ("CAPTURE1", "CAPTURE2"):
        profiles[capture] = {}
        for parameter in ("pelvis_joint_center_width_m", "hip_vertical_offset_m"):
            profiles[capture][parameter] = {}
            for variant in PROFILE_VARIANTS:
                selected = [
                    chain for owner, chain in profile_chains
                    if owner == capture and chain["parameter"] == parameter
                    and chain["variant"] == variant
                ]
                profiles[capture][parameter][variant] = _select_template_profile(selected, variant)
    profile_path = run_dir / "FIXED_TEMPLATE_PROFILES.json"
    dump_json(profile_path, _jsonable(profiles)); profile_path.chmod(0o444)
    plot_path = run_dir / "FIXED_TEMPLATE_PROFILES.png"
    _write_plot(plot_path, profiles); plot_path.chmod(0o444)
    print("PELVIS_AUDIT fixed-template profiles sealed", flush=True)

    belt_profiles: dict[str, Any] = {capture: {} for capture in ("CAPTURE1", "CAPTURE2")}
    coordinate_names = (
        "pelvis_center_x", "pelvis_center_y", "pelvis_center_z",
        "pelvis_rotvec_x", "pelvis_rotvec_y", "pelvis_rotvec_z",
    )
    tasks = []
    with ThreadPoolExecutor(max_workers=8) as pool:
        for capture in ("CAPTURE1", "CAPTURE2"):
            for offset, name in enumerate(coordinate_names):
                index = HEADING_DIMENSION + offset
                future = pool.submit(
                    _belt_profile,
                    objective=objectives[capture], seed=hybrid_states[capture],
                    low=low, high=high, sparsity=sparsity[capture],
                    coordinate_index=index, coordinate_name=name,
                )
                tasks.append((future, capture, name))
        for future, capture, name in tasks:
            belt_profiles[capture][name] = future.result()
    for capture in ("CAPTURE1", "CAPTURE2"):
        belt_profiles[capture]["measurement_only_covariance"] = _fixed_template_covariance(
            objectives[capture], hybrid_states[capture], low, high,
        )
    belt_path = run_dir / "BELT_EXTRINSIC_AUDIT.json"
    dump_json(belt_path, _jsonable(belt_profiles)); belt_path.chmod(0o444)
    print("PELVIS_AUDIT belt-extrinsic audit sealed", flush=True)

    latent_results = {}
    for capture in ("CAPTURE1", "CAPTURE2"):
        objective = LatentHipObjective(factors[capture], spec)
        latent_low, latent_high = _latent_bounds(spec)
        initial = _latent_seed(hybrid_states[capture], spec)
        rng = np.random.default_rng(2026082841 if capture == "CAPTURE1" else 2026082842)
        seeds = [
            ("TEMPLATE_0P24", initial),
            ("WIDTH_0P16", _set_latent_anatomical_hips(initial, spec, width=0.16, midpoint_z=-0.06)),
            ("WIDTH_0P32", _set_latent_anatomical_hips(initial, spec, width=0.32, midpoint_z=-0.06)),
            ("VERTICAL_MINUS_1SIGMA", _set_latent_anatomical_hips(initial, spec, width=0.24, midpoint_z=-0.13)),
            ("VERTICAL_PLUS_1SIGMA", _set_latent_anatomical_hips(initial, spec, width=0.24, midpoint_z=0.01)),
        ]
        for index in range(3):
            asymmetry = rng.normal(0.0, 0.035, 6)
            seeds.append((
                f"ASYMMETRIC_RANDOM_{index + 1}",
                _set_latent_anatomical_hips(
                    initial, spec, width=float(rng.uniform(0.16, 0.32)),
                    midpoint_z=float(rng.uniform(-0.13, 0.01)),
                    asymmetry=asymmetry,
                ),
            ))
        if len(seeds) != 8:
            raise AssertionError("latent multistart contract changed")
        latent_sparsity = _latent_sparsity(
            objective, seeds[0][1], latent_low, latent_high,
        )
        fits = []
        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = {
                pool.submit(
                    _fit_latent, objective, seed, latent_low, latent_high,
                    latent_sparsity,
                ): name for name, seed in seeds
            }
            for future in as_completed(futures):
                name = futures[future]
                fit = future.result()
                fits.append(dict(fit) | {"seed": name})
                print(f"LATENT_HIP {capture} {name} cost={fit['cost']:.6f}", flush=True)
        finite = [fit for fit in fits if fit["finite"]]
        selected = min(finite, key=lambda row: row["cost"])
        fit_summary = [{
            key: value for key, value in fit.items() if key != "state"
        } | {
            "geometry": _latent_geometry(objective, fit["state"]),
        } for fit in fits]
        latent_results[capture] = {
            "selected_seed": selected["seed"],
            "selected_cost": selected["cost"],
            "selected_state_coordinates": selected["state"].tolist(),
            "selected_geometry": _latent_geometry(objective, selected["state"]),
            "selected_cost_decomposition": selected["cost_decomposition"],
            "multistart": fit_summary,
            "multistart_count": len(fits),
            "identifiability": _latent_identifiability(
                objective, selected["state"], latent_low, latent_high,
            ),
            "prior_sensitivity": _prior_sensitivity(
                selected["state"], factors[capture], spec,
                latent_low, latent_high, latent_sparsity,
            ),
            "capture_parameter_sharing": False,
        }
        checkpoint = run_dir / f"{capture}_LATENT_HIP_CHECKPOINT.json"
        dump_json(checkpoint, _jsonable(latent_results[capture])); checkpoint.chmod(0o444)
        print(f"PELVIS_AUDIT {capture} latent-hip audit sealed", flush=True)

    def template_summary(capture: str, parameter: str, variant: str) -> dict[str, Any]:
        row = profiles[capture][parameter][variant]
        return {
            "minimum_value": row["profile_minimum_value"],
            "minimum_cost": row["profile_minimum_cost"],
            "thigh_lengths_at_minimum_m": row["profile_minimum_observables"]["thigh_lengths_m"],
            "thigh_headings_at_minimum_rad": row["profile_minimum_observables"]["thigh_headings_rad"],
        }

    result = {
        "schema": "biospur-pure-imu-v0-pelvis-hip-anchor-causal-audit-v1",
        "r3_results_immutable_and_fail_preserved": True,
        "access": access,
        "current_code_template": {
            "pelvis_width_m": 0.24,
            "pelvis_height_m": 0.12,
            "hip_vertical_offset_m": -0.06,
            "pelvis_torso_vertical_offset_m": 0.06,
            "measurement_status": "UNMEASURED_TEMPLATE_ASSUMPTION",
            "belt_sensor_is_hip_node": False,
        },
        "fixed_template_profile_summary": {
            capture: {
                parameter: {
                    variant: template_summary(capture, parameter, variant)
                    for variant in PROFILE_VARIANTS
                } for parameter in (
                    "pelvis_joint_center_width_m", "hip_vertical_offset_m",
                )
            } for capture in ("CAPTURE1", "CAPTURE2")
        },
        "fixed_template_profiles_artifact": {
            "path": str(profile_path), "sha256": sha256_file(profile_path),
            "plot_path": str(plot_path), "plot_sha256": sha256_file(plot_path),
        },
        "belt_extrinsic_audit_artifact": {
            "path": str(belt_path), "sha256": sha256_file(belt_path),
        },
        "latent_hip_results": latent_results,
        "external_biiliac_breadth": {
            "observation_code": "APPROX_BIILIAC_EXTERNAL_BONY_BREADTH",
            "value_m": 0.38,
            "uncertainty": "UNSPECIFIED_NO_SIGMA_INVENTED",
            "landmarks": "PALPABLE_SUPEROLATERAL_ILIAC_CREST_OR_ILIAC_WING_BONY_PROMINENCES",
            "estimator_factor": False,
            "mapped_to_hip_center_spacing": False,
            "transform_required_but_not_applied": [
                "subject-specific medial offsets to femoral-head centers",
                "vertical and anteroposterior landmark offsets",
                "landmark plane/tape geometry",
                "validated anatomical model or imaging",
            ],
            "role": "QUALITATIVE_EXTERNAL_PELVIS_ENVELOPE_AND_SCALE_QA_ONLY",
        },
        "causal_scope": {
            "capture1_capture2_joint_fit_or_parameter_transfer": False,
            "static_t_pose_or_action_label_metric_factor": False,
            "magnetometer_vendor_orientation_manual_quaternion_or_ik": False,
            "hxx_golf_boxing_capture3_uwb_spatial_opened": False,
            "thresholds_changed": False,
            "r3_overwritten": False,
            "product_or_viewer_unlocked": False,
            "recapture_recommended": False,
        },
    }
    result_path = run_dir / "PELVIS_HIP_CAUSAL_AUDIT.json"
    dump_json(result_path, _jsonable(result)); result_path.chmod(0o444)
    print(json.dumps({
        "result": str(result_path),
        "sha256": sha256_file(result_path),
        "template_profile_summary": result["fixed_template_profile_summary"],
        "latent": {
            capture: {
                "seed": latent_results[capture]["selected_seed"],
                "cost": latent_results[capture]["selected_cost"],
                "hip_center_distance_m": latent_results[capture]["selected_geometry"]["hip_center_distance_m"],
                "thigh_lengths_m": latent_results[capture]["selected_geometry"]["thigh_lengths_m"],
                "hip_coordinate_classes": {
                    name: row["evidence_class"]
                    for name, row in latent_results[capture]["identifiability"]["coordinate_evidence"].items()
                },
            } for capture in ("CAPTURE1", "CAPTURE2")
        },
    }, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
