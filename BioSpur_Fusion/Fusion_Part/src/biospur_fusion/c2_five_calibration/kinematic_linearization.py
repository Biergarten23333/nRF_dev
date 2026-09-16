"""Chain-rule Jacobians for fixed-projection temporal pose residuals.

Differentiate per-frame FK once, then apply the production linear stencils.
Shared calibration derivatives are separate; measured acceleration is held fixed.
"""
import numpy as np
from scipy.sparse import coo_matrix
import torch
from .geometry import joints_from_global, sensor_positions
from .operators import WIDTH, ACCELERATION_WIDTHS, position_coefficients
from .acceleration_metric import metric_residual
from .tracking import VELOCITY_TIME_SCALE_S
from .soft_observation import observation_rotation
from .temporal_linearization import linearize_temporal_residual


def stencil_matrix(frame_count, components, good, widths, derivative):
    """Same valid centres/scale-major-within-frame order as multiscale."""
    good=np.asarray(good)
    if good.dtype!=bool or good.shape!=(frame_count-WIDTH+1,):
        raise ValueError('original long-stencil support mask required')
    starts=np.flatnonzero(good);rows=[];cols=[];values=[]
    for scale,width in enumerate(widths):
        coefficients=position_coefficients(derivative,width)
        trim=(WIDTH-width)//2
        if width>WIDTH or (WIDTH-width)%2:raise ValueError('unaligned stencil width')
        for k,c in enumerate(coefficients):
            rows.append(((np.arange(len(starts))[:,None]*len(widths)+scale)*components+np.arange(components)).reshape(-1))
            cols.append(((starts[:,None]+trim+k)*components+np.arange(components)).reshape(-1))
            values.append(np.full(len(starts)*components,c))
    return coo_matrix((np.concatenate(values),(np.concatenate(rows),np.concatenate(cols))),
                      shape=(len(starts)*len(widths)*components,frame_count*components)).tocsr()


def temporal_pose_jacobians(objective, parameters, levers, *, mean_counts=None, deadline=None):
    """Return velocity/acceleration pose Jacobians; no new residual definition.

    `mean_counts` are the whole-tape record_mean denominators for windows.
    The caller retains all other residual blocks and calibration columns.
    """
    base=getattr(objective,'base',objective);n=len(base.prior)
    soft=hasattr(objective,'freeze_observation')
    if parameters.shape!=(n,21 if soft else 9):raise ValueError('full-frame pose coordinates required')
    def primitives(p):
        measured=base.observed
        if soft:
            correction=p[:,9:].reshape(-1,4,3)
            if objective.freeze_observation:correction=correction*0.
            measured=observation_rotation(measured,correction)
        r=base.model.rotation(base.prior,measured,p[:,:9])
        joints=joints_from_global(r,base.geometry)[:,1:]
        track=joints*VELOCITY_TIME_SCALE_S/base.tracking.scale[None,:,None]
        sensors=sensor_positions(r,base.geometry,levers)
        relative=metric_residual(sensors[:,1:]-sensors[:,:1],base.geometry,node_axis=1)/.75
        return torch.cat((track.reshape(n,69),relative.reshape(n,12)),1).flatten()
    start=np.repeat(np.arange(n),81)
    J,_,groups=linearize_temporal_residual(primitives,parameters,start,start+1,deadline=deadline)
    track_rows=(np.arange(n)[:,None]*81+np.arange(69)).reshape(-1)
    sensor_rows=(np.arange(n)[:,None]*81+np.arange(69,81)).reshape(-1)
    good=base.good.cpu().numpy();result={}
    for name,components,widths,derivative,rows,weight in (
        ('tracking_velocity',69,(WIDTH,),1,track_rows,.15),
        ('acceleration',12,ACCELERATION_WIDTHS,2,sensor_rows,1.)):
        operator=stencil_matrix(n,components,good,widths,derivative)
        count=operator.shape[0] if mean_counts is None else mean_counts[name]
        if isinstance(count,bool) or not isinstance(count,int) or count<operator.shape[0]:
            raise ValueError('whole-tape mean must cover all local residuals')
        result[name]=(operator@J[rows])*np.sqrt(weight/count)
    return result,groups


def pose_residual_jacobians(objective, parameters, levers, *, mean_counts=None, deadline=None):
    """All pose blocks at fixed calibration/projection, in owner row order.

    Returns block CSR matrices and weighted residuals. It does not assemble
    shared parameter derivatives or silently treat them as known precisely.
    """
    from .residual_blocks import ResidualBlocks
    from .residual_support import pose_row_support
    blocks=ResidualBlocks(global_mean_counts=mean_counts)
    objective.evaluate(parameters,levers,residual_blocks=blocks)
    support=pose_row_support(blocks,objective.valid.cpu().numpy())
    result,_=temporal_pose_jacobians(objective,parameters,levers,
                                    mean_counts=mean_counts,deadline=deadline)
    names=sorted(set(blocks)-set(result))
    starts=np.concatenate([support[k].start for k in names])
    stops=np.concatenate([support[k].stop for k in names])
    def remaining(p):
        terms=ResidualBlocks(global_mean_counts=mean_counts)
        objective.evaluate(p,levers,residual_blocks=terms)
        return torch.cat([terms[k].flatten() for k in names])
    matrix,_,_=linearize_temporal_residual(remaining,parameters,starts,stops,deadline=deadline)
    offset=0
    for name in names:
        size=blocks[name].numel();result[name]=matrix[offset:offset+size];offset+=size
    return result,{k:v.detach().cpu().numpy().reshape(-1) for k,v in blocks.items()}
