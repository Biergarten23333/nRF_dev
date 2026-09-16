#!/usr/bin/env python3
"""Derive corrected geometric embedding/tag candidates without IMU refitting."""
from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import json
from pathlib import Path

import numpy as np
from scipy.spatial.transform import Rotation

from biospur_fusion.c2_3a_kinematics import load_frozen_c2_3a
from biospur_fusion.c2_uwb_calibration.frozen_body_proxy import frozen_world_alignment
from biospur_fusion.c2_uwb_calibration.tag_geometry import (
    bind_frozen_parity, engineering_tag_points, unavailable_sensor_bindings,
)

ROOT = Path(__file__).resolve().parents[1]
SURFACE = ROOT/'config/body_calibration_v4_1/v47_subject_surface_anthropometry_20260828.json'
IDENTITY = ROOT/'datasets/phase2_calibration/phase2_targeted_calibration_20260817t130918z_capture_2_with_joint_label_c8645eb2/identity/SEALED_NODE_TO_BODY_GROUND_TRUTH.json'


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1 << 20), b''):
            h.update(block)
    return h.hexdigest()


def build(source: Path, output: Path, normals_path: Path):
    if output.exists() or output.with_suffix('.json').exists():
        raise ValueError('derived geometry output must be new')
    meta = json.loads(source.with_suffix('.json').read_text())
    kin = load_frozen_c2_3a()
    initial_alignment, _ = frozen_world_alignment(kin)
    a = Rotation.from_euler('z', meta['heading_correction_deg'], degrees=True).as_matrix() @ initial_alignment
    with np.load(source, allow_pickle=False) as archive:
        values = {key: archive[key] for key in archive.files}
    names = list(values['joint_names'])
    old = {name: values['joints_relative'][:,i] for i,name in enumerate(names)}
    embedding = bind_frozen_parity(kin.output_matrix_world_display_from_internal,
        a, old['hip_right'][0]-old['hip_left'][0])
    joints = {name: embedding.vectors(value) for name,value in old.items()}
    torso_up = embedding.vectors(old['shoulder_mid']/kin.geometry.torso_height_m)
    surface = json.loads(SURFACE.read_text())
    row = next(row for row in surface['measurements'] if row['measurement_id'] == 'chest_sensor_to_acromion_line_vertical_distance')
    readings = np.array([r['value_mm'] for r in row['observations']], float)/1000
    tags = engineering_tag_points(joints, torso_up, readings)
    values['joints_relative'] = np.stack([joints[name] for name in names],axis=1)
    values['node_offsets'] = np.stack([tags[name] for name in values['node_names']],axis=1)
    values['geometry_embedding_from_previous'] = embedding.matrix
    with np.load(normals_path, allow_pickle=False) as normals:
        if (not np.array_equal(normals['time_s'],values['time_s']) or
                not np.array_equal(normals['node_names'],values['node_names'])):
            raise ValueError('verified normals must have the exact pose timeline and nodes')
        vectors = normals['node_normals_world']
        if vectors.shape != values['node_offsets'].shape or not np.isfinite(vectors).all() or not np.allclose(np.linalg.norm(vectors,axis=-1),1.,atol=1e-10):
            raise ValueError('verified normals must be finite unit vectors')
        values['node_normals_world'] = vectors.copy()
    output.parent.mkdir(parents=True, exist_ok=True)
    np.savez(output, **values)
    report = dict(meta)
    report.update(role='GEOMETRIC_EMBEDDING_AND_ENGINEERING_TAG_CANDIDATE',
        source_pose=str(source.resolve()), source_pose_sha256=digest(source),
        surface_source=str(SURFACE), surface_sha256=digest(SURFACE),
        identity_source=str(IDENTITY), identity_sha256=digest(IDENTITY),
        geometry_embedding_from_previous=embedding.matrix.tolist(),
        reflection_internal=embedding.reflection_internal.tolist(),
        heading_after_reflection=embedding.heading_after_reflection.tolist(),
        initial_forward_convention='ANATOMICAL_RIGHT_MINUS_X_FORWARD_UP_CROSS_RIGHT_MINUS_Y',
        sensor_rotation_changed=False, anchors_changed=False, clocks_changed=False,
        imu_world_heading_status='INHERITED_NOT_INDEPENDENTLY_MEASURED',
        normals_owner='VERIFIED_WEAR_REGISTERED_NORMALS_UNCHANGED',
        normals_source=str(normals_path.resolve()), normals_sha256=digest(normals_path),
        tag_geometry_scope='ENGINEERING_PROXY_NOT_MEASURED_ANTENNA_CENTRES',
        chest_vertical_observations_m=readings.tolist(),
        chest_rule='SHOULDER_PROXY_MINUS_MEAN_OBSERVED_VERTICAL_DISTANCE_TIMES_EMBEDDED_TORSO_UP',
        geometry_covariance_status='UNAVAILABLE_NOT_PROPAGATED_NO_Q_R_CHANGE',
        remaining_unknowns=['chest anterior displacement and shoulder reference mapping',
            'all sensor-to-landmark mounting offsets', 'antenna phase centres',
            'display skeleton joint-centre geometry', 'IMU world heading calibration'],
        sensor_bindings={k:asdict(v) for k,v in unavailable_sensor_bindings(json.loads(IDENTITY.read_text())).items()},
        output_sha256=digest(output))
    output.with_suffix('.json').write_text(json.dumps(report,indent=2)+'\n')
    return {'output':str(output),'frames':len(values['time_s']),'geometry_scope':report['tag_geometry_scope']}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pose',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--antenna-normals',type=Path,required=True)
    args=parser.parse_args()
    print(json.dumps(build(args.pose,args.output,args.antenna_normals)))
