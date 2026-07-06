#!/usr/bin/env python3
"""Circular-SAR room image from overnight RotoArm capture.
1) Circle-fit the rotating tags' (BS2DCE/BSDC91) trajectory from recv TR ranging -> position(t).
2) For each RotoArm-tag CIR frame from the 6 fixed listeners: TX = tag position at that time,
   RX = listener position -> back-project tail onto room grid. Moving TX => angular diversity
   => the range-only bullseye becomes a real 2D map.
Usage: python3 roto_sar_image.py <overnight_base_dir>"""
import csv, glob, os, sys, json, collections, numpy as np, matplotlib
matplotlib.use('Agg'); import matplotlib.pyplot as plt
from scipy.optimize import least_squares
BASE=sys.argv[1]
MM_PER_NS=299.792458; SAMP_NS=1.0016; NSAMP=1016; PRE=8; POST=120
lay=json.load(open('SS-TWR/alt-SS-TWR/broadcast/logs/autopos_v3box_noref_20260704_030908/solve_v3_box/anchor_layout_v3_box.json'))
A={a['label']:np.array([a['x_mm'],a['y_mm'],a['z_mm']],float) for a in lay['anchors']}
ORD=['A','B','C','D','E','F','G','H']
LIS={'LB':A['B'],'LE':A['E'],'LF':A['F'],'LCCF4':None,'L9336':None,'L955A':None}
SNname={'0xf4':'BSCCF4','0x36':'BS9336','0x5a':'BS955A','0xce':'BS2DCE','0x91':'BSDC91'}
ROTO=['BS2DCE','BSDC91']

def solve_pos(byanchor):
    labs=[a for a in byanchor if byanchor[a]]
    if len(labs)<4: return None
    d=np.array([np.median(byanchor[a]) for a in labs]); P=np.array([A[a] for a in labs])
    r=least_squares(lambda x:np.linalg.norm(P-x,axis=1)-d,P.mean(0)); return r.x

traj=collections.defaultdict(list)
for tr in sorted(glob.glob(f'{BASE}/chunk*/recv/tr_all.csv')):
    rows=[r for r in csv.DictReader(open(tr)) if r['valid']=='1']
    if not rows: continue
    t0=float(rows[0]['host_epoch_s'])
    byname=collections.defaultdict(lambda:collections.defaultdict(lambda:collections.defaultdict(list)))
    for r in rows:
        try:
            rm=float(r['range_mm'])
            if rm<=0 or float(r['quality_percent'])<85: continue
            b=int((float(r['host_epoch_s'])-t0)/1.0)
            byname[r['peer_name']][b][ORD[int(r['anchor_id'])]].append(rm)
        except: pass
    for nm,bks in byname.items():
        for b,ba in bks.items():
            x=solve_pos(ba)
            if x is not None: traj[nm].append((t0+b, x))
for nm in traj: traj[nm].sort()
print("tags with trajectory:",{k:len(v) for k,v in traj.items()})

def wand_pos(nm):
    if nm not in traj or not traj[nm]: return None
    return np.median(np.array([x for _,x in traj[nm]]),axis=0)
for nm,L in [('BSCCF4','LCCF4'),('BS9336','L9336'),('BS955A','L955A')]:
    p=wand_pos(nm)
    if p is not None: LIS[L]=p
print("listener positions:",{k:(v.round().astype(int).tolist() if v is not None else None) for k,v in LIS.items()})

def circle_fit(pts):
    P=np.array([x for _,x in pts]); c=P.mean(0)
    U,S,Vt=np.linalg.svd(P-c, full_matrices=False); n=Vt[2]
    return c,n,float(np.median(np.linalg.norm(P-c,axis=1))),np.ptp(P,axis=0)
for nm in ROTO:
    if nm in traj and len(traj[nm])>10:
        c,n,rad,span=circle_fit(traj[nm])
        print(f"{nm}: {len(traj[nm])} pts center={c.round().astype(int).tolist()} R~{rad:.0f}mm span(xyz)={span.round().astype(int).tolist()} normal={n.round(2).tolist()}")

def tag_at(nm,t):
    tr=traj.get(nm,[])
    if len(tr)<2: return None
    ts=np.array([x[0] for x in tr])
    i=np.searchsorted(ts,t); i=max(1,min(i,len(tr)-1))
    (t0,p0),(t1,p1)=tr[i-1],tr[i]
    if t1==t0: return p0
    a=(t-t0)/(t1-t0); a=max(0,min(1,a)); return p0*(1-a)+p1*a

import re
def chunk_cfg_map(cd):
    """recv CFG: tag number -> BS name. This 5-tag capture assigns sequential UWB src
    0xb100+tag, so listener src does NOT carry the BS address suffix; resolve via recv CFG."""
    num2name={}
    for rl in sorted(glob.glob(f'{cd}/recv/**/raw.log',recursive=True))+glob.glob(f'{cd}/recv/raw.log'):
        try:
            for line in open(rl,errors='ignore'):
                m=re.search(r'CFG assigned\[\d+\]: bs=(BS[0-9A-Fa-f]+) tag=(\d+)',line)
                if m: num2name[m.group(2)]=m.group(1)
        except FileNotFoundError: pass
        if num2name: break
    return num2name

def probe_tagmap(pdir,num2name):
    m={}
    lpd=glob.glob(f'{pdir}/listener_*/lpd.csv')
    if not lpd: return m
    for r in csv.DictReader(open(lpd[0])):
        try:
            src=r['src'].lower(); last='0x'+src[-2:]
            if last in SNname: m[r['tag_id']]=SNname[last]          # BS-address src scheme
            elif src.startswith('0xb10'):                          # sequential 0xb100+tag scheme
                n=str(int(src,16)-0xb100)
                if n in num2name: m[r['tag_id']]=num2name[n]
        except: pass
    return m

def cir_frames(pdir,want_names,tmap):
    D=glob.glob(f'{pdir}/listener_*')
    if not D: return
    meta={}
    try:
        for r in csv.DictReader(open(f'{D[0]}/lcirm.csv')):
            nm=tmap.get(r['tag_id'])
            if nm not in want_names: continue
            try: meta[r['accepted_polls']]=(float(r['fp_index'])/64.0,float(r['rxpacc']),float(r['host_epoch_s']),nm)
            except: pass
        segs=collections.defaultdict(list)
        for r in csv.DictReader(open(f'{D[0]}/lcird.csv')):
            if r['accepted_polls'] in meta: segs[r['accepted_polls']].append((int(r['offset']),r['hex']))
    except FileNotFoundError: return
    for ap,seg in segs.items():
        fps,rp,ep,nm=meta[ap]; seg.sort(); h=''.join(x for _,x in seg)
        try: iq=np.frombuffer(bytes.fromhex(h),dtype='<i2').astype(float)
        except: continue
        if iq.size<2*NSAMP: continue
        mag=np.hypot(iq[0:2*NSAMP:2],iq[1:2*NSAMP:2])
        if rp>0: mag/=rp
        fp=int(round(fps))
        if fp-PRE<0 or fp+POST>NSAMP: continue
        yield ep,nm,mag[fp-PRE:fp+POST]

z0=900
MINEX=float(sys.argv[2]) if len(sys.argv)>2 else 0.0   # min bistatic excess delay (mm) to keep -> near-field gate
mintap=PRE+int(round(MINEX/MM_PER_NS/SAMP_NS)); mintap=max(mintap,PRE+3)
print(f"near-field gate: min excess {MINEX:.0f}mm -> min tap {mintap-PRE}")
xs=np.arange(-300,4800,35); ys=np.arange(-400,3600,35); GX,GY=np.meshgrid(xs,ys)
img=np.zeros(GX.shape); hits=np.zeros(GX.shape); nframe=0
for cd in sorted(glob.glob(f'{BASE}/chunk*')):
    num2name=chunk_cfg_map(cd)
    for L,rx in LIS.items():
        if rx is None: continue
        pdir=f'{cd}/{L}'; tmap=probe_tagmap(pdir,num2name)
        for t,nm,tail in cir_frames(pdir,set(ROTO),tmap):
            Tx=tag_at(nm,t)
            if Tx is None: continue
            pk=tail[PRE] if tail[PRE]>0 else tail.max(); tl=tail/pk
            base=np.linalg.norm(Tx-rx)
            dT=np.sqrt((GX-Tx[0])**2+(GY-Tx[1])**2+(z0-Tx[2])**2)
            dR=np.sqrt((GX-rx[0])**2+(GY-rx[1])**2+(z0-rx[2])**2)
            tap=PRE+np.round((dT+dR-base)/MM_PER_NS/SAMP_NS).astype(int)
            v=(tap>=mintap)&(tap<PRE+POST); amp=np.zeros(GX.shape); amp[v]=tl[tap[v]]
            img+=amp; hits+=v; nframe+=1
print(f"back-projected {nframe} RotoArm CIR frames")
img=img/np.maximum(hits,1)
fig,ax=plt.subplots(figsize=(11,8))
im=ax.pcolormesh(xs,ys,img,shading='auto',cmap='inferno'); fig.colorbar(im,ax=ax,label='SAR back-projected echo')
bx=[min(p[0] for p in A.values()),max(p[0] for p in A.values())]; byy=[min(p[1] for p in A.values()),max(p[1] for p in A.values())]
ax.plot([bx[0],bx[1],bx[1],bx[0],bx[0]],[byy[0],byy[0],byy[1],byy[1],byy[0]],'c-',lw=1,alpha=0.6)
for lab,p in A.items(): ax.plot(p[0],p[1],'s',ms=7,color='cyan'); ax.annotate(lab,(p[0],p[1]),color='cyan',fontsize=8)
for nm in ROTO:
    if nm in traj: P=np.array([x for _,x in traj[nm]]); ax.plot(P[:,0],P[:,1],'.',ms=1,color='lime',alpha=0.3)
ax.set_aspect('equal'); ax.set_xlabel('x (mm)'); ax.set_ylabel('y (mm)')
ax.set_title(f'Circular-SAR room image ({nframe} frames, near-field gate {MINEX:.0f}mm)')
tag=f'_gate{int(MINEX)}' if MINEX>0 else ''
out=os.path.join(os.path.dirname(__file__),'figs_20260704',f'roto_sar_image{tag}.png'); os.makedirs(os.path.dirname(out),exist_ok=True)
plt.savefig(out,dpi=130); print('saved',out)
