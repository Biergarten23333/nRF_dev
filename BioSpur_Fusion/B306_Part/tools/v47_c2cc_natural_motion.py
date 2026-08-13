#!/usr/bin/env python3
"""Label-independent stop-to-stop natural-motion frame binding.

Operator labels are permitted as temporal region identifiers only.  The
estimator consumes chronological local preintegrated paths and chronological
T4 paths; it never branches on a label or supplies a semantic direction.
"""
from __future__ import annotations

import dataclasses
import math
from dataclasses import dataclass

import numpy as np
from scipy.optimize import least_squares
from scipy.signal import find_peaks
from scipy.spatial.transform import Rotation

from v47_c2cc_sign_forensics import angle_deg, rotation_angle_deg, wahba_diagnostic


@dataclass(frozen=True)
class NaturalMotionConfig:
    schema: str = "biospur-c2cc-natural-motion-config-v1"
    minimum_segments: int = 8
    minimum_segment_displacement_m: float = 0.25
    minimum_imu_displacement_m: float = 0.10
    minimum_excitation_singular_ratio: float = 0.025
    maximum_excitation_condition: float = 40.0
    endpoint_median_error_limit_deg: float = 35.0
    endpoint_p95_error_limit_deg: float = 75.0
    normalized_path_median_residual_limit: float = 0.30
    normalized_path_p95_residual_limit: float = 0.90
    bootstrap_replicates: int = 300
    bootstrap_p95_rotation_limit_deg: float = 15.0
    leave_one_p95_rotation_limit_deg: float = 10.0
    leave_one_max_rotation_limit_deg: float = 15.0
    maximum_single_segment_weight_fraction: float = 0.25
    t4_position_sigma_m: float = 0.075
    robust_loss: str = "soft_l1"
    robust_f_scale_normalized: float = 0.15
    path_minimum_progress_fraction: float = 0.05
    t4_median_window_samples: int = 5
    t4_lowpass_cutoff_hz: float = 1.0
    stop_speed_candidate_limit_mps: float = 0.18
    stop_speed_prominence_mps: float = 0.04
    stop_minimum_separation_s: float = 0.50
    stop_boundary_search_fraction: float = 0.10
    time_offset_primary_s: float = 0.0
    time_offset_bound_s: float = 0.080
    time_offset_step_s: float = 0.005
    accelerometer_bias_prior_sigma_mps2: float = 0.10
    accelerometer_bias_bound_mps2: float = 0.25
    lever_arm_sensitivity_radius_m: float = 0.050
    heldout_stop_speed_limit_mps: float = 0.50
    heldout_room_scale_bound_m: float = 10.0
    random_seed: int = 47


FROZEN_NATURAL_MOTION_CONFIG = NaturalMotionConfig()


def preintegrated_path(t_s: np.ndarray, acceleration_N_mps2: np.ndarray) -> dict:
    t=np.asarray(t_s,float);acceleration=np.asarray(acceleration_N_mps2,float)
    if t.ndim!=1 or acceleration.shape!=(len(t),3) or len(t)<3 or np.any(np.diff(t)<=0):
        raise ValueError("invalid preintegration path")
    velocity=np.zeros_like(acceleration);raw=np.zeros_like(acceleration)
    for index,dt in enumerate(np.diff(t),1):
        velocity[index]=velocity[index-1]+.5*(acceleration[index-1]+acceleration[index])*dt
        raw[index]=raw[index-1]+.5*(velocity[index-1]+velocity[index])*dt
    duration=float(t[-1]-t[0]);corrected_velocity=velocity-((t-t[0])/duration)[:,None]*velocity[-1]
    corrected=np.zeros_like(acceleration)
    for index,dt in enumerate(np.diff(t),1):
        corrected[index]=corrected[index-1]+.5*(corrected_velocity[index-1]+corrected_velocity[index])*dt
    return {"time_s":t,"raw_velocity_mps":velocity,"raw_position_m":raw,
            "zupt_velocity_mps":corrected_velocity,"zupt_position_m":corrected,
            "raw_end_velocity_mps":velocity[-1],"zupt_end_velocity_mps":corrected_velocity[-1]}


def make_segment(*,segment_id:str,imu_time_s,imu_path_N_m,t4_time_s,t4_path_V4_m,
                 metadata:dict|None=None) -> dict:
    ti=np.asarray(imu_time_s,float);pn=np.asarray(imu_path_N_m,float);tt=np.asarray(t4_time_s,float);pv=np.asarray(t4_path_V4_m,float)
    if pn.shape!=(len(ti),3) or pv.shape!=(len(tt),3) or len(ti)<3 or len(tt)<2:
        raise ValueError("invalid segment arrays")
    if np.any(np.diff(ti)<=0) or np.any(np.diff(tt)<=0):raise ValueError("non-monotonic segment")
    pn=pn-pn[0];pv=pv-pv[0];dN=pn[-1];dV=pv[-1]
    return {"segment_id":str(segment_id),"imu_time_s":ti,"imu_path_N_m":pn,"t4_time_s":tt,
            "t4_path_V4_m":pv,"dN":dN,"dV":dV,"metadata":dict(metadata or {})}


def natural_stop_indices(t_s: np.ndarray, velocity_mps: np.ndarray,
                         config=FROZEN_NATURAL_MOTION_CONFIG) -> np.ndarray:
    """Find chronological 3-D speed minima without using protocol directions."""
    t=np.asarray(t_s,float);velocity=np.asarray(velocity_mps,float)
    if t.ndim!=1 or velocity.shape!=(len(t),3) or len(t)<3 or np.any(np.diff(t)<=0):
        raise ValueError("invalid T4 velocity trajectory")
    speed=np.linalg.norm(velocity,axis=1);dt=float(np.median(np.diff(t)))
    separation=max(1,int(round(config.stop_minimum_separation_s/dt)))
    candidates=find_peaks(-speed,prominence=config.stop_speed_prominence_mps,distance=separation)[0]
    candidates=candidates[speed[candidates]<=config.stop_speed_candidate_limit_mps]
    edge=max(1,int(math.ceil(config.stop_boundary_search_fraction*len(t))))
    boundary=np.asarray([int(np.argmin(speed[:edge])),len(t)-edge+int(np.argmin(speed[-edge:]))])
    return np.unique(np.concatenate([boundary,candidates])).astype(int)


def _factor_arrays(segments,config):
    source=[];target=[];weights=[];owners=[]
    for owner,segment in enumerate(segments):
        pn=np.asarray(segment["imu_path_N_m"]);pv=np.asarray(segment["t4_path_V4_m"]);ti=np.asarray(segment["imu_time_s"]);tt=np.asarray(segment["t4_time_s"])
        interpolated=np.column_stack([np.interp(tt,ti,pn[:,axis]) for axis in range(3)])
        scale_N=float(np.linalg.norm(interpolated[-1]));scale_V=float(np.linalg.norm(pv[-1]))
        if scale_N<config.minimum_imu_displacement_m or scale_V<config.minimum_segment_displacement_m:continue
        use=(np.linalg.norm(interpolated,axis=1)>=config.path_minimum_progress_fraction*scale_N)&(
            np.linalg.norm(pv,axis=1)>=config.path_minimum_progress_fraction*scale_V)
        count=int(np.sum(use))
        if count<2:continue
        for s,t in zip(interpolated[use]/scale_N,pv[use]/scale_V):
            source.append(s);target.append(t);weights.append(1/math.sqrt(count));owners.append(owner)
    return np.asarray(source),np.asarray(target),np.asarray(weights),np.asarray(owners,int)


def _fit_once(segments,config):
    if len(segments)<config.minimum_segments:raise ValueError("insufficient natural-motion segments")
    source,target,weights,owners=_factor_arrays(segments,config)
    endpoint_source=np.asarray([x["dN"]/np.linalg.norm(x["dN"]) for x in segments])
    endpoint_target=np.asarray([x["dV"]/np.linalg.norm(x["dV"]) for x in segments])
    diagnostic=wahba_diagnostic(endpoint_source,endpoint_target);initial=Rotation.from_matrix(diagnostic["proper"]).as_rotvec()
    def residual(rotvec):
        predicted=(Rotation.from_rotvec(rotvec).as_matrix()@source.T).T
        return ((predicted-target)*weights[:,None]).ravel()
    result=least_squares(residual,initial,loss=config.robust_loss,f_scale=config.robust_f_scale_normalized,max_nfev=1000,
                         xtol=1e-13,ftol=1e-13,gtol=1e-13)
    rotation=Rotation.from_rotvec(result.x).as_matrix();predicted=(rotation@source.T).T
    path_residual=np.linalg.norm(predicted-target,axis=1)
    endpoint_errors=np.asarray([angle_deg(rotation@s,t) for s,t in zip(endpoint_source,endpoint_target)])
    singular=np.asarray(diagnostic["singular_values"])
    if singular[0]<=np.finfo(float).eps or singular[-1]<=np.finfo(float).eps:
        ratio,condition=0.0,float("inf")
    else:
        ratio=float(singular[-1]/singular[0]);condition=float(singular[0]/singular[-1])
    segment_weights=np.asarray([np.sum(weights[owners==i]**2) for i in range(len(segments))]);segment_weights/=segment_weights.sum()
    return {"rotation":rotation,"endpoint_errors_deg":endpoint_errors,"path_residual_normalized":path_residual,
            "singular_values":singular,"singular_ratio":ratio,"condition":condition,"segment_weight_fraction":segment_weights,
            "optimizer":{"success":bool(result.success),"cost":float(result.cost),"optimality":float(result.optimality),"evaluations":int(result.nfev)}}


def fit_natural_motion(segments,config=FROZEN_NATURAL_MOTION_CONFIG,*,dataset_role="FITTING"):
    if dataset_role!="FITTING":raise ValueError("held-out data cannot enter natural-motion fitting")
    segments=list(segments);base=_fit_once(segments,config);rotation=base["rotation"]
    loo=[]
    for index in range(len(segments)):
        fitted=_fit_once(segments[:index]+segments[index+1:],dataclasses.replace(config,minimum_segments=config.minimum_segments-1))
        loo.append(rotation_angle_deg(rotation,fitted["rotation"]))
    rng=np.random.default_rng(config.random_seed);bootstrap=[]
    for _ in range(config.bootstrap_replicates):
        indices=rng.integers(0,len(segments),len(segments));sample=[segments[int(i)] for i in indices]
        fitted=_fit_once(sample,config);bootstrap.append(rotation_angle_deg(rotation,fitted["rotation"]))
    endpoint=base["endpoint_errors_deg"];path=base["path_residual_normalized"]
    checks={"proper_rotation":abs(np.linalg.det(rotation)-1)<1e-8 and np.linalg.norm(rotation.T@rotation-np.eye(3))<1e-8,
        "optimizer":base["optimizer"]["success"],"excitation":base["singular_ratio"]>=config.minimum_excitation_singular_ratio and base["condition"]<=config.maximum_excitation_condition,
        "endpoint_residual":float(np.median(endpoint))<=config.endpoint_median_error_limit_deg and float(np.quantile(endpoint,.95))<=config.endpoint_p95_error_limit_deg,
        "path_residual":float(np.median(path))<=config.normalized_path_median_residual_limit and float(np.quantile(path,.95))<=config.normalized_path_p95_residual_limit,
        "bootstrap":float(np.quantile(bootstrap,.95))<=config.bootstrap_p95_rotation_limit_deg,
        "leave_one_segment_out":float(np.quantile(loo,.95))<=config.leave_one_p95_rotation_limit_deg and max(loo)<=config.leave_one_max_rotation_limit_deg,
        "no_dominant_segment":float(np.max(base["segment_weight_fraction"]))<=config.maximum_single_segment_weight_fraction}
    return {**base,"endpoint_median_deg":float(np.median(endpoint)),"endpoint_p95_deg":float(np.quantile(endpoint,.95)),
        "path_median_normalized":float(np.median(path)),"path_p95_normalized":float(np.quantile(path,.95)),
        "bootstrap_rotation_deg":np.asarray(bootstrap),"bootstrap_p95_deg":float(np.quantile(bootstrap,.95)),
        "leave_one_rotation_deg":np.asarray(loo),"leave_one_p95_deg":float(np.quantile(loo,.95)),"leave_one_max_deg":float(max(loo)),
        "checks":checks,"accepted":all(checks.values()),"semantic_labels_read_by_estimator":False}


def evaluate_frozen_transform(segments,rotation,config=FROZEN_NATURAL_MOTION_CONFIG):
    segments=list(segments);source,target,weights,owners=_factor_arrays(segments,config);rotation=np.asarray(rotation,float)
    path=np.linalg.norm((rotation@source.T).T-target,axis=1)
    endpoint=np.asarray([angle_deg(rotation@x["dN"],x["dV"]) for x in segments])
    return {"segment_count":len(segments),"endpoint_errors_deg":endpoint,"endpoint_median_deg":float(np.median(endpoint)),
        "endpoint_p95_deg":float(np.quantile(endpoint,.95)),"path_median_normalized":float(np.median(path)),
        "path_p95_normalized":float(np.quantile(path,.95)),"checks":{"endpoint_residual":float(np.median(endpoint))<=config.endpoint_median_error_limit_deg and float(np.quantile(endpoint,.95))<=config.endpoint_p95_error_limit_deg,
        "path_residual":float(np.median(path))<=config.normalized_path_median_residual_limit and float(np.quantile(path,.95))<=config.normalized_path_p95_residual_limit}}
