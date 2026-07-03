#!/usr/bin/env python3
"""L-B up-link CIR mechanism discriminators, baseline-group vs test-group.

Compares FP amplitude / tail energy / rxpow for a tag's up-link between two chunk sets, to classify a
change as:  reflection (tail RISES, total ~flat)  vs  absorption/intrusion (FP + rxpow DROP)  vs
none (all flat).  This is the co-located CIR ARBITER for anchor-side ΔP moves — a change that is real
in the waveform shows here; a phase-beat/register artifact does not.

Default: BS955A(tag 3), baseline=chunks 1-5, test=chunks 7-8; other tags printed as controls.
Used 2026-07-03 to falsify the "BS955A->B 03:39 reflection" hypothesis (L-B waveform was flat).
"""
import os, sys, glob, math, re, argparse
HERE = os.path.dirname(os.path.abspath(__file__))
BROADCAST = os.path.dirname(HERE)
sys.path.insert(0, HERE)
from cir_notch_detector import iter_captures, decode_mag
import numpy as np

TAGNAME = {2: "BS9336", 3: "BS955A", 4: "BSCCF4"}

def stat(v):
    a = np.array(v, float)
    return (a.mean(), a.std(), len(a)) if len(a) else (float("nan"), 0.0, 0)

def parse_set(s):
    out = set()
    for part in s.split(","):
        if "-" in part:
            lo, hi = part.split("-"); out |= set(range(int(lo), int(hi) + 1))
        else:
            out.add(int(part))
    return out

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=None, help="soak base dir (default: latest overnight_soak_*)")
    ap.add_argument("--focus-tag", type=int, default=3)
    ap.add_argument("--baseline-chunks", default="1-5")
    ap.add_argument("--test-chunks", default="7-8")
    a = ap.parse_args()
    base = a.base or sorted(glob.glob(f"{BROADCAST}/logs/overnight_soak_*"))[-1]
    G = {f"base({a.baseline_chunks})": parse_set(a.baseline_chunks),
         f"test({a.test_chunks})": parse_set(a.test_chunks)}
    def group_of(ck):
        for g, s in G.items():
            if ck in s: return g
        return None
    rows = {g: {t: [] for t in TAGNAME} for g in G}
    for fn in sorted(glob.glob(f"{base}/chunk*/LB/*/raw.log")):
        m = re.search(r"chunk(\d+)", fn); ck = int(m.group(1)) if m else 0
        g = group_of(ck)
        if not g: continue
        with open(fn, errors="replace") as fh:
            for tag, hdr, data in iter_captures(fh):
                if tag not in TAGNAME: continue
                mags = decode_mag(data)
                if not mags: continue
                peak = max(mags); pi = mags.index(peak)
                fp_i = max(0, min(len(mags) - 1, int(round(hdr["firstPath"] / 64.0))))
                fp = mags[fp_i]; rp = hdr["rxpacc"] or 1
                lo, hi = pi + 8, min(len(mags), pi + 40); tail = mags[lo:hi]
                if fp <= 0 or peak <= 0 or not tail or hdr["maxGrowth"] <= 0: continue
                trms = math.sqrt(sum(v * v for v in tail) / len(tail))
                rxpow = 10 * math.log10(hdr["maxGrowth"] * (2 ** 17) / (rp * rp))
                rows[g][tag].append(dict(fp=fp, peak=peak, trms=trms, rxpow=rxpow))
    gb, gt = list(G)
    print(f"# L-B CIR mechanism discriminators  base={os.path.basename(base)}")
    print(f"{'group':16s}{'tag':8s}{'n':>7s}{'FPamp':>9s}{'tail_rms':>10s}{'rxpow':>9s}")
    for g in G:
        for t in TAGNAME:
            r = rows[g][t]
            if not r: continue
            fpm = stat([x['fp'] for x in r]); tlm = stat([x['trms'] for x in r]); rxm = stat([x['rxpow'] for x in r])
            print(f"{g:16s}{TAGNAME[t]:8s}{fpm[2]:>7d}{fpm[0]:>9.0f}{tlm[0]:>10.0f}{rxm[0]:>9.2f}")
    t = a.focus_tag
    b, s = rows[gb][t], rows[gt][t]
    if b and s:
        bfp = stat([x['fp'] for x in b])[0]; btl = stat([x['trms'] for x in b])[0]
        dfp = stat([x['fp'] for x in s])[0] - bfp
        dtl = stat([x['trms'] for x in s])[0] - btl
        drx = stat([x['rxpow'] for x in s])[0] - stat([x['rxpow'] for x in b])[0]
        print(f"\n{TAGNAME[t]}  {gt} minus {gb}:")
        print(f"  ΔFPamp   = {dfp:+.0f} ({100*dfp/bfp:+.1f}%)  [flat=>reflection/none, drop=>intrusion]")
        print(f"  Δtail_rms= {dtl:+.0f} ({100*dtl/btl:+.1f}%)  [up=>reflection energizes tail]")
        print(f"  Δrxpow   = {drx:+.2f} dB            [drop=>absorption]")
        verdict = ("REFLECTION (tail up, total flat)" if dtl / btl > 0.05 and abs(drx) < 0.5
                   else "ABSORPTION/INTRUSION (FP/rxpow down)" if drx < -0.5 or dfp / bfp < -0.05
                   else "NO WAVEFORM CHANGE (flat) -> anchor-side ΔP move is NOT physical")
        print(f"  => L-B ARBITER: {verdict}")

if __name__ == "__main__":
    main()
