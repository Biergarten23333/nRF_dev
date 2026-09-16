"""Frozen five-node H replay; a failed C2 candidate is diagnostic only."""
import json
from pathlib import Path

import numpy as np

from biospur_fusion.c2_imucoco.backend import ChunkedPoseStream, load_pose
from biospur_fusion.c2_imucoco.upstream import verify_assets
from biospur_fusion.c2_imucoco.workflow import INPUT_RUN, load_input, write
from biospur_fusion.c2_sparse_nodes.inputs import NODES, ROOT, sha
from .frontend import prepare
from .solver import solve_pose
from .shared_candidate import resolve_shared_candidate
from .workflow import SMPL, fingerprint, guard


FROZEN_FILES = (
    'FRONTEND.json', 'GEOMETRY.json', 'INITIAL_STATE.json',
    'PHYSICAL_CALIBRATION.json', 'C2_PRIOR.json', 'C2_VALIDATION.json',
    'TASK_CONTRACT.json',
)


def inference_sources():
    sources = fingerprint()
    entrypoint = 'tools/run_c2_five_holdout.py'
    sources[entrypoint] = sha(ROOT / entrypoint)
    return sources


def frozen_inputs(out, *, diagnostic_only):
    # Pose comparison is a downstream consumer, never a runtime prerequisite.
    # C2_VALIDATION contains only five-node inertial checks and frozen hashes.
    validation = json.loads((out / 'C2_VALIDATION.json').read_text())
    bound = {'PHYSICAL_CALIBRATION.json': validation['calibration_sha256'],
             'GEOMETRY.json': validation['geometry_sha256'],
             'C2_VALIDATION.npz': validation['output_sha256']}
    for name, expected in bound.items():
        if sha(out / name) != expected:
            raise ValueError('validated C2 input changed: ' + name)
    gates = validation['gates']
    required = {'coverage', 'offset_update_convergence', 'offset_design_rank',
                'offset_bounds', 'no_labelled_angles', 'calibration_acceleration',
                'fixed_pose_offset_consistency'}
    if set(gates) != required or any(type(value) is not bool for value in gates.values()):
        raise ValueError('complete five-node validation gates required')
    if not all(gates.values()) and not diagnostic_only:
        raise ValueError('C2 has not passed; H requires explicit diagnostic-only scope')
    return {name: sha(out / name) for name in FROZEN_FILES}


def reuse_neural(source, bindings):
    source = Path(source).resolve()
    record = json.loads((source / 'H_REPLAY.json').read_text())
    if record['output_sha256'] != sha(source / 'H_REPLAY.npz'):
        raise ValueError('reused H artifact changed')
    for name in ('FRONTEND.json', 'GEOMETRY.json', 'INITIAL_STATE.json', 'C2_PRIOR.json'):
        if record['frozen_inputs'][name] != bindings[name]:
            raise ValueError('neural input differs from prior H run: ' + name)
    for name, expected in record['input_sha256'].items():
        if sha(INPUT_RUN / name) != expected:
            raise ValueError('reused H source input changed: ' + name)
    physical_only = {'anatomy.py', 'solver.py', 'tracking.py', 'holdout.py'}
    for name, expected in record['source_sha256'].items():
        path = Path(name)
        if name == 'tools/run_c2_five_holdout.py':
            continue
        if path.parent.name == 'c2_five_calibration' and path.name in physical_only:
            continue
        if sha(ROOT / name) != expected:
            raise ValueError('neural producer code changed: ' + name)
    with np.load(source / 'H_REPLAY.npz') as archive:
        q = {k: archive[k] for k in ('time_s', 'prior', 'observed', 'acceleration', 'valid')}
    return q, record['neural'], dict(source_run=str(source),
        source_record_sha256=sha(source / 'H_REPLAY.json'),
        source_output_sha256=record['output_sha256'],
        previous_physical_output_reused=False,
        neural_wall_time_belongs_to_source_run=True)


def _shared_input_audits():
    names = ('CALIBRATION_INPUT_AUDIT.json', 'HOLDOUT_INPUT_AUDIT.json')
    audits = [json.loads((INPUT_RUN / name).read_text()) for name in names]
    for audit in audits:
        if (audit.get('consumed_nodes') != list(NODES) or audit.get('continuous_states') != 5
                or audit.get('reset_count') != 0 or audit.get('inter_action_motion_retained') is not True
                or audit.get('gap_policy') != 'NO_SYNTHETIC_UPDATES'):
            raise ValueError('continuous five-node filter-state contract required')
    if not audits[0].get('clock_sha256') or audits[0]['clock_sha256'] != audits[1].get('clock_sha256'):
        raise ValueError('C2/H clock provenance differs')
    return {name: sha(INPUT_RUN / name) for name in names}


def replay(out, *, diagnostic_only=False, neural_source=None,
           calibration_kind='legacy', compatibility=None):
    out = Path(out).resolve()
    guard(out, 'H_REPLAY')
    if calibration_kind not in ('legacy', 'shared'):
        raise ValueError('unknown calibration kind')
    shared = None
    input_audits = {}
    runtime_inputs = {}
    if calibration_kind == 'shared':
        if neural_source is not None:
            raise ValueError('shared diagnostic replay cannot reuse another neural run')
        shared = resolve_shared_candidate(out, diagnostic_only=diagnostic_only, compatibility=compatibility)
        bindings = shared['bindings']
        input_audits = _shared_input_audits()
        runtime_inputs = {name: sha(INPUT_RUN / name) for name in
                          ('CALIBRATION_CONTINUOUS_INPUT.npz', 'HOLDOUT_CONTINUOUS_INPUT.npz')}
    else:
        if compatibility is not None:
            raise ValueError('adapter compatibility applies only to shared candidates')
        bindings = frozen_inputs(out, diagnostic_only=diagnostic_only)
    if (out / 'H_REPLAY_START.json').exists():
        raise ValueError('H attempt already started; preserve its evidence')
    verify_assets()
    source = inference_sources()
    prior_record = json.loads((out / 'C2_PRIOR.json').read_text())
    if sha(SMPL) != prior_record['smpl_sha256']:
        raise ValueError('SMPL changed since C2 calibration')
    if sha(INPUT_RUN / 'CALIBRATION_CONTINUOUS_INPUT.npz') != prior_record['input_sha256']:
        raise ValueError('C2 source cache changed since calibration')
    c = json.loads((out / 'FRONTEND.json').read_text())
    g = json.loads((out / 'GEOMETRY.json').read_text())
    initial = json.loads((out / 'INITIAL_STATE.json').read_text())['global_rotation']
    levers = (shared['levers'] if shared is not None else
              json.loads((out / 'PHYSICAL_CALIBRATION.json').read_text())['fitted_sensor_levers_m'])
    write(out / 'H_REPLAY_START.json', dict(
        diagnostic_only=diagnostic_only, frozen_inputs=bindings,
        calibration_kind=calibration_kind, adapter_compatibility=compatibility,
        producer_sources=None if shared is None else shared['producer_sources'],
        input_audit_sha256=input_audits, start_input_sha256=runtime_inputs,
        legacy_C2_validation_gates_claimed=False,
        source_sha256=source, acceptance_gates_changed=False,
        purpose='Reconstruct H with the frozen candidate; no calibration or model fitting on H',
        stage_wall_limit_s=600, physical_wall_limit_s=120,
    ))
    reuse = None
    if neural_source is not None:
        q, neural_audit, reuse = reuse_neural(neural_source, bindings)
    else:
        cal = load_input(INPUT_RUN / 'CALIBRATION_CONTINUOUS_INPUT.npz')
        hold = load_input(INPUT_RUN / 'HOLDOUT_CONTINUOUS_INPUT.npz')
        joined = {}
        for node in NODES:
            a, b = cal['_continuous'][node]['imu'], hold['_continuous'][node]['imu']
            if a[-1, 0] >= b[0, 0]:
                raise ValueError('C2/H source boundary overlaps or goes backwards')
            joined[node] = {'imu': np.concatenate((a, b))}
        prepared = prepare(joined, c, g)
        poser, _ = load_pose(SMPL, out)
        stream = ChunkedPoseStream(poser, initial_global_rotation=initial,
                                   sensor_vertices=g['sensor_vertices'])
        output, neural_audit = stream.run(prepared['features'], input_valid=prepared['input_valid'], wall_limit_s=600,
            progress=lambda item: print(json.dumps(item), flush=True))
        # Same grid throughout C2 and H; downsampling keeps its original phase.
        first_h = max(hold['_continuous'][n]['imu'][0, 0] for n in NODES)
        ids = np.arange(0, len(prepared['time_s']), 3)
        ids = ids[prepared['time_s'][ids] >= first_h]
        q = dict(time_s=prepared['time_s'][ids], prior=output['global_rotation'][ids],
            observed=prepared['orientation'][ids], acceleration=prepared['acceleration_mps2'][ids],
            valid=prepared['input_valid'][ids])
    # One physical solve includes H01, H02 and the intervening recorded motion.
    rotation, physical = solve_pose(q['prior'], q['observed'], q['acceleration'],
        q['valid'], q['time_s'], g, levers, iterations=150, wall_limit_s=120)
    for name, expected in bindings.items():
        if sha(out / name) != expected:
            raise ValueError('frozen parameter changed during H replay: ' + name)
    if source != inference_sources():
        raise ValueError('source changed during H replay')
    if shared is not None:
        after = resolve_shared_candidate(out, diagnostic_only=True, compatibility=compatibility)
        if (after['bindings'] != bindings or after['provenance'] != shared['provenance']
                or after['inference_sources'] != shared['inference_sources']
                or _shared_input_audits() != input_audits
                or any(sha(INPUT_RUN / name) != expected for name, expected in runtime_inputs.items())):
            raise ValueError('shared replay binding changed during inference')
    np.savez_compressed(out / 'H_REPLAY.npz', **q, rotation=rotation)
    write(out / 'H_REPLAY.json', dict(
        status='H_DIAGNOSTIC_NOT_ACCEPTED' if diagnostic_only else 'H_REPLAY_PENDING_ASSESSMENT',
        diagnostic_only=diagnostic_only, product_accepted=False,
        calibration_kind=calibration_kind, adapter_compatibility=compatibility,
        producer_sources=None if shared is None else shared['producer_sources'],
        input_audit_sha256=input_audits, start_input_sha256=runtime_inputs,
        legacy_C2_validation_gates_claimed=False,
        frozen_inputs=bindings, source_sha256=source, neural=neural_audit,
        physical=physical, H_frames=len(q['time_s']), H_used_for_calibration=False,
        neural_reuse=reuse,
        ten_node_reference_opened=False, UWB_measurements_used=False,
        recurrent_reset_at_H_boundary=False, physical_reset_between_H_actions=False,
        inference='offline multi-frame reconstruction; not causal online performance',
        input_sha256={name: sha(INPUT_RUN / name) for name in
            ('CALIBRATION_CONTINUOUS_INPUT.npz', 'HOLDOUT_CONTINUOUS_INPUT.npz')},
        output_sha256=sha(out / 'H_REPLAY.npz'),
    ))
