#!/usr/bin/env python3
"""L-B up-link CIR notch detector — PER-TAG->B PER-LINK occlusion scoring (rider 2).

Three independent signals per captured poll, per tag (the three tag->B down... up-link rays
diverge above floor-anchor B, so each is scored and alarmed SEPARATELY — never a joint decision):
  1. rxpow (dB)      -> DROPS on real absorption           (meat truly in path)
  2. FP/peak (dB)    -> RISES when the direct ray is killed (multipath arrives late)
  3. FP/tail (dB)    -> DROPS: first path collapses vs late tail energy ("@ collapses, tail remains")

Thresholds are z-scores vs the per-tag LOS baseline distribution (calibrated tonight), NOT hardcoded.

Modes:
  baseline : read L-B raw.log(s) -> per-tag mean/std of the 3 metrics -> baseline.json
  replay   : score a raw.log against a baseline (test the detector offline)
  live     : stream L-B serial, per-capture per-tag z-scores + per-link ALARM
"""
import sys, time, math, json, argparse, glob
from collections import defaultdict

TAG = {2: "BS9336", 3: "BS955A", 4: "BSCCF4"}
BARS = " .:-=+*#%@"

# ---------- CIR decode ----------
def decode_mag(b):
    n = len(b)//4; out=[]
    for k in range(n):
        re=int.from_bytes(b[4*k:4*k+2],"little",signed=True)
        im=int.from_bytes(b[4*k+2:4*k+4],"little",signed=True)
        out.append(math.hypot(re,im))
    return out

def metrics_from_capture(hdr, mags):
    """Return dict of the 3 metrics (or None if unusable)."""
    if not mags: return None
    peak=max(mags); peak_i=mags.index(peak)
    fp_i=max(0,min(len(mags)-1,int(round(hdr["firstPath"]/64.0))))
    fp=mags[fp_i]
    rp=hdr["rxpacc"] or 1
    lo=peak_i+8; hi=min(len(mags),peak_i+40)
    tail=mags[lo:hi] if hi>lo else []
    tail_rms=math.sqrt(sum(v*v for v in tail)/len(tail)) if tail else 0.0
    if fp<=0 or peak<=0 or tail_rms<=0 or hdr["maxGrowth"]<=0: return None
    return {
        "rxpow": 10*math.log10(hdr["maxGrowth"]*(2**17)/(rp*rp)),
        "fp_over_peak": 20*math.log10(peak/fp),
        "fp_over_tail": 20*math.log10(fp/tail_rms),
        "fp_i": fp_i, "peak_i": peak_i,
    }

# ---------- raw.log stream parser ----------
def iter_captures(lines):
    """Yield (tag_id, hdr, accbytes) for each complete LCIRM..LCIRD..LCIRE."""
    hdr=None; cur=None; acc_len=0; chunks={}
    for raw in lines:
        raw=raw.strip()
        if raw.startswith("LCIRM;"):
            f=raw.split(";")
            try:
                hdr={"poll":int(f[4]),"tag":int(f[6]),"firstPath":int(f[10]),
                     "fpAmp1":int(f[11]),"maxGrowth":int(f[14]),"rxpacc":int(f[15]),"acc_len":int(f[16])}
                cur=hdr["poll"]; acc_len=hdr["acc_len"]; chunks={}
            except (IndexError,ValueError): hdr=None; cur=None
        elif raw.startswith("LCIRD;") and cur is not None:
            f=raw.split(";")
            try:
                if int(f[2])==cur:
                    off=int(f[3]); ln=int(f[4])
                    if len(f[5])>=2*ln: chunks[off]=bytes.fromhex(f[5][:2*ln])
            except (IndexError,ValueError): pass
        elif raw.startswith("LCIRE;") and cur is not None and hdr is not None:
            data=bytearray(); off=0; ok=True
            while off<acc_len:
                c=chunks.get(off)
                if c is None: ok=False; break
                data+=c; off+=len(c)
            if ok: yield hdr["tag"], hdr, bytes(data)
            hdr=None; cur=None; chunks={}

def mean_std(xs):
    n=len(xs)
    if n==0: return (0.0,0.0,0)
    m=sum(xs)/n
    var=sum((x-m)**2 for x in xs)/n if n>1 else 0.0
    return (m, math.sqrt(var), n)

# ---------- modes ----------
def mode_baseline(args):
    files=[]
    for p in args.raw: files+=glob.glob(p, recursive=True)
    if not files: sys.exit("no raw.log matched")
    acc=defaultdict(lambda: defaultdict(list))
    for fn in files:
        with open(fn, errors="replace") as fh:
            for tag,hdr,data in iter_captures(fh):
                m=metrics_from_capture(hdr,decode_mag(data))
                if m:
                    for k in ("rxpow","fp_over_peak","fp_over_tail"): acc[tag][k].append(m[k])
    base={"generated": time.strftime("%Y-%m-%d %H:%M:%S"), "files": files, "tags": {}}
    print(f"{'tag':8s}{'n':>7s} | {'rxpow m/sd':>16s} {'FP/peak m/sd':>16s} {'FP/tail m/sd':>16s}")
    for tag in sorted(acc):
        t={}
        for k in ("rxpow","fp_over_peak","fp_over_tail"):
            m,s,n=mean_std(acc[tag][k]); t[k]={"mean":m,"std":max(s,1e-6),"n":n}
        base["tags"][str(tag)]=t
        print(f"{TAG.get(tag,tag):8s}{t['rxpow']['n']:>7d} | "
              f"{t['rxpow']['mean']:7.2f}/{t['rxpow']['std']:<5.2f}  "
              f"{t['fp_over_peak']['mean']:6.2f}/{t['fp_over_peak']['std']:<5.2f}  "
              f"{t['fp_over_tail']['mean']:6.2f}/{t['fp_over_tail']['std']:<5.2f}")
    json.dump(base, open(args.out,"w"), indent=1)
    print(f"[ok] wrote {args.out}")

def score(tag, m, base, zabs, zfp):
    b=base["tags"].get(str(tag))
    if not b: return None
    zrx=(b["rxpow"]["mean"]-m["rxpow"])/b["rxpow"]["std"]          # + = dropped (occluded)
    zpk=(m["fp_over_peak"]-b["fp_over_peak"]["mean"])/b["fp_over_peak"]["std"]  # + = risen
    ztl=(b["fp_over_tail"]["mean"]-m["fp_over_tail"])/b["fp_over_tail"]["std"]  # + = dropped
    drx=m["rxpow"]-b["rxpow"]["mean"]
    occ = (zrx>=zabs) or (zpk>=zfp and ztl>=zfp)   # per-link: strong absorption OR clear FP collapse
    return dict(zrx=zrx,zpk=zpk,ztl=ztl,drx=drx,occ=occ)

def fmt(tag,m,s):
    flag = f">>> OCCLUDED (link {TAG.get(tag,tag)}->B)" if s["occ"] else "los"
    return (f"{TAG.get(tag,tag):7s} rxpow={m['rxpow']:5.1f}dB(Δ{s['drx']:+5.1f},z{s['zrx']:4.1f})  "
            f"FP/peak={m['fp_over_peak']:5.1f}dB(z{s['zpk']:4.1f})  "
            f"FP/tail={m['fp_over_tail']:5.1f}dB(z{s['ztl']:4.1f})  {flag}")

def mode_replay(args):
    base=json.load(open(args.baseline))
    files=[];
    for p in args.raw: files+=glob.glob(p,recursive=True)
    for fn in files:
        with open(fn,errors="replace") as fh:
            for tag,hdr,data in iter_captures(fh):
                m=metrics_from_capture(hdr,decode_mag(data))
                if not m: continue
                s=score(tag,m,base,args.z_abs,args.z_fp)
                if s: print(fmt(tag,m,s))

def mode_live(args):
    import serial
    base=json.load(open(args.baseline))
    ser=serial.Serial(args.port,args.baud,timeout=1.0); ser.reset_input_buffer()
    print(f"[notch] {args.port} vs {args.baseline}  (per-link z-alarm: |zabs|>={args.z_abs} or FP-collapse z>={args.z_fp})",flush=True)
    def lines():
        while True:
            yield ser.readline().decode("ascii","replace")
    last=0.0
    for tag,hdr,data in iter_captures(lines()):
        m=metrics_from_capture(hdr,decode_mag(data))
        if not m: continue
        s=score(tag,m,base,args.z_abs,args.z_fp)
        if not s: continue
        now=time.time()
        if s["occ"] or (now-last)>=args.min_interval_s:
            last=now
            print(f"[{time.strftime('%H:%M:%S')}] {fmt(tag,m,s)}",flush=True)

def main():
    ap=argparse.ArgumentParser()
    sub=ap.add_subparsers(dest="mode",required=True)
    b=sub.add_parser("baseline"); b.add_argument("--raw",nargs="+",required=True); b.add_argument("--out",required=True)
    r=sub.add_parser("replay"); r.add_argument("--baseline",required=True); r.add_argument("--raw",nargs="+",required=True)
    r.add_argument("--z-abs",type=float,default=3.0); r.add_argument("--z-fp",type=float,default=3.0)
    l=sub.add_parser("live"); l.add_argument("--baseline",required=True); l.add_argument("--port",required=True)
    l.add_argument("--baud",type=int,default=460800); l.add_argument("--z-abs",type=float,default=3.0)
    l.add_argument("--z-fp",type=float,default=3.0); l.add_argument("--min-interval-s",type=float,default=0.8)
    a=ap.parse_args()
    {"baseline":mode_baseline,"replay":mode_replay,"live":mode_live}[a.mode](a)

if __name__=="__main__":
    main()
