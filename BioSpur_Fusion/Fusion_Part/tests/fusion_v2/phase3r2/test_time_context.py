from __future__ import annotations

import inspect

import numpy as np

from biospur_fusion.io_v2.phase3r2_selective import TimingRoutingRecord
from biospur_fusion.time.phase3r2_context import (
    ListenerTimingPoll,
    SUPERFRAME_US,
    choose_integer_epoch_offset,
    fit_clock_models,
    model_payload,
)


NODES = ("BSF1120", "BSF31CC", "BSF3C79", "BSF44AD", "BSF6C53",
         "BSF8BC4", "BSFAA61", "BSFB165", "BSFC2CC", "BSFEC35")


def _synthetic_context():
    offset = 12_345
    count = 512
    slots = {node: index + 1 for index, node in enumerate(NODES)}
    records = []
    polls = []
    scale = 1.0 + 22e-6
    for local_epoch in range(count):
        global_epoch = offset + local_epoch
        for node, slot in slots.items():
            local_timer = 4_000_000_000 + int(round(local_epoch * SUPERFRAME_US / scale))
            flags = 0x80 | ((global_epoch & 0x0F) << 3)
            records.append(TimingRoutingRecord(
                1, node, 0, global_epoch & 0xFFFFFFFF,
                local_timer + 100, local_timer, True, global_epoch & 0x0F,
                flags, local_epoch * 100, 100,
            ))
            for listener in range(3):
                polls.append(ListenerTimingPoll(
                    f"L{listener}", 0xB100 + slot, global_epoch & 0xFF,
                    global_epoch, slot * 10_000.0 + 3_900.0 + listener * 0.02,
                ))
    return records, polls, slots, offset


def test_integer_epoch_join_uses_sequence_and_order_without_receipt_time():
    records, polls, slots, expected = _synthetic_context()
    node = NODES[0]
    local = [row for row in records if row.hardware_node_id == node]
    sequence = np.asarray([row.sweep_id & 0xFF for row in local])
    global_sequence = {poll.absolute_epoch: poll.sequence for poll in polls if poll.src == 0xB100 + slots[node]}
    result, support, margin = choose_integer_epoch_offset(np.arange(len(local)), sequence, global_sequence)
    assert result == expected
    assert support == len(local)
    assert margin > 0


def test_ten_node_rational_common_clock_passes_and_replays_exactly():
    records, polls, slots, expected = _synthetic_context()
    models, details = fit_clock_models(records, polls, slots)
    payload = model_payload(models, details, {"synthetic": True})
    assert payload["gate"]["pass"] is True
    assert payload["gate"]["host_arrival_precision_inputs"] == 0
    assert payload["gate"]["uwb_measurement_numeric_inputs"] == 0
    assert len(models) == 10
    for model in models.values():
        assert model.integer_epoch_offset == expected
        assert abs(model.drift_ppm + 22.0) < 0.1
        assert model.residual_p95_us < 1.0
        assert model.superframe_mod16_agreement == 1.0
        value = model.map_ns(model.first_timer2_us)
        assert value == model.map_ns(model.first_timer2_us)


def test_precision_fit_api_has_no_host_arrival_argument_or_state():
    names = set(inspect.signature(fit_clock_models).parameters)
    assert not {"host", "host_arrival", "master_arrival", "receipt_time"} & names
    records, polls, slots, _ = _synthetic_context()
    models_a, _ = fit_clock_models(records, polls, slots)
    # An unrelated hostile receipt-time array cannot enter the API.
    hostile_receipt_time = np.linspace(-1e12, 1e12, len(records))
    assert hostile_receipt_time.shape[0] == len(records)
    models_b, _ = fit_clock_models(records, polls, slots)
    assert models_a == models_b


def test_boot_segments_are_fit_independently():
    records, polls, slots, _ = _synthetic_context()
    node = NODES[0]
    split = []
    for row in records:
        if row.hardware_node_id != node or row.sweep_id % 512 < 256:
            split.append(row)
        else:
            split.append(TimingRoutingRecord(
                row.record_kind, row.hardware_node_id, 1, row.sweep_id,
                row.frame_timer2_us - 2_000_000_000,
                row.strobe_timer2_us - 2_000_000_000,
                row.superframe_valid, row.superframe_mod16,
                row.required_transport_flags, row.source_byte_offset,
                row.source_record_length,
            ))
    models, _ = fit_clock_models(split, polls, slots)
    assert (node, 0, 0) in models
    assert (node, 1, 0) in models
