#!/usr/bin/env python3
"""Differential multistatic CIR imaging (fixed aperture).
Static room baseline vs perturbed. Per (tag,probe) link: aligned mean CIR tail.
A change at X puts energy at excess delay (|Tx-X|+|X-Rx|-|Tx-Rx|). Back-project the
|perturbed - baseline| tail delta onto the room grid -> the moved thing lights up where
the CHANGED ellipsoids intersect; the static PSF cancels.
Geometry: 3 wand tags + 3 wand listeners at measured centroid C; LB/LE/LF at anchors.
Usage: python3 diff_backproject.py <baseline_dir> <perturbed_dir> [cx cy cz]"""
import csv, glob, os, sys, json, numpy as np, matplotlib
matplotlib.use('Agg'); import matplotlib.pyplot as plt
BASE0=sys.argv[1]; BASE1=sys.argv[2]
MM_PER_NS=299.792458; SAMP_NS=1.0016; NSAMP=1016; PRE=8; POST=120
lay=json.load(open('SS-TWR/alt-SS-TWR/broadcast/logs/autopos_v3box_noref_20260704_030908/solve_v3_box/anchor_layout_v3_box.json'))
A={a['label']:np.array([a['x_mm'],a['y_mm'],a['z_mm']],float) for a in lay['anchors']}
C=np.array([float(sys.argv[3]),float(sys.argv[4]),float(sys.argv[5])]) if len(sys.argv)>=6 else np.array([2458.,1317.,961.])
TAGS=[('4','BSCCF4','LCCF4'),('6','BS9336','L9336'),('10','BS955A','L955A')]
RX={'LB':A['B'],'LE':A['E'],'LF':A['F'],'LCCF4':C,'L9336':C,'L955A':C}
print(f"volume center C={C.round().tolist()}")

def mean_cir(base,probe,tid):
    D=glob.glob(f'{base}/{probe}/listener_*')
    if not D: return None
    meta={}
    try:
        for r in csv.DictReader(open(f'{D[0]}/lcirm.csv')):
            if r['tag_id']!=tid: continue
            try: meta[r['accepted_polls']]=(float(r['fp_index'])/64.0,float(r['rxpacc']))
            except: pass
        segs={}
        for r in csv.DictReader(open(f'{D[0]}/lcird.csv')):
            if r['accepted_polls'] in meta: segs.setdefault(r['accepted_polls'],[]).append((int(r['offset']),r['hex']))
    except FileNotFoundError: return None
    acc=np.zeros(PRE+POST); n=0
    for ap,seg in segs.items():
        fps,rp=meta[ap]; seg.sort(); h=''.join(x for _,x in seg)
        try: iq=np.frombuffer(bytes.fromhex(h),dtype='<i2').astype(float)
        except: continue
        if iq.size<2*NSAMP: continue
        mag=np.hypot(iq[0:2*NSAMP:2],iq[1:2*NSAMP:2])
        if rp>0: mag/=rp
        fp=int(round(fps))
        if fp-PRE<0 or fp+POST>NSAMP: continue
        acc+=mag[fp-PRE:fp+POST]; n+=1
    return acc/n if n>10 else None

# per-link tail delta (normalized to each state's own first-path)
links=[]; report=[]
for tid,tname,lname in TAGS:
    for rx in RX:
        m0=mean_cir(BASE0,rx,tid); m1=mean_cir(BASE1,rx,tid)
        if m0 is None or m1 is None: continue
        t0=m0/(m0[PRE] if m0[PRE]>0 else m0.max()); t1=m1/(m1[PRE] if m1[PRE]>0 else m1.max())
        d=np.abs(t1-t0)                      # tail change
        base=np.linalg.norm(C-RX[rx])
        links.append((C,RX[rx],d,base))
        report.append((f'{tname}->{rx}', float(d[PRE+3:].max()), float(np.sqrt(np.mean(d[PRE+3:]**2)))))
print(f"{len(links)} links")
print(f"{'link':16s} {'maxΔ':>7s} {'rmsΔ':>7s}")
for nm,mx,rm in sorted(report,key=lambda x:-x[1]): print(f"{nm:16s} {mx:7.3f} {rm:7.3f}")

# back-project the delta
z0=C[2]; xs=np.arange(-300,4800,35); ys=np.arange(-400,3600,35)
GX,GY=np.meshgrid(xs,ys); img=np.zeros(GX.shape); hits=np.zeros(GX.shape)
for Tx,Rx,d,base in links:
    dT=np.sqrt((GX-Tx[0])**2+(GY-Tx[1])**2+(z0-Tx[2])**2)
    dR=np.sqrt((GX-Rx[0])**2+(GY-Rx[1])**2+(z0-Rx[2])**2)
    tap=PRE+np.round((dT+dR-base)/MM_PER_NS/SAMP_NS).astype(int)
    valid=(tap>=PRE+3)&(tap<PRE+POST)
    amp=np.zeros(GX.shape); amp[valid]=d[tap[valid]]; img+=amp; hits+=valid
img=img/np.maximum(hits,1)

fig,ax=plt.subplots(1,2,figsize=(18,7))
gmax=float(np.max([r[1] for r in report])) if report else 0
im=ax[1].pcolormesh(xs,ys,img,shading='auto',cmap='inferno'); fig.colorbar(im,ax=ax[1],label='|ΔCIR| back-projected')
bx=[min(p[0] for p in A.values()),max(p[0] for p in A.values())]; byy=[min(p[1] for p in A.values()),max(p[1] for p in A.values())]
ax[1].plot([bx[0],bx[1],bx[1],bx[0],bx[0]],[byy[0],byy[0],byy[1],byy[1],byy[0]],'c-',lw=1,alpha=0.6)
for lab,p in A.items(): ax[1].plot(p[0],p[1],'s',ms=7,color='cyan'); ax[1].annotate(lab,(p[0],p[1]),color='cyan',fontsize=8)
ax[1].plot(C[0],C[1],'*',ms=18,color='lime'); ax[1].set_aspect('equal'); ax[1].set_xlabel('x (mm)'); ax[1].set_ylabel('y (mm)')
ax[1].set_title(f'Differential back-projection (change lights up; peak link Δ={gmax:.3f})')
# per-link delta bars
nm=[r[0] for r in report]; mx=[r[1] for r in report]
ax[0].barh(range(len(nm)),mx); ax[0].set_yticks(range(len(nm))); ax[0].set_yticklabels(nm,fontsize=7)
ax[0].axvline(0.02,color='r',ls=':',label='~1% floor'); ax[0].set_xlabel('max tail |Δ| (rel FP)'); ax[0].legend(fontsize=8); ax[0].invert_yaxis()
ax[0].set_title('Per-link tail change (baseline vs perturbed)')
fig.suptitle('Differential CIR imaging — did the perturbation exceed the floor, and where?',fontsize=12)
plt.tight_layout(rect=[0,0,1,0.95])
out=os.path.join(os.path.dirname(__file__),'figs_20260704','diff_image.png'); os.makedirs(os.path.dirname(out),exist_ok=True)
plt.savefig(out,dpi=120); print('saved',out)
