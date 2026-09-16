import numpy as np
import pytest
from biospur_fusion.c2_five_calibration.runtime_context import physical_runtime_context


def fixture():
    t=100+np.arange(180)/60
    r=np.tile(np.eye(3),(len(t),24,1,1))
    q=dict(time_s=t,orientation=r[:,:5].copy(),acceleration_mps2=np.zeros((len(t),5,3)),input_valid=np.ones(len(t),bool))
    return q,r,t[:90:3],r[:90:3]


def test_context_preserves_bridge_output_grid_and_checkpoint():
    data,prior,ct,cr=fixture();data['input_valid'][90]=False
    q,out,idx,warm,a=physical_runtime_context(data,prior,ct,cr,101.51)
    np.testing.assert_array_equal(q['time_s'][out],data['time_s'][::3][data['time_s'][::3]>=101.51])
    np.testing.assert_array_equal(q['time_s'][idx],ct[-11:])
    np.testing.assert_array_equal(warm,cr[-11:])
    assert a['bridge_frames']==1 and a['context_frames']==12
    assert not q['valid'][11]
    assert not a['posterior_covariance_transferred'] and not a['context_pose_is_fixed']


def test_rejects_shifted_checkpoint_and_future_leakage():
    data,prior,ct,cr=fixture()
    with pytest.raises(ValueError,match='grid'):
        physical_runtime_context(data,prior,ct+.001,cr,101.51)
    with pytest.raises(ValueError,match='strictly before'):
        physical_runtime_context(data,prior,ct,cr,101.)


def test_mixed_precision_warm_start_does_not_replace_runtime_prior():
    import torch
    from test_c2_joint_kinematics import geometry
    from biospur_fusion.c2_five_calibration.runtime_context import runtime_pose_warm_start
    from biospur_fusion.c2_five_calibration.anatomy import JointModel
    data,prior,ct,cr=fixture()
    q,out,idx,checkpoint,a=physical_runtime_context(data,prior.astype(np.float32),ct,cr,101.51)
    saved=q['prior'].copy()
    warm=runtime_pose_warm_start(q,geometry(),idx,checkpoint)
    expected=JointModel(geometry()).prior_target(torch.tensor(q['prior'],dtype=torch.float64),torch.tensor(q['observed'],dtype=torch.float64)).numpy()
    np.testing.assert_array_equal(warm[idx],checkpoint)
    np.testing.assert_array_equal(warm[out],expected[out])
    np.testing.assert_array_equal(q['prior'],saved)
    assert warm.dtype==np.float64
