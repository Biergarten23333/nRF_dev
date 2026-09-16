from types import SimpleNamespace

import numpy as np
import pytest

from biospur_fusion.c2_uwb_root_world.world_support_authority import WorldSupportGrant,WorldSupportAuthority
from biospur_fusion.c2_uwb_root_world.continuous_support import ContinuousSupportVelocity
from biospur_fusion.c2_uwb_root_world.support_velocity import SupportVelocityConfig
from test_c2_contact_conditional_heading import heading_owner
from test_c2_articulated_contact import ankles


def adapter(grants):
    s=ContinuousSupportVelocity.__new__(ContinuousSupportVelocity)
    s.points=None;s.world_authority=WorldSupportAuthority(grants)
    s.advance_actual_samples=lambda t:None
    s.protocol=SimpleNamespace(model=SimpleNamespace(support_context=True),
        evidence=lambda t:(np.ones(2,bool),np.ones(2),np.zeros(2,int)),
        position_evidence=lambda t:(np.ones(2,bool),np.ones(2),np.zeros(2,bool)))
    return s


@pytest.mark.parametrize('route',['stationary','soft','bridge'])
def test_raw_position_guard_requires_same_side_world_authority(route):
    from biospur_fusion.c2_uwb_root_world.protocol_contact import ProtocolContactStream
    grant=WorldSupportGrant(0,0.,1.,.1,'PRIOR','one')
    s=adapter((grant,))
    quiet=np.array([False,True])
    s.protocol.evidence=lambda t:(quiet if route=='stationary' else np.zeros(2,bool),None,None)
    s.protocol.position_evidence=lambda t:(quiet,None,None)
    s.soft_support_position_guard=route=='soft'
    s.points=object() if route=='soft' else None
    s.rotation_position_bridge=route=='bridge'
    s.protocol.rotation_bridge={side:SimpleNamespace(evidence=lambda t,i=i:bool(quiet[i]))
                               for i,side in enumerate(('left','right'))}
    s.protocol.rotation_position_evidence=lambda t,**kw:ProtocolContactStream.rotation_position_evidence(s.protocol,t,**kw)
    assert not s.position_is_considered(.5)  # left grant cannot authorize right evidence
    quiet[:]=[True,False]
    assert not s.position_is_considered(.05)  # not available yet
    assert s.position_is_considered(.5)
    assert not s.position_is_considered(1.)  # exact exclusive expiry
    s.world_authority=WorldSupportAuthority(())
    assert not s.position_is_considered(.5)
    s.world_authority=None  # legacy keeps the original classifier-only predicate
    assert s.position_is_considered(.5)


def test_raw_quiet_twenty_mps_without_grant_never_masks_position():
    j=heading_owner();x=j.state.root.vector.copy();x[3]=20.
    j._install(x,j.state.rotations,j.state.covariance)
    s=adapter(())
    assert not s.position_is_considered(.5)
    np.testing.assert_array_equal(j.state.root.vector,x)
    s.world_authority=None
    assert s.position_is_considered(.5)


def test_historical_raw_query_does_not_change_clock_grants_or_anchors():
    s=adapter((WorldSupportGrant(0,0.,1.,0.,'PRIOR','first'),
               WorldSupportGrant(1,1.,2.,1.,'PRIOR','second')))
    released=[]
    points=SimpleNamespace(sides=(1,),release=released.append)
    s.world_authority.apply(points,1.5,[True,True],[True,True])
    previous=s.world_authority.previous.copy();before=list(released)
    assert s.position_is_considered(.5)
    assert s.world_authority.mask_at(.5).tolist()==[True,False]
    assert s.world_authority.last_time==1.5
    assert s.world_authority.previous==previous and released==before
    with pytest.raises(ValueError,match='finite'):s.world_authority.mask_at(np.nan)
    grant=s.world_authority.grants[0]
    with pytest.raises(ValueError,match='overlapping'):WorldSupportAuthority((grant,grant)).mask_at(.5)


def test_quiet_sliding_without_world_authority_does_not_brake_or_anchor():
    j=heading_owner();x=j.state.root.vector.copy();x[3]=20.
    j._install(x,j.state.rotations,j.state.covariance)
    s=adapter(())
    stationary,conf,classes,valid,pconf,moving=s.world_evidence(0.,j.contacts)
    j.update_stationary_velocity(stationary,conf,.005,SupportVelocityConfig())
    j.update_contact(ankles(j),valid,valid,pconf,.005,moving)
    np.testing.assert_array_equal(j.state.root.vector,x)
    assert not j.contacts.sides and not stationary.any()
    assert (classes==0).all() and s.protocol.evidence(0)[0].all()


def test_grant_is_side_specific_available_and_expires_without_reusing_anchor():
    grant=WorldSupportGrant(0,0.,1.,.1,'EXTERNAL_NO_SLIP_EVIDENCE','first')
    renewed=WorldSupportGrant(0,1.2,2.,1.2,'EXTERNAL_NO_SLIP_EVIDENCE','second')
    s=adapter((grant,renewed));j=heading_owner()
    assert not s.world_evidence(.05,j.contacts)[0].any()
    valid,conf,_,point,pconf,moving=s.world_evidence(.1,j.contacts)
    assert valid.tolist()==[True,False]
    j.update_contact(ankles(j),point,point,pconf,.005,moving)
    old_episode=j.contacts._episodes[0]
    assert not s.world_evidence(1.,j.contacts)[0].any() and not j.contacts.sides
    x=j.state.root.vector.copy();x[2]+=.2;j._install(x,j.state.rotations,j.state.covariance)
    valid,conf,_,point,pconf,moving=s.world_evidence(1.2,j.contacts)
    j.update_contact(ankles(j),point,point,pconf,.005,moving)
    np.testing.assert_array_equal(j.state.root.vector,x)
    assert j.contacts._episodes[0]>old_episode
    np.testing.assert_allclose(j.contacts.means,j.state.root.position_m+ankles(j)[0])
    with pytest.raises(ValueError,match='monotonic'):s.world_evidence(.9,j.contacts)


def test_explicit_swing_veto_wins_over_world_prior():
    s=adapter((WorldSupportGrant(0,0.,1.,0.,'PROTOCOL_PRIOR','episode'),))
    s.protocol.evidence=lambda t:(np.zeros(2,bool),np.zeros(2),np.ones(2,int))
    s.protocol.position_evidence=lambda t:(np.zeros(2,bool),np.zeros(2),np.zeros(2,bool))
    j=heading_owner();stationary,_,classes,point,_,_=s.world_evidence(0,j.contacts)
    assert not stationary.any() and not point.any() and (classes==1).all()


def test_overlapping_grants_fail_before_mutation():
    grant=WorldSupportGrant(0,0.,1.,0.,'PROTOCOL_PRIOR','one')
    s=adapter((grant,grant));j=heading_owner();before=j.state.root.vector.copy()
    with pytest.raises(ValueError,match='overlapping'):s.world_evidence(0.,j.contacts)
    np.testing.assert_array_equal(j.state.root.vector,before)


def test_skipped_gap_with_reused_label_rebirths_world_reference():
    s=adapter((WorldSupportGrant(0,0.,1.,0.,'PRIOR','same-label'),
               WorldSupportGrant(0,2.,3.,2.,'PRIOR','same-label')))
    j=heading_owner()
    _,_,_,valid,conf,moving=s.world_evidence(.5,j.contacts)
    j.update_contact(ankles(j),valid,valid,conf,.005,moving)
    episode=j.contacts._episodes[0]
    x=j.state.root.vector.copy();x[2]+=.3;j._install(x,j.state.rotations,j.state.covariance)
    # No query inside the intervening gap: interval identity must still revoke.
    _,_,_,valid,conf,moving=s.world_evidence(2.5,j.contacts)
    assert not j.contacts.sides
    j.update_contact(ankles(j),valid,valid,conf,.005,moving)
    assert j.contacts._episodes[0]>episode
    np.testing.assert_array_equal(j.state.root.vector,x)
