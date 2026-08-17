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


def build_b0(vqf: Mapping[str, VqfNodeResult], mapping: FrozenOperatorMapping,
             calibration: CalibrationBundle) -> BaselineTrajectory:
    start = max(x.time_s[0] for x in vqf.values()); stop = min(x.time_s[-1] for x in vqf.values())
    rate = 200.0; t = np.arange(start, stop+1e-9, 1/rate)
    segment_q = {}
    for node, result in vqf.items():
        idx = np.searchsorted(result.time_s, t).clip(0, len(result.time_s)-1)
        segment = mapping.segment_for(node)
        segment_q[segment] = so3.continuous(so3.mul(result.quaternion6D_W_I[idx], calibration.by_node[node].q_I_S))
    joint_q = {j.name: so3.mul(so3.inv(segment_q[j.parent]), segment_q[j.child]) for j in JOINTS}
    positions = tuple(normalized_fk({s: segment_q[s][i] for s in segment_q}) for i in range(len(t)))
    return BaselineTrajectory("B0_OFFICIAL_VQF", t, segment_q, joint_q, positions,
                              {s:"VQF_COMPARATOR_NOT_TRUTH" for s in segment_q})


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
    joint_q = {j.name: so3.mul(so3.inv(q[j.parent]), q[j.child]) for j in JOINTS}
    positions = tuple(normalized_fk({s:q[s][i] for s in q}) for i in range(len(b0.time_s)))
    quality = {s:"QMT_CONDITIONAL_COMPARATOR" for s in q}
    return BaselineTrajectory("B1_VQF_QMT_CONDITIONAL", b0.time_s, q, joint_q, positions, quality)
