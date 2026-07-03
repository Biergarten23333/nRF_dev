#!/usr/bin/env python3
"""Decompose anchor-side ΔP for one tag->anchor link into rxpow / cir_pwr / first-path parts, per chunk.

ΔP = 10log10(cir_pwr*2^17 / (fp1^2+fp2^2+fp3^2)).  This splits a ΔP change into:
  - rxpow / 10log10(cir_pwr) moves  -> total-power change (absorption if down, reflection adds if up)
  - 10log10(fpterm) / fp1 moves     -> first-path change (direct-path diffraction OR TDMA phase-beat
                                       register artifact: fp1 drops while total stays flat)
If total is flat and only fpterm drops, the ΔP rise is a first-path-ratio effect (NOT absorption/
reflection) -> cross-check with the co-located CIR probe (cir_mech_discriminators.py) before attributing.

Default anchor = B (id 1). Used 2026-07-03: BS955A->B chunk7/8 showed fp1 -9% / total flat = artifact.
"""
import os, sys, glob, csv, re, math, argparse
import numpy as np
HERE = os.path.dirname(os.path.abspath(__file__)); BROADCAST = os.path.dirname(HERE)
ANCH = "ABCDEFGH"

def med(v):
    return float(np.median(v)) if len(v) else float("nan")

def parse_set(s):
    out = set()
    for p in s.split(","):
        if "-" in p:
            lo, hi = p.split("-"); out |= set(range(int(lo), int(hi) + 1))
        else:
            out.add(int(p))
    return out

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=None)
    ap.add_argument("--tag", type=int, required=True, help="tag_id (2=BS9336,3=BS955A,4=BSCCF4)")
    ap.add_argument("--anchor", type=int, default=1, help="anchor_id 0=A..7=H (default 1=B)")
    ap.add_argument("--baseline-chunks", default="1-5")
    a = ap.parse_args()
    base = a.base or sorted(glob.glob(f"{BROADCAST}/logs/overnight_soak_*"))[-1]
    bset = parse_set(a.baseline_chunks)
    per = {}
    for fn in sorted(glob.glob(f"{base}/chunk*/**/tag_rf_diag.csv", recursive=True)):
        if re.search(r"/BS[0-9A-Fa-f]+/tag_rf_diag\.csv$", fn): continue
        m = re.search(r"chunk(\d+)", fn); ck = int(m.group(1)) if m else 0
        for r in csv.DictReader(open(fn)):
            if str(r.get("anchor_diag_valid", "0")) not in ("1", "True", "true"): continue
            try:
                if int(r["tag_id"]) != a.tag or int(r["anchor_id"]) != a.anchor: continue
                cir = float(r["anchor_cir_pwr"]); n = float(r["anchor_rxpacc"])
                f1 = float(r["anchor_fp1"]); f2 = float(r["anchor_fp2"]); f3 = float(r["anchor_fp3"])
            except (KeyError, ValueError):
                continue
            s = f1 * f1 + f2 * f2 + f3 * f3
            if cir <= 0 or n <= 0 or s <= 0: continue
            d = per.setdefault(ck, {"dp": [], "rx": [], "fpt": [], "cir": [], "f1": []})
            d["dp"].append(10 * math.log10(cir * (2 ** 17) / s))
            d["rx"].append(10 * math.log10(cir * (2 ** 17) / (n * n)))
            d["fpt"].append(10 * math.log10(s)); d["cir"].append(10 * math.log10(cir)); d["f1"].append(f1)
    link = f"tag{a.tag}->{ANCH[a.anchor]}"
    print(f"# Anchor ΔP decomposition  {link}  base={os.path.basename(base)}")
    print(f"{'ck':>3s}{'n':>7s}{'ΔP':>7s}{'rxpow':>8s}{'10log(cir)':>11s}{'10log(fpterm)':>14s}{'fp1':>8s}")
    B = {}
    for ck in sorted(per):
        d = per[ck]
        vals = dict(dp=med(d["dp"]), rx=med(d["rx"]), cir=med(d["cir"]), fpt=med(d["fpt"]), f1=med(d["f1"]))
        if ck in bset:
            for k, v in vals.items(): B.setdefault(k, []).append(v)
        print(f"{ck:>3d}{len(d['dp']):>7d}{vals['dp']:>7.2f}{vals['rx']:>8.2f}{vals['cir']:>11.2f}{vals['fpt']:>14.2f}{vals['f1']:>8.0f}")
    if not B:
        print("(no baseline chunks matched)"); return
    b = {k: med(v) for k, v in B.items()}
    print(f"\nbaseline({a.baseline_chunks}): ΔP={b['dp']:.2f} rxpow={b['rx']:.2f} 10log(cir)={b['cir']:.2f} "
          f"10log(fpterm)={b['fpt']:.2f} fp1={b['f1']:.0f}")
    for ck in sorted(per):
        if ck in bset: continue
        d = per[ck]
        print(f"chunk{ck}-base: ΔΔP={med(d['dp'])-b['dp']:+.2f}  Δrxpow={med(d['rx'])-b['rx']:+.2f}  "
              f"Δ10log(cir)={med(d['cir'])-b['cir']:+.2f}  Δ10log(fpterm)={med(d['fpt'])-b['fpt']:+.2f}  "
              f"Δfp1={med(d['f1'])-b['f1']:+.0f}")

if __name__ == "__main__":
    main()
