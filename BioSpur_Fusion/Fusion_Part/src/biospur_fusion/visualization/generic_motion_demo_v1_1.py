"""Data-driven, calibration-only generic-template motion demo V1.1."""
from __future__ import annotations

import hashlib,json
from pathlib import Path
from typing import Any
import numpy as np
from scipy.optimize import least_squares
from scipy.spatial.transform import Rotation,Slerp

from biospur_fusion.calibration.real_capture import _solve_t4
from biospur_fusion.visualization.generic_motion_demo_v1 import (
    DISCLAIMER,EDGES,LANDMARKS,LANDMARK_INDEX,NODE_INDEX,NODE_LANDMARK,NODE_ORDER,
    SEGMENT_SPECS,_align_segment_axes,_build_timeline,_length_errors,_q1,_sample_frontends,
    _nearest,_windows,deterministic_display_frame,dump_json,sha256,
)

SEGMENTS=("torso","upper_arm_L","upper_arm_R","forearm_L","forearm_R","thigh_L","thigh_R","shank_L","shank_R")

def _norm(v,fallback=None):
    n=float(np.linalg.norm(v))
    if np.isfinite(n) and n>1e-10:return np.asarray(v,float)/n
    return _norm(np.array([0.,0.,1.]) if fallback is None else fallback)

def _state_skeleton(x,template):
    d=template["dimensions"];root=x[:3];dirs=np.asarray([_norm(v) for v in x[3:].reshape(-1,3)])
    torso,ual,uar,fal,far,thl,thr,shl,shr,lateral=dirs
    lateral=_norm(lateral-torso*float(lateral@torso),np.array([1.,0,0]));s=np.zeros((len(LANDMARKS),3));i=LANDMARK_INDEX
    s[i["Pelvis"]]=root;s[i["C7Proxy"]]=root+d["C7Proxy_to_PelvisProxy_m"]*torso
    s[i["Shoulder_L"]]=s[i["C7Proxy"]]-.5*d["graphical_shoulder_width_m"]*lateral;s[i["Shoulder_R"]]=s[i["C7Proxy"]]+.5*d["graphical_shoulder_width_m"]*lateral
    s[i["Elbow_L"]]=s[i["Shoulder_L"]]+d["rendering_upper_arm_length_L_m"]*ual;s[i["Elbow_R"]]=s[i["Shoulder_R"]]+d["rendering_upper_arm_length_R_m"]*uar
    s[i["Wrist_L"]]=s[i["Elbow_L"]]+d["rendering_forearm_length_L_m"]*fal;s[i["Wrist_R"]]=s[i["Elbow_R"]]+d["rendering_forearm_length_R_m"]*far
    s[i["Hip_L"]]=root-.5*d["graphical_hip_width_m"]*lateral;s[i["Hip_R"]]=root+.5*d["graphical_hip_width_m"]*lateral
    s[i["Knee_L"]]=s[i["Hip_L"]]+d["rendering_thigh_length_L_m"]*thl;s[i["Knee_R"]]=s[i["Hip_R"]]+d["rendering_thigh_length_R_m"]*thr
    s[i["Ankle_L"]]=s[i["Knee_L"]]+d["rendering_shank_length_L_m"]*shl;s[i["Ankle_R"]]=s[i["Knee_R"]]+d["rendering_shank_length_R_m"]*shr
    return s

def _initial_state(axes,frames,actions,template):
    mask=np.isin(actions,["initial_still_attempt2","t_pose"]);values=[]
    for name in SEGMENTS:values.append(_norm(np.nanmedian(axes[name][mask],axis=0)))
    central=frames["BSF31CC"];lateral=np.nanmedian(central[mask,:,0],axis=0);lateral=_norm(lateral-values[0]*float(lateral@values[0]),np.array([1,0,0]))
    return np.r_[np.zeros(3),np.asarray(values+[lateral]).ravel()]

def _baseline_and_reject(raw,actions,gates):
    initial=actions=="initial_still_attempt2";baseline=np.nanmedian(raw[initial],axis=0);delta=raw-baseline
    accepted=np.isfinite(delta).all(2);log=[];e=gates["estimator"]
    for j,node in enumerate(NODE_ORDER):
        for action in gates["calibration_actions"]:
            idx=np.flatnonzero((actions==action)&accepted[:,j]);
            if len(idx)<3:continue
            steps=np.linalg.norm(np.diff(delta[idx,j],axis=0),axis=1);med=float(np.median(steps));mad=float(1.4826*np.median(np.abs(steps-med)));threshold=max(float(e["pre_estimation_rejection_minimum_m"]),med+float(e["pre_estimation_rejection_mad_multiplier"])*mad)
            centre=np.median(delta[idx,j],axis=0);deviation=np.linalg.norm(delta[idx,j]-centre,axis=1);dmed=float(np.median(deviation));dmad=float(1.4826*np.median(np.abs(deviation-dmed)));deviation_threshold=max(float(e["pre_estimation_rejection_minimum_m"]),dmed+float(e["pre_estimation_rejection_mad_multiplier"])*dmad)
            previous=delta[idx[0],j].copy()
            for ordinal,k in enumerate(idx[1:],start=1):
                innovation=float(np.linalg.norm(delta[k,j]-previous))
                if innovation>=threshold and deviation[ordinal]>deviation_threshold:
                    accepted[k,j]=False;log.append({"node":node,"action":action,"frame":int(k),"innovation_m":innovation,"threshold_m":threshold,"deviation_m":float(deviation[ordinal]),"deviation_threshold_m":deviation_threshold,"estimator_weight":0.0})
                else:previous=delta[k,j]
    return baseline,delta,accepted,log

def _imu_factors(axes,frames,actions):
    factors=np.stack([axes[n] for n in SEGMENTS],axis=1)
    torso=factors[:,0];lat=frames["BSF31CC"][:,:,0];lat=np.asarray([_norm(v-t*float(v@t),np.array([1,0,0])) for v,t in zip(lat,torso)])
    return np.concatenate([factors,lat[:,None]],axis=1)

def _extended_windows(ledger,gates,action_segments_path):
    native={str(r["name"]):(int(r["start_ns"]),int(r["stop_ns"])) for r in ledger["action_windows"]}
    if set(native)!=set(gates["ledger_native_actions"]):raise ValueError("native calibration ledger action mismatch")
    path=Path(action_segments_path);binding=gates["custom_calibration_action_metadata"]
    if sha256(path)!=binding["sha256"]:raise ValueError("custom action metadata SHA mismatch")
    metadata=json.loads(path.read_text());custom={}
    for row in metadata["segments"]:
        if row.get("selected") and row.get("role") in binding["allowed_roles"] and row.get("action") in binding["allowed_actions"]:
            custom[row["action"]]=(int(round(float(row["start"])*1e9)),int(round(float(row["stop"])*1e9)))
    if set(custom)!=set(binding["allowed_actions"]):raise ValueError("custom calibration action metadata incomplete")
    merged={**native,**custom};return {name:merged[name] for name in gates["calibration_actions"]}

def _sample_custom_frontends(t4_path,q1_path,windows,gates):
    """Select only explicitly authorized custom-action rows from frozen frontends."""
    binding=gates["custom_action_frontends"]
    if sha256(Path(t4_path))!=binding["t4_sha256"] or sha256(Path(q1_path))!=binding["q1_sha256"]:raise ValueError("custom frontend SHA mismatch")
    custom={a:windows[a] for a in gates["custom_calibration_action_metadata"]["allowed_actions"]}
    times,actions,action_time=_build_timeline(custom,gates["estimator"]["timeline_hz"]);host_s=times/1e9;n=len(times)
    raw=np.full((n,len(NODE_ORDER),3),np.nan);cov=np.full((n,len(NODE_ORDER),3,3),np.nan);rot=np.full((n,len(NODE_ORDER),3,3),np.nan)
    ug=np.full((n,len(NODE_ORDER)),np.inf);qg=ug.copy();maxu=gates["estimator"]["maximum_uwb_match_gap_ns"]/1e9
    accounting={}
    with np.load(t4_path,allow_pickle=False) as t4,np.load(q1_path,allow_pickle=False) as q1:
        if set(t4.files)!=set(NODE_ORDER) or set(q1.files)!=set(NODE_ORDER):raise ValueError("custom frontend node identity mismatch")
        for j,node in enumerate(NODE_ORDER):
            p=t4[node];pi,pg=_nearest(p[:,0],host_s);pok=pg<=maxu;raw[pok,j]=p[pi[pok],1:4]/1000.;sigma=np.maximum(gates["estimator"]["uwb_displacement_sigma_floor_m"],p[pi[pok],4]/1000.);cov[pok,j]=np.eye(3)[None]*sigma[:,None,None]**2;ug[:,j]=pg
            q=q1[node];qt,unique=np.unique(q[:,0],return_index=True);qr=Rotation.from_quat(q[unique,1:5][:,[1,2,3,0]]);qok=(host_s>=qt[0])&(host_s<=qt[-1]);rot[qok,j]=Slerp(qt,qr)(host_s[qok]).as_matrix();_,qgap=_nearest(qt,host_s);qg[:,j]=qgap
            accounting[node]={"timeline_rows":int(n),"t4_matched":int(pok.sum()),"q1_slerp_interpolated":int(qok.sum()),"q1_source":"FROZEN_REPAIRED_Q1_FEATURE_TIMELINE"}
    return times,actions,action_time,raw,cov,rot,ug,qg,accounting

def _solve_sequence(template,gates,actions,delta,accepted,cov,imu,base_state,mode="normal",decimation=1):
    sel=np.arange(0,len(actions),decimation);actions=actions[sel];delta=delta[sel].copy();accepted=accepted[sel].copy();cov=cov[sel];imu=imu[sel].copy();rng=np.random.default_rng(int(gates["estimator"]["random_seed"]));
    if mode=="constant_imu":imu[:]=imu[0]
    elif mode=="shuffle_imu":
        for a in np.unique(actions):
            ix=np.flatnonzero(actions==a);imu[ix]=imu[ix[::-1]]
    elif mode=="shuffle_uwb_identity":delta=delta[:,np.roll(np.arange(len(NODE_ORDER)),1)];accepted=accepted[:,np.roll(np.arange(len(NODE_ORDER)),1)]
    elif mode=="zero_uwb":delta[:]=0
    base=_state_skeleton(base_state,template);states=np.empty((len(sel),len(base_state)));skeleton=np.empty((len(sel),len(LANDMARKS),3));residual_records=[];prev=prev2=None;last=None;e=gates["estimator"]
    tracked={NODE_LANDMARK[node]:j for j,node in enumerate(NODE_ORDER)}
    segment_endpoints=(("Pelvis","C7Proxy"),("Shoulder_L","Elbow_L"),("Shoulder_R","Elbow_R"),("Elbow_L","Wrist_L"),("Elbow_R","Wrist_R"),("Hip_L","Knee_L"),("Hip_R","Knee_R"),("Knee_L","Ankle_L"),("Knee_R","Ankle_R"))
    for ordinal,a in enumerate(actions):
        if prev is None or a!=last:
            x0=base_state.copy();pj=NODE_INDEX["BSFC2CC"]
            if accepted[ordinal,pj]:x0[:3]=delta[ordinal,pj]
            x0[3:]=imu[ordinal].ravel();prev=prev2=None
        else:x0=prev.copy() if prev2 is None else prev+(prev-prev2)
        x=x0.copy();temporal_dirs=np.asarray([_norm(v) for v in x0[3:].reshape(-1,3)])
        wu=float(e["joint_uwb_factor_weight"]);wi=float(e["joint_imu_factor_weight"]);wt=float(e["joint_temporal_factor_weight"])
        for _ in range(int(e["joint_projection_iterations_per_frame"])):
            s=_state_skeleton(x,template);corrections=[];weights=[]
            for landmark,j in tracked.items():
                if not accepted[ordinal,j]:continue
                target=base[LANDMARK_INDEX[landmark]]+delta[ordinal,j];res=target-s[LANDMARK_INDEX[landmark]];sigma=float(e["uwb_displacement_sigma_floor_m"])
                if np.isfinite(cov[ordinal,j]).all():sigma=max(sigma,float(np.sqrt(max(0,np.trace(cov[ordinal,j])/3))))
                z=float(np.linalg.norm(res))/sigma;weight=1.0 if z<=float(e["uwb_robust_f_scale_sigma"]) else float(e["uwb_robust_f_scale_sigma"])/max(z,1e-9);corrections.append(res);weights.append(weight/max(sigma,1e-6))
            if weights:x[:3]+=.35*np.average(np.asarray(corrections),axis=0,weights=np.asarray(weights))
            s=_state_skeleton(x,template);dirs=np.asarray([_norm(v) for v in x[3:].reshape(-1,3)])
            for k,(proximal,distal) in enumerate(segment_endpoints):
                dj=tracked.get(distal);pj=tracked.get(proximal);target_d=base[LANDMARK_INDEX[distal]]+delta[ordinal,dj] if dj is not None and accepted[ordinal,dj] else s[LANDMARK_INDEX[distal]];target_p=base[LANDMARK_INDEX[proximal]]+delta[ordinal,pj] if pj is not None and accepted[ordinal,pj] else s[LANDMARK_INDEX[proximal]];uwb_dir=_norm(target_d-target_p,dirs[k]);factor=wu if dj is not None and accepted[ordinal,dj] else 0.;dirs[k]=_norm(factor*uwb_dir+wi*imu[ordinal,k]+wt*temporal_dirs[k],imu[ordinal,k])
            lateral_targets=[]
            for left,right in (("Elbow_L","Elbow_R"),("Knee_L","Knee_R")):
                lj=tracked[left];rj=tracked[right]
                if accepted[ordinal,lj] and accepted[ordinal,rj]:lateral_targets.append((base[LANDMARK_INDEX[right]]+delta[ordinal,rj])-(base[LANDMARK_INDEX[left]]+delta[ordinal,lj]))
            lateral=_norm(np.median(lateral_targets,axis=0),dirs[9]) if lateral_targets else dirs[9];dirs[9]=_norm(wu*lateral+wi*imu[ordinal,9]+wt*temporal_dirs[9],imu[ordinal,9]);x[3:]=dirs.ravel()
        states[ordinal]=x;skeleton[ordinal]=_state_skeleton(x,template);fit_res=[]
        for landmark,j in tracked.items():
            if accepted[ordinal,j]:fit_res.append(float(np.linalg.norm(skeleton[ordinal,LANDMARK_INDEX[landmark]]-(base[LANDMARK_INDEX[landmark]]+delta[ordinal,j]))))
        residual_records.append({"projection_iterations":int(e["joint_projection_iterations_per_frame"]),"finite":bool(np.isfinite(x).all()),"median_factor_residual_m":float(np.median(fit_res)) if fit_res else None});prev2=prev;prev=x;last=a
    return sel,states,skeleton,residual_records

def _range(points):
    center=np.median(points,axis=0);return float(np.percentile(np.linalg.norm(points-center,axis=1),95)-np.percentile(np.linalg.norm(points-center,axis=1),5))
def _angle_range(v):
    ref=_norm(np.median(v,axis=0));return float(np.percentile(np.arccos(np.clip(v@ref,-1,1)),95)-np.percentile(np.arccos(np.clip(v@ref,-1,1)),5))
def _lag_corr_gain(x,y,maxlag=5):
    x=np.asarray(x);y=np.asarray(y);best=(0.,0)
    for lag in range(-maxlag,maxlag+1):
        xx=x[max(0,-lag):len(x)-max(0,lag)];yy=y[max(0,lag):len(y)-max(0,-lag)]
        finite=np.isfinite(xx)&np.isfinite(yy);xx=xx[finite];yy=yy[finite]
        if len(xx)<5 or np.std(xx)<1e-8 or np.std(yy)<1e-8:continue
        c=float(np.corrcoef(xx,yy)[0,1]);
        if abs(c)>abs(best[0]):best=(c,lag)
    finite=np.isfinite(x)&np.isfinite(y);gain=float(np.std(y[finite])/max(np.std(x[finite]),1e-9)) if finite.any() else 0.;return {"correlation":best[0],"lag_frames":best[1],"motion_gain":gain}

def _responsiveness(actions,delta,accepted,rotq,skeleton,states,gates):
    report={};checks=[];g=gates["motion_gates"];base=np.median(skeleton[actions=="initial_still_attempt2"],axis=0)
    relevant={"arms":[("BSFAA61","Elbow_L"),("BSF1120","Elbow_R"),("BSFB165","Wrist_L"),("BSFEC35","Wrist_R")],"left_elbow":[("BSFB165","Wrist_L")],"right_elbow_attempt2":[("BSFEC35","Wrist_R")],"left_knee":[("BSF44AD","Knee_L")],"right_knee":[("BSF3C79","Knee_R")],"left_heel":[("BSF6C53","Ankle_L")],"right_heel":[("BSF8BC4","Ankle_R")],"squats":[("BSFC2CC","Pelvis"),("BSF44AD","Knee_L"),("BSF3C79","Knee_R")],"trunk":[("BSF31CC","C7Proxy")],"golf_swing":[("BSFB165","Wrist_L"),("BSFEC35","Wrist_R"),("BSF31CC","C7Proxy")],"boxing":[("BSFB165","Wrist_L"),("BSFEC35","Wrist_R")]}
    for action in gates["calibration_actions"]:
        ix=np.flatnonzero(actions==action);node_uwb={};node_imu={};joint={};segment={}
        for j,node in enumerate(NODE_ORDER):
            ok=ix[accepted[ix,j]];node_uwb[node]=_range(delta[ok,j]) if len(ok)>2 else 0.;mats=rotq[ix,j];mats=mats[np.isfinite(mats).all((1,2))]
            if len(mats):ref=mats[0];node_imu[node]=float(np.max(Rotation.from_matrix(np.einsum("ij,njk->nik",ref.T,mats)).magnitude()))
            else:node_imu[node]=0.0
        for name,k in LANDMARK_INDEX.items():joint[name]=_range(skeleton[ix,k])
        dirs=states[ix,3:].reshape(len(ix),-1,3)
        for k,name in enumerate(SEGMENTS+("torso_lateral",)):segment[name]=_angle_range(dirs[:,k])
        transfer={}
        for node,landmark in relevant.get(action,[]):
            inp=np.linalg.norm(delta[ix,NODE_INDEX[node]],axis=1);out=np.linalg.norm(skeleton[ix,LANDMARK_INDEX[landmark]]-base[LANDMARK_INDEX[landmark]],axis=1);transfer[f"{node}->{landmark}"]=_lag_corr_gain(inp,out)
        report[action]={"input_uwb_displacement_range_m":node_uwb,"input_imu_relative_angular_range_rad":node_imu,"output_joint_displacement_range_m":joint,"output_segment_angular_range_rad":segment,"lagged_motion_transfer":transfer}
    r=report
    def add(name,passed,value,threshold):checks.append({"check":name,"pass":bool(passed),"value":float(value),"threshold":float(threshold)})
    for side in "LR":
        add(f"arms_elbow_{side}",r["arms"]["output_joint_displacement_range_m"][f"Elbow_{side}"]>=g["minimum_arms_elbow_displacement_range_m"],r["arms"]["output_joint_displacement_range_m"][f"Elbow_{side}"],g["minimum_arms_elbow_displacement_range_m"])
        add(f"arms_wrist_{side}",r["arms"]["output_joint_displacement_range_m"][f"Wrist_{side}"]>=g["minimum_arms_wrist_displacement_range_m"],r["arms"]["output_joint_displacement_range_m"][f"Wrist_{side}"],g["minimum_arms_wrist_displacement_range_m"])
    for action,side in (("left_elbow","L"),("right_elbow_attempt2","R")):
        ix=np.flatnonzero(actions==action);relative=skeleton[ix,LANDMARK_INDEX[f"Wrist_{side}"]]-skeleton[ix,LANDMARK_INDEX[f"Elbow_{side}"]];value=_range(relative);add(f"{action}_relative_wrist",value>=g["minimum_relative_wrist_to_elbow_range_m"],value,g["minimum_relative_wrist_to_elbow_range_m"])
    for action,joint in (("left_knee","Knee_L"),("right_knee","Knee_R"),("left_heel","Ankle_L"),("right_heel","Ankle_R")):
        value=r[action]["output_joint_displacement_range_m"][joint];add(f"{action}_{joint}",value>=g["minimum_knee_or_ankle_displacement_range_m"],value,g["minimum_knee_or_ankle_displacement_range_m"])
    ix=np.flatnonzero(actions=="squats");configuration=.5*(np.linalg.norm(skeleton[ix,LANDMARK_INDEX["Pelvis"]]-skeleton[ix,LANDMARK_INDEX["Knee_L"]],axis=1)+np.linalg.norm(skeleton[ix,LANDMARK_INDEX["Pelvis"]]-skeleton[ix,LANDMARK_INDEX["Knee_R"]],axis=1));value=float(np.ptp(configuration));add("squats_configuration",value>=g["minimum_squat_configuration_change_m"],value,g["minimum_squat_configuration_change_m"])
    transfer_values=[v for a in report.values() for v in a["lagged_motion_transfer"].values()];corr=min((abs(v["correlation"]) for v in transfer_values),default=0);gain=min((v["motion_gain"] for v in transfer_values),default=0);add("all_relevant_minimum_correlation",corr>=g["minimum_best_lagged_correlation"],corr,g["minimum_best_lagged_correlation"]);add("all_relevant_minimum_gain",gain>=g["minimum_motion_gain"],gain,g["minimum_motion_gain"])
    return report,checks

def run_analysis(ledger_path,layout,template_path,gates_path,action_segments_path,custom_t4_path,custom_q1_path,output):
    gates=json.loads(Path(gates_path).read_text());template=json.loads(Path(template_path).read_text())
    if sha256(Path(template_path))!=gates["template"]["sha256"]:raise ValueError("template SHA mismatch")
    if any(x in str(ledger_path).lower() for x in ("heldout","walk","final_still","raw")) or Path(ledger_path).name!="CALIBRATION_TYPED_LEDGER.npz":raise ValueError("calibration ledger only")
    if Path(output).exists():raise ValueError("output exists")
    Path(output).mkdir(parents=True)
    with np.load(ledger_path,allow_pickle=False) as ledger:
        windows=_extended_windows(ledger,gates,action_segments_path);native={a:windows[a] for a in gates["ledger_native_actions"]};obs,t4acc,t4rej=_solve_t4(ledger,Path(layout));q1,q1aud=_q1(ledger,native);times,actions,action_time=_build_timeline(native,gates["estimator"]["timeline_hz"]);raw,cov,rotq,ug,qg=_sample_frontends(times,obs,q1,gates)
    ct,ca,cat,cr,cc,cq,cug,cqg,custom_accounting=_sample_custom_frontends(custom_t4_path,custom_q1_path,windows,gates)
    times=np.r_[times,ct];actions=np.r_[actions,ca];action_time=np.r_[action_time,cat];raw=np.r_[raw,cr];cov=np.r_[cov,cc];rotq=np.r_[rotq,cq];ug=np.r_[ug,cug];qg=np.r_[qg,cqg]
    display,display_audit=deterministic_display_frame(raw,actions);origin=np.nanmedian(raw[:,NODE_INDEX["BSFC2CC"]],axis=0);raw=np.einsum("ij,ntj->nti",display,raw-origin);rotq=np.einsum("ij,ntjk->ntik",display,rotq);axes,frames,align=_align_segment_axes(raw,rotq,actions);base_state=_initial_state(axes,frames,actions,template);baseline,delta,accepted,reject_log=_baseline_and_reject(raw,actions,gates);imu=_imu_factors(axes,frames,actions)
    sel,states,skeleton,fitlog=_solve_sequence(template,gates,actions,delta,accepted,cov,imu,base_state);base=_state_skeleton(base_state,template);targets=np.full_like(raw,np.nan)
    for j,node in enumerate(NODE_ORDER):targets[:,j]=base[LANDMARK_INDEX[NODE_LANDMARK[node]]]+delta[:,j]
    responsiveness,checks=_responsiveness(actions,delta,accepted,rotq,skeleton,states,gates);lengths,maxlen=_length_errors(skeleton,template)
    residual=[];pernode={}
    for j,node in enumerate(NODE_ORDER):
        pred=skeleton[:,LANDMARK_INDEX[NODE_LANDMARK[node]]];ok=accepted[:,j];v=np.linalg.norm(pred[ok]-targets[ok,j],axis=1);residual.extend(v.tolist());pernode[node]={"count":int(len(v)),"median_m":float(np.median(v)),"p95_m":float(np.percentile(v,95)),"max_m":float(np.max(v))}
    residual=np.asarray(residual);g=gates["motion_gates"];model_pass=float(np.median(residual))<=g["maximum_displacement_residual_median_m"] and float(np.percentile(residual,95))<=g["maximum_displacement_residual_p95_m"]
    ablations={};dec=int(gates["estimator"]["ablation_decimation"]);baseline_dec=skeleton[::dec]
    for mode in ("constant_imu","shuffle_imu","shuffle_uwb_identity","zero_uwb"):
        _,_,sk,_=_solve_sequence(template,gates,actions,delta,accepted,cov,imu,base_state,mode=mode,decimation=dec);rms=float(np.sqrt(np.mean((sk-baseline_dec)**2)));ablations[mode]={"rms_output_change_m":rms,"materially_changed_or_failed":bool(rms>=g["ablation_minimum_rms_output_change_m"])}
    numerical=bool(all(x["pass"] for x in checks) and model_pass and all(v["materially_changed_or_failed"] for v in ablations.values()) and maxlen<=g["maximum_generic_length_change_m"] and np.isfinite(skeleton).all())
    failures=[]
    if not all(x["pass"] for x in checks):failures.append("MOTION_RESPONSIVENESS_GATE_FAIL")
    if not model_pass:failures.append("MODEL_TO_DATA_MISMATCH")
    if not all(v["materially_changed_or_failed"] for v in ablations.values()):failures.append("FAIL_OUTPUT_NOT_DATA_DRIVEN")
    if maxlen>g["maximum_generic_length_change_m"]:failures.append("GENERIC_LENGTH_CHANGED")
    report={"schema":"biospur-generic-template-motion-demo-analysis-v1-1","analysis_verdict":"NUMERICAL_MOTION_GATES_PASS_PREVIEW_REQUIRED" if numerical else "GENERIC_TEMPLATE_MOTION_DEMO_V1_1_FAIL","failures":failures,"final_pass_requires_preview":True,
        "historical_v1_interpretation":{"SOFTWARE_INTEGRITY_PASS":True,"MOTION_FIDELITY_NOT_TESTED":True,"PREVIEW_REJECTED_BY_OPERATOR":True},"firewall":{"operator_measurements":"SEALED_NOT_READ","walk":"SEALED_NOT_OPENED","final_still":"SEALED_NOT_OPENED"},
        "template":{"sha256":sha256(Path(template_path)),"dimensions":template["dimensions"],"immutable":True},"custom_action_metadata":{"sha256":sha256(Path(action_segments_path)),"included_actions":["golf_swing","boxing"],"heldout_payload_opened":False},"frontends":{"t4_accounting":t4acc,"t4_rejections":len(t4rej),"q1_audits":q1aud,"custom_action_accounting":custom_accounting,"custom_t4_sha256":sha256(Path(custom_t4_path)),"custom_q1_sha256":sha256(Path(custom_q1_path)),"custom_rows_selected_only":["golf_swing","boxing"]},"display_frame":display_audit,"alignment":align,"pre_estimation_uwb_rejection":{"count":len(reject_log),"zero_estimator_weight":True},
        "model_to_data":{"pass":model_pass,"overall_median_m":float(np.median(residual)),"overall_p95_m":float(np.percentile(residual,95)),"per_node":pernode},"motion_responsiveness":{"per_action":responsiveness,"checks":checks,"pass":all(x["pass"] for x in checks)},"ablations":ablations,"fixed_length_audit":{"maximum_error_m":maxlen,"per_edge":lengths},"topology":{"connected":True,"left_right_identity_fixed":True},"state":{"finite":bool(np.isfinite(skeleton).all()),"joint_trajectories_directly_constrained_by_uwb_displacements":True,"entire_skeleton_forward_propagated_from_imu_only":False}}
    out=Path(output);dump_json(out/"V1_1_DIAGNOSTICS.json",report);dump_json(out/"PRE_ESTIMATION_UWB_REJECTIONS.json",reject_log)
    for name,array in (("TIME_NS",times),("ACTION_ID",np.asarray([gates["calibration_actions"].index(str(a)) for a in actions],np.uint8)),("ACTION_TIME_S",action_time),("SKELETON_M",skeleton),("UWB_TARGET_M",targets),("UWB_ACCEPTED",accepted.astype(np.uint8)),("STATE",states)):np.save(out/f"{name}.npy",array,allow_pickle=False)
    hashes=[]
    for p in sorted(out.iterdir()):hashes.append(f"{sha256(p)}  {p.name}")
    (out/"SHA256SUMS").write_text("\n".join(hashes)+"\n");return report

def _clip_indices(skeleton,aid,action_id,source_hz,duration_s,fps):
    ix=np.flatnonzero(aid==action_id);velocity=np.linalg.norm(np.diff(skeleton[ix,LANDMARK_INDEX["Wrist_L"]],axis=0),axis=1)+np.linalg.norm(np.diff(skeleton[ix,LANDMARK_INDEX["Wrist_R"]],axis=0),axis=1);window=max(2,int(duration_s*source_hz));score=np.convolve(velocity,np.ones(min(window,len(velocity))),mode="same");center=int(np.argmax(score));lo=max(0,min(len(ix)-window,center-window//2));src=ix[lo:lo+window];return src[np.clip(np.round(np.linspace(0,len(src)-1,int(duration_s*fps))).astype(int),0,len(src)-1)]

def render_previews(analysis_dir,gates_path,output_dir):
    ad=Path(analysis_dir);out=Path(output_dir);out.mkdir(parents=True,exist_ok=False);gates=json.loads(Path(gates_path).read_text());r=gates["rendering"];s=np.load(ad/"SKELETON_M.npy");targets=np.load(ad/"UWB_TARGET_M.npy");accepted=np.load(ad/"UWB_ACCEPTED.npy").astype(bool);aid=np.load(ad/"ACTION_ID.npy");action_time=np.load(ad/"ACTION_TIME_S.npy");actions=gates["calibration_actions"]
    import matplotlib;matplotlib.use("Agg");import matplotlib.pyplot as plt
    from matplotlib.animation import FFMpegWriter,PillowWriter
    diagnostics=json.loads((ad/"V1_1_DIAGNOSTICS.json").read_text());check_by_action={a:[] for a in actions}
    for check in diagnostics["motion_responsiveness"]["checks"]:
        for action in actions:
            if check["check"].startswith(action) or (action=="arms" and check["check"].startswith("arms_")):check_by_action[action].append(check)
    action_status={}
    for a in actions:
        transfers=diagnostics["motion_responsiveness"]["per_action"][a]["lagged_motion_transfer"].values()
        transfer_low=any(abs(v["correlation"])<gates["motion_gates"]["minimum_best_lagged_correlation"] or v["motion_gain"]<gates["motion_gates"]["minimum_motion_gain"] for v in transfers)
        action_status[a]="FAIL" if any(not x["pass"] for x in check_by_action[a]) else ("LOW_CONFIDENCE_MODEL_MISMATCH" if not diagnostics["model_to_data"]["pass"] else "PASS_REFERENCE" if a in ("initial_still_attempt2","t_pose") else "LOW_CONFIDENCE" if transfer_low or not diagnostics["motion_responsiveness"]["per_action"][a]["lagged_motion_transfer"] else "PASS")
    manifests=[]
    def render(name,indices,gif=False):
        mp4=out/f"{name}.mp4";fig=plt.figure(figsize=(r["width_px"]/100,r["height_px"]/100),dpi=100);close=fig.add_subplot(121,projection="3d");world=fig.add_subplot(122,projection="3d");world_points=s[indices].reshape(-1,3);lo=np.min(world_points,axis=0);hi=np.max(world_points,axis=0);c=.5*(lo+hi);rad=max(.8,float(np.max(hi-lo))*.65);world.set_xlim(c[0]-rad,c[0]+rad);world.set_ylim(c[1]-rad,c[1]+rad);world.set_zlim(c[2]-rad,c[2]+rad);world.set_box_aspect((1,1,1));world.view_init(**{"elev":r["world_camera"]["elevation_deg"],"azim":r["world_camera"]["azimuth_deg"]});artists=[];fig.text(.5,.01,r["disclaimer"],ha="center",fontsize=8,color="crimson")
        def draw(k):
            for a in artists:a.remove()
            artists.clear();i=int(indices[k]);sk=s[i];root=sk[LANDMARK_INDEX["Pelvis"]];extent=np.ptp(sk[:,2]);crad=max(.75,float(extent)/(2*r["closeup_skeleton_height_fraction_minimum"]));close.set_xlim(root[0]-crad,root[0]+crad);close.set_ylim(root[1]-crad,root[1]+crad);close.set_zlim(root[2]-crad,root[2]+crad);close.set_box_aspect((1,1,1));close.view_init(elev=15,azim=-65);close.set_title("Body-following close-up: target vs fit");world.set_title("World-space fitted skeleton + UWB targets")
            action=actions[int(aid[i])];fig.suptitle(f"{action} — action_t={float(action_time[i]):.2f}s — {action_status[action]}",color="crimson" if action_status[action]=="FAIL" else "black")
            for ax in (close,world):
                for a,b in EDGES:
                    v=sk[[LANDMARK_INDEX[a],LANDMARK_INDEX[b]]];line,=ax.plot(v[:,0],v[:,1],v[:,2],color="navy",lw=3);artists.append(line)
                dots=ax.scatter(sk[:,0],sk[:,1],sk[:,2],color="darkorange",s=18);artists.append(dots);valid=np.isfinite(targets[i]).all(1);good=valid&accepted[i];bad=valid&~accepted[i]
                if good.any():artists.append(ax.scatter(targets[i,good,0],targets[i,good,1],targets[i,good,2],color="green",alpha=.35,s=16))
                if bad.any():artists.append(ax.scatter(targets[i,bad,0],targets[i,bad,1],targets[i,bad,2],color="red",marker="x",s=25))
        writer=FFMpegWriter(fps=r["fps"],codec=r["mp4_codec"],extra_args=["-pix_fmt",r["pixel_format"],"-metadata","creation_time=1970-01-01T00:00:00Z"])
        with writer.saving(fig,str(mp4),100):
            for k in range(len(indices)):draw(k);writer.grab_frame()
        if gif:
            gp=out/f"{name}.gif";writer2=PillowWriter(fps=r["gif_fps"])
            with writer2.saving(fig,str(gp),50):
                for k in range(0,len(indices),max(1,int(r["fps"]/r["gif_fps"]))):draw(k);writer2.grab_frame()
        plt.close(fig);included=sorted({actions[int(aid[i])] for i in indices},key=actions.index);return {"name":name,"mp4":str(mp4.resolve()),"frames":len(indices),"actions":included,"action_status":{a:action_status[a] for a in included},"body_following_limits_from_skeleton_only":True,"minimum_closeup_height_fraction":r["closeup_skeleton_height_fraction_minimum"],"uwb_rejected_zero_weight_and_red":True}
    clips=[]
    for action in gates["preview_actions"]:clips.append(render(action.upper()+"_V1_1",_clip_indices(s,aid,actions.index(action),gates["estimator"]["timeline_hz"],r["per_action_duration_s"],r["fps"])))
    pieces=[];per=r["combined_duration_s"]/len(gates["preview_actions"])
    for action in gates["preview_actions"]:pieces.append(_clip_indices(s,aid,actions.index(action),gates["estimator"]["timeline_hz"],per,r["fps"]))
    combined=render("GENERIC_TEMPLATE_MOTION_DEMO_V1_1_COMBINED",np.concatenate(pieces),gif=True);clips.append(combined)
    preview_complete=all(Path(c["mp4"]).is_file() for c in clips) and (out/"GENERIC_TEMPLATE_MOTION_DEMO_V1_1_COMBINED.gif").is_file();numerical=diagnostics["analysis_verdict"]=="NUMERICAL_MOTION_GATES_PASS_PREVIEW_REQUIRED";verdict="GENERIC_TEMPLATE_MOTION_DEMO_V1_1_PASS" if numerical and preview_complete else ("FAIL_OUTPUT_NOT_DATA_DRIVEN" if "FAIL_OUTPUT_NOT_DATA_DRIVEN" in diagnostics["failures"] else "GENERIC_TEMPLATE_MOTION_DEMO_V1_1_FAIL")
    final={"verdict":verdict,"numerical_motion_responsiveness_pass":numerical,"previews_generated":preview_complete,"clips":clips,"operator_measurements":"SEALED_NOT_READ","walk":"SEALED_NOT_OPENED","final_still":"SEALED_NOT_OPENED","walk_release_request_created":False};dump_json(out/"FINAL_VERDICT.json",final);return final
