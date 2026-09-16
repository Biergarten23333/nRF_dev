"""Bounded coarse-to-fine optimization of one physical pose objective."""
import time

import torch

from .anatomy import FLEXION
from .body_feasibility import selection_key


def optimize_pose(objective, levers, guess, *, iterations, wall_limit_s, temporal_polish=False):
    """Warm-start with slow motion, then solve both bandwidths together.

    The short-window curvature makes a cold Adam start converge poorly at the
    existing fixed budget. Continuation preserves the slow solution before
    adding the faster residual. Each stage retains its own best feasible
    iterate; scores from different objectives are never compared.
    """
    if iterations < 0:
        raise ValueError('iteration budget must be nonnegative')
    started = time.monotonic()
    phases = [('slow', iterations, .02), ('combined', iterations//2, .005)] if iterations else [('combined', 0, .005)]
    history = []
    selected = guess.detach().clone()
    executed = 0
    for name, budget, rate in phases:
        parameters = selected.clone().requires_grad_()
        optimizer = torch.optim.Adam([parameters], lr=rate)
        best_key = None
        best_step = 0
        for step in range(budget+1):
            if time.monotonic()-started > wall_limit_s:
                raise TimeoutError('physical joint stage exceeded budget')
            _, terms = objective.evaluate(parameters, levers)
            loss = terms['loss']
            if name == 'slow':
                loss = loss-terms['acceleration_loss']+terms['acceleration_loss_by_scale'][0]
            if not torch.isfinite(loss):
                raise ValueError('nonfinite joint objective')
            key=selection_key(loss.detach(),terms.get('body_violation',0.))
            if best_key is None or key < best_key:
                best_key = key
                selected = parameters.detach().clone()
                best_step = step
            if step == 0:
                history.append(_snapshot(terms, executed, name, float(loss.detach())))
            if step == budget:
                break
            optimizer.zero_grad()
            loss.backward()
            if not torch.isfinite(parameters.grad).all():
                raise ValueError('nonfinite joint gradient')
            torch.nn.utils.clip_grad_norm_([parameters], 10.)
            optimizer.step()
            with torch.no_grad():
                parameters[:, FLEXION].copy_(torch.minimum(
                    parameters[:, FLEXION].clamp_min(0.), objective.model.maximum_bend))
        with torch.no_grad():
            _, terms = objective.evaluate(selected, levers)
        history.append(_snapshot(terms, executed+best_step, name, best_key[-1]))
        executed += budget
    polish = None
    if temporal_polish:
        from .temporal_step import local_average_direction
        from .protocol_step import search_bend_step

        def evaluate_candidate(candidate):
            if time.monotonic()-started > wall_limit_s:
                raise TimeoutError('physical joint stage exceeded budget')
            _, candidate_terms = objective.evaluate(candidate, levers)
            return candidate_terms['loss'], candidate_terms.get('body_violation', 0.)

        selected, polish = search_bend_step(
            evaluate_candidate, selected,
            local_average_direction(selected, objective.valid),
            objective.model.maximum_bend, backtracks=2)
        with torch.no_grad():
            _, terms = objective.evaluate(selected, levers)
        history.append(_snapshot(terms, executed, 'temporal_search', float(terms['loss'])))
    return selected, history, dict(optimizer='Adam coarse-to-fine continuation',
        feasible_body_selected=float(terms.get('body_violation',0.))<=1e-9,
        body_selection_policy='feasible first; otherwise least worst penetration, explicitly unaccepted',
        optimizer_steps=executed, phases=[dict(name=n, iterations=b, learning_rate=r) for n,b,r in phases],
        selected_step=history[-1]['step'], stage_scores_compared_across_objectives=False,
        temporal_search=polish)


def _snapshot(terms, step, phase, phase_loss):
    return dict(step=step, phase=phase, loss=float(terms['loss'].detach()),
        body_violation_m=float(terms.get('body_violation',0.)),
        phase_loss=phase_loss, acceleration_rms_mps2=float(terms['acceleration_rms_mps2'].detach()),
        prior_position_rms_m=float(terms['prior_position_rms_m'].detach()),
        energy_components={name: float(terms[name].detach()) for name in (
            'acceleration_loss', 'orientation_likelihood_loss',
            'weighted_pose_prior_loss', 'weighted_pose_smoothness_loss',
            'orientation_correction_smoothness', 'body_loss', 'axial_loss',
            'conditioned_arm_loss', 'protocol_loss', 'history_loss', 'gap_process_loss',
            'pelvis_orientation_loss', 'pelvis_orientation_smoothness') if name in terms})
