"""Post-freeze comparison and review artifacts; never imported by the estimator."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
from scipy.spatial.transform import Rotation, Slerp

from .inputs import ROOT, CLOCK, sha
from .reference_timing import reference_sample_times, H_REPORT

CAL_REFERENCE = ROOT / 'logs/c2_pose_reset_qmt_avatar_v16_20260831_215300/POSE_RESET_QMT_TRAJECTORY.npz'
H_REFERENCE = ROOT / 'logs/c2_hxx_frozen_replay_20260831_220900/HXX_FROZEN_C2_REPLAY_TRAJECTORY.npz'
PAIRS = [('upper_arm_left','forearm_left'), ('upper_arm_right','forearm_right'),
         ('thigh_left','shank_left'), ('thigh_right','shank_right')]
LINES = [(0,1),(0,4),(1,4),(0,7),(0,10),(7,10),(1,2),(2,3),
         (4,5),(5,6),(7,8),(8,9),(10,11),(11,12)]


def baseline_on_grid(archive, key, times, contract, holdout):
    out, mask = {}, np.ones(len(times), bool)
    for segment in ['pelvis', 'torso'] + [s for pair in PAIRS for s in pair]:
        base = f'trajectory/{key}/{segment}'
        t = archive[base + '/time_root_s']
        t = reference_sample_times(t, key, segment, holdout)
        q = archive[base + '/quat_world_segment_wxyz']
        mask &= (times >= t[0]) & (times <= t[-1])
        clipped = np.clip(times, t[0], t[-1])
        out[segment] = Slerp(t-t[0], Rotation.from_quat(q[:, [1,2,3,0]]))(clipped-t[0]).as_matrix()
        index = np.clip(np.searchsorted(t, clipped), 1, len(t)-1)
        mask &= archive[base + '/mask'][index] & archive[base + '/mask'][index-1]
    return out, mask


def baseline_display(rotations, lengths, height, halfwidth):
    # Existing C2's left axis is -X; sparse model's left axis is +Y.
    b = Rotation.from_euler('z', -np.pi/2).as_matrix()
    rr = {k: b @ v @ b.T for k,v in rotations.items()}
    n = len(rr['pelvis']); j = np.zeros((n, 13, 3))
    for k, (parent, child) in enumerate(PAIRS):
        sign, arm = (1 if k % 2 == 0 else -1), k < 2
        v = np.array([0, sign * lengths['shoulder_width']/2, height]) if arm else np.array([0, sign*halfwidth, 0])
        base = np.einsum('nij,j->ni', rr['torso' if arm else 'pelvis'], v)
        mid = base - lengths['upper_arm' if arm else 'thigh'] * rr[parent][:,:,2]
        tip = mid - lengths['forearm' if arm else 'shank'] * rr[child][:,:,2]
        j[:, 1+3*k:4+3*k] = np.stack([base, mid, tip], axis=1)
    return j


def report(out, destination=None):
    destination = out if destination is None else destination
    destination.mkdir(parents=True, exist_ok=True)
    if not (out/'RUN_COMPLETE.json').exists():
        raise RuntimeError('comparison requires completed frozen five-node replay')
    calibration = json.loads((out/'CALIBRATION_FROZEN.json').read_text())
    seal = json.loads((out/'FREEZE_SEAL.json').read_text())['calibration_sha256']
    if sha(out/'CALIBRATION_FROZEN.json') != seal:
        raise RuntimeError('calibration seal mismatch')
    metrics = json.loads((out/'REPLAY_METRICS.json').read_text())
    c_audit = json.loads((out/'CALIBRATION_INPUT_AUDIT.json').read_text())
    h_audit = json.loads((out/'HOLDOUT_INPUT_AUDIT.json').read_text())
    comparison, viewer = {}, {}
    with np.load(CAL_REFERENCE, allow_pickle=False) as calref, np.load(H_REFERENCE, allow_pickle=False) as href:
        for i, (name, metric) in enumerate(metrics.items()):
            holdout = name.startswith('H')
            contract = (h_audit if holdout else c_audit)['contracts'][name]
            with np.load(out/(name+'_REPLAY.npz'), allow_pickle=False) as a:
                ref, valid = baseline_on_grid(href if holdout else calref, name if holdout else f'{i:02d}', a['time_s'], contract, holdout)
                source_valid=valid & a['input_valid']
                valid=source_valid & a['optimizer_success']
                bends = np.column_stack([np.rad2deg(np.arccos(np.clip(np.sum(ref[p][:,:,2]*ref[c][:,:,2], axis=1),-1,1))) for p,c in PAIRS])
                error = abs(a['bend_deg'] - bends)
                torso_ref = np.rad2deg(Rotation.from_matrix(np.swapaxes(ref['pelvis'],1,2) @ ref['torso']).magnitude())
                torso_ours = np.full(len(valid), np.nan)
                torso_ours[valid] = np.rad2deg(Rotation.from_matrix(
                    np.swapaxes(a['retained_rotations'][valid,0],1,2) @ a['torso_rotations'][valid]).magnitude())
                frame_change = Rotation.from_euler('z',-np.pi/2).as_matrix()
                retained_error, proximal_error = [], []
                for k, (parent, child) in enumerate(PAIRS):
                    for name_key, ours, collector in (
                        (child, a['retained_rotations'][:,k+1], retained_error),
                        (parent, a['proximal_rotations'][:,k], proximal_error)):
                        ref_dir = np.einsum('nij,nj->ni',np.swapaxes(ref['pelvis'],1,2),ref[name_key][:,:,2]) @ frame_change.T
                        our_dir = np.einsum('nij,nj->ni',np.swapaxes(a['retained_rotations'][:,0],1,2),ours[:,:,2])
                        collector.append(np.rad2deg(np.arccos(np.clip(np.sum(ref_dir*our_dir,axis=1),-1,1))))
                comparison[name] = dict(compared_frames=int(valid.sum()),
                    bend_mae_deg=np.mean(error[valid],axis=0).tolist(),
                    bend_p95_abs_difference_deg=np.quantile(error[valid],.95,axis=0).tolist(),
                    torso_relative_angle_mae_deg=float(np.mean(abs(torso_ours[valid]-torso_ref[valid]))),
                    retained_long_axis_mae_deg=np.mean(np.array(retained_error)[:,valid],axis=1).tolist(),
                    proximal_long_axis_mae_deg=np.mean(np.array(proximal_error)[:,valid],axis=1).tolist(),
                    excluded_frames=int((~valid).sum()), reference_is_ground_truth=False)
                reference_models = np.stack([baseline_display(ref, calibration['lengths'],h,w)
                    for h,w in zip(calibration['torso_display_models_m'],calibration['hip_display_half_width_models_m'])], axis=1)
                np.savez_compressed(destination/(name+'_REFERENCE_COMPARISON.npz'),
                    time_s=a['time_s'], reference_bend_deg=bends, reference_joints_m=reference_models,
                    bend_abs_difference_deg=error, comparison_valid=valid, source_comparison_valid=source_valid)
                # Human inspection can compare all three unobserved geometry hypotheses.
                viewer[name] = dict(t=np.round(a['time_s']-contract['lo'],3).tolist(),
                    sample_time_s=np.round(a['time_s'],6).tolist(),
                    five=np.round(a['joints_m'],4).tolist(), ten=np.round(reference_models,4).tolist(),
                    valid=valid.tolist(), bends=np.round(a['bend_deg'],1).tolist(),
                    reference_bends=np.round(bends,1).tolist())
    sensitivity = {}
    for name in ('H01_boxing','H02_golf'):
        with np.load(out/(name+'_REPLAY.npz')) as nominal:
            for scale in (.5, 2.):
                if not (out/(name+f'_PRIOR_{scale:g}.npz')).exists():
                    continue
                with np.load(out/(name+f'_PRIOR_{scale:g}.npz')) as alt:
                    valid = nominal['input_valid'] & nominal['optimizer_success'] & alt['optimizer_success']
                    differences = abs(nominal['bend_deg'] - alt['bend_deg'])
                    sensitivity[name+f'/{scale:g}'] = dict(
                        bend_p95_difference_deg=np.quantile(differences[valid],.95,axis=0).tolist(),
                        wrist_p95_displacement_mm=(np.quantile(np.linalg.norm(nominal['joints_m'][valid,1][:,[3,6]]-alt['joints_m'][valid,1][:,[3,6]],axis=2),.95,axis=0)*1000).tolist())
    document = dict(schema='five-node-pure-imu-post-freeze-comparison-v2',
        calibration_sha256=seal, reference_role='REGRESSION_COMPARATOR_NOT_TRUTH',
        reference_sha256={str(p.relative_to(ROOT)):sha(p) for p in (CAL_REFERENCE,H_REFERENCE)},
        joint_angle_order=['elbow_left','elbow_right','knee_left','knee_right'],
        timing='Recover each reference node TIMER2 from original episode offsets or Hxx quantized grid and clock; map to common target clock. No fitted temporal shift.',
        comparison=comparison, prior_sensitivity=sensitivity,
        timing_source_sha256={str(p.relative_to(ROOT)):sha(p) for p in
            (CLOCK, H_REPORT, Path(__file__).with_name('reference_timing.py'))},
        timing_scope='Source-node timestamp recovery and interpolation of archived reference; original coupled heading updates preserved, not a rerun of ten-node calibration.',
        evaluation_source_sha256=sha(Path(__file__)))
    (destination/'REFERENCE_COMPARISON.json').write_text(json.dumps(document,indent=2,allow_nan=False)+'\n')
    build_viewer(destination/'REPLAY_REVIEW.html',viewer,calibration)
    return document


def build_viewer(path, episodes, calibration):
    from .viewer import write_viewer
    write_viewer(path, dict(episodes=episodes, lines=LINES,
        geometry=list(zip(calibration['torso_display_models_m'],
                          calibration['hip_display_half_width_models_m']))))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--destination',type=Path, help='write a new review without replacing previous artifacts')
    args = parser.parse_args()
    value = report(args.output, args.destination)
    print(json.dumps({k:v for k,v in value['comparison'].items() if k.startswith('H')},indent=2))
