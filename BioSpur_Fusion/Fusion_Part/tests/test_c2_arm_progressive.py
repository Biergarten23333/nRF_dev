import copy
import json
import numpy as np
import pytest
from biospur_fusion.c2_five_calibration.progressive import ArmProgressiveSession
from biospur_fusion.c2_five_calibration.progressive.session import digest
from biospur_fusion.c2_sparse_nodes.inputs import NODES
from c2_arm_progressive_fixture import phase_stream,deliver


def session():
    return ArmProgressiveSession(source_kind='synthetic_orientation_and_gyro')


def test_future_changes_cannot_affect_prefix_and_axes_arrive_in_order():
    events,_=phase_stream();a=session()
    for e in events[:6]:deliver(a,e)
    saved=a.snapshots
    assert saved[-1]['arms'][0]['available_axis_columns']==[1]
    assert saved[-1]['arms'][0]['status']=='WAITING_FOR_COMPLEMENTARY_MOTION'
    assert saved[-1]['arms'][1]['available_axis_columns']==[]
    # No reference to the external future arrays is retained by the session.
    changed=copy.deepcopy(events)
    for e in changed[6:]:
        for r in e['rows'].values():r[:,8:11]*=12
    b=session()
    for e in changed[:6]:deliver(b,e)
    assert digest(a.snapshots)==digest(b.snapshots)
    deliver(a,events[6])
    assert a.snapshot()['arms'][0]['available_axis_columns']==[1,2]
    assert digest(a.snapshots[:6])==digest(saved)
    saved[-1]['arms'][0]['status']='CORRUPTED_EXTERNAL_COPY'
    assert a.snapshots[5]['arms'][0]['status']!='CORRUPTED_EXTERNAL_COPY'


def test_chunks_duplicate_and_pending_checkpoint_match_uninterrupted(tmp_path):
    events,_=phase_stream();a=session();b=session()
    for e in events[:3]:
        deliver(a,e);deliver(b,e,parts=7)
    assert digest(a.snapshots)==digest(b.snapshots)
    e=events[3];cut=1731
    b.ingest(e['phase_id'],e['start'],e['stop'],{n:r[:cut] for n,r in e['rows'].items()},chunk_id='partial',final=False)
    checkpoint=tmp_path/'checkpoint.json';b.save(checkpoint)
    c=ArmProgressiveSession.load(checkpoint)
    c.ingest(e['phase_id'],e['start'],e['stop'],{n:r[cut:] for n,r in e['rows'].items()},chunk_id='tail',final=True)
    deliver(a,e)
    assert digest(a.snapshots)==digest(c.snapshots)
    before=c.state_digest
    c.ingest(e['phase_id'],e['start'],e['stop'],{n:r[cut:] for n,r in e['rows'].items()},chunk_id='tail',final=True)
    assert c.state_digest==before
    bad=json.loads(checkpoint.read_text());bad['state']['phase_cursor']=8
    checkpoint.write_text(json.dumps(bad))
    with pytest.raises(ValueError,match='integrity'):ArmProgressiveSession.load(checkpoint)


def test_bad_order_identity_and_failed_solve_leave_committed_state_intact(monkeypatch):
    import biospur_fusion.c2_five_calibration.progressive.session as owner
    events,_=phase_stream();a=session()
    with pytest.raises(ValueError,match='exact next'):deliver(a,events[1])
    deliver(a,events[0]);before=a.state_digest
    def fail(*args,**kwargs):raise RuntimeError('injected solver failure')
    monkeypatch.setattr(owner,'update_arm',fail)
    with pytest.raises(RuntimeError,match='injected'):deliver(a,events[1])
    assert a.state_digest==before
    bad=copy.deepcopy(events[0]);bad['rows'][NODES[1]][0,8]+=.1
    with pytest.raises(ValueError,match='identity reused'):deliver(a,bad)
    assert a.state_digest==before


def test_full_batch_equivalence_and_no_false_support_with_zero_gyro():
    events,_=phase_stream();a=session()
    for e in events:deliver(a,e)
    for online,batch in zip(a.snapshot()['arms'],a.batch_check()):
        assert online['status']=='CONDITIONAL_SUPPORT'
        assert abs(online['selected']['cost']-batch['selected']['cost'])<1e-7
        np.testing.assert_allclose(online['selected']['mount'],batch['selected']['mount'],atol=1e-5)
    b=session()
    for e in events:
        for r in e['rows'].values():r[:,8:11]=0
        deliver(b,e)
    assert all(r['status']!='CONDITIONAL_SUPPORT' for r in b.snapshot()['arms'])
    assert not b.snapshot()['calibration_accepted'] and not b.snapshot()['full_C2_complete']


def test_real_input_adapter_is_not_silently_accepted():
    with pytest.raises(ValueError,match='not implemented'):
        ArmProgressiveSession(source_kind='real_raw6')


def test_max_dependency_and_passive_phase_do_not_create_arm_information():
    events,_=phase_stream();a=session()
    for e in events[:2]:deliver(a,e)
    before=a.snapshot();deliver(a,events[2]);after=a.snapshot()
    assert after['factor_count']==before['factor_count']
    assert after['arms']==before['arms']
    assert after['evidence_phase_count']==before['evidence_phase_count']+1
    for e in events[3:]:
        s=deliver(a,e)
        assert all(r.get('max_evidence_time',0)<=s['max_evidence_time'] for r in s['arms'])


def test_checkpoint_refuses_changed_producer(tmp_path,monkeypatch):
    import biospur_fusion.c2_five_calibration.progressive.session as owner
    a=session();path=tmp_path/'saved.json';a.save(path)
    monkeypatch.setattr(owner,'implementation_binding',lambda:{'different_source':'hash'})
    with pytest.raises(ValueError,match='implementation changed'):
        ArmProgressiveSession.load(path)
