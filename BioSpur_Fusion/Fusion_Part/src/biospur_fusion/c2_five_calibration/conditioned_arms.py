"""Experimental Akhter/Black (CVPR 2015) arm feasibility adapter.

Equations: pose-conditioned separating plane and projected bounds, paper §3.
Model assets are supplied explicitly, never learned from this subject's ten
IMUs. The public PosePriorPython checker is used as an independent oracle.
This module checks only arms; it is not a full-body physiological model.
"""
from pathlib import Path
import hashlib

import numpy as np
from scipy.io import loadmat
from scipy.ndimage import distance_transform_edt
import torch
import torch.nn.functional as F


def _basis(u, v):
    u=F.normalize(u,dim=-1)
    v=F.normalize(v-(v*u).sum(-1,keepdim=True)*u,dim=-1)
    return torch.stack((u,v,F.normalize(torch.linalg.cross(u,v),dim=-1)),-1)


def arm_bones(joints):
    # Published topology: back, right shoulder/upper/fore, left shoulder/upper/fore.
    a=[0,12,17,19,12,16,18];b=[12,17,19,21,16,18,20]
    # The published table accepts forward flexion toward -Z after right/up
    # alignment; our anatomical FK uses +Z. This parity boundary is established
    # with canonical forward/backward elbow fixtures, never H/reference fitting.
    parity=joints.new_tensor([1.,1.,-1.])
    return (joints[:,a]-joints[:,b])*parity


class ConditionedArms:
    @classmethod
    def from_spec(cls, spec):
        directory=Path(spec['asset_directory'])
        for name in ('jointAngleModel_v2.mat','staticPose.mat'):
            expected=spec['sha256'][name]
            if hashlib.sha256((directory/name).read_bytes()).hexdigest()!=expected:
                raise ValueError('conditioned arm model asset hash mismatch')
        return cls(directory)

    def __init__(self, asset_directory):
        path=Path(asset_directory)
        model=loadmat(path/'jointAngleModel_v2.mat')
        static=loadmat(path/'staticPose.mat')
        self.step=float(model['jmp'].item())
        self.di=np.asarray(static['di'],float)
        # Published checker uses this static reference vector.
        self.a=np.array([.997478132770155,.002323490336655,.070936422506501])
        self.spread=[model['angleSprd'][0,i].astype(bool) for i in (0,2)]
        self.planes=[model['sepPlane'][0,i] for i in (1,3)]
        self.axes=[model['E2'][0,i] for i in (1,3)]
        self.bounds=[model['bounds'][0,i] for i in (1,3)]
        self.filled=[];self.distances=[]
        for side in range(2):
            good=(self.spread[side]&np.isfinite(self.planes[side]).all(-1)
                  &np.isfinite(self.axes[side]).all(-1)&np.isfinite(self.bounds[side]).all(-1))
            # Periodic azimuth distance; extra copies avoid a false +/-180 seam.
            n=len(good);extended=np.tile(good,(3,1))
            distance,indices=distance_transform_edt(~extended,return_indices=True)
            ii=indices[0,n:2*n]%n;jj=indices[1,n:2*n]
            self.distances.append(distance[n:2*n]*self.step)
            self.filled.append(tuple(x[ii,jj] for x in (self.planes[side],self.axes[side],self.bounds[side])))

    def evaluate_bones(self, bones, *, extend=False):
        if bones.ndim!=3 or bones.shape[1:]!=(7,3):
            raise ValueError('expected N x 7 x 3 published arm bones')
        reference=_basis(bones[:,4]-bones[:,1],bones[:,0])
        terms=[];flags=[];support=[];distances=[]
        for side,parent in enumerate((2,5)):
            upper=F.normalize(bones[:,parent],dim=-1)
            local=(reference.transpose(-1,-2)@upper[...,None]).squeeze(-1)
            azimuth=torch.rad2deg(torch.atan2(local[:,1],local[:,0]))
            elevation=torch.rad2deg(torch.atan2(local[:,2],local[:,:2].norm(dim=-1)))
            ti=torch.floor((azimuth+180)/self.step).long().clamp(0,self.spread[side].shape[0]-1)
            pi=torch.floor((elevation+90)/self.step).long().clamp(0,self.spread[side].shape[1]-1)
            def tensor(x):return torch.as_tensor(x,dtype=bones.dtype,device=bones.device)
            spread=torch.as_tensor(self.spread[side],device=bones.device)[ti,pi]
            plane=tensor(self.planes[side])[ti,pi]
            e2=tensor(self.axes[side])[ti,pi];bound=tensor(self.bounds[side])[ti,pi]
            supported=torch.isfinite(plane).all(-1)&torch.isfinite(e2).all(-1)&torch.isfinite(bound).all(-1)&(plane[:,:3].norm(dim=-1)>1e-8)
            if extend:
                filled=self.filled[side]
                plane=tensor(filled[0])[ti,pi];e2=tensor(filled[1])[ti,pi];bound=tensor(filled[2])[ti,pi]
            grid=torch.stack((elevation/90,azimuth/180),-1)[None,:,None,:]
            distances.append(F.grid_sample(tensor(self.distances[side])[None,None],grid,
                mode='bilinear',padding_mode='border',align_corners=True).reshape(-1))
            plane=torch.nan_to_num(plane);e2=torch.nan_to_num(e2);bound=torch.nan_to_num(bound)
            plane=plane/plane[:,:3].norm(dim=-1,keepdim=True).clamp_min(1e-8)
            transported=(reference@tensor(self.a)[...,None]).squeeze(-1)
            alternative=(reference@tensor(self.di[:,parent])[...,None]).squeeze(-1)
            parallel=((upper-transported).norm(dim=-1)<1e-4)|((upper+transported).norm(dim=-1)<1e-4)
            normal=torch.where(parallel[:,None],torch.linalg.cross(upper,alternative),torch.linalg.cross(transported,upper))
            child_frame=_basis(upper,normal)
            child=(child_frame.transpose(-1,-2)@F.normalize(bones[:,parent+1],dim=-1)[...,None]).squeeze(-1)
            plane_error=(plane[:,:3]*child).sum(-1)+plane[:,3]
            projection=_basis(plane[:,:3],e2)
            uv=(projection[:,:,1:].transpose(-1,-2)@child[...,None]).squeeze(-1)
            violations=torch.stack((F.relu(plane_error),F.relu(bound[:,0]-uv[:,0]),F.relu(uv[:,0]-bound[:,1]),F.relu(bound[:,2]-uv[:,1]),F.relu(uv[:,1]-bound[:,3])),-1)
            terms.append(violations)
            flags.append(torch.stack((spread,supported&(violations.max(-1).values<=1e-10)),-1))
            support.append(supported)
        return dict(violations=torch.stack(terms,1),flags=torch.stack(flags,1),supported=torch.stack(support,1),parent_distance_deg=torch.stack(distances,1))

    def loss(self, joints, valid, *, residual_blocks=None):
        r=self.evaluate_bones(arm_bones(joints),extend=True)
        # Nearest supported cells supply a recovery gradient only. The audit
        # never treats such extensions as measured/valid portions of the table.
        from .residual_blocks import record_mean
        record_mean(residual_blocks, 'conditioned_arm', r['violations'][valid]/.2)
        record_mean(residual_blocks, 'conditioned_parent', r['parent_distance_deg'][valid]/20.)
        return (r['violations'][valid]/.2).square().mean()+(r['parent_distance_deg'][valid]/20.).square().mean()

    def audit(self, joints, valid):
        with torch.no_grad():r=self.evaluate_bones(arm_bones(joints))
        return dict(model='Akhter/Black CVPR2015 arm tables; third-party mirror',
            order=['right_upper','right_forearm','left_upper','left_forearm'],
            invalid_frames=(~r['flags'][valid]).reshape(-1,4).sum(0).tolist(),
            unsupported_parent_frames=(~r['supported'][valid]).sum(0).tolist(),
            frames=int(valid.sum()),scope='pose-conditioned arms only; topology mapping remains experimental')
