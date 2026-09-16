"""Latent heading corrections supported across the entire recorded C2 session.

Action times place numerical knots; they are not heading observations. Existing
raw direction factors remain the only registered direction evidence. This
removes the old hard constant tail after the early elbow calibration actions.
"""
import copy

import numpy as np
import torch

from biospur_fusion.c2_sparse_nodes.inputs import NODES
from .phase_contract import recorded_prefix
from .temporal_parameters import TemporalHeadingParameters


class SessionHeadingParameters(TemporalHeadingParameters):
    def __init__(self, baseline, time_s, contracts, *, rate_scale_deg_s=.1):
        if 'session_heading_increments' in baseline:
            raise ValueError('session increments require an immutable original baseline')
        super().__init__(baseline, time_s)
        names=recorded_prefix(contracts,require_complete=True)
        if not np.isfinite(rate_scale_deg_s) or rate_scale_deg_s<=0:
            raise ValueError('positive engineering drift-rate scale required')
        self.rate_scale_rad_s=float(np.deg2rad(rate_scale_deg_s))
        centers=np.array([(contracts[n]['lo']+contracts[n]['hi'])/2 for n in names])
        if (not np.isfinite(centers).all() or np.any(np.diff(centers)<=0)
                or centers[0]<=self.time_s[0] or centers[-1]>=self.time_s[-1]):
            raise ValueError('full-session knot times must be inside the continuous C2 tape')
        knots=np.r_[self.time_s[0],centers,self.time_s[-1]]
        self.knots=[knots.copy() for _ in range(4)]
        self.slices=[slice(i*len(knots),(i+1)*len(knots)) for i in range(4)]
        self.size=4*len(knots)
        self.basis=self.basis_at(self.time_s)

    def regularization(self, coefficients, *, residual_blocks=None):
        # Finite engineering smoothness, not a measured bias covariance.
        # Constant increments remain governed by existing raw direction factors.
        values=coefficients.reshape(4,-1)
        dt=torch.as_tensor(np.diff(self.knots[0]),dtype=values.dtype,device=values.device)
        rates=(values[:,1:]-values[:,:-1])/dt
        from .residual_blocks import record_mean
        record_mean(residual_blocks, 'heading_rate', rates/self.rate_scale_rad_s)
        return (rates/self.rate_scale_rad_s).square().mean()

    def frontend(self, coefficients):
        c=np.asarray(coefficients,float)
        if c.shape!=(self.size,) or not np.isfinite(c).all():
            raise ValueError('finite full-session heading coefficients required')
        result=copy.deepcopy(self.baseline)
        result.setdefault('temporal_heading_curves',{})
        result['session_heading_increments']={n:dict(time_s=k.tolist(),correction_rad=c[s].tolist())
            for n,k,s in zip(NODES[1:],self.knots,self.slices)}
        result['session_heading_audit']=dict(H_used=False,reference_used=False,
            source='all recorded C2 body/IMU joint objective; action times are knot locations only',
            rate_scale_deg_s=float(np.rad2deg(self.rate_scale_rad_s)),
            rate_scale_is_measured=False,extrapolation='hold last C2 value',
            H_drift_tracking_implemented=False)
        result['calibration_accepted']=False
        return result
