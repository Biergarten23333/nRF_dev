"""Synthetic artifact integrity and unchanged continuous H replay ownership."""
import json

import numpy as np
import pytest

from biospur_fusion.c2_five_calibration import holdout, shared_candidate as candidate
from biospur_fusion.c2_five_calibration.replay import _array_digest, parameter_digest
from biospur_fusion.c2_sparse_nodes.inputs import NODES, sha


def put(path, value):
    path.write_text(json.dumps(value))
    return sha(path)


@pytest.fixture
def frozen(tmp_path, monkeypatch):
    out = tmp_path / 'candidate'; out.mkdir()
    inputs = tmp_path / 'inputs'; inputs.mkdir()
    upstream = tmp_path / 'upstream'; upstream.mkdir()
    surface, smpl = tmp_path / 'surface.json', tmp_path / 'smpl.pkl'
    surface.write_text('{}'); smpl.write_bytes(b'synthetic model')
    (upstream / 'model').write_bytes(b'synthetic weights')
    core = tmp_path / 'core.py'; core.write_text('# synthetic numerical owner\n')
    sources = {'core.py': sha(core)}
    for key, value in dict(ROOT=tmp_path, INPUT_RUN=inputs, SURFACE=surface, SMPL=smpl,
                           DEFAULT_UPSTREAM=upstream).items():
        monkeypatch.setattr(candidate, key, value)
    monkeypatch.setattr(candidate, 'fingerprint', lambda: {'core.py': sha(core)})
    monkeypatch.setattr(candidate, 'verify_assets', lambda: {'files': {'model': {'sha256': sha(upstream/'model')}}})
    names = [prefix + '_action' for prefix in sorted(candidate.FIT)]
    contracts = {name: {'lo': i, 'hi': i + .5} for i, name in enumerate(names)}
    audit = dict(contracts=contracts, consumed_nodes=list(NODES), continuous_states=5, reset_count=0,
                 inter_action_motion_retained=True, gap_policy='NO_SYNTHETIC_UPDATES', clock_sha256='clock')
    put(inputs/'CALIBRATION_INPUT_AUDIT.json', audit)
    put(inputs/'HOLDOUT_INPUT_AUDIT.json', audit)
    (inputs/'CALIBRATION_CONTINUOUS_INPUT.npz').write_bytes(b'five-only synthetic source placeholder')
    (inputs/'HOLDOUT_CONTINUOUS_INPUT.npz').write_bytes(b'five-only synthetic H placeholder')
    put(out/'TASK_CONTRACT.json', {'schema': 'synthetic'})
    frontend = {'fixed_heading': [.2, -.1, .3, -.2]}
    geometry = {'nominal_sensor_levers_m': np.zeros((5,3)).tolist(), 'sensor_vertices': [1,2,3,4,5]}
    initial = np.tile(np.eye(3), (24,1,1))
    levers = np.zeros((5,3)); delta = np.zeros(4)
    models = {'upstream/model': sha(upstream/'model'), 'smpl.pkl': sha(smpl)}
    provenance = dict(source_sha256=sources, model_sha256=models,
        input_sha256=sha(inputs/'CALIBRATION_CONTINUOUS_INPUT.npz'),
        input_audit_sha256=sha(inputs/'CALIBRATION_INPUT_AUDIT.json'), surface_sha256=sha(surface),
        task_contract_sha256=sha(out/'TASK_CONTRACT.json'), action_contract_sha256=parameter_digest(contracts))
    binding = dict(continuous_prefix=True, retained_nodes=list(NODES), action_names=names,
        frontend_parameter_sha256=parameter_digest(frontend), geometry_parameter_sha256=parameter_digest(geometry),
        initial_state_sha256=_array_digest({'initial':initial}), action_contract_sha256=parameter_digest(contracts),
        provenance={k:provenance[k] for k in ('input_sha256','source_sha256','model_sha256')})
    put(out/'FRONTEND.json', frontend); put(out/'BASELINE_FRONTEND.json', frontend)
    put(out/'GEOMETRY.json', geometry)
    put(out/'INITIAL_STATE.json', dict(global_rotation=initial.tolist(), replay_binding=binding))
    np.savez(out/'C2_PRIOR.npz', time_s=np.arange(12)/20)
    prior = dict(replay_binding=binding, source_sha256=sources, model_sha256=models,
        input_sha256=provenance['input_sha256'], smpl_sha256=sha(smpl), surface_sha256=sha(surface),
        action_contract_sha256=parameter_digest(contracts), output_sha256=sha(out/'C2_PRIOR.npz'))
    put(out/'C2_PRIOR.json', prior)
    np.savez(out/'SHARED_PROBE.npz', synthetic=np.zeros(1))
    proof = dict(status='MECHANISM_PROBE_PASSED_NOT_CALIBRATION_ACCEPTED', accepted=False,
        provenance=provenance, source_sha256=sources, baseline_frontend_sha256=sha(out/'BASELINE_FRONTEND.json'),
        geometry_sha256=sha(out/'GEOMETRY.json'), output_sha256=sha(out/'SHARED_PROBE.npz'))
    put(out/'SHARED_PROBE.json', proof)
    arrays = {name+'/'+field:np.zeros(1) for name in names for field in ('rotation','parameters','time_s','valid')}
    np.savez(out/'SHARED_CALIBRATION.npz', **arrays, delta_rad=delta, sensor_levers_m=levers)
    report = dict(status='SHARED_CANDIDATE_FROZEN_PENDING_SEPARATE_VALIDATION', accepted=False,
        shared_proposal_accepted=False, provenance=provenance, source_sha256=sources,
        fitted_sensor_levers_m=levers.tolist(), heading_increment_rad=delta.tolist(),
        initial_energy=2., final_energy=2., objective_change=0., actions={n:{} for n in names},
        accepted_replay_binding=binding,
        **{k:False for k in ('H_data_opened','ten_node_reference_used','UWB_measurements_used',
            'legacy_validation_gates_claimed','calibration_accuracy_accepted','data_accuracy_accepted')})
    for field, name in dict(output_sha256='SHARED_CALIBRATION.npz', frontend_sha256='FRONTEND.json',
        geometry_sha256='GEOMETRY.json', initial_state_sha256='INITIAL_STATE.json',
        prior_metadata_sha256='C2_PRIOR.json', prior_output_sha256='C2_PRIOR.npz', probe_sha256='SHARED_PROBE.json').items():
        report[field] = sha(out/name)
    put(out/'SHARED_CALIBRATION.json', report)
    return out, inputs, smpl, report


def test_rejected_proposal_remains_only_a_diagnostic_checkpoint(frozen):
    out, _, _, _ = frozen
    with pytest.raises(ValueError, match='diagnostic-only'):
        candidate.resolve_shared_candidate(out, diagnostic_only=False)
    result = candidate.resolve_shared_candidate(out, diagnostic_only=True)
    assert result['levers'].shape == (5,3)
    assert not (out/'C2_VALIDATION.json').exists()
    assert not (out/'PHYSICAL_CALIBRATION.json').exists()


@pytest.mark.parametrize('name', ['FRONTEND.json','GEOMETRY.json','INITIAL_STATE.json','C2_PRIOR.json',
    'C2_PRIOR.npz','SHARED_CALIBRATION.npz','SHARED_PROBE.json','SHARED_PROBE.npz',
    'BASELINE_FRONTEND.json','TASK_CONTRACT.json'])
def test_each_artifact_link_is_required(frozen, name):
    out, _, _, _ = frozen
    with (out/name).open('ab') as stream: stream.write(b' ')
    with pytest.raises(ValueError, match='changed'):
        candidate.resolve_shared_candidate(out, diagnostic_only=True)


def test_numerical_source_change_cannot_be_approved_as_adapter(frozen):
    out, _, _, report = frozen
    (candidate.ROOT/'core.py').write_text('# changed numerical owner\n')
    compatibility = dict(schema='biospur-shared-replay-compatibility-v1',
        producer_sources_sha256=parameter_digest(report['source_sha256']),
        adapters={'core.py': {'before':report['source_sha256']['core.py'], 'after':sha(candidate.ROOT/'core.py')}})
    with pytest.raises(ValueError, match='only named replay adapters'):
        candidate.resolve_shared_candidate(out, diagnostic_only=True, compatibility=compatibility)


def test_new_adapter_requires_exact_review_record(frozen):
    out, _, _, report = frozen
    name = 'src/biospur_fusion/c2_five_calibration/shared_candidate.py'
    path = candidate.ROOT/name; path.parent.mkdir(parents=True); path.write_text('# synthetic adapter\n')
    with pytest.raises(ValueError, match='producer source differs'):
        candidate.resolve_shared_candidate(out, diagnostic_only=True)
    compatibility = dict(schema='biospur-shared-replay-compatibility-v1',
        producer_sources_sha256=parameter_digest(report['source_sha256']),
        adapters={name:{'before':None,'after':sha(path)}})
    candidate.resolve_shared_candidate(out, diagnostic_only=True, compatibility=compatibility)
    path.write_text('# changed again\n')
    with pytest.raises(ValueError, match='producer source differs'):
        candidate.resolve_shared_candidate(out, diagnostic_only=True, compatibility=compatibility)


@pytest.mark.parametrize('change', ['parameters', 'missing-action', 'status', 'energy'])
def test_malformed_shared_checkpoint_fails(frozen, change):
    out, _, _, report = frozen
    if change == 'parameters': report['heading_increment_rad'][0] = .1
    elif change == 'missing-action': report['actions'].pop(next(iter(report['actions'])))
    elif change == 'status': report['status'] = 'RUNNING'
    else: report['final_energy'] = float('inf')
    put(out/'SHARED_CALIBRATION.json', report)
    with pytest.raises(ValueError): candidate.resolve_shared_candidate(out, diagnostic_only=True)


def test_shared_h_preserves_one_recurrence_and_global_phase(frozen, monkeypatch):
    out, inputs, smpl, _ = frozen
    for name, value in dict(INPUT_RUN=inputs, SMPL=smpl).items(): monkeypatch.setattr(holdout,name,value)
    monkeypatch.setattr(holdout,'verify_assets',lambda:None)
    monkeypatch.setattr(holdout,'inference_sources',lambda:{'synthetic':'fixed'})
    monkeypatch.setattr(holdout,'resolve_shared_candidate',candidate.resolve_shared_candidate)
    calls = []
    def load(path):
        t = [.22,.3,.4] if path.name.startswith('HOLDOUT') else [0.,.1,.2]
        return {'_continuous':{n:{'imu':np.column_stack((t,np.zeros((3,10))))} for n in NODES}}
    monkeypatch.setattr(holdout,'load_input',load)
    def prepare(joined, frontend, geometry):
        assert frontend['fixed_heading'] == [.2,-.1,.3,-.2]  # No second heading increment.
        assert all(len(joined[n]['imu'])==6 for n in NODES)
        time = np.arange(12)/20
        return dict(time_s=time,features=np.ones((12,5,12)), orientation=np.tile(np.eye(3),(12,5,1,1)),
                    acceleration_mps2=np.zeros((12,5,3)), input_valid=np.array([True]*8+[False]+[True]*3))
    monkeypatch.setattr(holdout,'prepare',prepare)
    monkeypatch.setattr(holdout,'load_pose',lambda *args:(None,None))
    class Stream:
        def __init__(self, poser, *, initial_global_rotation, sensor_vertices):
            calls.append(('initial',np.asarray(initial_global_rotation)))
        def run(self, features, **kwargs):
            np.testing.assert_array_equal(kwargs['input_valid'], np.array([True]*8+[False]+[True]*3))
            calls.append(('frames',len(features)))
            return {'global_rotation':np.tile(np.eye(3),(12,24,1,1))},{'fake':True}
    monkeypatch.setattr(holdout,'ChunkedPoseStream',Stream)
    def solve(prior, observed, acc, valid, time, geometry, levers, **kwargs):
        np.testing.assert_array_equal(time,np.array([.3,.45]))
        calls.append(('physical',len(time)))
        return prior,{'action_labels_consumed':False}
    monkeypatch.setattr(holdout,'solve_pose',solve)
    holdout.replay(out,diagnostic_only=True,calibration_kind='shared')
    assert [name for name,_ in calls] == ['initial','frames','physical']
    record=json.loads((out/'H_REPLAY.json').read_text())
    assert record['diagnostic_only'] and not record['product_accepted']
    assert not record['legacy_C2_validation_gates_claimed']
    with pytest.raises(ValueError, match='already exists'):
        holdout.replay(out,diagnostic_only=True,calibration_kind='shared')


def test_shared_neural_reuse_is_rejected_before_loading(tmp_path):
    with pytest.raises(ValueError,match='cannot reuse'):
        holdout.replay(tmp_path,diagnostic_only=True,calibration_kind='shared',neural_source=tmp_path/'other')
