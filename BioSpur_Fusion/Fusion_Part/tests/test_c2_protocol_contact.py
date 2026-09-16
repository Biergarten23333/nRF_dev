import numpy as np
from biospur_fusion.c2_uwb_root_world.protocol_contact import ProtocolContactStream, ProtocolContactModel, causal_features


class Model:
    def __init__(self, t):
        self.data = {s: (None, None, t.copy()) for s in ('left', 'right')}
        self.calls = []
    def classify(self, side, index):
        self.calls.append((side, index))
        return (2, .9) if index == 2 else (0, .9)


def test_consumes_every_actual_sample_and_duplicate_query_no_recount():
    t = np.arange(6) * .005
    samples = {s: (t, np.zeros((6,3)), np.zeros((6,3))) for s in ('left', 'right')}
    model = Model(t)
    stream = ProtocolContactStream(model, samples)
    stream.advance(.015)
    assert len(model.calls) == 8
    assert sorted(i for s, i in model.calls if s == 'left') == [0,1,2,3]
    stream.advance(.015)
    assert len(model.calls) == 8
    assert stream.evidence(.005)[0].all()
    assert not stream.evidence(.010)[0].any()  # rolling not zero-ankle
    assert stream.evidence(.015)[0].all()
    assert not stream.evidence(.024)[0].any()  # stale


def test_fresh_actual_sample_works_between_pose_frames_without_widening_age():
    t = np.array([.000, .005, .010])
    model = Model(t)
    model.data = {s: (None,None,np.array([0.,.004,.009])) for s in ('left','right')}
    model.classify = lambda side,index: (0, .9)
    stream = ProtocolContactStream(model,{s:(t,None,None) for s in ('left','right')})
    stream.advance(.013)
    assert stream.evidence(.013)[0].all()
    assert not stream.evidence(.018)[0].any()


def test_feature_prefix_is_causal_and_no_posture_future():
    t = np.arange(60)*.005
    acc = np.tile([0.,0.,9.81],(60,1));gyro=np.zeros((60,3))
    offsets=np.tile([[0.,0.,-.9],[0.,0.,-.9]],(60,1,1))
    a=causal_features(t,acc,gyro,t,offsets,0,9.81)[0]
    acc[40:]=100;gyro[40:]=100;offsets[40:]=100
    b=causal_features(t,acc,gyro,t,offsets,0,9.81)[0]
    np.testing.assert_array_equal(a[:40],b[:40])


def test_unknown_rejects_far_point_not_always_nearest():
    model=ProtocolContactModel()
    model.data={'left':(np.ones((1,6))*100,np.ones(1,bool),np.zeros(1))}
    model.parameters={'left':(np.zeros((4,6)),np.ones(6),np.ones(4))}
    assert model.classify('left',0)==(-1,0.)


def test_gap_invalidates_25_sample_window():
    t=np.r_[np.arange(40)*.005, 1.+np.arange(30)*.005]
    acc=np.tile([0.,0.,9.81],(70,1));gyro=np.zeros((70,3))
    offsets=np.tile([[0.,0.,-.9],[0.,0.,-.9]],(70,1,1))
    _,valid,_,_,_=causal_features(t,acc,gyro,t,offsets,0,9.81)
    assert valid[39]
    assert not valid[40:64].any()
    assert valid[64:].all()


def test_feature_classifier_works_without_archive_or_action_metadata():
    model=ProtocolContactModel()
    model.parameters={'left':(np.array([[0]*6,[3]*6,[6]*6,[9]*6],float),np.ones(6),np.ones(4))}
    assert not hasattr(model,'data') and not hasattr(model,'regions_path')
    assert model.classify_feature('left',np.zeros(6))[0]==0
