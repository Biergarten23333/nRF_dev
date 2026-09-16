import numpy as np
from biospur_fusion.c2_uwb_root_world.contact_lifecycle import NavigationPositionHandoff


def test_no_sample_expiry_not_query_clock_or_tag_reset():
    o=NavigationPositionHandoff();o.advance(0.,.0075)
    queries=[.01,.05,.2,.001,.1,.01]
    values=[o.gain(q) for q in queries]
    np.testing.assert_allclose(values,[.02,.34,1.,0.,.74,.02])
    assert len(o.intervals)==1


def test_explicit_motion_ends_contact_before_gain_restoration():
    o=NavigationPositionHandoff();o.advance(0.,.0075)
    o.advance(.005,.005) # explicit veto, no continuing contact
    assert o.gain(.005)==0.
    assert np.isclose(o.gain(.010),.04)
    assert o.gain(.130)==1.


def test_aggregate_surviving_side_does_not_restart_and_history_is_pure():
    o=NavigationPositionHandoff();o.advance(0.,.0075)
    o.advance(.005,.0125) # other side still protects
    o.advance(.01,.015) # only last surviving side expiry matters
    assert o.gain(.013)==0.
    assert np.isclose(o.gain(.02),.04)
    before=o.gain(.012)
    o.advance(.03,.0375) # reentry after real unprotected gap
    assert o.gain(.012)==before
    assert np.isclose(o.gain(.02),.04)
    assert o.gain(.03)==0.


def test_default_unprotected_never_attenuated_and_full_recovery():
    o=NavigationPositionHandoff()
    o.advance(0.,0.);o.advance(.005,.005)
    assert o.gain(.006)==1.
    o.advance(.01,.0175)
    for t in np.arange(.02,.2,.005):
        assert np.isclose(o.gain(t),min(1.,(t-.0175)/.125))


def test_invalid_sweep_explicit_unit_gain_keeps_rejection_and_state():
    from dataclasses import replace
    from test_c2_correction_budget import fixture,args
    from biospur_fusion.c2_uwb_root_world.root_input_safety import guarded_raw_update
    state,row=fixture();row=replace(row,valid_mask=0)
    updated,decision,_,_=guarded_raw_update(state,row,**args(),gain_scale=1.,correction_gain_scope='position-only')
    assert not decision.accepted
    np.testing.assert_array_equal(updated.vector,state.vector)
    np.testing.assert_array_equal(updated.covariance,state.covariance)
