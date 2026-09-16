import numpy as np
import pytest
from dataclasses import replace
from scipy.spatial.transform import Rotation
from test_c2_articulated_contact import owner,ankles,ANCHORS,CLOCK,row_at
from biospur_fusion.c2_uwb_root_world.support_points import SupportPoints
from biospur_fusion.root_r3.models import RootState
from biospur_fusion.c2_uwb_root_world.root_input_safety import CausalImuHold
from biospur_fusion.c2_uwb_root_world.support_velocity import SupportVelocityConfig


def test_support_mask_actual_joint_joseph_and_validation():
    root=RootState(0.,np.zeros(9),np.eye(9)*.1);p=SupportPoints()
    p.update(root,np.zeros((2,3)),[True,False],[False,False],[1,1],.005)
    root=replace(root,time_s=.005);prior=p.covariance(root);h=np.zeros((3,12));h[:,:3]=np.eye(3);h[:,9:]=-np.eye(3)
    r=np.eye(3)*.02**2*25;gain=np.linalg.solve(h@prior@h.T+r,h@prior).T
    mask=np.ones(9,bool);mask[0]=False;gain[0]=0
    a=np.eye(12)-gain@h;expected=a@prior@a.T+gain@r@gain.T
    result=p.update(root,np.ones((2,3))*.01,[True,False],[True,False],[1,1],.005,base_gain_row_mask=mask)
    np.testing.assert_allclose(p.covariance(result),expected,atol=1e-14)
    assert result.vector[0]==root.vector[0]
    for bad in (np.ones(9),np.ones(8,bool)):
        with pytest.raises(ValueError):p.update(root,np.zeros((2,3)),[False,False],[False,False],[1,1],.005,base_gain_row_mask=bad)


def test_raw_and_velocity_consider_all_orientation_and_anchors():
    j=owner();j.contact_leg_only=True
    j.propagate_safe(CausalImuHold(0,[0,0,9.80665],np.eye(3)),.005)
    j.observe_imu_base(.005,j.base.copy())
    j.update_contact(ankles(j),[True,False],[False,False],[1,1],.005,[False,False])
    before=j.state.rotations.copy();anchors=j.contacts.means.copy()
    row=replace(row_at(.005,j.state.root.position_m+j.tags()['BSFEC35']+[.03,0,0]),node='BSFEC35')
    decision=j.update_ranges(row,anchors_m=ANCHORS,clock=CLOCK,reference_epoch_s=.005)
    assert decision.accepted
    np.testing.assert_array_equal(j.state.rotations,before);np.testing.assert_array_equal(j.contacts.means,anchors)
    j.update_stationary_velocity([True,False],[1,1],.005,SupportVelocityConfig())
    np.testing.assert_array_equal(j.state.rotations,before);np.testing.assert_array_equal(j.contacts.means,anchors)
    np.linalg.cholesky(j.contacts.covariance(j.tangent()))


def test_contact_moves_legs_only_and_bound_rolls_back(monkeypatch):
    j=owner();j.contact_leg_only=True
    j.update_contact(ankles(j),[True,False],[False,False],[1,1],.005,[False,False])
    j.propagate_safe(CausalImuHold(0,[0,0,9.80665],np.eye(3)),.005)
    j.observe_imu_base(.005,j.base.copy())
    old=j.state.rotations.copy();offset=ankles(j);offset[0,1]+=.01
    assert j.update_contact(offset,[True,False],[True,False],[1,1],.005,[False,False])
    np.testing.assert_array_equal(j.state.rotations[:6],old[:6])
    assert np.linalg.norm(j.state.rotations[6:]-old[6:])>0
    old=j.state;means=j.contacts.means.copy();cov=j.contacts.covariance(j.tangent()).copy()
    method=type(j)._update_contact
    def excessive(candidate,*args):
        method(candidate,*args)
        if np.any(args[2]):
            rotations=candidate.state.rotations.copy();rotations[6]=candidate.base[6]@Rotation.from_rotvec([.181,0,0]).as_matrix()
            candidate.state=replace(candidate.state,rotations=rotations)
    monkeypatch.setattr(type(j),'_update_contact',excessive)
    assert not j.update_contact(ankles(j),[True,False],[True,False],[1,1],.005,[False,False])
    np.testing.assert_array_equal(j.state.rotations,old.rotations)
    np.testing.assert_array_equal(j.state.root.vector,old.root.vector)
    np.testing.assert_array_equal(j.contacts.means,means);np.testing.assert_array_equal(j.contacts.covariance(j.tangent()),cov)
    assert j.contact_pose_rejected_count==1


def test_rejected_measurement_still_releases_invalid_anchor(monkeypatch):
    j=owner();j.contact_leg_only=True
    j.update_contact(ankles(j),[True,True],[False,False],[1,1],.005,[False,False])
    method=type(j)._update_contact
    def excessive(candidate,*args):
        method(candidate,*args)
        if np.any(args[2]):
            r=candidate.state.rotations.copy();r[8]=candidate.base[8]@Rotation.from_rotvec([.181,0,0]).as_matrix()
            candidate.state=replace(candidate.state,rotations=r)
    monkeypatch.setattr(type(j),'_update_contact',excessive)
    root=j.state.root.vector.copy()
    assert not j.update_contact(ankles(j),[False,True],[False,True],[1,1],.005,[False,False])
    assert j.contacts.sides==[1]
    assert j.contacts.audit[-1][2]==0
    np.testing.assert_array_equal(j.state.root.vector,root)
    np.linalg.cholesky(j.contacts.covariance(j.tangent()))


def test_default_gain_mask_matches_all_true_exactly():
    root=RootState(0.,np.zeros(9),np.eye(9)*.1);owners=[SupportPoints(),SupportPoints()]
    results=[]
    for k,p in enumerate(owners):
        p.update(root,np.zeros((2,3)),[True,False],[False,False],[1,1],.005)
        results.append(p.update(replace(root,time_s=.005),np.ones((2,3))*.01,[True,False],
            [True,False],[1,1],.005,base_gain_row_mask=None if k==0 else np.ones(9,bool)))
    np.testing.assert_array_equal(results[0].vector,results[1].vector)
    np.testing.assert_array_equal(owners[0].covariance(results[0]),owners[1].covariance(results[1]))
