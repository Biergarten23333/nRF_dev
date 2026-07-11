#!/usr/bin/env python3
"""Geiger MODE_SCAN capture analysis: ranges, 3D position track, CIR waterfall,
even/odd responder diagnostic. All outputs stay in this analysis/ dir."""
import json, re, struct, os
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from scipy.optimize import least_squares

HERE = os.path.dirname(os.path.abspath(__file__))
CAP = os.path.dirname(HERE)
LOG = os.path.join(CAP, "scan.log")
ANCHORS_JSON = "logs/system_calibration_20260710_233443/anchor_layout.json"

# --- anchor geometry (mm) ---
aj = json.load(open(ANCHORS_JSON))
P = np.array([[a["x_mm"], a["y_mm"], a["z_mm"]] for a in aj["anchors"]])  # (8,3)
LBL = [a["label"] for a in aj["anchors"]]

# --- parse log ---
cyc = []   # list of (ranges dict, cir_aid, cir_hex)
for L in open(LOG):
    if not L.startswith("LSCAN;"):
        continue
    rg = {int(m.group(1)): int(m.group(2)) for m in re.finditer(r';a(\d)=(-?\d+)', L)}
    cm = re.search(r';cir_aid=(\d+);', L)
    hm = re.search(r';cir=([0-9A-Fa-f]+)', L)
    cyc.append((rg, int(cm.group(1)) if cm else None,
                hm.group(1) if (hm and len(hm.group(1)) == 8128) else None))
N = len(cyc)
DUR = 120.0
t = np.arange(N) * (DUR / N)   # approx wall time (no per-line stamp)
print(f"parsed {N} cycles")

def valid(mm): return 300 <= mm <= 8000

# ============ FIG 1: per-anchor range vs time ============
fig, ax = plt.subplots(figsize=(12, 5))
for a in range(8):
    y = [rg.get(a, -1) for rg, _, _ in cyc]
    y = [v if valid(v) else np.nan for v in y]
    ax.plot(t, y, '.', ms=3, label=f"a{a}({LBL[a]})",
            color=plt.cm.tab10(a), alpha=0.7 if a % 2 == 0 else 0.9)
ax.set_xlabel("time (s, approx)"); ax.set_ylabel("range (mm)")
ax.set_title("Per-anchor range vs time (even anchors dense, odd sparse)")
ax.legend(ncol=8, fontsize=8, loc="upper center"); ax.grid(alpha=0.3)
fig.tight_layout(); fig.savefig(os.path.join(HERE, "fig1_ranges.png"), dpi=110)
plt.close(fig)

# ============ 3D trilateration (track) ============
def solve(rng, x0):
    ids = [a for a in rng if valid(rng[a])]
    if len(ids) < 4:
        return None, len(ids)
    Pi = P[ids]; di = np.array([rng[a] for a in ids], float)
    def res(x): return np.linalg.norm(Pi - x, axis=1) - di
    r = least_squares(res, x0, method="lm", max_nfev=200)
    return r.x, len(ids)

track = np.full((N, 3), np.nan)
x0 = P[[0, 2, 4, 6]].mean(0)   # start guess = centroid of reliable anchors
for i, (rg, _, _) in enumerate(cyc):
    sol, k = solve(rg, x0)
    if sol is not None:
        # keep inside a generous room box (reject blow-ups)
        if (-2000 < sol[0] < 7000 and -2500 < sol[1] < 5500 and -3500 < sol[2] < 2000):
            track[i] = sol; x0 = sol
solved = np.isfinite(track[:, 0]).sum()
print(f"3D solved cycles: {solved}/{N}")

# ============ FIG 2: 3D trajectory + top view ============
fig = plt.figure(figsize=(14, 6))
axA = fig.add_subplot(1, 2, 1, projection="3d")
m = np.isfinite(track[:, 0])
sc = axA.scatter(track[m, 0], track[m, 1], track[m, 2], c=t[m], cmap="viridis", s=8)
axA.scatter(P[:, 0], P[:, 1], P[:, 2], c="red", marker="^", s=60)
for a in range(8):
    axA.text(P[a, 0], P[a, 1], P[a, 2], f" {LBL[a]}", color="red", fontsize=9)
axA.set_xlabel("x (mm)"); axA.set_ylabel("y (mm)"); axA.set_zlabel("z (mm)")
axA.set_title(f"3D handheld track ({solved}/{N} cycles solved)\ncolor=time; red=anchors")
fig.colorbar(sc, ax=axA, shrink=0.5, label="time (s)")
# top view + last-30s highlighted (the rotation part)
axB = fig.add_subplot(1, 2, 2)
axB.scatter(track[m, 0], track[m, 1], c=t[m], cmap="viridis", s=8)
last = m & (t > DUR - 30)
axB.scatter(track[last, 0], track[last, 1], facecolors="none", edgecolors="red",
            s=40, label="last 30 s (rotation)")
axB.scatter(P[:, 0], P[:, 1], c="red", marker="^", s=70)
for a in range(8):
    axB.annotate(LBL[a], (P[a, 0], P[a, 1]), color="red", fontsize=9)
axB.set_xlabel("x (mm)"); axB.set_ylabel("y (mm)"); axB.set_aspect("equal")
axB.set_title("Top view (x-y); last 30 s circled"); axB.legend(); axB.grid(alpha=0.3)
fig.tight_layout(); fig.savefig(os.path.join(HERE, "fig2_track3d.png"), dpi=110)
plt.close(fig)

# ============ FIG 3: CIR waterfall for one anchor over its target-captures ============
def cir_mag(hx):
    b = bytes.fromhex(hx)
    iq = np.frombuffer(b, dtype="<i2").astype(float).reshape(-1, 2)  # (1016,2)
    return np.hypot(iq[:, 0], iq[:, 1])

# pick the even anchor with most CIR captures; build (capture# x tap) magnitude
best_a, best = 0, -1
for a in range(8):
    n = sum(1 for _, ca, hx in cyc if ca == a and hx)
    if n > best: best, best_a = n, a
caps = [(ti, cir_mag(hx)) for ti, (_, ca, hx) in zip(t, cyc) if ca == best_a and hx]
if caps:
    W = np.array([c[1] for c in caps]); tt = np.array([c[0] for c in caps])
    # zoom to the region around the first-path peak
    pk = int(np.median(np.argmax(W, axis=1)))
    lo, hi = max(0, pk - 40), min(1016, pk + 120)
    fig, ax = plt.subplots(figsize=(12, 5))
    im = ax.imshow(20 * np.log10(W[:, lo:hi] + 1), aspect="auto", origin="lower",
                   extent=[lo, hi, tt[0], tt[-1]], cmap="turbo")
    ax.set_xlabel("CIR tap index"); ax.set_ylabel("time (s)")
    ax.set_title(f"CIR waterfall — anchor a{best_a}({LBL[best_a]}), {len(caps)} captures "
                 f"(dB); watch multipath vs walk/rotation")
    fig.colorbar(im, ax=ax, label="|CIR| (dB)")
    fig.tight_layout(); fig.savefig(os.path.join(HERE, "fig3_cir_waterfall.png"), dpi=110)
    plt.close(fig)

# ============ FIG 4: even/odd responder diagnostic ============
tgt_rate = []; oth_rate = []
for a in range(8):
    astgt = [rg for rg, ca, _ in cyc if ca == a]
    other = [rg for rg, ca, _ in cyc if ca != a]
    tgt_rate.append(100 * np.mean([valid(rg.get(a, -1)) for rg in astgt]) if astgt else 0)
    oth_rate.append(100 * np.mean([valid(rg.get(a, -1)) for rg in other]) if other else 0)
fig, ax = plt.subplots(figsize=(10, 5))
x = np.arange(8); w = 0.38
ax.bar(x - w/2, tgt_rate, w, label="when it IS CIR-target (rank 0)", color="#2a9d8f")
ax.bar(x + w/2, oth_rate, w, label="otherwise (natural rank)", color="#e76f51")
ax.set_xticks(x); ax.set_xticklabels([f"a{a}({LBL[a]})\n{'even' if a%2==0 else 'ODD'}" for a in range(8)])
ax.set_ylabel("response rate (%)")
ax.set_title("Every anchor answers ~100% at rank 0 → anchors are FINE;\n"
             "odd anchors only ~8% at natural rank → firmware ranging-pass drops them")
ax.legend(); ax.grid(axis="y", alpha=0.3)
fig.tight_layout(); fig.savefig(os.path.join(HERE, "fig4_evenodd.png"), dpi=110)
plt.close(fig)

# ============ findings ============
with open(os.path.join(HERE, "findings.txt"), "w") as f:
    f.write(f"cycles={N}  3D-solved={solved}  CIR-anchor-for-waterfall=a{best_a}({LBL[best_a]})\n")
    f.write("even/odd response (target-rank0 % / natural %):\n")
    for a in range(8):
        f.write(f"  a{a}({LBL[a]},{'even' if a%2==0 else 'odd'}): {tgt_rate[a]:.0f}% / {oth_rate[a]:.0f}%\n")
    if m.sum():
        f.write(f"track x[{np.nanmin(track[:,0]):.0f},{np.nanmax(track[:,0]):.0f}] "
                f"y[{np.nanmin(track[:,1]):.0f},{np.nanmax(track[:,1]):.0f}] "
                f"z[{np.nanmin(track[:,2]):.0f},{np.nanmax(track[:,2]):.0f}] mm\n")
print("figures + findings written to", HERE)
print(open(os.path.join(HERE, "findings.txt")).read())
