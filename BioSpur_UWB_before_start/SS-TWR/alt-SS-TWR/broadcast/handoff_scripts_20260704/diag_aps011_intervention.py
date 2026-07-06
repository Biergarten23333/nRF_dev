#!/usr/bin/env python3
"""APS011 INTERVENTION TEST: re-fit the Stage-0 joint rigid-circle on APS011-corrected ranges.

Firmware does NOT apply APS011 (dwt_getrangebias uncalled; raw ToF). The table's range-dependent
SLOPE b(=+2.77% over 1.6-5.3m) is a SCALE contamination; the constant a(=-142mm) is absorbed by
antenna-delay calibration. The fitted circle RADIUS depends only on each anchor's range SWING
(rmax-rmin), in which a cancels -> radius moves purely with b. Prediction: corrected radius shrinks
~2.8% (452->~440, 543->~528, toward kinematics 435/534).

corrected_range = raw_range - dwt_getrangebias(raw_range)   (ch5/PRF64 narrow-band, ported verbatim)

Also: (task4b) wand static-tag pairwise distances raw vs corrected; (task5) anchor-anchor pair-scale
from pairs_all.csv (NO per-measurement RX power logged there -> range-table correction only, Friis-
implicit; stated).

Run: cd .../broadcast; ulimit -v 8000000; python3 handoff_scripts_20260704/diag_aps011_intervention.py
"""
import csv, glob, json, sys, collections, numpy as np
sys.path.insert(0,'scripts'); import tag_roster

BASE='logs/roto_sar_overnight_20260705_012548'
CACHE='handoff_scripts_20260704/coherent_stage0_cache.npz'
lay=json.load(open('logs/autopos_v3box_noref_20260704_030908/solve_v3_box/anchor_layout_v3_box.json'))
A={a['label']:np.array([a['x_mm'],a['y_mm'],a['z_mm']],float) for a in lay['anchors']}
ORD=['A','B','C','D','E','F','G','H']; ROTO=['BS2DCE','BSDC91']; STATIC=['BS9336','BS955A','BSCCF4']
GOOD=set(int(x) for x in np.load(CACHE,allow_pickle=True)['seg_cid'])

TBL=[1,1,1,2,2,3,4,6,7,9,10,12,13,15,16,17,19,21,23,26,30,42,55,65,85,255]; OFF=-17
def getbias_mm(r_mm):
    ri=int((r_mm/1000.0)*4.0); ri=min(ri,255); i=0
    while ri>TBL[i]: i+=1
    return (i+OFF)*10.0
def correct(r_mm): return r_mm-getbias_mm(r_mm)          # firmware: corrected = measured - getbias

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
    rng,c=cost(x)
    if np.sqrt(c/len(d))>300: return None                # match Stage-0 outlier reject
    return x

def chunk_id(p): return int(p.split('/chunk')[1].split('/')[0].split('_')[0])
roster=tag_roster.roster_from_session(BASE)['by_id']

def collect_positions(which,apply_corr):
    """per-sweep multilat positions per tag in `which` across good chunks."""
    pos=collections.defaultdict(list)
    for tr in sorted(glob.glob(f'{BASE}/chunk*/recv/tr_all.csv'),key=chunk_id):
        if chunk_id(tr) not in GOOD: continue
        agg=collections.defaultdict(dict); f=open(tr,newline=''); rd=csv.reader(f); hdr=next(rd); ix={h:i for i,h in enumerate(hdr)}
        iN,iV,iRM,iQ,iAn,iSw=ix['peer_name'],ix['valid'],ix['range_mm'],ix['quality_percent'],ix['anchor_id'],ix['sweep']
        for row in rd:
            if row[iV]!='1': continue
            nm=row[iN]
            if nm not in which: continue
            try:
                rm=float(row[iRM])
                if rm<=0 or float(row[iQ])<85: continue
                agg[(nm,row[iSw])][ORD[int(row[iAn])]]=rm
            except (ValueError,IndexError): continue
        for (nm,sw),ar in agg.items():
            if len(ar)<5: continue
            labs=list(ar); P=np.array([A[a] for a in labs])
            d=np.array([correct(ar[a]) if apply_corr else ar[a] for a in labs])
            x=multilat(P,d)
            if x is not None: pos[nm].append(x)
    return {k:np.array(v) for k,v in pos.items()}

def fit_circle(P):
    c=P.mean(0); _,_,Vt=np.linalg.svd(P-c,full_matrices=False); e1,e2=Vt[0],Vt[1]
    R=np.median(np.hypot((P-c)@e1,(P-c)@e2)); return c,R

print("[roster]",roster,flush=True)
print("="*88); print("TASK 4 -- APS011 intervention on Stage-0 circle radius"); print("="*88)
print(f"{'tag':7} {'R_raw':>7} {'R_corr':>7} {'dR mm':>6} {'dR %':>6} | {'kinem':>6} {'mech':>6}  (cache R=451.8/543.0)")
MECH={'BS2DCE':400,'BSDC91':510}; KIN={'BS2DCE':435,'BSDC91':534}
raw=collect_positions(ROTO,False); print("  [raw positions done]",flush=True)
cor=collect_positions(ROTO,True);  print("  [corrected positions done]",flush=True)
for nm in ROTO:
    if nm not in raw or nm not in cor: continue
    _,Rr=fit_circle(raw[nm]); _,Rc=fit_circle(cor[nm])
    print(f"{nm:7} {Rr:7.1f} {Rc:7.1f} {Rc-Rr:6.1f} {100*(Rc-Rr)/Rr:6.2f} | {KIN[nm]:6d} {MECH[nm]:6d}   N={len(raw[nm])}")
print("  predicted dR from slope b=-2.8% (a cancels in the swing): ~ -13mm(452), -15mm(543)")

# ---- task 4b: wand static-tag pairwise distances raw vs corrected ----
print("\n"+"="*88); print("TASK 4b -- wand static-tag pairwise distances (raw vs APS011-corrected)"); print("="*88)
sraw=collect_positions(STATIC,False); scor=collect_positions(STATIC,True)
cen_r={k:np.median(v,0) for k,v in sraw.items()}; cen_c={k:np.median(v,0) for k,v in scor.items()}
print(f"  {'pair':13} {'raw mm':>7} {'corr mm':>7} {'d mm':>5} {'d %':>6}")
tags=[t for t in STATIC if t in cen_r]
for i in range(len(tags)):
    for j in range(i+1,len(tags)):
        dr=np.linalg.norm(cen_r[tags[i]]-cen_r[tags[j]]); dc=np.linalg.norm(cen_c[tags[i]]-cen_c[tags[j]])
        print(f"  {tags[i][-4:]+'-'+tags[j][-4:]:13} {dr:7.0f} {dc:7.0f} {dc-dr:5.0f} {100*(dc-dr)/dr:6.2f}")
print("  (tape-truth wand separations not in-repo -> report CHANGE only; scale-move should track b)")

# ---- task 5: anchor-anchor pair scale (NO per-measurement RX power in autopos logs) ----
print("\n"+"="*88); print("TASK 5 -- anchor-anchor pair scale (pairs_all.csv; NO logged RX power -> range-table/Friis-implicit)"); print("="*88)
pairs=[]
for r in csv.DictReader(open('logs/autopos_v3box_noref_20260704_030908/solve_v3_box/pairs_all.csv')):
    try:
        if r['ok']!='1': continue
        raw_mm=float(r['raw_mm']); pairs.append((r['a'],r['b'],raw_mm))
    except (ValueError,KeyError): continue
raw_d=np.array([p[2] for p in pairs]); cor_d=np.array([correct(p[2]) for p in pairs])
lay_d=np.array([np.linalg.norm(A[p[0]]-A[p[1]]) for p in pairs])
s_rawlay=float(np.sum(raw_d*lay_d)/np.sum(lay_d*lay_d))   # raw = s*layout
s_corlay=float(np.sum(cor_d*lay_d)/np.sum(lay_d*lay_d))
s_corraw=float(np.sum(cor_d*raw_d)/np.sum(raw_d*raw_d))   # corrected = s*raw
print(f"  {len(pairs)} anchor-anchor pairs, span [{raw_d.min():.0f},{raw_d.max():.0f}]mm")
print(f"  scale(raw / solved-layout)       = {s_rawlay:.4f}  (raw reads {100*(s_rawlay-1):+.2f}% vs noref layout)")
print(f"  scale(APS011-corrected / raw)    = {s_corraw:.4f}  ({100*(s_corraw-1):+.2f}% -> layout would rescale by this)")
print(f"  scale(APS011-corrected / layout) = {s_corlay:.4f}  ({100*(s_corlay-1):+.2f}%)")
print("  => APS011 correction rescales anchor-anchor distances by ~b; a full bundle re-solve would")
print("     move the layout scale by this amount. (Only range-table correction possible: RX power not logged.)")
print("DONE")
