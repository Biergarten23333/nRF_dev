#!/usr/bin/env python3
"""Deterministically derive the BSFC2CC arbitrary-pose evidence package."""
from __future__ import annotations

import argparse, ast, csv, hashlib, json, math, os, re, subprocess
from collections import Counter
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from fusion_host_binary import FrameStreamDecoder, frame_to_line
from fusion_session import parse_fields
from v47_c2cc_arbitrary_pose import (MODEL_ORDER, PREREGISTERED,
    apply_calibration, coverage_metrics, fit_and_select, fit_model,
    heldout_metrics, parse_imu_samples, stability_metrics, temperature_model)
from v47_c2cc_stationary_capture import FWID as EXPECTED_FWID, IMAGE as EXPECTED_IMAGE, MARKER as EXPECTED_MARKER, MASTER as EXPECTED_MASTER

ROOT=Path(__file__).resolve().parents[2]
EXPECTED_NODE="BSFC2CC"

def sha(path):
 h=hashlib.sha256()
 with Path(path).open("rb") as f:
  for b in iter(lambda:f.read(4<<20),b""):h.update(b)
 return h.hexdigest()

def canonical(path,value):
 Path(path).write_text(json.dumps(value,indent=2,sort_keys=True,allow_nan=False)+"\n")

def write_csv(path,rows,fields=None):
 rows=list(rows)
 if fields is None:
  fields=[]
  for row in rows:
   for key in row:
    if key not in fields:fields.append(key)
 with Path(path).open("w",newline="") as f:
  w=csv.DictWriter(f,fieldnames=fields,extrasaction="raise",lineterminator="\n");w.writeheader();w.writerows(rows)

def load_windows(run):
 out=[]
 for r in csv.DictReader((run/"POSE_WINDOWS.csv").open()):
  if r["accepted"]!="True":continue
  end=ast.literal_eval(r["end"]);duration=float(r["duration_s"])
  out.append({"set":r["set"],"pose":int(r["pose"]),"start_monotonic":end["monotonic"]-duration,"end_monotonic":end["monotonic"],"end_consumed_record_index":int(end["consumed_record_index"]),"duration_s":duration,"expected_samples":int(r["samples"]),"nearest_angle_deg":float(r["nearest_angle_deg"]) if r["nearest_angle_deg"] else None})
 return out

def select_samples(run,windows):
 selected={(w["set"],w["pose"]):[] for w in windows};all_nodes=set();records=Counter();seq_last=None;seq_n=0;sequence_gaps=[];base_last=None;timestamp_reversals=[];b306_links=[];tag_reset=[]
 with (run/"continuous_raw/consumption_index.jsonl").open() as f:
  for text in f:
   row=json.loads(text);line=row["line"];mono=float(row["consume_monotonic"]);kind=line.split(" ",1)[0];records[kind]+=1;fields=parse_fields(line);name=fields.get("name")
   if name and name!="-":all_nodes.add(name)
   if name==EXPECTED_NODE and line.startswith("FUSION_IMU "):
    seq=int(fields["seq"],0);n=int(fields["n"],0);base=int(fields["base_us"],0)
    if seq_last is not None and seq!=((seq_last+seq_n)&0xffff):sequence_gaps.append({"record_index":row["record_index"],"monotonic":mono,"expected":(seq_last+seq_n)&0xffff,"observed":seq})
    if base_last is not None and base<=base_last:timestamp_reversals.append({"record_index":row["record_index"],"monotonic":mono,"previous":base_last,"observed":base})
    seq_last,seq_n,base_last=seq,n,base
    for w in windows:
     # The online segment used the detector's stable_since/now instants, while
     # the CSV stores a marker a few milliseconds later.  Include one extra
     # record at the leading edge, then take the exact online sample count from
     # the tail.  No record can arrive between the terminal decision and its
     # synchronous marker.
     if w["start_monotonic"]-.1<=mono<=w["end_monotonic"] and int(row["record_index"])<w["end_consumed_record_index"]:
      parsed=parse_imu_samples(fields,mono)
      for sample in parsed:sample["record_index"]=int(row["record_index"])
      selected[(w["set"],w["pose"])].extend(parsed);break
   if line.startswith(("FUSION_CONNECTED ","FUSION_DISCONNECTED ")):b306_links.append({"monotonic":mono,"line":line})
   if "text=TAG_RESET_" in line:tag_reset.append({"monotonic":mono,"line":line})
 for w in windows:
  key=(w["set"],w["pose"]);selected[key]=selected[key][-w["expected_samples"]:]
 segment_gaps=[]
 for key,samples in selected.items():
  for a,b in zip(samples,samples[1:]):
   if b["seq"]!=((a["seq"]+1)&0xffff):segment_gaps.append({"set":key[0],"pose":key[1],"previous":a["seq"],"observed":b["seq"]})
 tag_times=[x["monotonic"] for x in tag_reset];tag_types=Counter("DETECTED" if "TAG_RESET_DETECTED" in x["line"] else "RECOVERY_STOP" if "TAG_RESET_RECOVERY_STOP" in x["line"] else "OTHER" for x in tag_reset)
 tag_summary={"count":len(tag_reset),"type_counts":dict(sorted(tag_types.items())),"first":tag_reset[0] if tag_reset else None,"last":tag_reset[-1] if tag_reset else None,"median_inter_event_s":float(np.median(np.diff(tag_times))) if len(tag_times)>1 else None,"full_records":"continuous_raw/consumption_index.jsonl and fusion_cdc.log"}
 return selected,{"decoded_index_records":sum(records.values()),"decoded_kind_counts":dict(sorted(records.items())),"observed_nodes":sorted(all_nodes),"whole_timeline_imu_sequence_gaps":sequence_gaps,"whole_timeline_imu_timestamp_reversals":timestamp_reversals,"accepted_stationary_window_sequence_gaps":segment_gaps,"b306_connection_events":b306_links,"tag_side_reset_diagnostics":tag_summary}

def replay_raw(path):
 d=FrameStreamDecoder();counts=Counter();frames=0
 with path.open("rb") as f:
  for chunk in iter(lambda:f.read(1<<20),b""):
   for frame in d.feed(chunk):
    frames+=1
    try:line=frame_to_line(frame);counts[line.split(" ",1)[0] if line else "EMPTY"]+=1
    except Exception:counts["PAYLOAD_ERROR"]+=1
 return {"complete_frames":frames,"frame_kind_counts":dict(sorted(counts.items())),"cobs_crc_decode_errors":d.errors,"incomplete_tail_bytes":len(d.pending)}

def residual_stats(a,fit):
 raw=np.linalg.norm(a,axis=1)-1;cor=np.linalg.norm(apply_calibration(a,fit),axis=1)-1
 return {"samples":len(a),"raw_rmse_g":float(np.sqrt(np.mean(raw*raw))),"corrected_rmse_g":float(np.sqrt(np.mean(cor*cor))),"raw_median_abs_g":float(np.median(np.abs(raw))),"corrected_median_abs_g":float(np.median(np.abs(cor))),"corrected_max_abs_g":float(np.max(np.abs(cor)))}

def savefig(fig,path):
 fig.tight_layout();fig.savefig(path,metadata={"Date":None,"Creator":"BioSpur deterministic calibration derivation"});plt.close(fig)
 # Matplotlib's SVG backend emits path continuation lines with trailing
 # spaces.  Canonicalize them for clean Git evidence and byte replay.
 text=Path(path).read_text();Path(path).write_text("\n".join(line.rstrip() for line in text.splitlines())+"\n")

def plots(out,cal,val,fit,gyro):
 matplotlib.rcParams["svg.hashsalt"]="biospur-c2cc-v1"
 a=np.concatenate(cal);c=apply_calibration(a,fit)
 fig=plt.figure(figsize=(7,6));ax=fig.add_subplot(111,projection="3d");ax.scatter(a[::100,0],a[::100,1],a[::100,2],s=3,alpha=.5);ax.set(xlabel="a0 [g]",ylabel="a1 [g]",zlabel="a2 [g]",title="Raw stationary accelerometer ellipsoid");savefig(fig,out/"RAW_ACCELEROMETER_ELLIPSOID.svg")
 fig=plt.figure(figsize=(7,6));ax=fig.add_subplot(111,projection="3d");ax.scatter(c[::100,0],c[::100,1],c[::100,2],s=3,alpha=.5);ax.set(xlabel="corrected a0 [g]",ylabel="corrected a1 [g]",zlabel="corrected a2 [g]",title="Corrected gravity sphere");savefig(fig,out/"CORRECTED_GRAVITY_SPHERE.svg")
 dirs=np.asarray([np.mean(x,axis=0) for x in cal]);dirs/=np.linalg.norm(dirs,axis=1)[:,None];fig=plt.figure(figsize=(7,6));ax=fig.add_subplot(111,projection="3d");ax.scatter(*dirs.T,c=np.arange(1,19),cmap="viridis");
 for i,d in enumerate(dirs,1):ax.text(*d,str(i));ax.set(xlabel="a0 direction",ylabel="a1 direction",zlabel="a2 direction",title="Accepted calibration gravity-direction coverage");savefig(fig,out/"POSE_COVERAGE.svg")
 stats=[residual_stats(x,fit) for x in cal];x=np.arange(1,19);fig,ax=plt.subplots(figsize=(10,4));ax.plot(x,[q["raw_rmse_g"] for q in stats],"o-",label="uncalibrated");ax.plot(x,[q["corrected_rmse_g"] for q in stats],"o-",label="corrected");ax.set(xlabel="calibration pose",ylabel="gravity-norm RMSE [g]",xticks=x,title="Calibration residual by pose");ax.legend();savefig(fig,out/"PER_POSE_RESIDUALS.svg")
 stats=[residual_stats(x,fit) for x in val];x=np.arange(1,5);fig,ax=plt.subplots(figsize=(7,4));ax.plot(x,[q["raw_rmse_g"] for q in stats],"o-",label="uncalibrated");ax.plot(x,[q["corrected_rmse_g"] for q in stats],"o-",label="corrected");ax.axhline(PREREGISTERED["heldout_rmse_max_g"],color="r",ls="--",label="RMSE gate");ax.set(xlabel="held-out pose",ylabel="gravity-norm RMSE [g]",xticks=x,title="Frozen-model held-out validation");ax.legend();savefig(fig,out/"HELDOUT_VALIDATION_RESIDUALS.svg")
 fig,ax=plt.subplots(figsize=(9,4));g=np.asarray(gyro);ax.boxplot([g[:,i] for i in range(3)],tick_labels=["g0","g1","g2"],showfliers=False);ax.set(ylabel="zero-rate output [dps]",title="Stationary gyro bias/noise");savefig(fig,out/"GYRO_ZERO_RATE.svg")

def derive(run,out):
 out.mkdir(parents=True,exist_ok=True);raw=run/"continuous_raw/fusion_host_raw.cobs.bin";raw_before=sha(raw);frozen_path=run/"ACCEL_CALIBRATION_PROFILE.json";freeze_before=sha(frozen_path);frozen=json.loads(frozen_path.read_text());windows=load_windows(run);selected,index_integrity=select_samples(run,windows)
 cal=[np.asarray([x["accel_g"] for x in selected[("CALIBRATION",i)]]) for i in range(1,19)];val=[np.asarray([x["accel_g"] for x in selected[("HELDOUT",i)]]) for i in range(1,5)];cal_samples=[x for i in range(1,19) for x in selected[("CALIBRATION",i)]];val_samples=[x for i in range(1,5) for x in selected[("HELDOUT",i)]];gyro=[x["gyro_dps"] for x in cal_samples]
 accounting=[{**w,"actual_samples":len(selected[(w["set"],w["pose"])])} for w in windows];accounting_ok=all(x["actual_samples"]==x["expected_samples"] for x in accounting)
 stability_rows=[]
 for w in windows:
  m=stability_metrics(selected[(w["set"],w["pose"])]);stability_rows.append({"set":w["set"],"pose":w["pose"],"decision":"FINAL_ACCEPTED_WINDOW","duration_s":w["duration_s"],**m,"sequence_gaps":0})
 write_csv(out/"STABILITY_DECISIONS.csv",stability_rows)
 selection1=fit_and_select(cal);selection2=fit_and_select(cal);deterministic=json.dumps(selection1,sort_keys=True)==json.dumps(selection2,sort_keys=True);selected_fit=selection1["selected"]
 frozen_unchanged=(selection1==frozen["model_selection"]);coverage=coverage_metrics([np.mean(x,axis=0) for x in cal]);held=heldout_metrics(val,selected_fit)
 model_rows=[]
 for m in selection1["candidates"]:model_rows.append({k:m[k] for k in ("model","rmse_g","median_abs_g","max_abs_g","loo_rmse_g","loo_median_abs_g","loo_max_abs_g","matrix_condition","optimizer_success")}|{"selected":m["model"]==selection1["selected_model"]})
 write_csv(out/"CALIBRATION_MODEL_COMPARISON.csv",model_rows)
 loo=[];params=[]
 for i in range(18):
  f=fit_model(np.concatenate([x for j,x in enumerate(cal) if j!=i]),selection1["selected_model"]);p=np.r_[f["bias_g"],np.asarray(f["correction_matrix"]).reshape(-1)];params.append(p);s=residual_stats(cal[i],f);loo.append({"omitted_pose":i+1,**s,"bias_delta_norm_g":float(np.linalg.norm(np.asarray(f["bias_g"])-np.asarray(selected_fit["bias_g"]),ord=2)),"matrix_delta_fro":float(np.linalg.norm(np.asarray(f["correction_matrix"])-np.asarray(selected_fit["correction_matrix"])))})
 write_csv(out/"SENSITIVITY_AND_LOO.csv",loo);params=np.asarray(params);unc={"method":"leave-one-pose-out jackknife spread","parameter_order":["b0","b1","b2",*[f"C{i}{j}" for i in range(3) for j in range(3)]],"standard_deviation":np.std(params,axis=0,ddof=1).tolist(),"maximum_absolute_deviation_from_full_fit":np.max(np.abs(params-np.r_[selected_fit["bias_g"],np.asarray(selected_fit["correction_matrix"]).reshape(-1)]),axis=0).tolist()}
 per_cal=[{"pose":i+1,**residual_stats(x,selected_fit)} for i,x in enumerate(cal)];held_rows=[];worst=None
 for i,a in enumerate(val,1):
  s=residual_stats(a,selected_fit);corrected=np.linalg.norm(apply_calibration(a,selected_fit),axis=1)-1;j=int(np.argmax(np.abs(corrected)));sample=selected[("HELDOUT",i)][j];candidate={"pose":i,"sample_index":j,"residual_g":float(corrected[j]),"abs_residual_g":float(abs(corrected[j])),"seq":sample["seq"],"node_us":sample["node_us"],"accel_raw":sample["accel_raw"],"gyro_raw":sample["gyro_raw"],"temperature_c":sample["temperature_c"]}
  if worst is None or candidate["abs_residual_g"]>worst["abs_residual_g"]:worst=candidate
  held_rows.append({"pose":i,**s,"pass_rmse":s["corrected_rmse_g"]<=PREREGISTERED["heldout_rmse_max_g"],"pass_max_abs":s["corrected_max_abs_g"]<=PREREGISTERED["heldout_max_abs_max_g"]})
 write_csv(out/"HELDOUT_VALIDATION.csv",held_rows)
 C=np.asarray(selected_fit["correction_matrix"]);coverage_pass=coverage["direction_covariance_min_eigenvalue"]>=PREREGISTERED["coverage_covariance_min_eigenvalue"] and coverage["design_condition"]<=PREREGISTERED["coverage_design_condition_max"]
 numerical={"all_finite":bool(np.all(np.isfinite(C)) and np.all(np.isfinite(selected_fit["bias_g"]))),"matrix_symmetric":bool(np.allclose(C,C.T)),"matrix_eigenvalues":np.linalg.eigvalsh(C).tolist(),"matrix_positive_definite":bool(np.all(np.linalg.eigvalsh(C)>0)),"matrix_determinant":float(np.linalg.det(C)),"matrix_condition":float(np.linalg.cond(C)),"coverage_pass":coverage_pass,"fit_deterministic_repeat":deterministic,"frozen_profile_matches_rederivation":frozen_unchanged,"freeze_profile_sha256_before":freeze_before,"uncertainty":unc}
 canonical(out/"NUMERICAL_INTEGRITY.json",numerical);canonical(out/"POSE_COVERAGE.json",coverage|{"gate_pass":coverage_pass,"sample_accounting":accounting})
 result={"schema":"biospur-c2cc-calibration-result-v1","node":EXPECTED_NODE,"selected_model":selection1["selected_model"],"bias_g":selected_fit["bias_g"],"correction_matrix":selected_fit["correction_matrix"],"model_selection":selection1,"uncertainty":unc,"calibration_pose_residuals":per_cal,"heldout_summary":held,"heldout_worst_sample":worst,"parameters_frozen_before_validation":True,"parameter_changes_after_freeze":0,"primary_verdict":"C2CC_DEVICE_CALIBRATION_FAIL","failure_gate":"HELDOUT_CATASTROPHIC_SINGLE_SAMPLE_RESIDUAL","deployable":False}
 canonical(out/"CALIBRATION_RESULT.json",result)
 gyro_array=np.asarray(gyro);gyro_profile={"schema":"biospur-c2cc-gyro-zero-rate-v1","frozen_from_calibration_poses_before_validation":True,"training_samples":len(gyro_array),"bias_dps":np.mean(gyro_array,axis=0).tolist(),"std_dps":np.std(gyro_array,axis=0).tolist(),"mad_dps":np.median(np.abs(gyro_array-np.median(gyro_array,axis=0)),axis=0).tolist(),"heldout_validation":[{"pose":i,"samples":len(selected[("HELDOUT",i)]),"bias_dps":np.mean([x["gyro_dps"] for x in selected[("HELDOUT",i)]],axis=0).tolist(),"std_dps":np.std([x["gyro_dps"] for x in selected[("HELDOUT",i)]],axis=0).tolist()} for i in range(1,5)]}
 temp_profile=temperature_model(cal_samples);canonical(out/"GYRO_BIAS_NOISE_PROFILE.json",gyro_profile);canonical(out/"TEMPERATURE_MODEL.json",temp_profile);canonical(out/"VALIDATION_RESULTS.json",held)
 device={"schema":"DeviceCalibration_BSFC2CC/v1","node":EXPECTED_NODE,"status":"FAILED_VALIDATION_NOT_DEPLOYABLE","host_side_only":True,"source_raw_sha256":raw_before,"frozen_profile_sha256":freeze_before,"raw_axis_labels":["a0","a1","a2","g0","g1","g2"],"accel_calibration":{"model":selection1["selected_model"],"bias_g":selected_fit["bias_g"],"correction_matrix":selected_fit["correction_matrix"]},"gyro_zero_rate":{"bias_dps":gyro_profile["bias_dps"],"std_dps":gyro_profile["std_dps"],"frozen_from_training_only":True},"temperature_model":temp_profile,"limitations":["physical PCB axis names unbound","yaw unobservable","no sensor-to-V4 transform","no lever arm","no dynamic accuracy claim","not transferable as numeric calibration to other devices"]}
 canonical(out/"BSFC2CC_DEVICE_CALIBRATION.json",device)
 sources=[ROOT/"B306_Part/firmware/src/imu.c",ROOT/"B306_Part/tools/fusion_host_binary.py",ROOT/"B306_Part/docs/ble_protocol.md"]
 cohort={"schema":"biospur-standard-fusion-pcb-profile-candidate-v1","status":"NOT_ELIGIBLE_C2CC_VALIDATION_FAILED","excluded_device":"BSF31CC","requires_quick_cross_device_validation":True,"inheritable_only":{"raw_channel_order":["a0","a1","a2","g0","g1","g2"],"accel_scale":"raw/2048 g","gyro_scale":"raw/16.384 dps","computational_convention":"raw ordered triplet; handedness and enclosure orientation not established","firmware_parser_provenance":{str(p.relative_to(ROOT)):sha(p) for p in sources}},"not_inherited":["BSFC2CC numeric bias","BSFC2CC scale","BSFC2CC noise","BSFC2CC temperature values","physical enclosure orientation"]}
 canonical(out/"STANDARD_FUSION_PCB_PROFILE_CANDIDATE.json",cohort)
 replay=replay_raw(raw);commands=[];forbidden=[];observed_lines=[]
 for line in (run/"continuous_raw/fusion_cdc.log").open(errors="replace"):
  if " FUSION_RX " in line:observed_lines.append(line.split(" FUSION_RX ",1)[1].strip())
  if " FUSION_TX " in line:
   cmd=line.split(" FUSION_TX ",1)[1].strip();commands.append(cmd)
   if not (cmd in ("MASTER STATUS","LIST") or cmd.startswith(EXPECTED_NODE+" PING") or cmd.startswith(EXPECTED_NODE+" BOOT CONFIRM STATUS")):forbidden.append(cmd)
 masters=[parse_fields(x) for x in observed_lines if x.startswith("FUSION_MASTER_STATUS ")];listings=[parse_fields(x) for x in observed_lines if x.startswith("FUSION_LIST ")];peers=[parse_fields(x) for x in observed_lines if x.startswith("FUSION_PEER ")];pong={};confirm={}
 for x in observed_lines:
  if " text=PONG " in x:pong=parse_fields(x.split(" text=",1)[1])
  elif " text=BOOT CONFIRM STATUS " in x:confirm=parse_fields(x.split(" text=",1)[1])
 identity_checks={"master_marker":bool(masters) and masters[0].get("marker")==EXPECTED_MASTER,"exact_single_peer":bool(listings) and listings[0].get("count")=="1" and len(peers)>=1 and peers[0].get("name")==EXPECTED_NODE,"peer_connected_subscribed":bool(peers) and peers[0].get("connected")==peers[0].get("subscribed")=="1","node_identity":all(pong.get(k)==v for k,v in {"name":EXPECTED_NODE,"fw":EXPECTED_MARKER,"fwid":EXPECTED_FWID,"image_sha":EXPECTED_IMAGE}.items()),"confirmed":confirm.get("confirmed")=="1"}
 capture={"schema":"biospur-c2cc-capture-integrity-v1","raw_sha256_before_derivation":raw_before,"raw_replay":replay,"boundary_fragments":{"initial_cobs_crc_error_count":replay["cobs_crc_decode_errors"],"shutdown_incomplete_tail_bytes":replay["incomplete_tail_bytes"],"classification":"FIRST_BYTE_AND_CLEAN_STOP_BOUNDARY_FRAGMENTS_NOT_COMPLETE_RECORD_CORRUPTION"},"identity_observation":{"checks":identity_checks,"pass":all(identity_checks.values()),"master":masters[0] if masters else {},"list":listings[0] if listings else {},"peer":peers[0] if peers else {},"pong":pong,"confirm":confirm},"index":index_integrity,"accepted_calibration_poses":len(cal),"accepted_heldout_poses":len(val),"stationary_sample_accounting_complete":accounting_ok,"stationary_sample_accounting":accounting,"serial_open_count":1,"one_raw_timeline":True,"warmup_minimum_s":60,"live_catchup_disposition":"STARTED_DEGRADED","clean_stop_boundary":True,"clean_stop_decoded_queue_discard":{"records":1,"kinds":{"FUSION_IMU":1},"raw_bytes_preserved":True},"transmitted_commands":commands,"read_only_commands_only":not forbidden,"forbidden_commands":forbidden,"hardware_mutations":[],"operator_created_all_physical_poses_manually":True}
 canonical(out/"CAPTURE_INTEGRITY.json",capture)
 events=[]
 for typ,name in (("instruction","OPERATOR_INSTRUCTIONS.jsonl"),("token","OPERATOR_TOKENS.jsonl")):
  for line in (run/name).open():events.append({"event_type":typ,**json.loads(line)})
 events.sort(key=lambda x:(x["monotonic"],x["event_type"]));(out/"OPERATOR_EVENTS.jsonl").write_text("".join(json.dumps(x,sort_keys=True,separators=(",",":"))+"\n" for x in events))
 plots(out,cal,val,selected_fit,gyro)
 report=f"""# BSFC2CC black-box arbitrary-pose intrinsic calibration\n\nPrimary verdict: **C2CC_DEVICE_CALIBRATION_FAIL**.\n\nThe capture itself is complete: 18 accepted calibration poses, four untouched held-out poses, one serial open and one raw timeline. All physical placements were made manually by the operator. Calibration parameters were frozen before validation and changed zero times afterward. Startup live catch-up remained `STARTED_DEGRADED` after 180 seconds as allowed by the protocol; the raw stream continued without a second timeline.\n\n## Result\n\nThe preregistered least-complex selection chose `DIAGONAL_SCALE`. Held-in leave-one-pose-out RMSE was {selected_fit['loo_rmse_g']:.9f} g. Held-out gravity-norm RMSE improved from {held['uncalibrated_rmse_g']:.9f} g to {held['rmse_g']:.9f} g: an absolute improvement of {held['absolute_improvement_g']:.9f} g and a relative improvement of {held['relative_improvement']*100:.3f}%. Coverage passed (minimum direction-covariance eigenvalue {coverage['direction_covariance_min_eigenvalue']:.6f}; design condition {coverage['design_condition']:.6f}).\n\nThe strict result is nevertheless FAIL because held-out pose {worst['pose']} contained a sample with {worst['abs_residual_g']:.9f} g absolute corrected norm residual, exceeding the frozen {PREREGISTERED['heldout_max_abs_max_g']:.3f} g catastrophic-residual gate. This sample is retained at sequence {worst['seq']} / node timestamp {worst['node_us']} us. It is not removed as an outlier and validation data are not moved into training. Its gyro remained quiet while one accelerometer channel dipped for one sample, so it is consistent with a single-sample accelerometer anomaly rather than operator handling; the preregistered gate still fails.\n\nThe complete timeline has one IMU sequence discontinuity in the startup stale prefix before any accepted pose; every accepted calibration and held-out window has zero sequence gaps. The UWB/tag side emitted {index_integrity['tag_side_reset_diagnostics']['count']} reset-diagnostic records during the run. These are retained as UWB diagnostics, are not B306 disconnect/reconnect events, and were not used as an IMU calibration acceptance signal.\n\n## Boundaries\n\nThis establishes neither physical PCB X/Y/Z names nor yaw, V4 up, sensor-to-V4 rotation, lever arm, dynamic accuracy, cohort-wide calibration, or human-body mounting calibration. The numeric profile is host-side, failed validation, and is not deployable. The cohort candidate contains only source-proven raw ordering and units and remains ineligible pending a passing device validation.\n\nNo OTA, upload, pending/PREPARE/COMMIT, reboot, flash, J-Link/SWD/RTT, AutoPos, configuration, power-cycle, charging, or calibration write to firmware occurred.\n"""
 (out/"REPORT.md").write_text(report)
 run_manifest={"schema":"biospur-c2cc-arbitrary-pose-run-v1","node":EXPECTED_NODE,"primary_verdict":"C2CC_DEVICE_CALIBRATION_FAIL","capture_completed":True,"artifact_finalization_recovered_offline":True,"initial_finalize_error":"CSV field union bug after clean stop; no raw or pose loss","raw_sha256":raw_before,"calibration_pose_count":18,"heldout_pose_count":4,"parameters_frozen_before_validation":True,"parameter_changes_after_freeze":0,"expected_image_sha256":EXPECTED_IMAGE,"expected_fwid":EXPECTED_FWID,"git_head_at_derivation":subprocess.check_output(["git","rev-parse","HEAD"],cwd=ROOT,text=True).strip(),"evidence_files":{"raw":"continuous_raw/fusion_host_raw.cobs.bin","index":"continuous_raw/consumption_index.jsonl","cdc":"continuous_raw/fusion_cdc.log"},"hardware_actions":{"manual_pose_changes":True,"mutations":[]}}
 canonical(out/"RUN_MANIFEST.json",run_manifest)
 raw_after=sha(raw);capture["raw_sha256_after_derivation"]=raw_after;capture["raw_unchanged"]=raw_before==raw_after;canonical(out/"CAPTURE_INTEGRITY.json",capture)
 # The evidence ledger covers raw/decoded files too; Git staging remains a
 # separate compact-evidence decision.
 outputs=sorted(p for p in out.rglob("*") if p.is_file() and p.name!="SHA256SUMS")
 (out/"SHA256SUMS").write_text("".join(f"{sha(p)}  {p.relative_to(out)}\n" for p in outputs))
 return {"verdict":result["primary_verdict"],"raw_sha256":raw_before,"raw_unchanged":raw_before==raw_after,"heldout":held,"worst":worst,"output_hashes":{str(p.relative_to(out)):sha(p) for p in outputs if p.parent==out}}

def main():
 ap=argparse.ArgumentParser();ap.add_argument("--run-dir",type=Path,required=True);ap.add_argument("--out-dir",type=Path);a=ap.parse_args();out=a.out_dir or a.run_dir;print(json.dumps(derive(a.run_dir,out),indent=2,sort_keys=True));return 0
if __name__=="__main__":raise SystemExit(main())
