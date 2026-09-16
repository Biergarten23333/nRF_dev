import numpy as np
import pytest
from scipy.spatial.transform import Rotation

from biospur_fusion.c2_uwb_root_world.articulated_correction_slew import (
    CausalArticulatedCorrectionSlew,
)


SEGMENTS = ("upper", "lower")


def _target(upper=0.0, lower=0.0):
    return {
        "upper": np.array([0.0, 0.0, upper]),
        "lower": np.array([lower, 0.0, 0.0]),
    }


def _distance(left, right):
    return float((
        Rotation.from_rotvec(left).inv() * Rotation.from_rotvec(right)
    ).magnitude())


def test_publication_is_causal_and_rate_limited() -> None:
    slew = CausalArticulatedCorrectionSlew(
        segments=SEGMENTS,
        release_period_s=0.12,
        maximum_target_norm_rad=0.18 * np.sqrt(3.0),
    )
    initial = slew.sample(1.0, _target())
    update = slew.sample(1.005, _target(upper=0.18))

    limit = 2.0 * 0.18 * np.sqrt(3.0) * 0.005 / 0.12
    assert initial.step_maximum_rad == 0.0
    assert update.active
    assert update.step_maximum_rad <= limit + 1e-12
    assert _distance(initial.correction_rotvec["upper"],
                     update.correction_rotvec["upper"]) <= limit + 1e-12


def test_stationary_target_is_reached_within_one_release_period() -> None:
    slew = CausalArticulatedCorrectionSlew(
        segments=SEGMENTS,
        release_period_s=0.12,
        maximum_target_norm_rad=0.18 * np.sqrt(3.0),
    )
    slew.sample(0.0, _target())
    target = _target(upper=0.18, lower=-0.12)
    sample = None
    for index in range(1, 25):
        sample = slew.sample(index * 0.005, target)
    assert sample is not None
    np.testing.assert_allclose(sample.correction_rotvec["upper"], target["upper"])
    np.testing.assert_allclose(sample.correction_rotvec["lower"], target["lower"])
    assert not sample.active


def test_retarget_continues_from_last_publication_without_a_jump() -> None:
    slew = CausalArticulatedCorrectionSlew(
        segments=SEGMENTS,
        release_period_s=0.12,
        maximum_target_norm_rad=0.18 * np.sqrt(3.0),
    )
    slew.sample(0.0, _target())
    before = slew.sample(0.06, _target(upper=0.18))
    after = slew.sample(0.06, _target(upper=-0.18))
    assert after.step_maximum_rad == 0.0
    np.testing.assert_allclose(
        after.correction_rotvec["upper"], before.correction_rotvec["upper"]
    )


def test_invalid_inventory_cap_and_time_are_rejected() -> None:
    slew = CausalArticulatedCorrectionSlew(
        segments=SEGMENTS,
        release_period_s=0.12,
        maximum_target_norm_rad=0.2,
    )
    slew.sample(1.0, _target())
    with pytest.raises(ValueError, match="inventory"):
        slew.sample(1.005, {"upper": np.zeros(3)})
    with pytest.raises(ValueError, match="cap"):
        slew.sample(1.005, _target(upper=0.21))
    with pytest.raises(ValueError, match="reversed"):
        slew.sample(0.9, _target())
