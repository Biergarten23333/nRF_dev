"""
Paper B observability proof: common-mode antenna delay <-> layout scale near-degeneracy,
from the real Erlangen 8-anchor geometry.

Turns "UWB has the GPS clock<->scale coupling" into a Fisher/profile-likelihood statement:

  (1) Soft-spectrum attribution: which layout modes the data leaves weakly constrained, and
      an honest note that the single softest raw mode is a thin-two-layer wiggle artifact, not
      the delay-scale coupling.
  (2) Delay-direction marginal coupling: correlation rho, variance inflation 1/(1-rho^2), and
      alias gain d(geometry)/d(delay), for delay vs {iso-scale, vertical, horizontal}.
  (3) Profile-likelihood statement: profiling the whole layout out of the common-delay
      parameter flattens its likelihood by exactly the variance-inflation factor
      (F_bb,conditional / F_bb,profiled = 1/(1 - rho^2)).
  (4) A physical-units 2D cost valley in (common-delay mm, isotropic scale) -> the visual of
      the near-degeneracy and the alias slope "1 mm delay ~ X mm of array-scale".

Model: r = ||a_i - a_j|| + b_i + b_j (+ tag frames) + noise.  Vertical = Y = col 1.
Depends on harness.py / eval_lib.py (real-data loaders) sitting next to this file.
"""
import sys, os
HERE = os.path.dirname(os.path.abspath(__file__))
sys.argv = ["x", HERE]
import numpy as np
from collections import defaultdict
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import harness as H

np.set_printoptions(precision=3, suppress=True)
A = H.TRUTH.astype(float)
NA = 8
SIG_INTER, SIG_TAG = 30.0, 80.0
c = A.mean(0)
VY = 1                                    # vertical axis = Y
y = A[:, VY]
upper = y > np.median(y)                   # layer membership

INTER = [(d["i"], d["j"], d["range_mm"]) for d in H.INTER]
rows = []
for n in [f"static_ID{k:02d}" for k in range(1, 25)]:
    d = H.cap_dir(n)
    if d:
        rows += H.load_cap(d, n, 5)
frames = defaultdict(list)
for r in rows:
    frames[(r["source"], r["tag"], r["sweep"])].append((r["anchor"], r["range_mm"]))
frames = {k: v for k, v in frames.items() if len(v) >= 4}
fkeys = list(frames)
F = len(fkeys)

def trilaterate(meas):
    p = A.mean(0).copy()
    for _ in range(40):
        J, res = [], []
        for aid, rng in meas:
            d = p - A[aid]; nd = max(np.linalg.norm(d), 1e-6); u = d / nd
            J.append(u); res.append(nd - rng)
        dp = np.linalg.lstsq(np.array(J), -np.array(res), rcond=None)[0]
        p = p + dp
        if np.linalg.norm(dp) < 1e-4: break
    return p
TAGP = np.array([trilaterate(frames[k]) for k in fkeys])

# ---- joint FIM over {anchors(24), delays(8), tags(3F)} ----------------------
NS = 24 + NA
NP = NS + 3 * F
def aidx(i): return slice(3 * i, 3 * i + 3)
def didx(i): return 24 + i
def tidx(f): return slice(NS + 3 * f, NS + 3 * f + 3)
rJ = []
for (i, j, _) in INTER:
    g = np.zeros(NP); d = A[i] - A[j]; u = d / np.linalg.norm(d)
    g[aidx(i)] += u; g[aidx(j)] -= u; g[didx(i)] += 1; g[didx(j)] += 1
    rJ.append(g / SIG_INTER)
for f, k in enumerate(fkeys):
    for (aid, _) in frames[k]:
        g = np.zeros(NP); d = A[aid] - TAGP[f]; u = d / np.linalg.norm(d)
        g[aidx(aid)] += u; g[tidx(f)] -= u; g[didx(aid)] += 1
        rJ.append(g / SIG_TAG)
J = np.array(rJ)
Fim = J.T @ J
print(f"inter pairs={len(INTER)}  tag frames={F}  measurements={J.shape[0]}  params={NP}")

# Schur-marginalize tag positions -> 32x32 information on {anchors, delays}
s = np.arange(NS); t = np.arange(NS, NP)
Fss, Fst, Ftt = Fim[np.ix_(s, s)], Fim[np.ix_(s, t)], Fim[np.ix_(t, t)]
Ftt_inv = np.zeros_like(Ftt)
for f in range(F):
    b = slice(3 * f, 3 * f + 3)
    Ftt_inv[b, b] = np.linalg.inv(Ftt[b, b] + 1e-9 * np.eye(3))
Fred = Fss - Fst @ Ftt_inv @ Fst.T

# ---- gauge removal: SE(3) (3 translation + 3 rotation) ----------------------
G = np.zeros((NS, 6))
for i in range(NA):
    r = A[i] - c
    G[aidx(i), 0] = [1, 0, 0]; G[aidx(i), 1] = [0, 1, 0]; G[aidx(i), 2] = [0, 0, 1]
    G[aidx(i), 3] = [0, -r[2], r[1]]; G[aidx(i), 4] = [r[2], 0, -r[0]]; G[aidx(i), 5] = [-r[1], r[0], 0]
Gq, _ = np.linalg.qr(G)
U, _, _ = np.linalg.svd(Gq @ Gq.T)
Q = U[:, 6:]
Fq = 0.5 * (Q.T @ Fred @ Q + (Q.T @ Fred @ Q).T)
evals, evecs = np.linalg.eigh(Fq)
print(f"\ngauge-free spectrum (26 modes): lambda_min={evals[0]:.3e} lambda_max={evals[-1]:.3e} "
      f"cond={evals[-1]/evals[0]:.1f}")
print(f"5 softest eigenvalues: {evals[:5]}")

# ---- interpretable probe directions -----------------------------------------
def probe(v):
    v = v.astype(float); v = v - Gq @ (Gq.T @ v); n = np.linalg.norm(v)
    return v / n if n else v
def mk(fy=None):
    e = np.zeros(NS)
    return e
e_b = np.zeros(NS); e_b[24:32] = 1.0; e_b = probe(e_b)                # common delay
e_iso = np.zeros(NS); e_vz = np.zeros(NS); e_hx = np.zeros(NS)
e_sep = np.zeros(NS); e_ltlt = np.zeros(NS)
for i in range(NA):
    r = A[i] - c
    e_iso[aidx(i)] = r                                                 # isotropic scale
    e_vz[aidx(i)] = [0, r[1], 0]                                       # vertical stretch
    e_hx[aidx(i)] = [r[0], 0, r[2]]                                    # horizontal stretch
    e_sep[aidx(i)] = [0, 1.0 if upper[i] else -1.0, 0]                 # layer separation
    e_ltlt[aidx(i)] = [0, (r[0]) * (1 if upper[i] else -1), 0]         # differential layer tilt
e_iso, e_vz, e_hx, e_sep, e_ltlt = map(probe, [e_iso, e_vz, e_hx, e_sep, e_ltlt])
PROBES = {"common-delay": e_b, "iso-scale": e_iso, "vertical-stretch": e_vz,
          "horizontal-stretch": e_hx, "layer-separation": e_sep, "differential-layer-tilt": e_ltlt}

def cosang(a, b): return abs(float(a @ b) / (np.linalg.norm(a) * np.linalg.norm(b)))
print("\n=== soft-mode attribution: |cos| of each soft FIM mode with interpretable directions ===")
hdr = "mode  eigval   " + "  ".join(f"{k[:11]:>11}" for k in PROBES)
print(hdr)
for m in range(5):
    vm = Q @ evecs[:, m]
    cs = "  ".join(f"{cosang(vm, e):11.3f}" for e in PROBES.values())
    print(f"  v{m}  {evals[m]:.2e}  {cs}")
print("NOTE: the softest raw mode is a thin-two-layer wiggle (differential-layer-tilt / "
      "separation), NOT the delay-scale coupling. The delay-scale coupling is read off the "
      "delay DIRECTION's marginal below, which is the relevant quantity.")

# ---- delay-direction marginal coupling (the headline) -----------------------
Cq = np.linalg.inv(Fq)
def couple(name, e_geo):
    qa, qb = Q.T @ e_b, Q.T @ e_geo
    C2 = np.array([[qa @ Cq @ qa, qa @ Cq @ qb], [qb @ Cq @ qa, qb @ Cq @ qb]])
    rho = C2[0, 1] / np.sqrt(C2[0, 0] * C2[1, 1])
    infl = 1.0 / (1.0 - rho ** 2)
    M = np.linalg.inv(C2); gamma = -M[1, 0] / M[1, 1]
    print(f"  [delay <-> {name:12s}] rho={rho:+.3f}  inflation={infl:6.2f}x  alias d(geo)/d(delay)={gamma:+.3f}")
    return rho, infl, gamma
print("\n=== delay-direction marginal coupling (profiling proof) ===")
rho_iso, infl_iso, _ = couple("iso-scale", e_iso)
rho_h, infl_h, _ = couple("horizontal", e_hx)
rho_v, infl_v, _ = couple("vertical", e_vz)
print(f"\nPROFILE-LIKELIHOOD: profiling the full layout out of the common-delay parameter flattens")
print(f"its likelihood by the variance-inflation factor. delay<->iso-scale: {infl_iso:.1f}x flatter")
print(f"(conditional curvature F_bb is {infl_iso:.1f}x the profiled curvature 1/Cov_bb).")

# ---- physical-units 2D cost valley: BOTH axes in mm at the array edge -------
arm = np.max(np.linalg.norm(A - c, axis=1))                           # array radius (mm)
v_b_raw = np.zeros(NS); v_b_raw[24:32] = 1.0                          # +1 mm delay on every anchor
v_s_raw = np.zeros(NS)
for i in range(NA):
    v_s_raw[aidx(i)] = (A[i] - c) / arm                              # +1 mm scale AT THE EDGE
Vb = v_b_raw - Gq @ (Gq.T @ v_b_raw)
Vs = v_s_raw - Gq @ (Gq.T @ v_s_raw)
Vm = np.column_stack([Vb, Vs])
M2 = Vm.T @ Fred @ Vm                                                  # 2x2 info, BOTH axes in mm
w2, U2 = np.linalg.eigh(M2)
slope_mm_per_mm = -M2[0, 1] / M2[1, 1]                                 # d(edge-scale mm)/d(delay mm)
elong = np.sqrt(w2[1] / w2[0])
print(f"\n=== physical alias (2D valley, both axes mm at array edge) ===")
print(f"array radius ~ {arm:.0f} mm; valley elongation sqrt(cond 2x2) = {elong:.1f}:1")
print(f"alias slope: 1 mm common delay  ->  {slope_mm_per_mm:+.3f} mm edge-scale "
      f"({slope_mm_per_mm/arm*1e6:+.0f} ppm)")

dd = np.linspace(-60, 60, 161)                                        # delay mm
ee = np.linspace(-60, 60, 161)                                       # edge-scale mm
Dg, Eg = np.meshgrid(dd, ee)
COST = 0.5 * (M2[0, 0] * Dg ** 2 + 2 * M2[0, 1] * Dg * Eg + M2[1, 1] * Eg ** 2)
fig, ax = plt.subplots(figsize=(6.4, 5.3))
cs = ax.contourf(Dg, Eg, COST, levels=30, cmap="viridis")
ax.contour(Dg, Eg, COST, levels=np.percentile(COST, [1, 4, 10, 22, 40, 65, 90]),
           colors="w", linewidths=0.6, alpha=0.6)
tline = np.linspace(-60, 60, 2)
ax.plot(tline, tline * slope_mm_per_mm, "r--", lw=2,
        label=f"alias valley: 1 mm delay $\\to$ {slope_mm_per_mm:+.2f} mm edge-scale")
ax.set_xlim(-60, 60); ax.set_ylim(-60, 60)
ax.set_xlabel("common-mode antenna-delay offset  (mm, range-equiv)")
ax.set_ylabel("isotropic layout scale change  (mm at array edge)")
ax.set_title("Profile-likelihood valley: delay $\\leftrightarrow$ scale near-degeneracy\n"
             f"(real Erlangen geometry; $\\rho={rho_iso:.3f}$, valley {elong:.1f}:1)")
ax.legend(loc="upper right", fontsize=9)
plt.colorbar(cs, label="negative log-likelihood (a.u.)")
plt.tight_layout()
figp = os.path.join(HERE, "delay_scale_valley.png")
plt.savefig(figp, dpi=130)
print(f"\nsaved figure: {figp}")

# ---- results txt ------------------------------------------------------------
with open(os.path.join(HERE, "delay_scale_observability_results.txt"), "w") as f:
    f.write("Common-mode antenna delay <-> layout scale near-degeneracy (real Erlangen geometry)\n\n")
    f.write(f"gauge-free FIM (26 modes): lambda_min={evals[0]:.3e} lambda_max={evals[-1]:.3e} "
            f"cond={evals[-1]/evals[0]:.1f}\n")
    f.write(f"5 softest eigenvalues: {np.array2string(evals[:5], precision=3)}\n\n")
    f.write("soft-mode attribution (|cos| with interpretable directions):\n")
    f.write(hdr + "\n")
    for m in range(5):
        vm = Q @ evecs[:, m]
        f.write(f"  v{m}  {evals[m]:.2e}  " + "  ".join(f"{cosang(vm, e):11.3f}" for e in PROBES.values()) + "\n")
    f.write("\ndelay-direction marginal coupling:\n")
    f.write(f"  delay<->iso-scale : rho={rho_iso:+.3f} inflation={infl_iso:.2f}x\n")
    f.write(f"  delay<->horizontal: rho={rho_h:+.3f} inflation={infl_h:.2f}x\n")
    f.write(f"  delay<->vertical  : rho={rho_v:+.3f} inflation={infl_v:.2f}x\n")
    f.write(f"\nphysical alias (both axes mm at array edge, radius {arm:.0f} mm):\n")
    f.write(f"  1 mm common delay -> {slope_mm_per_mm:+.3f} mm edge-scale "
            f"({slope_mm_per_mm/arm*1e6:+.0f} ppm)\n")
    f.write(f"  valley elongation sqrt(cond 2x2) = {elong:.1f}:1\n")
    f.write("\nProfile-likelihood: profiling the full layout out of the common-delay parameter\n")
    f.write(f"flattens its likelihood by the variance-inflation factor ({infl_iso:.1f}x for iso-scale).\n")
print("saved results txt")
