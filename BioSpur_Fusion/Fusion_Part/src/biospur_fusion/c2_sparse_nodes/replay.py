"""Frozen pure-IMU replay; all unmeasured geometry stays in display sensitivity."""
from __future__ import annotations

import time
import numpy as np

from .calibration import synchronized
from .model import fit_frame, fk, orientations, JOINTS


def replay(ep, calibration, hz=20, limit=None, prior_scale=1.):
    started = time.monotonic()
    t, r, valid = synchronized(ep, calibration, hz)
    if limit is not None:
        t, r, valid = (a[:limit] for a in (t, r, valid))
    axes = np.asarray(calibration['hinge_axes'])
    states = np.full((len(t), 9), np.nan)
    joints = np.full((len(t), 3, 13, 3), np.nan)
    proximal = np.full((len(t), 4, 3, 3), np.nan)
    torsos = np.full((len(t), 3, 3), np.nan)
    bends = np.full((len(t), 4), np.nan)
    success = np.zeros(len(t), bool)
    previous = None
    for i in range(len(t)):
        if not valid[i]:
            previous = None
            continue
        x, info = fit_frame(r[i], axes, previous, prior_scale=prior_scale)
        states[i], bends[i], success[i] = x, info['bend_deg'], info['success']
        torsos[i], proximal[i] = orientations(x, r[i], axes)
        for m, (height, halfwidth) in enumerate(zip(calibration['torso_display_models_m'], calibration['hip_display_half_width_models_m'])):
            joints[i, m] = fk(x, r[i], axes, calibration['lengths'], height, halfwidth)
        previous = x if info['success'] else None
    report = dict(frames=len(t), solved_frames=int(valid.sum()),
        interpolation_gap_rejected_frames=int((~valid).sum()),
        optimizer_nonconverged_frames=int((valid & ~success).sum()),
        bend_p05_deg=np.nanquantile(bends, .05, axis=0).tolist(),
        bend_p95_deg=np.nanquantile(bends, .95, axis=0).tolist(),
        torso_relative_pelvis_p95_deg=float(np.rad2deg(np.nanquantile(np.linalg.norm(states[:, :3], axis=1), .95))),
        wall_s=time.monotonic() - started, claim='PRIOR_CONDITIONED_PURE_IMU_DIAGNOSTIC',
        latent_dof_without_informative_measurement=9,
        retained_orientation_residual_deg=0.,
        length_effect='FK geometry only; no extra orientation observability',
        joint_order=list(JOINTS))
    bone_error = []
    for k in range(4):
        a = 1 + 3 * k
        expected = [calibration['lengths'][z] for z in (('upper_arm', 'forearm') if k < 2 else ('thigh', 'shank'))]
        bone_error.extend(abs(np.linalg.norm(np.diff(joints[:, :, a:a+3], axis=2), axis=3) - expected).ravel())
    report['bone_closure_max_m'] = float(np.nanmax(bone_error))
    if report['bone_closure_max_m'] > 1e-8:
        raise RuntimeError('bone closure gate failed')
    arrays = dict(time_s=t, states=states, joints_m=joints, retained_rotations=r,
        torso_rotations=torsos, proximal_rotations=proximal, bend_deg=bends,
        optimizer_success=success, input_valid=valid)
    return arrays, report
