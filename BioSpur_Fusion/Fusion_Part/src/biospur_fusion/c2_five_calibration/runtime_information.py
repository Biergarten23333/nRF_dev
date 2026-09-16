"""Frozen-calibration runtime factors with once-only C2 boundary ownership."""
import numpy as np
import torch

from .residual_blocks import ResidualBlocks
from .residual_support import pose_row_support


class RuntimeInformationObjective:
    """Profiled historical factor plus residuals reaching new sample times.

    The historical factor is a local engineering quadratic. Its constant is
    omitted from optimization, reported separately, and never rescaled. Both
    experimental branches use identical current-row counts and ownership.
    """
    def __init__(self, objective, time_s, checkpoint_time, factor, *, use_history, bridge_missing_state=False):
        self.objective=objective
        self.model=objective.model
        self.initial=objective.initial
        self.valid=objective.valid
        self.use_history=bool(use_history)
        self.bridge_missing_state=bool(bridge_missing_state)
        self.count=len(checkpoint_time)
        time=np.asarray(time_s)
        checkpoint_time=np.asarray(checkpoint_time)
        if self.count!=10 or len(time)<=self.count or not np.allclose(
                time[:self.count],checkpoint_time,atol=1e-6,rtol=0):
            raise ValueError('exact ten-frame C2 boundary time correspondence required')
        dtype=self.initial.dtype
        self.matrix=torch.as_tensor(np.array(factor['matrix']),dtype=dtype)
        self.residual=torch.as_tensor(np.array(factor['residual']),dtype=dtype)
        self.point=torch.as_tensor(np.array(factor['point']),dtype=dtype)
        self.constant=float(factor['constant'])
        if (self.matrix.ndim!=2 or self.matrix.shape[1]!=210
                or self.residual.shape!=(len(self.matrix),) or self.point.shape!=(210,)
                or not all(torch.isfinite(x).all() for x in (self.matrix,self.residual,self.point))
                or not np.isfinite(self.constant) or self.constant<0):
            raise ValueError('finite profiled pose boundary factor required')
        self.masks=None

    def evaluate(self, parameters, levers):
        blocks=ResidualBlocks()
        rotation,old=self.objective.evaluate(parameters,levers,refresh_projection=True,
                                              projection_gap=False,residual_blocks=blocks)
        if self.masks is None:
            support=pose_row_support(blocks,self.valid.detach().cpu().numpy())
            self.masks={name:torch.as_tensor(row.stop>self.count) for name,row in support.items()}
            self.ownership={name:dict(retained=int(mask.sum()),excluded=int((~mask).sum()))
                            for name,mask in self.masks.items()}
        energy={name:value.reshape(-1)[self.masks[name]].square().sum() for name,value in blocks.items()}
        zero=parameters.sum()*0.
        history=(self.matrix@(parameters[:self.count].reshape(-1)-self.point)+self.residual).square().sum() if self.use_history else zero
        # Scale means in optimize_pose's slow stage are twice their contribution
        # to the combined two-scale acceleration energy.
        acceleration=blocks['acceleration']
        selected=(acceleration.reshape(-1)*self.masks['acceleration']).reshape(acceleration.shape)
        scales=selected.square().sum(dim=(0,2,3))*acceleration.shape[1]
        def total(names):return sum((energy.get(n,zero) for n in names),zero)
        gap_pose=gap_orientation=zero
        if self.bridge_missing_state:
            from .gap_continuity import gap_correction_energy
            from .soft_observation import observation_rotation
            adjusted=observation_rotation(self.objective.observed,parameters[:,9:].reshape(-1,4,3))
            seed=self.model.initial(self.objective.base.prior,adjusted)
            gap_pose,gap_orientation=gap_correction_energy(parameters,seed,self.valid,consumed_frames=self.count)
        terms=dict(loss=sum(energy.values(),zero)+history+gap_pose+gap_orientation,
            acceleration_loss=energy['acceleration'],acceleration_loss_by_scale=scales,
            orientation_likelihood_loss=energy['orientation'],
            weighted_pose_prior_loss=total(('angular_prior','projection_gap','tracking_position','tracking_velocity')),
            weighted_pose_smoothness_loss=energy['pose_smoothness'],
            orientation_correction_smoothness=energy['orientation_smoothness'],
            body_loss=total(tuple(n for n in energy if n.startswith('body_'))),
            axial_loss=energy.get('forearm_axial',zero),
            conditioned_arm_loss=total(('conditioned_arm','conditioned_parent')),
            history_loss=history,gap_process_loss=gap_pose+gap_orientation,body_violation=old['body_violation'],
            acceleration_rms_mps2=old['acceleration_rms_mps2'],
            prior_position_rms_m=old['prior_position_rms_m'])
        return rotation,terms
