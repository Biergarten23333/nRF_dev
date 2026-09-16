from __future__ import annotations

from types import SimpleNamespace

import pytest

from biospur_fusion.c2_coupled_progressive.continuous_full_session_reader import (
    FullSessionRecordTicket,
)
from tools.run_c2_full_session_reader_watermark_audit import (
    _BatchAuditAccumulator,
    _run_reader,
)


def _event(record: int, sample: int, kind: str, node: str) -> object:
    raw = SimpleNamespace(
        record_index=record, sample_index=sample, start_offset=record * 10,
        end_offset=record * 10 + 9, encoded_sha256=f"{record:064x}",
    )
    return SimpleNamespace(
        event_id=f"v47:{record}:{sample}", kind=kind, node_id=node,
        payload_owner=SimpleNamespace(raw=raw),
    )


class _TicketOwner:
    def __init__(self, batches: tuple[tuple[object, ...], ...]) -> None:
        self.batches = batches
        self.calls = [0] * len(batches)

    def ticket(self, ordinal: int) -> FullSessionRecordTicket:
        batch = self.batches[ordinal]
        raw = batch[0].payload_owner.raw
        return FullSessionRecordTicket(
            ordinal,
            (raw.record_index, raw.start_offset, raw.end_offset, raw.encoded_sha256),
            tuple(f"e{ordinal}{index}" for index in range(len(batch))),
            tuple(f"s{ordinal}{index}" for index in range(len(batch))),
            object(), self,
        )

    def _deliver_record(self, ticket: FullSessionRecordTicket, callback) -> None:
        ordinal = ticket.record_ordinal
        self.calls[ordinal] += 1
        if self.calls[ordinal] != 1:
            raise RuntimeError("synthetic ticket delivered more than once")
        callback(self.batches[ordinal])


class _Reader:
    def __init__(self) -> None:
        self.consume_calls = 0
        self.owner = _TicketOwner((
            tuple(_event(10, index, "IMU", "BSF0001") for index in range(2)),
            (_event(12, 0, "UWB", "BSF0002"),),
        ))

    def consume_record_batches(self, consumer):
        self.consume_calls += 1
        if self.consume_calls != 1:
            raise RuntimeError("public batch entry called more than once")
        for ordinal in range(len(self.owner.batches)):
            consumer(self.owner.ticket(ordinal))
        route = SimpleNamespace(
            event_count=3, events_by_region={"R0": 3},
            imu_by_region_and_node={"R0": {"BSF0001": 2}},
            exact_5ms_imu_edges=1, imu_dropout_edges=0,
            minimum_sensor_ready_lower_bound_ns=1,
            maximum_sensor_ready_lower_bound_ns=3,
            lifted_record_count=1, maximum_availability_lift_ns=2,
            total_availability_lift_ns=2, maximum_owner_watermark_entries=4,
            pending_event_high_water=2, identity_sha256="a" * 64,
        )
        access = {
            "nonempty_records": 3, "decoded_events": 3, "routed_events": 3,
            "source_bytes_read": 99, "prefix_bytes_read": 10,
            "bytes_after_session_read": 0, "window_sha256": "b" * 64,
        }
        return SimpleNamespace(route_audit=route, access_audit=access)


def test_batch_audit_counts_skipped_records_and_uses_public_entry_once() -> None:
    reader = _Reader()
    checkpoints = []
    result = _run_reader(reader, checkpoints.append)

    assert reader.consume_calls == 1
    assert reader.owner.calls == [1, 1]
    assert result["container_records"] == 3
    assert result["routed_sensor_records"] == 2
    assert result["skipped_non_sensor_container_records"] == 1
    assert result["record_tickets"] == result["callbacks"] == 2
    assert result["delivered_events"] == 3
    assert len(checkpoints) == 1 and checkpoints[0]["final"] is True
    assert checkpoints[0]["pending_events"] == 0


@pytest.mark.parametrize(("kind", "size"), (
    ("IMU", 1), ("IMU", 10), ("IMU", 16), ("UWB", 1),
))
def test_batch_cardinality_contract_accepts_valid_sensor_records(
    kind: str, size: int,
) -> None:
    batch = tuple(_event(20, index, kind, "BSF0003") for index in range(size))
    owner = _TicketOwner((batch,))
    accumulator = _BatchAuditAccumulator(lambda _row: None)

    accumulator.consume_ticket(owner.ticket(0))

    assert owner.calls == [1]
    assert accumulator.delivered_batches == accumulator.callbacks == 1
    assert accumulator.delivered_events == size


def test_batch_cardinality_contract_rejects_zero_overflow_and_multi_uwb() -> None:
    accumulator = _BatchAuditAccumulator(lambda _row: None)
    with pytest.raises(RuntimeError, match="batch size outside 1..16"):
        accumulator._accept_batch(object(), ())

    for batch in (
        tuple(_event(21, index, "IMU", "BSF0004") for index in range(17)),
        tuple(_event(22, index, "UWB", "BSF0005") for index in range(2)),
    ):
        owner = _TicketOwner((batch,))
        with pytest.raises(RuntimeError, match=(
            "batch size outside 1..16" if len(batch) > 16
            else "sensor batch cardinality mismatch"
        )):
            accumulator.consume_ticket(owner.ticket(0))
