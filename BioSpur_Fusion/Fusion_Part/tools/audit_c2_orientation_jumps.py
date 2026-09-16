"""Separate measured IMU steps, calibration-state steps and axial-frame steps."""
import argparse
import json
from pathlib import Path
import numpy as np
from scipy.spatial.transform import Rotation
from biospur_fusion.c2_coupled_progressive.contracts import NODE_TO_SEGMENT
from biospur_fusion.c2_coupled_progressive.estimator import interp_quat_wxyz
from tools.build_c2_continuous_pose import matrix


def steps(r):
    return np.degrees(Rotation.from_matrix(np.swapaxes(r[:-1],1,2)@r[1:]).magnitude())


def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--pose',type=Path,required=True)
    ap.add_argument('--previous',type=Path)
    ap.add_argument('--frontend',type=Path,required=True); ap.add_argument('--output',type=Path,required=True)
    a=ap.parse_args(); a.output.mkdir(parents=True,exist_ok=False)
    with np.load(a.pose/'ROTATIONS.npz') as z:
        t=z['time_s']; names=z['segment_names'].tolist(); post=z['base_segment_rotations_world']
    with np.load(a.pose/'PRE_IK_ROTATIONS.npz') as z:
        np.testing.assert_array_equal(t,z['time_s']); pre=z['base_segment_rotations_world']
    report={}
    for node,seg in NODE_TO_SEGMENT.items():
        with np.load(a.frontend/f'{node}.npz') as src:
            raw=matrix(interp_quat_wxyz(src['common_global_ns'].astype(float)*1e-9,
                                       src['quat_vqf_sensor_wxyz'],t))
        raw_step=steps(raw); pre_step=steps(pre[:,names.index(seg)]); post_step=steps(post[:,names.index(seg)])
        selected=np.unique(np.r_[np.argsort(pre_step)[-3:],np.argsort(post_step)[-3:]])
        report[seg]={'max_raw_deg':float(raw_step.max()),'max_pre_ik_deg':float(pre_step.max()),
                     'max_anatomical_deg':float(post_step.max()), 'events':[
                         dict(frame=int(i+1),time_s=float(t[i+1]-t[0]),dt_s=float(t[i+1]-t[i]),raw_deg=float(raw_step[i]),
                              calibrated_deg=float(pre_step[i]),anatomical_deg=float(post_step[i])) for i in selected]}
    (a.output/'JUMPS.json').write_text(json.dumps(report,indent=2)); print(json.dumps(report,indent=2))
    if a.previous:
        with np.load(a.previous/'POSE.npz') as old, np.load(a.pose/'POSE.npz') as new:
            np.testing.assert_array_equal(old['time_s'],new['time_s'])
            np.testing.assert_array_equal(old['joint_names'],new['joint_names'])
            dt=np.diff(new['time_s']); adjacent=(dt>0)&(dt<.0075)
            comparison={'scope':'relative FK endpoints, not world slip or positioning accuracy',
                        'adjacent_rows':int(adjacent.sum()),'excluded_long_intervals':int((~adjacent).sum()),
                        'peak_adjacent_endpoint_step_m':{label:float(np.linalg.norm(
                            np.diff(z['joints_relative'],axis=0),axis=2)[adjacent].max())
                            for label,z in [('previous',old),('candidate',new)]}}
        (a.output/'ENDPOINT_STEP_COMPARISON.json').write_text(json.dumps(comparison,indent=2))


if __name__=='__main__':main()
