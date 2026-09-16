"""Five-only C2 diagnostic of body-turn support, never a torso measurement."""
import json
from pathlib import Path
import numpy as np
from scipy.spatial.transform import Rotation
from biospur_fusion.c2_sparse_nodes.inputs import NODES,sha
from biospur_fusion.c2_sparse_nodes.calibration import matrices

CACHE=Path('logs/c2_five_node_inertial_rework_20260906_133807/CALIBRATION_CONTINUOUS_INPUT.npz')
OUT=Path('logs/c2_five_continuation_20260914_0540')

def heading_delta(rotation, baseline):
    # World relative rotation, z twist; unlike Euler yaw this is mount invariant.
    q=(rotation*baseline.inv()).as_quat()
    support=np.hypot(q[:,2],q[:,3])
    angle=(2*np.arctan2(q[:,2],q[:,3])+np.pi)%(2*np.pi)-np.pi
    return np.rad2deg(angle),support

def main():
    binding=sha(CACHE)
    if binding!='0da4a5b6664b52ff83eadd816087cb292e0fd5bd3b0bb1bcc68401ee6ab2dff8':
        raise ValueError('C2 input binding changed')
    report=[]
    with np.load(CACHE,allow_pickle=False) as z:
        # Last2seconds of T-pose is only a within-sensor orientation reference.
        baselines={}
        for node in NODES:
            a=z[f'02_t_pose/{node}/imu']
            baselines[node]=matrices(a[a[:,0]>=a[-1,0]-2]).mean()
        for action in ['04_shoulder_left','05_shoulder_right','06_elbow_left','07_elbow_right']:
            for node in NODES:
                a=z[f'{action}/{node}/imu'];t=a[:,0]-a[0,0];r=matrices(a)
                delta,support=heading_delta(r,baselines[node]);bins=[]
                for lo in range(0,30,5):
                    m=(t>=lo)&(t<lo+5)
                    if not m.any():continue
                    v=np.exp(1j*np.deg2rad(delta[m]));mean=v.mean()
                    bins.append(dict(interval_s=[lo,lo+5],world_z_twist_from_Tpose_deg=float(np.rad2deg(np.angle(mean))),
                        circular_concentration=float(abs(mean)),twist_support_min=float(support[m].min()),
                        gyro_speed_median_deg_s=float(np.rad2deg(np.median(np.linalg.norm(a[m,8:11],axis=1))))))
                change=(r[0].inv()*r).magnitude()
                report.append(dict(action=action,node=node,bins=bins,within_action_rotation_p95_deg=float(np.rad2deg(np.quantile(change,.95)))))
    result=dict(cache_sha256=binding,H_used=False,ten_used=False,calibration_changed=False,
        caveat='Per-sensor world twist, not anatomical torso heading. Arm articulation and attitude drift can both change this statistic.',rows=report)
    (OUT/'INACTIVE_NODE_TURN.json').write_text(json.dumps(result,indent=2,allow_nan=False))
    for a in report:
        print(a['action'],a['node'],[round(x['world_z_twist_from_Tpose_deg'],1) for x in a['bins']],round(a['within_action_rotation_p95_deg'],1))

if __name__=='__main__':main()
