"""Fixed shank mounting proposals in the unresolved functional-axis direction.

Only mounting changes here. The caller owns physical lever parameters and
must refresh learned features before comparing a fitted proposal.
"""
import copy
import numpy as np
import torch
from scipy.spatial.transform import Rotation
from biospur_fusion.c2_imucoco.preprocessing import WORLD_TO_SMPL


def transport_shank_observed(observed, geometry, angles_rad):
    """Differentiable local surrogate; force remains in the same world frame."""
    from .anatomy import exp_rotation
    if angles_rad.shape!=(2,) or not torch.isfinite(angles_rad).all():
        raise ValueError('two finite shared mounting coordinates required')
    change=exp_rotation(angles_rad[:,None]*angles_rad.new_tensor([0.,1.,0.]))
    correction=observed.new_tensor(geometry['bone_frame_correction'])[3:]
    world=observed.new_tensor(WORLD_TO_SMPL)
    bone_change=correction.transpose(-1,-2)@world@change@world.T@correction
    result=observed.clone()
    result[:,3:]=observed[:,3:]@bone_change
    return result


def shank_mount_proposal(calibration, geometry, angles_rad):
    """Return a copied frontend and two right-multiplying bone transforms.

    Rotating about functional segment Y preserves its measured lateral axis.
    No standing pose, knee angle or sensor position is fabricated here.
    """
    angles=np.asarray(angles_rad,dtype=float)
    if angles.shape!=(2,) or not np.isfinite(angles).all():
        raise ValueError('two finite constant shank mounting angles required')
    mount=np.asarray(calibration['segment_axes_in_sensor'],dtype=float).copy()
    correction=np.asarray(geometry['bone_frame_correction'],dtype=float)
    if mount.shape!=(5,3,3) or correction.shape!=(5,3,3):
        raise ValueError('five mounting and bone-frame rotations required')
    change=Rotation.from_rotvec(angles[:,None]*np.array([0.,1.,0.])).as_matrix()
    mount[3:]=mount[3:]@change
    result=copy.deepcopy(calibration)
    result['segment_axes_in_sensor']=mount.tolist()
    result.pop('mount_axis_information',None)
    result['calibration_accepted']=False
    result['mount_proposal']=dict(shank_increment_rad=angles.tolist(),
        learned_features_require_refresh=True,lever_parameters_updated=False,
        functional_axis_audit_requires_refresh=True)
    bone_change=correction[3:].transpose(0,2,1)@WORLD_TO_SMPL@change@WORLD_TO_SMPL.T@correction[3:]
    return result,bone_change
