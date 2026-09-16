from dataclasses import replace

import numpy as np
import pytest

from biospur_fusion.c2_coupled_progressive.continuous_frontend import (
    ActionInterval,
    CONTINUOUS_FRONTEND_SCHEMA,
    ContinuousClockOwner,
    NodeClockBinding,
)
from biospur_fusion.c2_coupled_progressive.continuous_group_epoch_owner import (
    CONTINUOUS_HINGE_RETENTION_CONTRACT,
    AuthoritativeContinuousHistoryOwner,
    OwnedNative200HistoryFrame,
    _digest_payload,
)
from biospur_fusion.c2_coupled_progressive.continuous_native200_bridge import (
    AuthoritativeNative200HistoryBridge,
    AuthoritativeNative200PosePublication,
)
from biospur_fusion.c2_coupled_progressive.continuous_stage2_adapter import (
    EventRegionOwner,
    adapt_verified_record,
)
from biospur_fusion.ingest.events import EventStatus, RecordType, TypedEvent
from biospur_fusion.root_r3.models import ImuSample

from test_c2_authoritative_articulated_fusion import _engine_and_packet
from test_c2_continuous_group_epoch_owner import _owned_frame, _uwb


def _fixture():
    engine, packet = _engine_and_packet(
        hinge_temporal_retention_contract=CONTINUOUS_HINGE_RETENTION_CONTRACT,
    )
    timer_us = 130_408
    frame = _owned_frame(engine, packet, timer_us, 0, contact=True)
    mapping = engine.native200_clock_mapping_owner(
        node=frame.node, clock_owner_sha256=frame.clock_owner_sha256,
    )
    acc_raw = np.array([2048, 0, 0], dtype=int)
    owned_imu = ImuSample(
        frame.source_global_ns * 1e-9,
        frame.imu_sample.availability_time_s,
        acc_raw.astype(float) / 2048.0 * 9.80665 + np.array([0.013, -0.007, 0.002]),
        frame.imu_sample.rotation_world_from_sensor,
        frame.imu_sample.source_sequence,
        frame.imu_sample.m1_valid,
        frame.imu_sample.m1_reset,
    )
    clock = ContinuousClockOwner(
        CONTINUOUS_FRONTEND_SCHEMA,
        (NodeClockBinding(
            frame.node,
            frame.boot_epoch,
            "B306_TIMER2",
            frame.clock_mapping_digest,
            mapping.a_ns_per_us,
            mapping.b_ns,
            frame.clock_owner_sha256,
            frame.clock_source_sha256,
        ),),
    )
    record = TypedEvent(
        frame.node,
        frame.boot_epoch,
        RecordType.IMU,
        frame.imu_sample.source_sequence,
        frame.source_timer_us,
        frame.source_global_ns,
        100,
        999,
        {
            "base_timer2_us": frame.timer2_base_us,
            "delta_us": frame.source_timer_us - frame.timer2_base_us,
            "acc_raw": acc_raw.tolist(),
            "gyro_raw": [0, 0, 0],
        },
        {"decoded_owner": frame.imu_owner_sha256},
        EventStatus.DECODED,
        frame.raw_provenance,
    )
    action = ActionInterval(
        0,
        "00_initial_still",
        frame.source_global_ns - 1_000_000,
        frame.source_global_ns + 1_000_000,
    )
    adapted = adapt_verified_record(
        record,
        availability_global_ns=round(frame.imu_sample.availability_time_s * 1e9),
        region_owner=EventRegionOwner(action=action),
        clock_owner=clock,
    )
    publication = AuthoritativeNative200PosePublication(
        source_event_id=adapted.event_id,
        node=frame.node,
        boot_epoch=frame.boot_epoch,
        timer2_base_us=frame.timer2_base_us,
        source_timer_us=frame.source_timer_us,
        source_global_ns=frame.source_global_ns,
        availability_global_ns=adapted.availability_global_ns,
        clock_domain="B306_TIMER2",
        clock_mapping_digest=frame.clock_mapping_digest,
        clock_owner_sha256=frame.clock_owner_sha256,
        clock_source_sha256=frame.clock_source_sha256,
        publication_revision=frame.publication_revision,
        source_frame=frame.source_frame,
        action_id=frame.action_id,
        raw_provenance=frame.raw_provenance,
        raw_acc_lsb=tuple(int(value) for value in acc_raw),
        imu_sample=owned_imu,
        base_rotations_world=frame.base_rotations_world,
        offsets_world_m=frame.offsets_world_m,
        offset_velocities_world_mps=frame.offset_velocities_world_mps,
        normals_world=frame.normals_world,
        joints_relative_world_m=frame.joints_relative_world_m,
        point_constraints_world_m=frame.point_constraints_world_m,
        imu_owner_sha256=frame.imu_owner_sha256,
        publication_owner_sha256=frame.publication_owner_sha256,
        base_pose_owner_digest=frame.base_pose_owner_digest,
        body_proxy_owner_sha256=frame.body_proxy_owner_sha256,
        contact_owner_digest=frame.contact_owner_digest,
        provenance="authoritative pose/FK/contact publisher",
    )
    history = AuthoritativeContinuousHistoryOwner(
        engine=engine,
        a_sigma_owner=packet.a_sigma_owner,
        b_sigma_owner=packet.b_sigma_owner,
        b_shadow_provenance="existing body-shadow geometry owner",
        history_provenance="source-owned native200 history",
        hinge_retention_contract=CONTINUOUS_HINGE_RETENTION_CONTRACT,
    )
    bridge = AuthoritativeNative200HistoryBridge(
        publication.imu_owner_sha256, publication.publication_owner_sha256,
    )
    return engine, history, adapted, publication, bridge


def _state_digest(engine, history):
    root = engine.root.publication_token()
    return _digest_payload({
        "root": (root.revision, root.time_s, root.digest),
        "pose": engine.pose.transition_snapshot(),
        "history": (history.revision, tuple(frame.digest for frame in history.frames)),
    })


def test_exact_bridge_binds_adapted_record_and_prepare_is_state_inert():
    engine, history, adapted, publication, bridge = _fixture()
    before = _state_digest(engine, history)
    bound = bridge.bind(adapted, publication)
    assert type(bound.payload_owner) is OwnedNative200HistoryFrame
    frame = bound.payload_owner
    assert frame.pose_publication_digest == publication.digest
    assert frame.raw_provenance == adapted.payload_owner.raw
    assert frame.timer2_base_us == adapted.imu_timer2.timer2_base_us
    assert frame.digest == OwnedNative200HistoryFrame(**{
        field: getattr(frame, field)
        for field in frame.__dataclass_fields__ if field != "digest"
    }).digest
    prepared = history.prepare_native200(bound)
    assert prepared.frame.digest == frame.digest
    assert _state_digest(engine, history) == before


def test_bridge_accepts_label_free_full_session_envelope_by_sensor_identity():
    _engine, _history, adapted, publication, bridge = _fixture()
    full_session = replace(
        adapted,
        action_index=-1,
        action_id="FULL_SESSION_CONTINUOUS_00_TO_19",
        region_id="FULL_SESSION_CONTINUOUS_00_TO_19",
    )
    bound = bridge.bind(full_session, publication)
    assert bound.action_id == "FULL_SESSION_CONTINUOUS_00_TO_19"
    assert bound.payload_owner.action_id == "00_initial_still"
    assert bound.payload_owner.pose_publication_digest == publication.digest


def test_bridge_rejects_unrecognized_label_mismatch():
    _engine, _history, adapted, publication, bridge = _fixture()
    mislabeled = replace(adapted, action_index=2, action_id="02_t_pose")
    with pytest.raises(ValueError, match="ownership mismatch"):
        bridge.bind(mislabeled, publication)


def test_decoded_typed_event_directly_still_rejects_before_mutation():
    engine, history, adapted, _publication, _bridge = _fixture()
    before = _state_digest(engine, history)
    with pytest.raises(TypeError, match="owned history-frame"):
        history.prepare_native200(adapted)
    assert _state_digest(engine, history) == before


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("source_timer_us", 130_409),
        ("node", "WRONG_NODE"),
        ("action_id", "02_t_pose"),
        ("clock_owner_sha256", "0" * 64),
        ("imu_owner_sha256", "2" * 64),
        ("publication_owner_sha256", "1" * 64),
    ),
)
def test_bridge_rejects_identity_time_action_and_owner_mismatch_before_mutation(field, value):
    engine, history, adapted, publication, bridge = _fixture()
    before = _state_digest(engine, history)
    poisoned = replace(publication, **{field: value}, digest="")
    with pytest.raises(ValueError, match="ownership mismatch|source owner"):
        bridge.bind(adapted, poisoned)
    assert _state_digest(engine, history) == before


@pytest.mark.parametrize("poison", ("delta", "acceleration"))
def test_bridge_rejects_decoded_delta_or_acceleration_mismatch_before_mutation(poison):
    engine, history, adapted, publication, bridge = _fixture()
    record = adapted.payload_owner
    payload = dict(record.payload)
    if poison == "delta":
        payload["delta_us"] += 1
        match = "base/delta mismatch"
    else:
        payload["acc_raw"] = [2047, 0, 0]
        match = "raw acceleration differs"
    poisoned = replace(adapted, payload_owner=replace(record, payload=payload))
    before = _state_digest(engine, history)
    with pytest.raises(ValueError, match=match):
        bridge.bind(poisoned, publication)
    assert _state_digest(engine, history) == before


def test_publication_rejects_incomplete_inventory_and_freezes_input_output():
    _engine, history, adapted, publication, bridge = _fixture()
    incomplete = dict(publication.offsets_world_m)
    incomplete.pop(next(iter(incomplete)))
    with pytest.raises(ValueError, match="inventory mismatch"):
        replace(publication, offsets_world_m=incomplete, digest="")
    assert history.revision == 0

    mutable = {key: np.array(value, copy=True) for key, value in publication.offsets_world_m.items()}
    frozen_publication = replace(publication, offsets_world_m=mutable, digest="")
    first = next(iter(mutable))
    mutable[first][0] += 10.0
    assert frozen_publication.offsets_world_m[first][0] != mutable[first][0]
    with pytest.raises(ValueError):
        frozen_publication.offsets_world_m[first][0] = 0.0
    bound = bridge.bind(adapted, frozen_publication)
    with pytest.raises(ValueError):
        bound.payload_owner.base_rotations_world["pelvis"][0, 0] = 0.0


def test_publication_and_frame_digest_are_stable_and_uwb_is_unchanged():
    _engine, _history, adapted, publication, bridge = _fixture()
    twin = replace(publication, digest="")
    assert twin.digest == publication.digest
    assert (
        bridge.bind(adapted, twin).payload_owner.digest
        == bridge.bind(adapted, publication).payload_owner.digest
    )
    uwb = _uwb("BSFC200", 120_000_000)
    assert bridge.bind(uwb) is uwb
    with pytest.raises(TypeError, match="non-IMU"):
        bridge.bind(uwb, publication)
