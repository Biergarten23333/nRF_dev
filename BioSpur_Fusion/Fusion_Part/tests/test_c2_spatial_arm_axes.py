import numpy as np
import pytest
import torch
from scipy.spatial.transform import Rotation

from biospur_fusion.c2_five_calibration.arm_protocol import (
    ArmProtocolRow, ArmProtocolTape, spatial_axis_residual,
)


def test_spatial_axis_sign_frame_and_gradient():
    a=torch.tensor([[.4,.5,.7]],dtype=torch.float64,requires_grad=True)
    b=torch.tensor([[.8,-.2,.3]],dtype=torch.float64)
    r=torch.tensor(Rotation.from_rotvec([.3,-.4,.2]).as_matrix())
    torch.testing.assert_close(spatial_axis_residual(a,b),spatial_axis_residual(-a,b))
    torch.testing.assert_close(spatial_axis_residual(a,b),spatial_axis_residual(a@r.T,b@r.T))
    assert torch.autograd.gradcheck(lambda x:spatial_axis_residual(x,b).square(),(a,))
    aligned=b.clone().requires_grad_()
    spatial_axis_residual(aligned,b).square().sum().backward()
    assert torch.isfinite(aligned.grad).all()
    with pytest.raises(ValueError,match='undefined'):
        spatial_axis_residual(a,b*0)


@pytest.mark.parametrize('multiple',[1,2])
def test_only_functional_axes_gain_inclination_information(multiple):
    row=ArmProtocolRow('06_elbow_left',0,np.array([0]),np.array([[1.,0.,0.]]),
                       np.array([1.,0.,0.]),np.array([1.]),multiple,{})
    pose=torch.eye(3,dtype=torch.float64).repeat(1,24,1,1)
    pose[:,9]=torch.tensor(Rotation.from_euler('z',.3).as_matrix())
    delta=torch.zeros(4,dtype=torch.float64)
    legacy=ArmProtocolTape([row],np.zeros(4))
    spatial=ArmProtocolTape([row],np.zeros(4),spatial_axes=True)
    assert legacy.energy_for_action(row.action,delta,pose).item()==pytest.approx(0.)
    assert spatial.energy_for_action(row.action,delta,pose).item()==pytest.approx(.09 if multiple==2 else 0.)
    # Directed forearm cues do not acquire a forced horizontal target.
    assert spatial.audit()[0]['direction_geometry']==('spatial_undirected_axis' if multiple==2 else 'horizontal_azimuth')
