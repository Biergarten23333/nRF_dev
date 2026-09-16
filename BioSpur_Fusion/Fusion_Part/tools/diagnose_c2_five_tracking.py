#!/usr/bin/env python3
"""Compare a preserved solver with the current one on five-only C2 windows."""
import argparse
import importlib.util
import json
from pathlib import Path

import numpy as np
import torch

from biospur_fusion.c2_five_calibration.geometry import DISPLAY, joints_from_global
from biospur_fusion.c2_five_calibration.solver import solve_pose
from biospur_fusion.c2_five_calibration.workflow import action_data
from biospur_fusion.c2_imucoco.body import bend_angles
from biospur_fusion.c2_imucoco.workflow import write
from biospur_fusion.c2_sparse_nodes.inputs import sha


def check(out):
    if (out/'TRACKING_PROBE.json').exists():
        raise ValueError('preserve completed tracking probe')
    policy=json.loads((out/'TRACKING_PROBE_CONTRACT.json').read_text())
    old=out/'BASELINE_SOLVER.py'
    if sha(old)!=policy['baseline_solver_sha256']:
        raise ValueError('preserved baseline solver changed')
    spec=importlib.util.spec_from_file_location(
        'biospur_fusion.c2_five_calibration._tracking_probe_baseline',old)
    baseline=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(baseline)
    geometry=json.loads((out/'GEOMETRY.json').read_text())
    actions=action_data(out)
    reports, arrays={},{}
    for name in policy['actions']:
        full=actions[name]
        take=full['time_s']<full['time_s'][0]+policy['window_s']
        q={key:value[take] for key,value in full.items()}
        bends={}; reports[name]={}
        for label,solver in [('baseline',baseline.solve_pose),('corrected',solve_pose)]:
            pose,audit=solver(q['prior'],q['observed'],q['acceleration'],q['valid'],
                q['time_s'],geometry,geometry['nominal_sensor_levers_m'],
                iterations=150,wall_limit_s=40)
            if not np.isfinite(pose).all() or audit['observed_rotation_max_element_error']>1e-5:
                raise ValueError('physical invariant failed')
            bends[label]=bend_angles(joints_from_global(torch.tensor(pose),geometry).numpy()[:,DISPLAY])
            arrays[name+'/'+label]=pose
            reports[name][label]=audit
        reports[name]['bend_change_mae_deg']=abs(bends['corrected']-bends['baseline']).mean(0).tolist()
        print(json.dumps(dict(action=name,report=reports[name])),flush=True)
    np.savez_compressed(out/'TRACKING_PROBE.npz',**arrays)
    write(out/'TRACKING_PROBE.json',dict(actions=reports,
        input_sha256={name:sha(out/name) for name in ('C2_PRIOR.json','C2_PRIOR.npz','GEOMETRY.json','TRACKING_PROBE_CONTRACT.json')},
        baseline_solver_sha256=sha(old),current_solver_sha256=sha(Path(__file__).resolve().parents[1]/'src/biospur_fusion/c2_five_calibration/solver.py'),
        source_sha256=sha(Path(__file__)),output_sha256=sha(out/'TRACKING_PROBE.npz'),
        H_used=False,ten_node_reference_used=False,parameters_refitted=False,
        qualification='Five-only mechanism/sensitivity probe, not pose accuracy against truth'))


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args();torch.set_num_threads(2);check(args.out.resolve())
