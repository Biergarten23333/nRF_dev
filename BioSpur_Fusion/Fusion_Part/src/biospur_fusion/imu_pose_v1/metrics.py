from __future__ import annotations

import numpy as np

from . import so3
from .fk import bone_lengths
from .joints import JOINTS
from .types import PoseFrame


def gauge_align(q_est: dict[str,np.ndarray], q_truth: dict[str,np.ndarray]) -> dict[str,np.ndarray]:
    # One common global yaw alignment from pelvis; no per-segment cheating.
    error=so3.mul(q_truth["pelvis"],so3.inv(q_est["pelvis"]))
    rv=so3.log(error);yaw=so3.exp(np.array([0.,0.,rv[2]]))
    return {s:so3.mul(yaw,q) for s,q in q_est.items()}


def synthetic_errors(frames:list[PoseFrame], truth_time:np.ndarray, truth:dict[str,np.ndarray]) -> dict:
    orientation=[];relative=[];static_tilt=[];lengths=[]
    for frame in frames:
        i=int(np.argmin(np.abs(truth_time-frame.time_s)))
        target={s:q[i] for s,q in truth.items()};aligned=gauge_align(dict(frame.segment_quaternions_W_S),target)
        orientation.extend(float(so3.geodesic(aligned[s],target[s])) for s in target)
        for j in JOINTS:
            a=so3.between(aligned[j.parent],aligned[j.child]);b=so3.between(target[j.parent],target[j.child])
            relative.append(float(so3.geodesic(a,b)))
        if frame.time_s-truth_time[0]<1.5:static_tilt.extend(orientation[-10:])
        lengths.append(bone_lengths(frame.normalized_joint_positions))
    length_array=np.vstack(lengths)
    return {"orientation_rad":np.asarray(orientation),"relative_joint_rad":np.asarray(relative),
            "static_tilt_rad":np.asarray(static_tilt),
            "bone_length_max_variation":float(np.max(np.ptp(length_array,axis=0)))}
