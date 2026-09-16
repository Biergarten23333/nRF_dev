"""Absolute support-point acceleration from a pelvis IMU and relative FK.

This supplies a physical residual, not contact detection or a loss weight.
Only an independently justified stationary material point has zero target.
Rolling contact must track the appropriate material point; an ankle is not
a stationary point merely because part of the foot touches the floor.
"""
import numpy as np
import torch

from .geometry import joints_from_global
from .operators import HZ, WIDTH, multiscale


def point_acceleration(rotation, pelvis_acceleration, geometry, pelvis_lever,
                       *, joints, local_points):
    """Return T-WIDTH+1 x 2 scales x points x XYZ, in world m/s².

    Inputs use the existing 20 Hz world frame and gravity-removed acceleration
    at the actual pelvis sensor. Offsets are in each named segment frame.
    The pelvis sensor lever is essential: the sensor is not the pelvis joint.
    """
    if rotation.ndim != 4 or rotation.shape[1:] != (24, 3, 3):
        raise ValueError('expected T x 24 x 3 x 3 global rotations')
    if len(rotation) < WIDTH or pelvis_acceleration.shape != (len(rotation), 3):
        raise ValueError('need aligned pelvis acceleration and a full stencil')
    indices = list(joints)
    if not indices or any(i < 0 or i >= 24 for i in indices):
        raise ValueError('support points require explicit segment indices')
    points = torch.as_tensor(local_points, dtype=rotation.dtype, device=rotation.device)
    lever = torch.as_tensor(pelvis_lever, dtype=rotation.dtype, device=rotation.device)
    if points.shape != (len(indices), 3) or lever.shape != (3,):
        raise ValueError('point offsets and pelvis lever must be XYZ vectors')
    position = joints_from_global(rotation, geometry)
    relative = position[:, indices] + (rotation[:, indices] @ points[..., None]).squeeze(-1)
    pelvis_sensor = (rotation[:, 0] @ lever[..., None]).squeeze(-1)
    relative = relative - pelvis_sensor[:, None]
    return multiscale(relative) + multiscale(pelvis_acceleration, acceleration=True)[:, :, None]


def stationary_support_mask(time_s, valid, stationary):
    """Keep only uninterrupted, explicitly stationary full windows per point.

    No threshold on network contact logits is implied by this API.
    """
    time_s, valid, stationary = np.asarray(time_s), np.asarray(valid), np.asarray(stationary)
    if valid.dtype != bool or stationary.dtype != bool:
        raise ValueError('valid and stationary must be explicit boolean masks')
    if time_s.ndim != 1 or valid.shape != time_s.shape or stationary.ndim != 2 or len(stationary) != len(time_s):
        raise ValueError('time, validity and per-point masks must align')
    if len(time_s) < WIDTH or not np.isfinite(time_s).all() or np.any(np.diff(time_s) <= 0):
        raise ValueError('need a finite increasing time axis with a full stencil')
    good = np.lib.stride_tricks.sliding_window_view(valid[:, None] & stationary, WIDTH, axis=0).all(-1)
    cadence = np.isclose(np.diff(time_s), 1/HZ, atol=1e-5, rtol=0)
    good &= np.lib.stride_tricks.sliding_window_view(cadence, WIDTH-1).all(-1)[:, None]
    return good
