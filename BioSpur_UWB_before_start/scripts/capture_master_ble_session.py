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

CONNECTED_RE = re.compile(
    r"Connected\[(?P<conn>\d+)\]:.*?(?:name=(?P<name>[^\s]+))?.*?tag_id=(?P<tag_id>-?\d+)"
)


def parse_tag_summary(text):
    for regex in (TAG_SUMMARY_RE_FULL, TAG_SUMMARY_RE_COMPACT, TAG_SUMMARY_RE_BUNDLE):
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

        if idx > 0 and "notify:" not in fragment and fragment.startswith(("TagSummary", "TS")):
            fragment = (prefix or "BLE notify: ") + fragment

        match = parse_tag_summary(fragment)
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
    conn_meta = {}
    connected_count = 0
    disconnected_count = 0
    pending = ""
    end = time.time() + args.duration

    with raw_log_path.open("w", encoding="utf-8") as raw_log:
        while time.time() < end:
            try:
                with serial.Serial(args.port, args.baud, timeout=0.2) as ser:
                    while time.time() < end:
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
        "duration_s": args.duration,
        "session_dir": str(session_dir),
        "unique_position_samples": len(positions),
        "summary_samples_used": len(summary_positions),
        "unique_streams": len({row["conn_id"] for row in positions}),
        "stream_tag_ids": {key: value["tag_id"] for key, value in conn_meta.items()},
        "connected_count": connected_count,
        "disconnected_count": disconnected_count,
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

    if first_window and last_window:
        summary["drift_first50_to_last50_mm"] = {
            "dx": summary["last_50_mean_mm"]["x"] - summary["first_50_mean_mm"]["x"],
            "dy": summary["last_50_mean_mm"]["y"] - summary["first_50_mean_mm"]["y"],
            "dz": summary["last_50_mean_mm"]["z"] - summary["first_50_mean_mm"]["z"],
        }

    with summary_json_path.open("w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)

    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
