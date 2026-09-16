from dataclasses import replace
import numpy as np
import pytest

from biospur_fusion.c2_uwb_root_world.raw_heading_authority import structural_heading_constraints
from biospur_fusion.c2_uwb_root_world.articulated_joint_filter import SEGMENT_HEADING_GROUP,HEADING_GROUP_NAMES
from biospur_fusion.c2_uwb_root_world.tight_range import RawRangeUpdateConfig,linearize_raw_range_factors
from biospur_fusion.c2_uwb_root_world.contact_raw_gain import grouped_heading_constraints,project_augmented_gain
from biospur_fusion.c2_uwb_calibration.articulated_range import _so3_right_jacobian
from biospur_fusion.c2_coupled_progressive.contracts import NODE_TO_SEGMENT
from test_c2_contact_conditional_heading import heading_owner,invariant
from test_c2_articulated_contact import owner,ankles
from test_c2_continuous_full_state_feedback import ANCHORS,CLOCK,row_at


def row(j,node):
    return replace(row_at(0.,j.state.root.position_m+j.tags()[node]+[.02,.01,.005]),node=node)


def correlated_owner(enabled):
    j=heading_owner(raw_structural_heading=enabled)
    direction=np.zeros(39);direction[0]=1.
    for i in (1,2,3):direction[9+3*i:12+3*i]=j.state.rotations[i,2,:]
    p=j.state.covariance+.05*np.outer(direction,direction)
    j._install(j.state.root.vector,j.state.rotations,p)
    return j


def test_correlated_chest_cannot_rotate_unobserved_arm_but_default_can():
    old=correlated_owner(False);new=correlated_owner(True)
    before=new.state.rotations.copy();p=new.state.covariance.copy()
    for j in (old,new):assert j.update_ranges(row(j,'BSF31CC'),anchors_m=ANCHORS,clock=CLOCK,reference_epoch_s=0.).accepted
    assert np.linalg.norm(old.last_orientation_delta[2:4])>1e-5
    np.testing.assert_allclose(new.state.rotations[2:4],before[2:4],atol=1e-13)
    assert np.linalg.norm(new.last_orientation_delta[1])>1e-8
    assert np.linalg.norm(new.state.root.position_m-[2.,1.3,1.])>1e-6
    assert np.linalg.norm(p[12:15,15:21])>0
    assert np.linalg.norm(new.state.covariance[12:15,15:21])>0
    np.linalg.cholesky(new.state.covariance)
    invariant(before,new.state.rotations)


@pytest.mark.parametrize('node',list(NODE_TO_SEGMENT))
def test_ten_nodes_keep_root_and_sensitive_group_authority(node):
    j=heading_owner(raw_structural_heading=True);old=j.state
    assert j.update_ranges(row(j,node),anchors_m=ANCHORS,clock=CLOCK,reference_epoch_s=0.).accepted
    audit=j.last_contact_routing_audit['structural_heading_authority']
    assert not audit['root_correction_restricted'] and not audit['root_jump_prevention_claimed']
    assert np.linalg.norm(j.state.root.position_m-old.root.position_m)>1e-8
    for group,name in enumerate(HEADING_GROUP_NAMES):
        ids=np.flatnonzero(SEGMENT_HEADING_GROUP==group)
        if name in audit['considered_groups']:
            np.testing.assert_allclose(j.state.rotations[ids],old.rotations[ids],atol=1e-13)
        else:assert np.linalg.norm(j.last_orientation_delta[ids])>1e-9
    invariant(old.rotations,j.state.rotations)


@pytest.mark.parametrize('mask',[1,7,255])
def test_partial_range_links_do_not_change_structural_fk_classification(mask):
    j=heading_owner(raw_structural_heading=True)
    _,expected=structural_heading_constraints(j.state.rotations,j.tag_jacobian('BSFEC35'))
    measurement=replace(row(j,'BSFEC35'),valid_mask=mask)
    assert j.update_ranges(measurement,anchors_m=ANCHORS,clock=CLOCK,reference_epoch_s=0.,
        config=RawRangeUpdateConfig(partial_tracking=True)).accepted
    assert j.last_contact_routing_audit['structural_heading_authority']['considered_groups']==expected['considered_groups']


def test_structural_contact_and_heading_constraints_share_actual_joseph(monkeypatch):
    import biospur_fusion.c2_uwb_root_world.contact_raw_gain as module
    j=correlated_owner(True);j.update_contact(ankles(j),[True,True],[False,False],[1,1],.005,[False,False])
    old=j.state;p=j.contacts.covariance(j.tangent());measurement=row(j,'BSF31CC')
    f=linearize_raw_range_factors(old.root,measurement,anchors_m=ANCHORS,clock=CLOCK,
        tag_offset_world_m=j.tags()[measurement.node],reference_epoch_s=0.,config=RawRangeUpdateConfig(),_enforce_geometry=False)
    h=np.zeros((len(f.anchors),len(p)));h[:,:9]=f.state_jacobian;h[:,9:39]=h[:,:3]@j.tag_jacobian(measurement.node)
    noise=np.diag(np.diag(f.r_prior_m2)/f.robust_weights)
    k=np.linalg.solve(h@p@h.T+noise,h@p).T
    from test_c2_contact_raw_gain import constraint
    structural,_=structural_heading_constraints(old.rotations,j.tag_jacobian(measurement.node))
    c=np.vstack((grouped_heading_constraints(old.rotations),structural,constraint(j,(0,1))))
    k,_=project_augmented_gain(k,p,c,tuple(range(39,len(p))))
    error=k@f.innovations_m;a=np.eye(len(p))-k@h;expected=a@p@a.T+k@noise@k.T
    reset=np.eye(len(p))
    for i in range(10):reset[9+3*i:12+3*i,9+3*i:12+3*i]=_so3_right_jacobian(error[9+3*i:12+3*i])
    expected=reset@expected@reset.T
    calls=[];real=module.project_contact_gain
    def record(*args):calls.append(1);return real(*args)
    monkeypatch.setattr(module,'project_contact_gain',record)
    assert j.update_ranges(measurement,anchors_m=ANCHORS,clock=CLOCK,reference_epoch_s=0.,protected_contact_sides=(0,1)).accepted
    assert len(calls)==1
    np.testing.assert_allclose(j.contacts.covariance(j.tangent()),expected,atol=2e-12)
    np.linalg.cholesky(j.contacts.covariance(j.tangent()))


def test_numerical_zero_is_not_small_physical_sensitivity_cutoff():
    j=heading_owner();jac=np.zeros((3,30));jac[0,3:6]=1e-18*j.state.rotations[1,2,:]
    _,audit=structural_heading_constraints(j.state.rotations,jac)
    assert audit['sensitive_groups']==['torso']


def test_default_explicit_disabled_exact_parity_and_required_mode():
    default=heading_owner();disabled=heading_owner(raw_structural_heading=False)
    for j in (default,disabled):assert j.update_ranges(row(j,'BSF31CC'),anchors_m=ANCHORS,clock=CLOCK,reference_epoch_s=0.).accepted
    np.testing.assert_array_equal(default.state.rotations,disabled.state.rotations)
    np.testing.assert_array_equal(default.state.root.vector,disabled.state.root.vector)
    np.testing.assert_array_equal(default.state.covariance,disabled.state.covariance)
    j=owner()
    from biospur_fusion.c2_uwb_root_world.articulated_contact import ArticulatedContactFilter
    with pytest.raises(ValueError,match='requires conditional'):
        ArticulatedContactFilter(j.state.root,j.base,geometry=j.geometry,hinges={},
            embedding=j.embedding,wear_yaw=j.wear_yaw,chest_vertical_m=.08,
            pelvis_mount_sensor_from_segment=j.mount,raw_structural_heading=True)


def test_consistent_recurrent_ten_node_evidence_retains_collective_drift_correction():
    j=heading_owner(raw_structural_heading=True);initial=j.state
    accepted={node:0 for node in NODE_TO_SEGMENT}
    for cycle in range(3):
        for node in NODE_TO_SEGMENT:
            # Repeated coherent small discrepancies are not latched out by
            # structural authority, even without any support/contact state.
            measurement=replace(row(j,node),sequence=cycle+1,sweep=cycle+1)
            assert j.update_ranges(measurement,anchors_m=ANCHORS,clock=CLOCK,reference_epoch_s=0.).accepted
            accepted[node]+=1
            np.linalg.cholesky(j.state.covariance)
    assert set(accepted.values())=={3}
    assert np.linalg.norm(j.state.root.position_m-initial.root.position_m)>1e-3
    assert np.linalg.norm(j.state.rotations-initial.rotations)>1e-4
    invariant(initial.rotations,j.state.rotations)


@pytest.mark.parametrize('rotations,jac',[(np.zeros((10,3,3)),np.zeros((3,30))),
    (np.tile(np.eye(3),(10,1,1)),np.zeros((3,29))),
    (np.tile(np.eye(3),(10,1,1)),np.full((3,30),np.nan))])
def test_structural_helper_rejects_invalid_inputs(rotations,jac):
    with pytest.raises(ValueError):structural_heading_constraints(rotations,jac)
