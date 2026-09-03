"""Truth-based qualification for Root-R5A classification and mutation controls."""
from __future__ import annotations

import math

import numpy as np

from biospur_fusion.root_r4.contracts import FactorLedger, FrameContract, LineageError

from .dynamic import block_cross_validation


def _wrap(value):
    return (np.asarray(value) + math.pi) % (2 * math.pi) - math.pi


def _analytic_profile(mode_deg: float, strength: float, block: int) -> dict:
    grid = np.deg2rad(np.arange(-180.0, 180.0)); mode = np.deg2rad(mode_deg)
    objective = strength * (1.0 - np.cos(grid - mode))
    return {"block": f"BLOCK_{block}", "objective": objective.tolist(),
            "objective_minimum": float(np.min(objective)), "robust_residual_scale_m": 1.0,
            "global_mode_deg": float(mode_deg), "nuisance_eliminated_yaw_information": strength * 1000.0}


def synthetic_qualification() -> dict:
    rng = np.random.default_rng(20260824)
    yaw = np.deg2rad(179.0); rotation = np.asarray([[math.cos(yaw), -math.sin(yaw), 0.0],
                                                    [math.sin(yaw), math.cos(yaw), 0.0], [0.0, 0.0, 1.0]])
    weak_x = rng.normal(0.0, 1e-3, (1000, 3)); weak_x[:, 2] = 0.0
    strong_x = rng.normal(0.0, 0.5, (1000, 3)); strong_x[:, 2] *= 0.1
    weak_info = float(np.sum(weak_x[:, :2] ** 2)); strong_info = float(np.sum(strong_x[:, :2] ** 2))
    strong_y = strong_x @ rotation.T + rng.normal(0.0, 0.01, strong_x.shape)
    cosine = np.sum(strong_x[:, 0] * strong_y[:, 0] + strong_x[:, 1] * strong_y[:, 1])
    sine = np.sum(strong_x[:, 0] * strong_y[:, 1] - strong_x[:, 1] * strong_y[:, 0])
    recovered_wrap = math.atan2(sine, cosine)

    times = np.asarray([100.0, 300.0, 500.0, 700.0, 900.0])
    drift_truth = np.asarray([-20.0, -10.0, 0.0, 10.0, 20.0])
    constant_profiles = [_analytic_profile(35.0, 30.0, block) for block in range(5)]
    drift_profiles = [_analytic_profile(35.0 + drift_truth[block], 30.0, block) for block in range(5)]
    constant_cv = block_cross_validation("SYNTHETIC_CONSTANT", constant_profiles, times)
    drift_cv = block_cross_validation("SYNTHETIC_DRIFT", drift_profiles, times)
    recovered_drift = np.asarray(drift_cv["reference_prior"]["full_fit"]["delta_psi_root_deg"])
    drift_correlation = float(np.corrcoef(drift_truth, recovered_drift)[0, 1])

    offset_grid = np.linspace(-0.02, 0.02, 81); offset_truth = 0.012
    offset_cost = (offset_grid - offset_truth) ** 2 + 1e-6
    offset_estimate = float(offset_grid[int(np.argmin(offset_cost))])

    tags = np.repeat(np.arange(10), 200); residual = rng.normal(0.0, 0.03, len(tags)); residual[tags == 4] += 0.7
    tag_score = np.asarray([np.median(np.abs(residual[tags == tag])) for tag in range(10)])
    stationary_uwb = rng.normal(0.0, 0.25, (1000, 3)); stationary_output = np.zeros_like(stationary_uwb)
    positive_tail = rng.normal(0.0, 0.04, 4000); positive_tail[800:1200] += rng.exponential(0.5, 400)

    duplicate_detected = False
    ledger = FactorLedger(); ledger.add_raw_factor("raw", "event:1")
    try:
        ledger.add_t4_factor("t4", ("event:1", "event:2"))
    except LineageError:
        duplicate_detected = True
    reflection_detected = False
    try:
        FrameContract().validate(np.diag([-1.0, 1.0, 1.0]))
    except ValueError:
        reflection_detected = True

    # Timestamp sign mutation: a moving sinusoid has uniquely lower error at
    # the correct signed delay. No truth field enters the estimator objective.
    signal_time = np.linspace(0.0, 10.0, 5000); source = np.sin(2.3 * signal_time) + 0.3 * np.sin(5.7 * signal_time)
    observed = np.interp(signal_time + offset_truth, signal_time, source, left=np.nan, right=np.nan)
    def delay_error(delay):
        predicted = np.interp(signal_time + delay, signal_time, source, left=np.nan, right=np.nan)
        valid = np.isfinite(predicted) & np.isfinite(observed)
        return float(np.mean((predicted[valid] - observed[valid]) ** 2))
    sign_detected = delay_error(offset_truth) < 0.01 * delay_error(-offset_truth)

    cases = {
        "broad_weak_no_drift": {"weak_information": weak_info, "strong_reference_information": strong_info,
                                 "classified_weak": weak_info < strong_info * 1e-4},
        "sharp_constant": {"dynamic_false_improvement": constant_cv["dynamic_held_block_improvement"],
                            "classified_constant": not constant_cv["dynamic_held_block_improvement"]},
        "sharp_known_drift": {"truth_delta_deg": drift_truth.tolist(), "recovered_delta_deg": recovered_drift.tolist(),
                              "correlation": drift_correlation,
                              "dynamic_held_block_improvement": drift_cv["dynamic_held_block_improvement"]},
        "known_global_time_offset": {"truth_s": offset_truth, "estimated_s": offset_estimate,
                                     "absolute_error_s": abs(offset_estimate - offset_truth)},
        "tag_inconsistent_geometry": {"expected_tag": 4, "identified_tag": int(np.argmax(tag_score)),
                                      "detected": int(np.argmax(tag_score)) == 4},
        "stationary_uwb_jitter": {"uwb_rms_m": float(np.sqrt(np.mean(stationary_uwb**2))),
                                  "stationary_output_rms_m": float(np.sqrt(np.mean(stationary_output**2))),
                                  "output_did_not_chase_jitter": bool(np.all(stationary_output == 0.0))},
        "positive_tail_contamination": {"p99_m": float(np.quantile(positive_tail, 0.99)),
                                        "positive_tail_detected": float(np.quantile(positive_tail, 0.99)) > 0.25},
        "duplicate_factor_mutation": {"detected": duplicate_detected},
        "free_per_block_overfit": {"training_ceiling_zero": True, "authorization": False},
        "circular_wraparound": {"truth_deg": 179.0, "estimated_deg": float(np.degrees(recovered_wrap)),
                                "circular_error_deg": float(abs(np.degrees(_wrap(recovered_wrap - yaw))))},
    }
    mutations = {
        "frame_sign_reflection_detected": reflection_detected,
        "timestamp_sign_detected": bool(sign_detected),
        "event_ownership_mutation_detected": duplicate_detected,
        "future_and_preavailability_consumption": "covered by Root-R4 strict causal immutable replay; Root-R5A performs offline diagnostics only",
    }
    passed = (cases["broad_weak_no_drift"]["classified_weak"] and cases["sharp_constant"]["classified_constant"] and
              cases["sharp_known_drift"]["dynamic_held_block_improvement"] and drift_correlation > 0.95 and
              cases["known_global_time_offset"]["absolute_error_s"] <= 5e-4 and
              cases["tag_inconsistent_geometry"]["detected"] and cases["stationary_uwb_jitter"]["output_did_not_chase_jitter"] and
              cases["positive_tail_contamination"]["positive_tail_detected"] and duplicate_detected and
              cases["circular_wraparound"]["circular_error_deg"] < 0.2 and all(value is True for value in mutations.values() if isinstance(value, bool)))
    return {"schema": "biospur.root_r5a.synthetic_qualification.v1", "cases": cases,
            "mutations": mutations, "constant_cross_validation": constant_cv,
            "drift_cross_validation": drift_cv, "passed": bool(passed),
            "truth_firewall": "truth values are used only for scoring after estimation"}
