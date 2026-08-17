from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
import numpy as np

from .mapping import EXPECTED_NODES
from .types import ImuSample, SEGMENTS


# Independent scalar-first quaternion oracle. It intentionally does not import
# production SO(3) helpers.
def _qn(q): return q/np.linalg.norm(q,axis=-1,keepdims=True)
def _qm(a,b):
    aw,ax,ay,az=np.moveaxis(a,-1,0); bw,bx,by,bz=np.moveaxis(b,-1,0)
    return np.stack((aw*bw-ax*bx-ay*by-az*bz,aw*bx+ax*bw+ay*bz-az*by,
                     aw*by-ax*bz+ay*bw+az*bx,aw*bz+ax*by-ay*bx+az*bw),axis=-1)
def _qi(q):
    x=_qn(q).copy();x[...,1:]*=-1;return x
def _qe(v):
    v=np.asarray(v,float); a=np.linalg.norm(v,axis=-1,keepdims=True); h=a/2
    s=np.empty_like(a);np.divide(np.sin(h),a,out=s,where=a>1e-12);s=np.where(a>1e-12,s,.5)
    return _qn(np.concatenate((np.cos(h),s*v),axis=-1))
def _ql(q):
    q=_qn(q);q=np.where(q[...,:1]<0,-q,q);v=q[...,1:];n=np.linalg.norm(v,axis=-1,keepdims=True)
    a=2*np.arctan2(n,np.clip(q[...,:1],-1,1));scale=np.empty_like(n);np.divide(a,n,out=scale,where=n>1e-12)
    return np.where(n>1e-12,scale*v,2*v)
def _R(q):
    w,x,y,z=np.moveaxis(_qn(q),-1,0)
    return np.stack((1-2*(y*y+z*z),2*(x*y-z*w),2*(x*z+y*w),2*(x*y+z*w),
                     1-2*(x*x+z*z),2*(y*z-x*w),2*(x*z-y*w),2*(y*z+x*w),
                     1-2*(x*x+y*y)),axis=-1).reshape(q.shape[:-1]+(3,3))


TOPOLOGY = {
    "pelvis":None,"torso":"pelvis","thigh_left":"pelvis","shank_left":"thigh_left",
    "thigh_right":"pelvis","shank_right":"thigh_right","upper_arm_left":"torso",
    "forearm_left":"upper_arm_left","upper_arm_right":"torso","forearm_right":"upper_arm_right",
}


@dataclass(frozen=True)
class SyntheticDataset:
    samples_by_node: dict[str,list[ImuSample]]
    truth_time_s: np.ndarray
    truth_q_W_S: dict[str,np.ndarray]
    q_I_S: dict[str,np.ndarray]
    gyro_bias: dict[str,np.ndarray]
    accel_bias: dict[str,np.ndarray]
    mapping: dict[str,str]
    events: tuple[str,...]


def generate(seed: int=1, duration_s: float=22.0, rate_hz: float=100.0,
             noise: bool=True, irregular: bool=True, gaps: bool=True,
             biases: bool=True, transients: bool=True, outliers: bool=True) -> SyntheticDataset:
    rng=np.random.default_rng(seed); n=int(duration_s*rate_hz)+1
    if irregular:
        t=np.cumsum(np.r_[0.,1/rate_hz+rng.uniform(-.0015,.0015,n-1)])
    else:
        t=np.arange(n)/rate_hz
    mapping=dict(zip(sorted(EXPECTED_NODES),SEGMENTS))
    # Preserve the real pelvis identity for layout-specific mutations.
    pelvis_node=next(k for k,v in mapping.items() if v=="pelvis")
    if pelvis_node!="BSFC2CC":
        other=mapping["BSFC2CC"]; mapping[pelvis_node]=other; mapping["BSFC2CC"]="pelvis"
    local={s:np.tile([1.,0,0,0],(n,1)) for s in SEGMENTS}
    phase=np.clip((t-2)/2,0,1)
    local["torso"]=_qe(phase[:,None]*np.column_stack((.25*np.sin(.5*t),.10*np.sin(.33*t),.35*np.sin(.27*t))))
    for side,sign in (("left",1),("right",-1)):
        local[f"upper_arm_{side}"]=_qe(phase[:,None]*np.column_stack((.35*np.sin(.7*t+sign), sign*1.2+.45*np.sin(.31*t), .25*np.sin(.41*t))))
        local[f"forearm_{side}"]=_qe(phase[:,None]*np.column_stack((.75+.65*np.sin(.9*t+sign),np.zeros(n),.08*np.sin(.2*t))))
        local[f"thigh_{side}"]=_qe(phase[:,None]*np.column_stack((.55*np.sin(.62*t+sign*.3),.12*np.sin(.23*t),.16*np.sin(.37*t+sign))))
        local[f"shank_{side}"]=_qe(phase[:,None]*np.column_stack((.65+.55*np.sin(.62*t+sign*.3),np.zeros(n),.05*np.sin(.2*t))))
    root=_qe(phase[:,None]*np.column_stack((.10*np.sin(.19*t),.08*np.sin(.23*t),.45*np.sin(.08*t))))
    truth={"pelvis":root}
    for segment in ("torso","thigh_left","shank_left","thigh_right","shank_right",
                    "upper_arm_left","forearm_left","upper_arm_right","forearm_right"):
        truth[segment]=_qn(_qm(truth[TOPOLOGY[segment]],local[segment]))
    q_I_S={};bg={};ba={};samples={}
    mount_rot={s:rng.normal(0,.35,3) for s in SEGMENTS}
    mount_rot["forearm_left"]+=np.array([.2,-.4,.6]);mount_rot["forearm_right"]+=np.array([-.3,.35,-.55])
    mount_rot["pelvis"]+=np.array([.65,-.25,.35])
    for node,segment in mapping.items():
        q_I_S[node]=_qe(mount_rot[segment]); q_WI=_qn(_qm(truth[segment],_qi(q_I_S[node])))
        ts=t
        omega=np.zeros((n,3)); omega[1:]=_ql(_qm(_qi(q_WI[:-1]),q_WI[1:]))/np.diff(ts)[:,None]
        bg[node]=rng.uniform(-.018,.018,3) if biases else np.zeros(3)
        ba[node]=rng.uniform(-.20,.20,3) if biases else np.zeros(3)
        accel=np.einsum("nij,j->ni",np.swapaxes(_R(q_WI),1,2),np.array([0,0,9.80665]))
        # Short bounded world-frame linear-acceleration transients.
        transient=np.zeros((n,3)); mask=(t>11)&(t<12.5)
        if transients: transient[mask,0]=1.2*np.sin((t[mask]-11)*4*np.pi)
        accel+=np.einsum("nij,nj->ni",np.swapaxes(_R(q_WI),1,2),transient)
        gyro=omega+bg[node];accel=accel+ba[node]
        if noise:
            gyro+=rng.normal(0,.004,gyro.shape);accel+=rng.normal(0,.06,accel.shape)
            if outliers:
                spike=np.arange(500,n,719);gyro[spike]=np.clip(gyro[spike]+rng.normal(0,2,(len(spike),3)),-8,8)
                accel[spike]=np.clip(accel[spike]+rng.normal(0,15,(len(spike),3)),-60,60)
        keep=np.ones(n,bool)
        if gaps: keep[(t>14.0)&(t<14.18)]=False
        rows=[]
        for i in np.flatnonzero(keep):
            age=float(rng.uniform(0,.005));rows.append(ImuSample(node,float(ts[i]),int(round(t[i]*1e6)),i,gyro[i],accel[i],age,0,bool(t[i]<1.8)))
        if noise and outliers and len(rows)>800: rows.insert(800,rows[799])
        samples[node]=rows
    return SyntheticDataset(samples,t,truth,q_I_S,bg,ba,mapping,
                            ("neutral","T_pose","bilateral_shoulders_elbows_hips_knees","squat",
                             "trunk_flexion_rotation","walking_like","linear_acceleration","gap_duplicate_spike"))
