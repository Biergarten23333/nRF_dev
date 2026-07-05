#!/usr/bin/env python3
"""Slow-chopper tap-level analysis (robust). For each CIR tap, full Lomb-Scargle periodogram
over 6-200s; record its peak power and peak period. Real chopper = CHOPPED links (LCCF4/LF/LB)
have their strongest tap peaking at a COMMON period, much stronger than CTRL (LE).
Usage: python3 slowchop_analyze.py [BASE]"""
import csv, glob, os, sys, math, numpy as np, matplotlib
matplotlib.use('Agg'); import matplotlib.pyplot as plt
from scipy.signal import lombscargle
BASE=sys.argv[1] if len(sys.argv)>1 else sorted(glob.glob('SS-TWR/alt-SS-TWR/broadcast/logs/slowchop_*'))[-1]
print(f"base={BASE}")
NSAMP=1016; PRE=12; POST=84; WIN=PRE+POST
periods=np.linspace(6,200,500); ang=2*np.pi*(1.0/periods)

def load(probe,tid='4'):
    D=glob.glob(f'{BASE}/{probe}/listener_*')
    if not D: return np.array([]),np.array([])
    meta={}
    for r in csv.DictReader(open(f'{D[0]}/lcirm.csv')):
        if r['tag_id']!=tid: continue
        try: meta[r['accepted_polls']]=(float(r['fp_index'])/64.0,float(r['rxpacc']),float(r['host_epoch_s']))
        except: pass
    segs={}
    for r in csv.DictReader(open(f'{D[0]}/lcird.csv')):
        if r['accepted_polls'] in meta: segs.setdefault(r['accepted_polls'],[]).append((int(r['offset']),r['hex']))
    T=[];W=[]
    for ap,seg in segs.items():
        fps,rp,ep=meta[ap];seg.sort();h=''.join(x for _,x in seg)
        try: iq=np.frombuffer(bytes.fromhex(h),dtype='<i2').astype(float)
        except: continue
        if iq.size<2*NSAMP: continue
        mag=np.hypot(iq[0:2*NSAMP:2],iq[1:2*NSAMP:2])
        if rp>0: mag/=rp
        fp=int(round(fps))
        if fp-PRE<0 or fp+POST>NSAMP: continue
        T.append(ep);W.append(mag[fp-PRE:fp+POST])
    o=np.argsort(T);return np.array(T)[o],(np.array(W)[o] if W else np.array([]))

data={p:load(p) for p in ['LCCF4','LF','LB','LE']}
for p,(T,W) in data.items(): print(f"  {p}: {len(T)} CIR")

def per_tap(T,W):
    T0=T-T[0]; pk=np.zeros(W.shape[1]); pP=np.zeros(W.shape[1])
    for k in range(W.shape[1]):
        y=W[:,k]-np.median(W[:,k])
        if y.std()<1e-9: continue
        px=lombscargle(T0,y,ang,normalize=True); j=int(np.argmax(px)); pk[k]=px[j]; pP[k]=periods[j]
    return pk,pP

print(f"\n{'chan':7s} {'nCIR':>5s} {'bestTap':>7s} {'peakLS':>6s} {'peakP_s':>7s} {'SNR':>5s}  {'note'}")
res={}
for p,(T,W) in data.items():
    if len(T)<30: print(f"{p}: too few"); continue
    pk,pP=per_tap(T,W); bt=int(np.argmax(pk)); snr=pk[bt]/max(np.median(pk[pk>0]),1e-9)
    res[p]=(pk,pP,bt,T-T[0],W)
    print(f"{p:7s} {len(T):5d} {bt-PRE:7d} {pk[bt]:6.3f} {pP[bt]:7.1f} {snr:5.1f}")

# do chopped channels agree on a period?
chop=[res[p] for p in ['LCCF4','LF','LB'] if p in res]
chopP=[r[1][r[2]] for r in chop]
print(f"\nCHOP best-tap periods: {[round(x,1) for x in chopP]}   CTRL(LE) best-tap period: {res['LE'][1][res['LE'][2]]:.1f}s" if 'LE' in res else "")
agree = (max(chopP)-min(chopP))/np.mean(chopP) < 0.2 if chopP else False
print(f"CHOP periods agree within 20%: {agree}")

# figure
fig,ax=plt.subplots(2,4,figsize=(20,8))
for i,p in enumerate(['LCCF4','LF','LB','LE']):
    if p not in res: continue
    pk,pP,bt,T0,W=res[p]
    ax[0][i].plot(np.arange(WIN)-PRE,pk);ax[0][i].axvline(bt-PRE,color='r',ls=':')
    ax[0][i].set_title(f'{p}: per-tap peak LS  (best tap {bt-PRE} @ {pP[bt]:.0f}s)',fontsize=9);ax[0][i].set_xlabel('tap')
    P0=pP[bt]; ph=(T0%P0)/P0; b=np.linspace(0,1,13);idx=np.digitize(ph,b)
    fm=[(W[idx==k,bt]-np.median(W[:,bt])).mean() for k in range(1,13)]
    ax[1][i].plot(np.linspace(0,1,12),fm,'-o',ms=3);ax[1][i].axhline(0,color='0.7',lw=0.6)
    ax[1][i].set_title(f'best tap folded @{P0:.0f}s (depth {max(fm)-min(fm):.3f})',fontsize=9);ax[1][i].set_xlabel('phase')
fig.suptitle('Slow-chopper tap-level. Real = CHOP(LCCF4/LF/LB) best taps agree on a period + strong; CTRL(LE) weak/other',fontsize=11)
plt.tight_layout(rect=[0,0,1,0.96]);out=os.path.join(os.path.dirname(__file__),'slowchop_result.png');plt.savefig(out,dpi=110);print('saved',out)
