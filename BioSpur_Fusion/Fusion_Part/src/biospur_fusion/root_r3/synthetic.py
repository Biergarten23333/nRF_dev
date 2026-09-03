"""Predeclared transfer and fault diagnostics independent of C1 outcomes."""
from __future__ import annotations

import itertools
import math

import numpy as np

from .estimator import RootFilterConfig
from .metrics import pareto_frontier, summary
from .models import PositionObservation
from .replay import run_cv_tracker


def harmonic_metrics(time_s: np.ndarray, truth: np.ndarray, estimate: np.ndarray, frequency_hz: float) -> dict:
    mask = np.isfinite(time_s) & np.isfinite(truth) & np.isfinite(estimate)
    t = np.asarray(time_s, float)[mask]; y = np.asarray(estimate, float)[mask]; x = np.asarray(truth, float)[mask]
    if len(t) < 20:
        return {"gain": None, "phase_delay_s": None, "samples": len(t)}
    omega = 2.0 * math.pi * frequency_hz
    design = np.c_[np.sin(omega * t), np.cos(omega * t), np.ones(len(t))]
    truth_coeff = np.linalg.lstsq(design, x, rcond=None)[0][:2]
    output_coeff = np.linalg.lstsq(design, y, rcond=None)[0][:2]
    truth_amp = float(np.linalg.norm(truth_coeff)); output_amp = float(np.linalg.norm(output_coeff))
    truth_phase = math.atan2(truth_coeff[1], truth_coeff[0])
    output_phase = math.atan2(output_coeff[1], output_coeff[0])
    phase = (output_phase - truth_phase + math.pi) % (2 * math.pi) - math.pi
    return {"gain": output_amp / truth_amp if truth_amp else None,
            "phase_delay_s": -phase / omega, "samples": len(t)}


def _truth(time_s: np.ndarray, frequency_hz: float) -> np.ndarray:
    omega = 2.0 * math.pi * frequency_hz
    return np.c_[0.50 * np.sin(omega * time_s),
                 0.25 * np.sin(omega * time_s + 0.3),
                 0.10 * np.sin(omega * time_s + 0.7)]


def synthetic_observations(frequency_hz: float, *, sigma_xy_m: float, sigma_z_m: float,
                           duration_s: float | None = None, seed: int = 12657410,
                           corruption: dict | None = None, dropout: tuple[float, float] | None = None) -> tuple[list[PositionObservation], np.ndarray, np.ndarray]:
    duration = float(duration_s if duration_s is not None else min(240.0, max(30.0, 6.0 / frequency_hz)))
    measurement = np.arange(0.0, duration, 0.012)
    truth = _truth(measurement, frequency_hz)
    rng = np.random.default_rng(seed)
    noise = np.c_[rng.normal(0.0, sigma_xy_m, len(measurement)),
                  rng.normal(0.0, sigma_xy_m, len(measurement)),
                  rng.normal(0.0, sigma_z_m, len(measurement))]
    measured = truth + noise
    tags = np.arange(len(measurement)) % 10
    if corruption:
        start = int(corruption.get("start_event", len(measurement) // 2))
        duration_events = int(corruption.get("duration_events", 1))
        affected_tags = int(corruption.get("affected_tags", 1))
        axis = int(corruption.get("axis", 0)); amplitude = float(corruption.get("amplitude_m", 1.0))
        active = ((np.arange(len(measurement)) >= start) &
                  (np.arange(len(measurement)) < start + duration_events) & (tags < affected_tags))
        measured[active, axis] += amplitude
    values = []
    for index, time_s in enumerate(measurement):
        if dropout and dropout[0] <= time_s <= dropout[1]:
            continue
        covariance = np.diag([sigma_xy_m**2, sigma_xy_m**2, sigma_z_m**2])
        values.append(PositionObservation(
            float(time_s), float(time_s + 0.014), measured[index], covariance,
            f"T{tags[index]}", (0, 1, 2, 3), "NOMINAL", True, True, index,
        ))
    return values, measurement, truth


def transfer_surface() -> dict:
    frequencies = (0.01, 0.03, 0.10, 0.30, 1.0, 2.0)
    parameter_rows = []
    for process, model, influence in itertools.product(
        (0.25, 0.50, 1.0), ((0.15, 0.30), (0.30, 0.60), (0.60, 1.20)), (0.02, 0.05, 0.10)
    ):
        config = RootFilterConfig(cv_acceleration_noise_mps2_sqrt_hz=process,
                                  maximum_position_influence_m=influence,
                                  nis_limit_3d=16.26623619623813)
        frequency_rows = []
        for frequency in frequencies:
            observations, measurement, truth = synthetic_observations(
                frequency, sigma_xy_m=model[0], sigma_z_m=model[1])
            result, audit = run_cv_tracker(observations, config)
            truth_at_output = _truth(result["time_s"] - 0.014, frequency)
            x_metrics = harmonic_metrics(result["time_s"], truth_at_output[:, 0], result["root_m"][:, 0], frequency)
            z_metrics = harmonic_metrics(result["time_s"], truth_at_output[:, 2], result["root_m"][:, 2], frequency)
            frequency_rows.append({"frequency_hz": frequency, "xy": x_metrics, "z": z_metrics,
                                   "accept_fraction": float(np.mean(result["accepted"])),
                                   "causality": {k: audit[k] for k in ("future_uwb_count", "future_imu_count", "preavailability_output_count")}})
        low = next(row for row in frequency_rows if row["frequency_hz"] == 0.03)
        high = next(row for row in frequency_rows if row["frequency_hz"] == 2.0)
        parameter_rows.append({
            "process_sigma": process, "model_sigma_xy": model[0], "model_sigma_z": model[1],
            "max_influence_m": influence, "frequencies": frequency_rows,
            "low_frequency_gain_error": abs(1.0 - float(low["xy"]["gain"])),
            "high_frequency_gain": float(high["xy"]["gain"]),
            "low_frequency_phase_delay_s": abs(float(low["xy"]["phase_delay_s"])),
        })
    compact = [{key: row[key] for key in ("process_sigma", "model_sigma_xy", "model_sigma_z", "max_influence_m",
                                           "low_frequency_gain_error", "high_frequency_gain", "low_frequency_phase_delay_s")}
               for row in parameter_rows]
    frontier = pareto_frontier(compact, ("low_frequency_gain_error", "high_frequency_gain", "low_frequency_phase_delay_s"))
    return {"schema": "biospur.root_r3.synthetic_transfer.v1", "rows": parameter_rows,
            "pareto_frontier": frontier,
            "note": "Synthetic transfer diagnostics do not substitute for real C1 or external truth."}


def fault_surfaces() -> dict:
    config = RootFilterConfig(cv_acceleration_noise_mps2_sqrt_hz=0.5,
                              maximum_position_influence_m=0.05)
    corruptions = []
    for amplitude, duration, affected, axis in itertools.product((0.25, 0.75, 1.5), (1, 5, 20), (1, 3, 5), (0, 2)):
        observations, _, _ = synthetic_observations(
            0.03, sigma_xy_m=0.30, sigma_z_m=0.60, duration_s=40.0,
            corruption={"amplitude_m": amplitude, "duration_events": duration,
                        "affected_tags": affected, "axis": axis},
        )
        result, audit = run_cv_tracker(observations, config)
        root_norm = np.linalg.norm(result["root_m"], axis=1)
        corruptions.append({
            "amplitude_m": amplitude, "duration_events": duration, "affected_tags": affected,
            "axis": "X" if axis == 0 else "Z", "maximum_root_norm_m": float(np.nanmax(root_norm)),
            "maximum_single_update_influence_m": float(np.nanmax(result["influence_m"])),
            "accept_fraction": float(np.mean(result["accepted"])),
            "future_counts": [audit["future_uwb_count"], audit["future_imu_count"], audit["preavailability_output_count"]],
        })
    dropouts = []
    for duration in (0.12, 0.60, 2.0, 5.0):
        observations, _, _ = synthetic_observations(
            0.03, sigma_xy_m=0.30, sigma_z_m=0.60, duration_s=40.0,
            dropout=(20.0, 20.0 + duration),
        )
        result, _ = run_cv_tracker(observations, config)
        before = int(np.searchsorted(result["time_s"], 20.0) - 1)
        after = int(np.searchsorted(result["time_s"], 20.0 + duration))
        before = max(0, before); after = min(len(result["time_s"]) - 1, after)
        dropouts.append({
            "duration_s": duration,
            "covariance_trace_before_m2": float(np.sum(result["covariance_diag_m2"][before])),
            "covariance_trace_after_m2": float(np.sum(result["covariance_diag_m2"][after])),
            "reentry_jump_m": float(np.linalg.norm(result["root_m"][after] - result["root_m"][max(0, after - 1)])),
            "mode_after": str(result["mode"][after]),
        })
    return {"schema": "biospur.root_r3.synthetic_faults.v1", "corruption_surface": corruptions,
            "dropout_surface": dropouts,
            "fault_classes": ["single_tag", "multi_tag", "burst", "persistent_bias", "poor_z", "correlated_multi_tag"],
            "anchor_fault_status": "STRUCTURALLY_TESTED_BY_SHARED_ANCHOR_HEALTH; REAL_DIRECT_RANGE_PATH_FRAME_BLOCKED"}
