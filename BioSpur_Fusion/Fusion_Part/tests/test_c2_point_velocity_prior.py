import numpy as np
import pytest
import torch
from test_c2_joint_kinematics import geometry
from biospur_fusion.c2_five_calibration.operators import HZ,filtered,apply_stencil,velocity_coefficients
from biospur_fusion.c2_five_calibration.velocity_prior import PointVelocityTrackingPrior
from biospur_fusion.c2_five_calibration.tracking import PoseTrackingPrior


def test_velocity_operator_matches_integrated_arbitrary_samples():
    rng=np.random.default_rng(11);v=rng.normal(size=(80,4,3));p=[rng.normal(size=(4,3))]
    for a,b in zip(v[:-1],v[1:]):p.append(p[-1]+(a+b)/(2*HZ))
    expected=filtered(torch.tensor(np.array(p)),1)
    actual=apply_stencil(torch.tensor(v),velocity_coefficients())
    torch.testing.assert_close(actual,expected,atol=1e-12,rtol=1e-12)


def test_selected_velocity_replaces_prior_without_changing_normalization():
    g=geometry();n=31;prior=torch.eye(3,dtype=torch.float64).repeat(n,24,1,1)
    new=PointVelocityTrackingPrior(prior,g,time_s=np.arange(n)/HZ,
        velocity_mps=np.ones((n,1,3))*.2,point_ids=[6],
        convention='root_relative_world_instantaneous_mps')
    blocks={};p,v,_=new.losses(prior,g,lambda x:filtered(x,1),residual_blocks=blocks)
    assert p==0
    expected=(.2/new.scale[5])**2/23
    torch.testing.assert_close(v,expected)
    torch.testing.assert_close(sum(x.square().sum() for x in blocks.values()),.15*(p+v))
    baseline=PoseTrackingPrior(prior,g)
    positions=baseline.positions.clone().requires_grad_()
    r=new.velocity_error(positions,lambda x:filtered(x,1))
    r.square().sum().backward()
    assert torch.isfinite(positions.grad).all() and positions.grad.abs().max()>0


def test_unverified_channels_and_time_conventions_fail_closed():
    g=geometry();n=31;p=torch.eye(3,dtype=torch.float64).repeat(n,24,1,1)
    kw=dict(time_s=np.arange(n)/HZ,velocity_mps=np.zeros((n,1,3)),point_ids=[6],convention='root_relative_world_instantaneous_mps')
    for points in ([22],[3255],[6,6]):
        with pytest.raises(ValueError,match='unsupported'):PointVelocityTrackingPrior(p,g,**{**kw,'point_ids':points})
    with pytest.raises(ValueError,match='convention'):PointVelocityTrackingPrior(p,g,**{**kw,'convention':'forward_interval'})
    with pytest.raises(ValueError,match='20 Hz'):PointVelocityTrackingPrior(p,g,**{**kw,'time_s':np.arange(n)/60})
