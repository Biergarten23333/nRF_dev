from __future__ import annotations

from collections import defaultdict
from typing import Mapping
import numpy as np

from .calibration import CalibrationBundle, SegmentCalibration
from .estimator import CoupledPoseEstimator, EstimatorConfig
from .frontend import ErrorStateImuFrontend, FrontendConfig
from .mapping import FrozenOperatorMapping
from .types import FrontendOutput, ImuSample, PoseFrame


def group_samples(samples: list[ImuSample]) -> dict[str,list[ImuSample]]:
    out=defaultdict(list)
    for x in samples: out[x.node_id].append(x)
    return {k:sorted(v,key=lambda x:(x.time_s,x.sequence)) for k,v in out.items()}


def run_frontends(samples_by_node: Mapping[str,list[ImuSample]], config: FrontendConfig|None=None,
                  initial_q_WI: Mapping[str,np.ndarray]|None=None) -> tuple[dict[str,list[FrontendOutput]],dict]:
    results={};reports={}
    for node in sorted(samples_by_node):
        frontend=ErrorStateImuFrontend(node,config)
        if initial_q_WI is not None:
            first=samples_by_node[node][0];frontend.q_WI=np.asarray(initial_q_WI[node],float).copy()
            frontend.initialized=True;frontend.last_time=first.time_s;frontend.last_boot=first.boot_id
        results[node]=frontend.run(list(samples_by_node[node]))
        reports[node]={"factor_counts":frontend.factor_counts,"bias_update_norm":frontend.bias_update_norm,
                       "final_gyro_bias":frontend.bg.tolist(),"final_accel_bias":frontend.ba.tolist()}
    return results,reports


def common_causal_grid(frontends: Mapping[str,list[FrontendOutput]], rate_hz:float=50.0,max_age_s:float=.03):
    start=max(x[0].time_s for x in frontends.values());stop=min(x[-1].time_s for x in frontends.values())
    times=np.arange(start,stop+1e-9,1/rate_hz);indices={k:0 for k in frontends};out=[]
    for t in times:
        row={};valid=True
        for node,series in frontends.items():
            i=indices[node]
            while i+1<len(series) and series[i+1].time_s<=t: i+=1
            indices[node]=i;item=series[i]
            if item.time_s>t+1e-12 or t-item.time_s>max_age_s:valid=False;break
            row[node]=item
        if valid:out.append((float(t),row))
    return out


def run_coupled(frontends: Mapping[str,list[FrontendOutput]], mapping:FrozenOperatorMapping,
                calibration:CalibrationBundle, config:EstimatorConfig|None=None,
                hinge_axes:Mapping[str,np.ndarray]|None=None,hinge_confidence:Mapping[str,float]|None=None,
                heading_targets:Mapping[str,np.ndarray]|None=None,
                heading_confidence:Mapping[str,float]|None=None) -> tuple[list[PoseFrame],CoupledPoseEstimator]:
    est=CoupledPoseEstimator(mapping,calibration,config,hinge_axes,hinge_confidence,heading_targets,heading_confidence)
    frames=[est.update(t,row) for t,row in common_causal_grid(frontends)]
    return frames,est


def calibration_from_known(mapping:FrozenOperatorMapping,q_I_S:Mapping[str,np.ndarray],sigma_rad:float=np.deg2rad(1.0)) -> CalibrationBundle:
    rows={node:SegmentCalibration(node,mapping.segment_for(node),np.asarray(q),np.eye(3)*sigma_rad**2,
          "SYNTHETIC_KNOWN_NONIDENTITY",("independent_synthetic_truth",), "C2CC_DISTINCT" if node=="BSFC2CC" else "H9") for node,q in q_I_S.items()}
    from types import MappingProxyType
    return CalibrationBundle(MappingProxyType(rows))
