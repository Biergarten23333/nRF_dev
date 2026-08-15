"""Q2 six-axis attitude frontend adapted from the audited local Phase-A code.

The adaptation keeps the Phase-A propagation and gravity-update equations but
lives in an independent namespace. No synthetic truth, UWB, action-specific
pose correction, or per-action reset is accepted here.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Mapping

import numpy as np

from biospur_fusion.imu.q1 import (
    quaternion_exp, quaternion_from_two_vectors, quaternion_multiply,
    quaternion_normalize, quaternion_to_matrix,
)


@dataclass
class PreparedImu:
    time_ns: np.ndarray
    boot_epoch: np.ndarray
    accel_mps2: np.ndarray
    accel_filtered_mps2: np.ndarray
    gyro_rad_s: np.ndarray
    jerk_mps3: np.ndarray
    candidate_stationary: np.ndarray
    confirmed_stationary: np.ndarray
    agreement_fraction: np.ndarray
    preliminary_gyro_bias_rad_s: np.ndarray
    input_total: int
    status_rejected: int
    duplicate_timestamps_rejected: int


@dataclass
class Q2Result:
    time_ns: np.ndarray
    boot_epoch: np.ndarray
    q_wxyz: np.ndarray
    covariance_rad2: np.ndarray
    accel_mps2: np.ndarray
    gyro_corrected_rad_s: np.ndarray
    stationary: np.ndarray
    gravity_accepted: np.ndarray
    gap_boundary: np.ndarray
    bias_rad_s: np.ndarray
    audit: dict


def _runs_at_least(mask: np.ndarray, times_s: np.ndarray, minimum_s: float) -> np.ndarray:
    keep=np.zeros(len(mask),bool);start=None
    for i,value in enumerate(mask):
        if value and start is None:start=i
        end=(not value and start is not None) or (value and start is not None and i==len(mask)-1)
        if end:
            stop=i if value and i==len(mask)-1 else i-1
            if stop>=start and times_s[stop]-times_s[start]>=minimum_s:keep[start:stop+1]=True
            start=None
    return keep


def prepare_one(imu: np.ndarray, cfg: Mapping[str,float], initial_window: tuple[int,int]) -> PreparedImu:
    accepted=np.flatnonzero(imu["status"]==1)
    order=accepted[np.argsort(imu["global_time_ns"][accepted],kind="stable")]
    times_all=imu["global_time_ns"][order].astype(np.int64)
    unique=np.r_[True,np.diff(times_all)>0]
    duplicates=int((~unique).sum());order=order[unique];times=times_all[unique]
    if len(times)<2:raise ValueError("insufficient accepted IMU samples")
    accel=imu["acc_raw"][order].astype(float)/float(cfg["accel_lsb_per_g"])*float(cfg["gravity_mps2"])
    gyro=np.deg2rad(imu["gyro_raw"][order].astype(float)/float(cfg["gyro_lsb_per_dps"]))
    boot=imu["boot_epoch"][order].astype(np.int64) if "boot_epoch" in imu.dtype.names else np.zeros(len(order),np.int64)
    filtered=np.empty_like(accel);filtered[0]=accel[0];jerk=np.zeros(len(times));tau=float(cfg["stationary_filter_time_constant_s"])
    for i in range(1,len(times)):
        dt=max(1e-6,(int(times[i])-int(times[i-1]))/1e9);alpha=dt/(tau+dt)
        filtered[i]=filtered[i-1]+alpha*(accel[i]-filtered[i-1]);jerk[i]=np.linalg.norm(filtered[i]-filtered[i-1])/dt
    initial=(times>=initial_window[0])&(times<=initial_window[1])
    preliminary=np.median(gyro[initial],axis=0) if np.any(initial) else np.median(gyro,axis=0)
    gyro_dps=np.linalg.norm(np.rad2deg(gyro-preliminary),axis=1)
    adev=np.abs(np.linalg.norm(filtered,axis=1)/float(cfg["gravity_mps2"])-1.)
    candidate=(gyro_dps<=float(cfg["stationary_gyro_limit_dps"]))&(adev<=float(cfg["stationary_accel_norm_deviation_g"]))&(jerk<=float(cfg["stationary_filtered_jerk_limit_mps3"]))
    return PreparedImu(times,boot,accel,filtered,gyro,jerk,candidate,np.zeros(len(times),bool),np.zeros(len(times)),preliminary,len(imu),int(len(imu)-len(accepted)),duplicates)


def prepare_stationarity(imus: Mapping[str,np.ndarray], cfg: Mapping[str,float], initial_window: tuple[int,int]) -> dict[str,PreparedImu]:
    prepared={n:prepare_one(v,cfg,initial_window) for n,v in sorted(imus.items())}
    origin=min(int(x.time_ns[0]) for x in prepared.values());bin_ns=int(round(float(cfg["agreement_bin_s"])*1e9));per={};max_bin=0
    for node,x in prepared.items():
        bins=((x.time_ns-origin)//bin_ns).astype(int);max_bin=max(max_bin,int(bins[-1]));count=np.bincount(bins,minlength=int(bins[-1])+1);positive=np.bincount(bins,weights=x.candidate_stationary.astype(float),minlength=int(bins[-1])+1);per[node]=(bins,positive/np.maximum(count,1))
    votes=np.zeros((len(prepared),max_bin+1),bool)
    for row,node in enumerate(sorted(prepared)):
        _,fraction=per[node];votes[row,:len(fraction)]=fraction>=.5
    agreement=votes.mean(axis=0)
    for node,x in prepared.items():
        bins=per[node][0];pre=x.candidate_stationary&(agreement[bins]>=float(cfg["multi_node_agreement_fraction"]));x.confirmed_stationary=_runs_at_least(pre,x.time_ns/1e9,float(cfg["minimum_stationary_duration_s"]));x.agreement_fraction=agreement[bins]
    return prepared


def run_q2_node(node: str, p: PreparedImu, initial_window: tuple[int,int], cfg: Mapping[str,float]) -> Q2Result:
    t=p.time_ns;initial=(t>=initial_window[0])&(t<=initial_window[1]);eligible=initial&p.confirmed_stationary;fallback=initial if int(eligible.sum())<20 else eligible
    bias=np.mean(p.gyro_rad_s[fallback],axis=0);accel_mean=np.mean(p.accel_filtered_mps2[fallback],axis=0)
    q=quaternion_from_two_vectors(accel_mean,np.array([0.,0.,1.]));n=len(t);qs=np.empty((n,4));cov=np.empty((n,3,3));gc=np.empty_like(p.gyro_rad_s);accepted=np.zeros(n,bool);gaps=np.zeros(n,bool)
    tilt=math.radians(float(cfg["initial_tilt_sigma_deg"]));yaw=math.radians(float(cfg["yaw_sigma_deg"]));P=np.diag([tilt*tilt,tilt*tilt,yaw*yaw]);gap_count=0;bias_updates=0
    for i in range(n):
        dt=0. if i==0 else (int(t[i])-int(t[i-1]))/1e9;omega=p.gyro_rad_s[i]-bias;gc[i]=omega
        if i and (dt<=0 or dt>float(cfg["maximum_propagated_gap_s"]) or p.boot_epoch[i]!=p.boot_epoch[i-1]):gaps[i]=True;gap_count+=1;dt=0.
        if dt>0:
            q=quaternion_normalize(quaternion_multiply(q,quaternion_exp(omega*dt)),q);noise=float(cfg["gyro_noise_sigma_rad_s_sqrt_hz"]);rw=float(cfg["gyro_bias_rw_sigma_rad_s2_sqrt_hz"]);P=P+np.eye(3)*(noise*noise*dt+rw*rw*dt**3/3.)
        norm_dev=abs(float(np.linalg.norm(p.accel_filtered_mps2[i]))/float(cfg["gravity_mps2"])-1.);gyro_dps=float(np.linalg.norm(np.rad2deg(omega)));dynamic=norm_dev<=float(cfg["dynamic_gravity_accel_norm_deviation_g"]) and gyro_dps<=float(cfg["dynamic_gravity_gyro_limit_dps"]) and p.jerk_mps3[i]<=float(cfg["dynamic_gravity_jerk_limit_mps3"])
        if (p.confirmed_stationary[i] or dynamic) and np.linalg.norm(p.accel_filtered_mps2[i])>1e-9:
            measured=p.accel_filtered_mps2[i]/np.linalg.norm(p.accel_filtered_mps2[i]);predicted=quaternion_to_matrix(q).T@np.array([0.,0.,1.]);error=np.cross(measured,predicted);gain=float(cfg["stationary_gravity_correction_gain"] if p.confirmed_stationary[i] else cfg["dynamic_gravity_correction_gain"]);q=quaternion_normalize(quaternion_multiply(q,quaternion_exp(gain*error)),q);P[:2,:2]*=max(.05,1.-gain);accepted[i]=True
        if p.confirmed_stationary[i]:
            alpha=float(cfg["stationary_bias_update_gain"]);bias=(1.-alpha)*bias+alpha*p.gyro_rad_s[i];bias_updates+=1
        qs[i]=q;cov[i]=P
    dt=np.diff(t)/1e9;positive=dt[dt>0];fraction=float(eligible.sum()/max(1,initial.sum()))
    audit={"frontend":"Q2_IMU_MOCAP_ATTITUDE","node":node,"input_total":p.input_total,"input_status_rejected":p.status_rejected,"accepted_unique_samples":int(n),"duplicate_timestamps_rejected":p.duplicate_timestamps_rejected,"source_accounting_closed":p.input_total==p.status_rejected+p.duplicate_timestamps_rejected+n,"effective_rate_hz":1./float(np.median(positive)) if len(positive) else 0.,"gap_boundaries":gap_count,"confirmed_initial_still_fraction":fraction,"gyro_bias_rad_s":bias.tolist(),"gyro_bias_uncertainty_rad_s":(np.std(p.gyro_rad_s[fallback],axis=0)/math.sqrt(max(1,int(fallback.sum())))).tolist(),"stationary_bias_updates":bias_updates,"finite":bool(np.isfinite(qs).all()),"absolute_heading_observed":False}
    return Q2Result(t,p.boot_epoch,qs,cov,p.accel_mps2,gc,p.confirmed_stationary.copy(),accepted,gaps,bias.copy(),audit)


def run_q2_frontend(imus: Mapping[str,np.ndarray], windows: Mapping[str,tuple[int,int]], cfg: Mapping[str,float]) -> tuple[dict[str,Q2Result],dict]:
    initial=windows["initial_still_attempt2"];prepared=prepare_stationarity(imus,cfg,initial);results={node:run_q2_node(node,x,initial,cfg) for node,x in prepared.items()};failed=[n for n,r in results.items() if r.audit["confirmed_initial_still_fraction"]<float(cfg["minimum_initial_still_eligible_fraction"])]
    return results,{"schema":"biospur-q2-preview-audit-v0","source_phase_a_sha256":"1569bf381668ec6391ba78c74ad90aeaec0b498d24bff447bdc21b7ebef606b8","nodes":{n:r.audit for n,r in sorted(results.items())},"failed_nodes":failed,"verdict":"FAIL_PREVIEW_CALIBRATION" if failed else "PASS_ENGINEERING_FRONTEND"}
