#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import re
import time
from collections import Counter, defaultdict
from pathlib import Path

import serial
from serial import SerialException


UL_RE = re.compile(
    r"\bUL;"
    r"(?P<t_ms>\d+);(?P<seq>\d+);(?P<anchor>[A-H]);(?P<anchor_id>\d+);"
    r"(?P<src>0x[0-9a-fA-F]+);(?P<dst>0x[0-9a-fA-F]+);(?P<code>0x[0-9a-fA-F]+);"
    r"(?P<q>\d+);(?P<level>\d+);(?P<cir>\d+);(?P<rxpacc>\d+);"
    r"(?P<fp1>\d+);(?P<len>\d+)"
)

UF_RE = re.compile(
    r"\bUF;"
    r"(?P<t_ms>\d+);(?P<seq>\d+);(?P<pan>0x[0-9a-fA-F]+);"
    r"(?P<src>0x[0-9a-fA-F]+);(?P<dst>0x[0-9a-fA-F]+);(?P<code>0x[0-9a-fA-F]+);"
    r"(?P<q>\d+);(?P<level>\d+);(?P<cir>\d+);(?P<rxpacc>\d+);"
    r"(?P<fp1>\d+);(?P<len>\d+)"
)


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        description="Capture compact UL records from the passive UWB listener."
    )
    ap.add_argument(
        "--port",
        default="/dev/serial/by-id/usb-SEGGER_J-Link_000760185886-if00",
        help="Listener serial port.",
    )
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--duration", type=float, default=180.0)
    ap.add_argument(
        "--out-dir",
        default="logs/uwb_listener_capture",
        help="Base output directory.",
    )
    return ap.parse_args()


def open_serial(port: str, baud: int) -> serial.Serial:
    return serial.Serial(port, baud, timeout=0.2, write_timeout=1)


def row_from_match(match: re.Match[str], host_elapsed_s: float) -> dict:
    row = match.groupdict()
    out = {
        "host_elapsed_s": f"{host_elapsed_s:.3f}",
        "listener_t_ms": int(row["t_ms"]),
        "seq": int(row["seq"]),
        "anchor": row["anchor"],
        "anchor_id": int(row["anchor_id"]),
        "src": row["src"].lower(),
        "dst": row["dst"].lower(),
        "code": row["code"].lower(),
        "q": int(row["q"]),
        "level": int(row["level"]),
        "cir": int(row["cir"]),
        "rxpacc": int(row["rxpacc"]),
        "fp1": int(row["fp1"]),
        "len": int(row["len"]),
    }
    return out


def uf_row_from_match(match: re.Match[str], host_elapsed_s: float) -> dict:
    row = match.groupdict()
    return {
        "host_elapsed_s": f"{host_elapsed_s:.3f}",
        "listener_t_ms": int(row["t_ms"]),
        "seq": int(row["seq"]),
        "pan": row["pan"].lower(),
        "src": row["src"].lower(),
        "dst": row["dst"].lower(),
        "code": row["code"].lower(),
        "q": int(row["q"]),
        "level": int(row["level"]),
        "cir": int(row["cir"]),
        "rxpacc": int(row["rxpacc"]),
        "fp1": int(row["fp1"]),
        "len": int(row["len"]),
    }


def main() -> int:
    args = parse_args()
    stamp = time.strftime("%Y%m%d_%H%M%S")
    out_dir = Path(args.out_dir) / f"listener_{stamp}"
    out_dir.mkdir(parents=True, exist_ok=True)
    raw_path = out_dir / "raw.log"
    csv_path = out_dir / "ul.csv"
    uf_csv_path = out_dir / "uf.csv"
    summary_path = out_dir / "summary.json"

    print(f"[LISTENER] port={args.port}", flush=True)
    print(f"[LISTENER] raw_log={raw_path}", flush=True)
    print(f"[LISTENER] ul_csv={csv_path}", flush=True)
    print(f"[LISTENER] uf_csv={uf_csv_path}", flush=True)

    rows: list[dict] = []
    uf_rows: list[dict] = []
    anchor_counts: Counter[str] = Counter()
    code_counts: Counter[str] = Counter()
    uf_code_counts: Counter[str] = Counter()
    uf_src_counts: Counter[str] = Counter()
    q_by_anchor: dict[str, list[int]] = defaultdict(list)
    serial_lost = False
    start = time.time()
    end = start + args.duration
    buf = ""

    fieldnames = [
        "host_elapsed_s",
        "listener_t_ms",
        "seq",
        "anchor",
        "anchor_id",
        "src",
        "dst",
        "code",
        "q",
        "level",
        "cir",
        "rxpacc",
        "fp1",
        "len",
    ]
    uf_fieldnames = [
        "host_elapsed_s",
        "listener_t_ms",
        "seq",
        "pan",
        "src",
        "dst",
        "code",
        "q",
        "level",
        "cir",
        "rxpacc",
        "fp1",
        "len",
    ]

    try:
        ser = open_serial(args.port, args.baud)
    except (SerialException, OSError) as exc:
        summary = {
            "success": False,
            "error": f"{type(exc).__name__}: {exc}",
            "port": args.port,
            "raw_log": str(raw_path),
            "ul_csv": str(csv_path),
            "uf_csv": str(uf_csv_path),
        }
        summary_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(summary, indent=2))
        return 1

    with ser, raw_path.open("w", encoding="utf-8") as raw, csv_path.open(
        "w", newline="", encoding="utf-8"
    ) as csv_file, uf_csv_path.open("w", newline="", encoding="utf-8") as uf_csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=fieldnames)
        uf_writer = csv.DictWriter(uf_csv_file, fieldnames=uf_fieldnames)
        writer.writeheader()
        uf_writer.writeheader()
        while time.time() < end:
            try:
                data = ser.read(ser.in_waiting or 1)
            except (SerialException, OSError) as exc:
                raw.write(f"[HOST_ERROR {time.monotonic():.3f}] serial_lost: {exc}\n")
                serial_lost = True
                break
            if not data:
                continue
            text = data.decode("utf-8", errors="replace")
            raw.write(text)
            raw.flush()
            buf += text
            while "\n" in buf:
                line, buf = buf.split("\n", 1)
                match = UL_RE.search(line)
                if match:
                    row = row_from_match(match, time.time() - start)
                    writer.writerow(row)
                    csv_file.flush()
                    rows.append(row)
                    anchor_counts[row["anchor"]] += 1
                    code_counts[row["code"]] += 1
                    q_by_anchor[row["anchor"]].append(row["q"])

                uf_match = UF_RE.search(line)
                if uf_match:
                    uf_row = uf_row_from_match(uf_match, time.time() - start)
                    uf_writer.writerow(uf_row)
                    uf_csv_file.flush()
                    uf_rows.append(uf_row)
                    uf_code_counts[uf_row["code"]] += 1
                    uf_src_counts[uf_row["src"]] += 1

            elapsed = time.time() - start
            if int(elapsed) % 10 == 0 and rows:
                # Keep console concise; detailed data is in raw/csv.
                pass

    q_summary = {}
    for anchor, values in q_by_anchor.items():
        values_sorted = sorted(values)
        q_summary[anchor] = {
            "count": len(values),
            "min": values_sorted[0],
            "median": values_sorted[len(values_sorted) // 2],
            "max": values_sorted[-1],
        }

    summary = {
        "success": not serial_lost,
        "serial_lost": serial_lost,
        "port": args.port,
        "duration_s": args.duration,
        "elapsed_s": time.time() - start,
        "ul_rows": len(rows),
        "uf_rows": len(uf_rows),
        "anchor_counts": dict(sorted(anchor_counts.items())),
        "code_counts": dict(sorted(code_counts.items())),
        "uf_code_counts": dict(sorted(uf_code_counts.items())),
        "uf_src_counts_top": dict(uf_src_counts.most_common(16)),
        "q_by_anchor": q_summary,
        "raw_log": str(raw_path),
        "ul_csv": str(csv_path),
        "uf_csv": str(uf_csv_path),
    }
    summary_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return 0 if summary["success"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
