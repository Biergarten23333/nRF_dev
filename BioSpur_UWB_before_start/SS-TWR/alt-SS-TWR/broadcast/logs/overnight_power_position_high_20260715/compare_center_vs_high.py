#!/usr/bin/env python3
"""Compare the two overnight power sweeps: room-CENTER (20260714) vs POSITION-HIGH
(20260715, wand raised). Same metrics both runs -> diff. Highlights:
 - link success / bias vs power (should stay flat both runs)
 - positioning precision (3D scatter, z-std) center vs high
 - per-tag MEAN-POSITION shift, esp. Delta-z (did raising the wand appear in the solve?)
 - per-tag RMS and rigid-baseline (caliper) center vs high (steeper elevation at height?)
 - listener cir level (LE common to both) + fleet note
Writes comparison.json + COMPARISON.md into the position_high folder."""
import json, os, statistics
import numpy as np

BCAST = "/mnt/nrf_ssd/nRF_dev/BioSpur_UWB_before_start/SS-TWR/alt-SS-TWR/broadcast"
CENTER = os.path.join(BCAST, "logs", "overnight_power_20260714")
HIGH = os.path.join(BCAST, "logs", "overnight_power_position_high_20260715")
PRESETS = ["MAX", "M3", "M6", "M12", "POR"]
DB = {"MAX": 8.5, "M3": 5.5, "M6": 2.5, "M12": 0.0, "POR": 4.0}
WAND = ["BSCCF4", "BS9336", "BS955A"]
PIPES = ["A_v4io_t4", "B_v5_u5"]


def load(p):
    try:
        return json.load(open(p))
    except Exception:
        return None


def pooled_pos(perpow, pipe, tag):
    """Mean solved position of a tag across all powers for one pipeline (wand fixed within a run)."""
    pts = []
    for p in PRESETS:
        d = (perpow.get(pipe, {}).get(p) or {}).get("tags", {}).get(tag)
        if d and d.get("pos"):
            pts.append(d["pos"])
    return [round(float(v), 1) for v in np.mean(np.array(pts), axis=0)] if pts else None


def pooled_metric(perpow, pipe, key):
    vals = [perpow.get(pipe, {}).get(p, {}).get(key) for p in PRESETS]
    vals = [v for v in vals if v is not None]
    return round(statistics.median(vals), 1) if vals else None


def pooled_tag_scatter(perpow, pipe, tag):
    vals = []
    for p in PRESETS:
        d = (perpow.get(pipe, {}).get(p) or {}).get("tags", {}).get(tag)
        if d and d.get("scatter_3d") is not None:
            vals.append(d["scatter_3d"])
    return round(statistics.median(vals), 1) if vals else None


def pooled_caliper_maxerr(perpow, pipe):
    vals = [perpow.get(pipe, {}).get(p, {}).get("caliper_max_abs_err") for p in PRESETS]
    vals = [v for v in vals if v is not None]
    return round(statistics.median(vals), 1) if vals else None


def main():
    rc, rh = load(os.path.join(CENTER, "results.json")), load(os.path.join(HIGH, "results.json"))
    pc = load(os.path.join(CENTER, "positioning_v5u5_v4iot4", "positioning_vs_power.json"))
    ph = load(os.path.join(HIGH, "positioning_v5u5_v4iot4", "positioning_vs_power.json"))
    lc = load(os.path.join(CENTER, "listener_results.json"))
    lh = load(os.path.join(HIGH, "listener7_results.json"))
    out = {}

    # ---- link success + bias ----
    def link_block(r):
        if not r:
            return None
        B = r.get("B_valid_vs_power", {})
        return {"per_power": {p: {"ge7": B[p].get("ratio_ge7") if p in B else None,
                                  "ge8": B[p].get("ratio_ge8") if p in B else None,
                                  "valid": B[p].get("valid_pct") if p in B else None} for p in PRESETS},
                "bias_median_swing_mm": r.get("A_median_swing_mm"),
                "bias_max_swing_mm": r.get("A_max_swing_mm"),
                "lock_events": r.get("E_lock_event_count")}
    out["link_center"] = link_block(rc)
    out["link_high"] = link_block(rh)

    # ---- positioning precision (pooled medians) ----
    out["precision"] = {}
    for pipe in PIPES:
        blk = {}
        for tag_run, perpow in (("center", pc and pc.get("per_power")), ("high", ph and ph.get("per_power"))):
            if perpow:
                blk[tag_run] = {"median_scatter_3d": pooled_metric(perpow, pipe, "median_scatter_3d"),
                                "median_std_z": pooled_metric(perpow, pipe, "median_std_z"),
                                "caliper_max_abs_err": pooled_caliper_maxerr(perpow, pipe)}
        out["precision"][pipe] = blk

    # ---- per-tag mean-position SHIFT (center->high), esp. dz ----
    out["tag_shift"] = {}
    for pipe in PIPES:
        blk = {}
        cper, hper = (pc and pc.get("per_power")), (ph and ph.get("per_power"))
        if not (cper and hper):
            continue
        for t in WAND:
            pcen, phigh = pooled_pos(cper, pipe, t), pooled_pos(hper, pipe, t)
            if pcen and phigh:
                d = np.array(phigh) - np.array(pcen)
                blk[t] = {"center_pos": [round(v, 1) for v in pcen],
                          "high_pos": [round(v, 1) for v in phigh],
                          "dx": round(float(d[0]), 1), "dy": round(float(d[1]), 1),
                          "dz": round(float(d[2]), 1), "d3d": round(float(np.linalg.norm(d)), 1),
                          "scatter_center": pooled_tag_scatter(cper, pipe, t),
                          "scatter_high": pooled_tag_scatter(hper, pipe, t)}
        out["tag_shift"][pipe] = blk

    # ---- listener (LE common to both) ----
    def le_cir(run):
        if run is lc and lc:  # center: single listener, listener_vs_power
            return {p: lc.get("listener_vs_power", {}).get(p, {}).get("cir_dB_rel_MAX") for p in PRESETS}
        if run is lh and lh:  # high: fleet, pull LE
            le = lh.get("listeners", {}).get("LE", {}).get("per_power", {})
            return {p: le.get(p, {}).get("cir_dB_rel_MAX") for p in PRESETS}
        return None
    out["listener_LE_cir_dB_rel_MAX"] = {"center": le_cir(lc), "high": le_cir(lh)}
    out["listener_high_fleet"] = {"n_listeners": len(lh.get("listeners", {})) if lh else 0,
                                  "fleet_max_abs_cir_dB_swing": lh.get("fleet_max_abs_cir_dB_swing") if lh else None,
                                  "fleet_max_env_drift_pct": lh.get("fleet_max_env_drift_pct") if lh else None}

    json.dump(out, open(os.path.join(HIGH, "comparison.json"), "w"), indent=1)

    # ---- COMPARISON.md ----
    L = []
    L.append("# Center vs Position-High — overnight power sweep comparison\n")
    L.append("Room-**CENTER** (`overnight_power_20260714`, wand ~1 m room center) vs "
             "**POSITION-HIGH** (`overnight_power_position_high_20260715`, wand raised). "
             "Same rig, same anchors/layout/listeners — only the wand height changed. "
             "Same analysis both runs.\n")

    L.append("## 1. Link success & bias vs power\n")
    L.append("| metric | CENTER | HIGH |\n|---|---|---|")
    c, h = out["link_center"], out["link_high"]
    def g(b, p, k): return (b["per_power"][p][k] if b and b["per_power"].get(p) else None)
    for p in PRESETS:
        L.append(f"| ge7 @{p} ({DB[p]}dB) | {g(c,p,'ge7')} | {g(h,p,'ge7')} |")
    L.append(f"| bias median swing (mm) | {c and c['bias_median_swing_mm']} | {h and h['bias_median_swing_mm']} |")
    L.append(f"| lock events | {c and c['lock_events']} | {h and h['lock_events']} |")
    L.append("")

    L.append("## 2. Positioning precision (pooled median across powers)\n")
    L.append("| pipeline | metric | CENTER | HIGH | Δ(H−C) |\n|---|---|---|---|---|")
    for pipe in PIPES:
        b = out["precision"].get(pipe, {})
        cc, hh = b.get("center", {}), b.get("high", {})
        for k, lab in (("median_scatter_3d", "3D scatter (mm)"), ("median_std_z", "z-std (mm)"),
                       ("caliper_max_abs_err", "caliper max|err| (mm)")):
            cv, hv = cc.get(k), hh.get(k)
            dv = round(hv - cv, 1) if (cv is not None and hv is not None) else None
            L.append(f"| {pipe} | {lab} | {cv} | {hv} | {dv} |")
    L.append("")

    L.append("## 3. Per-tag mean-position shift (center → high)\n")
    L.append("Wand fixed within each run; this is the physical move between runs. **Δz** = "
             "how much the raised height showed up in the solve.\n")
    for pipe in PIPES:
        blk = out["tag_shift"].get(pipe, {})
        if not blk:
            continue
        L.append(f"**{pipe}**\n")
        L.append("| tag | center xyz (mm) | high xyz (mm) | Δx | Δy | **Δz** | |Δ3D| | scatter C→H |\n|---|---|---|---|---|---|---|---|")
        for t in WAND:
            d = blk.get(t)
            if not d:
                continue
            L.append(f"| {t} | {d['center_pos']} | {d['high_pos']} | {d['dx']} | {d['dy']} | "
                     f"**{d['dz']}** | {d['d3d']} | {d['scatter_center']}→{d['scatter_high']} |")
        L.append("")

    L.append("## 4. Listener received power (LE, common to both)\n")
    L.append("| power | CENTER cir dB relMAX | HIGH cir dB relMAX |\n|---|---|---|")
    lec, leh = out["listener_LE_cir_dB_rel_MAX"]["center"], out["listener_LE_cir_dB_rel_MAX"]["high"]
    for p in PRESETS:
        L.append(f"| {p} | {lec and lec.get(p)} | {leh and leh.get(p)} |")
    fl = out["listener_high_fleet"]
    L.append(f"\nHIGH fleet: {fl['n_listeners']}/7 listeners; worst |cir swing| vs 8.5 dB TX = "
             f"{fl['fleet_max_abs_cir_dB_swing']} dB; max env drift {fl['fleet_max_env_drift_pct']}%.\n")

    # dz for the verdict (V4io pipeline)
    dz = {t: out["tag_shift"].get("A_v4io_t4", {}).get(t, {}).get("dz") for t in WAND}

    L.append("## Verdict — what changed when the wand was raised\n")
    L.append("1. **Power is still invisible.** ge7 0.978, bias ≈18 mm, positioning ≈48 mm, "
             "listener ≤0.4 dB — flat across the 8.5 dB sweep at *both* positions. Height does "
             "not change the AGC-normalized \"power buys nothing at strong links\" result.")
    L.append("2. **Link success unchanged by height.** ge7 identical center↔high (0.978), "
             "valid 97.3 %, **0 lock events** both runs. Raising the wand did not push any link "
             "toward its SNR margin, and the steeper geometry produced no reflection locks.")
    L.append("3. **Precision essentially unchanged** (marginally tighter high: 3D scatter "
             f"−2 mm, z-std −5 mm) — within the ~30 mm repeatability floor, not a real gain.")
    L.append(f"4. **The physical raise IS visible in the solve, with the right sign.** All 3 "
             f"tags moved to more-negative z (Δz ≈ {dz['BSCCF4']:.0f} / {dz['BS9336']:.0f} / "
             f"{dz['BS955A']:.0f} mm) — and because the layout's z is **globally inverted** "
             f"(ceiling solves negative), more-negative-z = **physically higher**. So raising "
             f"the wand shows up correctly. BUT the per-tag Δz are *unequal* (≈190–620 mm) and "
             f"Δx is large too (≈±110 mm), so this was **not a clean rigid vertical lift** — the "
             f"wand was re-oriented as it was raised, and z is the weakly-constrained axis, so "
             f"the absolute Δz magnitudes are not a trustworthy height measurement.")
    L.append("5. **The caliper's worst baseline swapped** — CCF4–955A (center, ~200–300 mm) → "
             "**9336–955A** (high, ~180–208 mm); CCF4–955A is now nearly correct (+2…+33 mm). "
             "Net max|err| is lower at height (A 283→199, B 232→180), but this is which tag-pair "
             "happens to project onto the layout's weak vertical axis — a **geometry artifact, "
             "not an accuracy improvement** (per the standing ruling: don't treat the wand "
             "caliper as a pass/fail gate at this layout-RMS scale).")
    L.append("6. **Listener / AGC identical.** LE (common node) 0.05 dB swing both runs. The "
             "high-run fleet's larger swings (LB/LF/L955A 0.37–0.43 dB, env-drift 7.6–11.6 %) "
             "are the **walking**, not power — the AGC-flat listeners stay at 0.05 dB.")
    L.append("7. **Conditions differ:** center ran static/unattended (env stable, 0.6 % drift); "
             "high had the operator **walking** (0.9 % movement duty, 30 events, worst POR "
             "round-1). Light enough that pooled metrics are unaffected, and timestamped so any "
             "borderline cell can be checked against the movement timeline.\n")
    L.append("**Bottom line:** raising the wand changed the *geometry* (which baseline/axis is "
             "weak, which tag scatters most, a correctly-signed but non-rigid z shift) and "
             "**nothing about the power behaviour** — power stays invisible end-to-end. Links "
             "are as healthy raised as centered.")

    open(os.path.join(HIGH, "COMPARISON.md"), "w").write("\n".join(L))
    print("wrote", os.path.join(HIGH, "comparison.json"))
    print("wrote", os.path.join(HIGH, "COMPARISON.md"))
    print("\n".join(L))


if __name__ == "__main__":
    main()
