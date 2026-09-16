#!/usr/bin/env python3
"""Downstream comparison only, after the personalized H output is sealed."""
import argparse
import json
from pathlib import Path

import numpy as np
import torch

from biospur_fusion.c2_five_calibration.geometry import DISPLAY, joints_from_global
from biospur_fusion.c2_imucoco.body import bend_angles
from biospur_fusion.c2_imucoco.workflow import INPUT_RUN
from biospur_fusion.c2_sparse_nodes.articulated_reference import H_REFERENCE, baseline_on_grid, verify_reference
from biospur_fusion.c2_sparse_nodes.evaluation import PAIRS
from biospur_fusion.c2_sparse_nodes.inputs import sha
from build_imucoco_review import angle_between


def main(out):
    if (out/'H_ASSESSMENT.json').exists():
        raise ValueError('preserve prior assessment')
    result = json.loads((out/'H_CANDIDATE.json').read_text())
    if result['output_sha256'] != sha(out/'H_CANDIDATE.npz'):
        raise ValueError('candidate changed after inference')
    start = json.loads((out/'H_START.json').read_text())
    if start['personalization_sha256'] != sha(out/'PERSONALIZATION.json'):
        raise ValueError('personalization changed after freeze')
    source = Path(json.loads((out/'PERSONALIZATION.json').read_text())['source_run'])
    for p, expected in start['verified_reuse_bindings'].items():
        if sha(Path(p)) != expected:
            raise ValueError('frozen source changed: '+p)
    geometry = json.loads((source/'GEOMETRY.json').read_text())
    contracts = json.loads((INPUT_RUN/'HOLDOUT_INPUT_AUDIT.json').read_text())['contracts']
    provenance = verify_reference()
    metrics = {}
    with np.load(out/'H_CANDIDATE.npz') as q, np.load(source/'H_REPLAY.npz') as old, np.load(H_REFERENCE) as reference:
        for key in ('time_s','observed','acceleration','valid','prior'):
            np.testing.assert_array_equal(q[key], old[key])
        for name, window in contracts.items():
            ids = np.flatnonzero((q['time_s'] >= window['lo']) & (q['time_s'] <= window['hi']))
            ref, valid = baseline_on_grid(reference, name, q['time_s'][ids], window, True)
            valid &= q['valid'][ids]
            target = np.column_stack([angle_between(ref[p][:,:,2], ref[c][:,:,2]) for p,c in PAIRS])
            variants = {}
            for label, rotation in (('previous',old['rotation'][ids]), ('candidate',q['rotation'][ids])):
                points = joints_from_global(torch.tensor(rotation), geometry).numpy()[:,DISPLAY]
                error = np.abs(bend_angles(points)-target)[valid]
                variants[label] = dict(mae_deg=error.mean(0).tolist(),
                    p95_deg=np.quantile(error,.95,axis=0).tolist(),
                    pass_each_joint=((error.mean(0)<=15)&(np.quantile(error,.95,axis=0)<=35)).tolist())
            metrics[name] = dict(frames=int(valid.sum()), **variants)
    report = dict(status='DOWNSTREAM_ENGINEERING_COMPARISON', product_accepted=False,
        candidate_sha256=result['output_sha256'], reference=provenance,
        joint_order=['left_elbow','right_elbow','left_knee','right_knee'],
        gates=dict(mae_deg=15,p95_deg=35), metrics=metrics,
        all_candidate_angle_gates_pass=all(all(v['candidate']['pass_each_joint']) for v in metrics.values()),
        not_external_ground_truth=True, H_tuning_performed=False,
        same_time_and_observations_verified=True, assessment_source_sha256=sha(Path(__file__)))
    (out/'H_ASSESSMENT.json').write_text(json.dumps(report,indent=2))
    print(json.dumps(report,indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',type=Path,required=True)
    main(parser.parse_args().out)
