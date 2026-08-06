#!/usr/bin/env python3
"""Read-only extraction for the N3 three-board export stalls."""

import json
import re
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CAP = ROOT / "UWB_Part/logs/overnight_20260803/phase_r_capture"
LOG = CAP / "fusion_cdc.log"
OUT = ROOT / "UWB_Part/logs/n3_stall_forensics_20260804/analysis.json"
START = 371236.285641365
STALL = {"BSFEC35": 11215.490, "BSF1120": 12095.903, "BSFB165": 19006.641}
KINDS = ("FUSION_UWB", "FUSION_IMU", "FUSION_QUEUE", "FUSION_TELEMETRY", "FUSION_QOS")
KV = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)=([^ ]+)")


def fields(line):
    return {k: v for k, v in KV.findall(line)}


def number(v):
    try:
        return int(v, 0)
    except (ValueError, TypeError):
        try:
            return float(v)
        except (ValueError, TypeError):
            return v


def slim(kind, f):
    keys = {
        "FUSION_UWB": "master_ms node_ms pkt sweep valid sf_valid sf_mod16",
        "FUSION_IMU": "master_ms seq n base_us",
        "FUSION_QUEUE": "master_ms node_ms q_drop_imu q_drop_uwb q_drop_ctl q_hwm_imu q_hwm_uwb q_hwm_ctl publisher_count publisher_max_us enq_imu enq_uwb enq_ctl abort_imu abort_uwb abort_ctl delivered_imu delivered_uwb delivered_ctl",
        "FUSION_TELEMETRY": "node_ms bytes frames crc header ring_drop sweep_drop duplicate reorder notify_ok drop_unsub drop_err uart_restarts uart_err last_sweep watchdog_feeds imu_pulls imu_dup imu_i2c_err imu_records imu_hreset imu_hfrozen imu_hrate imu_hcanary imu_hplaus imu_hdead imu_hident imu_hi2c imu_hrecover_ok imu_hrecover_fail imu_missed_deadlines",
        "FUSION_QOS": "master_ms window_ms reports event_gaps crc_ok crc_error nak rx_timeout first_event last_event delivered_imu delivered_uwb delivered_ctl",
    }[kind].split()
    return {k: number(f[k]) for k in keys if k in f}


def main():
    data = {n: {"records": defaultdict(list), "last_minute_5s": defaultdict(lambda: defaultdict(int))} for n in STALL}
    alarms = []
    with LOG.open(errors="replace") as fh:
        for line in fh:
            if "DATA_PLANE_SILENT" in line:
                alarms.append(line.strip())
            name = next((n for n in STALL if n in line), None)
            if not name:
                continue
            parts = line.split()
            if len(parts) < 4:
                continue
            try:
                mono = float(parts[1])
            except ValueError:
                continue
            kind = next((k for k in KINDS if k in line), None)
            if not kind:
                continue
            rel = mono - START
            f = fields(line)
            rec = {"relative_s": round(rel, 6), **slim(kind, f)}
            data[name]["records"][kind].append(rec)
            dt = rel - STALL[name]
            if -60 <= dt <= 5 and kind in ("FUSION_UWB", "FUSION_IMU"):
                bucket = int((dt + 60) // 5)
                data[name]["last_minute_5s"][str(bucket)][kind] += 1

    targets = [-300, -60, -10, 0, 10, 60, 3600, 999999]
    result = {"window_start_mono": START, "data_plane_silent_records": alarms, "nodes": {}}
    for name, nd in data.items():
        out = {"stall_relative_s": STALL[name], "last_minute_5s": nd["last_minute_5s"], "series": {}}
        for kind, recs in nd["records"].items():
            before = [r for r in recs if r["relative_s"] <= STALL[name]]
            after = [r for r in recs if r["relative_s"] > STALL[name]]
            out["series"][kind] = {
                "count": len(recs),
                "last_before": before[-1] if before else None,
                "first_after": after[0] if after else None,
                "last": recs[-1] if recs else None,
                "snapshots": [],
            }
            if kind == "FUSION_QOS":
                out["series"][kind]["aggregate_300s"] = {}
                for label, lo, hi in (("pre", STALL[name] - 300, STALL[name]),
                                      ("post", STALL[name], STALL[name] + 300)):
                    window = [r for r in recs if lo <= r["relative_s"] < hi]
                    sums = {key: sum(int(r.get(key, 0)) for r in window)
                            for key in ("reports", "event_gaps", "crc_ok", "crc_error",
                                        "nak", "rx_timeout")}
                    sums["windows"] = len(window)
                    out["series"][kind]["aggregate_300s"][label] = sums
            for off in targets:
                target = STALL[name] + off
                candidates = recs if off == 999999 else [r for r in recs if abs(r["relative_s"] - target) <= 20]
                if candidates:
                    chosen = recs[-1] if off == 999999 else min(candidates, key=lambda r: abs(r["relative_s"] - target))
                    if chosen not in out["series"][kind]["snapshots"]:
                        out["series"][kind]["snapshots"].append(chosen)
        result["nodes"][name] = out
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(OUT)


if __name__ == "__main__":
    main()
