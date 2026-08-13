#!/usr/bin/env python3
"""Rotation-aware black-box frame binding from stationary endpoint constraints.

Conventions: ``q_NS``/``R_NS`` map raw sensor vectors into the mount-local,
gravity-aligned navigation frame N.  The fitted proper ``R_V4_N`` maps N into
the capture-bound V4 frame.  A fixed sensor-frame antenna lever arm ``l_S`` is
an explicit calibration-only nuisance parameter.
"""
from __future__ import annotations

import dataclasses
import math
from dataclasses import dataclass

import numpy as np
from scipy.ndimage import median_filter
from scipy.optimize import least_squares
from scipy.optimize import minimize_scalar
from scipy.signal import butter, find_peaks, sosfiltfilt
from scipy.spatial.transform import Rotation

from v47_q1_eskf import G_MPS2,Q1T4ESKF,quaternion_to_matrix


@dataclass(frozen=True)
class RotationAwareConfig:
    schema: str="biospur-c2cc-rotation-aware-config-v1"
    identity_accelerometer_matrix: bool=True
    shared_accelerometer_bias_mps2: tuple[float,float,float]=(0.,0.,0.)
    gyro_bias_initialization_s: float=1.0
    time_offset_enabled: bool=False
    time_offset_s: float=0.0
    gyro_stationary_limit_dps: float=2.5
    accel_stationary_norm_limit_g: float=0.08
    stationary_window_s: float=0.25
    stationary_required_fraction: float=0.92
    stationary_minimum_s: float=0.30
    plateau_minimum_t4_solutions: int=2
    plateau_outlier_floor_m: float=0.20
    plateau_outlier_mad_multiplier: float=4.0
    transition_minimum_displacement_m: float=0.25
    transition_minimum_s: float=0.20
    transition_maximum_s: float=6.0
    t4_median_window_samples: int=5
    t4_lowpass_cutoff_hz: float=1.0
    turnaround_minimum_prominence_m: float=0.25
    turnaround_prominence_span_fraction: float=0.30
    turnaround_minimum_separation_s: float=0.60
    turnaround_minimum_imu_displacement_m: float=0.10
    minimum_vertical_transitions: int=4
    minimum_horizontal_transitions_each: int=2
    minimum_total_constraints: int=8
    gyro_saturation_dps: float=1900.0
    t4_endpoint_sigma_floor_m: float=0.075
    fit_loss: str="soft_l1"
    fit_f_scale_m: float=0.12
    lever_search_bound_m: float=0.25
    lever_norm_limit_m: float=0.20
    lever_ci95_limit_m: float=0.08
    lever_materiality_limit_m: float=0.20
    lever_sensitivity_radius_m: float=0.05
    lever_sensitivity_angle_limit_deg: float=5.0
    observability_condition_limit: float=1.0e4
    observability_singular_ratio_minimum: float=1.0e-4
    fit_residual_p95_limit_m: float=0.35
    up_dispersion_p95_limit_deg: float=15.0
    up_bootstrap_p95_limit_deg: float=10.0
    rotation_bootstrap_p95_limit_deg: float=15.0
    unsigned_fit_median_limit_deg: float=35.0
    unsigned_fit_p95_limit_deg: float=75.0
    signed_fit_median_limit_deg: float=35.0
    signed_fit_p95_limit_deg: float=75.0
    heldout_direction_median_limit_deg: float=45.0
    heldout_direction_p95_limit_deg: float=85.0
    heldout_stop_speed_limit_mps: float=0.35
    final_gravity_residual_median_limit_mps2: float=0.30
    cross_mount_up_limit_deg: float=10.0
    materially_different_mount_angle_deg: float=30.0
    bootstrap_replicates: int=200
    random_seed: int=47


FROZEN_ROTATION_AWARE_CONFIG=RotationAwareConfig()


def rotation_angle_deg(left,right):
    relative=np.asarray(left)@np.asarray(right).T
    return math.degrees(math.acos(float(np.clip((np.trace(relative)-1)/2,-1,1))))


def robust_center(points,config=FROZEN_ROTATION_AWARE_CONFIG):
    points=np.asarray(points,float)
    if len(points)<config.plateau_minimum_t4_solutions:raise ValueError("insufficient plateau T4 solutions")
    center=np.median(points,axis=0);distance=np.linalg.norm(points-center,axis=1)
    median=float(np.median(distance));mad=1.4826*float(np.median(np.abs(distance-median)))
    limit=max(config.plateau_outlier_floor_m,config.plateau_outlier_mad_multiplier*mad)
    keep=distance<=limit
    if np.sum(keep)<config.plateau_minimum_t4_solutions:raise ValueError("plateau rejected by robust center")
    center=np.median(points[keep],axis=0);retained=np.linalg.norm(points[keep]-center,axis=1)
    return center,{"input":len(points),"retained":int(np.sum(keep)),"outliers":int(np.sum(~keep)),
                   "radial_mad_m":mad,"radial_p95_m":float(np.quantile(retained,.95)),"limit_m":limit}


def stationary_runs(t_s,accel_mps2,gyro_dps,gyro_bias_dps,config=FROZEN_ROTATION_AWARE_CONFIG):
    t=np.asarray(t_s,float);accel=np.asarray(accel_mps2,float);gyro=np.asarray(gyro_dps,float)-gyro_bias_dps
    if len(t)<3 or np.any(np.diff(t)<=0):return []
    candidate=(np.linalg.norm(gyro,axis=1)<=config.gyro_stationary_limit_dps)&(
        np.abs(np.linalg.norm(accel,axis=1)/G_MPS2-1)<=config.accel_stationary_norm_limit_g)
    dt=float(np.median(np.diff(t)));window=max(1,int(round(config.stationary_window_s/dt)))
    fraction=np.convolve(candidate.astype(float),np.ones(window)/window,mode="same")
    quiet=fraction>=config.stationary_required_fraction
    runs=[];start=None
    for i,value in enumerate(np.r_[quiet,False]):
        if value and start is None:start=i
        elif not value and start is not None:
            end=i-1
            if t[end]-t[start]>=config.stationary_minimum_s:runs.append((start,end))
            start=None
    return runs


def propagate_mount_q1(t_s,accel_mps2,gyro_dps,config=FROZEN_ROTATION_AWARE_CONFIG):
    t=np.asarray(t_s,float);accel=np.asarray(accel_mps2,float);gyro=np.asarray(gyro_dps,float)
    training=(t-t[0])<config.gyro_bias_initialization_s
    if np.sum(training)<100:raise ValueError("insufficient one-second stationary initialization")
    gyro_bias=np.mean(gyro[training],axis=0);initial_accel=np.median(accel[training],axis=0)
    q1=Q1T4ESKF();q1.initialize_from_stationary(initial_accel,np.radians(gyro_bias))
    matrices=[];quaternions=[]
    for timestamp,a,g in zip(t,accel,gyro):
        q1.propagate(timestamp,a,np.radians(g));quaternions.append(q1.q.copy());matrices.append(quaternion_to_matrix(q1.q))
    raw_peak=float(np.max(np.abs(gyro)))
    integrity={"gyro_bias_dps":gyro_bias.tolist(),"gyro_peak_axis_dps":raw_peak,
      "gyro_saturated_or_clipped":raw_peak>=config.gyro_saturation_dps,
      "quaternion_norm_max_error":q1.max_quaternion_norm_error,"quaternion_sign_jump_max":q1.max_quaternion_sign_jump,
      "covariance_min_eigenvalue":q1.min_covariance_eigenvalue,"covariance_max_asymmetry":q1.max_covariance_asymmetry,
      "cholesky_failures":q1.cholesky_failures,"filter_resets":q1.reinitializations}
    if integrity["gyro_saturated_or_clipped"]:raise ValueError("gyro saturation")
    return np.asarray(quaternions),np.asarray(matrices),gyro_bias,integrity


def zupt_preintegrated_displacement(t_s,accel_mps2,R_NS):
    t=np.asarray(t_s,float);a=np.asarray(accel_mps2,float);rot=np.asarray(R_NS,float)
    if len(t)<3 or np.any(np.diff(t)<=0):raise ValueError("invalid preintegration interval")
    acceleration=np.einsum("nij,nj->ni",rot,a)+np.array([0.,0.,-G_MPS2])
    dt=np.diff(t);velocity=np.zeros_like(acceleration);displacement=np.zeros_like(acceleration)
    for i,d in enumerate(dt,1):
        velocity[i]=velocity[i-1]+.5*(acceleration[i-1]+acceleration[i])*d
        displacement[i]=displacement[i-1]+.5*(velocity[i-1]+velocity[i])*d
    duration=t[-1]-t[0];end_velocity=velocity[-1].copy()
    # Endpoint ZUPT removes the unique linear velocity drift satisfying both
    # stationary endpoints.  No accelerometer parameter is fitted or changed.
    corrected_velocity=velocity-(t-t[0])[:,None]/duration*end_velocity
    corrected=np.zeros_like(displacement)
    for i,d in enumerate(dt,1):corrected[i]=corrected[i-1]+.5*(corrected_velocity[i-1]+corrected_velocity[i])*d
    return corrected[-1],{"duration_s":duration,"raw_end_velocity_mps":end_velocity.tolist(),
        "raw_end_speed_mps":float(np.linalg.norm(end_velocity)),"corrected_end_speed_mps":float(np.linalg.norm(corrected_velocity[-1])),
        "raw_displacement_m":displacement[-1].tolist(),"zupt_displacement_m":corrected[-1].tolist()}


def build_endpoint_constraints(*,label,t_s,accel_mps2,gyro_dps,R_NS,gyro_bias_dps,
                               t4_t_s,t4_position_m,config=FROZEN_ROTATION_AWARE_CONFIG):
    runs=stationary_runs(t_s,accel_mps2,gyro_dps,gyro_bias_dps,config);plateaus=[]
    for start,end in runs:
        lo=t_s[start];hi=t_s[end];select=(t4_t_s>=lo)&(t4_t_s<=hi)
        if np.sum(select)<config.plateau_minimum_t4_solutions:continue
        try:center,quality=robust_center(t4_position_m[select],config)
        except ValueError:continue
        mid=.5*(lo+hi);index=int(np.argmin(np.abs(t_s-mid)))
        plateaus.append({"start_s":float(lo),"end_s":float(hi),"mid_s":float(mid),"imu_index":index,
                         "center_V4_m":center,"quality":quality})
    constraints=[]
    for number,(left,right) in enumerate(zip(plateaus,plateaus[1:]),1):
        lo=left["imu_index"];hi=right["imu_index"]
        duration=t_s[hi]-t_s[lo];dV=right["center_V4_m"]-left["center_V4_m"]
        if not(config.transition_minimum_s<=duration<=config.transition_maximum_s):continue
        if np.linalg.norm(dV)<config.transition_minimum_displacement_m:continue
        dN,integ=zupt_preintegrated_displacement(t_s[lo:hi+1],accel_mps2[lo:hi+1],R_NS[lo:hi+1])
        sigma=max(config.t4_endpoint_sigma_floor_m,left["quality"]["radial_p95_m"],right["quality"]["radial_p95_m"])
        constraints.append({"label":label,"transition":number,"start_s":float(t_s[lo]),"end_s":float(t_s[hi]),
          "dV":dV,"dN":dN,"R0":R_NS[lo],"R1":R_NS[hi],"sigma_m":sigma,"integration":integ,
          "left_plateau":left,"right_plateau":right})
    return constraints,plateaus


def regularized_turnarounds(t4_t_s,t4_position_m,config=FROZEN_ROTATION_AWARE_CONFIG):
    """Robust, action-local T4 path and alternating reversal candidates.

    This uses only a first-order low-pass trajectory.  It never takes an
    unregularized second difference, and callers must invoke it separately for
    every action block.
    """
    t=np.asarray(t4_t_s,float);p=np.asarray(t4_position_m,float)
    if len(t)<25 or p.shape!=(len(t),3) or np.any(np.diff(t)<=0):
        raise ValueError("invalid action-local T4 series")
    centered=p-np.median(p,axis=0);_,singular,vt=np.linalg.svd(centered,full_matrices=False)
    direction=vt[0];explained=float(singular[0]**2/np.sum(singular**2)) if np.sum(singular**2) else 0.
    projection=centered@direction
    # Give the PCA line a deterministic sign from the first material excursion.
    material=np.flatnonzero(np.abs(projection-projection[0])>=.20)
    if len(material) and projection[material[0]]<projection[0]:direction=-direction
    dt=float(np.median(np.diff(t)));fs=1/dt
    filtered_input=np.column_stack([median_filter(p[:,axis],size=config.t4_median_window_samples,mode="nearest") for axis in range(3)])
    sos=butter(2,config.t4_lowpass_cutoff_hz,btype="low",fs=fs,output="sos")
    smooth=sosfiltfilt(sos,filtered_input,axis=0);projected=(smooth-np.median(smooth,axis=0))@direction
    span=float(np.ptp(projected));prominence=max(config.turnaround_minimum_prominence_m,
        config.turnaround_prominence_span_fraction*span)
    distance=max(2,int(round(config.turnaround_minimum_separation_s/dt)))
    maxima,_=find_peaks(projected,prominence=prominence,distance=distance)
    minima,_=find_peaks(-projected,prominence=prominence,distance=distance)
    maxima_set=set(int(x) for x in maxima);merged=[]
    for index in np.sort(np.r_[maxima,minima]):
        kind=1 if int(index) in maxima_set else -1
        if merged and merged[-1][1]==kind:
            if kind*projected[index]>kind*projected[merged[-1][0]]:merged[-1]=(int(index),kind)
        else:merged.append((int(index),kind))
    velocity=np.gradient(smooth,t,axis=0)
    return {"time_s":t,"raw_position_m":p,"position_m":smooth,"velocity_mps":velocity,
        "principal_direction_V4":direction,"direction_explained":explained,"span_m":span,
        "prominence_m":prominence,"turnarounds":merged}


def build_turnaround_constraints(*,label,t_s,accel_mps2,R_NS,t4_t_s,t4_position_m,
                                 config=FROZEN_ROTATION_AWARE_CONFIG):
    """Build short reversal-to-reversal, endpoint-ZUPT displacement constraints."""
    t=np.asarray(t_s,float);a=np.asarray(accel_mps2,float);rot=np.asarray(R_NS,float)
    trajectory=regularized_turnarounds(t4_t_s,t4_position_m,config);rows=[]
    for number,((left,_),(right,_)) in enumerate(zip(trajectory["turnarounds"],trajectory["turnarounds"][1:]),1):
        lo=float(trajectory["time_s"][left]);hi=float(trajectory["time_s"][right]);select=(t>=lo)&(t<=hi)
        indices=np.flatnonzero(select)
        if len(indices)<40 or not(config.transition_minimum_s<=hi-lo<=config.transition_maximum_s):continue
        dV=trajectory["position_m"][right]-trajectory["position_m"][left]
        if np.linalg.norm(dV)<config.transition_minimum_displacement_m:continue
        dN,integration=zupt_preintegrated_displacement(t[select],a[select],rot[select])
        if np.linalg.norm(dN)<config.turnaround_minimum_imu_displacement_m:continue
        rows.append({"label":label,"transition":number,"start_s":lo,"end_s":hi,"dV":dV,"dN":dN,
            "R0":rot[indices[0]],"R1":rot[indices[-1]],"sigma_m":config.t4_endpoint_sigma_floor_m,
            "integration":integration,"t4_left_index":left,"t4_right_index":right})
    return rows,trajectory


def estimate_up(vertical_constraints,config=FROZEN_ROTATION_AWARE_CONFIG):
    if len(vertical_constraints)<config.minimum_vertical_transitions:raise ValueError("insufficient vertical transitions")
    vectors=[]
    for group in sorted(set(x["label"] for x in vertical_constraints)):
        rows=[x for x in vertical_constraints if x["label"]==group]
        reference=rows[0]["dV"]/np.linalg.norm(rows[0]["dV"])
        for row in rows:
            vector=row["dV"]/np.linalg.norm(row["dV"])
            # N is gravity aligned.  A positive short-stroke dN.z therefore
            # supplies the action-ledger sign without any PCB-axis knowledge.
            # The fallback retains the pure-T4 synthetic API.
            if "dN" in row:
                if float(row["dN"][2])<0:vector=-vector
            elif float(vector@reference)<0:vector=-vector
            vectors.append(vector)
    up=np.sum(vectors,axis=0);up/=np.linalg.norm(up)
    angles=np.degrees(np.arccos(np.clip(np.asarray(vectors)@up,-1,1)))
    rng=np.random.default_rng(config.random_seed);boot=[]
    for _ in range(config.bootstrap_replicates):
        sample=np.asarray(vectors)[rng.integers(0,len(vectors),len(vectors))];value=np.sum(sample,axis=0)
        if np.linalg.norm(value)>1e-9:value/=np.linalg.norm(value);boot.append(math.degrees(math.acos(float(np.clip(value@up,-1,1)))))
    result={"up_V4":up,"transition_count":len(vectors),"dispersion_median_deg":float(np.median(angles)),
      "dispersion_p95_deg":float(np.quantile(angles,.95)),"bootstrap_p95_deg":float(np.quantile(boot,.95)),
      "checks":{"dispersion":float(np.quantile(angles,.95))<=config.up_dispersion_p95_limit_deg,
                "bootstrap":float(np.quantile(boot,.95))<=config.up_bootstrap_p95_limit_deg}}
    if not all(result["checks"].values()):raise ValueError("V4 up unstable")
    return result


def _rotation_mapping_up(up):
    up=np.asarray(up,float);up/=np.linalg.norm(up)
    return Rotation.align_vectors(np.asarray([up]),np.asarray([[0.,0.,1.]]))[0].as_matrix()


def fit_rotation_lines(constraints,up,config=FROZEN_ROTATION_AWARE_CONFIG):
    """Fit a proper rotation from signed-up and horizontal line constraints.

    Reversal displacement lines are robust to endpoint magnitude drift.  The
    returned signed diagnostics separately test whether the temporal polarity
    is consistent; an undirected axis fit alone cannot establish a binding.
    """
    if len(constraints)<config.minimum_total_constraints:raise ValueError("insufficient total constraints")
    R_up=_rotation_mapping_up(up)
    def matrix(theta):return R_up@Rotation.from_rotvec([0.,0.,theta]).as_matrix()
    def line_cost(theta,rows=constraints):
        R=matrix(float(theta));value=0.
        for row in rows:
            a=R@row["dN"];b=np.asarray(row["dV"],float);a/=np.linalg.norm(a);b/=np.linalg.norm(b)
            value+=1-float(a@b)**2
        return value/len(rows)
    grid=np.linspace(-math.pi,math.pi,721,endpoint=False);values=np.asarray([line_cost(x) for x in grid]);i=int(np.argmin(values))
    step=2*math.pi/len(grid);opt=minimize_scalar(line_cost,bounds=(grid[i]-step,grid[i]+step),method="bounded",
        options={"xatol":1e-13});theta=float(opt.x);candidate=[theta,theta+math.pi]
    def metrics(angle):
        R=matrix(angle);unsigned=[];signed=[];by_label={}
        for row in constraints:
            a=R@row["dN"];b=np.asarray(row["dV"],float);dot=float(np.clip(a@b/np.linalg.norm(a)/np.linalg.norm(b),-1,1))
            s=math.degrees(math.acos(dot));u=min(s,180-s);signed.append(s);unsigned.append(u);by_label.setdefault(row["label"],[]).append(s)
        return R,np.asarray(unsigned),np.asarray(signed),by_label
    options=[metrics(x) for x in candidate];choice=min(range(2),key=lambda j:(np.quantile(options[j][2],.95),np.median(options[j][2])))
    R,unsigned,signed,by_label=options[choice];alternative=options[1-choice][2]
    aligned_source=[];aligned_target=[]
    for row in constraints:
        s=np.asarray(row["dN"],float);v=np.asarray(row["dV"],float);s/=np.linalg.norm(s);v/=np.linalg.norm(v)
        if float((R@s)@v)<0:v=-v
        aligned_source.append(s);aligned_target.append(v)
    singular=np.linalg.svd(np.asarray(aligned_target).T@np.asarray(aligned_source),compute_uv=False)
    condition=float(singular[0]/singular[-1]) if singular[-1] else math.inf;ratio=float(singular[-1]/singular[0]) if singular[0] else 0.
    counts={label:sum(row["label"]==label for row in constraints) for label in sorted(set(row["label"] for row in constraints))}
    per_action={label:{"count":len(values),"signed_median_deg":float(np.median(values)),
        "signed_p95_deg":float(np.quantile(values,.95))} for label,values in sorted(by_label.items())}
    checks={"proper_rotation":abs(np.linalg.det(R)-1)<1e-8 and np.linalg.norm(R.T@R-np.eye(3))<1e-8,
        "observable":condition<=config.observability_condition_limit and ratio>=config.observability_singular_ratio_minimum,
        "unsigned_fit":float(np.median(unsigned))<=config.unsigned_fit_median_limit_deg and float(np.quantile(unsigned,.95))<=config.unsigned_fit_p95_limit_deg,
        "signed_fit":float(np.median(signed))<=config.signed_fit_median_limit_deg and float(np.quantile(signed,.95))<=config.signed_fit_p95_limit_deg,
        "vertical_count":sum(label.startswith("vertical") and count for label,count in counts.items())>=config.minimum_vertical_transitions,
        "horizontal_1_count":counts.get("horizontal_1",0)>=config.minimum_horizontal_transitions_each,
        "horizontal_2_count":counts.get("horizontal_2",0)>=config.minimum_horizontal_transitions_each}
    return {"rotation":R,"yaw_about_N_deg":math.degrees(candidate[choice]),"determinant":float(np.linalg.det(R)),
        "orthonormality_error":float(np.linalg.norm(R.T@R-np.eye(3))),"unsigned_error_deg":unsigned,
        "signed_error_deg":signed,"signed_alternative_p95_deg":float(np.quantile(alternative,.95)),
        "unsigned_median_deg":float(np.median(unsigned)),"unsigned_p95_deg":float(np.quantile(unsigned,.95)),
        "signed_median_deg":float(np.median(signed)),"signed_p95_deg":float(np.quantile(signed,.95)),
        "per_action":per_action,"constraint_counts":counts,"excitation_singular_values":singular,
        "excitation_condition":condition,"excitation_singular_ratio":ratio,"checks":checks}


def validate_time_offset(offset_s,config=FROZEN_ROTATION_AWARE_CONFIG):
    offset=float(offset_s)
    if not math.isfinite(offset):raise ValueError("non-finite time offset")
    if not config.time_offset_enabled and offset!=config.time_offset_s:raise ValueError("time offset estimation disabled")
    return offset


def _initial_rotation(constraints,up):
    source=[np.array([0.,0.,1.])]*10;target=[up]*10
    for row in constraints:source.append(row["dN"]/np.linalg.norm(row["dN"]));target.append(row["dV"]/np.linalg.norm(row["dV"]))
    matrix=np.asarray(target).T@np.asarray(source);u,_,vt=np.linalg.svd(matrix);fix=np.eye(3);fix[-1,-1]=np.sign(np.linalg.det(u@vt))
    return u@fix@vt


def fit_rotation_and_lever(constraints,up,config=FROZEN_ROTATION_AWARE_CONFIG):
    if len(constraints)<config.minimum_total_constraints:raise ValueError("insufficient total constraints")
    initial=_initial_rotation(constraints,up);x0=np.r_[Rotation.from_matrix(initial).as_rotvec(),np.zeros(3)]
    def residual(x,rows=constraints):
        R=Rotation.from_rotvec(x[:3]).as_matrix();lever=x[3:]
        values=[]
        for row in rows:
            pred=R@(row["dN"]+(row["R1"]-row["R0"])@lever)
            values.extend((pred-row["dV"])/max(row["sigma_m"],config.t4_endpoint_sigma_floor_m))
        values.extend((R@np.array([0.,0.,1.])-up)/(math.radians(5)))
        return np.asarray(values)
    bound=np.r_[np.full(3,math.pi),np.full(3,config.lever_search_bound_m)]
    fit=least_squares(residual,x0,bounds=(-bound,bound),loss=config.fit_loss,f_scale=config.fit_f_scale_m/config.t4_endpoint_sigma_floor_m,
                      xtol=1e-12,ftol=1e-12,gtol=1e-12,max_nfev=10000)
    R=Rotation.from_rotvec(fit.x[:3]).as_matrix();lever=fit.x[3:]
    raw=[];lever_effect=[]
    for row in constraints:
        effect=(row["R1"]-row["R0"])@lever;pred=R@(row["dN"]+effect);raw.append(float(np.linalg.norm(pred-row["dV"])));lever_effect.append(float(np.linalg.norm(effect)))
    singular=np.linalg.svd(fit.jac,compute_uv=False);condition=float(singular[0]/singular[-1]);ratio=float(singular[-1]/singular[0])
    dof=max(1,len(fit.fun)-len(fit.x));variance=float(np.sum(fit.fun**2)/dof);cov=np.linalg.pinv(fit.jac.T@fit.jac)*variance
    lever_ci=1.96*np.sqrt(np.maximum(0,np.diag(cov)[3:]))
    rng=np.random.default_rng(config.random_seed);angles=[]
    groups=sorted(set(x["label"] for x in constraints))
    for _ in range(config.bootstrap_replicates):
        sampled=[]
        for group in groups:
            rows=[x for x in constraints if x["label"]==group];sampled.extend(rows[i] for i in rng.integers(0,len(rows),len(rows)))
        try:
            sub=least_squares(lambda x:residual(x,sampled),fit.x,bounds=(-bound,bound),max_nfev=2000)
            angles.append(rotation_angle_deg(R,Rotation.from_rotvec(sub.x[:3]).as_matrix()))
        except Exception:pass
    checks={"proper_rotation":abs(np.linalg.det(R)-1)<1e-8 and np.linalg.norm(R.T@R-np.eye(3))<1e-8,
      "observable":condition<=config.observability_condition_limit and ratio>=config.observability_singular_ratio_minimum,
      "fit_residual":float(np.quantile(raw,.95))<=config.fit_residual_p95_limit_m,
      "lever_inside_search":float(np.linalg.norm(lever))<=config.lever_norm_limit_m,
      "lever_identifiable":float(np.max(lever_ci))<=config.lever_ci95_limit_m,
      "lever_effect_bounded":float(np.quantile(lever_effect,.95))<=config.lever_materiality_limit_m,
      "bootstrap_rotation":len(angles)>=.9*config.bootstrap_replicates and float(np.quantile(angles,.95))<=config.rotation_bootstrap_p95_limit_deg}
    return {"rotation":R,"lever_S_m":lever,"determinant":float(np.linalg.det(R)),
      "orthonormality_error":float(np.linalg.norm(R.T@R-np.eye(3))),"residual_m":raw,
      "residual_median_m":float(np.median(raw)),"residual_p95_m":float(np.quantile(raw,.95)),
      "jacobian_singular_values":singular,"jacobian_condition":condition,"jacobian_singular_ratio":ratio,
      "lever_ci95_m":lever_ci,"lever_effect_p95_m":float(np.quantile(lever_effect,.95)),
      "rotation_bootstrap_p95_deg":float(np.quantile(angles,.95)) if angles else math.inf,
      "optimizer_success":bool(fit.success),"checks":checks,"covariance":cov}


def predict_displacement(binding,constraint):
    R=binding["rotation"];lever=binding["lever_S_m"]
    return R@(constraint["dN"]+(constraint["R1"]-constraint["R0"])@lever)


def direction_error_deg(predicted,observed):
    a=np.asarray(predicted);b=np.asarray(observed)
    return math.degrees(math.acos(float(np.clip(a@b/(np.linalg.norm(a)*np.linalg.norm(b)),-1,1))))


def serializable(value):
    if dataclasses.is_dataclass(value):return dataclasses.asdict(value)
    if isinstance(value,np.ndarray):return value.tolist()
    if isinstance(value,np.generic):return value.item()
    if isinstance(value,dict):return {k:serializable(v) for k,v in value.items()}
    if isinstance(value,(list,tuple)):return [serializable(v) for v in value]
    return value
