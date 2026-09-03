from __future__ import annotations

import binascii
from pathlib import Path
import struct

import numpy as np
import pytest

from biospur_fusion.v0.contracts import NODES
import biospur_fusion.time.common_clock as common_clock
import biospur_fusion.v0.raw_validation as raw_validation
from biospur_fusion.v0.raw_validation import (
    HEADER, IMU_HEADER, IMU_SAMPLE, _decode_imu_only, _read_raw_slice,
    load_raw_action_imu_only,
)


def _cobs_encode(raw: bytes) -> bytes:
    output = bytearray(); block = bytearray()
    for value in raw:
        if value == 0 or len(block) == 254:
            output.append(len(block) + 1); output.extend(block); block.clear()
            if value != 0:
                block.append(value)
        else:
            block.append(value)
    output.append(len(block) + 1); output.extend(block)
    return bytes(output)


def _frame(node: str, kind: int, payload: bytes, sequence: int) -> bytes:
    body = HEADER.pack(0x5342, 1, kind, int(node[3:], 16), len(payload), sequence, 1000) + payload
    raw = body + struct.pack("<H", binascii.crc_hqx(body, 0xFFFF))
    return _cobs_encode(raw) + b"\0"


class _Model:
    sigma_ns = 1000.0

    @staticmethod
    def map_ns(timer_us: int) -> int:
        return int(timer_us) * 1000


def test_raw_adapter_decodes_only_imu_and_skips_uwb_spatial_payload(tmp_path: Path) -> None:
    raw = bytearray(); sequence = 0
    for node in NODES:
        imu = IMU_HEADER.pack(7, 3, sequence, 10_000, 0)
        imu += b"".join(
            IMU_SAMPLE.pack(5_000 * i, 0, 0, 2048, 0, 0, 0) for i in range(3)
        )
        raw.extend(_frame(node, 3, imu, sequence)); sequence += 1
        raw.extend(_frame(node, 1, b"range_mm and anchor geometry must never be parsed", sequence)); sequence += 1
    path = tmp_path / "synthetic.cobs.bin"; path.write_bytes(raw)
    rows, audit = _decode_imu_only(
        path, 0, len(raw), {node: _Model() for node in NODES}, 0, 30_000_000,
    )
    assert set(rows) == set(NODES)
    assert all(np.count_nonzero(value["status"] == 1) == 3 for value in rows.values())
    assert audit["uwb_transport_envelopes_skipped_without_payload_decode"] == len(NODES)
    assert audit["uwb_spatial_fields_decoded"] == []
    assert audit["range_values_consumed"] is False
    assert audit["anchor_geometry_consumed"] is False


def test_bounded_reader_hashes_only_selected_bytes_with_hostile_neighbors(tmp_path: Path) -> None:
    prefix = b"GOLF_BOXING_HOSTILE_PREFIX" * 17
    selected = b"selected-action-only\x00" * 31
    suffix = b"GOLF_BOXING_HOSTILE_SUFFIX" * 19
    path = tmp_path / "continuous.cobs.bin"
    path.write_bytes(prefix + selected + suffix)
    payload, audit = _read_raw_slice(path, len(prefix), len(prefix) + len(selected))
    assert payload == selected
    assert audit["actual_read_intervals"] == [[len(prefix), len(prefix) + len(selected)]]
    assert audit["complete_container_hash_recalculated"] is False
    assert audit["complete_container_scan_attempted"] is False


def test_golf_boxing_overlap_rejected_before_any_payload_open() -> None:
    predeclaration = {
        "ledger": "/path/must/not/be/opened.cobs.bin",
        "sealed_container_sha256": "0" * 64,
        "raw_input": {
            "start_byte_inclusive": 100,
            "stop_byte_exclusive": 200,
            "slice_sha256": "1" * 64,
        },
        "forbidden_golf_boxing_raw_byte_ranges": [
            {"start_byte_inclusive": 150, "stop_byte_exclusive": 250},
        ],
    }
    with pytest.raises(ValueError, match="overlaps forbidden Golf/Boxing"):
        load_raw_action_imu_only(predeclaration)


def test_hostile_timing_reader_accounts_real_reads_and_seeks_without_touching_holdouts(
    tmp_path: Path, monkeypatch,
) -> None:
    path = tmp_path / "hostile_timing.jsonl"
    payload = bytearray(); forbidden_byte_intervals: list[tuple[int, int]] = []
    for index in range(1000):
        marker = "ORDINARY"
        if 900 <= index < 930:
            marker = "BOXING_TIMING_SENTINEL"
        elif 950 <= index < 980:
            marker = "GOLF_TIMING_SENTINEL"
        line = (
            f'{{"arrival_monotonic_ns":{index * 1_000_000},'
            f'"marker":"{marker}","padding":"{index:04d}-hostile-neighbor"}}\n'
        ).encode()
        start = len(payload); payload.extend(line)
        if marker != "ORDINARY":
            forbidden_byte_intervals.append((start, len(payload)))
    path.write_bytes(payload)

    real_read = common_clock._OS_READ
    real_lseek = common_clock._OS_LSEEK
    observed_reads: list[tuple[int, int, int]] = []
    observed_seeks: list[tuple[int, int, int]] = []

    def spy_read(fd: int, size: int) -> bytes:
        start = real_lseek(fd, 0, 1)
        value = real_read(fd, size)
        observed_reads.append((start, start + len(value), size))
        return value

    def spy_lseek(fd: int, offset: int, whence: int) -> int:
        before = real_lseek(fd, 0, 1)
        after = real_lseek(fd, offset, whence)
        observed_seeks.append((before, after, whence))
        return after

    monkeypatch.setattr(common_clock, "_OS_READ", spy_read)
    monkeypatch.setattr(common_clock, "_OS_LSEEK", spy_lseek)
    trace: list[dict] = []
    rows = common_clock._read_bounded_timed_lines(
        path, 400_000_000, 450_000_000, common_clock._jsonl_time_ns, trace,
        search_ceiling_fraction=0.75,
        forbidden_time_intervals_ns=((900_000_000, 930_000_000), (950_000_000, 980_000_000)),
    )

    assert rows
    timestamps = [common_clock._jsonl_time_ns(row) for row in rows]
    assert all(400_000_000 <= value < 450_000_000 for value in timestamps)
    traced_read_calls = sum(
        row.get("actual_read_calls", 0) for row in trace if row.get("call_type") == "os_read"
    )
    traced_seek_calls = sum(
        row.get("actual_seek_calls", 0) for row in trace if row.get("call_type") == "os_lseek"
    )
    assert traced_read_calls == len(observed_reads)
    assert traced_seek_calls == len(observed_seeks)
    assert sum(stop - start for start, stop, _ in observed_reads) < len(payload)
    assert not any(
        max(read_start, forbidden_start) < min(read_stop, forbidden_stop)
        for read_start, read_stop, _ in observed_reads
        for forbidden_start, forbidden_stop in forbidden_byte_intervals
    )
    binary_operation_ids = {
        row["operation_id"] for row in trace if ":binary_probe:" in row.get("operation_id", "")
    }
    assert binary_operation_ids
    for operation_id in binary_operation_ids:
        operation = [row for row in trace if row.get("operation_id") == operation_id]
        assert sum(row.get("actual_seek_calls", 0) for row in operation) == 1
        assert sum(row.get("actual_read_calls", 0) for row in operation) > 0
        assert any(row.get("call_type") == "parsed_timing_row" for row in operation)
    sequential = [
        row for row in trace if row.get("call_type") == "sequential_window_contract"
    ]
    assert len(sequential) == 1
    assert sequential[0]["all_timed_rows_within_requested_window"] is True


def test_hash_bound_seed_ceiling_uses_actual_monotonic_reads_without_touching_forbidden(
    tmp_path: Path, monkeypatch,
) -> None:
    path = tmp_path / "seeded_hostile_timing.jsonl"
    payload = bytearray(); line_starts = []; forbidden_bytes = []
    for index in range(1000):
        line_starts.append(len(payload))
        marker = "FORBIDDEN_HXX" if 900 <= index < 930 else "ORDINARY"
        line = (
            f'{{"arrival_monotonic_ns":{index * 1_000_000},'
            f'"marker":"{marker}","padding":"{index:04d}-seeded"}}\n'
        ).encode()
        left = len(payload); payload.extend(line)
        if marker == "FORBIDDEN_HXX":
            forbidden_bytes.append((left, len(payload)))
    path.write_bytes(payload)

    real_read = common_clock._OS_READ; real_lseek = common_clock._OS_LSEEK
    reads = []; seeks = []

    def spy_read(fd: int, size: int) -> bytes:
        left = real_lseek(fd, 0, 1); value = real_read(fd, size)
        reads.append((left, left + len(value), size)); return value

    def spy_lseek(fd: int, offset: int, whence: int) -> int:
        before = real_lseek(fd, 0, 1); after = real_lseek(fd, offset, whence)
        seeks.append((before, after, whence)); return after

    monkeypatch.setattr(common_clock, "_OS_READ", spy_read)
    monkeypatch.setattr(common_clock, "_OS_LSEEK", spy_lseek)
    trace = []
    rows = common_clock._read_bounded_timed_lines(
        path, 400_000_000, 450_000_000, common_clock._jsonl_time_ns, trace,
        safe_ceiling_seed_offset=line_starts[750],
        forbidden_time_intervals_ns=((900_000_000, 930_000_000),),
    )
    assert rows and reads and seeks
    assert not any(
        max(read_left, forbidden_left) < min(read_right, forbidden_right)
        for read_left, read_right, _ in reads
        for forbidden_left, forbidden_right in forbidden_bytes
    )
    assert sum(
        row.get("actual_read_calls", 0)
        for row in trace if row.get("call_type") == "os_read"
    ) == len(reads)
    assert sum(
        row.get("actual_seek_calls", 0)
        for row in trace if row.get("call_type") == "os_lseek"
    ) == len(seeks)
    contract = next(
        row for row in trace if row.get("call_type") == "search_ceiling_contract"
    )
    assert contract["ceiling_method"] == "HASH_BOUND_EXACT_BYTE_SEED_MONOTONIC_ADVANCE"
    assert contract["probe_timestamp_ns"] < 900_000_000


def test_real_capture2_adapter_is_action_bounded_and_never_hashes_container(monkeypatch) -> None:
    root = Path(__file__).resolve().parents[2]
    capture = root / (
        "datasets/phase2_calibration/"
        "phase2_targeted_calibration_20260817t130918z_capture_2_with_joint_label_c8645eb2"
    )
    raw = (capture / "system/fusion_continuous/fusion_host_raw.cobs.bin").resolve()
    if not raw.is_file():
        pytest.skip("sealed Capture2 evidence is not present")
    event = capture / "actions/04_shoulder_left/rep_01/events/ACTION_EVENTS.jsonl"
    readiness = capture / "system/readiness/SYSTEM_READINESS_REPORT.json"
    predeclaration = {
        "ledger": str(raw),
        "sealed_container_sha256": "74c1fdbbe7c302bc21b0665bff50137e84537946a347ea11133e1e6751c84268",
        "raw_input": {
            "start_byte_inclusive": 229626310,
            "stop_byte_exclusive": 231454128,
            "slice_sha256": "13371a86d643d1c22b83ba660d49286b29ce8cda514cbbcf2c693e976c931f46",
            "start_host_monotonic_ns": 196642277695044,
            "stop_host_monotonic_ns_exclusive": 196672307941599,
        },
        "forbidden_golf_boxing_raw_byte_ranges": [
            {"start_byte_inclusive": 297543640, "stop_byte_exclusive": 299370508},
            {"start_byte_inclusive": 301566984, "stop_byte_exclusive": 303392878},
        ],
        "forbidden_golf_boxing_timing_intervals_ns": [
            {
                "action": "H01_boxing",
                "start_host_monotonic_ns": 197758743612088,
                "stop_host_monotonic_ns_exclusive": 197788773310200,
            },
            {
                "action": "H02_golf",
                "start_host_monotonic_ns": 197824877240536,
                "stop_host_monotonic_ns_exclusive": 197854921151136,
            },
        ],
        "authoritative_action_event_source": str(event),
        "authoritative_action_event_source_sha256": "e0208c7f7b1ec8a45cec5070639335d4c8867b986f3c82f1a78a144ab7e53c14",
        "common_clock_timing_sources": {
            "capture_identity_authority": str(readiness),
            "capture_identity_authority_sha256": "77053bce68e255357ceeccdcced3977bce73e62d23052797e67abc14fd856d3e",
            "fusion_timing_log": str(capture / "system/fusion_continuous/fusion_cdc.log"),
            "listener_directory": str(capture / "system/listeners/passive_5"),
        },
    }
    original_sha256 = raw_validation.sha256_file
    real_open = common_clock._OS_OPEN
    real_close = common_clock._OS_CLOSE
    real_read = common_clock._OS_READ
    real_lseek = common_clock._OS_LSEEK
    fd_paths: dict[int, str] = {}
    observed_timing_reads: list[tuple[str, int, int]] = []
    observed_timing_seeks: list[tuple[str, int, int]] = []

    def spy_open(path, flags):
        fd = real_open(path, flags); fd_paths[fd] = str(Path(path).resolve()); return fd

    def spy_close(fd: int) -> None:
        real_close(fd); fd_paths.pop(fd, None)

    def spy_read(fd: int, size: int) -> bytes:
        start = real_lseek(fd, 0, 1); value = real_read(fd, size)
        observed_timing_reads.append((fd_paths[fd], start, start + len(value)))
        return value

    def spy_lseek(fd: int, offset: int, whence: int) -> int:
        before = real_lseek(fd, 0, 1); after = real_lseek(fd, offset, whence)
        observed_timing_seeks.append((fd_paths[fd], before, after)); return after

    def reject_complete_container_hash(path: Path) -> str:
        if Path(path).resolve() == raw:
            raise AssertionError("continuous raw container hash attempted")
        return original_sha256(path)

    monkeypatch.setattr(raw_validation, "sha256_file", reject_complete_container_hash)
    monkeypatch.setattr(common_clock, "_OS_OPEN", spy_open)
    monkeypatch.setattr(common_clock, "_OS_CLOSE", spy_close)
    monkeypatch.setattr(common_clock, "_OS_READ", spy_read)
    monkeypatch.setattr(common_clock, "_OS_LSEEK", spy_lseek)
    rows, audit = load_raw_action_imu_only(predeclaration)
    assert set(rows) == set(NODES)
    assert audit["decode"]["raw_access"]["actual_read_intervals"] == [[229626310, 231454128]]
    assert audit["sealed_container_sha256_recomputed"] is False
    assert audit["common_clock"]["gate"]["pass"] is True
    assert audit["common_clock"]["gate"]["timing_access"]["complete_file_scan_attempted"] is False
    assert all(
        row["unique_bytes_read"] < row["file_size"]
        for row in audit["common_clock"]["gate"]["timing_access"]["files"]
    )
    timing_access = audit["common_clock"]["gate"]["timing_access"]
    assert len(observed_timing_reads) == timing_access["raw_io_call_accounting"]["actual_os_read_calls"]
    assert len(observed_timing_seeks) == timing_access["raw_io_call_accounting"]["actual_os_seek_calls"]
    ceiling_by_path = {
        row["path"]: row for row in timing_access["search_ceiling_contracts"]
    }
    for path, contract in ceiling_by_path.items():
        actual_stops = [stop for actual_path, _, stop in observed_timing_reads if actual_path == path]
        assert actual_stops
        assert max(actual_stops) <= contract["probe_row_stop_byte_exclusive"]
        assert contract["probe_timestamp_ns"] < 197758743612088
    assert timing_access["all_sequential_timing_rows_within_action_plus_two_superframes"] is True
    assert timing_access["every_binary_search_probe_separately_accounted"] is True
    assert timing_access["no_full_file_traversal_proven_by_actual_read_union"] is True
    assert timing_access["golf_boxing_timing_interval_bytes_touched"] is False
    assert timing_access["binary_search_probe_count"] > 0
    mapping = audit["common_clock"]["gate"]["capture_identity"]["listener_source_binding"]["mapping"]
    assert set(mapping) == set(NODES)
    assert mapping["BSFC2CC"]["source_binding_method"] == "EXACTLY_ONE_BIJECTIVE_LRD_DESTINATION_ELIMINATION"
    assert audit["CURRENT_GOAL_GOLF_BOXING_BYTES_TOUCHED"] == "NO"
    assert audit["GOLF_BOXING_MEASUREMENTS_DECODED"] == "NO"
    assert audit["uwb_spatial_payload_decoded_or_consumed"] is False
