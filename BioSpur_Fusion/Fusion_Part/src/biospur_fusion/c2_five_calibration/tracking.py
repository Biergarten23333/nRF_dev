"""Position/velocity tracking within the learned pose prior.

PIP uses angular and linear tracking together. Here the learned pose remains
one regularizer, not an additional independent sensor or a PIP reproduction.
Bone-length-scaled uncertainty keeps the prior finite in physical units.
"""
import numpy as np
import torch

from .geometry import joints_from_global

ANGULAR_SCALE_RAD = .5
VELOCITY_TIME_SCALE_S = 1.


class PoseTrackingPrior:
    def __init__(self, prior, geometry, *, detach_target=True):
        positions = joints_from_global(prior, geometry)
        self.positions = positions.detach() if detach_target else positions
        variance = np.zeros(24)
        offsets = np.asarray(geometry['rest_offsets_m'])
        # Approximate independent angular uncertainty along each root-to-joint
        # chain. This is an engineering prior scale, not measured covariance.
        for joint in range(1, 24):
            variance[joint] = variance[geometry['parent'][joint]] + offsets[joint] @ offsets[joint]
        self.scale = torch.as_tensor(ANGULAR_SCALE_RAD * np.sqrt(variance[1:]),
                                    dtype=prior.dtype, device=prior.device)
        if torch.any(self.scale <= 0):
            raise ValueError('tracking prior requires nonzero root-to-joint lengths')

    def velocity_error(self, positions, derivative):
        return derivative(positions[:, 1:] - self.positions[:, 1:])

    def losses(self, rotation, geometry, derivative, derivative_valid=None, *, position_valid=None, residual_blocks=None):
        positions = joints_from_global(rotation, geometry)
        error = positions[:, 1:] - self.positions[:, 1:]
        position_error = error if position_valid is None else error[position_valid]
        position = (position_error / self.scale[None, :, None]).square().mean()
        speed_error = self.velocity_error(positions, derivative)
        if derivative_valid is not None:
            speed_error = speed_error[derivative_valid]
        velocity = (speed_error * VELOCITY_TIME_SCALE_S /
                    self.scale[None, :, None]).square().mean()
        from .residual_blocks import record_mean
        record_mean(residual_blocks, 'tracking_position', position_error / self.scale[None, :, None], .15)
        record_mean(residual_blocks, 'tracking_velocity', speed_error * VELOCITY_TIME_SCALE_S / self.scale[None, :, None], .15)
        return position, velocity, position_error.square().mean().sqrt()
