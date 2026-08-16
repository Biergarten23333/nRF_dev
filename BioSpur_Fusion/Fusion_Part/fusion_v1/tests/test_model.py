import numpy as np
from fusion_v1.model.geometry import transform,point,interpolate_pose
from fusion_v1.model.skeleton import ten_segment_topology
from fusion_v1.estimation.robust import cauchy_weight,PairHealth

L={"trunk":.5,"shoulder_half_width":.2,"upper_arm_L":.3,"upper_arm_R":.31,
   "hip_half_width":.1,"thigh_L":.45,"thigh_R":.46}

def test_fk_segment_lengths_are_capture_constants():
    s=ten_segment_topology(L); poses=s.forward(transform((0,0,0),(1,2,3)),{})
    assert np.isclose(np.linalg.norm(poses["Forearm_L"][:3,3]-poses["UpperArm_L"][:3,3]),.3)
    assert np.isclose(np.linalg.norm(poses["Shank_R"][:3,3]-poses["Thigh_R"][:3,3]),.46)
def test_pose_interpolation_is_asynchronous_and_bounded():
    a=transform((0,0,0),(0,0,0)); b=transform((0,0,np.pi),(2,0,0)); m=interpolate_pose(0,a,2,b,.5)
    assert np.allclose(m[:3,3],[.5,0,0]) and np.allclose(point(m,[1,0,0])[:2],[1.20710678,.70710678])
def test_robust_loss_downweights_large_residual(): assert cauchy_weight(10)<cauchy_weight(1)
def test_health_falls_fast_and_recovers_slowly():
    h=PairHealth(); before=h.update(10); after=h.update(0)
    assert before==.65 and after==.67
