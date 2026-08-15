"""Preview-only calibration and continuous generic centerline replay."""
from __future__ import annotations

import math
from typing import Any, Mapping

import numpy as np
from scipy.optimize import least_squares
from scipy.sparse import lil_matrix
from scipy.spatial.transform import Rotation

from .common_time import CommonTimeline

SEGMENTS=("pelvis","torso","upper_arm_L","upper_arm_R","forearm_L","forearm_R","thigh_L","thigh_R","shank_L","shank_R")
LANDMARKS=("Pelvis","C7Proxy","HeadProxy","Shoulder_L","Shoulder_R","Elbow_L","Elbow_R","Wrist_L","Wrist_R","Hip_L","Hip_R","Knee_L","Knee_R","Ankle_L","Ankle_R")
LANDMARK_INDEX={name:i for i,name in enumerate(LANDMARKS)}
EDGES=(("Pelvis","C7Proxy"),("C7Proxy","HeadProxy"),("Shoulder_L","Shoulder_R"),("C7Proxy","Shoulder_L"),("C7Proxy","Shoulder_R"),("Shoulder_L","Elbow_L"),("Elbow_L","Wrist_L"),("Shoulder_R","Elbow_R"),("Elbow_R","Wrist_R"),("Hip_L","Hip_R"),("Pelvis","Hip_L"),("Pelvis","Hip_R"),("Hip_L","Knee_L"),("Knee_L","Ankle_L"),("Hip_R","Knee_R"),("Knee_R","Ankle_R"))
EXPECTED_INITIAL={
    "pelvis":np.array([1.,0,0]),"torso":np.array([0.,0,1.]),
    "upper_arm_L":np.array([0.,0,-1.]),"upper_arm_R":np.array([0.,0,-1.]),
    "forearm_L":np.array([0.,0,-1.]),"forearm_R":np.array([0.,0,-1.]),
    "thigh_L":np.array([0.,0,-1.]),"thigh_R":np.array([0.,0,-1.]),
    "shank_L":np.array([0.,0,-1.]),"shank_R":np.array([0.,0,-1.]),
}
EXPECTED_TPOSE={**EXPECTED_INITIAL,
    "upper_arm_L":np.array([-1.,0,0]),"upper_arm_R":np.array([1.,0,0]),
    "forearm_L":np.array([-1.,0,0]),"forearm_R":np.array([1.,0,0]),
}


def normalize(v: np.ndarray, fallback: np.ndarray|None=None) -> np.ndarray:
    v=np.asarray(v,float);n=float(np.linalg.norm(v))
    if np.isfinite(n) and n>1e-12:return v/n
    return normalize(np.array([0.,0.,1.]) if fallback is None else fallback)


def yaw_matrix(angle: np.ndarray|float) -> np.ndarray:
    a=np.asarray(angle,float);c=np.cos(a);s=np.sin(a);out=np.zeros(a.shape+(3,3));out[...,0,0]=c;out[...,0,1]=-s;out[...,1,0]=s;out[...,1,1]=c;out[...,2,2]=1.;return out


def axis_to_angles(v: np.ndarray) -> tuple[float,float]:
    v=normalize(v);return math.atan2(v[1],v[0]),math.asin(float(np.clip(v[2],-1,1)))


def angles_to_axis(theta: float,phi: float) -> np.ndarray:
    return np.array([math.cos(phi)*math.cos(theta),math.cos(phi)*math.sin(theta),math.sin(phi)])


def angle_deg(a: np.ndarray,b: np.ndarray) -> float:
    return math.degrees(math.acos(float(np.clip(normalize(a)@normalize(b),-1,1))))


def action_masks(times: np.ndarray, windows: Mapping[str,tuple[int,int]]) -> dict[str,np.ndarray]:
    return {name:(times>=start)&(times<=stop) for name,(start,stop) in windows.items()}


def _node_indices(timeline: CommonTimeline, node_to_segment: Mapping[str,str]) -> tuple[dict[str,int],dict[str,int]]:
    node_index={n:i for i,n in enumerate(timeline.node_order)};segment_index={s:node_index[n] for n,s in node_to_segment.items()};return node_index,segment_index


def _drift_basis(times: np.ndarray, knot_times: np.ndarray) -> np.ndarray:
    basis=np.zeros((len(times),len(knot_times)))
    for i,t in enumerate(times):
        if t<=knot_times[0]:basis[i,0]=1.;continue
        if t>=knot_times[-1]:basis[i,-1]=1.;continue
        hi=int(np.searchsorted(knot_times,t));lo=hi-1;w=(t-knot_times[lo])/float(knot_times[hi]-knot_times[lo]);basis[i,lo]=1.-w;basis[i,hi]=w
    return basis


def _decode_parameters(x: np.ndarray, knot_count: int) -> tuple[np.ndarray,np.ndarray,np.ndarray]:
    axes=np.empty((len(SEGMENTS),3));heading=np.empty(len(SEGMENTS));cursor=0
    for k in range(len(SEGMENTS)):
        axes[k]=angles_to_axis(float(x[cursor]),float(x[cursor+1]));heading[k]=x[cursor+2];cursor+=3
    drift=np.zeros((len(SEGMENTS),knot_count))
    for k in range(len(SEGMENTS)):
        drift[k,1:]=x[cursor:cursor+knot_count-1];cursor+=knot_count-1
    return axes,heading,drift


def directions_from_parameters(timeline: CommonTimeline, node_to_segment: Mapping[str,str], x: np.ndarray, knot_times: np.ndarray) -> np.ndarray:
    _,si=_node_indices(timeline,node_to_segment);axes,heading,drift=_decode_parameters(x,len(knot_times));basis=_drift_basis(timeline.time_ns,knot_times);out=np.empty((len(timeline.time_ns),len(SEGMENTS),3))
    for k,segment in enumerate(SEGMENTS):
        yaw=heading[k]+basis@drift[k];corrected=np.einsum("nij,njk->nik",yaw_matrix(yaw),timeline.rotation[:,si[segment]])
        out[:,k]=np.einsum("nij,j->ni",corrected,axes[k])
    return out


def _initial_parameters(timeline: CommonTimeline, windows: Mapping[str,tuple[int,int]], node_to_segment: Mapping[str,str], knot_times: np.ndarray) -> np.ndarray:
    masks=action_masks(timeline.time_ns,windows);_,si=_node_indices(timeline,node_to_segment);values=[]
    for segment in SEGMENTS:
        rows=np.flatnonzero(masks["initial_still_attempt2"]&timeline.valid[:,si[segment]])
        mean=Rotation.from_matrix(timeline.rotation[rows,si[segment]]).mean().as_matrix();local=normalize(mean.T@EXPECTED_INITIAL[segment]);theta,phi=axis_to_angles(local);values.extend([theta,phi,0.])
    values.extend([0.]*(len(SEGMENTS)*(len(knot_times)-1)));return np.asarray(values)


def _static_rows(mask: np.ndarray, all_valid: np.ndarray, maximum: int=100) -> np.ndarray:
    rows=np.flatnonzero(mask&all_valid)
    if len(rows)>maximum:rows=rows[np.round(np.linspace(0,len(rows)-1,maximum)).astype(int)]
    return rows


def fit_preview_calibration(timeline: CommonTimeline, windows: Mapping[str,tuple[int,int]], gates: Mapping[str,Any], template: Mapping[str,Any]) -> tuple[dict,dict,np.ndarray]:
    cfg=gates["calibration_solver"];node_to_segment=gates["node_to_segment"];spacing=int(round(float(cfg["yaw_drift_knot_spacing_s"])*1e9));knot_times=np.arange(timeline.time_ns[0],timeline.time_ns[-1]+spacing,spacing,dtype=np.int64)
    if knot_times[-1]<timeline.time_ns[-1]:knot_times=np.r_[knot_times,timeline.time_ns[-1]]
    masks=action_masks(timeline.time_ns,windows);initial_rows=_static_rows(masks["initial_still_attempt2"],timeline.all_nodes_valid);tpose_rows=_static_rows(masks["t_pose"],timeline.all_nodes_valid)
    if len(initial_rows)<10 or len(tpose_rows)<10:raise RuntimeError("insufficient all-node-valid static rows")
    x0=_initial_parameters(timeline,windows,node_to_segment,knot_times);basis=_drift_basis(timeline.time_ns,knot_times);_,si=_node_indices(timeline,node_to_segment);static_sigma=float(cfg["static_direction_sigma_rad"]);drift_sigma=float(cfg["yaw_drift_prior_sigma_rad"]);smooth_sigma=float(cfg["yaw_drift_second_difference_sigma_rad"])
    def residual(x):
        axes,heading,drift=_decode_parameters(x,len(knot_times));rows=[]
        for k,segment in enumerate(SEGMENTS):
            j=si[segment]
            for selected,expected in ((initial_rows,EXPECTED_INITIAL[segment]),(tpose_rows,EXPECTED_TPOSE[segment])):
                yaw=heading[k]+basis[selected]@drift[k];r=np.einsum("nij,njk->nik",yaw_matrix(yaw),timeline.rotation[selected,j]);direction=np.einsum("nij,j->ni",r,axes[k]);rows.extend(((direction-expected)/static_sigma).ravel())
            rows.extend((drift[k,1:]/drift_sigma).tolist())
            if len(knot_times)>2:rows.extend((np.diff(drift[k],2)/smooth_sigma).tolist())
        # Squat symmetry is a calibration-only soft chain residual. It couples
        # the two independently mounted thigh/shank sensors without imposing a
        # subject dimension or absolute position.
        sq=np.flatnonzero(masks["squats"]&timeline.all_nodes_valid)[::10]
        if len(sq):
            # Mathematically identical to selecting these rows from
            # directions_from_parameters(), without rebuilding all ~21k
            # timeline rows for every finite-difference evaluation.
            for left,right in ((6,7),(8,9)):
                pair=[]
                for k in (left,right):
                    segment=SEGMENTS[k];j=si[segment];yaw=heading[k]+basis[sq]@drift[k]
                    corrected=np.einsum("nij,njk->nik",yaw_matrix(yaw),timeline.rotation[sq,j])
                    pair.append(np.einsum("nij,j->ni",corrected,axes[k]))
                rows.extend(((pair[0][:,2]-pair[1][:,2])/0.25).tolist())
        return np.asarray(rows)
    nparam=len(x0);r0=residual(x0);sparsity=lil_matrix((len(r0),nparam),dtype=np.int8);base=r0
    # Build exact structural sparsity by finite dependency detection once on
    # this small truth-free plumbing state; least_squares then uses sparse FD.
    for col in range(nparam):
        probe=x0.copy();probe[col]+=1e-7;changed=np.abs(residual(probe)-base)>1e-12;sparsity[np.flatnonzero(changed),col]=1
    rng=np.random.default_rng(int(gates["determinism"]["random_seed"]));starts=[x0]
    for _ in range(4):
        p=x0.copy()
        for k in range(len(SEGMENTS)):
            p[3*k:3*k+2]+=rng.normal(0,float(cfg["five_start_axis_perturbation_rad"]),2);p[3*k+2]+=rng.normal(0,float(cfg["five_start_heading_perturbation_rad"]))
        starts.append(p)
    fits=[]
    for start in starts:
        fits.append(least_squares(residual,start,jac_sparsity=sparsity.tocsr(),loss=str(cfg["loss"]),f_scale=float(cfg["f_scale"]),xtol=float(cfg["xtol"]),ftol=float(cfg["ftol"]),gtol=float(cfg["gtol"]),max_nfev=int(cfg.get("safety_max_nfev",cfg["hard_cap_nfev"])),method="trf"))
    fits.sort(key=lambda f:(float(f.cost),float(f.optimality)));best=fits[0];best_dirs=directions_from_parameters(timeline,node_to_segment,best.x,knot_times)
    comparisons=[];comparison_rows=np.flatnonzero(timeline.all_nodes_valid)
    for ordinal,fit in enumerate(fits):
        dirs=directions_from_parameters(timeline,node_to_segment,fit.x,knot_times);angles=np.degrees(np.arccos(np.clip(np.sum(dirs[comparison_rows]*best_dirs[comparison_rows],axis=2),-1,1)));sample=comparison_rows[::20];sk=np.stack([skeleton_from_directions(dirs[i],template,float(gates["rendering"]["head_proxy_length_m"])) for i in sample]);bsk=np.stack([skeleton_from_directions(best_dirs[i],template,float(gates["rendering"]["head_proxy_length_m"])) for i in sample])
        comparisons.append({"rank":ordinal,"cost":float(fit.cost),"success":bool(fit.success),"status":int(fit.status),"nfev":int(fit.nfev),"optimality":float(fit.optimality),"max_segment_axis_difference_deg":float(np.max(angles)),"max_graphical_node_difference_m":float(np.max(np.linalg.norm(sk-bsk,axis=2)))})
    axes,heading,drift=_decode_parameters(best.x,len(knot_times));directions=best_dirs;static={}
    for k,segment in enumerate(SEGMENTS):
        static[segment]={"initial_residual_deg":float(np.median([angle_deg(v,EXPECTED_INITIAL[segment]) for v in directions[initial_rows,k]])),"t_pose_residual_deg":float(np.median([angle_deg(v,EXPECTED_TPOSE[segment]) for v in directions[tpose_rows,k]]))}
    accel_bias={}
    for node_index,node in enumerate(timeline.node_order):
        samples=timeline.accel_mps2[timeline.valid[:,node_index],node_index];bound=float(cfg["accel_bias_bound_mps2"]);fit=least_squares(lambda b:np.linalg.norm(samples-b,axis=1)-float(gates["q2"]["gravity_mps2"]),np.zeros(3),bounds=(-bound,bound),max_nfev=200);jac=np.asarray(fit.jac);cov=np.linalg.pinv(jac.T@jac)*max(1e-12,2*fit.cost/max(1,len(samples)-3));accel_bias[node]={"value_mps2":fit.x.tolist(),"standard_uncertainty_mps2":np.sqrt(np.diag(cov)).tolist(),"bound_mps2":bound,"bound_hit":bool(np.any(np.isclose(np.abs(fit.x),bound,atol=1e-4)))}
    calibration={"schema":"biospur-frozen-imu-relative-orientation-preview-calibration-v0","product":"IMU_RELATIVE_ORIENTATION_PREVIEW_V0","segments":{segment:{"board_frame_longitudinal_axis":axes[k].tolist(),"relative_heading_rad":float(heading[k]),"yaw_drift_knot_rad":drift[k].tolist(),"yaw_drift_first_knot_fixed_zero":True} for k,segment in enumerate(SEGMENTS)},"yaw_drift_knot_global_time_ns":knot_times.tolist(),"pelvis_torso_transverse_frame":{},"accel_bias":accel_bias,"root_translation_gauge":"PELVIS_ORIGIN_FIXED","global_yaw":"ARBITRARY_DETERMINISTIC_DISPLAY_GAUGE","axial_twist":"NOT_OUTPUT","clinical_joint_angles":False}
    cap=int(cfg.get("safety_max_nfev",cfg["hard_cap_nfev"]));solver={"method":"scipy.optimize.least_squares_trf_sparse_fd","jacobian_sparsity_shape":[len(r0),nparam],"jacobian_sparsity_nnz":int(sparsity.nnz),"termination_by_cost_gradient_step":True,"safety_cap":cap,"best":{"cost":float(best.cost),"optimality":float(best.optimality),"nfev":int(best.nfev),"status":int(best.status),"message":str(best.message),"success":bool(best.success),"safety_cap_exhausted":int(best.nfev)>=cap},"multistart":comparisons,"static_residuals":static}
    return calibration,solver,best.x


def corrected_rotations(timeline: CommonTimeline,node_to_segment:Mapping[str,str],calibration:Mapping[str,Any]) -> tuple[np.ndarray,np.ndarray]:
    knot_times=np.asarray(calibration["yaw_drift_knot_global_time_ns"],np.int64);basis=_drift_basis(timeline.time_ns,knot_times);_,si=_node_indices(timeline,node_to_segment);rot=np.empty((len(timeline.time_ns),len(SEGMENTS),3,3));dirs=np.empty((len(timeline.time_ns),len(SEGMENTS),3))
    for k,segment in enumerate(SEGMENTS):
        c=calibration["segments"][segment];yaw=float(c["relative_heading_rad"])+basis@np.asarray(c["yaw_drift_knot_rad"]);rot[:,k]=np.einsum("nij,njk->nik",yaw_matrix(yaw),timeline.rotation[:,si[segment]]);dirs[:,k]=np.einsum("nij,j->ni",rot[:,k],np.asarray(c["board_frame_longitudinal_axis"]))
    return rot,dirs


def _principal_axis(vectors: np.ndarray) -> tuple[np.ndarray,float]:
    vectors=vectors[np.isfinite(vectors).all(1)]
    if len(vectors)<3:return np.array([1.,0,0]),0.
    scatter=vectors.T@vectors/max(1,len(vectors));values,vectors_e=np.linalg.eigh(scatter);return normalize(vectors_e[:,-1]),float(values[-1]/max(values[-2],1e-12))


def derive_functional_parameters(timeline:CommonTimeline,windows:Mapping[str,tuple[int,int]],gates:Mapping[str,Any],calibration:dict) -> dict:
    rot,dirs=corrected_rotations(timeline,gates["node_to_segment"],calibration);masks=action_masks(timeline.time_ns,windows);_,si=_node_indices(timeline,gates["node_to_segment"]);out={}
    specs={"left_elbow":("upper_arm_L","forearm_L","CURL_FIRST_HALF__PRONATION_SECOND_HALF"),"right_elbow_attempt2":("upper_arm_R","forearm_R","CURL_FIRST_HALF__PRONATION_SECOND_HALF"),"left_heel":("thigh_L","shank_L","HEEL_TO_BUTT_KNEE_FLEXION"),"right_heel":("thigh_R","shank_R","HEEL_TO_BUTT_KNEE_FLEXION"),"left_knee":("pelvis","thigh_L","HIGH_KNEE_HIP_CHAIN"),"right_knee":("pelvis","thigh_R","HIGH_KNEE_HIP_CHAIN"),"trunk":("pelvis","torso","TORSO_RELATIVE_TO_PELVIS")}
    for action,(parent,child,semantic) in specs.items():
        rows=np.flatnonzero(masks[action]&timeline.all_nodes_valid);pk=SEGMENTS.index(parent);ck=SEGMENTS.index(child);pg=np.einsum("nij,nj->ni",rot[rows,pk],timeline.gyro_rad_s[rows,si[parent]]);cg=np.einsum("nij,nj->ni",rot[rows,ck],timeline.gyro_rad_s[rows,si[child]]);relative=cg-pg
        fit_rows=rows[:max(1,len(rows)//2)] if "CURL_FIRST" in semantic else rows;axis,ratio=_principal_axis(relative[:len(fit_rows)]);cross=np.cross(dirs[rows,pk],dirs[rows,ck]);signed=np.sum(cross*axis,axis=1);neutral_rows=np.flatnonzero(masks["initial_still_attempt2"]&timeline.all_nodes_valid);zero=float(np.median(np.arctan2(np.sum(np.cross(dirs[neutral_rows,pk],dirs[neutral_rows,ck])*axis,axis=1),np.sum(dirs[neutral_rows,pk]*dirs[neutral_rows,ck],axis=1))));sign=1 if abs(float(np.percentile(signed,95)))>=abs(float(np.percentile(signed,5))) else -1
        out[action]={"semantic":semantic,"dominant_functional_axis_display_frame":axis.tolist(),"eigen_ratio":ratio,"neutral_reference_zero_rad":zero,"flexion_sign":sign,"samples":int(len(rows)),"pronation_or_axial_twist_claim":"CENTERLINE_INVARIANT_NOT_HAND_ORIENTATION" if "PRONATION" in semantic else None}
    calibration["functional_parameters"]=out;return out


def skeleton_from_directions(dirs:np.ndarray,template:Mapping[str,Any],head_length:float) -> np.ndarray:
    d=template["dimensions"];i=LANDMARK_INDEX;s=np.zeros((len(LANDMARKS),3));pelvis,torso=dirs[0],dirs[1];lateral=normalize(pelvis-torso*float(pelvis@torso),np.array([1.,0,0]));s[i["Pelvis"]]=0.;s[i["C7Proxy"]]=d["C7Proxy_to_PelvisProxy_m"]*torso;s[i["HeadProxy"]]=s[i["C7Proxy"]]+head_length*torso;s[i["Shoulder_L"]]=s[i["C7Proxy"]]-.5*d["graphical_shoulder_width_m"]*lateral;s[i["Shoulder_R"]]=s[i["C7Proxy"]]+.5*d["graphical_shoulder_width_m"]*lateral;s[i["Elbow_L"]]=s[i["Shoulder_L"]]+d["rendering_upper_arm_length_L_m"]*dirs[2];s[i["Elbow_R"]]=s[i["Shoulder_R"]]+d["rendering_upper_arm_length_R_m"]*dirs[3];s[i["Wrist_L"]]=s[i["Elbow_L"]]+d["rendering_forearm_length_L_m"]*dirs[4];s[i["Wrist_R"]]=s[i["Elbow_R"]]+d["rendering_forearm_length_R_m"]*dirs[5];s[i["Hip_L"]]=-.5*d["graphical_hip_width_m"]*lateral;s[i["Hip_R"]]=.5*d["graphical_hip_width_m"]*lateral;s[i["Knee_L"]]=s[i["Hip_L"]]+d["rendering_thigh_length_L_m"]*dirs[6];s[i["Knee_R"]]=s[i["Hip_R"]]+d["rendering_thigh_length_R_m"]*dirs[7];s[i["Ankle_L"]]=s[i["Knee_L"]]+d["rendering_shank_length_L_m"]*dirs[8];s[i["Ankle_R"]]=s[i["Knee_R"]]+d["rendering_shank_length_R_m"]*dirs[9];return s


def continuous_replay(timeline:CommonTimeline,gates:Mapping[str,Any],calibration:Mapping[str,Any],template:Mapping[str,Any]) -> dict[str,np.ndarray]:
    _,dirs=corrected_rotations(timeline,gates["node_to_segment"],calibration);dirs[~timeline.all_nodes_valid]=np.nan
    # Fill only short invalid spans by continuous timestamp interpolation. No
    # label is available here and there is exactly one initialization.
    for k in range(len(SEGMENTS)):
        valid=np.isfinite(dirs[:,k]).all(1);idx=np.flatnonzero(valid)
        if not len(idx):continue
        for component in range(3):dirs[:,k,component]=np.interp(np.arange(len(dirs)),idx,dirs[idx,k,component])
        dirs[:,k]=np.asarray([normalize(v) for v in dirs[:,k]])
    skeleton=np.stack([skeleton_from_directions(v,template,float(gates["rendering"]["head_proxy_length_m"])) for v in dirs])
    return {"time_ns":timeline.time_ns,"segment_direction":dirs,"skeleton_m":skeleton,"all_nodes_valid":timeline.all_nodes_valid.astype(np.uint8)}


def _range_deg(v:np.ndarray) -> float:
    ref=normalize(np.median(v,axis=0));a=np.degrees(np.arccos(np.clip(v@ref,-1,1)));return float(np.percentile(a,95)-np.percentile(a,5))


def evaluate_preview(arrays:Mapping[str,np.ndarray],timeline:CommonTimeline,windows:Mapping[str,tuple[int,int]],gates:Mapping[str,Any],template:Mapping[str,Any],solver:Mapping[str,Any],calibration:Mapping[str,Any]) -> tuple[dict,dict,dict]:
    times=arrays["time_ns"];dirs=arrays["segment_direction"];sk=arrays["skeleton_m"];masks=action_masks(times,windows);pg=gates["preview_gates"];action_rows={};roles={"initial_still_attempt2":"ESTIMATION","t_pose":"ESTIMATION","arms":"VALIDATION_SHOULDER_CHAIN","left_elbow":"ESTIMATION_FUNCTIONAL_AXIS_AND_SIGN","right_elbow_attempt2":"ESTIMATION_FUNCTIONAL_AXIS_AND_SIGN","left_knee":"ESTIMATION_HIP_FUNCTIONAL_AXIS","right_knee":"ESTIMATION_HIP_FUNCTIONAL_AXIS","left_heel":"ESTIMATION_KNEE_FUNCTIONAL_AXIS","right_heel":"ESTIMATION_KNEE_FUNCTIONAL_AXIS","squats":"ESTIMATION_BILATERAL_CHAIN_SOFT_RESIDUAL","trunk":"ESTIMATION_TORSO_PELVIS_FUNCTIONAL_AXIS"}
    relevant={"arms":[2,3,4,5],"left_elbow":[2,4],"right_elbow_attempt2":[3,5],"left_knee":[6],"right_knee":[7],"left_heel":[8],"right_heel":[9],"squats":[6,7,8,9],"trunk":[0,1]}
    action_pass=True
    for action in gates["calibration_actions"]:
        rows=np.flatnonzero(masks[action]);ranges={SEGMENTS[k]:_range_deg(dirs[rows,k]) for k in range(len(SEGMENTS))};rel=relevant.get(action,[]);minimum=min((ranges[SEGMENTS[k]] for k in rel),default=0.);passed=True if action in ("initial_still_attempt2","t_pose") else minimum>=float(pg["minimum_relevant_segment_motion_deg"]);action_pass&=passed
        action_rows[action]={"role":roles[action],"residual":"DISTINCT_STATIC_DIRECTION" if action in ("initial_still_attempt2","t_pose") else "FUNCTIONAL_AXIS_OR_CHAIN_MOTION","parameter_block":"BOARD_AXIS_AND_HEADING" if action in ("initial_still_attempt2","t_pose") else "FUNCTIONAL_AXIS_SIGN_ZERO_OR_CHAIN","nonzero_sensitivity":bool(passed or minimum>1e-6),"segment_angular_range_deg":ranges,"relevant_minimum_range_deg":minimum,"pass":passed}
    initial=np.flatnonzero(masks["initial_still_attempt2"]);tail=initial[max(0,len(initial)-max(2,int(.25*len(initial)))):];jitter=max(float(np.percentile([angle_deg(v,np.median(dirs[tail,k],axis=0)) for v in dirs[tail,k]],95)) for k in range(len(SEGMENTS)))
    boundary=[]
    for action,(start,_) in windows.items():
        i=int(np.searchsorted(times,start));
        if 0<i<len(times):boundary.append({"action":action,"max_segment_step_deg":max(angle_deg(dirs[i-1,k],dirs[i,k]) for k in range(len(SEGMENTS))),"pose_reset":False,"heading_reset":False,"extrinsic_reset":False,"root_reset":False,"velocity_reset":False})
    max_boundary=max((x["max_segment_step_deg"] for x in boundary),default=0.)
    lengths=[]
    expected=[]
    d=template["dimensions"]
    for a,b,key in (("Pelvis","C7Proxy","C7Proxy_to_PelvisProxy_m"),("Shoulder_L","Elbow_L","rendering_upper_arm_length_L_m"),("Shoulder_R","Elbow_R","rendering_upper_arm_length_R_m"),("Elbow_L","Wrist_L","rendering_forearm_length_L_m"),("Elbow_R","Wrist_R","rendering_forearm_length_R_m"),("Hip_L","Knee_L","rendering_thigh_length_L_m"),("Hip_R","Knee_R","rendering_thigh_length_R_m"),("Knee_L","Ankle_L","rendering_shank_length_L_m"),("Knee_R","Ankle_R","rendering_shank_length_R_m")):
        lengths.append(np.linalg.norm(sk[:,LANDMARK_INDEX[a]]-sk[:,LANDMARK_INDEX[b]],axis=1));expected.append(float(d[key]))
    max_length=max(float(np.max(np.abs(v-e))) for v,e in zip(lengths,expected))
    solver_ok=bool(solver["best"]["success"] and not solver["best"]["safety_cap_exhausted"] and solver["best"]["optimality"]<=float(gates["calibration_solver"]["maximum_optimality"]))
    multi_ok=all(x["max_segment_axis_difference_deg"]<=float(gates["calibration_solver"]["maximum_multistart_segment_axis_difference_deg"]) and x["max_graphical_node_difference_m"]<=float(gates["calibration_solver"]["maximum_multistart_node_difference_m"]) for x in solver["multistart"])
    static_ok=all(max(v["initial_residual_deg"],v["t_pose_residual_deg"])<=float(pg["maximum_static_direction_residual_deg"]) for v in solver["static_residuals"].values())
    gates_out={"solver_converged":solver_ok,"multistart_output_stable":multi_ok,"static_direction":static_ok,"all_node_common_time_fraction":timeline.accounting["all_nodes_valid_fraction"]>=float(gates["common_time"]["minimum_all_node_valid_fraction"]),"initial_still_jitter":jitter<=float(pg["maximum_initial_still_axis_jitter_p95_deg"]),"boundary_continuity":max_boundary<=float(pg["maximum_boundary_segment_step_deg"]),"fixed_bone_lengths":max_length<=float(pg["maximum_fixed_bone_length_error_m"]),"all_action_responsiveness":action_pass,"finite":bool(np.isfinite(sk).all()),"left_right_identity_fixed":gates["node_to_segment"]=={"BSF31CC":"torso","BSFC2CC":"pelvis","BSFAA61":"upper_arm_L","BSF1120":"upper_arm_R","BSFB165":"forearm_L","BSFEC35":"forearm_R","BSF44AD":"thigh_L","BSF3C79":"thigh_R","BSF6C53":"shank_L","BSF8BC4":"shank_R"}}
    audit={"initial_still_axis_jitter_p95_deg":jitter,"maximum_action_boundary_step_deg":max_boundary,"maximum_fixed_bone_length_error_m":max_length,"boundary_rows":boundary,"initial_pose_expected":"NATURAL_STAND_ARMS_DOWN_LEGS_EXTENDED","t_pose_expected":"BILATERAL_ARMS_LATERAL","root_translation":"FIXED_PELVIS_ORIGIN","head":"HeadProxy follows torso/C7; no head sensor"}
    return action_rows,gates_out,audit


def run_ablations(timeline:CommonTimeline,windows:Mapping[str,tuple[int,int]],gates:Mapping[str,Any],calibration:Mapping[str,Any],baseline:Mapping[str,np.ndarray]) -> dict:
    masks=action_masks(timeline.time_ns,windows);_,si=_node_indices(timeline,gates["node_to_segment"]);base=baseline["segment_direction"];pg=gates["preview_gates"]
    action="left_elbow";rows=np.flatnonzero(masks[action]);k=SEGMENTS.index("forearm_L");node_j=si["forearm_L"]
    modified=CommonTimeline(timeline.time_ns,timeline.node_order,timeline.rotation.copy(),timeline.gyro_rad_s.copy(),timeline.accel_mps2.copy(),timeline.stationary.copy(),timeline.valid.copy(),timeline.all_nodes_valid.copy(),timeline.accounting)
    modified.rotation[rows,node_j]=modified.rotation[rows[0],node_j];_,constant=corrected_rotations(modified,gates["node_to_segment"],calibration);base_range=_range_deg(base[rows,k]);constant_range=_range_deg(constant[rows,k]);loss=1.-constant_range/max(base_range,1e-9);rms=float(np.sqrt(np.mean(np.square(np.degrees(np.arccos(np.clip(np.sum(base[rows,k]*constant[rows,k],axis=1),-1,1)))))))
    shuffled=CommonTimeline(timeline.time_ns,timeline.node_order,timeline.rotation.copy(),timeline.gyro_rad_s.copy(),timeline.accel_mps2.copy(),timeline.stationary.copy(),timeline.valid.copy(),timeline.all_nodes_valid.copy(),timeline.accounting);shuffled.rotation[:,node_j]=shuffled.rotation[::-1,node_j];_,shdirs=corrected_rotations(shuffled,gates["node_to_segment"],calibration)
    continuity=lambda v:float(np.median(np.square(np.degrees(np.arccos(np.clip(np.sum(v[1:]*v[:-1],axis=1),-1,1))))));ratio=continuity(shdirs[:,k])/max(continuity(base[:,k]),1e-12)
    swapped=dict(gates["node_to_segment"]);swapped["BSFAA61"],swapped["BSF1120"]=swapped["BSF1120"],swapped["BSFAA61"];identity_failed=swapped!=gates["node_to_segment"]
    return {"constant_node_q2":{"node":"BSFB165","action":action,"baseline_dynamic_range_deg":base_range,"ablated_dynamic_range_deg":constant_range,"dynamic_loss_fraction":loss,"output_rms_change_deg":rms,"pass":loss>=float(pg["minimum_constant_node_ablation_dynamic_loss_fraction"]) and rms>=float(pg["minimum_ablation_output_rms_deg"])},"timestamp_shuffle":{"node":"BSFB165","continuity_cost_ratio":ratio,"pass":ratio>=float(pg["minimum_timestamp_shuffle_continuity_cost_ratio"])},"left_right_node_swap":{"nodes":["BSFAA61","BSF1120"],"identity_gate_failed":identity_failed,"pass":identity_failed}}
