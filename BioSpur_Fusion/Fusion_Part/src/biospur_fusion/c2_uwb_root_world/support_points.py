"""Persistent stochastic ankle proxies, with explicit root/anchor covariance.

Root-only observations use Schmidt anchor gain zero. Contact observations use
the ordinary joint Joseph update. Neither convention fixes the world gauge.
"""
import numpy as np
from dataclasses import replace


class SupportPoints:
    def __init__(self, position_sigma_m=.02, correlation_time_s=.125, *, restart_stationary_episode=False):
        if not all(np.isfinite(x) and x > 0 for x in (position_sigma_m,correlation_time_s)):
            raise ValueError('contact position uncertainty must be positive finite')
        self.sigma=position_sigma_m; self.correlation=correlation_time_s
        self.sides=[]; self.means=np.empty(0); self.cross=np.empty((9,0))
        self.anchor_covariance=np.empty((0,0)); self.audit=[]
        self.moving_by_side={}; self.stationary_entries=[]
        self.restart_stationary_episode=bool(restart_stationary_episode)
        self.last_time=None
        self.soft_sigma=.10
        # Engineering diffusion approximation, not exact small-dt OU noise.
        self.soft_diffusion=2*.10**2*.125
        self.tape=None
        self._record_state=None
        self._generations={0:0,1:0}
        self._episodes={}

    def snapshot(self,state):
        from .joint_transition_tape import JointSnapshot
        return JointSnapshot.capture(state.time_s,np.r_[state.vector,self.means],
            self.covariance(state),tuple((s,self._episodes[s]) for s in self.sides))

    def record_root_transition(self,mapping,before,after,kind,*,measurement=None):
        if self.tape is None or not self.tape.enabled:return
        if len(before.vector)!=9 or len(after.vector)!=9:raise ValueError('transition tape supports root9 only')
        prior=self.snapshot(before)
        size=len(prior.mean);a=np.eye(size);a[:9,:9]=mapping
        from .joint_transition_tape import JointSnapshot
        cross=mapping@self.cross
        posterior=JointSnapshot.capture(after.time_s,np.r_[after.vector,self.means],
            np.block([[after.covariance,cross],[cross.T,self.anchor_covariance]]),prior.episodes)
        if measurement is not None:
            h,r,innovation,gain=measurement
            full_h=np.zeros((len(innovation),size));full_h[:,:9]=h
            full_gain=np.zeros((size,len(innovation)));full_gain[:9]=gain
            measurement=(full_h,r,innovation,full_gain)
        self.tape.commit(kind,prior,posterior,a,independent_noise=True,measurement=measurement)
        self._record_state=after

    def root_transition(self, residual_map):
        """Exactly once after a committed root transition, never a trial."""
        a=np.asarray(residual_map,float)
        if a.ndim!=2 or a.shape[0]!=a.shape[1] or a.shape[0] not in (9,39) or not np.isfinite(a).all():
            raise ValueError('invalid root error transition')
        if not self.sides and self.cross.shape[0]!=len(a):self.cross=np.empty((len(a),0))
        if self.cross.shape[0]!=len(a):raise ValueError('support base-state dimension changed')
        self.cross=a@self.cross

    def covariance(self,state):
        return np.block([[state.covariance,self.cross],
                         [self.cross.T,self.anchor_covariance]])

    def release(self,side):
        if side not in self.sides:return
        prior=self.snapshot(self._record_state) if self.tape is not None and self.tape.enabled else None
        k=self.sides.index(side);keep=np.delete(np.arange(len(self.means)),np.arange(3*k,3*k+3))
        self.means=self.means[keep];self.cross=self.cross[:,keep]
        self.anchor_covariance=self.anchor_covariance[np.ix_(keep,keep)]
        self.sides.remove(side)
        self.moving_by_side.pop(side,None)
        self._episodes.pop(side,None)
        if prior is not None:
            indices=np.r_[np.arange(9),9+keep]
            self.tape.commit('topology',prior,self.snapshot(self._record_state),np.eye(len(prior.mean))[indices],independent_noise=True)

    def enter(self,state,side,offset,soft=False,jacobian=None):
        if side in self.sides:raise ValueError('duplicate support entry')
        prior=self.snapshot(state) if self.tape is not None and self.tape.enabled else None
        if not self.sides and self.cross.shape[0]!=len(state.vector):
            self.cross=np.empty((len(state.vector),0))
        if self.cross.shape[0]!=len(state.vector):raise ValueError('support base-state dimension changed')
        # Existing anchors and the new anchor share the same uncertain root.
        j=np.eye(len(state.vector))[:3] if jacobian is None else np.asarray(jacobian,float)
        if j.shape!=(3,len(state.vector)) or not np.isfinite(j).all():raise ValueError('invalid point Jacobian')
        shared=self.cross[:3,:].T if jacobian is None else self.cross.T@j.T
        marginal=state.covariance[:3,:3] if jacobian is None else j@state.covariance@j.T
        self.anchor_covariance=np.block([[self.anchor_covariance,shared],
            [shared.T,marginal+np.eye(3)*(self.soft_sigma if soft else self.sigma)**2]])
        cross=state.covariance[:,:3] if jacobian is None else state.covariance@j.T
        self.cross=np.column_stack((self.cross,cross))
        self.means=np.r_[self.means,state.position_m+np.asarray(offset)]
        self.sides.append(side)
        self.moving_by_side[side]=bool(soft)
        self._episodes[side]=self._generations[side]
        self._generations[side]+=1
        if prior is not None:
            a=np.vstack((np.eye(len(prior.mean)),np.eye(len(prior.mean))[:3]))
            self.tape.commit('topology',prior,self.snapshot(state),a,independent_noise=True)

    def update(self,state,offsets,valid,eligible,confidence,dt,moving=None,point_jacobians=None,
               base_gain_row_mask=None,base_gain_constraints=None):
        # A consider-state policy changes the actual gain before Joseph; it
        # must not discard error components after covariance assimilation.
        if base_gain_row_mask is not None:
            base_gain_row_mask=np.asarray(base_gain_row_mask)
            if base_gain_row_mask.shape!=(len(state.vector),) or base_gain_row_mask.dtype!=np.bool_:
                raise ValueError('base gain row mask must be a boolean base-state vector')
        if base_gain_constraints is not None:
            base_gain_constraints=np.asarray(base_gain_constraints,float)
            if (base_gain_constraints.ndim!=2 or base_gain_constraints.shape[1]!=len(state.vector)
                    or not len(base_gain_constraints) or not np.isfinite(base_gain_constraints).all()):
                raise ValueError('base gain constraints must be finite nonempty base-state rows')
        self._record_state=state
        moving=np.zeros(2,bool) if moving is None else np.asarray(moving,bool)
        elapsed=0. if self.last_time is None else state.time_s-self.last_time
        if elapsed<0:raise ValueError('support point time reversed')
        if elapsed>.0075:
            for side in tuple(self.sides):self.release(side)
        self.last_time=state.time_s
        previous=set(self.sides)
        for side in tuple(self.sides):
            if not valid[side]:self.release(side)
            elif self.restart_stationary_episode and self.moving_by_side[side] and not moving[side]:
                # A diffusing/moving ankle proxy is not an established fixed
                # foothold. Start the new stationary episode at its current
                # uncertain location, not the obsolete moving-point mean.
                self.release(side)
                previous.discard(side)
                self.stationary_entries.append((state.time_s,side))
        for side in np.flatnonzero(valid):
            if int(side) not in self.sides:self.enter(state,int(side),offsets[side],bool(moving[side]),
                None if point_jacobians is None else point_jacobians[side])
            self.moving_by_side[int(side)]=bool(moving[side])
        if 0<elapsed<=.0075:
            for side in self.sides:
                if side in previous and moving[side]:
                    k=self.sides.index(side);sl=slice(3*k,3*k+3)
                    prior=self.snapshot(state) if self.tape is not None and self.tape.enabled else None
                    self.anchor_covariance[sl,sl]+=np.eye(3)*self.soft_diffusion*elapsed
                    if prior is not None:
                        self.tape.commit('prediction',prior,self.snapshot(state),np.eye(len(prior.mean)),independent_noise=True)
        use=[s for s in self.sides if s in previous and eligible[s]]
        before=state.vector.copy();innov=np.empty(0)
        if use and 0<dt<=.0075:
            prior=self.snapshot(state) if self.tape is not None and self.tape.enabled else None
            base_size=len(state.vector)
            p=self.covariance(state);x=np.r_[state.vector,self.means]
            h=np.zeros((3*len(use),len(x)));innov=[];variance=[]
            for j,side in enumerate(use):
                k=self.sides.index(side);sl=slice(3*j,3*j+3)
                if point_jacobians is None:h[sl,:3]=np.eye(3)
                else:h[sl,:base_size]=point_jacobians[side]
                h[sl,base_size+3*k:base_size+3*k+3]=-np.eye(3)
                innov.extend(self.means[3*k:3*k+3]-state.position_m-offsets[side])
                sigma=self.soft_sigma if moving[side] else self.sigma
                variance.extend([sigma**2*max(1.,self.correlation/dt)/max(.1,confidence[side])]*3)
            innov=np.array(innov);r=np.diag(variance)
            gain=np.linalg.solve(h@p@h.T+r,h@p).T
            if base_gain_constraints is not None:
                from .contact_raw_gain import project_augmented_gain
                zero_rows=() if base_gain_row_mask is None else tuple(np.flatnonzero(~base_gain_row_mask))
                gain,_=project_augmented_gain(gain,p,base_gain_constraints,zero_rows)
            elif base_gain_row_mask is not None:
                gain[:base_size][~base_gain_row_mask]=0.
            x+=gain@innov;residual=np.eye(len(x))-gain@h
            p=residual@p@residual.T+gain@r@gain.T;p=(p+p.T)*.5
            np.linalg.cholesky(p)
            state=replace(state,vector=x[:base_size],covariance=p[:base_size,:base_size])
            self.means=x[base_size:];self.cross=p[:base_size,base_size:];self.anchor_covariance=p[base_size:,base_size:]
            if prior is not None:
                self.tape.commit('assimilation',prior,self.snapshot(state),residual,independent_noise=True,
                                 measurement=(h,r,innov,gain))
        self.audit.append((state.time_s,len(self.sides),len(use),
                           float(np.linalg.norm(innov)),*(state.vector-before)[:9],sum(bool(moving[s]) for s in self.sides)))
        self._record_state=state
        return state
