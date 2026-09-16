"""Frozen H replay for a completed whole-session C2 candidate (no fitting)."""
import argparse
import json
import os
from pathlib import Path

import numpy as np
import torch

from biospur_fusion.c2_imucoco.workflow import INPUT_RUN, load_input
from biospur_fusion.c2_imucoco.backend import load_pose, ChunkedPoseStream
from biospur_fusion.c2_five_calibration.frontend import prepare, initial_state
from biospur_fusion.c2_five_calibration.workflow import SMPL
from biospur_fusion.c2_five_calibration.soft_observation import solve_soft_observations
from biospur_fusion.c2_sparse_nodes.inputs import NODES, sha


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--candidate',type=Path,required=True)
    parser.add_argument('--continuous-physical-context',action='store_true',
        help='retain raw C2 boundary context and fitted-pose warm start; no posterior covariance is implied')
    args=parser.parse_args();out=args.candidate.resolve()
    result=json.loads((out/'RESULT.json').read_text())
    if not result['completed'] or not result['calibration_energy_improved'] or result['H_used'] or result['reference_used']:
        raise ValueError('completed five-only C2 proposal acceptance required')
    if (out/'H_PRODUCER.json').exists() or (out/'H_REPLAY.npz').exists():
        raise ValueError('preserve existing H evidence')
    files=['RESULT.json','FRONTEND_PROPOSAL.json','GEOMETRY.json','full_C2_proposal_final.npz']
    bindings={name:sha(out/name) for name in files}
    (out/'H_FROZEN_INPUTS.json').write_text(json.dumps(bindings,indent=2))
    frontend=json.loads((out/'FRONTEND_PROPOSAL.json').read_text())
    geometry=json.loads((out/'GEOMETRY.json').read_text())
    with np.load(out/'full_C2_proposal_final.npz') as archive:
        levers=archive['levers']
        checkpoint_time=archive['time_s']
        checkpoint_rotation=archive['rotation']
    # H is first opened after the fixed candidate is bound above.
    cal=load_input(INPUT_RUN/'CALIBRATION_CONTINUOUS_INPUT.npz')
    hold=load_input(INPUT_RUN/'HOLDOUT_CONTINUOUS_INPUT.npz')
    joined={node:dict(imu=np.concatenate((cal['_continuous'][node]['imu'],hold['_continuous'][node]['imu']))) for node in NODES}
    data=prepare(joined,frontend,geometry)
    initial=initial_state(prepare(cal['00_initial_still'],frontend,geometry),geometry)
    torch.set_num_threads(1);cwd=Path.cwd()
    try:poser,_=load_pose(SMPL,out)
    finally:os.chdir(cwd)
    stream=ChunkedPoseStream(poser,initial_global_rotation=initial,sensor_vertices=geometry['sensor_vertices'])
    pred,neural=stream.run(data['features'],input_valid=data['input_valid'],wall_limit_s=700,
        progress=lambda row:print(json.dumps(row),flush=True))
    first_h=max(hold['_continuous'][n]['imu'][0,0] for n in NODES)
    ids=np.arange(0,len(data['time_s']),3);ids=ids[data['time_s'][ids]>=first_h]
    q=dict(time_s=data['time_s'][ids],valid=data['input_valid'][ids],
        observed=data['orientation'][ids],acceleration=data['acceleration_mps2'][ids],
        prior=pred['global_rotation'][ids])
    if args.continuous_physical_context:
        from biospur_fusion.c2_five_calibration.runtime_context import physical_runtime_context,runtime_pose_warm_start
        context,output,indices,checkpoint,context_audit=physical_runtime_context(
            data,pred['global_rotation'],checkpoint_time,checkpoint_rotation,first_h)
        warm=runtime_pose_warm_start(context,geometry,indices,checkpoint)
        rotation,physical=solve_soft_observations(context,geometry,levers,iterations=150,
            wall_limit_s=400,initial_rotation=warm)
        rotation=rotation[output]
        np.testing.assert_array_equal(context['time_s'][output],q['time_s'])
        physical['boundary_context']=context_audit
    else:
        rotation,physical=solve_soft_observations(q,geometry,levers,iterations=150,wall_limit_s=400)
    if bindings!={name:sha(out/name) for name in files}:
        raise ValueError('C2 candidate changed during frozen H replay')
    np.savez_compressed(out/'H_REPLAY.npz',**q,rotation=rotation)
    (out/'H_PRODUCER.json').write_text(json.dumps(dict(completed=True,H_used_for_fit=False,
        reference_used=False,calibration_parameters_updated=False,bindings=bindings,
        raw_H_sha256=sha(INPUT_RUN/'HOLDOUT_CONTINUOUS_INPUT.npz'),
        neural=neural,physical=physical,output_sha256=sha(out/'H_REPLAY.npz')),indent=2))


if __name__=='__main__':main()
