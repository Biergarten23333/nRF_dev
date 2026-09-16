#!/usr/bin/env python3
"""Score existing five-C2 checkpoints without optimizer steps or parameter fits.

Run only after calibration and frozen C2 validation, with independent review.
Use an external process-group ceiling for C-extension calls, for example:
timeout --signal=TERM --kill-after=5s 115s python tools/audit_c2_five_checkpoint_retention.py --out CANDIDATE
"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import shlex
import signal
import sys
import time
import zipfile

sys.dont_write_bytecode = True
import numpy as np
import torch

from biospur_fusion.c2_coupled_progressive.contracts import EPISODES
from biospur_fusion.c2_five_calibration.geometry import OBSERVED
from biospur_fusion.c2_five_calibration.solver import solve_pose


ROOT = Path(__file__).resolve().parents[1]
SOURCE_FILES = (
    'src/biospur_fusion/c2_coupled_progressive/contracts.py',
    'src/biospur_fusion/c2_five_calibration/anatomy.py',
    'src/biospur_fusion/c2_five_calibration/geometry.py',
    'src/biospur_fusion/c2_five_calibration/solver.py',
    'src/biospur_fusion/c2_five_calibration/tracking.py',
    'src/biospur_fusion/c2_five_calibration/operators.py',
    'src/biospur_fusion/c2_articulated_biomechanics/__init__.py',
    'src/biospur_fusion/c2_articulated_biomechanics/model.py',
    'src/biospur_fusion/c2_articulated_biomechanics/orientation_ik.py',
)
INPUT_FILES = ('PHYSICAL_CALIBRATION.json', 'PHYSICAL_CALIBRATION.npz',
               'C2_VALIDATION.json', 'C2_VALIDATION.npz', 'C2_PRIOR.json',
               'C2_PRIOR.npz', 'GEOMETRY.json')
ROTATION_TOLERANCE = 1e-6
WALL_LIMIT_S = 120.


def sha(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1 << 20), b''):
            digest.update(block)
    return digest.hexdigest()


def archive(path, keys):
    """Reject undeclared payloads before decoding any array."""
    with zipfile.ZipFile(path) as zipped:
        names = zipped.namelist()
        if set(names) != {key + '.npy' for key in keys} or len(names) != len(keys):
            raise ValueError(f'five-C2 archive whitelist failed: {path.name}')
        if sum(item.file_size for item in zipped.infolist()) > 512 * 1024**2:
            raise ValueError(f'archive exceeds bounded size: {path.name}')
    return np.load(path, allow_pickle=False)


def rotation_errors(rotations):
    if rotations.shape[-2:] != (3, 3) or not np.isfinite(rotations).all():
        raise ValueError('rotation checkpoint must be finite 3x3 matrices')
    orthogonal = float(np.max(np.abs(rotations @ rotations.swapaxes(-1, -2) - np.eye(3))))
    determinant = float(np.max(np.abs(np.linalg.det(rotations) - 1.)))
    if max(orthogonal, determinant) > ROTATION_TOLERANCE:
        raise ValueError('checkpoint is not on SO(3) within declared precision')
    return dict(orthogonality_max_error=orthogonal, determinant_max_error=determinant)


def score(out, report, started):
    report['input_sha256'] = {name: sha(out / name) for name in INPUT_FILES}
    report['tool_sha256'] = sha(Path(__file__))
    calibration, validation, prior_meta, geometry = [json.loads((out / name).read_text()) for name in
        ('PHYSICAL_CALIBRATION.json', 'C2_VALIDATION.json', 'C2_PRIOR.json', 'GEOMETRY.json')]
    frozen_sources = calibration['source_sha256']
    if not isinstance(frozen_sources, dict) or frozen_sources != validation['source_sha256']:
        raise ValueError('calibration and validation must bind identical frozen source maps')
    if not set(SOURCE_FILES).issubset(frozen_sources):
        raise ValueError('frozen source map omits required scoring dependencies')
    for name in frozen_sources:
        path = Path(name)
        if (path.is_absolute() or path.suffix != '.py' or
                not name.startswith(('src/', 'tools/')) or
                not (ROOT / path).resolve().is_relative_to(ROOT)):
            raise ValueError('frozen source map must contain repository Python source paths only')
    report['source_sha256'] = {name: sha(ROOT / name) for name in frozen_sources}
    if report['source_sha256'] != frozen_sources:
        raise ValueError('current source differs from the frozen checkpoint source map')
    actions = tuple(EPISODES)
    if len(actions) != 19 or {name[:2] for name in actions} != {f'{i:02}' for i in range(20) if i != 1}:
        raise ValueError('expected the nineteen recorded C2 actions')
    for label, meta in (('PHYSICAL_CALIBRATION', calibration), ('C2_VALIDATION', validation)):
        if set(meta['actions']) != set(actions):
            raise ValueError(f'{label} must contain exactly all nineteen C2 actions')
        if meta['output_sha256'] != report['input_sha256'][label + '.npz']:
            raise ValueError(f'{label} payload binding failed')
        if meta['geometry_sha256'] != report['input_sha256']['GEOMETRY.json']:
            raise ValueError(f'{label} geometry binding failed')
        for name, digest in report['source_sha256'].items():
            if meta['source_sha256'].get(name) != digest:
                raise ValueError(f'{label} scoring source differs: {name}')
    if validation['calibration_sha256'] != report['input_sha256']['PHYSICAL_CALIBRATION.json']:
        raise ValueError('validation does not bind this frozen calibration')
    if prior_meta['output_sha256'] != report['input_sha256']['C2_PRIOR.npz']:
        raise ValueError('prior payload binding failed')
    levers = np.asarray(calibration['fitted_sensor_levers_m'])
    if levers.shape != (5, 3) or not np.isfinite(levers).all():
        raise ValueError('exactly five finite frozen lever vectors required')
    prior_keys = {'time_s', 'prior', 'observed', 'acceleration', 'valid'}
    with archive(out / 'C2_PRIOR.npz', prior_keys) as source:
        data = {key: source[key] for key in prior_keys}
    times = data['time_s']
    if times.ndim != 1 or len(times) < 2 or not np.isfinite(times).all() or np.any(np.diff(times) <= 0):
        raise ValueError('prior timestamps must be finite and strictly increasing')
    shapes = {'prior': (len(times), 24, 3, 3), 'observed': (len(times), 5, 3, 3),
              'acceleration': (len(times), 5, 3), 'valid': (len(times),)}
    for key, shape in shapes.items():
        if data[key].shape != shape or not np.isfinite(data[key]).all():
            raise ValueError(f'invalid five-C2 prior array: {key}')
    payload_keys = {f'{action}/{field}' for action in actions for field in ('rotation', 'time_s', 'valid')}
    with archive(out / 'PHYSICAL_CALIBRATION.npz', payload_keys) as fitted, \
            archive(out / 'C2_VALIDATION.npz', payload_keys) as validated:
        for action in actions:
            if time.monotonic() - started >= WALL_LIMIT_S:
                raise TimeoutError('checkpoint scoring wall limit')
            t = fitted[action + '/time_s']
            np.testing.assert_array_equal(t, validated[action + '/time_s'])
            indices = np.searchsorted(times, t)
            if not len(t) or np.any(indices >= len(times)) or not np.array_equal(times[indices], t):
                raise ValueError(f'{action} timestamps are not exact prior rows')
            q = {key: value[indices] for key, value in data.items()}
            for saved in (fitted, validated):
                np.testing.assert_array_equal(saved[action + '/valid'], q['valid'])
            row = report['actions'][action] = {}
            for name, saved in (('fit_checkpoint', fitted), ('validated_checkpoint', validated)):
                rotation = saved[action + '/rotation']
                if rotation.shape != q['prior'].shape:
                    raise ValueError(f'{action} checkpoint shape mismatch')
                evidence = row[name] = rotation_errors(rotation)
                evidence['observed_max_element_error'] = float(np.max(np.abs(rotation[:, OBSERVED] - q['observed'])))
                remaining = WALL_LIMIT_S - (time.monotonic() - started)
                if remaining <= 0:
                    raise TimeoutError('checkpoint scoring wall limit')
                reconstructed, audit = solve_pose(q['prior'], q['observed'], q['acceleration'],
                    q['valid'], t, geometry, levers, iterations=0,
                    wall_limit_s=remaining, initial_rotation=rotation)
                evidence['reconstruction_max_element_error'] = float(np.max(np.abs(reconstructed - rotation)))
                evidence['loss'] = float(audit['history'][-1]['loss'])
                evidence['valid_derivative_frames'] = audit['valid_derivative_frames']
                if (not math.isfinite(evidence['loss']) or
                        max(evidence['observed_max_element_error'], evidence['reconstruction_max_element_error']) > ROTATION_TOLERANCE):
                    raise ValueError(f'{action} saved {name} was not faithfully reconstructed')
            recorded = float(validation['actions'][action]['history'][-1]['loss'])
            if not math.isfinite(recorded):
                raise ValueError(f'{action} recorded validation objective is nonfinite')
            current = row['validated_checkpoint']['loss']
            row['recorded_validation_loss'] = recorded
            row['validation_loss_reproduction_error'] = abs(current - recorded)
            row['validation_loss_reproduction_tolerance'] = 1e-8 + 1e-8 * abs(recorded)
            if abs(current - recorded) > row['validation_loss_reproduction_tolerance']:
                raise ValueError(f'{action} recorded validation objective was not reproduced')
            fit_loss = row['fit_checkpoint']['loss']
            row['validation_minus_fit_loss'] = current - fit_loss
            row['better_checkpoint_tolerance'] = 1e-10 + 1e-10 * max(abs(current), abs(fit_loss))
            row['fit_checkpoint_strictly_better'] = current - fit_loss > row['better_checkpoint_tolerance']
    if any(sha(out / name) != digest for name, digest in report['input_sha256'].items()):
        raise ValueError('input changed during scoring')
    if any(sha(ROOT / name) != digest for name, digest in report['source_sha256'].items()) or sha(Path(__file__)) != report['tool_sha256']:
        raise ValueError('scoring source changed during audit')
    report['better_fit_checkpoint_actions'] = [name for name, row in report['actions'].items() if row['fit_checkpoint_strictly_better']]
    report['status'] = 'CHECKPOINT_BOOKKEEPING_COMPLETE_NOT_ACCURACY_OR_PRODUCT_APPROVAL'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', required=True, type=Path)
    out = parser.parse_args().out.resolve()
    if not out.is_dir():
        parser.error('--out must name an existing candidate directory')
    target = out / 'CHECKPOINT_RETENTION.json'
    if target.exists():
        parser.error('preserve existing CHECKPOINT_RETENTION.json')
    torch.set_num_threads(2)
    started = time.monotonic()
    report = dict(status='STARTED', actions={}, command=shlex.join([sys.executable, *sys.argv]),
        optimizer_steps=0, parameters_refitted=False, H_data_read=False, ten_node_data_read=False,
        qualification='Same-objective checkpoint bookkeeping, not accuracy evidence or permission to adopt a pose.',
        rotation_tolerance=ROTATION_TOLERANCE, wall_limit_s=WALL_LIMIT_S)
    def stop(*_):
        raise TimeoutError('checkpoint scoring deadline')
    signal.signal(signal.SIGALRM, stop)
    signal.signal(signal.SIGTERM, stop)
    signal.setitimer(signal.ITIMER_REAL, WALL_LIMIT_S)
    exit_code = 0
    try:
        score(out, report, started)
    except Exception as error:
        report.update(status='STOP', error_type=type(error).__name__, error=str(error))
        exit_code = 2
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        report['wall_s'] = time.monotonic() - started
        with target.open('x') as stream:
            json.dump(report, stream, indent=2, allow_nan=False)
            stream.write('\n')
    print(json.dumps(dict(status=report['status'], report=str(target))))
    return exit_code


if __name__ == '__main__':
    raise SystemExit(main())
