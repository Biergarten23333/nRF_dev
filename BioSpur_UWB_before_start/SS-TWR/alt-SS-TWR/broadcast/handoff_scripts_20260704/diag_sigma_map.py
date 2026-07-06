#!/usr/bin/env python3
"""DIAGNOSTICS -> SIGMA MAPPING  (first pass of the main-line listener PROXY GATE).

Q: does a passive listener's channel quality (overhearing a tag's poll) PREDICT the anchor-side
   ranging error r to that same tag?

Two ranging residuals, same pass (Spearman rho reported for BOTH):
  (a) r_circle  = measured - Stage-0 circle-model prediction.  External reference, but carries
      ~40-60 mm common-mode trajectory-position noise (shared across anchors) -> attenuates rho.
  (b) r_postfit = per-sweep 8-anchor LS multilateration post-fit residual (measured - |x_hat-anch|).
      Absorbs ~3/8 of the true per-link error (fit DOF), but ZERO tag-position confound.
  If rho(b) >> rho(a): channel IS informative, (a) merely diluted. If both weak: verdict
  "insufficient NLOS variation in a static room" is clean.

Diagnostics per overheard poll (listener lpd.csv), regressed by SPEARMAN (=> the DW power-formula
  convention constant is irrelevant; ranks only):
  dP   = 10log10(cir_pwr*2^17 / (fp1^2+fp2^2+fp3^2))     Decawave RX-FP (NLOS metric)
  fp   = 10log10((fp1^2+fp2^2+fp3^2) / rxpacc^2)          FP power (uncal, -A dBm)
  rxpacc, std_noise                                       raw registers
signed-r kept alongside |r| (NLOS bias is positive-definite; the sign relation may be stronger).

PRIMARY = co-located proxy pairs ONLY: {BS2DCE,BSDC91} x {LB@B, LE@E, LF@F} vs ranging error on the
  SAME anchor. Wand-side listeners (LCCF4/L9336/L955A: channel is tag->room-center, not tag->anchor)
  are EXPLORATORY secondary.  wand static soak = held-out validation (confound-free).
Tag ids via scripts/tag_roster.py (single source of truth). Listener<->anchor via roto_ridge.py.

Run: cd .../broadcast; ulimit -v 8000000; python3 handoff_scripts_20260704/diag_sigma_map.py
"""
import csv, glob, os, sys, json, collections, re, numpy as np
from scipy.stats import spearmanr, skew
import matplotlib; matplotlib.use('Agg'); import matplotlib.pyplot as plt
sys.path.insert(0,'scripts'); import tag_roster

BASE='logs/roto_sar_overnight_20260705_012548'
SOAK='logs/overnight_soak_v2_20260704_032348'
CACHE='handoff_scripts_20260704/coherent_stage0_cache.npz'
OUT='handoff_scripts_20260704/figs_20260704'; os.makedirs(OUT,exist_ok=True)
lay=json.load(open('logs/autopos_v3box_noref_20260704_030908/solve_v3_box/anchor_layout_v3_box.json'))
A={a['label']:np.array([a['x_mm'],a['y_mm'],a['z_mm']],float) for a in lay['anchors']}
ORD=['A','B','C','D','E','F','G','H']
ROTO=['BS2DCE','BSDC91']; STATIC=['BS9336','BS955A','BSCCF4']
COLO=[('LB','B'),('LE','E'),('LF','F')]                    # co-located listener -> anchor (roto_ridge.py)
LIS_ANCHOR=dict(COLO)
LISTENERS=['LB','LE','LF','LCCF4','L9336','L955A']
TOL=0.15
DIAGS=['dP','fp','pac','noi']
DIAGLBL={'dP':'Delta-P(RX-FP)dB','fp':'FPpower dB(uncal)','pac':'rxpacc','noi':'std_noise'}

def rows_idx(path):
    f=open(path,newline=''); rd=csv.reader(f)
    try: hdr=next(rd)
    except StopIteration: return
    ix={h:i for i,h in enumerate(hdr)}
    for row in rd: yield row,ix

def listener_frames(pdir,by_id,want):
    """yield (tagname,t,dP,fp,rxpacc,std_noise) for overheard tag polls."""
    lpd=glob.glob(f'{pdir}/listener_*/lpd.csv')
    if not lpd: return
    first=True
    for row,ix in rows_idx(lpd[0]):
        if first:
            iSrc,iTag,iT=ix['src'],ix['tag_id'],ix['host_epoch_s']
            i1,i2,i3=ix['fp1'],ix['fp2'],ix['fp3']; iC,iR,iN=ix['cir_pwr'],ix['rxpacc'],ix['std_noise']; first=False
        try:
            if row[iSrc][:5].lower()!='0xb10': continue      # tag poll only
            nm=by_id.get(row[iTag])
            if nm not in want: continue
            fp1=float(row[i1]); fp2=float(row[i2]); fp3=float(row[i3])
            cir=float(row[iC]); rp=float(row[iR]); sn=float(row[iN]); fpp=fp1*fp1+fp2*fp2+fp3*fp3
            if fpp<=0 or cir<=0 or rp<=0: continue
            yield (nm,float(row[iT]),10*np.log10(cir*131072.0/fpp),10*np.log10(fpp/(rp*rp)),rp,sn)
        except (ValueError,KeyError,IndexError): continue

FIT_FAIL_MM=2000.0     # postfit RMS above this = non-convergent fit, not a real ranging error
def multilat(P,d):
    """linear closed-form seed + damped (backtracking) Gauss-Newton so the fit CANNOT diverge.
    Returns (x_hat, postfit_resid = measured - predicted).  rpf=None if the fit fails to converge
    to a plausible cost (that is a numerical failure, NOT a large-but-real ranging error, which LS
    bounds by spreading it across links)."""
    a0=P[0]; M=2.0*(P[1:]-a0)
    b=(d[0]**2-d[1:]**2)+(np.einsum('ij,ij->i',P[1:],P[1:])-a0@a0)
    try: x=np.linalg.lstsq(M,b,rcond=None)[0]
    except np.linalg.LinAlgError: return None,None
    def cost(xx):
        rng=np.sqrt(np.einsum('ij,ij->i',xx-P,xx-P)); return rng,float(np.sum((rng-d)**2))
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
    if not np.all(np.isfinite(rng)) or np.sqrt(c/len(d))>FIT_FAIL_MM: return None,None
    return x, d-rng

# ---------------- Stage-0 circle model ----------------
d=np.load(CACHE,allow_pickle=True)
c=d['c']; e1=d['e1']; e2=d['e2']; Rv={t:float(d['R'][i]) for i,t in enumerate(d['tags'])}
tg=d['tg']; TH={'BS2DCE':d['th1'],'BSDC91':d['th2']}
seg_cid=[int(x) for x in d['seg_cid']]; seg_off=d['seg_off']
segments={}
for j,cid in enumerate(seg_cid):
    sl=slice(int(seg_off[j]),int(seg_off[j+1])); segments[cid]=(tg[sl],{t:TH[t][sl] for t in ROTO})
def model_angle(tag,cid,t):
    if cid not in segments: return None
    ts,thd=segments[cid]
    if t<ts[0] or t>ts[-1]: return None
    return float(np.interp(t,ts,thd[tag]))
def chunk_id(p): return int(p.split('/chunk')[1].split('/')[0].split('_')[0])

# ---------------- roto: per-(tag,sweep) residuals (both defs) ----------------
roster_roto=tag_roster.roster_from_session(BASE)['by_id']
print(f"[roster] roto by_id = {roster_roto}",flush=True)
sweep_t={t:[] for t in ROTO}; sweep_dat={t:[] for t in ROTO}   # dat=(cid,theta,{anchor:(rc,rpf)})
resid_by=collections.defaultdict(lambda:[[],[]])               # (tag,anchor)->[theta, r_circle]  (harmonics)
n_le5=0; n_fitfail=0
for tr in sorted(glob.glob(f'{BASE}/chunk*/recv/tr_all.csv'),key=chunk_id):
    cid=chunk_id(tr)
    if cid not in segments: continue
    agg=collections.defaultdict(lambda:[dict(),None]); first=True
    for row,ix in rows_idx(tr):
        if first:
            iN,iV,iRM,iQ,iAn,iSw,iT=ix['peer_name'],ix['valid'],ix['range_mm'],ix['quality_percent'],ix['anchor_id'],ix['sweep'],ix['host_epoch_s']; first=False
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
        p=c+Rv[nm]*(np.cos(ang)*e1+np.sin(ang)*e2); th=np.degrees(ang)%360.0
        labs=list(ar.keys())
        # (a) circle residual
        rc={a:ar[a]-float(np.sqrt(((p-A[a])**2).sum())) for a in labs}
        for a in labs: rb=resid_by[(nm,a)]; rb[0].append(th); rb[1].append(rc[a])
        # (b) postfit residual (>=5 anchors)
        rpf={}
        if len(labs)>=5:
            Pm=np.array([A[a] for a in labs]); dm=np.array([ar[a] for a in labs])
            xh,res=multilat(Pm,dm)
            if res is not None: rpf={a:float(res[i]) for i,a in enumerate(labs)}
            else: n_fitfail+=1
        else: n_le5+=1
        rr={a:(rc[a],rpf.get(a,np.nan)) for a in labs}
        sweep_t[nm].append(t); sweep_dat[nm].append((cid,th,rr))
for nm in ROTO:
    idx=np.argsort(sweep_t[nm]); sweep_t[nm]=np.array(sweep_t[nm])[idx]; sweep_dat[nm]=[sweep_dat[nm][i] for i in idx]
print(f"[roto] sweeps w/ residual: "+", ".join(f"{nm}={len(sweep_t[nm])}" for nm in ROTO)+f"  (postfit skipped: <5anch={n_le5}, nonconvergent={n_fitfail})",flush=True)
print("[roto] per-(tag,anchor) residual RMS (mm)  circle | postfit:")
for nm in ROTO:
    line=[]
    for a in ORD:
        rr=np.array(resid_by[(nm,a)][1]); rp=np.array([v[1] for dat in sweep_dat[nm] if dat[2].get(a) for v in [dat[2][a]]])
        rp=rp[np.isfinite(rp)]
        if len(rr)>50: line.append(f"{a}:{np.sqrt(np.mean(rr**2)):3.0f}/{(np.sqrt(np.mean(rp**2)) if len(rp)>50 else float('nan')):3.0f}")
    print(f"   {nm}: "+"  ".join(line))

# ---------------- join listener diagnostics -> anchor residuals ----------------
triples=collections.defaultdict(lambda:collections.defaultdict(list))
nframe=collections.Counter()
for cd in sorted(glob.glob(f'{BASE}/chunk*'),key=lambda p:chunk_id(p+'/x')):
    cid=chunk_id(cd+'/x')
    if cid not in segments: continue
    tmask={nm:np.array([i for i,dat in enumerate(sweep_dat[nm]) if dat[0]==cid]) for nm in ROTO}
    tidx={nm:(sweep_t[nm][tmask[nm]] if len(tmask[nm]) else np.array([])) for nm in ROTO}
    for L in LISTENERS:
        for nm,t,dP,fp,rp,sn in listener_frames(f'{cd}/{L}',roster_roto,ROTO):
            tt=tidx[nm]
            if not len(tt): continue
            j=int(np.searchsorted(tt,t)); j=min(max(j,1),len(tt)-1)
            jj=j if abs(tt[j]-t)<abs(tt[j-1]-t) else j-1
            if abs(tt[jj]-t)>TOL: continue
            nframe[(nm,L)]+=1
            _,th,rr=sweep_dat[nm][tmask[nm][jj]]
            for a,(rcv,rpv) in rr.items():
                dd=triples[(nm,L,a)]
                dd['dP'].append(dP); dd['fp'].append(fp); dd['pac'].append(rp); dd['noi'].append(sn)
                dd['rc'].append(rcv); dd['rpf'].append(rpv); dd['th'].append(th)
    print(f"   chunk{cid} joined; cum frames={sum(nframe.values())}",flush=True)
print(f"[roto] aligned listener frames (rotating tags): {sum(nframe.values())}")

def sp(x,y):
    x=np.asarray(x,float); y=np.asarray(y,float); m=np.isfinite(x)&np.isfinite(y)
    if m.sum()<30 or np.std(x[m])==0 or np.std(y[m])==0: return (np.nan,np.nan,int(m.sum()))
    rho,pv=spearmanr(x[m],y[m]); return (rho,pv,int(m.sum()))
def rho_of(dd,xkey,ykey,absy=False):
    y=np.abs(np.asarray(dd[ykey],float)) if absy else np.asarray(dd[ykey],float)
    return sp(dd[xkey],y)

# ---------------- PRIMARY: co-located proxy links ----------------
print("\n"+"="*100)
print("PRIMARY PROXY GATE -- co-located links {BS2DCE,BSDC91} x {LB@B,LE@E,LF@F}, dP headline")
print("rho>0 means worse channel (higher dP / more attenuated FP) -> larger ranging error")
print("="*100)
print(f"  {'tag':7} {'lis@anc':8} {'N':>5}  {'|rc|,dP':>8} {'rc,dP':>7} | {'|rpf|,dP':>8} {'rpf,dP':>7} | "
      f"{'|rpf|,fp':>8} {'|rpf|,pac':>9} {'|rpf|,noi':>9}")
prim=[]
for nm in ROTO:
    for L,a in COLO:
        dd=triples.get((nm,L,a))
        if not dd or len(dd['rc'])<50:
            print(f"  {nm:7} {L+'@'+a:8} {'--':>5}  (insufficient)"); continue
        row=dict(nm=nm,L=L,a=a,N=len(dd['rc']),
                 rc_abs=rho_of(dd,'dP','rc',True)[0], rc_sgn=rho_of(dd,'dP','rc')[0],
                 rpf_abs=rho_of(dd,'dP','rpf',True)[0], rpf_sgn=rho_of(dd,'dP','rpf')[0],
                 rpf_fp=rho_of(dd,'fp','rpf',True)[0], rpf_pac=rho_of(dd,'pac','rpf',True)[0], rpf_noi=rho_of(dd,'noi','rpf',True)[0])
        prim.append(row)
        print(f"  {nm:7} {L+'@'+a:8} {row['N']:5d}  {row['rc_abs']:8.3f} {row['rc_sgn']:7.3f} | "
              f"{row['rpf_abs']:8.3f} {row['rpf_sgn']:7.3f} | {row['rpf_fp']:8.3f} {row['rpf_pac']:9.3f} {row['rpf_noi']:9.3f}")
# gate counts per definition (on co-located links)
def cnt(key): return sum(1 for r in prim if abs(r[key])>0.30)
print(f"\n  co-located links passing |rho|>0.30:  |rc|,dP={cnt('rc_abs')}  rc,dP={cnt('rc_sgn')}  "
      f"|rpf|,dP={cnt('rpf_abs')}  rpf,dP={cnt('rpf_sgn')}   (of {len(prim)})")
gate_defs={'|rc|,dP':cnt('rc_abs'),'rc,dP':cnt('rc_sgn'),'|rpf|,dP':cnt('rpf_abs'),'rpf,dP':cnt('rpf_sgn')}
best_def=max(gate_defs,key=gate_defs.get); n_gate=gate_defs[best_def]; gate=n_gate>=3

# theta-CONTROL: is the roto co-located correlation genuine per-frame channel info, or a shared-theta
# curve-alignment artifact? Remove per-theta-bin median from BOTH r and dP (24 x 15deg bins), then
# Spearman the residuals. If it collapses to ~0 -> shared-theta (consistent with static null); if it
# survives -> real per-frame proxy value on that link.
def theta_detrend_rho(dd,ykey):
    th=np.asarray(dd['th'],float); x=np.asarray(dd['dP'],float); y=np.asarray(dd[ykey],float)
    m=np.isfinite(x)&np.isfinite(y)&np.isfinite(th); th,x,y=th[m],x[m],y[m]
    if len(x)<200: return (np.nan,np.nan,len(x))
    b=np.clip((th//15).astype(int),0,23); xr=x.copy(); yr=y.copy()
    for k in range(24):
        s=b==k
        if s.sum()>=10: xr[s]-=np.median(x[s]); yr[s]-=np.median(y[s])
    return sp(xr,yr)
print("\n  theta-CONTROL (per-frame channel info after removing shared rotation-angle covariate):")
print(f"    {'tag':7} {'lis@anc':8} {'raw rc,dP':>10} {'th-ctl rc,dP':>13} {'raw rpf,dP':>11} {'th-ctl rpf,dP':>14}")
for nm in ROTO:
    for L,a in COLO:
        dd=triples.get((nm,L,a))
        if not dd or len(dd['rc'])<200: continue
        rc_raw=rho_of(dd,'dP','rc')[0]; rc_ct=theta_detrend_rho(dd,'rc')[0]
        rp_raw=rho_of(dd,'dP','rpf')[0]; rp_ct=theta_detrend_rho(dd,'rpf')[0]
        print(f"    {nm:7} {L+'@'+a:8} {rc_raw:10.3f} {rc_ct:13.3f} {rp_raw:11.3f} {rp_ct:14.3f}")

# ---------------- pooled (co-located) + EXPLORATORY (remote) ----------------
def pool(sel,xkey,ykey,absy=True):
    """sel in {'colo','remote'}: pool residual vs diag across those links."""
    xs=[];ys=[]
    for (nm,L,a),dd in triples.items():
        co=(LIS_ANCHOR.get(L)==a)
        if sel=='colo' and not co: continue
        if sel=='remote' and co: continue
        xs+=dd[xkey]; y=np.abs(np.asarray(dd[ykey],float)) if absy else np.asarray(dd[ykey],float); ys+=list(y)
    return sp(xs,ys)
print("\nPOOLED co-located  rho(residual, diag):")
for g in DIAGS:
    print(f"   {DIAGLBL[g]:18}: |rc| {pool('colo',g,'rc')[0]:+.3f}  rc {pool('colo',g,'rc',False)[0]:+.3f} | "
          f"|rpf| {pool('colo',g,'rpf')[0]:+.3f}  rpf {pool('colo',g,'rpf',False)[0]:+.3f}")
print("EXPLORATORY remote wand-side listeners (tag->room-center channel; NOT the proxy question):")
for g in DIAGS:
    print(f"   {DIAGLBL[g]:18}: |rc| {pool('remote',g,'rc')[0]:+.3f} | |rpf| {pool('remote',g,'rpf')[0]:+.3f}")

# ---------------- binned conditional sigma(|r| | dP)  (co-located, postfit) ----------------
def binned(xs,ys,edges):
    xs=np.asarray(xs,float); ys=np.abs(np.asarray(ys,float)); out=[]
    for lo,hi in zip(edges[:-1],edges[1:]):
        m=(xs>=lo)&(xs<hi)&np.isfinite(ys)
        if m.sum()>=30: out.append((0.5*(lo+hi),int(m.sum()),float(np.std(ys[m])),float(np.median(ys[m]))))
    return out
cx=[];cy_rc=[];cy_rpf=[];cfp=[]
for (nm,L,a),dd in triples.items():
    if LIS_ANCHOR.get(L)==a: cx+=dd['dP']; cy_rc+=dd['rc']; cy_rpf+=dd['rpf']; cfp+=dd['fp']
cx=np.array(cx); cfp=np.array(cfp); bs=[]
if len(cx):
    edges=np.unique(np.round(np.quantile(cx,[0,.1,.25,.5,.75,.9,1.0]),2))
    print(f"\nBINNED sigma(|r| | Delta-P)  co-located  dP[{cx.min():.1f},{cx.max():.1f}]dB:")
    print(f"   {'dP center':>9} {'N':>7} {'sig|rc| mm':>11} {'sig|rpf| mm':>12} {'med|rpf| mm':>12}")
    b_rc=binned(cx,cy_rc,edges); b_rpf=binned(cx,cy_rpf,edges)
    for (cen,n,src,_),(_,_,srp,mdp) in zip(b_rc,b_rpf): print(f"   {cen:9.2f} {n:7d} {src:11.0f} {srp:12.0f} {mdp:12.0f}")
    bs=b_rpf
    if len(bs)>=2:
        s=np.array([x[2] for x in bs]); print(f"   sig|rpf| spread: {s.min():.0f}->{s.max():.0f} mm (ratio {s.max()/max(s.min(),1e-9):.2f})")

# ---------------- APS011 (uncorrected range-power bias) vs NLOS discrimination ----------------
# Firmware applies NO APS011 correction (dwt_getrangebias uncalled; range=raw ToF). So signed-r may
# carry the DW1000 deterministic RX-power-dependent bias. Fingerprint: signed-r vs RX power should be
# SMOOTH, MONOTONIC, cm-scale if APS011; NLOS instead adds an ASYMMETRIC positive-heavy tail.
print("\n"+"="*100)
print("APS011-vs-NLOS DISCRIMINATION  (firmware applies NO range-bias correction -> raw ToF)")
print("="*100)
if len(cx):
    RX=cfp+cx                                  # RX power dB (uncal) = FP + Delta-P
    rc=np.array(cy_rc,float); rpf=np.array(cy_rpf,float)
    def bin_signed(x,y,nb=10):
        m=np.isfinite(x)&np.isfinite(y); x,y=x[m],y[m]; q=np.quantile(x,np.linspace(0,1,nb+1)); out=[]
        for lo,hi in zip(q[:-1],q[1:]):
            s=(x>=lo)&(x<hi)
            if s.sum()>=100: out.append((0.5*(lo+hi),int(s.sum()),float(np.mean(y[s])),float(np.median(y[s]))))
        return out
    print("  co-located pooled: mean SIGNED r_postfit vs RX power (monotone+cm-scale => APS011-like):")
    print(f"    {'RX dB(uncal)':>12} {'N':>7} {'mean r mm':>10} {'median r mm':>12}")
    prof=bin_signed(RX,rpf,10)
    for cen,n,mn,md in prof: print(f"    {cen:12.1f} {n:7d} {mn:10.1f} {md:12.1f}")
    if len(prof)>=3:
        amp=max(p[2] for p in prof)-min(p[2] for p in prof)
        mono=spearmanr([p[0] for p in prof],[p[2] for p in prof])[0]
        print(f"    -> signed-r swing across RX range = {amp:.0f} mm (cm-scale?), monotonicity rho={mono:+.2f}")
    # NLOS tail: skew of r_postfit overall, and within a fixed RX bin (deterministic trend removed)
    mid=(RX>np.quantile(RX,0.4))&(RX<np.quantile(RX,0.6))&np.isfinite(rpf)
    sk_all=float(skew(rpf[np.isfinite(rpf)])); sk_mid=float(skew(rpf[mid])) if mid.sum()>100 else float('nan')
    print(f"  skew(r_postfit) all={sk_all:+.2f}  within mid-RX slice={sk_mid:+.2f}  "
          f"(NLOS => strong + skew; deterministic bias => ~symmetric)")

# ---------------- residual-vs-theta HARMONICS per anchor (NEW; range-residual domain) ----------------
print("\n"+"="*100)
print("RESIDUAL-vs-THETA HARMONICS per anchor  r_circle(th)=a0+a1c+b1s+a2c2+b2s2  (geometry-locked bias)")
print("NOT the CIR echo-ridge MPC run (roto_ridge.py, cut) -- that was CIR-magnitude domain; nothing to merge.")
print("="*100)
print(f"  {'tag':7} {'anc':3} {'N':>6} {'1st amp':>7} {'2nd amp':>7} {'var expl%':>9} {'rawRMS':>7} {'residRMS':>8}")
harm={}
for nm in ROTO:
    for a in ORD:
        th_l,r_l=resid_by[(nm,a)]
        if len(r_l)<200: continue
        th=np.radians(np.array(th_l)); r=np.array(r_l)
        D=np.column_stack([np.ones_like(th),np.cos(th),np.sin(th),np.cos(2*th),np.sin(2*th)])
        co=np.linalg.lstsq(D,r,rcond=None)[0]; fit=D@co
        a1=np.hypot(co[1],co[2]); a2=np.hypot(co[3],co[4]); ev=1-np.var(r-fit)/max(np.var(r),1e-9)
        harm[(nm,a)]=(a1,a2,ev,np.sqrt(np.mean(r**2)),np.std(r-fit))
        print(f"  {nm:7} {a:3} {len(r):6d} {a1:7.0f} {a2:7.0f} {100*ev:9.0f} {np.sqrt(np.mean(r**2)):7.0f} {np.std(r-fit):8.0f}")

# ---------------- SOAK held-out validation (static; confound-free) ----------------
print("\n"+"="*100)
print("HELD-OUT VALIDATION -- wand static soak.  r_detrend = range - per-anchor median (pure ranging noise)")
print("="*100)
roster_soak=tag_roster.roster_from_session(SOAK)['by_id']; print(f"[roster] soak by_id = {roster_soak}",flush=True)
def soak_cid(p):
    m=re.search(r'/chunk(\d+)_',p); return int(m.group(1)) if m else 0
soak_chunks=sorted(glob.glob(f'{SOAK}/chunk*'),key=soak_cid)
S_t=collections.defaultdict(list); S_dat=collections.defaultdict(list)
for cd in soak_chunks:
    cid=soak_cid(cd+'/'); tr=glob.glob(f'{cd}/recv*/tr_all.csv')
    if not tr: continue
    agg=collections.defaultdict(lambda:[dict(),None]); first=True
    for row,ix in rows_idx(tr[0]):
        if first:
            iN,iV,iRM,iQ,iAn,iSw,iT=ix['peer_name'],ix['valid'],ix['range_mm'],ix['quality_percent'],ix['anchor_id'],ix['sweep'],ix['host_epoch_s']; first=False
        if row[iV]!='1': continue
        nm=row[iN]
        if nm not in STATIC: continue
        try:
            rm=float(row[iRM])
            if rm<=0 or float(row[iQ])<85: continue
            k=(nm,row[iSw]); e=agg[k]; e[0][ORD[int(row[iAn])]]=rm
            if e[1] is None: e[1]=float(row[iT])
        except (ValueError,IndexError): continue
    for (nm,sw),(ar,t) in agg.items():
        S_t[nm].append(t); S_dat[nm].append((cid,dict(ar)))
med={}
for nm in STATIC:
    if not S_t[nm]: continue
    ar=collections.defaultdict(list)
    for _,rng in S_dat[nm]:
        for a,v in rng.items(): ar[a].append(v)
    med[nm]={a:float(np.median(v)) for a,v in ar.items()}
soak_swt={}; soak_swd={}
for nm in STATIC:
    if not S_t[nm]: continue
    idx=np.argsort(S_t[nm]); soak_swt[nm]=np.array(S_t[nm])[idx]
    soak_swd[nm]=[(S_dat[nm][i][0],{a:S_dat[nm][i][1][a]-med[nm][a] for a in S_dat[nm][i][1]}) for i in idx]
soak_tri=collections.defaultdict(lambda:collections.defaultdict(list))
for cd in soak_chunks:
    cid=soak_cid(cd+'/')
    tmask={nm:np.array([i for i,dat in enumerate(soak_swd.get(nm,[])) if dat[0]==cid]) for nm in STATIC}
    tidx={nm:(soak_swt[nm][tmask[nm]] if nm in soak_swt and len(tmask[nm]) else np.array([])) for nm in STATIC}
    for L in LISTENERS:
        for nm,t,dP,fp,rp,sn in listener_frames(f'{cd}/{L}',roster_soak,STATIC):
            tt=tidx.get(nm,np.array([]))
            if not len(tt): continue
            j=int(np.searchsorted(tt,t)); j=min(max(j,1),len(tt)-1)
            jj=j if abs(tt[j]-t)<abs(tt[j-1]-t) else j-1
            if abs(tt[jj]-t)>TOL: continue
            _,rr=soak_swd[nm][tmask[nm][jj]]
            for a,rval in rr.items():
                dd=soak_tri[(nm,L,a)]; dd['dP'].append(dP); dd['fp'].append(fp); dd['pac'].append(rp); dd['noi'].append(sn); dd['r'].append(rval)
n_soak_gate=0
if soak_tri:
    print("  static per-(tag,anchor) |r_detrend| RMS (mm, confound-free):")
    for nm in STATIC:
        if nm not in soak_swd: continue
        rr_by=collections.defaultdict(list)
        for _,rng in soak_swd[nm]:
            for a,v in rng.items(): rr_by[a].append(v)
        line=[f"{a}:{np.sqrt(np.mean(np.array(v)**2)):3.0f}" for a,v in sorted(rr_by.items()) if len(v)>50]
        if line: print(f"    {nm}: "+"  ".join(line))
    print("  co-located proxy links  rho(|r|,dP) / rho(r,dP):")
    for (nm,L,a),dd in sorted(soak_tri.items()):
        if LIS_ANCHOR.get(L)!=a or len(dd['r'])<50: continue
        ra=rho_of(dd,'dP','r',True); rs=rho_of(dd,'dP','r')
        if abs(ra[0])>0.3: n_soak_gate+=1
        print(f"    {nm:7} {L}@{a}  N={ra[2]:5d}  rho(|r|,dP)={ra[0]:+.3f} (p={ra[1]:.2g})  rho(r,dP)={rs[0]:+.3f}")
    sx=[];sy=[]
    for (nm,L,a),dd in soak_tri.items():
        if LIS_ANCHOR.get(L)==a: sx+=dd['dP']; sy+=list(np.abs(dd['r']))
    rp=sp(sx,sy); print(f"    POOLED co-located: rho(|r|,dP)={rp[0]:+.3f} (N={rp[2]})   passing |rho|>0.3: {n_soak_gate}")
else:
    print("  (no aligned soak listener/ranging frames)")

# ---------------- GATE + expected weighting gain ----------------
print("\n"+"="*100); print("GATE DECISION + EXPECTED WEIGHTING GAIN"); print("="*100)
print(f"  usable-relation gate (|rho|>0.30 on >=3 co-located links): {'PASS' if gate else 'FAIL'}  "
      f"(best def '{best_def}' -> {n_gate}/{len(prim)})   soak-passing={n_soak_gate}")
if len(cx) and len(bs)>=2:
    cent=np.array([b[0] for b in bs]); sigb=np.array([b[2] for b in bs])
    sig_i=np.interp(cx,cent,sigb); factor=float(np.mean(sig_i**2)*np.mean(1.0/sig_i**2)); gain=1-1/np.sqrt(factor)
    base=float(np.sqrt(np.mean(np.abs(np.array(cy_rpf,float)[np.isfinite(cy_rpf)])**2)))
    print(f"  per-measurement sigma|rpf| spans {sigb.min():.0f}-{sigb.max():.0f} mm across dP bins")
    print(f"  inverse-variance-vs-uniform variance factor = {factor:.3f}  ->  RMS reduction {100*gain:.1f}%")
    print(f"  on co-located |rpf| RMS ~{base:.0f} mm  ->  expected per-measurement gain ~= {base*gain:.1f} mm")
    print(f"  (position gain is same order via GDOP; achievable ONLY insofar as dP carries sigma info -> gate {'PASS' if gate else 'FAIL'})")

# ---------------- figure ----------------
fig,ax=plt.subplots(2,2,figsize=(12,8))
if len(cx):
    yy=np.abs(np.array(cy_rpf,float)); m=np.isfinite(yy)
    ax[0,0].hexbin(cx[m],yy[m],gridsize=40,bins='log',cmap='inferno'); ax[0,0].set_ylim(0,np.quantile(yy[m],0.99))
    ax[0,0].set_xlabel('listener Delta-P (dB)'); ax[0,0].set_ylabel('|r_postfit| (mm)'); ax[0,0].set_title('co-located: |r_postfit| vs Delta-P')
    if len(bs)>=2:
        ax[0,1].plot([b[0] for b in bs],[b[2] for b in bs],'o-'); ax[0,1].set_xlabel('Delta-P bin (dB)'); ax[0,1].set_ylabel('sigma(|r_postfit|) mm'); ax[0,1].set_title('binned conditional sigma')
if prim:
    labs=[f"{r['nm'][-4:]}\n{r['L']}@{r['a']}" for r in prim]; x=np.arange(len(labs))
    ax[1,0].bar(x-0.2,[r['rpf_abs'] for r in prim],0.4,label='|rpf|,dP'); ax[1,0].bar(x+0.2,[r['rpf_sgn'] for r in prim],0.4,label='rpf,dP')
    ax[1,0].axhline(0.3,c='r',ls='--'); ax[1,0].axhline(-0.3,c='r',ls='--'); ax[1,0].set_xticks(x); ax[1,0].set_xticklabels(labs,fontsize=6)
    ax[1,0].set_ylabel('Spearman rho'); ax[1,0].set_title('proxy gate: co-located rho'); ax[1,0].legend(fontsize=7)
if harm:
    labs=[f"{nm[-4:]}/{a}" for (nm,a) in harm]; a1=[harm[k][0] for k in harm]; raw=[harm[k][3] for k in harm]; x=np.arange(len(labs))
    ax[1,1].bar(x-0.2,a1,0.4,label='1st harm amp'); ax[1,1].bar(x+0.2,raw,0.4,label='raw RMS')
    ax[1,1].set_xticks(x); ax[1,1].set_xticklabels(labs,rotation=90,fontsize=5); ax[1,1].set_ylabel('mm'); ax[1,1].set_title('r_circle theta-harmonic vs raw RMS'); ax[1,1].legend(fontsize=7)
plt.tight_layout(); plt.savefig(f'{OUT}/diag_sigma_map.png',dpi=110); plt.close()
print(f"\nsaved {OUT}/diag_sigma_map.png"); print("DONE")
