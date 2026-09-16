"""Transport measured pelvis angular rate into a retained sensor's frame.

This removes measured common pelvis motion only. It does not observe an
absent upper arm or isolate elbow motion when shoulder/thorax motion exists.
Heading corrections transport vectors; their time derivatives are not gyro
measurements and are deliberately not added to physical angular rates.
"""
import numpy as np
from scipy.spatial.transform import Rotation,Slerp


def relative_angular_rate(sensor_time,sensor_gyro,sensor_world,
                          pelvis_time,pelvis_gyro,pelvis_world,*,max_gap_s=.025):
    def check(t,w,r):
        t=np.asarray(t,dtype=float);w=np.asarray(w,dtype=float);r=np.asarray(r,dtype=float)
        if (t.ndim!=1 or len(t)<2 or np.any(np.diff(t)<=0) or w.shape!=(len(t),3)
                or r.shape!=(len(t),3,3) or not all(np.isfinite(x).all() for x in (t,w,r))):
            raise ValueError('finite ordered timestamps, gyro vectors and rotations required')
        if not np.allclose(r.swapaxes(-1,-2)@r,np.eye(3),atol=1e-6,rtol=0) or np.any(np.linalg.det(r)<0):
            raise ValueError('proper world-from-sensor rotations required')
        return t,w,r
    t,w,r=check(sensor_time,sensor_gyro,sensor_world)
    pt,pw,pr=check(pelvis_time,pelvis_gyro,pelvis_world)
    if not np.isfinite(max_gap_s) or max_gap_s<=0:raise ValueError('positive maximum sample gap required')
    query=np.clip(t,pt[0],pt[-1])
    right=np.searchsorted(pt,query,side='left').clip(0,len(pt)-1)
    exact=pt[right]==query;left=np.where(exact,right,np.maximum(0,right-1))
    valid=(t>=pt[0])&(t<=pt[-1])&((pt[right]-pt[left])<=max_gap_s)
    interpolated_gyro=np.stack([np.interp(query,pt,pw[:,axis]) for axis in range(3)],axis=1)
    interpolated_world=Slerp(pt-pt[0],Rotation.from_matrix(pr))(query-pt[0]).as_matrix()
    common=np.einsum('nji,njk,nk->ni',r,interpolated_world,interpolated_gyro)
    relative=w-common
    # Invalid rows cannot silently enter a PCA or a likelihood as zero motion.
    relative[~valid]=np.nan;common[~valid]=np.nan
    return relative,common,valid
