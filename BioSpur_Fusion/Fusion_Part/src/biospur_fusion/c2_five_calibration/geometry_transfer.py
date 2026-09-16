"""Conditional geometry response using the existing physical observation model.

This is a matched, filtered relative-acceleration difference at 20 Hz, NOT
a replacement for raw 60 Hz neural features. Pose uncertainty remains the
caller's responsibility; no sensor measurement is changed here.
"""
import numpy as np
import torch

from .geometry import sensor_positions
from .operators import multiscale


def geometry_acceleration_difference(rotation, source, target, source_levers, target_levers):
    """Return target-minus-source predicted sensor acceleration, pelvis-relative.

    Both geometries must use the same joint topology and rotation convention.
    Common world translation cancels. Valid derivative-window ownership stays
    with the caller, exactly as for the ordinary acceleration residual.
    """
    if source['parent'] != target['parent']:
        raise ValueError('geometry transfer requires identical joint topology')
    if not np.allclose(source['bone_frame_correction'], target['bone_frame_correction'],
                       atol=1e-12, rtol=0):
        raise ValueError('geometry transfer cannot also change bone frames')
    for lever in (source_levers, target_levers):
        value=torch.as_tensor(lever)
        if value.shape!=(5,3) or not torch.isfinite(value).all():
            raise ValueError('five finite sensor lever vectors required')
    return multiscale(sensor_positions(rotation,target,target_levers)
                      - sensor_positions(rotation,source,source_levers))
