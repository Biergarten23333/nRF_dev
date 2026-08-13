#!/usr/bin/env python3
"""Frozen black-box sensor-to-V4 frame-binding primitives.

The module is intentionally independent of capture orchestration.  It accepts
timestamped IMU and T4 position arrays, regularizes every action independently,
and fits a proper rotation.  Held-out arrays are never accepted by ``fit``.
"""
from __future__ import annotations

import dataclasses
import math
from dataclasses import dataclass

import numpy as np
from scipy.interpolate import UnivariateSpline
from scipy.spatial.transform import Rotation

G_MPS2 = 9.80665


@dataclass(frozen=True)
class BindingConfig:
    schema: str = "biospur-c2cc-black-box-binding-config-v1"
    identity_accelerometer_matrix: bool = True
    accelerometer_bias_mps2: tuple[float, float, float] = (0.0, 0.0, 0.0)
    startup_gyro_bias_s: float = 1.0
    t4_position_sigma_m: float = 0.075
    spline_degree: int = 3
    time_offset_enabled: bool = False
    time_offset_s: float = 0.0
    time_offset_bound_s: float = 0.080
    minimum_action_duration_s: float = 4.0
    minimum_t4_solutions: int = 25
    minimum_imu_samples: int = 600
    minimum_displacement_m: float = 0.35
    minimum_direction_explained: float = 0.60
    minimum_horizontal_angle_deg: float = 35.0
    maximum_translation_gyro_p95_dps: float = 12.0
    minimum_dynamic_acceleration_mps2: float = 0.20
    minimum_dynamic_matches: int = 20
    minimum_excitation_singular_ratio: float = 0.025
    maximum_excitation_condition: float = 40.0
    maximum_orthonormality_error: float = 1e-8
    maximum_determinant_error: float = 1e-8
    maximum_fit_median_residual_deg: float = 35.0
    maximum_fit_p95_residual_deg: float = 75.0
    heldout_median_direction_error_deg: float = 40.0
    heldout_p95_direction_error_deg: float = 80.0
    final_stationary_residual_mps2: float = 0.30
    maximum_nominal_rejection_fraction: float = 0.10


FROZEN_CONFIG = BindingConfig()


def validate_time_offset(offset_s: float, config: BindingConfig = FROZEN_CONFIG) -> float:
    offset_s = float(offset_s)
    if not math.isfinite(offset_s) or abs(offset_s) > config.time_offset_bound_s:
        raise ValueError("time offset outside frozen bound")
    if not config.time_offset_enabled and offset_s != 0.0:
        raise ValueError("time offset estimation is disabled for the common B306 clock")
    return offset_s


def regularized_trajectory(t_s: np.ndarray, position_m: np.ndarray,
                           config: BindingConfig = FROZEN_CONFIG) -> dict[str, np.ndarray]:
    """Fit one action only; never smooth across action or held-out boundaries."""
    t = np.asarray(t_s, float); p = np.asarray(position_m, float)
    if t.ndim != 1 or p.shape != (len(t), 3) or len(t) < config.minimum_t4_solutions:
        raise ValueError("insufficient T4 trajectory")
    if not np.isfinite(t).all() or not np.isfinite(p).all() or np.any(np.diff(t) <= 0):
        raise ValueError("invalid T4 time series")
    local = t - t[0]
    smooth = np.empty_like(p); velocity = np.empty_like(p); acceleration = np.empty_like(p)
    # Existing static T4 evidence supports 75 mm one-sigma.  The smoothing
    # budget is fixed before capture and is applied independently per axis.
    budget = len(t) * config.t4_position_sigma_m**2
    for axis in range(3):
        spline = UnivariateSpline(local, p[:, axis], k=config.spline_degree, s=budget)
        smooth[:, axis] = spline(local)
        velocity[:, axis] = spline.derivative(1)(local)
        acceleration[:, axis] = spline.derivative(2)(local)
    return {"time_s": t, "position_m": smooth, "velocity_mps": velocity,
            "acceleration_mps2": acceleration, "raw_position_m": p}


def principal_direction(position_m: np.ndarray) -> tuple[np.ndarray, float, float]:
    p = np.asarray(position_m, float); centered = p - np.median(p, axis=0)
    _, singular, vt = np.linalg.svd(centered, full_matrices=False)
    direction = vt[0]
    first = p[np.argmax(np.linalg.norm(p-p[0], axis=1) > 0.20)]-p[0] if np.any(
        np.linalg.norm(p-p[0], axis=1) > 0.20) else p[-1]-p[0]
    if float(direction @ first) < 0: direction = -direction
    explained = float(singular[0]**2 / np.sum(singular**2)) if np.sum(singular**2) else 0.0
    span = float(np.ptp(centered @ direction))
    return direction, explained, span


def infer_v4_basis(vertical_position_m: np.ndarray, horizontal_1_position_m: np.ndarray,
                   horizontal_2_position_m: np.ndarray,
                   config: BindingConfig = FROZEN_CONFIG) -> dict:
    up, ev, sv = principal_direction(vertical_position_m)
    h1, e1, s1 = principal_direction(horizontal_1_position_m)
    h2, e2, s2 = principal_direction(horizontal_2_position_m)
    # Remove any vertical leakage before testing horizontal non-collinearity.
    h1 = h1-up*float(up@h1); h2 = h2-up*float(up@h2)
    if np.linalg.norm(h1) < 1e-8 or np.linalg.norm(h2) < 1e-8:
        raise ValueError("horizontal excitation parallel to vertical")
    h1 /= np.linalg.norm(h1); h2 /= np.linalg.norm(h2)
    angle = math.degrees(math.acos(float(np.clip(abs(h1@h2), -1, 1))))
    checks = {
        "spans": min(sv, s1, s2) >= config.minimum_displacement_m,
        "direction_explained": min(ev, e1, e2) >= config.minimum_direction_explained,
        "horizontal_noncollinear": angle >= config.minimum_horizontal_angle_deg,
    }
    if not all(checks.values()): raise ValueError(f"insufficient trajectory excitation: {checks}")
    h2o = h2-h1*float(h1@h2); h2o /= np.linalg.norm(h2o)
    if float(np.cross(h1, h2o)@up) < 0: h2o = -h2o
    return {"up": up, "horizontal_1": h1, "horizontal_2": h2o,
            "horizontal_angle_deg": angle, "explained": [ev, e1, e2],
            "spans_m": [sv, s1, s2], "checks": checks}


def _interp_rows(t_source: np.ndarray, values: np.ndarray, t_target: np.ndarray) -> np.ndarray:
    return np.column_stack([np.interp(t_target, t_source, values[:, i]) for i in range(3)])


def matched_excitation(imu_t_s: np.ndarray, accel_mps2: np.ndarray, gyro_dps: np.ndarray,
                       trajectory: dict[str, np.ndarray], gravity_sensor_mps2: np.ndarray,
                       config: BindingConfig = FROZEN_CONFIG) -> tuple[np.ndarray, np.ndarray, dict]:
    ti=np.asarray(imu_t_s,float); acc=np.asarray(accel_mps2,float); gyro=np.asarray(gyro_dps,float)
    tt=trajectory["time_s"]+validate_time_offset(config.time_offset_s, config)
    sensor_delta=_interp_rows(ti,acc,tt)-np.asarray(gravity_sensor_mps2,float)
    target=trajectory["acceleration_mps2"]
    amplitude=np.minimum(np.linalg.norm(sensor_delta,axis=1),np.linalg.norm(target,axis=1))
    use=amplitude>=config.minimum_dynamic_acceleration_mps2
    gyro_at=_interp_rows(ti,gyro,tt)
    gyro_norm=np.linalg.norm(gyro_at,axis=1)
    use &= gyro_norm <= config.maximum_translation_gyro_p95_dps
    if int(np.sum(use)) < config.minimum_dynamic_matches: raise ValueError("insufficient matched dynamic excitation")
    return sensor_delta[use],target[use],{"matches":int(np.sum(use)),
        "gyro_p95_dps":float(np.quantile(gyro_norm,.95)),"dynamic_fraction":float(np.mean(use))}


def proper_wahba(sensor_vectors: np.ndarray, v4_vectors: np.ndarray,
                  config: BindingConfig = FROZEN_CONFIG) -> dict:
    s=np.asarray(sensor_vectors,float);v=np.asarray(v4_vectors,float)
    if s.shape!=v.shape or s.ndim!=2 or s.shape[1]!=3: raise ValueError("vector shape mismatch")
    ns=np.linalg.norm(s,axis=1);nv=np.linalg.norm(v,axis=1);use=(ns>1e-9)&(nv>1e-9)
    s=s[use]/ns[use,None];v=v[use]/nv[use,None]
    cross=v.T@s
    u,sv,vt=np.linalg.svd(cross)
    correction=np.eye(3);correction[-1,-1]=np.sign(np.linalg.det(u@vt))
    R=u@correction@vt
    det=float(np.linalg.det(R));orth=float(np.linalg.norm(R.T@R-np.eye(3),ord="fro"))
    ratio=float(sv[-1]/sv[0]) if sv[0] else 0.;condition=float(sv[0]/sv[-1]) if sv[-1] else math.inf
    pred=(R@s.T).T;angles=np.degrees(np.arccos(np.clip(np.sum(pred*v,axis=1),-1,1)))
    checks={"proper_rotation":abs(det-1)<=config.maximum_determinant_error and orth<=config.maximum_orthonormality_error,
            "observable":ratio>=config.minimum_excitation_singular_ratio and condition<=config.maximum_excitation_condition,
            "fit_residual":float(np.median(angles))<=config.maximum_fit_median_residual_deg and
                           float(np.quantile(angles,.95))<=config.maximum_fit_p95_residual_deg}
    if not all(checks.values()): raise ValueError(f"invalid or unobservable rotation: {checks}")
    return {"rotation":R,"quaternion_xyzw":Rotation.from_matrix(R).as_quat(),"determinant":det,
            "orthonormality_error":orth,"singular_values":sv,"singular_ratio":ratio,
            "condition":condition,"median_residual_deg":float(np.median(angles)),
            "p95_residual_deg":float(np.quantile(angles,.95)),"checks":checks}


def fit_mount(*, stationary_accel_mps2: np.ndarray, blocks: dict[str, dict],
              config: BindingConfig = FROZEN_CONFIG, dataset_role: str = "CALIBRATION") -> dict:
    if dataset_role != "CALIBRATION": raise ValueError("held-out data cannot enter frame fit")
    required=("vertical","horizontal_1","horizontal_2")
    if tuple(blocks) != required: raise ValueError("exactly three ordered calibration blocks required")
    gravity=np.median(np.asarray(stationary_accel_mps2,float),axis=0)
    basis=infer_v4_basis(*(blocks[k]["trajectory"]["position_m"] for k in required),config)
    sensor=[];target=[];quality={}
    for key in required:
        x,y,q=matched_excitation(blocks[key]["imu_t_s"],blocks[key]["accel_mps2"],
                                 blocks[key]["gyro_dps"],blocks[key]["trajectory"],gravity,config)
        sensor.append(x);target.append(y);quality[key]=q
    # Gravity supplies the static up constraint with the same frozen weight as
    # 25 dynamic direction samples; translational evidence resolves yaw.
    sensor.append(np.repeat(gravity[None,:],25,axis=0));target.append(np.repeat(basis["up"][None,:],25,axis=0))
    result=proper_wahba(np.vstack(sensor),np.vstack(target),config)
    result.update(gravity_sensor_mps2=gravity,v4_basis=basis,block_quality=quality,
                  policy={"accelerometer_matrix":"IDENTITY","accelerometer_bias_mps2":[0,0,0],
                          "heldout_used":False,"mount_reuse":False},config=dataclasses.asdict(config))
    return result


def heldout_direction_errors(rotation: np.ndarray, sensor_delta: np.ndarray,
                             target_acceleration: np.ndarray) -> np.ndarray:
    pred=(np.asarray(rotation,float)@np.asarray(sensor_delta,float).T).T
    target=np.asarray(target_acceleration,float);npred=np.linalg.norm(pred,axis=1);nt=np.linalg.norm(target,axis=1)
    use=(npred>.2)&(nt>.2)
    return np.degrees(np.arccos(np.clip(np.sum(pred[use]*target[use],axis=1)/(npred[use]*nt[use]),-1,1)))


def rotation_angle_deg(left: np.ndarray, right: np.ndarray) -> float:
    delta=np.asarray(left,float)@np.asarray(right,float).T
    return math.degrees(math.acos(float(np.clip((np.trace(delta)-1)/2,-1,1))))
