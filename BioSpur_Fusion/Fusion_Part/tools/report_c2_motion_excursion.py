"""Saved-output motion statistics; displacement is not ground-truth drift."""
import json
from pathlib import Path
import numpy as np


def metrics(t, p):
    first = p[t <= t[0] + 3].mean(axis=0)
    last = p[t >= t[-1] - 3].mean(axis=0)
    delta = last - first
    return dict(duration_s=float(t[-1]-t[0]), samples=len(t),
                endpoint_window_s=3,
                endpoint_mean_shift_m=float(np.linalg.norm(delta)),
                endpoint_mean_shift_xyz_m=delta.tolist(),
                horizontal_mean_shift_m=float(np.linalg.norm(delta[:2])),
                maximum_excursion_from_initial_mean_m=float(np.linalg.norm(p-first,axis=1).max()),
                p95_radius_about_segment_mean_m=float(np.percentile(np.linalg.norm(p-p.mean(axis=0),axis=1),95)))


if __name__ == '__main__':
    root=Path('logs/c2_root_feedback_repair_20260912T121500Z')
    regions=json.loads(Path('logs/c2_full_continuous_ab_20260912/POSE_WORLD.json').read_text())['regions']
    with np.load(root/'publication/CONTINUOUS_AB.npz',allow_pickle=False) as z:
        t=z['time_s']; names={'A_pure_imu':'roots_a','B_posterior':'roots_b_posterior','B_published':'roots_b'}
        report={'scope':'ALL_REGIONS_SAVED_OUTPUT_NOT_TRUTH_ERROR',
                'warning':'Only initial_still is interpreted as quasi-static diagnostic; natural sway remains. Other regions include actual motion. No estimator rerun.',
                'full':{k:metrics(t,z[v]) for k,v in names.items()},'regions':[]}
        for r in regions:
            mask=(t>=r['start_ns']*1e-9)&(t<r['stop_ns']*1e-9)
            if mask.sum()<2: continue
            report['regions'].append({'id':r['region_id'],'action':r['action_id'],'kind':r['kind'],
                                      **{k:metrics(t[mask],z[v][mask]) for k,v in names.items()}})
    output=root/'MOTION_EXCURSION.json'
    output.write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({'full':report['full'],'initial_still':report['regions'][0],'region_count':len(report['regions'])}))
