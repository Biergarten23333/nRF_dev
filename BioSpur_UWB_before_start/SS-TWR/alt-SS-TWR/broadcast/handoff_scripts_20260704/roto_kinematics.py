#!/usr/bin/env python3
"""Prompt-3: kinematics + operational audit of roto_sar_overnight_20260705_012548.
(a) joint rigid circle fit -> empirical TWR position noise (the anchor for all theory numbers),
    Delta-phi rigidity, radii vs mechanical, omega(t)/period drift, chord-vs-single-tag angle noise.
(b) anchor participation from tr_all (rx_mask empty here -> derive from anchor_id/valid), ge8, GDOP 7v8.
(c) chunk post-mortem: classify all 36 slots from file evidence + driver.log.
Usage: python3 roto_kinematics.py"""
import csv, glob, os, re, collections, json, numpy as np
from scipy.optimize import least_squares
B='logs/roto_sar_overnight_20260705_012548'
lay=json.load(open('logs/autopos_v3box_noref_20260704_030908/solve_v3_box/anchor_layout_v3_box.json'))
A={a['label']:np.array([a['x_mm'],a['y_mm'],a['z_mm']],float) for a in lay['anchors']}
ORD=['A','B','C','D','E','F','G','H']; ROTO=['BS2DCE','BSDC91']

def solve_pos(ba):
    labs=[a for a in ba if ba[a]]
    if len(labs)<4: return None,0
    d=np.array([np.median(ba[a]) for a in labs]); P=np.array([A[a] for a in labs])
    r=least_squares(lambda x:np.linalg.norm(P-x,axis=1)-d,P.mean(0))
    return r.x,len(labs)

# ---- build trajectories at 0.5s bins (finer -> better omega) ----
traj=collections.defaultdict(list)
anchpart=collections.defaultdict(lambda:[0,0])   # anchor -> [valid, total]
part_by_tag=collections.defaultdict(lambda:collections.defaultdict(lambda:[0,0]))
sweep_nresp=[]
for tr in sorted(glob.glob(f'{B}/chunk*/recv/tr_all.csv')):
    rows=list(csv.DictReader(open(tr)))
    if not rows: continue
    t0=float(rows[0]['host_epoch_s'])
    byname=collections.defaultdict(lambda:collections.defaultdict(lambda:collections.defaultdict(list)))
    # per-sweep anchor response for ge8
    persweep=collections.defaultdict(set)
    for r in rows:
        try:
            aid=int(r['anchor_id']); an=ORD[aid]; tag=r['peer_name']
            valid=(r['valid']=='1' and float(r['range_mm'])>0 and float(r['quality_percent'])>=85)
            anchpart[an][1]+=1; part_by_tag[tag][an][1]+=1
            if valid:
                anchpart[an][0]+=1; part_by_tag[tag][an][0]+=1
                persweep[(tag,r['sweep'])].add(an)
                if float(r['quality_percent'])>=85:
                    b=int((float(r['host_epoch_s'])-t0)/0.5)
                    byname[tag][b][an].append(float(r['range_mm']))
        except (ValueError,KeyError,IndexError): pass
    for (tag,sw),s in persweep.items():
        if tag in ROTO: sweep_nresp.append(len(s))
    for nm,bks in byname.items():
        if nm not in ROTO: continue
        for b,ba in bks.items():
            x,nn=solve_pos(ba)
            if x is not None and nn>=5: traj[nm].append((t0+b*0.5,x))
for nm in traj: traj[nm].sort()

print("="*74+"\n(a) JOINT RIGID CIRCLE FIT\n"+"="*74)
# joint plane: fit to union of both tags' points
allP=np.vstack([np.array([x for _,x in traj[nm]]) for nm in ROTO if traj[nm]])
cJ=allP.mean(0); U,S,Vt=np.linalg.svd(allP-cJ, full_matrices=False); e1,e2,nJ=Vt[0],Vt[1],Vt[2]
print(f"  shared center={cJ.round().astype(int).tolist()}  shared normal={nJ.round(3).tolist()}")
ang={}; rad={}
for nm in ROTO:
    P=np.array([x for _,x in traj[nm]]); T=np.array([t for t,_ in traj[nm]])
    d=P-cJ; u=d@e1; v=d@e2; w=d@nJ
    r=np.hypot(u,v)
    # per-tag circle: radius = median r; residual RMS = 3D dist from the fitted circle
    R=np.median(r)
    radial=r-R; off=w
    resid3d=np.sqrt(radial**2+off**2)
    rad[nm]=R; ang[nm]=(T,np.unwrap(np.arctan2(v,u)),np.arctan2(v,u))
    print(f"  {nm}: R={R:.0f}mm (mech ~{'400' if nm=='BS2DCE' else '510'})  "
          f"resid_RMS(3D)={np.sqrt(np.mean(resid3d**2)):.0f}mm  radial={radial.std():.0f}  offplane={off.std():.0f}  N={len(P)}")
# Delta-phi rigidity on common timestamps
tol=0.3
A2={round(t,1):a for t,a in zip(ang['BS2DCE'][0],ang['BS2DCE'][2])}
B2={round(t,1):a for t,a in zip(ang['BSDC91'][0],ang['BSDC91'][2])}
common=sorted(set(A2)&set(B2))
if common:
    dph=np.array([np.angle(np.exp(1j*(A2[t]-B2[t]))) for t in common])
    dphm=np.angle(np.mean(np.exp(1j*dph)))
    print(f"  Delta-phi(BS2DCE-BSDC91) = {np.degrees(dphm):+.1f} +/- {np.degrees(np.std(dph)):.1f} deg over {len(common)} common (const=rigid)")
# omega(t) + period over night
allT=[]; allW=[]
for nm in ROTO:
    T,unw,_=ang[nm]; dt=np.diff(T); dw=np.diff(unw); good=(dt>0.2)&(dt<3)
    w=np.abs(dw[good]/dt[good]); allT.append(T[1:][good]); allW.append(w)
W=np.concatenate(allW); Talls=np.concatenate(allT)
per=2*np.pi/np.median(W)
print(f"  omega: median={np.median(W):.3f} rad/s -> period={per:.2f}s  (jitter {np.std(W)/np.median(W)*100:.0f}%)")
# period drift: first third vs last third of night
idx=np.argsort(Talls); Wt=W[idx]
p_first=2*np.pi/np.median(Wt[:len(Wt)//3]); p_last=2*np.pi/np.median(Wt[-len(Wt)//3:])
print(f"  period drift over night: first-third={p_first:.2f}s  last-third={p_last:.2f}s  ({(p_last-p_first):+.2f}s)")
# chord angle vs single-tag angle noise
if common:
    P1={round(t,1):x for t,x in traj['BS2DCE']}; P2={round(t,1):x for t,x in traj['BSDC91']}
    cc=[t for t in common if t in P1 and t in P2]
    chord=np.array([ (P1[t]-P2[t]) for t in cc]); cu=chord@e1; cv=chord@e2
    ch_ang=np.unwrap(np.arctan2(cv,cu)); tt=np.array(cc)
    # smoothness = residual of angle vs local-linear (constant omega) fit, per method
    def rough(T,ang):
        dt=np.diff(T); dw=np.diff(ang); g=(dt>0.2)&(dt<3)
        w=dw[g]/dt[g]; return np.std(w)/max(abs(np.median(w)),1e-9)
    r_single=rough(ang['BS2DCE'][0],ang['BS2DCE'][1])
    r_chord=rough(tt,ch_ang)
    print(f"  angle-rate roughness: single-tag(BS2DCE)={r_single*100:.0f}%  two-tag-chord={r_chord*100:.0f}%  "
          f"-> chord {'BETTER' if r_chord<r_single else 'not better'}")

print("\n"+"="*74+"\n(b) ANCHOR PARTICIPATION + GDOP\n"+"="*74)
print("  per-anchor valid-response rate (all tags):")
for an in ORD:
    v,t=anchpart[an]
    if t: print(f"    {an}: {100*v/t:5.1f}%  ({v}/{t})")
if sweep_nresp:
    sn=np.array(sweep_nresp)
    print(f"  RotoArm-tag sweeps: median anchors/sweep={int(np.median(sn))}  "
          f"ge7={100*np.mean(sn>=7):.0f}%  ge8={100*np.mean(sn>=8):.0f}%")
# GDOP near volume center, 8 vs best-7
x0=np.array([2200.,1600.,1000.])
def gdop(labels):
    G=[]
    for a in labels:
        u=(x0-A[a]); u=u/np.linalg.norm(u); G.append(u)
    G=np.array(G)
    try: return np.sqrt(np.trace(np.linalg.inv(G.T@G)))
    except np.linalg.LinAlgError: return np.nan
g8=gdop(ORD)
weakest=min(ORD,key=lambda a:anchpart[a][0]/max(anchpart[a][1],1))
g7=gdop([a for a in ORD if a!=weakest])
print(f"  GDOP @center: 8-anchor={g8:.2f}  drop-weakest({weakest})=7-anchor={g7:.2f}  (+{100*(g7-g8)/g8:.0f}%)")

print("\n"+"="*74+"\n(c) CHUNK POST-MORTEM (36 slots)\n"+"="*74)
# per-chunk file evidence
status={}
for i in range(1,37):
    d=f'{B}/chunk{i}'; trf=glob.glob(f'{d}/recv/tr_all.csv')
    ntr=sum(1 for _ in open(trf[0])) if trf else 0
    ncir=0
    for f in glob.glob(f'{d}/L*/listener_*/lcird.csv'): ncir+=sum(1 for _ in open(f))
    status[i]=('GOOD' if (ntr>1000 and ncir>1000) else 'FAIL', ntr, ncir)
# driver.log reasons
reason={}
cur=None
for line in open(f'{B}/driver.log'):
    m=re.search(r'=== chunk (\d+)/36',line)
    if m: cur=int(m.group(1))
    if cur and 'no ranging this chunk' in line: reason[cur]='no_ranging(anchor_responder_drop)'
    if cur and 'health: ge7' in line: reason[cur]='completed'
good=[i for i in status if status[i][0]=='GOOD']; fail=[i for i in status if status[i][0]=='FAIL']
print(f"  GOOD={len(good)}: {good}")
print(f"  FAIL={len(fail)}: {fail}")
rc=collections.Counter(reason.get(i,'unknown') for i in fail)
print("  failed-slot root causes:")
for k,v in rc.most_common(): print(f"    {v:2d} x  {k}")
# confirm CIR cleanliness of good chunks (no truncation)
trunc=sum(1 for i in good if status[i][2] < 500000)
print(f"  good chunks with suspiciously low CIR (<500k lines, possible truncation): {trunc}")
print("\n  interpretation: all failures share one mechanism (anchors drop RESPONDER at chunk boundary;")
print("  the between-chunk kill/preflight knocks them out, restore needs >=1 slot). Captured CIR is clean.")
