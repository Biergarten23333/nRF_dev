from __future__ import annotations
import numpy as np

def specific_force(R_SW,a_segment_W,alpha_W,omega_W,r_W,g_W,b_a):
    R=np.asarray(R_SW,float); a=np.asarray(a_segment_W,float)+np.cross(alpha_W,r_W)+np.cross(omega_W,np.cross(omega_W,r_W))
    return R@(a-np.asarray(g_W,float))+np.asarray(b_a,float)

def shared_axis_residual(axis_world,R_W_S_a,R_W_S_b,gyro_a,gyro_b):
    axis=np.asarray(axis_world,float); axis/=np.linalg.norm(axis)
    wa=np.asarray(R_W_S_a,float)@np.asarray(gyro_a,float); wb=np.asarray(R_W_S_b,float)@np.asarray(gyro_b,float)
    P=np.eye(3)-np.outer(axis,axis)
    return P@(wa-wb)

def scaled_rank_report(J,scales,fd_noise_floor=1e-10,declared_gauge_columns=()):
    J=np.asarray(J,float); scales=np.asarray(scales,float)
    if J.ndim!=2 or scales.shape!=(J.shape[1],) or np.any(scales<=0):raise ValueError("invalid scaled Jacobian")
    keep=[i for i in range(J.shape[1]) if i not in set(declared_gauge_columns)]
    A=J[:,keep]*scales[keep]; s=np.linalg.svd(A,compute_uv=False)
    eps=np.finfo(float).eps; tol=max(max(A.shape)*eps*(s[0] if len(s) else 0),10*fd_noise_floor)
    return {"rank":int(np.sum(s>tol)),"columns_after_gauge":len(keep),"tolerance":float(tol),"singular_values":s.tolist(),"weakest":float(s[-1]) if len(s) else 0.0}

def ensure_covariance(P,tol=1e-10):
    P=np.asarray(P,float); S=(P+P.T)/2
    if P.ndim!=2 or P.shape[0]!=P.shape[1] or not np.isfinite(P).all():raise ValueError("finite square covariance required")
    eig=np.linalg.eigvalsh(S)
    if eig[0]<-tol:raise ValueError("covariance not PSD")
    return S,eig

def conditional_prior_calibration(mapping,hypothesis_rank):
    """Finite conditional placeholder used when real evidence cannot identify a freeze.

    It deliberately reports prior-dominated broad marginals rather than inventing
    subject measurements or a body-fit solution.
    """
    marginals={}
    for node,role in sorted(mapping.items()):
        marginals[node]={"role":role,"T_segment_to_IMU":{"rotation_vector_rad":[0,0,0],"translation_m":[0,0,0],
            "covariance_diagonal":[0.25,0.25,0.25,0.01,0.01,0.01]},"status":"UNIDENTIFIED_PRIOR_DOMINATED"}
    return {"hypothesis_rank":hypothesis_rank,"mapping":mapping,"conditional_marginals":marginals,
            "critical_cross_covariances":"AVAILABLE_SCHEMA_NO_AUTHORITATIVE_NUMERIC_FREEZE",
            "declared_gauges":["global_translation_3","global_yaw_1"],"authoritative":False}

