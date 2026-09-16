import numpy as np
from biospur_fusion.c2_uwb_root_world.supervised_contact import SupervisedContactModel
from biospur_fusion.c2_uwb_root_world.protocol_contact import ProtocolContactStream


def model_for(context=1, lifted=False, valid=True, supported=True):
    model=SupervisedContactModel()
    model.moving_support_evidence=True
    model.data={};model.supervision={};model.report={};model.parameters={}
    for side in ('left','right'):
        f=np.array([[0.,0.,4.,4.,.1 if lifted else 0.,1.]])
        model.data[side]=(f,np.array([valid]),np.array([0.]))
        model.supervision[side]=(np.array([3]),np.array([context]))
        model.report[side]={'quiet_gyro_rms_limit':.1,'quiet_acc_std_limit':.1}
        centers=np.tile(f[0]+10,(6,1));centers[3 if supported else 1]=f[0]
        model.parameters[side]=(centers,np.ones(6),np.ones(6)*2)
    return model


def test_moving_support_evidence_is_not_stationarity():
    model=model_for()
    assert model.calibration_phase('left',0)==(1,3,1.,'SUPPORTED_MOVING_MODEL_EVIDENCE')
    samples={s:(np.array([0.]),np.zeros((1,3)),np.zeros((1,3))) for s in ('left','right')}
    stream=ProtocolContactStream(model,samples,lifecycle=True,calibration_phase_prior=True)
    stream.advance(0.)
    assert all(v[2] and v[4] for v in stream.point_latest.values())
    assert all(mode==0 for mode,confidence in stream.effective.values())
    # Stale source evidence does not become permanent protection.
    assert not stream.position_evidence(.1)[0].any()


def test_unknown_and_strong_vetoes_are_retained():
    for kwargs,expected in [({'supported':False},-1),({'lifted':True},1),
                            ({'valid':False},-1),({'context':2},-1),
                            ({'context':3},2),({'context':4},5),({'context':-1},-1)]:
        assert model_for(**kwargs).calibration_phase('left',0)[1]==expected


def test_default_behavior_and_fitted_support_confidence():
    model=model_for();model.moving_support_evidence=False
    assert model.calibration_phase('left',0)[1]==-1
    model.moving_support_evidence=True
    model.support_context=lambda side,index:(True,.21)
    assert model.calibration_phase('left',0)[2]==.21
