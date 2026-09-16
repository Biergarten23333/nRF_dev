import numpy as np
import pytest
from biospur_fusion.c2_uwb_root_world.contact_lifecycle import RotationPositionBridge
from biospur_fusion.c2_uwb_root_world.continuous_support import ContinuousSupportVelocity


def update(owner,t,**kwargs):
    values=dict(fresh=True,acceleration_quiet=True,lifted=False,direct_stationary=False,rotation_only=True)
    values.update(kwargs)
    owner.update(t,t,**values)


def test_whole_departure_expiry_return_no_refresh():
    o=RotationPositionBridge();update(o,0.,direct_stationary=True,rotation_only=False)
    for t in np.arange(.005,.211,.005):
        update(o,float(t))
        assert o.evidence(float(t)) == (t<.125)
    assert o.last_direct==0.
    update(o,.215,direct_stationary=True,rotation_only=False)
    update(o,.220)
    assert o.evidence(.220)


@pytest.mark.parametrize('veto',[dict(fresh=False),dict(acceleration_quiet=False),dict(lifted=True),dict(rotation_only=False)])
def test_explicit_veto_clears_reference(veto):
    o=RotationPositionBridge();update(o,0.,direct_stationary=True,rotation_only=False)
    update(o,.005,**veto);update(o,.010)
    assert not o.evidence(.010)


def test_stale_gap_and_no_new_soft_initialization():
    o=RotationPositionBridge();update(o,0.)
    assert not o.evidence(0.)
    update(o,.005,direct_stationary=True,rotation_only=False)
    update(o,.02)
    assert not o.evidence(.02)


def test_query_deadline_freshness_future_and_prefix_invariance():
    a,b=RotationPositionBridge(),RotationPositionBridge()
    for o in (a,b):
        update(o,0.,direct_stationary=True,rotation_only=False)
        for t in np.arange(.005,.121,.005):update(o,float(t))
    assert a.evidence(.124)
    assert not a.evidence(.125)
    assert not a.evidence(.128)
    assert not a.evidence(-.001)
    update(b,.125,direct_stationary=True,rotation_only=False)
    assert a.evidence(.119)==b.evidence(.119)
    old=b.last_direct;update(b,.125)
    assert b.last_direct==old


def test_position_routing_does_not_create_velocity_evidence():
    class Protocol:
        def advance(self,t):pass
        def evidence(self,t):return np.zeros(2,bool),np.zeros(2),np.full(2,3)
        def rotation_position_evidence(self,t):return .005<=t<.125
    support=ContinuousSupportVelocity.__new__(ContinuousSupportVelocity)
    support.protocol=Protocol();support.rotation_position_bridge=True
    support.soft_support_position_guard=False
    assert support.position_is_considered(.02)
    assert not support.position_is_considered(.125)
    assert not support.protocol.evidence(.02)[0].any()
    support.rotation_position_bridge=False
    assert not support.position_is_considered(.02)


def test_actual_stream_keeps_velocity_and_soft_point_history_exact():
    from biospur_fusion.c2_uwb_root_world.supervised_contact import SupervisedContactModel
    from biospur_fusion.c2_uwb_root_world.protocol_contact import ProtocolContactStream
    t=np.arange(45)*.005
    model=SupervisedContactModel();model.data={};model.supervision={};model.report={}
    for side in ('left','right'):
        f=np.zeros((len(t),6));f[1:42,:2]=10.
        model.data[side]=(f,np.ones(len(t),bool),t.copy())
        model.supervision[side]=(np.zeros(len(t),int),np.ones(len(t),int))
        model.report[side]={'quiet_gyro_rms_limit':.1,'quiet_acc_std_limit':.1}
    model.classify=lambda side,index:(0,1.)
    model.support_context=lambda side,index:(True,1.)
    samples={s:(t,np.zeros((len(t),3)),np.zeros((len(t),3))) for s in ('left','right')}
    old=ProtocolContactStream(model,samples,lifecycle=True,calibration_phase_prior=True)
    new=ProtocolContactStream(model,samples,lifecycle=True,calibration_phase_prior=True,rotation_position_bridge=True,
                              navigation_position_handoff=True)
    for epoch in t:
        old.advance(float(epoch));new.advance(float(epoch))
        for a,b in zip(old.evidence(epoch),new.evidence(epoch)):np.testing.assert_array_equal(a,b)
        for a,b in zip(old.position_evidence(epoch),new.position_evidence(epoch)):np.testing.assert_array_equal(a,b)
        assert new.rotation_position_evidence(epoch)==(.005<=epoch<.125)
        if .125<=epoch<.21:
            assert np.isclose(new.navigation_position_gain(epoch),(epoch-.125)/.125)
    assert old.audit==new.audit and old.phase_audit==new.phase_audit
    np.testing.assert_allclose(old.lifecycle_audit,new.lifecycle_audit,equal_nan=True)
