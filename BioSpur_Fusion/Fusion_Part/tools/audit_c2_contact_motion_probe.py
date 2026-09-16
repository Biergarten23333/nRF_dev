"""Compare final world-space motion, including contact transitions and tails."""
import argparse
import json
from pathlib import Path
import numpy as np


def audit(folder):
    with np.load(folder/'PROBE.npz') as d:
        t = d['time_s']-d['time_s'][0]; dt = np.diff(t)
        names = d['joint_names'].tolist()
        feet = [names.index('ankle_'+s) for s in ('left', 'right')]
        stationary = d['stationary'].astype(bool)
        data = {s: (d['roots_'+s].copy(), d['pose_'+s].copy()) for s in ('baseline','candidate')}
    windows = [(0,4.04), (4.04, min(34.06,float(t[-1]))), (13.6,14.1),
               (14.1,15.1), (20.5,21.4), (21.4,23.4), (52.1,52.8), (52.8,54.8), (0,float(t[-1]))]
    rows = []
    for lo, hi in windows:
        mask = (t[:-1]>=lo)&(t[1:]<=hi)&(dt<=.0075)
        if not mask.any(): continue
        row = {'window_s':[lo,hi], 'complete':float(t[-1])>=hi}
        for label, (root, pose) in data.items():
            motion = root[:,None]+pose
            step = np.linalg.norm(np.diff(motion,axis=0),axis=2)
            rootstep = np.linalg.norm(np.diff(root,axis=0),axis=1)
            footstep = step[:,feet]
            st = stationary[:-1]&stationary[1:]&mask[:,None]
            row[label] = dict(root_max_step_m=float(rootstep[mask].max()),
                root_path_m=float(rootstep[mask].sum()),
                root_max_speed_m_s=float((rootstep/dt)[mask].max()),
                all_joint_max_step_m=float(step[mask].max()),
                feet_max_step_m=footstep[mask].max(axis=0).tolist(),
                stationary_feet_path_m=[float(footstep[:,j][st[:,j]].sum()) for j in range(2)],
                root_extent_m=np.ptp(root[np.r_[mask,False]|np.r_[False,mask]],axis=0).tolist())
        rows.append(row)
    p0,p1=data['baseline'][1],data['candidate'][1]
    keep=[i for i,n in enumerate(names) if n not in ('knee_left','knee_right','ankle_left','ankle_right')]
    length_error=[]
    for side in ('left','right'):
        for a,b in [('hip_','knee_'),('knee_','ankle_')]:
            x,y=names.index(a+side),names.index(b+side)
            length_error.append(float(np.max(abs(np.linalg.norm(p0[:,x]-p0[:,y],axis=1)-np.linalg.norm(p1[:,x]-p1[:,y],axis=1)))))
    report=dict(scope='REAL_PREFIX_DIAGNOSTIC_NOT_FULL_CALIBRATION',windows=rows,
                upper_body_relative_max_change_m=float(np.max(np.linalg.norm(p1[:,keep]-p0[:,keep],axis=2))),
                limb_length_max_change_m=max(length_error),
                correction_max_m=float(np.max(np.linalg.norm(p1-p0,axis=2))),
                raw_estimator_changed=False)
    (folder/'MOTION_AUDIT.json').write_text(json.dumps(report,indent=2))
    print(json.dumps(report,indent=2))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('folder',type=Path)
    audit(parser.parse_args().folder)
