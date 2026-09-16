"""C2-only experimental personalization of the learned flexion prior.

Four positive gains use the explicitly approximate bend instructions. They
modify inferred proximal priors, never IMU observations, time, geometry, or
the released network weights. One noisy anchor per limb is weak evidence;
regularization and uncertainty remain explicit. This is not a yaw calibrator.
"""
import numpy as np
from scipy.optimize import least_squares
import torch

from .anatomy import JointModel, PROXIMAL, FLEXION
from .frontend import FIT

LOG_GAIN_SIGMA = .5


def fit_flexion_prior(actions, geometry, protocol):
    if len(actions) != 19 or {n[:2] for n in actions} != FIT:
        raise ValueError('all recorded C2 actions and no H are required')
    model = JointModel(geometry)
    gains = np.ones(4)
    records = []
    for row in protocol.rows:
        q = actions[row.action]
        if np.shape(q['observed']) != (len(q['prior']), 5, 3, 3):
            raise ValueError('exactly five observed rotations required')
        p = model.initial(torch.as_tensor(q['prior'], dtype=torch.float64),
                          torch.as_tensor(q['observed'], dtype=torch.float64))
        angles = p[row.index, 3+row.limb].numpy()
        root_weight = np.sqrt(row.weights)
        if len(angles) < 20 or row.weights.sum() < .5:
            raise ValueError('insufficient real support for personalized prior')

        def residual(log_gain):
            error = (np.exp(log_gain[0])*angles-np.pi/2)/protocol.sigma_rad
            return np.r_[root_weight*error, log_gain[0]/LOG_GAIN_SIGMA]

        result = least_squares(residual, [0.], max_nfev=50)
        if not result.success or not np.isfinite(result.x).all():
            raise ValueError('personalization fit failed')
        gain = float(np.exp(result.x[0])); gains[row.limb] = gain
        curvature = float((result.jac.T@result.jac)[0, 0])
        records.append(dict(action=row.action, limb=row.limb, gain=gain,
            prior_mean_deg=float(np.rad2deg(np.average(angles, weights=row.weights))),
            scaled_mean_deg=float(np.rad2deg(np.average(gain*angles, weights=row.weights))),
            regularized_local_log_gain_scale=float(1/np.sqrt(curvature)),
            uncertainty_is_independently_validated=False,
            supported_phase_weight=float(row.weights.sum())))
    return dict(status='FROZEN_EXPERIMENTAL_PRIOR_PENDING_H', flexion_gain=gains.tolist(),
        log_gain_prior_sigma=LOG_GAIN_SIGMA, phases=records, protocol=protocol.audit(),
        H_used_for_fit=False, ten_node_used=False, sensor_calibration_changed=False,
        weakness='one approximate bend anchor per limb; proportional generalization must be tested',
        all_recorded_action_names=sorted(actions))


def personalize_prior(prior, observed, geometry, calibration):
    p = torch.as_tensor(np.asarray(prior), dtype=torch.float64)
    obs = torch.as_tensor(np.asarray(observed), dtype=torch.float64)
    gain = torch.as_tensor(calibration['flexion_gain'], dtype=p.dtype)
    if (gain.shape != (4,) or not torch.isfinite(gain).all() or torch.any(gain <= 0)
            or p.shape != (len(obs),24,3,3) or obs.shape != (len(p),5,3,3)):
        raise ValueError('finite positive gains and aligned five-node prior required')
    model = JointModel(geometry)
    parameters = model.initial(p, obs)
    unbounded = parameters[:, FLEXION]*gain
    parameters[:, FLEXION] = torch.minimum(unbounded, model.maximum_bend)
    projected = model.rotation(p, obs, parameters)
    result = p.clone()
    # Preserve all other network outputs; the existing physical model remains
    # the sole owner of observed-orientation locking and shared-torso FK.
    result[:, PROXIMAL] = projected[:, PROXIMAL]
    return result.numpy(), dict(flexion_gain=gain.tolist(),
        clamped_frames=(unbounded > model.maximum_bend).sum(0).tolist(),
        total_frames=len(p), modified_bones=list(PROXIMAL),
        IMU_observations_changed=False, output_is_personalized_prior=True)
