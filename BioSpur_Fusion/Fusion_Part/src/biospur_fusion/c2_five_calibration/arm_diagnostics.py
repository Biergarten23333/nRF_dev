"""Bounded C2-only counterfactuals for the arm protocol reference frame.

Every fixed heading gets a fresh continuous neural replay. Pose solves use
identical cold starts and iteration budgets; no H or ten-node score selects
a heading. Profiles remain conditional, finite-budget diagnostics.
"""
import json
import time
from pathlib import Path

import numpy as np
import torch

from biospur_fusion.c2_sparse_nodes.inputs import ROOT,NODES,sha
from biospur_fusion.c2_imucoco.workflow import INPUT_RUN,SURFACE,load_input,write
from biospur_fusion.c2_imucoco.backend import load_pose
from biospur_fusion.c2_imucoco.upstream import verify_assets
from .frontend import fit_frontend,prepare,FIT
from .geometry import subject_geometry
from .placement import C2_VERTICES
from .workflow import SMPL,fingerprint
from .replay import C2ReplayEvaluator
from .shared_fit import RegisteredHeadingPrior
from .solver import PoseObjective
from .anatomy import FLEXION
from .arm_protocol import build_arm_protocol

POLICY=dict(right_increment_deg=[-30.,0.,30.],iterations=60,action_weight=1/19,
            reference_modes=['pelvis','thorax'],wall_limit_s=1100.,lever_fitting=False)


def _load(out):
    contract=json.loads((out/'TASK_CONTRACT.json').read_text())
    if contract.get('policy')!=POLICY or set(contract.get('fit_actions',[]))!=FIT:
        raise ValueError('arm diagnostic policy must be declared before execution')
    cache=INPUT_RUN/'CALIBRATION_CONTINUOUS_INPUT.npz'
    audit=INPUT_RUN/'CALIBRATION_INPUT_AUDIT.json'
    episodes=load_input(cache)
    c=fit_frontend(episodes,json.loads(SURFACE.read_text()))
    contracts=json.loads(audit.read_text())['contracts']
    provenance=dict(input_sha256=sha(cache),source_sha256=fingerprint(),
                    surface_sha256=sha(SURFACE),contract_sha256=sha(out/'TASK_CONTRACT.json'))
    return episodes,c,contracts,provenance


def probe(out):
    started=time.monotonic();episodes,c,contracts,provenance=_load(out)
    info=RegisteredHeadingPrior(c['heading_factors'],c['frozen_heading_correction_rad'])
    # No learned poses are needed to audit raw time support and factor parity.
    actions={}
    for name,ep in episodes.items():
        if name=='_continuous':continue
        from biospur_fusion.c2_imucoco.preprocessing import prepare_stream
        data=prepare_stream(ep,c)
        actions[name]=dict(time_s=data['time_s'][::3],valid=data['input_valid'][::3])
    tape=build_arm_protocol(episodes,c,contracts,actions,info.all_information)
    conditional=build_arm_protocol(episodes,c,contracts,actions,info.all_information,conditional_only=True)
    info.bind_arm_protocol(conditional,actions)
    for row in tape.rows:
        if not np.isfinite(row.direction).all() or not len(row.index):
            raise ValueError('empty or nonfinite real arm tape')
        if row.information.sum()>row.audit['original_information']+1e-12:
            raise ValueError('arm tape created information')
    write(out/'ARM_PROBE.json',dict(status='RAW_TAPE_SUPPORTED_NOT_POSE_ACCEPTED',
        provenance=provenance,rows=tape.audit(),conditional_rows=conditional.audit(),
        scalar_heading_information=info.information,conditional_information=info.conditional_information,
        wall_s=time.monotonic()-started,
        H_opened=False,ten_node_reference_opened=False))
    write(out/'BASELINE_FRONTEND.json',c)


def refine(objective, levers, tape, name, delta, reference, *, iterations, deadline):
    p=objective.initial.detach().clone().requires_grad_()
    optimizer=torch.optim.Adam([p],lr=.02);best=None;history=[]
    for step in range(iterations+1):
        if time.monotonic()>deadline:raise TimeoutError('arm profile stage budget')
        optimizer.zero_grad()
        r,terms=objective.evaluate(p,levers)
        protocol=tape.energy_for_action(name,delta,r,reference=reference)
        loss=terms['loss']/19+protocol
        if not torch.isfinite(loss):raise ValueError('nonfinite arm profile')
        value=float(loss.detach())
        if best is None or value<best['energy']:
            best=dict(energy=value,parameters=p.detach().clone(),rotation=r.detach().clone(),step=step)
        if step in (0,iterations):history.append(dict(step=step,energy=value))
        if step==iterations:break
        loss.backward()
        if not torch.isfinite(p.grad).all():raise ValueError('nonfinite arm pose gradient')
        torch.nn.utils.clip_grad_norm_([p],10.);optimizer.step()
        with torch.no_grad():
            p[:,FLEXION].copy_(torch.minimum(p[:,FLEXION].clamp_min(0.),objective.model.maximum_bend))
    r,terms=objective.evaluate(best['parameters'],levers)
    report=dict(energy=best['energy'],selected_step=best['step'],history=history,
        physical_loss=float(terms['loss']),acceleration_rms_mps2=float(terms['acceleration_rms_mps2']),
        prior_position_rms_m=float(terms['prior_position_rms_m']),
        protocol_energy=float(tape.energy_for_action(name,delta,r,reference=reference)),
        elbow_median_deg=torch.rad2deg(best['parameters'][:,3:5]).median(0).values.tolist(),
        observed_rotation_max_error=float((r[:,[0,18,19,4,5]]-objective.observed).abs().max()),
        converged_claimed=False)
    return best,report


def profile(out):
    started=time.monotonic();deadline=started+POLICY['wall_limit_s']
    episodes,c,contracts,provenance=_load(out)
    proof=json.loads((out/'ARM_PROBE.json').read_text())
    if proof.get('provenance')!=provenance:raise ValueError('arm probe source/input changed')
    assets=verify_assets();poser,body=load_pose(SMPL,out)
    geometry=subject_geometry(body,json.loads(SURFACE.read_text()),sensor_vertices=C2_VERTICES)
    write(out/'GEOMETRY.json',geometry)
    model_binding=dict(release_manifest=assets,smpl_sha256=sha(SMPL))
    evaluator=C2ReplayEvaluator(episodes,c,geometry,poser,contracts,
        {**{k:provenance[k] for k in ('input_sha256','source_sha256')},'model_sha256':model_binding},
        progress=lambda v:print(json.dumps(v),flush=True),wall_limit_s=300.)
    registered=RegisteredHeadingPrior(c['heading_factors'],c['frozen_heading_correction_rad'])
    levers=torch.tensor(geometry['nominal_sensor_levers_m'],dtype=torch.float64)
    results=[];tape=None
    for degrees in POLICY['right_increment_deg']:
        delta=torch.tensor([0.,np.deg2rad(degrees),0.,0.],dtype=torch.float64)
        replay=evaluator(delta.numpy());actions=replay['actions']
        if tape is None:tape=build_arm_protocol(episodes,c,contracts,actions,registered.all_information)
        arrays={};modes={}
        for reference in POLICY['reference_modes']:
            records={}
            for name,q in actions.items():
                objective=PoseObjective(**q,geometry=geometry)
                best,record=refine(objective,levers,tape,name,delta,reference,
                    iterations=POLICY['iterations'],deadline=deadline)
                records[name]=record
                arrays[f'{reference}/{name}/rotation']=best['rotation'].numpy()
                arrays[f'{reference}/{name}/parameters']=best['parameters'].numpy()
                arrays[f'{reference}/{name}/time_s']=q['time_s']
                print(json.dumps(dict(right_increment_deg=degrees,reference=reference,action=name,**record)),flush=True)
            modes[reference]=dict(actions=records,total_energy=sum(v['energy'] for v in records.values()))
        label=f'PROFILE_{degrees:+.0f}'
        np.savez_compressed(out/(label+'.npz'),**arrays)
        result=dict(right_increment_deg=degrees,modes=modes,replay_binding=replay['binding'],
            original_scalar_heading_energy=float(registered.energy(delta)),
            output_sha256=sha(out/(label+'.npz')))
        write(out/(label+'.json'),result);results.append(result)
    if fingerprint()!=provenance['source_sha256']:raise ValueError('source changed during arm profile')
    write(out/'ARM_PROFILE.json',dict(status='FINITE_BUDGET_CONDITIONAL_PROFILE_NOT_ADOPTED',
        provenance=provenance,model_binding=model_binding,policy=POLICY,profiles=results,
        tape=tape.audit(),wall_s=time.monotonic()-started,all_recorded_C2_used=True,
        H_opened=False,ten_node_reference_opened=False,calibration_adopted=False,
        limitations=['three-point conditional profile, not a global minimum or observability proof',
            'thorax is inferred and may absorb protocol conflict; lower cost is not pose accuracy',
            'fixed original levers and unchanged other headings isolate this hypothesis']))
