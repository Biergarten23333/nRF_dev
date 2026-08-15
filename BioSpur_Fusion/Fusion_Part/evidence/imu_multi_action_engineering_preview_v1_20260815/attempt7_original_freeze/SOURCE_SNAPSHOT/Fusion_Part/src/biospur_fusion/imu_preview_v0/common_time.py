"""Strict per-node association on a Listener-backed global-time grid."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

import numpy as np
from scipy.spatial.transform import Rotation, Slerp

from biospur_fusion.imu.q1 import quaternion_to_matrix
from .q2 import Q2Result


@dataclass
class CommonTimeline:
    time_ns: np.ndarray
    node_order: tuple[str,...]
    rotation: np.ndarray
    gyro_rad_s: np.ndarray
    accel_mps2: np.ndarray
    stationary: np.ndarray
    valid: np.ndarray
    all_nodes_valid: np.ndarray
    accounting: dict


def _brackets(source: np.ndarray, target: np.ndarray) -> tuple[np.ndarray,np.ndarray]:
    hi=np.searchsorted(source,target,side="left");return np.clip(hi-1,0,len(source)-1),np.clip(hi,0,len(source)-1)


def build_common_timeline(q2: Mapping[str,Q2Result], start_ns: int, stop_ns: int, cfg: Mapping[str,float]) -> CommonTimeline:
    nodes=tuple(sorted(q2));step=int(round(1e9/float(cfg["rate_hz"])));times=np.arange(start_ns,stop_ns+1,step,dtype=np.int64);n=len(times);m=len(nodes)
    rotation=np.full((n,m,3,3),np.nan);gyro=np.full((n,m,3),np.nan);accel=np.full((n,m,3),np.nan);stationary=np.zeros((n,m),bool);valid=np.zeros((n,m),bool);accounting={}
    max_gap=int(round(float(cfg["maximum_bracket_gap_s"])*1e9))
    for j,node in enumerate(nodes):
        r=q2[node];lo,hi=_brackets(r.time_ns,times);outside=(times<r.time_ns[0])|(times>r.time_ns[-1]);gap=(r.time_ns[hi]-r.time_ns[lo])>max_gap;boot=r.boot_epoch[lo]!=r.boot_epoch[hi];q2_gap=np.zeros(n,bool)
        for k,(a,b) in enumerate(zip(lo,hi)):
            if b>a and np.any(r.gap_boundary[a+1:b+1]):q2_gap[k]=True
        finite=np.isfinite(r.q_wxyz[lo]).all(1)&np.isfinite(r.q_wxyz[hi]).all(1)&np.isfinite(r.gyro_corrected_rad_s[lo]).all(1)&np.isfinite(r.gyro_corrected_rad_s[hi]).all(1)
        ok=~outside&~gap&~boot&~q2_gap&finite
        if np.any(ok):
            source_s=(r.time_ns-r.time_ns[0])/1e9;target_s=(times[ok]-r.time_ns[0])/1e9
            quat_xyzw=r.q_wxyz[:,[1,2,3,0]];rotation[ok,j]=Slerp(source_s,Rotation.from_quat(quat_xyzw))(target_s).as_matrix()
            denom=np.maximum(1,(r.time_ns[hi[ok]]-r.time_ns[lo[ok]])).astype(float);w=(times[ok]-r.time_ns[lo[ok]])/denom
            gyro[ok,j]=(1.-w[:,None])*r.gyro_corrected_rad_s[lo[ok]]+w[:,None]*r.gyro_corrected_rad_s[hi[ok]]
            accel[ok,j]=(1.-w[:,None])*r.accel_mps2[lo[ok]]+w[:,None]*r.accel_mps2[hi[ok]]
            stationary[ok,j]=r.stationary[lo[ok]]&r.stationary[hi[ok]]
        counts={"requested_grid_rows":n,"accepted_interpolated":int(ok.sum()),"outside_window_rejected":int(outside.sum()),"bracket_gap_rejected":int((~outside&gap).sum()),"clock_segment_rejected":int((~outside&~gap&boot).sum()),"q2_status_or_nonfinite_rejected":int((~outside&~gap&~boot&(q2_gap|~finite)).sum())}
        counts["grid_accounting_closed"]=counts["requested_grid_rows"]==sum(counts[k] for k in ("accepted_interpolated","outside_window_rejected","bracket_gap_rejected","clock_segment_rejected","q2_status_or_nonfinite_rejected"));accounting[node]=counts;valid[:,j]=ok
    all_valid=np.all(valid,axis=1)
    return CommonTimeline(times,nodes,rotation,gyro,accel,stationary,valid,all_valid,{"schema":"biospur-common-global-time-accounting-v0","timestamp_field":"global_time_ns","interpolation":{"orientation":"SO3_SLERP_PER_NODE","gyro":"LINEAR_PER_NODE","accel":"LINEAR_PER_NODE"},"nodes":accounting,"all_nodes_valid_rows":int(all_valid.sum()),"grid_rows":n,"all_nodes_valid_fraction":float(all_valid.mean()),"multi_node_residual_policy":"ONLY_ROWS_WHERE_ALL_RELEVANT_NODES_VALID"})
