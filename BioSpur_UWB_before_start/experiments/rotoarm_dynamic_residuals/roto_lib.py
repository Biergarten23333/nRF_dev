#!/usr/bin/env python3
"""
Shared library for RotoArm dynamic-residual analysis.

Read-only on all capture data. Reuses the Erlangen 'Model Outputs' Vicon parsing
convention (antenna markers) from experiments/antenna_orientation_erlangen/analyze.py.

Primary dataset: erlangen_20260528_optitrack roto series R01..R17.
  * Two tags on the arm simultaneously (dual radius):
        UWB BS2DCE  <-> Vicon marker 'WandB'  (R ~ 440 mm)
        UWB BSDC91  <-> Vicon marker 'WandC'  (R ~ 553 mm)
    (assignment resolved per-capture by residual minimisation, not assumed.)
  * 8 static anchors A..H (antenna markers).
  * Tilt series: R01 ~1deg (flat) .. R14/16/17 ~90deg (vertical plane).
  * Vicon 120 fps 'Model Outputs'; UWB TDMA 10 Hz, 9 ms active, 8 anchors
    -> nominal slot ~= 9/8 = 1.125 ms per rank (rank == anchor_id).

Time sync: NO hardware sync. UWB<->Vicon alignment is a post-hoc best-fit global
offset tau (optionally + linear drift). Per methodological constraint C1, only
offset-surviving components are reported as findings; absolute per-link bias is
flagged ALIGNMENT-DEPENDENT and quoted with +/-1-frame sensitivity.
"""
import csv, glob, os, math
import numpy as np

ROOT = "/mnt/nrf_ssd/nRF_dev/BioSpur_UWB_before_start"
BASE = os.path.join(ROOT, "autopos_pipeline/28052026_Erlangen_Official")
VIC  = os.path.join(BASE, "opti_captures/full")
CAP  = os.path.join(BASE, "captures/erlangen_20260528_optitrack")
LETTERS = "ABCDEFGH"
FPS = 120.0                       # Vicon Model Outputs frame rate
NOMINAL_SLOT_MS = 9.0 / 8.0       # 9 ms active window / 8 anchors = 1.125 ms/rank
N_ANCHORS = 8

# roto capture id -> (vicon file stem, uwb tags present)
ROTO_TAGS = ("BS2DCE", "BSDC91")
TAG_MARKERS = ("WandB", "WandC")   # candidate Vicon markers for the two tags

# ---------------------------------------------------------------- Vicon parsing
def parse_vicon(stem):
    """Parse a Model-Outputs Vicon csv (R01.csv ...). Returns per-marker (N,3)
    arrays for the 8 anchor antennas and the two moving wand markers, plus frame
    numbers and per-frame time (s). Trajectories section is ignored."""
    path = os.path.join(VIC, f"{stem}.csv")
    with open(path, newline='', encoding='utf-8-sig') as fh:
        rows = list(csv.reader(fh, delimiter='\t'))
    names = rows[2]
    col = {}
    for c in range(2, len(names)):
        n = names[c].strip()
        if n and n not in col:
            col[n] = c
    end = len(rows)
    for i in range(5, len(rows)):
        if rows[i] and rows[i][0].strip() == 'Trajectories':
            end = i
            break
    data = [rr for rr in rows[5:end] if rr and rr[0].strip()]

    def getxyz(marker):
        if marker not in col:
            return None
        cx = col[marker]
        pts = np.full((len(data), 3), np.nan)
        for i, rr in enumerate(data):
            try:
                pts[i] = (float(rr[cx]), float(rr[cx+1]), float(rr[cx+2]))
            except (ValueError, IndexError):
                pass
        return pts

    out = {}
    for L in LETTERS:
        out[L] = getxyz(f"Responder:{L}antenna")
    for m in TAG_MARKERS:
        out[m] = getxyz(f"Responder:{m}antenna")
        out[m + "_ctr"] = getxyz(f"Responder:{m}center")
    frame = np.array([float(rr[0]) for rr in data])
    out['frame'] = frame
    out['t'] = (frame - frame[0]) / FPS
    out['_path'] = path
    out['_nframes'] = len(data)
    return out

# ------------------------------------------------------------------ UWB parsing
def find_tr(capname, tag):
    hits = glob.glob(os.path.join(CAP, capname, "tag_capture_*", tag, "tr.csv"))
    return sorted(hits)[0] if hits else None

def parse_uwb(capname, tag):
    """Return per-sweep dict: sweep -> {'s':host_elapsed, 'epoch':host_epoch,
    'r':{anchor_id:range_mm}, 'raw':{anchor_id:raw_mm}, 'q':{anchor_id:quality}}.
    Valid rows only (valid==1, status=='O')."""
    path = find_tr(capname, tag)
    if not path:
        return None, None
    sweeps = {}
    with open(path, newline='') as fh:
        for row in csv.DictReader(fh):
            try:
                if int(row['valid']) != 1:
                    continue
            except (ValueError, KeyError):
                continue
            if row.get('status', 'O') != 'O':
                continue
            try:
                k = int(row['sweep']); a = int(row['anchor_id'])
                rng = float(row['range_mm']); s = float(row['host_elapsed_s'])
            except (ValueError, KeyError):
                continue
            if not (0 <= a < N_ANCHORS):
                continue
            d = sweeps.get(k)
            if d is None:
                d = sweeps[k] = {'s': s, 'epoch': float(row.get('host_epoch_s', 'nan') or 'nan'),
                                 'r': {}, 'raw': {}, 'q': {}}
            d['r'][a] = rng
            try:
                d['raw'][a] = float(row['raw_mm'])
            except (ValueError, KeyError):
                d['raw'][a] = rng
            try:
                d['q'][a] = float(row['quality_percent'])
            except (ValueError, KeyError):
                d['q'][a] = np.nan
    return sweeps, path

# ------------------------------------------------------------- geometry helpers
def anchor_positions(V):
    """Mean antenna position per anchor over valid frames."""
    return {L: np.nanmean(V[L], axis=0) for L in LETTERS}

def fit_plane_circle(P):
    """Fit plane + circle to a (N,3) trajectory. Returns dict with center, radius,
    plane normal, in-plane basis (u,v), tilt (deg from horizontal), out-of-plane
    rms, and per-point in-plane radius array."""
    m = np.all(np.isfinite(P), axis=1)
    Pg = P[m]
    c = Pg.mean(0)
    Q = Pg - c
    _, _, Vt = np.linalg.svd(Q, full_matrices=False)   # full_matrices=False: memory-safe
    normal = Vt[2]; u = Vt[0]; v = Vt[1]
    x = Q @ u; y = Q @ v
    A = np.c_[2*x, 2*y, np.ones(len(x))]
    b = x**2 + y**2
    sol, *_ = np.linalg.lstsq(A, b, rcond=None)
    cx, cy, cc = sol
    R = math.sqrt(max(cc + cx**2 + cy**2, 0.0))
    center = c + cx*u + cy*v
    tilt = math.degrees(math.acos(min(1.0, abs(normal[2]))))
    rad = np.sqrt((x - cx)**2 + (y - cy)**2)
    oop = Q @ normal
    return dict(center=center, radius=float(R), normal=normal, u=u, v=v,
                center_inplane=(float(cx), float(cy)),
                tilt_deg=float(tilt), rad_rms=float(np.std(rad)),
                oop_rms=float(np.std(oop)), n=int(m.sum()),
                zspan=float(Pg[:, 2].max() - Pg[:, 2].min()))

def phase_series(P, circ):
    """Continuous (unwrapped) rotation phase for each frame of trajectory P in the
    fitted circle plane. NaN frames -> nan phase."""
    Q = P - circ['center']
    x = Q @ circ['u']; y = Q @ circ['v']
    th = np.arctan2(y, x)
    return th   # wrapped [-pi,pi]; unwrap done after interpolation on valid stream

# ------------------------------------------------------------ interp / alignment
def _interp_pos(marker, frac_frame):
    """Linear interpolate a (Nf,3) Vicon marker at fractional frame indices."""
    n = len(marker)
    f = np.clip(frac_frame, 0, n - 1 - 1e-6)
    i0 = np.floor(f).astype(int)
    w = (f - i0)[:, None]
    return marker[i0] * (1 - w) + marker[i0 + 1] * w

def geom_range(marker, anc_pos, frac_frame):
    p = _interp_pos(marker, frac_frame)
    return np.linalg.norm(p - anc_pos, axis=1), p

def _stack_samples(sweeps):
    ks = sorted(sweeps)
    su = np.array([sweeps[k]['s'] for k in ks])
    a_idx = []; rr = []; sidx = []
    for i, k in enumerate(ks):
        for a, r in sweeps[k]['r'].items():
            a_idx.append(a); rr.append(r); sidx.append(i)
    return ks, su, np.array(a_idx), np.array(rr, float), np.array(sidx)

def _demeaned_rms_at(tau, su, a_idx, rr, sidx, marker, ancmat):
    frac = ((su + tau) * FPS)[sidx]
    g, _ = geom_range(marker, ancmat, frac)
    e = rr - g
    out = 0.0; ntot = 0
    for a in range(N_ANCHORS):
        m = a_idx == a
        ea = e[m]; ea = ea[np.isfinite(ea)]
        if ea.size > 1:
            out += np.sum((ea - ea.mean())**2); ntot += ea.size
    return math.sqrt(out / ntot) if ntot else 1e18

def global_align(sweeps, marker, anc, tau_grid=None):
    """Coarse global best-fit tau (per-anchor-demeaned). Reported for provenance
    only; the non-uniform time-warp below is what the analysis uses."""
    if tau_grid is None:
        tau_grid = np.arange(-3.0, 3.0, 0.02)
    ks, su, a_idx, rr, sidx = _stack_samples(sweeps)
    ancmat = np.array([anc[LETTERS[a]] for a in a_idx])
    best = (1e18, 0.0)
    for tau in tau_grid:
        r = _demeaned_rms_at(tau, su, a_idx, rr, sidx, marker, ancmat)
        if r < best[0]:
            best = (r, tau)
    return best[1], best[0]

def windowed_tau(sweeps, marker, anc, win=5.0, step=1.0, span=1.2, res=0.01):
    """Non-uniform local time-warp: fit ONE tau per sliding window (minimising the
    per-anchor-demeaned residual within that window), then interpolate a smooth
    per-sweep tau(t). Handles arbitrary non-uniform rotation / host-timestamp
    jitter that a single global offset cannot (see module docstring, C1).
    Returns (tau_per_sweep[nsweep], su, diag)."""
    ks, su, a_idx, rr, sidx = _stack_samples(sweeps)
    ancmat = np.array([anc[LETTERS[a]] for a in a_idx])
    # coarse global to center the local search
    g0, _ = global_align(sweeps, marker, anc, np.arange(-3.0, 3.0, 0.02))
    centers = np.arange(su[0] + win/2, su[-1] - win/2 + 1e-9, step)
    tau_c = []
    for c in centers:
        m = (su[sidx] >= c - win/2) & (su[sidx] <= c + win/2)
        if m.sum() < 20:
            tau_c.append(np.nan); continue
        sm, am, rm, sim = su, a_idx[m], rr[m], sidx[m]
        ancm = ancmat[m]
        best = (1e18, g0)
        for tau in np.arange(g0 - span, g0 + span, res):
            frac = ((sm + tau) * FPS)[sim]
            g, _ = geom_range(marker, ancm, frac)
            e = rm - g
            out = 0.0; nt = 0
            for a in range(N_ANCHORS):
                sel = am == a
                ea = e[sel]; ea = ea[np.isfinite(ea)]
                if ea.size > 1:
                    out += np.sum((ea - ea.mean())**2); nt += ea.size
            rms = math.sqrt(out / nt) if nt else 1e18
            if rms < best[0]:
                best = (rms, tau)
        # local refine
        t0 = best[1]
        for tau in np.arange(t0 - res, t0 + res, res/20):
            frac = ((su + tau) * FPS)[sim]
            g, _ = geom_range(marker, ancm, frac)
            e = rm - g; out = 0.0; nt = 0
            for a in range(N_ANCHORS):
                sel = am == a
                ea = e[sel]; ea = ea[np.isfinite(ea)]
                if ea.size > 1:
                    out += np.sum((ea - ea.mean())**2); nt += ea.size
            rms = math.sqrt(out / nt) if nt else 1e18
            if rms < best[0]:
                best = (rms, tau)
        tau_c.append(best[1])
    tau_c = np.array(tau_c)
    good = np.isfinite(tau_c)
    if good.sum() < 2:
        tau_sweep = np.full(len(su), g0)
    else:
        tau_sweep = np.interp(su, centers[good], tau_c[good])
    diag = dict(global_tau=float(g0), tau_start=float(tau_c[good][0]) if good.any() else float(g0),
                tau_end=float(tau_c[good][-1]) if good.any() else float(g0),
                tau_span=float(np.nanmax(tau_c) - np.nanmin(tau_c)) if good.any() else 0.0,
                n_windows=int(good.sum()))
    return tau_sweep, su, diag

def _scale_cost(sweeps, marker, anc, tau):
    """After global align, regress measured range on Vicon geom per anchor; correct
    marker has slope ~1.0. Cost = median|slope-1| + normalized residual. Cheap."""
    ks, su, a_idx, rr, sidx = _stack_samples(sweeps)
    frac = ((su + tau) * FPS)[sidx]
    slopes = []; resid = []
    for a in range(N_ANCHORS):
        sel = a_idx == a
        if sel.sum() < 20:
            continue
        gg = np.linalg.norm(_interp_pos(marker, frac[sel]) - anc[LETTERS[a]], axis=1)
        m = np.isfinite(rr[sel]) & np.isfinite(gg)
        if m.sum() < 20:
            continue
        A = np.polyfit(gg[m], rr[sel][m], 1)
        slopes.append(A[0]); resid.append(np.std(rr[sel][m] - np.polyval(A, gg[m])))
    if not slopes:
        return 1e18
    return float(np.median(np.abs(np.array(slopes) - 1.0)) + np.median(resid) / 1000.0)

def resolve_tag_mapping(sweeps_by_tag, V, anc):
    """Decide which UWB tag is which Vicon wand marker, per-capture. The correct
    marker has range-vs-geom slope ~1.0 (scale test) and lowest residual; the
    assignment is NOT assumed. Uses cheap global align + scale test.
    Returns {uwb_tag: marker}, per-(tag,marker) cost dict."""
    results = {}
    for tag in ROTO_TAGS:
        sw = sweeps_by_tag.get(tag)
        if not sw:
            continue
        for m in TAG_MARKERS:
            if V.get(m) is None or not np.isfinite(V[m]).any():
                results[(tag, m)] = 1e18; continue
            tau, _ = global_align(sw, V[m], anc, np.arange(-3.0, 3.0, 0.02))
            results[(tag, m)] = _scale_cost(sw, V[m], anc, tau)
    a1 = results.get((ROTO_TAGS[0], TAG_MARKERS[0]), 1e18) + results.get((ROTO_TAGS[1], TAG_MARKERS[1]), 1e18)
    a2 = results.get((ROTO_TAGS[0], TAG_MARKERS[1]), 1e18) + results.get((ROTO_TAGS[1], TAG_MARKERS[0]), 1e18)
    if a1 <= a2:
        mapping = {ROTO_TAGS[0]: TAG_MARKERS[0], ROTO_TAGS[1]: TAG_MARKERS[1]}
    else:
        mapping = {ROTO_TAGS[0]: TAG_MARKERS[1], ROTO_TAGS[1]: TAG_MARKERS[0]}
    return mapping, results

def elevation_deg(anc_pos, tag_pos):
    dz = np.abs(anc_pos[2] - tag_pos[:, 2])
    horiz = np.linalg.norm(anc_pos[:2] - tag_pos[:, :2], axis=1)
    return np.degrees(np.arctan2(dz, horiz))

def elev_bin(elev):
    """C3 bins: shallow <=25, unverified 25-37, steep >=37."""
    out = np.empty(elev.shape, dtype=object)
    out[elev <= 25] = 'shallow'
    out[(elev > 25) & (elev < 37)] = 'unverified'
    out[elev >= 37] = 'steep'
    return out
