#!/usr/bin/env python3
"""Interactive one-open BSFC2CC rotating-arm capture state machine."""
from __future__ import annotations
import argparse,hashlib,json,math,os,queue,shutil,signal,subprocess,sys,threading,time
from datetime import datetime,timezone
from pathlib import Path

from async_line_channel import ThreadedLineChannel
from fusion_session import parse_fields,parse_reply,resolve_fusion_port
from v47_c2cc_stationary_capture import (FWID,GEOMETRY,IMAGE,MARKER,MASTER,NODE,S2_CODE,S2_MANIFEST,anchor_preflight,start_listener,stop_listener)
from v47_c2cc_continuous_capture import LiveCatchupDetector,formal_start_disposition

ROOT=Path(__file__).resolve().parents[2]
ABORTS={"ABORT_MOTOR_TEST","停止"}
def token_disposition(token,accepted):
 if token in ABORTS:return "ABORT"
 if token in accepted:return "ACCEPT"
 return "REJECT"
def wall():return datetime.now(timezone.utc).astimezone().isoformat(timespec="milliseconds")
def sha(p):
 h=hashlib.sha256()
 with Path(p).open("rb") as f:
  for b in iter(lambda:f.read(4<<20),b""):h.update(b)
 return h.hexdigest()
def atomic(p,v):
 t=p.with_name(p.name+".tmp");t.write_text(json.dumps(v,indent=2,sort_keys=True)+"\n");os.replace(t,p)

class TokenInbox:
 def __init__(self):
  self.q=queue.Queue();self.thread=threading.Thread(target=self._run,daemon=True);self.thread.start()
 def _run(self):
  while True:
   line=sys.stdin.readline()
   if line=="":return
   self.q.put((line.strip(),time.monotonic(),wall()))

class Recorder:
 def __init__(self,root,duration_budget=1800):
  self.root=root;self.rawdir=root/"continuous_raw";self.warm=root/"warmup";self.formal=root/"formal";self.analysis=root/"analysis"
  for p in (self.rawdir,self.warm,self.formal,self.analysis):p.mkdir(parents=True,exist_ok=False)
  self.cdc=(self.rawdir/"fusion_cdc.log").open("x",buffering=1);self.raw=(self.rawdir/"fusion_host_raw.cobs.bin").open("xb",buffering=0);self.index=(self.rawdir/"consumption_index.jsonl").open("x",buffering=1)
  self.listener,self.listener_log=start_listener(self.rawdir/"listener_capture",duration_budget+600)
  self.ch=None;self.record_index=0;self.last={};self.last_imu_seq=None;self.last_imu_n=0;self.last_imu_base=None;self.last_uwb=None;self.last_uwb_strobe=None;self.last_motion=time.monotonic();self.events=[];self.aborted=False;self.last_record_gap=0;self.last_record_reversal=0;self.last_record_duplicate=0
  self.close_drain={};self.health_final={};self.listener_rc=None;self.listener_summary={}
  self.phase_counts={};self.phase="WARMUP"
 def open(self):
  self.open_mono=time.monotonic();self.open_wall=wall();self.ch=ThreadedLineChannel(resolve_fusion_port(None),self.cdc,"FUSION",decoded_queue_records=1048576,backlog_red_records=131072,raw_backlog_red_bytes=131072,stall_red_s=2,raw_file=self.raw);self.ch.transport_mode="binary";self.ch.text_pending.clear()
 def consume(self,deadline):
  line=self.ch.read(deadline)
  if not line:return None
  now=time.monotonic();self.record_index+=1;f=parse_fields(line);h=self.ch.health_snapshot();self.index.write(json.dumps({"record_index":self.record_index,"consume_monotonic":now,"raw_bytes_submitted":h["raw_bytes_submitted"],"phase":self.phase,"line":line},separators=(",",":"))+"\n")
  c=self.phase_counts.setdefault(self.phase,{"imu_samples":0,"imu_records":0,"uwb_sweeps":0,"sequence_gaps":0,"timestamp_reversals":0,"duplicates":0})
  self.last_record_gap=self.last_record_reversal=self.last_record_duplicate=0
  if f.get("name")==NODE and line.startswith("FUSION_IMU "):
   seq=int(f["seq"],0);n=int(f["n"],0);base=int(f["base_us"],0);c["imu_records"]+=1;c["imu_samples"]+=n
   if self.last_imu_seq is not None:
    expected=(self.last_imu_seq+self.last_imu_n)&0xffff
    self.last_record_duplicate=int(seq==self.last_imu_seq);self.last_record_gap=int(seq!=expected and not self.last_record_duplicate)
   self.last_record_reversal=int(self.last_imu_base is not None and base<=self.last_imu_base)
   c["sequence_gaps"]+=self.last_record_gap;c["timestamp_reversals"]+=self.last_record_reversal;c["duplicates"]+=self.last_record_duplicate
   self.last.update(imu_seq=seq,imu_n=n,imu_base_us=base,imu_master_ms=int(f["master_ms"],0));self.last_imu_seq,self.last_imu_n,self.last_imu_base=seq,n,base
   try:
    peak=max(math.sqrt(sum((int(v)/16.384)**2 for v in sample.split(",")[4:7])) for sample in f["samples"].split(";"))
    if peak>.5:self.last_motion=now
   except Exception:pass
  elif f.get("name")==NODE and line.startswith("FUSION_UWB "):
   sweep=int(f["sweep"],0);strobe=int(f["strobe_us"],0);c["uwb_sweeps"]+=1
   if self.last_uwb is not None:
    self.last_record_duplicate=int(sweep==self.last_uwb);self.last_record_gap=int(sweep!=((self.last_uwb+1)&0xffffffff) and not self.last_record_duplicate)
   self.last_record_reversal=int(self.last_uwb_strobe is not None and strobe<=self.last_uwb_strobe)
   c["sequence_gaps"]+=self.last_record_gap;c["timestamp_reversals"]+=self.last_record_reversal;c["duplicates"]+=self.last_record_duplicate
   self.last.update(uwb_sweep=sweep,uwb_strobe_us=strobe,uwb_master_ms=int(f["master_ms"],0));self.last_uwb,self.last_uwb_strobe=sweep,strobe
  if line.startswith(("FUSION_CONNECTED ","FUSION_DISCONNECTED ")) or "RESET" in line:self.events.append({"monotonic":now,"line":line})
  return line
 def marker(self,name,extra=None):
  h=self.ch.health_snapshot();return {"name":name,"wall":wall(),"monotonic":time.monotonic(),"raw_byte_offset":h["raw_bytes_submitted"],"decoded_record_index":h["decoded_records"],"consumed_record_index":self.record_index,"stream_values":dict(self.last),"health":h,**(extra or {})}
 def collect(self,seconds,phase,quiet_after=False,status=None):
  self.phase=phase;start=self.marker(phase+"_START");end_target=start["monotonic"]+seconds
  if status:print(status,flush=True)
  while not self.aborted:
   now=time.monotonic()
   if now>=end_target and (not quiet_after or now-self.last_motion>=5):break
   self.consume(now+.1)
  return {"start":start,"end":self.marker(phase+"_END"),"minimum_s":seconds,"quiet_extension":quiet_after}
 def close(self):
  if self.ch:self.close_drain=self.ch.quiesce_reader_and_drain("interactive_close");self.ch.close();self.health_final=self.ch.health_snapshot()
  rc,summary=stop_listener(self.listener,self.listener_log);self.listener_rc,self.listener_summary=rc,summary
  self.index.close();self.raw.close();self.cdc.close()

def identity_observation(lines):
 master=[];listing=[];peers=[];pong={};confirm={}
 for line in lines:
  f=parse_fields(line)
  if line.startswith("FUSION_MASTER_STATUS "):master.append(f)
  elif line.startswith("FUSION_LIST "):listing.append(f)
  elif line.startswith("FUSION_PEER "):peers.append(f)
  r=parse_reply(line)
  if r:
   x=parse_fields(r.text)
   if r.text.startswith("PONG "):pong=x
   elif r.text.startswith("BOOT CONFIRM STATUS "):confirm=x
 checks={"master":bool(master) and master[-1].get("marker")==MASTER,"membership":bool(listing) and listing[-1].get("count")=="1" and [x.get("name") for x in peers]==[NODE],"peer_ready":len(peers)==1 and peers[0].get("connected")==peers[0].get("subscribed")=="1","identity":all(pong.get(k)==v for k,v in {"name":NODE,"fw":MARKER,"fwid":FWID,"image_sha":IMAGE}.items()),"confirmed":confirm.get("confirmed")=="1"}
 return {"status":"OBSERVED_EXPECTED" if all(checks.values()) else "OBSERVED_UNEXPECTED","checks":checks,"master":master[-1] if master else {},"list":listing[-1] if listing else {},"peers":peers,"pong":pong,"confirm":confirm}

class Protocol:
 def __init__(self,rec,inbox,root):
  self.r,self.inbox,self.root=rec,inbox,root;self.instructions=[];self.tokens=[];self.brackets=[]
  self.ifile=(root/"OPERATOR_INSTRUCTIONS.jsonl").open("x",buffering=1);self.tfile=(root/"OPERATOR_TOKENS.jsonl").open("x",buffering=1)
 def wait(self,instruction,accepted,step):
  issue={"step":step,"instruction":instruction,"accepted_tokens":list(accepted),"wall":wall(),"monotonic":time.monotonic()};self.instructions.append(issue);self.ifile.write(json.dumps(issue,separators=(",",":"))+"\n")
  print(instruction,flush=True);print("Expected token: "+" or ".join(accepted),flush=True)
  while not self.r.aborted:
   self.r.consume(time.monotonic()+.1)
   try:token,mono,w=self.inbox.q.get_nowait()
   except queue.Empty:continue
   disposition=token_disposition(token,accepted);row={"step":step,"token":token,"wall":w,"monotonic":mono,"accepted":disposition=="ACCEPT","abort":disposition=="ABORT","disposition":disposition};self.tokens.append(row);self.tfile.write(json.dumps(row,separators=(",",":"))+"\n")
   if disposition=="ABORT":self.r.aborted=True;return token
   if disposition=="REJECT":print("Incorrect token. Expected exactly: "+" or ".join(accepted),flush=True);continue
   bracket={"step":step,"instruction":instruction,"instruction_wall":issue["wall"],"instruction_monotonic":issue["monotonic"],"token":token,"token_wall":w,"token_monotonic":mono,"instruction_to_confirmation_s":mono-issue["monotonic"]};self.brackets.append(bracket);return token
 def close(self):self.ifile.close();self.tfile.close()

def event_method(root):
 m={"schema":"biospur-rotation-independent-label-v1","frozen_before_movement":True,"uses_s2_state":False,
 "source_parameters":{"node":"BSFC2CC","gyro_bias_dps":[0.06103515625,-0.3662109375,0.0],"frozen_gyro_rms_threshold_dps":0.06892392529328092,"range_sigma_m":[0.03862304502754728,0.03,0.03,0.03,0.05649309137430271,0.054689580406085105,0.04537565572279582,0.06064183469847632]},
 "imu_motion":{"window_s":0.5,"bias_corrected_vector_rms_threshold_dps":0.5,"threshold_rule":"max(3 * frozen_gyro_rms_threshold_dps, 0.5 dps)","minimum_true_s":0.2,"secondary_integrated_abs_gyro_window_s":1.0,"secondary_threshold_deg":0.5},
 "uwb_motion":{"window_s":1.0,"minimum_valid_anchors":4,"per_anchor_shift_threshold_rule":"max(4 * frozen_range_sigma_m[anchor], 0.12 m)","minimum_shifted_anchors":3,"t4_displacement_threshold_m":0.15,"concordance":"multi-anchor predicate AND T4 predicate"},
 "sustained_motion":{"rule":"IMU predicate OR concordant UWB predicate","minimum_true_s":0.5},
 "imu_quiet":{"rule":"IMU motion predicate false continuously","minimum_s":1.0},
 "uwb_stable":{"window_s":1.5,"minimum_valid_anchors":4,"rule":"T4 displacement span <= 0.10 m AND at most one anchor exceeds max(3 sigma, 0.09 m) about trailing median"},
 "mechanical_settle":{"rule":"first intersection of IMU quiet and UWB stable after OFF bracket","ambiguity":"SETTLE_TIME_AMBIGUOUS with bounds from the two predicate start times when no unique intersection is supported"},
 "motor_off_is_settle":False,"circle_fit_label":"NO_EXTERNAL_TRUTH_SELF_CONSISTENCY_ONLY"};atomic(root/"SENSOR_EVENT_LABEL_METHOD.json",m);return m

def main():
 ap=argparse.ArgumentParser();ap.add_argument("--out-dir",type=Path,required=True);a=ap.parse_args();root=a.out_dir;root.mkdir(parents=True,exist_ok=False);method=event_method(root)
 copy=root/"FROZEN_S2_PARAMETER_MANIFEST.json";shutil.copyfile(S2_MANIFEST,copy);frozen={"git_head":subprocess.check_output(["git","rev-parse","HEAD"],cwd=ROOT,text=True).strip(),"geometry":{"path":str(GEOMETRY.relative_to(ROOT)),"sha256":sha(GEOMETRY)},"s2_parameter_manifest":{"copy":copy.name,"sha256":sha(copy)},"s2_code":{"path":str(S2_CODE.relative_to(ROOT)),"sha256":sha(S2_CODE)},"capture_tool_sha256":sha(Path(__file__)),"event_method_sha256":sha(root/"SENSOR_EVENT_LABEL_METHOD.json")};atomic(root/"FROZEN_INPUT_HASHES.json",frozen)
 rec=Recorder(root);inbox=TokenInbox();proto=Protocol(rec,inbox,root);stop=False
 def sig(_s,_f):rec.aborted=True
 signal.signal(signal.SIGINT,sig);signal.signal(signal.SIGTERM,sig);ledger={"serial_open_count":0,"commands":[],"phases":[],"events":[]}
 try:
  rec.open();ledger.update(serial_open_count=1,collector_open_wall=rec.open_wall,collector_open_monotonic=rec.open_mono);print("CAPTURE_OPENED",flush=True);print("WARMUP_RECORDING",flush=True)
  first=None;deadline=time.monotonic()+15
  while time.monotonic()<deadline and first is None:
   x=rec.consume(deadline)
   if x and x.startswith("FUSION_"):first=x
  obs=[]
  if first:
   for cmd in ("MASTER STATUS","LIST",f"{NODE} PING",f"{NODE} BOOT CONFIRM STATUS"):
    rec.ch.send(cmd);ledger["commands"].append({"command":cmd,"monotonic":time.monotonic(),"classification":"READ_ONLY_WARMUP"})
   end=time.monotonic()+5
   while time.monotonic()<end:
    x=rec.consume(end)
    if x:obs.append(x)
  try:ledger["anchor_observation"]=anchor_preflight(rec.warm)
  except Exception as e:ledger["anchor_observation"]={"status":"OBSERVED_DEGRADED","error":str(e)}
  ledger["identity_observation"]=identity_observation(obs);det=LiveCatchupDetector();seconds=[];next_sec=math.floor(time.monotonic())+1;bucket=[]
  while not rec.aborted:
   x=rec.consume(min(next_sec,time.monotonic()+.1));now=time.monotonic()
   if x:
    f=parse_fields(x)
    if f.get("name")==NODE and x.startswith("FUSION_IMU "):bucket.append(("i",int(f["n"]),now*1000-int(f["master_ms"]),rec.last_record_gap,rec.last_record_reversal))
    elif f.get("name")==NODE and x.startswith("FUSION_UWB "):bucket.append(("u",1,now*1000-int(f["master_ms"]),rec.last_record_gap,rec.last_record_reversal))
   if now>=next_sec:
    h=rec.ch.health_snapshot();offs=[q[2] for q in bucket];row={"start_monotonic":next_sec-1,"end_monotonic":next_sec,"imu_hz":sum(q[1] for q in bucket if q[0]=="i"),"uwb_hz":sum(q[1] for q in bucket if q[0]=="u"),"imu_gap_events":sum(q[3] for q in bucket if q[0]=="i"),"uwb_gap_events":sum(q[3] for q in bucket if q[0]=="u"),"age_offset_median_ms":sorted(offs)[len(offs)//2] if offs else None,"decoded_queue_depth":h["decoded_queue_depth"],"raw_queue_depth":h["raw_queue_depth"],"serial_input_bytes":h["serial_input_bytes"],"timestamp_jump":any(q[4] for q in bucket)};ok,d=det.update(row);row["live_evidence"]=d;seconds.append(row);bucket=[];elapsed=next_sec-rec.open_mono;disp=formal_start_disposition(elapsed,det.stable_seconds,30,180)
    if elapsed>=60 and disp:
     if disp=="LIVE_CATCHUP_OBSERVED":ledger["live_catchup_first_supported_monotonic"]=next_sec-det.stable_seconds+1
     ledger["formal_t0"]=rec.marker("FORMAL_T0",{"live_catchup":disp});atomic(rec.warm/"SECONDLY_EVIDENCE.json",seconds);print("FORMAL_T0" if disp=="LIVE_CATCHUP_OBSERVED" else "STARTED_DEGRADED",flush=True);break
    next_sec+=1
  if not rec.aborted:
   ledger["phases"].append(rec.collect(60,"INITIAL_STATIONARY",status="INITIAL_STATIONARY_RUNNING — NO ACTION REQUIRED"))
  if not rec.aborted:proto.wait("Set the speed control to approximately 3 RPM while keeping the motor OFF. Do not switch it on yet. When finished, enter exactly: RPM3_READY",("RPM3_READY",),"RPM3_READY")
  if not rec.aborted:proto.wait("Switch the motor ON now. Immediately after switching it on, enter exactly: LOW_ON",("LOW_ON",),"LOW_ON")
  if not rec.aborted:ledger["phases"].append(rec.collect(60,"LOW_ROTATION",status="LOW_ROTATION_RUNNING — NO ACTION REQUIRED"))
  if not rec.aborted:proto.wait("Switch the motor OFF now. Immediately after switching it off, enter exactly: LOW_OFF",("LOW_OFF",),"LOW_OFF")
  if not rec.aborted:ledger["phases"].append(rec.collect(60,"LOW_POST_OFF",quiet_after=True))
  if not rec.aborted:proto.wait("Set the speed control to approximately 8 RPM while keeping the motor OFF. When finished, enter exactly: RPM8_READY",("RPM8_READY",),"RPM8_READY")
  if not rec.aborted:proto.wait("Switch the motor ON now. Immediately after switching it on, enter exactly: MEDIUM_ON",("MEDIUM_ON",),"MEDIUM_ON")
  if not rec.aborted:ledger["phases"].append(rec.collect(60,"MEDIUM_ROTATION",status="MEDIUM_ROTATION_RUNNING — NO ACTION REQUIRED"))
  if not rec.aborted:proto.wait("Switch the motor OFF now. Immediately after switching it off, enter exactly: MEDIUM_OFF",("MEDIUM_OFF",),"MEDIUM_OFF")
  if not rec.aborted:ledger["phases"].append(rec.collect(60,"MEDIUM_POST_OFF",quiet_after=True))
  if not rec.aborted:
   tok=proto.wait("Confirm that operating this non-rigid rotating arm at approximately 15 RPM is mechanically safe. If safe, enter exactly: RPM15_SAFE. If not safe, enter exactly: RPM15_UNSAFE",("RPM15_SAFE","RPM15_UNSAFE"),"RPM15_SAFETY")
   if tok=="RPM15_SAFE":proto.wait("Set approximately 15 RPM while keeping the motor OFF. When finished, enter exactly: RPM15_READY",("RPM15_READY",),"RPM15_READY");ledger["selected_high_rpm"]=15
   elif tok=="RPM15_UNSAFE":
    options=tuple(f"SAFE_RPM_{n}_READY" for n in range(9,15));chosen=proto.wait("Choose the highest speed you consider mechanically safe, between 9 and 14 RPM. Set it while keeping the motor OFF, then enter SAFE_RPM_N_READY, replacing N with the selected integer RPM.",options,"SAFE_RPM_READY");ledger["selected_high_rpm"]=int(chosen.split("_")[2])
  if not rec.aborted:proto.wait("Switch the motor ON now. Immediately after switching it on, enter exactly: HIGH_ON",("HIGH_ON",),"HIGH_ON")
  if not rec.aborted:ledger["phases"].append(rec.collect(60,"HIGH_ROTATION",status="HIGH_ROTATION_RUNNING — NO ACTION REQUIRED"))
  if not rec.aborted:proto.wait("Switch the motor OFF now. Immediately after switching it off, enter exactly: HIGH_OFF",("HIGH_OFF",),"HIGH_OFF")
  if not rec.aborted:ledger["phases"].append(rec.collect(90,"HIGH_POST_OFF",quiet_after=True))
  if not rec.aborted:proto.wait("Set approximately 8 RPM while keeping the motor OFF. When finished, enter exactly: SHORT_RPM8_READY",("SHORT_RPM8_READY",),"SHORT_RPM8_READY")
  for n in range(1,6):
   if rec.aborted:break
   proto.wait(f"Cycle {n}: switch the motor ON now. Immediately after switching it on, enter exactly: CYCLE_{n}_ON",(f"CYCLE_{n}_ON",),f"CYCLE_{n}_ON")
   if not rec.aborted:ledger["phases"].append(rec.collect(10,f"CYCLE_{n}_ON_RUN"))
   if not rec.aborted:proto.wait(f"Cycle {n}: switch the motor OFF now. Immediately after switching it off, enter exactly: CYCLE_{n}_OFF",(f"CYCLE_{n}_OFF",),f"CYCLE_{n}_OFF")
   if not rec.aborted:ledger["phases"].append(rec.collect(30,f"CYCLE_{n}_POST_OFF",quiet_after=True))
  if not rec.aborted:proto.wait("Confirm that the motor is OFF. Do not attempt to return the arm to any home position. When confirmed, enter exactly: FINAL_MOTOR_OFF",("FINAL_MOTOR_OFF",),"FINAL_MOTOR_OFF")
  if not rec.aborted:ledger["phases"].append(rec.collect(60,"FINAL_STATIONARY",status="FINAL_STATIONARY_RUNNING — NO ACTION REQUIRED"))
  ledger["stop_reason"]="STOPPED_BY_OPERATOR" if rec.aborted else "PLANNED_SEQUENCE_COMPLETE"
 except Exception as e:ledger.update(stop_reason="INFRASTRUCTURE_STOP",error=f"{type(e).__name__}: {e}")
 finally:
  proto.close();rec.close();ledger.update(operator_instructions=proto.instructions,operator_tokens=proto.tokens,action_brackets=proto.brackets,phase_counts=rec.phase_counts,events=rec.events,close_drain=rec.close_drain,health_final=rec.health_final,listener_rc=rec.listener_rc,listener_summary=rec.listener_summary,raw_sha256=sha(rec.rawdir/"fusion_host_raw.cobs.bin"),finalized_wall=wall());atomic(root/"RUN_MANIFEST.json",ledger);atomic(root/"CAPTURE_PHASES.json",ledger.get("phases",[]));print(ledger["stop_reason"],flush=True)
 return 0 if ledger.get("stop_reason")=="PLANNED_SEQUENCE_COMPLETE" else 2
if __name__=="__main__":raise SystemExit(main())
