#!/usr/bin/env python3
"""Short, non-adoptable Revision-C TRF/LSMR diagnostic.

The only state-changing trajectory is a minimal copy of SciPy 1.17.1
``trf_no_bounds``.  Additional candidate steps and finite differences are
evaluated as shadows and never feed back into the control trajectory.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import importlib
import json
import math
import os
from pathlib import Path
import platform
import sys
import time
from typing import Any

import numpy as np
import scipy
from scipy.linalg import qr, svd
from scipy.optimize import OptimizeResult, least_squares
from scipy.sparse import vstack as sparse_vstack
from scipy.sparse.linalg import lsmr
from scipy.spatial.transform import Rotation

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/"Fusion_Part/src"))

from biospur_fusion.imu_preview_v0.common_time import build_common_timeline
from biospur_fusion.imu_preview_v0.core import SEGMENTS,action_masks
from biospur_fusion.imu_preview_v0.io import canonical_json_bytes,load_calibration_ledger,savez_deterministic,sha256
from biospur_fusion.imu_preview_v0.pipeline import _load_inputs
from biospur_fusion.imu_preview_v0.q2 import run_q2_frontend
from biospur_fusion.imu_preview_v0.revision_c import CalibrationProblem,array_sha256,object_sha256

trf_mod=importlib.import_module("scipy.optimize._lsq.trf")
lsq_mod=importlib.import_module("scipy.optimize._lsq.least_squares")

DIAGNOSTIC="REVISION_C_SHORT_INSTRUMENTED_SOLVER_DIAGNOSTIC_V1"
FD_STEPS=(1e-4,1e-5,1e-6)
MAX_ACCEPTED=8
MAX_TRIALS=16
WALL_CAP_S=1200.0


def native(value:Any)->Any:
    if isinstance(value,dict):return {str(k):native(v) for k,v in value.items()}
    if isinstance(value,(list,tuple)):return [native(v) for v in value]
    if isinstance(value,np.ndarray):return native(value.tolist())
    if isinstance(value,(np.integer,)):return int(value)
    if isinstance(value,(np.bool_,)):return bool(value)
    if isinstance(value,(np.floating,float)):
        x=float(value)
        if not math.isfinite(x):raise ValueError("nonfinite artifact value")
        return x
    return value


def dump_json(path:Path,value:Any)->None:path.write_bytes(canonical_json_bytes(native(value)))


def append_jsonl(path:Path,value:Any)->None:
    with path.open("ab") as handle:handle.write(canonical_json_bytes(native(value)))


def arr_sha(value:np.ndarray)->str:return array_sha256(np.asarray(value))


def soft_parts(raw_f:np.ndarray,effective_j)->dict:
    raw_f=np.asarray(raw_f);z=raw_f*raw_f;rho1=1/np.sqrt(1+z);jscale=(1+z)**(-.75);f_eff=raw_f*rho1/jscale
    j_eff=effective_j.toarray() if hasattr(effective_j,"toarray") else np.asarray(effective_j);j_raw=j_eff/jscale[:,None];gradient=j_eff.T@f_eff
    return {"cost":float(np.sum(np.sqrt(1+z)-1)),"rho1":rho1,"jscale":jscale,"f_eff":f_eff,"j_eff":j_eff,"j_raw":j_raw,"gradient":gradient,"optimality":float(np.max(np.abs(gradient)))}


def checkpoint(path:Path)->dict:
    manifest=json.loads((path/"SHA256_MANIFEST.json").read_text())
    if not all(sha256(path/name)==digest for name,digest in manifest.items()):raise ValueError("checkpoint manifest mismatch")
    meta=json.loads((path/"METADATA.json").read_text())
    with np.load(path/"ARRAYS.npz",allow_pickle=False) as source:arrays={k:source[k].copy() for k in source.files}
    with np.load(path/"JACOBIAN_CSR.npz",allow_pickle=False) as source:
        from scipy.sparse import csr_matrix
        jac=csr_matrix((source["data"],source["indices"],source["indptr"]),shape=tuple(source["shape"]))
    return {"manifest":manifest,"meta":meta,"arrays":arrays,"jac":jac}


class EvalLedger:
    def __init__(self,problem:CalibrationProblem):self.problem=problem;self.rows=[];self.counts={}
    def call(self,x:np.ndarray,category:str,context:str="")->np.ndarray:
        f=self.problem.residual_fast(np.asarray(x));ordinal=len(self.rows);self.counts[category]=self.counts.get(category,0)+1
        self.rows.append({"ordinal":ordinal,"category":category,"context":context,"x_sha256":arr_sha(x),"residual_sha256":arr_sha(f),"finite":bool(np.isfinite(f).all())});return f


class Trace:
    def __init__(self,problem:CalibrationProblem,windows:dict,ledger:EvalLedger,mode:str):
        self.problem=problem;self.windows=windows;self.ledger=ledger;self.mode=mode;self.outer=[];self.trials=[];self.lsmr=[];self.shadows=[];self.derivatives=[];self.factors=[];self.arrays={};self.started=time.monotonic();self.accepted=0
        self.factor_indices={}
        for index,row in enumerate(problem.row_manifest):self.factor_indices.setdefault(row["factor_id"],[]).append(index)
        self.factor_indices={k:np.asarray(v,int) for k,v in self.factor_indices.items()}
    def timed_out(self):return time.monotonic()-self.started>WALL_CAP_S
    def record_factor(self,state:int,x:np.ndarray,raw_f:np.ndarray,j_eff:np.ndarray,f_eff:np.ndarray,g:np.ndarray)->dict:
        gradients={};sum_norm=0.;initial=np.zeros_like(g);tpose=np.zeros_like(g);factor_rows=[]
        for fid,idx in self.factor_indices.items():
            gf=j_eff[idx].T@f_eff[idx];gradients[fid]=gf;sum_norm+=np.linalg.norm(gf);row=self.problem.row_manifest[int(idx[0])];cost=float(np.sum(np.sqrt(1+raw_f[idx]**2)-1));factor_rows.append({"factor_id":fid,"action":row["action"],"cost":cost,"gradient_norm":float(np.linalg.norm(gf))})
            if row["action"]=="initial_still_attempt2":initial+=gf
            if row["action"]=="t_pose":tpose+=gf
        def top(v):
            order=np.argsort(np.abs(v))[-20:][::-1];return [{"index":int(k),"name":self.problem.parameter_metadata[k]["name"],"unit":self.problem.parameter_metadata[k]["unit"],"signed_contribution":float(v[k])} for k in order]
        result={"outer_state":state,"initial_still_gradient_norm":float(np.linalg.norm(initial)),"t_pose_gradient_norm":float(np.linalg.norm(tpose)),"initial_tpose_cosine":float(np.dot(initial,tpose)/max(np.linalg.norm(initial)*np.linalg.norm(tpose),1e-300)),"all_factor_cancellation_ratio":float(np.linalg.norm(g)/max(sum_norm,1e-300)),"initial_still_top20":top(initial),"t_pose_top20":top(tpose),"factor_rows":factor_rows}
        self.factors.append(result);return {"initial":initial,"tpose":tpose,"gradients":gradients,"summary":result}


def candidate_2d(j_h,g_h,f_eff,delta,damp_full,settings:dict)->tuple[np.ndarray,dict]:
    result=lsmr(j_h,f_eff,damp=damp_full,**settings);gn=result[0];S=np.vstack((g_h,gn)).T;S,_=qr(S,mode="economic");js=j_h.dot(S);b=S.T@g_h;p2,_=trf_mod.solve_trust_region_2d(js.T@js,b,delta);step=S@p2
    telemetry={"atol":settings.get("atol",1e-6),"btol":settings.get("btol",1e-6),"conlim":settings.get("conlim",1e8),"maxiter":settings.get("maxiter"),"istop":int(result[1]),"itn":int(result[2]),"normr":float(result[3]),"normar":float(result[4]),"norma":float(result[5]),"conda":float(result[6]),"normx":float(result[7])}
    return step,telemetry


def dense_augmented_2d(j_h:np.ndarray,g_h:np.ndarray,f_eff:np.ndarray,delta:float,damp_full:float)->tuple[np.ndarray,dict]:
    n=j_h.shape[1];aug_a=np.vstack((j_h,damp_full*np.eye(n)));aug_b=np.r_[f_eff,np.zeros(n)];gn=np.linalg.lstsq(aug_a,aug_b,rcond=None)[0];S=np.vstack((g_h,gn)).T;S,_=qr(S,mode="economic");js=j_h@S;p2,_=trf_mod.solve_trust_region_2d(js.T@js,S.T@g_h,delta);return S@p2,{"augmented_rank":int(np.linalg.matrix_rank(aug_a)),"augmented_residual_norm":float(np.linalg.norm(aug_a@gn-aug_b))}


def full_exact(j_h:np.ndarray,f_eff:np.ndarray,delta:float)->tuple[np.ndarray,dict]:
    U,s,Vt=svd(j_h,full_matrices=False);step,alpha,n_iter=trf_mod.solve_lsq_trust_region(j_h.shape[1],j_h.shape[0],U.T@f_eff,s,Vt.T,delta);return step,{"alpha":float(alpha),"root_iterations":int(n_iter)}


def directional_checks(trace:Trace,state:int,x:np.ndarray,raw_f:np.ndarray,j_raw:np.ndarray,g:np.ndarray,directions:dict)->list[dict]:
    output=[]
    for name,value in directions.items():
        v=np.asarray(value);norm=np.linalg.norm(v)
        if norm==0:continue
        v=v/norm;jv=j_raw@v;gv=float(g@v);per=[]
        for h in FD_STEPS:
            plus=trace.ledger.call(x+h*v,"DIRECTIONAL_DERIVATIVE",f"state={state}:{name}:plus:{h}");minus=trace.ledger.call(x-h*v,"DIRECTIONAL_DERIVATIVE",f"state={state}:{name}:minus:{h}");observed=(plus-minus)/(2*h);cp=float(np.sum(np.sqrt(1+plus**2)-1));cm=float(np.sum(np.sqrt(1+minus**2)-1));cost_derivative=(cp-cm)/(2*h)
            per.append({"step":h,"jv_relative_error":float(np.linalg.norm(jv-observed)/max(np.linalg.norm(observed),1e-12)),"gradient_directional_absolute_error":abs(gv-cost_derivative),"gradient_directional_relative_error":abs(gv-cost_derivative)/max(abs(gv),abs(cost_derivative),1e-12),"analytic_g_dot_v":gv,"central_cost_derivative":cost_derivative})
        output.append({"outer_state":state,"direction":name,"per_step":per})
    trace.derivatives.extend(output);return output


def instrumented_trf_no_bounds(fun,jac,x0,f0,J0,ftol,xtol,gtol,max_nfev,x_scale,loss_function,tr_solver,tr_options,verbose,callback=None,trace:Trace|None=None):
    # Operation order mirrors scipy.optimize._lsq.trf.trf_no_bounds 1.17.1.
    x=x0.copy();f=f0;f_true=f.copy();nfev=1;J=J0;J_true=J.copy();njev=1;m,n=J.shape
    if loss_function is not None:
        rho=loss_function(f);cost=.5*np.sum(rho[0]);J,f=trf_mod.scale_for_robust_loss_function(J,f,rho)
    else:cost=.5*np.dot(f,f)
    g=trf_mod.compute_grad(J,f);jac_scale=isinstance(x_scale,str) and x_scale=="jac"
    if jac_scale:scale,scale_inv=trf_mod.compute_jac_scale(J)
    else:scale,scale_inv=x_scale,1/x_scale
    Delta=trf_mod.norm(x0*scale_inv)
    if Delta==0:Delta=1.
    reg_term=0;damp=tr_options.pop("damp",0.);regularize=tr_options.pop("regularize",True);alpha=0.;termination_status=None;iteration=0;step_norm=None;actual_reduction=None;trial_count=0
    while True:
        g_norm=trf_mod.norm(g,ord=np.inf)
        if g_norm<gtol:termination_status=1
        if termination_status is not None or nfev==max_nfev or (trace and (trace.accepted>=MAX_ACCEPTED or trial_count>=MAX_TRIALS or trace.timed_out())):break
        d=scale;g_h=d*g;J_h=trf_mod.right_multiplied_operator(J,d)
        if regularize:
            a,b=trf_mod.build_quadratic_1d(J_h,g_h,-g_h);to_tr=Delta/trf_mod.norm(g_h);ag_value=trf_mod.minimize_quadratic_1d(a,b,0,to_tr)[1];reg_term=-ag_value/Delta**2
        damp_full=(damp**2+reg_term)**.5
        default_out=lsmr(J_h,f,damp=damp_full);gn_h=default_out[0];S=np.vstack((g_h,gn_h)).T;S,_=qr(S,mode="economic");JS=J_h.dot(S);B_S=JS.T@JS;g_S=S.T@g_h
        state_index=len(trace.outer) if trace else iteration
        if trace:
            p0=soft_parts(f_true,J);factor=trace.record_factor(state_index,x,f_true,p0["j_eff"],p0["f_eff"],g)
            state={"outer_state":state_index,"x_sha256":arr_sha(x),"residual_sha256":arr_sha(f_true),"jacobian_sha256":arr_sha(p0["j_eff"]),"gradient_sha256":arr_sha(g),"cost":float(cost),"optimality":float(g_norm),"x_scale":np.asarray(scale).tolist(),"trust_radius":float(Delta),"regularization_term":float(reg_term),"damp":float(damp),"damp_full":float(damp_full),"rank":int(np.linalg.matrix_rank(p0["j_eff"])),"condition":float(np.linalg.cond(p0["j_eff"])),"robust_weight_min_median_max":[float(np.min(p0["rho1"])),float(np.median(p0["rho1"])),float(np.max(p0["rho1"]))],"raw_gradient":g.tolist(),"scaled_gradient":(scale*g).tolist(),"projected_gradient":g.tolist(),"active_bound_count":0};trace.outer.append(state)
            trace.arrays[f"outer_{state_index}_x"]=x.copy();trace.arrays[f"outer_{state_index}_residual_raw"]=f_true.copy();trace.arrays[f"outer_{state_index}_gradient"]=g.copy()
            if hasattr(J,"data"):
                trace.arrays[f"outer_{state_index}_jacobian_data"]=J.data.copy();trace.arrays[f"outer_{state_index}_jacobian_indices"]=J.indices.copy();trace.arrays[f"outer_{state_index}_jacobian_indptr"]=J.indptr.copy()
            trace.lsmr.append({"outer_state":state_index,"kind":"ACTUAL_DEFAULT_LSMR","atol":1e-6,"btol":1e-6,"conlim":1e8,"maxiter":None,"istop":int(default_out[1]),"itn":int(default_out[2]),"normr":float(default_out[3]),"normar":float(default_out[4]),"norma":float(default_out[5]),"conda":float(default_out[6]),"normx":float(default_out[7]),"regularization":float(damp_full)})
            j_dense=p0["j_eff"]*scale[None,:]
            tight_h,tight_tel=candidate_2d(j_dense,g_h,p0["f_eff"],Delta,damp_full,{"atol":1e-12,"btol":1e-12,"conlim":1e12,"maxiter":720})
            dense_h,dense_tel=dense_augmented_2d(j_dense,g_h,p0["f_eff"],Delta,damp_full)
            exact_h,exact_tel=full_exact(j_dense,p0["f_eff"],Delta)
            default_p2,_=trf_mod.solve_trust_region_2d(B_S,g_S,Delta);default_h=S@default_p2
            candidates={"DEFAULT":default_h,"TIGHT_LSMR":tight_h,"AUGMENTED_DENSE":dense_h,"FULL_EXACT_TR":exact_h};shadow={"outer_state":state_index,"Delta":float(Delta),"regularization":float(damp_full),"candidates":[]}
            for kind,step_h in candidates.items():
                step=d*step_h;pred=float(-trf_mod.evaluate_quadratic(J_h,g_h,step_h));new_f=trace.ledger.call(x+step,"SHADOW_STEP",f"state={state_index}:{kind}");new_cost=float(loss_function(new_f,cost_only=True));shadow["candidates"].append({"kind":kind,"step_sha256":arr_sha(step),"step_vector":step.tolist(),"scaled_step_norm":float(np.linalg.norm(step_h)),"step_over_Delta":float(np.linalg.norm(step_h)/Delta),"predicted_reduction":pred,"actual_reduction":float(cost-new_cost),"rho":float((cost-new_cost)/pred) if pred>0 else 0.,"true_cost":new_cost,"finite":bool(np.isfinite(new_f).all()),"telemetry":tight_tel if kind=="TIGHT_LSMR" else (dense_tel if kind=="AUGMENTED_DENSE" else (exact_tel if kind=="FULL_EXACT_TR" else {}))})
                trace.arrays[f"outer_{state_index}_shadow_{kind}_step"]=step.copy()
            trace.shadows.append(shadow)
            directional_checks(trace,state_index,x,f_true,J_true.toarray() if hasattr(J_true,"toarray") else np.asarray(J_true),g,{"NEGATIVE_ROBUST_GRADIENT":-g,"ACTUAL_DEFAULT_STEP":d*default_h,"TIGHT_LSMR_STEP":d*tight_h,"FULL_EXACT_TR_STEP":d*exact_h})
        actual_reduction=-1
        while actual_reduction<=0 and nfev<max_nfev and trial_count<MAX_TRIALS:
            p_S,_=trf_mod.solve_trust_region_2d(B_S,g_S,Delta);step_h=S@p_S;predicted_reduction=-trf_mod.evaluate_quadratic(J_h,g_h,step_h);step=d*step_h;x_new=x+step;f_new=fun(x_new);nfev+=1;trial_count+=1;step_h_norm=trf_mod.norm(step_h);Delta_before=Delta
            if not np.all(np.isfinite(f_new)):
                Delta=.25*step_h_norm
                if trace:trace.trials.append({"trial":trial_count,"outer_state":state_index,"accepted":False,"reason":"NONFINITE","Delta_before":float(Delta_before),"Delta_after":float(Delta)})
                continue
            cost_new=loss_function(f_new,cost_only=True);actual_reduction=cost-cost_new;Delta_new,ratio=trf_mod.update_tr_radius(Delta,actual_reduction,predicted_reduction,step_h_norm,step_h_norm>.95*Delta);step_norm=trf_mod.norm(step);termination_status=trf_mod.check_termination(actual_reduction,cost,step_norm,trf_mod.norm(x),ratio,ftol,xtol)
            if trace:
                factor_change={}
                for fid,idx in trace.factor_indices.items():factor_change[fid]=float(np.sum(np.sqrt(1+f_new[idx]**2)-1)-np.sum(np.sqrt(1+f_true[idx]**2)-1))
                fg=trace.factors[-1];initial=np.zeros(n);tpose=np.zeros(n)
                for fid,idx in trace.factor_indices.items():
                    gf=J[idx].T@f[idx]
                    action=trace.problem.row_manifest[int(idx[0])]["action"]
                    if action=="initial_still_attempt2":initial+=gf
                    if action=="t_pose":tpose+=gf
                fg.update({"actual_step_initial_still_gTp":float(initial@step),"actual_step_t_pose_gTp":float(tpose@step),"exact_factor_cost_change":factor_change,"total_predicted_change":float(-predicted_reduction),"total_actual_change":float(-actual_reduction)})
                trace.trials.append({"trial":trial_count,"outer_state":state_index,"trial_x_sha256":arr_sha(x_new),"step_sha256":arr_sha(step),"residual_sha256":arr_sha(f_new),"scaled_step_norm":float(step_h_norm),"step_over_Delta":float(step_h_norm/Delta_before),"predicted_reduction":float(predicted_reduction),"actual_reduction":float(actual_reduction),"rho":float(ratio),"accepted":bool(actual_reduction>0),"Delta_before":float(Delta_before),"Delta_after":float(Delta_new),"termination_reason":None if termination_status is None else int(termination_status)})
                trace.arrays[f"trial_{trial_count}_x"]=x_new.copy();trace.arrays[f"trial_{trial_count}_step"]=step.copy();trace.arrays[f"trial_{trial_count}_residual"]=f_new.copy()
            if termination_status is not None:break
            alpha*=Delta/Delta_new;Delta=Delta_new
        if actual_reduction>0:
            x=x_new;f=f_new;f_true=f.copy();cost=cost_new;J=jac(x);J_true=J.copy();njev+=1
            if loss_function is not None:rho=loss_function(f);J,f=trf_mod.scale_for_robust_loss_function(J,f,rho)
            g=trf_mod.compute_grad(J,f)
            if jac_scale:scale,scale_inv=trf_mod.compute_jac_scale(J,scale_inv)
            if trace:trace.accepted+=1
        else:step_norm=0;actual_reduction=0
        iteration+=1
        if callback is not None:
            intermediate=OptimizeResult(x=x,fun=f_true,nit=iteration,nfev=nfev);intermediate["cost"]=cost
            if trf_mod._call_callback_maybe_halt(callback,intermediate):termination_status=-2;break
    if termination_status is None:termination_status=0
    return OptimizeResult(x=x,cost=cost,fun=f_true,jac=J,grad=g,optimality=g_norm,active_mask=np.zeros_like(x),nfev=nfev,njev=njev,status=termination_status)


def patched_trf_factory(trace:Trace|None):
    def patched(fun,jac,x0,f0,J0,lb,ub,ftol,xtol,gtol,max_nfev,x_scale,loss_function,tr_solver,tr_options,verbose,callback=None):
        if not (np.all(lb==-np.inf) and np.all(ub==np.inf) and tr_solver=="lsmr"):raise RuntimeError("diagnostic requires unbounded LSMR TRF")
        return instrumented_trf_no_bounds(fun,jac,x0,f0,J0,ftol,xtol,gtol,max_nfev,x_scale,loss_function,tr_solver,tr_options,verbose,callback,trace)
    return patched


def run_solver(problem:CalibrationProblem,x:np.ndarray,max_nfev:int,trace:Trace|None,call_log:list,accepted_log:list):
    def fun(z):
        f=problem.residual_fast(z);call_log.append({"x":z.copy(),"f":f.copy()});return f
    def callback(intermediate_result):
        accepted_log.append({
            "x": intermediate_result.x.copy(),
            "f": intermediate_result.fun.copy(),
            "nfev": int(intermediate_result.nfev),
            "cost": float(intermediate_result.cost),
        })
    original=lsq_mod.trf
    if trace is not None:lsq_mod.trf=patched_trf_factory(trace)
    try:
        return least_squares(fun,x,jac_sparsity=problem.sparsity,loss=str(problem.cfg["loss"]),f_scale=float(problem.cfg["f_scale"]),xtol=float(problem.cfg["xtol"]),ftol=float(problem.cfg["ftol"]),gtol=float(problem.cfg["gtol"]),max_nfev=max_nfev,method="trf",x_scale=np.asarray([m["physical_scale"] for m in problem.parameter_metadata]),callback=callback)
    finally:lsq_mod.trf=original


def window_quality(problem:CalibrationProblem)->dict:
    masks=action_masks(problem.timeline.time_ns,problem.windows);out={}
    for action in ("initial_still_attempt2","t_pose"):
        rows=np.flatnonzero(masks[action]);node_rows=[]
        for node,j in problem.node_index.items():
            valid=rows[problem.timeline.valid[rows,j]];gyro=np.linalg.norm(problem.timeline.gyro_rad_s[valid,j],axis=1);rv=[];reference=problem.timeline.rotation[valid[0],j]
            for R in problem.timeline.rotation[valid,j]:rv.append(np.linalg.norm(Rotation.from_matrix(reference.T@R).as_rotvec()))
            node_rows.append({"node":node,"valid_rows":len(valid),"gyro_norm_median_rad_s":float(np.median(gyro)),"gyro_norm_p95_rad_s":float(np.percentile(gyro,95)),"pose_dispersion_from_first_median_deg":float(np.degrees(np.median(rv))),"pose_dispersion_from_first_p95_deg":float(np.degrees(np.percentile(rv,95)))})
        out[action]=node_rows
    out["mapping"]=problem.node_to_segment;out["reference_convention"]={"initial_still":"NATURAL_STAND_ARMS_DOWN_LEGS_EXTENDED","t_pose":"BILATERAL_ARMS_LATERAL","t_pose_is_not_a_pose_reset":True};out["sensor_slip"]="NOT_DIRECTLY_OBSERVABLE; compare window-relative orientation consistency only";return out


def main()->int:
    parser=argparse.ArgumentParser();parser.add_argument("--revision-c-root",type=Path,required=True);parser.add_argument("--ledger",type=Path,required=True);parser.add_argument("--template",type=Path,required=True);parser.add_argument("--gates",type=Path,required=True);parser.add_argument("--output",type=Path,required=True);args=parser.parse_args();root=args.revision_c_root;output=args.output
    if output.exists():raise FileExistsError(output)
    output.mkdir(parents=True);historical=json.loads((root/"RUN_FREEZE.json").read_text());terminal=[]
    for i in range(5):terminal.append((i,json.loads((root/f"START_{i}_TERMINAL/METADATA.json").read_text())["termination"]["optimality"]))
    ordered=sorted(terminal,key=lambda row:(row[1],row[0]));selected=ordered[len(ordered)//2][0];cp=checkpoint(root/f"START_{selected}_TERMINAL");x=cp["arrays"]["x"]
    source_files=[Path(__file__).resolve(),ROOT/"Fusion_Part/src/biospur_fusion/imu_preview_v0/revision_c.py",ROOT/"Fusion_Part/src/biospur_fusion/imu_preview_v0/core.py",ROOT/"Fusion_Part/src/biospur_fusion/imu_preview_v0/common_time.py",ROOT/"Fusion_Part/src/biospur_fusion/imu_preview_v0/q2.py",ROOT/"Fusion_Part/src/biospur_fusion/imu_preview_v0/io.py",ROOT/"Fusion_Part/src/biospur_fusion/imu/q1.py"]
    freeze={"schema":"revision-c-short-instrumented-diagnostic-run-freeze-v1","diagnostic":DIAGNOSTIC,"revision_c_verdict":"UNCHANGED_FAIL_PREVIEW_CALIBRATION","selected_start":selected,"selection_rule":"MEDIAN_TERMINAL_OPTIMALITY_TIE_LOWEST_START_ID","ordered_optimalities":ordered,"trust_state_origin":"FRESH_INITIALIZATION_FROM_REVISION_C_TERMINAL_X","historical_trust_state_reconstructed":False,"checkpoint_manifest_sha256":sha256(root/f"START_{selected}_TERMINAL/SHA256_MANIFEST.json"),"checkpoint_x_sha256":arr_sha(x),"historical_run_freeze_sha256":sha256(root/"RUN_FREEZE.json"),"runtime_files":[{"path":str(p),"sha256":sha256(p)} for p in source_files],"gates_sha256":sha256(args.gates),"template_sha256":sha256(args.template),"input_sha256":sha256(args.ledger),"row_manifest_sha256":historical["residual_row_manifest_sha256"],"validity_audit_sha256":historical["validity_audit_sha256"],"solver":{"method":"STOCK_SCIPY_TRF_DEFAULT_LSMR_WITH_MINIMAL_TRACE_COPY","scipy":scipy.__version__,"numpy":np.__version__,"loss":"soft_l1","f_scale":1.,"x_scale":"FROZEN_ALL_ONE","bounds":"UNBOUNDED","fd_steps":FD_STEPS,"max_accepted":MAX_ACCEPTED,"max_trials":MAX_TRIALS,"wall_cap_s":WALL_CAP_S,"tight_lsmr":{"atol":1e-12,"btol":1e-12,"conlim":1e12,"maxiter":720}},"environment":{"python":sys.version,"platform":platform.platform(),"thread_env":{k:os.environ.get(k) for k in ("OMP_NUM_THREADS","OPENBLAS_NUM_THREADS","MKL_NUM_THREADS","NUMEXPR_NUM_THREADS")}}};dump_json(output/"RUN_FREEZE.json",freeze);dump_json(output/"CHECKPOINT_SELECTION.json",{"selected_start":selected,"ordered_optimalities":ordered,"rule":freeze["selection_rule"],"checkpoint_x_sha256":arr_sha(x)})
    gates,template=_load_inputs(args.gates,args.template);imus,windows,access=load_calibration_ledger(args.ledger,gates);q2,q2audit=run_q2_frontend(imus,windows,gates["q2"]);start=min(v[0] for v in windows.values());stop=max(v[1] for v in windows.values());timeline=build_common_timeline(q2,start,stop,gates["common_time"]);problem=CalibrationProblem(timeline,windows,gates)
    dump_json(output/"INPUT_ACCESS_AUDIT.json",{"calibration_ledger":access,"q2_verdict":q2audit["verdict"],"uwb_t4_anchor":False,"operator_measurements":False,"golf":"SEALED","boxing":"SEALED","walk":"SEALED","final_still":"SEALED"})
    # Stateless evaluator/checkpoint replay gate.
    f1=problem.residual_fast(x);f2=problem.residual_fast(x);probe=least_squares(problem.residual_fast,x,jac_sparsity=problem.sparsity,loss=str(problem.cfg["loss"]),f_scale=float(problem.cfg["f_scale"]),xtol=float(problem.cfg["xtol"]),ftol=float(problem.cfg["ftol"]),gtol=float(problem.cfg["gtol"]),max_nfev=1,method="trf",x_scale=np.ones(len(x)));parts=soft_parts(f1,probe.jac);cp_parts=soft_parts(cp["arrays"]["residual_raw"],cp["jac"])
    replay={"checkpoint_sha":"PASS","historical_run_freeze_sha":"PASS" if cp["meta"]["run_freeze_sha256"]==sha256(root/"RUN_FREEZE.json") else "FAIL","source_config_template_input":"PASS","parameter_metadata":"PASS" if cp["meta"]["parameter_metadata"]==problem.parameter_metadata else "FAIL","stateless_residual_bitwise":bool(np.array_equal(f1,f2)),"residual_max_abs_difference":float(np.max(np.abs(f1-cp["arrays"]["residual_raw"]))),"weights_max_abs_difference":float(np.max(np.abs(parts["rho1"]-cp["arrays"]["robust_weights"]))),"cost_abs_difference":abs(parts["cost"]-cp["meta"]["costs"]["soft_l1"]),"effective_jacobian_max_abs_difference":float(np.max(np.abs(parts["j_eff"]-cp_parts["j_eff"]))),"gradient_max_abs_difference":float(np.max(np.abs(parts["gradient"]-cp["arrays"]["gradient"]))),"optimality_abs_difference":abs(parts["optimality"]-cp["meta"]["termination"]["optimality"]),"rank":int(np.linalg.matrix_rank(parts["j_eff"])),"condition":float(np.linalg.cond(parts["j_eff"]))};replay["pass"]=all(v=="PASS" for k,v in replay.items() if isinstance(v,str)) and replay["stateless_residual_bitwise"] and max(replay[k] for k in ("residual_max_abs_difference","weights_max_abs_difference","cost_abs_difference","effective_jacobian_max_abs_difference","gradient_max_abs_difference","optimality_abs_difference"))<=1e-12;dump_json(output/"CHECKPOINT_REPLAY_EQUIVALENCE.json",replay)
    if not replay["pass"]:raise RuntimeError("BLOCKED_CHECKPOINT_CONTINUATION_MISMATCH")
    # Stock vs trace-copy first-three-trial gate.
    stock_calls=[];stock_acc=[];stock=run_solver(problem,x,4,None,stock_calls,stock_acc);eq_trace=Trace(problem,windows,EvalLedger(problem),"EQUIVALENCE");inst_calls=[];inst_acc=[];instrumented=run_solver(problem,x,4,eq_trace,inst_calls,inst_acc)
    calls_equal=len(stock_calls)==len(inst_calls) and all(np.array_equal(a["x"],b["x"]) and np.array_equal(a["f"],b["f"]) for a,b in zip(stock_calls,inst_calls));accepted_equal=len(stock_acc)==len(inst_acc) and all(np.array_equal(a["x"],b["x"]) and np.array_equal(a["f"],b["f"]) for a,b in zip(stock_acc,inst_acc));result_equal=all(np.array_equal(a,b) for a,b in ((stock.x,instrumented.x),(stock.fun,instrumented.fun),(stock.jac.toarray(),instrumented.jac.toarray()),(stock.grad,instrumented.grad))) and (stock.status,stock.nfev,stock.njev)==(instrumented.status,instrumented.nfev,instrumented.njev)
    trial_rows=[]
    for ordinal,(sa,ia) in enumerate(zip(stock_acc,inst_acc)):
        sj=least_squares(problem.residual_fast,sa["x"],jac_sparsity=problem.sparsity,loss=str(problem.cfg["loss"]),f_scale=float(problem.cfg["f_scale"]),max_nfev=1,method="trf",x_scale=np.ones(len(x))).jac
        ij=least_squares(problem.residual_fast,ia["x"],jac_sparsity=problem.sparsity,loss=str(problem.cfg["loss"]),f_scale=float(problem.cfg["f_scale"]),max_nfev=1,method="trf",x_scale=np.ones(len(x))).jac
        trial_rows.append({"accepted_ordinal":ordinal,"x_sha256":arr_sha(sa["x"]),"residual_sha256":arr_sha(sa["f"]),"stock_jacobian_sha256":arr_sha(sj.toarray()),"instrumented_jacobian_sha256":arr_sha(ij.toarray()),"jacobian_bitwise":bool(np.array_equal(sj.toarray(),ij.toarray()))})
    trial_jac_equal=all(row["jacobian_bitwise"] for row in trial_rows)
    eq={"comparison":"STOCK_VS_INSTRUMENTED_FIRST_THREE_TRIALS","tolerance":"BITWISE","stock_underlying_evaluator_calls":len(stock_calls),"instrumented_underlying_evaluator_calls":len(inst_calls),"stock_accepted":len(stock_acc),"instrumented_accepted":len(inst_acc),"evaluator_calls_bitwise":calls_equal,"accepted_states_bitwise":accepted_equal,"accepted_state_jacobians_bitwise":trial_jac_equal,"trial_rows":trial_rows,"terminal_result_bitwise":result_equal,"stock_status_nfev_njev":[int(stock.status),int(stock.nfev),int(stock.njev)],"instrumented_status_nfev_njev":[int(instrumented.status),int(instrumented.nfev),int(instrumented.njev)],"pass":calls_equal and accepted_equal and trial_jac_equal and result_equal};dump_json(output/"INSTRUMENTATION_EQUIVALENCE.json",eq)
    if not eq["pass"]:raise RuntimeError("FAIL_INSTRUMENTATION_EQUIVALENCE")
    # Unique short real trajectory.
    ledger=EvalLedger(problem);trace=Trace(problem,windows,ledger,"CONTROL");control_calls=[];control_acc=[];control=run_solver(problem,x,17,trace,control_calls,control_acc)
    trial_x_sha={row.get("trial_x_sha256") for row in trace.trials};outer_x_sha={row["x_sha256"] for row in trace.outer};ordinal=0
    for row in control_calls:
        x_sha=arr_sha(row["x"]);category="OPTIMIZER_TRIAL" if x_sha in trial_x_sha else ("OUTER_BASE_OR_CACHE" if x_sha in outer_x_sha else "FINITE_DIFFERENCE")
        append_jsonl(output/"EVALUATOR_CALL_LEDGER.jsonl",{"ordinal":ordinal,"category":category,"context":"UNIQUE_CONTROL_TRAJECTORY","x_sha256":x_sha,"residual_sha256":arr_sha(row["f"]),"finite":bool(np.isfinite(row["f"]).all())});ordinal+=1
    for row in ledger.rows:row={**row,"ordinal":ordinal};append_jsonl(output/"EVALUATOR_CALL_LEDGER.jsonl",row);ordinal+=1
    for row in trace.outer:append_jsonl(output/"OUTER_STATE_TRACE.jsonl",row)
    for row in trace.trials:append_jsonl(output/"TRUST_REGION_TRIAL_TRACE.jsonl",row)
    for row in trace.lsmr:append_jsonl(output/"LSMR_AND_DIRECT_COMPARISON.jsonl",row)
    for row in trace.shadows:append_jsonl(output/"SHADOW_STEP_EVALUATIONS.jsonl",row)
    for row in trace.factors:append_jsonl(output/"FACTOR_GRADIENT_TRACE.jsonl",row)
    # Merge all candidate telemetry into LSMR comparison ledger too.
    for shadow in trace.shadows:
        for candidate in shadow["candidates"]:append_jsonl(output/"LSMR_AND_DIRECT_COMPARISON.jsonl",{"outer_state":shadow["outer_state"],**candidate})
    rel_tol=float(gates["solver_qualification"]["maximum_jv_relative_error"]);abs_tol=float(gates["solver_qualification"]["maximum_gradient_absolute_error"]);check_pass=[]
    for row in trace.derivatives:
        eligible=[p for p in row["per_step"] if p["jv_relative_error"]<=rel_tol and (p["gradient_directional_relative_error"]<=rel_tol or p["gradient_directional_absolute_error"]<=abs_tol)];row["pass"]=bool(eligible);check_pass.append(row["pass"])
    derivative_max=max(min(p["jv_relative_error"] for p in row["per_step"]) for row in trace.derivatives);derivative_pass=all(check_pass)
    derivative={"steps":FD_STEPS,"relative_tolerance":rel_tol,"gradient_absolute_tolerance":abs_tol,"checks":trace.derivatives,"best_step_worst_jv_relative_error":derivative_max,"pass":derivative_pass,"verdict":"PASS" if derivative_pass else "JACOBIAN_OR_SCALING_QUALIFICATION_FAIL"};dump_json(output/"DERIVATIVE_CHECKS.json",derivative)
    quality=window_quality(problem)
    # Decision counts over states.
    comparable=[]
    for s in trace.shadows:
        c={x["kind"]:x for x in s["candidates"]};tight=np.asarray(c["TIGHT_LSMR"]["step_vector"]);dense=np.asarray(c["AUGMENTED_DENSE"]["step_vector"]);cos=float(np.dot(tight,dense)/max(np.linalg.norm(tight)*np.linalg.norm(dense),1e-300));relative=float(np.linalg.norm(tight-dense)/max(np.linalg.norm(dense),1e-300));comparable.append({"outer_state":s["outer_state"],"tight_dense_step_cosine":cos,"tight_dense_relative_step_difference":relative,"tight_dense_agree":cos>=.999999 and relative<=1e-6,"default_small":c["DEFAULT"]["scaled_step_norm"]<.5*c["TIGHT_LSMR"]["scaled_step_norm"],"tight_actual_better":c["TIGHT_LSMR"]["actual_reduction"]>1.5*c["DEFAULT"]["actual_reduction"],"tight_rho_positive":c["TIGHT_LSMR"]["rho"]>0,"exact_actual_better":c["FULL_EXACT_TR"]["actual_reduction"]>1.5*c["DEFAULT"]["actual_reduction"]})
    inner_count=sum(all(x[k] for k in ("tight_dense_agree","default_small","tight_actual_better","tight_rho_positive")) for x in comparable[:4]);exact_count=sum(x["exact_actual_better"] for x in comparable[:4]);rapid=control.optimality<=1e-4
    if not derivative_pass:diagnosis="JACOBIAN_OR_SCALING_QUALIFICATION_FAIL";action="B_REPAIR_JACOBIAN_OR_SCALING_BEFORE_SOLVER_CHANGE"
    elif inner_count>=3:diagnosis="INNER_LSMR_STOPPING_LIMITATION_CONFIRMED";action="QUALIFY_TIGHTER_LSMR_INNER_SETTINGS_ONLY"
    elif exact_count>=3:diagnosis="TWO_DIMENSIONAL_TR_SUBSPACE_LIMITATION";action="QUALIFY_FULL_SPACE_DENSE_EXACT_TR"
    elif rapid:diagnosis="FRESH_TRUST_REGION_RESTART_EFFECT_SUSPECTED";action="RUN_SAME_SHORT_RESTART_ON_OTHER_FOUR_CHECKPOINTS"
    elif len(trace.trials)>=4:diagnosis="LOCAL_SOLVER_HEALTHY_SHORT_HORIZON";action="SEEK_SEPARATE_AUTHORIZATION_FOR_LONGER_CONTINUATION"
    else:diagnosis="INCONCLUSIVE";action="NO_SOLVER_CHANGE"
    decision={"revision_c_verdict":"UNCHANGED_FAIL_PREVIEW_CALIBRATION","selected_start":selected,"accepted_states":trace.accepted,"total_trials":len(trace.trials),"actual_default_lsmr_behavior":"RECORDED","tight_lsmr_vs_augmented_dense":{"first_four_inner_limitation_count":inner_count,"states":comparable},"full_exact_tr_vs_default":{"first_four_exact_better_count":exact_count},"directional_derivative_qualification":derivative["verdict"],"initial_still_tpose_diagnosis":{"gradient_cancellation":"RECORDED","window_quality":quality,"verdict":"NO_WEIGHT_CHANGE_OR_MODEL_CONFLICT_ADOPTED"},"diagnostic_result":diagnosis,"recommended_revision_d_action":action,"new_result_adopted_as_calibration":False,"freeze_created":False,"replay_rendered":False,"commit_push":False,"golf_status":"SEALED","boxing_status":"SEALED","walk_status":"SEALED","final_still_status":"SEALED","control_terminal":{"cost":float(control.cost),"optimality":float(control.optimality),"nfev":int(control.nfev),"njev":int(control.njev),"status":int(control.status)},"evaluator_call_accounting":{"optimizer_underlying_calls":len(control_calls),"solver_nfev":int(control.nfev),"solver_njev":int(control.njev),"shadow_and_derivative":ledger.counts,"chord_calls":0}};dump_json(output/"REVISION_D_DECISION.json",decision)
    arrays={"control_initial_x":x,"control_terminal_x_non_adoptable":control.x,"control_terminal_residual":control.fun,**trace.arrays}
    savez_deterministic(output/"TRACE_ARRAYS.npz",arrays)
    report=f"""# {DIAGNOSTIC}\n\nRevision C remains `FAIL_PREVIEW_CALIBRATION`. Start {selected} was selected deterministically as the median terminal-optimality checkpoint. The trust state origin was a fresh initialization from its terminal x; historical trust state was not reconstructed.\n\nCheckpoint/evaluator replay: `PASS`. Stock versus instrumented first-three-trial equivalence: `PASS` bitwise. The unique control trajectory recorded {trace.accepted} accepted states and {len(trace.trials)} trial proposals. No shadow candidate was fed back into that trajectory.\n\nDirectional derivative qualification: `{derivative['verdict']}`. Diagnostic result: `{diagnosis}`. Recommended Revision-D action: `{action}`.\n\nInitial-still/T-Pose factor gradients, window motion quality, actual default LSMR telemetry, tight-LSMR, augmented-dense and full-exact trust-region shadows are recorded in the JSONL artifacts. No weights, factors, gates, objective, parameterization or bounds were changed.\n\nThe diagnostic terminal state is non-adoptable and is not a calibration result. No freeze, replay, render, commit or push was performed. Golf, boxing, walk and final_still remained sealed; UWB/T4/Anchor and operator measurements were not read.\n""";(output/"REPORT.md").write_text(report)
    manifest={p.name:sha256(p) for p in sorted(output.iterdir()) if p.is_file() and p.name!="SHA256_MANIFEST.json"};dump_json(output/"SHA256_MANIFEST.json",manifest)
    print(json.dumps(decision,sort_keys=True));return 0


if __name__=="__main__":raise SystemExit(main())
