import json
from types import SimpleNamespace

import numpy as np
import pytest
from scipy.spatial.transform import Rotation

from biospur_fusion.c2_uwb_root_world.wear_frame import initial_wear_yaw_registration, registered_pelvis_rotations, common_anatomical_world_yaw
from biospur_fusion.c2_coupled_progressive.contracts import NODE_TO_SEGMENT
from biospur_fusion.c2_uwb_calibration.articulated_range import SEGMENTS, _proxy_points_from_rotations
from build_c2_natural_joint_inputs import build
from test_c2_articulated_range import _geometry


@pytest.mark.parametrize('node,target_xy',[('BSF31CC',[0.,-1.]),('BSFC2CC',[0.,-1.]),
                                         ('BSF6C53',[1.,0.]),('BSF8BC4',[-1.,0.])])
def test_backward_front_and_lateral_shank_registration_is_proper_yaw(node,target_xy):
    raw=np.array([0.,.8,.6])
    yaw,target=initial_wear_yaw_registration(node,raw)
    normal=yaw@raw
    np.testing.assert_allclose(normal[:2]/np.linalg.norm(normal[:2]),target_xy,atol=1e-12)
    assert normal[2]==raw[2]
    np.testing.assert_allclose(yaw.T@yaw,np.eye(3),atol=1e-12)
    assert np.linalg.det(yaw)==pytest.approx(1.)
    np.testing.assert_allclose(yaw[2],[0.,0.,1.])


@pytest.mark.parametrize('bad',[[0.,0.,1.],[0.,2.,0.],[np.nan,0.,0.]])
def test_unobservable_or_invalid_initial_normal_is_rejected(bad):
    with pytest.raises(ValueError):initial_wear_yaw_registration('BSF31CC',bad)


def test_runtime_normal_conjugation_with_nonidentity_mount():
    base=Rotation.from_euler('xyz',[.7,-.2,.4]).as_matrix()
    mount=Rotation.from_euler('xyz',[.2,.3,-.1]).as_matrix()
    raw=-(base@mount.T)[:,2]
    yaw,_=initial_wear_yaw_registration('BSF31CC',raw)
    corrected=base@Rotation.from_rotvec([.04,-.03,.02]).as_matrix()
    runtime=yaw@(corrected@base.T)@yaw.T@(yaw@raw)
    np.testing.assert_allclose(runtime,-(yaw@corrected@mount.T)[:,2],atol=1e-12)


def test_builder_preserves_geometry_native_inputs_and_registers_root_once(tmp_path,monkeypatch):
    geometry=_geometry()
    monkeypatch.setattr('biospur_fusion.c2_3a_kinematics.load_frozen_c2_3a',lambda:SimpleNamespace(geometry=geometry))
    continuous=tmp_path/'continuous';fit=tmp_path/'fit';continuous.mkdir();fit.mkdir()
    times=np.array([100.,100.005,100.010]);nodes=np.array(list(NODE_TO_SEGMENT));segments=np.asarray(SEGMENTS)
    physical=np.broadcast_to(Rotation.from_euler('x',.9).as_matrix(),(3,10,3,3)).copy()
    physical[1]=Rotation.from_rotvec(np.tile([.01,.02,.03],(10,1))).as_matrix()@physical[1]
    anatomical=physical.copy();anatomical[:,2]=anatomical[:,2]@Rotation.from_euler('z',.4).as_matrix()
    points=_proxy_points_from_rotations(dict(zip(SEGMENTS,physical.swapaxes(0,1))),geometry)
    names=np.asarray(list(points));joints=np.stack([points[n] for n in names],axis=1)
    embedding=np.diag([-1.,1.,1.]);joints=joints@embedding.T
    np.savez(continuous/'POSE.npz',time_s=times,joint_names=names,joints_relative=joints,
             geometry_embedding_from_previous=embedding,anchors_m=np.zeros((8,3)))
    np.savez(continuous/'ROTATIONS.npz',time_s=times,segment_names=segments,base_segment_rotations_world=anatomical)
    np.savez(continuous/'PRE_IK_ROTATIONS.npz',time_s=times,segment_names=segments,base_segment_rotations_world=physical)
    np.savez(fit/'REPLAY_CALIBRATION.npz',initial_world_sensor=np.tile(np.eye(3),(10,1,1)))
    (fit/'RESULT.json').write_text(json.dumps(dict(segment_order=list(SEGMENTS))))
    force=np.array([[1.,2.,9.8],[1.1,2.,9.8],[1.,2.1,9.8]])
    source=tmp_path/'source.npz';availability=times+.01
    np.savez(source,time_s=times,node_names=nodes,pelvis_acc_sensor=force,availability_time_s=availability)
    source.with_suffix('.json').write_text(json.dumps(dict(chest_vertical_observations_m=[.14,.15],
        tag_geometry_scope='ENGINEERING_PROXY_NOT_MEASURED_ANTENNA_CENTRES',normals_owner='OLD_STALE_OWNER')))
    output=tmp_path/'rebound';build(continuous,fit,source,output)
    with np.load(output/'POSE.npz') as p,np.load(output/'ROTATIONS.npz') as r,np.load(output/'NORMALS.npz') as n:
        for archive in (p,r,n):np.testing.assert_array_equal(archive['time_s'],times)
        np.testing.assert_array_equal(p['joints_relative'],joints)
        np.testing.assert_array_equal(p['geometry_embedding_from_previous'],embedding)
        np.testing.assert_array_equal(p['pelvis_acc_sensor'],force)
        np.testing.assert_array_equal(p['availability_time_s'],availability)
        np.testing.assert_array_equal(r['physical_segment_rotations_world'],physical)
        np.testing.assert_array_equal(r['base_segment_rotations_world'],anatomical)
        np.testing.assert_array_equal(p['pelvis_rotation_world_sensor'],physical[:,0])
        root,yaw=registered_pelvis_rotations(p,n)
        np.testing.assert_allclose(root,yaw@physical[:,0],atol=1e-12)
        for i,node in enumerate(nodes):
            si=SEGMENTS.index(NODE_TO_SEGMENT[str(node)]);y=n['yaw_registration_world'][i]
            np.testing.assert_allclose(n['node_normals_world'][:,i],-(y@physical[:,si])[:,:,2],atol=1e-12)
            np.testing.assert_allclose(n['node_normals_world'][:,i,2],-physical[:,si,2,2],atol=1e-12)
            np.testing.assert_allclose(np.linalg.norm(n['node_normals_world'][:,i],axis=1),1.,atol=1e-12)
            # Existing runtime conjugation applies registration once even when
            # estimator right-local corrections alter the physical segment.
            corrected=physical[0,si]@Rotation.from_rotvec([.04,-.03,.02]).as_matrix()
            world_error=corrected@physical[0,si].T
            runtime=y@world_error@y.T@n['node_normals_world'][0,i]
            np.testing.assert_allclose(runtime,-(y@corrected)[:,2],atol=1e-12)
        doubled=dict(p);doubled['pelvis_rotation_world_sensor']=root
        with pytest.raises(ValueError,match='minus-Z'):registered_pelvis_rotations(doubled,n)
    metadata=json.loads((output/'POSE.json').read_text())
    assert 'OLD_STALE_OWNER' not in json.dumps(metadata)
    assert metadata['normals_changed'] and not metadata['geometry_changed']
    assert not metadata['registration_uses_raw_ranges']
    with pytest.raises(FileExistsError):build(continuous,fit,source,output)
    common_output=tmp_path/'common';build(continuous,fit,source,common_output,common_world_yaw=True)
    with np.load(common_output/'POSE.npz') as p,np.load(common_output/'ROTATIONS.npz') as r,np.load(common_output/'NORMALS.npz') as n:
        g=p['common_world_yaw_from_calibrated']
        np.testing.assert_allclose(g.T@g,np.eye(3),atol=1e-12)
        assert np.linalg.det(g)==pytest.approx(1.)
        np.testing.assert_array_equal(p['geometry_embedding_from_previous'],np.eye(3))
        np.testing.assert_array_equal(p['source_display_embedding_from_previous'],embedding)
        np.testing.assert_array_equal(p['pelvis_acc_sensor'],force)
        np.testing.assert_array_equal(p['availability_time_s'],availability)
        np.testing.assert_array_equal(p['time_s'],times)
        np.testing.assert_allclose(r['physical_segment_rotations_world'],g@physical,atol=1e-12)
        np.testing.assert_allclose(r['base_segment_rotations_world'],g@anatomical,atol=1e-12)
        bones=r['physical_segment_rotations_world']
        for a,b in ((0,1),(2,3),(4,5),(6,7),(8,9)):
            np.testing.assert_allclose(bones[:,a].swapaxes(-1,-2)@bones[:,b],
                physical[:,a].swapaxes(-1,-2)@physical[:,b],atol=1e-12)
        rebuilt=_proxy_points_from_rotations(dict(zip(SEGMENTS,(g@anatomical).swapaxes(0,1))),geometry)
        np.testing.assert_allclose(p['joints_relative'],np.stack([rebuilt[k] for k in names],axis=1),atol=1e-12)
        for i,node in enumerate(nodes):
            j=SEGMENTS.index(NODE_TO_SEGMENT[str(node)])
            np.testing.assert_array_equal(n['yaw_registration_world'][i],np.eye(3))
            sensor=bones[:,j]@r['sensor_from_segment'][j].T
            np.testing.assert_allclose(sensor.swapaxes(-1,-2)@bones[:,j],
                np.broadcast_to(r['sensor_from_segment'][j],sensor.shape),atol=1e-12)
            np.testing.assert_allclose(n['node_normals_world'][:,i],-sensor[:,:,2],atol=1e-12)
        root,yaw=registered_pelvis_rotations(p,n)
        np.testing.assert_allclose(root,bones[:,0],atol=1e-12)
    meta=json.loads((common_output/'POSE.json').read_text())
    assert meta['relative_segment_rotations_preserved'] and not meta['physical_mounts_changed']
    assert not meta['physical_metrology_certified']


def test_common_yaw_preserves_motion_with_nonidentity_mount_and_binds_right():
    g=common_anatomical_world_yaw([.8,.3,.2])
    right=g@np.array([.8,.3,.2])
    assert right[0]<0
    assert abs(right[1])<1e-12
    raw=Rotation.from_euler('xyz',[[.2,.4,.6],[.6,-.2,1.1]]).as_matrix()
    mount=Rotation.from_euler('xyz',[.3,-.4,.8]).as_matrix()
    bone=g@raw;sensor=bone@mount.T
    np.testing.assert_allclose(sensor.swapaxes(-1,-2)@bone,np.broadcast_to(mount,bone.shape),atol=1e-12)
    np.testing.assert_allclose(bone[0].T@bone[1],raw[0].T@raw[1],atol=1e-12)


@pytest.mark.parametrize('bend',[.001,.4,2.4])
def test_common_yaw_runtime_natural_map_equivariance_including_above_rom(bend):
    from test_c2_natural_geometry_contact import natural_owner
    from biospur_fusion.c2_uwb_root_world.natural_geometry import natural_geometry_points_batch
    j=natural_owner(bend)
    r=j.state.rotations[None]
    g=common_anatomical_world_yaw([.8,.3,.2])
    old=natural_geometry_points_batch(r,j.geometry,j.hinges)
    new=natural_geometry_points_batch(g@r,j.geometry,j.hinges)
    for name in old:
        np.testing.assert_allclose(new[name],old[name]@g.T,atol=1e-11)
