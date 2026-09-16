"""Recurrent IMU/FK error state with one joint raw-range likelihood.

The nominal state is root p/v/pelvis-sensor accelerometer bias plus ten
segment rotations. Its 39D covariance uses right-local orientation errors.
No gyro biases or non-pelvis accelerometer biases are estimated. VQF increments
are treated as external orientation inputs, not independent attitude fixes.
This is a diagnostic display-proxy model, not anatomical calibration.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

import numpy as np
from scipy.spatial.transform import Rotation

from biospur_fusion.c2_uwb_calibration.articulated_range import (
    SEGMENTS, corrected_proxy_points, _corrected_proxy_point_jacobians,
    _so3_right_jacobian, _skew,
)
from biospur_fusion.c2_uwb_calibration.frozen_body_proxy import NODE_TO_PROXY_POINT
from biospur_fusion.c2_articulated_biomechanics.orientation_ik import (
    evaluate_hinge_projection_batch,
)
from biospur_fusion.root_r3.estimator import propagate_inertial, RootFilterConfig, _regularize
from biospur_fusion.root_r3.models import RootState
from .tight_range import (
    RawRangeUpdateConfig, RawRangeDecision, linearize_raw_range_factors,
    _valid_slots, _empty_decision, _robust_weights,
)


@dataclass(frozen=True)
class ArticulatedJointState:
    root: RootState
    rotations: np.ndarray
    covariance: np.ndarray

    def __post_init__(self):
        r = np.asarray(self.rotations, float)
        p = np.asarray(self.covariance, float)
        if r.shape != (10,3,3) or p.shape not in ((39,39),(15,15)):
            raise ValueError('joint state requires ten rotations and 39D or 15D covariance')
        if not np.isfinite(r).all() or not np.isfinite(p).all():
            raise ValueError('joint state must be finite')
        if not np.allclose(r.swapaxes(1,2)@r,np.eye(3),atol=1e-8,rtol=0) or not np.allclose(np.linalg.det(r),1,atol=1e-8,rtol=0):
            raise ValueError('joint rotations must be proper')
        if not np.array_equal(self.root.covariance,p[:9,:9]):
            raise ValueError('root marginal is not the joint covariance marginal')


class ArticulatedJointFilter:
    """Numerical state owner; the caller supplies clock-ordered events only."""

    def __init__(self, root, base_rotations, *, geometry, hinges,
                 pelvis_mount_sensor_from_segment, orientation_sigma_rad=.10,
                 orientation_rw_rad_sqrt_s=.01):
        self.geometry, self.hinges = geometry, hinges
        self.mount = np.asarray(pelvis_mount_sensor_from_segment,float).reshape(3,3)
        if not np.allclose(self.mount.T@self.mount,np.eye(3),atol=1e-8,rtol=0) or np.linalg.det(self.mount)<0:
            raise ValueError('pelvis mount must be a proper sensor-from-segment rotation')
        if orientation_sigma_rad<=0 or orientation_rw_rad_sqrt_s<0:
            raise ValueError('orientation uncertainty scales invalid')
        self.orientation_rw = float(orientation_rw_rad_sqrt_s)
        self.base = np.asarray(base_rotations,float).copy()
        covariance = np.zeros((39,39)); covariance[:9,:9]=root.covariance
        covariance[9:,9:]=np.eye(30)*orientation_sigma_rad**2
        self.state = ArticulatedJointState(root,self.base.copy(),covariance)
        self.last_base_time_s = root.time_s
        self.last_orientation_delta = np.zeros((10,3))
        self.last_projection = {}
        self.transition_observer = None

    @staticmethod
    def mapping(rotations):
        return {name:rotations[i] for i,name in enumerate(SEGMENTS)}

    def points(self):
        return corrected_proxy_points(self.mapping(self.state.rotations),
            {name:np.zeros(3) for name in SEGMENTS},self.geometry)

    def sensor_rotation(self):
        return self.state.rotations[0]@self.mount.T

    def _install(self, vector, rotations, covariance, error_map=None):
        covariance = _regularize(covariance,1e-12)
        root = RootState(self.state.root.time_s,vector,covariance[:9,:9].copy())
        self.state = ArticulatedJointState(root,rotations,covariance)
        if error_map is not None and self.transition_observer is not None:
            self.transition_observer(error_map)

    def propagate(self, time_s, force, config=RootFilterConfig()):
        old = self.state
        dt = float(time_s)-old.root.time_s
        if dt < -1e-12:
            raise ValueError('joint prediction reversed time')
        sensor = self.sensor_rotation()
        root, phi9 = propagate_inertial(old.root,time_s,force,sensor,config)
        phi = np.eye(39); phi[:9,:9]=phi9
        # Right segment error enters force through sensor_from_segment mount.
        acceleration_j = -old.rotations[0]@_skew(self.mount.T@(np.asarray(force)-old.root.vector[6:9]))
        phi[:3,9:12]=.5*dt*dt*acceleration_j
        phi[3:6,9:12]=dt*acceleration_j
        process = np.zeros((39,39))
        process[:9,:9]=root.covariance-phi9@old.root.covariance@phi9.T
        process[9:,9:]=np.eye(30)*self.orientation_rw**2*max(dt,0.)
        covariance = _regularize(phi@old.covariance@phi.T+process,1e-12)
        self.state=ArticulatedJointState(RootState(time_s,root.vector,covariance[:9,:9].copy()),old.rotations,covariance)

    def observe_imu_base(self, time_s, base_rotations):
        """Carry the corrected attitude through the next base IMU increment.

        R+ = Rcorrected * (Rbase_old.T * Rbase_new). The local covariance
        rotates by Delta.T; no fresh absolute VQF observation resets feedback.
        """
        if time_s < self.last_base_time_s or abs(time_s-self.state.root.time_s)>5e-6:
            raise ValueError('base attitude does not belong to current prediction epoch')
        new = np.asarray(base_rotations,float)
        delta = self.base.swapaxes(1,2)@new
        rotations = self.state.rotations@delta
        transform=np.eye(39)
        for i in range(10):
            transform[9+3*i:12+3*i,9+3*i:12+3*i]=delta[i].T
        self._install(self.state.root.vector,rotations,transform@self.state.covariance@transform.T,error_map=transform)
        self.base=new.copy(); self.last_base_time_s=float(time_s)
        self.project_hinges()

    def project_hinges(self):
        """Push mean AND covariance through the existing hinge/ROM projector.

        Forward finite differences (1e-6 rad) approximate the local projector
        Jacobian, including one-sided behavior at ROM limits. This is not a
        probabilistically exact constrained distribution at a limit.
        """
        if not self.hinges:
            return
        nominal=self.state.rotations
        base=self.mapping(nominal)
        eps=1e-6
        inputs=np.zeros((31,10,3))
        inputs[1:]=np.eye(30).reshape(30,10,3)*eps
        rows=[]; audit=[]
        for start in range(0,31,16):
            projected, diagnostics=evaluate_hinge_projection_batch(base,
                {name:inputs[start:start+16,i] for i,name in enumerate(SEGMENTS)},self.hinges)
            rows.extend(projected); audit.extend(diagnostics)
        corrections=np.array([[row[name] for name in SEGMENTS] for row in rows])
        projected=nominal[None]@Rotation.from_rotvec(corrections.reshape(-1,3)).as_matrix().reshape(31,10,3,3)
        local=projected[0].swapaxes(1,2)[None]@projected[1:]
        jac=Rotation.from_matrix(local.reshape(-1,3,3)).as_rotvec().reshape(30,30).T/eps
        transform=np.eye(39); transform[9:,9:]=jac
        self._install(self.state.root.vector,projected[0],transform@self.state.covariance@transform.T,error_map=transform)
        self.last_projection=audit[0]
        if not audit[0].get('post_projection_all_inside_rom',False):
            raise RuntimeError('joint posterior hinge projection failed ROM')

    def update_ranges(self, row, *, anchors_m, clock, range_bias_m=None, bias_prior=None,
                      reference_epoch_s=None, offset_velocity_world_mps=None,
                      config=RawRangeUpdateConfig()):
        """One iterated MAP likelihood jointly updates root and orientations."""
        config.validate()
        self.last_orientation_delta=np.zeros((10,3))
        if row.boot!=clock.boot_epoch:
            return _empty_decision('CLOCK_BOOT_UNAVAILABLE',config)
        if tuple(row.anchor_ids)!=tuple(range(8)):
            return _empty_decision('ANCHOR_IDENTITY_MISMATCH',config)
        if len(_valid_slots(row))<4:
            return _empty_decision('FEWER_THAN_FOUR_LINKS',config)
        old=self.state
        point_name=NODE_TO_PROXY_POINT[row.node]
        velocity=np.zeros(3) if offset_velocity_world_mps is None else np.asarray(offset_velocity_world_mps,float)
        factors=linearize_raw_range_factors(old.root,row,anchors_m=anchors_m,clock=clock,
            range_bias_m=range_bias_m,bias_prior=bias_prior,tag_offset_world_m=self.points()[point_name],
            tag_offset_velocity_world_mps=velocity,reference_epoch_s=reference_epoch_s,
            config=config,_enforce_geometry=False)
        ids=np.asarray(factors.anchors,int); dt=factors.link_epochs_s-factors.reference_epoch_s
        sigma=np.sqrt(np.diag(factors.r_prior_m2)); measured=factors.measured_ranges_m
        anchors=np.asarray(anchors_m)[ids]
        size=len(old.covariance)
        error=np.zeros(size)

        def linearize(error):
            points,jac=self._range_points_jacobian(old,error,point_name)
            root=old.root.vector+error[:9]
            tags=root[:3]+points[point_name]+dt[:,None]*(root[3:6]+velocity)
            distance_vector=tags-anchors; predicted=np.linalg.norm(distance_vector,axis=1)
            if np.any(predicted<=1e-9):
                raise FloatingPointError('singular articulated range derivative')
            unit=distance_vector/predicted[:,None]
            h=np.zeros((len(ids),size)); h[:,:3]=unit; h[:,3:6]=dt[:,None]*unit
            h[:,9:]=unit@jac
            innovation=measured-factors.bias_mean_m-predicted
            weight=_robust_weights(innovation,sigma,config)
            return predicted,innovation,weight,h

        # Iterated EKF in the fixed pre-update tangent. No repeated prior or
        # separate root-only update consumes the same raw observation.
        for iteration in range(1,config.maximum_iterations+1):
            predicted,innovation,weight,h=linearize(error)
            r=np.diag(sigma*sigma/weight)
            gain=np.linalg.solve(h@old.covariance@h.T+r,h@old.covariance).T
            next_error=gain@(innovation+h@error)
            step=next_error-error; error=next_error
            if np.linalg.norm(step)<=config.convergence_tolerance:
                break
        predicted,innovation,weight,h=linearize(error)
        design=h[:,:3].T@((weight/(sigma*sigma))[:,None]*h[:,:3])
        singular=np.linalg.svd(design,compute_uv=False)
        rank=int(np.sum(singular>3*np.finfo(float).eps*singular[0]))
        condition=float(singular[0]/singular[-1]) if rank==3 else np.inf
        accepted=rank==3 and np.isfinite(condition) and condition<=1e10
        if accepted:
            r=np.diag(sigma*sigma/weight)
            gain=np.linalg.solve(h@old.covariance@h.T+r,h@old.covariance).T
            ikh=np.eye(size)-gain@h
            covariance=ikh@old.covariance@ikh.T+gain@r@gain.T
            error=self._inject_range_error(old,error,covariance)
            self.last_orientation_delta=Rotation.from_matrix(old.rotations.swapaxes(1,2)@self.state.rotations).as_rotvec()
            # Tracker receives residuals of the actual projected posterior.
            predicted,innovation,weight,h=linearize(error)
        return RawRangeDecision(accepted,'ACCEPTED' if accepted else 'SOLVER_OR_GEOMETRY_REJECT',
            tuple(map(int,ids)),factors.link_epochs_s,factors.reference_epoch_s,
            measured,predicted,innovation,innovation/sigma,weight,sigma,rank,condition,
            iteration,config.uncertainty_provenance,np.sqrt(np.diag(factors.sensor_r_m2)))

    def _range_points_jacobian(self, old, error, point_name):
        base=self.mapping(old.rotations)
        correction={name:error[9+3*i:12+3*i] for i,name in enumerate(SEGMENTS)}
        points=corrected_proxy_points(base,correction,self.geometry,_batch_rotation_conversion=True)
        jac=_corrected_proxy_point_jacobians(base,correction,self.geometry,SEGMENTS)[point_name]
        return points,jac

    def _inject_range_error(self, old, error, covariance, *, project=True):
        orientation_error=error[9:].reshape(10,3)
        rotations=old.rotations@Rotation.from_rotvec(orientation_error).as_matrix()
        reset=np.eye(39)
        for i,delta in enumerate(orientation_error):
            reset[9+3*i:12+3*i,9+3*i:12+3*i]=_so3_right_jacobian(delta)
        self._install(old.root.vector+error[:9],rotations,reset@covariance@reset.T,error_map=reset)
        if project:self.project_hinges()
        actual=old.rotations.swapaxes(1,2)@self.state.rotations
        applied=error.copy()
        applied[9:]=Rotation.from_matrix(actual).as_rotvec().reshape(30)
        return applied


HEADING_GROUP_NAMES=('pelvis','torso','arm_left','arm_right','leg_left','leg_right')
SEGMENT_HEADING_GROUP=np.array([0,1,2,2,3,3,4,4,5,5],dtype=int)


class GravityPreservingHeadingFilter(ArticulatedJointFilter):
    """Root9 plus six persistent world-heading states, conditional on IMU tilt.

    Each elbow/knee pair shares one left world-Z rotation. Thus both gravity
    tilt and the exact input hinge relative rotation are preserved, without
    repeated attitude observations or a projection that could discard tilt.
    Heading uncertainty remains diagnostic, not calibrated VQF covariance.
    """

    def __init__(self, root, base_rotations, *, geometry, hinges,
                 pelvis_mount_sensor_from_segment, orientation_sigma_rad=.10,
                 orientation_rw_rad_sqrt_s=.01):
        super().__init__(root,base_rotations,geometry=geometry,hinges=hinges,
            pelvis_mount_sensor_from_segment=pelvis_mount_sensor_from_segment,
            orientation_sigma_rad=orientation_sigma_rad,
            orientation_rw_rad_sqrt_s=orientation_rw_rad_sqrt_s)
        covariance=np.zeros((15,15));covariance[:9,:9]=root.covariance
        covariance[9:,9:]=np.eye(6)*orientation_sigma_rad**2
        self.heading_rad=np.zeros(6)
        self.state=ArticulatedJointState(root,self.base.copy(),covariance)

    @staticmethod
    def _yaw_rotations(headings):
        vectors=np.zeros((10,3));vectors[:,2]=np.asarray(headings)[SEGMENT_HEADING_GROUP]
        return Rotation.from_rotvec(vectors).as_matrix()

    def propagate(self, time_s, force, config=RootFilterConfig()):
        old=self.state;dt=float(time_s)-old.root.time_s
        if dt < -1e-12:
            raise ValueError('joint prediction reversed time')
        sensor=self.sensor_rotation()
        root,phi9=propagate_inertial(old.root,time_s,force,sensor,config)
        phi=np.eye(15);phi[:9,:9]=phi9
        acceleration_heading=np.cross([0.,0.,1.],sensor@(np.asarray(force)-old.root.vector[6:9]))
        phi[:3,9]=.5*dt*dt*acceleration_heading
        phi[3:6,9]=dt*acceleration_heading
        process=np.zeros((15,15))
        process[:9,:9]=root.covariance-phi9@old.root.covariance@phi9.T
        process[9:,9:]=np.eye(6)*self.orientation_rw**2*max(dt,0.)
        covariance=_regularize(phi@old.covariance@phi.T+process,1e-12)
        self.state=ArticulatedJointState(RootState(time_s,root.vector,covariance[:9,:9].copy()),old.rotations,covariance)

    def observe_imu_base(self, time_s, base_rotations):
        if time_s < self.last_base_time_s or abs(time_s-self.state.root.time_s)>5e-6:
            raise ValueError('base attitude does not belong to current prediction epoch')
        self.base=np.asarray(base_rotations,float).copy()
        rotations=self._yaw_rotations(self.heading_rad)@self.base
        self._install(self.state.root.vector,rotations,self.state.covariance)
        self.last_base_time_s=float(time_s)

    def project_hinges(self):
        # No second hinge projection: shared group rotations preserve the
        # authoritative base's relative matrices and ROM exactly.
        return

    def _range_points_jacobian(self, old, error, point_name):
        rotations=self._yaw_rotations(error[9:])@old.rotations
        base=self.mapping(rotations);zero={name:np.zeros(3) for name in SEGMENTS}
        points=corrected_proxy_points(base,zero,self.geometry,_batch_rotation_conversion=True)
        full=_corrected_proxy_point_jacobians(base,zero,self.geometry,SEGMENTS)[point_name]
        jac=np.zeros((3,6))
        for i,group in enumerate(SEGMENT_HEADING_GROUP):
            # Contract the established right-local FK Jacobian onto world yaw.
            jac[:,group]+=full[:,3*i:3*i+3]@(rotations[i].T@np.array([0.,0.,1.]))
        return points,jac

    def _inject_range_error(self, old, error, covariance):
        self.heading_rad+=error[9:]
        rotations=self._yaw_rotations(error[9:])@old.rotations
        # World-yaw coordinates are additive/commuting: no SO(3) reset needed.
        self._install(old.root.vector+error[:9],rotations,covariance)
        return error
