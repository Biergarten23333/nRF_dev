"""Materialize an exact binary measurement ledger on the strict common clock."""
from __future__ import annotations

import json
import struct
import sys
from collections import Counter
from pathlib import Path
from typing import Mapping

import numpy as np

from biospur_fusion.time.common_clock import ClockModel

_REPO = Path(__file__).resolve().parents[4]
_TOOLS = _REPO / "B306_Part" / "tools"
if str(_TOOLS) not in sys.path:
    sys.path.insert(0, str(_TOOLS))
from fusion_host_binary import FrameError  # noqa: E402
from v47_real_data_adapter import (  # noqa: E402
    IMU_DTYPE as TRANSPORT_IMU_DTYPE, NODES, UWB_DTYPE as TRANSPORT_UWB_DTYPE,
    _decode_host_frame, _decode_imu, _decode_uwb, iter_cobs_records,
)

STATUS_OUTSIDE_WINDOW = 0
STATUS_ACCEPTED = 1
STATUS_OUTSIDE_CLOCK_SEGMENT = 2

IMU_LEDGER_DTYPE = np.dtype([
    ("boot_epoch", "<u2"), ("sequence", "<u2"), ("node_timer_us", "<u8"),
    ("global_time_ns", "<i8"), ("global_time_sigma_ns", "<u8"),
    ("master_arrival_ms", "<u8"), ("base_timer2_us", "<u8"), ("delta_us", "<u2"),
    ("acc_raw", "<i2", (3,)), ("gyro_raw", "<i2", (3,)), ("temp_raw", "<i2"),
    ("raw_record_index", "<u8"), ("raw_start_offset", "<u8"), ("raw_end_offset", "<u8"),
    ("raw_sample_index", "u1"), ("status", "u1"),
])
UWB_LEDGER_DTYPE = np.dtype([
    ("boot_epoch", "<u2"), ("sequence", "<u4"), ("node_timer_us", "<u8"),
    ("global_time_ns", "<i8"), ("global_time_sigma_ns", "<u8"),
    ("master_arrival_ms", "<u8"), ("node_ms", "<u4"), ("packet_sequence", "<u4"),
    ("sweep", "<u4"), ("poll_tx_dw40", "<u8"), ("identity", "<u2"),
    ("anchor_id", "u1", (8,)), ("rank", "u1", (8,)), ("range_mm", "<u2", (8,)),
    ("t_round_us", "<u2", (8,)), ("quality_percent", "u1", (8,)),
    ("cfo_ppm_q8", "<i2", (8,)), ("valid_mask", "u1"), ("flags", "u1"),
    ("frame_us", "<u8"), ("strobe_us", "<u8"),
    ("raw_record_index", "<u8"), ("raw_start_offset", "<u8"), ("raw_end_offset", "<u8"),
    ("status", "u1"),
])


def _global_bounds(bridge: Mapping[str, float], start_host_s: float, end_host_s: float) -> tuple[int, int]:
    slope = float(bridge["listener_global_us_per_host_s"])
    intercept = float(bridge["listener_global_us_intercept"])
    return int(round((slope * start_host_s + intercept) * 1000.0)), int(round((slope * end_host_s + intercept) * 1000.0))


def detect_boot_epochs(raw: Path) -> tuple[dict[str, int], dict]:
    """Require corroborated TIMER2 reversals in both local measurement streams."""
    last: dict[tuple[str, str], int] = {}; transitions: Counter[tuple[str, str]] = Counter()
    for _, encoded in iter_cobs_records(raw):
        try:
            frame = _decode_host_frame(encoded)
        except FrameError:
            continue
        if frame.node_name not in NODES or frame.kind not in (1, 3):
            continue
        source = "UWB" if frame.kind == 1 else "IMU"
        try:
            timer = struct.unpack_from("<Q", frame.payload, 102 if frame.kind == 1 else 4)[0]
        except struct.error:
            continue
        key = (frame.node_name, source); previous = last.get(key)
        if previous is not None and timer < previous:
            transitions[key] += 1
        last[key] = int(timer)
    formal_boot = {}; audit = {}
    for node in NODES:
        imu = transitions[(node, "IMU")]; uwb = transitions[(node, "UWB")]
        corroborated = imu == uwb
        formal_boot[node] = imu if corroborated else -1
        audit[node] = {"imu_timer_reversals": imu, "uwb_timer_reversals": uwb,
                       "corroborated": corroborated, "formal_boot_epoch": formal_boot[node]}
    if any(value < 0 for value in formal_boot.values()):
        raise RuntimeError(f"uncorroborated boot segmentation: {audit}")
    return formal_boot, audit


def build_time_event_ledger(raw: Path, models: Mapping[str, ClockModel], bridge: Mapping[str, float],
                            formal_start_host_s: float, formal_end_host_s: float,
                            output_npz: Path) -> dict:
    counts = {node: [0, 0] for node in NODES}; frame_kinds = Counter(); errors = []
    complete = 0; incomplete_tail = 0
    for end, encoded in iter_cobs_records(raw):
        try:
            frame = _decode_host_frame(encoded)
        except FrameError as error:
            if end == raw.stat().st_size:
                incomplete_tail = len(encoded)
            else:
                errors.append({"raw_end_offset": end, "error": str(error)})
            continue
        complete += 1; frame_kinds[str(frame.kind)] += 1
        if frame.node_name not in counts:
            continue
        if frame.kind == 3 and len(frame.payload) >= 2:
            counts[frame.node_name][0] += int(frame.payload[1])
        elif frame.kind == 1:
            counts[frame.node_name][1] += 1

    imu = {n: np.empty(counts[n][0], IMU_LEDGER_DTYPE) for n in NODES}
    uwb = {n: np.empty(counts[n][1], UWB_LEDGER_DTYPE) for n in NODES}
    ip = Counter(); up = Counter(); record_index = 0
    stream_boot: Counter[tuple[str, str]] = Counter(); last_timer: dict[tuple[str, str], int] = {}
    start_ns, end_ns = _global_bounds(bridge, formal_start_host_s, formal_end_host_s)

    for end, encoded in iter_cobs_records(raw):
        record_index += 1
        start = end - len(encoded) - 1
        try:
            frame = _decode_host_frame(encoded)
        except FrameError:
            continue
        node = frame.node_name
        if node not in models:
            continue
        model = models[node]
        if frame.kind == 3:
            temp = np.empty(max(1, int(frame.payload[1]) if len(frame.payload) > 1 else 1), TRANSPORT_IMU_DTYPE)
            try:
                size = _decode_imu(frame, temp, 0)
            except FrameError:
                continue
            for sample_index, row in enumerate(temp[:size]):
                timer = int(row["b306_us"]); key = (node, "IMU"); previous = last_timer.get(key)
                if previous is not None and timer < previous:
                    stream_boot[key] += 1
                last_timer[key] = timer
                event_boot = stream_boot[key]
                global_ns = model.map_ns(timer) if event_boot == model.boot_epoch else -1
                status = STATUS_OUTSIDE_CLOCK_SEGMENT if global_ns < 0 else (
                    STATUS_ACCEPTED if start_ns <= global_ns <= end_ns else STATUS_OUTSIDE_WINDOW)
                base = timer - int(row["delta_us"])
                imu[node][ip[node]] = (
                    event_boot, int(row["seq"]), timer, global_ns, int(round(model.sigma_ns)),
                    int(row["master_ms"]), base, int(row["delta_us"]), row["acc"], row["gyro"],
                    int(row["temp_raw"]), record_index, start, end, sample_index, status,
                )
                ip[node] += 1
        elif frame.kind == 1:
            temp = np.empty(1, TRANSPORT_UWB_DTYPE)
            try:
                _decode_uwb(frame, temp, 0)
            except FrameError:
                continue
            row = temp[0]; timer = int(row["strobe_us"]); key = (node, "UWB"); previous = last_timer.get(key)
            if previous is not None and timer < previous:
                stream_boot[key] += 1
            last_timer[key] = timer
            event_boot = stream_boot[key]
            global_ns = model.map_ns(timer) if event_boot == model.boot_epoch else -1
            status = STATUS_OUTSIDE_CLOCK_SEGMENT if global_ns < 0 else (
                STATUS_ACCEPTED if start_ns <= global_ns <= end_ns else STATUS_OUTSIDE_WINDOW)
            uwb[node][up[node]] = (
                event_boot, int(row["sweep"]), timer, global_ns, int(round(model.sigma_ns)),
                int(row["master_ms"]), int(row["node_ms"]), int(row["packet_seq"]), int(row["sweep"]),
                int(row["poll_tx"]), int(row["identity"]), row["anchor_id"], row["rank"], row["range_mm"],
                row["t_round_us"], row["quality"], row["cfo_ppm_q8"], int(row["valid_mask"]),
                int(row["flags"]), int(row["frame_us"]), timer, record_index, start, end, status,
            )
            up[node] += 1

    if any(ip[n] != len(imu[n]) or up[n] != len(uwb[n]) for n in NODES):
        raise RuntimeError("typed ledger allocation did not close")
    arrays = {}
    for node in NODES:
        arrays[f"imu_{node}"] = imu[node]; arrays[f"uwb_{node}"] = uwb[node]
    np.savez(output_npz, **arrays)
    categories = Counter()
    per_node = {}
    for node in NODES:
        node_counts = {}
        for label, code in (("outside-window", STATUS_OUTSIDE_WINDOW), ("accepted", STATUS_ACCEPTED),
                            ("outside-clock-segment", STATUS_OUTSIDE_CLOCK_SEGMENT)):
            value = int(np.sum(imu[node]["status"] == code) + np.sum(uwb[node]["status"] == code))
            node_counts[label] = value; categories[label] += value
        node_counts.update({"imu": len(imu[node]), "uwb": len(uwb[node]),
                            "imu_boot_transitions": stream_boot[(node, "IMU")],
                            "uwb_boot_transitions": stream_boot[(node, "UWB")]})
        per_node[node] = node_counts
    categories["invalid"] = len(errors)
    return {
        "schema": "biospur-time-event-ledger-v1", "ledger_path": str(output_npz.resolve()),
        "complete_transport_records": complete, "transport_frame_kinds": dict(sorted(frame_kinds.items())),
        "incomplete_tail_bytes": incomplete_tail, "decode_errors": errors,
        "measurement_accounting": dict(categories), "per_node": per_node,
        "formal_global_start_ns": start_ns, "formal_global_end_ns": end_ns,
        "master_arrival_semantics": "diagnostic only; not used for global_time_ns",
        "raw_byte_provenance": "every emitted measurement carries record index and encoded byte start/end",
        "exact": sum(categories.values()) == sum(len(imu[n]) + len(uwb[n]) for n in NODES) + len(errors),
    }
