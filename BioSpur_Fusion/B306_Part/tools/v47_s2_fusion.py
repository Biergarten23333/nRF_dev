#!/usr/bin/env python3
"""Causal S2 state machine with solved-position and raw-range estimators."""
from __future__ import annotations

import math
from collections import Counter, deque
from dataclasses import dataclass

import numpy as np

from v47_state_adaptive_fusion import cv_propagate, position_update, zero_velocity_update


S2_STATES = ("INIT", "STATIONARY", "MOTION_SUSPECTED", "MOVING",
             "SETTLING", "PLATFORM_CONFLICT")


def range_jacobian(position_m: np.ndarray, anchor_m: np.ndarray) -> np.ndarray:
    delta = np.asarray(position_m, float) - np.asarray(anchor_m, float)
    distance = float(np.linalg.norm(delta))
    if distance <= 1e-9:
        raise ValueError("range Jacobian undefined at anchor")
    return delta / distance


def corrected_range_m(raw_range_mm: float, anchor_delay_mm: float,
                      tag_delay_mm: float, *, transport_delay_applied: bool = False) -> float:
    if transport_delay_applied:
        raise ValueError("DELAY_DOUBLE_APPLICATION_REFUSED")
    return (float(raw_range_mm) - float(anchor_delay_mm) - float(tag_delay_mm)) / 1000.0


def scalar_range_update(x: np.ndarray, covariance: np.ndarray, measurement_m: float,
                        anchor_m: np.ndarray, variance_m2: float,
                        nis_gate: float) -> tuple[np.ndarray, np.ndarray, float, bool, float]:
    direction = range_jacobian(x[:3], anchor_m)
    predicted = float(np.linalg.norm(x[:3] - anchor_m))
    h = np.zeros((1, 6), float); h[0, :3] = direction
    innovation = float(measurement_m - predicted)
    s = float((h @ covariance @ h.T).item() + variance_m2)
    nis = innovation * innovation / s
    if nis > nis_gate:
        return x, covariance, nis, False, innovation
    gain = (covariance @ h.T / s).reshape(6)
    out_x = x + gain * innovation
    ident = np.eye(6); kh = np.outer(gain, h.reshape(6))
    out_p = (ident-kh) @ covariance @ (ident-kh).T + np.outer(gain, gain) * variance_m2
    out_p = .5 * (out_p + out_p.T)
    return out_x, out_p, nis, True, innovation


def robust_center(points) -> tuple[np.ndarray | None, float]:
    values = np.asarray(list(points), float)
    if values.ndim != 2 or len(values) == 0:
        return None, math.inf
    center = np.median(values, axis=0)
    radial = np.linalg.norm(values-center, axis=1)
    return center, float(1.4826*np.median(radial))


@dataclass(frozen=True)
class S2Parameters:
    position_r_m2: np.ndarray
    range_sigma_m: np.ndarray
    anchors_m: np.ndarray
    anchor_delay_mm: np.ndarray
    tag_delay_mm: float
    gyro_rms_threshold_dps: float
    accel_dev_rms_threshold_g: float
    gyro_std_threshold_dps: float
    accel_std_threshold_g: float
    gyro_angle_1s_threshold_deg: float
    gravity_change_threshold_deg: float
    position_shift_normalized: float = 1.35
    range_shift_normalized: float = 2.5
    candidate_scatter_normalized: float = 2.5
    suspected_confirm_dwell_s: float = .20
    suspected_clear_dwell_s: float = .50
    conflict_enter_dwell_s: float = .75
    conflict_resolve_dwell_s: float = .50
    moving_quiet_dwell_s: float = .75
    settling_dwell_s: float = 1.50
    candidate_window_s: float = 1.50
    min_candidate_positions: int = 6
    min_anchor_support: int = 4
    stationary_accel_sigma_mps2: float = .03
    suspected_accel_sigma_mps2: float = .25
    moving_accel_sigma_mps2: float = 1.0
    settling_accel_sigma_mps2: float = .20
    zupt_sigma_mps: float = .02
    nis_position_gate: float = 16.266236
    nis_range_gate: float = 10.827566
    fleet_context_enabled: bool = False
    platform_conflict_enabled: bool = True
    instantaneous_detector: bool = False
    zupt_enabled: bool = True
    fixed_candidate_scatter_m: float | None = None


class S2Fusion:
    """Shared causal state machine; `mode` selects T4-position or raw ranges."""
    def __init__(self, parameters: S2Parameters, mode: str, threshold_scale: float = 1.0):
        if mode not in ("S2P", "S2R"):
            raise ValueError("mode must be S2P or S2R")
        self.p, self.mode, self.scale = parameters, mode, float(threshold_scale)
        self.state = "INIT"; self.x = np.zeros(6); self.covariance = np.diag([1.,1.,1.,.1,.1,.1])
        self.published_position = np.full(3, np.nan); self.locked_position = None
        self.locked_ranges_m = np.full(8, np.nan)
        self.position_buffer = deque(); self.range_buffer = deque()
        self.last_time_s = None; self.last_control_s = None
        self.suspected_elapsed = self.clear_elapsed = self.conflict_elapsed = 0.
        self.quiet_elapsed = self.settling_elapsed = 0.
        self.transitions=[]; self.audit=[]; self.snapshots=[]; self.control_audit=[]
        self.zupt_updates=0; self.zaru_updates=0; self.reinitializations=0
        self.covariance_min_eigenvalue=math.inf; self.covariance_max_asymmetry=0.
        self.negative_dt=0; self.extreme_dt=0; self.published_motion_while_locked_max_m=0.
        self._last_published = None
        self._last_snapshot_state = None

    def _sigma(self):
        return {"STATIONARY":self.p.stationary_accel_sigma_mps2,
                "MOTION_SUSPECTED":self.p.suspected_accel_sigma_mps2,
                "PLATFORM_CONFLICT":self.p.suspected_accel_sigma_mps2,
                "SETTLING":self.p.settling_accel_sigma_mps2}.get(self.state,self.p.moving_accel_sigma_mps2)

    def _propagate(self,t):
        t=float(t)
        if self.last_time_s is None: self.last_time_s=t; return
        dt=t-self.last_time_s
        if dt < -1e-12: self.negative_dt+=1; raise ValueError("event timestamp reversal")
        if dt > 1.: self.extreme_dt+=1; raise ValueError("extreme event dt")
        self.x,self.covariance=cv_propagate(self.x,self.covariance,max(0.,dt),self._sigma())
        self.last_time_s=t; self._check()

    def _check(self):
        asym=float(np.max(np.abs(self.covariance-self.covariance.T)))
        eig=np.linalg.eigvalsh(.5*(self.covariance+self.covariance.T))
        if not np.isfinite(self.x).all() or not np.isfinite(self.covariance).all() or eig[0] < -1e-10:
            raise FloatingPointError("nonfinite/non-PSD S2 state")
        self.covariance_min_eigenvalue=min(self.covariance_min_eigenvalue,float(eig[0]))
        self.covariance_max_asymmetry=max(self.covariance_max_asymmetry,asym)

    def _trim(self,t):
        cutoff=t-self.p.candidate_window_s
        while self.position_buffer and self.position_buffer[0][0]<cutoff: self.position_buffer.popleft()
        while self.range_buffer and self.range_buffer[0][0]<cutoff: self.range_buffer.popleft()

    def candidate(self,t):
        self._trim(t)
        if len(self.position_buffer)<self.p.min_candidate_positions: return None,math.inf
        return robust_center([p for _,p in self.position_buffer])

    def candidate_ranges(self,t):
        self._trim(t)
        if not self.range_buffer: return np.full(8,np.nan),np.zeros(8,int)
        matrix=np.asarray([r for _,r in self.range_buffer]); out=np.full(8,np.nan); counts=np.zeros(8,int)
        for k in range(8):
            q=np.isfinite(matrix[:,k]); counts[k]=np.sum(q)
            if counts[k]: out[k]=np.median(matrix[q,k])
        return out,counts

    def _transition(self,t,new,reason,evidence):
        if new==self.state: return
        old=self.state; self.state=new
        self.transitions.append({"time_s":float(t),"from_state":old,"to_state":new,
                                 "reason":reason,"evidence":evidence})
        self.suspected_elapsed=self.clear_elapsed=self.conflict_elapsed=0.
        self.quiet_elapsed=self.settling_elapsed=0.
        if new=="MOVING":
            self.covariance[:3,:3]+=self.p.position_r_m2*4
            self.covariance[3:,3:]+=np.eye(3)*.25

    def _initialize_or_relock(self,t,center,reason):
        self.x[:3]=center; self.x[3:]=0.; self.locked_position=center.copy(); self.published_position=center.copy()
        self.covariance=np.zeros((6,6)); self.covariance[:3,:3]=self.p.position_r_m2
        self.covariance[3:,3:]=np.eye(3)*self.p.zupt_sigma_mps**2
        ranges,counts=self.candidate_ranges(t); self.locked_ranges_m=ranges.copy()
        self._transition(t,"STATIONARY",reason,{"anchor_support":int(np.sum(counts>=2))})

    def process_uwb(self,t,t4_position_m,ranges_mm,valid_mask,record_index=-1):
        self._propagate(t)
        t4=np.asarray(t4_position_m,float); ranges=np.full(8,np.nan)
        for k in range(8):
            if int(valid_mask)&(1<<k):
                ranges[k]=corrected_range_m(ranges_mm[k],self.p.anchor_delay_mm[k],self.p.tag_delay_mm)
        if np.isfinite(t4).all(): self.position_buffer.append((float(t),t4.copy()))
        self.range_buffer.append((float(t),ranges.copy())); self._trim(t)
        category="integrity_only"; reason=f"{self.state}_NO_STATE_UPDATE"; nis=math.nan; applied=0
        if self.state in ("MOVING","SETTLING"):
            if self.mode=="S2P":
                self.x,self.covariance,nis,take=position_update(self.x,self.covariance,t4,
                    self.p.position_r_m2,self.p.nis_position_gate,gate=True)
                category="accepted" if take else "rejected"; applied=int(take); reason="POSITION_KF" if take else "POSITION_NIS"
                self.audit.append({"record_index":record_index,"anchor_id":"","time_s":float(t),"category":category,
                    "reason":reason,"nis":nis,"applied":applied,"state":self.state})
            else:
                for aid in range(8):
                    if not np.isfinite(ranges[aid]):
                        self.audit.append({"record_index":record_index,"anchor_id":aid,"time_s":float(t),"category":"invalid",
                            "reason":"INVALID_MASK","nis":"","applied":0,"state":self.state}); continue
                    self.x,self.covariance,nis,take,innovation=scalar_range_update(self.x,self.covariance,ranges[aid],
                        self.p.anchors_m[aid],self.p.range_sigma_m[aid]**2,self.p.nis_range_gate)
                    self.audit.append({"record_index":record_index,"anchor_id":aid,"time_s":float(t),
                        "category":"accepted" if take else "rejected","reason":"RANGE_EKF" if take else "RANGE_NIS",
                        "nis":nis,"innovation_m":innovation,"applied":int(take),"state":self.state})
            self._check()
        else:
            if self.mode=="S2P":
                self.audit.append({"record_index":record_index,"anchor_id":"","time_s":float(t),"category":category,
                    "reason":reason,"nis":"","applied":0,"state":self.state})
            else:
                for aid in range(8):
                    valid=np.isfinite(ranges[aid]); self.audit.append({"record_index":record_index,"anchor_id":aid,"time_s":float(t),
                        "category":"integrity_only" if valid else "invalid","reason":reason if valid else "INVALID_MASK",
                        "nis":"","applied":0,"state":self.state})
        if self.state in ("STATIONARY","MOTION_SUSPECTED","PLATFORM_CONFLICT") and self.locked_position is not None:
            self.published_position=self.locked_position.copy()
        else: self.published_position=self.x[:3].copy()
        if (self._last_published is not None and self.state=="STATIONARY" and
                self._last_snapshot_state=="STATIONARY"):
            self.published_motion_while_locked_max_m=max(self.published_motion_while_locked_max_m,
                float(np.linalg.norm(self.published_position-self._last_published)))
        self._last_published=self.published_position.copy()
        self._last_snapshot_state=self.state
        center,scatter=self.candidate(t); cr,_=self.candidate_ranges(t)
        self.snapshots.append({"time_s":float(t),"state":self.state,"published_m":self.published_position.copy(),
            "internal_m":self.x[:3].copy(),"velocity_mps":self.x[3:].copy(),"candidate_m":None if center is None else center.copy(),
            "candidate_scatter_m":scatter,"candidate_ranges_m":cr.copy()})

    def process_control(self,t,features,sequence_advancing=True,fleet_common_mode=False):
        self._propagate(t); dt=0. if self.last_control_s is None else max(0.,float(t)-self.last_control_s); self.last_control_s=float(t)
        center,scatter=self.candidate(t); cr,counts=self.candidate_ranges(t)
        pos_scale=max(float(np.sqrt(np.trace(self.p.position_r_m2))),1e-6)
        pos_shift=math.inf if center is None or self.locked_position is None else float(np.linalg.norm(center-self.locked_position)/pos_scale)
        valid=np.isfinite(cr)&np.isfinite(self.locked_ranges_m)
        range_shift=float(np.median(np.abs(cr[valid]-self.locked_ranges_m[valid])/self.p.range_sigma_m[valid])) if np.sum(valid)>=self.p.min_anchor_support else 0.
        scatter_norm=scatter/pos_scale
        th=[self.p.gyro_rms_threshold_dps,self.p.accel_dev_rms_threshold_g,
            self.p.gyro_std_threshold_dps,self.p.accel_std_threshold_g]
        vals=[features[k] for k in ("gyro_rms_dps","accel_dev_rms_g","gyro_std_dps","accel_std_g")]
        fast_votes=sum(v>x*self.scale for v,x in zip(vals,th)); fast=fast_votes>=2
        angle=features["gyro_angle_1s_deg"]>self.p.gyro_angle_1s_threshold_deg*self.scale
        gravity=features["gravity_change_deg"]>self.p.gravity_change_threshold_deg*self.scale
        strong_gravity=features["gravity_change_deg"]>3.0*self.p.gravity_change_threshold_deg*self.scale
        imu_any=sequence_advancing and (fast or angle or gravity)
        imu_confirm=sequence_advancing and ((fast and angle) or gravity or fast_votes>=3)
        position_conflict=pos_shift>self.p.position_shift_normalized*self.scale
        range_conflict=range_shift>self.p.range_shift_normalized*self.scale
        spatial=position_conflict and range_conflict
        scatter_ok = (scatter <= self.p.fixed_candidate_scatter_m*self.scale
                      if self.p.fixed_candidate_scatter_m is not None else
                      scatter_norm <= self.p.candidate_scatter_normalized*self.scale)
        stable=(center is not None and scatter_ok and
                np.sum(counts>=2)>=self.p.min_anchor_support)
        # Integrated absolute gyro is valuable evidence for slow movement, but
        # its noise tail must not make a physically quiet node unrelockable.
        # A moved board may settle at a new gravity direction.  Relock uses
        # current short-window quietness, not closeness to the initial pose.
        quiet=sequence_advancing and fast_votes < 3
        if self.p.fleet_context_enabled and fleet_common_mode and not spatial and not gravity:
            imu_confirm=False
        if self.state=="INIT":
            if t>=1. and quiet and stable: self._initialize_or_relock(t,center,"INITIAL_ROBUST_PLATFORM")
        elif self.state=="STATIONARY":
            self.x[3:]=0.; self.x[:3]=self.locked_position
            if self.p.zupt_enabled:
                self.x,self.covariance,_=zero_velocity_update(self.x,self.covariance,self.p.zupt_sigma_mps); self.zupt_updates+=1
            if self.p.instantaneous_detector and imu_confirm and (spatial or strong_gravity):
                self._transition(t,"MOVING","INSTANTANEOUS_IMU_SPATIAL_THRESHOLD",{"fast":fast,"angle":angle,"gravity":gravity,"spatial":spatial})
            elif imu_confirm and (spatial or strong_gravity):
                self._transition(t,"MOTION_SUSPECTED","CAUSAL_IMU_SUSPICION",{"fast":fast,"angle":angle,"gravity":gravity,"spatial":spatial})
            elif spatial and self.p.platform_conflict_enabled:
                self.conflict_elapsed+=dt
                if self.conflict_elapsed>=self.p.conflict_enter_dwell_s:
                    self._transition(t,"PLATFORM_CONFLICT","SHIFT_WITHOUT_IMU_CONFIRMATION",{"pos_shift_norm":pos_shift,"range_shift_norm":range_shift})
            else: self.conflict_elapsed=0.
        elif self.state=="MOTION_SUSPECTED":
            confirmed=imu_confirm and (spatial or strong_gravity)
            self.suspected_elapsed=self.suspected_elapsed+dt if confirmed else 0.
            self.clear_elapsed=self.clear_elapsed+dt if quiet and not spatial else 0.
            if self.suspected_elapsed>=self.p.suspected_confirm_dwell_s:
                self._transition(t,"MOVING","MULTISCALE_IMU_SPATIAL_AGREEMENT",{"fast":fast,"angle":angle,"gravity":gravity,"pos_shift_norm":pos_shift,"range_shift_norm":range_shift})
            elif spatial and not imu_confirm and self.p.platform_conflict_enabled:
                self.conflict_elapsed+=dt
                if self.conflict_elapsed>=self.p.conflict_enter_dwell_s:
                    self._transition(t,"PLATFORM_CONFLICT","SUSPECTED_BECAME_PLATFORM_CONFLICT",{"pos_shift_norm":pos_shift,"range_shift_norm":range_shift})
            elif self.clear_elapsed>=self.p.suspected_clear_dwell_s:
                self._transition(t,"STATIONARY","TRANSIENT_IMU_DISTURBANCE_CLEARED",{})
        elif self.state=="PLATFORM_CONFLICT":
            if not quiet and spatial:
                self.suspected_elapsed+=dt
                if self.suspected_elapsed>=self.p.suspected_confirm_dwell_s:
                    self._transition(t,"MOVING","CONFLICT_RESOLVED_AS_MOTION",{"pos_shift_norm":pos_shift,"range_shift_norm":range_shift})
            elif quiet and stable and spatial:
                self.conflict_elapsed+=dt
                if self.conflict_elapsed>=self.p.conflict_resolve_dwell_s:
                    # Explicit auditable recovery to a verified new platform.
                    self._initialize_or_relock(t,center,"CONFLICT_RESOLVED_NEW_STATIONARY_PLATFORM")
            elif quiet and not spatial:
                self.clear_elapsed+=dt
                if self.clear_elapsed>=self.p.conflict_resolve_dwell_s:
                    self._transition(t,"STATIONARY","CONFLICT_RESOLVED_RF_TRANSIENT",{})
        elif self.state=="MOVING":
            self.quiet_elapsed=(self.quiet_elapsed+dt if quiet and stable else
                                max(0.,self.quiet_elapsed-.25*dt))
            if self.quiet_elapsed>=self.p.moving_quiet_dwell_s:
                self._transition(t,"SETTLING","IMU_QUIET_STABLE_CANDIDATE",{"speed_mps":float(np.linalg.norm(self.x[3:])),"scatter_norm":scatter_norm})
        elif self.state=="SETTLING":
            if imu_confirm and spatial:
                self._transition(t,"MOVING","SETTLING_INTERRUPTED_BY_MOTION_EVIDENCE",{"quiet":quiet,"stable":stable})
            else:
                if self.p.zupt_enabled:
                    self.x,self.covariance,_=zero_velocity_update(self.x,self.covariance,self.p.zupt_sigma_mps); self.zupt_updates+=1
                self.settling_elapsed=(self.settling_elapsed+dt if quiet and stable else
                                       max(0.,self.settling_elapsed-.5*dt))
                if self.settling_elapsed>=self.p.settling_dwell_s and np.linalg.norm(self.x[3:])<=.05:
                    self._initialize_or_relock(t,center,"SETTLED_ROBUST_PLATFORM")
        self.control_audit.append({"time_s":float(t),"state":self.state,"fast_votes":fast_votes,"fast":int(fast),
            "angle":int(angle),"gravity":int(gravity),"imu_confirm":int(imu_confirm),"spatial":int(spatial),
            "quiet":int(quiet),"stable":int(stable),"pos_shift_norm":pos_shift,"range_shift_norm":range_shift,
            "scatter_norm":scatter_norm,"suspected_elapsed_s":self.suspected_elapsed,"conflict_elapsed_s":self.conflict_elapsed,
            "quiet_elapsed_s":self.quiet_elapsed,"settling_elapsed_s":self.settling_elapsed,
            "fleet_common_mode":int(fleet_common_mode)})
        if self.locked_position is not None and self.state in ("STATIONARY","MOTION_SUSPECTED","PLATFORM_CONFLICT"):
            self.published_position=self.locked_position.copy()
        self._check()

    def accounting(self):
        counts=Counter(row["category"] for row in self.audit); reasons=Counter(row["reason"] for row in self.audit)
        return {"categories":dict(sorted(counts.items())),"reasons":dict(sorted(reasons.items())),
                "total":len(self.audit),"closed":sum(counts.values())==len(self.audit)}


def require_full_vector_binding(binding):
    if binding.get("sensor_to_v4_transform_status")!="BOUND":
        raise ValueError("FULL_VECTOR_INERTIAL_PROPAGATION_BLOCKED_FRAME_BINDING")
