#!/usr/bin/env python3
"""Offline BioSpur TIMER2 alignment prototype.

The parser reads an existing Fusion CDC text log without modifying it.  The
public API is ``align_log``.  The command-line entry point emits only derived
artifacts beneath the caller-supplied output directory.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, Mapping, Optional

import numpy as np

GRID_US = 110_000.0
SLOT_US = 10_000.0
UINT32 = 1 << 32

UWB_RE = re.compile(
    r"name=(BSF[0-9A-F]+).*?sweep=(\d+).*?poll_tx=([0-9A-F]+)"
    r".*?frame_us=(\d+)"
)
IMU_RE = re.compile(
    r"name=(BSF[0-9A-F]+).*?seq=(\d+).*?base_us=(\d+) n=(\d+)"
    r".*?samples=(.*)$"
)
TELEMETRY_RE = re.compile(
    r"name=(BSF[0-9A-F]+).*?imu_hreset=(\d+).*?imu_hrecover_ok=(\d+)"
)


@dataclass
class BoardStreams:
    name: str
    sweep_raw: list[int] = field(default_factory=list)
    frame_us: list[int] = field(default_factory=list)
    poll_tx_raw: list[int] = field(default_factory=list)
    uwb_host_s: list[float] = field(default_factory=list)
    imu_seq: list[int] = field(default_factory=list)
    imu_us: list[int] = field(default_factory=list)
    imu_batch_host_s: list[float] = field(default_factory=list)
    imu_batch_first_index: list[int] = field(default_factory=list)
    hreset_events_host_s: list[float] = field(default_factory=list)


@dataclass
class ClockModel:
    name: str
    slot: int
    reference_sweep: float
    reference_timer_us: float
    slope_us_per_sweep: float
    drift_ppm: float
    residual_std_us: float
    residual_p95_abs_us: float
    residual_max_abs_us: float
    epochs: int
    gap_events: int
    missing_epochs: int
    c_mod_us: float
    c_phase_std_us: float

    def timer_to_grid_us(self, timer_us: np.ndarray | float) -> np.ndarray:
        """Map one board's TIMER2 to its sweep-numbered 110 ms coordinate.

        The integer epoch origin remains board-local because the wire record has
        no beacon/superframe index.  This is intentional and reported by the
        dry run rather than hidden in the API.
        """
        timer = np.asarray(timer_us, dtype=np.float64)
        return (
            self.reference_sweep * GRID_US
            + (timer - self.reference_timer_us) * GRID_US / self.slope_us_per_sweep
        )


@dataclass
class AlignmentResult:
    streams: Dict[str, BoardStreams]
    models: Dict[str, ClockModel]
    aligned_uwb_us: Dict[str, np.ndarray]
    aligned_imu_us: Dict[str, np.ndarray]
    formal_start_s: float
    formal_end_s: float


def unwrap_u32(values: Iterable[int]) -> tuple[np.ndarray, int, int]:
    """Unwrap an ordered uint32 counter and count forward gaps.

    A delta in (0, 2**31) is forward, including a natural wrap.  Zero and
    backward deltas are rejected because they cannot define a clock fit.
    """
    raw = np.asarray(list(values), dtype=np.uint64)
    if raw.size == 0:
        return np.array([], dtype=np.int64), 0, 0
    out = np.empty(raw.size, dtype=np.int64)
    out[0] = int(raw[0])
    gap_events = 0
    missing = 0
    for idx in range(1, raw.size):
        delta = (int(raw[idx]) - int(raw[idx - 1])) & 0xFFFFFFFF
        if delta == 0 or delta >= (1 << 31):
            raise ValueError(f"non-forward uint32 transition at index {idx}: {delta}")
        out[idx] = out[idx - 1] + delta
        if delta > 1:
            gap_events += 1
            missing += delta - 1
    return out, gap_events, missing


def _weighted_line(x: np.ndarray, y: np.ndarray, w: np.ndarray) -> tuple[float, float]:
    sw = float(np.sum(w))
    xm = float(np.sum(w * x) / sw)
    ym = float(np.sum(w * y) / sw)
    slope = float(np.sum(w * (x - xm) * (y - ym)) / np.sum(w * (x - xm) ** 2))
    return ym - slope * xm, slope


def robust_linear_fit(x: np.ndarray, y: np.ndarray) -> tuple[float, float, np.ndarray]:
    """Huber IRLS line fit; returned residuals always include every observation."""
    x = np.asarray(x, dtype=np.float64)
    y = np.asarray(y, dtype=np.float64)
    if x.size < 3 or x.size != y.size:
        raise ValueError("fit needs at least three paired observations")
    xc = float(np.median(x))
    xs = max(float(np.ptp(x)), 1.0)
    z = (x - xc) / xs
    w = np.ones_like(z)
    intercept_z, slope_z = _weighted_line(z, y, w)
    for _ in range(20):
        residual = y - (intercept_z + slope_z * z)
        center = float(np.median(residual))
        scale = 1.4826 * float(np.median(np.abs(residual - center)))
        if scale < 1e-9:
            break
        u = np.abs(residual - center) / (1.345 * scale)
        new_w = np.ones_like(u)
        tail = u > 1.0
        new_w[tail] = 1.0 / u[tail]
        new_intercept, new_slope = _weighted_line(z, y, new_w)
        if abs(new_slope - slope_z) < 1e-9 and abs(new_intercept - intercept_z) < 1e-6:
            intercept_z, slope_z = new_intercept, new_slope
            break
        intercept_z, slope_z, w = new_intercept, new_slope, new_w
    slope = slope_z / xs
    intercept = intercept_z - slope * xc
    residual = y - (intercept + slope * x)
    return intercept, slope, residual


def circular_location_us(values: np.ndarray, period_us: float = GRID_US) -> tuple[float, float]:
    values = np.mod(np.asarray(values, dtype=np.float64), period_us)
    angle = 2.0 * np.pi * values / period_us
    mean_angle = math.atan2(float(np.sin(angle).mean()), float(np.cos(angle).mean()))
    center = (mean_angle % (2.0 * np.pi)) * period_us / (2.0 * np.pi)
    unwrapped = center + np.mod(values - center + period_us / 2.0, period_us) - period_us / 2.0
    return float(np.median(unwrapped) % period_us), float(np.std(unwrapped))


def circular_spread_us(values: Iterable[float], period_us: float = GRID_US) -> float:
    vals = np.sort(np.mod(np.asarray(list(values), dtype=np.float64), period_us))
    if vals.size < 2:
        return 0.0
    gaps = np.diff(np.r_[vals, vals[0] + period_us])
    return float(period_us - np.max(gaps))


def _parse_host_monotonic(line: str) -> Optional[float]:
    parts = line.split(" ", 3)
    if len(parts) < 4:
        return None
    try:
        return float(parts[1])
    except ValueError:
        return None


def extract_log(
    log_path: str | Path,
    start_s: float,
    end_s: float,
) -> Dict[str, BoardStreams]:
    streams: Dict[str, BoardStreams] = {}
    last_hreset: Dict[str, int] = {}
    with Path(log_path).open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            host_s = _parse_host_monotonic(line)
            if host_s is None or host_s < start_s or host_s > end_s:
                continue
            if "FUSION_UWB " in line:
                match = UWB_RE.search(line)
                if not match:
                    continue
                name, sweep, poll_tx, frame_us = match.groups()
                board = streams.setdefault(name, BoardStreams(name))
                board.sweep_raw.append(int(sweep))
                board.poll_tx_raw.append(int(poll_tx, 16))
                board.frame_us.append(int(frame_us))
                board.uwb_host_s.append(host_s)
            elif "FUSION_IMU " in line:
                match = IMU_RE.search(line)
                if not match:
                    continue
                name, seq, base_us, declared_n, payload = match.groups()
                board = streams.setdefault(name, BoardStreams(name))
                sample_fields = payload.split(";") if payload else []
                if len(sample_fields) != int(declared_n):
                    raise ValueError(
                        f"{name} IMU n={declared_n} but decoded {len(sample_fields)} samples"
                    )
                board.imu_batch_host_s.append(host_s)
                board.imu_batch_first_index.append(len(board.imu_us))
                for offset, sample in enumerate(sample_fields):
                    delta_us = int(sample.split(",", 1)[0])
                    board.imu_seq.append((int(seq) + offset) & 0xFFFFFFFF)
                    board.imu_us.append(int(base_us) + delta_us)
            elif "FUSION_TELEMETRY " in line:
                match = TELEMETRY_RE.search(line)
                if not match:
                    continue
                name, hreset, _ = match.groups()
                board = streams.setdefault(name, BoardStreams(name))
                current = int(hreset)
                previous = last_hreset.get(name, current)
                if current > previous:
                    board.hreset_events_host_s.extend([host_s] * (current - previous))
                last_hreset[name] = current
    return streams


def fit_board(board: BoardStreams, slot: int) -> tuple[ClockModel, np.ndarray, np.ndarray]:
    sweep, gap_events, missing = unwrap_u32(board.sweep_raw)
    timer = np.asarray(board.frame_us, dtype=np.float64)
    intercept, slope, residual = robust_linear_fit(sweep.astype(np.float64), timer)
    centered = residual - float(np.median(residual))
    phases = timer - slope * sweep - slot * SLOT_US
    c_mod, c_std = circular_location_us(phases)
    ref_sweep = float(np.median(sweep))
    ref_timer = float(intercept + slope * ref_sweep)
    model = ClockModel(
        name=board.name,
        slot=slot,
        reference_sweep=ref_sweep,
        reference_timer_us=ref_timer,
        slope_us_per_sweep=slope,
        drift_ppm=(slope / GRID_US - 1.0) * 1e6,
        residual_std_us=float(np.std(centered)),
        residual_p95_abs_us=float(np.percentile(np.abs(centered), 95)),
        residual_max_abs_us=float(np.max(np.abs(centered))),
        epochs=len(sweep),
        gap_events=gap_events,
        missing_epochs=missing,
        c_mod_us=c_mod,
        c_phase_std_us=c_std,
    )
    return model, sweep, residual


def align_log(
    log_path: str | Path,
    slots: Mapping[str, int],
    start_s: float,
    end_s: float,
) -> AlignmentResult:
    streams = extract_log(log_path, start_s, end_s)
    models: Dict[str, ClockModel] = {}
    aligned_uwb: Dict[str, np.ndarray] = {}
    aligned_imu: Dict[str, np.ndarray] = {}
    missing = sorted(set(slots) - set(streams))
    if missing:
        raise ValueError(f"slot-map boards absent from formal window: {missing}")
    for name, slot in slots.items():
        board = streams[name]
        model, _, _ = fit_board(board, slot)
        models[name] = model
        aligned_uwb[name] = model.timer_to_grid_us(np.asarray(board.frame_us))
        aligned_imu[name] = model.timer_to_grid_us(np.asarray(board.imu_us))
    return AlignmentResult(streams, models, aligned_uwb, aligned_imu, start_s, end_s)


def sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_csv(path: Path, fieldnames: list[str], rows: list[dict]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _hreset_neighborhood(board: BoardStreams) -> tuple[int, float]:
    if not board.hreset_events_host_s or not board.imu_batch_host_s:
        return len(board.hreset_events_host_s), 0.0
    imu = np.asarray(board.imu_us, dtype=np.float64)
    batch_host = np.asarray(board.imu_batch_host_s)
    first = np.asarray(board.imu_batch_first_index)
    maxima = []
    for event_s in board.hreset_events_host_s:
        batch_idx = int(np.argmin(np.abs(batch_host - event_s)))
        sample_idx = int(first[batch_idx])
        lo = max(1, sample_idx - 30)
        hi = min(len(imu), sample_idx + 31)
        maxima.append(float(np.max(np.abs(np.diff(imu[lo - 1 : hi]) - 5000.0))))
    return len(maxima), max(maxima, default=0.0)


def generate_artifacts(
    result: AlignmentResult,
    expected: Mapping[str, Mapping[str, int]],
    output_dir: str | Path,
) -> dict:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    table1, table2, table3, counts = [], [], [], []
    for name in sorted(result.models):
        model = result.models[name]
        board = result.streams[name]
        exp = expected.get(name, {})
        counts.append(
            {
                "name": name,
                "uwb_extracted": len(board.frame_us),
                "uwb_r4": exp.get("uwb", ""),
                "uwb_match": len(board.frame_us) == exp.get("uwb", len(board.frame_us)),
                "imu_extracted": len(board.imu_us),
                "imu_r4": exp.get("imu", ""),
                "imu_match": len(board.imu_us) == exp.get("imu", len(board.imu_us)),
            }
        )
        table1.append(
            {
                "name": name,
                "slot": model.slot,
                "reference_sweep": f"{model.reference_sweep:.1f}",
                "reference_timer_us": f"{model.reference_timer_us:.3f}",
                "slope_us_per_sweep": f"{model.slope_us_per_sweep:.6f}",
                "drift_ppm": f"{model.drift_ppm:.3f}",
                "residual_std_us": f"{model.residual_std_us:.3f}",
                "residual_p95_abs_us": f"{model.residual_p95_abs_us:.3f}",
                "residual_max_abs_us": f"{model.residual_max_abs_us:.3f}",
                "residual_abs_gt_1ms_pct": "",
                "residual_abs_gt_2ms_pct": "",
                "epochs": model.epochs,
                "gap_events": model.gap_events,
                "missing_epochs": model.missing_epochs,
                "p95_gate_lt_1000": model.residual_p95_abs_us < 1000.0,
            }
        )
        sweep_unwrapped, _, _ = unwrap_u32(board.sweep_raw)
        _, _, fit_residual = robust_linear_fit(
            sweep_unwrapped.astype(np.float64), np.asarray(board.frame_us, dtype=np.float64)
        )
        fit_residual -= np.median(fit_residual)
        table1[-1]["residual_abs_gt_1ms_pct"] = f"{100.0 * float(np.mean(np.abs(fit_residual) > 1000.0)):.3f}"
        table1[-1]["residual_abs_gt_2ms_pct"] = f"{100.0 * float(np.mean(np.abs(fit_residual) > 2000.0)):.3f}"
        table2.append(
            {
                "name": name,
                "slot": model.slot,
                "slot_offset_us": f"{model.slot * SLOT_US:.0f}",
                "c_mod_110ms_us": f"{model.c_mod_us:.3f}",
                "within_board_phase_std_us": f"{model.c_phase_std_us:.3f}",
            }
        )
        aligned = result.aligned_imu_us[name]
        intervals = np.diff(aligned)
        hreset_count, hreset_max_error = _hreset_neighborhood(board)
        uwb_span = float(result.aligned_uwb_us[name][-1] - result.aligned_uwb_us[name][0])
        imu_span = float(aligned[-1] - aligned[0])
        table3.append(
            {
                "name": name,
                "samples": len(aligned),
                "mean_interval_us": f"{float(np.mean(intervals)):.3f}",
                "std_interval_us": f"{float(np.std(intervals)):.3f}",
                "p99_abs_from_5000_us": f"{float(np.percentile(np.abs(intervals - 5000.0), 99)):.3f}",
                "max_abs_from_5000_us": f"{float(np.max(np.abs(intervals - 5000.0))):.3f}",
                "intervals_gt_7500_us": int(np.sum(intervals > 7500.0)),
                "coverage_vs_uwb_span": f"{imu_span / uwb_span:.6f}",
                "hreset_events": hreset_count,
                "hreset_neighborhood_max_local_error_us": f"{hreset_max_error:.3f}",
            }
        )
    _write_csv(out / "extraction_counts.csv", list(counts[0]), counts)
    _write_csv(out / "table1_clock_fits.csv", list(table1[0]), table1)
    _write_csv(out / "table2_cross_board_constants.csv", list(table2[0]), table2)
    _write_csv(out / "table3_imu_remap.csv", list(table3[0]), table3)

    models_json = {
        "formal_window": {"start_monotonic": result.formal_start_s, "end_monotonic": result.formal_end_s},
        "grid_us": GRID_US,
        "slot_us": SLOT_US,
        "integer_epoch_identifiable_across_boards": False,
        "reason": "kind-1 carries no beacon/superframe index and DK master_arrival_ms is excluded",
        "c_minimal_circular_spread_us": circular_spread_us(m.c_mod_us for m in result.models.values()),
        "models": {name: vars(model) for name, model in sorted(result.models.items())},
    }
    (out / "models.json").write_text(json.dumps(models_json, indent=2) + "\n", encoding="utf-8")
    return {
        "counts": counts,
        "table1": table1,
        "table2": table2,
        "table3": table3,
        "models": models_json,
    }


def make_figure(result: AlignmentResult, path: str | Path, seconds: float = 3.0) -> None:
    """Draw a diagnostic relative-time panel without inventing a global epoch."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    names = sorted(result.models, key=lambda n: result.models[n].slot)
    fig, ax = plt.subplots(figsize=(14, 7))
    for row, name in enumerate(names):
        # The wire format cannot resolve a shared integer epoch.  Normalize each
        # board at its first formal UWB record and say so in both plot and report.
        uwb = result.aligned_uwb_us[name]
        imu = result.aligned_imu_us[name]
        origin = uwb[0] - result.models[name].slot * SLOT_US
        u = (uwb - origin) / 1e6
        i = (imu - origin) / 1e6
        u = u[(u >= 0) & (u <= seconds)]
        i = i[(i >= 0) & (i <= seconds)]
        ax.vlines(u, row + 0.05, row + 0.43, color="#1f77b4", lw=1.0)
        ax.scatter(i, np.full(i.size, row - 0.13), s=1.2, color="#ff7f0e", alpha=0.75)
    ax.set_yticks(range(len(names)), names)
    ax.set_xlim(0, seconds)
    ax.set_xlabel("Board-local aligned time since that board's first formal UWB epoch (s)")
    ax.set_title("R4 UWB ticks (blue) and IMU samples (orange)\n"
                 "Diagnostic only: no cross-board integer beacon epoch exists in kind-1")
    ax.grid(axis="x", alpha=0.25)
    fig.tight_layout()
    fig.savefig(path, dpi=180)
    plt.close(fig)


def _load_inputs(run_state_path: Path, analysis_path: Path) -> tuple[dict, float, float, dict]:
    state = json.loads(run_state_path.read_text(encoding="utf-8"))
    analysis = json.loads(analysis_path.read_text(encoding="utf-8"))
    slots = {name: int(value) for name, value in state["slot_map"].items()}
    formal = analysis["formal_window"]
    expected = {
        name: {"uwb": node["uwb"]["records"], "imu": node["imu"]["samples"]}
        for name, node in analysis["nodes"].items()
    }
    return slots, float(formal["start_monotonic"]), float(formal["end_monotonic"]), expected


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument("--run-state", type=Path, required=True)
    parser.add_argument("--analysis", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    slots, start_s, end_s, expected = _load_inputs(args.run_state, args.analysis)
    result = align_log(args.log, slots, start_s, end_s)
    generate_artifacts(result, expected, args.output)
    make_figure(result, args.output / "aligned_streams_diagnostic.png")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
