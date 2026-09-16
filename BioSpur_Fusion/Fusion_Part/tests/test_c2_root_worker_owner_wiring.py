from dataclasses import asdict,replace

import numpy as np
import pytest

from biospur_fusion.c2_uwb_calibration.adaptive_nodes import AdaptiveNodeTrustConfig
from biospur_fusion.c2_uwb_calibration.direct_body_shadow_ab import DirectNodeLinkClock
from biospur_fusion.c2_uwb_root_world.causal_update_guard import ReachabilityClass,ReachabilityEnvelope
from biospur_fusion.c2_uwb_root_world.root_worker_owner_wiring import (
    BoundGroupPacket,OwnerBoundCoordinator,PoseTagLinkOwner,RangeInformationOwner,
    ReferenceOwnerBundle,execute_direct_reference)
from biospur_fusion.c2_uwb_root_world.u0 import UwbRow
from biospur_fusion.root_r3.estimator import RootFilterConfig
from biospur_fusion.root_r3.models import RootState


ANCHORS=np.array([[1.2,.1,.2],[-.2,1.3,.1],[.1,-.2,1.4],[-1.1,-1.2,-1.0],
                  [2.1,.2,.1],[.2,2.2,.3],[.3,.2,2.3],[-2.1,-2.2,-2.3]])


def fixture(anchor_delta=0.,pose_hash="1"*64):
    anchors=ANCHORS.copy();anchors[0,0]+=anchor_delta
    config=RootFilterConfig(.31,.51,.004,1e12,.051,.2,.181,.361,6,1e-12)
    initial=RootState(.05,np.array([.02,.01,.03,.01,-.01,.005,0,0,0]),np.eye(9)*.09)
    clocks={f"N{i}":DirectNodeLinkClock(f"N{i}",1000.+i*.01,i*10.,0,0,1_000_000) for i in range(10)}
    delay=np.linspace(.001,.008,8);tag_delay=.003
    weights={node:np.linspace(.71,.99,8)-(i*.001) for i,node in enumerate(sorted(clocks))}
    info=RangeInformationOwner(.083,.42,weights,"sealed U5B provisional quality/information owner")
    rows=[];geometry=[]
    qualities=tuple(range(91,99));tround=tuple(range(100,900,100))
    for i,node in enumerate(sorted(clocks)):
        clock=clocks[node];offset=np.array([.04+.001*i,-.025+.0005*i,.018-.0003*i])
        velocity=np.array([.012,-.007,.004])
        measured=[]
        for anchor in range(8):
            query=clock.link_time_ns(event_boot_epoch=0,strobe_us=60_000,t_round_us=tround[anchor])
            pose_time=int(query//5_000_000*5_000_000)
            geometry.append(PoseTagLinkOwner(node,anchor,query,pose_time,offset,velocity,
                pose_time//5_000_000,17,pose_hash))
            link_dt=(query-np.median([clock.link_time_ns(event_boot_epoch=0,strobe_us=60_000,t_round_us=x) for x in tround]))*1e-9
            point=initial.vector[:3]+offset+link_dt*initial.vector[3:6]
            measured.append(int(round((np.linalg.norm(anchors[anchor]-point)+delay[anchor]+tag_delay)*1000)))
        rows.append(UwbRow(node,0,1,1,60_000,100_000,tuple(range(8)),tuple(measured),tround,qualities,0xff))
    envelope=ReachabilityEnvelope(ReachabilityClass.NOMINAL,1.1,9.1,101.,1.1,11.,101.,.21,.21,1.1,.021,2,1.1,9e7,
        "sealed U1 root-only guard fixture policy")
    owner=ReferenceOwnerBundle(config,True,initial,anchors,clocks,delay,tag_delay,tuple(geometry),info,envelope,
        AdaptiveNodeTrustConfig(4,1e6,3.5,.25,.25,.75),"explicit complete RootFilterConfig owner",
        "sealed initial RootState owner","sealed canonical anchor-layout owner",
        "sealed per-node common-clock owner; strobe+t_round/2","U1_ROOT_POSITION_V1")
    return owner,BoundGroupPacket(owner.digest,tuple(rows))


def test_owner_bound_coordinator_matches_direct_public_reference_exactly():
    owner,packet=fixture();candidate=OwnerBoundCoordinator(owner).process(packet)
    reference=execute_direct_reference(owner,packet)
    assert (candidate.decision,candidate.root_reason,candidate.candidate_rank,candidate.link_count,candidate.guard_calls)==(
        reference.decision,reference.root_reason,reference.candidate_rank,reference.link_count,reference.guard_calls)
    assert candidate.link_count==80 and candidate.guard_calls==1
    for name in ("state","covariance"):
        np.testing.assert_allclose(getattr(candidate,name),getattr(reference,name),rtol=0,atol=1e-12)
    assert candidate.candidate_condition==pytest.approx(reference.candidate_condition,abs=1e-12)
    for left,right in zip(candidate.factors,reference.factors):
        for name in ("state_jacobian","sensor_r_m2","r_prior_m2","s_prior_m2"):
            np.testing.assert_allclose(getattr(left,name),getattr(right,name),rtol=0,atol=1e-12)
        assert left.prior_nis==pytest.approx(right.prior_nis,abs=1e-12)
        assert left.rank==right.rank==3 and left.condition==pytest.approx(right.condition,abs=1e-12)


def test_every_bound_owner_change_changes_digest():
    owner,_=fixture();changed_anchor,_=fixture(anchor_delta=.001);changed_pose,_=fixture(pose_hash="2"*64)
    assert len({owner.digest,changed_anchor.digest,changed_pose.digest})==3
    changed_config=replace(owner,root_config=replace(owner.root_config,fixed_lag_s=.201),digest="")
    changed_initial=replace(owner,initial_state=RootState(.05,owner.initial_state.vector+1e-4,owner.initial_state.covariance),digest="")
    changed_envelope=replace(owner,nominal_envelope=replace(owner.nominal_envelope,maximum_root_displacement_m=1.2),digest="")
    assert len({owner.digest,changed_config.digest,changed_initial.digest,changed_envelope.digest})==4


def test_stale_or_tampered_packet_rejects_before_state_mutation():
    owner,packet=fixture();other,_=fixture(anchor_delta=.001);coordinator=OwnerBoundCoordinator(other)
    before=coordinator.root.publication_token()
    with pytest.raises(ValueError,match="stale"):coordinator.process(packet)
    after=coordinator.root.publication_token()
    np.testing.assert_array_equal(before.state.vector,after.state.vector)
    np.testing.assert_array_equal(before.state.covariance,after.state.covariance)
    forged=BoundGroupPacket(other.digest,packet.rows);object.__setattr__(forged,"digest","0"*64)
    with pytest.raises(ValueError,match="digest"):coordinator.process(forged)


def test_missing_extra_and_zero_placeholder_owners_fail_closed():
    owner,_=fixture();values={key:value for key,value in owner.__dict__.items() if key!="digest"}
    missing=dict(values);missing.pop("guard_policy")
    with pytest.raises(TypeError):ReferenceOwnerBundle(**missing)
    with pytest.raises(TypeError):ReferenceOwnerBundle(**values,extra="bad")
    with pytest.raises(ValueError,match="zero/degenerate"):
        replace(owner,anchors_m=np.zeros((8,3)),digest="")
    with pytest.raises(ValueError,match="inventory"):
        replace(owner,pose_links=owner.pose_links[:-1],digest="")


def test_reference_uses_measured_round_trip_nonzero_geometry_and_explicit_config():
    owner,packet=fixture()
    assert owner.inertial is True and owner.root_config.fixed_lag_s==.2
    assert all(any(value!=0 for value in row.t_round_us) for row in packet.rows)
    assert all(np.linalg.norm(item.offset_world_m)>0 for item in owner.pose_links)
    assert all(np.linalg.norm(item.offset_velocity_world_mps)>0 for item in owner.pose_links)
    assert all(item.pose_time_ns<item.query_time_ns for item in owner.pose_links)


def test_initial_state_is_bundle_owned_readonly_and_caller_mutation_independent():
    vector=np.array([.02,.01,.03,.01,-.01,.005,0,0,0]);covariance=np.eye(9)*.09
    source=RootState(.05,vector,covariance);owner,_=fixture()
    replacement=replace(owner,initial_state=source,digest="");digest=replacement.digest
    vector[:]=999;covariance[:]=777
    assert replacement.digest==digest
    assert not replacement.initial_state.vector.flags.writeable
    assert not replacement.initial_state.covariance.flags.writeable
    assert np.max(replacement.initial_state.vector)<1
    with pytest.raises(ValueError):replacement.initial_state.vector[0]=1
    with pytest.raises(ValueError):replacement.initial_state.covariance[0,0]=1


@pytest.mark.parametrize("field",("vector","covariance","anchors","weights","pose"))
def test_forced_nested_owner_tamper_rejects_before_root_mutation(field):
    owner,packet=fixture();coordinator=OwnerBoundCoordinator(owner)
    before=coordinator.root.publication_token()
    if field=="vector":object.__setattr__(owner.initial_state,"vector",owner.initial_state.vector+1)
    elif field=="covariance":object.__setattr__(owner.initial_state,"covariance",owner.initial_state.covariance*2)
    elif field=="anchors":object.__setattr__(owner,"anchors_m",owner.anchors_m+1e-3)
    elif field=="weights":
        changed=dict(owner.range_information.weights_by_node);changed["N0"]=changed["N0"]*.9
        object.__setattr__(owner.range_information,"weights_by_node",changed)
    else:object.__setattr__(owner.pose_links[0],"source_revision",999)
    with pytest.raises(ValueError,match="digest"):coordinator.process(packet)
    after=coordinator.root.publication_token()
    np.testing.assert_array_equal(before.state.vector,after.state.vector)
    np.testing.assert_array_equal(before.state.covariance,after.state.covariance)
    assert before.revision==after.revision and before.digest==after.digest


def test_stale_original_packet_rejects_against_new_changed_owner():
    owner,packet=fixture();changed=replace(owner,root_config=replace(owner.root_config,fixed_lag_s=.201),digest="")
    coordinator=OwnerBoundCoordinator(changed);before=coordinator.root.publication_token()
    with pytest.raises(ValueError,match="stale"):coordinator.process(packet)
    after=coordinator.root.publication_token()
    np.testing.assert_array_equal(before.state.vector,after.state.vector)
    assert before.revision==after.revision and before.digest==after.digest


def test_make_root_owns_disjoint_readonly_state_arrays():
    owner,_=fixture();root=owner.make_root();state=root.current_state
    assert state is not owner.initial_state
    assert state.vector is not owner.initial_state.vector
    assert state.covariance is not owner.initial_state.covariance
    assert not np.shares_memory(state.vector,owner.initial_state.vector)
    assert not np.shares_memory(state.covariance,owner.initial_state.covariance)
    assert not state.vector.flags.writeable and not state.covariance.flags.writeable
    original_vector=state.vector.copy();original_covariance=state.covariance.copy()
    with pytest.raises(ValueError):owner.initial_state.vector[0]=999
    with pytest.raises(ValueError):owner.initial_state.covariance[0,0]=999
    np.testing.assert_array_equal(root.current_state.vector,original_vector)
    np.testing.assert_array_equal(root.current_state.covariance,original_covariance)


@pytest.mark.parametrize("field",("vector","covariance"))
def test_forced_tamper_before_construction_rejects_before_root_creation(field):
    owner,_=fixture()
    if field=="vector":object.__setattr__(owner.initial_state,"vector",owner.initial_state.vector+1)
    else:object.__setattr__(owner.initial_state,"covariance",owner.initial_state.covariance*2)
    with pytest.raises(ValueError,match="digest"):owner.make_root()
