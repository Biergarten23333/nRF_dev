#!/usr/bin/env python3
"""STAGE 0 (chunk-aware) of the coherent matched-filter test: proper joint rigid circle fit.

CRITICAL: only 14 of 36 roto chunks have ranging (rest = anchor_responder_drop). The rotation
phase theta(t) is continuous ONLY WITHIN a chunk; you must NOT interpolate across the ~40-min
inter-chunk gaps. So: auto-detect good chunks (>500 positions/tag), fit ONE global plane/center
from all good points, but build theta(t) and all rigidity stats PER CONTINUOUS CHUNK.

Rigid model: p_tag(t) = c + R_tag*rot(theta_tag(t)) in the fitted plane. Validation of the rigid
coupling: Delta-phi = wrapped(alpha1-alpha2) circular mean/std (accept sigma<~5deg), and inter-tag
chord length |p1-p2| std (rigid => near-constant). Phase model for later stages = per-chunk
smoothed single-tag angle by default; also compare the two-tag CHORD angle (the user's proposed
1/R-bypass) via per-rev period sigma and use whichever is lower.

Outputs coherent_stage0_cache.npz. Run: ulimit -v 8000000; python3 coherent_stage0.py [maxchunks]
"""
import csv, glob, os, sys, json, collections, numpy as np
import matplotlib; matplotlib.use('Agg'); import matplotlib.pyplot as plt
from scipy.optimize import least_squares
from scipy.signal import savgol_filter

B='logs/roto_sar_overnight_20260705_012548'
OUT='handoff_scripts_20260704/figs_20260704'; os.makedirs(OUT,exist_ok=True)
CACHE='handoff_scripts_20260704/coherent_stage0_cache.npz'
MAXCH=int(sys.argv[1]) if len(sys.argv)>1 else 99
lay=json.load(open('logs/autopos_v3box_noref_20260704_030908/solve_v3_box/anchor_layout_v3_box.json'))
A={a['label']:np.array([a['x_mm'],a['y_mm'],a['z_mm']],float) for a in lay['anchors']}
ORD=['A','B','C','D','E','F','G','H']; ROTO=['BS2DCE','BSDC91']; LAM=46.24

def multilat(anch_r):
    """Robust multilateration: linear closed-form seed -> nonlinear refine -> reject if the
    range-residual RMS is large (bad sweep/outlier). Nonlinear is what made the kinematics fit
    stable (R=435/534, correct normal); the linear-only version produced meter-scale outliers."""
    labs=[a for a in anch_r if len(anch_r[a])]
    if len(labs)<5: return None
    d=np.array([np.median(anch_r[a]) for a in labs]); P=np.array([A[a] for a in labs])
    a0=P[0]                                           # linear seed (reference-anchor subtraction)
    M=2*(P[1:]-a0); b=(d[0]**2-d[1:]**2)+(np.sum(P[1:]**2,axis=1)-np.dot(a0,a0))
    try: x0,*_=np.linalg.lstsq(M,b,rcond=None)
    except np.linalg.LinAlgError: x0=P.mean(0)
    r=least_squares(lambda x:np.linalg.norm(P-x,axis=1)-d, x0)
    if np.sqrt(np.mean(r.fun**2))>300: return None    # reject high-residual (outlier) sweep
    return r.x

def chunk_positions(tr):
    """PER-SWEEP multilateration in one chunk -> {tag:[(t,xyz)...]} sorted by t. Per-sweep (not
    0.5 s bins) because at omega~90 deg/s a 0.5 s bin smears 45 deg of arc -- that undersamples
    the rotation phase, which is exactly the quantity the coherent model needs. Per-sweep noise
    (~150 mm) is beaten down later by smoothing theta(t)."""
    cur=collections.defaultdict(lambda:[collections.defaultdict(list),None])   # (tag,sweep)->[ranges,t]
    for r in csv.DictReader(open(tr)):
        try:
            nm=r['peer_name']
            if nm not in ROTO: continue
            if not(r['valid']=='1' and float(r['range_mm'])>0 and float(r['quality_percent'])>=85): continue
            k=(nm,r['sweep']); cur[k][0][ORD[int(r['anchor_id'])]].append(float(r['range_mm'])); cur[k][1]=float(r['host_epoch_s'])
        except (ValueError,KeyError,IndexError): pass
    out={nm:[] for nm in ROTO}
    for (nm,sw),(ar,t) in cur.items():
        x=multilat(ar)
        if x is not None and t is not None: out[nm].append((t,x))
    for nm in ROTO: out[nm].sort()
    return out

# numeric chunk order, detect good chunks
chunk_dirs=sorted(glob.glob(f'{B}/chunk*/recv/tr_all.csv'),
                  key=lambda p:int(p.split('/chunk')[1].split('/')[0]))[:MAXCH]
good=[]   # list of (chunk_id, {tag:[(t,xyz)]})
for tr in chunk_dirs:
    cid=int(tr.split('/chunk')[1].split('/')[0])
    pos=chunk_positions(tr)
    if min(len(pos[nm]) for nm in ROTO)>=500:
        good.append((cid,pos))
print(f"  good chunks (>=500 pos/tag): {[c for c,_ in good]}")

# global plane/center from all good points
allP=np.vstack([np.array([x for _,x in pos[nm]]) for _,pos in good for nm in ROTO])
c=allP.mean(0); _,_,Vt=np.linalg.svd(allP-c,full_matrices=False); e1,e2,n=Vt[0],Vt[1],Vt[2]
R={nm:np.median([np.hypot((x-c)@e1,(x-c)@e2) for _,pos in good for t,x in pos[nm]]) for nm in ROTO}
print(f"  global center={c.round().astype(int).tolist()} normal={n.round(3).tolist()}  "
      f"R={ {k:round(v) for k,v in R.items()} }")

def proj(P): d=P-c; return d@e1,d@e2,d@n
def period_sigma(t,ang):
    ph=np.unwrap(ang); ph=ph*np.sign(ph[-1]-ph[0])
    n0=int(np.ceil(ph[0]/(2*np.pi))); n1=int(np.floor(ph[-1]/(2*np.pi))); cr=[]
    for k in range(n0,n1+1):
        j=np.searchsorted(ph,k*2*np.pi)
        if 0<j<len(ph): f=(k*2*np.pi-ph[j-1])/(ph[j]-ph[j-1]); cr.append(t[j-1]+f*(t[j]-t[j-1]))
    per=np.diff(np.array(cr)); per=per[(per>2)&(per<8)]
    return per

# per-chunk: build common grid, single & chord angles, smoothed per-tag model
segs=[]   # per good chunk: dict with tg, th_single{tag}, th_chord, model theta per tag
dphi_all=[]; clen_all=[]; per_chord=[]; per_single={nm:[] for nm in ROTO}
res_model={nm:[] for nm in ROTO}
runout={nm:[[],[],[]] for nm in ROTO}   # nm -> [theta, radial_dev, axial] samples
for cid,pos in good:
    T={nm:np.array([t for t,_ in pos[nm]]) for nm in ROTO}
    P={nm:np.array([x for _,x in pos[nm]]) for nm in ROTO}
    UV={nm:proj(P[nm]) for nm in ROTO}
    t0=max(T[nm][0] for nm in ROTO); t1=min(T[nm][-1] for nm in ROTO)
    if t1-t0<60: continue
    tg=np.arange(t0,t1,0.15)
    U={nm:np.interp(tg,T[nm],UV[nm][0]) for nm in ROTO}; V={nm:np.interp(tg,T[nm],UV[nm][1]) for nm in ROTO}
    Wp={nm:np.interp(tg,T[nm],UV[nm][2]) for nm in ROTO}
    ang={nm:np.arctan2(V[nm],U[nm]) for nm in ROTO}
    # rigidity
    delta=np.angle(np.exp(1j*(ang[ROTO[0]]-ang[ROTO[1]]))); dphi_all.append(delta)
    p1=c+np.outer(U[ROTO[0]],e1)+np.outer(V[ROTO[0]],e2)+np.outer(Wp[ROTO[0]],n)
    p2=c+np.outer(U[ROTO[1]],e1)+np.outer(V[ROTO[1]],e2)+np.outer(Wp[ROTO[1]],n)
    clen_all.append(np.linalg.norm(p1-p2,axis=1))
    # chord angle
    th_ch=np.arctan2(V[ROTO[0]]-V[ROTO[1]],U[ROTO[0]]-U[ROTO[1]])
    per_chord.append(period_sigma(tg,th_ch))
    for nm in ROTO: per_single[nm].append(period_sigma(tg,ang[nm]))
    # smoothed per-tag model angle; residual = circle model vs actual interpolated position
    win=max(5,int(0.7/0.15)|1)
    th_model={}; p_actual={ROTO[0]:p1,ROTO[1]:p2}
    for nm in ROTO:
        uw=np.unwrap(ang[nm]); th_model[nm]=savgol_filter(uw,win,2)
        pm=c+np.outer(np.cos(th_model[nm]),e1)*R[nm]+np.outer(np.sin(th_model[nm]),e2)*R[nm]
        res_model[nm].append(np.linalg.norm(pm-p_actual[nm],axis=1))
        # accumulate runout samples: radial dev (r-R) and axial (w) vs model angle, for harmonic fit
        rr=np.hypot(U[nm],V[nm]); ww=Wp[nm]
        runout[nm][0].append(th_model[nm]); runout[nm][1].append(rr-R[nm]); runout[nm][2].append(ww)
    segs.append(dict(cid=cid,tg=tg,th_model_1=th_model[ROTO[0]],th_model_2=th_model[ROTO[1]],th_chord=th_ch))

dphi=np.concatenate(dphi_all); Rbar=np.abs(np.mean(np.exp(1j*dphi)))
dphi_mean=np.degrees(np.angle(np.mean(np.exp(1j*dphi)))); dphi_sig=np.degrees(np.sqrt(-2*np.log(Rbar)))
clen=np.concatenate(clen_all)
pc=np.concatenate(per_chord); ps={nm:np.concatenate(per_single[nm]) for nm in ROTO}
print(f"\n[STAGE0] Delta-phi(alpha1-alpha2) = {dphi_mean:+.1f} +/- {dphi_sig:.1f} deg  "
      f"(accept sigma<~5deg; old 'joint' fit gave 157deg)")
print(f"[STAGE0] inter-tag chord length = {clen.mean():.0f} +/- {clen.std():.1f} mm "
      f"({100*clen.std()/clen.mean():.1f}%; rigid => near-constant)")
print(f"[STAGE0] per-rev period sigma/period:")
print(f"  chord           : {100*np.std(pc)/np.median(pc):.2f}%  (median {np.median(pc):.3f}s, {len(pc)} revs)")
for nm in ROTO:
    print(f"  {nm} single : {100*np.std(ps[nm])/np.median(ps[nm]):.2f}%  ({len(ps[nm])} revs)")
sig_ch=np.std(pc)/np.median(pc); sig_sg=min(np.std(ps[nm])/np.median(ps[nm]) for nm in ROTO)
use_chord=sig_ch<=sig_sg
print(f"  => PHASE MODEL = {'CHORD' if use_chord else 'per-tag SINGLE (smoothed)'} "
      f"(chord {'beats' if use_chord else 'does NOT beat'} single; TDMA non-simultaneity means the "
      f"chord difference adds independent noise instead of cancelling common-mode)")
res1=np.concatenate(res_model[ROTO[0]]); res2=np.concatenate(res_model[ROTO[1]])
print(f"[STAGE0] smoothed-model position residual RMS: {ROTO[0]}={np.sqrt(np.mean(res1**2)):.0f}mm  "
      f"{ROTO[1]}={np.sqrt(np.mean(res2**2)):.0f}mm  (vs lambda={LAM}mm; per-sweep multilat noise dominates)")

# ---- runout harmonic fit (radial & axial vs theta, 1st+2nd harmonic) MEASURED from trajectory ----
# (non-circular: correction derived from position data, NOT fit to the CIR image)
runout_coef={}
print("[STAGE0] runout harmonic fit (systematic wobble that autofocus could model):")
for nm in ROTO:
    th=np.concatenate(runout[nm][0]); rd=np.concatenate(runout[nm][1]); ax=np.concatenate(runout[nm][2])
    D=np.column_stack([np.cos(th),np.sin(th),np.cos(2*th),np.sin(2*th),np.ones_like(th)])
    cr,*_=np.linalg.lstsq(D,rd,rcond=None); ca,*_=np.linalg.lstsq(D,ax,rcond=None)
    rd_fit=D@cr; ax_fit=D@ca
    rad_amp=np.hypot(cr[0],cr[1]); ax_amp=np.hypot(ca[0],ca[1])
    ev_r=1-np.var(rd-rd_fit)/np.var(rd); ev_a=1-np.var(ax-ax_fit)/np.var(ax)
    runout_coef[nm]=(cr,ca)
    print(f"  {nm}: radial 1st-harm amp={rad_amp:.0f}mm (explains {100*ev_r:.0f}% of radial var, "
          f"resid {np.std(rd-rd_fit):.0f}mm) | axial amp={ax_amp:.0f}mm (explains {100*ev_a:.0f}%, "
          f"resid {np.std(ax-ax_fit):.0f}mm)")
print("  => if harmonics explain most of the 76/81mm radial/axial residual, runout is SYSTEMATIC")
print("     and autofocus-correctable; the unexplained resid is the random floor coherence must beat.")

# save geometry + per-chunk theta segments (concatenated + bounds)
seg_cid=np.array([s['cid'] for s in segs])
seg_len=np.array([len(s['tg']) for s in segs]); seg_off=np.concatenate([[0],np.cumsum(seg_len)])
np.savez(CACHE, c=c,e1=e1,e2=e2,n=n, tags=np.array(ROTO), R=np.array([R[nm] for nm in ROTO]),
         seg_cid=seg_cid, seg_off=seg_off,
         tg=np.concatenate([s['tg'] for s in segs]),
         th1=np.concatenate([s['th_model_1'] for s in segs]),
         th2=np.concatenate([s['th_model_2'] for s in segs]),
         thc=np.concatenate([s['th_chord'] for s in segs]),
         dphi=dphi_mean, dphi_sig=dphi_sig, use_chord=use_chord,
         chord_len=clen.mean(), chord_std=clen.std(), period=np.median(pc),
         runout_r1=runout_coef[ROTO[0]][0], runout_a1=runout_coef[ROTO[0]][1],
         runout_r2=runout_coef[ROTO[1]][0], runout_a2=runout_coef[ROTO[1]][1])
print(f"  saved {CACHE}  ({len(segs)} continuous segments)")
print("DONE")
