from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping
import numpy as np

from . import so3
from .calibration import CalibrationBundle
from .fk import normalized_fk
from .joints import JOINTS
from .mapping import FrozenOperatorMapping
from .official import VqfNodeResult


@dataclass(frozen=True)
class BaselineTrajectory:
    name: str
    time_s: np.ndarray
    segment_quaternions: Mapping[str, np.ndarray]
    joint_quaternions: Mapping[str, np.ndarray]
    normalized_positions: tuple[dict[str, np.ndarray], ...]
    quality: Mapping[str, str]
    segment_tilt_sigma_rad: Mapping[str, np.ndarray]
    joint_relative_sigma_rad: Mapping[str, np.ndarray]
    metadata: Mapping[str, object]


def build_b0(vqf: Mapping[str, VqfNodeResult], mapping: FrozenOperatorMapping,
             calibration: CalibrationBundle) -> BaselineTrajectory:
    start = max(x.time_s[0] for x in vqf.values()); stop = min(x.time_s[-1] for x in vqf.values())
    rate = 200.0; t = np.arange(start, stop+1e-9, 1/rate)
    segment_q = {};segment_sigma={}
    for node, result in vqf.items():
        idx = np.searchsorted(result.time_s, t).clip(0, len(result.time_s)-1)
        segment = mapping.segment_for(node)
        segment_q[segment] = so3.continuous(so3.mul(result.quaternion6D_W_I[idx], calibration.by_node[node].q_I_S))
        calibration_sigma=np.sqrt(np.linalg.eigvalsh(calibration.by_node[node].covariance_rad2).max())
        segment_sigma[segment]=np.sqrt(calibration_sigma**2+(result.bias_sigma_rad_s[idx]*.1)**2+np.deg2rad(.5)**2)
    joint_q = {j.name: so3.mul(so3.inv(segment_q[j.parent]), segment_q[j.child]) for j in JOINTS}
    joint_sigma={j.name:np.sqrt(segment_sigma[j.parent]**2+segment_sigma[j.child]**2) for j in JOINTS}
    positions = tuple(normalized_fk({s: segment_q[s][i] for s in segment_q}) for i in range(len(t)))
    return BaselineTrajectory("B0_OFFICIAL_VQF", t, segment_q, joint_q, positions,
                              {s:"VQF_COMPARATOR_NOT_TRUTH" for s in segment_q},segment_sigma,joint_sigma,
                              {"global_yaw":"GAUGE_ACTIVE","root_world_position":"UNAVAILABLE",
                               "uncertainty":"VQF_BIAS_SIGMA_PLUS_CALIBRATION_PROXY_NOT_TRUTH"})


def build_b1(b0: BaselineTrajectory, heading_offsets: Mapping[str, np.ndarray],
             heading_confidence: Mapping[str, float], hinge_axes: Mapping[str, np.ndarray]) -> BaselineTrajectory:
    q = {s:x.copy() for s,x in b0.segment_quaternions.items()}
    for joint in JOINTS:
        if joint.name not in heading_offsets or heading_confidence.get(joint.name, 0) < .25:
            continue
        delta = np.asarray(heading_offsets[joint.name]).reshape(-1)
        if len(delta) != len(b0.time_s):
            source = np.linspace(0, 1, len(delta)); target = np.linspace(0, 1, len(b0.time_s))
            delta = np.interp(target, source, delta)
        correction = np.column_stack((np.cos(delta/2), np.zeros(len(delta)), np.zeros(len(delta)), np.sin(delta/2)))
        q[joint.child] = so3.mul(correction, q[joint.child])
    # Confidence-gated soft projection of each calibrated hinge increment onto
    # its dominant-axis distribution. This is deliberately not a hard hinge.
    for joint in JOINTS:
        if joint.name not in hinge_axes or heading_confidence.get(joint.name,0)<.25:
            continue
        axis=np.asarray(hinge_axes[joint.name],float);axis/=np.linalg.norm(axis);strength=.2*float(np.clip(heading_confidence[joint.name],0,1))
        previous=so3.between(q[joint.parent][0],q[joint.child][0])
        for i in range(1,len(b0.time_s)):
            relative=so3.between(q[joint.parent][i],q[joint.child][i]);increment=so3.log(so3.between(previous,relative));orthogonal=increment-axis*np.dot(axis,increment)
            q[joint.child][i]=so3.apply_right(q[joint.child][i],-strength*orthogonal)
            previous=so3.between(q[joint.parent][i],q[joint.child][i])
    joint_q = {j.name: so3.mul(so3.inv(q[j.parent]), q[j.child]) for j in JOINTS}
    positions = tuple(normalized_fk({s:q[s][i] for s in q}) for i in range(len(b0.time_s)))
    quality = {s:"QMT_CONDITIONAL_COMPARATOR" for s in q}
    joint_sigma={k:v.copy() for k,v in b0.joint_relative_sigma_rad.items()}
    for joint in JOINTS:
        confidence=float(np.clip(heading_confidence.get(joint.name,0),0,1))
        if joint.name in hinge_axes and confidence>=.25:joint_sigma[joint.name]*=np.sqrt(1-.5*confidence)
    return BaselineTrajectory("B1_VQF_QMT_CONDITIONAL", b0.time_s, q, joint_q, positions, quality,
                              b0.segment_tilt_sigma_rad,joint_sigma,
                              {**b0.metadata,"qmt":"CONFIDENCE_GATED_HEADING_AND_SOFT_HINGE_PROJECTION"})
