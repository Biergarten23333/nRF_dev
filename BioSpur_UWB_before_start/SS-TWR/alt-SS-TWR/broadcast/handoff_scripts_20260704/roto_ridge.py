#!/usr/bin/env python3
"""Prompt-2: decide whether roto_sar data contains TRACKABLE SPECULAR WALL ECHOES before
investing in full plane fitting. Bin CIR by RotoArm rotation angle theta, look for echo RIDGES
in the (theta, excess-delay) heatmap, and overlay the image-theory mirror-model prediction for
each of the 4 side walls. A wall that is specular AND in view produces a ridge that follows the
predicted delay(theta) curve; diffuse back-projection (which failed) would smear it away.

Reuses roto_sar_image.py machinery (circle fit, trajectory, tag map) + phase_audit fractional
FP alignment (magnitude only here -- incoherent is fine for ridge detection).
Usage: python3 roto_ridge.py [n_chunks]"""
import csv, glob, os, sys, json, collections, re, numpy as np
import matplotlib; matplotlib.use('Agg'); import matplotlib.pyplot as plt
from scipy.optimize import least_squares
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'scripts'))
from tag_roster import load_roster            # single source of truth for tag_id<->name

BASE='logs/roto_sar_overnight_20260705_012548'
OUT='handoff_scripts_20260704/figs_20260704'; os.makedirs(OUT,exist_ok=True)
MM_PER_NS=299.792458; SAMP_NS=1.0016; NSAMP=1016; K=48; WSTART_BACK=48; WLEN=192
NCH=int(sys.argv[1]) if len(sys.argv)>1 else 99
lay=json.load(open('logs/autopos_v3box_noref_20260704_030908/solve_v3_box/anchor_layout_v3_box.json'))
A={a['label']:np.array([a['x_mm'],a['y_mm'],a['z_mm']],float) for a in lay['anchors']}
ORD=['A','B','C','D','E','F','G','H']
LIS={'LB':A['B'],'LE':A['E'],'LF':A['F'],'LCCF4':None,'L9336':None,'L955A':None}
ROTO=['BS2DCE','BSDC91']; Z0=900
NBIN=72; DEG=5; TAP_LO=2; TAP_HI=35     # excess-delay taps to examine (2..35 ns)

def frac_align(c,fp):
    start=int(round(fp))-WSTART_BACK
    if start<0 or start+WLEN>NSAMP: return None
    w=c[start:start+WLEN].astype(complex); frac=fp-start-K
    k=np.fft.fftfreq(WLEN)*WLEN
    return np.fft.ifft(np.fft.fft(w)*np.exp(1j*2*np.pi*k*frac/WLEN))

# ---------- trajectory (reuse roto_sar_image approach) ----------
def solve_pos(ba):
    labs=[a for a in ba if ba[a]]
    if len(labs)<4: return None
    d=np.array([np.median(ba[a]) for a in labs]); P=np.array([A[a] for a in labs])
    return least_squares(lambda x:np.linalg.norm(P-x,axis=1)-d,P.mean(0)).x
traj=collections.defaultdict(list)
for tr in sorted(glob.glob(f'{BASE}/chunk*/recv/tr_all.csv'))[:NCH]:
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
            if x is not None: traj[nm].append((t0+b,x))
for nm in traj: traj[nm].sort()
print("tags with trajectory:",{k:len(v) for k,v in traj.items()})
def wand_pos(nm):
    return np.median(np.array([x for _,x in traj[nm]]),axis=0) if traj.get(nm) else None
for nm,L in [('BSCCF4','LCCF4'),('BS9336','L9336'),('BS955A','L955A')]:
    p=wand_pos(nm)
    if p is not None: LIS[L]=p
print("listener positions:",{k:(v.round().astype(int).tolist() if v is not None else None) for k,v in LIS.items()})

# ---------- circle fit -> center, plane basis, per-tag angle(t) ----------
FIT={}
for nm in ROTO:
    if len(traj.get(nm,[]))<20: continue
    P=np.array([x for _,x in traj[nm]]); c=P.mean(0)
    U,S,Vt=np.linalg.svd(P-c, full_matrices=False); e1,e2,n=Vt[0],Vt[1],Vt[2]
    FIT[nm]=(c,e1,e2,n)
    print(f"{nm}: center={c.round().astype(int).tolist()} normal={n.round(2).tolist()}")
def interp(nm,t):
    tr=traj.get(nm,[])
    if len(tr)<2: return None
    ts=np.array([x[0] for x in tr]); i=max(1,min(np.searchsorted(ts,t),len(tr)-1))
    (t0,p0),(t1,p1)=tr[i-1],tr[i]
    if t1==t0: return p0
    a=min(1,max(0,(t-t0)/(t1-t0))); return p0*(1-a)+p1*a
def angle_at(nm,t):
    if nm not in FIT: return None
    p=interp(nm,t)
    if p is None: return None
    c,e1,e2,_=FIT[nm]; d=p-c
    return np.degrees(np.arctan2(d@e2,d@e1))%360, p

# ---------- tag map: SINGLE SOURCE OF TRUTH (scripts/tag_roster.py), no hardcoded maps ----------
TAGMAP=load_roster(BASE)['by_id']              # {tag_id_str: BS name} from the runtime CFG

# ---------- reassemble aligned magnitude frames ----------
def frames(pdir,tmap):
    D=glob.glob(f'{pdir}/listener_*')
    if not D: return
    for d in D:
        mfile=f'{d}/lcirm.csv'; dfile=f'{d}/lcird.csv'
        if not(os.path.exists(mfile) and os.path.exists(dfile)): continue
        meta={}
        for r in csv.DictReader(open(mfile)):
            nm=tmap.get(r['tag_id'])
            if nm not in ROTO: continue
            try: meta[r['accepted_polls']]=(float(r['fp_index'])/64.0,float(r['rxpacc']),float(r['host_epoch_s']),nm)
            except: pass
        buf={}; cur=None
        def emit(ap,sl):
            if ap not in meta or not sl: return None
            try: tot=b''.join(bytes.fromhex(sl[o]) for o in sorted(sl) if len(sl[o])%2==0)
            except ValueError: return None
            if len(tot)!=NSAMP*4: return None
            iq=np.frombuffer(tot,dtype='<i2').astype(np.float64); c=iq[0::2]+1j*iq[1::2]
            fps,rp,ep,nm=meta[ap]; w=frac_align(c,fps)
            if w is None: return None
            mag=np.abs(w)
            if rp>0: mag/=rp
            return ep,nm,mag
        for r in csv.DictReader(open(dfile)):
            try: ap=r['accepted_polls']; off=int(r['offset'])
            except: continue
            if off==0 and cur is not None:
                o=emit(cur,buf);
                if o: yield o
                buf={}
            cur=ap; buf[off]=r['hex']
        if cur is not None:
            o=emit(cur,buf)
            if o: yield o

# ---------- build M[bin,tap] per link ----------
links={}   # (L,tag) -> list-per-bin of aligned mag windows
for cd in sorted(glob.glob(f'{BASE}/chunk*'))[:NCH]:
    for L,rx in LIS.items():
        if rx is None: continue
        for ep,nm,mag in frames(f'{cd}/{L}',TAGMAP):
            res=angle_at(nm,ep)
            if res is None: continue
            th,Tx=res; b=int(th//DEG)%NBIN
            key=(L,nm)
            if key not in links: links[key]=([[] for _ in range(NBIN)], rx)
            links[key][0][b].append(mag)

# wall planes from anchor layout (state offsets explicitly)
xmin=min(p[0] for p in A.values()); xmax=max(p[0] for p in A.values())
ymin=min(p[1] for p in A.values()); ymax=max(p[1] for p in A.values())
ZFLOOR=0.0; ZCEIL=2500.0   # floor at z=0; ceiling ASSUMED 2500mm (upper anchors at ~1620)
WALLS={'x=xmin(W)':('x',xmin),'x=xmax(E)':('x',xmax),'y=ymin(S)':('y',ymin),'y=ymax(N)':('y',ymax),
       'z=0(floor)':('z',ZFLOOR),'z=2500(ceil)':('z',ZCEIL)}
print(f"wall planes: x[{xmin:.0f},{xmax:.0f}] y[{ymin:.0f},{ymax:.0f}] z[floor {ZFLOOR:.0f}, ceil {ZCEIL:.0f}]"
      f"  (RotoArm normal has large -z => floor/ceiling are the aperture's strong specular surfaces)")

def mirror(Tx,axis,val):
    m=Tx.copy()
    j={'x':0,'y':1,'z':2}[axis]; m[j]=2*val-Tx[j]
    return m

def pred_excess_taps(nm,rx,axis,val):
    """predicted excess-delay ridge (in taps past FP) vs bin center angle."""
    c,e1,e2,_=FIT[nm]; out=np.full(NBIN,np.nan)
    for b in range(NBIN):
        th=np.radians((b+0.5)*DEG)
        # TX on the fitted circle at this angle (use median radius)
        R=np.median(np.linalg.norm(np.array([x for _,x in traj[nm]])-c,axis=1))
        Tx=c+R*(np.cos(th)*e1+np.sin(th)*e2)
        Txm=mirror(Tx,axis,val)
        base=np.linalg.norm(Tx-rx); ex=np.linalg.norm(Txm-rx)-base   # mm
        out[b]=ex/MM_PER_NS/SAMP_NS
    return out

# ---------- score + plot ----------
scores=[]; panels=[]
for (L,nm),(bins,rx) in sorted(links.items()):
    tap0,tap1=K+TAP_LO,K+TAP_HI
    M=np.full((NBIN,tap1-tap0),np.nan)
    for b in range(NBIN):
        if bins[b]:
            M[b]=np.median(np.array(bins[b])[:,tap0:tap1],axis=0)
    if np.isnan(M).all(): continue
    colmed=np.nanmedian(M,axis=0); colstd=np.nanstd(M,axis=0)+1e-9
    Rsig=(M-colmed)/colstd            # background-removed, in per-tap sigma units
    panels.append((L,nm,Rsig,rx))
    for wn,(axis,val) in WALLS.items():
        pt=pred_excess_taps(nm,rx,axis,val)    # taps past FP
        vals=[]; dev=[]
        for b in range(NBIN):
            t=pt[b]-TAP_LO                       # index into Rsig columns
            if np.isnan(t) or t<0 or t>=tap1-tap0-1: continue
            lo=int(np.floor(t-1.5)); hi=int(np.ceil(t+1.5))
            seg=Rsig[b,max(0,lo):hi+1]
            if seg.size and not np.isnan(seg).all():
                vals.append(np.nanmax(seg))
                dev.append((np.nanargmax(seg)+max(0,lo)-t))
        vals=np.array(vals)
        sc=np.nanmean(vals) if len(vals) else np.nan          # mean along curve
        pk=np.nanmax(vals) if len(vals) else np.nan           # peak (angle-limited ridge)
        frac=np.mean(vals>2.0) if len(vals) else np.nan       # fraction of angles above 2 sigma
        rms=np.sqrt(np.nanmean(np.array(dev)**2))*SAMP_NS if dev else np.nan
        scores.append((L,nm,wn,sc,pk,frac,rms,len(vals)))

print("\n"+"="*80+"\nRIDGE-MATCH SCORES (background-removed sigma along predicted mirror-curve)\n"+"="*80)
print(f"  {'link':16} {'wall':12} {'mean_s':>7} {'peak_s':>7} {'frac>2s':>8} {'tau_rms_ns':>10} {'nbins':>6}")
for L,nm,wn,sc,pk,frac,rms,n in scores:
    # an angle-limited specular ridge: peak strong AND coherent over a contiguous arc (>=15% angles)
    flag=' <== RIDGE' if (pk==pk and pk>4.0 and frac>0.15) else ''
    print(f"  {L+'<-'+nm:16} {wn:12} {sc:7.2f} {pk:7.2f} {frac:8.2f} {rms:10.2f} {n:6d}{flag}")

# cache binned Rsig matrices so offset/wall re-scoring is instant (no re-reassembly)
np.savez(f'{OUT}/../roto_ridge_cache.npz',
         panels=np.array([f'{L}|{nm}' for L,nm,_,_ in panels]),
         Rsig=np.array([p[2] for p in panels]),
         rx=np.array([p[3] for p in panels]),
         TAP_LO=TAP_LO,TAP_HI=TAP_HI,NBIN=NBIN,DEG=DEG)

# montage of heatmaps with overlays
ncol=3; nrow=int(np.ceil(len(panels)/ncol))
fig,axes=plt.subplots(nrow,ncol,figsize=(5*ncol,3.2*nrow),squeeze=False)
ext=[TAP_LO*SAMP_NS,TAP_HI*SAMP_NS,0,360]
for i,(L,nm,Rsig,rx) in enumerate(panels):
    ax=axes[i//ncol][i%ncol]
    im=ax.imshow(np.clip(Rsig,-1,4),aspect='auto',origin='lower',extent=ext,cmap='inferno')
    for wn,(axis,val) in WALLS.items():
        pt=pred_excess_taps(nm,rx,axis,val)*SAMP_NS
        ax.plot(pt,(np.arange(NBIN)+0.5)*DEG,lw=1,alpha=0.8,label=wn)
    ax.set_title(f'{L}<-{nm}',fontsize=9); ax.set_xlim(TAP_LO*SAMP_NS,TAP_HI*SAMP_NS)
    if i==0: ax.legend(fontsize=5,loc='upper right')
    ax.set_xlabel('excess ns',fontsize=7); ax.set_ylabel('theta deg',fontsize=7)
for j in range(len(panels),nrow*ncol): axes[j//ncol][j%ncol].axis('off')
plt.tight_layout(); plt.savefig(f'{OUT}/roto_ridge_heatmaps.png',dpi=110); plt.close()
print(f"\nsaved {OUT}/roto_ridge_heatmaps.png")

# verdict
good=[s for s in scores if s[4]==s[4] and s[4]>4.0 and s[5]>0.15]   # peak>4sig, frac>15%
bywall=collections.Counter((s[2]) for s in good)
links_with=set((s[0],s[1]) for s in good)
print("\n"+"="*72+"\nVERDICT\n"+"="*72)
print(f"  (link,wall) pairs with specular ridge (peak>4 sigma over >15% of angles): {len(good)}")
print(f"  walls seen: {dict(bywall)}")
print(f"  distinct links showing >=1 ridge: {len(links_with)}")
strong_walls=[w for w,c in bywall.items() if c>=3]
if len(strong_walls)>=2 and len(links_with)>=3:
    print("  -> WALL/PLANE MAPPING ALIVE: proceed to joint image-theory plane fit (Step 2).")
elif good:
    print("  -> PARTIAL: some specular ridges but below the >=2walls/>=3links bar; marginal.")
else:
    print("  -> STATIC MAPPING DEAD on this rig: no specular ridge above 2 sigma. Drop that branch.")
