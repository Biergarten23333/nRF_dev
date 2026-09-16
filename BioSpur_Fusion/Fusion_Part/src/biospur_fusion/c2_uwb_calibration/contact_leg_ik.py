"""Conditional native-reference leg IK; not a joint probabilistic update.

Root/contact targets are held external. No ranges are consumed and no root or
pose covariance is produced. The inherited scales are engineering MAP priors.
Every correction is relative to this frame's native base, never accumulated.
"""
from dataclasses import dataclass
from typing import Mapping, Callable

import numpy as np
from scipy.optimize import least_squares
from scipy.spatial.transform import Rotation

from .articulated_range import (
    SEGMENTS, ArticulatedRangeConfig, corrected_proxy_points,
    _skew, _so3_right_jacobian, DEFAULT_POINT_CONSTRAINT_SIGMA_M,
)


def _leg_endpoints(base, corrections, geometry, active, names, embedding):
    """Exact inherited FK/J restricted to fixed-pelvis leg endpoints.

    Local additive rotation-vector coordinates and all signs match
    articulated_range._corrected_proxy_point_jacobians; no new body model.
    """
    deltas = np.stack([corrections[s] for s in active])
    rotations = np.stack([base[s] for s in active]) @ Rotation.from_rotvec(deltas).as_matrix()
    points, jacobians = {}, {}
    for name in names:
        side = name[6:]
        p = base['pelvis'] @ np.array([(-.5 if side == 'left' else .5)*geometry.hip_span_m, 0., 0.])
        j = np.zeros((3, 3*len(active)))
        for segment in ('thigh_'+side, 'shank_'+side):
            i = active.index(segment)
            v = np.array([0., 0., -geometry.segment_length_m[segment]])
            p = p + rotations[i] @ v
            j[:, 3*i:3*i+3] = -rotations[i] @ _skew(v) @ _so3_right_jacobian(deltas[i])
        points[name] = embedding @ p
        jacobians[name] = embedding @ j
    return points, jacobians


@dataclass(frozen=True)
class ContactLegIKResult:
    accepted: bool
    reason: str
    corrections: Mapping[str, np.ndarray]
    points: Mapping[str, np.ndarray]
    initial_cost: float
    final_cost: float
    nfev: int
    projection: Mapping


def solve_contact_leg_ik(*, base_rotations_world, geometry, root_position_m,
                         targets_world_m, hinge_projector: Callable,
                         previous_correction=None, embedding=None,
                         point_sigma_m=DEFAULT_POINT_CONSTRAINT_SIGMA_M,
                         config=ArticulatedRangeConfig()):
    """Fit supported legs only, then admit the actual hinge-projected pose.

    ``embedding`` acts on geometric points/Jacobians, not physical rotations
    or the original right-local correction coordinates. Rejection returns the
    native zero correction; callers must not install the rejected trial.
    """
    config.validate()
    root = np.asarray(root_position_m, dtype=float).reshape(3)
    g = np.eye(3) if embedding is None else np.asarray(embedding, dtype=float)
    if (g.shape != (3, 3) or not np.isfinite(g).all()
            or not np.allclose(g.T @ g, np.eye(3), atol=1e-8)
            or not np.isfinite(root).all()):
        raise ValueError('invalid fixed root or embedding')
    names = tuple(targets_world_m)
    if set(names) - {'ankle_left', 'ankle_right'}:
        raise ValueError('contact leg IK accepts ankle targets only')
    if isinstance(point_sigma_m, Mapping):
        if set(point_sigma_m) != set(names):
            raise ValueError('contact scales must match target names exactly')
        scales = {n: float(point_sigma_m[n]) for n in names}
    else:
        scalar = float(point_sigma_m)
        if not np.isfinite(scalar) or scalar <= 0:
            raise ValueError('contact scales must be finite and positive')
        scales = {n: scalar for n in names}
    if any(not np.isfinite(s) or s <= 0 for s in scales.values()):
        raise ValueError('contact scales must be finite and positive')
    targets = {k: np.asarray(targets_world_m[k], dtype=float).reshape(3) for k in names}
    if any(not np.isfinite(v).all() for v in targets.values()):
        raise ValueError('nonfinite contact target')
    active = tuple(s for s in SEGMENTS if any(
        s in ('thigh_' + n[6:], 'shank_' + n[6:]) for n in names))
    zero = {s: np.zeros(3) for s in SEGMENTS}
    def points(c):
        return {k: g @ v for k, v in corrected_proxy_points(
            base_rotations_world, c, geometry, _batch_rotation_conversion=True).items()}
    native = points(zero)
    if not active:
        return ContactLegIKResult(True, 'NO_CONTACT', zero, native, 0., 0., 0, {})
    previous = zero if previous_correction is None else previous_correction
    prior = np.concatenate([np.asarray(previous[s], dtype=float) for s in active])
    if not np.isfinite(prior).all():
        raise ValueError('nonfinite previous correction')
    limit = config.maximum_segment_correction_rad
    def unpack(x):
        c = {s: v.copy() for s, v in zero.items()}
        for i, s in enumerate(active):
            c[s] = x[3*i:3*i+3]
        return c
    def residual_c(c):
        x = np.concatenate([c[s] for s in active])
        p, _ = endpoints(x)
        return np.concatenate([x / config.orientation_prior_sigma_rad,
            (x-prior) / config.temporal_orientation_sigma_rad,
            *[(root+p[n]-targets[n]) / scales[n] for n in names]])
    def jac(x):
        _, j = endpoints(x)
        eye = np.eye(len(x))
        return np.vstack([eye/config.orientation_prior_sigma_rad,
            eye/config.temporal_orientation_sigma_rad,
            *[j[n]/scales[n] for n in names]])
    cached_x = None
    cached_result = None
    def endpoints(x):
        nonlocal cached_x, cached_result
        if cached_x is None or not np.array_equal(x, cached_x):
            cached_result = _leg_endpoints(base_rotations_world, unpack(x), geometry, active, names, g)
            cached_x = x.copy()
        return cached_result
    x0 = np.clip(prior, -limit, limit)
    # Admission is against the returned fallback (native zero pose), not an
    # uncommitted warm start which may already be worse than that fallback.
    initial = float(residual_c(zero) @ residual_c(zero))
    fit = least_squares(lambda x: residual_c(unpack(x)), x0, jac=jac,
                        bounds=(-limit, limit), max_nfev=config.maximum_nfev)
    corrected, projection = hinge_projector(base_rotations_world, unpack(fit.x))
    final = float(residual_c(corrected) @ residual_c(corrected))
    bounded = all(np.isfinite(corrected[s]).all() and
                  np.all(np.abs(corrected[s]) <= limit+1e-10) for s in SEGMENTS)
    inactive = all(np.allclose(corrected[s], 0., atol=1e-10) for s in SEGMENTS if s not in active)
    inside = projection.get('post_projection_all_inside_rom') is True
    accepted = bool(fit.success and bounded and inactive and inside and
                    np.isfinite(final) and final <= initial + 1e-9)
    return ContactLegIKResult(accepted, 'ACCEPTED' if accepted else 'PROJECTED_POSE_REJECTED',
        corrected if accepted else zero, points(corrected) if accepted else native,
        initial, final, fit.nfev, projection)
