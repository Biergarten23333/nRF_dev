"""Causal one-shot bumpless differential-yaw-rate bias arrester.

The estimator owns exactly one state per directed parent-child edge. Bias and
confidence may reset independently, while the applied correction ``eta`` is
never assigned from those estimator states inside a continuous valid epoch.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

import numpy as np

from pure_imu_baseline.math3d import multiply, normalize
from pure_imu_baseline.stage3.analysis import spatial_z_rates
from pure_imu_baseline.stage3.corrector import qz, spatial_yaw_rate

from . import ALGORITHM_ID

REASON_UNSUPPORTED = 0
REASON_ACTIVE = 1
REASON_CONFIDENCE_HOLD = 2
REASON_GAP_RESTART = 3
REASON_INVALID_EDGE = 4
REASON_OUTLIER_REJECTED = 5

OBS_NONE = 0
OBS_ACCEPTED_DIFFERENTIAL_RATE = 1
OBS_REJECTED_OUTLIER = 2


@dataclass
class EdgeState:
    eta: float = 0.0
    bias: float = 0.0
    confidence: float = 0.0
    applied_rate: float = 0.0
    epoch: int = 0
    restart_pending: bool = True
    support: deque = field(default_factory=deque)
    last_accept_time: float | None = None


def edge_topology(parent_index: np.ndarray, pelvis_index: int) -> tuple[np.ndarray, np.ndarray, list[list[int]]]:
    parent = np.asarray(parent_index, dtype=int)
    children = np.array([j for j in range(len(parent)) if j != pelvis_index], dtype=int)
    parents = parent[children]
    if np.any(parents < 0):
        raise ValueError("every non-pelvis node must have one parent")
    edge_for_child = {int(child): e for e, child in enumerate(children)}
    subtree_edges: list[list[int]] = []
    for root in range(len(parent)):
        affected = []
        for child, edge in edge_for_child.items():
            node = child
            while node >= 0 and node != root:
                node = int(parent[node])
            if node == root:
                affected.append(edge)
        subtree_edges.append(affected)
    return parents, children, subtree_edges


def _robust_accept(values: deque, value: float, cfg: dict) -> bool:
    if not np.isfinite(value) or abs(value) > float(cfg["maximum_abs_observation_rad_s"]):
        return False
    if len(values) < 12:
        return True
    x = np.fromiter((v for _, v in values), dtype=np.float64)
    median = float(np.median(x))
    mad = float(np.median(np.abs(x - median))) * 1.4826
    scale = max(float(cfg["robust_minimum_scale_rad_s"]), mad)
    return abs(value - median) <= float(cfg["robust_mad_multiplier"]) * scale


def correct(time_s: np.ndarray, raw_q_wxyz: np.ndarray, valid: np.ndarray,
            filter_reset: np.ndarray, stationary: np.ndarray,
            parent_index: np.ndarray, pelvis_index: int, config: dict,
            enabled_edges: np.ndarray | None = None) -> dict[str, np.ndarray]:
    if config.get("algorithm_id") != ALGORITHM_ID:
        raise ValueError("wrong algorithm configuration")
    t = np.asarray(time_s, dtype=np.float64)
    raw = np.asarray(raw_q_wxyz)
    valid = np.asarray(valid, dtype=bool)
    reset = np.asarray(filter_reset, dtype=bool)
    stationary = np.asarray(stationary, dtype=bool)
    if raw.shape[:2] != valid.shape or reset.shape != valid.shape or stationary.shape != valid.shape:
        raise ValueError("shape mismatch")
    if len(t) != len(raw) or np.any(~np.isfinite(t)) or np.any(np.diff(t) <= 0):
        raise ValueError("invalid timebase")

    n, m = valid.shape
    parents, children, subtree_edges = edge_topology(parent_index, pelvis_index)
    ecount = len(children)
    enabled = np.ones(ecount, dtype=bool) if enabled_edges is None else np.asarray(enabled_edges, dtype=bool)
    if enabled.shape != (ecount,):
        raise ValueError("enabled edge shape mismatch")

    eta = np.zeros((n, ecount), dtype=np.float64)
    bias = np.zeros_like(eta)
    conf = np.zeros_like(eta)
    rate = np.zeros_like(eta)
    accel = np.zeros_like(eta)
    target = np.full_like(eta, np.nan)
    zedge = np.full_like(eta, np.nan)
    state = np.zeros((n, ecount), dtype=np.uint8)
    reason = np.full((n, ecount), REASON_UNSUPPORTED, dtype=np.uint8)
    observation = np.zeros((n, ecount), dtype=np.uint8)
    epoch = np.zeros((n, ecount), dtype=np.uint32)
    accepted = np.zeros((n, ecount), dtype=bool)
    edge_valid = valid[:, parents] & valid[:, children]
    edge_stationary = stationary[:, parents] & stationary[:, children]
    node_correction = np.zeros((n, m), dtype=np.float64)
    corrected = raw.astype(np.float64, copy=True)
    node_rates = spatial_z_rates(raw.astype(np.float64), valid, t)

    ecfg = config["edge_bias_estimator"]
    acfg = config["applied_correction"]
    window = float(ecfg["window_s"])
    minimum_support = float(ecfg["minimum_support_s"])
    minimum_samples = int(ecfg["minimum_samples"])
    expected_rate = float(ecfg["expected_samples_per_second"])
    stride = int(ecfg["update_stride_frames"])
    max_bias = float(ecfg["maximum_abs_bias_rad_s"])
    max_bias_step = float(ecfg["maximum_bias_step_rad_s"])
    decay = float(ecfg["confidence_decay_s"])
    min_conf = float(ecfg["minimum_active_confidence"])
    full_conf = float(ecfg["full_rate_confidence"])
    max_rate = float(acfg["maximum_abs_rate_rad_s"])
    max_accel = float(acfg["maximum_abs_acceleration_rad_s2"])
    runtime = [EdgeState() for _ in range(ecount)]

    def restart(edges: list[int]) -> None:
        for e in edges:
            s = runtime[e]
            s.eta = 0.0
            s.bias = 0.0
            s.confidence = 0.0
            s.applied_rate = 0.0
            s.support.clear()
            s.last_accept_time = None
            s.epoch += 1
            s.restart_pending = True

    for k in range(n):
        dt = 0.0 if k == 0 else float(t[k] - t[k - 1])
        root_fault = bool(reset[k, pelvis_index] or (k > 0 and valid[k, pelvis_index] != valid[k - 1, pelvis_index]))
        if root_fault:
            restart(list(range(ecount)))
        else:
            touched: set[int] = set()
            for node in range(m):
                if node == pelvis_index:
                    continue
                fault = bool(reset[k, node] or (k > 0 and valid[k, node] != valid[k - 1, node]))
                if fault:
                    touched.update(subtree_edges[node])
            if touched:
                restart(sorted(touched))

        # Apply only state supported by samples strictly before this frame.
        for e, s in enumerate(runtime):
            old_rate = s.applied_rate
            if dt > 0 and s.last_accept_time is not None and not edge_stationary[k, e]:
                s.confidence *= float(np.exp(-dt / decay))
            gate = float(np.clip((s.confidence - min_conf) / max(1e-12, full_conf - min_conf), 0.0, 1.0))
            desired = float(np.clip(-s.bias * gate, -max_rate, max_rate)) if enabled[e] else 0.0
            if s.confidence < min_conf or s.restart_pending or not edge_valid[k, e]:
                desired = 0.0
            if dt > 0:
                max_delta = max_accel * dt
                s.applied_rate += float(np.clip(desired - s.applied_rate, -max_delta, max_delta))
                # Confidence decay is slow enough that rate reaches zero before
                # the active threshold. At/below threshold eta is held exactly.
                if s.confidence >= min_conf and not s.restart_pending and edge_valid[k, e] and enabled[e]:
                    s.eta += 0.5 * (old_rate + s.applied_rate) * dt
                else:
                    s.applied_rate = 0.0
            eta[k, e] = s.eta if enabled[e] else 0.0
            bias[k, e] = s.bias
            conf[k, e] = s.confidence
            rate[k, e] = s.applied_rate if enabled[e] else 0.0
            accel[k, e] = 0.0 if dt <= 0 else (rate[k, e] - (rate[k - 1, e] if k else 0.0)) / dt
            epoch[k, e] = s.epoch
            if not edge_valid[k, e]:
                reason[k, e] = REASON_INVALID_EDGE
            elif s.restart_pending:
                reason[k, e] = REASON_GAP_RESTART
            elif s.confidence < min_conf:
                reason[k, e] = REASON_CONFIDENCE_HOLD
            elif enabled[e]:
                reason[k, e] = REASON_ACTIVE
                state[k, e] = 1

        # Current increments become evidence only for future frames.
        if k > 0 and dt > 0:
            for e, s in enumerate(runtime):
                if not (edge_valid[k - 1, e] and edge_valid[k, e] and epoch[k - 1, e] == epoch[k, e]):
                    continue
                value = node_rates[k, int(children[e])] - node_rates[k, int(parents[e])]
                zedge[k, e] = value
                if not edge_stationary[k, e]:
                    continue
                if not _robust_accept(s.support, float(value), ecfg):
                    observation[k, e] = OBS_REJECTED_OUTLIER
                    reason[k, e] = REASON_OUTLIER_REJECTED
                    continue
                accepted[k, e] = True
                observation[k, e] = OBS_ACCEPTED_DIFFERENTIAL_RATE
                s.support.append((float(t[k]), float(value)))
                s.last_accept_time = float(t[k])
                while s.support and t[k] - s.support[0][0] > window:
                    s.support.popleft()
                span = s.support[-1][0] - s.support[0][0] if len(s.support) > 1 else 0.0
                if len(s.support) >= minimum_samples and span >= minimum_support and k % stride == 0:
                    robust_target = float(np.clip(np.median([v for _, v in s.support]), -max_bias, max_bias))
                    target[k, e] = robust_target
                    s.bias += float(np.clip(robust_target - s.bias, -max_bias_step, max_bias_step))
                    support_conf = len(s.support) / max(1.0, window * expected_rate)
                    s.confidence = max(s.confidence, min(1.0, support_conf))
                    s.restart_pending = False

    # Reconstruct node corrections by exact tree summation. An unsupported
    # edge has eta=0, so its child inherits its parent's correction.
    unresolved = set(int(x) for x in children)
    while unresolved:
        progressed = False
        for e, child_value in enumerate(children):
            child = int(child_value)
            if child not in unresolved:
                continue
            parent = int(parents[e])
            if parent == pelvis_index or parent not in unresolved:
                node_correction[:, child] = node_correction[:, parent] + eta[:, e]
                unresolved.remove(child)
                progressed = True
        if not progressed:
            raise ValueError("parent graph is not a rooted tree")
    apply = valid & (node_correction != 0.0)
    apply[:, pelvis_index] = False
    corrected[apply] = normalize(multiply(qz(node_correction[apply]), raw[apply].astype(np.float64)))
    corrected[:, pelvis_index] = raw[:, pelvis_index].astype(np.float64)
    corrected[~valid] = raw[~valid].astype(np.float64)
    return {
        "corrected_q_GB_wxyz": corrected,
        "node_correction_rad": node_correction,
        "edge_eta_rad": eta,
        "edge_bias_rad_s": bias,
        "edge_confidence": conf,
        "edge_applied_rate_rad_s": rate,
        "edge_applied_acceleration_rad_s2": accel,
        "edge_bias_target_rad_s": target,
        "edge_differential_yaw_rate_rad_s": zedge,
        "edge_state": state,
        "edge_reason": reason,
        "edge_observation": observation,
        "edge_epoch": epoch,
        "edge_valid": edge_valid,
        "edge_stationary": edge_stationary,
        "edge_observation_accepted": accepted,
        "edge_parents": parents,
        "edge_children": children,
    }
