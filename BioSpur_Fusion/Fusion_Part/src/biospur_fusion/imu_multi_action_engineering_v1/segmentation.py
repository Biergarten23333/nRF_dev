"""Signal-conditioned, single-pass segmentation for the eleven actions."""
from __future__ import annotations

from typing import Any,Mapping
import numpy as np


def _smooth(values:np.ndarray,count:int)->np.ndarray:
    count=max(1,count);kernel=np.ones(count);finite=np.isfinite(values);numerator=np.convolve(np.where(finite,values,0.),kernel,mode="same");denominator=np.convolve(finite.astype(float),kernel,mode="same");return numerator/np.maximum(denominator,1.)


def _sample(rows:np.ndarray,maximum:int)->np.ndarray:
    rows=np.asarray(rows,int)
    if len(rows)>maximum:rows=rows[np.round(np.linspace(0,len(rows)-1,maximum)).astype(int)]
    return np.unique(rows)


def _dynamic(rows:np.ndarray,energy:np.ndarray,cfg:Mapping[str,Any])->np.ndarray:
    finite=rows[np.isfinite(energy[rows])];threshold=max(np.quantile(energy[finite],float(cfg["dynamic_energy_quantile"])),np.deg2rad(float(cfg["minimum_dynamic_rate_dps"])))
    return finite[energy[finite]>=threshold]


def _returns(rows:np.ndarray,energy:np.ndarray,cfg:Mapping[str,Any])->np.ndarray:
    finite=rows[np.isfinite(energy[rows])];threshold=np.quantile(energy[finite],float(cfg["return_energy_quantile"]));return finite[energy[finite]<=threshold]


def segment_actions(timeline,windows:Mapping[str,tuple[int,int]],node_to_segment:Mapping[str,str],cfg:Mapping[str,Any])->dict:
    nodes={node:i for i,node in enumerate(timeline.node_order)};segment_node={segment:nodes[node] for node,segment in node_to_segment.items()};rate=1e9/float(np.median(np.diff(timeline.time_ns)));smooth_count=max(1,int(round(float(cfg["smoothing_s"])*rate)));maximum=int(cfg["maximum_rows_per_phase"]);minimum=int(cfg["minimum_rows_per_estimation_phase"]);gyro_norm=np.linalg.norm(timeline.gyro_rad_s,axis=2);gyro_energy=np.stack([_smooth(gyro_norm[:,j],smooth_count) for j in range(len(timeline.node_order))],axis=1);actions={}
    def window_rows(name,relevant=None):
        start,stop=windows[name];mask=(timeline.time_ns>=start)&(timeline.time_ns<=stop)
        if relevant is None:mask&=timeline.all_nodes_valid
        else:mask&=np.all(timeline.valid[:,[segment_node[segment] for segment in relevant]],axis=1)
        return np.flatnonzero(mask)
    def add(name,phase,rows,detector,relevant):
        selected=_sample(rows,maximum);actions.setdefault(name,[]).append({"phase":phase,"row_indices":selected.tolist(),"global_time_ns":timeline.time_ns[selected].astype(int).tolist(),"row_count":int(len(selected)),"detector":detector,"relevant_segments":list(relevant),"status":"PASS" if len(selected)>=minimum else "FAIL_INSUFFICIENT_PHASE_ROWS"})
    for name in ("initial_still_attempt2","t_pose"):
        rows=window_rows(name);combined=np.mean(gyro_energy,axis=1);add(name,"neutral_soft_pose" if name.startswith("initial") else "independent_t_pose_soft_pose",_returns(rows,combined,cfg),"LOW_AGGREGATE_GYRO_ENERGY_INTERIOR",tuple(segment_node))
    rows=window_rows("arms");li=(gyro_energy[:,segment_node["upper_arm_L"]]+gyro_energy[:,segment_node["forearm_L"]])/2;ri=(gyro_energy[:,segment_node["upper_arm_R"]]+gyro_energy[:,segment_node["forearm_R"]])/2;dynamic=_dynamic(rows,np.maximum(li,ri),cfg);ratio=li[dynamic]/np.maximum(ri[dynamic],1e-9);add("arms","left_arm_raise_lower",dynamic[ratio>=1.35],"LEFT_RIGHT_CHAIN_ENERGY_RATIO",("torso","upper_arm_L","forearm_L"));add("arms","right_arm_raise_lower",dynamic[ratio<=1/1.35],"LEFT_RIGHT_CHAIN_ENERGY_RATIO",("torso","upper_arm_R","forearm_R"));add("arms","bilateral_raise_lower",dynamic[(ratio>1/1.35)&(ratio<1.35)],"LEFT_RIGHT_CHAIN_ENERGY_RATIO",("torso","upper_arm_L","forearm_L","upper_arm_R","forearm_R"));add("arms","neutral_returns",_returns(rows,np.maximum(li,ri),cfg),"LOW_BILATERAL_CHAIN_ENERGY",("upper_arm_L","forearm_L","upper_arm_R","forearm_R"))
    for action,side in (("left_elbow","L"),("right_elbow_attempt2","R")):
        rows=window_rows(action);upper=segment_node[f"upper_arm_{side}"];fore=segment_node[f"forearm_{side}"];energy=np.maximum(gyro_energy[:,upper],gyro_energy[:,fore]);dynamic=_dynamic(rows,energy,cfg);ratio=gyro_energy[dynamic,upper]/np.maximum(gyro_energy[dynamic,fore],1e-9);cut=float(np.median(ratio));curl=dynamic[ratio>=cut];pronation=dynamic[ratio<cut]
        add(action,"curl",curl,"RELATIVE_PROXIMAL_TO_DISTAL_ACTIVITY_CLUSTER",(f"upper_arm_{side}",f"forearm_{side}"));add(action,"pronation_supination",pronation,"RELATIVE_PROXIMAL_TO_DISTAL_ACTIVITY_CLUSTER",(f"upper_arm_{side}",f"forearm_{side}"));add(action,"neutral_return",_returns(rows,energy,cfg),"LOW_CHAIN_ENERGY",(f"upper_arm_{side}",f"forearm_{side}"))
    for action,side in (("left_knee","L"),("right_knee","R")):
        rows=window_rows(action);energy=np.maximum(gyro_energy[:,segment_node["pelvis"]],gyro_energy[:,segment_node[f"thigh_{side}"]]);add(action,"high_knee_raise_lower",_dynamic(rows,energy,cfg),"PELVIS_THIGH_ACTIVITY",("pelvis",f"thigh_{side}",f"shank_{side}"));add(action,"neutral_return",_returns(rows,energy,cfg),"LOW_HIP_CHAIN_ENERGY",("pelvis",f"thigh_{side}",f"shank_{side}"))
    for action,side in (("left_heel","L"),("right_heel","R")):
        rows=window_rows(action);energy=np.maximum(gyro_energy[:,segment_node[f"thigh_{side}"]],gyro_energy[:,segment_node[f"shank_{side}"]]);add(action,"heel_to_butt_flexion",_dynamic(rows,energy,cfg),"THIGH_SHANK_ACTIVITY",(f"thigh_{side}",f"shank_{side}"));add(action,"neutral_return",_returns(rows,energy,cfg),"LOW_KNEE_CHAIN_ENERGY",(f"thigh_{side}",f"shank_{side}"))
    rows=window_rows("squats");lower=[segment_node[x] for x in ("pelvis","thigh_L","thigh_R","shank_L","shank_R")];energy=np.max(gyro_energy[:,lower],axis=1);add("squats","descent_ascent",_dynamic(rows,energy,cfg),"BILATERAL_LOWER_CHAIN_ACTIVITY",("pelvis","thigh_L","thigh_R","shank_L","shank_R"));add("squats","neutral_returns",_returns(rows,energy,cfg),"LOW_BILATERAL_LOWER_CHAIN_ENERGY",("pelvis","thigh_L","thigh_R","shank_L","shank_R"))
    rows=window_rows("trunk");torso=segment_node["torso"];pelvis=segment_node["pelvis"];energy=np.maximum(gyro_energy[:,torso],gyro_energy[:,pelvis]);dynamic=_dynamic(rows,energy,cfg);vectors=timeline.gyro_rad_s[dynamic,torso];center=vectors-np.median(vectors,axis=0);_,_,vh=np.linalg.svd(center,full_matrices=False);p1=center@vh[0];p2=center@vh[1];turn=np.abs(p1)>=np.abs(p2);add("trunk","left_turn",dynamic[turn&(p1>=0)],"SIGNED_DOMINANT_TORSO_GYRO_COMPONENT",("pelvis","torso"));add("trunk","right_turn",dynamic[turn&(p1<0)],"SIGNED_DOMINANT_TORSO_GYRO_COMPONENT",("pelvis","torso"));add("trunk","forward_flexion_recovery",dynamic[~turn],"NONCOLLINEAR_SECOND_TORSO_GYRO_COMPONENT",("pelvis","torso"));add("trunk","neutral_returns",_returns(rows,energy,cfg),"LOW_TRUNK_CHAIN_ENERGY",("pelvis","torso"))
    failures=[{"action":action,"phase":phase["phase"],"row_count":phase["row_count"]} for action,phases in actions.items() for phase in phases if phase["status"]!="PASS"]
    return {"schema":"biospur-signal-conditioned-action-segmentation-v1","timeline_rate_hz":rate,"actions":actions,"failures":failures,"pass":not failures,"labels_used_only_to_bound_calibration_windows":True,"signal_content_selects_phases":True}
