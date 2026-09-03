"""Q2_IMU_MOCAP_ATTITUDE: native-rate, six-axis IMU attitude frontend."""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Mapping

import numpy as np

from biospur_fusion.imu.q1 import (
    quaternion_exp,
    quaternion_from_two_vectors,
    quaternion_multiply,
    quaternion_normalize,
    quaternion_to_matrix,
)


@dataclass
class PreparedImu:
    time_ns: np.ndarray
    accel_mps2: np.ndarray
    accel_filtered_mps2: np.ndarray
    gyro_rad_s: np.ndarray
    jerk_mps3: np.ndarray
    candidate_stationary: np.ndarray
    confirmed_stationary: np.ndarray
    agreement_fraction: np.ndarray
    preliminary_gyro_bias_rad_s: np.ndarray
    duplicate_timestamps_rejected: int


@dataclass
class Q2Result:
    time_ns: np.ndarray
    q_wxyz: np.ndarray
    covariance_rad2: np.ndarray
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


def _prepare_one(
    imu: np.ndarray,
    cfg: Mapping[str, float],
    initial_window: tuple[int, int] | None,
) -> PreparedImu:
    valid=np.flatnonzero(imu["status"]==1);order=valid[np.argsort(imu["global_time_ns"][valid],kind="stable")]
    times=imu["global_time_ns"][order].astype(np.int64);unique=np.r_[True,np.diff(times)>0];duplicate_count=int((~unique).sum());order=order[unique];times=times[unique]
    accel=imu["acc_raw"][order].astype(float)/float(cfg["accel_lsb_per_g"])*float(cfg["gravity_mps2"])
    gyro=np.deg2rad(imu["gyro_raw"][order].astype(float)/float(cfg["gyro_lsb_per_dps"]));filtered=np.empty_like(accel);filtered[0]=accel[0];jerk=np.zeros(len(times));tau=float(cfg["stationary_filter_time_constant_s"])
    for i in range(1,len(times)):
        dt=max(1e-6,(int(times[i])-int(times[i-1]))/1e9);alpha=dt/(tau+dt);filtered[i]=filtered[i-1]+alpha*(accel[i]-filtered[i-1]);jerk[i]=np.linalg.norm(filtered[i]-filtered[i-1])/dt
    # JY61P capture-day zero-rate offsets can be much larger than the residual
    # stationary threshold.  Use an initial-window median only to bootstrap the
    # detector; the reported/frozen bias is still estimated from samples that
    # pass the complete multi-node stationary confirmation below.
    if initial_window is None:
        initial=np.ones(len(times),bool)
    else:
        initial=(times>=initial_window[0])&(times<=initial_window[1])
    preliminary_bias=np.median(gyro[initial],axis=0) if np.any(initial) else np.median(gyro,axis=0)
    gyro_dps=np.linalg.norm(np.rad2deg(gyro-preliminary_bias),axis=1);adev=np.abs(np.linalg.norm(filtered,axis=1)/float(cfg["gravity_mps2"])-1.)
    candidate=(gyro_dps<=float(cfg["stationary_gyro_limit_dps"]))&(adev<=float(cfg["stationary_accel_norm_deviation_g"]))&(jerk<=float(cfg["stationary_filtered_jerk_limit_mps3"]))
    return PreparedImu(times,accel,filtered,gyro,jerk,candidate,np.zeros(len(times),bool),np.zeros(len(times)),preliminary_bias,duplicate_count)


def prepare_stationarity(
    imus: Mapping[str, np.ndarray],
    cfg: Mapping[str, float],
    initial_window: tuple[int, int] | None = None,
) -> dict[str, PreparedImu]:
    prepared={node:_prepare_one(imu,cfg,initial_window) for node,imu in sorted(imus.items())};origin=min(int(x.time_ns[0]) for x in prepared.values());bin_ns=int(round(float(cfg["agreement_bin_s"])*1e9));per_node={};max_bin=0
    for node,x in prepared.items():
        bins=((x.time_ns-origin)//bin_ns).astype(int);max_bin=max(max_bin,int(bins[-1]));count=np.bincount(bins,minlength=int(bins[-1])+1);positive=np.bincount(bins,weights=x.candidate_stationary.astype(float),minlength=int(bins[-1])+1);per_node[node]=(bins,positive/np.maximum(count,1))
    node_vote=np.zeros((len(prepared),max_bin+1),bool)
    for row,node in enumerate(sorted(prepared)):
        _,fraction=per_node[node];node_vote[row,:len(fraction)]=fraction>=.5
    agreement=node_vote.mean(axis=0)
    for node,x in prepared.items():
        bins=per_node[node][0];pre=x.candidate_stationary&(agreement[bins]>=float(cfg["multi_node_agreement_fraction"]));x.confirmed_stationary=_runs_at_least(pre,x.time_ns/1e9,float(cfg["minimum_stationary_duration_s"]));x.agreement_fraction=agreement[bins]
    return prepared


def _angle_between_quaternions(q0: np.ndarray, q1: np.ndarray) -> float:
    dot=abs(float(np.asarray(q0)@np.asarray(q1)));return 2.*math.acos(min(1.,max(-1.,dot)))


def run_q2_node(node: str, prepared: PreparedImu, initial_window: tuple[int,int], cfg: Mapping[str,float], *, disable_accel_correction: bool=False) -> Q2Result:
    t=prepared.time_ns;a=prepared.accel_mps2;af=prepared.accel_filtered_mps2;gyr=prepared.gyro_rad_s;initial=(t>=initial_window[0])&(t<=initial_window[1]);eligible=initial&prepared.confirmed_stationary
    fallback=initial if int(eligible.sum())<20 else eligible;bias=np.mean(gyr[fallback],axis=0);accel_mean=np.mean(af[fallback],axis=0);q=quaternion_from_two_vectors(accel_mean,np.array([0.,0.,1.]));initial_q=q.copy();n=len(t);qs=np.empty((n,4));cov=np.empty((n,3,3));gc=np.empty_like(gyr);accepted=np.zeros(n,bool);gaps=np.zeros(n,bool)
    tilt=math.radians(float(cfg["initial_tilt_sigma_deg"]));yaw=math.radians(float(cfg["yaw_sigma_deg"]));P=np.diag([tilt*tilt,tilt*tilt,yaw*yaw]);max_norm=0.;gravity_rejected=0;bias_updates=0;gap_count=0
    for i in range(n):
        dt=0. if i==0 else (int(t[i])-int(t[i-1]))/1e9
        omega=gyr[i]-bias;gc[i]=omega
        if i and (dt<=0 or dt>float(cfg["maximum_propagated_gap_s"])):gaps[i]=True;gap_count+=1;dt=0.
        if dt>0:
            q=quaternion_normalize(quaternion_multiply(q,quaternion_exp(omega*dt)),q);noise=float(cfg["gyro_noise_sigma_rad_s_sqrt_hz"]);rw=float(cfg["gyro_bias_rw_sigma_rad_s2_sqrt_hz"]);P=P+np.eye(3)*(noise*noise*dt+rw*rw*dt**3/3.)
        norm_dev=abs(float(np.linalg.norm(af[i]))/float(cfg["gravity_mps2"])-1.);gyro_dps=float(np.linalg.norm(np.rad2deg(omega)));dynamic=(norm_dev<=float(cfg["dynamic_gravity_accel_norm_deviation_g"]) and gyro_dps<=float(cfg["dynamic_gravity_gyro_limit_dps"]) and prepared.jerk_mps3[i]<=float(cfg["dynamic_gravity_jerk_limit_mps3"]));use_gravity=prepared.confirmed_stationary[i] or dynamic
        if use_gravity and not disable_accel_correction and np.linalg.norm(af[i])>1e-9:
            measured=af[i]/np.linalg.norm(af[i]);predicted=quaternion_to_matrix(q).T@np.array([0.,0.,1.]);error=np.cross(measured,predicted);gain=float(cfg["stationary_gravity_correction_gain"] if prepared.confirmed_stationary[i] else cfg["dynamic_gravity_correction_gain"]);q=quaternion_normalize(quaternion_multiply(q,quaternion_exp(gain*error)),q);P[:2,:2]*=max(.05,1.-gain);accepted[i]=True
        else:gravity_rejected+=1
        if prepared.confirmed_stationary[i]:
            alpha=float(cfg["stationary_bias_update_gain"]);bias=(1.-alpha)*bias+alpha*gyr[i];bias_updates+=1
        qs[i]=q;cov[i]=P;max_norm=max(max_norm,abs(float(np.linalg.norm(q))-1.))
    initial_indices=np.flatnonzero(initial);drift=max((_angle_between_quaternions(initial_q,qs[i]) for i in initial_indices),default=math.nan);dt=np.diff(t)/1e9;positive=dt[dt>0];rate=1./float(np.median(positive)) if len(positive) else 0.;fraction=float(eligible.sum()/max(1,initial.sum()));acc_norm=np.linalg.norm(a[initial],axis=1)
    audit={"frontend":"Q2_IMU_MOCAP_ATTITUDE","node":node,"sample_count":int(n),"effective_rate_hz":rate,"duplicate_timestamps_rejected":prepared.duplicate_timestamps_rejected,"gap_boundaries":gap_count,"maximum_gap_s":float(np.max(positive)) if len(positive) else 0.,"preliminary_stationarity_gyro_bias_rad_s":prepared.preliminary_gyro_bias_rad_s.tolist(),"gyro_bias_rad_s":bias.tolist(),"gyro_bias_uncertainty_rad_s":(np.std(gyr[fallback],axis=0)/math.sqrt(max(1,int(fallback.sum())))).tolist(),"accel_norm_mean_mps2":float(np.mean(acc_norm)),"accel_norm_std_mps2":float(np.std(acc_norm)),"confirmed_initial_still_fraction":fraction,"gravity_updates_accepted":int(accepted.sum()),"gravity_updates_rejected_or_ineligible":int(gravity_rejected),"stationary_bias_updates":int(bias_updates),"orientation_covariance_final_rad2":P.tolist(),"initial_still_attitude_drift_rad":float(drift),"finite":bool(np.isfinite(qs).all() and np.isfinite(cov).all()),"maximum_quaternion_norm_error":max_norm,"absolute_heading_observed":False,"yaw_gauge":"DETERMINISTIC_DISPLAY_ZERO"}
    return Q2Result(t,qs,cov,gc,prepared.confirmed_stationary.copy(),accepted,gaps,bias.copy(),audit)


def run_q2_frontend(imus: Mapping[str,np.ndarray], action_windows: Mapping[str,tuple[int,int]], cfg: Mapping[str,float]) -> tuple[dict[str,Q2Result],dict]:
    initial=action_windows["initial_still_attempt2"];prepared=prepare_stationarity(imus,cfg,initial);results={node:run_q2_node(node,x,initial,cfg) for node,x in prepared.items()};failed=[node for node,r in results.items() if r.audit["confirmed_initial_still_fraction"]<float(cfg["minimum_initial_still_eligible_fraction"])]
    audit={"schema":"biospur-q2-imu-mocap-attitude-audit-v1","frontend":"Q2_IMU_MOCAP_ATTITUDE","nodes":{node:r.audit for node,r in sorted(results.items())},"minimum_initial_still_eligible_fraction":float(cfg["minimum_initial_still_eligible_fraction"]),"failed_nodes":failed,"verdict":"FAIL_IMU_STATIONARY_CALIBRATION" if failed else "PASS"}
    return results,audit
