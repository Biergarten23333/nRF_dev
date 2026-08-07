#!/usr/bin/env python3
"""Freeze the two wedged-link recordings before BSF1120's battery ends its.

Three artefacts per wedged board, because a QoS series on its own cannot answer
the question that matters:

  1. the full per-second QoS series, starting >= 10 min BEFORE onset, so there is
     a pre-wedge baseline to compare against;
  2. the SAME windows for the eight healthy boards, which is what separates "this
     board is depleting" from "the whole fleet is drifting";
  3. every record of any type in onset +/- 30 s, unfiltered.
"""
import csv
import glob
import json
import os
import re
import sys
from collections import defaultdict

RUN = "/mnt/nrf_ssd/nRF_dev/BioSpur_Fusion/UWB_Part/logs/v44_fleet_20260807"
OUT = os.path.join(RUN, "J_WEDGE")
LOGS = sorted(glob.glob(os.path.join(RUN, "I_RUN", "fusion_h0*.log")))

T0 = 1786108497.195507                      # run open, from the first log line
ONSET = {"BSFEC35": T0 + 1873.330, "BSF1120": T0 + 5893.342}
PRE_S = 900.0                               # 15 min of pre-wedge baseline
ALL_NODES = ["BSF1120", "BSF31CC", "BSF3C79", "BSF44AD", "BSF6C53",
             "BSF8BC4", "BSFAA61", "BSFB165", "BSFC2CC", "BSFEC35"]

QOS_FIELDS = ["master_ms", "handle", "window_start_ms", "window_ms", "reports",
              "event_gaps", "crc_ok", "crc_error", "nak", "rx_timeout",
              "imu_epoch_defer_drop", "delivered_imu", "delivered_uwb",
              "delivered_ctl", "first_event", "last_event"]
QOS_RE = {f: re.compile(r"\b" + f + r"=(-?\d+)") for f in QOS_FIELDS}
NAME_RE = re.compile(r"name=([A-Z0-9]+)")

earliest = min(ONSET.values()) - PRE_S
windows = {n: (ONSET[n] - 30.0, ONSET[n] + 30.0) for n in ONSET}

qos = defaultdict(list)                     # node -> rows
raw = {n: [] for n in ONSET}                # node -> every line in +/-30 s
counts = defaultdict(int)

for path in LOGS:
    with open(path, "rb") as fh:
        for bline in fh:
            line = bline.decode("utf-8", "replace")
            sp = line.split(None, 3)
            if len(sp) < 3:
                continue
            try:
                t = float(sp[0])
            except ValueError:
                continue
            if t < earliest:
                continue
            for node, (lo, hi) in windows.items():
                if lo <= t <= hi:
                    raw[node].append(line.rstrip("\n"))
            if sp[2] != "FUSION_QOS":
                continue
            m = NAME_RE.search(line)
            if not m:
                continue
            node = m.group(1)
            row = {"epoch": f"{t:.6f}", "t_run_s": f"{t - T0:.3f}", "node": node}
            for f in QOS_FIELDS:
                mm = QOS_RE[f].search(line)
                row[f] = mm.group(1) if mm else ""
            for w_node, onset in ONSET.items():
                row["dt_" + w_node] = f"{t - onset:.3f}"
            qos[node].append(row)
            counts[node] += 1

os.makedirs(OUT, exist_ok=True)
hdr = (["epoch", "t_run_s", "node"] + QOS_FIELDS +
       ["dt_" + n for n in ONSET])

# --- 1 + 2: one CSV holding the wedged board and all eight controls ---------
for wedged in ONSET:
    lo = ONSET[wedged] - PRE_S
    p = os.path.join(OUT, f"qos_{wedged}_with_controls.csv")
    n_rows = 0
    with open(p, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=hdr)
        w.writeheader()
        for node in ALL_NODES:
            for row in qos[node]:
                if float(row["epoch"]) >= lo:
                    w.writerow(row)
                    n_rows += 1
    print(f"{os.path.basename(p)}: {n_rows} rows")

# --- 3: everything in onset +/- 30 s ---------------------------------------
for node, lines in raw.items():
    p = os.path.join(OUT, f"onset_pm30s_{node}.log")
    with open(p, "w") as fh:
        fh.write("\n".join(lines) + "\n")
    print(f"{os.path.basename(p)}: {len(lines)} records, {os.path.getsize(p)/1e6:.1f} MB")

# --- the control comparison, computed here so it cannot be skipped ----------
def mean_of(node, lo, hi, field):
    vals = [int(r[field]) for r in qos[node]
            if lo <= float(r["epoch"]) <= hi and r[field] not in ("", None)]
    return (sum(vals) / len(vals), len(vals)) if vals else (float("nan"), 0)

report = {}
now = max(float(r["epoch"]) for r in qos["BSF1120"])
bands = {
    "BSF1120_pre_wedge":   (ONSET["BSF1120"] - 900, ONSET["BSF1120"]),
    "BSF1120_early_wedge": (ONSET["BSF1120"], ONSET["BSF1120"] + 900),
    "BSF1120_recent_900s": (now - 900, now),
}
for band, (lo, hi) in bands.items():
    row = {}
    for node in ALL_NODES:
        mr, n = mean_of(node, lo, hi, "reports")
        mg, _ = mean_of(node, lo, hi, "event_gaps")
        mc, _ = mean_of(node, lo, hi, "crc_error")
        if n:
            row[node] = {"reports": round(mr, 3), "event_gaps": round(mg, 4),
                         "crc_error": round(mc, 4), "windows": n}
    report[band] = row
json.dump(report, open(os.path.join(OUT, "control_comparison.json"), "w"), indent=1)

for band in bands:
    print(f"\n=== {band} ===")
    print(f"  {'node':9} {'reports':>8} {'gaps':>7} {'crc_err':>8} {'n':>5}")
    for node, v in sorted(report[band].items()):
        mark = " <<<" if node == "BSF1120" else ""
        print(f"  {node:9} {v['reports']:8.3f} {v['event_gaps']:7.4f} "
              f"{v['crc_error']:8.4f} {v['windows']:5d}{mark}")
