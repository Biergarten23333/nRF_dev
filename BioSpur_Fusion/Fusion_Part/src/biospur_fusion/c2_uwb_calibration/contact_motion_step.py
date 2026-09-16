"""Bounded kinematic motion reconstruction, not full PIP dynamics.

The inherited ankle endpoint is a proxy, not a measured foot sole. This small
SQP adjusts one root and four right-local leg rotations around the CURRENT
native pose. It has no force, torque, covariance, or new skeleton model.
"""
from dataclasses import dataclass
from typing import Callable, Mapping

import numpy as np
from scipy.optimize import minimize

from .articulated_range import SEGMENTS, corrected_proxy_points
from .contact_leg_ik import _leg_endpoints


LEGS = tuple(s for s in SEGMENTS if s.startswith(('thigh_', 'shank_')))
FEET = ('ankle_left', 'ankle_right')


@dataclass(frozen=True)
class ContactMotionStepConfig:
    root_target_sigma_m: float = .04
    root_prior_sigma_m: float = .08
    orientation_prior_sigma_rad: float = .10
    temporal_orientation_sigma_rad: float = .08
    maximum_root_step_m: float = .08
    maximum_segment_correction_rad: float = .18
    maximum_iterations: int = 12
    constraint_tolerance_m: float = 1e-7
    stationary_velocity_sigma_m_s: float | None = None

    def validate(self):
        positive = (self.root_target_sigma_m, self.root_prior_sigma_m,
                    self.orientation_prior_sigma_rad, self.temporal_orientation_sigma_rad,
                    self.maximum_root_step_m,
                    self.maximum_segment_correction_rad, self.constraint_tolerance_m)
        if any(not np.isfinite(v) or v <= 0 for v in positive):
            raise ValueError('motion-step scales must be finite and positive')
        if not isinstance(self.maximum_iterations, int) or self.maximum_iterations < 1:
            raise ValueError('motion-step iteration budget must be a positive integer')
        if (self.stationary_velocity_sigma_m_s is not None and
                (not np.isfinite(self.stationary_velocity_sigma_m_s) or self.stationary_velocity_sigma_m_s <= 0)):
            raise ValueError('stationary velocity scale must be finite and positive')


@dataclass(frozen=True)
class ContactMotionStepResult:
    accepted: bool
    reason: str
    root_position_m: np.ndarray
    corrections: Mapping[str, np.ndarray]
    points: Mapping[str, np.ndarray]
    feet_world_m: Mapping[str, np.ndarray]
    maximum_constraint_violation_m: float
    nit: int
    projection: Mapping


def _closest_in_balls(target, centers, radii, tolerance):
    """Exact closest root in the intersection of at most two closed balls.

    With fixed projected FK, foot bounds are root balls. This admission-stage
    repair changes root only, never interpolates output or hides foot motion.
    """
    def inside(point):
        return all(np.linalg.norm(point-c) <= r+tolerance for c, r in zip(centers, radii))
    if inside(target):
        return target.copy()
    candidates = []
    for c, r in zip(centers, radii):
        delta = target-c
        norm = np.linalg.norm(delta)
        candidate = c + delta * min(1., r/max(norm, 1e-30))
        if inside(candidate):
            candidates.append(candidate)
    if candidates:
        return min(candidates, key=lambda p: np.linalg.norm(p-target))
    if len(centers) != 2:
        return None
    c0, c1 = centers
    r0, r1 = radii
    distance = np.linalg.norm(c1-c0)
    if distance > r0+r1+tolerance or distance < abs(r0-r1)-tolerance or distance < 1e-15:
        return None
    axis = (c1-c0)/distance
    offset = (r0*r0-r1*r1+distance*distance)/(2*distance)
    center = c0+offset*axis
    radius = np.sqrt(max(0., r0*r0-offset*offset))
    perpendicular = target-center-axis*np.dot(target-center, axis)
    if np.linalg.norm(perpendicular) < 1e-15:
        seed = np.eye(3)[np.argmin(np.abs(axis))]
        perpendicular = np.cross(axis, seed)
    candidate = center+radius*perpendicular/np.linalg.norm(perpendicular)
    return candidate if inside(candidate) else None


def solve_contact_motion_step(*, base_rotations_world, geometry, root_target_m,
                              root_prior_m, previous_feet_world_m, dt_s,
                              foot_speed_limits_m_s, hinge_projector: Callable,
                              previous_correction=None, embedding=None,
                              config=ContactMotionStepConfig()):
    """Reconstruct one native-referenced step with committed-foot speed bounds.

    Constraint maps contain supported ankles only and must have matching keys.
    ``previous_feet_world_m`` MUST be FK of the previous committed pose/root,
    not native feet or a contact latch. Rejection is explicitly non-installable:
    returned native/prior diagnostics are not a safe replacement for committed
    state. On release the temporal correction prior decays toward native rather
    than resetting the previously committed correction in one frame.
    Optional stationary-velocity residuals prefer zero ankle motion inside the
    unchanged hard speed balls; their scale is engineering, not measurement R.
    """
    config.validate()
    target = np.asarray(root_target_m, dtype=float).reshape(3)
    prior = np.asarray(root_prior_m, dtype=float).reshape(3)
    g = np.eye(3) if embedding is None else np.asarray(embedding, dtype=float)
    if (not np.isfinite(target).all() or not np.isfinite(prior).all()
            or g.shape != (3, 3) or not np.isfinite(g).all()
            or not np.allclose(g.T@g, np.eye(3), atol=1e-8)):
        raise ValueError('invalid root or geometric embedding')
    if not np.isfinite(dt_s) or dt_s <= 0:
        raise ValueError('dt_s must be finite and positive')
    names = tuple(previous_feet_world_m)
    if set(names)-set(FEET) or set(names) != set(foot_speed_limits_m_s):
        raise ValueError('supported ankle maps must have matching keys')
    previous = {n: np.asarray(previous_feet_world_m[n], dtype=float).reshape(3) for n in names}
    radii = np.array([float(foot_speed_limits_m_s[n])*dt_s for n in names])
    if (any(not np.isfinite(p).all() for p in previous.values())
            or not np.isfinite(radii).all() or np.any(radii < 0)):
        raise ValueError('invalid committed feet or speed limit')
    zero = {s: np.zeros(3) for s in SEGMENTS}
    previous_c = zero if previous_correction is None else previous_correction
    previous_leg = np.concatenate([np.asarray(previous_c[s], dtype=float).reshape(3) for s in LEGS])
    if not np.isfinite(previous_leg).all():
        raise ValueError('nonfinite previous correction')
    def all_points(c):
        return {n: g@p for n, p in corrected_proxy_points(
            base_rotations_world, c, geometry, _batch_rotation_conversion=True).items()}
    def result(accepted, reason, root, c, violation, nit, projection):
        points = all_points(c)
        return ContactMotionStepResult(accepted, reason, root.copy(), c, points,
            {n: root+points[n] for n in FEET}, violation, nit, projection)
    def unpack(x):
        c = {s: v.copy() for s, v in zero.items()}
        for i, s in enumerate(LEGS):
            c[s] = x[3+3*i:6+3*i].copy()
        return c
    w_target = config.root_target_sigma_m**-2
    w_prior = config.root_prior_sigma_m**-2
    desired = (w_target*target+w_prior*prior)/(w_target+w_prior)
    if not names:
        temporal_weight = config.temporal_orientation_sigma_rad**-2
        native_weight = config.orientation_prior_sigma_rad**-2
        x = np.r_[np.zeros(3), np.clip(previous_leg*temporal_weight/(native_weight+temporal_weight),
                    -config.maximum_segment_correction_rad, config.maximum_segment_correction_rad)]
        corrected, projection = hinge_projector(base_rotations_world, unpack(x))
        bounded = all(np.isfinite(corrected[s]).all() and
                      np.all(np.abs(corrected[s]) <= config.maximum_segment_correction_rad+1e-10)
                      for s in SEGMENTS)
        fixed = all(np.allclose(corrected[s], 0., atol=1e-10) for s in SEGMENTS if s not in LEGS)
        accepted = bounded and fixed and projection.get('post_projection_all_inside_rom') is True
        root = prior+np.clip(desired-prior, -config.maximum_root_step_m, config.maximum_root_step_m)
        return result(accepted, 'NO_CONTACT' if accepted else 'PROJECTED_POSE_REJECTED',
                      root if accepted else prior, corrected if accepted else zero, 0., 0, projection)
    cached_x, cached_fk = None, None
    def fk(x):
        nonlocal cached_x, cached_fk
        if cached_x is None or not np.array_equal(x, cached_x):
            p, j = _leg_endpoints(base_rotations_world, unpack(x), geometry, LEGS, names, g)
            cached_fk = (np.stack([prior+x[:3]+p[n]-previous[n] for n in names]),
                         np.stack([np.column_stack((np.eye(3), j[n])) for n in names]))
            cached_x = x.copy()
        return cached_fk
    # Whiten the FULL quadratic precision, including the strong dt-scaled
    # tracking prior. Physical objective/constraints remain unchanged.
    root_scale = (config.root_target_sigma_m**-2+config.root_prior_sigma_m**-2)**-.5
    leg_scale = (config.orientation_prior_sigma_rad**-2+config.temporal_orientation_sigma_rad**-2)**-.5
    scale = np.r_[np.full(3, root_scale), np.full(12, leg_scale)]
    stationary_step_sigma = (None if config.stationary_velocity_sigma_m_s is None
                             else config.stationary_velocity_sigma_m_s*dt_s)
    def objective(y):
        x = y*scale
        residual = (prior+x[:3]-target)/config.root_target_sigma_m
        prior_residual = x[:3]/config.root_prior_sigma_m
        temporal = (x[3:]-previous_leg)/config.temporal_orientation_sigma_rad
        native = x[3:]/config.orientation_prior_sigma_rad
        value = .5*(residual@residual+prior_residual@prior_residual+native@native+temporal@temporal)
        gradient = np.empty_like(y)
        gradient[3:] = (native/config.orientation_prior_sigma_rad
                       +temporal/config.temporal_orientation_sigma_rad)*scale[3:]
        gradient[:3] = (residual/config.root_target_sigma_m
                       +prior_residual/config.root_prior_sigma_m)*scale[:3]
        if stationary_step_sigma is not None:
            displacement, endpoint_j = fk(x)
            foot_residual = displacement/stationary_step_sigma
            value += .5*float(np.sum(foot_residual*foot_residual))
            gradient += (endpoint_j.reshape(-1, 15).T@foot_residual.reshape(-1)
                         /stationary_step_sigma)*scale
        return value, gradient
    constraints = []
    for i, radius in enumerate(radii):
        if radius == 0:
            constraints.append(dict(type='eq',
                fun=lambda y, i=i: fk(y*scale)[0][i],
                jac=lambda y, i=i: fk(y*scale)[1][i]*scale))
        else:
            def fun(y, i=i, radius=radius):
                return radius-np.linalg.norm(fk(y*scale)[0][i])
            def jac(y, i=i):
                delta, j = fk(y*scale)
                return -(delta[i]/max(np.linalg.norm(delta[i]), 1e-15))@j[i]*scale
            constraints.append(dict(type='ineq', fun=fun, jac=jac))
    bounds = np.r_[np.full(3, config.maximum_root_step_m),
                   np.full(12, config.maximum_segment_correction_rad)]/scale
    initial = np.zeros(15)
    initial[:3] = (desired-prior)/scale[:3]
    native_weight = config.orientation_prior_sigma_rad**-2
    temporal_weight = config.temporal_orientation_sigma_rad**-2
    initial[3:] = previous_leg*temporal_weight/(native_weight+temporal_weight)/scale[3:]
    # One analytic equality-constrained PRIOR-quadratic warm start towards the ball
    # centres avoids spending the small SQP budget finding the support surface.
    # Singular straight-leg configurations use least squares, never inversion.
    delta, j = fk(np.zeros(15))
    a = j.reshape(-1, 15)*scale
    # The optional foot penalty vanishes on these linearized equality centres;
    # this is only a warm start, not a claimed Hessian for the full objective.
    weighted = a.T  # Whitened prior Hessian is exactly identity.
    initial -= weighted@np.linalg.lstsq(a@weighted, a@initial+delta.reshape(-1), rcond=1e-10)[0]
    initial = np.clip(initial, -bounds, bounds)
    fit = minimize(objective, initial, jac=True, method='SLSQP',
                   bounds=list(zip(-bounds, bounds)), constraints=constraints,
                   options={'maxiter': config.maximum_iterations, 'ftol': 1e-8})
    corrected, projection = hinge_projector(base_rotations_world, unpack(fit.x*scale))
    projection = dict(projection, motion_solver_status=int(fit.status),
                      motion_solver_message=str(fit.message), motion_solver_converged=bool(fit.success))
    bounded = all(np.isfinite(corrected[s]).all() and
                  np.all(np.abs(corrected[s]) <= config.maximum_segment_correction_rad+1e-10)
                  for s in SEGMENTS)
    fixed = all(np.allclose(corrected[s], 0., atol=1e-10) for s in SEGMENTS if s not in LEGS)
    if not bounded or not fixed or projection.get('post_projection_all_inside_rom') is not True:
        return result(False, 'PROJECTED_POSE_REJECTED', prior, zero, float('inf'), fit.nit, projection)
    p, _ = _leg_endpoints(base_rotations_world, corrected, geometry, LEGS, names, g)
    # Root-only optimum for the frozen projected pose, constrained by exact feet.
    if stationary_step_sigma is not None:
        foot_weight = stationary_step_sigma**-2
        desired = ((w_target+w_prior)*desired
                   +foot_weight*np.sum([previous[n]-p[n] for n in names], axis=0)) / (
                       w_target+w_prior+len(names)*foot_weight)
    root = _closest_in_balls(desired, [previous[n]-p[n] for n in names],
                             radii, config.constraint_tolerance_m)
    if root is None:
        return result(False, 'FOOT_CONSTRAINT_REJECTED', prior, zero, float('inf'), fit.nit, projection)
    violation = max(0., max(np.linalg.norm(root+p[n]-previous[n])-r for n, r in zip(names, radii)))
    accepted = bool(np.isfinite(root).all() and violation <= config.constraint_tolerance_m
                    and np.all(np.abs(root-prior) <= config.maximum_root_step_m+1e-10))
    # A budget-limited solve can still yield a verified feasible motion. Report
    # that separately; feasibility is not a claim of optimality or convergence.
    feasible_reason = ('ACCEPTED' if fit.success else
                       'FEASIBLE_ITERATION_LIMIT' if fit.status == 9 else 'FEASIBLE_NONCONVERGED')
    reason = feasible_reason if accepted else 'ROOT_STEP_REJECTED'
    return result(accepted, reason, root if accepted else prior,
                  corrected if accepted else zero, violation, fit.nit, projection)
