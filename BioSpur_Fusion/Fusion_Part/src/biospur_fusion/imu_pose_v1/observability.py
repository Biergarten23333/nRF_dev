from __future__ import annotations

import numpy as np

from . import so3
from .estimator import CoupledPoseEstimator
from .joints import JOINTS
from .types import SEGMENTS


def construct_information(estimator: CoupledPoseEstimator) -> tuple[np.ndarray,np.ndarray]:
    """Construct measured/gauge-conditioned and weak-prior information matrices."""
    if estimator.q is None:
        raise ValueError("estimator has no solved state")
    n=30; data=np.zeros((n,n))
    # Accelerometers constrain two tilt directions per segment, never yaw.
    for segment in SEGMENTS:
        sl=estimator._block(segment)
        vertical=so3.matrix(estimator.q[segment]).T@np.array([0.,0.,1.])
        P=np.eye(3)-np.outer(vertical,vertical)
        H=np.zeros((3,n));H[:,sl]=P
        data+=H.T@H/(np.deg2rad(2.0)**2)
    # Reconstruct the actually configured parent-child Jacobian architecture.
    for spec in JOINTS:
        H=estimator._joint_H(spec.parent,spec.child)
        data+=H.T@H/(estimator.config.temporal_relative_sigma_rad**2)
        if spec.kind=="hinge" and estimator.config.enable_hinge_axis:
            axis=estimator.hinge_axes.get(spec.name,np.array([1.,0,0.]));P=np.eye(3)-np.outer(axis,axis)
            data+=(P@H).T@(P@H)/(estimator.config.hinge_orthogonal_sigma_rad**2)
        if spec.name in estimator.heading_targets and estimator.config.enable_relative_heading:
            c=estimator.heading_confidence.get(spec.name,0)
            data+=c*H[2:3].T@H[2:3]/estimator.config.heading_sigma_rad**2
    prior=data+np.eye(n)/(np.deg2rad(90.0)**2)
    return .5*(data+data.T),.5*(prior+prior.T)


def svd_scan(matrix: np.ndarray, tolerances=(1e-4,1e-5,1e-6,1e-7,1e-8)) -> dict:
    scale=np.diag(1/np.sqrt(np.maximum(np.diag(matrix),1e-12)))
    white=scale@matrix@scale
    symmetry=float(np.linalg.norm(white-white.T))
    singular=np.linalg.svd(white,compute_uv=False)
    out={}
    for tol in tolerances:
        threshold=tol*max(float(singular[0]),1.0);rank=int(np.sum(singular>threshold))
        out[f"{tol:g}"]={"rank":rank,"nullity":len(singular)-rank,"threshold":threshold}
    return {"dimension":matrix.shape[0],"whitened_symmetry_error":symmetry,
            "singular_values":singular.tolist(),"tolerance_scan":out,
            "condition_number_nonzero":float(singular[0]/singular[singular>1e-10][-1]) if np.any(singular>1e-10) else float("inf")}
