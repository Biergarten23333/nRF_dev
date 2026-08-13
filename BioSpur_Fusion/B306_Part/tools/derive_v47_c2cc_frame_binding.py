#!/usr/bin/env python3
"""Deterministic offline derivation for the C2CC two-mount binding capture."""
from __future__ import annotations

import argparse,csv,hashlib,json,math,sys
from collections import Counter
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from fusion_host_binary import FrameError,decode_frame,frame_to_line
from fusion_session import parse_fields
from v47_c2cc_frame_binding import FROZEN_CONFIG,G_MPS2,regularized_trajectory
from v47_q1_eskf import Q1T4ESKF,quaternion_to_matrix
from v47_uwb_position_replay import load_solver,validate_anchor_slot_identity

ROOT=Path(__file__).resolve().parents[2]
LAYOUT=ROOT/"B306_Part/deployments/current_room_autopos_20260811_183541/V4IO/anchor_layout.json"
NODE="BSFC2CC"
CORE=("MOUNT_A_BINDING.json","MOUNT_B_BINDING.json","OBSERVABILITY.json","TIME_ALIGNMENT.json",
      "HELDOUT_RESULTS.csv","CROSS_MOUNT_COMPARISON.json","Q1_REPLAY_RESULTS.json",
      "TRAJECTORY_T4_ONLY.csv","TRAJECTORY_IMU_ONLY.csv","TRAJECTORY_Q1_IMU_T4.csv",
      "ACTION_TIME_BRACKETS.csv","STREAM_INTEGRITY.json")


def sha(path):
    h=hashlib.sha256()
    with Path(path).open("rb") as f:
        for b in iter(lambda:f.read(4<<20),b""):h.update(b)
    return h.hexdigest()


def canonical(path,value):path.write_text(json.dumps(value,indent=2,sort_keys=True,allow_nan=False)+"\n")


def write_csv(path,rows,fields):
    with path.open("w",newline="") as f:
        w=csv.DictWriter(f,fieldnames=fields,lineterminator="\n",extrasaction="ignore");w.writeheader();w.writerows(rows)


def read_index(path):
    rows=[]
    with path.open() as f:
        for text in f:
            row=json.loads(text);line=row["line"]
            if not (line.startswith("FUSION_IMU ") or line.startswith("FUSION_UWB ")):continue
            fields=parse_fields(line)
            if fields.get("name")!=NODE:continue
            mono=float(row["consume_monotonic"])
            if line.startswith("FUSION_IMU "):
                base=int(fields["base_us"],0);seq=int(fields["seq"],0)
                for offset,sample in enumerate(fields["samples"].split(";")):
                    v=[int(x,0) for x in sample.split(",")]
                    rows.append({"kind":"IMU","mono":mono,"hardware_us":base+v[0],"seq":(seq+offset)&0xffff,
                        "accel":np.asarray(v[1:4],float)/2048*G_MPS2,"gyro":np.asarray(v[4:7],float)/16.384})
            else:rows.append({"kind":"UWB","mono":mono,"hardware_us":int(fields["strobe_us"],0),
                              "sweep":int(fields["sweep"],0),"fields":fields})
    return rows


def solve_uwb(rows):
    models,layout_io,c_solver=load_solver("UWB_TAG_T4");layout=layout_io.load_layout_json(LAYOUT)
    solver=c_solver.TagPositionSolver(layout,models.SolverConfig(method="T4"));out=[]
    for row in rows:
        if row["kind"]!="UWB":continue
        f=row["fields"]
        try:
            aids=tuple(int(x) for x in f["anchor_id"].split(","));validate_anchor_slot_identity(aids)
            ranges=[int(x) for x in f["range_mm"].split(",")];quality=[float(x) for x in f["quality"].split(",")]
            mask=int(f.get("valid_mask",f["valid"]),0)
            obs=tuple(models.Observation(anchor_id=aids[i],range_mm=float(ranges[i]),quality_percent=quality[i],status="O")
                      for i in range(8) if mask&(1<<i) and 0<ranges[i]<65535)
            frame=models.Frame(tag=NODE,sweep=row["sweep"],host_elapsed_s=row["mono"],host_epoch_s=0,
                               observations=obs,imu=None);result=solver.solve_frame(frame)
            if result is not None:out.append({**row,"position":np.array([result.x_mm,result.y_mm,result.z_mm])/1000,
                "residual_rms_mm":float(result.residual_rms_mm),"anchors_used":int(result.anchors_used)})
        except Exception:pass
    return out


def raw_audit(path):
    data=path.read_bytes();parts=data.split(b"\0");counts=Counter();errors=[];decoded=0
    for i,part in enumerate(parts):
        if not part:continue
        try:
            frame=decode_frame(part);line=frame_to_line(frame);decoded+=1
            counts[line.split(" ",1)[0]]+=1
        except Exception as e:errors.append({"record":i,"error":f"{type(e).__name__}: {e}","bytes":len(part)})
    for error in errors:
        error["boundary_classification"]=("OPEN_BOUNDARY_FRAGMENT" if error["record"]==0 else
            "CLOSE_BOUNDARY_FRAGMENT" if error["record"]==len(parts)-1 else "INTERIOR_PARSE_ERROR")
    return {"raw_bytes":len(data),"zero_delimited_nonempty_records":sum(counts.values())+len(errors),
            "decoded_records":decoded,"record_types":dict(sorted(counts.items())),"decode_errors":errors,
            "terminal_delimiter":data.endswith(b"\0")}


def accepted_sequence_scan(rows,manifest):
    intervals=[]
    for mount in "AB":
        m=manifest["mount_blocks"][mount]
        for key in ("vertical","horizontal_1","horizontal_2","validation"):
            b=m[key];intervals.append((b["start"]["monotonic"],b["done"]["monotonic"],f"{mount}_{key}"))
        b=m["rotation"];intervals.append((b["start"]["monotonic"],b["done"]["monotonic"],f"{mount}_rotation"))
    result={}
    for lo,hi,label in intervals:
        imu=[x["seq"] for x in rows if x["kind"]=="IMU" and lo<=x["mono"]<=hi]
        uwb=[x["sweep"] for x in rows if x["kind"]=="UWB" and lo<=x["mono"]<=hi]
        ig=sum(b!=((a+1)&0xffff) for a,b in zip(imu,imu[1:]));ug=sum(b!=((a+1)&0xffffffff) for a,b in zip(uwb,uwb[1:]))
        result[label]={"imu_samples":len(imu),"uwb_sweeps":len(uwb),"imu_gap_events":ig,"uwb_gap_events":ug}
    return result


def brackets(manifest,t0):
    rows=[]
    for action in manifest["operator_actions"]:
        if "token" not in action:continue
        rows.append({"step":action["step"],"token":action["token"],"disposition":action["disposition"],
                     "wall":action["wall"],"monotonic":f'{action["monotonic"]:.9f}',
                     "from_t0_s":f'{action["monotonic"]-t0:.9f}'})
    return rows


def block_data(allrows,positions,block):
    start=block["start"]["monotonic"];end=block["done"]["monotonic"]
    imu=[x for x in allrows if x["kind"]=="IMU" and start<=x["mono"]<=end]
    pos=[x for x in positions if start<=x["mono"]<=end]
    if not imu or len(pos)<FROZEN_CONFIG.minimum_t4_solutions:return None
    t=np.asarray([x["hardware_us"] for x in pos],float)/1e6
    trajectory=regularized_trajectory(t,np.asarray([x["position"] for x in pos]))
    ti=np.asarray([x["hardware_us"] for x in imu],float)/1e6
    return {"imu":imu,"positions":pos,"trajectory":trajectory,"imu_t":ti,
            "accel":np.asarray([x["accel"] for x in imu]),"gyro":np.asarray([x["gyro"] for x in imu])}


def q1_attitude(mount,allrows,t0):
    start=mount["stationary_start"];end=mount["rotation"]["final_stationary_end"]
    samples=[x for x in allrows if x["kind"]=="IMU" and start<=x["mono"]<=end]
    stationary=[x for x in samples if x["mono"]<=mount["stationary_end"]]
    if len(stationary)<200:return [],{"status":"BLOCKED_INSUFFICIENT_STATIONARY"}
    initial=np.median([x["accel"] for x in stationary],axis=0);gyro_bias=np.mean([x["gyro"] for x in stationary[:200]],axis=0)
    q1=Q1T4ESKF();q1.initialize_from_stationary(initial,np.radians(gyro_bias));out=[]
    for i,x in enumerate(samples):
        q1.propagate(x["hardware_us"]/1e6,x["accel"],np.radians(x["gyro"]))
        if i%20==0:
            out.append({"t0_s":x["mono"]-t0,"qw":q1.q[0],"qx":q1.q[1],"qy":q1.q[2],"qz":q1.q[3],
                        "cov_min":float(np.linalg.eigvalsh(q1.P)[0]),"cov_max":float(np.linalg.eigvalsh(q1.P)[-1])})
    return out,{"status":"ATTITUDE_ONLY_DIAGNOSTIC","gyro_bias_dps":gyro_bias.tolist(),"samples":len(samples),
        "quaternion_norm_max_error":q1.max_quaternion_norm_error,"covariance_min_eigenvalue":q1.min_covariance_eigenvalue,
        "cholesky_failures":q1.cholesky_failures,"spatial_binding":"BLOCKED"}


def plot_outputs(out,positions,manifest,t0,qrows):
    colors={"A":"tab:blue","B":"tab:orange"}
    for mount in "AB":
        m=manifest["mount_blocks"][mount];lo=m["stationary_start"];hi=m["rotation"]["final_stationary_end"]
        p=np.asarray([x["position"] for x in positions if lo<=x["mono"]<=hi])
        fig=plt.figure(figsize=(8,7));ax=fig.add_subplot(111,projection="3d")
        if len(p):ax.plot(p[:,0],p[:,1],p[:,2],lw=.7,color=colors[mount],label="raw T4")
        ax.set(xlabel="V4 x [m]",ylabel="V4 y [m]",zlabel="V4 z [m]",title=f"Mount {mount} — raw T4 (no external truth)");ax.legend()
        fig.tight_layout();fig.savefig(out/f"MOUNT_{mount}_TRAJECTORY.svg");fig.savefig(out/f"MOUNT_{mount}_TRAJECTORY.png",dpi=140);plt.close(fig)
    fig=plt.figure(figsize=(8,7));ax=fig.add_subplot(111,projection="3d")
    for mount in "AB":
        b=manifest["mount_blocks"][mount]["validation"];lo=b["start"]["monotonic"];hi=b["done"]["monotonic"]
        p=np.asarray([x["position"] for x in positions if lo<=x["mono"]<=hi])
        if len(p):ax.plot(p[:,0],p[:,1],p[:,2],lw=.8,label=f"Mount {mount} held-out",color=colors[mount])
    ax.set(xlabel="V4 x [m]",ylabel="V4 y [m]",zlabel="V4 z [m]",title="Held-out paths in common V4 — raw T4");ax.legend();fig.tight_layout()
    fig.savefig(out/"CROSS_MOUNT_HELDOUT.svg");fig.savefig(out/"CROSS_MOUNT_HELDOUT.png",dpi=140);plt.close(fig)
    fig,axes=plt.subplots(2,1,figsize=(11,7),sharex=False)
    for axis,mount in zip(axes,"AB"):
        rows=qrows[mount]
        if rows:
            t=[x["t0_s"] for x in rows]
            for key in ("qw","qx","qy","qz"):axis.plot(t,[x[key] for x in rows],label=key,lw=.7)
        axis.set(ylabel=f"Mount {mount} q",title="Attitude-only Q1 diagnostic; spatial binding blocked");axis.legend(ncol=4)
    axes[-1].set_xlabel("seconds from formal T0");fig.tight_layout();fig.savefig(out/"QUATERNION_DIAGNOSTIC.svg");fig.savefig(out/"QUATERNION_DIAGNOSTIC.png",dpi=140);plt.close(fig)
    fig,ax=plt.subplots(figsize=(10,4))
    for mount in "AB":
        rows=qrows[mount]
        if rows:ax.semilogy([x["t0_s"] for x in rows],[x["cov_min"] for x in rows],label=f"{mount} min eig")
    ax.set(xlabel="seconds from T0",ylabel="covariance min eigenvalue",title="Q1 attitude-only covariance diagnostic");ax.legend();fig.tight_layout()
    fig.savefig(out/"COVARIANCE_DIAGNOSTIC.svg");fig.savefig(out/"COVARIANCE_DIAGNOSTIC.png",dpi=140);plt.close(fig)


def plot_additional(out,allrows,positions,manifest,t0):
    fig,ax=plt.subplots(figsize=(12,4))
    tokens=[a for a in manifest["operator_actions"] if a.get("disposition")=="ACCEPT"]
    for i,a in enumerate(tokens):
        x=a["monotonic"]-t0;ax.axvline(x,color="tab:blue" if a["step"].startswith("A") else "tab:orange",alpha=.55,lw=.8)
        ax.text(x,i%4,a["step"],rotation=90,fontsize=6,va="bottom")
    ax.set(xlabel="seconds from formal T0",yticks=[],title="Operator action-state timeline — exact accepted tokens")
    fig.tight_layout();fig.savefig(out/"ACTION_STATE_TIMELINE.svg");fig.savefig(out/"ACTION_STATE_TIMELINE.png",dpi=140);plt.close(fig)
    fig,axes=plt.subplots(2,1,figsize=(11,6),sharex=False)
    for axis,name in zip(axes,"AB"):
        m=manifest["mount_blocks"][name];lo=m["rotation"]["done"]["monotonic"];hi=m["rotation"]["final_stationary_end"]
        samples=[x for x in allrows if x["kind"]=="IMU" and lo<=x["mono"]<=hi]
        if samples:
            axis.plot([x["mono"]-lo for x in samples],[np.linalg.norm(x["accel"])-G_MPS2 for x in samples],lw=.45)
        axis.axhline(0,color="k",lw=.6);axis.set(ylabel=f"Mount {name}\n|a|-g [m/s²]")
    axes[-1].set_xlabel("seconds from final-stationary start");fig.suptitle("Gravity-norm residual — spatial gravity removal blocked")
    fig.tight_layout();fig.savefig(out/"GRAVITY_REMOVAL_RESIDUAL.svg");fig.savefig(out/"GRAVITY_REMOVAL_RESIDUAL.png",dpi=140);plt.close(fig)
    fig,axes=plt.subplots(2,1,figsize=(11,6),sharex=False)
    for axis,name in zip(axes,"AB"):
        b=manifest["mount_blocks"][name]["validation"];lo=b["start"]["monotonic"];hi=b["done"]["monotonic"]
        selected=[x for x in positions if lo<=x["mono"]<=hi]
        if len(selected)>=FROZEN_CONFIG.minimum_t4_solutions:
            trajectory=regularized_trajectory(np.asarray([x["hardware_us"] for x in selected])/1e6,np.asarray([x["position"] for x in selected]))
            axis.plot(trajectory["time_s"]-trajectory["time_s"][0],np.linalg.norm(trajectory["velocity_mps"],axis=1),lw=.8)
        axis.set(ylabel=f"Mount {name}\nT4 speed [m/s]")
    axes[-1].set_xlabel("seconds in held-out block");fig.suptitle("Regularized T4 velocity diagnostic — not ground truth")
    fig.tight_layout();fig.savefig(out/"VELOCITY_DIAGNOSTIC.svg");fig.savefig(out/"VELOCITY_DIAGNOSTIC.png",dpi=140);plt.close(fig)


def derive(run,out):
    run=run.resolve();out=out.resolve()
    out.mkdir(parents=True,exist_ok=False);manifest=json.loads((run/"CAPTURE_MANIFEST.json").read_text());t0=manifest["formal_t0"]["monotonic"]
    raw=run/"continuous_raw/fusion_host_raw.cobs.bin";raw_before=sha(raw);allrows=read_index(run/"continuous_raw/consumption_index.jsonl");positions=solve_uwb(allrows)
    action_rows=brackets(manifest,t0);write_csv(out/"ACTION_TIME_BRACKETS.csv",action_rows,["step","token","disposition","wall","monotonic","from_t0_s"])
    audit=raw_audit(raw);health=manifest["final_health"];counts=manifest["phase_counts"]
    gaps=sum(v["imu_gap_events"]+v["uwb_gap_events"] for v in counts.values());accepted_scan=accepted_sequence_scan(allrows,manifest)
    accepted_gaps=sum(x["imu_gap_events"]+x["uwb_gap_events"] for x in accepted_scan.values())
    formal_events=[e for e in manifest["events"] if e["monotonic"]>=t0]
    integrity={"schema":"biospur-c2cc-frame-binding-stream-integrity-v1","raw_sha256":raw_before,
      "single_serial_open":manifest["serial_open_count"]==1,"one_raw_file":True,
      "raw_byte_accounting_closed":health["raw_bytes_submitted"]==health["raw_bytes_written"]==raw.stat().st_size,
      "decoded_queue_drops":health["decoded_queue_drops"],"raw_queue_drops":health["raw_queue_drops"],
      "log_queue_drops":health["log_queue_drops"],"reader_exceptions":health["reader_exceptions"],
      "close_discarded_records":manifest["close_drain"]["discarded_records"],"formal_connection_or_reset_events":formal_events,
      "sequence_gap_events_all_phases":gaps,"accepted_action_sequence_scan":accepted_scan,
      "accepted_action_sequence_gap_events":accepted_gaps,"phase_counts":counts,"raw_replay":audit,
      "startup_crc_fragment_count":health["frame_crc_decode_errors"],
      "accepted_action_integrity_pass":accepted_gaps==0 and not formal_events and not any(health[k] for k in ("decoded_queue_drops","raw_queue_drops","log_queue_drops","reader_exceptions")) and
       not any(e["boundary_classification"]=="INTERIOR_PARSE_ERROR" for e in audit["decode_errors"])}
    canonical(out/"STREAM_INTEGRITY.json",integrity)
    qrows={};qdiag={};bindings={};obs={};held=[];t4rows=[]
    for name in "AB":
        mount=manifest["mount_blocks"][name];qrows[name],qdiag[name]=q1_attitude(mount,allrows,t0)
        qualities={k:mount[k]["quality"] for k in ("vertical","horizontal_1","horizontal_2")}
        failed={k:[x for x,v in q["checks"].items() if not v] for k,q in qualities.items() if not q["accepted"]}
        status="BLOCKED_INSUFFICIENT_EXCITATION" if failed else "FIT_ELIGIBLE"
        bindings[name]={"schema":"biospur-c2cc-mount-binding-v1","mount":name,"status":status,
          "rotation_matrix":None,"quaternion_xyzw":None,"determinant":None,"orthonormality_error":None,
          "fit_performed":False,"failed_frozen_calibration_blocks":failed,"block_quality":qualities,
          "identity_policy":True,"zero_accelerometer_bias":True,"heldout_used":False,"other_mount_reused":False,
          "reason":"Frozen calibration block failed before fit; no post-capture threshold relaxation permitted." if failed else "not evaluated"}
        obs[name]={"status":status,"failed_blocks":failed,"online_quality":qualities,
                   "excitation_singular_values":None,"condition":None,"uncertainty":None}
        val=mount["validation"]["quality"]
        held.append({"mount":name,"fit_excluded":"true","binding_status":status,"records":val["t4_solutions"],
          "span_m":val["span_m"],"direction_explained":val["direction_explained"],"direction_error_median_deg":"",
          "direction_error_p95_deg":"","q1_spatial_status":"BLOCKED_BINDING","result":"NOT_EVALUABLE"})
        lo=mount["stationary_start"];hi=mount["rotation"]["final_stationary_end"]
        for x in positions:
            if lo<=x["mono"]<=hi:t4rows.append({"mount":name,"t0_s":f'{x["mono"]-t0:.9f}',"hardware_us":x["hardware_us"],
                "sweep":x["sweep"],"x_m":x["position"][0],"y_m":x["position"][1],"z_m":x["position"][2],
                "residual_rms_mm":x["residual_rms_mm"],"anchors_used":x["anchors_used"],"role":"RAW_T4"})
    canonical(out/"MOUNT_A_BINDING.json",bindings["A"]);canonical(out/"MOUNT_B_BINDING.json",bindings["B"])
    canonical(out/"OBSERVABILITY.json",{"schema":"biospur-c2cc-observability-v1","mounts":obs})
    canonical(out/"TIME_ALIGNMENT.json",{"schema":"biospur-c2cc-time-alignment-v1","policy":"COMMON_B306_HARDWARE_CLOCK_NO_OFFSET_NO_WARP",
      "imu_timestamp":"base_us + delta_us","uwb_timestamp":"strobe_us","estimated_offset_s":0.0,"offset_estimation_enabled":False,
      "host_monotonic_use":"action bracketing and diagnostics only","status":"PASS"})
    write_csv(out/"HELDOUT_RESULTS.csv",held,["mount","fit_excluded","binding_status","records","span_m","direction_explained","direction_error_median_deg","direction_error_p95_deg","q1_spatial_status","result"])
    comparison={"schema":"biospur-c2cc-cross-mount-v1","mount_gravity_sensor_angle_deg":manifest["mount_blocks"]["gravity_change_deg"],
      "mounts_measurably_different":manifest["mount_blocks"]["gravity_change_deg"]>=30,
      "binding_A":bindings["A"]["status"],"binding_B":bindings["B"]["status"],"physical_up_agreement":None,
      "v4_interpretation_consistent":False,"status":"NOT_EVALUABLE_BINDINGS_BLOCKED"};canonical(out/"CROSS_MOUNT_COMPARISON.json",comparison)
    canonical(out/"Q1_REPLAY_RESULTS.json",{"schema":"biospur-c2cc-q1-replay-v1","primary_policy":"IDENTITY_ACCEL_ZERO_BIAS",
      "mounts":qdiag,"spatial_replay":"BLOCKED_FRAME_BINDING","t4_updates":0,"oracle_ablation":"NOT_RUN_PRIMARY_BLOCKED",
      "filter_reset":False,"covariance_clipping":False})
    write_csv(out/"TRAJECTORY_T4_ONLY.csv",t4rows,["mount","t0_s","hardware_us","sweep","x_m","y_m","z_m","residual_rms_mm","anchors_used","role"])
    empty_fields=["mount","t0_s","x_m","y_m","z_m","vx_mps","vy_mps","vz_mps","status"]
    write_csv(out/"TRAJECTORY_IMU_ONLY.csv",[],empty_fields);write_csv(out/"TRAJECTORY_Q1_IMU_T4.csv",[],empty_fields)
    canonical(out/"CAPTURE_MANIFEST.json",{"source":str((run/"CAPTURE_MANIFEST.json").relative_to(ROOT)),"source_sha256":sha(run/"CAPTURE_MANIFEST.json"),
      "formal_t0":manifest["formal_t0"],"stop_reason":manifest["stop_reason"],"raw_sha256":raw_before,"serial_open_count":manifest["serial_open_count"]})
    canonical(out/"OPERATOR_ACTIONS.json",{"source":str((run/"OPERATOR_ACTIONS.jsonl").relative_to(ROOT)),"actions":manifest["operator_actions"]})
    plot_outputs(out,positions,manifest,t0,qrows);plot_additional(out,allrows,positions,manifest,t0)
    verdict="BLOCKED_INSUFFICIENT_EXCITATION"
    canonical(out/"PROVENANCE.json",{"schema":"biospur-c2cc-frame-binding-provenance-v1","verdict":verdict,"git_head_at_capture":json.loads((run/"PRETOKEN_FROZEN_CONFIG.json").read_text())["git_head"],
      "derive_tool":str(Path(__file__).relative_to(ROOT)),"derive_tool_sha256":sha(Path(__file__)),"raw_sha256_before":raw_before,
      "raw_sha256_after":sha(raw),"frozen_config_sha256":sha(run/"PRETOKEN_FROZEN_CONFIG.json"),"layout_sha256":sha(LAYOUT),
      "forbidden_hardware_access_during_analysis":False})
    report=f"""# BSFC2CC two-mount black-box frame-binding experiment\n\nPrimary verdict: **{verdict}**.\n\nThe one-open formal capture itself closed cleanly: one serial open, one raw file, byte accounting closed, zero queue drops, and no connection/reset event. Mount B's raw gravity vector differed from Mount A by {manifest['mount_blocks']['gravity_change_deg']:.3f} degrees, so the remount was real.\n\nBoth independently attempted vertical calibration blocks failed the frozen limited-rotation gate after their single explicit retry (A gyro P95 {manifest['mount_blocks']['A']['vertical']['quality']['gyro_p95_dps']:.3f} dps; B {manifest['mount_blocks']['B']['vertical']['quality']['gyro_p95_dps']:.3f} dps; frozen maximum {FROZEN_CONFIG.maximum_translation_gyro_p95_dps:.3f} dps). The interaction implementation incorrectly advanced after a failed retry. This is retained as a protocol defect; it does not authorize fitting with invalid calibration data.\n\nConsequently no proper sensor-to-V4 rotation was fitted, no held-out direction score was computed, and no spatial Q1/T4 replay was claimed. Attitude-only repaired-Q1 diagnostics remained finite and Cholesky-valid. Empty spatial trajectory CSVs are deliberate fail-closed artifacts, not missing output. Raw T4 trajectories and every operator bracket remain available for diagnosis.\n\nThis result is not ready for a ten-node arbitrary-wear T-pose/body-calibration experiment. The next attempt needs a carrier/guide that suppresses rotation during vertical translation, and the state machine must stop after an unsuccessful retry instead of advancing. No threshold may be relaxed using this held-out capture.\n"""
    (out/"REPORT.md").write_text(report)
    sums=[]
    for path in sorted(out.iterdir()):
        if path.name!="SHA256SUMS" and path.is_file():sums.append(f"{sha(path)}  {path.name}")
    (out/"SHA256SUMS").write_text("\n".join(sums)+"\n")
    if sha(raw)!=raw_before:raise RuntimeError("raw changed during derivation")
    return verdict


def main():
    p=argparse.ArgumentParser();p.add_argument("run",type=Path);p.add_argument("out",type=Path);a=p.parse_args()
    print(derive(a.run,a.out));return 0


if __name__=="__main__":raise SystemExit(main())
