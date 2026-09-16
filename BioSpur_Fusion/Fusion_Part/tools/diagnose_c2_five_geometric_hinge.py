#!/usr/bin/env python3
"""Bounded five-only C2 comparison against the preserved pre-repair solver."""
import argparse
import importlib.util
import json
import sys
from pathlib import Path
import numpy as np
import torch

from biospur_fusion.c2_five_calibration import anatomy
from biospur_fusion.c2_five_calibration.solver import solve_pose
from biospur_fusion.c2_five_calibration.geometry import DISPLAY, joints_from_global
from biospur_fusion.c2_five_calibration.workflow import action_data, fingerprint
from biospur_fusion.c2_imucoco.body import bend_angles
from biospur_fusion.c2_sparse_nodes.inputs import sha


def load_module(path, name):
    spec=importlib.util.spec_from_file_location(name,path)
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run(out):
    if (out/'GEOMETRIC_PROBE.json').exists():
        raise ValueError('preserve earlier probe')
    policy=json.loads((out/'GEOMETRIC_PROBE_CONTRACT.json').read_text())
    for name,expected in policy['input_sha256'].items():
        if sha(out/name)!=expected:raise ValueError('probe input changed: '+name)
    legacy_anatomy=load_module(out/'BASELINE_anatomy.py',anatomy.__package__+'._legacy_anatomy')
    legacy_solver=load_module(out/'BASELINE_solver.py',anatomy.__package__+'._legacy_solver')
    geometry=json.loads((out/'GEOMETRY.json').read_text())
    data=action_data(out)
    reports,arrays={},{}
    for name in policy['actions']:
        full=data[name]
        ids=full['time_s']<full['time_s'][0]+policy['window_s']
        q={k:v[ids] for k,v in full.items()}
        reports[name]={}; bends={}
        for label,owner,solver in [('baseline',legacy_anatomy,legacy_solver.solve_pose),
                                    ('geometric',anatomy,solve_pose)]:
            # The old solver imports its owner inside solve_pose. This is a
            # single process/single solve switch, restored even on failure.
            sys.modules[anatomy.__name__]=owner
            try:
                pose,audit=solver(q['prior'],q['observed'],q['acceleration'],q['valid'],q['time_s'],
                    geometry,geometry['nominal_sensor_levers_m'],iterations=150,wall_limit_s=45.)
            finally:
                sys.modules[anatomy.__name__]=anatomy
            if not np.isfinite(pose).all() or audit['observed_rotation_max_element_error']>1e-12:
                raise ValueError('retained measurement invariant failed')
            bends[label]=bend_angles(joints_from_global(torch.tensor(pose),geometry).numpy()[:,DISPLAY])
            reports[name][label]=audit
            arrays[name+'/'+label]=pose
        reports[name]['geometric_bend_change_mae_deg']=abs(bends['geometric']-bends['baseline']).mean(0).tolist()
        print(json.dumps({'action':name,**reports[name]}),flush=True)
    np.savez_compressed(out/'GEOMETRIC_PROBE.npz',**arrays)
    report=dict(actions=reports,source_sha256=fingerprint(),input_sha256=policy['input_sha256'],
        H_used=False,ten_node_reference_used=False,parameters_refitted=False,
        qualification='five-only real mechanism probe; objective targets differ, loss is not a pose-accuracy ranking',
        output_sha256=sha(out/'GEOMETRIC_PROBE.npz'))
    (out/'GEOMETRIC_PROBE.json').write_text(json.dumps(report,indent=2)+'\n')


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--out',required=True,type=Path)
    args=parser.parse_args();torch.set_num_threads(2);run(args.out.resolve())
