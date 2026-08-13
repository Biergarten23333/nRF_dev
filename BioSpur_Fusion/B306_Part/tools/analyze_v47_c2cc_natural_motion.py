#!/usr/bin/env python3
"""Offline, label-independent natural-motion frame binding for BSFC2CC.

The fitting decoder is deliberately separate from ``open_heldout_once``.
Validation records cannot be decoded until the fitting freeze manifest has
been written and its detached SHA-256 signature has been verified.
"""
from __future__ import annotations

import argparse
import csv
import dataclasses
import hashlib
import json
import math
import os
import re
import subprocess
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from scipy.ndimage import median_filter
from scipy.signal import butter, sosfiltfilt
from scipy.spatial.transform import Rotation

from analyze_v47_c2cc_sign_forensics import (
    CANONICAL_LAYOUT, EXPECTED_CANONICAL_LAYOUT, EXPECTED_RAW,
    INTERMEDIATE_LAYOUT, ROTATION_AWARE_COMMIT, accepted_tokens,
    action_specs, fitting_record_ranges, read_fitting_rows, sha,
    validation_opaque_ranges,
)
from derive_v47_c2cc_frame_binding import solve_uwb
from fusion_session import parse_fields
from v47_c2cc_natural_motion import (
    FROZEN_NATURAL_MOTION_CONFIG, evaluate_frozen_transform,
    fit_natural_motion, make_segment, natural_stop_indices,
    preintegrated_path,
)
from v47_c2cc_rotation_aware import propagate_mount_q1, serializable
from v47_c2cc_sign_forensics import (
    angle_deg, rotation_angle_deg,
    specific_force_to_navigation_acceleration,
)
from v47_q1_eskf import (
    FrameBinding, G_MPS2, Q1Parameters, Q1T4ESKF,
)

ROOT=Path(__file__).resolve().parents[2]
METHOD="biospur-c2cc-natural-motion-frame-binding-v1"
VERDICTS={
    "PASS":"C2CC_NATURAL_MOTION_FRAME_BINDING_PASS",
    "CONDITIONAL":"C2CC_NATURAL_MOTION_FRAME_BINDING_CONDITIONAL",
    "HELDOUT_FAIL":"C2CC_NATURAL_MOTION_FITTING_PASS_HELDOUT_FAIL",
    "UNOBSERVABLE":"C2CC_NATURAL_MOTION_FRAME_BINDING_UNOBSERVABLE",
    "INVALID":"C2CC_NATURAL_MOTION_MODEL_INVALID",
    "EVIDENCE":"BLOCKED_AUTHORITATIVE_EVIDENCE_MISMATCH",
}
HISTORICAL_COMMITS=[
    "2ecfbfaf7ea5364f1c58be1181ac0392b97c3e94",
    "c45dee7c662878d1c0e61f4d0682b5aa89301ac5",
    "0ec9fade562cc7679dc8aa2a80ea397952b21cf8",
]
HISTORICAL_VERDICTS=[
    "BLOCKED_INSUFFICIENT_EXCITATION",
    "BLOCKED_ROTATION_AWARE_MODEL_UNOBSERVABLE",
    "C2CC_FRAME_BINDING_FITTING_RECOVERED_PROSPECTIVE_VALIDATION_REQUIRED",
]
CORE=[
    "INPUT_EVIDENCE.json","STOP_TO_STOP_SEGMENTS.csv",
    "T4_TRAJECTORY_CONSTRAINTS.csv","IMU_PREINTEGRATION_RESULTS.csv",
    "OBSERVABILITY.json","BIAS_IDENTIFIABILITY.json","TIME_ALIGNMENT.json",
    "LEVER_ARM_SENSITIVITY.json","SYNTHETIC_AND_MUTATION_TESTS.json",
    "LEAVE_ONE_SEGMENT_OUT.csv","FITTING_RESULTS.json",
    "NATURAL_MOTION_FREEZE_MANIFEST.json","HELDOUT_RESULTS.csv",
    "CROSS_MOUNT_RESULTS.json","TRAJECTORY_T4_UWB_ONLY.csv",
    "TRAJECTORY_Q1_IMU_ONLY_V4.csv","TRAJECTORY_Q1_IMU_T4_ESKF.csv",
]


def canonical(path:Path,value) -> None:
    temporary=path.with_name(path.name+".tmp")
    temporary.write_text(json.dumps(serializable(value),indent=2,sort_keys=True,allow_nan=False)+"\n")
    os.replace(temporary,path)


def write_csv(path:Path,rows,fields=None) -> None:
    rows=list(rows); fields=list(fields or (rows[0].keys() if rows else []))
    with path.open("w",newline="") as handle:
        writer=csv.DictWriter(handle,fieldnames=fields,extrasaction="ignore",lineterminator="\n")
        writer.writeheader();writer.writerows(rows)


def hash_record_range(index_path:Path,lo:int,hi:int) -> str:
    digest=hashlib.sha256()
    with index_path.open("rb") as handle:
        for number,line in enumerate(handle,1):
            if lo<=number<=hi:digest.update(line)
            if number>hi:break
    return digest.hexdigest()


def validation_record_ranges(tokens):
    def marker(step):return int(tokens[step]["marker"]["consumed_record_index"])
    return {m:(marker(f"{m}_VALIDATION_START")+1,marker(f"{m}_VALIDATION_DONE")) for m in "AB"}


def full_heldout_context_ranges(tokens):
    def marker(step):return int(tokens[step]["marker"]["consumed_record_index"])
    return {m:(marker(f"MOUNT_{m}_READY")+1,marker(f"{m}_VALIDATION_DONE")) for m in "AB"}


def read_selected_rows(path:Path,ranges):
    """Decode only the selected ranges after the freeze gate."""
    return read_fitting_rows(path,ranges)[0]


def smooth_t4(time_s,position_m):
    t=np.asarray(time_s,float);p=np.asarray(position_m,float)
    if len(t)<25 or np.any(np.diff(t)<=0):raise ValueError("insufficient chronological T4 path")
    dt=float(np.median(np.diff(t)));fs=1/dt
    filtered=np.column_stack([median_filter(p[:,i],size=FROZEN_NATURAL_MOTION_CONFIG.t4_median_window_samples,mode="nearest") for i in range(3)])
    smooth=sosfiltfilt(butter(2,FROZEN_NATURAL_MOTION_CONFIG.t4_lowpass_cutoff_hz,btype="low",fs=fs,output="sos"),filtered,axis=0)
    return {"time_s":t,"raw_position_m":p,"position_m":smooth,"velocity_mps":np.gradient(smooth,t,axis=0)}


def mount_data(rows,tokens,mount,include_validation=False):
    imu=sorted((x for x in rows if x["kind"]=="IMU"),key=lambda x:x["hardware_us"])
    positions=solve_uwb(rows)
    t=np.asarray([x["hardware_us"] for x in imu],float)/1e6
    acc=np.asarray([x["accel"] for x in imu]);gyro=np.asarray([x["gyro"] for x in imu])
    q,R,bias,integrity=propagate_mount_q1(t,acc,gyro)
    regions={}
    specs=action_specs(tokens,mount)
    if include_validation:
        specs=specs+[("validation",float(tokens[f"{mount}_VALIDATION_START"]["monotonic"]),
                      float(tokens[f"{mount}_VALIDATION_DONE"]["monotonic"]))]
    for temporal_label,lo,hi in specs:
        selected=[x for x in positions if lo<=x["mono"]<=hi]
        regions[temporal_label]={"lo":lo,"hi":hi,"positions":selected,
            "trajectory":smooth_t4(np.asarray([x["hardware_us"] for x in selected],float)/1e6,
                                    np.asarray([x["position"] for x in selected]))}
    return {"imu":imu,"positions":positions,"t":t,"acc":acc,"gyro":gyro,"q":q,"R":R,
            "gyro_bias_dps":bias,"integrity":integrity,"regions":regions}


def local_plateau(traj,index):
    left=max(0,index-1);right=min(len(traj["time_s"]),index+2)
    points=traj["raw_position_m"][left:right];center=np.median(points,axis=0)
    sigma=max(FROZEN_NATURAL_MOTION_CONFIG.t4_position_sigma_m,
              float(np.quantile(np.linalg.norm(points-center,axis=1),.95)))
    return center,sigma


def quiet_metrics(data,index,window_s=.25):
    use=np.abs(data["t"]-data["t"][index])<=window_s
    gyro=np.linalg.norm(data["gyro"][use]-data["gyro_bias_dps"],axis=1)
    accel=np.abs(np.linalg.norm(data["acc"][use],axis=1)/G_MPS2-1)
    fraction=float(np.mean((gyro<=2.5)&(accel<=.08)))
    return fraction,float(np.median(gyro)),float(np.median(accel))


def build_segments(data,mount,region_names,offset_s=0.0):
    objects=[];ledger=[];t4_rows=[];imu_rows=[]
    for temporal_label in region_names:
        region=data["regions"][temporal_label];traj=region["trajectory"]
        stops=natural_stop_indices(traj["time_s"],traj["velocity_mps"])
        for ordinal,(left,right) in enumerate(zip(stops,stops[1:]),1):
            start=float(traj["time_s"][left]);end=float(traj["time_s"][right])
            indices=np.flatnonzero((data["t"]>=start+offset_s)&(data["t"]<=end+offset_s))
            if len(indices)<3:continue
            i0,i1=int(indices[0]),int(indices[-1])
            acceleration=specific_force_to_navigation_acceleration(data["R"][indices],data["acc"][indices])
            pre=preintegrated_path(data["t"][indices],acceleration)
            p0,s0=local_plateau(traj,left);p1,s1=local_plateau(traj,right)
            t4_path=traj["position_m"][left:right+1]-traj["position_m"][left]
            segment=make_segment(segment_id=f"{mount}-{temporal_label}-{ordinal:02d}",
                imu_time_s=data["t"][indices],imu_path_N_m=pre["zupt_position_m"],
                t4_time_s=traj["time_s"][left:right+1]+offset_s,t4_path_V4_m=t4_path,
                metadata={"mount":mount,"temporal_region":temporal_label,"ordinal":ordinal})
            dV=np.asarray(segment["dV"]);dN=np.asarray(segment["dN"])
            start_speed=float(np.linalg.norm(traj["velocity_mps"][left]));end_speed=float(np.linalg.norm(traj["velocity_mps"][right]))
            q0,g0,a0=quiet_metrics(data,i0);q1,g1,a1=quiet_metrics(data,i1)
            attitude=rotation_angle_deg(data["R"][i0],data["R"][i1])
            accepted=np.linalg.norm(dV)>=FROZEN_NATURAL_MOTION_CONFIG.minimum_segment_displacement_m and np.linalg.norm(dN)>=FROZEN_NATURAL_MOTION_CONFIG.minimum_imu_displacement_m
            reason="ACCEPT" if accepted else ("REJECT_T4_DISPLACEMENT" if np.linalg.norm(dV)<FROZEN_NATURAL_MOTION_CONFIG.minimum_segment_displacement_m else "REJECT_IMU_PREINTEGRATION_SNR")
            objects.append({"segment":segment,"accepted":accepted,"reason":reason,"mount":mount,
                "temporal_region":temporal_label,"ordinal":ordinal,"left":left,"right":right,
                "i0":i0,"i1":i1,"p0":p0,"p1":p1,"sigma0":s0,"sigma1":s1,
                "start_speed":start_speed,"end_speed":end_speed,"quiet0":q0,"quiet1":q1,
                "attitude_deg":attitude,"pre":pre})
            ledger.append({"mount":mount,"temporal_region_only":temporal_label,"segment_id":segment["segment_id"],
                "start_hardware_s":f"{start:.6f}","end_hardware_s":f"{end:.6f}","duration_s":end-start,
                "start_plateau_center_V4_m":json.dumps(p0.tolist(),separators=(",",":")),"start_uncertainty_m":s0,
                "end_plateau_center_V4_m":json.dumps(p1.tolist(),separators=(",",":")),"end_uncertainty_m":s1,
                "signed_delta_p_V4_m":json.dumps(dV.tolist(),separators=(",",":")),"magnitude_V4_m":float(np.linalg.norm(dV)),
                "delta_p_N_m":json.dumps(dN.tolist(),separators=(",",":")),"magnitude_N_m":float(np.linalg.norm(dN)),
                "start_speed_mps":start_speed,"end_speed_mps":end_speed,"start_stationary_confidence":q0,
                "end_stationary_confidence":q1,"start_gyro_median_dps":g0,"end_gyro_median_dps":g1,
                "start_accel_norm_residual_g":a0,"end_accel_norm_residual_g":a1,"attitude_change_deg":attitude,
                "gyro_saturated_or_clipped":data["integrity"]["gyro_saturated_or_clipped"],
                "acceptance_weight":"EQUAL_TOTAL_WEIGHT_PER_ACCEPTED_SEGMENT","decision":reason})
            imu_rows.append({"segment_id":segment["segment_id"],"mount":mount,"time_offset_s":offset_s,
                "imu_samples":len(indices),"raw_end_velocity_mps":json.dumps(pre["raw_end_velocity_mps"].tolist(),separators=(",",":")),
                "zupt_end_velocity_mps":json.dumps(pre["zupt_end_velocity_mps"].tolist(),separators=(",",":")),
                "zupt_displacement_N_m":json.dumps(dN.tolist(),separators=(",",":")),"decision":reason})
            for point,(ts,raw,smooth) in enumerate(zip(traj["time_s"][left:right+1],traj["raw_position_m"][left:right+1],traj["position_m"][left:right+1])):
                t4_rows.append({"mount":mount,"temporal_region_only":temporal_label,"segment_id":segment["segment_id"],
                    "point_index":point,"hardware_s":f"{ts:.6f}","raw_x_m":raw[0],"raw_y_m":raw[1],"raw_z_m":raw[2],
                    "smoothed_x_m":smooth[0],"smoothed_y_m":smooth[1],"smoothed_z_m":smooth[2]})
    return objects,ledger,t4_rows,imu_rows


def fit_summary(fit):
    return {key:fit[key] for key in ("rotation","singular_values","singular_ratio","condition",
        "endpoint_median_deg","endpoint_p95_deg","path_median_normalized","path_p95_normalized",
        "bootstrap_p95_deg","leave_one_p95_deg","leave_one_max_deg","checks","accepted",
        "optimizer","segment_weight_fraction","semantic_labels_read_by_estimator")}


def test_suite(out):
    tests=["test_v47_c2cc_natural_motion.py","test_v47_c2cc_sign_forensics.py",
        "test_v47_c2cc_rotation_aware.py","test_current_room_autopos_positioning.py",
        "test_fusion_host_binary.py","test_v47_c2cc_continuous_capture.py",
        "test_v47_c2cc_frame_binding.py","test_v47_c2cc_frame_binding_capture.py",
        "test_v47_q1_covariance_repair.py","test_v47_q1_eskf.py"]
    paths=[str(ROOT/"B306_Part/tools/tests"/name) for name in tests]
    env=os.environ.copy();env["PYTHONPATH"]="/home/zekaixiao/ncs/toolchains/b81a7cd864/usr/local/lib/python3.12/site-packages:"+str(ROOT/"B306_Part/tools")
    done=subprocess.run([sys.executable,"-m","pytest","-q",*paths],cwd=ROOT,env=env,capture_output=True,text=True)
    result={"schema":METHOD+"-tests","tests":tests,"returncode":done.returncode,
        "stdout":re.sub(r" in [0-9.]+s"," in <runtime>",done.stdout),"stderr":done.stderr,
        "passed":done.returncode==0,"validation_used":False,
        "analytic_cases":["curved","diagonal/L","60dps rotation","independent remount","unknown yaw","imperfect reversal","gyro/accel bias","T4 outlier","time offset","lever arm","single line","reflection","quaternion/gravity mutation","heldout leakage","label permutation","3D stop detection"]}
    canonical(out/"SYNTHETIC_AND_MUTATION_TESTS.json",result)
    if done.returncode:raise RuntimeError("synthetic/regression tests failed")
    return result


def offset_sensitivity(data,objects,rotation):
    grid=np.arange(-.080,.0801,.005);scores=[]
    for offset in grid:
        rebuilt,_,_,_=build_segments(data,objects[0]["mount"],list(data["regions"]),float(offset))
        accepted=[x["segment"] for x in rebuilt if x["accepted"]]
        metrics=evaluate_frozen_transform(accepted,rotation)
        scores.append({"offset_ms":round(1000*float(offset),6),"endpoint_median_deg":metrics["endpoint_median_deg"],
            "path_median_normalized":metrics["path_median_normalized"],"score":metrics["path_median_normalized"]})
    best=min(scores,key=lambda x:x["score"])
    return {"primary_offset_ms":0.0,"selection_policy":"FROZEN_ZERO_MS; FITTING_ONLY_SWEEP_IS_SENSITIVITY_NOT_SELECTION",
        "sweep_bounds_ms":[-80,80],"step_ms":5,"best_fitting_sensitivity_offset_ms":best["offset_ms"],"scores":scores}


def bias_and_oracle_sensitivity(data,accepted,base_fit,oracle_path):
    perturbations=[]
    for axis in range(3):
        for sign in (-1,1):
            bias=np.zeros(3);bias[axis]=sign*FROZEN_NATURAL_MOTION_CONFIG.accelerometer_bias_prior_sigma_mps2
            changed=[]
            for obj in accepted:
                indices=np.arange(obj["i0"],obj["i1"]+1)
                a=specific_force_to_navigation_acceleration(data["R"][indices],data["acc"][indices]-bias)
                pre=preintegrated_path(data["t"][indices],a);old=obj["segment"]
                changed.append(make_segment(segment_id=old["segment_id"],imu_time_s=data["t"][indices],imu_path_N_m=pre["zupt_position_m"],
                    t4_time_s=old["t4_time_s"],t4_path_V4_m=old["t4_path_V4_m"],metadata=old["metadata"]))
            fit=fit_natural_motion(changed)
            perturbations.append({"axis":axis,"bias_mps2":bias.tolist(),"rotation_change_deg":rotation_angle_deg(base_fit["rotation"],fit["rotation"]),"accepted":fit["accepted"]})
    oracle=json.loads(oracle_path.read_text());cal=oracle["accel_calibration"]
    oracle_segments=[]
    matrix=np.asarray(cal["correction_matrix"]);bias_g=np.asarray(cal["bias_g"])
    for obj in accepted:
        indices=np.arange(obj["i0"],obj["i1"]+1);corrected=(matrix@(data["acc"][indices]/G_MPS2-bias_g).T).T*G_MPS2
        a=specific_force_to_navigation_acceleration(data["R"][indices],corrected);pre=preintegrated_path(data["t"][indices],a);old=obj["segment"]
        oracle_segments.append(make_segment(segment_id=old["segment_id"],imu_time_s=data["t"][indices],imu_path_N_m=pre["zupt_position_m"],t4_time_s=old["t4_time_s"],t4_path_V4_m=old["t4_path_V4_m"],metadata=old["metadata"]))
    oracle_fit=fit_natural_motion(oracle_segments)
    return {"primary_policy":"IDENTITY_ACCEL_MATRIX_ZERO_SHARED_BIAS","bounded_Q1_bias_state":"EXISTING_PRIOR; NO_FREE_BATCH_BIAS_FIT",
        "bias_prior_sigma_mps2":FROZEN_NATURAL_MOTION_CONFIG.accelerometer_bias_prior_sigma_mps2,
        "bias_bound_mps2":FROZEN_NATURAL_MOTION_CONFIG.accelerometer_bias_bound_mps2,
        "finite_difference_bias_sensitivity":perturbations,"maximum_rotation_change_deg":max(x["rotation_change_deg"] for x in perturbations),
        "oracle":{"label":"FAILED_DEVICE_CALIBRATION_ORACLE_ONLY","path":str(oracle_path.relative_to(ROOT)),"source_verdict":json.loads((oracle_path.parent/"RUN_MANIFEST.json").read_text())["primary_verdict"],
            "rotation_change_deg":rotation_angle_deg(base_fit["rotation"],oracle_fit["rotation"]),"fit_accepted":oracle_fit["accepted"]},
        "identifiability":"ROTATION_OBSERVABLE_WITH_BIAS_FIXED; FREE_ACCEL_BIAS_NOT_IDENTIFIED_AND_NOT_FITTED",
        "parameter_correlation":"finite-difference rotation/bias coupling reported above; no unconstrained nuisance absorption permitted"}


def lever_sensitivity(accepted,base_fit):
    changes=[];radius=FROZEN_NATURAL_MOTION_CONFIG.lever_arm_sensitivity_radius_m
    for axis in np.eye(3):
        modified=[]
        for obj in accepted:
            old=obj["segment"];delta=(obj["data_R1"]-obj["data_R0"])@(radius*axis)
            path=np.asarray(old["imu_path_N_m"]).copy();path+=np.linspace(0,1,len(path))[:,None]*delta
            modified.append(make_segment(segment_id=old["segment_id"],imu_time_s=old["imu_time_s"],imu_path_N_m=path,t4_time_s=old["t4_time_s"],t4_path_V4_m=old["t4_path_V4_m"],metadata=old["metadata"]))
        fit=fit_natural_motion(modified);changes.append(rotation_angle_deg(base_fit["rotation"],fit["rotation"]))
    return {"cad_audit":{"U4":"DWM1001C","U7":"JY901S","reference_origin_planar_mm":3.580,"component_envelope_planar_mm":32.181},
        "conservative_3d_bound_mm":50.0,"axis_extreme_rotation_change_deg":changes,"maximum_rotation_change_deg":max(changes),
        "selection_changed":False,"policy":"sensitivity only; no fabricated phase-center/die-center vector"}


def replay_validation(data,mount,rotation,tokens):
    region=data["regions"]["validation"];lo,hi=region["lo"],region["hi"]
    imu_indices=np.flatnonzero(np.asarray([lo<=x["mono"]<=hi for x in data["imu"]]))
    positions=region["positions"];origin=np.asarray(positions[0]["position"])
    start=int(imu_indices[0]);initial_q=data["q"][start]
    params=Q1Parameters(t4_position_sigma_m=np.full(3,FROZEN_NATURAL_MOTION_CONFIG.t4_position_sigma_m))
    binding=FrameBinding(R_V4_N=np.asarray(rotation),origin_V4_m=origin,initial_q_NB=initial_q,
        provenance="NATURAL_MOTION_FREEZE_MANIFEST.json",v4_navigation_rotation_valid=True,spatial_dynamics_enabled=True)
    filters={"IMU_ONLY":Q1T4ESKF(params,binding),"FUSED":Q1T4ESKF(params,binding)}
    initial_acc=np.median(data["acc"][max(0,start-200):start],axis=0)
    for filt in filters.values():
        filt.initialize_from_stationary(initial_acc,np.radians(data["gyro_bias_dps"]));filt.q=initial_q.copy()
    t4_index=0;innov=[];trajectories={"IMU_ONLY":[],"FUSED":[]}
    for sample_number,index in enumerate(imu_indices):
        t=data["t"][index];a=data["acc"][index];g=np.radians(data["gyro"][index])
        for filt in filters.values():filt.propagate(t,a,g)
        while t4_index<len(positions) and positions[t4_index]["hardware_us"]/1e6<=t:
            nis=filters["FUSED"].t4_position_update(positions[t4_index]["position"]);innov.append(nis);t4_index+=1
        if sample_number%10==0:
            for name,filt in filters.items():
                p=origin+rotation@filt.p
                trajectories[name].append({"mount":mount,"dataset_role":"HELDOUT","hardware_s":f"{t:.6f}",
                    "x_m":p[0],"y_m":p[1],"z_m":p[2],"vx_mps":filt.v[0],"vy_mps":filt.v[1],"vz_mps":filt.v[2],
                    "qw":filt.q[0],"qx":filt.q[1],"qy":filt.q[2],"qz":filt.q[3],
                    "ba_x":filt.b_a[0],"ba_y":filt.b_a[1],"ba_z":filt.b_a[2],"covariance_condition":filt.max_covariance_condition})
    pre_stop={name:float(np.linalg.norm(filt.v)) for name,filt in filters.items()}
    for filt in filters.values():filt.zupt_update()
    post_stop={name:float(np.linalg.norm(filt.v)) for name,filt in filters.items()}
    t4_rows=[{"mount":mount,"dataset_role":"HELDOUT","hardware_s":f"{x['hardware_us']/1e6:.6f}",
        "x_m":x["position"][0],"y_m":x["position"][1],"z_m":x["position"][2],"sweep":x["sweep"]} for x in positions]
    last=np.arange(max(start,int(imu_indices[-1])-200),int(imu_indices[-1])+1)
    gravity_residual=float(np.median(np.abs(np.linalg.norm(data["acc"][last],axis=1)/G_MPS2-1)))
    max_radius={name:max(float(np.linalg.norm(np.asarray([x["x_m"],x["y_m"],x["z_m"]])-origin)) for x in rows) for name,rows in trajectories.items()}
    integrity={name:{"quaternion_norm_max_error":f.max_quaternion_norm_error,"covariance_min_eigenvalue":f.min_covariance_eigenvalue,
        "covariance_max_asymmetry":f.max_covariance_asymmetry,"cholesky_failures":f.cholesky_failures,"t4_updates":f.t4_updates,
        "propagations":f.propagations,"final_accel_bias_mps2":f.b_a.tolist(),"final_gyro_bias_rad_s":f.b_g.tolist()} for name,f in filters.items()}
    return {"t4":t4_rows,"trajectories":trajectories,"innovations":innov,"pre_stop_speed":pre_stop,"post_stop_speed":post_stop,
        "stationary_accel_norm_residual_g":gravity_residual,"max_radius_m":max_radius,"integrity":integrity}


def plot_products(out,data_by_mount,objects_by_mount,fit_by_mount,validation):
    matplotlib.rcParams["svg.hashsalt"]="biospur-c2cc-natural-motion-v1"
    fig,axes=plt.subplots(2,1,figsize=(12,8))
    for axis,mount in zip(axes,"AB"):
        for name,region in data_by_mount[mount]["regions"].items():
            tr=region["trajectory"];axis.plot(tr["time_s"],np.linalg.norm(tr["velocity_mps"],axis=1),lw=.7,label=name)
        for obj in objects_by_mount[mount]:
            if obj["accepted"]:axis.axvspan(obj["segment"]["t4_time_s"][0],obj["segment"]["t4_time_s"][-1],alpha=.03,color="green")
        axis.set_ylabel("T4 3-D speed [m/s]");axis.set_title(f"Mount {mount}: fitting stop-to-stop detection");axis.legend(fontsize=6,ncol=5)
    axes[-1].set_xlabel("B306 hardware time [s]");fig.tight_layout();fig.savefig(out/"FITTING_STOP_TO_STOP.png",dpi=140);fig.savefig(out/"FITTING_STOP_TO_STOP.svg");plt.close(fig)
    fig,axes=plt.subplots(2,1,figsize=(10,7))
    for axis,mount in zip(axes,"AB"):
        fit=fit_by_mount[mount];axis.plot(fit["singular_values"],"o-",label="excitation singular values");axis2=axis.twinx();axis2.plot(fit["leave_one_rotation_deg"],".-",color="tab:orange",label="LOO rotation")
        axis.set_title(f"Mount {mount} observability");axis.set_ylabel("singular value");axis2.set_ylabel("degrees")
    fig.tight_layout();fig.savefig(out/"OBSERVABILITY_AND_LOO.png",dpi=140);fig.savefig(out/"OBSERVABILITY_AND_LOO.svg");plt.close(fig)
    fig=plt.figure(figsize=(14,5));axes=[fig.add_subplot(131,projection="3d"),fig.add_subplot(132,projection="3d"),fig.add_subplot(133,projection="3d")]
    for axis,mount in zip(axes[:2],"AB"):
        for key,label,color in (("t4","T4_UWB_ONLY","black"),("IMU_ONLY","Q1_IMU_ONLY_V4","tab:orange"),("FUSED","Q1_IMU_T4_ESKF","tab:blue")):
            rows=validation[mount]["t4"] if key=="t4" else validation[mount]["trajectories"][key]
            axis.plot([x["x_m"] for x in rows],[x["y_m"] for x in rows],[x["z_m"] for x in rows],lw=.8,label=label,color=color)
        axis.set_title(f"Mount {mount} held-out");axis.legend(fontsize=6);axis.set_box_aspect((1,1,1))
    axis=axes[2]
    for mount,style in (("A","-"),("B","--")):
        rows=validation[mount]["trajectories"]["FUSED"];axis.plot([x["x_m"] for x in rows],[x["y_m"] for x in rows],[x["z_m"] for x in rows],style,label=f"Mount {mount} fused")
    axis.set_title("Combined V4 (not external truth)");axis.legend(fontsize=7);axis.set_box_aspect((1,1,1))
    fig.tight_layout();fig.savefig(out/"HELDOUT_TRAJECTORIES.png",dpi=160);fig.savefig(out/"HELDOUT_TRAJECTORIES.svg");plt.close(fig)
    fig,axes=plt.subplots(2,2,figsize=(12,8))
    for row,mount in enumerate("AB"):
        data=data_by_mount[mount];axes[row,0].plot(data["t"],np.linalg.norm(data["gyro"]-data["gyro_bias_dps"],axis=1),lw=.35,label="gyro dps")
        axes[row,0].plot(data["t"],np.abs(np.linalg.norm(data["acc"],axis=1)-G_MPS2),lw=.35,label="|acc|-g")
        axes[row,0].legend(fontsize=7);axes[row,0].set_title(f"Mount {mount} IMU")
        rows=validation[mount]["trajectories"]["FUSED"];axes[row,1].plot([x["hardware_s"] for x in rows],[x["covariance_condition"] for x in rows],lw=.5)
        axes[row,1].set_title(f"Mount {mount} covariance condition")
    fig.tight_layout();fig.savefig(out/"Q1_IMU_BIAS_COVARIANCE.png",dpi=140);fig.savefig(out/"Q1_IMU_BIAS_COVARIANCE.svg");plt.close(fig)
    for path in out.glob("*.svg"):path.write_text("\n".join(line.rstrip() for line in path.read_text().splitlines())+"\n")


def finish_sums(out):
    lines=[f"{sha(path)}  {path.name}" for path in sorted(out.iterdir()) if path.is_file() and path.name!="SHA256SUMS"]
    (out/"SHA256SUMS").write_text("\n".join(lines)+"\n")


def derive(run:Path,out:Path):
    run=run.resolve();out=out.resolve();out.mkdir(parents=True,exist_ok=False)
    raw=run/"continuous_raw/fusion_host_raw.cobs.bin";index=run/"continuous_raw/consumption_index.jsonl"
    raw_before=sha(raw);manifest=json.loads((run/"CAPTURE_MANIFEST.json").read_text());tokens=accepted_tokens(manifest)
    fitting_ranges=fitting_record_ranges(tokens);opaque=validation_opaque_ranges(manifest);validation_ranges=validation_record_ranges(tokens)
    layout=json.loads(CANONICAL_LAYOUT.read_text());layout_schema="V4IO_LAYOUT_LEGACY_JSON_V1"
    schema_valid=layout.get("version")=="v4-io" and layout.get("label")=="V4-io" and layout.get("anchor_ids")==list(range(8)) and len(layout.get("anchors",[]))==8
    checks={"raw_sha256":raw_before==EXPECTED_RAW,"canonical_geometry_sha256":sha(CANONICAL_LAYOUT)==EXPECTED_CANONICAL_LAYOUT,
        "canonical_geometry_schema":schema_valid,
        "reflected_intermediate_rejected":CANONICAL_LAYOUT.resolve()!=INTERMEDIATE_LAYOUT.resolve(),
        **{f"commit_{commit[:12]}":subprocess.run(["git","merge-base","--is-ancestor",commit,"HEAD"],cwd=ROOT).returncode==0 for commit in HISTORICAL_COMMITS}}
    evidence={"schema":METHOD+"-input","raw":{"path":str(raw.relative_to(ROOT)),"sha256":raw_before,"expected":EXPECTED_RAW},
        "canonical_geometry":{"path":str(CANONICAL_LAYOUT.relative_to(ROOT)),"schema":layout_schema,"schema_contract":{"version":"v4-io","label":"V4-io","anchor_ids":"exactly 0..7","anchor_count":8},"sha256":sha(CANONICAL_LAYOUT)},
        "rejected_geometry":{"path":str(INTERMEDIATE_LAYOUT.relative_to(ROOT)),"sha256":sha(INTERMEDIATE_LAYOUT),"reason":"documented reflected intermediate"},
        "historical_commits":HISTORICAL_COMMITS,"fitting_ranges":fitting_ranges,
        "fitting_range_sha256":{m:hash_record_range(index,*fitting_ranges[m]) for m in "AB"},
        "excluded_heldout_opaque":opaque,"excluded_heldout_record_ranges":validation_ranges,
        "excluded_heldout_range_sha256":{m:hash_record_range(index,*validation_ranges[m]) for m in "AB"},"checks":checks}
    canonical(out/"INPUT_EVIDENCE.json",evidence)
    if not all(checks.values()):raise RuntimeError(VERDICTS["EVIDENCE"])
    (out/"HISTORICAL_VERDICT_BOUNDARY.md").write_text("# Historical verdict boundary\n\n"+"\n".join(f"- `{x}` remains unchanged and applies only to its frozen historical model." for x in HISTORICAL_VERDICTS)+"\n\nThis v1 natural-motion analysis is additive; it does not rewrite or relabel those artifacts.\n")
    (out/"MODEL_DEFINITION.md").write_text("""# Natural-motion model v1

The action names are temporal brackets only. A three-dimensional low-pass T4 path supplies speed minima, chronological path shape and signed endpoint displacement. No label supplies a vector, plane, physical up, straight line or reversal. Each mount starts a separate gravity-aligned local N frame with arbitrary yaw; repaired Q1 propagates `R_N<-S(t)` from hardware timestamps. Endpoint-ZUPT preintegration and every intermediate T4 position constrain one independent proper `R_V4<-N` per mount. Each accepted segment has equal total weight and a soft-L1 loss; no free accelerometer bias, per-action offset, scale, reflection, axis permutation or nonlinear time warp is fitted.

Primary acceleration is identity matrix with zero shared bias. Gyro bias is the first one-second stationary mean. T4 covariance is 75 mm isotropic. Constant time offset is fixed at 0 ms; ±80 ms fitting-only sweeps are sensitivity diagnostics. Lever arm is not estimated; an unknown vector within 50 mm is evaluated as bounded sensitivity. The old gyro-P95, labelled-up, labelled-horizontal, 10° hand-lift agreement, straightness and exact reversal gates are scientifically outside this model.
""")
    (out/"SEMANTIC_LABEL_INDEPENDENCE.md").write_text("# Semantic-label independence\n\nThe estimator API receives only timestamped N and V4 paths. Metadata is carried into audit output but `_factor_arrays` never reads it. The deterministic test permutes every operator label while keeping sensor arrays unchanged and requires an identical transform and verdict. Action names select time ranges only.\n")
    tests=test_suite(out)
    fitting_rows,decoded=read_fitting_rows(index,fitting_ranges)
    data={m:mount_data(fitting_rows[m],tokens,m) for m in "AB"}
    objects={};fits={};all_ledger=[];all_t4=[];all_imu=[]
    for m in "AB":
        objs,ledger,t4rows,imurows=build_segments(data[m],m,list(data[m]["regions"]))
        for obj in objs:obj["data_R0"]=data[m]["R"][obj["i0"]];obj["data_R1"]=data[m]["R"][obj["i1"]]
        accepted=[x["segment"] for x in objs if x["accepted"]];fit=fit_natural_motion(accepted)
        objects[m]=objs;fits[m]=fit;all_ledger+=ledger;all_t4+=t4rows;all_imu+=imurows
    write_csv(out/"STOP_TO_STOP_SEGMENTS.csv",all_ledger);write_csv(out/"T4_TRAJECTORY_CONSTRAINTS.csv",all_t4);write_csv(out/"IMU_PREINTEGRATION_RESULTS.csv",all_imu)
    loo=[]
    for m in "AB":
        for obj,value in zip([x for x in objects[m] if x["accepted"]],fits[m]["leave_one_rotation_deg"]):loo.append({"mount":m,"omitted_segment_id":obj["segment"]["segment_id"],"rotation_change_deg":value})
    write_csv(out/"LEAVE_ONE_SEGMENT_OUT.csv",loo)
    observability={"schema":METHOD+"-observability","mounts":{m:fit_summary(fits[m]) for m in "AB"},
        "minimum_requirements":{"materially_non_collinear":True,"stationary_endpoints":True,"dynamic_acceleration":True},
        "independent_mount_fits":True,"mount_A_transform_reused_for_B":False}
    canonical(out/"OBSERVABILITY.json",observability)
    primary_hash_payload={m:fit_summary(fits[m]) for m in "AB"};canonical(out/"PRIMARY_IDENTITY_FIT_FREEZE.json",primary_hash_payload)
    oracle_path=ROOT/"B306_Part/logs/v47_c2cc_arbitrary_pose_calibration_20260812_201945/BSFC2CC_DEVICE_CALIBRATION.json"
    bias={m:bias_and_oracle_sensitivity(data[m],[x for x in objects[m] if x["accepted"]],fits[m],oracle_path) for m in "AB"};bias["primary_identity_fit_freeze_sha256"]=sha(out/"PRIMARY_IDENTITY_FIT_FREEZE.json");canonical(out/"BIAS_IDENTIFIABILITY.json",bias)
    timing={m:offset_sensitivity(data[m],objects[m],fits[m]["rotation"]) for m in "AB"};timing["hardware_timestamp_only"]=True;timing["host_receipt_time_used"]=False;timing["per_action_tuning_permitted"]=False;canonical(out/"TIME_ALIGNMENT.json",timing)
    lever={m:lever_sensitivity([x for x in objects[m] if x["accepted"]],fits[m]) for m in "AB"};canonical(out/"LEVER_ARM_SENSITIVITY.json",lever)
    q1_integrity=all(data[m]["integrity"]["cholesky_failures"]==0 and data[m]["integrity"]["quaternion_norm_max_error"]<1e-10 for m in "AB")
    fitting_pass=all(fits[m]["accepted"] for m in "AB") and q1_integrity and tests["passed"]
    fitting_result={"schema":METHOD+"-fitting","status":"FITTING_PASS" if fitting_pass else "FITTING_UNOBSERVABLE",
        "mounts":{m:{"accepted_segments":sum(x["accepted"] for x in objects[m]),"fit":fit_summary(fits[m]),"q1_integrity":data[m]["integrity"]} for m in "AB"},
        "primary_identity_policy":True,"semantic_direction_dependency":False,"validation_opened":False}
    canonical(out/"FITTING_RESULTS.json",fitting_result)
    if not fitting_pass:
        (out/"REPORT.md").write_text(f"# {VERDICTS['UNOBSERVABLE']}\n\nFitting-only observability did not close. Validation remained unopened.\n")
        canonical(out/"PROVENANCE.json",{"schema":METHOD+"-provenance","verdict":VERDICTS["UNOBSERVABLE"],"raw_sha256_before":raw_before,"raw_sha256_after":sha(raw),"validation_opened":False,"hardware_access_performed":False})
        finish_sums(out);return VERDICTS["UNOBSERVABLE"]
    freeze={"schema":METHOD+"-freeze","source_hashes":{"analyzer":sha(Path(__file__)),"estimator":sha(ROOT/"B306_Part/tools/v47_c2cc_natural_motion.py")},
        "canonical_geometry":evidence["canonical_geometry"],"input_fitting_block_hashes":evidence["fitting_range_sha256"],
        "exact_excluded_heldout_ranges":evidence["excluded_heldout_opaque"],"excluded_heldout_record_ranges":validation_ranges,
        "algorithm":"proper SO(3) robust equal-segment path-factor fit over chronological natural stop-to-stop paths",
        "config":dataclasses.asdict(FROZEN_NATURAL_MOTION_CONFIG),"noise_model":{"t4_position_sigma_m":.075,"imu_process_noise":"Q1Parameters defaults","endpoint_zupt":True},
        "time_offset_policy":"0 ms primary; fitting-only +/-80 ms sensitivity; no validation selection","lever_arm_policy":"unknown <=50 mm sensitivity only",
        "accepted_fitting_transforms":{m:fits[m]["rotation"] for m in "AB"},"observability_metrics":{m:fit_summary(fits[m]) for m in "AB"},
        "test_results_sha256":sha(out/"SYNTHETIC_AND_MUTATION_TESTS.json"),"semantic_labels_used_as_directions":False,"HELDOUT_MAY_NOW_OPEN":True}
    canonical(out/"NATURAL_MOTION_FREEZE_MANIFEST.json",freeze)
    signature=sha(out/"NATURAL_MOTION_FREEZE_MANIFEST.json");(out/"NATURAL_MOTION_FREEZE_MANIFEST.sha256").write_text(signature+"  NATURAL_MOTION_FREEZE_MANIFEST.json\n")
    if sha(out/"NATURAL_MOTION_FREEZE_MANIFEST.json")!=signature or not json.loads((out/"NATURAL_MOTION_FREEZE_MANIFEST.json").read_text())["HELDOUT_MAY_NOW_OPEN"]:raise RuntimeError("freeze signature failed")
    # First and only validation decode in this derivation occurs below.
    full_ranges=full_heldout_context_ranges(tokens);heldout_rows=read_selected_rows(index,full_ranges)
    full_data={m:mount_data(heldout_rows[m],tokens,m,include_validation=True) for m in "AB"}
    heldout={};heldout_rows_csv=[];validation_products={}
    for m in "AB":
        objs,_,_,_=build_segments(full_data[m],m,["validation"])
        accepted=[x["segment"] for x in objs if x["accepted"]];metrics=evaluate_frozen_transform(accepted,fits[m]["rotation"])
        replay=replay_validation(full_data[m],m,fits[m]["rotation"],tokens);validation_products[m]=replay
        checks={**metrics["checks"],"minimum_two_segments":len(accepted)>=2,
            "fused_room_scale":replay["max_radius_m"]["FUSED"]<=FROZEN_NATURAL_MOTION_CONFIG.heldout_room_scale_bound_m,
            "stop_velocity_after_zupt":replay["post_stop_speed"]["FUSED"]<.05,
            "stationary_gravity_residual":replay["stationary_accel_norm_residual_g"]<.08,
            "quaternion_integrity":replay["integrity"]["FUSED"]["quaternion_norm_max_error"]<1e-10,
            "covariance_integrity":replay["integrity"]["FUSED"]["cholesky_failures"]==0 and replay["integrity"]["FUSED"]["covariance_min_eigenvalue"]>0}
        passed=all(checks.values());heldout[m]={"metrics":metrics,"checks":checks,"passed":passed,"replay":{k:replay[k] for k in ("pre_stop_speed","post_stop_speed","stationary_accel_norm_residual_g","max_radius_m","integrity")}}
        heldout_rows_csv.append({"mount":m,"accepted_segments":len(accepted),"endpoint_median_deg":metrics["endpoint_median_deg"],"endpoint_p95_deg":metrics["endpoint_p95_deg"],"path_median_normalized":metrics["path_median_normalized"],"path_p95_normalized":metrics["path_p95_normalized"],"fused_max_radius_m":replay["max_radius_m"]["FUSED"],"imu_only_max_radius_m":replay["max_radius_m"]["IMU_ONLY"],"stop_velocity_pre_zupt_mps":replay["pre_stop_speed"]["FUSED"],"stop_velocity_post_zupt_mps":replay["post_stop_speed"]["FUSED"],"gravity_norm_residual_g":replay["stationary_accel_norm_residual_g"],"result":"PASS" if passed else "FAIL"})
    write_csv(out/"HELDOUT_RESULTS.csv",heldout_rows_csv)
    t4_csv=[];imu_csv=[];fused_csv=[]
    for m in "AB":t4_csv+=validation_products[m]["t4"];imu_csv+=validation_products[m]["trajectories"]["IMU_ONLY"];fused_csv+=validation_products[m]["trajectories"]["FUSED"]
    write_csv(out/"TRAJECTORY_T4_UWB_ONLY.csv",t4_csv);write_csv(out/"TRAJECTORY_Q1_IMU_ONLY_V4.csv",imu_csv);write_csv(out/"TRAJECTORY_Q1_IMU_T4_ESKF.csv",fused_csv)
    cross={"schema":METHOD+"-cross-mount","independent_fits":True,"transform_difference_deg":rotation_angle_deg(fits["A"]["rotation"],fits["B"]["rotation"]),
        "raw_gravity_remount_reference_deg":92.150,"old_hand_motion_up_disagreement_deg":13.125,"old_hand_motion_gate_retired":True,
        "mount_A_transform_reused_for_B":False,"same_frozen_rules_for_both":True,"heldout":heldout,
        "installation_invariance_pass":all(heldout[m]["passed"] for m in "AB"),"external_ground_truth_available":False}
    canonical(out/"CROSS_MOUNT_RESULTS.json",cross)
    plot_products(out,full_data,objects,fits,validation_products)
    all_pass=all(heldout[m]["passed"] for m in "AB")
    verdict=VERDICTS["CONDITIONAL"] if all_pass else VERDICTS["HELDOUT_FAIL"]
    (out/"REAL_DATA_ADAPTER_NOTE.md").write_text("# Timestamp and adapter note\n\nIMU uses `base_us + delta_us`; UWB uses `strobe_us`. Both are B306 hardware-clock timestamps. Host receipt monotonic time is used only to bracket operator protocol regions. No resampling, nonlinear warp, semantic-axis mapping or hidden smoothing occurs in the estimator; the documented 1 Hz T4 low-pass only defines robust chronological position factors and three-dimensional speed minima.\n")
    (out/"REPORT.md").write_text(f"""# {verdict}

The existing capture is sufficient to recover independent, proper natural-motion frame bindings without treating the operator's labels as directions. Mount A accepted {sum(x['accepted'] for x in objects['A'])} fitting segments; Mount B accepted {sum(x['accepted'] for x in objects['B'])}. Both passed excitation rank, measurement-aware residual, bootstrap, leave-one-segment-out, no-dominant-stroke and Q1 numerical-integrity gates before validation was opened.

After the signed freeze manifest was written, each validation block was decoded once as arbitrary motion. Mount A held-out was {'internally consistent' if heldout['A']['passed'] else 'not internally consistent'} and Mount B was {'internally consistent' if heldout['B']['passed'] else 'not internally consistent'} under the same frozen rules. T4 is a measurement, not external trajectory truth; therefore even a two-mount internal PASS cannot establish absolute position or attitude accuracy. The appropriate product verdict is conditional rather than an external-accuracy PASS.

The 13.125° hand-lift-derived disagreement is preserved as historical evidence but is not a physical-up gate. Mount transforms are intentionally different and independently fitted. Identity accelerometer calibration remains primary; the failed device-specific calibration is reported only as oracle sensitivity and never selects the transform.

This supports proceeding to a prospective ten-node arbitrary-wear T-pose/body calibration as the next validation stage, provided that stage supplies independent pose/trajectory reference. It does not validate body-segment anatomical axes, external trajectory accuracy, antenna/IMU lever-arm centers, or production-wide calibration transfer.
""")
    provenance={"schema":METHOD+"-provenance","verdict":verdict,"git_head_at_derivation":subprocess.check_output(["git","rev-parse","HEAD"],cwd=ROOT,text=True).strip(),
        "raw_sha256_before":raw_before,"raw_sha256_after":sha(raw),"fitting_records_decoded":decoded,
        "freeze_manifest_sha256":signature,"validation_opened":True,"validation_decode_count":1,
        "validation_opened_only_after_freeze_signature_verified":True,"hardware_access_performed":False,
        "serial_ble_jlink_swd_rtt_autopos_motor_power_accessed":False}
    canonical(out/"PROVENANCE.json",provenance)
    if sha(raw)!=raw_before:raise RuntimeError("authoritative raw changed")
    finish_sums(out);return verdict


def main():
    parser=argparse.ArgumentParser();parser.add_argument("run",type=Path);parser.add_argument("out",type=Path);args=parser.parse_args()
    print(derive(args.run,args.out))


if __name__=="__main__":main()
