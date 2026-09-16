"""C2-only repeatability diagnostics, without changing fitted parameters.

A dominant raw gyro axis is not automatically a joint axis when proximal
segments move. These checks expose instability; they do not certify anatomy.
"""
import numpy as np

from biospur_fusion.c2_sparse_nodes.inputs import NODES


def _axis(rows, start, end):
    elapsed = rows[:, 0]-rows[0, 0]
    # Use the same excitation selection as the current functional estimator.
    good = (elapsed >= start) & (elapsed < end) & (np.linalg.norm(rows[:, 8:11], axis=1) > .3)
    good &= np.r_[False, np.diff(rows[:, 0]) <= .025]
    omega = rows[good, 8:11]
    if len(omega) < 100:
        return None, dict(status='INSUFFICIENT_EXCITATION', samples=len(omega))
    _, singular, axes = np.linalg.svd(omega, full_matrices=False)
    axis = axes[0]
    residual = omega-(omega@axis)[:, None]*axis
    return axis, dict(status='MEASURED_NOT_ACCEPTANCE', samples=len(omega),
        principal_energy_fraction=float(singular[0]**2/(singular@singular)),
        transverse_gyro_rms_rad_s=float(np.sqrt(np.mean(np.sum(residual**2, axis=1)))))


def axis_repeatability(episodes):
    if any(name.startswith('H') for name in episodes):
        raise ValueError('calibration diagnostics must not consume H-series')
    checks = []
    for name, node, phase, start, end in (
        ('06_elbow_left', NODES[1], 'flexion', 0., 15.),
        ('06_elbow_left', NODES[1], 'pronation', 15., 30.),
        ('07_elbow_right', NODES[2], 'flexion', 0., 15.),
        ('07_elbow_right', NODES[2], 'pronation', 15., 30.),
        ('10_knee_left_seated', NODES[3], 'seated_extension', 0., 30.),
        ('11_knee_right_seated', NODES[4], 'seated_extension', 0., 30.),
        ('14_trunk_flex_extend', NODES[0], 'pelvis_pitch', 0., 30.),
    ):
        mid = (start+end)/2
        first, a = _axis(episodes[name][node]['imu'], start, mid)
        second, b = _axis(episodes[name][node]['imu'], mid, end)
        angle = None if first is None or second is None else float(
            np.degrees(np.arccos(np.clip(abs(first@second), 0., 1.))))
        checks.append(dict(action=name, node=node, phase=phase,
            split_intervals_s=[[start, mid], [mid, end]], first=a, second=b,
            unsigned_axis_difference_deg=angle))
    return dict(status='DIAGNOSTIC_ONLY', checks=checks,
                parameter_refit=False, action_accuracy_acceptance=False,
                limitation='split-phase raw gyro repeatability; not an independent proximal/distal joint-axis measurement')
