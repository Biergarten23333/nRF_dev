#!/usr/bin/env python3
"""F3 unattended persistence/readback and safe-idle terminal."""

from __future__ import annotations

import argparse, hashlib, json, subprocess, sys, time
from datetime import datetime, timezone
from pathlib import Path

from async_line_channel import ThreadedLineChannel
from batch_g_control import relay_command_patient
from coldstart_fusion_control import decode_guard
from fusion_session import parse_fields, resolve_fusion_port
from pre_ramp_hardening import request_list
from capacity_ramp import RecordingAssembler
from relay8_tag_control import wait_master_status

MAP = (("BSF3C79",1),("BSFC2CC",2),("BSF44AD",3),("BSF6C53",4),("BSF8BC4",5),
       ("BSF1120",6),("BSF31CC",7),("BSFAA61",8),("BSFEC35",9),("BSFB165",10))
MARKER="tag-fusion-link-relay8.3-fix1"
IMAGE_HASH="b7395454e971aae771ec28aa469614c7bcbe5acef8f1d4f85f5852fcedc10530"

def now(): return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
def write(p,v): p.write_text(json.dumps(v,indent=2,sort_keys=True)+"\n")
def read(ch,node,cmd,prefix):
    return relay_command_patient(ch,node,cmd,prefix,attempts=1,reply_timeout_s=100.0 if node=="BSFB165" else 25.0)

def cfg_fields(ch,node):
    reply=read(ch,node,"CFG_STATUS","CFG ")
    return reply,parse_fields(reply["reply"]["text"])

def sha_index(root: Path):
    rows=[]
    for p in sorted(root.rglob("*")):
        if p.is_file() and p.name!="SHA256SUMS.json":
            rows.append({"path":str(p.relative_to(root)),"sha256":hashlib.sha256(p.read_bytes()).hexdigest(),"bytes":p.stat().st_size})
    write(root/"SHA256SUMS.json",rows)

def main():
    ap=argparse.ArgumentParser();ap.add_argument("root",type=Path);ap.add_argument("--registry",type=Path,required=True);a=ap.parse_args()
    a.root.mkdir(parents=True,exist_ok=False)
    reg=json.loads(a.registry.read_text()); expected={n:s for n,s in MAP}
    if reg.get("assignments")!=expected: raise RuntimeError("registry mismatch")
    state={"started":now(),"status":"IN_PROGRESS","nodes":{},"behavioral_rate_verification":"DEFERRED_TAG_MASTER_CONNECTED"}
    write(a.root/"state.json",state)
    ch=None
    with (a.root/"fusion_cdc.log").open("x",buffering=1) as log:
      try:
        ch=ThreadedLineChannel(resolve_fusion_port(None),log,"FUSION",decoded_queue_records=131072,backlog_red_records=16384,raw_backlog_red_bytes=16384,stall_red_s=1.0)
        ch.transport_mode="binary";ch.text_pending.clear()
        state["decode_before_send"]=decode_guard(ch,15.0);state["master_status"]=wait_master_status(ch)
        listing=request_list(ch,RecordingAssembler(),{},tuple(expected))
        if set(listing["peers"])!=set(expected): raise RuntimeError(f"fleet mismatch {listing['peers']}")
        state["preflight_list"]=listing;write(a.root/"state.json",state)
        for node,slot in MAP:
          row={"slot":slot,"started":now(),"status":"IN_PROGRESS","persist_write_attempts":0,"reboot_attempts":0}
          state["nodes"][node]=row;write(a.root/"state.json",state);print(f"{node} SLOT={slot} START",flush=True)
          try:
            version=read(ch,node,"VERSION","VERSION ");img=read(ch,node,"IMGSTAT","IMGSTAT ");before,bf=cfg_fields(ch,node)
            if f"fw={MARKER}" not in version["reply"]["text"]: raise RuntimeError("marker mismatch")
            if IMAGE_HASH not in img["reply"]["text"] or "confirmed=1" not in img["reply"]["text"]: raise RuntimeError("image mismatch")
            boot_before=int(bf["boot"],0);row["prewrite"]={"version":version,"imgstat":img,"cfg":before,"boot":boot_before,"resetreas":bf["resetreas"]}
            already=(bf.get("stored")=="1" and bf.get("pslot")==f"{slot}/12")
            if already:
              if node!="BSF3C79": raise RuntimeError(f"unexpected pre-existing persisted schedule {bf}")
              row["resume_classification"]="F2_ALREADY_COMPLETE_NO_REWRITE_NO_REBOOT"
            else:
              if bf.get("stored")!="0" or bf.get("state")!="UNPROVISIONED": raise RuntimeError(f"unsafe prewrite state {bf}")
              cmd=(f"CFG TAG={slot} SLOT={slot} COUNT=12 MASK=0x{1<<slot:04X} PERIOD=10 ACTIVE=9 EPOCH=5000 "
                   "BEACON_SYNC=1 BEACON_WIN_N=1 DW_ANCHOR=0 RUN=1 PMODE=0 PERSIST=1")
              row["persist_command"]=cmd;row["persist_write_attempts"]=1;row["persist_reply"]=read(ch,node,cmd,"CFG_PERSIST_OK ");write(a.root/"state.json",state)
              immediate,imf=cfg_fields(ch,node);row["immediate_readback"]=immediate
              if imf.get("stored")!="1" or imf.get("pslot")!=f"{slot}/12": raise RuntimeError(f"immediate NVS readback mismatch {imf}")
              row["reboot_attempts"]=1;row["reboot_reply"]=read(ch,node,"REBOOT","REBOOTING");write(a.root/"state.json",state)
              time.sleep(3)
            after,af=cfg_fields(ch,node);row["postboot_cfg_status"]=after
            required={"slot":f"{slot}/12","stored":"1","pslot":f"{slot}/12","pperiod":"10","psync":"1"}
            if any(af.get(k)!=v for k,v in required.items()) or af.get("state")=="UNPROVISIONED": raise RuntimeError(f"postboot readback mismatch {af}")
            boot_after=int(af["boot"],0)
            if not already and boot_after!=boot_before+1: raise RuntimeError(f"boot did not increment {boot_before}->{boot_after}")
            row.update(status="COMPLETE",ended=now(),boot_before=boot_before,boot_after=boot_after,resetreas=af["resetreas"],running_state=af["state"])
            if af["resetreas"]=="00000001": row["PROMINENT"]="fix1 commanded reboot returned RESETPIN"
            print(f"{node} COMPLETE boot={boot_after} resetreas={af['resetreas']}",flush=True)
          except Exception as e:
            row.update(status="QUARANTINED",ended=now(),error=f"{type(e).__name__}: {e}");print(f"{node} QUARANTINED {row['error']}",flush=True)
          write(a.root/"state.json",state)
        # Fleet readback, then volatile composed idle. Each result is checkpointed.
        slots=[]
        for node,slot in MAP:
          try:
            rb,f=cfg_fields(ch,node);state["nodes"][node]["fleet_readback"]=rb
            if f.get("stored")=="1" and f.get("pslot")==f"{slot}/12": slots.append(slot)
          except Exception as e: state["nodes"][node]["fleet_readback_error"]=f"{type(e).__name__}: {e}"
          write(a.root/"state.json",state)
        idle_dispatch=time.monotonic();last_uwb={n:idle_dispatch for n,_ in MAP}
        for node,slot in MAP:
          try:
            cmd=(f"CFG TAG={slot} SLOT={slot} COUNT=12 PERIOD=10 ACTIVE=9 EPOCH=5000 BEACON_SYNC=0 "
                 "BEACON_WIN_N=1 DW_ANCHOR=0 RUN=0 PMODE=3")
            state["nodes"][node]["idle"]={"command":cmd,"reply":read(ch,node,cmd,"CFG_OK "),"volatile":True}
          except Exception as e: state["nodes"][node]["idle"]={"error":f"{type(e).__name__}: {e}","volatile":True}
          write(a.root/"state.json",state)
        witness_start=time.monotonic();deadline=witness_start+120
        while time.monotonic()<deadline:
          line=ch.read(min(deadline,time.monotonic()+.5))
          if line and line.startswith("FUSION_UWB proto=7 name="):
            name=parse_fields(line).get("name")
            if name in last_uwb:last_uwb[name]=time.monotonic()
          if all(time.monotonic()-v>=90 for v in last_uwb.values()):break
        state["quiet_witness"]={n:time.monotonic()-t for n,t in last_uwb.items()}
        for n in last_uwb: state["nodes"][n]["idle_quiet_pass"]=state["quiet_witness"][n]>=90
        state["persisted_slot_set"]=sorted(slots);state["guard_slot_11_empty"]=(sorted(slots)==list(range(1,11)))
        state["fix1_resetreas_distribution"]={x:sum(r.get("resetreas")==x for r in state["nodes"].values()) for x in ("00000001","00000004")}
        state["status"]="PASS_SAFE_IDLE" if len(slots)==10 and all(r.get("idle_quiet_pass") for r in state["nodes"].values()) else "PARTIAL_SAFE_IDLE"
        state["ended"]=now();write(a.root/"state.json",state)
      finally:
        if ch: state["host_drain"]=ch.health_snapshot();ch.close();write(a.root/"state.json",state)
    beacon=a.root/"main_beacon_period_100.json"
    cp=subprocess.run([sys.executable,"B306_Part/tools/listener_vcom_command.py","--snr","760184545","--expected-marker","listener-beacon-main-v6.1","--command","BEACON_PERIOD 100","--output",str(beacon),"--post-seconds","8"],capture_output=True,text=True)
    state["main_beacon_restore"]={"returncode":cp.returncode,"evidence":str(beacon),"stdout":cp.stdout,"stderr":cp.stderr};write(a.root/"state.json",state)
    sha_index(a.root);print(json.dumps({"status":state["status"],"slots":state["persisted_slot_set"],"resetreas":state["fix1_resetreas_distribution"],"beacon_rc":cp.returncode},indent=2))
    return 0 if state["status"]=="PASS_SAFE_IDLE" and cp.returncode==0 else 2

if __name__=="__main__": raise SystemExit(main())
