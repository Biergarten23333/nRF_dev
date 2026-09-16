"""Schmidt-style support handling without deterministic-position assumptions."""
from collections import deque
import numpy as np

from biospur_fusion.root_r3.models import RootState
from biospur_fusion.root_r3.estimator import RootFilterConfig, propagate_inertial
from biospur_fusion.c2_uwb_root_world.tight_range import update_raw_ranges, linearize_raw_range_factors
from biospur_fusion.c2_uwb_root_world.root_input_safety import guarded_raw_update
from biospur_fusion.c2_uwb_root_world.continuous_support import ContinuousSupportVelocity
from biospur_fusion.c2_uwb_root_world.support_velocity import SupportVelocityConfig
from test_c2_continuous_full_state_feedback import ANCHORS, CLOCK, row_at


def fixture():
    seed = RootState(0., np.r_[[1.9, 1.3, 1.], np.zeros(6)], np.eye(9) * .1)
    prior, _ = propagate_inertial(seed, .5, np.array([0., 0., 9.80665]), np.eye(3), RootFilterConfig())
    return prior, row_at(.5, np.array([2., 1.3, 1.]))


def args():
    return dict(anchors_m=ANCHORS, clock=CLOCK, tag_offset_world_m=np.zeros(3),
                tag_offset_velocity_world_mps=np.zeros(3))


def test_consider_gain_preserves_p_mean_and_covariance_with_v_bias_feedback():
    before, row = fixture()
    after, d = update_raw_ranges(before, row, consider_position=True, **args())
    assert d.accepted
    np.testing.assert_array_equal(before.position_m, after.position_m)
    np.testing.assert_allclose(before.covariance[:3, :3], after.covariance[:3, :3], atol=1e-14)
    assert np.linalg.norm(after.vector[3:6] - before.vector[3:6]) > 1e-4
    assert np.linalg.norm(after.vector[6:9] - before.vector[6:9]) > 1e-4
    f = linearize_raw_range_factors(before, row, **args())
    h, p = f.state_jacobian, before.covariance
    r = np.diag(np.square(d.sigma_m) / d.robust_weights)
    k = np.linalg.solve(h @ p @ h.T + r, h @ p).T
    k[:3] = 0.
    ikh = np.eye(9) - k @ h
    np.testing.assert_allclose(after.covariance, ikh @ p @ ikh.T + k @ r @ k.T, atol=1e-14)
    np.linalg.cholesky(after.covariance)
    post = linearize_raw_range_factors(after, row, **args())
    np.testing.assert_array_equal(d.innovations_m, post.innovations_m)


def test_disabled_is_exact_original_and_guard_nis_unchanged():
    before, row = fixture()
    a, _ = update_raw_ranges(before, row, **args())
    b, _ = update_raw_ranges(before, row, consider_position=False, **args())
    np.testing.assert_array_equal(a.vector, b.vector)
    np.testing.assert_array_equal(a.covariance, b.covariance)
    _, _, nis_a, limit_a = guarded_raw_update(before, row, **args())
    _, _, nis_b, limit_b = guarded_raw_update(before, row, consider_position=True, **args())
    assert nis_a == nis_b and limit_a == limit_b


def test_support_release_stale_and_future_context():
    owner = ContinuousSupportVelocity.__new__(ContinuousSupportVelocity)
    owner.protocol = None
    owner.config = SupportVelocityConfig()
    owner.support_history = deque([(1., np.array([True, False]), np.array([1., 1.]))])
    assert owner.position_is_considered(1.001)
    assert not owner.position_is_considered(.999)
    assert not owner.position_is_considered(1.008)
    owner.support_history.append((1.005, np.array([False, False]), np.array([1.005, 1.005])))
    assert not owner.position_is_considered(1.006)
    assert owner.position_is_considered(1.004)


def test_unconstrained_position_update_restored_after_release():
    before, row = fixture()
    locked, _ = update_raw_ranges(before, row, consider_position=True, **args())
    released, _ = update_raw_ranges(before, row, consider_position=False, **args())
    assert np.linalg.norm(locked.position_m - before.position_m) == 0
    assert np.linalg.norm(released.position_m - before.position_m) > .01
