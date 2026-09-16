"""Global elapsed-time gain and actual-gain covariance consistency."""
import math
import numpy as np
import pytest
from biospur_fusion.c2_uwb_root_world.correction_budget import GlobalCorrectionBudget
from biospur_fusion.c2_uwb_root_world.tight_range import update_raw_ranges, linearize_raw_range_factors
from biospur_fusion.c2_uwb_root_world.root_input_safety import guarded_raw_update
from test_c2_support_position_guard import fixture, args


def test_global_clock_consumes_rejections_and_resets_gaps():
    owner = GlobalCorrectionBudget(.25)
    assert owner.consume(1.) == (0., 0.)
    assert owner.consume(1.) == (0., 0.)
    owner.consume(1.01)  # rejected raw event still consumes its interval
    alpha, dt = owner.consume(1.02)
    assert dt == pytest.approx(.01)
    assert alpha == pytest.approx(1-math.exp(-.01/.25))
    assert owner.consume(2.)[0] == 0.
    assert owner.consume(2.01)[0] == pytest.approx(alpha)
    with pytest.raises(ValueError):
        owner.consume(1.)


def test_ten_tags_share_one_elapsed_budget():
    owner = GlobalCorrectionBudget(.75)
    owner.consume(0.)
    complement = 1.
    for epoch in np.arange(1, 11) * .01:
        complement *= 1-owner.consume(float(epoch))[0]
    assert complement == pytest.approx(math.exp(-.1/.75))


@pytest.mark.parametrize('value', [0., -1., float('nan'), float('inf')])
def test_invalid_budget(value):
    with pytest.raises(ValueError):
        GlobalCorrectionBudget(value)


@pytest.mark.parametrize('consider', [False, True])
def test_scaled_full_prior_gain_and_joseph(consider):
    before, row = fixture()
    after, decision = update_raw_ranges(before, row, gain_scale=.1, consider_position=consider, **args())
    assert decision.accepted
    factors = linearize_raw_range_factors(before, row, **args())
    h, p = factors.state_jacobian, before.covariance
    r = np.diag(np.square(decision.sigma_m)/decision.robust_weights)
    k = np.linalg.solve(h @ p @ h.T+r, h @ p).T
    if consider:
        k[:3] = 0.
    k *= .1
    np.testing.assert_allclose(after.vector, before.vector+k@factors.innovations_m, atol=1e-14)
    ikh = np.eye(9)-k@h
    np.testing.assert_allclose(after.covariance, ikh@p@ikh.T+k@r@k.T, atol=1e-14)
    np.linalg.cholesky(after.covariance)
    assert np.linalg.norm(after.vector[3:]-before.vector[3:]) > 1e-5
    if consider:
        np.testing.assert_array_equal(after.position_m, before.position_m)


def test_zero_gain_is_exact_no_information_or_state_update():
    before, row = fixture()
    after, decision = update_raw_ranges(before, row, gain_scale=0., **args())
    assert decision.accepted  # admitted observation, explicit zero state correction
    assert after is before


def test_disabled_exact_and_admission_unchanged():
    before, row = fixture()
    a, _ = update_raw_ranges(before, row, **args())
    b, _ = update_raw_ranges(before, row, gain_scale=None, **args())
    np.testing.assert_array_equal(a.vector, b.vector)
    np.testing.assert_array_equal(a.covariance, b.covariance)
    _, _, nis_a, limit_a = guarded_raw_update(before, row, **args())
    _, _, nis_b, limit_b = guarded_raw_update(before, row, gain_scale=.1, **args())
    assert nis_a == nis_b and limit_a == limit_b
