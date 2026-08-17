"""Probabilistic-style cycle segmentation with explicit boundary uncertainty."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np


@dataclass(frozen=True)
class Cycle:
    start_s: float
    peak_s: float
    stop_s: float
    boundary_uncertainty_s: float
    confidence: float


def _smooth(x: np.ndarray, width: int) -> np.ndarray:
    width = max(1, int(width))
    return np.convolve(x, np.ones(width) / width, mode="same")


def _node_signal(node: dict[str, np.ndarray], hz: float = 25.0) -> tuple[np.ndarray, np.ndarray]:
    t = node["timer2_us"].astype(float)
    t = (t - t[0]) * 1e-6
    gyro = node["gyro_raw"].astype(float)
    acc = node["acc_raw"].astype(float)
    energy = np.linalg.norm(gyro - np.median(gyro, axis=0), axis=1)
    energy += 0.15 * np.linalg.norm(acc - np.median(acc, axis=0), axis=1)
    grid = np.arange(0.0, min(30.0, t[-1]) + 0.5 / hz, 1.0 / hz)
    return grid, np.interp(grid, t, energy)


def aggregate_motion(imu: dict[str, dict[str, np.ndarray]], hz: float = 25.0) -> tuple[np.ndarray, np.ndarray]:
    signals = [_node_signal(node, hz) for node in imu.values() if len(node["timer2_us"]) > 20]
    if not signals:
        return np.array([]), np.array([])
    stop = min(x[0][-1] for x in signals)
    grid = np.arange(0.0, stop + 0.5 / hz, 1.0 / hz)
    stack = np.stack([np.interp(grid, t, v) for t, v in signals])
    scale = np.median(np.abs(stack - np.median(stack, axis=1, keepdims=True)), axis=1) + 1e-6
    normalized = stack / scale[:, None]
    return grid, _smooth(np.quantile(normalized, 0.8, axis=0), max(1, int(hz * 0.16)))


def segment_cycles(imu: dict[str, dict[str, np.ndarray]], action_id: str, settings_scale: float = 1.0) -> dict[str, Any]:
    if "still" in action_id:
        return {"action_id": action_id, "cycles": [], "unassigned_intervals": [[0.0, 30.0]], "status": "LOW_DYNAMIC_CONTEXT_NO_CYCLE_ASSUMPTION", "settings_scale": settings_scale}
    t, signal = aggregate_motion(imu)
    if len(t) < 20:
        return {"action_id": action_id, "cycles": [], "unassigned_intervals": [[0.0, 30.0]], "status": "INSUFFICIENT_SIGNAL", "settings_scale": settings_scale}
    baseline = float(np.median(signal))
    mad = float(np.median(np.abs(signal - baseline)) + 1e-9)
    threshold = baseline + 1.3 * settings_scale * mad
    minimum_distance = max(5, int(0.55 / np.median(np.diff(t))))
    candidates = [i for i in range(1, len(signal) - 1) if signal[i] > threshold and signal[i] >= signal[i-1] and signal[i] > signal[i+1]]
    peaks: list[int] = []
    for index in sorted(candidates, key=lambda i: signal[i], reverse=True):
        if all(abs(index - chosen) >= minimum_distance for chosen in peaks):
            peaks.append(index)
    peaks.sort()
    cycles: list[Cycle] = []
    for index in peaks:
        left = index
        right = index
        local = baseline + 0.45 * (signal[index] - baseline)
        while left > 0 and signal[left] > local:
            left -= 1
        while right + 1 < len(signal) and signal[right] > local:
            right += 1
        width = max(0.08, float(t[right] - t[left]))
        prominence = max(0.0, float((signal[index] - threshold) / (abs(threshold) + mad)))
        uncertainty = min(0.35, max(0.04, 0.12 * width + 0.06 / (1.0 + prominence)))
        confidence = float(np.clip(0.45 + 0.25 * prominence, 0.05, 0.98))
        candidate = Cycle(float(t[left]), float(t[index]), float(t[right]), uncertainty, confidence)
        if not cycles or candidate.start_s >= cycles[-1].stop_s:
            cycles.append(candidate)
    assigned = [(c.start_s, c.stop_s) for c in cycles]
    unassigned = []
    cursor = 0.0
    for start, stop in assigned:
        if start - cursor > 0.1:
            unassigned.append([cursor, start])
        cursor = max(cursor, stop)
    if 30.0 - cursor > 0.1:
        unassigned.append([cursor, 30.0])
    return {
        "action_id": action_id,
        "cycles": [c.__dict__ for c in cycles],
        "unassigned_intervals": unassigned,
        "status": "PROBABILISTIC_BOUNDARIES_NOT_MANUAL_TRUTH",
        "threshold": threshold,
        "baseline": baseline,
        "settings_scale": settings_scale,
        "assumed_repetition_count": None,
    }
