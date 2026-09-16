#!/usr/bin/env python3
"""One initial per-node wear-heading registration; continuous raw sensor tilt."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
from scipy.spatial.transform import Rotation

from biospur_fusion.c2_uwb_calibration.antenna_los import NODE_OUTWARD_MINUS_Z_IN_SEGMENT


def sha256(path: Path) -> str:
    digest=hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda:stream.read(1<<20),b''):
            digest.update(block)
    return digest.hexdigest()


def matrix(q):
    return Rotation.from_quat(q[:,[1,2,3,0]]).as_matrix()


def build(frontend: Path, pose_path: Path, output: Path):
    if output.exists() or output.with_suffix('.json').exists():
        raise FileExistsError(output)
    with np.load(pose_path,allow_pickle=False) as p:
        times = p['time_s']
        nodes = p['node_names']
        pelvis_rotation = p['pelvis_rotation_world_sensor']
    with np.load(frontend/'BSFC2CC.npz',allow_pickle=False) as raw:
        index = np.searchsorted(raw['common_global_ns'].astype(float)*1e-9,times,side='right')-1
        if np.any(index<0):
            raise ValueError('pose precedes pelvis orientation source')
        pelvis_raw = matrix(raw['quat_vqf_sensor_wxyz'][index])
    world_from_vqf = pelvis_rotation[0]@pelvis_raw[0].T
    mapping_error = float(np.max(np.abs(world_from_vqf@pelvis_raw-pelvis_rotation)))
    if mapping_error>1e-10:
        raise ValueError('inherited pelvis world rotation is not one fixed mapping')
    # Explicit operator-attested wear frame: forward=-Y, left=+X, up=+Z.
    world_from_wear = np.column_stack(([0.,-1.,0.],[1.,0.,0.],[0.,0.,1.]))
    normals, source_times, registrations, audits = [], [], [], {}
    source_hashes = {}
    for name in nodes:
        node = str(name)
        path = frontend/f'{node}.npz'
        with np.load(path,allow_pickle=False) as raw:
            raw_times = raw['common_global_ns'].astype(float)*1e-9
            index = np.searchsorted(raw_times,times,side='right')-1
            if np.any(index<0):
                raise ValueError(f'{node}: pose precedes orientation source')
            rotations = world_from_vqf@matrix(raw['quat_vqf_sensor_wxyz'][index])
            used_times = raw_times[index]
            initial_raw_quaternion = raw['quat_vqf_sensor_wxyz'][index[0]]
        raw_normal = -rotations[:,:,2]
        target = world_from_wear@NODE_OUTWARD_MINUS_Z_IN_SEGMENT[node]
        if np.linalg.norm(raw_normal[0,:2])<1e-6 or np.linalg.norm(target[:2])<1e-6:
            raise ValueError('initial wear yaw cannot be determined from a vertical normal')
        yaw = np.arctan2(target[1],target[0])-np.arctan2(raw_normal[0,1],raw_normal[0,0])
        registration = Rotation.from_rotvec([0.,0.,yaw]).as_matrix()
        normal = raw_normal@registration.T
        if np.any(used_times>times):
            raise ValueError('normal source association used a future sample')
        normals.append(normal)
        source_times.append(used_times)
        registrations.append(registration)
        audits[node] = {'yaw_registration_rad':float(yaw),
            'initial_raw_quaternion_wxyz':initial_raw_quaternion.tolist(),
            'initial_raw_normal_world':raw_normal[0].tolist(),
            'wear_target_world':target.tolist(), 'initial_registered_normal_world':normal[0].tolist(),
            'z_tilt_preservation_max_error':float(np.max(np.abs(raw_normal[:,2]-normal[:,2]))),
            'maximum_source_age_s':float(np.max(times-used_times))}
        source_hashes[node] = sha256(path)
    output.parent.mkdir(parents=True,exist_ok=True)
    np.savez_compressed(output,time_s=times,node_names=nodes,
        node_normals_world=np.stack(normals,axis=1),
        normal_source_time_s=np.stack(source_times,axis=1),
        yaw_registration_world=np.stack(registrations),
        inherited_world_from_vqf=world_from_vqf,world_from_wear=world_from_wear)
    result = {'role':'WEAR_REGISTERED_NORMAL_PRIOR_NOT_EXACT_LOS',
        'registration':'ONE_INITIAL_YAW_PER_NODE_FROM_ATTESTED_WEAR_FRAME_PRESERVE_RAW_TILT',
        'body_root_pose_changed':False,'range_values_consumed':False,
        'small_limb_occlusion':False,'torso_ray_occlusion':False,
        'pose_source':str(pose_path.resolve()),'pose_sha256':sha256(pose_path),
        'frontend_source':str(frontend.resolve()),'frontend_node_sha256':source_hashes,
        'pelvis_constant_world_mapping_max_error':mapping_error,
        'world_from_wear_columns':'FORWARD_MINUS_Y_LEFT_PLUS_X_UP_PLUS_Z',
        'node_audit':audits,'output_sha256':sha256(output)}
    output.with_suffix('.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result))


if __name__=='__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--frontend',type=Path,required=True)
    parser.add_argument('--pose',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    build(args.frontend,args.pose,args.output)
