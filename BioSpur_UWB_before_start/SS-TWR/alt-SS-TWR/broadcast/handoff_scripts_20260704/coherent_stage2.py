#!/usr/bin/env python3
"""STAGE 2: POSITIVE CONTROL for the coherent matched filter. Focus on the near-field STATIC
structure around the RotoArm (motor mount / tripod / floor under the arm) using the Stage-0
fitted-circle trajectory + phase model. If this focuses, the trajectory+phase model is PROVEN on
real data and any later wall-null is a weak echo, not broken coherence. If it fails, coherence is
trajectory-limited (-> Stage 3 autofocus; still fails -> mechanical/trajectory blocker).

Matched filter (per link, RX=listener fixed, TX=tag on the fitted circle):
  excess(x,theta) = |TX(theta)-x| + |x-RX| - |TX(theta)-RX|          [mm]
  S(x) = Σ_f  z_f( tap = K + excess/mm_per_tap )  * exp(-j 2π excess/λ)     (frac-tap interp)
Self-normalizing coherent gain (== |S|^2 / E|S_scrambled|^2 since a phase-scramble preserves
magnitudes): gain(x) = |S(x)|^2 / Σ_f |z_f(tap)|^2   in [1, N].  Explicit theta-scramble null
also computed on the peak voxel as a sanity anchor.
Also: per-revolution coherent / cross-rev incoherent stacking (S_rev[k] coherent within rev k,
Σ_k |S_rev[k]|^2), to localize inter-revolution drift.

Run: ulimit -v 8000000; python3 coherent_stage2.py [grid_mm=45] [frame_stride=1]
"""
import csv, glob, os, sys, json, collections, numpy as np
import matplotlib; matplotlib.use('Agg'); import matplotlib.pyplot as plt
from scipy.optimize import least_squares
B='logs/roto_sar_overnight_20260705_012548'
OUT='handoff_scripts_20260704/figs_20260704'; CDIR='handoff_scripts_20260704/cir_cache'
S0=np.load('handoff_scripts_20260704/coherent_stage0_cache.npz',allow_pickle=True)
lay=json.load(open('logs/autopos_v3box_noref_20260704_030908/solve_v3_box/anchor_layout_v3_box.json'))
A={a['label']:np.array([a['x_mm'],a['y_mm'],a['z_mm']],float) for a in lay['anchors']}
ORD=['A','B','C','D','E','F','G','H']
GRID=float(sys.argv[1]) if len(sys.argv)>1 else 45.0
STRIDE=int(sys.argv[2]) if len(sys.argv)>2 else 1
K=48; C_MM_NS=299.792458; SAMP_NS=1.0016; MM_PER_TAP=SAMP_NS*C_MM_NS; LAM=46.24; WLEN=192

c=S0['c']; e1=S0['e1']; e2=S0['e2']; R=S0['R']; tags=list(S0['tags'])
seg_off=S0['seg_off']; tg=S0['tg']; TH=[S0['th1'],S0['th2']]
SEG=[(tg[seg_off[i]:seg_off[i+1]], TH[0][seg_off[i]:seg_off[i+1]], TH[1][seg_off[i]:seg_off[i+1]])
     for i in range(len(seg_off)-1)]
print(f"stage0: center={c.round().astype(int).tolist()} R={R.round().tolist()} segs={len(SEG)} "
      f"Delta-phi={float(S0['dphi']):+.0f}+-{float(S0['dphi_sig']):.0f}deg")

def theta_at(t,ti):
    for ts,a1,a2 in SEG:
        if ts[0]<=t<=ts[-1]: return float(np.interp(t,ts,a1 if ti==0 else a2))
    return None

# ---- listener positions: LB/LE/LF = anchors B/E/F; wand-side = median multilat of static tag ----
def multilat(anch_r):
    labs=[a for a in anch_r if len(anch_r[a])]
    if len(labs)<5: return None
    d=np.array([np.median(anch_r[a]) for a in labs]); P=np.array([A[a] for a in labs])
    a0=P[0]; M=2*(P[1:]-a0); b=(d[0]**2-d[1:]**2)+(np.sum(P[1:]**2,axis=1)-np.dot(a0,a0))
    try: x0,*_=np.linalg.lstsq(M,b,rcond=None)
    except np.linalg.LinAlgError: x0=P.mean(0)
    r=least_squares(lambda x:np.linalg.norm(P-x,axis=1)-d,x0)
    return r.x if np.sqrt(np.mean(r.fun**2))<300 else None
def static_listener_pos(statictag):
    pos=[]
    for tr in sorted(glob.glob(f'{B}/chunk*/recv/tr_all.csv'),key=lambda p:int(p.split('/chunk')[1].split('/')[0]))[:2]:
        cur=collections.defaultdict(lambda:collections.defaultdict(list))
        for r in csv.DictReader(open(tr)):
            try:
                if r['peer_name']!=statictag or r['valid']!='1' or float(r['range_mm'])<=0 or float(r['quality_percent'])<85: continue
                cur[r['sweep']][ORD[int(r['anchor_id'])]].append(float(r['range_mm']))
            except (ValueError,KeyError,IndexError): pass
        for sw,ar in list(cur.items())[::5]:
            x=multilat(ar)
            if x is not None: pos.append(x)
        if len(pos)>200: break
    return np.median(np.array(pos),axis=0) if pos else None
LISTPOS={'LB':A['B'],'LE':A['E'],'LF':A['F']}
for L,st in [('LCCF4','BSCCF4'),('L9336','BS9336'),('L955A','BS955A')]:
    p=static_listener_pos(st); LISTPOS[L]=p if p is not None else c
    print(f"  listener {L} @ {np.round(LISTPOS[L]).astype(int).tolist()}  "
          f"(|center|={np.linalg.norm(LISTPOS[L]-c):.0f}mm)")

# ---- voxel grid around arm center (extend downward toward floor/mount) ----
gx=np.arange(-700,701,GRID); gy=np.arange(-700,701,GRID); gz=np.arange(-1000,301,GRID)
VX,VY,VZ=np.meshgrid(c[0]+gx,c[1]+gy,c[2]+gz,indexing='ij')
vox=np.stack([VX.ravel(),VY.ravel(),VZ.ravel()],1)   # (Nv,3)
Nv=len(vox); print(f"voxel grid {len(gx)}x{len(gy)}x{len(gz)} = {Nv} voxels @ {GRID}mm")

NEAR=[('LCCF4','BS2DCE'),('LCCF4','BSDC91'),('L9336','BS2DCE'),('L9336','BSDC91'),
      ('L955A','BS2DCE'),('L955A','BSDC91')]
taps_idx=np.arange(WLEN)
MIN_EX=2.0*MM_PER_TAP; MAX_EX=45.0*MM_PER_TAP     # mild echo gate; ratio-to-null cancels the rest
NPERM=5                                            # scrambled-theta realizations for E|S_scram|^2

def _accum(Zs, TXs, dRX):
    """coherent sum S over frames given per-frame z-window and TX position (vectorized voxels)."""
    S=np.zeros(Nv,np.complex128)
    for i in range(len(TXs)):
        excess=np.linalg.norm(vox-TXs[i],axis=1)+dRX-np.linalg.norm(TXs[i]-LISTPOS_RX)
        valid=(excess>MIN_EX)&(excess<MAX_EX)
        tap=K+excess/MM_PER_TAP; z=Zs[i]
        zq=(np.interp(tap,taps_idx,z.real,left=0,right=0)+1j*np.interp(tap,taps_idx,z.imag,left=0,right=0))*valid
        S+=zq*np.exp(-1j*2*np.pi*excess/LAM)
    return S

LISTPOS_RX=None
def matched_filter(link):
    global LISTPOS_RX
    L,T=link; ti=tags.index(T); RX=LISTPOS[L]; LISTPOS_RX=RX
    f=f'{CDIR}/{L}_{T}.npz'
    if not os.path.exists(f): return None
    dat=np.load(f); EP=dat['EP']; Z=dat['Z']
    ths=[theta_at(t,ti) for t in EP]
    keep=[i for i in range(0,len(EP),STRIDE) if ths[i] is not None]
    if len(keep)<50: return None
    TH=np.array([ths[i] for i in keep]); Zs=Z[keep]
    TXs=c+np.outer(np.cos(TH),e1)*R[ti]+np.outer(np.sin(TH),e2)*R[ti]
    dRX=np.linalg.norm(vox-RX,axis=1)
    S_true=_accum(Zs,TXs,dRX)
    # scrambled-theta null: same z_i, permuted TX -> E|S_scram|^2 (cancels the array-factor of the
    # near-constant FP background, which is present identically in true and scrambled)
    rng=np.random.default_rng(0); Pnull=np.zeros(Nv)
    for m in range(NPERM):
        perm=rng.permutation(len(keep))
        Pnull+=np.abs(_accum(Zs,TXs[perm],dRX))**2
    Pnull/=NPERM
    gain=np.abs(S_true)**2/np.maximum(Pnull,1e-9)      # == user's |S|^2 / E|S_scrambled|^2
    return dict(link=link,gain=gain,nfr=len(keep),S=S_true,Pnull=Pnull)

results=[]
print("\n  gain = |S_true|^2 / E|S_scrambled|^2  (ratio-to-null; ~1 = no coherent focus, >>1 = real)")
for link in NEAR:
    r=matched_filter(link)
    if r is None: print(f"  {link}: no cache"); continue
    g=r['gain']; j=int(np.argmax(g)); pk=vox[j]
    p50=np.median(g[g>0]); p999=np.percentile(g[g>0],99.9)
    results.append(r)
    print(f"  {r['link'][0]}<-{r['link'][1]}: N={r['nfr']:5d}  PEAK gain={g[j]:5.2f}x  "
          f"median={p50:.2f} 99.9pct={p999:.2f}  peak@{np.round(pk-c).astype(int).tolist()}(rel c)")

# figure: max-projection gain map of strongest link
if results:
    best=max(results,key=lambda r:r['gain'].max())
    G=best['gain'].reshape(len(gx),len(gy),len(gz))
    fig,ax=plt.subplots(1,3,figsize=(13,4))
    for k,lab in enumerate(['proj over X (Y-Z)','proj over Y (X-Z)','proj over Z (X-Y)']):
        im=ax[k].imshow(G.max(axis=k).T,origin='lower',aspect='auto',cmap='inferno')
        ax[k].set_title(f'{best["link"][0]}<-{best["link"][1]}  {lab}',fontsize=9); plt.colorbar(im,ax=ax[k])
    plt.tight_layout(); plt.savefig(f'{OUT}/coherent_stage2_posctrl.png',dpi=110); plt.close()

# VERDICT: peak must beat its own null bulk (99.9 pct) AND be >=~10x, on >=2 links
if results:
    print("\n"+"="*70+"\nSTAGE 2 VERDICT (positive control, ratio-to-scrambled-null)\n"+"="*70)
    peaks=[r['gain'].max() for r in results]
    over=[r['gain'].max()/np.percentile(r['gain'][r['gain']>0],99.9) for r in results]
    print(f"  peak gains over null: {[round(x,2) for x in peaks]}")
    print(f"  peak / own-99.9pct (localization): {[round(x,2) for x in over]}")
    strong=[i for i in range(len(results)) if peaks[i]>=10 and over[i]>=3]
    print(f"  links with peak>=10x null AND >=3x localized: {len(strong)}/{len(results)}")
    if len(strong)>=2:
        print("  => POSITIVE CONTROL PASSES: trajectory+phase model focuses REAL near-field structure.")
    else:
        print("  => POSITIVE CONTROL FAILS/WEAK: no coherent focus above the scrambled null.")
        print("     The apparent raw gain was the array-factor of the near-constant FP background")
        print("     (cancels in the ratio). => coherent imaging is trajectory-knowledge-limited on")
        print("     this rig. Next per flowchart: Stage-3 autofocus on the near field.")
print("DONE")
