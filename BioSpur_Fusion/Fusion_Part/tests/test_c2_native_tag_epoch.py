from dataclasses import replace
import numpy as np
import pytest
from scipy.spatial.transform import Rotation

from biospur_fusion.c2_uwb_root_world.articulated_joint_filter import ArticulatedJointState
from biospur_fusion.c2_uwb_root_world.root_input_safety import CausalImuHold
from biospur_fusion.c2_uwb_root_world.tight_range import linearize_raw_range_factors,RawRangeUpdateConfig
from biospur_fusion.c2_uwb_root_world.native_tag_epoch import temporal_structural_constraints
from test_c2_contact_conditional_heading import heading_owner,invariant
from test_c2_continuous_full_state_feedback import ANCHORS,CLOCK,row_at


def moving(enabled=True):
    j=heading_owner(native_tag_epoch_correction=enabled,raw_structural_heading=True)
    x=j.state.root.vector.copy();x[3:6]=[20.,0.,0.]
    j._install(x,j.state.rotations,j.state.covariance)
    j.propagate_safe(CausalImuHold(0,[0,0,9.80665],j.sensor_rotation()),.005)
    native=j.base.copy();native[2:4]=native[2:4]@Rotation.from_rotvec([0,.06,.03]).as_matrix()
    j.observe_imu_base(.005,native)
    j.propagate_safe(CausalImuHold(.005,[0,0,9.80665],j.sensor_rotation()),.009)
    return j


def predicted(j,node='BSFEC35',mask=255):
    measurement=replace(row_at(.009,j.state.root.position_m+j.tags()[node]),node=node,valid_mask=mask)
    p,v,jp,jv,_=j._raw_tag_reference(node,.009)
    f=linearize_raw_range_factors(j.state.root,measurement,anchors_m=ANCHORS,clock=CLOCK,
        tag_offset_world_m=p,tag_offset_velocity_world_mps=v,reference_epoch_s=.009,
        config=RawRangeUpdateConfig(partial_tracking=True),_enforce_geometry=False)
    jac=jp[None]+(f.link_epochs_s-.009)[:,None,None]*jv[None]
    return f,np.einsum('ni,nij->nj',f.state_jacobian[:,:3],jac)


@pytest.mark.parametrize('mask',[1,7,255])
def test_intertick_full_link_jacobian_matches_finite_difference(mask):
    j=moving();old=j.state;f,analytic=predicted(j,mask=mask)
    numeric=np.zeros_like(analytic)
    for c in range(30):
        d=np.zeros((10,3));d.flat[c]=2e-7;values=[]
        for sign in (-1,1):
            j.state=ArticulatedJointState(old.root,old.rotations@Rotation.from_rotvec(sign*d).as_matrix(),old.covariance)
            values.append(predicted(j,mask=mask)[0].predicted_ranges_m)
        numeric[:,c]=(values[1]-values[0])/4e-7
    np.testing.assert_allclose(analytic,numeric,atol=5e-9)
    j.state=old
    # The old derivative lacks both reference-age advancement and secant J.
    assert np.max(abs(analytic-f.state_jacobian[:,:3]@j.tag_jacobian('BSFEC35')))>1e-3


def test_reference_advance_and_high_translation_are_not_speed_limited():
    j=moving();node='BSFEC35';p,v,jp,jv,audit=j._raw_tag_reference(node,.009)
    np.testing.assert_allclose(p-j.tags()[node],.004*v,atol=1e-14)
    np.testing.assert_allclose(jp-j.tag_jacobian(node),.004*jv,atol=1e-14)
    assert np.linalg.norm(v)>6.
    assert j.state.root.velocity_mps[0]>19.9
    f,_=predicted(j)
    points=j.state.root.position_m+p+(f.link_epochs_s-.009)[:,None]*(j.state.root.velocity_mps+v)
    np.testing.assert_allclose(f.predicted_ranges_m,np.linalg.norm(points-ANCHORS[list(f.anchors)],axis=1),atol=1e-13)
    assert audit['motion_status']=='FRESH_NATIVE_SECANT'


@pytest.mark.parametrize('dt,reference,reason',[(0.,.009,'NO_NATIVE_INCREMENT'),(.02,.009,'NATIVE_SAMPLE_GAP'),
    (.005,.01001,'NATIVE_HOLD_EXPIRED')])
def test_stale_or_gap_motion_holds_point_without_extrapolation(dt,reference,reason):
    j=moving();j.native_dt_s=dt
    p,v,jp,jv,audit=j._raw_tag_reference('BSFEC35',reference)
    np.testing.assert_array_equal(p,j.tags()['BSFEC35'])
    np.testing.assert_array_equal(jp,j.tag_jacobian('BSFEC35'))
    np.testing.assert_array_equal(v,np.zeros(3));np.testing.assert_array_equal(jv,np.zeros((3,30)))
    assert audit['motion_status']==reason


def test_after_retraction_recomputes_motion_and_uses_actual_h_joseph(monkeypatch):
    from biospur_fusion.c2_uwb_root_world.contact_raw_gain import grouped_heading_constraints,project_augmented_gain
    from biospur_fusion.c2_uwb_calibration.articulated_range import _so3_right_jacobian
    j=moving();old=j.state;f,hpose=predicted(j)
    measurement=replace(row_at(.009,j.state.root.position_m+j._raw_tag_reference('BSFEC35',.009)[0]+[.03,.02,0]),node='BSFEC35')
    p,v,jp,jv,_=j._raw_tag_reference(measurement.node,.009)
    f=linearize_raw_range_factors(old.root,measurement,anchors_m=ANCHORS,clock=CLOCK,
        tag_offset_world_m=p,tag_offset_velocity_world_mps=v,reference_epoch_s=.009,config=RawRangeUpdateConfig(),_enforce_geometry=False)
    h=np.zeros((len(f.anchors),39));h[:,:9]=f.state_jacobian
    h[:,9:]=np.einsum('ni,nij->nj',h[:,:3],jp[None]+(f.link_epochs_s-.009)[:,None,None]*jv)
    noise=np.diag(np.diag(f.r_prior_m2)/f.robust_weights);prior=old.covariance
    gain=np.linalg.solve(h@prior@h.T+noise,h@prior).T
    structural,_=temporal_structural_constraints(old.rotations,jp,jv)
    gain,_=project_augmented_gain(gain,prior,np.vstack((grouped_heading_constraints(old.rotations),structural)))
    error=gain@f.innovations_m;a=np.eye(39)-gain@h;expected=a@prior@a.T+gain@noise@gain.T
    reset=np.eye(39)
    for i in range(10):reset[9+3*i:12+3*i,9+3*i:12+3*i]=_so3_right_jacobian(error[9+3*i:12+3*i])
    expected=reset@expected@reset.T
    decision=j.update_ranges(measurement,anchors_m=ANCHORS,clock=CLOCK,reference_epoch_s=.009,
        offset_velocity_world_mps=np.array([999.,999.,999.]))
    assert decision.accepted
    np.testing.assert_allclose(j.state.covariance,expected,atol=2e-12)
    after_v=j._raw_tag_reference(measurement.node,.009)[1]
    assert np.linalg.norm(after_v-v)>1e-6
    np.testing.assert_allclose(j.last_contact_routing_audit['native_tag_epoch_after']['relative_tag_velocity_mps'],after_v)
    after_f,_=predicted(j)
    np.testing.assert_allclose(decision.predicted_ranges_m,after_f.predicted_ranges_m,atol=1e-13)
    invariant(old.rotations,j.state.rotations)
    np.linalg.cholesky(j.state.covariance)


def test_velocity_sensitivity_retains_heading_authority_when_point_j_is_zero():
    j=moving();point=np.zeros((3,30));velocity=point.copy();velocity[0,3:6]=j.state.rotations[1,2,:]
    _,audit=temporal_structural_constraints(j.state.rotations,point,velocity)
    assert audit['sensitive_groups']==['torso']


def test_disabled_default_exact_parity():
    a=heading_owner();b=heading_owner(native_tag_epoch_correction=False)
    for j in (a,b):
        measurement=replace(row_at(0.,j.state.root.position_m+j.tags()['BSFEC35']+[.02,.01,0]),node='BSFEC35')
        assert j.update_ranges(measurement,anchors_m=ANCHORS,clock=CLOCK,reference_epoch_s=0.).accepted
    np.testing.assert_array_equal(a.state.root.vector,b.state.root.vector)
    np.testing.assert_array_equal(a.state.rotations,b.state.rotations)
    np.testing.assert_array_equal(a.state.covariance,b.state.covariance)


def test_all_tags_use_same_native_motion_including_chest_scale():
    j=moving();native=j.native_motion(with_jacobian=False)[2]
    for node,velocity in native.items():
        _,v,_,_,_=j._raw_tag_reference(node,.009)
        np.testing.assert_allclose(v,velocity,atol=1e-12)


def test_above_rom_geometry_reference_jacobian_matches_finite_difference():
    from test_c2_natural_geometry_contact import natural_owner
    j=natural_owner(2.4);j.native_dt_s=.005;j.native_increment[7]=Rotation.from_rotvec([.02,.01,0]).as_matrix()
    old=j.state;_,_,analytic,_,_=j._raw_tag_reference('BSF6C53',.004)
    for c in range(18,24):
        d=np.zeros((10,3));d.flat[c]=2e-7;values=[]
        for sign in (-1,1):
            j.state=ArticulatedJointState(old.root,old.rotations@Rotation.from_rotvec(sign*d).as_matrix(),old.covariance)
            values.append(j._raw_tag_reference('BSF6C53',.004)[0])
        np.testing.assert_allclose((values[1]-values[0])/4e-7,analytic[:,c],atol=1e-7)


def test_unsupported_outlier_rejects_atomically_and_coherent_drift_still_corrects():
    j=moving();before=j.state
    measurement=replace(row_at(.009,j.state.root.position_m+j._raw_tag_reference('BSFEC35',.009)[0]),node='BSFEC35')
    bad=replace(measurement,ranges_mm=tuple(int(x)+10000 for x in measurement.ranges_mm))
    assert not j.update_ranges(bad,anchors_m=ANCHORS,clock=CLOCK,reference_epoch_s=.009).accepted
    assert j.state is before
    for k in range(3):
        measurement=replace(row_at(.009,j.state.root.position_m+j._raw_tag_reference('BSFEC35',.009)[0]+[.01,.01,0]),node='BSFEC35',sequence=k+1)
        assert j.update_ranges(measurement,anchors_m=ANCHORS,clock=CLOCK,reference_epoch_s=.009).accepted
    assert np.linalg.norm(j.state.root.position_m-before.root.position_m)>1e-4
    assert j.state.root.velocity_mps[0]>19.9


def test_reversed_reference_is_not_future_native_evidence():
    j=moving()
    with pytest.raises(ValueError,match='precedes'):j._raw_tag_reference('BSFEC35',.004)


def test_snapshot_matches_all_tag_owner_queries_and_is_not_mutated():
    from biospur_fusion.c2_uwb_root_world.native_tag_epoch import NativeTagSnapshot
    j=moving();tags=j.tags();velocities=j.native_motion(with_jacobian=False)[2];nodes=list(tags)
    points=np.stack([tags[n] for n in nodes]);velocity=np.stack([velocities[n] for n in nodes])
    snapshot=NativeTagSnapshot(points,velocity,j.last_base_time_s,j.native_dt_s)
    original=points.copy();points[:]=999.;velocity[:]=999.
    for reference in (.005,.009,.010,.01001):
        for i,node in enumerate(nodes):
            np.testing.assert_allclose(snapshot.point_at(i,reference),j._raw_tag_reference(node,reference)[0],atol=1e-13)
    np.testing.assert_array_equal(snapshot.points,original)
    with pytest.raises(ValueError):snapshot.points[0,0]=1.
    queried=snapshot.point_at(0,.009);queried[:]=888.
    np.testing.assert_array_equal(snapshot.points,original)


def test_strict_prior_history_snapshot_excludes_equal_or_later_posterior():
    from biospur_fusion.c2_uwb_root_world.native_tag_epoch import NativeTagSnapshot
    first_link=.007;reference=.009
    prior=NativeTagSnapshot([[1.,2.,3.]],[[4.,5.,6.]],.005,.005)
    posterior=NativeTagSnapshot([[999.,999.,999.]],[[100.,100.,100.]],.005,.005)
    history=[(.006,prior),(.007,posterior),(.008,posterior)]
    selected=next(snapshot for epoch,snapshot in reversed(history) if epoch<first_link)
    np.testing.assert_allclose(selected.point_at(0,reference),[1.016,2.020,3.024])
    np.testing.assert_array_equal(selected.point_at(0,.011),[1.,2.,3.])


@pytest.mark.parametrize('dt',[0.,.02])
def test_snapshot_missing_or_gap_secant_holds_physical_native_point(dt):
    from biospur_fusion.c2_uwb_root_world.native_tag_epoch import NativeTagSnapshot
    s=NativeTagSnapshot([[1.,2.,3.]],[[100.,100.,100.]],.005,dt)
    np.testing.assert_array_equal(s.point_at(0,.009),[1.,2.,3.])
    with pytest.raises(ValueError,match='precedes'):s.point_at(0,.004)
