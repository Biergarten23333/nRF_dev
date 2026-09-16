"""Causal held-IMU expiry and pre-update raw-sweep consistency admission.

These are diagnostic policies using existing clock cadence and significance;
they are not calibrated measurement noise or production-qualified classifiers.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import numpy as np
from scipy.stats import chi2

from biospur_fusion.root_r3.estimator import (
    RootFilterConfig, propagate_inertial, propagate_constant_velocity,
)
from .tight_range import (
    RawRangeUpdateConfig, RawRangeDecision, linearize_raw_range_factors,
    update_raw_ranges, _valid_slots,PreparedRawRangeUpdate,
)


@dataclass(frozen=True)
class PropagationSafetyAudit:
    inertial_duration_s: float
    stale_cv_duration_s: float
    hold_deadline_s: float


class CausalImuHold:
    """A received force sample owns at most one nominal 200 Hz period."""

    def __init__(self,time_s,force,rotation):
        self.time_s=-np.inf
        self.observe(time_s,force,rotation)

    def observe(self,time_s,force,rotation):
        if not np.isfinite(float(time_s)) or float(time_s)<self.time_s:
            raise ValueError('actual IMU sample reversed time')
        force=np.asarray(force,float);rotation=np.asarray(rotation,float)
        if force.shape!=(3,) or rotation.shape!=(3,3) or not np.isfinite(force).all() or not np.isfinite(rotation).all():
            raise ValueError('IMU sample must be finite')
        if not np.allclose(rotation.T@rotation,np.eye(3),atol=1e-8,rtol=0) or not np.isclose(np.linalg.det(rotation),1.,atol=1e-8,rtol=0):
            raise ValueError('IMU rotation must be proper SO(3)')
        self.time_s=float(time_s);self.force=force.copy();self.rotation=rotation.copy()

    def propagate(self,state,target_time_s,config=RootFilterConfig(), *, transition_observer=None):
        before=state
        target=float(target_time_s);start=float(state.time_s)
        if not np.isfinite(target) or not np.isfinite(start) or target<start-1e-12 or start<self.time_s-1e-12:
            raise ValueError('propagation/input chronology invalid')
        deadline=self.time_s+1./200.
        split=min(target,max(start,deadline))
        fresh=max(0.,split-start)
        transition=np.eye(9)
        if fresh>1e-12:
            state,phi=propagate_inertial(state,split,self.force,self.rotation,config)
            transition=phi@transition
        stale=max(0.,target-float(state.time_s))
        if stale>1e-12:
            state,phi=propagate_constant_velocity(state,target,config)
            transition=phi@transition
        elif float(state.time_s)!=target:
            # Preserve exact event clock even at the numerical boundary.
            state,phi=propagate_inertial(state,target,self.force,self.rotation,config)
            transition=phi@transition
        if transition_observer is not None:
            from .joint_transition_tape import notify_transition
            notify_transition(transition_observer,transition,before,state,'prediction')
        return state,PropagationSafetyAudit(fresh,stale,deadline)


@lru_cache(maxsize=8)
def inherited_raw_nis_limit(degrees_of_freedom):
    if degrees_of_freedom not in range(1,9):
        raise ValueError('raw sweep NIS requires one to eight retained links')
    confidence=chi2.cdf(RootFilterConfig().nis_limit_3d,3)
    return float(chi2.ppf(confidence,degrees_of_freedom))


def guarded_raw_update(state,row,*,anchors_m,clock,range_bias_m=None,bias_prior=None,
                       tag_offset_world_m,tag_offset_velocity_world_mps,
                       information_weights=None,reference_epoch_s=None,
                       config=RawRangeUpdateConfig(),consider_position=False,gain_scale=None,
                       correction_gain_scope='full-state',
                       transition_observer=None):
    """Reject prior-inconsistent complete sweeps before any state mutation.

    The unrobust innovation covariance includes the common root prior. A
    consistent large correction under a broad prior can pass; amplitude alone
    is not evidence of a bad range. The external legacy bias approximation
    remains unchanged by default. An optional strictly pre-link bias snapshot
    supplies the same total measurement covariance to admission and update;
    missing root/bias cross covariance remains an explicit approximation.
    """
    if range_bias_m is not None and bias_prior is not None:
        raise ValueError('range_bias_m and bias_prior are mutually exclusive')
    arguments=dict(anchors_m=anchors_m,clock=clock,range_bias_m=range_bias_m,
        bias_prior=bias_prior,
        tag_offset_world_m=tag_offset_world_m,
        tag_offset_velocity_world_mps=tag_offset_velocity_world_mps,
        information_weights=information_weights,reference_epoch_s=reference_epoch_s,
        config=config)
    count=len(_valid_slots(row))
    if row.boot!=clock.boot_epoch or tuple(row.anchor_ids)!=tuple(range(8)) or count<(1 if config.partial_tracking else 4):
        new,decision=update_raw_ranges(state,row,**arguments,consider_position=consider_position,gain_scale=gain_scale,
            correction_gain_scope=correction_gain_scope,transition_observer=transition_observer)
        return new,decision,np.nan,np.nan
    factors=linearize_raw_range_factors(state,row,**arguments,_enforce_geometry=False)
    if len(factors.anchors)!=count:
        raise ValueError('retained raw factor count disagrees with selected slots')
    limit=inherited_raw_nis_limit(count)
    reason=None
    if not config.partial_tracking and (factors.rank!=3 or not np.isfinite(factors.condition) or factors.condition>1e10):
        reason='SOLVER_OR_GEOMETRY_REJECT'
    elif factors.prior_nis>limit:
        reason='PRIOR_RAW_SWEEP_NIS_REJECT'
    if reason is not None:
        sigma=np.sqrt(np.diag(factors.r_prior_m2))
        decision=RawRangeDecision(False,reason,factors.anchors,factors.link_epochs_s,
            factors.reference_epoch_s,factors.measured_ranges_m,factors.predicted_ranges_m,
            factors.innovations_m,factors.innovations_m/sigma,factors.robust_weights,
            sigma,factors.rank,factors.condition,0,config.uncertainty_provenance,
            np.sqrt(np.diag(factors.sensor_r_m2)))
        return state,decision,factors.prior_nis,limit
    reuse={}
    if config.symmetric_corrected_discrepancy:
        reuse['prepared']=PreparedRawRangeUpdate(state,row,anchors_m,clock,range_bias_m,bias_prior,
            information_weights,tag_offset_world_m,tag_offset_velocity_world_mps,config,factors,reference_epoch_s)
    new,decision=update_raw_ranges(state,row,**arguments,consider_position=consider_position,gain_scale=gain_scale,
        correction_gain_scope=correction_gain_scope,transition_observer=transition_observer,**reuse)
    return new,decision,factors.prior_nis,limit
