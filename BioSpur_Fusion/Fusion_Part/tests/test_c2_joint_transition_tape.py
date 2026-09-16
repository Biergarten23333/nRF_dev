import numpy as np
import pytest

from biospur_fusion.c2_uwb_root_world.joint_transition_tape import JointSnapshot,JointTransitionTape
from biospur_fusion.c2_uwb_root_world.support_points import SupportPoints
from biospur_fusion.c2_uwb_root_world.support_velocity import update_support_velocity
from biospur_fusion.c2_uwb_root_world.root_input_safety import CausalImuHold
from biospur_fusion.root_r3.models import RootState


def run(enabled):
    points=SupportPoints();points.tape=JointTransitionTape(enabled=enabled)
    state=RootState(0.,np.zeros(9),np.eye(9)*.1)
    hold=CausalImuHold(0.,[0,0,9.81],np.eye(3))
    offsets=np.array([[0,0,-1],[.2,0,-1]])
    state=points.update(state,offsets,[True,True],[True,True],[1,1],.005)
    for tick in range(1,5):
        state,_=hold.propagate(state,tick*.005,transition_observer=points.root_transition)
        state,_,_=update_support_velocity(state,[[.01,0,0]],[1],.005,
                                         transition_observer=points.root_transition)
        state=points.update(state,offsets,[True,True],[True,True],[1,1],.005,
                            moving=[False,True])
    points.release(0)
    points.enter(state,0,offsets[0])
    return state,points


def test_connected_forward_exact_and_episode_maps():
    a,pa=run(False);b,pb=run(True)
    np.testing.assert_array_equal(a.vector,b.vector)
    np.testing.assert_array_equal(pa.covariance(a),pb.covariance(b))
    assert not pa.tape.records
    assert pb._episodes[0]==1
    records=list(pb.tape.records)
    assert {r.kind for r in records}=={'prediction','assimilation','topology'}
    assert records[0].error_map.shape==(12,9)
    assert records[-2].error_map.shape==(12,15)
    assert records[-1].error_map.shape==(15,12)
    for record in records:
        np.testing.assert_array_equal(record.error_cross,record.before.covariance@record.error_map.T)
    assert not records[-1].after.mean.flags.writeable


def test_expiry_capacity_and_disabled_no_touch():
    disabled=JointTransitionTape();disabled.commit(None,None,None,None,independent_noise=False)
    tape=JointTransitionTape(enabled=True,maximum_records=2)
    before=JointSnapshot.capture(0,np.zeros(9),np.eye(9))
    for epoch in (.01,.02,.03,.2):
        after=JointSnapshot.capture(epoch,np.zeros(9),np.eye(9))
        tape.commit('prediction',before,after,np.eye(9),independent_noise=True)
        before=after
    assert tape.capacity_dropped==1
    assert len(tape.records)==1


def test_unrecorded_commit_and_missing_noise_rejected():
    tape=JointTransitionTape(enabled=True)
    a=JointSnapshot.capture(0,np.zeros(9),np.eye(9))
    with pytest.raises(ValueError):tape.commit('prediction',a,a,np.eye(9),independent_noise=False)
    tape.commit('prediction',a,a,np.eye(9),independent_noise=True)
    b=JointSnapshot.capture(0,np.ones(9),np.eye(9))
    with pytest.raises(ValueError):tape.commit('prediction',b,b,np.eye(9),independent_noise=True)
