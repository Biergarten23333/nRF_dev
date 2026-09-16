"""Continuous C2 replay boundary; calibration rows are not inference inputs."""
import json

import numpy as np
import pytest

from biospur_fusion.c2_five_calibration import frozen_replay
from biospur_fusion.c2_sparse_nodes.inputs import sha


def arrays(n=1870):
    return dict(time_s=12.003+np.arange(n)/60,
        prior=np.tile(np.eye(3),(n,24,1,1)),observed=np.tile(np.eye(3),(n,5,1,1)),
        acceleration=np.arange(n*15,dtype=float).reshape(n,5,3)/100,
        valid=np.arange(n)%17!=0)


def test_global_decimation_keeps_original_phase_and_support(tmp_path):
    q=arrays();path=tmp_path/'C2_PRIOR.npz';np.savez_compressed(path,**q)
    actual=frozen_replay.continuous_inputs(path)
    for key in q:np.testing.assert_array_equal(actual[key],q[key][::3])
    # A hypothetical action starts on frame608, not the global decimation
    # phase. No per-action slice can replace the continuous returned grid.
    assert actual['time_s'][203]==q['time_s'][609]


@pytest.mark.parametrize('defect',['field','time','shape','support'])
def test_continuous_input_rejects_changed_contract(tmp_path,defect):
    q=arrays()
    if defect=='field':q['action_label']=np.zeros(len(q['time_s']))
    if defect=='time':q['time_s'][200]+=.001
    if defect=='shape':q['observed']=q['observed'][:,:4]
    if defect=='support':q['valid']=q['valid'].astype(float)
    path=tmp_path/'C2_PRIOR.npz';np.savez_compressed(path,**q)
    with pytest.raises(ValueError):frozen_replay.continuous_inputs(path)


def test_frozen_replay_never_calls_calibration_or_protocol(tmp_path,monkeypatch):
    from biospur_fusion.c2_five_calibration import frontend,shared_fit,solver,arm_protocol
    q=arrays();np.savez_compressed(tmp_path/'C2_PRIOR.npz',**q)
    policy=dict(probe_frames=600,probe_iterations=3,probe_wall_s=60,
                resource_probe_iterations=3,resource_probe_wall_s=60,
                iterations=150,wall_limit_s=600,global_decimation=3)
    (tmp_path/'SHARED_REPLAY_CONTRACT.json').write_text(json.dumps(dict(C2_replay_policy=policy,compatibility={})))
    bindings={'C2_PRIOR.npz':sha(tmp_path/'C2_PRIOR.npz')}
    def resolve(out,**kwargs):
        assert kwargs['diagnostic_only'] is True
        return dict(bindings=bindings,geometry={},levers=np.zeros((5,3)),producer_sources={},provenance={},inference_sources={})
    monkeypatch.setattr(frozen_replay,'resolve_shared_candidate',resolve)
    def forbidden(*a,**kw):raise AssertionError('calibration/protocol called during frozen inference')
    monkeypatch.setattr(frontend,'fit_frontend',forbidden)
    monkeypatch.setattr(shared_fit,'fit_shared_orientation',forbidden)
    monkeypatch.setattr(solver,'fit_levers',forbidden)
    monkeypatch.setattr(arm_protocol.ArmProtocolTape,'energy_for_action',forbidden)
    calls=[]
    def infer(**kw):
        calls.append(kw)
        assert set(kw)=={'time_s','prior','observed','acceleration','valid','geometry','levers','iterations','wall_limit_s'}
        return kw['prior'].copy(),dict(action_labels_consumed=False,optimizer_steps=kw['iterations']+kw['iterations']//2)
    monkeypatch.setattr(frozen_replay,'solve_pose',infer)
    frozen_replay.replay(tmp_path,probe=True)
    frozen_replay.replay(tmp_path,resource_probe=True)
    frozen_replay.replay(tmp_path)
    assert len(calls)==3 and len(calls[0]['time_s'])==600
    for key in q:np.testing.assert_array_equal(calls[2][key],q[key][::3])
    assert calls[2]['iterations']==150
    report=json.loads((tmp_path/'C2_FROZEN_REPLAY.json').read_text())
    assert not report['accepted'] and not report['legacy_validation_gates_claimed']
    assert not report['protocol_conditioned_pose_reused'] and not report['calibration_parameter_updates']
    assert sha(tmp_path/'C2_PRIOR.npz')==bindings['C2_PRIOR.npz']
    assert not (tmp_path/'C2_VALIDATION.json').exists()
    with pytest.raises(ValueError,match='already exists'):frozen_replay.replay(tmp_path)
