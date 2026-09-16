#!/usr/bin/env python3
"""Bounded representative checks before the all-C2 shared-parameter fit."""
import argparse
import json
from pathlib import Path

import numpy as np
import torch

from biospur_fusion.c2_five_calibration.workflow import action_data, fingerprint
from biospur_fusion.c2_five_calibration.solver import solve_pose
from biospur_fusion.c2_imucoco.workflow import write
from biospur_fusion.c2_sparse_nodes.inputs import sha


def check(out):
    if (out/'REPRESENTATIVE.json').exists():raise ValueError('preserve previous representative checks')
    prior=json.loads((out/'C2_PRIOR.json').read_text())
    if prior['source_sha256']!=fingerprint():raise ValueError('source changed after neural initialization')
    geometry=json.loads((out/'GEOMETRY.json').read_text())
    actions=action_data(out)
    reports={}
    for name in ('00_initial_still','06_elbow_left','07_elbow_right','08_hip_left'):
        q=actions[name]
        rotation,report=solve_pose(q['prior'],q['observed'],q['acceleration'],q['valid'],q['time_s'],
            geometry,geometry['nominal_sensor_levers_m'],iterations=150,wall_limit_s=120)
        if not np.isfinite(rotation).all() or report['observed_rotation_max_element_error']>1e-5:
            raise ValueError('representative physical mechanism failed: '+name)
        reports[name]=report
        print(json.dumps(dict(action=name,**report)),flush=True)
    write(out/'REPRESENTATIVE.json',reports)
    write(out/'REPRESENTATIVE_SOURCE.json',dict(core=fingerprint(),entrypoint=sha(Path(__file__))))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args();torch.set_num_threads(2);check(args.out.resolve())
