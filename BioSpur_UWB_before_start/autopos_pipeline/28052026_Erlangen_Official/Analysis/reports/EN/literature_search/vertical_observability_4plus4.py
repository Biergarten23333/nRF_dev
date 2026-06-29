"""
Vertical observability vs upper-layer anchor count  (Paper A, claim 2: the 4+4 geometry).

Claim under test: a two-layer array restores well-conditioned vertical GEOMETRY for anchor
self-calibration, the lower (coplanar) layer alone leaves the vertical exactly under-determined,
and raising more anchors monotonically improves vertical observability.

TWO complementary metrics (both honest, they answer different questions):

(A) ANCHOR SELF-CALIBRATION vertical CRLB  [the claim-2 quantity].
    Inter-anchor ranging only, r_ij = ||a_i - a_j|| + noise. Hold 3 lower reference corners
    fixed (gauge). Estimate the remaining anchors' coordinates. The Cramer-Rao lower bound on
    their vertical (Y) coordinates measures how well vertical geometry is recovered.
      Key fact: for a COPLANAR layer, d r_ij / d y = (y_i - y_j)/r = 0, so the vertical column
      of the Jacobian is exactly zero -> vertical is SINGULAR (CRLB = inf). One raised anchor
      makes (y_i - y_j) != 0 and unlocks it. This is the mechanism the 4+4 array exploits.

(B) DOWNSTREAM tag-localization VDOP  [corroboration].
    With the calibrated layout, range-only position DOP for tags in the volume. Coplanar
    anchors are NOT singular here (tags sit off-plane) but vertical DOP is poor; more upper
    anchors reduce it.

Geometry: 4 corners of an L x L square. Each corner always has a LOWER anchor (Y=y_low). k of
the corners additionally have an UPPER anchor (Y=y_up). Y = vertical (matches eval_lib).

Outputs: vertical_observability_4plus4.png + _results.txt in this folder.
"""
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import os
from itertools import combinations

OUT = os.path.dirname(os.path.abspath(__file__))

# ---- geometry (metres; Y = vertical) ----------------------------------------
L      = 5.0
Y_LOW  = 0.15
Y_UP   = 1.65
SIGMA  = 0.030          # 30 mm inter-anchor ranging noise (solver cfg)
corners_xz = np.array([[-L/2, -L/2], [L/2, -L/2], [L/2, L/2], [-L/2, L/2]])

def build_anchors(upper_set):
    """Return anchors and the index list of FIXED reference anchors (3 lower corners)."""
    A = [[x, Y_LOW, z] for (x, z) in corners_xz]          # 0..3 lower
    for c in upper_set:
        x, z = corners_xz[c]
        A.append([x, Y_UP, z])                            # uppers appended
    A = np.asarray(A, float)
    fixed = [0, 1, 2]                                      # gauge: 3 lower reference corners
    return A, fixed

# ---- (A) anchor self-cal vertical CRLB --------------------------------------
def selfcal_vertical_crlb(A, fixed):
    n = len(A)
    free = [i for i in range(n) if i not in fixed]
    if not free:
        return np.inf, np.inf, 0
    idx = {a: 3 * t for t, a in enumerate(free)}          # param offset per free anchor
    P = 3 * len(free)
    rows = []
    for i, j in combinations(range(n), 2):
        d = A[i] - A[j]; r = np.linalg.norm(d); u = d / r
        g = np.zeros(P)
        if i in idx: g[idx[i]:idx[i] + 3] += u
        if j in idx: g[idx[j]:idx[j] + 3] -= u
        if np.any(g):
            rows.append(g / SIGMA)
    J = np.array(rows)
    F = J.T @ J
    w = np.linalg.eigvalsh(F)
    # y-coordinate CRLB via pseudo-inverse on the observable subspace
    Fpinv = np.linalg.pinv(F, rcond=1e-10)
    yvar = [Fpinv[idx[a] + 1, idx[a] + 1] for a in free]   # +1 = Y component
    ycols = [idx[a] + 1 for a in free]
    # detect exact vertical singularity: any y-direction in the FIM null space
    ydir_info = np.array([F[c, c] for c in ycols])
    singular = np.any(ydir_info < 1e-12)
    yvar = np.array(yvar)
    if singular or np.any(yvar > 1e8):
        return np.inf, np.inf, int(np.sum(ydir_info < 1e-12))
    return np.sqrt(yvar.mean()), np.sqrt(yvar.max()), 0

# ---- (B) downstream tag VDOP ------------------------------------------------
gx = np.linspace(-1.5, 1.5, 7); gz = np.linspace(-1.5, 1.5, 7); gy = np.array([0.5, 1.0, 1.5])
TAGS = np.array([[x, y, z] for x in gx for y in gy for z in gz])
def tag_vdop(A):
    vd, hd = [], []
    for x in TAGS:
        d = x - A; r = np.linalg.norm(d, axis=1); u = d / r[:, None]
        M = u.T @ u
        if np.linalg.eigvalsh(M)[0] < 1e-9:
            continue
        C = np.linalg.inv(M)
        vd.append(np.sqrt(C[1, 1])); hd.append(np.sqrt(C[0, 0] + C[2, 2]))
    return np.mean(vd), np.mean(hd)

# ---- balanced raising order (opposite corners first) ------------------------
BAL = {0: [], 1: [0], 2: [0, 2], 3: [0, 2, 1], 4: [0, 1, 2, 3]}

print("=== (A) Anchor self-cal vertical CRLB (inter-anchor ranging, sigma=30mm) ===")
print(f"{'k':>3} {'#anch':>5} {'vCRLB_mean(mm)':>14} {'vCRLB_worst(mm)':>15} {'#sing':>6}")
selfcal = []
for k in range(5):
    A, fixed = build_anchors(BAL[k])
    vm, vw, ns = selfcal_vertical_crlb(A, fixed)
    selfcal.append((k, len(A), vm, vw, ns))
    sm = "  inf (singular)" if not np.isfinite(vm) else f"{vm*1000:14.2f}"
    sw = f"{'inf':>15}" if not np.isfinite(vw) else f"{vw*1000:15.2f}"
    print(f"{k:>3} {len(A):>5} {sm} {sw} {ns:>6}")

print("\n=== (B) Downstream tag VDOP (range-only, sigma=1) ===")
print(f"{'k':>3} {'#anch':>5} {'VDOP':>7} {'HDOP':>7} {'V/H':>6}")
tagdop = []
for k in range(5):
    A, _ = build_anchors(BAL[k])
    vm, hm = tag_vdop(A)
    tagdop.append((k, vm, hm))
    print(f"{k:>3} {len(A):>5} {vm:7.2f} {hm:7.2f} {vm/hm:6.2f}")

# ---- balance vs count control at k=2 ----------------------------------------
print("\n=== Balance vs count at fixed k=2 ===")
res_bal = {}
for name, ups in [("diagonal(balanced)", [0, 2]), ("adjacent(clustered)", [0, 1])]:
    A, fixed = build_anchors(ups)
    vm, vw, _ = selfcal_vertical_crlb(A, fixed)
    vdop, _ = tag_vdop(A)
    res_bal[name] = (vm, vdop)
    print(f"  {name:20s}: self-cal vCRLB_mean = {vm*1000:6.2f} mm, tag VDOP = {vdop:.2f}")
bal_ratio = res_bal["adjacent(clustered)"][0] / res_bal["diagonal(balanced)"][0]
print(f"  -> clustering the same count inflates self-cal vertical CRLB by {bal_ratio:.2f}x")

# ---- plot -------------------------------------------------------------------
fig, ax = plt.subplots(1, 2, figsize=(11, 4.2))
ks = [r[0] for r in selfcal]
vc = [r[2] * 1000 if np.isfinite(r[2]) else None for r in selfcal]
vwc = [r[3] * 1000 if np.isfinite(r[3]) else None for r in selfcal]
kf = [k for k, v in zip(ks, vc) if v is not None]
vf = [v for v in vc if v is not None]
vwf = [v for v in vwc if v is not None]
ax[0].plot(kf, vf, "o-", color="C3", lw=2, label="mean over anchors")
ax[0].plot(kf, vwf, "s--", color="C1", lw=1.8, label="worst-conditioned anchor")
ax[0].axhline(60, color="0.6", ls="--", lw=1, label="production vertical err ~60 mm")
ax[0].annotate("k=0: coplanar layer\nvertical EXACTLY singular\n(CRLB = $\\infty$)",
               xy=(0.0, 60), xytext=(0.18, 95), fontsize=9,
               ha="left", va="center",
               arrowprops=dict(arrowstyle="->", color="0.4"))
ax[0].set_xlabel("upper-layer anchor count  k  (balanced raising)")
ax[0].set_ylabel("self-cal vertical CRLB  (mm, 1$\\sigma$)")
ax[0].set_title("(A) Anchor self-calibration: vertical geometry observability")
ax[0].set_xticks(ks); ax[0].legend(); ax[0].grid(alpha=0.3)

ks2 = [r[0] for r in tagdop]
vd = [r[1] for r in tagdop]; hd = [r[2] for r in tagdop]
ax[1].plot(ks2, vd, "o-", color="C3", lw=2, label="VDOP (vertical)")
ax[1].plot(ks2, hd, "s--", color="C0", lw=1.6, label="HDOP (horizontal)")
ax[1].set_xlabel("upper-layer anchor count  k")
ax[1].set_ylabel("tag DOP (dimensionless)")
ax[1].set_title("(B) Downstream tag positioning DOP")
ax[1].set_xticks(ks2); ax[1].legend(); ax[1].grid(alpha=0.3)

plt.tight_layout()
fig_path = os.path.join(OUT, "vertical_observability_4plus4.png")
plt.savefig(fig_path, dpi=130)
print(f"\nsaved figure: {fig_path}")

# ---- results txt ------------------------------------------------------------
with open(os.path.join(OUT, "vertical_observability_4plus4_results.txt"), "w") as f:
    f.write("Vertical observability vs upper-layer anchor count (Paper A, claim 2)\n")
    f.write(f"footprint {L}x{L} m, y_low={Y_LOW}, y_up={Y_UP}, inter-anchor sigma={SIGMA*1000:.0f} mm\n\n")
    f.write("(A) Anchor self-cal vertical CRLB (inter-anchor ranging only):\n")
    f.write(f"{'k':>3} {'#anch':>5} {'vCRLB_mean_mm':>13} {'vCRLB_worst_mm':>14} {'#sing':>6}\n")
    for (k, na, vm, vw, ns) in selfcal:
        sm = "inf" if not np.isfinite(vm) else f"{vm*1000:.3f}"
        sw = "inf" if not np.isfinite(vw) else f"{vw*1000:.3f}"
        f.write(f"{k:>3} {na:>5} {sm:>13} {sw:>14} {ns:>6}\n")
    f.write("\n(B) Downstream tag VDOP (range-only, sigma=1):\n")
    f.write(f"{'k':>3} {'VDOP':>7} {'HDOP':>7} {'V/H':>6}\n")
    for (k, vm, hm) in tagdop:
        f.write(f"{k:>3} {vm:7.3f} {hm:7.3f} {vm/hm:6.3f}\n")
    f.write("\nBalance vs count at fixed k=2:\n")
    for name, (vm, vdop) in res_bal.items():
        f.write(f"  {name:20s}: self-cal vCRLB_mean = {vm*1000:.3f} mm, tag VDOP = {vdop:.3f}\n")
    f.write(f"  clustering the same count inflates self-cal vertical CRLB {bal_ratio:.3f}x\n")
print("saved results txt")
