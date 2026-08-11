#!/usr/bin/env python3
"""One-open BSFC2CC warm-up, CDC catch-up and formal stationary capture."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import shutil
import signal
import subprocess
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

from async_line_channel import ThreadedLineChannel
from fusion_session import parse_fields, parse_reply, resolve_fusion_port
from v47_c2cc_stationary_capture import (
    FWID, GEOMETRY, IMAGE, MARKER, MASTER, NODE, S2_CODE, S2_MANIFEST,
    anchor_preflight, start_listener, stop_listener,
)


ROOT=Path(__file__).resolve().parents[2]


def wall(): return datetime.now(timezone.utc).astimezone().isoformat(timespec="milliseconds")
def sha(path):
    h=hashlib.sha256()
    with Path(path).open("rb") as f:
        for block in iter(lambda:f.read(4<<20),b""):h.update(block)
    return h.hexdigest()
def atomic(path,value):
    tmp=path.with_name(path.name+".tmp");tmp.write_text(json.dumps(value,indent=2,sort_keys=True)+"\n",encoding="utf-8");os.replace(tmp,path)


def robust_sigma(values):
    if not values:return 0.0
    ordered=sorted(values);med=ordered[len(ordered)//2]
    dev=sorted(abs(x-med) for x in values)
    return 1.4826*dev[len(dev)//2]

def formal_start_disposition(elapsed_s,stable_seconds,required_seconds,max_warmup_s):
    if stable_seconds>=required_seconds:return "LIVE_CATCHUP_OBSERVED"
    if elapsed_s>=max_warmup_s:return "STARTED_DEGRADED"
    return None

def lifecycle_is_single_timeline(phases,manifest):
    ordered=[phases.get(x) for x in ("COLLECTOR_OPEN","RAW_RECORDING_FROM_FIRST_BYTE","FORMAL_T0","CLEAN_STOP")]
    return (manifest.get("serial_open_count")==1 and phases.get("one_raw_file") is True and
            phases.get("WARMUP_RECORDING")==phases.get("COLLECTOR_OPEN") and
            all(x is not None for x in ordered) and ordered==sorted(ordered))


class LiveCatchupDetector:
    """Data-derived offset plateau plus cadence/queue/sequence evidence."""
    def __init__(self):
        self.seconds=deque(maxlen=40);self.stable_seconds=0;self.first_stable=None
    def update(self,row):
        self.seconds.append(dict(row));ok=False;detail={"enough_history":len(self.seconds)>=10}
        if len(self.seconds)>=10:
            recent=list(self.seconds)[-10:];offsets=[x["age_offset_median_ms"] for x in recent if x["age_offset_median_ms"] is not None]
            diffs=[abs(b-a) for a,b in zip(offsets,offsets[1:])];sigma=robust_sigma(diffs)
            left=offsets[:5];right=offsets[-5:];shift=abs(sorted(left)[len(left)//2]-sorted(right)[len(right)//2]) if left and right else math.inf
            data_threshold=max(6*sigma,max(diffs) if diffs else 0.0,1e-9)
            cadence=all(185<=x["imu_hz"]<=215 and 7<=x["uwb_hz"]<=10 for x in recent[-5:])
            queues=all(x["decoded_queue_depth"]==0 and x["raw_queue_depth"]<=1 and x["serial_input_bytes"] in (0,1) for x in recent[-5:])
            continuous=all(x["imu_gap_events"]==0 and x["uwb_gap_events"]==0 and not x["timestamp_jump"] for x in recent)
            plateau=len(offsets)==10 and shift<=data_threshold
            ok=cadence and queues and continuous and plateau
            detail.update(offset_shift_ms=shift,offset_stability_threshold_ms=data_threshold,
                          offset_difference_robust_sigma_ms=sigma,cadence=cadence,queues=queues,
                          sequences_continuous=continuous,source_age_plateau=plateau)
        self.stable_seconds=self.stable_seconds+1 if ok else 0
        if ok and self.first_stable is None:self.first_stable=row["end_monotonic"]
        return ok,{**detail,"stable_seconds":self.stable_seconds}


def classify_observation(lines):
    master_rows=[];list_rows=[];peers=[];pong={};confirm={}
    for line in lines:
        fields=parse_fields(line)
        if line.startswith("FUSION_MASTER_STATUS "):master_rows.append(fields)
        elif line.startswith("FUSION_LIST "):list_rows.append(fields)
        elif line.startswith("FUSION_PEER "):peers.append(fields)
        reply=parse_reply(line)
        if reply:
            text=parse_fields(reply.text)
            if reply.text.startswith("PONG "):pong=text
            elif reply.text.startswith("BOOT CONFIRM STATUS "):confirm=text
    checks={"master":bool(master_rows) and master_rows[-1].get("marker")==MASTER,
      "membership":bool(list_rows) and list_rows[-1].get("count")=="1" and [x.get("name") for x in peers]==[NODE],
      "peer_ready":len(peers)==1 and peers[0].get("connected")==peers[0].get("subscribed")=="1",
      "identity":all(pong.get(k)==v for k,v in {"name":NODE,"fw":MARKER,"fwid":FWID,"image_sha":IMAGE}.items()),
      "confirmed":confirm.get("confirmed")=="1"}
    status="OBSERVED_EXPECTED" if all(checks.values()) else "OBSERVED_UNEXPECTED" if any((master_rows,list_rows,peers,pong,confirm)) else "UNAVAILABLE"
    return {"status":status,"checks":checks,"master":master_rows[-1] if master_rows else {},"list":list_rows[-1] if list_rows else {},"peers":peers,"pong":pong,"confirm":confirm}


def capture(root:Path,duration:float,min_warmup:float,max_warmup:float,stable_required:int):
    continuous=root/"continuous_raw";warm=root/"warmup";formal=root/"formal";analysis=root/"analysis"
    for p in (continuous,warm,formal,analysis):p.mkdir(parents=True,exist_ok=False)
    frozen_copy=root/"FROZEN_S2_PARAMETER_MANIFEST.json";shutil.copyfile(S2_MANIFEST,frozen_copy)
    status=subprocess.check_output(["git","status","--porcelain=v1","-z"],cwd=ROOT)
    frozen={"git_head":subprocess.check_output(["git","rev-parse","HEAD"],cwd=ROOT,text=True).strip(),"source_status_sha256":hashlib.sha256(status).hexdigest(),
      "geometry":{"path":str(GEOMETRY.relative_to(ROOT)),"sha256":sha(GEOMETRY)},"s2_parameter_manifest":{"copy":"FROZEN_S2_PARAMETER_MANIFEST.json","sha256":sha(frozen_copy)},
      "s2_code":{"path":str(S2_CODE.relative_to(ROOT)),"sha256":sha(S2_CODE)},"capture_tool_sha256":sha(Path(__file__)),"recorded_wall":wall(),"recorded_monotonic":time.monotonic(),"storage_free_bytes":shutil.disk_usage(root).free}
    atomic(root/"FROZEN_INPUT_HASHES.json",frozen)
    stop=False
    def sig(_s,_f):
        nonlocal stop;stop=True
    signal.signal(signal.SIGINT,sig);signal.signal(signal.SIGTERM,sig)
    listener_proc,listener_log=start_listener(continuous/"listener_capture",duration+max_warmup+120)
    cdc=(continuous/"fusion_cdc.log").open("x",encoding="utf-8",buffering=1);raw=(continuous/"fusion_host_raw.cobs.bin").open("xb",buffering=0);idx=(continuous/"consumption_index.jsonl").open("x",encoding="utf-8",buffering=1)
    opened=time.monotonic();opened_wall=wall();print("CAPTURE_OPENED",flush=True)
    ledger={"status":"RUNNING","collector_open_monotonic":opened,"collector_open_wall":opened_wall,"serial_open_count":0,"commands":[],"events":[],"phase":"COLLECTOR_OPEN"}
    ch=None;last_imu=last_uwb=None;last_imu_n=0;last_values={};all_observation=[];seconds=[];detector=LiveCatchupDetector();formal_marker=None
    try:
        port=resolve_fusion_port(None);ch=ThreadedLineChannel(port,cdc,"FUSION",decoded_queue_records=1048576,backlog_red_records=131072,raw_backlog_red_bytes=131072,stall_red_s=2,raw_file=raw);ledger["serial_open_count"]=1;ch.transport_mode="binary";ch.text_pending.clear();print("WARMUP_RECORDING",flush=True)
        first_valid=None;deadline=time.monotonic()+15
        while time.monotonic()<deadline and first_valid is None:
            line=ch.read(deadline)
            if line and line.startswith("FUSION_"):first_valid=line
        ledger["first_decoded_line"]=first_valid;ledger["first_byte_monotonic"]=ch.health_snapshot()["first_raw_monotonic"]
        if first_valid:
            for command in ("MASTER STATUS","LIST",f"{NODE} PING",f"{NODE} BOOT CONFIRM STATUS"):
                ch.send(command);ledger["commands"].append({"command":command,"monotonic":time.monotonic(),"classification":"READ_ONLY_WARMUP"})
            until=time.monotonic()+5
            while time.monotonic()<until:
                line=ch.read(until)
                if line:all_observation.append(line)
        try:ledger["anchor_observation"]=anchor_preflight(warm)
        except Exception as exc:ledger["anchor_observation"]={"status":"OBSERVED_DEGRADED","error":f"{type(exc).__name__}: {exc}"}
        ledger["identity_observation"]=classify_observation(all_observation)
        phase_start=time.monotonic();next_second=math.floor(phase_start)+1;bucket=[];record_index=0;print("CDC_BACKLOG_OBSERVED",flush=True)
        while not stop and formal_marker is None:
            now=time.monotonic();line=ch.read(min(next_second,now+.10));now=time.monotonic()
            if line:
                record_index+=1;fields=parse_fields(line);entry={"record_index":record_index,"consume_monotonic":now,"raw_bytes_submitted":ch.health_snapshot()["raw_bytes_submitted"],"line":line};idx.write(json.dumps(entry,separators=(",",":"))+"\n")
                if fields.get("name")==NODE and line.startswith("FUSION_IMU "):
                    seq=int(fields["seq"],0);n=int(fields["n"],0);base=int(fields["base_us"],0);master=int(fields["master_ms"],0);gap=0 if last_imu is None or seq==((last_imu+last_imu_n)&0xffff) else 1;last_imu,last_imu_n=seq,n;last_values.update(imu_seq=seq,imu_n=n,imu_base_us=base,imu_master_ms=master);bucket.append(("imu",n,gap,now*1000-master,base))
                elif fields.get("name")==NODE and line.startswith("FUSION_UWB "):
                    sweep=int(fields["sweep"],0);strobe=int(fields["strobe_us"],0);master=int(fields["master_ms"],0);gap=0 if last_uwb is None or sweep==((last_uwb+1)&0xffffffff) else 1;last_uwb=sweep;last_values.update(uwb_sweep=sweep,uwb_strobe_us=strobe,uwb_master_ms=master);bucket.append(("uwb",1,gap,now*1000-master,strobe))
                if line.startswith(("FUSION_CONNECTED ","FUSION_DISCONNECTED ")) or "RESET" in line:ledger["events"].append({"monotonic":now,"line":line})
            if now>=next_second:
                health=ch.health_snapshot();imu=[x for x in bucket if x[0]=="imu"];uwb=[x for x in bucket if x[0]=="uwb"];offsets=[x[3] for x in bucket]
                row={"start_monotonic":next_second-1,"end_monotonic":next_second,"imu_hz":sum(x[1] for x in imu),"uwb_hz":len(uwb),"imu_gap_events":sum(x[2] for x in imu),"uwb_gap_events":sum(x[2] for x in uwb),"age_offset_median_ms":sorted(offsets)[len(offsets)//2] if offsets else None,"decoded_queue_depth":health["decoded_queue_depth"],"raw_queue_depth":health["raw_queue_depth"],"serial_input_bytes":health["serial_input_bytes"],"raw_bytes_submitted":health["raw_bytes_submitted"],"timestamp_jump":False}
                ok,detail=detector.update(row);row["live_evidence"]=detail;seconds.append(row);bucket=[];elapsed=next_second-opened
                if detector.stable_seconds==stable_required:print("LIVE_CATCHUP_OBSERVED",flush=True);ledger["live_catchup_monotonic"]=next_second-stable_required+1
                disposition=formal_start_disposition(elapsed,detector.stable_seconds,stable_required,max_warmup)
                degraded=disposition=="STARTED_DEGRADED"
                if elapsed>=min_warmup and disposition:
                    health=ch.health_snapshot();formal_marker={"wall":wall(),"monotonic":time.monotonic(),"raw_byte_offset":health["raw_bytes_submitted"],"decoded_record_index":health["decoded_records"],"consumed_record_index":record_index,"stream_values":dict(last_values),"health":health,"source_age_offset_median_ms":row["age_offset_median_ms"],"live_catchup":"STARTED_DEGRADED" if degraded else "LIVE_CATCHUP_OBSERVED"}
                    print("STARTED_DEGRADED" if degraded else "FORMAL_T0",flush=True)
                next_second+=1
        atomic(warm/"SECONDLY_EVIDENCE.json",seconds)
        atomic(root/"CDC_LIVE_CATCHUP.json",{"schema":"biospur-cdc-live-catchup-v1","estimator":"host_consume_monotonic_ms-master_receipt_ms; absolute offset is arbitrary; plateau threshold is derived from six robust sigmas and observed first differences","seconds":seconds,"formal_marker":formal_marker})
        if formal_marker and not stop:
            formal_end=formal_marker["monotonic"]+duration;last_values_t0=dict(last_values);formal_events=[]
            while not stop and time.monotonic()<formal_end:
                line=ch.read(min(formal_end,time.monotonic()+.10));now=time.monotonic()
                if not line:continue
                record_index+=1;fields=parse_fields(line);idx.write(json.dumps({"record_index":record_index,"consume_monotonic":now,"raw_bytes_submitted":ch.health_snapshot()["raw_bytes_submitted"],"line":line},separators=(",",":"))+"\n")
                if fields.get("name")==NODE and line.startswith("FUSION_IMU "):last_values.update(imu_seq=int(fields["seq"],0),imu_n=int(fields["n"],0),imu_base_us=int(fields["base_us"],0),imu_master_ms=int(fields["master_ms"],0))
                elif fields.get("name")==NODE and line.startswith("FUSION_UWB "):last_values.update(uwb_sweep=int(fields["sweep"],0),uwb_strobe_us=int(fields["strobe_us"],0),uwb_master_ms=int(fields["master_ms"],0))
                if line.startswith(("FUSION_CONNECTED ","FUSION_DISCONNECTED ")) or "RESET" in line:formal_events.append({"monotonic":now,"line":line})
            t1=time.monotonic();reason="STOPPED_BY_OPERATOR" if stop else "PLANNED_DURATION_COMPLETE";print("FORMAL_COMPLETE" if not stop else reason,flush=True)
            ledger.update(formal_t0=formal_marker,formal_initial_values=last_values_t0,formal_t1_monotonic=t1,formal_t1_wall=wall(),formal_duration_s=t1-formal_marker["monotonic"],formal_final_values=dict(last_values),formal_events=formal_events,stop_reason=reason)
        else:ledger["stop_reason"]="STOPPED_BY_OPERATOR" if stop else "INFRASTRUCTURE_STOP"
    except Exception as exc:ledger.update(stop_reason="INFRASTRUCTURE_STOP",error=f"{type(exc).__name__}: {exc}")
    finally:
        if ch:
            ledger["close_drain"]=ch.quiesce_reader_and_drain("continuous_close");ch.close();ledger["health_final"]=ch.health_snapshot()
        rc,summary=stop_listener(listener_proc,listener_log);ledger["listener_rc"]=rc;ledger["listener_summary"]=summary
        idx.close();raw.close();cdc.close();ledger["raw_sha256"]=sha(continuous/"fusion_host_raw.cobs.bin");ledger["identity_observation"]=classify_observation(all_observation);ledger["status"]="COMPLETE";ledger["finalized_wall"]=wall();atomic(root/"RUN_MANIFEST.json",ledger)
        phases={"COLLECTOR_OPEN":opened,"RAW_RECORDING_FROM_FIRST_BYTE":ledger.get("first_byte_monotonic"),"WARMUP_RECORDING":opened,"LIVE_CATCHUP":ledger.get("live_catchup_monotonic"),"FORMAL_T0":(formal_marker or {}).get("monotonic"),"CLEAN_STOP":ledger.get("formal_t1_monotonic"),"one_serial_open":ledger["serial_open_count"]==1,"one_raw_file":True}
        phases["single_timeline_valid"]=lifecycle_is_single_timeline(phases,ledger);atomic(root/"CAPTURE_PHASES.json",phases)
    return ledger


def main():
    ap=argparse.ArgumentParser();ap.add_argument("--out-dir",type=Path,required=True);ap.add_argument("--duration-s",type=float,default=600);ap.add_argument("--min-warmup-s",type=float,default=60);ap.add_argument("--max-warmup-s",type=float,default=180);ap.add_argument("--stable-s",type=int,default=30);a=ap.parse_args();a.out_dir.mkdir(parents=True,exist_ok=False)
    ledger=capture(a.out_dir,a.duration_s,a.min_warmup_s,a.max_warmup_s,a.stable_s);print(ledger.get("stop_reason"),flush=True);return 0 if ledger.get("stop_reason")=="PLANNED_DURATION_COMPLETE" else 2
if __name__=="__main__":raise SystemExit(main())
