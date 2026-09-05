"""Bounded raw-UWB coverage and propagation-availability accounting.

This module deliberately stops before positioning or calibration.  A valid
range bit proves only that a scalar was transported; it is not a LOS label.
"""
from __future__ import annotations

from collections import Counter, defaultdict
import hashlib
from pathlib import Path
from statistics import median
from typing import Any, Iterable, Mapping, Sequence

import numpy as np

from biospur_fusion.ingest.events import RecordType, TypedEvent
from biospur_fusion.ingest.v47 import FrameError, _uwb_event, decode_frame


ANCHOR_COUNT = 8
DIRECT_MINIMUM_LINKS = 4
SUPERFRAME_S = 0.120
CLOCK_P95_LIMIT_US = 500.0
CLOCK_MAXIMUM_LIMIT_US = 1000.0


def decode_bounded_uwb(
    raw_path: Path,
    start_byte: int,
    stop_byte: int,
    *,
    expected_slice_sha256: str,
) -> tuple[tuple[TypedEvent, ...], dict[str, Any]]:
    """Decode UWB records from one exact, complete-frame half-open byte slice."""

    raw_path = Path(raw_path).resolve()
    size = raw_path.stat().st_size
    if not 0 <= start_byte < stop_byte <= size:
        raise ValueError("invalid bounded UWB byte interval")
    with raw_path.open("rb") as stream:
        stream.seek(start_byte)
        payload = stream.read(stop_byte - start_byte)
    if len(payload) != stop_byte - start_byte:
        raise RuntimeError("short bounded UWB read")
    observed_sha256 = hashlib.sha256(payload).hexdigest()
    if observed_sha256 != expected_slice_sha256:
        raise ValueError("bounded UWB slice SHA-256 mismatch")
    if payload and payload[-1] != 0:
        raise ValueError("bounded UWB slice does not end at a complete COBS record")

    events: list[TypedEvent] = []
    complete_records = decode_errors = non_uwb_records = 0
    cursor = 0
    for encoded in payload.split(b"\0"):
        if not encoded:
            cursor += 1
            continue
        complete_records += 1
        raw_start = start_byte + cursor
        raw_end = raw_start + len(encoded) + 1
        cursor += len(encoded) + 1
        try:
            frame = decode_frame(encoded)
            if frame.kind != 1:
                non_uwb_records += 1
                continue
            events.append(_uwb_event(
                frame,
                0,
                (complete_records, raw_start, raw_end, encoded),
            ))
        except (FrameError, IndexError, ValueError):
            decode_errors += 1
    if cursor != len(payload) + 1:
        # split() emits a final empty member for the terminating delimiter.
        raise RuntimeError("bounded UWB byte accounting mismatch")
    return tuple(events), {
        "schema": "biospur.c2.uwb_calibration.bounded_decode.v1",
        "raw_path": str(raw_path),
        "start_byte_inclusive": int(start_byte),
        "stop_byte_exclusive": int(stop_byte),
        "bytes_read": len(payload),
        "slice_sha256": observed_sha256,
        "complete_records": complete_records,
        "non_uwb_records": non_uwb_records,
        "uwb_records": len(events),
        "decode_errors": decode_errors,
        "whole_container_scan": False,
    }


def _valid_links(event: TypedEvent) -> list[dict[str, Any]]:
    if event.record_type is not RecordType.UWB:
        raise ValueError("coverage input is not UWB")
    payload = event.payload
    anchor_ids = list(payload["anchor_id"])
    if len(anchor_ids) != ANCHOR_COUNT or sorted(anchor_ids) != list(range(ANCHOR_COUNT)):
        raise ValueError("UWB anchor IDs are not the A--H bijection")
    if len(set(anchor_ids)) != ANCHOR_COUNT:
        raise ValueError("duplicate UWB anchor ID")
    output = []
    for slot, anchor in enumerate(anchor_ids):
        value = int(payload["range_mm"][slot])
        t_round = int(payload["t_round_us"][slot])
        valid = bool(int(payload["valid_mask"]) & (1 << slot))
        if valid and 0 < value < 0xFFFF and t_round > 0:
            output.append({
                "anchor": int(anchor),
                "range_m": value / 1000.0,
                "t_round_us": t_round,
                "quality_percent": int(payload["quality_percent"][slot]),
            })
    return output


def _fraction(numerator: int, denominator: int) -> float | None:
    return float(numerator / denominator) if denominator else None


def _distribution(values: Sequence[float]) -> dict[str, float | int | None]:
    if not values:
        return {"count": 0, "minimum": None, "median": None, "maximum": None}
    array = np.asarray(values, dtype=float)
    return {
        "count": int(len(array)),
        "minimum": float(np.min(array)),
        "median": float(np.median(array)),
        "maximum": float(np.max(array)),
    }


def summarize_clock_health(
    clock_models: Mapping[str, Mapping[str, float]],
) -> dict[str, Any]:
    """Apply the explicit Gate-0 residual limits to every node model."""

    if not clock_models:
        raise ValueError("clock health requires at least one model")
    nodes = {}
    for node, model in sorted(clock_models.items()):
        p95 = float(model["clean_residual_p95_us"])
        maximum = float(model["clean_residual_max_us"])
        strict = p95 < CLOCK_P95_LIMIT_US and maximum < CLOCK_MAXIMUM_LIMIT_US
        nodes[str(node)] = {
            "strict_residual_gate": strict,
            "clean_residual_p95_us": p95,
            "clean_residual_max_us": maximum,
            "sigma_ns": float(model["sigma_ns"]),
            "clean_pairs": int(model["clean_pairs"]),
            "capture_span_coverage": float(model["capture_span_coverage"]),
            "max_clean_anchor_gap_s": float(model["max_clean_anchor_gap_s"]),
        }
    degraded = [node for node, row in nodes.items() if not row["strict_residual_gate"]]
    return {
        "strict_residual_gate": not degraded,
        "p95_limit_us": CLOCK_P95_LIMIT_US,
        "maximum_limit_us": CLOCK_MAXIMUM_LIMIT_US,
        "degraded_nodes": degraded,
        "nodes": nodes,
    }


def summarize_beacon_clock_health(
    clock_document: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate the Beacon-only clock contract and summarize every node.

    Historical Capture2 stores the full Beacon counter in archived LBD rows,
    while node transport carries the modulo-16 epoch.  LBD may resolve that
    discrete integer, but Listener poll/response observations are forbidden.
    Continuous UWB and IMU measurement time remains B306 TIMER2.
    """

    if clock_document.get("schema") != "biospur.c2.beacon_only_clock_table.v1":
        raise ValueError("clock input is not a Beacon-only clock table")
    contract = clock_document.get("clock_contract", {})
    required_timers = {
        "uwb.strobe_us", "uwb.frame_us", "imu.base_us", "imu.trigger_us",
    }
    gates = {
        "clock_contract_pass": contract.get("pass") is True,
        "lbd_only": contract.get("accepted_listener_record_kinds") == ["LBD"],
        "lpd_lrd_forbidden": set(contract.get("forbidden_listener_record_kinds", ()))
        == {"LPD", "LRD"},
        "poll_response_not_consumed": (
            contract.get("poll_or_response_measurements_consumed") is False
        ),
        "b306_timer2_measurement_time": (
            contract.get("measurement_time_source") == "B306_TIMER2"
        ),
        "same_clock_for_uwb_and_imu": (
            set(contract.get("same_model_applies_to", ())) == required_timers
        ),
    }
    if not all(gates.values()):
        failed = [name for name, passed in gates.items() if not passed]
        raise ValueError(f"Beacon-only clock contract failed: {failed}")

    models = clock_document.get("models", {})
    if len(models) != 10:
        raise ValueError("Beacon-only Capture2 clock requires ten node models")
    nodes = {}
    for node, model in sorted(models.items()):
        p95 = float(model["residual_p95_us"])
        maximum = float(model["residual_max_us"])
        strict = (
            p95 < CLOCK_P95_LIMIT_US
            and maximum < CLOCK_MAXIMUM_LIMIT_US
            and float(model["sf_mod16_agreement_fraction"]) == 1.0
            and int(model["timestamp_reversals"]) == 0
        )
        nodes[str(node)] = {
            "strict_residual_gate": strict,
            "residual_p95_us": p95,
            "residual_max_us": maximum,
            "sigma_ns": float(model["sigma_ns"]),
            "drift_ppm": float(model["drift_ppm"]),
            "sf_mod16_agreement_fraction": float(
                model["sf_mod16_agreement_fraction"]
            ),
            "timestamp_reversals": int(model["timestamp_reversals"]),
        }
    degraded = [node for node, row in nodes.items() if not row["strict_residual_gate"]]
    return {
        "strict_residual_gate": not degraded,
        "p95_limit_us": CLOCK_P95_LIMIT_US,
        "maximum_limit_us": CLOCK_MAXIMUM_LIMIT_US,
        "degraded_nodes": degraded,
        "contract_gates": gates,
        "nodes": nodes,
    }


def summarize_episode_coverage(
    events: Iterable[TypedEvent],
    *,
    expected_nodes: Sequence[str],
    clock_models: Mapping[str, Mapping[str, float]],
    episode_start_global_ns: int,
    episode_stop_global_ns_exclusive: int,
    superframe_s: float = SUPERFRAME_S,
) -> dict[str, Any]:
    """Summarize direct UWB availability and kinematic-propagation demand."""

    expected = tuple(sorted(str(node) for node in expected_nodes))
    if len(expected) != 10 or len(set(expected)) != 10:
        raise ValueError("Capture2 coverage requires ten unique body nodes")
    if set(clock_models) != set(expected):
        raise ValueError("clock-model node set differs from body-node set")
    duration_ns = episode_stop_global_ns_exclusive - episode_start_global_ns
    duration_s = duration_ns * 1e-9
    if duration_s <= 0.0:
        raise ValueError("non-positive episode duration")
    if superframe_s <= 0.0:
        raise ValueError("non-positive superframe")

    by_node: dict[str, list[list[dict[str, Any]]]] = {node: [] for node in expected}
    link_epoch_ns: list[int] = []
    bins: dict[int, dict[str, int]] = defaultdict(dict)
    outside_window = 0
    superframe_ns = int(round(superframe_s * 1e9))
    ordered_events = sorted(events, key=lambda event: (
        event.node_id, int(event.node_timer_us or 0), event.sequence,
    ))
    for event in ordered_events:
        if event.node_id not in by_node:
            raise ValueError(f"unsealed UWB node {event.node_id}")
        links = _valid_links(event)
        model = clock_models[event.node_id]
        a = float(model["a_ns_per_us"])
        b = float(model["b_ns"])
        epochs = [
            int(round(a * (int(event.payload["strobe_us"]) + 0.5 * row["t_round_us"]) + b))
            for row in links
        ]
        if epochs:
            event_epoch = int(round(median(epochs)))
        else:
            event_epoch = int(round(a * int(event.payload["strobe_us"]) + b))
        if not episode_start_global_ns <= event_epoch < episode_stop_global_ns_exclusive:
            outside_window += 1
            continue
        by_node[event.node_id].append(links)
        link_epoch_ns.extend(epochs)
        bin_index = int((event_epoch - episode_start_global_ns) // superframe_ns)
        bins[bin_index][event.node_id] = max(
            len(links), bins[bin_index].get(event.node_id, 0)
        )

    node_rows: dict[str, Any] = {}
    total_events = total_links = 0
    for node in expected:
        updates = by_node[node]
        counts = [len(row) for row in updates]
        total_events += len(updates)
        total_links += sum(counts)
        anchors: dict[str, Any] = {}
        for anchor in range(ANCHOR_COUNT):
            rows = [
                link for update in updates for link in update
                if link["anchor"] == anchor
            ]
            anchors[chr(ord("A") + anchor)] = {
                "valid_links": len(rows),
                "valid_fraction_of_updates": _fraction(len(rows), len(updates)),
                "range_m": _distribution([row["range_m"] for row in rows]),
                "quality_percent": _distribution([
                    row["quality_percent"] for row in rows
                ]),
                "t_round_us": _distribution([row["t_round_us"] for row in rows]),
            }
        histogram = Counter(counts)
        node_rows[node] = {
            "uwb_updates": len(updates),
            "observed_rate_hz": len(updates) / duration_s,
            "valid_links": sum(counts),
            "valid_link_count_histogram": {
                str(count): int(histogram.get(count, 0))
                for count in range(ANCHOR_COUNT + 1)
            },
            "fraction_updates_with_8_links": _fraction(counts.count(8), len(counts)),
            "fraction_updates_with_at_least_7_links": _fraction(
                sum(count >= 7 for count in counts), len(counts)
            ),
            "fraction_direct_3d_candidate_updates": _fraction(
                sum(count >= DIRECT_MINIMUM_LINKS for count in counts), len(counts)
            ),
            "fraction_partial_or_missing_updates": _fraction(
                sum(count < DIRECT_MINIMUM_LINKS for count in counts), len(counts)
            ),
            "anchors": anchors,
        }

    expected_bin_count = int((duration_ns + superframe_ns - 1) // superframe_ns)
    direct_nodes_per_bin = []
    partial_nodes_per_bin = []
    for bin_index in range(expected_bin_count):
        row = bins.get(bin_index, {})
        direct_nodes_per_bin.append(sum(
            count >= DIRECT_MINIMUM_LINKS for count in row.values()
        ))
        partial_nodes_per_bin.append(sum(
            0 < count < DIRECT_MINIMUM_LINKS for count in row.values()
        ))
    direct_histogram = Counter(direct_nodes_per_bin)
    return {
        "schema": "biospur.c2.uwb_calibration.episode_coverage.v1",
        "semantic_limit": (
            "VALID_MASK_COVERAGE_ONLY;NOT_LOS_CLASSIFICATION;NOT_POSITION_ACCURACY"
        ),
        "duration_s": duration_s,
        "uwb_updates": total_events,
        "valid_links": total_links,
        "events_outside_episode_window": outside_window,
        "nodes": node_rows,
        "link_epoch_global_ns": _distribution(link_epoch_ns),
        "propagation_availability": {
            "superframe_s": superframe_s,
            "expected_bins": expected_bin_count,
            "fraction_bins_with_any_direct_node": _fraction(
                sum(value >= 1 for value in direct_nodes_per_bin), expected_bin_count
            ),
            "fraction_bins_with_at_least_5_direct_nodes": _fraction(
                sum(value >= 5 for value in direct_nodes_per_bin), expected_bin_count
            ),
            "fraction_bins_with_all_10_direct_nodes": _fraction(
                sum(value == 10 for value in direct_nodes_per_bin), expected_bin_count
            ),
            "direct_nodes_per_bin": _distribution(direct_nodes_per_bin),
            "partial_nodes_per_bin": _distribution(partial_nodes_per_bin),
            "direct_node_count_histogram": {
                str(count): int(direct_histogram.get(count, 0))
                for count in range(11)
            },
        },
    }
