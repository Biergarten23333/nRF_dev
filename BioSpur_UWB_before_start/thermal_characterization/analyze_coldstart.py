#!/usr/bin/env python3
"""Cold-start thermal characterization — CFO analysis.

Reads the raw/ logs from a capture_coldstart.py run and quantifies how long
after power-on the DW1000 clock-frequency-offset (CFO) takes to reach thermal
equilibrium. CFO is the temperature proxy (the on-chip temp sensor is not
readable at runtime).

    cfo_ppm = rxtofs / ttcki * 1e6

rxtofs is already sign-extended to 32-bit signed by the firmware (printed %d);
ttcki is the receiver time-tracking interval (%lu). Both come straight off the
LPD/LRD lines the listeners already emit — no firmware change.

Per listener we lock onto a SINGLE TX source (the dominant one by frame count)
and use only that source's frames. rxtofs is the receiver's offset relative to
a given transmitter, so mixing transmitters would stack discrete per-source
clock steps on top of the thermal drift. Holding the source fixed isolates the
receiver's (plus that one transmitter's) warm-up transient, which is what the
pre-warm time needs.

Time axis: now_ms (device uptime, ms). Nodes cold-boot at t=0, so now_ms is
milliseconds-since-power-on directly. metadata.t0 is kept for record only.

Outputs (under <run_dir>/):
  figures/cfo_vs_time.png
  figures/cfo_drift_rate.png
  coldstart_summary.json
  coldstart_report.txt

Usage:
  analyze_coldstart.py <run_dir>
  analyze_coldstart.py --run-dir <run_dir>
"""
import os
import sys
import json
import argparse
from collections import Counter, defaultdict

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from scipy.stats import spearmanr

NODE_ORDER = ["LB", "LE", "LF", "LCCF4", "L9336", "L955A"]
COLORS = {
    "LB": "tab:blue", "LE": "tab:orange", "LF": "tab:green",
    "LCCF4": "tab:red", "L9336": "tab:purple", "L955A": "tab:brown",
}

BIN_S = 5.0                 # CFO curve bin width
DRIFT_WINDOW_S = 60.0       # sliding window for d(CFO)/dt
STABLE_THRESH_PPM_PER_MIN = 0.05     # overnight steady-state drift rate
STABLE_SUSTAIN_S = 5 * 60.0          # must hold below threshold this long
CFO_SANE_PPM = 60.0                  # reject |cfo| beyond this (PLL-lock garbage)


# ----------------------------------------------------------------------------- parse
def parse_log(path):
    """Parse one listener log. Returns dict src_hex -> (now_ms[], cfo_ppm[]).

    Robust to: leading no-data, truncated/garbled lines (LCCF4), CIR dump lines,
    and out-of-range CFO from PLL lock. Only LPD/LRD lines with valid rxtofs/
    ttcki are kept.
    """
    by_src_ms = defaultdict(list)
    by_src_cfo = defaultdict(list)
    if not os.path.exists(path):
        return {}, 0, 0
    n_lines = 0
    n_used = 0
    with open(path, "r", encoding="ascii", errors="ignore") as f:
        for line in f:
            n_lines += 1
            if not (line.startswith("LPD") or line.startswith("LRD")):
                continue
            p = line.rstrip("\n").split(";")
            if len(p) < 21:
                continue
            try:
                now_ms = int(p[4])
                src = p[8]
                rxtofs = None
                ttcki = None
                for t in p[20:]:
                    if t.startswith("rxtofs="):
                        rxtofs = int(t.split("=", 1)[1])
                    elif t.startswith("ttcki="):
                        ttcki = int(t.split("=", 1)[1])
                if rxtofs is None or ttcki is None or ttcki == 0:
                    continue
                if not (0 <= now_ms < 4_294_967_296):
                    continue
                cfo = rxtofs / ttcki * 1e6
                if not (-CFO_SANE_PPM < cfo < CFO_SANE_PPM):
                    continue
            except (ValueError, IndexError):
                continue
            by_src_ms[src].append(now_ms)
            by_src_cfo[src].append(cfo)
            n_used += 1
    out = {}
    for src in by_src_ms:
        out[src] = (np.asarray(by_src_ms[src], dtype=np.int64),
                    np.asarray(by_src_cfo[src], dtype=np.float64))
    return out, n_lines, n_used


def dominant_source(per_src):
    """Pick the TX source with the most frames (robust vs a stray first line)."""
    if not per_src:
        return None
    return max(per_src, key=lambda s: per_src[s][0].size)


# ----------------------------------------------------------------------------- binning
def bin_curve(now_ms, cfo, bin_s=BIN_S):
    """Median CFO per bin_s window. Returns (t_sec_centers, cfo_median), sorted."""
    if now_ms.size == 0:
        return np.array([]), np.array([])
    t_s = now_ms / 1000.0
    order = np.argsort(t_s)
    t_s = t_s[order]
    cfo = cfo[order]
    # anchor bins to power-on (t=0), not to the first sample, so all listeners
    # share a common grid for cross-correlation.
    idx = np.floor(t_s / bin_s).astype(np.int64)
    centers = []
    meds = []
    for b in np.unique(idx):
        m = idx == b
        centers.append((b + 0.5) * bin_s)
        meds.append(float(np.median(cfo[m])))
    return np.asarray(centers), np.asarray(meds)


def drift_rate(t_s, cfo, window_s=DRIFT_WINDOW_S):
    """|d(CFO)/dt| in ppm/min via a sliding-window linear fit centered on each
    binned point. Returns (t_s, drift_ppm_per_min). Points with <2 samples in
    their window get NaN."""
    n = t_s.size
    out = np.full(n, np.nan)
    if n < 2:
        return t_s, out
    half = window_s / 2.0
    for i in range(n):
        lo = t_s[i] - half
        hi = t_s[i] + half
        m = (t_s >= lo) & (t_s <= hi)
        if m.sum() < 2:
            continue
        tt = t_s[m]
        cc = cfo[m]
        if tt.max() - tt.min() < 1e-6:
            continue
        slope = np.polyfit(tt, cc, 1)[0]        # ppm per second
        out[i] = abs(slope) * 60.0              # ppm per minute
    return t_s, out


def find_t_stable(t_s, drift, thresh=STABLE_THRESH_PPM_PER_MIN,
                  sustain_s=STABLE_SUSTAIN_S):
    """Earliest t where drift stays below `thresh` for at least `sustain_s`.
    Returns t_stable in seconds, or None if never sustained-stable."""
    n = t_s.size
    if n == 0:
        return None
    valid = ~np.isnan(drift)
    for i in range(n):
        if not valid[i] or drift[i] >= thresh:
            continue
        # from i, require every valid point up to t_s[i]+sustain_s to be below
        end = t_s[i] + sustain_s
        ok = True
        reached_end = False
        for j in range(i, n):
            if t_s[j] > end:
                reached_end = True
                break
            if valid[j] and drift[j] >= thresh:
                ok = False
                break
        # accept only if the sustain window is actually covered by data
        if ok and (reached_end or (t_s[-1] - t_s[i] >= sustain_s)):
            return float(t_s[i])
    return None


# ----------------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description="Analyze cold-start CFO warm-up.")
    ap.add_argument("run_dir", nargs="?", default=None)
    ap.add_argument("--run-dir", dest="run_dir_opt", default=None)
    args = ap.parse_args()
    run_dir = args.run_dir or args.run_dir_opt
    if not run_dir:
        ap.error("run_dir is required (positional or --run-dir)")
    run_dir = os.path.abspath(run_dir)
    raw_dir = os.path.join(run_dir, "raw")
    fig_dir = os.path.join(run_dir, "figures")
    os.makedirs(fig_dir, exist_ok=True)

    meta = {}
    mpath = os.path.join(run_dir, "metadata.json")
    if os.path.exists(mpath):
        try:
            meta = json.load(open(mpath))
        except Exception:
            meta = {}

    # --- parse + per-listener CFO curve ---
    curves = {}          # name -> (t_s, cfo_med)
    drifts = {}          # name -> (t_s, drift_ppm_per_min)
    per_listener = {}    # name -> summary dict
    print("=" * 72)
    print("COLD-START CFO ANALYSIS")
    print(f"run: {run_dir}")
    if meta.get("t0_iso"):
        print(f"t0 : {meta['t0_iso']}  (duration {meta.get('duration_min','?')} min)")
    print("=" * 72)

    for name in NODE_ORDER:
        per_src, n_lines, n_used = parse_log(os.path.join(raw_dir, f"{name}.log"))
        src = dominant_source(per_src)
        if src is None:
            print(f"  {name:<7} no usable frames ({n_lines} lines)")
            per_listener[name] = {"name": name, "status": "no_data",
                                  "n_lines": n_lines, "n_frames": 0}
            continue
        now_ms, cfo = per_src[src]
        min_uptime_s = float(now_ms.min()) / 1000.0
        t_s, cfo_med = bin_curve(now_ms, cfo)
        td_s, drift = drift_rate(t_s, cfo_med)
        curves[name] = (t_s, cfo_med)
        drifts[name] = (td_s, drift)

        t_stable = find_t_stable(td_s, drift)
        # CFO excursion: first bin -> t_stable (or final bin if never stable)
        cfo_start = float(cfo_med[0])
        if t_stable is not None:
            k = int(np.argmin(np.abs(t_s - t_stable)))
            cfo_at_stable = float(cfo_med[k])
        else:
            cfo_at_stable = float(cfo_med[-1])
        excursion = cfo_at_stable - cfo_start
        cfo_final = float(np.median(cfo_med[-max(1, len(cfo_med) // 20):]))

        d = {
            "name": name, "status": "ok", "src": src,
            "n_frames": int(now_ms.size),
            "n_srcs_seen": len(per_src),
            "min_uptime_s": round(min_uptime_s, 1),
            "t_span_min": round(float(now_ms.max() - now_ms.min()) / 60000.0, 2),
            "t_stable_min": round(t_stable / 60.0, 2) if t_stable is not None else None,
            "cfo_start_ppm": round(cfo_start, 3),
            "cfo_excursion_ppm": round(excursion, 3),
            "cfo_final_ppm": round(cfo_final, 3),
        }
        per_listener[name] = d
        warn = "" if min_uptime_s < 10 else f"  [WARN first frame at uptime {min_uptime_s:.0f}s]"
        ts_txt = f"{d['t_stable_min']:.1f} min" if d["t_stable_min"] is not None else "NOT REACHED"
        print(f"  {name:<7} src={src:<8} n={d['n_frames']:>7}  "
              f"t_stable={ts_txt:<12} excursion={excursion:+.2f}ppm  "
              f"final={cfo_final:+.2f}ppm{warn}")

    # --- pre-warm recommendation ---
    t_stables = [per_listener[n]["t_stable_min"] for n in NODE_ORDER
                 if per_listener.get(n, {}).get("t_stable_min") is not None]
    never = [n for n in NODE_ORDER
             if per_listener.get(n, {}).get("status") == "ok"
             and per_listener[n]["t_stable_min"] is None]
    if t_stables:
        pre_warm = int(np.ceil(max(t_stables) / 5.0) * 5)
    else:
        pre_warm = None

    # --- Figure 1: CFO vs time ---
    fig, ax = plt.subplots(figsize=(12, 6))
    for name in NODE_ORDER:
        if name not in curves:
            continue
        t_s, cfo_med = curves[name]
        ax.plot(t_s / 60.0, cfo_med, color=COLORS[name], lw=1.2, label=name)
        d = per_listener[name]
        if d.get("t_stable_min") is not None:
            ax.axvline(d["t_stable_min"], color=COLORS[name], lw=0.8, ls=":", alpha=0.6)
    if pre_warm is not None:
        ax.axvline(pre_warm, color="k", lw=1.4, ls="--",
                   label=f"pre-warm = {pre_warm} min")
    ax.set_xlabel("minutes since power-on")
    ax.set_ylabel("CFO (ppm)")
    ax.set_title("Cold-start CFO warm-up (dotted = per-listener t_stable, "
                 "dashed = recommended pre-warm)")
    ax.legend(loc="best", fontsize=8, ncol=2)
    ax.grid(alpha=0.3)
    fig.tight_layout()
    f1 = os.path.join(fig_dir, "cfo_vs_time.png")
    fig.savefig(f1, dpi=150)
    plt.close(fig)

    # --- Figure 2: drift rate ---
    fig, ax = plt.subplots(figsize=(12, 6))
    for name in NODE_ORDER:
        if name not in drifts:
            continue
        td_s, drift = drifts[name]
        ax.plot(td_s / 60.0, drift, color=COLORS[name], lw=1.2, label=name)
    ax.axhline(STABLE_THRESH_PPM_PER_MIN, color="k", lw=1.2, ls="--",
               label=f"stable threshold = {STABLE_THRESH_PPM_PER_MIN} ppm/min")
    ax.set_xlabel("minutes since power-on")
    ax.set_ylabel("|d(CFO)/dt|  (ppm/min)")
    ax.set_yscale("log")
    ax.set_ylim(1e-3, 10)
    ax.set_title("Cold-start CFO drift rate (60 s sliding window)")
    ax.legend(loc="best", fontsize=8, ncol=2)
    ax.grid(alpha=0.3, which="both")
    fig.tight_layout()
    f2 = os.path.join(fig_dir, "cfo_drift_rate.png")
    fig.savefig(f2, dpi=150)
    plt.close(fig)

    # --- cross-listener consistency (Spearman on common time grid) ---
    active = [n for n in NODE_ORDER if n in curves]
    corr = {}
    if len(active) >= 2:
        # resample each listener's curve onto a shared bin grid via nearest bin
        grids = {}
        for name in active:
            t_s, cfo_med = curves[name]
            grids[name] = dict(zip(np.round(t_s / BIN_S).astype(int), cfo_med))
        for i in range(len(active)):
            for j in range(i + 1, len(active)):
                a, b = active[i], active[j]
                common = sorted(set(grids[a]) & set(grids[b]))
                if len(common) >= 5:
                    va = [grids[a][k] for k in common]
                    vb = [grids[b][k] for k in common]
                    rho, _ = spearmanr(va, vb)
                    corr[f"{a}-{b}"] = round(float(rho), 3)
                else:
                    corr[f"{a}-{b}"] = None
    corr_vals = [v for v in corr.values() if v is not None]

    # --- write summary JSON ---
    summary = {
        "run_dir": run_dir,
        "t0_iso": meta.get("t0_iso"),
        "duration_min": meta.get("duration_min"),
        "per_listener": {n: per_listener[n] for n in NODE_ORDER if n in per_listener},
        "cross_listener_spearman": corr,
        "cross_listener_spearman_summary": {
            "median": round(float(np.median(corr_vals)), 3) if corr_vals else None,
            "min": round(float(np.min(corr_vals)), 3) if corr_vals else None,
            "n_pairs": len(corr_vals),
        },
        "recommendation": {
            "pre_warm_minutes": pre_warm,
            "threshold_ppm_per_min": STABLE_THRESH_PPM_PER_MIN,
            "sustain_min": STABLE_SUSTAIN_S / 60.0,
            "listeners_never_stable": never,
            "note": "derived from overnight steady-state (0.05 ppm/min); "
                    "pre_warm = max(t_stable) rounded up to next 5 min",
        },
        "figures": [f1, f2],
    }
    with open(os.path.join(run_dir, "coldstart_summary.json"), "w") as f:
        json.dump(summary, f, indent=2)

    # --- human-readable report ---
    lines = []
    lines.append("=" * 72)
    lines.append("COLD-START THERMAL CHARACTERIZATION — CFO REPORT")
    lines.append("=" * 72)
    lines.append(f"run dir     : {run_dir}")
    lines.append(f"t0          : {meta.get('t0_iso', '?')}")
    lines.append(f"duration    : {meta.get('duration_min', '?')} min")
    lines.append(f"proxy       : cfo_ppm = rxtofs / ttcki * 1e6  (per-listener "
                 f"dominant TX source)")
    lines.append("")
    lines.append(f"{'listener':<9}{'src':<9}{'frames':>8}  {'t_stable':>10}"
                 f"  {'excursion':>11}  {'final':>9}")
    lines.append("-" * 72)
    for name in NODE_ORDER:
        d = per_listener.get(name, {})
        if d.get("status") != "ok":
            lines.append(f"{name:<9}{'-':<9}{'0':>8}  {'NO DATA':>10}")
            continue
        ts = f"{d['t_stable_min']:.1f} min" if d["t_stable_min"] is not None else "NOT REACHED"
        lines.append(f"{name:<9}{d['src']:<9}{d['n_frames']:>8}  {ts:>10}"
                     f"  {d['cfo_excursion_ppm']:>+10.2f}p  {d['cfo_final_ppm']:>+8.2f}p")
    lines.append("-" * 72)
    lines.append("")
    if pre_warm is not None:
        lines.append(f">> RECOMMENDED PRE-WARM: {pre_warm} minutes")
        lines.append(f"   (= max t_stable {max(t_stables):.1f} min, rounded up to next 5 min;")
        lines.append(f"    threshold {STABLE_THRESH_PPM_PER_MIN} ppm/min sustained "
                     f"{STABLE_SUSTAIN_S/60:.0f} min)")
    else:
        lines.append(">> RECOMMENDED PRE-WARM: UNDETERMINED (no listener reached "
                     "sustained stability)")
    if never:
        lines.append(f"   WARNING: never-stable within capture: {', '.join(never)} "
                     f"(extend duration or inspect)")
    lines.append("")
    lines.append("Cross-listener consistency (Spearman rho of CFO curves):")
    if corr_vals:
        lines.append(f"   median rho = {np.median(corr_vals):+.3f}   "
                     f"min rho = {np.min(corr_vals):+.3f}   "
                     f"pairs = {len(corr_vals)}")
        for k, v in corr.items():
            lines.append(f"     {k:<16} {('%.3f' % v) if v is not None else 'n/a'}")
    else:
        lines.append("   (insufficient overlap to correlate)")
    lines.append("")
    lines.append("Figures:")
    lines.append(f"   {f1}")
    lines.append(f"   {f2}")
    lines.append("=" * 72)
    report = "\n".join(lines)
    with open(os.path.join(run_dir, "coldstart_report.txt"), "w") as f:
        f.write(report + "\n")

    print("")
    print(report)
    print("")
    print(f"[wrote] {os.path.join(run_dir, 'coldstart_summary.json')}")
    print(f"[wrote] {os.path.join(run_dir, 'coldstart_report.txt')}")
    print(f"[wrote] {f1}")
    print(f"[wrote] {f2}")


if __name__ == "__main__":
    main()
