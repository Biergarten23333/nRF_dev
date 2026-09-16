from dataclasses import replace
import numpy as np
from biospur_fusion.c2_uwb_root_world.persistent_tag_error import PersistentTagErrorFilter
from biospur_fusion.c2_uwb_root_world.support_points import SupportPoints
from biospur_fusion.c2_uwb_root_world.support_velocity import update_support_velocity
from biospur_fusion.c2_uwb_root_world.root_input_safety import CausalImuHold
from biospur_fusion.c2_uwb_root_world.tight_range import RawRangeUpdateConfig
from biospur_fusion.root_r3.estimator import RootFilterConfig
from biospur_fusion.root_r3.models import RootState
from test_c2_continuous_full_state_feedback import ANCHORS,CLOCK,row_at

NODES=tuple('node'+str(i) for i in range(10))
TRUTH=np.array([2.,1.25,1.])
CONFIG=RawRangeUpdateConfig(nominal_sigma_m=.25,positive_nlos_cauchy_scale_m=.12,
                           partial_tracking=True,symmetric_corrected_discrepancy=True)

def owner():
    return PersistentTagErrorFilter(RootState(0.,np.r_[TRUTH,np.zeros(6)],np.eye(9)*.01),NODES)

def update(f,target=TRUTH,mask=255,observer=None,consider=False):
    row=replace(row_at(f.root.time_s,target),node=NODES[0],valid_mask=mask)
    return f.update_tracking(row,anchors_m=ANCHORS,clock=CLOCK,
        offset_world_m=np.zeros(3),offset_velocity_world_mps=np.zeros(3),
        basis_world_from_local=np.eye(3),basis_velocity_world_from_local=np.zeros((3,3)),
        reference_epoch_s=f.root.time_s,config=CONFIG,
        transition_observer=observer,consider_position=consider)

def test_partial_full_prior_nis_and_rejection_do_not_commit():
    f=owner();decision,nis,limit=update(f,TRUTH+[.1,0,0],1)
    assert decision.accepted and np.isfinite(nis) and np.isfinite(limit)
    assert np.linalg.norm(f.covariance[:9,9:])>0
    assert np.linalg.norm(f.error)>0
    np.linalg.cholesky(f.covariance)
    before=f.state;seen=[]
    decision,_,_=update(f,TRUTH+[100,0,0],1,seen.append)
    assert not decision.accepted and f.state is before and not seen
    decision,_,_=update(f,mask=0)
    assert not decision.accepted and f.state is before

def test_root_nuisance_contact_cross_survives_all_update_owners():
    f=owner();update(f,TRUTH+[.1,0,0])
    points=SupportPoints();offset=np.array([[0,0,-1],[.2,0,-1]])
    f.state=points.update(f.state,offset,[True,True],[False,False],[1,1],.005)
    assert points.cross.shape==(39,6)
    assert np.linalg.norm(points.cross[9:])>0
    np.testing.assert_array_equal(points.cross,np.column_stack((f.covariance[:,:3],f.covariance[:,:3])))
    p=points.covariance(f.state);seen=[]
    def observer(a):seen.append(a);points.root_transition(a)
    f.state,_,noise=update_support_velocity(f.state,[[.1,0,0]],[1.],.005,
        consider_position=True,transition_observer=observer)
    h=np.zeros((3,45));h[:,3:6]=np.eye(3)
    gain=np.zeros((45,3));gain[:39]=np.linalg.solve(h@p@h.T+noise,h@p[:,:39]).T;gain[:3]=0
    residual=np.eye(45)-gain@h
    np.testing.assert_allclose(points.covariance(f.state),residual@p@residual.T+gain@noise@gain.T,atol=1e-14)
    hold=CausalImuHold(0.,[0,0,9.80665],np.eye(3));phis=[]
    root,_=hold.propagate(f.root,.012,RootFilterConfig(),transition_observer=phis.append)
    cross=points.cross.copy();f.commit_propagation(root,phis[0],points.root_transition)
    expected=np.eye(39);expected[:9,:9]=phis[0]
    np.testing.assert_allclose(points.cross,expected@cross)
    np.linalg.cholesky(points.covariance(f.state))
    p_before=f.root.position_m.copy();decision,_,_=update(f,TRUTH+[.2,0,0],observer=points.root_transition,consider=True)
    assert decision.accepted
    np.testing.assert_array_equal(f.root.position_m,p_before)
    np.linalg.cholesky(points.covariance(f.state))
    f.state=points.update(f.state,offset,[True,True],[True,True],[1,1],.005)
    np.linalg.cholesky(points.covariance(f.state))
    assert points.covariance(f.state).shape==(45,45)
    assert len(points.audit[-1])==14

def test_episode_reset_is_off_by_default():
    f=owner();p=SupportPoints();offset=np.zeros((2,3))
    p.update(f.state,offset,[True,False],[False,False],[1,1],.005,[True,False])
    f.state=replace(f.state,time_s=.005)
    p.update(f.state,offset,[True,False],[False,False],[1,1],.005,[False,False])
    assert not p.stationary_entries
