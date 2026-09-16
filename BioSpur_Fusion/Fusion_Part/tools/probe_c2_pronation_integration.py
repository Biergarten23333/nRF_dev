"""Read-only C2 gyro-integration discriminant; not a pose estimator."""
import json
from pathlib import Path
import numpy as np
from scipy.spatial.transform import Rotation
from biospur_fusion.c2_sparse_nodes.calibration import _gyro_axis,matrices
from biospur_fusion.c2_sparse_nodes.inputs import sha
r=Path('logs/c2_five_overnight_progressive_20260913_180146')
p=Path('logs/c2_five_node_inertial_rework_20260906_133807/CALIBRATION_CONTINUOUS_INPUT.npz')
assert sha(p)=='0da4a5b6664b52ff83eadd816087cb292e0fd5bd3b0bb1bcc68401ee6ab2dff8'
report=[]
with np.load(p,allow_pickle=False) as z:
 for action,node in [('06_elbow_left','BSFEC35'),('07_elbow_right','BSFB165')]:
  a=z[f'{action}/{node}/imu'];t=a[:,0]-a[0,0];v=matrices(a);axis,_=_gyro_axis(a,15,30)
  if (v[0].apply(axis))[2]>0:axis=-axis
  modes={}
  for mode in ['trapezoidal','current']:
   q=v[0];rot=[q.as_quat()]
   for i,dt in enumerate(np.diff(t)):
    omega=(a[i,8:11]+a[i+1,8:11])/2 if mode=='trapezoidal' else a[i+1,8:11]
    q=q*Rotation.from_rotvec(omega*dt);rot.append(q.as_quat())
   modes[mode]=Rotation.from_quat(np.array(rot))
  curves={k:x.apply(np.tile(axis,(len(t),1))) for k,x in dict(VQF=v,**modes).items()}
  bins=[]
  for lo in range(14,30,2):
   m=(t>=lo)&(t<lo+2)
   values={}
   for name,d in curves.items():
    mean=d[m].mean(0);mean/=np.linalg.norm(mean)
    values[name]=dict(azimuth_deg=float(np.rad2deg(np.arctan2(mean[1],mean[0]))),elevation_deg=float(np.rad2deg(np.arcsin(mean[2]))))
   perp=a[m,8:11]-np.outer(a[m,8:11]@axis,axis)
   bins.append(dict(interval=[lo,lo+2],direction=values,accel_norm_median=float(np.median(np.linalg.norm(a[m,5:8],axis=1))),offaxis_gyro_rms_deg_s=float(np.rad2deg(np.sqrt(np.mean(np.sum(perp**2,axis=1)))))))
  report.append(dict(action=action,bins=bins,orientation_difference_p95_deg={k:float(np.rad2deg(np.quantile((v.inv()*x).magnitude(),.95))) for k,x in modes.items()}))
(r/'C2_PRONATION_INTEGRATION.json').write_text(json.dumps(dict(H_used=False,reference_used=False,calibration_changed=False,actions=report),indent=2))
print(json.dumps(report,indent=2))
