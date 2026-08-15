"""Per-node strict common-time resampling with covariance and validity masks."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping
import numpy as np
from scipy.spatial.transform import Rotation,Slerp


@dataclass
class CommonTimelineV1:
    time_ns:np.ndarray
    node_order:tuple[str,...]
    rotation:np.ndarray
    covariance_rad2:np.ndarray
    gyro_rad_s:np.ndarray
    accel_mps2:np.ndarray
    valid:np.ndarray
    all_nodes_valid:np.ndarray
    accounting:dict


def _brackets(source,target):
    hi=np.searchsorted(source,target,side="left");return np.clip(hi-1,0,len(source)-1),np.clip(hi,0,len(source)-1)


def build_common_timeline(q2:Mapping[str,object],start_ns:int,stop_ns:int,cfg:Mapping[str,float])->CommonTimelineV1:
    nodes=tuple(sorted(q2));step=int(round(1e9/float(cfg["rate_hz"])));times=np.arange(start_ns,stop_ns+1,step,dtype=np.int64);n=len(times);m=len(nodes);rotation=np.full((n,m,3,3),np.nan);covariance=np.full((n,m,3,3),np.nan);gyro=np.full((n,m,3),np.nan);accel=np.full((n,m,3),np.nan);valid=np.zeros((n,m),bool);accounting={};maximum=int(round(float(cfg["maximum_bracket_gap_s"])*1e9))
    for j,node in enumerate(nodes):
        result=q2[node];lo,hi=_brackets(result.time_ns,times);outside=(times<result.time_ns[0])|(times>result.time_ns[-1]);gap=(result.time_ns[hi]-result.time_ns[lo])>maximum;boot=result.boot_epoch[lo]!=result.boot_epoch[hi];boundary=np.zeros(n,bool)
        for k,(a,b) in enumerate(zip(lo,hi)):
            if b>a and np.any(result.gap_boundary[a+1:b+1]):boundary[k]=True
        finite=np.isfinite(result.q_wxyz[lo]).all(1)&np.isfinite(result.q_wxyz[hi]).all(1)&np.isfinite(result.covariance_rad2[lo]).all((1,2))&np.isfinite(result.covariance_rad2[hi]).all((1,2))&np.isfinite(result.gyro_corrected_rad_s[lo]).all(1)&np.isfinite(result.gyro_corrected_rad_s[hi]).all(1);ok=~outside&~gap&~boot&~boundary&finite
        if np.any(ok):
            source=(result.time_ns-result.time_ns[0])/1e9;target=(times[ok]-result.time_ns[0])/1e9;rotation[ok,j]=Slerp(source,Rotation.from_quat(result.q_wxyz[:,[1,2,3,0]]))(target).as_matrix();denom=np.maximum(1,result.time_ns[hi[ok]]-result.time_ns[lo[ok]]).astype(float);weight=(times[ok]-result.time_ns[lo[ok]])/denom
            for target_array,source_array in ((covariance,result.covariance_rad2),(gyro,result.gyro_corrected_rad_s),(accel,result.accel_mps2)):target_array[ok,j]=(1.-weight.reshape((-1,)+(1,)*(source_array.ndim-1)))*source_array[lo[ok]]+weight.reshape((-1,)+(1,)*(source_array.ndim-1))*source_array[hi[ok]]
        counts={"requested_grid_rows":n,"accepted_interpolated":int(ok.sum()),"outside_window_rejected":int(outside.sum()),"bracket_gap_rejected":int((~outside&gap).sum()),"boot_epoch_rejected":int((~outside&~gap&boot).sum()),"q2_gap_or_nonfinite_rejected":int((~outside&~gap&~boot&(boundary|~finite)).sum())};counts["accounting_closed"]=counts["requested_grid_rows"]==sum(counts[key] for key in ("accepted_interpolated","outside_window_rejected","bracket_gap_rejected","boot_epoch_rejected","q2_gap_or_nonfinite_rejected"));accounting[node]=counts;valid[:,j]=ok
    all_valid=np.all(valid,axis=1);audit={"schema":"biospur-common-time-v1","rate_hz":float(cfg["rate_hz"]),"maximum_bracket_gap_s":float(cfg["maximum_bracket_gap_s"]),"orientation":"SO3_SLERP","covariance":"LINEAR_BETWEEN_VALID_SAME_BOOT_BRACKETS","gyro_accel":"LINEAR_BETWEEN_VALID_SAME_BOOT_BRACKETS","nodes":accounting,"all_nodes_valid_rows":int(all_valid.sum()),"grid_rows":n,"all_nodes_valid_fraction":float(all_valid.mean()),"invalid_rows_are_not_interpolated_here":True};return CommonTimelineV1(times,nodes,rotation,covariance,gyro,accel,valid,all_valid,audit)
