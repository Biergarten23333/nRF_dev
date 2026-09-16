"""Measured-size adaptation of the upstream SMPL kinematic model.

Tape measurements are surface-landmark chords, not joint centres. The shape
fit below is an explicitly approximate diagnostic: a 20 mm mapping standard
deviation and a standard-normal shape prior prevent exact surface-to-joint
substitution. It is not an anatomical calibration acceptance result.
"""
from __future__ import annotations

import copy
import numpy as np
from scipy.optimize import least_squares
import torch

LIMBS = (
    ('left_upper_arm_surface_length', 16, 18),
    ('right_upper_arm_surface_length', 17, 19),
    ('left_forearm_surface_length', 18, 20),
    ('right_forearm_surface_length', 19, 21),
    ('left_thigh_surface_length', 1, 4),
    ('right_thigh_surface_length', 2, 5),
    ('left_shank_surface_length', 4, 7),
    ('right_shank_surface_length', 5, 8),
)


def fit_subject_shape(model, surface):
    lookup = {m['measurement_id']: m for m in surface['measurements']}
    targets, sigma, evidence = [], [], []
    for key, _, _ in LIMBS:
        obs = [o['value_mm']/1000 for o in lookup[key]['observations'] if 'value_mm' in o]
        if not obs:
            raise ValueError(f'missing side-labelled measurement: {key}')
        targets.append(float(np.mean(obs)))
        sigma.append(max(.020, float(np.std(obs))))
        evidence.append(dict(measurement_id=key, readings_m=obs,
            target_m=targets[-1], mapping_sigma_m=sigma[-1]))
    targets, sigma = np.asarray(targets), np.asarray(sigma)
    # SMPL's shape blend is affine. Evaluate its joint basis once; all fitting
    # then uses that same upstream geometry instead of a replacement skeleton.
    with torch.no_grad():
        j0, _ = model.get_zero_pose_joint_and_vertex(torch.zeros(1, 10))
        jb, _ = model.get_zero_pose_joint_and_vertex(torch.eye(10))
    j0 = j0[0].cpu().numpy()
    basis = jb.cpu().numpy() - j0

    def lengths(beta):
        j = j0 + np.einsum('b,bjc->jc', beta, basis)
        return np.asarray([np.linalg.norm(j[b]-j[a]) for _, a, b in LIMBS])

    def residual(beta):
        return np.r_[(lengths(beta)-targets)/sigma, beta]

    fit = least_squares(residual, np.zeros(10), bounds=(-3., 3.), max_nfev=100)
    if not fit.success or not np.isfinite(fit.x).all():
        raise ValueError('subject shape fit failed')
    with torch.no_grad():
        joints, vertices = model.get_zero_pose_joint_and_vertex(torch.tensor(fit.x, dtype=torch.float32)[None])
    subject = copy.copy(model)
    subject._J = joints[0].clone()
    subject._v_template = vertices[0].clone()
    report = dict(status='SURFACE_TO_JOINT_PROXY_DIAGNOSTIC_NOT_ANATOMICALLY_QUALIFIED',
        method='upstream SMPL shape blend; uncertainty-weighted limb-length fit with beta prior',
        beta=fit.x.tolist(), observations=evidence,
        default_joint_lengths_m=lengths(np.zeros(10)).tolist(),
        fitted_joint_lengths_m=lengths(fit.x).tolist(),
        residual_mm=((lengths(fit.x)-targets)*1000).tolist(),
        excluded_measurements='unassigned forearm range, external pelvic/shoulder breadths and sensor distances are not joint-centre constraints',
        applications=['subject forward kinematics'],
        neural_orientation_network_conditioned_on_shape=False)
    return subject, report


def display_joints(global_rotation, model):
    """Map the upstream 24-joint skeleton to the established 13-point viewer."""
    # Root + L shoulder/elbow/wrist + R shoulder/elbow/wrist + hips/knees/ankles.
    local = model.inverse_kinematics_R(global_rotation.reshape(-1, 24, 3, 3))
    _, joints = model.forward_kinematics(local)
    indices = [0, 16, 18, 20, 17, 19, 21, 1, 4, 7, 2, 5, 8]
    points = joints[:, indices].detach().cpu().numpy()
    return points


def bend_angles(points):
    output = []
    for a, b, c in ((1, 2, 3), (4, 5, 6), (7, 8, 9), (10, 11, 12)):
        u, v = points[:, b]-points[:, a], points[:, c]-points[:, b]
        cosine = np.sum(u*v, axis=-1) / (np.linalg.norm(u, axis=-1)*np.linalg.norm(v, axis=-1))
        output.append(np.rad2deg(np.arccos(np.clip(cosine, -1, 1))))
    return np.stack(output, axis=-1)
