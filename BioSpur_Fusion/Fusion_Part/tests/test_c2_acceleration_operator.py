"""Physical consistency, independent of pose fitting or reference action angles."""
import numpy as np
import torch
import pytest

from biospur_fusion.c2_five_calibration.operators import HZ, filtered, integrated_acceleration


def test_multiscale_matches_integrated_trajectory_at_the_same_time_centres():
    from biospur_fusion.c2_five_calibration.operators import multiscale
    random = np.random.default_rng(921)
    a = random.normal(size=(100, 5, 3))
    position = [random.normal(size=(5, 3))]
    velocity = random.normal(size=(5, 3))
    dt = 1/HZ
    for first, second in zip(a[:-1], a[1:]):
        position.append(position[-1]+dt*velocity+dt**2*(2*first+second)/6)
        velocity = velocity+dt*(first+second)/2
    actual = multiscale(torch.tensor(np.stack(position)))
    expected = multiscale(torch.tensor(a), acceleration=True)
    assert actual.shape == (90, 2, 5, 3)
    torch.testing.assert_close(actual, expected, atol=1e-10, rtol=1e-10)


@pytest.mark.parametrize('frequency', [.5, 3.])
def test_multiscale_retains_slow_and_fast_acceleration(frequency):
    from biospur_fusion.c2_five_calibration.operators import multiscale
    t = torch.arange(600, dtype=torch.float64)/HZ
    acceleration = torch.sin(2*np.pi*frequency*t)[:, None]
    response = multiscale(acceleration, acceleration=True)
    rms = response.square().mean(0).sqrt().flatten()
    # A 0.5 Hz signal survives the long window; the 3 Hz signal is retained
    # by the additional bandwidth. This checks signal recovery, not pose truth.
    assert rms[0 if frequency == .5 else 1] > .3
    if frequency == 3.:
        assert rms[1] > 5*rms[0]


def test_piecewise_linear_acceleration_matches_integrated_trajectory():
    random = np.random.default_rng(104)
    acceleration = random.normal(size=(200, 5, 3))
    positions = [random.normal(size=(5, 3))]
    velocity = random.normal(size=(5, 3))
    dt = 1/HZ
    for a, b in zip(acceleration[:-1], acceleration[1:]):
        positions.append(positions[-1]+dt*velocity+dt**2*(2*a+b)/6)
        velocity = velocity+dt*(a+b)/2
    expected = filtered(torch.tensor(np.stack(positions)), 2)
    actual = integrated_acceleration(torch.tensor(acceleration))
    torch.testing.assert_close(actual, expected, atol=1e-10, rtol=1e-10)


def test_sinusoid_does_not_create_large_spurious_acceleration_residual():
    time = np.arange(800)/HZ
    for frequency in (.25, .5, 1., 1.5, 2.):
        omega = 2*np.pi*frequency
        positions = torch.tensor(np.sin(omega*time)[:, None])
        actual = -omega**2*positions
        predicted = filtered(positions, 2)
        matched = integrated_acceleration(actual)
        # Remaining error is 20 Hz linear interpolation, not the old 43% at 2 Hz.
        assert (predicted-matched).norm()/matched.norm() < .04


def test_offset_design_and_pose_residual_use_the_same_acceleration_operator():
    from biospur_fusion.c2_five_calibration.solver import acceleration_residual, lever_system, valid_support
    from test_c2_five_calibration import geometry
    from scipy.spatial.transform import Rotation
    random = np.random.default_rng(44)
    rotation = Rotation.random(80*24, random_state=random).as_matrix().reshape(80,24,3,3)
    acceleration = random.normal(size=(80,5,3))
    valid = np.ones(80, dtype=bool)
    valid[25] = False
    levers = random.normal(size=(5,3))*.03
    g = geometry()
    matrix, target = lever_system(rotation, acceleration, valid, g)
    residual = acceleration_residual(torch.tensor(rotation), torch.tensor(acceleration), g, levers)
    np.testing.assert_allclose((matrix@levers.ravel()-target),
        residual.numpy()[valid_support(valid)].ravel(), atol=1e-11)
