"""Apply newly fitted calibration states to the full continuous IMU stream.

Only fitted world-yaw states are held between registered calibration windows;
raw IMU orientation keeps evolving, and common drift evolves analytically.
This explicitly remains offline full-capture calibration replay.
"""
import argparse
import json
from pathlib import Path

import numpy as np
from scipy.spatial.transform import Rotation

from biospur_fusion.c2_coupled_progressive.contracts import NODE_TO_SEGMENT
from biospur_fusion.c2_coupled_progressive.pose_reset_avatar import _quat_on_pelvis_time
from biospur_fusion.c2_coupled_progressive.estimator import interp_quat_wxyz
from biospur_fusion.c2_uwb_calibration.articulated_range import SEGMENTS
from biospur_fusion.c2_uwb_calibration.frozen_body_proxy import frozen_world_alignment
from biospur_fusion.c2_3a_kinematics import load_frozen_c2_3a
from biospur_fusion.c2_3a_kinematics.interface import POINT_NAMES
from biospur_fusion.c2_articulated_biomechanics.model import fit_articulated_model
from biospur_fusion.c2_articulated_biomechanics.orientation_ik import solve_hinge_flexion_deg, reconstruct_distal_orientation
from tools.audit_c2_continuous_calibration_pose import continuous_episodes
from tools.c2_continuous_pose_inputs import ContinuousPoseAlignedSpans
from tools.build_c2_avatar_interactive import _load_trajectory
from tools.build_c2_continuous_pose import matrix, quaternion, batch_fk


def export_coverage(frontend, output, times):
    """Expose interpolation across missing input and endpoint extrapolation."""
    masks = {}; counts = {}
    for node in NODE_TO_SEGMENT:
        with np.load(frontend/f'{node}.npz') as src:
            clock = src['common_global_ns'].astype(float)*1e-9
            right = np.searchsorted(clock, times)
            outside = (times < clock[0]) | (times > clock[-1])
            hi = np.clip(right, 1, len(clock)-1); lo = hi-1
            inside = (times-clock[lo] > 1e-9) & (clock[hi]-times > 1e-9)
            gap = inside & ((src['time_us'][hi]-src['time_us'][lo]) != 5000) & ~outside
            masks[node+'_gap_interpolated'] = gap
            masks[node+'_outside_coverage'] = outside
            counts[node] = dict(gap_interpolated=int(gap.sum()), outside_coverage=int(outside.sum()))
    np.savez_compressed(output/'COVERAGE.npz', time_s=times, **masks)
    (output/'COVERAGE.json').write_text(json.dumps(dict(nodes=counts,
        uniform_observed_coverage=False, policy='Diagnostic interpolation only; no production acceptance'),indent=2))


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--fit',type=Path,required=True)
    ap.add_argument('--frontend',type=Path,required=True)
    ap.add_argument('--output',type=Path,required=True)
    ap.add_argument('--audit-before-ik', action='store_true',
                    help='Save input rotations to distinguish bend-plane changes from mount errors')
    ap.add_argument('--endpoint-preserving-ik', action='store_true',
                    help='Diagnostic anatomical axial-coordinate solve; physical stream saved separately')
    ap.add_argument('--calibration-settling-s',type=float,
                    help='Causal settling time for calibration corrections only, not raw IMU motion')
    args=ap.parse_args(); args.output.mkdir(parents=True,exist_ok=False)
    report=json.loads((args.fit/'RESULT.json').read_text())
    pre=_load_trajectory(args.fit/'PRE_IK.npz')
    provider=ContinuousPoseAlignedSpans(); episodes,_=continuous_episodes(args.frontend,provider)
    fit=np.load(args.fit/'REPLAY_CALIBRATION.npz')
    old=np.load('logs/c2_tag_geometry_repair_20260912_154000/POSE_TAG_VERIFIED.npz')
    times=old['time_s']; embedding=old['geometry_embedding_from_previous']
    export_coverage(args.frontend, args.output, times)
    kin=load_frozen_c2_3a(); align,_=frozen_world_alignment(kin)
    yaw=json.loads(Path('logs/c2_joint_feedback_20260912T143535Z/POSE_REBUILT.json').read_text())['heading_correction_deg']
    align=Rotation.from_euler('z',yaw,degrees=True).as_matrix()@align
    model=fit_articulated_model(pre,report)
    ref=float(fit['reference_time_s']); duration=float(fit['final_reference_time_s'])-ref
    closure=float(fit['common_pelvis_yaw_closure_rad'])
    rotations={}; audit={}
    for node,seg in NODE_TO_SEGMENT.items():
        j=report['segment_order'].index(seg); mount=fit['initial_world_sensor'][j].T
        ts=[]; ys=[]; off=[]
        for ep in episodes:
            row=pre['trajectory'][f'{ep.chronological_index:02d}'][seg]
            t=row['time_root_s']; sensor=matrix(_quat_on_pelvis_time(ep,seg,t))
            delta=matrix(row['quat_world_segment_wxyz'])@np.swapaxes(sensor@mount,1,2)
            rv=Rotation.from_matrix(delta).as_rotvec()
            off.append(float(np.linalg.norm(rv[:,:2],axis=1).max()))
            total=np.arctan2(delta[:,1,0],delta[:,0,0])
            ts.append(t); ys.append(total+(t-ref)/duration*closure)
        if max(off)>1e-6:
            raise ValueError(f'{seg}: calibration state is not world yaw')
        state_time=np.concatenate(ts); state_yaw=np.concatenate(ys)
        if np.any(np.diff(state_time)<=0): raise ValueError('nonchronological state tape')
        index=np.searchsorted(state_time,times,side='right')-1
        if np.any(index<0): raise ValueError('output precedes fitted state coverage')
        with np.load(args.frontend/f'{node}.npz') as src:
            q=interp_quat_wxyz(src['common_global_ns'].astype(float)*1e-9,src['quat_vqf_sensor_wxyz'],times)
        world_yaw=state_yaw[index]-(times-ref)/duration*closure
        publication=None
        if args.calibration_settling_s is not None:
            from biospur_fusion.c2_native200_calibration.correction_publication import publish_heading_correction
            desired=world_yaw.copy()
            world_yaw,_=publish_heading_correction(times,desired,settling_s=args.calibration_settling_s)
            publication=dict(settling_s=args.calibration_settling_s,
                target_peak_step_deg=float(np.degrees(abs(np.angle(np.exp(1j*np.diff(desired))))).max()),
                applied_peak_step_deg=float(np.degrees(abs(np.diff(world_yaw))).max()),
                raw_imu_filtered=False)
        rotations[seg]=Rotation.from_euler('z',world_yaw.reshape(-1, 1)).as_matrix()@matrix(q)@mount
        audit[seg]=dict(max_off_yaw_rad=max(off),state_samples=len(state_time),
                        correction_publication=publication,
                        state_policy='previous fitted functional yaw; continuously evolving common drift and IMU')
    if args.audit_before_ik or args.endpoint_preserving_ik:
        np.savez_compressed(args.output/'PRE_IK_ROTATIONS.npz', time_s=times,
            base_segment_rotations_world=np.stack([align@rotations[s] for s in SEGMENTS],axis=1),
            segment_names=SEGMENTS)
    for name,joint in model.items():
        p,c=quaternion(rotations[joint.parent]),quaternion(rotations[joint.child])
        if args.endpoint_preserving_ik:
            from biospur_fusion.c2_articulated_biomechanics.bend_plane import reconcile_hinge_bend_plane
            parent,corrected,metrics=reconcile_hinge_bend_plane(p,c,joint)
            rotations[joint.parent]=matrix(parent)
            audit[name]=metrics
        else:
            flex,_=solve_hinge_flexion_deg(p,c,joint)
            corrected,_=reconstruct_distal_orientation(p,c,flex,joint)
        rotations[joint.child]=matrix(corrected)
    rotations={s:align@r for s,r in rotations.items()}
    points=batch_fk(rotations,kin.geometry)
    pose=np.stack([points[n]@embedding.T for n in POINT_NAMES],axis=1)
    np.savez_compressed(args.output/'POSE.npz',time_s=times,joints_relative=pose,
        joint_names=np.asarray(POINT_NAMES),geometry_embedding_from_previous=embedding,
        anchors_m=old['anchors_m'])
    np.savez_compressed(args.output/'ROTATIONS.npz',time_s=times,
        base_segment_rotations_world=np.stack([rotations[s] for s in SEGMENTS],axis=1),segment_names=SEGMENTS)
    (args.output/'RESULT.json').write_text(json.dumps(dict(segments=audit,frames=len(times),
        endpoint_preserving_ik=args.endpoint_preserving_ik,
        physical_orientation_stream='PRE_IK_ROTATIONS.npz' if args.endpoint_preserving_ik else None,
        offline_calibration_replay=True,root_fusion_recomputed=False,source_fit=str(args.fit.resolve())),indent=2))
    print('continuous calibrated pose exported',len(times),flush=True)

if __name__=='__main__':main()
