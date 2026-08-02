#!/usr/bin/env python3
"""Same-session IMU-on/off UWB timing comparison over Fusion Master CDC."""

from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path

from analyze_multiunit_alignment import fit
from fusion_session import (
    ANOMALY_COUNTERS,
    FusionController,
    LineChannel,
    SessionError,
    counter_deltas,
    imu_sequence_gaps,
    parse_fields,
    resolve_fusion_port,
)


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def command(controller: FusionController, text: str, prefix: str):
    return controller.command(
        text,
        lambda reply: reply.startswith(prefix),
        allow_resend_after_tx=False,
    )


def scheduled_rows(lines: list[str]) -> list[tuple[int, int]]:
    rows: list[tuple[int, int]] = []
    last_sweep: int | None = None
    last_stamp: int | None = None
    for line in lines:
        if not line.startswith("FUSION_UWB "):
            continue
        fields = parse_fields(line)
        if fields.get("sweep") is None or fields.get("strobe_us") in (None, "-"):
            continue
        sweep = int(fields["sweep"], 0)
        stamp = int(fields["strobe_us"], 0)
        if (
            last_sweep is not None
            and last_stamp is not None
            and (sweep <= last_sweep or stamp <= last_stamp)
        ):
            continue
        rows.append((sweep, stamp))
        last_sweep = sweep
        last_stamp = stamp
    if len(rows) < 3:
        raise SessionError("fewer than three monotonic UWB rows")
    return rows


def segment(
    lines: list[str],
    baseline: dict[str, str],
    final: dict[str, str],
) -> dict[str, object]:
    rows = scheduled_rows(lines)
    uwb = [line for line in lines if line.startswith("FUSION_UWB ")]
    gaps, imu_records = imu_sequence_gaps(lines)
    anomalies = counter_deltas(baseline, final, ANOMALY_COUNTERS)
    return {
        "fit": fit(rows),
        "uwb_records": len(uwb),
        "healthy_uwb_records": sum(
            " verdict=healthy " in f" {line} " for line in uwb
        ),
        "imu_records": imu_records,
        "imu_sequence_gaps": gaps,
        "anomaly_counter_deltas": anomalies,
        "nonzero_anomaly_counter_deltas": {
            name: value for name, value in anomalies.items() if value
        },
        "baseline_telemetry": baseline,
        "final_telemetry": final,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--port")
    parser.add_argument("--bsf", default="BSF3C79")
    parser.add_argument("--half-duration-s", type=float, default=300.0)
    parser.add_argument("--timeout", type=float, default=8.0)
    args = parser.parse_args()

    args.out_dir.mkdir(parents=True, exist_ok=False)
    predictions = {
        "written_before_cdc_open": True,
        "design": "one port session, one cr2 image, IMU on then off",
        "half_duration_s": args.half_duration_s,
        "preflight": "remote B306 REBOOT, then one counter clear",
        "prediction": {
            "imu_on_sigma_us": 97.2,
            "imu_off_sigma_us": 33.2,
            "sigma_ratio_on_over_off": 2.9,
            "imu_on_abs_p95_us": 130.0,
            "imu_off_abs_p95_us": 34.2,
            "causal_gate": (
                "ratio >=1.5 supports coexistence broadening; ratio <1.5 "
                "means the earlier cross-image difference was confounded"
            ),
            "all_anomaly_deltas_zero": True,
            "uwb_healthy_in_both_halves": True,
        },
    }
    write_json(args.out_dir / "predictions.json", predictions)
    summary: dict[str, object] = {
        "status": "IN_PROGRESS",
        "started_utc": datetime.now(timezone.utc).isoformat(),
    }
    write_json(args.out_dir / "summary.json", summary)

    channel = None
    imu_active = False
    try:
        port = resolve_fusion_port(args.port)
        summary["port"] = port
        with (args.out_dir / "raw.log").open("a", buffering=1) as raw_log:
            channel = LineChannel(port, raw_log, "FUSION")
            controller = FusionController(channel, args.bsf, args.timeout, 3)
            summary["bridge_before_reboot"] = controller.ensure_bridge()
            summary["post_reboot"] = controller.reboot_preflight()
            status = command(controller, "IMU STATUS", "IMU ")
            summary["initial_status"] = status.__dict__
            if "active=1 " in f"{status.text} ":
                raise SessionError("IMU unexpectedly active after reboot")
            summary["clear"] = command(
                controller, "COUNTERS CLEAR", "COUNTERS CLEARED"
            ).__dict__
            summary["rate"] = command(
                controller, "IMU RATE=200", "IMU RATE OK "
            ).__dict__
            summary["batch"] = command(
                controller, "IMU BATCH=2", "IMU BATCH OK "
            ).__dict__
            on_baseline = controller.wait_telemetry()
            start = controller.command(
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
            )
            summary["start"] = start.__dict__
            imu_active = True
            print("A+3 IMU-ON half started", flush=True)
            on_lines = controller.collect(args.half_duration_s)
            on_final = controller.latest_telemetry
            if on_final is None:
                raise SessionError("IMU-on half lacks final telemetry")

            stop = command(controller, "IMU STOP", "IMU STOP OK ")
            summary["stop"] = stop.__dict__
            imu_active = False
            off_baseline = controller.wait_telemetry(
                newer_than_ms=int(on_final["node_ms"], 0)
            )
            if off_baseline.get("imu_active") != "0":
                raise SessionError("IMU did not become inactive for off half")
            print("A+3 IMU-OFF half started", flush=True)
            off_lines = controller.collect(args.half_duration_s)
            off_final = controller.latest_telemetry
            if off_final is None:
                raise SessionError("IMU-off half lacks final telemetry")

            on = segment(on_lines, on_baseline, on_final)
            off = segment(off_lines, off_baseline, off_final)
            on_sigma = float(on["fit"]["residual"]["sigma_us"])
            off_sigma = float(off["fit"]["residual"]["sigma_us"])
            ratio = on_sigma / off_sigma if off_sigma else None
            analysis = {
                "imu_on": on,
                "imu_off": off,
                "comparison": {
                    "sigma_ratio_on_over_off": ratio,
                    "causal_gate_supports_broadening": (
                        ratio is not None and ratio >= 1.5
                    ),
                    "prediction_sigma_ratio": 2.9,
                },
                "gates": {
                    "both_uwb_healthy": (
                        on["healthy_uwb_records"] == on["uwb_records"]
                        and off["healthy_uwb_records"] == off["uwb_records"]
                    ),
                    "both_counter_sets_clean": (
                        not on["nonzero_anomaly_counter_deltas"]
                        and not off["nonzero_anomaly_counter_deltas"]
                    ),
                    "imu_on_sequence_clean": on["imu_sequence_gaps"] == 0,
                    "imu_absent_off": off["imu_records"] == 0,
                },
            }
            analysis["pass"] = all(analysis["gates"].values())
            summary["analysis"] = analysis
            summary["status"] = "PASS" if analysis["pass"] else "FAILED"
            summary["completed_utc"] = datetime.now(timezone.utc).isoformat()
            write_json(args.out_dir / "analysis.json", analysis)
    except Exception as exc:
        summary["status"] = "FAILED"
        summary["error"] = str(exc)
        summary["completed_utc"] = datetime.now(timezone.utc).isoformat()
        if imu_active and channel is not None:
            try:
                controller = FusionController(channel, args.bsf, args.timeout, 1)
                summary["rollback_stop"] = command(
                    controller, "IMU STOP", "IMU STOP "
                ).__dict__
                imu_active = False
            except Exception as rollback_exc:
                summary["rollback_stop_error"] = str(rollback_exc)
        raise
    finally:
        if channel is not None:
            channel.close()
        write_json(args.out_dir / "summary.json", summary)

    print(f"A+3 VERDICT: {summary['status']}", flush=True)
    return 0 if summary["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
