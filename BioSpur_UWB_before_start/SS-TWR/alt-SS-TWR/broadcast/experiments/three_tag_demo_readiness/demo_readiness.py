#!/usr/bin/env python3
"""3-tag demo-readiness: cold-start lottery (Phase A) + prewarm-retry reliability (Phase B).
Phase re-draw = cmd_all REBOOT (full cold boot; clears stuck-0; same lottery as reconnect).
ge7 = fraction of a tag's TR sweeps with popcount(valid_mask)>=7 (matches prewarm_probe_ge7).
Authoritative per-tag data = Master_Tag TR stream (ttyACM0). Geiger (ttyACM6) = unfiltered cross-check.
"""
import serial, time, json, os, sys
from collections import defaultdict

OUT = "/mnt/nrf_ssd/nRF_dev/BioSpur_UWB_before_start/SS-TWR/alt-SS-TWR/broadcast/experiments/three_tag_demo_readiness"
TAGS = ["BS9336","BS955A","BSCCF4"]
THRESH = 0.85          # prewarm ge7 victim threshold
GOOD_ALL = 0.90        # "good start" = all 3 tags ge7 >= this
SETTLE = 16            # s after reboot before measuring (reconnect + re-CFG)
MEAS = 30             # s TR window per cold start
HOLD_S = 720          # s hold/persistence test (12 min)
HOLD_BIN = 60         # s bin for hold
N = 10               # cold starts per phase
MAX_ATTEMPTS = 5      # prewarm reroll budget

results = {"meta":{"threshold":THRESH,"good_all":GOOD_ALL,"settle_s":SETTLE,"meas_s":MEAS,
                   "hold_s":HOLD_S,"N":N,"max_attempts":MAX_ATTEMPTS,"redraw":"cmd_all REBOOT"},
           "phase_A":{"cold_starts":[]}, "phase_B":{"cold_starts":[]}}

def save():
    with open(os.path.join(OUT,"results.json"),"w") as f: json.dump(results,f,indent=2)

def log(m):
    print(m, flush=True)

def pc(h):
    try: return bin(int(h,16)).count("1")
    except: return 0

def reboot():
    m=serial.Serial(); m.port="/dev/ttyACM0"; m.baudrate=115200
    m.dtr=False; m.rts=False; m.timeout=0.2; m.open(); time.sleep(0.1); m.reset_input_buffer()
    m.write(b"cmd_all REBOOT\n"); m.flush(); time.sleep(2.5); m.close()

def tr_measure(secs):
    """Return per-tag {ge7,ge8,n,raw_nonzero,rng_sample}."""
    m=serial.Serial(); m.port="/dev/ttyACM0"; m.baudrate=115200
    m.dtr=False; m.rts=False; m.timeout=0.2; m.open(); time.sleep(0.1); m.reset_input_buffer()
    t0=time.time(); buf=b""
    while time.time()-t0<secs:
        c=m.read(8192); buf+=c if c else b""
    m.close()
    per=defaultdict(lambda:{"n":0,"ge7c":0,"ge8c":0,"rawc":0,"sample":None})
    for ln in buf.decode(errors="replace").splitlines():
        if "notify: TR;" not in ln: continue
        nm=ln.split(" notify:")[0].split()[-1]
        if nm not in TAGS: continue
        p=ln.split("notify: ",1)[1].split(";")
        if len(p)<8 or p[0]!="TR": continue
        v=pc(p[6]); d=per[nm]; d["n"]+=1
        if v>=7: d["ge7c"]+=1
        if v>=8: d["ge8c"]+=1
        rngs=[x for x in p[7].split(",") if x.strip()]
        nz=sum(1 for x in rngs if x.isdigit() and int(x)>0)
        if nz>0: d["rawc"]+=1
        if d["sample"] is None and nz>0: d["sample"]=p[7]
    out={}
    for t in TAGS:
        d=per[t]; n=d["n"]
        out[t]={"n":n,"ge7":round(d["ge7c"]/n,3) if n else 0.0,
                "ge8":round(d["ge8c"]/n,3) if n else 0.0,
                "raw_frac":round(d["rawc"]/n,3) if n else 0.0,
                "hz":round(n/secs,2),"sample":d["sample"]}
    return out

def geiger_snap(secs=5):
    try:
        g=serial.Serial(); g.port="/dev/ttyACM6"; g.baudrate=460800
        g.dtr=False; g.rts=False; g.timeout=0.2; g.open(); time.sleep(0.1); g.reset_input_buffer()
        t0=time.time(); buf=b""
        while time.time()-t0<secs:
            c=g.read(8192); buf+=c if c else b""
        g.close()
        from collections import Counter
        lines=[l for l in buf.decode(errors="replace").splitlines() if l.startswith(("UF;","UL;"))]
        p=Counter()
        for ln in lines:
            f=ln.split(";")
            if len(f)>=7 and f[5]=="0xffff": p[f[4]]+=1
        return {"frames_s":round(len(lines)/secs,1),"pollers":dict(p)}
    except Exception as e:
        return {"error":str(e)}

def is_good(meas):
    return all(meas[t]["ge7"]>=GOOD_ALL for t in TAGS)

def cold_probe():
    """One cold boot -> settle -> measure. Returns meas dict."""
    reboot(); time.sleep(SETTLE)
    return tr_measure(MEAS)

def hold_test(mins_s):
    log(f"    HOLD {mins_s}s in {HOLD_BIN}s bins ...")
    bins=[]
    t0=time.time()
    while time.time()-t0 < mins_s:
        b=tr_measure(HOLD_BIN)
        row={t:b[t]["ge7"] for t in TAGS}; row["ok"]=is_good(b)
        bins.append(row)
        log(f"      t={int(time.time()-t0)}s ge7 "+" ".join(f"{t}={b[t]['ge7']:.0%}" for t in TAGS)+f" ok={row['ok']}")
        results["_hold_partial"]=bins; save()
    return bins

# ================= PHASE A: lottery (no reroll) =================
log("=== PHASE A: cold-start lottery, NO reroll, N=%d ===" % N)
good_A=[]
for i in range(1,N+1):
    g=geiger_snap(5)
    meas=cold_probe()
    good=is_good(meas)
    rec={"i":i,"geiger":g,"tags":meas,"good":good}
    results["phase_A"]["cold_starts"].append(rec); save()
    log(f"  [A{i}] "+" ".join(f"{t}={meas[t]['ge7']:.0%}(n{meas[t]['n']})" for t in TAGS)+f"  GOOD={good}")
    if good: good_A.append(i)
results["phase_A"]["good_count"]=len(good_A)
results["phase_A"]["good_starts"]=good_A
log(f"  PHASE A: {len(good_A)}/{N} good starts")
# persistence hold on the last good start (or reboot until good, max 6 tries)
if True:
    for _ in range(6):
        meas=cold_probe()
        if is_good(meas): break
    log("  PHASE A persistence hold (12min) on a good start:")
    results["phase_A"]["hold"]=hold_test(HOLD_S); results.pop("_hold_partial",None); save()

# ================= PHASE B: prewarm retry (reboot-reroll until all pass) =================
log("=== PHASE B: prewarm retry (reboot-reroll <=%d until all ge7>=%.2f), N=%d ===" % (MAX_ATTEMPTS,THRESH,N))
lockok_B=[]
for i in range(1,N+1):
    attempts=[]
    conv=False; meas=None
    for a in range(1,MAX_ATTEMPTS+1):
        meas=cold_probe()
        ge7={t:meas[t]["ge7"] for t in TAGS}
        victims=[t for t in TAGS if ge7[t]<THRESH]
        attempts.append({"attempt":a,"ge7":ge7,"victims":victims})
        if not victims:
            conv=True; break
    rec={"i":i,"attempts_used":len(attempts),"converged":conv,
         "final":{t:meas[t] for t in TAGS},"attempts":attempts}
    results["phase_B"]["cold_starts"].append(rec); save()
    log(f"  [B{i}] converged={conv} in {len(attempts)} attempt(s); final "+" ".join(f"{t}={meas[t]['ge7']:.0%}" for t in TAGS))
    if conv: lockok_B.append(i)
results["phase_B"]["lock_count"]=len(lockok_B)
log(f"  PHASE B: {len(lockok_B)}/{N} locked within {MAX_ATTEMPTS} attempts")
# persistence hold on a locked start
for _ in range(MAX_ATTEMPTS+2):
    meas=cold_probe()
    if is_good(meas): break
log("  PHASE B persistence hold (12min) on a locked start:")
results["phase_B"]["hold"]=hold_test(HOLD_S); results.pop("_hold_partial",None); save()

log("=== DONE Phase A+B ===")
save()
