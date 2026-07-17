#!/usr/bin/env python3
"""ONE COMMAND: cold boot -> distinct-slot TDMA -> pre-demo GO/NO-GO check.
Same command is the recovery (clears stuck-0/bad phase). Master_Tag CDC /dev/ttyACM0.
Usage: python3 demo_start.py            (default; reboots + applies + checks)
       python3 demo_start.py --check    (check only; read per-tag ge7, no reboot)"""
import serial, time, sys
from collections import defaultdict, Counter
TAGS=["BS9336","BS955A","BSCCF4"]; GOOD=0.90
def cmd(c,secs=2.0):
    s=serial.Serial(); s.port="/dev/ttyACM0"; s.baudrate=115200
    s.dtr=False;s.rts=False;s.timeout=0.2;s.open();time.sleep(0.1);s.reset_input_buffer()
    s.write((c+"\n").encode());s.flush()
    t0=time.time();buf=b""
    while time.time()-t0<secs:
        x=s.read(4096);buf+=x if x else b""
    s.close();return buf.decode(errors="replace")
def popc(h):
    try: return bin(int(h,16)).count("1")
    except: return 0
def check(secs=30):
    out=cmd("",0.1)  # noop open
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
    g={t:(per[t]["ge7"]/per[t]["n"] if per[t]["n"] else 0.0) for t in TAGS}
    go=all(g[t]>=GOOD for t in TAGS) and all(per[t]["n"]>50 for t in TAGS)
    print("  per-tag ge7: "+"  ".join(f"{t}={g[t]:.0%}(n{per[t]['n']})" for t in TAGS))
    print("  ===> "+("GO ✓ (all 3 tags >=90%)" if go else "NO-GO ✗ (re-run demo_start.py)"))
    return go
if "--check" in sys.argv:
    print("[pre-demo check, no reboot]"); check(); sys.exit(0)
print("[demo_start] 1/3 cold reboot all tags ..."); cmd("cmd_all REBOOT",2.5); time.sleep(16)
print("[demo_start] 2/3 distinct-slot roster + auto ...")
for t in TAGS: cmd(f"tdma roster {t} motion",1.2)
o=cmd("tdma auto 1",3.0)
print("  applied: "+("all 3 CFG_OK LIVE=1" if o.count("LIVE=1")>=3 else f"WARNING only {o.count('LIVE=1')}/3 LIVE=1")); time.sleep(4)
print("[demo_start] 3/3 pre-demo check (30s):"); check(30)
