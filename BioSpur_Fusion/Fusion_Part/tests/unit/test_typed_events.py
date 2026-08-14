import pytest
from biospur_fusion.ingest.events import EventStatus, RecordType, TypedEvent


def test_measurements_require_authoritative_node_time():
    with pytest.raises(ValueError):
        TypedEvent("BSF0001", 0, RecordType.IMU, 1, None, None, None, 10, {}, {}, EventStatus.DECODED)


def test_arrival_is_retained_but_not_promoted_to_global_time():
    event = TypedEvent("BSF0001", 0, RecordType.IMU, 1, 1234, None, None, 999, {}, {})
    assert event.node_timer_us == 1234 and event.global_time_ns is None
    assert event.master_arrival_ms == 999
