#!/usr/bin/env python3
"""Continue an evidence-bound C2 prefix through every remaining action.

Re-read raw input without copying it. Reuse the qualified14 physical prefix
only after matching its frontend and source bindings. Each new action obtains
a fresh prefix-conditioned recurrence and cumulative physical parameter fit.
"""
import argparse
import json
from pathlib import Path
import time

import numpy as np
import torch

from biospur_fusion.c2_sparse_nodes.inputs import ROOT,NODES,FiveNodeFrontend,episode_contracts,sha
from biospur_fusion.c2_five_calibration.frontend import fit_frontend
from biospur_fusion.c2_five_calibration.replay import replay_prior,parameter_digest
from biospur_fusion.c2_five_calibration.workflow import fingerprint,SMPL
from biospur_fusion.c2_five_calibration.progressive.physical_prefix import refine_prefix
from biospur_fusion.c2_imucoco.workflow import SURFACE
from biospur_fusion.c2_imucoco.backend import load_pose
from biospur_fusion.c2_imucoco.upstream import verify_assets


def write(path,value):
    path.write_text(json.dumps(value,indent=2,allow_nan=False))


def main(out,seed):
    out=out.resolve();seed=seed.resolve();out.mkdir(parents=True,exist_ok=False)
    started=time.monotonic();deadline=started+4500
    neural=seed/'arrived_neural_prefix14';physical=seed/'physical_prefix14'
    old=json.loads((physical/'RESULT.json').read_text())
    if not old.get('completed') or sha(physical/'PHYSICAL_PREFIX.npz')!=old['output_sha256']:
        raise ValueError('qualified physical prefix seed required')
    old_contract=json.loads((neural/'CONTRACT.json').read_text())
    if old_contract['H_used'] or old_contract['ten_node_used']:
        raise ValueError('seed cannot contain H or ten-node information')
    for name,value in json.loads((seed/'STAGE_06_PHYSICAL_SOURCE_HASHES.json').read_text()).items():
        if sha(ROOT/name)!=value:raise ValueError('physical seed producer changed: '+name)
    sources=fingerprint()
    for name in ('tools/run_c2_progressive_physical_candidate.py',
                 'src/biospur_fusion/c2_five_calibration/progressive/physical_prefix.py'):
        sources[name]=sha(ROOT/name)
    contract=dict(kind='PROGRESSIVE_PHYSICAL_C2_CANDIDATE',seed=str(seed),
        source_sha256=sources,H_used=False,ten_node_used=False,wall_limit_s=4500,
        neural_wall_limit_per_prefix_s=600,physical_wall_limit_per_prefix_s=600,
        physical_iterations=30,shared_parameter_update='cumulative replacement; old values only warm starts',
        scope='frames plus nominal-centered sensor levers and latent pose; constant arm heading remains conditional',
        acceptance='diagnostic candidate; no proof of H accuracy or unique anatomy',
        surface_sha256=sha(SURFACE),smpl_sha256=sha(SMPL),models=verify_assets())
    write(out/'CONTRACT.json',contract)
    geometry=json.loads((neural/'GEOMETRY.json').read_text())
    write(out/'GEOMETRY.json',geometry)
    with np.load(physical/'PHYSICAL_PREFIX.npz',allow_pickle=False) as z:
        previous={k:z[k] for k in z.files}
    reader=FiveNodeFrontend();contracts=episode_contracts();episodes={};chunks={n:[] for n in NODES}
    surface=json.loads(SURFACE.read_text());poser=None;history=[]
    for action,window in contracts.items():
        if time.monotonic()>=deadline:raise TimeoutError('progressive candidate total budget')
        data,audit=reader.read({action:window},start=reader.cursor,keep_continuous=True)
        episodes[action]=data[action]
        for n in NODES:chunks[n].append(data['_continuous'][n]['imu'])
        if list(contracts).index(action)<list(contracts).index('14_trunk_flex_extend'):
            continue
        current=dict(episodes)
        current['_continuous']={}
        for n in NODES:
            a=np.concatenate(chunks[n])
            current['_continuous'][n]={'imu':a[a[:,0]<=episodes[action][n]['imu'][-1,0]]}
        calibration=fit_frontend(current,surface,prefix=True)
        stage=out/action;stage.mkdir()
        write(stage/'FRONTEND.json',calibration);write(stage/'INPUT_AUDIT.json',audit)
        if action=='14_trunk_flex_extend':
            old_front=json.loads((neural/'FRONTEND.json').read_text())
            if parameter_digest(calibration)!=parameter_digest(old_front):
                raise ValueError('fresh arrived frontend does not match seed')
            history.append(dict(action=action,reused_qualified_seed=True,seed_hash=old['output_sha256']))
            write(out/'HISTORY.json',history)
            continue
        if deadline-time.monotonic()<1200:
            raise TimeoutError('insufficient budget for next complete neural+physical prefix')
        if poser is None:poser,_=load_pose(SMPL,out)
        q,neural_audit,_=replay_prior(current,calibration,geometry,poser,prefix=True,
            wall_limit_s=600,progress=lambda x:print(json.dumps(dict(action=action,neural=x)),flush=True))
        np.savez_compressed(stage/'C2_PREFIX_PRIOR.npz',**q)
        write(stage/'NEURAL_AUDIT.json',neural_audit)
        q20={k:v[::3] for k,v in q.items()}
        checkpoint,physical_audit=refine_prefix(q20,geometry,previous=previous,iterations=30,
            wall_limit_s=600,progress=lambda name,x:write(stage/(name.upper()+'_AUDIT.json'),x))
        np.savez_compressed(stage/'PHYSICAL_PREFIX.npz',**checkpoint)
        write(stage/'PHYSICAL_AUDIT.json',physical_audit)
        previous=checkpoint
        entry=dict(action=action,frames=len(q20['time_s']),
            frontend_sha256=sha(stage/'FRONTEND.json'),
            prior_sha256=sha(stage/'C2_PREFIX_PRIOR.npz'),physical_sha256=sha(stage/'PHYSICAL_PREFIX.npz'),
            elapsed_seconds=time.monotonic()-started,calibration_accepted=False)
        history.append(entry);write(out/'HISTORY.json',history)
        print(json.dumps(entry),flush=True)
        for name,value in sources.items():
            if sha(ROOT/name)!=value:raise ValueError('candidate producer changed: '+name)
    write(out/'RESULT.json',dict(completed=True,actions=list(contracts),history=history,
        frozen_final_prefix='19_heel_to_butt_right',calibration_accepted=False,
        H_evaluated=False,elapsed_seconds=time.monotonic()-started))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--out',required=True,type=Path)
    p.add_argument('--seed',required=True,type=Path)
    a=p.parse_args();torch.set_num_threads(1);main(a.out,a.seed)
