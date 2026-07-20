#!/usr/bin/env python3
"""Per-link elevation angle for the position_high run — to decide whether "0 lock events"
means "Layer-2 immune" or just "never entered the steep-angle regime".

Elevation of a tag<->anchor link = asin(|dz| / d3d), the vertical angle of the line of
sight. |dz| and the horizontal distance are INVARIANT to the layout's known global z-sign
flip (both tag and anchor solved in the same frame), so the elevation ANGLE is trustworthy
even though absolute z is weakly constrained. Computed for HIGH and CENTER tag positions
against the same anchor layout, cross-referenced with lock-event counts (both 0)."""
import json, os, math
import numpy as np

REPO = "/mnt/nrf_ssd/nRF_dev/BioSpur_UWB_before_start"
HIGH = os.path.join(REPO, "SS-TWR/alt-SS-TWR/broadcast/logs/overnight_power_position_high_20260715")
CENTER = os.path.join(REPO, "SS-TWR/alt-SS-TWR/broadcast/logs/overnight_power_20260714")
LAYOUT = os.path.join(REPO, "logs/system_calibration_20260710_233443/anchor_layout.json")
WAND = ["BSCCF4", "BS9336", "BS955A"]
STEEP = 30.0        # Erlangen threshold where discrete Layer-2 reflection locks appeared
STEEP_HI = 37.0     # the "37 deg+" bar the operator asked about


def anchors():
    d = json.load(open(LAYOUT))
    return {a["label"]: np.array([a["x_mm"], a["y_mm"], a["z_mm"]]) for a in d["anchors"]}


def tag_positions(run_key):
    """Pooled high/center tag positions (V4io pipeline) from comparison.json."""
    c = json.load(open(os.path.join(HIGH, "comparison.json")))
    ts = c["tag_shift"]["A_v4io_t4"]
    key = "high_pos" if run_key == "high" else "center_pos"
    return {t: np.array(ts[t][key]) for t in WAND if t in ts and ts[t].get(key)}


def lock_count(folder):
    try:
        return json.load(open(os.path.join(folder, "results.json"))).get("E_lock_event_count")
    except Exception:
        return None


def elevations(anc, tags):
    rows = []
    for t, p in tags.items():
        for lab, ap in anc.items():
            dv = p - ap
            horiz = math.hypot(dv[0], dv[1])
            d3d = float(np.linalg.norm(dv))
            elev = math.degrees(math.asin(abs(dv[2]) / d3d)) if d3d > 0 else 0.0
            rows.append({"tag": t, "anchor": lab, "elev_deg": round(elev, 1),
                         "dz_mm": round(abs(float(dv[2])), 0), "horiz_mm": round(horiz, 0),
                         "range_mm": round(d3d, 0)})
    return rows


def summarize(rows):
    e = sorted([r["elev_deg"] for r in rows])
    return {"n_links": len(rows), "min": e[0], "median": round(float(np.median(e)), 1),
            "max": e[-1], "n_ge30": sum(x >= STEEP for x in e), "n_ge37": sum(x >= STEEP_HI for x in e)}


def main():
    anc = anchors()
    print("anchor heights (z_mm):", {k: round(float(v[2])) for k, v in anc.items()})
    out = {"layout": "V4io", "steep_deg": STEEP, "steep_hi_deg": STEEP_HI, "runs": {}}
    for run, folder in (("high", HIGH), ("center", CENTER)):
        tags = tag_positions(run)
        rows = elevations(anc, tags)
        s = summarize(rows)
        s["lock_events"] = lock_count(folder)
        out["runs"][run] = {"summary": s, "links": rows}
        print(f"\n===== {run.upper()} (lock events = {s['lock_events']}) =====")
        print(f"  elevation: min {s['min']}  median {s['median']}  MAX {s['max']} deg  "
              f"| links >=30: {s['n_ge30']}/{s['n_links']}  >=37: {s['n_ge37']}/{s['n_links']}")
        # per tag: steepest link
        for t in WAND:
            tr = [r for r in rows if r["tag"] == t]
            if not tr:
                continue
            st = max(tr, key=lambda r: r["elev_deg"])
            alld = sorted((r["elev_deg"] for r in tr))
            print(f"    {t}: steepest {st['elev_deg']}deg to anchor {st['anchor']} "
                  f"(dz={st['dz_mm']:.0f} horiz={st['horiz_mm']:.0f}); range {alld[0]}..{alld[-1]}deg")
    json.dump(out, open(os.path.join(HIGH, "elevation_analysis.json"), "w"), indent=1)

    # verdict
    hi = out["runs"]["high"]["summary"]
    print("\n===== VERDICT =====")
    if hi["max"] >= STEEP_HI and hi["lock_events"] == 0:
        print(f"MAX elevation {hi['max']}deg >= 37 AND 0 locks -> genuine 'home env immune to "
              f"Layer-2' (tested the steep regime, {hi['n_ge37']} links >=37deg, none locked).")
    elif hi["max"] >= STEEP and hi["lock_events"] == 0:
        print(f"MAX elevation {hi['max']}deg in [30,37) AND 0 locks -> entered the steep zone "
              f"({hi['n_ge30']} links >=30deg) but not the 37deg+ bar; partial immunity evidence.")
    else:
        print(f"MAX elevation only {hi['max']}deg (<30) -> '0 lock' is NOT a pass: the raised "
              f"position still did not create steep links. Layer-2 immunity UNTESTED at home.")
    print("->", os.path.join(HIGH, "elevation_analysis.json"))


if __name__ == "__main__":
    main()
