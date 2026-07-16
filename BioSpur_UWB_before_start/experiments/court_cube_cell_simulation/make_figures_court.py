#!/usr/bin/env python3
"""Figures for the court-cube simulation. CPU/matplotlib only; the two headline
figures (d_close x coverage, VDOP x z_high) are built first. Recomputes thin
spatial maps via the reused geo.py kernels. Read-only."""
import os, sys, json, math
os.environ.setdefault("OMP_NUM_THREADS", "2")
import numpy as np
import torch
torch.set_num_threads(2)
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.colors import ListedColormap, BoundaryNorm

HERE = os.path.dirname(os.path.abspath(__file__))
FIG = os.path.join(HERE, "figures")
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
CELLSPLIT = os.path.join(REPO, "experiments", "cell_split_simulation")
sys.path.insert(0, CELLSPLIT)
import geo
DEV = geo.get_device(0)
R = json.load(open(os.path.join(HERE, "results.json")))
HOME_VDOP = R["d6_killcheck"]["home_room"]["median_VDOP"]

COURTS = {"basketball": (28.0, 15.0), "tennis": (36.6, 18.3)}
Z_LOW, INSET = 0.3, 0.5

def build_anchors(court, z_high, inset=INSET):
    L, W = COURTS[court]; xc = L/2
    py0, py1 = inset, W-inset
    def pole(px):
        return [(px, py0, Z_LOW), (px, py0, z_high), (px, py1, Z_LOW), (px, py1, z_high)]
    A = torch.tensor(pole(inset)+pole(xc), dtype=torch.float32, device=DEV)
    all12 = torch.tensor(pole(inset)+pole(xc)+pole(L-inset), dtype=torch.float32, device=DEV)
    return A, all12, (L, W, xc)

def cellA_map(court, z_high, zt=1.0):
    A, _, (L, W, xc) = build_anchors(court, z_high)
    xs = torch.arange(0, xc+1e-6, 0.25); ys = torch.arange(0, W+1e-6, 0.25)
    gx, gy = torch.meshgrid(xs, ys, indexing="ij")
    P = torch.stack([gx.reshape(-1), gy.reshape(-1), torch.full((gx.numel(),), zt)], 1).to(DEV)
    rng, elev, uvec = geo.link_geometry(P, A)
    me = elev.max(1).values.reshape(len(xs), len(ys)).cpu().numpy()
    dop = geo.dop_from_uvec(uvec, mask=None, clock=True, rank_guard=True)
    vd = dop["VDOP"].reshape(len(xs), len(ys)).cpu().numpy()
    return xs.numpy(), ys.numpy(), me, vd, A.cpu().numpy()

# ============ FIG 1 (HEADLINE): d_close x coverage curve ====================
fig, axs = plt.subplots(1, 2, figsize=(12, 4.8), constrained_layout=True)
for k, court in enumerate(["basketball", "tennis"]):
    ax = axs[k]
    cfgs = {c["z_high"]: c for c in R["courts"][court]["configs"]}
    for zh, style in [(4.0, "--"), (8.0, "-")]:
        c = cfgs[zh]
        dc = [r["d_close"] for r in c["dclose"]]
        feas = [r["cov_ge4_feasible"]*100 for r in c["dclose"]]
        gent = [r["cov_ge4_feas_gentle"]*100 for r in c["dclose"]]
        ax.plot(dc, feas, style, color="#1f77b4", marker="o",
                label=f"≥4 feasible (truss {zh:.0f} m)")
        ax.plot(dc, gent, style, color="#2ca02c", marker="s",
                label=f"≥4 feasible & gentle (truss {zh:.0f} m)")
    ax.axhline(90, ls=":", color="gray"); ax.text(8, 91.5, "90% viable", color="gray", fontsize=8)
    ax.axvspan(8, 12, color="#d73027", alpha=0.08)
    ax.text(9.6, 8, "plausible\nHBS range\n(8–12 m)", color="#d73027", fontsize=8, ha="center")
    ax.set_title(f"{court}  (cell {COURTS[court][0]/2:.0f}×{COURTS[court][1]:.0f} m)", fontsize=11)
    ax.set_xlabel("d_close — assumed link-closure distance (m)  [UNMEASURED]")
    ax.set_ylabel("% of cell volume covered"); ax.set_ylim(-3, 103)
    ax.legend(fontsize=7.5, loc="lower right")
fig.suptitle("D1 — the cube lives or dies on an UNMEASURED HBS link budget\n"
             "(coverage only reaches 90% at d_close 15–20 m; through-body HBS likely gives 8–12 m)",
             fontsize=12)
fig.savefig(os.path.join(FIG, "fig1_dclose_coverage.png"), dpi=120); plt.close(fig)

# ============ FIG 2 (HEADLINE): VDOP x z_high ==============================
fig = plt.figure(figsize=(13, 4.8), constrained_layout=True)
gs = fig.add_gridspec(1, 3, width_ratios=[1.1, 1, 1])
ax0 = fig.add_subplot(gs[0, 0])
for court, col in [("basketball", "#1f77b4"), ("tennis", "#d62728")]:
    zc = [c["z_high"] for c in R["courts"][court]["configs"]]
    vd = [c["pure_median_VDOP"] for c in R["courts"][court]["configs"]]
    ax0.plot(zc, vd, "o-", color=col, label=court)
ax0.axhline(HOME_VDOP, ls="--", color="k", lw=1)
ax0.text(3.1, HOME_VDOP+0.05, f"home-room VDOP={HOME_VDOP:.2f}", fontsize=8)
ax0.axhspan(0, 1.5, color="#1a9850", alpha=0.08)
ax0.set_xlabel("truss height z_high (m)"); ax0.set_ylabel("pure median VDOP (all 8 in-cell)")
ax0.set_title("D2 — VDOP vs truss height", fontsize=10); ax0.legend(fontsize=8)
# spatial VDOP maps at zh=4 (z-dead) vs zh=8 (conditioned), basketball
for j, zh in enumerate([4.0, 8.0]):
    ax = fig.add_subplot(gs[0, 1+j])
    xs, ys, me, vd, A = cellA_map("basketball", zh)
    im = ax.pcolormesh(xs, ys, np.clip(vd, 0, 3).T, cmap="viridis", shading="auto")
    ax.scatter(A[:, 0], A[:, 1], c=["k" if a[2] < 1 else "w" for a in A],
               edgecolors="k", s=45, zorder=5)
    med = [c for c in R["courts"]["basketball"]["configs"] if c["z_high"] == zh][0]["pure_median_VDOP"]
    ax.set_title(f"basketball VDOP @ truss {zh:.0f} m (med {med:.2f})", fontsize=9)
    ax.set_xlabel("x (m)"); ax.set_aspect("equal")
    if j == 0: ax.set_ylabel("y (m)")
    fig.colorbar(im, ax=ax, shrink=0.8, label="VDOP")
fig.suptitle("D2 — a low truss collapses the cube into a z-dead flat array; ≥6–8 m restores vertical DOP", fontsize=12)
fig.savefig(os.path.join(FIG, "fig2_vdop_zhigh.png"), dpi=120); plt.close(fig)

# ============ FIG 3: D3 elevation bands (did the small-room failure move?) ==
BAND_CMAP = ListedColormap(["#1a9850", "#fee08b", "#d73027"])
BAND_NORM = BoundaryNorm([0, 25, 37, 90], BAND_CMAP.N)
fig, axs = plt.subplots(1, 2, figsize=(11, 4.6), constrained_layout=True)
for k, zh in enumerate([4.0, 8.0]):
    ax = axs[k]
    xs, ys, me, vd, A = cellA_map("basketball", zh, zt=1.0)
    im = ax.pcolormesh(xs, ys, me.T, cmap=BAND_CMAP, norm=BAND_NORM, shading="auto")
    ax.scatter(A[:, 0], A[:, 1], c=["#111" if a[2] < 1 else "#fff" for a in A],
               edgecolors="k", s=55, zorder=5)
    d3 = [c for c in R["courts"]["basketball"]["configs"] if c["z_high"] == zh][0]["d3_nearpole"]
    ax.set_title(f"truss {zh:.0f} m  ·  strict-safe {d3['frac_strict_safe_allgentle']*100:.0f}%  ·  "
                 f"any≥37° {d3['frac_any_steep37']*100:.0f}%", fontsize=9)
    ax.set_xlabel("x (m)"); ax.set_aspect("equal")
    if k == 0: ax.set_ylabel("y (m)")
cbar = fig.colorbar(im, ax=axs, ticks=[12.5, 31, 63], shrink=0.8)
cbar.ax.set_yticklabels(["≤25°\nsafe", "25–37°\nrisk", "≥37°\ndanger"])
fig.suptitle("D3 — the small-room failure MOVED to the poles: a tall truss makes near-pole\n"
             "high-anchor links Layer-2 steep (basketball, tag @1.0 m; ●=low ○=high anchor)", fontsize=11)
fig.savefig(os.path.join(FIG, "fig3_elevation_bands.png"), dpi=120); plt.close(fig)

# ============ FIG 4: D4 overlap band vs d_close ============================
fig, ax = plt.subplots(figsize=(8, 5), constrained_layout=True)
for court, col in [("basketball", "#1f77b4"), ("tennis", "#d62728")]:
    ov = [o for o in R["courts"][court]["overlap"] if o["z_high"] == 8.0][0]
    dcs = sorted(float(k) for k in ov["by_dclose"])
    widths = []
    for dc in dcs:
        key = [k for k in ov["by_dclose"] if abs(float(k) - dc) < 1e-6][0]
        widths.append(ov["by_dclose"][key]["overlap_band_width_m_midline"])
    ax.plot(dcs, widths, "o-", color=col, label=f"{court} overlap width")
ax.axhline(0.6, ls=":", color="gray"); ax.text(12, 1.2, "0.6 m walker needs", color="gray", fontsize=8)
ax.set_xlabel("d_close (m)"); ax.set_ylabel("overlap band width at midline (m)")
ax.set_title("D4 — overlap/handover band (truss 8 m): a genuine sweet-spot,\nbut only once d_close is large enough to reach both cells", fontsize=10)
ax.legend(fontsize=9)
fig.savefig(os.path.join(FIG, "fig4_overlap_band.png"), dpi=120); plt.close(fig)

print("figures:", sorted(os.listdir(FIG)))
