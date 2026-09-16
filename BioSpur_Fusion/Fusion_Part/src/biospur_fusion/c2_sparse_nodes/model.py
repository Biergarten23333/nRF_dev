"""Pure orientation IK and measured-length FK, with explicit unobservability.

Measured pelvis/distal orientations are hard observations. A hinge chain still
has one unobserved flexion parameter; forearm twist adds two and torso three. Nine instantaneous
parameters are selected by priors. Bone lengths do not change that rank.
"""
from __future__ import annotations

import numpy as np
from scipy.optimize import least_squares
from scipy.spatial.transform import Rotation

JOINTS = ('pelvis', 'shoulder_left', 'elbow_left', 'wrist_left',
          'shoulder_right', 'elbow_right', 'wrist_right',
          'hip_left', 'knee_left', 'ankle_left', 'hip_right', 'knee_right', 'ankle_right')
IK_CONFIG = dict(nominal_flexion_deg=[15.,15.,5.,5.], shoulder_sigma_rad=1.5,
    hip_sigma_rad=.9, torso_sigma_rad=.6, flexion_sigma_rad=1.3,
    forearm_twist_sigma_rad=1.5, temporal_sigma_rad=.4,
    torso_component_bounds_rad=[1.2,1.2,1.5], flexion_upper_deg=155.,
    forearm_twist_bound_rad=2.)


def rotation_vectors(vectors):
    vectors = np.asarray(vectors)
    return Rotation.from_rotvec(vectors.reshape(-1,3)).as_matrix().reshape(vectors.shape[:-1]+(3,3))


def orientations(x, retained, axes):
    """Shared kinematic owner; accepts one frame or arbitrary leading batches."""
    torso = retained[...,0,:,:] @ rotation_vectors(x[...,:3])
    unspun = retained[...,1:,:,:].copy()
    twist = np.zeros(x.shape[:-1]+(2,3)); twist[...,2] = -x[...,7:9]
    unspun[...,:2,:,:] = unspun[...,:2,:,:] @ rotation_vectors(twist)
    proximal = unspun @ rotation_vectors(-x[...,3:7,None]*axes)
    return torso, proximal


def fk(x, retained, axes, lengths, torso_height, hip_half_width):
    torso, proximal = orientations(x, retained, axes)
    joints = [np.zeros(3)]
    for k in range(4):
        side, arm = (1 if k % 2 == 0 else -1), k < 2
        base = (torso @ np.array([0, side * lengths['shoulder_width'] / 2, torso_height])
                if arm else retained[0] @ np.array([0, side * hip_half_width, 0]))
        upper = lengths['upper_arm'] if arm else lengths['thigh']
        lower = lengths['forearm'] if arm else lengths['shank']
        mid = base - upper * proximal[k, :, 2]
        tip = mid - lower * retained[k + 1, :, 2]
        joints.extend((base, mid, tip))
    return np.asarray(joints)


def fit_frame(retained, axes, previous=None, prior_scale=1., max_nfev=35):
    """No action label, UWB input, old pose, or measured length enters attitude IK."""
    cfg = IK_CONFIG
    nominal = np.deg2rad(cfg['nominal_flexion_deg'])
    neutral_state = np.r_[np.zeros(3), nominal, np.zeros(2)]
    x0 = neutral_state.copy() if previous is None else previous.copy()

    def residual(x):
        torso, prox = orientations(x, retained, axes)
        arm = Rotation.from_matrix(torso.T @ prox[:2]).as_rotvec().ravel() / cfg['shoulder_sigma_rad']
        hip = Rotation.from_matrix(retained[0].T @ prox[2:]).as_rotvec().ravel() / cfg['hip_sigma_rad']
        neutral = np.r_[x[:3] / cfg['torso_sigma_rad'],
                        (x[3:7]-nominal) / cfg['flexion_sigma_rad'],
                        x[7:9] / cfg['forearm_twist_sigma_rad']] / prior_scale
        temporal = (x - (x0 if previous is not None else neutral_state)) / cfg['temporal_sigma_rad']
        return np.r_[arm, hip, neutral, temporal]

    result = least_squares(residual, x0, bounds=(
        np.r_[-np.array(cfg['torso_component_bounds_rad']), np.zeros(4),
              -np.full(2,cfg['forearm_twist_bound_rad'])],
        np.r_[cfg['torso_component_bounds_rad'], np.full(4,np.deg2rad(cfg['flexion_upper_deg'])),
              np.full(2,cfg['forearm_twist_bound_rad'])]),
        max_nfev=max_nfev, ftol=1e-6, xtol=1e-6, gtol=1e-6)
    torso, prox = orientations(result.x, retained, axes)
    bends = np.rad2deg(np.arccos(np.clip(np.sum(prox[:, :, 2] * retained[1:, :, 2], axis=1), -1, 1)))
    return result.x, dict(success=bool(result.success), nfev=result.nfev,
        bend_deg=bends, conditional_hinge_deg=np.rad2deg(result.x[3:7]),
        instantaneous_unobserved_dof=9, measurement_rank_over_latent_parameters=0,
        torso_relative_pelvis_deg=float(np.rad2deg(np.linalg.norm(result.x[:3]))))
