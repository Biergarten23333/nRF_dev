import numpy as np
import pytest

from biospur_fusion.root_r3.models import RootState
from biospur_fusion.c2_uwb_root_world.support_velocity import update_support_velocity


@pytest.mark.parametrize('field', ['velocity_sigma_mps', 'correlation_time_s', 'maximum_sample_age_s'])
@pytest.mark.parametrize('value', [np.nan, np.inf, 0., -1.])
def test_invalid_configuration(field, value):
    from biospur_fusion.c2_uwb_root_world.support_velocity import SupportVelocityConfig
    with pytest.raises(ValueError):
        SupportVelocityConfig(**{field: value})


def state(v=1.):
    x = np.zeros(9)
    x[3] = v
    p = np.eye(9)
    p[0, 3] = p[3, 0] = .3
    p[6, 3] = p[3, 6] = -.2
    return RootState(0., x, p)


def test_support_changes_velocity_and_correlated_bias_not_overwrite():
    a = state()
    b, _, _ = update_support_velocity(a, [[0, 0, 0]], [1.], .005)
    assert 0 < b.vector[3] < a.vector[3]
    assert b.vector[6] > 0 and b.vector[0] < 0
    np.linalg.cholesky(b.covariance)
    assert b.vector[2] == a.vector[2]


def test_walking_relative_offset_preserves_root_translation():
    a = state()
    b, _, _ = update_support_velocity(a, [[1., 0, 0]], [1.], .005)
    np.testing.assert_array_equal(a.vector, b.vector)


def test_two_identical_feet_not_double_information():
    a = state()
    b, _, _ = update_support_velocity(a, [[0, 0, 0]], [1.], .005)
    c, _, _ = update_support_velocity(a, [[0, 0, 0], [0, 0, 0]], [1., 1.], .005)
    np.testing.assert_array_equal(b.vector, c.vector)
    np.testing.assert_array_equal(b.covariance, c.covariance)


def test_information_rate_invariant():
    finals = []
    for hz in (100, 200, 400):
        a = state()
        for _ in range(hz):
            a, _, _ = update_support_velocity(a, [[0, 0, 0]], [1.], 1 / hz)
        finals.append(a)
    for a in finals[1:]:
        np.testing.assert_allclose(a.vector, finals[0].vector, atol=1e-12)
        np.testing.assert_allclose(a.covariance, finals[0].covariance, atol=1e-12)


def test_conflicting_feet_weaken_observation():
    _, _, a = update_support_velocity(state(), [[0, 0, 0]], [1.], .005)
    _, _, b = update_support_velocity(state(), [[-1, 0, 0], [1, 0, 0]], [1., 1.], .005)
    assert b[0, 0] > a[0, 0]


@pytest.mark.parametrize('dt', [0, -1, np.nan])
def test_invalid_interval(dt):
    with pytest.raises(ValueError):
        update_support_velocity(state(), [[0, 0, 0]], [1.], dt)


def test_vertical_motion_target_preserved_and_bias_reduced():
    a = state(0.)
    a.vector[5] = .4
    unchanged, _, _ = update_support_velocity(a, [[0, 0, .4]], [1.], .005)
    np.testing.assert_array_equal(unchanged.vector, a.vector)
    corrected, _, _ = update_support_velocity(a, [[0, 0, 0]], [1.], .005)
    assert 0 < corrected.vector[5] < .4
    np.linalg.cholesky(corrected.covariance)


def test_pure_position_jump_is_not_claimed_corrected():
    a = state(0.)
    a.vector[0] = 1.
    corrected, _, _ = update_support_velocity(a, [[0, 0, 0]], [1.], .005)
    np.testing.assert_array_equal(corrected.vector, a.vector)


@pytest.mark.parametrize('disturbance', ['pivot', 'landing', 'flight', 'lift'])
def test_adapter_vetoes_motion_even_quiet_fk(tmp_path, disturbance):
    from biospur_fusion.c2_uwb_root_world.continuous_support import ContinuousSupportVelocity
    times = np.arange(-1., .61, .005)
    acc = np.tile([0., 0., 9.81], (len(times), 1))
    acc[:, 2] += .001 * np.sin(np.arange(len(times)))
    gyro = np.tile([.001, .001, .001], (len(times), 1))
    at = np.flatnonzero(times >= .4)[0]
    if disturbance == 'pivot':
        gyro[at:] = 1.
    elif disturbance == 'landing':
        acc[at:] = [0, 0, 15]
    elif disturbance == 'flight':
        acc[at:] = 0
    for node in ('BSF6C53', 'BSF8BC4'):
        np.savez(tmp_path / (node + '.npz'), common_global_ns=np.rint((10 + times) * 1e9).astype('int64'),
                 acc_mps2=acc, gyro_rads=gyro)
    query = np.arange(0., .6, .005)
    points = np.tile([[-.1, 0, -1.], [.1, 0, -1.]], (len(query), 1, 1))
    if disturbance == 'lift':
        points[query >= .405, 0, 2] += .2
    pose = dict(joint_names=['ankle_left', 'ankle_right'], joints_relative=points, time_s=query + 10)
    owner = ContinuousSupportVelocity(tmp_path, pose, 10.)
    for i, t in enumerate(query):
        owner.update(RootState(t, state().vector, state().covariance), i)
    rows = np.array(owner.audit)
    assert np.any(rows[(query > .3) & (query < .39), 1:3])
    if disturbance == 'lift':
        assert not np.any(rows[query > .42, 1])
    else:
        assert not np.any(rows[query > .42, 1:3])


def test_adapter_duplicates_stale_and_future_isolation(tmp_path):
    from biospur_fusion.c2_uwb_root_world.continuous_support import ContinuousSupportVelocity
    times = np.r_[np.arange(-1., .401, .005), 1., 1.005, 2.]
    acc = np.tile([0., 0., 9.81], (len(times), 1))
    acc[:, 2] += .001 * np.sin(np.arange(len(times)))
    gyro = np.tile([.001, .001, .001], (len(times), 1))
    for node in ('BSF6C53', 'BSF8BC4'):
        np.savez(tmp_path / (node + '.npz'), common_global_ns=np.rint((10 + times) * 1e9).astype('int64'),
                 acc_mps2=acc, gyro_rads=gyro)
    query = np.r_[np.arange(0., .401, .005), .401, .402, .5, 1., 1.005]
    pose = dict(joint_names=['ankle_left', 'ankle_right'], time_s=query + 10,
                joints_relative=np.tile([[-.1, 0, -1.], [.1, 0, -1.]], (len(query), 1, 1)))
    owner = ContinuousSupportVelocity(tmp_path, pose, 10.)
    for i, t in enumerate(query):
        before = RootState(t, state().vector, state().covariance)
        after = owner.update(before, i)
        if t in (.401, .402):
            np.testing.assert_array_equal(after.vector, before.vector)
            assert owner.support_valid.all()
        if t >= .5:
            assert not owner.support_valid.any()
    profiles = owner.profiles
    for node in ('BSF6C53', 'BSF8BC4'):
        acc[-1], gyro[-1] = 1000., 1000.
        np.savez(tmp_path / (node + '.npz'), common_global_ns=np.rint((10 + times) * 1e9).astype('int64'),
                 acc_mps2=acc, gyro_rads=gyro)
    assert ContinuousSupportVelocity(tmp_path, pose, 10.).profiles == profiles
