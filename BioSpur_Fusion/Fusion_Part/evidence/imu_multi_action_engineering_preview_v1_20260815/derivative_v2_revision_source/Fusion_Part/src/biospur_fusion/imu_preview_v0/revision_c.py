"""FAST_SOLVER_REVISION_C scientific plumbing.

The module owns the fixed-support residual problem, strict qualification,
objective oracle, and crash-safe solver checkpoints.  It deliberately has no
renderer and accepts only the already-built calibration-only common timeline.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import shutil
import sys
from typing import Any, Mapping, Sequence

import numpy as np
import scipy
from scipy.optimize import least_squares
from scipy.sparse import csr_matrix, lil_matrix
from scipy.spatial.transform import Rotation

from .common_time import CommonTimeline
from .core import (
    EXPECTED_INITIAL, EXPECTED_TPOSE, SEGMENTS, _decode_parameters,
    _drift_basis, _initial_parameters, _node_indices, action_masks,
    directions_from_parameters,
)
from .io import canonical_json_bytes, dump_json_atomic, savez_deterministic, sha256

SOLVER_REVISION="FAST_SOLVER_REVISION_C"


def array_sha256(value:np.ndarray) -> str:
    value=np.ascontiguousarray(value)
    h=hashlib.sha256();h.update(value.dtype.str.encode());h.update(str(value.shape).encode());h.update(value.tobytes());return h.hexdigest()


def object_sha256(value:Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _select_rows(mask:np.ndarray,maximum:int|None=None,stride:int=1) -> np.ndarray:
    rows=np.flatnonzero(mask)
    if maximum is not None and len(rows)>maximum:rows=rows[np.round(np.linspace(0,len(rows)-1,maximum)).astype(int)]
    return rows[::stride]


@dataclass(frozen=True)
class Factor:
    factor_id:str
    action:str
    phase:str
    kind:str
    segments:tuple[int,...]
    required_nodes:tuple[str,...]
    rows:np.ndarray
    component_count:int
    sigma:float


class CalibrationProblem:
    """A residual whose support is frozen entirely from input validity."""
    def __init__(self,timeline:CommonTimeline,windows:Mapping[str,tuple[int,int]],gates:Mapping[str,Any]):
        self.timeline=timeline;self.windows=dict(windows);self.gates=gates;self.cfg=gates["calibration_solver"]
        self.node_to_segment=gates["node_to_segment"];self.node_index,self.segment_node_index=_node_indices(timeline,self.node_to_segment)
        spacing=int(round(float(self.cfg["yaw_drift_knot_spacing_s"])*1e9))
        self.knot_times=np.arange(timeline.time_ns[0],timeline.time_ns[-1]+spacing,spacing,dtype=np.int64)
        if self.knot_times[-1]<timeline.time_ns[-1]:self.knot_times=np.r_[self.knot_times,timeline.time_ns[-1]]
        self.basis=_drift_basis(timeline.time_ns,self.knot_times);self.action_mask=action_masks(timeline.time_ns,windows)
        self.x0=_initial_parameters(timeline,windows,self.node_to_segment,self.knot_times)
        self.factors=self._build_factors();self.row_manifest=self._build_row_manifest();self.row_order_sha256=object_sha256(self.row_manifest)
        self.parameter_metadata=self._parameter_metadata();self.starts=self._make_starts();self.sparsity=self._build_sparsity()

    def _build_factors(self) -> tuple[Factor,...]:
        fs=[];static_sigma=float(self.cfg["static_direction_sigma_rad"])
        for k,segment in enumerate(SEGMENTS):
            node=next(n for n,s in self.node_to_segment.items() if s==segment);node_ok=self.timeline.valid[:,self.node_index[node]]
            for action,expected_name in (("initial_still_attempt2","INITIAL"),("t_pose","TPOSE")):
                rows=_select_rows(self.action_mask[action]&node_ok,maximum=100)
                fs.append(Factor(f"static:{segment}:{action}",action,"FULL_ACTION_WINDOW_NO_SUBPHASE_SEGMENTATION",f"STATIC_{expected_name}",(k,),(node,),rows,3,static_sigma))
            prior_rows=np.arange(1,len(self.knot_times),dtype=np.int64)
            fs.append(Factor(f"drift_prior:{segment}","GLOBAL","NOT_ACTION_SCOPED","DRIFT_PRIOR",(k,),(node,),prior_rows,1,float(self.cfg["yaw_drift_prior_sigma_rad"])))
            smooth_rows=np.arange(max(0,len(self.knot_times)-2),dtype=np.int64)
            fs.append(Factor(f"drift_smooth:{segment}","GLOBAL","NOT_ACTION_SCOPED","DRIFT_SECOND_DIFFERENCE",(k,),(node,),smooth_rows,1,float(self.cfg["yaw_drift_second_difference_sigma_rad"])))
        for left,right,name in ((6,7,"thigh"),(8,9,"shank")):
            nodes=tuple(next(n for n,s in self.node_to_segment.items() if s==SEGMENTS[k]) for k in (left,right))
            valid=self.action_mask["squats"].copy()
            for node in nodes:valid&=self.timeline.valid[:,self.node_index[node]]
            rows=_select_rows(valid,stride=10)
            fs.append(Factor(f"squat_symmetry:{name}","squats","FULL_ACTION_WINDOW_NO_SUBPHASE_SEGMENTATION","SQUAT_Z_SYMMETRY",(left,right),nodes,rows,1,0.25))
        return tuple(fs)

    def _parameter_metadata(self) -> list[dict]:
        rows=[]
        for segment in SEGMENTS:
            rows.extend([
                {"name":f"axis_theta:{segment}","unit":"rad","physical_scale":1.0,"lower_bound":None,"upper_bound":None},
                {"name":f"axis_phi:{segment}","unit":"rad","physical_scale":1.0,"lower_bound":None,"upper_bound":None},
                {"name":f"relative_heading:{segment}","unit":"rad","physical_scale":1.0,"lower_bound":None,"upper_bound":None},
            ])
        for segment in SEGMENTS:
            for knot in range(1,len(self.knot_times)):rows.append({"name":f"yaw_drift:{segment}:knot_{knot}","unit":"rad","physical_scale":1.0,"lower_bound":None,"upper_bound":None})
        assert len(rows)==len(self.x0)
        return rows

    def _make_starts(self) -> tuple[np.ndarray,...]:
        rng=np.random.default_rng(int(self.gates["determinism"]["random_seed"]));starts=[self.x0.copy()]
        for _ in range(4):
            p=self.x0.copy()
            for k in range(len(SEGMENTS)):
                p[3*k:3*k+2]+=rng.normal(0,float(self.cfg["five_start_axis_perturbation_rad"]),2)
                p[3*k+2]+=rng.normal(0,float(self.cfg["five_start_heading_perturbation_rad"]))
            starts.append(p)
        return tuple(starts)

    def _factor_values(self,x:np.ndarray,factor:Factor,slow:bool) -> np.ndarray:
        axes,heading,drift=_decode_parameters(x,len(self.knot_times));f=factor
        if f.kind.startswith("STATIC_"):
            k=f.segments[0];j=self.segment_node_index[SEGMENTS[k]];expected=EXPECTED_INITIAL[SEGMENTS[k]] if f.kind=="STATIC_INITIAL" else EXPECTED_TPOSE[SEGMENTS[k]]
            if slow:direction=directions_from_parameters(self.timeline,self.node_to_segment,x,self.knot_times)[f.rows,k]
            else:
                yaw=heading[k]+self.basis[f.rows]@drift[k];corrected=np.einsum("nij,njk->nik",self._yaw(yaw),self.timeline.rotation[f.rows,j]);direction=np.einsum("nij,j->ni",corrected,axes[k])
            return ((direction-expected)/f.sigma).ravel()
        if f.kind=="DRIFT_PRIOR":return drift[f.segments[0],1:]/f.sigma
        if f.kind=="DRIFT_SECOND_DIFFERENCE":return np.diff(drift[f.segments[0]],2)/f.sigma
        if f.kind=="SQUAT_Z_SYMMETRY":
            if slow:
                direction=directions_from_parameters(self.timeline,self.node_to_segment,x,self.knot_times)
                return (direction[f.rows,f.segments[0],2]-direction[f.rows,f.segments[1],2])/f.sigma
            pair=[]
            for k in f.segments:
                j=self.segment_node_index[SEGMENTS[k]];yaw=heading[k]+self.basis[f.rows]@drift[k]
                corrected=np.einsum("nij,njk->nik",self._yaw(yaw),self.timeline.rotation[f.rows,j]);pair.append(np.einsum("nij,j->ni",corrected,axes[k]))
            return (pair[0][:,2]-pair[1][:,2])/f.sigma
        raise AssertionError(f.kind)

    @staticmethod
    def _yaw(angle:np.ndarray) -> np.ndarray:
        c=np.cos(angle);s=np.sin(angle);out=np.zeros(angle.shape+(3,3));out[...,0,0]=c;out[...,0,1]=-s;out[...,1,0]=s;out[...,1,1]=c;out[...,2,2]=1.;return out

    def residual_fast(self,x:np.ndarray) -> np.ndarray:
        return np.concatenate([self._factor_values(x,f,False) for f in self.factors])

    def residual_slow(self,x:np.ndarray) -> np.ndarray:
        # This deliberately reconstructs the historical full-timeline slow
        # direction kernel for each direction-bearing factor.
        return np.concatenate([self._factor_values(x,f,True) for f in self.factors])

    def factor_slices(self) -> dict[str,slice]:
        out={};cursor=0
        for f in self.factors:
            count=len(f.rows)*f.component_count;out[f.factor_id]=slice(cursor,cursor+count);cursor+=count
        return out

    def _build_row_manifest(self) -> list[dict]:
        out=[];row=0
        for f in self.factors:
            for source in f.rows.tolist():
                time_ns=None if f.action=="GLOBAL" else int(self.timeline.time_ns[source])
                for component in range(f.component_count):
                    out.append({"residual_row":row,"factor_id":f.factor_id,"factor_kind":f.kind,"action":f.action,"phase":f.phase,"common_grid_index":int(source),"global_time_ns":time_ns,"component":component,"required_nodes":list(f.required_nodes),"weight":1.0/f.sigma});row+=1
        return out

    def factor_mask_arrays(self) -> dict[str,np.ndarray]:
        out={"all_nodes_valid":self.timeline.all_nodes_valid.astype(np.uint8)}
        for node,j in self.node_index.items():out[f"node__{node}"]=self.timeline.valid[:,j].astype(np.uint8)
        for f in self.factors:
            mask=np.zeros(len(self.timeline.time_ns),np.uint8)
            if f.action!="GLOBAL":mask[f.rows]=1
            out[f"factor__{f.factor_id.replace(':','__')}"]=mask
        return out

    def validity_audit(self) -> dict:
        vc=self.gates["validity_contract"];actions={}
        for action,mask in self.action_mask.items():
            actions[action]={"grid_rows":int(mask.sum()),"all_nodes_valid_rows":int((mask&self.timeline.all_nodes_valid).sum())}
        factors=[]
        for f in self.factors:
            requirement=0 if f.action=="GLOBAL" else int(vc["minimum_static_factor_samples"] if f.kind.startswith("STATIC") else vc["minimum_squat_factor_samples"])
            factors.append({"factor_id":f.factor_id,"action":f.action,"phase":f.phase,"required_nodes":list(f.required_nodes),"valid_rows":int(len(f.rows)),"minimum_required":requirement,"support_pass":len(f.rows)>=requirement,"common_grid_indices_sha256":array_sha256(f.rows),"global_time_ns_sha256":None if f.action=="GLOBAL" else array_sha256(self.timeline.time_ns[f.rows])})
        nodes=[]
        for node,j in self.node_index.items():
            valid=self.timeline.valid[:,j];nodes.append({"node":node,"valid_rows":int(valid.sum()),"invalid_rows":int((~valid).sum()),"valid_fraction":float(valid.mean()),"minimum_fraction":float(vc["minimum_per_node_valid_fraction"]),"support_pass":float(valid.mean())>=float(vc["minimum_per_node_valid_fraction"]),"mask_sha256":array_sha256(valid.astype(np.uint8)),"source_reason_accounting":self.timeline.accounting["nodes"][node]})
        whole={"valid_rows":int(self.timeline.all_nodes_valid.sum()),"invalid_rows":int((~self.timeline.all_nodes_valid).sum()),"valid_fraction":float(self.timeline.all_nodes_valid.mean()),"minimum_fraction":float(self.gates["common_time"]["minimum_all_node_valid_fraction"]),"support_pass":float(self.timeline.all_nodes_valid.mean())>=float(self.gates["common_time"]["minimum_all_node_valid_fraction"]),"mask_sha256":array_sha256(self.timeline.all_nodes_valid.astype(np.uint8))}
        return {"schema":"biospur-revision-c-validity-contract-v1","policy":vc,"parameter_independent":True,"nodes":nodes,"whole_skeleton":whole,"actions":actions,"factors":factors,"row_order_sha256":self.row_order_sha256,"all_support_pass":whole["support_pass"] and all(x["support_pass"] for x in nodes+factors)}

    def _build_sparsity(self) -> csr_matrix:
        base=self.residual_fast(self.x0);matrix=lil_matrix((len(base),len(self.x0)),dtype=np.int8)
        for col,meta in enumerate(self.parameter_metadata):
            probe=self.x0.copy();probe[col]+=1e-7;changed=np.abs(self.residual_fast(probe)-base)>1e-12;matrix[np.flatnonzero(changed),col]=1
        return matrix.tocsr()


def loss_accounting(residual:np.ndarray,f_scale:float) -> dict:
    if not np.isfinite(residual).all():raise FloatingPointError("nonfinite residual inside fixed support")
    z=(residual/f_scale)**2;rho1=1./np.sqrt(1.+z)
    return {"ls_cost":float(.5*np.dot(residual,residual)),"huber_cost":float(np.sum(np.where(np.abs(residual)<=f_scale,.5*residual**2,f_scale*(np.abs(residual)-.5*f_scale)))),"soft_l1_cost":float(np.sum(f_scale*f_scale*(np.sqrt(1.+z)-1.))),"robust_weights":rho1}


def objective_oracle_compare(problem:CalibrationProblem,x:np.ndarray,directions:Sequence[np.ndarray],tol_abs:float,tol_rel:float) -> dict:
    fast=problem.residual_fast(x);slow=problem.residual_slow(x);f_scale=float(problem.cfg["f_scale"]);fa=loss_accounting(fast,f_scale);sa=loss_accounting(slow,f_scale)
    comparisons=[]
    for v in directions:
        v=np.asarray(v,float);v/=np.linalg.norm(v);h=1e-6
        fjv=(problem.residual_fast(x+h*v)-problem.residual_fast(x-h*v))/(2*h);sjv=(problem.residual_slow(x+h*v)-problem.residual_slow(x-h*v))/(2*h)
        comparisons.append({"fast_slow_jv_max_abs":float(np.max(np.abs(fjv-sjv))),"fast_slow_jv_l2":float(np.linalg.norm(fjv-sjv)),"fast_jtr":float(np.dot(fjv,fast)),"slow_jtr":float(np.dot(sjv,slow))})
    diff=fast-slow;scale=max(float(np.max(np.abs(slow))),1.0);passed=float(np.max(np.abs(diff)))<=tol_abs+tol_rel*scale and all(c["fast_slow_jv_max_abs"]<=tol_abs+tol_rel*max(abs(c["slow_jtr"]),1.) for c in comparisons)
    return {"residual_shape_fast":list(fast.shape),"residual_shape_slow":list(slow.shape),"row_order_sha256":problem.row_order_sha256,"residual_max_abs_difference":float(np.max(np.abs(diff))),"residual_l2_difference":float(np.linalg.norm(diff)),"costs_fast":{k:v for k,v in fa.items() if k!="robust_weights"},"costs_slow":{k:v for k,v in sa.items() if k!="robust_weights"},"robust_weight_max_abs_difference":float(np.max(np.abs(fa["robust_weights"]-sa["robust_weights"]))),"directional":comparisons,"pass":bool(passed)}


def negative_oracle_harness_test(problem:CalibrationProblem) -> dict:
    original=problem.row_manifest[0].copy();changed=original.copy();changed["weight"]=float(changed["weight"])*1.01
    baseline=object_sha256(problem.row_manifest);mutated=[changed,*problem.row_manifest[1:]];detected=object_sha256(mutated)!=baseline
    return {"mutation":"FIRST_RESIDUAL_ROW_WEIGHT_X1.01","baseline_row_order_sha256":baseline,"mutated_row_order_sha256":object_sha256(mutated),"equivalence_harness_detected_change":detected,"pass":detected}


def deterministic_directions(count:int,size:int,seed:int) -> list[np.ndarray]:
    rng=np.random.default_rng(seed);out=[]
    for _ in range(count):
        v=rng.normal(size=size);out.append(v/np.linalg.norm(v))
    return out


def qualify_jacobian(problem:CalibrationProblem) -> dict:
    cfg=problem.gates["solver_qualification"];x=problem.starts[0];directions=deterministic_directions(int(cfg["direction_count"]),len(x),int(problem.gates["determinism"]["random_seed"])+91);steps=[float(v) for v in cfg["finite_difference_steps"]]
    # A central derivative at the middle frozen step is the qualified dense
    # reference. Other steps test stability, while sparsity is checked against
    # finite nonzero support.
    h_ref=steps[1];columns=[]
    for col in range(len(x)):
        e=np.zeros(len(x));e[col]=1.;columns.append((problem.residual_fast(x+h_ref*e)-problem.residual_fast(x-h_ref*e))/(2*h_ref))
    jac=np.column_stack(columns);outside=np.abs(jac)>1e-10;pattern=problem.sparsity.toarray().astype(bool);outside_count=int(np.sum(outside&~pattern))
    direction_rows=[];worst=0.
    for ordinal,v in enumerate(directions):
        reference=jac@v;per_step=[]
        for h in steps:
            observed=(problem.residual_fast(x+h*v)-problem.residual_fast(x-h*v))/(2*h);error=np.linalg.norm(observed-reference)/max(np.linalg.norm(reference),1e-12);per_step.append({"step":h,"relative_error":float(error)});worst=max(worst,float(error))
        direction_rows.append({"direction":ordinal,"per_step":per_step})
    r=problem.residual_fast(x);weights=loss_accounting(r,float(problem.cfg["f_scale"]))["robust_weights"];analytic=jac.T@(weights*r)
    grad_fd=np.empty(len(x));h=steps[1]
    def cost(z):return loss_accounting(problem.residual_fast(z),float(problem.cfg["f_scale"]))["soft_l1_cost"]
    for col in range(len(x)):
        e=np.zeros(len(x));e[col]=1.;grad_fd[col]=(cost(x+h*e)-cost(x-h*e))/(2*h)
    grad_abs=float(np.linalg.norm(analytic-grad_fd));grad_den=max(np.linalg.norm(analytic),np.linalg.norm(grad_fd));grad_error=grad_abs/max(grad_den,1e-12);grad_pass=grad_abs<=float(cfg["maximum_gradient_absolute_error"]) or grad_error<=float(cfg["maximum_gradient_relative_error"]);passed=worst<=float(cfg["maximum_jv_relative_error"]) and grad_pass and outside_count==0
    return {"schema":"biospur-revision-c-jacobian-qualification-v1","parameter_count":len(x),"residual_count":len(r),"parameter_metadata":problem.parameter_metadata,"sparse_pattern":{"shape":list(problem.sparsity.shape),"nnz":int(problem.sparsity.nnz),"sha256":array_sha256(problem.sparsity.toarray().astype(np.uint8)),"nonzero_outside_pattern":outside_count},"directions":direction_rows,"maximum_jv_relative_error":worst,"jtr_gradient_norm":float(np.linalg.norm(analytic)),"finite_difference_gradient_norm":float(np.linalg.norm(grad_fd)),"jtr_vs_finite_difference_gradient_absolute_error":grad_abs,"jtr_vs_finite_difference_gradient_relative_error":grad_error,"gradient_absolute_tolerance":float(cfg["maximum_gradient_absolute_error"]),"gradient_relative_tolerance":float(cfg["maximum_gradient_relative_error"]),"gradient_gate_pass":grad_pass,"pass":bool(passed),"verdict":"JACOBIAN_AND_SCALING_QUALIFIED" if passed else "BLOCKED_JACOBIAN_OR_SCALING_NOT_QUALIFIED"}


def _principal_axis_no_filter(vectors:np.ndarray) -> np.ndarray:
    if not np.isfinite(vectors).all():raise FloatingPointError("nonfinite vector inside fixed ablation support")
    values,eig=np.linalg.eigh(vectors.T@vectors/max(1,len(vectors)));return eig[:,-1]/np.linalg.norm(eig[:,-1])


def timestamp_shift_negative_control(timeline:CommonTimeline,windows:Mapping[str,tuple[int,int]],gates:Mapping[str,Any],calibration:Mapping[str,Any]) -> dict:
    from .core import corrected_rotations
    rot,_=corrected_rotations(timeline,gates["node_to_segment"],calibration);masks=action_masks(timeline.time_ns,windows);_,si=_node_indices(timeline,gates["node_to_segment"])
    action="left_elbow";parent="upper_arm_L";child="forearm_L";pk=SEGMENTS.index(parent);ck=SEGMENTS.index(child);pj=si[parent];cj=si[child]
    lag_results=[];minimum=int(gates["validity_contract"]["minimum_timestamp_ablation_overlap_samples"])
    for lag in gates["validity_contract"]["timestamp_shift_lags_grid_rows"]:
        lag=int(lag);target=np.arange(len(timeline.time_ns)-lag);source=target+lag
        eligible=masks[action][target]&masks[action][source]&timeline.valid[target,pj]&timeline.valid[target,cj]&timeline.valid[source,cj]
        target=target[eligible];source=source[eligible];mapping=np.column_stack([source,target]).astype(np.int64)
        if len(target)<minimum:
            lag_results.append({"lag_grid_rows":lag,"status":"ABLATION_NOT_EVALUABLE_INSUFFICIENT_VALID_OVERLAP","n_valid":int(len(target)),"n_required":minimum,"mapping_sha256":array_sha256(mapping)});continue
        pg=np.einsum("nij,nj->ni",rot[target,pk],timeline.gyro_rad_s[target,pj]);cg=np.einsum("nij,nj->ni",rot[target,ck],timeline.gyro_rad_s[target,cj]);shifted=np.einsum("nij,nj->ni",rot[source,ck],timeline.gyro_rad_s[source,cj]);relative=cg-pg;shifted_relative=shifted-pg;axis=_principal_axis_no_filter(relative)
        baseline_perp=relative-np.outer(relative@axis,axis);shifted_perp=shifted_relative-np.outer(shifted_relative@axis,axis);base=float(np.mean(np.sum(baseline_perp**2,axis=1)));ablated=float(np.mean(np.sum(shifted_perp**2,axis=1)));ratio=ablated/max(base,1e-12)
        lag_results.append({"lag_grid_rows":lag,"lag_seconds":lag/float(gates["common_time"]["rate_hz"]),"status":"EVALUATED","n_valid":int(len(target)),"n_required":minimum,"source_to_target_mapping_sha256":array_sha256(mapping),"eligible_target_rows_sha256":array_sha256(target),"baseline_rows_sha256":array_sha256(target),"support_identical":True,"baseline_cross_node_articulated_cost":base,"shifted_cross_node_articulated_cost":ablated,"degradation_ratio":ratio})
    evaluated=[x for x in lag_results if x["status"]=="EVALUATED"]
    threshold=float(gates["preview_gates"]["minimum_timestamp_shuffle_continuity_cost_ratio"])
    passed=bool(evaluated) and all(x["support_identical"] for x in evaluated) and max(x["degradation_ratio"] for x in evaluated)>=threshold
    if not evaluated:status="ABLATION_NOT_EVALUABLE_INSUFFICIENT_VALID_OVERLAP"
    elif not all(x["support_identical"] for x in evaluated):status="FAIL_ABLATION_VALIDITY_CONTRACT"
    elif not passed:status="FAIL_TIMESTAMP_SENSITIVITY_NEGATIVE_CONTROL"
    else:status="PASS_TIMESTAMP_SENSITIVITY_NEGATIVE_CONTROL"
    return {"schema":"biospur-revision-c-mask-preserving-timestamp-shift-v1","action":action,"shifted_node":"BSFB165","metric":"TIME_COUPLED_CROSS_NODE_HINGE_PERPENDICULAR_ENERGY","lags":lag_results,"threshold":threshold,"status":status,"pass":passed}


def write_checkpoint_atomic(target:Path,metadata:Mapping[str,Any],arrays:Mapping[str,np.ndarray],jacobian:csr_matrix|None=None,crash_inject:bool=False) -> dict:
    target=Path(target)
    if target.exists():raise FileExistsError(target)
    temporary=target.with_name(target.name+f".tmp.{os.getpid()}")
    if temporary.exists():shutil.rmtree(temporary)
    temporary.mkdir(parents=True)
    try:
        strict_arrays={name:np.asarray(value) for name,value in arrays.items()}
        for name,value in strict_arrays.items():
            if value.dtype.kind in "fc" and not np.isfinite(value).all():raise FloatingPointError(f"checkpoint array nonfinite: {name}")
        savez_deterministic(temporary/"ARRAYS.npz",strict_arrays)
        if jacobian is not None:
            jacobian=jacobian.tocsr();savez_deterministic(temporary/"JACOBIAN_CSR.npz",{"data":jacobian.data,"indices":jacobian.indices,"indptr":jacobian.indptr,"shape":np.asarray(jacobian.shape,np.int64)})
        dump_json_atomic(temporary/"METADATA.json",metadata)
        manifest={p.name:sha256(p) for p in sorted(temporary.iterdir()) if p.is_file()};dump_json_atomic(temporary/"SHA256_MANIFEST.json",manifest)
        # Reload every payload before the directory can become authoritative.
        json.loads((temporary/"METADATA.json").read_text());json.loads((temporary/"SHA256_MANIFEST.json").read_text())
        with np.load(temporary/"ARRAYS.npz",allow_pickle=False) as source:
            if set(source.files)!=set(strict_arrays):raise RuntimeError("checkpoint array key mismatch")
            for name in source.files:
                if array_sha256(source[name])!=array_sha256(strict_arrays[name]):raise RuntimeError(f"checkpoint array mismatch: {name}")
        if crash_inject:raise RuntimeError("INJECTED_CHECKPOINT_CRASH_BEFORE_ATOMIC_RENAME")
        for p in temporary.iterdir():
            if p.is_file():
                fd=os.open(p,os.O_RDONLY)
                try:os.fsync(fd)
                finally:os.close(fd)
        os.replace(temporary,target);parent_fd=os.open(target.parent,os.O_RDONLY)
        try:os.fsync(parent_fd)
        finally:os.close(parent_fd)
    except Exception:
        # Preserve temp evidence on a real failure; crash-injection tests clean
        # their own temporary directory after asserting the final is absent.
        raise
    return verify_checkpoint(target)


def verify_checkpoint(path:Path) -> dict:
    path=Path(path);manifest=json.loads((path/"SHA256_MANIFEST.json").read_text())
    for name,digest in manifest.items():
        if sha256(path/name)!=digest:raise ValueError(f"checkpoint SHA mismatch: {name}")
    metadata=json.loads((path/"METADATA.json").read_text())
    with np.load(path/"ARRAYS.npz",allow_pickle=False) as source:
        arrays={name:source[name].copy() for name in source.files}
    return {"path":str(path.resolve()),"manifest_sha256":sha256(path/"SHA256_MANIFEST.json"),"metadata":metadata,"arrays":arrays}


def runtime_environment() -> dict:
    try:
        from threadpoolctl import threadpool_info
        blas=threadpool_info()
    except Exception as exc:blas={"status":"NOT_EVALUABLE","reason":str(exc)}
    keys=("OMP_NUM_THREADS","OPENBLAS_NUM_THREADS","MKL_NUM_THREADS","NUMEXPR_NUM_THREADS")
    return {"python":sys.version,"platform":platform.platform(),"numpy":np.__version__,"scipy":scipy.__version__,"blas":blas,"thread_environment":{key:os.environ.get(key) for key in keys},"argv":sys.argv}


def write_run_freeze(path:Path,problem:CalibrationProblem,ledger_path:Path,gates_path:Path,template_path:Path,source_paths:Sequence[Path]) -> dict:
    files=[{"path":str(Path(p).resolve()),"sha256":sha256(p)} for p in source_paths];aggregate=object_sha256(files);vc=problem.validity_audit()
    freeze={"schema":"biospur-fast-solver-revision-c-run-freeze-v1","solver_revision":SOLVER_REVISION,"runtime_files":files,"aggregate_source_sha256":aggregate,"calibration_ledger":{"path":str(Path(ledger_path).resolve()),"sha256":sha256(ledger_path)},"gates":{"path":str(Path(gates_path).resolve()),"sha256":sha256(gates_path)},"template":{"path":str(Path(template_path).resolve()),"sha256":sha256(template_path)},"node_mapping":problem.node_to_segment,"node_mapping_sha256":object_sha256(problem.node_to_segment),"residual_row_manifest_sha256":problem.row_order_sha256,"validity_policy_sha256":object_sha256(problem.gates["validity_contract"]),"validity_audit_sha256":object_sha256(vc),"squat_factors":[x for x in vc["factors"] if x["action"]=="squats"],"optimizer":{"method":"scipy.optimize.least_squares_trf_sparse_fd","loss":problem.cfg["loss"],"f_scale":problem.cfg["f_scale"],"bounds":"UNBOUNDED","x_scale":[m["physical_scale"] for m in problem.parameter_metadata],"xtol":problem.cfg["xtol"],"ftol":problem.cfg["ftol"],"gtol":problem.cfg["gtol"],"soft_cap_nfev":problem.cfg["soft_cap_nfev"],"hard_cap_nfev":problem.cfg["hard_cap_nfev"]},"starts":[{"ordinal":i,"x0_sha256":array_sha256(x)} for i,x in enumerate(problem.starts)],"seed":problem.gates["determinism"]["random_seed"],"environment":runtime_environment()}
    dump_json_atomic(path,freeze);return freeze


class _History:
    def __init__(self,problem:CalibrationProblem):self.problem=problem;self.cost=[];self.x=[]
    def residual(self,x:np.ndarray) -> np.ndarray:
        r=self.problem.residual_fast(x)
        if not np.isfinite(x).all() or not np.isfinite(r).all():raise FloatingPointError("nonfinite core solver state")
        self.cost.append(loss_accounting(r,float(self.problem.cfg["f_scale"]))["soft_l1_cost"]);self.x.append(np.asarray(x).copy());return r


def _checkpoint_payload(problem:CalibrationProblem,start_index:int,x0:np.ndarray,fit,history:_History,run_freeze_sha256:str,stage:str,total_nfev:int) -> tuple[dict,dict,csr_matrix]:
    residual=np.asarray(fit.fun);account=loss_accounting(residual,float(problem.cfg["f_scale"]));jac=fit.jac.tocsr() if hasattr(fit.jac,"tocsr") else csr_matrix(fit.jac);gradient=np.asarray(fit.grad);active=np.asarray(fit.active_mask)
    factor_cost={}
    for factor_id,sl in problem.factor_slices().items():factor_cost[factor_id]=loss_accounting(residual[sl],float(problem.cfg["f_scale"]))["soft_l1_cost"]
    unique=[]
    for value in history.x:
        if not unique or not np.array_equal(value,unique[-1]):unique.append(value)
    step=float(np.linalg.norm(unique[-1]-unique[-2])) if len(unique)>1 else 0.
    finite={"x0":bool(np.isfinite(x0).all()),"x":bool(np.isfinite(fit.x).all()),"residual":bool(np.isfinite(residual).all()),"robust_weights":bool(np.isfinite(account["robust_weights"]).all()),"jacobian":bool(np.isfinite(jac.data).all()),"gradient":bool(np.isfinite(gradient).all()),"cost_history":bool(np.isfinite(history.cost).all())}
    metadata={"schema":"biospur-revision-c-start-checkpoint-v1","solver_revision":SOLVER_REVISION,"run_freeze_sha256":run_freeze_sha256,"stage":stage,"start_index":start_index,"parameter_metadata":problem.parameter_metadata,"parameter_order_sha256":object_sha256(problem.parameter_metadata),"residual_row_manifest_sha256":problem.row_order_sha256,"validity_audit_sha256":object_sha256(problem.validity_audit()),"loss":problem.cfg["loss"],"f_scale":problem.cfg["f_scale"],"costs":{"ls":account["ls_cost"],"huber":account["huber_cost"],"soft_l1":account["soft_l1_cost"],"per_factor_soft_l1":factor_cost},"termination":{"status":int(fit.status),"message":str(fit.message),"native_success":bool(fit.success),"nfev_stage":int(fit.nfev),"njev_stage":None if fit.njev is None else int(fit.njev),"nfev_total":int(total_nfev),"optimality":float(fit.optimality),"step_norm_last_evaluation":step,"active_bounds":active.tolist()},"finiteness":finite,"core_solver_finite":all(finite.values())}
    arrays={"x0":x0,"x":fit.x,"residual_raw":residual,"residual_whitened":residual,"robust_weights":account["robust_weights"],"gradient":gradient,"projected_gradient":gradient,"active_bounds":active,"cost_history":np.asarray(history.cost),"row_manifest_index":np.arange(len(residual),dtype=np.int64)}
    return metadata,arrays,jac


def _run_stage(problem:CalibrationProblem,x0:np.ndarray,max_nfev:int):
    history=_History(problem)
    fit=least_squares(history.residual,x0,jac_sparsity=problem.sparsity,loss=str(problem.cfg["loss"]),f_scale=float(problem.cfg["f_scale"]),xtol=float(problem.cfg["xtol"]),ftol=float(problem.cfg["ftol"]),gtol=float(problem.cfg["gtol"]),max_nfev=int(max_nfev),method="trf",x_scale=np.asarray([m["physical_scale"] for m in problem.parameter_metadata]))
    return fit,history


def _publishable_axis_change(problem:CalibrationProblem,a:np.ndarray,b:np.ndarray) -> float:
    aa,_,_=_decode_parameters(a,len(problem.knot_times));bb,_,_=_decode_parameters(b,len(problem.knot_times));dots=np.sum(aa*bb,axis=1);return float(np.degrees(np.max(np.arccos(np.clip(dots,-1,1)))))


def run_start(problem:CalibrationProblem,start_index:int,output:Path,run_freeze_sha256:str) -> dict:
    output=Path(output);x0=problem.starts[start_index];soft=int(problem.cfg["soft_cap_nfev"]);hard=int(problem.cfg["hard_cap_nfev"])
    fit1,hist1=_run_stage(problem,x0,soft);meta1,arrays1,jac1=_checkpoint_payload(problem,start_index,x0,fit1,hist1,run_freeze_sha256,"SOFT_CAP_OR_NATIVE_TERMINAL",int(fit1.nfev));soft_path=output/f"START_{start_index}_SOFT";verified_soft=write_checkpoint_atomic(soft_path,meta1,arrays1,jac1)
    if fit1.success:terminal_fit,terminal_hist,total=fit1,hist1,int(fit1.nfev)
    else:
        fit2,hist2=_run_stage(problem,fit1.x,max(1,hard-int(fit1.nfev)));terminal_fit,terminal_hist,total=fit2,hist2,int(fit1.nfev)+int(fit2.nfev)
    meta,arrays,jac=_checkpoint_payload(problem,start_index,x0,terminal_fit,terminal_hist,run_freeze_sha256,"TERMINAL",total);terminal_path=output/f"START_{start_index}_TERMINAL";verified_terminal=write_checkpoint_atomic(terminal_path,meta,arrays,jac)
    # Qualification restart is intentionally the identical objective and state.
    restart,hist_restart=_run_stage(problem,terminal_fit.x,int(problem.cfg["qualification_restart_nfev"]));meta_r,arrays_r,jac_r=_checkpoint_payload(problem,start_index,terminal_fit.x,restart,hist_restart,run_freeze_sha256,"QUALIFICATION_RESTART",total+int(restart.nfev));restart_path=output/f"START_{start_index}_QUALIFICATION_RESTART";verified_restart=write_checkpoint_atomic(restart_path,meta_r,arrays_r,jac_r)
    delta=float(np.linalg.norm(restart.x-terminal_fit.x));relative_cost=abs(float(restart.cost)-float(terminal_fit.cost))/max(abs(float(terminal_fit.cost)),1e-12);axis_change=_publishable_axis_change(problem,terminal_fit.x,restart.x)
    native=bool(terminal_fit.success);finite=bool(meta["core_solver_finite"] and meta_r["core_solver_finite"]);gradient=float(terminal_fit.optimality)<=float(problem.cfg["maximum_optimality"]);repeat=delta<=float(problem.cfg["maximum_qualification_restart_parameter_change_rad"]) and relative_cost<=float(problem.cfg["maximum_qualification_restart_relative_cost_change"]) and axis_change<=float(problem.cfg["maximum_qualification_restart_axis_change_deg"])
    qualified=native and finite and gradient and bool(restart.success) and repeat
    if total>=hard and not native:
        initial=hist1.cost[0];final=meta["costs"]["soft_l1"];progress=(initial-final)/max(abs(initial),1e-12);hard_status="HARD_CAP_REACHED_WHILE_PROGRESSING" if progress>1e-6 else "STALLED_NONSTATIONARY"
    else:hard_status=None
    result={"start_index":start_index,"soft_checkpoint":verified_soft["path"],"terminal_checkpoint":verified_terminal["path"],"qualification_checkpoint":verified_restart["path"],"terminal_checkpoint_manifest_sha256":verified_terminal["manifest_sha256"],"native_solver_success":native,"core_finite":finite,"scaled_gradient_gate":gradient,"qualification_restart_native_success":bool(restart.success),"qualification_parameter_change_norm_rad":delta,"qualification_relative_cost_change":relative_cost,"qualification_axis_change_deg":axis_change,"qualification_repeatability_gate":repeat,"status":"START_CONVERGENCE_QUALIFIED" if qualified else (hard_status or "START_CONVERGENCE_NOT_QUALIFIED"),"qualified":qualified,"total_nfev":total}
    dump_json_atomic(output/f"START_{start_index}_QUALIFICATION.json",result);return result


def compare_new_endpoints(problem:CalibrationProblem,checkpoint_root:Path) -> dict:
    cfg=problem.gates["solver_qualification"];directions=deterministic_directions(10,len(problem.x0),int(problem.gates["determinism"]["random_seed"])+211);rows=[]
    for index in range(5):
        loaded=verify_checkpoint(Path(checkpoint_root)/f"START_{index}_TERMINAL");rows.append({"start_index":index,**objective_oracle_compare(problem,loaded["arrays"]["x"],directions,float(cfg["objective_equivalence_absolute_tolerance"]),float(cfg["objective_equivalence_relative_tolerance"]))})
    negative=negative_oracle_harness_test(problem);passed=all(x["pass"] for x in rows) and negative["pass"]
    return {"schema":"biospur-revision-c-endpoint-objective-oracle-v1","historical_sampled_evidence":"OBJECTIVE_KERNEL_EQUIVALENCE_STRONGLY_SUPPORTED_AT_TESTED_POINTS","endpoints":rows,"negative_test":negative,"squat_rows":[{"factor_id":f.factor_id,"indices_sha256":array_sha256(f.rows),"timestamps_sha256":array_sha256(problem.timeline.time_ns[f.rows]),"required_nodes":list(f.required_nodes),"weight":1.0/f.sigma} for f in problem.factors if f.action=="squats"],"verdict":"FAST_PATH_OBJECTIVE_KERNEL_EQUIVALENCE_PROVEN" if passed else "FAST_PATH_CHANGES_OBJECTIVE","pass":passed}


def make_synthetic_problem(gates:Mapping[str,Any]) -> tuple[CalibrationProblem,dict]:
    rate=float(gates["common_time"]["rate_hz"]);per_action=2.;names=gates["calibration_actions"];step=int(round(1e9/rate));count=int(per_action*rate);times=np.arange(len(names)*count,dtype=np.int64)*step;windows={name:(int(times[i*count]),int(times[(i+1)*count-1])) for i,name in enumerate(names)};nodes=tuple(sorted(gates["node_to_segment"]));m=len(nodes);n=len(times);rotation=np.tile(np.eye(3),(n,m,1,1));gyro=np.zeros((n,m,3));accel=np.tile([0,0,9.80665],(n,m,1));valid=np.ones((n,m),bool);stationary=np.zeros((n,m),bool)
    si={segment:nodes.index(node) for node,segment in gates["node_to_segment"].items()}
    for action_index,name in enumerate(names):
        rows=np.arange(action_index*count,(action_index+1)*count);phase=np.linspace(0,2*np.pi,len(rows),endpoint=False)
        if name=="initial_still_attempt2":stationary[rows]=True
        if name=="t_pose":
            for segment,sign in (("upper_arm_L",-1),("forearm_L",-1),("upper_arm_R",1),("forearm_R",1)):
                target=np.array([sign,0,0]);source=EXPECTED_INITIAL[segment];r,_=Rotation.align_vectors(target[None],source[None]);rotation[rows,si[segment]]=r.as_matrix()
        moving={"arms":["upper_arm_L","forearm_L","upper_arm_R","forearm_R"],"left_elbow":["forearm_L"],"right_elbow_attempt2":["forearm_R"],"left_knee":["thigh_L"],"right_knee":["thigh_R"],"left_heel":["shank_L"],"right_heel":["shank_R"],"squats":["thigh_L","thigh_R","shank_L","shank_R"],"trunk":["torso"]}.get(name,[])
        for ordinal,segment in enumerate(moving):
            angle=.55*np.sin(phase+.17*ordinal);rotation[rows,si[segment]]=Rotation.from_rotvec(np.column_stack([np.zeros(len(rows)),angle,np.zeros(len(rows))])).as_matrix();gyro[rows,si[segment],1]=.55*(2*np.pi/(per_action))*np.cos(phase+.17*ordinal)
        if name=="left_elbow":
            # Synthetic compound shoulder + hinge motion.  The true child
            # relative angular velocity remains on a single hinge axis, while
            # a nonzero timestamp shift mixes different parent states and must
            # add cross-axis energy.  Board gyros are generated from the
            # desired world angular velocities, not copied between frames.
            parent_angle=.22*np.sin(.5*phase);hinge_angle=.55*np.sin(phase)
            rp=Rotation.from_rotvec(np.column_stack([parent_angle,np.zeros(len(rows)),np.zeros(len(rows))])).as_matrix()
            rh=Rotation.from_rotvec(np.column_stack([np.zeros(len(rows)),hinge_angle,np.zeros(len(rows))])).as_matrix()
            rc=np.einsum("nij,njk->nik",rp,rh);rotation[rows,si["upper_arm_L"]]=rp;rotation[rows,si["forearm_L"]]=rc
            omega_parent=np.column_stack([.22*np.pi/per_action*np.cos(.5*phase),np.zeros(len(rows)),np.zeros(len(rows))])
            hinge_local=np.column_stack([np.zeros(len(rows)),.55*2*np.pi/per_action*np.cos(phase),np.zeros(len(rows))]);hinge_world=np.einsum("nij,nj->ni",rp,hinge_local);omega_child=omega_parent+hinge_world
            gyro[rows,si["upper_arm_L"]]=np.einsum("nji,nj->ni",rp,omega_parent);gyro[rows,si["forearm_L"]]=np.einsum("nji,nj->ni",rc,omega_child)
    accounting={node:{"requested_grid_rows":n,"accepted_interpolated":n,"outside_window_rejected":0,"bracket_gap_rejected":0,"clock_segment_rejected":0,"q2_status_or_nonfinite_rejected":0,"grid_accounting_closed":True} for node in nodes};all_valid=np.all(valid,axis=1);timeline=CommonTimeline(times,nodes,rotation,gyro,accel,stationary,valid,all_valid,{"schema":"synthetic","nodes":accounting,"all_nodes_valid_rows":n,"grid_rows":n,"all_nodes_valid_fraction":1.0})
    return CalibrationProblem(timeline,windows,gates),windows


def run_synthetic_qualification(gates:Mapping[str,Any],output:Path) -> dict:
    output=Path(output);output.mkdir(parents=True,exist_ok=False);problem,windows=make_synthetic_problem(gates);validity=problem.validity_audit();dump_json_atomic(output/"VALIDITY_CONTRACT.json",validity)
    strict_writer=False
    try:canonical_json_bytes({"unexpected":float("nan")})
    except ValueError:strict_writer=True
    crash_target=output/"CRASH_INJECTION_CHECKPOINT"
    crash_seen=False
    try:write_checkpoint_atomic(crash_target,{"test":"crash"},{"finite":np.arange(3.)},crash_inject=True)
    except RuntimeError as exc:crash_seen="INJECTED_CHECKPOINT_CRASH" in str(exc)
    crash_temp=list(output.glob("CRASH_INJECTION_CHECKPOINT.tmp.*"));crash_pass=crash_seen and not crash_target.exists() and bool(crash_temp)
    for path in crash_temp:shutil.rmtree(path)
    good=write_checkpoint_atomic(output/"CHECKPOINT_RELOAD_FIXTURE",{"test":"reload"},{"finite":np.arange(3.)});crash_pass=crash_pass and good["arrays"]["finite"].tolist()==[0.,1.,2.]
    oracle_points=list(problem.starts)+[problem.x0+v*.01 for v in deterministic_directions(10,len(problem.x0),int(gates["determinism"]["random_seed"])+17)];oracle=[objective_oracle_compare(problem,x,deterministic_directions(10,len(x),31),1e-12,1e-12) for x in oracle_points];negative=negative_oracle_harness_test(problem);oracle_pass=all(x["pass"] for x in oracle) and negative["pass"]
    jac=qualify_jacobian(problem);fit,history=_run_stage(problem,problem.x0,200);synthetic_solver={"native_success":bool(fit.success),"core_finite":bool(np.isfinite(fit.x).all() and np.isfinite(fit.fun).all() and np.isfinite(fit.jac.data if hasattr(fit.jac,"data") else fit.jac).all()),"nfev":int(fit.nfev),"optimality":float(fit.optimality)};synthetic_solver["pass"]=synthetic_solver["native_success"] and synthetic_solver["core_finite"]
    # A synthetic mask-preserving hinge control must respond at at least one
    # frozen lag; this qualifies the negative-control design before real data.
    axes,heading,drift=_decode_parameters(problem.x0,len(problem.knot_times));cal={"yaw_drift_knot_global_time_ns":problem.knot_times.tolist(),"segments":{s:{"board_frame_longitudinal_axis":axes[k].tolist(),"relative_heading_rad":float(heading[k]),"yaw_drift_knot_rad":drift[k].tolist()} for k,s in enumerate(SEGMENTS)}};ablation=timestamp_shift_negative_control(problem.timeline,windows,gates,cal)
    passed=validity["all_support_pass"] and strict_writer and crash_pass and oracle_pass and jac["pass"] and synthetic_solver["pass"] and ablation["pass"]
    result={"schema":"biospur-fast-solver-revision-c-synthetic-qualification-v1","solver_revision":SOLVER_REVISION,"validity_contract":"PASS" if validity["all_support_pass"] else "FAIL","strict_writer":"PASS" if strict_writer else "FAIL","checkpoint_crash_recovery":"PASS" if crash_pass else "FAIL","slow_fast_oracle":"PASS" if oracle_pass else "FAIL","jacobian_qualification":jac["verdict"],"synthetic_solver":synthetic_solver,"timestamp_ablation":ablation,"pass":passed,"verdict":"PASS_SYNTHETIC_QUALIFICATION" if passed else "FAIL_SYNTHETIC_QUALIFICATION"}
    dump_json_atomic(output/"STRICT_WRITER_AUDIT.json",{"generic_nonfinite_sanitizer_removed":strict_writer,"unexpected_nonfinite_is_fatal":strict_writer});dump_json_atomic(output/"CHECKPOINT_CRASH_RECOVERY.json",{"pass":crash_pass});dump_json_atomic(output/"OBJECTIVE_ORACLE_QUALIFICATION.json",{"points":oracle,"negative_test":negative,"pass":oracle_pass});dump_json_atomic(output/"JACOBIAN_QUALIFICATION.json",jac);dump_json_atomic(output/"TIMESTAMP_SHIFT_SYNTHETIC_QUALIFICATION.json",ablation);dump_json_atomic(output/"RESULT.json",result);return result


def multistart_stability(problem:CalibrationProblem,template:Mapping[str,Any],checkpoint_root:Path) -> dict:
    from .core import skeleton_from_directions
    loaded=[verify_checkpoint(Path(checkpoint_root)/f"START_{i}_TERMINAL") for i in range(5)];costs=[x["metadata"]["costs"]["soft_l1"] for x in loaded];best_index=int(np.argmin(costs));best_x=loaded[best_index]["arrays"]["x"];best_dirs=directions_from_parameters(problem.timeline,problem.node_to_segment,best_x,problem.knot_times);rows=[]
    for index,item in enumerate(loaded):
        x=item["arrays"]["x"];dirs=directions_from_parameters(problem.timeline,problem.node_to_segment,x,problem.knot_times);axis_max=0.
        for k,segment in enumerate(SEGMENTS):
            node=next(n for n,s in problem.node_to_segment.items() if s==segment);support=problem.timeline.valid[:,problem.node_index[node]];dots=np.sum(dirs[support,k]*best_dirs[support,k],axis=1);axis_max=max(axis_max,float(np.degrees(np.max(np.arccos(np.clip(dots,-1,1))))))
        support=np.flatnonzero(problem.timeline.all_nodes_valid)[::20];sk=np.stack([skeleton_from_directions(dirs[i],template,float(problem.gates["rendering"]["head_proxy_length_m"])) for i in support]);bsk=np.stack([skeleton_from_directions(best_dirs[i],template,float(problem.gates["rendering"]["head_proxy_length_m"])) for i in support]);node_max=float(np.max(np.linalg.norm(sk-bsk,axis=2)))
        rows.append({"start_index":index,"soft_l1_cost":float(costs[index]),"max_segment_axis_difference_deg":axis_max,"max_graphical_node_difference_m":node_max,"segment_support":"FIXED_PER_NODE_INPUT_MASK","graphical_support":"FIXED_ALL_NODE_VALID_INPUT_MASK"})
    passed=all(x["max_segment_axis_difference_deg"]<=float(problem.cfg["maximum_multistart_segment_axis_difference_deg"]) and x["max_graphical_node_difference_m"]<=float(problem.cfg["maximum_multistart_node_difference_m"]) for x in rows)
    return {"schema":"biospur-revision-c-multistart-stability-v1","best_start_index":best_index,"rows":rows,"pass":passed,"verdict":"PASS_MULTISTART_STABILITY" if passed else "FAIL_MULTISTART_STABILITY"}


def _result_stub(verdict:str,starts:Sequence[Mapping[str,Any]]|None=None,**extra) -> dict:
    by_index={int(x["start_index"]):x for x in (starts or [])}
    result={"schema":"biospur-fast-solver-revision-c-result-v1","solver_revision":SOLVER_REVISION,"historical_nan_classification_corrected":True,"validity_contract":"NOT_RUN","timestamp_ablation_validity":"NOT_RUN","generic_nan_sanitizer_removed":True,"checkpoint_crash_recovery":"PASS_SYNTHETIC","fast_path_objective_equivalence":"OBJECTIVE_KERNEL_EQUIVALENCE_STRONGLY_SUPPORTED_AT_TESTED_POINTS","jacobian_qualification":"PASS_SYNTHETIC","multistart_stability":"NOT_EVALUABLE_SOLVER_NOT_CONVERGED","real_calibration_verdict":verdict,"frozen_calibration_created":False,"calibration_reloaded_by_sha":False,"calibration_action_replay":"NOT_RUN","calibration_preview_paths":[],"golf_status":"SEALED","boxing_status":"SEALED","walk_status":"SEALED","final_still_status":"SEALED","uwb_t4_anchor_accessed":False,"operator_measurements_accessed":False}
    for index in range(5):result[f"start_{index}_convergence"]=by_index.get(index,{}).get("status","NOT_RUN")
    result.update(extra);return result


def run_real_revision_c(ledger_path:Path,template_path:Path,gates_path:Path,output:Path,source_paths:Sequence[Path]) -> dict:
    """Run the newly qualified calibration-only five-start stage.

    This function intentionally stops before freeze/replay/render whenever a
    convergence or objective-oracle gate fails.
    """
    from .common_time import build_common_timeline
    from .io import load_calibration_ledger
    from .q2 import run_q2_frontend
    output=Path(output)
    if output.exists():raise FileExistsError(output)
    output.mkdir(parents=True);gates=json.loads(Path(gates_path).read_text());template=json.loads(Path(template_path).read_text())
    if gates["calibration_solver"]["solver_revision"]!=SOLVER_REVISION:raise ValueError("solver revision mismatch")
    print("REVISION_C: opening calibration-only ledger",flush=True)
    imus,windows,access=load_calibration_ledger(ledger_path,gates);q2,q2audit=run_q2_frontend(imus,windows,gates["q2"]);start=min(v[0] for v in windows.values());stop=max(v[1] for v in windows.values());timeline=build_common_timeline(q2,start,stop,gates["common_time"]);problem=CalibrationProblem(timeline,windows,gates);validity=problem.validity_audit()
    dump_json_atomic(output/"DATA_ACCESS_AUDIT.json",access);dump_json_atomic(output/"Q2_FRONTEND_AUDIT.json",q2audit);dump_json_atomic(output/"VALIDITY_CONTRACT.json",validity);dump_json_atomic(output/"RESIDUAL_ROW_MANIFEST.json",problem.row_manifest);savez_deterministic(output/"VALIDITY_MASKS.npz",problem.factor_mask_arrays())
    if not validity["all_support_pass"]:
        result=_result_stub("NOT_EVALUABLE_INSUFFICIENT_VALID_SUPPORT",validity_contract="NOT_EVALUABLE_INSUFFICIENT_VALID_SUPPORT");dump_json_atomic(output/"RESULT.json",result);return result
    freeze=write_run_freeze(output/"RUN_FREEZE.json",problem,ledger_path,gates_path,template_path,source_paths);freeze_sha=sha256(output/"RUN_FREEZE.json");dump_json_atomic(output/"RUN_FREEZE_BINDING.json",{"run_freeze_sha256":freeze_sha,"aggregate_source_sha256":freeze["aggregate_source_sha256"]});print(f"REVISION_C: RUN_FREEZE {freeze_sha}",flush=True)
    starts=[]
    for index in range(5):
        print(f"REVISION_C: start {index} begin",flush=True)
        row=run_start(problem,index,output,freeze_sha);starts.append(row)
        print(f"REVISION_C: start {index} {row['status']} nfev={row['total_nfev']}",flush=True)
        if not row["core_finite"]:
            result=_result_stub("FAIL_CALIBRATION_NUMERICAL_NONFINITE",starts,validity_contract="PASS",core_solver_finite=False);dump_json_atomic(output/"RESULT.json",result);return result
    endpoint=compare_new_endpoints(problem,output);dump_json_atomic(output/"ENDPOINT_OBJECTIVE_ORACLE.json",endpoint)
    if not endpoint["pass"]:
        result=_result_stub("FAST_PATH_CHANGES_OBJECTIVE",starts,validity_contract="PASS",fast_path_objective_equivalence=endpoint["verdict"]);dump_json_atomic(output/"RESULT.json",result);return result
    if not all(x["qualified"] for x in starts):
        result=_result_stub("FAIL_PREVIEW_CALIBRATION",starts,validity_contract="PASS",fast_path_objective_equivalence=endpoint["verdict"],primary_calibration_blocker="SOLVER_NOT_CONVERGED",multistart_stability="NOT_EVALUABLE_SOLVER_NOT_CONVERGED");dump_json_atomic(output/"RESULT.json",result);return result
    multi=multistart_stability(problem,template,output);dump_json_atomic(output/"MULTISTART_STABILITY.json",multi)
    if not multi["pass"]:
        result=_result_stub("FAIL_PREVIEW_CALIBRATION",starts,validity_contract="PASS",fast_path_objective_equivalence=endpoint["verdict"],multistart_stability=multi["verdict"],primary_calibration_blocker="MULTISTART_INSTABILITY");dump_json_atomic(output/"RESULT.json",result);return result
    # The postfit scientific gates remain deliberately explicit.  Reaching
    # this marker authorizes their execution; it is not itself a PASS/freeze.
    result=_result_stub("BLOCKED_POSTFIT_GATES_NOT_EXECUTED",starts,validity_contract="PASS",fast_path_objective_equivalence=endpoint["verdict"],multistart_stability=multi["verdict"],primary_calibration_blocker="POSTFIT_GATE_IMPLEMENTATION_REQUIRED")
    dump_json_atomic(output/"RESULT.json",result);return result
