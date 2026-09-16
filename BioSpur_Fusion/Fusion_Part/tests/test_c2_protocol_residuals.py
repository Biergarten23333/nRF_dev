import numpy as np
import pytest
import torch
import torch.nn.functional as F
from biospur_fusion.c2_five_calibration.residual_blocks import signed_huber_residual


def parity(energy, blocks, variables):
    exported=sum(v.square().sum() for v in blocks.values())
    torch.testing.assert_close(exported,energy,atol=1e-12,rtol=1e-12)
    got=torch.autograd.grad(exported,variables,retain_graph=True)
    expected=torch.autograd.grad(energy,variables)
    for a,b in zip(got,expected):torch.testing.assert_close(a,b,atol=1e-10,rtol=1e-10)


def test_robust_residual_at_zero_join_and_both_tails():
    x=torch.tensor([-4.,-1.,-.1,0.,.1,1.,4.],dtype=torch.float64,requires_grad=True)
    r=signed_huber_residual(x)
    parity(2*F.smooth_l1_loss(x,torch.zeros_like(x),reduction='sum'),{'r':r},(x,))
    torch.testing.assert_close(torch.autograd.functional.jacobian(signed_huber_residual,x)[3,3],x.new_tensor(1.))
    assert torch.autograd.gradcheck(signed_huber_residual,(x,))


def test_bend_protocol_keeps_all_registered_weights_and_support():
    from test_c2_bend_protocol import inputs
    from biospur_fusion.c2_five_calibration.protocol_pose import build_bend_protocol
    contracts,actions=inputs();tape=build_bend_protocol(contracts,actions)
    p=torch.linspace(0.,3.,601*9,dtype=torch.float64).reshape(601,9).requires_grad_()
    blocks={};energy=sum(tape.energy_for_action(a,p,residual_blocks=blocks) for a in sorted(tape.actions))
    parity(energy,blocks,(p,));assert len(blocks)==4
    for row in tape.rows:assert blocks[f'bend/{row.action}/{row.limb}'].shape==row.index.shape


@pytest.mark.parametrize('temporal', [False,True])
def test_heading_export_keeps_registered_information(temporal):
    from test_c2_shared_fit import fixture
    from biospur_fusion.c2_five_calibration.calibration_prior import RegisteredHeadingPrior
    from biospur_fusion.c2_five_calibration.temporal_parameters import TemporalRegisteredHeadingPrior
    actions,factors,heading,*_=fixture();q=next(iter(actions.values()))
    for rows in factors.values():
        for f in rows:f['measurement_time_s']=.7
    prior=(TemporalRegisteredHeadingPrior(dict(heading_factors=factors,frozen_heading_correction_rad=heading),q['time_s'])
           if temporal else RegisteredHeadingPrior(factors,heading))
    delta=torch.full((30,4) if temporal else (4,),.03,dtype=torch.float64,requires_grad=True)
    blocks={};energy=prior.energy(delta,residual_blocks=blocks)
    parity(energy,blocks,(delta,));assert len(blocks)==len(prior.rows)


@pytest.mark.parametrize('spatial', [False,True])
def test_arm_rows_have_joint_heading_and_torso_derivatives(spatial):
    from test_c2_arm_protocol import fixture
    tape,pose=fixture(np.linspace(0.,.5,10),sensor_error=.2);tape.spatial_axes=spatial
    pose.requires_grad_();delta=torch.full((10,4),.03,dtype=torch.float64,requires_grad=True)
    blocks={};energy=tape.energy_for_action('06_elbow_left',delta,pose,residual_blocks=blocks)
    parity(energy,blocks,(delta,pose))


def test_session_rate_has_no_absolute_information():
    from test_c2_whole_session import session
    from biospur_fusion.c2_sparse_nodes.inputs import NODES
    from biospur_fusion.c2_five_calibration.session_heading import SessionHeadingParameters
    data,contracts=session();model=SessionHeadingParameters(dict(frozen_heading_correction_rad={n:0. for n in NODES[1:]}),data['time_s'],contracts)
    x=torch.linspace(0.,.1,model.size,dtype=torch.float64,requires_grad=True)
    blocks={};energy=model.regularization(x,residual_blocks=blocks);parity(energy,blocks,(x,))
    b={};model.regularization(torch.ones_like(x),residual_blocks=b)
    assert b['heading_rate'].count_nonzero()==0


def test_live_shared_regularizer_export_and_constant_guard():
    from test_c2_shared_fit import fixture
    from biospur_fusion.c2_five_calibration.calibration_prior import RegisteredHeadingPrior
    from biospur_fusion.c2_five_calibration.shared_fit import shared_regularization, LEVER_SIGMA_M
    _,factors,heading,*_=fixture();prior=RegisteredHeadingPrior(factors,heading)
    delta=torch.full((4,),.03,dtype=torch.float64,requires_grad=True)
    lever=torch.full((5,3),.01,dtype=torch.float64,requires_grad=True);nominal=torch.zeros_like(lever)
    b={};energy=shared_regularization(prior,delta,lever,nominal,residual_blocks=b)
    torch.testing.assert_close(energy,prior.energy(delta)+(lever/LEVER_SIGMA_M).square().sum(),atol=0,rtol=0)
    parity(energy,b,(delta,lever))
    with pytest.raises(ValueError,match='separately'):
        shared_regularization(prior,delta,lever,nominal,constant_heading_energy=1.,residual_blocks={})
    torch.testing.assert_close(shared_regularization(prior,delta,lever,nominal,constant_heading_energy=1.),energy+1.)


def test_registered_support_distinguishes_shared_only_from_pose_rows():
    from types import SimpleNamespace
    from test_c2_bend_protocol import inputs
    from biospur_fusion.c2_five_calibration.protocol_pose import build_bend_protocol
    from biospur_fusion.c2_five_calibration.residual_support import calibration_row_support
    contracts,actions=inputs();bend=build_bend_protocol(contracts,actions)
    blocks={'sensor_levers':torch.zeros(5,3),'heading/0':torch.tensor(0.)}
    p=torch.ones(601,9)
    for action in sorted(bend.actions):bend.energy_for_action(action,p,residual_blocks=blocks)
    prior=SimpleNamespace(rows=[0],arm_protocol=None)
    support=calibration_row_support(blocks,prior,bend_protocol=bend)
    assert support['heading/0'] is None and support['sensor_levers'] is None
    key='bend/06_elbow_left/0'
    np.testing.assert_array_equal(support[key].start,np.arange(300,600))
    assert support[key].owned_by(0,400).sum()==100
    blocks.pop(key)
    with pytest.raises(ValueError,match='exactly'):
        calibration_row_support(blocks,prior,bend_protocol=bend)
