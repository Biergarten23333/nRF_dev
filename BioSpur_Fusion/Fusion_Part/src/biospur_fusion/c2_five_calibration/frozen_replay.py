"""Label-free continuous C2 pose inference from a frozen shared candidate.

Calibration's protocol-conditioned action poses are never runtime inputs.
The saved full neural prior is candidate-bound; downsampling has one global
phase and pose inference has no per-action reset or action-angle target.
"""
import json
from pathlib import Path
import time

import numpy as np

from biospur_fusion.c2_imucoco.workflow import write
from biospur_fusion.c2_sparse_nodes.inputs import ROOT,sha
from .shared_candidate import resolve_shared_candidate
from .solver import solve_pose
from .workflow import fingerprint,guard


def continuous_inputs(path):
    with np.load(path,allow_pickle=False) as archive:
        expected={'time_s','prior','observed','acceleration','valid'}
        if set(archive.files)!=expected:raise ValueError('exact continuous prior fields required')
        data={k:archive[k] for k in expected}
    n=len(data['time_s'])
    shapes={'time_s':(n,), 'prior':(n,24,3,3), 'observed':(n,5,3,3),
            'acceleration':(n,5,3), 'valid':(n,)}
    if n<90 or any(data[k].shape!=shape or not np.isfinite(data[k]).all() for k,shape in shapes.items()):
        raise ValueError('invalid continuous five-node prior shape or values')
    if data['valid'].dtype!=np.bool_ or not np.allclose(np.diff(data['time_s']),1/60,atol=1e-6,rtol=0):
        raise ValueError('original uniform60Hz time and boolean support required')
    return {k:v[::3] for k,v in data.items()}


def execution_sources():
    name='tools/run_c2_five_frozen_replay.py'
    return {**fingerprint(),name:sha(ROOT/name)}


def replay(out, *, probe=False, resource_probe=False):
    out=Path(out).resolve();started=time.monotonic()
    if probe and resource_probe:raise ValueError('choose one frozen probe mode')
    label='C2_FROZEN_PROBE' if probe else 'C2_FROZEN_RESOURCE_PROBE' if resource_probe else 'C2_FROZEN_REPLAY'
    guard(out,label)
    if (out/(label+'_START.json')).exists():raise ValueError('preserve the started frozen replay')
    contract_path=out/'SHARED_REPLAY_CONTRACT.json'
    contract=json.loads(contract_path.read_text())
    required=dict(probe_frames=600,probe_iterations=3,probe_wall_s=60,
                  resource_probe_iterations=3,resource_probe_wall_s=60,
                  iterations=150,wall_limit_s=600,global_decimation=3)
    if contract.get('C2_replay_policy')!=required:
        raise ValueError('exact predeclared frozen C2 replay policy required')
    frozen=resolve_shared_candidate(out,diagnostic_only=True,compatibility=contract.get('compatibility'))
    sources=execution_sources()
    bindings={**frozen['bindings'],'SHARED_REPLAY_CONTRACT.json':sha(contract_path)}
    q=continuous_inputs(out/'C2_PRIOR.npz')
    full_frames=len(q['time_s'])
    if full_frames<required['probe_frames']:raise ValueError('full C2 requires at least600frames for the declared probe')
    if probe:q={k:v[:required['probe_frames']] for k,v in q.items()}
    else:
        labels=['C2_FROZEN_PROBE']+([] if resource_probe else ['C2_FROZEN_RESOURCE_PROBE'])
        for proof_label in labels:
            proof=json.loads((out/(proof_label+'.json')).read_text())
            expected_frames=required['probe_frames'] if proof_label=='C2_FROZEN_PROBE' else full_frames
            if (proof.get('status')!='FROZEN_C2_DIAGNOSTIC_NOT_ACCEPTED'
                    or not proof.get('probe') or proof.get('frames')!=expected_frames
                    or proof.get('full_frames')!=full_frames
                    or proof.get('rotation_validated') is not True
                    or proof.get('physical',{}).get('optimizer_steps')!=4
                    or proof['frozen_inputs']!=bindings or proof['source_sha256']!=sources
                    or proof['output_sha256']!=sha(out/(proof_label+'.npz'))):
                raise ValueError('frozen replay probe/source/input changed')
    write(out/(label+'_START.json'),dict(frozen_inputs=bindings,source_sha256=sources,
        frames=len(q['time_s']),full_frames=full_frames,policy=required,
        diagnostic_only=True,calibration_parameter_updates=False,action_labels_consumed=False))
    rotation,physical=solve_pose(**q,geometry=frozen['geometry'],levers=frozen['levers'],
        iterations=required['probe_iterations'] if probe else required['resource_probe_iterations'] if resource_probe else required['iterations'],
        wall_limit_s=required['probe_wall_s'] if probe else required['resource_probe_wall_s'] if resource_probe else required['wall_limit_s'])
    after=resolve_shared_candidate(out,diagnostic_only=True,compatibility=contract.get('compatibility'))
    if (after['bindings']!=frozen['bindings'] or after['provenance']!=frozen['provenance']
            or after['inference_sources']!=frozen['inference_sources']
            or sources!=execution_sources() or any(sha(out/name)!=value for name,value in bindings.items())):
        raise ValueError('source or frozen inputs changed during C2 replay')
    if (rotation.shape!=(len(q['time_s']),24,3,3) or not np.isfinite(rotation).all()
            or not np.allclose(rotation[:,[0,18,19,4,5]],q['observed'],atol=1e-6,rtol=0)
            or not np.allclose(rotation@rotation.transpose(0,1,3,2),np.eye(3),atol=1e-5,rtol=0)
            or not np.allclose(np.linalg.det(rotation),1.,atol=1e-5,rtol=0)):
        raise ValueError('frozen C2 output violates rotation or retained observation contract')
    np.savez_compressed(out/(label+'.npz'),**q,rotation=rotation)
    write(out/(label+'.json'),dict(status='FROZEN_C2_DIAGNOSTIC_NOT_ACCEPTED',
        diagnostic_only=True,accepted=False,calibration_kind='shared',
        calibration_parameter_updates=False,action_labels_consumed=False,
        protocol_conditioned_pose_reused=False,legacy_validation_gates_claimed=False,
        H_data_opened=False,ten_node_reference_used=False,UWB_measurements_used=False,
        global_decimation=3,per_action_reset=False,full_frames=full_frames,
        frames=len(q['time_s']),probe=probe or resource_probe,resource_probe=resource_probe,
        rotation_validated=True,physical=physical,
        frozen_inputs=bindings,source_sha256=sources,producer_sources=frozen['producer_sources'],
        output_sha256=sha(out/(label+'.npz')),wall_s=time.monotonic()-started))
