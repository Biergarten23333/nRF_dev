#!/usr/bin/env python3
"""Read-only per-node v47 cold-power persistence audit."""
from __future__ import annotations
import argparse, json, time
from datetime import datetime, timezone
from pathlib import Path
from async_line_channel import ThreadedLineChannel
from capacity_ramp import b306_command
from coldstart_fusion_control import decode_guard
from fusion_session import parse_fields, resolve_fusion_port

MARKER = "b306-imu-relay-v47"
FWID = "f7436728c36efdd28f848e7ef59c7c422437afb8c6ee07dd8924e31967046eed"
IMAGE = "90ef063b227feb4c70499cc186df866c24da658fba98773eacc40da73a0abf98"

def now(): return datetime.now(timezone.utc).astimezone().isoformat()

def main() -> int:
    ap=argparse.ArgumentParser();ap.add_argument("--out-dir",type=Path,required=True)
    ap.add_argument("--node",required=True)
    ap.add_argument("--observe-s",type=float,default=20)
    ap.add_argument("--max-uptime-ms",type=int,default=600_000)
    a=ap.parse_args();a.out_dir.mkdir(parents=True,exist_ok=False)
    node=a.node
    result={"schema":"biospur-v47-power-persistence-v1","started":now(),"node":node,"status":"IN_PROGRESS"}
    ch=None
    with (a.out_dir/"fusion_cdc.log").open("x",encoding="utf-8",buffering=1) as log:
      try:
        ch=ThreadedLineChannel(resolve_fusion_port(None),log,"FUSION",decoded_queue_records=131072,
          backlog_red_records=16384,raw_backlog_red_bytes=16384,stall_red_s=2)
        ch.transport_mode="binary";ch.text_pending.clear();result["guard"]=decode_guard(ch,20)
        result["ping"]=b306_command(ch,node,"PING","PONG ")
        result["status_reply"]=b306_command(ch,node,"STATUS","STATUS ")
        result["confirm_reply"]=b306_command(ch,node,"BOOT CONFIRM STATUS","BOOT CONFIRM STATUS ")
        result["ring_reply"]=b306_command(ch,node,"RING STATUS","RING ")
        result["corpse_reply"]=b306_command(ch,node,"CORPSE STATUS","CORPSE ")
        pf=parse_fields(str(result["ping"]["text"]));sf=parse_fields(str(result["status_reply"]["text"]));cf=parse_fields(str(result["confirm_reply"]["text"]))
        rf=parse_fields(str(result["ring_reply"]["text"]));cpf=parse_fields(str(result["corpse_reply"]["text"]))
        result["parsed"]={"ping":pf,"status":sf,"confirm":cf,"ring":rf,"corpse":cpf}
        counts={"uwb":0,"imu":0,"telemetry":0}; deadline=time.monotonic()+a.observe_s
        while time.monotonic()<deadline:
          line=ch.read(deadline)
          if not line or parse_fields(line).get("name")!=node: continue
          if line.startswith("FUSION_UWB "):counts["uwb"]+=1
          elif line.startswith("FUSION_IMU "):counts["imu"]+=1
          elif line.startswith("FUSION_TELEMETRY "):counts["telemetry"]+=1
        result["stream_counts"]=counts
        failures=[]
        if pf.get("name")!=node:failures.append("wrong node")
        if pf.get("fw")!=MARKER:failures.append("wrong marker")
        if pf.get("fwid")!=FWID:failures.append("wrong FWID")
        if pf.get("image_sha")!=IMAGE:failures.append("wrong active image SHA")
        if cf.get("confirmed")!="1":failures.append("not confirmed")
        if rf.get("init")!="cold":failures.append("init is not cold")
        if cpf.get("reboot_owner")!="0":failures.append("reboot_owner is not zero")
        try:
          uptime_ms=int(sf.get("up_ms","-1"),0)
          result["uptime_ms"]=uptime_ms
          if uptime_ms<0:failures.append("uptime unavailable")
          elif uptime_ms>a.max_uptime_ms:failures.append("uptime does not prove fresh power cycle")
        except ValueError:failures.append("uptime malformed")
        if any(counts[k]<=0 for k in counts):failures.append("missing passive stream")
        result["failures"]=failures;result["status"]="PASS" if not failures else "BLOCKED"
        return 0 if not failures else 2
      except Exception as e:
        result["status"]="BLOCKED";result["error"]=f"{type(e).__name__}: {e}";return 2
      finally:
        if ch:result["host_health"]=ch.health_snapshot();ch.close()
        result["ended"]=now();(a.out_dir/"result.json").write_text(json.dumps(result,indent=2,sort_keys=True)+"\n")
if __name__=="__main__":raise SystemExit(main())
