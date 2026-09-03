#!/usr/bin/env python3
"""Bounded whole-capture raw/T4 yaw analysis for the frozen C1/C2/C3 ledgers."""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import gc
import hashlib
import json
import math
from pathlib import Path
import time

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from biospur_fusion.root_r4.contracts import wrap_degrees
from biospur_fusion.root_r4.data import C1Data, SEGMENT_POINT_JOINTS, m1_relative_points
from biospur_fusion.root_r5a.profiles import (
    RawGroups,
    _huber,
    _rotation,
    circular_profile,
    raw_evaluator,
    t4_evaluator,
)


REPOSITORY = Path("/mnt/nrf_ssd/nRF_dev/BioSpur_Fusion")
R4_ROOT = Path("/tmp/biospur_c1_uwb_imu_root_r4_20260824T100721Z")
R4_MANIFEST_EXPECTED = "fb5000d759e35abcc7d568711d4b5e2ef2d3cca6862c76f7b1cb75c3ec35e48e"
M1_ROOT = Path("/tmp/biospur_pure_imu_mvp_m1_20260823T135120Z")
UWB_ROOT = Path("/tmp/biospur_c123_uwb_counterfactual_20260823T155948Z")
SCHEDULE_PATH = Path("/tmp/biospur_c123_uwb_beacon_causal_replay_20260823T171739Z/C123_UWB_EVENT_SCHEDULE.npz")
EPOCH_AUDIT_PATH = Path("/tmp/biospur_c123_uwb_beacon_causal_replay_20260823T171739Z/C123_BEACON_EPOCH_MAPPING_AUDIT.json")
RAW_PATH = UWB_ROOT / "RAW_UWB_TAG_TRAJECTORIES.npz"
INVENTORY_PATH = UWB_ROOT / "C123_UWB_READONLY_INVENTORY.json"
IDENTITY_PATH = UWB_ROOT / "C123_UWB_IDENTITY_AND_TAG_MAP.json"
M1_MANIFEST_PATH = M1_ROOT / "REPRODUCIBILITY_MANIFEST.json"
LAYOUT_PATH = REPOSITORY / "B306_Part/deployments/current_room_autopos_20260811_183541/V4IO_LAYOUT.json"

CAPTURE_IDS = (1, 2, 3)
FULL_RUNTIME_LIMIT_S = 60.0 * 60.0
HARD_RUNTIME_LIMIT_S = 90.0 * 60.0
STRIDE = 40
PROFILE_EVALUATION_BUDGET = 430
ROLLING_BIN_COUNT = 60


@dataclass
class PreparedCapture:
    capture_id: int
    session_uuid: str
    ledger_start_s: float
    ledger_end_s: float
    layout_status: str
    data: C1Data
    event_groups: list[np.ndarray]
    event_mask: np.ndarray
    raw_groups: RawGroups
    padded_event_ids: np.ndarray
    sampling_stride: int
    available_epoch_count: int
    selected_epoch_count: int


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(16 << 20):
            digest.update(block)
    return digest.hexdigest()


def canonical_r4_manifest_hash() -> tuple[str, str]:
    value = json.loads((R4_ROOT / "REPRODUCIBILITY_MANIFEST.json").read_text())
    recorded = value.pop("manifest_payload_sha256")
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return recorded, hashlib.sha256(payload).hexdigest()


def load_capture(capture_id: int) -> tuple[C1Data, dict]:
    """Load one frozen capture through the same lineage contract as Root-R4."""

    m1_path = M1_ROOT / f"CAPTURE{capture_id}_MVP_REPLAY_DATA.npz"
    with np.load(m1_path, allow_pickle=False) as archive:
        needed = ("time_s", "node_ids", "segment_names", "joint_names", "joint_positions_m", "joint_available")
        m1 = {name: archive[name] for name in needed}
    nodes = tuple(str(value) for value in m1["node_ids"])
    segments = tuple(str(value) for value in m1["segment_names"])
    prefix = f"c{capture_id}_"
    with np.load(SCHEDULE_PATH, allow_pickle=False) as schedule:
        node_index = schedule[prefix + "node_index"].astype(np.int16)
        source_index = schedule[prefix + "source_index"].astype(np.int32)
        epoch = schedule[prefix + "epoch"].astype(np.int64)
        sweep = schedule[prefix + "sweep"].astype(np.uint32)
        measurement = schedule[prefix + "measurement_s"].astype(float)
        availability = schedule[prefix + "available_s"].astype(float)
        strobe = schedule[prefix + "strobe_s"].astype(float)
        xyz = schedule[prefix + "xyz_m"].astype(float)
        used_mask = schedule[prefix + "used_mask"].astype(np.uint8)
    count = len(node_index)
    shape8 = (count, 8)
    raw_record = np.empty(count, np.uint64)
    raw_start = np.empty(count, np.uint64)
    raw_end = np.empty(count, np.uint64)
    packet = np.empty(count, np.uint32)
    raw_sweep = np.empty(count, np.uint32)
    anchor_id = np.empty(shape8, np.uint8)
    ranges = np.empty(shape8, float)
    quality = np.empty(shape8, np.uint8)
    tround = np.empty(shape8, np.uint16)
    cfo = np.empty(shape8, float)
    valid_mask = np.empty(count, np.uint8)
    solved_xyz = np.empty((count, 3), float)
    solved_used = np.empty(count, np.uint8)
    solved_cov = np.empty((count, 3), float)
    solved_residual = np.empty(shape8, float)
    with np.load(RAW_PATH, allow_pickle=False) as raw:
        for node_number, node in enumerate(nodes):
            rows = np.flatnonzero(node_index == node_number)
            source = source_index[rows]
            raw_prefix = f"c{capture_id}_{node}_"
            raw_record[rows] = raw[raw_prefix + "raw_record_index"][source]
            raw_start[rows] = raw[raw_prefix + "raw_start"][source]
            raw_end[rows] = raw[raw_prefix + "raw_end"][source]
            packet[rows] = raw[raw_prefix + "packet_sequence"][source]
            raw_sweep[rows] = raw[raw_prefix + "sweep"][source]
            anchor_id[rows] = raw[raw_prefix + "anchor_id"][source]
            ranges[rows] = raw[raw_prefix + "range_mm"][source].astype(float) / 1000.0
            quality[rows] = raw[raw_prefix + "quality"][source]
            tround[rows] = raw[raw_prefix + "t_round_us"][source]
            cfo[rows] = raw[raw_prefix + "cfo_ppm_q8"][source].astype(float) / 256.0
            valid_mask[rows] = raw[raw_prefix + "valid_mask"][source]
            solved_xyz[rows] = raw[raw_prefix + "solved_xyz_m"][source]
            solved_used[rows] = raw[raw_prefix + "solved_used_mask"][source]
            solved_cov[rows] = raw[raw_prefix + "solved_covariance_diag_m2"][source]
            solved_residual[rows] = raw[raw_prefix + "solved_residuals_m"][source]
    exact_checks = {
        "sweep_exact": bool(np.array_equal(sweep, raw_sweep)),
        "t4_xyz_bit_exact": bool(np.array_equal(xyz.astype(np.float32), solved_xyz.astype(np.float32))),
        "t4_used_mask_exact": bool(np.array_equal(used_mask, solved_used)),
        "anchor_slots_exact_0_to_7": bool(np.all(anchor_id == np.arange(8, dtype=np.uint8))),
        "measurement_not_after_availability": bool(np.all(measurement <= availability + 1e-12)),
        "unique_node_source_pairs": len(set(zip(node_index.tolist(), source_index.tolist()))) == count,
    }
    if not all(exact_checks.values()):
        raise RuntimeError(f"capture {capture_id} lineage closure failed: {exact_checks}")
    raw_valid = (((valid_mask[:, None] >> np.arange(8)) & 1).astype(bool) &
                 (ranges > 0.0) & (ranges < 65.535))
    epoch_audit = json.loads(EPOCH_AUDIT_PATH.read_text())
    models = epoch_audit["captures"][str(capture_id)]["mapping_models"]
    slopes = np.asarray([float(models[node]["slope_common_s_per_local_s"]) for node in nodes])
    raw_measurement = strobe[:, None] + slopes[node_index, None] * tround.astype(float) * 0.5e-6
    raw_availability = np.broadcast_to(availability[:, None], shape8).copy()
    relative, _, left, right, interpolated = m1_relative_points(m1, node_index, measurement)
    raw_relative_flat, _, _, _, _ = m1_relative_points(
        m1, np.repeat(node_index, 8), raw_measurement.reshape(-1))
    raw_relative = raw_relative_flat.reshape(count, 8, 3)
    layout = json.loads(LAYOUT_PATH.read_text())
    anchors = np.asarray([[row["x_mm"], row["y_mm"], row["z_mm"]] for row in layout["anchors"]], float) / 1000.0
    delays = np.asarray([row.get("d_anchor_mm", 0.0) for row in layout["anchors"]], float) / 1000.0
    data = C1Data(nodes, segments, node_index, source_index, epoch, sweep, measurement, availability, strobe,
                  xyz, used_mask, solved_cov, solved_residual, relative, raw_relative, left, right, interpolated,
                  raw_record, raw_start, raw_end, packet, anchor_id, ranges, raw_valid, raw_measurement,
                  raw_availability, quality, tround, cfo, anchors, delays, slopes, {})
    return data, exact_checks


def grouped_event_ids(data: C1Data, stride: int) -> tuple[list[np.ndarray], np.ndarray, int]:
    geometry = np.all(np.isfinite(data.root_relative_n_m), axis=1) & np.all(np.isfinite(data.t4_xyz_m), axis=1)
    ids = np.flatnonzero(geometry)
    order = ids[np.argsort(data.epoch[ids], kind="stable")]
    ordered_epoch = data.epoch[order]
    starts = np.r_[0, np.flatnonzero(np.diff(ordered_epoch)) + 1]
    stops = np.r_[starts[1:], len(order)]
    available = [order[start:stop] for start, stop in zip(starts, stops) if stop - start >= 4]
    selected = available[::stride]
    mask = np.zeros(data.event_count, bool)
    if selected:
        mask[np.concatenate(selected)] = True
    return selected, mask, len(available)


def make_raw_groups(data: C1Data, groups: list[np.ndarray]) -> tuple[RawGroups, np.ndarray]:
    width = max(len(group) for group in groups)
    count = len(groups)
    event_relative = np.zeros((count, width, 3))
    link_relative = np.zeros((count, width, 8, 3))
    t4 = np.zeros((count, width, 3))
    ranges = np.zeros((count, width, 8))
    valid = np.zeros((count, width, 8), bool)
    padded_ids = np.full((count, width), -1, np.int64)
    tags: set[int] = set()
    anchors: set[int] = set()
    events = 0
    for index, group in enumerate(groups):
        size = len(group)
        events += size
        padded_ids[index, :size] = group
        tags.update(data.node_index[group].tolist())
        event_relative[index, :size] = data.root_relative_n_m[group]
        raw_link_relative = data.raw_root_relative_n_m[group]
        t4[index, :size] = data.t4_xyz_m[group]
        ranges[index, :size] = data.raw_range_m[group]
        finite = np.all(np.isfinite(raw_link_relative), axis=2)
        # Invalid edge links are excluded by ``valid`` below.  Store finite
        # padding so NaN*False cannot contaminate the batched Hessian.
        link_relative[index, :size] = np.where(finite[..., None], raw_link_relative, 0.0)
        valid[index, :size] = data.raw_valid[group] & finite
        anchors.update(data.anchor_id[group][valid[index, :size]].tolist())
    raw = RawGroups(event_relative, link_relative, t4, ranges, valid, data.anchors_v4_m,
                    data.anchor_delay_m, count, events, int(np.sum(valid)), len(tags), len(anchors))
    return raw, padded_ids


def prepare_capture(capture_id: int, stride: int, inventory: dict) -> tuple[PreparedCapture, dict]:
    data, checks = load_capture(capture_id)
    groups, mask, available_count = grouped_event_ids(data, stride)
    raw, padded = make_raw_groups(data, groups)
    row = inventory["captures"][str(capture_id)]
    return PreparedCapture(capture_id, row["session_uuid"], float(row["first_uwb_time"]),
                           float(row["last_uwb_time"]), row["layout_status"], data, groups,
                           mask, raw, padded, stride, available_count, len(groups)), checks


def raw_residual_timeline(prepared: PreparedCapture, yaw_rad: float) -> tuple[np.ndarray, np.ndarray]:
    groups = prepared.raw_groups
    rotation = _rotation(yaw_rad)
    event_relative = groups.event_relative_n @ rotation.T
    link_relative = groups.link_relative_n @ rotation.T
    padded = np.any(groups.valid, axis=2)
    candidates = np.where(padded[..., None], groups.t4_v4 - event_relative, np.nan)
    root = np.nanmedian(candidates, axis=1)
    for _ in range(5):
        vectors = root[:, None, None, :] + link_relative - groups.anchors_v4[None, None, :, :]
        distance = np.linalg.norm(vectors, axis=3)
        error = distance + groups.delays_m[None, None, :] - groups.ranges_m
        jacobian = vectors / np.maximum(distance[..., None], 1e-12)
        weight = np.minimum(1.0, 0.20 / np.maximum(np.abs(error), 1e-12)) * groups.valid
        hessian = np.einsum("gtai,gta,gtaj->gij", jacobian, weight, jacobian) + np.eye(3)[None] * 1e-7
        gradient = np.einsum("gtai,gta->gi", jacobian, weight * error)
        root -= np.linalg.solve(hessian, gradient[..., None])[..., 0]
    vectors = root[:, None, None, :] + link_relative - groups.anchors_v4[None, None, :, :]
    error = np.linalg.norm(vectors, axis=3) + groups.delays_m[None, None, :] - groups.ranges_m
    event_ids = np.maximum(prepared.padded_event_ids, 0)
    times = prepared.data.raw_measurement_s[event_ids]
    return times[groups.valid], error[groups.valid]


def t4_residual_timeline(prepared: PreparedCapture, yaw_rad: float) -> tuple[np.ndarray, np.ndarray]:
    rotation = _rotation(yaw_rad)
    times: list[np.ndarray] = []
    residuals: list[np.ndarray] = []
    for group in prepared.event_groups:
        x = prepared.data.root_relative_n_m[group]
        y = prepared.data.t4_xyz_m[group]
        x = x - np.mean(x, axis=0)
        y = y - np.mean(y, axis=0)
        residuals.append(np.linalg.norm(y - x @ rotation.T, axis=1))
        times.append(prepared.data.measurement_s[group])
    return np.concatenate(times), np.concatenate(residuals)


def rolling_summary(times: np.ndarray, residuals: np.ndarray, start: float, stop: float) -> dict:
    edges = np.linspace(start, stop, ROLLING_BIN_COUNT + 1)
    bins = np.clip(np.digitize(times, edges[1:-1], right=False), 0, ROLLING_BIN_COUNT - 1)
    rows = []
    absolute = np.abs(residuals)
    for index in range(ROLLING_BIN_COUNT):
        selected = absolute[bins == index]
        if not len(selected):
            continue
        rows.append({"start_s": float(edges[index]), "stop_s": float(edges[index + 1]),
                     "centre_s": float(0.5 * (edges[index] + edges[index + 1])), "count": int(len(selected)),
                     "median_abs_m": float(np.median(selected)), "p95_abs_m": float(np.quantile(selected, 0.95))})
    medians = np.asarray([row["median_abs_m"] for row in rows])
    p10, p90 = np.quantile(medians, [0.10, 0.90])
    global_median = float(np.median(absolute))
    return {"bin_count": len(rows), "bin_width_s": float((stop - start) / ROLLING_BIN_COUNT), "rows": rows,
            "global_median_abs_m": global_median, "global_p95_abs_m": float(np.quantile(absolute, 0.95)),
            "rolling_median_p10_m": float(p10), "rolling_median_p90_m": float(p90),
            "rolling_median_p90_p10_ratio": float(p90 / max(p10, 1e-12)),
            "rolling_median_p90_minus_p10_m": float(p90 - p10)}


def profile_is_informative(profile: dict) -> bool:
    return (profile["asymptotic_profile_interval_width_95_deg"] < 45.0 and
            len(profile["material_modes"]) == 1 and profile["profile_grid_converged"])


def classify_capture(raw: dict, t4: dict, raw_time: dict, t4_time: dict) -> tuple[str, dict]:
    difference = abs(wrap_degrees(raw["global_mode_deg"] - t4["global_mode_deg"]))
    mismatch_limit = max(10.0, 0.5 * (raw["asymptotic_profile_interval_width_95_deg"] +
                                      t4["asymptotic_profile_interval_width_95_deg"]))
    informative = profile_is_informative(raw) and profile_is_informative(t4)
    structured = any(summary["rolling_median_p90_minus_p10_m"] >= 0.05 and
                     summary["rolling_median_p90_minus_p10_m"] /
                     max(summary["global_median_abs_m"], 1e-12) >= 0.20
                     for summary in (raw_time, t4_time))
    if not informative:
        label = "FULL_CAPTURE_YAW_INFORMATION_WEAK"
    elif difference > mismatch_limit:
        label = "FULL_CAPTURE_REPRESENTATION_MISMATCH"
    elif structured:
        label = "FULL_CAPTURE_STRUCTURED_TIME_RESIDUALS"
    else:
        label = "FULL_CAPTURE_RAW_T4_INTERNALLY_CONSISTENT"
    return label, {"raw_t4_difference_abs_deg": difference, "representation_mismatch_limit_deg": mismatch_limit,
                   "both_profiles_informative_unimodal": informative, "structured_time_residuals": structured,
                   "structured_rule": "rolling median p90-p10 >=0.05 m and >=20% of global median in either representation"}


def profile_plot(results: list[dict], output: Path) -> None:
    fig, axes = plt.subplots(3, 1, figsize=(11, 11), sharex=True)
    for ax, result in zip(axes, results):
        for layer, color in (("raw", "#1565c0"), ("t4", "#d84315")):
            profile = result["profiles"][layer]
            x = np.mod(np.asarray(profile["grid_yaw_deg"]), 360.0)
            y = np.asarray(profile["normalized_delta_objective"])
            order = np.argsort(x)
            ax.plot(x[order], y[order], color=color, label=layer.upper(), linewidth=1.35)
            ax.axvline(profile["global_mode_deg"] % 360.0, color=color, alpha=0.45, linestyle="--")
        ax.set_title(f"Capture {result['capture_id']} — one yaw over the complete selected interval")
        ax.set_ylabel("normalized Δ objective")
        ax.grid(alpha=0.25)
        ax.legend()
    axes[-1].set_xlabel("yaw of R_V4_from_N (degrees, 0–360)")
    axes[-1].set_xlim(0, 360)
    fig.suptitle("Whole-capture matched-support circular yaw profiles")
    fig.tight_layout()
    fig.savefig(output, dpi=145, bbox_inches="tight", metadata={"Software": "biospur-whole-capture-yaw"})
    plt.close(fig)


def residual_plot(results: list[dict], output: Path) -> None:
    fig, axes = plt.subplots(3, 1, figsize=(12, 11), sharex=False)
    for ax, result in zip(axes, results):
        for layer, color in (("raw", "#1565c0"), ("t4", "#d84315")):
            rows = result["residual_timeline"][layer]["rows"]
            x = [row["centre_s"] for row in rows]
            median = [row["median_abs_m"] for row in rows]
            p95 = [row["p95_abs_m"] for row in rows]
            ax.plot(x, median, color=color, linewidth=1.5, label=f"{layer.upper()} median |residual|")
            ax.plot(x, p95, color=color, linewidth=0.9, linestyle=":", alpha=0.7, label=f"{layer.upper()} p95")
        ax.set_title(f"Capture {result['capture_id']} — fixed full-capture yaw, no rolling refit")
        ax.set_ylabel("residual (m)")
        ax.grid(alpha=0.25)
        ax.legend(ncol=2, fontsize=8)
    axes[-1].set_xlabel("common time (s)")
    fig.suptitle("Residual diagnostics versus common time")
    fig.tight_layout()
    fig.savefig(output, dpi=145, bbox_inches="tight", metadata={"Software": "biospur-whole-capture-yaw"})
    plt.close(fig)


def compact_profile(profile: dict) -> dict:
    return profile


def report_markdown(payload: dict) -> str:
    sampling = payload["computation"]["sampling"]
    lines = ["# Whole-capture yaw analysis", "", f"Overall label: `{payload['overall_label']}`", "",
             "Each capture was fitted as one continuous interval. Raw ranges and T4 were separate matched-support solves; no block, action, cycle, rolling-yaw, or dynamic-drift fit was performed.", "",
             "## Capture provenance and support", "",
             "| Capture | Frozen session ID | Common-time coverage (s) | Duration (s) | Global epochs | Source/T4 events | Valid raw ranges | Tags / anchors | Coordinate-layout status |", "|---|---|---:|---:|---:|---:|---:|---:|---|"]
    for row in payload["captures"]:
        support = row["support"]
        lines.append(f"| C{row['capture_id']} | `{row['session_uuid']}` | {row['ledger_common_time_start_s']:.6f} to {row['ledger_common_time_end_s']:.6f} | {row['ledger_duration_s']:.6f} | {support['global_epochs']} | {support['source_events']} / {support['t4_events']} | {support['valid_raw_ranges']} | {len(support['active_tags'])} / {len(support['active_anchors'])} | `{row['layout_status']}` |")
    lines += ["", f"Sampling: **{sampling}**. The pre-run estimate was {payload['computation']['estimated_total_runtime_s']:.1f} s; actual analysis runtime was {payload['computation']['actual_analysis_runtime_s']:.1f} s.", "",
              "## Whole-capture results", "", "| Capture | Primary label | Raw yaw / width | T4 yaw / width | Circular disagreement | Modes and information | Residual timeline |", "|---|---|---:|---:|---:|---|---|"]
    for row in payload["captures"]:
        raw = row["profiles"]["raw"]
        t4 = row["profiles"]["t4"]
        diag = row["interpretation"]
        mode_text = (f"raw {len(raw['material_modes'])} mode, I={raw['nuisance_eliminated_yaw_information']:.1f}; "
                     f"T4 {len(t4['material_modes'])} mode, I={t4['nuisance_eliminated_yaw_information']:.1f}")
        residual_text = "structured" if diag["structured_time_residuals"] else "stable under the stated rolling-summary rule"
        lines.append(f"| C{row['capture_id']} | `{row['primary_label']}` | {raw['global_mode_deg']:.3f}° / {raw['asymptotic_profile_interval_width_95_deg']:.3f}° | {t4['global_mode_deg']:.3f}° / {t4['asymptotic_profile_interval_width_95_deg']:.3f}° | {diag['raw_t4_difference_abs_deg']:.3f}° | {mode_text} | {residual_text} |")
    lines += ["", "A narrow profile records internal local curvature only; it does not override a raw/T4 disagreement or a structured residual timeline.", "",
              "## Cross-capture comparability", "", "Cross-capture yaw comparison is **not authorized**. The frozen identity ledger proves the same ten device-to-segment identities, but each capture uses a session-local IMU calibration. C1 alone has a capture-bound UWB coordinate layout; C2 verifies the anchor UUID set without capture-bound coordinates, and C3 has only raw anchor-slot identities without capture-bound coordinates. No provenance proves unchanged donning across the three sessions.", "",
              "## Safety and scope", "", f"Root-R4 manifest `{payload['root_r4']['manifest_recomputed_after']}` remained exact and unchanged. No sealed custom-action data, external truth, Vicon, real fusion, frame authorization, estimator change, commit, push, or merge was involved.", ""]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    if output.exists() and any(output.iterdir()):
        raise FileExistsError(output)
    output.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    recorded_before, recomputed_before = canonical_r4_manifest_hash()
    if recorded_before != recomputed_before or recomputed_before != R4_MANIFEST_EXPECTED:
        raise RuntimeError("Root-R4 manifest mismatch")
    inventory = json.loads(INVENTORY_PATH.read_text())
    identity = json.loads(IDENTITY_PATH.read_text())
    m1_manifest = json.loads(M1_MANIFEST_PATH.read_text())
    if tuple(sorted(map(int, inventory["captures"]))) != CAPTURE_IDS:
        raise RuntimeError("frozen ledger does not contain exactly captures 1, 2, and 3")
    if not identity["same_mapping_observed_all_captures"]:
        raise RuntimeError("device-to-segment identity mapping differs")

    preparation_started = time.perf_counter()
    prepared: list[PreparedCapture] = []
    lineage_checks: dict[str, dict] = {}
    benchmark_rows = []
    for capture_id in CAPTURE_IDS:
        print(f"PREPARE_CAPTURE_{capture_id}", flush=True)
        item, checks = prepare_capture(capture_id, 1, inventory)
        objective, _, _ = raw_evaluator(item.raw_groups)
        benchmark_started = time.perf_counter()
        for yaw in np.deg2rad((-120.0, 0.0, 120.0)):
            objective(float(yaw))
        seconds_per_eval = (time.perf_counter() - benchmark_started) / 3.0
        benchmark_rows.append({"capture_id": capture_id, "seconds_per_raw_yaw_evaluation": seconds_per_eval,
                               "full_available_epochs": item.available_epoch_count})
        print(f"BENCHMARK_CAPTURE_{capture_id} seconds_per_yaw={seconds_per_eval:.6f}", flush=True)
        prepared.append(item)
        lineage_checks[str(capture_id)] = checks
    preparation_elapsed = time.perf_counter() - preparation_started
    estimated_total = preparation_elapsed + sum(row["seconds_per_raw_yaw_evaluation"] * PROFILE_EVALUATION_BUDGET
                                                 for row in benchmark_rows)
    if estimated_total > FULL_RUNTIME_LIMIT_S:
        prepared.clear()
        gc.collect()
        prepared = [prepare_capture(capture_id, STRIDE, inventory)[0] for capture_id in CAPTURE_IDS]
        sampling = "UNIFORM_GLOBAL_EPOCH_STRIDE_40"
        sampling_stride = STRIDE
    else:
        sampling = "FULL_RESOLUTION_ALL_AVAILABLE_GLOBAL_EPOCHS"
        sampling_stride = 1
    print(f"SAMPLING_DECISION {sampling} estimate_s={estimated_total:.3f}", flush=True)

    analysis_started = time.perf_counter()
    results = []
    for item in prepared:
        print(f"PROFILE_CAPTURE_{item.capture_id}", flush=True)
        if time.perf_counter() - started > HARD_RUNTIME_LIMIT_S:
            raise TimeoutError("90-minute hard stop reached")
        t4_profile = circular_profile("T4", f"CAPTURE_{item.capture_id}_WHOLE", *t4_evaluator(item.data, item.event_mask))
        raw_profile = circular_profile("RAW", f"CAPTURE_{item.capture_id}_WHOLE", *raw_evaluator(item.raw_groups))
        raw_time, raw_residual = raw_residual_timeline(item, math.radians(raw_profile["global_mode_deg"]))
        t4_time, t4_residual = t4_residual_timeline(item, math.radians(t4_profile["global_mode_deg"]))
        raw_summary = rolling_summary(raw_time, raw_residual, item.ledger_start_s, item.ledger_end_s)
        t4_summary = rolling_summary(t4_time, t4_residual, item.ledger_start_s, item.ledger_end_s)
        label, interpretation = classify_capture(raw_profile, t4_profile, raw_summary, t4_summary)
        ids = np.flatnonzero(item.event_mask)
        active_tags = [item.data.nodes[index] for index in sorted(np.unique(item.data.node_index[ids]).tolist())]
        finite_raw = item.data.raw_valid[ids] & np.all(np.isfinite(item.data.raw_root_relative_n_m[ids]), axis=2)
        active_anchors = sorted(np.unique(item.data.anchor_id[ids][finite_raw]).astype(int).tolist())
        result = {"capture_id": item.capture_id, "session_uuid": item.session_uuid,
                  "ledger_common_time_start_s": item.ledger_start_s, "ledger_common_time_end_s": item.ledger_end_s,
                  "ledger_duration_s": item.ledger_end_s - item.ledger_start_s,
                  "analysis_support_start_s": float(np.min(item.data.measurement_s[ids])),
                  "analysis_support_end_s": float(np.max(item.data.measurement_s[ids])),
                  "layout_status": item.layout_status, "primary_label": label, "interpretation": interpretation,
                  "support": {"global_epochs": item.selected_epoch_count, "full_available_global_epochs": item.available_epoch_count,
                              "source_events": int(len(ids)), "t4_events": int(t4_profile["support"]["events"]),
                              "valid_raw_ranges": int(raw_profile["support"]["samples"]),
                              "active_tags": active_tags, "active_anchors": active_anchors,
                              "sampling_stride_global_epochs": item.sampling_stride,
                              "same_physical_event_support_raw_and_t4": True},
                  "profiles": {"raw": compact_profile(raw_profile), "t4": compact_profile(t4_profile)},
                  "residual_timeline": {"raw": raw_summary, "t4": t4_summary}}
        results.append(result)
    actual_analysis_runtime = time.perf_counter() - analysis_started
    recorded_after, recomputed_after = canonical_r4_manifest_hash()
    if (recorded_after, recomputed_after) != (recorded_before, recomputed_before):
        raise RuntimeError("Root-R4 manifest changed during analysis")

    payload = {"schema": "biospur.whole_capture_yaw_analysis.v1",
               "overall_label": "THREE_CAPTURE_COMPARISON_NOT_AUTHORIZED",
               "captures": results,
               "cross_capture_comparison": {"authorized": False,
                    "same_device_segment_mapping": True,
                    "same_donning_proven": False,
                    "same_calibration": False,
                    "calibration_contract": "session-local calibration objects from CAPTURE{1,2,3}_REPLAY_RESULT",
                    "same_world_frame_proven": False,
                    "reason": "C1 has a capture-bound layout; C2 and C3 do not, and unchanged donning is not proven."},
               "computation": {"sampling": sampling, "sampling_stride": sampling_stride,
                    "full_resolution_limit_s": FULL_RUNTIME_LIMIT_S, "hard_stop_s": HARD_RUNTIME_LIMIT_S,
                    "estimated_total_runtime_s": estimated_total, "preparation_and_benchmark_runtime_s": preparation_elapsed,
                    "benchmark": benchmark_rows, "profile_evaluation_budget_per_capture": PROFILE_EVALUATION_BUDGET,
                    "actual_analysis_runtime_s": actual_analysis_runtime,
                    "no_partition_or_partial_capture_fit": True, "one_yaw_per_capture_per_representation": True},
               "provenance": {"capture_ledger": str(INVENTORY_PATH), "identity_ledger": str(IDENTITY_PATH),
                    "event_schedule": str(SCHEDULE_PATH), "raw_trajectories": str(RAW_PATH),
                    "m1_manifest": str(M1_MANIFEST_PATH), "lineage_checks": lineage_checks,
                    "m1_capture_hashes": {str(capture_id): m1_manifest["artifacts"][f"CAPTURE{capture_id}_MVP_REPLAY_DATA.npz"]["sha256"] for capture_id in CAPTURE_IDS},
                    "raw_capture_hashes": {str(capture_id): inventory["captures"][str(capture_id)]["raw_sha256"] for capture_id in CAPTURE_IDS}},
               "root_r4": {"expected_manifest": R4_MANIFEST_EXPECTED, "manifest_recorded_before": recorded_before,
                    "manifest_recomputed_before": recomputed_before, "manifest_recorded_after": recorded_after,
                    "manifest_recomputed_after": recomputed_after, "unchanged": True},
               "scope": {"sealed_custom_actions_opened": False, "external_truth_opened": False, "vicon_opened": False,
                    "real_fusion_executed": False, "dynamic_drift_fit": False, "block_or_action_fit": False,
                    "tag_or_anchor_loo": False, "commit": False, "push": False, "merge": False,
                    "raw_and_t4_combined_as_independent_factors": False}}
    json_path = output / "WHOLE_CAPTURE_YAW_ANALYSIS.json"
    json_path.write_text(json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n")
    profile_plot(results, output / "WHOLE_CAPTURE_YAW_PROFILES.png")
    residual_plot(results, output / "WHOLE_CAPTURE_RESIDUALS_VS_TIME.png")
    (output / "WHOLE_CAPTURE_YAW_ANALYSIS.md").write_text(report_markdown(payload))
    expected_files = {"WHOLE_CAPTURE_YAW_ANALYSIS.md", "WHOLE_CAPTURE_YAW_ANALYSIS.json",
                      "WHOLE_CAPTURE_YAW_PROFILES.png", "WHOLE_CAPTURE_RESIDUALS_VS_TIME.png"}
    actual_files = {path.name for path in output.iterdir() if path.is_file()}
    if actual_files != expected_files:
        raise RuntimeError(f"unexpected deliverables: {actual_files}")
    print(json.dumps({"output": str(output), "sampling": sampling, "estimated_total_runtime_s": estimated_total,
                      "actual_analysis_runtime_s": actual_analysis_runtime,
                      "labels": {str(row['capture_id']): row['primary_label'] for row in results}}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
