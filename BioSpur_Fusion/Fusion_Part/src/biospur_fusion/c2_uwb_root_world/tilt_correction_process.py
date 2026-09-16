"""Engineering OU process on native-relative log attitude corrections.

This is a process prior, not repeated independent IMU attitude observations.
The retained component is log-axis yaw; mixed finite Euler heading need not
remain invariant. Pure world-yaw correction is preserved exactly.
"""
import numpy as np
from scipy.spatial.transform import Rotation

from biospur_fusion.c2_uwb_calibration.articulated_range import _so3_right_jacobian


def restore_tilt(base, rotations, dt_s, *, sigma_rad=.10, correlation_s=.125,
                 yaw_rw_rad_sqrt_s=.01):
    """Return rotations, right-error Jacobian blocks, independent process Q."""
    base=np.asarray(base,float);rotations=np.asarray(rotations,float)
    if (base.shape!=rotations.shape or base.ndim!=3 or base.shape[1:]!=(3,3)
            or not np.isfinite(base).all() or not np.isfinite(rotations).all()
            or not np.isfinite(dt_s) or dt_s<0
            or not np.isfinite(sigma_rad) or sigma_rad<=0
            or not np.isfinite(correlation_s) or correlation_s<=0
            or not np.isfinite(yaw_rw_rad_sqrt_s) or yaw_rw_rad_sqrt_s<0):
        raise ValueError('invalid tilt process input')
    for matrix in (base,rotations):
        if not np.allclose(matrix.swapaxes(1,2)@matrix,np.eye(3),atol=1e-8,rtol=0) or not np.allclose(np.linalg.det(matrix),1.,atol=1e-8,rtol=0):
            raise ValueError('tilt process requires proper rotations')
    count=len(base)
    if dt_s==0:
        return rotations.copy(),np.tile(np.eye(3),(count,1,1)),np.zeros((count,3,3))
    delta=Rotation.from_matrix(base.swapaxes(1,2)@rotations).as_rotvec()
    gravity=base[:,2,:]
    yaw=gravity[:,:,None]*gravity[:,None,:];tilt=np.eye(3)-yaw
    rho=np.exp(-dt_s/correlation_s);u=yaw+rho*tilt
    corrected=np.einsum('nij,nj->ni',u,delta)
    output=base@Rotation.from_rotvec(corrected).as_matrix()
    jac=np.empty((count,3,3));noise=np.empty_like(jac)
    for i in range(count):
        right=_so3_right_jacobian(corrected[i])
        jac[i]=np.linalg.solve(_so3_right_jacobian(delta[i]).T,(right@u[i]).T).T
        qlog=sigma_rad**2*(-np.expm1(-2*dt_s/correlation_s))*tilt[i]+yaw_rw_rad_sqrt_s**2*dt_s*yaw[i]
        noise[i]=right@qlog@right.T
    return output,jac,noise
