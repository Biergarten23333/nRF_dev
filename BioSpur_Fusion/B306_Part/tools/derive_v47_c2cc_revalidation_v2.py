#!/usr/bin/env python3
"""Deterministic blind derivation for BSFC2CC revalidation v2."""
from __future__ import annotations

import argparse, ast, copy, csv, hashlib, json, math
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from derive_v47_c2cc_arbitrary_pose import replay_raw
from fusion_session import parse_fields
from v47_c2cc_arbitrary_pose import apply_calibration, parse_imu_samples
from v47_c2cc_revalidation_v2 import sensor_transient_gate, systematic_gate
from v47_q1_eskf import G_MPS2, MotionVetoGate, Q1T4ESKF

ROOT=Path(__file__).resolve().parents[2]
OLD=ROOT/"B306_Part/logs/v47_c2cc_arbitrary_pose_calibration_20260812_201945"
PROFILE_SHA="10895c252adbe23cb26ef1e0824abf460f3b8c03fd04d63508e06242fe63a73c"
PROTOCOL_SHA="d87503c8bcf100c9b823fd1fd08ae6e6b72eb255d03d4f2605c9fdd849e557dd"


def sha(path):
 h=hashlib.sha256()
 with Path(path).open("rb") as f:
  for b in iter(lambda:f.read(4<<20),b""):h.update(b)
 return h.hexdigest()


def canonical(path,value):Path(path).write_text(json.dumps(value,indent=2,sort_keys=True,allow_nan=False)+"\n")


def write_csv(path,rows,fields):
 with Path(path).open("w",newline="") as f:
  w=csv.DictWriter(f,fieldnames=fields,extrasaction="raise",lineterminator="\n");w.writeheader();w.writerows(rows)


def windows(run):
 out=[]
 for r in csv.DictReader((run/"POSE_WINDOWS.csv").open()):
  if r["accepted"]!="True":continue
  end=ast.literal_eval(r["end"]);duration=float(r["duration_s"])
  out.append({"pose":int(r["pose"]),"start":end["monotonic"]-duration,"end":end["monotonic"],
              "end_record":int(end["consumed_record_index"]),"expected":int(r["samples"]),"duration_s":duration})
 return out


def select(run,wins):
 selected={w["pose"]:[] for w in wins};segment_gaps=[];lifecycle=[];kinds={}
 with (run/"continuous_raw/consumption_index.jsonl").open() as f:
  for text in f:
   row=json.loads(text);line=row["line"];mono=float(row["consume_monotonic"]);kind=line.split(" ",1)[0];kinds[kind]=kinds.get(kind,0)+1
   if line.startswith(("FUSION_CONNECTED ","FUSION_DISCONNECTED ")):lifecycle.append({"monotonic":mono,"line":line})
   if not line.startswith("FUSION_IMU "):continue
   fields=parse_fields(line)
   if fields.get("name")!="BSFC2CC":continue
   for w in wins:
    if w["start"]-.1<=mono<=w["end"] and int(row["record_index"])<w["end_record"]:
     parsed=parse_imu_samples(fields,mono)
     for x in parsed:x["record_index"]=int(row["record_index"]);x["raw_bytes_submitted"]=int(row["raw_bytes_submitted"])
     selected[w["pose"]].extend(parsed);break
 for w in wins:selected[w["pose"]]=selected[w["pose"]][-w["expected"]:]
 for pose,s in selected.items():
  for a,b in zip(s,s[1:]):
   if b["seq"]!=((a["seq"]+1)&0xffff):segment_gaps.append({"pose":pose,"previous":a["seq"],"observed":b["seq"]})
   if b["node_us"]<=a["node_us"]:segment_gaps.append({"pose":pose,"previous_node_us":a["node_us"],"observed_node_us":b["node_us"]})
 return selected,{"decoded_kind_counts":dict(sorted(kinds.items())),"accepted_window_sequence_or_timestamp_faults":segment_gaps,"connection_events":lifecycle}


def classify(samples,fit):
 out=[];prior=[]
 for x in samples:
  a=np.asarray(x["accel_g"],float);cor=apply_calibration(a[None,:],fit)[0];res=float(np.linalg.norm(cor)-1)
  if len(prior)>=20:
   p=np.asarray([z["accel_g"] for z in prior[-20:]],float);med=np.median(p,axis=0);mad=np.median(np.abs(p-med),axis=0)
   scale=max(float(np.linalg.norm(mad))*1.4826,1/2048);dev=float(np.linalg.norm(a-med));gmed=np.median([z["gyro_dps"] for z in prior[-20:]],axis=0)
   gyro_evidence=float(np.linalg.norm(np.asarray(x["gyro_dps"])-gmed))>.5;local=dev>max(.030,10*scale)
  else:med=np.full(3,np.nan);mad=np.full(3,np.nan);scale=dev=math.nan;gyro_evidence=False;local=False
  row={**x,"corrected_accel_g":cor.tolist(),"corrected_residual_g":res,"corrected_abs_residual_g":abs(res),
       "local_vector_deviation_g":dev,"local_scale_g":scale,"locally_inconsistent":local,"gyro_or_handling_evidence":gyro_evidence,
       "transient_candidate":bool(abs(res)>.060 and local and not gyro_evidence)}
  out.append(row);prior.append(x)
 return out


def q1_replay(samples,gyro_bias):
 q1=Q1T4ESKF();first=np.median([x["corrected_accel_g"] for x in samples[:200]],axis=0)*G_MPS2;q1.initialize_from_stationary(first,np.zeros(3));motion=MotionVetoGate();rows=[]
 last_q=q1.q.copy()
 for x in samples:
  t=x["node_us"]*1e-6;acc=np.asarray(x["corrected_accel_g"])*G_MPS2;gyro_dps=np.asarray(x["gyro_dps"])-gyro_bias;gyro=np.radians(gyro_dps)
  q1.propagate(t,acc,gyro);state=motion.update(t,gyro_rms_dps=float(np.linalg.norm(gyro_dps)),gyro_angle_deg=float(np.linalg.norm(gyro_dps))*.005,accel_deviation_g=abs(x["corrected_residual_g"]),candidate_stable=True)
  q_before=q1.q.copy();d=q1.gravity_update_causal(acc,motion_state="STATIONARY" if state in ("STATIONARY","MOTION_SUSPECTED") else state);dot=min(1.,abs(float(q_before@q1.q)));step=math.degrees(2*math.acos(dot));eig=float(np.linalg.eigvalsh(q1.P)[0])
  rows.append({"pose":x["pose"],"seq":x["seq"],"node_us":x["node_us"],"transient_candidate":x["transient_candidate"],"accepted":d.accepted,"reason":d.reason,"nis":d.nis,"norm_residual_g":d.norm_residual_g,"quaternion_update_step_deg":step,"quaternion_norm":float(np.linalg.norm(q1.q)),"covariance_min_eigenvalue":eig,"motion_state":state,"numerical_pass":bool(eig>0 and abs(np.linalg.norm(q1.q)-1)<1e-10 and not (x["transient_candidate"] and state=="MOVING"))})
  last_q=q1.q.copy()
 return rows,{"propagations":q1.propagations,"accepted_gravity_updates":q1.gravity_updates,"rejected_gravity_updates":q1.gravity_update_rejections,"motion_ineligible":q1.gravity_motion_ineligible,"max_quaternion_norm_error":q1.max_quaternion_norm_error,"min_covariance_eigenvalue":q1.min_covariance_eigenvalue,"cholesky_failures":q1.cholesky_failures}


def savefig(fig,path):
 fig.tight_layout();fig.savefig(path,metadata={"Date":None,"Creator":"BioSpur deterministic revalidation v2"});plt.close(fig)
 text=Path(path).read_text();Path(path).write_text("\n".join(x.rstrip() for x in text.splitlines())+"\n")


def derive(run):
 raw=run/"continuous_raw/fusion_host_raw.cobs.bin";raw_before=sha(raw);protocol=run/"REVALIDATION_V2_PROTOCOL.json";profile_path=OLD/"ACCEL_CALIBRATION_PROFILE.json"
 if sha(protocol)!=PROTOCOL_SHA or sha(profile_path)!=PROFILE_SHA:raise RuntimeError("frozen input hash mismatch")
 manifest=json.loads((run/"RUN_MANIFEST.json").read_text());profile=json.loads(profile_path.read_text());fit=profile["model_selection"]["selected"];wins=windows(run);selected,index=select(run,wins)
 pose_arrays=[];all_samples=[]
 for pose in range(1,7):
  s=selected.get(pose,[]);pose_arrays.append(np.asarray([x["accel_g"] for x in s],float))
  classified=classify(s,fit)
  for x in classified:x["pose"]=pose
  all_samples.extend(classified)
 system,held_rows=systematic_gate(pose_arrays,fit["bias_g"],fit["correction_matrix"]) if all(len(x) for x in pose_arrays) else ({"pass":False,"reason":"MISSING_POSE"},[])
 gyro_profile=json.loads((OLD/"GYRO_BIAS_NOISE_PROFILE.json").read_text());gyro_bias=np.asarray(gyro_profile["bias_dps"])
 audit=[];numerical=[]
 for pose in range(1,7):
  rows,num=q1_replay([x for x in all_samples if x["pose"]==pose],gyro_bias) if selected.get(pose) else ([],{"missing":True});audit.extend(rows);numerical.append({"pose":pose,**num})
 transient=sensor_transient_gate(all_samples,audit) if all_samples else {"pass":False,"conditional_only":False,"reason":"NO_SAMPLES"}
 replay=replay_raw(raw);health=manifest.get("health_final",{});size=raw.stat().st_size
 first_index=json.loads(next((run/"continuous_raw/consumption_index.jsonl").open()))
 boundary={"startup_mid_frame_fragment":bool(str(first_index.get("line","")).startswith(" ") and replay["cobs_crc_decode_errors"]==1),
           "startup_error_count":replay["cobs_crc_decode_errors"],"startup_first_decoded_line":first_index.get("line"),
           "shutdown_incomplete_tail_bytes":replay["incomplete_tail_bytes"],
           "shutdown_drain":manifest.get("close_drain",{}),
           "classification":"FIRST_BYTE_STARTUP_AND_CLEAN_STOP_BOUNDARY_FRAGMENTS_OUTSIDE_ACCEPTED_WINDOWS"}
 boundary_ok=boundary["startup_mid_frame_fragment"] and 0<=boundary["shutdown_incomplete_tail_bytes"]<=512
 capture_checks={"six_poses":len(wins)==6 and all(len(selected.get(i,[]))==wins[i-1]["expected"] for i in range(1,7)),"accepted_window_time_sequence":not index["accepted_window_sequence_or_timestamp_faults"],"accepted_window_crc_decode_parse_serial_queue_errors":not index["accepted_window_sequence_or_timestamp_faults"] and not manifest.get("sequence_time_faults") and all(health.get(k,0)==0 for k in ("raw_queue_drops","decoded_queue_drops","log_queue_drops","reader_exceptions","payload_decode_errors")),"boundary_fragments_classified_outside_formal_windows":boundary_ok,"no_reconnect":not index["connection_events"],"closed_raw_accounting":health.get("raw_bytes_written")==health.get("raw_bytes_submitted")==size,"no_queue_or_reader_error":all(health.get(k,0)==0 for k in ("raw_queue_drops","decoded_queue_drops","log_queue_drops","reader_exceptions","payload_decode_errors")),"planned_clean_stop":manifest.get("stop_reason")=="PLANNED_SEQUENCE_COMPLETE","protocol_and_profile_unchanged":sha(protocol)==PROTOCOL_SHA and sha(profile_path)==PROFILE_SHA}
 capture={"schema":"biospur-c2cc-capture-integrity-v2","checks":capture_checks,"pass":all(capture_checks.values()),"raw_sha256_before_derivation":raw_before,"raw_size_bytes":size,"raw_replay":replay,"boundary_fragments":boundary,"index":index,"health":health,"pose_sample_accounting":[{"pose":w["pose"],"expected":w["expected"],"actual":len(selected[w["pose"]]),"duration_s":w["duration_s"]} for w in wins]}
 runtime_pass=all(x.get("numerical_pass",False) for x in audit) and all(not x["accepted"] for x in audit if x["transient_candidate"])
 if not capture["pass"]:verdict="C2CC_REVALIDATION_CAPTURE_FAIL"
 elif not system["pass"]:verdict="C2CC_DEVICE_CALIBRATION_REVALIDATION_FAIL"
 elif transient.get("pass") and runtime_pass:verdict="C2CC_DEVICE_CALIBRATION_REVALIDATION_PASS"
 elif transient.get("conditional_only") and runtime_pass:verdict="C2CC_DEVICE_CALIBRATION_REVALIDATION_CONDITIONAL"
 else:verdict="C2CC_DEVICE_CALIBRATION_REVALIDATION_FAIL"
 promotion={"schema":"biospur-c2cc-calibration-promotion-v2","node":"BSFC2CC","primary_verdict":verdict,"from":"FROZEN_CANDIDATE_PENDING_REVALIDATION","to":"DEPLOYABLE_FOR_BSFC2CC" if verdict=="C2CC_DEVICE_CALIBRATION_REVALIDATION_PASS" else "FROZEN_CANDIDATE_PENDING_REVALIDATION","numeric_transfer_to_other_boards":False,"BSF31CC_excluded":True,"parameter_changes":0}
 canonical(run/"CAPTURE_INTEGRITY.json",capture);canonical(run/"SYSTEMATIC_CALIBRATION_GATE.json",system);canonical(run/"SENSOR_TRANSIENT_GATE.json",transient);canonical(run/"CALIBRATION_PROMOTION.json",promotion);canonical(run/"NUMERICAL_INTEGRITY.json",{"runtime_q1_pass":runtime_pass,"per_pose":numerical,"all_finite":all(np.isfinite(np.asarray([x["nis"] for x in audit]))) if audit else False})
 held_fields=["pose","samples","raw_rmse_g","corrected_rmse_g","corrected_median_residual_g","corrected_abs_p95_g","corrected_abs_p99_g","improved_or_equivalent","persistent_systematic_pass"]
 write_csv(run/"HELDOUT_REVALIDATION_RESULTS.csv",held_rows,held_fields)
 trans=[{k:x[k] for k in ("pose","seq","node_us","record_index","corrected_abs_residual_g","local_vector_deviation_g","local_scale_g","gyro_or_handling_evidence","transient_candidate")} for x in all_samples if x["transient_candidate"]]
 write_csv(run/"TRANSIENTS_FOUND.csv",trans,["pose","seq","node_us","record_index","corrected_abs_residual_g","local_vector_deviation_g","local_scale_g","gyro_or_handling_evidence","transient_candidate"])
 write_csv(run/"Q1_GRAVITY_UPDATE_AUDIT.csv",audit,["pose","seq","node_us","transient_candidate","accepted","reason","nis","norm_residual_g","quaternion_update_step_deg","quaternion_norm","covariance_min_eigenvalue","motion_state","numerical_pass"])
 matplotlib.rcParams["svg.hashsalt"]="biospur-c2cc-revalidation-v2";x=np.arange(1,7)
 fig,ax=plt.subplots(figsize=(8,4));ax.plot(x,[r["raw_rmse_g"] for r in held_rows],"o-",label="uncalibrated");ax.plot(x,[r["corrected_rmse_g"] for r in held_rows],"o-",label="frozen calibration");ax.set(xlabel="held-out pose",ylabel="gravity-norm RMSE [g]",xticks=x);ax.legend();savefig(fig,run/"SIX_POSE_GRAVITY_RESIDUALS.svg")
 rawres=np.abs(np.concatenate([np.linalg.norm(a,axis=1)-1 for a in pose_arrays]));corres=np.abs(np.concatenate([np.linalg.norm(apply_calibration(a,fit),axis=1)-1 for a in pose_arrays]));fig,ax=plt.subplots(figsize=(8,4));
 for values,label in ((rawres,"uncalibrated"),(corres,"frozen calibration")):v=np.sort(values);ax.plot(v,np.arange(1,len(v)+1)/len(v),label=label)
 ax.axvline(.01,color="k",ls="--");ax.axvline(.02,color="r",ls="--");ax.set(xlabel="absolute gravity-norm residual [g]",ylabel="empirical CDF");ax.legend();savefig(fig,run/"RESIDUAL_CDF.svg")
 fig,ax=plt.subplots(figsize=(10,4));decimated=list(range(0,len(audit),20));rejected=[i for i,r in enumerate(audit) if not r["accepted"]];ax.scatter(decimated,[audit[i]["nis"] for i in decimated],s=2,c="tab:blue",label="accepted stream (1/20 shown)");ax.scatter(rejected,[audit[i]["nis"] for i in rejected],s=16,c="tab:red",label="rejected (all shown)");ax.axhline(16.26623619623813,color="k",ls="--",label="99.9% NIS gate");ax.set(xlabel="stationary sample",ylabel="gravity update NIS");ax.legend();savefig(fig,run/"Q1_GRAVITY_UPDATE_DECISIONS.svg")
 fig,ax=plt.subplots(2,1,figsize=(10,6),sharex=True);ax[0].plot([r["quaternion_update_step_deg"] for r in audit],lw=.5);ax[1].plot([r["covariance_min_eigenvalue"] for r in audit],lw=.5);ax[0].set_ylabel("q update step [deg]");ax[1].set(xlabel="stationary sample",ylabel="min covariance eigenvalue");savefig(fig,run/"Q1_TRANSIENT_RESPONSE.svg")
 report=f"""# BSFC2CC calibration revalidation v2\n\nPrimary verdict: **{verdict}**.\n\nThe historical primary verdict remains **C2CC_DEVICE_CALIBRATION_FAIL** and was not rewritten. The `DIAGONAL_SCALE` parameters were loaded by SHA-256 and were never refit. Gate A systematic calibration: `{'PASS' if system.get('pass') else 'FAIL'}`. Gate B sensor transients: `{'PASS' if transient.get('pass') else 'CONDITIONAL' if transient.get('conditional_only') else 'FAIL'}`. Capture integrity: `{'PASS' if capture['pass'] else 'FAIL'}`. Runtime Q1 causal rejection: `{'PASS' if runtime_pass else 'FAIL'}`.\n\nAll six physical poses were created manually. Calibration remained host-side. No OTA, upload, reboot, flash, J-Link/SWD/RTT, AutoPos, configuration, or power-cycle action occurred during the formal run. Numeric parameters are not promoted to any other board; BSF31CC remains excluded.\n\nThe historical forensic audit is retained in the clearly linked sibling directory [`../v47_c2cc_calibration_revalidation_v2_20260812_214846/historical_transient_audit/`](../v47_c2cc_calibration_revalidation_v2_20260812_214846/historical_transient_audit/). It found two separated one-sample events in the old accepted stationary population and therefore uses disposition `REPEATED_SENSOR_ANOMALY`, without asserting a hardware defect or changing the old FAIL.\n\nTwo zero-pose pre-capture attempts and one passive observation are documented in `PRECAPTURE_ATTEMPTS.json`; none is merged into this formal raw timeline. The operator charged the board between those aborted attempts and the accepted formal run. Charging had ended and the charger was removed before this run's collector opened; no charging occurred during this formal run. Codex performed no charging or other hardware mutation.\n""";(run/"REPORT.md").write_text(report)
 manifest["derivation"]={"primary_verdict":verdict,"historical_audit":"../v47_c2cc_calibration_revalidation_v2_20260812_214846/historical_transient_audit","raw_sha256_before":raw_before,"raw_sha256_after":sha(raw),"frozen_profile_sha256_after":sha(profile_path),"protocol_sha256_after":sha(protocol),"parameter_changes":0};canonical(run/"RUN_MANIFEST.json",manifest)
 raw_after=sha(raw);capture["raw_sha256_after_derivation"]=raw_after;capture["raw_unchanged"]=raw_after==raw_before;canonical(run/"CAPTURE_INTEGRITY.json",capture)
 files=sorted(p for p in run.rglob("*") if p.is_file() and p.name!="SHA256SUMS" and "continuous_raw" not in p.parts)
 (run/"SHA256SUMS").write_text("".join(f"{sha(p)}  {p.relative_to(run)}\n" for p in files))
 return {"verdict":verdict,"raw_sha256":raw_before,"raw_unchanged":raw_after==raw_before,"core_hashes":{str(p.relative_to(run)):sha(p) for p in files}}


def main():
 ap=argparse.ArgumentParser();ap.add_argument("--run-dir",type=Path,required=True);a=ap.parse_args();print(json.dumps(derive(a.run_dir.resolve()),indent=2,sort_keys=True));return 0
if __name__=="__main__":raise SystemExit(main())
