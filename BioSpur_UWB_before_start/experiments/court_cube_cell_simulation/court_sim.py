#!/usr/bin/env python3
"""
court_sim.py — Court-Scale Double-Cube Cell Simulation (4+4+4, 12 anchors).

PURE SIMULATION. No hardware/firmware/captures. Read-only on existing data.
Reuses the cell_split_simulation geometry code path WITHOUT redefining anything:
  geo.link_geometry  -> range + elevation + LOS unit vectors
  geo.dop_from_uvec  -> GDOP/HDOP/VDOP from the 4-column (x,y,z,1) design matrix,
                        sqrt(diag((G^T G)^-1)); <4 feasible -> NaN -> 0 coverage.
GPU-first (cupy if present, else torch on cuda:0, FP32). CPU capped at 2 threads.

Architecture under test (fixed, not modified):
  6 poles: 2 at baseline-A, 2 at centerline (shared), 2 at baseline-B.
  Each pole = one LOW + one HIGH anchor -> 8 cuboid vertices per cell.
  Cell A = 4 baseline-A + 4 centerline = 8 ;  Cell B = 4 centerline + 4 baseline-B = 8.
  12 anchors total (4 centerline shared). ONE poll = the tag's 8 in-cell anchors.
"""
import os, sys, json, time, math, threading, subprocess
os.environ.setdefault("OMP_NUM_THREADS", "2")
os.environ.setdefault("MKL_NUM_THREADS", "2")
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
CELLSPLIT = os.path.join(REPO, "experiments", "cell_split_simulation")
sys.path.insert(0, CELLSPLIT)          # reuse geo.py (DO NOT re-implement)
import torch
torch.set_num_threads(2)
import geo                              # noqa: E402  (from cell_split_simulation)

# ------- backend self-report (cupy preferred, torch fallback) --------------
BACKEND = "torch"
try:
    import cupy  # noqa
    BACKEND = "cupy(available)+torch(compute)"
except Exception:
    BACKEND = "torch (cupy not installed -> fallback)"
DEV = geo.get_device(0)
torch.manual_seed(20260715)

# ------------------------------------------------------------------ SWEEPS
COURTS = {
    # (length_x_m, width_y_m) ; each cell footprint = length/2 x width
    "basketball": (28.0, 15.0),
    "tennis":     (36.6, 18.3),   # incl. runback/buffer -> stress case
}
Z_HIGH_SWEEP = [3.0, 4.0, 6.0, 8.0, 10.0]      # truss height (m) — venue go/no-go
D_CLOSE_SWEEP = [8.0, 10.0, 12.0, 15.0, 18.0, 20.0]  # link-closure dist (m) — UNMEASURED
Z_TAG_SWEEP = [round(0.2 * i, 1) for i in range(1, 11)]  # 0.2..2.0 m
Z_LOW = 0.3           # low ring (m)  [assumption]
INSET = 0.5           # pole inset from lines (m) [assumption; sensitivity in D-sens]
GRID_M = 0.25         # position grid spacing (m)
SAFE_DEG = 25.0
DANGER_DEG = 37.0
VIABLE_COV = 0.90     # >=4 feasible must cover >=90% of volume to call "viable"
RANGE_SIGMA_MM = 25.0 # baseline ranging sigma (solver-v2 principle) for accuracy floor

# ------------------------------------------------------------------ geometry
def build_anchors(court, z_high, inset=INSET, z_low=Z_LOW):
    """Return (anchorsA[8,3], anchorsB[8,3], all12[12,3], poles) in metres.
    x = court length, y = width, z = up (+)."""
    L, W = COURTS[court]
    xc = L / 2.0
    px_A, px_C, px_B = inset, xc, L - inset
    py0, py1 = inset, W - inset
    def pole(px, py):        # low + high anchor on a pole
        return [(px, py, z_low), (px, py, z_high)]
    baseA = pole(px_A, py0) + pole(px_A, py1)      # 4
    center = pole(px_C, py0) + pole(px_C, py1)     # 4 (shared)
    baseB = pole(px_B, py0) + pole(px_B, py1)      # 4
    A = torch.tensor(baseA + center, dtype=torch.float32, device=DEV)
    B = torch.tensor(center + baseB, dtype=torch.float32, device=DEV)
    all12 = torch.tensor(baseA + center + baseB, dtype=torch.float32, device=DEV)
    poles = dict(baseA=[(px_A, py0), (px_A, py1)], center=[(px_C, py0), (px_C, py1)],
                 baseB=[(px_B, py0), (px_B, py1)])
    return A, B, all12, poles, (L, W, xc)

def cell_grid(court, cell="A"):
    """Tag position grid over a cell footprint (all heights). Returns
    P[N,3], (nxy, nh), xs, ys, zs."""
    L, W = COURTS[court]
    xc = L / 2.0
    x0, x1 = (0.0, xc) if cell == "A" else (xc, L)
    xs = torch.arange(x0, x1 + 1e-6, GRID_M, dtype=torch.float32)
    ys = torch.arange(0.0, W + 1e-6, GRID_M, dtype=torch.float32)
    zs = torch.tensor(Z_TAG_SWEEP, dtype=torch.float32)
    gx, gy, gz = torch.meshgrid(xs, ys, zs, indexing="ij")
    P = torch.stack([gx.reshape(-1), gy.reshape(-1), gz.reshape(-1)], 1).to(DEV)
    return P, (len(xs) * len(ys), len(zs)), xs.numpy(), ys.numpy(), np.array(Z_TAG_SWEEP)

def frac(mask_bool):
    return float(mask_bool.float().mean().item())

# ------------------------------------------------------------------ evaluate
def eval_config(court, z_high):
    """Compute per-position geometry ONCE for a config; sweep d_close as masks."""
    A, B, all12, poles, (L, W, xc) = build_anchors(court, z_high)
    P, (nxy, nh), xs, ys, zs = cell_grid(court, "A")
    rng, elev, uvec = geo.link_geometry(P, A)     # ranges/elev in metres/deg
    rng_m = rng                                    # already metres
    gentle = elev <= SAFE_DEG                       # [N,8]
    # pure geometric conditioning (all 8 in-cell anchors, no link gate)
    dop_pure = geo.dop_from_uvec(uvec, mask=None, clock=True, rank_guard=True)
    # min horizontal distance to any pole (for near-pole elevation story)
    pole_xy = torch.tensor([p for grp in poles.values() for p in grp],
                           dtype=torch.float32, device=DEV)   # [6,2]
    dxy = torch.linalg.vector_norm(P[:, None, :2] - pole_xy[None, :, :], dim=2)  # [N,6]
    min_pole_dist = dxy.min(dim=1).values

    out = dict(court=court, z_high=z_high, z_span=z_high - Z_LOW,
               n_points=int(P.shape[0]), n_xy=nxy, n_h=nh,
               footprint_m=[round(xc, 2), round(W, 2)],
               diag_m=round(math.hypot(xc, W), 2),
               aspect_zspan_over_diag=round((z_high - Z_LOW) / math.hypot(xc, W), 3),
               pure_median_VDOP=geo.nanmedian(dop_pure["VDOP"]),
               pure_median_HDOP=geo.nanmedian(dop_pure["HDOP"]),
               pure_median_GDOP=geo.nanmedian(dop_pure["GDOP"]),
               pure_frac_fix=frac(torch.isfinite(dop_pure["VDOP"])),
               dclose=[])

    # reshape helpers: [nxy, nh]
    def per_height(boolmask_N):
        return boolmask_N.reshape(nxy, nh).float().mean(dim=0).cpu().numpy()  # [nh]

    for dc in D_CLOSE_SWEEP:
        feasible = rng_m <= dc                       # [N,8]
        nfeas = feasible.sum(dim=1)
        nfeas_gentle = (feasible & gentle).sum(dim=1)
        ge4 = nfeas >= 4
        ge4g = nfeas_gentle >= 4
        dop = geo.dop_from_uvec(uvec, mask=feasible, clock=True, rank_guard=True)
        vd = dop["VDOP"]
        rec = dict(
            d_close=dc,
            cov_ge4_feasible=frac(ge4),
            cov_ge4_feas_gentle=frac(ge4g),
            median_VDOP_gated=geo.nanmedian(vd), p95_VDOP_gated=geo.pct(vd, 0.95),
            median_HDOP_gated=geo.nanmedian(dop["HDOP"]),
            frac_solvable=frac(torch.isfinite(vd)),
            per_height_ge4=[round(float(v), 4) for v in per_height(ge4)],
            per_height_ge4gentle=[round(float(v), 4) for v in per_height(ge4g)],
            worst_height_ge4=float(per_height(ge4).min()),
            worst_height_ge4gentle=float(per_height(ge4g).min()),
        )
        out["dclose"].append(rec)

    # viability d_close: smallest dc with overall >=4-feasible >= 90%
    viable = [r["d_close"] for r in out["dclose"] if r["cov_ge4_feasible"] >= VIABLE_COV]
    out["viable_dclose_ge4feasible"] = min(viable) if viable else None
    viable_g = [r["d_close"] for r in out["dclose"] if r["cov_ge4_feas_gentle"] >= VIABLE_COV]
    out["viable_dclose_ge4gentle"] = min(viable_g) if viable_g else None

    # D3 near-pole steep analytic radii (worst tag height for each anchor ring)
    zt_hi = max(Z_TAG_SWEEP)   # 2.0 (worst for low anchor: tag well above it)
    zt_lo = min(Z_TAG_SWEEP)   # 0.2
    def radius(dz, deg):
        return dz / math.tan(math.radians(deg)) if dz > 0 else 0.0
    out["d3_nearpole"] = dict(
        high_anchor_dz_max=round(z_high - zt_lo, 2),   # tag low, high anchor far above
        r25_high=round(radius(z_high - 1.0, 25.0), 2),  # at nominal tag 1.0 m
        r37_high=round(radius(z_high - 1.0, 37.0), 2),
        low_anchor_dz_max=round(zt_hi - Z_LOW, 2),     # tag at 2.0, low anchor at 0.3
        r25_low=round(radius(zt_hi - Z_LOW, 25.0), 2),
        r37_low=round(radius(zt_hi - Z_LOW, 37.0), 2),
        # fraction of volume that is strictly safe (ALL 8 in-cell links <=25)
        frac_strict_safe_allgentle=frac((elev <= SAFE_DEG).all(dim=1)),
        frac_any_steep37=frac((elev >= DANGER_DEG).any(dim=1)),
        mean_gentle_count=float(gentle.sum(dim=1).float().mean().item()),
    )
    return out, (A, B, all12, poles, (L, W, xc))

# ------------------------------------------------------------------ D4 overlap
def eval_overlap(court, z_high):
    """Overlap band around the shared centerline: >=4 feasible from BOTH cells;
    joint 12-anchor DOP there. Uses a generous+a strict d_close to bound it."""
    A, B, all12, poles, (L, W, xc) = build_anchors(court, z_high)
    # full-court grid at a representative tag height (1.0 m) for band width
    xs = torch.arange(0.0, L + 1e-6, GRID_M, dtype=torch.float32)
    ys = torch.arange(0.0, W + 1e-6, GRID_M, dtype=torch.float32)
    gx, gy = torch.meshgrid(xs, ys, indexing="ij")
    zt = 1.0
    P = torch.stack([gx.reshape(-1), gy.reshape(-1),
                     torch.full((gx.numel(),), zt)], 1).to(DEV)
    rA, eA, uA = geo.link_geometry(P, A)
    rB, eB, uB = geo.link_geometry(P, B)
    _, _, u12 = geo.link_geometry(P, all12)
    r12, _, _ = geo.link_geometry(P, all12)
    res = dict(court=court, z_high=z_high, tag_h=zt, by_dclose={})
    for dc in [12.0, 15.0, 20.0]:
        fA = (rA <= dc).sum(1) >= 4
        fB = (rB <= dc).sum(1) >= 4
        both = fA & fB
        m12 = r12 <= dc
        dop12 = geo.dop_from_uvec(u12, mask=m12, clock=True, rank_guard=True)
        # band width along x at mid-y
        nx, ny = len(xs), len(ys)
        both_grid = both.reshape(nx, ny)
        jy = ny // 2
        col = both_grid[:, jy].cpu().numpy()
        xsn = xs.numpy()
        width = float(xsn[col].max() - xsn[col].min()) if col.any() else 0.0
        vd_both = dop12["VDOP"][both]; vd_both = vd_both[torch.isfinite(vd_both)]
        res["by_dclose"][dc] = dict(
            overlap_frac_court=frac(both),
            overlap_band_width_m_midline=round(width, 2),
            joint12_median_VDOP_on_overlap=float(vd_both.median().item()) if vd_both.numel() else None,
            joint12_median_GDOP_on_overlap=(lambda g: float(g[torch.isfinite(g)].median().item()) if torch.isfinite(g).any() else None)(dop12["GDOP"][both]),
        )
    res["walker_needs_m"] = 0.6   # 1.5 m/s * 2 sweeps / 5 Hz
    return res

# ------------------------------------------------------------------ D5 body
def eval_body_crossing(court):
    """Rigid body of horizontal extent BODY_M straddling the centerline. Under
    PER-TAG zone assignment, band where one body's tags split across cells."""
    L, W = COURTS[court]
    xc = L / 2.0
    BODY_M = 2.0    # worst-case worn-tag horizontal spread (stride/arms/lying)
    # per-tag: split whenever body spans the centerline -> centroid within BODY/2
    split_band_m = BODY_M
    return dict(court=court, body_extent_m=BODY_M, centerline_x_m=round(xc, 2),
                per_tag_split_band_m=split_band_m,
                walker_dwell_needed_m=0.6,
                centroid_assignment_split_band_m=0.0,
                note=("per-tag zone assignment splits one body's tags across two "
                      "anchor sets + two bias calibrations within a 2.0 m band at "
                      "the centerline (injected relative pose error); centroid "
                      "(whole-body->one-cell) assignment removes the split. Residual "
                      "cost = one handoff epoch, absorbed if the D4 overlap band "
                      ">= per-epoch travel (0.6 m)."))

# ================================================================== RUN
# --- live resource sampler (start + peak) -------------------------------
class Sampler(threading.Thread):
    """Samples GPU util (nvidia-smi), whole-box CPU%, and THIS process's CPU%
    (normalised to % of the box) so the self-report separates my workload from
    the user's concurrent J-Link load."""
    def __init__(self):
        super().__init__(daemon=True)
        self.stop = False
        self.cpu_sys = []; self.cpu_proc = []; self.gpu = []; self.mem = []
        import psutil
        self._proc = psutil.Process()
        self._ncpu = psutil.cpu_count()
        self._proc.cpu_percent(None)        # prime
    def _gpu(self):
        try:
            o = subprocess.check_output(
                ["nvidia-smi", "--query-gpu=utilization.gpu,memory.used",
                 "--format=csv,noheader,nounits", "-i", "0"], timeout=2).decode()
            u, m = o.strip().split(",")
            return float(u), float(m)
        except Exception:
            return float("nan"), float("nan")
    def run(self):
        import psutil
        while not self.stop:
            self.cpu_sys.append(psutil.cpu_percent(interval=0.25))
            self.cpu_proc.append(self._proc.cpu_percent(None) / self._ncpu)  # % of box
            g, m = self._gpu(); self.gpu.append(g); self.mem.append(m)

t0 = time.time()
try:
    import psutil
    ncpu = psutil.cpu_count()
    cpu_start = psutil.cpu_percent(interval=0.3)
except Exception:
    ncpu, cpu_start = os.cpu_count(), None
smp = Sampler(); smp.start()

RES = dict(meta={}, assumptions={}, courts={}, d6_killcheck={})
geoms = {}
for court in COURTS:
    RES["courts"][court] = dict(dims_m=COURTS[court],
                                cell_footprint_m=[COURTS[court][0]/2, COURTS[court][1]],
                                configs=[], overlap=[], body=eval_body_crossing(court))
    for zh in Z_HIGH_SWEEP:
        cfg, g = eval_config(court, zh)
        RES["courts"][court]["configs"].append(cfg)
        geoms[(court, zh)] = g
        RES["courts"][court]["overlap"].append(eval_overlap(court, zh))

# --- D-sensitivity: inset 0.5 -> 1.0 m on the stress case (tennis, zh=6) ---
A0, *_ = build_anchors("tennis", 6.0, inset=0.5)
sens = {}
for ins in [0.5, 1.0]:
    A, B, all12, poles, (L, W, xc) = build_anchors("tennis", 6.0, inset=ins)
    P, (nxy, nh), *_ = cell_grid("tennis", "A")
    rng, elev, uvec = geo.link_geometry(P, A)
    dopp = geo.dop_from_uvec(uvec, mask=None, clock=True, rank_guard=True)
    feas = rng <= 15.0
    dopg = geo.dop_from_uvec(uvec, mask=feas, clock=True, rank_guard=True)
    sens[ins] = dict(pure_median_VDOP=geo.nanmedian(dopp["VDOP"]),
                     cov_ge4_feasible_dc15=frac((feas.sum(1) >= 4)))
RES["assumptions"]["inset_sensitivity_tennis_zh6"] = sens

# --- D6 kill-check: court cube vs home-room baseline (from cell_split) ----
home = json.load(open(os.path.join(CELLSPLIT, "results.json")))
hbb = home["meta"]["anchor_bbox_mm"]
h_zspan = (hbb["z"][1] - hbb["z"][0]) / 1000.0
h_diag = math.hypot(hbb["x"][1]-hbb["x"][0], hbb["y"][1]-hbb["y"][0]) / 1000.0
home_ref = dict(source="experiments/cell_split_simulation/results.json",
                footprint_m=[round((hbb["x"][1]-hbb["x"][0])/1000, 2),
                             round((hbb["y"][1]-hbb["y"][0])/1000, 2)],
                z_span_m=round(h_zspan, 2), diag_m=round(h_diag, 2),
                aspect_zspan_over_diag=round(h_zspan / h_diag, 3),
                median_VDOP=round(home["task1"]["median_VDOP"], 3),
                median_GDOP=round(home["task1"]["median_GDOP"], 3),
                note_erlangen_accuracy_floor_mm="72-100 (bias/multipath, NOT geometry)")
# court cube pure conditioning per config (link budget aside)
court_cond = []
for court in COURTS:
    for cfg in RES["courts"][court]["configs"]:
        court_cond.append(dict(court=court, z_high=cfg["z_high"],
                               aspect=cfg["aspect_zspan_over_diag"],
                               pure_median_VDOP=round(cfg["pure_median_VDOP"], 3),
                               better_conditioned_than_home=bool(
                                   cfg["pure_median_VDOP"] <= home_ref["median_VDOP"])))
RES["d6_killcheck"] = dict(home_room=home_ref, court_cube=court_cond,
    accuracy_note=("VDOP is geometric conditioning only. Achievable position "
                   "accuracy is bias/multipath-floored at ~72-100 mm (Erlangen); "
                   "the court does NOT lower that floor, it only tiles the same "
                   "~72-100 mm cell across a bigger space. 'court -> mm' would be "
                   "wrong. accuracy_1sigma_mm ~= VDOP * range_sigma(25mm) is a "
                   "conditioning-limited LOWER bound, not the real floor."))

# ------------------------------------------------------------------ finalize
smp.stop = True; smp.join(timeout=2)
def _peak(a):
    a = [x for x in a if x == x]  # drop nan
    return round(max(a), 1) if a else None
def _mean(a):
    a = [x for x in a if x == x]
    return round(sum(a)/len(a), 1) if a else None
gpu_peak_mem = (torch.cuda.max_memory_allocated(DEV)/1e6) if DEV.type == "cuda" else 0.0
RES["meta"] = dict(
    backend=BACKEND, device=str(DEV),
    gpu_name=(torch.cuda.get_device_name(DEV) if DEV.type == "cuda" else "cpu"),
    gpu_total_mem_MB=(round(torch.cuda.get_device_properties(DEV).total_memory/1e6) if DEV.type=="cuda" else 0),
    gpu_torch_peak_alloc_MB=round(gpu_peak_mem, 1),
    gpu_util_pct_peak=_peak(smp.gpu), gpu_util_pct_mean=_mean(smp.gpu),
    gpu_used_mem_MB_peak=_peak(smp.mem),
    cpu_count=ncpu, worker_processes=1, torch_num_threads=torch.get_num_threads(),
    cpu_box_pct_start=cpu_start, cpu_box_pct_peak=_peak(smp.cpu_sys), cpu_box_pct_mean=_mean(smp.cpu_sys),
    cpu_thisproc_pct_peak=_peak(smp.cpu_proc), cpu_thisproc_pct_mean=_mean(smp.cpu_proc),
    cpu_note="cpu_box_* is whole-machine (incl. user's J-Link); cpu_thisproc_* is my share (% of the 12-core box) — that is the number that must stay <=30%",
    runtime_s=round(time.time()-t0, 2), seed=20260715)
RES["assumptions"].update(dict(
    court_bounds="ASSUMED (no authoritative court dims/truss in repo)",
    basketball_m=COURTS["basketball"], tennis_m=COURTS["tennis"],
    z_low_m=Z_LOW, z_high_sweep_m=Z_HIGH_SWEEP, inset_m=INSET,
    d_close_sweep_m=D_CLOSE_SWEEP, d_close_note="UNMEASURED — must come from a real HBS capture",
    z_tag_sweep_m=Z_TAG_SWEEP, grid_m=GRID_M, safe_deg=SAFE_DEG, danger_deg=DANGER_DEG,
    range_sigma_mm=RANGE_SIGMA_MM,
    dop_model="4-unknown (x,y,z,clock); rows=LOS unit vec + 1 col; sqrt(diag((G^T G)^-1)) — reused from cell_split geo.py"))

def _san(o):
    if isinstance(o, dict): return {k: _san(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)): return [_san(v) for v in o]
    if isinstance(o, float) and (math.isnan(o) or math.isinf(o)): return None
    if isinstance(o, (np.floating,)): return float(o)
    if isinstance(o, (np.integer,)): return int(o)
    return o
with open(os.path.join(HERE, "results.json"), "w") as f:
    json.dump(_san(RES), f, indent=2)
# also drop a run log
with open(os.path.join(HERE, "logs", "run.log"), "w") as f:
    f.write(f"court_sim run seed={RES['meta']['seed']} runtime={RES['meta']['runtime_s']}s\n")
    f.write(f"backend={BACKEND} device={RES['meta']['device']} gpu={RES['meta']['gpu_name']}\n")
    f.write(f"gpu_util_peak={RES['meta']['gpu_util_pct_peak']}% mem_peak={RES['meta']['gpu_used_mem_MB_peak']}MB "
            f"myproc_cpu_peak={RES['meta']['cpu_thisproc_pct_peak']}% box_cpu_peak={RES['meta']['cpu_box_pct_peak']}% cores={ncpu}\n")

# ---------- console ----------
m = RES["meta"]
print(f"[backend] {m['backend']} | {m['device']} {m['gpu_name']} "
      f"({m['gpu_total_mem_MB']}MB) torch_alloc_peak={m['gpu_torch_peak_alloc_MB']}MB")
print(f"[resources] GPU util peak/mean={m['gpu_util_pct_peak']}/{m['gpu_util_pct_mean']}% "
      f"mem_peak={m['gpu_used_mem_MB_peak']}MB | cores={m['cpu_count']} workers=1 threads={m['torch_num_threads']} "
      f"| my-proc CPU peak/mean={m['cpu_thisproc_pct_peak']}/{m['cpu_thisproc_pct_mean']}% of box "
      f"(box-wide {m['cpu_box_pct_peak']}/{m['cpu_box_pct_mean']}%) | runtime={m['runtime_s']}s")
print("\n[D1 link-budget] viability d_close (>=4 feasible >=90% of cell):")
for court in COURTS:
    for cfg in RES["courts"][court]["configs"]:
        if cfg["z_high"] in (4.0, 8.0):
            row = " ".join(f"{r['cov_ge4_feasible']*100:4.0f}" for r in cfg["dclose"])
            print(f"  {court:10s} zh={cfg['z_high']:.0f}m  cov%@dc[8,10,12,15,18,20]= {row}  "
                  f"viable@{cfg['viable_dclose_ge4feasible']}m")
print("\n[D2 truss height] pure median VDOP vs z_high:")
for court in COURTS:
    vs = " ".join(f"{c['z_high']:.0f}m:{c['pure_median_VDOP']:.2f}" for c in RES["courts"][court]["configs"])
    print(f"  {court:10s} {vs}")
print("\n[D6 kill-check] home-room VDOP={:.2f} (aspect {:.2f}); court cube:".format(
    home_ref["median_VDOP"], home_ref["aspect_zspan_over_diag"]))
for cc in court_cond:
    if cc["z_high"] in (4.0, 8.0):
        print(f"  {cc['court']:10s} zh={cc['z_high']:.0f}m aspect={cc['aspect']:.2f} "
              f"VDOP={cc['pure_median_VDOP']:.2f} better_than_home={cc['better_conditioned_than_home']}")
print("\n[done] results.json + logs/run.log written")
