"""A changed calibration cannot retain a stale neural recurrence or artifact."""
import copy
import json

import numpy as np
import pytest

from biospur_fusion.c2_five_calibration import replay, workflow
from biospur_fusion.c2_five_calibration.frontend import FIT
from biospur_fusion.c2_five_calibration.shared_orientation import with_heading_increment
from biospur_fusion.c2_sparse_nodes.inputs import NODES, sha


def fixture():
    rows = np.zeros((501,11))
    rows[:,0] = 1200.+np.arange(501)/200
    rows[:,1] = 1.
    rows[:,5:8] = [1., .2, 9.80665]
    episode = {node:{'imu':rows.copy()} for node in NODES}
    episodes = {key+'_action':episode for key in FIT}
    episodes['00_initial_still'] = episodes.pop('00_action')
    episodes['_continuous'] = episode
    calibration = dict(pelvis_closure_rad=0., functional_yaw_rad=[0.]*5,
        initial_sensor_rotations=np.tile(np.eye(3),(5,1,1)).tolist(),
        segment_axes_in_sensor=np.tile(np.eye(3),(5,1,1)).tolist(),
        frozen_heading_correction_rad={n:0. for n in NODES[1:]},
        acc_bias_sensor=np.zeros((5,3)).tolist())
    offsets = np.zeros((24,3)); offsets[18] = [1.,0.,0.]; offsets[19] = [-1.,0.,0.]
    geometry = dict(rest_offsets_m=offsets.tolist(), bone_frame_correction=np.tile(np.eye(3),(5,1,1)).tolist())
    return episodes, calibration, geometry


def install_recording_stream(monkeypatch):
    calls = []
    class Stream:
        def __init__(self, poser, *, initial_global_rotation, sensor_vertices):
            self.initial = initial_global_rotation.copy()
            self.calls = 0
            calls.append(self)

        def run(self, features, **kwargs):
            self.calls += 1
            self.features = features.copy()
            self.valid = kwargs['input_valid'].copy()
            # This stateful fake exposes the entire prefix dependence. It is
            # an ownership test, not a pose-accuracy or calibration oracle.
            self.state = np.cumsum(features[:,0,6])
            return {'global_rotation':np.repeat(self.initial[None],len(features),axis=0)}, {'fake':True}

    monkeypatch.setattr(replay, 'ChunkedPoseStream', Stream)
    return calls


def test_changed_frontend_reencodes_and_reinitializes_one_continuous_stream(monkeypatch):
    episodes, c, g = fixture()
    calls = install_recording_stream(monkeypatch)
    original = copy.deepcopy(c)
    before, audit0, initial0 = replay.replay_prior(episodes, c, g, None)
    changed = with_heading_increment(c, np.array([.4, -.3, .2, -.1]))
    after, audit1, initial1 = replay.replay_prior(episodes, changed, g, None)
    assert len(calls) == 2 and all(x.calls == 1 for x in calls)
    assert not np.array_equal(calls[0].features, calls[1].features)
    assert not np.array_equal(initial0, initial1)
    np.testing.assert_array_equal(after['time_s'], before['time_s'])
    np.testing.assert_array_equal(after['valid'], before['valid'])
    np.testing.assert_array_equal(calls[0].valid, before['valid'])
    np.testing.assert_array_equal(calls[1].valid, after['valid'])
    b0, b1 = audit0['replay_binding'], audit1['replay_binding']
    for field in ('frontend_parameter_sha256', 'prepared_stream_sha256', 'features_sha256', 'initial_state_sha256'):
        assert b0[field] != b1[field]
    assert b0['geometry_parameter_sha256'] == b1['geometry_parameter_sha256']
    assert b0['time_range_s'] == b1['time_range_s']
    assert b1['continuous_prefix'] and len(b1['action_names']) == 19
    assert not b1['prior_reused_from_other_calibration']
    assert c == original


@pytest.mark.parametrize('defect', ['H', 'missing', 'removed'])
def test_bad_calibration_scope_fails_before_network(monkeypatch, defect):
    episodes, c, g = fixture()
    calls = install_recording_stream(monkeypatch)
    if defect == 'H': episodes['H01_boxing'] = episodes['_continuous']
    elif defect == 'missing': del episodes['19_action']
    else: episodes['19_action'] = {**episodes['19_action'], 'REMOVED':{'imu':np.zeros((4,11))}}
    with pytest.raises(ValueError): replay.replay_prior(episodes, c, g, None)
    assert not calls


def test_mutated_calibration_during_replay_fails(monkeypatch):
    episodes, c, g = fixture()
    class MutatingStream:
        def __init__(self, *args, **kwargs): pass
        def run(self, features, **kwargs):
            c['functional_yaw_rad'][1] += .1
            return {}, {}
    monkeypatch.setattr(replay, 'ChunkedPoseStream', MutatingStream)
    with pytest.raises(ValueError, match='changed during replay'):
        replay.replay_prior(episodes, c, g, None)


def test_arrived_prefix_replay_does_not_require_future_actions(monkeypatch):
    from biospur_fusion.c2_coupled_progressive.contracts import EPISODES
    original,c,g=fixture();episode=original['_continuous']
    names=EPISODES[:14]
    episodes={name:copy.deepcopy(episode) for name in names}
    episodes['_continuous']=copy.deepcopy(episode)
    c.update(prefix_last_action=names[-1],fit_actions=sorted(names))
    calls=install_recording_stream(monkeypatch)
    _,audit,_=replay.replay_prior(episodes,c,g,None,prefix=True)
    assert len(calls)==1
    assert audit['replay_binding']['prefix_last_action']==names[-1]
    assert audit['replay_binding']['full_recorded_C2'] is False
    # A future row in any retained continuous stream is forbidden even though
    # the action dictionary and calibration labels still name an early prefix.
    a=episodes['_continuous'][NODES[0]]['imu']
    extra=a[-1:].copy();extra[:,0]+=.005
    episodes['_continuous'][NODES[0]]['imu']=np.concatenate((a,extra))
    with pytest.raises(ValueError,match='beyond arrived prefix'):
        replay.replay_prior(episodes,c,g,None,prefix=True)
    assert len(calls)==1


def test_artifact_cannot_pair_prior_with_new_frontend(tmp_path):
    _, c, g = fixture()
    np.savez(tmp_path/'C2_PRIOR.npz', time_s=np.arange(4.))
    binding = dict(continuous_prefix=True, frontend_parameter_sha256=replay.parameter_digest(c),
                   geometry_parameter_sha256=replay.parameter_digest(g))
    (tmp_path/'C2_PRIOR.json').write_text(json.dumps(dict(output_sha256=sha(tmp_path/'C2_PRIOR.npz'), replay_binding=binding)))
    (tmp_path/'GEOMETRY.json').write_text(json.dumps(g))
    (tmp_path/'FRONTEND.json').write_text(json.dumps(with_heading_increment(c, np.ones(4)*.1)))
    with pytest.raises(ValueError, match='different calibration'):
        workflow.action_data(tmp_path)


def test_old_unbound_prior_requires_explicit_regeneration(tmp_path):
    np.savez(tmp_path/'C2_PRIOR.npz', time_s=np.arange(4.))
    (tmp_path/'C2_PRIOR.json').write_text(json.dumps(dict(output_sha256=sha(tmp_path/'C2_PRIOR.npz'))))
    with pytest.raises(ValueError, match='candidate-bound'):
        workflow.action_data(tmp_path)


def test_callback_owns_baseline_and_binds_action_windows(monkeypatch):
    episodes, c, g = fixture()
    calls = install_recording_stream(monkeypatch)
    contracts = {name:dict(lo=1200., hi=1202.) for name in episodes if name != '_continuous'}
    provenance = dict(input_sha256='a'*64, source_sha256={'synthetic':'b'*64}, model_sha256={'fake':'c'*64})
    baseline_digest = replay.parameter_digest(c)
    contract_digest = replay.parameter_digest(contracts)
    evaluator = replay.C2ReplayEvaluator(episodes,c,g,None,contracts,provenance)
    c['functional_yaw_rad'][1] = .7
    contracts['00_initial_still']['lo'] = 1201.
    first = evaluator(np.zeros(4))
    second = evaluator(np.ones(4)*.2)
    assert len(calls) == 2
    assert second['binding']['baseline_frontend_sha256'] == baseline_digest
    assert second['binding']['action_contract_sha256'] == contract_digest
    assert first['binding']['action_support_sha256'] == second['binding']['action_support_sha256']
    assert len(second['actions']) == 19
    assert second['frontend']['functional_yaw_rad'][1] == 0.
    assert second['frontend']['frozen_heading_correction_rad'][NODES[1]] == .2
    assert second['continuous']['prior'].shape[0] == len(calls[1].features)


def test_action_slicing_changes_cannot_reuse_old_prior(tmp_path, monkeypatch):
    _, c, g = fixture()
    np.savez(tmp_path/'C2_PRIOR.npz', time_s=np.arange(4.))
    contracts = {key+'_action':dict(lo=1200.,hi=1202.) for key in FIT}
    (tmp_path/'FRONTEND.json').write_text(json.dumps(c))
    (tmp_path/'GEOMETRY.json').write_text(json.dumps(g))
    binding = dict(continuous_prefix=True, frontend_parameter_sha256=replay.parameter_digest(c),
                   geometry_parameter_sha256=replay.parameter_digest(g))
    (tmp_path/'C2_PRIOR.json').write_text(json.dumps(dict(output_sha256=sha(tmp_path/'C2_PRIOR.npz'),
        replay_binding=binding, action_contract_sha256=replay.parameter_digest(contracts))))
    contracts['02_action']['lo'] += .05
    (tmp_path/'CALIBRATION_INPUT_AUDIT.json').write_text(json.dumps(dict(contracts=contracts)))
    monkeypatch.setattr(workflow, 'INPUT_RUN', tmp_path)
    with pytest.raises(ValueError, match='action-window'):
        workflow.action_data(tmp_path)
