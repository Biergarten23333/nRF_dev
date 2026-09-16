"""Optional RAW mean authority from actual tag FK heading sensitivity.

Cross-covariance alone does not grant a tag authority over a structurally
unobserved heading group under this explicit consider-state policy. This is
not a claim that such correlations are mathematically invalid, nor a root
continuity bound or an independent measurement. Native/contact routes remain
owned by their existing policies.
"""
import numpy as np

from .articulated_joint_filter import SEGMENT_HEADING_GROUP,HEADING_GROUP_NAMES


def structural_heading_constraints(rotations,tag_jacobian):
    """Return zero-yaw rows for groups with numerically zero FK sensitivity.

    The test precedes range-line projection: a partially observed range sweep
    must not be mistaken for a structurally unrelated tag. Tolerance bounds
    floating-point dot-product cancellation only, without a physical cutoff.
    Combine these rows with heading/contact/consider restrictions once, before
    the existing covariance-weighted gain projection and actual-gain Joseph.
    """
    rotations=np.asarray(rotations,float);jac=np.asarray(tag_jacobian,float)
    if (rotations.shape!=(10,3,3) or jac.shape!=(3,30)
            or not np.isfinite(rotations).all() or not np.isfinite(jac).all()
            or not np.allclose(rotations.swapaxes(-1,-2)@rotations,np.eye(3),atol=1e-9,rtol=0)
            or not np.allclose(np.linalg.det(rotations),1.,atol=1e-9,rtol=0)):
        raise ValueError('structural heading authority requires proper rotations and finite tag FK Jacobian')
    basis=np.zeros((30,6))
    for i,group in enumerate(SEGMENT_HEADING_GROUP):basis[3*i:3*i+3,group]=rotations[i,2,:]
    sensitivity=jac@basis
    scale=np.linalg.norm(np.abs(jac)@np.abs(basis),axis=0)
    tolerance=np.finfo(float).eps*jac.shape[1]*scale
    norms=np.linalg.norm(sensitivity,axis=0)
    unobserved=norms<=tolerance
    rows=np.zeros((int(unobserved.sum()),39))
    rows[:,9:]=(basis[:,unobserved]/np.linalg.norm(basis[:,unobserved],axis=0)).T
    return rows,dict(
        policy='RAW_TAG_FK_STRUCTURAL_ZERO_GROUPS_CONSIDER_ONLY',
        heading_group_names=list(HEADING_GROUP_NAMES),
        considered_groups=[HEADING_GROUP_NAMES[i] for i in np.flatnonzero(unobserved)],
        sensitive_groups=[HEADING_GROUP_NAMES[i] for i in np.flatnonzero(~unobserved)],
        sensitivity_norm_m_per_rad=norms.tolist(),numerical_zero_tolerance_m_per_rad=tolerance.tolist(),
        root_correction_restricted=False,root_jump_prevention_claimed=False)
