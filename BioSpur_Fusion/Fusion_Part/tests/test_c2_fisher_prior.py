import numpy as np
import pytest
import torch
from scipy.spatial.transform import Rotation
from biospur_fusion.c2_five_calibration.fisher_prior import FrozenFisherPrior


def body():
    return torch.eye(3,dtype=torch.float64).repeat(2,24,1,1)


def test_directional_confidence_and_mask():
    p = torch.diag(torch.tensor([10.,2.,1.],dtype=torch.float64)).repeat(2,1,1,1)
    factor = FrozenFisherPrior(p,[16],np.array([True,False]))
    a,b = body(),body()
    a[:,16]=torch.from_numpy(Rotation.from_rotvec([.1,0,0]).as_matrix())
    b[:,16]=torch.from_numpy(Rotation.from_rotvec([0,0,.1]).as_matrix())
    assert float(factor.energy(b)) > 3*float(factor.energy(a))
    a[1,16]=torch.from_numpy(Rotation.from_rotvec([2,0,0]).as_matrix())
    assert float(factor.energy(a)) == pytest.approx(3*(1-np.cos(.1)))


def test_reflection_posterior_optimum_is_on_so3():
    p=torch.diag(torch.tensor([5.,3.,-1.],dtype=torch.float64)).repeat(2,1,1,1)
    factor=FrozenFisherPrior(p,[16],np.ones(2,dtype=bool))
    assert float(factor.energy(body())) == pytest.approx(0.)


def test_gradient_matches_finite_difference():
    factor=FrozenFisherPrior(torch.eye(3).repeat(2,1,1,1),[16],np.ones(2,dtype=bool))
    def energy(angle):
        skew=torch.zeros(3,3,dtype=torch.float64)
        skew[0,1]=-angle;skew[1,0]=angle
        r=body();r[:,16]=torch.matrix_exp(skew)
        return factor.energy(r)
    a=torch.tensor(.2,dtype=torch.float64,requires_grad=True)
    derivative=torch.autograd.grad(energy(a),a)[0]
    numeric=(energy(a.detach()+1e-6)-energy(a.detach()-1e-6))/2e-6
    assert float(derivative)==pytest.approx(float(numeric),abs=1e-8)


def test_reject_empty_valid_support():
    with pytest.raises(ValueError):
        FrozenFisherPrior(torch.eye(3).repeat(2,1,1,1),[16],np.zeros(2,dtype=bool))
