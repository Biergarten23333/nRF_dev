"""Frozen body-policy regression on already-seen H; never fit H parameters."""
import argparse
import json
from pathlib import Path
import time

import numpy as np
import torch

from run_c2_body_feasibility import check_bindings,dynamic_audit,write
from biospur_fusion.c2_sparse_nodes.inputs import ROOT,sha
from biospur_fusion.c2_five_calibration.solver import solve_pose
from biospur_fusion.c2_five_calibration.body_feasibility import BodyFeasibility
from biospur_fusion.c2_five_calibration.workflow import fingerprint


def main(out):
    torch.set_num_threads(1)
    if not json.loads((out/'C2_RESULT.json').read_text())['completed']:
        raise ValueError('complete C2 experiment required before H')
    frozen=dict(source_sha256=fingerprint(),geometry_sha256=sha(out/'BODY_GEOMETRY.json'),
        C2_result_sha256=sha(out/'C2_RESULT.json'),runner_sha256=sha(Path(__file__)),
        C2_outputs={n:sha(out/('all_C2_'+n+'.npz')) for n in ('control','constrained')},
        parameters_updated=False,H_is_new_blind_test=False,iterations=150,wall_limit_s=300)
    write(out/'H_FROZEN.json',frozen)
    bindings=json.loads((out/'BASELINE_BINDING.json').read_text());check_bindings(bindings)
    old=ROOT/'logs/c2_shared_full_20260914'
    hmeta=json.loads((old/'holdout/RESULT.json').read_text())
    hfile=old/'holdout/H_REPLAY.npz'
    if sha(hfile)!=hmeta['output_sha256']:raise ValueError('H baseline changed')
    with np.load(hfile,allow_pickle=False) as z:
        q={k:z[k] for k in ('time_s','prior','observed','acceleration','valid')};warm=z['rotation']
    with np.load(old/'candidate/CHECKPOINT.npz',allow_pickle=False) as z:levers=z['levers']
    g=json.loads((out/'BODY_GEOMETRY.json').read_text());plain=dict(g);plain.pop('body_feasibility')
    if plain!=json.loads((old/'candidate/GEOMETRY.json').read_text()):
        raise ValueError('cached neural prior requires unchanged kinematic geometry')
    anatomy=BodyFeasibility(g);reports={};started=time.monotonic()
    for branch,geom in (('control',plain),('constrained',g)):
        r,audit=solve_pose(**q,geometry=geom,levers=levers,iterations=150,
                          wall_limit_s=300-(time.monotonic()-started),initial_rotation=warm)
        np.savez_compressed(out/('H_'+branch+'.npz'),**q,rotation=r)
        reports[branch]=dict(solver=audit,body=anatomy.audit(torch.as_tensor(r),torch.as_tensor(q['valid'])),
                            dynamic=dynamic_audit(q,r,g,levers))
        print(json.dumps(dict(branch=branch,body=reports[branch]['body']['max_excess_proxy_m'],
            acceleration=reports[branch]['dynamic']['relative_acceleration_rms_mps2_by_node'])),flush=True)
    check_bindings(bindings)
    for name,value in frozen['source_sha256'].items():
        if sha(ROOT/name)!=value:raise ValueError('H producer changed')
    if sha(out/'BODY_GEOMETRY.json')!=frozen['geometry_sha256']:raise ValueError('H geometry changed')
    for name,value in frozen['C2_outputs'].items():
        if sha(out/('all_C2_'+name+'.npz'))!=value:raise ValueError('C2 output changed during H')
    if sha(Path(__file__))!=frozen['runner_sha256']:raise ValueError('H runner changed')
    write(out/'H_RESULT.json',dict(completed=True,branches=reports,H_input_sha256=sha(hfile),
        outputs={n:sha(out/('H_'+n+'.npz')) for n in reports},frozen=frozen,
        H_used_for_parameter_fit=False,product_accepted=False,ten_node_used=False))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--out',type=Path,required=True)
    main(p.parse_args().out.resolve())
