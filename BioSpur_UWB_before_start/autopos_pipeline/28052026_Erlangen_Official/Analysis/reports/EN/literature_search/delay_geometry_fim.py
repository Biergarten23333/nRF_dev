"""
Fisher / observability analysis of the joint anchor self-calibration.

Goal: quantify, from the real Erlangen geometry, the near-degeneracy between
common-mode antenna delay and the vertical layout mode. This turns "we also see
the GPS clock<->altitude coupling" into numbers:
  - smallest non-gauge FIM eigenvalue + condition number
  - the soft eigenvector's overlap with {common-delay, vertical-stretch, horiz-stretch}
  - correlation rho_{b,z}, vertical variance inflation 1/(1-rho^2), alias gain gamma=dz/db

Model:  r = ||a_i - x|| + b_i + eps   (b_i = anchor antenna delay, range-equiv mm)
Params: theta = [anchors(8x3=24), anchor_delays(8), tag_positions(3 per frame)]
Vertical axis = Y = column 1 (matches eval_lib).
"""
import sys
SCR = "/tmp/claude-1000/-home-zekaixiao-Documents-nRF-dev-BioSpur-UWB-before-start/bf909886-ad48-495f-b091-a809435e28fc/scratchpad"
sys.argv = ["fim", SCR]
import numpy as np
from collections import defaultdict
import harness as H

np.set_printoptions(precision=3, suppress=True)
A = H.TRUTH.astype(float)              # 8x3 ground-truth anchors (Vicon), vertical=col1
NA = 8
SIG_INTER, SIG_TAG = 30.0, 80.0        # mm, same as solver cfg

# ---- measurements -----------------------------------------------------------
INTER = [(d["i"], d["j"], d["range_mm"]) for d in H.INTER]

rows = []
for n in [f"static_ID{k:02d}" for k in range(1, 25)]:
    d = H.cap_dir(n)
    if d:
        rows += H.load_cap(d, n, 5)
frames = defaultdict(list)
for r in rows:
    frames[(r["source"], r["tag"], r["sweep"])].append((r["anchor"], r["range_mm"]))
frames = {k: v for k, v in frames.items() if len(v) >= 4}   # need >=4 anchors
fkeys = list(frames)
F = len(fkeys)
print(f"inter-anchor pairs={len(INTER)}  static tag frames (>=4 anchors)={F}")

# ---- trilaterate each frame against truth anchors (geometry init) -----------
def trilaterate(meas):
    p = A.mean(0).copy()
    for _ in range(30):
        Jr, res = [], []
        for aid, rng in meas:
            d = p - A[aid]; nd = np.linalg.norm(d)
            if nd < 1e-6: nd = 1e-6
            u = d / nd
            Jr.append(u); res.append(nd - rng)
        Jr = np.array(Jr); res = np.array(res)
        try:
            dp = np.linalg.lstsq(Jr, -res, rcond=None)[0]
        except Exception:
            break
        p = p + dp
        if np.linalg.norm(dp) < 1e-4: break
    return p
TAGP = np.array([trilaterate(frames[k]) for k in fkeys])

# ---- build weighted Jacobian rows -------------------------------------------
# theta layout: anchors 0..23, delays 24..31, tags 32..32+3F-1
NS = 24 + NA               # structural params (anchors+delays) = 32
NT = 3 * F
NP = NS + NT
rowsJ = []
def aidx(i): return slice(3 * i, 3 * i + 3)
def didx(i): return 24 + i
def tidx(f): return slice(NS + 3 * f, NS + 3 * f + 3)

# inter-anchor: ||a_i-a_j|| + b_i + b_j
for (i, j, rng) in INTER:
    g = np.zeros(NP)
    d = A[i] - A[j]; nd = np.linalg.norm(d); u = d / nd
    g[aidx(i)] += u; g[aidx(j)] -= u
    g[didx(i)] += 1.0; g[didx(j)] += 1.0
    rowsJ.append(g / SIG_INTER)

# tag-anchor: ||a_i - x_f|| + b_i
for f, k in enumerate(fkeys):
    for (aid, rng) in frames[k]:
        g = np.zeros(NP)
        d = A[aid] - TAGP[f]; nd = np.linalg.norm(d); u = d / nd
        g[aidx(aid)] += u; g[tidx(f)] -= u
        g[didx(aid)] += 1.0
        rowsJ.append(g / SIG_TAG)

J = np.array(rowsJ)
Fim = J.T @ J
print(f"design: measurements={J.shape[0]}  params={NP}  (structural={NS}, tags={NT})")

# ---- marginalize tag positions (Schur complement) ---------------------------
s = np.arange(NS); t = np.arange(NS, NP)
Fss = Fim[np.ix_(s, s)]; Fst = Fim[np.ix_(s, t)]; Ftt = Fim[np.ix_(t, t)]
# Ftt is block-diagonal (3x3 per frame): invert per block for stability
Ftt_inv = np.zeros_like(Ftt)
for f in range(F):
    b = slice(3 * f, 3 * f + 3)
    Ftt_inv[b, b] = np.linalg.inv(Ftt[b, b] + 1e-9 * np.eye(3))
Fred = Fss - Fst @ Ftt_inv @ Fst.T      # 32x32 information on {anchors, delays}

# ---- gauge: remove anchor SE(3) (3 translations + 3 rotations) --------------
c = A.mean(0)
G = np.zeros((NS, 6))
for i in range(NA):
    r = A[i] - c
    G[aidx(i), 0] = [1, 0, 0]; G[aidx(i), 1] = [0, 1, 0]; G[aidx(i), 2] = [0, 0, 1]
    G[aidx(i), 3] = [0, -r[2], r[1]]    # rot x
    G[aidx(i), 4] = [r[2], 0, -r[0]]    # rot y
    G[aidx(i), 5] = [-r[1], r[0], 0]    # rot z
Gq, _ = np.linalg.qr(G)                 # orthonormal gauge basis (32x6)
# complement basis Q (32 x 26)
U, sv, _ = np.linalg.svd(Gq @ Gq.T)
Q = U[:, 6:]                            # columns orthogonal to gauge
Fq = Q.T @ Fred @ Q                     # 26x26, gauge-free information
Fq = 0.5 * (Fq + Fq.T)
evals, evecs = np.linalg.eigh(Fq)
lam_min, lam_max = evals[0], evals[-1]
cond = lam_max / lam_min
print(f"\n=== gauge-free FIM spectrum (26 modes) ===")
print(f"lambda_min = {lam_min:.4e}   lambda_max = {lam_max:.4e}   condition kappa = {cond:.3e}")
print(f"5 smallest eigenvalues: {evals[:5]}")

# ---- interpretable probe directions (in 32-d structural space) --------------
def probe(vec):
    v = vec.astype(float)
    v = v - Gq @ (Gq.T @ v)             # remove gauge component
    n = np.linalg.norm(v)
    return v / n if n > 0 else v
e_b = np.zeros(NS); e_b[24:32] = 1.0;                         e_b = probe(e_b)   # common delay
e_vz = np.zeros(NS)
e_hx = np.zeros(NS)
e_iso = np.zeros(NS)
for i in range(NA):
    r = A[i] - c
    e_vz[aidx(i)] = [0, r[1], 0]        # vertical stretch (changes layer separation)
    e_hx[aidx(i)] = [r[0], 0, r[2]]     # horizontal stretch
    e_iso[aidx(i)] = r                  # isotropic scale
e_vz, e_hx, e_iso = probe(e_vz), probe(e_hx), probe(e_iso)

v_min = Q @ evecs[:, 0]                 # softest mode in 32-d structural space
def cosang(a, b): return abs(float(a @ b) / (np.linalg.norm(a) * np.linalg.norm(b)))
print(f"\n=== softest mode v_min overlap (|cos|) with interpretable directions ===")
print(f"  common-delay      : {cosang(v_min, e_b):.3f}")
print(f"  vertical-stretch  : {cosang(v_min, e_vz):.3f}")
print(f"  horizontal-stretch: {cosang(v_min, e_hx):.3f}")
print(f"  isotropic-scale   : {cosang(v_min, e_iso):.3f}")

# ---- covariance & coupling scalars ------------------------------------------
Cq = np.linalg.inv(Fq)                  # gauge-free covariance (26x26)
def cov2(ea, eb):
    qa, qb = Q.T @ ea, Q.T @ eb
    return np.array([[qa @ Cq @ qa, qa @ Cq @ qb],
                     [qb @ Cq @ qa, qb @ Cq @ qb]])

def couple(name, e_geo):
    C2 = cov2(e_b, e_geo)               # [[var_b, cov],[cov, var_geo]]
    rho = C2[0, 1] / np.sqrt(C2[0, 0] * C2[1, 1])
    infl = 1.0 / (1.0 - rho ** 2)
    M = np.linalg.inv(C2)               # information; alias slope d(geo)/d(b)
    gamma = -M[1, 0] / M[1, 1]
    print(f"  [{name:18s}] rho={rho:+.3f}  var_inflation 1/(1-rho^2)={infl:6.2f}x  "
          f"alias_gain d(geo)/d(b)={gamma:+.3f}")
    return rho, infl, gamma

print(f"\n=== delay <-> geometry coupling (marginal 2x2) ===")
couple("delay<->vertical", e_vz)
couple("delay<->horizontal", e_hx)
couple("delay<->iso-scale", e_iso)

# ---- DOP-style: variance ratio of vertical vs horizontal stretch ------------
qv, qh = Q.T @ e_vz, Q.T @ e_hx
var_v = qv @ Cq @ qv; var_h = qh @ Cq @ qh
print(f"\n=== anisotropy (DOP analog) ===")
print(f"  var(vertical-stretch)/var(horizontal-stretch) = {var_v/var_h:.2f}x   "
      f"(sqrt = {np.sqrt(var_v/var_h):.2f}x worse vertical)")
