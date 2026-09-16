import copy

import numpy as np
import pytest
from scipy.spatial.transform import Rotation

from biospur_fusion.c2_uwb_root_world.wear_frame import registered_pelvis_rotations
from biospur_fusion.root_r3.estimator import RootFilterConfig, propagate_inertial
from biospur_fusion.root_r3.models import RootState


def fixture():
    r=Rotation.from_euler('xyz',[[.2,.3,.1],[.1,.4,.2]]).as_matrix()
    y=Rotation.from_euler('z',176,degrees=True).as_matrix()
    pose=dict(time_s=np.array([1.,1.005]),node_names=np.array(['BSFC2CC']),pelvis_rotation_world_sensor=r)
    registration=dict(time_s=pose['time_s'].copy(),node_names=pose['node_names'].copy(),
                      yaw_registration_world=y[None],node_normals_world=-(y@r)[:,:,2,None].transpose(0,2,1))
    return pose,registration,y


def test_left_world_yaw_keeps_gravity_and_source_inputs():
    p,q,y=fixture();before=copy.deepcopy(p)
    r,m=registered_pelvis_rotations(p,q)
    np.testing.assert_allclose(r,y@p['pelvis_rotation_world_sensor'])
    np.testing.assert_allclose(r[:,2],p['pelvis_rotation_world_sensor'][:,2])
    np.testing.assert_allclose(-r[:,:,2],q['node_normals_world'][:,0])
    for key in p:np.testing.assert_array_equal(p[key],before[key])
    assert not np.shares_memory(r,p['pelvis_rotation_world_sensor'])
    np.testing.assert_allclose(m,y)


def test_physical_acceleration_rotation_and_bias_coordinates():
    p,q,y=fixture();r,_=registered_pelvis_rotations(p,q)
    x=np.r_[np.zeros(6),[.02,-.01,.03]]
    state=RootState(0.,x,np.eye(9));force=np.array([.3,.6,9.7])
    old,_=propagate_inertial(state,.005,force,p['pelvis_rotation_world_sensor'][0],RootFilterConfig())
    new,_=propagate_inertial(state,.005,force,r[0],RootFilterConfig())
    np.testing.assert_allclose(new.vector[3:6],y@old.vector[3:6],atol=1e-12)
    np.testing.assert_array_equal(new.vector[6:9],state.vector[6:9])
    np.testing.assert_allclose(new.vector[2],old.vector[2],atol=1e-12)
    assert np.linalg.eigvalsh(new.covariance).min()>0


def test_reapplying_nonidentity_registration_fails_normal_identity():
    p,q,_=fixture()
    p['pelvis_rotation_world_sensor'],_=registered_pelvis_rotations(p,q)
    with pytest.raises(ValueError,match='minus-Z'):
        registered_pelvis_rotations(p,q)


@pytest.mark.parametrize('fault',['time','node','reflection','tilt','normal'])
def test_incompatible_registration_fails_closed(fault):
    p,q,y=fixture()
    if fault=='time':q['time_s'][0]+=.001
    elif fault=='node':q['node_names']=np.array(['other'])
    elif fault=='reflection':q['yaw_registration_world'][0]=np.diag([-1.,1.,1.])
    elif fault=='tilt':q['yaw_registration_world'][0]=Rotation.from_euler('x',.1).as_matrix()
    else:q['node_normals_world'][0,0,0]+=.01
    with pytest.raises(ValueError):registered_pelvis_rotations(p,q)
