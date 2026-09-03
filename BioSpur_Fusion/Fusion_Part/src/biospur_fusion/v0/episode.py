"""General signal-conditioned five-phase calibration-episode segmentation."""
from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping

import numpy as np

from .contracts import NODES
from .data import G, si_samples


PHASES = (
    "VERIFIED_PRE_REST",
    "REST_TO_ACTION_TRANSITION",
    "FORMAL_ACTION_OR_HOLD",
    "ACTION_TO_REST_TRANSITION",
    "VERIFIED_POST_REST",
)


def _runs(mask: np.ndarray) -> list[tuple[int, int]]:
    padded = np.r_[False, np.asarray(mask, bool), False]
    edges = np.flatnonzero(padded[1:] != padded[:-1])
    return [(int(left), int(right)) for left, right in edges.reshape(-1, 2)]


def _bridge_short_false_runs(mask: np.ndarray, maximum_bins: int) -> np.ndarray:
    result = np.asarray(mask, bool).copy()
    if maximum_bins <= 0:
        return result
    for left, right in _runs(~result):
        if left > 0 and right < len(result) and right - left <= maximum_bins:
            result[left:right] = True
    return result


def _phase_row(
    name: str, start_ns: int, stop_ns: int, centers_ns: np.ndarray,
    gyro_q90: np.ndarray, accel_q90: np.ndarray, gravity_step_q90: np.ndarray,
    stable: np.ndarray,
) -> dict[str, Any]:
    selected = (centers_ns >= start_ns) & (centers_ns < stop_ns)
    return {
        "phase": name,
        "start_global_time_ns": int(start_ns),
        "stop_global_time_ns_exclusive": int(stop_ns),
        "duration_s": float(max(0, stop_ns - start_ns) * 1e-9),
        "metric_bin_count": int(np.count_nonzero(selected)),
        "stable_bin_fraction": (
            float(np.mean(stable[selected])) if np.any(selected) else 0.0
        ),
        "gyro_q90_deg_s_max": (
            float(np.max(gyro_q90[selected])) if np.any(selected) else None
        ),
        "accel_norm_residual_q90_mps2_max": (
            float(np.max(accel_q90[selected])) if np.any(selected) else None
        ),
        "gravity_direction_step_q90_deg_max": (
            float(np.nanmax(gravity_step_q90[selected])) if np.any(selected) else None
        ),
    }


def segment_five_phase_episode(
    rows: Mapping[str, np.ndarray], *, action: str,
    action_kind: str,
    formal_start_global_ns: int, formal_stop_global_ns_exclusive: int,
    contract: Mapping[str, Any], boundary_authority: Mapping[str, Any],
) -> dict[str, Any]:
    """Segment one bounded episode with one action/node-independent method.

    Manifest events only bound the episode and formal interval. Rest is accepted
    exclusively from multi-node IMU stability; a whole buffer is never declared
    rest by label.
    """
    if action_kind not in {"STATIONARY_REFERENCE", "MOVEMENT_OR_POSE"}:
        raise ValueError("unknown calibration episode action kind")
    if set(rows) != set(NODES):
        raise ValueError("five-phase segmentation requires the exact ten nodes")
    accepted = {node: value[value["status"] == 1] for node, value in rows.items()}
    if any(len(value) < 2 for value in accepted.values()):
        raise RuntimeError(f"{action}: complete episode lacks accepted IMU rows")
    episode_start = max(int(value["global_time_ns"][0]) for value in accepted.values())
    episode_stop = min(int(value["global_time_ns"][-1]) for value in accepted.values()) + 1
    formal_start = int(formal_start_global_ns)
    formal_stop = int(formal_stop_global_ns_exclusive)
    if not episode_start < formal_start < formal_stop < episode_stop:
        raise RuntimeError(
            f"{action}: formal interval is not strictly inside the complete episode"
        )

    bin_ns = int(round(float(contract["bin_width_ms"]) * 1e6))
    edges = np.arange(episode_start, episode_stop + bin_ns, bin_ns, dtype=np.int64)
    if len(edges) < 4:
        raise RuntimeError(f"{action}: complete episode is too short")
    centers = edges[:-1] + bin_ns // 2
    gyro_q90 = np.full(len(centers), np.nan)
    accel_q90 = np.full(len(centers), np.nan)
    gravity_step_q90 = np.full(len(centers), np.nan)
    node_coverage = np.zeros(len(centers), np.int16)
    node_values = {}
    for node, value in accepted.items():
        acc, gyro = si_samples(value)
        node_values[node] = (
            value["global_time_ns"].astype(np.int64),
            np.degrees(np.linalg.norm(gyro, axis=1)),
            np.abs(np.linalg.norm(acc, axis=1) - G), acc,
        )
    gravity_by_bin = np.full((len(centers), len(NODES), 3), np.nan)
    for index, (left, right) in enumerate(zip(edges[:-1], edges[1:])):
        gyro_values = []
        accel_values = []
        for node_index, (times, gyro, accel, accel_vector) in enumerate(node_values.values()):
            lo = int(np.searchsorted(times, left, side="left"))
            hi = int(np.searchsorted(times, right, side="left"))
            if hi > lo:
                node_coverage[index] += 1
                gyro_values.append(gyro[lo:hi])
                accel_values.append(accel[lo:hi])
                gravity = np.mean(accel_vector[lo:hi], axis=0)
                norm = float(np.linalg.norm(gravity))
                if norm > np.finfo(float).eps:
                    gravity_by_bin[index, node_index] = gravity / norm
        if gyro_values:
            gyro_q90[index] = float(np.quantile(np.concatenate(gyro_values), 0.90))
            accel_q90[index] = float(np.quantile(np.concatenate(accel_values), 0.90))
    for index in range(1, len(centers)):
        valid = np.isfinite(gravity_by_bin[index]).all(axis=1) & np.isfinite(
            gravity_by_bin[index - 1]
        ).all(axis=1)
        if np.any(valid):
            cosine = np.einsum(
                "ij,ij->i", gravity_by_bin[index - 1, valid], gravity_by_bin[index, valid]
            )
            steps = np.degrees(np.arccos(np.clip(cosine, -1.0, 1.0)))
            gravity_step_q90[index] = float(np.quantile(steps, 0.90))
    gravity_step_q90[0] = 0.0
    complete = node_coverage == len(NODES)
    stable = (
        complete
        & (gyro_q90 <= float(contract["stable_gyro_q90_max_deg_s"]))
        & (accel_q90 <= float(contract["stable_accel_norm_residual_q90_max_mps2"]))
        & (gravity_step_q90 <= float(contract["stable_gravity_direction_step_q90_max_deg"]))
    )
    bridged_bins = int(round(
        float(contract["maximum_bridged_unstable_gap_s"]) / (bin_ns * 1e-9)
    ))
    stable = _bridge_short_false_runs(stable, bridged_bins)
    activity = complete & (
        (gyro_q90 >= float(contract["activity_gyro_q90_min_deg_s"]))
        | (accel_q90 >= float(contract["activity_accel_norm_residual_q90_min_mps2"]))
        | (gravity_step_q90 >= float(contract["activity_gravity_direction_step_q90_min_deg"]))
    )
    minimum_rest_bins = int(np.ceil(
        float(contract["minimum_verified_rest_duration_s"]) / (bin_ns * 1e-9)
    ))
    pre_mask = stable & (centers < formal_start)
    post_mask = stable & (centers >= formal_stop)
    pre_runs = [row for row in _runs(pre_mask) if row[1] - row[0] >= minimum_rest_bins]
    post_runs = [row for row in _runs(post_mask) if row[1] - row[0] >= minimum_rest_bins]
    failures = []
    if not pre_runs:
        failures.append("NO_SIGNAL_VERIFIED_PREFIX_REST")
    if not post_runs:
        failures.append("NO_SIGNAL_VERIFIED_SUFFIX_REST")
    if failures:
        return {
            "schema": contract["schema"],
            "action": action,
            "EPISODE_COMPLETENESS": "FAIL",
            "failure_result": contract["failure_result"],
            "failures": failures,
            "boundary_authority": dict(boundary_authority),
            "whole_buffer_labeled_rest": False,
            "manifest_labels_treated_as_rest_evidence": False,
            "method_contract_sha256": hashlib.sha256(json.dumps(
                dict(contract), sort_keys=True, separators=(",", ":")
            ).encode()).hexdigest(),
            "episode_bounds": [episode_start, episode_stop],
            "formal_annotation_bounds": [formal_start, formal_stop],
            "metric_bin_count": int(len(centers)),
        }

    def gravity_reference(run: tuple[int, int]) -> np.ndarray:
        left, right = run
        reference = np.nanmedian(gravity_by_bin[left:right], axis=0)
        norms = np.linalg.norm(reference, axis=1, keepdims=True)
        return reference / np.maximum(norms, np.finfo(float).eps)

    def recovery_mismatch(left: tuple[int, int], right: tuple[int, int]) -> float:
        pre_reference = gravity_reference(left)
        post_reference = gravity_reference(right)
        cosine = np.einsum("ij,ij->i", pre_reference, post_reference)
        return float(np.quantile(np.degrees(np.arccos(np.clip(cosine, -1.0, 1.0))), 0.90))

    selected_pair = None
    rejected_pairs = []
    # The action-local onset is anchored to the latest signal-verified rest
    # that still has independent onset evidence.  Earlier stable plateaus are
    # retained in the audit, but cannot enlarge the transition merely because
    # they occurred first in a long administrative buffer.
    for candidate_pre in reversed(pre_runs):
        for candidate_post in post_runs:
            mismatch = recovery_mismatch(candidate_pre, candidate_post)
            midpoint = formal_start + (formal_stop - formal_start) // 2
            onset_window = (
                (centers >= int(edges[candidate_pre[1]]))
                & (centers < midpoint)
            )
            recovery_window = (
                (centers >= midpoint)
                & (centers < int(edges[candidate_post[0]]))
            )
            onset_activity = bool(np.any(activity & onset_window))
            recovery_activity = bool(np.any(activity & recovery_window))
            transition_supported = (
                action_kind == "STATIONARY_REFERENCE"
                or (onset_activity and recovery_activity)
            )
            rejected_pairs.append({
                "pre_run": list(candidate_pre), "post_run": list(candidate_post),
                "recovery_gravity_direction_q90_deg": mismatch,
                "onset_activity_observed": onset_activity,
                "recovery_activity_observed": recovery_activity,
                "transition_supported": transition_supported,
            })
            if transition_supported:
                selected_pair = (candidate_pre, candidate_post, mismatch)
                break
        if selected_pair is not None:
            break
    if selected_pair is None:
        return {
            "schema": contract["schema"], "action": action,
            "action_kind": action_kind,
            "EPISODE_COMPLETENESS": "FAIL",
            "failure_result": contract["failure_result"],
            "failures": ["NO_SIGNAL_SUPPORTED_BIDIRECTIONAL_TRANSITION_REST_PAIR"],
            "boundary_authority": dict(boundary_authority),
            "whole_buffer_labeled_rest": False,
            "manifest_labels_treated_as_rest_evidence": False,
            "candidate_rest_pair_audit": rejected_pairs,
            "method_contract_sha256": hashlib.sha256(json.dumps(
                dict(contract), sort_keys=True, separators=(",", ":")
            ).encode()).hexdigest(),
            "episode_bounds": [episode_start, episode_stop],
            "formal_annotation_bounds": [formal_start, formal_stop],
            "metric_bin_count": int(len(centers)),
        }
    (pre_left, pre_right), (post_left, post_right), recovery_q90 = selected_pair
    pre_start = max(episode_start, int(edges[pre_left]))
    pre_stop = min(formal_start, int(edges[pre_right]))
    post_start = max(formal_stop, int(edges[post_left]))
    post_stop = min(episode_stop, int(edges[post_right]))
    stationary_transition_ns = int(round(
        float(contract.get("minimum_stationary_transition_duration_s", 0.0)) * 1e9
    ))
    if action_kind == "STATIONARY_REFERENCE" and stationary_transition_ns > 0:
        # A stationary protocol item has no required motion onset or recovery,
        # but it still has five chronological phases.  Keep a declared temporal
        # transition on either side of the formal hold without mislabelling it
        # as activity, and retain the full minimum signal-verified rest length.
        pre_stop = min(pre_stop, formal_start - stationary_transition_ns)
        post_start = max(post_start, formal_stop + stationary_transition_ns)
        minimum_rest_ns = int(round(
            float(contract["minimum_verified_rest_duration_s"]) * 1e9
        ))
        if pre_stop - pre_start < minimum_rest_ns:
            failures.append("STATIONARY_PREFIX_REST_TOO_SHORT_AFTER_TRANSITION_PARTITION")
        if post_stop - post_start < minimum_rest_ns:
            failures.append("STATIONARY_SUFFIX_REST_TOO_SHORT_AFTER_TRANSITION_PARTITION")
    interior_activity = np.flatnonzero(
        activity & (centers >= pre_stop) & (centers < post_start)
    )
    if len(interior_activity) and action_kind != "STATIONARY_REFERENCE":
        first_activity = int(edges[int(interior_activity[0])])
        last_activity = int(edges[int(interior_activity[-1]) + 1])
        formal_phase_start = max(
            formal_start, int(edges[int(interior_activity[0]) + 1]),
        )
        formal_phase_stop = min(
            formal_stop, int(edges[int(interior_activity[-1])]),
        )
        activity_partition = "SIGNAL_ONSET_OFFSET_AND_RECOVERY_AUDITED"
    else:
        first_activity = (
            int(edges[int(interior_activity[0])]) if len(interior_activity) else None
        )
        last_activity = (
            int(edges[int(interior_activity[-1]) + 1]) if len(interior_activity) else None
        )
        formal_phase_start, formal_phase_stop = formal_start, formal_stop
        activity_partition = "STATIONARY_FORMAL_HOLD_NO_POSE_TEMPLATE_CONDITIONING"
    minimum_formal_ns = int(round(float(contract["minimum_formal_phase_duration_s"]) * 1e9))
    if formal_phase_stop - formal_phase_start < minimum_formal_ns:
        failures.append("FORMAL_PHASE_TOO_SHORT_AFTER_SIGNAL_CONDITIONING")
    transition_in_start = pre_stop
    transition_out_stop = post_start
    phase_bounds = (
        (PHASES[0], pre_start, pre_stop),
        (PHASES[1], transition_in_start, formal_phase_start),
        (PHASES[2], formal_phase_start, formal_phase_stop),
        (PHASES[3], formal_phase_stop, transition_out_stop),
        (PHASES[4], post_start, post_stop),
    )
    if any(stop < start for _, start, stop in phase_bounds):
        failures.append("NON_MONOTONIC_PHASE_PARTITION")
    phases = [
        _phase_row(
            name, start, stop, centers, gyro_q90, accel_q90, gravity_step_q90, stable,
        )
        for name, start, stop in phase_bounds
    ]
    result = {
        "schema": contract["schema"],
        "action": action,
        "action_kind": action_kind,
        "EPISODE_COMPLETENESS": "PASS" if not failures else "FAIL",
        "failure_result": None if not failures else contract["failure_result"],
        "failures": failures,
        "boundary_authority": dict(boundary_authority),
        "episode_bounds": [episode_start, episode_stop],
        "formal_annotation_bounds": [formal_start, formal_stop],
        "phases": phases,
        "phase_order": list(PHASES),
        "activity_partition": activity_partition,
        "stationary_transition_semantics": (
            "TIME_PARTITION_ONLY_NO_MOTION_OR_POSE_CLAIM"
            if action_kind == "STATIONARY_REFERENCE" and stationary_transition_ns > 0
            else "NOT_APPLICABLE"
        ),
        "minimum_stationary_transition_duration_s": stationary_transition_ns * 1e-9,
        "recovery_pre_post_gravity_direction_q90_deg": recovery_q90,
        "pre_post_rest_gravity_direction_difference_is_audit_only": True,
        "candidate_rest_pair_audit": rejected_pairs,
        "first_activity_global_time_ns": first_activity,
        "last_activity_global_time_ns_exclusive": last_activity,
        "pre_rest_verified_from_signal": True,
        "post_rest_verified_from_signal": True,
        "whole_buffer_labeled_rest": False,
        "manifest_labels_treated_as_rest_evidence": False,
        "same_method_for_every_capture_action_and_node": True,
        "per_action_threshold_override": False,
        "per_node_threshold_override": False,
        "node_count": len(NODES),
        "metric_bin_count": int(len(centers)),
        "complete_ten_node_bin_fraction": float(np.mean(complete)),
        "unverified_prefix_duration_s": float((pre_start - episode_start) * 1e-9),
        "unverified_suffix_duration_s": float((episode_stop - post_stop) * 1e-9),
        "method_contract": dict(contract),
        "method_contract_sha256": hashlib.sha256(json.dumps(
            dict(contract), sort_keys=True, separators=(",", ":")
        ).encode()).hexdigest(),
    }
    return result


def phase_bounds(diagnostic: Mapping[str, Any], phase: str) -> tuple[int, int]:
    row = next(item for item in diagnostic["phases"] if item["phase"] == phase)
    return int(row["start_global_time_ns"]), int(row["stop_global_time_ns_exclusive"])
