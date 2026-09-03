"""Shared causal global-Z relative-heading corrector.

The module intentionally has no capture, action, checkpoint, viewer, or camera
input.  It consumes only raw orientation, raw validity/reset state, a causal
stationarity mask, time, topology, and the frozen shared configuration.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

import numpy as np

from pure_imu_baseline.math3d import multiply, normalize

from . import ALGORITHM_ID

OBS_NONE = 0
OBS_STATIONARY_RATE = 1
REASON_PELVIS_GAUGE = 0
REASON_ACTIVE = 1
REASON_INSUFFICIENT_SUPPORT = 2
REASON_RAW_INVALID = 3
REASON_GAP_RESTART = 4
REASON_LOW_CONFIDENCE = 5
REASON_KINEMATIC_WITHHELD = 6


def qz(angle: np.ndarray | float) -> np.ndarray:
    a = np.asarray(angle, dtype=np.float64)
    return np.stack((np.cos(a / 2), np.zeros_like(a), np.zeros_like(a), np.sin(a / 2)), axis=-1)


def spatial_yaw_rate(q0: np.ndarray, q1: np.ndarray, dt: float) -> float:
    """Global-Z component of Log(R1 R0^T)/dt, sign invariant in q."""
    dot = float(np.dot(q0, q1))
    b = -q1 if dot < 0 else q1
    dq = multiply(b, np.array([q0[0], -q0[1], -q0[2], -q0[3]]))
    dq = normalize(dq)
    if dq[0] < 0:
        dq = -dq
    v = dq[1:]
    nv = float(np.linalg.norm(v))
    if nv < 1e-15:
        return 0.0
    angle = 2.0 * np.arctan2(nv, float(dq[0]))
    return float(angle * v[2] / nv / dt)


@dataclass
class NodeState:
    correction: float = 0.0
    bias: float = 0.0
    confidence: float = 0.0
    epoch: int = 0
    support: deque = field(default_factory=deque)
    restart_pending: bool = True


def _subtrees(parent_index: np.ndarray) -> list[list[int]]:
    result: list[list[int]] = []
    for root in range(len(parent_index)):
        children = []
        for node in range(len(parent_index)):
            p = node
            while p >= 0 and p != root:
                p = int(parent_index[p])
            if p == root:
                children.append(node)
        result.append(children)
    return result


def correct(time_s: np.ndarray, raw_q_wxyz: np.ndarray, valid: np.ndarray,
            filter_reset: np.ndarray, stationary: np.ndarray,
            parent_index: np.ndarray, pelvis_index: int, config: dict,
            enabled: bool = True) -> dict[str, np.ndarray]:
    """Execute one deterministic causal pass with independent runtime state."""
    if config.get("algorithm_id") != ALGORITHM_ID:
        raise ValueError("wrong algorithm configuration")
    t = np.asarray(time_s, dtype=np.float64)
    raw = np.asarray(raw_q_wxyz)
    valid = np.asarray(valid, dtype=bool)
    reset = np.asarray(filter_reset, dtype=bool)
    stationary = np.asarray(stationary, dtype=bool)
    if raw.shape[:2] != valid.shape or stationary.shape != valid.shape or reset.shape != valid.shape:
        raise ValueError("shape mismatch")
    if np.any(np.diff(t) <= 0):
        raise ValueError("time must be strictly increasing")
    n, m = valid.shape
    corrected = raw.copy()
    corrections = np.zeros((n, m), dtype=np.float64)
    biases = np.zeros((n, m), dtype=np.float64)
    confidence = np.zeros((n, m), dtype=np.float64)
    states = np.zeros((n, m), dtype=np.uint8)
    reasons = np.full((n, m), REASON_INSUFFICIENT_SUPPORT, dtype=np.uint8)
    observations = np.zeros((n, m), dtype=np.uint8)
    epochs = np.zeros((n, m), dtype=np.uint32)
    z = np.full((n, m), np.nan, dtype=np.float64)
    bcfg = config["bias_estimator"]
    max_window = float(bcfg["window_s"])
    min_samples = int(bcfg["minimum_samples"])
    min_support = float(bcfg["minimum_support_s"])
    max_bias = float(bcfg["maximum_abs_bias_rad_s"])
    max_step = float(bcfg["maximum_bias_step_rad_s"])
    min_conf = float(bcfg["minimum_active_confidence"])
    decay = float(bcfg["confidence_decay_s"])
    max_corr = float(config["correction"]["maximum_abs_correction_rad"])
    tree = _subtrees(np.asarray(parent_index, dtype=int))
    runtime = [NodeState() for _ in range(m)]

    def restart(which: list[int]) -> None:
        for j in which:
            s = runtime[j]
            s.correction = 0.0; s.bias = 0.0; s.confidence = 0.0
            s.support.clear(); s.epoch += 1; s.restart_pending = True

    for k in range(n):
        dt = 0.0 if k == 0 else float(t[k] - t[k-1])
        root_fault = (reset[k, pelvis_index] or
                      (k > 0 and valid[k, pelvis_index] != valid[k-1, pelvis_index]))
        if root_fault:
            restart(list(range(m)))
        else:
            for j in range(m):
                if j == pelvis_index:
                    continue
                if reset[k, j] or (k > 0 and valid[k, j] != valid[k-1, j]):
                    restart(tree[j])

        # Apply only the state supported by samples strictly before this frame.
        pelvis = runtime[pelvis_index]
        for j, s in enumerate(runtime):
            if j == pelvis_index:
                s.correction = 0.0
            elif enabled and dt > 0 and s.confidence >= min_conf and pelvis.confidence >= min_conf:
                rate = -(s.bias - pelvis.bias)
                s.correction = float(np.clip(s.correction + rate * dt, -max_corr, max_corr))
            elif dt > 0:
                s.confidence *= np.exp(-dt / decay)
            corrections[k, j] = 0.0 if j == pelvis_index else s.correction
            biases[k, j] = s.bias
            confidence[k, j] = s.confidence
            epochs[k, j] = s.epoch
            if j == pelvis_index:
                reasons[k, j] = REASON_PELVIS_GAUGE
            elif not valid[k, j]:
                reasons[k, j] = REASON_RAW_INVALID
            elif s.restart_pending:
                reasons[k, j] = REASON_GAP_RESTART
            elif s.confidence < min_conf:
                reasons[k, j] = REASON_LOW_CONFIDENCE
            else:
                reasons[k, j] = REASON_ACTIVE; states[k, j] = 1
            if valid[k, j] and j != pelvis_index and enabled and s.correction != 0.0:
                corrected[k, j] = normalize(multiply(qz(s.correction), raw[k, j]))

        # Current orientation increment becomes evidence only for future frames.
        if k > 0 and dt > 0:
            for j, s in enumerate(runtime):
                if valid[k-1, j] and valid[k, j]:
                    z[k, j] = spatial_yaw_rate(raw[k-1, j], raw[k, j], dt)
                if stationary[k, j] and np.isfinite(z[k, j]):
                    s.support.append((float(t[k]), float(z[k, j])))
                    while s.support and t[k] - s.support[0][0] > max_window:
                        s.support.popleft()
                    span = s.support[-1][0] - s.support[0][0] if len(s.support) > 1 else 0.0
                    # Bias is a deliberately slow state. Updating at 10 Hz is
                    # causal and avoids needlessly recomputing an identical
                    # robust statistic at every 60 Hz display frame.
                    if k % 6 == 0 and len(s.support) >= min_samples and span >= min_support:
                        target = float(np.clip(np.median([x[1] for x in s.support]), -max_bias, max_bias))
                        s.bias += float(np.clip(target - s.bias, -max_step, max_step))
                        s.confidence = min(1.0, len(s.support) / max(min_samples, int(max_window * 60.0)))
                        s.restart_pending = False
                        observations[k, j] = OBS_STATIONARY_RATE

    corrected[~valid] = raw[~valid]
    return {"corrected_q_GB_wxyz": corrected, "correction_rad": corrections,
            "bias_rad_s": biases, "correction_confidence": confidence,
            "correction_state": states, "inactive_reason": reasons,
            "observation_type": observations, "correction_epoch": epochs,
            "spatial_yaw_rate_rad_s": z}
