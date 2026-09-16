"""C2-only diagnostic: quaternion increments versus retained measured gyro."""
import json
from pathlib import Path
import numpy as np
from scipy.spatial.transform import Rotation
from biospur_fusion.c2_sparse_nodes.calibration import _gyro_axis,matrices
from biospur_fusion.c2_sparse_nodes.inputs import sha

root=Path('logs/c2_five_overnight_progressive_20260913_180146')
cache=Path('logs/c2_five_node_inertial_rework_20260906_133807/CALIBRATION_CONTINUOUS_INPUT.npz')
if sha(cache)!='0da4a5b6664b52ff83eadd816087cb292e0fd5bd3b0bb1bcc68401ee6ab2dff8':raise ValueError('C2 cache binding changed')
result=[]
with np.load(cache,allow_pickle=False) as z:
 for action,node in [('06_elbow_left','BSFEC35'),('07_elbow_right','BSFB165')]:
  rows=z[f'{action}/{node}/imu'];t=rows[:,0]-rows[0,0];rot=matrices(rows)
  dt=np.diff(t);qrate=(rot[:-1].inv()*rot[1:]).as_rotvec()/dt[:,None]
  gyro=(rows[:-1,8:11]+rows[1:,8:11])/2
  correction=qrate-gyro;world=rot[:-1].apply(correction)
  for lo,hi in [(0,15),(15,30)]:
   valid=(t[:-1]>=lo)&(t[1:]<hi)&(dt>0)&(dt<.025)
   a,_=_gyro_axis(rows,lo,(lo+hi)/2);b,_=_gyro_axis(rows,(lo+hi)/2,hi)
   axis,_=_gyro_axis(rows,lo,hi);direction=rot.apply(np.broadcast_to(axis,(len(rows),3)).copy())
   selected=(t>=lo)&(t<hi);mean=direction[selected].mean(0);mean/=np.linalg.norm(mean)
   cone=np.rad2deg(np.arccos(np.clip(direction[selected]@mean,-1,1)))
   result.append(dict(action=action,phase=[lo,hi],axis_split_acute_deg=float(np.rad2deg(np.arccos(np.clip(abs(a@b),0,1)))),axis_world_cone_p95_deg=float(np.quantile(cone,.95)),
    quaternion_gyro_rate_difference_rms_deg_s=np.rad2deg(np.sqrt(np.mean(correction[valid]**2,axis=0))).tolist(),
    integrated_world_vertical_correction_deg=float(np.rad2deg(np.sum(world[valid,2]*dt[valid]))),
    gyro_speed_p95_deg_s=float(np.rad2deg(np.quantile(np.linalg.norm(gyro[valid],axis=1),.95))),max_dt_s=float(dt.max())))
(root/'C2_ATTITUDE_GYRO_DIAGNOSTIC.json').write_text(json.dumps(dict(H_used=False,reference_used=False,correction_fitted=False,cache_sha256=sha(cache),phases=result),indent=2))
print(json.dumps(result,indent=2))
