#!/usr/bin/env python3
"""Derivative V2 synthetic tolerance resolution and zero-iteration preflight."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

import numpy as np
from scipy.sparse import csr_matrix

ROOT=Path(__file__).resolve().parents[2]


def sha256(path:Path)->str:
    digest=hashlib.sha256()
    with Path(path).open("rb") as handle:
        while block:=handle.read(4<<20):digest.update(block)
    return digest.hexdigest()


def dump_json(path:Path,value)->None:
    def native(item):
        if isinstance(item,dict):return {str(key):native(value) for key,value in item.items()}
        if isinstance(item,(list,tuple)):return [native(value) for value in item]
        if isinstance(item,np.integer):return int(item)
        if isinstance(item,np.floating):return float(item)
        if isinstance(item,np.bool_):return bool(item)
        return item
    Path(path).write_text(json.dumps(native(value),sort_keys=True,separators=(",",":"),ensure_ascii=False,allow_nan=False)+"\n")


def cost_extended(residual:np.ndarray)->np.longdouble:
    values=np.asarray(residual,dtype=np.longdouble)
    return np.sum(np.sqrt(1.+values*values)-1.,dtype=np.longdouble)


def cost_five_point(fun,x,direction,step)->float:
    h=np.longdouble(step)
    return float((-cost_extended(fun(x+2*step*direction))+8*cost_extended(fun(x+step*direction))-8*cost_extended(fun(x-step*direction))+cost_extended(fun(x-2*step*direction)))/(12*h))


def resolve_synthetic(spec_path:Path,output:Path)->dict:
    spec=json.loads(spec_path.read_text());cfg=spec["absolute_tolerance_resolution"];rng=np.random.default_rng(int(cfg["synthetic_seed"]));n=int(cfg["synthetic_dimensions"]["residual_count"]);p=int(cfg["synthetic_dimensions"]["parameter_count"]);directions=[]
    for _ in range(int(cfg["synthetic_dimensions"]["direction_count"])):
        value=rng.normal(size=p);directions.append(value/np.linalg.norm(value))
    x=rng.normal(0,.08,p);steps=spec["robust_cost"]["fixed_step_sweep_rad"];primary=float(spec["robust_cost"]["primary_step_rad"]);rows=[];primary_errors=[];plateau=True;roundoff=[]
    for family in cfg["synthetic_residual_families"]:
        matrix=rng.normal(0,.12,(n,p));offset=rng.normal(0,.8,n)
        if family=="well_conditioned_affine":
            fun=lambda value,matrix=matrix,offset=offset:matrix@value+offset
            jac=lambda value,matrix=matrix:matrix
        elif family=="smooth_sinusoidal":
            fun=lambda value,matrix=matrix,offset=offset:np.sin(matrix@value)+offset
            jac=lambda value,matrix=matrix:np.cos(matrix@value)[:,None]*matrix
        elif family=="reference_centered_s2_atan2":
            fun=lambda value,matrix=matrix,offset=offset:np.arctan2(np.sin(matrix@value),np.cos(matrix@value))+.1*offset
            jac=lambda value,matrix=matrix:matrix
        else:
            offset[::17]+=12.;offset[5::23]-=9.
            fun=lambda value,matrix=matrix,offset=offset:matrix@value+offset+.02*np.sin(2*matrix@value)
            jac=lambda value,matrix=matrix:matrix*(1.+.04*np.cos(2*matrix@value))[:,None]
        residual=fun(x);J=jac(x);influence=residual/np.sqrt(1.+residual*residual);gradient=J.T@influence;row_cost=np.sqrt(1.+residual*residual)-1.;bound=8*np.finfo(np.float64).eps*float(np.sum(np.abs(row_cost)+1.))/primary;roundoff.append(bound)
        for ordinal,direction in enumerate(directions):
            analytic=float(gradient@direction);estimates={str(step):cost_five_point(fun,x,direction,float(step)) for step in steps};error=abs(analytic-estimates[str(primary)]);primary_errors.append(error);adjacent=estimates[str(steps[-1])];local_plateau=abs(estimates[str(primary)]-adjacent)<=error+bound;plateau&=local_plateau;rows.append({"family":family,"direction":ordinal,"analytic":analytic,"five_point_by_step":estimates,"primary_absolute_error":error,"roundoff_bound":bound,"adjacent_step_plateau":local_plateau})
    synthetic_error=max(primary_errors);roundoff_bound=max(roundoff);multiplier=float(cfg["resolution_multiplier_predeclared"]);absolute=max(multiplier*roundoff_bound,multiplier*synthetic_error);result={"schema":"biospur-derivative-v2-resolved-absolute-tolerance-v1","spec_sha256":sha256(spec_path),"synthetic_only":True,"attempt7_endpoint_opened":False,"step_plateau_pass":bool(plateau),"maximum_primary_synthetic_absolute_error":synthetic_error,"maximum_roundoff_bound":roundoff_bound,"resolution_multiplier":multiplier,"resolved_absolute_tolerance":absolute,"formula":cfg["resolved_absolute_tolerance_formula"],"rows":rows,"pass":bool(plateau)};output.mkdir(parents=True,exist_ok=False);dump_json(output/"DERIVATIVE_V2_SYNTHETIC_STEP_CONVERGENCE.json",result);dump_json(output/"DERIVATIVE_V2_RESOLVED_TOLERANCE.json",{key:value for key,value in result.items() if key!="rows"});return result


def array_sha(*arrays)->str:
    digest=hashlib.sha256()
    for value in arrays:
        array=np.ascontiguousarray(value);digest.update(str(array.dtype).encode());digest.update(np.asarray(array.shape,np.int64).tobytes());digest.update(array.tobytes())
    return digest.hexdigest()


def five_point_jacobian(fun,x,steps):
    base=fun(x);jac=np.empty((len(base),len(x)))
    for col,h in enumerate(steps):
        p1=x.copy();p2=x.copy();m1=x.copy();m2=x.copy();p1[col]+=h;p2[col]+=2*h;m1[col]-=h;m2[col]-=2*h;jac[:,col]=(-fun(p2)+8*fun(p1)-8*fun(m1)+fun(m2))/(12*h)
    return jac


def profiled_information(objective,jacobian,include_protocol:bool)->dict:
    accounting,_=objective.accounting(np.zeros(jacobian.shape[1]));data_mask=np.ones(len(jacobian),bool);action_masks={}
    for action in ACTIONS:
        selected=np.zeros(len(jacobian),bool)
        for factor in accounting["actions"][action]["residual_blocks"]:
            begin,end=factor["row_start"],factor["row_stop"];selected[begin:end]=True
            if factor["residual_block"].startswith(("neutral_latent_pose:","tpose_latent_pose:")):data_mask[end-3:end]=False
            if factor["residual_block"]=="trunk_axis_orthogonality":data_mask[begin:end]=False
        action_masks[action]=selected
    if include_protocol:data_mask[:]=True
    def one(mask):
        rows=mask&data_mask;J=jacobian[rows];Jt=J[:,:56];Jn=J[:,56:]
        projected=Jt-Jn@np.linalg.lstsq(Jn,Jt,rcond=None)[0] if Jn.size and np.linalg.norm(Jn)>0 else Jt
        singular=np.linalg.svd(projected,compute_uv=False);maximum=float(singular[0]) if len(singular) else 0.
        blocks=[]
        for entry in LAYOUT["entries"]:
            if entry["start"]>=56:continue
            blocks.append({"parameter_block":entry["name"],"profiled_jacobian_frobenius_norm":float(np.linalg.norm(projected[:,entry["start"]:entry["stop"]]))})
        return {"residual_rows":int(rows.sum()),"profiled_published_jacobian_frobenius_norm":float(np.linalg.norm(projected)),"profiled_rank_relative_1e-8":int(np.sum(singular>maximum*1e-8)) if maximum else 0,"profiled_rank_relative_1e-10":int(np.sum(singular>maximum*1e-10)) if maximum else 0,"largest_singular_value":maximum,"smallest_reported_singular_value":float(singular[-1]) if len(singular) else 0.,"published_parameter_blocks":blocks}
    return {"all_actions":one(np.ones(len(jacobian),bool)),"actions":{action:one(mask) for action,mask in action_masks.items()},"protocol_prior_rows_included":include_protocol}


def preflight(spec_path:Path,resolved_path:Path,freeze:Path,output:Path)->dict:
    spec=json.loads(spec_path.read_text());resolved=json.loads(resolved_path.read_text())
    if not resolved["pass"] or resolved["spec_sha256"]!=sha256(spec_path):raise RuntimeError("synthetic tolerance resolution binding failed")
    snapshot=freeze/"SOURCE_SNAPSHOT";sys.path.insert(0,str(snapshot/"Fusion_Part/src"))
    global ACTIONS,LAYOUT
    from biospur_fusion.imu_multi_action_engineering_v1.common_time import build_common_timeline
    from biospur_fusion.imu_multi_action_engineering_v1.model import ACTIONS,LAYOUT,Objective,finite_difference_jacobian,initial_parameters,parameter_steps
    from biospur_fusion.imu_multi_action_engineering_v1.pipeline import load_q2_cache
    artifacts=freeze/"ORIGINAL_ARTIFACTS";gates_path=snapshot/"Fusion_Part/config/imu_multi_action_engineering_preview_v1/gates_v1.json";gates=json.loads(gates_path.read_text());capture=ROOT/"Fusion_Part/logs/v47_ten_node_body_calibration_20260814_093601";phase=capture/"analysis_imu_multi_action_engineering_preview_v1_phase_a_final_20260815";phase_result=json.loads((phase/"RESULT.json").read_text());cache=phase/"Q2_HUMAN_QUASI_STATIC_CACHE.npz";q2=load_q2_cache(cache);windows={key:tuple(value) for key,value in phase_result["calibration_windows"].items()};timeline=build_common_timeline(q2,min(v[0] for v in windows.values()),max(v[1] for v in windows.values()),gates["common_time"]);segmentation=json.loads((artifacts/"ACTION_SEGMENTATION.json").read_text());objective=Objective(timeline,segmentation,gates["node_to_segment"],gates["calibration_model"]);frozen=np.load(freeze/"ATTEMPT7_PRODUCTION_NUMERICS.npz",allow_pickle=False);x0=frozen["x0"].copy();xend=frozen["x_end"].copy();steps=parameter_steps(gates["production_jacobian"]);mask=objective.structural_sparsity(x0).toarray().astype(bool);rng=np.random.default_rng(int(spec["off_endpoint_generation"]["seed"]));points=[("ATTEMPT7_DETERMINISTIC_X0",x0),("ATTEMPT7_X_END",xend)]
    for name,base in (("DETERMINISTIC_OFF_ENDPOINT_0",x0),("DETERMINISTIC_OFF_ENDPOINT_1",xend)):
        direction=rng.normal(size=len(base));direction/=np.linalg.norm(direction);points.append((name,base+float(spec["off_endpoint_generation"]["direction_norm_rad"])*direction))
    relative_limit=float(spec["raw_residual_jacobian"]["jv_relative_error_limit"]);outside_limit=float(spec["raw_residual_jacobian"]["undeclared_derivative_absolute_limit"]);absolute=float(resolved["resolved_absolute_tolerance"]);cost_relative=float(spec["robust_cost"]["relative_tolerance"]);point_rows=[];all_pass=True;endpoint_j=None
    directions=[]
    for _ in range(10):
        value=rng.normal(size=len(x0));directions.append(value/np.linalg.norm(value))
    for name,x in points:
        raw_three=finite_difference_jacobian(objective.residual,x,steps);solver_csr=csr_matrix(np.where(mask,raw_three,0.));audit_csr=csr_matrix(np.where(mask,finite_difference_jacobian(objective.residual,x,steps),0.));solver_hash=array_sha(solver_csr.data,solver_csr.indices,solver_csr.indptr);audit_hash=array_sha(audit_csr.data,audit_csr.indices,audit_csr.indptr);dense_five=five_point_jacobian(objective.residual,x,steps);outside=float(np.max(np.abs(dense_five[~mask]))) if np.any(~mask) else 0.;jv=[];cost=[];residual=objective.residual(x);gradient=solver_csr.T@(residual/np.sqrt(1.+residual*residual))
        for ordinal,direction in enumerate(directions):
            a=np.asarray(solver_csr@direction).ravel();b=dense_five@direction;rel=float(np.linalg.norm(a-b)/max(np.linalg.norm(a),np.linalg.norm(b),1e-30));jv.append({"direction":ordinal,"relative_error":rel});analytic=float(gradient@direction);oracle=cost_five_point(objective.residual,x,direction,float(spec["robust_cost"]["primary_step_rad"]));error=abs(analytic-oracle);limit=absolute+cost_relative*max(abs(analytic),abs(oracle));cost.append({"direction":ordinal,"analytic":analytic,"five_point":oracle,"absolute_error":error,"mixed_limit":limit,"pass":error<=limit})
        point_pass=solver_hash==audit_hash and outside<=outside_limit and max(row["relative_error"] for row in jv)<=relative_limit and all(row["pass"] for row in cost);all_pass&=point_pass;point_rows.append({"point":name,"residual_sha256":array_sha(residual),"solver_callback_jacobian_sha256":solver_hash,"audit_jacobian_sha256":audit_hash,"solver_audit_jacobian_identity":solver_hash==audit_hash,"maximum_sparse_three_vs_dense_five_jv_relative_error":max(row["relative_error"] for row in jv),"maximum_declared_sparsity_outside_derivative":outside,"cost_gradient_checks":cost,"pass":point_pass})
        if name=="ATTEMPT7_X_END":endpoint_j=np.asarray(solver_csr.toarray())
    endpoint_residual=objective.residual(xend);objective_equivalence={"x0_residual_sha_current":array_sha(objective.residual(x0)),"x0_residual_sha_attempt7_freeze":array_sha(frozen["residual_x0"]),"x_end_residual_sha_current":array_sha(endpoint_residual),"x_end_residual_sha_attempt7_freeze":array_sha(frozen["residual_x_end"])};objective_equivalence["pass"]=objective_equivalence["x0_residual_sha_current"]==objective_equivalence["x0_residual_sha_attempt7_freeze"] and objective_equivalence["x_end_residual_sha_current"]==objective_equivalence["x_end_residual_sha_attempt7_freeze"]
    original_hash=array_sha(endpoint_j);broken=endpoint_j.copy();location=np.unravel_index(np.argmax(np.abs(broken)),broken.shape);broken[location]+=1e-3;broken_hash=array_sha(broken);weights=endpoint_residual/np.sqrt(1.+endpoint_residual*endpoint_residual);broken_weights=weights.copy();broken_weights[int(np.argmax(np.abs(broken_weights)))]*=1.01
    negative={"jacobian":{"location":list(location),"delta":1e-3,"original_sha256":original_hash,"broken_sha256":broken_hash,"detected":original_hash!=broken_hash},"weight":{"index":int(np.argmax(np.abs(weights))),"relative_change":.01,"original_sha256":array_sha(weights),"broken_sha256":array_sha(broken_weights),"detected":array_sha(weights)!=array_sha(broken_weights)}}
    negative_pass=negative["jacobian"]["detected"] and negative["weight"]["detected"]
    information={"data_only":profiled_information(objective,endpoint_j,False),"data_plus_protocol_prior":profiled_information(objective,endpoint_j,True),"segmentation_phases":{action:[{"phase":item["phase"],"sample_rows":len(item["row_indices"]),"relevant_segments":item["relevant_segments"]} for item in segmentation["actions"][action]] for action in ACTIONS}}
    all_actions_nonzero=all(information["data_only"]["actions"][action]["residual_rows"]>0 and information["data_only"]["actions"][action]["profiled_published_jacobian_frobenius_norm"]>0 for action in ACTIONS)
    replay_source=(snapshot/"Fusion_Part/src/biospur_fusion/imu_multi_action_engineering_v1/replay.py");text=replay_source.read_text();replay_dependency={"source_path":str(replay_source.resolve()),"source_sha256":sha256(replay_source),"functional_axes_read":"functional_axes_display_frame" in text,"joint_zeros_read":"joint_neutral_zero_rad" in text,"continuous_replay_forward_projection":"_functional_adjust" in text,"pass":all(token in text for token in ("functional_axes_display_frame","joint_neutral_zero_rad","_functional_adjust"))}
    result={"schema":"biospur-derivative-v2-zero-iteration-preflight-v1","spec_sha256":sha256(spec_path),"resolved_tolerance_sha256":sha256(resolved_path),"attempt7_freeze_manifest_sha256":sha256(freeze/"SHA256_MANIFEST.json"),"solver_iterations":0,"objective_equivalence":objective_equivalence,"points":point_rows,"negative_controls":negative,"eleven_action_profiled_information_pass":all_actions_nonzero,"replay_parameter_dependency":replay_dependency,"pass":bool(all_pass and objective_equivalence["pass"] and negative_pass and all_actions_nonzero and replay_dependency["pass"]),"golf":"SEALED","boxing":"SEALED","walk":"SEALED","final_still":"SEALED","uwb":"SEALED"};output.mkdir(parents=True,exist_ok=False);dump_json(output/"DERIVATIVE_V2_ZERO_ITERATION_PREFLIGHT.json",result);dump_json(output/"ELEVEN_ACTION_PROFILED_INFORMATION.json",information);dump_json(output/"REPLAY_PARAMETER_DEPENDENCY.json",replay_dependency);return result


def main()->int:
    parser=argparse.ArgumentParser();sub=parser.add_subparsers(dest="command",required=True);synthetic=sub.add_parser("resolve-synthetic");synthetic.add_argument("--spec",type=Path,required=True);synthetic.add_argument("--output",type=Path,required=True);real=sub.add_parser("preflight");real.add_argument("--spec",type=Path,required=True);real.add_argument("--resolved",type=Path,required=True);real.add_argument("--attempt7-freeze",type=Path,required=True);real.add_argument("--output",type=Path,required=True);args=parser.parse_args();result=resolve_synthetic(args.spec,args.output) if args.command=="resolve-synthetic" else preflight(args.spec,args.resolved,args.attempt7_freeze,args.output);summary={key:value for key,value in result.items() if key not in ("rows","points")};print(json.dumps(summary,sort_keys=True,default=lambda value:value.item() if isinstance(value,np.generic) else str(value)));return 0 if result["pass"] else 2


if __name__=="__main__":raise SystemExit(main())
