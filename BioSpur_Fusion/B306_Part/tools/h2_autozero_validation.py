#!/usr/bin/env python3
"""Self-quantifying H2 gyro auto-zero validation over Fusion Master CDC."""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from fusion_session import (
    ANOMALY_COUNTERS,
    FusionController,
    LineChannel,
    SessionError,
    counter_deltas,
    resolve_fusion_port,
)
from imu_remote_validation import imu_sequence_audit, parse_imu_samples


ACC_SCALE_G = 16.0 / 32768.0
GYRO_SCALE_DPS = 2000.0 / 32768.0


def json_default(item):
    if isinstance(item, np.generic):
        return item.item()
    raise TypeError(f"Object of type {type(item).__name__} is not JSON serializable")


def write_json(path: Path, value) -> None:
    path.write_text(
        json.dumps(
            value,
            indent=2,
            sort_keys=True,
            default=json_default,
        )
        + "\n"
    )


def command(controller: FusionController, text: str, prefix: str):
    return controller.command(
        text,
        lambda reply: reply.startswith(prefix),
        allow_resend_after_tx=False,
    )


def ensure_stopped(controller: FusionController) -> dict:
    status = command(controller, "IMU STATUS", "IMU ")
    if "active=0 " in f"{status.text} ":
        return {"status": status.__dict__, "stop": None}
    stopped = command(controller, "IMU STOP", "IMU STOP OK ")
    return {"status": status.__dict__, "stop": stopped.__dict__}


def moving_average_vectors(values: np.ndarray, samples: int) -> np.ndarray:
    if samples <= 1:
        return values.copy()
    kernel = np.ones(samples, dtype=float) / samples
    padded = np.pad(
        values,
        ((samples // 2, samples - 1 - samples // 2), (0, 0)),
        mode="edge",
    )
    return np.column_stack(
        [np.convolve(padded[:, axis], kernel, mode="valid") for axis in range(3)]
    )


def unit_vector(vector: np.ndarray) -> np.ndarray:
    norm = float(np.linalg.norm(vector))
    if norm < 1e-9:
        raise SessionError("zero-length vector in P1 orientation analysis")
    return vector / norm


def angle_from_reference(vectors: np.ndarray, reference: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(vectors, axis=1)
    valid = norms > 1e-9
    result = np.full(len(vectors), np.nan)
    dots = np.sum(vectors[valid] * reference, axis=1) / norms[valid]
    result[valid] = np.degrees(np.arccos(np.clip(dots, -1.0, 1.0)))
    return result


def unwrap_timer_us(samples: list[dict[str, int]]) -> np.ndarray:
    unwrapped = [int(samples[0]["timer_us"])]
    for sample in samples[1:]:
        previous_raw = unwrapped[-1] & 0xFFFFFFFF
        delta = (int(sample["timer_us"]) - previous_raw) & 0xFFFFFFFF
        if delta > 0x7FFFFFFF:
            raise SessionError(f"non-monotonic TIMER2 delta: {delta}")
        unwrapped.append(unwrapped[-1] + delta)
    return np.asarray(unwrapped, dtype=np.float64)


def first_crossing(
    times: np.ndarray, curve: np.ndarray, threshold: float, after_s: float = 0.0
) -> int:
    candidates = np.flatnonzero((times >= after_s) & (curve >= threshold))
    if not len(candidates):
        raise SessionError(f"accel angle never crossed {threshold:.2f} deg")
    return int(candidates[0])


def analyze_p1(
    samples: list[dict[str, int]],
    sequence: dict,
    baseline: dict[str, str],
    final: dict[str, str],
) -> tuple[dict, list[dict[str, float]]]:
    if len(samples) < 100:
        raise SessionError(f"too few P1 samples: {len(samples)}")

    timer_us = unwrap_timer_us(samples)
    times = (timer_us - timer_us[0]) / 1e6
    accel = np.asarray(
        [[sample["ax"], sample["ay"], sample["az"]] for sample in samples],
        dtype=float,
    ) * ACC_SCALE_G
    gyro = np.asarray(
        [[sample["gx"], sample["gy"], sample["gz"]] for sample in samples],
        dtype=float,
    ) * GYRO_SCALE_DPS

    dt_median = float(np.median(np.diff(times)))
    smooth_count = max(1, round(0.5 / dt_median))
    accel_smooth = moving_average_vectors(accel, smooth_count)

    initial_mask = times <= min(2.0, times[-1] * 0.1)
    final_mask = times >= max(times[-1] - 3.0, times[-1] * 0.9)
    initial_accel = np.mean(accel_smooth[initial_mask], axis=0)
    final_accel = np.mean(accel_smooth[final_mask], axis=0)
    initial_unit = unit_vector(initial_accel)
    final_unit = unit_vector(final_accel)
    accel_angle = angle_from_reference(accel_smooth, initial_unit)
    final_accel_angle = math.degrees(
        math.acos(float(np.clip(np.dot(initial_unit, final_unit), -1.0, 1.0)))
    )

    cross = np.cross(initial_unit, final_unit)
    if np.linalg.norm(cross) < math.sin(math.radians(20.0)):
        raise SessionError(
            f"P1 final tilt too small for a stable axis: {final_accel_angle:.2f} deg"
        )
    # Gravity expressed in board coordinates rotates opposite to the physical
    # board rotation. Gyro therefore projects on -cross(g_start, g_end).
    rotation_axis = -unit_vector(cross)
    gyro_projected = gyro @ rotation_axis

    start_threshold = max(3.0, final_accel_angle * 0.05)
    finish_threshold = final_accel_angle * 0.95
    motion_start_index = first_crossing(times, accel_angle, start_threshold)
    motion_finish_index = first_crossing(
        times, accel_angle, finish_threshold, times[motion_start_index]
    )
    bias_crossing_index = first_crossing(
        times, accel_angle, min(1.0, final_accel_angle * 0.01)
    )
    window_start_index = max(
        0, int(np.searchsorted(times, times[bias_crossing_index] - 0.5))
    )
    # Use the final stable hold as the common end point.
    window_end_index = len(times) - 1

    # The operator holds the board flat until capture is announced. Keep the
    # bias window strictly in that first second so smoothing around motion
    # onset cannot leak real rotation into the bias estimate.
    bias_mask = times <= 1.0
    if np.count_nonzero(bias_mask) < 10:
        raise SessionError(
            "P1 lacked a stationary flat pre-roll for gyro bias measurement"
        )
    gyro_bias_dps = float(np.mean(gyro_projected[bias_mask]))
    gyro_corrected = gyro_projected - gyro_bias_dps

    dt = np.diff(times)
    raw_step = 0.5 * (gyro_projected[:-1] + gyro_projected[1:]) * dt
    corrected_step = 0.5 * (gyro_corrected[:-1] + gyro_corrected[1:]) * dt
    gyro_raw_integral = np.concatenate(([0.0], np.cumsum(raw_step)))
    gyro_corrected_integral = np.concatenate(
        ([0.0], np.cumsum(corrected_step))
    )
    raw_window = (
        gyro_raw_integral[window_end_index]
        - gyro_raw_integral[window_start_index]
    )
    corrected_window = (
        gyro_corrected_integral[window_end_index]
        - gyro_corrected_integral[window_start_index]
    )
    accel_window = (
        accel_angle[window_end_index] - accel_angle[window_start_index]
    )
    integral_error = corrected_window - accel_window
    absolute_error = abs(integral_error)
    ratio = (
        corrected_window / accel_window if abs(accel_window) > 1e-6 else None
    )
    motion_duration = (
        times[motion_finish_index] - times[motion_start_index]
    )
    average_rate = (
        (accel_angle[motion_finish_index] - accel_angle[motion_start_index])
        / motion_duration
        if motion_duration > 0
        else None
    )

    protocol_valid = bool(70.0 <= final_accel_angle <= 110.0)
    sign_correct = bool(corrected_window > 0.0)
    if not protocol_valid:
        verdict = "INVALID_TILT"
    elif abs(corrected_window) < 0.10 * abs(accel_window):
        verdict = "AUTOZERO_EATEN"
    elif sign_correct and 0.50 <= ratio <= 1.50:
        verdict = "GYRO_SURVIVES"
    else:
        verdict = "AMBIGUOUS"

    anomaly_deltas = counter_deltas(baseline, final, ANOMALY_COUNTERS)
    node_dt_s = (
        (int(final["node_ms"], 0) - int(baseline["node_ms"], 0))
        & 0xFFFFFFFF
    ) / 1000.0
    frame_delta = (
        int(final["frames"], 0) - int(baseline["frames"], 0)
    ) & 0xFFFFFFFF

    analysis = {
        "verdict": verdict,
        "protocol_valid": protocol_valid,
        "sample_count": len(samples),
        "sequence": sequence,
        "timer_dt_median_ms": dt_median * 1000.0,
        "accel_initial_g": initial_accel.tolist(),
        "accel_final_g": final_accel.tolist(),
        "accel_final_angle_deg": final_accel_angle,
        "rotation_axis_gyro_frame": rotation_axis.tolist(),
        "motion_start_s": float(times[motion_start_index]),
        "motion_finish_s": float(times[motion_finish_index]),
        "motion_duration_s": float(motion_duration),
        "data_derived_average_rate_dps": float(average_rate),
        "comparison_window_start_s": float(times[window_start_index]),
        "comparison_window_end_s": float(times[window_end_index]),
        "comparison_accel_angle_deg": float(accel_window),
        "gyro_projected_bias_dps": gyro_bias_dps,
        "gyro_raw_integral_deg": float(raw_window),
        "gyro_bias_corrected_integral_deg": float(corrected_window),
        "gyro_integral_error_deg": float(integral_error),
        "gyro_integral_absolute_error_deg": float(absolute_error),
        "gyro_to_accel_ratio": float(ratio) if ratio is not None else None,
        "gyro_sign_correct": sign_correct,
        "pre_registered_thresholds": {
            "valid_accel_final_angle_deg": [70.0, 110.0],
            "autozero_eaten_abs_ratio_lt": 0.10,
            "gyro_survives_signed_ratio": [0.50, 1.50],
        },
        "uwb_frame_delta": frame_delta,
        "uwb_window_s": node_dt_s,
        "uwb_rate_hz": frame_delta / node_dt_s if node_dt_s else None,
        "anomaly_counter_deltas": anomaly_deltas,
    }

    curves = []
    for index, sample in enumerate(samples):
        curves.append(
            {
                "timer_s": float(times[index]),
                "seq": sample["seq"],
                "accel_angle_deg": float(accel_angle[index]),
                "gyro_projected_dps": float(gyro_projected[index]),
                "gyro_integral_raw_deg": float(
                    gyro_raw_integral[index]
                    - gyro_raw_integral[window_start_index]
                ),
                "gyro_integral_bias_corrected_deg": float(
                    gyro_corrected_integral[index]
                    - gyro_corrected_integral[window_start_index]
                ),
                "ax_g": float(accel[index, 0]),
                "ay_g": float(accel[index, 1]),
                "az_g": float(accel[index, 2]),
                "gx_dps": float(gyro[index, 0]),
                "gy_dps": float(gyro[index, 1]),
                "gz_dps": float(gyro[index, 2]),
            }
        )
    return analysis, curves


def write_curves(path: Path, curves: list[dict[str, float]]) -> None:
    with path.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(curves[0]))
        writer.writeheader()
        writer.writerows(curves)


def plot_curves(path: Path, curves: list[dict[str, float]], analysis: dict) -> None:
    times = [row["timer_s"] for row in curves]
    accel = [row["accel_angle_deg"] for row in curves]
    gyro = [row["gyro_integral_bias_corrected_deg"] for row in curves]
    rate = [row["gyro_projected_dps"] for row in curves]
    figure, axes = plt.subplots(2, 1, figsize=(11, 7), sharex=True)
    axes[0].plot(times, accel, label="accelerometer-derived tilt", linewidth=2)
    axes[0].plot(times, gyro, label="projected gyro integral", linewidth=1.5)
    axes[0].set_ylabel("angle (deg)")
    axes[0].grid(True, alpha=0.25)
    axes[0].legend()
    axes[0].set_title(
        f"P1 {analysis['verdict']}: error "
        f"{analysis['gyro_integral_error_deg']:.2f} deg"
    )
    axes[1].plot(times, rate, color="tab:orange")
    axes[1].set_xlabel("TIMER2 time (s)")
    axes[1].set_ylabel("projected gyro (deg/s)")
    axes[1].grid(True, alpha=0.25)
    figure.tight_layout()
    figure.savefig(path, dpi=160)
    plt.close(figure)


def reanalyze(out_dir: Path) -> int:
    summary_path = out_dir / "summary.json"
    summary = json.loads(summary_path.read_text())
    lines = []
    for raw in (out_dir / "raw.log").read_text(errors="replace").splitlines():
        marker = " FUSION_RX "
        if marker in raw:
            lines.append(raw.split(marker, 1)[1])
    samples = parse_imu_samples(lines)
    sequence = imu_sequence_audit(lines)
    analysis, curves = analyze_p1(
        samples, sequence, summary["baseline"], summary["final"]
    )
    write_json(out_dir / "analysis.json", analysis)
    write_curves(out_dir / "curves.csv", curves)
    plot_curves(out_dir / "curves.png", curves, analysis)
    summary["analysis"] = analysis
    summary["status"] = "COMPLETE"
    summary.pop("error", None)
    write_json(summary_path, summary)
    print(
        f"H2_P1_REANALYZED verdict={analysis['verdict']} "
        f"accel={analysis['comparison_accel_angle_deg']:.2f}deg "
        f"gyro={analysis['gyro_bias_corrected_integral_deg']:.2f}deg "
        f"error={analysis['gyro_integral_error_deg']:.2f}deg",
        flush=True,
    )
    return 0


def run(args) -> int:
    args.out_dir.mkdir(parents=True, exist_ok=False)
    predictions = {
        "written_before_hardware_open": True,
        "protocol": "P1",
        "instruction": (
            "Tilt the board slowly from flat to upright over about 30 seconds, "
            "then hold upright for 5 seconds."
        ),
        "candidate": {
            "register": f"{args.candidate_reg:02X}",
            "value": f"{args.candidate_value:04X}",
            "restore": f"{args.restore_value:04X}",
        },
        "duration_s": args.duration_s,
        "metric": (
            "projected gyro integral versus accelerometer-derived gravity "
            "angle over the same TIMER2 window"
        ),
        "thresholds": {
            "valid_accel_final_angle_deg": [70.0, 110.0],
            "autozero_eaten_abs_ratio_lt": 0.10,
            "gyro_survives_signed_ratio": [0.50, 1.50],
        },
    }
    write_json(args.out_dir / "predictions.json", predictions)
    summary: dict = {"status": "IN_PROGRESS", "predictions": predictions}
    channel = None
    controller = None
    imu_started = False
    candidate_applied = False
    lines: list[str] = []
    try:
        with (args.out_dir / "raw.log").open("a", buffering=1) as raw_log:
            port = resolve_fusion_port(args.port)
            summary["fusion_port"] = port
            channel = LineChannel(port, raw_log, "FUSION")
            controller = FusionController(
                channel, args.bsf, args.timeout, args.max_attempts
            )
            summary["bridge"] = controller.ensure_bridge()
            summary["stopped"] = ensure_stopped(controller)
            summary["baseline_reg"] = command(
                controller,
                f"IMU REG={args.candidate_reg:02X}",
                "IMU REG OK ",
            ).__dict__
            applied = command(
                controller,
                f"IMU REG={args.candidate_reg:02X} "
                f"VAL={args.candidate_value:04X}",
                "IMU REG OK ",
            )
            summary["candidate_applied"] = applied.__dict__
            required = (
                f"addr={args.candidate_reg:02X}",
                f"request={args.candidate_value:04X}",
                f"readback={args.candidate_value:04X}",
                "volatile=1",
                "saved=0",
            )
            if any(token not in applied.text for token in required):
                raise SessionError(f"candidate readback mismatch: {applied.text}")
            candidate_applied = True
            summary["candidate_verify"] = command(
                controller,
                f"IMU REG={args.candidate_reg:02X}",
                "IMU REG OK ",
            ).__dict__
            summary["rate"] = command(
                controller, "IMU RATE=200", "IMU RATE OK "
            ).__dict__
            summary["batch"] = command(
                controller, "IMU BATCH=2", "IMU BATCH OK "
            ).__dict__
            baseline = controller.wait_telemetry()
            summary["baseline"] = baseline
            summary["start"] = controller.command(
                "IMU START",
                lambda text: (
                    text.startswith("IMU START OK ")
                    and "61=0001:P" in text
                    and "03=000B:P" in text
                    and "1F=0002:P" in text
                    and "volatile=1" in text
                    and "saved=0" in text
                ),
                allow_resend_after_tx=False,
            ).__dict__
            imu_started = True
            print(
                f"H2_P1_CAPTURE_ACTIVE duration={args.duration_s:.1f}s "
                f"candidate={args.candidate_reg:02X}:{args.candidate_value:04X}",
                flush=True,
            )
            lines = controller.collect(args.duration_s)
            final = controller.latest_telemetry
            if final is None:
                raise SessionError("P1 capture lacked final telemetry")
            summary["final"] = final
            summary["stop"] = command(
                controller, "IMU STOP", "IMU STOP OK "
            ).__dict__
            imu_started = False
            restored = command(
                controller,
                f"IMU REG={args.candidate_reg:02X} "
                f"VAL={args.restore_value:04X}",
                "IMU REG OK ",
            )
            summary["restore"] = restored.__dict__
            candidate_applied = False
            expected_restore = f"readback={args.restore_value:04X}"
            if expected_restore not in restored.text:
                raise SessionError(f"restore readback mismatch: {restored.text}")
            summary["restore_verify"] = command(
                controller,
                f"IMU REG={args.candidate_reg:02X}",
                "IMU REG OK ",
            ).__dict__

            samples = parse_imu_samples(lines)
            sequence = imu_sequence_audit(lines)
            analysis, curves = analyze_p1(samples, sequence, baseline, final)
            write_json(args.out_dir / "analysis.json", analysis)
            write_curves(args.out_dir / "curves.csv", curves)
            plot_curves(args.out_dir / "curves.png", curves, analysis)
            summary["analysis"] = analysis
            summary["status"] = "COMPLETE"
            print(
                f"H2_P1_COMPLETE verdict={analysis['verdict']} "
                f"accel={analysis['comparison_accel_angle_deg']:.2f}deg "
                f"gyro={analysis['gyro_bias_corrected_integral_deg']:.2f}deg "
                f"error={analysis['gyro_integral_error_deg']:.2f}deg",
                flush=True,
            )
    except Exception as exc:
        summary["status"] = "FAILED"
        summary["error"] = str(exc)
        raise
    finally:
        if controller is not None and imu_started:
            try:
                summary["rollback_stop"] = command(
                    controller, "IMU STOP", "IMU STOP "
                ).__dict__
            except Exception as exc:
                summary["rollback_stop_error"] = str(exc)
        if controller is not None and candidate_applied:
            try:
                summary["rollback_restore"] = command(
                    controller,
                    f"IMU REG={args.candidate_reg:02X} "
                    f"VAL={args.restore_value:04X}",
                    "IMU REG ",
                ).__dict__
            except Exception as exc:
                summary["rollback_restore_error"] = str(exc)
        if channel is not None:
            channel.close()
        write_json(args.out_dir / "summary.json", summary)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument("--bsf", default="BSF3C79")
    parser.add_argument("--port")
    parser.add_argument("--duration-s", type=float, default=45.0)
    parser.add_argument(
        "--candidate-reg", type=lambda text: int(text, 16), default=0x61
    )
    parser.add_argument(
        "--candidate-value", type=lambda text: int(text, 16), default=0x0001
    )
    parser.add_argument(
        "--restore-value", type=lambda text: int(text, 16), default=0x0000
    )
    parser.add_argument("--timeout", type=float, default=8.0)
    parser.add_argument("--max-attempts", type=int, default=3)
    parser.add_argument(
        "--reanalyze",
        action="store_true",
        help="analyze an already captured out-dir without opening hardware",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        if args.reanalyze:
            return reanalyze(args.out_dir)
        return run(args)
    except SessionError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
