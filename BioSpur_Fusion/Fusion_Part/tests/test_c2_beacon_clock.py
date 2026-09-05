import json
from pathlib import Path

import numpy as np
from collections import Counter

from biospur_fusion.c2_uwb_root_world.beacon_clock import (
    BeaconBridge,
    BeaconObservation,
    _fit_node,
    _nearest_mod16,
    build_beacon_index,
    fit_beacon_bridges,
)
from biospur_fusion.time.common_clock import UwbClockAnchor
from biospur_fusion.ingest.v47 import _measurement_stream_boot_epoch


def test_beacon_index_never_decodes_poll_or_response_json(tmp_path):
    listener_dir = tmp_path / "listeners_root"
    rows_dir = listener_dir / "listeners"
    rows_dir.mkdir(parents=True)
    (listener_dir / "summary.json").write_text(json.dumps({
        "listeners": {"1": {"listener_key": "LAE"}},
    }))
    beacon = {
        "listener_key": "LAE",
        "listener_snr": "1",
        "arrival_monotonic_ns": 1_000_000_000,
        "kind": "LBD",
        "parsed_ok": True,
        "fields": {
            "beacon_index": 0,
            "superframe_counter": 7,
            "schedule_generation": 1,
            "cycle_period_us": 120_000,
            "tx_offset_us": 0,
        },
    }
    # These are intentionally invalid JSON after the kind marker. If the
    # extractor attempts to decode either diagnostic kind, this test fails.
    payload = (
        b'{"kind":"LPD",not-json}\n'
        b'{"kind":"LRD",not-json}\n'
        + json.dumps(beacon, separators=(",", ":")).encode() + b"\n"
    )
    (rows_dir / "1.jsonl").write_bytes(payload)
    output = tmp_path / "beacons.jsonl"
    audit = build_beacon_index(listener_dir, output)
    assert audit["beacon_rows_written"] == 1
    assert audit["non_beacon_json_rows_decoded"] == 0
    assert audit["accepted_input_kind"] == "LBD"
    row = json.loads(output.read_text())
    assert row["superframe_counter"] == 7
    assert "kind" not in row


def test_beacon_bridge_recovers_host_to_superframe_clock():
    rows = []
    for listener, delay_us in (("LAE", 2_000.0), ("LBF", 5_000.0)):
        for index in range(20):
            counter = 1_000 + index
            global_us = counter * 120_000.0
            host_s = (global_us + delay_us) / 1_000_000.0
            rows.append(BeaconObservation(listener, host_s, counter, 1, 120_000, 0))
    bridges = fit_beacon_bridges(rows)
    assert set(bridges) == {"LAE", "LBF"}
    assert abs(bridges["LAE"].global_us_per_host_s - 1_000_000.0) < 1e-5
    assert bridges["LAE"].residual_max_us < 1e-4


def test_node_clock_uses_beacon_integer_but_b306_timer_for_continuous_time():
    slot = 2
    offset = 2_000
    anchors = []
    for local_epoch in range(30):
        counter = local_epoch + offset
        timer_us = 10_000_000 + local_epoch * 120_000
        global_us = counter * 120_000 + slot * 10_000 + 3_900
        host_s = global_us / 1_000_000.0 + 0.004
        anchors.append(UwbClockAnchor(
            "BSFC2CC", host_s, 0, local_epoch, timer_us, timer_us,
            True, counter & 0x0F,
        ))
    bridges = {
        "LAE": BeaconBridge("LAE", 1_000_000.0, -4_000.0, 30, 30, 0.0, 0.0),
    }
    model, audit = _fit_node("BSFC2CC", anchors, slot, bridges)
    assert model.integer_epoch_offset == offset
    assert model.sf_mod16_agreement_fraction == 1.0
    assert model.timestamp_reversals == 0
    assert model.residual_max_us < 1e-6
    assert audit["phase_us"] == 23_900.0


def test_nearest_mod16_preserves_carried_superframe_residue():
    value = 1234.2
    result = _nearest_mod16(value, 7)
    assert result & 0x0F == 7
    assert abs(result - value) <= 8


def test_cross_queue_timer_order_does_not_create_false_boot():
    last = {}
    boots = Counter()
    assert _measurement_stream_boot_epoch("BSF0001", 3, 10_000, last, boots) == 0
    # A later-delivered UWB strobe may be older than the latest IMU trigger.
    assert _measurement_stream_boot_epoch("BSF0001", 1, 9_000, last, boots) == 0
    assert _measurement_stream_boot_epoch("BSF0001", 3, 11_000, last, boots) == 0
    assert _measurement_stream_boot_epoch("BSF0001", 1, 10_000, last, boots) == 0


def test_each_measurement_stream_detects_its_own_timer_reset():
    last = {}
    boots = Counter()
    for kind in (1, 3):
        assert _measurement_stream_boot_epoch("BSF0001", kind, 10_000, last, boots) == 0
        assert _measurement_stream_boot_epoch("BSF0001", kind, 100, last, boots) == 1
