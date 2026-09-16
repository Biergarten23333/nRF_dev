"""Experimental navigation observations; not enabled in production inference.

Gyro increments constrain temporal orientation changes, not absolute heading.
Bias is an explicit input, never assumed identified by gravity or a pose prior.
The quaternion initializer supplies tilt, and no second VQF yaw likelihood.
"""
import numpy as np
from scipy.spatial.transform import Rotation
import torch


def bias_yaw_basis(time_s, unit_up_in_sensor, anchor_index, *, maximum_gap_s=.0075):
    """First-order open-loop heading sensitivity to a subtracted sensor bias.

    The returned seconds-valued basis gives delta_yaw = offset + basis @
    delta_bias_rad_s. Tilt is frozen; this is NOT the feedback response of VQF
    or a measurement of bias. The anchor only chooses a parameterization.
    Unknown attitude during a gap cannot be integrated, so fail closed.
    """
    time = np.asarray(time_s, dtype=float)
    up = np.asarray(unit_up_in_sensor, dtype=float)
    if (time.ndim != 1 or len(time)<2 or up.shape != (len(time), 2, 3)
            or not isinstance(anchor_index, (int, np.integer))
            or not 0<=anchor_index<len(time)):
        raise ValueError('expected time, T x two unit sensor-up vectors and a valid anchor index')
    dt = np.diff(time)
    if (not np.isfinite(time).all() or not np.isfinite(up).all()
            or not np.isfinite(maximum_gap_s) or maximum_gap_s<=0
            or np.any(dt<=0)):
        raise ValueError('finite inputs and strictly increasing times required')
    if not np.allclose(np.linalg.norm(up, axis=-1), 1., atol=1e-8, rtol=0):
        raise ValueError('unit up direction required, not measured acceleration')
    if np.any(dt>maximum_gap_s):
        raise ValueError('unobserved attitude across gap: heading basis unavailable')
    integral = np.concatenate((np.zeros((1, 2, 3)),
        np.cumsum(.5*(up[:-1]+up[1:])*dt[:, None, None], axis=0)))
    return integral[anchor_index]-integral


def forearm_yaw_transport(orientation, acceleration, yaw):
    """Transport both forearms about SMPL up; preserve pelvis and shanks.

    yaw has one state per frame and forearm. This function changes no time,
    mounting, body identity or recorded observation in place.
    """
    if (orientation.ndim != 4 or orientation.shape[1:] != (5, 3, 3)
            or acceleration.shape != orientation.shape[:2]+(3,)
            or yaw.shape != (len(orientation), 2)):
        raise ValueError('expected T x five rotations/accelerations and T x two forearm yaw states')
    if any(v.dtype != orientation.dtype or v.device != orientation.device
           for v in (acceleration, yaw)):
        raise ValueError('navigation tensors require one dtype and device')
    if not all(torch.isfinite(v).all() for v in (orientation, acceleration, yaw)):
        raise ValueError('nonfinite navigation input')
    angles = torch.cat((yaw.new_zeros(len(yaw), 1), yaw, yaw.new_zeros(len(yaw), 2)), dim=1)
    c, s = torch.cos(angles), torch.sin(angles)
    zero, one = torch.zeros_like(angles), torch.ones_like(angles)
    world = torch.stack((c, zero, s, zero, one, zero, -s, zero, c), -1).reshape(-1, 5, 3, 3)
    return world@orientation, (world@acceleration[..., None]).squeeze(-1)


def preintegrate_gyro(time_s, gyro_sensor_rad_s, grid_s, sensor_from_segment,
                     bias_sensor_rad_s, *, maximum_gap_s=.0075, subdivisions=1):
    """Integrate raw gyro using actual timestamps and a fixed mounting.

    Piecewise-linear rates are evaluated at each subinterval midpoint. Output
    increments map next-frame segment coordinates into the preceding frame:
    R_world_segment[k+1] = R_world_segment[k] @ delta[k]. A raw gap crossing
    invalidates that complete output interval; its placeholder is not evidence.
    """
    if subdivisions not in (1, 2):
        raise ValueError('one or two integration subdivisions required')
    time_s, gyro, grid = [np.asarray(v, dtype=float) for v in (time_s, gyro_sensor_rad_s, grid_s)]
    mounting, bias = [np.asarray(v, dtype=float) for v in (sensor_from_segment, bias_sensor_rad_s)]
    if (time_s.ndim != 1 or len(time_s)<2 or gyro.shape != (len(time_s), 3)
            or grid.ndim != 1 or len(grid)<2 or mounting.shape != (3, 3) or bias.shape != (3,)):
        raise ValueError('invalid gyro preintegration shapes')
    if (not all(np.isfinite(v).all() for v in (time_s, gyro, grid, mounting, bias))
            or np.any(np.diff(time_s)<=0) or np.any(np.diff(grid)<=0)
            or not np.isfinite(maximum_gap_s) or maximum_gap_s<=0):
        raise ValueError('finite inputs and strictly increasing raw/output times required')
    if not (np.allclose(mounting.T@mounting, np.eye(3), atol=1e-8, rtol=0)
            and np.isclose(np.linalg.det(mounting), 1., atol=1e-8, rtol=0)):
        raise ValueError('sensor-from-segment mounting must be a proper rotation')
    if grid[0]<time_s[0] or grid[-1]>time_s[-1]:
        raise ValueError('gyro data must cover the output grid without extrapolation')
    result = np.tile(np.eye(3), (len(grid)-1, 1, 1))
    valid = np.ones(len(result), dtype=bool)
    gaps = np.flatnonzero(np.diff(time_s)>maximum_gap_s)
    for k, (lo, hi) in enumerate(zip(grid[:-1], grid[1:])):
        if np.any((time_s[gaps]<hi) & (time_s[gaps+1]>lo)):
            valid[k] = False
            continue
        begin, end = np.searchsorted(time_s, [lo, hi], side='right')
        inner = time_s[begin:end]
        edges = np.r_[lo, inner[inner<hi], hi]
        if subdivisions == 2:
            edges = np.sort(np.r_[edges, (edges[:-1]+edges[1:])/2])
        dt = np.diff(edges)
        middle = (edges[1:]+edges[:-1])/2
        rates = np.column_stack([np.interp(middle, time_s, gyro[:, j]) for j in range(3)])
        segment_rates = (rates-bias)@mounting
        steps = Rotation.from_rotvec(segment_rates*dt[:, None]).as_matrix()
        for step in steps:
            result[k] = result[k]@step
    return result, valid


def gyro_increment_residual(orientation, increments):
    """Chordal SO(3) residual in the preceding segment frame.

    Frobenius norm divided by sqrt(2) is 2 sin(angle/2), locally radians.
    All components remain visible: yaw freedom cannot conceal tilt mismatch.
    No covariance or acceptance threshold is inferred here.
    """
    if orientation.shape[1:] != (3, 3) or increments.shape != (len(orientation)-1, 3, 3):
        raise ValueError('one gyro increment per adjacent orientation pair required')
    predicted = orientation[:-1].transpose(-1, -2)@orientation[1:]
    return (predicted-increments)/(2.**.5)
