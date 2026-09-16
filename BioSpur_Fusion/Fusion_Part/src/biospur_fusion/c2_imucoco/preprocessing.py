"""Explicit frame, unit and time conversion to published IMUCoCo features.

BioSpur world: forward X, left Y, up Z. SMPL world: left X, up Y,
forward Z. The 6D orientation encoding consists of two matrix columns.
"""
from __future__ import annotations

import numpy as np
from scipy.spatial.transform import Rotation, Slerp
from scipy.ndimage import uniform_filter1d

from biospur_fusion.c2_sparse_nodes.inputs import NODES
from biospur_fusion.c2_sparse_nodes.calibration import relative
from biospur_fusion.c2_sparse_nodes.inertial import acceleration, world_sensor

WORLD_TO_SMPL = np.array([[0., 1., 0.], [0., 0., 1.], [1., 0., 0.]])
# In the functional anatomical frame the segment Z axis points proximally.
# Left/right forearms therefore have opposite T-pose reference rotations.
TPOSE_SEGMENTS = Rotation.from_rotvec(np.array([[0., 0., 0.], [np.pi/2, 0., 0.],
    [-np.pi/2, 0., 0.], [0., 0., 0.], [0., 0., 0.]])).as_matrix()


def encode(rotations, linear_acceleration):
    rotations = np.asarray(rotations)
    linear_acceleration = np.asarray(linear_acceleration)
    if rotations.shape[:-2] != linear_acceleration.shape[:-1] or rotations.shape[-2:] != (3, 3):
        raise ValueError('rotation/acceleration shape mismatch')
    if not np.isfinite(rotations).all() or not np.isfinite(linear_acceleration).all():
        raise ValueError('nonfinite model input')
    if not np.allclose(rotations @ np.swapaxes(rotations, -1, -2), np.eye(3), atol=1e-5):
        raise ValueError('input is not a rotation')
    if not np.allclose(np.linalg.det(rotations), 1., atol=1e-5):
        raise ValueError('reflection is not a rotation')
    r6d = rotations[..., :2].swapaxes(-1, -2).reshape(*rotations.shape[:-2], 6)
    return np.concatenate((r6d, linear_acceleration), axis=-1).astype(np.float32)


def prepare_stream(episode, calibration, *, hz=60, mounting='functional', include_bias_transport=False):
    if set(episode) != set(NODES):
        raise ValueError('exactly the five authorized IMUs are required')
    if hz != 60:
        raise ValueError('published model and translation integration require 60 Hz')
    for node in NODES:
        rows = np.asarray(episode[node]['imu'])
        if rows.ndim != 2 or rows.shape[1] != 11 or len(rows) < 2 or not np.isfinite(rows).all():
            raise ValueError('each IMU requires at least two finite timestamped 11-column rows')
        if np.any(np.diff(rows[:, 0]) <= 0):
            raise ValueError('non-monotonic source time')
    lo = max(episode[n]['imu'][0, 0] for n in NODES)
    hi = min(episode[n]['imu'][-1, 0] for n in NODES)
    if hi-lo < 1/hz:
        raise ValueError('five IMUs have no shared replay interval')
    time = lo + np.arange(int(np.floor((hi-lo)*hz))) / hz
    rotations, accelerations = [], []
    bias_transports = []
    valid = np.ones(len(time), dtype=bool)
    if mounting not in ('functional', 'tpose'):
        raise ValueError('unknown mounting calibration')
    reference = TPOSE_SEGMENTS if mounting == 'functional' else np.asarray(calibration['measured_tpose_segments'])
    for i, node in enumerate(NODES):
        rows = episode[node]['imu']
        segment = relative(rows, node, calibration)
        # This is a fixed bone-coordinate conversion, not alignment to a
        # measured ten-node pose or a per-frame visual correction.
        rr = WORLD_TO_SMPL @ segment @ reference[i].T @ WORLD_TO_SMPL.T
        rotations.append(Slerp(rows[:, 0]-lo, Rotation.from_matrix(rr))(time-lo).as_matrix())
        aa = acceleration(rows, node, calibration) @ WORLD_TO_SMPL.T
        # Offline anti-alias filtering at 200 Hz; timestamp stays at the
        # centre. No claim that this frontend is causal.
        aa = uniform_filter1d(aa, size=7, axis=0, mode='nearest')
        accelerations.append(np.column_stack([np.interp(time, rows[:, 0], aa[:, j]) for j in range(3)]))
        if include_bias_transport:
            # A constant sensor-coordinate bias passes through exactly the
            # same rotation, anti-alias filter and interpolation as force.
            # Applying the decimated bone rotation instead is not equivalent.
            response = WORLD_TO_SMPL @ world_sensor(rows, node, calibration)
            response = uniform_filter1d(response, size=7, axis=0, mode='nearest')
            response = response.reshape(len(rows), 9)
            bias_transports.append(np.column_stack([
                np.interp(time, rows[:, 0], response[:, j]) for j in range(9)
            ]).reshape(len(time), 3, 3))
        for k in np.flatnonzero(np.diff(rows[:, 0]) > .025):
            valid &= ~((time >= rows[k, 0]-.02) & (time <= rows[k+1, 0]+.02))
    rr, aa = np.stack(rotations, axis=1), np.stack(accelerations, axis=1)
    result = dict(time_s=time, orientation=rr, acceleration_mps2=aa,
                  features=encode(rr, aa), input_valid=valid)
    if include_bias_transport:
        # a(new_bias) = a(current_bias) - response @ (new_bias-current_bias).
        # Frozen frontend rotations are conditional here, not recalculated
        # navigation attitudes or an independent estimate of bias covariance.
        result['sensor_bias_response'] = np.stack(bias_transports, axis=1)
    return result
