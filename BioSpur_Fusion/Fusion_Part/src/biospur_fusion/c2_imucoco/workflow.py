"""Filesystem boundary for candidate calibration and diagnostic model replay."""
from __future__ import annotations

import json
from pathlib import Path
import time

import numpy as np
import torch

from biospur_fusion.c2_sparse_nodes.inputs import NODES, ROOT, sha
from .calibration import fit_calibration
from .preprocessing import prepare_stream, WORLD_TO_SMPL
from .upstream import DEFAULT_UPSTREAM, verify_assets

INPUT_RUN = ROOT / 'logs/c2_five_node_inertial_rework_20260906_133807'
SURFACE = ROOT / 'config/body_calibration_v4_1/v47_subject_surface_anthropometry_20260828.json'
CALIBRATION_FILE = 'CALIBRATION_CANDIDATE.json'


def write(path, value):
    path.write_text(json.dumps(value, indent=2, allow_nan=False)+'\n')


def source_fingerprint():
    paths = [ROOT/'tools/run_c2_imucoco.py']
    for package in ('c2_imucoco', 'c2_sparse_nodes'):
        paths.extend(sorted((ROOT/'src/biospur_fusion'/package).glob('*.py')))
    return {str(p.relative_to(ROOT)): sha(p) for p in paths}


def verify_probe(out, smpl, calibration):
    """A saved file alone is not a passing gate; bind the result to its inputs."""
    record = json.loads((out/'POSE_PROBE.json').read_text())
    expected = dict(smpl_sha256=sha(Path(smpl)),
                    calibration_sha256=sha(calibration), source_sha256=source_fingerprint())
    if any(record.get(k) != v for k, v in expected.items()):
        raise ValueError('probe code, calibration or model changed; use a new run')
    if record.get('status') != 'RUNNABLE_DIAGNOSTIC_NOT_MOTION_ACCURACY_ACCEPTED' or record.get('frames') != 300:
        raise ValueError('short pose probe did not pass')
    if record.get('output_sha256') != sha(out/'POSE_PROBE.npz'):
        raise ValueError('probe output changed')


def load_input(path):
    """Reject mixed-node archives before decoding any sensor array.

    Selection from the original transport belongs to FiveNodeFrontend. At
    this boundary the archive must already contain only the five IMU streams;
    silently selecting five keys would conceal an incorrectly built export.
    """
    with np.load(path, allow_pickle=False) as data:
        if not data.files or len(data.files) != len(set(data.files)):
            raise ValueError('empty or duplicate five-node archive keys')
        for key in data.files:
            parts = key.split('/')
            if len(parts) != 3 or parts[1] not in NODES or parts[2] != 'imu':
                raise ValueError('five-node archive contains forbidden payload: ' + key)
        names = list(dict.fromkeys(k.split('/')[0] for k in data.files))
        expected = {f'{e}/{n}/imu' for e in names for n in NODES}
        if set(data.files) != expected:
            raise ValueError('every episode requires exactly the five retained nodes')
        result = {}
        for episode in names:
            result[episode] = {}
            for node in NODES:
                rows = data[f'{episode}/{node}/imu']
                if (rows.ndim != 2 or rows.shape[1] != 11 or len(rows) < 4
                        or not np.isfinite(rows).all() or np.any(np.diff(rows[:, 0]) <= 0)):
                    raise ValueError('invalid five-node IMU array: ' + episode + '/' + node)
                result[episode][node] = {'imu': rows}
        return result


def prepare(out):
    if (out/CALIBRATION_FILE).exists() or (out/'CALIBRATION_FROZEN.json').exists():
        raise ValueError('calibration already exists; do not overwrite a fitted model')
    assets = verify_assets()
    data = load_input(INPUT_RUN/'CALIBRATION_CONTINUOUS_INPUT.npz')
    c, audit = fit_calibration(data, json.loads(SURFACE.read_text()))
    c.update(calibration_input_sha256=sha(INPUT_RUN/'CALIBRATION_CONTINUOUS_INPUT.npz'),
             upstream_revision=assets['revision'], surface_sha256=sha(SURFACE))
    write(out/CALIBRATION_FILE, c)
    write(out/'CALIBRATION_PROTOCOL.json', audit)
    write(out/'INPUT_PROVENANCE.json', dict(calibration_cache=str(INPUT_RUN/'CALIBRATION_CONTINUOUS_INPUT.npz'),
        holdout_cache=str(INPUT_RUN/'HOLDOUT_CONTINUOUS_INPUT.npz'),
        consumed_nodes=list(NODES), source_audit=str(INPUT_RUN/'CALIBRATION_INPUT_AUDIT.json'),
        calibration_input_sha256=c['calibration_input_sha256'], uwb_ranges_or_positions_used=False))


def encoder_probe(out):
    from .chunked import ChunkedFeatures
    from .backend import VERTICES
    if (out/'ENCODER_REAL_PROBE.json').exists():
        raise ValueError('encoder probe already exists; use a new run')
    started = time.monotonic()
    c = json.loads((out/CALIBRATION_FILE).read_text())
    data = load_input(INPUT_RUN/'CALIBRATION_CONTINUOUS_INPUT.npz')
    prepared = prepare_stream(data['02_t_pose'], c)
    encoder = ChunkedFeatures()
    mapping = encoder.set_placements(encoder.model.mesh_positions[VERTICES])
    x = torch.from_numpy(prepared['features'][:300])[None]
    with torch.inference_mode():
        feat = encoder.forward(x).cpu().numpy()
    if not np.isfinite(feat).all() or feat.std() == 0:
        raise RuntimeError('pretrained encoder did not produce finite nonconstant features')
    np.savez_compressed(out/'ENCODER_REAL_PROBE.npz', time_s=prepared['time_s'][:300],
        features=prepared['features'][:300], encoded=feat[0], input_valid=prepared['input_valid'][:300])
    write(out/'ENCODER_REAL_PROBE.json', dict(status='ENCODER_ONLY_PASSED_NOT_FULL_POSE',
        frames=300, sensor_count=5, output_shape=list(feat.shape),
        joint_to_device_mapping=mapping.tolist(), wall_s=time.monotonic()-started,
        full_pose_reconstructed=False))


def replay(out, smpl, *, probe=False, diagnostic_only=False):
    from .protocol import require_replay_scope
    if not probe:
        require_replay_scope(diagnostic_only=diagnostic_only)
    from .backend import load_pose, ChunkedPoseStream
    from .body import fit_subject_shape, display_joints, bend_angles
    label = 'POSE_PROBE' if probe else 'C2_H_REPLAY'
    if (out/(label+'.json')).exists() or (out/(label+'.npz')).exists():
        raise ValueError('stage output already exists; use a new run')
    verify_assets()
    c = json.loads((out/CALIBRATION_FILE).read_text())
    if sha(INPUT_RUN/'CALIBRATION_CONTINUOUS_INPUT.npz') != c['calibration_input_sha256']:
        raise ValueError('calibration input changed')
    if not probe:
        verify_probe(out, smpl, out/CALIBRATION_FILE)
    cal = load_input(INPUT_RUN/'CALIBRATION_CONTINUOUS_INPUT.npz')
    continuous = cal['_continuous']
    if probe:
        end = max(continuous[n]['imu'][0, 0] for n in NODES) + 5.1
        continuous = {n: {'imu': v['imu'][v['imu'][:, 0] <= end]} for n, v in continuous.items()}
    if not probe:
        hold = load_input(INPUT_RUN/'HOLDOUT_CONTINUOUS_INPUT.npz')
        continuous = {n: {'imu': np.concatenate((continuous[n]['imu'], hold['_continuous'][n]['imu']))} for n in NODES}
    prepared = prepare_stream(continuous, c)
    if probe:
        prepared = {k: v[:300] for k, v in prepared.items()}
    poser, body = load_pose(smpl, out)
    subject, geometry = fit_subject_shape(body, json.loads(SURFACE.read_text()))
    # The released pose head uses the standard skeleton internally. Report
    # both standard and measured-size FK; do not claim shape-conditioned pose.
    stream = ChunkedPoseStream(poser)
    output, audit = stream.run(prepared['features'], wall_limit_s=120. if probe else 1200.,
        progress=lambda p: print(json.dumps(p), flush=True))
    global_pose = torch.from_numpy(output['global_rotation'])
    standard = display_joints(global_pose, body) @ WORLD_TO_SMPL
    measured = display_joints(global_pose, subject) @ WORLD_TO_SMPL
    output.update(time_s=prepared['time_s'], input_valid=prepared['input_valid'],
        retained_input_orientation=prepared['orientation'],
        standard_joints_m=standard, measured_joints_m=measured,
        bend_deg=bend_angles(measured))
    np.savez_compressed(out/(label+'.npz'), **output)
    write(out/(label+'.json'), dict(status='RUNNABLE_DIAGNOSTIC_NOT_MOTION_ACCURACY_ACCEPTED',
        **audit, smpl_sha256=sha(Path(smpl)), calibration_sha256=sha(out/CALIBRATION_FILE),
        source_sha256=source_fingerprint(), output_sha256=sha(out/(label+'.npz')),
        inference_input_sha256={name: sha(INPUT_RUN/name) for name in
            (['CALIBRATION_CONTINUOUS_INPUT.npz'] if probe else
             ['CALIBRATION_CONTINUOUS_INPUT.npz', 'HOLDOUT_CONTINUOUS_INPUT.npz'])},
        valid_input_frames=int(prepared['input_valid'].sum()),
        geometry_use='measured-size FK diagnostic; neural orientation uses upstream mean-shape model',
        heldout_used_for_fit=False))
    write(out/'SUBJECT_GEOMETRY.json', geometry)
