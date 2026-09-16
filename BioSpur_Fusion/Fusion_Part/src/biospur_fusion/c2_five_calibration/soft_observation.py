"""Experimental SIP-style finite orientation likelihood around retained IMUs.

This is pose-state estimation with frozen calibration, not H calibration.
SIP (2017), equations 11-12 and table 1, uses an SO(3) residual with
w_orientation/w_acceleration=20. We preserve that ratio under our existing
0.75 m/s² acceleration normalization; the other objective terms are ours.
Root attitude remains the gauge. Sensor-world acceleration stays measured:
these are bone-pose observation residuals, not a navigation-frame rewrite.
"""
import numpy as np
import torch
from scipy.spatial.transform import Rotation
from .geometry import OBSERVED
from .solver import PoseObjective
from .optimization import optimize_pose

SIP_ORIENTATION_TO_ACCELERATION_WEIGHT=20.


def observation_rotation(observed, correction):
    if correction.shape!=(len(observed),4,3):raise ValueError('four limb rotation residuals per frame required')
    x,y,z=correction.unbind(-1);zero=torch.zeros_like(x)
    skew=torch.stack((zero,-z,y,z,zero,-x,-y,x,zero),-1).reshape(-1,4,3,3)
    result=observed.clone();result[:,1:]=torch.matrix_exp(skew)@observed[:,1:]
    return result


class SoftObservationObjective:
    parameter_count=21
    def __init__(self, base, *, protocol=None, freeze_observation=False):
        self.base=base;self.model=base.model;self.body=base.body;self.valid=base.valid
        self.observed=base.observed;self.acceleration=base.acceleration
        self.protocol=protocol
        self.freeze_observation=freeze_observation
        self.initial=torch.cat((base.initial,torch.zeros(len(base.initial),12,dtype=base.initial.dtype)),1)

    def parameters_from_rotation(self, rotation):
        previous=torch.as_tensor(rotation,dtype=self.observed.dtype)
        if previous.shape!=self.base.prior.shape or not torch.isfinite(previous).all():
            raise ValueError('finite full-body warm start required')
        if not torch.allclose(previous[:,0],self.observed[:,0],atol=1e-6,rtol=0):
            raise ValueError('soft warm start cannot change the pelvis gauge')
        if self.freeze_observation and not torch.allclose(previous[:,OBSERVED],self.observed,atol=1e-6,rtol=0):
            raise ValueError('locked observation warm start must preserve all five attitudes')
        if not torch.allclose(previous@previous.transpose(-1,-2),torch.eye(3,dtype=previous.dtype),atol=1e-5,rtol=0) or torch.any(torch.linalg.det(previous)<0):
            raise ValueError('soft warm start requires proper rotations')
        pose=self.model.initial(previous,previous[:,OBSERVED])
        pose[:,:3]=torch.from_numpy(Rotation.from_matrix((previous[:,9]@self.base.prior[:,9].transpose(-1,-2)).numpy()).as_rotvec())
        difference=previous[:,OBSERVED[1:]]@self.observed[:,1:].transpose(-1,-2)
        correction=torch.from_numpy(Rotation.from_matrix(difference.numpy().reshape(-1,3,3)).as_rotvec().reshape(len(previous),12))
        return torch.cat((pose,correction),1)

    def evaluate(self, parameters, levers, *, observed=None,acceleration=None,refresh_projection=False,projection_gap=False,residual_blocks=None):
        if parameters.shape!=(len(self.observed),self.parameter_count):
            raise ValueError('soft observation objective requires 21 coordinates per frame')
        if residual_blocks is not None and self.protocol is not None:
            raise ValueError('protocol residual export is not implemented; refuse incomplete factors')
        pose=parameters[:,:9];correction=parameters[:,9:].reshape(-1,4,3)
        if self.freeze_observation:correction=correction*0.
        measured=self.base.observed if observed is None else observed
        adjusted=observation_rotation(measured,correction)
        rotation,terms=self.base.evaluate(pose,levers,observed=adjusted,acceleration=acceleration,
                                         refresh_projection=refresh_projection,projection_gap=projection_gap,residual_blocks=residual_blocks)
        # The exponential-coordinate residual is the SO(3) log on the local
        # branch. Reject a wrapped representation rather than score it as log.
        if torch.any(correction.norm(dim=-1)>=np.pi):raise ValueError('orientation residual left its principal branch')
        orientation=correction[self.valid].square().mean()*(SIP_ORIENTATION_TO_ACCELERATION_WEIGHT/.75**2)
        continuity=.1*((correction[1:]-correction[:-1])[self.base.valid_pairs]/.12).square().mean()
        from .residual_blocks import record_mean
        record_mean(residual_blocks, 'orientation', correction[self.valid], SIP_ORIENTATION_TO_ACCELERATION_WEIGHT/.75**2)
        record_mean(residual_blocks, 'orientation_smoothness', (correction[1:]-correction[:-1])[self.base.valid_pairs]/.12, .1)
        protocol=rotation.sum()*0. if self.protocol is None else self.protocol(pose,rotation)
        terms={**terms,'loss':terms['loss']+orientation+continuity+protocol,
               'orientation_likelihood_loss':orientation,'orientation_correction_smoothness':continuity,
               'protocol_loss':protocol}
        return rotation,terms


def solve_soft_observations(data,geometry,levers,*,iterations=150,wall_limit_s=300,initial_rotation=None,protocol=None,freeze_observation=False,temporal_polish=False,pelvis_observation=False):
    base=PoseObjective(**data,geometry=geometry);objective=SoftObservationObjective(base,protocol=protocol,freeze_observation=freeze_observation)
    if pelvis_observation:
        from .pelvis_observation import PelvisObservationObjective
        objective=PelvisObservationObjective(objective)
    guess=objective.initial.clone()
    if initial_rotation is not None:guess=objective.parameters_from_rotation(initial_rotation)
    parameters,history,audit=optimize_pose(objective,levers,guess,iterations=iterations,wall_limit_s=wall_limit_s,temporal_polish=temporal_polish)
    rotation,terms=objective.evaluate(parameters,levers)
    correction=torch.rad2deg(parameters[:,9:21].reshape(-1,4,3).norm(dim=-1))[base.valid]
    return rotation.detach().numpy(),dict(history=history,optimizer=audit,
        orientation_residual_mean_deg=correction.mean(0).tolist(),orientation_residual_max_deg=correction.max(0).values.tolist(),
        orientation_residual_p95_deg=torch.quantile(correction,.95,dim=0).tolist(),
        body_feasibility=base.body.audit(rotation,base.valid),
        source='https://virtualhumans.mpi-inf.mpg.de/papers/SIP2017/SIP2017.pdf',
        SIP_orientation_to_acceleration_weight=SIP_ORIENTATION_TO_ACCELERATION_WEIGHT,
        reproduction_claimed=False,calibration_parameters_changed=False,raw_acceleration_rotated=False,
        retained_orientation_locked=freeze_observation and not pelvis_observation,
        limb_orientation_locked=freeze_observation,
        pelvis_joint_equals_tag_orientation=not pelvis_observation,
        pelvis_observation_residual_mean_deg=(float(torch.rad2deg(parameters[:,21:].norm(dim=-1))[base.valid].mean()) if pelvis_observation else 0.),
        covariance_interpretation='engineering likelihood weight ratio; not an independently measured noise covariance',
        model=('joint body pose and five retained bone attitude residuals; pelvis attitude has finite observation likelihood'
               if pelvis_observation else
               'joint body pose and four retained bone attitude residuals; pelvis gauge fixed'))
