"""Unified synthetic S2 objective and observability/profile analysis."""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Mapping

import numpy as np
from scipy.linalg import null_space
from scipy.optimize import least_squares
from scipy.spatial.transform import Rotation

from biospur_fusion.imu_multi_action_v1.core import (
    SEGMENTS, axis_angle_rad, deterministic_information_subset,
    fit_functional_axis, interpolate_rotations_so3, normalize,
    olsson_weighted_residual, tangent_update,
)
from .human_synthetic import HumanSyntheticDataset, SEGMENT_TO_NODE


FUNCTIONAL = {
    "elbow_L": ("upper_arm_L", "forearm_L", "LEFT_ELBOW_CURL"),
    "elbow_R": ("upper_arm_R", "forearm_R", "RIGHT_ELBOW_CURL"),
    "knee_L": ("thigh_L", "shank_L", "LEFT_REAR_HEEL_TO_BUTTOCK_KNEE_FLEXION"),
    "knee_R": ("thigh_R", "shank_R", "RIGHT_REAR_HEEL_TO_BUTTOCK_KNEE_FLEXION"),
    "hip_L": ("pelvis", "thigh_L", "LEFT_FRONT_HIGH_KNEE_RAISE_RELAXED_SHANK"),
    "hip_R": ("pelvis", "thigh_R", "RIGHT_FRONT_HIGH_KNEE_RAISE_RELAXED_SHANK"),
}


@dataclass(frozen=True)
class Initializers:
    a_B: Mapping[str, np.ndarray]
    transverse_B: Mapping[str, np.ndarray]
    functional_parent_B: Mapping[str, np.ndarray]
    functional_child_B: Mapping[str, np.ndarray]
    heading_rad: Mapping[str, float]
    functional_reports: Mapping[str, Any]
    gyro_bias_B_rad_s: Mapping[str, np.ndarray]
    accel_bias_B_mps2: Mapping[str, np.ndarray]


def _segment_node(dataset: HumanSyntheticDataset, segment: str) -> str:
    return next(node for node,value in dataset.node_to_segment.items() if value==segment)


def _phase_map(segmentation: Mapping[str, Any]) -> dict[str, tuple[int,int]]:
    return {row["semantic_phase"]:(int(row["start_ns"]),int(row["stop_ns"]))
            for row in segmentation["segments"]}


def _indices(stream, window: tuple[int,int]) -> np.ndarray:
    return np.flatnonzero((stream.time_ns>=window[0])&(stream.time_ns<=window[1]))


def _principal(rows: np.ndarray) -> np.ndarray:
    rows=np.asarray(rows,float)-np.median(rows,axis=0)
    _,vectors=np.linalg.eigh(rows.T@rows/max(1,len(rows)))
    return normalize(vectors[:,-1])


def _project_orthogonal(vector: np.ndarray, axis: np.ndarray) -> np.ndarray:
    return normalize(vector-axis*float(vector@axis))


def _yaw_to_align(source: np.ndarray, target: np.ndarray) -> float:
    source=np.asarray(source,float);target=np.asarray(target,float)
    if np.linalg.norm(source[:2])<1e-8 or np.linalg.norm(target[:2])<1e-8:return 0.0
    return math.atan2(target[1],target[0])-math.atan2(source[1],source[0])


def build_initializers(dataset: HumanSyntheticDataset,
                       segmentation: Mapping[str, Any],
                       gates: Mapping[str, Any]) -> Initializers:
    phases=_phase_map(segmentation)
    initial=phases["NATURAL_STANDING_STILL"]
    gyro_bias={};accel_bias={};a={}
    z=np.array([0.,0.,1.])
    for segment in SEGMENTS:
        stream=dataset.nodes[_segment_node(dataset,segment)];idx=_indices(stream,initial)
        gyro_bias[segment]=np.median(stream.gyro_B_rad_s[idx],axis=0)
        expected=np.einsum("nji,j->ni",stream.R_N_i_from_B_i[idx],9.80665*z)
        accel_bias[segment]=np.median(stream.accel_B_mps2[idx]-expected,axis=0)
        target=z if segment in ("pelvis","torso") else -z
        candidates=np.einsum("nji,j->ni",stream.R_N_i_from_B_i[idx],target)
        a[segment]=normalize(np.median(candidates,axis=0))
    fp={};fc={};reports={}
    noise={"gyro_sigma_rad_s":float(gates["noise_floors"]["gyro_rad_s"]),
           "accel_sigma_mps2":float(gates["noise_floors"]["accel_mps2"])}
    sampling={"minimum_mandatory_informative_samples":int(gates["sampling"]["minimum_informative"]),
              "max_samples_per_action_factor":int(gates["sampling"]["maximum_per_phase_factor"])}
    for name,(parent,child,phase) in FUNCTIONAL.items():
        hp,hc,report=fit_functional_axis(
            dataset.nodes[_segment_node(dataset,parent)],
            dataset.nodes[_segment_node(dataset,child)],phases[phase],noise,sampling)
        fp[name]=hp;fc[name]=hc;reports[name]=report
    # Align unsigned bilateral pelvis hip axes, then obtain torso bend axis independently.
    pelvis_l=fp["hip_L"];pelvis_r=fp["hip_R"]
    if pelvis_l@pelvis_r<0:pelvis_r=-pelvis_r
    b_pelvis=normalize(pelvis_l+pelvis_r)
    bend_stream=dataset.nodes[_segment_node(dataset,"torso")]
    bend_idx=_indices(bend_stream,phases["TRUNK_FORWARD_BEND_AND_RECOVER"])
    b_torso=_principal(bend_stream.gyro_B_rad_s[bend_idx]-gyro_bias["torso"])
    b_pelvis=_project_orthogonal(b_pelvis,a["pelvis"])
    b_torso=_project_orthogonal(b_torso,a["torso"])
    transverse={"pelvis":b_pelvis,"torso":b_torso}

    heading={"pelvis":0.0}
    def representative(segment: str, phase: str) -> np.ndarray:
        stream=dataset.nodes[_segment_node(dataset,segment)];idx=_indices(stream,phases[phase]);return stream.R_N_i_from_B_i[idx[len(idx)//2]]
    # Torso bend axis to pelvis independently estimated high-knee axis.
    target=representative("pelvis","TRUNK_FORWARD_BEND_AND_RECOVER")@b_pelvis
    source=representative("torso","TRUNK_FORWARD_BEND_AND_RECOVER")@b_torso
    heading["torso"]=_yaw_to_align(source,target)
    # Thighs from hip axes.
    for side in ("L","R"):
        phase=f"{('LEFT' if side=='L' else 'RIGHT')}_FRONT_HIGH_KNEE_RAISE_RELAXED_SHANK"
        target=Rotation.from_euler("z",heading["pelvis"]).apply(representative("pelvis",phase)@fp[f"hip_{side}"])
        source=representative(f"thigh_{side}",phase)@fc[f"hip_{side}"]
        heading[f"thigh_{side}"]=_yaw_to_align(source,target)
        heel=f"{('LEFT' if side=='L' else 'RIGHT')}_REAR_HEEL_TO_BUTTOCK_KNEE_FLEXION"
        target=Rotation.from_euler("z",heading[f"thigh_{side}"]).apply(representative(f"thigh_{side}",heel)@fp[f"knee_{side}"])
        source=representative(f"shank_{side}",heel)@fc[f"knee_{side}"]
        heading[f"shank_{side}"]=_yaw_to_align(source,target)
    # Upper arms from T-pose arm line, then forearms from elbow axes.
    for side,sign in (("L",1.0),("R",-1.0)):
        phase="STATIC_BILATERAL_ARM_LINE"
        target=sign*Rotation.from_euler("z",heading["torso"]).apply(representative("torso",phase)@b_torso)
        source=representative(f"upper_arm_{side}",phase)@a[f"upper_arm_{side}"]
        heading[f"upper_arm_{side}"]=_yaw_to_align(source,target)
        elbow=f"{('LEFT' if side=='L' else 'RIGHT')}_ELBOW_CURL"
        target=Rotation.from_euler("z",heading[f"upper_arm_{side}"]).apply(representative(f"upper_arm_{side}",elbow)@fp[f"elbow_{side}"])
        source=representative(f"forearm_{side}",elbow)@fc[f"elbow_{side}"]
        heading[f"forearm_{side}"]=_yaw_to_align(source,target)
    # Jointly refine all relative headings from observation-derived axes.  The
    # discrete pi branches are enumerated; no truth orientation is consulted.
    order=tuple(segment for segment in SEGMENTS if segment!="pelvis")
    rough=np.array([heading[s] for s in order])
    def heading_residual(values:np.ndarray)->np.ndarray:
        h={"pelvis":0.0,**{s:float(values[k]) for k,s in enumerate(order)}};rows=[]
        def world(segment:str,phase:str,axis:np.ndarray)->np.ndarray:
            stream=dataset.nodes[_segment_node(dataset,segment)];idx=_indices(stream,phases[phase]);pick=np.linspace(0,len(idx)-1,24).round().astype(int);R=stream.R_N_i_from_B_i[idx[pick]];yaw=Rotation.from_euler("z",h[segment]).as_matrix();return np.einsum("ij,njk,k->ni",yaw,R,axis)
        bt=world("torso","STATIC_BILATERAL_ARM_LINE",b_torso);bp=world("pelvis","STATIC_BILATERAL_ARM_LINE",b_pelvis);rows.append((bt-bp).ravel())
        for side,sign in (("L",1.0),("R",-1.0)):
            for segment in (f"upper_arm_{side}",f"forearm_{side}"):rows.append((world(segment,"STATIC_BILATERAL_ARM_LINE",a[segment])-sign*bt).ravel())
        for name,(parent,child,phase) in FUNCTIONAL.items():
            gp=world(parent,phase,fp[name]);gc=world(child,phase,fc[name]);rows.append(np.cross(gp,gc).ravel())
        for side in ("L","R"):
            phase=f"{('LEFT' if side=='L' else 'RIGHT')}_FRONT_HIGH_KNEE_RAISE_RELAXED_SHANK";gp=world("pelvis",phase,fp[f"hip_{side}"]);bl=world("pelvis",phase,b_pelvis);rows.append(np.cross(gp,bl).ravel())
        return np.concatenate(rows)
    starts=[rough.copy(),rough.copy(),rough.copy(),rough.copy(),np.zeros_like(rough)]
    for segment in ("upper_arm_L","upper_arm_R","forearm_L","forearm_R"):starts[1][order.index(segment)]+=math.pi
    for segment in ("torso","upper_arm_L","upper_arm_R","forearm_L","forearm_R"):starts[2][order.index(segment)]+=math.pi
    for segment in ("thigh_L","thigh_R","shank_L","shank_R"):starts[3][order.index(segment)]+=math.pi
    heading_fits=[least_squares(heading_residual,start,method="trf",loss="huber",f_scale=1.5,max_nfev=120,ftol=1e-11,xtol=1e-11,gtol=1e-11) for start in starts]
    best=min(heading_fits,key=lambda fit:(float(fit.cost),tuple(fit.x)))
    for k,segment in enumerate(order):heading[segment]=float((best.x[k]+math.pi)%(2*math.pi)-math.pi)
    reports["relative_heading_initializer"]={"method":"JOINT_AXIS_TPOSE_HIGH_KNEE_SIGN_BRANCH_ENUMERATION","costs":[float(f.cost) for f in heading_fits],"selected_cost":float(best.cost),"truth_read":False}
    return Initializers(a,transverse,fp,fc,heading,reports,gyro_bias,accel_bias)


class S2UnifiedProblem:
    """One shared static calibration across every semantic phase."""
    def __init__(self,dataset:HumanSyntheticDataset,segmentation:Mapping[str,Any],
                 gates:Mapping[str,Any],template:Mapping[str,Any]):
        self.dataset=dataset;self.segmentation=segmentation;self.gates=gates;self.template=template
        self.phases=_phase_map(segmentation);self.init=build_initializers(dataset,segmentation,gates)
        self._build_index();self._prepare_samples()

    def _build_index(self)->None:
        self.slices={};names=[];cursor=0
        for segment in ("pelvis","torso"):
            self.slices[f"frame:{segment}"]=slice(cursor,cursor+3);names += [f"frame:{segment}:{x}" for x in "xyz"];cursor+=3
        for segment in SEGMENTS[2:]:
            self.slices[f"axis:{segment}"]=slice(cursor,cursor+2);names += [f"axis:{segment}:t0",f"axis:{segment}:t1"];cursor+=2
        for joint in FUNCTIONAL:
            for role in ("parent","child"):
                self.slices[f"functional:{joint}:{role}"]=slice(cursor,cursor+2);names += [f"functional:{joint}:{role}:t0",f"functional:{joint}:{role}:t1"];cursor+=2
        for segment in SEGMENTS:
            if segment=="pelvis":continue
            self.slices[f"heading:{segment}"]=slice(cursor,cursor+1);names.append(f"heading:{segment}");cursor+=1
        self.parameter_names=names;self.parameter_count=cursor

    def unpack(self,value:np.ndarray):
        value=np.asarray(value,float);a={};b={}
        for segment in ("pelvis","torso"):
            update=Rotation.from_rotvec(value[self.slices[f"frame:{segment}"]]).as_matrix()
            a[segment]=update@self.init.a_B[segment];b[segment]=update@self.init.transverse_B[segment]
        for segment in SEGMENTS[2:]:a[segment]=tangent_update(self.init.a_B[segment],value[self.slices[f"axis:{segment}"]])
        fp={};fc={}
        for joint in FUNCTIONAL:
            fp[joint]=tangent_update(self.init.functional_parent_B[joint],value[self.slices[f"functional:{joint}:parent"]])
            fc[joint]=tangent_update(self.init.functional_child_B[joint],value[self.slices[f"functional:{joint}:child"]])
        heading={"pelvis":0.0}
        for segment in SEGMENTS:
            if segment!="pelvis":heading[segment]=self.init.heading_rad[segment]+float(value[self.slices[f"heading:{segment}"]][0])
        return a,b,fp,fc,heading

    def _balanced(self,phase:str,segments:tuple[str,...],maximum:int|None=None)->np.ndarray:
        reference=self.dataset.nodes[_segment_node(self.dataset,segments[0])];idx=_indices(reference,self.phases[phase])
        score=np.zeros(len(idx))
        for segment in segments:
            stream=self.dataset.nodes[_segment_node(self.dataset,segment)];score+=np.linalg.norm(stream.gyro_B_rad_s[idx]-self.init.gyro_bias_B_rad_s[segment],axis=1)
        target=int(self.gates["sampling"]["target_per_phase_factor"])
        cap=int(maximum if maximum is not None else min(target,int(self.gates["sampling"]["maximum_per_phase_factor"])))
        return idx[deterministic_information_subset(score,int(self.gates["sampling"]["minimum_informative"]),cap)]

    def _prepare_samples(self)->None:
        self.samples={}
        self.samples["initial"]={s:self._balanced("NATURAL_STANDING_STILL",(s,),125) for s in SEGMENTS}
        self.samples["tpose"]={s:self._balanced("STATIC_BILATERAL_ARM_LINE",(s,),125) for s in SEGMENTS}
        for phase in ("LEFT_ARM_RAISE_LOWER", "RIGHT_ARM_RAISE_LOWER",
                      "BILATERAL_ARM_RAISE_LOWER"):
            self.samples[f"arms:{phase}"]=self._balanced(
                phase, ("torso", "upper_arm_L", "upper_arm_R", "forearm_L", "forearm_R")
            )
        for joint,(parent,child,phase) in FUNCTIONAL.items():self.samples[f"functional:{joint}"]=self._balanced(phase,(parent,child))
        for side in ("L","R"):
            self.samples[f"pronation:{side}"]=self._balanced(f"{('LEFT' if side=='L' else 'RIGHT')}_FOREARM_PRONATION_SUPINATION",(f"upper_arm_{side}",f"forearm_{side}"))
        for phase in ("TRUNK_LEFT_ROTATION","TRUNK_RIGHT_ROTATION","TRUNK_FORWARD_BEND_AND_RECOVER"):
            self.samples[f"trunk:{phase}"]=self._balanced(phase,("pelvis","torso"))
        self.samples["squat"]=self._balanced("BILATERAL_SQUAT",("pelvis","thigh_L","thigh_R","shank_L","shank_R"))

    def R_W_from_B(self,segment:str,indices:np.ndarray,heading:Mapping[str,float])->np.ndarray:
        stream=self.dataset.nodes[_segment_node(self.dataset,segment)]
        yaw=Rotation.from_euler("z",heading[segment]).as_matrix()
        return np.einsum("ij,njk->nik",yaw,stream.R_N_i_from_B_i[indices])

    def _corrected(self,segment:str,indices:np.ndarray)->tuple[np.ndarray,np.ndarray,np.ndarray]:
        stream=self.dataset.nodes[_segment_node(self.dataset,segment)]
        gyro=stream.gyro_B_rad_s-self.init.gyro_bias_B_rad_s[segment]
        accel=stream.accel_B_mps2-self.init.accel_bias_B_mps2[segment]
        dt=float(np.median(np.diff(stream.time_ns)))/1e9
        alpha=np.gradient(gyro,dt,axis=0,edge_order=2)
        return gyro[indices],accel[indices],alpha[indices]

    def _lever(self,segment:str,which:str,a:Mapping[str,np.ndarray],b:Mapping[str,np.ndarray],side:str|None=None)->np.ndarray:
        d=self.template["dimensions"]
        length={"torso":d["C7Proxy_to_PelvisProxy_m"],"upper_arm_L":d["rendering_upper_arm_length_L_m"],"upper_arm_R":d["rendering_upper_arm_length_R_m"],"forearm_L":d["rendering_forearm_length_L_m"],"forearm_R":d["rendering_forearm_length_R_m"],"thigh_L":d["rendering_thigh_length_L_m"],"thigh_R":d["rendering_thigh_length_R_m"],"shank_L":d["rendering_shank_length_L_m"],"shank_R":d["rendering_shank_length_R_m"],"pelvis":0.10}
        if which=="proximal":return -0.5*float(length[segment])*a[segment]
        if which=="distal":return 0.5*float(length[segment])*a[segment]
        if which=="shoulder":return 0.5*float(length["torso"])*a["torso"]+(1 if side=="L" else -1)*0.5*float(d["graphical_shoulder_width_m"])*b["torso"]
        if which=="hip":return -0.5*float(length["pelvis"])*a["pelvis"]+(1 if side=="L" else -1)*0.5*float(d["graphical_hip_width_m"])*b["pelvis"]
        raise KeyError(which)

    def _shared_point(self,parent:str,child:str,indices:np.ndarray,Rp:np.ndarray,Rc:np.ndarray,rp:np.ndarray,rc:np.ndarray)->np.ndarray:
        wp,ap,alphap=self._corrected(parent,indices);wc,ac,alphac=self._corrected(child,indices)
        termp=np.cross(alphap,np.broadcast_to(rp,wp.shape))+np.cross(wp,np.cross(wp,np.broadcast_to(rp,wp.shape)))
        termc=np.cross(alphac,np.broadcast_to(rc,wc.shape))+np.cross(wc,np.cross(wc,np.broadcast_to(rc,wc.shape)))
        predp=np.einsum("nij,nj->ni",Rp,ap+termp);predc=np.einsum("nij,nj->ni",Rc,ac+termc)
        return (predp-predc)/float(self.gates["noise_floors"]["shared_point_accel_mps2"])

    def residual_blocks(self,value:np.ndarray,include: set[str]|None=None)->list[tuple[str,str,np.ndarray]]:
        a,b,fp,fc,heading=self.unpack(value);blocks=[]
        def add(action,factor,rows):
            key=f"{action}|{factor}"
            if include is None or key in include:blocks.append((action,factor,np.ravel(np.asarray(rows,float))))
        sigma=float(self.gates["noise_floors"]["orientation_rad"]);soft=math.radians(6.0)
        expected={s:(np.array([0.,0.,1.]) if s in ("pelvis","torso") else np.array([0.,0.,-1.])) for s in SEGMENTS}
        for segment,idx in self.samples["initial"].items():
            R=self.R_W_from_B(segment,idx,heading);pred=np.einsum("nij,j->ni",R,a[segment]);add("initial_still_attempt2",f"natural_direction:{segment}",(pred-expected[segment])/soft)
        # T-pose is distinct: upright torso/pelvis and a bilateral arm-line proxy.
        idx=self.samples["tpose"]["torso"];Rt=self.R_W_from_B("torso",idx,heading);bt=np.einsum("nij,j->ni",Rt,b["torso"]);at=np.einsum("nij,j->ni",Rt,a["torso"]);add("t_pose","torso_upright",(at-np.array([0.,0.,1.]))/soft)
        Rp=self.R_W_from_B("pelvis",idx,heading);bp=np.einsum("nij,j->ni",Rp,b["pelvis"]);add("t_pose","torso_pelvis_transverse_proxy",(bt-bp)/soft)
        for side,sign in (("L",1.0),("R",-1.0)):
            for segment in (f"upper_arm_{side}",f"forearm_{side}"):
                R=self.R_W_from_B(segment,idx,heading);axis=np.einsum("nij,j->ni",R,a[segment]);add("t_pose",f"bilateral_arm_line:{segment}",(axis-sign*bt)/soft)
        # Local Olsson and global functional-axis alignment, action balanced.
        for joint,(parent,child,phase) in FUNCTIONAL.items():
            idx=self.samples[f"functional:{joint}"];wp,ap,_=self._corrected(parent,idx);wc,ac,_=self._corrected(child,idx)
            ol=olsson_weighted_residual(fp[joint],fc[joint],wp,wc,ap,ac,float(self.gates["noise_floors"]["gyro_rad_s"]),float(self.gates["noise_floors"]["accel_mps2"]))
            add(phase,f"olsson_gyro:{joint}",ol[0::2]);add(phase,f"olsson_acceleration:{joint}",ol[1::2])
            Rparent=self.R_W_from_B(parent,idx,heading);Rchild=self.R_W_from_B(child,idx,heading);gp=np.einsum("nij,j->ni",Rparent,fp[joint]);gc=np.einsum("nij,j->ni",Rchild,fc[joint]);add(phase,f"functional_axis_alignment:{joint}",np.cross(gp,gc)/soft)
        # Bilateral high-knee axes independently establish pelvis lateral/forward frame.
        for side in ("L","R"):
            phase=f"{('LEFT' if side=='L' else 'RIGHT')}_FRONT_HIGH_KNEE_RAISE_RELAXED_SHANK";idx=self.samples[f"functional:hip_{side}"];Rp=self.R_W_from_B("pelvis",idx,heading);bp=np.einsum("nij,j->ni",Rp,b["pelvis"]);gh=np.einsum("nij,j->ni",Rp,fp[f"hip_{side}"]);add(phase,f"independent_pelvis_functional_lateral:hip_{side}",np.cross(gh,bp)/soft)
        # Pronation is separate from curl and softly informs the forearm long axis.
        for side in ("L","R"):
            phase=f"{('LEFT' if side=='L' else 'RIGHT')}_FOREARM_PRONATION_SUPINATION";idx=self.samples[f"pronation:{side}"];Ru=self.R_W_from_B(f"upper_arm_{side}",idx,heading);Rf=self.R_W_from_B(f"forearm_{side}",idx,heading);wu,_,_=self._corrected(f"upper_arm_{side}",idx);wf,_,_=self._corrected(f"forearm_{side}",idx);delta=np.einsum("nij,nj->ni",Rf,wf)-np.einsum("nij,nj->ni",Ru,wu);axis=np.einsum("nij,j->ni",Rf,a[f"forearm_{side}"]);perp=delta-axis*np.sum(delta*axis,axis=1)[:,None];add(phase,f"pronation_soft_cone:forearm_{side}",perp/float(self.gates["noise_floors"]["functional_off_axis_rad_s"]))
        # The three arm phases enter separately.  Shoulder motion remains full
        # 3-DOF; the only physical link is a finite-covariance shared graphical
        # shoulder point.  No sagittal/frontal motion plane is imposed.
        for phase in ("LEFT_ARM_RAISE_LOWER", "RIGHT_ARM_RAISE_LOWER",
                      "BILATERAL_ARM_RAISE_LOWER"):
            idx=self.samples[f"arms:{phase}"]
            Rt=self.R_W_from_B("torso",idx,heading)
            for side in ("L","R"):
                upper=f"upper_arm_{side}"
                Ru=self.R_W_from_B(upper,idx,heading)
                add(phase,f"shared_point_acceleration:shoulder_proxy_{side}",
                    self._shared_point(
                        "torso",upper,idx,Rt,Ru,
                        self._lever("torso","shoulder",a,b,side),
                        self._lever(upper,"proximal",a,b),
                    ))
        # Time-resolved trunk factors, not endpoint locks.
        for phase in ("TRUNK_LEFT_ROTATION","TRUNK_RIGHT_ROTATION","TRUNK_FORWARD_BEND_AND_RECOVER"):
            idx=self.samples[f"trunk:{phase}"];Rp=self.R_W_from_B("pelvis",idx,heading);Rt=self.R_W_from_B("torso",idx,heading);wp,_,_=self._corrected("pelvis",idx);wt,_,_=self._corrected("torso",idx);delta=np.einsum("nij,nj->ni",Rt,wt)-np.einsum("nij,nj->ni",Rp,wp)
            if "FORWARD" in phase:axis=np.einsum("nij,j->ni",Rp,b["pelvis"]);factor="trunk_forward_bend_functional_lateral"
            else:
                apred=np.einsum("nij,j->ni",Rt,a["torso"]);ppred=np.einsum("nij,j->ni",Rp,a["pelvis"]);axis=np.asarray([normalize(x+y) for x,y in zip(apred,ppred)]);factor="trunk_turn_functional_superior"
            perp=delta-axis*np.sum(delta*axis,axis=1)[:,None];add(phase,factor,perp/float(self.gates["noise_floors"]["functional_off_axis_rad_s"]))
            # Effective lumbar proxy shared-point acceleration.
            add(phase,"shared_point_acceleration:pelvis_torso",self._shared_point("pelvis","torso",idx,Rp,Rt,self._lever("pelvis","proximal",a,b),self._lever("torso","proximal",a,b)))
        # Squat bilateral functional axis alignment; no contact/root observation.
        idx=self.samples["squat"]
        for joint,parent,child in (("hip_L","pelvis","thigh_L"),("hip_R","pelvis","thigh_R"),("knee_L","thigh_L","shank_L"),("knee_R","thigh_R","shank_R")):
            Rparent=self.R_W_from_B(parent,idx,heading);Rchild=self.R_W_from_B(child,idx,heading);gp=np.einsum("nij,j->ni",Rparent,fp[joint]);gc=np.einsum("nij,j->ni",Rchild,fc[joint]);add("squats",f"bilateral_chain:{joint}",np.cross(gp,gc)/soft)
        # Time-resolved shared-point candidates for all graph connections.
        connection_specs=[]
        for side in ("L","R"):
            connection_specs += [
                (f"{('LEFT' if side=='L' else 'RIGHT')}_ELBOW_CURL",f"upper_arm_{side}",f"forearm_{side}",self._lever(f"upper_arm_{side}","distal",a,b),self._lever(f"forearm_{side}","proximal",a,b),f"elbow_{side}"),
                (f"{('LEFT' if side=='L' else 'RIGHT')}_FRONT_HIGH_KNEE_RAISE_RELAXED_SHANK","pelvis",f"thigh_{side}",self._lever("pelvis","hip",a,b,side),self._lever(f"thigh_{side}","proximal",a,b),f"hip_{side}"),
                (f"{('LEFT' if side=='L' else 'RIGHT')}_REAR_HEEL_TO_BUTTOCK_KNEE_FLEXION",f"thigh_{side}",f"shank_{side}",self._lever(f"thigh_{side}","distal",a,b),self._lever(f"shank_{side}","proximal",a,b),f"knee_{side}"),
            ]
        for phase,parent,child,rp,rc,name in connection_specs:
            joint=("elbow_"+name[-1] if name.startswith("elbow") else "hip_"+name[-1] if name.startswith("hip") else "knee_"+name[-1]);idx=self.samples[f"functional:{joint}"];Rp=self.R_W_from_B(parent,idx,heading);Rc=self.R_W_from_B(child,idx,heading);add(phase,f"shared_point_acceleration:{name}",self._shared_point(parent,child,idx,Rp,Rc,rp,rc))
        return blocks

    def residual(self,value:np.ndarray,include:set[str]|None=None)->np.ndarray:
        return np.concatenate([rows for _,_,rows in self.residual_blocks(value,include)])

    def numerical_jacobian(self,value:np.ndarray,include:set[str]|None=None,step:float=2e-6)->np.ndarray:
        base=self.residual(value,include);J=np.empty((len(base),len(value)))
        for column in range(len(value)):
            plus=value.copy();minus=value.copy();plus[column]+=step;minus[column]-=step
            J[:,column]=(self.residual(plus,include)-self.residual(minus,include))/(2*step)
        return J

    def old_null_direction(self)->np.ndarray:
        stream=self.dataset.nodes[_segment_node(self.dataset,"torso")];start=self.phases["NATURAL_STANDING_STILL"][0];index=int(np.searchsorted(stream.time_ns,start));Q=stream.R_N_i_from_B_i[index]
        v=np.zeros(self.parameter_count);v[self.slices["frame:torso"]]=-Q.T@np.array([0.,0.,1.]);v[self.slices["heading:torso"]]=1.0
        return v

    def output_metrics(self,value:np.ndarray)->dict:
        a,b,_,_,heading=self.unpack(value);truth=self.dataset.truth;predicted_axes={};actual_axes={};sample=None
        for segment in SEGMENTS:
            stream=self.dataset.nodes[_segment_node(self.dataset,segment)];sample=np.arange(0,len(stream.time_ns),20);R=self.R_W_from_B(segment,sample,heading);predicted_axes[segment]=np.einsum("nij,j->ni",R,a[segment]);actual_axes[segment]=truth.R_W_from_S[segment][sample,:,2]
        # One legal common global-yaw alignment only.
        cross=0.0;dot=0.0
        for segment in SEGMENTS:
            p=predicted_axes[segment];q=actual_axes[segment];dot+=float(np.sum(p[:,0]*q[:,0]+p[:,1]*q[:,1]));cross+=float(np.sum(p[:,0]*q[:,1]-p[:,1]*q[:,0]))
        beta=math.atan2(cross,dot);Rbeta=Rotation.from_euler("z",beta).as_matrix();errors=[]
        for segment in SEGMENTS:
            pred=np.einsum("ij,nj->ni",Rbeta,predicted_axes[segment]);actual=actual_axes[segment];angles=np.arctan2(np.linalg.norm(np.cross(pred,actual),axis=1),np.sum(pred*actual,axis=1));errors.extend(angles.tolist())
        # Relative graphical nodes use the same immutable generic template.
        dims=self.template["dimensions"];count=len(sample);root=np.zeros((count,3));axes={s:np.einsum("ij,nj->ni",Rbeta,predicted_axes[s]) for s in SEGMENTS};Rt=self.R_W_from_B("torso",sample,heading);Rp=self.R_W_from_B("pelvis",sample,heading);bt=np.einsum("ij,nj->ni",Rbeta,np.einsum("nij,j->ni",Rt,b["torso"]));bp=np.einsum("ij,nj->ni",Rbeta,np.einsum("nij,j->ni",Rp,b["pelvis"]));pred_nodes={"PelvisProxy":root};pred_nodes["C7Proxy"]=root+float(dims["C7Proxy_to_PelvisProxy_m"])*axes["torso"]
        for side,sign in (("L",1.0),("R",-1.0)):
            shoulder=pred_nodes["C7Proxy"]+sign*0.5*float(dims["graphical_shoulder_width_m"])*bt;elbow=shoulder+float(dims[f"rendering_upper_arm_length_{side}_m"])*axes[f"upper_arm_{side}"];wrist=elbow+float(dims[f"rendering_forearm_length_{side}_m"])*axes[f"forearm_{side}"];hip=root+sign*0.5*float(dims["graphical_hip_width_m"])*bp;knee=hip+float(dims[f"rendering_thigh_length_{side}_m"])*axes[f"thigh_{side}"];ankle=knee+float(dims[f"rendering_shank_length_{side}_m"])*axes[f"shank_{side}"];pred_nodes.update({f"ShoulderProxy_{side}":shoulder,f"Elbow_{side}":elbow,f"Wrist_{side}":wrist,f"HipProxy_{side}":hip,f"Knee_{side}":knee,f"Ankle_{side}":ankle})
        node_delta=[]
        truth_root=truth.graphical_nodes_W_m["PelvisProxy"][sample]
        for name,pred in pred_nodes.items():
            truth_name="C7Proxy" if name=="C7Proxy" else name
            actual=truth.graphical_nodes_W_m[truth_name][sample]-truth_root
            node_delta.extend(np.linalg.norm(pred-actual,axis=1).tolist())
        return {"common_global_yaw_alignment_rad":beta,"maximum_segment_axis_error_deg":math.degrees(max(errors)),"rms_segment_axis_error_deg":math.degrees(float(np.sqrt(np.mean(np.square(errors))))),"graphical_node_rms_mm":1000*float(np.sqrt(np.mean(np.square(node_delta)))),"graphical_node_max_mm":1000*max(node_delta)}


def svd_report(problem:S2UnifiedProblem,value:np.ndarray,include:set[str]|None=None)->tuple[np.ndarray,dict]:
    J=problem.numerical_jacobian(value,include);_,s,Vh=np.linalg.svd(J,full_matrices=False);rel=float(problem.gates["observability"]["relative_singular_value_threshold"]);absolute=float(s[0]*rel);rank=int(np.sum(s>absolute));v=problem.old_null_direction();jv=J@v
    return J,{"shape":[int(x) for x in J.shape],"parameter_count":problem.parameter_count,"rank":rank,"nullity":problem.parameter_count-rank,"sigma_max":float(s[0]),"weakest":float(s[-1]),"relative_threshold":rel,"absolute_threshold":absolute,"bottom_singular_values":s[-int(problem.gates["observability"]["bottom_spectrum_count"]):].tolist(),"old_null_Jv_l2":float(np.linalg.norm(jv)),"old_null_Jv_per_parameter_norm":float(np.linalg.norm(jv)/np.linalg.norm(v)),"weakest_vector":Vh[-1].tolist()}


def linear_profile_information(J:np.ndarray,v:np.ndarray)->dict:
    unit=v/np.linalg.norm(v);N=null_space(unit[None]);ja=J@unit;Jeta=J@N;projected=ja-Jeta@(np.linalg.pinv(Jeta)@ja);information=float(projected@projected);return {"I_eff":information,"sigma_alpha_rad":float(1/math.sqrt(information)) if information>0 else float("inf"),"projected_Jv_l2":float(np.linalg.norm(projected)),"nuisance_columns":int(N.shape[1])}
