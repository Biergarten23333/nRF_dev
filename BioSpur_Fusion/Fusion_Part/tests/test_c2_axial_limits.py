import numpy as np
import torch
from biospur_fusion.c2_five_calibration.axial_limits import axial_terms


def test_periodic_axial_cost_and_finite_rest_gradient():
    g={'forearm_axial_envelope':dict(limit_deg=100,scale_deg=20,weight=1)}
    q=torch.zeros(4,9,dtype=torch.float64,requires_grad=True)
    with torch.no_grad():q[:,7]=torch.deg2rad(torch.tensor([0.,80.,140.,500.],dtype=q.dtype))
    valid=torch.ones(4,dtype=torch.bool)
    loss=axial_terms(q,valid,g)['axial_loss'];loss.backward()
    assert torch.isfinite(q.grad).all()
    assert q.grad[0,7]==q.grad[1,7]==0
    assert q.grad[2,7]>0
    torch.testing.assert_close(q.grad[2,7],q.grad[3,7])
    assert not q.grad[:,:7].any()


def test_invalid_frames_and_opposite_arm_not_forced_symmetric():
    g={'forearm_axial_envelope':dict(limit_deg=100,scale_deg=20,weight=1)}
    q=torch.zeros(2,9,dtype=torch.float64);q[:,7:9]=torch.deg2rad(torch.tensor([[70.,-90.],[170.,170.]]))
    assert axial_terms(q,torch.tensor([True,False]),g)['axial_loss']==0
    assert axial_terms(q,torch.tensor([True,True]),{})['axial_loss']==0
