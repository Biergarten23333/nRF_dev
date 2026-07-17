#!/usr/bin/env python3
"""Resume Phase C cold starts to N=10 (preserves existing), then STOP (hold runs separately)."""
import serial, time, json, os
from collections import defaultdict, Counter
OUT="/mnt/nrf_ssd/nRF_dev/BioSpur_UWB_before_start/SS-TWR/alt-SS-TWR/broadcast/experiments/three_tag_demo_readiness"
TAGS=["BS9336","BS955A","BSCCF4"]; GOOD_ALL=0.90; SETTLE=16; MEAS=30; N=10
res=json.load(open(os.path.join(OUT,"results.json")))
pc_=res.setdefault("phase_C",{"entry":"reboot -> tdma roster x3 -> tdma auto 1 -> verify","cold_starts":[]})
def save(): json.dump(res,open(os.path.join(OUT,"results.json"),"w"),indent=2)
def log(m): print(m,flush=True)
def popc(h):
    try: return bin(int(h,16)).count("1")
    except: return 0
def cmd(c,secs=2.0):
    s=serial.Serial(); s.port="/dev/ttyACM0"; s.baudrate=115200
    s.dtr=False;s.rts=False;s.timeout=0.2;s.open();time.sleep(0.1);s.reset_input_buffer()
    s.write((c+"\n").encode());s.flush()
    t0=time.time();buf=b""
    while time.time()-t0<secs:
        x=s.read(4096);buf+=x if x else b""
    s.close();return buf.decode(errors="replace")
def apply_distinct():
    for t in TAGS: cmd(f"tdma roster {t} motion",1.2)
    out=cmd("tdma auto 1",3.0)
    return {"live_ok":out.count("LIVE=1"),"cfg_ok":out.count("CFG_OK TAG=")}
def measure(secs):
    m=serial.Serial(); m.port="/dev/ttyACM0"; m.baudrate=115200
    m.dtr=False;m.rts=False;m.timeout=0.2;m.open();time.sleep(0.1);m.reset_input_buffer()
    t0=time.time();buf=b""
    while time.time()-t0<secs:
        x=m.read(8192);buf+=x if x else b""
    m.close();per=defaultdict(lambda:{"n":0,"ge7":0,"ge8":0,"raw":0})
    for ln in buf.decode(errors="replace").splitlines():
        if "notify: TR;" not in ln: continue
        nm=ln.split(" notify:")[0].split()[-1]
        if nm not in TAGS: continue
        p=ln.split("notify: ",1)[1].split(";")
        if len(p)<8 or p[0]!="TR": continue
        d=per[nm];d["n"]+=1;v=popc(p[6])
        if v>=7:d["ge7"]+=1
        if v>=8:d["ge8"]+=1
        if any(x.isdigit() and int(x)>0 for x in p[7].split(",")): d["raw"]+=1
    o={}
    for t in TAGS:
        d=per[t];n=d["n"]
        o[t]={"n":n,"ge7":round(d["ge7"]/n,3) if n else 0.0,"ge8":round(d["ge8"]/n,3) if n else 0.0,
              "raw_frac":round(d["raw"]/n,3) if n else 0.0,"hz":round(n/secs,2)}
    return o
def geiger(secs=5):
    g=serial.Serial();g.port="/dev/ttyACM6";g.baudrate=460800
    g.dtr=False;g.rts=False;g.timeout=0.2;g.open();time.sleep(0.1);g.reset_input_buffer()
    t0=time.time();buf=b""
    while time.time()-t0<secs:
        x=g.read(8192);buf+=x if x else b""
    g.close();p=Counter()
    for ln in buf.decode(errors="replace").splitlines():
        f=ln.split(";")
        if len(f)>=7 and f[5]=="0xffff" and f[4].startswith("0xb"): p[f[4]]+=1
    return dict(p)
def good(m): return all(m[t]["ge7"]>=GOOD_ALL for t in TAGS)
start=len(pc_["cold_starts"])+1
log(f"=== PHASE C resume from C{start} to C{N} ===")
for i in range(start,N+1):
    cmd("cmd_all REBOOT",2.5); time.sleep(SETTLE)
    ap=apply_distinct(); time.sleep(4)
    m=measure(MEAS); g=geiger(5); gd=good(m)
    pc_["cold_starts"].append({"i":i,"apply":ap,"tags":m,"geiger":g,"good":gd}); save()
    log(f"  [C{i}] "+" ".join(f"{t}={m[t]['ge7']:.0%}" for t in TAGS)+f" | live={ap['live_ok']} pollers={g} GOOD={gd}")
pc_["good_count"]=sum(1 for c in pc_["cold_starts"] if c["good"]); save()
log(f"=== PHASE C cold starts: {pc_['good_count']}/{len(pc_['cold_starts'])} good ==="); save()
