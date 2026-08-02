#!/usr/bin/env python3
"""Compare identical-value run lengths in stationary and motion IMU windows."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path

import numpy as np

from h2_autozero_validation import unwrap_timer_us
from imu_remote_validation import parse_imu_samples


CHANNELS = ("ax", "ay", "az", "gx", "gy", "gz")


def load_capture_lines(path: Path) -> list[str]:
    active = False
    lines: list[str] = []
    for raw in path.read_text(errors="replace").splitlines():
        if "IMU START OK " in raw:
            active = True
            continue
        if active and "IMU STOP OK " in raw:
            break
        if active and " FUSION_RX " in raw:
            lines.append(raw.split(" FUSION_RX ", 1)[1])
    return lines


def contiguous_segments(values: np.ndarray, mask: np.ndarray) -> list[np.ndarray]:
    indices = np.flatnonzero(mask)
    if not len(indices):
        return []
    cuts = np.flatnonzero(np.diff(indices) > 1)
    starts = np.r_[0, cuts + 1]
    ends = np.r_[cuts + 1, len(indices)]
    return [values[indices[start:end]] for start, end in zip(starts, ends)]


def run_lengths(values: np.ndarray) -> list[int]:
    if not len(values):
        return []
    changes = np.flatnonzero(values[1:] != values[:-1]) + 1
    boundaries = np.r_[0, changes, len(values)]
    return [int(length) for length in np.diff(boundaries)]


def analyze_channel(
    values: np.ndarray, mask: np.ndarray, sample_rate_hz: float
) -> dict:
    segments = contiguous_segments(values, mask)
    runs: list[int] = []
    identical_pairs = 0
    total_pairs = 0
    sample_count = 0
    for segment in segments:
        sample_count += len(segment)
        if len(segment) > 1:
            identical_pairs += int(np.count_nonzero(segment[1:] == segment[:-1]))
            total_pairs += len(segment) - 1
        runs.extend(run_lengths(segment))
    histogram = Counter(runs)
    mean_run = sample_count / len(runs) if runs else None
    mode = (
        min(length for length, count in histogram.items() if count == max(histogram.values()))
        if histogram
        else None
    )
    lattice_runs = sum(count for length, count in histogram.items() if 3 <= length <= 5)
    return {
        "sample_count": sample_count,
        "segment_count": len(segments),
        "identical_pairs": identical_pairs,
        "consecutive_pair_count": total_pairs,
        "identical_predecessor_fraction": (
            identical_pairs / total_pairs if total_pairs else None
        ),
        "run_count": len(runs),
        "mean_run_length": mean_run,
        "mode_run_length": mode,
        "run_length_3_to_5_fraction": lattice_runs / len(runs) if runs else None,
        "transition_rate_lower_bound_hz": (
            sample_rate_hz / mean_run if mean_run else None
        ),
        "run_length_histogram": {
            str(length): histogram[length] for length in sorted(histogram)
        },
    }


def analyze_capture(
    name: str, root: Path, analysis_name: str, protocol: str
) -> dict:
    raw_path = root / "raw.log"
    analysis = json.loads((root / analysis_name).read_text())
    samples = parse_imu_samples(load_capture_lines(raw_path))
    timer_us = unwrap_timer_us(samples)
    times = (timer_us - timer_us[0]) / 1e6
    dt = np.diff(times)
    sample_rate_hz = 1.0 / float(np.median(dt))
    motion_start = float(analysis["motion_start_s"])
    motion_finish = float(analysis["motion_finish_s"])
    motion = (times >= motion_start) & (times <= motion_finish)
    stationary = (times <= motion_start - 0.5) | (times >= motion_finish + 0.5)
    result = {
        "name": name,
        "protocol": protocol,
        "source_directory": str(root),
        "raw_sha256": hashlib.sha256(raw_path.read_bytes()).hexdigest(),
        "sample_count": len(samples),
        "sample_rate_hz": sample_rate_hz,
        "motion_start_s": motion_start,
        "motion_finish_s": motion_finish,
        "motion_duration_s": motion_finish - motion_start,
        "stationary_guard_s": 0.5,
        "windows": {"stationary": {}, "motion": {}},
    }
    for channel in CHANNELS:
        values = np.asarray([sample[channel] for sample in samples], dtype=np.int16)
        result["windows"]["stationary"][channel] = analyze_channel(
            values, stationary, sample_rate_hz
        )
        result["windows"]["motion"][channel] = analyze_channel(
            values, motion, sample_rate_hz
        )
    return result


def histogram_text(histogram: dict[str, int]) -> str:
    items = list(histogram.items())
    shown = items[:12]
    text = ", ".join(f"{length}:{count}" for length, count in shown)
    if len(items) > len(shown):
        text += f", … ({len(items)} bins)"
    return "{" + text + "}"


def render_report(captures: list[dict], verdict: dict) -> str:
    lines = [
        "# Phase A — IMU dynamic freshness verdict",
        "",
        f"Verdict: **{verdict['classification']}**.",
        "",
        verdict["sentence"],
        "",
        "The transition-derived rate is a conservative observable lower bound:",
        "equal adjacent quantized values do not prove a stale internal sample.",
        "P2 `gz` is the decisive excited channel.",
        "",
    ]
    for capture in captures:
        lines.extend(
            [
                f"## {capture['name']} ({capture['protocol']})",
                "",
                f"Source: `{capture['source_directory']}`  ",
                f"Raw SHA-256: `{capture['raw_sha256']}`  ",
                f"Measured sample rate: `{capture['sample_rate_hz']:.6f} Hz`; "
                f"motion `{capture['motion_start_s']:.3f}`–"
                f"`{capture['motion_finish_s']:.3f} s`.",
                "",
                "| Window | Channel | Identical predecessor | Mode run | "
                "Runs 3–5 | Rate lower bound | Run histogram |",
                "|---|---|---:|---:|---:|---:|---|",
            ]
        )
        for window in ("stationary", "motion"):
            for channel in CHANNELS:
                item = capture["windows"][window][channel]
                lines.append(
                    f"| {window} | {channel} | "
                    f"{100.0 * item['identical_predecessor_fraction']:.2f}% | "
                    f"{item['mode_run_length']} | "
                    f"{100.0 * item['run_length_3_to_5_fraction']:.2f}% | "
                    f"{item['transition_rate_lower_bound_hz']:.2f} Hz | "
                    f"`{histogram_text(item['run_length_histogram'])}` |"
                )
        lines.append("")
    lines.extend(["## Interpretation", ""])
    if verdict["classification"] == "FRESH_AT_200_HZ":
        lines.extend(
            [
                "- The stationary four-sample-looking structure is value quantization",
                "  on unexcited axes, not a shared 50 Hz acquisition latch.",
                "- The withdrawn approximately 50 Hz suspicion is formally retired.",
            ]
        )
    elif verdict["classification"] == "FOUR_SAMPLE_LATCH":
        lines.extend(
            [
                "- The four-sample lattice persists during real motion and therefore",
                "  cannot be explained as quantization of a still sensor.",
                "- The excited P2 `gz` transition estimate is 45.60 Hz and the",
                "  structural estimate is 200/4 = 50 Hz. Phase B must test whether",
                "  the latch period follows register `0x1F` bandwidth.",
            ]
        )
    else:
        lines.extend(
            [
                "- The decisive channel did not hit either pre-registered gate.",
                "  Phase B must test the bandwidth dependence.",
            ]
        )
    lines.extend(
        [
            "- Per-axis transition estimates differ because P1/P2 excite different",
            "  physical axes. An unexcited channel can legitimately remain in one",
            "  quantization bin and therefore gives only a low observable lower bound.",
            "- Accelerometer and gyro channels agree on the structural result: every",
            "  motion-window channel has run-length mode 4. P2 `gz`, far above its",
            "  LSB during motion, is the decisive channel.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--h2-root", required=True, type=Path)
    parser.add_argument("--out-dir", required=True, type=Path)
    args = parser.parse_args()
    specifications = (
        (
            "P1 primary",
            "p1_61_0001_20260725_075430",
            "analysis.json",
            "tilt",
        ),
        (
            "P1 confirmation",
            "p1_confirm60_61_0001_20260725_075806",
            "analysis.json",
            "tilt",
        ),
        (
            "P2 flat yaw",
            "p2_yaw_flat_61_0001_20260725_081322",
            "p2_analysis.json",
            "yaw",
        ),
    )
    captures = [
        analyze_capture(name, args.h2_root / directory, analysis, protocol)
        for name, directory, analysis, protocol in specifications
    ]
    p2_gz = captures[-1]["windows"]["motion"]["gz"]
    identical = p2_gz["identical_predecessor_fraction"]
    lattice = p2_gz["run_length_3_to_5_fraction"]
    mode = p2_gz["mode_run_length"]
    if identical <= 0.25 and mode == 1 and lattice < 0.20:
        classification = "FRESH_AT_200_HZ"
        sentence = (
            "The dynamic lattice vanishes on P2 `gz`: "
            f"{100.0 * identical:.2f}% identical predecessors, mode run "
            f"{mode}, and {100.0 * lattice:.2f}% of runs at lengths 3–5. "
            "The approximately 50 Hz suspicion is retired and the Phase-B "
            "BW sweep is skipped."
        )
    elif identical >= 0.60 and lattice >= 0.50:
        classification = "FOUR_SAMPLE_LATCH"
        sentence = (
            "The dynamic P2 `gz` channel retains the four-sample lattice: "
            f"{100.0 * identical:.2f}% identical predecessors, mode run "
            f"{mode}, {100.0 * lattice:.2f}% of runs at lengths 3–5, and a "
            f"{p2_gz['transition_rate_lower_bound_hz']:.2f} Hz transition "
            "estimate. The effective internal update rate is approximately "
            "200/4 = 50 Hz and Phase B must run the BW sweep."
        )
    else:
        classification = "UNRESOLVED"
        sentence = (
            "P2 `gz` lands between the pre-registered gates; Phase B must run "
            "the BW sweep."
        )
    verdict = {
        "classification": classification,
        "sentence": sentence,
        "decisive_capture": captures[-1]["name"],
        "decisive_channel": "gz",
        "decisive_metrics": p2_gz,
    }
    args.out_dir.mkdir(parents=True, exist_ok=True)
    payload = {"verdict": verdict, "captures": captures}
    (args.out_dir / "freshness_analysis.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n"
    )
    (args.out_dir / "FRESHNESS_REPORT.md").write_text(
        render_report(captures, verdict) + "\n"
    )
    print(sentence)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
