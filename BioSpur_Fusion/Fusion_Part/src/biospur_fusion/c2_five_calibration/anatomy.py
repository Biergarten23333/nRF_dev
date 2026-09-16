"""Reduced limb kinematics with retained distal IMU rotations as observations.

Elbows have flexion and axial rotation; knees use a hinge approximation.
Flexion is the geometric long-axis angle, as in final C2 analytic hinge IK.
Fixed rest-frame alignment handles noncollinear SMPL bones. No action labels
or removed-node measurements enter the inverse reconstruction.
"""
import numpy as np
import torch
import torch.nn.functional as F
from biospur_fusion.c2_articulated_biomechanics.model import HINGE_SPECS, _minimal_alignment

from .geometry import OBSERVED

TORSO = [3,6,9,13,14]
PROXIMAL = [16,17,1,2]
TIP = [20,21,7,8]
JOINT_NAMES = ('elbow_left','elbow_right','knee_left','knee_right')
MAX_BEND = np.deg2rad([HINGE_SPECS[name][4] for name in JOINT_NAMES])
PARAMETER_COUNT = 9
FLEXION = slice(3, 7)
TWIST = slice(7, 9)


# axis_angle_to_matrix(fast=True), documentation lines566–588, accessed2026-09-07:
# https://pytorch3d.readthedocs.io/en/latest/_modules/pytorch3d/transforms/rotation_conversions.html
# License: https://raw.githubusercontent.com/facebookresearch/pytorch3d/main/LICENSE
#
# BSD License
# For PyTorch3D software
# Copyright (c) Meta Platforms, Inc. and affiliates. All rights reserved.
# Redistribution and use in source and binary forms, with or without modification,
# are permitted provided that the following conditions are met:
#  * Redistributions of source code must retain the above copyright notice, this
#    list of conditions and the following disclaimer.
#  * Redistributions in binary form must reproduce the above copyright notice,
#    this list of conditions and the following disclaimer in the documentation
#    and/or other materials provided with the distribution.
#  * Neither the name Meta nor the names of its contributors may be used to
#    endorse or promote products derived from this software without specific
#    prior written permission.
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND
# ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
# WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
# DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE FOR
# ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES
# (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
# LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON
# ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
# (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
# SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

def exp_rotation(axis_angle):
    # Official fast=True branch, unchanged algebra; no PyTorch3D installation.
    shape=axis_angle.shape
    device,dtype=axis_angle.device,axis_angle.dtype
    angles=torch.norm(axis_angle,p=2,dim=-1,keepdim=True).unsqueeze(-1)
    rx,ry,rz=axis_angle[...,0],axis_angle[...,1],axis_angle[...,2]
    zeros=torch.zeros(shape[:-1],dtype=dtype,device=device)
    cross_product_matrix=torch.stack([zeros,-rz,ry,rz,zeros,-rx,-ry,rx,zeros],dim=-1).view(shape+(3,))
    outer_product=axis_angle.unsqueeze(-1)*axis_angle.unsqueeze(-2)
    identity=torch.eye(3,dtype=dtype,device=device)
    return (torch.cos(angles)*identity+torch.sinc(angles/torch.pi)*cross_product_matrix
            +.5*torch.sinc(angles/(2*torch.pi))**2*outer_product)


class JointModel:
    def __init__(self, geometry, *, dtype=torch.float64):
        self.torso_model=geometry.get('torso_pose_model','rigid')
        if self.torso_model not in ('rigid','relative_prior'):
            raise ValueError('unknown torso pose model')
        offsets=torch.as_tensor(geometry['rest_offsets_m'],dtype=dtype)
        corrections=torch.as_tensor(geometry['bone_frame_correction'],dtype=dtype)
        # Functional sensor frame Y points left. In SMPL rest coordinates,
        # arm flexion-plane axes are +/-Y and knee hinge axes are X.
        guide=torch.tensor([[0.,1.,0.],[0.,-1.,0.],[1.,0.,0.],[1.,0.,0.]],dtype=dtype)
        self.distal_axis=F.normalize(offsets[TIP],dim=-1)
        self.proximal_axis=F.normalize(offsets[OBSERVED[1:]],dim=-1)
        self.distal_hinge=(corrections[1:].transpose(-1,-2)@guide[...,None]).squeeze(-1)
        self.distal_hinge=F.normalize(self.distal_hinge-(
            self.distal_hinge*self.distal_axis).sum(-1,keepdim=True)*self.distal_axis,dim=-1)
        # Positive forward hinge, opposite the old inverse-rotation guides.
        self.distal_hinge*=torch.tensor([-1.,-1.,1.,1.],dtype=dtype)[:,None]
        self.rest_alignment=torch.as_tensor(_minimal_alignment(
            self.proximal_axis.numpy(),self.distal_axis.numpy()).as_matrix(),dtype=dtype)
        self.proximal_hinge=(self.rest_alignment.transpose(-1,-2)
            @self.distal_hinge[...,None]).squeeze(-1)
        self.maximum_bend=torch.as_tensor(MAX_BEND,dtype=dtype)

    def prior_target(self, prior, observed):
        """Use the same authoritative segment frames on both sides of FK.

        Learned IMU rotations and independent spine/clavicle rotations are
        discarded by this model. They cannot remain hidden position or
        angular targets that move the free joints to compensate for them.
        Learned proximal predictions are projected onto the same geometric
        hinge manifold. An unreachable prior must not remain an angular or
        Cartesian target that pulls a valid joint back toward its old branch.
        """
        return self.rotation(prior,observed,self.initial(prior,observed))

    def initial(self, prior, observed, *, return_diagnostics=False):
        parent=prior[:,PROXIMAL]
        child=observed[:,1:]
        u=(parent@self.proximal_axis[...,None]).squeeze(-1)
        v=(child@self.distal_axis[...,None]).squeeze(-1)
        angle=torch.atan2(torch.linalg.cross(u,v).norm(dim=-1),(u*v).sum(-1))
        angle=torch.minimum(angle,self.maximum_bend)
        # Use the learned parent's anatomical hinge, not cross(u,v): the
        # latter would conceal a reversed bend by changing twist by pi.
        world_hinge=(parent[:,:2]@self.proximal_hinge[:2,...,None]).squeeze(-1)
        local_hinge=(child[:,:2].transpose(-1,-2)@world_hinge[...,None]).squeeze(-1)
        d=self.distal_axis[:2]; hinge=self.distal_hinge[:2]
        projected=local_hinge-(local_hinge*d).sum(-1,keepdim=True)*d
        supported=projected.norm(dim=-1)>1e-6
        tangent=torch.linalg.cross(d,hinge)
        twist=torch.atan2((projected*tangent).sum(-1),(projected*hinge).sum(-1))
        # A parallel parent hinge has no observable plane. Zero is the
        # declared functional-plane prior, never a new IMU observation.
        twist=torch.where(supported,twist,torch.zeros_like(twist))
        # NumPy unwrap's piecewise-constant 2*pi correction, kept in torch so
        # calibration proposals can differentiate their geometric projection.
        difference=twist[1:]-twist[:-1]
        wrapped=torch.remainder(difference+torch.pi,2*torch.pi)-torch.pi
        wrapped=torch.where((wrapped==-torch.pi)&(difference>0),torch.pi,wrapped)
        correction=torch.where(difference.abs()<torch.pi,0.,wrapped-difference)
        twist=torch.cat((twist[:1],twist[1:]+torch.cumsum(correction,dim=0)),dim=0)
        parameters=torch.cat((torch.zeros(len(prior),3,dtype=prior.dtype),angle,twist),-1)
        if return_diagnostics:
            return parameters,dict(unsupported_arm_plane_frames=(~supported).sum(0).tolist(),
                plane_source='projected learned proximal hinge; not an observed proximal sensor',
                magnitude_source='learned proximal and measured distal long-axis angle')
        return parameters

    def rotation(self, prior, observed, parameters):
        if parameters.shape != (len(prior),PARAMETER_COUNT):
            raise ValueError('joint model requires 9 parameters: shared torso, flexion, twist')
        rotation=prior.clone()
        # Mature C2 _standing_proxy_joints uses one torso rotation for both
        # shoulder anchors. Preserve that rigid-body boundary in SMPL FK:
        # absent spine/clavicle sensors must not become independently fitted
        # joints that shorten the shoulder span to explain wrist acceleration.
        torso=exp_rotation(parameters[:,:3])@prior[:,9]
        rotation[:,TORSO]=(exp_rotation(parameters[:,:3])[:,None]@prior[:,TORSO]
                          if self.torso_model=='relative_prior' else torso[:,None])
        flexion=exp_rotation(-parameters[:,FLEXION,None]*self.distal_hinge)
        twist=exp_rotation(parameters[:,TWIST,None]*self.distal_axis[:2])
        relative=torch.cat((twist@flexion[:,:2],flexion[:,2:]),dim=1)@self.rest_alignment
        rotation[:,PROXIMAL]=observed[:,1:]@relative
        rotation[:,OBSERVED]=observed
        return rotation
