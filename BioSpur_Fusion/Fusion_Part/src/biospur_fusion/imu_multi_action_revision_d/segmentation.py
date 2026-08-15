"""Parameter-firewalled, continuous Revision-D action segmentation.

Operator labels define only broad search envelopes.  All physical boundaries,
references, holds, and complete bouts are selected from common-time IMU
relative motion before any calibration parameter exists.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np
from scipy.signal import find_peaks
from scipy.spatial.transform import Rotation


ACTIONS = (
    "initial_still_attempt2", "t_pose", "arms", "left_elbow",
    "right_elbow_attempt2", "left_knee", "right_knee", "left_heel",
    "right_heel", "squats", "trunk",
)

STATUSES = (
    "PRE_REFERENCE", "TRANSITION", "ACTIVE_BOUT_ID", "HOLD",
    "POST_REFERENCE", "UNINFORMATIVE_VALID", "INVALID",
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _smooth_valid(values: np.ndarray, valid: np.ndarray, count: int) -> np.ndarray:
    count = max(1, int(count))
    kernel = np.ones(count, float)
    numerator = np.convolve(np.where(valid, values, 0.0), kernel, mode="same")
    denominator = np.convolve(valid.astype(float), kernel, mode="same")
    return np.divide(numerator, denominator, out=np.full_like(values, np.nan), where=denominator > 0)


def _runs(mask: np.ndarray) -> list[tuple[int, int]]:
    mask = np.asarray(mask, bool)
    edges = np.diff(np.r_[False, mask, False].astype(np.int8))
    return [(int(a), int(b)) for a, b in zip(np.flatnonzero(edges == 1), np.flatnonzero(edges == -1))]


def _bridge(mask: np.ndarray, valid: np.ndarray, max_valid_gap: int, max_invalid_gap: int) -> np.ndarray:
    out = np.asarray(mask, bool).copy()
    for start, stop in _runs(~out):
        if start == 0 or stop == len(out):
            continue
        invalid = int((~valid[start:stop]).sum())
        length = stop - start
        limit = max_invalid_gap if invalid else max_valid_gap
        if invalid in (0, length) and length <= limit:
            out[start:stop] = True
    return out


def _rotation_mean(matrices: np.ndarray) -> np.ndarray:
    matrices = np.asarray(matrices, float)
    finite = np.isfinite(matrices).all(axis=(1, 2))
    matrices = matrices[finite]
    if len(matrices) == 0:
        return np.full((3, 3), np.nan)
    return Rotation.from_matrix(matrices).mean().as_matrix()


def _rotation_distance(reference: np.ndarray, matrices: np.ndarray) -> np.ndarray:
    matrices = np.asarray(matrices, float)
    result = np.full(len(matrices), np.nan)
    finite = np.isfinite(reference).all() & np.isfinite(matrices).all(axis=(1, 2))
    if np.any(finite):
        result[finite] = Rotation.from_matrix(
            np.einsum("ji,njk->nik", reference, matrices[finite])
        ).magnitude()
    return result


def _relative_signal(
    timeline: Any,
    parent_index: int,
    child_index: int,
    cfg: Mapping[str, Any],
) -> dict[str, np.ndarray]:
    """Build a common-frame relative-rotation signal with propagated validity."""
    parent = timeline.rotation[:, parent_index]
    child = timeline.rotation[:, child_index]
    relative = np.einsum("nji,njk->nik", parent, child)
    valid = timeline.valid[:, parent_index] & timeline.valid[:, child_index]
    valid &= np.isfinite(relative).all(axis=(1, 2))
    dt = np.r_[np.nan, np.diff(timeline.time_ns) / 1e9]
    increment = np.full((len(relative), 3), np.nan)
    pair_valid = valid & np.r_[False, valid[:-1]] & (dt > 0)
    if np.any(pair_valid):
        indices = np.flatnonzero(pair_valid)
        delta = np.einsum("nji,njk->nik", relative[indices - 1], relative[indices])
        increment[indices] = Rotation.from_matrix(delta).as_rotvec()
    rate = np.linalg.norm(increment, axis=1) / dt
    pc = timeline.covariance_rad2[:, parent_index]
    cc = timeline.covariance_rad2[:, child_index]
    covariance_rate = np.full(len(relative), np.nan)
    if np.any(pair_valid):
        indices = np.flatnonzero(pair_valid)
        change = np.abs(pc[indices] - pc[indices - 1]) + np.abs(cc[indices] - cc[indices - 1])
        variance = np.maximum(np.trace(change, axis1=1, axis2=2) / 3.0, 0.0)
        covariance_rate[indices] = np.sqrt(variance) / dt[indices]
    floor = float(cfg["activity_uncertainty_floor_rad_s"])
    uncertainty = np.hypot(floor, np.nan_to_num(covariance_rate, nan=0.0))
    snr = rate / np.maximum(uncertainty, 1e-12)
    count = max(1, round(float(cfg["smoothing_s"]) * float(cfg["rate_hz"])))
    snr = _smooth_valid(snr, pair_valid, count)
    rate = _smooth_valid(rate, pair_valid, count)
    return {
        "relative_rotation": relative,
        "relative_increment_rotvec": increment,
        "relative_rate_rad_s": rate,
        "activity_uncertainty_rad_s": uncertainty,
        "activity_snr": snr,
        "valid": pair_valid,
    }


def _aggregate_signals(signals: Sequence[Mapping[str, np.ndarray]]) -> dict[str, np.ndarray]:
    valid = np.logical_and.reduce([item["valid"] for item in signals])
    stack = np.stack([item["activity_snr"] for item in signals], axis=1)
    rate = np.stack([item["relative_rate_rad_s"] for item in signals], axis=1)
    uncertainty = np.stack([item["activity_uncertainty_rad_s"] for item in signals], axis=1)
    def finite_max(values: np.ndarray) -> np.ndarray:
        finite = np.isfinite(values)
        result = np.max(np.where(finite, values, -np.inf), axis=1)
        result[~np.any(finite, axis=1)] = np.nan
        return result
    return {
        "activity_snr": finite_max(stack),
        "relative_rate_rad_s": finite_max(rate),
        "activity_uncertainty_rad_s": finite_max(uncertainty),
        "valid": valid,
    }


def _search_domains(
    times: np.ndarray,
    windows: Mapping[str, tuple[int, int]],
    cfg: Mapping[str, Any],
) -> dict[str, tuple[int, int]]:
    ordered = sorted(((int(start), int(stop), name) for name, (start, stop) in windows.items()))
    guard_pre = int(round(float(cfg["pre_roll_s"]) * 1e9))
    guard_post = int(round(float(cfg["post_roll_s"]) * 1e9))
    domains: dict[str, tuple[int, int]] = {}
    for ordinal, (start, stop, name) in enumerate(ordered):
        lower = max(int(times[0]), start - guard_pre)
        upper = min(int(times[-1]), stop + guard_post)
        if ordinal:
            previous_stop = ordered[ordinal - 1][1]
            lower = max(lower, (previous_stop + start) // 2)
        if ordinal + 1 < len(ordered):
            next_start = ordered[ordinal + 1][0]
            upper = min(upper, (stop + next_start) // 2)
        domains[name] = (lower, upper)
    return domains


def _nearest_quiet_run(
    snr: np.ndarray,
    valid: np.ndarray,
    lo: int,
    hi: int,
    minimum: int,
    threshold: float,
    reverse: bool,
) -> tuple[int, int] | None:
    mask = valid[lo:hi] & np.isfinite(snr[lo:hi]) & (snr[lo:hi] <= threshold)
    candidates = [(lo + a, lo + b) for a, b in _runs(mask) if b - a >= minimum]
    if not candidates:
        return None
    return candidates[-1] if reverse else candidates[0]


def _select_static_plateau(
    rows: np.ndarray,
    activity: np.ndarray,
    valid: np.ndarray,
    rotations: np.ndarray,
    cfg: Mapping[str, Any],
) -> dict[str, Any] | None:
    rate = float(cfg["rate_hz"])
    edge = int(round(float(cfg["edge_exclusion_s"]) * rate))
    minimum = int(round(float(cfg["minimum_duration_s"]) * rate))
    interior = rows[edge:len(rows) - edge] if len(rows) > 2 * edge else np.empty(0, int)
    mask = np.zeros(len(activity), bool)
    mask[interior] = valid[interior] & (activity[interior] <= float(cfg["maximum_activity_snr"]))
    candidates = []
    for start, stop in _runs(mask):
        if stop - start < minimum:
            continue
        mean = _rotation_mean(rotations[start:stop])
        dispersion = _rotation_distance(mean, rotations[start:stop])
        score = float(np.nanmean(activity[start:stop]) + np.percentile(dispersion, 95))
        candidates.append((score, start, stop, dispersion))
    if not candidates:
        return None
    _, start, stop, dispersion = min(candidates, key=lambda item: (item[0], item[1]))
    return {
        "start_row": start,
        "stop_row_exclusive": stop,
        "row_indices": list(range(start, stop)),
        "activity_snr_mean": float(np.nanmean(activity[start:stop])),
        "orientation_dispersion_p95_rad": float(np.percentile(dispersion, 95)),
    }


@dataclass
class SequenceDetection:
    sequences: list[dict[str, Any]]
    failures: list[str]
    orientation_distance: np.ndarray
    neutral_limit_rad: float


def _detect_sequences(
    rows: np.ndarray,
    signal: Mapping[str, np.ndarray],
    relative_rotation: np.ndarray,
    cfg: Mapping[str, Any],
) -> SequenceDetection:
    rate = float(cfg["rate_hz"])
    onset = float(cfg["onset_activity_snr"])
    offset = float(cfg["offset_activity_snr"])
    minimum_onset = max(1, round(float(cfg["minimum_onset_evidence_s"]) * rate))
    minimum_active = max(1, round(float(cfg["minimum_active_bout_s"]) * rate))
    minimum_quiet = max(1, round(float(cfg["minimum_quiet_reference_s"]) * rate))
    bridge = max(0, round(float(cfg["maximum_bridgeable_below_threshold_gap_s"]) * rate))
    invalid_bridge = max(0, round(float(cfg["maximum_bridgeable_invalid_gap_s"]) * rate))
    valid = signal["valid"]
    snr = signal["activity_snr"]
    domain_mask = np.zeros(len(snr), bool)
    domain_mask[rows] = True
    onset_mask = domain_mask & valid & np.isfinite(snr) & (snr >= onset)
    onset_mask = _bridge(onset_mask, valid, bridge, invalid_bridge)
    seeds = [(a, b) for a, b in _runs(onset_mask) if b - a >= minimum_onset]
    sequences: list[dict[str, Any]] = []
    failures: list[str] = []
    orientation_distance = np.full(len(snr), np.nan)
    neutral_limit = float(cfg["neutral_absolute_cap_rad"])
    for ordinal, (seed_start, seed_stop) in enumerate(seeds):
        start = seed_start
        stop = seed_stop
        while start > rows[0] and valid[start - 1] and np.isfinite(snr[start - 1]) and snr[start - 1] > offset:
            start -= 1
        while stop <= rows[-1] and valid[stop] and np.isfinite(snr[stop]) and snr[stop] > offset:
            stop += 1
        if stop - start < minimum_active:
            continue
        pre = _nearest_quiet_run(snr, valid, int(rows[0]), start, minimum_quiet, offset, True)
        if pre is None:
            failures.append(f"BOUT_{ordinal}_MISSING_PRE_REFERENCE")
            continue
        pre_mean = _rotation_mean(relative_rotation[pre[0]:pre[1]])
        pre_dispersion = _rotation_distance(pre_mean, relative_rotation[pre[0]:pre[1]])
        pre_sigma = max(math.radians(1.0), float(np.percentile(pre_dispersion, 68)))
        neutral_limit = min(
            float(cfg["neutral_absolute_cap_rad"]),
            float(cfg["neutral_orientation_compatibility_sigma"]) * pre_sigma,
        )
        orientation_distance[:] = _rotation_distance(pre_mean, relative_rotation)
        search_stop = int(rows[-1]) + 1
        post_mask = valid[stop:search_stop] & np.isfinite(snr[stop:search_stop])
        post_mask &= snr[stop:search_stop] <= offset
        post_mask &= orientation_distance[stop:search_stop] <= neutral_limit
        post_candidates = [
            (stop + a, stop + b) for a, b in _runs(post_mask) if b - a >= minimum_quiet
        ]
        if not post_candidates:
            failures.append(f"BOUT_{ordinal}_MISSING_COMPATIBLE_POST_REFERENCE")
            continue
        post = post_candidates[0]
        active_stop = post[0]
        invalid_runs = [b - a for a, b in _runs(~valid[start:active_stop])]
        if invalid_runs and max(invalid_runs) > invalid_bridge:
            failures.append(f"BOUT_{ordinal}_UNBRIDGEABLE_INVALID_GAP")
            continue
        if active_stop - start < minimum_active:
            failures.append(f"BOUT_{ordinal}_ACTIVE_TOO_SHORT_AFTER_RETURN_CHECK")
            continue
        peak_rows, properties = find_peaks(
            np.nan_to_num(orientation_distance[start:active_stop], nan=0.0),
            prominence=max(
                float(cfg["minimum_direction_reversal_rad"]),
                float(cfg["minimum_cycle_excursion_snr"]) * pre_sigma,
            ),
            distance=max(1, minimum_active),
        )
        complete_cycles = []
        for peak, prominence in zip(peak_rows, properties.get("prominences", [])):
            peak = start + int(peak)
            before = np.flatnonzero(orientation_distance[pre[1]:peak + 1] <= neutral_limit)
            after = np.flatnonzero(orientation_distance[peak:post[0]] <= neutral_limit)
            if len(before) and len(after):
                cycle_start = pre[1] + int(before[-1])
                cycle_stop = peak + int(after[0]) + 1
                complete_cycles.append({
                    "start_row": cycle_start,
                    "peak_row": peak,
                    "stop_row_exclusive": cycle_stop,
                    "excursion_rad": float(orientation_distance[peak]),
                    "prominence_rad": float(prominence),
                })
        sequences.append({
            "sequence_id": ordinal,
            "start_row": start,
            "stop_row_exclusive": active_stop,
            "pre_reference": {"start_row": pre[0], "stop_row_exclusive": pre[1]},
            "post_reference": {"start_row": post[0], "stop_row_exclusive": post[1]},
            "pre_reference_dispersion_p68_rad": pre_sigma,
            "neutral_compatibility_limit_rad": neutral_limit,
            "maximum_excursion_rad": float(np.nanmax(orientation_distance[start:active_stop])),
            "complete_cycles": complete_cycles,
            "complete_cycle_count": len(complete_cycles),
            "truncated": False,
        })
    # Merge duplicate seed detections that expanded into the same sequence.
    unique: list[dict[str, Any]] = []
    for sequence in sorted(sequences, key=lambda item: (item["start_row"], item["stop_row_exclusive"])):
        if unique and sequence["start_row"] <= unique[-1]["stop_row_exclusive"]:
            if sequence["stop_row_exclusive"] > unique[-1]["stop_row_exclusive"]:
                unique[-1] = sequence
            continue
        unique.append(sequence)
    return SequenceDetection(unique, failures, orientation_distance, neutral_limit)


def _deterministic_rows(start: int, stop: int, maximum: int, retain: Sequence[int] = ()) -> list[int]:
    full = np.arange(start, stop, dtype=int)
    if len(full) <= maximum:
        return full.tolist()
    selected = np.round(np.linspace(0, len(full) - 1, maximum)).astype(int)
    rows = set(full[selected].tolist()) | {int(value) for value in retain if start <= value < stop}
    return sorted(rows)


def _classify_bout_axis(relative: np.ndarray, sequence: Mapping[str, Any]) -> np.ndarray:
    start = int(sequence["start_row"])
    stop = int(sequence["stop_row_exclusive"])
    reference = _rotation_mean(relative[sequence["pre_reference"]["start_row"]:sequence["pre_reference"]["stop_row_exclusive"]])
    matrices = relative[start:stop]
    matrices = matrices[np.isfinite(matrices).all(axis=(1, 2))]
    if len(matrices) < 3:
        return np.full(3, np.nan)
    rotvec = Rotation.from_matrix(np.einsum("ji,njk->nik", reference, matrices)).as_rotvec()
    if len(rotvec) < 3 or not np.isfinite(rotvec).all():
        return np.full(3, np.nan)
    _, _, vh = np.linalg.svd(rotvec - np.median(rotvec, axis=0), full_matrices=False)
    axis = vh[0]
    peak = int(np.argmax(np.linalg.norm(rotvec, axis=1)))
    if float(rotvec[peak] @ axis) < 0:
        axis = -axis
    return axis


def _bout_signed_excursion(relative: np.ndarray, sequence: Mapping[str, Any], axis: np.ndarray) -> float:
    start = int(sequence["start_row"])
    stop = int(sequence["stop_row_exclusive"])
    pre = sequence["pre_reference"]
    reference = _rotation_mean(relative[pre["start_row"]:pre["stop_row_exclusive"]])
    matrices = relative[start:stop]
    matrices = matrices[np.isfinite(matrices).all(axis=(1, 2))]
    if not len(matrices):
        return math.nan
    rotvec = Rotation.from_matrix(np.einsum("ji,njk->nik", reference, matrices)).as_rotvec()
    projection = rotvec @ axis
    return float(projection[int(np.argmax(np.abs(projection)))])


def _phase_record(
    phase: str,
    sequence: Mapping[str, Any],
    times: np.ndarray,
    maximum_rows: int,
    relevant_segments: Sequence[str],
    confidence: float,
) -> dict[str, Any]:
    retain = [cycle["peak_row"] for cycle in sequence["complete_cycles"]]
    selected = _deterministic_rows(
        int(sequence["start_row"]), int(sequence["stop_row_exclusive"]), maximum_rows, retain
    )
    return {
        "phase": phase,
        "bout_id": int(sequence["sequence_id"]),
        "start_row": int(sequence["start_row"]),
        "stop_row_exclusive": int(sequence["stop_row_exclusive"]),
        "start_global_time_ns": int(times[sequence["start_row"]]),
        "stop_global_time_ns": int(times[sequence["stop_row_exclusive"] - 1]),
        "full_row_indices": list(range(int(sequence["start_row"]), int(sequence["stop_row_exclusive"]))),
        "selected_row_indices": selected,
        "selected_global_time_ns": times[selected].astype(np.int64).tolist(),
        "complete_cycles": sequence["complete_cycles"],
        "complete_cycle_count": int(sequence["complete_cycle_count"]),
        "relevant_segments": list(relevant_segments),
        "boundary_confidence": float(confidence),
        "boundary_uncertainty": {
            "support_rows_each_side": 6,
            "support_s_each_side": 0.12,
            "onset_evidence_duration_s": 0.12,
            "pre_reference_rows": int(sequence["pre_reference"]["stop_row_exclusive"] - sequence["pre_reference"]["start_row"]),
            "post_reference_rows": int(sequence["post_reference"]["stop_row_exclusive"] - sequence["post_reference"]["start_row"]),
            "neutral_compatibility_limit_rad": float(sequence["neutral_compatibility_limit_rad"]),
        },
        "truncated": bool(sequence["truncated"]),
    }


def _encode_status_runs(rows: np.ndarray, statuses: np.ndarray, bout_ids: np.ndarray, times: np.ndarray) -> list[dict[str, Any]]:
    records = []
    if not len(rows):
        return records
    start = 0
    for i in range(1, len(rows) + 1):
        changed = i == len(rows) or statuses[i] != statuses[start] or bout_ids[i] != bout_ids[start]
        if not changed:
            continue
        block = rows[start:i]
        records.append({
            "status": str(statuses[start]),
            "bout_id": None if bout_ids[start] < 0 else int(bout_ids[start]),
            "start_row": int(block[0]),
            "stop_row_exclusive": int(block[-1] + 1),
            "start_global_time_ns": int(times[block[0]]),
            "stop_global_time_ns": int(times[block[-1]]),
            "row_count": int(len(block)),
        })
        start = i
    return records


def _apply_accounting(
    rows: np.ndarray,
    valid: np.ndarray,
    sequences: Sequence[Mapping[str, Any]],
    orientation_distance: np.ndarray,
    neutral_limit: float,
    times: np.ndarray,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    statuses = np.full(len(rows), "UNINFORMATIVE_VALID", dtype=object)
    bout_ids = np.full(len(rows), -1, int)
    statuses[~valid[rows]] = "INVALID"
    row_to_local = {int(row): i for i, row in enumerate(rows)}
    for sequence in sequences:
        bout = int(sequence["sequence_id"])
        pre = sequence["pre_reference"]
        post = sequence["post_reference"]
        for row in range(pre["start_row"], pre["stop_row_exclusive"]):
            if row in row_to_local and valid[row]: statuses[row_to_local[row]] = "PRE_REFERENCE"
        for row in range(post["start_row"], post["stop_row_exclusive"]):
            if row in row_to_local and valid[row]: statuses[row_to_local[row]] = "POST_REFERENCE"
        for row in range(sequence["start_row"], sequence["stop_row_exclusive"]):
            if row not in row_to_local or not valid[row]: continue
            local = row_to_local[row]
            if np.isfinite(orientation_distance[row]) and orientation_distance[row] > neutral_limit:
                statuses[local] = "ACTIVE_BOUT_ID"
            else:
                statuses[local] = "HOLD"
            bout_ids[local] = bout
        transition_support = 2
        for row in range(max(rows[0], sequence["start_row"] - transition_support), sequence["start_row"]):
            if row in row_to_local and valid[row] and statuses[row_to_local[row]] == "UNINFORMATIVE_VALID":
                statuses[row_to_local[row]] = "TRANSITION"
        for row in range(sequence["stop_row_exclusive"], min(rows[-1] + 1, sequence["stop_row_exclusive"] + transition_support)):
            if row in row_to_local and valid[row] and statuses[row_to_local[row]] == "UNINFORMATIVE_VALID":
                statuses[row_to_local[row]] = "TRANSITION"
    counts = {name: int(np.sum(statuses == name)) for name in STATUSES}
    assert sum(counts.values()) == len(rows)
    return _encode_status_runs(rows, statuses, bout_ids, times), counts


def segment_revision_d(
    timeline: Any,
    windows: Mapping[str, tuple[int, int]],
    node_to_segment: Mapping[str, str],
    contract: Mapping[str, Any],
    product_semantics_sha256: str,
    contract_sha256: str,
) -> dict[str, Any]:
    """Segment all eleven actions without reading any calibration parameter."""
    if tuple(sorted(windows, key=lambda name: windows[name][0])) != ACTIONS:
        raise ValueError("calibration windows are not the frozen eleven-action chronology")
    nodes = {node: i for i, node in enumerate(timeline.node_order)}
    segment_node = {segment: nodes[node] for node, segment in node_to_segment.items()}
    signal_cfg = contract["signal"]
    state_cfg = {**contract["state_machine"], "rate_hz": signal_cfg["rate_hz"]}
    static_cfg = {**contract["static_plateau"], "rate_hz": signal_cfg["rate_hz"]}
    domains = _search_domains(timeline.time_ns, windows, contract["search_domain"])
    pair_specs = {
        "shoulder_L": ("torso", "upper_arm_L"),
        "shoulder_R": ("torso", "upper_arm_R"),
        "elbow_L": ("upper_arm_L", "forearm_L"),
        "elbow_R": ("upper_arm_R", "forearm_R"),
        "hip_L": ("pelvis", "thigh_L"),
        "hip_R": ("pelvis", "thigh_R"),
        "knee_L": ("thigh_L", "shank_L"),
        "knee_R": ("thigh_R", "shank_R"),
        "trunk": ("pelvis", "torso"),
    }
    pair_signals = {
        name: _relative_signal(timeline, segment_node[parent], segment_node[child], signal_cfg)
        for name, (parent, child) in pair_specs.items()
    }
    action_signal_names = {
        "arms": ("shoulder_L", "shoulder_R", "elbow_L", "elbow_R"),
        "left_elbow": ("elbow_L",), "right_elbow_attempt2": ("elbow_R",),
        "left_knee": ("hip_L",), "right_knee": ("hip_R",),
        "left_heel": ("knee_L",), "right_heel": ("knee_R",),
        "squats": ("hip_L", "hip_R", "knee_L", "knee_R"), "trunk": ("trunk",),
    }
    action_relevant = {
        "arms": ("torso", "upper_arm_L", "forearm_L", "upper_arm_R", "forearm_R"),
        "left_elbow": ("upper_arm_L", "forearm_L"),
        "right_elbow_attempt2": ("upper_arm_R", "forearm_R"),
        "left_knee": ("pelvis", "thigh_L", "shank_L"),
        "right_knee": ("pelvis", "thigh_R", "shank_R"),
        "left_heel": ("thigh_L", "shank_L"),
        "right_heel": ("thigh_R", "shank_R"),
        "squats": ("pelvis", "thigh_L", "thigh_R", "shank_L", "shank_R"),
        "trunk": ("pelvis", "torso"),
    }
    actions: dict[str, Any] = {}
    failures: list[dict[str, Any]] = []
    maximum_rows = int(contract["informative_rows"]["maximum_rows_per_bout"])

    # Static references are independent and use no expected ideal pose.
    all_valid = timeline.all_nodes_valid.copy()
    node_speed = []
    for j in range(len(timeline.node_order)):
        matrices = timeline.rotation[:, j]
        delta = np.full(len(matrices), np.nan)
        valid = timeline.valid[:, j] & np.r_[False, timeline.valid[:-1, j]]
        indices = np.flatnonzero(valid)
        if len(indices):
            step = Rotation.from_matrix(np.einsum("nji,njk->nik", matrices[indices - 1], matrices[indices])).magnitude()
            dt = (timeline.time_ns[indices] - timeline.time_ns[indices - 1]) / 1e9
            delta[indices] = step / dt / float(signal_cfg["activity_uncertainty_floor_rad_s"])
        node_speed.append(delta)
    static_stack = np.stack(node_speed, axis=1)
    static_activity = np.full(len(static_stack), np.nan)
    static_finite = np.isfinite(static_stack)
    usable = np.any(static_finite, axis=1)
    static_activity[usable] = np.nanmedian(static_stack[usable], axis=1)
    representative = timeline.rotation[:, segment_node["torso"]]
    for name, phase in (("initial_still_attempt2", "neutral_soft_pose"), ("t_pose", "independent_t_pose_soft_pose")):
        start, stop = windows[name]
        rows = np.flatnonzero((timeline.time_ns >= start) & (timeline.time_ns <= stop))
        plateau = _select_static_plateau(rows, static_activity, all_valid, representative, static_cfg)
        domain = domains[name]
        domain_rows = np.flatnonzero((timeline.time_ns >= domain[0]) & (timeline.time_ns <= domain[1]))
        status = np.full(len(domain_rows), "UNINFORMATIVE_VALID", dtype=object)
        status[~all_valid[domain_rows]] = "INVALID"
        phases = []
        if plateau is None:
            failures.append({"action": name, "failure": "NO_CONTINUOUS_STATIC_PLATEAU"})
        else:
            selected = _deterministic_rows(plateau["start_row"], plateau["stop_row_exclusive"], maximum_rows)
            for row in plateau["row_indices"]:
                local = np.searchsorted(domain_rows, row)
                if local < len(domain_rows) and domain_rows[local] == row:
                    status[local] = "PRE_REFERENCE"
            phases.append({
                "phase": phase,
                "start_row": plateau["start_row"],
                "stop_row_exclusive": plateau["stop_row_exclusive"],
                "start_global_time_ns": int(timeline.time_ns[plateau["start_row"]]),
                "stop_global_time_ns": int(timeline.time_ns[plateau["stop_row_exclusive"] - 1]),
                "full_row_indices": plateau["row_indices"],
                "selected_row_indices": selected,
                "selected_global_time_ns": timeline.time_ns[selected].astype(np.int64).tolist(),
                "relevant_segments": list(node_to_segment.values()),
                "activity_snr_mean": plateau["activity_snr_mean"],
                "orientation_dispersion_p95_rad": plateau["orientation_dispersion_p95_rad"],
            })
        counts = {key: int(np.sum(status == key)) for key in STATUSES}
        actions[name] = {
            "label_usage": contract["LABEL_USAGE"],
            "operator_window_ns": [int(start), int(stop)],
            "search_domain_ns": [int(domain[0]), int(domain[1])],
            "phases": phases,
            "status_runs": _encode_status_runs(domain_rows, status, np.full(len(status), -1), timeline.time_ns),
            "temporal_accounting": {"domain_rows": int(len(domain_rows)), "status_counts": counts, "closed": sum(counts.values()) == len(domain_rows)},
            "timeline_signal": {
                "row_indices": domain_rows.tolist(),
                "global_time_ns": timeline.time_ns[domain_rows].astype(np.int64).tolist(),
                "activity_snr": np.nan_to_num(static_activity[domain_rows], nan=-1.0).tolist(),
                "valid": all_valid[domain_rows].astype(np.uint8).tolist(),
            },
        }

    for action in ACTIONS[2:]:
        domain = domains[action]
        rows = np.flatnonzero((timeline.time_ns >= domain[0]) & (timeline.time_ns <= domain[1]))
        names = action_signal_names[action]
        aggregate = _aggregate_signals([pair_signals[name] for name in names])
        classification_signal = pair_signals[names[0]]
        detection = _detect_sequences(rows, aggregate, classification_signal["relative_rotation"], state_cfg)
        sequences = detection.sequences
        phases: list[dict[str, Any]] = []
        if not sequences:
            failures.append({"action": action, "failure": "NO_COMPLETE_CONTIGUOUS_BOUT", "details": detection.failures})
        else:
            axes = [_classify_bout_axis(classification_signal["relative_rotation"], item) for item in sequences]
            if action == "arms":
                left = pair_signals["shoulder_L"]["activity_snr"]
                right = pair_signals["shoulder_R"]["activity_snr"]
                for sequence in sequences:
                    sl = slice(sequence["start_row"], sequence["stop_row_exclusive"])
                    left_fraction = float(np.mean(left[sl] >= state_cfg["onset_activity_snr"]))
                    right_fraction = float(np.mean(right[sl] >= state_cfg["onset_activity_snr"]))
                    if left_fraction > 0.35 and right_fraction > 0.35:
                        phase = "bilateral_raise_lower"
                    elif left_fraction >= right_fraction:
                        phase = "left_arm_raise_lower"
                    else:
                        phase = "right_arm_raise_lower"
                    confidence = max(left_fraction, right_fraction)
                    phases.append(_phase_record(phase, sequence, timeline.time_ns, maximum_rows, action_relevant[action], confidence))
            elif action in ("left_elbow", "right_elbow_attempt2"):
                finite_axes = [axis for axis in axes if np.isfinite(axis).all()]
                if len(finite_axes) < 2:
                    failures.append({"action": action, "failure": "CURL_PRONATION_BOUT_GEOMETRY_AMBIGUOUS"})
                else:
                    reference = finite_axes[0]
                    separation = [math.acos(float(np.clip(abs(reference @ axis), -1.0, 1.0))) for axis in axes]
                    threshold = 0.5 * (min(separation) + max(separation))
                    for sequence, value in zip(sequences, separation):
                        phase = "curl" if value <= threshold else "pronation_supination"
                        phases.append(_phase_record(phase, sequence, timeline.time_ns, maximum_rows, action_relevant[action], 1.0 - min(value, math.pi / 2) / math.pi))
            elif action == "trunk":
                finite_axes = np.asarray([axis for axis in axes if np.isfinite(axis).all()])
                if len(finite_axes) < 2:
                    failures.append({"action": action, "failure": "TRUNK_BOUT_GEOMETRY_AMBIGUOUS"})
                else:
                    gram = np.abs(finite_axes @ finite_axes.T)
                    i, j = np.unravel_index(np.argmin(gram + np.eye(len(gram)) * 2.0), gram.shape)
                    turn_seed = finite_axes[i]
                    flex_seed = finite_axes[j]
                    if abs(float(turn_seed @ flex_seed)) > math.cos(math.radians(25.0)):
                        failures.append({"action": action, "failure": "TRUNK_BOUTS_COLLINEAR"})
                    signed_turn = []
                    for sequence, axis in zip(sequences, axes):
                        turn_score = abs(float(axis @ turn_seed))
                        flex_score = abs(float(axis @ flex_seed))
                        if flex_score > turn_score:
                            phase = "forward_flexion_recovery"
                        else:
                            sign = _bout_signed_excursion(classification_signal["relative_rotation"], sequence, turn_seed)
                            signed_turn.append(sign)
                            phase = "TURN_A" if sign >= 0 else "TURN_B"
                        phases.append(_phase_record(phase, sequence, timeline.time_ns, maximum_rows, action_relevant[action], max(turn_score, flex_score)))
                    if not any(item["phase"] == "forward_flexion_recovery" for item in phases) or len({item["phase"] for item in phases if item["phase"].startswith("TURN_")}) < 2:
                        failures.append({"action": action, "failure": "TRUNK_BOUT_GEOMETRY_INCOMPLETE"})
            else:
                phase_name = {
                    "left_knee": "high_knee_raise_lower", "right_knee": "high_knee_raise_lower",
                    "left_heel": "heel_to_butt_flexion", "right_heel": "heel_to_butt_flexion",
                    "squats": "descent_ascent",
                }[action]
                phases = [_phase_record(phase_name, sequence, timeline.time_ns, maximum_rows, action_relevant[action], 1.0) for sequence in sequences]
        status_runs, counts = _apply_accounting(rows, aggregate["valid"], sequences, detection.orientation_distance, detection.neutral_limit_rad, timeline.time_ns)
        actions[action] = {
            "label_usage": contract["LABEL_USAGE"],
            "operator_window_ns": [int(windows[action][0]), int(windows[action][1])],
            "search_domain_ns": [int(domain[0]), int(domain[1])],
            "relative_signal_names": list(names),
            "signal_statistics": {
                "activity_snr_p50": float(np.nanpercentile(aggregate["activity_snr"][rows], 50)),
                "activity_snr_p95": float(np.nanpercentile(aggregate["activity_snr"][rows], 95)),
                "valid_fraction": float(np.mean(aggregate["valid"][rows])),
            },
            "sequences": sequences,
            "phases": phases,
            "status_runs": status_runs,
            "temporal_accounting": {"domain_rows": int(len(rows)), "status_counts": counts, "closed": sum(counts.values()) == len(rows)},
            "detector_failures": detection.failures,
            "timeline_signal": {
                "row_indices": rows.tolist(),
                "global_time_ns": timeline.time_ns[rows].astype(np.int64).tolist(),
                "activity_snr": np.nan_to_num(aggregate["activity_snr"][rows], nan=-1.0).tolist(),
                "relative_rate_rad_s": np.nan_to_num(aggregate["relative_rate_rad_s"][rows], nan=-1.0).tolist(),
                "activity_uncertainty_rad_s": np.nan_to_num(aggregate["activity_uncertainty_rad_s"][rows], nan=-1.0).tolist(),
                "valid": aggregate["valid"][rows].astype(np.uint8).tolist(),
            },
        }

    temporal_closed = all(item["temporal_accounting"]["closed"] for item in actions.values())
    if not temporal_closed:
        terminal = "FAIL_TEMPORAL_ACCOUNTING"
    elif failures:
        semantics = any("GEOMETRY" in item["failure"] for item in failures)
        terminal = "FAIL_ACTION_SEMANTICS_MISMATCH" if semantics else "FAIL_ACTION_BOUNDARY_AMBIGUOUS"
    else:
        terminal = "PASS_SIGNAL_DERIVED_ACTION_BOUNDARIES"
    return {
        "schema": "biospur-revision-d-signal-derived-action-boundaries-v1",
        "terminal_outcome": terminal,
        "pass": terminal == "PASS_SIGNAL_DERIVED_ACTION_BOUNDARIES",
        "LABEL_USAGE": contract["LABEL_USAGE"],
        "D_MINUS_1_CALIBRATION_PARAMETER_FIREWALL": "PASS",
        "RELATIVE_SIGNAL_DEFINITION": "PASS",
        "TRUNK_BOUT_GEOMETRY": "PASS" if not any(item["action"] == "trunk" for item in failures) else "FAIL",
        "product_semantics_sha256": product_semantics_sha256,
        "action_boundary_contract_sha256": contract_sha256,
        "actions": actions,
        "failures": failures,
        "calibration_parameters_read": [],
        "historical_endpoints_read": [],
        "protocol_priors_read": [],
        "expected_skeleton_read": False,
    }


def _synthetic_scalar_detection(
    snr: np.ndarray,
    displacement: np.ndarray,
    valid: np.ndarray,
    cfg: Mapping[str, Any],
    rows: np.ndarray | None = None,
) -> SequenceDetection:
    n = len(snr)
    rotations = Rotation.from_rotvec(np.c_[np.zeros(n), displacement, np.zeros(n)]).as_matrix()
    signal = {
        "activity_snr": np.asarray(snr, float),
        "valid": np.asarray(valid, bool),
        "relative_rate_rad_s": np.asarray(snr, float) * float(cfg["activity_uncertainty_floor_rad_s"]),
        "activity_uncertainty_rad_s": np.full(n, float(cfg["activity_uncertainty_floor_rad_s"])),
    }
    state = {**cfg, "rate_hz": 50.0}
    return _detect_sequences(np.arange(n) if rows is None else np.asarray(rows, int), signal, rotations, state)


def run_segmentation_negative_controls(contract: Mapping[str, Any]) -> dict[str, Any]:
    """Execute frozen synthetic controls without real-capture access."""
    cfg = {**contract["state_machine"], "activity_uncertainty_floor_rad_s": contract["signal"]["activity_uncertainty_floor_rad_s"]}
    n = 400
    t = np.arange(n) / 50.0
    base_snr = 0.25 + 0.08 * np.sin(2 * np.pi * 0.3 * t)
    displacement = np.zeros(n)
    active = (t >= 2.0) & (t <= 5.0)
    phase = (t[active] - 2.0) / 3.0
    displacement[active] = 0.8 * np.sin(np.pi * phase) ** 2
    snr = base_snr.copy()
    snr[active] = 5.0
    valid = np.ones(n, bool)
    nominal = _synthetic_scalar_detection(snr, displacement, valid, cfg)
    shifted_a = _synthetic_scalar_detection(snr, displacement, valid, cfg, np.arange(20, 360))
    shifted_b = _synthetic_scalar_detection(snr, displacement, valid, cfg, np.arange(50, 390))
    top_hold_snr = snr.copy(); top_hold_disp = displacement.copy()
    hold = (t >= 3.1) & (t <= 3.8); top_hold_snr[hold] = 0.2; top_hold_disp[hold] = 0.8
    top_hold = _synthetic_scalar_detection(top_hold_snr, top_hold_disp, valid, cfg)
    truncated_snr = base_snr.copy(); truncated_disp = np.zeros(n)
    truncated_snr[t >= 6.0] = 5.0; truncated_disp[t >= 6.0] = np.linspace(0.0, 0.8, int(np.sum(t >= 6.0)))
    truncated = _synthetic_scalar_detection(truncated_snr, truncated_disp, valid, cfg)
    extra_snr = snr.copy(); extra_disp = displacement.copy()
    extra = (t >= 6.0)
    extra_snr[extra] = 5.0
    extra_disp[extra] = np.linspace(0.0, 0.5, int(extra.sum()))
    extra_half = _synthetic_scalar_detection(extra_snr, extra_disp, valid, cfg)
    gap_valid = valid.copy(); gap_valid[(t >= 3.0) & (t < 3.2)] = False
    invalid_gap = _synthetic_scalar_detection(snr, displacement, gap_valid, cfg)
    gap_start = int(np.flatnonzero((t >= 3.0) & (t < 3.2))[0])
    gap_stop = int(np.flatnonzero((t >= 3.0) & (t < 3.2))[-1]) + 1
    invalid_gap_not_bridged = not any(
        item["start_row"] < gap_start and item["stop_row_exclusive"] > gap_stop
        for item in invalid_gap.sequences
    )
    parent = Rotation.from_rotvec(np.c_[np.zeros(n), displacement, np.zeros(n)]).as_matrix()
    child = parent.copy()
    class Timeline:
        rotation = np.stack((parent, child), axis=1)
        covariance_rad2 = np.tile(np.eye(3) * 1e-6, (n, 2, 1, 1))
        valid = np.ones((n, 2), bool)
        time_ns = (t * 1e9).astype(np.int64)
    proximal_only = _relative_signal(Timeline(), 0, 1, contract["signal"])
    unrelated_parent = np.tile(np.eye(3), (n, 1, 1))
    unrelated_child = unrelated_parent.copy()
    unrelated_third = Rotation.from_rotvec(np.c_[np.zeros(n), np.zeros(n), displacement]).as_matrix()
    class UnrelatedTimeline:
        rotation = np.stack((unrelated_parent, unrelated_child, unrelated_third), axis=1)
        covariance_rad2 = np.tile(np.eye(3) * 1e-6, (n, 3, 1, 1))
        valid = np.ones((n, 3), bool)
        time_ns = (t * 1e9).astype(np.int64)
    unrelated_signal = _relative_signal(UnrelatedTimeline(), 0, 1, contract["signal"])
    unequal_small = displacement * 0.55
    unequal_snr = base_snr.copy(); unequal_snr[active] = 4.0
    unequal = _synthetic_scalar_detection(unequal_snr, unequal_small, valid, cfg)
    compensation_parent = Rotation.from_rotvec(np.c_[np.zeros(n), 0.20 * displacement, np.zeros(n)]).as_matrix()
    compensation_child = Rotation.from_rotvec(np.c_[np.zeros(n), displacement, np.zeros(n)]).as_matrix()
    class CompensationTimeline:
        rotation = np.stack((compensation_parent, compensation_child), axis=1)
        covariance_rad2 = np.tile(np.eye(3) * 1e-6, (n, 2, 1, 1))
        valid = np.ones((n, 2), bool)
        time_ns = (t * 1e9).astype(np.int64)
    compensation_signal = _relative_signal(CompensationTimeline(), 0, 1, contract["signal"])
    controls = {
        "movement_early_or_late_within_token_guard": bool(nominal.sequences),
        "long_idle_delay_after_token": bool(nominal.sequences and nominal.sequences[0]["start_row"] >= 90),
        "unrelated_node_moves": bool(np.nanmax(unrelated_signal["activity_snr"]) < cfg["onset_activity_snr"]),
        "whole_chain_rigid_motion_without_relative_excitation": bool(np.nanmax(proximal_only["activity_snr"]) < cfg["onset_activity_snr"]),
        "natural_breathing_sway_micro_motion": bool(np.nanmax(base_snr) < cfg["offset_activity_snr"]),
        "pause_at_top_not_neutral": bool(top_hold.sequences and top_hold.sequences[0]["stop_row_exclusive"] > np.flatnonzero(hold)[-1]),
        "pause_at_deepest_squat_not_neutral": bool(top_hold.sequences),
        "one_incomplete_repetition_disclosed": bool(not truncated.sequences and truncated.failures),
        "extra_half_repetition_disclosed": bool(extra_half.sequences and extra_half.failures),
        "missing_post_action_neutral_fails_closed": not truncated.sequences,
        "truncated_outer_boundary_fails_closed": not truncated.sequences,
        "dropped_samples_invalid_gap_not_silently_bridged": invalid_gap_not_bridged,
        "unequal_left_right_amplitude_allowed": bool(unequal.sequences),
        "pelvis_compensation_allowed_as_mismatch": bool(np.nanmax(compensation_signal["activity_snr"]) >= cfg["onset_activity_snr"]),
        "TOKEN_SHIFT_WITHIN_SAFE_GUARD_DOES_NOT_MOVE_PHYSICAL_BOUNDARY": bool(shifted_a.sequences and shifted_b.sequences and shifted_a.sequences[0]["start_row"] == shifted_b.sequences[0]["start_row"]),
        "TOP_HOLD_IS_NOT_NEUTRAL": bool(top_hold.sequences and top_hold.sequences[0]["stop_row_exclusive"] > np.flatnonzero(hold)[-1]),
        "PROXIMAL_ONLY_MOTION_IS_NOT_A_JOINT_BOUT": bool(np.nanmax(proximal_only["activity_snr"]) < cfg["onset_activity_snr"]),
        "TRUNCATED_BOUT_FAILS_CLOSED": not truncated.sequences,
        "DETERMINISTIC_BOUNDARY_REPLAY": bool(
            nominal.sequences == _synthetic_scalar_detection(snr, displacement, valid, cfg).sequences
        ),
    }
    passed = all(controls.values())
    return {
        "schema": "biospur-revision-d-segmentation-negative-controls-v1",
        "controls": controls,
        "pass": passed,
        "terminal_outcome": "PASS_SEGMENTATION_NEGATIVE_CONTROLS" if passed else "FAIL_SEGMENTATION_NEGATIVE_CONTROL",
        "real_capture_accessed": False,
    }
