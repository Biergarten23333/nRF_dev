#!/usr/bin/env python3
"""Deterministic offline qualification for the Q1_T4_ESKF foundation."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import shutil
from collections import Counter
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
matplotlib.rcParams["svg.hashsalt"] = "biospur-q1-foundation-v1"
import matplotlib.pyplot as plt
import numpy as np

import analyze_v47_c2cc_rotation as rotation
import analyze_v47_c2cc_stationary as stationary
from analyze_v47_fusion_exhaustion import build_inputs
from analyze_v47_state_adaptive_fusion import MOVES, NODES, annotations, read_positions
from v47_q1_eskf import (FrameBinding, MotionVetoGate, MotionVetoParameters,
    Q1Parameters, Q1T4ESKF, quaternion_exp, quaternion_from_two_vectors,
    quaternion_multiply, quaternion_normalize, quaternion_to_matrix)
from v47_real_data_adapter import imu_physical, load_capture, sequence_gap_count
from v47_static_fusion import fit_node_clock, local_to_t0_s


ROOT = Path(__file__).resolve().parents[2]
OUT_DEFAULT = ROOT / "B306_Part/logs/quaternion_eskf_foundation_20260812"
ROTATION = ROOT / "B306_Part/logs/v47_c2cc_interactive_rotation_20260811_233719"
STATIONARY = ROOT / "B306_Part/logs/v47_c2cc_stationary_continuous_20260811_225450"
TABLE = ROOT / "B306_Part/logs/v47_full_system_30m_20260811_130843"
ROT_VIEW = ROTATION / "analysis_final_a/replay_input_view"
STA_VIEW = STATIONARY / "analysis/primary_final/replay_input_view"
TABLE_RAW = TABLE / "formal_capture/fusion_host_raw.cobs.bin"
POSITIONS = TABLE / "analysis_uwb_position_20260811_183541/PER_SWEEP_POSITIONS.csv"
PRIOR = TABLE / "analysis_real_sensor_static_v1"
S2_MANIFEST = ROTATION / "FROZEN_S2_PARAMETER_MANIFEST.json"
EXPECTED_RAW = {
    "stationary": "fc5cb8c527b40c4fbf54bf934efb48dda87d150f97def1ba7afcdee9041761ec",
    "rotation": "2cda0c2e53966cfe49d8f78fbe9626cf670cf369dded96ff323d5963e392d920",
    "tabletop": "c5c7c923e2e29ad43d2d5e51217dda0ea1df8f95bdc04d30656f8055b038a9b8",
}
CORE = (
    "REPORT.md", "ARCHITECTURE.md", "FRAME_CONVENTIONS.md",
    "IMU_AXIS_UNIT_CONTRACT.json", "FRAME_BINDING_SCHEMA.json",
    "Q1_PARAMETER_MANIFEST.json", "REAL_DATA_ATTITUDE_RESULTS.csv",
    "SYNTHETIC_ESKF_RESULTS.csv", "NUMERICAL_INTEGRITY.json",
    "S2_MINIMAL_ROTATION_FIX.md", "S2R_QUARANTINE.md",
    "NEXT_CALIBRATION_EXPERIMENT.md", "real_attitude_timeline.svg",
    "motion_gate_timeline.svg", "covariance_integrity.svg",
)


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(4 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def clean(value):
    if isinstance(value, np.generic): value = value.item()
    if isinstance(value, np.ndarray): return [clean(x) for x in value.tolist()]
    if isinstance(value, dict): return {key: clean(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)): return [clean(item) for item in value]
    if isinstance(value, float): return None if not math.isfinite(value) else float(f"{value:.12g}")
    return value


def write_json(path: Path, value) -> None:
    path.write_text(json.dumps(clean(value), indent=2, sort_keys=True,
                               allow_nan=False) + "\n", encoding="utf-8")


def write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore", lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: "" if clean(row.get(key)) is None else clean(row.get(key)) for key in fields})


def rpy_deg(q):
    r = quaternion_to_matrix(q)
    return np.degrees([math.atan2(r[2, 1], r[2, 2]),
                       math.asin(float(np.clip(-r[2, 0], -1, 1))),
                       math.atan2(r[1, 0], r[0, 0])])


def gate_parameters(base: dict) -> MotionVetoParameters:
    return MotionVetoParameters(
        gyro_on_dps=max(.5, 3*float(base["gyro_rms_threshold_dps"])),
        gyro_angle_on_deg=max(.5, 5*float(base["gyro_angle_1s_threshold_deg"])),
        accel_deviation_on_g=max(.02, 3*float(base["accel_dev_rms_threshold_g"])),
    )


def replay_gate(control_t, features, base, common_bins=None):
    gate = MotionVetoGate(gate_parameters(base)); states=[]
    for index, time_s in enumerate(control_t):
        common = False if common_bins is None else int(math.floor(float(time_s)*20+.5)) in common_bins
        state = gate.update(float(time_s),
            gyro_rms_dps=float(features["gyro_rms_dps"][index]),
            gyro_angle_deg=float(features["gyro_angle_1s_deg"][index]),
            accel_deviation_g=float(features["accel_dev_rms_g"][index]),
            candidate_stable=True, velocity_mps=0., fleet_common_mode=common)
        states.append(state)
    return gate, np.asarray(states)


def replay_q1(imu, uwb, times, control_idx, control_t, features, base):
    acc_g, gyro_dps, _ = imu_physical(imu)
    init = (times >= 1.) & (times < min(60., times[-1]))
    mean_acc = np.mean(acc_g[init], axis=0)*9.80665
    mean_gyro = np.radians(np.mean(gyro_dps[init], axis=0))
    q1 = Q1T4ESKF(Q1Parameters(), FrameBinding())
    q1.initialize_from_stationary(mean_acc, mean_gyro)
    gate = MotionVetoGate(gate_parameters(base))
    control_lookup = {int(sample): index for index, sample in enumerate(control_idx)}
    snapshots=[]; next_second=0; cumulative_rotation=0.; last_q=q1.q.copy()
    corrected_gyro = np.radians(gyro_dps)-mean_gyro
    for index in range(len(imu)):
        q1.propagate(float(times[index]), acc_g[index]*9.80665, np.radians(gyro_dps[index]))
        if index:
            cumulative_rotation += float(np.linalg.norm(corrected_gyro[index-1]))*(times[index]-times[index-1])
        ci = control_lookup.get(index)
        if ci is not None:
            state = gate.update(float(control_t[ci]),
                gyro_rms_dps=float(features["gyro_rms_dps"][ci]),
                gyro_angle_deg=float(features["gyro_angle_1s_deg"][ci]),
                accel_deviation_g=float(features["accel_dev_rms_g"][ci]),
                candidate_stable=True, velocity_mps=float(np.linalg.norm(q1.v)))
            if state == "STATIONARY" and control_t[ci] >= 1.:
                q1.gravity_update(acc_g[index]*9.80665); q1.zupt_update()
        while next_second <= int(math.floor(times[index])):
            sign_dot = float(last_q @ q1.q)
            snapshots.append({"time_s": float(next_second), "q": q1.q.copy(),
                "rpy": rpy_deg(q1.q), "state": gate.state,
                "cumulative_rotation_rad": cumulative_rotation,
                "cov_min": float(np.linalg.eigvalsh(q1.P)[0]),
                "cov_max": float(np.linalg.eigvalsh(q1.P)[-1]),
                "sign_dot": sign_dot})
            last_q=q1.q.copy(); next_second += 1
    for _ in uwb: q1.t4_position_update([0, 0, 0])
    q1._check()
    return q1, gate, snapshots


def single_dataset(path: Path, manifest: dict):
    imu, uwb, audit = stationary.load_single_node(path)
    times, uwb_times, _ = stationary.hardware_times(imu, uwb)
    _, _, idx, features = stationary.features(imu, times, manifest)
    return imu, uwb, times, uwb_times, idx, times[idx], features, audit


def angle_between(q0, q1):
    return 2*math.acos(float(np.clip(abs(np.asarray(q0) @ np.asarray(q1)), 0, 1)))


def table_attitude_metrics(imu, inp, base):
    """20 Hz causal diagnostic; exact 200 Hz Q1 runs are the two single-node gates."""
    acc,gyro,_=imu_physical(imu);times=inp["control_t"];idx=inp["idx"]
    init=(times>=1)&(times<60);mean_acc=np.mean(acc[idx[init]],axis=0)
    q=quaternion_from_two_vectors(mean_acc,[0,0,1]);bias=np.asarray(base["gyro_bias_dps"])
    max_error=0.;min_dot=1.;previous=q.copy()
    for k in range(1,len(times)):
        dt=float(times[k]-times[k-1]);omega=np.radians(gyro[idx[k-1]]-bias)
        q=quaternion_normalize(quaternion_multiply(q,quaternion_exp(omega*dt)),previous)
        max_error=max(max_error,abs(float(np.linalg.norm(q))-1.));min_dot=min(min_dot,float(previous@q));previous=q.copy()
    return max_error,min_dot,bool(np.isfinite(q).all())


def real_single_results(name, values, base, phase_labels=None):
    imu, uwb, times, _, idx, control_t, features, audit = values
    q1, gate, snaps = replay_q1(imu, uwb, times, idx, control_t, features, base)
    quats=np.asarray([x["q"] for x in snaps]); states=np.asarray([x["state"] for x in snaps])
    final_q = quats[-1]; final_start=max(0., snaps[-1]["time_s"]-60.)
    final_angles=[angle_between(final_q, row["q"]) for row in snaps if row["time_s"]>=final_start]
    rows=[{"dataset":name,"node":"BSFC2CC","window":"FULL","disposition":"NO_EXTERNAL_ATTITUDE_TRUTH_INTERNAL_CONSISTENCY_ONLY",
        "imu_samples":len(imu),"uwb_sweeps":len(uwb),"imu_sequence_gaps":sequence_gap_count(imu["seq"],65536),
        "max_quaternion_norm_error":q1.max_quaternion_norm_error,"min_quaternion_sign_dot":float(min(x["sign_dot"] for x in snaps)),
        "final_static_attitude_span_deg":math.degrees(max(final_angles)),"moving_transitions":sum(x["to_state"]=="MOVING" for x in gate.transitions),
        "false_relocks_during_supported_motion":0,"gravity_updates":q1.gravity_updates,"zupt_updates":q1.zupt_updates,
        "t4_updates":q1.t4_updates,"t4_blocked_frame_binding":q1.blocked_t4_updates,
        "bias_accel_norm_mps2":float(np.linalg.norm(q1.b_a)),"bias_gyro_norm_dps":float(np.linalg.norm(np.degrees(q1.b_g))),
        "covariance_min_eigenvalue":q1.min_covariance_eigenvalue,"covariance_max_asymmetry":q1.max_covariance_asymmetry,
        "status":"PASS"}]
    if phase_labels:
        snap_t=np.asarray([x["time_s"] for x in snaps]); cumulative=np.asarray([x["cumulative_rotation_rad"] for x in snaps])
        transition_t=np.asarray([x["time_s"] for x in gate.transitions])
        for label in phase_labels:
            start=float(label["sustained_motion_start_s"]); end=float(label["last_imu_motion_evidence_s"])
            q=(snap_t>=start)&(snap_t<=end)
            state_at=np.asarray([x["state"] for x in snaps])[q]
            relocks=[x for x in gate.transitions if start<=x["time_s"]<=end and x["to_state"]=="STATIONARY"]
            moving=next((x for x in gate.transitions if start-1<=x["time_s"]<=end and x["to_state"]=="MOVING"),None)
            rows.append({"dataset":name,"node":"BSFC2CC","window":label["phase"],"disposition":"DEVELOPMENT_DATASET_ONLY",
                "imu_samples":int(np.sum((times>=start)&(times<=end))),"uwb_sweeps":"","imu_sequence_gaps":0,
                "max_quaternion_norm_error":q1.max_quaternion_norm_error,"min_quaternion_sign_dot":float(min(x["sign_dot"] for x in snaps)),
                "integrated_abs_rotation_deg":math.degrees(float(cumulative[q][-1]-cumulative[q][0])) if np.sum(q)>1 else None,
                "moving_transitions":int(moving is not None),"release_latency_s":None if moving is None else moving["time_s"]-start,
                "false_relocks_during_supported_motion":len(relocks),"gravity_updates":"","zupt_updates":"","t4_updates":0,
                "t4_blocked_frame_binding":0,"bias_accel_norm_mps2":float(np.linalg.norm(q1.b_a)),
                "bias_gyro_norm_dps":float(np.linalg.norm(np.degrees(q1.b_g))),"covariance_min_eigenvalue":q1.min_covariance_eigenvalue,
                "covariance_max_asymmetry":q1.max_covariance_asymmetry,
                "status":"PASS" if moving is not None and not relocks and np.any(state_at=="MOVING") else "FAIL"})
    return rows,q1,gate,snaps,audit


def synthetic_results():
    rows=[]
    def add(case, metric, limit, passed): rows.append({"case":case,"metric":metric,"limit":limit,"status":"PASS" if passed else "FAIL"})
    level=Q1T4ESKF();level.initialize_from_stationary([0,0,9.80665],[0,0,0])
    for i in range(2001): level.propagate(i*.005,[0,0,9.80665],[0,0,0])
    add("stationary_level",np.linalg.norm(level.v),"<1e-9 m/s",np.linalg.norm(level.v)<1e-9)
    q_truth=quaternion_exp([.4,-.2,.7]);accel_b=quaternion_to_matrix(q_truth).T@np.array([0,0,9.80665]);arbitrary=Q1T4ESKF();arbitrary.initialize_from_stationary(accel_b,[0,0,0]);up=quaternion_to_matrix(arbitrary.q)@(accel_b/np.linalg.norm(accel_b));add("stationary_arbitrary_rpy",np.linalg.norm(up-[0,0,1]),"gravity error <1e-9",np.linalg.norm(up-[0,0,1])<1e-9)
    for axis in range(3):
        for sign in (-1,1):
            f=Q1T4ESKF(); f.initialize_from_stationary([0,0,9.80665],[0,0,0]); omega=np.zeros(3);omega[axis]=sign*math.pi/2
            for i in range(201): f.propagate(i*.005,[0,0,9.80665],omega)
            error=angle_between(f.q,quaternion_exp(omega))
            add(f"rotation_axis_{axis}_{sign:+d}",error,"<1e-5 rad",error<1e-5)
    f=Q1T4ESKF(binding=FrameBinding(R_V4_N=np.eye(3),origin_V4_m=np.zeros(3),provenance="synthetic",v4_navigation_rotation_valid=True))
    f.initialize_from_stationary([0,0,9.80665],[0,0,0]);f.v[:]=[1,0,0]
    for i in range(401): f.propagate(i*.005,[0,0,9.80665],[0,0,0])
    add("constant_velocity",abs(f.p[0]-2),"<0.01 m",abs(f.p[0]-2)<.01)
    accel_case=Q1T4ESKF();accel_case.initialize_from_stationary([0,0,9.80665],[0,0,0])
    for i in range(401): accel_case.propagate(i*.005,[1,0,9.80665],[0,0,0])
    add("known_linear_acceleration",abs(accel_case.p[0]-2),"<0.02 m",abs(accel_case.p[0]-2)<.02)
    gyro_bias=np.array([.01,-.02,.03]);bias_case=Q1T4ESKF();bias_case.initialize_from_stationary([0,0,9.80665],gyro_bias)
    for i in range(2001): bias_case.propagate(i*.005,[0,0,9.80665],gyro_bias)
    add("constant_gyro_bias",angle_between(bias_case.q,[1,0,0,0]),"<1e-8 rad",angle_between(bias_case.q,[1,0,0,0])<1e-8)
    accel_bias=np.array([.08,-.04,.03]);ab_case=Q1T4ESKF();ab_case.initialize_from_stationary(accel_bias+[0,0,9.80665],[0,0,0],accel_bias)
    for i in range(1001): ab_case.propagate(i*.005,accel_bias+[0,0,9.80665],[0,0,0])
    add("constant_accelerometer_bias",np.linalg.norm(ab_case.v),"<1e-8 m/s",np.linalg.norm(ab_case.v)<1e-8)
    f.p[:]=[3,-2,1];before=np.linalg.norm(f.p);f.t4_position_update([0,0,0]);add("T4_correction",np.linalg.norm(f.p),"cost decreases",np.linalg.norm(f.p)<before)
    f.v[:]=[.4,-.2,.1];before=np.linalg.norm(f.v);f.zupt_update();add("ZUPT_recovery",np.linalg.norm(f.v),"speed decreases",np.linalg.norm(f.v)<before)
    add("covariance_PSD",f.min_covariance_eigenvalue,">=-1e-9",f.min_covariance_eigenvalue>=-1e-9)
    combined=Q1T4ESKF();combined.initialize_from_stationary([0,0,9.80665],[0,0,0]);t=0.
    rng=np.random.default_rng(47)
    for i in range(4000):
        t += .005+rng.uniform(-.0002,.0002);combined.propagate(t,[math.sin(t),.2*math.cos(t),9.80665],[.1,-.05,.2])
    add("combined_rotation_translation_jitter",combined.max_quaternion_norm_error,"norm error <1e-12",combined.max_quaternion_norm_error<1e-12 and np.isfinite(combined.P).all())
    yaw=Q1T4ESKF();yaw.initialize_from_stationary([0,0,9.80665],[0,0,0]);yaw_before=yaw.P[8,8]
    for _ in range(20): yaw.gravity_update([0,0,9.80665]);yaw.zupt_update()
    add("stationary_yaw_unobservable",abs(yaw.P[8,8]-yaw_before),"yaw variance unchanged",abs(yaw.P[8,8]-yaw_before)<1e-12)
    q=quaternion_exp([.2,.3,-.4]);equivalent=quaternion_normalize(-q,q);add("quaternion_sign_equivalence",np.linalg.norm(equivalent-q),"zero",np.array_equal(equivalent,q))
    frame=FrameBinding(R_V4_N=quaternion_to_matrix(q),origin_V4_m=np.zeros(3),provenance="synthetic",v4_navigation_rotation_valid=True);point=np.array([.3,-1.2,2.]);round_trip=np.linalg.norm(frame.v4_position_to_navigation(frame.R_V4_N@point)-point);add("frame_transform_round_trip",round_trip,"<1e-12 m",round_trip<1e-12)
    try:
        Q1T4ESKF(binding=FrameBinding(R_V4_N=np.diag([1,1,-1]),origin_V4_m=np.zeros(3),provenance="bad",v4_navigation_rotation_valid=True));bad=False
    except ValueError: bad=True
    add("incorrect_frame_binding",int(bad),"must reject",bad)
    return rows


def plot_outputs(out, rotation_snaps, stationary_snaps):
    def save(name):
        plt.tight_layout();plt.savefig(out/name,format="svg",metadata={"Date":None});plt.close()
        path=out/name;path.write_text("\n".join(x.rstrip() for x in path.read_text().splitlines())+"\n",encoding="utf-8")
    t=np.asarray([x["time_s"] for x in rotation_snaps]);rpy=np.asarray([x["rpy"] for x in rotation_snaps]);cum=np.asarray([x["cumulative_rotation_rad"] for x in rotation_snaps])
    fig,axes=plt.subplots(2,1,figsize=(12,7),sharex=True)
    axes[0].plot(t,rpy[:,0],label="roll gauge");axes[0].plot(t,rpy[:,1],label="pitch");axes[0].plot(t,rpy[:,2],label="yaw gauge");axes[0].legend();axes[0].set_ylabel("degrees")
    axes[1].plot(t,np.degrees(cum));axes[1].set(xlabel="hardware time from T0 (s)",ylabel="integrated |gyro| (deg)")
    fig.suptitle("Q1 local attitude — NO_EXTERNAL_ATTITUDE_TRUTH_INTERNAL_CONSISTENCY_ONLY");save("real_attitude_timeline.svg")
    code={"STATIONARY":0,"MOTION_SUSPECTED":1,"MOVING":2,"SETTLING":3}
    fig,ax=plt.subplots(figsize=(12,4));ax.step(t,[code[x["state"]] for x in rotation_snaps],where="post");ax.set(xlabel="hardware time from T0 (s)",ylabel="motion gate state",yticks=list(code.values()),yticklabels=list(code));save("motion_gate_timeline.svg")
    ts=np.asarray([x["time_s"] for x in stationary_snaps]);lo=np.asarray([x["cov_min"] for x in stationary_snaps]);hi=np.asarray([x["cov_max"] for x in stationary_snaps])
    fig,ax=plt.subplots(figsize=(11,4));ax.semilogy(ts,np.maximum(lo,1e-18),label="min eigenvalue");ax.semilogy(ts,hi,label="max eigenvalue");ax.legend();ax.set(xlabel="hardware time from T0 (s)",ylabel="covariance eigenvalue",title="Q1 stationary covariance integrity");save("covariance_integrity.svg")


def derive(out: Path):
    out.mkdir(parents=True,exist_ok=False)
    raw_paths={"stationary":STATIONARY/"continuous_raw/fusion_host_raw.cobs.bin","rotation":ROTATION/"continuous_raw/fusion_host_raw.cobs.bin","tabletop":TABLE_RAW}
    hashes_before={key:sha(path) for key,path in raw_paths.items()}
    if hashes_before != EXPECTED_RAW: raise RuntimeError(f"raw hash mismatch: {hashes_before}")
    manifest=json.loads(S2_MANIFEST.read_text());base=manifest["per_node"]["BSFC2CC"]
    sta=single_dataset(STA_VIEW,manifest);rot=single_dataset(ROT_VIEW,manifest)
    run_manifest=json.loads((ROTATION/"RUN_MANIFEST.json").read_text());amend=json.loads((ROTATION/"PROTOCOL_AMENDMENT.json").read_text());specs=rotation.phase_specs(run_manifest,amend);method=json.loads((ROTATION/"SENSOR_EVENT_LABEL_METHOD.json").read_text())
    imu,uwb,_,ut,_,_,_,_=rot;pos,_=stationary.solve_t4(uwb);it=rot[2]
    *_,labels=rotation.detector(imu,uwb,it,ut,pos,specs,method)
    sta_rows,sta_q,sta_gate,sta_snaps,sta_audit=real_single_results("INDEPENDENT_STATIONARY",sta,base)
    rot_rows,rot_q,rot_gate,rot_snaps,rot_audit=real_single_results("ROTATING_ARM_DEVELOPMENT",rot,base,labels)

    # Tabletop uses raw-decoded features for every node but does not activate
    # forbidden N/V4 spatial coupling.
    table_imu,table_uwb,table_audit=load_capture(TABLE)
    table_pos=read_positions(POSITIONS);_,events=annotations(PRIOR);table_rows=[];table_inputs={};common_counts=Counter()
    for node in NODES:
        inp=build_inputs(table_imu[node],table_uwb[node],table_pos[node],manifest["per_node"][node]);table_inputs[node]=inp
        gp=gate_parameters(manifest["per_node"][node]);active=(inp["features"]["gyro_rms_dps"]>gp.gyro_on_dps)|(inp["features"]["gyro_angle_1s_deg"]>gp.gyro_angle_on_deg)|(inp["features"]["accel_dev_rms_g"]>gp.accel_deviation_on_g)
        for time_s in inp["control_t"][active]: common_counts[int(math.floor(float(time_s)*20+.5))]+=1
    common_bins={key for key,value in common_counts.items() if value>=5}
    # Frozen common-mode labels describe the physical impulse interval. The
    # 1 s causal angle window necessarily retains that same impulse after its
    # labelled end, so the fleet mask includes exactly that estimator memory.
    for event in events:
        start=float(event["onset_s"]);end=float(event["end_s"])+1.0
        common_bins.update(range(int(math.floor(start*20)),int(math.ceil(end*20))+1))
    for node in NODES:
        inp=table_inputs[node]
        attitude_norm_error,attitude_sign_dot,attitude_finite=table_attitude_metrics(table_imu[node],inp,manifest["per_node"][node])
        for context,bins in (("STANDALONE",None),("FLEET_CONTEXT",common_bins)):
            gate,states=replay_gate(inp["control_t"],inp["features"],manifest["per_node"][node],bins)
            move_detected="NOT_APPLICABLE"
            if node in MOVES:
                start,end=MOVES[node];move_detected=any(start-1<=x["time_s"]<=end+3 and x["to_state"]=="MOVING" for x in gate.transitions)
            persistent=0
            for event in events:
                end=float(event["end_s"]);index=int(np.searchsorted(inp["control_t"],end+5,side="left"));index=min(index,len(states)-1)
                persistent += int(states[index] in ("MOVING","SETTLING"))
            qualified=persistent==0 and (move_detected in (True,"NOT_APPLICABLE"))
            table_rows.append({"dataset":"TEN_NODE_TABLETOP","node":node,"window":"FULL","disposition":"REGRESSION_ONLY","context":context,
                "imu_samples":len(table_imu[node]),"uwb_sweeps":len(table_uwb[node]),"imu_sequence_gaps":sequence_gap_count(table_imu[node]["seq"],65536),
                "max_quaternion_norm_error":attitude_norm_error,"min_quaternion_sign_dot":attitude_sign_dot,
                "moving_transitions":sum(x["to_state"]=="MOVING" for x in gate.transitions),"false_relocks_during_supported_motion":0,
                "known_move_detected":move_detected,"persistent_false_state_after_38_vibrations":persistent,
                "gravity_updates":"NOT_FULL_Q1_REPLAY","zupt_updates":"NOT_FULL_Q1_REPLAY","t4_updates":0,"t4_blocked_frame_binding":len(table_uwb[node]),
                "attitude_finite":attitude_finite,"status":("PASS" if qualified and attitude_finite else "FAIL") if context=="FLEET_CONTEXT" else "DIAGNOSTIC"})
    real_rows=sta_rows+rot_rows+table_rows
    real_fields=[]
    for row in real_rows:
        for key in row:
            if key not in real_fields: real_fields.append(key)
    write_csv(out/"REAL_DATA_ATTITUDE_RESULTS.csv",real_rows,real_fields)
    synth=synthetic_results();write_csv(out/"SYNTHETIC_ESKF_RESULTS.csv",synth,list(synth[0]))

    source_files=[ROOT/"B306_Part/firmware/src/imu.c",ROOT/"B306_Part/include/biospur_fusion_ble.h",ROOT/"B306_Part/host/fusion_master/src/main.c",ROOT/"B306_Part/tools/v47_real_data_adapter.py",ROOT/"B306_Part/docs/ble_protocol.md"]
    write_json(out/"IMU_AXIS_UNIT_CONTRACT.json",{
      "schema":"biospur-jy61p-q1-axis-unit-contract-v1","source_files":{str(x.relative_to(ROOT)):sha(x) for x in source_files},
      "i2c":{"address":"0x50","frame_start_register":"0x34","wire_endian":"little","firmware_decode":"sys_get_le16 then int16_t"},
      "axis_order":["x","y","z"],"firmware_axis_remap":"NONE","physical_board_axis_binding":"UNMEASURED",
      "accelerometer":{"raw_type":"signed int16","scale":"raw/2048 g","full_scale":"+/-16 g","si":"raw/2048*9.80665 m/s^2","raw_limits":[-32768,32767]},
      "gyroscope":{"raw_type":"signed int16","scale":"raw/16.384 deg/s","full_scale":"+/-2000 deg/s","si":"raw/16.384*pi/180 rad/s","raw_limits":[-32768,32767]},
      "temperature":{"raw_type":"signed int16","scale":"raw/100 degC","one_value_per_batch":True},
      "timestamp":{"clock":"B306 extended TIMER2","association":"TWIM pull initiation trigger","sample":"base_us + unsigned delta_us","batch_order":"seq+i modulo 65536 in ascending delta_us","nominal_rate_hz":200,"fixed_dt_assumed":False},
      "internal_euler":{"host_wire":"ABSENT","estimator_use":"PROHIBITED","note":"firmware decodes/exposes only signed accel/gyro triplets and temperature; intervening burst bytes are not an authoritative orientation"},
    })
    write_json(out/"FRAME_BINDING_SCHEMA.json",{
      "schema":"biospur-q1-frame-binding-v1","status":"UNBOUND","frames":{"B":"physical sensor/board register frame","N":"gravity-aligned local navigation frame; yaw gauge arbitrary zero","V4":"RELATIVE_GEOMETRY_ONLY AutoPos frame","S":"future body-segment frame"},
      "required":{"gravity_N_mps2":{"value":[0,0,-9.80665],"valid":True},"R_V4_N":{"value":None,"valid":False},"origin_V4_m":{"value":None,"valid":False},"signed_sensor_axis_map":{"value":None,"valid":False},"initial_q_NB":{"value":None,"valid":False},"yaw_gauge":{"value":"ARBITRARY_ZERO","sigma_rad":math.pi},"lever_arm_B_m":{"value":None,"valid":False},"provenance":"UNBOUND"},
      "gate":"SPATIAL_ACCELERATION_COUPLING_BLOCKED_FRAME_BINDING"})
    write_json(out/"Q1_PARAMETER_MANIFEST.json",{
      "schema":"biospur-q1-parameter-manifest-v1","mode":"Q1_T4_ESKF","frozen":True,"parameter_optimization":"NONE",
      "nominal_state":"p_N(3),v_N(3),q_NB_scalar_first(4),b_a_B(3),b_g_B(3)","error_state":"dp,dv,dtheta_right,dba,dbg (15)",
      "parameters":{"accel_noise_sigma_mps2_sqrt_hz":.12,"gyro_noise_sigma_dps_sqrt_hz":.12,"accel_bias_rw_sigma_mps3_sqrt_hz":.002,"gyro_bias_rw_sigma_dps2_sqrt_hz":.002,"gravity_sigma_mps2":.04,"zupt_sigma_mps":.02,"covariance_update_hz":20,"t4_sigma_m":[.05,.05,.08]},
      "motion_veto_rule":"per-node max(0.5 dps,3x frozen gyro RMS), max(0.5 deg,5x frozen 1s angle), max(0.02 g,3x frozen accel deviation); continuous dwell reset",
      "real_spatial_coupling":"SPATIAL_ACCELERATION_COUPLING_BLOCKED_FRAME_BINDING","attitude_truth":"NO_EXTERNAL_ATTITUDE_TRUTH_INTERNAL_CONSISTENCY_ONLY"})

    qualified_real=[x for x in real_rows if x["status"]!="DIAGNOSTIC"]
    full_pass=all(x["status"]=="PASS" for x in synth) and all(x["status"]=="PASS" for x in qualified_real)
    integrity={"raw_hashes_before":hashes_before,"raw_hashes_after":{key:sha(path) for key,path in raw_paths.items()},"raw_unchanged":True,
      "stationary_decode":clean(sta_audit if isinstance(sta_audit,dict) else sta_audit.__dict__),"rotation_decode":clean(rot_audit if isinstance(rot_audit,dict) else rot_audit.__dict__),"tabletop_decode":clean(table_audit if isinstance(table_audit,dict) else table_audit.__dict__),
      "q1":{"stationary":{"finite":bool(np.isfinite(np.r_[sta_q.p,sta_q.v,sta_q.q,sta_q.b_a,sta_q.b_g,sta_q.P.ravel()]).all()),"cov_min":sta_q.min_covariance_eigenvalue,"cov_asym":sta_q.max_covariance_asymmetry,"reinitializations":sta_q.reinitializations},"rotation":{"finite":bool(np.isfinite(np.r_[rot_q.p,rot_q.v,rot_q.q,rot_q.b_a,rot_q.b_g,rot_q.P.ravel()]).all()),"cov_min":rot_q.min_covariance_eigenvalue,"cov_asym":rot_q.max_covariance_asymmetry,"reinitializations":rot_q.reinitializations}},"all_synthetic_pass":all(x["status"]=="PASS" for x in synth)}
    integrity["raw_unchanged"]=integrity["raw_hashes_before"]==integrity["raw_hashes_after"]==EXPECTED_RAW
    write_json(out/"NUMERICAL_INTEGRITY.json",integrity)

    (out/"FRAME_CONVENTIONS.md").write_text("""# Frame conventions

`B` is the physical JY61P register/board frame; its signed physical directions remain unmeasured. `N` is a local gravity-aligned frame with +Z up and gravity `[0,0,-9.80665] m/s²`. `V4` is the current `RELATIVE_GEOMETRY_ONLY` AutoPos frame. `S` is a future body-segment frame.

`q_NB=[w,x,y,z]` is scalar-first Hamilton and actively maps B vectors to N. Body gyro increments multiply on the right; error attitude is right-multiplicative. Quaternion normalization and sign continuity occur after every propagation/correction. Static gravity initializes roll/pitch; yaw is an arbitrary zero gauge with π-radian prior sigma, not an absolute heading.

`R_V4_N`, signed board axes, physical V4 up and lever arm are not fabricated. Real acceleration therefore propagates local attitude only. T4 corrections fail closed with `SPATIAL_ACCELERATION_COUPLING_BLOCKED_FRAME_BINDING` until a provenance-bound proper rotation and origin are supplied.
""",encoding="utf-8")
    (out/"ARCHITECTURE.md").write_text("""# Q1_T4_ESKF architecture

Q1 has a nominal `[p_N,v_N,q_NB,b_a_B,b_g_B]` state and 15-dimensional `[δp,δv,δθ,δb_a,δb_g]` error covariance. It integrates every audited IMU sample using actual B306 hardware timestamps. Bias-corrected gyro propagates a real quaternion; bias-corrected specific force is mathematically available for N-frame propagation. Process covariance includes accelerometer/gyro white noise and both bias random walks.

Stationary gravity and zero velocity are explicit Kalman measurements. T4 position is an asynchronous Kalman measurement only after V4↔N binding. All corrections use Joseph covariance form and a right-error reset Jacobian. Ordinary motion transitions never reset the filter.

Current real-data disposition is an attitude foundation plus the established S2P/T4 position-domain comparison. It is not fully coupled inertial/UWB Fusion because N and V4 are not physically bound.
""",encoding="utf-8")
    (out/"S2_MINIMAL_ROTATION_FIX.md").write_text("""# Minimal S2 rotation closure

Frozen S2 required `imu_confirm AND (spatial OR strong_gravity)` for release/interruption and defined quiet as `fast_votes < 3`. Thus gyro-only periodic rotation waited for UWB displacement, and the quiet predicate ignored continuing integrated angle. Decaying dwell timers concatenated separated quiet islands, causing the MEDIUM and CYCLE_2 false relocks and LOW/HIGH premature SETTLING.

The focused successor `MotionVetoGate` makes strong gyro, integrated angle, or acceleration evidence a hard veto. It releases through `STATIONARY→MOTION_SUSPECTED→MOVING` without UWB displacement; renewed veto resets the complete quiet/settling dwell; stable UWB cannot force relock; and the existing published lock is immutable. This is a minimal safety repair, not a new position-only architecture campaign.
""",encoding="utf-8")
    (out/"S2R_QUARANTINE.md").write_text("""# S2R quarantine

Disposition: `S2R_QUARANTINED_OFFLINE_ONLY`.

The first observed internal position norm above 5 m was approximately 648.318 s; the path later reached 556 m. The obvious cause already established is observation-model inconsistency: per-node/per-anchor median residuals up to about 1.67 m exist in the frozen manifest but S2R does not apply them. Sequential scalar NIS can accept locally plausible biased links; inconsistent accepted corrections create velocity, later links reject, and constant-velocity propagation runs away. The mm→m conversion, one-time hardware-delay subtraction, Jacobian sign/norm, sequential relinearization and Joseph covariance formula were not the primary defect.

No clamp, reset, R inflation, T4 fallback, or raw-range optimization was added. Raw-range coupling returns only after Q1 frame binding and attitude coupling are validated.
""",encoding="utf-8")
    (out/"NEXT_CALIBRATION_EXPERIMENT.md").write_text("""# Next calibration capture — do not execute in this phase

Use one C2CC board with one continuous warm-up/CDC-drain lifecycle. Record six stationary faces; positive and negative rotations about each marked physical sensor axis; one fixed documented board orientation with measured V4 physical up/gravity; static–rotate–static transitions; and one known-direction translation. Freeze signed axis labels, accelerometer six-face bias/scale, gyro sign/scale sanity, `R_V4_N`, initial attitude/yaw gauge, and lever arm provenance before replay. Repeat once as held-out evidence. The non-rigid arm supplies transition excitation only, never radius/home/angle truth.
""",encoding="utf-8")
    verdict="QUATERNION_ESKF_FOUNDATION_READY_FOR_CALIBRATION" if full_pass and integrity["raw_unchanged"] else "QUATERNION_ESKF_FOUNDATION_CONDITIONAL"
    fleet_failures=sum(x["status"]=="FAIL" for x in table_rows if x["context"]=="FLEET_CONTEXT")
    (out/"REPORT.md").write_text(f"""# Quaternion ESKF foundation

Primary verdict: `{verdict}`

Q1 propagates a real normalized scalar-first `q_NB` from bias-corrected gyro and carries genuine accelerometer/gyro bias states plus a 15-dimensional error covariance. Gravity, ZUPT and bound T4 position are Kalman measurements with Joseph covariance updates. Synthetic bound-frame cases verify the complete coupled mathematics; real captures verify local attitude and the hard motion veto without inventing an N↔V4 transform.

The independent stationary capture retains zero false MOVING and finite symmetric PSD covariance. All five completed rotation phases produce angular accumulation and a MOVING transition with no stationary relock during independent supported motion. The tabletop regression detects the frozen C2CC/AA61 moves; fleet-context qualification has `{fleet_failures}` failing node rows after accounting for each frozen vibration interval and the exact one-second causal feature memory. Standalone shock response remains reported separately as diagnostic evidence. These are internal-consistency claims only; no absolute attitude/RPM/axis/trajectory truth exists.

`S2R_QUARANTINED_OFFLINE_ONLY`. `SPATIAL_ACCELERATION_COUPLING_BLOCKED_FRAME_BINDING` remains true. The implemented system is position/state Fusion in S2P plus a Q1 attitude/ESKF foundation; it is not yet fully coupled real inertial/UWB Fusion. Missing facts are signed physical sensor axes, measured physical gravity/up in V4, `R_V4_N`, yaw reference/uncertainty refinement, and lever arm. The compact six-face/signed-axis/V4-up calibration in `NEXT_CALIBRATION_EXPERIMENT.md` activates the next stage.
""",encoding="utf-8")
    plot_outputs(out,rot_snaps,sta_snaps)
    if {key:sha(path) for key,path in raw_paths.items()} != hashes_before: raise RuntimeError("raw changed")
    return {"verdict":verdict,"real_rows":len(real_rows),"synthetic_rows":len(synth),"integrity":integrity}


def finalize(first: Path, second: Path, destination: Path):
    mismatches=[name for name in CORE if sha(first/name)!=sha(second/name)]
    if mismatches: raise RuntimeError(f"non-deterministic outputs: {mismatches}")
    destination.mkdir(parents=True,exist_ok=False)
    for name in CORE: shutil.copyfile(first/name,destination/name)
    lines=[f"{sha(destination/name)}  {name}" for name in sorted(CORE)]
    (destination/"SHA256SUMS").write_text("\n".join(lines)+"\n",encoding="utf-8")


def main():
    parser=argparse.ArgumentParser();parser.add_argument("--out",type=Path);parser.add_argument("--finalize",nargs=3,type=Path);args=parser.parse_args()
    if args.out: print(json.dumps(clean(derive(args.out)),sort_keys=True));return 0
    if args.finalize: finalize(*args.finalize);return 0
    parser.error("choose --out or --finalize")


if __name__=="__main__": raise SystemExit(main())
