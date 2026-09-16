from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from biospur_fusion.c2_coupled_progressive.continuous_frontend import (
    CONTINUOUS_FRONTEND_SCHEMA,
    ContinuousClockOwner,
    NodeClockBinding,
)
from biospur_fusion.c2_uwb_root_world.action00_gap02_diagnostic_plan import (
    DiagnosticEventRouter,
    EXPECTED_GAP_ID,
    load_action00_gap02_diagnostic_plan,
)
from biospur_fusion.ingest.events import (
    RawByteProvenance,
    RecordType,
    TypedEvent,
)


ROOT = Path(__file__).resolve().parents[1]
NODES = tuple(f"BSF{index:04X}" for index in range(10))


def _clock() -> ContinuousClockOwner:
    return ContinuousClockOwner(CONTINUOUS_FRONTEND_SCHEMA, tuple(
        NodeClockBinding(
            node, 7, "B306_TIMER2", f"{index + 1:064x}", 1_000.0, 0.0,
            "a" * 64, "b" * 64,
        )
        for index, node in enumerate(NODES)
    ))


def _record(node: str, *, timer_us: int, sequence: int, start: int, ordinal: int) -> TypedEvent:
    return TypedEvent(
        node, 7, RecordType.IMU, sequence, timer_us, None, None, 0,
        payload={
            "base_timer2_us": timer_us - 5_000,
            "acc_raw": (0, 0, 2048), "gyro_raw": (0, 0, 0),
        },
        raw=RawByteProvenance(ordinal, start, start + 2, f"{ordinal + 100:064x}", 0),
    )


def _uwb_record(node: str, *, strobe_us: int, frame_us: int, sequence: int,
                start: int, ordinal: int) -> TypedEvent:
    return TypedEvent(
        node, 7, RecordType.UWB, sequence, strobe_us, None, None, 0,
        payload={"strobe_us": strobe_us, "frame_us": frame_us},
        raw=RawByteProvenance(ordinal, start, start + 2, f"{ordinal + 100:064x}", 0),
    )


def _timer_inside(start_ns: int) -> int:
    return (start_ns + 999) // 1_000


def test_plan_binds_exact_contiguous_source_regions_and_diagnostic_semantics():
    plan = load_action00_gap02_diagnostic_plan(ROOT, _clock())
    assert [row.region_id for row in plan.regions] == [
        "00_initial_still", EXPECTED_GAP_ID, "02_t_pose",
    ]
    assert plan.regions[0].stop_offset == plan.regions[1].start_offset
    assert plan.regions[1].stop_offset == plan.regions[2].start_offset
    assert plan.branch_contract.gap_semantics == (
        "IDENTICAL_A_B_PROCESS_EVERY_REAL_UNLABELLED_IMU_NO_HOLD_NO_INTERPOLATION"
    )
    assert plan.branch_contract.terminal_missing_semantics == "AUDIT_ONLY_NO_TRANSLATION_MODE_CHANGE"
    assert plan.branch_contract.orientation_history.startswith("ONE_PERSISTENT_NATIVE200_VQF")
    assert not plan.product_ready and not plan.scientific_pass
    with pytest.raises(ValueError, match="digest"):
        replace(plan, digest="0" * 64)


def test_router_preserves_all_ten_node_real_gap_events_and_uint16_wrap():
    clock = _clock()
    plan = load_action00_gap02_diagnostic_plan(ROOT, clock)
    router = DiagnosticEventRouter(plan, clock)
    emitted = []
    sequences = (0xFFFF, 0, 1)
    ordinal = 0
    for region, sequence in zip(plan.regions, sequences):
        timer_us = _timer_inside(region.start_ns)
        availability = timer_us * 1_000 + 1_000_000
        for index, node in enumerate(NODES):
            emitted.extend(router.route_original_record((_record(
                node, timer_us=timer_us, sequence=sequence,
                start=region.start_offset + 4 * index, ordinal=ordinal,
            ),)))
            ordinal += 1
    audit = router.finish()
    gap = emitted[10:20]
    assert all(row.kind == "IMU" and row.action_index == -1 for row in gap)
    assert all(row.action_id == EXPECTED_GAP_ID and row.region_id == EXPECTED_GAP_ID for row in gap)
    assert audit.event_count == 30
    assert audit.events_by_region[EXPECTED_GAP_ID] == 10
    assert audit.imu_by_region_and_node[EXPECTED_GAP_ID] == {node: 1 for node in NODES}
    assert audit.imu_dropout_edges == 20


def test_router_rejects_byte_time_mismatch_replay_and_availability_regression_atomically():
    clock = _clock()
    plan = load_action00_gap02_diagnostic_plan(ROOT, clock)
    router = DiagnosticEventRouter(plan, clock)
    action00, gap, _action02 = plan.regions
    first = _record(
        NODES[0], timer_us=_timer_inside(action00.start_ns), sequence=4,
        start=action00.start_offset, ordinal=1,
    )
    router.route_original_record((first,))
    before = router.owner_bytes()

    mismatched = _record(
        NODES[1], timer_us=_timer_inside(gap.start_ns), sequence=5,
        start=action00.start_offset + 10, ordinal=2,
    )
    with pytest.raises(ValueError, match="byte/time"):
        router.route_original_record((mismatched,))
    assert router.owner_bytes() == before
    with pytest.raises(ValueError, match="replay"):
        router.route_original_record((first,))
    assert router.owner_bytes() == before

    future_frame = _uwb_record(
        NODES[0], strobe_us=first.node_timer_us + 5_000,
        frame_us=first.node_timer_us + 20_000, sequence=5,
        start=action00.start_offset + 20, ordinal=3,
    )
    router.route_original_record((future_frame,))
    before_regression = router.owner_bytes()
    second = _record(
        NODES[0], timer_us=first.node_timer_us + 10_000, sequence=6,
        start=action00.start_offset + 30, ordinal=4,
    )
    with pytest.raises(ValueError, match="availability chronology"):
        router.route_original_record((second,))
    assert router.owner_bytes() == before_regression


def test_router_rejects_non_uint16_sequence_and_incomplete_node_conservation():
    clock = _clock()
    plan = load_action00_gap02_diagnostic_plan(ROOT, clock)
    router = DiagnosticEventRouter(plan, clock)
    region = plan.regions[0]
    invalid = _record(
        NODES[0], timer_us=_timer_inside(region.start_ns), sequence=65_536,
        start=region.start_offset, ordinal=1,
    )
    before = router.owner_bytes()
    with pytest.raises(ValueError, match="uint16"):
        router.route_original_record((invalid,))
    assert router.owner_bytes() == before
    with pytest.raises(RuntimeError, match="ten-node"):
        router.finish()
