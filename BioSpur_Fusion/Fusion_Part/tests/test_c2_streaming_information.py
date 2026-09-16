import time
import numpy as np
import pytest
import torch
from biospur_fusion.c2_five_calibration.streaming_information import StreamingInformation,linearize_residual
from biospur_fusion.c2_five_calibration.marginalization import marginalize_linear_system


def test_streamed_elimination_matches_whole_joint_system_with_shared_null_mode():
    rng=np.random.default_rng(83);J=np.zeros((60,7));r=rng.normal(size=60)
    # Coordinates x0..x4, shared calibration s, unobserved shared u.
    J[:20,:3]=rng.normal(size=(20,3));J[:20,5]=rng.normal(size=20)
    J[20:40,2:4]=rng.normal(size=(20,2));J[20:40,5]=rng.normal(size=20)
    J[40:,3:6]=rng.normal(size=(20,3))
    stream=StreamingInformation(['s','u'])
    stream.append(J[:20][:,[0,1,2,5,6]],r[:20],['x0','x1','x2','s','u'],np.zeros(5),['x2','s','u'])
    stream.append(J[20:40][:,[2,3,5,6]],r[20:40],['x2','x3','s','u'],np.zeros(4),['x3','s','u'])
    factor=stream.append(J[40:][:,[3,4,5,6]],r[40:],['x3','x4','s','u'],np.zeros(4),['x4','s','u'])
    direct=marginalize_linear_system(J,r,np.array([4,5,6]))
    for delta in rng.normal(size=(10,3)):
        np.testing.assert_allclose(factor.energy(delta),direct.energy(delta),atol=1e-10)
    assert factor.unresolved_dimensions==1
    with pytest.raises(ValueError,match='fresh/current'):
        stream.append(np.ones((1,1)),[0],['x0'],[0],['s','u'])
    with pytest.raises(ValueError,match='linearization point'):
        stream.append(np.ones((1,1)),[0],['s'],[.1],['s','u'])


def test_bounded_autograd_matches_exact_jacobian_and_deadline():
    def f(x):return torch.cat((x.square(),torch.sin(x),x[:1]*x[1:]))
    x=torch.linspace(.1,.5,5,dtype=torch.float64)
    J,r=linearize_residual(f,x,row_batch=3)
    np.testing.assert_allclose(J,torch.autograd.functional.jacobian(f,x),atol=1e-14)
    np.testing.assert_allclose(r,f(x))
    with pytest.raises(ValueError,match='memory'):
        linearize_residual(f,x,max_bytes=1)
    with pytest.raises(TimeoutError):linearize_residual(f,x,deadline=time.monotonic()-1)


def test_failed_append_does_not_change_previous_boundary():
    state=StreamingInformation(['s'],max_columns=3)
    first=state.append(np.eye(2),np.zeros(2),['x','s'],[0.,0.],['s'])
    with pytest.raises(ValueError,match='retain every shared'):
        state.append(np.ones((1,2)),[0.],['y','s'],[0.,0.],['y'])
    assert state.factor is first and state.columns==('s',)
    with pytest.raises(ValueError,match='budget'):
        state.append(np.ones((1,4)),[0.],['a','b','c','s'],[0.]*4,['s'])
    assert state.factor is first
