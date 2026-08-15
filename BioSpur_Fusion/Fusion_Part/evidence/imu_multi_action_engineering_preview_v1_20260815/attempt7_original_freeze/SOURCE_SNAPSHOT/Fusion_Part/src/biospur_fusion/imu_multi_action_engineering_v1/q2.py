"""Human quasi-static Q2 frontend for wearable motion capture.

Normal breathing, sway, muscle activity, and strap micro-motion are retained as
human motion. They contribute continuous confidence to bias/gravity evidence;
there is no robotic-still binary veto and no whole-window fallback.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any,Mapping

import numpy as np

from biospur_fusion.imu.q1 import (
    quaternion_exp,quaternion_from_two_vectors,quaternion_multiply,
    quaternion_normalize,quaternion_to_matrix,
)
from biospur_fusion.imu_preview_v0.q2 import Q2Result


@dataclass
class QuasiStaticPrepared:
    time_ns:np.ndarray
    boot_epoch:np.ndarray
    accel_mps2:np.ndarray
    accel_filtered_mps2:np.ndarray
    gyro_rad_s:np.ndarray
    jerk_mps3:np.ndarray
    gyro_change_rad_s2:np.ndarray
    local_weight:np.ndarray
    quasi_static_weight:np.ndarray
    input_total:int
    status_rejected:int
    duplicate_timestamps_rejected:int


def _cauchy(value:np.ndarray,scale:float)->np.ndarray:
    return 1./(1.+np.square(np.asarray(value,float)/float(scale)))


def _moving_average(values:np.ndarray,count:int)->np.ndarray:
    count=max(1,int(count));return np.convolve(values,np.ones(count)/count,mode="same")


def _decode_one(imu:np.ndarray,cfg:Mapping[str,float],initial:tuple[int,int])->QuasiStaticPrepared:
    accepted=np.flatnonzero(imu["status"]==1);order=accepted[np.argsort(imu["global_time_ns"][accepted],kind="stable")];times_all=imu["global_time_ns"][order].astype(np.int64);unique=np.r_[True,np.diff(times_all)>0];duplicates=int((~unique).sum());order=order[unique];times=times_all[unique]
    if len(times)<2:raise ValueError("insufficient accepted IMU samples")
    accel=imu["acc_raw"][order].astype(float)/float(cfg["accel_lsb_per_g"])*float(cfg["gravity_mps2"]);gyro=np.deg2rad(imu["gyro_raw"][order].astype(float)/float(cfg["gyro_lsb_per_dps"]));boot=imu["boot_epoch"][order].astype(np.int64) if "boot_epoch" in imu.dtype.names else np.zeros(len(order),np.int64)
    filtered=np.empty_like(accel);filtered[0]=accel[0];jerk=np.zeros(len(times));change=np.zeros(len(times));tau=float(cfg["filter_time_constant_s"])
    for i in range(1,len(times)):
        dt=max(1e-6,(int(times[i])-int(times[i-1]))/1e9);alpha=dt/(tau+dt);filtered[i]=filtered[i-1]+alpha*(accel[i]-filtered[i-1]);jerk[i]=np.linalg.norm(filtered[i]-filtered[i-1])/dt;change[i]=np.linalg.norm(gyro[i]-gyro[i-1])/dt
    center=np.median(gyro,axis=0);rate=np.linalg.norm(gyro-center,axis=1);accel_dev=np.abs(np.linalg.norm(filtered,axis=1)/float(cfg["gravity_mps2"])-1.)
    local=np.power(_cauchy(rate,math.radians(float(cfg["gyro_rate_scale_dps"])))*_cauchy(change,math.radians(float(cfg["gyro_change_scale_dps_per_s"])))*_cauchy(accel_dev,float(cfg["accel_norm_scale_g"]))*_cauchy(jerk,float(cfg["jerk_scale_mps3"])),.25);dt_med=float(np.median(np.diff(times)))/1e9;local=_moving_average(local,max(1,round(float(cfg["temporal_support_s"])/dt_med)))
    return QuasiStaticPrepared(times,boot,accel,filtered,gyro,jerk,change,local,np.zeros(len(times)),len(imu),int(len(imu)-len(accepted)),duplicates)


def prepare_quasi_static(imus:Mapping[str,np.ndarray],cfg:Mapping[str,float],initial:tuple[int,int])->dict[str,QuasiStaticPrepared]:
    prepared={node:_decode_one(value,cfg,initial) for node,value in sorted(imus.items())};origin=min(int(x.time_ns[0]) for x in prepared.values());bin_ns=int(round(float(cfg["cross_node_bin_s"])*1e9));fractions={};max_bin=0
    for node,item in prepared.items():
        bins=((item.time_ns-origin)//bin_ns).astype(int);max_bin=max(max_bin,int(bins[-1]));count=np.bincount(bins,minlength=int(bins[-1])+1);total=np.bincount(bins,weights=item.local_weight,minlength=int(bins[-1])+1);fractions[node]=(bins,total/np.maximum(count,1))
    support=np.zeros((len(prepared),max_bin+1))
    for row,node in enumerate(sorted(prepared)):
        value=fractions[node][1];support[row,:len(value)]=value
    common=np.median(support,axis=0);coupling=float(cfg["maximum_cross_node_confidence_influence"])
    for node,item in prepared.items():
        bins=fractions[node][0];multiplier=(1.-coupling)+coupling*np.sqrt(np.clip(common[bins],0.,1.));item.quasi_static_weight=np.clip(item.local_weight*multiplier,0.,1.)
    return prepared


def _weighted_quantile(values:np.ndarray,weights:np.ndarray,q:float)->float:
    order=np.argsort(values);v=np.asarray(values)[order];w=np.asarray(weights,float)[order];c=np.cumsum(w)
    return float(v[min(len(v)-1,int(np.searchsorted(c,float(q)*c[-1])))]) if c[-1]>0 else math.nan


def _effective_sample_size(weights:np.ndarray,times:np.ndarray,correlation_s:float)->float:
    weights=np.asarray(weights,float);kish=float(weights.sum()**2/max(1e-15,np.square(weights).sum()));positive=weights>0
    if not np.any(positive):return 0.
    duration=max(0.,(int(times[positive][-1])-int(times[positive][0]))/1e9);return min(kish,max(1.,duration/float(correlation_s)))


def _robust_bias(gyro:np.ndarray,weights:np.ndarray,times:np.ndarray,cfg:Mapping[str,float])->tuple[np.ndarray,np.ndarray,float,dict]:
    keep=weights>float(cfg["minimum_numerical_weight"])
    if int(keep.sum())<3:return np.full(3,np.nan),np.full((3,3),np.nan),0.,{"iterations":0,"converged":False}
    g=gyro[keep];base=weights[keep];b=np.asarray([_weighted_quantile(g[:,k],base,.5) for k in range(3)]);scale=max(math.radians(float(cfg["bias_huber_minimum_scale_dps"])),1.4826*_weighted_quantile(np.linalg.norm(g-b,axis=1),base,.5));iterations=0
    for iterations in range(1,31):
        residual=np.linalg.norm(g-b,axis=1);h=np.minimum(1.,float(cfg["bias_huber_k"])*scale/np.maximum(residual,1e-15));effective=base*h;new=np.sum(effective[:,None]*g,axis=0)/effective.sum()
        if np.linalg.norm(new-b)<1e-12:b=new;break
        b=new
    residual=g-b;effective=base*np.minimum(1.,float(cfg["bias_huber_k"])*scale/np.maximum(np.linalg.norm(residual,axis=1),1e-15));neff=_effective_sample_size(effective,times[keep],float(cfg["correlation_time_s"]));cov=(residual.T@(effective[:,None]*residual))/max(effective.sum(),1e-15)/max(neff,1.)
    return b,cov,neff,{"iterations":iterations,"converged":iterations<30,"huber_scale_rad_s":scale,"raw_positive_weight_samples":int(keep.sum()),"weight_sum":float(effective.sum())}


def _gravity_direction(accel:np.ndarray,weights:np.ndarray,times:np.ndarray,cfg:Mapping[str,float])->tuple[np.ndarray,float,float,dict]:
    norm=np.linalg.norm(accel,axis=1);keep=(weights>float(cfg["minimum_numerical_weight"]))&(norm>1e-9)
    if int(keep.sum())<3:return np.full(3,np.nan),math.inf,0.,{"iterations":0,"converged":False}
    unit=accel[keep]/norm[keep,None];base=weights[keep];direction=np.sum(base[:,None]*unit,axis=0);direction/=np.linalg.norm(direction);iterations=0;scale=math.radians(float(cfg["gravity_huber_minimum_scale_deg"]))
    for iterations in range(1,31):
        angle=np.arccos(np.clip(unit@direction,-1,1));scale=max(math.radians(float(cfg["gravity_huber_minimum_scale_deg"])),1.4826*_weighted_quantile(angle,base,.5));h=np.minimum(1.,float(cfg["gravity_huber_k"])*scale/np.maximum(angle,1e-15));effective=base*h;new=np.sum(effective[:,None]*unit,axis=0);new/=np.linalg.norm(new)
        if math.acos(float(np.clip(new@direction,-1,1)))<1e-12:direction=new;break
        direction=new
    angle=np.arccos(np.clip(unit@direction,-1,1));effective=base*np.minimum(1.,float(cfg["gravity_huber_k"])*scale/np.maximum(angle,1e-15));neff=_effective_sample_size(effective,times[keep],float(cfg["correlation_time_s"]));uncertainty=math.sqrt(float(np.sum(effective*np.square(angle))/max(effective.sum(),1e-15))/max(neff,1.))
    return direction,uncertainty,neff,{"iterations":iterations,"converged":iterations<30,"angular_residual_p95_deg":math.degrees(_weighted_quantile(angle,effective,.95)),"raw_positive_weight_samples":int(keep.sum()),"weight_sum":float(effective.sum())}


def estimate_bias_and_gravity(prepared:Mapping[str,QuasiStaticPrepared],windows:Mapping[str,tuple[int,int]],cfg:Mapping[str,float])->dict[str,dict]:
    out={};initial=windows["initial_still_attempt2"];tpose=windows["t_pose"]
    for node,item in sorted(prepared.items()):
        bias,cov,neff,bdiag=_robust_bias(item.gyro_rad_s,item.quasi_static_weight,item.time_ns,cfg);poses={}
        for name,window in (("initial_still_attempt2",initial),("t_pose",tpose)):
            mask=(item.time_ns>=window[0])&(item.time_ns<=window[1]);direction,uncertainty,gneff,gdiag=_gravity_direction(item.accel_filtered_mps2[mask],item.quasi_static_weight[mask],item.time_ns[mask],cfg);poses[name]={"gravity_direction_board":direction.tolist(),"angular_standard_uncertainty_deg":math.degrees(uncertainty),"effective_sample_size":gneff,**gdiag}
        out[node]={"gyro_bias_rad_s":bias.tolist(),"gyro_bias_covariance_rad2_s2":cov.tolist(),"gyro_bias_standard_uncertainty_dps":np.degrees(np.sqrt(np.diag(cov))).tolist(),"gyro_bias_effective_sample_size":neff,"bias_diagnostic":bdiag,"gravity":poses}
    return out


def _run_q2_node(node:str,item:QuasiStaticPrepared,estimate:Mapping[str,Any],cfg:Mapping[str,float],reference_start_ns:int)->Q2Result:
    keep=item.time_ns>=int(reference_start_ns);t=item.time_ns[keep];boot=item.boot_epoch[keep];accel=item.accel_mps2[keep];filtered=item.accel_filtered_mps2[keep];weight=item.quasi_static_weight[keep];gyro=item.gyro_rad_s[keep];bias=np.asarray(estimate["gyro_bias_rad_s"],float);gravity=np.asarray(estimate["gravity"]["initial_still_attempt2"]["gravity_direction_board"],float);q=quaternion_from_two_vectors(gravity,np.array([0.,0.,1.]));n=len(t);qs=np.empty((n,4));cov=np.empty((n,3,3));corrected=gyro-bias;accepted=np.zeros(n,bool);gaps=np.zeros(n,bool);tilt=math.radians(float(cfg["initial_tilt_sigma_deg"]));yaw=math.radians(float(cfg["yaw_sigma_deg"]));P=np.diag([tilt*tilt,tilt*tilt,yaw*yaw]);gap_count=0
    for i in range(n):
        dt=0. if i==0 else (int(t[i])-int(t[i-1]))/1e9;omega=corrected[i]
        if i and (dt<=0 or dt>float(cfg["maximum_propagated_gap_s"]) or boot[i]!=boot[i-1]):gaps[i]=True;gap_count+=1;dt=0.
        if dt>0:
            q=quaternion_normalize(quaternion_multiply(q,quaternion_exp(omega*dt)),q);noise=float(cfg["gyro_noise_sigma_rad_s_sqrt_hz"]);P=P+np.eye(3)*noise*noise*dt
        if np.linalg.norm(filtered[i])>1e-9:
            measured=filtered[i]/np.linalg.norm(filtered[i]);predicted=quaternion_to_matrix(q).T@np.array([0.,0.,1.]);error=np.cross(measured,predicted);gain=float(cfg["maximum_gravity_correction_gain"])*float(weight[i]);q=quaternion_normalize(quaternion_multiply(q,quaternion_exp(gain*error)),q);P[:2,:2]*=max(.05,1.-gain);accepted[i]=gain>float(cfg["minimum_numerical_weight"])
        qs[i]=q;cov[i]=P
    positive=np.diff(t);positive=positive[positive>0];pre_reference=int((~keep).sum());audit={"frontend":"Q2_HUMAN_QUASI_STATIC_V1","node":node,"input_total":item.input_total,"input_status_rejected":item.status_rejected,"accepted_unique_samples":n,"pre_reference_samples_preserved_but_not_propagated":pre_reference,"duplicate_timestamps_rejected":item.duplicate_timestamps_rejected,"source_accounting_closed":item.input_total==item.status_rejected+item.duplicate_timestamps_rejected+n+pre_reference,"effective_rate_hz":1e9/float(np.median(positive)) if len(positive) else 0.,"gap_boundaries":gap_count,"quasi_static_weight_mean":float(np.mean(weight)),"quasi_static_weight_p05":float(np.percentile(weight,5)),"quasi_static_weight_p95":float(np.percentile(weight,95)),"finite":bool(np.isfinite(qs).all()),"absolute_heading_observed":False,"integration_origin":"INITIAL_NEUTRAL_WINDOW_START; NO_BACKWARD_PROPAGATION_FROM_FUTURE_REFERENCE"}
    return Q2Result(t,boot,qs,cov,accel,corrected,weight>=.5,accepted,gaps,bias.copy(),audit)


def run_q2_from_prepared(prepared:Mapping[str,QuasiStaticPrepared],windows:Mapping[str,tuple[int,int]],cfg:Mapping[str,float])->tuple[dict[str,Q2Result],dict,dict[str,dict]]:
    estimates=estimate_bias_and_gravity(prepared,windows,cfg);reference_start=int(windows["initial_still_attempt2"][0]);results={node:_run_q2_node(node,item,estimates[node],cfg,reference_start) for node,item in prepared.items()};failed=[]
    for node,estimate in estimates.items():
        bias_max=max(estimate["gyro_bias_standard_uncertainty_dps"]);gravity_max=max(pose["angular_standard_uncertainty_deg"] for pose in estimate["gravity"].values());neff=min(estimate["gyro_bias_effective_sample_size"],*(pose["effective_sample_size"] for pose in estimate["gravity"].values()))
        if bias_max>float(cfg["maximum_bias_standard_uncertainty_dps"]) or gravity_max>float(cfg["maximum_gravity_angular_standard_uncertainty_deg"]) or neff<float(cfg["minimum_effective_sample_size"]) or not results[node].audit["finite"]:failed.append(node)
    audit={"schema":"biospur-q2-human-quasi-static-v1-audit","stationarity_model":"CONTINUOUS_PER_NODE_HUMAN_QUASI_STATIC_CONFIDENCE","hard_global_veto":False,"whole_window_fallback":False,"data_stream_validity":"PASS" if all(r.audit["source_accounting_closed"] for r in results.values()) else "FAIL","q2_output_finite":"PASS" if all(r.audit["finite"] for r in results.values()) else "FAIL","operator_fault":False,"nodes":{node:{**result.audit,"bias_and_gravity":estimates[node]} for node,result in sorted(results.items())},"failed_nodes":failed,"verdict":"PASS_Q2_HUMAN_QUASI_STATIC_V1" if not failed else "BLOCKED_Q2_BIAS_OR_GRAVITY_UNCERTAINTY_TOO_LARGE"}
    return results,audit,estimates


def run_q2_frontend_v1(imus:Mapping[str,np.ndarray],windows:Mapping[str,tuple[int,int]],cfg:Mapping[str,float])->tuple[dict[str,Q2Result],dict,dict[str,QuasiStaticPrepared],dict[str,dict]]:
    prepared=prepare_quasi_static(imus,cfg,windows["initial_still_attempt2"]);results,audit,estimates=run_q2_from_prepared(prepared,windows,cfg);return results,audit,prepared,estimates
