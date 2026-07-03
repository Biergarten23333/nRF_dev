#!/usr/bin/env python3
"""Time-resolved anchor-side ΔP + fp1 per tag->anchor link, wall-clock bins (host_epoch_s).

Reveals the temporal SIGNATURE of a ΔP move:
  - two-state toggle + MUTUAL EXCLUSION between tags -> TDMA/BLE-UWB phase-beat (one sacrificed tag
    at a time; its fp1 drops, total flat). A physical occluder cannot anti-phase two tags.
  - monotone drift                                    -> thermal / AGC / antenna drift
  - one sharp sustained step at a wall-clock time      -> candidate discrete physical/bystander event

Default anchor = B (id 1). Used 2026-07-03: BS955A->B & BSCCF4->B toggled ΔP 2.4<->6.5 mutually
exclusive all night, BS9336->B flat -> both census "events" classified as phase-beat, env static.
"""
import os, sys, glob, csv, re, math, time, argparse
import numpy as np
HERE = os.path.dirname(os.path.abspath(__file__)); BROADCAST = os.path.dirname(HERE)
ANCH = "ABCDEFGH"; TAG = {2: "BS9336", 3: "BS955A", 4: "BSCCF4"}

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=None)
    ap.add_argument("--anchor", type=int, default=1, help="anchor_id 0=A..7=H (default 1=B)")
    ap.add_argument("--bin-s", type=int, default=300)
    ap.add_argument("--elevated-dp", type=float, default=5.5, help="mark bins with ΔP>=this")
    a = ap.parse_args()
    base = a.base or sorted(glob.glob(f"{BROADCAST}/logs/overnight_soak_*"))[-1]
    D = {t: {} for t in TAG}
    for fn in sorted(glob.glob(f"{base}/chunk*/**/tag_rf_diag.csv", recursive=True)):
        if re.search(r"/BS[0-9A-Fa-f]+/tag_rf_diag\.csv$", fn): continue
        for r in csv.DictReader(open(fn)):
            if str(r.get("anchor_diag_valid", "0")) not in ("1", "True", "true"): continue
            try:
                t = int(r["tag_id"]); an = int(r["anchor_id"])
                if an != a.anchor or t not in TAG: continue
                te = float(r["host_epoch_s"]); cir = float(r["anchor_cir_pwr"])
                f1 = float(r["anchor_fp1"]); f2 = float(r["anchor_fp2"]); f3 = float(r["anchor_fp3"])
            except (KeyError, ValueError):
                continue
            s = f1 * f1 + f2 * f2 + f3 * f3
            if cir <= 0 or s <= 0: continue
            D[t].setdefault(int(te // a.bin_s), []).append((10 * math.log10(cir * (2 ** 17) / s), f1))
    allb = sorted(set(b for t in D for b in D[t]))
    tags = [t for t in (3, 2, 4) if t in D and D[t]]
    print(f"# Anchor ΔP/fp1 time-resolved  anchor={ANCH[a.anchor]}  bin={a.bin_s}s  base={os.path.basename(base)}")
    print(f"{'time':>6s} | " + " | ".join(f"{TAG[t]+' ΔP/fp1':>16s}" for t in tags))
    for b in allb:
        hhmm = time.strftime("%H:%M", time.localtime(b * a.bin_s)); cells = []
        for t in tags:
            v = D[t].get(b)
            cells.append(f"{np.median([x[0] for x in v]):5.1f}/{np.median([x[1] for x in v]):5.0f}"
                         if v else f"{'--':>11s}")
        elev = [TAG[t] for t in tags if D[t].get(b) and np.median([x[0] for x in D[t][b]]) >= a.elevated_dp]
        tail = ("   elevated: " + ",".join(elev)) if elev else ""
        print(f"{hhmm:>6s} | " + " | ".join(f"{c:>16s}" for c in cells) + tail)

if __name__ == "__main__":
    main()
