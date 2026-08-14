import numpy as np
from biospur_fusion.uwb.frontend import guarded_position_update


def test_rejected_flyers_are_byte_identical():
    for distance in (.5, 1.0, 2.0):
        state = np.zeros(6); covariance = np.eye(6) * .01
        state_before = state.tobytes(); p_before = covariance.tobytes()
        out, out_p, decision = guarded_position_update(
            state, covariance, np.array([distance, 0, 0]), np.eye(3) * .0025)
        assert not decision.accepted and decision.reason == "REJECT_NIS"
        assert out.tobytes() == state_before and out_p.tobytes() == p_before


def test_accepted_update_is_joseph_pd():
    state = np.zeros(6); covariance = np.eye(6) * .01
    out, out_p, decision = guarded_position_update(
        state, covariance, np.array([.01, 0, 0]), np.eye(3) * .0025)
    assert decision.accepted and out[0] > 0
    assert np.allclose(out_p, out_p.T)
    np.linalg.cholesky(out_p)
