"""Build matched natural FK and physical sensor inputs; never reuse old roots.

This exports an offline calibration diagnostic. Anatomical parent-axis changes
must not be mistaken for physical sensor rotations by the downstream filter.
"""
import argparse
import hashlib
import json
from pathlib import Path

import numpy as np

from biospur_fusion.c2_coupled_progressive.contracts import NODE_TO_SEGMENT
from biospur_fusion.c2_uwb_calibration.frozen_body_proxy import NODE_TO_PROXY_POINT
from biospur_fusion.c2_uwb_root_world.wear_frame import (
    initial_wear_yaw_registration, registered_pelvis_rotations, WORLD_FROM_WEAR,
    common_anatomical_world_yaw,
)
from biospur_fusion.c2_uwb_calibration.antenna_los import NODE_OUTWARD_MINUS_Z_IN_SEGMENT
from biospur_fusion.c2_uwb_calibration.articulated_range import _proxy_points_from_rotations


def _sha256(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream,'sha256').hexdigest()


def build(continuous, fit, source, output, *, common_world_yaw=False):
    output.mkdir(parents=True, exist_ok=False)
    with np.load(continuous/'POSE.npz') as p, np.load(continuous/'ROTATIONS.npz') as a, \
            np.load(continuous/'PRE_IK_ROTATIONS.npz') as physical, np.load(source) as raw:
        for stream in (a, physical, raw):
            np.testing.assert_array_equal(p['time_s'], stream['time_s'])
        np.testing.assert_array_equal(a['segment_names'], physical['segment_names'])
        names=p['joint_names'].tolist(); segments=a['segment_names'].tolist()
        nodes=raw['node_names'].tolist()
        if set(nodes)!=set(NODE_TO_SEGMENT):
            raise ValueError('exact ten-node inventory required')
        report=json.loads((fit/'RESULT.json').read_text())
        with np.load(fit/'REPLAY_CALIBRATION.npz') as calibration:
            mounts=np.stack([calibration['initial_world_sensor'][report['segment_order'].index(s)].T
                             for s in segments])
        anatomical_rotations=a['base_segment_rotations_world']
        physical_rotations=physical['base_segment_rotations_world']
        values={k:p[k] for k in p.files}
        common_yaw=None
        if common_world_yaw:
            from biospur_fusion.c2_3a_kinematics import load_frozen_c2_3a
            geometry=load_frozen_c2_3a().geometry
            initial=_proxy_points_from_rotations(dict(zip(segments,anatomical_rotations[:1].swapaxes(0,1))),geometry)
            common_yaw=common_anatomical_world_yaw(initial['hip_right'][0]-initial['hip_left'][0])
            anatomical_rotations=common_yaw@anatomical_rotations
            physical_rotations=common_yaw@physical_rotations
            rebuilt=_proxy_points_from_rotations(dict(zip(segments,anatomical_rotations.swapaxes(0,1))),geometry)
            values.update(joints_relative=np.stack([rebuilt[n] for n in names],axis=1),
                geometry_embedding_from_previous=np.eye(3),
                source_display_embedding_from_previous=p['geometry_embedding_from_previous'],
                common_world_yaw_from_calibrated=common_yaw)
        # R_world_sensor = R_world_segment @ R_sensor_segment.T.
        sensor=physical_rotations@mounts.swapaxes(1,2)
        np.testing.assert_allclose(sensor.swapaxes(-1,-2)@sensor,np.broadcast_to(np.eye(3),sensor.shape),atol=1e-9)
        np.testing.assert_allclose(np.linalg.det(sensor),1.,atol=1e-9)
        points=dict(zip(names,values['joints_relative'].swapaxes(0,1)))
        metadata=json.loads(source.with_suffix('.json').read_text())
        from biospur_fusion.c2_3a_kinematics import load_frozen_c2_3a
        height=load_frozen_c2_3a().geometry.torso_height_m
        chest_scale=1.-float(np.mean(metadata['chest_vertical_observations_m']))/height
        tags={n:points[NODE_TO_PROXY_POINT[n]] for n in nodes}
        tags['BSF31CC']=chest_scale*points['shoulder_mid']
        physical_normals=np.stack([-sensor[:,segments.index(NODE_TO_SEGMENT[n]),:,2] for n in nodes],axis=1)
        registrations=[];normals=[];wear_audit={}
        for i,node in enumerate(nodes):
            if common_world_yaw:
                yaw=np.eye(3);target=WORLD_FROM_WEAR@NODE_OUTWARD_MINUS_Z_IN_SEGMENT[node]
            else:
                yaw,target=initial_wear_yaw_registration(node,physical_normals[0,i])
            registered=physical_normals[:,i]@yaw.T
            registrations.append(yaw);normals.append(registered)
            wear_audit[node]=dict(initial_physical_normal_world=physical_normals[0,i].tolist(),
                wear_target_world=target.tolist(),initial_registered_normal_world=registered[0].tolist(),
                yaw_registration_world=yaw.tolist(),
                initial_prior_angular_conflict_deg=float(np.degrees(np.arccos(np.clip(
                    registered[0]@target/np.linalg.norm(target),-1.,1.)))),
                z_tilt_preservation_max_error=float(np.max(np.abs(registered[:,2]-physical_normals[:,i,2]))))
        registrations=np.stack(registrations);normals=np.stack(normals,axis=1)
        values.update(node_names=np.asarray(nodes),
            node_offsets=np.stack([tags[n] for n in nodes],axis=1),node_normals_world=normals,
            pelvis_rotation_world_sensor=sensor[:,segments.index('pelvis')],
            pelvis_acc_sensor=raw['pelvis_acc_sensor'],availability_time_s=raw['availability_time_s'])
        registration=dict(time_s=p['time_s'],node_names=np.asarray(nodes),
            node_normals_world=normals,yaw_registration_world=registrations,
            world_from_wear=WORLD_FROM_WEAR)
        # Default POSE retains unregistered attitudes with a runtime wear yaw.
        # Common-world mode bakes its shared yaw and stores runtime identity.
        # Both paths apply registration exactly once to force and normals.
        registered_pelvis_rotations(values,registration)
        # No root estimates, velocities or support classifications are copied.
        np.savez_compressed(output/'POSE.npz',**values)
        np.savez_compressed(output/'ROTATIONS.npz',time_s=p['time_s'],segment_names=a['segment_names'],
            base_segment_rotations_world=anatomical_rotations,
            physical_segment_rotations_world=physical_rotations,
            sensor_from_segment=mounts,pelvis_mount_sensor_from_segment=mounts[segments.index('pelvis')])
        np.savez_compressed(output/'NORMALS.npz',**registration)
        metadata=dict(chest_vertical_observations_m=metadata['chest_vertical_observations_m'],
            source_sensor_samples=str(source.resolve()),
            source_natural_pose=str(continuous.resolve()),source_calibration_fit=str(fit.resolve()),
            role='OFFLINE_MATCHED_NATURAL_JOINT_INPUTS_NOT_FUSION_OUTPUT',
            requires_physical_anatomical_separation=True,root_estimate_copied=False,
            support_classification_copied=False,offline_calibration_replay=True,
            tag_geometry_scope=metadata['tag_geometry_scope'],
            normals_owner='ONE_INITIAL_PROPER_YAW_REBOUND_FROM_NEW_PHYSICAL_STREAM_TO_ATTESTED_WEAR_PRIOR',
            normals_changed=True,geometry_changed=False,physical_rotation_stream_changed=False,
            pelvis_sensor_rotation_storage='UNREGISTERED_RUNTIME_APPLIES_STORED_YAW_ONCE',
            registration_uses_raw_ranges=False,
            world_from_wear_columns='FORWARD_MINUS_Y_LEFT_PLUS_X_UP_PLUS_Z',
            wear_registration_audit=wear_audit,
            source_physical_rotations=str((continuous/'PRE_IK_ROTATIONS.npz').resolve()),
            source_physical_rotations_sha256=_sha256(continuous/'PRE_IK_ROTATIONS.npz'),
            source_calibration_sha256=_sha256(fit/'REPLAY_CALIBRATION.npz'),
            source_sensor_samples_sha256=_sha256(source),
            availability_semantics='INHERITED_RAW_AVAILABILITY_NOT_OFFLINE_CALIBRATION_READINESS')
        metadata['output_sha256']={name:_sha256(output/name) for name in ('POSE.npz','ROTATIONS.npz','NORMALS.npz')}
        if common_world_yaw:
            metadata.update(role='OFFLINE_COMMON_WORLD_YAW_BOUNDARY_DIAGNOSTIC_NOT_ACCEPTED_FUSION_INPUT',
                normals_owner='COMMON_ANATOMICAL_RIGHT_WORLD_MINUS_X_NO_INDEPENDENT_NORMAL_PRIOR_OVERRIDE',
                geometry_changed=True,physical_rotation_stream_changed=True,
                pelvis_sensor_rotation_storage='COMMON_WORLD_YAW_BAKED_RUNTIME_YAW_IDENTITY',
                common_world_yaw_from_calibrated=common_yaw.tolist(),
                common_world_yaw_reference='FIRST_NATIVE_FRAME_UNREFLECTED_HIP_RIGHT_MINUS_HIP_LEFT',
                computational_embedding='IDENTITY_DISPLAY_REFLECTION_EXCLUDED',
                relative_segment_rotations_preserved=True,
                physical_mounts_changed=False,
                physical_metrology_certified=False)
        (output/'POSE.json').write_text(json.dumps(metadata,indent=2))
    return dict(frames=len(values['time_s']),nodes=len(nodes),root_estimate_copied=False)


if __name__=='__main__':
    ap=argparse.ArgumentParser(description=__doc__)
    for name in ('continuous','fit','source','output'):
        ap.add_argument('--'+name,type=Path,required=True)
    ap.add_argument('--common-world-yaw',action='store_true',help='Opt-in shared proper world-frame diagnostic; exclude display reflection')
    args=ap.parse_args()
    print(json.dumps(build(args.continuous,args.fit,args.source,args.output,common_world_yaw=args.common_world_yaw)))
