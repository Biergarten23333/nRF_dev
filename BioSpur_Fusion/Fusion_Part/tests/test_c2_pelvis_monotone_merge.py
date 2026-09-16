from __future__ import annotations

import pytest

from biospur_fusion.c2_uwb_root_world.pelvis_monotone_merge import (
    CompleteWindowBarrier, PelvisTwoStreamMonotoneMerge,
)
from biospur_fusion.ingest.events import RawByteProvenance, RecordType, TypedEvent


SHA = "a" * 64


def event(kind, measurement_ns, arrival, *, boot=0, node="BSFC2CC", raw=True):
    provenance = None if not raw else RawByteProvenance(
        arrival, arrival * 100, arrival * 100 + 99, "b" * 64, 0,
    )
    return TypedEvent(
        node, boot, kind, arrival, measurement_ns // 1000, measurement_ns,
        10, arrival, {}, {}, raw=provenance,
    )


def owner(capacity=32):
    return PelvisTwoStreamMonotoneMerge(
        node="BSFC2CC", boot_epoch=0, source_sha256=SHA,
        window_end_offset=9999, capacity=capacity,
    )


def collect(rows):
    merge = owner()
    output = []
    for kind, timestamp, arrival in rows:
        output.extend(merge.submit(
            event(kind, timestamp, arrival), measurement_time_ns=timestamp,
            stream_progress_ns=timestamp,
        ))
    output.extend(merge.finish(CompleteWindowBarrier(SHA, 9999, True)))
    return output, merge


@pytest.mark.parametrize("rows", [
    [
        (RecordType.IMU, 0, 1), (RecordType.IMU, 100_000_000, 2),
        (RecordType.IMU, 400_000_000, 3), (RecordType.UWB, 0, 4),
        (RecordType.UWB, 100_000_000, 5), (RecordType.UWB, 400_000_000, 6),
    ],
    [
        (RecordType.UWB, 0, 1), (RecordType.UWB, 100_000_000, 2),
        (RecordType.UWB, 400_000_000, 3), (RecordType.IMU, 0, 4),
        (RecordType.IMU, 100_000_000, 5), (RecordType.IMU, 400_000_000, 6),
    ],
])
def test_adversarial_cross_stream_skew_matches_full_measurement_sort(rows):
    output, merge = collect(rows)
    expected = sorted(
        rows, key=lambda row: (row[1], 0 if row[0] is RecordType.IMU else 1, row[2], 0),
    )
    assert [(row.event.record_type, row.measurement_time_ns, row.source_record_index) for row in output] == expected
    assert merge.submitted == merge.dispatched == len(rows)
    assert merge.pending == 0


def test_equal_epoch_is_imu_before_uwb_and_labels_do_not_drain():
    merge = owner()
    assert merge.submit(event(RecordType.IMU, 100, 1), measurement_time_ns=100, stream_progress_ns=100) == ()
    assert merge.label_boundary(1, "01_neutral_sway") == ()
    ready = merge.submit(event(RecordType.UWB, 100, 2), measurement_time_ns=100, stream_progress_ns=100)
    assert [row.event.record_type for row in ready] == [RecordType.IMU, RecordType.UWB]


def test_stalled_stream_capacity_trip_is_state_inert():
    merge = owner(capacity=2)
    merge.submit(event(RecordType.IMU, 100, 1), measurement_time_ns=100, stream_progress_ns=100)
    merge.submit(event(RecordType.IMU, 200, 2), measurement_time_ns=200, stream_progress_ns=200)
    before = (merge.submitted, merge.dispatched, merge.pending)
    with pytest.raises(OverflowError):
        merge.submit(event(RecordType.IMU, 300, 3), measurement_time_ns=300, stream_progress_ns=300)
    assert (merge.submitted, merge.dispatched, merge.pending) == before
    ready = merge.submit(event(RecordType.UWB, 250, 4), measurement_time_ns=250, stream_progress_ns=250)
    assert [row.measurement_time_ns for row in ready] == [100, 200]
    assert merge.pending == 1


def test_stream_identity_monotonicity_and_provenance_fail_closed():
    merge = owner()
    merge.submit(event(RecordType.IMU, 100, 1), measurement_time_ns=100, stream_progress_ns=100)
    for bad in (
        event(RecordType.IMU, 100, 2),
        event(RecordType.IMU, 90, 2),
        event(RecordType.IMU, 200, 2, boot=1),
        event(RecordType.IMU, 200, 2, node="OTHER"),
        event(RecordType.IMU, 200, 2, raw=False),
    ):
        with pytest.raises((ValueError, TypeError)):
            merge.submit(bad, measurement_time_ns=int(bad.global_time_ns), stream_progress_ns=int(bad.global_time_ns))


def test_only_authenticated_complete_barrier_drains():
    merge = owner()
    merge.submit(event(RecordType.IMU, 100, 1), measurement_time_ns=100, stream_progress_ns=100)
    merge.submit(event(RecordType.UWB, 50, 2), measurement_time_ns=50, stream_progress_ns=50)
    assert merge.pending == 1
    for barrier in (
        CompleteWindowBarrier(SHA, 9999, False),
        CompleteWindowBarrier("c" * 64, 9999, True),
        CompleteWindowBarrier(SHA, 9998, True),
    ):
        with pytest.raises(ValueError):
            merge.finish(barrier)
    assert [row.measurement_time_ns for row in merge.finish(
        CompleteWindowBarrier(SHA, 9999, True),
    )] == [100]


def test_uwb_progress_is_monotone_but_range_reference_need_not_be():
    merge = owner()
    merge.submit(event(RecordType.IMU, 2000, 1), measurement_time_ns=2000, stream_progress_ns=2000)
    first = merge.submit(
        event(RecordType.UWB, 1000, 2), measurement_time_ns=1000,
        stream_progress_ns=100,
    )
    assert first == ()
    second = merge.submit(
        event(RecordType.UWB, 500, 3), measurement_time_ns=500,
        stream_progress_ns=200,
    )
    assert second == ()

    before = (merge.submitted, merge.dispatched, merge.pending)
    with pytest.raises(ValueError, match="reversed or duplicated"):
        merge.submit(
            event(RecordType.UWB, 700, 4), measurement_time_ns=700,
            stream_progress_ns=200,
        )
    assert (merge.submitted, merge.dispatched, merge.pending) == before
    assert [row.measurement_time_ns for row in merge.finish(
        CompleteWindowBarrier(SHA, 9999, True),
    )] == [500, 1000, 2000]
