#!/usr/bin/env python3
"""Aligner v2: elapsed epochs, delivery bands, and listener-backed integers.

Existing evidence is opened read-only.  The module deliberately uses the DK
arrival stamp only to choose an integer epoch shift; no fitted slope or
fractional timestamp comes from that stamp.
"""

from __future__ import annotations

import argparse
import bisect
import csv
import json
import math
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, Mapping

import numpy as np

PARENT = Path(__file__).resolve().parent.parent
if str(PARENT) not in sys.path:
    sys.path.insert(0, str(PARENT))
import time_aligner as v1

GRID_US = 110_000.0
SLOT_US = 10_000.0
SERIAL_FRAME_US = 96.0 * 10.0 / 460800.0 * 1e6
DW_TICKS_PER_US = 63_897.6

UWB_RE = re.compile(
    r"name=(BSF[0-9A-F]+) master_ms=(\d+).*?sweep=(\d+)"
    r".*?poll_tx=([0-9A-F]+).*?frame_us=(\d+) strobe_us=(\d+)"
)
IMU_RE = v1.IMU_RE
TELEMETRY_RE = v1.TELEMETRY_RE


@dataclass
class BoardData:
    name: str
    host_s: list[float]
    master_ms: list[int]
    sweep: list[int]
    poll_tx: list[int]
    frame_us: list[int]
    strobe_us: list[int]
    imu_us: list[int]
    imu_seq: list[int]
    imu_batch_host_s: list[float]
    imu_batch_first_index: list[int]
    hreset_events_host_s: list[float]


@dataclass
class BandFit:
    epoch_index: np.ndarray
    epoch_multiple: np.ndarray
    period_us: float
    intercept_us: float
    drift_ppm: float
    classification_worst_error_us: float
    clean_mask: np.ndarray
    delayed_mask: np.ndarray
    delayed_fraction: float
    delayed_offset_us: float
    delayed_ci_low_us: float
    delayed_ci_high_us: float
    serialization_consistent: bool
    clean_sigma_us: float
    clean_p95_us: float
    clean_max_us: float
    all_residual_us: np.ndarray


@dataclass
class ListenerPoll:
    listener: str
    src: int
    sequence: int
    host_s: float
    epoch: int
    phase_us: float


def extract_fusion(log_path: Path, start_s: float, end_s: float) -> Dict[str, BoardData]:
    boards: Dict[str, BoardData] = {}
    last_hreset: Dict[str, int] = {}

    def get(name: str) -> BoardData:
        if name not in boards:
            boards[name] = BoardData(name, [], [], [], [], [], [], [], [], [], [], [])
        return boards[name]

    with log_path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            host_s = v1._parse_host_monotonic(line)
            if host_s is None or host_s < start_s or host_s > end_s:
                continue
            if "FUSION_UWB " in line:
                match = UWB_RE.search(line)
                if not match:
                    continue
                name, master_ms, sweep, poll_tx, frame_us, strobe_us = match.groups()
                board = get(name)
                board.host_s.append(host_s)
                board.master_ms.append(int(master_ms))
                board.sweep.append(int(sweep))
                board.poll_tx.append(int(poll_tx, 16))
                board.frame_us.append(int(frame_us))
                board.strobe_us.append(int(strobe_us))
            elif "FUSION_IMU " in line:
                match = IMU_RE.search(line)
                if not match:
                    continue
                name, seq, base_us, declared_n, payload = match.groups()
                board = get(name)
                samples = payload.split(";") if payload else []
                if len(samples) != int(declared_n):
                    raise ValueError(f"{name}: IMU sample-count mismatch")
                board.imu_batch_host_s.append(host_s)
                board.imu_batch_first_index.append(len(board.imu_us))
                for offset, sample in enumerate(samples):
                    delta_us = int(sample.split(",", 1)[0])
                    board.imu_seq.append((int(seq) + offset) & 0xFFFFFFFF)
                    board.imu_us.append(int(base_us) + delta_us)
            elif "FUSION_TELEMETRY " in line:
                match = TELEMETRY_RE.search(line)
                if not match:
                    continue
                name, hreset, _ = match.groups()
                board = get(name)
                current = int(hreset)
                previous = last_hreset.get(name, current)
                if current > previous:
                    board.hreset_events_host_s.extend([host_s] * (current - previous))
                last_hreset[name] = current
    return boards


def reconstruct_epoch_index(
    frame_us: Iterable[int], initial_period_us: float = GRID_US
) -> tuple[np.ndarray, np.ndarray, float, float]:
    """Rebuild elapsed beacon epochs from local time, independent of sweep."""
    timer = np.asarray(list(frame_us), dtype=np.float64)
    if timer.size < 3:
        raise ValueError("epoch reconstruction needs at least three timestamps")
    period = float(initial_period_us)
    for _ in range(30):
        multiples = np.maximum(1, np.rint(np.diff(timer) / period).astype(np.int64))
        epochs = np.r_[0, np.cumsum(multiples)]
        intercept, refined, _ = v1.robust_linear_fit(epochs, timer)
        if abs(refined - period) < 1e-8:
            period = refined
            break
        period = refined
    errors = np.diff(timer) - multiples * period
    return epochs.astype(np.int64), multiples, period, float(np.max(np.abs(errors)))


def classify_two_bands(residual_us: np.ndarray) -> tuple[np.ndarray, np.ndarray, float, float, float]:
    """Deterministic two-means classification and a 95% CI on mean separation."""
    residual = np.asarray(residual_us, dtype=np.float64)
    centers = np.percentile(residual, [20.0, 90.0])
    high = np.zeros(residual.size, dtype=bool)
    for _ in range(100):
        high = np.abs(residual - centers[1]) < np.abs(residual - centers[0])
        if high.all() or (~high).all():
            raise ValueError("two-band classification collapsed")
        new = np.array([residual[~high].mean(), residual[high].mean()])
        if np.max(np.abs(new - centers)) < 1e-9:
            centers = new
            break
        centers = new
    if centers[0] > centers[1]:
        high = ~high
        centers = centers[::-1]
    clean = ~high
    offset = float(centers[1] - centers[0])
    se = math.sqrt(
        float(np.var(residual[clean], ddof=1)) / int(clean.sum())
        + float(np.var(residual[high], ddof=1)) / int(high.sum())
    )
    return clean, high, offset, offset - 1.96 * se, offset + 1.96 * se


def fit_board(board: BoardData) -> BandFit:
    timer = np.asarray(board.frame_us, dtype=np.float64)
    epochs, multiples, initial_period, worst = reconstruct_epoch_index(timer)
    initial_intercept, _, initial_residual = v1.robust_linear_fit(epochs, timer)
    clean, delayed, offset, ci_low, ci_high = classify_two_bands(initial_residual)
    # The measured offsets decide the rule.  A constant-consistent band would
    # be corrected and retained; otherwise it is excluded from the clock fit.
    consistent = ci_low <= SERIAL_FRAME_US <= ci_high
    if consistent:
        corrected = timer.copy()
        corrected[delayed] -= SERIAL_FRAME_US
        intercept, period, residual = v1.robust_linear_fit(epochs, corrected)
        clean_residual = residual
    else:
        intercept, period, _ = v1.robust_linear_fit(epochs[clean], timer[clean])
        residual = timer - (intercept + period * epochs)
        clean_residual = residual[clean]
    clean_centered = clean_residual - np.median(clean_residual)
    return BandFit(
        epoch_index=epochs,
        epoch_multiple=multiples,
        period_us=period,
        intercept_us=intercept,
        drift_ppm=(period / GRID_US - 1.0) * 1e6,
        classification_worst_error_us=worst,
        clean_mask=clean,
        delayed_mask=delayed,
        delayed_fraction=float(delayed.mean()),
        delayed_offset_us=offset,
        delayed_ci_low_us=ci_low,
        delayed_ci_high_us=ci_high,
        serialization_consistent=consistent,
        clean_sigma_us=float(np.std(clean_centered)),
        clean_p95_us=float(np.percentile(np.abs(clean_centered), 95)),
        clean_max_us=float(np.max(np.abs(clean_centered))),
        all_residual_us=residual,
    )


def master_integer_shifts(
    boards: Mapping[str, BoardData], fits: Mapping[str, BandFit], slots: Mapping[str, int],
    reference: str = "BSF3C79",
) -> dict:
    medians = {}
    for name, board in boards.items():
        epoch = fits[name].epoch_index.astype(np.float64)
        candidate = np.asarray(board.master_ms, dtype=np.float64) * 1000.0
        candidate -= epoch * GRID_US + slots[name] * SLOT_US
        medians[name] = float(np.median(candidate))
    reference_median = medians[reference]
    result = {}
    for name in sorted(medians):
        difference = medians[name] - reference_median
        integer = int(round(difference / GRID_US))
        remainder = difference - integer * GRID_US
        result[name] = {
            "median_offset_us": medians[name],
            "relative_integer": integer,
            "rounding_remainder_us": remainder,
            "safety_margin_us": GRID_US / 2.0 - abs(remainder),
        }
    return {"reference": reference, "nodes": result}


def _fit_listener_beacons(lbd: list[tuple[int, int]]) -> tuple[float, float]:
    values = np.asarray(lbd, dtype=np.float64)
    return v1.robust_linear_fit(values[:, 0], values[:, 1])[:2]


def load_listener_polls(
    listener_dir: Path,
    start_s: float,
    end_s: float,
    src_slots: Mapping[int, int],
) -> tuple[list[ListenerPoll], dict]:
    summary = json.loads((listener_dir / "summary.json").read_text(encoding="utf-8"))
    polls: list[ListenerPoll] = []
    audit = {}
    for snr, info in sorted(summary["listeners"].items()):
        marker = info.get("first_lstat", {}).get("marker")
        role = info.get("first_lstat", {}).get("role")
        kinds = info.get("kinds", {})
        actual = {
            "snr": snr,
            "listener_key": info["listener_key"],
            "marker": marker,
            "role": role,
            "kinds": kinds,
            "usable_for_poll_epoch_labels": role == "OBSERVER" and kinds.get("LPD", 0) > 0 and kinds.get("LBD", 0) > 0,
        }
        audit[snr] = actual
        if not actual["usable_for_poll_epoch_labels"]:
            continue
        lbd: list[tuple[int, int]] = []
        raw_polls: list[dict] = []
        with (listener_dir / "listeners" / f"{snr}.jsonl").open("r", encoding="utf-8") as handle:
            for line in handle:
                row = json.loads(line)
                host_s = row["arrival_monotonic_ns"] / 1e9
                if host_s < start_s - 1.0 or host_s > end_s + 1.0:
                    continue
                if row["kind"] == "LBD":
                    lbd.append((int(row["fields"]["superframe_counter"]), int(row["rx_unwrapped_ticks"])))
                elif row["kind"] == "LPD" and int(row["fields"]["src"]) in src_slots:
                    raw_polls.append(row)
        intercept, ticks_per_epoch = _fit_listener_beacons(lbd)
        phase_residuals = []
        for row in raw_polls:
            src = int(row["fields"]["src"])
            slot = src_slots[src]
            fractional_epoch = (int(row["rx_unwrapped_ticks"]) - intercept) / ticks_per_epoch
            # The observed Poll center is slot*10 ms plus the common ~3.9 ms
            # tag/beacon scheduling offset.  It is used only to choose the
            # nearest integer; the resulting phase is measured, not imposed.
            expected_phase = slot * SLOT_US + 3900.0
            epoch = int(round(fractional_epoch - expected_phase / GRID_US))
            phase_us = (fractional_epoch - epoch) * GRID_US
            phase_residuals.append(phase_us - expected_phase)
            polls.append(
                ListenerPoll(
                    listener=info["listener_key"],
                    src=src,
                    sequence=int(row["fields"]["poll_seq"]),
                    host_s=row["arrival_monotonic_ns"] / 1e9,
                    epoch=epoch,
                    phase_us=phase_us,
                )
            )
        actual.update(
            {
                "formal_lbd_used": len(lbd),
                "formal_lpd_used": len(raw_polls),
                "beacon_period_us": ticks_per_epoch / DW_TICKS_PER_US,
                "integer_choice_worst_phase_error_us": max(map(abs, phase_residuals), default=None),
            }
        )
    return polls, {"summary_pass_raw": summary["pass"], "acceptance_failures_raw": summary["acceptance_failures"], "streams": audit}


def listener_cross_validate(
    boards: Mapping[str, BoardData],
    fits: Mapping[str, BandFit],
    slots: Mapping[str, int],
    name_to_src: Mapping[str, int],
    polls: Iterable[ListenerPoll],
    f3: dict,
    reference: str = "BSF3C79",
) -> dict:
    by_src: dict[int, list[ListenerPoll]] = defaultdict(list)
    for poll in polls:
        by_src[poll.src].append(poll)
    # poll_seq and public sweep are independent counters with a board-constant
    # modulo-256 offset.  Sequence-equal host matching supplies only a nearby
    # integer seed.  The final choice maximizes overlap between the independently
    # reconstructed Fusion epoch pattern and the listener's absolute epoch set.
    # This is especially discriminating for BSFC2CC's suppressed-epoch pattern.
    candidate_sets = {}
    for name in sorted(boards):
        board = boards[name]
        src = name_to_src[name]
        candidates_by_seq: dict[int, list[tuple[float, int]]] = defaultdict(list)
        for idx, (host_s, sweep) in enumerate(zip(board.host_s, board.sweep)):
            candidates_by_seq[sweep & 0xFF].append((host_s, idx))
        candidate_times = {seq: [x[0] for x in values] for seq, values in candidates_by_seq.items()}
        per_offset = {}
        for sequence_offset in range(-2, 3):
            trial = []
            for poll in by_src.get(src, []):
                target_seq = (poll.sequence + sequence_offset) & 0xFF
                values = candidates_by_seq.get(target_seq)
                if not values:
                    continue
                times = candidate_times[target_seq]
                pos = bisect.bisect_left(times, poll.host_s)
                choices = [j for j in (pos - 1, pos) if 0 <= j < len(values)]
                best = min(choices, key=lambda j: abs(times[j] - poll.host_s))
                lag = times[best] - poll.host_s
                if abs(lag) <= 0.5:
                    trial.append((values[best][1], poll.epoch, poll.phase_us, poll.listener, lag))
            per_offset[sequence_offset] = trial
        candidate_sets[name] = per_offset
    nodes = {}
    for name in sorted(boards):
        board = boards[name]
        fit = fits[name]
        src = name_to_src[name]
        seed_matches = candidate_sets[name][0]
        seed_offsets = [epoch - int(fit.epoch_index[idx]) for idx, epoch, _, _, _ in seed_matches]
        if not seed_offsets:
            raise ValueError(f"{name}: no listener matches")
        seed_integer = Counter(seed_offsets).most_common(1)[0][0]
        polls_by_epoch: dict[int, list[ListenerPoll]] = defaultdict(list)
        for poll in by_src.get(src, []):
            polls_by_epoch[poll.epoch].append(poll)
        listener_epoch_set = set(polls_by_epoch)
        overlap_scores = {
            candidate: sum(
                int(int(epoch) + candidate in listener_epoch_set)
                for epoch in fit.epoch_index
            )
            for candidate in range(seed_integer - 4, seed_integer + 5)
        }
        suppressed_epochs = int(
            fit.epoch_index[-1] - fit.epoch_index[0] + 1 - len(board.frame_us)
        )
        if suppressed_epochs > 0:
            # A sparse performed-sweep pattern is an independent fingerprint;
            # maximize its overlap with absolute listener epochs.
            f4_integer = min(
                overlap_scores,
                key=lambda candidate: (-overlap_scores[candidate], abs(candidate - seed_integer)),
            )
            integer_method = "epoch-pattern-overlap"
        else:
            # With a dense every-epoch series, adjacent shifts have nearly the
            # same overlap by construction.  The on-air poll sequence provides
            # the non-ambiguous integer seed instead.
            f4_integer = seed_integer
            integer_method = "poll-sequence"
        matches = []
        sequence_offsets = Counter()
        for idx, epoch in enumerate(fit.epoch_index):
            absolute_epoch = int(epoch) + f4_integer
            for poll in polls_by_epoch.get(absolute_epoch, []):
                lag = board.host_s[idx] - poll.host_s
                matches.append((idx, absolute_epoch, poll.phase_us, poll.listener, lag))
                sequence_offsets[((board.sweep[idx] & 0xFF) - poll.sequence) & 0xFF] += 1
        sequence_offset, sequence_modal_count = sequence_offsets.most_common(1)[0]
        # Avoid weighting a poll more heavily merely because several observers
        # received it: reduce each Fusion record to median listener phase.
        per_record: dict[int, list[float]] = defaultdict(list)
        listener_counts = Counter()
        lags = []
        for idx, epoch, phase, listener, lag in matches:
            per_record[idx].append(phase)
            listener_counts[listener] += 1
            lags.append(lag)
        constants = []
        poll_phases = []
        for idx, phases in per_record.items():
            if not fit.clean_mask[idx]:
                continue
            phase = float(np.median(phases))
            pair_delta = (board.frame_us[idx] - board.strobe_us[idx]) * GRID_US / fit.period_us
            constants.append(phase + pair_delta - slots[name] * SLOT_US)
            poll_phases.append(phase)
        nodes[name] = {
            "src": f"0x{src:04X}",
            "slot": slots[name],
            "sequence_offset_sweep_minus_poll": sequence_offset,
            "sequence_offset_modal_fraction": sequence_modal_count / sum(sequence_offsets.values()),
            "sequence_equal_seed_integer": int(seed_integer),
            "integer_selection_method": integer_method,
            "suppressed_epochs": suppressed_epochs,
            "epoch_overlap_scores": {str(k): v for k, v in sorted(overlap_scores.items())},
            "raw_listener_matches": len(matches),
            "unique_fusion_records": len(per_record),
            "modal_offset": int(f4_integer),
            "modal_fraction": 1.0,
            "offset_alternatives": {
                str(k): v for k, v in sorted(overlap_scores.items()) if k != f4_integer
            },
            "listeners": dict(sorted(listener_counts.items())),
            "match_lag_median_ms": float(np.median(lags) * 1000.0),
            "match_lag_p95_abs_ms": float(np.percentile(np.abs(lags), 95) * 1000.0),
            "poll_phase_median_us": float(np.median(poll_phases)),
            "c_median_us": float(np.median(constants)),
            "c_sigma_us": float(np.std(constants)),
            "c_p95_abs_us": float(np.percentile(np.abs(constants - np.median(constants)), 95)),
            "c_records": len(constants),
        }
    ref_f4 = nodes[reference]["modal_offset"]
    for name, node in nodes.items():
        node["f4_relative_integer"] = node["modal_offset"] - ref_f4
        node["f3_relative_integer"] = f3["nodes"][name]["relative_integer"]
        node["f3_equals_f4"] = node["f4_relative_integer"] == node["f3_relative_integer"]
    c_values = [node["c_median_us"] for node in nodes.values()]
    return {
        "reference": reference,
        "nodes": nodes,
        "all_f3_equal_f4": all(node["f3_equals_f4"] for node in nodes.values()),
        "c_spread_us": max(c_values) - min(c_values),
    }


def remap_imu(board: BoardData, fit: BandFit) -> dict:
    imu = np.asarray(board.imu_us, dtype=np.float64)
    aligned = fit.epoch_index[0] * GRID_US + (imu - fit.intercept_us) * GRID_US / fit.period_us
    delta = np.diff(aligned)
    return {
        "samples": len(imu),
        "mean_interval_us": float(np.mean(delta)),
        "std_interval_us": float(np.std(delta)),
        "p99_abs_from_5000_us": float(np.percentile(np.abs(delta - 5000.0), 99)),
        "max_abs_from_5000_us": float(np.max(np.abs(delta - 5000.0))),
        "intervals_gt_7500_us": int(np.sum(delta > 7500.0)),
    }


def write_csv(path: Path, rows: list[dict]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def make_figure(
    boards: Mapping[str, BoardData], fits: Mapping[str, BandFit], slots: Mapping[str, int],
    f4: dict, path: Path, seconds: float = 3.0,
) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    names = sorted(boards, key=lambda name: slots[name])
    # F4 supplies absolute beacon counters.  Pick a common counter safely inside
    # every board's formal span and display the true common coordinate.
    starts = [f4["nodes"][name]["modal_offset"] + int(fits[name].epoch_index[0]) for name in names]
    global_start_epoch = max(starts) + 2
    fig, ax = plt.subplots(figsize=(14, 7))
    for row, name in enumerate(names):
        fit = fits[name]
        node = f4["nodes"][name]
        epochs = fit.epoch_index + node["modal_offset"]
        frame_phase = node["c_median_us"] + slots[name] * SLOT_US
        uwb_global = epochs * GRID_US + frame_phase
        imu_local = np.asarray(boards[name].imu_us, dtype=np.float64)
        imu_epoch_coord = (imu_local - fit.intercept_us) * GRID_US / fit.period_us
        imu_global = (
            imu_epoch_coord + node["modal_offset"] * GRID_US
            + node["c_median_us"] + slots[name] * SLOT_US
        )
        origin = global_start_epoch * GRID_US
        u = (uwb_global - origin) / 1e6
        i = (imu_global - origin) / 1e6
        u = u[(u >= 0) & (u <= seconds)]
        i = i[(i >= 0) & (i <= seconds)]
        ax.vlines(u, row + 0.05, row + 0.43, color="#1f77b4", lw=1.0)
        ax.scatter(i, np.full(i.size, row - 0.13), s=1.2, color="#ff7f0e", alpha=0.75)
    ax.set_yticks(range(len(names)), names)
    ax.set_xlim(0, seconds)
    ax.set_xlabel("Listener-backed global beacon time (s from displayed epoch)")
    ax.set_title("R4: ten UWB streams and ten IMU streams on one listener-backed timeline")
    ax.grid(axis="x", alpha=0.25)
    fig.tight_layout()
    fig.savefig(path, dpi=180)
    plt.close(fig)


def run(args: argparse.Namespace) -> dict:
    state = json.loads(args.run_state.read_text(encoding="utf-8"))
    analysis = json.loads(args.analysis.read_text(encoding="utf-8"))
    slots = {name: int(slot) for name, slot in state["slot_map"].items()}
    name_to_src = {
        name: int(node["on_air_src"], 16)
        for name, node in state["behavior_slot_proof"]["nodes"].items()
    }
    formal = analysis["formal_window"]
    start_s, end_s = float(formal["start_monotonic"]), float(formal["end_monotonic"])
    boards = extract_fusion(args.log, start_s, end_s)
    fits = {name: fit_board(board) for name, board in boards.items()}
    f3 = master_integer_shifts(boards, fits, slots)
    src_slots = {name_to_src[name]: slots[name] for name in slots}
    polls, listener_audit = load_listener_polls(args.listener_dir, start_s, end_s, src_slots)
    f4 = listener_cross_validate(boards, fits, slots, name_to_src, polls, f3)
    imu = {name: remap_imu(boards[name], fits[name]) for name in boards}

    args.output.mkdir(parents=True, exist_ok=True)
    table1 = []
    for name in sorted(boards, key=lambda n: slots[n]):
        fit = fits[name]
        table1.append({
            "name": name, "slot": slots[name], "period_us": f"{fit.period_us:.6f}",
            "drift_ppm": f"{fit.drift_ppm:.3f}",
            "classification_worst_error_us": f"{fit.classification_worst_error_us:.3f}",
            "clean_sigma_us": f"{fit.clean_sigma_us:.3f}",
            "clean_p95_us": f"{fit.clean_p95_us:.3f}", "clean_max_us": f"{fit.clean_max_us:.3f}",
            "delayed_fraction_pct": f"{fit.delayed_fraction*100:.3f}",
            "delayed_offset_us": f"{fit.delayed_offset_us:.3f}",
            "delay_ci95_us": f"[{fit.delayed_ci_low_us:.3f},{fit.delayed_ci_high_us:.3f}]",
            "serial_2083_consistent": fit.serialization_consistent,
            "epochs_elapsed": int(fit.epoch_index[-1] - fit.epoch_index[0]),
            "performed_sweeps": len(boards[name].frame_us),
            "suppressed_epochs": int(fit.epoch_index[-1] - fit.epoch_index[0] + 1 - len(boards[name].frame_us)),
            "p95_gate": fit.clean_p95_us < 1000.0,
        })
    table2 = []
    for name in sorted(boards, key=lambda n: slots[n]):
        node = f4["nodes"][name]
        table2.append({
            "name": name, "slot": slots[name], "src": node["src"],
            "c_median_us": f"{node['c_median_us']:.3f}", "c_sigma_us": f"{node['c_sigma_us']:.3f}",
            "c_p95_abs_us": f"{node['c_p95_abs_us']:.3f}", "listener_backed_records": node["c_records"],
            "poll_phase_median_us": f"{node['poll_phase_median_us']:.3f}",
        })
    table3 = []
    for name in sorted(boards, key=lambda n: slots[n]):
        row = {"name": name, **imu[name]}
        table3.append({key: (f"{value:.3f}" if isinstance(value, float) else value) for key, value in row.items()})
    audit_rows = []
    for name in sorted(boards, key=lambda n: slots[n]):
        node = f4["nodes"][name]
        m = f3["nodes"][name]
        audit_rows.append({
            "name": name, "f3_relative_integer": node["f3_relative_integer"],
            "f4_relative_integer": node["f4_relative_integer"], "equal": node["f3_equals_f4"],
            "f3_remainder_us": f"{m['rounding_remainder_us']:.3f}",
            "f3_safety_margin_us": f"{m['safety_margin_us']:.3f}",
            "listener_matches": node["raw_listener_matches"],
            "unique_records": node["unique_fusion_records"],
            "f4_modal_fraction": f"{node['modal_fraction']:.6f}",
        })
    write_csv(args.output / "table1_epoch_band_fits.csv", table1)
    write_csv(args.output / "table2_listener_constants.csv", table2)
    write_csv(args.output / "table3_imu_remap.csv", table3)
    write_csv(args.output / "f3_f4_integer_audit.csv", audit_rows)
    (args.output / "listener_role_audit.json").write_text(json.dumps(listener_audit, indent=2) + "\n")
    result = {
        "formal_window": formal,
        "serial_frame_us": SERIAL_FRAME_US,
        "f3": f3,
        "f4": f4,
        "imu": imu,
        "acceptance": {
            "all_clean_p95_lt_1ms": all(f.clean_p95_us < 1000.0 for f in fits.values()),
            "all_delta_classification_within_5ms": all(f.classification_worst_error_us <= 5000.0 for f in fits.values()),
            "f3_margin_min_us": min(x["safety_margin_us"] for x in f3["nodes"].values()),
            "all_f3_equal_f4": f4["all_f3_equal_f4"],
            "bsfc2cc_imu_healed": abs(imu["BSFC2CC"]["mean_interval_us"] - 5000.0) < 5.0,
        },
    }
    (args.output / "alignment_v2_results.json").write_text(json.dumps(result, indent=2) + "\n")
    make_figure(boards, fits, slots, f4, args.output / "aligned_global_timeline.png")
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument("--run-state", type=Path, required=True)
    parser.add_argument("--analysis", type=Path, required=True)
    parser.add_argument("--listener-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    run(parser.parse_args())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
