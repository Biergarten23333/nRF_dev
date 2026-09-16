"""Stage boundaries and evidence for five-node C2 physical calibration."""
import json
from pathlib import Path
import time
import numpy as np
import torch

from biospur_fusion.c2_sparse_nodes.inputs import NODES, ROOT, sha
from biospur_fusion.c2_imucoco.workflow import INPUT_RUN, SURFACE, load_input, write
from biospur_fusion.c2_imucoco.backend import load_pose
from biospur_fusion.c2_imucoco.upstream import verify_assets
from .frontend import FIT, fit_frontend
from .replay import replay_prior, parameter_digest, slice_actions
from .geometry import subject_geometry
from .placement import C2_VERTICES, PLACEMENT_EVIDENCE
from .solver import solve_pose, lever_system, fit_levers

SMPL = ROOT/'third_party/smpl/SMPL_MALE.pkl'


def fingerprint():
    files = [ROOT/'tools/run_c2_five_calibration.py']
    files.extend(ROOT/'src/biospur_fusion/c2_coupled_progressive'/name for name in
                 ('estimator.py','pose_reset_avatar.py','math_utils.py','contracts.py'))
    for package in ('c2_five_calibration','c2_imucoco','c2_sparse_nodes'):
        files.extend(sorted((ROOT/'src/biospur_fusion'/package).glob('*.py')))
    # Actual imported mature hinge dependency closure. Other articulated
    # workers may add unrelated temporal modules; they are not this solver.
    files.extend(ROOT/'src/biospur_fusion/c2_articulated_biomechanics'/name
                 for name in ('__init__.py','model.py','orientation_ik.py'))
    return {str(p.relative_to(ROOT)):sha(p) for p in files}


def guard(out, label):
    if (out/(label+'.json')).exists() or (out/(label+'.npz')).exists():
        raise ValueError('stage already exists; preserve evidence')


def prior(out, *, probe):
    label='PROBE' if probe else 'C2_PRIOR'
    guard(out,label)
    verify_assets()
    episodes=load_input(INPUT_RUN/'CALIBRATION_CONTINUOUS_INPUT.npz')
    c=fit_frontend(episodes,json.loads(SURFACE.read_text()))
    poser,body=load_pose(SMPL,out)
    g=subject_geometry(body,json.loads(SURFACE.read_text()),sensor_vertices=C2_VERTICES)
    g['placement_evidence']=PLACEMENT_EVIDENCE
    if not probe:
        proof=json.loads((out/'PROBE.json').read_text())
        if proof['source_sha256']!=fingerprint() or not proof['finite_physical_solve']:
            raise ValueError('probe/source gate failed')
        if (parameter_digest(json.loads((out/'FRONTEND.json').read_text())) != parameter_digest(c)
                or parameter_digest(json.loads((out/'GEOMETRY.json').read_text())) != parameter_digest(g)):
            raise ValueError('probe calibration differs from continuous prior calibration')
    result,audit,initial=replay_prior(episodes,c,g,poser,probe=probe,
        wall_limit_s=120 if probe else 600,progress=lambda p: print(json.dumps(p),flush=True))
    report=dict(**audit,source_sha256=fingerprint(),input_sha256=sha(INPUT_RUN/'CALIBRATION_CONTINUOUS_INPUT.npz'),
        surface_sha256=sha(SURFACE),smpl_sha256=sha(SMPL),H_data_opened=False,UWB_measurements_used=False)
    report['action_contract_sha256']=parameter_digest(json.loads(
        (INPUT_RUN/'CALIBRATION_INPUT_AUDIT.json').read_text())['contracts'])
    if probe:
        q={k:v[::3] for k,v in result.items()}
        fixed,physical=solve_pose(q['prior'],q['observed'],q['acceleration'],q['valid'],q['time_s'],g,
            g['nominal_sensor_levers_m'],iterations=30)
        result['probe_physical_rotation']=fixed
        report.update(finite_physical_solve=bool(np.isfinite(fixed).all()),physical=physical)
        write(out/'FRONTEND.json',c)
        write(out/'GEOMETRY.json',g)
        write(out/'INITIAL_STATE.json',dict(global_rotation=initial.tolist(),
            status='calibrated retained directions; hanging upper-arm assumption initializes missing pose'))
    np.savez_compressed(out/(label+'.npz'),**result)
    report['output_sha256']=sha(out/(label+'.npz'))
    write(out/(label+'.json'),report)


def action_data(out):
    meta=json.loads((out/'C2_PRIOR.json').read_text())
    if meta['output_sha256']!=sha(out/'C2_PRIOR.npz'):
        raise ValueError('prior output changed')
    binding=meta.get('replay_binding')
    if binding is None or not binding['continuous_prefix']:
        raise ValueError('prior lacks a complete candidate-bound continuous replay')
    if (binding['frontend_parameter_sha256'] != parameter_digest(json.loads((out/'FRONTEND.json').read_text()))
            or binding['geometry_parameter_sha256'] != parameter_digest(json.loads((out/'GEOMETRY.json').read_text()))):
        raise ValueError('prior belongs to a different calibration or geometry')
    with np.load(out/'C2_PRIOR.npz') as f:
        data={k:f[k] for k in f.files}
    contracts=json.loads((INPUT_RUN/'CALIBRATION_INPUT_AUDIT.json').read_text())['contracts']
    if meta.get('action_contract_sha256') != parameter_digest(contracts):
        raise ValueError('prior action-window contract changed')
    return slice_actions(data,contracts)


def physical(out, *, validate=False):
    """Fit shared parameters, then replay every C2 action with frozen offsets."""
    label='C2_VALIDATION' if validate else 'PHYSICAL_CALIBRATION'
    guard(out,label)
    started=time.monotonic()
    g=json.loads((out/'GEOMETRY.json').read_text())
    actions=action_data(out)
    contract=json.loads((out/'TASK_CONTRACT.json').read_text())
    if set(contract['fit_actions']) != FIT or contract.get('validation_actions'):
        raise ValueError('calibration must use all recorded C2 actions; no internal holdout split')
    if validate:
        candidate=json.loads((out/'PHYSICAL_CALIBRATION.json').read_text())
        transfer=json.loads((out/'FIXED_POSE_TRANSFER_AUDIT.json').read_text())
        levers=candidate['fitted_sensor_levers_m']
    else:
        proof=json.loads((out/'REPRESENTATIVE.json').read_text())
        if len(proof)<3 or any(r['observed_rotation_max_element_error']>1e-5 for r in proof.values()):
            raise ValueError('representative joint-mechanism gate not satisfied')
        levers=g['nominal_sensor_levers_m']
    fit_policy=contract.get('fit_convergence',dict(max_rounds=6,offset_tolerance_m=.001))
    if not 2<=fit_policy['max_rounds']<=6 or not 0<fit_policy['offset_tolerance_m']<=.001:
        raise ValueError('bounded offset convergence policy required')
    converged=False
    rounds=[]
    for iteration in range(1 if validate else fit_policy['max_rounds']):
        outputs,reports,systems={},{},[]
        for name,q in actions.items():
            if not validate and name[:2] not in FIT:continue
            if time.monotonic()-started>(600 if validate else 900):
                raise TimeoutError('C2 stage budget reached')
            r,report=solve_pose(q['prior'],q['observed'],q['acceleration'],q['valid'],q['time_s'],g,levers,iterations=150)
            reports[name]=report
            outputs[name+'/rotation']=r;outputs[name+'/time_s']=q['time_s'];outputs[name+'/valid']=q['valid']
            if not validate:systems.append(lever_system(r,q['acceleration'],q['valid'],g))
            print(json.dumps(dict(iteration=iteration,action=name,**report)),flush=True)
        if not validate:
            previous=np.asarray(levers)
            levers,fit=fit_levers(systems,g['nominal_sensor_levers_m'])
            update=float(np.max(np.abs(levers-previous)))
            converged=update<=fit_policy['offset_tolerance_m'] and not any(fit['active_bounds'])
            rounds.append(dict(iteration=iteration,lever_fit=fit,actions=reports,
                max_offset_update_m=update,offset_update_converged=converged))
            write(out/f'FIT_ITERATION_{iteration}.json',rounds[-1])
            if converged or any(fit['active_bounds']):break
    report=dict(actions=reports,source_sha256=fingerprint(),wall_s=time.monotonic()-started,
        H_data_opened=False,ten_node_reference_used=False,geometry_sha256=sha(out/'GEOMETRY.json'),
        action_angle_targets_used=False,calibration_protocol='ALL_RECORDED_C2',
        C2_check_is_in_sample=True)
    if not validate:
        report.update(fitted_sensor_levers_m=levers.tolist(),lever_fit=fit,iterations=rounds,
            offset_update_converged=converged,fit_convergence_policy=fit_policy,
            fit_stop_reason='converged' if converged else 'offset_bound' if any(fit['active_bounds']) else 'round_budget',
            status='PARAMETERS_FROZEN_PENDING_VALIDATION',accepted=False,
            fit_trajectories='intermediate parameter fit; use final frozen-parameter replay for evaluation')
    else:
        fit=candidate['lever_fit']
        gates=dict(coverage={k[:2] for k in reports}==FIT and {k[:2] for k in candidate['actions']}==FIT,
            offset_update_convergence=candidate.get('offset_update_converged',False),
            offset_design_rank=fit['design_rank']==15,offset_bounds=not any(fit['active_bounds']),
            no_labelled_angles=all(not x['action_labels_consumed'] for x in reports.values()),
            calibration_acceleration=max(x['history'][-1]['acceleration_rms_mps2'] for x in reports.values())<=1.5,
            fixed_pose_offset_consistency=all(x['fixed_pose_offset_rms_ratio']<=1.1 for x in transfer['actions'].values())
                and {k[:2] for k in transfer['actions']}==FIT)
        report.update(gates=gates,status='C2_INERTIAL_GATES_PASSED_PENDING_POSE' if all(gates.values()) else 'C2_FAILED',
            accepted=False,all_actions_replayed_with_frozen_parameters=True,
            calibration_sha256=sha(out/'PHYSICAL_CALIBRATION.json'),H_replay_authorized_by_gate=False)
    np.savez_compressed(out/(label+'.npz'),**outputs)
    report['output_sha256']=sha(out/(label+'.npz'))
    write(out/(label+'.json'),report)
