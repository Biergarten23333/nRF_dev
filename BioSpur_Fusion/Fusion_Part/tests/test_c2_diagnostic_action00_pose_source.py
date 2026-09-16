from dataclasses import replace

import pytest

from biospur_fusion.c2_timing_contract import MAXIMUM_POSE_AGE_NS
from biospur_fusion.c2_coupled_progressive.continuous_frontend import (
    CONTINUOUS_FRONTEND_SCHEMA,
    ContinuousClockOwner,
    NodeClockBinding,
)
from biospur_fusion.c2_coupled_progressive.continuous_native200_bridge import (
    AuthoritativeNative200HistoryBridge,
)
from biospur_fusion.c2_uwb_root_world.diagnostic_action00_pose_source import (
    DiagnosticAction00StrictFloorPoseSource,
)
from biospur_fusion.ingest.events import RawByteProvenance
from biospur_fusion.root_r3.models import ImuSample

from test_c2_continuous_native200_bridge import _fixture


def _owned_source():
    engine, _history, event, publication, bridge = _fixture()
    frame = bridge.bind(event, publication).payload_owner
    mapping = engine.native200_clock_mapping_owner(
        node=frame.node, clock_owner_sha256=frame.clock_owner_sha256,
    )
    clock = ContinuousClockOwner(CONTINUOUS_FRONTEND_SCHEMA, (NodeClockBinding(
        frame.node, frame.boot_epoch, "B306_TIMER2", frame.clock_mapping_digest,
        mapping.a_ns_per_us, mapping.b_ns, frame.clock_owner_sha256,
        frame.clock_source_sha256,
    ),))
    source = DiagnosticAction00StrictFloorPoseSource(
        bridge=bridge, clock_owner=clock,
    )
    return source, frame, bridge, clock


def _next_frame(frame, clock):
    timer = frame.source_timer_us + 5_000
    global_ns = clock.binding_for(frame.node).global_ns(timer)
    sample = ImuSample(
        global_ns * 1e-9, global_ns * 1e-9 + 0.0001,
        frame.imu_sample.specific_force_sensor_mps2,
        frame.imu_sample.rotation_world_from_sensor,
        (frame.imu_sample.source_sequence + 1) & 0xFFFF,
    )
    return replace(
        frame, timer2_base_us=frame.timer2_base_us + 5_000,
        source_timer_us=timer, source_global_ns=global_ns,
        publication_revision=frame.publication_revision + 1,
        source_frame=frame.source_frame + 1, imu_sample=sample,
        raw_provenance=RawByteProvenance(
            frame.raw_provenance.record_index + 1,
            frame.raw_provenance.start_offset + 32,
            frame.raw_provenance.end_offset + 32,
            "7" * 64,
            frame.raw_provenance.sample_index,
        ),
        pose_publication_digest="8" * 64, digest="",
    )


def test_bridge_owned_action00_frame_round_trip_and_strict_floor():
    source, frame, _bridge, _clock = _owned_source()
    source.accept_bound_frame(frame)
    selection = source.selection_for(
        "BSFC2CC", frame.source_global_ns + 1_000_000,
    )
    assert selection.frame is frame
    assert selection.offset.pose_time_ns == frame.source_global_ns
    assert selection.offset.pose_age_ns == 1_000_000
    assert selection.source_owner_digest == source.owner_digest
    assert source.product_ready is source.scientific_pass is False


def test_query_is_strictly_past_and_rejects_unknown_node_without_mutation():
    source, frame, _bridge, _clock = _owned_source()
    source.accept_bound_frame(frame)
    before = source.owner_bytes()
    with pytest.raises(ValueError, match="predates"):
        source.selection_for("BSFC2CC", frame.source_global_ns)
    with pytest.raises(ValueError, match="absent"):
        source.selection_for("UNKNOWN", frame.source_global_ns + 1)
    assert source.owner_bytes() == before


def test_replay_foreign_owner_and_non_action00_reject_byte_inertly():
    source, frame, _bridge, _clock = _owned_source()
    source.accept_bound_frame(frame)
    before = source.owner_bytes()
    with pytest.raises(ValueError, match="replay"):
        source.accept_bound_frame(frame)
    with pytest.raises(ValueError, match="source mismatch"):
        source.accept_bound_frame(replace(
            frame, publication_owner_sha256="0" * 64, digest="",
        ))
    with pytest.raises(ValueError, match="Action00"):
        source.accept_bound_frame(replace(frame, action_id="02_t_pose", digest=""))
    assert source.owner_bytes() == before


def test_selection_digest_rejects_tampering():
    source, frame, _bridge, _clock = _owned_source()
    source.accept_bound_frame(frame)
    selection = source.selection_for("BSFC2CC", frame.source_global_ns + 1)
    with pytest.raises(ValueError, match="digest mismatch"):
        replace(selection, digest="0" * 64)


def test_two_frames_select_latest_strict_past_and_reject_stale():
    source, first, _bridge, clock = _owned_source()
    second = _next_frame(first, clock)
    source.accept_bound_frame(first)
    source.accept_bound_frame(second)
    assert source.selection_for("BSFC2CC", second.source_global_ns).frame is first
    assert source.selection_for("BSFC2CC", second.source_global_ns + 1).frame is second
    with pytest.raises(ValueError, match="stale"):
        source.selection_for(
            "BSFC2CC", second.source_global_ns + int(MAXIMUM_POSE_AGE_NS) + 1,
        )


def test_foreign_clock_base_pose_and_availability_regression_are_byte_noops():
    source, first, _bridge, clock = _owned_source()
    source.accept_bound_frame(first)
    second = _next_frame(first, clock)
    before = source.owner_bytes()
    poisons = (
        replace(second, clock_mapping_digest="0" * 64, digest=""),
        replace(second, base_pose_owner_digest="0" * 64, digest=""),
    )
    for poison in poisons:
        with pytest.raises(ValueError, match="clock|base-pose|chronology"):
            source.accept_bound_frame(poison)
        assert source.owner_bytes() == before

    delayed_first = replace(first, imu_sample=replace(
        first.imu_sample,
        availability_time_s=first.imu_sample.measurement_time_s + 0.020,
    ), digest="")
    delayed_source = DiagnosticAction00StrictFloorPoseSource(
        bridge=_bridge, clock_owner=clock,
    )
    delayed_source.accept_bound_frame(delayed_first)
    before = delayed_source.owner_bytes()
    with pytest.raises(ValueError, match="chronology"):
        delayed_source.accept_bound_frame(_next_frame(delayed_first, clock))
    assert delayed_source.owner_bytes() == before


def test_repeated_or_lower_source_frame_rejects_byte_inertly():
    source, first, _bridge, clock = _owned_source()
    first = replace(first, source_frame=2, digest="")
    source.accept_bound_frame(first)
    before = source.owner_bytes()
    for source_frame in (2, 1):
        with pytest.raises(ValueError, match="chronology"):
            source.accept_bound_frame(replace(
                _next_frame(first, clock), source_frame=source_frame, digest="",
            ))
        assert source.owner_bytes() == before


def test_constructor_requires_typed_bridge_and_clock_owner():
    _source, frame, bridge, clock = _owned_source()
    with pytest.raises(TypeError, match="bridge"):
        DiagnosticAction00StrictFloorPoseSource(bridge=object(), clock_owner=clock)
    foreign = AuthoritativeNative200HistoryBridge("0" * 64, bridge.publication_owner_sha256)
    source = DiagnosticAction00StrictFloorPoseSource(bridge=foreign, clock_owner=clock)
    with pytest.raises(ValueError, match="source mismatch"):
        source.accept_bound_frame(frame)
