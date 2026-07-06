#!/usr/bin/env python3
"""APS011 SLOPE-ONLY re-analysis. SUPERSEDES the full-table intervention in DIAG report 5b, which
applied corrected = raw - getbias(raw) and thereby re-injected the constant a=-142mm that is
(i) already absorbed by antenna-delay calibration, (ii) degenerate with the delay parameters,
(iii) EVB1000-design-specific (Sidorenko 2019: only the SLOPE transfers). The full correction's
+142mm constant, added to every range, drives an OFF-CENTROID expansion (orbit center ~1.1m from
the anchor centroid) that ENLARGED the fitted radius (+20mm) and swamped the -2.8% slope shrink ->
wrong-signed conclusion. Fingerprint: radii moved by near-equal ABSOLUTE amounts, not multiplicative.

SLOPE-ONLY correction, per link (tag,anchor):
    corrected = raw - (getbias(raw) - getbias(Rbar_link)),   Rbar_link = link mean operating range.
This zeroes the correction at the link's mean range (no constant) and applies only the range-
dependent (slope) part == a pure scale about Rbar_link. Falsifiable prediction: each radius DROPS
~=(1-b) TOWARD mechanical (Stage-0 449.3->~437 / 545.8->~531 ; kinematics 435->~423 / 534->~519).

Run: cd .../broadcast; ulimit -v 8000000; python3 handoff_scripts_20260704/diag_aps011_slope_only.py
"""
import csv, glob, json, sys, collections, numpy as np
sys.path.insert(0, 'scripts'); import tag_roster

BASE='logs/roto_sar_overnight_20260705_012548'
CACHE='handoff_scripts_20260704/coherent_stage0_cache.npz'
lay=json.load(open('logs/autopos_v3box_noref_20260704_030908/solve_v3_box/anchor_layout_v3_box.json'))
A={a['label']:np.array([a['x_mm'],a['y_mm'],a['z_mm']],float) for a in lay['anchors']}
ORD=['A','B','C','D','E','F','G','H']; ROTO=['BS2DCE','BSDC91']
GOOD=set(int(x) for x in np.load(CACHE,allow_pickle=True)['seg_cid'])
MECH={'BS2DCE':400,'BSDC91':510}; KIN_RAW={'BS2DCE':435,'BSDC91':534}

TBL=[1,1,1,2,2,3,4,6,7,9,10,12,13,15,16,17,19,21,23,26,30,42,55,65,85,255]; OFF=-17
def getbias_mm(r):                              # APS011 ch5/PRF64 narrow-band (verified vs firmware)
    ri=min(int((r/1000.0)*4.0),255); i=0
    while ri>TBL[i]: i+=1
    return (i+OFF)*10.0

def multilat(P,d):
    a0=P[0]; M=2.0*(P[1:]-a0); b=(d[0]**2-d[1:]**2)+(np.einsum('ij,ij->i',P[1:],P[1:])-a0@a0)
    try: x=np.linalg.lstsq(M,b,rcond=None)[0]
    except np.linalg.LinAlgError: return None
    def cost(xx): rng=np.sqrt(np.einsum('ij,ij->i',xx-P,xx-P)); return rng,float(np.sum((rng-d)**2))
    for _ in range(10):
        diff=x-P; rng=np.maximum(np.sqrt(np.einsum('ij,ij->i',diff,diff)),1e-6)
        try: dx=np.linalg.lstsq(diff/rng[:,None],d-rng,rcond=None)[0]
        except np.linalg.LinAlgError: break
        _,c0=cost(x); step=1.0; moved=False
        for _bt in range(8):
            _,cn=cost(x+step*dx)
            if cn<c0: x=x+step*dx; moved=True; break
            step*=0.5
        if not moved or step*step*np.dot(dx,dx)<1e-4: break
    _,c=cost(x)
    return x if np.sqrt(c/len(d))<=300 else None

def chunk_id(p): return int(p.split('/chunk')[1].split('/')[0].split('_')[0])

# ---------- pass 1: per-(tag,anchor) mean operating range Rbar_link (from RAW) ----------
Rbar=collections.defaultdict(list)
rows_cache=[]                                   # (chunk, tag, sweep, {anchor:raw})
for tr in sorted(glob.glob(f'{BASE}/chunk*/recv/tr_all.csv'), key=chunk_id):
    if chunk_id(tr) not in GOOD: continue
    agg=collections.defaultdict(dict); f=open(tr,newline=''); rd=csv.reader(f); hdr=next(rd)
    ix={h:i for i,h in enumerate(hdr)}
    iN,iV,iRM,iQ,iAn,iSw,iEp=ix['peer_name'],ix['valid'],ix['range_mm'],ix['quality_percent'],ix['anchor_id'],ix['sweep'],ix['host_epoch_s']
    for row in rd:
        if row[iV]!='1': continue
        nm=row[iN]
        if nm not in ROTO: continue
        try:
            rm=float(row[iRM])
            if rm<=0 or float(row[iQ])<85: continue
            an=ORD[int(row[iAn])]; agg[(nm,row[iSw])][an]=(rm,float(row[iEp])); Rbar[(nm,an)].append(rm)
        except (ValueError,IndexError): continue
    for (nm,sw),ar in agg.items():
        if len(ar)>=5: rows_cache.append((nm,sw,ar))
Rbar={k:float(np.mean(v)) for k,v in Rbar.items()}

def corrected(raw, tag, an, mode):
    if mode=='raw': return raw
    if mode=='full': return raw - getbias_mm(raw)                        # the flawed 5b correction
    return raw - (getbias_mm(raw) - getbias_mm(Rbar[(tag,an)]))          # slope-only

def positions(mode, binned):
    """multilaterate per tag; binned=False -> per-sweep (Stage-0 style); True -> 0.5s bin (kinematics)."""
    if not binned:
        pos=collections.defaultdict(list)
        for nm,sw,ar in rows_cache:
            labs=list(ar); P=np.array([A[a] for a in labs])
            d=np.array([corrected(ar[a][0],nm,a,mode) for a in labs])
            x=multilat(P,d)
            if x is not None: pos[nm].append(x)
        return {k:np.array(v) for k,v in pos.items()}
    # 0.5 s binned: median CORRECTED range per anchor per (tag,0.5s bin)
    binagg=collections.defaultdict(lambda: collections.defaultdict(list))
    for nm,sw,ar in rows_cache:
        for a,(rm,ep) in ar.items():
            binagg[(nm,int(ep/0.5))][a].append(corrected(rm,nm,a,mode))
    pos=collections.defaultdict(list)
    for (nm,bb),ad in binagg.items():
        labs=[a for a in ad if len(ad[a])]
        if len(labs)<5: continue
        P=np.array([A[a] for a in labs]); d=np.array([np.median(ad[a]) for a in labs])
        x=multilat(P,d)
        if x is not None: pos[nm].append(x)
    return {k:np.array(v) for k,v in pos.items()}

def joint_radii(pos):
    """Stage-0 JOINT: shared plane (SVD on union of both tags), per-tag median radius."""
    allP=np.vstack([pos[nm] for nm in ROTO if nm in pos and len(pos[nm])]); c=allP.mean(0)
    _,_,Vt=np.linalg.svd(allP-c, full_matrices=False); e1,e2=Vt[0],Vt[1]
    out={}
    for nm in ROTO:
        if nm not in pos: continue
        d=pos[nm]-c; out[nm]=float(np.median(np.hypot(d@e1,d@e2)))
    return out, c

print("="*94)
print("APS011 SLOPE-ONLY re-fit (supersedes 5b full-table). getbias verified: "
      f"-140@0.5m {getbias_mm(500):.0f}, -70@2.6m {getbias_mm(2600):.0f}, 0@5.3m {getbias_mm(5300):.0f}")
print("="*94)
b_frac=(getbias_mm(5300)-getbias_mm(500))/(5300-500)     # slope of getbias over the span
print(f"Rbar per link (mm): "+", ".join(f"{k[0][-4:]}@{k[1]}={v:.0f}" for k,v in sorted(Rbar.items())))
print(f"getbias slope b over 0.5-5.3m = {100*b_frac:.2f}% (scale-equivalent). Prediction: R *= (1-b).\n")

for method,binned in [("Stage-0 joint (per-sweep)",False),("kinematics-style (0.5s bin)",True)]:
    praw=positions('raw',binned); pslp=positions('slope',binned); pful=positions('full',binned)
    Rraw,_=joint_radii(praw); Rslp,_=joint_radii(pslp); Rful,_=joint_radii(pful)
    print(f"--- {method} ---")
    print(f"  {'tag':7} {'R_raw':>7} {'R_slopeonly':>11} {'dR%':>6} {'R_full(5b)':>10} {'mech':>5} "
          f"{'pred(1-b)':>9} {'resid_excess':>12}")
    for nm in ROTO:
        if nm not in Rraw: continue
        pred=Rraw[nm]*(1-b_frac); resid=Rslp[nm]-MECH[nm]
        print(f"  {nm:7} {Rraw[nm]:7.1f} {Rslp[nm]:11.1f} {100*(Rslp[nm]-Rraw[nm])/Rraw[nm]:6.2f} "
              f"{Rful[nm]:10.1f} {MECH[nm]:5d} {pred:9.1f} {resid:+9.1f} mm")
    print()

print("="*94)
print("READING: slope-only should SHRINK R toward mechanical (matching pred(1-b)); the full-table")
print("R_full(5b) instead GREW -> that growth was the +142mm-constant off-centroid artifact, not slope.")
print("residual_excess = R_slopeonly - mechanical = the radius excess NOT explained by APS011 slope.")
print("DONE")
