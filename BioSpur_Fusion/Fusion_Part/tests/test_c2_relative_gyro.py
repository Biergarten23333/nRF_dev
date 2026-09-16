import numpy as np
import pytest
from scipy.spatial.transform import Rotation
from biospur_fusion.c2_five_calibration.relative_gyro import relative_angular_rate


def test_common_motion_removed_and_relative_component_preserved():
    t=np.arange(100)*.005
    root=Rotation.from_rotvec(np.column_stack((t*0,t*0,t))).as_matrix()
    mount=Rotation.from_rotvec([.3,-.2,.5]).as_matrix()
    sensor=root@mount
    pelvis=np.tile([0.,0.,1.],(len(t),1))
    common=np.einsum('ji,nj->ni',mount,pelvis)
    relative=np.tile([.2,.4,-.1],(len(t),1))
    out,removed,valid=relative_angular_rate(t,common+relative,sensor,t,pelvis,root)
    assert valid.all();np.testing.assert_allclose(out,relative,atol=1e-12)
    np.testing.assert_allclose(removed,common,atol=1e-12)
    world=Rotation.from_rotvec([-.4,.1,.2]).as_matrix()
    other,_,_=relative_angular_rate(t,common+relative,world@sensor,t,pelvis,world@root)
    np.testing.assert_allclose(other,out,atol=1e-12)


def test_missing_pelvis_interval_and_extrapolation_are_excluded():
    t=np.arange(20)*.005;r=np.tile(np.eye(3),(len(t),1,1));w=np.ones((len(t),3))
    keep=np.r_[np.arange(2,5),np.arange(12,18)]
    out,common,valid=relative_angular_rate(t,w,r,t[keep],w[keep],r[keep])
    assert not valid[:2].any() and not valid[5:12].any() and not valid[18:].any()
    assert np.isnan(out[~valid]).all() and np.isnan(common[~valid]).all()
    np.testing.assert_allclose(out[valid],0,atol=1e-12)
    with pytest.raises(ValueError,match='ordered'):
        relative_angular_rate(t[::-1],w,r,t,w,r)
