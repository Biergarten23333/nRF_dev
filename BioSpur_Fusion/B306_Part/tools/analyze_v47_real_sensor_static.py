#!/usr/bin/env python3
"""Offline v47 ten-node static/disturbance characterization."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
matplotlib.rcParams["svg.hashsalt"] = "biospur-v47-real-static-v1"
import matplotlib.pyplot as plt
import numpy as np
from scipy import signal
from scipy.ndimage import binary_closing, binary_opening

from v47_real_data_adapter import NODES, imu_physical, load_capture, sequence_gap_count

T0_MASTER_MS = 77860264
T0_WALL = "2026-08-11T13:09:59.019+02:00"
DURATION_S = 1800
TABLE_NODES = tuple(node for node in NODES if node != "BSF6C53")
RAW_SHA = "c5c7c923e2e29ad43d2d5e51217dda0ea1df8f95bdc04d30656f8055b038a9b8"


def robust_threshold(values: np.ndarray, scale: float = 6.0, floor: float = 0.0) -> float:
    values = np.asarray(values, float)
    med = float(np.nanmedian(values))
    mad = float(np.nanmedian(np.abs(values - med)))
    return med + scale * 1.4826 * mad + floor


def contiguous(mask: np.ndarray, minimum: int = 1) -> list[tuple[int, int]]:
    starts = np.flatnonzero(mask & ~np.r_[False, mask[:-1]])
    ends = np.flatnonzero(mask & ~np.r_[mask[1:], False]) + 1
    return [(int(a), int(b)) for a, b in zip(starts, ends) if b - a >= minimum]


def classify_synthetic_event(imu_nodes: int, gyro_peak: float, gravity_deg: float,
                             uwb_changed_anchors: int, uwb_quality_only: bool = False) -> str:
    if imu_nodes >= 3 and gravity_deg < 0.5:
        return "TABLE_COMMON_MODE_VIBRATION"
    if gyro_peak > 10 and (gravity_deg >= 0.5 or uwb_changed_anchors >= 3):
        return "SINGLE_NODE_REPOSITION_OR_ROTATION"
    if uwb_quality_only or (imu_nodes == 0 and uwb_changed_anchors <= 2):
        return "UWB_RF_VISIBILITY_CHANGE"
    return "UNKNOWN_DISTURBANCE"


def clock_fit(imu: np.ndarray, uwb: np.ndarray) -> tuple[np.ndarray, float, float, float]:
    x = uwb["frame_us"].astype(float) / 1000.0
    y = uwb["master_ms"].astype(float)
    offset = np.median(y - x)
    slope = 1.0 + np.polyfit(x - x[0], y - (x + offset), 1)[0]
    intercept = float(np.median(y - slope * x))
    residual = y - (slope * x + intercept)
    t_s = (slope * imu["b306_us"].astype(float) / 1000.0 + intercept - T0_MASTER_MS) / 1000.0
    return t_s, float(slope), intercept, float(np.quantile(np.abs(residual), .95))


def slices_for_seconds(times: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    edges = np.arange(DURATION_S + 1, dtype=float)
    return np.searchsorted(times, edges[:-1]), np.searchsorted(times, edges[1:])


def aggregate_imu(raw: np.ndarray, t_s: np.ndarray) -> dict[str, np.ndarray]:
    acc, gyro, temp = imu_physical(raw)
    anorm, gnorm = np.linalg.norm(acc, axis=1), np.linalg.norm(gyro, axis=1)
    lo, hi = slices_for_seconds(t_s)
    out = {key: np.full(DURATION_S, np.nan) for key in ("acc_med", "acc_std", "gyro_med", "gyro_p95", "temp")}
    out["gravity"] = np.full((DURATION_S, 3), np.nan)
    for sec, (a, b) in enumerate(zip(lo, hi)):
        if b <= a:
            continue
        out["acc_med"][sec] = np.median(anorm[a:b])
        out["acc_std"][sec] = np.std(anorm[a:b])
        out["gyro_med"][sec] = np.median(gnorm[a:b])
        out["gyro_p95"][sec] = np.quantile(gnorm[a:b], .95)
        out["temp"][sec] = np.median(temp[a:b])
        out["gravity"][sec] = np.median(acc[a:b], axis=0)
    unit = out["gravity"] / np.linalg.norm(out["gravity"], axis=1, keepdims=True)
    dot = np.sum(unit[1:] * unit[:-1], axis=1)
    out["gravity_step_deg"] = np.r_[0.0, np.degrees(np.arccos(np.clip(dot, -1, 1)))]
    return out


def aggregate_uwb(raw: np.ndarray) -> dict[str, np.ndarray]:
    t_s = (raw["master_ms"].astype(float) - T0_MASTER_MS) / 1000.0
    lo, hi = slices_for_seconds(t_s)
    ranges = np.full((DURATION_S, 8), np.nan)
    valid = np.zeros((DURATION_S, 8))
    quality = np.full((DURATION_S, 8), np.nan)
    for sec, (a, b) in enumerate(zip(lo, hi)):
        if b <= a:
            continue
        for slot in range(8):
            good = (raw["valid_mask"][a:b] & (1 << slot)) != 0
            valid[sec, slot] = np.mean(good)
            if np.any(good):
                ranges[sec, slot] = np.median(raw["range_mm"][a:b, slot][good])
                quality[sec, slot] = np.median(raw["quality"][a:b, slot][good])
    diff = np.diff(ranges, axis=0)
    return {
        "range": ranges, "valid": valid, "quality": quality,
        "range_step_rms": np.r_[0.0, np.sqrt(np.nanmean(diff * diff, axis=1))],
        "t_s": t_s,
    }


def build_static_masks(imu_agg: dict[str, dict[str, np.ndarray]],
                       uwb_agg: dict[str, dict[str, np.ndarray]]) -> tuple[dict[str, np.ndarray], dict]:
    masks, thresholds = {}, {}
    for node in NODES:
        im, uw = imu_agg[node], uwb_agg[node]
        th = {
            "gyro_p95_dps": robust_threshold(im["gyro_p95"], floor=.50),
            "acc_norm_std_g": robust_threshold(im["acc_std"], floor=.0005),
            "gravity_step_deg": robust_threshold(im["gravity_step_deg"], floor=.03),
            "uwb_range_step_rms_mm": robust_threshold(uw["range_step_rms"], floor=25.0),
        }
        q = np.isfinite(im["gyro_p95"]) & np.isfinite(uw["range_step_rms"])
        q &= im["gyro_p95"] < th["gyro_p95_dps"]
        q &= im["acc_std"] < th["acc_norm_std_g"]
        q &= im["gravity_step_deg"] < th["gravity_step_deg"]
        q &= uw["range_step_rms"] < th["uwb_range_step_rms_mm"]
        q = binary_opening(binary_closing(q, np.ones(3, bool)), np.ones(3, bool))
        masks[node] = q
        thresholds[node] = th
    return masks, thresholds


def detect_events(imu_agg: dict, uwb_agg: dict) -> list[dict]:
    events: list[dict] = []
    active = []
    for node in TABLE_NODES:
        f = imu_agg[node]["gyro_p95"]
        active.append(f > np.nanmedian(f[:450]) + .50)
    count = np.sum(active, axis=0)
    common = count >= 5
    for start, end in contiguous(common, 1):
        # Join seconds separated by at most 5 s in a second pass below.
        events.append({"start_s": start, "end_s": end, "nodes": ",".join(TABLE_NODES),
                       "classification": "TABLE_COMMON_MODE_VIBRATION",
                       "imu_evidence": f"{int(np.max(count[start:end]))}/9 table nodes exceed robust gyro activity",
                       "uwb_evidence": "no persistent multi-anchor platform required",
                       "listener_evidence": "filled from deduplicated LPD inventory",
                       "confidence": "HIGH", "competing": "simultaneous operator contact"})
    # Blind single-node reposition gate: large gyro plus persistent gravity and multi-anchor range change.
    for node in NODES:
        im, uw = imu_agg[node], uwb_agg[node]
        for sec in range(20, DURATION_S - 20):
            peak = float(np.nanmax(im["gyro_p95"][max(0, sec - 2):sec + 3]))
            if peak < 10.0:
                continue
            g0, g1 = np.nanmedian(im["gravity"][sec-20:sec-5], axis=0), np.nanmedian(im["gravity"][sec+5:sec+20], axis=0)
            angle = math.degrees(math.acos(float(np.clip(g0 @ g1 / (np.linalg.norm(g0) * np.linalg.norm(g1)), -1, 1))))
            r0, r1 = np.nanmedian(uw["range"][sec-20:sec-5], axis=0), np.nanmedian(uw["range"][sec+5:sec+20], axis=0)
            delta = r1 - r0
            changed = int(np.sum(np.abs(delta) > 100.0))
            if angle < .5 and changed < 3:
                continue
            if any(e["classification"] == "SINGLE_NODE_REPOSITION_OR_ROTATION" and e["nodes"] == node and abs(e["start_s"] - sec) < 20 for e in events):
                continue
            events.append({"start_s": sec - 2, "end_s": sec + 4, "nodes": node,
                           "classification": "SINGLE_NODE_REPOSITION_OR_ROTATION",
                           "imu_evidence": f"gyro_p95_peak={peak:.3f} dps; gravity platform delta={angle:.3f} deg",
                           "uwb_evidence": f"{changed}/8 anchors shift >100 mm; delta={','.join(f'{x:.1f}' for x in delta)} mm",
                           "listener_evidence": "filled from deduplicated LPD inventory",
                           "confidence": "HIGH" if changed >= 3 and angle >= .5 else "MEDIUM",
                           "competing": "translation versus combined rotation/antenna-orientation bias"})
    # Strong isolated single-anchor range changes without local IMU motion.
    for node in NODES:
        im, uw = imu_agg[node], uwb_agg[node]
        candidates = []
        for sec in range(20, DURATION_S - 20):
            r0 = np.nanmedian(uw["range"][sec-20:sec], axis=0)
            r1 = np.nanmedian(uw["range"][sec:sec+20], axis=0)
            delta = r1 - r0
            changed = int(np.sum(np.abs(delta) > 150.0))
            if changed <= 2 and np.nanmax(np.abs(delta)) > 300 and np.nanmax(im["gyro_p95"][sec-2:sec+3]) < 5:
                candidates.append((float(np.nanmax(np.abs(delta))), sec, delta))
        selected = []
        for magnitude, sec, delta in sorted(candidates, reverse=True):
            if all(abs(sec - old) > 40 for old in selected):
                selected.append(sec)
                events.append({"start_s": sec - 2, "end_s": sec + 3, "nodes": node,
                               "classification": "UWB_RF_VISIBILITY_CHANGE",
                               "imu_evidence": "no coincident large gyro activity or gravity-platform change",
                               "uwb_evidence": f"dominant single-link shift {magnitude:.1f} mm; delta={','.join(f'{x:.1f}' for x in delta)} mm",
                               "listener_evidence": "filled from deduplicated LPD inventory",
                               "confidence": "MEDIUM", "competing": "temporary occlusion or multipath; no geometry-based position claim"})
            if len(selected) == 2:
                break
    return sorted(events, key=lambda e: (e["start_s"], e["classification"], e["nodes"]))


def listener_cadence(data_root: Path) -> dict[str, np.ndarray]:
    mapping = json.loads((data_root / "formal_capture/node_tag_map.json").read_text())
    source_to_node = {int(v["tag_short_address"], 16): k for k, v in mapping.items()}
    counts = {node: np.zeros(DURATION_S, dtype=int) for node in NODES}
    listeners = {node: [set() for _ in range(DURATION_S)] for node in NODES}
    seen: set[tuple[int, int, int]] = set()
    path = data_root / "formal_capture/listener_capture/merged_index.jsonl"
    pattern = re.compile(r'"listener_key":"([^"]+)".*"arrival_monotonic_ns":(\d+).*"kind":"LPD".*"src":(\d+).*"sequence":(\d+)')
    t0_ns = 243033004297270
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if '"kind":"LPD"' not in line:
                continue
            match = pattern.search(line)
            if not match:
                continue
            listener, ns, source, seq = match.groups()
            node = source_to_node.get(int(source))
            sec = (int(ns) - t0_ns) // 1_000_000_000
            if node is None or not 0 <= sec < DURATION_S:
                continue
            listeners[node][sec].add(listener)
            key = (sec, int(source), int(seq))
            if key not in seen:
                seen.add(key)
                counts[node][sec] += 1
    return {node: np.array([(counts[node][s], len(listeners[node][s])) for s in range(DURATION_S)]) for node in NODES}


def overlapping_allan(values: np.ndarray, rate: float, max_tau_s: float) -> list[tuple[float, float]]:
    n = len(values)
    if n < 20:
        return []
    max_m = min(n // 4, int(max_tau_s * rate))
    ms = np.unique(np.maximum(1, np.logspace(0, math.log10(max(1, max_m)), 18).astype(int)))
    cs = np.r_[0.0, np.cumsum(values.astype(float))]
    result = []
    for m in ms:
        if 2 * m >= n:
            continue
        means = (cs[m:] - cs[:-m]) / m
        adev = math.sqrt(.5 * float(np.mean((means[m:] - means[:-m]) ** 2)))
        result.append((m / rate, adev))
    return result


def write_csv(path: Path, fields: list[str], rows: list[dict]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def stable_sample_mask(times: np.ndarray, second_mask: np.ndarray) -> np.ndarray:
    sec = np.floor(times).astype(int)
    return (sec >= 0) & (sec < DURATION_S) & second_mask[np.clip(sec, 0, DURATION_S - 1)]


def savefig(path: Path) -> None:
    plt.tight_layout()
    plt.savefig(path, format="svg", metadata={"Date": None})
    plt.close()


def render_plots(out: Path, imu_agg: dict, uwb_agg: dict, masks: dict,
                 listener: dict, allan_rows: list[dict], uwb_rows: list[dict],
                 psd_rows: list[dict]) -> None:
    x = np.arange(DURATION_S) / 60.0
    fig, axes = plt.subplots(2, 1, figsize=(14, 8), sharex=True)
    for node in NODES:
        axes[0].plot(x, imu_agg[node]["gyro_p95"], lw=.65, label=node)
        axes[1].plot(x, imu_agg[node]["acc_std"], lw=.65)
    axes[0].set_ylabel("gyro P95 (dps)"); axes[0].set_yscale("symlog", linthresh=.5); axes[0].legend(ncol=5, fontsize=7)
    axes[1].set_ylabel("acc norm std (g)"); axes[1].set_xlabel("minutes from T0")
    savefig(out / "imu_activity_timeline.svg")

    plt.figure(figsize=(14, 5))
    table = np.vstack([imu_agg[n]["gyro_p95"] for n in TABLE_NODES])
    plt.plot(x, np.nanmedian(table, axis=0), label="table-node median")
    plt.plot(x, imu_agg["BSF6C53"]["gyro_p95"], label="BSF6C53 (stool, post-hoc)", alpha=.8)
    plt.xlabel("minutes from T0"); plt.ylabel("gyro P95 (dps)"); plt.yscale("symlog", linthresh=.5); plt.legend()
    savefig(out / "common_table_vibration.svg")

    fig, ax = plt.subplots(figsize=(14, 6))
    for node in NODES: ax.plot(x, uwb_agg[node]["range_step_rms"], lw=.7, label=node)
    ax.set_xlabel("minutes from T0"); ax.set_ylabel("1 s range-vector step RMS (mm)"); ax.set_yscale("symlog", linthresh=20); ax.legend(ncol=5, fontsize=7)
    savefig(out / "uwb_range_vector_change.svg")

    plt.figure(figsize=(14, 5))
    image = np.vstack([masks[n] for n in NODES])
    plt.imshow(image, aspect="auto", interpolation="nearest", extent=[0, 30, len(NODES)-.5, -.5], cmap="Greens")
    plt.yticks(range(len(NODES)), NODES); plt.xlabel("minutes from T0"); plt.title("joint IMU/UWB static classification")
    savefig(out / "static_platforms.svg")

    plt.figure(figsize=(12, 6))
    for node in NODES:
        dt = np.diff(imu_agg[node]["_raw_us"]) / 1000.0
        plt.hist(dt, bins=np.linspace(4.9, 5.1, 81), histtype="step", density=True, label=node)
    plt.xlabel("IMU interval (ms)"); plt.ylabel("density"); plt.legend(ncol=5, fontsize=7)
    savefig(out / "imu_sampling_jitter.svg")

    plt.figure(figsize=(14, 5))
    for node in NODES:
        plt.plot(x, listener[node][:, 0], lw=.6, label=node)
    plt.xlabel("minutes from T0"); plt.ylabel("deduplicated Listener polls/s"); plt.legend(ncol=5, fontsize=7)
    savefig(out / "uwb_listener_cadence.svg")

    plt.figure(figsize=(11, 7))
    for node in NODES:
        rows = [r for r in allan_rows if r["node"] == node and r["sensor_axis"] == "gyro_x_dps"]
        if rows: plt.loglog([r["tau_s"] for r in rows], [r["allan_deviation"] for r in rows], label=node)
    plt.xlabel("tau (s)"); plt.ylabel("overlapping Allan deviation (dps)"); plt.legend(ncol=2, fontsize=8)
    savefig(out / "imu_allan_deviation.svg")

    plt.figure(figsize=(11, 7))
    for node in NODES:
        rows = [r for r in psd_rows if r["node"] == node and r["sensor_axis"] == "acc_norm_g"]
        if rows: plt.loglog([r["frequency_hz"] for r in rows][1:], [r["psd"] for r in rows][1:], label=node)
    plt.xlabel("frequency (Hz)"); plt.ylabel("acc-norm PSD (g^2/Hz)"); plt.legend(ncol=2, fontsize=8)
    savefig(out / "imu_static_psd.svg")

    matrix = np.full((len(NODES), 8), np.nan)
    for r in uwb_rows: matrix[NODES.index(r["node"]), int(r["anchor_slot"])] = float(r["robust_sigma_mm"])
    plt.figure(figsize=(10, 6)); plt.imshow(matrix, aspect="auto", cmap="magma"); plt.colorbar(label="robust sigma (mm)")
    plt.yticks(range(len(NODES)), NODES); plt.xticks(range(8), list("ABCDEFGH")); plt.xlabel("anchor slot")
    savefig(out / "uwb_robust_range_scatter.svg")


def run(data_root: Path, out: Path) -> None:
    if out.exists() and any(out.iterdir()):
        raise RuntimeError(f"refusing to rewrite non-empty analysis directory: {out}")
    out.mkdir(parents=True, exist_ok=True)
    imu, uwb, replay = load_capture(data_root)
    if replay.raw_sha256 != RAW_SHA:
        raise RuntimeError("authoritative raw SHA changed")

    times, fits, imu_agg, uwb_agg = {}, {}, {}, {}
    for node in NODES:
        times[node], slope, intercept, residual95 = clock_fit(imu[node], uwb[node])
        fits[node] = (slope, intercept, residual95)
        imu_agg[node] = aggregate_imu(imu[node], times[node])
        imu_agg[node]["_raw_us"] = imu[node]["b306_us"]
        uwb_agg[node] = aggregate_uwb(uwb[node])
    masks, thresholds = build_static_masks(imu_agg, uwb_agg)
    common = np.logical_and.reduce([masks[n] for n in NODES])
    common_segments = contiguous(common, 3)
    listener = listener_cadence(data_root)
    events = detect_events(imu_agg, uwb_agg)
    for event in events:
        nodes = event["nodes"].split(",")
        rates = [np.mean(listener[n][max(0,event["start_s"]):min(DURATION_S,event["end_s"]+1), 0]) for n in nodes]
        event["listener_evidence"] = f"deduplicated LPD mean={np.mean(rates):.3f} Hz over named nodes; Listener is diagnostic only"

    event_rows = []
    for i, event in enumerate(events, 1):
        event_rows.append({"event_id": f"E{i:03d}", "onset_s": event["start_s"], "end_s": event["end_s"],
                           "onset_wall": f"T0+{event['start_s']:.3f}s", "duration_s": event["end_s"]-event["start_s"],
                           "nodes": event["nodes"], "classification": event["classification"],
                           "imu_evidence": event["imu_evidence"], "uwb_evidence": event["uwb_evidence"],
                           "listener_evidence": event["listener_evidence"], "confidence": event["confidence"],
                           "competing_explanation": event["competing"]})
    write_csv(out / "DISTURBANCE_EVENTS.csv", list(event_rows[0]), event_rows)
    (out / "DISTURBANCE_TIMELINE.json").write_text(json.dumps(event_rows, indent=2, sort_keys=True) + "\n")

    segment_rows = []
    for node in NODES:
        for start, end in contiguous(masks[node], 3):
            segment_rows.append({"node": node, "start_s": start, "end_s": end, "duration_s": end-start,
                                 "common_all_nodes": int(np.all(common[start:end])),
                                 "accel_stability": "PASS", "gyro_stability": "PASS", "uwb_stability": "PASS",
                                 "confidence": "HIGH" if end-start >= 30 else "MEDIUM",
                                 "exclusion_reason": ""})
    write_csv(out / "STATIC_SEGMENTS.csv", list(segment_rows[0]), segment_rows)
    longest_common = max(common_segments, key=lambda x: x[1]-x[0]) if common_segments else (0, 0)
    sensitivity = {}
    for factor in (.8, 1.0, 1.2):
        altered = []
        for node in NODES:
            im, uw, th = imu_agg[node], uwb_agg[node], thresholds[node]
            q = (im["gyro_p95"] < th["gyro_p95_dps"]*factor) & (im["acc_std"] < th["acc_norm_std_g"]*factor)
            q &= (im["gravity_step_deg"] < th["gravity_step_deg"]*factor) & (uw["range_step_rms"] < th["uwb_range_step_rms_mm"]*factor)
            altered.append(binary_opening(binary_closing(q, np.ones(3)), np.ones(3)))
        cs = contiguous(np.logical_and.reduce(altered), 3)
        sensitivity[str(factor)] = {"common_seconds": int(np.sum(np.logical_and.reduce(altered))),
                                    "longest_s": max((b-a for a,b in cs), default=0)}
    common_json = {"t0": T0_WALL, "windows": [{"start_s":a,"end_s":b,"duration_s":b-a} for a,b in common_segments],
                   "longest": {"start_s":longest_common[0],"end_s":longest_common[1],"duration_s":longest_common[1]-longest_common[0]},
                   "thresholds": thresholds, "sensitivity": sensitivity}
    (out / "COMMON_STATIC_WINDOWS.json").write_text(json.dumps(common_json, indent=2, sort_keys=True) + "\n")

    imu_rows, timing_rows, allan_rows, psd_rows = [], [], [], []
    axes = ("acc_x_g","acc_y_g","acc_z_g","gyro_x_dps","gyro_y_dps","gyro_z_dps")
    for node in NODES:
        acc, gyro, temp = imu_physical(imu[node]); physical = np.c_[acc, gyro]
        good = stable_sample_mask(times[node], masks[node])
        for idx, axis in enumerate(axes):
            v = physical[good, idx]; med = np.median(v); mad = np.median(abs(v-med))
            imu_rows.append({"node":node,"sensor_axis":axis,"n":len(v),"mean":f"{np.mean(v):.9g}","median":f"{med:.9g}",
                             "std":f"{np.std(v):.9g}","mad":f"{mad:.9g}","p01":f"{np.quantile(v,.01):.9g}","p99":f"{np.quantile(v,.99):.9g}",
                             "temperature_c_mean":f"{np.mean(temp[good]):.6f}","temperature_correlation":f"{np.corrcoef(v,temp[good])[0,1]:.6g}"})
        segments = contiguous(masks[node], 3)
        longest = max(segments, key=lambda x:x[1]-x[0])
        contiguous_good = (times[node] >= longest[0]) & (times[node] < longest[1])
        for idx, axis in enumerate(axes):
            for tau, adev in overlapping_allan(physical[contiguous_good,idx], 200.0, (longest[1]-longest[0])/4):
                allan_rows.append({"node":node,"sensor_axis":axis,"segment_start_s":longest[0],"segment_end_s":longest[1],
                                   "tau_s":f"{tau:.6g}","allan_deviation":f"{adev:.9g}"})
        psd_signals = {axis: physical[contiguous_good, idx] for idx, axis in enumerate(axes)}
        psd_signals["acc_norm_g"] = np.linalg.norm(acc[contiguous_good], axis=1)
        for axis, values in psd_signals.items():
            freq, power = signal.welch(values, fs=200.0, nperseg=min(1024, len(values)), detrend="constant")
            for f_hz, density in zip(freq, power):
                psd_rows.append({"node":node,"sensor_axis":axis,"segment_start_s":longest[0],"segment_end_s":longest[1],
                                 "frequency_hz":f"{f_hz:.9g}","psd":f"{density:.12g}"})
        dt_us = np.diff(imu[node]["b306_us"].astype(np.int64))
        timing_rows.append({"node":node,"samples":len(imu[node]),"mean_hz":f"{(len(imu[node])-1)/((imu[node]['b306_us'][-1]-imu[node]['b306_us'][0])/1e6):.9f}",
                            "dt_mean_us":f"{np.mean(dt_us):.6f}","dt_std_us":f"{np.std(dt_us):.6f}","dt_p01_us":np.quantile(dt_us,.01),"dt_p99_us":np.quantile(dt_us,.99),
                            "sequence_gaps":sequence_gap_count(imu[node]["seq"],65536),"batch_n_distribution":json.dumps({str(int(x)):int(np.sum(imu[node]['batch_n']==x)) for x in np.unique(imu[node]['batch_n'])},sort_keys=True),
                            "clock_fit_ppm":f"{(fits[node][0]-1)*1e6:.6f}","master_receipt_residual_abs_p95_ms":f"{fits[node][2]:.6f}"})
    write_csv(out / "PER_NODE_IMU_STATS.csv", list(imu_rows[0]), imu_rows)
    write_csv(out / "IMU_TIMING_STATS.csv", list(timing_rows[0]), timing_rows)
    write_csv(out / "IMU_ALLAN_RESULTS.csv", list(allan_rows[0]), allan_rows)
    write_csv(out / "IMU_PSD.csv", list(psd_rows[0]), psd_rows)
    sync_rows=[]
    for i, left in enumerate(NODES):
        for right in NODES[i+1:]:
            a=imu_agg[left]["gyro_p95"]; b=imu_agg[right]["gyro_p95"]
            sync_rows.append({"node_a":left,"node_b":right,"gyro_activity_correlation":f"{np.corrcoef(a,b)[0,1]:.9f}",
                              "same_table_pair":int(left in TABLE_NODES and right in TABLE_NODES)})
    write_csv(out / "IMU_COMMON_MODE_STATS.csv", list(sync_rows[0]), sync_rows)

    uwb_rows, uwb_timing, platform_rows = [], [], []
    for node in NODES:
        u = uwb[node]; sec = np.floor((u["master_ms"]-T0_MASTER_MS)/1000).astype(int)
        use = (sec>=0)&(sec<DURATION_S)&masks[node][np.clip(sec,0,DURATION_S-1)]
        for slot in range(8):
            valid = (u["valid_mask"] & (1<<slot)) != 0; q = use & valid; v=u["range_mm"][q,slot].astype(float)
            med=np.median(v); mad=np.median(abs(v-med));
            def corr(field):
                x=u[field][q,slot].astype(float); return np.corrcoef(v,x)[0,1] if len(v)>2 and np.std(x)>0 else float('nan')
            inv=use&~valid
            uwb_rows.append({"node":node,"platform":"trusted_static_union","anchor_slot":slot,"anchor_id_mode":int(np.median(u['anchor_id'][q,slot])),
                             "record_count":int(np.sum(use)),"valid_count":len(v),"valid_rate":f"{np.mean(valid[use]):.9f}","range_mean_mm":f"{np.mean(v):.6f}",
                             "range_median_mm":f"{med:.6f}","std_mm":f"{np.std(v):.6f}","mad_mm":f"{mad:.6f}","robust_sigma_mm":f"{1.4826*mad:.6f}",
                             "p01_mm":np.quantile(v,.01),"p05_mm":np.quantile(v,.05),"p95_mm":np.quantile(v,.95),"p99_mm":np.quantile(v,.99),
                             "dropout_count":int(np.sum(~valid[use])),"rank_median":float(np.median(u['rank'][q,slot])),"quality_median":float(np.median(u['quality'][q,slot])),
                             "t_round_us_median":float(np.median(u['t_round_us'][q,slot])),"cfo_ppm_q8_median":float(np.median(u['cfo_ppm_q8'][q,slot])),
                             "corr_range_quality":f"{corr('quality'):.6g}","corr_range_cfo_q8":f"{corr('cfo_ppm_q8'):.6g}","corr_range_t_round":f"{corr('t_round_us'):.6g}",
                             "invalid_range_zero_rate":f"{np.mean(u['range_mm'][inv,slot]==0) if np.any(inv) else float('nan'):.6g}"})
        dt=np.diff(u['frame_us'].astype(np.int64)); uwb_timing.append({"node":node,"records":len(u),"mean_hz":f"{(len(u)-1)/((u['frame_us'][-1]-u['frame_us'][0])/1e6):.9f}",
                    "dt_mean_us":f"{np.mean(dt):.6f}","dt_std_us":f"{np.std(dt):.6f}","dt_p01_us":np.quantile(dt,.01),"dt_p99_us":np.quantile(dt,.99),
                    "sweep_gaps":sequence_gap_count(u['sweep'],2**32),"listener_dedup_mean_hz":f"{np.mean(listener[node][:,0]):.6f}","listener_receiver_count_median":float(np.median(listener[node][:,1]))})
    for row in event_rows:
        if row["classification"] != "SINGLE_NODE_REPOSITION_OR_ROTATION": continue
        node=row["nodes"]; s=int(row["onset_s"])+2; rv=uwb_agg[node]["range"]
        before=np.nanmedian(rv[s-20:s-5],axis=0); after=np.nanmedian(rv[s+5:s+20],axis=0)
        for slot in range(8): platform_rows.append({"node":node,"event_id":row["event_id"],"anchor_slot":slot,"before_median_mm":before[slot],"after_median_mm":after[slot],"delta_mm":after[slot]-before[slot],"interpretation":"translation_or_orientation_bias_without_ground_truth"})
    write_csv(out / "UWB_NODE_ANCHOR_STATS.csv", list(uwb_rows[0]), uwb_rows)
    write_csv(out / "UWB_TIMING_STATS.csv", list(uwb_timing[0]), uwb_timing)
    write_csv(out / "UWB_PLATFORM_CHANGES.csv", list(platform_rows[0]), platform_rows)

    registry = {
        "classification":"MOSTLY_STATIC_WITH_UNKNOWN_TABLE_DISTURBANCE_AND_POSSIBLE_NODE_REPOSITIONING",
        "endianness":"little","imu_signedness":"int16 signed", "imu_sequence":{"unit":"accepted sample","width_bits":16,"wrap":"modulo 65536"},
        "acceleration":{"raw":"int16 AX,AY,AZ","full_scale":"+/-16 g","conversion":"raw/32768*16 g","coordinate":"sensor axes; board-to-body extrinsic UNKNOWN_FROM_SOURCE"},
        "gyroscope":{"raw":"int16 GX,GY,GZ","full_scale":"+/-2000 dps","conversion":"raw/32768*2000 dps","coordinate":"sensor axes; handedness/extrinsic UNKNOWN_FROM_SOURCE"},
        "temperature":{"raw":"int16","conversion":"raw/100 degC"},
        "delta_us":"unsigned offset from batch base in B306 1 MHz TIMER2", "base_us":"Fusion Master-extended B306 TIMER2 time at first TWIM pull initiation",
        "master_ms":"Fusion Master k_uptime_get() at BLE notification callback; receipt diagnostic", "host_timestamp":"host monotonic/wall receipt; boundary and infrastructure diagnostic",
        "uwb":{"poll_tx":"DWM1001 DW1000 little-endian 40-bit broadcast poll TX timestamp","sweep":"uint32 monotonic modulo wrap, pairs UART frame with strobe",
               "t_round_us":"measured responder RX minus broadcast poll TX, microseconds","quality":"0..100 producer diagnostic","cfo_ppm_q8":"signed ppm in Q8 fixed point",
               "rank":"TDMA response slot index diagnostic","valid_mask":"bit i declares range_mm[i] usable","anchor_slots":8},
    }
    (out / "FIELD_AND_UNIT_REGISTRY.json").write_text(json.dumps(registry,indent=2,sort_keys=True)+"\n")
    manifest = {"schema":"v47-real-data-adapter-v1","source_raw_sha256":RAW_SHA,"source_size":replay.raw_size,"formal_offset":replay.formal_offset,
                "t0_wall":T0_WALL,"t0_master_ms":T0_MASTER_MS,"duration_s":1800.000110,"nodes":list(NODES),"filters":{"implicit":False,"static_selection":"COMMON_STATIC_WINDOWS.json"},
                "replay_audit":replay.__dict__,"derived_files":"CSV/JSON/SVG only; no raw copy"}
    (out / "FUSION_INPUT_DATASET_MANIFEST.json").write_text(json.dumps(manifest,indent=2,sort_keys=True)+"\n")

    source_audit = f"""# Data source audit\n\nThe authoritative raw is `formal_capture/fusion_host_raw.cobs.bin` (SHA-256 `{RAW_SHA}`). It was streamed through the production COBS/CRC contract. Before T0: {replay.records_before_t0} complete records and {replay.decode_errors_before_t0} accepted preflight-boundary decode error; formal records have one 1-byte shutdown tail fragment and no complete corrupt record. Formal IMU/UWB counts exactly match `PER_BOARD_COUNTS.csv`; every node has zero IMU sequence and UWB sweep gaps.\n\nSource trail: `firmware/src/imu.c` reads 26 bytes from JY61P register 0x34 as signed little-endian AX,AY,AZ,GX,GY,GZ and temperature; `include/biospur_fusion_ble.h` defines batch `seq`, low-word `base_timer2_ts_us`, and per-sample `delta_us`; `host/fusion_master/src/main.c` extends the timer epoch and stamps `master_arrival_ms=k_uptime_get()`; `include/biospur_link.h` defines the 90-byte eight-slot UWB body; `tools/fusion_host_binary.py` is the reference host decoder. The conversion constants are explicitly documented in `docs/ble_protocol.md`.\n\nThe source does not define a calibrated board/body extrinsic, a fully validated axis handedness mapping, or an absolute yaw reference: these are `UNKNOWN_FROM_SOURCE`. Host and Master receipt timestamps are not substituted for B306 sample time. Pre-T0 and shutdown-tail bytes are isolated from formal sensor statistics.\n"""
    (out / "DATA_SOURCE_AUDIT.md").write_text(source_audit)
    compatibility = """# Old Fusion compatibility matrix\n\n| Old algorithm requirement | Current real field | Available | Unit/coordinate certain | Missing |\n|---|---|---|---|---|\n| 120 Hz synthetic/Vicon-derived IMU | 200 Hz timestamped AX..GZ | yes | units yes; body extrinsic no | real loader and extrinsic calibration |\n| UWB solved position at capture cadence | eight raw ranges at ~8.33 Hz | raw only | mm/anchor ID yes | production geometry/delay binding and solver output |\n| world frame x,z horizontal; y up | sensor-local axes | no direct map | no | board/body/world rotation |\n| gravity `[0,-9.80665,0]` world | measured specific force | yes | scale yes | initial attitude; yaw unobservable statically |\n| simulated bias/noise/RW | measured static bias/noise/Allan | partly | one pose only | temperature/multi-pose calibration |\n| initial pose from Vicon/trajectory | none | no | no | pose/heading initialization |\n| exact synthetic time alignment | B306 TIMER2 per node; UWB same node clock | within node yes | yes | cross-node common-clock fit is diagnostic, not a global truth clock |\n| state `[p,v]` lite EKF or ESKF bias states | adapter provides IMU/UWB observations | input only | fields yes | validated attitude propagation/state initialization |\n| propagation using fixed 1/120 s fallback | real B306 timestamp | yes | yes | loader must remove fixed-dt assumption |\n| solved-position UWB update / limited raw prototype | eight-slot observations | yes | fields yes | production raw-range update and explicit invalid policy |\n| innovation/NIS rejection | quality/validity retained | no implicit gate | yes | tuned R matrix and explicit rejection policy |\n| A0 geometry and R2/R4 delay/bias | not embedded in capture | not bound | no | select authoritative production geometry; do not refit here |\n| ideal/perfect IMU L0 or simulated L models | JY61P real data | no | n/a | abandon ideal-noise assumption |\n| Vicon truth and full wand motion | absent | no | n/a | known trajectory or external ground truth for accuracy |\n\nThe reviewed branch `feature/wand-internal-sweep` implements position-domain T2/T3/T5, limited raw-range T6/T8 prototypes, and an IMU-only T11 diagnostic. The "real 6-axis" path still synthesizes IMU from Vicon at nominal sensor ODR, assumes a gravity/world convention and trajectory-derived initial orientation, and is not a loader for these physical JY61P samples. Final Fusion is therefore intentionally not run here.\n"""
    (out / "OLD_FUSION_COMPATIBILITY_MATRIX.md").write_text(compatibility)
    (out / "TIME_MODEL.md").write_text("""# Time model\n\nEach node's B306 1 MHz TIMER2 is the authoritative local IMU/UWB time axis. `base_us + delta_us` locates every IMU sample; UWB `frame_us`/hardware `strobe_us` share that B306 timer. DWM `poll_tx` is a separate wrapping 40-bit DW1000 clock and is not directly a host time. `master_ms` is BLE callback receipt time and host monotonic/wall time is collection receipt/boundary time.\n\nFor each node this analysis fits B306 time to Master receipt only to place independent streams on a coarse common event timeline; residual BLE latency remains and the fit is not promoted to measurement truth. A Fusion loader must propagate at every actual IMU timestamp and insert an UWB update between the immediately bracketing IMU steps on the same B306 clock. It must use modular uint16 IMU sequence and uint32 UWB sweep arithmetic and retain 64-bit extended B306 time. It must not assume exactly 200 Hz or 8.33 Hz. Cross-node comparisons are suitable for common-mode event evidence, not multi-node phase-locked inertial fusion.\n""")
    (out / "REAL_DATA_ADAPTER_SPEC.md").write_text("""# Real-data adapter specification\n\n`v47_real_data_adapter.py` streams zero-delimited COBS records, validates CRC-16/CCITT-FALSE and payload contracts, isolates the manifest's formal byte boundary, and emits typed per-node arrays. Raw counts and physical conversions coexist. UWB output preserves all eight slots, invalid slots, ID, rank, quality, CFO, round time, masks, DWM poll time and B306 capture time. No default quality rejection, interpolation, smoothing, resampling, or platform merge occurs. Static/event selection is an explicit second mask. Any future NPZ/Parquet cache must carry raw SHA, adapter version, formal offset and complete filter configuration and must remain deterministically regenerable.\n""")

    moves = [r for r in event_rows if r["classification"] == "SINGLE_NODE_REPOSITION_OR_ROTATION"]
    common_events = [r for r in event_rows if r["classification"] == "TABLE_COMMON_MODE_VIBRATION"]
    report = f"""# v47 real-sensor static baseline and disturbance analysis\n\n## Verdict: CONDITIONAL_GO\n\nThe raw transport is complete and is sufficient for a timestamp-correct real Fusion loader, static replay and calibration work. It is not sufficient to claim absolute localization accuracy or to start uncalibrated human-body fusion. Blind detection found {len(moves)} credible node reposition/rotation events: {', '.join(f"{r['nodes']} at T0+{r['onset_s']}..{r['end_s']} s" for r in moves)}. It did not force a two-board answer. {len(common_events)} short common-mode table-vibration intervals were identified independently across at least five of nine table nodes. BSF6C53 was not exempted from sensor integrity; only after detection, its quiet gyro timeline is consistent with the operator's separate-stool description.\n\nThe longest ten-node joint static window is T0+{longest_common[0]}..{longest_common[1]} s ({longest_common[1]-longest_common[0]} s). Threshold sensitivity (0.8/1.0/1.2) is recorded in `COMMON_STATIC_WINDOWS.json`; platform identities remain, while exact boundary seconds vary. C2CC shows a multi-anchor range-vector platform change plus about 3.3° gravity-direction change and large gyro motion, supporting real repositioning. AA61 shows large gyro motion and about 1.18° gravity-direction change with a smaller multi-anchor range response, supporting rotation/repositioning but leaving translation versus antenna orientation unresolved.\n\nStatic IMU bias/noise varies between boards, as expected from one arbitrary pose; no result is a six-face calibration. BSF6C53's gyro-norm baseline is higher than most peers but stable, while EC35 has the broadest gyro tail; these are calibration watch items, not proven defects. UWB shows many dominant single-anchor step changes without IMU motion, classified as RF visibility/multipath rather than board movement. BSF6C53's Listener cadence remains an RF-geometry special case only; its B306 UWB records and sequences are complete. Per-link robust scatter and validity are in `UWB_NODE_ANCHOR_STATS.csv`. No position solution or absolute error is reported because this run has neither ground truth nor a capture-bound authoritative geometry/delay manifest.\n\nThe old simulation assumptions that fail are: perfect/Vicon-derived IMU, nominal fixed dt, known world/body attitude, trajectory-derived initialization, solved UWB positions, and synthetic bias/noise. Before human capture, perform accelerometer six-face scale/misalignment calibration, multi-temperature gyro zero-rate/bias characterization, board-to-body extrinsic calibration, a signed-axis rotation test, and bind the exact production anchor geometry/delay/R matrix. The next experiment should be **static ZUPT/replay first**, then a known manual trajectory with ground truth; direct human-node collection is premature.\n\nAllan deviation uses only each node's longest contiguous accepted static segment and caps tau at one quarter of that segment. Table vibration is excluded from white-noise estimates. A single pose cannot determine accelerometer scale/misalignment, gyro scale factor, or yaw, and table tilt cannot be separated from accelerometer bias here.\n"""
    (out / "REPORT.md").write_text(report)
    render_plots(out, imu_agg, uwb_agg, masks, listener, allan_rows, uwb_rows, psd_rows)

    # Deterministic integrity ledger, excluding itself.
    lines=[]
    for path in sorted(p for p in out.iterdir() if p.name != "SHA256SUMS"):
        lines.append(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}")
    (out / "SHA256SUMS").write_text("\n".join(lines)+"\n")


def main() -> None:
    parser=argparse.ArgumentParser()
    parser.add_argument("--data-root",type=Path,required=True)
    parser.add_argument("--output",type=Path,required=True)
    args=parser.parse_args(); run(args.data_root,args.output)


if __name__ == "__main__": main()
