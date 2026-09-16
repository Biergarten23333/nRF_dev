#!/usr/bin/env python3
"""Attribute pose changes to observation replacement, joint projection and fitting.

Read-only, post-inference diagnosis. Reference rotations never enter a solver.
"""
import argparse
from contextlib import ExitStack
import json
from pathlib import Path

import numpy as np
from scipy.spatial.transform import Rotation
import torch

from biospur_fusion.c2_five_calibration.anatomy import JointModel, PROXIMAL
from biospur_fusion.c2_five_calibration.geometry import OBSERVED, joints_from_global
from biospur_fusion.c2_five_calibration.solver import bend_cosines
from biospur_fusion.c2_five_calibration.workflow import action_data
from biospur_fusion.c2_imucoco.workflow import INPUT_RUN, write
from biospur_fusion.c2_sparse_nodes.evaluation import CAL_REFERENCE, H_REFERENCE, PAIRS, baseline_on_grid
from biospur_fusion.c2_sparse_nodes.inputs import sha


def audit(out):
    target = out / 'PROJECTION_AUDIT.json'
    if target.exists():
        raise ValueError('preserve existing projection audit')
    geometry = json.loads((out / 'GEOMETRY.json').read_text())
    actions = action_data(out)
    contracts = json.loads((INPUT_RUN / 'CALIBRATION_INPUT_AUDIT.json').read_text())['contracts']
    h_contracts = json.loads((INPUT_RUN / 'HOLDOUT_INPUT_AUDIT.json').read_text())['contracts']
    records = {}
    for label in ('C2_VALIDATION', 'H_REPLAY'):
        record = json.loads((out / (label + '.json')).read_text())
        if record['output_sha256'] != sha(out / (label + '.npz')):
            raise ValueError('inference output changed: ' + label)
    with np.load(out / 'H_REPLAY.npz') as data:
        h = {k: data[k] for k in data.files}
    model = JointModel(geometry)
    with ExitStack() as stack:
        calref = stack.enter_context(np.load(CAL_REFERENCE))
        href = stack.enter_context(np.load(H_REFERENCE))
        fitted = stack.enter_context(np.load(out / 'C2_VALIDATION.npz'))
        for index, (name, window) in enumerate({**contracts, **h_contracts}.items()):
            hold = name.startswith('H')
            if hold:
                ids = (h['time_s'] >= window['lo']) & (h['time_s'] <= window['hi'])
                q = {k: v[ids] for k, v in h.items()}
                final = q['rotation']
            else:
                q = actions[name]
                final = fitted[name + '/rotation']
            prior = torch.tensor(q['prior'], dtype=torch.float64)
            observed = torch.tensor(q['observed'], dtype=torch.float64)
            exact = prior.clone()
            exact[:, OBSERVED] = observed
            projection = model.rotation(prior, observed, model.initial(prior, observed))
            stages = dict(network=prior, retained_replacement=exact,
                          joint_projection=projection, optimized=torch.tensor(final))
            ref, valid = baseline_on_grid(href if hold else calref,
                name if hold else f'{index:02d}', q['time_s'], window, hold)
            valid &= q['valid']
            reference = np.rad2deg(np.arccos(np.clip(np.stack([
                (ref[a][:, :, 2] * ref[b][:, :, 2]).sum(-1) for a, b in PAIRS], -1), -1, 1)))
            result = {}
            for label, rotation in stages.items():
                bend = np.rad2deg(np.arccos(np.clip(bend_cosines(rotation, geometry).numpy(), -1, 1)))
                delta = prior[:, PROXIMAL].transpose(-1, -2) @ rotation[:, PROXIMAL]
                angle = np.rad2deg(Rotation.from_matrix(delta.numpy().reshape(-1, 3, 3)).magnitude()).reshape(-1, 4)
                positions = joints_from_global(rotation, geometry)
                result[label] = dict(bend_MAE_deg=np.abs(bend-reference)[valid].mean(0).tolist(),
                    proximal_change_from_network_mean_deg=angle[valid].mean(0).tolist())
                if name == 'H01_boxing':
                    result[label]['frame_244'] = dict(time_s=float(q['time_s'][244]),
                        bend_deg=bend[244].tolist(), reference_bend_deg=reference[244].tolist(),
                        proximal_change_from_network_deg=angle[244].tolist(),
                        right_shoulder_elbow_wrist_m=positions[244, [17, 19, 21]].tolist())
            records[name] = result
    write(target, dict(actions=records, fitting_performed=False, gates_changed=False,
        reference_is_external_truth=False, purpose='Locate the stage introducing observed regression',
        source_sha256=sha(Path(__file__)), geometry_sha256=sha(out/'GEOMETRY.json'),
        input_sha256={name:sha(out/name) for name in ('C2_PRIOR.npz', 'C2_VALIDATION.npz', 'H_REPLAY.npz')},
        reference_sha256={str(p):sha(p) for p in (CAL_REFERENCE,H_REFERENCE)}))
    print(json.dumps({k:v for k,v in records.items() if k.startswith('H')}, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    torch.set_num_threads(2)
    audit(args.out.resolve())
