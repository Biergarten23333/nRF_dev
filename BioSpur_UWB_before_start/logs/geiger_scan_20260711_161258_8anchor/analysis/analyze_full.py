#!/usr/bin/env python3
"""Comprehensive analysis of a Geiger MODE_SCAN capture.
Outputs many figures + findings_full.txt, all in this analysis/ dir.

Sections:
  1  CIR waterfall  -- ALL 8 anchors
  2  Mean CIR channel signature per anchor (peak-aligned)
  3  First-path tap + peak magnitude per anchor vs time
  4  Per-anchor range residual from the trilaterated position (bias/NLOS)
  5  RMS delay spread (multipath richness) per anchor
  6  Position-solve quality: #anchors, solve residual, speed vs time
  7  Diagnostic fields (rxtofs/rcph/ttcki/agc) of the CIR-target vs time
  8  Wand loop: circle fit + bearing coverage
  9  CIR-derived range vs SS-TWR range cross-check per anchor
"""
import json, re, os
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from scipy.optimize import least_squares

HERE = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(os.path.dirname(HERE), "scan.log")
TAP_NS = 1.0016            # DW1000 accumulator sample period
TAP_M = 299792458 * TAP_NS * 1e-9   # ~0.3003 m per tap
NTAP = 1016

aj = json.load(open("logs/system_calibration_20260710_233443/anchor_layout.json"))
P = np.array([[a["x_mm"], a["y_mm"], a["z_mm"]] for a in aj["anchors"]])
LBL = [a["label"] for a in aj["anchors"]]
wj = json.load(open("logs/system_calibration_20260710_233443/wand_positions.json"))
W = np.array([v["position_mm"] for v in wj["wand_tags"].values()])
Wc = W.mean(0)
F = open(os.path.join(HERE, "findings_full.txt"), "w")
def log(*a): print(*a); print(*a, file=F)

# ---------- parse ----------
rng_rows, cir_aid, cir_mag, diag = [], [], [], []
for L in open(LOG):
    if not L.startswith("LSCAN;"):
        continue
    rng_rows.append({int(m.group(1)): int(m.group(2))
                     for m in re.finditer(r';a(\d)=(-?\d+)', L)})
    cm = re.search(r';cir_aid=(\d+);', L)
    dm = re.search(r';rcph=(\d+);rxtofs=(-?\d+);ttcki=(\d+);agc=(\d+);', L)
    hm = re.search(r';cir=([0-9A-Fa-f]+)', L)
    cir_aid.append(int(cm.group(1)) if cm else None)
    diag.append(tuple(int(x) for x in dm.groups()) if dm else None)
    if hm and len(hm.group(1)) == 8128:
        b = np.frombuffer(bytes.fromhex(hm.group(1)), dtype="<i2").astype(float).reshape(NTAP, 2)
        cir_mag.append(np.hypot(b[:, 0], b[:, 1]))
    else:
        cir_mag.append(None)
N = len(rng_rows); t = np.arange(N) * 120.0 / N
log(f"cycles={N}  CIR-lines={sum(x is not None for x in cir_mag)}")

def valid(mm): return 300 <= mm <= 8000

# ---------- trilaterate (relaxed z for hand-raised highs) ----------
def solve(rg, x0):
    ids = [a for a in rg if valid(rg[a])]
    if len(ids) < 4:
        return None, ids, np.nan
    Pi = P[ids]; di = np.array([rg[a] for a in ids], float)
    r = least_squares(lambda x: np.linalg.norm(Pi - x, axis=1) - di, x0,
                      method="lm", max_nfev=200)
    return r.x, ids, float(np.sqrt(np.mean(r.fun**2)))
pos = np.full((N, 3), np.nan); resid = np.full(N, np.nan); nanch = np.zeros(N, int)
x0 = P.mean(0)
for i, rg in enumerate(rng_rows):
    s, ids, rms = solve(rg, x0); nanch[i] = len(ids)
    if s is not None and -2500 < s[0] < 7500 and -3000 < s[1] < 6000 and -6000 < s[2] < 1500:
        pos[i] = s; resid[i] = rms; x0 = s
mpos = np.isfinite(pos[:, 0])
log(f"solved={mpos.sum()}/{N}  median solve-residual={np.nanmedian(resid):.0f}mm")

# per-anchor CIR-target cycle indices
tgt_idx = {a: [i for i in range(N) if cir_aid[i] == a and cir_mag[i] is not None]
           for a in range(8)}

# ============================================================ 1: waterfall x8
fig, axes = plt.subplots(2, 4, figsize=(20, 9))
for a in range(8):
    ax = axes[a // 4][a % 4]; idx = tgt_idx[a]
    if not idx:
        ax.set_title(f"a{a}({LBL[a]}) no CIR"); continue
    Wm = np.array([cir_mag[i] for i in idx]); tt = t[idx]
    pk = int(np.median(np.argmax(Wm, axis=1))); lo, hi = max(0, pk-30), min(NTAP, pk+130)
    im = ax.imshow(20*np.log10(Wm[:, lo:hi]+1), aspect="auto", origin="lower",
                   extent=[lo, hi, tt[0], tt[-1]], cmap="turbo")
    ax.set_title(f"a{a}({LBL[a]})  {len(idx)} caps"); ax.set_xlabel("tap"); ax.set_ylabel("t(s)")
    fig.colorbar(im, ax=ax, shrink=0.7)
fig.suptitle("1 | CIR waterfall (dB) -- all 8 anchors (Geiger->anchor channel over time)", fontsize=14)
fig.tight_layout(); fig.savefig(os.path.join(HERE, "full1_waterfall_all8.png"), dpi=95); plt.close(fig)

# ============================================================ 2: mean CIR signature (peak-aligned)
fig, ax = plt.subplots(figsize=(11, 6))
CENTER = 60
for a in range(8):
    idx = tgt_idx[a]
    if not idx: continue
    stack = []
    for i in idx:
        c = cir_mag[i]; pk = int(np.argmax(c))
        seg = np.zeros(200)
        for k in range(200):
            j = pk - CENTER + k
            if 0 <= j < NTAP: seg[k] = c[j]
        stack.append(seg / (seg.max() + 1e-9))
    mean = np.mean(stack, 0)
    ax.plot((np.arange(200)-CENTER)*TAP_NS, mean, label=f"a{a}({LBL[a]})", color=plt.cm.tab10(a))
ax.axvline(0, color="k", ls=":", lw=0.8)
ax.set_xlabel("delay relative to peak (ns)"); ax.set_ylabel("normalized |CIR|")
ax.set_title("2 | Mean CIR channel signature per anchor (peak-aligned)\n"
             "sharp+low-tail = clean LOS;  wide tail = multipath/NLOS")
ax.set_xlim(-15, 60); ax.legend(ncol=4); ax.grid(alpha=0.3)
fig.tight_layout(); fig.savefig(os.path.join(HERE, "full2_mean_cir.png"), dpi=110); plt.close(fig)

# ============================================================ 3: first-path tap + peak mag vs time
fig, (axU, axL) = plt.subplots(2, 1, figsize=(13, 8), sharex=True)
for a in range(8):
    idx = tgt_idx[a]
    if not idx: continue
    pks = np.array([np.argmax(cir_mag[i]) for i in idx])
    mags = np.array([cir_mag[i].max() for i in idx])
    axU.plot(t[idx], pks, '.', ms=5, color=plt.cm.tab10(a), label=f"a{a}({LBL[a]})")
    axL.plot(t[idx], 20*np.log10(mags+1), '.', ms=5, color=plt.cm.tab10(a))
axU.set_ylabel("first-path tap index"); axU.set_title("3 | CIR first-path tap (top) & peak magnitude dB (bottom) vs time")
axU.legend(ncol=4, fontsize=8); axU.grid(alpha=0.3)
axL.set_ylabel("peak |CIR| (dB)"); axL.set_xlabel("time (s)"); axL.grid(alpha=0.3)
fig.tight_layout(); fig.savefig(os.path.join(HERE, "full3_firstpath.png"), dpi=110); plt.close(fig)

# ============================================================ 4: per-anchor range residual from solved pos
res_by_a = {a: [] for a in range(8)}
for i in range(N):
    if not mpos[i]: continue
    for a in range(8):
        if valid(rng_rows[i].get(a, -1)):
            res_by_a[a].append(np.linalg.norm(pos[i]-P[a]) - rng_rows[i][a])
fig, (axB, axH) = plt.subplots(1, 2, figsize=(15, 5))
means = [np.mean(res_by_a[a]) for a in range(8)]; stds = [np.std(res_by_a[a]) for a in range(8)]
axB.bar(range(8), means, yerr=stds, capsize=4, color=["#2a9d8f" if a%2==0 else "#e76f51" for a in range(8)])
axB.axhline(0, color="k", lw=0.8)
axB.set_xticks(range(8)); axB.set_xticklabels([f"a{a}\n{LBL[a]}" for a in range(8)])
axB.set_ylabel("range residual = |pos-anchor| - measured (mm)")
axB.set_title("4 | Per-anchor range bias (mean) & noise (std) from solved position")
axB.grid(axis="y", alpha=0.3)
for a in range(8):
    axH.hist(res_by_a[a], bins=40, histtype="step", label=f"a{a}({LBL[a]})", color=plt.cm.tab10(a))
axH.set_xlabel("residual (mm)"); axH.set_title("residual distribution per anchor"); axH.legend(ncol=2, fontsize=8)
axH.set_xlim(-1500, 1500); axH.grid(alpha=0.3)
fig.tight_layout(); fig.savefig(os.path.join(HERE, "full4_range_residuals.png"), dpi=110); plt.close(fig)
log("\nper-anchor range residual (bias +/- noise, mm) from solved position:")
for a in range(8):
    log(f"  a{a}({LBL[a]}): bias={means[a]:+7.0f}  noise={stds[a]:5.0f}  n={len(res_by_a[a])}")

# ============================================================ 5: RMS delay spread per anchor
def delay_spread_ns(c):
    pk = int(np.argmax(c)); lo, hi = max(0, pk-5), min(NTAP, pk+120)
    seg = c[lo:hi]**2; tau = (np.arange(lo, hi)-pk)*TAP_NS
    if seg.sum() <= 0: return np.nan
    mean = (tau*seg).sum()/seg.sum()
    return float(np.sqrt(((tau-mean)**2*seg).sum()/seg.sum()))
ds = {a: [delay_spread_ns(cir_mag[i]) for i in tgt_idx[a]] for a in range(8)}
fig, ax = plt.subplots(figsize=(11, 5))
ax.boxplot([ds[a] for a in range(8)], labels=[f"a{a}\n{LBL[a]}" for a in range(8)], showfliers=False)
ax.set_ylabel("RMS delay spread (ns)")
ax.set_title("5 | Multipath richness (RMS delay spread) per anchor -- higher = more reflections/NLOS")
ax.grid(axis="y", alpha=0.3)
fig.tight_layout(); fig.savefig(os.path.join(HERE, "full5_delayspread.png"), dpi=110); plt.close(fig)
log("\nRMS delay spread median (ns) per anchor:")
for a in range(8): log(f"  a{a}({LBL[a]}): {np.nanmedian(ds[a]):.2f} ns")

# ============================================================ 6: solve quality vs time
speed = np.full(N, np.nan)
ii = np.where(mpos)[0]
for k in range(1, len(ii)):
    dt = t[ii[k]]-t[ii[k-1]]
    if dt > 0: speed[ii[k]] = np.linalg.norm(pos[ii[k]]-pos[ii[k-1]])/dt
fig, ax = plt.subplots(3, 1, figsize=(13, 9), sharex=True)
ax[0].plot(t, nanch, '.', ms=3); ax[0].set_ylabel("#anchors used"); ax[0].axhline(8, color="g", ls=":")
ax[0].set_title("6 | Position-solve quality vs time"); ax[0].grid(alpha=0.3)
ax[1].plot(t[mpos], resid[mpos], '.', ms=3, color="purple"); ax[1].set_ylabel("solve residual RMS (mm)")
ax[1].grid(alpha=0.3); ax[1].set_ylim(0, np.nanpercentile(resid, 98))
ax[2].plot(t[mpos], speed[mpos], '.', ms=3, color="darkorange"); ax[2].set_ylabel("speed (mm/s)")
ax[2].set_xlabel("time (s)"); ax[2].grid(alpha=0.3); ax[2].set_ylim(0, np.nanpercentile(speed, 97))
fig.tight_layout(); fig.savefig(os.path.join(HERE, "full6_solve_quality.png"), dpi=110); plt.close(fig)

# ============================================================ 7: diagnostic fields vs time
di = [(t[i], cir_aid[i], diag[i]) for i in range(N) if diag[i] is not None and cir_aid[i] is not None]
fig, ax = plt.subplots(2, 2, figsize=(14, 8))
labels = ["rcph", "rxtofs (clock offset)", "ttcki", "agc (EDG1)"]
for fi in range(4):
    axx = ax[fi//2][fi%2]
    for a in range(8):
        pts = [(tt, dd[fi]) for tt, ca, dd in di if ca == a]
        if pts:
            axx.plot([p[0] for p in pts], [p[1] for p in pts], '.', ms=4,
                     color=plt.cm.tab10(a), label=f"a{a}" if fi==0 else None)
    axx.set_title(labels[fi]); axx.set_xlabel("time(s)"); axx.grid(alpha=0.3)
ax[0][0].legend(ncol=4, fontsize=7)
fig.suptitle("7 | CIR-target per-frame diagnostics vs time", fontsize=13)
fig.tight_layout(); fig.savefig(os.path.join(HERE, "full7_diag.png"), dpi=110); plt.close(fig)
agc_vals = [dd[3] for _, _, dd in di]
log(f"\nagc(EDG1): min={min(agc_vals)} max={max(agc_vals)} mean={np.mean(agc_vals):.1f} "
    f"-> {'ALL ZERO (needs check)' if max(agc_vals)==0 else 'nonzero'}")
rxtofs_by_a = {a: [dd[1] for _, ca, dd in di if ca==a] for a in range(8)}
log("rxtofs (RX clock offset) median per anchor:")
for a in range(8):
    v = rxtofs_by_a[a]
    if v: log(f"  a{a}({LBL[a]}): {np.median(v):.0f}")

# ============================================================ 8: wand loop circle fit
seg = pos[mpos & (t > 75)][:, :2]
def circ_res(p): return np.hypot(seg[:,0]-p[0], seg[:,1]-p[1]) - p[2]
if len(seg) > 8:
    r = least_squares(circ_res, [Wc[0], Wc[1], 1000.0])
    cx, cy, cr = r.x
    ang = np.sort(np.arctan2(seg[:,1]-cy, seg[:,0]-cx))
    cover = np.degrees(2*np.pi - np.diff(np.concatenate([ang,[ang[0]+2*np.pi]])).max())
    fig, ax = plt.subplots(figsize=(8, 8))
    sc = ax.scatter(pos[mpos,0], pos[mpos,1], c=t[mpos], cmap="viridis", s=12)
    th = np.linspace(0, 2*np.pi, 100)
    ax.plot(cx+cr*np.cos(th), cy+cr*np.sin(th), 'r-', lw=2, label=f"fit circle r={cr:.0f}mm")
    ax.scatter(W[:,0], W[:,1], c="magenta", marker="*", s=280, edgecolors="k", zorder=5, label="wand")
    ax.scatter(P[:,0], P[:,1], c="black", marker="^", s=70)
    for a in range(8): ax.annotate(LBL[a], (P[a,0], P[a,1]), fontsize=8)
    ax.set_aspect("equal"); ax.set_xlabel("x(mm)"); ax.set_ylabel("y(mm)")
    ax.set_title(f"8 | Wand loop: fit circle center=({cx:.0f},{cy:.0f}) r={cr:.0f}mm\n"
                 f"offset from wand={np.hypot(cx-Wc[0],cy-Wc[1]):.0f}mm  bearing coverage={cover:.0f}deg")
    ax.legend(); ax.grid(alpha=0.3); fig.colorbar(sc, ax=ax, label="time(s)")
    fig.tight_layout(); fig.savefig(os.path.join(HERE, "full8_loop_circle.png"), dpi=110); plt.close(fig)
    log(f"\nwand loop: fit-circle center=({cx:.0f},{cy:.0f}) r={cr:.0f}mm  "
        f"offset-from-wand={np.hypot(cx-Wc[0],cy-Wc[1]):.0f}mm  coverage={cover:.0f}deg")

# ============================================================ 9: CIR-tap range vs SS-TWR range corr
log("\nCIR first-path-tap vs SS-TWR range correlation per anchor (relative check):")
for a in range(8):
    idx = [i for i in tgt_idx[a] if valid(rng_rows[i].get(a,-1))]
    if len(idx) < 8: continue
    taps = np.array([np.argmax(cir_mag[i]) for i in idx])*TAP_M*1000
    rr = np.array([rng_rows[i][a] for i in idx], float)
    if taps.std() > 1e-6:
        c = np.corrcoef(taps, rr)[0,1]
        log(f"  a{a}({LBL[a]}): corr={c:+.2f}  (tap moves {taps.std():.0f}mm, range {rr.std():.0f}mm, n={len(idx)})")
F.close()
print("=== all figures + findings_full.txt written to", HERE)
