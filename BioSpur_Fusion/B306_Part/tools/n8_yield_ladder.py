#!/usr/bin/env python3
"""N8 section 6 deliverables: yield ladder, sequence-accounted loss, event log.

THE TRAP THIS IS WRITTEN AROUND
-------------------------------
The brief is explicit that `q_drop` understates loss roughly tenfold, and that
IMU `seq` is 16-bit -- wrapping every 327.68 s at 200 Hz -- so on a badly
degraded board a large gap ALIASES PAST THE WRAP and silently reads small. That
is what once made BSFAA61 report 35 % when the truth was 8 %.

So every IMU figure here is computed two independent ways and both are printed:

  seq-accounted : sum of (seq_delta - 10) across consecutive records, which is
                  exact for gaps < 65536 samples and WRONG (aliased low) beyond
  nominal       : samples that should have arrived in the wall-clock span at
                  200 Hz, against samples actually delivered (records x 10)

**Where they disagree, the nominal figure is the true one and the difference is
the aliasing.** Both are reported side by side, always, per the brief.
"""
import collections
import glob
import json
import os
import re
import sys

RUN = "/mnt/nrf_ssd/nRF_dev/BioSpur_Fusion/UWB_Part/logs/v44_fleet_20260807/I_RUN"
NODES = ["BSF1120", "BSF31CC", "BSF3C79", "BSF44AD", "BSF6C53",
         "BSF8BC4", "BSFAA61", "BSFB165", "BSFC2CC", "BSFEC35"]

UWB = re.compile(rb"FUSION_UWB proto=7 name=([A-Z0-9]+) .*? sweep=(\d+) .*? valid=0x([0-9a-f]+)")
IMU = re.compile(rb"FUSION_IMU proto=7 name=([A-Z0-9]+) master_ms=\d+ seq=(\d+)")

# node -> hour -> stats
uwb_h = collections.defaultdict(lambda: collections.defaultdict(
    lambda: {"n": 0, "ge8": 0, "ge7": 0, "gap": 0, "first": None, "last": None,
             "t0": None, "t1": None}))
imu_h = collections.defaultdict(lambda: collections.defaultdict(
    lambda: {"n": 0, "seqgap": 0, "first": None, "last": None,
             "t0": None, "t1": None}))
prev_sweep, prev_seq = {}, {}

for path in sorted(glob.glob(os.path.join(RUN, "fusion_h0*.log"))):
    with open(path, "rb") as fh:
        for line in fh:
            if b"FUSION_UWB" in line:
                m = UWB.search(line)
                if not m:
                    continue
                try:
                    t = float(line.split(None, 1)[0])
                except ValueError:
                    continue
                node = m.group(1).decode()
                sw = int(m.group(2))
                mask = int(m.group(3), 16)
                hr = int(t // 3600)
                d = uwb_h[node][hr]
                d["n"] += 1
                if mask == 0xFF:
                    d["ge8"] += 1
                if bin(mask).count("1") >= 7:
                    d["ge7"] += 1
                if node in prev_sweep:
                    step = sw - prev_sweep[node]
                    if 1 < step < 100000:
                        d["gap"] += step - 1
                prev_sweep[node] = sw
                d["t0"] = d["t0"] or t
                d["t1"] = t
            elif b"FUSION_IMU" in line:
                m = IMU.search(line)
                if not m:
                    continue
                try:
                    t = float(line.split(None, 1)[0])
                except ValueError:
                    continue
                node = m.group(1).decode()
                sq = int(m.group(2))
                hr = int(t // 3600)
                d = imu_h[node][hr]
                d["n"] += 1
                if node in prev_seq:
                    step = (sq - prev_seq[node]) % 65536
                    if step > 10:
                        d["seqgap"] += step - 10
                prev_seq[node] = sq
                d["t0"] = d["t0"] or t
                d["t1"] = t

hours = sorted({h for n in uwb_h for h in uwb_h[n]})
h0 = hours[0]

print("=" * 96)
print("UWB YIELD LADDER  (per node per hour)")
print("  delivered -> 8/8 -> 7+/8   |  sweep-sequence gaps")
print("=" * 96)
print(f"{'node':9} {'hr':>3} {'delivered':>10} {'Hz':>6} {'8/8':>9} {'7+/8':>9} {'sweep gaps':>11}")
uwb_tot = collections.defaultdict(lambda: [0, 0, 0, 0])
for node in NODES:
    for hr in hours:
        d = uwb_h[node].get(hr)
        if not d or d["n"] == 0:
            continue
        span = max(d["t1"] - d["t0"], 1e-9)
        hz = d["n"] / span
        print(f"{node:9} {hr-h0:3d} {d['n']:10d} {hz:6.3f} "
              f"{100.0*d['ge8']/d['n']:8.4f}% {100.0*d['ge7']/d['n']:8.4f}% {d['gap']:11d}")
        a = uwb_tot[node]
        a[0] += d["n"]; a[1] += d["ge8"]; a[2] += d["ge7"]; a[3] += d["gap"]

print("-" * 96)
print(f"{'TOTAL':9} {'':3} {'delivered':>10} {'':6} {'8/8':>9} {'7+/8':>9} {'sweep gaps':>11}")
for node in NODES:
    a = uwb_tot[node]
    if a[0] == 0:
        print(f"{node:9} {'':3} {0:10d}  INSUFFICIENT (no UWB records)"); continue
    print(f"{node:9} {'':3} {a[0]:10d} {'':6} {100.0*a[1]/a[0]:8.4f}% "
          f"{100.0*a[2]/a[0]:8.4f}% {a[3]:11d}")

print()
print("=" * 96)
print("IMU YIELD LADDER  (per node, whole run)")
print("  BOTH accountings printed. Disagreement == 16-bit seq wrap aliasing.")
print("=" * 96)
print(f"{'node':9} {'records':>9} {'samples':>10} {'span_s':>8} {'nominal':>10} "
      f"{'nominal loss':>13} {'seq-acct loss':>14} {'verdict':>10}")
imu_rows = {}
for node in NODES:
    tot = {"n": 0, "seqgap": 0, "t0": None, "t1": None}
    for hr in hours:
        d = imu_h[node].get(hr)
        if not d or d["n"] == 0:
            continue
        tot["n"] += d["n"]; tot["seqgap"] += d["seqgap"]
        tot["t0"] = tot["t0"] if tot["t0"] is not None else d["t0"]
        tot["t1"] = d["t1"]
    if tot["n"] == 0:
        print(f"{node:9} INSUFFICIENT (no IMU records)"); continue
    span = tot["t1"] - tot["t0"]
    delivered = tot["n"] * 10
    nominal = span * 200.0
    nloss = 100.0 * (nominal - delivered) / nominal if nominal > 0 else float("nan")
    sloss = 100.0 * tot["seqgap"] / (tot["seqgap"] + delivered) if delivered else float("nan")
    verdict = "agree" if abs(nloss - sloss) < 0.5 else "ALIASED"
    print(f"{node:9} {tot['n']:9d} {delivered:10d} {span:8.0f} {nominal:10.0f} "
          f"{nloss:12.4f}% {sloss:13.4f}% {verdict:>10}")
    imu_rows[node] = {"records": tot["n"], "samples": delivered, "span_s": round(span, 1),
                      "nominal_samples": round(nominal), "nominal_loss_pct": round(nloss, 4),
                      "seq_accounted_loss_pct": round(sloss, 4), "verdict": verdict}

json.dump({"uwb": {n: {"delivered": uwb_tot[n][0], "ge8": uwb_tot[n][1],
                       "ge7": uwb_tot[n][2], "sweep_gaps": uwb_tot[n][3]} for n in NODES},
           "imu": imu_rows},
          open(os.path.join(os.path.dirname(RUN), "J_WEDGE", "n8_yield.json"), "w"), indent=1)
print("\nwrote J_WEDGE/n8_yield.json")
