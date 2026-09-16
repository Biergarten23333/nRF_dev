"""Re-run existing native calibration on continuous IMU input; no pose copying.

Registered actions select calibration factors, not orientation resets. All
sensor timestamps are put on the existing beacon world clock before fitting.
This is an offline full-calibration replay, not an online latency claim.
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import numpy as np

from biospur_fusion.c2_coupled_progressive.frontend import (
    VerifiedFrontendArchive, NodeSeries, EpisodeFrontend,
)
from biospur_fusion.c2_coupled_progressive.output_coordinates import freeze_capture_wide_lateral_reflection
from biospur_fusion.c2_coupled_progressive.contracts import load_effective_config, NODE_TO_SEGMENT
from biospur_fusion.c2_coupled_progressive.renderer import display_models, joints_for_frame, render_triptych
from biospur_fusion.c2_native200_calibration import load_native200_pose_reset_module
from biospur_fusion.c2_articulated_biomechanics.model import fit_articulated_model
from biospur_fusion.c2_articulated_biomechanics.orientation_ik import apply_orientation_constrained_ik
from tools.run_c2_pose_reset_avatar import _trajectory_npz, _frozen_replay_npz
from tools.c2_pose_stage_audit import summarize_pose_stage


def continuous_episodes(frontend: Path, provider):
    archive = VerifiedFrontendArchive()
    archive.verify_seal_and_semantics()
    streams = {}
    for node in NODE_TO_SEGMENT:
        with np.load(frontend / f'{node}.npz') as z:
            streams[node] = {k: z[k] for k in z.files}
    result, audit = [], []
    for old in archive.episodes():
        nodes = {}
        native_by_node = {}
        for node, series in old.nodes.items():
            src = streams[node]
            ix = np.searchsorted(src['time_us'], series.time_us)
            if np.any(ix >= len(src['time_us'])) or not np.array_equal(src['time_us'][ix], series.time_us):
                raise ValueError(f'nonexact input identity: {old.qa_label}/{node}')
            if not np.array_equal(src['boot_epoch'][ix], series.derived_boot_epoch):
                raise ValueError(f'boot mismatch: {old.qa_label}/{node}')
            nodes[node] = NodeSeries(
                time_us=src['common_global_ns'][ix].astype(float) / 1000,
                derived_boot_epoch=src['boot_epoch'][ix],
                acc_mps2=src['acc_mps2'][ix], gyro_rads=src['gyro_rads'][ix],
                quat_world_sensor_wxyz=src['quat_vqf_sensor_wxyz'][ix],
                contiguous_span_id=src['contiguous_span_id'][ix],
                gap_covariance_rad2=series.gap_covariance_rad2,
            )
            native_by_node[node] = {k: src[k][ix] for k in
                ('time_us', 'common_global_ns', 'boot_epoch', 'contiguous_span_id')}
            audit.append(dict(action=old.qa_label, node=node, samples=len(ix),
                              exact_timer_and_boot=True, clock='existing beacon common_global_ns'))
        reports = json.loads(json.dumps(old.pair_alignment_reports))
        for report in reports.values():
            report['corresponding_timing_span_windows']['predicted_parent_minus_child_offset_s'] = 0.0
        episode = EpisodeFrontend(old.chronological_index, old.qa_label, nodes, reports)
        provider.register(episode, native_by_node)
        result.append(episode)
    return result, audit


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--frontend', type=Path, required=True)
    ap.add_argument('--output', type=Path, required=True)
    ap.add_argument('--resume-preik', type=Path, help='Reuse completed pre-IK solve after export interruption')
    policy = ap.add_mutually_exclusive_group(required=True)
    policy.add_argument('--legacy-standing-reset', action='store_true',
                        help='Rejected historical diagnostic only: erases natural initial flexion')
    policy.add_argument('--functional-wear-mounts', action='store_true',
                    help='Diagnostic functional axes + qualitative wear mounts; no standing reset')
    policy.add_argument('--mount-posterior', type=Path,
                        help='Explicit same-capture fixed mounting posterior, not a pose archive')
    args = ap.parse_args()
    args.output = args.output.resolve()
    args.output.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    native, adapter = load_native200_pose_reset_module()
    from tools.c2_continuous_pose_inputs import ContinuousPoseAlignedSpans
    provider = ContinuousPoseAlignedSpans()
    episodes, input_audit = continuous_episodes(args.frontend, provider)
    native.aligned_spans_for_episode = provider
    (args.output/'INPUT.json').write_text(json.dumps(input_audit, indent=2))
    calibration = native.estimate_pose_reset_calibration(episodes)
    mount_audit = None
    if args.mount_posterior:
        if args.resume_preik:
            raise ValueError('cannot reuse a different mounting solve')
        from biospur_fusion.c2_native200_calibration.functional_mount import calibration_with_mounts
        from tools.build_c2_avatar_interactive import _sha256
        prior = json.loads(args.mount_posterior.read_text())['summary']
        calibration = calibration_with_mounts(calibration, prior['sensor_from_segment_mean'])
        mount_audit = dict(source=str(args.mount_posterior.resolve()), sha256=_sha256(args.mount_posterior),
                           policy='same capture fixed mount reuse; no pose samples copied',
                           covariance=prior['mount_covariance_rad2'], validated=False)
        (args.output/'MOUNT_AUDIT.json').write_text(json.dumps(mount_audit, indent=2))
    if args.functional_wear_mounts:
        if args.resume_preik:
            raise ValueError('cannot reuse pose solved with a different mounting model')
        from biospur_fusion.c2_native200_calibration.functional_mount import (
            functional_wear_mounts, calibration_with_mounts)
        mounts, mount_audit = functional_wear_mounts(native, episodes, calibration)
        calibration = calibration_with_mounts(calibration, mounts)
        (args.output/'MOUNT_AUDIT.json').write_text(json.dumps(mount_audit, indent=2))
    print('initial calibration ready', flush=True)
    trajectory = native.build_pose_reset_trajectory(episodes, calibration, sample_step=1)
    stage_audit = {'mounted_input': summarize_pose_stage(trajectory)}
    report = dict(adapter=adapter, frontend=str(args.frontend.resolve()), offline_full_calibration=True,
                  action_resets=0, scientific_pass=False, fusion_recomputed=False)
    report['mounting'] = mount_audit
    stages = [
        ('protocol_torso_heading', lambda: native.apply_protocol_torso_heading_updates(trajectory, episodes)),
    ]
    if not args.resume_preik:
        for name, fn in stages:
            report[name] = fn()
            stage_audit[name] = summarize_pose_stage(trajectory)
    axes, report['qmt_olsson_hinge_axes'] = native.estimate_hinge_axes_olsson(episodes, calibration)
    print('functional axes ready', flush=True)
    if args.resume_preik:
        from tools.build_c2_avatar_interactive import _load_trajectory, _sha256
        trajectory = _load_trajectory(args.resume_preik)
        report['resumed_preik'] = dict(path=str(args.resume_preik.resolve()),sha256=_sha256(args.resume_preik),
                                     reason='completed solve, relative-path metadata export failure')
        for ep in episodes:
            np.testing.assert_allclose(trajectory['trajectory'][f'{ep.chronological_index:02d}']['pelvis']['time_root_s'],
                                       ep.nodes['BSFC2CC'].time_us*1e-6,rtol=0,atol=1e-9)
    else:
        report['protocol_shoulder_heading'] = native.apply_protocol_shoulder_plane_updates(trajectory, episodes, axes)
        stage_audit['protocol_shoulder_heading'] = summarize_pose_stage(trajectory)
        report['protocol_hip_heading'] = native.apply_protocol_hip_heading_soft_updates(trajectory, episodes)
        stage_audit['protocol_hip_heading'] = summarize_pose_stage(trajectory)
        report['qmt_hinges'] = native.apply_qmt_hinge_soft_updates(trajectory, episodes, axes)
        stage_audit['qmt_hinges'] = summarize_pose_stage(trajectory)
    print('all 19 calibration heading updates ready', flush=True)
    if 'output_coordinate_convention' not in trajectory:
        trajectory['output_coordinate_convention'] = freeze_capture_wide_lateral_reflection(trajectory)
    _trajectory_npz(args.output/'PRE_IK.npz', trajectory)
    frozen = native.freeze_pose_reset_replay_calibration(trajectory, episodes, calibration)
    _frozen_replay_npz(args.output/'REPLAY_CALIBRATION.npz', frozen)
    report['segment_order'] = frozen['segment_order']
    model = fit_articulated_model(trajectory, report)
    corrected, report['orientation_ik'] = apply_orientation_constrained_ik(trajectory, model)
    stage_audit['orientation_ik'] = summarize_pose_stage(corrected)
    (args.output/'STAGE_ATTRIBUTION.json').write_text(json.dumps(stage_audit, indent=2))
    _trajectory_npz(args.output/'POST_IK.npz', corrected)
    config = load_effective_config(); display = display_models(config)[1]
    renders = []
    for ep in episodes:
        key = f'{ep.chronological_index:02d}'
        row = corrected['trajectory'][key]['pelvis']
        for fraction in (.25, .5, .75):
            index = int((len(row['time_root_s'])-1)*fraction)
            path = args.output/f'{ep.qa_label}_{int(fraction*100)}.png'
            render_triptych(joints_for_frame(corrected,key,index,display,config),path,
                            f'{ep.qa_label} at {fraction:.0%} | recalibrated continuous IMU')
            renders.append(str(path))
    report['renders'] = renders
    report['wall_s'] = time.monotonic()-started
    (args.output/'RESULT.json').write_text(json.dumps(report, indent=2, default=lambda v: v.tolist() if isinstance(v,np.ndarray) else str(v)))
    print(json.dumps(dict(actions=len(episodes),wall_s=report['wall_s'],output=str(args.output))),flush=True)


if __name__ == '__main__':
    main()
