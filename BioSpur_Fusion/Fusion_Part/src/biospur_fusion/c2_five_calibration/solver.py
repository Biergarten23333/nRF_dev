"""Offline multi-frame inertial fitting using SIP's observation structure.

This adapts the published orientation/acceleration/prior objective; it does not
claim SIP reproduction. Global translation cancels by subtracting pelvis IMU.
No action labels or prescribed action angles enter pose inference.
"""
import time
import numpy as np
from scipy.optimize import lsq_linear
from scipy.spatial.transform import Rotation
import torch
import torch.nn.functional as F

from .geometry import OBSERVED, joints_from_global, sensor_positions
from .tracking import PoseTrackingPrior
from .operators import HZ, WIDTH, ACCELERATION_WIDTHS, filtered, multiscale
from .body_feasibility import BodyFeasibility
from .acceleration_metric import metric_residual


def valid_support(valid):
    return np.convolve(np.asarray(valid,dtype=int),np.ones(WIDTH,dtype=int),'valid') == WIDTH


def acceleration_residual(rotation, acceleration, geometry, levers):
    # Both sides remain expressed in world axes. Subtracting the pelvis
    # SENSOR position cancels common translation without declaring a rotating
    # pelvis frame inertial. sensor_positions includes R_pelvis * lever_pelvis;
    # its second derivative includes the front mounting's rotational terms.
    # Do not rotate positions into pelvis axes before differentiation unless
    # the corresponding non-inertial transport terms are also introduced.
    relative_acc = acceleration-acceleration[:,:1]
    return (multiscale(sensor_positions(rotation,geometry,levers))
            -multiscale(relative_acc,acceleration=True))[:,:,1:]


def bend_cosines(rotation, geometry):
    j = joints_from_global(rotation,geometry)
    values = []
    for a,b,c in ((16,18,20),(17,19,21),(1,4,7),(2,5,8)):
        u,v = j[:,b]-j[:,a],j[:,c]-j[:,b]
        values.append((F.normalize(u,dim=-1)*F.normalize(v,dim=-1)).sum(-1))
    return torch.stack(values,-1)


class PoseObjective:
    """One pose-energy owner; recreate targets after each calibrated replay.

    Transported observations with these frozen targets form only a local
    surrogate, never an independently refreshed learned prior.
    """
    def __init__(self, prior, observed, acceleration, valid, time_s, geometry):
        from .anatomy import JointModel
        self.prior,self.observed,self.acceleration = [
            torch.as_tensor(x,dtype=torch.float64) for x in (prior,observed,acceleration)]
        if len(time_s)!=len(self.prior) or not np.allclose(np.diff(time_s),1/HZ,atol=1e-6):
            raise ValueError('physical solver requires the original uniform 20 Hz grid')
        valid = np.asarray(valid)
        if valid.shape != (len(self.prior),) or valid.dtype != np.bool_:
            raise ValueError('physical solver requires one boolean validity per frame')
        self.valid = torch.as_tensor(valid)
        self.valid_pairs = self.valid[1:] & self.valid[:-1]
        self.good=torch.as_tensor(valid_support(valid))
        if self.good.sum()<20:
            raise ValueError('insufficient uninterrupted derivative support')
        self.geometry=geometry
        self.body=BodyFeasibility(geometry)
        self.conditioned_arms=None
        if geometry.get('conditioned_arm_prior') is not None:
            from .conditioned_arms import ConditionedArms
            self.conditioned_arms=ConditionedArms.from_spec(geometry['conditioned_arm_prior'])
        self.model=JointModel(geometry)
        self.target=self.model.prior_target(self.prior,self.observed)
        self.tracking=PoseTrackingPrior(self.target,geometry)
        self.initial,self.seed_audit=self.model.initial(self.prior,self.observed,return_diagnostics=True)

    def parameters_from_rotation(self, rotation):
        """Re-express a feasible warm start without moving its tracking target."""
        prior,observed,model=self.prior,self.observed,self.model
        previous=torch.as_tensor(rotation,dtype=prior.dtype)
        if (previous.shape!=prior.shape or not torch.isfinite(previous).all()
                or not torch.allclose(previous[:,OBSERVED],observed,atol=1e-6,rtol=0)):
            raise ValueError('warm start must preserve the five observed rotations')
        if (not torch.allclose(previous@previous.transpose(-1,-2),torch.eye(3,dtype=prior.dtype),atol=1e-5,rtol=0)
                or not torch.allclose(torch.linalg.det(previous),torch.ones(previous.shape[:2],dtype=prior.dtype),atol=1e-5,rtol=0)):
            raise ValueError('warm start requires proper rotations')
        guess=model.initial(previous,observed)
        guess[:,:3]=torch.from_numpy(Rotation.from_matrix(
            (previous[:,9]@prior[:,9].transpose(-1,-2)).numpy()).as_rotvec())
        return guess

    def evaluate(self, parameters, levers, *, observed=None, acceleration=None,
                 refresh_projection=False, projection_gap=False, residual_blocks=None):
        from .anatomy import PROXIMAL, TORSO
        if refresh_projection and getattr(self,'_frozen_window',False):
            raise ValueError('fixed window must preserve whole-tape seed and projection')
        observed=self.observed if observed is None else observed
        acceleration=self.acceleration if acceleration is None else acceleration
        if projection_gap and not refresh_projection:
            raise ValueError('calibration projection gap requires candidate-dependent projection')
        if refresh_projection:
            seed=self.model.initial(self.prior,observed)
            target=self.model.rotation(self.prior,observed,seed)
            tracking=PoseTrackingPrior(target,self.geometry,detach_target=False)
        else:
            seed,target,tracking=self.initial,self.target,self.tracking
        rotation=self.model.rotation(self.prior,observed,parameters)
        residual=acceleration_residual(rotation,acceleration,self.geometry,levers)[self.good]
        weighted_residual=metric_residual(residual,self.geometry,node_axis=2)
        scale_loss=(weighted_residual/.75).square().mean(dim=(0,2,3))
        acc_loss=scale_loss.mean()
        prior_loss=((rotation[self.valid][:,PROXIMAL+TORSO]-target[self.valid][:,PROXIMAL+TORSO])/.5).square().mean()
        # Experimental calibration evidence from the part of the learned
        # proximal orientation discarded by geometric projection. This is
        # correlated pose-prior information, never an extra measurement.
        gap_loss=(((target[self.valid][:,PROXIMAL]-self.prior[self.valid][:,PROXIMAL])/.5).square().mean()
                  if projection_gap else prior_loss*0.)
        position_loss,velocity_loss,position_rms=tracking.losses(
            rotation,self.geometry,lambda p:filtered(p,1),self.good,position_valid=self.valid,residual_blocks=residual_blocks)
        change=parameters-seed
        smooth_loss=((change[1:]-change[:-1])[self.valid_pairs]/.12).square().mean()
        structure=self.body.evaluate(rotation,self.valid,residual_blocks=residual_blocks)
        from .axial_limits import axial_terms
        axial=axial_terms(parameters,self.valid,self.geometry,residual_blocks=residual_blocks)
        conditioned_loss=(self.conditioned_arms.loss(joints_from_global(rotation,self.geometry),self.valid,residual_blocks=residual_blocks)
            if self.conditioned_arms is not None else prior_loss*0.)
        from .residual_blocks import record_mean
        record_mean(residual_blocks, 'acceleration', weighted_residual/.75)
        record_mean(residual_blocks, 'angular_prior', (rotation[self.valid][:,PROXIMAL+TORSO]-target[self.valid][:,PROXIMAL+TORSO])/.5, .15)
        if projection_gap:
            record_mean(residual_blocks, 'projection_gap', (target[self.valid][:,PROXIMAL]-self.prior[self.valid][:,PROXIMAL])/.5, .15)
        record_mean(residual_blocks, 'pose_smoothness', (change[1:]-change[:-1])[self.valid_pairs]/.12, .1)
        loss=conditioned_loss+acc_loss+.15*(prior_loss+gap_loss+position_loss+velocity_loss)+.1*smooth_loss+structure['body_loss']+axial['axial_loss']
        return rotation,dict(loss=loss,acceleration_loss=acc_loss,conditioned_arm_loss=conditioned_loss,
            **axial,
            body_loss=structure['body_loss'],body_violation=structure['body_violation'],
            acceleration_loss_by_scale=scale_loss,
            acceleration_rms_mps2=residual.square().mean().sqrt(),
            prior_position_rms_m=position_rms,projection_gap_loss=gap_loss,
            angular_prior_loss=prior_loss,position_prior_loss=position_loss,
            velocity_prior_loss=velocity_loss,
            weighted_pose_prior_loss=.15*(prior_loss+gap_loss+position_loss+velocity_loss),
            weighted_pose_smoothness_loss=.1*smooth_loss)


def solve_pose(prior, observed, acceleration, valid, time_s, geometry, levers,
               *, iterations=150, wall_limit_s=120., initial_rotation=None):
    """Identical label-free joint solver for calibration and runtime."""
    from .anatomy import FLEXION
    from .optimization import optimize_pose
    start = time.monotonic()
    objective=PoseObjective(prior,observed,acceleration,valid,time_s,geometry)
    prior,observed,acceleration=objective.prior,objective.observed,objective.acceleration
    good,model=objective.good,objective.model
    initial,seed_audit=objective.initial,objective.seed_audit
    guess=initial.clone()
    if initial_rotation is not None:
        guess=objective.parameters_from_rotation(initial_rotation)
    best_parameters,history,optimizer_audit=optimize_pose(objective,levers,guess,
        iterations=iterations,wall_limit_s=wall_limit_s-(time.monotonic()-start))
    r,terms=objective.evaluate(best_parameters,levers)
    return r.numpy(),dict(history=history,wall_s=time.monotonic()-start,
        axial_excess_max_deg=float(torch.rad2deg(terms['axial_excess_rad']).detach()),
        body_feasibility=objective.body.audit(r,objective.valid),
        valid_derivative_frames=int(good.sum()),observed_rotation_max_element_error=float((r[:,OBSERVED]-observed).abs().max()),
        valid_pose_frames=int(objective.valid.sum()),
        valid_correction_pairs=int(objective.valid_pairs.sum()),
        invalid_pose_policy='exclude from pose priors and correction smoothness; derivative windows require complete valid support',
        joint_flexion_range_deg=[float(torch.rad2deg(best_parameters[:,FLEXION]).min()),float(torch.rad2deg(best_parameters[:,FLEXION]).max())],
        iterations=iterations,**optimizer_audit,action_labels_consumed=False,
        optimizer_warm_started=initial_rotation is not None,
        prior_target_frames='observed IMUs, shared torso and geometric positive-hinge projection of learned proximal prior',
        pose_prior='joint rotation, root-relative position and velocity; correlated learned prior, not new observations',
        rest_bone_geometry_preserved=True,neutral_rest_rotations_preserved=False,
        joint_angle_semantics='geometric long-axis bend; zero means collinear bones, not identity SMPL joint rotation',
        joint_rom_deg=np.rad2deg(model.maximum_bend.numpy()).tolist(),
        geometric_seed=seed_audit,
        unsupported_seed_plane_policy='functional-plane prior; no measured proximal hinge is fabricated',
        torso_model='one shared torso rotation, following mature C2 rigid shoulder-anchor FK',
        acceleration_operator='two matched bandwidths, equal average energy, same centres and gap support',
        acceleration_window_widths=list(ACCELERATION_WIDTHS),
        acceleration_scales_independent_measurements=False,
        joint_model='elbow flexion/axial rotation; knee hinge approximation',
        inference='offline; calibrated retained orientations and learned proximal prior')


def lever_system(rotation, acceleration, valid, geometry):
    r,a = [torch.as_tensor(x,dtype=torch.float64) for x in (rotation,acceleration)]
    j = joints_from_global(r,geometry)[:,OBSERVED]
    target = multiscale(a-a[:,:1],acceleration=True)-multiscale(j-j[:,:1])
    dd = multiscale(r[:,OBSERVED]).numpy()
    matrix = np.zeros((len(dd),len(ACCELERATION_WIDTHS),4,3,15))
    for k in range(1,5):
        matrix[:,:,k-1,:,0:3] = -dd[:,:,0]
        matrix[:,:,k-1,:,k*3:k*3+3] = dd[:,:,k]
    keep = valid_support(valid)
    matrix=metric_residual(torch.from_numpy(matrix),geometry,node_axis=2).numpy()
    target=metric_residual(target[:,:,1:],geometry,node_axis=2).numpy()
    return matrix[keep].reshape(-1,15),target[keep].reshape(-1)


def fit_levers(systems, nominal, *, radius=.04, regularization_sigma=.025):
    # Two correlated filtered versions must not double information against
    # the offset prior. Match PoseObjective's average over the scale axis.
    weight = 1/np.sqrt(len(ACCELERATION_WIDTHS))
    design = np.concatenate([a for a,b in systems])*weight
    target = np.concatenate([b for a,b in systems])*weight
    centre = np.asarray(nominal).reshape(15)
    singular = np.linalg.svd(design,compute_uv=False)
    reg = np.eye(15)/regularization_sigma
    result = lsq_linear(np.vstack((design,reg)),np.r_[target,reg@centre],
        bounds=(centre-radius,centre+radius),tol=1e-10,max_iter=100)
    if not result.success:
        raise ValueError('sensor offset fit failed')
    return result.x.reshape(5,3),dict(design_rank=int(np.linalg.matrix_rank(design)),
        singular_values=singular.tolist(),condition_number=float(singular[0]/singular[-1]) if singular[-1]>0 else None,
        active_bounds=result.active_mask.tolist(),offset_change_m=(result.x-centre).reshape(5,3).tolist(),
        residual_before_mps2=float(np.sqrt(np.mean((design@centre-target)**2))),
        residual_after_mps2=float(np.sqrt(np.mean((design@result.x-target)**2))),
        conditional_on_pose_prior=True,independently_measured_offsets=False)
