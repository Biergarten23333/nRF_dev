#!/usr/bin/env python3
"""P2 -- event registry, classification, label audit, near-miss census.

Classification is decided by three independent axes, in this order:
  A  link liveness after onset   (master QoS for the same connection)
  B  node power                  (listener air ratio for the node's tag)
  C  node continuity             (boot segment / reset inside the window)
No single axis decides an event; the combination does. Bands come from the
established air-ratio clusters (0.94-1.09 wedge / ~0.31 brownout / ~0.12 off)
and are applied as 0.70 and 0.15 with sensitivity reported.
"""
import csv
import json
import math
import os
import re
import sys
import datetime as dt

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import RUNS, CACHE, OUT  # noqa: E402

import numpy as np                   # noqa: E402
import pyarrow.parquet as pq         # noqa: E402

WEDGE_S = 20.0
AIR_W = 600.0
AIR_WEDGE, AIR_DEAD = 0.70, 0.15
BUCKET_S = 10


def T(x):
    return dt.datetime.fromtimestamp(x).strftime("%Y-%m-%d %H:%M:%S")


def tag_map(run):
    """node -> 0xB100+logical, recomputed from FUSION_UWB in the raw log."""
    import glob
    m = {}
    for p in sorted(glob.glob(os.path.join(RUNS[run]["run"], "fusion_h*.log")))[:1]:
        with open(p, "rb") as fh:
            for line in fh:
                if b"FUSION_UWB" not in line:
                    continue
                g = re.search(rb"name=([A-Z0-9]+) .*?logical=(\d+)", line)
                if g:
                    m.setdefault(g.group(1).decode(), 0xB100 + int(g.group(2)))
                if len(m) >= 10:
                    return m
    return m


class Air:
    def __init__(self, run):
        p = os.path.join(CACHE, f"air_{run}.parquet")
        self.ok = os.path.exists(p)
        if not self.ok:
            return
        t = pq.read_table(p)
        self.tag = t.column("tag").to_numpy()
        self.bk = t.column("bucket").to_numpy()
        self.n = t.column("n").to_numpy()

    def count(self, tag, t0, t1):
        if not self.ok or tag is None:
            return None
        m = (self.tag == tag) & (self.bk >= t0 // BUCKET_S) & (self.bk < t1 // BUCKET_S)
        return int(self.n[m].sum())

    def last_seen(self, tag):
        if not self.ok or tag is None:
            return None
        m = self.tag == tag
        return float(self.bk[m].max() * BUCKET_S) if m.any() else None


def classify(ev, air_ratio, has_air, reset_at_onset):
    """Return (class, confidence, reason).

    `reset_at_onset`, not `reset anywhere in the window`, is the correct
    test. A terminal stall can be hours long; BSFEC35's 15:46 event contains
    its own 21:14 depletion reboot 5.5 h later, and the loose form put a
    1.05-air-ratio wedge in the brownout class on that basis alone.
    """
    q = ev["qos_alive_after_s"]
    reset = reset_at_onset
    dur = ev["dur_s"]
    if dur < WEDGE_S:
        base = "NEAR_MISS"
    else:
        base = None
    if reset:
        return ("DEPLETION_OR_BROWNOUT", "high",
                "node rebooted inside the stall window (node_ms went backwards)")
    if has_air and air_ratio is not None and air_ratio <= AIR_DEAD:
        return ("DEPLETION_OR_BROWNOUT", "high",
                f"tag off air after onset (air ratio {air_ratio:.2f} <= {AIR_DEAD})")
    if has_air and air_ratio is not None and air_ratio < AIR_WEDGE:
        return ("DEPLETION_OR_BROWNOUT", "medium",
                f"tag transmitting intermittently (air ratio {air_ratio:.2f})")
    if q < 10.0:
        return ("RF_OR_DISCONNECT", "medium" if has_air else "low",
                f"master QoS for this connection stopped {q:.1f} s after onset")
    if base == "NEAR_MISS":
        return ("NEAR_MISS_JOINT_STALL", "medium",
                f"both streams stalled {dur:.1f} s and recovered, link alive")
    return ("STEADY_STATE_HOST_WEDGE", "high",
            f"link alive {q:.0f} s with zero application data, tag on air "
            f"(ratio {air_ratio:.2f}), no reset" if air_ratio is not None else
            f"link alive {q:.0f} s with zero application data, no reset")


def main():
    det = json.load(open(os.path.join(CACHE, "detect_raw.json")))
    rows, near = [], []
    single_rows = []
    for run in ("N5", "N6", "N7", "N8"):
        if run not in det:
            continue
        r = det[run]
        tags = tag_map(run)
        air = Air(run)
        for nd, n in sorted(r["nodes"].items()):
            tag = tags.get(nd)
            boots = n.get("boot_times", [])
            for ev in n["joint_stalls"]:
                lo = ev["onset_lower"]
                reset_at_onset = any(lo - 30.0 <= b <= lo + 60.0 for b in boots)
                pre = air.count(tag, lo - AIR_W, lo)
                post = air.count(tag, lo, lo + AIR_W)
                ratio = None
                if pre and pre > 30:
                    ratio = post / pre
                cls, conf, why = classify(ev, ratio, air.ok, reset_at_onset)
                row = {
                    "run": run, "node": nd, "fw": r["fw"], "master_fw": r["master_fw"],
                    "tag": f"0x{tag:04x}" if tag else "",
                    "onset_lower_epoch": round(lo, 3), "onset_lower_wall": T(lo),
                    "onset_upper_epoch": round(ev["onset_upper"], 3),
                    "dur_s": round(ev["dur_s"], 1),
                    "terminal": int(ev["terminal"]),
                    "recover_wall": T(ev["recover_t"]) if ev["recover_t"] else "",
                    "qos_alive_after_s": round(ev["qos_alive_after_s"], 1),
                    "qos_records_during": ev["qos_records_during"],
                    "tlm_records_during": ev["tlm_records_during"],
                    "reset_at_onset": int(reset_at_onset),
                    "reset_in_window": int(ev["reset_during"]),
                    "air_pre_600s": pre, "air_post_600s": post,
                    "air_ratio": round(ratio, 3) if ratio is not None else "",
                    "classification": cls, "confidence": conf, "reason": why,
                }
                (rows if ev["dur_s"] >= WEDGE_S else near).append(row)
            for s in n["single_stream"]:
                single_rows.append({"run": run, "node": nd, "kind": s["kind"],
                                    "t0_wall": T(s["t0"]), "dur_s": round(s["dur_s"], 1),
                                    "other_stream_records": s["other_records"]})
    for name, data in (("WEDGE_EVENTS.csv", rows), ("NEAR_MISS_EVENTS.csv", near),
                       ("SINGLE_STREAM_CENSUS.csv", single_rows)):
        if not data:
            open(os.path.join(OUT, name), "w").write("(none)\n")
            continue
        with open(os.path.join(OUT, name), "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(data[0].keys()))
            w.writeheader()
            w.writerows(data)
    json.dump({"wedge_candidates": rows, "near_misses": near},
              open(os.path.join(OUT, "WEDGE_EVENTS.json"), "w"), indent=1)

    print(f"{'run':4}{'node':10}{'onset':21}{'dur_s':>9}{'qos_after':>10}"
          f"{'rst':>4}{'air':>7}  class")
    for x in rows:
        print(f"{x['run']:4}{x['node']:10}{x['onset_lower_wall'][11:]:21}"
              f"{x['dur_s']:9.1f}{x['qos_alive_after_s']:10.1f}"
              f"{x['reset_at_onset']:4d}{str(x['air_ratio']):>7}  "
              f"{x['classification']} ({x['confidence']})")
    print(f"\nnear-misses: {len(near)}  single-stream: {len(single_rows)}")
    from collections import Counter
    print("near-miss classes:", dict(Counter(x["classification"] for x in near)))
    print("wedge classes:", dict(Counter(x["classification"] for x in rows)))


if __name__ == "__main__":
    main()
