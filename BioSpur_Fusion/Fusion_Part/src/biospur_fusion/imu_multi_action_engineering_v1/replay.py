"""Label-blind continuous replay that consumes every frozen functional block."""
from __future__ import annotations

import math
from typing import Any,Mapping
import numpy as np
from scipy.spatial.transform import Rotation

from biospur_fusion.imu_preview_v0.core import skeleton_from_directions
from .model import SEGMENTS,normalize,yaw


def _limited_fill(values:np.ndarray,valid:np.ndarray,max_rows:int)->tuple[np.ndarray,np.ndarray,int]:
    out=values.copy();filled=valid.copy();count=0;i=0
    while i<len(valid):
        if valid[i]:i+=1;continue
        start=i
        while i<len(valid) and not valid[i]:i+=1
        stop=i-1;length=stop-start+1
        if start>0 and i<len(valid) and length<=max_rows:
            alpha=np.arange(1,length+1)/(length+1);out[start:i]=(1-alpha[:,None])*out[start-1]+alpha[:,None]*out[i];filled[start:i]=True;count+=length
    return out,filled,count


def _functional_adjust(direction:np.ndarray,axis:np.ndarray,zero:float,gain:float)->np.ndarray:
    axis=normalize(axis);projected=direction-axis*float(direction@axis)
    if np.linalg.norm(projected)<1e-12:return normalize(direction)
    projected=Rotation.from_rotvec(-float(zero)*axis).apply(normalize(projected));return normalize((1.-gain)*direction+gain*projected)


def continuous_replay(timeline,calibration:Mapping[str,Any],template:Mapping[str,Any],gates:Mapping[str,Any])->dict[str,np.ndarray]:
    nodes={node:i for i,node in enumerate(timeline.node_order)};segment_node={segment:nodes[node] for node,segment in gates["node_to_segment"].items()};n=len(timeline.time_ns);raw=np.full((n,len(SEGMENTS),3),np.nan);corrected_rotation=np.full((n,len(SEGMENTS),3,3),np.nan)
    for k,segment in enumerate(SEGMENTS):
        c=calibration["segments"][segment];j=segment_node[segment];corrected_rotation[:,k]=np.einsum("ij,njk->nik",yaw(float(c["relative_heading_rad"])),timeline.rotation[:,j]);raw[:,k]=np.einsum("nij,j->ni",corrected_rotation[:,k],np.asarray(c["board_frame_longitudinal_axis"]))
    observed_valid=timeline.all_nodes_valid.copy();valid=np.ones(n,bool);max_rows=int(round(float(gates["preview_gates"]["maximum_short_interpolation_gap_s"])*float(gates["common_time"]["rate_hz"])));filled_count=0
    for k in range(len(SEGMENTS)):
        raw[:,k],component_valid,count=_limited_fill(raw[:,k],observed_valid,max_rows);filled_count+=count;valid&=component_valid
    output=raw.copy();functional={key:np.asarray(value,float) for key,value in calibration["functional_axes_display_frame"].items()};zeros=calibration["joint_neutral_zero_rad"];gain=float(gates["preview_gates"]["functional_projection_gain"]);index={segment:i for i,segment in enumerate(SEGMENTS)}
    for i in np.flatnonzero(valid):
        output[i,index["torso"]]=_functional_adjust(output[i,index["torso"]],normalize(np.cross(functional["trunk_turn"],functional["trunk_flex"])),zeros["trunk"],gain)
        for side in ("L","R"):
            output[i,index[f"upper_arm_{side}"]]=_functional_adjust(output[i,index[f"upper_arm_{side}"]],functional[f"shoulder_{side}"],0.,gain)
            output[i,index[f"forearm_{side}"]]=_functional_adjust(output[i,index[f"forearm_{side}"]],functional[f"elbow_{side}"],zeros[f"elbow_{side}"],gain)
            output[i,index[f"thigh_{side}"]]=_functional_adjust(output[i,index[f"thigh_{side}"]],functional[f"hip_{side}"],zeros[f"hip_{side}"],gain)
            output[i,index[f"shank_{side}"]]=_functional_adjust(output[i,index[f"shank_{side}"]],functional[f"knee_{side}"],zeros[f"knee_{side}"],gain)
    skeleton=np.full((n,15,3),np.nan)
    for i in np.flatnonzero(valid):skeleton[i]=skeleton_from_directions(output[i],template,float(gates["rendering"]["head_proxy_length_m"]))
    return {"time_ns":timeline.time_ns.copy(),"raw_segment_direction":raw,"segment_direction":output,"corrected_rotation":corrected_rotation,"skeleton_m":skeleton,"observed_all_nodes_valid":timeline.all_nodes_valid.astype(np.uint8),"replay_valid":valid.astype(np.uint8),"short_gap_interpolated_rows":np.asarray([filled_count],np.int64)}


def functional_consumption_audit(timeline,calibration:Mapping[str,Any],template:Mapping[str,Any],gates:Mapping[str,Any],baseline:Mapping[str,np.ndarray])->dict:
    rows=[];base=baseline["segment_direction"];targets={"shoulder_L":"upper_arm_L","shoulder_R":"upper_arm_R","elbow_L":"forearm_L","elbow_R":"forearm_R","hip_L":"thigh_L","hip_R":"thigh_R","knee_L":"shank_L","knee_R":"shank_R","trunk_turn":"torso","trunk_flex":"torso"};index={s:i for i,s in enumerate(SEGMENTS)}
    for name,target in targets.items():
        modified={**calibration,"functional_axes_display_frame":{**calibration["functional_axes_display_frame"]}};axis=np.asarray(modified["functional_axes_display_frame"][name]);reference=np.array([1.,0,0]) if abs(axis[0])<.9 else np.array([0.,1.,0]);modified["functional_axes_display_frame"][name]=Rotation.from_rotvec(.01*normalize(np.cross(axis,reference))).apply(axis).tolist();changed=continuous_replay(timeline,modified,template,gates)["segment_direction"];difference=np.degrees(np.arccos(np.clip(np.sum(base[:,index[target]]*changed[:,index[target]],axis=1),-1,1)));finite=np.isfinite(difference);maximum=float(np.max(difference[finite])) if np.any(finite) else 0.;rows.append({"functional_parameter":name,"target_segment":target,"perturbation_rad":.01,"maximum_output_change_deg":maximum,"consumed":maximum>1e-6})
    for name,target in (("elbow_L","forearm_L"),("elbow_R","forearm_R"),("hip_L","thigh_L"),("hip_R","thigh_R"),("knee_L","shank_L"),("knee_R","shank_R"),("trunk","torso")):
        modified={**calibration,"joint_neutral_zero_rad":{**calibration["joint_neutral_zero_rad"]}};modified["joint_neutral_zero_rad"][name]+=.01;changed=continuous_replay(timeline,modified,template,gates)["segment_direction"];difference=np.degrees(np.arccos(np.clip(np.sum(base[:,index[target]]*changed[:,index[target]],axis=1),-1,1)));finite=np.isfinite(difference);maximum=float(np.max(difference[finite])) if np.any(finite) else 0.;rows.append({"functional_parameter":f"neutral_zero:{name}","target_segment":target,"perturbation_rad":.01,"maximum_output_change_deg":maximum,"consumed":maximum>1e-6})
    return {"schema":"biospur-frozen-functional-parameter-consumption-v1","rows":rows,"pass":all(row["consumed"] for row in rows)}
