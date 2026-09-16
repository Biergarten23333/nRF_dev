import numpy as np
import torch
from test_c2_shared_fit import fixture,geometry
from biospur_fusion.c2_five_calibration.solver import PoseObjective
from biospur_fusion.c2_five_calibration.soft_observation import SoftObservationObjective
from biospur_fusion.c2_five_calibration.calibration_prior import RegisteredHeadingPrior
from biospur_fusion.c2_five_calibration.joint_residuals import JointResidualObjective
from biospur_fusion.c2_five_calibration.shared_orientation import transport_heading
from biospur_fusion.c2_five_calibration.shared_fit import LEVER_SIGMA_M


def test_full_surrogate_energy_and_pose_heading_lever_gradients_match_live_equation():
    torch.set_num_threads(1);actions,factors,heading,*_=fixture();q=next(iter(actions.values()))
    obj=SoftObservationObjective(PoseObjective(**q,geometry=geometry()))
    prior=RegisteredHeadingPrior(factors,heading);nominal=torch.zeros(5,3,dtype=torch.float64)
    joint=JointResidualObjective(obj,prior,torch.zeros(4,dtype=torch.float64),nominal,projection_gap=True)
    p=obj.initial.clone().requires_grad_();h=torch.full((4,),.02,dtype=p.dtype,requires_grad=True)
    lever=torch.full((5,3),.01,dtype=p.dtype,requires_grad=True)
    rotation,energy,blocks=joint.evaluate(p,h,lever)
    observed,acceleration=transport_heading(obj.observed,obj.acceleration,h)
    rr,terms=obj.evaluate(p,lever,observed=observed,acceleration=acceleration,refresh_projection=True,projection_gap=True)
    expected=terms['loss']+prior.energy(h)+(lever/LEVER_SIGMA_M).square().sum()
    exported=sum(v.square().sum() for v in blocks.values())
    torch.testing.assert_close(rotation,rr);torch.testing.assert_close(energy,expected,atol=1e-12,rtol=1e-12)
    torch.testing.assert_close(exported,expected,atol=1e-12,rtol=1e-12)
    a=torch.autograd.grad(exported,(p,h,lever),retain_graph=True);b=torch.autograd.grad(expected,(p,h,lever))
    for x,y in zip(a,b):torch.testing.assert_close(x,y,atol=1e-9,rtol=1e-10)
    assert torch.isfinite(a[1]).all() and a[1].abs().sum()>0
