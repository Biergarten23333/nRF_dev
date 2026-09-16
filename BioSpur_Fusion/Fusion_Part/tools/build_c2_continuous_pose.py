#!/usr/bin/env python3
"""Continuous ten-sensor pose using one initial mounting alignment and existing IK/FK."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import numpy as np
from scipy.spatial.transform import Rotation
from biospur_fusion.c2_3a_kinematics import load_frozen_c2_3a
from biospur_fusion.c2_3a_kinematics.interface import POINT_NAMES
from biospur_fusion.c2_coupled_progressive.contracts import NODE_TO_SEGMENT
from biospur_fusion.c2_coupled_progressive.calibration_native200_archive import CalibrationNative200Archive
from biospur_fusion.c2_uwb_calibration.frozen_body_proxy import frozen_world_alignment, NODE_TO_PROXY_POINT
from biospur_fusion.c2_uwb_calibration.antenna_los import NODE_OUTWARD_MINUS_Z_IN_SEGMENT
from biospur_fusion.c2_articulated_biomechanics.orientation_ik import solve_hinge_flexion_deg, reconstruct_distal_orientation
from tools.build_c2_full_session_ten_node_ab import _hinges, _static_inputs
from biospur_fusion.c2_uwb_calibration.articulated_range import SEGMENTS


def matrix(q):
    return Rotation.from_quat(q[:, [1, 2, 3, 0]]).as_matrix()


def quaternion(r):
    return Rotation.from_matrix(r).as_quat()[:, [3, 0, 1, 2]]


def batch_fk(rotations, geometry):
    """Vectorized exact corrected_proxy_points geometry, tested against that owner."""
    n = len(rotations['pelvis'])
    def rot(segment, v):
        return rotations[segment] @ np.asarray(v)
    points = {'pelvis_center': np.zeros((n, 3)),
              'shoulder_mid': rot('torso', [0, 0, geometry.torso_height_m])}
    for side, sign in [('left', -1), ('right', 1)]:
        points['shoulder_'+side] = points['shoulder_mid'] + rot('torso', [sign*geometry.shoulder_span_m/2, 0, 0])
        points['hip_'+side] = rot('pelvis', [sign*geometry.hip_span_m/2, 0, 0])
        for start, end, segment in [('shoulder','elbow','upper_arm'), ('elbow','wrist','forearm'),
                                    ('hip','knee','thigh'), ('knee','ankle','shank')]:
            segment += '_'+side
            points[end+'_'+side] = points[start+'_'+side] + rot(segment, [0,0,-geometry.segment_length_m[segment]])
    return points


def build(frontend, output, *, rotations_output=None):
    if output.exists():
        raise ValueError('output must be new')
    if rotations_output is not None and (rotations_output.exists() or rotations_output.resolve() == output.resolve()):
        raise ValueError('rotation export must be a distinct new file')
    manifest = json.loads((frontend/'RESULT.json').read_text())
    nodes = tuple(NODE_TO_SEGMENT)
    streams = {}
    for node in nodes:
        with np.load(frontend/f'{node}.npz') as z:
            streams[node] = {k:z[k] for k in ['common_global_ns','availability_global_ns','acc_mps2','quat_vqf_sensor_wxyz']}
    pelvis = streams['BSFC2CC']
    # One common initial alignment after one second of actual continuous input.
    first = max(s['common_global_ns'][0] for s in streams.values()) + 1_000_000_000
    valid = pelvis['common_global_ns'] >= first
    times = pelvis['common_global_ns'][valid]
    availability = pelvis['availability_global_ns'][valid].copy()
    kin = load_frozen_c2_3a()
    alignment, _ = frozen_world_alignment(kin)
    old = CalibrationNative200Archive.from_sealed_archives().actions['00_initial_still'].trajectory
    rotations, selections = {}, {}
    for node, segment in NODE_TO_SEGMENT.items():
        s = streams[node]
        index = np.searchsorted(s['common_global_ns'], times, side='right')-1
        if np.any(index < 0):
            raise ValueError('pose precedes node coverage')
        selections[node] = index
        raw = matrix(s['quat_vqf_sensor_wxyz'][index])
        initial_segment = matrix(old[segment]['quat_world_segment_wxyz'][:1])[0]
        mounting = raw[0].T @ initial_segment
        rotations[segment] = alignment @ raw @ mounting
        availability = np.maximum(availability, s['availability_global_ns'][index])
    hinge_audit = {}
    for name, joint in _hinges().items():
        p, c = quaternion(rotations[joint.parent]), quaternion(rotations[joint.child])
        flexion, stats = solve_hinge_flexion_deg(p, c, joint)
        corrected, reconstruction = reconstruct_distal_orientation(p, c, flexion, joint)
        rotations[joint.child] = matrix(corrected)
        hinge_audit[name] = {**stats, **reconstruction}
    points = batch_fk(rotations, kin.geometry)
    # The inherited helper calls local +X forward, whereas this FK uses X
    # for hip span. Preserve the accepted mirrored-body convention and bind
    # its horizontal front (right cross up) to the attested ABEF direction.
    right = points['hip_right'][0] - points['hip_left'][0]
    right[2] = 0
    forward = np.cross(right, [0., 0., 1.])
    if np.linalg.norm(forward) < 1e-6:
        raise ValueError('initial hip span cannot define horizontal heading')
    yaw = -np.pi/2 - np.arctan2(forward[1], forward[0])
    heading = Rotation.from_rotvec([0.,0.,yaw]).as_matrix()
    rotations = {s:heading @ r for s,r in rotations.items()}
    points = batch_fk(rotations, kin.geometry)
    alignment = heading @ alignment
    if rotations_output is not None:
        sensor_rotation = alignment @ matrix(pelvis['quat_vqf_sensor_wxyz'][valid])
        mounting = sensor_rotation[0].T @ rotations['pelvis'][0]
        if not np.allclose(sensor_rotation @ mounting, rotations['pelvis'], atol=1e-12, rtol=0):
            raise ValueError('pelvis sensor/segment mapping is not constant')
        rotations_output.parent.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(rotations_output, time_s=times.astype(float)*1e-9,
            base_segment_rotations_world=np.stack([rotations[s] for s in SEGMENTS],axis=1),
            segment_names=np.asarray(SEGMENTS), pelvis_mount_sensor_from_segment=mounting)
    _, _, anchors, _, _ = _static_inputs()
    output.parent.mkdir(parents=True, exist_ok=True)
    np.savez(output, time_s=times.astype(float)*1e-9,
        availability_time_s=availability.astype(float)*1e-9,
        joints_relative=np.stack([points[n] for n in POINT_NAMES], axis=1),
        node_offsets=np.stack([points[NODE_TO_PROXY_POINT[n]] for n in nodes],axis=1),
        node_normals_world=np.stack([rotations[NODE_TO_SEGMENT[n]] @ NODE_OUTWARD_MINUS_Z_IN_SEGMENT[n] for n in nodes],axis=1),
        pelvis_rotation_world_sensor=alignment @ matrix(pelvis['quat_vqf_sensor_wxyz'][valid]),
        pelvis_acc_sensor=pelvis['acc_mps2'][valid],
        node_names=np.asarray(nodes), joint_names=np.asarray(POINT_NAMES), anchors_m=anchors)
    audit = {'role':'CONTINUOUS_VQF_INITIAL_MOUNT_EXISTING_HINGE_FK_DIAGNOSTIC',
             'frames':len(times), 'pose_archive_runtime_overwrite':False,
             'initial_mount_alignments_per_node':1, 'initial_preparation_s':1,
             'heading_correction_deg':float(np.degrees(yaw)),
             'initial_forward_convention':'ACCEPTED_MIRRORED_BODY_RIGHT_CROSS_UP_TO_ABEF_MINUS_Y',
             'calibration_parameters':'REUSED;NOT_REFIT', 'geometry':'DISPLAY_PROXY_NOT_MEASURED_ANTENNA_CENTRES',
             'frontend':str(frontend.resolve()), 'regions':manifest['regions'],
             'first_time_s':float(times[0]*1e-9), 'last_time_s':float(times[-1]*1e-9),
             'hinges':hinge_audit, 'scientific_pass':False}
    output.with_suffix('.json').write_text(json.dumps(audit,indent=2)+'\n')
    return audit


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--frontend',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--rotations-output',type=Path)
    a=p.parse_args(); print(json.dumps(build(a.frontend,a.output,rotations_output=a.rotations_output),default=str)[:1500])
