"""Arrived-prefix shared parameter updates and future-evidence rejection."""
import copy
import numpy as np
import pytest
import torch
from test_c2_shared_fit import fixture, TRUTH
from test_c2_joint_kinematics import geometry
from biospur_fusion.c2_coupled_progressive.contracts import EPISODES
from biospur_fusion.c2_five_calibration.shared_fit import fit_shared_orientation,_tape


def prefix_fixture():
    actions,factors,heading,replay,calls=fixture()
    mapping=dict(zip(actions,EPISODES));names=list(actions)[:4]
    def convert(data):return {mapping[n]:data[n] for n in names}
    for rows in factors.values():
        for row in rows:row['action']=mapping[row['action']]
    def callback(delta):
        result=replay(delta);result['actions']=convert(result['actions']);return result
    return convert(actions),factors,heading,callback,calls


def test_prefix_shared_parameters_update_with_replayed_acceptance():
    torch.set_num_threads(1)
    actions,factors,heading,replay,calls=prefix_fixture()
    checkpoint,audit=fit_shared_orientation(actions,geometry(),np.zeros((5,3)),factors,heading,replay,
        prefix=True,iterations=60,pose_iterations=4,outer_rounds=1,wall_limit_s=120)
    assert np.max(abs(np.rad2deg(checkpoint['delta'].numpy()-TRUTH)))<5
    assert len(calls)>=2
    assert audit['action_count']==4 and not audit['complete_recorded_C2']
    assert audit['rounds'][0]['accepted']
    assert audit['prefix_last_action']==EPISODES[3]


def test_prefix_rejects_skipped_and_holdout_actions():
    actions,*_=prefix_fixture()
    bad=copy.deepcopy(actions);bad.pop(EPISODES[1])
    with pytest.raises(ValueError):_tape(bad,prefix=True)
    bad=copy.deepcopy(actions);bad['H01_boxing']=next(iter(actions.values()))
    with pytest.raises(ValueError):_tape(bad,prefix=True)
    with pytest.raises(ValueError):_tape(actions)


def test_replay_cannot_add_future_action():
    actions,factors,heading,replay,_=prefix_fixture()
    def contaminated(delta):
        r=replay(delta);r['actions'][EPISODES[4]]=copy.deepcopy(next(iter(r['actions'].values())))
        return r
    with pytest.raises(ValueError,match='changed the C2 action tape'):
        fit_shared_orientation(actions,geometry(),np.zeros((5,3)),factors,heading,contaminated,prefix=True)


def test_prefix_evaluator_rebuilds_recurrence_and_rejects_future_contract(monkeypatch):
    from test_c2_shared_replay import fixture as replay_fixture,install_recording_stream
    from biospur_fusion.c2_five_calibration.replay import C2ReplayEvaluator
    episodes,c,g=replay_fixture();sample=episodes['00_initial_still']
    names=list(EPISODES[:4]);episodes={n:copy.deepcopy(sample) for n in names}
    episodes['_continuous']=copy.deepcopy(sample)
    c.update(prefix_last_action=names[-1],fit_actions=sorted(names))
    contracts={n:dict(lo=1200.,hi=1202.) for n in names}
    provenance=dict(input_sha256='test',source_sha256='test',model_sha256='test')
    calls=install_recording_stream(monkeypatch)
    evaluate=C2ReplayEvaluator(episodes,c,g,None,contracts,provenance,prefix=True,continuous_grid=True)
    a=evaluate(np.zeros(4));b=evaluate(np.array([.1,0,0,0]))
    assert len(calls)==2 and set(a['actions'])==set(names)
    assert a['binding']['full_recorded_C2'] is False
    assert not np.array_equal(calls[0].features,calls[1].features)
    for n in names:np.testing.assert_array_equal(a['actions'][n]['time_s'],b['actions'][n]['time_s'])
    contracts[EPISODES[4]]=dict(lo=1200.,hi=1202.)
    with pytest.raises(ValueError,match='cutoff mismatch'):
        C2ReplayEvaluator(episodes,c,g,None,contracts,provenance,prefix=True)


def test_shared_update_beats_matched_fixed_heading_control():
    torch.set_num_threads(1)
    actions,factors,heading,replay,_=prefix_fixture()
    _,audit=fit_shared_orientation(actions,geometry(),np.zeros((5,3)),factors,heading,replay,
        prefix=True,matched_control=True,iterations=30,pose_iterations=3,outer_rounds=1,wall_limit_s=120)
    r=audit['rounds'][0]
    assert r['matched_optimizer_steps']==33
    assert r['accepted'] and r['replayed_energy']<r['fixed_heading_control_energy']
