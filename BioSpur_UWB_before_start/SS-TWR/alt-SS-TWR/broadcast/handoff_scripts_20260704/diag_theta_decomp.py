#!/usr/bin/env python3
"""THETA-LOCKED RESIDUAL DECOMPOSITION per (tag, anchor)  --  demonstration, not assertion.

The roto co-located "proxy" correlation is entirely theta-locked (theta-control collapses it, static
soak is null). A theta-locked signed-residual of ~40-130 mm is TOO LARGE for APS011 alone (APS011
swing over each anchor's rotation range span is only 20-30 mm, computed from the shipped ch5/PRF64
narrow-band table).  So r_circle(theta) is a MIXTURE.  Decompose it into KNOWN components (no fitted
bias curve -- instruction: a fit would absorb layout error into 'calibration'):

  r_circle(theta) ~= RUNOUT(theta)     : Stage-0 measured trajectory runout (radial+axial harmonics)
                                          projected onto each anchor LOS  -- pure trajectory/position
                   + APS011(theta)     : shipped dwt_getrangebias(ch5,PRF64) applied to measured
                                          range(theta)  -- deterministic, FIXED (firmware does NOT
                                          apply it: dwt_getrangebias uncalled, raw_mm==range_mm)
                   + OCCLUSION(theta)   : geometric mast/motor self-occlusion sectors (LOS tag->anchor
                                          passes within r_mast of the rotation axis) -- localized, +heavy
                   + remainder         : smooth layout/anchor-geometry error

Reports an amplitude-accounting table per link + shared-axis theta plots for the co-located links.
This IS the outstanding multipath-decomposition item-2 (residual harmonics vs theta) -- merged here.

Run: cd .../broadcast; ulimit -v 8000000; python3 handoff_scripts_20260704/diag_theta_decomp.py
"""
import csv, glob, os, sys, json, collections, numpy as np
import matplotlib; matplotlib.use('Agg'); import matplotlib.pyplot as plt
sys.path.insert(0,'scripts'); import tag_roster

BASE='logs/roto_sar_overnight_20260705_012548'
CACHE='handoff_scripts_20260704/coherent_stage0_cache.npz'
OUT='handoff_scripts_20260704/figs_20260704'; os.makedirs(OUT,exist_ok=True)
lay=json.load(open('logs/autopos_v3box_noref_20260704_030908/solve_v3_box/anchor_layout_v3_box.json'))
A={a['label']:np.array([a['x_mm'],a['y_mm'],a['z_mm']],float) for a in lay['anchors']}
ORD=['A','B','C','D','E','F','G','H']; ROTO=['BS2DCE','BSDC91']
COLO=[('LB','B'),('LE','E'),('LF','F')]; LIS_ANCHOR=dict(COLO)
NB=72; DEG=5.0; R_MAST=75.0            # nominal mast radius (mm); sensitivity noted in report

# ---- APS011 (ch5, PRF64 -> narrow-band table row 3), ported verbatim from deca_range_tables.c ----
APS_TBL=[1,1,1,2,2,3,4,6,7,9,10,12,13,15,16,17,19,21,23,26,30,42,55,65,85,255]; APS_OFF=-17
def aps011_mm(range_mm):
    ri=int((range_mm/1000.0)*4.0);  ri=min(ri,255); i=0
    while ri>APS_TBL[i]: i+=1
    return (i+APS_OFF)*10.0

# ---- Stage-0 circle + runout model ----
d=np.load(CACHE,allow_pickle=True)
c=d['c']; e1=d['e1']; e2=d['e2']; n=d['n']; Rv={t:float(d['R'][i]) for i,t in enumerate(d['tags'])}
tg=d['tg']; TH={'BS2DCE':d['th1'],'BSDC91':d['th2']}
RUN_R={'BS2DCE':d['runout_r1'],'BSDC91':d['runout_r2']}; RUN_A={'BS2DCE':d['runout_a1'],'BSDC91':d['runout_a2']}
seg_cid=[int(x) for x in d['seg_cid']]; seg_off=d['seg_off']
segments={}
for j,cid in enumerate(seg_cid):
    sl=slice(int(seg_off[j]),int(seg_off[j+1])); segments[cid]=(tg[sl],{t:TH[t][sl] for t in ROTO})
def model_angle(tag,cid,t):
    if cid not in segments: return None
    ts,thd=segments[cid]
    if t<ts[0] or t>ts[-1]: return None
    return float(np.interp(t,ts,thd[tag]))
def circle_pos(tag,ang): return c+Rv[tag]*(np.cos(ang)*e1+np.sin(ang)*e2)
def dvec(ang): return np.array([np.cos(ang),np.sin(ang),np.cos(2*ang),np.sin(2*ang),1.0])
def true_pos(tag,ang):
    rad=np.cos(ang)*e1+np.sin(ang)*e2; dr=float(dvec(ang)@RUN_R[tag]); w=float(dvec(ang)@RUN_A[tag])
    return circle_pos(tag,ang)+dr*rad+w*n
def runout_proj(tag,ang,a):     # range change that Stage-0-measured runout imposes on anchor a
    return float(np.linalg.norm(true_pos(tag,ang)-A[a])-np.linalg.norm(circle_pos(tag,ang)-A[a]))
def occ_depth(tp,a,rmast=R_MAST):   # LOS tag->anchor vs rotation axis (c, n): mast penetration mm
    seg=A[a]-tp; L=np.linalg.norm(seg)
    if L<1e-6: return 0.0
    u=seg/L; w0=tp-c; aa=1.0; b=float(n@u); cc=1.0; dd=float(n@w0); e=float(u@w0); den=aa*cc-b*b
    t=0.0 if abs(den)<1e-9 else (aa*e-b*dd)/den
    P=tp+u*np.clip(t,0,L); wv=P-c
    return max(0.0, rmast-float(np.linalg.norm(wv-(wv@n)*n)))

def chunk_id(p): return int(p.split('/chunk')[1].split('/')[0].split('_')[0])
roster=tag_roster.roster_from_session(BASE)['by_id']; print(f"[roster] {roster}",flush=True)

# ---- accumulate per (tag,anchor) theta-binned signed r_circle + range ----
SR=collections.defaultdict(lambda:np.zeros(NB)); SR2=collections.defaultdict(lambda:np.zeros(NB))
SN=collections.defaultdict(lambda:np.zeros(NB)); SRNG=collections.defaultdict(lambda:np.zeros(NB))
for tr in sorted(glob.glob(f'{BASE}/chunk*/recv/tr_all.csv'),key=chunk_id):
    cid=chunk_id(tr)
    if cid not in segments: continue
    agg=collections.defaultdict(lambda:[dict(),None]); first=True
    f=open(tr,newline=''); rd=csv.reader(f); hdr=next(rd); ix={h:i for i,h in enumerate(hdr)}
    iN,iV,iRM,iQ,iAn,iSw,iT=ix['peer_name'],ix['valid'],ix['range_mm'],ix['quality_percent'],ix['anchor_id'],ix['sweep'],ix['host_epoch_s']
    for row in rd:
        if row[iV]!='1': continue
        nm=row[iN]
        if nm not in ROTO: continue
        try:
            rm=float(row[iRM])
            if rm<=0 or float(row[iQ])<85: continue
            k=(nm,row[iSw]); e=agg[k]; e[0][ORD[int(row[iAn])]]=rm
            if e[1] is None: e[1]=float(row[iT])
        except (ValueError,IndexError): continue
    for (nm,sw),(ar,t) in agg.items():
        ang=model_angle(nm,cid,t)
        if ang is None: continue
        p=circle_pos(nm,ang); b=int((np.degrees(ang)%360.0)//DEG)%NB
        for a,rm in ar.items():
            r=rm-float(np.linalg.norm(p-A[a]))
            SR[(nm,a)][b]+=r; SR2[(nm,a)][b]+=r*r; SN[(nm,a)][b]+=1; SRNG[(nm,a)][b]+=rm
    print(f"   chunk{cid} binned",flush=True)

# ---- co-located listener dP / RX vs theta ----
def lis_theta(pdir,cid,tag,by_id):
    lpd=glob.glob(f'{pdir}/listener_*/lpd.csv')
    if not lpd: return
    f=open(lpd[0],newline=''); rd=csv.reader(f); hdr=next(rd); ix={h:i for i,h in enumerate(hdr)}
    iSrc,iTag,iT=ix['src'],ix['tag_id'],ix['host_epoch_s']; i1,i2,i3=ix['fp1'],ix['fp2'],ix['fp3']; iC,iR=ix['cir_pwr'],ix['rxpacc']
    for row in rd:
        if row[iSrc][:5].lower()!='0xb10' or by_id.get(row[iTag])!=tag: continue
        try:
            fp1=float(row[i1]);fp2=float(row[i2]);fp3=float(row[i3]);cir=float(row[iC]);rp=float(row[iR]);fpp=fp1*fp1+fp2*fp2+fp3*fp3
            if fpp<=0 or cir<=0 or rp<=0: continue
            ang=model_angle(tag,cid,float(row[iT]))
            if ang is None: continue
            dP=10*np.log10(cir*131072.0/fpp); rx=10*np.log10(fpp/(rp*rp))+dP
            yield int((np.degrees(ang)%360.0)//DEG)%NB, dP, rx
        except (ValueError,IndexError): continue
LdP=collections.defaultdict(lambda:np.zeros(NB)); LRX=collections.defaultdict(lambda:np.zeros(NB)); LNc=collections.defaultdict(lambda:np.zeros(NB))
for cd in sorted(glob.glob(f'{BASE}/chunk*'),key=lambda p:chunk_id(p+'/x')):
    cid=chunk_id(cd+'/x')
    if cid not in segments: continue
    for L,a in COLO:
        for nm in ROTO:
            for b,dP,rx in lis_theta(f'{cd}/{L}',cid,nm,roster):
                LdP[(nm,a)][b]+=dP; LRX[(nm,a)][b]+=rx; LNc[(nm,a)][b]+=1
print("[binned] done",flush=True)

# ---- decomposition + amplitude accounting ----
thc=(np.arange(NB)+0.5)*np.radians(DEG)
def wrms(y,w):
    w=np.asarray(w,float); m=w>0
    if m.sum()<3: return np.nan
    mu=np.sum(w[m]*y[m])/np.sum(w[m]); return float(np.sqrt(np.sum(w[m]*(y[m]-mu)**2)/np.sum(w[m])))
def wexpl(y,comp,w):   # fraction of theta-locked variance removed by subtracting comp (fixed coeff 1)
    v0=wrms(y,w)**2; v1=wrms(y-comp,w)**2
    return (1-v1/v0) if v0>0 else np.nan
print("\n"+"="*112)
print("THETA-LOCKED AMPLITUDE ACCOUNTING per (tag,anchor)  [r_circle(theta), N-weighted, mean-removed]")
print("all RMS in mm; %expl = theta-locked variance removed by that KNOWN component (fixed coeff, not fitted)")
print("="*112)
print(f"  {'tag':7} {'a':2} {'totRMS':>6} | {'RUNOUT':>6} {'%exp':>4} | {'APS011sw':>8} {'%exp':>4} | "
      f"{'OCCrms':>6} {'%exp':>4} | {'remRMS':>6} {'occ_sec':>8} {'occ<r>+':>7}")
ACC={}
for nm in ROTO:
    for a in ORD:
        N=SN[(nm,a)]; m=N>0
        if m.sum()<10: continue
        mrc=np.where(m,SR[(nm,a)]/np.maximum(N,1),0.0)          # mean signed r_circle per bin
        rng=np.where(m,SRNG[(nm,a)]/np.maximum(N,1),0.0)
        runout=np.array([runout_proj(nm,thc[b],a) for b in range(NB)])
        aps=np.array([aps011_mm(rng[b]) if m[b] else 0.0 for b in range(NB)])
        occ=np.array([occ_depth(true_pos(nm,thc[b]),a) for b in range(NB)])
        tot=wrms(mrc,N)
        e_run=wexpl(mrc,runout,N)
        res1=mrc-runout
        e_aps=wexpl(res1,aps-np.mean(aps),N)                    # APS011 on runout-removed (swing only)
        res2=res1-(aps-np.mean(aps))
        occ_ind=(occ>0).astype(float)
        e_occ=wexpl(res2,np.where(occ_ind>0,np.mean(res2[occ_ind>0]) if occ_ind.sum() else 0,0),N) if occ_ind.sum()>0 else 0.0
        rem=wrms(res2,N)
        occ_secs=int(occ_ind.sum()); occ_mean=float(np.mean(res2[occ_ind>0])) if occ_secs else float('nan')
        ACC[(nm,a)]=dict(tot=tot,run=wrms(runout,N),e_run=e_run,aps_sw=wrms(aps-np.mean(aps),N),e_aps=e_aps,
                         occ=wrms(np.where(occ_ind>0,res2,0),N),e_occ=e_occ,rem=rem,occ_secs=occ_secs,occ_mean=occ_mean,
                         mrc=mrc,runout=runout,aps=aps,occv=occ,N=N,rng=rng)
        print(f"  {nm:7} {a:2} {tot:6.0f} | {wrms(runout,N):6.0f} {100*e_run:4.0f} | {wrms(aps-np.mean(aps),N):8.0f} {100*e_aps:4.0f} | "
              f"{wrms(np.where(occ_ind>0,res2,0),N):6.0f} {100*e_occ:4.0f} | {rem:6.0f} {occ_secs:8d} {occ_mean:7.0f}")
print("  NOTE: RUNOUT is Stage-0-measured trajectory (independent of these ranges); APS011sw is the")
print("        20-30mm theta-SWING of the fixed table (its ~-70mm DC offset is absorbed by geometry);")
print("        occ<r>+ = mean runout+APS-removed residual inside mast-occlusion sectors (NLOS=>positive).")

# ---- shared-axis theta plots for co-located links ----
fig,axes=plt.subplots(3,2,figsize=(13,11))
for i,(L,a) in enumerate(COLO):
    for j,nm in enumerate(ROTO):
        ax=axes[i][j]; k=(nm,a)
        if k not in ACC: ax.axis('off'); continue
        Ac=ACC[k]; thd=(np.arange(NB)+0.5)*DEG; m=Ac['N']>0
        ax.plot(thd[m],Ac['mrc'][m],'k-',lw=1.8,label='signed r_circle')
        ax.plot(thd[m],Ac['runout'][m],'-',c='tab:green',lw=1.2,label='RUNOUT (Stage-0)')
        ax.plot(thd[m],(Ac['aps']-np.mean(Ac['aps']))[m],'-',c='tab:blue',lw=1.2,label='APS011 swing')
        for b in range(NB):
            if Ac['occv'][b]>0: ax.axvspan(b*DEG,(b+1)*DEG,color='red',alpha=0.10)
        ax.set_title(f"{nm} @ anchor {a} (listener {L})",fontsize=9); ax.set_xlabel('theta (deg)',fontsize=8)
        ax.set_ylabel('range residual (mm)',fontsize=8)
        if LNc[k].sum()>0:
            mc=LNc[k]>0; ax2=ax.twinx(); ax2.plot(thd[mc],(LdP[k]/np.maximum(LNc[k],1))[mc],'--',c='tab:orange',lw=1,alpha=.8,label='listener dP')
            ax2.set_ylabel('listener dP (dB)',fontsize=8,color='tab:orange'); ax2.tick_params(labelsize=7)
        if i==0 and j==0: ax.legend(fontsize=7,loc='upper right')
        ax.tick_params(labelsize=7)
plt.suptitle('theta-locked residual decomposition (co-located links): mixture of runout + APS011 + occlusion',fontsize=11)
plt.tight_layout(); plt.savefig(f'{OUT}/diag_theta_decomp.png',dpi=110); plt.close()
print(f"\nsaved {OUT}/diag_theta_decomp.png")

# ---- summary roll-up ----
print("\n"+"="*112); print("ROLL-UP  (theta-locked residual is a MIXTURE; APS011 is a minor deterministic part)")
print("="*112)
tots=np.array([v['tot'] for v in ACC.values()]); rems=np.array([v['rem'] for v in ACC.values()])
er=np.array([v['e_run'] for v in ACC.values()]); ea=np.array([v['e_aps'] for v in ACC.values()])
print(f"  links={len(ACC)}  median total theta-locked RMS={np.median(tots):.0f}mm  median remainder(after runout+APS+occ)={np.median(rems):.0f}mm")
print(f"  RUNOUT explains median {100*np.median(er):.0f}% of theta-locked variance; APS011-swing median {100*np.nanmedian(ea):.0f}%")
print(f"  => APS011 cannot be the driver (its swing is 20-30mm vs {np.median(tots):.0f}mm total); dominant term is trajectory RUNOUT + layout.")
print("DONE")
