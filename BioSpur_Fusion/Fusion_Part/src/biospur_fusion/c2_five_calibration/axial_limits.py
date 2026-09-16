"""Explicit experimental forearm rotation prior in the inverse hinge frame.

ISB separates flexion from radioulnar rotation. The independently tested
canonical forward model establishes inverse TWIST = -forward axial rotation.
The bound here is an engineering envelope, not a measured personal ROM.
"""
import numpy as np
import torch


def axial_terms(parameters, valid, geometry, *, residual_blocks=None):
    spec = geometry.get('forearm_axial_envelope')
    zero = parameters.sum()*0.
    if spec is None:
        return dict(axial_loss=zero, axial_excess_rad=zero)
    limit, scale, weight = (spec[k] for k in ('limit_deg', 'scale_deg', 'weight'))
    if not np.isfinite([limit, scale, weight]).all() or not 0 < limit < 180 or scale <= 0 or weight < 0:
        raise ValueError('invalid forearm axial envelope')
    # Equivalent 2*pi representations must carry identical anatomical cost.
    twist = parameters[:, 7:9]
    principal = torch.atan2(torch.sin(twist), torch.cos(twist))
    excess = torch.relu(principal[valid].abs()-np.deg2rad(limit))
    from .residual_blocks import record_mean
    record_mean(residual_blocks, "forearm_axial", excess/np.deg2rad(scale), weight)
    return dict(axial_loss=weight*(excess/np.deg2rad(scale)).square().mean(),
                axial_excess_rad=excess.max())
