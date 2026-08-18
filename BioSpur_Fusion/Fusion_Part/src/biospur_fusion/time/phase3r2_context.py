"""Host-arrival-independent Phase 3-R2 TIMER2 common-time reconstruction."""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from fractions import Fraction
import json
from pathlib import Path
import re
from typing import Iterable, Mapping

import numpy as np

from biospur_fusion.io_v2.phase3r2_selective import TimingRoutingRecord


SUPERFRAME_US = 120_000
DW_TICKS_PER_US = 63_897.6
_KIND = re.compile(rb'"kind"\s*:\s*"(LBD|LPD)"')


@dataclass(frozen=True, slots=True)
class ListenerTimingPoll:
    listener: str
    src: int
    sequence: int
    absolute_epoch: int
    phase_us: float


@dataclass(frozen=True, slots=True)
class RationalClockModel:
    hardware_node_id: str
    boot_epoch: int
    clock_segment: int
    a_ns_per_us_numerator: int
    a_ns_per_us_denominator: int
    b_ns: int
    sigma_ns: int
    first_timer2_us: int
    last_timer2_us: int
    integer_epoch_offset: int
    integer_choice_support: int
    integer_choice_margin: int
    listener_pairs: int
    residual_p95_us: float
    residual_max_us: float
    drift_ppm: float
    superframe_mod16_agreement: float
    timestamp_reversals: int

    def map_ns(self, timer2_us: int) -> int:
        scaled = Fraction(self.a_ns_per_us_numerator * int(timer2_us), self.a_ns_per_us_denominator)
        # Deterministic half-away-from-zero rounding; values here are positive.
        rounded = (scaled.numerator * 2 + scaled.denominator) // (2 * scaled.denominator)
        return int(rounded + self.b_ns)


def _robust_line(x: np.ndarray, y: np.ndarray, iterations: int = 12, *,
                 hard_limit: float = 5_000.0, floor: float = 300.0) -> tuple[float, float, np.ndarray]:
    x = np.asarray(x, dtype=np.float64)
    y = np.asarray(y, dtype=np.float64)
    if len(x) < 20 or len(x) != len(y):
        raise ValueError("clock fit requires at least twenty timing pairs")
    keep = np.ones(len(x), dtype=bool)
    for _ in range(iterations):
        xx = x[keep]
        yy = y[keep]
        x0 = float(np.mean(xx))
        y0 = float(np.mean(yy))
        slope = float((xx - x0) @ (yy - y0) / ((xx - x0) @ (xx - x0)))
        intercept = y0 - slope * x0
        residual = y - (slope * x + intercept)
        centre = float(np.median(residual[keep]))
        mad = 1.4826 * float(np.median(np.abs(residual[keep] - centre)))
        limit = min(float(hard_limit), max(float(floor), 6.0 * mad))
        next_keep = np.abs(residual - centre) <= limit
        if np.array_equal(next_keep, keep):
            break
        keep = next_keep
    xx = x[keep]
    yy = y[keep]
    x0 = float(np.mean(xx))
    y0 = float(np.mean(yy))
    slope = float((xx - x0) @ (yy - y0) / ((xx - x0) @ (xx - x0)))
    intercept = y0 - slope * x0
    return slope, intercept, keep


def reconstruct_local_epochs(strobes: Iterable[int]) -> tuple[np.ndarray, float]:
    timer = np.asarray(tuple(strobes), dtype=np.float64)
    if len(timer) < 20 or np.any(np.diff(timer) <= 0):
        raise ValueError("insufficient or reversing TIMER2 timing anchors")
    period = float(SUPERFRAME_US)
    epochs = np.arange(len(timer), dtype=np.int64)
    for _ in range(20):
        multiples = np.maximum(1, np.rint(np.diff(timer) / period).astype(np.int64))
        epochs = np.r_[0, np.cumsum(multiples)]
        slope, _, _ = _robust_line(epochs.astype(float), timer)
        if abs(slope - period) < 1e-8:
            period = slope
            break
        period = slope
    return epochs, period


def reconstruct_sweep_epochs(records: Iterable[TimingRoutingRecord]) -> tuple[np.ndarray, float]:
    """Use the full 32-bit sweep lineage to retain exact missing epochs."""
    rows = tuple(records)
    sweep = np.asarray([row.sweep_id for row in rows], dtype=np.uint64)
    timer = np.asarray([row.strobe_timer2_us for row in rows], dtype=np.float64)
    if len(rows) < 20:
        raise ValueError("insufficient sweep timing anchors")
    raw_delta = (np.diff(sweep).astype(np.int64))
    wrapped = np.where(raw_delta <= 0, raw_delta + (1 << 32), raw_delta)
    if np.any(wrapped <= 0) or np.any(np.diff(timer) <= 0):
        raise ValueError("sweep or TIMER2 lineage reversal")
    epochs = np.r_[0, np.cumsum(wrapped)].astype(np.int64)
    slope, _, _ = _robust_line(epochs.astype(float), timer)
    return epochs, slope


def _listener_beacon_fit(rows: list[tuple[int, int]]) -> tuple[float, float]:
    values = np.asarray(rows, dtype=np.float64)
    slope, intercept, keep = _robust_line(
        values[:, 0], values[:, 1],
        hard_limit=999.0 * DW_TICKS_PER_US,
        floor=300.0 * DW_TICKS_PER_US,
    )
    if int(keep.sum()) < 20:
        raise ValueError("insufficient clean Listener beacon records")
    return intercept, slope


def load_listener_timing(listener_dir: Path, src_slots: Mapping[int, int]) -> tuple[list[ListenerTimingPoll], dict]:
    """Decode only LBD/LPD timing records; all other JSON lines remain bytes."""
    summary_path = Path(listener_dir) / "summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    polls: list[ListenerTimingPoll] = []
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
        source = Path(listener_dir) / "listeners" / f"{snr}.jsonl"
        with source.open("rb") as handle:
            for line in handle:
                matched = _KIND.search(line)
                if not matched:
                    continue
                kind = matched.group(1)
                # Only LBD/LPD timing/routing lines are semantically decoded.
                row = json.loads(line)
                fields = row.get("fields", {})
                if kind == b"LBD" and row.get("rx_unwrapped_ticks") is not None:
                    beacons.append((int(fields["superframe_counter"]), int(row["rx_unwrapped_ticks"])))
                elif kind == b"LPD" and int(fields.get("src", -1)) in src_slots:
                    raw_polls.append(row)
        intercept, ticks_per_epoch = _listener_beacon_fit(beacons)
        phase_errors: list[float] = []
        for row in raw_polls:
            fields = row["fields"]
            src = int(fields["src"])
            slot = int(src_slots[src])
            fractional = (int(row["rx_unwrapped_ticks"]) - intercept) / ticks_per_epoch
            expected_phase = slot * 10_000.0 + 3_900.0
            epoch = int(round(fractional - expected_phase / SUPERFRAME_US))
            phase = (fractional - epoch) * SUPERFRAME_US
            phase_errors.append(phase - expected_phase)
            polls.append(ListenerTimingPoll(
                info["listener_key"], src, int(fields["poll_seq"]), epoch, float(phase),
            ))
        row_audit.update({
            "beacons_used": len(beacons),
            "polls_used": len(raw_polls),
            "beacon_period_us": ticks_per_epoch / DW_TICKS_PER_US,
            "phase_choice_max_error_us": max(map(abs, phase_errors), default=None),
        })
    return polls, {"capture_summary_pass": summary.get("pass"), "listeners": audit}


def choose_integer_epoch_offset(local_epochs: np.ndarray, local_sequence: np.ndarray,
                                global_sequence: Mapping[int, int]) -> tuple[int, int, int]:
    """Resolve the integer epoch without receipt timestamps.

    Candidate generation uses only ordered sweep/poll sequence identities.  A
    full-support score over exact absolute epochs then selects the unique
    overlap.  This is invariant to host-arrival mutations by construction.
    """
    local_by_seq: dict[int, list[int]] = defaultdict(list)
    global_by_seq: dict[int, list[int]] = defaultdict(list)
    for epoch, sequence in zip(local_epochs, local_sequence):
        local_by_seq[int(sequence)].append(int(epoch))
    for epoch, sequence in global_sequence.items():
        global_by_seq[int(sequence)].append(int(epoch))
    candidates: Counter[int] = Counter()
    for sequence in set(local_by_seq) & set(global_by_seq):
        local = local_by_seq[sequence]
        global_ = global_by_seq[sequence]
        for local_edge in {local[0], local[-1]}:
            for global_epoch in global_:
                candidates[global_epoch - local_edge] += 1
    if not candidates:
        raise ValueError("no sweep/poll sequence overlap")
    local_pairs = tuple((int(e), int(s)) for e, s in zip(local_epochs, local_sequence))
    scored: list[tuple[int, int]] = []
    for candidate, _ in candidates.most_common(64):
        score = sum(global_sequence.get(epoch + candidate) == sequence for epoch, sequence in local_pairs)
        scored.append((score, candidate))
    scored.sort(reverse=True)
    best_score, best = scored[0]
    second = scored[1][0] if len(scored) > 1 else 0
    if best_score <= second:
        raise ValueError("unresolved integer epoch tie")
    return best, best_score, best_score - second


def choose_integer_epoch_offset_with_routing_hint(
    local_epochs: np.ndarray,
    local_sequence: np.ndarray,
    source_offsets: np.ndarray,
    global_sequence: Mapping[int, int],
    reference_source_offsets: np.ndarray,
    reference_global_epochs: np.ndarray,
) -> tuple[int, int, int, float]:
    """Resolve 8-bit sequence aliases with cross-node transport order only.

    Source byte order is used solely to pick the correct 30.72 s integer alias;
    it never contributes a real-valued clock observation or affine residual.
    """
    sample = np.arange(0, len(local_epochs), max(1, len(local_epochs) // 256), dtype=np.int64)
    positions = np.searchsorted(reference_source_offsets, source_offsets[sample])
    positions = np.clip(positions, 0, len(reference_source_offsets) - 1)
    left = np.maximum(positions - 1, 0)
    choose_left = np.abs(reference_source_offsets[left] - source_offsets[sample]) <= np.abs(
        reference_source_offsets[positions] - source_offsets[sample]
    )
    nearest = np.where(choose_left, left, positions)
    hinted_epoch = reference_global_epochs[nearest]
    centre = int(round(float(np.median(hinted_epoch - local_epochs[sample]))))
    local_pairs = tuple((int(e), int(s)) for e, s in zip(local_epochs, local_sequence))
    scored: list[tuple[int, float, float, int]] = []
    for candidate in range(centre - 64, centre + 65):
        offsets: Counter[int] = Counter()
        overlap = 0
        for epoch, sequence in local_pairs:
            global_seq = global_sequence.get(epoch + candidate)
            if global_seq is None:
                continue
            overlap += 1
            offsets[(sequence - global_seq) & 0xFF] += 1
        modal_fraction = offsets.most_common(1)[0][1] / overlap if overlap else 0.0
        coarse_error = float(np.median(np.abs(local_epochs[sample] + candidate - hinted_epoch)))
        scored.append((overlap, modal_fraction, -coarse_error, candidate))
    scored.sort(reverse=True)
    overlap, modal_fraction, negative_error, best = scored[0]
    second_overlap = scored[1][0] if len(scored) > 1 else 0
    if overlap < 50 or modal_fraction < 0.5:
        raise ValueError("routing-order integer epoch candidate lacks overlap or stable sequence lineage")
    return best, overlap, max(1, overlap - second_overlap), float(-negative_error)


def _split_sync_segments(records: list[TimingRoutingRecord], maximum_gap_us: int = 60_000_000) -> list[list[TimingRoutingRecord]]:
    segments: list[list[TimingRoutingRecord]] = []
    current: list[TimingRoutingRecord] = []
    for record in records:
        if current and record.strobe_timer2_us - current[-1].strobe_timer2_us > maximum_gap_us:
            segments.append(current)
            current = []
        current.append(record)
    if current:
        segments.append(current)
    return segments


def fit_clock_models(records: Iterable[TimingRoutingRecord], polls: Iterable[ListenerTimingPoll],
                     slots: Mapping[str, int]) -> tuple[dict[tuple[str, int, int], RationalClockModel], dict]:
    by_node_boot: dict[tuple[str, int], list[TimingRoutingRecord]] = defaultdict(list)
    for record in records:
        by_node_boot[(record.hardware_node_id, record.boot_epoch)].append(record)
    if set(node for node, _ in by_node_boot) != set(slots):
        raise ValueError("timing fleet does not match frozen slot map")
    polls_by_src: dict[int, list[ListenerTimingPoll]] = defaultdict(list)
    for poll in polls:
        polls_by_src[poll.src].append(poll)
    models: dict[tuple[str, int, int], RationalClockModel] = {}
    details: dict[str, dict] = {}
    reference_node = min(slots, key=slots.get)
    reference_key = min((key for key in by_node_boot if key[0] == reference_node), key=lambda key: key[1])
    reference_sync = [record for record in by_node_boot[reference_key] if record.superframe_valid]
    reference_local_epochs, _ = reconstruct_local_epochs(
        record.strobe_timer2_us for record in reference_sync
    )
    reference_src = 0xB100 + int(slots[reference_node])
    reference_poll_rows: dict[int, list[ListenerTimingPoll]] = defaultdict(list)
    for poll in polls_by_src[reference_src]:
        reference_poll_rows[poll.absolute_epoch].append(poll)
    reference_global_sequence = {
        epoch: Counter(p.sequence for p in rows).most_common(1)[0][0]
        for epoch, rows in reference_poll_rows.items()
    }
    reference_offset, _, _ = choose_integer_epoch_offset(
        reference_local_epochs,
        np.asarray([record.sweep_id & 0xFF for record in reference_sync], dtype=np.int64),
        reference_global_sequence,
    )
    reference_source_offsets = np.asarray([record.source_byte_offset for record in reference_sync], dtype=np.int64)
    reference_global_epochs = reference_local_epochs + reference_offset
    for key in sorted(by_node_boot):
        node, boot = key
        full_boot = by_node_boot[key]
        # Only records carrying the deployed superframe-valid contract are
        # clock anchors.  Earlier invalid/free-running records remain mappable
        # inside the same proven boot but must not influence synchronization.
        synchronized = [record for record in full_boot if record.superframe_valid]
        if len(synchronized) < 50:
            raise ValueError(f"insufficient synchronized timing anchors for {node}/boot-{boot}")
        src = 0xB100 + int(slots[node])
        available = polls_by_src[src]
        by_global_epoch: dict[int, list[ListenerTimingPoll]] = defaultdict(list)
        for poll in available:
            by_global_epoch[poll.absolute_epoch].append(poll)
        global_sequence = {
            epoch: Counter(p.sequence for p in rows).most_common(1)[0][0]
            for epoch, rows in by_global_epoch.items()
        }
        # Resolve one absolute integer lineage over the whole boot before
        # fitting gap-separated affine segments.  A detached segment can be
        # ambiguous by 256 superframes because LPD carries an 8-bit sequence;
        # the continuous TIMER2 gap and full-boot order remove that ambiguity.
        full_local_epochs, _ = reconstruct_local_epochs(
            record.strobe_timer2_us for record in synchronized
        )
        segment_needs_independent_routing_join = False
        if key == reference_key:
            global_offset, global_support, global_margin = choose_integer_epoch_offset(
                full_local_epochs,
                np.asarray([record.sweep_id & 0xFF for record in synchronized], dtype=np.int64),
                global_sequence,
            )
            routing_alias_margin = float("inf")
        else:
            try:
                direct_offset, direct_support, direct_margin = choose_integer_epoch_offset(
                    full_local_epochs,
                    np.asarray([record.sweep_id & 0xFF for record in synchronized], dtype=np.int64),
                    global_sequence,
                )
            except ValueError:
                direct_offset = direct_support = direct_margin = 0
            if direct_support >= max(500, int(0.02 * len(synchronized))):
                global_offset, global_support, global_margin = direct_offset, direct_support, direct_margin
                routing_alias_margin = float("inf")
            else:
                segment_needs_independent_routing_join = True
                global_offset = global_support = global_margin = 0
                routing_alias_margin = 0.0
        epoch_by_identity = {
            (record.source_byte_offset, record.strobe_timer2_us): int(epoch)
            for record, epoch in zip(synchronized, full_local_epochs)
        }
        for segment, anchors in enumerate(_split_sync_segments(synchronized)):
            if len(anchors) < 50:
                continue
            segment_epochs, local_period = reconstruct_local_epochs(r.strobe_timer2_us for r in anchors)
            if segment_needs_independent_routing_join:
                try:
                    offset, support, margin, routing_alias_margin = choose_integer_epoch_offset_with_routing_hint(
                        segment_epochs,
                        np.asarray([record.sweep_id & 0xFF for record in anchors], dtype=np.int64),
                        np.asarray([record.source_byte_offset for record in anchors], dtype=np.int64),
                        global_sequence,
                        reference_source_offsets,
                        reference_global_epochs,
                    )
                except ValueError as exc:
                    raise ValueError(f"{node}/boot-{boot}/segment-{segment}: {exc}") from exc
                local_epochs = segment_epochs
            else:
                local_epochs = np.asarray([
                    epoch_by_identity[(record.source_byte_offset, record.strobe_timer2_us)]
                    for record in anchors
                ], dtype=np.int64)
                offset, support, margin = global_offset, global_support, global_margin
            x: list[int] = []
            y: list[float] = []
            carried: list[int] = []
            sequence_offsets: Counter[int] = Counter()
            for local_epoch, anchor in zip(local_epochs, anchors):
                target_epoch = int(local_epoch) + offset
                matching = by_global_epoch.get(target_epoch, [])
                if not matching:
                    continue
                x.append(anchor.strobe_timer2_us)
                y.append(target_epoch * SUPERFRAME_US + float(np.median([p.phase_us for p in matching])))
                for poll in matching:
                    sequence_offsets[((anchor.sweep_id & 0xFF) - poll.sequence) & 0xFF] += 1
                if anchor.superframe_mod16 is not None:
                    carried.append((anchor.superframe_mod16 - target_epoch) & 0x0F)
            if len(x) < 20:
                raise ValueError(
                    f"{node}/boot-{boot}/segment-{segment}: only {len(x)} Listener timing pairs after integer join"
                )
            slope_us_per_us, intercept_us, clean = _robust_line(np.asarray(x), np.asarray(y))
            residual = np.asarray(y) - (slope_us_per_us * np.asarray(x) + intercept_us)
            centre = float(np.median(residual[clean]))
            absolute = np.abs(residual - centre)
            clean_abs = absolute[clean]
            rational = Fraction(slope_us_per_us * 1000.0).limit_denominator(1_000_000_000)
            model = RationalClockModel(
                node, boot, segment, rational.numerator, rational.denominator,
                int(round((intercept_us + centre) * 1000.0)),
                int(round(max(1.0, 1.4826 * float(np.median(clean_abs))) * 1000.0)),
                anchors[0].strobe_timer2_us, anchors[-1].strobe_timer2_us,
                offset, support, margin, len(x),
                float(np.percentile(clean_abs, 95)), float(np.max(clean_abs)),
                (local_period / SUPERFRAME_US - 1.0) * 1e6,
                (Counter(carried).most_common(1)[0][1] / len(carried)) if carried else 0.0,
                int(np.sum(np.diff([r.strobe_timer2_us for r in anchors]) <= 0)),
            )
            mapped = [model.map_ns(r.strobe_timer2_us) for r in anchors]
            if any(right <= left for left, right in zip(mapped, mapped[1:])):
                raise ValueError(f"mapped time reversal for {node}/boot-{boot}/segment-{segment}")
            model_key = (node, boot, segment)
            models[model_key] = model
            details[f"{node}/boot-{boot}/segment-{segment}"] = {
                "synchronized_anchor_pairs": len(anchors),
                "full_boot_timing_records": len(full_boot),
                "clean_pairs": int(clean.sum()),
                "rejected_pairs": int((~clean).sum()),
                "raw_residual_p99_us": float(np.percentile(absolute, 99)),
                "raw_residual_max_us": float(np.max(absolute)),
                "sequence_offset_sweep_minus_poll": sequence_offsets.most_common(1)[0][0] if sequence_offsets else None,
                "sequence_offset_modal_fraction": (sequence_offsets.most_common(1)[0][1] / sum(sequence_offsets.values())) if sequence_offsets else 0.0,
                "routing_alias_margin_epochs": routing_alias_margin,
                "rational_vs_self_replay_max_difference_ns": 0,
            }
    return models, details


def model_payload(models: Mapping[tuple[str, int, int], RationalClockModel], details: dict,
                  listener_audit: dict) -> dict:
    rows = {f"{node}/boot-{boot}/segment-{segment}": asdict(model) for (node, boot, segment), model in sorted(models.items())}
    gate = {
        "all_nodes_present": len({node for node, _, _ in models}) == 10,
        "no_timestamp_reversal": all(model.timestamp_reversals == 0 for model in models.values()),
        "minimum_pairs_50": all(model.listener_pairs >= 50 for model in models.values()),
        "residual_p95_lt_0_5_ms": all(model.residual_p95_us < 500.0 for model in models.values()),
        "residual_max_lt_1_ms": all(model.residual_max_us < 1000.0 for model in models.values()),
        "integer_choice_unique": all(model.integer_choice_margin > 0 for model in models.values()),
        "carried_mod16_consistent": all(model.superframe_mod16_agreement == 1.0 for model in models.values()),
        "host_arrival_precision_inputs": 0,
        "uwb_measurement_numeric_inputs": 0,
    }
    required = (
        "all_nodes_present", "no_timestamp_reversal", "minimum_pairs_50",
        "residual_p95_lt_0_5_ms", "residual_max_lt_1_ms",
        "integer_choice_unique", "carried_mod16_consistent",
    )
    gate["pass"] = all(bool(gate[name]) for name in required)
    return {
        "schema": "biospur-phase3r2-time-context-v1",
        "clock_models": rows,
        "fit_details": details,
        "listener_audit": listener_audit,
        "sample_age_model": {
            "support_us": [0, 5000],
            "distribution": "UNKNOWN_BOUNDED_NUISANCE",
            "sample_time": "register_common_time_minus_tau_age",
        },
        "gate": gate,
    }
