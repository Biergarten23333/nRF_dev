from __future__ import annotations

import binascii
import hashlib
import json
from pathlib import Path
import struct

import numpy as np

from biospur_fusion.v0.c2_progressive.range_reader import (
    CaptureWideImuState,
    HOST_ENVELOPE,
    IMU_HEADER,
    IMU_SAMPLE,
    SealedFrozenEvaluationRangeReader,
    SealedHeldoutRangeReader,
    SealedPrefitRangeReader,
)


def _cobs_encode(raw: bytes) -> bytes:
    output = bytearray()
    block_start = 0
    for index, value in enumerate(raw):
        if value == 0 or index - block_start == 254:
            output.append(index - block_start + 1)
            output.extend(raw[block_start:index])
            block_start = index + (1 if value == 0 else 0)
    output.append(len(raw) - block_start + 1)
    output.extend(raw[block_start:])
    return bytes(output)


def _record(kind: int, node_id: int, payload: bytes) -> bytes:
    body = HOST_ENVELOPE.pack(
        0x5342, 1, kind, node_id, len(payload), 7, 123,
    ) + payload
    raw = body + struct.pack("<H", binascii.crc_hqx(body, 0xFFFF))
    return _cobs_encode(raw) + b"\0"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_capture_wide_state_names_inter_episode_gap_without_reset() -> None:
    state = CaptureWideImuState([f"BSF{index:04X}" for index in range(10)])
    state.begin_action("a", 0)
    state.observe(node="BSF0000", action="a", action_index=0, sequence=1, timer_us=5_000)
    state.begin_action("b", 1)
    state.observe(node="BSF0000", action="b", action_index=1, sequence=2, timer_us=105_000)
    audit = state.audit()
    assert audit["episode_reset_count"] == 0
    assert audit["gaps_concatenated"] is False
    assert audit["nodes"]["BSF0000"]["inter_episode_gaps"][0]["gap_us"] == 100_000
    assert audit["nodes"]["BSF0000"]["missing_sample_slots_within_open_ranges_only"] == 0
    assert audit["nodes"]["BSF0000"]["sealed_inter_episode_duration_counted_as_missing_samples"] is False


def test_reader_opens_only_sealed_prefit_interval_and_skips_other_kind(tmp_path: Path) -> None:
    nodes = [f"BSF{index:04X}" for index in range(10)]
    imu = IMU_HEADER.pack(7, 1, 41, 1_000_000, 22) + IMU_SAMPLE.pack(
        0, 1, 2, 2048, 3, 4, 5,
    )
    training = _record(1, 0, b"opaque spatial bytes") + _record(3, 0, imu)
    raw = bytearray()
    ranges = []
    for index in range(19):
        start = len(raw)
        raw.extend(training)
        stop = len(raw)
        heldout_imu = IMU_HEADER.pack(7, 1, 42, 1_005_000, 22) + IMU_SAMPLE.pack(
            0, 6, 7, 2048, 8, 9, 10,
        )
        raw.extend(_record(3, 0, heldout_imu))
        held_stop = len(raw)
        raw.extend(b"gap\0")
        ranges.append({
            "action": f"action_{index:02d}",
            "chronological_index": index,
            "prefit_training_interval": [start, stop],
            "fit_freeze_heldout_interval": [stop, held_stop],
        })
    raw_path = tmp_path / "raw.cobs.bin"
    raw_path.write_bytes(raw)
    plan = {
        "schema": "biospur-c2-main-payload-byte-access-plan-v1",
        "payload_file": raw_path.name,
        "prefit_policy": "Only prefit_training_interval ranges may be decoded before fit freeze.",
        "ranges": ranges,
    }
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    plan_path.chmod(0o444)
    reader = SealedPrefitRangeReader(
        root=tmp_path,
        plan_path=plan_path,
        expected_plan_sha256=_sha256(plan_path),
        nodes=nodes,
    )
    action = reader.read_action(0)
    assert action.access_audit["requested_half_open_interval"] == ranges[0]["prefit_training_interval"]
    assert action.access_audit["heldout_bytes_touched"] is False
    assert action.decode_audit["counts"]["opaque_kind_1_skipped"] == 1
    assert action.decode_audit["non_imu_envelope_payload_interpreted"] is False
    assert action.decode_audit["device_carried_status_field_present"] is False
    assert "decode_acceptance_status" in action.rows_by_node["BSF0000"].dtype.names
    assert "status" not in action.rows_by_node["BSF0000"].dtype.names
    assert "derived_boot_epoch" in action.rows_by_node["BSF0000"].dtype.names
    assert action.rows_by_node["BSF0000"]["acc_raw"].tolist() == [[1, 2, 2048]]
    assert sum(len(rows) for rows in action.rows_by_node.values()) == 1

    heldout_reader = SealedHeldoutRangeReader(
        root=tmp_path,
        plan_path=plan_path,
        expected_plan_sha256=_sha256(plan_path),
        nodes=nodes,
    )
    heldout = heldout_reader.read_action(0)
    assert heldout.access_audit["requested_half_open_interval"] == ranges[0][
        "fit_freeze_heldout_interval"
    ]
    assert heldout.access_audit["prefit_training_interval"] == ranges[0][
        "prefit_training_interval"
    ]
    assert heldout.access_audit["heldout_bytes_touched"] is True
    assert heldout.access_audit["interval_role"] == "POST_FRESH_FROZEN_HELDOUT_EVALUATION"
    assert heldout.decode_audit["fit_or_owner_update_allowed"] is False
    assert heldout_reader.reader_session_id.startswith("C2_HELDOUT_READER_")

    evaluation_reader = SealedFrozenEvaluationRangeReader(
        root=tmp_path,
        plan_path=plan_path,
        expected_plan_sha256=_sha256(plan_path),
        nodes=nodes,
    )
    evaluation = evaluation_reader.read_action(0)
    combined = evaluation.combined_action.rows_by_node["BSF0000"]
    assert combined["acc_raw"].tolist() == [[1, 2, 2048], [6, 7, 2048]]
    assert evaluation.heldout_source_indices_by_node["BSF0000"].tolist() == [1]
    assert evaluation.access_audit["ranges"][
        "PREFIT_ORIENTATION_RECONSTRUCTION_ONLY"
    ]["heldout_bytes_touched"] is False
    assert evaluation.access_audit["ranges"][
        "HELDOUT_FROZEN_SCIENTIFIC_EVALUATION"
    ]["heldout_bytes_touched"] is True
    assert evaluation.decode_audit["training_rows_allowed_in_scientific_metric"] is False
    assert evaluation_reader.reader_session_id.startswith("C2_FROZEN_EVALUATION_READER_")
