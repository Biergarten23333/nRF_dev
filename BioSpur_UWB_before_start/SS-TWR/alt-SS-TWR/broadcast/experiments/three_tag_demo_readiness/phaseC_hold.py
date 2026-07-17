#!/usr/bin/env python3
"""Phase C persistence: reboot -> apply distinct-slot ONCE -> hold 13min, measure ge7 in 60s bins."""
import serial, time, json, os
from collections import defaultdict
OUT="/mnt/nrf_ssd/nRF_dev/BioSpur_UWB_before_start/SS-TWR/alt-SS-TWR/broadcast/experiments/three_tag_demo_readiness"
TAGS=["BS9336","BS955A","BSCCF4"]; GOOD_ALL=0.90; SETTLE=16; HOLD_S=780; HOLD_BIN=60
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
def apply_distinct():
    for t in TAGS: cmd(f"tdma roster {t} motion",1.2)
    return cmd("tdma auto 1",3.0)
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
log("=== PHASE C persistence hold (distinct-slot, 13min) ===")
cmd("cmd_all REBOOT",2.5); time.sleep(SETTLE); apply_distinct(); time.sleep(4)
bins=[]; t0=time.time(); dips=0
while time.time()-t0<HOLD_S:
    g=measure(HOLD_BIN); ok=all(g[t]>=GOOD_ALL for t in TAGS)
    if not ok: dips+=1
    row={"t":int(time.time()-t0),**g,"ok":ok}; bins.append(row)
    res.setdefault("phase_C",{})["hold"]=bins; res["phase_C"]["hold_dips"]=dips; save()
    log(f"  t={row['t']}s "+" ".join(f"{t}={g[t]:.0%}" for t in TAGS)+f" ok={ok}")
ok_bins=sum(1 for b in bins if b["ok"])
res["phase_C"]["hold_ok_frac"]=round(ok_bins/len(bins),3); save()
log(f"=== HOLD: {ok_bins}/{len(bins)} bins all-3-good ({dips} dips) ===")
