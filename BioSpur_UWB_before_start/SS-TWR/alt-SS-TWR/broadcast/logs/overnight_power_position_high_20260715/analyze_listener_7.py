#!/usr/bin/env python3
"""7-listener cross-analysis (position_high run). Same thinking as the single-listener
overnight F-section, scaled to the whole fleet: for EACH of the 7 co-located listeners,
bin its LPD polls to the power cells (by capture-dir timestamp window), test whether it
SEES the tag power sweep (cir_pwr / fp1 median per power, in dB rel that listener's MAX),
and check environment stability (hourly cir drift, robust CV). Fleet verdict = does ANY
listener respond to the 8.5 dB TX swing."""
import csv, glob, os, json, statistics, datetime, re

ROOT = os.path.dirname(os.path.abspath(__file__))
PRESETS = ["MAX", "M3", "M6", "M12", "POR"]
DB = {"MAX": 8.5, "M3": 5.5, "M6": 2.5, "M12": 0.0, "POR": 4.0}
LISTENERS = ["LB", "LE", "LF", "LA", "LCCF4", "L9336", "L955A"]

# cell windows from capture-session dir timestamps: {level}_3min_YYYYMMDD_HHMMSS = start
cells = []
for d in glob.glob(os.path.join(ROOT, "round_*", "*_3min_*")):
    m = re.search(r"/(MAX|M3|M6|M12|POR)_3min_(\d{8})_(\d{6})$", d)
    if not m:
        continue
    ts = datetime.datetime.strptime(m.group(2) + m.group(3), "%Y%m%d%H%M%S").timestamp()
    cells.append((ts, ts + 200.0, m.group(1)))
cells.sort()


def assign(ep):
    for s, e, lvl in cells:
        if s <= ep <= e:
            return lvl
    return None


def db_of(x, ref):
    import math
    return round(10 * math.log10(x / ref), 2) if (x > 0 and ref and ref > 0) else None


def analyze_listener(name):
    lpds = sorted(glob.glob(os.path.join(ROOT, "listener", name, "listener_*", "lpd.csv")))
    if not lpds:
        return None
    by_power = {p: {"fp1": [], "cir": [], "std": []} for p in PRESETS}
    hourly = {}
    t0 = cells[0][0] if cells else None
    total = 0
    for lpd in lpds:
        with open(lpd) as fh:
            for r in csv.DictReader(fh):
                try:
                    ep = float(r["host_epoch_s"]); fp1 = float(r["fp1"])
                    cir = float(r["cir_pwr"]); std = float(r["std_noise"])
                except Exception:
                    continue
                if cir <= 0:
                    continue
                total += 1
                lvl = assign(ep)
                if lvl:
                    by_power[lvl]["fp1"].append(fp1); by_power[lvl]["cir"].append(cir); by_power[lvl]["std"].append(std)
                if t0:
                    hourly.setdefault(int((ep - t0) // 3600), []).append(cir)
    ref_cir = statistics.median(by_power["MAX"]["cir"]) if by_power["MAX"]["cir"] else None
    per_power = {}
    for p in PRESETS:
        c = by_power[p]["cir"]
        if c:
            per_power[p] = {"n": len(c), "cir_med": round(statistics.median(c), 0),
                            "cir_dB_rel_MAX": db_of(statistics.median(c), ref_cir),
                            "fp1_med": round(statistics.median(by_power[p]["fp1"]), 0),
                            "std_noise_med": round(statistics.median(by_power[p]["std"]), 0),
                            "tx_dB_below_MAX": round(DB[p] - DB["MAX"], 1)}
    binned = sum(len(v["cir"]) for v in by_power.values())
    hr = {str(h): {"n": len(v), "cir_med": round(statistics.median(v), 0)} for h, v in sorted(hourly.items())}
    meds = [v["cir_med"] for v in hr.values()]
    allcir = [c for p in PRESETS for c in by_power[p]["cir"]]
    if allcir:
        gmed = statistics.median(allcir); gmad = statistics.median([abs(x - gmed) for x in allcir]) or 1
    else:
        gmed = gmad = 0
    swing = [abs(per_power[p]["cir_dB_rel_MAX"]) for p in per_power if per_power[p]["cir_dB_rel_MAX"] is not None]
    return {"lpd_files": len(lpds), "rows_total": total, "rows_binned": binned,
            "per_power": per_power,
            "max_abs_cir_dB_swing": round(max(swing), 2) if swing else None,
            "hourly_cir_med": hr,
            "env_cir_drift_pct": round(100 * (max(meds) - min(meds)) / statistics.median(meds), 1) if meds else None,
            "global_cir_med": round(gmed, 0),
            "cir_robust_cv_pct": round(100 * 1.4826 * gmad / gmed, 1) if gmed else None}


def main():
    out = {"n_cells": len(cells), "listeners": {}}
    for name in LISTENERS:
        r = analyze_listener(name)
        if r:
            out["listeners"][name] = r
    # fleet verdict
    swings = {n: v["max_abs_cir_dB_swing"] for n, v in out["listeners"].items()
              if v.get("max_abs_cir_dB_swing") is not None}
    out["fleet_max_abs_cir_dB_swing"] = round(max(swings.values()), 2) if swings else None
    out["fleet_worst_listener"] = max(swings, key=swings.get) if swings else None
    drifts = {n: v["env_cir_drift_pct"] for n, v in out["listeners"].items()
              if v.get("env_cir_drift_pct") is not None}
    out["fleet_max_env_drift_pct"] = round(max(drifts.values()), 1) if drifts else None
    with open(os.path.join(ROOT, "listener7_results.json"), "w") as f:
        json.dump(out, f, indent=1)

    # compact per-listener table
    print(f"{len(out['listeners'])}/7 listeners with data; cells={len(cells)}")
    print("listener | rows_binned | cir_dB_rel_MAX  [MAX  M3  M6  M12  POR] | max|swing| | env_drift%")
    for n in LISTENERS:
        v = out["listeners"].get(n)
        if not v:
            print(f"  {n:>6} : NO DATA"); continue
        pp = v["per_power"]
        cells_s = "  ".join(f"{pp[p]['cir_dB_rel_MAX']:+.2f}" if p in pp and pp[p]['cir_dB_rel_MAX'] is not None else "  -  " for p in PRESETS)
        print(f"  {n:>6} : {v['rows_binned']:>8} | {cells_s} | {v['max_abs_cir_dB_swing']} | {v['env_cir_drift_pct']}")
    print(f"\nFLEET: worst listener {out['fleet_worst_listener']} max|cir swing| = {out['fleet_max_abs_cir_dB_swing']} dB "
          f"(tag TX swing 8.5 dB); max env drift {out['fleet_max_env_drift_pct']}%")
    print("->", os.path.join(ROOT, "listener7_results.json"))


if __name__ == "__main__":
    main()
