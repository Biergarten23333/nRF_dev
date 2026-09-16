#!/usr/bin/env python3
"""One sealed progressive candidate, frozen H inference, no H calibration."""
import argparse
import json
from pathlib import Path
import time

import numpy as np
import torch

from c2_progressive_candidate_io import resolve
from biospur_fusion.c2_sparse_nodes.inputs import NODES,ROOT,sha,FiveNodeFrontend,episode_contracts
from biospur_fusion.c2_five_calibration.frontend import prepare,initial_state
from biospur_fusion.c2_five_calibration.solver import solve_pose
from biospur_fusion.c2_five_calibration.workflow import fingerprint,SMPL
from biospur_fusion.c2_imucoco.backend import load_pose,ChunkedPoseStream
from biospur_fusion.c2_imucoco.upstream import verify_assets


def write(path,value):
    path.write_text(json.dumps(value,indent=2,allow_nan=False))


def main(candidate,out,kind="progressive"):
    if kind=="shared":
        from c2_shared_prefix_candidate_io import resolve
    else:
        from c2_progressive_candidate_io import resolve
    candidate=candidate.resolve();out=out.resolve();out.mkdir(parents=True,exist_ok=False)
    started=time.monotonic()
    # Resolve/freeze before any holdout contract or payload is opened.
    calibration,geometry,levers,bindings=resolve(candidate)
    sources=fingerprint()
    for name in ('tools/replay_c2_progressive_candidate_h.py','tools/c2_progressive_candidate_io.py'):
        sources[name]=sha(ROOT/name)
    if kind=='shared':sources['tools/c2_shared_prefix_candidate_io.py']=sha(ROOT/'tools/c2_shared_prefix_candidate_io.py')
    write(out/'FROZEN.json',dict(candidate=str(candidate),bindings=bindings,source_sha256=sources,
        model_assets=verify_assets(),smpl_sha256=sha(SMPL),diagnostic_only=True,
        calibration_accepted=False,calibration_parameters_updated=False,
        neural_wall_limit_s=600,physical_wall_limit_s=300,total_wall_limit_s=1800,
        physical_iterations=150,H_used_to_select_parameters=False))
    write(out/'GEOMETRY.json',geometry)
    reader=FiveNodeFrontend()
    cal,cal_audit=reader.read(episode_contracts(),keep_continuous=True)
    h_contracts=episode_contracts(holdout=True)
    hold,h_audit=reader.read(h_contracts,start=reader.cursor,keep_continuous=True)
    if cal_audit['clock_sha256']!=h_audit['clock_sha256']:raise ValueError('C2/H clocks differ')
    write(out/'INPUT_AUDIT.json',dict(calibration=cal_audit,holdout=h_audit))
    joined={n:{'imu':np.concatenate((cal['_continuous'][n]['imu'],hold['_continuous'][n]['imu']))} for n in NODES}
    initial=initial_state(prepare(cal['00_initial_still'],calibration,geometry),geometry)
    data=prepare(joined,calibration,geometry)
    poser,_=load_pose(SMPL,out)
    stream=ChunkedPoseStream(poser,initial_global_rotation=initial,sensor_vertices=geometry['sensor_vertices'])
    prediction,neural=stream.run(data['features'],input_valid=data['input_valid'],wall_limit_s=600,
        progress=lambda item:print(json.dumps(item),flush=True))
    first_h=max(hold['_continuous'][n]['imu'][0,0] for n in NODES)
    ids=np.arange(0,len(data['time_s']),3)
    ids=ids[data['time_s'][ids]>=first_h]
    q=dict(time_s=data['time_s'][ids],prior=prediction['global_rotation'][ids],
           observed=data['orientation'][ids],acceleration=data['acceleration_mps2'][ids],valid=data['input_valid'][ids])
    np.savez_compressed(out/'H_PRIOR.npz',**q)
    write(out/'NEURAL_AUDIT.json',neural)
    rotation,physical=solve_pose(**q,geometry=geometry,levers=levers,iterations=150,wall_limit_s=300)
    np.savez_compressed(out/'H_REPLAY.npz',**q,rotation=rotation)
    _,_,_,after=resolve(candidate)
    if after!=bindings:raise ValueError('candidate changed during H')
    for name,value in sources.items():
        if sha(ROOT/name)!=value:raise ValueError('H producer changed: '+name)
    write(out/'RESULT.json',dict(completed=True,output_sha256=sha(out/'H_REPLAY.npz'),
        input_audit_sha256=sha(out/'INPUT_AUDIT.json'),
        elapsed_seconds=time.monotonic()-started,physical=physical,bindings=bindings,
        contracts=h_contracts,diagnostic_only=True,calibration_parameters_updated=False,
        reference_used=False,calibration_accepted=False))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--candidate',required=True,type=Path)
    p.add_argument('--out',required=True,type=Path)
    p.add_argument('--candidate-kind',choices=['progressive','shared'],default='progressive')
    a=p.parse_args();torch.set_num_threads(1);main(a.candidate,a.out,a.candidate_kind)
