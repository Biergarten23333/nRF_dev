#!/usr/bin/env python3
"""Reboot-only ten-board OTA confirmation timing rehearsal (never writes slots)."""

from __future__ import annotations
import argparse, json, math, statistics, time
from datetime import datetime, timezone
from pathlib import Path

from async_line_channel import ThreadedLineChannel
from capacity_ramp import b306_command
from coldstart_fusion_control import decode_guard
from confirm_b306_v32 import wait_master_status
from fusion_session import SessionError, parse_fields, resolve_fusion_port

NODES=("BSF3C79","BSFC2CC","BSF44AD","BSF6C53","BSF8BC4",
       "BSF1120","BSF31CC","BSFAA61","BSFEC35","BSFB165")
RETRYABLE=("bridge_not_ready","not_connected","reason=syntax","truncated")

def percentile95(values):
    ordered=sorted(values); rank=.95*(len(ordered)-1); low=int(rank); fraction=rank-low
    return ordered[low]+fraction*(ordered[min(low+1,len(ordered)-1)]-ordered[low])

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--out-dir",required=True,type=Path)
    p.add_argument("--master-marker",required=True)
    p.add_argument("--fusion-port")
    p.add_argument("--per-node-timeout-s",type=float,default=60)
    p.add_argument("--ready-timeout-s",type=float,default=180)
    p.add_argument("--restore-max-s",type=float,required=True,
                   help="archived production-master restore maximum")
    p.add_argument("--prepare-confirm-max-s",type=float,required=True,
                   help="archived PREPARE through confirmed=1 maximum")
    a=p.parse_args(); a.out_dir.mkdir(parents=True,exist_ok=False)
    result={"schema":"biospur-ota-timing-rehearsal-v1","status":"IN_PROGRESS",
            "started":datetime.now(timezone.utc).astimezone().isoformat(),"samples":[]}
    channel=None
    with (a.out_dir/"fusion_cdc.log").open("x",encoding="utf-8",buffering=1) as log:
      try:
        channel=ThreadedLineChannel(resolve_fusion_port(a.fusion_port),log,"FUSION",
            decoded_queue_records=65536,backlog_red_records=8192,
            raw_backlog_red_bytes=8192,stall_red_s=1)
        channel.transport_mode="binary"; channel.text_pending.clear()
        result["port"]=channel.port
        result["decode_before_send"]=decode_guard(channel,15)
        master=wait_master_status(channel); result["master_status"]=master
        if f"marker={a.master_marker}" not in master: raise SessionError(f"master mismatch: {master}")
        ready_deadline=time.monotonic()+a.ready_timeout_s
        while "ready=10" not in master:
          if time.monotonic()>=ready_deadline: raise SessionError(f"fleet not ready: {master}")
          time.sleep(1); master=wait_master_status(channel)
        result["fleet_ready_status"]=master
        for node in NODES:
          before=b306_command(channel,node,"PING","PONG "); before_fields=parse_fields(str(before["text"]))
          if before_fields.get("name")!=node: raise SessionError(f"pre-reboot wrong node: {before}")
          # T0 is before the only mutating command. T1/T2 equal T0 because the
          # production master and decoded CDC are deliberately already ready;
          # archived restore time is added separately to the conservative sum.
          t0=time.monotonic(); t1=t0; t2=t0
          reboot=b306_command(channel,node,"REBOOT","REBOOT QUEUED ")
          deadline=t0+a.per_node_timeout_s; after=None; errors=[]; disconnect_observed=False
          while time.monotonic()<deadline:
            try:
              candidate=b306_command(channel,node,"PING","PONG ")
              f=parse_fields(str(candidate["text"]))
              if f.get("name")==node and disconnect_observed:
                after=candidate; break
            except Exception as exc:
              errors.append(f"{type(exc).__name__}: {exc}")
              if any(token in str(exc) for token in RETRYABLE): disconnect_observed=True
            time.sleep(.25)
          if after is None: raise SessionError(f"{node} did not prove fresh reboot: {errors[-3:]}")
          t3=time.monotonic(); status=b306_command(channel,node,"BOOT CONFIRM STATUS","BOOT CONFIRM STATUS "); t4=time.monotonic()
          if "confirmed=1" not in str(status["text"]): raise SessionError(f"{node} lost confirmation: {status}")
          result["samples"].append({"node":node,"t0":t0,"t1":t1,"t2":t2,"t3":t3,"t4":t4,
            "reboot_reply":reboot,"pong":after,"confirm_status":status,
            "components_s":{"master_restore_live":t1-t0,"cdc_ready_live":t2-t1,
              "route_to_pong":t3-t2,"status":t4-t3,"reboot_to_status":t4-t0},
            "retry_errors":errors})
        totals=[s["components_s"]["reboot_to_status"] for s in result["samples"]]
        route=[s["components_s"]["route_to_pong"] for s in result["samples"]]
        status_times=[s["components_s"]["status"] for s in result["samples"]]
        upper=a.restore_max_s+max(route)+max(status_times)+a.prepare_confirm_max_s
        margin=max(30.0,.25*upper)
        result["summary"]={"count":len(totals),"reboot_to_status_max_s":max(totals),
          "reboot_to_status_p95_s":percentile95(totals),"component_max_s":{
            "archived_master_restore":a.restore_max_s,"route_to_pong":max(route),
            "status":max(status_times),"archived_prepare_to_confirm":a.prepare_confirm_max_s},
          "conservative_upper_s":upper,"margin_policy":"max(30 seconds, 25%)",
          "margin_s":margin,"upper_plus_margin_s":upper+margin,
          "gate":"PASS" if upper+margin<180 else "BLOCKED"}
        result["status"]=result["summary"]["gate"]
        return 0 if result["status"]=="PASS" else 2
      except Exception as exc:
        result["status"]="BLOCKED"; result["error"]=f"{type(exc).__name__}: {exc}"; return 2
      finally:
        if channel: channel.close()
        result["ended"]=datetime.now(timezone.utc).astimezone().isoformat()
        (a.out_dir/"result.json").write_text(json.dumps(result,indent=2,sort_keys=True)+"\n")

if __name__=="__main__": raise SystemExit(main())
