"""Opt-in, single-epoch tangent bridge for the natural bend-plane adapter.

This is anatomical projection only, not a physical sensor attitude update.
The caller owns temporal twist state and must commit the returned nominal
``last_twist_rad`` only when accepting that epoch. Covariance/state integration
is deliberately outside this component.
"""

import numpy as np
from scipy.spatial.transform import Rotation

from .bend_plane import reconcile_hinge_bend_plane
from .model import DOWN, _rotation, _wxyz


def linearize_bend_plane_projection(rotations, segments, model, epsilon=1e-6,
                                    *, initial_twist_rad=None,
                                    extension_threshold_deg=.5):
    """Return anatomical rotations, full right-local Jacobian, nominal audits.

    Input/output tangents satisfy ``R_new = R @ Exp(delta)``. For the ten
    articulated segments the Jacobian is 30 x 30, including altered parent
    rows. Only disjoint hinge pairs are supported. All stencil evaluations
    use the same held twist, never another perturbation's temporal state.

    Central differences are piecewise: a stencil crossing the extension or
    upper-ROM boundary raises ValueError instead of returning a misleading
    derivative. Away from those boundaries, both reliable-plane and held-
    twist branches are supported. The derivative conditions on the supplied
    twist state; it does not differentiate through previous epochs.
    """
    rotations = np.asarray(rotations, dtype=float)
    segments = tuple(segments)
    if (rotations.shape != (len(segments), 3, 3)
            or len(set(segments)) != len(segments)
            or not np.isfinite(rotations).all()
            or not np.isfinite(epsilon) or epsilon <= 0
            or not 0 <= extension_threshold_deg < 90):
        raise ValueError('invalid bend-plane linearization input')
    if (not np.allclose(rotations.swapaxes(1, 2) @ rotations, np.eye(3),
                        atol=1e-8, rtol=0)
            or not np.allclose(np.linalg.det(rotations), 1., atol=1e-8, rtol=0)):
        raise ValueError('proper input rotation matrices required')
    initial_twist_rad = {} if initial_twist_rad is None else dict(initial_twist_rad)
    if (set(initial_twist_rad) - set(model)
            or not all(np.isfinite(value) for value in initial_twist_rad.values())):
        raise ValueError('invalid per-joint initial twist state')
    index = {name: i for i, name in enumerate(segments)}
    if any(joint.parent not in index or joint.child not in index
           for joint in model.values()):
        raise ValueError('hinge participant missing from segment stack')
    pairs = [(index[joint.parent], index[joint.child]) for joint in model.values()]
    participants = [i for pair in pairs for i in pair]
    if len(set(participants)) != len(participants):
        raise ValueError('natural bend-plane linearization requires disjoint hinges')
    projected = rotations.copy()
    jacobian = np.eye(3 * len(segments))
    audits = {}
    # Six independent input axes per pair, with positive and negative stencils.
    inputs = np.r_[np.eye(6), -np.eye(6)].reshape(12, 2, 3) * epsilon
    increments = Rotation.from_rotvec(inputs.reshape(-1, 3)).as_matrix().reshape(12, 2, 3, 3)
    for (name, joint), (parent, child) in zip(model.items(), pairs):
        base = rotations[[parent, child]]
        trials = base[None] @ increments
        all_rows = np.concatenate((base[None], trials))
        directions = all_rows @ DOWN
        bend = np.degrees(np.arctan2(
            np.linalg.norm(np.cross(directions[:, 0], directions[:, 1]), axis=1),
            np.sum(directions[:, 0] * directions[:, 1], axis=1)))
        reliable = ((bend > extension_threshold_deg)
                    & (bend < 180 - extension_threshold_deg))
        capped = bend > joint.maximum_deg
        if np.any(reliable != reliable[0]) or np.any(capped != capped[0]):
            raise ValueError(f'{name}: finite-difference stencil crosses projection branch boundary')
        kwargs = dict(initial_twist_rad=initial_twist_rad.get(name, 0.),
                      extension_threshold_deg=extension_threshold_deg)
        parent_q, child_q, audit = reconcile_hinge_bend_plane(
            _wxyz(Rotation.from_matrix(base[:1])),
            _wxyz(Rotation.from_matrix(base[1:])), joint, **kwargs)
        projected[parent] = _rotation(parent_q).as_matrix()[0]
        projected[child] = _rotation(child_q).as_matrix()[0]
        # Batching is safe only after checking every row is in the same branch:
        # reliable rows each resolve their own plane; held rows all use kwargs.
        # Quaternion unwrap differs by 2*pi only and cannot alter rotations.
        parent_q, child_q, _ = reconcile_hinge_bend_plane(
            _wxyz(Rotation.from_matrix(trials[:, 0])),
            _wxyz(Rotation.from_matrix(trials[:, 1])), joint, **kwargs)
        output = np.stack((_rotation(parent_q).as_matrix(),
                           _rotation(child_q).as_matrix()), axis=1)
        local = projected[[parent, child]].swapaxes(1, 2)[None] @ output
        differences = Rotation.from_matrix(local.reshape(-1, 3, 3)).as_rotvec().reshape(12, 6)
        block = ((differences[:6] - differences[6:]) / (2 * epsilon)).T
        slots = np.r_[np.arange(3 * parent, 3 * parent + 3),
                      np.arange(3 * child, 3 * child + 3)]
        jacobian[slots] = 0.
        jacobian[np.ix_(slots, slots)] = block
        audits[name] = dict(audit, linearization_branch='observed_plane' if reliable[0] else 'held_twist',
                            linearization_epsilon_rad=epsilon,
                            twist_state_derivative_included=False)
    return projected, jacobian, audits
