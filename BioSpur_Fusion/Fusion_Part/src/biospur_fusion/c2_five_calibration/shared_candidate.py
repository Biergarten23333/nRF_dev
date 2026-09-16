"""Read-only binding boundary for diagnostic shared-calibration consumers."""
import json
from pathlib import Path

import numpy as np

from biospur_fusion.c2_imucoco.upstream import DEFAULT_UPSTREAM, verify_assets
from biospur_fusion.c2_imucoco.workflow import INPUT_RUN, SURFACE
from biospur_fusion.c2_sparse_nodes.inputs import NODES, ROOT, sha
from .frontend import FIT
from .replay import _array_digest, parameter_digest
from .workflow import SMPL, fingerprint
from .shared_fit import LEVER_RADIUS_M
from .rotation_equivalence import verify_equivalent_rotation

# These files own artifact/replay boundaries, not calibration or pose math.
ADAPTER_SOURCES = frozenset({
    'src/biospur_fusion/c2_five_calibration/shared_candidate.py',
    'src/biospur_fusion/c2_five_calibration/rotation_equivalence.py',
    'src/biospur_fusion/c2_five_calibration/holdout.py',
    'src/biospur_fusion/c2_five_calibration/frozen_replay.py',
    'tools/run_c2_five_holdout.py', 'tools/run_c2_five_frozen_replay.py',
    'tools/run_c2_five_isolated.py',
})


def _read(path):
    return json.loads(path.read_text())


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _finite(value, shape, name):
    array = np.asarray(value, dtype=float)
    _require(array.shape == shape and np.isfinite(array).all(), 'invalid ' + name)
    return array


def _source_check(producer, compatibility, *, out=None):
    """Check the whole current source closure; exceptions name exact old/new bytes.

    The caller supplies a separately reviewed compatibility record. This is an
    integrity check, not a way for a record's claimed reviewer to grant approval.
    An absent producer entry is represented by null only for new adapter files.
    """
    current = fingerprint()
    for name in ('tools/run_c2_five_shared_calibration.py', *sorted(ADAPTER_SOURCES)):
        if (ROOT / name).is_file():
            current[name] = sha(ROOT / name)
    _require(isinstance(producer, dict) and bool(producer), 'missing producer source map')
    record = compatibility or {}
    changes = record.get('adapters', {})
    _require(isinstance(changes, dict) and set(changes) <= ADAPTER_SOURCES,
             'compatibility may cover only named replay adapters')
    if record:
        _require(record.get('schema') in ('biospur-shared-replay-compatibility-v1',
                                          'biospur-shared-replay-compatibility-v2')
                 and record.get('producer_sources_sha256') == parameter_digest(producer),
                 'compatibility producer binding differs')
    equivalent = None
    if 'numerical_equivalence' in record:
        _require(record.get('schema') == 'biospur-shared-replay-compatibility-v2',
                 'numerical equivalence requires explicit v2 compatibility')
        equivalent = verify_equivalent_rotation(out, record['numerical_equivalence'], producer, current)
    for name in set(producer) | set(current):
        before, after = producer.get(name), current.get(name)
        if before != after:
            if name == equivalent:
                continue
            _require(name in changes and after is not None
                     and changes[name] == {'before': before, 'after': after},
                     'producer source differs: ' + name)
    for name, change in changes.items():
        _require(change == {'before': producer.get(name), 'after': current.get(name)}
                 and current.get(name) is not None, 'stale adapter compatibility: ' + name)
    return current


def resolve_shared_candidate(out, *, diagnostic_only, compatibility=None):
    """Return a frozen diagnostic candidate; never evaluate legacy fit gates.

    Call again after replay and compare bindings/provenance. Neither a lower
    fitting energy nor accepting an outer proposal establishes pose accuracy.
    This resolver loads no H or reference data and invokes no fit/inference.
    """
    _require(diagnostic_only is True, 'shared candidate requires diagnostic-only scope')
    out = Path(out).resolve()
    report = _read(out / 'SHARED_CALIBRATION.json')
    _require(report.get('status') == 'SHARED_CANDIDATE_FROZEN_PENDING_SEPARATE_VALIDATION'
             and report.get('accepted') is False, 'incomplete shared candidate status')
    for name in ('H_data_opened', 'ten_node_reference_used', 'UWB_measurements_used',
                 'legacy_validation_gates_claimed', 'calibration_accuracy_accepted',
                 'data_accuracy_accepted'):
        _require(report.get(name) is False, 'invalid shared scope: ' + name)
    _require(type(report.get('shared_proposal_accepted')) is bool, 'missing proposal disposition')
    provenance = report['provenance']
    bound = {
        'SHARED_CALIBRATION.npz': report['output_sha256'],
        'FRONTEND.json': report['frontend_sha256'], 'GEOMETRY.json': report['geometry_sha256'],
        'INITIAL_STATE.json': report['initial_state_sha256'],
        'C2_PRIOR.json': report['prior_metadata_sha256'], 'C2_PRIOR.npz': report['prior_output_sha256'],
        'SHARED_PROBE.json': report['probe_sha256'], 'TASK_CONTRACT.json': provenance['task_contract_sha256'],
    }
    if report.get('shared_fit',{}).get('continuous'):
        _require(bool(report.get('continuous_output_sha256')), 'missing continuous checkpoint binding')
        bound['CONTINUOUS_CALIBRATION.npz']=report['continuous_output_sha256']
    for name, expected in bound.items():
        _require(sha(out / name) == expected, 'shared artifact changed: ' + name)
    proof = _read(out / 'SHARED_PROBE.json')
    _require(proof.get('status') == 'MECHANISM_PROBE_PASSED_NOT_CALIBRATION_ACCEPTED'
             and proof.get('accepted') is False and proof.get('provenance') == provenance,
             'shared probe provenance/status differs')
    for name, expected in {'BASELINE_FRONTEND.json': proof['baseline_frontend_sha256'],
                           'GEOMETRY.json': proof['geometry_sha256'],
                           'SHARED_PROBE.npz': proof['output_sha256']}.items():
        _require(sha(out / name) == expected, 'shared probe artifact changed: ' + name)
        bound[name] = expected
    bound['SHARED_CALIBRATION.json'] = sha(out / 'SHARED_CALIBRATION.json')
    replay_contract = out / 'SHARED_REPLAY_CONTRACT.json'
    if replay_contract.exists():
        _require(_read(replay_contract).get('compatibility') == compatibility,
                 'replay contract compatibility differs')
        bound[replay_contract.name] = sha(replay_contract)
    frontend, geometry = _read(out / 'FRONTEND.json'), _read(out / 'GEOMETRY.json')
    initial_record, prior = _read(out / 'INITIAL_STATE.json'), _read(out / 'C2_PRIOR.json')
    initial = _finite(initial_record['global_rotation'], (24, 3, 3), 'initial rotations')
    levers = _finite(report['fitted_sensor_levers_m'], (5, 3), 'sensor levers')
    delta = _finite(report['heading_increment_rad'], (4,), 'heading increment')
    nominal = _finite(geometry['nominal_sensor_levers_m'], (5, 3), 'nominal levers')
    _require(np.max(np.abs(levers - nominal)) <= LEVER_RADIUS_M + 1e-10, 'lever bounds changed')
    for key in ('initial_energy', 'final_energy', 'objective_change'):
        _require(np.isfinite(float(report[key])), 'nonfinite recorded energy: ' + key)
    audit = _read(INPUT_RUN / 'CALIBRATION_INPUT_AUDIT.json')
    contracts = audit['contracts']
    _require(len(contracts) == 19 and {name[:2] for name in contracts} == FIT,
             'missing declared C2 actions')
    _require(set(report['actions']) == set(contracts), 'incomplete shared action records')
    with np.load(out / 'SHARED_CALIBRATION.npz', allow_pickle=False) as archive:
        expected = {'delta_rad', 'sensor_levers_m'} | {
            name + '/' + field for name in contracts for field in ('rotation', 'parameters', 'time_s', 'valid')}
        _require(set(archive.files) == expected, 'shared checkpoint array schema differs')
        _require(np.array_equal(archive['delta_rad'], delta)
                 and np.array_equal(archive['sensor_levers_m'], levers), 'JSON/NPZ calibration differs')
    binding = report['accepted_replay_binding']
    _require(binding.get('continuous_prefix') is True and binding.get('retained_nodes') == list(NODES)
             and set(binding.get('action_names', [])) == set(contracts), 'invalid continuous replay binding')
    _require(binding['frontend_parameter_sha256'] == parameter_digest(frontend)
             and binding['geometry_parameter_sha256'] == parameter_digest(geometry)
             and binding['initial_state_sha256'] == _array_digest({'initial': initial}),
             'candidate parameter/replay binding differs')
    _require(isinstance(prior.get('replay_binding'), dict)
             and {'continuous_prefix', 'frontend_parameter_sha256', 'geometry_parameter_sha256',
                  'initial_state_sha256'} <= set(prior['replay_binding']), 'missing prior replay binding')
    _require(initial_record['replay_binding'] == binding
             and all(binding.get(k) == v for k, v in prior['replay_binding'].items()),
             'initial/prior checkpoint binding differs')
    _require(prior['output_sha256'] == bound['C2_PRIOR.npz'], 'prior output binding differs')
    contract_hash = parameter_digest(contracts)
    _require(all(value == contract_hash for value in (
        provenance['action_contract_sha256'], binding['action_contract_sha256'],
        prior['action_contract_sha256'])), 'action contract binding differs')
    producer = provenance['source_sha256']
    _require(report['source_sha256'] == proof['source_sha256'] == prior['source_sha256'] == producer,
             'producer source maps differ')
    inference = _source_check(producer, compatibility, out=out)
    actual = {
        'input_sha256': sha(INPUT_RUN / 'CALIBRATION_CONTINUOUS_INPUT.npz'),
        'input_audit_sha256': sha(INPUT_RUN / 'CALIBRATION_INPUT_AUDIT.json'),
        'surface_sha256': sha(SURFACE),
    }
    _require(all(provenance[k] == v for k, v in actual.items()), 'C2 input provenance changed')
    manifest = verify_assets()
    models = {str((DEFAULT_UPSTREAM / name).relative_to(ROOT)): row['sha256']
              for name, row in manifest['files'].items()}
    models[str(SMPL.relative_to(ROOT))] = sha(SMPL)
    _require(provenance['model_sha256'] == prior['model_sha256'] == models,
             'released model provenance changed')
    _require(prior['smpl_sha256'] == models[str(SMPL.relative_to(ROOT))]
             and prior['input_sha256'] == actual['input_sha256']
             and prior['surface_sha256'] == actual['surface_sha256'], 'prior input binding differs')
    _require(binding['provenance'] == {k: provenance[k] for k in
             ('input_sha256', 'source_sha256', 'model_sha256')}, 'callback provenance differs')
    return dict(bindings=bound, frontend=frontend, geometry=geometry, initial=initial,
                levers=levers, producer_sources=producer, inference_sources=inference,
                provenance=provenance)
