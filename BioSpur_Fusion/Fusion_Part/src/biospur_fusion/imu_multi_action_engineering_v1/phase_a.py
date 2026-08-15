"""Phase-A physical qualification before any V1 nonlinear solver exists."""
from __future__ import annotations

import json
import math
from pathlib import Path
import time
from typing import Any,Mapping

import numpy as np
from scipy.optimize import minimize_scalar
from scipy.spatial.transform import Rotation

from biospur_fusion.imu.q1 import quaternion_to_matrix
from biospur_fusion.imu_preview_v0.core import EXPECTED_INITIAL,EXPECTED_TPOSE
from biospur_fusion.imu_preview_v0.io import canonical_json_bytes,load_calibration_ledger,savez_deterministic,sha256
from .q2 import QuasiStaticPrepared,prepare_quasi_static,run_q2_from_prepared

EXPECTED_MAPPING={"BSF31CC":"torso","BSFC2CC":"pelvis","BSFAA61":"upper_arm_L","BSF1120":"upper_arm_R","BSFB165":"forearm_L","BSFEC35":"forearm_R","BSF44AD":"thigh_L","BSF3C79":"thigh_R","BSF6C53":"shank_L","BSF8BC4":"shank_R"}
ARM_SEGMENTS={"upper_arm_L","upper_arm_R","forearm_L","forearm_R"}


def _dump(path:Path,value:Any)->None:path.write_bytes(canonical_json_bytes(value))


def phase_a_gate_binding(gates:Mapping[str,Any])->dict:
    keys=("product","allowed_npz_keys","calibration_actions","node_to_segment","q2","static_subset","static_compatibility","data_firewall")
    return {key:gates[key] for key in keys}


def phase_a_gate_sha256(gates:Mapping[str,Any])->str:
    return __import__("hashlib").sha256(canonical_json_bytes(phase_a_gate_binding(gates))).hexdigest()


def _angle(a:np.ndarray,b:np.ndarray)->float:
    a=np.asarray(a,float);b=np.asarray(b,float);a/=np.linalg.norm(a);b/=np.linalg.norm(b);return math.degrees(math.acos(float(np.clip(a@b,-1,1))))


def _yaw(angle:float)->np.ndarray:
    c=math.cos(angle);s=math.sin(angle);return np.array([[c,-s,0.],[s,c,0.],[0.,0.,1.]])


def select_static_subset(prepared:Mapping[str,QuasiStaticPrepared],window:tuple[int,int],cfg:Mapping[str,float])->tuple[tuple[int,int],dict]:
    start,stop=map(int,window);edge=int(float(cfg["edge_exclusion_fraction"])*(stop-start));lo=start+edge;hi=stop-edge;duration=int(round(float(cfg["duration_s"])*1e9));step=50_000_000;candidates=[]
    for a in range(lo,hi-duration+1,step):
        b=a+duration;rows=[];node_means=[]
        for node,item in sorted(prepared.items()):
            mask=(item.time_ns>=a)&(item.time_ns<=b);weights=item.quasi_static_weight[mask];mean=float(np.mean(weights)) if len(weights) else 0.;node_means.append(mean);rows.append({"node":node,"sample_count":int(mask.sum()),"quasi_static_weight_mean":mean,"quasi_static_weight_p05":float(np.percentile(weights,5)) if len(weights) else 0.,"quasi_static_weight_sum":float(np.sum(weights))})
        score=float(np.mean(node_means));candidates.append((-score,a,b,rows))
    if not candidates:raise RuntimeError("no quasi-static interior subset candidates")
    negative,a,b,rows=min(candidates,key=lambda item:(item[0],item[1]));return (a,b),{"start_global_time_ns":a,"stop_global_time_ns":b,"duration_s":(b-a)/1e9,"selection":"MAXIMUM_AGGREGATE_CONTINUOUS_QUASI_STATIC_CONFIDENCE_WITH_INTERIOR_EDGE_EXCLUSION","aggregate_quasi_static_confidence":-negative,"nodes":rows,"pass":True,"pass_semantics":"INTERVAL_SELECTED; QUALITY_GATED_BY_BIAS_GRAVITY_UNCERTAINTY_AND_POSE_DISPERSION"}


def _mean_rotation(result,window:tuple[int,int])->np.ndarray:
    mask=(result.time_ns>=window[0])&(result.time_ns<=window[1]);mats=np.asarray([quaternion_to_matrix(q) for q in result.q_wxyz[mask]])
    if len(mats)<10:raise RuntimeError("insufficient Q2 static mean rows")
    return Rotation.from_matrix(mats).mean().as_matrix()


def _best_two_pose_axis(initial_R:np.ndarray,tpose_R:np.ndarray,initial_expected:np.ndarray,tpose_expected:np.ndarray)->dict:
    def evaluate(h):
        y=_yaw(-float(h));a=initial_R.T@(y@initial_expected);b=tpose_R.T@(y@tpose_expected);axis=a+b
        if np.linalg.norm(axis)<1e-12:
            reference=np.array([1.,0.,0.]) if abs(a[0])<.9 else np.array([0.,1.,0.]);axis=np.cross(a,reference)
        axis=axis/np.linalg.norm(axis);ri=_angle(_yaw(float(h))@initial_R@axis,initial_expected);rt=_angle(_yaw(float(h))@tpose_R@axis,tpose_expected);return max(ri,rt),axis,ri,rt
    grid=np.linspace(-math.pi,math.pi,181);best=min((evaluate(h)[0],h) for h in grid);opt=minimize_scalar(lambda h:evaluate(h)[0],bounds=(best[1]-math.radians(4),best[1]+math.radians(4)),method="bounded",options={"xatol":1e-12});cost,axis,ri,rt=evaluate(float(opt.x));return {"best_relative_heading_rad":float(opt.x),"best_board_longitudinal_axis":axis.tolist(),"initial_direction_mismatch_deg":ri,"t_pose_direction_mismatch_deg":rt,"maximum_direction_mismatch_deg":cost}


def _integrate_gyro(result,initial_center:int,tpose_center:int)->np.ndarray:
    mask=(result.time_ns>=initial_center)&(result.time_ns<=tpose_center);times=result.time_ns[mask];gyro=result.gyro_corrected_rad_s[mask];rotation=Rotation.identity()
    for i in range(1,len(times)):
        dt=(int(times[i])-int(times[i-1]))/1e9
        if 0<dt<=.05:rotation=rotation*Rotation.from_rotvec(gyro[i-1]*dt)
    return rotation.as_matrix()


def _pose_dispersion_p95_deg(result,window:tuple[int,int])->float:
    mask=(result.time_ns>=window[0])&(result.time_ns<=window[1])
    matrices=np.asarray([quaternion_to_matrix(q) for q in result.q_wxyz[mask]])
    if len(matrices)<10:return math.inf
    mean=Rotation.from_matrix(matrices).mean().as_matrix()
    angles=np.degrees(Rotation.from_matrix(np.einsum("ji,njk->nik",mean,matrices)).magnitude())
    return float(np.percentile(angles,95))


def add_pose_dispersion_gate(subset:dict,q2:Mapping[str,Any],initial:tuple[int,int],tpose:tuple[int,int],limit_deg:float)->dict:
    passed=True
    for name,window in (("initial_still_attempt2",initial),("t_pose",tpose)):
        by_node={node:_pose_dispersion_p95_deg(result,window) for node,result in sorted(q2.items())}
        node_pass={node:value<=float(limit_deg) for node,value in by_node.items()}
        subset[name]["pose_dispersion_p95_deg_by_node"]=by_node
        subset[name]["maximum_pose_dispersion_p95_deg"]=max(by_node.values())
        subset[name]["pose_dispersion_gate_pass_by_node"]=node_pass
        subset[name]["pose_dispersion_gate_pass"]=all(node_pass.values())
        passed=passed and subset[name]["pose_dispersion_gate_pass"]
    subset["pose_dispersion_limit_deg"]=float(limit_deg)
    subset["pass"]=bool(subset["pass"] and passed)
    return subset


def compatibility(q2:Mapping[str,Any],mapping:Mapping[str,str],initial:tuple[int,int],tpose:tuple[int,int],cfg:Mapping[str,float])->dict:
    rows=[];all_pass=True;all_rotation=True;all_direction=True;all_q2_raw=True
    for node,segment in sorted(mapping.items()):
        ri=_mean_rotation(q2[node],initial);rt=_mean_rotation(q2[node],tpose);relative=ri.T@rt;relative_deg=float(np.degrees(Rotation.from_matrix(relative).magnitude()));fit=_best_two_pose_axis(ri,rt,EXPECTED_INITIAL[segment],EXPECTED_TPOSE[segment]);gyro_delta=_integrate_gyro(q2[node],sum(initial)//2,sum(tpose)//2);gyro_mismatch=float(np.degrees(Rotation.from_matrix(relative.T@gyro_delta).magnitude()));arm=segment in ARM_SEGMENTS
        rotation_pass=(float(cfg["minimum_arm_board_relative_rotation_deg"])<=relative_deg<=float(cfg["maximum_arm_board_relative_rotation_deg"])) if arm else relative_deg<=float(cfg["maximum_non_arm_board_relative_rotation_deg"]);direction_limit=float(cfg["maximum_arm_direction_mismatch_deg"] if arm else cfg["maximum_non_arm_direction_mismatch_deg"]);direction_pass=fit["maximum_direction_mismatch_deg"]<=direction_limit;gyro_pass=gyro_mismatch<=float(cfg["maximum_q2_vs_raw_gyro_relative_rotation_deg"]);passed=rotation_pass and direction_pass and gyro_pass;all_pass&=passed;all_rotation&=rotation_pass;all_direction&=direction_pass;all_q2_raw&=gyro_pass
        mismatch=Rotation.from_matrix(relative.T@gyro_delta)
        rows.append({"node":node,"segment":segment,"arm_segment":arm,"observed_board_relative_rotation_deg":relative_deg,"observed_board_relative_rotation_rotvec_rad":Rotation.from_matrix(relative).as_rotvec().tolist(),**fit,"raw_gyro_integrated_rotation_deg":float(np.degrees(Rotation.from_matrix(gyro_delta).magnitude())),"raw_gyro_integrated_rotation_rotvec_rad":Rotation.from_matrix(gyro_delta).as_rotvec().tolist(),"q2_vs_raw_gyro_relative_rotation_mismatch_deg":gyro_mismatch,"q2_vs_raw_gyro_mismatch_rotvec_rad":mismatch.as_rotvec().tolist(),"rotation_gate_pass":rotation_pass,"direction_gate_pass":direction_pass,"q2_raw_gyro_gate_pass":gyro_pass,"pass":passed})
    if all_pass:verdict="PASS_STATIC_POSE_WINDOWS_COMPATIBLE"
    elif all_rotation and all_direction and not all_q2_raw:verdict="BLOCKED_Q2_RELATIVE_ROTATION_INCONSISTENT"
    else:verdict="BLOCKED_STATIC_POSE_WINDOWS_INCOMPATIBLE"
    return {"schema":"biospur-static-pose-compatibility-v1","rows":rows,"pass":all_pass,"component_gates":{"static_board_relative_rotation":all_rotation,"fixed_extrinsic_direction_compatibility":all_direction,"q2_vs_raw_gyro_relative_rotation":all_q2_raw},"verdict":verdict}


def run_phase_a(ledger_path:Path,gates_path:Path,output:Path)->dict:
    started=time.perf_counter();output=Path(output)
    if output.exists():raise FileExistsError(output)
    output.mkdir(parents=True);gates=json.loads(Path(gates_path).read_text());mapping=gates["node_to_segment"]
    mapping_pass=mapping==EXPECTED_MAPPING and len(set(mapping.values()))==10;mapping_audit={"schema":"biospur-node-frame-mapping-audit-v1","mapping":mapping,"expected_operator_constrained_mapping":EXPECTED_MAPPING,"unique_segments":len(set(mapping.values())),"mapping_pass":mapping_pass,"frame_contract":{"quaternion":"SCALAR_FIRST_HAMILTON_q_NB","rotation":"ACTIVE_BOARD_TO_NODE_LOCAL_GRAVITY_FRAME","gyro_increment":"BOARD_FRAME_RIGHT_MULTIPLICATIVE","absolute_yaw":"UNOBSERVED_COMMON_DISPLAY_GAUGE","segment_axial_twist":"QUOTIENT_NOT_PUBLISHED"},"verdict":"PASS_NODE_FRAME_MAPPING" if mapping_pass else "BLOCKED_NODE_FRAME_MAPPING"};_dump(output/"NODE_FRAME_MAPPING_AUDIT.json",mapping_audit)
    imus,windows,access=load_calibration_ledger(ledger_path,gates);load_done=time.perf_counter();prepared=prepare_quasi_static(imus,gates["q2"],windows["initial_still_attempt2"]);prepare_done=time.perf_counter();initial,initial_audit=select_static_subset(prepared,windows["initial_still_attempt2"],gates["static_subset"]);tpose,tpose_audit=select_static_subset(prepared,windows["t_pose"],gates["static_subset"]);subset={"schema":"biospur-human-quasi-static-subset-selection-v1","initial_still_attempt2":initial_audit,"t_pose":tpose_audit,"pass":True}
    q2,q2audit,estimates=run_q2_from_prepared(prepared,windows,gates["q2"]);q2_done=time.perf_counter();subset=add_pose_dispersion_gate(subset,q2,initial,tpose,float(gates["static_subset"]["maximum_pose_dispersion_p95_deg"]));_dump(output/"STATIC_SUBSET_SELECTION.json",subset);_dump(output/"Q2_RELATIVE_ROTATION_AUDIT.json",q2audit);_dump(output/"SENSOR_DATA_VALIDITY.json",{"schema":"biospur-sensor-data-validity-v1","status":q2audit["data_stream_validity"],"operator_fault":False,"nodes":{node:{key:value for key,value in row.items() if key in ("input_total","input_status_rejected","accepted_unique_samples","duplicate_timestamps_rejected","source_accounting_closed","effective_rate_hz","gap_boundaries","finite")} for node,row in q2audit["nodes"].items()}});_dump(output/"BIAS_AND_GRAVITY_ESTIMATION_WITH_UNCERTAINTY.json",{"schema":"biospur-bias-gravity-uncertainty-v1","estimates":estimates,"gate_verdict":q2audit["verdict"]});_dump(output/"NEUTRAL_POSE_REFERENCE_QUALITY.json",{"schema":"biospur-neutral-and-tpose-reference-quality-v1","references_are_independent":True,"initial_still_attempt2":subset["initial_still_attempt2"],"t_pose":subset["t_pose"],"pass":subset["pass"]});static=compatibility(q2,mapping,initial,tpose,gates["static_compatibility"]);_dump(output/"STATIC_POSE_COMPATIBILITY.json",static)
    passed=mapping_pass and subset["pass"] and q2audit["verdict"]=="PASS_Q2_HUMAN_QUASI_STATIC_V1" and static["pass"]
    blocker=None
    if not mapping_pass:blocker="BLOCKED_NODE_FRAME_MAPPING"
    elif not subset["pass"]:blocker="BLOCKED_NEUTRAL_OR_TPOSE_REFERENCE_UNCERTAINTY_TOO_LARGE"
    elif q2audit["verdict"]!="PASS_Q2_HUMAN_QUASI_STATIC_V1":blocker=q2audit["verdict"]
    elif not static["pass"]:blocker=static["verdict"]
    cache={}
    for node,value in sorted(q2.items()):
        prefix=f"{node}__";cache.update({prefix+"time_ns":value.time_ns,prefix+"boot_epoch":value.boot_epoch,prefix+"q_wxyz":value.q_wxyz,prefix+"covariance_rad2":value.covariance_rad2,prefix+"accel_mps2":value.accel_mps2,prefix+"gyro_corrected_rad_s":value.gyro_corrected_rad_s,prefix+"stationary":value.stationary.astype(np.uint8),prefix+"gravity_accepted":value.gravity_accepted.astype(np.uint8),prefix+"gap_boundary":value.gap_boundary.astype(np.uint8),prefix+"bias_rad_s":value.bias_rad_s})
    savez_deterministic(output/"Q2_HUMAN_QUASI_STATIC_CACHE.npz",cache)
    binding=phase_a_gate_binding(gates);_dump(output/"PHASE_A_GATE_BINDING.json",binding);result={"schema":"biospur-imu-multi-action-engineering-preview-v1-phase-a-result","product":gates["product"],"phase":"A","pass":passed,"verdict":"PASS_PHASE_A_Q2_FRAME_STATIC_COMPATIBILITY" if passed else blocker,"nonlinear_solver_started":False,"calibration_ledger_sha256":sha256(ledger_path),"gates_sha256":sha256(gates_path),"phase_a_gate_binding_sha256":phase_a_gate_sha256(gates),"q2_cache_sha256":sha256(output/"Q2_HUMAN_QUASI_STATIC_CACHE.npz"),"data_access":access,"selected_windows":{"initial_still_attempt2":list(initial),"t_pose":list(tpose)},"calibration_windows":{name:list(value) for name,value in windows.items()},"timing_s":{"ledger_load":load_done-started,"quasi_static_prepare":prepare_done-load_done,"q2":q2_done-prepare_done,"compatibility_cache_and_write":time.perf_counter()-q2_done,"total":time.perf_counter()-started},"golf":"SEALED","boxing":"SEALED","walk":"SEALED","final_still":"SEALED","uwb_t4_anchor":False,"operator_measurements":False};_dump(output/"RESULT.json",result);_dump(output/"DATA_ACCESS_AUDIT.json",access);manifest={p.name:sha256(p) for p in sorted(output.iterdir()) if p.is_file() and p.name!="SHA256_MANIFEST.json"};_dump(output/"SHA256_MANIFEST.json",manifest);return result
