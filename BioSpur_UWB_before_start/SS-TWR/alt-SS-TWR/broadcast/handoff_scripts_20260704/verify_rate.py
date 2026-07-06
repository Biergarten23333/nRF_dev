#!/usr/bin/env python3
"""VERIFICATION (ii): per-link CIR frame rate on the single-tag dry run.
Settles the 0.27-vs-1.3 Hz per-link question: with ONE tag polled, does each full-CIR listener
reach the readout ceiling (~1.5-1.7 Hz), or does per-link stay sub-1 Hz?

Per full-CIR listener (link = listener x the single tag):
  - OFFERED poll rate  = lpd.csv rows/s  (every poll the listener decoded)
  - CAPTURED CIR rate  = lcirm.csv rows/s (one row per full-CIR dump)
  - inter-frame interval mean/median/p95 (needed for sub-Nyquist spectral design)
  - whether the CIR-readout ceiling BINDS (captured << offered).
Empty-room only (excludes the walk window from chunk_manifest.json if present).

Verdict: per-link >= 1.0-1.3 Hz -> respiration-band sampling fixable at the SCHEDULING layer (no
firmware change); still < 1 Hz -> windowed-readout firmware (16-32 taps vs 1016) is the prerequisite.

Usage: python3 verify_rate.py <session_dir>   (works on continuous OR old per-chunk layouts)
Reuses tag_roster.py for tag identity (single source of truth).
"""
import csv, glob, os, sys, json, numpy as np
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'scripts'))
from tag_roster import load_roster

SESS = sys.argv[1] if len(sys.argv) > 1 else 'logs/roto_sar_overnight_20260705_012548'
LISTENERS = ['LCCF4', 'L9336', 'L955A', 'LB', 'LE', 'LF']
CEIL_LO, CEIL_HI = 1.5, 1.7   # expected CIR-readout ceiling (Hz)

roster = load_roster(SESS); id2name = roster.get('by_id', {})
# walk window to exclude (empty-room only)
walk = (None, None)
mpath = os.path.join(SESS, 'chunk_manifest.json')
if os.path.exists(mpath):
    m = json.load(open(mpath))
    walk = (m.get('walk_start_epoch'), m.get('walk_stop_epoch'))

def col_times(path, tcol='host_epoch_s', tagcol='tag_id'):
    """return {name: sorted np.array of epochs} for rows of a listener csv, empty-room only."""
    out = {}
    if not os.path.exists(path):
        return out
    for r in csv.DictReader(open(path)):
        try:
            t = float(r[tcol]); tid = r.get(tagcol, '?')
        except (ValueError, KeyError):
            continue
        if walk[0] and walk[0] <= t <= walk[1]:   # drop the walk window
            continue
        out.setdefault(id2name.get(tid, f'tag{tid}'), []).append(t)
    return {k: np.sort(np.array(v)) for k, v in out.items()}

def rate_stats(ts):
    if len(ts) < 5:
        return None
    dt = np.diff(ts); dt = dt[(dt > 0) & (dt < 30)]
    if len(dt) < 3:
        return None
    return dict(n=len(ts), rate=1.0/np.median(dt), mean=dt.mean(), median=np.median(dt),
                p95=np.percentile(dt, 95))

def find(listener, fname):
    for p in [f'{SESS}/{listener}/listener_*/{fname}', f'{SESS}/chunk*/{listener}/listener_*/{fname}']:
        g = sorted(glob.glob(p))
        if g:
            return g
    return []

print("="*90)
print(f"CIR FRAME-RATE VERIFICATION  session={SESS}")
print(f"tag roster: {id2name}   walk-window excluded: {walk if walk[0] else 'none'}")
print("="*90)
print(f"{'listener':9} {'link(tag)':9} {'offered_Hz':>10} {'captured_Hz':>11} {'ceilingBinds':>12} "
      f"{'dt_mean':>8} {'dt_med':>7} {'dt_p95':>7} {'Nframes':>8}")
per_link = []
for L in LISTENERS:
    # captured = lcirm (full CIR); offered = lpd (all polls decoded)
    cap = {}; off = {}
    for f in find(L, 'lcirm.csv'):
        for nm, ts in col_times(f).items():
            cap.setdefault(nm, []).extend(ts.tolist())
    for f in find(L, 'lpd.csv'):
        for nm, ts in col_times(f).items():
            off.setdefault(nm, []).extend(ts.tolist())
    names = sorted(set(cap) | set(off))
    if not names:
        print(f"{L:9} (no CIR data)")
        continue
    for nm in names:
        cs = rate_stats(np.sort(np.array(cap.get(nm, [])))) if nm in cap else None
        os_ = rate_stats(np.sort(np.array(off.get(nm, [])))) if nm in off else None
        if cs is None:
            continue
        offered = os_['rate'] if os_ else float('nan')
        binds = 'YES' if (os_ and cs['rate'] < 0.8*offered) else 'no'
        per_link.append((L, nm, cs['rate']))
        print(f"{L:9} {nm:9} {offered:10.2f} {cs['rate']:11.2f} {binds:>12} "
              f"{cs['mean']:8.2f} {cs['median']:7.2f} {cs['p95']:7.2f} {cs['n']:8d}")

# ---- verdict ----
print("\n"+"="*90+"\nVERDICT (rate)\n"+"="*90)
if not per_link:
    print("  no per-link rates computed"); sys.exit(0)
rates = [r for _, _, r in per_link]
med = float(np.median(rates)); lo = float(np.min(rates))
print(f"  per-link captured CIR rate: median={med:.2f} Hz  min={lo:.2f} Hz  (over {len(rates)} links)")
print(f"  CIR-readout ceiling assumed ~{CEIL_LO}-{CEIL_HI} Hz; single-tag exclusive should approach it.")
if med >= 1.0:
    print(f"  => per-link >= 1.0 Hz ({med:.2f}) -> respiration-band sampling is FIXABLE AT THE SCHEDULING")
    print("     LAYER (fewer tags / faster poll), NO firmware change. Ceiling, not tag-sharing, is the")
    print("     residual limit; windowed readout only needed to push materially past ~1.7 Hz.")
else:
    print(f"  => per-link < 1.0 Hz ({med:.2f}) EVEN single-tag -> the 1016-tap CIR readout itself is the")
    print("     bottleneck. WINDOWED-READOUT FIRMWARE (16-32 taps around a target delay) is the")
    print("     PREREQUISITE for any respiration work.")
print("  (On a MULTI-tag session this per-link rate is aggregate/ntags and is NOT the dry-run answer.)")
