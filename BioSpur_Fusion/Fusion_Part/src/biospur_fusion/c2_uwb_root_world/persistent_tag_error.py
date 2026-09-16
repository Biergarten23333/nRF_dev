"""Root9 plus ten constant segment-local effective tag discrepancies.

Discrepancies are signed engineering nuisance states, not calibrated anatomy.
One likelihood updates root and nuisance with their full cross covariance.
The common translation gauge remains prior-referenced. No separate range-bias
tracker may consume these same observations. Orientation and IMU bias owners
are unchanged; only the pelvis accelerometer bias is part of RootState.
"""
from __future__ import annotations

from dataclasses import dataclass
import numpy as np

from biospur_fusion.root_r3.estimator import propagate_inertial
from biospur_fusion.root_r3.models import RootState
from .tight_range import (RawRangeUpdateConfig, RawRangeDecision,
    PersistentRangeBiasConfig, linearize_raw_range_factors,
    _empty_decision, _valid_slots, _robust_weights)


@dataclass(frozen=True)
class JointTagErrorState(RootState):
    """Authoritative root9 + discrepancy30 Gaussian; root9 is only a view."""
    def __post_init__(self):
        if (np.asarray(self.vector).shape!=(39,) or np.asarray(self.covariance).shape!=(39,39)
                or not np.isfinite(self.time_s) or not np.isfinite(self.vector).all()
                or not np.isfinite(self.covariance).all()
                or not np.allclose(self.covariance,self.covariance.T,atol=1e-10)):
            raise ValueError('invalid joint tag-error state')
        np.linalg.cholesky(self.covariance)


class PersistentTagErrorFilter:
    def __init__(self, root: RootState, nodes):
        self.nodes=tuple(nodes)
        if len(self.nodes)!=10 or len(set(self.nodes))!=10:
            raise ValueError('exact ten-node inventory required')
        covariance=np.zeros((39,39))
        covariance[:9,:9]=root.covariance
        covariance[9:,9:]=np.eye(30)*PersistentRangeBiasConfig().initial_sigma_m**2
        self.state=JointTagErrorState(root.time_s,np.r_[root.vector,np.zeros(30)],covariance)
        self.last_error_delta=np.zeros((10,3))

    @property
    def root(self):
        return RootState(self.state.time_s,self.state.vector[:9],self.state.covariance[:9,:9])

    @property
    def error(self):return self.state.vector[9:].reshape(10,3)

    @property
    def covariance(self):return self.state.covariance

    def commit_propagation(self,root,phi,transition_observer=None):
        """Commit actual held-input/CV root transition, preserving all cross blocks."""
        phi=np.asarray(phi,float)
        if phi.shape!=(9,9) or not np.isfinite(phi).all():raise ValueError('invalid root transition')
        covariance=self.covariance.copy()
        cross=phi@covariance[:9,9:]
        covariance[:9,:9]=root.covariance
        covariance[:9,9:]=cross;covariance[9:,:9]=cross.T
        vector=self.state.vector.copy();vector[:9]=root.vector
        self.state=JointTagErrorState(root.time_s,vector,covariance)
        if transition_observer is not None:
            full=np.eye(39);full[:9,:9]=phi;transition_observer(full)

    def propagate(self, time_s, force, rotation, config):
        root,phi=propagate_inertial(self.root,time_s,force,rotation,config)
        # Nuisance is constant in its declared local basis: Q_error = 0.
        self.commit_propagation(root,phi)

    def update_ranges(self,row,*,anchors_m,clock,offset_world_m,
                      offset_velocity_world_mps,basis_world_from_local,
                      basis_velocity_world_from_local,reference_epoch_s,
                      information_weights=None,config=RawRangeUpdateConfig()):
        config.validate()
        self.last_error_delta=np.zeros((10,3))
        if row.boot!=clock.boot_epoch:
            return _empty_decision('CLOCK_BOOT_UNAVAILABLE',config)
        if tuple(row.anchor_ids)!=tuple(range(8)):
            return _empty_decision('ANCHOR_IDENTITY_MISMATCH',config)
        if len(_valid_slots(row))<4:
            return _empty_decision('FEWER_THAN_FOUR_LINKS',config)
        n=self.nodes.index(row.node)
        basis=np.asarray(basis_world_from_local,float)
        rate=np.asarray(basis_velocity_world_from_local,float)
        if basis.shape!=(3,3) or rate.shape!=(3,3) or not np.isfinite(basis).all() or not np.isfinite(rate).all() or not np.allclose(basis.T@basis,np.eye(3),atol=1e-8):
            raise ValueError('finite orthogonal geometric basis and finite derivative required')
        offset=np.asarray(offset_world_m,float)
        velocity=np.asarray(offset_velocity_world_mps,float)
        factors=linearize_raw_range_factors(self.root,row,anchors_m=anchors_m,clock=clock,
            tag_offset_world_m=offset+basis@self.error[n],
            tag_offset_velocity_world_mps=velocity+rate@self.error[n],
            information_weights=information_weights,reference_epoch_s=reference_epoch_s,
            config=config,_enforce_geometry=False)
        ids=np.asarray(factors.anchors,int)
        dt=factors.link_epochs_s-factors.reference_epoch_s
        measured=factors.measured_ranges_m
        sigma=np.sqrt(np.diag(factors.r_prior_m2))
        prior=np.r_[self.root.vector,self.error.ravel()]
        covariance=self.covariance.copy()
        error=np.zeros(39)
        node_slice=slice(9+3*n,12+3*n)
        link_basis=basis[None]+dt[:,None,None]*rate[None]

        def linearize(delta):
            estimate=prior+delta
            tags=(estimate[:3]+offset+dt[:,None]*(estimate[3:6]+velocity)
                  +link_basis@estimate[node_slice])
            vector=tags-np.asarray(anchors_m)[ids]
            distance=np.linalg.norm(vector,axis=1)
            if np.any(distance<=1e-9):
                raise ValueError('singular effective-tag range')
            unit=vector/distance[:,None]
            h=np.zeros((len(ids),39));h[:,:3]=unit;h[:,3:6]=dt[:,None]*unit
            h[:,node_slice]=np.einsum('ni,nij->nj',unit,link_basis)
            innovation=measured-distance
            weight=_robust_weights(innovation,sigma,config)
            return distance,innovation,weight,h

        for iteration in range(1,config.maximum_iterations+1):
            predicted,innovation,weight,h=linearize(error)
            r=np.diag(sigma*sigma/weight)
            gain=np.linalg.solve(h@covariance@h.T+r,h@covariance).T
            updated=gain@(innovation+h@error)
            converged=np.linalg.norm(updated-error)<=config.convergence_tolerance
            error=updated
            if converged:
                break
        predicted,innovation,weight,h=linearize(error)
        singular=np.linalg.svd(h[:,:3].T@((weight/(sigma*sigma))[:,None]*h[:,:3]),compute_uv=False)
        rank=int(np.sum(singular>3*np.finfo(float).eps*singular[0]))
        condition=float(singular[0]/singular[-1]) if rank==3 else np.inf
        accepted=rank==3 and np.isfinite(condition) and condition<=1e10
        if accepted:
            r=np.diag(sigma*sigma/weight)
            gain=np.linalg.solve(h@covariance@h.T+r,h@covariance).T
            ikh=np.eye(39)-gain@h
            posterior=ikh@covariance@ikh.T+gain@r@gain.T
            posterior=(posterior+posterior.T)*.5
            estimate=prior+error
            if not np.isfinite(estimate).all() or not np.isfinite(posterior).all():
                raise FloatingPointError('nonfinite joint discrepancy posterior')
            self.last_error_delta=error[9:].reshape(10,3)
            self.state=JointTagErrorState(float(factors.reference_epoch_s),estimate,posterior)
        return RawRangeDecision(accepted,'ACCEPTED' if accepted else 'SOLVER_OR_GEOMETRY_REJECT',
            tuple(map(int,ids)),factors.link_epochs_s,factors.reference_epoch_s,
            measured,predicted,innovation,innovation/sigma,weight,sigma,rank,condition,
            iteration,config.uncertainty_provenance,np.sqrt(np.diag(factors.sensor_r_m2)))

    def update_tracking(self,row,*,anchors_m,clock,offset_world_m,
                        offset_velocity_world_mps,basis_world_from_local,
                        basis_velocity_world_from_local,reference_epoch_s,
                        information_weights=None,config=RawRangeUpdateConfig(),
                        consider_position=False,transition_observer=None):
        """Single-prior full39 NIS/Joseph; optional root-position consider gain.

        This opt-in route does not use an external range-bias tracker. The
        inherited raw sensor R is unchanged; nuisance uncertainty enters S
        through the full correlated state, never again as independent noise.
        """
        from .root_input_safety import inherited_raw_nis_limit
        config.validate();self.last_error_delta=np.zeros((10,3))
        slots=_valid_slots(row)
        reason=('CLOCK_BOOT_UNAVAILABLE' if row.boot!=clock.boot_epoch else
                'ANCHOR_IDENTITY_MISMATCH' if tuple(row.anchor_ids)!=tuple(range(8)) else
                'NO_VALID_LINKS' if not slots else None)
        if reason:return _empty_decision(reason,config),np.nan,np.nan
        if not config.partial_tracking and len(slots)<4:
            return _empty_decision('FEWER_THAN_FOUR_LINKS',config),np.nan,np.nan
        n=self.nodes.index(row.node)
        basis=np.asarray(basis_world_from_local,float);rate=np.asarray(basis_velocity_world_from_local,float)
        if (basis.shape!=(3,3) or rate.shape!=(3,3) or not np.isfinite(basis).all()
                or not np.isfinite(rate).all() or not np.allclose(basis.T@basis,np.eye(3),atol=1e-8)):
            raise ValueError('invalid geometric nuisance basis')
        def factors(current=None):
            current=self.state if current is None else current
            root=RootState(current.time_s,current.vector[:9],current.covariance[:9,:9])
            error=current.vector[9:].reshape(10,3)[n]
            return linearize_raw_range_factors(root,row,anchors_m=anchors_m,clock=clock,
                tag_offset_world_m=np.asarray(offset_world_m)+basis@error,
                tag_offset_velocity_world_mps=np.asarray(offset_velocity_world_mps)+rate@error,
                information_weights=information_weights,reference_epoch_s=reference_epoch_s,
                config=config,_enforce_geometry=False)
        f=factors();dt=f.link_epochs_s-f.reference_epoch_s
        h=np.zeros((len(f.anchors),39));h[:,:9]=f.state_jacobian
        h[:,9+3*n:12+3*n]=np.einsum('ni,nij->nj',h[:,:3],basis[None]+dt[:,None,None]*rate[None])
        p=self.covariance
        innovation_covariance=h@p@h.T+f.r_prior_m2
        nis=float(f.innovations_m@np.linalg.solve(innovation_covariance,f.innovations_m))
        limit=inherited_raw_nis_limit(len(f.anchors))
        reason=('SOLVER_OR_GEOMETRY_REJECT' if not config.partial_tracking and
                (f.rank<3 or not np.isfinite(f.condition) or f.condition>1e10) else
                'PRIOR_RAW_SWEEP_NIS_REJECT' if nis>limit else 'ACCEPTED')
        sigma=np.sqrt(np.diag(f.r_prior_m2))
        if reason=='ACCEPTED':
            r=np.diag(sigma*sigma/f.robust_weights)
            gain=np.linalg.solve(h@p@h.T+r,h@p).T
            if consider_position:gain[:3]=0.
            delta=gain@f.innovations_m;residual=np.eye(39)-gain@h
            posterior=residual@p@residual.T+gain@r@gain.T
            candidate=JointTagErrorState(float(f.reference_epoch_s),self.state.vector+delta,(posterior+posterior.T)*.5)
            final=factors(candidate)
            self.state=candidate
            self.last_error_delta=delta[9:].reshape(10,3)
            if transition_observer is not None:transition_observer(residual)
        else:final=f
        decision=RawRangeDecision(reason=='ACCEPTED',reason,f.anchors,f.link_epochs_s,
            f.reference_epoch_s,f.measured_ranges_m,final.predicted_ranges_m,final.innovations_m,
            final.innovations_m/sigma,f.robust_weights,sigma,f.rank,f.condition,
            int(reason=='ACCEPTED'),config.uncertainty_provenance,np.sqrt(np.diag(f.sensor_r_m2)))
        return decision,nis,limit
