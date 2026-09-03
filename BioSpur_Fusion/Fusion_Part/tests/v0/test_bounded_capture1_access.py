from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pytest

from biospur_fusion.v0 import bounded_capture1 as bounded


ROW_DTYPE = np.dtype([
    ("boot_epoch", "<u2"), ("sequence", "<u2"), ("node_timer_us", "<u8"),
    ("global_time_ns", "<i8"), ("global_time_sigma_ns", "<u8"),
    ("master_arrival_ms", "<u8"), ("base_timer2_us", "<u8"),
    ("delta_us", "<u2"), ("acc_raw", "<i2", (3,)),
    ("gyro_raw", "<i2", (3,)), ("temp_raw", "<i2"),
    ("raw_record_index", "<u8"), ("raw_start_offset", "<u8"),
    ("raw_end_offset", "<u8"), ("raw_sample_index", "u1"), ("status", "u1"),
])


def _fixture_npz(path: Path) -> np.ndarray:
    rows = np.zeros(100, dtype=ROW_DTYPE)
    rows["sequence"] = np.arange(100, dtype=np.uint16)
    rows["node_timer_us"] = 10_000 + 5_000 * np.arange(100, dtype=np.uint64)
    rows["global_time_ns"] = 1_000_000 * np.arange(100, dtype=np.int64)
    rows["status"] = 1
    np.savez(path, imu_TEST=rows)
    return rows


def test_npz_metadata_and_selected_rows_use_real_bounded_os_calls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "tiny.npz"
    expected = _fixture_npz(path)
    calls = {"read": 0, "seek": 0}
    real_read = os.read; real_seek = os.lseek

    def spy_read(fd: int, size: int) -> bytes:
        calls["read"] += 1
        return real_read(fd, size)

    def spy_seek(fd: int, offset: int, whence: int) -> int:
        calls["seek"] += 1
        return real_seek(fd, offset, whence)

    monkeypatch.setattr(bounded, "_OS_READ", spy_read)
    monkeypatch.setattr(bounded, "_OS_LSEEK", spy_seek)
    metadata_trace: list[dict] = []
    member = bounded._inspect_stored_npy_members(
        path, ["imu_TEST.npy"], metadata_trace,
    )["imu_TEST.npy"]
    assert member.rows == len(expected)
    payload_interval = (member.array_data_offset, member.member_stop_offset)
    assert not any(
        bounded._overlaps(
            (row["start_byte_inclusive"], row["stop_byte_exclusive"]),
            payload_interval,
        )
        for row in metadata_trace if row.get("call_type") == "os_read"
    )

    forbidden = [member.row_interval(40, 50)]
    access_trace: list[dict] = []
    with bounded._AuditedBinaryFile(path, access_trace) as handle:
        observed, window = bounded._read_member_window(
            handle, member, 10_000_000, 20_000_000, (0, 40), forbidden,
        )
    audit = bounded._trace_summary(access_trace, path.stat().st_size, forbidden)
    assert np.array_equal(observed, expected[10:20])
    assert window["requested_time_window_ns"] == [10_000_000, 20_000_000]
    assert audit["forbidden_intersections"] == []
    assert audit["no_full_file_traversal"] is True
    assert audit["every_binary_search_probe_separately_accounted"] is True
    assert audit["binary_search_probes"]
    assert audit["actual_os_read_calls"] == calls["read"] - sum(
        row.get("actual_read_calls", 0)
        for row in metadata_trace if row.get("call_type") == "os_read"
    )
    assert audit["actual_os_seek_calls"] == calls["seek"] - sum(
        row.get("actual_seek_calls", 0)
        for row in metadata_trace if row.get("call_type") == "os_lseek"
    )


def test_selected_window_intersection_is_rejected_before_sequential_read(
    tmp_path: Path,
) -> None:
    path = tmp_path / "tiny.npz"
    _fixture_npz(path)
    trace: list[dict] = []
    member = bounded._inspect_stored_npy_members(
        path, ["imu_TEST.npy"], trace,
    )["imu_TEST.npy"]
    trace.clear()
    forbidden = [member.row_interval(15, 18)]
    with bounded._AuditedBinaryFile(path, trace) as handle:
        with pytest.raises(ValueError, match="intersects Hxx bytes"):
            bounded._read_member_window(
                handle, member, 10_000_000, 20_000_000, (0, 40), forbidden,
            )
    assert not any(
        row.get("purpose") == "selected_sequential_window" for row in trace
    )
