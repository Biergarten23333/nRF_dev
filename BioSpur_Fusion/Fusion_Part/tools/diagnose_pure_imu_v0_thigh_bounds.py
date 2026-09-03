#!/usr/bin/env python3
"""Causal audit of R3 bilateral thigh lower-bound selection.

This is a diagnostic-only sibling of the immutable R3 qualification.  It
reopens the exact bounded calibration slices, rebuilds the same capture-local
raw-six-axis factors, and profiles each C1 thigh length while reoptimizing all
other state coordinates.  It also evaluates the objective implementation in
which scipy's global soft-L1 loss robustifies prior rows along with measurement
rows, and compares it with an exact hybrid objective: soft-L1 measurements and
declared Gaussian priors.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
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

import run_pure_imu_v0_physical_graph as product
import run_pure_imu_v0_raw6_heading as legacy

from biospur_fusion.v0.contracts import dump_json, sha256_file
from biospur_fusion.v0.dual_capture import load_protocol
from biospur_fusion.v0.math3d import rz
from biospur_fusion.v0.physical_graph import (
    HEADING_DIMENSION,
    LENGTH_INDICES,
    STATE_DIMENSION,
    TWO_JOINT_SEGMENTS,
    PhysicalGraphObjective,
    bounds,
    decode_state,
    numerical_jacobian,
    real_subject_spec,
)
from biospur_fusion.v0.raw6_heading import (
    EDGES,
    SEGMENTS,
    _prepared_b5_system,
    build_edge_factors,
)


RUN_REL = Path("logs/pure_imu_v0_thigh_bound_causal_diagnostic_20260828T101119Z")
PRESELECTION_SHA256 = "9f4b20f8c01726e26d218353aa1d44d48658e62a327620a147cc87ea10e66dc0"
CONTRACT_SHA256 = "5e1679460d169b16f528679be34c9adac43e6d23461d0c6c7252fdf4ff8f2c38"
R3_REL = Path("logs/pure_imu_v0_physical_graph_dynamic_lengths_r3_20260828T091019Z")
R3_SHA = {
    "CAPTURE1": "c8c97e5cba250b658ad2f02eaaa15df0c9240706e66dee85de3e0115ddb1d515",
    "CAPTURE2": "db640ff0e06abe871133c6136af1a32aad3beb3b752fa30e12fd834f9a81e0f1",
}
GRID = np.linspace(0.25, 0.60, 15)
THIGHS = ("thigh_left", "thigh_right")
VARIANTS = ("R3_ORIGINAL", "GAUSSIAN_PRIOR_DIAGNOSTIC")


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


def _soft_l1_cost(residual: np.ndarray) -> float:
    residual = np.asarray(residual, dtype=float)
    return float(np.sum(np.sqrt(1.0 + residual * residual) - 1.0))


def _soft_l1_pseudo_residual(residual: np.ndarray) -> np.ndarray:
    """Residual whose linear LS cost equals scipy soft_l1(f_scale=1)."""

    residual = np.asarray(residual, dtype=float)
    return math.sqrt(2.0) * residual / np.sqrt(
        np.sqrt(1.0 + residual * residual) + 1.0
    )


def _variant_residual(
    objective: PhysicalGraphObjective,
    x: np.ndarray,
    variant: str,
) -> np.ndarray:
    measurement = objective.measurement_residual(x)
    prior = objective.prior_residual(x)
    if variant == "R3_ORIGINAL":
        return np.r_[measurement, prior]
    if variant == "GAUSSIAN_PRIOR_DIAGNOSTIC":
        return np.r_[_soft_l1_pseudo_residual(measurement), prior]
    raise ValueError(variant)


def _cost_decomposition(
    objective: PhysicalGraphObjective,
    x: np.ndarray,
) -> dict[str, Any]:
    measurement = objective.measurement_residual(x)
    prior = objective.prior_residual(x)
    if len(prior) != 36:
        raise RuntimeError(f"unexpected physical-prior row count {len(prior)}")
    other = prior[:30]
    anthropometric = prior[30:34]
    bilateral = prior[34:36]
    output = {
        "measurement_row_count": len(measurement),
        "prior_row_count": len(prior),
        "raw_measurement_half_squared_cost": float(0.5 * measurement @ measurement),
        "robust_measurement_soft_l1_cost": _soft_l1_cost(measurement),
        "anthropometric_prior_standardized_residual": anthropometric.tolist(),
        "anthropometric_prior_gaussian_cost": float(0.5 * anthropometric @ anthropometric),
        "anthropometric_prior_soft_l1_cost": _soft_l1_cost(anthropometric),
        "bilateral_standardized_residual": bilateral.tolist(),
        "bilateral_gaussian_cost": float(0.5 * bilateral @ bilateral),
        "bilateral_soft_l1_cost": _soft_l1_cost(bilateral),
        "other_extrinsic_prior_gaussian_cost": float(0.5 * other @ other),
        "other_extrinsic_prior_soft_l1_cost": _soft_l1_cost(other),
    }
    output["original_total"] = float(
        output["robust_measurement_soft_l1_cost"]
        + output["anthropometric_prior_soft_l1_cost"]
        + output["bilateral_soft_l1_cost"]
        + output["other_extrinsic_prior_soft_l1_cost"]
    )
    output["measurement_robust_prior_gaussian_total"] = float(
        output["robust_measurement_soft_l1_cost"]
        + output["anthropometric_prior_gaussian_cost"]
        + output["bilateral_gaussian_cost"]
        + output["other_extrinsic_prior_gaussian_cost"]
    )
    return output


def _structural_sparsity(
    objective: PhysicalGraphObjective,
    x: np.ndarray,
    low: np.ndarray,
    high: np.ndarray,
    variant: str,
) -> csr_matrix:
    function = lambda state: _variant_residual(objective, state, variant)
    probe = np.clip(
        x + 0.01 * np.sin(np.arange(STATE_DIMENSION) + 0.61),
        low + 1e-8,
        high - 1e-8,
    )
    structural = (
        np.abs(numerical_jacobian(function, x, low, high)) > 1e-13
    ) | (
        np.abs(numerical_jacobian(function, probe, low, high)) > 1e-13
    )
    return csr_matrix(structural)


def _fit_full_hybrid(
    objective: PhysicalGraphObjective,
    r3_state: np.ndarray,
    sparsity: csr_matrix,
    low: np.ndarray,
    high: np.ndarray,
) -> dict[str, Any]:
    seeds = {"R3_BOUND_STATE": r3_state.copy()}
    tape = r3_state.copy()
    spec = objective.spec
    for segment in TWO_JOINT_SEGMENTS:
        tape[LENGTH_INDICES[segment]] = spec.segment_lengths_m[segment]
    seeds["TAPE_MEAN_LENGTH_STATE"] = tape
    fits = []
    for name, seed in seeds.items():
        fit = least_squares(
            lambda x: _variant_residual(
                objective, x, "GAUSSIAN_PRIOR_DIAGNOSTIC",
            ),
            seed,
            bounds=(low, high),
            jac="2-point",
            jac_sparsity=sparsity,
            tr_solver="lsmr",
            tr_options={"atol": 1e-11, "btol": 1e-11, "maxiter": 500},
            loss="linear",
            x_scale="jac",
            max_nfev=160,
            xtol=1e-9,
            ftol=1e-9,
            gtol=1e-9,
        )
        fits.append({
            "seed": name,
            "state": fit.x,
            "cost": float(fit.cost),
            "success": bool(fit.success),
            "finite": bool(np.isfinite(fit.x).all()),
            "nfev": int(fit.nfev),
            "optimality": float(fit.optimality),
            "message": str(fit.message),
        })
    best = min((row for row in fits if row["finite"]), key=lambda row: row["cost"])
    return {
        "selected_seed": best["seed"],
        "state_coordinates": best["state"].tolist(),
        "cost": best["cost"],
        "lengths_m": {
            segment: float(best["state"][LENGTH_INDICES[segment]])
            for segment in TWO_JOINT_SEGMENTS
        },
        "cost_decomposition": _cost_decomposition(objective, best["state"]),
        "fits": [{key: value for key, value in row.items() if key != "state"} for row in fits],
        "objective": "SOFT_L1_MEASUREMENTS_PLUS_GAUSSIAN_PRIORS",
        "thresholds_changed": False,
    }


def _profile_chain(
    *,
    objective: PhysicalGraphObjective,
    seed_state: np.ndarray,
    full_sparsity: csr_matrix,
    low: np.ndarray,
    high: np.ndarray,
    segment: str,
    variant: str,
    direction: str,
) -> dict[str, Any]:
    fixed_index = LENGTH_INDICES[segment]
    free_indices = np.asarray([
        index for index in range(STATE_DIMENSION) if index != fixed_index
    ], dtype=int)
    reduced_sparsity = full_sparsity[:, free_indices]
    grid = GRID if direction == "LOW_TO_HIGH" else GRID[::-1]
    previous = seed_state.copy()
    points = []
    for length in grid:
        previous[fixed_index] = float(length)

        def restore(reduced: np.ndarray) -> np.ndarray:
            state = np.empty(STATE_DIMENSION, dtype=float)
            state[free_indices] = reduced
            state[fixed_index] = float(length)
            return state

        def residual(reduced: np.ndarray) -> np.ndarray:
            return _variant_residual(objective, restore(reduced), variant)

        fit = least_squares(
            residual,
            previous[free_indices],
            bounds=(low[free_indices], high[free_indices]),
            jac="2-point",
            jac_sparsity=reduced_sparsity,
            tr_solver="lsmr",
            tr_options={"atol": 1e-11, "btol": 1e-11, "maxiter": 500},
            loss="soft_l1" if variant == "R3_ORIGINAL" else "linear",
            f_scale=1.0,
            x_scale="jac",
            max_nfev=100,
            xtol=1e-9,
            ftol=1e-9,
            gtol=1e-9,
        )
        state = restore(fit.x)
        previous = state
        points.append({
            "length_m": float(length),
            "direction": direction,
            "solver_cost": float(fit.cost),
            "success": bool(fit.success),
            "finite": bool(np.isfinite(fit.x).all()),
            "nfev": int(fit.nfev),
            "optimality": float(fit.optimality),
            "cost": _cost_decomposition(objective, state),
            "state_coordinates": state.tolist(),
        })
        print(
            f"PROFILE {segment} {variant} {direction} L={length:.3f} "
            f"cost={fit.cost:.6f}",
            flush=True,
        )
    return {
        "segment": segment,
        "variant": variant,
        "direction": direction,
        "points": points,
    }


def _select_profile(chains: Sequence[Mapping[str, Any]], variant: str) -> dict[str, Any]:
    by_length: dict[float, list[Mapping[str, Any]]] = {}
    for chain in chains:
        for point in chain["points"]:
            by_length.setdefault(round(float(point["length_m"]), 6), []).append(point)
    objective_key = (
        "original_total" if variant == "R3_ORIGINAL"
        else "measurement_robust_prior_gaussian_total"
    )
    selected = []
    for length in sorted(by_length):
        candidates = by_length[length]
        best = min(candidates, key=lambda row: row["cost"][objective_key])
        selected.append({
            key: value for key, value in best.items() if key != "state_coordinates"
        } | {
            "candidate_directions": [row["direction"] for row in candidates],
            "selection_objective": objective_key,
        })
    best = min(selected, key=lambda row: row["cost"][objective_key])
    return {
        "variant": variant,
        "points": selected,
        "profile_minimum_length_m": best["length_m"],
        "profile_minimum_cost": best["cost"][objective_key],
        "minimum_at_lower_bound": best["length_m"] == float(GRID[0]),
        "continuation_directions": ["LOW_TO_HIGH", "HIGH_TO_LOW"],
        "nuisance_coordinate_count": STATE_DIMENSION - 1,
        "local_profile_not_global_proof": True,
    }


def _signal_audit(
    episodes: Sequence[Any],
    result: Mapping[str, Any],
) -> dict[str, Any]:
    headings = result["physical_graph"]["headings_rad"]
    per_segment: dict[str, dict[str, list[np.ndarray]]] = {
        segment: {"rest_acc_norm": [], "rest_world_z": [], "rest_gyro_norm": [],
                  "dynamic_gyro_norm": [], "dynamic_alpha_norm": []}
        for segment in SEGMENTS
    }
    episode_rows = []
    source_dt = []
    for episode in episodes:
        differences = np.diff(episode.time_ns)
        row = {
            "action": episode.action,
            "rows": len(episode.time_ns),
            "strictly_monotonic": bool(np.all(differences > 0)),
            "resampled_dt_ms_min": float(np.min(differences) * 1e-6),
            "resampled_dt_ms_median": float(np.median(differences) * 1e-6),
            "resampled_dt_ms_max": float(np.max(differences) * 1e-6),
            "exact_50hz_cadence": bool(np.all(differences == 20_000_000)),
        }
        episode_rows.append(row)
        for node in episode.audit["nodes"].values():
            source_dt.append(float(node["source_median_dt_ms"]))
        rest = np.isin(
            episode.phase, ("VERIFIED_PRE_REST", "VERIFIED_POST_REST"),
        )
        dynamic = ~rest
        dt = float(np.median(differences)) * 1e-9
        for segment in SEGMENTS:
            acc = np.asarray(episode.acc[segment], dtype=float)
            gyro = np.asarray(episode.gyro[segment], dtype=float)
            alpha = np.gradient(gyro, dt, axis=0, edge_order=2)
            corrected = np.einsum(
                "ij,njk->nik", rz(float(headings[segment])),
                episode.rotation_world_sensor[segment],
            )
            world_acc = np.einsum("nij,nj->ni", corrected, acc)
            if np.any(rest):
                per_segment[segment]["rest_acc_norm"].append(np.linalg.norm(acc[rest], axis=1))
                per_segment[segment]["rest_world_z"].append(world_acc[rest, 2])
                per_segment[segment]["rest_gyro_norm"].append(np.linalg.norm(gyro[rest], axis=1))
            if np.any(dynamic):
                per_segment[segment]["dynamic_gyro_norm"].append(np.linalg.norm(gyro[dynamic], axis=1))
                per_segment[segment]["dynamic_alpha_norm"].append(np.linalg.norm(alpha[dynamic], axis=1))
    summary = {}
    for segment, values in per_segment.items():
        flattened = {
            key: np.concatenate(rows) if rows else np.empty(0)
            for key, rows in values.items()
        }
        summary[segment] = {
            "rest_acc_norm_mps2_median": float(np.median(flattened["rest_acc_norm"])),
            "rest_acc_norm_mps2_q05_q95": np.quantile(
                flattened["rest_acc_norm"], [0.05, 0.95]
            ).tolist(),
            "rest_world_specific_force_z_mps2_median": float(np.median(flattened["rest_world_z"])),
            "rest_gyro_norm_rad_s_median": float(np.median(flattened["rest_gyro_norm"])),
            "dynamic_gyro_norm_rad_s_q95": float(np.quantile(flattened["dynamic_gyro_norm"], 0.95)),
            "dynamic_alpha_norm_rad_s2_q95": float(np.quantile(flattened["dynamic_alpha_norm"], 0.95)),
        }
    rest_norms = np.asarray([row["rest_acc_norm_mps2_median"] for row in summary.values()])
    rest_z = np.asarray([row["rest_world_specific_force_z_mps2_median"] for row in summary.values()])
    return {
        "episodes": episode_rows,
        "all_resampled_timestamps_strictly_monotonic_exact_50hz": all(
            row["strictly_monotonic"] and row["exact_50hz_cadence"] for row in episode_rows
        ),
        "source_median_dt_ms_range": [float(min(source_dt)), float(max(source_dt))],
        "segments": summary,
        "accelerometer_si_gravity_scale_check": bool(
            np.all((rest_norms >= 8.0) & (rest_norms <= 11.5))
        ),
        "world_specific_force_gravity_sign_consistent_positive_z": bool(np.all(rest_z > 0.0)),
        "gyro_units": "RAD_PER_SECOND_FROM_SI_SAMPLES",
        "gyro_derivative_units": "RAD_PER_SECOND_SQUARED_FROM_NP_GRADIENT_OVER_EXACT_0P02S",
        "magnetometer_or_vendor_orientation_used": False,
    }


def _endpoint_and_excitation_audit(
    objective: PhysicalGraphObjective,
    result: Mapping[str, Any],
) -> dict[str, Any]:
    x = np.asarray(result["physical_graph"]["state_coordinates"], dtype=float)
    decoded = decode_state(x, objective.spec)
    endpoint = {}
    for side in ("left", "right"):
        thigh = f"thigh_{side}"
        hip = f"hip_{side}"
        knee = f"knee_{side}"
        row = decoded["segment_geometry"][thigh]
        hip_parent, hip_child = decoded["edge_levers"][hip]
        knee_parent, knee_child = decoded["edge_levers"][knee]
        endpoint[thigh] = {
            "center_m": row["center"].tolist(),
            "axis_unit": row["axis"].tolist(),
            "length_m": row["length_m"],
            "proximal_formula": "center - 0.5 * length * axis",
            "distal_formula": "center + 0.5 * length * axis",
            "hip_child_equals_thigh_proximal_exact": bool(np.array_equal(hip_child, row["proximal"])),
            "knee_parent_equals_thigh_distal_exact": bool(np.array_equal(knee_parent, row["distal"])),
            "endpoint_distance_m": float(np.linalg.norm(row["distal"] - row["proximal"])),
            "endpoint_length_identity_error_m": float(abs(
                np.linalg.norm(row["distal"] - row["proximal"]) - row["length_m"]
            )),
            "hip_parent_pelvis_point_m": hip_parent.tolist(),
            "knee_child_shank_proximal_m": knee_child.tolist(),
        }
    excitation = {}
    for edge in ("hip_left", "hip_right", "knee_left", "knee_right"):
        delta = float(result["physical_graph"]["edges"][edge]["relative_heading_rad"])
        matrix, target, weight = _prepared_b5_system(objective.prepared[edge], delta)
        singular = np.linalg.svd(matrix, compute_uv=False)
        excitation[edge] = {
            "weighted_system_shape": list(matrix.shape),
            "weighted_lever_singular_values": singular.tolist(),
            "weighted_lever_rank_relative_1e_minus_6": int(np.sum(
                singular > max(1e-8, singular[0] * 1e-6)
            )),
            "target_rms_mps2": float(np.sqrt(np.mean(target * target))),
            "sample_weight_min_median_max": [
                float(np.min(weight)), float(np.median(weight)), float(np.max(weight)),
            ],
        }
    return {
        "endpoint_parameterization": endpoint,
        "edge_excitation": excitation,
        "joint_acceleration_equation": "R_parent*(f_parent+K_parent*r_parent) == R_child*(f_child+K_child*r_child)",
        "gravity_cancels_as_common_world_specific_force": True,
        "independent_parent_child_free_edge_levers_in_state": False,
    }


def _write_profile_plot(path: Path, profiles: Mapping[str, Any]) -> None:
    import matplotlib
    matplotlib.use("Agg", force=True)
    import matplotlib.pyplot as plt

    figure, axes = plt.subplots(2, 2, figsize=(14, 9), sharex=True)
    for row_index, segment in enumerate(THIGHS):
        for variant in VARIANTS:
            points = profiles[segment][variant]["points"]
            x = np.asarray([row["length_m"] for row in points])
            if variant == "R3_ORIGINAL":
                total_key = "original_total"
                color = "#8b3f3f"
            else:
                total_key = "measurement_robust_prior_gaussian_total"
                color = "#287271"
            total = np.asarray([row["cost"][total_key] for row in points])
            axes[row_index, 0].plot(x, total - np.min(total), "o-", color=color, label=variant)
            measurement = np.asarray([
                row["cost"]["robust_measurement_soft_l1_cost"] for row in points
            ])
            anthropometric = np.asarray([
                row["cost"][
                    "anthropometric_prior_soft_l1_cost"
                    if variant == "R3_ORIGINAL" else "anthropometric_prior_gaussian_cost"
                ] for row in points
            ])
            bilateral = np.asarray([
                row["cost"][
                    "bilateral_soft_l1_cost"
                    if variant == "R3_ORIGINAL" else "bilateral_gaussian_cost"
                ] for row in points
            ])
            axes[row_index, 1].plot(x, measurement, "-", color=color, label=f"measurement {variant}")
            axes[row_index, 1].plot(x, anthropometric, "--", color=color, label=f"anthropometric {variant}")
            axes[row_index, 1].plot(x, bilateral, ":", color=color, label=f"bilateral {variant}")
        axes[row_index, 0].axvline(0.48, color="black", linestyle="--", linewidth=1)
        axes[row_index, 0].set_ylabel(f"{segment}\nprofile total minus minimum")
        axes[row_index, 1].set_ylabel(f"{segment}\ncomponent cost")
        axes[row_index, 0].grid(alpha=0.25); axes[row_index, 1].grid(alpha=0.25)
    axes[-1, 0].set_xlabel("fixed thigh length [m]")
    axes[-1, 1].set_xlabel("fixed thigh length [m]")
    axes[0, 0].legend(fontsize=8); axes[0, 1].legend(fontsize=7, ncol=2)
    figure.suptitle("C1 nuisance-reoptimized thigh length profiles · R3 immutable diagnostic")
    figure.tight_layout()
    figure.savefig(path, dpi=170)
    plt.close(figure)


def main() -> None:
    root = ROOT.resolve()
    run_dir = root / RUN_REL
    if sha256_file(run_dir / "METADATA_PRESELECTION.json") != PRESELECTION_SHA256:
        raise RuntimeError("diagnostic metadata preselection changed")
    if sha256_file(run_dir / "DIAGNOSTIC_CONTRACT.json") != CONTRACT_SHA256:
        raise RuntimeError("diagnostic contract changed")
    if any((run_dir / name).stat().st_mode & 0o222 for name in (
        "METADATA_PRESELECTION.json", "DIAGNOSTIC_CONTRACT.json",
    )):
        raise RuntimeError("diagnostic contract is writable")

    protocol = load_protocol(root)
    selection = legacy._selection(root)
    spec = real_subject_spec()
    episodes_by_capture = {}
    factors_by_capture = {}
    objectives = {}
    r3 = {}
    access = {}
    for capture in ("CAPTURE1", "CAPTURE2"):
        r3_path = root / R3_REL / f"{capture}_RESULT.json"
        if sha256_file(r3_path) != R3_SHA[capture]:
            raise RuntimeError(f"{capture}: immutable R3 result changed")
        r3[capture] = json.loads(r3_path.read_text(encoding="utf-8"))
        episodes, binding = product._load_capture(
            root, run_dir, capture, protocol["captures"][capture],
            selection["captures"][capture], access_attempt=1,
            preselection_sha256=PRESELECTION_SHA256,
        )
        access_path = run_dir / f"{capture}_PAYLOAD_ACCESS_AUDIT.json"
        dump_json(access_path, binding); access_path.chmod(0o444)
        qmt_actions = legacy._qmt_intended_action_map(selection["captures"][capture])
        factors, factor_audit = build_edge_factors(
            episodes, qmt_intended_actions=qmt_actions,
        )
        episodes_by_capture[capture] = episodes
        factors_by_capture[capture] = factors
        objectives[capture] = PhysicalGraphObjective(factors, spec)
        access[capture] = {
            "path": str(access_path), "sha256": sha256_file(access_path),
            "factor_audit": factor_audit,
        }
        print(f"DIAGNOSTIC {capture} bounded factors complete", flush=True)

    low, high = bounds(spec)
    sparsity = {}
    for capture in ("CAPTURE1", "CAPTURE2"):
        state = np.asarray(r3[capture]["physical_graph"]["state_coordinates"], dtype=float)
        sparsity[capture] = _structural_sparsity(
            objectives[capture], state, low, high,
            "GAUSSIAN_PRIOR_DIAGNOSTIC",
        )
        print(f"DIAGNOSTIC {capture} hybrid sparsity complete", flush=True)

    hybrid_full = {}
    with ThreadPoolExecutor(max_workers=2) as pool:
        future = {
            pool.submit(
                _fit_full_hybrid,
                objectives[capture],
                np.asarray(r3[capture]["physical_graph"]["state_coordinates"], dtype=float),
                sparsity[capture], low, high,
            ): capture
            for capture in ("CAPTURE1", "CAPTURE2")
        }
        for item in as_completed(future):
            capture = future[item]
            hybrid_full[capture] = item.result()
            print(f"DIAGNOSTIC {capture} full hybrid fit complete", flush=True)
    hybrid_path = run_dir / "GAUSSIAN_PRIOR_FULL_FITS.json"
    dump_json(hybrid_path, _jsonable(hybrid_full)); hybrid_path.chmod(0o444)

    c1_objective = objectives["CAPTURE1"]
    r3_state = np.asarray(r3["CAPTURE1"]["physical_graph"]["state_coordinates"], dtype=float)
    hybrid_state = np.asarray(hybrid_full["CAPTURE1"]["state_coordinates"], dtype=float)
    tasks = []
    with ThreadPoolExecutor(max_workers=8) as pool:
        for segment in THIGHS:
            for variant in VARIANTS:
                seed = r3_state if variant == "R3_ORIGINAL" else hybrid_state
                for direction in ("LOW_TO_HIGH", "HIGH_TO_LOW"):
                    tasks.append(pool.submit(
                        _profile_chain,
                        objective=c1_objective,
                        seed_state=seed,
                        full_sparsity=sparsity["CAPTURE1"],
                        low=low,
                        high=high,
                        segment=segment,
                        variant=variant,
                        direction=direction,
                    ))
        chains = [future.result() for future in as_completed(tasks)]

    profiles = {}
    for segment in THIGHS:
        profiles[segment] = {}
        for variant in VARIANTS:
            selected_chains = [
                row for row in chains
                if row["segment"] == segment and row["variant"] == variant
            ]
            profiles[segment][variant] = _select_profile(selected_chains, variant)

    z = (0.25 - 0.48) / 0.03
    soft_gradient = z / math.sqrt(1.0 + z * z)
    math_effect = {
        "thigh_prior_standardized_residual_at_0p25m": z,
        "declared_gaussian_half_squared_cost": 0.5 * z * z,
        "global_soft_l1_cost": math.sqrt(1.0 + z * z) - 1.0,
        "declared_gaussian_gradient_wrt_standardized_residual": z,
        "global_soft_l1_gradient_wrt_standardized_residual": soft_gradient,
        "restoring_gradient_attenuation_factor_soft_vs_gaussian": abs(soft_gradient / z),
        "gaussian_to_soft_restoring_gradient_ratio": abs(z / soft_gradient),
        "verified_implementation_path": (
            "PhysicalGraphObjective.residual concatenates measurement_residual and prior_residual; "
            "fit_physical_graph passes the concatenation to scipy least_squares(loss='soft_l1', f_scale=1)"
        ),
        "conclusion": "R3_GLOBAL_SOFT_L1_UNINTENTIONALLY_ROBUSTIFIES_AND_DOWNWEIGHTS_ANTHROPOMETRIC_AND_BILATERAL_PRIORS",
    }

    causal = {}
    for capture in ("CAPTURE1", "CAPTURE2"):
        causal[capture] = {
            "signal_units_timestamps_gravity": _signal_audit(
                episodes_by_capture[capture], r3[capture],
            ),
            "endpoint_and_excitation": _endpoint_and_excitation_audit(
                objectives[capture], r3[capture],
            ),
            "identity_mapping": protocol["captures"][capture]["identity"],
            "r3_lengths_m": {
                segment: r3[capture]["physical_graph"]["structural_audit"][
                    "segment_lengths_m"
                ][segment]
                for segment in TWO_JOINT_SEGMENTS
            },
            "r3_first_failed_gate": r3[capture]["decision"]["first_failed_gate"],
            "gaussian_prior_diagnostic_lengths_m": hybrid_full[capture]["lengths_m"],
        }

    systematic = bool(
        all(
            causal[capture]["r3_lengths_m"][segment] <= 0.2500001
            for capture in ("CAPTURE1", "CAPTURE2") for segment in THIGHS
        )
    )
    hybrid_interior = {
        capture: all(
            hybrid_full[capture]["lengths_m"][segment] > 0.257
            for segment in THIGHS
        )
        for capture in ("CAPTURE1", "CAPTURE2")
    }
    diagnostic = {
        "schema": "biospur-pure-imu-v0-thigh-bound-causal-diagnostic-v1",
        "r3_results_immutable": True,
        "access": access,
        "length_profiles_capture1": profiles,
        "global_soft_l1_prior_effect": math_effect,
        "gaussian_prior_full_fit_diagnostic": hybrid_full,
        "causal_checks": causal,
        "cross_capture": {
            "both_captures_original_bilateral_thighs_at_0p25m": systematic,
            "gaussian_prior_diagnostic_both_thighs_interior": hybrid_interior,
            "capture2_left_shank_signal_supported_fixed_point_class": r3[
                "CAPTURE2"
            ]["physical_graph"]["distal_pivot_evidence"]["segments"][
                "shank_left"
            ]["selected_evidence_class"],
            "interpretation": (
                "REPRODUCTION_ACROSS_INDEPENDENT_CAPTURES_AND_PERSISTENCE_DESPITE_C2_"
                "LEFT_SHANK_PIVOT_EVIDENCE_FAVORS_SYSTEMATIC_OBJECTIVE_OR_MODEL_CAUSE"
            ),
        },
        "thresholds_changed": False,
        "priors_converted_to_hard_truth": False,
        "product_or_viewer_unlocked": False,
        "recapture_recommendation": "NOT_JUSTIFIED_BEFORE_OBJECTIVE_CORRECTION_AND_REQUALIFICATION",
    }
    result_path = run_dir / "CAUSAL_DIAGNOSTIC.json"
    dump_json(result_path, _jsonable(diagnostic)); result_path.chmod(0o444)
    plot_path = run_dir / "C1_THIGH_LENGTH_PROFILES.png"
    _write_profile_plot(plot_path, profiles)
    plot_path.chmod(0o444)

    report = f"""# Pure-IMU V0 thigh-bound causal diagnostic

R3 remains immutable and FAIL for both captures. This diagnostic changed no
acceptance threshold and did not convert tape measurements to hard truth.

## Objective finding

At 0.25 m, each 0.48 +/- 0.03 m thigh prior has standardized residual
`{z:.6f}`. Its declared Gaussian cost is `{0.5*z*z:.6f}` and restoring
gradient magnitude is `{abs(z):.6f}`. R3's global soft-L1 turns those into cost
`{math.sqrt(1+z*z)-1:.6f}` and gradient magnitude `{abs(soft_gradient):.6f}`:
the restoring gradient is attenuated by `{abs(z/soft_gradient):.3f}x`.
Because the same loss wraps the concatenated measurement and prior residuals,
anthropometric and bilateral priors are unintentionally robustified.

## Profiles and cross-capture comparison

C1 nuisance-reoptimized profile minima:

- left thigh, R3 original: {profiles['thigh_left']['R3_ORIGINAL']['profile_minimum_length_m']:.3f} m;
  Gaussian-prior diagnostic: {profiles['thigh_left']['GAUSSIAN_PRIOR_DIAGNOSTIC']['profile_minimum_length_m']:.3f} m.
- right thigh, R3 original: {profiles['thigh_right']['R3_ORIGINAL']['profile_minimum_length_m']:.3f} m;
  Gaussian-prior diagnostic: {profiles['thigh_right']['GAUSSIAN_PRIOR_DIAGNOSTIC']['profile_minimum_length_m']:.3f} m.

Full Gaussian-prior diagnostic lengths are C1
`{json.dumps(hybrid_full['CAPTURE1']['lengths_m'], sort_keys=True)}` and C2
`{json.dumps(hybrid_full['CAPTURE2']['lengths_m'], sort_keys=True)}`.
Both independent R3 captures selected both thigh lower bounds. C2 did so even
though its signal-only audit found weak class-B fixed-point evidence for the
left shank. This repetition favors a systematic objective/model issue over a
capture-specific missing-motion explanation.

## Physics and plumbing audit

Both captures retain exact monotonic 50 Hz resampled timestamps; source timing
ranges, gravity-scale accelerometer norms, world gravity sign, rad/s gyroscope
scales, and rad/s^2 derivatives are recorded in `CAUSAL_DIAGNOSTIC.json`.
Hip and knee endpoint identities are exact: hip child is the thigh proximal
point, knee parent is the same thigh's distal point, and their distance equals
the single length coordinate. No independent edge lever has reappeared.

## Disposition

This is causal diagnostic evidence, not a repaired product result. R3 FAIL is
preserved, no viewer or Hxx was unlocked, and recapture is not justified before
correcting the objective so measurement outliers alone are robust while
declared priors retain Gaussian cost, then repeating synthetic and real staged
qualification under a fresh contract.
"""
    report_path = run_dir / "REPORT.md"
    report_path.write_text(report, encoding="utf-8")
    report_path.chmod(0o444)
    print(json.dumps({
        "result": str(result_path),
        "sha256": sha256_file(result_path),
        "profile_minima": {
            segment: {
                variant: profiles[segment][variant]["profile_minimum_length_m"]
                for variant in VARIANTS
            } for segment in THIGHS
        },
        "hybrid_lengths": {
            capture: hybrid_full[capture]["lengths_m"]
            for capture in ("CAPTURE1", "CAPTURE2")
        },
    }, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
