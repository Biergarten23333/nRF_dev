import numpy as np
import pytest

from biospur_fusion.c2_uwb_root_world.root_correction_slew import (
    CausalRootCorrectionSlew,
)


def _slew() -> CausalRootCorrectionSlew:
    return CausalRootCorrectionSlew(
        release_period_s=0.12,
        maximum_correction_m=0.05,
    )


def test_accepted_correction_is_continuous_then_fully_released() -> None:
    slew = _slew()
    before = slew.sample(0.995, np.zeros(3), np.zeros(3))
    slew.install(1.0, np.array([0.05, 0.0, 0.0]))
    at_update = slew.sample(1.0, np.array([0.05, 0.0, 0.0]), np.zeros(3))
    halfway = slew.sample(1.06, np.array([0.05, 0.0, 0.0]), np.zeros(3))
    complete = slew.sample(1.12, np.array([0.05, 0.0, 0.0]), np.zeros(3))

    np.testing.assert_allclose(before.position_m, [0.0, 0.0, 0.0])
    np.testing.assert_allclose(at_update.position_m, before.position_m)
    np.testing.assert_allclose(halfway.position_m, [0.025, 0.0, 0.0])
    np.testing.assert_allclose(complete.position_m, [0.05, 0.0, 0.0])
    np.testing.assert_allclose(
        halfway.velocity_mps, [0.05 / 0.12, 0.0, 0.0]
    )
    np.testing.assert_allclose(complete.velocity_mps, np.zeros(3))
    assert complete.active_correction_count == 0


def test_overlapping_corrections_superpose_without_position_jump() -> None:
    slew = _slew()
    slew.install(1.0, np.array([0.03, 0.0, 0.0]))
    first = slew.sample(1.06, np.array([0.03, 0.0, 0.0]), np.zeros(3))
    slew.install(1.06, np.array([0.0, -0.02, 0.0]))
    second = slew.sample(1.06, np.array([0.03, -0.02, 0.0]), np.zeros(3))

    np.testing.assert_allclose(first.position_m, second.position_m)
    assert second.active_correction_count == 2


def test_many_overlapping_corrections_share_one_release_speed_cap() -> None:
    slew = _slew()
    slew.sample(0.0, np.zeros(3), np.zeros(3))
    posterior = np.zeros(3)
    for index in range(10):
        posterior[0] += 0.04
        slew.install(0.005 * index, np.array([0.04, 0.0, 0.0]))
        sample = slew.sample(0.005 * index, posterior, np.zeros(3))
    prior_position = sample.position_m.copy()
    posterior[0] += 0.04
    slew.install(0.05, np.array([0.04, 0.0, 0.0]))
    following = slew.sample(0.05, posterior, np.zeros(3))

    published_speed = np.linalg.norm(following.position_m - prior_position) / 0.005
    assert published_speed <= slew.maximum_release_speed_mps + 1e-12


def test_owner_approved_constraint_can_exceed_uwb_influence_cap() -> None:
    slew = _slew()
    slew.sample(1.0, np.zeros(3), np.zeros(3))
    slew.install(
        1.0,
        np.array([0.08, 0.0, 0.0]),
        enforce_filter_influence_cap=False,
    )
    at_update = slew.sample(1.0, np.array([0.08, 0.0, 0.0]), np.zeros(3))
    np.testing.assert_allclose(at_update.position_m, np.zeros(3))


@pytest.mark.parametrize(
    ("time_s", "delta", "message"),
    [
        (1.0, [0.051, 0.0, 0.0], "influence cap"),
        (1.0, [np.nan, 0.0, 0.0], "finite"),
    ],
)
def test_invalid_correction_is_rejected(time_s, delta, message) -> None:
    slew = _slew()
    with pytest.raises(ValueError, match=message):
        slew.install(time_s, np.asarray(delta))


def test_time_reversal_is_rejected() -> None:
    slew = _slew()
    slew.sample(1.0, np.zeros(3), np.zeros(3))
    with pytest.raises(ValueError, match="reversed time"):
        slew.install(0.9, np.zeros(3))
