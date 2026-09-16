#!/usr/bin/env python3
"""Continuous offline raw-range root A/B; no action-boundary state changes.

The input pose is independently rebuilt from the full continuous VQF stream.
Only root translation differs: A propagates IMU; B adds all-node raw ranges.
This measurement-ordered replay is not a causal availability-time benchmark.
"""
from __future__ import annotations

import argparse
from collections import Counter, deque
from dataclasses import asdict, replace
import hashlib
import json
from pathlib import Path
import signal
import time

import numpy as np
from scipy.spatial.transform import Rotation

from biospur_fusion.c2_uwb_root_world.u0 import ClockModel, UwbRow
from biospur_fusion.c2_uwb_root_world.tight_range import (
    RawRangeUpdateConfig, PersistentRangeBiasConfig,
    PersistentRangeBiasTracker, update_raw_ranges, ExternalRangeInformationWeights,
)
from biospur_fusion.c2_uwb_calibration.antenna_los import outward_facing_information_weights, hard_back_facing_mask
from biospur_fusion.c2_uwb_root_world import tight_range as range_owner
from biospur_fusion.c2_uwb_root_world.split_fusion import (
    FixedLagDriftConfig, FixedLagRangeDriftCorrector,
)
from biospur_fusion.c2_uwb_root_world.root_correction_slew import CausalRootCorrectionSlew
from biospur_fusion.root_r3.estimator import RootFilterConfig, propagate_inertial
from biospur_fusion.root_r3.models import RootState
from biospur_fusion.c2_uwb_root_world.archive_persistence import atomic_json, atomic_npz, persist_ancillary
from run_c2_h01_tight_raw_range_fusion import _anchors, _initialize


class CooperativeTermination:
    """CLI-only first-TERM checkpoint request; second TERM terminates normally.

    No exception or persistence runs inside the handler. The event transaction
    finishes before the replay observes the request. Restore the prior handler
    on normal exit or exception; SIGKILL always remains available.
    """
    def __init__(self):
        self.reason=None

    def __enter__(self):
        self.previous=signal.getsignal(signal.SIGTERM)
        signal.signal(signal.SIGTERM,self._request)
        return self

    def _request(self,signum,frame):
        self.reason='SIGTERM'
        signal.signal(signal.SIGTERM,signal.SIG_DFL)

    def __call__(self):
        return self.reason

    def __exit__(self,*exc):
        signal.signal(signal.SIGTERM,self.previous)


def _stack_rows(rows,shape):
    """A stopped replay may have no observations of a particular kind."""
    return np.stack(rows) if rows else np.empty((0,*shape),dtype=float)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1 << 20), b''):
            h.update(block)
    return h.hexdigest()


def clock_for_node(frontend: Path, node: str, origin_s: float) -> ClockModel:
    with np.load(frontend / f'{node}.npz', allow_pickle=False) as a:
        timer = a['time_us']
        common = a['common_global_ns']
        boots = np.unique(a['boot_epoch'])
        if len(boots) != 1:
            raise ValueError(f'{node}: multiple boot epochs need piecewise clock handling')
        boot = int(boots[0])
        slope = float((int(common[-1]) - int(common[0])) / (int(timer[-1]) - int(timer[0])))
        intercept = float(int(common[0]) - slope * int(timer[0]) - origin_s * 1e9)
        error = np.abs(slope * timer[::100] + intercept - (common[::100] - origin_s * 1e9))
        if np.max(error) > 100:
            raise ValueError(f'{node}: non-affine clock mapping {np.max(error)} ns')
    return ClockModel(boot, slope, intercept, float(np.max(error)))


def raw_events(frontend: Path, clocks: dict, origin_s: float, stop_s: float, partial_tracking=False):
    events = []
    with (frontend / 'UWB.jsonl').open() as stream:
        for line in stream:
            source = json.loads(line)
            node, p = source['node'], source['payload']
            if node not in clocks:
                continue
            row = UwbRow(node, int(source['boot_epoch']), int(p['packet_sequence']),
                int(p['sweep']), int(p['strobe_us']), int(p['frame_us']),
                tuple(p['anchor_id']), tuple(p['range_mm']), tuple(p['t_round_us']),
                tuple(p['quality_percent']), int(p['valid_mask']), int(p['identity']), int(p['node_ms']))
            valid = [i for i in range(8) if row.valid_mask & (1 << i) and 0 < row.ranges_mm[i] < 0xffff]
            # Tracking factors are not standalone 3D fixes. Do not discard
            # directional information before the initialized estimator sees it.
            if len(valid) < (1 if partial_tracking else 4):
                continue
            epoch = float(np.median([clocks[node].seconds(row.strobe_us + .5 * row.t_round_us[i]) for i in valid]))
            if 0 <= epoch <= stop_s:
                events.append((epoch, row, float(source['availability_global_ns']) * 1e-9 - origin_s))
    events.sort(key=lambda x: x[0])
    return events


def protected_raw_contact_sides(protocol, history, current_episodes, first_link):
    """Use pre-link support only, in an episode still active at assimilation."""
    prior=next((entry for entry in reversed(history) if entry[0]<first_link),None)
    if prior is None:return ()
    supported=protocol.position_evidence(first_link-1e-8)[0]
    return tuple(int(side) for side in np.flatnonzero(supported)
                 if side in current_episodes and side in prior[1]
                 and prior[1][side]==current_episodes[side])


def run(frontend: Path, pose_path: Path, output: Path, duration_s: float | None,
        max_runtime_s: float = 540.0, feedback_mode: str = 'legacy-split',
        large_shadow: bool = False, antenna_normals: Path | None = None,
        back_cut: bool = False, articulated_rotations: Path | None = None,
        articulated_mode: str = 'full', joint_bias_variance: bool = False,
        persistent_error_rotations: Path | None = None,
        inertial_acceleration_noise_density: float | None = None,
        propagation_admission_safety: bool = False,
        signed_effective_discrepancy: bool = False,
        root_bias_variance: bool = False, support_velocity: bool = False,
        support_position_guard: bool = False, protocol_contact_regions: Path | None = None,
        correction_time_constant_s: float | None = None, contact_lifecycle: bool = False,
        supervised_contact: bool = False, support_points: bool = False,
        joint_raw_initialization: bool = False, calibration_phase_prior: bool = False,
        corrected_discrepancy_likelihood: bool = False, partial_range_tracking: bool = False,
        registered_root_wear_frame: bool = False, joint_tag_contact: bool = False,
        restart_stationary_episode: bool = False, soft_support_position_guard: bool = False,
        joint_transition_tape: bool = False, articulated_contact: bool = False,
        fixed_lag_marginal: bool = False, articulated_tilt_restoration: bool = False,
        correction_gain_scope: str = 'full-state', rotation_position_bridge: bool = False,
        navigation_position_handoff: bool = False, contact_leg_only: bool = False,
        natural_geometry_only: bool = False, articulated_fit: Path | None = None,
        supported_point_raw_routing: bool = False,
        conditional_imu_heading: bool = False, stop_request=None,
        calibration_moving_support: bool = False, raw_structural_heading: bool = False,
        world_support_policy: str = 'legacy', native_tag_epoch_correction: bool = False) -> dict:
    if native_tag_epoch_correction and not (articulated_contact and natural_geometry_only):
        raise ValueError('native tag epochs require articulated natural geometry')
    if world_support_policy!='legacy' and not (support_velocity and protocol_contact_regions is not None):
        raise ValueError('explicit world support policy requires protocol support')
    if raw_structural_heading and not (articulated_contact and conditional_imu_heading):
        raise ValueError('structural raw heading requires articulated conditional heading')
    if calibration_moving_support and not (supervised_contact and calibration_phase_prior):
        raise ValueError('moving support evidence requires supervised calibration phase')
    if conditional_imu_heading and (not natural_geometry_only or articulated_tilt_restoration or contact_leg_only):
        raise ValueError('conditional IMU heading requires natural articulated geometry without OU or leg-only policy')
    if supported_point_raw_routing and not natural_geometry_only:
        raise ValueError('supported-point raw routing requires the natural articulated geometry owner')
    if natural_geometry_only and (not articulated_contact or articulated_fit is None or contact_leg_only):
        raise ValueError('natural geometry requires full articulated contact and its matching calibration fit')
    if contact_leg_only and (not articulated_contact or articulated_tilt_restoration
                            or not support_velocity or not support_position_guard):
        raise ValueError('contact-leg-only requires articulated contact and native support, without OU')
    # Native motion/contact evidence belongs to every joint/contact run.
    # It must not depend on suppressing the UWB attitude gain or enabling OU.
    native_contact_protections = articulated_contact
    if correction_gain_scope not in ('full-state','position-only'):
        raise ValueError('unknown correction gain scope')
    if correction_gain_scope=='position-only' and (correction_time_constant_s is None or
            articulated_rotations is not None or persistent_error_rotations is not None or joint_tag_contact
            or fixed_lag_marginal or feedback_mode!='full-state'):
        raise ValueError('position-only correction budget requires the live full-state root9 owner')
    if articulated_tilt_restoration and (not articulated_contact or not support_velocity or not support_position_guard):
        raise ValueError('tilt restoration requires articulated contact and inherited stationary protections')
    if fixed_lag_marginal and (not support_points or not support_velocity or not propagation_admission_safety
            or joint_transition_tape or joint_tag_contact or articulated_rotations is not None
            or persistent_error_rotations is not None or not joint_raw_initialization):
        raise ValueError('fixed lag requires initialized protected root9/contact path')
    if articulated_contact and (articulated_rotations is None or not registered_root_wear_frame
            or not support_velocity or not support_position_guard
            or not support_points or protocol_contact_regions is None or not propagation_admission_safety
            or not partial_range_tracking or not joint_raw_initialization or articulated_mode!='full'
            or joint_tag_contact or joint_transition_tape or soft_support_position_guard or correction_time_constant_s is not None):
        raise ValueError('articulated contact requires registered initialized native joint path without gain switches')
    if joint_transition_tape and (not support_points or not support_velocity or not propagation_admission_safety
            or joint_tag_contact or articulated_rotations is not None or persistent_error_rotations is not None):
        raise ValueError('transition tape requires input-safe root9 plus support points')
    if soft_support_position_guard and (not support_position_guard or not support_points
            or protocol_contact_regions is None or joint_tag_contact):
        raise ValueError('soft support routing requires baseline root9 protocol point guard')
    if joint_tag_contact and (persistent_error_rotations is None or not propagation_admission_safety
            or not support_points or not partial_range_tracking or not joint_raw_initialization
            or not registered_root_wear_frame or root_bias_variance or signed_effective_discrepancy):
        raise ValueError('joint tag/contact requires registered input-safe initialized partial tracking and no external bias tracker')
    if navigation_position_handoff and not rotation_position_bridge:
        raise ValueError('navigation handoff requires rotation position bridge')
    if rotation_position_bridge and (not support_position_guard or not contact_lifecycle
            or not supervised_contact or not calibration_phase_prior or not support_points
            or articulated_rotations or persistent_error_rotations or joint_tag_contact
            or fixed_lag_marginal or soft_support_position_guard or correction_time_constant_s is not None):
        raise ValueError('rotation position bridge requires baseline supervised root9 contact path')
    if restart_stationary_episode and not support_points:
        raise ValueError('stationary episode restart requires support points')
    if registered_root_wear_frame and (antenna_normals is None or (articulated_rotations is not None and not articulated_contact) or (persistent_error_rotations is not None and not joint_tag_contact) or feedback_mode!='full-state'):
        raise ValueError('registered root wear frame requires verified normals and full-state root-only propagation')
    if partial_range_tracking and (not joint_raw_initialization or not propagation_admission_safety):
        raise ValueError('partial tracking requires strict joint initialization and input-safe EKF')
    if corrected_discrepancy_likelihood and not (root_bias_variance or joint_tag_contact):
        raise ValueError('corrected discrepancy likelihood requires authenticated bias uncertainty')
    if calibration_phase_prior and not supervised_contact:
        raise ValueError('calibration phase prior requires supervised calibration model')
    if (supervised_contact or support_points) and (not contact_lifecycle or not propagation_admission_safety):
        raise ValueError('supervised contacts/points require input-safe contact lifecycle')
    if joint_raw_initialization and not propagation_admission_safety:
        raise ValueError('joint raw initialization requires input-safe full-state mode')
    if contact_lifecycle and (not support_velocity or protocol_contact_regions is None):
        raise ValueError('contact lifecycle requires protocol support')
    if contact_lifecycle and correction_time_constant_s is not None and (
            articulated_rotations is not None or persistent_error_rotations is not None or joint_tag_contact):
        raise ValueError('contact lifecycle correction budget is supported only by the root9 owner')
    if correction_time_constant_s is not None and not propagation_admission_safety:
        raise ValueError('correction budget requires input-safe full-state path')
    if protocol_contact_regions is not None and not support_position_guard:
        raise ValueError('protocol contact requires support position guard')
    if support_position_guard and not support_velocity:
        raise ValueError('support position guard requires support velocity')
    if support_velocity and (not propagation_admission_safety or signed_effective_discrepancy):
        raise ValueError('support velocity requires unchanged unsigned input-safe root mode')
    if root_bias_variance and (not propagation_admission_safety or signed_effective_discrepancy):
        raise ValueError('root bias variance requires unsigned input-safe root-only mode')
    if signed_effective_discrepancy and not propagation_admission_safety:
        raise ValueError('signed discrepancy experiment requires input-safe root-only mode')
    if propagation_admission_safety and (feedback_mode!='full-state' or (persistent_error_rotations is not None and not joint_tag_contact) or (articulated_rotations is not None and not articulated_contact) or inertial_acceleration_noise_density is not None):
        raise ValueError('input safety requires inherited-noise root-only full-state mode')
    if inertial_acceleration_noise_density is not None and (not np.isfinite(inertial_acceleration_noise_density) or inertial_acceleration_noise_density<=0):
        raise ValueError('inertial acceleration noise density must be finite positive')
    if persistent_error_rotations is not None and (feedback_mode!='full-state' or articulated_rotations is not None or joint_bias_variance):
        raise ValueError('persistent tag error requires root-only full-state mode')
    if articulated_mode not in ('full','heading'):
        raise ValueError('unknown articulated mode')
    if joint_bias_variance and (articulated_rotations is None or articulated_mode!='heading'):
        raise ValueError('joint bias-variance repair is opt-in heading mode only')
    if large_shadow and back_cut:
        raise ValueError('soft antenna weighting and hard back-cut are mutually exclusive')
    antenna_enabled = large_shadow or back_cut
    if feedback_mode not in ('legacy-split', 'full-state'):
        raise ValueError('unknown root feedback mode')
    full_feedback = feedback_mode == 'full-state'
    if articulated_rotations is not None and (not full_feedback or large_shadow):
        raise ValueError('joint articulated mode requires full-state and no soft weighting')
    started = time.perf_counter()
    output.mkdir(parents=True, exist_ok=False)
    with np.load(pose_path, allow_pickle=False) as archive:
        pose = {key: np.array(archive[key]) for key in archive.files}
    if 'geometry_embedding_from_previous' in pose and articulated_rotations is not None and not articulated_contact:
        raise ValueError('geometric embedding is not yet supported by articulated state/Jacobian owners')
    global_times = pose['time_s']
    origin_s = float(global_times[0])
    times = global_times - origin_s
    nodes = list(map(str, pose['node_names']))
    root_rotations=pose['pelvis_rotation_world_sensor']
    root_wear_yaw=None
    if registered_root_wear_frame:
        from biospur_fusion.c2_uwb_root_world.wear_frame import registered_pelvis_rotations
        with np.load(antenna_normals,allow_pickle=False) as registration:
            root_rotations,root_wear_yaw=registered_pelvis_rotations(pose,registration)
    support = None
    if support_velocity:
        from biospur_fusion.c2_uwb_root_world.continuous_support import ContinuousSupportVelocity
        support = ContinuousSupportVelocity(frontend, pose, origin_s, protocol_contact_regions, contact_lifecycle,
                                            supervised_contact,support_points,calibration_phase_prior,restart_stationary_episode,
                                            soft_support_position_guard,rotation_position_bridge,navigation_position_handoff,
                                            calibration_moving_support=calibration_moving_support,
                                            world_support_policy=world_support_policy)
    contact_transition=support.points.root_transition if support is not None and support.points is not None else None
    if joint_transition_tape:
        from biospur_fusion.c2_uwb_root_world.joint_transition_tape import JointTransitionTape
        support.points.tape=JointTransitionTape(enabled=True)
    lag=None
    if fixed_lag_marginal:
        from biospur_fusion.c2_uwb_root_world.fixed_lag_marginal import FixedLagMarginal
        lag=FixedLagMarginal()
        support.points.tape=lag
    normals = None
    if antenna_enabled:
        if antenna_normals is None:
            raise ValueError('large-shadow requires separately verified antenna normals')
        with np.load(antenna_normals, allow_pickle=False) as source:
            if not np.array_equal(source['time_s'],global_times) or not np.array_equal(source['node_names'],pose['node_names']):
                raise ValueError('antenna normals do not share the exact pose timeline and nodes')
            normals = np.array(source['node_normals_world'])
            if articulated_contact:registration_yaw=np.array(source['yaw_registration_world'])
        if normals.shape != (len(times),len(nodes),3) or not np.isfinite(normals).all():
            raise ValueError('invalid continuous antenna normals')
        if not np.allclose(np.linalg.norm(normals,axis=2),1.0,atol=1e-10,rtol=0):
            raise ValueError('antenna normals must be unit vectors')
    if len(nodes) != 10 or len(set(nodes)) != 10 or not np.all(np.diff(times) > 0):
        raise ValueError('requires ten unique nodes and strictly increasing continuous time')
    stop_s = float(times[-1] if duration_s is None else min(times[-1], times[0] + duration_s))
    clocks = {node: clock_for_node(frontend, node, origin_s) for node in nodes}
    uwb = raw_events(frontend, clocks, origin_s, stop_s, partial_range_tracking)
    anchors = _anchors()
    node_index = {node: i for i, node in enumerate(nodes)}
    offsets = pose['node_offsets']
    range_config = RawRangeUpdateConfig(nominal_sigma_m=.25, positive_nlos_cauchy_scale_m=.12,
        uncertainty_provenance='INHERITED_H01_DISPLAY_PROXY_UNCALIBRATED_DIAGNOSTIC')
    # Previous-sample association, including causal backward offset derivative.
    offset_velocity = np.zeros_like(offsets)
    offset_velocity[1:] = np.diff(offsets, axis=0) / np.diff(times)[:, None, None]
    seeds, initial_rows, initial_observations = [], [], []
    for epoch, row, available in uwb:
        if row.node in initial_rows or epoch < times[0]:
            continue
        # Partial rows remain on the tracking timeline but cannot initialize
        # an independent per-node Cartesian position.
        if len(range_owner._valid_slots(row)) < 4:
            continue
        index = int(np.clip(np.searchsorted(times, epoch, side='right') - 1, 0, len(times)-1))
        try:
            seed = _initialize(row, anchors, clocks[row.node])
        except RuntimeError:
            continue
        seeds.append(seed.position_m - offsets[index, node_index[row.node]])
        initial_rows.append(row.node)
        initial_observations.append((epoch,row,index,available))
        if len(seeds) == len(nodes):
            break
    if len(seeds) != 10:
        raise RuntimeError('initial common root needs one valid raw solve from every node')
    initial_position = np.median(np.stack(seeds), axis=0)
    t0 = float(times[0])
    state = RootState(t0, np.r_[initial_position, np.zeros(6)], np.diag([1.,1.,1.,1.,1.,1.,.25,.25,.25]))
    initialization_report=None;initialization_consumed=set();initialization_seen=set()
    if joint_raw_initialization:
        from biospur_fusion.c2_uwb_root_world.raw_initialization import initialize_raw_batch,row_identity
        independent_prior=RootState(t0,np.r_[np.mean(anchors,axis=0),np.zeros(6)],state.covariance)
        observations=[]
        for epoch,row,index,_ in initial_observations:
            n=node_index[row.node]
            if back_cut:
                scores,_=outward_facing_information_weights(initial_position+offsets[index,n],anchors,normals[index,n])
                row=replace(row,valid_mask=hard_back_facing_mask(row.valid_mask,scores))
            observations.append((row,clocks[row.node],offsets[index,n],offset_velocity[index,n],epoch))
        state,initialization_report=initialize_raw_batch(t0,independent_prior,initial_position,observations,anchors,range_config,
            aggregate_geometry=natural_geometry_only)
        initialization_report['latest_recorded_availability_epoch_s']=max(r[3] for r in initial_observations)
        initialization_report['time_basis']='RELATIVE_TO_ORIGIN_GLOBAL_S'
        initialization_report['a_initial_gauge_changed']=True
        initialization_consumed={tuple(i) for i in initialization_report['identities']}
        initial_position=state.position_m.copy()
    inertial = state
    if lag is not None:
        lag.information_time_s=initialization_report['startup_ready_epoch_s']
        lag.information_availability_s=initialization_report['latest_recorded_availability_epoch_s']
    # Initialization stays identical to the sealed baseline. Only subsequent
    # corrected range residuals use the explicit discrepancy likelihood.
    if corrected_discrepancy_likelihood:
        range_config=replace(range_config,symmetric_corrected_discrepancy=True)
    if partial_range_tracking:
        range_config=replace(range_config,partial_tracking=True)
    config = RootFilterConfig()
    if inertial_acceleration_noise_density is not None:
        config=replace(config,inertial_acceleration_noise_mps2_sqrt_hz=float(inertial_acceleration_noise_density))
    tag_error=None
    error_rows,error_covariance_rows,error_delta_rows=[],[],[]
    error_history=deque(maxlen=32)
    if persistent_error_rotations is not None:
        from biospur_fusion.c2_uwb_root_world.persistent_tag_error import PersistentTagErrorFilter
        from biospur_fusion.c2_uwb_calibration.articulated_range import SEGMENTS
        from biospur_fusion.c2_coupled_progressive.contracts import NODE_TO_SEGMENT
        if 'geometry_embedding_from_previous' not in pose:
            raise ValueError('persistent tag error requires explicit repaired geometry')
        with np.load(persistent_error_rotations,allow_pickle=False) as source:
            if not np.array_equal(source['time_s'],global_times) or tuple(source['segment_names'])!=SEGMENTS:
                raise ValueError('error basis requires exact native pose timeline and segments')
            error_rotations=np.array(source['base_segment_rotations_world'])
        if error_rotations.shape!=(len(times),10,3,3):
            raise ValueError('error basis needs ten rotation matrices per native sample')
        for start in range(0,len(times),8192):
            block=error_rotations[start:start+8192]
            if not np.isfinite(block).all() or not np.allclose(block.swapaxes(-1,-2)@block,np.eye(3),atol=1e-8,rtol=0) or not np.allclose(np.linalg.det(block),1.,atol=1e-8,rtol=0):
                raise ValueError('error basis input rotations must be finite and proper')
        error_segment_indices=[SEGMENTS.index(NODE_TO_SEGMENT[n]) for n in nodes]
        embedding=pose['geometry_embedding_from_previous']
        tag_error=PersistentTagErrorFilter(state,nodes)
        error_history.append((t0,tag_error.error.copy()))

        def error_basis(index,node_index):
            segment=error_segment_indices[node_index]
            basis=embedding@error_rotations[index,segment]
            rate=np.zeros((3,3)) if index==0 else embedding@(error_rotations[index,segment]-error_rotations[index-1,segment])/(times[index]-times[index-1])
            return basis,rate
    joint = None
    joint_pose_rows, joint_covariance_rows, joint_delta_rows, joint_heading_rows = [], [], [], []
    raw_contact_routing_audit = []
    joint_tag_rows,joint_attitude_rows,joint_active_snapshots=[],[],[]
    maximum_gravity_tilt_invariant_error = 0.0
    maximum_hinge_relative_rotation_error = 0.0
    joint_offset_velocity = np.zeros((10,3))
    joint_history = deque(maxlen=32)
    contact_episode_history = deque(maxlen=32)
    if articulated_rotations is not None:
        from biospur_fusion.c2_uwb_root_world.articulated_joint_filter import ArticulatedJointFilter,GravityPreservingHeadingFilter,HEADING_GROUP_NAMES
        from biospur_fusion.c2_uwb_calibration.articulated_range import SEGMENTS
        from biospur_fusion.c2_uwb_calibration.frozen_body_proxy import NODE_TO_PROXY_POINT
        from biospur_fusion.c2_coupled_progressive.contracts import NODE_TO_SEGMENT
        from biospur_fusion.c2_3a_kinematics import load_frozen_c2_3a
        from build_c2_full_session_ten_node_ab import _hinges
        with np.load(articulated_rotations,allow_pickle=False) as archive:
            if not np.array_equal(archive['time_s'],global_times) or tuple(archive['segment_names'])!=SEGMENTS:
                raise ValueError('joint base rotations do not match continuous pose clock/order')
            base_rotations=np.array(archive['physical_segment_rotations_world'] if natural_geometry_only
                                    else archive['base_segment_rotations_world'])
            mount=np.array(archive['pelvis_mount_sensor_from_segment'])
        if articulated_fit is None:
            hinges=_hinges()
        else:
            from biospur_fusion.c2_articulated_biomechanics.model import fit_articulated_model
            from build_c2_avatar_interactive import _load_trajectory
            hinges=fit_articulated_model(_load_trajectory(articulated_fit/'PRE_IK.npz'),
                                        json.loads((articulated_fit/'RESULT.json').read_text()))
        joint_type=GravityPreservingHeadingFilter if articulated_mode=='heading' else ArticulatedJointFilter
        joint=joint_type(state,base_rotations[0],geometry=load_frozen_c2_3a().geometry,
            hinges=hinges,pelvis_mount_sensor_from_segment=mount)
        if articulated_contact:
            from biospur_fusion.c2_uwb_root_world.articulated_contact import ArticulatedContactFilter
            if 'geometry_embedding_from_previous' not in pose:raise ValueError('explicit geometric embedding required')
            pose_metadata=json.loads(pose_path.with_suffix('.json').read_text())
            if pose_metadata.get('requires_physical_anatomical_separation',False) and not natural_geometry_only:
                raise ValueError('natural anatomical inputs require a physical-frame-aware projection owner; old projector cannot consume this bundle')
            if natural_geometry_only and Path(pose_metadata['source_calibration_fit']).resolve()!=articulated_fit.resolve():
                raise ValueError('natural inputs and joint model must share the same calibration fit')
            if conditional_imu_heading and (not np.allclose(pose['geometry_embedding_from_previous'],np.eye(3),atol=1e-12,rtol=0)
                    or not np.allclose(registration_yaw,np.eye(3),atol=1e-12,rtol=0)):
                raise ValueError('conditional IMU heading requires one baked physical world frame, without FK-only reflection or independent wear yaw')
            joint=ArticulatedContactFilter(state,base_rotations[0],geometry=load_frozen_c2_3a().geometry,
                hinges=hinges,pelvis_mount_sensor_from_segment=mount,
                embedding=pose['geometry_embedding_from_previous'],wear_yaw=root_wear_yaw,
                chest_vertical_m=float(np.mean(pose_metadata['chest_vertical_observations_m'])),
                tilt_restoration=articulated_tilt_restoration,contact_leg_only=contact_leg_only,
                conditional_imu_heading=conditional_imu_heading,
                raw_structural_heading=raw_structural_heading,
                native_tag_epoch_correction=native_tag_epoch_correction,
                restart_stationary_episode=restart_stationary_episode,
                **({'natural_geometry_only':True} if natural_geometry_only else {}))
            articulated_binding=joint.validate_native_binding(pose,base_rotations,root_rotations)
            support.points=joint.contacts
            contact_transition=None
        joint_previous_base_time=t0
    bias = None if joint_tag_contact else PersistentRangeBiasTracker(PersistentRangeBiasConfig(
        signed_effective_discrepancy=signed_effective_discrepancy))
    drift_config = FixedLagDriftConfig(maximum_velocity_step_mps=.020)
    drift = None if full_feedback else FixedLagRangeDriftCorrector(drift_config)
    slew = None if full_feedback else CausalRootCorrectionSlew(release_period_s=.12, maximum_correction_m=.020)
    timeline = [(float(t), 0, i) for i, t in enumerate(times) if t0 <= t <= stop_s]
    timeline.extend((epoch, 1, (row, available)) for epoch, row, available in uwb if epoch > t0)
    timeline.sort(key=lambda e: (e[0], e[1]))
    force, rotation = pose['pelvis_acc_sensor'][0], root_rotations[0]
    safety=None
    stale_intervals=[]
    admission_nis_rows,admission_limit_rows=[],[]
    if propagation_admission_safety:
        from biospur_fusion.c2_uwb_root_world.root_input_safety import CausalImuHold,guarded_raw_update
        safety=CausalImuHold(t0,force,rotation)
    cumulative = np.zeros(3)
    out_index, a_rows, b_rows, b_posterior, state_b = [], [], [], [], []
    covariance_diagonal, feedback_deltas = [], []
    shadow_scores, shadow_weights, shadow_epochs, shadow_tag, shadow_normal = [], [], [], [], []
    shadow_reasons = Counter()
    source_rows, selected_masks, removed_counts, selected_counts = [], [], [], []
    bias_variance_rows, bias_snapshot_epochs = [], []
    support_position_guard_rows = []
    correction_budget = None
    correction_gain_rows, correction_dt_rows = [], []
    if correction_time_constant_s is not None:
        from biospur_fusion.c2_uwb_root_world.correction_budget import GlobalCorrectionBudget
        correction_budget = GlobalCorrectionBudget(correction_time_constant_s)
    root_history = deque([(t0,state.position_m.copy(),state.velocity_mps.copy())],maxlen=32)
    decisions, drift_rows = [], []
    counts = {node: Counter() for node in nodes}
    last_progress = started
    loop_started = time.perf_counter()
    bounded_stop = False
    stop_reason=None
    last_processed_time=t0
    for event_time, kind, payload in timeline:
        requested=None if stop_request is None else stop_request()
        if requested:
            bounded_stop=True
            stop_reason=str(requested)
            break
        if time.perf_counter() - started > max_runtime_s:
            bounded_stop = True
            stop_reason='MAX_RUNTIME'
            break
        if lag is not None:
            lag.observation_time_s=event_time
            lag.availability_time_s=event_time
            if kind==0:
                lag.availability_time_s=max(event_time,float(pose['availability_time_s'][payload])-origin_s)
                lag.information_availability_s=max(lag.information_availability_s,lag.availability_time_s)
            else:
                source_row,availability=payload
                lag.availability_time_s=availability
            lag.advance(event_time)
        if support is not None:
            support.advance_actual_samples(event_time)
        if safety is not None:
            if articulated_contact:
                joint.propagate_safe(safety,event_time,config)
                state=joint.state.root
                _,safety_audit=safety.propagate(inertial,event_time,config)
            elif joint_tag_contact:
                transitions=[]
                predicted,safety_audit=safety.propagate(tag_error.root,event_time,config,transition_observer=transitions.append)
                tag_error.commit_propagation(predicted,transitions[0],contact_transition)
                state=tag_error.root
            else:
                state,safety_audit=safety.propagate(state,event_time,config,transition_observer=contact_transition)
            inertial,_=safety.propagate(inertial,event_time,config)
            if safety_audit.stale_cv_duration_s>1e-12:
                stale_intervals.append((event_time-safety_audit.stale_cv_duration_s,event_time,safety_audit.hold_deadline_s))
        elif tag_error is not None:
            tag_error.propagate(event_time,force,rotation,config)
            state=tag_error.root
        elif joint is None:
            state, _ = propagate_inertial(state, event_time, force, rotation, config)
        else:
            joint.propagate(event_time,force,config)
            state=joint.state.root
        if safety is None:
            inertial, _ = propagate_inertial(inertial, event_time, force, rotation, config)
        if kind == 0:
            i = payload
            if joint is not None:
                before_points=joint.points()
                if articulated_contact:before_tags=joint.tags()
                joint.observe_imu_base(event_time,base_rotations[i])
                after_points=joint.points()
                dt=event_time-joint_previous_base_time
                if dt>0:
                    if articulated_contact:
                        if native_contact_protections:
                            _,_,native_tags=joint.native_motion(with_jacobian=False);joint_offset_velocity=np.stack([native_tags[n] for n in nodes])
                        else:
                            after_tags=joint.tags();joint_offset_velocity=np.stack([(after_tags[n]-before_tags[n])/dt for n in nodes])
                    else:joint_offset_velocity=np.stack([(after_points[NODE_TO_PROXY_POINT[n]]-before_points[NODE_TO_PROXY_POINT[n]])/dt for n in nodes])
                joint_previous_base_time=event_time
                state=joint.state.root
                if not articulated_contact:
                    joint_pose_rows.append(np.stack([after_points[str(name)] for name in pose['joint_names']]))
                    joint_covariance_rows.append(np.diag(joint.state.covariance).copy())
                if articulated_mode=='heading':
                    joint_heading_rows.append(joint.heading_rad.copy())
                    corrected=joint.state.rotations
                    maximum_gravity_tilt_invariant_error=max(maximum_gravity_tilt_invariant_error,
                        float(np.max(np.abs(corrected[:,2,:]-base_rotations[i,:,2,:]))))
                    for parent,child in ((2,3),(4,5),(6,7),(8,9)):
                        maximum_hinge_relative_rotation_error=max(maximum_hinge_relative_rotation_error,
                            float(np.max(np.abs(corrected[parent].T@corrected[child]-base_rotations[i,parent].T@base_rotations[i,child]))))
            force, rotation = pose['pelvis_acc_sensor'][i], root_rotations[i]
            if safety is not None:
                safety.observe(event_time,force,rotation)
            if support is not None:
                if articulated_contact:
                    before_contact=state.vector.copy()
                    stationary,stationary_confidence,stationary_classes,valid,confidence,moving=support.world_evidence(event_time,joint.contacts)
                    epochs=support.protocol.sample_epochs(event_time)
                    dt=times[i]-times[i-1] if i else 0.
                    velocity_eligible=stationary&(epochs>support.protocol_consumed_epochs)&(0<dt<=support.config.maximum_sample_age_s)
                    innovation,noise=np.zeros(3),np.zeros((3,3))
                    if native_contact_protections:
                        innovation,noise=joint.update_stationary_velocity(velocity_eligible,stationary_confidence,dt,support.config)
                        support.protocol_consumed_epochs[velocity_eligible]=epochs[velocity_eligible]
                    epochs=support.protocol.sample_epochs(event_time)
                    dt=times[i]-times[i-1] if i else 0.
                    eligible=valid&(epochs>support.point_consumed_epochs)&(0<dt<=.0075)
                    points=joint.points()
                    joint.update_contact(np.stack([points['ankle_left'],points['ankle_right']]),valid,eligible,confidence,dt,moving)
                    state=joint.state.root
                    support.point_consumed_epochs[eligible]=epochs[eligible]
                    support.point_mask_audit.append((*valid,*moving,*eligible))
                    support.support_audit.append(stationary.copy() if native_contact_protections else valid.copy())
                    support.audit.append((event_time,*(velocity_eligible if native_contact_protections else eligible),
                        *(stationary_classes if native_contact_protections else (0,0)),*innovation,*noise.ravel(),*(state.vector-before_contact),*np.diag(state.covariance)))
                    points=joint.points()
                    joint_pose_rows.append(np.stack([points[str(name)] for name in pose['joint_names']]))
                    joint_covariance_rows.append(np.diag(joint.state.covariance).copy())
                    joint_tag_rows.append(np.stack([joint.tags()[n] for n in nodes]))
                    joint_attitude_rows.append(Rotation.from_matrix(joint.base.swapaxes(1,2)@joint.state.rotations).as_rotvec())
                    if conditional_imu_heading:
                        corrected=joint.state.rotations
                        maximum_gravity_tilt_invariant_error=max(maximum_gravity_tilt_invariant_error,
                            float(np.max(np.abs(corrected[:,2,:]-joint.base[:,2,:]))))
                        for parent,child in ((2,3),(4,5),(6,7),(8,9)):
                            maximum_hinge_relative_rotation_error=max(maximum_hinge_relative_rotation_error,
                                float(np.max(np.abs(corrected[parent].T@corrected[child]-joint.base[parent].T@joint.base[child]))))
                        if max(maximum_gravity_tilt_invariant_error,maximum_hinge_relative_rotation_error)>1e-8:
                            raise RuntimeError('conditional IMU heading violated native tilt or relative-joint preservation')
                    if joint.contacts.sides and len(joint_active_snapshots)<4 and (not joint_active_snapshots or event_time-joint_active_snapshots[-1]['time_s']>=.12):
                        joint_active_snapshots.append(dict(time_s=event_time,sides=list(joint.contacts.sides),
                            root=state.vector.tolist(),rotations=joint.state.rotations.tolist(),
                            anchors=joint.contacts.means.tolist(),covariance=joint.contacts.covariance(joint.tangent()).tolist()))
                elif joint_tag_contact:
                    tag_error.state=support.update(tag_error.state,i)
                    state=tag_error.root
                else:
                    state = support.update(state, i)
            published_position = (state.position_m if slew is None else
                slew.sample(event_time, state.position_m, state.velocity_mps).position_m)
            if lag is not None:
                lag.add_native(len(out_index),support.points.snapshot(state))
            out_index.append(i)
            a_rows.append(inertial.position_m.copy())
            b_rows.append(published_position.copy())
            b_posterior.append(state.position_m.copy())
            state_b.append(state.vector.copy())
            covariance_diagonal.append(np.diag(state.covariance).copy())
            if tag_error is not None:
                error_rows.append(tag_error.error.copy())
                error_covariance_rows.append(np.diag(tag_error.covariance).copy())
        else:
            row, available = payload
            gain_scale, gain_dt = (None, np.nan) if correction_budget is None else correction_budget.consume(event_time)
            correction_gain_rows.append(np.nan if gain_scale is None else gain_scale)
            correction_dt_rows.append(gain_dt)
            original_row = row
            i = int(np.clip(np.searchsorted(times, event_time, side='right') - 1, 0, len(times)-1))
            n = node_index[row.node]
            offset, velocity = offsets[i,n], offset_velocity[i,n]
            if joint is not None:
                offset=joint.tags()[row.node] if articulated_contact else joint.points()[NODE_TO_PROXY_POINT[row.node]]
                velocity=joint_offset_velocity[n]
                if native_contact_protections:
                    _,_,native_tags=joint.native_motion(with_jacobian=False)
                    velocity=native_tags[row.node]
            prior_bias = np.zeros(8) if tag_error is not None else bias.bias_vector(row.node)
            prior_snapshot=None
            if joint_bias_variance or root_bias_variance:
                original_slots=[slot for slot in range(8) if row.valid_mask&(1<<slot) and 0<row.ranges_mm[slot]<0xffff]
                if original_slots:
                    earliest_original=min(clocks[row.node].seconds(row.strobe_us+.5*row.t_round_us[slot]) for slot in original_slots)
                    prior_snapshot=bias.prior_snapshot(row.node,snapshot_time_s=float(np.nextafter(earliest_original,-np.inf)))
            bias_variance_rows.append(np.zeros(8) if prior_snapshot is None else prior_snapshot.variance_m2.copy())
            bias_snapshot_epochs.append(np.nan if prior_snapshot is None else prior_snapshot.snapshot_time_s)
            before_state = state.vector.copy()
            external = None
            scores, weights, evidence_time = np.full(8,np.nan), np.ones(8), np.nan
            tag_prior, normal_prior = np.full(3,np.nan), np.full(3,np.nan)
            if antenna_enabled:
                valid = [slot for slot in range(8) if row.valid_mask & (1<<slot) and 0<row.ranges_mm[slot]<0xffff]
                earliest = min((clocks[row.node].seconds(row.strobe_us+.5*row.t_round_us[slot])
                    for slot in valid),default=-np.inf)
                pose_index = int(np.searchsorted(times,earliest,side='left')-1)
                root_prior = next((entry for entry in reversed(root_history) if entry[0]<earliest),None)
                if pose_index>=0 and root_prior is not None:
                    root_epoch, root_position, root_velocity = root_prior
                    # Predict from a root posterior that predates every link;
                    # the current sweep and later IMU ticks cannot feed its weights.
                    tag_prior = root_position+(event_time-root_epoch)*root_velocity+offsets[pose_index,n]
                    if tag_error is not None:
                        prior_error=next((entry for entry in reversed(error_history) if entry[0]<earliest),None)
                        if prior_error is None:
                            raise RuntimeError('no strictly prior effective tag error')
                        prior_basis,_=error_basis(pose_index,n)
                        tag_prior+=prior_basis@prior_error[1][n]
                    normal_prior = normals[pose_index,n]
                    if joint is not None:
                        body_prior=next((entry for entry in reversed(joint_history) if entry[0]<earliest),None)
                        if body_prior is None:
                            raise RuntimeError('joint strict-prior geometry unavailable')
                        body_offset=(body_prior[3].point_at(n,event_time)
                            if native_tag_epoch_correction else body_prior[1][n])
                        tag_prior=root_position+(event_time-root_epoch)*root_velocity+body_offset
                        normal_prior=body_prior[2][n]
                        evidence_time=max(root_epoch,body_prior[0])
                    scores,weights = outward_facing_information_weights(tag_prior,anchors,normal_prior)
                    evidence_time = max(root_epoch,float(times[pose_index])) if joint is None else evidence_time
                    if back_cut:
                        row=replace(row,valid_mask=hard_back_facing_mask(row.valid_mask,scores))
                        weights=np.where(scores>=0,1.0,0.0)
                    else:
                        external = ExternalRangeInformationWeights(row.node,evidence_time,weights,
                            'STRICT_PRE_LINK_ANTENNA_FACING_WEARING_BACK_ONLY')
                    shadow_reasons['APPLIED'] += 1
                else:
                    if back_cut:
                        row=replace(row,valid_mask=0)
                        weights=np.zeros(8)
                        shadow_reasons['HARD_CUT_NO_STRICT_PRIOR'] += 1
                    else:
                        shadow_reasons['NEUTRAL_NO_STRICT_PRIOR'] += 1
            source_rows.append(original_row)
            selected_masks.append(row.valid_mask)
            original_valid=[slot for slot in range(8) if original_row.valid_mask&(1<<slot)
                and 0<original_row.ranges_mm[slot]<0xffff]
            selected_count=sum(bool(row.valid_mask&(1<<slot)) for slot in original_valid)
            selected_counts.append(selected_count)
            removed_counts.append(len(original_valid)-selected_count)
            shadow_scores.append(scores)
            shadow_weights.append(weights)
            shadow_epochs.append(evidence_time)
            shadow_tag.append(tag_prior)
            shadow_normal.append(normal_prior)
            admission_nis,admission_limit=np.nan,np.nan
            if lag is not None:
                retained=range_owner._valid_slots(row)
                lag.observation_time_s=max([event_time]+[clocks[row.node].seconds(row.strobe_us+.5*row.t_round_us[s]) for s in retained])
            consider_position = False
            if navigation_position_handoff:
                # Empty/invalid sweeps still receive an explicit scope-valid
                # unit gain; their ordinary admission rejection is unchanged.
                gain_scale=1.
                correction_gain_rows[-1]=gain_scale
            if support_position_guard and original_valid and (not articulated_contact or native_contact_protections):
                first_epoch = min(clocks[row.node].seconds(original_row.strobe_us + .5 * original_row.t_round_us[slot]) for slot in original_valid)
                consider_position = support.position_is_considered(first_epoch)
                if navigation_position_handoff:
                    gain_scale=support.protocol.navigation_position_gain(first_epoch)
                    correction_gain_rows[-1]=gain_scale
            support_position_guard_rows.append(consider_position)
            if joint_raw_initialization and row_identity(row) in initialization_consumed:
                identity=row_identity(row)
                if identity in initialization_seen:
                    raise ValueError('duplicate initialization source identity in replay')
                initialization_seen.add(identity)
                decision=range_owner._empty_decision('INITIALIZATION_CONSUMED',range_config)
                if articulated_contact:joint_delta_rows.append(np.zeros((10,3)))
                if tag_error is not None:error_delta_rows.append(np.zeros((10,3)))
            elif articulated_contact:
                protected_sides=()
                if supported_point_raw_routing and original_valid:
                    first_link=min(clocks[row.node].seconds(original_row.strobe_us+.5*original_row.t_round_us[slot])
                                   for slot in original_valid)
                    protected_sides=protected_raw_contact_sides(support.protocol,contact_episode_history,
                                                               joint.contacts._episodes,first_link)
                decision=joint.update_ranges(row,anchors_m=anchors,clock=clocks[row.node],
                    range_bias_m=prior_bias if prior_snapshot is None else None,bias_prior=prior_snapshot,
                    reference_epoch_s=event_time,offset_velocity_world_mps=velocity,config=range_config,
                    consider_position=consider_position,preserve_anchor_mean=native_contact_protections,
                    **({'protected_contact_sides':protected_sides} if supported_point_raw_routing else {}))
                if supported_point_raw_routing:
                    raw_contact_routing_audit.append(dict(time_s=event_time+origin_s,node=row.node,
                        accepted=decision.accepted,reason=decision.reason,
                        protected_sides=list(map(int,protected_sides)),
                        audit=joint.last_contact_routing_audit))
                state=joint.state.root;admission_nis=joint.last_nis;admission_limit=joint.last_nis_limit
                joint_delta_rows.append(joint.last_orientation_delta.copy())
            elif joint_tag_contact:
                basis,basis_rate=error_basis(i,n)
                decision,admission_nis,admission_limit=tag_error.update_tracking(row,
                    anchors_m=anchors,clock=clocks[row.node],offset_world_m=offset,
                    offset_velocity_world_mps=velocity,basis_world_from_local=basis,
                    basis_velocity_world_from_local=basis_rate,reference_epoch_s=event_time,
                    information_weights=external,config=range_config,consider_position=consider_position,
                    transition_observer=contact_transition)
                state=tag_error.root
                error_delta_rows.append(tag_error.last_error_delta.copy())
            elif safety is not None:
                state,decision,admission_nis,admission_limit=guarded_raw_update(state,row,
                    anchors_m=anchors,clock=clocks[row.node],
                    range_bias_m=prior_bias if prior_snapshot is None else None,
                    bias_prior=prior_snapshot,
                    tag_offset_world_m=offset,tag_offset_velocity_world_mps=velocity,
                    information_weights=external,reference_epoch_s=event_time if back_cut else None,
                    config=range_config,consider_position=consider_position,gain_scale=gain_scale,
                    correction_gain_scope='position-only' if navigation_position_handoff else correction_gain_scope,
                    transition_observer=contact_transition)
            elif tag_error is not None:
                basis,basis_rate=error_basis(i,n)
                decision=tag_error.update_ranges(row,anchors_m=anchors,clock=clocks[row.node],
                    offset_world_m=offset,offset_velocity_world_mps=velocity,
                    basis_world_from_local=basis,basis_velocity_world_from_local=basis_rate,
                    reference_epoch_s=event_time,information_weights=external,config=range_config)
                state=tag_error.root
                error_delta_rows.append(tag_error.last_error_delta.copy())
            elif joint is not None:
                decision=joint.update_ranges(row,anchors_m=anchors,clock=clocks[row.node],
                    range_bias_m=prior_bias if prior_snapshot is None else None,
                    bias_prior=prior_snapshot,reference_epoch_s=event_time,
                    offset_velocity_world_mps=velocity,config=range_config)
                state=joint.state.root
                joint_delta_rows.append(joint.last_orientation_delta.copy())
            else:
                state, decision = update_raw_ranges(state, row, anchors_m=anchors, clock=clocks[row.node],
                range_bias_m=prior_bias, tag_offset_world_m=offset,
                information_weights=external,
                tag_offset_velocity_world_mps=velocity,
                state_update_indices=None if full_feedback else (0,1,2),
                correction_gain=1.0 if full_feedback else .20,
                maximum_position_step_m=None if full_feedback else .020, config=range_config,
                    reference_epoch_s=event_time if back_cut else None)
            state_delta = state.vector - before_state
            admission_nis_rows.append(admission_nis)
            admission_limit_rows.append(admission_limit)
            feedback_deltas.append(state_delta)
            delta = state_delta[:3]
            cumulative += delta
            if slew is not None:
                slew.install(event_time, delta)
            if drift is not None:
                state, dd = drift.observe(state, node=row.node, decision=decision, anchors_m=anchors,
                    tag_offset_world_m=offset, tag_offset_velocity_world_mps=velocity,
                    rotation_world_from_sensor=rotation, range_bias_m=prior_bias,
                    cumulative_absolute_position_correction_m=cumulative)
                if dd.reason != 'UPDATE_PERIOD_NOT_REACHED':
                    drift_rows.append((event_time, dd.accepted, dd.reason, dd.rank, dd.condition,
                        dd.velocity_delta_mps.copy(), dd.accelerometer_bias_delta_mps2.copy()))
            if tag_error is None:
                bias.update(row.node, decision)
            counts[row.node]['accepted' if decision.accepted else 'rejected'] += 1
            residual, weight, measured, link = (np.full(8, np.nan) for _ in range(4))
            residual[list(decision.anchors)] = decision.innovations_m
            weight[list(decision.anchors)] = decision.robust_weights
            measured[list(decision.anchors)] = decision.measured_ranges_m
            link[list(decision.anchors)] = decision.link_epochs_s
            decisions.append((event_time, available, row.node, decision.accepted, decision.reason,
                residual, weight, measured, link, prior_bias.copy(), delta))
        if antenna_enabled:
            root_history.append((event_time,state.position_m.copy(),state.velocity_mps.copy()))
            if tag_error is not None:
                error_history.append((event_time,tag_error.error.copy()))
            if joint is not None:
                points=joint.points()
                world_error=joint.state.rotations@joint.base.swapaxes(1,2)
                if articulated_contact:
                    corrected_normals=np.stack([registration_yaw[k]@world_error[SEGMENTS.index(NODE_TO_SEGMENT[n])]@registration_yaw[k].T@normals[i,k] for k,n in enumerate(nodes)])
                else:corrected_normals=np.stack([world_error[SEGMENTS.index(NODE_TO_SEGMENT[n])]@normals[i,k] for k,n in enumerate(nodes)])
                tags=joint.tags() if articulated_contact else {n:points[NODE_TO_PROXY_POINT[n]] for n in nodes}
                tag_points=np.stack([tags[n] for n in nodes])
                if native_tag_epoch_correction:
                    from biospur_fusion.c2_uwb_root_world.native_tag_epoch import NativeTagSnapshot
                    _,_,native_tag_velocity=joint.native_motion(with_jacobian=False)
                    tag_snapshot=NativeTagSnapshot(tag_points,
                        np.stack([native_tag_velocity[n] for n in nodes]),
                        joint.last_base_time_s,joint.native_dt_s)
                    joint_history.append((event_time,tag_points,corrected_normals,tag_snapshot))
                else:
                    joint_history.append((event_time,tag_points,corrected_normals))
        if supported_point_raw_routing:
            contact_episode_history.append((event_time,dict(joint.contacts._episodes)))
        last_processed_time=event_time
        if time.perf_counter() - last_progress > 30:
            last_progress = time.perf_counter()
            print(json.dumps({'progress_time_s':event_time, 'stop_s':stop_s, 'elapsed_s':last_progress-started}), flush=True)
    loop_runtime_s = time.perf_counter() - loop_started
    if lag is not None:
        # Only horizons fully covered by processed events are released.
        lag.advance(last_processed_time)
    persistence_started = time.perf_counter()
    indices = np.asarray(out_index,dtype=int)
    a, b = _stack_rows(a_rows,(3,)), _stack_rows(b_rows,(3,))
    if not np.isfinite(a).all() or not np.isfinite(b).all():
        raise RuntimeError('nonfinite continuous root')
    arrays = dict(time_s=global_times[indices], origin_global_s=origin_s, roots_a=a, roots_b=b,
        uwb_support_position_guard=np.asarray(support_position_guard_rows, dtype=bool),
        uwb_correction_gain=np.asarray(correction_gain_rows),
        uwb_correction_dt_s=np.asarray(correction_dt_rows),
        roots_b_posterior=_stack_rows(b_posterior,(3,)), root_state_b=_stack_rows(state_b,(9,)),
        root_covariance_diagonal_b=_stack_rows(covariance_diagonal,(9,)),
        uwb_state_delta=_stack_rows(feedback_deltas,(9,)),
        antenna_facing_cosine=_stack_rows(shadow_scores,(8,)),
        antenna_information_weight=_stack_rows(shadow_weights,(8,)),
        antenna_evidence_time_s=np.asarray(shadow_epochs)+origin_s,
        antenna_predicted_tag_m=_stack_rows(shadow_tag,(3,)),
        antenna_outward_normal_world=_stack_rows(shadow_normal,(3,)),
        source_valid_mask=np.asarray([row.valid_mask for row in source_rows],dtype=np.uint8),
        source_range_mm=np.asarray([row.ranges_mm for row in source_rows],dtype=np.uint16).reshape(-1,8),
        source_t_round_us=np.asarray([row.t_round_us for row in source_rows],dtype=np.uint32).reshape(-1,8),
        source_strobe_us=np.asarray([row.strobe_us for row in source_rows],dtype=np.uint64),
        source_frame_us=np.asarray([row.frame_us for row in source_rows],dtype=np.uint64),
        source_sequence=np.asarray([row.sequence for row in source_rows],dtype=np.uint32),
        source_sweep=np.asarray([row.sweep for row in source_rows],dtype=np.uint32),
        selected_valid_mask=np.asarray(selected_masks,dtype=np.uint8),
        back_removed_count=np.asarray(removed_counts,dtype=np.uint8),
        selected_valid_count=np.asarray(selected_counts,dtype=np.uint8),
        joints_relative=pose['joints_relative'][indices], joint_names=pose['joint_names'],
        node_names=pose['node_names'], anchors_world_m=anchors,
        uwb_time_s=np.asarray([d[0] for d in decisions]) + origin_s,
        uwb_availability_s=np.asarray([d[1] for d in decisions]) + origin_s,
        uwb_node=np.asarray([d[2] for d in decisions]), uwb_accepted=np.asarray([d[3] for d in decisions]),
        uwb_reason=np.asarray([d[4] for d in decisions]),
        raw_range_residual_m=_stack_rows([d[5] for d in decisions],(8,)),
        raw_range_robust_weight=_stack_rows([d[6] for d in decisions],(8,)),
        raw_range_measured_m=_stack_rows([d[7] for d in decisions],(8,)),
        raw_range_link_epoch_s=_stack_rows([d[8] for d in decisions],(8,)) + origin_s,
        persistent_range_bias_prior_m=_stack_rows([d[9] for d in decisions],(8,)),
        persistent_range_bias_prior_variance_m2=_stack_rows(bias_variance_rows,(8,)),
        persistent_range_bias_snapshot_relative_s=np.asarray(bias_snapshot_epochs),
        persistent_range_bias_snapshot_time_s=np.asarray(bias_snapshot_epochs)+origin_s,
        absolute_position_delta_m=_stack_rows([d[10] for d in decisions],(3,)),
        drift_time_s=np.asarray([d[0] for d in drift_rows]) + origin_s,
        drift_accepted=np.asarray([d[1] for d in drift_rows]),
        drift_reason=np.asarray([d[2] for d in drift_rows]),
        drift_rank=np.asarray([d[3] for d in drift_rows]),
        drift_condition=np.asarray([d[4] for d in drift_rows]),
        drift_velocity_delta_mps=np.asarray([d[5] for d in drift_rows]).reshape(-1,3),
        drift_accelerometer_bias_delta_mps2=np.asarray([d[6] for d in drift_rows]).reshape(-1,3))
    if joint is not None:
        arrays.update(joints_relative_b=_stack_rows(joint_pose_rows,pose['joints_relative'].shape[1:]),
            joint_covariance_diagonal_b=_stack_rows(joint_covariance_rows,(len(joint.state.covariance),)),
            uwb_segment_orientation_delta_rad=_stack_rows(joint_delta_rows,(10,3)))
        if articulated_mode=='heading':
            arrays.update(heading_group_names=np.asarray(HEADING_GROUP_NAMES),
                heading_group_state_rad=_stack_rows(joint_heading_rows,(6,)))
    if 'geometry_embedding_from_previous' in pose:
        arrays['tag_offsets_relative'] = pose['node_offsets'][indices]
        arrays['geometry_embedding_from_previous'] = pose['geometry_embedding_from_previous']
    if articulated_contact:
        arrays['tag_offsets_relative_b']=_stack_rows(joint_tag_rows,(10,3))
        arrays['segment_correction_rotvec_b']=_stack_rows(joint_attitude_rows,(10,3))
    if safety is not None:
        arrays.update(raw_admission_prior_nis=np.asarray(admission_nis_rows),
            raw_admission_nis_limit=np.asarray(admission_limit_rows),
            stale_imu_cv_intervals_s=np.asarray(stale_intervals,float).reshape(-1,3)+origin_s)
    if tag_error is not None:
        arrays.update(tag_error_local_m=_stack_rows(error_rows,(10,3)),
            joint_error_covariance_diagonal=_stack_rows(error_covariance_rows,(len(tag_error.covariance),)),
            tag_error_update_delta_m=_stack_rows(error_delta_rows,(10,3)),
            joint_error_covariance_final=tag_error.covariance,
            tag_error_local_final_m=tag_error.error)
    if lag is not None:
        arrays.update(lag.arrays(origin_s))
    array_assembly_s = time.perf_counter() - persistence_started
    core_save_s = atomic_npz(output / 'CONTINUOUS_AB.npz', arrays)
    coverage = {'schema':'biospur.c2.archive.coverage.v1',
        'core_complete':True, 'ancillary_complete':False,
        'numerical_complete':not bounded_stop, 'bounded_stop':bounded_stop,'stop_reason':stop_reason,
        'sample_count':len(indices), 'raw_event_count':len(decisions),
        'first_time_s':float(global_times[indices[0]]) if len(indices) else None,
        'last_time_s':float(global_times[indices[-1]]) if len(indices) else None,
        'requested_stop_relative_s':stop_s, 'origin_global_s':origin_s,
        'last_processed_event_relative_s':last_processed_time,
        'loop_runtime_s':loop_runtime_s, 'array_assembly_s':array_assembly_s,
        'core_save_s':core_save_s, 'core_encoding':'NPZ_STORED_UNCOMPRESSED',
        'scientific_pass':False}
    atomic_json(output/'COVERAGE.json', coverage)
    result = {'schema':'biospur.c2.continuous.archive.raw-range.ab.v1',
        'status':('PARTIAL_BOUNDED_CHECKPOINT' if bounded_stop else 'DIAGNOSTIC_COMPLETE_NOT_SCIENTIFIC_PASS'),
        'scientific_pass':False, 'bounded_stop':bounded_stop, 'requested_stop_s':stop_s,'stop_reason':stop_reason,
        'pose_source':str(pose_path.resolve()), 'pose_sha256':sha256(pose_path),
        'runner_source':str(Path(__file__).resolve()), 'runner_sha256':sha256(Path(__file__)),
        'range_owner_source':str(Path(range_owner.__file__).resolve()),
        'range_owner_sha256':sha256(Path(range_owner.__file__)),
        'frontend_source':str(frontend.resolve()), 'frontend_result_sha256':sha256(frontend/'RESULT.json'),
        'sample_count':len(indices), 'duration_s':float(times[indices[-1]]-times[indices[0]]) if len(indices) else 0.,
        'runtime_s':time.perf_counter()-started,
        'loop_runtime_s':loop_runtime_s,
        'world_binding':'SAME_INITIAL_ROOT_AND_CONTINUOUS_RELATIVE_BODY_POSE_FOR_A_B',
        'feedback_mode':feedback_mode,
        'large_shadow_enabled':large_shadow,
        'hard_back_cut_enabled':back_cut,
        'hard_back_cut_policy':'COSINE_LT_ZERO_REMOVED_FRONT_OR_TANGENT_UNIT_INFORMATION',
        'removed_valid_links':int(sum(removed_counts)),
        'selected_link_count_histogram':dict(Counter(selected_counts)),
        'raw_update_reasons':dict(Counter(d[4] for d in decisions)),
        'range_reference_policy':'ORIGINAL_UNMASKED_SWEEP_MEDIAN' if back_cut else 'VALID_LINK_MEDIAN',
        'large_shadow_policy':'ANTENNA_FACING_WEARING_BACK_ONLY_0.5_PLUS_0.25_COSINE',
        'torso_ray_occlusion_enabled':False, 'other_limb_occlusion_enabled':False,
        'antenna_normals_source':None if antenna_normals is None else str(antenna_normals.resolve()),
        'antenna_normals_sha256':None if antenna_normals is None else sha256(antenna_normals),
        'antenna_weight_reasons':dict(shadow_reasons),
        'root_feedback_owner':'biospur_fusion.c2_uwb_root_world.tight_range.update_raw_ranges',
        'full_state_covariance_feedback':full_feedback,
        'root_propagation':'200HZ_IMU_PRIMARY_WITH_ASYNCHRONOUS_RAW_RANGE_CORRECTIONS',
        'publication':'ESTIMATOR_POSTERIOR_NO_DISPLAY_CORRECTION' if full_feedback else 'LEGACY_ROOT_SLEW',
        'range_bias_approximation':'RETAINED_EXTERNAL_MEAN_TRACKER_NO_ROOT_NUISANCE_CROSS_COVARIANCE',
        'joint_bias_variance_enabled':joint_bias_variance,
        'uwb_mechanism':'ALL_TEN_RAW_NODE_RANGES_WITH_CONTINUOUS_FK_OFFSETS_UPDATE_SHARED_ROOT_ONLY',
        'uwb_updates_joint_orientations':False,
        'initial_position_m':initial_position.tolist(), 'initial_raw_node_candidates_m':np.stack(seeds).tolist(),
        'initial_nodes':initial_rows, 'initialization':'OFFLINE_ONE_FIRST_RAW_SOLVE_PER_NODE_MEDIAN_MINUS_CONTINUOUS_FK_PROXY',
        'initialization_future_prefix_used':True,
        'post_initial_uwb_used_by_a':False,
        'action_labels_control_updates':world_support_policy=='protocol',
        'state_resets':0, 'old_per_frame_pose_consumed':False,
        'chronology':'OFFLINE_MEASUREMENT_EPOCH_ORDER_NOT_AVAILABILITY_CAUSAL',
        'output_time_basis':'ALL_NPZ_TIMES_ABSOLUTE_COMMON_GLOBAL_SECONDS',
        'pose_association':('NATIVE_SECANT_TAG_REFERENCE_AND_PER_LINK_JACOBIAN_STRICT_PRIOR_ANTENNA_SNAPSHOT'
            if native_tag_epoch_correction else 'PREVIOUS_SAMPLE_OFFSET_AND_BACKWARD_DIFFERENCE_VELOCITY'),
        'raw_range_update_config':asdict(range_config), 'drift_config':asdict(drift_config),
        'root_filter_config':asdict(config),
        'inertial_acceleration_noise_density_override':inertial_acceleration_noise_density,
        'inertial_noise_scope':'INHERITED_DEFAULT' if inertial_acceleration_noise_density is None else 'DIRECTOR_AUTHORIZED_MODEL_SENSITIVITY_NOT_SENSOR_CALIBRATION',
        'absolute_gain':1.0 if full_feedback else .20,
        'absolute_cap_m':None if full_feedback else .020,
        'slew_period_s':None if full_feedback else .12,
        'node_counts':{node:dict(count) for node,count in counts.items()},
        'clocks':{node:asdict(clock) for node,clock in clocks.items()},
        'drift_reasons':dict(Counter(d[2] for d in drift_rows)),
        'drift_accepted':sum(bool(d[1]) for d in drift_rows),
        'a_final_displacement_m':float(np.linalg.norm(a[-1]-a[0])) if len(a) else None,
        'b_final_displacement_m':float(np.linalg.norm(b[-1]-b[0])) if len(b) else None,
        'a_max_displacement_m':float(np.max(np.linalg.norm(a-a[0],axis=1))) if len(a) else None,
        'b_max_displacement_m':float(np.max(np.linalg.norm(b-b[0],axis=1))) if len(b) else None,
        'limitations':['No Vicon truth: boundedness is not accuracy.',
            'FK node locations are strap/landmark proxies, not antenna phase centre metrology.',
            'No action resets or still/contact pinning. Actual IMU gaps retain previous sample.',
            'Shared raw-range initial gauge; A is IMU-only only after initialization.']}
    if joint is not None:
        import inspect
        from biospur_fusion.c2_uwb_root_world import articulated_joint_filter
        result.update(world_binding='SAME_INITIAL_ROOT_AND_BASE_IMU_GAUGE_DIFFERENT_RECURRENT_B_BODY',
            uwb_updates_joint_orientations=True,
            uwb_mechanism='ONE_RAW_LIKELIHOOD_JOINT_ROOT9_SEGMENT_ERROR30',
            root_feedback_owner='biospur_fusion.c2_uwb_root_world.articulated_joint_filter.ArticulatedJointFilter',
            articulated_rotations_source=str(articulated_rotations.resolve()),
            articulated_rotations_sha256=sha256(articulated_rotations),
            articulated_owner_source=str(Path(inspect.getfile(type(joint))).resolve()),
            articulated_owner_sha256=sha256(Path(inspect.getfile(type(joint)))),
            articulated_parent_sha256=sha256(Path(articulated_joint_filter.__file__)),
            orientation_input='INCREMENTS_OF_CONTINUOUS_VQF_AND_EXISTING_HINGE_PROJECTED_BASE',
            orientation_transport='RIGHT_LOCAL_DELTA_CONJUGATION_WORLD_ERROR_PERSISTENT',
            hinge_covariance='FORWARD_FINITE_DIFFERENCE_LOCAL_PUSHFORWARD_EPS_1E-6_NOT_EXACT_TRUNCATED_DISTRIBUTION',
            orientation_prior_sigma_rad=.10,orientation_rw_rad_sqrt_s=.01,
            bias_state_scope='PELVIS_SENSOR_ACCELEROMETER_ONLY_NO_OTHER_IMU_BIASES',
            motion_linearization='MEASURED_PER_LINK_EPOCH_WITH_ROOT_AND_PRIOR_FK_VELOCITY_HELD_DURING_UPDATE',
            joint_nonzero_orientation_updates=int(np.sum(np.linalg.norm(arrays['uwb_segment_orientation_delta_rad'],axis=(1,2))>1e-10)),
            joint_max_orientation_update_rad=float(np.linalg.norm(arrays['uwb_segment_orientation_delta_rad'],axis=2).max(initial=0.)))
        if articulated_mode=='heading':
            result.update(uwb_mechanism='ONE_RAW_LIKELIHOOD_JOINT_ROOT9_SHARED_HEADING6',
                orientation_input='CURRENT_CONTINUOUS_VQF_HINGE_BASE_WITH_PERSISTENT_HEADING',
                root_feedback_owner='biospur_fusion.c2_uwb_root_world.articulated_joint_filter.GravityPreservingHeadingFilter',
                orientation_transport='SIX_PERSISTENT_WORLD_YAW_GROUPS_LEFT_APPLIED_TO_CURRENT_IMU_BASE',
                hinge_covariance='NO_PROJECTION_SHARED_PARENT_CHILD_WORLD_YAW_PRESERVES_RELATIVE_R',
                orientation_scope='HEADING_ONLY_CONDITIONAL_ON_UPSTREAM_VQF_GRAVITY_TILT_NOT_FULL_ATTITUDE_CALIBRATION',
                orientation_uncertainty_provenance='UNCHANGED_DIAGNOSTIC_PRIOR_AND_RW_NOT_CALIBRATED_NOT_PREFIX_FITTED',
                heading_group_names=list(HEADING_GROUP_NAMES),
                maximum_gravity_tilt_invariant_error=maximum_gravity_tilt_invariant_error,
                maximum_hinge_relative_rotation_error=maximum_hinge_relative_rotation_error,
                heading_max_abs_rad=np.max(np.abs(arrays['heading_group_state_rad']),axis=0,initial=0.).tolist())
        if joint_bias_variance:
            result['range_bias_approximation']='EXISTING_PRE_LINK_MEAN_AND_VARIANCE_NO_ROOT_NUISANCE_CROSS_COVARIANCE'
    if 'geometry_embedding_from_previous' in pose:
        pose_metadata = json.loads(pose_path.with_suffix('.json').read_text())
        result['tag_geometry_metadata'] = pose_metadata
        result['tag_geometry_scope'] = pose_metadata['tag_geometry_scope']
    if tag_error is not None:
        from biospur_fusion.c2_uwb_root_world import persistent_tag_error
        result.update(persistent_tag_error_enabled=True,
            root_feedback_owner='biospur_fusion.c2_uwb_root_world.persistent_tag_error.PersistentTagErrorFilter',
            persistent_error_source_sha256=sha256(Path(persistent_tag_error.__file__)),
            error_rotations_source=str(persistent_error_rotations.resolve()),
            error_rotations_sha256=sha256(persistent_error_rotations),
            error_state_frame='SEGMENT_LOCAL_WITH_GEOMETRIC_EMBEDDING_G_R',
            error_prior_sigma_m=PersistentRangeBiasConfig().initial_sigma_m,
            error_random_walk=0.0,error_uncertainty_scope='INHERITED_PROVISIONAL_NOT_CALIBRATED',
            joint_covariance_final_min_eigenvalue=float(np.linalg.eigvalsh(tag_error.covariance).min()),
            range_bias_approximation='EXTERNAL_TRACKER_DISABLED_SINGLE_JOINT_SIGNED_POSITION_ERROR_LIKELIHOOD',
            error_gauge='PRIOR_REFERENCED_NO_ZERO_SUM_PIN_NO_PHYSICAL_CALIBRATION_CLAIM',
            uwb_mechanism='JOINT_ROOT9_AND_CONSTANT_LOCAL_TAG_DISCREPANCY30_FULL_CROSS_COVARIANCE')
    if joint_tag_contact:
        result.update(joint_tag_contact=True,base_state_dimension=39,maximum_contact_state_dimension=45,
            joint_initialization_scope='APPROXIMATE_ZERO_NUISANCE_CONDITIONAL_ROOT_POSTERIOR_PLUS_INDEPENDENT_0.30M_NUISANCE_PRIOR_MISSING_STARTUP_CROSS',
            external_range_bias_tracker_enabled=False,
            contact_covariance_scope='FULL_BASE39_TO_CONTACT_CROSS_SCHMIDT_ROOT_OBSERVATIONS_JOINT_CONTACT_UPDATES',
            joint_contact_final_min_eigenvalue=float(np.linalg.eigvalsh(support.points.covariance(tag_error.state)).min()))
    if safety is not None:
        from biospur_fusion.c2_uwb_root_world import root_input_safety
        result.update(propagation_admission_safety=True,
            input_safety_owner_sha256=sha256(Path(root_input_safety.__file__)),
            imu_hold_validity_s=1./200.,
            stale_input_policy='EXACT_DEADLINE_SPLIT_THEN_EXISTING_CV_PRESERVE_VELOCITY_RESUME_ON_ACTUAL_IMU',
            stale_cv_total_duration_s=float(sum(b-a for a,b,_ in stale_intervals)),
            stale_cv_subinterval_count=len(stale_intervals),
            raw_admission_policy='UNROBUST_PRIOR_SWEEP_NIS_ACTUAL_LINK_DOF_INHERITED_ROOT_SIGNIFICANCE',
            raw_admission_scope='DIAGNOSTIC_UNQUALIFIED_NO_POSITION_CLIP_WIDE_PRIOR_LARGE_SHIFTS_MAY_PASS',
            baseline_input_policy_changed_for_both_a_b=True)
    if root_bias_variance:
        result.update(root_bias_variance_enabled=True,
            root_bias_variance_policy='SAME_STRICT_PRE_LINK_UNSIGNED_PRIOR_IN_ADMISSION_AND_ROOT_UPDATE',
            root_bias_variance_config=asdict(bias.config),
            root_bias_variance_cross_covariance='UNAVAILABLE_NOT_PROPAGATED_DIAGNOSTIC_APPROXIMATION')
    if signed_effective_discrepancy:
        result.update(effective_discrepancy_mode='SIGNED_ENGINEERING_GEOMETRY_RADIO_NOT_PHYSICAL_NLOS',
            effective_discrepancy_config=asdict(bias.config),
            effective_discrepancy_timing='PRIOR_MEAN_ONLY_ROOT_UPDATE_THEN_POSTERIOR_TRACKER_FOR_NEXT_SWEEP',
            effective_discrepancy_covariance='INHERITED_EXTERNAL_MEAN_NO_ROOT_CROSS_COVARIANCE_NO_R_CHANGE')
    result['support_velocity_enabled'] = support_velocity
    result['restart_stationary_episode'] = restart_stationary_episode
    result['rotation_position_bridge']=rotation_position_bridge
    result['navigation_position_handoff']=navigation_position_handoff
    if navigation_position_handoff:
        result['navigation_position_handoff_policy']='AGGREGATE_ANY_SIDE_PROTECTION_RELEASE_THEN125MS_KP_ONLY_LINEAR_RECOVERY_KV_KB_FULL_NO_CONTACT_EVIDENCE'
    if rotation_position_bridge:
        result['rotation_position_bridge_policy']='MAX125MS_FROM_LAST_DIRECT_STATIONARY_FRESH_ACCEL_QUIET_NONLIFT_FORMAL_ARTICULATION_ONLY_NO_VELOCITY_EVIDENCE'
        result['rotation_position_bridge_samples']={s:o.bridge_samples for s,o in support.protocol.rotation_bridge.items()}
    result['soft_support_position_guard'] = soft_support_position_guard
    result['soft_support_routing_scope'] = ('FRESH_HISTORICAL_POSITION_SUPPORT_CONSIDERS_ROOT_POSITION_NO_SOFT_ZUPT'
        if soft_support_position_guard else 'STATIONARY_SUPPORT_ONLY')
    result['support_position_guard_enabled'] = support_position_guard
    result['support_position_guard_count'] = int(np.count_nonzero(support_position_guard_rows))
    result['protocol_contact_regions'] = None if protocol_contact_regions is None else str(protocol_contact_regions)
    result['correction_time_constant_s'] = correction_time_constant_s
    result['correction_gain_scope'] = correction_gain_scope
    result['contact_lifecycle'] = contact_lifecycle
    result['supervised_contact'] = supervised_contact
    result['calibration_phase_prior'] = calibration_phase_prior
    result['calibration_moving_support'] = calibration_moving_support
    result['world_support_policy'] = world_support_policy
    result['native_tag_epoch_correction'] = native_tag_epoch_correction
    result['raw_structural_heading'] = raw_structural_heading
    result['corrected_discrepancy_likelihood'] = corrected_discrepancy_likelihood
    result['partial_range_tracking'] = partial_range_tracking
    result['registered_root_wear_frame'] = registered_root_wear_frame
    result['root_wear_binding'] = None if root_wear_yaw is None else {
        'world_yaw_matrix':root_wear_yaw.tolist(),
        'registration_source':str(antenna_normals),
        'registration_sha256':sha256(antenna_normals),
        'scope':'BOTH_A_B_ROOT_SENSOR_ROTATION_ONLY_FORCE_BIAS_FK_UNCHANGED',
        'claim':'CONSISTENT_WITH_ATTESTED_WEAR_PRIOR_NOT_INDEPENDENT_HEADING_TRUTH'}
    result['support_points'] = support_points
    result['joint_raw_initialization']=initialization_report
    result['initialization_consumed_in_replay']=len(initialization_seen)
    if initialization_report is not None:
        result['initialization']='BUFFERED_JOINT_RAW_POSITION_WITH_INDEPENDENT_BROAD_PRIOR_EXACT_ONCE_SOURCE_LEDGER'
    result['support_point_root_update_convention'] = 'SINGLE_PRIOR_EKF_UNIT_GAIN_SCHMIDT_ANCHORS' if support_points else None
    result['correction_budget_policy'] = 'ALL_TAGS_SINGLE_RAW_ATTEMPT_CLOCK_FIRST_TIE_GAP_GT_120MS_ZERO_GAIN_NO_BACKLOG'
    if correction_gain_scope=='position-only':
        result['correction_budget_policy']='ALL_TAGS_SINGLE_RAW_ATTEMPT_CLOCK_FIRST_TIE_GAP_GT120MS_ZERO_POSITION_GAIN;VELOCITY_BIAS_FULL_GAIN;NO_BACKLOG'
    result['joint_transition_tape']=joint_transition_tape
    if lag is not None:
        result['fixed_lag_marginal']=lag.diagnostic()
    if articulated_contact:
        result['articulated_contact_binding']=articulated_binding
        result['contact_factor_policy']='NATIVE_MOTION_VELOCITY_AND_POSITION_CONTACT_WITH_FULL_JOINT_JACOBIANS;STATIONARY_POSITION_CONSIDER;ANCHOR_MEAN_PROTECTED'
        result['articulated_tilt_restoration']=articulated_tilt_restoration
        if articulated_tilt_restoration:
            result['contact_factor_policy']='POSITION_FULL_JOINT_PLUS_NATIVE_INCREMENT_VELOCITY_WITH_POSE_JACOBIAN;RAW_AND_VELOCITY_SCHMIDT_ANCHORS;STATIONARY_ROOT_POSITION_CONSIDER'
            result['tilt_process']={'sigma_rad':joint.tilt_sigma,'correlation_s':joint.tilt_correlation_s,
                'yaw_rw_rad_sqrt_s':joint.orientation_rw,'provenance':'INITIAL_ENGINEERING_PRIOR_NOT_MEASURED_OR_FITTED',
                'yaw_semantics':'LOCAL_LOG_COMPONENT_PRESERVED_NOT_MIXED_EULER_HEADING',
                'native_noise':'TILT_OU_AND_YAW_RW_ONCE_PER_NATIVE_ELAPSED_TIME_NO_ISOTROPIC_DOUBLE_NOISE'}
        result['uwb_updates_joint_orientations']=True
        result['native_contact_protections']=native_contact_protections
        result['natural_geometry_only']=natural_geometry_only
        result['supported_point_raw_routing']=supported_point_raw_routing
        result['conditional_imu_heading']=conditional_imu_heading
        if conditional_imu_heading:
            from biospur_fusion.c2_uwb_root_world import contact_raw_gain, support_points as support_points_owner
            result['orientation_scope']='SIX_GROUP_WORLD_YAW_GAIN_CONDITIONAL_ON_NATIVE_IMU_TILT_AND_PAIR_RELATIVE_ROTATIONS'
            result['conditional_heading_covariance']='FULL_ROOT39_CONTACT_JOSEPH_AND_SO3_RESET_WITH_ACTUAL_RESTRICTED_GAIN;NO_COVARIANCE_PROJECTION'
            result['conditional_heading_sources']={str(Path(module.__file__).resolve()):sha256(Path(module.__file__))
                for module in (contact_raw_gain,support_points_owner)}
            result['maximum_gravity_tilt_invariant_error']=maximum_gravity_tilt_invariant_error
            result['maximum_hinge_relative_rotation_error']=maximum_hinge_relative_rotation_error
        if supported_point_raw_routing:
            result['raw_contact_routing_policy']='SUPPORTED_POINT_RAW_CORRECTION_ROUTING_DIAGNOSTIC_NOT_MOVING_CONTACT_TRUTH_OR_ZUPT'
            result['raw_contact_routing_audit_file']='RAW_CONTACT_ROUTING.json'
            result['raw_contact_routing_count']=sum(bool(row['protected_sides']) and row['accepted'] for row in raw_contact_routing_audit)
            result['raw_contact_finite_shift_max_m']=max((row['audit']['finite_endpoint_shift_max_m'] for row in raw_contact_routing_audit),default=0.)
        result['root_feedback_owner']='biospur_fusion.c2_uwb_root_world.articulated_contact.ArticulatedContactFilter'
        if natural_geometry_only:
            result['orientation_input']='CONTINUOUS_PRE_IK_PHYSICAL_ROTATION_INCREMENTS_WITH_SEPARATE_NATURAL_ENDPOINT_MAP'
        result['articulated_fit']=None if articulated_fit is None else str(articulated_fit.resolve())
        result['uwb_mechanism']='ONE_JOINT_ROOT39_PLUS_ACTIVE_CONTACTS_RAW_LIKELIHOOD'
        result['contact_leg_only']=contact_leg_only
        if contact_leg_only:
            result['uwb_updates_joint_orientations']=False
            result['contact_factor_policy']='ACCUMULATED_JOINT_COVARIANCE_CONTACT_LEGS_ONLY;RAW_VELOCITY_SCHMIDT_POSE_AND_ANCHORS;NATIVE_SUPPORT_GUARD;NO_OU'
            result['contact_pose_bound_rad']=joint.contact_pose_bound_rad
            result['contact_pose_rejected_count']=joint.contact_pose_rejected_count
    result.update(core_complete=True, ancillary_complete=False,
        array_assembly_s=array_assembly_s, core_save_s=core_save_s)
    atomic_json(output/'RESULT.json', result)
    def save_auxiliary(staging):
        if support is not None:
            support.save(staging)
        if articulated_contact:
            atomic_json(staging/'ACTIVE_JOINT_SNAPSHOTS.json', joint_active_snapshots)
            if supported_point_raw_routing:
                atomic_json(staging/'RAW_CONTACT_ROUTING.json',raw_contact_routing_audit)
            if contact_leg_only:
                np.savez(staging/'CONTACT_LEG_POLICY.npz',
                    rows=np.asarray(joint.contact_policy_audit).reshape(-1,4),
                    columns=np.asarray(['relative_time_s','accepted','maximum_leg_correction_rad','maximum_nonleg_correction_rad']))
        if joint_transition_tape:
            atomic_json(staging/'JOINT_TRANSITION_TAPE.json', support.points.tape.diagnostic())
    ancillary_save_s = persist_ancillary(output, save_auxiliary)
    result.update(ancillary_complete=True, ancillary_save_s=ancillary_save_s,
        runtime_s=time.perf_counter()-started)
    atomic_json(output/'RESULT.json', result)
    coverage.update(ancillary_complete=True, ancillary_save_s=ancillary_save_s,
        runtime_s=time.perf_counter()-started)
    atomic_json(output/'COVERAGE.json', coverage)
    print(json.dumps(result), flush=True)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--frontend', type=Path, required=True)
    parser.add_argument('--pose', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--duration-s', type=float)
    parser.add_argument('--max-runtime-s', type=float, default=540.0)
    parser.add_argument('--feedback-mode', choices=('legacy-split','full-state'), default='legacy-split')
    parser.add_argument('--large-shadow',action='store_true')
    parser.add_argument('--back-cut',action='store_true')
    parser.add_argument('--antenna-normals',type=Path)
    parser.add_argument('--articulated-rotations',type=Path)
    parser.add_argument('--articulated-mode',choices=('full','heading'),default='full')
    parser.add_argument('--joint-bias-variance',action='store_true')
    parser.add_argument('--persistent-error-rotations',type=Path)
    parser.add_argument('--inertial-acceleration-noise-density',type=float)
    parser.add_argument('--propagation-admission-safety',action='store_true')
    parser.add_argument('--signed-effective-discrepancy',action='store_true',
        help='Opt-in signed engineering discrepancy, not physical NLOS calibration')
    parser.add_argument('--root-bias-variance',action='store_true',
        help='Use the existing unsigned pre-link bias prior variance in root admission and update')
    parser.add_argument('--support-velocity', action='store_true', help='Conservative shank support velocity root update')
    parser.add_argument('--support-position-guard', action='store_true', help='Fresh support makes position a consider state during UWB updates')
    parser.add_argument('--soft-support-position-guard', action='store_true', help='Diagnostic: fresh moving point support also routes UWB position gain to zero; no soft ZUPT')
    parser.add_argument('--protocol-contact-regions', type=Path, help='Offline full-calibration contact fitting regions')
    parser.add_argument('--correction-time-constant-s', type=float, help='Engineering global UWB correction gain damping')
    parser.add_argument('--correction-gain-scope',choices=('full-state','position-only'),default='full-state',
        help='Opt-in position-row-only gain budget preserves full velocity/bias gain')
    parser.add_argument('--contact-lifecycle', action='store_true', help='Conservative stationary-ankle lifecycle and contact position-consider update')
    parser.add_argument('--supervised-contact', action='store_true', help='Actual full-protocol supervision independent of old classifier')
    parser.add_argument('--calibration-phase-prior', action='store_true', help='Retrospective formal calibration phase priors; not features-only runtime')
    parser.add_argument('--corrected-discrepancy-likelihood',action='store_true',help='Symmetric inherited tail for signed bias-corrected residuals, not physical NLOS')
    parser.add_argument('--partial-range-tracking',action='store_true',help='Initialized full-prior directional EKF with 1–8 retained links; initializer remains strict')
    parser.add_argument('--registered-root-wear-frame',action='store_true',help='Use the same proper pelvis world-yaw for inertial propagation and registered antenna normals')
    parser.add_argument('--support-points', action='store_true', help='Stochastic persistent ankle points with root cross covariance')
    parser.add_argument('--joint-raw-initialization',action='store_true',help='Buffered joint raw mean/covariance and exact-once first-row ledger')
    parser.add_argument('--joint-tag-contact',action='store_true',help='Opt-in coherent root39 plus stochastic contacts, no external bias tracker')
    parser.add_argument('--restart-stationary-episode',action='store_true',help='Unpromoted candidate: establish a new uncertain ankle reference on moving-to-stationary transition')
    parser.add_argument('--rotation-position-bridge',action='store_true',help='At most125ms position-only guard after direct stationary evidence during fresh rotation-only formal support')
    parser.add_argument('--navigation-position-handoff',action='store_true',help='Finite125ms Kp-only recovery after aggregate protection ends; never extends contact evidence')
    parser.add_argument('--joint-transition-tape',action='store_true',help='Bounded diagnostic root/contact error-transition recording; no smoothing')
    parser.add_argument('--articulated-contact',action='store_true',help='Joint root/segment/contact likelihood with verified geometric and physical frame adapters')
    parser.add_argument('--fixed-lag-marginal',action='store_true',help='120 ms delayed historical root/contact marginal estimator; current filter unchanged')
    parser.add_argument('--articulated-tilt-restoration',action='store_true',help='Engineering native-relative tilt OU process with inherited stationary protections')
    parser.add_argument('--contact-leg-only',action='store_true',help='Diagnostic bounded contact-only leg correction; raw and velocity consider pose, no OU')
    parser.add_argument('--natural-geometry-only',action='store_true',help='Keep physical states separate from the natural IK geometry map')
    parser.add_argument('--articulated-fit',type=Path,help='Matching PRE_IK/RESULT calibration model directory')
    parser.add_argument('--supported-point-raw-routing',action='store_true',help='Diagnostic correction-only consideration of fresh supported points; native motion unchanged')
    parser.add_argument('--conditional-imu-heading',action='store_true',help='Opt-in grouped yaw corrections in the joint/contact owner; preserve native IMU tilt and relative elbow/knee motion')
    parser.add_argument('--calibration-moving-support',action='store_true',help='Use fitted moving-support group evidence in standing calibration contexts; never adds stationary evidence')
    parser.add_argument('--raw-structural-heading',action='store_true',help='Raw heading updates only affect groups with actual tag FK sensitivity; preserve full covariance')
    parser.add_argument('--world-support-policy',choices=('legacy','protocol','unavailable'),default='legacy',help='Separate no-slip world authority from quiet/contact features; protocol is an offline calibration prior, unavailable adds no world no-slip observations')
    parser.add_argument('--native-tag-epoch-correction',action='store_true',help='Evaluate articulated tag position and Jacobian at each raw link epoch using causal native relative motion')
    args = parser.parse_args(argv)
    with CooperativeTermination() as termination:
        return run(args.frontend, args.pose, args.output, args.duration_s, args.max_runtime_s, args.feedback_mode,
        args.large_shadow,args.antenna_normals,args.back_cut,args.articulated_rotations,args.articulated_mode,args.joint_bias_variance,args.persistent_error_rotations,args.inertial_acceleration_noise_density,args.propagation_admission_safety,args.signed_effective_discrepancy,args.root_bias_variance,args.support_velocity,args.support_position_guard,args.protocol_contact_regions,args.correction_time_constant_s,args.contact_lifecycle,args.supervised_contact,args.support_points,args.joint_raw_initialization,args.calibration_phase_prior,args.corrected_discrepancy_likelihood,args.partial_range_tracking,args.registered_root_wear_frame,args.joint_tag_contact,args.restart_stationary_episode,args.soft_support_position_guard,args.joint_transition_tape,args.articulated_contact,args.fixed_lag_marginal,args.articulated_tilt_restoration,args.correction_gain_scope,args.rotation_position_bridge,args.navigation_position_handoff,args.contact_leg_only,args.natural_geometry_only,args.articulated_fit,
        supported_point_raw_routing=args.supported_point_raw_routing,
        conditional_imu_heading=args.conditional_imu_heading,stop_request=termination,
        calibration_moving_support=args.calibration_moving_support,
        raw_structural_heading=args.raw_structural_heading,world_support_policy=args.world_support_policy,
        native_tag_epoch_correction=args.native_tag_epoch_correction)


if __name__ == '__main__':
    main()
