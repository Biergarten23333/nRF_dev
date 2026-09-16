"""Probe and artifact boundaries for prior-conditioned all-C2 shared fitting.

This owner opens only the declared five-C2 archive and released model assets.
It creates no legacy validation verdict and never consumes holdout data.
"""
import json
import resource
import time
from pathlib import Path

import numpy as np
import torch

from biospur_fusion.c2_sparse_nodes.inputs import ROOT,NODES,sha
from biospur_fusion.c2_imucoco.workflow import INPUT_RUN,SURFACE,load_input,write
from biospur_fusion.c2_imucoco.backend import load_pose
from biospur_fusion.c2_imucoco.upstream import DEFAULT_UPSTREAM,verify_assets
from .frontend import FIT,fit_frontend
from .geometry import subject_geometry,OBSERVED
from .placement import C2_VERTICES,PLACEMENT_EVIDENCE
from .replay import C2ReplayEvaluator,replay_prior,parameter_digest
from .shared_orientation import transport_heading,with_heading_increment
from .shared_fit import fit_shared_orientation
from .calibration_prior import RegisteredHeadingPrior
from .arm_protocol import build_arm_protocol
from .solver import PoseObjective,solve_pose,valid_support
from .workflow import fingerprint,SMPL

POLICY=dict(iterations=60,pose_iterations=60,outer_rounds=1,wall_limit_s=900.)
PROBE_DELTA=np.array([.05,-.04,.03,-.02])
CONTINUOUS_POLICY = {**POLICY, 'wall_limit_s':1500.}
DISK_LIMIT=1_000_000_000


def source_fingerprint():
    return {**fingerprint(),'tools/run_c2_five_shared_calibration.py':sha(ROOT/'tools/run_c2_five_shared_calibration.py')}


def _contract(out):
    value=json.loads((out/'TASK_CONTRACT.json').read_text())
    if (value.get('schema')!='biospur-c2-five-shared-calibration-v1'
            or set(value.get('fit_actions',[]))!=FIT or value.get('validation_actions',[])
            or value.get('shared_fit_policy')!=(CONTINUOUS_POLICY if value.get('continuous_physical_fit',False) else POLICY)
            or value.get('probe_delta_rad')!=PROBE_DELTA.tolist()):
        raise ValueError('shared calibration requires the exact predeclared policy')
    return sha(out/'TASK_CONTRACT.json')


def _guard(out, names):
    if any((out/name).exists() for name in names):
        raise ValueError('stage artifact already exists; preserve previous evidence')


def _disk(out, extra=0):
    size=sum(p.stat().st_size for p in out.rglob('*') if p.is_file() and not p.is_symlink())
    if size+extra>DISK_LIMIT:raise ValueError('shared calibration output exceeds 1 GB bound')


def _save_arrays(out, name, arrays):
    _guard(out,[name]);_disk(out,sum(np.asarray(v).nbytes for v in arrays.values()))
    np.savez_compressed(out/name,**arrays);_disk(out)
    return sha(out/name)


def _provenance(out):
    contract_sha=_contract(out)
    manifest=verify_assets()
    models={str((DEFAULT_UPSTREAM/name).relative_to(ROOT)):entry['sha256']
            for name,entry in manifest['files'].items()}
    models[str(SMPL.relative_to(ROOT))]=sha(SMPL)
    input_path=INPUT_RUN/'CALIBRATION_CONTINUOUS_INPUT.npz'
    audit_path=INPUT_RUN/'CALIBRATION_INPUT_AUDIT.json'
    contracts=json.loads(audit_path.read_text())['contracts']
    if len(contracts)!=19 or {name[:2] for name in contracts}!=FIT:
        raise ValueError('input action contracts must be the 19 recorded C2 actions')
    # Inspect names before decoding any IMU arrays. No selection from a mixed
    # archive and no caller-controlled data path are permitted here.
    expected={f'{action}/{node}/imu' for action in [*contracts,'_continuous'] for node in NODES}
    with np.load(input_path,allow_pickle=False) as archive:
        if len(archive.files)!=len(expected) or set(archive.files)!=expected:
            raise ValueError('archive must contain exactly the declared five-only C2 keys')
    record=dict(input_sha256=sha(input_path),source_sha256=source_fingerprint(),model_sha256=models,
        surface_sha256=sha(SURFACE),input_audit_sha256=sha(audit_path),
        action_contract_sha256=parameter_digest(contracts),task_contract_sha256=contract_sha)
    return record,contracts


def _setup(out):
    episodes=load_input(INPUT_RUN/'CALIBRATION_CONTINUOUS_INPUT.npz')
    surface=json.loads(SURFACE.read_text())
    calibration=fit_frontend(episodes,surface)
    poser,body=load_pose(SMPL,out)  # Upstream changes cwd; all paths here are absolute.
    geometry=subject_geometry(body,surface,sensor_vertices=C2_VERTICES)
    geometry['placement_evidence']=PLACEMENT_EVIDENCE
    return episodes,calibration,geometry,poser


def _progress(value):
    print(json.dumps(value,allow_nan=False),flush=True)


def probe(out):
    out=Path(out).resolve();started=time.monotonic()
    _guard(out,['SHARED_PROBE.json','SHARED_PROBE.npz','BASELINE_FRONTEND.json','GEOMETRY.json'])
    provenance,contracts=_provenance(out)
    episodes,baseline,geometry,poser=_setup(out)
    data0,audit0,initial0=replay_prior(episodes,baseline,geometry,poser,probe=True,
        wall_limit_s=120.,progress=_progress)
    proposed=with_heading_increment(baseline,PROBE_DELTA)
    data1,audit1,initial1=replay_prior(episodes,proposed,geometry,poser,probe=True,
        wall_limit_s=120.,progress=_progress)
    if len(data0['time_s'])!=300 or len(data1['time_s'])!=300:
        raise ValueError('shared probe requires two 300-frame standing replays')
    if not np.array_equal(data0['time_s'],data1['time_s']) or not np.array_equal(data0['valid'],data1['valid']):
        raise ValueError('shared probe changed time or valid support')
    expected_r,expected_a=transport_heading(torch.tensor(data0['observed']),
        torch.tensor(data0['acceleration']),torch.tensor(PROBE_DELTA))
    errors=dict(rotation=float(np.max(np.abs(data1['observed']-expected_r.numpy()))),
                acceleration=float(np.max(np.abs(data1['acceleration']-expected_a.numpy()))))
    if max(errors.values())>1e-6:raise ValueError('frontend and direct heading transport disagree')
    binding0,binding1=audit0['replay_binding'],audit1['replay_binding']
    if (binding0['features_sha256']==binding1['features_sha256']
            or binding0['initial_state_sha256']==binding1['initial_state_sha256']):
        raise ValueError('heading change did not regenerate features and initial state')
    arrays={}; physical={}
    for label,data in (('baseline',data0),('changed',data1)):
        q={k:v[::3] for k,v in data.items()}
        rotation,physical[label]=solve_pose(**q,geometry=geometry,
            levers=geometry['nominal_sensor_levers_m'],iterations=30,wall_limit_s=120.)
        if not np.isfinite(rotation).all():raise ValueError('nonfinite probe physical pose')
        arrays.update({label+'/'+k:v for k,v in data.items()})
        arrays[label+'/physical_rotation']=rotation
    difference=float(np.max(np.abs(arrays['changed/physical_rotation']-arrays['baseline/physical_rotation'])))
    if difference<=1e-8:raise ValueError('changed retained orientations did not reach physical output')
    if _provenance(out)[0]!=provenance:raise ValueError('probe inputs or source changed during execution')
    write(out/'BASELINE_FRONTEND.json',baseline);write(out/'GEOMETRY.json',geometry)
    output_sha=_save_arrays(out,'SHARED_PROBE.npz',arrays)
    report=dict(status='MECHANISM_PROBE_PASSED_NOT_CALIBRATION_ACCEPTED',accepted=False,
        provenance=provenance,source_sha256=provenance['source_sha256'],
        baseline_frontend_sha256=sha(out/'BASELINE_FRONTEND.json'),geometry_sha256=sha(out/'GEOMETRY.json'),
        frames_per_replay=300,probe_delta_rad=PROBE_DELTA.tolist(),transport_max_errors=errors,
        physical=physical,physical_rotation_max_change=difference,
        baseline_replay=audit0,changed_replay=audit1,output_sha256=output_sha,
        action_count=len(contracts),H_data_opened=False,ten_node_reference_used=False,
        fitted_heading_claimed=False,data_accuracy_accepted=False,
        process_peak_rss_kib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        resource_scope='this process; external launcher enforces descendant RSS and wall limits',
        wall_s=time.monotonic()-started)
    write(out/'SHARED_PROBE.json',report);_disk(out)
    return report


def fit(out):
    out=Path(out).resolve();started=time.monotonic()
    _guard(out,['SHARED_CALIBRATION.json','SHARED_CALIBRATION.npz','FRONTEND.json',
                'INITIAL_STATE.json','C2_PRIOR.json','C2_PRIOR.npz'])
    provenance,contracts=_provenance(out)
    proof=json.loads((out/'SHARED_PROBE.json').read_text())
    if (proof.get('status')!='MECHANISM_PROBE_PASSED_NOT_CALIBRATION_ACCEPTED'
            or proof.get('provenance')!=provenance
            or proof.get('output_sha256')!=sha(out/'SHARED_PROBE.npz')
            or proof.get('baseline_frontend_sha256')!=sha(out/'BASELINE_FRONTEND.json')
            or proof.get('geometry_sha256')!=sha(out/'GEOMETRY.json')):
        raise ValueError('exact shared probe/source/input/model/contract gate failed')
    episodes,baseline,geometry,poser=_setup(out)
    if (parameter_digest(baseline)!=parameter_digest(json.loads((out/'BASELINE_FRONTEND.json').read_text()))
            or parameter_digest(geometry)!=parameter_digest(json.loads((out/'GEOMETRY.json').read_text()))):
        raise ValueError('fresh frontend or geometry differs from the sealed probe')
    continuous = json.loads((out/'TASK_CONTRACT.json').read_text()).get('continuous_physical_fit',False)
    policy=CONTINUOUS_POLICY if continuous else POLICY
    evaluator=C2ReplayEvaluator(episodes,baseline,geometry,poser,contracts,
        {k:provenance[k] for k in ('input_sha256','source_sha256','model_sha256')},
        wall_limit_s=600.,progress=_progress,continuous_grid=continuous)
    # Immutable baseline tensors are required for the callback transport gate.
    # fit_shared_orientation separately replays delta=0 before scoring; this
    # extra baseline replay is explicit, never another calibration's cache.
    baseline_replay=evaluator(np.zeros(4))
    registered=RegisteredHeadingPrior(baseline['heading_factors'],baseline['frozen_heading_correction_rad'],baseline_calibration=baseline)
    protocol_actions=baseline_replay['actions']
    if continuous:
        full={k:v[::3] for k,v in baseline_replay['continuous'].items()}
        protocol_actions={name:full for name in contracts}
    protocol=build_arm_protocol(episodes,baseline,contracts,protocol_actions,
        registered.all_information,conditional_only=True)
    checkpoint,audit=fit_shared_orientation(baseline_replay['actions'],geometry,
        geometry['nominal_sensor_levers_m'],baseline['heading_factors'],
        baseline['frozen_heading_correction_rad'],evaluator,arm_protocol=protocol,continuous=continuous,
        baseline_calibration=baseline,**policy)
    accepted=checkpoint['replay'];levers=checkpoint['levers'].numpy();outputs={};actions={}
    if continuous:
        from .continuous import action_checkpoints
        full={k:v[::3] for k,v in accepted['continuous'].items()}
        # Export windows from the one fitted state, with no action restart or refit.
        continuous_sha=_save_arrays(out,'CONTINUOUS_CALIBRATION.npz',dict(time_s=full['time_s'],valid=full['valid'],
            rotation=checkpoint['rotations']['_continuous'].numpy(),parameters=checkpoint['parameters']['_continuous'].numpy()))
        checkpoint=action_checkpoints(checkpoint,full,accepted['actions'])
    for name,q in accepted['actions'].items():
        objective=PoseObjective(**q,geometry=geometry)
        rotation,terms=objective.evaluate(checkpoint['parameters'][name],checkpoint['levers'])
        saved=checkpoint['rotations'][name]
        error=float((saved[:,OBSERVED]-objective.observed).abs().max())
        if not torch.allclose(rotation,saved,atol=1e-6,rtol=0) or error>1e-6:
            raise ValueError('accepted checkpoint cannot reproduce its hard observed pose')
        actions[name]=dict(loss=float(terms['loss']),acceleration_rms_mps2=float(terms['acceleration_rms_mps2']),
            prior_position_rms_m=float(terms['prior_position_rms_m']),
            observed_rotation_max_element_error=error,valid_derivative_frames=int(valid_support(q['valid']).sum()))
        outputs.update({name+'/rotation':saved.numpy(),name+'/parameters':checkpoint['parameters'][name].numpy(),
            name+'/time_s':q['time_s'],name+'/valid':q['valid']})
    outputs.update(delta_rad=checkpoint['delta'].numpy(),sensor_levers_m=levers)
    if _provenance(out)[0]!=provenance:raise ValueError('fit inputs or source changed during execution')
    write(out/'FRONTEND.json',accepted['frontend'])
    write(out/'INITIAL_STATE.json',dict(global_rotation=accepted['initial'].tolist(),
        status='candidate-bound initialization; inferred missing proximal poses are a prior',
        replay_binding=accepted['binding']))
    prior_sha=_save_arrays(out,'C2_PRIOR.npz',accepted['continuous'])
    write(out/'C2_PRIOR.json',dict(**accepted['neural_audit'],
        source_sha256=provenance['source_sha256'],input_sha256=provenance['input_sha256'],
        model_sha256=provenance['model_sha256'],surface_sha256=provenance['surface_sha256'],
        smpl_sha256=provenance['model_sha256'][str(SMPL.relative_to(ROOT))],
        action_contract_sha256=provenance['action_contract_sha256'],output_sha256=prior_sha,
        H_data_opened=False,UWB_measurements_used=False,accepted=False))
    output_sha=_save_arrays(out,'SHARED_CALIBRATION.npz',outputs)
    initial_energy=audit['rounds'][0]['previous_energy'] if audit['rounds'] else checkpoint['energy']
    report=dict(status='SHARED_CANDIDATE_FROZEN_PENDING_SEPARATE_VALIDATION',accepted=False,
        shared_proposal_accepted=any(row['accepted'] for row in audit['rounds']),
        provenance=provenance,source_sha256=provenance['source_sha256'],
        probe_sha256=sha(out/'SHARED_PROBE.json'),geometry_sha256=sha(out/'GEOMETRY.json'),
        frontend_sha256=sha(out/'FRONTEND.json'),initial_state_sha256=sha(out/'INITIAL_STATE.json'),
        prior_metadata_sha256=sha(out/'C2_PRIOR.json'),prior_output_sha256=prior_sha,output_sha256=output_sha,
        continuous_output_sha256=continuous_sha if continuous else None,
        heading_increment_rad=checkpoint['delta'].tolist(),fitted_sensor_levers_m=levers.tolist(),
        lever_update_m=(levers-np.asarray(geometry['nominal_sensor_levers_m'])).tolist(),
        initial_energy=initial_energy,final_energy=checkpoint['energy'],
        objective_change=checkpoint['energy']-initial_energy,shared_fit=audit,actions=actions,
        accepted_replay_binding=accepted['binding'],baseline_replay_binding=baseline_replay['binding'],
        baseline_full_replay_count_before_fitter=1,fit_policy=policy,
        H_data_opened=False,ten_node_reference_used=False,UWB_measurements_used=False,
        legacy_validation_gates_claimed=False,calibration_accuracy_accepted=False,data_accuracy_accepted=False,
        process_peak_rss_kib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        resource_scope='this process; external launcher enforces descendant RSS and wall limits',
        wall_s=time.monotonic()-started)
    write(out/'SHARED_CALIBRATION.json',report);_disk(out)
    return report
