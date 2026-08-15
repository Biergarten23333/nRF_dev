"""Calibration-only generic-template motion demo.

The generic dimensions are immutable rendering priors.  This module accepts no
operator measurement input and no held-out payload input.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np
from scipy.optimize import least_squares
from scipy.spatial.transform import Rotation

from biospur_fusion.calibration.real_capture import NODE_TO_SEGMENT, _solve_t4
from biospur_fusion.imu.frontend import audits_as_json, run_q1_attitude
from biospur_fusion.imu.q1 import quaternion_to_matrix


NODE_ORDER = tuple(NODE_TO_SEGMENT)
NODE_INDEX = {node: i for i, node in enumerate(NODE_ORDER)}
LANDMARKS = (
    "Pelvis", "C7Proxy", "Shoulder_L", "Shoulder_R", "Elbow_L", "Elbow_R",
    "Wrist_L", "Wrist_R", "Hip_L", "Hip_R", "Knee_L", "Knee_R", "Ankle_L", "Ankle_R",
)
LANDMARK_INDEX = {name: i for i, name in enumerate(LANDMARKS)}
NODE_LANDMARK = {
    "BSFC2CC": "Pelvis", "BSF31CC": "C7Proxy", "BSFAA61": "Elbow_L",
    "BSF1120": "Elbow_R", "BSFB165": "Wrist_L", "BSFEC35": "Wrist_R",
    "BSF44AD": "Knee_L", "BSF3C79": "Knee_R", "BSF6C53": "Ankle_L", "BSF8BC4": "Ankle_R",
}
EDGES = (
    ("Pelvis", "C7Proxy"), ("C7Proxy", "Shoulder_L"), ("C7Proxy", "Shoulder_R"),
    ("Shoulder_L", "Elbow_L"), ("Elbow_L", "Wrist_L"),
    ("Shoulder_R", "Elbow_R"), ("Elbow_R", "Wrist_R"),
    ("Pelvis", "Hip_L"), ("Pelvis", "Hip_R"),
    ("Hip_L", "Knee_L"), ("Knee_L", "Ankle_L"),
    ("Hip_R", "Knee_R"), ("Knee_R", "Ankle_R"),
)
SEGMENT_SPECS = {
    "pelvis_torso_proxy": ("BSFC2CC", "BSFC2CC", "BSF31CC"),
    "torso": ("BSF31CC", "BSFC2CC", "BSF31CC"),
    "upper_arm_L": ("BSFAA61", "BSF31CC", "BSFAA61"),
    "upper_arm_R": ("BSF1120", "BSF31CC", "BSF1120"),
    "forearm_L": ("BSFB165", "BSFAA61", "BSFB165"),
    "forearm_R": ("BSFEC35", "BSF1120", "BSFEC35"),
    "thigh_L": ("BSF44AD", "BSFC2CC", "BSF44AD"),
    "thigh_R": ("BSF3C79", "BSFC2CC", "BSF3C79"),
    "shank_L": ("BSF6C53", "BSF44AD", "BSF6C53"),
    "shank_R": ("BSF8BC4", "BSF3C79", "BSF8BC4"),
}
DISCLAIMER = (
    "Generic non-clinical motion demo. Skeleton dimensions are not subject-specific. "
    "Axial twist and clinical joint angles are not validated."
)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(4 << 20), b""):
            h.update(block)
    return h.hexdigest()


def dump_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")


def _normalize(vector: np.ndarray, fallback: np.ndarray | None = None) -> np.ndarray:
    norm = float(np.linalg.norm(vector))
    if np.isfinite(norm) and norm > 1e-10:
        return np.asarray(vector, float) / norm
    if fallback is None:
        raise ValueError("degenerate direction")
    return _normalize(np.asarray(fallback, float))


def _nearest(times: np.ndarray, targets: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    at = np.searchsorted(times, targets)
    hi = np.clip(at, 0, len(times)-1); lo = np.clip(at-1, 0, len(times)-1)
    take_hi = np.abs(times[hi]-targets) < np.abs(times[lo]-targets)
    idx = np.where(take_hi, hi, lo)
    return idx, np.abs(times[idx]-targets)


def _windows(ledger, gates: dict) -> dict[str, tuple[int, int]]:
    rows = {str(r["name"]): (int(r["start_ns"]), int(r["stop_ns"])) for r in ledger["action_windows"]}
    expected = gates["calibration_actions"]
    if set(rows) != set(expected):
        raise ValueError(f"calibration action firewall mismatch: {sorted(rows)}")
    return {name: rows[name] for name in expected}


def _q1(ledger, windows):
    start, stop = windows["initial_still_attempt2"]; end = max(v[1] for v in windows.values())
    q1 = {}; audits = {}
    for node in NODE_ORDER:
        q1[node], audits[node] = run_q1_attitude(
            ledger[f"imu_{node}"], node_id=node, initial_start_ns=start,
            initial_end_ns=stop, analysis_end_ns=end,
        )
    return q1, audits_as_json(audits)


def _build_timeline(windows: dict, hz: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    times=[]; actions=[]; action_time=[]; step=int(round(1e9/hz))
    for action,(start,stop) in windows.items():
        t=np.arange(start, stop+1, step, dtype=np.int64)
        times.extend(t.tolist()); actions.extend([action]*len(t)); action_time.extend(((t-start)/1e9).tolist())
    return np.asarray(times,np.int64),np.asarray(actions),np.asarray(action_time,float)


def _sample_frontends(times, observations, q1, gates):
    n=len(times); raw=np.full((n,len(NODE_ORDER),3),np.nan);cov=np.full((n,len(NODE_ORDER),3,3),np.nan)
    rotations=np.full((n,len(NODE_ORDER),3,3),np.nan); uwb_gap=np.full((n,len(NODE_ORDER)),np.iinfo(np.int64).max,dtype=np.int64)
    q1_gap=uwb_gap.copy(); maxu=int(gates["estimator"]["maximum_uwb_match_gap_ns"]);maxq=int(gates["estimator"]["maximum_q1_match_gap_ns"])
    for j,node in enumerate(NODE_ORDER):
        obs=observations[node];idx,gap=_nearest(obs["time_ns"],times);ok=gap<=maxu
        raw[ok,j]=obs["position"][idx[ok]];cov[ok,j]=obs["covariance"][idx[ok]];uwb_gap[:,j]=gap
        qi,qgap=_nearest(q1[node]["global_time_ns"],times);qok=qgap<=maxq
        rotations[qok,j]=np.asarray([quaternion_to_matrix(q) for q in q1[node]["q_wxyz"][qi[qok]]]);q1_gap[:,j]=qgap
    return raw,cov,rotations,uwb_gap,q1_gap


def deterministic_display_frame(raw: np.ndarray, actions: np.ndarray) -> tuple[np.ndarray, dict]:
    p=lambda node: raw[:,NODE_INDEX[node]]
    align=np.isin(actions,["initial_still_attempt2","t_pose"])
    central=p("BSF31CC");pelvis=p("BSFC2CC");ankle=.5*(p("BSF6C53")+p("BSF8BC4"))
    v1=central-pelvis;v2=pelvis-ankle
    vv=np.r_[v1[align & np.isfinite(v1).all(1)],v2[align & np.isfinite(v2).all(1)]]
    z=_normalize(np.nanmedian(vv,axis=0),np.array([0,0,1.]))
    root=pelvis.copy();valid=np.isfinite(root).all(1);center=np.nanmedian(root[valid],axis=0)
    horizontal=root[valid]-center;horizontal-=np.outer(horizontal@z,z)
    scatter=horizontal.T@horizontal;values,vectors=np.linalg.eigh(scatter);x=_normalize(vectors[:,np.argmax(values)],np.array([1,0,0.]))
    net=np.zeros(3)
    for action in np.unique(actions):
        idx=np.flatnonzero((actions==action)&valid)
        if len(idx)>1: net+=root[idx[-1]]-root[idx[0]]
    net-=z*float(net@z);sign_metric=float(net@x)
    if abs(sign_metric)<1e-9:
        first=int(np.flatnonzero(np.abs(x)>1e-9)[0]); flip=x[first]<0
    else: flip=sign_metric<0
    if flip:x=-x
    y=_normalize(np.cross(z,x));x=_normalize(np.cross(y,z))
    rotation=np.vstack([x,y,z])
    return rotation,{"rule":"DOMINANT_HORIZONTAL_CALIBRATION_MOVEMENT_TO_POSITIVE_X","eigenvalues":values.tolist(),
        "vertical_source":"ANKLE_MIDPOINT_TO_PELVIS_TO_CENTRAL_INITIAL_STILL_AND_T_POSE","sign_metric":sign_metric,
        "compass_heading_claimed":False,"R_display_from_V4":rotation.tolist()}


def _align_segment_axes(raw_d, rotations, actions):
    align=np.isin(actions,["initial_still_attempt2","t_pose"]); axes={}; frames={}; audit={}
    basis=np.eye(3)
    for name,(sensor,proximal,distal) in SEGMENT_SPECS.items():
        sj=NODE_INDEX[sensor];pi=NODE_INDEX[proximal];di=NODE_INDEX[distal]
        target=raw_d[:,di]-raw_d[:,pi];valid=align&np.isfinite(target).all(1)&np.isfinite(rotations[:,sj]).all((1,2))
        target=np.asarray([_normalize(v) for v in target[valid]]);rq=rotations[valid,sj]
        candidates=[]
        for axis_index in range(3):
            for sign in (-1.,1.):
                local=basis[axis_index]*sign;source=np.einsum("nij,j->ni",rq,local)
                rot,rssd=Rotation.align_vectors(target,source);pred=rot.apply(source)
                error=float(np.median(np.arccos(np.clip(np.sum(pred*target,axis=1),-1,1))))
                candidates.append((error,axis_index,sign,rot.as_matrix(),local))
        error,axis_index,sign,alignment,local=min(candidates,key=lambda x:(x[0],x[1],x[2]))
        full=np.einsum("ij,njk->nik",alignment,rotations[:,sj]);axis=np.einsum("nij,j->ni",full,local)
        axes[name]=axis;frames[sensor]=full
        audit[name]={"sensor_node":sensor,"selected_board_axis":"xyz"[axis_index],"selected_sign":int(sign),
            "median_alignment_residual_rad":error,"alignment_samples":int(valid.sum()),"axial_twist_dof":False}
    # Every node has a display-frame orientation for placement offsets.  For a
    # node used by one segment, use that segment's fitted Q1-to-display binding.
    for name,(sensor,_,_) in SEGMENT_SPECS.items():
        if sensor in frames: continue
    return axes,frames,audit


def _smooth_axis(values, actions, alpha):
    out=np.empty_like(values);previous=None;last_action=None
    for i,(v,a) in enumerate(zip(values,actions)):
        if not np.isfinite(v).all():v=previous if previous is not None else np.array([0.,0.,1.])
        v=_normalize(v)
        if previous is None or a!=last_action: current=v
        else: current=_normalize((1-alpha)*previous+alpha*v,previous)
        out[i]=current;previous=current;last_action=a
    return out


def _blend_axis(imu, raw_vector, covariance, actions, gates):
    e=gates["estimator"];wimu=float(e["imu_axis_primary_weight"]);floor=float(e["uwb_radial_sigma_floor_m"])
    output=np.empty_like(imu); weights=np.zeros(len(imu)); normalized=np.full(len(imu),np.nan)
    for i in range(len(imu)):
        iv=_normalize(imu[i]);rv=raw_vector[i]
        if not np.isfinite(rv).all() or np.linalg.norm(rv)<1e-8:output[i]=iv;continue
        rv=_normalize(rv);sigma=floor
        if np.isfinite(covariance[i]).all():sigma=max(floor,float(np.sqrt(max(0,np.trace(covariance[i])/3))))
        angle=float(np.arccos(np.clip(iv@rv,-1,1)));z=angle/max(.05,sigma)
        robust=1.0 if z<=e["uwb_huber_delta_sigma"] else float(e["uwb_huber_delta_sigma"])/z
        w=(1-wimu)*robust;output[i]=_normalize(wimu*iv+w*rv,iv);weights[i]=w;normalized[i]=z
    return _smooth_axis(output,actions,float(e["axis_temporal_smoothing_alpha"])),weights,normalized


def _root_filter(pelvis, actions, alpha_p, alpha_v, hz):
    out=np.empty_like(pelvis);vel=np.zeros_like(pelvis);p=None;v=np.zeros(3);last=None
    for i,(obs,a) in enumerate(zip(pelvis,actions)):
        if p is None or a!=last:
            p=obs.copy() if np.isfinite(obs).all() else np.zeros(3);v=np.zeros(3)
        else:
            pred=p+v/hz
            if np.isfinite(obs).all():
                innovation=obs-pred;p=pred+alpha_p*innovation;v=(1-alpha_v)*v+alpha_v*innovation*hz
            else:p=pred
        out[i]=p;vel[i]=v;last=a
    return out,vel


def _construct(template, raw, cov, rotations, actions, axes, gates):
    d=template["dimensions"]; e=gates["estimator"]
    skeleton=np.zeros((len(actions),len(LANDMARKS),3)); axis_audit={}
    pelvis_raw=raw[:,NODE_INDEX["BSFC2CC"]]
    root,velocity=_root_filter(pelvis_raw,actions,float(e["root_position_smoothing_alpha"]),float(e["root_velocity_smoothing_alpha"]),float(e["timeline_hz"]))
    def blended(name,prox,dist):
        rv=raw[:,NODE_INDEX[dist]]-raw[:,NODE_INDEX[prox]];cv=cov[:,NODE_INDEX[dist]]+cov[:,NODE_INDEX[prox]]
        ax,w,z=_blend_axis(axes[name],rv,cv,actions,gates);axis_audit[name]={"uwb_soft_weight_median":float(np.median(w)),"imu_primary_weight":float(e["imu_axis_primary_weight"]),"axis_residual_rad_median":float(np.nanmedian(z)*.05)};return ax
    torso=blended("torso","BSFC2CC","BSF31CC")
    ua_l=blended("upper_arm_L","BSF31CC","BSFAA61");ua_r=blended("upper_arm_R","BSF31CC","BSF1120")
    fa_l=blended("forearm_L","BSFAA61","BSFB165");fa_r=blended("forearm_R","BSF1120","BSFEC35")
    th_l=blended("thigh_L","BSFC2CC","BSF44AD");th_r=blended("thigh_R","BSFC2CC","BSF3C79")
    sh_l=blended("shank_L","BSF44AD","BSF6C53");sh_r=blended("shank_R","BSF3C79","BSF8BC4")
    # Torso lateral has a deterministic gauge but no separately optimized axial-twist state.
    lateral=np.empty_like(torso);prev=np.array([1.,0,0])
    for i,t in enumerate(torso):
        candidate=np.array([1.,0,0])-t*float(t@np.array([1.,0,0]))
        lateral[i]=_normalize(candidate,prev);prev=lateral[i]
    s=skeleton;idx=LANDMARK_INDEX
    s[:,idx["Pelvis"]]=root;s[:,idx["C7Proxy"]]=root+float(d["C7Proxy_to_PelvisProxy_m"])*torso
    s[:,idx["Shoulder_L"]]=s[:,idx["C7Proxy"]]-.5*float(d["graphical_shoulder_width_m"])*lateral
    s[:,idx["Shoulder_R"]]=s[:,idx["C7Proxy"]]+.5*float(d["graphical_shoulder_width_m"])*lateral
    s[:,idx["Elbow_L"]]=s[:,idx["Shoulder_L"]]+float(d["rendering_upper_arm_length_L_m"])*ua_l
    s[:,idx["Elbow_R"]]=s[:,idx["Shoulder_R"]]+float(d["rendering_upper_arm_length_R_m"])*ua_r
    s[:,idx["Wrist_L"]]=s[:,idx["Elbow_L"]]+float(d["rendering_forearm_length_L_m"])*fa_l
    s[:,idx["Wrist_R"]]=s[:,idx["Elbow_R"]]+float(d["rendering_forearm_length_R_m"])*fa_r
    s[:,idx["Hip_L"]]=root-.5*float(d["graphical_hip_width_m"])*lateral;s[:,idx["Hip_R"]]=root+.5*float(d["graphical_hip_width_m"])*lateral
    s[:,idx["Knee_L"]]=s[:,idx["Hip_L"]]+float(d["rendering_thigh_length_L_m"])*th_l
    s[:,idx["Knee_R"]]=s[:,idx["Hip_R"]]+float(d["rendering_thigh_length_R_m"])*th_r
    s[:,idx["Ankle_L"]]=s[:,idx["Knee_L"]]+float(d["rendering_shank_length_L_m"])*sh_l
    s[:,idx["Ankle_R"]]=s[:,idx["Knee_R"]]+float(d["rendering_shank_length_R_m"])*sh_r
    return skeleton,velocity,axis_audit


def _fit_placements(raw, rotations, skeleton, gates):
    b=float(gates["estimator"]["placement_component_bound_m"]);offsets=np.zeros((len(NODE_ORDER),3));audit={}
    for j,node in enumerate(NODE_ORDER):
        landmark=skeleton[:,LANDMARK_INDEX[NODE_LANDMARK[node]]];valid=np.isfinite(raw[:,j]).all(1)&np.isfinite(rotations[:,j]).all((1,2))
        if not valid.any():audit[node]={"status":"NO_FACTORS","bound_hit":False};continue
        def residual(r):return (raw[valid,j]-(landmark[valid]-np.einsum("nij,j->ni",rotations[valid,j],r))).ravel()
        fit=least_squares(residual,np.zeros(3),bounds=(-b,b),loss=gates["estimator"]["placement_robust_loss"],
            f_scale=float(gates["estimator"]["placement_robust_f_scale_m"]),max_nfev=int(gates["estimator"]["placement_max_function_evaluations"]))
        offsets[j]=fit.x;hit=bool(np.any(np.minimum(fit.x+b,b-fit.x)<=1e-5))
        audit[node]={"offset_sensor_to_antenna_m":fit.x.tolist(),"bound_m":[-b,b],"bound_hit":hit,
            "bound_hit_disclosed":hit,"factor_count":int(valid.sum()),"cost":float(fit.cost),"success":bool(fit.success)}
    return offsets,audit


def _residual_audit(raw,cov,rotations,skeleton,offsets,gates,times,actions):
    per={};downweighted=[];rejected=[];mask=np.zeros((len(times),len(NODE_ORDER)),dtype=np.uint8);e=gates["estimator"]
    for j,node in enumerate(NODE_ORDER):
        landmark=skeleton[:,LANDMARK_INDEX[NODE_LANDMARK[node]]];prediction=landmark-np.einsum("nij,j->ni",rotations[:,j],offsets[j])
        residual=np.linalg.norm(raw[:,j]-prediction,axis=1);sigma=np.full(len(times),float(e["uwb_radial_sigma_floor_m"]))
        finite=np.isfinite(cov[:,j]).all((1,2));sigma[finite]=np.maximum(sigma[finite],np.sqrt(np.maximum(0,np.trace(cov[finite,j],axis1=1,axis2=2)/3)))
        z=residual/sigma;valid=np.isfinite(residual);strong=valid&(z>float(e["uwb_huber_delta_sigma"]));reject=valid&(z>float(e["uwb_reject_threshold_sigma"]));mask[strong,j]=1;mask[reject,j]=2
        for i in np.flatnonzero(strong):
            row={"time_ns":int(times[i]),"action":str(actions[i]),"node":node,"residual_m":float(residual[i]),"normalized_residual":float(z[i]),
                 "classification":"REJECTED" if reject[i] else "STRONGLY_DOWNWEIGHTED"}
            (rejected if reject[i] else downweighted).append(row)
        values=residual[valid]
        per[node]={"factor_count":int(valid.sum()),"median_m":float(np.median(values)),"p95_m":float(np.percentile(values,95)),"max_m":float(np.max(values)),
            "strongly_downweighted_count":int((strong&~reject).sum()),"rejected_count":int(reject.sum())}
    return per,downweighted,rejected,mask


def _length_errors(skeleton,template):
    d=template["dimensions"]
    expected={
        "torso":("Pelvis","C7Proxy",d["C7Proxy_to_PelvisProxy_m"]),"shoulder_width":("Shoulder_L","Shoulder_R",d["graphical_shoulder_width_m"]),
        "hip_width":("Hip_L","Hip_R",d["graphical_hip_width_m"]),"upper_arm_L":("Shoulder_L","Elbow_L",d["rendering_upper_arm_length_L_m"]),
        "upper_arm_R":("Shoulder_R","Elbow_R",d["rendering_upper_arm_length_R_m"]),"forearm_L":("Elbow_L","Wrist_L",d["rendering_forearm_length_L_m"]),
        "forearm_R":("Elbow_R","Wrist_R",d["rendering_forearm_length_R_m"]),"thigh_L":("Hip_L","Knee_L",d["rendering_thigh_length_L_m"]),
        "thigh_R":("Hip_R","Knee_R",d["rendering_thigh_length_R_m"]),"shank_L":("Knee_L","Ankle_L",d["rendering_shank_length_L_m"]),
        "shank_R":("Knee_R","Ankle_R",d["rendering_shank_length_R_m"])}
    out={};maximum=0.
    for name,(a,b,value) in expected.items():
        actual=np.linalg.norm(skeleton[:,LANDMARK_INDEX[b]]-skeleton[:,LANDMARK_INDEX[a]],axis=1);error=np.max(np.abs(actual-float(value)));maximum=max(maximum,float(error));out[name]={"expected_m":float(value),"maximum_absolute_error_m":float(error)}
    return out,maximum


def run_analysis(calibration_ledger: Path,layout: Path,template_path: Path,gates_path: Path,output: Path) -> dict:
    gates=json.loads(gates_path.read_text());template=json.loads(template_path.read_text())
    if gates["operator_measurements"]!="SEALED_AND_FORBIDDEN" or gates["heldout"]!={"walk":"SEALED","final_still":"SEALED"}:raise ValueError("payload firewall is not sealed")
    if any(x in str(calibration_ledger).lower() for x in ("heldout","walk","final_still","raw")) or calibration_ledger.name!="CALIBRATION_TYPED_LEDGER.npz":raise ValueError("only calibration typed ledger is accepted")
    if sha256(layout)!=gates["frontends"]["geometry_sha256"]:raise ValueError("canonical geometry SHA mismatch")
    if output.exists():raise ValueError("output must not exist")
    output.mkdir(parents=True)
    with np.load(calibration_ledger,allow_pickle=False) as ledger:
        windows=_windows(ledger,gates);observations,t4_accounting,t4_rejections=_solve_t4(ledger,layout);q1,q1_audits=_q1(ledger,windows)
        times,actions,action_time=_build_timeline(windows,float(gates["estimator"]["timeline_hz"]));raw,cov,rotq,uwb_gap,q1_gap=_sample_frontends(times,observations,q1,gates)
    display,frame_audit=deterministic_display_frame(raw,actions);origin=np.nanmedian(raw[:,NODE_INDEX["BSFC2CC"]],axis=0)
    raw_d=np.einsum("ij,ntj->nti",display,raw-origin);rot_d=np.einsum("ij,ntjk->ntik",display,rotq)
    axes,segment_frames,alignment_audit=_align_segment_axes(raw_d,rot_d,actions)
    # Fill node frame bindings from the segment using that sensor node.
    node_frames=np.full_like(rot_d,np.nan)
    for segment,(node,_,_) in SEGMENT_SPECS.items():node_frames[:,NODE_INDEX[node]]=segment_frames[node]
    skeleton0,_,_=_construct(template,raw_d,cov,rot_d,actions,axes,gates)
    offsets,placement_audit=_fit_placements(raw_d,node_frames,skeleton0,gates)
    corrected=raw_d.copy()
    for j in range(len(NODE_ORDER)):corrected[:,j]=raw_d[:,j]+np.einsum("nij,j->ni",node_frames[:,j],offsets[j])
    axes2,segment_frames2,_=_align_segment_axes(corrected,rot_d,actions)
    node_frames2=np.full_like(rot_d,np.nan)
    for segment,(node,_,_) in SEGMENT_SPECS.items():node_frames2[:,NODE_INDEX[node]]=segment_frames2[node]
    skeleton,velocity,axis_audit=_construct(template,corrected,cov,rot_d,actions,axes2,gates)
    residuals,downweighted,rejected,rejection_mask=_residual_audit(raw_d,cov,node_frames2,skeleton,offsets,gates,times,actions)
    length_detail,max_length_error=_length_errors(skeleton,template);finite=bool(np.isfinite(skeleton).all() and np.isfinite(velocity).all())
    topology_connected=set(sum(([a,b] for a,b in EDGES),[]))==set(LANDMARKS);identity_fixed=True
    undisclosed=[n for n,v in placement_audit.items() if v.get("bound_hit") and not v.get("bound_hit_disclosed")]
    failures=[];pg=gates["preview_gates"]
    if max_length_error>float(pg["maximum_generic_length_change_m"]):failures.append("GENERIC_LENGTH_CHANGED")
    if not identity_fixed:failures.append("LEFT_RIGHT_IDENTITY_SWAP")
    if not topology_connected:failures.append("TOPOLOGY_DISCONNECTED")
    if not finite:failures.append("NONFINITE_STATE")
    if undisclosed:failures.append("UNDISCLOSED_PLACEMENT_BOUND_HIT")
    report={"schema":"biospur-generic-template-motion-demo-analysis-v1","verdict":"GENERIC_TEMPLATE_MOTION_DEMO_CALIBRATION_PASS" if not failures else "GENERIC_TEMPLATE_MOTION_DEMO_CALIBRATION_FAIL","failures":failures,
        "product":"GENERIC_TEMPLATE_MOTION_DEMO_V1","claims":"MOTION_DEMONSTRATION_ONLY","disclaimer":DISCLAIMER,
        "template":{"name":template["template_name"],"sha256":sha256(template_path),"dimensions":template["dimensions"],"dimensions_immutable":True,"capture_derived_geometry_values_used":False},
        "firewall":{"operator_measurements":"SEALED_NOT_READ","walk":"SEALED_NOT_OPENED","final_still":"SEALED_NOT_OPENED","calibration_ledger_open_count":1},
        "inputs":{"calibration_ledger_sha256":sha256(calibration_ledger),"layout_sha256":sha256(layout),"gates_sha256":sha256(gates_path)},
        "frontends":{"position":"UWB_TAG_T4","attitude":"Q1_ATTITUDE_ONLY","t4_accounting":t4_accounting,"canonical_t4_rejection_count":len(t4_rejections),"q1_audits":q1_audits},
        "display_frame":frame_audit,"sensor_axis_alignment":alignment_audit,"imu_axis_residuals":axis_audit,"placement_nuisance":placement_audit,
        "uwb_residuals_per_node":residuals,"uwb_downweight_accounting":{"strongly_downweighted":len(downweighted),"rejected":len(rejected),"all_events_logged":True},
        "fixed_length_audit":{"maximum_absolute_error_m":max_length_error,"per_edge":length_detail},
        "topology":{"connected":topology_connected,"edges":[list(x) for x in EDGES],"left_right_identity_fixed":identity_fixed,"axial_segment_twist_dof":False},
        "state":{"finite":finite,"frame_count":len(times),"pelvis_root_translation_and_velocity":True},
        "resampling":{"analysis_timeline_hz":float(gates["estimator"]["timeline_hz"]),"nearest_uwb_max_gap_ns":int(gates["estimator"]["maximum_uwb_match_gap_ns"]),"nearest_q1_max_gap_ns":int(gates["estimator"]["maximum_q1_match_gap_ns"]),"render_interpolation_logged_separately":True}}
    dump_json(output/"DEMO_DIAGNOSTICS.json",report);dump_json(output/"UWB_DOWNWEIGHT_LOG.json",{"strongly_downweighted":downweighted,"rejected":rejected})
    np.save(output/"TIME_NS.npy",times,allow_pickle=False);np.save(output/"ACTION_ID.npy",np.asarray([gates["calibration_actions"].index(str(a)) for a in actions],dtype=np.uint8),allow_pickle=False)
    np.save(output/"ACTION_TIME_S.npy",action_time,allow_pickle=False);np.save(output/"SKELETON_M.npy",skeleton,allow_pickle=False);np.save(output/"RAW_UWB_M.npy",raw_d,allow_pickle=False);np.save(output/"UWB_REJECTION_MASK.npy",rejection_mask,allow_pickle=False);np.save(output/"ROOT_VELOCITY_MPS.npy",velocity,allow_pickle=False)
    bound_hits=[node for node,value in placement_audit.items() if value.get("bound_hit")]
    (output/"REPORT.md").write_text("\n".join([
        "# Generic template motion demo V1 — calibration analysis","",f"**{report['verdict']}**","",
        "This is a motion-only, non-clinical generic-template product. It does not use the failed capture-derived dimensions or operator measurements.","",
        f"- Template: `{template['template_name']}` (`{sha256(template_path)}`)",
        f"- Frames: {len(times)} at {gates['estimator']['timeline_hz']:.1f} Hz across calibration actions only",
        f"- Maximum hard length error: {max_length_error:.3e} m",
        f"- Disclosed placement bound hits: {', '.join(bound_hits) if bound_hits else 'none'}",
        f"- Strongly downweighted UWB observations: {len(downweighted)}",
        f"- Rejected UWB observations: {len(rejected)}",
        "- Operator measurements: `SEALED_NOT_READ`",
        "- Walk/final-still: `SEALED_NOT_OPENED`","",
        DISCLAIMER,"","Full per-node residuals, axis residuals, offsets, bounds and frontend accounting are in `DEMO_DIAGNOSTICS.json`.",""]),encoding="utf-8")
    (output/"WALK_RELEASE_REQUEST.md").write_text("\n".join([
        "# Walk release request","",
        "The calibration-only generic-template preview must be reviewed and explicitly accepted before any walk payload is opened.","",
        "Acceptance will make exactly this one-way transition:","","```text","WALK_HELDOUT_STATUS:","  SEALED -> CONSUMED_FOR_VISUALIZATION","```","",
        "`final_still` will remain sealed. After preview acceptance, no template dimension, estimator parameter, gate, covariance, robust-loss threshold, rejection rule, alignment rule, camera rule or rendering rule may change.","",
        "This request does not itself authorize or open walk data.",""]),encoding="utf-8")
    hashes=[]
    for path in sorted(output.iterdir()):
        if path.name!="SHA256SUMS":hashes.append(f"{sha256(path)}  {path.name}")
    (output/"SHA256SUMS").write_text("\n".join(hashes)+"\n",encoding="utf-8")
    return report


def render_preview(analysis_dir: Path,gates_path: Path,output_mp4: Path,output_gif: Path) -> dict:
    gates=json.loads(gates_path.read_text());actions_all=gates["calibration_actions"];preview=set(gates["preview_actions"])
    skeleton=np.load(analysis_dir/"SKELETON_M.npy",allow_pickle=False);raw=np.load(analysis_dir/"RAW_UWB_M.npy",allow_pickle=False);mask=np.load(analysis_dir/"UWB_REJECTION_MASK.npy",allow_pickle=False)
    aid=np.load(analysis_dir/"ACTION_ID.npy",allow_pickle=False);at=np.load(analysis_dir/"ACTION_TIME_S.npy",allow_pickle=False)
    selected=np.asarray([actions_all[int(x)] in preview for x in aid]);indices=np.flatnonzero(selected)
    if not len(indices):raise ValueError("no authorized calibration preview frames")
    finite=np.r_[skeleton[indices].reshape(-1,3),raw[indices].reshape(-1,3)];finite=finite[np.isfinite(finite).all(1)]
    center=np.median(skeleton[indices,LANDMARK_INDEX["Pelvis"]],axis=0);radius=max(1.2,float(np.percentile(np.linalg.norm(finite-center,axis=1),99))*1.1)
    limits={axis:[float(center[i]-radius),float(center[i]+radius)] for i,axis in enumerate("xyz")}
    import matplotlib;matplotlib.use("Agg");import matplotlib.pyplot as plt
    from matplotlib.animation import FFMpegWriter,PillowWriter
    r=gates["rendering"];dpi=100;fig=plt.figure(figsize=(int(r["width_px"])/dpi,int(r["height_px"])/dpi),dpi=dpi);ax=fig.add_subplot(111,projection="3d")
    ax.set_xlim(limits["x"]);ax.set_ylim(limits["y"]);ax.set_zlim(limits["z"]);ax.set_box_aspect((1,1,1));ax.view_init(elev=float(r["fixed_camera"]["elevation_deg"]),azim=float(r["fixed_camera"]["azimuth_deg"]));ax.set_xlabel("display +X: dominant motion (m)");ax.set_ylabel("display Y (m)");ax.set_zlabel("display vertical proxy (m)")
    watermark=fig.text(.5,.012,DISCLAIMER,ha="center",va="bottom",fontsize=9,color="crimson",bbox={"facecolor":"white","alpha":.85,"edgecolor":"crimson"})
    artists=[]
    def draw(i):
        nonlocal artists
        for a in artists:a.remove()
        artists=[];sk=skeleton[i]
        for a,b in EDGES:
            values=sk[[LANDMARK_INDEX[a],LANDMARK_INDEX[b]]];line,=ax.plot(values[:,0],values[:,1],values[:,2],color="navy",linewidth=3);artists.append(line)
        dots=ax.scatter(sk[:,0],sk[:,1],sk[:,2],s=22,color="darkorange");artists.append(dots)
        valid=np.isfinite(raw[i]).all(1);normal=valid&(mask[i]<2);reject=valid&(mask[i]>=2)
        if normal.any():artists.append(ax.scatter(raw[i,normal,0],raw[i,normal,1],raw[i,normal,2],s=14,color="gray",alpha=float(r["raw_uwb_alpha"])))
        if reject.any():artists.append(ax.scatter(raw[i,reject,0],raw[i,reject,1],raw[i,reject,2],s=28,color="red",marker="x"))
        action=actions_all[int(aid[i])];ax.set_title(f"GENERIC_TEMPLATE_MOTION_DEMO_V1 — {action} — {at[i]:.1f}s")
    output_mp4.parent.mkdir(parents=True,exist_ok=True);writer=FFMpegWriter(fps=int(r["fps"]),codec=r["mp4_codec"],extra_args=["-pix_fmt",r["pixel_format"],"-metadata","creation_time=1970-01-01T00:00:00Z"])
    with writer.saving(fig,str(output_mp4),dpi):
        for i in indices:draw(int(i));writer.grab_frame()
    gif_indices=indices[::max(1,int(round(float(r["display_time_scale"]))))]
    gif=PillowWriter(fps=int(r["gif_fps"]));
    with gif.saving(fig,str(output_gif),dpi=60):
        for i in gif_indices:draw(int(i));gif.grab_frame()
    plt.close(fig)
    manifest={"schema":"biospur-generic-template-motion-demo-render-v1","mp4":str(output_mp4.resolve()),"gif":str(output_gif.resolve()),"source_frame_count":int(len(indices)),"gif_frame_count":int(len(gif_indices)),"mp4_fps":int(r["fps"]),"gif_fps":int(r["gif_fps"]),"display_time_scale":float(r["display_time_scale"]),"fixed_axes_m":limits,"fixed_camera":r["fixed_camera"],"equal_axis_scaling":True,"raw_uwb_dots":True,"rejected_uwb_red":True,"render_interpolation":{"used":False,"unlogged":False,"analysis_use":"FORBIDDEN"},"watermark_every_frame":DISCLAIMER,"walk":"SEALED_NOT_OPENED","final_still":"SEALED_NOT_OPENED"}
    dump_json(output_mp4.with_suffix(".manifest.json"),manifest);return manifest
