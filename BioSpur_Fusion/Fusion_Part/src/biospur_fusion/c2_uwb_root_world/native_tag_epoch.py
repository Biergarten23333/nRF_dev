"""Causal native-relative tag motion at an intertick raw reference epoch.

Uses the existing backward native increment, not estimator correction steps.
The native secant is conditional on the measured IMU increment; its uncertainty
is not asserted independent of the pose prior. No speed or displacement cap.
"""
from dataclasses import dataclass
import numpy as np

from .natural_geometry import natural_geometry
from .raw_heading_authority import structural_heading_constraints
from biospur_fusion.c2_uwb_calibration.frozen_body_proxy import NODE_TO_PROXY_POINT


def native_tag_freshness(native_dt_s,native_time_s,reference_s):
    """Shared native-motion validity; no independent antenna freshness policy."""
    times=np.asarray([native_dt_s,native_time_s,reference_s],float)
    if not np.isfinite(times).all():raise ValueError('native tag epochs must be finite')
    age=float(reference_s-native_time_s)
    if age < -1e-9:raise ValueError('raw tag reference precedes current native pose')
    age=max(0.,age)
    reason=('NO_NATIVE_INCREMENT' if native_dt_s<=0 else
            'NATIVE_SAMPLE_GAP' if native_dt_s>.0075 else
            'NATIVE_HOLD_EXPIRED' if age>.005+1e-12 else 'FRESH_NATIVE_SECANT')
    return age,reason


@dataclass(frozen=True)
class NativeTagSnapshot:
    """Copied native-epoch points/secants for a strictly prior history entry.

    The history owner must select an entry published strictly before the first
    raw link; native_time_s is the physical point epoch, NOT publication time.
    Querying never substitutes current filter state or advances this snapshot.
    Arrays can contain all ten tags, avoiding repeated FK evaluations.
    """
    points: np.ndarray
    velocities: np.ndarray
    native_time_s: float
    native_dt_s: float

    def __post_init__(self):
        points=np.array(self.points,dtype=float,copy=True)
        velocities=np.array(self.velocities,dtype=float,copy=True)
        if (points.ndim!=2 or points.shape[1]!=3 or not len(points)
                or velocities.shape!=points.shape or not np.isfinite(points).all()
                or not np.isfinite(velocities).all()):
            raise ValueError('native tag snapshot requires matching finite Nx3 arrays')
        native_tag_freshness(self.native_dt_s,self.native_time_s,self.native_time_s)
        points.flags.writeable=False;velocities.flags.writeable=False
        object.__setattr__(self,'points',points);object.__setattr__(self,'velocities',velocities)
        object.__setattr__(self,'native_time_s',float(self.native_time_s))
        object.__setattr__(self,'native_dt_s',float(self.native_dt_s))

    def point_at(self,index,reference_s):
        if (isinstance(index,(bool,np.bool_)) or not isinstance(index,(int,np.integer))
                or not 0<=index<len(self.points)):
            raise ValueError('native tag snapshot index is invalid')
        age,reason=native_tag_freshness(self.native_dt_s,self.native_time_s,reference_s)
        return self.points[index]+(age*self.velocities[index] if reason=='FRESH_NATIVE_SECANT' else 0.)


def native_tag_reference(*,node,rotations,increment,native_dt_s,native_time_s,
                         reference_s,point,jacobian,geometry,hinges,embedding,chest_scale):
    """Return reference point, secant velocity and consistent right-local J.

    Current point/J are supplied by the same geometry owner. The virtual prior
    pose R_previous=R_current D.T excludes every estimator correction. Under
    a right-local current perturbation, its error is D delta; hence
    J_velocity=(J_current-J_previous blockdiag(D))/native_dt.
    """
    age,reason=native_tag_freshness(native_dt_s,native_time_s,reference_s)
    velocity=np.zeros(3);velocity_jac=None if jacobian is None else np.zeros((3,30))
    if reason=='FRESH_NATIVE_SECANT':
        previous=np.asarray(rotations)@np.asarray(increment).swapaxes(-1,-2)
        p,j,_=natural_geometry(previous,geometry,hinges,with_jacobian=jacobian is not None)
        name=NODE_TO_PROXY_POINT[node];scale=chest_scale if node=='BSF31CC' else 1.
        previous_point=scale*np.asarray(embedding)@p[name]
        velocity=(np.asarray(point)-previous_point)/native_dt_s
        if jacobian is not None:
            transform=np.zeros((30,30))
            for i in range(10):transform[3*i:3*i+3,3*i:3*i+3]=increment[i]
            previous_jac=scale*np.asarray(embedding)@j[name]
            velocity_jac=(np.asarray(jacobian)-previous_jac@transform)/native_dt_s
    reference_point=np.asarray(point)+age*velocity
    reference_jac=None if jacobian is None else np.asarray(jacobian)+age*velocity_jac
    audit=dict(native_time_s=float(native_time_s),reference_time_s=float(reference_s),
        native_dt_s=float(native_dt_s),reference_age_s=age,motion_status=reason,
        reference_advance_m=(age*velocity).tolist(),relative_tag_velocity_mps=velocity.tolist(),
        per_link_extrapolation='EXISTING_LINEAR_LINK_OFFSET_FROM_RAW_REFERENCE_NO_NEW_CAP',
        stale_policy='HOLD_TAG_POINT_ZERO_RELATIVE_VELOCITY_AFTER_NATIVE_GAP_OR_HOLD_EXPIRY')
    return reference_point,velocity,reference_jac,velocity_jac,audit


def temporal_structural_constraints(rotations,reference_jacobian,velocity_jacobian):
    """A zero instantaneous heading sensitivity need not imply zero motion J."""
    rows,audit=structural_heading_constraints(rotations,reference_jacobian)
    _,motion=structural_heading_constraints(rotations,velocity_jacobian)
    considered=[g for g in audit['considered_groups'] if g in motion['considered_groups']]
    rows=rows[[g in considered for g in audit['considered_groups']]]
    audit.update(reference_considered_groups=audit['considered_groups'],
        considered_groups=considered,
        sensitive_groups=[g for g in audit['heading_group_names'] if g not in considered],
        native_velocity_sensitivity_norm_per_rad=motion['sensitivity_norm_m_per_rad'],
        native_velocity_numerical_zero_tolerance=motion['numerical_zero_tolerance_m_per_rad'])
    return rows,audit
