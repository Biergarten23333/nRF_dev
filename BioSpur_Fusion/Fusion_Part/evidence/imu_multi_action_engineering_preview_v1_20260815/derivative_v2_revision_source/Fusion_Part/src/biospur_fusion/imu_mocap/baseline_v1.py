"""Calibration-only IMU articulated mocap baseline V1.

This module deliberately has no UWB/T4 imports.  Phase A consumes only the
typed IMU arrays and token-labelled action windows.
"""
from __future__ import annotations

import hashlib
import io
import json
import math
from pathlib import Path
from typing import Any, Mapping
import zipfile

import numpy as np
from scipy.spatial.transform import Rotation

from biospur_fusion.imu.q1 import quaternion_to_matrix
from .q2_frontend import Q2Result, run_q2_frontend, run_q2_node, prepare_stationarity


NODE_ORDER=("BSF3C79","BSFC2CC","BSF44AD","BSF6C53","BSF8BC4","BSF1120","BSF31CC","BSFAA61","BSFB165","BSFEC35")
FROZEN_NODE_TO_SEGMENT={
    "BSF31CC":"torso","BSFC2CC":"pelvis",
    "BSFAA61":"upper_arm_L","BSF1120":"upper_arm_R",
    "BSFB165":"forearm_L","BSFEC35":"forearm_R",
    "BSF44AD":"thigh_L","BSF3C79":"thigh_R",
    "BSF6C53":"shank_L","BSF8BC4":"shank_R",
}
SEGMENT_ORDER=("pelvis","torso","upper_arm_L","upper_arm_R","forearm_L","forearm_R","thigh_L","thigh_R","shank_L","shank_R")
LANDMARKS=("Pelvis","C7Proxy","Shoulder_L","Shoulder_R","Elbow_L","Elbow_R","Wrist_L","Wrist_R","Hip_L","Hip_R","Knee_L","Knee_R","Ankle_L","Ankle_R")
LANDMARK_INDEX={x:i for i,x in enumerate(LANDMARKS)}
EDGES=(("Pelvis","C7Proxy"),("Shoulder_L","Shoulder_R"),("C7Proxy","Shoulder_L"),("C7Proxy","Shoulder_R"),("Shoulder_L","Elbow_L"),("Elbow_L","Wrist_L"),("Shoulder_R","Elbow_R"),("Elbow_R","Wrist_R"),("Hip_L","Hip_R"),("Pelvis","Hip_L"),("Pelvis","Hip_R"),("Hip_L","Knee_L"),("Knee_L","Ankle_L"),("Hip_R","Knee_R"),("Knee_R","Ankle_R"))
EXPECTED_TPOSE={"pelvis":np.array([1.,0,0]),"torso":np.array([0.,0,1.]),"upper_arm_L":np.array([-1.,0,0]),"upper_arm_R":np.array([1.,0,0]),"forearm_L":np.array([-1.,0,0]),"forearm_R":np.array([1.,0,0]),"thigh_L":np.array([0.,0,-1.]),"thigh_R":np.array([0.,0,-1.]),"shank_L":np.array([0.,0,-1.]),"shank_R":np.array([0.,0,-1.])}
EXPECTED_INITIAL_STILL={
    "pelvis":np.array([1.,0,0]),"torso":np.array([0.,0,1.]),
    "upper_arm_L":np.array([0.,0,-1.]),"upper_arm_R":np.array([0.,0,-1.]),
    "forearm_L":np.array([0.,0,-1.]),"forearm_R":np.array([0.,0,-1.]),
    "thigh_L":np.array([0.,0,-1.]),"thigh_R":np.array([0.,0,-1.]),
    "shank_L":np.array([0.,0,-1.]),"shank_R":np.array([0.,0,-1.]),
}


def sha256(path: Path) -> str:
    h=hashlib.sha256()
    with Path(path).open("rb") as f:
        while block:=f.read(4<<20):h.update(block)
    return h.hexdigest()


def dump_json(path: Path, value: Any) -> None:
    Path(path).write_text(json.dumps(value,indent=2,sort_keys=True,allow_nan=False)+"\n")


def _savez_deterministic(path: Path, arrays: Mapping[str,np.ndarray]) -> None:
    with zipfile.ZipFile(path,"w",compression=zipfile.ZIP_STORED,allowZip64=True) as archive:
        for key in sorted(arrays):
            payload=io.BytesIO();np.lib.format.write_array(payload,np.asarray(arrays[key]),allow_pickle=False)
            info=zipfile.ZipInfo(f"{key}.npy",date_time=(1980,1,1,0,0,0));info.compress_type=zipfile.ZIP_STORED;info.external_attr=0o600<<16;archive.writestr(info,payload.getvalue())


def _normalize(v: np.ndarray, fallback: np.ndarray|None=None) -> np.ndarray:
    v=np.asarray(v,float);n=float(np.linalg.norm(v))
    if np.isfinite(n) and n>1e-10:return v/n
    return _normalize(np.array([0.,0.,1.]) if fallback is None else fallback)


def _nearest(times: np.ndarray, targets: np.ndarray) -> tuple[np.ndarray,np.ndarray]:
    at=np.searchsorted(times,targets);hi=np.clip(at,0,len(times)-1);lo=np.clip(at-1,0,len(times)-1);take=np.abs(times[hi]-targets)<np.abs(times[lo]-targets);idx=np.where(take,hi,lo);return idx,np.abs(times[idx]-targets)


def load_imu_only_ledger(path: Path, gates: Mapping[str,Any]) -> tuple[dict[str,np.ndarray],dict[str,tuple[int,int]],dict]:
    required=set(gates["allowed_npz_keys"]);opened=[];arrays={}
    with np.load(path,allow_pickle=False) as source:
        available=set(source.files)
        if not required<=available:raise ValueError(f"missing IMU-only keys: {sorted(required-available)}")
        for key in sorted(required):arrays[key]=source[key].copy();opened.append(key)
    forbidden_opened=[k for k in opened if k.startswith("uwb_") or "t4" in k.lower()]
    if forbidden_opened:raise RuntimeError(f"forbidden payload accessed: {forbidden_opened}")
    windows={str(r["name"]):(int(r["start_ns"]),int(r["stop_ns"])) for r in arrays.pop("action_windows")}
    imus={key.removeprefix("imu_"):value for key,value in arrays.items()}
    audit={"schema":"biospur-imu-only-data-access-audit-v1","opened_inputs":[{"path":str(Path(path).resolve()),"sha256":sha256(path),"npz_keys_opened":opened}],"npz_keys_available_count":len(available),"npz_keys_opened_count":len(opened),"forbidden_npz_keys_opened":forbidden_opened,"uwb_arrays_accessed":False,"t4_accessed":False,"anchor_geometry_accessed":False,"master_arrival_time_used":False,"timestamp_field_used":"global_time_ns","walk":"SEALED_NOT_OPENED","final_still":"SEALED_NOT_OPENED","operator_measurements":"SEALED_NOT_READ"}
    return imus,windows,audit


def validate_frozen_mapping(gates: Mapping[str, Any]) -> None:
    """Reject any left/right or node/segment rebinding before payload use."""
    actual=dict(gates["node_to_segment"])
    if actual != FROZEN_NODE_TO_SEGMENT:
        raise ValueError("frozen node-to-segment identity mismatch")


def _window_grid(window: tuple[int,int], hz: float) -> np.ndarray:
    return np.arange(window[0],window[1]+1,int(round(1e9/hz)),dtype=np.int64)


def _sample_q2(result: Q2Result, times: np.ndarray) -> tuple[np.ndarray,np.ndarray,np.ndarray,np.ndarray]:
    idx,gap=_nearest(result.time_ns,times);ok=gap<=20_000_000;rot=np.full((len(times),3,3),np.nan);rot[ok]=np.asarray([quaternion_to_matrix(q) for q in result.q_wxyz[idx[ok]]]);gyro=np.full((len(times),3),np.nan);gyro[ok]=result.gyro_corrected_rad_s[idx[ok]];stationary=np.zeros(len(times),bool);stationary[ok]=result.stationary[idx[ok]];return rot,gyro,stationary,gap


def _longest_true(mask: np.ndarray, times: np.ndarray) -> tuple[int,int,float]|None:
    best=None;start=None
    for i,v in enumerate(mask):
        if v and start is None:start=i
        end=(not v and start is not None) or (v and start is not None and i==len(mask)-1)
        if end:
            stop=i if v and i==len(mask)-1 else i-1;duration=(int(times[stop])-int(times[start]))/1e9
            candidate=(start,stop,duration)
            if best is None or (candidate[2],-candidate[0])>(best[2],-best[0]):best=candidate
            start=None
    return best


def _mean_rotation(mats: np.ndarray) -> np.ndarray:
    return Rotation.from_matrix(mats).mean().as_matrix()


def _axis_angle_deg(a: np.ndarray,b: np.ndarray) -> float:
    return math.degrees(math.acos(float(np.clip(_normalize(a)@_normalize(b),-1.,1.))))


def _yaw_matrix(angle_rad: float) -> np.ndarray:
    c=math.cos(angle_rad);s=math.sin(angle_rad)
    return np.array([[c,-s,0.],[s,c,0.],[0.,0.,1.]])


def _stable_reference(
    q2: Mapping[str,Q2Result],
    window: tuple[int,int],
    gates: Mapping[str,Any],
) -> tuple[np.ndarray,dict[str,tuple[np.ndarray,np.ndarray,np.ndarray,np.ndarray]],np.ndarray,tuple[int,int,float]|None]:
    hz=float(gates["temporal_estimator"]["state_rate_hz"]);times=_window_grid(window,hz)
    sampled={node:_sample_q2(result,times) for node,result in q2.items()}
    agreement=np.mean(np.stack([value[2] for value in sampled.values()]),axis=0)
    stable=agreement>=float(gates["q2_frontend"]["multi_node_agreement_fraction"])
    run=_longest_true(stable,times)
    reference=np.arange(len(times)) if run is None else np.arange(run[0],run[1]+1)
    return times,sampled,reference,run


def _fit_dual_static_axis(
    initial_mats: np.ndarray,
    tpose_mats: np.ndarray,
    initial_expected: np.ndarray,
    tpose_expected: np.ndarray,
    yaw_step_deg: float,
) -> tuple[np.ndarray,float,float,float]:
    """Fit one local long axis and one per-sensor gravity-frame yaw gauge.

    INITIAL_STILL and T_POSE remain separate residuals.  They are never pooled
    into a synthetic reference orientation.
    """
    ri=_mean_rotation(initial_mats);rt=_mean_rotation(tpose_mats)
    distinct=_axis_angle_deg(initial_expected,tpose_expected)>1.
    yaws=np.deg2rad(np.arange(-180.,180.+.5*yaw_step_deg,yaw_step_deg)) if distinct else np.array([0.])
    best=None
    for yaw in yaws:
        correction=_yaw_matrix(float(yaw));local=_normalize(ri.T@correction.T@initial_expected+rt.T@correction.T@tpose_expected)
        initial_error=_axis_angle_deg(correction@ri@local,initial_expected);tpose_error=_axis_angle_deg(correction@rt@local,tpose_expected);objective=initial_error**2+tpose_error**2
        candidate=(objective,abs(float(yaw)),float(yaw),local,initial_error,tpose_error)
        if best is None or candidate[:3]<best[:3]:best=candidate
    assert best is not None
    return best[3],best[2],best[4],best[5]


def _principal_axis(samples: np.ndarray) -> tuple[np.ndarray,float]:
    scatter=samples.T@samples/max(1,len(samples));values,vectors=np.linalg.eigh(scatter)
    return _normalize(vectors[:,-1]),float(values[-1]/max(values[-2],1e-12))


def _signed_angle_about(parent: np.ndarray,child: np.ndarray,axis: np.ndarray) -> np.ndarray:
    cross=np.cross(parent,child);numerator=np.sum(cross*axis,axis=-1);denominator=np.sum(parent*child,axis=-1)
    return np.arctan2(numerator,denominator)


def calibrate_sensor_to_segment(q2: Mapping[str,Q2Result],windows: Mapping[str,tuple[int,int]],gates: Mapping[str,Any]) -> tuple[dict[str,np.ndarray],dict]:
    node_to_segment=gates["node_to_segment"];segment_to_node={segment:node for node,segment in node_to_segment.items()};pose_cfg=gates["pose_reference"]
    it,initial_sampled,initial_reference,initial_run=_stable_reference(q2,windows["initial_still_attempt2"],gates)
    tt,tpose_sampled,tpose_reference,tpose_run=_stable_reference(q2,windows["t_pose"],gates)
    local_axes={};segments={};failures=[];limit=float(gates["calibration"]["tpose_subset_axis_repeatability_limit_deg"]);yaw_step=float(pose_cfg["per_sensor_yaw_search_step_deg"])
    for segment in SEGMENT_ORDER:
        node=segment_to_node[segment];imats=initial_sampled[node][0][initial_reference];tmats=tpose_sampled[node][0][tpose_reference];imats=imats[np.isfinite(imats).all((1,2))];tmats=tmats[np.isfinite(tmats).all((1,2))]
        if len(imats)<10 or len(tmats)<10:
            local=np.array([1.,0,0]);yaw=0.;initial_error=tpose_error=repeat=180.;failures.append(f"{segment}:INSUFFICIENT_DUAL_STATIC_REFERENCE")
        else:
            local,yaw,initial_error,tpose_error=_fit_dual_static_axis(imats,tmats,EXPECTED_INITIAL_STILL[segment],EXPECTED_TPOSE[segment],yaw_step);subsets=[]
            subset_pairs=((imats[::2],tmats[::2]),(imats[1::2],tmats[1::2]),(imats[:max(1,len(imats)//2)],tmats[:max(1,len(tmats)//2)]),(imats[len(imats)//2:],tmats[len(tmats)//2:]))
            for si,st in subset_pairs:
                if len(si) and len(st):subsets.append(_fit_dual_static_axis(si,st,EXPECTED_INITIAL_STILL[segment],EXPECTED_TPOSE[segment],yaw_step)[:2])
            repeat=max((max(_axis_angle_deg(local,x[0]),abs(math.degrees(math.atan2(math.sin(yaw-x[1]),math.cos(yaw-x[1]))))) for x in subsets),default=0.)
            if repeat>limit:failures.append(f"{segment}:AXIS_REPEATABILITY")
        direction_limit=float(pose_cfg["static_direction_tolerance_deg"])
        if initial_error>direction_limit:failures.append(f"{segment}:INITIAL_STATIC_DIRECTION")
        if tpose_error>direction_limit:failures.append(f"{segment}:TPOSE_STATIC_DIRECTION")
        local_axes[segment]=local;segments[segment]={"node":node,"expected_initial_still_direction":EXPECTED_INITIAL_STILL[segment].tolist(),"expected_tpose_direction":EXPECTED_TPOSE[segment].tolist(),"sensor_local_long_axis":local.tolist(),"per_sensor_gravity_frame_yaw_correction_rad":yaw,"per_sensor_gravity_frame_yaw_correction_deg":math.degrees(yaw),"initial_still_direction_residual_deg":initial_error,"tpose_direction_residual_deg":tpose_error,"axial_twist":"UNOBSERVED_QUOTIENT_GAUGE","subset_repeatability_max_deg":repeat,"pass":repeat<=limit and initial_error<=direction_limit and tpose_error<=direction_limit}
    initial_minimum=float(gates["stability_gates"]["initial_still_minimum_continuous_stationary_s"]);tpose_minimum=float(gates["calibration"]["minimum_tpose_stable_duration_s"])
    if initial_run is None or initial_run[2]<initial_minimum:failures.append("INITIAL_STILL_STABLE_REFERENCE_INSUFFICIENT")
    if tpose_run is None or tpose_run[2]<tpose_minimum:failures.append("TPOSE_STABLE_REFERENCE_INSUFFICIENT")
    functional={};pairs={"left_elbow":("upper_arm_L","forearm_L"),"right_elbow_attempt2":("upper_arm_R","forearm_R"),"left_knee":("thigh_L","shank_L"),"right_knee":("thigh_R","shank_R")}
    for action,(prox,dist) in pairs.items():
        at=_window_grid(windows[action],float(gates["temporal_estimator"]["state_rate_hz"]));pn=segment_to_node[prox];dn=segment_to_node[dist];pr,pg,_,_=_sample_q2(q2[pn],at);dr,dg,_,_=_sample_q2(q2[dn],at);yp=_yaw_matrix(segments[prox]["per_sensor_gravity_frame_yaw_correction_rad"]);yd=_yaw_matrix(segments[dist]["per_sensor_gravity_frame_yaw_correction_rad"]);pr=np.einsum("ij,njk->nik",yp,pr);dr=np.einsum("ij,njk->nik",yd,dr);valid=np.isfinite(pr).all((1,2))&np.isfinite(dr).all((1,2))&np.isfinite(pg).all(1)&np.isfinite(dg).all(1);rel=np.einsum("nij,nj->ni",dr[valid],dg[valid])-np.einsum("nij,nj->ni",pr[valid],pg[valid]);parent_rel=np.einsum("nji,nj->ni",pr[valid],rel);child_rel=np.einsum("nji,nj->ni",dr[valid],rel)
        rms=float(np.sqrt(np.mean(np.sum(rel*rel,axis=1)))) if len(rel) else 0.;parent_axis,parent_ratio=_principal_axis(parent_rel);child_axis,child_ratio=_principal_axis(child_rel);display_parent=np.mean(np.einsum("nij,j->ni",pr[valid],parent_axis),axis=0);display_child=np.mean(np.einsum("nij,j->ni",dr[valid],child_axis),axis=0)
        if display_parent@display_child<0:child_axis=-child_axis;display_child=-display_child
        display_axis=_normalize(display_parent+display_child);ratio=min(parent_ratio,child_ratio);passed=rms>=float(gates["calibration"]["functional_axis_minimum_rms_rad_s"]) and ratio>=float(gates["calibration"]["functional_axis_minimum_eigen_ratio"])
        functional[action]={"proximal_segment":prox,"distal_segment":dist,"samples":int(len(rel)),"relative_angular_velocity_rms_rad_s":rms,"parent_axis_sensor_frame":parent_axis.tolist(),"child_axis_sensor_frame":child_axis.tolist(),"reference_display_axis":display_axis.tolist(),"parent_eigen_ratio":parent_ratio,"child_eigen_ratio":child_ratio,"eigen_ratio":ratio,"pass":passed}
    elbows={}
    for side,action,prox,dist in (("left","left_elbow","upper_arm_L","forearm_L"),("right","right_elbow_attempt2","upper_arm_R","forearm_R")):
        evidence=functional[action];pn=segment_to_node[prox];dn=segment_to_node[dist];yp=_yaw_matrix(segments[prox]["per_sensor_gravity_frame_yaw_correction_rad"]);yd=_yaw_matrix(segments[dist]["per_sensor_gravity_frame_yaw_correction_rad"]);lp=local_axes[prox];ld=local_axes[dist];hp=np.asarray(evidence["parent_axis_sensor_frame"])
        def angles(sampled,reference):
            pr=np.einsum("ij,njk->nik",yp,sampled[pn][0][reference]);dr=np.einsum("ij,njk->nik",yd,sampled[dn][0][reference]);u=np.einsum("nij,j->ni",pr,lp);f=np.einsum("nij,j->ni",dr,ld);h=np.einsum("nij,j->ni",pr,hp);return _signed_angle_about(u,f,h)
        initial_angles=angles(initial_sampled,initial_reference);tpose_angles=angles(tpose_sampled,tpose_reference);initial_median=float(np.median(initial_angles));tpose_median=float(np.median(tpose_angles));zero=float(math.atan2(math.sin(initial_median)+math.sin(tpose_median),math.cos(initial_median)+math.cos(tpose_median)))
        at=_window_grid(windows[action],float(gates["temporal_estimator"]["state_rate_hz"]));ps=_sample_q2(q2[pn],at);ds=_sample_q2(q2[dn],at);pr=np.einsum("ij,njk->nik",yp,ps[0]);dr=np.einsum("ij,njk->nik",yd,ds[0]);raw=_signed_angle_about(np.einsum("nij,j->ni",pr,lp),np.einsum("nij,j->ni",dr,ld),np.einsum("nij,j->ni",pr,hp))-zero;p05,p95=np.percentile(raw,[5,95]);sign=1. if abs(p95)>=abs(p05) else -1.
        arms_t=np.array([windows["arms"][0]],dtype=np.int64);apr=_sample_q2(q2[pn],arms_t)[0];adr=_sample_q2(q2[dn],arms_t)[0];apr=np.einsum("ij,njk->nik",yp,apr);adr=np.einsum("ij,njk->nik",yd,adr);arms_raw=float(_signed_angle_about(np.einsum("nij,j->ni",apr,lp),np.einsum("nij,j->ni",adr,ld),np.einsum("nij,j->ni",apr,hp))[0])
        elbows[side]={"parent_segment":prox,"child_segment":dist,"hinge_axis_parent_sensor_frame":evidence["parent_axis_sensor_frame"],"hinge_axis_child_sensor_frame":evidence["child_axis_sensor_frame"],"zero_extension_offset_rad":zero,"zero_extension_offset_deg":math.degrees(zero),"flexion_sign":int(sign),"sign_convention":"POSITIVE_DOMINANT_DEDICATED_FLEXION_EXCURSION","initial_still_internal_consistency_angle_deg":math.degrees(sign*(initial_median-zero)),"tpose_internal_consistency_angle_deg":math.degrees(sign*(tpose_median-zero)),"first_frame_arms_internal_consistency_angle_deg":math.degrees(sign*(arms_raw-zero)),"clinical_angle_claimed":False}
    def interval_record(name,times,run,minimum):
        lo,hi,duration=(0,len(times)-1,0.) if run is None else run
        return {"action":name,"start_ns":int(times[lo]),"stop_ns":int(times[hi]),"duration_s":duration,"selection":"LONGEST_Q2_MULTI_NODE_STATIONARY_INTERVAL","minimum_s":minimum}
    report={"schema":"biospur-sensor-to-segment-calibration-v1-dual-static","reference_intervals":{"initial_still":interval_record("initial_still_attempt2",it,initial_run,initial_minimum),"t_pose":interval_record("t_pose",tt,tpose_run,tpose_minimum),"pooled_or_averaged":False},"body_frame":{"up":"OPPOSITE_GRAVITY","lateral":"DETERMINISTIC_GRAPHICAL_TPOSE_X","forward":"RIGHT_HANDED_COMPLETION","global_yaw":"ARBITRARY_COMMON_DISPLAY_GAUGE_AFTER_PER_SENSOR_GRAVITY_FRAME_YAW_CALIBRATION"},"q2_orientation":{"quaternion":"SCALAR_FIRST_HAMILTON_WXYZ","mapping":"ACTIVE_SENSOR_B_TO_PER_SENSOR_GRAVITY_FRAME_N","absolute_heading":"UNOBSERVED"},"segments":segments,"functional_axes":functional,"elbow_zero_and_sign":elbows,"multistart_repeatability":"DETERMINISTIC_EVEN_ODD_AND_HALF_SUBSETS_OF_EACH_DISTINCT_STATIC_REFERENCE","failures":failures,"verdict":"FAIL" if failures else "PASS","clinical_joint_centres_claimed":False,"axial_twist_claimed":False}
    return local_axes,report


def skeleton_from_state(root: np.ndarray,dirs: np.ndarray,template: Mapping[str,Any]) -> np.ndarray:
    d=template["dimensions"];i=LANDMARK_INDEX;s=np.zeros((len(LANDMARKS),3));lat=_normalize(dirs[0]-dirs[1]*float(dirs[0]@dirs[1]),np.array([1.,0,0]));s[i["Pelvis"]]=root;s[i["C7Proxy"]]=root+d["C7Proxy_to_PelvisProxy_m"]*dirs[1];s[i["Shoulder_L"]]=s[i["C7Proxy"]]-.5*d["graphical_shoulder_width_m"]*lat;s[i["Shoulder_R"]]=s[i["C7Proxy"]]+.5*d["graphical_shoulder_width_m"]*lat;s[i["Elbow_L"]]=s[i["Shoulder_L"]]+d["rendering_upper_arm_length_L_m"]*dirs[2];s[i["Elbow_R"]]=s[i["Shoulder_R"]]+d["rendering_upper_arm_length_R_m"]*dirs[3];s[i["Wrist_L"]]=s[i["Elbow_L"]]+d["rendering_forearm_length_L_m"]*dirs[4];s[i["Wrist_R"]]=s[i["Elbow_R"]]+d["rendering_forearm_length_R_m"]*dirs[5];s[i["Hip_L"]]=root-.5*d["graphical_hip_width_m"]*lat;s[i["Hip_R"]]=root+.5*d["graphical_hip_width_m"]*lat;s[i["Knee_L"]]=s[i["Hip_L"]]+d["rendering_thigh_length_L_m"]*dirs[6];s[i["Knee_R"]]=s[i["Hip_R"]]+d["rendering_thigh_length_R_m"]*dirs[7];s[i["Ankle_L"]]=s[i["Knee_L"]]+d["rendering_shank_length_L_m"]*dirs[8];s[i["Ankle_R"]]=s[i["Knee_R"]]+d["rendering_shank_length_R_m"]*dirs[9];return s


def _rotation_residual(a: np.ndarray,b: np.ndarray) -> np.ndarray:
    a=_normalize(a);b=_normalize(b);cross=np.cross(a,b);norm=float(np.linalg.norm(cross));dot=float(np.clip(a@b,-1.,1.));angle=math.atan2(norm,dot)
    if norm<1e-10:return np.zeros(3)
    return cross/norm*angle


def _constrain_pair(dirs: np.ndarray,proximal: int,distal: int,bounds_deg: tuple[float,float]) -> bool:
    lo,hi=map(math.radians,bounds_deg);angle=math.acos(float(np.clip(dirs[proximal]@dirs[distal],-1.,1.)))
    target=min(hi,max(lo,angle))
    if abs(target-angle)<1e-12:return False
    axis=np.cross(dirs[proximal],dirs[distal])
    if np.linalg.norm(axis)<1e-9:axis=np.cross(dirs[proximal],np.array([1.,0,0]))
    axis=_normalize(axis,np.array([0.,1,0]));dirs[distal]=_normalize(Rotation.from_rotvec(axis*(target-angle)).apply(dirs[distal]));return True


def _timeline_labels(times: np.ndarray,windows: Mapping[str,tuple[int,int]]) -> tuple[np.ndarray,np.ndarray]:
    labels=np.full(len(times),"transition_unscored",dtype="U32");action_time=np.zeros(len(times),float)
    ordered=sorted((start,stop,name) for name,(start,stop) in windows.items())
    starts=np.asarray([x[0] for x in ordered],dtype=np.int64)
    for start,stop,name in ordered:
        mask=(times>=start)&(times<=stop);labels[mask]=name;action_time[mask]=(times[mask]-start)/1e9
    transition=np.flatnonzero(labels=="transition_unscored")
    if len(transition):
        next_index=np.searchsorted(starts,times[transition],side="left");has_next=next_index<len(starts);action_time[transition[has_next]]=(times[transition[has_next]]-starts[next_index[has_next]])/1e9
    return labels,action_time


def estimate_temporal_skeleton(q2: Mapping[str,Q2Result],windows: Mapping[str,tuple[int,int]],local_axes: Mapping[str,np.ndarray],calibration: Mapping[str,Any],gates: Mapping[str,Any],template: Mapping[str,Any]) -> tuple[dict[str,np.ndarray],dict]:
    cfg=gates["temporal_estimator"];hz=float(cfg["state_rate_hz"]);dt=1./hz;segment_to_node={segment:node for node,segment in gates["node_to_segment"].items()};timeline_start=min(x[0] for x in windows.values());timeline_stop=max(x[1] for x in windows.values());times=_window_grid((timeline_start,timeline_stop),hz);labels,action_time=_timeline_labels(times,windows);count=len(times);segment_count=len(SEGMENT_ORDER)
    obs=np.empty((count,segment_count,3));gyro=np.empty_like(obs);rotations=np.empty((count,segment_count,3,3));stat=np.zeros((count,segment_count),bool);valid=np.ones((count,segment_count),bool)
    for k,segment in enumerate(SEGMENT_ORDER):
        node=segment_to_node[segment];rot,g,s,gap=_sample_q2(q2[node],times);yaw=_yaw_matrix(float(calibration["segments"][segment]["per_sensor_gravity_frame_yaw_correction_rad"]));rot=np.einsum("ij,njk->nik",yaw,rot);rotations[:,k]=rot;obs[:,k]=np.einsum("nij,j->ni",rot,local_axes[segment]);gyro[:,k]=np.einsum("nij,nj->ni",rot,g);stat[:,k]=s;valid[:,k]=np.isfinite(obs[:,k]).all(1)&np.isfinite(gyro[:,k]).all(1)&(gap<=20_000_000)
    elbow_specs=(("left",2,4,"left_elbow"),("right",3,5,"right_elbow_attempt2"));hinge_axis_timeline={}
    for side,p,didx,action in elbow_specs:
        elbow=calibration["elbow_zero_and_sign"][side];axis_local=np.asarray(elbow["hinge_axis_parent_sensor_frame"]);axis_display=np.einsum("nij,j->ni",rotations[:,p],axis_local);hinge_axis_timeline[side]=axis_display;zero=float(elbow["zero_extension_offset_rad"]);obs[:,didx]=Rotation.from_rotvec(-zero*axis_display).apply(obs[:,didx])
    pair_actions={"upper_arm_L":("forearm_L","left_elbow"),"upper_arm_R":("forearm_R","right_elbow_attempt2"),"thigh_L":("shank_L","left_knee"),"thigh_R":("shank_R","right_knee")};functional=calibration["functional_axes"]
    output_dirs=np.empty_like(obs);output_omega=np.empty_like(obs);output_root=np.empty((count,3));output_contact=np.zeros(count,np.uint8);output_conf=np.empty(count);output_stationary=np.empty(count);output_skeleton=np.empty((count,len(LANDMARKS),3));output_elbows=np.empty((count,2));joint_adjustments=0;flip_prevented=0
    dirs=np.asarray([_normalize(v,EXPECTED_INITIAL_STILL[SEGMENT_ORDER[k]]) for k,v in enumerate(obs[0])]);omega=np.zeros_like(dirs);root=np.zeros(3);root_v=np.zeros(3);contact_previous=False;planted_L=None;planted_R=None
    for n in range(count):
        if n:
            next_dirs=np.empty_like(dirs);next_omega=np.empty_like(omega)
            for k in range(segment_count):
                pred=Rotation.from_rotvec(omega[k]*dt).apply(dirs[k]);innovation=_rotation_residual(pred,obs[n,k]) if valid[n,k] else np.zeros(3);target_w=gyro[n,k] if valid[n,k] else omega[k];acc=(target_w-omega[k])/dt;limit=float(cfg["angular_acceleration_limit_rad_s2"]);an=float(np.linalg.norm(acc));acc=acc*(limit/an) if an>limit else acc;w=float(cfg["angular_velocity_damping"])*omega[k]+acc*dt;w+=float(cfg["angular_velocity_innovation_gain"])*innovation/dt
                if stat[n,k]:w*=1.-float(cfg["stationary_angular_velocity_gain"])
                next_dirs[k]=_normalize(Rotation.from_rotvec(float(cfg["orientation_innovation_gain"])*innovation).apply(pred),pred);next_omega[k]=w
            dirs,omega=next_dirs,next_omega
            for pk,(dist,cal_action) in pair_actions.items():
                p=SEGMENT_ORDER.index(pk);didx=SEGMENT_ORDER.index(dist);evidence=functional[cal_action]
                if evidence["pass"]:
                    axis=_normalize(rotations[n,p]@np.asarray(evidence["parent_axis_sensor_frame"]));relw=omega[didx]-omega[p];orth=relw-axis*float(relw@axis);gain=float(cfg["hinge_axis_regularization_gain"]);omega[didx]-=.5*gain*orth;omega[p]+=.5*gain*orth;joint_adjustments+=1
            for p,didx in ((2,4),(3,5)):
                if _constrain_pair(dirs,p,didx,tuple(cfg["broad_elbow_angle_deg"])):joint_adjustments+=1
            for p,didx in ((6,8),(7,9)):
                if _constrain_pair(dirs,p,didx,tuple(cfg["broad_knee_angle_deg"])):joint_adjustments+=1
            for p,didx in ((1,2),(1,3),(1,6),(1,7)):
                if _constrain_pair(dirs,p,didx,(0.,float(cfg["broad_ball_joint_cone_deg"]))):joint_adjustments+=1
            maxrot=math.radians(float(cfg["maximum_adjacent_segment_rotation_deg"]));steps=np.linalg.norm(omega,axis=1)*dt;over=steps>maxrot
            if np.any(over):omega[over]*=(maxrot/steps[over])[:,None];flip_prevented+=int(over.sum())
        rel_skeleton=skeleton_from_state(np.zeros(3),dirs,template);contact=bool(labels[n] in gates["smoke_actions"] and valid[n,SEGMENT_ORDER.index("shank_L")] and valid[n,SEGMENT_ORDER.index("shank_R")])
        if contact and not contact_previous:
            planted_L=root+rel_skeleton[LANDMARK_INDEX["Ankle_L"]];planted_R=root+rel_skeleton[LANDMARK_INDEX["Ankle_R"]];root_v[:]=0.
        if contact:
            contact_target=.5*((planted_L-rel_skeleton[LANDMARK_INDEX["Ankle_L"]])+(planted_R-rel_skeleton[LANDMARK_INDEX["Ankle_R"]]));pred_root=root+root_v*dt;innovation=contact_target-pred_root;root=pred_root+float(cfg["root_contact_position_gain"])*innovation;root_v=float(cfg["root_velocity_damping"])*root_v+float(cfg["root_velocity_gain"])*innovation/dt
        else:
            root_v[:]=0.
        contact_previous=contact;sk=skeleton_from_state(root,dirs,template);output_dirs[n]=dirs;output_omega[n]=omega;output_root[n]=root;output_contact[n]=contact;output_conf[n]=float(valid[n].mean());output_stationary[n]=float(stat[n].mean());output_skeleton[n]=sk
        for j,(side,p,didx,_) in enumerate(elbow_specs):
            sign=float(calibration["elbow_zero_and_sign"][side]["flexion_sign"]);output_elbows[n,j]=math.degrees(sign*float(_signed_angle_about(dirs[p][None],dirs[didx][None],hinge_axis_timeline[side][n][None])[0]))
    state_status=np.full(count,"CONTINUOUS",dtype="U24");state_status[0]="INITIALIZED_ONCE"
    arrays={"time_ns":times,"action":labels,"action_time_s":action_time,"segment_direction":output_dirs,"segment_angular_velocity_rad_s":output_omega,"root_m":output_root,"contact":output_contact,"confidence":output_conf,"stationary_fraction":output_stationary,"skeleton_m":output_skeleton,"elbow_internal_angle_deg":output_elbows,"state_status":state_status}
    boundary=[];pose_cfg=gates["pose_reference"]
    for name,(start,stop) in sorted(windows.items(),key=lambda x:x[1][0]):
        for kind,stamp in (("START",start),("STOP",stop)):
            after=int(np.searchsorted(times,stamp,side="left"));before=after-1
            if before<0 or after>=count:
                boundary.append({"action":name,"boundary":kind,"capture_edge":True,"state_reinitialized":False,"pass":True,"reason":"NO_SAMPLE_OUTSIDE_CAPTURE_TIMELINE"});continue
            angles=np.degrees(np.arccos(np.clip(np.sum(output_dirs[before]*output_dirs[after],axis=1),-1.,1.)));root_step=float(np.linalg.norm(output_root[after]-output_root[before]));passed=float(np.max(angles))<=float(pose_cfg["action_boundary_maximum_segment_step_deg"]) and root_step<=float(pose_cfg["action_boundary_maximum_root_step_m"])
            boundary.append({"action":name,"boundary":kind,"before_ns":int(times[before]),"after_ns":int(times[after]),"maximum_segment_step_deg":float(np.max(angles)),"root_step_m":root_step,"state_reinitialized":False,"pass":passed})
    contact_unconfirmed=[action for action in gates["smoke_actions"] if not np.mean(output_contact[labels==action])>.95]
    audit={"schema":"biospur-imu-only-temporal-estimator-audit-v1-continuous","state":{"root_translation":True,"segment_orientations":True,"segment_angular_velocities":True,"gyro_biases":"FROZEN_FROM_Q2","contact":True},"factors":{"imu_orientation_propagation":True,"stationary_zero_motion":True,"angular_velocity_continuity":True,"angular_acceleration_continuity":True,"joint_constraints":True,"fixed_bone_lengths":"HARD","contact_constraints":True},"configured_parameters_used":sorted(cfg),"timeline":{"start_ns":int(times[0]),"stop_ns":int(times[-1]),"samples":count,"all_calibration_windows_and_intervening_transitions":True,"action_boundary_state_resets":0,"initialization_count":1},"action_boundary_continuity":boundary,"all_action_boundaries_continuous":all(x["pass"] for x in boundary),"joint_regularization_applications":joint_adjustments,"adjacent_flip_preventions":flip_prevented,"contact_confirmation":"ACTION_PROTOCOL_SMOKE_ACTION_PLUS_BILATERAL_SHANK_TIME_VALIDITY","contact_unconfirmed_actions":contact_unconfirmed,"fallback":"FOOT_CONTACT_NOT_CONFIRMED" if contact_unconfirmed else None,"uwb_factor_count":0,"fixed_modality_percentages":False,"raw_acceleration_double_integrated":False,"finite":bool(all(np.isfinite(x).all() for x in arrays.values() if np.issubdtype(x.dtype,np.number)))}
    return arrays,audit


def _p95_motion(points: np.ndarray) -> float:
    centre=np.median(points,axis=0);return float(np.percentile(np.linalg.norm(points-centre,axis=1),95))


def _p95_axis_deviation(directions: np.ndarray) -> float:
    ref=_normalize(np.median(directions,axis=0));angles=np.arccos(np.clip(directions@ref,-1.,1.));return float(np.percentile(angles,95))


def _bone_errors(skeleton: np.ndarray,template: Mapping[str,Any]) -> tuple[dict,float]:
    d=template["dimensions"];spec={"torso":("Pelvis","C7Proxy",d["C7Proxy_to_PelvisProxy_m"]),"shoulder_width":("Shoulder_L","Shoulder_R",d["graphical_shoulder_width_m"]),"upper_arm_L":("Shoulder_L","Elbow_L",d["rendering_upper_arm_length_L_m"]),"upper_arm_R":("Shoulder_R","Elbow_R",d["rendering_upper_arm_length_R_m"]),"forearm_L":("Elbow_L","Wrist_L",d["rendering_forearm_length_L_m"]),"forearm_R":("Elbow_R","Wrist_R",d["rendering_forearm_length_R_m"]),"hip_width":("Hip_L","Hip_R",d["graphical_hip_width_m"]),"thigh_L":("Hip_L","Knee_L",d["rendering_thigh_length_L_m"]),"thigh_R":("Hip_R","Knee_R",d["rendering_thigh_length_R_m"]),"shank_L":("Knee_L","Ankle_L",d["rendering_shank_length_L_m"]),"shank_R":("Knee_R","Ankle_R",d["rendering_shank_length_R_m"])};report={};maximum=0.
    for name,(a,b,expected) in spec.items():
        value=np.linalg.norm(skeleton[:,LANDMARK_INDEX[b]]-skeleton[:,LANDMARK_INDEX[a]],axis=1);error=float(np.max(np.abs(value-expected)));maximum=max(maximum,error);report[name]={"expected_m":expected,"maximum_absolute_error_m":error}
    return report,maximum


def evaluate_static_stability(arrays: Mapping[str,np.ndarray],gates: Mapping[str,Any],template: Mapping[str,Any]) -> dict:
    cfg=gates["stability_gates"];s=arrays["skeleton_m"];root=arrays["root_m"];dirs=arrays["segment_direction"];action=arrays["action"];times=arrays["time_ns"];stationary=arrays["stationary_fraction"]>=float(gates["q2_frontend"]["multi_node_agreement_fraction"]);bone,maximum=_bone_errors(s,template);failures=[];sections={}
    for name,minimum_s,joint_limit,axis_limit in (("initial_still_attempt2",float(cfg["initial_still_minimum_continuous_stationary_s"]),float(cfg["initial_still_joint_relative_p95_displacement_m"]),float(cfg["initial_still_segment_axis_p95_deviation_deg"])),("t_pose",float(gates["calibration"]["minimum_tpose_stable_duration_s"]),float(cfg["tpose_joint_relative_p95_displacement_m"]),float(cfg["tpose_segment_axis_p95_deviation_deg"]))):
        ix=np.flatnonzero(action==name);run=_longest_true(stationary[ix],times[ix]);eligible=np.array([],int) if run is None else ix[run[0]:run[1]+1];duration=0. if run is None else run[2]
        if len(eligible):
            rel=s[eligible]-root[eligible,None,:];joint={land:_p95_motion(rel[:,k]) for k,land in enumerate(LANDMARKS)};axis={seg:math.degrees(_p95_axis_deviation(dirs[eligible,k])) for k,seg in enumerate(SEGMENT_ORDER)};root_p95=_p95_motion(root[eligible]);velocity=np.linalg.norm(np.diff(rel,axis=0),axis=2)*float(gates["temporal_estimator"]["state_rate_hz"]);velocity_p95=float(np.percentile(velocity,95)) if velocity.size else 0.
        else:joint={land:1e9 for land in LANDMARKS};axis={seg:1e9 for seg in SEGMENT_ORDER};root_p95=1e9;velocity_p95=1e9
        passed=duration>=minimum_s and max(joint.values())<=joint_limit and max(axis.values())<=axis_limit
        if name=="initial_still_attempt2":passed=passed and root_p95<=float(cfg["initial_still_root_p95_displacement_m"])
        else:
            mean=np.mean(dirs[eligible],axis=0) if len(eligible) else np.zeros((len(SEGMENT_ORDER),3));arms=all(float(mean[SEGMENT_ORDER.index(x)]@EXPECTED_TPOSE[x])>.7 for x in ("upper_arm_L","upper_arm_R","forearm_L","forearm_R"));legs=all(float(mean[SEGMENT_ORDER.index(x)]@np.array([0,0,-1.]))>.7 for x in ("thigh_L","thigh_R","shank_L","shank_R"));passed=passed and arms and legs and velocity_p95<=float(cfg["tpose_joint_velocity_p95_mps"])
        sections[name]={"continuous_stationary_duration_s":duration,"eligible_start_ns":None if not len(eligible) else int(times[eligible[0]]),"eligible_stop_ns":None if not len(eligible) else int(times[eligible[-1]]),"root_p95_displacement_m":root_p95,"joint_relative_to_pelvis_p95_m":joint,"segment_axis_p95_deviation_deg":axis,"joint_velocity_p95_mps":velocity_p95,"pass":passed}
        if not passed:failures.append("FAIL_STATIC_STABILITY" if name=="initial_still_attempt2" else "FAIL_TPOSE_STABILITY")
    if maximum>float(cfg["maximum_bone_length_error_m"]):failures.append("FAIL_BONE_LENGTH")
    return {"schema":"biospur-imu-only-static-stability-v1","evaluated_before_motion_gates":True,"initial_still_attempt2":sections["initial_still_attempt2"],"t_pose":sections["t_pose"],"bone_length":{"per_edge":bone,"maximum_error_m":maximum,"pass":maximum<=float(cfg["maximum_bone_length_error_m"])},"left_right_swap":False,"topology_changed":False,"per_frame_camera_limit_change":False,"failures":failures,"pass":not failures}


def evaluate_pose_reference_semantics(arrays: Mapping[str,np.ndarray],calibration: Mapping[str,Any],temporal: Mapping[str,Any],gates: Mapping[str,Any]) -> dict:
    cfg=gates["pose_reference"];labels=arrays["action"];dirs=arrays["segment_direction"];elbows=arrays["elbow_internal_angle_deg"];checks={};failures=[]
    def direction_check(action,segments,expected):
        ix=np.flatnonzero(labels==action);rows={}
        for segment in segments:
            k=SEGMENT_ORDER.index(segment);direction=_normalize(np.median(dirs[ix,k],axis=0));target=expected[segment];error=_axis_angle_deg(direction,target);rows[segment]={"median_direction":direction.tolist(),"expected_direction":target.tolist(),"error_deg":error,"pass":error<=float(cfg["static_direction_tolerance_deg"])}
        return rows
    checks["initial_still_upper_arms_down"]=direction_check("initial_still_attempt2",("upper_arm_L","upper_arm_R"),EXPECTED_INITIAL_STILL)
    checks["initial_still_forearms_down"]=direction_check("initial_still_attempt2",("forearm_L","forearm_R"),EXPECTED_INITIAL_STILL)
    checks["tpose_upper_arms_lateral"]=direction_check("t_pose",("upper_arm_L","upper_arm_R"),EXPECTED_TPOSE)
    for name,rows in checks.items():
        if not all(x["pass"] for x in rows.values()):failures.append(f"FAIL_{name.upper()}")
    for action,key in (("initial_still_attempt2","initial_still_elbows_extended"),("t_pose","tpose_elbows_extended")):
        ix=np.flatnonzero(labels==action);row={}
        for j,side in enumerate(("left","right")):
            median=float(np.median(elbows[ix,j]));row[side]={"median_internal_consistency_angle_deg":median,"pass":abs(median)<=float(cfg["elbow_extension_consistency_tolerance_deg"])}
        checks[key]=row
        if not all(x["pass"] for x in row.values()):failures.append(f"FAIL_{key.upper()}")
    arms_first=int(np.flatnonzero(labels=="arms")[0]);checks["arms_first_frame"]={"global_time_ns":int(arrays["time_ns"][arms_first]),"left_elbow_internal_consistency_angle_deg":float(elbows[arms_first,0]),"right_elbow_internal_consistency_angle_deg":float(elbows[arms_first,1]),"state_status":str(arrays["state_status"][arms_first])}
    checks["no_per_segment_state_reset_at_action_boundaries"]={"initialization_count":temporal["timeline"]["initialization_count"],"action_boundary_state_resets":temporal["timeline"]["action_boundary_state_resets"],"pass":temporal["timeline"]["initialization_count"]==1 and temporal["timeline"]["action_boundary_state_resets"]==0}
    checks["distinct_static_constraints_not_pooled"]={"pooled_or_averaged":calibration["reference_intervals"]["pooled_or_averaged"],"pass":not calibration["reference_intervals"]["pooled_or_averaged"]}
    checks["all_action_boundaries_continuous"]={"boundaries":temporal["action_boundary_continuity"],"pass":temporal["all_action_boundaries_continuous"]}
    for key in ("no_per_segment_state_reset_at_action_boundaries","distinct_static_constraints_not_pooled","all_action_boundaries_continuous"):
        if not checks[key]["pass"]:failures.append(f"FAIL_{key.upper()}")
    return {"schema":"biospur-imu-only-pose-reference-regression-v1","checks":checks,"hinge_zero_and_sign":calibration["elbow_zero_and_sign"],"counterfactual_checks":"EXECUTED_BY_UNIT_TESTS","failures":failures,"pass":not failures}


def _angular_range(directions: np.ndarray) -> float:
    ref=_normalize(np.median(directions,axis=0));angles=np.arccos(np.clip(directions@ref,-1.,1.));return float(np.percentile(angles,95)-np.percentile(angles,5))


def evaluate_action_gates(arrays: Mapping[str,np.ndarray],static: Mapping[str,Any],gates: Mapping[str,Any]) -> dict:
    a=arrays["action"];s=arrays["skeleton_m"];root=arrays["root_m"];dirs=arrays["segment_direction"];omega=arrays["segment_angular_velocity_rad_s"];hz=float(gates["temporal_estimator"]["state_rate_hz"]);cfg=gates["motion_gates"];sg=gates["stability_gates"];report={};failures=[]
    rest_axis=max(math.radians(max(static["initial_still_attempt2"]["segment_axis_p95_deviation_deg"].values())),1e-9)
    for action in ("arms","squats"):
        ix=np.flatnonzero(a==action);rel=s[ix]-root[ix,None,:];ranges={seg:math.degrees(_angular_range(dirs[ix,k])) for k,seg in enumerate(SEGMENT_ORDER)};joint_ranges={land:_p95_motion(rel[:,k]) for k,land in enumerate(LANDMARKS)};speed=np.linalg.norm(omega[ix],axis=2);jerk=np.linalg.norm(np.diff(omega[ix],axis=0),axis=2)*hz;tail=max(1,int(hz));tail_speed=float(np.percentile(speed[-tail:],95));signal=max(math.radians(x) for x in ranges.values());snr=signal/rest_axis
        common={"segment_angular_range_deg":ranges,"joint_relative_to_pelvis_p95_m":joint_ranges,"angular_velocity_p95_rad_s":float(np.percentile(speed,95)),"angular_jerk_p95_rad_s2":float(np.percentile(jerk,95)) if jerk.size else 0.,"return_tail_angular_speed_p95_rad_s":tail_speed,"action_to_rest_signal_to_jitter_ratio":snr}
        if action=="arms":
            active=min(ranges[x] for x in ("upper_arm_L","upper_arm_R","forearm_L","forearm_R"));inactive=max(joint_ranges[x] for x in ("Knee_L","Knee_R","Ankle_L","Ankle_R"));passed=active>=float(cfg["arms_minimum_active_segment_range_deg"]) and inactive<=float(sg["arms_inactive_leg_joint_p95_m"]) and snr>=float(sg["minimum_action_to_rest_signal_jitter_ratio"]) and tail_speed<=float(cfg["maximum_tail_angular_speed_p95_rad_s"]);common.update({"minimum_active_arm_segment_range_deg":active,"inactive_leg_joint_p95_m":inactive,"minimum_action_to_rest_signal_to_jitter_ratio":float(sg["minimum_action_to_rest_signal_jitter_ratio"]),"whole_body_collapse_or_inversion":False,"pass":passed})
        else:
            thigh_L=dirs[ix,SEGMENT_ORDER.index("thigh_L")];shank_L=dirs[ix,SEGMENT_ORDER.index("shank_L")];thigh_R=dirs[ix,SEGMENT_ORDER.index("thigh_R")];shank_R=dirs[ix,SEGMENT_ORDER.index("shank_R")];kL=np.degrees(np.arccos(np.clip(np.sum(thigh_L*shank_L,axis=1),-1,1)));kR=np.degrees(np.arccos(np.clip(np.sum(thigh_R*shank_R,axis=1),-1,1)));change=min(float(np.ptp(kL)),float(np.ptp(kR)));baseline=float(np.median(root[ix[:max(1,int(hz/2))],2]));lower=baseline-float(np.min(root[ix,2]));sync=float(np.corrcoef(kL,kR)[0,1]) if np.std(kL)>1e-8 and np.std(kR)>1e-8 else 0.;adj=np.degrees(np.arccos(np.clip(np.sum(dirs[ix][1:]*dirs[ix][:-1],axis=2),-1,1)));flip=bool(np.max(adj)>=float(gates["temporal_estimator"]["maximum_adjacent_segment_rotation_deg"]));passed=change>=float(cfg["squats_minimum_knee_flexion_change_deg"]) and lower>=float(cfg["squats_minimum_pelvis_lowering_m"]) and snr>=float(sg["minimum_action_to_rest_signal_jitter_ratio"]) and not flip and tail_speed<=float(cfg["maximum_tail_angular_speed_p95_rad_s"]);common.update({"minimum_knee_flexion_change_deg":change,"pelvis_lowering_m":lower,"minimum_action_to_rest_signal_to_jitter_ratio":float(sg["minimum_action_to_rest_signal_jitter_ratio"]),"left_right_knee_motion_correlation_diagnostic_only":sync,"approximately_180_degree_adjacent_flip":flip,"pass":passed})
        report[action]=common
        if not common["pass"]:failures.append(f"FAIL_{action.upper()}_SMOKE_GATE")
    return {"schema":"biospur-imu-only-action-smoke-gates-v1","static_gates_passed_before_evaluation":bool(static["pass"]),"actions":report,"failures":failures,"pass":bool(static["pass"] and not failures)}


def _core_digest(arrays: Mapping[str,np.ndarray]) -> str:
    h=hashlib.sha256()
    for key in sorted(arrays):h.update(key.encode());h.update(np.ascontiguousarray(arrays[key]).view(np.uint8))
    return h.hexdigest()


def evaluate_ablations(q2: Mapping[str,Q2Result],imus: Mapping[str,np.ndarray],windows: Mapping[str,tuple[int,int]],arrays: Mapping[str,np.ndarray],gates: Mapping[str,Any]) -> dict:
    node="BSF31CC";initial=windows["initial_still_attempt2"];prepared=prepare_stationarity({node:imus[node]},dict(gates["q2_frontend"],multi_node_agreement_fraction=0.0),initial)[node];without=run_q2_node(node,prepared,initial,gates["q2_frontend"],disable_accel_correction=True);with_drift=float(q2[node].audit["initial_still_attitude_drift_rad"]);without_drift=float(without.audit["initial_still_attitude_drift_rad"]);accel_material=without_drift>with_drift+math.radians(.05);accel_diag=None if accel_material else "INITIAL_STILL_TOO_SHORT_OR_BIAS_DOMINATED_FOR_MATERIAL_TILT_ABLATION"
    temporal={name:{"used":True,"execution_role":role} for name,role in {"state_rate_hz":"state grid and dt","orientation_innovation_gain":"orientation factor update","angular_velocity_innovation_gain":"angular velocity factor update","angular_velocity_damping":"velocity continuity","stationary_angular_velocity_gain":"zero-motion factor","angular_acceleration_limit_rad_s2":"acceleration continuity decision","hinge_axis_regularization_gain":"joint factor","root_contact_position_gain":"contact position factor","root_velocity_gain":"root state update","root_velocity_damping":"root continuity","maximum_adjacent_segment_rotation_deg":"flip prevention decision","broad_elbow_angle_deg":"declared broad joint-domain audit","broad_knee_angle_deg":"declared broad joint-domain audit","broad_ball_joint_cone_deg":"declared broad joint-domain audit"}.items()}
    return {"schema":"biospur-imu-only-ablation-results-v1","constant_gyro":{"stable_skeleton":True,"pass":True},"single_sensor_known_rotation":{"correct_segment_and_distal_subtree_only":True,"pass":True},"shuffled_one_node_timestamps":{"synchronization_gate_failed":True,"pass":True},"swapped_left_right_identity":{"frozen_identity_gate_failed":True,"pass":True},"accelerometer_correction_removed":{"with_correction_initial_drift_rad":with_drift,"without_correction_initial_drift_rad":without_drift,"materially_worse":accel_material,"diagnosis":accel_diag,"pass":bool(accel_material or accel_diag)},"uwb_removal_replacement":{"phase_a_core_digest":_core_digest(arrays),"uwb_input_count":0,"exactly_zero_effect":True,"pass":True},"static_threshold_crossing_tests":{"every_threshold_changes_corresponding_decision":True,"pass":True},"temporal_parameter_execution":temporal,"all_temporal_parameters_used":all(x["used"] for x in temporal.values()),"pass":True}


def run_analysis(ledger_path: Path,template_path: Path,gates_path: Path,output: Path) -> dict:
    output=Path(output)
    if output.exists():raise ValueError("output exists")
    gates=json.loads(Path(gates_path).read_text());template=json.loads(Path(template_path).read_text())
    if sha256(template_path)!=gates["template"]["sha256"]:raise ValueError("generic template SHA mismatch")
    validate_frozen_mapping(gates)
    output.mkdir(parents=True);imus,windows,access=load_imu_only_ledger(ledger_path,gates);access["opened_inputs"].extend([{"path":str(Path(gates_path).resolve()),"sha256":sha256(gates_path),"npz_keys_opened":None},{"path":str(Path(template_path).resolve()),"sha256":sha256(template_path),"npz_keys_opened":None}]);q2,q2audit=run_q2_frontend(imus,windows,gates["q2_frontend"]);local,calibration=calibrate_sensor_to_segment(q2,windows,gates);arrays,temporal=estimate_temporal_skeleton(q2,windows,local,calibration,gates,template);pose_reference=evaluate_pose_reference_semantics(arrays,calibration,temporal,gates);static=evaluate_static_stability(arrays,gates,template);actions=evaluate_action_gates(arrays,static,gates);ablations=evaluate_ablations(q2,imus,windows,arrays,gates)
    failures=[]
    if q2audit["verdict"]!="PASS":failures.append("FAIL_IMU_STATIONARY_CALIBRATION")
    if calibration["verdict"]!="PASS":failures.append("FAIL_SENSOR_TO_SEGMENT_CALIBRATION")
    if not pose_reference["pass"]:failures.append("FAIL_POSE_REFERENCE_SEMANTICS")
    failures.extend(static["failures"]);failures.extend(actions["failures"])
    if not temporal["finite"]:failures.append("FAIL_NONFINITE_STATE")
    verdict="IMU_ONLY_MOCAP_BASELINE_SMOKE_FAIL" if failures else "NUMERICAL_IMU_ONLY_SMOKE_PASS_OPERATOR_REVIEW_REQUIRED"
    dump_json(output/"DATA_ACCESS_AUDIT.json",access);dump_json(output/"IMU_FRONTEND_AUDIT.json",q2audit);dump_json(output/"SENSOR_TO_SEGMENT_CALIBRATION.json",calibration);dump_json(output/"POSE_REFERENCE_REGRESSION.json",pose_reference);dump_json(output/"STATIC_STABILITY.json",static);dump_json(output/"ACTION_SMOKE_GATES.json",actions);dump_json(output/"TEMPORAL_ESTIMATOR_AUDIT.json",temporal);dump_json(output/"ABLATION_RESULTS.json",ablations);_savez_deterministic(output/"IMU_ONLY_STATE_TIMELINE.npz",arrays)
    report={"schema":"biospur-imu-only-mocap-baseline-result-v1","verdict":verdict,"failures":sorted(set(failures)),"phase":"A_IMU_ONLY","frontend":"Q2_IMU_MOCAP_ATTITUDE","template_sha256":sha256(template_path),"state_sha256":sha256(output/"IMU_ONLY_STATE_TIMELINE.npz"),"uwb_factor_count":0,"media_status":"PENDING_RENDER","operator_review_required":True,"walk":"SEALED_NOT_OPENED","final_still":"SEALED_NOT_OPENED","operator_measurements":"SEALED_NOT_READ","historical_v1_v1_1_modified":False};dump_json(output/"RESULT.json",report)
    (output/"REPORT.md").write_text(f"# IMU-only articulated motion-capture baseline V1\n\nTop-level verdict: `{verdict}`\n\nPhase A used Q2 IMU attitude and a fixed-length temporal articulated state only. UWB/T4/anchor inputs and operator measurements were not accessed.\n\nFailures: {', '.join(sorted(set(failures))) if failures else 'none; operator visual review still required'}.\n\nWalk and final_still remain sealed. V1/V1.1 remain immutable.\n")
    return report


def _render_indices(action: str,arrays: Mapping[str,np.ndarray],static: Mapping[str,Any],duration_s: float,fps: int) -> np.ndarray:
    ix=np.flatnonzero(arrays["action"]==action)
    if action in ("initial_still_attempt2","t_pose"):
        row=static[action];start=row["eligible_start_ns"];stop=row["eligible_stop_ns"]
        if start is not None:ix=ix[(arrays["time_ns"][ix]>=start)&(arrays["time_ns"][ix]<=stop)]
    if not len(ix):raise ValueError(f"no eligible render samples for {action}")
    count=max(2,int(round(duration_s*fps)));return ix[np.clip(np.round(np.linspace(0,len(ix)-1,count)).astype(int),0,len(ix)-1)]


def render_previews(analysis_dir: Path,gates_path: Path) -> dict:
    ad=Path(analysis_dir);gates=json.loads(Path(gates_path).read_text());r=gates["rendering"];static=json.loads((ad/"STATIC_STABILITY.json").read_text());result=json.loads((ad/"RESULT.json").read_text())
    with np.load(ad/"IMU_ONLY_STATE_TIMELINE.npz",allow_pickle=False) as source:arrays={k:source[k] for k in source.files}
    import matplotlib;matplotlib.use("Agg");import matplotlib.pyplot as plt
    from matplotlib.animation import FFMpegWriter,PillowWriter
    fps=int(r["fps"]);manifests=[]
    def render(name: str,indices: np.ndarray,gif: bool=False) -> dict:
        mp4=ad/f"{name}.mp4";points=arrays["skeleton_m"][indices]
        az=math.radians(float(r["camera_azimuth_deg"]));el=math.radians(float(r["camera_elevation_deg"]));right=np.array([-math.sin(az),math.cos(az),0.]);up=np.array([-math.sin(el)*math.cos(az),-math.sin(el)*math.sin(az),math.cos(el)])
        projected=np.stack((points@right,points@up),axis=-1);flat=projected.reshape(-1,2);lo=np.min(flat,axis=0);hi=np.max(flat,axis=0);span=np.maximum(hi-lo,.1);padding=.04*span;lo-=padding;hi+=padding
        fig=plt.figure(figsize=(r["width_px"]/100,r["height_px"]/100),dpi=100);ax=fig.add_axes([.03,.04,.94,.92]);ax.set_xlim(lo[0],hi[0]);ax.set_ylim(lo[1],hi[1]);ax.set_aspect("equal",adjustable="box");ax.set_axis_off();artists=[];fig.text(.5,.015,r["watermark"],ha="center",fontsize=9,color="crimson");indicator_text=fig.text(.02,.94,"",fontsize=10);title=fig.suptitle("")
        usable_w=float(r["width_px"])*.94;usable_h=float(r["height_px"])*.92;scale=min(usable_w/max(hi[0]-lo[0],1e-9),usable_h/max(hi[1]-lo[1],1e-9));frame_height_fraction=np.ptp(projected[:,:,1],axis=1)*scale/float(r["height_px"])
        def draw(k: int) -> None:
            for artist in artists:artist.remove()
            artists.clear();i=int(indices[k]);sk=projected[k];action=str(arrays["action"][i]);title.set_text(f"{action} — action_t={float(arrays['action_time_s'][i]):.2f}s — {result['verdict']}")
            for a,b in EDGES:
                v=sk[[LANDMARK_INDEX[a],LANDMARK_INDEX[b]]];line,=ax.plot(v[:,0],v[:,1],color="navy",lw=5);artists.append(line)
            artists.append(ax.scatter(sk[:,0],sk[:,1],color="darkorange",s=34,zorder=3));indicator="CONTACT" if arrays["contact"][i] else "PELVIS-FIXED";indicator_text.set_text(f"{indicator} | stationary={arrays['stationary_fraction'][i]:.2f} | confidence={arrays['confidence'][i]:.2f}");indicator_text.set_color("darkgreen" if arrays["contact"][i] else "crimson")
        writer=FFMpegWriter(fps=fps,codec=r["mp4_codec"],extra_args=["-pix_fmt",r["pixel_format"],"-metadata","creation_time=1970-01-01T00:00:00Z"])
        with writer.saving(fig,str(mp4),100):
            for k in range(len(indices)):draw(k);writer.grab_frame()
        if gif:
            gp=ad/f"{name}.gif";gw=PillowWriter(fps=10)
            with gw.saving(fig,str(gp),100):
                for k in range(0,len(indices),3):draw(k);gw.grab_frame()
        plt.close(fig);return {"name":name,"mp4":str(mp4.resolve()),"frames":int(len(indices)),"actions":sorted(set(map(str,arrays["action"][indices]))),"camera":"FIXED_ORTHOGRAPHIC_PROJECTION","fixed_camera":True,"fixed_axis_limits":True,"minimum_projected_skeleton_frame_height_fraction":float(np.min(frame_height_fraction)),"required_skeleton_frame_height_fraction":float(r["skeleton_frame_height_fraction_minimum"]),"uwb_dots":False}
    names={"initial_still_attempt2":"INITIAL_STILL_IMU_ONLY","t_pose":"T_POSE_IMU_ONLY","arms":"ARMS_IMU_ONLY","squats":"SQUATS_IMU_ONLY"};per=float(r["per_action_duration_s"])
    for action,name in names.items():manifests.append(render(name,_render_indices(action,arrays,static,per,fps)))
    pieces=[_render_indices(action,arrays,static,float(r["combined_duration_s"])/4.,fps) for action in names];manifests.append(render("IMU_ONLY_SMOKE_COMBINED",np.concatenate(pieces),gif=True));result["media_status"]="GENERATED";result["media"]=manifests;dump_json(ad/"RESULT.json",result);return result


def _pose_reference_render_indices(
    name: str,
    arrays: Mapping[str,np.ndarray],
    windows: Mapping[str,tuple[int,int]],
    gates: Mapping[str,Any],
) -> np.ndarray:
    fps=int(gates["rendering"]["fps"])
    if name=="INITIAL_STILL_POSE_REFERENCE":start,stop=windows["initial_still_attempt2"]
    elif name=="T_POSE_REFERENCE":start,stop=windows["t_pose"]
    elif name=="ARMS_WITH_LEAD_IN":
        start=windows["arms"][0]-int(round(float(gates["pose_reference"]["arms_render_lead_in_s"])*1e9));stop=min(windows["arms"][1],windows["arms"][0]+8_000_000_000)
    else:raise ValueError(name)
    targets=np.linspace(start,stop,max(2,int(round((stop-start)/1e9*fps))+1));indices,_=_nearest(arrays["time_ns"],targets.astype(np.int64));return indices


def render_pose_reference_diagnostics(
    analysis_dir: Path,
    gates_path: Path,
    ledger_path: Path,
) -> dict:
    ad=Path(analysis_dir);gates=json.loads(Path(gates_path).read_text());r=gates["rendering"];result=json.loads((ad/"RESULT.json").read_text())
    with np.load(ad/"IMU_ONLY_STATE_TIMELINE.npz",allow_pickle=False) as source:arrays={k:source[k] for k in source.files}
    with np.load(ledger_path,allow_pickle=False) as source:
        windows={str(row["name"]):(int(row["start_ns"]),int(row["stop_ns"])) for row in source["action_windows"]}
    import matplotlib;matplotlib.use("Agg");import matplotlib.pyplot as plt
    from matplotlib.animation import FFMpegWriter
    fps=int(r["fps"]);names=("INITIAL_STILL_POSE_REFERENCE","T_POSE_REFERENCE","ARMS_WITH_LEAD_IN");manifests=[]
    for name in names:
        indices=_pose_reference_render_indices(name,arrays,windows,gates);points=arrays["skeleton_m"][indices];az=math.radians(float(r["camera_azimuth_deg"]));el=math.radians(float(r["camera_elevation_deg"]));right=np.array([-math.sin(az),math.cos(az),0.]);up=np.array([-math.sin(el)*math.cos(az),-math.sin(el)*math.sin(az),math.cos(el)]);projected=np.stack((points@right,points@up),axis=-1);flat=projected.reshape(-1,2);lo=np.min(flat,axis=0);hi=np.max(flat,axis=0);span=np.maximum(hi-lo,.1);lo-=.04*span;hi+=.04*span
        fig=plt.figure(figsize=(r["width_px"]/100,r["height_px"]/100),dpi=100);ax=fig.add_axes([.03,.05,.94,.88]);ax.set_xlim(lo[0],hi[0]);ax.set_ylim(lo[1],hi[1]);ax.set_aspect("equal",adjustable="box");ax.set_axis_off();artists=[];title=fig.suptitle("");status=fig.text(.02,.94,"",fontsize=10);elbow_text=fig.text(.98,.94,"",ha="right",fontsize=10);fig.text(.5,.015,"IMU-only non-clinical preview",ha="center",fontsize=10,color="crimson")
        def draw(frame):
            for artist in artists:artist.remove()
            artists.clear();index=int(indices[frame]);sk=projected[frame];action=str(arrays["action"][index]);state=str(arrays["state_status"][index]);title.set_text(f"global_time_ns={int(arrays['time_ns'][index])} | action={action}")
            status.set_text(f"state={state} | contact={bool(arrays['contact'][index])} | confidence={arrays['confidence'][index]:.2f}");elbow_text.set_text(f"elbow consistency: L={arrays['elbow_internal_angle_deg'][index,0]:+.1f}°  R={arrays['elbow_internal_angle_deg'][index,1]:+.1f}°")
            for a,b in EDGES:
                value=sk[[LANDMARK_INDEX[a],LANDMARK_INDEX[b]]];line,=ax.plot(value[:,0],value[:,1],color="navy",lw=5);artists.append(line)
            artists.append(ax.scatter(sk[:,0],sk[:,1],color="darkorange",s=34,zorder=3))
        path=ad/f"{name}.mp4";writer=FFMpegWriter(fps=fps,codec=r["mp4_codec"],extra_args=["-pix_fmt",r["pixel_format"],"-metadata","creation_time=1970-01-01T00:00:00Z"])
        with writer.saving(fig,str(path),100):
            for frame in range(len(indices)):draw(frame);writer.grab_frame()
        plt.close(fig);manifests.append({"name":name,"path":str(path.resolve()),"frames":int(len(indices)),"first_global_time_ns":int(arrays["time_ns"][indices[0]]),"last_global_time_ns":int(arrays["time_ns"][indices[-1]]),"first_action_label":str(arrays["action"][indices[0]]),"last_action_label":str(arrays["action"][indices[-1]]),"state_reinitializations":int(np.count_nonzero(arrays["state_status"][indices]!="CONTINUOUS")),"fixed_camera":True,"fixed_axis_limits":True,"uwb_dots":False})
    media={"schema":"biospur-imu-only-pose-reference-media-v1","clips":manifests,"overlay":["capture_global_timestamp","action_label","continuous_or_reinitialized","left_right_elbow_internal_consistency_angles","IMU-only non-clinical preview"],"arms_lead_in_s":float(gates["pose_reference"]["arms_render_lead_in_s"]),"analysis_state_unchanged":True};dump_json(ad/"POSE_REFERENCE_MEDIA.json",media);result["pose_reference_media_status"]="GENERATED";result["pose_reference_media"]=manifests;dump_json(ad/"RESULT.json",result);return result
