#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import re
import time
from collections import Counter
from pathlib import Path

import serial
from serial import SerialException


LPD_RE = re.compile(
    r"\bLPD;(?P<ver>\d+);"
    r"(?P<listener_id>\d+);"
    r"(?P<near_anchor_id>\d+);"
    r"(?P<listener_t_ms>\d+);"
    r"(?P<accepted_polls>\d+);"
    r"(?P<poll_seq>\d+);"
    r"(?P<tag_id>\d+);"
    r"(?P<src>0x[0-9A-Fa-f]+);"
    r"(?P<dst>0x[0-9A-Fa-f]+);"
    r"(?P<rx_ts_lo32>\d+);"
    r"(?P<carrier_integrator>-?\d+);"
    r"(?P<fp_index>\d+);"
    r"(?P<fp1>\d+);"
    r"(?P<fp2>\d+);"
    r"(?P<fp3>\d+);"
    r"(?P<cir_pwr>\d+);"
    r"(?P<rxpacc>\d+);"
    r"(?P<std_noise>\d+);"
    r"(?P<frame_len>\d+);"
    r"(?P<poll_mask>0x[0-9A-Fa-f]+)"
)

LRD_RE = re.compile(
    r"\bLRD;(?P<ver>\d+);"
    r"(?P<listener_id>\d+);"
    r"(?P<near_anchor_id>\d+);"
    r"(?P<listener_t_ms>\d+);"
    r"(?P<accepted_resps>\d+);"
    r"(?P<resp_seq>\d+);"
    r"(?P<anchor_id>\d+);"
    r"(?P<src>0x[0-9A-Fa-f]+);"
    r"(?P<dst>0x[0-9A-Fa-f]+);"
    r"(?P<rx_ts_lo32>\d+);"
    r"(?P<carrier_integrator>-?\d+);"
    r"(?P<fp_index>\d+);"
    r"(?P<fp1>\d+);"
    r"(?P<fp2>\d+);"
    r"(?P<fp3>\d+);"
    r"(?P<cir_pwr>\d+);"
    r"(?P<rxpacc>\d+);"
    r"(?P<std_noise>\d+);"
    r"(?P<frame_len>\d+)"
)

LCIRM_RE = re.compile(
    r"\bLCIRM;(?P<ver>\d+);"
    r"(?P<listener_id>\d+);"
    r"(?P<near_anchor_id>\d+);"
    r"(?P<accepted_polls>\d+);"
    r"(?P<poll_seq>\d+);"
    r"(?P<tag_id>\d+);"
    r"(?P<poll_mask>0x[0-9A-Fa-f]+);"
    r"(?P<rx_ts_lo32>\d+);"
    r"(?P<carrier_integrator>-?\d+);"
    r"(?P<fp_index>\d+);"
    r"(?P<fp1>\d+);"
    r"(?P<fp2>\d+);"
    r"(?P<fp3>\d+);"
    r"(?P<cir_pwr>\d+);"
    r"(?P<rxpacc>\d+);"
    r"(?P<acc_len>\d+)"
)

LCIRD_RE = re.compile(
    r"\bLCIRD;(?P<ver>\d+);"
    r"(?P<accepted_polls>\d+);"
    r"(?P<offset>\d+);"
    r"(?P<len>\d+);"
    r"(?P<hex>[0-9A-Fa-f]*)"
)

LCIRE_RE = re.compile(
    r"\bLCIRE;(?P<ver>\d+);"
    r"(?P<accepted_polls>\d+);"
    r"(?P<acc_len>\d+)"
)

LSTAT_RE = re.compile(
    r"\bLSTAT;(?P<ver>\d+);"
    r"(?P<listener_id>\d+);"
    r"(?P<near_anchor_id>\d+);"
    r"(?P<good_frames>\d+);"
    r"(?P<accepted_polls>\d+);"
    r"(?P<ignored_nonpoll>\d+);"
    r"(?P<ignored_poll_mask>\d+);"
    r"(?P<bad_header>\d+);"
    r"(?P<too_long>\d+);"
    r"(?P<rx_errors>\d+);"
    r"(?P<cir_captures>\d+);"
    r"(?P<last_status>0x[0-9A-Fa-f]+);"
    r"(?P<last_src>0x[0-9A-Fa-f]+);"
    r"(?P<last_dst>0x[0-9A-Fa-f]+);"
    r"(?P<last_code>0x[0-9A-Fa-f]+)"
    # new listener fw appends ring_drops;self_recover;rx_enable_failures;fps
    # (optional so this parser still reads old-firmware LSTAT lines)
    r"(?:;(?P<ring_drops>\d+);(?P<self_recover>\d+);"
    r"(?P<rx_enable_failures>\d+);(?P<fps>\d+))?"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Capture LPD/LCIR rows from the generic co-located UWB poll listener."
    )
    parser.add_argument("--port", required=True, help="Listener serial port. No default is provided to avoid opening the legacy listener.")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--duration", type=float, default=180.0)
    parser.add_argument(
        "--out-dir",
        default="logs/uwb_poll_listener_capture",
        help="Base output directory; a timestamped listener_* directory is created inside it.",
    )
    return parser.parse_args()


def row_from_match(match: re.Match[str], host_elapsed_s: float, host_epoch_s: float) -> dict:
    row = match.groupdict()
    out = {
        "host_elapsed_s": f"{host_elapsed_s:.6f}",
        "host_epoch_s": f"{host_epoch_s:.6f}",
    }
    for key, value in row.items():
        if value is None:
            out[key] = ""
        elif key in {"src", "dst", "poll_mask", "hex", "last_status", "last_src", "last_dst", "last_code"}:
            out[key] = value.lower()
        elif key == "carrier_integrator":
            out[key] = int(value)
        else:
            out[key] = int(value)
    return out


def open_serial(port: str, baud: int) -> serial.Serial:
    return serial.Serial(port, baud, timeout=0.2, write_timeout=1)


def main() -> int:
    args = parse_args()
    stamp = time.strftime("%Y%m%d_%H%M%S")
    out_dir = Path(args.out_dir) / f"listener_{stamp}"
    out_dir.mkdir(parents=True, exist_ok=True)

    raw_path = out_dir / "raw.log"
    lpd_path = out_dir / "lpd.csv"
    lrd_path = out_dir / "lrd.csv"
    lcirm_path = out_dir / "lcirm.csv"
    lcird_path = out_dir / "lcird.csv"
    lcire_path = out_dir / "lcire.csv"
    lstat_path = out_dir / "lstat.csv"
    summary_path = out_dir / "summary.json"

    lpd_fields = [
        "host_elapsed_s",
        "host_epoch_s",
        "ver",
        "listener_id",
        "near_anchor_id",
        "listener_t_ms",
        "accepted_polls",
        "poll_seq",
        "tag_id",
        "src",
        "dst",
        "rx_ts_lo32",
        "carrier_integrator",
        "fp_index",
        "fp1",
        "fp2",
        "fp3",
        "cir_pwr",
        "rxpacc",
        "std_noise",
        "frame_len",
        "poll_mask",
    ]
    lrd_fields = [
        "host_elapsed_s",
        "host_epoch_s",
        "ver",
        "listener_id",
        "near_anchor_id",
        "listener_t_ms",
        "accepted_resps",
        "resp_seq",
        "anchor_id",
        "src",
        "dst",
        "rx_ts_lo32",
        "carrier_integrator",
        "fp_index",
        "fp1",
        "fp2",
        "fp3",
        "cir_pwr",
        "rxpacc",
        "std_noise",
        "frame_len",
    ]
    lcirm_fields = [
        "host_elapsed_s",
        "host_epoch_s",
        "ver",
        "listener_id",
        "near_anchor_id",
        "accepted_polls",
        "poll_seq",
        "tag_id",
        "poll_mask",
        "rx_ts_lo32",
        "carrier_integrator",
        "fp_index",
        "fp1",
        "fp2",
        "fp3",
        "cir_pwr",
        "rxpacc",
        "acc_len",
    ]
    lcird_fields = [
        "host_elapsed_s",
        "host_epoch_s",
        "ver",
        "accepted_polls",
        "offset",
        "len",
        "hex",
    ]
    lcire_fields = [
        "host_elapsed_s",
        "host_epoch_s",
        "ver",
        "accepted_polls",
        "acc_len",
    ]
    lstat_fields = [
        "host_elapsed_s",
        "host_epoch_s",
        "ver",
        "listener_id",
        "near_anchor_id",
        "good_frames",
        "accepted_polls",
        "ignored_nonpoll",
        "ignored_poll_mask",
        "bad_header",
        "too_long",
        "rx_errors",
        "cir_captures",
        "last_status",
        "last_src",
        "last_dst",
        "last_code",
        "ring_drops",
        "self_recover",
        "rx_enable_failures",
        "fps",
    ]

    print(f"[POLL_LISTENER] port={args.port}", flush=True)
    print(f"[POLL_LISTENER] out_dir={out_dir}", flush=True)

    rows: dict[str, list[dict]] = {
        "lpd": [],
        "lrd": [],
        "lcirm": [],
        "lcird": [],
        "lcire": [],
        "lstat": [],
    }
    tag_counts: Counter[int] = Counter()
    poll_mask_counts: Counter[str] = Counter()
    serial_lost = False
    start = time.time()
    end = start + args.duration
    pending = ""

    try:
        ser = open_serial(args.port, args.baud)
    except (SerialException, OSError) as exc:
        summary = {
            "success": False,
            "error": f"{type(exc).__name__}: {exc}",
            "port": args.port,
            "out_dir": str(out_dir),
            "raw_log": str(raw_path),
        }
        summary_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(summary, indent=2))
        return 1

    with ser, raw_path.open("w", encoding="utf-8") as raw, \
            lpd_path.open("w", newline="", encoding="utf-8") as lpd_file, \
            lrd_path.open("w", newline="", encoding="utf-8") as lrd_file, \
            lcirm_path.open("w", newline="", encoding="utf-8") as lcirm_file, \
            lcird_path.open("w", newline="", encoding="utf-8") as lcird_file, \
            lcire_path.open("w", newline="", encoding="utf-8") as lcire_file, \
            lstat_path.open("w", newline="", encoding="utf-8") as lstat_file:
        writers = {
            "lpd": csv.DictWriter(lpd_file, fieldnames=lpd_fields),
            "lrd": csv.DictWriter(lrd_file, fieldnames=lrd_fields),
            "lcirm": csv.DictWriter(lcirm_file, fieldnames=lcirm_fields),
            "lcird": csv.DictWriter(lcird_file, fieldnames=lcird_fields),
            "lcire": csv.DictWriter(lcire_file, fieldnames=lcire_fields),
            "lstat": csv.DictWriter(lstat_file, fieldnames=lstat_fields),
        }
        files = {
            "lpd": lpd_file,
            "lrd": lrd_file,
            "lcirm": lcirm_file,
            "lcird": lcird_file,
            "lcire": lcire_file,
            "lstat": lstat_file,
        }
        for writer in writers.values():
            writer.writeheader()

        while time.time() < end:
            try:
                data = ser.read(ser.in_waiting or 1)
            except (SerialException, OSError) as exc:
                raw.write(f"[HOST_ERROR {time.monotonic():.3f}] serial_lost: {exc}\n")
                raw.flush()
                serial_lost = True
                break
            if not data:
                continue

            text = data.decode("utf-8", errors="replace")
            raw.write(text)
            raw.flush()
            pending += text

            while "\n" in pending:
                line, pending = pending.split("\n", 1)
                line = line.rstrip("\r")
                if not line:
                    continue
                now = time.time()
                host_elapsed_s = now - start
                checks = [
                    ("lpd", LPD_RE),
                    ("lrd", LRD_RE),
                    ("lcirm", LCIRM_RE),
                    ("lcird", LCIRD_RE),
                    ("lcire", LCIRE_RE),
                    ("lstat", LSTAT_RE),
                ]
                for name, regex in checks:
                    match = regex.search(line)
                    if not match:
                        continue
                    row = row_from_match(match, host_elapsed_s, now)
                    writers[name].writerow(row)
                    files[name].flush()
                    rows[name].append(row)
                    if name == "lpd":
                        tag_counts[int(row["tag_id"])] += 1
                        poll_mask_counts[str(row["poll_mask"])] += 1
                    break

    summary = {
        "success": not serial_lost,
        "serial_lost": serial_lost,
        "port": args.port,
        "duration_s": args.duration,
        "elapsed_s": time.time() - start,
        "lpd_rows": len(rows["lpd"]),
        "lrd_rows": len(rows["lrd"]),
        "lcirm_rows": len(rows["lcirm"]),
        "lcird_rows": len(rows["lcird"]),
        "lcire_rows": len(rows["lcire"]),
        "lstat_rows": len(rows["lstat"]),
        "tag_counts": {str(k): v for k, v in sorted(tag_counts.items())},
        "poll_mask_counts": dict(sorted(poll_mask_counts.items())),
        "out_dir": str(out_dir),
        "raw_log": str(raw_path),
        "lpd_csv": str(lpd_path),
        "lrd_csv": str(lrd_path),
        "lcirm_csv": str(lcirm_path),
        "lcird_csv": str(lcird_path),
        "lcire_csv": str(lcire_path),
        "lstat_csv": str(lstat_path),
    }
    summary_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return 0 if summary["success"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
