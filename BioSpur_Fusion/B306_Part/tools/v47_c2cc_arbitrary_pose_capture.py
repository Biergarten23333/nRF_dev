#!/usr/bin/env python3
"""One-open, interactive BSFC2CC arbitrary-pose calibration capture."""
from __future__ import annotations

import argparse, csv, hashlib, json, math, os, queue, signal, subprocess, sys, threading, time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from async_line_channel import ThreadedLineChannel
from fusion_session import parse_fields, parse_reply, resolve_fusion_port
from v47_c2cc_arbitrary_pose import (ACCEL_LSB_PER_G, GYRO_LSB_PER_DPS,
    PREREGISTERED, coverage_metrics, distinct_direction, fit_and_select,
    heldout_metrics, parse_imu_samples, stability_metrics, temperature_model)
from v47_c2cc_continuous_capture import LiveCatchupDetector, formal_start_disposition
from v47_c2cc_stationary_capture import FWID, IMAGE, MARKER, MASTER, NODE

ROOT=Path(__file__).resolve().parents[2]
POSES=[
 "PCB 主元件面朝上，平放并静止。", "PCB 主元件面朝下，平放并静止。",
 "以一条长边稳定支撑，选择不会碰到天线的方向。", "换到相对的长边稳定支撑。",
 "以一条短边稳定支撑。", "换到相对的短边稳定支撑。",
 "以第一个板角/非天线区域形成斜置姿态。", "换到明显不同的第二个板角斜置。",
 "翻面后以第一个板角斜置。", "翻面后以第二个板角斜置。",
 "选择尚未出现的中等倾角姿态，避免接近前十个姿态。", "选择另一方向的中等倾角姿态。",
 "自适应姿态：选择一个与此前明显不同的新斜置方向。", "自适应姿态：换另一边或角，扩大球面覆盖。",
 "自适应姿态：选择未覆盖方向，保持支撑稳定。", "自适应姿态：翻转并改变倾角，避免重复。",
 "自适应姿态：再选择一个新的稳定斜置方向。", "自适应姿态：完成最后一个最大差异方向。",
]
VALIDATION=[
 "held-out 1：任选一个未用于标定的新姿态。", "held-out 2：换到另一个新的斜置姿态。",
 "held-out 3：再次翻转并采用新的倾角。", "held-out 4：最后一个未见姿态。",
]

def wall(): return datetime.now(timezone.utc).astimezone().isoformat(timespec="milliseconds")
def sha(path):
 h=hashlib.sha256()
 with Path(path).open("rb") as f:
  for b in iter(lambda:f.read(4<<20),b""):h.update(b)
 return h.hexdigest()
def atomic(path,value):
 t=path.with_name(path.name+".tmp");t.write_text(json.dumps(value,indent=2,sort_keys=True)+"\n");os.replace(t,path)
def token_disposition(token, accepted):
 if token=="STOP": return "STOP"
 return "ACCEPT" if token in accepted else "REJECT"

class Inbox:
 def __init__(self):self.q=queue.Queue();threading.Thread(target=self._run,daemon=True).start()
 def _run(self):
  for line in sys.stdin:self.q.put((line.strip(),time.monotonic(),wall()))

class Recorder:
 def __init__(self,root):
  self.root=root;self.rawdir=root/"continuous_raw";self.rawdir.mkdir()
  self.cdc=(self.rawdir/"fusion_cdc.log").open("x",buffering=1);self.raw=(self.rawdir/"fusion_host_raw.cobs.bin").open("xb",buffering=0);self.index=(self.rawdir/"consumption_index.jsonl").open("x",buffering=1)
  self.ch=None;self.phase="COLLECTOR_OPEN";self.record_index=0;self.last_seq=None;self.last_n=0;self.last_base=None;self.faults=deque();self.all_faults=[];self.all_nodes=set();self.unexpected=[];self.samples=[];self.rows=[];self.aborted=False
 def open(self):
  self.open_mono=time.monotonic();self.open_wall=wall();port=resolve_fusion_port(None)
  self.ch=ThreadedLineChannel(port,self.cdc,"FUSION",decoded_queue_records=1048576,backlog_red_records=131072,raw_backlog_red_bytes=131072,stall_red_s=2,raw_file=self.raw);self.ch.transport_mode="binary";self.ch.text_pending.clear();return port
 def consume(self,deadline):
  line=self.ch.read(deadline)
  if not line:return None,[]
  now=time.monotonic();self.record_index+=1;f=parse_fields(line);name=f.get("name");
  if name and name!="-":self.all_nodes.add(name)
  if name and name not in (NODE,"-"):
   self.unexpected.append({"monotonic":now,"phase":self.phase,"line":line})
  h=self.ch.health_snapshot();self.index.write(json.dumps({"record_index":self.record_index,"consume_monotonic":now,"raw_bytes_submitted":h["raw_bytes_submitted"],"phase":self.phase,"line":line},separators=(",",":"))+"\n")
  parsed=[]
  if line.startswith("FUSION_IMU ") and name==NODE:
   try:
    parsed=parse_imu_samples(f,now);seq=int(f["seq"],0);n=int(f["n"],0);base=int(f["base_us"],0)
    if self.last_seq is not None:
     expected=(self.last_seq+self.last_n)&0xffff
     if seq!=expected:
      fault={"monotonic":now,"kind":"IMU_SEQUENCE","expected":expected,"observed":seq};self.faults.append(fault);self.all_faults.append(fault)
     if base<=self.last_base:
      fault={"monotonic":now,"kind":"IMU_TIMESTAMP_REVERSAL","previous":self.last_base,"observed":base};self.faults.append(fault);self.all_faults.append(fault)
    self.last_seq,self.last_n,self.last_base=seq,n,base
    for x in parsed:x["phase"]=self.phase
    self.samples.extend(parsed)
   except Exception as e:
    fault={"monotonic":now,"kind":"IMU_PARSE","error":str(e)};self.faults.append(fault);self.all_faults.append(fault)
  while self.faults and self.faults[0]["monotonic"]<now-120:self.faults.popleft()
  return line,parsed
 def marker(self,name,extra=None):
  h=self.ch.health_snapshot();return {"name":name,"wall":wall(),"monotonic":time.monotonic(),"raw_byte_offset":h["raw_bytes_submitted"],"decoded_record_index":h["decoded_records"],"consumed_record_index":self.record_index,**(extra or {})}
 def close(self):
  drain={};health={}
  if self.ch:
   drain=self.ch.quiesce_reader_and_drain("arbitrary_pose_clean_stop");self.ch.close();health=self.ch.health_snapshot()
  self.index.close();self.raw.close();self.cdc.close();return drain,health

class Protocol:
 def __init__(self,rec,inbox,root):
  self.r=rec;self.inbox=inbox;self.instructions=[];self.tokens=[];self.fi=(root/"OPERATOR_INSTRUCTIONS.jsonl").open("x",buffering=1);self.ft=(root/"OPERATOR_TOKENS.jsonl").open("x",buffering=1)
 def wait(self,text,accepted,step):
  row={"step":step,"instruction":text,"accepted_tokens":list(accepted),"wall":wall(),"monotonic":time.monotonic()};self.instructions.append(row);self.fi.write(json.dumps(row,separators=(",",":"))+"\n")
  print(text,flush=True);print("请只回复："+" / ".join((*accepted,"STOP")),flush=True)
  while not self.r.aborted:
   self.r.consume(time.monotonic()+.1)
   try:token,mono,w=self.inbox.q.get_nowait()
   except queue.Empty:continue
   d=token_disposition(token,accepted);tr={"step":step,"token":token,"monotonic":mono,"wall":w,"disposition":d};self.tokens.append(tr);self.ft.write(json.dumps(tr,separators=(",",":"))+"\n")
   if d=="STOP":self.r.aborted=True;return "STOP"
   if d=="ACCEPT":return token
   print("输入无效；当前只接受："+" / ".join((*accepted,"STOP")),flush=True)
 def close(self):self.fi.close();self.ft.close()

def collect_fixed_segment(rec, label, decisions, accepted_dirs, require_distinct):
 rec.phase=label;start=rec.marker(label+"_FIXED");recent=deque();stable_since=None;last_eval=0.;segment=[]
 while not rec.aborted:
  now=time.monotonic();_,new=rec.consume(now+.1)
  recent.extend(new)
  while recent and recent[0]["host_monotonic"]<now-1.0:recent.popleft()
  if now-last_eval<.25:continue
  last_eval=now;m=stability_metrics(list(recent));fault=any(x["monotonic"]>=now-1 for x in rec.faults);m.update({"phase":label,"monotonic":now,"sequence_or_time_fault":fault})
  stable=bool(m.get("stable")) and not fault
  if stable:
   if stable_since is None:stable_since=now-1.0
  else:stable_since=None
  m["continuous_stable_s"]=0 if stable_since is None else now-stable_since;decisions.append(m)
  if stable_since is not None and now-stable_since>=PREREGISTERED["stable_target_s"]:
   segment=[x for x in rec.samples if stable_since<=x["host_monotonic"]<=now]
   mean=np.mean([x["accel_g"] for x in segment],axis=0);direction=mean/np.linalg.norm(mean);ok,angle=distinct_direction(direction,accepted_dirs)
   if require_distinct and not ok:
    return {"accepted":False,"reason":"DUPLICATE_OR_INSUFFICIENT_DIRECTION","nearest_angle_deg":angle,"start":start,"end":rec.marker(label+"_REJECT")},segment,direction
   return {"accepted":True,"reason":"STABLE_AND_DISTINCT" if require_distinct else "STABLE","nearest_angle_deg":angle,"start":start,"end":rec.marker(label+"_ACCEPT"),"duration_s":now-stable_since,"samples":len(segment)},segment,direction
 return {"accepted":False,"reason":"OPERATOR_STOP","start":start,"end":rec.marker(label+"_STOP")},[],None

def write_csv(path, rows, fields=None):
 rows=list(rows)
 if fields is None:
  fields=[]
  for row in rows:
   for key in row:
    if key not in fields:fields.append(key)
 with path.open("x",newline="") as f:
  w=csv.DictWriter(f,fieldnames=fields,lineterminator="\n");w.writeheader();w.writerows(rows)

def finish_outputs(root,rec,ledger,pose_sets,val_sets,pose_windows,decisions,frozen,validation):
 all_stationary=[x for s in pose_sets+val_sets for x in s];gyro=np.asarray([x["gyro_dps"] for x in all_stationary]);acc=np.asarray([x["accel_g"] for x in all_stationary]);temp=temperature_model(all_stationary)
 gyro_profile={"axis_labels":["g0","g1","g2"],"bias_dps":np.mean(gyro,axis=0).tolist() if len(gyro) else None,"std_dps":np.std(gyro,axis=0).tolist() if len(gyro) else None,"mad_dps":np.median(np.abs(gyro-np.median(gyro,axis=0)),axis=0).tolist() if len(gyro) else None,"samples":len(gyro),"temperature_model":temp}
 atomic(root/"ACCEL_CALIBRATION_PROFILE.json",frozen or {"status":"NOT_FROZEN"});atomic(root/"GYRO_BIAS_NOISE_PROFILE.json",gyro_profile);atomic(root/"TEMPERATURE_MODEL.json",temp);atomic(root/"VALIDATION_RESULTS.json",validation or {"status":"NOT_RUN"})
 atomic(root/"AXIS_FRAME_BOUNDARY.json",{"raw_axes":["a0","a1","a2","g0","g1","g2"],"physical_axis_binding":"UNKNOWN","body_or_V4_binding":"NOT_ESTABLISHED","yaw":"UNOBSERVABLE_FROM_GRAVITY","gyro_scale_factor":"NOT_CALIBRATED","gyro_misalignment":"NOT_CALIBRATED","full_accel_matrix_identifiability":"norm-only ellipsoid; unique SPD correction convention used","forbidden_claims":["absolute attitude truth","sensor-to-body transform","V4 transform"]})
 atomic(root/"POSITION_PROXY.json",{"status":"NO_ABSOLUTE_POSE_TRUTH","proxy":"normalized stationary raw acceleration direction","limitations":"gravity direction only; yaw, board/body/V4 axes and absolute orientation unavailable"})
 write_csv(root/"POSE_WINDOWS.csv",pose_windows)
 write_csv(root/"STABILITY_DECISIONS.csv",decisions)
 rows=[]
 for p,s in enumerate(pose_sets,1):
  for x in s:rows.append({"set":"CALIBRATION","pose":p,"host_monotonic":f'{x["host_monotonic"]:.9f}',"node_us":x["node_us"],"seq":x["seq"],"temp_c":x["temperature_c"],**{f"a{i}_raw":x["accel_raw"][i] for i in range(3)},**{f"g{i}_raw":x["gyro_raw"][i] for i in range(3)}})
 for p,s in enumerate(val_sets,1):
  for x in s:rows.append({"set":"HELDOUT","pose":p,"host_monotonic":f'{x["host_monotonic"]:.9f}',"node_us":x["node_us"],"seq":x["seq"],"temp_c":x["temperature_c"],**{f"a{i}_raw":x["accel_raw"][i] for i in range(3)},**{f"g{i}_raw":x["gyro_raw"][i] for i in range(3)}})
 write_csv(root/"continuous_raw"/"stationary_imu_samples.csv",rows)
 if pose_sets:
  dirs=np.asarray([np.mean([x["accel_g"] for x in s],axis=0) for s in pose_sets]);dirs/=np.linalg.norm(dirs,axis=1)[:,None]
  fig=plt.figure(figsize=(7,6));ax=fig.add_subplot(111,projection="3d");ax.scatter(*dirs.T,c=np.arange(len(dirs)),cmap="viridis");
  for i,d in enumerate(dirs):ax.text(*d,str(i+1));ax.set(xlabel="a0 direction",ylabel="a1 direction",zlabel="a2 direction",title="Calibration gravity-direction coverage (raw axes)");fig.tight_layout();fig.savefig(root/"POSE_COVERAGE.svg");plt.close(fig)
 if all_stationary:
  t=np.arange(len(acc))/200;fig,ax=plt.subplots(2,1,figsize=(11,6),sharex=True);ax[0].plot(t,np.linalg.norm(acc,axis=1),lw=.4);ax[0].set_ylabel("raw accel norm [g]");ax[1].plot(t,np.linalg.norm(gyro,axis=1),lw=.4);ax[1].set(ylabel="gyro norm [dps]",xlabel="concatenated stationary time [s]");fig.tight_layout();fig.savefig(root/"STABILITY_TIMELINE.svg");plt.close(fig)
 if frozen and pose_sets:
  models=frozen["model_selection"]["candidates"];fig,ax=plt.subplots(figsize=(7,4));ax.bar([x["model"] for x in models],[x["loo_rmse_g"] for x in models]);ax.set(ylabel="leave-one-pose-out RMSE [g]",title="Preregistered model comparison");fig.tight_layout();fig.savefig(root/"MODEL_RESIDUALS.svg");plt.close(fig)
 atomic(root/"CALIBRATION_SAMPLES_INDEX.json",{"pose_windows":pose_windows,"authoritative_raw":"continuous_raw/fusion_host_raw.cobs.bin","derived_samples":"continuous_raw/stationary_imu_samples.csv"})
 coverage=coverage_metrics([np.mean([x["accel_g"] for x in s],axis=0) for s in pose_sets]) if pose_sets else {}
 gate=(len(pose_sets)==18 and len(val_sets)==4 and frozen and validation and validation.get("pass") and coverage.get("direction_covariance_min_eigenvalue",0)>=PREREGISTERED["coverage_covariance_min_eigenvalue"] and coverage.get("design_condition",math.inf)<=PREREGISTERED["coverage_design_condition_max"])
 verdict="C2CC_DEVICE_CALIBRATION_PASS" if gate else ("C2CC_DEVICE_CALIBRATION_FAIL" if len(pose_sets)==18 and len(val_sets)==4 and frozen and validation else "C2CC_CALIBRATION_CAPTURE_FAIL")
 report=f"""# BSFC2CC arbitrary-pose IMU intrinsic calibration\n\nVerdict: **{verdict}**\n\nThis was one continuous, one-open v47 capture. It used 18 calibration poses and {len(val_sets)} held-out poses. The calibration was frozen before held-out validation. No external pose truth was available: all axes remain `a0..a2/g0..g2`, yaw is unobservable, and no board/body/V4 transform is claimed.\n\n- Selected accelerometer model: `{(frozen or {}).get('model_selection',{}).get('selected_model','NONE')}`\n- Held-out uncalibrated/calibrated RMSE: `{(validation or {}).get('uncalibrated_rmse_g','NA')}` / `{(validation or {}).get('rmse_g','NA')}` g\n- Held-out absolute/relative improvement: `{(validation or {}).get('absolute_improvement_g','NA')}` g / `{(validation or {}).get('relative_improvement','NA')}`\n- Held-out maximum absolute norm residual: `{(validation or {}).get('max_abs_g','NA')}` g\n- Coverage minimum eigenvalue: `{coverage.get('direction_covariance_min_eigenvalue','NA')}`\n- Design condition: `{coverage.get('design_condition','NA')}`\n- Temperature model: `{'ENABLED' if temp.get('enabled') else temp.get('reason')}`\n- Raw sequence/time faults observed: `{len(rec.all_faults)}` (full run manifest preserves details)\n\nMagnetic supports were permitted only as mechanical holders, away from the DWM1001C antenna. No magnetometer data or UWB acceptance criterion was used. This calibration is host-side only and was not written to firmware.\n"""
 (root/"REPORT.md").write_text(report)
 ledger.update(verdict=verdict,coverage=coverage,preregistered_thresholds=PREREGISTERED,all_observed_nodes=sorted(rec.all_nodes),unexpected_observation_count=len(rec.unexpected),unexpected_observations=rec.unexpected,sequence_time_faults=rec.all_faults)
 atomic(root/"RUN_MANIFEST.json",ledger)
 files=sorted(p for p in root.rglob("*") if p.is_file() and p.name!="SHA256SUMS")
 (root/"SHA256SUMS").write_text("".join(f"{sha(p)}  {p.relative_to(root)}\n" for p in files))
 return verdict

def main():
 ap=argparse.ArgumentParser();ap.add_argument("--out-root",type=Path,default=ROOT/"B306_Part/logs");a=ap.parse_args();root=a.out_root/("v47_c2cc_arbitrary_pose_calibration_"+datetime.now().strftime("%Y%m%d_%H%M%S"));root.mkdir(parents=True)
 print(f"RUN_DIR={root}",flush=True);atomic(root/"PREREGISTERED_THRESHOLDS.json",PREREGISTERED)
 ledger={"schema":"biospur-c2cc-arbitrary-pose-calibration-run-v1","node":NODE,"expected_only":True,"magnetic_support_policy":"AUTHORIZED_AS_MECHANICAL_HOLDER_ONLY_KEEP_AWAY_FROM_DWM_ANTENNA","hardware_mutations":[],"commands":[],"serial_open_count":0,"phases":[],"start_wall":wall()};rec=Recorder(root);proto=Protocol(rec,Inbox(),root);pose_sets=[];val_sets=[];pose_windows=[];decisions=[];accepted_dirs=[];frozen=None;validation=None
 def stop(_s,_f):rec.aborted=True
 signal.signal(signal.SIGINT,stop);signal.signal(signal.SIGTERM,stop)
 try:
  port=rec.open();ledger.update(serial_open_count=1,port=port,collector_open=rec.marker("COLLECTOR_OPEN"));print("COLLECTOR_OPEN — raw capture active from first byte",flush=True)
  # Decode-before-send guard.  Only four read-only observations are transmitted.
  guard=None;deadline=time.monotonic()+20
  while time.monotonic()<deadline and guard is None:
   line,_=rec.consume(deadline)
   if line and line.startswith("FUSION_IMU ") and parse_fields(line).get("name")==NODE:guard=line
  if not guard:raise RuntimeError("READ_ONLY_IDENTITY_GUARD_FAILED_NO_BSFC2CC_IMU")
  ledger["decode_before_send_guard"]={"passed":True,"known_record":guard[:240],"port":port,"baud":115200,"dtr":False,"rts":False}
  observations=[]
  for cmd in ("MASTER STATUS","LIST",f"{NODE} PING",f"{NODE} BOOT CONFIRM STATUS"):
   rec.ch.send(cmd);ledger["commands"].append({"command":cmd,"classification":"READ_ONLY_IDENTITY_OBSERVATION","monotonic":time.monotonic()})
  end=time.monotonic()+5
  while time.monotonic()<end:
   line,_=rec.consume(end)
   if line:observations.append(line)
  masters=[parse_fields(x) for x in observations if x.startswith("FUSION_MASTER_STATUS ")];listings=[parse_fields(x) for x in observations if x.startswith("FUSION_LIST ")];peers=[parse_fields(x) for x in observations if x.startswith("FUSION_PEER ")]
  pong={};confirm={}
  for line in observations:
   r=parse_reply(line)
   if r:
    f=parse_fields(r.text)
    if r.text.startswith("PONG "):pong=f
    elif r.text.startswith("BOOT CONFIRM STATUS "):confirm=f
  peer_names=[x.get("name") for x in peers]
  checks={"master_marker":bool(masters) and masters[-1].get("marker")==MASTER,"expected_only":bool(listings) and listings[-1].get("count")=="1" and peer_names==[NODE],"peer_ready":len(peers)==1 and peers[0].get("connected")==peers[0].get("subscribed")=="1","identity":all(pong.get(k)==v for k,v in {"name":NODE,"fw":MARKER,"fwid":FWID,"image_sha":IMAGE}.items()),"confirmed":confirm.get("confirmed")=="1"}
  ledger["identity_observation"]={"checks":checks,"master":masters[-1] if masters else {},"list":listings[-1] if listings else {},"peers":peers,"pong":pong,"confirm":confirm}
  if not all(checks.values()):raise RuntimeError("EXPECTED_NODE_IDENTITY_OR_CONFIRMATION_FAILED")
  # Finish at least 60 s warm-up while proving 30 consecutive live seconds.
  rec.phase="WARMUP_AND_CDC_DRAIN";det=LiveCatchupDetector();next_sec=math.floor(time.monotonic())+1;bucket=[];warm_seconds=[]
  while not rec.aborted:
   line,new=rec.consume(min(next_sec,time.monotonic()+.1));now=time.monotonic()
   if line:
    f=parse_fields(line)
    if new:bucket.append(("i",len(new),now*1000-int(f["master_ms"],0)))
    elif line.startswith("FUSION_UWB ") and f.get("name")==NODE:bucket.append(("u",1,now*1000-int(f["master_ms"],0)))
   if now>=next_sec:
    offsets=[x[2] for x in bucket];row={"start_monotonic":next_sec-1,"end_monotonic":next_sec,"imu_hz":sum(x[1] for x in bucket if x[0]=="i"),"uwb_hz":sum(x[1] for x in bucket if x[0]=="u"),"imu_gap_events":sum(x["monotonic"]>=next_sec-1 and x["kind"]=="IMU_SEQUENCE" for x in rec.faults),"uwb_gap_events":0,"age_offset_median_ms":float(np.median(offsets)) if offsets else None,"decoded_queue_depth":rec.ch.health_snapshot()["decoded_queue_depth"],"raw_queue_depth":rec.ch.health_snapshot()["raw_queue_depth"],"serial_input_bytes":rec.ch.health_snapshot()["serial_input_bytes"],"timestamp_jump":False};ok,d=det.update(row);row.update(monotonic=next_sec,live_evidence=d);warm_seconds.append(row);bucket=[];elapsed=next_sec-rec.open_mono;disp=formal_start_disposition(elapsed,det.stable_seconds,30,180)
    if elapsed>=60 and disp:
     ledger["live_catchup"]=rec.marker("LIVE_CATCHUP",{"disposition":disp,"stable_seconds":det.stable_seconds});atomic(root/"WARMUP_SECONDLY_EVIDENCE.json",warm_seconds);print(disp,flush=True);break
    next_sec+=1
  if not rec.aborted:
   if proto.wait("INITIAL_STILL：保持 BSFC2CC 当前姿态完全不动，不要碰板。磁性夹具只能作机械支撑并远离 DWM1001C 天线。准备好后回复 FIXED。",("FIXED",),"INITIAL_STILL_FIXED")!="STOP":
    row,seg,_=collect_fixed_segment(rec,"INITIAL_STILL",decisions,[],False);ledger["phases"].append(row)
    if row["accepted"]:proto.wait("INITIAL_STILL 已接受。准备进入 18 个 calibration poses；回复 NEXT。",("NEXT","REPEAT"),"INITIAL_STILL_NEXT")
  for i,instruction in enumerate(POSES,1):
   while not rec.aborted:
    if proto.wait(f"CALIBRATION POSE {i}/18：{instruction} 不要定义物理轴；放稳后松手，回复 FIXED。",("FIXED",),f"CAL_{i}_FIXED")=="STOP":break
    row,seg,direction=collect_fixed_segment(rec,f"CALIBRATION_POSE_{i}",decisions,accepted_dirs,True);row.update(set="CALIBRATION",pose=i);pose_windows.append(row);ledger["phases"].append(row)
    if not row["accepted"]:
     print(f"POSE {i} 未接受：{row['reason']}，最近方向夹角={row.get('nearest_angle_deg')}。",flush=True);proto.wait("请改变姿态后重做当前 pose；回复 REPEAT。",("REPEAT",),f"CAL_{i}_REPEAT");continue
    pose_sets.append(seg);accepted_dirs.append(direction.tolist());print(f"POSE {i} ACCEPTED: {len(seg)} samples, stable {row['duration_s']:.1f}s",flush=True)
    token=proto.wait("该姿态已接受。回复 NEXT 进入下一姿态，或 REPEAT 重做并替换本姿态。",("NEXT","REPEAT"),f"CAL_{i}_NEXT")
    if token=="REPEAT":pose_sets.pop();accepted_dirs.pop();continue
    break
  if len(pose_sets)==18 and not rec.aborted:
   cov=coverage_metrics(accepted_dirs)
   if cov["direction_covariance_min_eigenvalue"]<PREREGISTERED["coverage_covariance_min_eigenvalue"] or cov["design_condition"]>PREREGISTERED["coverage_design_condition_max"]:raise RuntimeError("CALIBRATION_POSE_COVERAGE_INSUFFICIENT_REPAIR_REQUIRED")
   done=threading.Event();box={}
   def fit_work():
    try:box["selection"]=fit_and_select([np.asarray([x["accel_g"] for x in s]) for s in pose_sets])
    except Exception as e:box["error"]=f"{type(e).__name__}: {e}"
    done.set()
   threading.Thread(target=fit_work,daemon=True).start();rec.phase="FREEZE_CALIBRATION"
   while not done.is_set():rec.consume(time.monotonic()+.1)
   if "error" in box:raise RuntimeError("CALIBRATION_FIT_FAILED "+box["error"])
   frozen={"schema":"biospur-c2cc-host-accel-calibration-v1","frozen_before_validation":True,"freeze_marker":rec.marker("FREEZE_CALIBRATION"),"raw_axis_labels":["a0","a1","a2"],"input_scale":"raw/2048 g","gravity_target_g":1.0,"coverage":cov,"model_selection":box["selection"]};atomic(root/"ACCEL_CALIBRATION_PROFILE.json",frozen);print("CALIBRATION_FROZEN — held-out data has not been used",flush=True)
  for i,instruction in enumerate(VALIDATION,1):
   if rec.aborted or not frozen:break
   while not rec.aborted:
    if proto.wait(f"HELD-OUT VALIDATION {i}/4：{instruction} 放稳后回复 FIXED。此数据不会重拟合。",("FIXED",),f"VAL_{i}_FIXED")=="STOP":break
    row,seg,direction=collect_fixed_segment(rec,f"HELDOUT_POSE_{i}",decisions,accepted_dirs+[np.mean([x["accel_g"] for x in s],axis=0).tolist() for s in val_sets],True);row.update(set="HELDOUT",pose=i);pose_windows.append(row);ledger["phases"].append(row)
    if not row["accepted"]:proto.wait("held-out 姿态重复或不稳定；改变姿态后回复 REPEAT。",("REPEAT",),f"VAL_{i}_REPEAT");continue
    val_sets.append(seg);print(f"HELD-OUT {i} ACCEPTED",flush=True);token=proto.wait("held-out 姿态已接受；回复 NEXT，或 REPEAT 重做并替换。",("NEXT","REPEAT"),f"VAL_{i}_NEXT")
    if token=="REPEAT":val_sets.pop();continue
    break
  if frozen and len(val_sets)==4:
   validation=heldout_metrics([np.asarray([x["accel_g"] for x in s]) for s in val_sets],frozen["model_selection"]["selected"]);atomic(root/"VALIDATION_RESULTS.json",validation)
  if not rec.aborted:
   proto.wait("FINAL_STILL：保持最后姿态不动，回复 FIXED 后采集最终静止段。",("FIXED",),"FINAL_STILL_FIXED")
   if not rec.aborted:
    row,_,_=collect_fixed_segment(rec,"FINAL_STILL",decisions,[],False);ledger["phases"].append(row)
  ledger["stop_reason"]="PLANNED_SEQUENCE_COMPLETE" if not rec.aborted else "OPERATOR_STOP"
 except Exception as e:ledger.update(stop_reason="FAIL_CLOSED",error=f"{type(e).__name__}: {e}");print(ledger["error"],flush=True)
 finally:
  proto.close();drain,health=rec.close();ledger.update(clean_stop=rec.marker("CLEAN_STOP") if rec.ch else {"wall":wall()},close_drain=drain,health_final=health,operator_instructions=proto.instructions,operator_tokens=proto.tokens,raw_sha256=sha(root/"continuous_raw"/"fusion_host_raw.cobs.bin"),end_wall=wall(),calibration_pose_count=len(pose_sets),heldout_pose_count=len(val_sets));verdict=finish_outputs(root,rec,ledger,pose_sets,val_sets,pose_windows,decisions,frozen,validation);print(f"{ledger['stop_reason']} VERDICT={verdict} RUN_DIR={root}",flush=True)
 return 0 if ledger.get("stop_reason")=="PLANNED_SEQUENCE_COMPLETE" and verdict=="C2CC_DEVICE_CALIBRATION_PASS" else 2

if __name__=="__main__":raise SystemExit(main())
