"""Synthetic mission-level transfer/Pareto and candidate eligibility accounting."""
from __future__ import annotations

import itertools
import math

import numpy as np


def _harmonic(time: np.ndarray, truth: np.ndarray, estimate: np.ndarray, frequency: float) -> dict:
    omega = 2 * math.pi * frequency; design = np.c_[np.sin(omega * time), np.cos(omega * time), np.ones(len(time))]
    a = np.linalg.lstsq(design, truth, rcond=None)[0][:2]; b = np.linalg.lstsq(design, estimate, rcond=None)[0][:2]
    gain = float(np.linalg.norm(b) / np.linalg.norm(a)); phase = float((math.atan2(b[1], b[0]) - math.atan2(a[1], a[0]) + math.pi) % (2 * math.pi) - math.pi)
    return {"gain": gain, "gain_error": abs(1.0 - gain), "phase_delay_s": -phase / omega}


def _simulate(alpha: float, cap: float, uwb_sigma: float, z_scale: float, seed: int = 416204) -> dict:
    rng = np.random.default_rng(seed); dt = 0.005; time = np.arange(0.0, 120.0, dt)
    low_f, high_f = 0.03, 2.0
    truth = np.c_[0.8 * np.sin(2 * np.pi * low_f * time) + 0.05 * np.sin(2 * np.pi * high_f * time),
                  0.5 * np.cos(2 * np.pi * low_f * time + 0.2) + 0.04 * np.sin(2 * np.pi * high_f * time + 0.3),
                  0.2 * np.sin(2 * np.pi * low_f * time + 0.7) + 0.02 * np.sin(2 * np.pi * high_f * time)]
    inertial = truth + np.c_[0.0003 * time**2, -0.0002 * time**2, 0.0005 * time**2]
    estimate = np.empty_like(truth); estimate[0] = inertial[0]; corrections = []
    uwb_indices = np.arange(0, len(time), 20); dropout = (time[uwb_indices] >= 55.0) & (time[uwb_indices] <= 60.0)
    uwb_values = truth[uwb_indices] + rng.normal(0.0, [uwb_sigma, uwb_sigma, uwb_sigma * z_scale], (len(uwb_indices), 3))
    cursor = 0; offset = estimate[0] - inertial[0]; reentry = []
    for index in range(1, len(time)):
        estimate[index] = inertial[index] + offset
        while cursor < len(uwb_indices) and uwb_indices[cursor] + 4 <= index:  # 20 ms availability delay
            if not dropout[cursor]:
                innovation = uwb_values[cursor] - estimate[index]; proposed = alpha * innovation
                norm = float(np.linalg.norm(proposed)); applied = proposed if norm <= cap else proposed * cap / norm
                estimate[index] += applied; offset += applied; corrections.append(float(np.linalg.norm(applied)))
                if time[uwb_indices[cursor]] > 60.0 and len(reentry) < 5: reentry.append(float(np.linalg.norm(applied)))
            cursor += 1
    low = _harmonic(time, 0.8 * np.sin(2 * np.pi * low_f * time), estimate[:, 0], low_f)
    high_truth = 0.05 * np.sin(2 * np.pi * high_f * time)
    high = _harmonic(time, high_truth, estimate[:, 0] - 0.8 * np.sin(2 * np.pi * low_f * time), high_f)
    return {"alpha": alpha, "cap_m": cap, "uwb_sigma_xy_m": uwb_sigma, "z_sigma_scale": z_scale,
            "low_frequency": low, "high_frequency": high,
            "root_correction_jitter_m": float(np.std(corrections)), "maximum_event_influence_m": float(max(corrections)),
            "dropout_covariance_proxy": 5.0 * 0.1, "reacquisition_jump_m": float(max(reentry) if reentry else 0.0),
            "future_data_influence": 0}


def frequency_and_pareto() -> tuple[dict, dict]:
    rows = [_simulate(alpha, cap, sigma, z) for alpha, cap, sigma, z in itertools.product(
        (0.02, 0.05, 0.10), (0.02, 0.05), (0.15, 0.30), (2.0, 4.0))]
    dominated = []
    objectives = np.asarray([[row["low_frequency"]["gain_error"], abs(row["low_frequency"]["phase_delay_s"]),
                              row["high_frequency"]["gain_error"], row["root_correction_jitter_m"],
                              row["reacquisition_jump_m"]] for row in rows])
    for i in range(len(rows)):
        dominated.append(bool(any(np.all(objectives[j] <= objectives[i] + 1e-12) and np.any(objectives[j] < objectives[i] - 1e-12)
                                  for j in range(len(rows)) if j != i)))
    frontier = [{**row, "row": index} for index, (row, bad) in enumerate(zip(rows, dominated)) if not bad]
    return ({"schema": "biospur.root_r4.frequency_response.v1", "evidence_class": "SYNTHETIC_TRUTH",
             "rows": rows, "no_production_row_selected": True},
            {"schema": "biospur.root_r4.pareto_frontier.v1", "frontier": frontier,
             "dominated_rows": [index for index, bad in enumerate(dominated) if bad],
             "no_production_row_selected": True})


def candidate_matrix(*, frame_authorized: bool, inertial_synthetic_pass: bool, lineage_closed: bool) -> tuple[dict, dict]:
    rows = [
        ("R4-C0", "Frozen M1 body, fixed root", "COMPLETED", "body-only reference; not global positioning"),
        ("R4-C1", "Experimental inertial root only", "COMPLETED" if inertial_synthetic_pass else "STOPPED",
         "synthetically qualified equations; real C1 unconstrained drift remains unqualified"),
        ("R4-C2", "T4-only common-root", "DIAGNOSTIC_ONLY" if not frame_authorized else "COMPLETED",
         "T4_TRACKER_WITH_M1_GEOMETRY; genuine IMU-T4 disabled when frame unauthorized"),
        ("R4-C3", "Raw-range-only common root", "STOPPED_FRAME_GATE" if not frame_authorized else "COMPLETED",
         "raw likelihood architecture and synthetic path implemented"),
        ("R4-C4", "T4-initialized raw-range fusion", "STOPPED_FRAME_GATE" if not frame_authorized else "COMPLETED",
         "T4 initializer/health context, raw active likelihood"),
        ("R4-C5", "Disjoint lineage-safe hybrid", "STOPPED_FRAME_GATE" if not frame_authorized else "COMPLETED",
         "even epochs raw; odd epochs replacing T4 factors" if lineage_closed else "lineage unavailable"),
        ("R4-F1", "T4 global frame", "EVALUATED_NOT_AUTHORIZED", "global multi-tag/multi-time fit"),
        ("R4-F2", "Raw-range global frame", "EVALUATED_NOT_AUTHORIZED", "raw likelihood with profiled common root"),
        ("R4-F3", "Lineage-safe hybrid frame", "EVALUATED_NOT_AUTHORIZED", "disjoint epochs; no double counting"),
        ("NC-1", "Naive raw+T4 independent", "REJECTED", "REJECTED_NAIVE_T4_RAW_DOUBLE_COUNTING"),
        ("NC-2", "Single-node frame alignment", "DIAGNOSTIC_REJECTED", "SINGLE_NODE_ALIGNMENT_DIAGNOSTIC"),
        ("NC-3", "Per-node world rotations", "STRUCTURALLY_REJECTED", "one common frame invariant"),
        ("NC-4", "Free-scale/reflection", "DIAGNOSTIC_REJECTED", "DIAGNOSTIC_COUNTERFACTUAL_NOT_AUTHORIZED_FRAME"),
    ]
    matrix = {"schema": "biospur.root_r4.candidate_architecture_matrix.v1",
              "rows": [{"candidate": candidate, "architecture": architecture, "status": status, "reason": reason}
                       for candidate, architecture, status, reason in rows]}
    results = {"schema": "biospur.root_r4.candidate_results.v1", "results": matrix["rows"],
               "genuine_lineage_safe_real_c1_imu_uwb_common_root_candidate": None if not frame_authorized else "R4-C5",
               "structurally_supported_candidate": "R4-C5" if lineage_closed else None}
    return matrix, results
