import numpy as np
import pytest
from biospur_fusion.c2_native200_calibration.correction_publication import publish_heading_correction


def test_new_calibration_target_cannot_retroactively_move_state():
    t=np.arange(0,2,.005); target=np.where(t>=1,np.pi/2,0.)
    y,_=publish_heading_correction(t,target,settling_s=1.)
    assert y[200]==0
    np.testing.assert_allclose(y[201],(1-np.exp(-.005))*np.pi/2,atol=1e-14)
    assert np.max(np.diff(y))<np.radians(.5)


def test_chunk_state_matches_whole_and_crosses_pi_short_way():
    t=np.arange(0,2,.005); target=np.where(t>=.5,np.radians(-179),np.radians(179))
    whole,_=publish_heading_correction(t,target,settling_s=.3)
    a,state=publish_heading_correction(t[:137],target[:137],settling_s=.3)
    b,_=publish_heading_correction(t[137:],target[137:],settling_s=.3,state=state)
    np.testing.assert_array_equal(whole,np.r_[a,b])
    assert whole[-1]>np.pi
    assert np.max(abs(np.diff(whole)))<np.radians(.04)


def test_constant_correction_has_no_motion_filtering_effect():
    t=np.array([0.,.005,.01,.02]); target=np.full(4,.3)
    result,_=publish_heading_correction(t,target,settling_s=1.)
    np.testing.assert_array_equal(result,target)
    # A motion trajectory composed with this correction is unchanged; there
    # is no access to or filtering of that trajectory in this state owner.


def test_invalid_clock_and_time_constant_rejected():
    with pytest.raises(ValueError):
        publish_heading_correction([0.,0.],[0.,1.],settling_s=1.)
    with pytest.raises(ValueError):
        publish_heading_correction([0.,.005],[0.,1.],settling_s=0.)
    _,state=publish_heading_correction([0.,.005],[0.,1.],settling_s=1.)
    with pytest.raises(ValueError):
        publish_heading_correction([.005],[1.],settling_s=1.,state=state)
