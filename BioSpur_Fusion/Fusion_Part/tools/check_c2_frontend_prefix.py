#!/usr/bin/env python3
"""C2-only prefix assembly using the existing raw-derived five-only archive.

Read action keys only as they arrive; never open the full continuous array or
any fitted frontend/prior/H. Verify final mounts against fresh raw frame prefix.
"""
import argparse
import json
from pathlib import Path

import numpy as np

from biospur_fusion.c2_sparse_nodes.inputs import ROOT,NODES,sha
from biospur_fusion.c2_coupled_progressive.contracts import EPISODES
from biospur_fusion.c2_five_calibration.frontend import fit_frontend


def main(out, reference):
    out.mkdir(parents=True,exist_ok=False)
    archive=ROOT/'logs/c2_five_node_inertial_rework_20260906_133807/CALIBRATION_CONTINUOUS_INPUT.npz'
    surface=ROOT/'config/body_calibration_v4_1/v47_subject_surface_anthropometry_20260828.json'
    contract=dict(kind='ARRIVED_PREFIX_FRONTEND_ASSEMBLY_CHECK',H_used=False,ten_node_used=False,
        archive_sha256=sha(archive),surface_sha256=sha(surface),continuous_key_opened=False,
        clock_scope='existing offline transport clock mapping, not online synchronization proof')
    (out/'CONTRACT.json').write_text(json.dumps(contract,indent=2))
    episodes={};history=[]
    with np.load(archive,allow_pickle=False) as z:
        expected={f'{a}/{n}/imu' for a in (*EPISODES,'_continuous') for n in NODES}
        if set(z.files)!=expected:raise ValueError('archive is not the complete declared five-only C2 archive')
        for action in EPISODES:
            episodes[action]={n:{'imu':z[f'{action}/{n}/imu']} for n in NODES}
            if '14_trunk_flex_extend' not in episodes:continue
            calibration=fit_frontend(episodes,json.loads(surface.read_text()),prefix=True)
            (out/(action+'_FRONTEND.json')).write_text(json.dumps(calibration,indent=2))
            entry=dict(last_action=action,fit_actions=calibration['fit_actions'],
                last_heading_action={n:calibration['heading_factors'][n][-1]['action'] for n in NODES[1:]},
                complete=calibration['complete_recorded_C2'])
            history.append(entry)
            print(action,entry['last_heading_action'],flush=True)
    fresh=json.loads(reference.read_text())['final']['frames']
    differences={n:float(np.max(np.abs(np.asarray(calibration['segment_axes_in_sensor'][i])-
                                      np.asarray(fresh[n]['mount'])))) for i,n in enumerate(NODES)}
    if any(v>1e-10 for v in differences.values()):raise ValueError('cached/fresh raw frame result differs')
    (out/'RESULT.json').write_text(json.dumps(dict(completed=True,history=history,
        fresh_raw_mount_max_element_difference=differences,full_pose_calibration=False),indent=2))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--out',required=True,type=Path)
    p.add_argument('--reference',required=True,type=Path)
    a=p.parse_args();main(a.out,a.reference)
