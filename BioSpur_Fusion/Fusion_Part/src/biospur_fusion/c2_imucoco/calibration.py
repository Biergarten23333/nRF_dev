"""Five-node parameter calibration and per-action evidence for IMUCoCo."""
from __future__ import annotations

import numpy as np
from scipy.spatial.transform import Rotation

from biospur_fusion.c2_sparse_nodes.inputs import NODES
from biospur_fusion.c2_sparse_nodes.calibration import calibrate, matrices, relative
from .protocol import implemented_ledger, MISSING_COMPONENTS
from .diagnostics import axis_repeatability


def fit_calibration(episodes, surface):
    if any(name.startswith('H') for name in episodes):
        raise ValueError('H-series data cannot enter calibration')
    for episode in episodes.values():
        if set(episode) != set(NODES):
            raise ValueError('calibration payload must contain only the five retained nodes')
    c = calibrate(episodes, surface)
    # These were inherited from the old pose solver but are not consumed by
    # IMUCoCo. In particular, a conditional standing elbow estimate is not a
    # calibrated initial state supplied to the published pose network.
    unused = {key: c.pop(key) for key in (
        'standing_elbow_bend_estimate_deg', 'initial_pose_assumption',
        'lengths', 'hinge_axes', 'hinge_audit',
        'torso_display_models_m', 'hip_display_half_width_models_m') if key in c}
    c['unused_legacy_diagnostics'] = unused
    bias, tpose = [], []
    g = np.array([0., 0., 9.80665])
    for n in NODES:
        rows = episodes['00_initial_still'][n]['imu']
        expected = np.einsum('nji,j->ni', matrices(rows).as_matrix(), g)
        bias.append(np.median(rows[:, 5:8]-expected, axis=0).tolist())
        rr = relative(episodes['02_t_pose'][n]['imu'], n, c)
        tpose.append(Rotation.from_matrix(rr).mean().as_matrix().tolist())
    c.update(acc_bias_sensor=bias, measured_tpose_segments=tpose,
        calibration_status='INCOMPLETE', calibration_accepted=False,
        missing_calibration_components=MISSING_COMPONENTS.copy(),
        reconstruction='published IMUCoCo encoder + published DTP pose weights',
        generic_missing_segment_pose_solver_used=False,
        input_rate_hz=60, acceleration_units='m/s^2, gravity removed',
        input_orientation_representation='SMPL global segment rotation, first two columns',
        calibration_scope='offline same-session partial IMU frontend fit; candidate only, not accepted five-node calibration',
        sensor_placement_status='published wrist and above-ankle vertex proxies; offsets not measured',
        surface_to_joint_mapping_status='uncertainty-weighted proxy diagnostic only')
    # Old calibration metadata described the superseded pose optimizer. Keep
    # only assumptions belonging to the five-node orientation frontend.
    c['assumptions'] = [
        'T-pose defines common forward/left and functional motion defines bone axes',
        'natural standing approximates pelvis/shank vertical, not forearm vertical',
        'elbow flexion and pronation are separate 0-15 and 15-30 second phases',
        'common pelvis yaw closure extrapolated from C2; offline, not causal',
        'upstream trained motion prior estimates missing torso and proximal orientations',
        'surface lengths are uncertain proxies, not measured internal joint-centre lengths',
    ]
    ledger = implemented_ledger(episodes, c)
    quality = {}
    for name, ep in episodes.items():
        if name.startswith('_'):
            continue
        quality[name] = {}
        for n in NODES:
            rows = ep[n]['imu']
            quality[name][n] = dict(samples=len(rows),
                duration_s=float(rows[-1, 0]-rows[0, 0]),
                gyro_rms_rad_s=float(np.sqrt(np.mean(rows[:, 8:11]**2))),
                gaps_over_25ms=int(np.sum(np.diff(rows[:, 0]) > .025)))
    return c, dict(functional_protocol=ledger, every_recorded_action_input_quality=quality,
                   axis_repeatability=axis_repeatability(episodes),
                   missing_action_policy='no synthetic replacement of an unacquired action')
