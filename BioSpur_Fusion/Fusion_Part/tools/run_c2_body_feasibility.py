"""Fixed-calibration structure experiment; frozen cached observations, no H fit.

An existing, verified-before-edit baseline is input evidence, not a newly
certified candidate. Existing producer hashes are never rewritten or bypassed.
The new body envelope changes neither the network features nor its recurrence.
"""
import argparse
import copy
import json
from pathlib import Path
import time

import numpy as np
import torch

from biospur_fusion.c2_sparse_nodes.inputs import ROOT,sha,episode_contracts
from biospur_fusion.c2_five_calibration.body_feasibility import BodyFeasibility,make_body_spec
from biospur_fusion.c2_five_calibration.solver import solve_pose,acceleration_residual,valid_support
from biospur_fusion.c2_five_calibration.workflow import fingerprint,SMPL
from biospur_fusion.c2_imucoco.workflow import SURFACE
from biospur_fusion.c2_imucoco.backend import load_pose


def write(path,value):
    path.write_text(json.dumps(value,indent=2,allow_nan=False))


def check_bindings(bindings):
    for name,value in bindings.items():
        if sha(Path(name))!=value:raise ValueError('baseline evidence changed: '+name)


def dynamic_audit(q,rotation,g,levers):
    residual=acceleration_residual(torch.as_tensor(rotation),torch.as_tensor(q['acceleration']),g,levers)
    values=residual[valid_support(q['valid'])].square().mean((0,1,3)).sqrt().tolist()
    return dict(relative_acceleration_rms_mps2_by_node=values,
                nodes=['left_forearm','right_forearm','left_shank','right_shank'])


def main(out,actions,iterations):
    torch.set_num_threads(1);started=time.monotonic()
    binding_path=out/'BASELINE_BINDING.json'
    bindings=json.loads(binding_path.read_text());check_bindings(bindings)
    baseline=ROOT/'logs/c2_shared_full_20260914/candidate'
    g=json.loads((baseline/'GEOMETRY.json').read_text())
    _,body=load_pose(SMPL,out)
    _,skin=body.get_zero_pose_joint_and_vertex()
    g['body_feasibility']=make_body_spec(g,skin.numpy(),json.loads(SURFACE.read_text()))
    # This runs before inspecting old output positions or fitting new poses.
    write(out/'BODY_GEOMETRY.json',g)
    write(out/'EXPERIMENT_CONTRACT.json',dict(baseline_bindings=bindings,source_sha256=fingerprint(),
        runner_sha256=sha(Path(__file__)),body_geometry_sha256=sha(out/'BODY_GEOMETRY.json'),
        iterations=iterations,actions=actions,wall_limit_s=900,parameters_refitted=False,
        H_used_for_fit=False,ten_used=False,neural_recomputed=False,
        cached_prior_reason='calibration, all network features, initial state and geometric bone offsets unchanged',
        acceptance='all valid frames clear inner core; each relative acceleration RMS <= control*1.1 + 0.1 m/s2; not accuracy proof'))
    with np.load(baseline/'ACCEPTED_PRIOR.npz',allow_pickle=False) as z:q={k:z[k][::3] for k in z.files}
    with np.load(baseline/'CHECKPOINT.npz',allow_pickle=False) as z:
        old=z['rotation'];levers=z['levers']
        if not np.array_equal(q['time_s'],z['time_s']):raise ValueError('physical sample grid changed')
    anatomy=BodyFeasibility(g)
    write(out/'OLD_C2_BODY_AUDIT.json',anatomy.audit(torch.as_tensor(old),torch.as_tensor(q['valid'])))
    plain=copy.deepcopy(g);plain.pop('body_feasibility')
    contracts=episode_contracts()
    if actions==['all']:windows={'all_C2':(q,old)}
    else:
        windows={}
        for name in actions:
            if name not in contracts:raise ValueError('only recorded C2 actions allowed')
            c=contracts[name];ids=(q['time_s']>=c['lo']-1)&(q['time_s']<c['hi']+1)
            windows[name]=({k:v[ids] for k,v in q.items()},old[ids])
    reports={}
    for name,(part,warm) in windows.items():
        before=anatomy.audit(torch.as_tensor(warm),torch.as_tensor(part['valid']))
        results={}
        for branch,geom in (('control',plain),('constrained',g)):
            remaining=900-(time.monotonic()-started)
            if remaining<=0:raise TimeoutError('C2 structure experiment total deadline')
            r,audit=solve_pose(**part,geometry=geom,levers=levers,iterations=iterations,
                               wall_limit_s=remaining,initial_rotation=warm)
            structural=anatomy.audit(torch.as_tensor(r),torch.as_tensor(part['valid']))
            dynamic=dynamic_audit(part,r,g,levers)
            results[branch]=dict(body=structural,dynamic=dynamic,solver=audit)
            np.savez_compressed(out/(name+'_'+branch+'.npz'),**part,rotation=r)
            print(json.dumps(dict(action=name,branch=branch,body=structural['max_excess_proxy_m'],
                                  acceleration=dynamic['relative_acceleration_rms_mps2_by_node'])),flush=True)
        a=np.asarray(results['control']['dynamic']['relative_acceleration_rms_mps2_by_node'])
        b=np.asarray(results['constrained']['dynamic']['relative_acceleration_rms_mps2_by_node'])
        reports[name]=dict(before=before,**results,
            structure_and_dynamic_gate=bool(results['constrained']['body']['accepted'] and np.all(b<=a*1.1+.1)))
        write(out/'C2_COMPARISON.json',reports)
    check_bindings(bindings)
    contract=json.loads((out/'EXPERIMENT_CONTRACT.json').read_text())
    for name,value in contract['source_sha256'].items():
        if sha(ROOT/name)!=value:raise ValueError('producer changed during C2 experiment')
    write(out/'C2_RESULT.json',dict(completed=True,all_gates_passed=all(v['structure_and_dynamic_gate'] for v in reports.values()),
        elapsed_s=time.monotonic()-started,calibration_parameters_updated=False,product_accepted=False,
        geometry_sha256=sha(out/'BODY_GEOMETRY.json'),H_opened=False,ten_opened=False))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--out',type=Path,required=True)
    p.add_argument('--actions',nargs='+',default=['all']);p.add_argument('--iterations',type=int,default=60)
    a=p.parse_args();main(a.out.resolve(),a.actions,a.iterations)
