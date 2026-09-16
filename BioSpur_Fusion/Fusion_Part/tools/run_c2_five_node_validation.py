#!/usr/bin/env python3
"""Bounded independent five-node calibration and frozen 19+2 diagnostic replay."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import resource
import time

os.environ.setdefault('OPENBLAS_NUM_THREADS', '1')
os.environ.setdefault('OMP_NUM_THREADS', '1')
import numpy as np

from biospur_fusion.c2_sparse_nodes.inputs import (
    ROOT, NODES, FiveNodeFrontend, episode_contracts, save_input, sha,
)
from biospur_fusion.c2_sparse_nodes.calibration import calibrate
from biospur_fusion.c2_sparse_nodes.replay import replay
from biospur_fusion.c2_sparse_nodes.model import IK_CONFIG


def write(path, obj):
    path.write_text(json.dumps(obj, indent=2, allow_nan=False) + '\n')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--output', type=Path, required=True)
    args = ap.parse_args()
    out = args.output.resolve()
    if not out.is_relative_to(ROOT / 'logs') or (out / 'CALIBRATION_FROZEN.json').exists():
        raise ValueError('new/unfinished logs output required; cannot overwrite a freeze')
    out.mkdir(exist_ok=True)
    started = time.monotonic()
    owner_files = sorted((ROOT / 'src/biospur_fusion/c2_sparse_nodes').glob('*.py')) + [Path(__file__).resolve()]
    code = {str(p.relative_to(ROOT)): sha(p) for p in owner_files}
    surface_path = ROOT / 'config/body_calibration_v4_1/v47_subject_surface_anthropometry_20260828.json'
    contracts = episode_contracts()
    frontend = FiveNodeFrontend()
    print('CALIBRATION_INPUT_START five nodes, continuous VQF, C2 only', flush=True)
    episodes, audit = frontend.read(contracts)
    save_input(out / 'CALIBRATION_FIVE_INPUT.npz', episodes)
    write(out / 'CALIBRATION_INPUT_AUDIT.json', audit)
    calibration = calibrate(episodes, json.loads(surface_path.read_text()))
    calibration.update(source_sha256=code, surface_sha256=sha(surface_path),
        input_sha256=sha(out / 'CALIBRATION_FIVE_INPUT.npz'), hz=20,
        code_frozen_before_holdout=True, reconstruction='pure IMU hinge IK with explicit latent priors; tape lengths in FK only',
        priors=IK_CONFIG, sensitivity_prior_scales=[.5, 1., 2.],
        validation_scope='same subject/session diagnostic; no independent ground truth')
    print('CALIBRATION_RESULT', json.dumps({k: calibration[k] for k in ('lengths','hinge_audit','functional_yaw_rad')}), flush=True)
    short, short_report = replay(episodes['16_squat'], calibration, limit=40)
    write(out / 'SHORT_REPLAY_GATE.json', short_report)
    if short_report['wall_s'] > 120 or short_report['solved_frames'] < 1:
        raise RuntimeError('short replay runtime/input gate failed')
    write(out / 'CALIBRATION_FROZEN.json', calibration)
    seal = sha(out / 'CALIBRATION_FROZEN.json')
    write(out / 'FREEZE_SEAL.json', dict(calibration_sha256=seal, holdout_opened=False,
        short_stage='GO_FOR_DIAGNOSTIC_FAILURE_ANALYSIS; NOT ACCURACY_ACCEPTANCE', source_sha256=code))
    results = {}
    for name, ep in episodes.items():
        arr, report = replay(ep, calibration)
        np.savez_compressed(out / (name + '_REPLAY.npz'), **arr)
        results[name] = report
        write(out / 'REPLAY_METRICS.json', results)
        print(name, 'frames', report['frames'], 'bend_p95_deg', np.round(report['bend_p95_deg'], 1).tolist(), flush=True)
    print('HOLDOUT_INPUT_START frozen calibration SHA256=' + seal, flush=True)
    if sha(out / 'CALIBRATION_FROZEN.json') != seal:
        raise RuntimeError('calibration mutated before holdout')
    holdouts, haudit = frontend.read(episode_contracts(holdout=True), start=frontend.cursor)
    save_input(out / 'HOLDOUT_FIVE_INPUT.npz', holdouts)
    write(out / 'HOLDOUT_INPUT_AUDIT.json', haudit)
    for name, ep in holdouts.items():
        arr, report = replay(ep, calibration)
        np.savez_compressed(out / (name + '_REPLAY.npz'), **arr)
        results[name] = report
        write(out / 'REPLAY_METRICS.json', results)
        print(name, 'frames', report['frames'], 'bend_p95_deg', np.round(report['bend_p95_deg'], 1).tolist(), flush=True)
    for name, ep in holdouts.items():
        sensitivity = {}
        for scale in (.5, 2.):
            arr, report = replay(ep, calibration, prior_scale=scale)
            np.savez_compressed(out / (name + f'_PRIOR_{scale:g}.npz'), **arr)
            sensitivity[str(scale)] = report
        write(out / (name + '_PRIOR_SENSITIVITY.json'), sensitivity)
    if any(sha(ROOT / p) != h for p, h in code.items()) or sha(out / 'CALIBRATION_FROZEN.json') != seal:
        raise RuntimeError('frozen source/calibration changed during replay')
    write(out / 'RUN_COMPLETE.json', dict(status='DIAGNOSTIC_COMPLETE_NOT_VALIDATED_PRODUCT',
        episodes=len(results), nodes=list(NODES), calibration_sha256=seal,
        wall_s=time.monotonic()-started, max_rss_mb=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024,
        raw_data_modified=False, source_sha256=code))


if __name__ == '__main__':
    main()
