#!/usr/bin/env python3
"""STAGE 1: complex CIR reassembly for the 12 roto links ({BS2DCE,BSDC91} x 6 listeners).
Firmware-verified byte order, fractional FP alignment to tap K, FP-referencing z[n]<-z[n]/z[K],
quality filter (as in the phase audit). Caches per-link npz (EP, FP, Zc complex N x WLEN) into
handoff_scripts_20260704/cir_cache/ for Stage 2/3. Memory: chunked per (chunk,listener),
full_matrices not used here. Run: ulimit -v 8000000; python3 coherent_stage1.py [maxchunks]
"""
import csv, glob, os, sys, re, collections, numpy as np
B='logs/roto_sar_overnight_20260705_012548'
CDIR='handoff_scripts_20260704/cir_cache'; os.makedirs(CDIR,exist_ok=True)
MAXCH=int(sys.argv[1]) if len(sys.argv)>1 else 99
NSAMP=1016; K=48; WSTART_BACK=48; WLEN=192
LISTENERS=['LB','LE','LF','LCCF4','L9336','L955A']; ROTO=['BS2DCE','BSDC91']

def chunk_cfg_map(cd):
    """tag_id (str int) -> BS name, from 'CFG assigned[..]: bs=BSxxxx tag=D' in recv raw.log."""
    m={}
    for rl in glob.glob(f'{cd}/recv/raw.log')+glob.glob(f'{cd}/recv/**/raw.log',recursive=True):
        try:
            for line in open(rl,errors='ignore'):
                mm=re.search(r'CFG assigned\[\d+\]: bs=(BS[0-9A-Fa-f]+) tag=(\d+)',line)
                if mm: m[mm.group(2)]=mm.group(1)
        except FileNotFoundError: pass
        if m: break
    return m

def frac_align(c,fp):
    start=int(round(fp))-WSTART_BACK
    if start<0 or start+WLEN>NSAMP: return None
    w=c[start:start+WLEN].astype(complex); frac=fp-start-K
    k=np.fft.fftfreq(WLEN)*WLEN
    return np.fft.ifft(np.fft.fft(w)*np.exp(1j*2*np.pi*k*frac/WLEN))

def frames(pdir,num2name):
    """yield (tag_name, epoch, fp, rxpacc, z_complex[WLEN] FP-referenced)."""
    for d in glob.glob(f'{pdir}/listener_*'):
        mfile=f'{d}/lcirm.csv'; dfile=f'{d}/lcird.csv'
        if not(os.path.exists(mfile) and os.path.exists(dfile)): continue
        meta={}
        for r in csv.DictReader(open(mfile)):
            nm=num2name.get(r['tag_id'])
            if nm not in ROTO: continue
            try: meta[r['accepted_polls']]=(nm,float(r['host_epoch_s']),float(r['fp_index'])/64.0,float(r['rxpacc']))
            except (ValueError,KeyError): pass
        buf={}; cur=None
        def emit(ap,sl):
            if ap not in meta or not sl: return None
            try: tot=b''.join(bytes.fromhex(sl[o]) for o in sorted(sl) if len(sl[o])%2==0)
            except ValueError: return None
            if len(tot)!=NSAMP*4: return None
            iq=np.frombuffer(tot,dtype='<i2').astype(np.float64); cc=iq[0::2]+1j*iq[1::2]
            nm,ep,fp,rx=meta[ap]; w=frac_align(cc,fp)
            if w is None or abs(w[K])<1e-9: return None
            return nm,ep,fp,rx,w/w[K]                 # FP-referenced
        for r in csv.DictReader(open(dfile)):
            try: ap=r['accepted_polls']; off=int(r['offset'])
            except (ValueError,KeyError): continue
            if off==0 and cur is not None:
                o=emit(cur,buf)
                if o is not None: yield o
                buf={}
            cur=ap; buf[off]=r['hex']
        if cur is not None:
            o=emit(cur,buf)
            if o is not None: yield o

chunk_dirs=sorted(glob.glob(f'{B}/chunk*'),key=lambda p:int(p.split('/chunk')[1]))[:MAXCH]
acc={(L,T):{'EP':[],'FP':[],'RX':[],'Z':[]} for L in LISTENERS for T in ROTO}
for cd in chunk_dirs:
    num2name=chunk_cfg_map(cd)
    for L in LISTENERS:
        pdir=f'{cd}/{L}'
        if not os.path.isdir(pdir): continue
        for nm,ep,fp,rx,w in frames(pdir,num2name):
            a=acc[(L,nm)]; a['EP'].append(ep); a['FP'].append(fp); a['RX'].append(rx); a['Z'].append(w.astype(np.complex64))
    # (progress printed per chunk)
    tot=sum(len(acc[(L,T)]['EP']) for L in LISTENERS for T in ROTO)
    print(f"  {cd.split('/chunk')[1]:>3}: cumulative frames={tot}", flush=True)

print("\nper-link frame counts + quality filter (rxpacc>=0.7*median), caching:")
for L in LISTENERS:
    for T in ROTO:
        a=acc[(L,T)]
        if len(a['EP'])<30:
            print(f"  {L}<-{T}: {len(a['EP'])} (too few, skip)"); continue
        EP=np.array(a['EP']); FP=np.array(a['FP']); RX=np.array(a['RX']); Z=np.array(a['Z'])
        rxmed=np.median(RX); fpmed=np.median(FP)
        ok=(RX>=0.7*rxmed)&(np.abs(FP-fpmed)<=3.0)
        EP,FP,Z=EP[ok],FP[ok],Z[ok]; o=np.argsort(EP)
        np.savez(f'{CDIR}/{L}_{T}.npz', EP=EP[o],FP=FP[o],Z=Z[o])
        print(f"  {L}<-{T}: kept {ok.sum()}/{len(ok)} frames -> {CDIR}/{L}_{T}.npz")
print("DONE")
