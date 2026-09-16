#!/usr/bin/env python3
"""Check frozen calibration against raw orientation increments on real C2 data."""
import argparse
import copy
import json
from pathlib import Path
import numpy as np
from scipy.spatial.transform import Rotation
from biospur_fusion.c2_imucoco.workflow import INPUT_RUN,load_input,write
from biospur_fusion.c2_sparse_nodes.calibration import matrices,relative
from biospur_fusion.c2_sparse_nodes.inputs import NODES,sha


def audit(out):
    target=out/'FROZEN_FRAME_AUDIT.json'
    if target.exists(): raise ValueError('preserve previous frame audit')
    c=json.loads((out/'FRONTEND.json').read_text())
    if c.get('heading_replay_mode')!='FROZEN_AFTER_ALL_RECORDED_C2':
        raise ValueError('frozen calibration frame required')
    legacy=copy.deepcopy(c);legacy.pop('frozen_heading_correction_rad')
    data=load_input(INPUT_RUN/'CALIBRATION_CONTINUOUS_INPUT.npz')['_continuous']
    report={}
    for node in NODES:
        rows=data[node]['imu'];dt=np.diff(rows[:,0])
        def rate(r):
            return np.rad2deg(Rotation.from_matrix(np.swapaxes(r[:-1],1,2)@r[1:]).magnitude())/dt
        raw=rate(matrices(rows).as_matrix());old=rate(relative(rows,node,legacy));new=rate(relative(rows,node,c))
        errors=abs(new-raw);ids=np.flatnonzero(abs(old-raw)>1.)
        if errors.max()>1e-8: raise ValueError('calibration manufactured rotation: '+node)
        report[node]=dict(raw_rows=len(rows),legacy_jump_rows=len(ids),
            legacy_max_extra_rate_deg_s=float(max(abs(old-raw))),
            frozen_max_rate_difference_deg_s=float(errors.max()),
            events=[dict(time_s=float(rows[i+1,0]),raw_rate_deg_s=float(raw[i]),
                legacy_rate_deg_s=float(old[i]),frozen_rate_deg_s=float(new[i])) for i in ids])
    write(target,dict(status='RAW_MOTION_INCREMENT_INVARIANT_PASSED',nodes=report,
        frontend_sha256=sha(out/'FRONTEND.json'),source_sha256=sha(Path(__file__)),
        time_shift_fitted=False,ten_node_data_used=False,H_data_used=False,
        limitation='Fixes injected calibration-update jumps, not a proof of absolute heading or pose accuracy.'))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--out',type=Path,required=True)
    audit(p.parse_args().out.resolve())
