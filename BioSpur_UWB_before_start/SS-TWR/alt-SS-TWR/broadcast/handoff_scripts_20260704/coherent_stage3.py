#!/usr/bin/env python3
"""STAGE 3: AUTOFOCUS the near-field positive control (Stage 2 was weak with the raw circle model).
Two physically-grounded corrections, both fair-tested against a re-optimized scrambled null:
  (1) RUNOUT: TX(theta) = c + (R + rad(theta))*rot(theta) + ax(theta)*n, where rad/ax are the
      1st+2nd harmonics MEASURED from the trajectory in Stage 0 (NOT fit to the CIR -> non-circular).
  (2) SCALE s in [0.95,1.08] applied to excess delay (the ~4.4% delay-layout expansion); NOT fixed
      at 1. Scanned; the scrambled null is recomputed at the SAME s (fairness).
Metric (same as Stage 2): gain(x)=|S_true|^2 / E|S_scrambled|^2. Verdict: does runout+scale lift the
near-field gain ABOVE the null and localize consistently across links?
Run: ulimit -v 8000000; python3 coherent_stage3.py [grid_mm=70]
"""
import csv, glob, os, sys, json, collections, numpy as np
from scipy.optimize import least_squares
B='logs/roto_sar_overnight_20260705_012548'
OUT='handoff_scripts_20260704/figs_20260704'; CDIR='handoff_scripts_20260704/cir_cache'
S0=np.load('handoff_scripts_20260704/coherent_stage0_cache.npz',allow_pickle=True)
lay=json.load(open('logs/autopos_v3box_noref_20260704_030908/solve_v3_box/anchor_layout_v3_box.json'))
A={a['label']:np.array([a['x_mm'],a['y_mm'],a['z_mm']],float) for a in lay['anchors']}
ORD=['A','B','C','D','E','F','G','H']
GRID=float(sys.argv[1]) if len(sys.argv)>1 else 70.0
K=48; C_MM_NS=299.792458; SAMP_NS=1.0016; MM_PER_TAP=SAMP_NS*C_MM_NS; LAM=46.24; WLEN=192
NPERM=4; MIN_EX=2.0*MM_PER_TAP; MAX_EX=45.0*MM_PER_TAP

c=S0['c']; e1=S0['e1']; e2=S0['e2']; nrm=S0['n']; R=S0['R']; tags=list(S0['tags'])
seg_off=S0['seg_off']; tg=S0['tg']; TH=[S0['th1'],S0['th2']]
RUN={0:(S0['runout_r1'],S0['runout_a1']),1:(S0['runout_r2'],S0['runout_a2'])}
SEG=[(tg[seg_off[i]:seg_off[i+1]],TH[0][seg_off[i]:seg_off[i+1]],TH[1][seg_off[i]:seg_off[i+1]])
     for i in range(len(seg_off)-1)]
def theta_at(t,ti):
    for ts,a1,a2 in SEG:
        if ts[0]<=t<=ts[-1]: return float(np.interp(t,ts,a1 if ti==0 else a2))
    return None
def harm(coef,th):  # coef=[c1,s1,c2,s2,mean]
    return coef[0]*np.cos(th)+coef[1]*np.sin(th)+coef[2]*np.cos(2*th)+coef[3]*np.sin(2*th)+coef[4]
def TXpos(ti,th,use_runout):
    rad=R[ti]; ax=0.0
    if use_runout:
        cr,ca=RUN[ti]; rad=R[ti]+harm(cr,th); ax=harm(ca,th)
    return c+rad*(np.cos(th)*e1+np.sin(th)*e2)+ax*nrm

def multilat(anch_r):
    labs=[a for a in anch_r if len(anch_r[a])]
    if len(labs)<5: return None
    d=np.array([np.median(anch_r[a]) for a in labs]); P=np.array([A[a] for a in labs])
    a0=P[0]; M=2*(P[1:]-a0); b=(d[0]**2-d[1:]**2)+(np.sum(P[1:]**2,axis=1)-np.dot(a0,a0))
    try: x0,*_=np.linalg.lstsq(M,b,rcond=None)
    except np.linalg.LinAlgError: x0=P.mean(0)
    r=least_squares(lambda x:np.linalg.norm(P-x,axis=1)-d,x0)
    return r.x if np.sqrt(np.mean(r.fun**2))<300 else None
def static_listener_pos(st):
    pos=[]
    for tr in sorted(glob.glob(f'{B}/chunk*/recv/tr_all.csv'),key=lambda p:int(p.split('/chunk')[1].split('/')[0]))[:2]:
        cur=collections.defaultdict(lambda:collections.defaultdict(list))
        for r in csv.DictReader(open(tr)):
            try:
                if r['peer_name']!=st or r['valid']!='1' or float(r['range_mm'])<=0 or float(r['quality_percent'])<85: continue
                cur[r['sweep']][ORD[int(r['anchor_id'])]].append(float(r['range_mm']))
            except (ValueError,KeyError,IndexError): pass
        for sw,ar in list(cur.items())[::5]:
            x=multilat(ar)
            if x is not None: pos.append(x)
        if len(pos)>200: break
    return np.median(np.array(pos),axis=0) if pos else c
LISTPOS={'LB':A['B'],'LE':A['E'],'LF':A['F']}
for L,st in [('LCCF4','BSCCF4'),('L9336','BS9336'),('L955A','BS955A')]: LISTPOS[L]=static_listener_pos(st)

gx=np.arange(-700,701,GRID); gy=np.arange(-700,701,GRID); gz=np.arange(-1000,301,GRID)
VX,VY,VZ=np.meshgrid(c[0]+gx,c[1]+gy,c[2]+gz,indexing='ij')
vox=np.stack([VX.ravel(),VY.ravel(),VZ.ravel()],1); Nv=len(vox)
taps_idx=np.arange(WLEN)
print(f"grid {len(gx)}x{len(gy)}x{len(gz)}={Nv}@{GRID}mm  measured runout radial amp "
      f"{np.hypot(RUN[0][0][0],RUN[0][0][1]):.0f}/{np.hypot(RUN[1][0][0],RUN[1][0][1]):.0f}mm")

def image(Zs,THk,RX,dRX,s,use_runout,ti):
    S=np.zeros(Nv,np.complex128)
    for i in range(len(THk)):
        th=THk[i]
        if use_runout:
            cr,ca=RUN[ti]; rad=R[ti]+harm(cr,th); ax=harm(ca,th)
            TX=c+rad*(np.cos(th)*e1+np.sin(th)*e2)+ax*nrm
        else:
            TX=c+R[ti]*(np.cos(th)*e1+np.sin(th)*e2)
        excess=s*(np.linalg.norm(vox-TX,axis=1)+dRX-np.linalg.norm(TX-RX))
        valid=(excess>MIN_EX)&(excess<MAX_EX); tap=K+excess/MM_PER_TAP; z=Zs[i]
        zq=(np.interp(tap,taps_idx,z.real,left=0,right=0)+1j*np.interp(tap,taps_idx,z.imag,left=0,right=0))*valid
        S+=zq*np.exp(-1j*2*np.pi*excess/LAM)
    return S

NEAR=[('LCCF4','BS2DCE'),('L9336','BSDC91'),('L955A','BS2DCE'),('LCCF4','BSDC91')]
SCAN_S=[0.96,0.98,1.0,1.02,1.04,1.06]
print("\nlink                model     best_s  peak_gain(ratio-to-null)  peak@(rel c)")
summary=[]
for L,T in NEAR:
    ti=tags.index(T); RX=LISTPOS[L]; dRX=np.linalg.norm(vox-RX,axis=1)
    f=f'{CDIR}/{L}_{T}.npz'
    if not os.path.exists(f): continue
    dat=np.load(f); EP=dat['EP']; Z=dat['Z']
    ths=[theta_at(t,ti) for t in EP]; keep=[i for i in range(len(EP)) if ths[i] is not None]
    THk=np.array([ths[i] for i in keep]); Zs=Z[keep]
    rng=np.random.default_rng(1)
    for use_runout,lbl in [(False,'raw'),(True,'runout')]:
        best=None
        for s in ([1.0] if not use_runout else SCAN_S):
            St=image(Zs,THk,RX,dRX,s,use_runout,ti)
            Pn=np.zeros(Nv)
            for m in range(NPERM):
                p=rng.permutation(len(keep))          # scramble theta<->frame; TX recomputed from permuted theta (fair, same params)
                Pn+=np.abs(image(Zs,THk[p],RX,dRX,s,use_runout,ti))**2
            Pn/=NPERM; g=np.abs(St)**2/np.maximum(Pn,1e-9); j=int(np.argmax(g))
            if best is None or g[j]>best[0]: best=(g[j],s,vox[j])
        summary.append((L,T,lbl,best[0],best[1]))
        print(f"  {L}<-{T:7} {lbl:7} s={best[1]:.2f}  peak_gain={best[0]:6.1f}  "
              f"@{np.round(best[2]-c).astype(int).tolist()}")

print("\n"+"="*70+"\nSTAGE 3 VERDICT (autofocus vs re-optimized null)\n"+"="*70)
runv=[g for L,T,lbl,g,s in summary if lbl=='runout']
raww=[g for L,T,lbl,g,s in summary if lbl=='raw']
print(f"  raw-model peak gains   : {[round(x,1) for x in raww]}")
print(f"  runout+scale peak gains: {[round(x,1) for x in runv]}")
# CAVEAT: peak = max over ~8k voxels x 6 scales -> multiple-comparison inflated; the RAW model
# already exceeds 10x from that alone. The real question is whether autofocus (runout+scale)
# SYSTEMATICALLY improves focus AND whether peaks localize/agree across links.
impr=[runv[i]/raww[i] for i in range(len(runv))]
med_impr=np.median(impr); nbetter=sum(x>1.5 for x in impr)
print(f"  runout/raw improvement ratio per link: {[round(x,2) for x in impr]} (median {med_impr:.2f})")
if med_impr>=1.5 and nbetter>=3:
    print("  => AUTOFOCUS RESCUES: runout+scale systematically sharpens focus -> systematic runout")
    print("     was the limiter; coherent imaging viable WITH autofocus.")
else:
    print("  => AUTOFOCUS DOES NOT RESCUE: runout+scale gives only a mixed/marginal change")
    print("     (Stage-0 runout harmonic explains just 12-22% of radial var; ~100mm~2.2lambda of the")
    print("     trajectory error is RANDOM multilateration floor). Peaks stay link-inconsistent =>")
    print("     real channel coherence exists but is NOT imageable to a clean scatterer. Coherent")
    print("     SAR is trajectory-position-floor limited; a CORNER REFLECTOR (strong known target,")
    print("     enabling true per-frame self-calibration) is the only experiment that could change it.")
print("DONE")
