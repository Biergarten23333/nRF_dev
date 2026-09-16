import numpy as np
import pytest
from types import SimpleNamespace
import ast
import inspect
import textwrap
import hashlib
import pickle
from dataclasses import replace

from biospur_fusion.root_r3.estimator import CausalDelayedRootFilter,RootFilterConfig
from biospur_fusion.root_r3.models import PositionObservation,RootState,SystemMode
from biospur_fusion.root_r3 import estimator as root_estimator
from biospur_fusion.c2_uwb_root_world.causal_update_guard import (
    CandidateKind,ReachabilityClass,ReachabilityEnvelope,
)
from biospur_fusion.c2_uwb_root_world.causal_update_transaction import (
    commit_causal_update_transaction,execute_causal_update_transaction,
    prepare_causal_update_transaction,
)
from biospur_fusion.c2_uwb_root_world import causal_update_transaction as transaction_module
from biospur_fusion.c2_uwb_calibration import causal_articulated_pose as pose_module
from biospur_fusion.c2_uwb_calibration.causal_articulated_pose import CausalArticulatedPose
from biospur_fusion.c2_uwb_calibration.articulated_range import SEGMENTS

def _root():
    return CausalDelayedRootFilter(RootState(0.,np.zeros(9),np.eye(9)*.1),
        RootFilterConfig(fixed_lag_s=1.,nis_limit_3d=1e9),inertial=False)

def _cross_covariance_root():
    vector=np.array([0.,0.,0.,.4,-.2,.1,.01,-.02,.03])
    covariance=np.eye(9)*.2
    covariance[0,3]=covariance[3,0]=.04
    covariance[1,7]=covariance[7,1]=-.03
    covariance[2,8]=covariance[8,2]=.02
    assert np.linalg.eigvalsh(covariance).min()>0.
    return CausalDelayedRootFilter(RootState(0.,vector,covariance),
        RootFilterConfig(fixed_lag_s=1.,nis_limit_3d=1e9),inertial=False)

def _obs(position=.01):
    return PositionObservation(.05,.1,np.array([position,0.,0.]),np.eye(3)*.1,"tag",(0,1,2,3))

def _envelope(displacement=1.,joint_step=10.):
    return ReachabilityEnvelope(ReachabilityClass.NOMINAL,displacement,10.,100.,joint_step,100.,1000.,
        1.,1.,1.,.01,2,10.,1e8,"synthetic U2 fixture limits")

def _owner_bytes(root):
    return pickle.dumps(root._prepare_position_rollback(),protocol=5)

def _pose(monkeypatch):
    def projector(_base,correction):
        knee_left=float(correction["shank_left"][0])
        return correction,{"post_projection_all_inside_rom":True,
            "fk_direction_residual_maximum_deg":0.,
            "joint":{name:{"post_projection_signed_deg":np.degrees(knee_left) if name=="knee_left" else 0.} for name in
                ("elbow_left","elbow_right","knee_left","knee_right")}}
    monkeypatch.setattr(pose_module,"project_hinge_corrections",projector)
    monkeypatch.setattr(pose_module,"corrected_proxy_points",lambda _b,_c,_g:{
        "ankle_left":np.array([0.,.1,-.9]),"ankle_right":np.array([0.,-.1,-.9])})
    pose=CausalArticulatedPose(action_start_s=0.,action_stop_s=1.,
        rotations_at_fraction=lambda _:{segment:np.eye(3) for segment in SEGMENTS},
        geometry=SimpleNamespace(),hinge_projector=projector)
    base={segment:np.eye(3) for segment in SEGMENTS}
    for timer_us in (35_000,40_000,45_000):
        pose.sample(timer_us*1000*1e-9,source_node="pelvis",source_boot_epoch=7,
            previous_source_timer_us=timer_us-5_000,source_timer_us=timer_us,
            previous_source_global_ns=(timer_us-5_000)*1000,
            source_global_ns=timer_us*1000,
            source_clock_mapping_digest="a"*64,
            previous_base_rotations_world=base,
            current_base_rotations_world=base)
    return pose

def _source_binding():
    return {"source_node":"pelvis","source_boot_epoch":7,
        "previous_timer_us":40_000,"current_timer_us":45_000,
        "previous_global_ns":40_000_000,"current_global_ns":45_000_000,
        "source_clock_mapping_digest":"a"*64}

def _projection(knee_left=0.):
    return {"post_projection_all_inside_rom":True,
        "fk_direction_residual_maximum_deg":0.,
        "joint":{name:{"post_projection_signed_deg":np.degrees(knee_left) if name=="knee_left" else 0.}
                 for name in ("elbow_left","elbow_right","knee_left","knee_right")}}

def test_root_only_accept_commits_once_without_pose():
    root=_root(); result=execute_causal_update_transaction(root=root,observation=_obs(),
        kind=CandidateKind.ROOT_POSITION,nominal_envelope=_envelope())
    assert result.root_committed and not result.pose_committed and not result.rejection_recorded
    assert root.current_state.time_s==.1 and root.health_snapshot()["tags"]["tag"]["accepted"]==1

@pytest.mark.parametrize("received_at_committed_horizon",[False,True])
def test_authoritative_position_commit_is_p_only_despite_cross_covariance(
        received_at_committed_horizon):
    observation=_obs(.3)
    unrestricted=_cross_covariance_root()
    unrestricted_plan=(
        unrestricted.prepare_received_position_at_measurement_horizon(observation)
        if received_at_committed_horizon
        else unrestricted.prepare_position(
            observation,processing_time_s=observation.availability_time_s)
    )
    initial=_cross_covariance_root().current_state.vector
    assert unrestricted_plan.measurement_candidate.vector[3:9].tobytes()!=initial[3:9].tobytes()

    root=_cross_covariance_root(); before=_owner_bytes(root)
    prepared=prepare_causal_update_transaction(
        root=root,observation=observation,kind=CandidateKind.ROOT_POSITION,
        nominal_envelope=_envelope(),
        received_at_committed_horizon=received_at_committed_horizon)
    assert _owner_bytes(root)==before
    result=commit_causal_update_transaction(prepared)
    assert result.root_committed
    assert np.linalg.norm(prepared.root_ticket.decision.applied_position_delta_m)>0.
    assert np.linalg.norm(prepared.root_ticket.decision.availability_applied_position_delta_m)>0.
    assert root.current_state.vector[:3].tobytes()!=initial[:3].tobytes()
    assert root.current_state.vector[3:9].tobytes()==initial[3:9].tobytes()
    assert prepared.root_ticket.decision.availability_applied_velocity_delta_mps==pytest.approx(0.)

@pytest.mark.parametrize("received_at_committed_horizon",[False,True])
def test_p_only_guard_rejection_leaves_cross_covariance_owner_numeric_state_inert(
        received_at_committed_horizon):
    root=_cross_covariance_root()
    vector_before=root.current_state.vector.tobytes()
    covariance_before=root.current_state.covariance.tobytes()
    prepared=prepare_causal_update_transaction(
        root=root,observation=_obs(.3),kind=CandidateKind.ROOT_POSITION,
        nominal_envelope=_envelope(1e-6),
        received_at_committed_horizon=received_at_committed_horizon)
    result=commit_causal_update_transaction(prepared)
    assert not result.root_committed and result.rejection_recorded
    assert root.current_state.vector.tobytes()==vector_before
    assert root.current_state.covariance.tobytes()==covariance_before

def test_guard_reject_preserves_prediction_journal_and_records_once():
    root=_root(); before_snapshots=tuple(root._snapshots); result=execute_causal_update_transaction(
        root=root,observation=_obs(.2),kind=CandidateKind.ROOT_POSITION,
        nominal_envelope=_envelope(1e-6))
    assert not result.root_committed and result.rejection_recorded
    assert len(root._snapshots)==len(before_snapshots)
    assert root._snapshots[0].state.vector.tobytes()==before_snapshots[0].state.vector.tobytes()
    assert root.health_snapshot()["tags"]["tag"]["rejected"]==1

def test_root_only_rejects_pose_payload_and_precommit_failure_is_atomic():
    root=_cross_covariance_root(); before=_owner_bytes(root)
    with pytest.raises(RuntimeError,match="boom"):
        execute_causal_update_transaction(root=root,observation=_obs(),kind=CandidateKind.ROOT_POSITION,
            nominal_envelope=_envelope(),_precommit_hook=lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    assert _owner_bytes(root)==before

def test_p_only_commit_exception_rolls_back_cross_covariance_owner_bytes(monkeypatch):
    root=_cross_covariance_root()
    prepared=prepare_causal_update_transaction(
        root=root,observation=_obs(.3),kind=CandidateKind.ROOT_POSITION,
        nominal_envelope=_envelope())
    before=_owner_bytes(root)
    original=CausalDelayedRootFilter._apply_prevalidated_position
    def apply_then_raise(owner,ticket):
        original(owner,ticket)
        raise RuntimeError("after root apply")
    monkeypatch.setattr(CausalDelayedRootFilter,"_apply_prevalidated_position",apply_then_raise)
    with pytest.raises(RuntimeError,match="after root apply"):
        commit_causal_update_transaction(prepared)
    assert _owner_bytes(root)==before

def test_prepared_p_only_rollback_clone_is_detached_from_cross_covariance_owner():
    root=_cross_covariance_root()
    prepared=prepare_causal_update_transaction(
        root=root,observation=_obs(.3),kind=CandidateKind.ROOT_POSITION,
        nominal_envelope=_envelope())
    before=_owner_bytes(root)
    prepared.root_rollback.last_force[0]=123.
    prepared.root_rollback.last_rotation[0,0]=456.
    prepared.root_rollback.snapshots.clear()
    assert _owner_bytes(root)==before

def test_stale_root_ticket_rejects_duplicate_commit():
    root=_root(); plan=root.prepare_position(_obs()); ticket=root._prevalidate_position_plan(plan)
    root._apply_prevalidated_position(ticket)
    with pytest.raises(RuntimeError,match="STALE"):
        root._prevalidate_position_plan(plan)

def test_articulated_accept_commits_root_pose_and_temporal_once(monkeypatch):
    root=_root(); pose=_pose(monkeypatch); correction={segment:np.zeros(3) for segment in SEGMENTS}
    before=pose.install_count
    temporal=pose._CausalArticulatedPose__hinge_temporal_owner
    temporal_before=(temporal._history_bytes(),temporal._CausalHingeTemporalOwner__revision)
    result=execute_causal_update_transaction(root=root,observation=_obs(),
        kind=CandidateKind.ARTICULATED_IK,nominal_envelope=_envelope(),pose=pose,
        correction_at_measurement=correction,hinge_projection_at_source=_projection(),
        native200_source_binding=_source_binding())
    assert result.root_committed and result.pose_committed
    assert pose.install_count==before+1 and pose.publication_token().latest_sample_s==pytest.approx(.045)
    assert temporal._CausalHingeTemporalOwner__revision==temporal_before[1]+1
    assert len(temporal._CausalHingeTemporalOwner__history)==1
    rebased=temporal._CausalHingeTemporalOwner__history[0]
    assert rebased.source_timer_us==45_000 and rebased.continuity_generation==1
    assert rebased.qdot_rad_s is None and rebased.qddot_rad_s2 is None
    states=[]
    for timer_us in (105_000,110_000,115_000):
        base={segment:np.eye(3) for segment in SEGMENTS}
        sample=pose.sample(timer_us*1e-6,source_node="pelvis",source_boot_epoch=7,
            source_timer_us=timer_us,source_global_ns=timer_us*1_000,
            previous_source_timer_us=timer_us-5_000,
            previous_source_global_ns=(timer_us-5_000)*1_000,
            source_clock_mapping_digest="a"*64,
            previous_base_rotations_world=base,
            current_base_rotations_world=base)
        states.append(sample.hinge_temporal.validity.name)
    assert temporal._CausalHingeTemporalOwner__revision==temporal_before[1]+4
    assert states==["RESET_SEQUENCE_GAP","WARMUP_QDDOT","QUALIFIED"]


def test_source_owned_pose_after_late_uwb_availability_keeps_source_chronology(monkeypatch):
    pose = _pose(monkeypatch)
    correction = {segment: np.zeros(3) for segment in SEGMENTS}
    pose.install(
        correction, measurement_time_s=0.0465, availability_time_s=0.100,
    )
    before_generation = pose.hinge_continuity_generation
    base = {segment: np.eye(3) for segment in SEGMENTS}
    sample = pose.sample(
        0.050, source_node="pelvis", source_boot_epoch=7,
        previous_source_timer_us=45_000, source_timer_us=50_000,
        previous_source_global_ns=45_000_000,
        source_global_ns=50_000_000,
        source_clock_mapping_digest="a" * 64,
        previous_base_rotations_world=base,
        current_base_rotations_world=base,
    )
    assert sample.time_s == pytest.approx(0.050)
    assert pose.latest_availability_s == pytest.approx(0.100)
    assert pose.publication_token().latest_sample_s == pytest.approx(0.050)
    assert pose.hinge_continuity_generation == before_generation

def test_guard_uses_target_schedule_not_fixed_origin(monkeypatch):
    root=_root(); pose=_pose(monkeypatch); correction={segment:np.zeros(3) for segment in SEGMENTS}
    correction["shank_left"]=np.array([1.,0.,0.])
    pose_before=pose.transition_snapshot(); temporal=pose._CausalArticulatedPose__hinge_temporal_owner
    history_before=temporal._history_bytes()
    result=execute_causal_update_transaction(root=root,observation=_obs(),
        kind=CandidateKind.ARTICULATED_IK,nominal_envelope=_envelope(joint_step=.001),
        pose=pose,correction_at_measurement=correction,
        hinge_projection_at_source=_projection(1.),native200_source_binding=_source_binding())
    assert not result.pose_committed and result.rejection_recorded
    assert pose.transition_snapshot()["target_correction"]["shank_left"].tobytes()==pose_before["target_correction"]["shank_left"].tobytes()
    assert temporal._history_bytes()==history_before

def test_direct_hinge_uses_authenticated_native200_history(monkeypatch):
    pose=_pose(monkeypatch); correction={segment:np.zeros(3) for segment in SEGMENTS}
    correction["shank_left"]=np.array([.3,0,0])
    plan=pose.prepare_guarded_install(correction,measurement_time_s=.05,availability_time_s=.1,
        projection_at_source=_projection(.3),source_binding=_source_binding())
    evidence=plan.direct_hinge
    assert evidence.source_time_s==pytest.approx(.045)
    assert evidence.source_timer_us==45_000
    assert evidence.previous_source_timer_us==40_000
    assert type(evidence.rom_valid) is bool and type(evidence.fk_valid) is bool
    assert evidence.joint_step_maximum_rad.shape==(4,) and evidence.joint_step_maximum_rad.flags.writeable is False
    assert evidence.joint_rate_maximum_rad_s[2]>0.

def test_stale_temporal_snapshot_blocks_pose_plan(monkeypatch):
    pose=_pose(monkeypatch); correction={segment:np.zeros(3) for segment in SEGMENTS}
    base={segment:np.eye(3) for segment in SEGMENTS}
    plan=pose.prepare_guarded_install(correction,measurement_time_s=.05,availability_time_s=.1,
        projection_at_source=_projection(),source_binding=_source_binding())
    pose.sample(.05,source_node="pelvis",source_boot_epoch=7,
        source_timer_us=50_000,source_global_ns=50_000_000,
        previous_source_timer_us=45_000,previous_source_global_ns=45_000_000,
        source_clock_mapping_digest="a"*64,
        previous_base_rotations_world=base,current_base_rotations_world=base)
    with pytest.raises(RuntimeError,match="STALE"):
        pose._prevalidate_install_plan(plan)

def test_root_only_leaves_existing_pose_bytes_stable(monkeypatch):
    pose=_pose(monkeypatch); before=(pose.transition_snapshot(),pose.install_count,pose.publication_token())
    execute_causal_update_transaction(root=_root(),observation=_obs(),
        kind=CandidateKind.ROOT_POSITION,nominal_envelope=_envelope())
    after=(pose.transition_snapshot(),pose.install_count,pose.publication_token())
    assert before[1:]==after[1:]
    for side in SEGMENTS:
        assert before[0]["target_correction"][side].tobytes()==after[0]["target_correction"][side].tobytes()

def test_commit_methods_contain_only_assignment_operations():
    for method in (
        CausalDelayedRootFilter._apply_prevalidated_position,
        CausalDelayedRootFilter._rollback_prevalidated_position,
        CausalArticulatedPose._apply_prevalidated_install,
        CausalArticulatedPose._rollback_prevalidated_install,
    ):
        tree=ast.parse(textwrap.dedent(inspect.getsource(method)))
        assert not any(isinstance(node,(ast.Call,ast.Raise,ast.If,ast.Try)) for node in ast.walk(tree))

def test_root_plan_is_deep_readonly_and_digest_rejects_replacement():
    root=_root(); plan=root.prepare_position(_obs())
    with pytest.raises(ValueError): plan.measurement_candidate.vector[0]=9
    changed=replace(plan,measurement_candidate=RootState(plan.measurement_candidate.time_s,
        plan.measurement_candidate.vector+1,plan.measurement_candidate.covariance))
    with pytest.raises(RuntimeError,match="STALE"):
        root._prevalidate_position_plan(changed)

def test_typed_root_plan_digest_is_reference_identical_and_all_fields_bound():
    root=_root(); plan=root.prepare_position(_obs())
    assert len(PositionObservation.__dataclass_fields__)==10
    owner=hashlib.sha256()
    root_estimator._root_hash_update(owner,(plan.base_revision,plan.observation,
        plan.processing_time_s,plan.decision,plan.imu_prediction,
        plan.measurement_candidate,plan.snapshots,plan.health,plan.anchor_health,
        plan.recovery_good,plan.mode,plan.last_observation_measurement_s,
        plan.last_accepted_measurement_s,plan.last_accepted_availability_s,
        plan.last_availability_s,plan.causal_state_digest))
    assert plan.digest==owner.hexdigest()==root_estimator._root_plan_digest(plan)
    edge_index=next(i for i,row in enumerate(plan.snapshots) if row.incoming_edge is not None)
    edge=plan.snapshots[edge_index].incoming_edge
    changed_edge=replace(edge,input_owner=edge.input_owner+"_tampered")
    changed_snapshots=list(plan.snapshots)
    changed_snapshots[edge_index]=replace(changed_snapshots[edge_index],incoming_edge=changed_edge)
    mutations=(
        replace(plan,base_revision=plan.base_revision+1),
        replace(plan,observation=replace(plan.observation,tag_id="tampered")),
        replace(plan,processing_time_s=plan.processing_time_s+1),
        replace(plan,decision=replace(plan.decision,reason="tampered")),
        replace(plan,imu_prediction=RootState(plan.imu_prediction.time_s,
            plan.imu_prediction.vector+1,plan.imu_prediction.covariance)),
        replace(plan,measurement_candidate=RootState(plan.measurement_candidate.time_s,
            plan.measurement_candidate.vector+1,plan.measurement_candidate.covariance)),
        replace(plan,snapshots=tuple(changed_snapshots)),
        replace(plan,health=plan.health+(("tampered",0,0,0,None),)),
        replace(plan,anchor_health=plan.anchor_health+((7,0,0,0,None),)),
        replace(plan,recovery_good=plan.recovery_good+1),
        replace(plan,mode=SystemMode.TIME_INVALID),
        replace(plan,last_observation_measurement_s=plan.last_observation_measurement_s+1),
        replace(plan,last_accepted_measurement_s=0.),
        replace(plan,last_accepted_availability_s=0.),
        replace(plan,last_availability_s=plan.last_availability_s+1),
    )
    for mutation in mutations:
        with pytest.raises(RuntimeError,match="STALE"):
            root._prevalidate_position_plan(mutation)

def test_pose_plan_nested_payload_and_planned_derivative_tamper_reject(monkeypatch):
    pose=_pose(monkeypatch); correction={segment:np.zeros(3) for segment in SEGMENTS}
    correction["shank_left"]=np.array([.3,0,0]); plan=pose.prepare_guarded_install(
        correction,measurement_time_s=.05,availability_time_s=.1,
        projection_at_source=_projection(.3),source_binding=_source_binding())
    with pytest.raises(TypeError): plan.target_correction["shank_left"]=np.zeros(3)
    with pytest.raises(ValueError): plan.target_correction["shank_left"][0]=9
    changed_evidence=replace(plan.direct_hinge,joint_step_maximum_rad=np.zeros(4))
    with pytest.raises(RuntimeError,match="STALE"):
        pose._prevalidate_install_plan(replace(plan,direct_hinge=changed_evidence))
    changed_target={key:value.copy() for key,value in plan.target_correction.items()}
    changed_target["shank_left"][0]+=1
    with pytest.raises(RuntimeError,match="STALE"):
        pose._prevalidate_install_plan(replace(plan,target_correction=changed_target))

def test_public_commit_ticket_surface_is_absent(monkeypatch):
    root=_root(); pose=_pose(monkeypatch)
    assert not hasattr(root,"prevalidate_position_plan") and not hasattr(root,"commit_position_ticket")
    assert not hasattr(pose,"prevalidate_install_plan") and not hasattr(pose,"commit_install_ticket")

def test_u1_is_called_exactly_once(monkeypatch):
    calls=[]; original=transaction_module.evaluate_candidate_transition
    def counted(*args,**kwargs): calls.append(1); return original(*args,**kwargs)
    monkeypatch.setattr(transaction_module,"evaluate_candidate_transition",counted)
    execute_causal_update_transaction(root=_root(),observation=_obs(),
        kind=CandidateKind.ROOT_POSITION,nominal_envelope=_envelope())
    assert calls==[1]

def test_legacy_add_position_matches_reference_bytes():
    old=_root(); new=_root(); observation=_obs()
    expected=old._add_position_reference(observation); actual=new.add_position(observation)
    assert expected.reason==actual.reason and expected.nis==actual.nis
    assert old.current_state.vector.tobytes()==new.current_state.vector.tobytes()
    assert old.current_state.covariance.tobytes()==new.current_state.covariance.tobytes()

def test_invalid_future_and_articulated_requirements_fail_closed():
    root=_root()
    future=PositionObservation(.2,.1,np.zeros(3),np.eye(3),"tag",(0,1,2,3))
    with pytest.raises(ValueError):
        execute_causal_update_transaction(root=root,observation=future,
            kind=CandidateKind.ROOT_POSITION,nominal_envelope=_envelope())
    with pytest.raises(ValueError,match="REQUIRES_POSE"):
        execute_causal_update_transaction(root=root,observation=_obs(),
            kind=CandidateKind.ARTICULATED_IK,nominal_envelope=_envelope())

def test_repeated_rejections_are_fixed_memory():
    root=_root(); initial_events=len(root._constraint_events)
    for index in range(10_000):
        t=(index+1)*.001
        observation=PositionObservation(t,t,np.array([.2,0,0]),np.eye(3)*.1,"tag",(0,1,2,3))
        execute_causal_update_transaction(root=root,observation=observation,
            kind=CandidateKind.ROOT_POSITION,nominal_envelope=_envelope(1e-6))
    assert len(root._constraint_events)==initial_events and len(root._snapshots)==1
    assert root.health_snapshot()["tags"]["tag"]["rejected"]==10_000
