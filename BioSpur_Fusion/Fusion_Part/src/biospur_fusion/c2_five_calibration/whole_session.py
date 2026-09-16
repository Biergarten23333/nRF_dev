"""Full C2 continuation: all recorded actions remain in every solve.

Stages change free variables, never the calibration dataset. Previous stages
are numerical starts, not additional observations. The network is a finite
prior; raw-time replay is mandatory before accepting shared calibration.
"""
import time

import numpy as np
import torch

from .phase_contract import recorded_prefix
from .shared_fit import _optimize
from .solver import PoseObjective
from .soft_observation import SoftObservationObjective
from .temporal_parameters import TemporalRegisteredHeadingPrior
from .session_heading import SessionHeadingParameters
from .arm_protocol import build_arm_protocol
from .protocol_pose import build_bend_protocol
from .body_feasibility import selection_key


def whole_session_support(data, contracts):
    names = recorded_prefix(contracts, require_complete=True)
    t = np.asarray(data['time_s'])
    valid = np.asarray(data['valid'])
    if (t.ndim != 1 or len(t) < 2 or not np.isfinite(t).all()
            or not np.allclose(np.diff(t), .05, atol=1e-6, rtol=0)
            or valid.shape != t.shape or valid.dtype != bool):
        raise ValueError('one continuous original 20 Hz C2 tape required')
    coverage = []
    last = -np.inf
    for name in names:
        lo, hi = contracts[name]['lo'], contracts[name]['hi']
        if not np.isfinite([lo, hi]).all() or hi <= lo or lo < last:
            raise ValueError('ordered nonoverlapping C2 action contracts required')
        if t[0] > lo+.05 or t[-1] < hi-.05:
            raise ValueError('whole C2 tape does not cover '+name)
        mask = (t >= lo) & (t < hi)
        if np.count_nonzero(valid & mask) < 20:
            raise ValueError('insufficient valid C2 support: '+name)
        coverage.append(dict(action=name, frames=int(mask.sum()), valid_frames=int((valid & mask).sum())))
        last = hi
    return coverage


def fit_whole_session(data, geometry, baseline, contracts, episodes, replay,
                      *, iterations=20, wall_limit_s=1800., progress=None, resume=None, final_iterations=None, iteration_progress=None, spatial_arm_axes=False):
    """Calibrate on full C2; callback returns fresh data plus raw transport proof.

    No H/reference argument exists. Final acceptance is internal C2 energy,
    never a claim about motion tracking accuracy. Both proposal and fixed-yaw
    control receive identical shared and final pose iteration budgets.
    """
    if iterations < 1 or wall_limit_s <= 0:
        raise ValueError('positive whole-session budget required')
    coverage = whole_session_support(data, contracts)
    names = [row['action'] for row in coverage]
    if set(episodes) != set(names):
        raise ValueError('exact recorded C2 episodes only; no H or extra input')
    model = SessionHeadingParameters(baseline, data['time_s'], contracts)
    prior = TemporalRegisteredHeadingPrior(baseline, data['time_s'])
    actions = {name:data for name in names}
    tape = build_arm_protocol(episodes, baseline, contracts, actions,
                              prior.all_information, conditional_only=True, spatial_axes=spatial_arm_axes)
    prior.bind_arm_protocol(tape, actions)
    protocol = build_bend_protocol(contracts, actions)
    nominal = torch.as_tensor(geometry['nominal_sensor_levers_m'], dtype=torch.float64)
    zero = torch.zeros(len(data['time_s']), 4, dtype=torch.float64)
    deadline = time.monotonic()+wall_limit_s
    stages = []

    def run(name, objective, parameters, levers, delta, *, shared=False, freeze=False, temporal=False, heading_energy=0., checkpoint=None, count=None):
        result = _optimize({'_continuous':objective}, {'_continuous':parameters},
            levers, delta, nominal, prior, iterations=iterations if count is None else count, deadline=deadline,
            shared=shared, freeze_heading=freeze, heading_model=model if temporal else None,
            pose_protocol=protocol, pose_learning_rate=.005,constant_heading_energy=heading_energy,
            initial_heading_coefficients=None if checkpoint is None else checkpoint['coefficients'],
            expected_initial_energy=None if checkpoint is None else checkpoint['energy'],
            progress=None if iteration_progress is None else lambda row:iteration_progress(dict(stage=name,**row)))
        audit = dict(stage=name, full_C2=True, action_count=len(names),
            time_frames=len(data['time_s']), energy=result['energy'],
            body_violation_m=result['body_violation'], selected_step=result['step'],
            history=result['history'], previous_result_is_observation=False,
            iteration_budget=iterations if count is None else count,
            best_at_budget_end=result['step']==(iterations if count is None else count),
            convergence_claimed=False)
        stages.append(audit)
        if progress is not None:
            progress(audit, result)
        return result

    base = PoseObjective(**data, geometry=geometry)
    objective = SoftObservationObjective(base)
    if resume is None:
        locked = SoftObservationObjective(base, freeze_observation=True)
        seed = run('full_C2_pose_initialization', locked, locked.initial, nominal, zero)
        warm = objective.parameters_from_rotation(seed['rotations']['_continuous'])
        stabilized = run('full_C2_joint_pose', objective, warm, nominal, zero)
        start = stabilized['parameters']['_continuous']
        control_checkpoint=proposal_checkpoint=None
        control_start=proposal_start=start
        control_levers=proposal_levers=nominal
    else:
        if set(resume)!={'control','proposal'}:raise ValueError('matched C2 checkpoints required')
        control_checkpoint,proposal_checkpoint=resume['control'],resume['proposal']
        control_start=torch.as_tensor(control_checkpoint['parameters'],dtype=torch.float64)
        proposal_start=torch.as_tensor(proposal_checkpoint['parameters'],dtype=torch.float64)
        control_levers=torch.as_tensor(control_checkpoint['levers'],dtype=torch.float64)
        proposal_levers=torch.as_tensor(proposal_checkpoint['levers'],dtype=torch.float64)
    control = run('full_C2_fixed_heading_control', objective, control_start, control_levers, zero,
                  shared=True, freeze=True, temporal=True,checkpoint=control_checkpoint)
    proposal = run('full_C2_shared_calibration', objective, proposal_start, proposal_levers, zero,
                   shared=True, temporal=True,checkpoint=proposal_checkpoint)
    frontend = model.frontend(proposal['heading_coefficients'].numpy())
    refreshed = replay(frontend, proposal['heading_coefficients'].numpy())
    if (not refreshed.get('binding') or
            not refreshed.get('transport', {}).get('raw_time_transport_verified')):
        raise ValueError('fresh raw-time transport and neural replay binding required')
    fresh_data = refreshed['data']
    whole_session_support(fresh_data, contracts)
    for key in ('time_s', 'valid'):
        if not np.array_equal(fresh_data[key], data[key]):
            raise ValueError('fresh replay changed whole-session '+key)
    # Same loss and budgets, re-evaluated on their respective fresh inputs.
    control = run('full_C2_control_final', objective,
        objective.parameters_from_rotation(control['rotations']['_continuous']),
        control['levers'], control['delta'],count=final_iterations)
    fresh = SoftObservationObjective(PoseObjective(**fresh_data, geometry=geometry))
    candidate = run('full_C2_proposal_final', fresh,
        fresh.parameters_from_rotation(proposal['rotations']['_continuous']),
        proposal['levers'], proposal['delta'],
        heading_energy=float(model.regularization(proposal['heading_coefficients'])),count=final_iterations)
    accepted = (candidate['body_violation'] <= 1e-9 and
        selection_key(candidate['energy'], candidate['body_violation']) <
        selection_key(control['energy'], control['body_violation']))
    return dict(candidate=candidate, control=control, frontend=frontend), dict(
        completed=True, calibration_energy_improved=accepted, tracking_accepted=False,
        resumed_numerical_checkpoint=resume is not None,optimizer_moments_resumed=False,
        whole_C2_coverage=coverage, stages=stages, H_used=False, reference_used=False,
        stages_split_data=False, all_parameters_finalized_after_full_session=True,
        replay_binding=refreshed['binding'], raw_transport=refreshed['transport'],
        bend_protocol=protocol.audit(), arm_protocol=tape.audit(),
        acceleration_frame='world axes; relative to moving pelvis sensor, including rotating lever',
        acceptance_scope='C2 internal objective only; independent animation comparison remains required')
