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
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

import serial


TAG_POS_RE = re.compile(
    r"Tag pos sweep=(?P<sweep>\d+) used=(?P<used>\d+) lower=(?P<lower>\d+) upper=(?P<upper>\d+) "
    r"xyz=\((?P<x>-?\d+),(?P<y>-?\d+),(?P<z>-?\d+)\) mm rms=(?P<rms>\d+) mm max=(?P<max>\d+) mm "
    r"anchors=\[(?P<anchors>[A-Z,]*)\]"
)

TAG_MOTION_SUMMARY_RE = re.compile(
    r"Tag motion summary sweep=(?P<sweep>\d+) plan=(?P<plan>\w+) sweep_ms=(?P<sweep_ms>\d+) "
    r"active=(?P<active>\d+) used=(?P<used>\d+) lower=(?P<lower>\d+) upper=(?P<upper>\d+) "
    r"xyz=\((?P<x>-?\d+),(?P<y>-?\d+),(?P<z>-?\d+)\) mm rms=(?P<rms>\d+) mm max=(?P<max>\d+) mm "
    r"anchors=\[(?P<anchors>[A-Z,]*)\]"
)

TAG_MOTION_STREAM_RE = re.compile(
    r"(?:Tag motion summary\s+)?sweep=(?P<sweep>\d+)\s+plan=(?P<plan>\w+)\s+"
    r"(?:sweep_ms=(?P<sweep_ms>\d+)\s+)?(?:active=(?P<active>\d+)\s+)?"
    r"(?:used=(?P<used>\d+)\s+)?(?:lower=(?P<lower>\d+)\s+)?"
    r"(?:upper=(?P<upper>\d+)\s+)?xyz=\((?P<x>-?\d+),(?P<y>-?\d+),(?P<z>-?\d+)\)"
    r".*?rms=(?P<rms>\d+)\s*mm.*?max=(?P<max>\d+)\s*mm.*?"
    r"anchors=\[(?P<anchors>[A-Z,]*)\]",
    re.DOTALL,
)

RANGE_RE = re.compile(
    r"Range anchor=(?P<anchor>\d+) addr=0x(?P<addr>[0-9a-fA-F]+) raw=(?P<raw>\d+) mm "
    r"filt=(?P<filt>\d+) mm ok=(?P<ok>\d+) fail=(?P<fail>\d+) q=(?P<q>\d+)%"
)


def mean_or_none(values):
    return None if not values else statistics.mean(values)


def pstdev_or_none(values):
    return None if len(values) < 2 else statistics.pstdev(values)


def handle_text_line(text, raw_log, positions, ranges, subset_counter, range_values_by_anchor):
    if not text:
        return

    print(text)
    sys.stdout.flush()
    raw_log.write(text + "\n")
    raw_log.flush()

    match = TAG_POS_RE.search(text)
    if match:
        anchors = match.group("anchors").split(",") if match.group("anchors") else []
        subset_label = ",".join(anchors)
        subset_counter[subset_label] += 1
        positions.append(
            {
                "sweep": int(match.group("sweep")),
                "used": int(match.group("used")),
                "lower": int(match.group("lower")),
                "upper": int(match.group("upper")),
                "x_mm": int(match.group("x")),
                "y_mm": int(match.group("y")),
                "z_mm": int(match.group("z")),
                "rms_mm": int(match.group("rms")),
                "max_mm": int(match.group("max")),
                "anchors": anchors,
            }
        )
        return

    match = TAG_MOTION_SUMMARY_RE.search(text)
    if match:
        anchors = match.group("anchors").split(",") if match.group("anchors") else []
        subset_label = ",".join(anchors)
        subset_counter[subset_label] += 1
        positions.append(
            {
                "sweep": int(match.group("sweep")),
                "used": int(match.group("used")),
                "lower": int(match.group("lower")),
                "upper": int(match.group("upper")),
                "x_mm": int(match.group("x")),
                "y_mm": int(match.group("y")),
                "z_mm": int(match.group("z")),
                "rms_mm": int(match.group("rms")),
                "max_mm": int(match.group("max")),
                "anchors": anchors,
            }
        )
        return

    match = RANGE_RE.search(text)
    if match:
        anchor_id = int(match.group("anchor"))
        filt_mm = int(match.group("filt"))
        range_values_by_anchor[anchor_id].append(filt_mm)
        ranges.append(
            {
                "anchor_id": anchor_id,
                "addr_hex": match.group("addr"),
                "raw_mm": int(match.group("raw")),
                "filt_mm": filt_mm,
                "ok": int(match.group("ok")),
                "fail": int(match.group("fail")),
                "quality_percent": int(match.group("q")),
            }
        )


def recover_motion_positions_from_text(text):
    records = []
    seen = set()
    for match in TAG_MOTION_STREAM_RE.finditer(text):
        sweep = int(match.group("sweep"))
        x_mm = int(match.group("x"))
        y_mm = int(match.group("y"))
        z_mm = int(match.group("z"))
        anchor_text = match.group("anchors") or ""
        anchor_list = anchor_text.split(",") if anchor_text else []
        key = (sweep, x_mm, y_mm, z_mm, tuple(anchor_list))
        if key in seen:
            continue
        seen.add(key)

        records.append(
            {
                "sweep": sweep,
                "used": int(match.group("used")) if match.group("used") else len(anchor_list),
                "lower": int(match.group("lower")) if match.group("lower") else 0,
                "upper": int(match.group("upper")) if match.group("upper") else 0,
                "x_mm": x_mm,
                "y_mm": y_mm,
                "z_mm": z_mm,
                "rms_mm": int(match.group("rms")) if match.group("rms") else None,
                "max_mm": int(match.group("max")) if match.group("max") else None,
                "anchors": anchor_list,
            }
        )

    return records


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Reset a Tag board, capture serial logs, and summarize runtime localization stability."
    )
    parser.add_argument("snr", help="SEGGER serial number")
    parser.add_argument("port", help="Serial port path, preferably /dev/serial/by-id/...")
    parser.add_argument(
        "--duration",
        type=float,
        default=120.0,
        help="Capture duration in seconds after reset",
    )
    parser.add_argument(
        "--baud",
        type=int,
        default=115200,
        help="Serial baud rate",
    )
    parser.add_argument(
        "--skip-sweeps",
        type=int,
        default=1,
        help="Ignore the first N Tag pos sweeps in summary statistics",
    )
    parser.add_argument(
        "--out-dir",
        default="logs/tag_sessions",
        help="Base directory for capture artifacts",
    )
    parser.add_argument(
        "--settle",
        type=float,
        default=2.0,
        help="Seconds to wait for the serial device to reappear after reset",
    )
    parser.add_argument(
        "--no-reset",
        action="store_true",
        help="Do not reset the board before capturing serial output",
    )
    parser.add_argument(
        "--session-name",
        default=None,
        help="Optional fixed session directory name under --out-dir",
    )
    args = parser.parse_args()

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    if args.session_name:
        session_dir = Path(args.out_dir) / args.session_name
    else:
        session_dir = Path(args.out_dir) / f"tag_{args.snr}_{timestamp}"
    session_dir.mkdir(parents=True, exist_ok=True)

    raw_log_path = session_dir / "raw.log"
    positions_csv_path = session_dir / "positions.csv"
    ranges_csv_path = session_dir / "ranges.csv"
    summary_json_path = session_dir / "summary.json"

    if not args.no_reset:
        subprocess.run(
            ["nrfjprog", "--reset", "-f", "NRF52", "--snr", args.snr],
            check=False,
        )

    positions = []
    ranges = []
    subset_counter = Counter()
    range_values_by_anchor = defaultdict(list)
    unexpected_frame_events = 0
    solve_pending_events = 0

    deadline = time.time() + args.settle
    while time.time() < deadline:
        if os.path.exists(args.port):
            break
        time.sleep(0.1)

    end = time.time() + args.duration
    with raw_log_path.open("w", encoding="utf-8") as raw_log:
        pending = ""
        while time.time() < end:
            try:
                with serial.Serial(args.port, args.baud, timeout=0.2) as ser:
                    while time.time() < end:
                        chunk = ser.read(ser.in_waiting or 1)
                        if not chunk:
                            continue

                        pending += chunk.decode("utf-8", errors="replace")
                        pending = pending.replace("\r\n", "\n").replace("\r", "\n")
                        while "\n" in pending:
                            line, pending = pending.split("\n", 1)
                            handle_text_line(
                                line.rstrip("\r"),
                                raw_log,
                                positions,
                                ranges,
                                subset_counter,
                                range_values_by_anchor,
                            )
                            if "unexpected frame" in line:
                                unexpected_frame_events += 1
                            if "Tag solve pending" in line:
                                solve_pending_events += 1
                    break
            except (serial.SerialException, OSError):
                time.sleep(0.2)

        if pending.strip():
            pending = pending.replace("\r\n", "\n").replace("\r", "\n")
            handle_text_line(
                pending.rstrip("\r"),
                raw_log,
                positions,
                ranges,
                subset_counter,
                range_values_by_anchor,
            )
            if "unexpected frame" in pending:
                unexpected_frame_events += pending.count("unexpected frame")
            if "Tag solve pending" in pending:
                solve_pending_events += pending.count("Tag solve pending")

    recovered_positions = recover_motion_positions_from_text(
        raw_log_path.read_text(encoding="utf-8", errors="replace")
    )
    if recovered_positions:
        existing = {
            (
                row["sweep"],
                row["x_mm"],
                row["y_mm"],
                row["z_mm"],
                row["rms_mm"],
                row["max_mm"],
                tuple(row["anchors"]),
            )
            for row in positions
        }
        for row in recovered_positions:
            key = (
                row["sweep"],
                row["x_mm"],
                row["y_mm"],
                row["z_mm"],
                row["rms_mm"],
                row["max_mm"],
                tuple(row["anchors"]),
            )
            if key not in existing:
                positions.append(row)
                existing.add(key)

    with positions_csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "sweep",
                "used",
                "lower",
                "upper",
                "x_mm",
                "y_mm",
                "z_mm",
                "rms_mm",
                "max_mm",
                "anchors",
            ],
        )
        writer.writeheader()
        for row in positions:
            row_copy = dict(row)
            row_copy["anchors"] = ",".join(row["anchors"])
            writer.writerow(row_copy)

    with ranges_csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "anchor_id",
                "addr_hex",
                "raw_mm",
                "filt_mm",
                "ok",
                "fail",
                "quality_percent",
            ],
        )
        writer.writeheader()
        for row in ranges:
            writer.writerow(row)

    summary_positions = [
        row for row in positions if row["sweep"] > args.skip_sweeps
    ]
    x_values = [row["x_mm"] for row in summary_positions]
    y_values = [row["y_mm"] for row in summary_positions]
    z_values = [row["z_mm"] for row in summary_positions]
    rms_values = [row["rms_mm"] for row in summary_positions if row["rms_mm"] is not None]
    max_values = [row["max_mm"] for row in summary_positions if row["max_mm"] is not None]

    range_stats = {}
    for anchor_id, values in sorted(range_values_by_anchor.items()):
        range_stats[str(anchor_id)] = {
            "count": len(values),
            "mean_mm": mean_or_none(values),
            "std_mm": pstdev_or_none(values),
            "min_mm": min(values),
            "max_mm": max(values),
        }

    summary = {
        "snr": args.snr,
        "port": args.port,
        "duration_s": args.duration,
        "skip_sweeps": args.skip_sweeps,
        "session_dir": str(session_dir),
        "position_samples": len(positions),
        "position_samples_used_in_summary": len(summary_positions),
        "range_samples": len(ranges),
        "unexpected_frame_events": unexpected_frame_events,
        "solve_pending_events": solve_pending_events,
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
        "residual_std_mm": {
            "rms": pstdev_or_none(rms_values),
            "max": pstdev_or_none(max_values),
        },
        "anchor_subset_frequency_all": dict(subset_counter),
        "anchor_subset_frequency_summary": dict(
            Counter(",".join(row["anchors"]) for row in summary_positions)
        ),
        "range_stats_mm": range_stats,
    }

    with summary_json_path.open("w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, sort_keys=True)
        f.write("\n")

    print("")
    print(f"capture_dir: {session_dir}")
    print(f"position_samples: {len(positions)}")
    print(f"position_samples_used_in_summary: {len(summary_positions)}")
    print(
        "position_mean_mm:",
        json.dumps(summary["position_mean_mm"], ensure_ascii=True),
    )
    print(
        "position_std_mm:",
        json.dumps(summary["position_std_mm"], ensure_ascii=True),
    )
    print(
        "residual_mean_mm:",
        json.dumps(summary["residual_mean_mm"], ensure_ascii=True),
    )
    print(
        "anchor_subset_frequency_summary:",
        json.dumps(summary["anchor_subset_frequency_summary"], ensure_ascii=True),
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
