#!/usr/bin/env python3
"""make_figures.py — figures for the cell-split simulation. CPU/matplotlib only
(the heavy geometry it plots was computed on GPU by run_sim.py; here we recompute
a few thin maps at the exact requested display heights 0.5/1.0/1.5 m). Read-only."""
import os, json, math
os.environ.setdefault("OMP_NUM_THREADS", "2")
import numpy as np
import torch
torch.set_num_threads(2)
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.colors import ListedColormap, BoundaryNorm
import geo

HERE = os.path.dirname(os.path.abspath(__file__))
FIG = os.path.join(HERE, "figures")
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
DEV = geo.get_device(0)
R = json.load(open(os.path.join(HERE, "results.json")))

LAYOUT = os.path.join(REPO, "logs/system_calibration_20260710_233443/anchor_layout.json")
d = json.load(open(LAYOUT))
ANCH = torch.tensor([[a["x_mm"], a["y_mm"], a["z_mm"]] for a in d["anchors"]],
                    dtype=torch.float32, device=DEV)
LAB = [a["label"] for a in d["anchors"]]
cx, cy = ANCH[:, 0].mean().item(), ANCH[:, 1].mean().item()
BB = R["meta"]["anchor_bbox_mm"]
MARGIN = 200.0  # bbox -> tracking rect (room 500 - wall 300)
DISP_H = [500.0, 1000.0, 1500.0]     # display slices (m*1000)
HSTEP = 100.0
BAND_CMAP = ListedColormap(["#1a9850", "#fee08b", "#d73027"])   # safe / risk / danger
BAND_NORM = BoundaryNorm([0, 25, 37, 90], BAND_CMAP.N)

def base_rect():
    return [BB["x"][0] - MARGIN, BB["x"][1] + MARGIN, BB["y"][0] - MARGIN, BB["y"][1] + MARGIN]

def scale_rect(rect, s):
    x0, x1, y0, y1 = rect
    return [cx + s*(x0-cx), cx + s*(x1-cx), cy + s*(y0-cy), cy + s*(y1-cy)]

def shrink(anch, s):
    A = anch.clone(); A[:, 0] = cx + s*(anch[:, 0]-cx); A[:, 1] = cy + s*(anch[:, 1]-cy)
    return A

def grid_maps(anchors, rect, h_mm):
    x0, x1, y0, y1 = rect
    xs = torch.arange(x0, x1+1e-6, HSTEP, dtype=torch.float32)
    ys = torch.arange(y0, y1+1e-6, HSTEP, dtype=torch.float32)
    gx, gy = torch.meshgrid(xs, ys, indexing="ij")
    P = torch.stack([gx.reshape(-1), gy.reshape(-1),
                     torch.full((gx.numel(),), -h_mm)], 1).to(DEV)
    _, elev, uvec = geo.link_geometry(P, anchors)
    me = elev.max(1).values.reshape(len(xs), len(ys)).cpu().numpy()
    dop = geo.dop_from_uvec(uvec, mask=None, clock=True)
    vd = dop["VDOP"].reshape(len(xs), len(ys)).cpu().numpy()
    return xs.numpy(), ys.numpy(), me, vd

def overlay_anchors(ax, A, s_m=1.0):
    A = A.cpu().numpy()
    for i, a in enumerate(A):
        low = a[2] > -800
        ax.scatter(a[0]/1000, a[1]/1000, marker=("o" if low else "^"),
                   s=70, c="k", edgecolors="w", zorder=5, linewidths=0.8)

# ---- Fig set 1: elevation-band maps per s at 0.5/1.0/1.5 m -----------------
for s in [1.0, 0.7, 0.5, 0.4]:
    A = shrink(ANCH, s); rect = scale_rect(base_rect(), s)
    fig, axs = plt.subplots(1, 3, figsize=(13, 4.2), constrained_layout=True)
    for j, h in enumerate(DISP_H):
        xs, ys, me, _ = grid_maps(A, rect, h)
        ax = axs[j]
        im = ax.pcolormesh(xs/1000, ys/1000, me.T, cmap=BAND_CMAP, norm=BAND_NORM, shading="auto")
        overlay_anchors(ax, A)
        safe = (me <= 25).mean()*100
        ax.set_title(f"h={h/1000:.1f} m   safe≤25°={safe:.0f}%", fontsize=10)
        ax.set_xlabel("x (m)"); ax.set_aspect("equal")
        if j == 0: ax.set_ylabel("y (m)")
    cbar = fig.colorbar(im, ax=axs, ticks=[12.5, 31, 63], shrink=0.8)
    cbar.ax.set_yticklabels(["≤25°\nsafe", "25–37°\nrisk", "≥37°\ndanger"])
    fig.suptitle(f"Max-link elevation bands — Cell-1 shrink s={s:.2f}  "
                 f"(anchors: ●=low ring, ▲=high ring)", fontsize=12)
    fig.savefig(os.path.join(FIG, f"fig_elev_bands_s{int(s*100):03d}.png"), dpi=110)
    plt.close(fig)

# ---- Fig set 2: VDOP maps at 1.0 m for s=1.0 and s=0.5 --------------------
fig, axs = plt.subplots(1, 2, figsize=(10, 4.4), constrained_layout=True)
for k, s in enumerate([1.0, 0.5]):
    A = shrink(ANCH, s); rect = scale_rect(base_rect(), s)
    xs, ys, _, vd = grid_maps(A, rect, 1000.0)
    ax = axs[k]
    im = ax.pcolormesh(xs/1000, ys/1000, np.clip(vd, 0, 3).T, cmap="viridis", shading="auto")
    overlay_anchors(ax, A)
    ax.set_title(f"VDOP @1.0 m — s={s:.2f}  (median={R['task2']['sweep'][0 if s==1 else 3]['median_VDOP']:.2f})", fontsize=10)
    ax.set_xlabel("x (m)"); ax.set_aspect("equal")
    if k == 0: ax.set_ylabel("y (m)")
    fig.colorbar(im, ax=ax, shrink=0.85, label="VDOP (4×4)")
fig.suptitle("Vertical DOP improves as Cell-1 shrinks (z-baseline grows relative to footprint)", fontsize=11)
fig.savefig(os.path.join(FIG, "fig_vdop_maps.png"), dpi=110)
plt.close(fig)

# ---- Fig 3: overlap band map ---------------------------------------------
ov = np.load(os.path.join(HERE, "overlap_map.npz"))
both4 = ov["both4"]; xs = ov["xs"]; ys = ov["ys"]; H = ov["heights"]
A1 = ov["A1"]; A2 = ov["A2"]
hi = int(np.argmin(np.abs(H - 1000)))
fig, ax = plt.subplots(figsize=(8, 5.2), constrained_layout=True)
ax.pcolormesh(xs/1000, ys/1000, both4[:, :, hi].T, cmap=ListedColormap(["#efefef", "#4575b4"]), shading="auto")
for a in A1:
    ax.scatter(a[0]/1000, a[1]/1000, marker=("o" if a[2] > -800 else "^"), s=70,
               c="#b2182b", edgecolors="w", zorder=5, label="_")
for a in A2:
    ax.scatter(a[0]/1000, a[1]/1000, marker=("o" if a[2] > -800 else "^"), s=70,
               c="#1a1a1a", edgecolors="w", zorder=5, label="_")
ax.set_title(f"Overlap band @1.0 m (blue = ≥4 gentle links from BOTH cells)\n"
             f"red=Cell-1 (shrunk s={R['task2']['best_s_used']}), black=Cell-2 ({R['task3']['best_cell2_variant']})  "
             f"joint VDOP={R['task4']['joint_median_VDOP']:.2f}", fontsize=10)
ax.set_xlabel("x (m)"); ax.set_ylabel("y (m)"); ax.set_aspect("equal")
fig.savefig(os.path.join(FIG, "fig_overlap_band.png"), dpi=110)
plt.close(fig)

# ---- Fig 4: s-sweep key table as a plot ----------------------------------
sw = R["task2"]["sweep"]
S = [x["s"] for x in sw]
safe = [x["bands"]["safe"]*100 for x in sw]
danger = [x["bands"]["danger"]*100 for x in sw]
ge4 = [x["frac_ge4_gentle"]*100 for x in sw]
mvd = [x["median_VDOP"] for x in sw]
fig, ax1 = plt.subplots(figsize=(8.5, 5), constrained_layout=True)
ax1.plot(S, safe, "o-", color="#1a9850", label="% vol all-links ≤25° (safe)")
ax1.plot(S, ge4, "s--", color="#4575b4", label="% vol with ≥4 gentle links")
ax1.plot(S, danger, "v-", color="#d73027", label="% vol max-elev ≥37° (danger)")
ax1.axhline(95, ls=":", color="gray", lw=1); ax1.text(0.42, 96, "95% PASS line", color="gray", fontsize=8)
ax1.set_xlabel("shrink ratio s (→ denser Cell-1)"); ax1.set_ylabel("% of Cell-1 tracking volume")
ax1.invert_xaxis(); ax1.set_ylim(-3, 105); ax1.legend(loc="center left", fontsize=8)
ax2 = ax1.twinx()
ax2.plot(S, mvd, "D-", color="#762a83", label="median VDOP")
ax2.set_ylabel("median VDOP (4×4)", color="#762a83"); ax2.tick_params(axis="y", labelcolor="#762a83")
ax2.legend(loc="upper right", fontsize=8)
ax1.set_title("Shrink-ratio sweep: DOP improves but elevation & gentle-link fallback collapse\n"
              "(NO s reaches the 95% safe line — the idea is dead as specified)", fontsize=10)
fig.savefig(os.path.join(FIG, "fig_sweep_keytable.png"), dpi=120)
plt.close(fig)

# ---- Fig 5: z-compression rescue tradeoff --------------------------------
zc = R["task2"]["zcompress_rescue_at_s050"]
zsp = [z["z_span_mm"] for z in zc]
zsafe = [z["frac_safe"]*100 for z in zc]
zvd = [z["median_VDOP"] for z in zc]
fig, ax1 = plt.subplots(figsize=(8, 5), constrained_layout=True)
ax1.plot(zsp, zsafe, "o-", color="#1a9850", label="% safe ≤25°")
ax1.set_xlabel("anchor z-span (mm)  [1779 = current]"); ax1.set_ylabel("% safe volume", color="#1a9850")
ax1.axhline(95, ls=":", color="gray", lw=1); ax1.text(300, 90, "95% PASS line", color="gray", fontsize=8)
ax1.set_ylim(0, 100)
ax2 = ax1.twinx()
ax2.plot(zsp, zvd, "D-", color="#762a83", label="median VDOP")
ax2.set_ylabel("median VDOP", color="#762a83")
ax1.set_title("Height-compression 'rescue' at s=0.5 fails:\nflattening the array never reaches 95% safe and destroys VDOP", fontsize=10)
fig.savefig(os.path.join(FIG, "fig_zcompress_rescue.png"), dpi=120)
plt.close(fig)

# ---- Fig 6: Cell-2 variants comparison -----------------------------------
V = R["task3"]["variants"]
names = list(V.keys()); nA = [V[n]["n_anchors"] for n in names]
safeV = [V[n]["bands"]["safe"]*100 for n in names]
fixV = [V[n]["frac_with_fix"]*100 for n in names]
vdV = [V[n]["median_VDOP"] for n in names]
surv = [V[n]["single_loss"]["model_survivable"] for n in names]
fig, axs = plt.subplots(1, 2, figsize=(11, 4.2), constrained_layout=True)
x = np.arange(len(names))
axs[0].bar(x-0.2, safeV, 0.4, label="% safe ≤25°", color="#1a9850")
axs[0].bar(x+0.2, fixV, 0.4, label="% with a fix (≥4 gentle)", color="#4575b4")
axs[0].set_xticks(x); axs[0].set_xticklabels([f"{n}\n({a} anch)" for n, a in zip(names, nA)])
axs[0].set_ylabel("%"); axs[0].legend(fontsize=8); axs[0].set_title("Coverage")
axs[1].bar(x, vdV, 0.5, color=["#d73027" if not s else "#762a83" for s in surv])
for i, (v, s) in enumerate(zip(vdV, surv)):
    axs[1].text(i, v+0.05, "SURVIVES\nsingle-loss" if s else "DIES\nsingle-loss",
                ha="center", fontsize=7, color=("#1a9850" if s else "#d73027"))
axs[1].set_xticks(x); axs[1].set_xticklabels(names); axs[1].set_ylabel("median VDOP (gentle-only)")
axs[1].set_title("DOP & single-loss survivability")
fig.suptitle("Cell-2 variants: V-A(4) dies on single-anchor loss; V-B(6)/V-C(8) survive", fontsize=11)
fig.savefig(os.path.join(FIG, "fig_cell2_variants.png"), dpi=110)
plt.close(fig)

print("figures written:", sorted(os.listdir(FIG)))
