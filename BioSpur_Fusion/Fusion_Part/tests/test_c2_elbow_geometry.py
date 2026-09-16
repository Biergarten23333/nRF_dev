import numpy as np
import pytest
import torch
from scipy.spatial.transform import Rotation
from biospur_fusion.c2_five_calibration.elbow_geometry import forward_elbow,inverse_elbow,geometric_bend


def test_independent_forward_inverse_signed_angles_and_geometric_bend():
    values=np.deg2rad([[0,8,0],[40,-9,70],[145,12,-80],[-35,8,40]])
    # Independent SciPy intrinsic rotations, not the implementation under test.
    oracle=torch.tensor(Rotation.from_euler('XYZ',values*np.array([1,1,-1])).as_matrix())
    args=torch.tensor(values)
    torch.testing.assert_close(forward_elbow(*args.T),oracle,atol=1e-12,rtol=0)
    torch.testing.assert_close(inverse_elbow(oracle),args,atol=1e-12,rtol=0)
    expected=np.arccos(np.cos(values[:,0])*np.cos(values[:,1]))
    np.testing.assert_allclose(geometric_bend(oracle),expected,atol=1e-12)
    assert inverse_elbow(oracle)[-1,0]<0
    assert geometric_bend(oracle)[-1]>0  # unsigned angle cannot enforce anti-reversal
    np.testing.assert_allclose(geometric_bend(oracle)[0],np.deg2rad(8),atol=1e-12)


def test_zero_carrying_matches_existing_canonical_hinge():
    f=torch.linspace(0,2.5,21,dtype=torch.float64);p=torch.linspace(-1.2,1.2,21,dtype=torch.float64)
    z=torch.zeros_like(f)
    expected=Rotation.from_rotvec(np.c_[f.numpy(),z.numpy(),z.numpy()]).as_matrix()@Rotation.from_rotvec(np.c_[z.numpy(),z.numpy(),-p.numpy()]).as_matrix()
    np.testing.assert_allclose(forward_elbow(f,z,p),expected,atol=1e-12)
    torch.testing.assert_close(geometric_bend(forward_elbow(f,z,p)),f,atol=1e-12,rtol=0)


def test_finite_parameter_gradients_and_inverse_roundtrip():
    q=torch.tensor([[0.,.14,.3],[.7,-.1,-.4]],dtype=torch.float64,requires_grad=True)
    assert torch.autograd.gradcheck(lambda v:forward_elbow(*v.T),(q,))
    assert torch.autograd.gradcheck(lambda v:inverse_elbow(forward_elbow(*v.T)),(q,))
    geometric_bend(forward_elbow(*q.T)).sum().backward()
    assert torch.isfinite(q.grad).all()


def test_reflection_and_singular_branch_are_rejected():
    bad=torch.eye(3,dtype=torch.float64);bad[0,0]=-1
    with pytest.raises(ValueError,match='proper'):inverse_elbow(bad)
    r=torch.tensor(Rotation.from_euler('Y',np.pi/2).as_matrix())
    with pytest.raises(ValueError,match='singularity'):inverse_elbow(r)
