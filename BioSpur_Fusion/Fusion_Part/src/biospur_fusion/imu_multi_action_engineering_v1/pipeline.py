"""Qualification, shared fit, freeze, isolated replay, and evidence gates."""
from __future__ import annotations

import json,math,time
from pathlib import Path
from typing import Any,Mapping
import numpy as np
from scipy.spatial.transform import Rotation

from biospur_fusion.imu_preview_v0.io import dump_json,savez_deterministic,sha256
from biospur_fusion.imu_preview_v0.q2 import Q2Result
from .common_time import build_common_timeline
from .phase_a import phase_a_gate_sha256
from .model import ACTIONS,LAYOUT,Objective,_decode,canonical_calibration,finite_difference_jacobian,fit_one,initial_parameters,migrate_spherical_latent_checkpoint,parameter_steps
from .relative_excitation import relative_excitation
from .replay import continuous_replay,functional_consumption_audit
from .segmentation import segment_actions


def load_q2_cache(path:Path)->dict[str,Q2Result]:
    out={}
    with np.load(path,allow_pickle=False) as source:
        nodes=sorted({key.split("__",1)[0] for key in source.files})
        for node in nodes:
            p=f"{node}__";out[node]=Q2Result(source[p+"time_ns"].copy(),source[p+"boot_epoch"].copy(),source[p+"q_wxyz"].copy(),source[p+"covariance_rad2"].copy(),source[p+"accel_mps2"].copy(),source[p+"gyro_corrected_rad_s"].copy(),source[p+"stationary"].astype(bool),source[p+"gravity_accepted"].astype(bool),source[p+"gap_boundary"].astype(bool),source[p+"bias_rad_s"].copy(),{})
    return out


def _soft_l1_cost_extended(residual):
    values=np.asarray(residual,dtype=np.longdouble);return np.sum(np.sqrt(1.+values*values)-1.,dtype=np.longdouble)


def _soft_l1_cost(residual):return float(_soft_l1_cost_extended(residual))


def _derivative_audit(objective:Objective,x:np.ndarray,jac:np.ndarray,cfg:Mapping[str,Any])->dict:
    rng=np.random.default_rng(41017);step=float(cfg["directional_step_rad"]);rows=[]
    for ordinal in range(10):
        direction=rng.normal(size=len(x));direction/=np.linalg.norm(direction);fd=(objective.residual(x+step*direction)-objective.residual(x-step*direction))/(2*step);jv=jac@direction;relative=float(np.linalg.norm(fd-jv)/max(np.linalg.norm(fd),np.linalg.norm(jv),1e-12));r=objective.residual(x);analytic=float((jac.T@(r/np.sqrt(1.+r*r)))@direction);c2p=_soft_l1_cost_extended(objective.residual(x+2*step*direction));cp=_soft_l1_cost_extended(objective.residual(x+step*direction));cm=_soft_l1_cost_extended(objective.residual(x-step*direction));c2m=_soft_l1_cost_extended(objective.residual(x-2*step*direction));cost_fd=float((-c2p+8*cp-8*cm+c2m)/(12*np.longdouble(step)));cost_absolute=abs(analytic-cost_fd);cost_relative=cost_absolute/max(abs(analytic),abs(cost_fd),1.);rows.append({"direction":ordinal,"jv_relative_error":relative,"soft_l1_analytic_directional_derivative":analytic,"soft_l1_five_point_scalar_cost_directional_derivative":cost_fd,"soft_l1_cost_directional_absolute_error":cost_absolute,"soft_l1_cost_directional_normalized_error":cost_relative,"normalization_floor_whitened_cost_per_rad":1.})
    mask=objective.structural_sparsity(x).toarray().astype(bool);outside=float(np.max(np.abs(jac[~mask]))) if np.any(~mask) else 0.;passed=max(x["jv_relative_error"] for x in rows)<=float(cfg["maximum_jv_relative_error"]) and max(x["soft_l1_cost_directional_normalized_error"] for x in rows)<=float(cfg["maximum_soft_l1_directional_cost_relative_error"]) and outside<=float(cfg["maximum_omitted_derivative_absolute"]);return {"schema":"biospur-production-endpoint-derivative-audit-v2","jacobian_method":"EXPLICIT_STRUCTURAL_SPARSITY_PLUS_3_POINT_CENTRAL_FD","scalar_cost_oracle":"EXTENDED_PRECISION_FIVE_POINT_CENTRAL_DIFFERENCE","small_derivative_normalization":"max(abs(analytic),abs(finite_difference),1_whitened_cost_per_rad)","structural_sparsity_nnz":int(mask.sum()),"dense_shape":list(mask.shape),"maximum_derivative_outside_declared_structure":outside,"directions":rows,"maximum_jv_relative_error":max(x["jv_relative_error"] for x in rows),"maximum_soft_l1_cost_directional_normalized_error":max(x["soft_l1_cost_directional_normalized_error"] for x in rows),"maximum_soft_l1_cost_directional_absolute_error":max(x["soft_l1_cost_directional_absolute_error"] for x in rows),"pass":passed}


def _action_sensitivity(objective:Objective,x:np.ndarray,jac:np.ndarray,cfg:Mapping[str,Any])->dict:
    accounting,slices=objective.accounting(x);total=float(np.square(jac).sum());h=jac.T@jac+np.eye(jac.shape[1])*1e-10;base_trace=float(np.trace(np.linalg.pinv(h)));rows=[]
    for action in ACTIONS:
        block=jac[slices[action]];norm=float(np.linalg.norm(block));info=float(np.square(block).sum()/max(total,1e-30));removed=np.r_[np.arange(0,slices[action].start),np.arange(slices[action].stop,len(jac))];trace=float(np.trace(np.linalg.pinv(jac[removed].T@jac[removed]+np.eye(jac.shape[1])*1e-10)));effect=(trace-base_trace)/max(base_trace,1e-30);passed=accounting["actions"][action]["scalar_rows"]>0 and norm>=float(cfg["minimum_action_jacobian_frobenius_norm"]) and info>=float(cfg["minimum_action_information_fraction"]);rows.append({"action":action,"scalar_residual_rows":accounting["actions"][action]["scalar_rows"],"residual_blocks":accounting["actions"][action]["residual_blocks"],"jacobian_frobenius_norm":norm,"jacobian_rank":int(np.linalg.matrix_rank(block)),"total_information_fraction":info,"linearized_leave_one_action_out_covariance_trace_change_fraction":effect,"frozen_output_consumer":"SEGMENT_CORRECTION_OR_FUNCTIONAL_ARTICULATED_REPLAY","status":"PASS" if passed else "DECLARED_ACTION_UNUSED"})
    return {"schema":"biospur-action-residual-parameter-sensitivity-v1","accounting":accounting,"rows":rows,"pass":all(row["status"]=="PASS" for row in rows),"motion_range_used_as_sensitivity":False}


def _relative_excitation_audit(timeline,segmentation,node_to_segment,contract):
    nodes={node:i for i,node in enumerate(timeline.node_order)};si={segment:nodes[node] for node,segment in node_to_segment.items()};specs={"elbow_L":("left_elbow","upper_arm_L","forearm_L"),"elbow_R":("right_elbow_attempt2","upper_arm_R","forearm_R"),"high_knee_L":("left_knee","pelvis","thigh_L"),"high_knee_R":("right_knee","pelvis","thigh_R"),"heel_to_butt_L":("left_heel","thigh_L","shank_L"),"heel_to_butt_R":("right_heel","thigh_R","shank_R"),"trunk":("trunk","pelvis","torso")};rows=[]
    for name,(action,parent,child) in specs.items():
        selected=np.unique([row for phase in segmentation["actions"][action] if "neutral" not in phase["phase"] for row in phase["row_indices"]]);valid=timeline.valid[selected,si[parent]]&timeline.valid[selected,si[child]];selected=selected[valid];result=relative_excitation(timeline.rotation[selected,si[parent]],timeline.rotation[selected,si[child]],timeline.covariance_rad2[selected,si[parent]],timeline.covariance_rad2[selected,si[child]],contract);rows.append({"chain":name,"action":action,"parent":parent,"child":child,**result})
    return {"schema":"biospur-real-action-relative-excitation-v1","rows":rows,"pass":all(row["pass"] for row in rows)}


def _multistart_axis_difference(a,b):
    aa,_,af,_,_=_decode(a);ba,_,bf,_,_=_decode(b);angles=[math.degrees(math.acos(float(np.clip(aa[s]@ba[s],-1,1)))) for s in aa];angles.extend(math.degrees(math.acos(float(np.clip(af[s]@bf[s],-1,1)))) for s in af);return max(angles)


def analyze(phase_a_dir:Path,template_path:Path,gates_path:Path,contract_path:Path,output:Path,resume_checkpoint:Path|None=None,resume_parameterization:str|None=None)->dict:
    started=time.perf_counter();phase=json.loads((phase_a_dir/"RESULT.json").read_text());gates=json.loads(gates_path.read_text());template=json.loads(template_path.read_text());contract=json.loads(contract_path.read_text());output=Path(output)
    if output.exists():raise FileExistsError(output)
    output.mkdir(parents=True);failures=[]
    if not phase["pass"]:failures.append("PHASE_A_NOT_QUALIFIED")
    if phase.get("phase_a_gate_binding_sha256")!=phase_a_gate_sha256(gates):failures.append("PHASE_A_RELEVANT_GATES_SHA_MISMATCH")
    cache=phase_a_dir/"Q2_HUMAN_QUASI_STATIC_CACHE.npz"
    if not cache.exists() or sha256(cache)!=phase.get("q2_cache_sha256"):failures.append("Q2_CACHE_BINDING_FAIL")
    if sha256(template_path)!=gates["template"]["sha256"]:failures.append("TEMPLATE_SHA_MISMATCH")
    if failures:
        result={"schema":"biospur-engineering-preview-calibration-result-v1","pass":False,"verdict":failures[0],"failures":failures,"nonlinear_solver_started":False,"golf":"SEALED","boxing":"SEALED","walk":"SEALED","final_still":"SEALED"};dump_json(output/"RESULT.json",result);return result
    q2=load_q2_cache(cache);windows={name:tuple(value) for name,value in phase["calibration_windows"].items()};start=min(x[0] for x in windows.values());stop=max(x[1] for x in windows.values());timeline=build_common_timeline(q2,start,stop,gates["common_time"]);common_done=time.perf_counter();segmentation=segment_actions(timeline,windows,gates["node_to_segment"],gates["action_segmentation"]);dump_json(output/"ACTION_SEGMENTATION.json",segmentation);dump_json(output/"COMMON_TIME_ACCOUNTING.json",timeline.accounting)
    if not segmentation["pass"]:
        result={"schema":"biospur-engineering-preview-calibration-result-v1","pass":False,"verdict":"BLOCKED_ACTION_SEMANTICS_MISMATCH","failures":segmentation["failures"],"nonlinear_solver_started":False,"golf":"SEALED","boxing":"SEALED","walk":"SEALED","final_still":"SEALED"};dump_json(output/"RESULT.json",result);return result
    excitation=_relative_excitation_audit(timeline,segmentation,gates["node_to_segment"],contract);dump_json(output/"ACTION_RELATIVE_EXCITATION_AUDIT.json",excitation)
    if not excitation["pass"]:
        result={"schema":"biospur-engineering-preview-calibration-result-v1","pass":False,"verdict":"BLOCKED_ACTION_SEMANTICS_MISMATCH","failures":[row for row in excitation["rows"] if not row["pass"]],"nonlinear_solver_started":False,"golf":"SEALED","boxing":"SEALED","walk":"SEALED","final_still":"SEALED"};dump_json(output/"RESULT.json",result);return result
    objective=Objective(timeline,segmentation,gates["node_to_segment"],gates["calibration_model"]);x0=initial_parameters(objective);accounting,_=objective.accounting(x0);dump_json(output/"STATIC_LATENT_REFERENCE_COVARIANCE.json",objective.static_reference_audit);dump_json(output/"PRE_SOLVER_RESIDUAL_ACCOUNTING.json",accounting)
    fit_start=x0;fit_budget=int(gates["calibration_solver"]["one_start_max_nfev"]);resume_audit=None
    if resume_checkpoint is not None:
        resume_checkpoint=Path(resume_checkpoint);source=json.loads(resume_checkpoint.read_text());source_segmentation=resume_checkpoint.parent/"ACTION_SEGMENTATION.json";resume_failures=[]
        if resume_parameterization not in ("SPHERICAL_AZIMUTH_ELEVATION_WITH_POLE_SINGULARITY","REFERENCE_CENTERED_S2_TANGENT_CHART"):resume_failures.append("CHECKPOINT_PARAMETERIZATION_NOT_EXPLICIT")
        if source.get("source_cache_sha256")!=sha256(cache):resume_failures.append("SOURCE_CACHE_SHA_MISMATCH")
        if not source_segmentation.exists() or sha256(source_segmentation)!=sha256(output/"ACTION_SEGMENTATION.json"):resume_failures.append("ACTION_SEGMENTATION_SHA_MISMATCH")
        old_x=np.asarray(source.get("x",[]),float)
        if old_x.shape!=(96,) or not np.isfinite(old_x).all():resume_failures.append("CHECKPOINT_PARAMETER_INVALID")
        if resume_failures:
            resume_audit={"schema":"biospur-pole-chart-checkpoint-migration-v1","pass":False,"failures":resume_failures,"source_checkpoint":str(resume_checkpoint.resolve())};dump_json(output/"CHECKPOINT_RESUME_AUDIT.json",resume_audit)
            result={"schema":"biospur-engineering-preview-calibration-result-v1","pass":False,"verdict":"BLOCKED_CHECKPOINT_RESUME_BINDING","failures":resume_failures,"nonlinear_solver_started":False,"golf":"SEALED","boxing":"SEALED","walk":"SEALED","final_still":"SEALED"};dump_json(output/"RESULT.json",result);return result
        fit_start=migrate_spherical_latent_checkpoint(old_x) if resume_parameterization=="SPHERICAL_AZIMUTH_ELEVATION_WITH_POLE_SINGULARITY" else old_x.copy();recomputed_cost=_soft_l1_cost(objective.residual(fit_start));cost_difference=abs(recomputed_cost-float(source["cost"]));cost_tolerance=1e-10*max(1.,abs(float(source["cost"])))
        resume_audit={"schema":"biospur-checkpoint-objective-replay-v1","pass":cost_difference<=cost_tolerance,"source_checkpoint":str(resume_checkpoint.resolve()),"source_checkpoint_sha256":sha256(resume_checkpoint),"source_cache_sha256_verified":True,"action_segmentation_sha256_verified":True,"source_parameterization":resume_parameterization,"new_parameterization":"REFERENCE_CENTERED_S2_TANGENT_CHART","source_soft_l1_cost":float(source["cost"]),"replayed_soft_l1_cost":recomputed_cost,"absolute_cost_difference":cost_difference,"cost_tolerance":cost_tolerance,"objective_rows":len(objective.residual(fit_start)),"physical_state_migration":"EXACT_S2_AXIS_PRESERVING" if resume_parameterization.startswith("SPHERICAL") else "IDENTITY","solver_state_reused":False}
        dump_json(output/"CHECKPOINT_RESUME_AUDIT.json",resume_audit)
        if not resume_audit["pass"]:
            result={"schema":"biospur-engineering-preview-calibration-result-v1","pass":False,"verdict":"BLOCKED_CHECKPOINT_OBJECTIVE_NOT_EQUIVALENT","nonlinear_solver_started":False,"golf":"SEALED","boxing":"SEALED","walk":"SEALED","final_still":"SEALED"};dump_json(output/"RESULT.json",result);return result
        fit_budget=int(gates["calibration_solver"]["restart_max_nfev"])
    solver_start=time.perf_counter();fit=fit_one(objective,fit_start,gates["calibration_solver"],gates["production_jacobian"],fit_budget);first_done=time.perf_counter();dump_json(output/"START_0_CHECKPOINT.json",{"x":fit.x.tolist(),"parameterization":"REFERENCE_CENTERED_S2_TANGENT_CHART","cost":float(fit.cost),"optimality":float(fit.optimality),"nfev":int(fit.nfev),"status":int(fit.status),"success":bool(fit.success),"message":str(fit.message),"source_cache_sha256":sha256(cache),"gates_sha256":sha256(gates_path),"resumed_from":str(resume_checkpoint.resolve()) if resume_checkpoint is not None else None,"resume_audit_pass":None if resume_audit is None else resume_audit["pass"]})
    jac=finite_difference_jacobian(objective.residual,fit.x,parameter_steps(gates["production_jacobian"]));derivative=_derivative_audit(objective,fit.x,jac,gates["production_jacobian"]);sensitivity=_action_sensitivity(objective,fit.x,jac,gates["production_jacobian"]);dump_json(output/"PRODUCTION_JACOBIAN_AUDIT.json",derivative);dump_json(output/"ACTION_RESIDUAL_PARAMETER_SENSITIVITY.json",sensitivity)
    first_wall=first_done-solver_start;solver_ok=bool(fit.success and fit.optimality<=float(gates["calibration_solver"]["maximum_optimality"]));performance_ok=first_wall<=float(gates["calibration_solver"]["temporary_maximum_wall_time_s"]);qualification=solver_ok and performance_ok and derivative["pass"] and sensitivity["pass"]
    fits=[fit];multistart=[]
    if qualification:
        restart=fit_one(objective,fit.x,gates["calibration_solver"],gates["production_jacobian"],int(gates["calibration_solver"]["restart_max_nfev"]));relative_cost=abs(float(restart.cost-fit.cost))/max(abs(float(fit.cost)),1e-12);qualification&=relative_cost<=float(gates["calibration_solver"]["maximum_restart_cost_relative_change"]);rng=np.random.default_rng(int(gates["calibration_solver"]["random_seed"]));starts=[x0]
        for _ in range(int(gates["calibration_solver"]["multistart_count"])-1):
            x=x0.copy()
            for entry in LAYOUT["entries"]:
                sigma=float(gates["calibration_solver"]["axis_perturbation_rad"] if entry["block"] in ("sensor_axis_quotient","latent_pose_reference_tangent_chart") else gates["calibration_solver"]["heading_perturbation_rad"] if entry["block"]=="relative_heading" else gates["calibration_solver"]["functional_axis_perturbation_rad"] if entry["block"]=="functional_axis_or_subspace" else gates["calibration_solver"]["joint_zero_perturbation_rad"]);x[entry["start"]:entry["stop"]]+=rng.normal(0,sigma,entry["stop"]-entry["start"])
            starts.append(x)
        for ordinal,start_value in enumerate(starts[1:],1):
            candidate=fit_one(objective,start_value,gates["calibration_solver"],gates["production_jacobian"],int(gates["calibration_solver"]["one_start_max_nfev"]));fits.append(candidate);dump_json(output/f"START_{ordinal}_CHECKPOINT.json",{"x":candidate.x.tolist(),"cost":float(candidate.cost),"optimality":float(candidate.optimality),"nfev":int(candidate.nfev),"status":int(candidate.status),"success":bool(candidate.success),"message":str(candidate.message),"source_cache_sha256":sha256(cache),"gates_sha256":sha256(gates_path)})
        best=min(fits,key=lambda item:(float(item.cost),float(item.optimality)));multistart=[{"start":i,"cost":float(item.cost),"optimality":float(item.optimality),"success":bool(item.success),"maximum_publishable_axis_difference_deg":_multistart_axis_difference(item.x,best.x)} for i,item in enumerate(fits)];qualification&=all(row["success"] and row["maximum_publishable_axis_difference_deg"]<=float(gates["calibration_solver"]["maximum_multistart_publishable_axis_difference_deg"]) for row in multistart);fit=best
    wall=time.perf_counter()-solver_start;qualification&=wall<=float(gates["calibration_solver"]["temporary_maximum_wall_time_s"]);solver_audit={"schema":"biospur-engineering-preview-solver-v1","first_start_wall_time_s":first_done-solver_start,"total_numeric_wall_time_s":wall,"first_start":{"success":bool(fits[0].success),"cost":float(fits[0].cost),"optimality":float(fits[0].optimality),"nfev":int(fits[0].nfev)},"derivative_pass":derivative["pass"],"action_sensitivity_pass":sensitivity["pass"],"multistart":multistart,"pass":qualification};dump_json(output/"SOLVER_AUDIT.json",solver_audit)
    if not qualification:
        result={"schema":"biospur-engineering-preview-calibration-result-v1","pass":False,"verdict":"BLOCKED_REQUIRED_PARAMETER_UNOBSERVABLE" if not sensitivity["pass"] else "FAIL_PREVIEW_CALIBRATION","nonlinear_solver_started":True,"solver":solver_audit,"golf":"SEALED","boxing":"SEALED","walk":"SEALED","final_still":"SEALED"};dump_json(output/"RESULT.json",result);return result
    metadata={"gates_sha256":sha256(gates_path),"template_sha256":sha256(template_path),"phase_a_result_sha256":sha256(phase_a_dir/"RESULT.json"),"q2_cache_sha256":sha256(cache),"calibration_ledger_sha256":phase["calibration_ledger_sha256"],"timeline_start_global_time_ns":int(start),"timeline_stop_global_time_ns":int(stop),"functional_parameters_consumed_by_replay":True,"labels_forbidden_during_replay":True};calibration=canonical_calibration(fit.x,metadata);frozen=output/"FROZEN_CALIBRATION.json";dump_json(frozen,calibration);digest=sha256(frozen);(output/"FROZEN_CALIBRATION.sha256").write_text(f"{digest}  FROZEN_CALIBRATION.json\n");result={"schema":"biospur-engineering-preview-calibration-result-v1","pass":True,"verdict":"PASS_IMU_MULTI_ACTION_ENGINEERING_CALIBRATION_V1","nonlinear_solver_started":True,"frozen_calibration_sha256":digest,"solver":solver_audit,"timing_s":{"common_time_and_segmentation":common_done-started,"numeric_calibration":wall,"total":time.perf_counter()-started},"golf":"SEALED","boxing":"SEALED","walk":"SEALED","final_still":"SEALED"};dump_json(output/"RESULT.json",result);return result


def replay(cache_path:Path,phase_result_path:Path,template_path:Path,gates_path:Path,frozen_path:Path,frozen_sha_path:Path,output:Path)->dict:
    expected=frozen_sha_path.read_text().split()[0]
    if sha256(frozen_path)!=expected:raise ValueError("frozen calibration SHA mismatch")
    calibration=json.loads(frozen_path.read_text());gates=json.loads(gates_path.read_text());template=json.loads(template_path.read_text());phase=json.loads(phase_result_path.read_text())
    for path,key in ((gates_path,"gates_sha256"),(template_path,"template_sha256"),(cache_path,"q2_cache_sha256")):
        if sha256(path)!=calibration[key]:raise ValueError(f"frozen {key} binding mismatch")
    q2=load_q2_cache(cache_path);timeline=build_common_timeline(q2,int(calibration["timeline_start_global_time_ns"]),int(calibration["timeline_stop_global_time_ns"]),gates["common_time"]);arrays=continuous_replay(timeline,calibration,template,gates);consumption=functional_consumption_audit(timeline,calibration,template,gates,arrays);output=Path(output)
    if output.exists():raise FileExistsError(output)
    output.mkdir(parents=True);savez_deterministic(output/"CONTINUOUS_LABEL_BLIND_REPLAY.npz",arrays);dump_json(output/"FUNCTIONAL_PARAMETER_CONSUMPTION.json",consumption);audit={"schema":"biospur-isolated-engineering-preview-replay-v1","frozen_sha_verified":True,"fit_object_available":False,"initialization_count":1,"labels_visible_during_state_replay":False,"pose_resets":0,"heading_resets":0,"contact_resets":0,"root_resets":0,"velocity_resets":0,"ankle_reanchors":0,"functional_parameters_consumed":consumption["pass"],"state_sha256":sha256(output/"CONTINUOUS_LABEL_BLIND_REPLAY.npz"),"golf":"SEALED","boxing":"SEALED","walk":"SEALED","final_still":"SEALED","pass":consumption["pass"]};dump_json(output/"REPLAY_AUDIT.json",audit);return audit
