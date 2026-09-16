"""Moving ankle proxies must not become obsolete fixed footholds on reentry."""
import numpy as np
import pytest

from biospur_fusion.c2_uwb_root_world.root_input_safety import CausalImuHold
from test_c2_articulated_contact import ankles
from test_c2_contact_conditional_heading import heading_owner, invariant


@pytest.mark.parametrize('restart', [False, True])
def test_articulated_owner_receives_stationary_reentry_policy(restart):
    joint = heading_owner(restart_stationary_episode=restart)
    assert joint.contacts.restart_stationary_episode is restart
    joint.update_contact(ankles(joint), [False, True], [False, False],
                         [1., 1.], .005, [False, True])
    episode = joint.contacts._episodes[1]
    joint.contacts.means += [.024, -.004, .002]
    joint.propagate_safe(CausalImuHold(0., [0., 0., 9.80665], joint.sensor_rotation()), .005)
    joint.observe_imu_base(.005, joint.base.copy())
    before = joint.state
    joint.update_contact(ankles(joint), [False, True], [False, True],
                         [1., 1.], .005, [False, False])
    invariant(before.rotations, joint.state.rotations)
    np.linalg.cholesky(joint.contacts.covariance(joint.tangent()))
    if restart:
        np.testing.assert_array_equal(joint.state.root.vector, before.root.vector)
        np.testing.assert_allclose(joint.contacts.means,
                                   before.root.position_m + ankles(joint)[1], atol=1e-14)
        assert joint.contacts._episodes[1] == episode + 1
        assert joint.contacts.audit[-1][2] == 0  # no fabricated entry observation
        assert joint.contacts.stationary_entries == [(.005, 1)]
    else:
        # This one-tick synthetic point has little independent uncertainty;
        # verify assimilation, not the magnitude of the older real episode.
        assert joint.contacts.audit[-1][2] == 1
        assert not np.array_equal(joint.state.root.vector, before.root.vector)
        assert joint.contacts._episodes[1] == episode

    current_episode = joint.contacts._episodes[1]
    joint.propagate_safe(CausalImuHold(.005, [0., 0., 9.80665], joint.sensor_rotation()), .010)
    joint.observe_imu_base(.010, joint.base.copy())
    joint.update_contact(ankles(joint), [False, True], [False, True],
                         [1., 1.], .005, [False, False])
    assert joint.contacts._episodes[1] == current_episode
    np.linalg.cholesky(joint.contacts.covariance(joint.tangent()))
