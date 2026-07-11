#!/usr/bin/env python3
"""Re-analysis with correct activity timeline: start=stand+rotate, middle=random
scan, end=loop around the wand tag. Overlays wand, relaxes z (hand-raised highs
are legit), checks whether the end-loop encircles the wand."""
import json, re, os
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from scipy.optimize import least_squares

HERE = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(os.path.dirname(HERE), "scan.log")

aj = json.load(open("logs/system_calibration_20260710_233443/anchor_layout.json"))
P = np.array([[a["x_mm"], a["y_mm"], a["z_mm"]] for a in aj["anchors"]])
LBL = [a["label"] for a in aj["anchors"]]
wj = json.load(open("logs/system_calibration_20260710_233443/wand_positions.json"))
W = np.array([v["position_mm"] for v in wj["wand_tags"].values()])   # (3,3)
Wc = W.mean(0)   # wand centroid

rows = []
for L in open(LOG):
    if L.startswith("LSCAN;"):
        rows.append({int(m.group(1)): int(m.group(2))
                     for m in re.finditer(r';a(\d)=(-?\d+)', L)})
N = len(rows); t = np.arange(N) * 120.0 / N

def solve(rg, x0):
    ids = [a for a in rg if 300 <= rg[a] <= 8000]
    if len(ids) < 4:
        return None
    Pi = P[ids]; di = np.array([rg[a] for a in ids], float)
    r = least_squares(lambda x: np.linalg.norm(Pi - x, axis=1) - di, x0,
                      method="lm", max_nfev=200)
    return r.x

tr = np.full((N, 3), np.nan); x0 = P.mean(0)
for i, rg in enumerate(rows):
    s = solve(rg, x0)
    # relaxed gate: keep x/y roughly in-room, z UNbounded upward (hand raised high)
    if s is not None and -2500 < s[0] < 7500 and -3000 < s[1] < 6000 and -6000 < s[2] < 1500:
        tr[i] = s; x0 = s
m = np.isfinite(tr[:, 0])
print(f"solved {m.sum()}/{N}")

# distance from wand centroid over time (loop => ~constant at the end)
dist = np.linalg.norm(tr[:, :2] - Wc[:2], axis=1)

# --- detect the loop: last window where bearing to wand sweeps ~360 deg ---
def angspan(seg):
    ang = np.sort(np.arctan2(seg[:, 1] - Wc[1], seg[:, 0] - Wc[0]))
    if len(ang) < 5:
        return 0
    gaps = np.diff(np.concatenate([ang, [ang[0] + 2*np.pi]]))
    return np.degrees(2*np.pi - gaps.max())   # covered arc = full minus largest gap

for w0 in (60, 70, 80, 90):
    seg = tr[m & (t > w0)]
    print(f"  last {120-w0:>2}s: n={len(seg):3d}  bearing coverage around wand = {angspan(seg):.0f} deg  "
          f"mean dist={np.nanmean(np.linalg.norm(seg[:,:2]-Wc[:2],axis=1)):.0f}mm")

# ================= FIG: top view + dist(t) + z(t) =================
fig = plt.figure(figsize=(15, 5))
# panel 1: top view, colored by time, wand + loop
ax1 = fig.add_subplot(1, 3, 1)
sc = ax1.scatter(tr[m, 0], tr[m, 1], c=t[m], cmap="viridis", s=10)
loop = m & (t > 80)
ax1.scatter(tr[loop, 0], tr[loop, 1], facecolors="none", edgecolors="red", s=45,
            linewidths=1.2, label="last 40 s (loop)")
ax1.scatter(P[:, 0], P[:, 1], c="black", marker="^", s=70)
for a in range(8):
    ax1.annotate(LBL[a], (P[a, 0], P[a, 1]), fontsize=8)
ax1.scatter(W[:, 0], W[:, 1], c="magenta", marker="*", s=260, edgecolors="k",
            zorder=5, label="wand tags")
ax1.scatter([Wc[0]], [Wc[1]], c="magenta", marker="+", s=200, label="wand center")
ax1.set_xlabel("x (mm)"); ax1.set_ylabel("y (mm)"); ax1.set_aspect("equal")
ax1.set_title("Top view: track (color=time) + loop around wand"); ax1.legend(fontsize=8)
ax1.grid(alpha=0.3)
fig.colorbar(sc, ax=ax1, label="time (s)")
# panel 2: distance to wand vs time (phases)
ax2 = fig.add_subplot(1, 3, 2)
ax2.plot(t[m], dist[m], '.', ms=4)
ax2.axhline(np.nanmean(dist[m & (t > 80)]), color="red", ls="--",
            label=f"loop mean r={np.nanmean(dist[m&(t>80)]):.0f}mm")
ax2.set_xlabel("time (s)"); ax2.set_ylabel("horiz. distance to wand (mm)")
ax2.set_title("Distance to wand vs time\n(start=rotate, mid=scan, end=loop~const)")
ax2.legend(); ax2.grid(alpha=0.3)
# panel 3: height vs time (hand raised high => z more negative/up)
ax3 = fig.add_subplot(1, 3, 3)
ax3.plot(t[m], tr[m, 2], '.', ms=4, color="teal")
ax3.axhline(0, color="gray", ls=":", label="floor plane (A-D)")
ax3.axhline(-1500, color="brown", ls=":", label="ceiling plane (E-H)")
ax3.set_xlabel("time (s)"); ax3.set_ylabel("z (mm, more negative = higher)")
ax3.set_title("Height vs time (hand-raised highs are real)")
ax3.legend(fontsize=8); ax3.grid(alpha=0.3)
fig.tight_layout(); fig.savefig(os.path.join(HERE, "fig5_wand_loop.png"), dpi=110)
plt.close(fig)
print("wrote fig5_wand_loop.png ; wand centroid =", Wc.round(0))
