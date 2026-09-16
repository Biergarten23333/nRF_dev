from dataclasses import replace
import copy
import numpy as np
import pytest
from scipy.spatial.transform import Rotation

from biospur_fusion.c2_articulated_biomechanics.model import HingeJoint
from biospur_fusion.c2_uwb_root_world.articulated_joint_filter import ArticulatedJointState
from biospur_fusion.c2_uwb_root_world.natural_geometry import natural_geometry, natural_geometry_points_batch
from biospur_fusion.c2_uwb_calibration.articulated_range import SEGMENTS, _proxy_points_from_rotations
from biospur_fusion.c2_uwb_calibration.frozen_body_proxy import NODE_TO_PROXY_POINT
from test_c2_articulated_contact import owner, ankles
from test_c2_continuous_full_state_feedback import ANCHORS, CLOCK, row_at


def natural_owner(bend=.4):
    j=owner()
    j.natural_geometry_only=True
    j.hinges={'left':HingeJoint('left','thigh_left','shank_left','09',
        (1.,0.,0.),(1.,0.,0.),(0.,0.,0.,1.),1.,0.,120.,1,1)}
    r=j.state.rotations.copy()
    r[6]=Rotation.from_rotvec([.1,-.2,.3]).as_matrix()
    r[7]=r[6]@Rotation.from_rotvec([0.,bend,0.]).as_matrix()
    j.state=ArticulatedJointState(j.state.root,r,j.state.covariance)
    j.base=r.copy()
    return j


@pytest.mark.parametrize('bend',[.001,.4,2.4])
def test_mapped_points_full_physical_local_jacobian(bend):
    j=natural_owner(bend);old=j.state;expected=j.point_jacobians()
    for column in range(30):
        delta=np.zeros((10,3));delta.flat[column]=2e-7
        values=[]
        for sign in (1.,-1.):
            r=old.rotations@Rotation.from_rotvec(sign*delta).as_matrix()
            j.state=ArticulatedJointState(old.root,r,old.covariance)
            values.append(j.points())
        for name in expected:
            np.testing.assert_allclose((values[0][name]-values[1][name])/4e-7,
                                       expected[name][:,column],atol=3e-8)
    j.state=old


@pytest.mark.parametrize('bend',[.001,.4,2.4])
def test_project_is_geometry_only_and_sensor_normals_unchanged(bend):
    j=natural_owner(bend);old=j.state
    j.update_contact(ankles(j),[True,False],[False,False],[1,1],.005,[False,False])
    old=j.state;cov=j.contacts.covariance(j.tangent()).copy()
    sensor=j.sensor_rotation().copy()
    normals=j.state.rotations@j.base.swapaxes(1,2)
    j.project_hinges()
    assert j.state is old
    np.testing.assert_array_equal(j.contacts.covariance(j.tangent()),cov)
    np.testing.assert_array_equal(j.sensor_rotation(),sensor)
    np.testing.assert_array_equal(j.state.rotations@j.base.swapaxes(1,2),normals)
    assert not j.last_projection['physical_angles_certified']
    assert j.last_projection['endpoint_identity_fast_path']==(bend<np.radians(120))


def test_under_rom_exact_fk_and_batch_binding_with_mixed_caps():
    j=natural_owner()
    raw=_proxy_points_from_rotations(j.mapping(j.state.rotations),j.geometry)
    points,_,_=natural_geometry(j.state.rotations,j.geometry,j.hinges)
    for name in raw:np.testing.assert_array_equal(points[name],raw[name])
    blocks=np.stack([natural_owner(b).state.rotations for b in (.001,.4,2.4)])
    batch=natural_geometry_points_batch(blocks,j.geometry,j.hinges)
    for i,r in enumerate(blocks):
        single,_,_=natural_geometry(r,j.geometry,j.hinges)
        for name in batch:np.testing.assert_allclose(batch[name][i],single[name],atol=1e-12)
    points={n:v@j.embedding.T for n,v in batch.items()}
    names=list(points);nodes=list(NODE_TO_PROXY_POINT)
    tags={n:points[p].copy() for n,p in NODE_TO_PROXY_POINT.items()}
    tags['BSF31CC']=points['shoulder_mid']*j.chest_scale
    pose={'joint_names':names,'node_names':nodes,
          'joints_relative':np.stack([points[n] for n in names],axis=1),
          'node_offsets':np.stack([tags[n] for n in nodes],axis=1)}
    sensor=j.wear_yaw@blocks[:,0]@j.mount.T
    audit=j.validate_native_binding(pose,blocks,sensor)
    assert audit['maximum_joint_error_m']<1e-12


@pytest.mark.parametrize('bend',[.4,2.4])
def test_native_motion_uses_same_map_and_chain(bend):
    j=natural_owner(bend);old=j.state
    j.native_dt_s=.005
    d=np.zeros((10,3));d[6]=[.001,.002,0.];d[7]=[-.001,0.,.001]
    j.native_increment=Rotation.from_rotvec(d).as_matrix()
    velocity,derivative,tags=j.native_motion()
    for column in range(18,24):
        perturb=np.zeros((10,3));perturb.flat[column]=2e-7
        values=[]
        for sign in (1.,-1.):
            r=old.rotations@Rotation.from_rotvec(sign*perturb).as_matrix()
            j.state=ArticulatedJointState(old.root,r,old.covariance)
            values.append(j.native_motion(with_jacobian=False)[0])
        np.testing.assert_allclose((values[0]-values[1])/4e-7,derivative[:,:,column],atol=2e-6)
    j.state=old
    assert np.isfinite(velocity).all()
    assert np.isfinite(np.stack(list(tags.values()))).all()


@pytest.mark.parametrize('node',['BSF31CC','BSFAA61','BSF1120','BSFEC35',
                                'BSFB165','BSF44AD','BSF3C79','BSF6C53','BSF8BC4'])
def test_raw_full_pose_gain_and_protected_means(node):
    j=natural_owner()
    j.update_contact(ankles(j),[True,True],[True,True],[1,1],.005,[False,False])
    position=j.state.root.position_m.copy();anchors=j.contacts.means.copy()
    row=replace(row_at(0.,position+j.tags()[node]+[.02,.01,0.]),node=node)
    decision=j.update_ranges(row,anchors_m=ANCHORS,clock=CLOCK,reference_epoch_s=0.,
                             consider_position=True,preserve_anchor_mean=True)
    assert decision.accepted
    assert np.linalg.norm(j.last_orientation_delta)>1e-6
    np.testing.assert_array_equal(j.state.root.position_m,position)
    np.testing.assert_array_equal(j.contacts.means,anchors)
    np.linalg.cholesky(j.contacts.covariance(j.tangent()))


def test_candidate_and_deepcopy_geometry_cache_are_isolated():
    j=natural_owner();before=j.points()['ankle_left'].copy()
    for candidate in (j._candidate(),copy.deepcopy(j)):
        error=np.zeros(39);error[9+3*6]=.04
        candidate._inject_range_error(candidate.state,error,candidate.state.covariance)
        assert np.linalg.norm(candidate.points()['ankle_left']-before)>1e-4
        np.testing.assert_array_equal(j.points()['ankle_left'],before)


@pytest.mark.parametrize('bend',[np.radians(120.),np.radians(180.)])
def test_degenerate_geometry_fails_without_physical_commit(bend):
    j=natural_owner(bend);old=j.state
    with pytest.raises(ValueError):j.project_hinges()
    assert j.state is old
