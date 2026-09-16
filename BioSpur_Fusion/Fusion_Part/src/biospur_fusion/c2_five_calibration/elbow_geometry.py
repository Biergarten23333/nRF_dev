"""Experimental fixed-carrying-angle elbow geometry, not a calibrated model.

Canonical forward order: flexion about X, carrying about Y, then pronation
about longitudinal -Z. Parent/child anatomical bases must be supplied by a
separate calibration owner; sensor coordinate frames remain proper SO(3).
This extends the existing DOWN/-Z, HINGE/+X forward convention. It does not
enable a new model in JointModel or infer carrying angle from a single IMU.

Reference: ISB JCS Part II (2005), and Laidig et al., Self-Calibrating
Magnetometer-Free Inertial Motion Tracking of 2-DoF Joints (2022).
"""
import torch

from .anatomy import exp_rotation


def forward_elbow(flexion, carrying, pronation):
    """Parent-to-child rotation; all angles are signed radians.

    Signed flexion remains explicit: negative flexion must be rejected by
    the anatomical owner, never hidden by an unsigned geometric bend.
    """
    flexion, carrying, pronation = torch.broadcast_tensors(flexion, carrying, pronation)
    zero = torch.zeros_like(flexion)
    return (exp_rotation(torch.stack((flexion, zero, zero), -1))
            @ exp_rotation(torch.stack((zero, carrying, zero), -1))
            @ exp_rotation(torch.stack((zero, zero, -pronation), -1)))


def inverse_elbow(relative):
    """Signed FE/CA/PS on the nonsingular |CA| < 90-degree branch."""
    if relative.shape[-2:] != (3, 3) or not torch.isfinite(relative).all():
        raise ValueError('finite proper relative elbow rotations required')
    identity = torch.eye(3, dtype=relative.dtype, device=relative.device)
    if (not torch.allclose(relative @ relative.transpose(-1, -2), identity.expand_as(relative),
                           atol=1e-6, rtol=0)
            or torch.any(torch.linalg.det(relative) < 0)):
        raise ValueError('proper relative elbow rotations required')
    cosine = torch.linalg.vector_norm(relative[..., 0, :2], dim=-1)
    if torch.any(cosine < 1e-6):
        raise ValueError('carrying-angle singularity; flexion and pronation not separable')
    flexion = torch.atan2(-relative[..., 1, 2], relative[..., 2, 2])
    carrying = torch.atan2(relative[..., 0, 2], cosine)
    pronation = torch.atan2(relative[..., 0, 1], relative[..., 0, 0])
    return torch.stack((flexion, carrying, pronation), -1)


def geometric_bend(relative):
    """Angle of the two longitudinal axes; not the signed flexion angle."""
    # Pronation leaves -Z unchanged. Parent long axis is also -Z.
    return torch.atan2(torch.linalg.vector_norm(relative[..., :2, 2], dim=-1),
                       relative[..., 2, 2])
