#!/usr/bin/env python3
"""Live readout for the net IN/OUT test — run in a SECOND terminal while netblock_test.sh
records. Every ~3s prints the recent-window mean first-path (FP) and tail (dP) for BSCCF4 as
heard by each listener, so you SEE the step when you move the net. Ctrl-C to quit.
Reads the active session from /tmp/netblock_current."""
import csv, glob, os, time, math, sys
WIN=6.0  # seconds of recent samples to average

def base():
    try: return open('/tmp/netblock_current').read().strip()
    except: return None

def read_recent(probe, B, now):
    D=glob.glob(f'{B}/{probe}/listener_*')
    if not D: return None
    fp=[]; dp=[]
    try:
        with open(f'{D[0]}/lpd.csv') as f:
            for r in csv.DictReader(f):
                if r.get('tag_id')!='4': continue
                try:
                    te=float(r['host_epoch_s'])
                    if te < now-WIN: continue
                    rp=float(r['rxpacc']); f1,f2,f3=float(r['fp1']),float(r['fp2']),float(r['fp3']); cp=float(r['cir_pwr'])
                    if rp<=0 or cp<=0: continue
                    FP=10*math.log10((f1*f1+f2*f2+f3*f3)/(rp*rp)); RX=10*math.log10(cp*(2**17)/(rp*rp))
                    fp.append(FP); dp.append(RX-FP)
                except: pass
    except FileNotFoundError: return None
    if not fp: return (0,float('nan'),float('nan'))
    n=len(fp); import statistics as st
    return (n, st.mean(fp), st.mean(dp))

B=base()
if not B or not os.path.isdir(B):
    print("no active session (start netblock_test.sh first)"); sys.exit(1)
print(f"live on {B}   (window {WIN:.0f}s, BSCCF4)   Ctrl-C to quit")
print(f"{'time':8s} | {'LF  FP   dP':>16s} | {'LB  FP   dP':>16s} | {'LCCF4 FP  dP':>16s} | {'LE FP   dP':>16s}")
try:
    while True:
        now=time.time(); cells=[]
        for p in ['LF','LB','LCCF4','LE']:
            r=read_recent(p,B,now)
            if r is None: cells.append(f"{'--':>16s}"); continue
            n,fp,dp=r
            cells.append(f"{fp:6.2f} {dp:5.2f}(n{n:2d})")
        print(f"{time.strftime('%H:%M:%S')} | "+" | ".join(cells))
        time.sleep(3)
except KeyboardInterrupt:
    print("\nbye")
