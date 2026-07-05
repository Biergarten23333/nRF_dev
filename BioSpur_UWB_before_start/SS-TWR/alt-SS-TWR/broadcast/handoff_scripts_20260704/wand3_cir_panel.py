#!/usr/bin/env python3
"""18-link CIR overview after flashing L-9336/L-955A to cirprobe.
6 probes x 3 wand tags. Shows the 3 wand-side quasi-monostatic vantages (near volume center)
now delivering full CIR alongside the 3 anchor-side probes.
Usage: python3 wand3_cir_panel.py <capture_base_dir>"""
import csv, glob, os, sys, numpy as np, matplotlib
matplotlib.use('Agg'); import matplotlib.pyplot as plt
BASE=sys.argv[1]
NSAMP=1016; PRE=8; POST=100
TAGS=[('4','BSCCF4'),('6','BS9336'),('10','BS955A')]
WAND=['LCCF4','L9336','L955A']; ANCH=['LB','LE','LF']
PROBES=WAND+ANCH
COL={'LCCF4':'#d62728','L9336':'#ff7f0e','L955A':'#e377c2','LB':'#1f77b4','LE':'#2ca02c','LF':'#7f7f7f'}

def mean_cir(probe,tid):
    D=glob.glob(f'{BASE}/{probe}/listener_*')
    if not D: return None,0
    meta={}
    try:
        for r in csv.DictReader(open(f'{D[0]}/lcirm.csv')):
            if r['tag_id']!=tid: continue
            try: meta[r['accepted_polls']]=(float(r['fp_index'])/64.0,float(r['rxpacc']))
            except: pass
        segs={}
        for r in csv.DictReader(open(f'{D[0]}/lcird.csv')):
            if r['accepted_polls'] in meta: segs.setdefault(r['accepted_polls'],[]).append((int(r['offset']),r['hex']))
    except FileNotFoundError: return None,0
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
    return (acc/n if n>0 else None), n

fig,ax=plt.subplots(1,3,figsize=(18,6),sharey=True)
taps=np.arange(PRE+POST)-PRE
print(f"{'link':16s} {'nCIR':>4s} {'FPpeak':>8s}")
for j,(tid,tname) in enumerate(TAGS):
    for probe in PROBES:
        m,n=mean_cir(probe,tid)
        if m is None or n==0: print(f"{probe}->{tname:8s} {n:4d}   none"); continue
        pk=m[PRE] if m[PRE]>0 else m.max()
        dB=20*np.log10(np.maximum(m,1e-9)/pk)
        wandside=probe in WAND
        ax[j].plot(taps,dB,color=COL[probe],lw=2.2 if wandside else 1.2,
                   ls='-' if wandside else '--',
                   label=f"{probe}{' (wand)' if wandside else ''}  n={n}",alpha=0.9 if wandside else 0.7)
        print(f"{probe}->{tname:8s} {n:4d} {pk:8.4f}")
    ax[j].set_title(f'tag {tid} = {tname}',fontsize=11)
    ax[j].set_xlabel('tap (0 = first path)'); ax[j].axvline(0,color='0.8',lw=0.8,zorder=0)
    ax[j].set_ylim(-40,3); ax[j].grid(alpha=0.25); ax[j].legend(fontsize=8,loc='upper right')
ax[0].set_ylabel('CIR magnitude (dB, rel. first-path)')
fig.suptitle('18-link CIR after flashing L-9336 / L-955A to cirprobe — solid=wand-side (near volume center), dashed=anchor-side',fontsize=12)
plt.tight_layout(rect=[0,0,1,0.96])
out=os.path.join(os.path.dirname(__file__),'figs_20260704','wand3_cir_panel.png')
os.makedirs(os.path.dirname(out),exist_ok=True)
plt.savefig(out,dpi=120); print('saved',out)
