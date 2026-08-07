#!/usr/bin/env python3
"""L3 Part B -- end-to-end latency, at-capacity vs degraded, on data already on disk.

Method notes that matter more than the code:

* Drift is larger than the signal (+/-20 ppm over 132 min is +/-158 ms against a
  ~52 ms latency), so it is removed FIRST, per node per window per stream.
* The drift fit is a LOWER-ENVELOPE fit, not a plain least squares: bin by 60 s,
  take the minimum offset in each bin, fit through the minima. A plain fit is
  dragged around by the queueing distribution -- which is the thing being
  measured -- so it would partially absorb the signal.
* master_ms has 1 ms resolution. That is the measurement floor; nothing below
  1 ms is real.
* IMU latency is measured from base_us, the FIRST sample of a batch of ten
  spanning 45 ms. That adds a constant ~45 ms of batching which cancels in the
  min-subtraction, so IMU and UWB minima are not comparable in absolute terms.
  Within a stream, across windows, they are.
"""
import array
import glob
import json
import os
import re
import numpy as np

RUN = "/mnt/nrf_ssd/nRF_dev/BioSpur_Fusion/UWB_Part/logs/v44_fleet_20260807/I_RUN"
OUT = "/mnt/nrf_ssd/nRF_dev/BioSpur_Fusion/UWB_Part/logs/v44_fleet_20260807/J_WEDGE"

T0 = 1786108497.195507
BASE_LO, BASE_HI = T0 + 40.0, 1786116500.083039     # at capacity, not scanning
DEG_LO = 1786116500.083039                           # scanning resumed

TIER = {"BSF8BC4": "A", "BSF31CC": "A", "BSF3C79": "A", "BSFB165": "A",
        "BSF1120": "A",
        "BSF44AD": "B", "BSFAA61": "B", "BSFC2CC": "B", "BSF6C53": "B",
        "BSFEC35": "-"}

UWB = re.compile(rb"FUSION_UWB proto=7 name=([A-Z0-9]+) master_ms=(\d+) .*? frame_us=(\d+)")
IMU = re.compile(rb"FUSION_IMU proto=7 name=([A-Z0-9]+) master_ms=(\d+) seq=\d+ base_us=(\d+)")

# key -> (master_ms array, offset array)
data = {}


def add(node, window, stream, ms, off):
    k = (node, window, stream)
    if k not in data:
        data[k] = (array.array("q"), array.array("q"))
    data[k][0].append(ms)
    data[k][1].append(off)


n_lines = 0
for path in sorted(glob.glob(os.path.join(RUN, "fusion_h0*.log"))):
    with open(path, "rb") as fh:
        for line in fh:
            n_lines += 1
            if b"FUSION_UWB" in line:
                m, stream = UWB.search(line), "UWB"
            elif b"FUSION_IMU" in line:
                m, stream = IMU.search(line), "IMU"
            else:
                continue
            if not m:
                continue
            sp = line.split(None, 1)
            try:
                t = float(sp[0])
            except ValueError:
                continue
            if t < BASE_LO:
                continue
            window = "at_capacity" if t < BASE_HI else "degraded"
            node = m.group(1).decode()
            ms = int(m.group(2))
            ts = int(m.group(3))
            add(node, window, stream, ms, ms * 1000 - ts)

print(f"scanned {n_lines} lines, {len(data)} groups, "
      f"{sum(len(v[0]) for v in data.values())} records")


def envelope_fit(t_ms, off_us, bin_ms=60000):
    """Fit a line through per-bin minima. Returns (slope_us_per_ms, intercept)."""
    b = (t_ms - t_ms[0]) // bin_ms
    edges = np.unique(b)
    if edges.size < 3:
        return None
    bt, bo = [], []
    for e in edges:
        sel = b == e
        if sel.sum() < 10:
            continue
        bt.append(t_ms[sel].mean())
        bo.append(off_us[sel].min())
    if len(bt) < 3:
        return None
    return np.polyfit(np.array(bt, float), np.array(bo, float), 1)


results = {}
drift = {}
for (node, window, stream), (a_ms, a_off) in sorted(data.items()):
    t = np.frombuffer(a_ms, dtype=np.int64).astype(np.float64)
    o = np.frombuffer(a_off, dtype=np.int64).astype(np.float64)
    if t.size < 200:
        results[f"{node}|{window}|{stream}"] = {"n": int(t.size),
                                                "status": "INSUFFICIENT"}
        continue
    fit = envelope_fit(t, o)
    if fit is None:
        results[f"{node}|{window}|{stream}"] = {"n": int(t.size),
                                                "status": "INSUFFICIENT_BINS"}
        continue
    slope, inter = fit
    ppm = slope * 1e3        # us per ms -> us per s -> ppm (1 us/s == 1 ppm)
    resid = o - (slope * t + inter)
    lat = resid - resid.min()
    drift[f"{node}|{window}|{stream}"] = round(float(ppm), 2)
    results[f"{node}|{window}|{stream}"] = {
        "n": int(t.size), "tier": TIER.get(node, "?"),
        "drift_ppm": round(float(ppm), 2),
        "p50_ms": round(float(np.percentile(lat, 50)) / 1000, 2),
        "p95_ms": round(float(np.percentile(lat, 95)) / 1000, 2),
        "p99_ms": round(float(np.percentile(lat, 99)) / 1000, 2),
        "max_ms": round(float(lat.max()) / 1000, 2),
        "span_min": round(float((t[-1] - t[0]) / 60000), 1),
        "status": "OK",
    }

json.dump(results, open(os.path.join(OUT, "latency_by_node.json"), "w"), indent=1)

# ---- drift sanity ---------------------------------------------------------
# The slopes are NOT scattered about zero: every node sits near -33 ppm. That is
# a COMMON-MODE reference offset between the DK's k_uptime clock and the nodes'
# TIMER2, not ten independent crystal errors -- ten independent crystals would
# not cluster inside 4 ppm. So the outlier test must be fleet-relative (median
# +/- 3*MAD), not zero-relative. A zero-relative test rejects all ten good nodes
# and keeps only the genuinely bad fits, which is exactly backwards.
vals = np.array(sorted(drift.values()))
med = float(np.median(vals))
mad = float(np.median(np.abs(vals - med))) or 1.0
LIM = 3.0 * 1.4826 * mad
SHORT = {k for k, v in results.items()
         if v.get("status") == "OK" and v.get("span_min", 0) < 20.0}
BAD = {k for k, v in drift.items() if abs(v - med) > LIM} | SHORT
print(f"\n=== drift fits (ppm) ===")
print(f"  fleet median {med:.2f} ppm, MAD {mad:.2f}, "
      f"outlier band {med:.2f} +/- {LIM:.2f}; span floor 20 min")
for k in sorted(drift):
    why = ""
    if k in SHORT:
        why = f"  * span {results[k]['span_min']:.0f} min"
    elif k in BAD:
        why = "  * drift outlier"
    print(f"  {k:34} {drift[k]:8.2f}{why}")
print(f"  node-to-node spread (kept): "
      f"{min(v for k,v in drift.items() if k not in BAD):.2f} .. "
      f"{max(v for k,v in drift.items() if k not in BAD):.2f} ppm")

print("\n=== per stream / window / tier ===")
agg = {}
for k, v in results.items():
    if v.get("status") != "OK" or k in BAD:
        continue
    node, window, stream = k.split("|")
    for grp in ((stream, window, "ALL"), (stream, window, v["tier"])):
        agg.setdefault(grp, []).append((v["p95_ms"], v["p99_ms"], v["p50_ms"], v["n"]))

print(f"{'stream':6} {'window':13} {'tier':5} {'nodes':>5} {'records':>9} "
      f"{'p50':>7} {'p95':>7} {'p99':>7}")
table = {}
for grp in sorted(agg):
    rows = agg[grp]
    n = sum(r[3] for r in rows)
    p50 = sum(r[2] for r in rows) / len(rows)
    p95 = sum(r[0] for r in rows) / len(rows)
    p99 = sum(r[1] for r in rows) / len(rows)
    table["|".join(grp)] = {"nodes": len(rows), "records": n,
                            "p50_ms": round(p50, 2), "p95_ms": round(p95, 2),
                            "p99_ms": round(p99, 2)}
    print(f"{grp[0]:6} {grp[1]:13} {grp[2]:5} {len(rows):5d} {n:9d} "
          f"{p50:7.2f} {p95:7.2f} {p99:7.2f}")

print("\n=== B4 dose-response: tier B loses more events, so it must wait longer ===")
dose = {}
for stream in ("UWB", "IMU"):
    a = table.get(f"{stream}|degraded|A")
    b = table.get(f"{stream}|degraded|B")
    c = table.get(f"{stream}|at_capacity|ALL")
    if not (a and b):
        print(f"  {stream}: INSUFFICIENT")
        continue
    holds = b["p95_ms"] > a["p95_ms"]
    dose[stream] = {"tierA_p95": a["p95_ms"], "tierB_p95": b["p95_ms"],
                    "delta_ms": round(b["p95_ms"] - a["p95_ms"], 2),
                    "prediction_holds": bool(holds)}
    print(f"  {stream}: tier A p95 {a['p95_ms']:.2f} ms (18.42 ev/s) vs "
          f"tier B p95 {b['p95_ms']:.2f} ms (16.18 ev/s)  "
          f"delta {b['p95_ms']-a['p95_ms']:+.2f} ms  -> "
          f"{'HOLDS' if holds else 'FAILS -- model is wrong'}")
    if c:
        print(f"       at-capacity baseline p95 {c['p95_ms']:.2f} ms "
              f"(20.09 ev/s), degraded-vs-baseline "
              f"{(a['p95_ms']+b['p95_ms'])/2 - c['p95_ms']:+.2f} ms")

print("\n=== headline: p95 difference, degraded minus at-capacity ===")
diff = {}
for stream in ("UWB", "IMU"):
    c = table.get(f"{stream}|at_capacity|ALL")
    d = table.get(f"{stream}|degraded|ALL")
    if c and d:
        diff[stream] = {"at_capacity_p95_ms": c["p95_ms"],
                        "degraded_p95_ms": d["p95_ms"],
                        "delta_ms": round(d["p95_ms"] - c["p95_ms"], 2),
                        "delta_pct": round(100*(d["p95_ms"]-c["p95_ms"])/c["p95_ms"], 1)}
        print(f"  {stream}: {c['p95_ms']:.2f} -> {d['p95_ms']:.2f} ms  "
              f"({d['p95_ms']-c['p95_ms']:+.2f} ms, "
              f"{100*(d['p95_ms']-c['p95_ms'])/c['p95_ms']:+.1f}%)")

json.dump({"per_node": results, "aggregate": table,
           "drift_fleet_median_ppm": round(med, 2),
           "excluded": sorted(BAD), "dose_response": dose, "p95_difference": diff},
          open(os.path.join(OUT, "latency_summary.json"), "w"), indent=1)
print("\nwrote latency_by_node.json, latency_summary.json")
