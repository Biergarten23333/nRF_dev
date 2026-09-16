"""Slice a fixed C2 pose objective without refitting its seed or target."""
import copy
import torch
from .operators import WIDTH
from .solver import PoseObjective, valid_support
from .soft_observation import SoftObservationObjective


def _pose_window(objective, start, stop, *, reproject):
    """Keep whole-tape unwrap/targets; returned scalar loss is still local.

    Use ResidualBlocks(global_mean_counts=...) for streamed energy. This is
    a fixed-projection window, not a fresh calibration or neural inference.
    Model/body/asset owners are shared; mutable seed/target tensors are cloned.
    """
    if type(objective) not in (PoseObjective, SoftObservationObjective):
        raise TypeError('explicit pose objective required')
    if isinstance(objective, SoftObservationObjective) and objective.protocol is not None:
        raise ValueError('slice registered protocol rows separately; callback scope is unknown')
    source=getattr(objective,'base',objective)
    if (isinstance(start,bool) or isinstance(stop,bool)
            or not isinstance(start,int) or not isinstance(stop,int)
            or start<0 or stop>len(source.prior) or stop-start<WIDTH):
        raise ValueError('window must lie on the original tape and cover the derivative stencil')
    part=copy.copy(source)
    for name in ('prior','observed','acceleration','valid'):
        setattr(part,name,getattr(source,name)[start:stop])
    part.initial=source.initial[start:stop].clone()
    part.target=source.target[start:stop].clone()
    part.valid_pairs=part.valid[1:]&part.valid[:-1]
    part.good=torch.as_tensor(valid_support(part.valid.cpu().numpy()),device=part.valid.device)
    if not part.good.any() or not part.valid_pairs.any():
        raise ValueError('window needs valid derivative and adjacent-pair support')
    part.tracking=copy.copy(source.tracking)
    part.tracking.positions=source.tracking.positions[start:stop].clone()
    part._frozen_window=not reproject
    part.seed_audit=dict(source='whole-tape seed slice; no per-window unwrap',
                        start=start,stop=stop)
    if isinstance(objective,SoftObservationObjective):
        result=SoftObservationObjective(part,freeze_observation=objective.freeze_observation)
        result.initial=objective.initial[start:stop].clone()
        return result
    return part


def frozen_pose_window(objective, start, stop):
    """Preserve the original seed/target and reject local reprojection."""
    return _pose_window(objective,start,stop,reproject=False)


def reprojected_pose_window(objective, start, stop):
    """Explicit local-surrogate projection window.

    Caller must check owned residuals against the whole-tape evaluation at
    the fixed shared point; unwrap branches are not assumed globally equal.
    Original pose coordinates and global mean counts must remain unchanged.
    """
    return _pose_window(objective,start,stop,reproject=True)
