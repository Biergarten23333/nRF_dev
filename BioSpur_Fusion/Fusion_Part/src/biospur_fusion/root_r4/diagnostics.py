"""Per-link residual, body-shadow proxy, and shared-fault diagnostics."""
from __future__ import annotations

import math

import numpy as np

from .contracts import yaw_rotation_v4_from_n
from .data import C1Data


def _safe(value: float) -> float | None:
    return float(value) if np.isfinite(value) else None


def _longest_fault_duration(time: np.ndarray, fault: np.ndarray) -> float:
    bad = np.asarray(fault, bool)
    if not np.any(bad):
        return 0.0
    starts = np.flatnonzero(bad & np.r_[True, ~bad[:-1]])
    stops = np.flatnonzero(bad & np.r_[~bad[1:], True])
    return float(max(time[stop] - time[start] for start, stop in zip(starts, stops)))


def _summary(time: np.ndarray, residual: np.ndarray, present: np.ndarray, total: int) -> dict:
    values = np.asarray(residual, float); finite = np.isfinite(values) & np.asarray(present, bool)
    selected = values[finite]
    if not len(selected):
        return {"event_count": 0, "availability_rate": 0.0, "missing_rate": 1.0,
                "classification": "RANGE_LINK_UNKNOWN"}
    median = float(np.median(selected)); mad = float(np.median(np.abs(selected - median))); robust = 1.4826 * mad
    centred = selected - np.mean(selected)
    autocorrelation = (float(np.dot(centred[:-1], centred[1:]) / np.dot(centred, centred))
                       if len(selected) > 2 and np.dot(centred, centred) > 0.0 else None)
    fault = finite & (np.abs(values) > 0.50)
    accepted = int(np.sum(np.abs(selected) <= 0.25)); downweighted = int(np.sum((np.abs(selected) > 0.25) & (np.abs(selected) <= 0.75)))
    rejected = int(np.sum(np.abs(selected) > 0.75))
    classification = ("RANGE_LINK_CREDIBLE" if robust < 0.15 and rejected / len(selected) < 0.05 else
                      "RANGE_LINK_DEGRADED" if rejected / len(selected) < 0.30 else "RANGE_LINK_REJECTED")
    return {
        "event_count": int(len(selected)), "availability_rate": float(len(selected) / total),
        "missing_rate": float(1.0 - len(selected) / total), "median_residual_m": median,
        "mad_m": mad, "robust_sigma_m": robust, "p95_absolute_residual_m": float(np.quantile(np.abs(selected), 0.95)),
        "positive_residual_tail_p95_m": _safe(np.quantile(selected[selected > 0.0], 0.95)) if np.any(selected > 0.0) else None,
        "negative_residual_tail_p05_m": _safe(np.quantile(selected[selected < 0.0], 0.05)) if np.any(selected < 0.0) else None,
        "temporal_autocorrelation_lag1": autocorrelation,
        "consecutive_fault_duration_max_s": _longest_fault_duration(np.asarray(time, float), fault),
        "accepted_count": accepted, "downweighted_count": downweighted, "rejected_count": rejected,
        "classification": classification,
    }


def per_link_health(data: C1Data) -> tuple[dict, dict]:
    rows = []; used = ((data.t4_used_mask[:, None] >> data.anchor_id) & 1).astype(bool)
    for tag, node in enumerate(data.nodes):
        tag_rows = np.flatnonzero(data.node_index == tag); total = len(tag_rows)
        for anchor in range(8):
            residual = data.t4_residual_m[tag_rows, anchor]
            present = data.raw_valid[tag_rows, anchor] & used[tag_rows, anchor] & np.isfinite(residual)
            rows.append({"tag_id": node, "anchor_id": anchor,
                         **_summary(data.raw_measurement_s[tag_rows, anchor], residual, present, total)})
    tag_aggregate = []
    for tag, node in enumerate(data.nodes):
        tag_rows = np.flatnonzero(data.node_index == tag)
        residual = data.t4_residual_m[tag_rows].reshape(-1); present = (data.raw_valid[tag_rows] & used[tag_rows]).reshape(-1)
        time = np.repeat(data.measurement_s[tag_rows], 8)
        tag_aggregate.append({"tag_id": node, **_summary(time, residual, present, len(tag_rows) * 8)})
    anchor_aggregate = []
    for anchor in range(8):
        residual = data.t4_residual_m[:, anchor]; present = data.raw_valid[:, anchor] & used[:, anchor]
        anchor_aggregate.append({"anchor_id": anchor, **_summary(data.raw_measurement_s[:, anchor], residual, present, data.event_count)})
    audit = {
        "schema": "biospur.root_r4.per_link_health.v1",
        "residual_definition": "canonical T4 per-anchor residual from the exact constituent raw record; not external truth",
        "frame_dependency": "none for this per-tag multilateration residual; common-root residuals withheld because frame not authorized",
        "rows": rows, "tag_aggregate": tag_aggregate, "anchor_aggregate": anchor_aggregate,
        "classification_counts": {name: sum(row["classification"] == name for row in rows)
                                  for name in ("RANGE_LINK_CREDIBLE", "RANGE_LINK_DEGRADED", "RANGE_LINK_REJECTED", "RANGE_LINK_UNKNOWN")},
    }
    residual_audit = {
        "schema": "biospur.root_r4.raw_range_residual.v1", "source": "canonical T4 exact raw constituent residuals",
        "raw_links": data.raw_event_count, "finite_solver_residuals": int(np.sum(np.isfinite(data.t4_residual_m) & used)),
        "per_link_health_reference": "PER_LINK_HEALTH_AUDIT.json",
        "warning": "The capture-bound common-root frame was not authorized; these residuals diagnose ranging/solver consistency, not world accuracy.",
    }
    return audit, residual_audit


def common_mode_audit(data: C1Data) -> dict:
    used = ((data.t4_used_mask[:, None] >> data.anchor_id) & 1).astype(bool)
    order = np.argsort(data.epoch, kind="stable"); ordered_epoch = data.epoch[order]
    starts = np.r_[0, np.flatnonzero(np.diff(ordered_epoch)) + 1]; stops = np.r_[starts[1:], len(order)]
    rows = []
    for anchor in range(8):
        epoch_scores = []; cofault = 0; eligible = 0
        for start, stop in zip(starts, stops):
            group = order[start:stop]
            values = data.t4_residual_m[group, anchor]
            valid = data.raw_valid[group, anchor] & used[group, anchor] & np.isfinite(values)
            if np.sum(valid) < 4:
                continue
            selected = values[valid]; epoch_scores.append(float(np.median(selected))); eligible += 1
            cofault += int(np.sum(selected > 0.25) >= 3)
        scores = np.asarray(epoch_scores)
        rows.append({"anchor_id": anchor, "eligible_epochs": eligible,
                     "median_common_residual_m": float(np.median(scores)) if len(scores) else None,
                     "p95_common_residual_m": float(np.quantile(scores, 0.95)) if len(scores) else None,
                     "three_tag_positive_cofault_rate": float(cofault / eligible) if eligible else None,
                     "classification": "SHARED_ANCHOR_FAULT_SUSPECTED" if eligible and cofault / eligible > 0.05
                     else "COMMON_MODE_FAULT_UNRESOLVED"})
    tag_rows = []
    for tag, node in enumerate(data.nodes):
        ids = np.flatnonzero(data.node_index == tag); values = data.t4_residual_m[ids]
        valid = data.raw_valid[ids] & used[ids] & np.isfinite(values)
        per_event = np.sum((values > 0.25) & valid, axis=1)
        tag_rows.append({"tag_id": node, "multi_anchor_positive_fault_rate": float(np.mean(per_event >= 3)),
                         "classification": "TAG_COMMON_MODE_FAULT_SUSPECTED" if np.mean(per_event >= 3) > 0.05
                         else "COMMON_MODE_FAULT_UNRESOLVED"})
    return {"schema": "biospur.root_r4.shared_anchor_common_mode.v1", "anchors": rows, "tags": tag_rows,
            "external_fault_truth_available": False}


def body_shadow_diagnostic(data: C1Data, yaw_v4_from_n_rad: float) -> dict:
    rotation = yaw_rotation_v4_from_n(yaw_v4_from_n_rad)
    root = np.full((data.event_count, 3), np.nan)
    order = np.argsort(data.epoch, kind="stable"); ordered_epoch = data.epoch[order]
    starts = np.r_[0, np.flatnonzero(np.diff(ordered_epoch)) + 1]; stops = np.r_[starts[1:], len(order)]
    for start, stop in zip(starts, stops):
        group = order[start:stop]
        root[group] = np.median(data.t4_xyz_m[group] - data.root_relative_n_m[group] @ rotation.T, axis=0)
    predicted_tag = root[:, None, :] + data.raw_root_relative_n_m @ rotation.T
    radial = predicted_tag - root[:, None, :]; radial_norm = np.linalg.norm(radial, axis=2)
    direction = data.anchors_v4_m[None, :, :] - predicted_tag
    direction /= np.maximum(np.linalg.norm(direction, axis=2)[..., None], 1e-12)
    radial_unit = radial / np.maximum(radial_norm[..., None], 1e-12)
    # Negative alignment means the anchor ray points back through the modeled common-root/body volume.
    score = -np.einsum("nai,nai->na", radial_unit, direction)
    risk = (score > 0.35) & (radial_norm > 0.20)
    used = ((data.t4_used_mask[:, None] >> data.anchor_id) & 1).astype(bool)
    residual = data.t4_residual_m; valid = data.raw_valid & used & np.isfinite(residual)
    positive = residual > 0.25
    risk_rate = float(np.mean(positive[valid & risk])) if np.any(valid & risk) else None
    clear_rate = float(np.mean(positive[valid & ~risk])) if np.any(valid & ~risk) else None
    return {
        "schema": "biospur.root_r4.body_shadow_geometry_diagnostic.v1",
        "classification": "BODY_SHADOW_CONSISTENT_DIAGNOSTIC_ONLY",
        "proxy": "per-link-midpoint candidate-frame radial ray points through common-root/body volume; not capsule intersection and not observed NLOS truth",
        "frame_status": "OFFLINE_FULL_CAPTURE_FRAME_DIAGNOSTIC_NOT_CAUSAL; NOT_AUTHORIZED",
        "risk_samples": int(np.sum(valid & risk)), "clear_samples": int(np.sum(valid & ~risk)),
        "positive_residual_rate_risk": risk_rate, "positive_residual_rate_clear": clear_rate,
        "risk_minus_clear": (risk_rate - clear_rate) if risk_rate is not None and clear_rate is not None else None,
        "CIR_used": False, "RSS_used": False, "first_path_power_used": False,
        "external_nlos_labels_available": False,
    }
