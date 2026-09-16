import numpy as np
import pytest
import torch
from biospur_fusion.c2_five_calibration.temporal_linearization import disjoint_row_groups,linearize_temporal_residual


def test_grouped_gradients_match_full_jacobian_including_zero_derivatives():
    torch.set_num_threads(1);x=torch.arange(60,dtype=torch.float64).reshape(30,2)/100
    start=np.r_[np.arange(30),np.arange(26),np.arange(10,20)]
    stop=np.r_[np.arange(30)+1,np.arange(26)+5,np.arange(10,20)+1]
    def f(x):return torch.cat((x[:,0].square(),torch.stack([torch.sin(x[i:i+5]).sum() for i in range(26)]),x[10:20,1]*0.))
    J,r,groups=linearize_temporal_residual(f,x,start,stop,group_batch=3)
    expected=torch.autograd.functional.jacobian(f,x).reshape(len(start),-1)
    np.testing.assert_allclose(J.toarray(),expected,atol=1e-14)
    np.testing.assert_allclose(r,f(x));assert groups<10
    assert J.nnz==int(np.sum((stop-start)*2))
    for rows in disjoint_row_groups(start,stop,len(x)):
        assert all(stop[a]<=start[b] for a,b in zip(rows,rows[1:]))
    with pytest.raises(ValueError,match='memory'):
        linearize_temporal_residual(f,x,start,stop,max_bytes=1)


def test_illegal_support_is_rejected():
    for start,stop in [([0],[0]),([-1],[1]),([29],[31]),([0.5],[1.5])]:
        with pytest.raises(ValueError):disjoint_row_groups(start,stop,30)


def test_validity_gaps_and_shuffled_row_order_preserve_derivatives():
    x=torch.linspace(-.3,.3,48,dtype=torch.float64).reshape(24,2)
    rows=np.array([17,2,9,0,18,4]);start=rows.copy();stop=rows+4
    def f(p):return torch.stack([(p[i:i+4,0]*p[i:i+4,1]).sum() for i in rows])
    J,_,_=linearize_temporal_residual(f,x,start,stop,group_batch=2)
    np.testing.assert_allclose(J.toarray(),torch.autograd.functional.jacobian(f,x).reshape(len(rows),-1),atol=1e-14)
