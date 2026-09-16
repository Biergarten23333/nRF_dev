"""Opt-in finite articulated/contact state; no independent node offsets.

Geometry parity acts on FK vectors/Jacobians, never physical IMU rotations.
Nominal attitude uses the existing hinge projector unless natural_geometry_only
is explicitly selected; that mode keeps physical attitudes unprojected and
caps only endpoint geometry. Contact geometry remains an ankle proxy, not
measured sole contact or antenna-centre metrology.

conditional_imu_heading optionally restricts all measurement gains to six
grouped world-yaw pose corrections while retaining the full 39D covariance.
It conditions on native IMU tilt; it is not an independent gravity observation.
"""
from dataclasses import dataclass
import copy
import numpy as np
from scipy.spatial.transform import Rotation

from .articulated_joint_filter import ArticulatedJointFilter,ArticulatedJointState
from .support_points import SupportPoints
from .root_input_safety import inherited_raw_nis_limit
from .tight_range import RawRangeDecision,RawRangeUpdateConfig,linearize_raw_range_factors,_empty_decision,_valid_slots
from biospur_fusion.c2_uwb_calibration.articulated_range import SEGMENTS,_corrected_proxy_point_jacobians,_skew,_proxy_points_from_rotations
from biospur_fusion.c2_uwb_calibration.frozen_body_proxy import NODE_TO_PROXY_POINT
from biospur_fusion.root_r3.models import RootState
from biospur_fusion.root_r3.estimator import propagate_inertial,propagate_constant_velocity,RootFilterConfig


@dataclass(frozen=True)
class ArticulatedTangent(RootState):
    def __post_init__(self):
        if self.vector.shape!=(39,) or self.covariance.shape!=(39,39):raise ValueError('39D tangent required')
        if not np.isfinite(self.vector).all() or not np.isfinite(self.covariance).all():raise ValueError('nonfinite tangent')
        np.linalg.cholesky(self.covariance)


class ArticulatedContactFilter(ArticulatedJointFilter):
    def __init__(self,*args,embedding,wear_yaw,chest_vertical_m,tilt_restoration=False,
                 contact_leg_only=False,natural_geometry_only=False,conditional_imu_heading=False,
                 restart_stationary_episode=False,raw_structural_heading=False,
                 native_tag_epoch_correction=False,**kwargs):
        if native_tag_epoch_correction and not natural_geometry_only:
            raise ValueError('native tag epoch correction requires natural geometry')
        self.native_tag_epoch_correction=bool(native_tag_epoch_correction)
        if raw_structural_heading and not conditional_imu_heading:
            raise ValueError('raw structural heading requires conditional IMU heading')
        self.raw_structural_heading=bool(raw_structural_heading)
        if conditional_imu_heading and (tilt_restoration or contact_leg_only or not natural_geometry_only):
            raise ValueError('conditional IMU heading requires natural geometry and excludes tilt/leg-only policies')
        self.conditional_imu_heading=bool(conditional_imu_heading)
        if contact_leg_only and tilt_restoration:
            raise ValueError('contact-leg-only policy excludes tilt restoration')
        self.contact_leg_only=bool(contact_leg_only)
        self.natural_geometry_only=bool(natural_geometry_only)
        self.contact_pose_bound_rad=.18
        self.contact_pose_rejected_count=0
        self.contact_policy_audit=[]
        self.tilt_restoration=bool(tilt_restoration)
        self.tilt_sigma=float(kwargs.get('orientation_sigma_rad',.10))
        self.tilt_correlation_s=.125
        super().__init__(*args,**kwargs)
        self.embedding=np.asarray(embedding,float)
        self.wear_yaw=np.asarray(wear_yaw,float)
        for matrix in (self.embedding,self.wear_yaw):
            if matrix.shape!=(3,3) or not np.allclose(matrix.T@matrix,np.eye(3),atol=1e-10):raise ValueError('invalid frame map')
        if np.linalg.det(self.wear_yaw)<0:raise ValueError('physical wear map must be proper')
        self.chest_scale=1.-float(chest_vertical_m)/self.geometry.torso_height_m
        self.contacts=SupportPoints(restart_stationary_episode=restart_stationary_episode)
        self.contacts.cross=np.empty((39,0))
        self.transition_observer=self.contacts.root_transition
        self.last_nis=np.nan;self.last_nis_limit=np.nan
        self.last_contact_routing_audit=None
        self._cache_state=None
        self._point_cache=None;self._jacobian_cache=None;self._tag_cache=None
        self._natural_geometry_cache=None
        self.native_increment=np.tile(np.eye(3),(10,1,1))
        self.native_dt_s=0.

    def _check_cache(self):
        # Every committed propagation/retraction replaces the immutable state.
        # A cache may never survive a state identity change.
        if self._cache_state is not self.state:
            self._cache_state=self.state
            self._point_cache=None;self._jacobian_cache=None;self._tag_cache=None
            self._natural_geometry_cache=None

    def _natural_geometry(self):
        self._check_cache()
        if self._natural_geometry_cache is None:
            from .natural_geometry import natural_geometry
            self._natural_geometry_cache=natural_geometry(self.state.rotations,self.geometry,self.hinges)
        return self._natural_geometry_cache

    def points(self):
        self._check_cache()
        if self._point_cache is None:
            points=self._natural_geometry()[0] if self.natural_geometry_only else super().points()
            self._point_cache={name:self.embedding@point for name,point in points.items()}
        return self._point_cache

    def point_jacobians(self):
        self._check_cache()
        if self._jacobian_cache is None:
            if self.natural_geometry_only:
                jac=self._natural_geometry()[1]
            else:
                jac=_corrected_proxy_point_jacobians(self.mapping(self.state.rotations),
                    {name:np.zeros(3) for name in SEGMENTS},self.geometry,SEGMENTS)
            self._jacobian_cache={name:self.embedding@value for name,value in jac.items()}
        return self._jacobian_cache

    def tags(self):
        self._check_cache()
        if self._tag_cache is None:
            points=self.points()
            self._tag_cache={node:points[name].copy() for node,name in NODE_TO_PROXY_POINT.items()}
            self._tag_cache['BSF31CC']=self.chest_scale*points['shoulder_mid']
        return self._tag_cache

    def tag_jacobian(self,node):
        jac=self.point_jacobians()[NODE_TO_PROXY_POINT[node]]
        return self.chest_scale*jac if node=='BSF31CC' else jac

    def sensor_rotation(self):
        return self.wear_yaw@super().sensor_rotation()

    def _heading_constraints(self):
        if not self.conditional_imu_heading:return None
        from .contact_raw_gain import grouped_heading_constraints
        return grouped_heading_constraints(self.state.rotations)

    def _raw_tag_reference(self,node,reference_s,*,with_jacobian=True):
        from .native_tag_epoch import native_tag_reference
        return native_tag_reference(node=node,rotations=self.state.rotations,
            increment=self.native_increment,native_dt_s=self.native_dt_s,
            native_time_s=self.last_base_time_s,reference_s=reference_s,
            point=self.tags()[node],jacobian=self.tag_jacobian(node) if with_jacobian else None,
            geometry=self.geometry,hinges=self.hinges,embedding=self.embedding,chest_scale=self.chest_scale)

    def project_hinges(self):
        if self.natural_geometry_only:
            # Validate/report geometry only. The physical pose, covariance and
            # contact cross-covariance must never receive the anatomical gauge.
            self.last_projection=self._natural_geometry()[2].copy()
            return
        if not self.hinges:return
        from biospur_fusion.c2_articulated_biomechanics.hinge_linearization import linearize_hinge_projection
        projected,jac,audit=linearize_hinge_projection(self.state.rotations,SEGMENTS,self.hinges)
        transform=np.eye(39);transform[9:,9:]=jac
        self._install(self.state.root.vector,projected,transform@self.state.covariance@transform.T,error_map=transform)
        self.last_projection=audit
        if not audit.get('post_projection_all_inside_rom',False):raise RuntimeError('articulated contact hinge failed')

    def observe_imu_base(self,time_s,base_rotations):
        """Native transaction first half; contact finalizes/project before publish.

        No raw event or output may intervene. Unlike the inherited owner this
        does not project twice around the same native contact observation.
        """
        if time_s<self.last_base_time_s or abs(time_s-self.state.root.time_s)>5e-6:raise ValueError('invalid native base epoch')
        new=np.asarray(base_rotations,float);delta=self.base.swapaxes(1,2)@new
        transform=np.eye(39)
        for i in range(10):transform[9+3*i:12+3*i,9+3*i:12+3*i]=delta[i].T
        self._install(self.state.root.vector,self.state.rotations@delta,transform@self.state.covariance@transform.T,error_map=transform)
        self.native_increment=delta.copy();self.native_dt_s=float(time_s-self.last_base_time_s)
        self.base=new.copy();self.last_base_time_s=float(time_s)
        if self.tilt_restoration:
            from .tilt_correction_process import restore_tilt
            rotations,jac,noise=restore_tilt(self.base,self.state.rotations,self.native_dt_s,
                sigma_rad=self.tilt_sigma,correlation_s=self.tilt_correlation_s,
                yaw_rw_rad_sqrt_s=self.orientation_rw)
            transition=np.eye(39);q=np.zeros((39,39))
            for i in range(10):
                sl=slice(9+3*i,12+3*i);transition[sl,sl]=jac[i];q[sl,sl]=noise[i]
            self._install(self.state.root.vector,rotations,
                transition@self.state.covariance@transition.T+q,error_map=transition)

    def native_motion(self,*,with_jacobian=True):
        """Pure external native increment at the CURRENT corrected nominal.

        The virtual previous pose excludes every estimator retraction, including
        tilt restoration. D is held external; errors at the previous pose are
        D times current right-local errors. This conditions on the native IMU
        increment and does not claim its noise independent of the base state.
        """
        dt=self.native_dt_s
        if dt<=0:return np.zeros((2,3)),np.zeros((2,3,30)),{n:np.zeros(3) for n in NODE_TO_PROXY_POINT}
        current=self.state.rotations;previous=current@self.native_increment.swapaxes(1,2)
        mappings=[self.mapping(r) for r in (previous,current)]
        if self.natural_geometry_only:
            from .natural_geometry import natural_geometry
            geometry=[natural_geometry(r,self.geometry,self.hinges,with_jacobian=with_jacobian)
                      for r in (previous,current)]
            points=[{n:self.embedding@p for n,p in result[0].items()} for result in geometry]
        else:
            points=[{n:self.embedding@p for n,p in _proxy_points_from_rotations(m,self.geometry).items()} for m in mappings]
        names=('ankle_left','ankle_right')
        velocity=np.stack([(points[1][n]-points[0][n])/dt for n in names])
        derivative=None
        if with_jacobian:
            if self.natural_geometry_only:
                jac=[{n:self.embedding@p for n,p in result[1].items()} for result in geometry]
            else:
                jac=[{n:self.embedding@p for n,p in _corrected_proxy_point_jacobians(m,
                     {s:np.zeros(3) for s in SEGMENTS},self.geometry,SEGMENTS).items()} for m in mappings]
            transform=np.zeros((30,30))
            for i in range(10):transform[3*i:3*i+3,3*i:3*i+3]=self.native_increment[i]
            derivative=np.stack([(jac[1][n]-jac[0][n]@transform)/dt for n in names])
        tags={n:(points[1][p]-points[0][p])/dt for n,p in NODE_TO_PROXY_POINT.items()}
        tags['BSF31CC']*=self.chest_scale
        return velocity,derivative,tags

    def update_stationary_velocity(self,eligible,confidence,dt,config):
        """Existing stationary proxy likelihood, with mutable-pose derivative.

        Root position and anchor means are consider states, as in the baseline.
        Full joint Joseph retains their uncertainty and cross-covariance.
        """
        selected=np.asarray(eligible,bool)
        if not np.any(selected):return np.zeros(3),np.zeros((3,3))
        velocity,derivative,_=self.native_motion()
        from .support_velocity import support_velocity_evidence
        target,noise,weights=support_velocity_evidence(-velocity[selected],
            np.asarray(confidence,float)[selected],dt,config)
        candidate=self._candidate();old=candidate.state
        p=candidate.contacts.covariance(candidate.tangent());h=np.zeros((3,len(p)))
        h[:,3:6]=np.eye(3);h[:,9:39]=np.einsum('i,ijk->jk',weights,derivative[selected])
        innovation=target-old.root.velocity_mps;s=h@p@h.T+noise
        gain=np.linalg.solve(s,h@p).T
        if self.conditional_imu_heading:
            from .contact_raw_gain import project_augmented_gain
            gain,_=project_augmented_gain(gain,p,self._heading_constraints(),tuple(range(3))+tuple(range(39,len(p))))
        else:
            gain[:3]=0.;gain[39:]=0.
            if self.contact_leg_only:gain[9:39]=0.
        error=gain@innovation;residual=np.eye(len(p))-gain@h
        posterior=residual@p@residual.T+gain@noise@gain.T
        posterior=(posterior+posterior.T)*.5;np.linalg.cholesky(posterior)
        candidate.contacts.cross=posterior[:39,39:];candidate.contacts.anchor_covariance=posterior[39:,39:]
        # Internal native transaction only: final position-contact update
        # projects once, before any raw observation or publication may occur.
        candidate._inject_range_error(old,error[:39],posterior[:39,:39],project=False)
        if self.contact_leg_only:
            np.linalg.cholesky(candidate.contacts.covariance(candidate.tangent()))
        self.state=candidate.state;self.last_projection=candidate.last_projection
        self.contacts.cross=candidate.contacts.cross;self.contacts.anchor_covariance=candidate.contacts.anchor_covariance
        return innovation,noise

    def validate_native_binding(self,pose,rotations,registered_sensor):
        maximum=0.;maximum_tag=0.
        names=list(map(str,pose['joint_names']));nodes=list(map(str,pose['node_names']))
        for start in range(0,len(rotations),8192):
            stop=min(len(rotations),start+8192);block=rotations[start:stop]
            if not np.isfinite(block).all() or not np.allclose(block.swapaxes(-1,-2)@block,np.eye(3),atol=1e-8) or not np.allclose(np.linalg.det(block),1.,atol=1e-8):raise ValueError('improper native attitudes')
            if self.natural_geometry_only:
                from .natural_geometry import natural_geometry_points_batch
                raw_points=natural_geometry_points_batch(block,self.geometry,self.hinges)
            else:
                raw_points=_proxy_points_from_rotations(dict(zip(SEGMENTS,block.swapaxes(0,1))),self.geometry)
            points={name:value@self.embedding.T for name,value in raw_points.items()}
            predicted=np.stack([points[n] for n in names],axis=1)
            maximum=max(maximum,float(np.max(np.abs(predicted-pose['joints_relative'][start:stop]))))
            tags={n:points[NODE_TO_PROXY_POINT[n]] for n in nodes};tags['BSF31CC']=points['shoulder_mid']*self.chest_scale
            maximum_tag=max(maximum_tag,float(np.max(np.abs(np.stack([tags[n] for n in nodes],axis=1)-pose['node_offsets'][start:stop]))))
            if not np.allclose(self.wear_yaw@block[:,0]@self.mount.T,registered_sensor[start:stop],atol=1e-9,rtol=0):raise ValueError('physical sensor registration mismatch')
        if maximum>1e-9 or maximum_tag>1e-9:raise ValueError('native geometric binding mismatch')
        return dict(frames=len(rotations),maximum_joint_error_m=maximum,maximum_tag_error_m=maximum_tag)

    def tangent(self):
        return ArticulatedTangent(self.state.root.time_s,np.r_[self.state.root.vector,np.zeros(30)],self.state.covariance)

    def propagate_safe(self,hold,target,config=RootFilterConfig()):
        start=self.state.root.time_s
        if target<start:raise ValueError('reversed articulated prediction')
        split=min(target,max(start,hold.time_s+.005))
        for end,inertial in ((split,True),(target,False)):
            old=self.state;dt=end-old.root.time_s
            if dt<=0:continue
            if inertial:
                root,f=propagate_inertial(old.root,end,hold.force,self.sensor_rotation(),config)
            else:root,f=propagate_constant_velocity(old.root,end,config)
            phi=np.eye(39);phi[:9,:9]=f
            if inertial:
                ja=-self.wear_yaw@old.rotations[0]@_skew(self.mount.T@(hold.force-old.root.vector[6:9]))
                phi[:3,9:12]=.5*dt*dt*ja;phi[3:6,9:12]=dt*ja
            q=np.zeros((39,39));q[:9,:9]=root.covariance-f@old.root.covariance@f.T
            if not self.tilt_restoration:q[9:,9:]=np.eye(30)*self.orientation_rw**2*dt
            p=phi@old.covariance@phi.T+q;p=(p+p.T)*.5
            self.state=ArticulatedJointState(RootState(end,root.vector,p[:9,:9]),old.rotations,p)
            self.contacts.root_transition(phi)

    def update_contact(self,offsets,valid,eligible,confidence,dt,moving):
        candidate=self._candidate()
        candidate._update_contact(offsets,valid,eligible,confidence,dt,moving)
        if self.contact_leg_only:
            relative=Rotation.from_matrix(self.base.swapaxes(1,2)@candidate.state.rotations).as_rotvec()
            nonleg=float(np.max(np.linalg.norm(relative[:6],axis=1)))
            maximum=float(np.max(np.linalg.norm(relative[6:],axis=1)))
            accepted=bool(np.isfinite(relative).all() and nonleg<=1e-9 and
                          maximum<=self.contact_pose_bound_rad+1e-12)
            self.contact_policy_audit.append((self.state.root.time_s,accepted,maximum,nonleg))
            if not accepted:
                self.contact_pose_rejected_count+=1
                # Roll back the measurement, never actual support release or
                # elapsed independent anchor diffusion. No sample is reused.
                candidate=self._candidate()
                candidate._update_contact(offsets,valid,np.zeros(2,bool),confidence,dt,moving)
                relative=Rotation.from_matrix(self.base.swapaxes(1,2)@candidate.state.rotations).as_rotvec()
                if (not np.isfinite(relative).all() or
                    np.max(np.linalg.norm(relative[:6],axis=1))>1e-9 or
                    np.max(np.linalg.norm(relative[6:],axis=1))>self.contact_pose_bound_rad+1e-12):
                    raise RuntimeError('contact topology fallback violates native pose bound')
            np.linalg.cholesky(candidate.contacts.covariance(candidate.tangent()))
        self.state=candidate.state;self.last_projection=candidate.last_projection
        history=self.contacts.audit
        history.extend(candidate.contacts.audit)
        entries=self.contacts.stationary_entries
        entries.extend(candidate.contacts.stationary_entries)
        self.contacts.__dict__.update(candidate.contacts.__dict__)
        self.contacts.audit=history
        self.contacts.stationary_entries=entries
        return accepted if self.contact_leg_only else None

    def _candidate(self):
        candidate=copy.copy(self);candidate.contacts=copy.copy(self.contacts)
        for name in ('means','cross','anchor_covariance'):
            setattr(candidate.contacts,name,getattr(self.contacts,name).copy())
        for name in ('sides',):
            setattr(candidate.contacts,name,list(getattr(self.contacts,name)))
        candidate.contacts.audit=[]
        candidate.contacts.stationary_entries=[]
        for name in ('moving_by_side','_episodes','_generations'):
            setattr(candidate.contacts,name,dict(getattr(self.contacts,name)))
        candidate.transition_observer=candidate.contacts.root_transition
        return candidate

    def _update_contact(self,offsets,valid,eligible,confidence,dt,moving):
        old=self.state
        jac=self.point_jacobians();h=np.zeros((2,3,39));h[:,:,:3]=np.eye(3)
        h[0,:,9:]=jac['ankle_left'];h[1,:,9:]=jac['ankle_right']
        mask=None
        if self.contact_leg_only:
            mask=np.ones(39,dtype=bool);mask[9:27]=False
        tangent=self.contacts.update(self.tangent(),offsets,valid,eligible,confidence,dt,moving,
                                     point_jacobians=h,base_gain_row_mask=mask,
                                     base_gain_constraints=self._heading_constraints())
        error=tangent.vector-np.r_[old.root.vector,np.zeros(30)]
        if np.any(error):self._inject_range_error(old,error,tangent.covariance)
        else:
            self._install(old.root.vector,old.rotations,tangent.covariance)
            self.project_hinges()

    def update_ranges(self,row,*,anchors_m,clock,range_bias_m=None,bias_prior=None,
                      reference_epoch_s=None,offset_velocity_world_mps=None,config=RawRangeUpdateConfig(),
                      consider_position=False,preserve_anchor_mean=False,protected_contact_sides=()):
        """Prepare on isolated numerical owner; commit only complete result.

        Nonempty protected_contact_sides is explicit fresh caller authority,
        never inferred from retained contact state. It replaces the root-position
        gain mask with first-order supported-point RAW correction routing and
        holds all anchor means. This includes explicitly authorized moving
        support; it is not a zero-velocity likelihood or a native-motion lock.
        """
        config.validate()
        protected_contact_sides=tuple(protected_contact_sides)
        if (any(isinstance(side,(bool,np.bool_)) or not isinstance(side,(int,np.integer))
                or side not in (0,1) for side in protected_contact_sides)
                or len(set(protected_contact_sides))!=len(protected_contact_sides)):
            raise ValueError('contact routing sides must be distinct 0/1 integers')
        if protected_contact_sides:
            if not self.natural_geometry_only or self.contact_leg_only:
                raise ValueError('contact raw routing requires natural full-pose geometry mode')
            if any(side not in self.contacts.sides or side not in self.contacts._episodes
                   for side in protected_contact_sides):
                raise ValueError('contact raw routing requires an existing active support episode')
        candidate=self._candidate()
        decision=candidate._update_ranges_candidate(row,anchors_m=anchors_m,clock=clock,
            range_bias_m=range_bias_m,bias_prior=bias_prior,reference_epoch_s=reference_epoch_s,
            offset_velocity_world_mps=offset_velocity_world_mps,config=config,
            consider_position=consider_position,preserve_anchor_mean=preserve_anchor_mean,
            protected_contact_sides=protected_contact_sides)
        self.last_nis=candidate.last_nis;self.last_nis_limit=candidate.last_nis_limit
        self.last_orientation_delta=candidate.last_orientation_delta
        self.last_contact_routing_audit=candidate.last_contact_routing_audit
        self.last_contact_routing_audit['decision_reason']=decision.reason
        if decision.accepted:
            self.state=candidate.state;self.last_projection=candidate.last_projection
            self.contacts.means=candidate.contacts.means;self.contacts.cross=candidate.contacts.cross
            self.contacts.anchor_covariance=candidate.contacts.anchor_covariance
        return decision

    def _update_ranges_candidate(self,row,*,anchors_m,clock,range_bias_m,bias_prior,
                      reference_epoch_s,offset_velocity_world_mps,config,consider_position=False,preserve_anchor_mean=False,
                      protected_contact_sides=()):
        self.last_orientation_delta=np.zeros((10,3));self.last_nis=np.nan;self.last_nis_limit=np.nan
        self.last_contact_routing_audit=dict(
            mode='SUPPORTED_POINT_RAW_CORRECTION_ROUTING_DIAGNOSTIC',
            protected_sides=tuple(int(side) for side in protected_contact_sides),
            active_episode_ids=tuple(self.contacts._episodes[side] for side in protected_contact_sides),
            update_applied=False,endpoint_shift_status='NO_UPDATE_APPLIED',
            ankle_endpoint_shift_m=np.zeros((2,3)).tolist(),
            ankle_endpoint_shift_max_m=0.,
            finite_endpoint_shift_m=np.zeros((len(protected_contact_sides),3)).tolist(),
            finite_endpoint_shift_max_m=0.,linear_gain_residual_max=None,
            linear_endpoint_correction_max_m=None,
            finite_endpoint_lock_guaranteed=False)
        if row.boot!=clock.boot_epoch:return _empty_decision('CLOCK_BOOT_UNAVAILABLE',config)
        if tuple(row.anchor_ids)!=tuple(range(8)):return _empty_decision('ANCHOR_IDENTITY_MISMATCH',config)
        if not _valid_slots(row):return _empty_decision('NO_VALID_LINKS',config)
        old=self.state;point=self.tags()[row.node]
        tag_jac=self.tag_jacobian(row.node)
        if self.native_tag_epoch_correction:
            if reference_epoch_s is None:
                reference_epoch_s=float(np.median([clock.seconds(row.strobe_us+.5*row.t_round_us[i])
                    for i in _valid_slots(row)]))
            point,offset_velocity_world_mps,tag_jac,velocity_jac,motion_audit=self._raw_tag_reference(row.node,reference_epoch_s)
            self.last_contact_routing_audit['native_tag_epoch']=motion_audit
        before_ankles=np.stack([old.root.position_m+self.points()[name]
                               for name in ('ankle_left','ankle_right')])
        factors=linearize_raw_range_factors(old.root,row,anchors_m=anchors_m,clock=clock,
            range_bias_m=range_bias_m,bias_prior=bias_prior,tag_offset_world_m=point,
            tag_offset_velocity_world_mps=offset_velocity_world_mps,reference_epoch_s=reference_epoch_s,
            config=config,_enforce_geometry=False)
        h=np.zeros((len(factors.anchors),39+len(self.contacts.means)))
        h[:,:9]=factors.state_jacobian
        if self.native_tag_epoch_correction:
            link_jac=tag_jac[None]+(factors.link_epochs_s-factors.reference_epoch_s)[:,None,None]*velocity_jac[None]
            h[:,9:39]=np.einsum('ni,nij->nj',h[:,:3],link_jac)
        else:h[:,9:39]=h[:,:3]@tag_jac
        p=self.contacts.covariance(self.tangent());r=np.diag(np.diag(factors.r_prior_m2)/factors.robust_weights)
        innovation=factors.innovations_m;s_prior=h@p@h.T+factors.r_prior_m2
        self.last_nis=float(innovation@np.linalg.solve(s_prior,innovation));self.last_nis_limit=inherited_raw_nis_limit(len(innovation))
        if not np.isfinite(self.last_nis) or self.last_nis>self.last_nis_limit:return _empty_decision('RAW_PRIOR_NIS',config)
        s=h@p@h.T+r
        k=np.linalg.solve(s,h@p).T
        protected_names=tuple(('ankle_left','ankle_right')[side] for side in protected_contact_sides)
        if protected_names:
            from .contact_raw_gain import project_contact_gain
            point_jac=self.point_jacobians()
            constraint=np.zeros((len(protected_names),3,39));constraint[:,:,:3]=np.eye(3)
            for i,name in enumerate(protected_names):constraint[i,:,9:]=point_jac[name]
            constraint=constraint.reshape(-1,39)
            if not self.conditional_imu_heading:
                k[:39],projection_audit=project_contact_gain(k[:39],p[:39,:39],constraint)
        if self.conditional_imu_heading:
            from .contact_raw_gain import project_augmented_gain
            combined=self._heading_constraints()
            if self.raw_structural_heading:
                if self.native_tag_epoch_correction:
                    from .native_tag_epoch import temporal_structural_constraints
                    structural,structural_audit=temporal_structural_constraints(old.rotations,tag_jac,velocity_jac)
                else:
                    from .raw_heading_authority import structural_heading_constraints
                    structural,structural_audit=structural_heading_constraints(old.rotations,tag_jac)
                combined=np.vstack((combined,structural))
                self.last_contact_routing_audit['structural_heading_authority']=structural_audit
            if protected_names:combined=np.vstack((combined,constraint))
            zero_rows=tuple(range(3)) if consider_position and not protected_names else ()
            if protected_names or preserve_anchor_mean:zero_rows+=tuple(range(39,len(p)))
            k,projection_audit=project_augmented_gain(k,p,combined,zero_rows)
        else:
            if not protected_names and consider_position:k[:3]=0.
            if protected_names or preserve_anchor_mean or self.contact_leg_only:k[39:]=0.
            if self.contact_leg_only:k[9:39]=0.
        error=k@innovation;a=np.eye(len(p))-k@h
        posterior=a@p@a.T+k@r@k.T;posterior=(posterior+posterior.T)*.5
        np.linalg.cholesky(posterior)
        self.contacts.means+=error[39:];self.contacts.cross=posterior[:39,39:]
        self.contacts.anchor_covariance=posterior[39:,39:]
        if self.contact_leg_only:
            self._inject_range_error(old,error[:39],posterior[:39,:39],project=False)
            np.linalg.cholesky(self.contacts.covariance(self.tangent()))
        else:
            self._inject_range_error(old,error[:39],posterior[:39,:39])
        self.last_orientation_delta=Rotation.from_matrix(old.rotations.swapaxes(1,2)@self.state.rotations).as_rotvec()
        ankle_shifts=np.stack([self.state.root.position_m+self.points()[name]
                              for name in ('ankle_left','ankle_right')])-before_ankles
        self.last_contact_routing_audit.update(update_applied=True,endpoint_shift_status='FINITE_RETRACTION_MEASURED',
            ankle_endpoint_shift_m=ankle_shifts.tolist(),
            ankle_endpoint_shift_max_m=float(np.linalg.norm(ankle_shifts,axis=1).max()),
            root_position_correction_m=(self.state.root.position_m-old.root.position_m).tolist(),
            orientation_correction_norm_rad=float(np.linalg.norm(self.last_orientation_delta)))
        if protected_names:
            np.linalg.cholesky(self.contacts.covariance(self.tangent()))
            shifts=ankle_shifts[list(protected_contact_sides)]
            self.last_contact_routing_audit.update(projection_audit,
                authority='EXPLICIT_CALLER_FRESH_ACTIVE_EPISODES',
                protected_sides=tuple(int(side) for side in protected_contact_sides),
                root_position_mask_replaced=bool(consider_position),anchor_means_protected=True,
                finite_endpoint_shift_m=shifts.tolist(),
                finite_endpoint_shift_max_m=float(np.linalg.norm(shifts,axis=1).max()),
                linear_endpoint_correction_max_m=float(np.abs(constraint@error[:39]).max()))
        after_point=self.tags()[row.node]
        if self.native_tag_epoch_correction:
            after_point,offset_velocity_world_mps,_,_,after_audit=self._raw_tag_reference(row.node,reference_epoch_s,with_jacobian=False)
            self.last_contact_routing_audit['native_tag_epoch_after']=after_audit
        after=linearize_raw_range_factors(self.state.root,row,anchors_m=anchors_m,clock=clock,
            range_bias_m=range_bias_m,bias_prior=bias_prior,tag_offset_world_m=after_point,
            tag_offset_velocity_world_mps=offset_velocity_world_mps,reference_epoch_s=reference_epoch_s,
            config=config,_enforce_geometry=False)
        sigma=np.sqrt(np.diag(factors.r_prior_m2))
        return RawRangeDecision(True,'ACCEPTED',factors.anchors,factors.link_epochs_s,factors.reference_epoch_s,
            factors.measured_ranges_m,after.predicted_ranges_m,after.innovations_m,after.innovations_m/sigma,
            factors.robust_weights,sigma,factors.rank,factors.condition,1,config.uncertainty_provenance,
            np.sqrt(np.diag(factors.sensor_r_m2)))
