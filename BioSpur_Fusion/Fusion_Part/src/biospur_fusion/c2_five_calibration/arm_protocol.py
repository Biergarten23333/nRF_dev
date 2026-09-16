"""Time-resolved arm protocol priors, never a measured thorax orientation.

Keep the original functional axes and total information. Only the reference
frame changes from the measured pelvis to the existing latent thorax. A tape
uses the solver's original grid; missing coverage loses information without
redistribution. This is a discretized anatomical prior, not sensor likelihood.
"""
from dataclasses import dataclass

import numpy as np
import torch
from scipy.spatial.transform import Rotation, Slerp

from biospur_fusion.c2_sparse_nodes.inputs import NODES
from biospur_fusion.c2_sparse_nodes.calibration import _gyro_axis, matrices
from biospur_fusion.c2_imucoco.preprocessing import WORLD_TO_SMPL
from .heading import _aligned
from .phase_contract import direction_target,phase_bounds,phase_key


def horizontal_residual(direction, target, multiple):
    """Correction-minus-target residual about SMPL up Y; lines are modulo pi."""
    h1=direction[..., [0,2]].square().sum(-1)
    h2=target[..., [0,2]].square().sum(-1)
    if torch.any(h1<1e-16) or torch.any(h2<1e-16):
        raise ValueError('undefined horizontal protocol direction')
    angle=torch.atan2(direction[...,2]*target[...,0]-direction[...,0]*target[...,2],
                     direction[...,0]*target[...,0]+direction[...,2]*target[...,2])
    return -torch.atan2(torch.sin(multiple*angle),torch.cos(multiple*angle))/multiple


def yaw_vectors(direction, angle):
    x,y,z=direction.unbind(-1);c,s=torch.cos(angle),torch.sin(angle)
    return torch.stack((c*x+s*z,y,-s*x+c*z),-1)


def spatial_axis_residual(direction, target):
    """Unsigned 3D functional-axis angle, not a directed limb pose target."""
    a=direction.norm(dim=-1,keepdim=True);b=target.norm(dim=-1,keepdim=True)
    if torch.any(a<1e-12) or torch.any(b<1e-12):
        raise ValueError('undefined spatial protocol axis')
    direction=direction/a;target=target/b
    return torch.atan2(torch.linalg.cross(direction,target).norm(dim=-1),
                       (direction*target).sum(-1).abs())


def cell_information(raw_time, support, grid, lo, hi, information):
    """Duration quadrature on raw timestamps; allocations precede validity cuts."""
    if len(raw_time)<2 or len(grid)<2 or np.any(np.diff(raw_time)<=0) or np.any(np.diff(grid)<=0):
        raise ValueError('strictly increasing raw and solver times required')
    # Cap individual support at a normal 200 Hz half-period on each side of
    # a gap: a missing interval must not acquire quadrature mass.
    left=np.maximum(np.r_[raw_time[0]-.0025,(raw_time[:-1]+raw_time[1:])/2],raw_time-.0025)
    right=np.minimum(np.r_[(raw_time[:-1]+raw_time[1:])/2,raw_time[-1]+.0025],raw_time+.0025)
    mass=np.maximum(0.,np.minimum(right,hi)-np.maximum(left,lo))*support
    index=np.searchsorted((grid[:-1]+grid[1:])/2,raw_time)
    allocated=np.bincount(index,weights=mass,minlength=len(grid))
    return allocated*(information/mass.sum()) if mass.sum()>0 else allocated*0.


@dataclass(frozen=True)
class ArmProtocolRow:
    action: str
    limb: int
    index: np.ndarray
    direction: np.ndarray
    target_axis: np.ndarray
    information: np.ndarray
    multiple: int
    audit: dict
    phase_id: str | None = None

    @property
    def factor_key(self):
        return phase_key(self.action,self.phase_id)


class ArmProtocolTape:
    def __init__(self, rows, baseline_heading, *, spatial_axes=False):
        self.rows=tuple(rows)
        self.spatial_axes=bool(spatial_axes)
        self.baseline=torch.as_tensor(baseline_heading,dtype=torch.float64).clone()
        if self.baseline.shape!=(4,) or not torch.isfinite(self.baseline).all():
            raise ValueError('four finite baseline headings required')

    def energy_for_action(self, action, delta, rotation, *, reference='thorax', residual_blocks=None):
        if reference not in ('thorax','pelvis'):
            raise ValueError('protocol reference must be thorax or pelvis')
        total=rotation.sum()*0.+delta.sum()*0.
        for row in self.rows:
            if row.action!=action or not len(row.index):continue
            d=torch.as_tensor(row.direction,dtype=rotation.dtype,device=rotation.device)
            b=torch.as_tensor(row.target_axis,dtype=rotation.dtype,device=rotation.device)
            info=torch.as_tensor(row.information,dtype=rotation.dtype,device=rotation.device)
            target=rotation[row.index,9 if reference=='thorax' else 0]@b
            increment=delta[row.limb] if delta.ndim==1 else delta[row.index,row.limb]
            direction=yaw_vectors(d,self.baseline[row.limb].to(delta)+increment)
            residual=(spatial_axis_residual(direction,target)
                      if self.spatial_axes and row.multiple==2 else
                      horizontal_residual(direction,target,row.multiple))
            from .residual_blocks import record_weighted
            record_weighted(residual_blocks, f'arm/{row.factor_key}/{row.limb}', residual, info)
            total=total+(info*residual.square()).sum()
        return total

    def audit(self):
        return [{**row.audit,'direction_geometry':
                 'spatial_undirected_axis' if self.spatial_axes and row.multiple==2
                 else 'horizontal_azimuth'} for row in self.rows]


def build_arm_protocol(episodes, calibration, contracts, actions, information, *, conditional_only=False, spatial_axes=False):
    """Freeze raw axes/support; candidate poses cannot turn their weights down."""
    if any(n.startswith('H') for n in episodes) or any(set(ep)!=set(NODES) for ep in episodes.values()):
        raise ValueError('only five-node C2 input is allowed')
    rows_out=[]
    for limb,node in enumerate(NODES[1:3]):
        for factor in calibration['heading_factors'][node]:
            if conditional_only and factor.get('used_for_frozen_heading',True):continue
            action=factor['action'];kind=factor['source_role'];q=actions[action]
            start,stop=phase_bounds(factor)
            original,pelvis,sensor=_aligned(episodes[action],node,calibration,start_s=start,stop_s=stop)
            source=episodes[action][node]['imu']
            target_axis,multiple=direction_target(kind,limb)
            if kind in ('directed_side','directed_forward'):
                axis=-np.asarray(calibration['forearm_mount_calibration'][node]['axis_sensor_long'])
            else:
                axis,_=_gyro_axis(source,start,stop)
            d=sensor@axis;v=pelvis@target_axis
            support=(np.linalg.norm(d[:,:2],axis=1)*np.linalg.norm(v[:,:2],axis=1))**2
            action_start=contracts[action]['lo']
            lo=action_start+start;hi=min(contracts[action]['hi'],action_start+stop)
            grid=q['time_s'];raw_time=original[:,0]
            key=phase_key(action,factor.get('phase_id'))
            info=float(information[node][key])
            allocated=cell_information(raw_time,support,grid,lo,hi,info)
            valid=q['valid'].copy()&(grid>=max(lo,raw_time[0]))&(grid<=min(hi,raw_time[-1]))
            # Match the existing frontend's conservative interpolation gap mask.
            for sample in (source,episodes[action][NODES[0]]['imu']):
                for k in np.flatnonzero(np.diff(sample[:,0])>.025):
                    valid&=~((grid>=sample[k,0]-.02)&(grid<=sample[k+1,0]+.02))
            valid&=allocated>0
            index=np.flatnonzero(valid)
            beta=Rotation.from_euler('z',calibration['functional_yaw_rad'][limb+1]).as_matrix()
            interpolated=Slerp(raw_time-raw_time[0],matrices(original))(grid[index]-raw_time[0]).as_matrix()
            world_direction=WORLD_TO_SMPL@beta@interpolated@axis
            if 'temporal_heading_curves' in calibration:
                from biospur_fusion.c2_sparse_nodes.heading_transport import temporal_heading
                offset=temporal_heading(grid[index],node,calibration)-calibration['frozen_heading_correction_rad'][node]
                world_direction=Rotation.from_euler('y',offset[:,None]).apply(world_direction)
            audit=dict(action=action,node=node,source_role=kind,axis_sensor=axis.tolist(),
                phase_id=factor.get('phase_id'),factor_key=key,registered_phase_interval_s=[start,stop],
                formal_interval_s=[lo,hi],original_first_row_offset_s=float(source[0,0]-action_start),
                original_information=info,realized_information=float(allocated[index].sum()),
                lost_information=float(info-allocated[index].sum()),solver_rows=len(index),
                original_axis_fit_preserved=True,maximum_raw_bracket_s=float(np.max(np.diff(raw_time))),
                candidate_dependent_weights=False,reference_is_measured=False,
                approximation='raw duration/support allocated to nearest original 20 Hz cell; direction SLERP at cell centre')
            rows_out.append(ArmProtocolRow(action,limb,index,world_direction,WORLD_TO_SMPL@target_axis,
                                           allocated[index],multiple,audit,factor.get('phase_id')))
    return ArmProtocolTape(rows_out,[calibration['frozen_heading_correction_rad'][n] for n in NODES[1:]],
                           spatial_axes=spatial_axes)
