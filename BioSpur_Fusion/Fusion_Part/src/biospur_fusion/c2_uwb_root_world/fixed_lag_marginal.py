"""Bounded historical root marginals, conditional on the current joint model.

Unlike the diagnostic transition tape, accepted measurement likelihoods
update historical means and covariances. The current filter is never changed.
An independent coherent conditional current mean/covariance is carried through
the live filter's explicit affine prediction and measurement linearizations.
Historical clones never restart from a constrained live posterior.
"""
from collections import deque
from dataclasses import dataclass

import numpy as np


@dataclass
class HistoricalRoot:
    index: int
    time_s: float
    mean: np.ndarray
    covariance: np.ndarray
    cross: np.ndarray
    last_observation_s: float
    required_availability_s: float


class FixedLagMarginal:
    enabled = True

    def __init__(self, horizon_s=.12, maximum_roots=64, maximum_events=256):
        if not np.isfinite(horizon_s) or horizon_s <= 0 or maximum_roots < 1 or maximum_events < 1:
            raise ValueError('invalid fixed-lag bounds')
        self.horizon_s=float(horizon_s)
        self.maximum_roots=int(maximum_roots)
        self.maximum_events=int(maximum_events)
        self.pending=deque()
        self.events=deque()
        self.completed=[]
        self.observation_time_s=-np.inf
        self.information_time_s=-np.inf
        self.availability_time_s=-np.inf
        self.information_availability_s=-np.inf
        self.maximum_pending=0
        self.measurements=0
        self.source_horizon_skips=0
        self.unavailable=0
        self.last_snapshot=None
        self.release_clock_s=-np.inf
        self.shadow_mean=None
        self.shadow_covariance=None

    def advance(self, time_s):
        """Strict comparison keeps all observations exactly at the deadline."""
        if not np.isfinite(time_s):raise ValueError('invalid release time')
        self.release_clock_s=max(self.release_clock_s,float(time_s))
        while self.pending and self.pending[0].time_s+self.horizon_s < time_s:
            row=self.pending.popleft()
            deadline=row.time_s+self.horizon_s
            if row.last_observation_s > deadline:
                self.unavailable+=1
            else:
                self.completed.append((row.index,row.mean,row.covariance,self.release_clock_s,
                                       row.last_observation_s,deadline,row.required_availability_s))

    def add_native(self, index, snapshot):
        self.advance(snapshot.time_s)
        self._check_prior(snapshot)
        self._seed_shadow(snapshot)
        if self.last_snapshot is None:self.last_snapshot=snapshot
        if self.pending and snapshot.time_s <= self.pending[-1].time_s:
            raise ValueError('native history must strictly increase')
        if len(self.pending) >= self.maximum_roots:
            raise RuntimeError('fixed-lag native capacity exhausted')
        self.pending.append(HistoricalRoot(int(index),snapshot.time_s,self.shadow_mean[:9].copy(),
            self.shadow_covariance[:9,:9].copy(),self.shadow_covariance[:9].copy(),
            max(snapshot.time_s,self.information_time_s),self.information_availability_s))
        self.maximum_pending=max(self.maximum_pending,len(self.pending))

    def _seed_shadow(self,snapshot):
        if self.shadow_mean is None:
            self.shadow_mean=snapshot.mean.copy()
            self.shadow_covariance=snapshot.covariance.copy()

    def _check_prior(self, snapshot):
        previous=self.last_snapshot
        if previous is not None and (previous.time_s!=snapshot.time_s or previous.episodes!=snapshot.episodes
                or not np.array_equal(previous.mean,snapshot.mean)
                or not np.array_equal(previous.covariance,snapshot.covariance)):
            raise ValueError('unrecorded joint state or contact episode transition')

    def commit(self, kind, before, after, error_map, *, independent_noise, measurement=None):
        if not independent_noise or after.time_s < before.time_s:
            raise ValueError('invalid committed transition chronology')
        self._check_prior(before)
        self._seed_shadow(before)
        if kind!='prediction' and before.time_s!=after.time_s:
            raise ValueError('measurement/topology must share an epoch')
        if kind!='topology' and before.episodes!=after.episodes:
            raise ValueError('contact episode change requires topology map')
        self.advance(after.time_s)
        while self.events and self.events[0] < after.time_s-self.horizon_s:
            self.events.popleft()
        if len(self.events)>=self.maximum_events:
            raise RuntimeError('fixed-lag event capacity exhausted')
        self.events.append(after.time_s)
        mapping=np.asarray(error_map,float)
        if mapping.shape!=(len(after.mean),len(before.mean)) or not np.isfinite(mapping).all():
            raise ValueError('invalid explicit joint transition')
        if kind in ('prediction','topology'):
            if measurement is not None:raise ValueError('prediction cannot assimilate data')
            noise=after.covariance-mapping@before.covariance@mapping.T
            noise=(noise+noise.T)*.5
            tolerance=1e-10*max(1.,float(np.linalg.norm(after.covariance,ord=np.inf)))
            if np.linalg.eigvalsh(noise).min() < -tolerance:
                raise ValueError('explicit prediction/topology noise is not positive semidefinite')
            self.shadow_mean=after.mean+mapping@(self.shadow_mean-before.mean)
            self.shadow_covariance=mapping@self.shadow_covariance@mapping.T+noise
            self.shadow_covariance=(self.shadow_covariance+self.shadow_covariance.T)*.5
            for row in self.pending:row.cross=row.cross@mapping.T
            self.last_snapshot=after
            return
        if kind!='assimilation' or measurement is None:
            raise ValueError('historical update requires actual measurement likelihood')
        h,r,innovation,current_gain=map(lambda x:np.asarray(x,float),measurement)
        n=len(before.mean);m=len(innovation)
        if (h.shape!=(m,n) or r.shape!=(m,m) or current_gain.shape!=(n,m)
                or not all(np.isfinite(x).all() for x in (h,r,innovation,current_gain))):
            raise ValueError('invalid measurement evidence')
        if not np.allclose(mapping,np.eye(n)-current_gain@h,atol=1e-12,rtol=1e-12):
            raise ValueError('measurement gain disagrees with committed transition')
        observation=max(after.time_s,self.observation_time_s)
        self.information_time_s=max(self.information_time_s,observation)
        self.information_availability_s=max(self.information_availability_s,self.availability_time_s,after.time_s)
        p=self.shadow_covariance
        conditional_innovation=innovation-h@(self.shadow_mean-before.mean)
        s=h@p@h.T+r
        np.linalg.cholesky(s)
        shadow_gain=np.linalg.solve(s,h@p).T
        shadow_map=np.eye(n)-shadow_gain@h
        # One factorization for all historical roots; no cadence reduction.
        cross=np.stack([row.cross for row in self.pending]) if self.pending else np.empty((0,9,n))
        gains=np.linalg.solve(s,h@cross.reshape(-1,n).T).T.reshape(-1,9,m)
        for row,gain in zip(self.pending,gains):
            if self.information_time_s > row.time_s+self.horizon_s:
                # Zero historical gain, not an invented mean correction.
                # The coherent shadow current gain still transports cross.
                row.cross=row.cross@shadow_map.T
                self.source_horizon_skips+=1
                continue
            row.mean=row.mean+gain@conditional_innovation
            row.covariance=row.covariance-gain@s@gain.T
            row.covariance=(row.covariance+row.covariance.T)*.5
            row.cross=row.cross-gain@h@p
            row.last_observation_s=max(row.last_observation_s,self.information_time_s)
            row.required_availability_s=max(row.required_availability_s,self.information_availability_s)
        self.shadow_mean=self.shadow_mean+shadow_gain@conditional_innovation
        self.shadow_covariance=shadow_map@p@shadow_map.T+shadow_gain@r@shadow_gain.T
        self.shadow_covariance=(self.shadow_covariance+self.shadow_covariance.T)*.5
        np.linalg.cholesky(self.shadow_covariance)
        self.measurements+=1
        self.last_snapshot=after

    def arrays(self, origin_s=0.):
        rows=self.completed
        return dict(lag_native_index=np.array([x[0] for x in rows],dtype=np.int64),
            lag_root_state=np.array([x[1] for x in rows]).reshape(-1,9),
            lag_root_covariance=np.array([x[2] for x in rows]).reshape(-1,9,9),
            lag_emission_time_s=np.array([x[3]+origin_s for x in rows]),
            lag_last_observation_time_s=np.array([x[4]+origin_s for x in rows]),
            lag_deadline_time_s=np.array([x[5]+origin_s for x in rows]),
            lag_required_availability_time_s=np.array([x[6]+origin_s for x in rows]),
            lag_earliest_information_ready_time_s=np.array([max(x[3],x[6])+origin_s for x in rows]))

    def diagnostic(self):
        return dict(scope='COHERENT_SHADOW_FIXED_LAG_CONDITIONAL_ON_LIVE_AFFINE_MODELS',
            horizon_s=self.horizon_s,published=len(self.completed),tail_unavailable=len(self.pending),
            information_unavailable=self.unavailable,maximum_pending=self.maximum_pending,
            measurements=self.measurements,source_horizon_skips=self.source_horizon_skips,
            shadow_dimension=0 if self.shadow_mean is None else len(self.shadow_mean),
            emission_clock='MODEL_TIME_FINALIZATION_NOT_ARRIVAL_ORDERED_REPLAY',
            readiness_clock='MAX_MODEL_FINALIZATION_AND_REQUIRED_AVAILABILITY_LOWER_BOUND')
