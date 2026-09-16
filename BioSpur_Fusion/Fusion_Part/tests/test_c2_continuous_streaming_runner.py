import hashlib
import io
import struct
import time
from dataclasses import replace

import numpy as np
import pytest

from biospur_fusion.c2_coupled_progressive.continuous_frontend import (
    ActionInterval,
    CAPTURE2_PROTOCOL_SLOTS,
    CONTINUOUS_FRONTEND_SCHEMA,
    ContinuousClockOwner,
    NodeClockBinding,
    SourceBoundGap,
)
from biospur_fusion.c2_coupled_progressive.continuous_native200_bridge import (
    AuthoritativeNative200HistoryBridge,
    AuthoritativeNative200PosePublication,
)
from biospur_fusion.c2_coupled_progressive.continuous_streaming_runner import (
    AuthorizedByteWindow,
    ContinuousSliceRunner,
    FrozenPublicationIndex,
    IncrementalV47WindowDecoder,
    StreamingSlicePreregistration,
    stream_authorized_windows,
)
from biospur_fusion.c2_coupled_progressive.continuous_uwb_owner import (
    Capture2ContinuousUwbCalibrationOwner,
)
from biospur_fusion.ingest.events import EventStatus, RawByteProvenance, RecordType, TypedEvent
from biospur_fusion.root_r3.models import ImuSample

from fusion_host_binary import HostFrame, KIND_IMU, KIND_UWB, encode_frame

from test_c2_continuous_group_epoch_owner import _owned_frame, _real_owner_fixture
from test_c2_authoritative_articulated_fusion import _engine_and_packet


def _sha(value):
    return hashlib.sha256(value).hexdigest()


def _imu_frame(timer_us, sequence):
    payload = struct.pack("<BBHQh", 7, 1, sequence, timer_us - 5_000, 0)
    payload += struct.pack("<Hhhhhhh", 5_000, 2048, 0, 0, 0, 0, 0)
    return encode_frame(HostFrame(KIND_IMU, 0xC2CC, sequence, 999, payload))


def _imu_batch_frame(timer_us, sequence, count=3):
    payload = struct.pack("<BBHQh", 7, count, sequence, timer_us - 5_000, 0)
    payload += b"".join(
        struct.pack("<Hhhhhhh", 5_000 + sample, 2048, sample, 0, 0, 0, 0)
        for sample in range(count)
    )
    return encode_frame(HostFrame(KIND_IMU, 0xC2CC, sequence, 999, payload))


def _uwb_frame(timer_us, sequence):
    payload = bytearray(184)
    struct.pack_into("<BBHII", payload, 0, 7, 1, 184, sequence, 999)
    struct.pack_into("<I", payload, 12, sequence)
    payload[16:21] = (123456).to_bytes(5, "little")
    struct.pack_into("<H", payload, 21, 1)
    struct.pack_into("<HH", payload, 24, 1200, 1000)
    payload[28:36] = bytes(range(8)); payload[36:44] = bytes(range(8))
    struct.pack_into("<8H", payload, 44, *([2000] * 8))
    struct.pack_into("<8H", payload, 60, *([100] * 8))
    payload[76:84] = bytes([100] * 8)
    struct.pack_into("<8h", payload, 84, *([0] * 8))
    payload[100] = 0xFF
    struct.pack_into("<QQ", payload, 102, timer_us + 1000, timer_us)
    return encode_frame(HostFrame(KIND_UWB, 0xC2CC, sequence, 999, bytes(payload)))


def _decode_chunks(encoded, cuts, *, first_record=10, event_cap=None):
    auth = AuthorizedByteWindow(
        "synthetic-v47", "a" * 64, 400, 400 + len(encoded), first_record,
        _sha(encoded), 0, 1_000_000_000,
        {"BSFC2CC:3": 7, "BSFC2CC:1": 7}, 512,
    )
    decoder = IncrementalV47WindowDecoder(auth, maximum_emitted_events=event_cap)
    events, cursor = [], 0
    for end in (*cuts, len(encoded)):
        if end > cursor:
            events.extend(decoder.feed(encoded[cursor:end], absolute_offset=400 + cursor))
            cursor = end
    decoder.finish()
    return tuple(events), decoder


def _decoder_state(decoder):
    return (
        bytes(decoder._pending), decoder._pending_start, decoder._next_offset,
        decoder._record_index, dict(decoder._last_timer_by_stream),
        decoder.emitted_events, decoder._window_hash.copy().digest(),
    )


def test_cursor_decoder_is_byte_exact_for_one_multi_uwb_and_imu_batch_all_splits():
    payloads = (
        _imu_frame(100_000, 1),
        _imu_frame(100_000, 1) + _imu_frame(105_000, 2) + _imu_frame(110_000, 3),
        _uwb_frame(100_000, 3),
        _uwb_frame(100_000, 3) + _uwb_frame(105_000, 4),
        _imu_batch_frame(100_000, 0xFFFF),
    )
    for payload in payloads:
        expected, expected_decoder = _decode_chunks(payload, ())
        delimiters = tuple(index for index, value in enumerate(payload) if value == 0)
        representative_splits = {
            1, 2, len(payload) // 2, len(payload) - 2, len(payload) - 1,
        }
        for delimiter in delimiters:
            representative_splits.update((delimiter - 1, delimiter, delimiter + 1))
        representative_splits = tuple(sorted(
            split for split in representative_splits if 0 < split < len(payload)
        ))
        for split in representative_splits:
            observed, decoder = _decode_chunks(payload, (split,))
            assert observed == expected
            assert _decoder_state(decoder) == _decoder_state(expected_decoder)
        observed, decoder = _decode_chunks(payload, representative_splits)
        assert observed == expected
        assert _decoder_state(decoder) == _decoder_state(expected_decoder)
        for row in expected:
            raw = row.raw
            local_start = raw.start_offset - 400
            local_end = raw.end_offset - 401
            assert raw.record_index == 11 + payload[:local_start].count(b"\0")
            assert raw.encoded_sha256 == _sha(payload[local_start:local_end])
            assert payload[local_end] == 0
    batch, _ = _decode_chunks(payloads[-1], ())
    assert [row.sequence for row in batch] == [0xFFFF, 0, 1]
    assert [row.raw.sample_index for row in batch] == [0, 1, 2]


def test_empty_delimiters_and_failure_boundaries_match_stream_contract():
    frame = _imu_frame(100_000, 1)
    payload = b"\0\0" + frame + b"\0\0"
    rows, _ = _decode_chunks(payload, (1, 2, len(payload) - 2))
    assert len(rows) == 1 and rows[0].raw.record_index == 11
    assert (rows[0].raw.start_offset, rows[0].raw.end_offset) == (
        402, 402 + len(frame),
    )

    auth = AuthorizedByteWindow(
        "synthetic", "a" * 64, 0, 2, 0, _sha(b"\x01\0"), 0, 1,
        {"BSFC2CC:3": 7}, 256,
    )
    invalid = IncrementalV47WindowDecoder(auth)
    with pytest.raises(ValueError, match="failed closed"):
        invalid.feed(b"\x01\0", absolute_offset=0)
    assert bytes(invalid._pending) == b"" and invalid._record_index == 1

    descending = _imu_frame(105_000, 1) + _imu_frame(100_000, 2)
    auth = replace(auth, end_offset=len(descending), window_sha256=_sha(descending))
    regressed = IncrementalV47WindowDecoder(auth)
    with pytest.raises(ValueError, match="failed closed"):
        regressed.feed(descending, absolute_offset=0)
    assert bytes(regressed._pending) == b"" and regressed._record_index == 2
    assert regressed._last_timer_by_stream == {"BSFC2CC:3": 100_000}

    good = _imu_frame(100_000, 1)
    auth = replace(auth, end_offset=len(good), window_sha256=_sha(good))
    offset = IncrementalV47WindowDecoder(auth)
    before = _decoder_state(offset)
    with pytest.raises(ValueError, match="not contiguous"):
        offset.feed(good, absolute_offset=1)
    assert _decoder_state(offset) == before

    batch = _imu_batch_frame(100_000, 1, count=2)
    capped = IncrementalV47WindowDecoder(
        replace(auth, end_offset=len(batch), window_sha256=_sha(batch)),
        maximum_emitted_events=1,
    )
    with pytest.raises(OverflowError, match="emitted-event capacity"):
        capped.feed(batch, absolute_offset=0)
    assert bytes(capped._pending) == b"" and capped.emitted_events == 0

    tail = IncrementalV47WindowDecoder(replace(
        auth, end_offset=len(good) - 1, window_sha256=_sha(good[:-1]),
    ))
    tail.feed(good[:-1], absolute_offset=0)
    with pytest.raises(ValueError, match="inside a COBS record"):
        tail.finish()


def test_large_cursor_batch_scales_linearly_with_fixed_pending():
    def run(count):
        payload = b"".join(_imu_frame(100_000 + 5_000 * index, index)
                           for index in range(count))
        started = time.perf_counter()
        rows, decoder = _decode_chunks(payload, (), first_record=0)
        return time.perf_counter() - started, len(rows), decoder.maximum_pending_bytes

    small_s, small_rows, small_pending = run(400)
    large_s, large_rows, large_pending = run(1_600)
    print(
        "cursor_scaling "
        f"small_records=400 small_s={small_s:.9f} "
        f"large_records=1600 large_s={large_s:.9f} "
        f"ratio={large_s / small_s:.6f} pending={large_pending}"
    )
    assert large_rows == 4 * small_rows
    assert large_s <= 6 * small_s + 0.05
    assert small_pending == large_pending == 0


def test_incremental_decoder_crosses_arbitrary_chunks_and_bounds_pending_memory():
    encoded = _imu_frame(100_000, 1) + _imu_frame(105_000, 2)
    auth = AuthorizedByteWindow(
        "synthetic-v47", "a" * 64, 400, 400 + len(encoded), 10,
        _sha(encoded), 0, 1_000_000_000, {"BSFC2CC:3": 7}, 256,
    )
    decoder = IncrementalV47WindowDecoder(auth)
    events = []
    cursor = 400
    for width in (1, 7, 3, 11, len(encoded)):
        chunk = encoded[cursor - 400:cursor - 400 + width]
        if not chunk:
            continue
        events.extend(decoder.feed(chunk, absolute_offset=cursor))
        cursor += len(chunk)
        if cursor == auth.end_offset:
            break
    if cursor < auth.end_offset:
        events.extend(decoder.feed(encoded[cursor - 400:], absolute_offset=cursor))
    decoder.finish()
    assert [row.sequence for row in events] == [1, 2]
    assert all(row.boot_epoch == 7 for row in events)
    assert events[0].raw.record_index == 11
    assert events[1].raw.end_offset == auth.end_offset
    assert decoder.maximum_pending_bytes <= auth.maximum_record_bytes

    poison = AuthorizedByteWindow(
        "synthetic-v47", "a" * 64, 0, 257, 0, _sha(b"x" * 257),
        0, 1_000_000_000, {"BSFC2CC:3": 0}, 256,
    )
    with pytest.raises(OverflowError, match="capacity"):
        IncrementalV47WindowDecoder(poison).feed(b"x" * 257, absolute_offset=0)


def test_two_acquired_windows_stream_incrementally_and_seek_over_gap():
    left = _imu_frame(100_000, 1)
    right = _imu_frame(105_000, 2)
    gap = b"gap bytes are deliberately not decoded"
    payload = left + gap + right
    windows = (
        AuthorizedByteWindow(
            "synthetic", "a" * 64, 0, len(left), 0, _sha(left),
            0, 1_000_000_000, {"BSFC2CC:3": 7}, 256,
        ),
        AuthorizedByteWindow(
            "synthetic", "a" * 64, len(left) + len(gap), len(payload), 1,
            _sha(right), 1_000_000_000, 2_000_000_000,
            {"BSFC2CC:3": 7}, 256,
        ),
    )
    emitted = []
    audit = stream_authorized_windows(
        io.BytesIO(payload), windows, chunk_bytes=7, transport_event_cap=8,
        on_event=lambda event, window: emitted.append((event, window)),
    )
    assert [event.sequence for event, _window in emitted] == [1, 2]
    assert [window for _event, window in emitted] == [windows[0], windows[1]]
    assert audit.opened_sources == 1
    assert audit.bytes_read == len(left) + len(right)
    assert audit.skipped_bytes == len(gap)
    assert audit.emitted_events == 2
    assert audit.maximum_pending_record_bytes < 256


def test_transport_event_cap_trips_during_emission_not_after_collection():
    payload = _imu_frame(100_000, 1) + _imu_frame(105_000, 2)
    window = AuthorizedByteWindow(
        "synthetic", "a" * 64, 0, len(payload), 0, _sha(payload),
        0, 1_000_000_000, {"BSFC2CC:3": 7}, 256,
    )
    emitted = []
    with pytest.raises(OverflowError, match="emitted-event capacity"):
        stream_authorized_windows(
            io.BytesIO(payload), (window,), chunk_bytes=len(payload),
            transport_event_cap=1,
            on_event=lambda event, _window: emitted.append(event),
        )
    assert emitted == []


def _timeline(action00_end, action02_start):
    rows = []
    for slot in CAPTURE2_PROTOCOL_SLOTS:
        if not slot.acquired:
            continue
        if slot.index == 0:
            start, end = 0, action00_end
        elif slot.index == 2:
            start, end = action02_start, action02_start + 1_000_000_000
        else:
            start = rows[-1].end_common_global_ns
            end = start + 1_000_000_000
        rows.append(ActionInterval(
            slot.index, slot.action_id, start, end, end_inclusive=slot.index == 19,
        ))
    return tuple(rows)


def _clock_owner(engine, frame):
    bindings = []
    for node, clock in sorted(engine.static.clocks.items()):
        if node == frame.node:
            mapping = engine.native200_clock_mapping_owner(
                node=node, clock_owner_sha256=frame.clock_owner_sha256,
            )
            digest = mapping.digest
            owner = frame.clock_owner_sha256
        else:
            digest = _sha(f"mapping:{node}".encode())
            owner = "b" * 64
        bindings.append(NodeClockBinding(
            node, clock.boot_epoch, "B306_TIMER2", digest,
            clock.a_ns_per_us, clock.b_ns, owner, "c" * 64,
        ))
    return ContinuousClockOwner(CONTINUOUS_FRONTEND_SCHEMA, tuple(bindings))


def _pose_publication(frame, event_id, availability_ns, imu):
    return AuthoritativeNative200PosePublication(
        event_id, frame.node, frame.boot_epoch, frame.timer2_base_us,
        frame.source_timer_us, frame.source_global_ns, availability_ns,
        "B306_TIMER2", frame.clock_mapping_digest, frame.clock_owner_sha256,
        frame.clock_source_sha256, frame.publication_revision, frame.source_frame,
        frame.action_id, frame.raw_provenance, (2048, 0, 0), imu, frame.base_rotations_world,
        frame.offsets_world_m, frame.offset_velocities_world_mps,
        frame.normals_world, frame.joints_relative_world_m,
        frame.point_constraints_world_m, frame.imu_owner_sha256,
        frame.publication_owner_sha256, frame.base_pose_owner_digest,
        frame.body_proxy_owner_sha256, frame.contact_owner_digest,
        "existing frozen native200 trajectory/FK/body-shadow/contact publishers",
    )


def _imu_record(frame):
    acc_raw = [2048, 0, 0]
    imu = ImuSample(
        frame.source_global_ns * 1e-9,
        (frame.source_global_ns + 100_000) * 1e-9,
        np.asarray(acc_raw, float) / 2048.0 * 9.80665,
        frame.imu_sample.rotation_world_from_sensor,
        frame.publication_revision,
    )
    raw = frame.raw_provenance
    record = TypedEvent(
        frame.node, frame.boot_epoch, RecordType.IMU, frame.publication_revision,
        frame.source_timer_us, frame.source_global_ns, 100, 999,
        {"base_timer2_us": frame.timer2_base_us,
         "delta_us": frame.source_timer_us - frame.timer2_base_us,
         "acc_raw": acc_raw, "gyro_raw": [0, 0, 0]}, {}, EventStatus.DECODED, raw,
    )
    event_id = f"v47:{raw.record_index}:{raw.sample_index}:{raw.start_offset}:{raw.end_offset}:{raw.encoded_sha256}"
    return record, _pose_publication(frame, event_id, frame.source_global_ns + 100_000, imu)


def _uwb_record(row, index):
    return TypedEvent(
        row.node, row.boot, RecordType.UWB, row.sequence, row.strobe_us,
        None, None, 999,
        {"node_ms": row.node_ms, "sweep": row.sweep, "identity": row.identity,
         "anchor_id": list(row.anchor_ids), "range_mm": list(row.ranges_mm),
         "t_round_us": list(row.t_round_us), "quality_percent": list(row.quality),
         "valid_mask": row.valid_mask, "frame_us": row.frame_us,
         "strobe_us": row.strobe_us}, {}, EventStatus.DECODED,
        RawByteProvenance(index, index * 32, index * 32 + 32, _sha(f"uwb:{index}".encode())),
    )


def _runner_fixture():
    group_owner, rows, _availability_ns = _real_owner_fixture()
    engine = group_owner._composition.engine
    history = group_owner._composition.history

    def clone_factory():
        fresh_owner, _fresh_rows, _fresh_availability = _real_owner_fixture()
        return fresh_owner._composition.engine, fresh_owner._composition.history

    group_owner._composition._clone_factory = clone_factory
    base_frame = history.frames[-1]
    clock_owner = _clock_owner(engine, base_frame)
    action00_end = 170_000_000
    action02_start = 180_000_000
    intervals = _timeline(action00_end, action02_start)
    gap = SourceBoundGap(
        "UNASSIGNED_INTER_ACTION_GAP", action00_end, action02_start,
        "00_initial_still", "02_t_pose", "action00-owner", "1" * 64,
        "action02-owner", "2" * 64,
    )
    seed = Capture2ContinuousUwbCalibrationOwner(
        clock_owner=clock_owner, action_intervals=intervals, source_gaps=(gap,),
        subowners=(("continuous-group", group_owner),),
    )
    records = []
    publications = {}
    _unused_engine, template_packet = _engine_and_packet()
    for revision, timer_us in enumerate(
        (160_408, 165_408, 170_408, 175_408, 180_408), start=6,
    ):
        frame = _owned_frame(engine, template_packet, timer_us, revision, contact=True)
        action_id = (
            "00_initial_still" if frame.source_global_ns < action00_end
            else "UNASSIGNED_INTER_ACTION_GAP" if frame.source_global_ns < action02_start
            else "02_t_pose"
        )
        frame = replace(frame, action_id=action_id, digest="")
        record, publication = _imu_record(frame)
        publications[publication.source_event_id] = publication
        if action_id != "UNASSIGNED_INTER_ACTION_GAP":
            records.append(record)
    records.extend(
        _uwb_record(replace(row, frame_us=row.strobe_us + 1_000), 100 + index)
        for index, row in enumerate(rows)
    )
    # One source-owned prior-action row arrives after Action02.  Its already
    # finalized bucket must remain diagnostic without creating a second group.
    records.append(_uwb_record(replace(rows[0], strobe_us=rows[0].strobe_us + 20), 200))
    auth = AuthorizedByteWindow(
        "synthetic-production-shaped", "a" * 64, 0, 10_000, 0, "b" * 64,
        0, 300_000_000,
        {f"{node}:1": engine.static.clocks[node].boot_epoch for node in engine.static.clocks}
        | {"BSFC2CC:3": base_frame.boot_epoch},
    )
    prereg = StreamingSlicePreregistration(
        (auth,), 100, 10_000, 120, 2 * 1024**3, 3,
        {"00_initial_still": 0, "01_neutral_sway": action00_end,
         "02_t_pose": action02_start}, action02_start,
        {"trajectory": "3" * 64, "clock": "4" * 64, "fk": "5" * 64,
         "body_shadow": "6" * 64, "contact": "7" * 64,
         "imu": "e" * 64, "publication": "f" * 64},
    )
    index = FrozenPublicationIndex(
        publications, "3" * 64, "4" * 64, "5" * 64, "6" * 64, "7" * 64,
    )
    bridge = AuthoritativeNative200HistoryBridge("e" * 64, "f" * 64)
    return ContinuousSliceRunner(
        seed=seed, clock_owner=clock_owner, bridge=bridge,
        publications=index, preregistration=prereg,
    ), records


def test_production_shaped_slice_uses_bridge_gap_marker_and_isolated_ab_parity():
    runner, records = _runner_fixture()
    result = runner.run_decoded(records)
    assert result.candidate_parity and len(result.candidate_digests_a) == 1
    assert result.branch_a.protocol_markers[0].action_id == "01_neutral_sway"
    assert result.branch_a.protocol_marker_and_gap_accounted
    assert result.branch_a.fixed_parameter_revision == 0
    assert result.branch_b.fixed_parameter_revision == 0
    assert result.branch_a.uwb_commit_enabled is False
    assert result.branch_b.uwb_commit_enabled is True
    assert result.branch_b.time_varying_state_revision == result.branch_a.time_varying_state_revision + 1
    assert result.partial_slice_finished is False
    gap_id = next(event_id for event_id in result.dispatched_event_ids if event_id.startswith("gap:"))
    action02_id = next(
        event_id for event_id in result.dispatched_event_ids
        if event_id in runner.publications.publications
        and runner.publications.publications[event_id].action_id == "02_t_pose"
    )
    delayed_action00_uwb = next(
        event_id for event_id in result.dispatched_event_ids if event_id.startswith("v47:200:")
    )
    assert result.dispatched_event_ids.index(gap_id) < result.dispatched_event_ids.index(action02_id)
    assert result.dispatched_event_ids.index(action02_id) < result.dispatched_event_ids.index(delayed_action00_uwb)
    assert dict(result.group_counters_a)["LATE_SEALED_BUCKET_ROW"] == 1
    assert result.group_counters_a == result.group_counters_b


def test_runner_poison_clock_owner_fails_before_seed_mutation():
    runner, records = _runner_fixture()
    before = runner.seed.snapshot()
    poisoned = replace(records[0], global_time_ns=records[0].global_time_ns + 1)
    with pytest.raises(ValueError, match="disagrees"):
        runner.run_decoded([poisoned])
    assert runner.seed.snapshot() == before


def test_runner_poison_publication_owner_fails_before_seed_mutation():
    runner, records = _runner_fixture()
    record = records[0]
    raw = record.raw
    event_id = (
        f"v47:{raw.record_index}:{raw.sample_index}:"
        f"{raw.start_offset}:{raw.end_offset}:{raw.encoded_sha256}"
    )
    poisoned = replace(
        runner.publications.publications[event_id],
        publication_owner_sha256="0" * 64,
        digest="",
    )
    publications = FrozenPublicationIndex(
        {**runner.publications.publications, event_id: poisoned},
        runner.publications.trajectory_sha256,
        runner.publications.clock_sha256,
        runner.publications.fk_owner_sha256,
        runner.publications.body_shadow_owner_sha256,
        runner.publications.contact_owner_sha256,
    )
    poisoned_runner = ContinuousSliceRunner(
        seed=runner.seed, clock_owner=runner.clock_owner, bridge=runner.bridge,
        publications=publications, preregistration=runner.preregistration,
    )
    before = runner.seed.snapshot()
    with pytest.raises(ValueError, match="bridge source owner"):
        poisoned_runner.run_decoded([record])
    assert runner.seed.snapshot() == before
