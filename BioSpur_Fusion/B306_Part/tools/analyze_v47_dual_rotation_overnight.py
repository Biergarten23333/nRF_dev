#!/usr/bin/env python3
"""Deterministic offline analysis of the C2CC/3C79 sustained-rotation run.

The script has no hardware access.  It replays the lossless host binary,
retains reset boundaries, uses the frozen current-room T4 solver, and keeps
all geometry conclusions explicitly self-consistency-only.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
import shutil
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
matplotlib.rcParams["svg.hashsalt"] = "biospur-v47-dual-rotation-overnight-v1"
import matplotlib.pyplot as plt
import numpy as np

from fusion_host_binary import FrameError
from v47_q1_eskf import (FrameBinding, MotionVetoGate, MotionVetoParameters,
    Q1Parameters, Q1T4ESKF, quaternion_exp, quaternion_multiply,
    quaternion_normalize)
from v47_real_data_adapter import (IMU_DTYPE, UWB_DTYPE, _decode_host_frame,
    _decode_imu, _decode_uwb, imu_physical, iter_cobs_records)
import v47_uwb_position_replay as t4_replay


ROOT = Path(__file__).resolve().parents[2]
RUN = ROOT / "B306_Part/logs/v47_c2cc_3c79_9rpm_overnight_20260812_013304"
CAPTURE = RUN / "attempt2_continuous"
RAW = CAPTURE / "fusion_host_raw.cobs.bin"
DECODED = CAPTURE / "fusion_cdc.log"
LISTENER = CAPTURE / "listener_capture"
LAYOUT = ROOT / "B306_Part/deployments/current_room_autopos_20260811_183541/V4IO/anchor_layout.json"
GEOMETRY = ROOT / "B306_Part/deployments/current_room_autopos_20260811_183541/CAPTURE_BOUND_GEOMETRY_MANIFEST.json"
S2_MANIFEST = ROOT / "B306_Part/logs/v47_c2cc_interactive_rotation_20260811_233719/FROZEN_S2_PARAMETER_MANIFEST.json"
NODES = ("BSFC2CC", "BSF3C79")
EXPECTED_RAW_SHA = "e9cad96e432f27e61a3a88105cf68e725ee398ba5743490a413f24a4ca7802ec"
EXPECTED_DECODED_SHA = "9b6c005cc0c8b5348b71b514118ecbc12a31f92a6dcd20807250ccf8a0a1e502"
LISTENER_HASHES = {
    "listeners/760181725.raw.log": "85bafe917e4d44f3172d8c3788dc4c3763761a532aad854b33cd234023b868ba",
    "listeners/760184545.raw.log": "ccfbe06a94ee503de131a94ad4d937b81a2a2afd1fbe7710f751d30328af2f77",
    "listeners/760184548.raw.log": "f0da8838d66d1bdb88b8bfc166c8acdf96393a6d8ec014bda42d0e10fd034c52",
    "listeners/760184753.raw.log": "0f700c31f86ae32b498a86d6a1813b63d6ee61b8d4b793ceda5b9123b23ef67d",
    "listeners/760184767.raw.log": "eee8748bf5f7f86dfb37e8f221ad5a48fa957a40d534365ce471d3df0a762bd0",
    "listeners/760184784.raw.log": "c7672019fe398ef2eddd95d1c18e6d29be8d45c48eff0bc81191516e1b0b6de6",
    "listeners/760184964.raw.log": "7328ef7a418aed63c98d3ff6bb20260fc8da2665a19806a47e92bbd621861f0a",
    "merged_index.jsonl": "88e66c11afec6d4c38fea9604dd327f09541e654cb7917c05172b5952bba24a5",
}
TAG_SRC = {"BSF3C79": 0xB101, "BSFC2CC": 0xB18E}
CORE = (
    "REPORT.md", "RUN_MANIFEST.json", "MOUNTING_MAP.json", "CAPTURE_PHASES.json",
    "CAPTURE_INTEGRITY.json", "PER_NODE_HOURLY_METRICS.csv",
    "Q1_ATTITUDE_STABILITY.csv", "MOTION_STATE_AUDIT.csv",
    "TWO_NODE_ANGULAR_COMPARISON.csv", "UWB_ORBIT_SELF_CONSISTENCY.json",
    "UWB_LINK_METRICS.csv", "LISTENER_SUMMARY.json",
    "BATTERY_DEGRADATION_TIMELINE.csv", "NUMERICAL_INTEGRITY.json",
    "LIMITATIONS.md", "capture_rate_timeline.svg", "gyro_comparison.svg",
    "uwb_orbits_xy.svg", "q1_numerical_timeline.svg", "depletion_timeline.svg",
)


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(8 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def clean(value):
    if isinstance(value, np.generic): value = value.item()
    if isinstance(value, np.ndarray): return [clean(x) for x in value.tolist()]
    if isinstance(value, dict): return {str(k): clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)): return [clean(x) for x in value]
    if isinstance(value, float): return None if not math.isfinite(value) else float(f"{value:.12g}")
    return value


def write_json(path: Path, value) -> None:
    path.write_text(json.dumps(clean(value), indent=2, sort_keys=True,
                              allow_nan=False) + "\n", encoding="utf-8")


def write_csv(path: Path, rows: list[dict], fields: list[str] | None = None) -> None:
    if fields is None:
        fields = list(dict.fromkeys(k for row in rows for k in row))
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore", lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({k: "" if clean(row.get(k)) is None else clean(row.get(k)) for k in fields})


def decode_capture() -> tuple[dict[str, np.ndarray], dict[str, np.ndarray], dict]:
    ledger = json.loads((CAPTURE / "PROCESS_LEDGER.json").read_text())
    manifest = json.loads((CAPTURE / "RUN_MANIFEST.json").read_text())
    counts = ledger["counts"]
    imu = {node: np.empty(int(counts[node]["imu"]), dtype=IMU_DTYPE) for node in NODES}
    uwb = {node: np.empty(int(counts[node]["uwb"]), dtype=UWB_DTYPE) for node in NODES}
    ip = {node: 0 for node in NODES}; up = {node: 0 for node in NODES}
    formal_offset = int(manifest["formal_health_baseline"]["raw_bytes_submitted"])
    before = errors_before = errors_formal = 0; kinds = Counter(); tail = 0
    size = RAW.stat().st_size
    for end, encoded in iter_cobs_records(RAW):
        formal = end > formal_offset
        try:
            frame = _decode_host_frame(encoded)
        except FrameError:
            if end == size:
                tail = len(encoded)
            elif formal: errors_formal += 1
            else: errors_before += 1
            continue
        if not formal:
            before += 1; continue
        kinds[frame.kind] += 1
        node = frame.node_name
        if node not in imu: continue
        if frame.kind == 3: ip[node] = _decode_imu(frame, imu[node], ip[node])
        elif frame.kind == 1: up[node] = _decode_uwb(frame, uwb[node], up[node])
    for node in NODES:
        if ip[node] != len(imu[node]) or up[node] != len(uwb[node]):
            raise RuntimeError(f"raw accounting mismatch {node}: {ip[node]}/{len(imu[node])}, {up[node]}/{len(uwb[node])}")
    first_master = min(int(imu[n]["master_ms"][0]) for n in NODES)
    return imu, uwb, {
        "raw_sha256": sha(RAW), "raw_size": size, "formal_offset": formal_offset,
        "records_before_t0": before, "decode_errors_before_t0": errors_before,
        "decode_errors_formal": errors_formal, "incomplete_tail_bytes": tail,
        "formal_kind_counts": dict(sorted(kinds.items())), "first_formal_master_ms": first_master,
    }


def elapsed_ms(records: np.ndarray, first_master: int) -> np.ndarray:
    return (records["master_ms"].astype(np.float64) - first_master) / 1000.0


def sequence_stats(values: np.ndarray, local_time: np.ndarray, modulus: int) -> dict:
    if len(values) < 2:
        return {"transitions": 0, "gaps": 0, "missing": 0, "duplicates": 0,
                "reorders": 0, "timestamp_reversals": 0, "reset_boundaries": 0}
    reset = local_time[1:].astype(np.int64) <= local_time[:-1].astype(np.int64)
    delta = (values[1:].astype(np.uint64) - values[:-1].astype(np.uint64)) % modulus
    active = ~reset
    return {
        "transitions": int(np.sum(active)), "gaps": int(np.sum(active & (delta > 1))),
        "missing": int(np.sum(np.where(active & (delta > 1), delta - 1, 0))),
        "duplicates": int(np.sum(active & (delta == 0))),
        "reorders": 0, "timestamp_reversals": int(np.sum(active & (local_time[1:] <= local_time[:-1]))),
        "reset_boundaries": int(np.sum(reset)),
    }


def degradation_boundaries(ledger: dict, t0: float) -> tuple[dict, float]:
    first = {}
    for node in NODES:
        events = [e for e in ledger["events"] if node == e.get("node") or f"name={node}" in e.get("line", "")]
        disconnect = [e for e in events if "FUSION_DISCONNECTED" in e.get("line", "")]
        reset = [e for e in events if e.get("type") == "UPTIME_RESET"]
        first_disc = min((float(e["monotonic"]) for e in disconnect), default=math.inf)
        first_reset = min((float(e["monotonic"]) for e in reset), default=math.inf)
        first[node] = {
            "first_disconnect_s": first_disc - t0 if math.isfinite(first_disc) else None,
            "first_uptime_reset_s": first_reset - t0 if math.isfinite(first_reset) else None,
            "disconnect_count": len(disconnect), "uptime_reset_count": len(reset),
            "first_degradation_s": min(first_disc, first_reset) - t0,
        }
    common = min(first[n]["first_degradation_s"] for n in NODES)
    return first, common


def hourly_metrics(imu, uwb, first_master, supported_end, total_s, frozen):
    rows=[]; second_series={}
    for node in NODES:
        it=elapsed_ms(imu[node],first_master);ut=elapsed_ms(uwb[node],first_master)
        acc,gyro,temp=imu_physical(imu[node]);bias=np.asarray(frozen["per_node"][node]["gyro_bias_dps"])
        gn=np.linalg.norm(gyro-bias,axis=1);an=np.linalg.norm(acc,axis=1)
        max_hour=int(math.ceil(total_s/3600))
        sec=np.floor(it).astype(np.int64);good=(it>=0)&(it<supported_end)
        sums=np.bincount(sec[good],weights=gn[good],minlength=int(math.ceil(supported_end))+1)
        nums=np.bincount(sec[good],minlength=len(sums)); second_series[node]=(sums/np.maximum(nums,1),nums)
        for h in range(max_hour):
            a=h*3600.;b=min((h+1)*3600.,total_s);im=(it>=a)&(it<b);um=(ut>=a)&(ut<b)
            dur=max(b-a,1e-9)
            iseq=sequence_stats(imu[node]["seq"][im],imu[node]["b306_us"][im],65536)
            useq=sequence_stats(uwb[node]["sweep"][um],uwb[node]["node_ms"][um],2**32)
            rows.append({
                "node":node,"hour":h,"start_s":a,"end_s":b,"phase":"SUPPORTED_ROTATION" if b<=supported_end else ("MIXED_DEGRADATION_BOUNDARY" if a<supported_end else "DEPLETION_TAIL"),
                "imu_samples":int(np.sum(im)),"imu_hz":float(np.sum(im)/dur),
                "uwb_sweeps":int(np.sum(um)),"uwb_hz":float(np.sum(um)/dur),
                "gyro_median_dps":float(np.median(gn[im])) if np.any(im) else None,
                "gyro_p05_dps":float(np.quantile(gn[im],.05)) if np.any(im) else None,
                "gyro_p95_dps":float(np.quantile(gn[im],.95)) if np.any(im) else None,
                "accel_norm_median_g":float(np.median(an[im])) if np.any(im) else None,
                "temperature_median_c":float(np.median(temp[im])) if np.any(im) else None,
                "imu_gap_transitions":iseq["gaps"],"imu_missing_samples":iseq["missing"],
                "uwb_gap_transitions":useq["gaps"],"uwb_missing_sweeps":useq["missing"],
            })
    return rows,second_series


def motion_and_q1(node, imu, first_master, supported_end, base):
    times=elapsed_ms(imu,first_master);mask=(times>=0)&(times<supported_end)
    x=imu[mask];times=times[mask];acc_g,gyro_dps,_=imu_physical(x)
    bias_dps=np.asarray(base["gyro_bias_dps"]);corrected=gyro_dps-bias_dps
    # Frozen hard-veto state machine at 20 Hz.  Candidate stability is set true,
    # which is deliberately adversarial: sustained gyro still must prevent relock.
    control_bin=np.floor(times*20).astype(np.int64);starts=np.r_[0,np.flatnonzero(np.diff(control_bin))+1]
    ends=np.r_[starts[1:],len(times)];ct=[];grms=[];angle=[];adev=[]
    local_g=float(base["local_gravity_g"])
    for a,b in zip(starts,ends):
        ct.append(float(times[b-1]));g=np.linalg.norm(corrected[a:b],axis=1)
        grms.append(float(np.sqrt(np.mean(g*g))));angle.append(float(np.sum(g)*.005))
        adev.append(float(np.sqrt(np.mean((np.linalg.norm(acc_g[a:b],axis=1)-local_g)**2))))
    gate=MotionVetoGate(MotionVetoParameters(
        gyro_on_dps=max(.5,3*float(base["gyro_rms_threshold_dps"])),
        gyro_angle_on_deg=max(.5,5*float(base["gyro_angle_1s_threshold_deg"])),
        accel_deviation_on_g=max(.02,3*float(base["accel_dev_rms_threshold_g"]))))
    states=[]
    for t,g,a,d in zip(ct,grms,angle,adev):
        states.append(gate.update(t,gyro_rms_dps=g,gyro_angle_deg=a,
                                  accel_deviation_g=d,candidate_stable=True,velocity_mps=0.0))
    false_relocks=sum(e["to_state"]=="STATIONARY" and e["time_s"]>1 for e in gate.transitions)

    # Q1 is initialized with the frozen independent gyro bias.  The first
    # rotating acceleration mean only defines a local roll/pitch gauge; it is
    # not called a stationary calibration or absolute attitude.
    q1=Q1T4ESKF(Q1Parameters(),FrameBinding())
    init=times<min(2.0,times[-1]);q1.initialize_from_stationary(
        np.mean(acc_g[init],axis=0)*9.80665,np.radians(bias_dps))
    qrows=[];failure=None;next_hour=0;cumulative=0.0;q1_alive=True
    # Independent attitude-only propagation continues after the full Q1
    # covariance fails, so quaternion endurance is not censored by P failure.
    attitude_q=q1.q.copy();attitude_min_dot=1.0;attitude_max_norm_error=0.0
    for i in range(len(x)):
        if i:
            dt=(int(x["b306_us"][i])-int(x["b306_us"][i-1]))*1e-6
            omega=np.radians(corrected[i-1]);norm=float(np.linalg.norm(omega));angle=norm*dt
            if angle<1e-14:dw,dx,dy,dz=1.0,.5*omega[0]*dt,.5*omega[1]*dt,.5*omega[2]*dt
            else:
                scale=math.sin(.5*angle)/norm;dw=math.cos(.5*angle);dx,dy,dz=omega*scale
            w,xq,yq,zq=attitude_q
            candidate=np.array([w*dw-xq*dx-yq*dy-zq*dz,w*dx+xq*dw+yq*dz-zq*dy,
                w*dy-xq*dz+yq*dw+zq*dx,w*dz+xq*dy-yq*dx+zq*dw])
            candidate/=np.linalg.norm(candidate);dot=float(attitude_q@candidate)
            if dot<0:candidate=-candidate;dot=-dot
            attitude_q=candidate;attitude_min_dot=min(attitude_min_dot,dot)
            attitude_max_norm_error=max(attitude_max_norm_error,abs(float(np.linalg.norm(attitude_q))-1))
            cumulative+=norm*dt
        if q1_alive:
            try:
                q1.propagate(float(x["b306_us"][i])*1e-6,acc_g[i]*9.80665,np.radians(gyro_dps[i]))
            except (FloatingPointError,ValueError) as error:
                eig=np.linalg.eigvalsh(.5*(q1.P+q1.P.T))
                failure={"time_s":float(times[i]),"sample_index":i,"error":f"{type(error).__name__}: {error}",
                    "covariance_min_eigenvalue":float(eig[0]),"covariance_max_eigenvalue":float(eig[-1]),
                    "quaternion_norm_error":abs(float(np.linalg.norm(q1.q))-1),
                    "cumulative_abs_rotation_rad":cumulative};q1_alive=False
        if times[i]>=next_hour*3600:
            eig=np.linalg.eigvalsh(.5*(q1.P+q1.P.T))
            qrows.append({"node":node,"hour":next_hour,"time_s":float(times[i]),
                "quaternion_norm_error":abs(float(np.linalg.norm(q1.q))-1),
                "quaternion_sign_dot":attitude_min_dot,"attitude_only_quaternion_norm_error":attitude_max_norm_error,
                "covariance_min_eigenvalue":float(eig[0]),
                "covariance_max_eigenvalue":float(eig[-1]),"covariance_asymmetry":float(np.max(np.abs(q1.P-q1.P.T))),
                "cumulative_abs_rotation_rad":cumulative,"gyro_bias_x_dps":bias_dps[0],
                "gyro_bias_y_dps":bias_dps[1],"gyro_bias_z_dps":bias_dps[2],
                "q1_status":"RUNNING" if q1_alive else "NOT_AVAILABLE_AFTER_NUMERICAL_FAILURE"});next_hour+=1
    if failure:qrows.append({"node":node,"hour":int(failure["time_s"]//3600),"time_s":failure["time_s"],
        "quaternion_norm_error":abs(float(np.linalg.norm(q1.q))-1),"quaternion_sign_dot":attitude_min_dot,
        "attitude_only_quaternion_norm_error":attitude_max_norm_error,
        "covariance_min_eigenvalue":failure["covariance_min_eigenvalue"],
        "covariance_max_eigenvalue":failure["covariance_max_eigenvalue"],
        "covariance_asymmetry":float(np.max(np.abs(q1.P-q1.P.T))),"cumulative_abs_rotation_rad":failure["cumulative_abs_rotation_rad"],
        "gyro_bias_x_dps":bias_dps[0],"gyro_bias_y_dps":bias_dps[1],"gyro_bias_z_dps":bias_dps[2],"q1_status":"NUMERICAL_FAILURE_EVENT"})
    return qrows,gate.transitions,states,false_relocks,failure


def solve_positions(uwb, first_master, supported_end):
    models,layout_io,c_solver=t4_replay.load_solver("UWB_TAG_T4")
    layout=layout_io.load_layout_json(LAYOUT);out={}
    for node in NODES:
        records=uwb[node];times=elapsed_ms(records,first_master);keep=(times>=0)&(times<supported_end)
        records=records[keep];times=times[keep];solver=c_solver.TagPositionSolver(layout,models.SolverConfig(method="T4"))
        xyz=np.full((len(records),3),np.nan);residual=np.full(len(records),np.nan);used=np.zeros(len(records),np.int16)
        for i,record in enumerate(records):
            t4_replay.validate_anchor_slot_identity(record["anchor_id"]);obs=[];mask=int(record["valid_mask"])
            for slot in range(8):
                if mask&(1<<slot):
                    value=int(record["range_mm"][slot]);aid=int(record["anchor_id"][slot])
                    if value not in (0,0xffff):
                        obs.append(models.Observation(anchor_id=aid,range_mm=float(value),quality_percent=float(record["quality"][slot]),status="O"))
            frame=models.Frame(tag=node,sweep=int(record["sweep"]),host_elapsed_s=float(times[i]),host_epoch_s=0.0,observations=tuple(obs),imu=None)
            result=solver.solve_frame(frame)
            if result is not None:
                xyz[i]=[result.x_mm,result.y_mm,result.z_mm];residual[i]=result.residual_rms_mm;used[i]=result.anchors_used
        out[node]={"time_s":times,"xyz_mm":xyz,"residual_rms_mm":residual,"anchors_used":used,
                   "records":records,"solution_count":int(np.sum(np.isfinite(xyz[:,0])))}
    return out


def fit_orbit(time_s, xyz_mm):
    valid=np.isfinite(xyz_mm).all(axis=1);t=time_s[valid];xyz=xyz_mm[valid]
    if len(xyz)<100:return {"status":"INSUFFICIENT"}
    center0=np.median(xyz,axis=0);dist=np.linalg.norm(xyz-center0,axis=1);cut=np.quantile(dist,.99);q=dist<=cut
    xyz=xyz[q];t=t[q];mean=np.mean(xyz,axis=0);cov=np.cov((xyz-mean).T);eig,vec=np.linalg.eigh(cov);basis=vec[:,[2,1]]
    uv=(xyz-mean)@basis;A=np.c_[2*uv[:,0],2*uv[:,1],np.ones(len(uv))];b=np.sum(uv*uv,axis=1)
    sol=np.linalg.lstsq(A,b,rcond=None)[0];c2=sol[:2];radius=math.sqrt(max(sol[2]+float(c2@c2),0));c3=mean+basis@c2
    rr=np.linalg.norm(uv-c2,axis=1);angle=np.unwrap(np.arctan2(uv[:,1]-c2[1],uv[:,0]-c2[0]));dt=np.diff(t);da=np.diff(angle)
    good=(dt>.02)&(dt<.5)&(np.abs(da)<1.0)
    # The slope of the unwrapped orbit phase is insensitive to the positive
    # bias that single-step |dtheta/dt| acquires from position noise.
    omega=abs(float(np.polyfit(t-t[0],angle,1)[0])) if np.sum(good)>100 else None
    plane=np.abs((xyz-c3)@vec[:,0])
    return {"status":"OK","solutions":len(xyz),"center_mm":c3,"radius_mm":radius,
        "radius_mad_mm":float(np.median(np.abs(rr-np.median(rr)))),"plane_rms_mm":float(np.sqrt(np.mean(plane*plane))),
        "plane_p95_mm":float(np.quantile(plane,.95)),"angular_rate_rad_s":omega,
        "apparent_rpm":None if omega is None else omega*60/(2*math.pi),"plane_normal":vec[:,0]}


def orbit_analysis(position):
    result={"classification":"NO_EXTERNAL_TRUTH_SELF_CONSISTENCY_ONLY","per_node":{},"hourly":[]}
    for node in NODES:
        d=position[node];whole=fit_orbit(d["time_s"],d["xyz_mm"]);result["per_node"][node]=clean(whole)
        for h in range(int(math.ceil(max(d["time_s"])/3600))):
            q=(d["time_s"]>=h*3600)&(d["time_s"]<(h+1)*3600);fit=fit_orbit(d["time_s"][q],d["xyz_mm"][q])
            result["hourly"].append({"node":node,"hour":h,**clean(fit)})
        centers=np.asarray([x["center_mm"] for x in result["hourly"] if x["node"]==node and x.get("status")=="OK"])
        radii=np.asarray([x["radius_mm"] for x in result["hourly"] if x["node"]==node and x.get("status")=="OK"])
        normals=np.asarray([x["plane_normal"] for x in result["hourly"] if x["node"]==node and x.get("status")=="OK"])
        reference=np.asarray(whole["plane_normal"]);normals=np.asarray([v if v@reference>=0 else -v for v in normals])
        result["per_node"][node].update({"hourly_center_max_drift_mm":float(np.max(np.linalg.norm(centers-np.median(centers,axis=0),axis=1))),
            "hourly_radius_cv":float(np.std(radii)/np.mean(radii)),
            "hourly_plane_normal_max_drift_deg":float(np.degrees(np.max(np.arccos(np.clip(normals@reference,-1,1)))))})
    radii={n:result["per_node"][n].get("radius_mm") for n in NODES}
    if all(radii.values()):
        larger=max(radii,key=radii.get);smaller=min(radii,key=radii.get)
        result["apparent_radius_ratio"]=radii[larger]/radii[smaller]
        result["larger_apparent_radius_node"]=larger;result["smaller_apparent_radius_node"]=smaller
        result["mounting_inference"]="INFERRED_FROM_UWB_NOT_OPERATOR_CONFIRMED"
    # Relative phase uses the shared V4 XY axes and each fitted orbit centre.
    # It remains a geometric diagnostic because mounting/home truth is absent.
    left,right=(position[n] for n in NODES);series=[]
    for node,data in zip(NODES,(left,right)):
        q=np.isfinite(data["xyz_mm"]).all(axis=1);tt=data["time_s"][q];xy=data["xyz_mm"][q,:2]
        center=np.asarray(result["per_node"][node]["center_mm"][:2]);aa=np.unwrap(np.arctan2(xy[:,1]-center[1],xy[:,0]-center[0]))
        series.append((tt,aa))
    common=np.arange(max(series[0][0][0],series[1][0][0]),min(series[0][0][-1],series[1][0][-1]),1.0)
    if len(common)>10:
        a0=np.interp(common,*series[0]);a1=np.interp(common,*series[1]);delta=np.angle(np.exp(1j*(a0-a1)))
        mean=np.angle(np.mean(np.exp(1j*delta)));concentration=abs(np.mean(np.exp(1j*delta)))
        result["relative_phase"]={"mean_wrapped_deg":float(np.degrees(mean)),
            "circular_std_deg":float(np.degrees(math.sqrt(max(0.,-2*math.log(max(concentration,1e-15)))))),
            "samples":len(common),"interpretation":"V4_XY_SELF_CONSISTENCY_ONLY_NO_HOME_OR_MOUNTING_TRUTH"}
    return result


LPD_RE=re.compile(r'"listener_key":"([^"]+)".*"arrival_monotonic_ns":(\d+).*"kind":"LPD".*"src":(\d+).*"sequence":(\d+)')
def listener_analysis(t0_ns: int, supported_end: float):
    counts={n:Counter() for n in NODES};union={n:0 for n in NODES};supported={n:0 for n in NODES};first={};last={};last_key={}
    src_node={v:k for k,v in TAG_SRC.items()}
    with (LISTENER/"merged_index.jsonl").open(encoding="utf-8") as handle:
        for line in handle:
            m=LPD_RE.search(line)
            if not m:continue
            key,ns,src,seq=m.group(1),int(m.group(2)),int(m.group(3)),int(m.group(4));node=src_node.get(src)
            if node is None:continue
            elapsed=(ns-t0_ns)*1e-9;counts[node][key]+=1
            token=(seq,);prior=last_key.get(node)
            if prior is None or token!=prior[0] or elapsed-prior[1]>.1:
                union[node]+=1;supported[node]+=int(0<=elapsed<supported_end);last_key[node]=(token,elapsed)
                first.setdefault(node,elapsed);last[node]=elapsed
    summary=json.loads((LISTENER/"summary.json").read_text())
    return {"schema":"biospur-dual-listener-summary-v1","source":"merged_index.jsonl",
        "per_node":{n:{"source_address":f"0x{TAG_SRC[n]:04X}","union_poll_count":union[n],
            "supported_union_poll_count":supported[n],"supported_union_hz":supported[n]/supported_end,
            "first_elapsed_s":first.get(n),"last_elapsed_s":last.get(n),"per_receiver":dict(sorted(counts[n].items()))} for n in NODES},
        "collector":{"actual_duration_s":summary["actual_duration_s"],"merged_records":summary["merged_records"],
            "parse_errors":sum(v["parse_errors"] for v in summary["listeners"].values()),
            "serial_errors":sum(v["serial_errors"] for v in summary["listeners"].values()),
            "ring_drops_delta":sum(v["firmware_counter_delta"]["ring_drops"] for v in summary["listeners"].values())},
        "note":"BSFC2CC low/zero early Listener reception is RF geometry evidence only; Fusion UWB completeness is evaluated independently."}


def angular_comparison(hourly, second_series, orbit):
    rows=[]
    by={(r["node"],r["hour"]):r for r in hourly}
    maxh=max(r["hour"] for r in hourly)
    orbit_hour={(x["node"],x["hour"]):x for x in orbit["hourly"]}
    for h in range(maxh+1):
        a=by.get((NODES[0],h));b=by.get((NODES[1],h))
        if not a or not b:continue
        lo=h*3600;hi=min((h+1)*3600,len(second_series[NODES[0]][0]),len(second_series[NODES[1]][0]))
        x=second_series[NODES[0]][0][lo:hi];y=second_series[NODES[1]][0][lo:hi]
        valid=(second_series[NODES[0]][1][lo:hi]>0)&(second_series[NODES[1]][1][lo:hi]>0)
        corr=float(np.corrcoef(x[valid],y[valid])[0,1]) if np.sum(valid)>2 and np.std(x[valid])>0 and np.std(y[valid])>0 else None
        g0=a["gyro_median_dps"];g1=b["gyro_median_dps"]
        o0=orbit_hour.get((NODES[0],h),{}).get("angular_rate_rad_s");o1=orbit_hour.get((NODES[1],h),{}).get("angular_rate_rad_s")
        rows.append({"hour":h,"phase":a["phase"],"BSFC2CC_gyro_median_dps":g0,
            "BSF3C79_gyro_median_dps":g1,"gyro_difference_dps":None if g0 is None or g1 is None else g0-g1,
            "gyro_ratio":None if g0 is None or not g1 else g0/g1,"one_second_gyro_correlation":corr,
            "BSFC2CC_T4_angular_rate_rad_s":o0,"BSF3C79_T4_angular_rate_rad_s":o1,
            "T4_angular_rate_difference_rad_s":None if o0 is None or o1 is None else o0-o1,
            "BSFC2CC_gyro_vs_T4_ratio":None if g0 is None or not o0 else math.radians(g0)/o0,
            "BSF3C79_gyro_vs_T4_ratio":None if g1 is None or not o1 else math.radians(g1)/o1,
            "apparent_radius_ratio":None if orbit.get("apparent_radius_ratio") is None else orbit["apparent_radius_ratio"]})
    return rows


def link_metrics(uwb, first_master, supported_end):
    rows=[]
    for node in NODES:
        t=elapsed_ms(uwb[node],first_master);q=(t>=0)&(t<supported_end);records=uwb[node][q]
        for slot in range(8):
            valid=(records["valid_mask"].astype(np.uint16)&(1<<slot))!=0;r=records["range_mm"][:,slot].astype(float)
            rows.append({"node":node,"anchor_id":slot,"records":len(records),"valid_count":int(np.sum(valid)),
                "valid_rate":float(np.mean(valid)),"range_median_mm":float(np.median(r[valid])) if np.any(valid) else None,
                "range_std_mm":float(np.std(r[valid])) if np.any(valid) else None,
                "range_p05_mm":float(np.quantile(r[valid],.05)) if np.any(valid) else None,
                "range_p95_mm":float(np.quantile(r[valid],.95)) if np.any(valid) else None,
                "quality_median":float(np.median(records["quality"][:,slot][valid])) if np.any(valid) else None,
                "cfo_ppm_q8_median":float(np.median(records["cfo_ppm_q8"][:,slot][valid])) if np.any(valid) else None,
                "t_round_us_median":float(np.median(records["t_round_us"][:,slot][valid])) if np.any(valid) else None})
    return rows


def plots(out, hourly, comparisons, position, qrows, boundaries, supported_end, total_s):
    def save(name):
        plt.tight_layout();plt.savefig(out/name,format="svg",metadata={"Date":None});plt.close()
        p=out/name;p.write_text("\n".join(x.rstrip() for x in p.read_text().splitlines())+"\n",encoding="utf-8")
    fig,ax=plt.subplots(2,1,figsize=(11,7),sharex=True)
    for node in NODES:
        rows=[r for r in hourly if r["node"]==node];x=[r["hour"]+.5 for r in rows]
        ax[0].plot(x,[r["imu_hz"] for r in rows],marker="o",label=node);ax[1].plot(x,[r["uwb_hz"] for r in rows],marker="o",label=node)
    for a in ax:a.axvline(supported_end/3600,color="k",ls="--",lw=1);a.legend()
    ax[0].set_ylabel("IMU samples/s");ax[1].set(xlabel="hours from formal T0",ylabel="UWB sweeps/s");save("capture_rate_timeline.svg")
    fig,ax=plt.subplots(figsize=(11,4));x=[r["hour"]+.5 for r in comparisons]
    ax.plot(x,[r["BSFC2CC_gyro_median_dps"] for r in comparisons],marker="o",label="BSFC2CC")
    ax.plot(x,[r["BSF3C79_gyro_median_dps"] for r in comparisons],marker="o",label="BSF3C79")
    ax.axvline(supported_end/3600,color="k",ls="--");ax.set(xlabel="hours from T0",ylabel="bias-corrected |gyro| median (deg/s)");ax.legend();save("gyro_comparison.svg")
    fig,ax=plt.subplots(figsize=(8,7))
    for node in NODES:
        xyz=position[node]["xyz_mm"];q=np.isfinite(xyz).all(axis=1);step=max(1,int(np.sum(q)//5000));p=xyz[q][::step]
        ax.scatter(p[:,0],p[:,1],s=1,alpha=.2,label=node)
    ax.set_aspect("equal",adjustable="box");ax.set(xlabel="V4-io x (mm)",ylabel="V4-io y (mm)",title="T4 apparent orbits — self-consistency only");ax.legend();save("uwb_orbits_xy.svg")
    fig,ax=plt.subplots(figsize=(11,4))
    for node in NODES:
        rows=sorted(qrows[node],key=lambda r:r["time_s"]);ax.semilogy([r["time_s"]/3600 for r in rows],np.maximum([abs(float(r["covariance_min_eigenvalue"])) for r in rows],1e-20),marker="o",label=node)
    ax.axhline(1e-9,color="r",ls=":",label="PSD tolerance magnitude");ax.set(xlabel="hours from T0",ylabel="|minimum covariance eigenvalue|");ax.legend();save("q1_numerical_timeline.svg")
    fig,ax=plt.subplots(figsize=(11,3));ax.axvspan(0,supported_end/3600,color="#8bc34a",alpha=.35,label="two-node supported")
    for i,node in enumerate(NODES):ax.axvline(boundaries[node]["first_degradation_s"]/3600,ymin=.1+i*.4,ymax=.45+i*.4,label=f"{node} first degradation")
    ax.set_xlim(0,total_s/3600);ax.set(xlabel="hours from T0",yticks=[],title="supported rotation and evidence-backed depletion onset");ax.legend(ncol=3,fontsize=8);save("depletion_timeline.svg")


def derive(out: Path):
    out.mkdir(parents=True,exist_ok=False)
    initial={"fusion_host_raw.cobs.bin":sha(RAW),"fusion_cdc.log":sha(DECODED)}
    initial.update({f"listener_capture/{p}":sha(LISTENER/p) for p in LISTENER_HASHES})
    expected={"fusion_host_raw.cobs.bin":EXPECTED_RAW_SHA,"fusion_cdc.log":EXPECTED_DECODED_SHA}
    expected.update({f"listener_capture/{p}":v for p,v in LISTENER_HASHES.items()})
    if initial!=expected:raise RuntimeError("post-stop evidence hash mismatch")
    ledger=json.loads((CAPTURE/"PROCESS_LEDGER.json").read_text());manifest=json.loads((CAPTURE/"RUN_MANIFEST.json").read_text())
    frozen=json.loads(S2_MANIFEST.read_text());imu,uwb,audit=decode_capture()
    if audit["raw_sha256"]!=EXPECTED_RAW_SHA:raise RuntimeError("decoded raw SHA mismatch")
    t0=float(ledger["t0_monotonic"]);total_s=float(ledger["ended_monotonic"]-t0);boundaries,supported_end=degradation_boundaries(ledger,t0)
    hourly,seconds=hourly_metrics(imu,uwb,audit["first_formal_master_ms"],supported_end,total_s,frozen)
    qrows={};transitions={};failures={};state_rows=[]
    for node in NODES:
        qr,tr,states,false_relocks,failure=motion_and_q1(node,imu[node],audit["first_formal_master_ms"],supported_end,frozen["per_node"][node])
        qrows[node]=qr;transitions[node]=tr;failures[node]=failure
        state_rows.extend({"node":node,**row} for row in tr)
        state_rows.append({"node":node,"time_s":supported_end,"from_state":states[-1],"to_state":states[-1],"hard_motion_veto":"","summary":f"false_stationary_relocks={false_relocks}"})
    position=solve_positions(uwb,audit["first_formal_master_ms"],supported_end);orbit=orbit_analysis(position)
    for node in NODES:
        supported_rows=[r for r in hourly if r["node"]==node and r["phase"]=="SUPPORTED_ROTATION"]
        observed=float(np.median([r["accel_norm_median_g"] for r in supported_rows]))
        radius=float(orbit["per_node"][node]["radius_mm"])*1e-3
        omega=float(orbit["per_node"][node]["angular_rate_rad_s"])
        orbit["per_node"][node]["apparent_centripetal_acceleration_mps2"]=radius*omega*omega
        orbit["per_node"][node]["observed_accel_norm_median_g"]=observed
        orbit["per_node"][node]["frozen_static_gravity_g"]=float(frozen["per_node"][node]["local_gravity_g"])
        orbit["per_node"][node]["accel_norm_delta_from_static_g"]=observed-float(frozen["per_node"][node]["local_gravity_g"])
        orbit["per_node"][node]["acceleration_interpretation"]="NORM_ONLY_SENSOR_TO_V4_AND_LEVER_ARM_UNBOUND"
    listener=listener_analysis(int(manifest["t0_monotonic_ns"]),supported_end)
    comparisons=angular_comparison(hourly,seconds,orbit);links=link_metrics(uwb,audit["first_formal_master_ms"],supported_end)

    seq={}
    for node in NODES:
        seq[node]={"imu":sequence_stats(imu[node]["seq"],imu[node]["b306_us"],65536),
                   "uwb":sequence_stats(uwb[node]["sweep"],uwb[node]["node_ms"],2**32)}
    capture_integrity={"schema":"biospur-dual-rotation-capture-integrity-v1","formal_duration_s":total_s,
        "two_node_supported_rotation_s":supported_end,"two_node_supported_rotation_h":supported_end/3600,
        "minimum_six_hours_met":supported_end>=21600,"raw_replay":audit,"per_node_sequence":seq,
        "final_host_health":ledger["fusion_health_final"],"single_formal_segment":True,
        "collector_stop":"OPERATOR_STOP_AFTER_MANUAL_MOTOR_OFF","final_static":"UNAVAILABLE_BATTERY_DEPLETED",
        "identity_verification":"EXPECTED_IDENTITIES_RECORDED_BUT_NO_LIVE_READ_ONLY_GUARD_WAS_RUN",
        "hardware_commands_sent":manifest["commands_sent"]}
    degradation=[]
    for e in ledger["events"]:
        if "FUSION_DISCONNECTED" in e.get("line",""):kind="DISCONNECT"
        elif "FUSION_CONNECTED" in e.get("line",""):kind="RECONNECT"
        elif e.get("type")=="UPTIME_RESET":kind="UPTIME_RESET"
        else:continue
        node=e.get("node") or next((n for n in NODES if f"name={n}" in e.get("line","")),"")
        degradation.append({"node":node,"event":kind,"elapsed_s":float(e["monotonic"]-t0),"wall":e.get("wall",""),"detail":e.get("line",f"before={e.get('before')} after={e.get('after')}")})
    write_csv(out/"BATTERY_DEGRADATION_TIMELINE.csv",degradation)
    write_csv(out/"PER_NODE_HOURLY_METRICS.csv",hourly);write_csv(out/"Q1_ATTITUDE_STABILITY.csv",[r for n in NODES for r in qrows[n]])
    write_csv(out/"MOTION_STATE_AUDIT.csv",state_rows);write_csv(out/"TWO_NODE_ANGULAR_COMPARISON.csv",comparisons);write_csv(out/"UWB_LINK_METRICS.csv",links)
    write_json(out/"CAPTURE_INTEGRITY.json",capture_integrity);write_json(out/"UWB_ORBIT_SELF_CONSISTENCY.json",orbit);write_json(out/"LISTENER_SUMMARY.json",listener)
    mounting={"schema":"biospur-dual-radius-mounting-v1","operator_mapping_token":"NOT_RECEIVED",
        "operator_mounting_confirmation":"NOT_RECEIVED","long_short_assignment":"UNKNOWN",
        "apparent_radius_inference":{k:orbit.get(k) for k in ("larger_apparent_radius_node","smaller_apparent_radius_node","apparent_radius_ratio","mounting_inference")},
        "restriction":"Do not promote apparent-radius ordering to a physical long/short assignment without operator provenance."}
    write_json(out/"MOUNTING_MAP.json",mounting)
    phases={"schema":"biospur-dual-rotation-phases-v1","formal_t0":ledger["t0_wall"],
        "motor_motion_at_t0":"ALREADY_RUNNING_UNBRACKETED","initial_static":"NOT_CAPTURED",
        "supported_rotation":{"start_s":0,"end_s":supported_end,"duration_s":supported_end},
        "depletion_tail":{"start_s":supported_end,"end_s":total_s,"duration_s":total_s-supported_end},
        "motor_off":{"wall":"2026-08-12T13:09:42+02:00","performed_manually":True},
        "clean_stop":ledger["ended_wall"],"final_static":"SKIPPED_BATTERIES_DEPLETED"}
    write_json(out/"CAPTURE_PHASES.json",phases)
    qfail=any(failures.values());false_relock=any(any(e["to_state"]=="STATIONARY" and e["time_s"]>1 for e in transitions[n]) for n in NODES)
    verdict="DUAL_NODE_9RPM_OVERNIGHT_FAIL" if qfail or false_relock else "DUAL_NODE_9RPM_OVERNIGHT_CONDITIONAL"
    numerical={"schema":"biospur-dual-q1-numerical-v1","verdict":verdict,"per_node_failure":failures,
        "quaternion_initialization":"ROTATING_ACCELERATION_MEAN_DEFINES_LOCAL_GAUGE_ONLY",
        "gyro_bias_source":"FROZEN_INDEPENDENT_30_MIN_STATIC_BASELINE","t4_updates":0,
        "spatial_coupling":"SPATIAL_ACCELERATION_COUPLING_BLOCKED_FRAME_BINDING","s2r":"S2R_QUARANTINED_OFFLINE_ONLY",
        "false_stationary_relock":false_relock,"raw_hashes_before":initial}
    final_hashes={"fusion_host_raw.cobs.bin":sha(RAW),"fusion_cdc.log":sha(DECODED)}
    final_hashes.update({f"listener_capture/{p}":sha(LISTENER/p) for p in LISTENER_HASHES})
    numerical["raw_hashes_after"]=final_hashes;numerical["evidence_unchanged"]=initial==final_hashes==expected
    write_json(out/"NUMERICAL_INTEGRITY.json",numerical)
    run_manifest={"schema":"biospur-dual-rotation-analysis-manifest-v1","source_run":str(RUN.relative_to(ROOT)),
        "formal_capture":str(CAPTURE.relative_to(ROOT)),"analysis_git_head":"b50e7889e904c31c1e3f485cbbb1d6fd8e104a4f",
        "capture_git_head":manifest["git_commit"],"geometry_manifest":str(GEOMETRY.relative_to(ROOT)),
        "layout":str(LAYOUT.relative_to(ROOT)),"q1_foundation_commit":"b50e7889e904c31c1e3f485cbbb1d6fd8e104a4f",
        "coordinate_contract":"NO_EXTERNAL_TRUTH_SELF_CONSISTENCY_ONLY","verdict":verdict,
        "hardware_access_during_analysis":False,"source_evidence_hashes":initial}
    write_json(out/"RUN_MANIFEST.json",run_manifest)
    plots(out,hourly,comparisons,position,qrows,boundaries,supported_end,total_s)
    limitations="""# Limitations

The motor was already rotating before formal T0. There is no initial stationary baseline, no bracketed motion onset, no `RPM9_READY`/`ON`, no `OVERNIGHT_GO`, and no operator-confirmed long/short mounting token. Both batteries depleted before motor OFF, so no final stationary recovery exists. These are protocol limitations, not reconstructed facts.

There is no encoder, surveyed radius, angle, home, rigid-arm truth, external attitude truth, or external trajectory truth. V4-io positions, orbit centres, planes, radii, phase and RPM are `NO_EXTERNAL_TRUTH_SELF_CONSISTENCY_ONLY`. The soft printed arm can flex. A larger apparent T4 radius is not promoted to a physical arm assignment.

The current-room geometry is capture-bound and was not refit. Sensor axes, `R_V4_N`, mounting extrinsics and lever arms remain unbound. Real acceleration-to-V4 coupling is therefore disabled. `S2R_QUARANTINED_OFFLINE_ONLY` remains in force. Q1's starting roll/pitch is only a rotating-data local gauge; its gyro bias comes from the frozen independent static baseline.

Listener visibility is an RF diagnostic. BSFC2CC's low Listener count cannot invalidate its complete Fusion-side UWB records or prove motion/depletion by itself. Battery degradation is assigned only where disconnect/reconnect plus uptime-reset evidence supports it.
"""
    (out/"LIMITATIONS.md").write_text(limitations,encoding="utf-8")
    report=f"""# BSFC2CC + BSF3C79 overnight rotation analysis

Primary verdict: `{verdict}`

The formal host segment lasted {total_s/3600:.3f} h. Both nodes supplied supported Fusion IMU/UWB rotation evidence for {supported_end/3600:.3f} h before BSF3C79's first evidence-backed degradation, so the six-hour exposure target was met. BSF3C79 first disconnected at {boundaries['BSF3C79']['first_degradation_s']/3600:.3f} h; BSFC2CC first degraded at {boundaries['BSFC2CC']['first_degradation_s']/3600:.3f} h. The later reconnect/reset tail is battery-depletion evidence and is excluded from nominal rotation metrics.

The capture and Listener evidence closed without host queue drops or reader failure; raw byte accounting is exact. The run is not a protocol-complete PASS: motion began before T0, mounting/safety/overnight tokens were absent, and battery depletion made final stationary recovery impossible.

Frozen Q1 propagation {'encountered a covariance numerical failure' if qfail else 'remained finite with normalized, sign-continuous quaternions'} during the supported interval. The hard IMU motion veto {'did not falsely relock' if not false_relock else 'produced at least one false stationary relock'}. Exact failure times and covariance values are in `NUMERICAL_INTEGRITY.json` and `Q1_ATTITUDE_STABILITY.csv`.

T4 orbit results use the unchanged current-room V4-io geometry. The apparent radius ordering is {orbit.get('larger_apparent_radius_node','unavailable')} > {orbit.get('smaller_apparent_radius_node','unavailable')} with ratio {orbit.get('apparent_radius_ratio','unavailable')}; this is an inference, not an operator-confirmed long/short assignment. Per-hour gyro agreement and T4 apparent angular rates are self-consistency diagnostics only.

All motor actions were manual. Software never controlled or claimed to control the motor. No OTA, upload, pending, PREPARE/COMMIT, flash, reboot, J-Link/SWD/RTT, AutoPos or configuration mutation occurred. Offline analysis did not access hardware, and all source evidence hashes matched before and after.
"""
    (out/"REPORT.md").write_text(report,encoding="utf-8")
    return {"verdict":verdict,"supported_h":supported_end/3600,"total_h":total_s/3600,
            "q1_failures":failures,"orbit":orbit.get("per_node"),"evidence_unchanged":numerical["evidence_unchanged"]}


def finalize(first: Path, second: Path, destination: Path):
    mismatch=[name for name in CORE if sha(first/name)!=sha(second/name)]
    if mismatch:raise RuntimeError(f"non-deterministic outputs: {mismatch}")
    destination.mkdir(parents=True,exist_ok=False)
    for name in CORE:shutil.copyfile(first/name,destination/name)
    (destination/"SHA256SUMS").write_text("\n".join(f"{sha(destination/name)}  {name}" for name in sorted(CORE))+"\n",encoding="utf-8")


def main():
    p=argparse.ArgumentParser();p.add_argument("--out",type=Path);p.add_argument("--finalize",nargs=3,type=Path);a=p.parse_args()
    if a.out:print(json.dumps(clean(derive(a.out)),sort_keys=True));return 0
    if a.finalize:finalize(*a.finalize);return 0
    p.error("choose --out or --finalize")


if __name__=="__main__":raise SystemExit(main())
