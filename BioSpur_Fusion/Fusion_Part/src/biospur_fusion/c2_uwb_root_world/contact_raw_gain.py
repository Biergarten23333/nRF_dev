"""Route raw-update gain away from selected supported endpoint corrections.

This is a gain restriction, not another contact measurement. The caller owns
fresh supported-episode authority and the augmented Joseph covariance update. The
linear restriction does not guarantee exact finite-retraction endpoint locks.
"""
import numpy as np


def grouped_heading_constraints(rotations):
    """Right-local complement of the existing six common world-yaw groups."""
    from .articulated_joint_filter import SEGMENT_HEADING_GROUP
    rotations=np.asarray(rotations,float)
    if (rotations.shape!=(10,3,3) or not np.isfinite(rotations).all()
            or not np.allclose(rotations.swapaxes(-1,-2)@rotations,np.eye(3),atol=1e-9,rtol=0)
            or not np.allclose(np.linalg.det(rotations),1.,atol=1e-9,rtol=0)):
        raise ValueError('ten proper rotations required for heading constraints')
    basis=np.zeros((30,6))
    for i,group in enumerate(SEGMENT_HEADING_GROUP):
        basis[3*i:3*i+3,group]=rotations[i].T@np.array([0.,0.,1.])
    q,_=np.linalg.qr(basis,mode='complete')
    constraint=np.zeros((24,39));constraint[:,9:]=q[:,6:].T
    return constraint


def project_augmented_gain(gain,covariance,base_constraints,zero_rows=()):
    """Combine base restrictions and consider rows in ONE gain projection.

    Keep the augmented covariance intact; callers use this actual gain in
    Joseph and apply the normal tangent-reset transport after retraction.
    """
    base_constraints=np.asarray(base_constraints,float)
    if base_constraints.ndim!=2 or base_constraints.shape[1]>len(gain):
        raise ValueError('invalid base gain constraints')
    zero_rows=tuple(zero_rows)
    if any(isinstance(row,(bool,np.bool_)) or not isinstance(row,(int,np.integer))
           or row<0 or row>=len(gain) for row in zero_rows):
        raise ValueError('consider rows must be valid nonnegative integer gain rows')
    constraint=np.zeros((len(base_constraints)+len(zero_rows),len(gain)))
    constraint[:len(base_constraints),:base_constraints.shape[1]]=base_constraints
    for i,row in enumerate(zero_rows):constraint[len(base_constraints)+i,row]=1.
    return project_contact_gain(gain,covariance,constraint)


def project_contact_gain(gain, covariance, constraint):
    """Return covariance-metric projected base gain and numerical audit.

    For P=L L.T and A=C L, K' = K - L A^+ C K. SVD avoids squaring the
    condition number via C P C.T and admits redundant endpoint constraints.
    Rank truncation uses floating-point resolution, not a tuned gain weight.
    """
    gain = np.asarray(gain, float)
    covariance = np.asarray(covariance, float)
    constraint = np.asarray(constraint, float)
    if (gain.ndim != 2 or covariance.shape != (len(gain), len(gain))
            or constraint.ndim != 2 or constraint.shape[1] != len(gain)
            or not len(constraint)
            or not all(np.isfinite(v).all() for v in (gain, covariance, constraint))
            or not np.allclose(covariance, covariance.T, atol=1e-12, rtol=1e-10)):
        raise ValueError('invalid contact gain projection arrays')
    lower = np.linalg.cholesky(covariance)
    u, singular, vt = np.linalg.svd(constraint @ lower, full_matrices=False)
    tolerance = np.finfo(float).eps * max(constraint.shape) * singular[0]
    keep = singular > tolerance
    removed = vt[keep].T @ ((u[:, keep].T @ (constraint @ gain)) / singular[keep, None])
    projected = gain - lower @ removed
    residual = constraint @ projected
    # A materially unsatisfied constraint must not be advertised as projected.
    scale = max(1., float(np.linalg.norm(constraint) * np.linalg.norm(gain)))
    if np.linalg.norm(residual) > 1e-10 * scale:
        raise FloatingPointError('contact gain constraint is numerically unresolved')
    return projected, dict(constraint_rank=int(keep.sum()),
                          constraint_rows=len(constraint),
                          singular_values=singular.tolist(),
                          rank_tolerance=float(tolerance),
                          linear_gain_residual_max=float(np.max(np.abs(residual))),
                          finite_endpoint_lock_guaranteed=False)
