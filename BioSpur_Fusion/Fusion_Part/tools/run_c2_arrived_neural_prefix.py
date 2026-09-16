#!/usr/bin/env python3
"""Fresh raw C2 prefix -> measured geometry -> fresh published recurrence.

This is calibration-prefix inference only. It never opens H or a previously
fitted frontend/neural tape. The current prefix may be reinterpreted using its
arrived functional axes; earlier online snapshots are not rewritten.
"""
import argparse
import json
from pathlib import Path
import time

import numpy as np
import torch

from biospur_fusion.c2_sparse_nodes.inputs import ROOT,NODES,FiveNodeFrontend,episode_contracts,sha
from biospur_fusion.c2_five_calibration.frontend import fit_frontend
from biospur_fusion.c2_five_calibration.geometry import subject_geometry
from biospur_fusion.c2_five_calibration.placement import C2_VERTICES,PLACEMENT_EVIDENCE
from biospur_fusion.c2_five_calibration.replay import replay_prior
from biospur_fusion.c2_five_calibration.workflow import fingerprint,SMPL
from biospur_fusion.c2_imucoco.workflow import SURFACE
from biospur_fusion.c2_imucoco.backend import load_pose
from biospur_fusion.c2_imucoco.upstream import verify_assets


def main(out, last_action):
    out=out.resolve();out.mkdir(parents=True,exist_ok=False);start=time.monotonic()
    contracts=episode_contracts();names=list(contracts)
    if last_action not in names or names.index(last_action)<names.index('14_trunk_flex_extend'):
        raise ValueError('prefix must include all retained functional frames through14')
    contracts={n:contracts[n] for n in names[:names.index(last_action)+1]}
    sources=fingerprint();sources[str(Path(__file__).resolve())]=sha(Path(__file__).resolve())
    assets=verify_assets()
    (out/'CONTRACT.json').write_text(json.dumps(dict(kind='ARRIVED_REAL_NEURAL_PREFIX',
        last_action=last_action,actions=list(contracts),source_sha256=sources,
        surface_sha256=sha(SURFACE),smpl_sha256=sha(SMPL),model_assets=assets,
        H_used=False,ten_node_used=False,neural_prior_reused=False,
        full_pose_calibration=False,wall_limit_s=600),indent=2))
    episodes,audit=FiveNodeFrontend().read(contracts,keep_continuous=True)
    # Reader decodes postroll; these forward VQF outputs are not used by the
    # current prefix's frontend or recurrence. Crop per-node before preparation.
    for n in NODES:
        a=episodes['_continuous'][n]['imu']
        episodes['_continuous'][n]['imu']=a[a[:,0]<=episodes[last_action][n]['imu'][-1,0]]
    (out/'INPUT_AUDIT.json').write_text(json.dumps(audit,indent=2))
    surface=json.loads(SURFACE.read_text())
    calibration=fit_frontend(episodes,surface,prefix=True)
    (out/'FRONTEND.json').write_text(json.dumps(calibration,indent=2))
    poser,body=load_pose(SMPL,out)
    geometry=subject_geometry(body,surface,sensor_vertices=C2_VERTICES)
    geometry['placement_evidence']=PLACEMENT_EVIDENCE
    (out/'GEOMETRY.json').write_text(json.dumps(geometry,indent=2))
    remaining=600-(time.monotonic()-start)
    if remaining<=0:raise TimeoutError('input consumed prefix budget')
    data,neural,initial=replay_prior(episodes,calibration,geometry,poser,prefix=True,
        wall_limit_s=remaining,progress=lambda x:print(json.dumps(x),flush=True))
    np.savez_compressed(out/'PREFIX_PRIOR.npz',**data)
    (out/'INITIAL_STATE.json').write_text(json.dumps(initial.tolist()))
    (out/'NEURAL_AUDIT.json').write_text(json.dumps(neural,indent=2))
    for name,value in sources.items():
        if sha(ROOT/name)!=value:raise ValueError('source changed during neural prefix')
    (out/'RESULT.json').write_text(json.dumps(dict(completed=True,last_action=last_action,
        frames=len(data['time_s']),valid_frames=int(data['valid'].sum()),
        elapsed_seconds=time.monotonic()-start,output_sha256=sha(out/'PREFIX_PRIOR.npz'),
        calibration_accepted=False,pose_refinement_run=False),indent=2))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--out',required=True,type=Path)
    p.add_argument('--last-action',default='14_trunk_flex_extend')
    a=p.parse_args();torch.set_num_threads(1);main(a.out,a.last_action)
