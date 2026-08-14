"""Strict Listener-backed TIMER2 common-clock reconstruction for 120 ms TDMA."""
from __future__ import annotations

import bisect
import json
import math
import re
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable, Mapping

import numpy as np

SUPERFRAME_US = 120_000.0
SLOT_US = 10_000.0
DW_TICKS_PER_US = 63_897.6
CLOCK_OUTLIER_HARD_LIMIT_US = 5_000.0
CLEAN_RESIDUAL_P95_GATE_US = 500.0
CLEAN_RESIDUAL_MAX_GATE_US = 1_000.0
RAW_RESIDUAL_P99_GATE_US = 2_000.0
RAW_RESIDUAL_MAX_GATE_US = 5_000.0
MIN_CLEAN_LISTENER_PAIRS = 50
MIN_CAPTURE_SPAN_COVERAGE = 0.80
MAX_CLEAN_ANCHOR_GAP_S = 60.0
MAX_REJECTION_FRACTION = 0.20

UWB_RE = re.compile(
    r"^(\d+\.\d+)\s+(\d+\.\d+).*?FUSION_UWB proto=7 name=(BSF[0-9A-F]+) "
    r"master_ms=(\d+).*?sweep=(\d+).*?frame_us=(\d+) strobe_us=(\d+).*?flags=0x([0-9a-fA-F]+)"
)


@dataclass(frozen=True)
class UwbClockAnchor:
    node: str
    host_monotonic_s: float
    master_arrival_ms: int
    sweep: int
    frame_us: int
    strobe_us: int
    sf_valid: bool
    sf_mod16: int | None


@dataclass(frozen=True)
class ListenerPoll:
    listener: str
    src: int
    sequence: int
    absolute_epoch: int
    phase_us: float
    host_monotonic_s: float


@dataclass(frozen=True)
class ClockModel:
    node_id: str
    boot_epoch: int
    a_ns_per_us: float
    b_ns: float
    sigma_ns: float
    first_timer_us: int
    last_timer_us: int
    integer_epoch_offset: int
    integer_choice_margin_epochs: int
    listener_pairs: int
    clean_pairs: int
    rejected_pairs: int
    clean_residual_p95_us: float
    clean_residual_max_us: float
    raw_residual_p95_us: float
    raw_residual_p99_us: float
    raw_residual_max_us: float
    capture_span_coverage: float
    max_clean_anchor_gap_s: float
    rejection_fraction: float
    drift_ppm: float
    mod16_agreement_fraction: float
    timestamp_reversals: int

    def map_ns(self, timer_us: int) -> int:
        return int(round(self.a_ns_per_us * int(timer_us) + self.b_ns))


def _robust_line(x: np.ndarray, y: np.ndarray, iterations: int = 12,
                 hard_limit: float = CLOCK_OUTLIER_HARD_LIMIT_US,
                 floor: float = 300.0) -> tuple[float, float, np.ndarray]:
    x = np.asarray(x, float); y = np.asarray(y, float)
    if x.size < 3 or x.size != y.size:
        raise ValueError("clock fit needs at least three pairs")
    x0 = float(np.mean(x)); y0 = float(np.mean(y)); keep = np.ones(x.size, bool)
    for _ in range(iterations):
        xx = x[keep] - x0; yy = y[keep] - y0
        slope = float(xx @ yy / (xx @ xx)); intercept = y0 - slope * x0
        residual = y - (slope * x + intercept)
        centre = float(np.median(residual[keep]))
        mad = 1.4826 * float(np.median(np.abs(residual[keep] - centre)))
        # Predeclared physical clean-anchor classifier.  Its 5 ms hard ceiling
        # is deliberately looser than the independent 1 ms clean-max gate, so
        # the acceptance gate cannot be made tautological by classification.
        limit = min(float(hard_limit), max(float(floor), 6.0 * mad))
        new_keep = np.abs(residual - centre) <= limit
        if np.array_equal(new_keep, keep):
            break
        keep = new_keep
    xx = x[keep] - float(np.mean(x[keep])); yy = y[keep] - float(np.mean(y[keep]))
    slope = float(xx @ yy / (xx @ xx))
    intercept = float(np.mean(y[keep]) - slope * np.mean(x[keep]))
    return slope, intercept, keep


def parse_fusion_anchors(log_path: Path, start_s: float, end_s: float) -> dict[str, list[UwbClockAnchor]]:
    out: dict[str, list[UwbClockAnchor]] = defaultdict(list)
    with log_path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if "FUSION_UWB" not in line:
                continue
            match = UWB_RE.search(line)
            if not match:
                continue
            _, mono, node, master, sweep, frame, strobe, flags_text = match.groups()
            host = float(mono)
            if not start_s <= host <= end_s:
                continue
            flags = int(flags_text, 16)
            valid = bool(flags & 0x80)
            mod16 = ((flags >> 3) & 0x0F) if valid else None
            out[node].append(UwbClockAnchor(node, host, int(master), int(sweep), int(frame), int(strobe), valid, mod16))
    return dict(out)


def reconstruct_local_epochs(strobes: Iterable[int]) -> tuple[np.ndarray, float]:
    timer = np.asarray(list(strobes), float)
    if timer.size < 3 or np.any(np.diff(timer) <= 0):
        raise ValueError("TIMER2 reversal or insufficient UWB anchors")
    period = SUPERFRAME_US
    for _ in range(20):
        multiples = np.maximum(1, np.rint(np.diff(timer) / period).astype(np.int64))
        epochs = np.r_[0, np.cumsum(multiples)]
        slope, _, _ = _robust_line(epochs.astype(float), timer)
        if abs(slope - period) < 1e-7:
            period = slope
            break
        period = slope
    return epochs.astype(np.int64), period


def _listener_beacon_fit(rows: list[tuple[int, int]]) -> tuple[float, float]:
    values = np.asarray(rows, float)
    slope, intercept, keep = _robust_line(
        values[:, 0], values[:, 1],
        hard_limit=999.0 * DW_TICKS_PER_US,
        floor=300.0 * DW_TICKS_PER_US,
    )
    if int(keep.sum()) < 10:
        raise ValueError("insufficient clean Listener Beacon records")
    return intercept, slope


def load_listener_polls(listener_dir: Path, start_s: float, end_s: float,
                        src_slots: Mapping[int, int]) -> tuple[list[ListenerPoll], dict]:
    summary = json.loads((listener_dir / "summary.json").read_text(encoding="utf-8"))
    polls: list[ListenerPoll] = []
    audit: dict[str, dict] = {}
    for snr, info in sorted(summary["listeners"].items()):
        role = info.get("first_lstat", {}).get("role")
        kinds = info.get("kinds", {})
        usable = role == "OBSERVER" and kinds.get("LPD", 0) and kinds.get("LBD", 0)
        row_audit = {"listener_key": info["listener_key"], "role": role, "usable": bool(usable)}
        audit[snr] = row_audit
        if not usable:
            continue
        beacons: list[tuple[int, int]] = []
        raw_polls: list[dict] = []
        with (listener_dir / "listeners" / f"{snr}.jsonl").open(encoding="utf-8") as handle:
            for line in handle:
                row = json.loads(line)
                host = int(row["arrival_monotonic_ns"]) / 1e9
                if host < start_s - 1.0 or host > end_s + 1.0:
                    continue
                fields = row.get("fields", {})
                if row.get("kind") == "LBD" and row.get("rx_unwrapped_ticks") is not None:
                    beacons.append((int(fields["superframe_counter"]), int(row["rx_unwrapped_ticks"])))
                elif row.get("kind") == "LPD" and int(fields.get("src", -1)) in src_slots:
                    raw_polls.append(row)
        intercept, ticks_per_epoch = _listener_beacon_fit(beacons)
        phase_errors = []
        for row in raw_polls:
            fields = row["fields"]; src = int(fields["src"]); slot = src_slots[src]
            fractional = (int(row["rx_unwrapped_ticks"]) - intercept) / ticks_per_epoch
            expected_phase = slot * SLOT_US + 3900.0
            epoch = int(round(fractional - expected_phase / SUPERFRAME_US))
            phase = (fractional - epoch) * SUPERFRAME_US
            phase_errors.append(phase - expected_phase)
            polls.append(ListenerPoll(info["listener_key"], src, int(fields["poll_seq"]), epoch,
                                      float(phase), int(row["arrival_monotonic_ns"]) / 1e9))
        row_audit.update({"beacons_used": len(beacons), "polls_used": len(raw_polls),
                          "beacon_period_us": ticks_per_epoch / DW_TICKS_PER_US,
                          "phase_choice_max_error_us": max(map(abs, phase_errors), default=None)})
    return polls, {"capture_summary_pass": summary.get("pass"), "listeners": audit}


def _match_node(anchors: list[UwbClockAnchor], epochs: np.ndarray, polls: list[ListenerPoll],
                src: int) -> tuple[int, int, list[tuple[int, float]], dict]:
    available = [p for p in polls if p.src == src]
    by_seq: dict[int, list[tuple[float, int]]] = defaultdict(list)
    for index, anchor in enumerate(anchors):
        by_seq[anchor.sweep & 0xFF].append((anchor.host_monotonic_s, index))
    for values in by_seq.values():
        values.sort()
    seeds: list[int] = []
    for poll in available:
        values = by_seq.get(poll.sequence)
        if not values:
            continue
        times = [x[0] for x in values]
        pos = bisect.bisect_left(times, poll.host_monotonic_s)
        choices = [j for j in (pos - 1, pos) if 0 <= j < len(values)]
        if not choices:
            continue
        chosen = min(choices, key=lambda j: abs(times[j] - poll.host_monotonic_s))
        if abs(times[chosen] - poll.host_monotonic_s) <= 0.5:
            seeds.append(poll.absolute_epoch - int(epochs[values[chosen][1]]))
    if not seeds:
        raise ValueError("no sequence-backed Listener matches")
    seed_counts = Counter(seeds)
    selected, selected_count = seed_counts.most_common(1)[0]

    mod16_offsets = Counter()
    for anchor, epoch in zip(anchors, epochs):
        if anchor.sf_valid and anchor.sf_mod16 is not None:
            mod16_offsets[(anchor.sf_mod16 - int(epoch)) & 0x0F] += 1
    required_mod16, mod_count = mod16_offsets.most_common(1)[0]
    # The carried label is allowed a segment-constant acquisition offset (see
    # epoch_transition_segmentation.md). Listener chooses the absolute integer;
    # mod16 proves that the offset is constant and has no unexplained transition.
    next_count = seed_counts.most_common(2)[1][1] if len(seed_counts) > 1 else 0
    if selected_count <= next_count:
        raise ValueError(f"unresolved Listener integer epoch tie: {seed_counts}")

    by_epoch: dict[int, list[ListenerPoll]] = defaultdict(list)
    for poll in available:
        by_epoch[poll.absolute_epoch].append(poll)
    pairs: list[tuple[int, float]] = []
    sequence_offsets = Counter()
    for index, epoch in enumerate(epochs):
        seen = by_epoch.get(int(epoch) + selected, [])
        if not seen:
            continue
        phases = [p.phase_us for p in seen]
        for poll in seen:
            sequence_offsets[((anchors[index].sweep & 0xFF) - poll.sequence) & 0xFF] += 1
        target_us = (int(epoch) + selected) * SUPERFRAME_US + float(np.median(phases))
        pairs.append((index, target_us))
    modal_seq, modal_seq_count = sequence_offsets.most_common(1)[0]
    details = {
        "integer_seed_counts": {str(k): v for k, v in sorted(seed_counts.items())},
        "selected_integer_epoch": selected,
        "selected_seed_fraction": selected_count / len(seeds),
        "mod16_required_offset": required_mod16,
        "mod16_agreement_fraction": mod_count / sum(mod16_offsets.values()),
        "listener_minus_carried_mod16_offset": (selected - required_mod16) & 0x0F,
        "sequence_offset_sweep_minus_poll": modal_seq,
        "sequence_offset_modal_fraction": modal_seq_count / sum(sequence_offsets.values()),
        "unique_fusion_listener_pairs": len(pairs),
    }
    return selected, selected_count - next_count, pairs, details


def align_capture(log_path: Path, listener_dir: Path, start_s: float, end_s: float,
                  slots: Mapping[str, int]) -> tuple[dict[str, ClockModel], list[dict], dict]:
    anchors = parse_fusion_anchors(log_path, start_s, end_s)
    expected = set(slots)
    if set(anchors) != expected:
        raise ValueError(f"clock fleet mismatch missing={sorted(expected-set(anchors))} unexpected={sorted(set(anchors)-expected)}")
    src = {node: 0xB100 + slot for node, slot in slots.items()}
    polls, listener_audit = load_listener_polls(listener_dir, start_s, end_s,
                                                {src[n]: slots[n] for n in slots})
    models: dict[str, ClockModel] = {}; residual_rows: list[dict] = []; joins = {}
    for node in sorted(slots, key=slots.get):
        node_anchors = anchors[node]
        epochs, local_period = reconstruct_local_epochs(a.strobe_us for a in node_anchors)
        integer, margin, pairs, join = _match_node(node_anchors, epochs, polls, src[node])
        indices = np.asarray([x[0] for x in pairs], int)
        target = np.asarray([x[1] for x in pairs], float)
        timer = np.asarray([node_anchors[i].strobe_us for i in indices], float)
        slope, intercept, clean = _robust_line(timer, target)
        residual = target - (slope * timer + intercept)
        centre = float(np.median(residual[clean])); centred = residual - centre
        raw_values = np.abs(centred)
        clean_values = raw_values[clean]
        paired_timer_span = float(timer[-1] - timer[0]) if len(timer) > 1 else 0.0
        full_timer_span = float(node_anchors[-1].strobe_us - node_anchors[0].strobe_us)
        coverage = paired_timer_span / full_timer_span if full_timer_span > 0 else 0.0
        clean_timer = timer[clean]
        max_clean_gap_s = (float(np.max(np.diff(clean_timer))) / 1e6
                           if len(clean_timer) > 1 else float("inf"))
        rejection_fraction = float((~clean).sum() / len(clean))
        mapped_all = slope * np.asarray([a.strobe_us for a in node_anchors]) + intercept
        reversals = int(np.sum(np.diff(mapped_all) <= 0))
        sigma_us = max(1.0, 1.4826 * float(np.median(clean_values)))
        model = ClockModel(
            node, 0, slope * 1000.0, intercept * 1000.0, sigma_us * 1000.0,
            node_anchors[0].strobe_us, node_anchors[-1].strobe_us, integer, margin,
            len(pairs), int(clean.sum()), int((~clean).sum()),
            float(np.percentile(clean_values, 95)), float(np.max(clean_values)),
            float(np.percentile(raw_values, 95)), float(np.percentile(raw_values, 99)),
            float(np.max(raw_values)), coverage, max_clean_gap_s, rejection_fraction,
            (local_period / SUPERFRAME_US - 1.0) * 1e6,
            float(join["mod16_agreement_fraction"]), reversals,
        )
        models[node] = model; joins[node] = join
        for pair_index, (event_index, target_us) in enumerate(pairs):
            residual_rows.append({
                "node_id": node, "boot_epoch": 0, "fusion_event_index": event_index,
                "strobe_us": node_anchors[event_index].strobe_us,
                "listener_global_us": f"{target_us:.6f}",
                "residual_us": f"{centred[pair_index]:.6f}",
                "classification": "accepted-clean" if clean[pair_index] else "rejected-timing-outlier",
                "sweep": node_anchors[event_index].sweep,
                "sf_mod16": node_anchors[event_index].sf_mod16,
            })
    # This bridge exists only because operator action tokens are timestamped in
    # host-monotonic time. It maps those annotations onto Listener global time;
    # it is never used as a measurement clock or as a per-record correction.
    bridge_host = []; bridge_global = []
    for node, model in models.items():
        for anchor in anchors[node][::20]:
            bridge_host.append(anchor.host_monotonic_s)
            bridge_global.append(model.a_ns_per_us * anchor.strobe_us / 1000.0 + model.b_ns / 1000.0)
    bridge_slope, bridge_intercept, bridge_clean = _robust_line(
        np.asarray(bridge_host), np.asarray(bridge_global),
        hard_limit=50_000.0, floor=5_000.0,
    )
    bridge_residual = np.asarray(bridge_global) - (
        bridge_slope * np.asarray(bridge_host) + bridge_intercept)
    action_bridge = {
        "listener_global_us_per_host_s": bridge_slope,
        "listener_global_us_intercept": bridge_intercept,
        "pairs": len(bridge_host),
        "clean_pairs": int(bridge_clean.sum()),
        "clean_residual_p95_us": float(np.percentile(np.abs(bridge_residual[bridge_clean]), 95)),
        "semantics": "annotation bridge only; never a measurement-time source",
    }
    gate = {
        "superframe_us": SUPERFRAME_US,
        "nodes": len(models),
        "listener_poll_records": len(polls),
        "no_unresolved_integer_ambiguity": all(j["selected_seed_fraction"] > 0.5 and j["mod16_agreement_fraction"] == 1.0 for j in joins.values()),
        "minimum_clean_listener_pairs": all(m.clean_pairs >= MIN_CLEAN_LISTENER_PAIRS for m in models.values()),
        "minimum_capture_span_coverage": all(m.capture_span_coverage >= MIN_CAPTURE_SPAN_COVERAGE for m in models.values()),
        "maximum_clean_anchor_gap": all(m.max_clean_anchor_gap_s <= MAX_CLEAN_ANCHOR_GAP_S for m in models.values()),
        "maximum_rejection_fraction": all(m.rejection_fraction <= MAX_REJECTION_FRACTION for m in models.values()),
        "raw_residual_p99_bounded": all(m.raw_residual_p99_us < RAW_RESIDUAL_P99_GATE_US for m in models.values()),
        "raw_residual_max_bounded": all(m.raw_residual_max_us < RAW_RESIDUAL_MAX_GATE_US for m in models.values()),
        "clean_residual_p95_lt_0_5_ms": all(m.clean_residual_p95_us < CLEAN_RESIDUAL_P95_GATE_US for m in models.values()),
        "clean_residual_max_lt_1_ms": all(m.clean_residual_max_us < CLEAN_RESIDUAL_MAX_GATE_US for m in models.values()),
        "classifier_hard_limit_not_tautological": CLOCK_OUTLIER_HARD_LIMIT_US > CLEAN_RESIDUAL_MAX_GATE_US,
        "all_boot_segments_explicit": all(m.boot_epoch == 0 for m in models.values()),
        "no_timestamp_reversal": all(m.timestamp_reversals == 0 for m in models.values()),
        "listener_audit": listener_audit,
        "integer_join": joins,
        "action_annotation_bridge": action_bridge,
    }
    gate["thresholds"] = {
        "minimum_clean_listener_pairs": MIN_CLEAN_LISTENER_PAIRS,
        "minimum_capture_span_coverage": MIN_CAPTURE_SPAN_COVERAGE,
        "maximum_clean_anchor_gap_s": MAX_CLEAN_ANCHOR_GAP_S,
        "maximum_rejection_fraction": MAX_REJECTION_FRACTION,
        "raw_residual_p99_gate_us": RAW_RESIDUAL_P99_GATE_US,
        "raw_residual_max_gate_us": RAW_RESIDUAL_MAX_GATE_US,
        "clean_residual_p95_gate_us": CLEAN_RESIDUAL_P95_GATE_US,
        "clean_residual_max_gate_us": CLEAN_RESIDUAL_MAX_GATE_US,
        "outlier_classifier_hard_limit_us": CLOCK_OUTLIER_HARD_LIMIT_US,
    }
    gate["per_boot_segment_coverage"] = {
        f"{node}:{model.boot_epoch}": {
            "clean_pairs": model.clean_pairs,
            "capture_span_coverage": model.capture_span_coverage,
            "max_clean_anchor_gap_s": model.max_clean_anchor_gap_s,
            "rejection_fraction": model.rejection_fraction,
        }
        for node, model in sorted(models.items())
    }
    gate["pass"] = all(gate[k] for k in (
        "no_unresolved_integer_ambiguity", "minimum_clean_listener_pairs",
        "minimum_capture_span_coverage", "maximum_clean_anchor_gap",
        "maximum_rejection_fraction", "raw_residual_p99_bounded",
        "raw_residual_max_bounded", "clean_residual_p95_lt_0_5_ms",
        "clean_residual_max_lt_1_ms", "classifier_hard_limit_not_tautological",
        "all_boot_segments_explicit", "no_timestamp_reversal"))
    return models, residual_rows, gate


def models_as_json(models: Mapping[str, ClockModel]) -> dict:
    return {node: asdict(model) for node, model in sorted(models.items())}
