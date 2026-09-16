"""Matched estimator-coordinate comparison; no display correction or alignment."""
import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
from build_c2_full_continuous_ab_viewer import build


def compare(previous, candidate, labels=None):
    paths=[previous/'CONTINUOUS_AB.npz',candidate/'CONTINUOUS_AB.npz']
    with np.load(paths[0]) as left,np.load(paths[1]) as right:
        for key in ('time_s','source_strobe_us','source_sequence','source_range_mm','source_valid_mask','uwb_node','joint_names'):
            np.testing.assert_array_equal(left[key],right[key],err_msg=key)
        arrays=[{k:np.array(z[k]) for k in ('time_s','roots_b','joints_relative_b','joint_names',
            'segment_correction_rotvec_b','raw_range_residual_m','anchors_world_m','selected_valid_mask')} for z in (left,right)]
    metrics=[]
    for z in arrays:
        root=z['roots_b'];pose=z['joints_relative_b'];world=root[:,None]+pose
        step=np.linalg.norm(np.diff(root,axis=0),axis=1)
        feet={}
        for side in ('left','right'):
            p=world[:,list(z['joint_names']).index('ankle_'+side)]
            d=np.linalg.norm(np.diff(p,axis=0),axis=1)
            feet[side]=dict(path_m=float(d.sum()),peak_step_m=float(d.max()))
        metrics.append(dict(root_peak_step_m=float(step.max()),root_path_m=float(step.sum()),
            root_peak_excursion_m=float(np.linalg.norm(root-root[0],axis=1).max()),feet=feet,
            maximum_attitude_correction_deg=float(np.rad2deg(np.linalg.norm(z['segment_correction_rotvec_b'],axis=-1).max())),
            median_absolute_range_residual_m=float(np.nanmedian(abs(z['raw_range_residual_m'])))))
    output=candidate/'comparison';output.mkdir(exist_ok=False)
    report=dict(scope='MATCHED_SHORT_REAL_ESTIMATOR_COMPARISON_NOT_FULL_CALIBRATION',
        source_sha256=[hashlib.sha256(p.read_bytes()).hexdigest() for p in paths],
        samples=len(arrays[0]['time_s']),duration_s=float(np.ptp(arrays[0]['time_s'])),
        selected_mask_changes=int(np.count_nonzero(arrays[0]['selected_valid_mask']!=arrays[1]['selected_valid_mask'])),
        metrics=dict(previous=metrics[0],candidate=metrics[1]),scientific_pass=False)
    (output/'METRICS.json').write_text(json.dumps(report,indent=2))
    np.savez_compressed(output/'VIEW.npz',time_s=arrays[0]['time_s'],
        roots_a=arrays[0]['roots_b'],roots_b=arrays[1]['roots_b'],
        joints_relative=arrays[0]['joints_relative_b'],joints_relative_b=arrays[1]['joints_relative_b'],
        joint_names=arrays[0]['joint_names'],anchors_world_m=arrays[0]['anchors_world_m'])
    (output/'ACTIONS.json').write_text('[]')
    (output/'SUMMARY.json').write_text(json.dumps(dict(
        comparison_labels=labels or [previous.name,candidate.name],
        viewer_policy='同一连续窗口的真实求解坐标对照；左右区别见标题。无显示滤波或轨迹平移，不是全体校准验收。',
        scientific_pass=False),ensure_ascii=False))
    build(output/'VIEW.npz',output/'ACTIONS.json',output/'COMPARISON.html',summary_path=output/'SUMMARY.json')
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig,axes=plt.subplots(2,1,figsize=(10,6),sharex=True)
    for z,label in zip(arrays,('previous','candidate')):
        t=z['time_s']-z['time_s'][0];root=z['roots_b']
        axes[0].plot(t,1000*np.linalg.norm(root-root[0],axis=1),label=label)
        axes[1].plot(t[1:],1000*np.linalg.norm(np.diff(root,axis=0),axis=1),label=label)
    axes[0].set_ylabel('Root excursion (mm)');axes[1].set_ylabel('Native root step (mm)')
    axes[1].set_xlabel('Time (s)');axes[0].legend();fig.tight_layout()
    fig.savefig(output/'COORDINATES.png');plt.close(fig)
    print(json.dumps(report,indent=2))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('previous',type=Path);parser.add_argument('candidate',type=Path)
    args=parser.parse_args();compare(args.previous,args.candidate)
