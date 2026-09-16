#!/usr/bin/env python3
"""Check the next shared-offset update without changing a frozen calibration."""
import argparse
import json
from pathlib import Path

import numpy as np
import torch

from biospur_fusion.c2_five_calibration.frontend import FIT
from biospur_fusion.c2_five_calibration.solver import lever_system, fit_levers
from biospur_fusion.c2_five_calibration.workflow import action_data
from biospur_fusion.c2_imucoco.workflow import write
from biospur_fusion.c2_sparse_nodes.inputs import sha


def audit(out):
    target = out / 'CONVERGENCE_AUDIT.json'
    if target.exists():
        raise ValueError('preserve existing convergence audit')
    calibration = json.loads((out / 'PHYSICAL_CALIBRATION.json').read_text())
    replay = json.loads((out / 'C2_VALIDATION.json').read_text())
    geometry = json.loads((out / 'GEOMETRY.json').read_text())
    if sha(out / 'C2_VALIDATION.npz') != replay['output_sha256']:
        raise ValueError('frozen replay changed')
    actions = action_data(out)
    if {name[:2] for name in actions} != FIT:
        raise ValueError('all C2 actions required')
    with np.load(out / 'C2_VALIDATION.npz') as archive:
        systems = []
        for name, data in actions.items():
            np.testing.assert_array_equal(archive[name + '/time_s'], data['time_s'])
            systems.append(lever_system(archive[name + '/rotation'], data['acceleration'],
                                        data['valid'], geometry))
    candidate, fit = fit_levers(systems, geometry['nominal_sensor_levers_m'])
    change = candidate - np.asarray(calibration['fitted_sensor_levers_m'])
    report = dict(actions=list(actions), frozen_parameters_changed=False,
        next_offset_update_m=change.tolist(), max_next_update_mm=float(abs(change).max()*1000),
        next_active_bounds=fit['active_bounds'], next_fit=fit,
        role='Numerical convergence diagnostic at frozen replay poses; not a new calibration or an accuracy test.',
        source_sha256=sha(Path(__file__)),
        bound_inputs={name: sha(out/name) for name in
            ('PHYSICAL_CALIBRATION.json', 'C2_VALIDATION.json', 'GEOMETRY.json', 'C2_PRIOR.json')})
    write(target, report)
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    torch.set_num_threads(2)
    audit(args.out.resolve())
