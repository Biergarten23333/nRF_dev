import numpy as np
import pytest
from biospur_fusion.c2_uwb_root_world.finite_horizon_release import FiniteHorizonRootRelease


def test_initial_single_and_expiry():
    r = FiniteHorizonRootRelease(.12)
    np.testing.assert_array_equal(r.sample(0, [1, 2, 3], [0, 0, 0]).position_m, [1, 2, 3])
    r.install(.1, [6, 0, 0])
    np.testing.assert_allclose(r.sample(.1, [7, 2, 3], [0, 0, 0]).position_m, [1, 2, 3])
    np.testing.assert_allclose(r.sample(.16, [7, 2, 3], [0, 0, 0]).position_m, [4, 2, 3])
    result = r.sample(.23, [7, 2, 3], [0, 0, 0])
    np.testing.assert_array_equal(result.position_m, [7, 2, 3])
    assert result.active_correction_count == 0


def test_overlap_signed_cancellation_and_irregular_sampling():
    r = FiniteHorizonRootRelease(1)
    r.install(0, [4, 0, 0])
    r.install(.2, [-4, 0, 0])
    result = r.sample(.37, [0, 0, 0], [0, 0, 0])
    np.testing.assert_allclose(result.withheld_correction_m, [-.8, 0, 0])
    np.testing.assert_array_equal(result.release_velocity_mps, [0, 0, 0])
    assert result.active_correction_count == 2
    assert r.sample(1.1, [0, 0, 0], [0, 0, 0]).active_correction_count == 1
    np.testing.assert_array_equal(r.sample(1.3, [0, 0, 0], [0, 0, 0]).position_m, [0, 0, 0])


def test_cannot_publish_before_installed_event_or_rewind():
    r = FiniteHorizonRootRelease()
    r.install(2, [1, 0, 0])
    with pytest.raises(ValueError):
        r.sample(1, [0, 0, 0], [0, 0, 0])
    r.sample(2.2, [1, 0, 0], [0, 0, 0])
    with pytest.raises(ValueError):
        r.install(2.1, [1, 0, 0])
