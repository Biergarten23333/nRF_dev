"""Finite front-belt orientation observation, separate from the pelvis joint.

The three extra coordinates are pose states, not time-varying mounting
calibration. Raw acceleration and the calibrated network input stay unchanged.
This does not claim to reconstruct measured skin deformation from SMPL.
"""
import torch

from .anatomy import exp_rotation
from .soft_observation import SIP_ORIENTATION_TO_ACCELERATION_WEIGHT


class PelvisObservationObjective:
    parameter_count = 24

    def __init__(self, base):
        if base.parameter_count != 21:
            raise ValueError('pelvis observation requires the 21-coordinate soft limb owner')
        self.base = base
        self.model, self.body, self.valid = base.model, base.body, base.valid
        self.observed, self.acceleration = base.observed, base.acceleration
        self.initial = torch.cat((base.initial, torch.zeros_like(base.initial[:,:3])), dim=1)

    def parameters_from_rotation(self, rotation):
        from scipy.spatial.transform import Rotation
        previous = torch.as_tensor(rotation, dtype=self.observed.dtype)
        if previous.shape != self.base.base.prior.shape or not torch.isfinite(previous).all():
            raise ValueError('finite full-body warm start required')
        if (not torch.allclose(previous @ previous.transpose(-1,-2), torch.eye(3,dtype=previous.dtype),atol=1e-5,rtol=0)
                or torch.any(torch.linalg.det(previous) < 0)):
            raise ValueError('proper body rotations required')
        root_delta = previous[:,0] @ self.observed[:,0].transpose(-1,-2)
        delta = torch.as_tensor(Rotation.from_matrix(root_delta.numpy()).as_rotvec(),dtype=previous.dtype)
        # Reuse the established limb and torso coordinate conversion. Only
        # its root equality check needs the original measured root restored.
        proxy = previous.clone()
        proxy[:,0] = self.observed[:,0]
        return torch.cat((self.base.parameters_from_rotation(proxy), delta), dim=1)

    def evaluate(self, parameters, levers, *, observed=None, acceleration=None,
                 refresh_projection=False, projection_gap=False, residual_blocks=None):
        if parameters.shape != (len(self.observed), self.parameter_count):
            raise ValueError('expected 21 body/limb coordinates and three pelvis coordinates')
        delta = parameters[:,21:]
        if not torch.isfinite(delta).all() or torch.any(delta.norm(dim=-1) >= torch.pi):
            raise ValueError('pelvis observation residual left its principal branch')
        measured = self.observed if observed is None else observed
        body_observation = measured.clone()
        body_observation[:,0] = exp_rotation(delta) @ measured[:,0]
        rotation, terms = self.base.evaluate(
            parameters[:,:21], levers, observed=body_observation, acceleration=acceleration,
            refresh_projection=refresh_projection, projection_gap=projection_gap,
            residual_blocks=residual_blocks)
        weight = SIP_ORIENTATION_TO_ACCELERATION_WEIGHT/.75**2
        orientation = weight*delta[self.valid].square().mean()
        difference = (delta[1:]-delta[:-1])[self.base.base.valid_pairs]/.12
        continuity = .1*difference.square().mean()
        from .residual_blocks import record_mean
        record_mean(residual_blocks,'pelvis_orientation',delta[self.valid],weight)
        record_mean(residual_blocks,'pelvis_orientation_smoothness',difference,.1)
        return rotation, {**terms, 'loss':terms['loss']+orientation+continuity,
            'pelvis_orientation_loss':orientation,
            'pelvis_orientation_smoothness':continuity}
