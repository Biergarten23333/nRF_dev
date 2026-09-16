"""All-C2 shared heading proposals, conditional on explicit pose/intent priors.

A frozen neural prior supplies an inner surrogate only. A fresh replay and
pose refinement must lower the same full energy before a checkpoint changes.
This module owns no file I/O, network inference, or holdout input.
"""
import copy
import time

import numpy as np
import torch

from .calibration_prior import RegisteredHeadingPrior
from .frontend import FIT
from .shared_orientation import transport_heading
from .solver import PoseObjective
from .temporal_torso import PoseVariables
from .body_feasibility import selection_key

LEVER_RADIUS_M = .04
LEVER_SIGMA_M = .025


def _tape(actions, *, prefix=False):
    if prefix:
        from .phase_contract import recorded_prefix
        names=list(recorded_prefix(actions))
    else:
        names=sorted(actions)
    if not prefix and (len(names)!=19 or {n[:2] for n in names}!=FIT):
        raise ValueError('shared calibration requires all 19 C2 actions exactly once')
    for name in names:
        q=actions[name]; count=len(q['time_s'])
        for key,shape in (('prior',(count,24,3,3)),('observed',(count,5,3,3)),
                          ('acceleration',(count,5,3)),('valid',(count,)),('time_s',(count,))):
            if np.shape(q[key])!=shape or not np.isfinite(q[key]).all():
                raise ValueError('invalid action tensor: '+name+'/'+key)
    return names


def _objectives(actions, geometry, *, continuous=False, prefix=False):
    if continuous:
        names = _tape(actions, prefix=prefix)
        q = actions[names[0]]
        if any(actions[name] is not q for name in names):
            raise ValueError('continuous objectives require one authoritative full tape')
        return {'_continuous':PoseObjective(**q,geometry=geometry)}
    return {n:PoseObjective(**{k:actions[n][k] for k in (
        'prior','observed','acceleration','valid','time_s')},geometry=geometry) for n in _tape(actions, prefix=prefix)}


def _deadline(deadline):
    if time.monotonic()>deadline:
        raise TimeoutError('shared orientation proposal budget exceeded')


def shared_regularization(prior, delta, lever, nominal, *, heading_model=None,
                          coefficients=None, constant_heading_energy=0., residual_blocks=None):
    """One owner for persistent-parameter energy and optional factor export.

    An inherited scalar constant is retained in energy but is not a parameter
    observation. Export currently rejects it rather than inventing its rows.
    """
    if residual_blocks is not None and constant_heading_energy != 0.:
        raise ValueError('constant heading energy must be accounted separately before export')
    if residual_blocks is None:
        energy=prior.energy(delta)
    else:
        energy=prior.energy(delta,residual_blocks=residual_blocks)
    residual=(lever-nominal)/LEVER_SIGMA_M
    from .residual_blocks import record_weighted
    record_weighted(residual_blocks, 'sensor_levers', residual, 1.)
    energy=energy+residual.square().sum()+constant_heading_energy
    if heading_model is not None and hasattr(heading_model,'regularization'):
        energy=energy+(heading_model.regularization(coefficients) if residual_blocks is None else
                       heading_model.regularization(coefficients,residual_blocks=residual_blocks))
    return energy


def _optimize(objectives, parameters, levers, delta, nominal, prior, *,
              iterations, deadline, shared, torso_knot_spacing_s=None, refresh_projection=False,
              projection_gap=False, pose_protocol=None, freeze_heading=False,
              heading_model=None, pose_learning_rate=.02, constant_heading_energy=0.,
              initial_heading_coefficients=None, expected_initial_energy=None, progress=None,
              fit_shank_mount=False):
    """Accumulate equal-action gradients without retaining 19 autograd graphs."""
    variables={n:PoseVariables(p,torso_knot_spacing_s,
        parameter_count=getattr(objectives[n],'parameter_count',9)) for n,p in parameters.items()}
    pose_trainable=[p for v in variables.values() for p in v.trainable]
    if fit_shank_mount and (not shared or not refresh_projection):
        raise ValueError('shared mounting requires refreshed candidate pose projection')
    mount_angles=torch.zeros(2,dtype=torch.float64,requires_grad=fit_shank_mount)
    if heading_model is not None and (len(objectives)!=1 or '_continuous' not in objectives):
        raise ValueError('temporal heading requires one continuous pose objective')
    increment=torch.zeros(4 if heading_model is None else heading_model.size,
                          dtype=torch.float64,requires_grad=shared and not freeze_heading)
    if initial_heading_coefficients is not None:
        initial=torch.as_tensor(initial_heading_coefficients,dtype=increment.dtype)
        if initial.shape!=increment.shape or not torch.isfinite(initial).all():
            raise ValueError('invalid resumed heading coefficients')
        with torch.no_grad():increment.copy_(initial)
    lever=levers.detach().clone().requires_grad_(shared)
    groups=[dict(params=pose_trainable,lr=pose_learning_rate)]
    if shared:
        groups += [dict(params=[lever],lr=.0005)]
        if fit_shank_mount:
            groups += [dict(params=[mount_angles],lr=.005)]
        if not freeze_heading:
            groups += [dict(params=[increment],lr=.02)]
    optimizer=torch.optim.Adam(groups)
    best=None; history=[]
    for step in range(iterations+1):
        _deadline(deadline); optimizer.zero_grad(); total=0.; rotations={}; violation=0.
        for name,objective in objectives.items():
            parameters=variables[name].value()
            transported_delta=increment if heading_model is None else heading_model.increments(increment)
            total_delta=delta+transported_delta
            observed,acceleration=transport_heading(objective.observed,objective.acceleration,transported_delta)
            if fit_shank_mount:
                from .mount_transport import transport_shank_observed
                base=getattr(objective,'base',objective)
                observed=transport_shank_observed(observed,base.geometry,mount_angles)
            rotation,terms=objective.evaluate(parameters,lever,observed=observed,acceleration=acceleration,
                                             refresh_projection=refresh_projection,projection_gap=projection_gap)
            protocol_names = (sorted({a for rows in prior.factor_actions.values() for a in rows.values()})
                              if name == '_continuous' else [name])
            if name == '_continuous' and pose_protocol is not None:
                protocol_names=sorted(set(protocol_names)|pose_protocol.actions)
            protocol = sum((prior.energy_for_action(a,total_delta,rotation) for a in protocol_names),
                           rotation.sum()*0.)
            if pose_protocol is not None:
                protocol=protocol+sum((pose_protocol.energy_for_action(a,parameters) for a in protocol_names),
                                      parameters.sum()*0.)
            loss=terms['loss']/len(objectives)+protocol
            if not torch.isfinite(loss): raise ValueError('nonfinite shared pose energy')
            total+=float(loss.detach()); rotations[name]=rotation.detach().clone()
            violation=max(violation,float(terms.get('body_violation',0.)))
            if step<iterations: loss.backward()
        # The pose backward above releases the temporal interpolation graph.
        # Rebuild this small branch for regularization; keep the same leaves,
        # without retaining the full trajectory graph across backward calls.
        regularizer_delta=delta+(increment if heading_model is None else heading_model.increments(increment))
        regularizer=shared_regularization(prior,regularizer_delta,lever,nominal,
            heading_model=heading_model,coefficients=increment,constant_heading_energy=constant_heading_energy)
        if not torch.isfinite(regularizer): raise ValueError('nonfinite shared regularizer')
        total+=float(regularizer.detach())
        if step==0 and expected_initial_energy is not None and not np.isclose(total,expected_initial_energy,atol=1e-8,rtol=1e-8):
            raise ValueError('resume checkpoint does not reproduce its original objective')
        if best is None or selection_key(total,violation)<selection_key(best['energy'],best['body_violation']):
            best=dict(energy=total,delta=total_delta.detach().clone(),levers=lever.detach().clone(),
                heading_coefficients=increment.detach().clone(),
                body_violation=violation,
                shank_mount_angles_rad=mount_angles.detach().clone(),
                mounting_fit_is_local_surrogate=fit_shank_mount,
                parameters={n:v.value().detach().clone() for n,v in variables.items()},rotations=rotations,step=step)
        if step%5==0 or step==iterations:
            row=dict(step=step,energy=total,body_violation_m=violation,selected_step=best['step'])
            if fit_shank_mount:
                row['shank_mount_angles_deg']=torch.rad2deg(mount_angles.detach()).tolist()
            history.append(row)
            if progress is not None:progress(row)
        if step==iterations:break
        if shared: regularizer.backward()
        active=pose_trainable+(([lever] if freeze_heading else [increment,lever]) if shared else [])
        if fit_shank_mount:active.append(mount_angles)
        if any(p.grad is None or not torch.isfinite(p.grad).all() for p in active):
            raise ValueError('nonfinite shared gradient')
        torch.nn.utils.clip_grad_norm_(active,10.);optimizer.step()
        with torch.no_grad():
            for name,v in variables.items():
                v.clamp_flexion(objectives[name].model.maximum_bend)
            if shared: lever.copy_(torch.maximum(nominal-LEVER_RADIUS_M,torch.minimum(lever,nominal+LEVER_RADIUS_M)))
    best['history']=history
    return best


def _replay(replay_evaluator, delta, baseline, deadline, *, prefix=False):
    _deadline(deadline)
    replay=copy.deepcopy(replay_evaluator(delta.detach().numpy().copy()))
    _deadline(deadline)
    if not isinstance(replay,dict) or not replay.get('binding'):
        raise ValueError('fresh replay and its provenance binding are required')
    actions=replay['actions']
    if _tape(actions, prefix=prefix)!=_tape(baseline, prefix=prefix): raise ValueError('replay changed the C2 action tape')
    for name,q in actions.items():
        b=baseline[name]
        if not np.array_equal(q['time_s'],b['time_s']) or not np.array_equal(q['valid'],b['valid']):
            raise ValueError('replay changed physical time/support')
        observed,acceleration=transport_heading(torch.as_tensor(b['observed'],dtype=torch.float64),
            torch.as_tensor(b['acceleration'],dtype=torch.float64),delta)
        if not np.allclose(q['observed'],observed.numpy(),atol=1e-6,rtol=0):
            raise ValueError('replay orientation does not match total shared yaw')
        if not np.allclose(q['acceleration'],acceleration.numpy(),atol=1e-6,rtol=0):
            raise ValueError('replay acceleration does not match total shared yaw')
    return replay


def fit_shared_orientation(actions, geometry, nominal_levers, factors, baseline_heading,
                           replay_evaluator, *, iterations=60, pose_iterations=30,
                           outer_rounds=2, wall_limit_s=120., arm_protocol=None, continuous=False,
                           torso_knot_spacing_s=None, prefix=False, matched_control=False,
                           pose_protocol=None, baseline_calibration=None):
    """Return a complete accepted checkpoint and an audit, never an adopted fit.

    replay_evaluator(total_delta) returns actions plus a fresh replay binding.
    All proposals are measured from immutable baseline actions/calibration.
    The initial checkpoint is also replayed and pose-refined by the same owner.
    """
    if min(iterations,pose_iterations,outer_rounds)<0 or wall_limit_s<=0:
        raise ValueError('nonnegative iteration counts and positive budget required')
    started=time.monotonic();deadline=started+wall_limit_s
    baseline=copy.deepcopy(actions);names=_tape(baseline, prefix=prefix)
    if pose_protocol is not None and not pose_protocol.actions.issubset(names):
        raise ValueError('pose protocol cannot access absent calibration actions')
    nominal=torch.as_tensor(nominal_levers,dtype=torch.float64)
    if nominal.shape!=(5,3) or not torch.isfinite(nominal).all():
        raise ValueError('five finite nominal lever arms required')
    prior=RegisteredHeadingPrior(factors,baseline_heading,baseline_calibration=baseline_calibration)
    if any(set(rows.values())-set(baseline) for rows in prior.factor_actions.values()):
        raise ValueError('heading factor action is absent from the calibration tape')
    replay=_replay(replay_evaluator,torch.zeros(4,dtype=torch.float64),baseline,deadline,prefix=prefix)
    def objective_actions(value):
        if not continuous:
            return value['actions']
        q = {k:v[::3] for k,v in value['continuous'].items()}
        return {name:q for name in baseline}
    full_baseline = replay.get('continuous')
    prior.bind_arm_protocol(arm_protocol,objective_actions(replay))
    objectives=_objectives(objective_actions(replay),geometry,continuous=continuous,prefix=prefix)
    accepted=_optimize(objectives,{n:o.initial for n,o in objectives.items()},nominal,
        torch.zeros(4,dtype=torch.float64),nominal,prior,iterations=pose_iterations,deadline=deadline,shared=False,
        torso_knot_spacing_s=torso_knot_spacing_s,pose_protocol=pose_protocol)
    accepted['replay']=replay; rounds=[];stop_reason='outer_round_budget'
    for index in range(outer_rounds):
        control=None
        if matched_control:
            # Identical starting checkpoint, optimizer restarts and pose/lever
            # budgets; only the heading parameter is frozen in this branch.
            fixed=_optimize(objectives,accepted['parameters'],accepted['levers'],accepted['delta'],nominal,
                prior,iterations=iterations,deadline=deadline,shared=True,
                torso_knot_spacing_s=torso_knot_spacing_s,freeze_heading=True,pose_protocol=pose_protocol)
            fixed_warm={n:o.parameters_from_rotation(fixed['rotations'][n]) for n,o in objectives.items()}
            control=_optimize(objectives,fixed_warm,fixed['levers'],fixed['delta'],nominal,
                prior,iterations=pose_iterations,deadline=deadline,shared=False,
                torso_knot_spacing_s=torso_knot_spacing_s,pose_protocol=pose_protocol)
            control['replay']=accepted['replay']
        proposal=_optimize(objectives,accepted['parameters'],accepted['levers'],accepted['delta'],nominal,
            prior,iterations=iterations,deadline=deadline,shared=True,torso_knot_spacing_s=torso_knot_spacing_s,
            pose_protocol=pose_protocol)
        _deadline(deadline)
        candidate_replay=_replay(replay_evaluator,proposal['delta'],baseline,deadline,prefix=prefix)
        if continuous:
            from .continuous import check_continuous_transport
            check_continuous_transport(full_baseline,candidate_replay['continuous'],proposal['delta'])
        fresh=_objectives(objective_actions(candidate_replay),geometry,continuous=continuous,prefix=prefix)
        warm={n:o.parameters_from_rotation(proposal['rotations'][n]) for n,o in fresh.items()}
        candidate=_optimize(fresh,warm,proposal['levers'],proposal['delta'],nominal,prior,
            iterations=pose_iterations,deadline=deadline,shared=False,torso_knot_spacing_s=torso_knot_spacing_s,
            pose_protocol=pose_protocol)
        tolerance=1e-10+1e-10*abs(accepted['energy'])
        threshold=accepted['energy'] if control is None else min(accepted['energy'],control['energy'])
        if geometry.get('body_feasibility'):
            threshold_key=selection_key(accepted['energy'],accepted['body_violation'])
            if control is not None:threshold_key=min(threshold_key,selection_key(control['energy'],control['body_violation']))
            improved=selection_key(candidate['energy'],candidate['body_violation'])<threshold_key
        else:
            improved=candidate['energy']<threshold-tolerance
        rounds.append(dict(round=index,accepted=improved,previous_energy=accepted['energy'],
            surrogate_energy=proposal['energy'],replayed_energy=candidate['energy'],
            proposed_delta_rad=proposal['delta'].tolist(),
            proposed_body_violation_m=candidate['body_violation'],
            fixed_heading_control_energy=None if control is None else control['energy'],
            matched_optimizer_steps=iterations+pose_iterations if control is not None else None))
        if improved:
            candidate['replay']=candidate_replay;accepted=candidate;objectives=fresh
        else:
            if control is not None and selection_key(control['energy'],control['body_violation'])<selection_key(accepted['energy'],accepted['body_violation']):
                accepted=control
            stop_reason='replayed_energy_did_not_beat_control' if control is not None else 'replayed_energy_did_not_improve'
            break
    _deadline(deadline)
    return copy.deepcopy(accepted),dict(rounds=rounds,stop_reason=stop_reason,wall_s=time.monotonic()-started,
        pose_protocol=None if pose_protocol is None else pose_protocol.audit(),
        body_feasibility={name:o.body.audit(accepted['rotations'][name],o.valid) for name,o in objectives.items()},
        action_count=len(names),action_weight=None if continuous else 1/len(names),
        matched_fixed_heading_control=matched_control,
        arrived_prefix=prefix,prefix_last_action=names[-1],complete_recorded_C2=len(names)==19,
        physical_time_support='complete continuous C2, including inter-action intervals' if continuous else 'action windows',
        continuous=continuous,torso_knot_spacing_s=torso_knot_spacing_s,
        torso_parameterization='free per frame' if torso_knot_spacing_s is None else 'cubic correction to moving neural prior; experimental regularizer',
        registered_information=prior.information,
        conditional_arm_information=prior.conditional_information,
        conditional_arm_rows=[] if arm_protocol is None else arm_protocol.audit(),
        conditional_reference='latent thorax; broad protocol prior, not measured orientation',
        lever_prior_count=1,lever_sigma_m=LEVER_SIGMA_M,lever_radius_m=LEVER_RADIUS_M,
        inference='prior-conditioned regularized energy; not independent sensor-only MAP',
        data_only_observability_claimed=False,calibration_adopted=False,
        recurrent_replay_required_for_acceptance=True)
