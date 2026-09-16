"""Sparse finite differences of the existing, authoritative hinge projector.

Independent elbow/knee pairs have disjoint parent/child inputs. Their six
perturbations can share batch rows, without changing epsilon, model or cadence.
Coupled models retain the dense finite-difference path.
"""
import numpy as np
from scipy.spatial.transform import Rotation

from .orientation_ik import evaluate_hinge_projection_batch, project_hinge_rotation_pairs


def linearize_hinge_projection(rotations, segments, model, epsilon=1e-6):
    """Return projected rotations, right-local Jacobian and nominal audit."""
    rotations = np.asarray(rotations, dtype=float)
    segments = tuple(segments)
    if (rotations.shape != (len(segments), 3, 3)
            or len(set(segments)) != len(segments)
            or not np.isfinite(epsilon) or epsilon <= 0):
        raise ValueError('invalid hinge linearization input')
    width = 3 * len(segments)
    if not model:
        return rotations.copy(), np.eye(width), {}
    index = {name: i for i, name in enumerate(segments)}
    pairs = [(index[joint.parent], index[joint.child]) for joint in model.values()]
    participants = [i for pair in pairs for i in pair]
    disjoint = len(set(participants)) == len(participants)
    inputs = np.zeros((7 if disjoint else width + 1, len(segments), 3))
    if disjoint:
        for parent, child in pairs:
            inputs[1:4, parent] = np.eye(3) * epsilon
            inputs[4:7, child] = np.eye(3) * epsilon
    else:
        inputs[1:] = np.eye(width).reshape(width, len(segments), 3) * epsilon
    base = dict(zip(segments, rotations))
    if disjoint:
        projected = rotations[None] @ Rotation.from_rotvec(inputs.reshape(-1, 3)).as_matrix().reshape(
            len(inputs), len(segments), 3, 3)
        parents, children = np.asarray(pairs).T
        joints = tuple(model.values())
        count = len(joints)
        axes = np.array([joint.positive_sign * np.asarray(joint.parent_axis) for joint in joints])
        child_output, *_ = project_hinge_rotation_pairs(
            projected[:, parents].reshape(-1, 3, 3),
            projected[:, children].reshape(-1, 3, 3),
            np.tile(axes, (len(inputs), 1)),
            np.tile([joint.minimum_deg for joint in joints], len(inputs)),
            np.tile([joint.maximum_deg for joint in joints], len(inputs)))
        projected[:, children] = child_output.reshape(len(inputs), count, 3, 3)
        # Keep the public audit owner; only derivative rows bypass dictionary
        # conversion and repeated per-joint numerical dispatch.
        _, audits = evaluate_hinge_projection_batch(base,
            {name: np.zeros((1, 3)) for name in segments}, model)
        local = projected[0].swapaxes(1, 2)[None] @ projected[1:]
        differences = Rotation.from_matrix(local.reshape(-1, 3, 3)).as_rotvec().reshape(
            6, len(segments), 3) / epsilon
        jacobian = np.eye(width)
        for parent, child in pairs:
            child_rows = slice(3*child, 3*child+3)
            jacobian[child_rows] = 0.
            jacobian[child_rows, 3*parent:3*parent+3] = differences[:3, child].T
            jacobian[child_rows, 3*child:3*child+3] = differences[3:6, child].T
        return projected[0], jacobian, audits[0]
    rows, audits = [], []
    for start in range(0, len(inputs), 16):
        projected, audit = evaluate_hinge_projection_batch(base,
            {name: inputs[start:start+16, i] for i, name in enumerate(segments)}, model)
        rows.extend(projected)
        audits.extend(audit)
    corrections = np.array([[row[name] for name in segments] for row in rows])
    projected = rotations[None] @ Rotation.from_rotvec(
        corrections.reshape(-1, 3)).as_matrix().reshape(len(inputs), len(segments), 3, 3)
    local = projected[0].swapaxes(1, 2)[None] @ projected[1:]
    differences = Rotation.from_matrix(local.reshape(-1, 3, 3)).as_rotvec().reshape(
        len(inputs)-1, len(segments), 3) / epsilon
    jacobian = differences.reshape(width, width).T
    return projected[0], jacobian, audits[0]
