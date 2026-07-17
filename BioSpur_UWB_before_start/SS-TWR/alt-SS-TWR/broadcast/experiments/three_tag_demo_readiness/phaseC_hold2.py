#!/usr/bin/env python3
"""Kill-resilient persistence hold: apply distinct-slot ONCE (first run), then measure the SAME
running session in 60s bins, accumulating elapsed-since-apply across restarts. Target 15 min."""
import serial, time, json, os
from collections import defaultdict
OUT="/mnt/nrf_ssd/nRF_dev/BioSpur_UWB_before_start/SS-TWR/alt-SS-TWR/broadcast/experiments/three_tag_demo_readiness"
STATE=os.path.join(OUT,"hold_state.json")
TAGS=["BS9336","BS955A","BSCCF4"]; GOOD_ALL=0.90; SETTLE=16; TARGET=900; BIN=60
res=json.load(open(os.path.join(OUT,"results.json")))
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
def measure(secs):
    m=serial.Serial(); m.port="/dev/ttyACM0"; m.baudrate=115200
    m.dtr=False;m.rts=False;m.timeout=0.2;m.open();time.sleep(0.1);m.reset_input_buffer()
    t0=time.time();buf=b""
    while time.time()-t0<secs:
        x=m.read(8192);buf+=x if x else b""
    m.close();per=defaultdict(lambda:{"n":0,"ge7":0})
    for ln in buf.decode(errors="replace").splitlines():
        if "notify: TR;" not in ln: continue
        nm=ln.split(" notify:")[0].split()[-1]
        if nm not in TAGS: continue
        p=ln.split("notify: ",1)[1].split(";")
        if len(p)<8 or p[0]!="TR": continue
        d=per[nm];d["n"]+=1
        if popc(p[6])>=7:d["ge7"]+=1
    return {t:(round(per[t]["ge7"]/per[t]["n"],3) if per[t]["n"] else 0.0) for t in TAGS}
if not os.path.exists(STATE):
    log("=== fresh apply (reboot -> tdma auto) ===")
    cmd("cmd_all REBOOT",2.5); time.sleep(SETTLE)
    for t in TAGS: cmd(f"tdma roster {t} motion",1.2)
    cmd("tdma auto 1",3.0); time.sleep(4)
    start=time.time(); json.dump({"start":start},open(STATE,"w"))
    res.setdefault("phase_C",{})["hold"]=[]; res["phase_C"]["hold_note"]="kill-resilient continuous session"; save()
else:
    start=json.load(open(STATE))["start"]; log(f"=== resume hold, session elapsed {int(time.time()-start)}s ===")
hold=res.setdefault("phase_C",{}).setdefault("hold",[])
while time.time()-start<TARGET:
    g=measure(BIN); el=int(time.time()-start); ok=all(g[t]>=GOOD_ALL for t in TAGS)
    hold.append({"t":el,**g,"ok":ok}); save()
    log(f"  t={el}s "+" ".join(f"{t}={g[t]:.0%}" for t in TAGS)+f" ok={ok}")
oks=sum(1 for b in hold if b["ok"])
res["phase_C"]["hold_ok_frac"]=round(oks/len(hold),3) if hold else 0
res["phase_C"]["hold_span_s"]=hold[-1]["t"] if hold else 0; save()
log(f"=== HOLD reached {hold[-1]['t']}s: {oks}/{len(hold)} bins all-good ===")
