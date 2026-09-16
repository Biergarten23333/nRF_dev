"""Bounded real-data admission probe; never replaces the production estimator."""
import argparse
from collections import Counter
from dataclasses import asdict
from functools import partial
import json
from pathlib import Path
import time

import numpy as np

from biospur_fusion.c2_3a_kinematics import load_frozen_c2_3a
from biospur_fusion.c2_articulated_biomechanics.orientation_ik import project_hinge_corrections
from biospur_fusion.c2_uwb_calibration.articulated_range import SEGMENTS
from biospur_fusion.c2_uwb_calibration.contact_motion_step import (
    ContactMotionStepConfig, solve_contact_motion_step,
)
from build_c2_full_session_ten_node_ab import _hinges


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--seconds', type=float, default=25.)
    parser.add_argument('--wall', type=float, default=100.)
    parser.add_argument('--tracking', action='store_true')
    parser.add_argument('--stationary-target', action='store_true')
    parser.add_argument('--hinge-consistent', action='store_true')
    parser.add_argument('--pelvis-orientation', action='store_true')
    parser.add_argument('--correction-prediction', choices=('rate', 'constant'), default='rate')
    parser.add_argument('--resume-probe', type=Path)
    parser.add_argument('--start-frame', type=int, default=0)
    parser.add_argument('--stop-file', type=Path)
    parser.add_argument('--calibrated-pose', type=Path,
                        help='Directory containing time-matched POSE.npz and ROTATIONS.npz')
    parser.add_argument('--calibration-fit', type=Path,
                        help='Matching PRE_IK.npz and RESULT.json used to fit hinge axes')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    base = Path('logs/c2_passive_seated_repair_20260914_070933/full')
    with np.load(base/'CONTINUOUS_AB.npz') as b:
        time_s = b['time_s']; n = int(np.searchsorted(time_s, time_s[0]+args.seconds))
        t = time_s[:n].copy(); targets = b['roots_b_posterior'][:n].copy()
        native = b['joints_relative'][:n].copy(); names = b['joint_names'].tolist()
        velocities = b['root_state_b'][:n, 3:6].copy()
        anchors = b['anchors_world_m'].copy()
    with np.load(base/'SUPPORT_POINTS.npz') as s:
        stationary = (s['valid'][:n].astype(bool) & s['eligible'][:n].astype(bool)
                      & ~s['moving'][:n].astype(bool))
    if bool(args.calibrated_pose) != bool(args.calibration_fit):
        raise ValueError('calibrated pose and matching calibration fit are required together')
    rotation_path = (args.calibrated_pose/'ROTATIONS.npz' if args.calibrated_pose else
                     Path('logs/c2_joint_feedback_20260912T143535Z/ROTATIONS.npz'))
    pose_path = (args.calibrated_pose/'POSE.npz' if args.calibrated_pose else
                 Path('logs/c2_tag_geometry_repair_20260912_154000/POSE_TAG_VERIFIED.npz'))
    with np.load(rotation_path) as r:
        np.testing.assert_array_equal(r['time_s'][:n], t)
        assert r['segment_names'].tolist() == list(SEGMENTS)
        rotations = r['base_segment_rotations_world'][:n].copy()
    with np.load(pose_path) as p:
        embedding = p['geometry_embedding_from_previous'].copy()
        if args.calibrated_pose:
            np.testing.assert_array_equal(p['time_s'][:n], t)
            assert p['joint_names'].tolist() == names
            native = p['joints_relative'][:n].copy()
    geometry = load_frozen_c2_3a().geometry
    # Legs only: preserve the accepted native arm model exactly.
    if args.calibration_fit:
        from biospur_fusion.c2_articulated_biomechanics.model import fit_articulated_model
        from tools.build_c2_avatar_interactive import _load_trajectory
        all_hinges = fit_articulated_model(
            _load_trajectory(args.calibration_fit/'PRE_IK.npz'),
            json.loads((args.calibration_fit/'RESULT.json').read_text()))
    else:
        all_hinges = _hinges()
    model = {k: v for k, v in all_hinges.items() if k.startswith('knee_')}
    project = partial(project_hinge_corrections, model=model)
    roots = targets.copy(); poses = native.copy(); committed = 1
    correction_history = np.zeros((n, len(SEGMENTS), 3))
    root_velocity_history = np.zeros((n, 3))
    root_velocity_history[0] = velocities[0]
    correction = {s: np.zeros(3) for s in SEGMENTS}
    if args.tracking:
        from biospur_fusion.c2_uwb_calibration.contact_motion_tracking import (
            ContactMotionTrackingConfig, initialize_contact_motion_tracking, advance_contact_motion_tracking)
        tracking_config = ContactMotionTrackingConfig(correction_prediction=args.correction_prediction)
        tracking = initialize_contact_motion_tracking(targets[0], velocities[0])
    start = args.start_frame
    if start:
        if not args.tracking or args.resume_probe is None:
            raise ValueError('resume requires a saved tracking probe')
        from biospur_fusion.c2_uwb_calibration.contact_motion_tracking import ContactMotionTrackingState
        with np.load(args.resume_probe) as saved:
            np.testing.assert_array_equal(saved['time_s'][:start+1], t[:start+1])
            roots[:start+1] = saved['roots_candidate'][:start+1]
            poses[:start+1] = saved['pose_candidate'][:start+1]
            correction_history[:start+1] = saved['correction_rotvec'][:start+1]
            root_velocity_history[:start+1] = saved['root_velocity_candidate'][:start+1]
        correction = dict(zip(SEGMENTS, correction_history[start].copy()))
        rates = (correction_history[start]-correction_history[start-1])/(t[start]-t[start-1])
        tracking = ContactMotionTrackingState(roots[start].copy(), root_velocity_history[start].copy(),
                                             correction, dict(zip(SEGMENTS, rates)))
        committed = start+1
    feet = ('ankle_left', 'ankle_right'); indices = [names.index(s) for s in feet]
    reasons = Counter(); maximum_correction = 0.; elapsed = []
    stop = 'PREFIX_COMPLETE'
    config_kwargs = {'maximum_iterations': 24}
    if args.stationary_target:
        config_kwargs['stationary_velocity_sigma_m_s'] = .01
    solver_config = ContactMotionStepConfig(**config_kwargs)
    if args.pelvis_orientation and not args.hinge_consistent:
        raise ValueError('pelvis orientation requires the hinge-consistent solver')
    if args.hinge_consistent:
        if not args.tracking:
            raise ValueError('hinge-consistent probe requires tracking')
        from biospur_fusion.c2_uwb_calibration.contact_hinge_motion import ContactHingeMotionConfig
        solver_config = ContactHingeMotionConfig(solve_pelvis_orientation=args.pelvis_orientation, **config_kwargs)
    for i in range(start+1, n):
        if args.stop_file is not None and args.stop_file.exists():
            stop = 'EXTERNAL_STOP'; break
        if time.monotonic()-started > args.wall:
            stop = 'WALL_BOUND'; break
        dt = t[i]-t[i-1]
        active = stationary[i] & stationary[i-1] & (dt <= .0075)
        previous = {f: roots[i-1]+poses[i-1, indices[j]] for j, f in enumerate(feet) if active[j]}
        tick = time.monotonic()
        kwargs = dict(
            base_rotations_world=dict(zip(SEGMENTS, rotations[i])), geometry=geometry,
            previous_feet_world_m=previous, dt_s=dt,
            foot_speed_limits_m_s={f: .01 for f in previous},
            hinge_projector=project, embedding=embedding)
        if args.tracking:
            extra = {}
            if args.hinge_consistent:
                extra = dict(hinge_model=model, knee_motion_prior_m={
                    k: roots[i-1]+poses[i-1, names.index(k)]+native[i, names.index(k)]-native[i-1, names.index(k)]
                    for k in ('knee_left', 'knee_right')})
            tracking, result = advance_contact_motion_tracking(
                tracking, root_target_m=targets[i], upstream_root_velocity_m_s=velocities[i],
                solver_config=solver_config, config=tracking_config, **kwargs, **extra)
        else:
            result = solve_contact_motion_step(
                root_target_m=targets[i], root_prior_m=roots[i-1],
                previous_correction=correction,
                config=solver_config, **kwargs)
        elapsed.append(time.monotonic()-tick); reasons[result.reason] += 1
        if not result.accepted:
            # Fail visibly. Never manufacture low slip by holding rejected frames.
            failed = dict(frame=i, time_s=t[i], dt_s=dt, root_target=targets[i],
                          upstream_velocity=velocities[i], rotations=rotations[i],
                          embedding=embedding, active=active,
                          previous_world_feet=roots[i-1]+poses[i-1, indices],
                          root_committed=roots[i-1], previous_correction=correction_history[i-1])
            if args.tracking:
                failed.update(root_velocity=tracking.root_velocity_m_s,
                              correction_velocity=np.stack([tracking.correction_velocity_rad_s[s] for s in SEGMENTS]))
                if args.hinge_consistent:
                    failed['knee_motion_prior_m'] = np.stack([extra['knee_motion_prior_m'][k]
                                                            for k in ('knee_left', 'knee_right')])
            np.savez_compressed(args.output/'REJECTED_INPUT.npz', **failed)
            (args.output/'REJECTED_INPUT.json').write_text(json.dumps(dict(
                reason=result.reason, projection=result.projection,
                maximum_constraint_violation_m=result.maximum_constraint_violation_m,
                iterations=result.nit,
                solver_config=asdict(solver_config), segments=list(SEGMENTS),
                tracking_config=asdict(tracking_config) if args.tracking else None,
                frame=i, previous_state_is_committed=True),indent=2))
            stop = 'REJECTED_FRAME'; break
        roots[i] = result.root_position_m; correction = result.corrections
        correction_history[i] = np.stack([correction[s] for s in SEGMENTS])
        root_velocity_history[i] = ((roots[i]-roots[i-1])/dt if not args.tracking
                                    else tracking.root_velocity_m_s)
        for key, value in result.points.items():
            if key in names:
                poses[i, names.index(key)] = value
        maximum_correction = max(maximum_correction, max(np.linalg.norm(v) for v in correction.values()))
        committed = i+1
    t=t[:committed]; roots=roots[:committed]; poses=poses[:committed]
    stats = {}
    mask = stationary[1:committed] & stationary[:committed-1]
    for label, root, pose in [('baseline', targets[:committed], native[:committed]), ('candidate', roots, poses)]:
        step = np.linalg.norm(np.diff(root, axis=0), axis=1)
        footstep = np.linalg.norm(np.diff(root[:, None]+pose[:, indices], axis=0), axis=2)
        stats[label] = {'root_max_step_m': float(step.max(initial=0)),
                        'stationary_foot_path_m': [float(footstep[:, j][mask[:, j]].sum()) for j in range(2)]}
    report = dict(status=stop, committed_frames=committed, requested_frames=n,
                  duration_s=float(t[-1]-t[0]), reasons=dict(reasons),
                  max_root_target_distance_m=float(np.linalg.norm(roots-targets[:committed], axis=1).max()),
                  max_leg_correction_rad=maximum_correction, stats=stats,
                  solve_median_ms=float(np.median(elapsed)*1000), wall_s=time.monotonic()-started,
                  explicit_tracking_state=args.tracking,
                  stationary_velocity_target=args.stationary_target,
                  hinge_consistent=args.hinge_consistent, resumed_after_frame=start,
                  pelvis_orientation=args.pelvis_orientation,
                  correction_prediction=args.correction_prediction,
                  newly_solved_frames=committed-start-1,
                  newly_solved_interval_relative_s=[float(t[start]-t[0]), float(t[-1]-t[0])],
                  prefix_metrics_include_inherited_history=bool(start),
                  production_replaced=False, scientific_pass=False)
    report['calibrated_pose'] = str(args.calibrated_pose) if args.calibrated_pose else None
    report['calibration_fit'] = str(args.calibration_fit) if args.calibration_fit else None
    report['upstream_root_and_contact_classification_recomputed'] = False
    np.savez_compressed(args.output/'PROBE.npz', time_s=t, roots_baseline=targets[:committed],
                        roots_candidate=roots, pose_baseline=native[:committed], pose_candidate=poses,
                        stationary=stationary[:committed], joint_names=names,
                        correction_rotvec=correction_history[:committed],
                        root_velocity_candidate=root_velocity_history[:committed], segment_names=SEGMENTS)
    np.savez_compressed(args.output/'VIEW.npz', time_s=t, roots_a=targets[:committed],
                        roots_b=roots, joints_relative=native[:committed], joints_relative_b=poses,
                        anchors_world_m=anchors, joint_names=names)
    (args.output/'RESULT.json').write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2), flush=True)


if __name__ == '__main__':
    main()
