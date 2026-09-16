import torch
from biospur_fusion.c2_five_calibration.gap_continuity import gap_correction_energy


def test_gap_variance_scales_with_elapsed_time_without_measuring_missing_pose():
    v=torch.tensor([True,True,False,True,True])
    p=torch.zeros(5,21,dtype=torch.float64,requires_grad=True)
    with torch.no_grad():p[3,0]=.12;p[3,9]=.12
    seed=torch.zeros(5,9,dtype=p.dtype)
    a,b=gap_correction_energy(p,seed,v)
    torch.testing.assert_close(a,torch.tensor(.1/(2*2*9),dtype=p.dtype))
    torch.testing.assert_close(b,torch.tensor(.1/(2*2*12),dtype=p.dtype))
    grad=torch.autograd.grad(a+b,p)[0]
    assert torch.count_nonzero(grad[2])==0
    assert grad[1,0]!=0 and grad[3,0]!=0
    c,d=gap_correction_energy(p,seed,v,consumed_frames=4)
    assert c==0 and d==0


def test_no_gaps_adds_no_factors():
    p=torch.randn(30,21,dtype=torch.float64)
    a,b=gap_correction_energy(p,p[:,:9],torch.ones(30,dtype=torch.bool))
    assert a==0 and b==0
