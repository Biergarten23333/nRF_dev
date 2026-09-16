"""Experimental replacement of selected learned temporal-prior channels.

This is not an IMU measurement. Only directly supervised, non-mesh ASP
points are supported: upstream channels 21,4,5,3 map to SMPL 23,7,8,6.
The unselected channels retain the original pose-derived velocity target;
the original normalization and weight are preserved, not counted twice.
No production workflow enables this candidate by default.
"""
import numpy as np
import torch

from .operators import HZ, WIDTH, apply_stencil, velocity_coefficients
from .tracking import PoseTrackingPrior

SUPPORTED_POINTS = (23, 7, 8, 6)


class PointVelocityTrackingPrior(PoseTrackingPrior):
    def __init__(self, prior, geometry, *, time_s, velocity_mps, point_ids,
                 convention, detach_target=True):
        super().__init__(prior, geometry, detach_target=detach_target)
        t = np.asarray(time_s)
        points = tuple(point_ids)
        if convention != 'root_relative_world_instantaneous_mps':
            raise ValueError('velocity must use the declared point, frame and time convention')
        if (not points or len(set(points)) != len(points)
                or any(p not in SUPPORTED_POINTS for p in points)):
            raise ValueError('unsupported or duplicate supervised non-mesh point')
        if (t.shape != (len(prior),) or len(t) < WIDTH or not np.isfinite(t).all()
                or not np.allclose(np.diff(t), 1/HZ, atol=1e-7, rtol=0)):
            raise ValueError('velocity must share the original 20 Hz pose grid')
        velocity = torch.as_tensor(velocity_mps, dtype=prior.dtype, device=prior.device)
        if velocity.shape != (len(prior), len(points), 3) or not torch.isfinite(velocity).all():
            raise ValueError('finite labelled point velocities required')
        self.point_ids = points
        self.target_velocity = apply_stencil(velocity.detach().clone(), velocity_coefficients())

    def velocity_error(self, positions, derivative):
        error = super().velocity_error(positions, derivative)
        predicted = derivative(positions[:, self.point_ids])
        if predicted.shape != self.target_velocity.shape:
            raise ValueError('velocity prior must use the whole matching derivative support')
        result = error.clone()
        result[:, [p-1 for p in self.point_ids]] = predicted-self.target_velocity
        return result
