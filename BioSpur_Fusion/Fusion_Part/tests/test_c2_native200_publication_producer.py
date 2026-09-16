from dataclasses import replace

import numpy as np
import pytest

from biospur_fusion.c2_uwb_calibration.articulated_range import SEGMENTS
from biospur_fusion.c2_coupled_progressive.native200_publication_producer import (
    ACTION_KEYS,
    CONTACT_NOT_APPLIED_OWNER,
    Native200PublicationProducer,
    PublicationActionSource,
    accepted_pose_frame,
    accepted_pose_frames,
    array_sha256,
)
from biospur_fusion.c2_uwb_root_world.authoritative_articulated_fusion import (
    Native200ClockMappingOwner,
)
from biospur_fusion.ingest.events import EventStatus, RawByteProvenance, RecordType, TypedEvent

from test_c2_authoritative_articulated_fusion import _engine_and_packet


def _source(action, key, start, count=3):
    time_us = start + np.arange(count, dtype=np.int64) * 5_000
    boot = np.full(count, 7, dtype=np.int64)
    span = np.zeros(count, dtype=np.int64)
    acc = np.tile(np.array([9.82, 0.0, 0.0]), (count, 1))
    acc[0] = [9.81, 0.0, 0.0]
    if count > 1:
        acc[1] = [9.81965, 0.003, -0.004]
    sensor = np.tile(np.array([1.0, 0.0, 0.0, 0.0]), (count, 1))
    root_time = 12.0 + np.arange(count, dtype=float) * 0.005
    trajectory = {
        segment: {
            "time_root_s": root_time.copy(),
            "quat_world_segment_wxyz": sensor.copy(),
            "mask": np.ones(count, dtype=bool),
        }
        for segment in SEGMENTS
    }
    arrays = {
        "time_us": time_us,
        "derived_boot_epoch": boot,
        "contiguous_span_id": span,
        "acc_mps2": acc,
        "quat_world_sensor_wxyz": sensor,
    }
    return PublicationActionSource(
        action, key, time_us, boot, span, acc, sensor, trajectory,
        {name: array_sha256(value) for name, value in arrays.items()},
    )


def _fixture(count=3):
    engine, packet = _engine_and_packet()
    clock = Native200ClockMappingOwner(
        "BSFC2CC", "B306_TIMER2", 7, 1_000.0, 10_000.0, "a" * 64,
    )
    sources = {
        "00_initial_still": _source("00_initial_still", "00", 100_000, count),
        "02_t_pose": _source("02_t_pose", "01", 200_000, count),
    }
    producer = Native200PublicationProducer(
        sources=sources,
        clocks={action: clock for action in sources},
        alignment=np.eye(3),
        geometry=packet.b_shadow_owner.geometry,
        geometry_owner_digest="b" * 64,
        frontend_sha256="c" * 64,
        frontend_manifest_sha256="d" * 64,
        trajectory_sha256="e" * 64,
        clock_table_sha256="f" * 64,
        clock_source_sha256="1" * 64,
        publication_owner_sha256="2" * 64,
        body_rule_owners={"fk": "3" * 64, "normal": "4" * 64, "body": "5" * 64},
        frontend_manifest_schema="frontend-v1",
        frontend_reconstruction_role="CALIBRATED_CAPTURE_WIDE_POSTERIOR",
    )
    return producer, sources, clock, packet.b_shadow_owner.geometry


def _event(source, clock, frame, *, raw_acc=(2048, 0, 0), action="00_initial_still"):
    timer = int(source.time_us[frame])
    base = timer - 100
    raw = RawByteProvenance(frame, frame * 20, frame * 20 + 19, "6" * 64, frame)
    return TypedEvent(
        "BSFC2CC", 7, RecordType.IMU, frame, timer, clock.global_ns(timer), 10, 0,
        {"base_timer2_us": base, "delta_us": 100, "acc_raw": list(raw_acc), "gyro_raw": [0, 0, 0]},
        {}, EventStatus.DECODED, raw,
    )


def test_exact_publications_keep_raw_identity_separate_from_calibrated_force():
    producer, sources, clock, _ = _fixture()
    first = producer.publication_for_selected_pelvis_event(
        _event(sources["00_initial_still"], clock, 1),
        action_id="00_initial_still", availability_global_ns=clock.global_ns(105_000) + 1,
    )
    second = producer.publication_for_selected_pelvis_event(
        _event(sources["02_t_pose"], clock, 1, action="02_t_pose"),
        action_id="02_t_pose", availability_global_ns=clock.global_ns(205_000) + 1,
    )
    assert first.raw_acc_lsb == (2048, 0, 0)
    assert np.array_equal(first.imu_sample.specific_force_sensor_mps2, np.array([9.81965, 0.003, -0.004]))
    assert not np.array_equal(first.imu_sample.specific_force_sensor_mps2, np.array([9.80665, 0.0, 0.0]))
    assert (first.publication_revision, second.publication_revision) == (0, 1)
    assert first.base_pose_owner_digest == second.base_pose_owner_digest
    assert first.point_constraints_world_m == second.point_constraints_world_m == {}
    assert first.contact_owner_digest == second.contact_owner_digest == CONTACT_NOT_APPLIED_OWNER


def test_label_free_publication_resolves_action_from_hardware_tick():
    producer, sources, clock, _ = _fixture()
    event = _event(sources["02_t_pose"], clock, 1, action="wrong-label-is-ignored")
    assert producer.action_for_pelvis_event(event) == "02_t_pose"
    publication = producer.publication_for_pelvis_event(
        event, availability_global_ns=clock.global_ns(205_000) + 1,
    )
    assert publication.action_id == "02_t_pose"


@pytest.mark.parametrize("poison", ("tick", "boot", "span", "action", "raw"))
def test_exact_source_identity_rejects_poison(poison):
    producer, sources, clock, _ = _fixture()
    source = sources["00_initial_still"]
    event = _event(source, clock, 1)
    action = "00_initial_still"
    if poison == "tick":
        event = replace(event, node_timer_us=event.node_timer_us + 1, global_time_sigma_ns=None)
    elif poison == "boot":
        event = replace(event, boot_epoch=8)
    elif poison == "span":
        source.span.setflags(write=True); source.span[1] = 1; source.span.setflags(write=False)
    elif poison == "action":
        action = "01_marker"
    else:
        event = replace(event, payload={**event.payload, "acc_raw": [40000, 0, 0]})
    with pytest.raises(ValueError):
        producer.publication_for_selected_pelvis_event(
            event, action_id=action, availability_global_ns=clock.global_ns(105_000) + 1,
        )


def test_mask_alignment_hash_and_segment_time_fail_closed():
    source = _source("00_initial_still", "00", 100_000)
    hashes = dict(source.array_hashes); hashes["acc_mps2"] = "0" * 64
    with pytest.raises(ValueError, match="array binding"):
        replace(source, array_hashes=hashes)
    trajectory = {segment: dict(values) for segment, values in source.trajectory.items()}
    trajectory[SEGMENTS[0]]["time_root_s"] = trajectory[SEGMENTS[0]]["time_root_s"] + np.array([0.0, 1e-5, 0.0])
    with pytest.raises(ValueError, match="time ownership"):
        replace(source, trajectory=trajectory)
    producer, sources, clock, _ = _fixture()
    sources["00_initial_still"].trajectory[SEGMENTS[0]]["mask"].setflags(write=True)
    sources["00_initial_still"].trajectory[SEGMENTS[0]]["mask"][1] = False
    sources["00_initial_still"].trajectory[SEGMENTS[0]]["mask"].setflags(write=False)
    with pytest.raises(ValueError, match="masked"):
        producer.publication_for_selected_pelvis_event(
            _event(sources["00_initial_still"], clock, 1), action_id="00_initial_still",
            availability_global_ns=clock.global_ns(105_000) + 1,
        )
    with pytest.raises(ValueError, match="alignment"):
        Native200PublicationProducer(
            sources=sources, clocks={a: clock for a in sources}, alignment=np.diag([1.0, 1.0, -1.0]),
            geometry=_[0] if isinstance(_, tuple) else _, geometry_owner_digest="b" * 64,
            frontend_sha256="c" * 64, frontend_manifest_sha256="d" * 64,
            trajectory_sha256="e" * 64, clock_table_sha256="f" * 64,
            clock_source_sha256="1" * 64, publication_owner_sha256="2" * 64,
            body_rule_owners={"fk": "3" * 64}, frontend_manifest_schema="v1",
            frontend_reconstruction_role="role",
        )


def test_array_hash_grammar_and_action04_frame_rule_parity():
    from biospur_fusion.c2_coupled_progressive.frontend import _array_sha256
    from biospur_fusion.c2_uwb_calibration.antenna_los import rotation_from_wxyz
    from biospur_fusion.c2_uwb_calibration.articulated_range import corrected_proxy_points

    value = np.arange(12, dtype=np.float64).reshape(3, 4)
    assert array_sha256(value) == _array_sha256(value)
    _producer, sources, _clock, geometry = _fixture()
    source = sources["00_initial_still"]
    rotations, points, _normals = accepted_pose_frame(source.trajectory, 1, np.eye(3), geometry)
    expected_rotations = {
        segment: rotation_from_wxyz(source.trajectory[segment]["quat_world_segment_wxyz"][1])
        for segment in SEGMENTS
    }
    expected_points = corrected_proxy_points(
        expected_rotations, {segment: np.zeros(3) for segment in SEGMENTS}, geometry,
    )
    for key in rotations:
        assert np.array_equal(rotations[key], expected_rotations[key])
    for key in points:
        assert np.array_equal(points[key], expected_points[key])


@pytest.mark.parametrize("count", (1, 10, 16))
def test_accepted_pose_frames_is_byte_exact_ordered_and_nonaliasing(count):
    _producer, sources, _clock, geometry = _fixture(count + 1)
    source = sources["00_initial_still"]
    frames = tuple(range(1, count + 1))
    actual = accepted_pose_frames(source.trajectory, frames, np.eye(3), geometry)
    expected = tuple(
        accepted_pose_frame(source.trajectory, frame, np.eye(3), geometry)
        for frame in frames
    )
    assert len(actual) == count
    for row, oracle in zip(actual, expected):
        for left, right in zip(row, oracle):
            assert tuple(left) == tuple(right)
            assert all(np.array_equal(left[key], right[key]) for key in left)
    if count > 1:
        assert all(
            not np.shares_memory(actual[0][part][key], actual[1][part][key])
            for part in range(3) for key in actual[0][part]
        )


def test_prepared_pose_rows_bind_owner_record_revision_and_are_deeply_immutable():
    producer, sources, clock, _ = _fixture(4)
    records = tuple(_event(sources["00_initial_still"], clock, frame) for frame in (1, 2, 3))
    rows = producer._prepare_pelvis_record_rows(records)
    assert rows is not None and len(rows) == 3
    for row in rows:
        for mapping in (row.rotations, row.points, row.normals):
            with pytest.raises(TypeError):
                mapping["forged"] = np.zeros(3)
            for value in mapping.values():
                with pytest.raises(ValueError):
                    value.setflags(write=True)
    first = producer._publication_for_pelvis_event_with_row(
        records[0], availability_global_ns=clock.global_ns(105_000) + 1,
        prepared_row=rows[0],
    )
    oracle, oracle_sources, oracle_clock, _ = _fixture(4)
    expected = oracle.publication_for_pelvis_event(
        _event(oracle_sources["00_initial_still"], oracle_clock, 1),
        availability_global_ns=oracle_clock.global_ns(105_000) + 1,
    )
    assert first.digest == expected.digest
    with pytest.raises(ValueError, match="chronology reversed"):
        producer._publication_for_pelvis_event_with_row(
            records[0], availability_global_ns=clock.global_ns(105_000) + 1,
            prepared_row=rows[0],
        )
    foreign, _, _, _ = _fixture(4)
    with pytest.raises(RuntimeError, match="foreign, replayed, or stale"):
        foreign._publication_for_pelvis_event_with_row(
            records[0], availability_global_ns=clock.global_ns(105_000) + 1,
            prepared_row=rows[0],
        )


def test_prepared_pose_rows_fall_back_for_nonhomogeneous_source_structure():
    producer, sources, clock, _ = _fixture(4)
    source = sources["00_initial_still"]
    good = _event(source, clock, 1)
    other = _event(sources["02_t_pose"], clock, 1, action="02_t_pose")
    assert producer._prepare_pelvis_record_rows((good, other)) is None
    source.span.setflags(write=True)
    source.span[2] = 1
    source.span.setflags(write=False)
    assert producer._prepare_pelvis_record_rows((good, _event(source, clock, 2))) is None
    source.trajectory[SEGMENTS[0]]["mask"].setflags(write=True)
    source.trajectory[SEGMENTS[0]]["mask"][1] = False
    source.trajectory[SEGMENTS[0]]["mask"].setflags(write=False)
    assert producer._prepare_pelvis_record_rows((good,)) is None


def test_accepted_pose_frames_matches_real_nonidentity_alignment():
    producer = Native200PublicationProducer.from_sealed_archives()
    assert not np.array_equal(producer._alignment, np.eye(3))
    source = producer._sources["00_initial_still"]
    start = next(
        index for index in range(1, len(source.time_us) - 15)
        if np.all(np.diff(source.time_us[index:index + 16]) == 5_000)
        and np.all(source.span[index:index + 16] == source.span[index])
        and all(np.all(source.trajectory[s]["mask"][index:index + 16]) for s in SEGMENTS)
    )
    frames = tuple(range(start, start + 16))
    actual = accepted_pose_frames(
        source.trajectory, frames, producer._alignment, producer._geometry,
    )
    expected = tuple(
        accepted_pose_frame(
            source.trajectory, frame, producer._alignment, producer._geometry,
        ) for frame in frames
    )
    for row, oracle in zip(actual, expected):
        for left, right in zip(row, oracle):
            assert tuple(left) == tuple(right)
            assert all(np.array_equal(left[key], right[key]) for key in left)
