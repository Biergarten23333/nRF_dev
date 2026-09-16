import numpy as np
import pytest
from biospur_fusion.c2_uwb_root_world.contact_lifecycle import ContactLifecycle
from biospur_fusion.c2_uwb_root_world.protocol_contact import ProtocolContactStream
from biospur_fusion.c2_uwb_root_world.support_velocity import update_support_velocity
from test_c2_support_velocity import state


def update(owner,t,cls=0,**kwargs):
    values=dict(fresh=True,quiet_motion=True,lifted=False,seated_context=False)
    values.update(kwargs)
    return owner.update(t,cls,.9,**values)


def test_unknown_bridge_expires_without_refreshing_direct_evidence():
    owner=ContactLifecycle()
    assert update(owner,0.)[0]==owner.QUIET
    for t in np.arange(1,25)*.005:
        assert update(owner,float(t),-1)[0]==owner.BRIDGED
    assert update(owner,.125,-1)[0]==owner.RELEASED
    assert owner.last_direct==0.


@pytest.mark.parametrize('kwargs,cls', [({'quiet_motion':False},-1),({'lifted':True},-1),
    ({'fresh':False},-1),({},1),({},2),({},3)])
def test_motion_lift_stale_and_unconfirmed_modes_release(kwargs,cls):
    owner=ContactLifecycle();update(owner,0.)
    assert update(owner,.005,cls,**kwargs)[0]==owner.RELEASED


def test_duplicates_do_not_count_and_gap_breaks_confirmation():
    owner=ContactLifecycle()
    assert update(owner,0.,-1,seated_context=True)[0]==0
    assert update(owner,0.,-1,seated_context=True)[0]==0
    for t in np.arange(1,20)*.005:update(owner,float(t),-1,seated_context=True)
    assert update(owner,.2,-1,seated_context=True)[0]==0
    for t in np.arange(1,25)*.005+.2:update(owner,float(t),-1,seated_context=True)
    assert update(owner,.326,-1,seated_context=True)[0]==owner.SEATED_STATIONARY


def test_seated_context_never_bypasses_lift_or_motion():
    owner=ContactLifecycle()
    for i in range(100):
        assert update(owner,i*.005,-1,seated_context=True,lifted=True)[0]==0
    for i in range(100,200):
        assert update(owner,i*.005,3,seated_context=True,quiet_motion=False)[0]==0


def test_position_consider_uses_actual_gain_joseph_and_preserves_feedback():
    prior=state(1.)
    after,innovation,r=update_support_velocity(prior,[[0,0,0]],[1.],.005,consider_position=True)
    np.testing.assert_array_equal(after.position_m,prior.position_m)
    np.testing.assert_array_equal(after.covariance[:3,:3],prior.covariance[:3,:3])
    assert after.velocity_mps[0]<prior.velocity_mps[0]
    assert after.vector[6]>0
    h=np.zeros((3,9));h[:,3:6]=np.eye(3);p=prior.covariance
    k=np.linalg.solve(h@p@h.T+r,h@p).T;k[:3]=0
    ikh=np.eye(9)-k@h
    np.testing.assert_allclose(after.covariance,ikh@p@ikh.T+k@r@k.T,atol=1e-14)
    np.linalg.cholesky(after.covariance)


def test_same_seated_context_separates_moving_and_quiet_leg_historically():
    t=np.arange(50)*.005
    class Model:
        def __init__(self):
            self.data={};self.report={}
            for side in ('left','right'):
                f=np.zeros((len(t),6))
                if side=='left':f[:,1]=10.;f[:,4]=.1
                self.data[side]=(f,np.ones(len(t),bool),t.copy())
                self.report[side]={'quiet_gyro_rms_limit':.1,'quiet_acc_std_limit':.1}
        def classify(self,side,index):return (3,.8) if side=='left' else (-1,0.)
    samples={s:(t,None,None) for s in ('left','right')}
    stream=ProtocolContactStream(Model(),samples,lifecycle=True)
    stream.advance(.15)
    valid,_,classes=stream.evidence(.15)
    np.testing.assert_array_equal(valid,[False,True])
    np.testing.assert_array_equal(classes,[3,-1])
    assert not stream.evidence(.10)[0].any()  # no retroactive latch
    assert not stream.evidence(.16)[0].any()  # stale both
    count=len(stream.audit);stream.advance(.15);assert len(stream.audit)==count


def test_default_contact_update_exact():
    p=state();a=update_support_velocity(p,[[0,0,0]],[1.],.005)[0]
    b=update_support_velocity(p,[[0,0,0]],[1.],.005,consider_position=False)[0]
    np.testing.assert_array_equal(a.vector,b.vector)
    np.testing.assert_array_equal(a.covariance,b.covariance)


def test_held_actual_sample_is_not_assimilated_twice():
    from biospur_fusion.c2_uwb_root_world.continuous_support import ContinuousSupportVelocity
    from biospur_fusion.c2_uwb_root_world.support_velocity import SupportVelocityConfig
    from types import SimpleNamespace
    owner=ContinuousSupportVelocity.__new__(ContinuousSupportVelocity)
    owner.protocol=SimpleNamespace(evidence=lambda t:(np.ones(2,bool),np.ones(2),np.zeros(2,int)),
                                   sample_epochs=lambda t:np.array([.005,.005]))
    owner.contact_lifecycle=True;owner.config=SupportVelocityConfig()
    owner.protocol_consumed_epochs=np.full(2,-np.inf)
    owner.times=np.array([0.,.005,.010]);owner.velocity=np.zeros((3,2,3))
    owner.support_audit=[];owner.audit=[]
    p=state()
    owner._update_protocol(p,0)
    assert np.isneginf(owner.protocol_consumed_epochs).all()
    first=owner._update_protocol(p,1)
    second=owner._update_protocol(first,2)
    np.testing.assert_array_equal(first.vector,second.vector)
    np.testing.assert_array_equal(first.covariance,second.covariance)
    assert owner.support_valid.all()
    assert not np.any(owner.audit[-1][1:3])
