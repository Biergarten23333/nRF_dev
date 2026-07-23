#!/usr/bin/env python3
"""Remote IMU validation over the explicitly selected Fusion Master RTT link."""

from __future__ import annotations

import argparse
import json
import math
import re
import statistics
import time
from pathlib import Path

from fusion_session import (
    ANOMALY_COUNTERS,
    FusionController,
    RttLineChannel,
    SessionError,
    counter_deltas,
    parse_fields,
)


SELFTEST_RE = re.compile(
    r"IMU SELFTEST (?P<verdict>PASS|FAIL) err=(?P<err>-?\d+) "
    r"ax=(?P<ax>-?\d+) ay=(?P<ay>-?\d+) az=(?P<az>-?\d+) "
    r"gx=(?P<gx>-?\d+) gy=(?P<gy>-?\d+) gz=(?P<gz>-?\d+) "
    r"temp=(?P<temp>-?\d+) chip_ms=(?P<c0>\d+)/(?P<c1>\d+)/(?P<c2>\d+)"
)


def write_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def open_controller(args, raw_log):
    channel = RttLineChannel(
        serial_number=args.serial_number,
        device=args.device,
        address=args.address,
        speed_khz=args.speed_khz,
        up_channel=0,
        down_channel=0,
        log_file=raw_log,
        label="FUSION_RTT",
    )
    controller = FusionController(
        channel, args.bsf, args.timeout, args.max_attempts
    )
    return channel, controller


def command(controller, text: str, prefix: str):
    return controller.command(
        text,
        lambda reply: reply.startswith(prefix),
        allow_resend_after_tx=False,
    )


def ensure_stopped(controller) -> dict:
    status = command(controller, "IMU STATUS", "IMU ")
    if "active=0 " in f"{status.text} ":
        return {"status": status.__dict__, "stop": None}
    stopped = command(controller, "IMU STOP", "IMU STOP OK ")
    return {"status": status.__dict__, "stop": stopped.__dict__}


def collect_selftests(controller, count: int) -> list[dict]:
    selftests = []
    for _ in range(count):
        reply = command(controller, "IMU SELFTEST", "IMU SELFTEST ")
        match = SELFTEST_RE.fullmatch(reply.text)
        if match is None:
            raise SessionError(f"unparseable selftest reply: {reply.text}")
        item = {
            key: value if key == "verdict" else int(value)
            for key, value in match.groupdict().items()
        }
        item["correlation"] = reply.correlation
        selftests.append(item)
    return selftests


def analyze_selftests(selftests: list[dict]) -> dict:
    first = selftests[0]
    acc_g = [first[axis] / 32768.0 * 16.0 for axis in ("ax", "ay", "az")]
    gyro_dps = [
        first[axis] / 32768.0 * 2000.0 for axis in ("gx", "gy", "gz")
    ]
    chip_sequences = [
        [item["c0"], item["c1"], item["c2"]] for item in selftests
    ]
    flat = [value for triplet in chip_sequences for value in triplet]
    transitions = sum(
        current != previous for previous, current in zip(flat, flat[1:])
    )
    return {
        "R2": {
            "raw": first,
            "acc_g": acc_g,
            "acc_norm_g": math.sqrt(sum(value * value for value in acc_g)),
            "gyro_dps": gyro_dps,
            "temperature_c": first["temp"] / 100.0,
            "all_selftests_pass": all(
                item["verdict"] == "PASS" and item["err"] == 0
                for item in selftests
            ),
        },
        "R3": {
            "selftest_count": len(selftests),
            "chip_ms_triplets": chip_sequences,
            "unique_chip_ms": len(set(flat)),
            "transitions": transitions,
            "first_2ms_interval_steps": sum(
                item["c1"] != item["c0"] for item in selftests
            ),
            "following_4ms_interval_steps": sum(
                item["c2"] != item["c1"] for item in selftests
            ),
            "steps_on_every_read": transitions == len(flat) - 1,
        },
    }


def run_r1_r3(args) -> int:
    args.out_dir.mkdir(parents=True, exist_ok=False)
    write_json(
        args.out_dir / "predictions.json",
        {
            "written_before_rtt_open": True,
            "R1": (
                "Provision immediate and post-restart reads are expected to "
                "match 61=0000,63=FFFF,03=000B,1F=0002; after B306 reboot "
                "boot verification is expected to remain PASS."
            ),
            "R2": (
                "At rest, acceleration magnitude is expected near 1 g, gyro "
                "small, and temperature plausible."
            ),
            "R3": (
                "The chip-ms field is expected to advance on sensor refresh "
                "boundaries rather than on every host read."
            ),
        },
    )
    summary: dict = {"status": "IN_PROGRESS", "steps": {}}
    channel = None
    try:
        with (args.out_dir / "raw.log").open("a", buffering=1) as raw_log:
            channel, controller = open_controller(args, raw_log)
            summary["steps"]["bridge"] = controller.ensure_bridge()
            summary["steps"]["stopped"] = ensure_stopped(controller)

            provision = command(
                controller, "IMU PROVISION", "IMU PROVISION "
            )
            summary["steps"]["R1_provision"] = provision.__dict__
            if not provision.text.startswith("IMU PROVISION PASS "):
                raise SessionError(f"provisioning failed: {provision.text}")

            summary["steps"]["R1_reboot"] = controller.reboot_preflight()
            summary["steps"]["R1_bridge_after_reboot"] = (
                controller.ensure_bridge()
            )
            persisted = command(controller, "IMU STATUS", "IMU ")
            summary["steps"]["R1_persistence"] = persisted.__dict__
            required = (
                "active=0",
                "verify=PASS",
                "61=0000",
                "63=FFFF",
                "03=000B",
                "1F=0002",
            )
            if any(token not in persisted.text for token in required):
                raise SessionError(
                    f"post-reboot persistence mismatch: {persisted.text}"
                )

            selftests = collect_selftests(controller, args.selftest_count)
            write_json(args.out_dir / "selftests.json", selftests)
            summary["steps"].update(analyze_selftests(selftests))
            summary["status"] = "PASS"
    except Exception as exc:
        summary["status"] = "FAILED"
        summary["error"] = str(exc)
        raise
    finally:
        if channel is not None:
            channel.close()
        write_json(args.out_dir / "summary.json", summary)
    return 0


def run_r2_r3(args) -> int:
    """Read-only follow-up after a failed R1; do not retry provisioning."""
    args.out_dir.mkdir(parents=True, exist_ok=False)
    write_json(
        args.out_dir / "predictions.json",
        {
            "written_before_rtt_open": True,
            "R1_context": (
                "R1 failed before SAVE/RESTART. This run must not issue "
                "IMU PROVISION; B306 is rebooted first to discard partial "
                "volatile sensor writes."
            ),
            "R2": (
                "At rest, acceleration magnitude is expected near 1 g, gyro "
                "small, and temperature plausible, but the result is measured "
                "under the persisted default/WARN register configuration."
            ),
            "R3": (
                "The chip-ms field is expected to advance on sensor refresh "
                "boundaries rather than on every host read."
            ),
        },
    )
    summary: dict = {"status": "IN_PROGRESS", "steps": {}}
    channel = None
    try:
        with (args.out_dir / "raw.log").open("a", buffering=1) as raw_log:
            channel, controller = open_controller(args, raw_log)
            summary["steps"]["bridge"] = controller.ensure_bridge()
            summary["steps"]["reboot_after_failed_R1"] = (
                controller.reboot_preflight()
            )
            summary["steps"]["bridge_after_reboot"] = controller.ensure_bridge()
            summary["steps"]["stopped"] = ensure_stopped(controller)
            status = command(controller, "IMU STATUS", "IMU ")
            summary["steps"]["persisted_status"] = status.__dict__

            selftests = collect_selftests(controller, args.selftest_count)
            write_json(args.out_dir / "selftests.json", selftests)
            summary["steps"].update(analyze_selftests(selftests))
            summary["status"] = "PASS"
    except Exception as exc:
        summary["status"] = "FAILED"
        summary["error"] = str(exc)
        raise
    finally:
        if channel is not None:
            channel.close()
        write_json(args.out_dir / "summary.json", summary)
    return 0


def decode_imu_record(
    fields: dict[str, str],
) -> tuple[int, int, int, list[list[int]]] | None:
    required = {"base_us", "seq", "n", "samples", "temp_raw"}
    if not required <= fields.keys():
        return None
    try:
        base = int(fields["base_us"], 0)
        sequence = int(fields["seq"], 0)
        count = int(fields["n"], 0)
        temperature = int(fields["temp_raw"], 0)
        encoded_samples = fields["samples"].split(";")
        values = [
            [int(value, 0) for value in encoded.split(",")]
            for encoded in encoded_samples
        ]
    except ValueError:
        return None
    if len(values) != count or any(len(sample) != 7 for sample in values):
        return None
    return base, sequence, temperature, values


def parse_imu_samples(lines: list[str]) -> list[dict[str, int]]:
    samples: list[dict[str, int]] = []
    for line in lines:
        if not line.startswith("FUSION_IMU "):
            continue
        decoded = decode_imu_record(parse_fields(line))
        if decoded is None:
            continue
        base, sequence, temperature, values = decoded
        for index, sample in enumerate(values):
            samples.append(
                {
                    "seq": (sequence + index) & 0xFFFF,
                    "timer_us": (base + sample[0]) & 0xFFFFFFFF,
                    "ax": sample[1],
                    "ay": sample[2],
                    "az": sample[3],
                    "gx": sample[4],
                    "gy": sample[5],
                    "gz": sample[6],
                    "temp": temperature,
                }
            )
    return samples


def imu_sequence_audit(lines: list[str]) -> dict:
    previous_seq: int | None = None
    previous_n = 0
    gap_events = 0
    missing_samples = 0
    malformed_lines = 0
    valid_records = 0
    for line in lines:
        if not line.startswith("FUSION_IMU "):
            continue
        fields = parse_fields(line)
        decoded = decode_imu_record(fields)
        if decoded is None:
            malformed_lines += 1
            continue
        _, seq, _, values = decoded
        count = len(values)
        if previous_seq is not None:
            expected = (previous_seq + previous_n) & 0xFFFF
            if seq != expected:
                gap_events += 1
                missing_samples += (seq - expected) & 0xFFFF
        previous_seq = seq
        previous_n = count
        valid_records += 1
    return {
        "valid_records": valid_records,
        "malformed_imu_lines": malformed_lines,
        "gap_events": gap_events,
        "missing_samples": missing_samples,
    }


def capture_lines_from_raw(path: Path, bsf: str) -> list[str]:
    """Recover the exact START-to-STOP stream, including lines read around commands."""
    active = False
    lines: list[str] = []
    stop_marker = f"line={bsf} IMU STOP"
    for raw in path.read_text(errors="replace").splitlines():
        marker = " FUSION_RTT_RX "
        if marker not in raw:
            continue
        payload = raw.split(marker, 1)[1]
        if "text=IMU START OK " in f"{payload} ":
            active = True
            continue
        if active and stop_marker in payload:
            break
        if active:
            imu_offset = payload.find("FUSION_IMU ")
            if imu_offset > 0:
                lines.append(
                    "HOST_RTT_CORRUPTION kind=interleaved_prefix "
                    f"bytes={imu_offset}"
                )
                payload = payload[imu_offset:]
            lines.append(payload)
    return lines


def sample_summary(samples: list[dict[str, int]]) -> dict:
    acc_norm = []
    gyro_axes = {axis: [] for axis in ("gx", "gy", "gz")}
    for sample in samples:
        acc = [
            sample[axis] / 32768.0 * 16.0
            for axis in ("ax", "ay", "az")
        ]
        acc_norm.append(math.sqrt(sum(value * value for value in acc)))
        for axis in gyro_axes:
            gyro_axes[axis].append(sample[axis] / 32768.0 * 2000.0)
    gyro_triplets = [
        (sample["gx"], sample["gy"], sample["gz"]) for sample in samples
    ]
    exact_zero_triplets = sum(triplet == (0, 0, 0) for triplet in gyro_triplets)
    return {
        "sample_count": len(samples),
        "acc_norm_g_mean": statistics.fmean(acc_norm) if acc_norm else None,
        "acc_norm_g_std": statistics.pstdev(acc_norm) if len(acc_norm) > 1 else None,
        "gyro_dps_mean": {
            axis: statistics.fmean(values) if values else None
            for axis, values in gyro_axes.items()
        },
        "gyro_dps_std": {
            axis: statistics.pstdev(values) if len(values) > 1 else None
            for axis, values in gyro_axes.items()
        },
        "gyro_unique_triplets": len(set(gyro_triplets)),
        "gyro_exact_zero_triplets": exact_zero_triplets,
        "gyro_exact_zero_fraction": (
            exact_zero_triplets / len(samples) if samples else None
        ),
        "temperature_c_mean": (
            statistics.fmean(sample["temp"] for sample in samples) / 100.0
            if samples
            else None
        ),
    }


def boundary_summary(samples: list[dict[str, int]], boundary_s: float) -> dict:
    if not samples:
        return {"available": False}
    start = samples[0]["timer_us"]
    elapsed = [((sample["timer_us"] - start) & 0xFFFFFFFF) / 1e6 for sample in samples]
    before = [
        sample
        for sample, seconds in zip(samples, elapsed)
        if boundary_s - 5.0 <= seconds < boundary_s
    ]
    after = [
        sample
        for sample, seconds in zip(samples, elapsed)
        if boundary_s <= seconds < boundary_s + 5.0
    ]

    def axis_stats(group, axis):
        values = [sample[axis] for sample in group]
        return {
            "count": len(values),
            "raw_mean": statistics.fmean(values) if values else None,
            "raw_std": statistics.pstdev(values) if len(values) > 1 else None,
            "zero_fraction": (
                sum(value == 0 for value in values) / len(values)
                if values
                else None
            ),
        }

    return {
        "available": bool(before and after),
        "boundary_s": boundary_s,
        "before": {axis: axis_stats(before, axis) for axis in ("gx", "gy", "gz")},
        "after": {axis: axis_stats(after, axis) for axis in ("gx", "gy", "gz")},
    }


def build_capture_analysis(
    capture_lines: list[str],
    baseline: dict[str, str],
    final: dict[str, str],
    boundary_s: float,
) -> dict:
    samples = parse_imu_samples(capture_lines)
    sequence = imu_sequence_audit(capture_lines)
    anomalies = counter_deltas(baseline, final, ANOMALY_COUNTERS)
    frame_delta = (
        int(final["frames"], 0) - int(baseline["frames"], 0)
    ) & 0xFFFFFFFF
    node_dt_s = (
        (int(final["node_ms"], 0) - int(baseline["node_ms"], 0))
        & 0xFFFFFFFF
    ) / 1000.0
    return {
        **sample_summary(samples),
        "raw_capture_line_count": len(capture_lines),
        "imu_records": sequence["valid_records"],
        "imu_sequence_gaps": sequence["gap_events"],
        "imu_missing_samples": sequence["missing_samples"],
        "host_malformed_imu_lines": sequence["malformed_imu_lines"],
        "host_interleaved_prefix_lines": sum(
            line.startswith(
                "HOST_RTT_CORRUPTION kind=interleaved_prefix "
            )
            for line in capture_lines
        ),
        "host_embedded_health_lines": sum(
            "FUSION_HEALTH" in line
            and not line.startswith("FUSION_HEALTH")
            for line in capture_lines
        ),
        "host_malformed_uwb_lines": sum(
            line.startswith("FUSION_UWB ")
            and " verdict=" not in line
            for line in capture_lines
        ),
        "anomaly_counter_deltas": anomalies,
        "uwb_frame_delta": frame_delta,
        "uwb_rate_hz": frame_delta / node_dt_s if node_dt_s else None,
        "healthy_uwb_records": sum(
            line.startswith("FUSION_UWB ")
            and " verdict=healthy " in f" {line} "
            for line in capture_lines
        ),
        "observed_uwb_records": sum(
            line.startswith("FUSION_UWB ") for line in capture_lines
        ),
        "boundary": boundary_summary(samples, boundary_s),
        "ci_lines": [
            line
            for line in capture_lines
            if line.startswith(("FUSION_CI_CURRENT", "FUSION_CI_UPDATED"))
        ],
    }


def run_capture(args) -> int:
    args.out_dir.mkdir(parents=True, exist_ok=False)
    write_json(
        args.out_dir / "predictions.json",
        {
            "written_before_rtt_open": True,
            "label": args.label,
            "duration_s": args.duration_s,
            "prediction": args.prediction,
        },
    )
    summary: dict = {
        "status": "IN_PROGRESS",
        "label": args.label,
        "duration_s": args.duration_s,
    }
    channel = None
    controller = None
    imu_started = False
    lines: list[str] = []
    try:
        with (args.out_dir / "raw.log").open("a", buffering=1) as raw_log:
            channel, controller = open_controller(args, raw_log)
            summary["bridge"] = controller.ensure_bridge()
            if args.reboot:
                summary["reboot"] = controller.reboot_preflight()
                summary["bridge_after_reboot"] = controller.ensure_bridge()
            summary["stopped"] = ensure_stopped(controller)
            summary["clear"] = command(
                controller, "COUNTERS CLEAR", "COUNTERS CLEARED"
            ).__dict__
            summary["rate"] = command(
                controller, f"IMU RATE={args.imu_rate}", "IMU RATE OK "
            ).__dict__
            summary["batch"] = command(
                controller, f"IMU BATCH={args.imu_batch}", "IMU BATCH OK "
            ).__dict__
            baseline = controller.wait_telemetry()
            summary["baseline"] = baseline
            summary["start"] = command(
                controller, "IMU START", "IMU START OK "
            ).__dict__
            imu_started = True

            lines = controller.collect(args.duration_s)
            final = controller.latest_telemetry
            if final is None:
                raise SessionError("capture lacked final telemetry")
            summary["final"] = final
            summary["stop"] = command(
                controller, "IMU STOP", "IMU STOP OK "
            ).__dict__
            imu_started = False
            summary["counters"] = controller.counters()

            raw_log.flush()
            capture_lines = capture_lines_from_raw(
                args.out_dir / "raw.log", args.bsf
            )
            summary["analysis"] = build_capture_analysis(
                capture_lines, baseline, final, args.boundary_s
            )
            summary["status"] = "COMPLETE"
            write_json(args.out_dir / "analysis.json", summary["analysis"])
    except Exception as exc:
        summary["status"] = "FAILED"
        summary["error"] = str(exc)
        if imu_started and controller is not None:
            try:
                summary["rollback_stop"] = command(
                    controller, "IMU STOP", "IMU STOP "
                ).__dict__
            except Exception as stop_exc:
                summary["rollback_stop_error"] = str(stop_exc)
        raise
    finally:
        if channel is not None:
            channel.close()
        write_json(args.out_dir / "summary.json", summary)
    return 0


def run_reanalyze(args) -> int:
    summary_path = args.out_dir / "summary.json"
    summary = json.loads(summary_path.read_text())
    capture_lines = capture_lines_from_raw(args.out_dir / "raw.log", args.bsf)
    analysis = build_capture_analysis(
        capture_lines,
        summary["baseline"],
        summary["final"],
        args.boundary_s,
    )
    summary["analysis"] = analysis
    summary["status"] = (
        "COMPLETE_WITH_HOST_LOG_CORRUPTION"
        if (
            analysis["host_malformed_imu_lines"]
            + analysis["host_interleaved_prefix_lines"]
            + analysis["host_embedded_health_lines"]
            + analysis["host_malformed_uwb_lines"]
        )
        != 0
        else "COMPLETE"
    )
    summary.pop("error", None)
    write_json(args.out_dir / "analysis.json", analysis)
    write_json(summary_path, summary)
    return 0


def run_finalize(args) -> int:
    """Leave B306 rebooted and IMU quiet without changing UWB configuration."""
    args.out_dir.mkdir(parents=True, exist_ok=False)
    summary: dict = {"status": "IN_PROGRESS", "uwb_action": "NONE"}
    channel = None
    try:
        with (args.out_dir / "raw.log").open("a", buffering=1) as raw_log:
            channel, controller = open_controller(args, raw_log)
            summary["bridge"] = controller.ensure_bridge()
            summary["before"] = ensure_stopped(controller)
            summary["reboot"] = controller.reboot_preflight()
            summary["bridge_after_reboot"] = controller.ensure_bridge()
            final = command(controller, "IMU STATUS", "IMU ")
            summary["final"] = final.__dict__
            if "active=0 " not in f"{final.text} ":
                raise SessionError(f"final IMU state is not quiet: {final.text}")
            summary["status"] = "PASS"
    except Exception as exc:
        summary["status"] = "FAILED"
        summary["error"] = str(exc)
        raise
    finally:
        if channel is not None:
            channel.close()
        write_json(args.out_dir / "summary.json", summary)
    return 0


def common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--bsf", default="BSF3C79")
    parser.add_argument("--serial-number", type=int, default=683234364)
    parser.add_argument("--device", default="nRF52840_xxAA")
    parser.add_argument("--address", type=lambda value: int(value, 0), default=0x20002100)
    parser.add_argument("--speed-khz", type=int, default=4000)
    parser.add_argument("--timeout", type=float, default=8.0)
    parser.add_argument("--max-attempts", type=int, default=3)
    parser.add_argument("--out-dir", type=Path, required=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    r1 = sub.add_parser("r1-r3")
    common(r1)
    r1.add_argument("--selftest-count", type=int, default=30)

    r23 = sub.add_parser("r2-r3")
    common(r23)
    r23.add_argument("--selftest-count", type=int, default=30)

    capture = sub.add_parser("capture")
    common(capture)
    capture.add_argument("--label", required=True)
    capture.add_argument("--duration-s", type=float, required=True)
    capture.add_argument("--prediction", required=True)
    capture.add_argument("--reboot", action="store_true")
    capture.add_argument("--imu-rate", type=int, choices=(50, 100, 200), default=200)
    capture.add_argument("--imu-batch", type=int, choices=range(1, 6), default=2)
    capture.add_argument("--boundary-s", type=float, default=65.5)

    reanalyze = sub.add_parser("reanalyze")
    common(reanalyze)
    reanalyze.add_argument("--boundary-s", type=float, default=65.5)

    finalize = sub.add_parser("finalize")
    common(finalize)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        if args.action == "r1-r3":
            return run_r1_r3(args)
        if args.action == "r2-r3":
            return run_r2_r3(args)
        if args.action == "reanalyze":
            return run_reanalyze(args)
        if args.action == "finalize":
            return run_finalize(args)
        return run_capture(args)
    except SessionError as exc:
        print(f"ERROR: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
