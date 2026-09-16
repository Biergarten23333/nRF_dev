#!/usr/bin/env python3
"""Check optimizer convergence against the same five-only frozen objective."""
import argparse
import json
from pathlib import Path
import numpy as np
import torch
from biospur_fusion.c2_five_calibration.workflow import action_data
from biospur_fusion.c2_five_calibration.solver import solve_pose
from biospur_fusion.c2_five_calibration.geometry import DISPLAY, joints_from_global
from biospur_fusion.c2_imucoco.body import bend_angles
from biospur_fusion.c2_imucoco.workflow import write
from biospur_fusion.c2_sparse_nodes.inputs import sha


def main(out):
    target=out/'OPTIMIZER_DIAGNOSTIC.json'
    if target.exists(): raise ValueError('preserve prior probe')
    contract=json.loads((out/'TASK_CONTRACT.json').read_text())
    g=json.loads((out/'GEOMETRY.json').read_text())
    levers=json.loads((out/'PHYSICAL_CALIBRATION.json').read_text())['fitted_sensor_levers_m']
    actions=action_data(out); report={}; outputs={}
    for name in contract['short_actions']:
        q=actions[name]; ids=q['time_s']<q['time_s'][0]+contract['window_s'];q={k:v[ids] for k,v in q.items()}
        args=(q['prior'],q['observed'],q['acceleration'],q['valid'],q['time_s'],g,levers)
        first,a=solve_pose(*args,iterations=150,wall_limit_s=40)
        warm,b=solve_pose(*args,iterations=150,wall_limit_s=40,initial_rotation=first)
        long,c=solve_pose(*args,iterations=600,wall_limit_s=80)
        if abs(a['history'][-1]['loss']-b['history'][0]['loss'])>1e-6:
            raise ValueError('warm-start objective changed; no valid causal comparison')
        def bends(r):return bend_angles(joints_from_global(torch.tensor(r),g).numpy()[:,DISPLAY])
        initial=bends(first)
        row=dict(cold_150=a,warm_150=b,cold_600=c,
            warm_change_mae_deg=np.mean(abs(bends(warm)-initial),axis=0).tolist(),
            long_change_mae_deg=np.mean(abs(bends(long)-initial),axis=0).tolist(),
            warm_relative_loss_improvement=1-b['history'][-1]['loss']/a['history'][-1]['loss'],
            long_relative_loss_improvement=1-c['history'][-1]['loss']/a['history'][-1]['loss'])
        report[name]=row
        for label,r in [('cold_150',first),('warm_150',warm),('cold_600',long)]:outputs[name+'/'+label]=r
        print(json.dumps(dict(action=name,losses=[x['history'][-1]['loss'] for x in (a,b,c)],
            warm_change=row['warm_change_mae_deg'],long_change=row['long_change_mae_deg'])),flush=True)
    np.savez_compressed(out/'OPTIMIZER_DIAGNOSTIC.npz',**outputs)
    write(target,dict(actions=report,ten_node_used=False,H_used=False,offsets_fixed=True,
        calibration_refitted=False,source_sha256=sha(Path(__file__)),
        qualification='Same-objective convergence and sensitivity; lower objective is not motion-accuracy ground truth.'))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--out',type=Path,required=True)
    args=p.parse_args();torch.set_num_threads(2);main(args.out.resolve())
