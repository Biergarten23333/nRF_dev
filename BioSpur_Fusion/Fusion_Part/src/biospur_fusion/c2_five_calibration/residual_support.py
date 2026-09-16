"""Structural pose-frame envelopes for the live residual export.

Envelopes address full per-frame pose coordinates, not spline controls. All
shared calibration coordinates must remain retained separately. Fixed learned
inputs are not counted as free pose variables. The long stencil also owns the
validity mask for the shorter acceleration scale, so both conservatively use
WIDTH frames. No dependency is inferred from numerical derivatives.
"""
from dataclasses import dataclass
import numpy as np
from .operators import WIDTH

SINGLE_FRAME = frozenset((
    'tracking_position', 'angular_prior', 'projection_gap', 'orientation',
    'body_inner', 'body_outer', 'body_swing', 'body_twist', 'body_posterior',
    'forearm_axial', 'conditioned_arm', 'conditioned_parent'))
PAIR = frozenset(('pose_smoothness', 'orientation_smoothness'))
WINDOW = frozenset(('acceleration', 'tracking_velocity'))


@dataclass(frozen=True)
class RowSupport:
    start: np.ndarray
    stop: np.ndarray

    def owned_by(self, start, stop):
        """Assign each row once, to the chunk containing its last support frame."""
        if start < 0 or stop <= start:
            raise ValueError('ordered chunk bounds required')
        return (self.stop > start) & (self.stop <= stop)

    def touches_history(self, retained_start):
        return self.start < retained_start


def pose_row_support(blocks, valid, *, frame_offset=0):
    """Validate each exported pose block and expand support in flatten order."""
    valid=np.asarray(valid)
    if valid.ndim!=1 or valid.dtype!=bool or len(valid)<WIDTH or frame_offset<0:
        raise ValueError('original boolean validity tape and nonnegative offset required')
    singles=np.flatnonzero(valid)
    pairs=np.flatnonzero(valid[:-1]&valid[1:])
    windows=np.flatnonzero(np.convolve(valid.astype(int),np.ones(WIDTH,dtype=int),'valid')==WIDTH)
    output={}
    for name,value in blocks.items():
        if name in SINGLE_FRAME:
            first,width=singles,1
        elif name in PAIR:
            first,width=pairs,2
        elif name in WINDOW:
            first,width=windows,WIDTH
        else:
            raise ValueError(f'undeclared pose residual support: {name}')
        shape=tuple(value.shape)
        if not shape or shape[0]!=len(first):
            raise ValueError(f'residual leading dimension disagrees with support: {name}')
        repeat=int(np.prod(shape[1:],dtype=int))
        begin=np.repeat(first+frame_offset,repeat)
        output[name]=RowSupport(begin,begin+width)
    return output


def calibration_row_support(blocks, prior, *, bend_protocol=None, heading_model=None):
    """Registered protocol rows use original tape indices; shared-only is None.

    Shared-only factors must be added once with their calibration columns;
    they must not be re-added on every pose window. This accepts a full export
    of the registered owners, not an arbitrary selected action subset.
    """
    declared={f'heading/{i}':None for i in range(len(prior.rows))}
    declared['sensor_levers']=None
    if heading_model is not None and hasattr(heading_model,'regularization'):
        declared['heading_rate']=None
    arm=getattr(prior,'arm_protocol',None)
    for row in (() if arm is None else arm.rows):
        if len(row.index):
            declared[f'arm/{row.factor_key}/{row.limb}']=np.asarray(row.index)
    for row in (() if bend_protocol is None else bend_protocol.rows):
        declared[f'bend/{row.action}/{row.limb}']=np.asarray(row.index)
    if set(blocks)!=set(declared):
        raise ValueError('export must contain exactly the registered calibration blocks')
    result={}
    for name,index in declared.items():
        if index is None:
            result[name]=None
            continue
        if (index.ndim!=1 or not np.issubdtype(index.dtype,np.integer)
                or np.any(index<0) or np.any(np.diff(index)<=0)
                or tuple(blocks[name].shape)!=index.shape):
            raise ValueError(f'protocol residual changed original time support: {name}')
        result[name]=RowSupport(index.copy(),index+1)
    return result
