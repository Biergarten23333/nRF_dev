"""Synthetic-only selection of the frozen Stage 3-R1 state machine limits."""
from __future__ import annotations

import copy

import numpy as np

from pure_imu_baseline.math3d import multiply
from pure_imu_baseline.stage3.analysis import quat_distance, relative
from pure_imu_baseline.stage3.corrector import qz

from .corrector import correct


PARENT = np.array([1, -1, 0, 2, 0, 4, 1, 6, 1, 8], dtype=int)
PELVIS = 1
EDGE_CHILDREN = np.array([0, 2, 3, 4, 5, 6, 7, 8, 9], dtype=int)


def base(duration: float = 30.0, rate_hz: float = 60.0):
    t = np.arange(0.0, duration + 0.25 / rate_hz, 1.0 / rate_hz, dtype=np.float64)
    q = np.zeros((len(t), 10, 4), dtype=np.float64)
    q[..., 0] = 1.0
    valid = np.ones((len(t), 10), dtype=bool)
    reset = np.zeros_like(valid)
    reset[0] = True
    stationary = np.ones_like(valid)
    return t, q, valid, reset, stationary


def angle(q: np.ndarray) -> np.ndarray:
    return np.unwrap(2.0 * np.arctan2(q[..., 3], q[..., 0]), axis=0)


def run(config: dict, t, q, valid, reset, stationary):
    return correct(t, q, valid, reset, stationary, PARENT, PELVIS, config)


def maximum_same_epoch_excess(qraw, qcor, valid, epoch, edge: int) -> float:
    p = int(PARENT[EDGE_CHILDREN[edge]])
    c = int(EDGE_CHILDREN[edge])
    qr = relative(qraw[:, p], qraw[:, c])
    qc = relative(qcor[:, p], qcor[:, c])
    raw_step = quat_distance(qr[1:], qr[:-1])
    cor_step = quat_distance(qc[1:], qc[:-1])
    use = valid[1:, p] & valid[:-1, p] & valid[1:, c] & valid[:-1, c] & (epoch[1:, edge] == epoch[:-1, edge])
    return float(np.max(cor_step[use] - raw_step[use])) if np.any(use) else 0.0


def drift_case(config: dict, bias: float, duration: float = 35.0) -> tuple[dict, dict]:
    t, q, valid, reset, stationary = base(duration)
    q[:, 3] = qz(bias * t)
    out = run(config, t, q, valid, reset, stationary)
    raw_angle = angle(q[:, 3]) - angle(q[:, 2])
    cor_angle = angle(out["corrected_q_GB_wxyz"][:, 3]) - angle(out["corrected_q_GB_wxyz"][:, 2])
    use = t >= 20.0
    raw_rate = float(abs(np.median(np.gradient(raw_angle, t)[use])))
    cor_rate = float(abs(np.median(np.gradient(cor_angle, t)[use])))
    reduction = 1.0 - cor_rate / raw_rate if raw_rate > 1e-12 else 0.0
    return out, {"raw_rate_rad_s": raw_rate, "corrected_rate_rad_s": cor_rate,
                 "recovery_fraction": reduction}


def run_synthetic_qualification(config: dict) -> tuple[dict, dict]:
    cases: dict[str, dict] = {}

    t, q, valid, reset, stationary = base(12.0)
    zero = run(config, t, q, valid, reset, stationary)
    cases["zero_drift"] = {"pass": bool(np.max(np.abs(zero["edge_eta_rad"])) == 0.0)}

    common_q = qz(0.012 * t)
    q[:] = common_q[:, None, :]
    common = run(config, t, q, valid, reset, stationary)
    cases["common_complete_body_drift"] = {"pass": bool(np.max(np.abs(common["edge_eta_rad"])) <= 1e-12),
        "maximum_abs_eta_rad": float(np.max(np.abs(common["edge_eta_rad"]))) }

    within, metric = drift_case(config, 0.012)
    metric["pass"] = metric["recovery_fraction"] >= 0.70
    cases["edge_drift_within_supported_range"] = metric
    beyond, metric_beyond = drift_case(config, 0.060)
    metric_beyond["maximum_estimated_bias_rad_s"] = float(np.max(np.abs(beyond["edge_bias_rad_s"])))
    metric_beyond["pass"] = metric_beyond["maximum_estimated_bias_rad_s"] <= config["edge_bias_estimator"]["maximum_abs_bias_rad_s"] + 1e-12
    cases["edge_drift_beyond_supported_range"] = metric_beyond

    def transition_case(name: str, stationary_mask, mutate=None):
        tt, qq, vv, rr, ss = base(28.0)
        qq[:, 3] = qz(0.012 * tt)
        ss[:] = stationary_mask(tt)[:, None]
        if mutate:
            mutate(tt, qq, vv, rr, ss)
        oo = run(config, tt, qq, vv, rr, ss)
        excess = maximum_same_epoch_excess(qq, oo["corrected_q_GB_wxyz"], vv, oo["edge_epoch"], 2)
        return {"pass": excess <= 1e-6, "maximum_same_epoch_excess_increment_rad": excess,
                "maximum_abs_eta_rate_rad_s": float(np.max(np.abs(oo["edge_applied_rate_rad_s"]))),
                "maximum_active_acceleration_rad_s2": float(np.max(np.abs(oo["edge_applied_acceleration_rad_s2"])))}, oo, (tt, qq, vv)

    cases["stationarity_entry"], _, _ = transition_case("entry", lambda tt: tt >= 3.0)
    cases["stationarity_exit"], exit_out, exit_data = transition_case("exit", lambda tt: tt < 18.0)
    cases["confidence_flicker"], _, _ = transition_case("flicker", lambda tt: (tt < 13.0) | (tt >= 13.2))
    loss_t, loss_q, loss_valid, loss_reset, loss_stationary = base(125.0)
    loss_q[:, 3] = qz(0.012 * loss_t)
    loss_stationary[:] = (loss_t < 15.0)[:, None]
    loss_out = run(config, loss_t, loss_q, loss_valid, loss_reset, loss_stationary)
    loss_excess = maximum_same_epoch_excess(loss_q, loss_out["corrected_q_GB_wxyz"], loss_valid, loss_out["edge_epoch"], 2)
    loss_metric = {"pass": loss_excess <= 1e-6,
                   "maximum_same_epoch_excess_increment_rad": loss_excess,
                   "maximum_abs_eta_rate_rad_s": float(np.max(np.abs(loss_out["edge_applied_rate_rad_s"]))),
                   "maximum_active_acceleration_rad_s2": float(np.max(np.abs(loss_out["edge_applied_acceleration_rad_s2"])))}
    frozen = loss_out["edge_eta_rad"][np.searchsorted(loss_t, 115.0):, 2]
    loss_metric["eta_frozen_after_loss"] = bool(np.ptp(frozen) <= 1e-12)
    loss_metric["pass"] = loss_metric["pass"] and loss_metric["eta_frozen_after_loss"]
    cases["support_loss"] = loss_metric

    def outlier_mutate(tt, qq, vv, rr, ss):
        for when in (8.0, 10.0, 12.0):
            k = np.searchsorted(tt, when)
            qq[k:, 3] = multiply(qz(0.8), qq[k:, 3])
    cases["isolated_outliers"], outlier, _ = transition_case("outliers", lambda tt: np.ones_like(tt, bool), outlier_mutate)
    cases["isolated_outliers"]["rejected_observations"] = int(np.sum(outlier["edge_observation"] == 2))
    cases["isolated_outliers"]["pass"] = cases["isolated_outliers"]["pass"] and cases["isolated_outliers"]["rejected_observations"] >= 3

    tt = np.cumsum(np.resize(np.array([1/60, 1/58, 1/62, 1/59], float), 1800)); tt -= tt[0]
    qq = np.zeros((len(tt), 10, 4)); qq[..., 0] = 1; qq[:, 3] = qz(0.01 * tt)
    vv = np.ones((len(tt), 10), bool); rr = np.zeros_like(vv); rr[0] = True; ss = np.ones_like(vv)
    irregular = run(config, tt, qq, vv, rr, ss)
    cases["irregular_valid_dt"] = {"pass": bool(np.all(np.isfinite(irregular["edge_eta_rad"]))) }

    tt, qq, vv, rr, ss = base(18.0); qq[:, 3] = qz(0.012 * tt); qq[::2] *= -1
    signs = run(config, tt, qq, vv, rr, ss)
    cases["quaternion_sign_flips"] = {"pass": maximum_same_epoch_excess(qq, signs["corrected_q_GB_wxyz"], vv, signs["edge_epoch"], 2) <= 1e-6}
    tt, qq, vv, rr, ss = base(18.0); qq[:, 3] = qz(np.linspace(2.8, 3.5, len(tt)))
    crossing = run(config, tt, qq, vv, rr, ss)
    cases["plus_minus_pi_crossing"] = {"pass": maximum_same_epoch_excess(qq, crossing["corrected_q_GB_wxyz"], vv, crossing["edge_epoch"], 2) <= 1e-6,
        "angle_wrapping_used": False}

    def root_gap(tt, qq, vv, rr, ss):
        gap = (tt >= 16) & (tt < 17); vv[gap, PELVIS] = False
    root_metric, root_out, root_data = transition_case("root_gap", lambda tt: np.ones_like(tt, bool), root_gap)
    post = np.searchsorted(root_data[0], 17.0)
    root_metric["first_post_gap_eta_zero"] = bool(np.all(root_out["edge_eta_rad"][post] == 0.0))
    root_metric["pass"] = root_metric["pass"] and root_metric["first_post_gap_eta_zero"]
    cases["root_gap"] = root_metric

    def subtree_gap(tt, qq, vv, rr, ss):
        gap = (tt >= 16) & (tt < 17); vv[gap, 2] = False
    sub_metric, sub_out, sub_data = transition_case("subtree_gap", lambda tt: np.ones_like(tt, bool), subtree_gap)
    post = np.searchsorted(sub_data[0], 17.0)
    sub_metric["affected_edge_eta_zero"] = bool(np.all(sub_out["edge_eta_rad"][post, [1, 2]] == 0.0))
    sub_metric["unaffected_edges_preserved"] = bool(sub_out["edge_epoch"][post, 0] == sub_out["edge_epoch"][post - 1, 0])
    sub_metric["pass"] = sub_metric["pass"] and sub_metric["affected_edge_eta_zero"] and sub_metric["unaffected_edges_preserved"]
    cases["subtree_gap"] = sub_metric

    enabled = np.ones(9, bool); enabled[2] = False
    tt, qq, vv, rr, ss = base(18.0); qq[:, 2] = qz(0.4); qq[:, 3] = qz(0.7)
    partial = correct(tt, qq, vv, rr, ss, PARENT, PELVIS, config, enabled)
    raw_rel = relative(qq[:, 2], qq[:, 3]); cor_rel = relative(partial["corrected_q_GB_wxyz"][:, 2], partial["corrected_q_GB_wxyz"][:, 3])
    cases["partial_edge_fallback"] = {"pass": bool(np.max(quat_distance(raw_rel, cor_rel)) <= 1e-12),
        "unsupported_edge_relative_orientation_error_rad": float(np.max(quat_distance(raw_rel, cor_rel)))}

    motions = {
        "forearm_pronation_supination": lambda tt: 1.2*np.sin(1.5*tt),
        "elbow_flexion": lambda tt: 1.1*(0.5+0.5*np.sin(1.1*tt)),
        "squat": lambda tt: 0.9*(0.5+0.5*np.sin(0.75*tt)),
        "boxing_like_motion": lambda tt: 0.8*np.sin(5.0*tt)*(np.sin(0.3*tt)>0),
        "slow_articulation": lambda tt: 0.65*np.sin(0.22*tt),
    }
    for name, profile in motions.items():
        tt, qq, vv, rr, ss = base(15.0); ss[:] = False; qq[:, 3] = qz(profile(tt))
        oo = run(config, tt, qq, vv, rr, ss)
        cases[name] = {"pass": bool(np.array_equal(qq, oo["corrected_q_GB_wxyz"])),
                       "maximum_abs_eta_rad": float(np.max(np.abs(oo["edge_eta_rad"]))) }

    assert len(cases) == 20, len(cases)
    transition_names = ["stationarity_entry", "stationarity_exit", "confidence_flicker", "support_loss",
                        "isolated_outliers", "irregular_valid_dt", "quaternion_sign_flips", "plus_minus_pi_crossing",
                        "root_gap", "subtree_gap", "partial_edge_fallback"]
    transition = {name: cases[name] for name in transition_names}
    transition["all_passed"] = all(cases[name]["pass"] for name in transition_names)
    transition["direct_eta_assignment_used"] = False
    transition["eta_wrapping_used"] = False
    motion_names = [name for name in cases if name not in transition_names]
    motion = {name: cases[name] for name in motion_names}
    motion["supported_differential_recovery_gate_70_percent"] = cases["edge_drift_within_supported_range"]["recovery_fraction"] >= 0.70
    motion["all_passed"] = all(cases[name]["pass"] for name in motion_names)
    return transition, motion
