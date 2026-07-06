#!/usr/bin/env python3
"""Step-0 zero-cost audit on existing overnight data. Produces the two numbers that gate the
coherent-SAR and respiration branches, and the RotoArm rigidity for autofocus.

A. PHASE STABILITY (the never-looked-at asset). Complex CIR (I/Q, not magnitude) on a fully
   STATIC link (static wand tag -> fixed listener). Reference each tail tap's phase to the
   first-path tap (clock-independent per review). Circular std of that FP-referenced phase across
   frames = the coherent-processing floor. <~30-40 deg => coherent SAR + respiration viable.
B. RotoArm CIRCLE RESIDUAL + ANGULAR-SPEED JITTER, and rigid-arm consistency (both roto tags
   share center/plane, fixed angular offset). Decides whether ~8-param parametric autofocus can
   describe the 50k-frame TX track.
Usage: python3 step0_audit.py <overnight_base> [n_chunks_for_phase]"""
import csv, glob, os, sys, json, collections, numpy as np
from scipy.optimize import least_squares
BASE=sys.argv[1]; NPHASE=int(sys.argv[2]) if len(sys.argv)>2 else 2
NSAMP=1016; PRE=8; POST=120
lay=json.load(open('SS-TWR/alt-SS-TWR/broadcast/logs/autopos_v3box_noref_20260704_030908/solve_v3_box/anchor_layout_v3_box.json'))
A={a['label']:np.array([a['x_mm'],a['y_mm'],a['z_mm']],float) for a in lay['anchors']}
ORD=['A','B','C','D','E','F','G','H']

def circstd_deg(ph):
    R=np.abs(np.mean(np.exp(1j*ph)))
    return np.degrees(np.sqrt(max(-2*np.log(max(R,1e-12)),0)))

# ---- tag_id -> name via lpd src (0xb100+tag) + recv CFG ----
import re
def cfg_map(cd):
    m={}
    for rl in glob.glob(f'{cd}/recv/**/raw.log',recursive=True)+glob.glob(f'{cd}/recv/raw.log'):
        try:
            for line in open(rl,errors='ignore'):
                x=re.search(r'CFG assigned\[\d+\]: bs=(BS[0-9A-Fa-f]+) tag=(\d+)',line)
                if x: m[x.group(2)]=x.group(1)
        except FileNotFoundError: pass
        if m: break
    return m
def tagid_name(pdir,cm):
    out={}
    lpd=glob.glob(f'{pdir}/listener_*/lpd.csv')
    if not lpd: return out
    for r in csv.DictReader(open(lpd[0])):
        try:
            src=r['src'].lower()
            if src.startswith('0xb10'):
                n=str(int(src,16)-0xb100)
                if n in cm: out[r['tag_id']]=cm[n]
        except: pass
    return out

def complex_frames(pdir,tid):
    D=glob.glob(f'{pdir}/listener_*')
    if not D: return [],[]
    meta={}
    for r in csv.DictReader(open(f'{D[0]}/lcirm.csv')):
        if r['tag_id']!=tid: continue
        try: meta[r['accepted_polls']]=(float(r['fp_index'])/64.0,float(r['rxpacc']),float(r['host_epoch_s']))
        except: pass
    segs=collections.defaultdict(list)
    for r in csv.DictReader(open(f'{D[0]}/lcird.csv')):
        if r['accepted_polls'] in meta: segs[r['accepted_polls']].append((int(r['offset']),r['hex']))
    C=[];fpidx=[]
    for ap,seg in segs.items():
        fps,rp,ep=meta[ap]; seg.sort(); h=''.join(x for _,x in seg)
        try: iq=np.frombuffer(bytes.fromhex(h),dtype='<i2').astype(float)
        except: continue
        if iq.size<2*NSAMP: continue
        c=iq[0:2*NSAMP:2]+1j*iq[1:2*NSAMP:2]
        fp=int(round(fps))
        if fp-PRE<0 or fp+POST>NSAMP: continue
        if rp>0: c=c/rp
        C.append(c[fp-PRE:fp+POST]); fpidx.append(fps)
    return np.array(C),np.array(fpidx)

print("="*70); print("A. PHASE STABILITY AUDIT (static wand link, FP-referenced)")
chunks=sorted(glob.glob(f'{BASE}/chunk*'))[:NPHASE]
# use a fixed static listener; pick the static wand tag with most frames
STATIC_LISTENER='LB'
allC=[]; name_used=None
for cd in chunks:
    cm=cfg_map(cd); tn=tagid_name(f'{cd}/{STATIC_LISTENER}',cm)
    # static wand tags only
    for tid,nm in tn.items():
        if nm in ('BS2DCE','BSDC91'): continue   # skip rotating
        C,fpi=complex_frames(f'{cd}/{STATIC_LISTENER}',tid)
        if len(C)>20:
            allC.append((nm,C,fpi))
if not allC:
    print("  no static complex frames found");
else:
    # pick the name with most frames
    byname=collections.defaultdict(list)
    for nm,C,fpi in allC: byname[nm].append((C,fpi))
    nm=max(byname,key=lambda k:sum(len(c) for c,_ in byname[k]))
    C=np.vstack([c for c,_ in byname[nm]]); fpi=np.concatenate([f for _,f in byname[nm]])
    name_used=nm
    mag=np.abs(C).mean(0)
    print(f"  link {STATIC_LISTENER}<-{nm}  frames={len(C)}  fp_index std={np.std(fpi)/64.0*1.0016*299.79:.1f}mm (0=perfectly static)")
    print(f"  {'tap(from FP)':>12s} {'mean|CIR|':>10s} {'SNRdB':>6s} {'phaseSTD(deg,FPref)':>20s}")
    fpref=np.angle(C[:,PRE])
    noise=np.median(mag[PRE+60:])   # far tail as noise proxy
    for k in [PRE+3,PRE+5,PRE+8,PRE+12,PRE+18,PRE+25,PRE+40]:
        ph=np.angle(C[:,k])-fpref
        s=circstd_deg(ph); snr=20*np.log10(max(mag[k],1e-9)/max(noise,1e-9))
        flag=' <-- coherent OK' if (s<40 and snr>6) else (' (low SNR)' if snr<6 else ' <-- too noisy for coherent')
        print(f"  {k-PRE:12d} {mag[k]:10.4f} {snr:6.1f} {s:20.1f}{flag}")
    good=[circstd_deg(np.angle(C[:,k])-fpref) for k in range(PRE+3,PRE+50) if 20*np.log10(max(mag[k],1e-9)/max(noise,1e-9))>6]
    if good: print(f"  VERDICT: median phaseSTD over SNR>6dB tail taps = {np.median(good):.1f} deg  -> {'COHERENT/RESP VIABLE' if np.median(good)<40 else 'COHERENT DEAD (incoherent-only)'}")
    else: print("  VERDICT: no tail tap above 6dB SNR on this link")

print("="*70); print("B. RotoArm CIRCLE RESIDUAL + SPEED JITTER + rigid-arm consistency")
def solve(ba):
    labs=[a for a in ba if ba[a]]
    if len(labs)<4: return None
    d=np.array([np.median(ba[a]) for a in labs]);P=np.array([A[a] for a in labs])
    return least_squares(lambda x:np.linalg.norm(P-x,axis=1)-d,P.mean(0)).x
traj={}
for tag in ['BS2DCE','BSDC91']:
    pts=[]
    for tr in sorted(glob.glob(f'{BASE}/chunk*/recv/tr_all.csv')):
        rows=[r for r in csv.DictReader(open(tr)) if r['valid']=='1' and r['peer_name']==tag]
        if not rows: continue
        t0=float(rows[0]['host_epoch_s'])
        byb=collections.defaultdict(lambda:collections.defaultdict(list))
        for r in rows:
            try:
                rm=float(r['range_mm'])
                if rm<=0 or float(r['quality_percent'])<85: continue
                byb[(t0,int((float(r['host_epoch_s'])-t0)/0.5))][ORD[int(r['anchor_id'])]].append(rm)
            except: pass
        for (tt,b),ba in byb.items():
            x=solve(ba)
            if x is not None: pts.append((tt+b*0.5,x))
    pts.sort(); traj[tag]=pts
for tag,pts in traj.items():
    if len(pts)<50: print(f"  {tag}: too few pts"); continue
    P=np.array([x for _,x in pts]); T=np.array([t for t,_ in pts]); c=P.mean(0)
    U,S,Vt=np.linalg.svd(P-c, full_matrices=False); n=Vt[2]; e1,e2=Vt[0],Vt[1]
    u=(P-c)@e1; v=(P-c)@e2; rad=np.sqrt(u**2+v**2)
    resid_inplane=np.std(rad)               # circle radial residual
    outplane=np.std((P-c)@n)                # thickness off-plane
    ang=np.unwrap(np.arctan2(v,u))
    # angular speed within contiguous chunks (dt small)
    dt=np.diff(T); da=np.diff(ang); good=(dt>0.1)&(dt<3)
    spd=np.abs(da[good]/dt[good])           # rad/s
    period=2*np.pi/np.median(spd) if len(spd) else float('nan')
    print(f"  {tag}: {len(pts)}pts R={rad.mean():.0f}mm  circle-resid(in-plane)={resid_inplane:.0f}mm  off-plane={outplane:.0f}mm")
    print(f"        period={period:.1f}s  speed jitter={np.std(spd)/max(np.median(spd),1e-9)*100:.0f}% (of median)")
# rigid arm: distance between the two roto tags over time (should be ~constant)
if len(traj.get('BS2DCE',[]))>20 and len(traj.get('BSDC91',[]))>20:
    A2={round(t,1):x for t,x in traj['BS2DCE']}; B2={round(t,1):x for t,x in traj['BSDC91']}
    common=[k for k in A2 if k in B2]
    if common:
        dd=np.array([np.linalg.norm(A2[k]-B2[k]) for k in common])
        print(f"  rigid-arm: |BS2DCE-BSDC91| = {dd.mean():.0f} +/- {dd.std():.0f} mm over {len(common)} common samples (should be ~const if rigid)")
