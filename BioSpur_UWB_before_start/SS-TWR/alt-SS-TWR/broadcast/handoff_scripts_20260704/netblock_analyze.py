#!/usr/bin/env python3
"""Analyze the static net IN/OUT test: compare BSCCF4 first-path (FP) and tail (dP) between
IN (net blocking) and OUT (net removed) segments, per listener. A real occlusion = FP steps
down (shadow) and/or dP steps up (tail enrichment) when IN, on the shadowed links.
Usage: python3 netblock_analyze.py [BASE_DIR]   (defaults to latest netblock_* session)"""
import csv, glob, os, sys, math, numpy as np
BASE = sys.argv[1] if len(sys.argv)>1 else sorted(glob.glob(
    'SS-TWR/alt-SS-TWR/broadcast/logs/netblock_*'))[-1]
print(f"base = {BASE}")
# marks: epoch label
marks=[]
with open(f'{BASE}/marks.txt') as f:
    for ln in f:
        p=ln.split()
        if len(p)>=3 and p[0].isdigit(): marks.append((float(p[0]), p[2].upper()))
if len(marks)<2:
    print("need >=2 marks (IN/OUT transitions). marks found:", marks); sys.exit(1)
# build labeled intervals [t_i, t_{i+1}) with label of mark i; drop 2s settle after each mark
segs=[]
for i,(t,lab) in enumerate(marks):
    t_end = marks[i+1][0] if i+1<len(marks) else t+1e9
    segs.append((t+2.0, t_end, lab))
print("segments:", [(round(a),round(b),l) for a,b,l in segs])

def load(probe):
    D=glob.glob(f'{BASE}/{probe}/listener_*')
    if not D: return None
    t=[];fp=[];dp=[]
    with open(f'{D[0]}/lpd.csv') as f:
        for r in csv.DictReader(f):
            if r['tag_id']!='4': continue   # BSCCF4
            try:
                rp=float(r['rxpacc']);f1,f2,f3=float(r['fp1']),float(r['fp2']),float(r['fp3']);cp=float(r['cir_pwr'])
                if rp<=0 or cp<=0: continue
                FP=10*math.log10((f1*f1+f2*f2+f3*f3)/(rp*rp)); RX=10*math.log10(cp*(2**17)/(rp*rp))
                t.append(float(r['host_epoch_s'])); fp.append(FP); dp.append(RX-FP)
            except: pass
    return np.array(t),np.array(fp),np.array(dp)

def bylabel(t,v):
    out={'IN':[], 'OUT':[]}
    for a,b,lab in segs:
        m=(t>=a)&(t<b)
        if lab in out: out[lab].extend(v[m].tolist())
    return {k:np.array(x) for k,x in out.items()}

print(f"\n{'listener':9s} {'FP_OUT':>7s} {'FP_IN':>7s} {'dFP':>6s} | {'dP_OUT':>7s} {'dP_IN':>7s} {'d_dP':>6s}   verdict")
for probe in ['LF','LB','LCCF4','LE']:
    r=load(probe)
    if r is None: print(f"{probe}: no data"); continue
    t,fp,dp=r
    F=bylabel(t,fp); D=bylabel(t,dp)
    if len(F['IN'])<5 or len(F['OUT'])<5: print(f"{probe}: too few samples (IN={len(F['IN'])} OUT={len(F['OUT'])})"); continue
    dfp=F['IN'].mean()-F['OUT'].mean(); ddp=D['IN'].mean()-D['OUT'].mean()
    # significance: t-like using pooled std
    sf=math.sqrt(F['IN'].var()/len(F['IN'])+F['OUT'].var()/len(F['OUT']))+1e-9
    z=dfp/sf
    v=''
    if dfp<-1.0 and abs(z)>3: v='*** FP DROP (shadow)'
    elif ddp>1.0: v='** tail up'
    elif abs(z)>3: v='* small but sig'
    else: v='flat'
    print(f"{probe:9s} {F['OUT'].mean():7.2f} {F['IN'].mean():7.2f} {dfp:6.2f} | {D['OUT'].mean():7.2f} {D['IN'].mean():7.2f} {ddp:6.2f}   {v}  (n_in={len(F['IN'])},n_out={len(F['OUT'])},z={z:.1f})")
print("\nInterpretation: FP drop on LF/LB when IN = shadow occlusion confirmed (chopper will work).")
print("LCCF4 expected ~flat FP (near path unblocked); tail dP up = net acting as reflector.")
