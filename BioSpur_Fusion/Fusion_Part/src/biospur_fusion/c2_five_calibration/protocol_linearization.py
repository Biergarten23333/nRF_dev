"""Registered action-factor derivatives; phase information is never rescaled."""
import copy
from dataclasses import replace
import numpy as np
from scipy.sparse import csr_matrix
import torch
from .arm_protocol import ArmProtocolTape
from .protocol_pose import BendProtocol
from .shared_orientation import transport_heading
from .soft_observation import observation_rotation
from .temporal_linearization import linearize_temporal_residual


def protocol_window(prior, bend, start, stop):
    """Select original protocol rows and shift indices onto a local pose tape.

    Shared scalar heading factors/basis stay global and must not be evaluated
    on the shortened delta grid. Returned prior is for energy_for_action only.
    """
    if not isinstance(start,int) or not isinstance(stop,int) or start<0 or stop<=start:
        raise ValueError('ordered original frame bounds required')
    local=copy.copy(prior)
    if prior.arm_protocol is not None:
        rows=[]
        for row in prior.arm_protocol.rows:
            mask=(row.index>=start)&(row.index<stop)
            rows.append(replace(row,index=row.index[mask]-start,direction=row.direction[mask],information=row.information[mask]))
        local.arm_protocol=ArmProtocolTape(rows,prior.arm_protocol.baseline,spatial_axes=prior.arm_protocol.spatial_axes)
    if bend is not None:
        rows=[]
        for row in bend.rows:
            mask=(row.index>=start)&(row.index<stop)
            rows.append(replace(row,index=row.index[mask]-start,weights=row.weights[mask]))
        bend=BendProtocol(rows,bend.source_sha256,sigma_deg=np.rad2deg(bend.sigma_rad))
    return local,bend


def action_factor_jacobians(objective, prior, bend, parameters, coefficients, basis, delta, *, deadline=None):
    """CSR pose/heading columns for arm and bend rows on this exact pose grid."""
    base=getattr(objective,'base',objective);n,width=parameters.shape
    basis=np.asarray(basis,float)
    if basis.shape!=(n,4,len(coefficients)) or not np.isfinite(basis).all():
        raise ValueError('heading basis must match the pose tape')
    sampled=torch.as_tensor(basis,dtype=parameters.dtype)@coefficients
    point=torch.cat((parameters,sampled),1)
    indices={}
    if prior.arm_protocol is not None:
        for row in prior.arm_protocol.rows:
            if len(row.index):indices[f'arm/{row.factor_key}/{row.limb}']=row.index
    if bend is not None:
        for row in bend.rows:indices[f'bend/{row.action}/{row.limb}']=row.index
    for idx in indices.values():
        if np.any(idx<0) or np.any(idx>=n):raise ValueError('action support outside pose tape')
    names=sorted(indices);starts=np.concatenate([indices[k] for k in names]) if names else np.empty(0,dtype=int)
    actions=sorted({a for rows in prior.factor_actions.values() for a in rows.values()} | (set() if bend is None else bend.actions))
    def residual(x):
        p=x[:,:width];inc=x[:,width:]
        observed,_=transport_heading(objective.observed,objective.acceleration,inc)
        if width==21:
            correction=p[:,9:].reshape(-1,4,3)
            if objective.freeze_observation:correction=correction*0.
            observed=observation_rotation(observed,correction)
        rotation=base.model.rotation(base.prior,observed,p[:,:9]);blocks={}
        for action in actions:
            prior.energy_for_action(action,delta+inc,rotation,residual_blocks=blocks)
            if bend is not None:bend.energy_for_action(action,p,residual_blocks=blocks)
        return torch.cat([blocks[k].flatten() for k in names])
    if not len(starts):
        return dict(pose=csr_matrix((0,n*width)),heading=csr_matrix((0,len(coefficients))),residual=np.empty(0),start=starts,names=names)
    J,r,_=linearize_temporal_residual(residual,point,starts,starts+1,deadline=deadline)
    columns=np.arange(point.numel()).reshape(n,width+4)
    return dict(pose=J[:,columns[:,:width].flatten()],
                heading=J[:,columns[:,width:].flatten()]@csr_matrix(basis.reshape(n*4,-1)),
                residual=r,start=starts,names=names)
