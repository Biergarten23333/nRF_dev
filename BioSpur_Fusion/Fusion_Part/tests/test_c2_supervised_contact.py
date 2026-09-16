import numpy as np
from biospur_fusion.c2_uwb_root_world.supervised_contact import SupervisedContactModel,formal_action_indices


def test_support_group_ambiguity_is_not_stationary_or_airborne():
    model=SupervisedContactModel()
    centers=np.ones((6,6))*5
    centers[0]=0.;centers[3]=.01
    model.parameters={'left':(centers,np.ones(6),np.ones(6)*3)}
    model.data={'left':(np.zeros((1,6)),np.ones(1,bool),np.zeros(1))}
    # Fine class cannot distinguish supportedquiet versus supportedmoving.
    assert model.classify('left',0)[0]==-1
    valid,confidence=model.support_context('left',0)
    assert valid and confidence>0
    model.data['left'][1][0]=False
    assert model.support_context('left',0)==(False,0.)


def test_lift_or_rolling_prototype_is_not_support_group():
    model=SupervisedContactModel()
    centers=np.ones((6,6))*5;centers[1]=0.
    model.parameters={'left':(centers,np.ones(6),np.ones(6)*3)}
    model.data={'left':(np.zeros((1,6)),np.ones(1,bool),np.zeros(1))}
    assert model.support_context('left',0)==(False,0.)


def test_formal_prior_exact_boundaries_and_gap():
    actions=[dict(id='00_still',start_s=104.,end_s=134.),dict(id='02_stand',start_s=140.,end_s=170.)]
    assert formal_action_indices(np.array([.19,3.999,4.,33.999,34.,39.,40.,70.]),actions,100.).tolist()==[-1,-1,0,0,-1,-1,2,-1]


def test_protocol_phase_stationary_rolling_seated_and_motion_release():
    model=SupervisedContactModel();model.supervision={};model.data={};model.report={}
    for side in ('left','right'):
        contexts=np.array([-1,1,1,2,3,3,4,5,1,1])
        features=np.zeros((10,6));features[2,0:2]=10 # rotation keeps soft standing support
        features[3,4]=.1;features[4,0]=10 # lifted active; rotating forefoot
        features[7,0]=10;features[8,3]=10 # moving passive seated; adjustment
        valid=np.ones(10,bool);valid[9]=False
        model.data[side]=(features,valid,np.zeros(10))
        model.supervision[side]=(np.zeros(10,int),contexts)
        model.report[side]={'quiet_gyro_rms_limit':.1,'quiet_acc_std_limit':.1}
        phases=[model.calibration_phase(side,j)[1] for j in range(10)]
        assert phases==[-1,0,3,1,2,0,4,5,-1,-1]


def test_features_only_stream_never_calls_protocol_prior():
    from biospur_fusion.c2_uwb_root_world.protocol_contact import ProtocolContactStream
    class FeatureOnly:
        data={side:(np.zeros((1,6)),np.ones(1,bool),np.zeros(1)) for side in ('left','right')}
        def classify(self,side,index):return 0,1.
        def calibration_phase(self,*args):raise AssertionError('online route accessed action prior')
    samples={side:(np.zeros(1),np.zeros((1,3)),np.zeros((1,3))) for side in ('left','right')}
    stream=ProtocolContactStream(FeatureOnly(),samples)
    stream.advance(0.)
    assert len(stream.audit)==2


def test_passive_seated_height_is_not_motion_and_active_lift_still_releases():
    model=SupervisedContactModel();model.supervision={};model.data={};model.report={}
    for side in ('left','right'):
        f=np.zeros((5,6));f[:,4]=.2;f[2,0]=10
        model.data[side]=(f,np.array([True,True,True,False,True]),np.zeros(5))
        model.supervision[side]=(np.zeros(5,int),np.array([5,4,5,5,-1]))
        model.report[side]={'quiet_gyro_rms_limit':.1,'quiet_acc_std_limit':.1}
        assert [model.calibration_phase(side,j)[1] for j in range(5)]==[4,1,5,-1,-1]
        assert model.phase_lift_evidence(side,0,-1) # no exception outside formal prior


def test_passive_phase_height_policy_reaches_lifecycle_and_points():
    from biospur_fusion.c2_uwb_root_world.protocol_contact import ProtocolContactStream
    model=SupervisedContactModel();model.supervision={};model.data={};model.report={}
    epochs=np.arange(4)*.005
    for side in ('left','right'):
        f=np.zeros((4,6));f[:,4]=.2;f[1,0]=10
        model.data[side]=(f,np.array([True,True,False,True]),epochs.copy())
        model.supervision[side]=(np.zeros(4,int),np.array([5,5,5,-1]))
        model.report[side]={'quiet_gyro_rms_limit':.1,'quiet_acc_std_limit':.1}
    model.classify=lambda side,index:(4,1.)
    model.support_context=lambda side,index:(False,0.)
    samples={side:(epochs,np.zeros((4,3)),np.zeros((4,3))) for side in ('left','right')}
    stream=ProtocolContactStream(model,samples,lifecycle=True,calibration_phase_prior=True)
    stream.advance(0.)
    assert stream.position_evidence(0.)[0].all()
    assert not any(row[8] for row in stream.lifecycle_audit)
    for epoch in epochs[1:]:
        stream.advance(epoch)
        assert not stream.position_evidence(epoch)[0].any()


def test_explicit_protocol_release_cannot_bridge_into_gap():
    from biospur_fusion.c2_uwb_root_world.protocol_contact import ProtocolContactStream
    class Model:
        stationary_classes=(0,4);seated_classes=(4,5)
        data={side:(np.zeros((3,6)),np.ones(3,bool),np.array([0.,.005,.010])) for side in ('left','right')}
        report={side:{'quiet_gyro_rms_limit':.1,'quiet_acc_std_limit':.1} for side in ('left','right')}
        def classify(self,side,index):return -1,0.
        def support_context(self,side,index):return False,0.
        def calibration_phase(self,side,index):
            # Positive lift is release evidence. Acceleration-only UNKNOWN
            # has its own bounded weak-carry tests and is not an explicit lift.
            return [(1,3,1.,'SUPPORTED_ARTICULATION'),(2,1,1.,'LIFT_EVIDENCE'),(-1,-1,0.,'NO_FORMAL_PRIOR')][index]
    samples={side:(np.array([0.,.005,.010]),np.zeros((3,3)),np.zeros((3,3))) for side in ('left','right')}
    stream=ProtocolContactStream(Model(),samples,lifecycle=True,calibration_phase_prior=True)
    stream.advance(0.);assert stream.position_evidence(0.)[0].all()
    stream.advance(.005);assert not stream.position_evidence(.005)[0].any()
    stream.advance(.010);assert not stream.position_evidence(.010)[0].any()
