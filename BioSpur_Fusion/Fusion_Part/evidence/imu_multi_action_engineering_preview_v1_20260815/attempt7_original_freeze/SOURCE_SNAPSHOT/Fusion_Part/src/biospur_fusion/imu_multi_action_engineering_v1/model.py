"""Unified eleven-action shared calibration objective and production Jacobian."""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any,Mapping

import numpy as np
from scipy.optimize import least_squares
from scipy.sparse import lil_matrix
from scipy.spatial.transform import Rotation

from biospur_fusion.imu_preview_v0.core import EXPECTED_INITIAL,EXPECTED_TPOSE

SEGMENTS=("pelvis","torso","upper_arm_L","upper_arm_R","forearm_L","forearm_R","thigh_L","thigh_R","shank_L","shank_R")
FUNCTIONAL=("shoulder_L","shoulder_R","elbow_L","elbow_R","hip_L","hip_R","knee_L","knee_R","trunk_turn","trunk_flex")
ZEROS=("elbow_L","elbow_R","hip_L","hip_R","knee_L","knee_R","trunk")
ACTIONS=("initial_still_attempt2","t_pose","arms","left_elbow","right_elbow_attempt2","left_knee","right_knee","left_heel","right_heel","squats","trunk")


def normalize(v:np.ndarray)->np.ndarray:
    v=np.asarray(v,float);return v/max(np.linalg.norm(v),1e-15)


def angles_to_axis(theta:float,phi:float)->np.ndarray:
    return np.array([math.cos(phi)*math.cos(theta),math.cos(phi)*math.sin(theta),math.sin(phi)])


def axis_to_angles(v:np.ndarray)->tuple[float,float]:
    v=normalize(v);return math.atan2(v[1],v[0]),float(np.clip(math.asin(float(np.clip(v[2],-1,1))),-math.pi/2+1e-6,math.pi/2-1e-6))


def _tangent_basis(reference:np.ndarray)->tuple[np.ndarray,np.ndarray]:
    reference=normalize(reference)
    seed=np.array([1.,0.,0.]) if abs(reference[0])<.9 else np.array([0.,1.,0.])
    first=normalize(seed-reference*float(seed@reference))
    return first,normalize(np.cross(reference,first))


def tangent_to_axis(reference:np.ndarray,coordinates:np.ndarray)->np.ndarray:
    """S2 exponential chart with a nonsingular origin at a pose reference."""
    reference=normalize(reference);first,second=_tangent_basis(reference)
    tangent=float(coordinates[0])*first+float(coordinates[1])*second
    angle=float(np.linalg.norm(tangent))
    if angle<1e-12:return normalize(reference+tangent)
    return normalize(math.cos(angle)*reference+math.sin(angle)*tangent/angle)


def axis_to_tangent(reference:np.ndarray,axis:np.ndarray)->np.ndarray:
    """Inverse S2 chart used only to migrate an equivalent old checkpoint."""
    reference=normalize(reference);axis=normalize(axis);first,second=_tangent_basis(reference)
    cosine=float(np.clip(reference@axis,-1.,1.));angle=math.acos(cosine)
    if angle<1e-12:return np.zeros(2)
    tangent=axis-cosine*reference
    if np.linalg.norm(tangent)<1e-12:raise ValueError("antipodal S2 chart is not uniquely invertible")
    tangent=angle*normalize(tangent)
    return np.array([tangent@first,tangent@second])


def s2_log(reference:np.ndarray,directions:np.ndarray)->np.ndarray:
    """Geodesic S2 residual vectors in the reference tangent plane."""
    reference=normalize(reference);directions=np.asarray(directions,float)
    scalar=directions.ndim==1
    values=directions[None] if scalar else directions
    values=values/np.maximum(np.linalg.norm(values,axis=1,keepdims=True),1e-15)
    cosine=np.clip(values@reference,-1.,1.);angles=np.arccos(cosine)
    tangent=values-cosine[:,None]*reference
    norm=np.linalg.norm(tangent,axis=1);scale=np.divide(angles,norm,out=np.ones_like(angles),where=norm>1e-12)
    result=tangent*scale[:,None]
    return result[0] if scalar else result


def yaw(angle:float)->np.ndarray:
    c=math.cos(angle);s=math.sin(angle);return np.array([[c,-s,0.],[s,c,0.],[0.,0.,1.]])


def parameter_layout()->dict:
    entries=[];cursor=0
    for segment in SEGMENTS:
        entries.append({"name":f"segment_axis:{segment}","block":"sensor_axis_quotient","start":cursor,"stop":cursor+2,"units":"rad"});cursor+=2
    for segment in SEGMENTS[1:]:
        entries.append({"name":f"relative_heading:{segment}","block":"relative_heading","start":cursor,"stop":cursor+1,"units":"rad"});cursor+=1
    for name in FUNCTIONAL:
        entries.append({"name":f"functional_axis:{name}","block":"functional_axis_or_subspace","start":cursor,"stop":cursor+2,"units":"rad"});cursor+=2
    for name in ZEROS:
        entries.append({"name":f"neutral_zero:{name}","block":"joint_neutral_zero","start":cursor,"stop":cursor+1,"units":"rad"});cursor+=1
    for pose in ("initial_still_attempt2","t_pose"):
        for segment in SEGMENTS:
            entries.append({"name":f"latent_pose:{pose}:{segment}","block":"latent_pose_reference_tangent_chart","start":cursor,"stop":cursor+2,"units":"rad"});cursor+=2
    assert cursor==96
    return {"schema":"biospur-engineering-preview-parameter-layout-v1","dimension":cursor,"global_yaw_gauge":"PELVIS_RELATIVE_HEADING_FIXED_ZERO","entries":entries}


LAYOUT=parameter_layout()


def _decode(x:np.ndarray):
    cursor=0;axes={}
    for segment in SEGMENTS:axes[segment]=angles_to_axis(float(x[cursor]),float(x[cursor+1]));cursor+=2
    headings={"pelvis":0.}
    for segment in SEGMENTS[1:]:headings[segment]=float(x[cursor]);cursor+=1
    functional={}
    for name in FUNCTIONAL:functional[name]=angles_to_axis(float(x[cursor]),float(x[cursor+1]));cursor+=2
    zeros={}
    for name in ZEROS:zeros[name]=float(x[cursor]);cursor+=1
    latent={}
    for pose in ("initial_still_attempt2","t_pose"):
        latent[pose]={}
        expected=EXPECTED_INITIAL if pose=="initial_still_attempt2" else EXPECTED_TPOSE
        for segment in SEGMENTS:latent[pose][segment]=tangent_to_axis(expected[segment],x[cursor:cursor+2]);cursor+=2
    return axes,headings,functional,zeros,latent


def migrate_spherical_latent_checkpoint(x:np.ndarray)->np.ndarray:
    """Map the old pole-singular nuisance coordinates to the same S2 vectors."""
    migrated=np.asarray(x,float).copy();cursor=56
    for pose in ("initial_still_attempt2","t_pose"):
        expected=EXPECTED_INITIAL if pose=="initial_still_attempt2" else EXPECTED_TPOSE
        for segment in SEGMENTS:
            old_axis=angles_to_axis(float(x[cursor]),float(x[cursor+1]))
            migrated[cursor:cursor+2]=axis_to_tangent(expected[segment],old_axis);cursor+=2
    assert cursor==96
    return migrated


def _phase(segmentation:Mapping[str,Any],action:str,name:str)->np.ndarray:
    for item in segmentation["actions"][action]:
        if item["phase"]==name:return np.asarray(item["row_indices"],int)
    raise KeyError((action,name))


@dataclass
class Objective:
    timeline:Any
    segmentation:Mapping[str,Any]
    node_to_segment:Mapping[str,str]
    cfg:Mapping[str,float]

    def __post_init__(self):
        nodes={node:i for i,node in enumerate(self.timeline.node_order)};self.segment_node={segment:nodes[node] for node,segment in self.node_to_segment.items()};self.block_order=[];self.static_sigma={};self.static_reference_audit={};self.used_rows=np.unique([row for phases in self.segmentation["actions"].values() for phase in phases for row in phase["row_indices"]]);self.row_lookup=np.full(len(self.timeline.time_ns),-1,int);self.row_lookup[self.used_rows]=np.arange(len(self.used_rows));self._evaluation_cache=None
        for pose,phase in (("initial_still_attempt2","neutral_soft_pose"),("t_pose","independent_t_pose_soft_pose")):
            rows=_phase(self.segmentation,pose,phase);self.static_reference_audit[pose]={}
            for segment in SEGMENTS:
                j=self.segment_node[segment];rotation=Rotation.from_matrix(self.timeline.rotation[rows,j]);mean=rotation.mean();dispersion=rotation*mean.inv();angles=dispersion.magnitude();empirical=float(np.percentile(angles,68));floor=math.radians(float(self.cfg["minimum_static_model_mismatch_sigma_deg"]));sigma=max(empirical,floor);q2_sigma=np.sqrt(np.maximum(np.trace(self.timeline.covariance_rad2[rows,j],axis1=1,axis2=2)/3.,0.));self.static_sigma[(pose,segment)]=sigma;self.static_reference_audit[pose][segment]={"sample_rows":int(len(rows)),"empirical_rotation_dispersion_p68_deg":math.degrees(empirical),"model_mismatch_floor_deg":math.degrees(floor),"model_mismatch_sigma_deg":math.degrees(sigma),"q2_angular_sigma_p50_deg":math.degrees(float(np.median(q2_sigma))),"q2_angular_sigma_p95_deg":math.degrees(float(np.percentile(q2_sigma,95))),"per_sample_effective_sigma":"sqrt(model_mismatch_sigma_rad^2 + trace(q2_covariance_rad2)/3)","latent_reference_estimated":True,"residual_geometry":"GEODESIC_S2_LOG"}

    def prepare_evaluation(self,x:np.ndarray)->None:
        decoded=_decode(x);axes,headings,_,_,_=decoded;values={}
        for segment in SEGMENTS:
            j=self.segment_node[segment];rotation=np.einsum("ij,njk->nik",yaw(headings[segment]),self.timeline.rotation[self.used_rows,j]);values[segment]=(rotation,np.einsum("nij,j->ni",rotation,axes[segment]),np.einsum("nij,nj->ni",rotation,self.timeline.gyro_rad_s[self.used_rows,j]))
        self._evaluation_cache=(decoded,values)

    def corrected(self,x:np.ndarray,rows:np.ndarray,segment:str)->tuple[np.ndarray,np.ndarray,np.ndarray]:
        if self._evaluation_cache is not None:
            local=self.row_lookup[rows]
            if np.all(local>=0):return tuple(value[local] for value in self._evaluation_cache[1][segment])
        axes,headings,_,_,_=_decode(x);j=self.segment_node[segment];r=np.einsum("ij,njk->nik",yaw(headings[segment]),self.timeline.rotation[rows,j]);direction=np.einsum("nij,j->ni",r,axes[segment]);gyro=np.einsum("nij,nj->ni",r,self.timeline.gyro_rad_s[rows,j]);return r,direction,gyro

    def hinge(self,x,rows,parent,child,joint):
        _,dp,gp=self.corrected(x,rows,parent);_,dc,gc=self.corrected(x,rows,child);functional=self._evaluation_cache[0][2] if self._evaluation_cache is not None else _decode(x)[2];axis=functional[joint];relative=gc-gp;sigma=float(self.cfg["functional_gyro_sigma_rad_s"]);orth=float(self.cfg["functional_axis_orthogonality_sigma"]);return np.r_[np.cross(relative,axis).ravel()/sigma,(dc@axis)/orth]

    def pronation(self,x,rows,parent,child):
        _,_,gp=self.corrected(x,rows,parent);_,dc,gc=self.corrected(x,rows,child);relative=gc-gp;return (np.cross(relative,dc)/float(self.cfg["functional_gyro_sigma_rad_s"])).ravel()

    def neutral(self,x,rows,parent,child,joint,zero_name):
        _,dp,_=self.corrected(x,rows,parent);_,dc,_=self.corrected(x,rows,child);decoded=self._evaluation_cache[0] if self._evaluation_cache is not None else _decode(x);functional,zeros=decoded[2],decoded[3];axis=functional[joint];angle=np.arctan2(np.einsum("ni,i->n",np.cross(dp,dc),axis),np.einsum("ni,ni->n",dp,dc));return (angle-zeros[zero_name])/float(self.cfg["neutral_zero_sigma_rad"])

    def static_segment(self,x,rows,pose,segment,expected):
        latent=(self._evaluation_cache[0] if self._evaluation_cache is not None else _decode(x))[4];_,direction,_=self.corrected(x,rows,segment);reference=latent[pose][segment];protocol_sigma=math.radians(float(self.cfg["initial_protocol_prior_sigma_deg"] if pose=="initial_still_attempt2" else self.cfg["tpose_protocol_prior_sigma_deg"]));j=self.segment_node[segment];q2_sigma=np.sqrt(np.maximum(np.trace(self.timeline.covariance_rad2[rows,j],axis1=1,axis2=2)/3.,0.));effective_sigma=np.sqrt(self.static_sigma[(pose,segment)]**2+q2_sigma**2);observation=s2_log(reference,direction)/effective_sigma[:,None];protocol=s2_log(expected[segment],reference)/protocol_sigma;return np.r_[observation.ravel(),protocol]

    def blocks(self,x:np.ndarray)->dict[str,list[tuple[str,np.ndarray]]]:
        self.prepare_evaluation(x);out={action:[] for action in ACTIONS};ini=_phase(self.segmentation,"initial_still_attempt2","neutral_soft_pose");tp=_phase(self.segmentation,"t_pose","independent_t_pose_soft_pose")
        for segment in SEGMENTS:
            out["initial_still_attempt2"].append((f"neutral_latent_pose:{segment}",self.static_segment(x,ini,"initial_still_attempt2",segment,EXPECTED_INITIAL)));out["t_pose"].append((f"tpose_latent_pose:{segment}",self.static_segment(x,tp,"t_pose",segment,EXPECTED_TPOSE)))
        for joint,parent,child in (("elbow_L","upper_arm_L","forearm_L"),("elbow_R","upper_arm_R","forearm_R"),("hip_L","pelvis","thigh_L"),("hip_R","pelvis","thigh_R"),("knee_L","thigh_L","shank_L"),("knee_R","thigh_R","shank_R")):
            out["initial_still_attempt2"].append((f"neutral_zero_{joint}",self.neutral(x,ini,parent,child,joint,joint)))
        out["initial_still_attempt2"].append(("neutral_zero_trunk",self.neutral(x,ini,"pelvis","torso","trunk_turn","trunk")));out["t_pose"].append(("extended_elbow_L",self.neutral(x,tp,"upper_arm_L","forearm_L","elbow_L","elbow_L")));out["t_pose"].append(("extended_elbow_R",self.neutral(x,tp,"upper_arm_R","forearm_R","elbow_R","elbow_R")))
        for phase,sides in (("left_arm_raise_lower",("L",)),("right_arm_raise_lower",("R",)),("bilateral_raise_lower",("L","R"))):
            rows=_phase(self.segmentation,"arms",phase)
            for side in sides:
                out["arms"].append((f"{phase}_shoulder_{side}",self.hinge(x,rows,"torso",f"upper_arm_{side}",f"shoulder_{side}")));out["arms"].append((f"{phase}_elbow_{side}",self.hinge(x,rows,f"upper_arm_{side}",f"forearm_{side}",f"elbow_{side}")))
        returns=_phase(self.segmentation,"arms","neutral_returns");out["arms"].append(("neutral_return_elbow_L",self.neutral(x,returns,"upper_arm_L","forearm_L","elbow_L","elbow_L")));out["arms"].append(("neutral_return_elbow_R",self.neutral(x,returns,"upper_arm_R","forearm_R","elbow_R","elbow_R")))
        for action,side in (("left_elbow","L"),("right_elbow_attempt2","R")):
            curl=_phase(self.segmentation,action,"curl");pron=_phase(self.segmentation,action,"pronation_supination");ret=_phase(self.segmentation,action,"neutral_return");out[action].append(("elbow_flexion_axis_soft",self.hinge(x,curl,f"upper_arm_{side}",f"forearm_{side}",f"elbow_{side}")));out[action].append(("forearm_longitudinal_rotation_invariance",self.pronation(x,pron,f"upper_arm_{side}",f"forearm_{side}")));out[action].append(("neutral_return",self.neutral(x,ret,f"upper_arm_{side}",f"forearm_{side}",f"elbow_{side}",f"elbow_{side}")))
        for action,side in (("left_knee","L"),("right_knee","R")):
            active=_phase(self.segmentation,action,"high_knee_raise_lower");ret=_phase(self.segmentation,action,"neutral_return");out[action].append(("hip_motion_subspace",self.hinge(x,active,"pelvis",f"thigh_{side}",f"hip_{side}")));out[action].append(("neutral_return",self.neutral(x,ret,"pelvis",f"thigh_{side}",f"hip_{side}",f"hip_{side}")))
        for action,side in (("left_heel","L"),("right_heel","R")):
            active=_phase(self.segmentation,action,"heel_to_butt_flexion");ret=_phase(self.segmentation,action,"neutral_return");out[action].append(("knee_flexion_axis_soft",self.hinge(x,active,f"thigh_{side}",f"shank_{side}",f"knee_{side}")));out[action].append(("neutral_return",self.neutral(x,ret,f"thigh_{side}",f"shank_{side}",f"knee_{side}",f"knee_{side}")))
        sq=_phase(self.segmentation,"squats","descent_ascent")
        for side in ("L","R"):
            out["squats"].append((f"hip_chain_{side}",self.hinge(x,sq,"pelvis",f"thigh_{side}",f"hip_{side}")));out["squats"].append((f"knee_chain_{side}",self.hinge(x,sq,f"thigh_{side}",f"shank_{side}",f"knee_{side}")))
        _,_,gpl=self.corrected(x,sq,"pelvis");_,_,gtl=self.corrected(x,sq,"thigh_L");_,_,gtr=self.corrected(x,sq,"thigh_R");out["squats"].append(("soft_bilateral_coordination",(np.linalg.norm(gtl-gpl,axis=1)-np.linalg.norm(gtr-gpl,axis=1))/float(self.cfg["soft_bilateral_sigma_rad_s"])))
        axes,headings,functional,zeros,latent=self._evaluation_cache[0];turn=functional["trunk_turn"];flex=functional["trunk_flex"];normal=normalize(np.cross(turn,flex))
        for phase in ("left_turn","right_turn","forward_flexion_recovery"):
            rows=_phase(self.segmentation,"trunk",phase);_,_,gp=self.corrected(x,rows,"pelvis");_,_,gc=self.corrected(x,rows,"torso");out["trunk"].append((f"trunk_motion_subspace_{phase}",((gc-gp)@normal)/float(self.cfg["functional_gyro_sigma_rad_s"])))
        out["trunk"].append(("trunk_axis_orthogonality",np.asarray([turn@flex/float(self.cfg["functional_axis_orthogonality_prior_sigma"])])));ret=_phase(self.segmentation,"trunk","neutral_returns");out["trunk"].append(("neutral_return",self.neutral(x,ret,"pelvis","torso","trunk_turn","trunk")))
        return out

    def residual(self,x:np.ndarray)->np.ndarray:
        blocks=self.blocks(x);return np.concatenate([values for action in ACTIONS for _,values in blocks[action]])

    def accounting(self,x:np.ndarray)->tuple[dict,dict[str,slice]]:
        blocks=self.blocks(x);cursor=0;actions={};slices={}
        for action in ACTIONS:
            start=cursor;rows=[]
            for name,value in blocks[action]:rows.append({"residual_block":name,"scalar_rows":int(len(value)),"row_start":cursor,"row_stop":cursor+len(value)});cursor+=len(value)
            slices[action]=slice(start,cursor);actions[action]={"residual_blocks":rows,"scalar_rows":cursor-start}
        return {"schema":"biospur-action-residual-accounting-v1","total_scalar_rows":cursor,"actions":actions},slices

    def _columns(self,*names):
        columns=[]
        for entry in LAYOUT["entries"]:
            if any(entry["name"]==name or entry["name"].startswith(name) for name in names):columns.extend(range(entry["start"],entry["stop"]))
        return sorted(set(columns))

    def block_dependency_columns(self,action:str,name:str)->list[int]:
        segment=lambda s:self._columns(f"segment_axis:{s}",f"relative_heading:{s}")
        if name.startswith(("neutral_latent_pose:","tpose_latent_pose:")):
            s=name.split(":",1)[1];pose="initial_still_attempt2" if name.startswith("neutral") else "t_pose";return sorted(set(segment(s)+self._columns(f"latent_pose:{pose}:{s}")))
        if action in ("left_elbow","right_elbow_attempt2"):
            side="L" if action=="left_elbow" else "R";columns=segment(f"upper_arm_{side}")+segment(f"forearm_{side}")
            if "forearm_longitudinal" not in name:columns+=self._columns(f"functional_axis:elbow_{side}",f"neutral_zero:elbow_{side}")
            return sorted(set(columns))
        if action in ("left_knee","right_knee"):
            side="L" if action=="left_knee" else "R";return sorted(set(segment("pelvis")+segment(f"thigh_{side}")+self._columns(f"functional_axis:hip_{side}",f"neutral_zero:hip_{side}")))
        if action in ("left_heel","right_heel"):
            side="L" if action=="left_heel" else "R";return sorted(set(segment(f"thigh_{side}")+segment(f"shank_{side}")+self._columns(f"functional_axis:knee_{side}",f"neutral_zero:knee_{side}")))
        if action=="trunk":return sorted(set(segment("pelvis")+segment("torso")+self._columns("functional_axis:trunk_turn","functional_axis:trunk_flex","neutral_zero:trunk")))
        if action=="squats":
            if name=="soft_bilateral_coordination":return sorted(set(segment("pelvis")+segment("thigh_L")+segment("thigh_R")))
            side="L" if name.endswith("_L") else "R";joint="hip" if name.startswith("hip") else "knee";parent="pelvis" if joint=="hip" else f"thigh_{side}";child=f"thigh_{side}" if joint=="hip" else f"shank_{side}";return sorted(set(segment(parent)+segment(child)+self._columns(f"functional_axis:{joint}_{side}")))
        joint_specs={"elbow_L":("upper_arm_L","forearm_L"),"elbow_R":("upper_arm_R","forearm_R"),"hip_L":("pelvis","thigh_L"),"hip_R":("pelvis","thigh_R"),"knee_L":("thigh_L","shank_L"),"knee_R":("thigh_R","shank_R"),"trunk_turn":("pelvis","torso")}
        for joint,(parent,child) in joint_specs.items():
            if joint in name or (joint=="trunk_turn" and "trunk" in name):return sorted(set(segment(parent)+segment(child)+self._columns(f"functional_axis:{joint}",f"neutral_zero:{joint.removesuffix('_turn')}")))
        if "shoulder_L" in name:return sorted(set(segment("torso")+segment("upper_arm_L")+self._columns("functional_axis:shoulder_L")))
        if "shoulder_R" in name:return sorted(set(segment("torso")+segment("upper_arm_R")+self._columns("functional_axis:shoulder_R")))
        raise KeyError((action,name))

    def structural_sparsity(self,x:np.ndarray):
        blocks=self.blocks(x);rows=sum(len(value) for action in ACTIONS for _,value in blocks[action]);mask=lil_matrix((rows,len(x)),dtype=np.int8);cursor=0
        for action in ACTIONS:
            for name,value in blocks[action]:
                columns=self.block_dependency_columns(action,name);mask[cursor:cursor+len(value),columns]=1;cursor+=len(value)
        return mask.tocsr()


def initial_parameters(objective:Objective)->np.ndarray:
    x=np.zeros(96);cursor=0;rows=_phase(objective.segmentation,"initial_still_attempt2","neutral_soft_pose")
    for segment in SEGMENTS:
        j=objective.segment_node[segment];mean=Rotation.from_matrix(objective.timeline.rotation[rows,j]).mean().as_matrix();local=normalize(mean.T@EXPECTED_INITIAL[segment]);x[cursor:cursor+2]=axis_to_angles(local);cursor+=2
    cursor+=9
    for joint,parent,child in (("shoulder_L","torso","upper_arm_L"),("shoulder_R","torso","upper_arm_R"),("elbow_L","upper_arm_L","forearm_L"),("elbow_R","upper_arm_R","forearm_R"),("hip_L","pelvis","thigh_L"),("hip_R","pelvis","thigh_R"),("knee_L","thigh_L","shank_L"),("knee_R","thigh_R","shank_R"),("trunk_turn","pelvis","torso"),("trunk_flex","pelvis","torso")):
        candidates=[]
        for action in ACTIONS:
            for phase in objective.segmentation["actions"][action]:
                if parent in phase["relevant_segments"] and child in phase["relevant_segments"]:candidates.extend(phase["row_indices"])
        selected=np.unique(candidates);_,_,gp=objective.corrected(x,selected,parent);_,_,gc=objective.corrected(x,selected,child);relative=gc-gp
        if joint=="trunk_flex":
            _,_,vh=np.linalg.svd(relative-np.median(relative,axis=0),full_matrices=False);axis=vh[1]
        else:
            _,_,vh=np.linalg.svd(relative-np.median(relative,axis=0),full_matrices=False);axis=vh[0]
        x[cursor:cursor+2]=axis_to_angles(axis);cursor+=2
    cursor+=7
    for _pose in ("initial_still_attempt2","t_pose"):
        for _segment in SEGMENTS:x[cursor:cursor+2]=0.;cursor+=2
    assert cursor==96;return x


def finite_difference_jacobian(fun,x:np.ndarray,steps:np.ndarray)->np.ndarray:
    base=fun(x);jac=np.empty((len(base),len(x)))
    for col,step in enumerate(steps):
        plus=x.copy();minus=x.copy();plus[col]+=step;minus[col]-=step;jac[:,col]=(fun(plus)-fun(minus))/(2.*step)
    return jac


def parameter_steps(cfg:Mapping[str,float])->np.ndarray:
    steps=np.empty(96)
    for entry in LAYOUT["entries"]:
        key="axis_step_rad" if entry["block"] in ("sensor_axis_quotient","latent_pose_reference_tangent_chart") else "heading_step_rad" if entry["block"]=="relative_heading" else "functional_axis_step_rad" if entry["block"]=="functional_axis_or_subspace" else "joint_zero_step_rad";steps[entry["start"]:entry["stop"]]=float(cfg[key])
    return steps


def bounds()->tuple[np.ndarray,np.ndarray]:
    lower=np.full(96,-math.pi);upper=np.full(96,math.pi)
    for entry in LAYOUT["entries"]:
        if entry["stop"]-entry["start"]==2 and entry["block"]!="latent_pose_reference_tangent_chart":lower[entry["start"]+1]=-math.pi/2;upper[entry["start"]+1]=math.pi/2
        if entry["block"]=="latent_pose_reference_tangent_chart":lower[entry["start"]:entry["stop"]]=-math.pi/2;upper[entry["start"]:entry["stop"]]=math.pi/2
    return lower,upper


def fit_one(objective:Objective,start:np.ndarray,solver_cfg:Mapping[str,Any],max_nfev:int):
    options={"atol":float(solver_cfg["lsmr_atol"]),"btol":float(solver_cfg["lsmr_btol"]),"conlim":float(solver_cfg["lsmr_conlim"]),"maxiter":int(solver_cfg["lsmr_maxiter"])};return least_squares(objective.residual,start,jac="3-point",jac_sparsity=objective.structural_sparsity(start),method="trf",tr_solver="lsmr",tr_options=options,loss=str(objective.cfg["loss"]),f_scale=float(objective.cfg["f_scale"]),bounds=bounds(),xtol=float(solver_cfg["xtol"]),ftol=float(solver_cfg["ftol"]),gtol=float(solver_cfg["gtol"]),max_nfev=int(max_nfev),workers=int(solver_cfg["finite_difference_workers"]))


def canonical_calibration(x:np.ndarray,metadata:Mapping[str,Any])->dict:
    axes,headings,functional,zeros,latent=_decode(x);published_entries=[entry for entry in LAYOUT["entries"] if entry["block"]!="latent_pose_reference_tangent_chart"];return {"schema":"biospur-frozen-imu-multi-action-engineering-preview-v1","product":"IMU_MULTI_ACTION_ENGINEERING_PREVIEW_V1","published_parameter_layout":{"dimension":56,"entries":published_entries,"profiled_calibration_nuisance_dimension":40,"profiled_nuisance":"SEPARATE_NEUTRAL_AND_TPOSE_LATENT_REFERENCES_IN_REFERENCE_CENTERED_S2_TANGENT_CHARTS"},"published_parameter_vector":x[:56].tolist(),"segments":{segment:{"board_frame_longitudinal_axis":axes[segment].tolist(),"relative_heading_rad":headings[segment]} for segment in SEGMENTS},"functional_axes_display_frame":{name:value.tolist() for name,value in functional.items()},"joint_neutral_zero_rad":zeros,"profiled_latent_pose_reference_audit":{pose:{segment:value.tolist() for segment,value in values.items()} for pose,values in latent.items()},"global_yaw_gauge":"PELVIS_RELATIVE_HEADING_FIXED_ZERO","axial_segment_twist":"NOT_OUTPUT","clinical_joint_angles":False,"generic_dimensions_subject_specific":False,**dict(metadata)}
