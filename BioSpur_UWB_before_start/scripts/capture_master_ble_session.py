#!/usr/bin/env python3
import argparse
import csv
import json
import os
import re
import statistics
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import serial


TAG_NOTIFY_PREFIX_RE = r"(?:BLE(?:\[(?P<conn>\d+)(?::[^\]]*)?\])?|BS[0-9A-F]{4}|NUS)"


TAG_SUMMARY_RE_FULL = re.compile(
    rf"{TAG_NOTIFY_PREFIX_RE} notify: TagSummary sweep=(?P<sweep>\d+) plan=(?P<plan>\w+) "
    r"xyz=\((?P<x>-?\d+),(?P<y>-?\d+),(?P<z>-?\d+)\) "
    r"rms=(?P<rms>\d+) max=(?P<max>\d+)"
    r"(?: anchors=\[(?P<anchors>[A-Z,]*)\])?"
    r"(?: motion_dt=(?P<motion_dt>\d+))?"
    r"(?: disp=(?P<disp>\d+) speed=(?P<speed>\d+))?"
)

TAG_SUMMARY_RE_COMPACT = re.compile(
    rf"{TAG_NOTIFY_PREFIX_RE} notify: TagSummary s=(?P<sweep>\d+) p=(?P<plan>\w+) "
    r"xyz=\((?P<x>-?\d+),(?P<y>-?\d+),(?P<z>-?\d+)\) "
    r"r=(?P<rms>\d+) m=(?P<max>\d+)"
    r"(?: a=\[(?P<anchors>[A-Z,]*)\])?"
    r"(?: dt=(?P<motion_dt>\d+)| motion=na)?"
)

TAG_SUMMARY_RE_BUNDLE = re.compile(
    rf"{TAG_NOTIFY_PREFIX_RE} notify: (?:TS|TagSummary) s=(?P<sweep>\d+) p=(?P<plan>\w+) "
    r"xyz=(?:(?P<x>-?\d+),(?P<y>-?\d+),(?P<z>-?\d+)|\((?P<x2>-?\d+),(?P<y2>-?\d+),(?P<z2>-?\d+)\)) "
    r"r=(?P<rms>\d+) m=(?P<max>\d+)"
    r"(?: a=(?P<anchors>[A-Z0-9,\[\]]*))?"
    r"(?: (?:d|dt)=(?P<motion_dt>\d+)| motion=na)?"
)

TAG_SUMMARY_RE_SEMI = re.compile(
    rf"{TAG_NOTIFY_PREFIX_RE} notify: TS;"
    r"(?P<ver>\d+);"
    r"(?P<sweep>\d+);"
    r"(?P<plan>[tfrx]);"
    r"(?P<x>-?\d+);(?P<y>-?\d+);(?P<z>-?\d+);"
    r"(?P<rms>\d+);(?P<max>\d+);"
    r"(?P<anchors>[A-Z0-9]*);"
    r"(?P<slot_idx>\d+);(?P<slot_cnt>\d+);"
    r"(?P<src>[MSB]);"
    r"(?P<cut>[01]);"
    r"(?P<reason>[SPRCN]);"
    r"(?P<motion_dt>\d+)"
)

CM_RE = re.compile(
    rf"{TAG_NOTIFY_PREFIX_RE} notify: CM;"
    r"(?P<ver>\d+);"
    r"(?P<sweep>\d+);"
    r"(?P<anchor>\d+);"
    r"(?P<status>[a-z_]+);"
    r"(?P<raw>-?\d+);"
    r"(?P<filt>\d+);"
    r"(?P<q>\d+);"
    r"(?P<ok>\d+);"
    r"(?P<fail>\d+)"
)

CONNECTED_RE = re.compile(
    r"Connected\[(?P<conn>\d+)\]:.*?(?:name=(?P<name>[^\s]+))?.*?tag_id=(?P<tag_id>-?\d+)"
)


def parse_tag_summary(text):
    for regex in (
        TAG_SUMMARY_RE_FULL,
        TAG_SUMMARY_RE_COMPACT,
        TAG_SUMMARY_RE_BUNDLE,
        TAG_SUMMARY_RE_SEMI,
    ):
        match = regex.search(text)
        if match:
            return match
    return None


def iter_tag_summary_matches(text):
    prefix = None
    if "notify:" in text:
        prefix = text.split("notify:", 1)[0] + "notify: "

    for idx, fragment in enumerate(text.split("|")):
        fragment = fragment.strip()
        if not fragment:
            continue

        if idx > 0 and "notify:" not in fragment and fragment.startswith(("TagSummary", "TS", "TS;")):
            fragment = (prefix or "BLE notify: ") + fragment

        match = parse_tag_summary(fragment)
        if match:
            yield match


def parse_cm(text):
    return CM_RE.search(text)


def iter_cm_matches(text):
    prefix = None
    if "notify:" in text:
        prefix = text.split("notify:", 1)[0] + "notify: "

    for idx, fragment in enumerate(text.split("|")):
        fragment = fragment.strip()
        if not fragment:
            continue

        if idx > 0 and "notify:" not in fragment and fragment.startswith("CM;"):
            fragment = (prefix or "NUS notify: ") + fragment

        match = parse_cm(fragment)
        if match:
            yield match


def mean_or_none(values):
    return None if not values else statistics.mean(values)


def pstdev_or_none(values):
    return None if len(values) < 2 else statistics.pstdev(values)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Capture long-running BLE TagSummary output from the master serial console."
    )
    parser.add_argument("snr", help="SEGGER serial number for the master board")
    parser.add_argument("port", help="Serial port path for the master board")
    parser.add_argument(
        "--duration",
        type=float,
        default=28800.0,
        help="Capture duration in seconds",
    )
    parser.add_argument(
        "--max-duration-s",
        type=float,
        default=None,
        help="Maximum capture duration in seconds (overrides --duration when provided).",
    )
    parser.add_argument(
        "--min-cm-records",
        type=int,
        default=0,
        help="Minimum CM records required for threshold-based early stop.",
    )
    parser.add_argument(
        "--min-ok-per-anchor",
        type=int,
        default=0,
        help="Minimum CM status=ok records required for each required anchor.",
    )
    parser.add_argument(
        "--require-anchors",
        default="",
        help="Comma-separated required anchor IDs for CM sufficiency (example: 0,1,2,3,4,5,6,7).",
    )
    parser.add_argument(
        "--start-on-first-cm",
        action="store_true",
        help=(
            "When enabled, reset the max-duration window when the first CM "
            "record is observed (useful when OTA upload happens before CM capture)."
        ),
    )
    parser.add_argument(
        "--baud",
        type=int,
        default=115200,
        help="Serial baud rate",
    )
    parser.add_argument(
        "--settle",
        type=float,
        default=0.1,
        help="Seconds to wait for the serial device to reappear after reset",
    )
    parser.add_argument(
        "--no-reset",
        action="store_true",
        help="Do not reset the board before capturing serial output",
    )
    parser.add_argument(
        "--out-dir",
        default="logs/master_ble_sessions",
        help="Base directory for capture artifacts",
    )
    parser.add_argument(
        "--session-name",
        default=None,
        help="Optional fixed session directory name under --out-dir",
    )
    parser.add_argument(
        "--skip-sweeps",
        type=int,
        default=2,
        help="Ignore the first N summarized sweeps in statistics",
    )
    args = parser.parse_args()

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    if args.session_name:
        session_dir = Path(args.out_dir) / args.session_name
    else:
        session_dir = Path(args.out_dir) / f"master_{args.snr}_{timestamp}"
    session_dir.mkdir(parents=True, exist_ok=True)

    raw_log_path = session_dir / "raw.log"
    positions_csv_path = session_dir / "positions.csv"
    cm_ranges_csv_path = session_dir / "cm_ranges.csv"
    summary_json_path = session_dir / "summary.json"

    if not args.no_reset:
        subprocess.run(
            ["nrfjprog", "--reset", "-f", "NRF52", "--snr", args.snr],
            check=False,
        )

    deadline = time.time() + args.settle
    while time.time() < deadline:
        if os.path.exists(args.port):
            break
        time.sleep(0.1)

    position_by_stream = {}
    cm_rows = []
    conn_meta = {}
    connected_count = 0
    disconnected_count = 0
    pending = ""
    max_duration_s = args.max_duration_s if args.max_duration_s is not None else args.duration
    required_anchors = []
    if args.require_anchors.strip():
        required_anchors = sorted(
            {
                int(token.strip())
                for token in args.require_anchors.split(",")
                if token.strip() != ""
            }
        )

    cm_ok_counts = {}
    capture_start = time.time()
    end = capture_start + max_duration_s
    window_start = capture_start
    first_cm_wall_time = None
    stop_reason = "max_duration_reached"
    thresholds_enabled = (
        args.min_cm_records > 0
        or args.min_ok_per_anchor > 0
        or len(required_anchors) > 0
    )
    capture_done = False

    def cm_thresholds_met() -> bool:
        if len(cm_rows) < args.min_cm_records:
            return False

        seen_anchors = {row["anchor_id"] for row in cm_rows}
        for aid in required_anchors:
            if aid not in seen_anchors:
                return False
            if cm_ok_counts.get(aid, 0) < args.min_ok_per_anchor:
                return False
        return True

    with raw_log_path.open("w", encoding="utf-8") as raw_log:
        while (time.time() < end) and (not capture_done):
            try:
                with serial.Serial(args.port, args.baud, timeout=0.2) as ser:
                    while (time.time() < end) and (not capture_done):
                        chunk = ser.read(ser.in_waiting or 1)
                        if not chunk:
                            continue

                        pending += chunk.decode("utf-8", errors="replace")
                        while "\n" in pending:
                            line, pending = pending.split("\n", 1)
                            text = line.rstrip("\r")
                            if not text:
                                continue

                            raw_log.write(text + "\n")
                            raw_log.flush()

                            if "Connected:" in text or re.search(r"Connected\[\d+\]:", text):
                                connected_count += 1
                                connected_match = CONNECTED_RE.search(text)
                                if connected_match:
                                    conn_meta[connected_match.group("conn")] = {
                                        "tag_id": int(connected_match.group("tag_id")),
                                        "name": connected_match.group("name") or "",
                                    }
                            if "Disconnected:" in text or re.search(r"Disconnected\[\d+\]:", text):
                                disconnected_count += 1

                            for match in iter_tag_summary_matches(text):
                                conn_id = match.groupdict().get("conn") or "0"
                                sweep = int(match.group("sweep"))
                                x = match.group("x") or match.group("x2")
                                y = match.group("y") or match.group("y2")
                                z = match.group("z") or match.group("z2")
                                position_by_stream[(conn_id, sweep)] = {
                                    "conn_id": conn_id,
                                    "tag_id": conn_meta.get(conn_id, {}).get("tag_id", ""),
                                    "peer_name": conn_meta.get(conn_id, {}).get("name", ""),
                                    "sweep": sweep,
                                    "plan": match.group("plan"),
                                    "x_mm": int(x),
                                    "y_mm": int(y),
                                    "z_mm": int(z),
                                    "rms_mm": int(match.group("rms")),
                                    "max_mm": int(match.group("max")),
                                    "anchors": match.group("anchors") or "",
                                    "motion_dt_ms": int(match.group("motion_dt") or 0),
                                    "disp_mm": int(match.groupdict().get("disp") or 0),
                                    "speed_mm_s": int(match.groupdict().get("speed") or 0),
                                }

                            for match in iter_cm_matches(text):
                                now_ts = time.time()
                                if args.start_on_first_cm and first_cm_wall_time is None:
                                    first_cm_wall_time = now_ts
                                    window_start = now_ts
                                    end = window_start + max_duration_s
                                conn_id = match.groupdict().get("conn") or "0"
                                anchor_id = int(match.group("anchor"))
                                status = match.group("status")
                                cm_rows.append(
                                    {
                                        "conn_id": conn_id,
                                        "tag_id": conn_meta.get(conn_id, {}).get("tag_id", ""),
                                        "peer_name": conn_meta.get(conn_id, {}).get("name", ""),
                                        "sweep": int(match.group("sweep")),
                                        "anchor_id": anchor_id,
                                        "status": status,
                                        "raw_mm": int(match.group("raw")),
                                        "filt_mm": int(match.group("filt")),
                                        "quality_percent": int(match.group("q")),
                                        "ok_count": int(match.group("ok")),
                                        "fail_count": int(match.group("fail")),
                                    }
                                )
                                if status == "ok":
                                    cm_ok_counts[anchor_id] = cm_ok_counts.get(anchor_id, 0) + 1

                            if thresholds_enabled and cm_thresholds_met():
                                stop_reason = "threshold_met"
                                capture_done = True
                    break
            except (serial.SerialException, OSError):
                time.sleep(0.2)

        if pending.strip():
            raw_log.write(pending.rstrip("\r") + "\n")

    positions = [
        position_by_stream[key]
        for key in sorted(position_by_stream, key=lambda item: (int(item[0]), item[1]))
    ]

    with positions_csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "sweep",
                "conn_id",
                "tag_id",
                "peer_name",
                "plan",
                "x_mm",
                "y_mm",
                "z_mm",
                "rms_mm",
                "max_mm",
                "anchors",
                "motion_dt_ms",
                "disp_mm",
                "speed_mm_s",
            ],
        )
        writer.writeheader()
        for row in positions:
            writer.writerow(row)

    with cm_ranges_csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "sweep",
                "conn_id",
                "tag_id",
                "peer_name",
                "anchor_id",
                "status",
                "raw_mm",
                "filt_mm",
                "quality_percent",
                "ok_count",
                "fail_count",
            ],
        )
        writer.writeheader()
        for row in cm_rows:
            writer.writerow(row)

    summary_positions = [row for row in positions if row["sweep"] > args.skip_sweeps]
    x_values = [row["x_mm"] for row in summary_positions]
    y_values = [row["y_mm"] for row in summary_positions]
    z_values = [row["z_mm"] for row in summary_positions]
    rms_values = [row["rms_mm"] for row in summary_positions]
    max_values = [row["max_mm"] for row in summary_positions]

    first_window = summary_positions[:50]
    last_window = summary_positions[-50:] if len(summary_positions) >= 50 else summary_positions

    def mean_xyz(rows, key):
        values = [row[key] for row in rows]
        return mean_or_none(values)

    summary = {
        "snr": args.snr,
        "port": args.port,
        "duration_s": max_duration_s,
        "elapsed_s": time.time() - capture_start,
        "capture_window_s": time.time() - window_start,
        "window_started_on_first_cm": bool(args.start_on_first_cm),
        "first_cm_offset_s": (
            None if first_cm_wall_time is None else first_cm_wall_time - capture_start
        ),
        "stop_reason": stop_reason,
        "session_dir": str(session_dir),
        "unique_position_samples": len(positions),
        "summary_samples_used": len(summary_positions),
        "unique_streams": len({row["conn_id"] for row in positions}),
        "stream_tag_ids": {key: value["tag_id"] for key, value in conn_meta.items()},
        "connected_count": connected_count,
        "disconnected_count": disconnected_count,
        "cm_threshold_policy": {
            "min_cm_records": args.min_cm_records,
            "min_ok_per_anchor": args.min_ok_per_anchor,
            "required_anchors": required_anchors,
        },
        "cm_records": len(cm_rows),
        "cm_anchor_ids": sorted({row["anchor_id"] for row in cm_rows}),
        "cm_status_counts": {},
        "cm_records_per_anchor": {},
        "cm_ok_records_per_anchor": {str(k): v for k, v in sorted(cm_ok_counts.items())},
        "position_mean_mm": {
            "x": mean_or_none(x_values),
            "y": mean_or_none(y_values),
            "z": mean_or_none(z_values),
        },
        "position_std_mm": {
            "x": pstdev_or_none(x_values),
            "y": pstdev_or_none(y_values),
            "z": pstdev_or_none(z_values),
        },
        "residual_mean_mm": {
            "rms": mean_or_none(rms_values),
            "max": mean_or_none(max_values),
        },
        "first_50_mean_mm": {
            "x": mean_xyz(first_window, "x_mm"),
            "y": mean_xyz(first_window, "y_mm"),
            "z": mean_xyz(first_window, "z_mm"),
        },
        "last_50_mean_mm": {
            "x": mean_xyz(last_window, "x_mm"),
            "y": mean_xyz(last_window, "y_mm"),
            "z": mean_xyz(last_window, "z_mm"),
        },
    }

    for row in cm_rows:
        summary["cm_status_counts"][row["status"]] = (
            summary["cm_status_counts"].get(row["status"], 0) + 1
        )
        key = str(row["anchor_id"])
        summary["cm_records_per_anchor"][key] = (
            summary["cm_records_per_anchor"].get(key, 0) + 1
        )

    if first_window and last_window:
        summary["drift_first50_to_last50_mm"] = {
            "dx": summary["last_50_mean_mm"]["x"] - summary["first_50_mean_mm"]["x"],
            "dy": summary["last_50_mean_mm"]["y"] - summary["first_50_mean_mm"]["y"],
            "dz": summary["last_50_mean_mm"]["z"] - summary["first_50_mean_mm"]["z"],
        }

    seen_anchors = set(summary["cm_anchor_ids"])
    missing_required = [aid for aid in required_anchors if aid not in seen_anchors]
    below_ok_required = [
        aid for aid in required_anchors if cm_ok_counts.get(aid, 0) < args.min_ok_per_anchor
    ]
    summary["cm_threshold_result"] = {
        "thresholds_enabled": thresholds_enabled,
        "met": (stop_reason == "threshold_met") if thresholds_enabled else None,
        "missing_required_anchors": missing_required,
        "below_ok_required_anchors": below_ok_required,
    }

    with summary_json_path.open("w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)

    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
