"""One complete continuous-C2 local-surrogate residual evaluation.

Reuses the same observation transport and registered energy owners as shared_fit.
A frozen neural prior remains a local surrogate; no posterior or accepted raw
replay is implied by exporting its factors. No H/reference input is consumed.
"""
import torch
from .shared_orientation import transport_heading
from .shared_fit import shared_regularization
from .residual_blocks import ResidualBlocks


class JointResidualObjective:
    def __init__(self, objective, prior, delta, nominal, *, heading_model=None,
                 pose_protocol=None, refresh_projection=True, projection_gap=False):
        if getattr(objective,'protocol',None) is not None:
            raise ValueError('use registered C2 protocol owners, not opaque callbacks')
        base=getattr(objective,'base',objective)
        while not hasattr(base,'prior') and hasattr(base,'base'):
            base=base.base
        if getattr(base,'_frozen_window',False):
            raise ValueError('joint reprojection requires the original continuous objective')
        if delta.shape not in ((4,),(len(base.prior),4)):
            raise ValueError('baseline delta must match the full C2 heading grid')
        self.objective=objective;self.prior=prior;self.delta=delta;self.nominal=nominal
        self.heading_model=heading_model;self.pose_protocol=pose_protocol
        self.refresh_projection=refresh_projection;self.projection_gap=projection_gap
        names={a for rows in prior.factor_actions.values() for a in rows.values()}
        if pose_protocol is not None:names|=pose_protocol.actions
        if any(n.startswith('H') for n in names):raise ValueError('H protocol is forbidden')
        self.actions=tuple(sorted(names))

    def evaluate(self, parameters, coefficients, levers):
        increment=(coefficients if self.heading_model is None else
                   self.heading_model.increments(coefficients))
        if self.heading_model is None and coefficients.shape!=(4,):
            raise ValueError('four shared heading increments required')
        observed,acceleration=transport_heading(self.objective.observed,self.objective.acceleration,increment)
        blocks=ResidualBlocks()
        rotation,terms=self.objective.evaluate(parameters,levers,observed=observed,
            acceleration=acceleration,refresh_projection=self.refresh_projection,
            projection_gap=self.projection_gap,residual_blocks=blocks)
        delta=self.delta+increment;energy=terms['loss']
        for action in self.actions:
            energy=energy+self.prior.energy_for_action(action,delta,rotation,residual_blocks=blocks)
            if self.pose_protocol is not None:
                energy=energy+self.pose_protocol.energy_for_action(action,parameters,residual_blocks=blocks)
        energy=energy+shared_regularization(self.prior,delta,levers,self.nominal,
            heading_model=self.heading_model,coefficients=coefficients,residual_blocks=blocks)
        return rotation,energy,blocks
