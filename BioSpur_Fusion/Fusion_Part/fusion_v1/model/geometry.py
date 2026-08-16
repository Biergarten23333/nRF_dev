from __future__ import annotations
import numpy as np
from scipy.spatial.transform import Rotation, Slerp

def transform(rotation_vector, translation):
    T=np.eye(4); T[:3,:3]=Rotation.from_rotvec(rotation_vector).as_matrix(); T[:3,3]=translation
    return T

def compose(a,b): return np.asarray(a) @ np.asarray(b)
def point(T,p): return (np.asarray(T) @ np.r_[p,1.0])[:3]

def interpolate_pose(t0,T0,t1,T1,t):
    if not t0 <= t <= t1 or t1 <= t0: raise ValueError("invalid interpolation time")
    u=(t-t0)/(t1-t0)
    rotations=Rotation.from_matrix(np.stack([T0[:3,:3],T1[:3,:3]]))
    out=np.eye(4); out[:3,:3]=Slerp([t0,t1],rotations)([t]).as_matrix()[0]
    out[:3,3]=(1-u)*T0[:3,3]+u*T1[:3,3]
    return out

