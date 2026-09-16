"""Synthetic event fixtures only; no recorded-data replay."""
from dataclasses import replace
import json
import os
import signal
import subprocess
import sys

import numpy as np
import pytest

import run_c2_continuous_archive_ab as runner
from biospur_fusion.root_r3.models import RootState
from test_c2_continuous_full_state_feedback import ANCHORS,CLOCK,row_at


def test_first_term_requests_stop_and_restores_prior_on_exception():
    previous=signal.getsignal(signal.SIGTERM)
    with pytest.raises(RuntimeError):
        with runner.CooperativeTermination() as stop:
            assert stop() is None
            signal.raise_signal(signal.SIGTERM)
            assert stop()=='SIGTERM'
            assert signal.getsignal(signal.SIGTERM)==signal.SIG_DFL
            raise RuntimeError('fixture')
    assert signal.getsignal(signal.SIGTERM)==previous


def test_second_term_terminates_subprocess():
    script='''import signal
from run_c2_continuous_archive_ab import CooperativeTermination
with CooperativeTermination() as stop:
    signal.raise_signal(signal.SIGTERM)
    print(stop(),flush=True)
    signal.raise_signal(signal.SIGTERM)
raise RuntimeError('second TERM failed')
'''
    env=dict(os.environ);env['PYTHONPATH']='src:tools'
    result=subprocess.run([sys.executable,'-c',script],env=env,capture_output=True,text=True,timeout=10)
    assert result.returncode==-signal.SIGTERM
    assert result.stdout.strip()=='SIGTERM'


def test_cli_installs_request_callback_and_restores_handler(monkeypatch,tmp_path):
    previous=signal.getsignal(signal.SIGTERM)
    def fake(*args,**kwargs):
        assert signal.getsignal(signal.SIGTERM)!=previous
        signal.raise_signal(signal.SIGTERM)
        assert kwargs['stop_request']()=='SIGTERM'
        return {'fixture':True}
    monkeypatch.setattr(runner,'run',fake)
    assert runner.main(['--frontend',str(tmp_path),'--pose',str(tmp_path/'pose.npz'),
                        '--output',str(tmp_path/'out')])=={'fixture':True}
    assert signal.getsignal(signal.SIGTERM)==previous


def test_general_motion_cli_options_reach_owner(monkeypatch,tmp_path):
    def fake(*args,**kwargs):
        assert kwargs['world_support_policy']=='unavailable'
        assert kwargs['native_tag_epoch_correction'] is True
        return {'fixture':True}
    monkeypatch.setattr(runner,'run',fake)
    assert runner.main(['--frontend',str(tmp_path),'--pose',str(tmp_path/'pose.npz'),
        '--output',str(tmp_path/'out'),'--world-support-policy','unavailable',
        '--native-tag-epoch-correction','--partial-range-tracking'])=={'fixture':True}


def synthetic_inputs(tmp_path,monkeypatch):
    frontend=tmp_path/'frontend';frontend.mkdir();(frontend/'RESULT.json').write_text('{}')
    times=np.arange(5)*.005;nodes=[f'node{i}' for i in range(10)]
    pose=tmp_path/'pose.npz'
    np.savez(pose,time_s=times+100.,node_names=nodes,node_offsets=np.zeros((5,10,3)),
        pelvis_rotation_world_sensor=np.tile(np.eye(3),(5,1,1)),pelvis_acc_sensor=np.tile([0,0,9.80665],(5,1)),
        joint_names=['root'],joints_relative=np.zeros((5,1,3)))
    events=[(.001,replace(row_at(.001,[2.,1.3,1.]),node=node),.001) for node in nodes]
    monkeypatch.setattr(runner,'clock_for_node',lambda *args:CLOCK)
    monkeypatch.setattr(runner,'raw_events',lambda *args:events)
    monkeypatch.setattr(runner,'_anchors',lambda:ANCHORS)
    monkeypatch.setattr(runner,'_initialize',lambda *args:RootState(0.,np.r_[[2.,1.3,1.],np.zeros(6)],np.eye(9)))
    return frontend,pose


@pytest.mark.parametrize('processed',[0,1,4,12])
def test_stop_at_event_boundary_persists_exact_partial_prefix(tmp_path,monkeypatch,processed):
    frontend,pose=synthetic_inputs(tmp_path,monkeypatch)
    calls=0
    def stop():
        nonlocal calls
        calls+=1
        return 'SIGTERM' if calls>processed else None
    output=tmp_path/'out'
    result=runner.run(frontend,pose,output,None,feedback_mode='full-state',stop_request=stop)
    assert calls==processed+1
    coverage=json.loads((output/'COVERAGE.json').read_text())
    assert result['status']=='PARTIAL_BOUNDED_CHECKPOINT'
    assert result['stop_reason']==coverage['stop_reason']=='SIGTERM'
    assert coverage['core_complete'] and coverage['ancillary_complete']
    assert not coverage['numerical_complete']
    with np.load(output/'CONTINUOUS_AB.npz') as data:
        assert len(data['time_s'])+len(data['uwb_time_s'])==processed
        assert data['roots_a'].shape==(len(data['time_s']),3)
        assert data['raw_range_residual_m'].shape==(len(data['uwb_time_s']),8)
        if processed==0:
            assert coverage['first_time_s'] is None and coverage['last_time_s'] is None


def test_internal_runtime_stop_also_persists_empty_checkpoint(tmp_path,monkeypatch):
    frontend,pose=synthetic_inputs(tmp_path,monkeypatch)
    result=runner.run(frontend,pose,tmp_path/'out',None,max_runtime_s=-1.,feedback_mode='full-state')
    assert result['stop_reason']=='MAX_RUNTIME' and result['sample_count']==0


def test_no_request_preserves_complete_result(tmp_path,monkeypatch):
    frontend,pose=synthetic_inputs(tmp_path,monkeypatch)
    result=runner.run(frontend,pose,tmp_path/'out',None,feedback_mode='full-state')
    assert result['stop_reason'] is None and not result['bounded_stop']
    assert result['sample_count']==5


def test_term_inside_event_finishes_transaction_then_saves(tmp_path,monkeypatch):
    frontend,pose=synthetic_inputs(tmp_path,monkeypatch)
    real=runner.propagate_inertial;sent=False
    def interrupted(*args,**kwargs):
        nonlocal sent
        if not sent:
            sent=True
            signal.raise_signal(signal.SIGTERM)
        return real(*args,**kwargs)
    monkeypatch.setattr(runner,'propagate_inertial',interrupted)
    with runner.CooperativeTermination() as stop:
        result=runner.run(frontend,pose,tmp_path/'out',None,feedback_mode='full-state',stop_request=stop)
    assert sent and result['stop_reason']=='SIGTERM' and result['sample_count']==1
    with np.load(tmp_path/'out/CONTINUOUS_AB.npz') as data:
        assert len(data['time_s'])==1 and len(data['uwb_time_s'])==0
        assert data['root_state_b'].shape==(1,9)


def test_unrequested_callback_exact_core_parity(tmp_path,monkeypatch):
    frontend,pose=synthetic_inputs(tmp_path,monkeypatch)
    runner.run(frontend,pose,tmp_path/'default',None,feedback_mode='full-state')
    runner.run(frontend,pose,tmp_path/'explicit',None,feedback_mode='full-state',stop_request=lambda:None)
    with np.load(tmp_path/'default/CONTINUOUS_AB.npz') as a,np.load(tmp_path/'explicit/CONTINUOUS_AB.npz') as b:
        assert a.files==b.files
        for name in a.files:
            assert a[name].dtype==b[name].dtype and a[name].shape==b[name].shape
            np.testing.assert_array_equal(a[name],b[name])


@pytest.mark.parametrize('count',[0,1])
def test_empty_articulated39_supervised_support_ancillary_shapes_and_nonempty_parity(tmp_path,count):
    from types import SimpleNamespace
    from biospur_fusion.c2_uwb_root_world.continuous_support import ContinuousSupportVelocity
    from biospur_fusion.c2_uwb_root_world.ankle_contact import AnkleContactConfig
    from biospur_fusion.c2_uwb_root_world.support_velocity import SupportVelocityConfig
    from test_c2_contact_conditional_heading import heading_owner
    support=ContinuousSupportVelocity.__new__(ContinuousSupportVelocity)
    support.points=heading_owner().contacts
    support.origin_s=100.;support.contact_lifecycle=True
    support.points.audit=[tuple(np.arange(14,dtype=float))]*count
    support.point_mask_audit=[(True,False,False,True,True,False)]*count
    support.audit=[tuple(np.arange(35,dtype=float))]*count
    support.support_audit=[(True,False)]*count
    support.offsets=np.zeros((2,2,3));support.velocity=np.zeros((2,2,3))
    support.profiles={};support.config=SupportVelocityConfig();support.detector_config=AnkleContactConfig()
    support.acc_reference={};support.samples={side:(np.array([0.]),) for side in ('left','right')}
    model=SimpleNamespace(supervision={side:(np.array([0]),np.array([1])) for side in ('left','right')},
        action_ids={side:np.array(['00']) for side in ('left','right')},report={},regions_path='fixture',regions_sha256='fixture')
    support.protocol=SimpleNamespace(model=model,calibration_phase_prior=True,
        phase_audit=[(0.,0.,0.,1.,0.,0.,0.,'fixture')]*count,
        audit=[tuple(np.arange(6,dtype=float))]*count,
        lifecycle_audit=[tuple(np.arange(12,dtype=float))]*count)
    support.save(tmp_path)
    expected={'SUPPORT_POINTS.npz':14,'CONTACT.npz':35,'CALIBRATION_PHASE_PRIOR.npz':7,
        'CONTACT_SENSOR_EVENTS.npz':6,'CONTACT_LIFECYCLE_EVENTS.npz':12}
    for name,width in expected.items():
        with np.load(tmp_path/name) as data:assert data['rows'].shape==(count,width)
    with np.load(tmp_path/'SUPPORT_POINTS.npz') as data:
        assert data['final_base_anchor_cross'].shape==(39,0)
        assert data['valid'].shape==(count,2)
        if count:np.testing.assert_array_equal(data['rows'],np.asarray(support.points.audit))
    with np.load(tmp_path/'CONTACT.npz') as data:
        assert data['support_valid'].shape==(count,2)
        if count:np.testing.assert_array_equal(data['rows'],np.asarray(support.audit))
