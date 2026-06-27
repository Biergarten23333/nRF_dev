"""
Clean, textbook GPS-style analysis at the TAG-LOCALIZATION level.

For a tag at x seeing anchors a_i:  r_i = ||x - a_i|| + b + eps   (b = common clock/
antenna delay, identical on every range -> exactly a GPS receiver-clock bias).

Geometry matrix rows g_i = [u_i^T, 1], u_i = (x - a_i)/||x - a_i||.
This is the GPS DOP setup. We quantify, per real tag position, how a common delay
projects into VERTICAL vs HORIZONTAL position -- the alias vector
        w = (A^T A)^{-1} A^T 1        (position shift per unit unmodelled delay)
and the clock<->vertical correlation / variance inflation when b is co-estimated.

Vertical axis is checked from the data (not assumed).
"""
import sys
SCR = "/tmp/claude-1000/-home-zekaixiao-Documents-nRF-dev-BioSpur-UWB-before-start/bf909886-ad48-495f-b091-a809435e28fc/scratchpad"
sys.argv = ["t", SCR]
import numpy as np
from collections import defaultdict
import harness as H
np.set_printoptions(precision=3, suppress=True)

A = H.TRUTH.astype(float)
labels = list("ABCDEFGH")
print("=== anchor layout: per-axis spread (find the two-layer/vertical axis) ===")
for ax, nm in enumerate("XYZ"):
    print(f"  axis {nm}: min={A[:,ax].min():8.1f} max={A[:,ax].max():8.1f} "
          f"span={np.ptp(A[:,ax]):7.1f}  vals={np.round(A[:,ax],0)}")
# two layers: split by the axis whose values are most clearly bimodal at ~1.4m
print("  (lower group ABCD vs upper EFGH per axis:)")
for ax, nm in enumerate("XYZ"):
    lo = A[:4, ax].mean(); hi = A[4:, ax].mean()
    print(f"    {nm}: ABCD mean={lo:8.1f}  EFGH mean={hi:8.1f}  separation={hi-lo:8.1f}")
VERT = 1  # Y, per eval_lib; confirmed against the print above

# ---- real tag positions (trilaterated against truth anchors) ----------------
rows = []
for n in [f"static_ID{k:02d}" for k in range(1, 25)]:
    d = H.cap_dir(n)
    if d:
        rows += H.load_cap(d, n, 5)
frames = defaultdict(list)
for r in rows:
    frames[(r["source"], r["tag"], r["sweep"])].append((r["anchor"], r["range_mm"]))
frames = {k: v for k, v in frames.items() if len(v) >= 4}

def trilat(meas):
    p = A.mean(0).copy()
    for _ in range(40):
        Jr = []; res = []
        for aid, rng in meas:
            d = p - A[aid]; nd = max(np.linalg.norm(d), 1e-6)
            Jr.append(d / nd); res.append(nd - rng)
        dp = np.linalg.lstsq(np.array(Jr), -np.array(res), rcond=None)[0]
        p = p + dp
        if np.linalg.norm(dp) < 1e-4:
            break
    return p

# ---- per-tag GPS DOP / alias decomposition ----------------------------------
hor_ax = [a for a in range(3) if a != VERT]
wv, wh, rho, infl, vdop, hdop = [], [], [], [], [], []
for k, meas in frames.items():
    aids = [m[0] for m in meas]
    x = trilat(meas)
    U = np.array([(x - A[i]) / max(np.linalg.norm(x - A[i]), 1e-6) for i in aids])  # N x 3
    # alias vector w = (A^T A)^-1 A^T 1  : position shift per unit unmodelled delay
    try:
        w = np.linalg.solve(U.T @ U, U.T @ np.ones(len(aids)))
    except np.linalg.LinAlgError:
        continue
    wv.append(abs(w[VERT]))
    wh.append(np.linalg.norm(w[hor_ax]))
    # 4-param geometry [u, 1]; covariance (sigma=1)
    G = np.hstack([U, np.ones((len(aids), 1))])
    C = np.linalg.inv(G.T @ G)
    bi = 3
    rho.append(C[VERT, bi] / np.sqrt(C[VERT, VERT] * C[bi, bi]))
    # vertical variance inflation from co-estimating the clock vs clock-known
    Cv_clockfixed = np.linalg.inv(U.T @ U)[VERT, VERT]
    infl.append(C[VERT, VERT] / Cv_clockfixed)
    vdop.append(np.sqrt(C[VERT, VERT]))
    hdop.append(np.sqrt(C[hor_ax[0], hor_ax[0]] + C[hor_ax[1], hor_ax[1]]))

wv, wh, rho, infl = map(np.array, (wv, wh, rho, infl))
vdop, hdop = np.array(vdop), np.array(hdop)
print(f"\n=== tag-localization GPS analysis over {len(wv)} real static tag frames (medians) ===")
print(f"  alias vector w = (A^T A)^-1 A^T 1   (mm position shift per 1 mm common delay)")
print(f"    |w_vertical|   = {np.median(wv):.3f}")
print(f"    |w_horizontal| = {np.median(wh):.3f}")
print(f"    ratio vert/horiz = {np.median(wv)/np.median(wh):.2f}x   "
      f"(>1 => a common delay lands mostly in VERTICAL)")
print(f"  so a tag antenna delay of, e.g., 35 mm  ->  vertical bias ~ {35*np.median(wv):.0f} mm, "
      f"horizontal bias ~ {35*np.median(wh):.0f} mm")
print(f"\n  clock<->vertical correlation rho       median = {np.median(rho):+.3f}")
print(f"  vertical variance inflation 1/(1-rho^2)~ median = {np.median(infl):.2f}x "
      f"(cost of co-estimating the delay)")
print(f"  VDOP median = {np.median(vdop):.2f}   HDOP median = {np.median(hdop):.2f}   "
      f"VDOP/HDOP = {np.median(vdop)/np.median(hdop):.2f}x")
