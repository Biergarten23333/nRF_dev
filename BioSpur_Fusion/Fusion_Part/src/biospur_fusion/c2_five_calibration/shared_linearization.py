"""Pose-factor columns for temporal heading and lever calibration.

Differentiates the existing refreshed geometric projection, conditional on a
fixed neural prior and unwrap branch. Does not include registered action or
calibration-prior rows; those remain separate owners. Raw temporal replay and
installation/bias uncertainty are not supplied by this local surrogate.
"""
import numpy as np
from scipy.sparse import csr_matrix
import torch
from .shared_orientation import transport_heading
from .residual_blocks import ResidualBlocks
from .residual_support import pose_row_support
from .temporal_linearization import linearize_temporal_residual
from .solver import lever_system


def shared_pose_jacobians(objective, parameters, coefficients, levers, basis, *,
                          mean_counts=None, projection_gap=False, deadline=None):
    """Return pose, heading-coefficient, lever CSR matrices in one row order.

    Basis is sampled on this original time grid, shape [frames,4,coefficients].
    Shared columns must be retained jointly when eliminating historical poses.
    """
    base=getattr(objective,'base',objective);n=len(parameters);width=parameters.shape[1]
    basis=np.asarray(basis,float)
    if (basis.shape!=(n,4,len(coefficients)) or not np.isfinite(basis).all()
            or coefficients.ndim!=1 or width!=getattr(objective,'parameter_count',9)):
        raise ValueError('heading basis and full-frame pose coordinates must agree')
    sampled=torch.as_tensor(basis,dtype=parameters.dtype,device=parameters.device)@coefficients
    point=torch.cat((parameters,sampled),1)
    def evaluate(x):
        observed,acceleration=transport_heading(objective.observed,objective.acceleration,x[:,width:])
        blocks=ResidualBlocks(global_mean_counts=mean_counts)
        rotation,_=objective.evaluate(x[:,:width],levers,observed=observed,acceleration=acceleration,
                                      refresh_projection=True,projection_gap=projection_gap,residual_blocks=blocks)
        return rotation,acceleration,blocks
    rotation,acceleration,blocks=evaluate(point);names=sorted(blocks)
    support=pose_row_support(blocks,base.valid.cpu().numpy())
    starts=np.concatenate([support[k].start for k in names]);stops=np.concatenate([support[k].stop for k in names])
    def residual(x):
        _,_,b=evaluate(x);return torch.cat([b[k].flatten() for k in names])
    J,r,groups=linearize_temporal_residual(residual,point,starts,stops,deadline=deadline)
    columns=np.arange(n*(width+4)).reshape(n,width+4)
    pose=J[:,columns[:,:width].flatten()]
    heading=J[:,columns[:,width:].flatten()]@csr_matrix(basis.reshape(n*4,-1))
    A,_=lever_system(rotation.detach().cpu().numpy(),acceleration.detach().cpu().numpy(),
                      base.valid.cpu().numpy(),base.geometry)
    lever_blocks=[];slices={};offset=0
    for name in names:
        count=blocks[name].numel();slices[name]=slice(offset,offset+count);offset+=count
        if name=='acceleration':
            denominator=count if mean_counts is None else mean_counts[name]
            lever_blocks.append(csr_matrix(A/.75/np.sqrt(denominator)))
        else:
            lever_blocks.append(csr_matrix((count,15)))
    from scipy.sparse import vstack
    return dict(pose=pose,heading=heading,levers=vstack(lever_blocks,format='csr'),
                residual=r,blocks=slices,groups=groups,start=starts,stop=stops)
