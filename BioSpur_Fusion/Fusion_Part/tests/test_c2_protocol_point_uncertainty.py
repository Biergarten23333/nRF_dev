"""Unknown adjustment can retain bounded point evidence, not create support."""
import numpy as np
import pytest

from biospur_fusion.c2_uwb_root_world.protocol_contact import ProtocolContactStream
from biospur_fusion.c2_uwb_root_world.supervised_contact import SupervisedContactModel


def make_stream(times, *, direct=True):
    times=np.asarray(times,float)
    model=SupervisedContactModel();model.data={};model.report={};model.supervision={}
    for side in ('left','right'):
        features=np.zeros((len(times),6))
        features[:,3]=10.  # Acceleration adjustment, not quiet or lifted.
        if direct:
            features[0,3]=0.
            features[0,:2]=10.  # Genuine supported articulation, not ZUPT.
        model.data[side]=(features,np.ones(len(times),bool),times.copy())
        model.report[side]={'quiet_gyro_rms_limit':.1,'quiet_acc_std_limit':.1}
        model.supervision[side]=(np.full(len(times),-1),np.ones(len(times),int))
    model.classify=lambda side,index:(-1,0.)
    model.support_context=lambda side,index:(False,0.)
    samples={side:(times,None,None) for side in ('left','right')}
    return ProtocolContactStream(model,samples,lifecycle=True,calibration_phase_prior=True)


@pytest.mark.parametrize('context',[1,2])
def test_adjustment_unknown_reuses_existing_weak_horizon_without_zupt(context):
    stream=make_stream(np.arange(31)*.005)
    for side in ('left','right'):stream.model.supervision[side][1][:]=context
    stream.advance(0.)
    assert stream.position_evidence(0.)[0].all()
    for index in range(1,25):
        epoch=index*.005;stream.advance(epoch)
        valid,confidence,moving=stream.position_evidence(epoch)
        assert valid.all() and moving.all()
        np.testing.assert_allclose(confidence,1.-epoch/.125)
        assert not stream.evidence(epoch)[0].any()
        assert stream.point_direct['left'][0]==0.
        assert stream.phase_audit[-1][-1]=='ADJUSTMENT_ACCELERATION_PROXY'
    stream.advance(.125)
    assert not stream.position_evidence(.125)[0].any()
    stream.advance(.15)
    assert not stream.position_evidence(.15)[0].any()


def test_unknown_cannot_birth_support_without_direct_predecessor():
    stream=make_stream(np.arange(10)*.005,direct=False)
    for t in np.arange(10)*.005:
        stream.advance(t)
        assert not stream.position_evidence(t)[0].any()
        assert stream.point_direct['left'] is None


@pytest.mark.parametrize('kind',['lift','forefoot','seated','invalid','other_known_unknown'])
def test_positive_veto_clears_carry_immediately_and_cannot_revive(kind):
    stream=make_stream([0.,.005,.010])
    for side in ('left','right'):
        features,valid,_=stream.model.data[side]
        if kind=='lift':features[1,4]=.1
        elif kind=='forefoot':stream.model.supervision[side][1][1]=3
        elif kind=='seated':stream.model.supervision[side][1][1]=4
        elif kind=='invalid':valid[1]=False
    if kind=='other_known_unknown':
        phase=stream.model.calibration_phase
        stream.model.calibration_phase=lambda side,index:(4,-1,0.,'UNKNOWN_SEATED_SEMANTICS') if index==1 else phase(side,index)
    stream.advance(0.);assert stream.position_evidence(0.)[0].all()
    for epoch in (.005,.010):
        stream.advance(epoch)
        assert not stream.position_evidence(epoch)[0].any()
        assert stream.point_direct['left'] is None


@pytest.mark.parametrize('stale',['sample_gap','pose','query'])
def test_stale_information_never_carries_point_authority(stale):
    times=[0.,.015] if stale=='sample_gap' else [0.,.005]
    stream=make_stream(times)
    if stale=='sample_gap':
        for side in ('left','right'):stream.model.supervision[side][1][1]=-1
    if stale=='pose':
        for side in ('left','right'):stream.model.data[side][2][1]=-.01
    stream.advance(times[-1])
    query=times[-1]+.008 if stale=='query' else times[-1]
    assert not stream.position_evidence(query)[0].any()
    if stale!='query':assert stream.point_direct['left'] is None


def test_lifecycle_bridged_unknown_does_not_refresh_point_direct_timestamp():
    stream=make_stream(np.arange(51)*.005)
    for side in ('left','right'):
        stream.model.data[side][0][:]=0.  # Quiet after the direct first sample.
        stream.model.supervision[side][1][1:]=-1
    stream.advance(0.)
    for index in range(1,51):
        epoch=index*.005;stream.advance(epoch)
        valid,_,_=stream.position_evidence(epoch)
        assert valid.all()==(epoch<.125)
        if stream.point_direct['left'] is not None:
            assert stream.point_direct['left'][0]==0.


def test_unknown_across_formal_boundary_keeps_original_expiry_and_duplicate_queries_do_not_renew():
    stream=make_stream(np.arange(31)*.005)
    for side in ('left','right'):stream.model.supervision[side][1][10:]=-1
    stream.advance(.05)
    assert stream.position_evidence(.05)[0].all()
    previous=stream.point_direct.copy();count=len(stream.point_history)
    stream.advance(.05)
    assert stream.point_direct==previous and len(stream.point_history)==count
    stream.advance(.125)
    assert not stream.position_evidence(.125)[0].any()


def test_carry_expires_at_query_time_even_before_next_actual_sample():
    stream=make_stream(np.arange(25)*.005)
    stream.advance(.12)
    assert stream.position_evidence(.124)[0].all()
    # The latest sample itself is still fresh at .125; the original direct
    # evidence horizon nevertheless expires exactly here, not at .1275.
    assert not stream.position_evidence(.125)[0].any()
