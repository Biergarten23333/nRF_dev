import numpy as np
import torch
from test_c2_shared_fit import fixture
from biospur_fusion.c2_five_calibration.calibration_prior import RegisteredHeadingPrior
from biospur_fusion.c2_five_calibration.shared_prior_linearization import shared_prior_jacobian
from biospur_fusion.c2_five_calibration.shared_fit import shared_regularization


def test_shared_prior_jacobian_gives_same_live_energy_gradient_once():
    _,factors,heading,*_=fixture();prior=RegisteredHeadingPrior(factors,heading)
    h=torch.full((4,),.03,dtype=torch.float64,requires_grad=True)
    lever=torch.full((5,3),.01,dtype=torch.float64,requires_grad=True);nominal=torch.zeros_like(lever);delta=torch.zeros(4,dtype=h.dtype)
    J,r=shared_prior_jacobian(prior,delta,h,lever,nominal)
    energy=shared_regularization(prior,delta+h,lever,nominal)
    expected=torch.autograd.grad(energy,(h,lever))
    np.testing.assert_allclose(2*J.T@r,torch.cat([v.flatten() for v in expected]),atol=1e-12)
    np.testing.assert_allclose(r@r,energy.detach(),atol=1e-12)
    assert J.shape[1]==19
