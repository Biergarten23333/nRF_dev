"""Shared calibration coordinates, separate from per-frame pose parameters.

Four limb yaw increments change the retained sensors' world frames. The
transport also accepts a sampled temporal proposal on the same time grid.
They are calibration proposals, not measured motion or independently observed
heading. Pelvis fixes the common yaw gauge. Candidate fitting must regenerate
the continuous neural prior before accepting a proposal.
"""
import copy

import numpy as np
import torch

from biospur_fusion.c2_sparse_nodes.inputs import NODES


def transport_heading(observed, acceleration, delta_rad):
    """Left rotation about SMPL world Y; temporal post-filter use is a surrogate.

    A temporal candidate must be replayed at raw timestamps before filtering
    and interpolation for acceptance; see temporal_transport_check.
    """
    if (observed.ndim != 4 or observed.shape[1:] != (5, 3, 3)
            or acceleration.shape != observed.shape[:2] + (3,)
            or delta_rad.shape not in ((4,), (len(observed),4))):
        raise ValueError('shared heading requires four constant or T x four increments')
    if any(x.dtype != observed.dtype or x.device != observed.device
           for x in (acceleration, delta_rad)):
        raise ValueError('shared heading tensors must use the same dtype and device')
    if not all(torch.isfinite(x).all() for x in (observed, acceleration, delta_rad)):
        raise ValueError('nonfinite shared heading input')
    angles = torch.cat((delta_rad.new_zeros(delta_rad.shape[:-1]+(1,)), delta_rad),dim=-1)
    c, s = torch.cos(angles), torch.sin(angles)
    zero, one = torch.zeros_like(angles), torch.ones_like(angles)
    rotation = torch.stack((c, zero, s, zero, one, zero, -s, zero, c), -1).reshape(angles.shape+(3,3))
    return rotation @ observed, (rotation @ acceleration[..., None]).squeeze(-1)


def with_heading_increment(calibration, delta_rad):
    """Build a fresh frontend for replay; never mutate a cached calibration.

    BioSpur world Z maps to SMPL world Y, so the same signed increment is
    applied to the frontend's frozen yaw. Existing raw heading factors remain
    evidence; they must not also be counted through a second posterior prior.
    """
    if 'shared_orientation_proposal' in calibration:
        raise ValueError('total heading increments require the original baseline frontend')
    delta = np.asarray(delta_rad, dtype=float)
    frozen = calibration.get('frozen_heading_correction_rad', {})
    if delta.shape != (4,) or not np.isfinite(delta).all():
        raise ValueError('four finite constant heading increments required')
    if set(frozen) != set(NODES[1:]) or not np.isfinite(list(frozen.values())).all():
        raise ValueError('shared heading requires the complete frozen five-node frontend')
    result = copy.deepcopy(calibration)
    result['frozen_heading_correction_rad'] = {
        node: float(frozen[node] + delta[i]) for i, node in enumerate(NODES[1:])
    }
    # Temporal replay reads curve ordinates instead of the scalar fallback.
    # A joint calibration increment must reach both representations exactly
    # once; otherwise the optimizer and fresh sensor replay disagree.
    curves=result.get('temporal_heading_curves',{})
    for i,node in enumerate(NODES[1:]):
        if node in curves:
            values=np.asarray(curves[node]['correction_rad'],dtype=float)
            if values.ndim!=1 or not np.isfinite(values).all():
                raise ValueError('invalid baseline temporal heading values')
            curves[node]['correction_rad']=(values+delta[i]).tolist()
    result['shared_orientation_proposal'] = dict(
        increment_rad=delta.tolist(), pelvis_gauge_increment_rad=0.,
        status='PROPOSED_REQUIRES_CONTINUOUS_PRIOR_REPLAY',
        observation_status='prior-conditioned calibration parameter; not measured heading',
        time_varying_correction=False)
    result['calibration_accepted'] = False
    return result
