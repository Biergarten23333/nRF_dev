import numpy as np
import pytest
import torch
from scipy.spatial.transform import Rotation

from biospur_fusion.c2_five_calibration.support_acceleration import point_acceleration, stationary_support_mask
from test_c2_joint_kinematics import geometry


def test_exact_stationary_point_with_moving_pelvis_and_nonzero_sensor_lever():
    g = geometry()
    t = np.arange(81)/20
    omega = .7
    r = Rotation.from_rotvec(np.column_stack((omega*t, np.zeros((len(t),2))))).as_matrix()
    rotations = torch.tensor(np.repeat(r[:,None],24,axis=1))
    offsets = np.asarray(g['rest_offsets_m'])
    foot = sum(offsets[i] for i in (2,5,8)) + np.array([0.,-.04,.18])
    lever = np.array([0.,.08,.12])
    v = foot-lever
    # Translate the whole rotating chain to keep this material point fixed.
    # a(sensor) = -d²[R(t)(foot-lever)]/dt², analytic constant angular speed.
    acceleration = torch.tensor(omega**2 * np.einsum('tij,j->ti',r,v*np.array([0.,1.,1.])))
    residual = point_acceleration(rotations,acceleration,g,lever,joints=[8],local_points=[[0.,-.04,.18]])
    # The production operator assumes piecewise-linear acceleration. Bound
    # interpolation error of this smooth sinusoid by h² max|a''| / 8.
    interpolation_bound = (1/20)**2 * omega**4 * np.linalg.norm(v[1:]) / 8
    assert residual.abs().max() < interpolation_bound
    wrong = point_acceleration(rotations,acceleration,g,np.zeros(3),joints=[8],local_points=[[0.,-.04,.18]])
    assert wrong.square().mean().sqrt() > .03


def test_support_windows_reject_flight_and_time_gaps():
    t = np.arange(40)/20
    valid = np.ones(40,bool)
    stationary = np.ones((40,2),bool)
    stationary[17,0] = False
    t[25:] += .1
    mask = stationary_support_mask(t,valid,stationary)
    for start in range(len(mask)):
        assert mask[start,0] == (not start<=17<start+11 and not start<25<start+11)
        assert mask[start,1] == (not start<25<start+11)
    with pytest.raises(ValueError,match='boolean'):
        stationary_support_mask(t,valid,stationary.astype(float))
