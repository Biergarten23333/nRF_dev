"""Specific-force transport and calibrated IMU lever arms for offline replay.

All vectors use the same world gauge as the retained segment orientations.
No reference pose or missing-node payload is an input to this module.
"""
from __future__ import annotations

import numpy as np
from scipy.optimize import lsq_linear
from scipy.signal import savgol_filter
from scipy.spatial.transform import Rotation

from .calibration import matrices, relative, synchronized
from .inputs import NODES

G = np.array([0., 0., 9.80665])


def world_sensor(rows, node, calibration):
    i = NODES.index(node)
    if 'segment_axes_in_sensor' in calibration:
        return relative(rows,node,calibration)@np.asarray(calibration['segment_axes_in_sensor'])[i].T
    mounting = Rotation.from_euler('z', calibration['functional_yaw_rad'][i]).as_matrix()
    mounting = mounting @ np.asarray(calibration['initial_sensor_rotations'])[i]
    return relative(rows, node, calibration) @ mounting


def acceleration(rows, node, calibration):
    bias = np.asarray(calibration.get('acc_bias_sensor', np.zeros((5, 3))))[NODES.index(node)]
    return np.einsum('nij,nj->ni', world_sensor(rows, node, calibration), rows[:, 5:8]-bias) - G


def smooth(values, samples=41, derivative=0, dt=.005):
    width = min(samples, len(values) if len(values) % 2 else len(values)-1)
    if width < 5:
        raise ValueError('insufficient samples for derivative support')
    return savgol_filter(values, width, 3, deriv=derivative, delta=dt, axis=0)


def fit_lever(rotations_ddot, relative_acc, length, axial_offset=0.):
    """Bounded pivot calibration; transverse offsets have an explicit 3 cm prior."""
    design = rotations_ddot.reshape(-1, 3)
    target = relative_acc.reshape(-1)
    # A constant world residual is nuisance acceleration/bias, not a lever arm.
    matrix = np.column_stack((design, np.tile(np.eye(3), (len(rotations_ddot), 1))))
    regularizer = np.diag([1/.03, 1/.03, 1/.2, 1/.5, 1/.5, 1/.5])
    centre = np.array([0., 0., -axial_offset-length/2, 0., 0., 0.])
    result = lsq_linear(np.vstack((matrix, regularizer)),
        np.r_[target, regularizer@centre],
        bounds=(np.array([-.05,-.05,-axial_offset-length, -2,-2,-2]),
                np.array([.05,.05,-axial_offset-.015, 2,2,2])))
    residual = (matrix@result.x-target).reshape(-1,3)
    return result.x[:3], dict(pivot_residual_rms_mps2=float(np.sqrt(np.mean(residual**2))),
        nuisance_world_acceleration_mps2=result.x[3:].tolist(),
        design_singular_values=np.linalg.svd(design, compute_uv=False).tolist(),
        bound_active=result.active_mask.tolist(), success=bool(result.success))


def calibrate_inertial(episodes, calibration):
    c = dict(calibration)
    biases = []
    for n in NODES:
        rows = episodes['00_initial_still'][n]['imu']
        expected = np.einsum('nji,j->ni', matrices(rows).as_matrix(), G)
        biases.append(np.median(rows[:,5:8]-expected, axis=0).tolist())
    c['acc_bias_sensor'] = biases
    levers, evidence = [], {}
    for i, name in enumerate(('04_shoulder_left','05_shoulder_right','10_knee_left_seated','11_knee_right_seated'),1):
        rows = episodes[name][NODES[i]]['imu']
        root = episodes[name][NODES[0]]['imu']
        time = rows[:,0]-rows[0,0]
        rr = relative(rows,NODES[i],c)
        derivative = smooth(rr, 101, 2)
        acc = smooth(acceleration(rows,NODES[i],c),101)
        root_acc = smooth(acceleration(root,NODES[0],c),101)
        acc -= np.column_stack([np.interp(rows[:,0],root[:,0],root_acc[:,j]) for j in range(3)])
        good = (time>=0)&(time<=30)&(np.linalg.norm(derivative,axis=(1,2))>1.)
        length = c['lengths']['forearm' if i<3 else 'shank']
        offset=c['lengths']['upper_arm'] if i<3 else 0.
        lever, audit = fit_lever(derivative[good],acc[good],length,offset)
        lever[2]+=offset
        levers.append(lever.tolist())
        evidence[NODES[i]] = dict(episode=name, selected_rows=int(good.sum()), **audit,
            assumption=('registered shoulder raise keeps elbow extended; shoulder approximately fixed relative to pelvis'
                if i<3 else 'knee approximately stationary relative to pelvis in seated knee extension'),
            lever_origin='elbow' if i<3 else 'knee',
            upper_arm_length_subtracted=i<3,
            measured_by_tape=False)
    c.update(imu_levers_from_joint_m=levers, lever_calibration=evidence,
        acceleration_role='world specific-force minus gravity; pelvis acceleration subtracted in world frame',
        derivative_role='offline centred filtering; no causal/real-time claim')
    return c


def synchronized_inertial(ep, calibration, hz=20):
    time, rotations, valid = synchronized(ep, calibration, hz)
    force = []
    for n in NODES:
        rows = ep[n]['imu']
        acc = smooth(acceleration(rows,n,calibration))
        force.append(np.column_stack([np.interp(time,rows[:,0],acc[:,j]) for j in range(3)]))
        # Do not let the centred filter bridge a dropped-sample interval.
        for i in np.flatnonzero(np.diff(rows[:,0])>.025):
            valid &= ~((time>=rows[i,0]-.105)&(time<=rows[i+1,0]+.105))
    return time, rotations, np.stack(force,axis=1), valid
