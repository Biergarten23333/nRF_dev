import numpy as np
import pytest
from dataclasses import replace

from biospur_fusion.root_r3.models import ImuSample
from biospur_fusion.c2_uwb_root_world import causal_update_transaction as tx
from test_c2_authoritative_articulated_fusion import (
    _engine_and_packet,
    _epoch,
    _packet_with_nodes,
)


def _fixture(count=10, **kwargs):
    engine, packet = _engine_and_packet(clock_owner_sha256="a" * 64, **kwargs)
    if count != 10:
        packet = _packet_with_nodes(packet, count)
    return engine, packet, _epoch(engine, packet)


def _state(engine):
    temporal = engine.pose._CausalArticulatedPose__hinge_temporal_owner
    return (
        engine.root.publication_token().digest,
        engine.pose.publication_token().digest,
        engine.robust.snapshot(),
        temporal._state_bytes(),
    )


def test_prepare_is_state_inert_and_commit_is_one_shot():
    engine, packet, epoch = _fixture()
    before = _state(engine)
    prepared = engine.prepare_admission(packet, epoch)
    assert _state(engine) == before
    assert prepared.root_candidate_m.flags.writeable is False
    assert all(not value.flags.writeable for value in prepared.correction_candidate.values())
    observation = prepared.root_observation
    transaction_observation = prepared.causal_transaction.root_observation
    assert observation is not transaction_observation
    assert transaction_observation is prepared.causal_transaction.root_plan.observation
    assert observation.root_position_m.flags.writeable is False
    assert observation.covariance_m2.flags.writeable is False
    assert observation.root_position_m is not transaction_observation.root_position_m
    assert observation.covariance_m2 is not transaction_observation.covariance_m2
    for field in (
        "measurement_time_s", "availability_time_s", "tag_id", "anchors",
        "quality_state", "frame_valid", "physical_point_valid", "source_sequence",
    ):
        assert getattr(observation,field)==getattr(transaction_observation,field)
    assert observation.root_position_m.tobytes()==transaction_observation.root_position_m.tobytes()
    assert observation.covariance_m2.tobytes()==transaction_observation.covariance_m2.tobytes()
    result = engine.commit_admission(prepared)
    assert result.accepted and _state(engine) != before
    with pytest.raises(RuntimeError, match="CONSUMED"):
        engine.commit_admission(prepared)


def test_disjoint_engines_prepare_identical_candidates_and_reject_cross_owner():
    left, left_packet, left_epoch = _fixture()
    right, right_packet, right_epoch = _fixture()
    a = left.prepare_admission(left_packet, left_epoch)
    b = right.prepare_admission(right_packet, right_epoch)
    assert a.public_candidate_digest == b.public_candidate_digest
    assert a.root_observation is not b.root_observation
    assert a.root_observation.root_position_m is not b.root_observation.root_position_m
    with pytest.raises(RuntimeError, match="INVALID_OR_CONSUMED"):
        left.commit_admission(b)
    assert _state(left) == _state(_fixture()[0])


@pytest.mark.parametrize("field", ("packet_digest", "epoch_digest"))
def test_packet_and_epoch_binding_tamper_rejects(field):
    engine, packet, epoch = _fixture()
    prepared = engine.prepare_admission(packet, epoch)
    before = _state(engine)
    tampered = replace(prepared, **{field: "0" * 64})
    with pytest.raises(RuntimeError, match="STALE"):
        engine.commit_admission(tampered)
    assert _state(engine) == before


@pytest.mark.parametrize("owner", ("root", "pose", "robust"))
def test_stale_owner_revision_rejects_before_mutation(owner):
    engine, packet, epoch = _fixture()
    prepared = engine.prepare_admission(packet, epoch)
    if owner == "root":
        token = engine.root.publication_token()
        seconds = token.time_s + 0.001
        engine.add_imu(ImuSample(
            seconds, seconds, np.array([0.0, 0.0, 9.80665]), np.eye(3), 999_999
        ))
    elif owner == "pose":
        engine.pose.reset_hinge_continuity(1)
    else:
        engine.robust.revision += 1
    before = _state(engine)
    with pytest.raises(RuntimeError, match="STALE"):
        engine.commit_admission(prepared)
    assert _state(engine) == before


def test_rejected_plan_cannot_commit_and_u1_runs_once(monkeypatch):
    calls = []
    original = tx.evaluate_candidate_transition

    def counted(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(tx, "evaluate_candidate_transition", counted)
    engine, packet, epoch = _fixture(nominal_displacement=1e-9)
    before = _state(engine)
    prepared = engine.prepare_admission(packet, epoch)
    assert calls == [1] and _state(engine) == before
    assert prepared.prepared_result is not None
    assert prepared.root_observation is None
    with pytest.raises(RuntimeError, match="REJECTED"):
        engine.commit_admission(prepared)
    assert _state(engine) == before


def test_every_exposed_observation_field_is_digest_bound_to_root_plan():
    import biospur_fusion.c2_uwb_root_world.authoritative_articulated_fusion as module

    engine, packet, epoch = _fixture()
    prepared = engine.prepare_admission(packet, epoch)
    observation = prepared.root_observation
    mutations = (
        replace(observation,measurement_time_s=observation.measurement_time_s+1e-6),
        replace(observation,availability_time_s=observation.availability_time_s+1e-6),
        replace(observation,root_position_m=observation.root_position_m+1e-6),
        replace(observation,covariance_m2=observation.covariance_m2*1.001),
        replace(observation,tag_id=observation.tag_id+"_tampered"),
        replace(observation,anchors=tuple(reversed(observation.anchors))),
        replace(observation,quality_state=observation.quality_state+"_tampered"),
        replace(observation,frame_valid=not observation.frame_valid),
        replace(observation,physical_point_valid=not observation.physical_point_valid),
        replace(observation,source_sequence=observation.source_sequence+1),
    )
    before = _state(engine)
    for changed in mutations:
        position=np.asarray(changed.root_position_m,float).copy()
        covariance=np.asarray(changed.covariance_m2,float).copy()
        position.setflags(write=False); covariance.setflags(write=False)
        changed=replace(changed,root_position_m=position,covariance_m2=covariance)
        candidate=replace(prepared,root_observation=changed,public_candidate_digest="")
        candidate=replace(
            candidate,
            public_candidate_digest=module._prepared_admission_candidate_digest(candidate),
        )
        assert candidate.public_candidate_digest != prepared.public_candidate_digest
        with pytest.raises(RuntimeError,match="ROOT_OBSERVATION"):
            engine.commit_admission(candidate)
        assert _state(engine)==before


@pytest.mark.parametrize("failure_owner", ("robust", "pose", "root"))
def test_apply_failure_rolls_back_every_owner(monkeypatch, failure_owner):
    engine, packet, epoch = _fixture()
    prepared = engine.prepare_admission(packet, epoch)
    target = {
        "robust": (engine.robust, "_apply_prevalidated_commit"),
        "pose": (engine.pose, "_apply_prevalidated_install"),
        "root": (engine.root, "_apply_prevalidated_position"),
    }[failure_owner]
    original = getattr(*target)

    def apply_then_fail(ticket):
        original(ticket)
        raise RuntimeError("INJECTED_AFTER_" + failure_owner.upper())

    monkeypatch.setattr(*target, apply_then_fail)
    before = _state(engine)
    with pytest.raises(RuntimeError, match="INJECTED_AFTER"):
        engine.commit_admission(prepared)
    assert _state(engine) == before


def test_root_only_prepare_has_zero_articulated_solver(monkeypatch):
    engine, packet, epoch = _fixture(count=1)
    import biospur_fusion.c2_uwb_root_world.authoritative_articulated_fusion as module

    monkeypatch.setattr(
        module, "solve_articulated_ranges",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("IK called")),
    )
    prepared = engine.prepare_admission(packet, epoch)
    assert prepared.root_only
    assert engine.commit_admission(prepared).accepted


def test_admit_wrapper_matches_explicit_prepare_commit_bytes():
    old, old_packet, old_epoch = _fixture()
    new, new_packet, new_epoch = _fixture()
    expected = old.admit(old_packet, old_epoch)
    prepared = new.prepare_admission(new_packet, new_epoch)
    actual = new.commit_admission(prepared)
    assert (actual.accepted, actual.reason, actual.sequence) == (
        expected.accepted, expected.reason, expected.sequence
    )
    assert actual.root_position_m.tobytes() == expected.root_position_m.tobytes()
    assert actual.root_covariance_m2.tobytes() == expected.root_covariance_m2.tobytes()
    assert actual.pose_token_digest == expected.pose_token_digest
    assert tuple(actual.trusted_nodes) == tuple(expected.trusted_nodes)
    for node in actual.node_position_m:
        assert actual.node_position_m[node].tobytes() == expected.node_position_m[node].tobytes()
    for segment in actual.segment_correction_rotvec:
        assert (
            actual.segment_correction_rotvec[segment].tobytes()
            == expected.segment_correction_rotvec[segment].tobytes()
        )
    assert _state(old) == _state(new)


def test_accepted_result_materialization_failure_is_prepare_only(monkeypatch):
    engine, packet, epoch = _fixture()
    before = _state(engine)

    def fail(**_kwargs):
        raise RuntimeError("INJECTED_RESULT_MATERIALIZATION")

    monkeypatch.setattr(engine, "_materialize_result", fail)
    with pytest.raises(RuntimeError, match="RESULT_MATERIALIZATION"):
        engine.prepare_admission(packet, epoch)
    assert _state(engine) == before


def test_commit_performs_no_result_work_after_first_mutation(monkeypatch):
    engine, packet, epoch = _fixture()
    prepared = engine.prepare_admission(packet, epoch)
    mutated = {"value": False}
    original_apply = engine.robust._apply_prevalidated_commit
    original_pose_token = engine.pose.publication_token

    def first_apply(ticket):
        original_apply(ticket)
        mutated["value"] = True

    def token_guard():
        assert not mutated["value"], "publication token read after mutation"
        return original_pose_token()

    def forbidden(*_args, **_kwargs):
        assert not mutated["value"], "result helper called after mutation"
        raise AssertionError("unexpected result helper call during commit")

    monkeypatch.setattr(engine.robust, "_apply_prevalidated_commit", first_apply)
    monkeypatch.setattr(engine.pose, "publication_token", token_guard)
    monkeypatch.setattr(engine, "_materialize_result", forbidden)
    import biospur_fusion.c2_uwb_root_world.authoritative_articulated_fusion as module
    monkeypatch.setattr(module, "corrected_proxy_points", forbidden)
    result = engine.commit_admission(prepared)
    assert result is prepared.prepared_result and result.accepted
