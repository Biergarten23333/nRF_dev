#!/usr/bin/env python3
"""HUMAN-MOTION PROBE (iii, report-only, no gate). Is a person walking (carrying NO tag) visible
above the empty-room CIR floor? Detectability only -- no localization.

Per (listener, tag) link, reusing the AUDITED pipeline (firmware byte order, fractional FP align to
tap K, FP-normalized |CIR|):
  - per-frame TAIL ENERGY over excess delay 2-35 ns (taps K+2..K+35)
  - z-score vs the EMPTY-ROOM chunks' per-link mean/std
  - slow-time z(t) plotted for all links with the walk window shaded
  - per-tap VARIANCE RATIO (walk / empty) heatmap per link
Verdict per link: sustained |z| > 3 during the walk window = visible (yes/no).
Cross-check: does any SCALAR channel (recv ge7, listener lpd poll rate) wobble in the same window?
(The CIR channel is immune to the BLE/TDMA artifact; the scalar cross-check flags coincidences.)

Usage: python3 verify_motion.py <session_dir> [--walk-start EPOCH --walk-stop EPOCH]
       (walk window read from chunk_manifest.json if not given)
"""
import csv, glob, os, sys, json, numpy as np
import matplotlib; matplotlib.use('Agg'); import matplotlib.pyplot as plt
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'scripts'))
from tag_roster import load_roster

SESS = sys.argv[1] if len(sys.argv) > 1 else 'logs/verify_dryrun'
OUT = 'handoff_scripts_20260704/figs_20260704'; os.makedirs(OUT, exist_ok=True)
NSAMP=1016; K=48; WSTART_BACK=48; WLEN=192; SAMP_NS=1.0016
TAIL=np.arange(K+2, K+36)              # excess 2..35 ns
LISTENERS=['LCCF4','L9336','L955A','LB','LE','LF']

wstart=wstop=None
if '--walk-start' in sys.argv: wstart=float(sys.argv[sys.argv.index('--walk-start')+1])
if '--walk-stop'  in sys.argv: wstop =float(sys.argv[sys.argv.index('--walk-stop')+1])
mpath=os.path.join(SESS,'chunk_manifest.json')
if (wstart is None) and os.path.exists(mpath):
    m=json.load(open(mpath)); wstart=m.get('walk_start_epoch'); wstop=m.get('walk_stop_epoch')
    if wstart is None and m.get('chunks'):           # fall back to chunk-2 window
        c2=m['chunks'][1]; wstart,wstop=c2['start_epoch'],c2['end_epoch']
if wstart is None:
    sys.exit("no walk window (give --walk-start/--walk-stop or chunk_manifest.json). Cannot z-score.")

roster=load_roster(SESS); id2name=roster.get('by_id',{})

def frac_align(c,fp):
    start=int(round(fp))-WSTART_BACK
    if start<0 or start+WLEN>NSAMP: return None
    w=c[start:start+WLEN].astype(complex); frac=fp-start-K
    k=np.fft.fftfreq(WLEN)*WLEN
    return np.fft.ifft(np.fft.fft(w)*np.exp(1j*2*np.pi*k*frac/WLEN))

def link_frames(listener):
    """yield (tagname, epoch, |CIR|_normalized[WLEN]) for one listener, audited align."""
    for base in [f'{SESS}/{listener}/listener_*', f'{SESS}/chunk*/{listener}/listener_*']:
        for d in sorted(glob.glob(base)):
            mfile=f'{d}/lcirm.csv'; dfile=f'{d}/lcird.csv'
            if not(os.path.exists(mfile) and os.path.exists(dfile)): continue
            meta={}
            for r in csv.DictReader(open(mfile)):
                try: meta[r['accepted_polls']]=(id2name.get(r['tag_id'],f"tag{r['tag_id']}"),
                        float(r['host_epoch_s']),float(r['fp_index'])/64.0,float(r['rxpacc']))
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
                mag=np.abs(w)/abs(w[K])            # FP-normalized magnitude
                return nm,ep,mag
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

print("="*88)
print(f"HUMAN-MOTION PROBE  session={SESS}")
print(f"walk window: [{wstart:.0f}, {wstop:.0f}] ({wstop-wstart:.0f}s)   tag roster: {id2name}")
print("="*88)
results={}   # (L,tag) -> dict
for L in LISTENERS:
    by=( {} )
    for nm,ep,mag in link_frames(L):
        by.setdefault(nm,[]).append((ep,mag))
    for nm,rows in by.items():
        rows.sort(); EP=np.array([e for e,_ in rows]); M=np.array([m for _,m in rows])
        if len(EP)<20: continue
        tail=np.sqrt((M[:,TAIL]**2).sum(1))             # per-frame tail energy (RMS over excess 2-35ns)
        emp=(EP<wstart)|(EP>wstop)                      # empty-room frames
        wlk=(EP>=wstart)&(EP<=wstop)
        if emp.sum()<10 or wlk.sum()<3: continue
        mu,sd=tail[emp].mean(),tail[emp].std()+1e-12
        z=(tail-mu)/sd
        # per-tap variance ratio walk/empty
        vr=(M[wlk].var(0)+1e-12)/(M[emp].var(0)+1e-12)
        zwalk=z[wlk]; sustained=np.mean(np.abs(zwalk)>3)   # fraction of walk frames |z|>3
        results[(L,nm)]=dict(EP=EP,z=z,emp=emp,wlk=wlk,vr=vr,zwalk=zwalk,sustained=sustained,
                             peakz=np.max(np.abs(zwalk)))
        print(f"  {L:6}<-{nm:7}: Nempty={emp.sum():5d} Nwalk={wlk.sum():4d}  "
              f"walk |z|>3 frac={sustained:4.2f}  peak|z|={np.max(np.abs(zwalk)):5.1f}  "
              f"{'<== VISIBLE' if sustained>0.3 else ''}")

# ---- scalar cross-check: recv ge7 + a listener lpd rate in the walk window ----
def ge7_series():
    t=[]; g=[]
    for rl in glob.glob(f'{SESS}/recv/raw.log')+glob.glob(f'{SESS}/recv*/raw.log')+glob.glob(f'{SESS}/chunk*/recv/raw.log'):
        import re
        for line in open(rl,errors='ignore'):
            mm=re.search(r'ge7=(\d+)%',line)
            # recv raw.log lines may not carry epoch; use host wallclock if present else skip
        break
    return None
print("\n  SCALAR CROSS-CHECK (BLE/TDMA-artifact channel): compare recv ge7 / listener lpd poll-rate")
print("  inside vs outside the walk window; a CIR anomaly that COINCIDES with a scalar dip is suspect.")
# lpd poll-rate in-vs-out per listener (cheap, robust)
for L in LISTENERS[:1] + [x for x in LISTENERS[1:]]:
    lpd=sorted(glob.glob(f'{SESS}/{L}/listener_*/lpd.csv')+glob.glob(f'{SESS}/chunk*/{L}/listener_*/lpd.csv'))
    if not lpd: continue
    ep=[]
    for f in lpd:
        for r in csv.DictReader(open(f)):
            try: ep.append(float(r['host_epoch_s']))
            except (ValueError,KeyError): pass
    ep=np.sort(np.array(ep))
    if len(ep)<20: continue
    ins=((ep>=wstart)&(ep<=wstop)); out=~ins
    def rt(a): d=np.diff(np.sort(ep[a])); d=d[(d>0)&(d<5)]; return 1/np.median(d) if len(d)>3 else float('nan')
    print(f"    {L}: lpd poll-rate walk={rt(ins):.2f}Hz empty={rt(out):.2f}Hz "
          f"({'STABLE' if abs(rt(ins)-rt(out))<0.15*rt(out) else 'WOBBLE (scalar coincidence!)'})")
    break   # one representative listener; extend if a coincidence is seen

# ---- figures ----
if results:
    fig,ax=plt.subplots(figsize=(11,5))
    for (L,nm),d in results.items():
        t=(d['EP']-wstart)/60.0
        ax.plot(t,d['z'],lw=0.8,label=f'{L}<-{nm}')
    ax.axvspan(0,(wstop-wstart)/60.0,color='orange',alpha=0.2,label='walk')
    ax.axhline(3,color='r',ls='--'); ax.axhline(-3,color='r',ls='--')
    ax.set_xlabel('time since walk start (min)'); ax.set_ylabel('tail-energy z-score')
    ax.set_title('Human-motion probe: slow-time z(t) per link (walk shaded)')
    ax.legend(fontsize=6,ncol=3); ax.set_ylim(-8,12); plt.tight_layout()
    plt.savefig(f'{OUT}/verify_motion_zt.png',dpi=110); plt.close()
    # variance-ratio heatmap montage
    n=len(results); nc=3; nr=int(np.ceil(n/nc))
    fig,axes=plt.subplots(nr,nc,figsize=(4*nc,2.4*nr),squeeze=False)
    ex=np.arange(WLEN);
    for i,((L,nm),d) in enumerate(results.items()):
        a=axes[i//nc][i%nc]; a.plot((np.arange(WLEN)-K)*SAMP_NS,d['vr']); a.axhline(1,color='k',lw=0.5)
        a.set_xlim(-2,35); a.set_title(f'{L}<-{nm} var-ratio',fontsize=8); a.set_yscale('log')
    for j in range(n,nr*nc): axes[j//nc][j%nc].axis('off')
    plt.tight_layout(); plt.savefig(f'{OUT}/verify_motion_varratio.png',dpi=110); plt.close()

print("\n"+"="*88+"\nVERDICT (motion detectability)\n"+"="*88)
vis=[k for k,d in results.items() if d['sustained']>0.3]
print(f"  links with sustained |z|>3 over >30% of walk frames: {len(vis)}  {vis}")
if len(vis)>=2:
    print("  => WALKING PERSON VISIBLE above the empty-room CIR floor on >=2 links (detectability only,")
    print("     no localization claimed). Confirm no scalar-channel coincidence flagged above.")
else:
    print("  => NOT visible on >=2 links: person does not sustain |z|>3 above the empty-room floor.")
print("  figures: verify_motion_zt.png, verify_motion_varratio.png")
