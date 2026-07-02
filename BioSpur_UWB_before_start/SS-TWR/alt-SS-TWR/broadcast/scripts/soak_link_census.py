#!/usr/bin/env python3
"""Per-link static-environment background census + time-variation watch for the overnight soak.

Uses the ANCHOR self-read (recv tag_rf_diag.csv) for ALL tag->anchor links. Baseline = this room,
this layout, static furniture (chair/iron-rack at A etc.) INCLUDED — that IS the background. Per-link
z-scoring absorbs each link's zero-point; this census makes the zero-points explicit and flags:
  - naturally-suspicious links (high/wide ΔP) = candidate natural static-NLOS samples (furniture);
    marked proxy-ready if a co-located listener exists (B/E/F).
  - TIME-VARYING links (per-chunk distribution drift/jump) = something moved / non-static source ->
    that link's baseline is questionable.
"""
import sys, csv, math, glob, re, argparse
from collections import defaultdict
import numpy as np

ANCH = "ABCDEFGH"
TAG = {2: "BS9336", 3: "BS955A", 4: "BSCCF4"}
LISTENER_ANCHORS = {1, 4, 5}  # B, E, F have a co-located listener (proxy-ready)

def dP(cir, f1, f2, f3):
    s = f1*f1 + f2*f2 + f3*f3
    return 10*math.log10(cir*(2**17)/s) if (cir > 0 and s > 0) else None

def rxpow(cir, n):
    return 10*math.log10(cir*(2**17)/(n*n)) if (cir > 0 and n > 0) else None

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True, help="soak base dir")
    ap.add_argument("--drift-dp-db", type=float, default=3.0,
                    help="flag link time-varying if chunk-median ΔP range exceeds this (dB)")
    a = ap.parse_args()
    files = sorted(glob.glob(f"{a.base}/**/tag_rf_diag.csv", recursive=True))
    # dedupe: recv writes a combined top-level tag_rf_diag.csv AND per-tag subdir copies
    # (.../BS9336/tag_rf_diag.csv). Keep only the combined file per chunk to avoid double-counting.
    files = [f for f in files if not re.search(r"/BS[0-9A-Fa-f]+/tag_rf_diag\.csv$", f)]
    if not files:
        sys.exit(f"no tag_rf_diag.csv under {a.base}")
    # link -> chunk -> lists
    data = defaultdict(lambda: defaultdict(lambda: {"dp": [], "rx": []}))
    for fn in files:
        m = re.search(r"chunk(\d+)", fn)
        ck = int(m.group(1)) if m else 0
        try:
            rows = list(csv.DictReader(open(fn)))
        except Exception:
            continue
        for r in rows:
            if str(r.get("anchor_diag_valid", "0")) not in ("1", "True", "true"):
                continue
            try:
                t = int(r["tag_id"]); an = int(r["anchor_id"])
                cir = float(r["anchor_cir_pwr"]); n = float(r["anchor_rxpacc"])
                d = dP(cir, float(r["anchor_fp1"]), float(r["anchor_fp2"]), float(r["anchor_fp3"]))
                rp = rxpow(cir, n)
            except (KeyError, ValueError):
                continue
            if d is not None: data[(t, an)][ck]["dp"].append(d)
            if rp is not None: data[(t, an)][ck]["rx"].append(rp)

    def agg(vals):
        v = np.array(vals, float)
        return (float(np.median(v)), float(np.percentile(v, 75) - np.percentile(v, 25)), len(v))

    print(f"# Soak per-link background census  ({len(files)} tag_rf_diag files, {len(data)} links)\n")
    print(f"{'link':14s}{'n':>7s}{'rxpow_med':>10s}{'ΔP_med':>8s}{'ΔP_IQR':>8s}  {'chunks(ΔP med)':>22s}  flags")
    rows_out = []
    for (t, an), byck in sorted(data.items(), key=lambda kv: (kv[0][1], kv[0][0])):
        allc = [ck for ck in byck]
        dp_all = [x for ck in byck for x in byck[ck]["dp"]]
        rx_all = [x for ck in byck for x in byck[ck]["rx"]]
        if not dp_all: continue
        dpm, dpiqr, n = agg(dp_all)
        rxm, _, _ = agg(rx_all) if rx_all else (float("nan"), 0, 0)
        # per-chunk ΔP medians (time series)
        ck_meds = [float(np.median(byck[ck]["dp"])) for ck in sorted(byck) if byck[ck]["dp"]]
        drift = (max(ck_meds) - min(ck_meds)) if len(ck_meds) >= 2 else 0.0
        flags = []
        if dpm >= 8.0: flags.append("NLOS-bg")           # natural static NLOS candidate
        elif dpm >= 6.0: flags.append("lean-NLOS")
        if dpiqr >= 6.0: flags.append("wide")
        if drift >= a.drift_dp_db and len(ck_meds) >= 2: flags.append(f"TIME-VARY(Δ{drift:.1f})")
        if an in LISTENER_ANCHORS and dpm >= 6.0: flags.append("proxy-ready(L-%s)" % ANCH[an])
        ck_str = ",".join(f"{m:.1f}" for m in ck_meds[-6:])
        link = f"{TAG.get(t,t)}->{ANCH[an]}"
        print(f"{link:14s}{n:>7d}{rxm:>10.1f}{dpm:>8.1f}{dpiqr:>8.1f}  {ck_str:>22s}  {' '.join(flags)}")
        rows_out.append((link, dpm, drift, flags))
    tv = [r for r in rows_out if any("TIME-VARY" in f for f in r[3])]
    sus = [r for r in rows_out if any(f in ("NLOS-bg", "wide") for f in r[3])]
    print(f"\nSUSPICIOUS (natural static-NLOS / wide): {', '.join(r[0] for r in sus) or 'none'}")
    print(f"TIME-VARYING (something moved? baseline suspect): {', '.join(r[0] for r in tv) or 'none — env static, good'}")

if __name__ == "__main__":
    main()
