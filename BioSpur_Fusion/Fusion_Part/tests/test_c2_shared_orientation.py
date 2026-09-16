"""Calibration heading transport must agree with the actual frontend replay."""
import copy

import numpy as np
import pytest
import torch
from scipy.spatial.transform import Rotation

from biospur_fusion.c2_five_calibration.frontend import prepare
from biospur_fusion.c2_five_calibration.shared_orientation import (
    transport_heading, with_heading_increment,
)
from biospur_fusion.c2_sparse_nodes.inputs import NODES


def inputs():
    rng = np.random.default_rng(54)
    observed = torch.from_numpy(Rotation.random(85, random_state=rng).as_matrix().reshape(17, 5, 3, 3))
    acceleration = torch.from_numpy(rng.normal(size=(17, 5, 3)))
    return observed, acceleration


def test_heading_matches_independent_rotation_and_preserves_sensor_force():
    observed, acceleration = inputs()
    delta = torch.tensor([.31, -.48, .17, -.26], dtype=observed.dtype)
    result, force = transport_heading(observed, acceleration, delta)
    independent = Rotation.from_rotvec(np.column_stack((np.zeros(5), np.r_[0., delta.numpy()], np.zeros(5)))).as_matrix()
    np.testing.assert_allclose(result, independent @ observed.numpy(), atol=1e-14)
    np.testing.assert_allclose(force, (independent @ acceleration.numpy()[..., None])[..., 0], atol=1e-14)
    torch.testing.assert_close(result[:, 0], observed[:, 0], rtol=0, atol=0)
    torch.testing.assert_close(force[:, 0], acceleration[:, 0], rtol=0, atol=0)
    torch.testing.assert_close(result.transpose(-1, -2) @ force[..., None],
                               observed.transpose(-1, -2) @ acceleration[..., None])
    torch.testing.assert_close(force[..., 1], acceleration[..., 1], rtol=0, atol=0)
    torch.testing.assert_close(result[:-1].transpose(-1, -2) @ result[1:],
                               observed[:-1].transpose(-1, -2) @ observed[1:])


def test_zero_transport_identity_and_finite_derivative():
    observed, acceleration = inputs()
    delta = torch.zeros(4, dtype=observed.dtype, requires_grad=True)
    result, force = transport_heading(observed, acceleration, delta)
    torch.testing.assert_close(result, observed, rtol=0, atol=0)
    torch.testing.assert_close(force, acceleration, rtol=0, atol=0)
    assert torch.autograd.gradcheck(lambda d: transport_heading(observed, acceleration, d),
                                    (delta,), eps=1e-6, atol=1e-8, rtol=1e-5)


def test_shared_transport_agrees_with_reencoded_raw_frontend():
    rng = np.random.default_rng(64)
    times = np.arange(501) / 200
    episode = {}
    for i, node in enumerate(NODES):
        vector = np.column_stack((.2*np.sin(times), .3*np.cos(times*.6+i), times*.1))
        rotation = Rotation.from_rotvec(vector)
        rows = np.zeros((len(times), 11))
        rows[:, 0] = times + 237000.
        rows[:, 1:5] = rotation.as_quat()[:, [3, 0, 1, 2]]
        rows[:, 5:8] = rng.normal(size=(len(times), 3)) + [0., 0., 9.80665]
        episode[node] = dict(imu=rows)
    c = dict(pelvis_closure_rad=0., functional_yaw_rad=[.1, -.2, .3, .05, -.07],
             initial_sensor_rotations=Rotation.random(5, random_state=rng).as_matrix().tolist(),
             segment_axes_in_sensor=Rotation.random(5, random_state=rng).as_matrix().tolist(),
             frozen_heading_correction_rad={n:.03*i for i,n in enumerate(NODES[1:])},
             acc_bias_sensor=np.zeros((5,3)).tolist())
    g = dict(bone_frame_correction=Rotation.random(5, random_state=rng).as_matrix().tolist())
    original = copy.deepcopy(c)
    before = prepare(episode, c, g)
    delta = np.array([.2, -.3, -.12, .16])
    updated = with_heading_increment(c, delta)
    after = prepare(episode, updated, g)
    r, a = transport_heading(torch.from_numpy(before['orientation']),
                              torch.from_numpy(before['acceleration_mps2']), torch.from_numpy(delta))
    np.testing.assert_allclose(after['orientation'], r, atol=2e-14)
    np.testing.assert_allclose(after['acceleration_mps2'], a, atol=2e-14)
    np.testing.assert_array_equal(after['time_s'], before['time_s'])
    np.testing.assert_array_equal(after['input_valid'], before['input_valid'])
    assert not np.array_equal(after['features'], before['features'])
    assert c == original
    assert not updated['calibration_accepted']


@pytest.mark.parametrize('delta', [np.zeros(5), np.zeros((18, 4)), np.array([0., 0., 0., np.nan])])
def test_transport_rejects_wrong_shape_or_nonfinite_parameters(delta):
    observed, acceleration = inputs()
    with pytest.raises(ValueError):
        transport_heading(observed, acceleration, torch.from_numpy(delta))
    with pytest.raises(ValueError):
        with_heading_increment({}, delta)


def test_framewise_transport_is_not_a_constant_frontend_parameter():
    observed, acceleration = inputs()
    delta = torch.zeros(len(observed), 4, dtype=observed.dtype)
    r, a = transport_heading(observed, acceleration, delta)
    torch.testing.assert_close(r, observed)
    torch.testing.assert_close(a, acceleration)
    with pytest.raises(ValueError, match='constant'):
        with_heading_increment({}, delta.numpy())


def test_removed_node_frontend_is_rejected():
    with pytest.raises(ValueError, match='complete frozen'):
        with_heading_increment({'frozen_heading_correction_rad':{n:0. for n in (*NODES[1:], 'REMOVED')}}, np.zeros(4))


def test_total_increment_cannot_accumulate_on_an_old_proposal():
    baseline = {'frozen_heading_correction_rad':{n:0. for n in NODES[1:]}}
    proposal = with_heading_increment(baseline, np.ones(4)*.1)
    with pytest.raises(ValueError, match='original baseline'):
        with_heading_increment(proposal, np.ones(4)*.2)
