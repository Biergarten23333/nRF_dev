#!/usr/bin/env python3
"""Five-only frozen-block diagnostics; no reference pose or gate changes."""
import argparse
import json
import time
from pathlib import Path

import numpy as np
import torch

from biospur_fusion.c2_imucoco.workflow import write
from biospur_fusion.c2_imucoco.body import bend_angles
from biospur_fusion.c2_five_calibration.workflow import action_data
from biospur_fusion.c2_five_calibration.geometry import DISPLAY, joints_from_global
from biospur_fusion.c2_five_calibration.solver import lever_system, solve_pose
from biospur_fusion.c2_sparse_nodes.inputs import sha


def main(out):
    target = out / 'OFFSET_BLOCK_DIAGNOSTIC.json'
    if target.exists():
        raise ValueError('preserve prior diagnostic')
    contract = json.loads((out / 'BLOCK_DIAGNOSTIC_CONTRACT.json').read_text())
    names = contract['short_actions']
    sources = ['GEOMETRY.json', 'PHYSICAL_CALIBRATION.json', 'C2_VALIDATION.json',
               'C2_VALIDATION.npz', 'C2_PRIOR.json', 'C2_PRIOR.npz']
    bindings = {name: sha(out / name) for name in sources}
    g = json.loads((out / 'GEOMETRY.json').read_text())
    fit = json.loads((out / 'PHYSICAL_CALIBRATION.json').read_text())
    nominal = np.asarray(g['nominal_sensor_levers_m'])
    fitted = np.asarray(fit['fitted_sensor_levers_m'])
    shank_only = nominal.copy()
    shank_only[3:] = fitted[3:]
    variants = dict(nominal=nominal, fitted=fitted, shank_only=shank_only)
    actions = action_data(out)
    contributions = {}
    with np.load(out / 'C2_VALIDATION.npz') as archive:
        for name, q in actions.items():
            a, b = lever_system(archive[name + '/rotation'], q['acceleration'], q['valid'], g)
            residual = a @ fitted.ravel() - b
            gradient = (a.T @ residual).reshape(5, 3)
            contributions[name] = dict(
                residual_rms_by_limb_mps2=np.sqrt(np.mean(residual.reshape(-1, 4, 3)**2, axis=(0, 2))).tolist(),
                offset_loss_gradient=gradient.tolist(),
                derivative_frames=len(residual) // 12)
    started = time.monotonic()
    reports, outputs = {}, {}
    for name in names:
        whole = actions[name]
        take = whole['time_s'] < whole['time_s'][0] + contract['window_s']
        q = {k: v[take] for k, v in whole.items()}
        case, bends = {}, {}
        for label, levers in variants.items():
            if time.monotonic() - started > 150:
                raise TimeoutError('short block experiment budget')
            rotation, audit = solve_pose(q['prior'], q['observed'], q['acceleration'],
                q['valid'], q['time_s'], g, levers, iterations=150, wall_limit_s=25)
            bend = bend_angles(joints_from_global(torch.tensor(rotation), g).numpy()[:, DISPLAY])
            bends[label] = bend
            outputs[name + '/' + label] = rotation
            case[label] = dict(solve=audit, median_bend_deg=np.median(bend, axis=0).tolist())
        for label in ('fitted', 'shank_only'):
            difference = abs(bends[label] - bends['nominal'])
            case[label]['change_from_nominal_mae_deg'] = difference.mean(axis=0).tolist()
            case[label]['change_from_nominal_max_deg'] = difference.max(axis=0).tolist()
        reports[name] = case
        print(json.dumps(dict(action=name, sensitivity={key: case[key]['change_from_nominal_mae_deg']
                         for key in ('fitted', 'shank_only')})), flush=True)
    if any(sha(out / name) != expected for name, expected in bindings.items()):
        raise ValueError('frozen source changed during block probe')
    np.savez_compressed(out / 'OFFSET_BLOCK_DIAGNOSTIC.npz', **outputs)
    write(target, dict(actions=reports, fixed_pose_action_contributions=contributions,
        bound_inputs=bindings, source_sha256=sha(Path(__file__)), wall_s=time.monotonic()-started,
        ten_node_reference_used=False, H_used=False, parameters_refitted=False,
        qualification='Counterfactual sensitivity, not pose error against truth; all 19 actions remain in calibration.',
        output_sha256=sha(out / 'OFFSET_BLOCK_DIAGNOSTIC.npz')))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', required=True, type=Path)
    args = parser.parse_args()
    torch.set_num_threads(2)
    main(args.out.resolve())
