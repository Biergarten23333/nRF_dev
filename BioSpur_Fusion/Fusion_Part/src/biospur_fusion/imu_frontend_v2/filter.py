from __future__ import annotations
from dataclasses import dataclass,field
import numpy as np
from .so3 import q_from_two_vectors,q_to_R,qexp,qmul,qnormalize,skew
from .timebase import native_dt_seconds
G=9.80665
@dataclass(frozen=True)
class FrontendConfig:
 gyro_noise:float=0.012;gyro_bias_rw:float=0.0005;accel_bias_rw:float=0.0002
 gravity_sigma:float=0.08;gravity_norm_full:float=0.35;gravity_norm_zero:float=2.5
 gyro_full:float=0.35;gyro_zero:float=2.0;jerk_full:float=12.;jerk_zero:float=80.
 max_observed_dt:float=0.02;gravity_stride:int=5;sample_age_support_us:tuple=(0,5000)
def ramp(value,full,zero):return float(np.clip((zero-value)/(zero-full),0,1))
@dataclass
class ImuFrontend:
 node:str;boot_epoch:int;cfg:FrontendConfig=field(default_factory=FrontendConfig)
 q:np.ndarray=field(default_factory=lambda:np.array([1.,0,0,0]));bg:np.ndarray=field(default_factory=lambda:np.zeros(3));ba:np.ndarray=field(default_factory=lambda:np.zeros(3))
 P:np.ndarray=field(default_factory=lambda:np.diag([0.1]*3+[0.03]*3+[1.0]*3))
 last_timer_us:int|None=None;last_acc:np.ndarray|None=None;sample_count:int=0;propagation_updates:int=0;gravity_accepted:int=0;gravity_rejected:int=0;bias_process_updates:int=0;gap_events:int=0;gap_only_events:int=0
 min_dt:float=float("inf");max_dt:float=0.;stillness_score:float=0.;timing_sensitivity_rad:float=0.;first_common_ns:int|None=None;last_common_ns:int|None=None
 def initialize(self,acc):
  a=np.asarray(acc,float)-self.ba
  if np.linalg.norm(a)<1e-6:raise ValueError("invalid initial gravity")
  self.q=q_from_two_vectors(a,[0,0,G])
 def step(self,acc,gyro,timer_us,common_ns,sample_age_support_us=(0,5000),valid=True):
  acc=np.asarray(acc,float);gyro=np.asarray(gyro,float)
  if not valid or not np.all(np.isfinite(acc)) or not np.all(np.isfinite(gyro)):return {"valid":False,"reason":"INVALID_INPUT"}
  if self.sample_count==0:self.initialize(acc);self.first_common_ns=int(common_ns)
  dt=None
  if self.last_timer_us is not None:
   dt=native_dt_seconds(timer_us,self.last_timer_us);self.min_dt=min(self.min_dt,dt);self.max_dt=max(self.max_dt,dt)
   if dt<=self.cfg.max_observed_dt:
    Q=np.diag([self.cfg.gyro_noise**2]*3+[self.cfg.gyro_bias_rw**2]*3+[self.cfg.accel_bias_rw**2]*3)*dt
    F=np.eye(9);F[:3,:3]-=skew(gyro-self.bg)*dt;F[:3,3:6]=-np.eye(3)*dt
    self.P=F@self.P@F.T+Q
    self.q=qnormalize(qmul(self.q,qexp((gyro-self.bg)*dt)));self.propagation_updates+=1
   else:
    self.gap_events+=1;self.gap_only_events+=1
    self.P[:3,:3]+=np.eye(3)*min((self.cfg.gyro_noise*dt+0.05)**2,100.)
    self.P[3:6,3:6]+=np.eye(3)*self.cfg.gyro_bias_rw**2*dt
    self.P[6:9,6:9]+=np.eye(3)*self.cfg.accel_bias_rw**2*dt
   self.bias_process_updates+=1
  corrected=acc-self.ba;norm=float(np.linalg.norm(corrected));jerk=0. if self.last_acc is None or not dt else float(np.linalg.norm(acc-self.last_acc)/max(dt,1e-9))
  weight=ramp(abs(norm-G),self.cfg.gravity_norm_full,self.cfg.gravity_norm_zero)*ramp(float(np.linalg.norm(gyro)),self.cfg.gyro_full,self.cfg.gyro_zero)*ramp(jerk,self.cfg.jerk_full,self.cfg.jerk_zero)
  self.stillness_score=weight
  if self.sample_count%self.cfg.gravity_stride==0 and weight>1e-4 and norm>1e-6:
   pred=q_to_R(self.q).T@np.array([0.,0.,1.]);meas=corrected/norm;res=np.cross(pred,meas)
   H=np.zeros((3,9));H[:,:3]=-skew(pred);H[:,6:9]=-(np.eye(3)-np.outer(meas,meas))/norm
   Rm=np.eye(3)*(self.cfg.gravity_sigma/max(weight,1e-3))**2;S=H@self.P@H.T+Rm;S=(S+S.T)/2
   minimum=float(np.linalg.eigvalsh(S).min())
   if not np.isfinite(minimum):raise FloatingPointError("nonfinite gravity innovation covariance")
   if minimum<1e-12:S+=np.eye(3)*(1e-12-minimum)
   K=np.linalg.solve(S,(self.P@H.T).T).T
   K[3:9,:]=0.0
   dx=K@res
   if np.linalg.norm(dx[:3])>0.25:
    self.gravity_rejected+=1
    dx=None
   if dx is not None:
    prior_yaw=float(pred@self.P[:3,:3]@pred);self.q=qnormalize(qmul(self.q,qexp(dx[:3])));self.bg+=dx[3:6];self.ba+=dx[6:9]
    I=np.eye(9);self.P=(I-K@H)@self.P@(I-K@H).T+K@Rm@K.T;post_yaw=float(pred@self.P[:3,:3]@pred)
    if post_yaw<prior_yaw:self.P[:3,:3]+=np.outer(pred,pred)*(prior_yaw-post_yaw)
    self.gravity_accepted+=1
  else:self.gravity_rejected+=1
  self.P=(self.P+self.P.T)/2;self.last_timer_us=int(timer_us);self.last_common_ns=int(common_ns);self.last_acc=acc;self.sample_count+=1
  age_max=max(sample_age_support_us)*1e-6;self.timing_sensitivity_rad=max(self.timing_sensitivity_rad,float(np.linalg.norm(gyro-self.bg))*age_max)
  return {"valid":True,"q":self.q.copy(),"bg":self.bg.copy(),"ba":self.ba.copy(),"P":self.P.copy(),"stillness_score":weight,"timing_sensitivity_rad":self.timing_sensitivity_rad}
 def summary(self):
  eig=np.linalg.eigvalsh(self.P)
  return {"hardware_node_id":self.node,"boot_epoch":self.boot_epoch,"samples":self.sample_count,"propagation_updates":self.propagation_updates,"gravity_accepted":self.gravity_accepted,"gravity_rejected":self.gravity_rejected,"bias_process_updates":self.bias_process_updates,"gap_events":self.gap_events,"gap_only_events":self.gap_only_events,"dt_min_s":None if self.min_dt==float('inf') else self.min_dt,"dt_max_s":self.max_dt,"quaternion_norm_error":abs(float(np.linalg.norm(self.q))-1),"bg_radps":self.bg.tolist(),"ba_mps2":self.ba.tolist(),"covariance_min_eigenvalue":float(eig.min()),"covariance_trace":float(np.trace(self.P)),"timing_sensitivity_rad":self.timing_sensitivity_rad,"yaw_gauge_id":f"YAW_GAUGE_{self.node}_BOOT_{self.boot_epoch}","b_a_observability":"WEAK_PRIOR_DOMINATED","logical_role":None,"mapping_status":"UNASSIGNED"}
