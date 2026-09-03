"""Gauge-fixed constant versus gyro-bias-driven yaw-drift diagnostics."""
from __future__ import annotations

from dataclasses import dataclass
import math

import numpy as np
from scipy.optimize import least_squares, minimize

from biospur_fusion.root_r4.contracts import wrap_degrees

from .constants import (DYNAMIC_BIAS_ABS_MAX_DPS, DYNAMIC_HELD_BLOCK_MEAN_RATIO_MAX,
                        DYNAMIC_HELD_BLOCK_MIN_WINS, DYNAMIC_PRIORS)
from .profiles import profile_value


def _wrap_rad(value: np.ndarray | float):
    return (np.asarray(value) + math.pi) % (2 * math.pi) - math.pi


def _normalized_value(profile: dict, yaw: float) -> float:
    scale = float(profile["robust_residual_scale_m"])
    return max(0.0, (profile_value(profile, yaw) - float(profile["objective_minimum"])) / scale**2)


def constant_fit(profiles: list[dict], training: list[int]) -> dict:
    grid = np.deg2rad(np.arange(-180.0, 180.0, 1.0))
    values = np.asarray([sum(_normalized_value(profiles[index], yaw) for index in training) for yaw in grid])
    centre = float(grid[int(np.argmin(values))]); half = np.deg2rad(1.0)
    result = minimize(lambda x: sum(_normalized_value(profiles[index], float(x[0])) for index in training),
                      np.asarray([centre]), method="Nelder-Mead",
                      options={"xatol": 1e-9, "fatol": 1e-12, "maxiter": 1000})
    yaw = float(_wrap_rad(result.x[0]))
    return {"yaw_rad": yaw, "yaw_deg": wrap_degrees(float(np.degrees(yaw))),
            "training_objective": float(result.fun), "gauge_valid": True, "parameters": 1}


@dataclass(frozen=True)
class DynamicFit:
    psi0: float
    delta: np.ndarray
    bias: np.ndarray
    objective: float
    prior_name: str
    success: bool

    def record(self, times_s: np.ndarray) -> dict:
        aligned = _wrap_rad(self.psi0 + self.delta)
        transition = _wrap_rad(np.diff(self.delta) - self.bias[:-1] * np.diff(times_s))
        return {
            "psi_NM_0_deg": wrap_degrees(float(np.degrees(self.psi0))),
            "delta_psi_root_deg": np.degrees(self.delta).tolist(),
            "aligned_yaw_deg": [wrap_degrees(float(value)) for value in np.degrees(aligned)],
            "b_g_root_dps": np.degrees(self.bias).tolist(),
            "objective": self.objective, "prior": self.prior_name, "success": self.success,
            "gauge": "delta_psi_root(t_ref)=0", "gauge_residual_rad": float(abs(self.delta[0])),
            "maximum_abs_bias_dps": float(np.max(np.abs(np.degrees(self.bias)))),
            "maximum_transition_equation_residual_deg": float(np.max(np.abs(np.degrees(transition)))) if len(transition) else 0.0,
        }


def dynamic_fit(profiles: list[dict], times_s: np.ndarray, training: list[int], prior: dict) -> DynamicFit:
    static = constant_fit(profiles, training)
    modes = np.deg2rad([profile["global_mode_deg"] for profile in profiles])
    delta_init = _wrap_rad(modes - modes[0]); delta_init[0] = 0.0
    bias_init = np.zeros(5)
    for index in range(4):
        bias_init[index] = float(_wrap_rad(delta_init[index + 1] - delta_init[index]) / (times_s[index + 1] - times_s[index]))
    bias_init[-1] = bias_init[-2]
    x0 = np.r_[static["yaw_rad"], delta_init[1:], bias_init]
    sigma_yaw = np.deg2rad(float(prior["yaw_process_sigma_deg"]))
    sigma_bias = np.deg2rad(float(prior["bias_rw_sigma_dps"]))
    bias_bound = np.deg2rad(DYNAMIC_BIAS_ABS_MAX_DPS)

    def unpack(x: np.ndarray) -> tuple[float, np.ndarray, np.ndarray]:
        return float(_wrap_rad(x[0])), np.r_[0.0, _wrap_rad(x[1:5])], x[5:10]

    information = np.asarray([max(1e-9, float(profile.get("nuisance_eliminated_yaw_information", 1.0)))
                              for profile in profiles])
    information /= max(float(np.median(information)), 1e-9)

    def residual_vector(x: np.ndarray) -> np.ndarray:
        psi, delta, bias = unpack(x); value = 0.0
        rows = []
        for index in training:
            rows.append(float(math.sqrt(information[index]) * _wrap_rad(psi + delta[index] - modes[index])))
        dt = np.diff(times_s)
        evolution = _wrap_rad(np.diff(delta) - bias[:-1] * dt)
        rows.extend((evolution / sigma_yaw).tolist())
        rows.extend((np.diff(bias) / sigma_bias).tolist())
        rows.append(float(bias[0] / bias_bound))
        return np.asarray(rows)

    bounds = [(-math.pi, math.pi)] + [(-math.pi, math.pi)] * 4 + [(-bias_bound, bias_bound)] * 5
    starts = [x0]
    candidate = x0.copy(); candidate[0] = float(_wrap_rad(candidate[0] + math.pi)); starts.append(candidate)
    lower = np.asarray([row[0] for row in bounds]); upper = np.asarray([row[1] for row in bounds])
    results = [least_squares(residual_vector, np.clip(start, lower + 1e-10, upper - 1e-10),
                             bounds=(lower, upper), max_nfev=500,
                             xtol=1e-11, ftol=1e-11, gtol=1e-11) for start in starts]
    result = min(results, key=lambda row: float(np.sum(row.fun**2)))
    psi, delta, bias = unpack(result.x)
    return DynamicFit(psi, delta, bias, 0.5 * float(np.sum(result.fun**2)), str(prior["name"]), bool(result.success))


def block_cross_validation(layer: str, profiles: list[dict], times_s: np.ndarray) -> dict:
    if len(profiles) < 5:
        raise ValueError("five independent block profiles required")
    block_profiles = profiles[:5]; prior_rows = []
    for prior in DYNAMIC_PRIORS:
        folds = []
        for held in range(5):
            training = [index for index in range(5) if index != held]
            static = constant_fit(block_profiles, training)
            dynamic = dynamic_fit(block_profiles, times_s, training, prior)
            static_score = _normalized_value(block_profiles[held], static["yaw_rad"])
            dynamic_yaw = float(_wrap_rad(dynamic.psi0 + dynamic.delta[held]))
            dynamic_score = _normalized_value(block_profiles[held], dynamic_yaw)
            folds.append({"held_block": held, "constant_predicted_yaw_deg": static["yaw_deg"],
                          "dynamic_predicted_yaw_deg": wrap_degrees(float(np.degrees(dynamic_yaw))),
                          "held_constant_score": static_score, "held_dynamic_score": dynamic_score,
                          "dynamic_to_constant_ratio": (dynamic_score + 1e-9) / (static_score + 1e-9),
                          "dynamic_wins": dynamic_score < static_score, "dynamic_fit": dynamic.record(times_s)})
        ratios = [row["dynamic_to_constant_ratio"] for row in folds]
        aggregate_ratio = ((float(np.mean([row["held_dynamic_score"] for row in folds])) + 1e-9) /
                           (float(np.mean([row["held_constant_score"] for row in folds])) + 1e-9))
        wins = sum(row["dynamic_wins"] for row in folds)
        full = dynamic_fit(block_profiles, times_s, list(range(5)), prior)
        prior_rows.append({"prior": prior, "folds": folds, "mean_ratio": aggregate_ratio,
                           "mean_of_fold_ratios_diagnostic": float(np.mean(ratios)),
                           "median_ratio": float(np.median(ratios)), "wins": wins,
                           "held_block_improvement_gate": aggregate_ratio <= DYNAMIC_HELD_BLOCK_MEAN_RATIO_MAX and wins >= DYNAMIC_HELD_BLOCK_MIN_WINS,
                           "full_fit": full.record(times_s)})
    reference = next(row for row in prior_rows if row["prior"]["name"] == "REFERENCE")
    plausible = all(row["full_fit"]["maximum_abs_bias_dps"] <= DYNAMIC_BIAS_ABS_MAX_DPS + 1e-9 for row in prior_rows)
    stable = len({row["held_block_improvement_gate"] for row in prior_rows}) == 1
    return {"schema": "biospur.root_r5a.block_cross_validation.v1", "layer": layer,
            "primary_metric": "held-block normalized robust-profile objective; lower is better",
            "prior_sensitivity": prior_rows, "reference_prior": reference,
            "dynamic_held_block_improvement": bool(reference["held_block_improvement_gate"] and stable),
            "dynamic_prior_conclusion_stable": stable, "dynamic_trajectory_within_bias_bound": plausible,
            "real_fusion": False, "diagnostic_only": True}


def trajectory_rows(layer: str, cross_validation: dict, times_s: np.ndarray) -> list[dict]:
    fit = cross_validation["reference_prior"]["full_fit"]
    return [{"layer": layer, "block": index, "time_s": float(times_s[index]),
             "psi_NM_0_deg": fit["psi_NM_0_deg"], "delta_psi_root_deg": fit["delta_psi_root_deg"][index],
             "aligned_yaw_deg": fit["aligned_yaw_deg"][index], "b_g_root_dps": fit["b_g_root_dps"][index],
             "uncertainty_status": "PRIOR_SENSITIVITY_ONLY_NO_EXTERNAL_TRUTH"}
            for index in range(5)]


def profiled_timing_cross_validation(layer: str, profiles_by_offset: dict[float, list[dict]],
                                     times_s: np.ndarray) -> dict:
    """Select one global timing offset on training blocks, then score held block."""

    reference_prior = next(row for row in DYNAMIC_PRIORS if row["name"] == "REFERENCE")
    folds = []
    for held in range(5):
        training = [index for index in range(5) if index != held]
        constant_candidates = []
        dynamic_candidates = []
        for offset in sorted(profiles_by_offset):
            profiles = profiles_by_offset[offset][:5]
            constant = constant_fit(profiles, training)
            dynamic = dynamic_fit(profiles, times_s, training, reference_prior)
            constant_candidates.append((constant["training_objective"], offset, constant, profiles))
            dynamic_candidates.append((dynamic.objective, offset, dynamic, profiles))
        _, c_offset, constant, c_profiles = min(constant_candidates, key=lambda row: (row[0], abs(row[1]), row[1]))
        _, d_offset, dynamic, d_profiles = min(dynamic_candidates, key=lambda row: (row[0], abs(row[1]), row[1]))
        c_score = _normalized_value(c_profiles[held], constant["yaw_rad"])
        d_yaw = float(_wrap_rad(dynamic.psi0 + dynamic.delta[held])); d_score = _normalized_value(d_profiles[held], d_yaw)
        folds.append({"held_block": held, "C1_selected_offset_s": c_offset, "D1_selected_offset_s": d_offset,
                      "C1_held_score": c_score, "D1_held_score": d_score,
                      "D1_to_C1_ratio": (d_score + 1e-9) / (c_score + 1e-9), "D1_wins": d_score < c_score,
                      "D1_dynamic_fit": dynamic.record(times_s)})
    aggregate_ratio = ((float(np.mean([row["D1_held_score"] for row in folds])) + 1e-9) /
                       (float(np.mean([row["C1_held_score"] for row in folds])) + 1e-9))
    wins = sum(row["D1_wins"] for row in folds)
    return {"schema": "biospur.root_r5a.profiled_timing_cv.v1", "layer": layer, "folds": folds,
            "aggregate_D1_to_C1_ratio": aggregate_ratio, "D1_wins": wins,
            "D1_held_block_improvement": aggregate_ratio <= DYNAMIC_HELD_BLOCK_MEAN_RATIO_MAX and wins >= DYNAMIC_HELD_BLOCK_MIN_WINS,
            "timing_is_one_global_training_selected_value_per_fold": True}
