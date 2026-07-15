#!/usr/bin/env python3
"""
Erlangen antenna-orientation FOLLOW-UP analysis (ID13-ID24, tag BSF66F).

Tests the ELEVATION HYPOTHESIS that the aggregate "MAJOR, RMS=128mm" verdict of
the first report actually hides TWO layers:
  L1 (smooth, ~30-60mm) : azimuth/yaw antenna ripple, all links.
  L2 (discrete, 300-500mm): first-path null on STEEP cross-layer links -> LDE
                            locks a reflection.

Pure software, read-only on all Erlangen capture data. Reuses the vetted
geometry (anchor positions, per-cell BIAS metric, anchor_id->letter map) from
../results.json and re-parses tr.csv per-sweep for the Layer-2 time-series work.
Outputs results_followup.json + figures into this followup/ directory.
"""
import csv, json, glob, math, os, time, resource
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from scipy import stats as sstats

t0 = time.time()
ROOT = "/mnt/nrf_ssd/nRF_dev/BioSpur_UWB_before_start"
BASE = os.path.join(ROOT, "autopos_pipeline/28052026_Erlangen_Official")
CAP  = os.path.join(BASE, "captures/erlangen_20260528_optitrack")
EXP  = os.path.join(ROOT, "experiments/antenna_orientation_erlangen")
OUT  = os.path.join(EXP, "followup")
PREV = json.load(open(os.path.join(EXP, "results.json")))

IDS = list(range(13, 25))
LETTERS = 'ABCDEFGH'
HEIGHT = {13:'mid',14:'mid',15:'mid',16:'mid',
          17:'low',18:'low',19:'low',20:'low',
          21:'high',22:'high',23:'high',24:'high'}
ORIENT = {13:'ABEF',14:'BCGF',15:'CDHG',16:'ADHE',
          17:'ABEF',18:'BCGF',19:'CDHG',20:'ADHE',
          21:'ABEF',22:'BCGF',23:'CDHG',24:'ADHE'}
YAW = {'ABEF':0, 'BCGF':90, 'CDHG':180, 'ADHE':270}
ORDER = ['ABEF','BCGF','CDHG','ADHE']          # yaw 0,90,180,270
REF_ORIENT = 'ABEF'
HEIGHT_REF = {'mid':13, 'low':17, 'high':21}
HEIGHTS = ['mid','low','high']
LOW_RING  = set('ABCD')   # anchors mounted low  (z~240)
HIGH_RING = set('EFGH')   # anchors mounted high (z~1650)
MAP = {int(k): v for k, v in PREV['meta']['anchor_id_letter_map'].items()}  # anchor_id->letter
LET2ID = {v: k for k, v in MAP.items()}

# ------- geometry & BIAS metric straight from the vetted previous results -------
anchor_global = {L: np.array(PREV['anchor_global'][L], float) for L in LETTERS}
tag_ant = {i: np.array(PREV['tag_move'][str(i)]['ant_mean'], float) for i in IDS}
# absolute BIAS per (id, anchor) = UWB mean - geometric range to Vicon antenna
abias = {i: {L: PREV['per_id'][str(i)]['anchors'][L]['bias_mm'] for L in LETTERS} for i in IDS}
# BIAS orientation delta vs same-height ABEF ref  (keyed [height][anchor][orient])
bdelta = {h: {L: dict(PREV['bias']['height_tables'][h]['table'][L]['delta']) for L in LETTERS}
          for h in HEIGHTS}
abef_bias = {h: {L: PREV['bias']['height_tables'][h]['table'][L]['ref_val_mm'] for L in LETTERS}
             for h in HEIGHTS}

def id_of(h, o):
    return next(i for i in IDS if HEIGHT[i] == h and ORIENT[i] == o)

def link_geom(idn, L):
    """elevation angle (deg) and 3D length (mm) for the tag(idn)->anchor(L) link."""
    a = anchor_global[L]; t = tag_ant[idn]
    dz = abs(a[2] - t[2]); horiz = math.hypot(a[0]-t[0], a[1]-t[1])
    el = math.degrees(math.atan2(dz, horiz))
    d3 = float(np.linalg.norm(a - t))
    return el, d3

def layer_of(h, L):
    """same-layer vs cross-layer: tag ring vs anchor ring (mid tag ~ high side)."""
    tag_ring = 'high' if h in ('high','mid') else 'low'   # mid tag z~1122 sits high
    anch_ring = 'low' if L in LOW_RING else 'high'
    return 'cross' if tag_ring != anch_ring else 'same'

# ===========================================================================
# TASK 1 - ELEVATION HYPOTHESIS
# ===========================================================================
# 72 BIAS deltas (8 anchors x 3 non-ref orients x 3 heights), each tagged with
# the elevation / 3D length of its own orientation-ID link.
rows = []
for h in HEIGHTS:
    for L in LETTERS:
        for o in ORDER:
            if o == REF_ORIENT:
                continue
            idn = id_of(h, o)
            el, d3 = link_geom(idn, L)
            rows.append(dict(height=h, orient=o, anchor=L, idn=idn,
                             dbias=bdelta[h][L][o], adbias=abs(bdelta[h][L][o]),
                             elev=el, dist=d3, layer=layer_of(h, L),
                             steep=(el >= 30.0)))
adbias = np.array([r['adbias'] for r in rows])
elev   = np.array([r['elev']   for r in rows])
dist   = np.array([r['dist']   for r in rows])

rho_el, p_el = sstats.spearmanr(elev, adbias)
rho_di, p_di = sstats.spearmanr(dist, adbias)
rho_ed, p_ed = sstats.spearmanr(elev, dist)          # elevation<->distance confound
# partial Spearman of |dbias| vs elevation controlling for distance (rank-based)
def partial_spearman(x, y, z):
    rx = sstats.rankdata(x); ry = sstats.rankdata(y); rz = sstats.rankdata(z)
    def res(a, b):
        b1 = np.polyfit(b, a, 1); return a - np.polyval(b1, b)
    return float(np.corrcoef(res(rx, rz), res(ry, rz))[0, 1])
partial_el = partial_spearman(adbias, elev, dist)    # control distance
partial_di = partial_spearman(adbias, dist, elev)    # control elevation

def split_stats(mask):
    v = adbias[mask]
    if v.size == 0:
        return dict(n=0, median=None, rms=None, max=None)
    return dict(n=int(v.size), median=float(np.median(v)),
                rms=float(np.sqrt(np.mean(v**2))), max=float(np.max(v)))

t1 = dict(
    n=len(rows),
    spearman_elev=dict(rho=float(rho_el), p=float(p_el)),
    spearman_dist=dict(rho=float(rho_di), p=float(p_di)),
    spearman_elev_vs_dist=dict(rho=float(rho_ed), p=float(p_ed)),
    partial_elev_given_dist=partial_el,
    partial_dist_given_elev=partial_di,
    split_30deg=dict(shallow=split_stats(elev < 30), steep=split_stats(elev >= 30)),
    split_layer=dict(same=split_stats(np.array([r['layer']=='same' for r in rows])),
                     cross=split_stats(np.array([r['layer']=='cross' for r in rows]))),
    rows=rows,
)

# 1.4 absolute ABEF baseline bias vs elevation (24 anchor x height cells)
ab_rows = []
for h in HEIGHTS:
    refid = HEIGHT_REF[h]
    for L in LETTERS:
        el, d3 = link_geom(refid, L)
        ab_rows.append(dict(height=h, anchor=L, abs_bias=abef_bias[h][L],
                            elev=el, dist=d3))
ab_bias_arr = np.array([r['abs_bias'] for r in ab_rows])
ab_elev_arr = np.array([r['elev'] for r in ab_rows])
ab_dist_arr = np.array([r['dist'] for r in ab_rows])
rho_ab_el, p_ab_el = sstats.spearmanr(ab_elev_arr, ab_bias_arr)
rho_ab_di, p_ab_di = sstats.spearmanr(ab_dist_arr, ab_bias_arr)
t1['abef_baseline'] = dict(
    n=len(ab_rows), rows=ab_rows,
    spearman_elev=dict(rho=float(rho_ab_el), p=float(p_ab_el)),
    spearman_dist=dict(rho=float(rho_ab_di), p=float(p_ab_di)),
    bias_range_mm=[float(ab_bias_arr.min()), float(ab_bias_arr.max())],
)

# ---- figure: elevation scatter (color=height, marker by same/cross layer) ----
fig, ax = plt.subplots(1, 2, figsize=(12, 5))
hcol = {'mid':'#1b9e77','low':'#d95f02','high':'#7570b3'}
mmark = {'same':'o','cross':'^'}
for r in rows:
    ax[0].scatter(r['elev'], r['adbias'], c=hcol[r['height']], marker=mmark[r['layer']],
                  s=70, edgecolor='k', linewidth=0.4, alpha=0.85)
ax[0].axvline(30, ls='--', c='grey'); ax[0].axhline(150, ls=':', c='red', alpha=0.6)
ax[0].set_xlabel('link elevation angle (deg)'); ax[0].set_ylabel('|BIAS orientation Δ| (mm)')
ax[0].set_title(f'|Δ| vs elevation  (Spearman ρ={rho_el:.2f}, p={p_el:.1e})')
from matplotlib.lines import Line2D
leg = [Line2D([0],[0],marker='o',color='w',markerfacecolor=hcol[h],markeredgecolor='k',
              label=f'{h} tag',markersize=9) for h in HEIGHTS]
leg += [Line2D([0],[0],marker=mmark[k],color='w',markerfacecolor='grey',markeredgecolor='k',
               label=f'{k}-layer',markersize=9) for k in ('same','cross')]
ax[0].legend(handles=leg, fontsize=8, loc='upper left')
for r in rows:
    ax[1].scatter(r['dist'], r['adbias'], c=hcol[r['height']], marker=mmark[r['layer']],
                  s=70, edgecolor='k', linewidth=0.4, alpha=0.85)
ax[1].set_xlabel('link 3D length (mm)'); ax[1].set_ylabel('|BIAS orientation Δ| (mm)')
ax[1].set_title(f'|Δ| vs distance  (Spearman ρ={rho_di:.2f}, p={p_di:.1e})')
fig.suptitle('Erlangen orientation Δ: elevation is the driver, not range', fontweight='bold')
fig.tight_layout()
fig.savefig(os.path.join(OUT, 'elevation_correlation.png'), dpi=130)
plt.close(fig)

# ===========================================================================
# TASK 2 - HARMONIC DECOMPOSITION (exact, 4 samples @ 90deg)
# ===========================================================================
def harmonic4(vals):
    """vals in yaw order [0,90,180,270]. Exact DFT of 4 equally spaced pts."""
    v0, v90, v180, v270 = vals
    c0 = (v0 + v90 + v180 + v270) / 4.0
    a1 = (v0 - v180) / 2.0
    b1 = (v90 - v270) / 2.0
    c2 = (v0 - v90 + v180 - v270) / 4.0
    A1 = math.hypot(a1, b1)
    phi1 = math.degrees(math.atan2(b1, a1)) % 360.0
    # total variance about c0 = A1^2/2 + c2^2 ; var explained by 1st harmonic:
    denom = A1**2 + 2*c2**2
    varexp = (A1**2 / denom) if denom > 1e-12 else float('nan')
    return dict(c0=c0, a1=a1, b1=b1, c2=c2, A1=A1, phi1=phi1,
                abs_c2=abs(c2), var_explained=varexp)

harm = {}          # [anchor][height]
n_bad_cos = 0
for L in LETTERS:
    harm[L] = {}
    for h in HEIGHTS:
        vals = [abias[id_of(h, o)][L] for o in ORDER]   # absolute bias, yaw order
        harm[L][h] = harmonic4(vals)
        if harm[L][h]['abs_c2'] >= 0.5 * harm[L][h]['A1']:
            n_bad_cos += 1
n_cells = len(LETTERS) * len(HEIGHTS)

# 2.2 mid+high-only effect size on the deltas (48 non-ref deltas)
mh = np.array([r['adbias'] for r in rows if r['height'] in ('mid','high')])
alld = np.array([r['adbias'] for r in rows])
midonly = np.array([r['adbias'] for r in rows if r['height'] == 'mid'])
def eff(v):
    return dict(n=int(v.size), rms=float(np.sqrt(np.mean(v**2))),
                median=float(np.median(v)), max=float(np.max(v)))

# 2.3 phase vs azimuth PER HEIGHT (no cross-height averaging)
phase_vs_az = {}
for h in HEIGHTS:
    refid = HEIGHT_REF[h]
    tp = tag_ant[refid]
    per = {}
    diffs = []
    for L in LETTERS:
        ap = anchor_global[L]
        az = math.degrees(math.atan2(ap[1]-tp[1], ap[0]-tp[0])) % 360
        phi = harm[L][h]['phi1']
        d = (phi - az + 180) % 360 - 180
        per[L] = dict(phi1=phi, azimuth=az, phi_minus_az=d, A1=harm[L][h]['A1'])
        diffs.append(d)
    # circular concentration of (phi-az): |mean resultant| in [0,1]; 1 => phase tracks azimuth
    ang = np.radians(diffs)
    R = float(np.hypot(np.mean(np.cos(ang)), np.mean(np.sin(ang))))
    phase_vs_az[h] = dict(per_anchor=per, resultant_R=R,
                          circ_std_deg=float(math.degrees(math.sqrt(-2*math.log(max(R,1e-9))))))

t2 = dict(
    harmonic=harm, n_cells=n_cells, n_bad_cosine=n_bad_cos,
    frac_bad_cosine=n_bad_cos / n_cells,
    effect_all=eff(alld), effect_mid_high=eff(mh), effect_mid_only=eff(midonly),
    phase_vs_azimuth=phase_vs_az,
)

# ---- figure: harmonic heatmap (A1, |c2|, var_explained) ----
fig, axs = plt.subplots(1, 3, figsize=(15, 4.2))
A1m = np.array([[harm[L][h]['A1'] for h in HEIGHTS] for L in LETTERS])
c2m = np.array([[harm[L][h]['abs_c2'] for h in HEIGHTS] for L in LETTERS])
vem = np.array([[harm[L][h]['var_explained'] for h in HEIGHTS] for L in LETTERS])
for ax_, M, title, cmap, fmt in [
        (axs[0], A1m, 'A1  first-harmonic amp (mm)', 'viridis', '%.0f'),
        (axs[1], c2m, '|c2|  cosine misfit (mm)', 'magma', '%.0f'),
        (axs[2], vem, 'variance explained by 1st harmonic', 'RdYlGn', '%.2f')]:
    im = ax_.imshow(M, aspect='auto', cmap=cmap)
    ax_.set_xticks(range(3)); ax_.set_xticklabels(HEIGHTS)
    ax_.set_yticks(range(8)); ax_.set_yticklabels(list(LETTERS))
    ax_.set_title(title)
    for i in range(8):
        for j in range(3):
            ax_.text(j, i, fmt % M[i, j], ha='center', va='center',
                     color='white' if cmap != 'RdYlGn' else 'black', fontsize=8)
    fig.colorbar(im, ax=ax_, fraction=0.046)
fig.suptitle('Harmonic decomposition of BIAS vs yaw (per anchor x height)', fontweight='bold')
fig.tight_layout()
fig.savefig(os.path.join(OUT, 'harmonic_heatmap.png'), dpi=130)
plt.close(fig)

# ===========================================================================
# TASK 3 - DIRECT 180-degree PAIRS
# ===========================================================================
# Pair1 ABEF<->CDHG (0<->180); Pair2 BCGF<->ADHE (90<->270).
# 180-diff of absolute BIAS = for pair1: bias(CDHG)-bias(ABEF)=bdelta(CDHG);
# for pair2: bias(ADHE)-bias(BCGF)=bdelta(ADHE)-bdelta(BCGF).
pair_tbl = {}
for L in LETTERS:
    pair_tbl[L] = {}
    for h in HEIGHTS:
        p1 = bdelta[h][L]['CDHG'] - bdelta[h][L]['ABEF']   # ABEF delta is 0
        p2 = bdelta[h][L]['ADHE'] - bdelta[h][L]['BCGF']
        el_ref, _ = link_geom(HEIGHT_REF[h], L)
        pair_tbl[L][h] = dict(p1=float(p1), p2=float(p2),
                              max_abs=float(max(abs(p1), abs(p2))), elev=el_ref)

def caliper_from(vals):
    a = np.abs(np.array(vals))
    return dict(n=int(a.size), typical_median=float(np.median(a)),
                typical_rms=float(np.sqrt(np.mean(a**2))), worst=float(np.max(a)))

mid_splits = [pair_tbl[L]['mid'][k] for L in LETTERS for k in ('p1','p2')]
all_splits = [pair_tbl[L][h][k] for L in LETTERS for h in HEIGHTS for k in ('p1','p2')]
# steep vs shallow 180-splits by ref-link elevation
steep_splits = [pair_tbl[L][h][k] for L in LETTERS for h in HEIGHTS for k in ('p1','p2')
                if pair_tbl[L][h]['elev'] >= 30]
shallow_splits = [pair_tbl[L][h][k] for L in LETTERS for h in HEIGHTS for k in ('p1','p2')
                  if pair_tbl[L][h]['elev'] < 30]
CALIPER_FAIL = 324.0
cal_mid = caliper_from(mid_splits)
cal_all = caliper_from(all_splits)
cal_steep = caliper_from(steep_splits)
cal_shallow = caliper_from(shallow_splits)

# ---- 3.4 HOME geometry elevation check ----
home_layout_path = os.path.join(ROOT, "logs/system_calibration_20260710_233443/anchor_layout.json")
wandpos_path = os.path.join(ROOT, "logs/overnight_radar_20260711/wand_recapture/wand_positions_updated.json")
home = {'available': False}
if os.path.exists(home_layout_path) and os.path.exists(wandpos_path):
    hl = json.load(open(home_layout_path))
    wp = json.load(open(wandpos_path))
    hanch = {a['label']: np.array([a['x_mm'], a['y_mm'], a['z_mm']], float) for a in hl['anchors']}
    links = []
    for tg in wp['tags']:
        tpos = np.array(tg['position_mm'], float)
        for lab, apos in hanch.items():
            dz = abs(apos[2]-tpos[2]); horiz = math.hypot(apos[0]-tpos[0], apos[1]-tpos[1])
            el = math.degrees(math.atan2(dz, horiz))
            links.append(dict(tag=tg['tag_name'], anchor=lab, elev=float(el),
                              dist=float(np.linalg.norm(apos-tpos)), steep=bool(el >= 30)))
    els = np.array([l['elev'] for l in links])
    home = dict(available=True, layout=os.path.relpath(home_layout_path, ROOT),
                wandpos=os.path.relpath(wandpos_path, ROOT),
                n_links=len(links), n_steep=int((els >= 30).sum()),
                frac_steep=float((els >= 30).mean()),
                elev_min=float(els.min()), elev_max=float(els.max()),
                elev_median=float(np.median(els)),
                per_tag={tg['tag_name']: dict(
                    n_steep=int(sum(1 for l in links if l['tag']==tg['tag_name'] and l['steep'])),
                    n=int(sum(1 for l in links if l['tag']==tg['tag_name'])),
                    elevs=[round(l['elev'],1) for l in links if l['tag']==tg['tag_name']])
                    for tg in wp['tags']},
                links=links)

t3 = dict(pair_table=pair_tbl, caliper_fail_mm=CALIPER_FAIL,
          caliper_mid=cal_mid, caliper_all=cal_all,
          caliper_steep=cal_steep, caliper_shallow=cal_shallow,
          frac_explained_mid=dict(typical=cal_mid['typical_rms']/CALIPER_FAIL,
                                  worst=cal_mid['worst']/CALIPER_FAIL),
          frac_explained_all=dict(typical=cal_all['typical_rms']/CALIPER_FAIL,
                                  worst=cal_all['worst']/CALIPER_FAIL),
          home_geometry=home)

# ===========================================================================
# TASK 4 - CLASSIFY LAYER-2 CELLS (|bias Δ| > 150 mm)
# ===========================================================================
def find_tr_csv(idn):
    dirs = glob.glob(os.path.join(CAP, f"static_ID{idn:02d}_BSF66F_*"))
    if not dirs:
        return None
    hits = glob.glob(os.path.join(sorted(dirs)[0], "tag_capture_*", "BSF66F", "tr.csv"))
    return sorted(hits)[0] if hits else None

def sweep_series(idn, anchor_id):
    """ordered per-sweep valid range series (mm) plus host_elapsed_s for anchor."""
    path = find_tr_csv(idn)
    ts, rs = [], []
    with open(path, newline='') as fh:
        for row in csv.DictReader(fh):
            try:
                if int(row['valid']) != 1 or row.get('status','O') != 'O':
                    continue
                if int(row['anchor_id']) != anchor_id:
                    continue
                rs.append(float(row['range_mm'])); ts.append(float(row['host_elapsed_s']))
            except (ValueError, KeyError):
                continue
    return np.array(ts), np.array(rs)

def kmeans1d(x, iters=50):
    """1-D 2-means; returns centers, labels."""
    x = np.asarray(x, float)
    c = np.array([np.percentile(x, 25), np.percentile(x, 75)])
    if c[0] == c[1]:
        c = np.array([x.min(), x.max()])
    lab = np.zeros(len(x), int)
    for _ in range(iters):
        lab = (np.abs(x[:, None] - c[None, :]).argmin(1))
        for k in (0, 1):
            if (lab == k).any():
                c[k] = x[lab == k].mean()
    return c, lab

def classify_series(r):
    """return dict of shape metrics + verdict for a per-sweep range series."""
    n = len(r)
    med = float(np.median(r)); std = float(np.std(r, ddof=1))
    iqr = float(np.percentile(r, 75) - np.percentile(r, 25))
    # bimodality coefficient (Sarle): b = (g^2 + 1)/k ; >0.555 => bimodal-ish
    g = float(sstats.skew(r)); k = float(sstats.kurtosis(r, fisher=False))
    bc = (g**2 + 1) / k if k > 0 else float('nan')
    # 2-means separation
    c, lab = kmeans1d(r)
    frac1 = float((lab == 0).mean()); frac2 = float((lab == 1).mean())
    within = np.sqrt(np.mean([(r[lab==j] - c[j]).var() if (lab==j).any() else 0 for j in (0,1)]))
    sep = float(abs(c[1]-c[0]) / (within + 1e-6))
    minor = min(frac1, frac2)
    bimodal = (sep > 3.0 and minor > 0.12 and abs(c[1]-c[0]) > 40)
    # time stability: rolling mean over ~5 s windows (series is time-ordered)
    win = max(5, n // 24)
    roll = np.convolve(r, np.ones(win)/win, mode='valid') if n >= win else r
    drift = float(roll.max() - roll.min())
    # step: largest jump between consecutive rolling means
    step = float(np.max(np.abs(np.diff(roll)))) if roll.size > 1 else 0.0
    return dict(n=n, median=med, std=std, iqr=iqr, skew=g, kurtosis=k,
                bimodality_coeff=float(bc), km_centers=[float(c[0]), float(c[1])],
                km_frac=[frac1, frac2], km_sep_sigma=sep, km_gap_mm=float(abs(c[1]-c[0])),
                roll_drift_mm=drift, roll_max_step_mm=step, bimodal=bool(bimodal),
                roll=roll)

# room bounds from Vicon anchor extents (rough single-bounce reflector model)
axyz = np.array([anchor_global[L] for L in LETTERS])
room = dict(zfloor=float(min(0.0, axyz[:,2].min())),      # low anchors ~240 -> floor ~0
            zceil=float(axyz[:,2].max() + 800.0),          # ceiling ~800mm above high ring
            xmin=float(axyz[:,0].min()), xmax=float(axyz[:,0].max()),
            ymin=float(axyz[:,1].min()), ymax=float(axyz[:,1].max()))

def reflection_excess(idn, L, plane):
    """excess path (mm) of a single specular bounce off `plane` vs direct link.
    plane in {'floor','ceil','xmin','xmax','ymin','ymax'}."""
    a = anchor_global[L].copy(); t = tag_ant[idn].copy()
    img = t.copy()
    if plane == 'floor':  img[2] = 2*room['zfloor'] - t[2]
    elif plane == 'ceil': img[2] = 2*room['zceil']  - t[2]
    elif plane == 'xmin': img[0] = 2*room['xmin'] - t[0]
    elif plane == 'xmax': img[0] = 2*room['xmax'] - t[0]
    elif plane == 'ymin': img[1] = 2*room['ymin'] - t[1]
    elif plane == 'ymax': img[1] = 2*room['ymax'] - t[1]
    direct = np.linalg.norm(a - t)
    refl = np.linalg.norm(a - img)
    return float(refl - direct)

L2_THRESH = 150.0
layer2 = []
for r in rows:
    if r['adbias'] > L2_THRESH:
        layer2.append(r)
layer2.sort(key=lambda r: -r['adbias'])

l2_cells = []
for r in layer2:
    h, o, L, idn = r['height'], r['orient'], r['anchor'], r['idn']
    aid = LET2ID[L]
    ts, rs = sweep_series(idn, aid)
    cls = classify_series(rs)
    # 4.3 orientation-specificity: this cell's abs bias vs the other 3 orients at same h,anchor
    others = {oo: abias[id_of(h, oo)][L] for oo in ORDER}
    other_vals = [v for oo, v in others.items() if oo != o]
    spec = float(abs(others[o] - np.median(other_vals)))
    graded = np.std(other_vals) > 60  # are the others also spread?
    abef_val = others[REF_ORIENT]
    abef_is_max = abef_val >= max(others.values()) - 1e-6
    true_baseline = float(np.median([v for oo, v in others.items() if oo != REF_ORIENT and v == v]))
    # 4.4 reflector plausibility (only meaningful for a positive path lengthening).
    # A negative delta on a steep link almost always means the ABEF *reference* is
    # itself an elevated (contaminated) steep-link lock -- test the reference's own
    # absolute excess above the true baseline instead of the (spurious) negative delta.
    ref_contam = (r['dbias'] < 0 and r['elev'] >= 30 and (abef_val - true_baseline) > 100)
    excess_target = r['dbias'] if r['dbias'] > 0 else (abef_val - true_baseline if ref_contam else None)
    refl = {pl: reflection_excess(idn if r['dbias'] > 0 else id_of(h, REF_ORIENT), L, pl)
            for pl in ('floor','ceil','xmin','xmax','ymin','ymax')}
    best_pl = min(refl, key=lambda p: abs(refl[p] - excess_target)) if excess_target else None
    best_match = abs(refl[best_pl] - excess_target) if best_pl else None
    # verdict
    if r['dbias'] < 0:
        verdict = ('REF-CONTAMINATED (ABEF ref is the steep-link lock)' if ref_contam
                   else 'NEG-DELTA (shorter-path at this orientation)')
    elif cls['bimodal']:
        verdict = 'BIMODAL'
    elif (cls['km_sep_sigma'] > 4 and min(cls['km_frac']) < 0.12 and cls['km_gap_mm'] > 150):
        verdict = 'STABLE-WRONG-PATH (+sparse excursions)'
    elif cls['roll_max_step_mm'] > 80:
        verdict = 'DRIFT/STEP'
    else:
        verdict = 'STABLE-WRONG-PATH'
    cell = dict(cell=f"{L}@{h}/{o}", id=idn, anchor=L, height=h, orient=o,
                dbias=r['dbias'], elev=r['elev'], dist=r['dist'],
                abs_bias=abias[idn][L], abs_bias_all_orients=others,
                abef_is_max=bool(abef_is_max), true_baseline_mm=true_baseline,
                ref_contaminated=bool(ref_contam),
                orient_specificity_mm=spec, others_graded=bool(graded),
                shape=cls, reflector_excess_mm=refl, reflector_excess_target_mm=excess_target,
                best_reflector=best_pl, best_reflector_resid_mm=best_match,
                verdict=verdict)
    l2_cells.append(cell)
    # per-cell time series figure
    fig, ax2 = plt.subplots(1, 2, figsize=(11, 3.6),
                            gridspec_kw={'width_ratios':[2, 1]})
    ax2[0].plot(ts, rs, lw=0.5, color='#333')
    if cls['roll'].size:
        wpad = (len(rs)-cls['roll'].size)//2
        ax2[0].plot(ts[wpad:wpad+cls['roll'].size], cls['roll'], color='red', lw=1.5, label='rolling')
    ax2[0].set_xlabel('host elapsed (s)'); ax2[0].set_ylabel('range (mm)')
    ax2[0].set_title(f"{L}@{h}/{o} (ID{idn}) Δbias={r['dbias']:+.0f}mm elev={r['elev']:.0f}°  [{verdict}]",
                     fontsize=9)
    ax2[0].legend(fontsize=7)
    ax2[1].hist(rs, bins=40, color='#4477aa', orientation='horizontal')
    for cc in cls['km_centers']:
        ax2[1].axhline(cc, color='red', ls='--', lw=0.8)
    ax2[1].set_xlabel('count'); ax2[1].set_title(f"sep={cls['km_sep_sigma']:.1f}σ gap={cls['km_gap_mm']:.0f}mm", fontsize=9)
    fig.tight_layout()
    safe = f"{L}_{h}_{o}"
    fig.savefig(os.path.join(OUT, f'l2_{safe}.png'), dpi=120)
    plt.close(fig)

# strip heavy arrays before JSON
for c in l2_cells:
    c['shape'].pop('roll', None)
t4 = dict(threshold_mm=L2_THRESH, n_cells=len(l2_cells), room_model=room, cells=l2_cells)

# ===========================================================================
# assemble & write
# ===========================================================================
peak_rss_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0
out = dict(
    meta=dict(generated_by="experiments/antenna_orientation_erlangen/followup/followup.py",
              reuses="../results.json (geometry, BIAS metric, anchor map)",
              anchor_id_letter_map={str(k): v for k, v in MAP.items()},
              anchor_z_mm={L: float(anchor_global[L][2]) for L in LETTERS},
              tag_z_by_height={h: float(tag_ant[HEIGHT_REF[h]][2]) for h in HEIGHTS}),
    task1_elevation=t1,
    task2_harmonic=t2,
    task3_pairs=t3,
    task4_layer2=t4,
    runtime_s=None, peak_rss_mb=peak_rss_mb,
)
out['runtime_s'] = time.time() - t0
with open(os.path.join(OUT, "results_followup.json"), "w") as fh:
    json.dump(out, fh, indent=2)

# console summary
print(f"runtime {out['runtime_s']:.1f}s  peak RSS {peak_rss_mb:.0f} MB")
print(f"[T1] Spearman |Δ| vs elev  ρ={rho_el:+.3f} p={p_el:.2e} | vs dist ρ={rho_di:+.3f} p={p_di:.2e}"
      f" | elev↔dist ρ={rho_ed:.2f}")
print(f"     partial(elev|dist)={partial_el:+.3f}  partial(dist|elev)={partial_di:+.3f}")
print(f"     30° split: shallow {t1['split_30deg']['shallow']} | steep {t1['split_30deg']['steep']}")
print(f"     ABEF abs-bias vs elev ρ={rho_ab_el:+.3f} p={p_ab_el:.2e}")
print(f"[T2] bad-cosine cells {n_bad_cos}/{n_cells} ({100*n_bad_cos/n_cells:.0f}%)")
print(f"     effect all={eff(alld)}")
print(f"     effect mid+high={eff(mh)}  <-- honest headline")
print(f"     phase-vs-az resultant R: " + ", ".join(f"{h}={phase_vs_az[h]['resultant_R']:.2f}" for h in HEIGHTS))
print(f"[T3] caliper mid: {cal_mid}  frac(typ/worst)={cal_mid['typical_rms']/CALIPER_FAIL:.0%}/{cal_mid['worst']/CALIPER_FAIL:.0%}")
print(f"     caliper all: {cal_all}")
if home['available']:
    print(f"     HOME steep links: {home['n_steep']}/{home['n_links']} ({home['frac_steep']:.0%}) elev {home['elev_min']:.0f}-{home['elev_max']:.0f}° median {home['elev_median']:.0f}°")
print(f"[T4] Layer-2 cells (|Δ|>{L2_THRESH:.0f}): {len(l2_cells)}")
for c in l2_cells:
    print(f"     {c['cell']:14s} Δ={c['dbias']:+.0f} elev={c['elev']:.0f}° -> {c['verdict']}"
          f"  (bimod sep={c['shape']['km_sep_sigma']:.1f}σ, step={c['shape']['roll_max_step_mm']:.0f}mm)")
print("WROTE", os.path.join(OUT, "results_followup.json"))
