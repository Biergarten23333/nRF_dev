"""Opt-in root/leg reconstruction directly on the inherited knee manifold.

Explicit knee flexion removes the unsigned-bend projection from optimization.
The model owner's axes, ROM, and shortest-direction alignment define every
trial, constraint, and returned pose. Ankle endpoints remain geometric proxies;
this is a kinematic engineering model, not rigid-body PIP dynamics.
"""
from dataclasses import dataclass

import numpy as np
from scipy.optimize import minimize
from scipy.spatial.transform import Rotation

from ..c2_articulated_biomechanics.model import DOWN
from ..c2_articulated_biomechanics.orientation_ik import (
    _shortest_alignment_matrix, project_hinge_corrections,
)
from .articulated_range import SEGMENTS, corrected_proxy_points, _skew, _so3_right_jacobian
from .contact_motion_step import ContactMotionStepConfig, ContactMotionStepResult


SIDES = ('left', 'right')


@dataclass(frozen=True)
class ContactHingeMotionConfig(ContactMotionStepConfig):
    # Prediction uncertainty, not an imposed maximum knee displacement.
    knee_motion_sigma_m: float = .002
    solve_pelvis_orientation: bool = False

    def validate(self):
        super().validate()
        if not np.isfinite(self.knee_motion_sigma_m) or self.knee_motion_sigma_m <= 0:
            raise ValueError('knee prediction scale must be finite and positive')
        if not isinstance(self.solve_pelvis_orientation, bool):
            raise ValueError('pelvis orientation opt-in must be boolean')


class _HingeKinematics:
    """Root and two leg hinges, optionally native-relative pelvis coordinates.

    The existing FK owner uses pelvis attitude for hip offsets only. Its extra
    coordinates do not rotate the independently measured torso or arms.
    """

    def __init__(self, base, geometry, model, embedding, solve_pelvis_orientation=False):
        self.base = base
        self.geometry = geometry
        self.model = model
        self.embedding = embedding
        self.solve_pelvis_orientation = solve_pelvis_orientation

    def decode(self, x, derivatives=True):
        corrections = {s: np.zeros(3) for s in SEGMENTS}
        points, jacobians = {}, {}
        dimension = 14 if self.solve_pelvis_orientation else 11
        correction_j = np.zeros((15 if self.solve_pelvis_orientation else 12, dimension))
        pelvis = self.base['pelvis']
        if self.solve_pelvis_orientation:
            corrections['pelvis'] = x[11:14].copy()
            pelvis = pelvis@Rotation.from_rotvec(corrections['pelvis']).as_matrix()
            correction_j[12:15,11:14] = np.eye(3)
        for side_index, side in enumerate(SIDES):
            start = 3+4*side_index
            thigh, shank = 'thigh_'+side, 'shank_'+side
            joint = self.model['knee_'+side]
            axis = joint.positive_sign*np.asarray(joint.parent_axis)
            local = x[start:start+3]
            theta = x[start+3]
            parent = self.base[thigh]@Rotation.from_rotvec(local).as_matrix()
            bent_down = Rotation.from_rotvec(axis*theta).apply(DOWN)
            target = parent@bent_down
            child = _shortest_alignment_matrix((self.base[shank]@DOWN)[None], target[None])[0]@self.base[shank]
            corrections[thigh] = local.copy()
            corrections[shank] = Rotation.from_matrix(self.base[shank].T@child).as_rotvec()
            hip_local = np.array([(-.5 if side=='left' else .5)*self.geometry.hip_span_m, 0., 0.])
            hip = pelvis@hip_local
            upper = self.geometry.segment_length_m[thigh]
            lower = self.geometry.segment_length_m[shank]
            knee = hip+upper*(parent@DOWN)
            ankle = knee+lower*target
            points['knee_'+side] = self.embedding@knee
            points['ankle_'+side] = self.embedding@ankle
            if not derivatives:
                continue
            right_j = _so3_right_jacobian(local)
            knee_j = np.zeros((3, dimension))
            ankle_j = np.zeros((3, dimension))
            knee_j[:, :3] = ankle_j[:, :3] = np.eye(3)
            knee_j[:, start:start+3] = self.embedding@(-parent@_skew(upper*DOWN)@right_j)
            ankle_j[:, start:start+3] = self.embedding@(-parent@_skew(upper*DOWN+lower*bent_down)@right_j)
            ankle_j[:, start+3] = self.embedding@(lower*parent@np.cross(axis, bent_down))
            if self.solve_pelvis_orientation:
                hip_j = self.embedding@(-pelvis@_skew(hip_local)@_so3_right_jacobian(x[11:14]))
                knee_j[:,11:14] = hip_j
                ankle_j[:,11:14] = hip_j
            jacobians['knee_'+side] = knee_j
            jacobians['ankle_'+side] = ankle_j
            correction_j[6*side_index:6*side_index+3, start:start+3] = np.eye(3)
            # Only the SO(3) logarithm/shortest-alignment boundary needs numeric
            # derivatives. Eight perturbations per leg share one batched kernel.
            epsilon = 1e-6
            perturbations = np.r_[np.eye(4), -np.eye(4)]*epsilon
            coordinates = x[start:start+4]+perturbations
            parents = self.base[thigh]@Rotation.from_rotvec(coordinates[:, :3]).as_matrix()
            directions = Rotation.from_rotvec(coordinates[:, 3, None]*axis).apply(DOWN)
            targets = np.einsum('nij,nj->ni', parents, directions)
            children = _shortest_alignment_matrix(
                np.broadcast_to(self.base[shank]@DOWN, targets.shape), targets)@self.base[shank]
            logs = Rotation.from_matrix(self.base[shank].T@children).as_rotvec()
            correction_j[6*side_index+3:6*side_index+6, start:start+4] = ((logs[:4]-logs[4:])/(2*epsilon)).T
        return corrections, points, jacobians, correction_j


def solve_contact_hinge_motion(*, base_rotations_world, geometry, root_target_m,
                               root_prior_m, previous_feet_world_m, dt_s,
                               foot_speed_limits_m_s, hinge_model,
                               predicted_knees_world_m=None, hinge_projector=None,
                               previous_correction=None, embedding=None,
                               config=ContactHingeMotionConfig()):
    """Solve actual-hinge FK with committed-foot bounds and predicted knees.

    Pass actual committed world ankles and causally predicted world knees.
    Knee predictions are soft observations, never clipped or frozen. Existing
    tracking callers can inject this solver with a partial binding of the hinge
    model and current predictions; configuration must be this opt-in subclass.
    Rejection is non-installable and returns native/prior diagnostic values.
    """
    config.validate()
    if not np.isfinite(dt_s) or dt_s <= 0:
        raise ValueError('dt_s must be finite and positive')
    target = np.asarray(root_target_m, dtype=float).reshape(3)
    prior = np.asarray(root_prior_m, dtype=float).reshape(3)
    g = np.eye(3) if embedding is None else np.asarray(embedding, dtype=float)
    if (not np.isfinite(target).all() or not np.isfinite(prior).all() or g.shape != (3, 3)
            or not np.isfinite(g).all() or not np.allclose(g.T@g, np.eye(3), atol=1e-8)):
        raise ValueError('invalid root or embedding')
    base = {s: np.asarray(base_rotations_world[s], dtype=float).reshape(3, 3) for s in SEGMENTS}
    if any(not np.isfinite(r).all() for r in base.values()):
        raise ValueError('nonfinite native rotation')
    model = {n: hinge_model[n] for n in ('knee_left', 'knee_right')}
    for side in SIDES:
        joint = model['knee_'+side]
        axis = joint.positive_sign*np.asarray(joint.parent_axis)
        if (joint.parent != 'thigh_'+side or joint.child != 'shank_'+side
                or not np.isfinite(axis).all() or not np.isclose(np.linalg.norm(axis), 1.)
                or abs(axis@DOWN) > 1e-8 or not 0 <= joint.minimum_deg <= joint.maximum_deg < 180):
            raise ValueError('unsupported inherited knee-axis/ROM contract')
    previous = {n: np.asarray(v, dtype=float).reshape(3) for n, v in previous_feet_world_m.items()}
    knees = {n: np.asarray(v, dtype=float).reshape(3) for n, v in (predicted_knees_world_m or {}).items()}
    if (set(previous)-{'ankle_left', 'ankle_right'} or set(previous) != set(foot_speed_limits_m_s)
            or set(knees)-{'knee_left', 'knee_right'}):
        raise ValueError('invalid ankle or knee prediction keys')
    radii = {n: float(foot_speed_limits_m_s[n])*dt_s for n in previous}
    if (any(not np.isfinite(v).all() for v in (*previous.values(), *knees.values()))
            or any(not np.isfinite(r) or r < 0 for r in radii.values())):
        raise ValueError('nonfinite prediction or invalid foot-speed limit')
    active_names = tuple(s for side in SIDES for s in ('thigh_'+side, 'shank_'+side))
    if config.solve_pelvis_orientation:
        active_names += ('pelvis',)
    zero = {s: np.zeros(3) for s in SEGMENTS}
    previous_c = zero if previous_correction is None else previous_correction
    previous_active = np.concatenate([np.asarray(previous_c[s], dtype=float).reshape(3) for s in active_names])
    if not np.isfinite(previous_active).all():
        raise ValueError('nonfinite prior correction')
    kinematics = _HingeKinematics(base, geometry, model, g, config.solve_pelvis_orientation)
    dimension = 14 if config.solve_pelvis_orientation else 11
    limit = config.maximum_segment_correction_rad
    lower = np.r_[np.full(3, -config.maximum_root_step_m), np.full(dimension-3, -limit)]
    upper = -lower
    x0 = np.zeros(dimension)
    if config.solve_pelvis_orientation:
        x0[11:14] = np.clip(previous_active[12:15], -limit, limit)
    w_target, w_prior = config.root_target_sigma_m**-2, config.root_prior_sigma_m**-2
    x0[:3] = np.clip((target-prior)*w_target/(w_target+w_prior), lower[:3], upper[:3])
    for j, side in enumerate(SIDES):
        start = 3+4*j
        joint = model['knee_'+side]
        x0[start:start+3] = np.clip(previous_active[6*j:6*j+3], -limit, limit)
        parent = base[joint.parent]@Rotation.from_rotvec(x0[start:start+3]).as_matrix()
        child = base[joint.child]@Rotation.from_rotvec(previous_active[6*j+3:6*j+6]).as_matrix()
        bend = np.arctan2(np.linalg.norm(np.cross(parent@DOWN, child@DOWN)), (parent@DOWN)@(child@DOWN))
        lower[start+3], upper[start+3] = np.radians([joint.minimum_deg, joint.maximum_deg])
        x0[start+3] = np.clip(bend, lower[start+3], upper[start+3])
    if previous:
        # A support-centred INITIAL root avoids enormous soft-foot residuals
        # before SQP starts. It does not replace the optimized final root.
        _, initial_points, _, _ = kinematics.decode(x0, derivatives=False)
        x0[:3] = np.clip(np.mean([previous[n]-initial_points[n] for n in previous],axis=0)-prior,
                         lower[:3],upper[:3])
    # Full Gauss-Newton whitening includes coupled support directions and their
    # weak knee-motion nullspace. This changes coordinates only, not weights.
    _, _, initial_j, initial_cj = kinematics.decode(x0)
    curvature = (initial_cj.T@initial_cj)*(config.orientation_prior_sigma_rad**-2
                                         +config.temporal_orientation_sigma_rad**-2)
    curvature[:3,:3] += np.eye(3)*(w_target+w_prior)
    for n in knees:
        curvature += (initial_j[n].T@initial_j[n])/config.knee_motion_sigma_m**2
    if config.stationary_velocity_sigma_m_s is not None:
        for n in previous:
            curvature += (initial_j[n].T@initial_j[n])/(config.stationary_velocity_sigma_m_s*dt_s)**2
    eigenvalues,eigenvectors = np.linalg.eigh(curvature)
    if not np.isfinite(eigenvalues).all() or np.min(eigenvalues) <= 0:
        raise ValueError('manifold prior must define a positive definite metric')
    transform = eigenvectors/np.sqrt(eigenvalues)[None,:]
    cached_y = None
    cached = None
    def evaluate(y):
        nonlocal cached_y, cached
        if cached_y is None or not np.array_equal(cached_y, y):
            x = x0+transform@y
            c, p, j, cj = kinematics.decode(x)
            packed = np.concatenate([c[s] for s in active_names])
            cached = (prior+x[:3], c, p, {n: v@transform for n,v in j.items()}, packed, cj@transform)
            cached_y = y.copy()
        return cached
    def objective(y):
        root, c, p, j, packed, cj = evaluate(y)
        residuals = [(root-target)/config.root_target_sigma_m,
                     (root-prior)/config.root_prior_sigma_m,
                     packed/config.orientation_prior_sigma_rad,
                     (packed-previous_active)/config.temporal_orientation_sigma_rad]
        root_j = transform[:3]
        jacobians = [root_j/config.root_target_sigma_m, root_j/config.root_prior_sigma_m,
                     cj/config.orientation_prior_sigma_rad, cj/config.temporal_orientation_sigma_rad]
        for n, prediction in knees.items():
            residuals.append((root+p[n]-prediction)/config.knee_motion_sigma_m)
            jacobians.append(j[n]/config.knee_motion_sigma_m)
        if config.stationary_velocity_sigma_m_s is not None:
            sigma = config.stationary_velocity_sigma_m_s*dt_s
            for n in previous:
                residuals.append((root+p[n]-previous[n])/sigma)
                jacobians.append(j[n]/sigma)
        residual = np.concatenate(residuals)
        jacobian = np.vstack(jacobians)
        return .5*float(residual@residual), jacobian.T@residual
    constraints = [dict(type='ineq', fun=lambda y: np.r_[limit-evaluate(y)[4], limit+evaluate(y)[4]],
                        jac=lambda y: np.vstack((-evaluate(y)[5], evaluate(y)[5]))),
                   dict(type='ineq', fun=lambda y: np.r_[x0+transform@y-lower, upper-x0-transform@y],
                        jac=lambda y: np.vstack((transform,-transform)))]
    for n, radius in radii.items():
        def displacement(y, n=n):
            root, _, p, j, _, _ = evaluate(y)
            return root+p[n]-previous[n], j[n]
        if radius == 0:
            constraints.append(dict(type='eq', fun=lambda y, d=displacement: d(y)[0],
                                    jac=lambda y, d=displacement: d(y)[1]))
        else:
            def constraint(y, d=displacement, r=radius):
                return r-np.linalg.norm(d(y)[0])
            def constraint_j(y, d=displacement):
                delta, jac = d(y)
                return -(delta/max(np.linalg.norm(delta), 1e-15))@jac
            constraints.append(dict(type='ineq', fun=constraint, jac=constraint_j))
    fit = minimize(objective, np.zeros(dimension), jac=True, method='SLSQP', constraints=constraints,
                   options={'maxiter': config.maximum_iterations, 'ftol': 1e-8})
    root, corrections, p, _, packed, _ = evaluate(fit.x)
    # Verification only: no post-solve pose modification is ever installed.
    projector = hinge_projector or (lambda b,c: project_hinge_corrections(b,c,model))
    verified, projection = projector(base, {s:c.copy() for s,c in corrections.items()})
    parity = max(np.linalg.norm(verified[s]-corrections[s]) for s in SEGMENTS)
    candidate_points = {n:g@v for n,v in corrected_proxy_points(
        base,corrections,geometry,_batch_rotation_conversion=True).items()}
    fk_parity = max(np.linalg.norm(candidate_points[n]-v) for n,v in p.items())
    violation = max([0.]+[np.linalg.norm(root+candidate_points[n]-previous[n])-r for n,r in radii.items()])
    accepted = bool(np.isfinite(root).all() and np.isfinite(packed).all()
        and np.max(np.abs(packed)) <= limit+1e-10
        and np.all(np.abs(root-prior) <= config.maximum_root_step_m+1e-10)
        and violation <= config.constraint_tolerance_m
        and parity <= 1e-8 and fk_parity <= 1e-8
        and projection.get('post_projection_all_inside_rom') is True)
    reason = ('ACCEPTED' if fit.success else 'FEASIBLE_NONCONVERGED') if accepted else 'MANIFOLD_CONSTRAINT_REJECTED'
    projection = dict(projection, motion_solver_status=int(fit.status), motion_solver_converged=bool(fit.success),
                      motion_solver_message=str(fit.message), manifold_verification_difference_rad=float(parity),
                      manifold_fk_difference_m=float(fk_parity))
    output_c = corrections if accepted else zero
    output_root = root if accepted else prior
    points = candidate_points if accepted else {n:g@v for n,v in corrected_proxy_points(
        base,output_c,geometry,_batch_rotation_conversion=True).items()}
    return ContactMotionStepResult(accepted, reason, output_root.copy(), output_c, points,
        {'ankle_'+s:output_root+points['ankle_'+s] for s in SIDES},float(violation),fit.nit,projection)
