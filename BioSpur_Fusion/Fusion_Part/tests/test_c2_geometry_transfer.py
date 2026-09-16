import copy
import numpy as np
import torch
import pytest
from scipy.spatial.transform import Rotation
from biospur_fusion.c2_five_calibration.geometry_transfer import geometry_acceleration_difference
from test_c2_joint_kinematics import geometry


def test_rotating_sensor_lever_has_expected_centripetal_geometry_difference():
    g=geometry();g['bone_frame_correction']=np.tile(np.eye(3),(5,1,1)).tolist()
    t=np.arange(100)/20
    r=torch.tensor(Rotation.from_rotvec(np.c_[t*.4,t*0,t*0]).as_matrix())
    r=r[:,None].repeat(1,24,1,1)
    levers=np.zeros((5,3));changed=levers.copy();changed[1,1]=.06
    difference=geometry_acceleration_difference(r,g,g,levers,changed)
    expected=np.c_[t*0,-.06*.4**2*np.cos(.4*t),-.06*.4**2*np.sin(.4*t)][5:-5]
    # Independent continuous centripetal formula; finite-window error bounded.
    np.testing.assert_allclose(difference[:,:,1],np.repeat(expected[:,None],2,axis=1),atol=2e-5)
    torch.testing.assert_close(difference[:,:,[0,2,3,4]],torch.zeros_like(difference[:,:,[0,2,3,4]]),atol=0,rtol=0)
    zero=geometry_acceleration_difference(r,g,g,levers,levers)
    assert not torch.count_nonzero(zero)
    wrong=copy.deepcopy(g);wrong['bone_frame_correction'][0][0][0]=-1
    with pytest.raises(ValueError,match='bone frames'):
        geometry_acceleration_difference(r,g,wrong,levers,levers)
