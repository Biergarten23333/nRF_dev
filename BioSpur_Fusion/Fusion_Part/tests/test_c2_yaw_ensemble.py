import numpy as np
import pytest
from scipy.spatial.transform import Rotation
from biospur_fusion.c2_imucoco.yaw_ensemble import YAWS_DEG,average_yaw_predictions


def yaw(a):return Rotation.from_euler('y',a,degrees=True).as_matrix()


def test_common_gauge_covariance_and_exact_member_recovery():
    truth=Rotation.random(48,random_state=17).as_matrix().reshape(2,24,3,3)
    members={a:yaw(a)@truth for a in YAWS_DEG}
    mean,audit=average_yaw_predictions(members)
    np.testing.assert_allclose(mean,truth,atol=1e-12)
    assert audit['member_deviation_deg'].max()<1e-10
    # Arbitrary predictions under the four gauges, then cyclicly reindex the
    # same physical members after changing the base gauge by 90 degrees.
    noise=Rotation.from_rotvec(np.random.default_rng(3).normal(size=(4*48,3))*.1).as_matrix().reshape(4,2,24,3,3)
    members={a:yaw(a)@noise[i]@truth for i,a in enumerate(YAWS_DEG)}
    mean,_=average_yaw_predictions(members)
    shifted={a:members[(a+90)%360] for a in YAWS_DEG}
    actual,_=average_yaw_predictions(shifted)
    np.testing.assert_allclose(actual,yaw(90)@mean,atol=1e-12)
    np.testing.assert_allclose(np.linalg.det(mean),1,atol=1e-12)


def test_missing_reflected_and_ambiguous_members_fail_closed():
    r=np.tile(np.eye(3),(2,24,1,1));members={a:yaw(a)@r for a in YAWS_DEG}
    with pytest.raises(ValueError,match='four'):average_yaw_predictions({0:r})
    bad={**members,0:r*np.array([-1,1,1])}
    with pytest.raises(ValueError,match='proper'):average_yaw_predictions(bad)
    with pytest.raises(ValueError,match='ambiguous'):average_yaw_predictions({a:r for a in YAWS_DEG})
