#!/usr/bin/env python3
"""Offline closeout for the relay8.1 unattended ten-node run."""

from __future__ import annotations

import argparse
import bisect
import concurrent.futures
import csv
import hashlib
import json
import math
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

import numpy as np

from batch_g_day_h3 import SLOT_MAP, SLOT10
from batch_g_overnight import NODES, TAG_NUMBER, u32_delta
from fusion_session import parse_fields


ROOT = Path(__file__).resolve().parents[2]
ALIGNER_DIR = Path(__file__).resolve().parent / "alignment" / "v2"
if str(ALIGNER_DIR) not in sys.path:
    sys.path.insert(0, str(ALIGNER_DIR))
import time_aligner_v2 as align  # noqa: E402

LAYOUT = (
    ROOT
    / "UWB_Part/2026-07-15-FREEZE/scripts/solvers/"
    "erlangen_deployment_v4io_t4/reference_layout_inputs/anchor_layout.json"
)
HARD_TELEMETRY = (
    "crc",
    "header",
    "ring_drop",
    "duplicate",
    "reorder",
    "drop_err",
    "notify_errno",
    "uart_err",
    "logger_drop",
)
QUEUE_GATES = (
    "q_drop_imu",
    "q_drop_uwb",
    "abort_imu",
    "abort_uwb",
)
LED_COUNTERS = ("crc", "seq", "queue", "io", "disc")


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    if not rows:
        return
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def iter_fusion(path: Path, start: float, end: float):
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for raw in handle:
            parts = raw.split(" ", 3)
            if len(parts) < 4:
                continue
            try:
                host_mono = float(parts[1])
            except ValueError:
                continue
            if start <= host_mono <= end:
                yield host_mono, raw.rstrip("\n")


def first_last_update(
    table: dict[str, dict[str, dict[str, int]]],
    node: str,
    fields: dict[str, str],
    keys: tuple[str, ...],
) -> None:
    values = {key: int(fields[key], 0) for key in keys if key in fields}
    if not values:
        return
    row = table.setdefault(node, {"first": {}, "last": {}})
    for key, value in values.items():
        row["first"].setdefault(key, value)
        row["last"][key] = value


def deltas(row: dict[str, dict[str, int]], keys: tuple[str, ...]) -> dict[str, int]:
    return {
        key: u32_delta(row["first"][key], row["last"][key])
        for key in keys
        if key in row.get("first", {}) and key in row.get("last", {})
    }


def parse_imu(fields: dict[str, str]) -> list[tuple[int, list[int]]]:
    samples = []
    for token in fields.get("samples", "").split(";"):
        if not token:
            continue
        values = [int(value, 0) for value in token.split(",")]
        if len(values) == 7:
            samples.append((values[0], values[1:]))
    return samples


def snapshot_status(snapshot: dict[str, object], node: str) -> dict[str, str]:
    row = snapshot.get("beacon_status", {}).get(node, {})
    return row.get("fields", {}) if isinstance(row, dict) else {}


def listener_field_metrics(listener_dir: Path, start: float, end: float) -> dict[str, object]:
    main_snr = "760184545"
    sub_snr = "760181725"
    result: dict[str, object] = {
        "main_lstat": [],
        "main_lbtx_counters": [],
        "sub_roles": [],
        "sub_lftx": 0,
    }
    for snr in (main_snr, sub_snr):
        path = listener_dir / "listeners" / f"{snr}.jsonl"
        if not path.exists():
            continue
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                row = json.loads(line)
                host = int(row.get("arrival_monotonic_ns", 0)) / 1e9
                if not start <= host <= end:
                    continue
                if snr == main_snr:
                    if row.get("kind") == "LSTAT":
                        result["main_lstat"].append(row.get("fields", {}))
                    elif row.get("kind") == "LBTX":
                        result["main_lbtx_counters"].append(
                            int(row.get("fields", {}).get("superframe_counter", 0))
                        )
                if snr == sub_snr:
                    if row.get("kind") == "LSTAT":
                        result["sub_roles"].append(row.get("fields", {}).get("role"))
                    elif row.get("kind") == "LBTX":
                        result["sub_lftx"] += 1
    counters = result["main_lbtx_counters"]
    if len(counters) >= 2:
        attempts = max(1, u32_delta(counters[0], counters[-1]))
        start_fail = max(0, attempts - (len(counters) - 1))
        result["main_start_fail_delta"] = start_fail
        result["main_epoch_delta"] = attempts
        result["main_start_fail_fraction"] = start_fail / attempts
    else:
        result["main_start_fail_fraction"] = None
    result["sub_slaved"] = (
        bool(result["sub_roles"])
        and all(role == "SLAVED" for role in result["sub_roles"])
        and result["sub_lftx"] == 0
    )
    return result


def robust_listener_epoch_offsets(
    boards: dict[str, object],
    fits: dict[str, object],
    name_to_src: dict[str, int],
    polls: list[object],
) -> dict[str, object]:
    """Identify the absolute epoch without assuming sweep low8 == poll_seq.

    Runtime CFG resets the tag-owned sweep counter while the over-air poll
    sequence has its own history.  First learn their modulo-256 offset from
    nearest host-time pairs, then rematch by that offset and take the modal
    listener_epoch - fitted_epoch integer.
    """
    by_src: dict[int, list[object]] = defaultdict(list)
    for poll in polls:
        by_src[poll.src].append(poll)
    nodes: dict[str, object] = {}
    for name, board in boards.items():
        source = name_to_src[name]
        source_polls = by_src.get(source, [])
        times = list(board.host_s)
        nearest_offsets: list[int] = []
        for poll in source_polls:
            position = bisect.bisect_left(times, poll.host_s)
            choices = [i for i in (position - 1, position) if 0 <= i < len(times)]
            if not choices:
                continue
            index = min(choices, key=lambda i: abs(times[i] - poll.host_s))
            if abs(times[index] - poll.host_s) <= 0.5:
                nearest_offsets.append(
                    ((int(board.sweep[index]) & 0xFF) - poll.sequence) & 0xFF
                )
        if not nearest_offsets:
            raise ValueError(f"{name}: no listener sequence-offset seed")
        sequence_offset, sequence_modal_count = Counter(nearest_offsets).most_common(1)[0]
        candidates: dict[int, list[tuple[float, int]]] = defaultdict(list)
        for index, (host, sweep) in enumerate(zip(times, board.sweep)):
            candidates[int(sweep) & 0xFF].append((host, index))
        candidate_times = {
            key: [row[0] for row in values] for key, values in candidates.items()
        }
        epoch_offsets: list[int] = []
        matched = 0
        for poll in source_polls:
            values = candidates.get((poll.sequence + sequence_offset) & 0xFF, [])
            if not values:
                continue
            value_times = candidate_times[(poll.sequence + sequence_offset) & 0xFF]
            position = bisect.bisect_left(value_times, poll.host_s)
            choices = [i for i in (position - 1, position) if 0 <= i < len(values)]
            if not choices:
                continue
            choice = min(choices, key=lambda i: abs(value_times[i] - poll.host_s))
            host, index = values[choice]
            if abs(host - poll.host_s) > 0.5:
                continue
            epoch_offsets.append(poll.epoch - int(fits[name].epoch_index[index]))
            matched += 1
        if not epoch_offsets:
            raise ValueError(f"{name}: no listener epoch matches")
        modal_offset, modal_count = Counter(epoch_offsets).most_common(1)[0]
        nodes[name] = {
            "src": f"0x{source:04X}",
            "sequence_offset_sweep_minus_poll": sequence_offset,
            "sequence_offset_modal_fraction": sequence_modal_count / len(nearest_offsets),
            "modal_offset": modal_offset,
            "modal_fraction": modal_count / len(epoch_offsets),
            "raw_listener_matches": matched,
        }
    return {"method": "host-seeded-sequence-offset", "nodes": nodes}


def solve_positions_worker(payload):
    name, anchors, frames = payload
    from scipy.optimize import least_squares

    anchors = np.asarray(anchors, dtype=float)
    previous = np.mean(anchors, axis=0)
    solved = []
    for host, ranges in frames:
        valid = np.array([i for i, value in enumerate(ranges) if 0 < value < 65535])
        if valid.size < 4:
            continue
        points = anchors[valid]
        radii = np.asarray([ranges[i] for i in valid], dtype=float)

        def residual(position):
            return np.linalg.norm(points - position, axis=1) - radii

        fit = least_squares(
            residual,
            previous,
            loss="huber",
            f_scale=50.0,
            max_nfev=30,
        )
        if fit.success and np.all(np.isfinite(fit.x)):
            previous = fit.x
            solved.append((host, *map(float, fit.x)))
    return name, solved


def plot_products(
    fusion_log: Path,
    start: float,
    end: float,
    output: Path,
) -> tuple[dict[str, object], list[dict[str, object]]]:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    layout = json.loads(LAYOUT.read_text(encoding="utf-8"))
    anchors = np.asarray(
        [[row["x_mm"], row["y_mm"], row["z_mm"]] for row in layout["anchors"]],
        dtype=float,
    )
    range_frames: dict[str, list[tuple[float, list[int]]]] = defaultdict(list)
    imu: dict[str, list[tuple[float, list[float]]]] = defaultdict(list)
    for host, line in iter_fusion(fusion_log, start, end):
        fields = parse_fields(line)
        node = fields.get("name")
        if node not in NODES:
            continue
        if "FUSION_UWB " in line and "ranges" in fields:
            ranges = [65535] * 8
            for item in fields["ranges"].split(","):
                if ":" not in item:
                    continue
                idx, value = item.split(":", 1)
                if int(idx) < 8:
                    ranges[int(idx)] = int(value)
            range_frames[node].append((host, ranges))
        elif "FUSION_IMU " in line:
            base = int(fields.get("base_us", "0"), 0)
            for offset, axes in parse_imu(fields):
                t = (base + offset) / 1e6
                converted = [
                    axes[0] / 2048.0,
                    axes[1] / 2048.0,
                    axes[2] / 2048.0,
                    axes[3] * 2000.0 / 32768.0,
                    axes[4] * 2000.0 / 32768.0,
                    axes[5] * 2000.0 / 32768.0,
                ]
                imu[node].append((t, converted))

    jobs = [(node, anchors.tolist(), range_frames[node]) for node in NODES]
    positions = {}
    with concurrent.futures.ProcessPoolExecutor(max_workers=8) as pool:
        for node, solved in pool.map(solve_positions_worker, jobs):
            positions[node] = solved

    position_stats: dict[str, object] = {}
    fig = plt.figure(figsize=(12, 9))
    ax = fig.add_subplot(111, projection="3d")
    colors = plt.cm.tab10(np.linspace(0, 1, len(NODES)))
    for color, node in zip(colors, NODES):
        values = np.asarray([row[1:] for row in positions.get(node, [])], dtype=float)
        attempted = len(range_frames[node])
        if values.size == 0:
            position_stats[node] = {"attempted": attempted, "solved": 0, "rms_mm": None}
            continue
        mean = values.mean(axis=0)
        axis_rms = np.sqrt(np.mean((values - mean) ** 2, axis=0))
        rms = float(np.sqrt(np.mean(np.sum((values - mean) ** 2, axis=1))))
        position_stats[node] = {
            "attempted": attempted,
            "solved": len(values),
            "mean_mm": mean.tolist(),
            "axis_rms_mm": axis_rms.tolist(),
            "rms_mm": rms,
        }
        display = values[:: max(1, len(values) // 1500)]
        ax.scatter(display[:, 0], display[:, 1], display[:, 2], s=2, alpha=0.25, color=color)
        ax.scatter(*mean, s=45, color=color, label=f"{node} ({rms:.1f} mm)")
    ax.scatter(anchors[:, 0], anchors[:, 1], anchors[:, 2], marker="^", s=70, color="black", label="anchors")
    ax.set_xlabel("x (mm)")
    ax.set_ylabel("y (mm)")
    ax.set_zlabel("z (mm)")
    ax.set_title("First 10 min: per-node position scatter (V4-io geometry)")
    ax.legend(fontsize=7, ncol=2)
    fig.tight_layout()
    fig.savefig(output / "positions_3d_first10min.png", dpi=180)
    plt.close(fig)
    write_json(output / "positions_first10min.json", {"layout": str(LAYOUT), "nodes": position_stats})

    imu_rows: list[dict[str, object]] = []
    for node in NODES:
        rows = imu[node]
        values = np.asarray([row[1] for row in rows], dtype=float)
        if values.size == 0:
            imu_rows.append({"node": node, "samples": 0})
            continue
        names = ("ax_g", "ay_g", "az_g", "gx_dps", "gy_dps", "gz_dps")
        record: dict[str, object] = {"node": node, "samples": len(values)}
        for index, name in enumerate(names):
            record[f"{name}_mean"] = float(values[:, index].mean())
            record[f"{name}_std"] = float(values[:, index].std())
        imu_rows.append(record)
        t = np.asarray([row[0] for row in rows])
        t -= t[0]
        step = max(1, len(t) // 12000)
        fig, axes = plt.subplots(2, 1, figsize=(12, 7), sharex=True)
        for index, name in enumerate(names[:3]):
            axes[0].plot(t[::step], values[::step, index], lw=0.45, label=name)
        for index, name in enumerate(names[3:], start=3):
            axes[1].plot(t[::step], values[::step, index], lw=0.45, label=name)
        axes[0].set_ylabel("acceleration (g)")
        axes[1].set_ylabel("angular rate (deg/s)")
        axes[1].set_xlabel("node time (s)")
        axes[0].legend(ncol=3)
        axes[1].legend(ncol=3)
        axes[0].set_title(f"{node}: first 10 min six-axis IMU")
        fig.tight_layout()
        fig.savefig(output / f"imu_{node}_first10min.png", dpi=160)
        plt.close(fig)
    write_csv(output / "imu_bias_noise_first10min.csv", imu_rows)
    return position_stats, imu_rows


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def analyze(args: argparse.Namespace) -> dict[str, object]:
    state = json.loads((args.capture_root / "OVERNIGHT_RUN_STATE.json").read_text(encoding="utf-8"))
    run_state = json.loads((args.capture_root / "run_state.json").read_text(encoding="utf-8"))
    snapshots = json.loads((args.capture_root / "snapshots.json").read_text(encoding="utf-8"))
    chunks = state.get("chunks", [])
    if not chunks:
        raise RuntimeError("no capture chunks")
    w_chunks = chunks[: min(6, len(chunks))]
    w_start = float(w_chunks[0]["capture"]["started_monotonic"])
    w_end = float(w_chunks[-1]["capture"]["ended_monotonic"])
    fusion_log = args.capture_root / "fusion_cdc.log"
    listener_dir = args.capture_root / "continuous_listener_capture"

    boards = align.extract_fusion(fusion_log, w_start, w_end)
    fits = {node: align.fit_board(boards[node]) for node in NODES if node in boards}
    f3 = align.master_integer_shifts(boards, fits, SLOT_MAP)
    name_to_src = {node: 0xB100 + TAG_NUMBER[node] for node in NODES}
    polls, listener_audit = align.load_listener_polls(
        listener_dir,
        w_start,
        w_end,
        {name_to_src[node]: SLOT_MAP[node] for node in NODES},
    )
    f4 = robust_listener_epoch_offsets(boards, fits, name_to_src, polls)

    uwb_mod: dict[str, list[int]] = defaultdict(list)
    imu_batches: dict[str, list[dict[str, object]]] = defaultdict(list)
    queue: dict[str, dict[str, dict[str, int]]] = {}
    telemetry: dict[str, dict[str, dict[str, int]]] = {}
    disconnects = []
    malformed = []
    for _host, line in iter_fusion(fusion_log, w_start, w_end):
        fields = parse_fields(line)
        node = fields.get("name")
        if "FUSION_UWB " in line and node in NODES and fields.get("sf_valid") == "1":
            uwb_mod[node].append(int(fields["sf_mod16"], 0))
        elif "FUSION_IMU " in line and node in NODES:
            samples = parse_imu(fields)
            imu_batches[node].append(
                {
                    "seq": int(fields["seq"], 0),
                    "n": int(fields["n"], 0),
                    "base_us": int(fields["base_us"], 0),
                    "last_offset": samples[-1][0] if samples else 0,
                }
            )
        elif "FUSION_QUEUE " in line and node in NODES:
            first_last_update(queue, node, fields, QUEUE_GATES)
        elif "FUSION_TELEMETRY " in line and node in NODES:
            first_last_update(
                telemetry,
                node,
                fields,
                HARD_TELEMETRY + ("imu_i2c_err", "imu_hreset"),
            )
        elif "FUSION_DISCONNECTED " in line:
            disconnects.append(line)
        elif "FUSION_MALFORMED " in line:
            malformed.append(line)

    pre = state["w_before"]
    post_index = int(w_chunks[-1]["snapshot_index"])
    post = snapshots[post_index]
    led_pre = parse_fields(str(pre.get("ledstat", "")))
    led_post = parse_fields(str(post.get("ledstat", "")))
    led_delta = {
        key: u32_delta(int(led_pre[key], 0), int(led_post[key], 0))
        for key in LED_COUNTERS
        if key in led_pre and key in led_post
    }
    listener_metrics = listener_field_metrics(listener_dir, w_start, w_end)
    decoder_errors = sum(int(row["capture"].get("decoder_errors", 0)) for row in w_chunks)
    host_red: list[object] = []
    for row in w_chunks:
        markers = row.get("host_drain", {}).get("red_markers", [])
        if isinstance(markers, list):
            host_red.extend(markers)
        elif isinstance(markers, int):
            host_red.extend(
                {"chunk": row.get("index"), "ordinal": ordinal + 1}
                for ordinal in range(markers)
            )

    node_rows: dict[str, object] = {}
    for node in NODES:
        board = boards.get(node)
        fit = fits.get(node)
        if board is None or fit is None or len(board.frame_us) < 2:
            node_rows[node] = {"available": False, "pass": False}
            continue
        epoch_span = int(fit.epoch_index[-1] - fit.epoch_index[0])
        uwb_rate = (len(board.frame_us) - 1) / (epoch_span * 0.110) if epoch_span else 0.0
        mods = uwb_mod[node]
        delta_plus_one = (
            sum(((b - a) & 0xF) == 1 for a, b in zip(mods, mods[1:]))
            / max(1, len(mods) - 1)
        )
        absolute = fit.epoch_index + int(f4["nodes"][node]["modal_offset"])
        paired = min(len(mods), len(absolute))
        epoch_exact = (
            sum(mods[index] == (int(absolute[index]) & 0xF) for index in range(paired))
            / max(1, paired)
        )
        batches = imu_batches[node]
        delivered = sum(int(row["n"]) for row in batches)
        imu_gap_events = 0
        imu_missing_samples = 0
        for left, right in zip(batches, batches[1:]):
            expected = (int(left["seq"]) + int(left["n"])) & 0xFFFF
            observed = int(right["seq"]) & 0xFFFF
            if observed != expected:
                imu_gap_events += 1
                delta = (observed - expected) & 0xFFFF
                if delta < 0x8000:
                    imu_missing_samples += delta
        expected_imu = delivered + imu_missing_samples
        imu_fraction = delivered / expected_imu if expected_imu else 0.0
        q_delta = deltas(queue.get(node, {"first": {}, "last": {}}), QUEUE_GATES)
        t_delta = deltas(
            telemetry.get(node, {"first": {}, "last": {}}),
            HARD_TELEMETRY + ("imu_i2c_err", "imu_hreset"),
        )
        a = snapshot_status(pre, node)
        b = snapshot_status(post, node)
        if all(key in a and key in b for key in ("rx", "miss")):
            rx = u32_delta(int(a["rx"], 0), int(b["rx"], 0))
            miss = u32_delta(int(a["miss"], 0), int(b["miss"], 0))
            miss_fraction = miss / (rx + miss) if rx + miss else None
        else:
            rx = miss = None
            miss_fraction = None
        hard_zero = all(t_delta.get(key, 0) == 0 for key in HARD_TELEMETRY)
        queues_zero = all(q_delta.get(key, 0) == 0 for key in QUEUE_GATES)
        node_pass = (
            queues_zero
            and hard_zero
            and imu_fraction >= 0.999
            and imu_gap_events == 0
            and uwb_rate >= 0.99 * (1000.0 / 110.0)
        )
        node_rows[node] = {
            "available": True,
            "slot": SLOT_MAP[node],
            "uwb_records": len(board.frame_us),
            "elapsed_epochs": epoch_span,
            "tag_domain_uwb_hz": uwb_rate,
            "imu_samples": delivered,
            "imu_delivery_fraction": imu_fraction,
            "imu_sequence_gaps": imu_gap_events,
            "imu_missing_samples": imu_missing_samples,
            "queue_deltas": q_delta,
            "telemetry_deltas": t_delta,
            "delta_mod16_plus1_fraction": delta_plus_one,
            "listener_epoch_mod16_exact_fraction": epoch_exact,
            "listener_pairs": paired,
            "beacon_rx_delta": rx,
            "beacon_miss_delta": miss,
            "beacon_window_miss_fraction": miss_fraction,
            "pass": node_pass,
        }

    global_pass = (
        len(node_rows) == 10
        and all(bool(row.get("pass")) for row in node_rows.values())
        and all(value == 0 for value in led_delta.values())
        and decoder_errors == 0
        and not host_red
        and not disconnects
        and not malformed
        and bool(listener_metrics.get("sub_slaved"))
        and (
            listener_metrics.get("main_start_fail_fraction") is not None
            and float(listener_metrics["main_start_fail_fraction"]) <= 0.01
        )
    )
    fix_readings = {
        "all_delta_mod16_plus1_ge_99_9pct": all(
            row.get("delta_mod16_plus1_fraction", 0.0) >= 0.999
            for row in node_rows.values()
        ),
        "listener_absolute_epoch_match_100pct": all(
            row.get("listener_epoch_mod16_exact_fraction", 0.0) == 1.0
            for row in node_rows.values()
        ),
        "window_miss_approximately_zero": all(
            row.get("beacon_window_miss_fraction") is not None
            and row["beacon_window_miss_fraction"] <= 0.001
            for row in node_rows.values()
        ),
        "slot10_node": SLOT10,
        "slot10_tag_domain_hz": node_rows.get(SLOT10, {}).get("tag_domain_uwb_hz"),
        "slot10_rate_ge_9hz": node_rows.get(SLOT10, {}).get("tag_domain_uwb_hz", 0.0) >= 9.0,
    }

    first10_end = min(w_start + 600.0, w_end)
    position_stats, imu_rows = plot_products(
        fusion_log, w_start, first10_end, args.output
    )

    capture_end = max(float(row["capture"]["ended_monotonic"]) for row in chunks)
    last_data: dict[str, float] = {}
    for host, line in iter_fusion(fusion_log, w_start, capture_end):
        if "FUSION_UWB " not in line and "FUSION_IMU " not in line:
            continue
        node = parse_fields(line).get("name")
        if node in NODES:
            last_data[node] = host
    endurance = {}
    for node in NODES:
        epochs = run_state.get("alive_epochs", {}).get(node, [])
        closed = [
            float(row["closed_monotonic"])
            for row in epochs
            if row.get("closed_monotonic") is not None
            and float(row["closed_monotonic"]) >= w_start
        ]
        ble_end = min(closed) if closed else capture_end
        data_end = min(last_data.get(node, w_start), capture_end)
        data_cessation = capture_end - data_end > 300.0
        endurance[node] = {
            "alive_s_from_W_start": max(0.0, data_end - w_start),
            "data_plane_cessation_observed": data_cessation,
            "data_plane_right_censored": not data_cessation,
            "ble_alive_s_from_W_start": max(0.0, ble_end - w_start),
            "ble_death_observed": bool(closed),
            "ble_right_censored": not bool(closed),
        }

    result = {
        "w_window": {"start_monotonic": w_start, "end_monotonic": w_end, "duration_s": w_end - w_start},
        "w_verdict": "PASS" if global_pass else "FAIL",
        "nodes": node_rows,
        "dk_led_counter_deltas": led_delta,
        "decoder_errors": decoder_errors,
        "host_red_markers": host_red,
        "disconnects": disconnects,
        "malformed": malformed,
        "listener_field": listener_metrics,
        "fix_readings": fix_readings,
        "listener_audit": listener_audit,
        "f4": f4,
        "positions": position_stats,
        "imu_bias_noise": imu_rows,
        "endurance": endurance,
        "terminal_reason": state.get("terminal_reason"),
        "remaining_capacity_context": True,
    }
    write_json(args.output / "analysis.json", result)
    write_csv(
        args.output / "w_gate_table.csv",
        [
            {
                "node": node,
                "slot": row.get("slot"),
                "uwb_hz": row.get("tag_domain_uwb_hz"),
                "imu_delivery": row.get("imu_delivery_fraction"),
                "imu_seq_gaps": row.get("imu_sequence_gaps"),
                "delta_mod16_plus1": row.get("delta_mod16_plus1_fraction"),
                "epoch_exact": row.get("listener_epoch_mod16_exact_fraction"),
                "window_miss": row.get("beacon_window_miss_fraction"),
                "pass": row.get("pass"),
            }
            for node, row in node_rows.items()
        ],
    )
    write_csv(
        args.output / "endurance.csv",
        [{"node": node, **row} for node, row in endurance.items()],
    )
    return result


def build_report(args: argparse.Namespace, result: dict[str, object]) -> None:
    def fmt_float(value: object, digits: int = 6) -> str:
        return f"{float(value):.{digits}f}" if isinstance(value, (int, float)) else "-"

    ota_ledger = json.loads(args.ota_ledger.read_text(encoding="utf-8"))
    complete = 2 + sum(row.get("status") == "COMPLETE" for row in ota_ledger)
    quarantined = sum(row.get("status") == "QUARANTINED" for row in ota_ledger)
    alive_values = [row["alive_s_from_W_start"] for row in result["endurance"].values()]
    endurance_line = (
        f"Endurance outcome: terminal={result.get('terminal_reason')}, "
        f"per-node data-service span {min(alive_values)/3600:.2f}–{max(alive_values)/3600:.2f} h; "
        "data cessation is not assumed to mean battery death, and this is not a full-charge endurance record."
    )
    lines = [
        "# relay8.1 overnight report",
        "",
        f"OTA outcome: {complete}/10 canonical relay8.1 confirmed, {quarantined} quarantined.",
        f"W verdict: **{result['w_verdict']}** across the preregistered ten-node gate.",
        endurance_line,
        "",
        "## Phase 1 — OTA and command-path warm-up",
        "",
        "| BSF | result | confirmed | first successful VERSION after true app start (s) |",
        "|---|---|---:|---:|",
        "| BSF3C79 | COMPLETE | 1 | 287.58 (earlier patient discriminator) |",
        "| BSFC2CC | COMPLETE | 1 | not measured against the corrected discriminator |",
    ]
    for row in ota_ledger:
        delay = row.get("first_success_delay_from_discontinuity_s")
        lines.append(
            f"| {row['node']} | {row['status']} | {row.get('confirmed', '-')} | "
            f"{delay:.3f} |" if isinstance(delay, (int, float)) else
            f"| {row['node']} | {row['status']} | {row.get('confirmed', '-')} | - |"
        )
    measured = [
        float(row["first_success_delay_from_discontinuity_s"])
        for row in ota_ledger
        if row["node"] != "BSF44AD"
        and isinstance(row.get("first_success_delay_from_discontinuity_s"), (int, float))
    ]
    lines += [
        "",
        f"Corrected seven-board observed distribution: min {min(measured):.3f} s, "
        f"median {float(np.median(measured)):.3f} s, max {max(measured):.3f} s. "
        "The first query was deliberately scheduled after the 15 s readiness hold, so this is an observation bound, not the intrinsic earliest command-ready time.",
        "",
        "## W qualification",
        "",
        f"Window duration: {result['w_window']['duration_s']:.3f} s. All rates are tag-domain rates.",
        "",
        "| BSF | slot | UWB Hz | IMU delivered | IMU gaps | Δmod16 +1 | epoch exact | miss fraction | verdict |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for node in sorted(NODES, key=lambda name: SLOT_MAP[name]):
        row = result["nodes"][node]
        lines.append(
            f"| {node} | {row.get('slot')} | {row.get('tag_domain_uwb_hz', 0):.5f} | "
            f"{row.get('imu_delivery_fraction', 0):.6f} | {row.get('imu_sequence_gaps')} | "
            f"{row.get('delta_mod16_plus1_fraction', 0):.6f} | "
            f"{row.get('listener_epoch_mod16_exact_fraction', 0):.6f} | "
            f"{fmt_float(row.get('beacon_window_miss_fraction'))} | "
            f"{'PASS' if row.get('pass') else 'FAIL'} |"
        )
    lines += [
        "",
        "Gated counter deltas are preserved in `analysis/analysis.json`. Non-zero gated deltas by node:",
        "",
    ]
    for node in sorted(NODES, key=lambda name: SLOT_MAP[name]):
        row = result["nodes"][node]
        nonzero = {
            **{
                f"queue.{key}": value
                for key, value in row.get("queue_deltas", {}).items()
                if value != 0
            },
            **{
                f"telemetry.{key}": value
                for key, value in row.get("telemetry_deltas", {}).items()
                if key in HARD_TELEMETRY and value != 0
            },
        }
        if nonzero:
            lines.append(f"- {node}: `{json.dumps(nonzero, sort_keys=True)}`")
    if not any(
        value != 0
        for row in result["nodes"].values()
        for value in {
            **row.get("queue_deltas", {}),
            **{
                key: value
                for key, value in row.get("telemetry_deltas", {}).items()
                if key in HARD_TELEMETRY
            },
        }.values()
    ):
        lines.append("- none")
    fix = result["fix_readings"]
    lines += [
        "",
        "### relay8.1 fix readings",
        "",
        f"- Δmod16 +1 ≥99.9% on all ten: **{fix['all_delta_mod16_plus1_ge_99_9pct']}**.",
        f"- Listener absolute epoch exact on all ten: **{fix['listener_absolute_epoch_match_100pct']}**.",
        f"- Beacon-window miss approximately zero: **{fix['window_miss_approximately_zero']}**.",
        f"- Slot-10 {fix['slot10_node']} rate: {fmt_float(fix.get('slot10_tag_domain_hz'), 5)} Hz; ≥9 Hz: **{fix['slot10_rate_ge_9hz']}**.",
        "",
        "### Source-audited attribution",
        "",
        "- The beacon tracker extrapolates the next window by one fixed period in the tag's local DW clock (`UWB_Part/relay8_1-workspace/src/include/tag_beacon_sync.h:80-87`) and uses only a −500/+600 µs window (`:11-12`, `:104-127`). A miss advances the same local prediction by one period (`UWB_Part/relay8_1-workspace/src/src/ss_twr_init.c:614-625`); broad reacquisition is not entered until 30 s without a valid beacon (`:773-779`, `:815-833`). The observed roughly 30 s reacquisition cadence is therefore consistent with unmodelled relative DW-clock drift escaping the narrow window.",
        "- relay8.1 services the slot-tail window only after the complete sweep (`UWB_Part/relay8_1-workspace/src/src/ss_twr_init.c:3709-3718`, service at `:837-857`), despite a declared slot-10 tail budget of only 1,400 µs (`UWB_Part/relay8_1-workspace/src/include/tag_beacon_sync.h:13`). The measured slot-10 every-other-epoch output shows that this service point remains too late in the real path; this is an inference from the source ordering plus hardware data, not a direct internal timing trace.",
        "- Runtime configuration resets the tag-owned sweep counter to zero (`UWB_Part/relay8_1-workspace/src/src/ss_twr_init.c:2814-2828`), while the public value is exactly that local counter (`UWB_Part/relay8_1-workspace/src/include/tag_relay6.h:22-29`). B306 classifies a backward jump as reorder and deliberately keeps the old baseline (`B306_Part/firmware/src/main.c:767-792`). Thus the post-CFG reorder increments are a deterministic generation/rebase incompatibility, not host packet loss.",
        "",
        "## First-ten-minute data products",
        "",
        "Absolute position accuracy is out of scope because there is no ground truth. RMS below is scatter about each node's own mean using the standing V4-io geometry.",
        "",
        "![Ten-node position scatter](analysis/positions_3d_first10min.png)",
        "",
        "| BSF | solved/attempted | scatter RMS (mm) |",
        "|---|---:|---:|",
    ]
    for node in NODES:
        row = result["positions"][node]
        rms = row.get("rms_mm")
        lines.append(
            f"| {node} | {row.get('solved', 0)}/{row.get('attempted', 0)} | "
            f"{rms:.2f} |" if isinstance(rms, (int, float)) else
            f"| {node} | 0/{row.get('attempted', 0)} | - |"
        )
    lines += [
        "",
        "Per-node six-axis figures are `analysis/imu_BSFxxxx_first10min.png`; the complete mean/noise table is `analysis/imu_bias_noise_first10min.csv`. Acceleration means include the gravity projection and therefore are not pure sensor bias.",
        "",
        "## Remaining-capacity endurance",
        "",
        "| BSF | data service from W start (h) | data cessation | BLE link (h) | BLE death |",
        "|---|---:|---|---:|---|",
    ]
    for node, row in result["endurance"].items():
        lines.append(
            f"| {node} | {row['alive_s_from_W_start']/3600:.3f} | "
            f"{row['data_plane_cessation_observed']} | "
            f"{row['ble_alive_s_from_W_start']/3600:.3f} | {row['ble_death_observed']} |"
        )
    lines += [
        "",
        "## Integrity and limitations",
        "",
        f"- Decoder errors: {result['decoder_errors']}; malformed: {len(result['malformed'])}; disconnects during W: {len(result['disconnects'])}.",
        f"- DK LED counter deltas: `{json.dumps(result['dk_led_counter_deltas'], sort_keys=True)}`.",
        f"- Sub remained SLAVED: {result['listener_field']['sub_slaved']}; main start-failure fraction: {fmt_float(result['listener_field'].get('main_start_fail_fraction'))}.",
        "- Batteries had already spent hours off-dock before this run. The endurance rows measure remaining capacity only.",
        "- Data-plane cessation while BLE remains connected is classified as an application/data-path stall, not as battery death. The two lifetimes are reported separately.",
        "- `analysis/SHA256SUMS` contains the exact hashes of raw evidence and derived products.",
    ]
    args.report.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--capture-root", required=True, type=Path)
    parser.add_argument("--ota-ledger", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    result = analyze(args)
    build_report(args, result)

    evidence = [
        args.capture_root / "fusion_cdc.log",
        args.capture_root / "continuous_listener_capture/merged_index.jsonl",
        args.capture_root / "OVERNIGHT_RUN_STATE.json",
        args.capture_root / "run_state.json",
        args.capture_root / "snapshots.json",
        args.ota_ledger,
        args.report,
    ] + sorted(path for path in args.output.iterdir() if path.name != "SHA256SUMS")
    with (args.output / "SHA256SUMS").open("w", encoding="utf-8") as handle:
        for path in evidence:
            handle.write(f"{sha256(path)}  {path}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
