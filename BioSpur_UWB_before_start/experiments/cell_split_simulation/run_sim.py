#!/usr/bin/env python3
"""
run_sim.py — Cell-Split Layout Simulation (shrink ratio x elevation x DOP).

PURE SIMULATION. Read-only on all repo data. GPU-first (torch/cuda, FP32),
CPU capped at 2 threads. Writes everything under experiments/cell_split_simulation/.

Tasks:
  1  Baseline (current 8-anchor layout) elevation + DOP over full room.
  2  Shrink-ratio sweep (Cell-1) s in {1.0,.85,.7,.6,.5,.4} + pass/fail.
  3  Cell-2 variants V-A(4)/V-B(6)/V-C(8) + single-loss + hardware ledger.
  4  Overlap / handover geometry.
  5  Timing arithmetic (pure).
Outputs: results.json + figures/*.png (figures made by make_figures.py).
"""
import os, sys, json, time, math
os.environ.setdefault("OMP_NUM_THREADS", "2")
os.environ.setdefault("MKL_NUM_THREADS", "2")
import torch
torch.set_num_threads(2)
import geo

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
LAYOUT = os.path.join(REPO, "logs/system_calibration_20260710_233443/anchor_layout.json")
LAYOUT_V5SL = os.path.join(REPO, "logs/system_calibration_20260710_233443/anchor_layout_v5_scalelock.json")

DEV = geo.get_device(0)
torch.manual_seed(1234)

# ------------------------------------------------------------------ constants
MARGIN_ROOM_MM = 500.0      # anchor bbox -> room bounds (ASSUMED; no room survey in repo)
MARGIN_WALL_MM = 300.0      # room -> tracking volume wall margin (prompt)
HEIGHTS_MM = [200, 400, 600, 800, 1000, 1200, 1400, 1600, 1800]  # 0.2..1.8 m
HSTEP_MM = 100.0            # 0.1 m horizontal grid
SHRINK_S = [1.0, 0.85, 0.7, 0.6, 0.5, 0.4]
SAFE_DEG, DANGER_DEG = 25.0, 37.0

# ------------------------------------------------------------------ load layout
def load_anchors(path):
    d = json.load(open(path))
    ids = [a["label"] for a in d["anchors"]]
    P = torch.tensor([[a["x_mm"], a["y_mm"], a["z_mm"]] for a in d["anchors"]],
                     dtype=torch.float32, device=DEV)
    return ids, P, d

IDS, ANCH, LAYOUT_JSON = load_anchors(LAYOUT)
CENTROID = ANCH[:, :2].mean(dim=0)                       # xy centroid (mm)
Z_LOW = ANCH[ANCH[:, 2] > -800, 2].mean().item()        # low-ring mean z
Z_HIGH = ANCH[ANCH[:, 2] <= -800, 2].mean().item()      # high-ring mean z
BBOX = dict(x=(ANCH[:, 0].min().item(), ANCH[:, 0].max().item()),
            y=(ANCH[:, 1].min().item(), ANCH[:, 1].max().item()),
            z=(ANCH[:, 2].min().item(), ANCH[:, 2].max().item()))

# tracking rectangle = anchor bbox + (room margin - wall margin) = bbox + 0.2 m
def base_rect():
    m = MARGIN_ROOM_MM - MARGIN_WALL_MM
    return [BBOX["x"][0] - m, BBOX["x"][1] + m, BBOX["y"][0] - m, BBOX["y"][1] + m]

def scale_rect(rect, s, cx, cy):
    x0, x1, y0, y1 = rect
    return [cx + s * (x0 - cx), cx + s * (x1 - cx),
            cy + s * (y0 - cy), cy + s * (y1 - cy)]

# ------------------------------------------------------------------ evaluators
def eval_layout(anchors, rect, elev_gate=None, want_maps=False, clock=True):
    """Full elevation + DOP evaluation of `anchors` over the tracking box `rect`
    at HEIGHTS_MM. elev_gate: if set, DOP uses only links with elev<=gate
    (mask); else all anchors. Returns summary dict (+ per-slice maps if asked)."""
    x0, x1, y0, y1 = rect
    P, nx, ny, nz, xs, ys = geo.make_grid(x0, x1, y0, y1, HEIGHTS_MM, HSTEP_MM, DEV)
    rng, elev, uvec = geo.link_geometry(P, anchors)
    max_elev = elev.max(dim=1).values                    # [N]
    n_steep = (elev >= DANGER_DEG).sum(dim=1)            # links >=37 deg
    n_gentle = (elev <= SAFE_DEG).sum(dim=1)            # links <=25 deg
    mask = (elev <= elev_gate) if elev_gate is not None else None
    dop = geo.dop_from_uvec(uvec, mask=mask, clock=clock)
    # secondary: fix built ONLY from gentle (<=25deg) links, if >=4 exist
    dop_gentle = geo.dop_from_uvec(uvec, mask=(elev <= SAFE_DEG), clock=clock)
    bands = geo.band_fractions(max_elev)
    summ = dict(
        n_points=int(P.shape[0]),
        rect_mm=[round(v, 1) for v in rect],
        bands=bands,
        median_VDOP=geo.nanmedian(dop["VDOP"]), p95_VDOP=geo.pct(dop["VDOP"], 0.95),
        median_HDOP=geo.nanmedian(dop["HDOP"]), p95_HDOP=geo.pct(dop["HDOP"], 0.95),
        median_GDOP=geo.nanmedian(dop["GDOP"]), p95_GDOP=geo.pct(dop["GDOP"], 0.95),
        median_max_elev=geo.nanmedian(max_elev), p95_max_elev=geo.pct(max_elev, 0.95),
        mean_n_steep=float(n_steep.float().mean().item()),
        frac_with_fix=float(torch.isfinite(dop["VDOP"]).float().mean().item()),
        # looser, practical criterion: >=4 GENTLE (<=25deg) links => can solve using
        # only safe links (steep links can be gated out to dodge wrong-path lock)
        frac_ge4_gentle=float((n_gentle >= 4).float().mean().item()),
        median_VDOP_gentle_only=geo.nanmedian(dop_gentle["VDOP"]),
        p95_VDOP_gentle_only=geo.pct(dop_gentle["VDOP"], 0.95),
    )
    if want_maps:
        shape = (nx, ny, nz)
        summ["_maps"] = dict(
            xs=xs.cpu().numpy(), ys=ys.cpu().numpy(), heights=HEIGHTS_MM,
            max_elev=max_elev.reshape(shape).cpu().numpy(),
            VDOP=dop["VDOP"].reshape(shape).cpu().numpy(),
            HDOP=dop["HDOP"].reshape(shape).cpu().numpy(),
        )
    return summ

# ------------------------------------------------------------------ Task 3 helpers
def perimeter_samples(rect, n_per_edge=9):
    """Candidate anchor xy positions along the 4 walls of `rect` (mm)."""
    x0, x1, y0, y1 = rect
    pts = []
    for t in torch.linspace(0, 1, n_per_edge):
        t = t.item()
        pts += [(x0 + t * (x1 - x0), y0), (x0 + t * (x1 - x0), y1),
                (x0, y0 + t * (y1 - y0)), (x1, y0 + t * (y1 - y0))]
    # dedup
    uniq = sorted(set((round(a, 1), round(b, 1)) for a, b in pts))
    return torch.tensor(uniq, dtype=torch.float32, device=DEV)

def optimize_cell2(perim_xy, track_rect, n_low, n_high, restarts=1200, seed=7):
    """Coarse random-restart placement of (n_low+n_high) anchors on `perim_xy`
    at the two ring heights. Objective: maximise %safe(<=25) then minimise
    median VDOP over `track_rect`. Returns (best_anchors[K,3], best_summary)."""
    g = torch.Generator(device="cpu").manual_seed(seed)
    K = n_low + n_high
    Np = perim_xy.shape[0]
    x0, x1, y0, y1 = track_rect
    P, *_ = geo.make_grid(x0, x1, y0, y1, HEIGHTS_MM, HSTEP_MM, DEV)
    best = None
    for r in range(restarts):
        idx = torch.randperm(Np, generator=g)[:K]
        xy = perim_xy[idx]
        zs = torch.tensor([Z_LOW] * n_low + [Z_HIGH] * n_high,
                          dtype=torch.float32, device=DEV)
        A = torch.cat([xy, zs[:, None]], dim=1)          # [K,3]
        rng, elev, uvec = geo.link_geometry(P, A)
        max_elev = elev.max(dim=1).values
        safe = (max_elev <= SAFE_DEG).float().mean().item()
        mask = elev <= SAFE_DEG
        dop = geo.dop_from_uvec(uvec, mask=mask, clock=True)
        mvd = geo.nanmedian(dop["VDOP"])
        mvd = mvd if math.isfinite(mvd) else 1e9
        score = (round(safe, 4), -mvd)                    # maximise safe, then min VDOP
        if best is None or score > best[0]:
            best = (score, A.clone(), safe, mvd)
    A = best[1]
    summ = eval_layout(A, track_rect, elev_gate=SAFE_DEG, clock=True)
    summ_all = eval_layout(A, track_rect, elev_gate=None, clock=True)
    summ["all_links_dop"] = dict(median_VDOP=summ_all["median_VDOP"],
                                 p95_VDOP=summ_all["p95_VDOP"],
                                 median_GDOP=summ_all["median_GDOP"])
    return A, summ

def single_loss(anchors, track_rect, clock=True):
    """Drop each anchor once; report worst-case median VDOP and fix coverage."""
    x0, x1, y0, y1 = track_rect
    P, *_ = geo.make_grid(x0, x1, y0, y1, HEIGHTS_MM, HSTEP_MM, DEV)
    K = anchors.shape[0]
    ncol = 4 if clock else 3
    out = []
    for drop in range(K):
        keep = [i for i in range(K) if i != drop]
        A = anchors[keep]
        rng, elev, uvec = geo.link_geometry(P, A)
        mask = elev <= SAFE_DEG
        dop = geo.dop_from_uvec(uvec, mask=mask, clock=clock)
        out.append(dict(dropped=drop,
                        median_VDOP=geo.nanmedian(dop["VDOP"]),
                        frac_fix=float(torch.isfinite(dop["VDOP"]).float().mean().item()),
                        n_anchors=A.shape[0], enough_for_model=(A.shape[0] >= ncol)))
    finite = [o["median_VDOP"] for o in out if math.isfinite(o["median_VDOP"])]
    worst = max(finite) if finite else float("nan")
    return dict(per_drop=out, worst_median_VDOP=worst,
                min_frac_fix=min(o["frac_fix"] for o in out),
                model_survivable=all(o["enough_for_model"] for o in out))

# ================================================================== RUN
t0 = time.time()
results = dict(meta={}, task1={}, task2={}, task3={}, task4={}, task5={})

# ---- Task 1: baseline over full room --------------------------------------
rect0 = base_rect()
base = eval_layout(ANCH, rect0, elev_gate=None, want_maps=True, clock=True)
base_maps = base.pop("_maps")
# secondary clock-free (TOA) VDOP for reference
base_toa = eval_layout(ANCH, rect0, elev_gate=None, clock=False)
base["median_VDOP_toa3x3"] = base_toa["median_VDOP"]
base["p95_VDOP_toa3x3"] = base_toa["p95_VDOP"]
results["task1"] = base
BASE_MED_VDOP = base["median_VDOP"]

# V5 scale-lock cross-check on the baseline (robustness of the geometry choice)
_, ANCH_V5, _ = load_anchors(LAYOUT_V5SL)
base_v5 = eval_layout(ANCH_V5, rect0, elev_gate=None, clock=True)
results["task1"]["v5scalelock_crosscheck"] = dict(
    bands=base_v5["bands"], median_VDOP=base_v5["median_VDOP"],
    median_max_elev=base_v5["median_max_elev"])

# Sweet-spot diagnostic: %safe in the CENTRAL-half footprint at mid heights
# (0.8-1.2 m) — the realistic operating envelope where a hand-held wand lives.
cxr, cyr = 0.5 * (rect0[0] + rect0[1]), 0.5 * (rect0[2] + rect0[3])
half = [cxr - 0.25 * (rect0[1] - rect0[0]), cxr + 0.25 * (rect0[1] - rect0[0]),
        cyr - 0.25 * (rect0[3] - rect0[2]), cyr + 0.25 * (rect0[3] - rect0[2])]
Pss, *_ = geo.make_grid(half[0], half[1], half[2], half[3], [800, 1000, 1200], HSTEP_MM, DEV)
_, ess, _ = geo.link_geometry(Pss, ANCH)
me_ss = ess.max(dim=1).values
results["task1"]["sweetspot_central_midheight"] = dict(
    region="inner-50% footprint x 0.8-1.2 m",
    frac_safe=float((me_ss <= SAFE_DEG).float().mean().item()),
    frac_ge4_gentle=float(((ess <= SAFE_DEG).sum(1) >= 4).float().mean().item()),
    median_max_elev=float(me_ss.median().item()))

# Floor-datum sensitivity: the absolute elevation numbers depend on where the
# floor sits relative to the solved z=0 low-ring plane. Primary = floor at low
# ring (H_low=0). Alt = array mounted high (low ring 0.9 m above floor), tags
# below. Only %safe (all-links) is reported to show the trend is datum-robust.
def eval_safe_with_datum(anchors, rect, h_low_mm):
    x0, x1, y0, y1 = rect
    xs = torch.arange(x0, x1 + 1e-6, HSTEP_MM, dtype=torch.float32)
    ys = torch.arange(y0, y1 + 1e-6, HSTEP_MM, dtype=torch.float32)
    zs = torch.tensor([h_low_mm - h for h in HEIGHTS_MM], dtype=torch.float32)  # z=H_low-h
    gx, gy, gz = torch.meshgrid(xs, ys, zs, indexing="ij")
    P = torch.stack([gx.reshape(-1), gy.reshape(-1), gz.reshape(-1)], 1).to(DEV)
    _, e, _ = geo.link_geometry(P, anchors)
    me = e.max(dim=1).values
    return float((me <= SAFE_DEG).float().mean().item()), float(me.median().item())
s0 = eval_safe_with_datum(ANCH, rect0, 0.0)
s900 = eval_safe_with_datum(ANCH, rect0, 900.0)
results["task1"]["datum_sensitivity"] = dict(
    floor_at_low_ring_Hlow0=dict(frac_safe=s0[0], median_max_elev=s0[1]),
    array_high_Hlow900=dict(frac_safe=s900[0], median_max_elev=s900[1]),
    note="both datums fail the 95%-safe rule badly; trend datum-robust")

# ---- Task 2: shrink sweep --------------------------------------------------
cx, cy = CENTROID[0].item(), CENTROID[1].item()
sweep = []
maps_by_s = {}
for s in SHRINK_S:
    A_s = ANCH.clone()
    A_s[:, 0] = cx + s * (ANCH[:, 0] - cx)
    A_s[:, 1] = cy + s * (ANCH[:, 1] - cy)
    rect_s = scale_rect(rect0, s, cx, cy)
    want = s in (1.0, 0.7, 0.5, 0.4)
    r = eval_layout(A_s, rect_s, elev_gate=None, want_maps=want, clock=True)
    if want:
        maps_by_s[s] = r.pop("_maps")
    passed = (r["bands"]["safe"] >= 0.95) and (r["median_VDOP"] <= BASE_MED_VDOP)
    r["s"] = s
    r["PASS"] = bool(passed)
    r["pass_safe95"] = bool(r["bands"]["safe"] >= 0.95)
    r["pass_vdop_improve"] = bool(r["median_VDOP"] <= BASE_MED_VDOP)
    sweep.append(r)
results["task2"] = dict(baseline_median_VDOP=BASE_MED_VDOP, sweep=sweep,
                        any_pass=any(x["PASS"] for x in sweep))

# Rescue probe: at s=0.5, compress the anchor z-span toward its mean by factor c
# to pull link elevations down. Report the elevation-gain vs VDOP-cost tradeoff.
zc_out = []
s_r = 0.5
A_r = ANCH.clone()
A_r[:, 0] = cx + s_r * (ANCH[:, 0] - cx)
A_r[:, 1] = cy + s_r * (ANCH[:, 1] - cy)
rect_r = scale_rect(rect0, s_r, cx, cy)
zmean = ANCH[:, 2].mean().item()
for c in [1.0, 0.7, 0.5, 0.3, 0.15]:
    A_rc = A_r.clone()
    A_rc[:, 2] = zmean + c * (ANCH[:, 2] - zmean)
    rr = eval_layout(A_rc, rect_r, elev_gate=None, clock=True)
    zc_out.append(dict(z_compress=c, z_span_mm=round(1779.0 * c, 0),
                       frac_safe=rr["bands"]["safe"], frac_ge4_gentle=rr["frac_ge4_gentle"],
                       median_VDOP=rr["median_VDOP"], p95_VDOP=rr["p95_VDOP"],
                       median_max_elev=rr["median_max_elev"]))
results["task2"]["zcompress_rescue_at_s050"] = zc_out

# choose best s for Task 3/4: best passing, else s=0.5 (prompt)
passing = [x for x in sweep if x["PASS"]]
if passing:
    best_s = max(passing, key=lambda x: (x["bands"]["safe"], -x["median_VDOP"]))["s"]
else:
    best_s = 0.5
results["task2"]["best_s_used"] = best_s

# ---- Task 3: Cell-2 variants ----------------------------------------------
# Room split along x; Cell-1 (shrunk) -> low-x half, Cell-2 -> high-x half.
room_x0 = BBOX["x"][0] - MARGIN_ROOM_MM; room_x1 = BBOX["x"][1] + MARGIN_ROOM_MM
room_y0 = BBOX["y"][0] - MARGIN_ROOM_MM; room_y1 = BBOX["y"][1] + MARGIN_ROOM_MM
x_mid = 0.5 * (room_x0 + room_x1)
vac_rect = [x_mid, room_x1, room_y0, room_y1]                        # vacated (Cell-2) half
vac_track = [x_mid + MARGIN_WALL_MM, room_x1 - MARGIN_WALL_MM,
             room_y0 + MARGIN_WALL_MM, room_y1 - MARGIN_WALL_MM]     # Cell-2 tracking box
perim = perimeter_samples(vac_rect, n_per_edge=9)

VARIANTS = {"V-A": (2, 2), "V-B": (3, 3), "V-C": (4, 4)}
variant_out = {}
best_cell2 = None
for name, (nl, nh) in VARIANTS.items():
    A2, summ = optimize_cell2(perim, vac_track, nl, nh, restarts=1200, seed=13)
    sl = single_loss(A2, vac_track, clock=True)
    variant_out[name] = dict(
        n_anchors=nl + nh, n_low=nl, n_high=nh,
        anchors_mm=[[round(v, 1) for v in a] for a in A2.cpu().tolist()],
        bands=summ["bands"], median_VDOP=summ["median_VDOP"], p95_VDOP=summ["p95_VDOP"],
        median_HDOP=summ["median_HDOP"], median_GDOP=summ["median_GDOP"],
        frac_with_fix=summ["frac_with_fix"], all_links_dop=summ["all_links_dop"],
        single_loss=dict(worst_median_VDOP=sl["worst_median_VDOP"],
                         min_frac_fix=sl["min_frac_fix"],
                         model_survivable=sl["model_survivable"]),
    )
    # pick best VIABLE Cell-2 for Task 4: must survive single-anchor loss (>=4
    # anchors remain solvable), then rank by coverage (frac_with_fix).
    if sl["model_survivable"]:
        key = (summ["frac_with_fix"], summ["bands"]["safe"])
        if best_cell2 is None or key > best_cell2[0]:
            best_cell2 = (key, name, A2)
if best_cell2 is None:                       # no variant survived single loss
    # fall back to V-C (8) which always has the most redundancy
    A2c, _ = optimize_cell2(perim, vac_track, 4, 4, restarts=1200, seed=13)
    best_cell2 = ((0, 0), "V-C", A2c)

# hardware ledger
LISTENERS_TOTAL = 7; GATEWAYS_RESERVED = 2
free_units = LISTENERS_TOTAL - GATEWAYS_RESERVED
results["task3"] = dict(
    variants=variant_out, vac_track_mm=[round(v, 1) for v in vac_track],
    z_low_mm=Z_LOW, z_high_mm=Z_HIGH,
    hardware=dict(listeners_total=LISTENERS_TOTAL, gateways_reserved=GATEWAYS_RESERVED,
                  free_units=free_units,
                  note="Cell-2 anchor demand vs free spare radios; existing 8 A-H stay in Cell-1."),
    best_cell2_variant=best_cell2[1])

# ---- Task 4: overlap / handover -------------------------------------------
# Best Cell-1 at best_s, translated so its footprint centres in the low-x half.
A1 = ANCH.clone()
A1[:, 0] = cx + best_s * (ANCH[:, 0] - cx)
A1[:, 1] = cy + best_s * (ANCH[:, 1] - cy)
low_half_cx = 0.5 * (room_x0 + x_mid)
low_half_cy = 0.5 * (room_y0 + room_y1)
A1[:, 0] += (low_half_cx - A1[:, 0].mean())
A1[:, 1] += (low_half_cy - A1[:, 1].mean())
A2 = best_cell2[2]

# full-room grid, count <=25 links from each cell
P, nx, ny, nz, xs, ys = geo.make_grid(room_x0, room_x1, room_y0, room_y1,
                                      HEIGHTS_MM, HSTEP_MM, DEV)
_, e1, u1 = geo.link_geometry(P, A1)
_, e2, u2 = geo.link_geometry(P, A2)
n1 = (e1 <= SAFE_DEG).sum(dim=1)
n2 = (e2 <= SAFE_DEG).sum(dim=1)
both4 = (n1 >= 4) & (n2 >= 4)                      # overlap band: >=4 safe links from BOTH
# joint DOP (union anchors, <=25 links) vs each cell alone on overlap points
Aall = torch.cat([A1, A2], dim=0)
_, eall, uall = geo.link_geometry(P, Aall)
mask_all = eall <= SAFE_DEG
mask1 = e1 <= SAFE_DEG
mask2 = e2 <= SAFE_DEG
dop_joint = geo.dop_from_uvec(uall, mask=mask_all, clock=True)
dop_c1 = geo.dop_from_uvec(u1, mask=mask1, clock=True)
dop_c2 = geo.dop_from_uvec(u2, mask=mask2, clock=True)

def _med_on(x, sel):
    v = x[sel]; v = v[torch.isfinite(v)]
    return v.median().item() if v.numel() else float("nan")

overlap_frac = float(both4.float().mean().item())
# overlap width along x at the boundary (mid-y, mid-height slice)
mid_h = HEIGHTS_MM.index(1000)
both4_grid = both4.reshape(nx, ny, nz)
jy = ny // 2
col = both4_grid[:, jy, mid_h]                      # along x at mid-y, h=1.0m
xs_np = xs
overlap_x = xs_np[col.cpu()]
overlap_width_mm = float((overlap_x.max() - overlap_x.min()).item()) if overlap_x.numel() else 0.0
# handover feasibility: walking 1.5 m/s, need >=2 sweep periods at 5Hz/cell (0.2s each)
walk_speed = 1.5; periods_needed = 2; rate_cell = 5.0
min_width_needed_mm = walk_speed * (periods_needed / rate_cell) * 1000.0
results["task4"] = dict(
    overlap_frac_of_room=overlap_frac,
    overlap_points=int(both4.sum().item()),
    joint_median_VDOP=_med_on(dop_joint["VDOP"], both4),
    cell1_median_VDOP_on_overlap=_med_on(dop_c1["VDOP"], both4),
    cell2_median_VDOP_on_overlap=_med_on(dop_c2["VDOP"], both4),
    joint_median_GDOP=_med_on(dop_joint["GDOP"], both4),
    overlap_width_mm_midline=overlap_width_mm,
    min_width_needed_mm=min_width_needed_mm,
    handover_feasible=bool(overlap_width_mm >= min_width_needed_mm and both4.sum().item() > 0),
    note="overlap band = grid points with >=4 links <=25deg from BOTH cells")
# stash overlap map for figure
results["task4"]["_map_shape"] = [nx, ny, nz]
import numpy as np
np.savez(os.path.join(HERE, "overlap_map.npz"),
         both4=both4_grid.cpu().numpy(), xs=xs.cpu().numpy(), ys=ys.cpu().numpy(),
         heights=np.array(HEIGHTS_MM), A1=A1.cpu().numpy(), A2=A2.cpu().numpy())

# ---- Task 5: timing arithmetic (pure) -------------------------------------
GUARD_US, SPACING_US, TAIL_US, POLL_US = 1200, 1000, 800, 335
FINAL_US = 200            # reverse SS-TWR single frame (ASSUMED; prompt 150-200)
INTERBLOCK_GUARD_US = GUARD_US
SLOT_PERIOD_US, SLOT_ACTIVE_US = 10000, 9000
SUPERFRAME_US = 100000    # 10 Hz system
def collector_window(N):
    return GUARD_US + (N - 1) * SPACING_US + TAIL_US - POLL_US
sweep8 = collector_window(8)
timing = dict(
    constants=dict(GUARD_us=GUARD_US, SPACING_us=SPACING_US, TAIL_us=TAIL_US,
                   POLL_airtime_us=POLL_US, FINAL_us=FINAL_US,
                   interblock_guard_us=INTERBLOCK_GUARD_US,
                   slot_period_us=SLOT_PERIOD_US, slot_active_us=SLOT_ACTIVE_US,
                   superframe_us=SUPERFRAME_US),
    sweep_us=dict(N4=collector_window(4), N6=collector_window(6),
                  N8=sweep8, N9=collector_window(9)),
)
# --- Rate model ---------------------------------------------------------
# The system's *sweep cadence* is inherited from the 100 ms superframe: a tag
# polls once per superframe per owned slot -> 1 slot/tag = 10 Hz (the current
# single-cell motion profile). Cell-split shares that cadence across n cells.
#   Policy A (rate-preserving, matches "10 Hz system"): the tag keeps its ONE
#     slot and rotates which cell it sweeps each superframe -> 10/n Hz per cell.
#   Policy B (slot-doubling): the tag owns one slot PER cell each superframe ->
#     10 Hz per cell, but consumes n of the 10 slots (fewer tags fit).
# A combined dual-cell epoch cannot share one slot: Block1+FINAL+guard+Block2
# (~16 ms) exceeds the 9 ms active window -> cells MUST occupy separate slots.
SYS_SWEEP_HZ = 1e6 / SUPERFRAME_US                 # 10 Hz (1 sweep / superframe / slot)
n_slots = SUPERFRAME_US // SLOT_PERIOD_US          # 10
two_block = {}
for name, (nl, nh) in VARIANTS.items():
    Nc2 = nl + nh
    sw2 = collector_window(Nc2)
    epoch_us = sweep8 + FINAL_US + INTERBLOCK_GUARD_US + sw2 + FINAL_US + INTERBLOCK_GUARD_US
    two_block[name] = dict(
        cell2_anchors=Nc2, cell1_sweep_us=sweep8, cell2_sweep_us=sw2,
        combined_epoch_us=epoch_us,
        epoch_exceeds_one_slot=bool(epoch_us > SLOT_ACTIVE_US),
        each_block_fits_one_slot=bool(sweep8 <= SLOT_ACTIVE_US and sw2 <= SLOT_ACTIVE_US),
        rate_hz_per_cell_policyA=SYS_SWEEP_HZ / 2,        # rotate 1 slot -> 5 Hz
        rate_hz_per_cell_policyB=SYS_SWEEP_HZ,            # 2 slots -> 10 Hz
        slots_used_policyB=2,
        airtime_util_of_100ms=epoch_us / SUPERFRAME_US,  # how little RF is actually used
    )
timing["two_block"] = two_block
# 3-block (court-scale preview): 3 alternating 8-anchor blocks
epoch3 = 3 * (sweep8 + FINAL_US + INTERBLOCK_GUARD_US)
timing["three_block"] = dict(
    blocks=3, per_block_anchors=8, per_block_sweep_us=sweep8,
    combined_epoch_us=epoch3,
    rate_hz_per_cell_policyA=SYS_SWEEP_HZ / 3,           # ~3.33 Hz
    rate_hz_per_cell_policyB=SYS_SWEEP_HZ,               # 10 Hz, uses 3 slots
    slots_used_policyB=3,
    airtime_util_of_100ms=epoch3 / SUPERFRAME_US,
)
timing["system_sweep_hz"] = SYS_SWEEP_HZ
timing["note"] = ("per-cell rate = 10 Hz / n_cells under the rate-preserving "
                  "policy (Policy A); slot-doubling (Policy B) holds 10 Hz/cell "
                  "at the cost of slot occupancy. RF airtime per epoch is ~13-30 "
                  "ms of the 100 ms budget, so 10 Hz is a superframe-structure "
                  "choice, not an airtime limit.")
results["task5"] = timing

# ------------------------------------------------------------------ meta / save
try:
    import psutil
    cpu_pct = psutil.cpu_percent(interval=0.3)
    ncpu = psutil.cpu_count()
except Exception:
    cpu_pct, ncpu = None, os.cpu_count()
gpu_mem_mb = (torch.cuda.max_memory_allocated(DEV) / 1e6) if DEV.type == "cuda" else 0.0
results["meta"] = dict(
    device=str(DEV),
    gpu_name=(torch.cuda.get_device_name(DEV) if DEV.type == "cuda" else "cpu"),
    gpu_max_mem_MB=round(gpu_mem_mb, 1),
    torch_num_threads=torch.get_num_threads(),
    cpu_percent_during=cpu_pct, cpu_count=ncpu,
    runtime_s=round(time.time() - t0, 2),
    layout_used="anchor_layout.json (V4-io, deployed geometry)",
    layout_reason=("V4-io is the physically deployed layout that would actually be "
                   "shrunk; V5 scale-locked (35mm rms from V4-io) used only as a "
                   "baseline cross-check; V5 (non-locked) rejected (scale unidentifiable)."),
    conventions=dict(up="-z", floor_datum="low-ring z=0 plane (ASSUMED)",
                     z_low_mm=Z_LOW, z_high_mm=Z_HIGH,
                     room_margin_mm=MARGIN_ROOM_MM, wall_margin_mm=MARGIN_WALL_MM,
                     dop_model="4-unknown pseudorange (x,y,z,clock); 3x3 TOA secondary"),
    anchor_bbox_mm=BBOX, centroid_mm=[cx, cy],
    seed=1234,
)

def _san(o):
    if isinstance(o, dict): return {k: _san(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)): return [_san(v) for v in o]
    if isinstance(o, float) and (math.isnan(o) or math.isinf(o)): return None
    return o

with open(os.path.join(HERE, "results.json"), "w") as f:
    json.dump(_san(results), f, indent=2)

# stash maps for figures
np.savez(os.path.join(HERE, "baseline_maps.npz"),
         max_elev=base_maps["max_elev"], VDOP=base_maps["VDOP"],
         xs=base_maps["xs"], ys=base_maps["ys"], heights=np.array(HEIGHTS_MM),
         anchors=ANCH.cpu().numpy())
for s, m in maps_by_s.items():
    np.savez(os.path.join(HERE, f"maps_s{int(s*100):03d}.npz"),
             max_elev=m["max_elev"], VDOP=m["VDOP"],
             xs=m["xs"], ys=m["ys"], heights=np.array(HEIGHTS_MM))

# ---------- console summary ----------
print(f"[device] {results['meta']['device']} ({results['meta']['gpu_name']}) "
      f"gpu_max_mem={results['meta']['gpu_max_mem_MB']}MB "
      f"threads={results['meta']['torch_num_threads']} "
      f"cpu%={cpu_pct} runtime={results['meta']['runtime_s']}s")
print(f"[T1 baseline] safe<=25:{base['bands']['safe']*100:.1f}%  "
      f"25-37:{base['bands']['risk']*100:.1f}%  >=37:{base['bands']['danger']*100:.1f}%  "
      f"medVDOP={base['median_VDOP']:.2f} p95VDOP={base['p95_VDOP']:.2f} "
      f"med_max_elev={base['median_max_elev']:.1f}deg")
print("[T2 shrink]  s   %safe  %>=37  medVDOP p95VDOP medGDOP  PASS")
for r in sweep:
    print(f"           {r['s']:.2f}  {r['bands']['safe']*100:5.1f}  {r['bands']['danger']*100:5.1f}  "
          f"{r['median_VDOP']:6.2f}  {r['p95_VDOP']:6.2f}  {r['median_GDOP']:6.2f}   {r['PASS']}")
print(f"[T2] any pass: {results['task2']['any_pass']}   best_s_used: {best_s}")
for name, v in variant_out.items():
    print(f"[T3 {name}] anchors={v['n_anchors']} safe={v['bands']['safe']*100:.1f}% "
          f"medVDOP={v['median_VDOP']:.2f} fix={v['frac_with_fix']*100:.1f}% "
          f"single-loss worstVDOP={v['single_loss']['worst_median_VDOP']} "
          f"survivable={v['single_loss']['model_survivable']}")
print(f"[T4 overlap] best_viable_cell2={best_cell2[1]} frac_room={overlap_frac*100:.2f}% "
      f"width_mid={overlap_width_mm:.0f}mm need>={min_width_needed_mm:.0f}mm "
      f"jointVDOP={results['task4']['joint_median_VDOP']:.2f} "
      f"feasible={results['task4']['handover_feasible']}")
print(f"[T5 timing] sweep8={sweep8}us  2-block/cell (A)="
      f"{two_block['V-A']['rate_hz_per_cell_policyA']:.2f}Hz  "
      f"3-block/cell (A)={timing['three_block']['rate_hz_per_cell_policyA']:.2f}Hz  "
      f"(policyB doubling -> {SYS_SWEEP_HZ:.0f}Hz/cell)")
print("[done] results.json written")
