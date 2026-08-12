#!/usr/bin/env python3
"""One-open blind six-pose BSFC2CC revalidation-v2 capture."""
from __future__ import annotations

import argparse, csv, hashlib, json, math, os, signal, time
from collections import deque
from pathlib import Path

import numpy as np

from fusion_session import parse_fields, parse_reply
from v47_c2cc_arbitrary_pose import PREREGISTERED, stability_metrics
from v47_c2cc_arbitrary_pose_capture import Inbox, Protocol, Recorder, atomic, wall
from v47_c2cc_continuous_capture import LiveCatchupDetector, formal_start_disposition
from v47_c2cc_stationary_capture import FWID, IMAGE, MARKER, MASTER, NODE

ROOT=Path(__file__).resolve().parents[2]
OLD=ROOT/"B306_Part/logs/v47_c2cc_arbitrary_pose_calibration_20260812_201945"
PROFILE_SHA="10895c252adbe23cb26ef1e0824abf460f3b8c03fd04d63508e06242fe63a73c"
PROTOCOL_SHA="d87503c8bcf100c9b823fd1fd08ae6e6b72eb255d03d4f2605c9fdd849e557dd"
POSES=[
 "POSE 1/6：让外壳宽大的 A 面朝上，平放在刚性底座/陶瓷支撑上；放稳并完全松手后回复 FIXED。",
 "POSE 2/6：翻转外壳，让同一个宽大的 A 面朝下；放稳并完全松手后回复 FIXED。",
 "POSE 3/6：让外壳的长边 1 朝下，竖直稳定支撑；放稳并完全松手后回复 FIXED。",
 "POSE 4/6：翻到相对的另一条长边朝下；放稳并完全松手后回复 FIXED。",
 "POSE 5/6：让外壳的短边 1 朝下，竖直稳定支撑；放稳并完全松手后回复 FIXED。",
 "POSE 6/6：翻到相对的另一条短边朝下；放稳并完全松手后回复 FIXED。",
]


def sha(path):
 h=hashlib.sha256()
 with Path(path).open("rb") as f:
  for b in iter(lambda:f.read(4<<20),b""):h.update(b)
 return h.hexdigest()


def write_csv(path,rows):
 rows=list(rows);fields=[]
 for row in rows:
  for key in row:
   if key not in fields:fields.append(key)
 with Path(path).open("x",newline="") as f:
  w=csv.DictWriter(f,fieldnames=fields,lineterminator="\n");w.writeheader();w.writerows(rows)


def collect(rec,label,decisions,target_s):
 rec.phase=label;start=rec.marker(label+"_FIXED");recent=deque();stable_since=None;last_eval=0.
 while not rec.aborted:
  now=time.monotonic();_,new=rec.consume(now+.1);recent.extend(new)
  while recent and recent[0]["host_monotonic"]<now-1:recent.popleft()
  if now-last_eval<.25:continue
  last_eval=now;m=stability_metrics(list(recent));fault=any(x["monotonic"]>=now-1 for x in rec.faults)
  stable=bool(m.get("stable")) and not fault
  if stable and stable_since is None:stable_since=now-1
  if not stable:stable_since=None
  m.update(phase=label,monotonic=now,sequence_or_time_fault=fault,
           continuous_stable_s=0 if stable_since is None else now-stable_since);decisions.append(m)
  if stable_since is not None and now-stable_since>=target_s:
   segment=[x for x in rec.samples if stable_since<=x["host_monotonic"]<=now]
   return {"accepted":True,"reason":"CONTINUOUS_STATIONARY_BLIND","start":start,"end":rec.marker(label+"_ACCEPT"),
           "duration_s":now-stable_since,"samples":len(segment)},segment
 return {"accepted":False,"reason":"OPERATOR_STOP","start":start,"end":rec.marker(label+"_STOP")},[]


def main():
 ap=argparse.ArgumentParser();ap.add_argument("--run-dir",type=Path,required=True);a=ap.parse_args();root=a.run_dir.resolve()
 protocol=root/"REVALIDATION_V2_PROTOCOL.json"
 if sha(protocol)!=PROTOCOL_SHA:raise RuntimeError("PROTOCOL_HASH_MISMATCH_BEFORE_CAPTURE")
 profile=OLD/"ACCEL_CALIBRATION_PROFILE.json"
 if sha(profile)!=PROFILE_SHA:raise RuntimeError("FROZEN_CALIBRATION_HASH_MISMATCH_BEFORE_CAPTURE")
 protocol_mtime=protocol.stat().st_mtime_ns;inbox=Inbox();rec=Recorder(root);proto=Protocol(rec,inbox,root)
 ledger={"schema":"biospur-c2cc-revalidation-v2-capture-v1","node":NODE,"protocol_sha256":PROTOCOL_SHA,
  "protocol_mtime_ns":protocol_mtime,"frozen_profile_sha256":PROFILE_SHA,"historical_verdict":"C2CC_DEVICE_CALIBRATION_FAIL",
  "parameter_changes_after_freeze":0,"serial_open_count":0,"commands":[],"phases":[],"hardware_mutations":[],"start_wall":wall()}
 poses=[];windows=[];decisions=[]
 def stop(_s,_f):rec.aborted=True
 signal.signal(signal.SIGINT,stop);signal.signal(signal.SIGTERM,stop)
 try:
  port=rec.open();ledger.update(serial_open_count=1,port=port,collector_open=rec.marker("COLLECTOR_OPEN"),protocol_frozen_before_capture=protocol.stat().st_mtime_ns==protocol_mtime)
  print("COLLECTOR_OPEN — raw capture active from first byte; frozen parameters verified",flush=True)
  guard=None;deadline=time.monotonic()+20
  while time.monotonic()<deadline and guard is None:
   line,_=rec.consume(deadline)
   if line and line.startswith("FUSION_IMU ") and parse_fields(line).get("name")==NODE:guard=line
  if not guard:raise RuntimeError("READ_ONLY_IDENTITY_GUARD_FAILED_NO_BSFC2CC_IMU")
  ledger["decode_before_send_guard"]={"passed":True,"known_record":guard[:240],"port":port,"baud":115200,"dtr":False,"rts":False}
  rec.phase="WARMUP_AND_CDC_DRAIN";det=LiveCatchupDetector();next_sec=math.floor(time.monotonic())+1;bucket=[];warm=[]
  while not rec.aborted:
   line,new=rec.consume(min(next_sec,time.monotonic()+.1));now=time.monotonic()
   if line:
    f=parse_fields(line)
    if new:bucket.append(("i",len(new),now*1000-int(f["master_ms"],0)))
    elif line.startswith("FUSION_UWB ") and f.get("name")==NODE:bucket.append(("u",1,now*1000-int(f["master_ms"],0)))
   if now>=next_sec:
    offsets=[x[2] for x in bucket];h=rec.ch.health_snapshot();row={"start_monotonic":next_sec-1,"end_monotonic":next_sec,"imu_hz":sum(x[1] for x in bucket if x[0]=="i"),"uwb_hz":sum(x[1] for x in bucket if x[0]=="u"),"imu_gap_events":sum(x["monotonic"]>=next_sec-1 and x["kind"]=="IMU_SEQUENCE" for x in rec.faults),"uwb_gap_events":0,"age_offset_median_ms":float(np.median(offsets)) if offsets else None,"decoded_queue_depth":h["decoded_queue_depth"],"raw_queue_depth":h["raw_queue_depth"],"serial_input_bytes":h["serial_input_bytes"],"timestamp_jump":False};_,d=det.update(row);row.update(monotonic=next_sec,live_evidence=d);warm.append(row);bucket=[];elapsed=next_sec-rec.open_mono;disp=formal_start_disposition(elapsed,det.stable_seconds,30,180)
    if elapsed>=60 and disp:
     ledger["live_catchup"]=rec.marker("LIVE_CATCHUP",{"disposition":disp,"stable_seconds":det.stable_seconds});atomic(root/"WARMUP_SECONDLY_EVIDENCE.json",warm);print(disp,flush=True);break
    next_sec+=1
  # Query only after the startup backlog has drained.  The failed first
  # preflight proved that sending into the stale prefix can yield a count=0
  # snapshot despite already decoding live BSFC2CC IMU records.
  observations=[]
  for cmd in ("MASTER STATUS","LIST",f"{NODE} PING",f"{NODE} BOOT CONFIRM STATUS"):
   rec.ch.send(cmd);ledger["commands"].append({"command":cmd,"classification":"READ_ONLY_IDENTITY_OBSERVATION_AFTER_LIVE_CATCHUP","monotonic":time.monotonic()})
  end=time.monotonic()+20
  while time.monotonic()<end:
   line,_=rec.consume(end)
   if line:observations.append(line)
  masters=[parse_fields(x) for x in observations if x.startswith("FUSION_MASTER_STATUS ")];listings=[parse_fields(x) for x in observations if x.startswith("FUSION_LIST ")];peers=[parse_fields(x) for x in observations if x.startswith("FUSION_PEER ")]
  pong={};confirm={}
  for line in observations:
   reply=parse_reply(line)
   if reply:
    f=parse_fields(reply.text)
    if reply.text.startswith("PONG "):pong=f
    elif reply.text.startswith("BOOT CONFIRM STATUS "):confirm=f
  names=[x.get("name") for x in peers]
  checks={"master_marker":bool(masters) and masters[-1].get("marker")==MASTER,"exact_single_peer":bool(listings) and listings[-1].get("count")=="1" and names==[NODE],
   "peer_ready":len(peers)==1 and peers[0].get("connected")==peers[0].get("subscribed")=="1",
   "identity":all(pong.get(k)==v for k,v in {"name":NODE,"fw":MARKER,"fwid":FWID,"image_sha":IMAGE}.items()),"confirmed":confirm.get("confirmed")=="1"}
  ledger["identity_observation"]={"checks":checks,"master":masters[-1] if masters else {},"list":listings[-1] if listings else {},"peers":peers,"pong":pong,"confirm":confirm}
  if not all(checks.values()):raise RuntimeError("EXPECTED_SINGLE_NODE_IDENTITY_OR_CONFIRMATION_FAILED_AFTER_LIVE_CATCHUP")
  for i,instruction in enumerate(POSES,1):
   if rec.aborted:break
   if proto.wait(instruction,("FIXED",),f"POSE_{i}_FIXED")=="STOP":break
   row,segment=collect(rec,f"HELDOUT_POSE_{i}",decisions,32.0);row.update(set="HELDOUT_REVALIDATION_V2",pose=i);windows.append(row);ledger["phases"].append(row)
   if not row["accepted"]:break
   poses.append(segment);print(f"POSE {i}/6 ACCEPTED — blind stationary window complete; no residual evaluated",flush=True)
  if len(poses)==6 and not rec.aborted:
   print("FINAL_STILL — 请保持第 6 姿态不动，自动采集 20 秒后 clean stop。",flush=True)
   row,_=collect(rec,"FINAL_STILL",decisions,20.0);ledger["phases"].append(row)
  ledger["stop_reason"]="PLANNED_SEQUENCE_COMPLETE" if len(poses)==6 and not rec.aborted else "STOPPED_BY_OPERATOR" if rec.aborted else "CAPTURE_INCOMPLETE"
 except Exception as e:
  ledger.update(stop_reason="FAIL_CLOSED",error=f"{type(e).__name__}: {e}");print(ledger["error"],flush=True)
 finally:
  proto.close();drain,health=rec.close();ledger.update(close_drain=drain,health_final=health,end_wall=wall(),pose_count=len(poses),sequence_time_faults=rec.all_faults,all_observed_nodes=sorted(rec.all_nodes),unexpected_observations=rec.unexpected)
  raw=root/"continuous_raw/fusion_host_raw.cobs.bin";ledger.update(raw_sha256=sha(raw),frozen_profile_sha256_after=sha(profile),protocol_sha256_after=sha(protocol),parameter_changes_after_freeze=0)
  atomic(root/"RUN_MANIFEST.json",ledger);write_csv(root/"POSE_WINDOWS.csv",windows);write_csv(root/"STABILITY_DECISIONS.csv",decisions)
  events=[]
  for kind,items in (("instruction",proto.instructions),("token",proto.tokens)):
   for x in items:events.append({"event_type":kind,**x})
  events.sort(key=lambda x:(x["monotonic"],x["event_type"]));(root/"OPERATOR_EVENTS.jsonl").write_text("".join(json.dumps(x,sort_keys=True,separators=(",",":"))+"\n" for x in events))
  print(f"CLEAN_STOP stop_reason={ledger['stop_reason']} poses={len(poses)} RUN_DIR={root}",flush=True)
 return 0 if ledger.get("stop_reason")=="PLANNED_SEQUENCE_COMPLETE" else 2


if __name__=="__main__":raise SystemExit(main())
