#!/usr/bin/env python3
"""
Erlangen antenna-orientation analysis (ID13-ID24, tag BSF66F).

Read-only on the Erlangen capture data. Outputs results.json + tables into
experiments/antenna_orientation_erlangen/.

Data model
----------
* TR (UWB two-way ranging): one dir per ID
    captures/erlangen_20260528_optitrack/static_ID{n}_BSF66F_120s_*/
        tag_capture_*/BSF66F/tr.csv
  columns of interest: sweep, anchor_id (0..7), range_mm, valid, status(O/T)
* Vicon ("Model Outputs", 120 fps): opti_captures/full/ID{n}.csv
  Tracks Responder:A..H (the 8 anchors, antenna+center markers) and
  Responder:I (the moving tag BSF66F, antenna+center markers), all in one frame.
"""
import csv, json, glob, math, os, sys, time
import numpy as np
from scipy.optimize import least_squares

t0 = time.time()
ROOT = "/mnt/nrf_ssd/nRF_dev/BioSpur_UWB_before_start"
BASE = os.path.join(ROOT, "autopos_pipeline/28052026_Erlangen_Official")
CAP  = os.path.join(BASE, "captures/erlangen_20260528_optitrack")
VIC  = os.path.join(BASE, "opti_captures/full")
OUT  = os.path.join(ROOT, "experiments/antenna_orientation_erlangen")

IDS = list(range(13, 25))
HEIGHT = {13:'mid',14:'mid',15:'mid',16:'mid',
          17:'low',18:'low',19:'low',20:'low',
          21:'high',22:'high',23:'high',24:'high'}
ORIENT = {13:'ABEF',14:'BCGF',15:'CDHG',16:'ADHE',
          17:'ABEF',18:'BCGF',19:'CDHG',20:'ADHE',
          21:'ABEF',22:'BCGF',23:'CDHG',24:'ADHE'}
# yaw index in degrees for the 4 walls (90 deg steps, tag rotated in sequence)
YAW = {'ABEF':0, 'BCGF':90, 'CDHG':180, 'ADHE':270}
REF_ORIENT = 'ABEF'
LETTERS = 'ABCDEFGH'
HEIGHT_REF = {'mid':13, 'low':17, 'high':21}

# ------------------------------------------------------------------ TR parsing
def find_tr_csv(idn):
    dirs = glob.glob(os.path.join(CAP, f"static_ID{idn:02d}_BSF66F_*"))
    if not dirs:
        return None
    d = sorted(dirs)[0]
    hits = glob.glob(os.path.join(d, "tag_capture_*", "BSF66F", "tr.csv"))
    return sorted(hits)[0] if hits else None

def parse_tr(idn):
    """Return dict anchor_id -> {mean,std,median,n} using valid rows only."""
    path = find_tr_csv(idn)
    if not path:
        return None, None
    per = {a: [] for a in range(8)}
    nrows = 0
    with open(path, newline='') as fh:
        r = csv.DictReader(fh)
        for row in r:
            nrows += 1
            try:
                valid = int(row['valid'])
            except (ValueError, KeyError):
                continue
            if valid != 1:
                continue
            if row.get('status', 'O') != 'O':
                continue
            try:
                a = int(row['anchor_id']); rng = float(row['range_mm'])
            except (ValueError, KeyError):
                continue
            if 0 <= a < 8:
                per[a].append(rng)
    stats = {}
    for a in range(8):
        arr = np.array(per[a], dtype=float)
        if arr.size:
            stats[a] = dict(mean=float(arr.mean()), std=float(arr.std(ddof=1)) if arr.size>1 else 0.0,
                            median=float(np.median(arr)), n=int(arr.size))
        else:
            stats[a] = dict(mean=float('nan'), std=float('nan'), median=float('nan'), n=0)
    return stats, path

# --------------------------------------------------------------- Vicon parsing
def parse_vicon(idn):
    path = os.path.join(VIC, f"ID{idn}.csv")
    with open(path, newline='', encoding='utf-8-sig') as fh:
        rows = list(csv.reader(fh, delimiter='\t'))
    names = rows[2]
    # first occurrence of each marker (cols 2..) -> X column index
    col = {}
    for c in range(2, len(names)):
        n = names[c].strip()
        if n and n not in col:
            col[n] = c
    # The CSV has TWO sections: "Model Outputs" (clean, gap-filled, modeled) and
    # a later "Trajectories" section (raw markers, different column layout).
    # We only parse Model Outputs -> stop at the Trajectories header.
    end = len(rows)
    for i in range(5, len(rows)):
        if rows[i] and rows[i][0].strip() == 'Trajectories':
            end = i
            break
    data = []
    for rr in rows[5:end]:
        if not rr or not rr[0].strip():
            continue
        data.append(rr)
    ncol = max(len(x) for x in data)
    def getxyz(marker):
        cx = col[marker]
        pts = []
        for rr in data:
            try:
                x = float(rr[cx]); y = float(rr[cx+1]); z = float(rr[cx+2])
                pts.append((x, y, z))
            except (ValueError, IndexError):
                pts.append((np.nan, np.nan, np.nan))
        return np.array(pts, dtype=float)
    out = {}
    for L in LETTERS:
        out[L+'_ant'] = getxyz(f"Responder:{L}antenna")
        out[L+'_ctr'] = getxyz(f"Responder:{L}center")
    out['tag_ant'] = getxyz("Responder:Iantenna")
    out['tag_ctr'] = getxyz("Responder:Icenter")
    out['_nframes'] = len(data)
    out['_path'] = path
    return out

def nanmean_xyz(arr):
    return np.nanmean(arr, axis=0)

# --------------------------------------------------------------- trilateration
def trilaterate(anchor_pos, ranges, guess):
    """anchor_pos: (k,3), ranges: (k,), guess:(3,). Least squares."""
    m = np.isfinite(ranges)
    A = anchor_pos[m]; r = ranges[m]
    def resid(p):
        return np.linalg.norm(A - p, axis=1) - r
    sol = least_squares(resid, guess, method='lm', max_nfev=2000)
    return sol.x, float(np.sqrt(np.mean(resid(sol.x)**2)))

# ------------------------------------------------------------------- run all
tr = {}
tr_path = {}
vic = {}
for idn in IDS:
    s, p = parse_tr(idn)
    tr[idn] = s; tr_path[idn] = p
    vic[idn] = parse_vicon(idn)

# anchor positions per ID (mean antenna over frames); also global mean
anchor_pos = {}   # idn -> {L: xyz}
tag_ant = {}      # idn -> xyz (mean)
tag_ant_std = {}  # idn -> xyz std
tag_ctr = {}
tag_ctr_std = {}
for idn in IDS:
    v = vic[idn]
    anchor_pos[idn] = {L: nanmean_xyz(v[L+'_ant']) for L in LETTERS}
    ta = v['tag_ant']; tc = v['tag_ctr']
    tag_ant[idn] = np.nanmean(ta, axis=0)
    tag_ant_std[idn] = np.nanstd(ta, axis=0)
    tag_ctr[idn] = np.nanmean(tc, axis=0)
    tag_ctr_std[idn] = np.nanstd(tc, axis=0)

# global mean anchor positions (should be ~constant across IDs)
anchor_global = {}
for L in LETTERS:
    stack = np.array([anchor_pos[idn][L] for idn in IDS])
    anchor_global[L] = np.nanmean(stack, axis=0)
anchor_spread = {}   # max over IDs of |pos - global| per letter
for L in LETTERS:
    stack = np.array([anchor_pos[idn][L] for idn in IDS])
    anchor_spread[L] = float(np.nanmax(np.linalg.norm(stack - anchor_global[L], axis=1)))

# geometric range tag_antenna -> anchor_antenna (per ID)
def geom_ranges(idn, mapping):
    """mapping: anchor_id -> letter. returns array len 8."""
    p = tag_ant[idn]
    out = np.full(8, np.nan)
    for a in range(8):
        L = mapping[a]
        out[a] = np.linalg.norm(anchor_global[L] - p)
    return out

# --------------------------- determine anchor_id -> letter mapping
# UWB mean per id/anchor
uwb_mean = {idn: np.array([tr[idn][a]['mean'] for a in range(8)]) for idn in IDS}

identity = {a: LETTERS[a] for a in range(8)}
# residual for identity mapping, averaged over IDs
res_by_anchor = {a: [] for a in range(8)}
for idn in IDS:
    g = geom_ranges(idn, identity)
    for a in range(8):
        res_by_anchor[a].append(uwb_mean[idn][a] - g[a])
identity_bias = {a: float(np.nanmean(res_by_anchor[a])) for a in range(8)}

# brute-force best permutation minimizing spread of per-anchor bias
# (correct mapping => each anchor's UWB-geom bias is a small, consistent constant)
import itertools
best = None
# cost = sum over anchor of std of (uwb-geom) across IDs  + penalty for |bias| huge
def mapping_cost(mapping):
    tot = 0.0
    for a in range(8):
        vals = []
        for idn in IDS:
            g = np.linalg.norm(anchor_global[mapping[a]] - tag_ant[idn])
            vals.append(uwb_mean[idn][a] - g)
        vals = np.array(vals)
        tot += np.nanstd(vals) + 0.02*abs(np.nanmean(vals))
    return tot
# identity cost
id_cost = mapping_cost(identity)
# search permutations (8! = 40320, fast enough)
best_map, best_cost = identity, id_cost
for perm in itertools.permutations(LETTERS):
    mp = {a: perm[a] for a in range(8)}
    c = mapping_cost(mp)
    if c < best_cost:
        best_cost = c; best_map = mp
MAP = best_map
mapping_is_identity = (MAP == identity)

# ------------------------------------------------------------ per-ID stats table
per_id = {}
for idn in IDS:
    entry = {'height': HEIGHT[idn], 'orient': ORIENT[idn], 'yaw': YAW[ORIENT[idn]]}
    g = geom_ranges(idn, MAP)
    anchors = {}
    for a in range(8):
        L = MAP[a]
        anchors[L] = dict(anchor_id=a,
                          mean_mm=tr[idn][a]['mean'], std_mm=tr[idn][a]['std'],
                          median_mm=tr[idn][a]['median'], n=tr[idn][a]['n'],
                          geom_mm=float(g[a]),
                          bias_mm=float(tr[idn][a]['mean'] - g[a]))
    entry['anchors'] = anchors
    entry['n_frames_uwb'] = int(sum(tr[idn][a]['n'] for a in range(8)))
    per_id[idn] = entry

# ---------------- generalized orientation-effect machinery (raw vs bias) ------
# metric='mean_mm' -> raw UWB range (CONFOUNDED by any tag displacement)
# metric='bias_mm' -> UWB - geometric range to the actual Vicon antenna position
#                     (removes tag displacement; isolates antenna directionality)
HEIGHT_IDS = (('mid',[13,14,15,16]),('low',[17,18,19,20]),('high',[21,22,23,24]))

def getval(idn, L, metric):
    return per_id[idn]['anchors'][L][metric]

def verdict_rms(rms):
    if rms > 30: return "MAJOR (>30mm)"
    if rms >= 10: return "MODERATE (10-30mm)"
    return "NEGLIGIBLE (<10mm)"

def harmonic_fit(thetas_deg, values):
    th = np.radians(np.array(thetas_deg, dtype=float))
    y = np.array(values, dtype=float)
    m = np.isfinite(y)
    th = th[m]; y = y[m]
    if th.size < 3:
        return None
    M = np.column_stack([np.ones_like(th), np.cos(th), np.sin(th)])
    coef, *_ = np.linalg.lstsq(M, y, rcond=None)
    c0, A, B = coef
    amp = float(math.hypot(A, B))
    phi = float(math.degrees(math.atan2(B, A)) % 360)
    resid = y - M @ coef
    rms = float(np.sqrt(np.mean(resid**2))) if resid.size else 0.0
    return dict(c0=float(c0), amp=amp, phase_deg=phi, rms=rms, npts=int(th.size))

def compute_orientation_effect(metric):
    # 3.1 per-height delta vs ABEF ref
    height_tables = {}
    for h, ids in HEIGHT_IDS:
        ref = HEIGHT_REF[h]
        order = sorted(ids, key=lambda i: YAW[ORIENT[i]])
        tbl = {}
        for L in LETTERS:
            ref_val = getval(ref, L, metric)
            deltas = {ORIENT[i]: float(getval(i, L, metric) - ref_val) for i in order}
            tbl[L] = dict(ref_val_mm=float(ref_val), delta=deltas,
                          max_abs=float(max(abs(v) for o,v in deltas.items() if o != REF_ORIENT)))
        height_tables[h] = dict(ref_id=ref, order=order, table=tbl)
    # 3.2 effect size (all heights/orients)
    all_d = np.array([v for h in height_tables for L in LETTERS
                      for o,v in height_tables[h]['table'][L]['delta'].items() if o != REF_ORIENT])
    effect = dict(n=int(all_d.size), max_abs=float(np.max(np.abs(all_d))),
                  median_abs=float(np.median(np.abs(all_d))),
                  rms=float(np.sqrt(np.mean(all_d**2))), mean=float(np.mean(all_d)))
    effect['verdict'] = verdict_rms(effect['rms'])
    # 3.4 per-height RMS
    height_rms = {}
    for h in height_tables:
        ds = np.array([v for L in LETTERS
                       for o,v in height_tables[h]['table'][L]['delta'].items() if o != REF_ORIENT])
        height_rms[h] = dict(rms=float(np.sqrt(np.mean(ds**2))),
                             max_abs=float(np.max(np.abs(ds))), median_abs=float(np.median(np.abs(ds))))
    # 3.3 cosine fit per anchor (per height, then averaged)
    cosine_fit = {}
    for L in LETTERS:
        per_h = {}; amps = []; phases = []
        for h, ids in HEIGHT_IDS:
            fit = harmonic_fit([YAW[ORIENT[i]] for i in ids], [getval(i, L, metric) for i in ids])
            per_h[h] = fit
            if fit: amps.append(fit['amp']); phases.append(fit['phase_deg'])
        cosine_fit[L] = dict(per_height=per_h,
                             amp_mean=float(np.mean(amps)) if amps else float('nan'),
                             amp_max=float(np.max(amps)) if amps else float('nan'),
                             phase_mean_deg=float(np.mean(phases)) if phases else float('nan'))
    amps_all = [cosine_fit[L]['amp_mean'] for L in LETTERS]
    cosine_summary = dict(amp_mean_over_anchors=float(np.mean(amps_all)),
                          amp_median_over_anchors=float(np.median(amps_all)),
                          amp_max_over_anchors=float(np.max(amps_all)),
                          n_anchors_amp_gt20=int(sum(1 for a in amps_all if a > 20)))
    return dict(height_tables=height_tables, effect=effect, height_rms=height_rms,
                cosine_fit=cosine_fit, cosine_summary=cosine_summary)

raw  = compute_orientation_effect('mean_mm')   # naive / confounded
bias = compute_orientation_effect('bias_mm')   # geometry-corrected (primary)

# keep old names pointing at raw for backward compat of downstream/prints
height_tables = raw['height_tables']; effect = raw['effect']
height_rms = raw['height_rms']; cosine_fit = raw['cosine_fit']; cosine_summary = raw['cosine_summary']

# --------------------------- Task 3.5 Vicon ground truth
# A. tag didn't move
tag_move = {}
for idn in IDS:
    tag_move[idn] = dict(
        ctr_std_mm=[float(x) for x in tag_ctr_std[idn]],
        ctr_std_norm=float(np.linalg.norm(tag_ctr_std[idn])),
        ant_std_mm=[float(x) for x in tag_ant_std[idn]],
        ant_std_norm=float(np.linalg.norm(tag_ant_std[idn])),
        ctr_mean=[float(x) for x in tag_ctr[idn]],
        ant_mean=[float(x) for x in tag_ant[idn]],
    )
# inter-ID delta of tag center within same height
tag_interid = {}
for h, ids in (('mid',[13,14,15,16]),('low',[17,18,19,20]),('high',[21,22,23,24])):
    ref = HEIGHT_REF[h]
    for i in ids:
        d_ant = float(np.linalg.norm(tag_ant[i] - tag_ant[ref]))
        d_ctr = float(np.linalg.norm(tag_ctr[i] - tag_ctr[ref]))
        tag_interid[i] = dict(vs_ref=ref, ant_delta_mm=d_ant, ctr_delta_mm=d_ctr)

# B. position error per orientation (trilaterate UWB, compare to Vicon tag antenna)
pos_err = {}
anchor_mat_global = np.array([anchor_global[MAP[a]] for a in range(8)])
for idn in IDS:
    ranges = uwb_mean[idn]
    guess = tag_ant[idn]  # start near truth for stable convergence
    est, rms_fit = trilaterate(anchor_mat_global, ranges, guess)
    truth = tag_ant[idn]
    err = est - truth
    pos_err[idn] = dict(
        est=[float(x) for x in est], truth=[float(x) for x in truth],
        err_vec=[float(x) for x in err],
        err_norm=float(np.linalg.norm(err)),
        x_err=float(err[0]), y_err=float(err[1]), z_err=float(err[2]),
        fit_rms_mm=float(rms_fit),
        horiz_err=float(math.hypot(err[0], err[1])),
        horiz_dir_deg=float(math.degrees(math.atan2(err[1], err[0])) % 360),
    )

# C. orientation-induced position error spread per height
pos_spread = {}
for h, ids in (('mid',[13,14,15,16]),('low',[17,18,19,20]),('high',[21,22,23,24])):
    errs = [pos_err[i]['err_norm'] for i in ids]
    pos_spread[h] = dict(min=float(min(errs)), max=float(max(errs)),
                         spread=float(max(errs)-min(errs)),
                         per_orient={ORIENT[i]: pos_err[i]['err_norm'] for i in ids})

# D. does error vector rotate with tag yaw?
# Harmonic coherence: fit ex(yaw), ey(yaw) to 1st harmonic; coherence = fraction
# of horizontal-error variance explained by a yaw-locked rotating vector.
rotate_check = {}
for h, ids in (('mid',[13,14,15,16]),('low',[17,18,19,20]),('high',[21,22,23,24])):
    rows = []
    for i in ids:
        rows.append(dict(orient=ORIENT[i], yaw=YAW[ORIENT[i]],
                         horiz_dir_deg=pos_err[i]['horiz_dir_deg'],
                         horiz_err=pos_err[i]['horiz_err'],
                         err_vec=pos_err[i]['err_vec']))
    th = np.radians([YAW[ORIENT[i]] for i in ids])
    ex = np.array([pos_err[i]['x_err'] for i in ids])
    ey = np.array([pos_err[i]['y_err'] for i in ids])
    M = np.column_stack([np.ones_like(th), np.cos(th), np.sin(th)])
    cohs = []
    for comp in (ex, ey):
        coef,*_ = np.linalg.lstsq(M, comp, rcond=None)
        harm = M[:,1:] @ coef[1:]               # rotating part only
        var = float(np.var(comp))
        cohs.append(float(np.var(harm)/var) if var > 1e-9 else 0.0)
    rotate_check[h] = dict(rows=rows,
                           horiz_harmonic_coherence=float(np.mean(cohs)),
                           horiz_err_range=[float(min(r['horiz_err'] for r in rows)),
                                            float(max(r['horiz_err'] for r in rows))])

# Geometric-consistency check: does each anchor's cosine PHASE track its azimuth
# as seen from the tag? (Task 3.3). Use mid-height tag position as representative.
tagp = tag_ant[13]
anchor_azimuth = {}
for L in LETTERS:
    ap = anchor_global[L]
    anchor_azimuth[L] = float(math.degrees(math.atan2(ap[1]-tagp[1], ap[0]-tagp[0])) % 360)
phase_vs_azimuth = {}
for L in LETTERS:
    fit_phase = bias['cosine_fit'][L]['phase_mean_deg']
    diff = (fit_phase - anchor_azimuth[L] + 180) % 360 - 180
    phase_vs_azimuth[L] = dict(anchor_azimuth_deg=anchor_azimuth[L],
                               bias_fit_phase_deg=fit_phase,
                               phase_minus_azimuth_deg=float(diff))

# --------------------------- Task 4 wand caliper implication
# Use the geometry-corrected (bias) amplitudes: raw amplitudes at low height are
# contaminated by the 150-220mm tag displacement, so bias is the honest number.
A_amp = bias['cosine_summary']['amp_mean_over_anchors']
A_max = bias['cosine_summary']['amp_max_over_anchors']
A_amp_raw = raw['cosine_summary']['amp_mean_over_anchors']
caliper = dict(
    per_anchor_amp_mean_mm=A_amp,            # bias-based (primary)
    per_anchor_amp_max_mm=A_max,
    per_anchor_amp_mean_raw_mm=A_amp_raw,    # raw (confounded, for reference)
    max_ccf4_vs_955a_diff_mm=float(2*A_amp),   # 2x amplitude (180 deg opposed)
    max_ccf4_vs_955a_diff_worst_mm=float(2*A_max),
    observed_caliper_fail_mm=324.0,
    fraction_explained_mean=float(2*A_amp/324.0),
    fraction_explained_worst=float(2*A_max/324.0),
)

# ------------------------------------------------------------------ assemble
results = dict(
    meta=dict(
        generated_by="experiments/antenna_orientation_erlangen/analyze.py",
        session="erlangen_20260528_optitrack (Vicon 2026-05-28)",
        tag="BSF66F",
        ids=IDS,
        note_duration="captures are 120s each (session_notes duration_s=120), not 60s",
        anchor_id_letter_map={a: MAP[a] for a in range(8)},
        mapping_is_identity=mapping_is_identity,
        mapping_cost=float(best_cost), identity_cost=float(id_cost),
        wall_pointing="ABEF=-X, BCGF=-Y, CDHG=+X, ADHE=+Y (from anchor geometry)",
    ),
    anchor_global={L: [float(x) for x in anchor_global[L]] for L in LETTERS},
    anchor_spread_mm=anchor_spread,
    identity_bias_mm=identity_bias,
    per_id=per_id,
    # raw (naive) orientation effect on measured range -- CONFOUNDED by tag motion
    raw=dict(height_tables=raw['height_tables'], effect_size=raw['effect'],
             height_rms=raw['height_rms'], cosine_fit=raw['cosine_fit'],
             cosine_summary=raw['cosine_summary']),
    # bias (geometry-corrected) orientation effect -- PRIMARY antenna-directionality metric
    bias=dict(height_tables=bias['height_tables'], effect_size=bias['effect'],
              height_rms=bias['height_rms'], cosine_fit=bias['cosine_fit'],
              cosine_summary=bias['cosine_summary']),
    tag_move=tag_move,
    tag_interid=tag_interid,
    pos_err=pos_err,
    pos_spread=pos_spread,
    rotate_check=rotate_check,
    anchor_azimuth_from_tag_deg=anchor_azimuth,
    phase_vs_azimuth=phase_vs_azimuth,
    caliper=caliper,
    runtime_s=None,
)
results['runtime_s'] = time.time() - t0

with open(os.path.join(OUT, "results.json"), "w") as fh:
    json.dump(results, fh, indent=2)

# ------------------------------------------------------------------ console
print(f"runtime {results['runtime_s']:.1f}s")
print("anchor_id->letter:", {a: MAP[a] for a in range(8)}, "identity:", mapping_is_identity)
print("anchor spread across IDs (mm):", {L: round(anchor_spread[L],1) for L in LETTERS})
print("identity per-anchor UWB-geom bias (mm):", {LETTERS[a]: round(identity_bias[a],1) for a in range(8)})
print()
print("=== Effect size: RAW UWB delta vs ABEF ref (CONFOUNDED by tag motion) ===")
print(" ", raw['effect'])
print("  per-height RMS:", {h: round(raw['height_rms'][h]['rms'],1) for h in raw['height_rms']})
print("=== Effect size: BIAS (geometry-corrected) = antenna directionality ===")
print(" ", bias['effect'])
print("  per-height RMS:", {h: round(bias['height_rms'][h]['rms'],1) for h in bias['height_rms']})
print()
print("=== Cosine amplitude per anchor (mm) : raw | bias ===")
for L in LETTERS:
    print(f"  {L}: raw amp_mean={raw['cosine_fit'][L]['amp_mean']:6.1f}  |  bias amp_mean={bias['cosine_fit'][L]['amp_mean']:6.1f} phase={bias['cosine_fit'][L]['phase_mean_deg']:5.0f}deg")
print("  raw  cosine summary:", raw['cosine_summary'])
print("  bias cosine summary:", bias['cosine_summary'])
print()
print("=== Tag movement (Vicon) ===")
for idn in IDS:
    tm = tag_move[idn]
    print(f"  ID{idn} {HEIGHT[idn]:>4} {ORIENT[idn]}: ctr_std={tm['ctr_std_norm']:.2f}mm ant_std={tm['ant_std_norm']:.2f}mm  interID_ctr={tag_interid[idn]['ctr_delta_mm']:.1f}mm ant={tag_interid[idn]['ant_delta_mm']:.1f}mm")
print()
print("=== Position error per ID ===")
for idn in IDS:
    pe = pos_err[idn]
    print(f"  ID{idn} {HEIGHT[idn]:>4} {ORIENT[idn]}: err={pe['err_norm']:.1f}mm (x{pe['x_err']:+.0f} y{pe['y_err']:+.0f} z{pe['z_err']:+.0f}) horiz={pe['horiz_err']:.0f}@{pe['horiz_dir_deg']:.0f}deg fit_rms={pe['fit_rms_mm']:.1f}")
print("pos spread per height:", {h: round(pos_spread[h]['spread'],1) for h in pos_spread})
print("horiz-error harmonic coherence w/ yaw:", {h: round(rotate_check[h]['horiz_harmonic_coherence'],2) for h in rotate_check},
      " horiz_err small (mm):", {h: [round(x) for x in rotate_check[h]['horiz_err_range']] for h in rotate_check})
print()
print("=== Geometric consistency: bias cosine phase vs anchor azimuth-from-tag ===")
for L in LETTERS:
    pv = phase_vs_azimuth[L]
    print(f"  {L}: azimuth={pv['anchor_azimuth_deg']:5.0f}deg  fit_phase={pv['bias_fit_phase_deg']:5.0f}deg  diff={pv['phase_minus_azimuth_deg']:+5.0f}deg")
print()
print("=== Caliper implication ===")
print(caliper)
print("\nWROTE", os.path.join(OUT, "results.json"))
