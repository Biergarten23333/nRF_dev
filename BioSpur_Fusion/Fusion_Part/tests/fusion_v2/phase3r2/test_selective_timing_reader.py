from __future__ import annotations

import builtins
import dataclasses
import io
from pathlib import Path

import numpy as np
import pytest

from biospur_fusion.imu_pose_v2.calibration import fit_joint_calibration
from biospur_fusion.imu_pose_v2.estimator import ContinuousArticulatedEstimator
from biospur_fusion.imu_pose_v2.synthetic import frontend_frame, synthetic_calibration_rows

from biospur_fusion.io_v2.phase3r2_selective import (
    SelectiveTimingError,
    TimingRoutingRecord,
    assert_public_schema_safe,
    build_binary_fixture,
    iter_binary_timing_records,
    iter_text_timing_records,
)


FORBIDDEN = {"range_mm", "ranges", "t_round", "distance", "quality", "cfo", "rssi", "payload", "raw_line"}


def _line(measurement: bytes, *, prefix: bytes = b"100.0 200.0", strobe: bytes = b"5000000") -> bytes:
    return (
        prefix + b" FUSION_RX FUSION_UWB proto=7 name=BSFC2CC "
        b"master_ms=123 node_ms=456 pkt=1 sweep=77 identity=E88E "
        b"range_mm=" + measurement + b" ranges=0:" + measurement +
        b" t_round_us=" + measurement + b" quality=" + measurement +
        b" cfo_ppm_q8=" + measurement + b" frame_us=5000100 strobe_us=" +
        strobe + b" flags=0x98\n"
    )


def _semantic(record: TimingRoutingRecord) -> tuple:
    return (
        record.record_kind, record.hardware_node_id, record.boot_epoch,
        record.sweep_id, record.frame_timer2_us, record.strobe_timer2_us,
        record.superframe_valid, record.superframe_mod16,
        record.required_transport_flags,
    )


def test_public_return_schema_has_no_measurement_or_raw_escape_hatch():
    assert_public_schema_safe()
    names = {field.name.lower() for field in dataclasses.fields(TimingRoutingRecord)}
    assert not names & FORBIDDEN
    instance = TimingRoutingRecord(1, "BSFC2CC", 0, 1, 2, 3, False, None, 0, 4, 5)
    assert not hasattr(instance, "__dict__")


@pytest.mark.parametrize("sentinel", [b"83910472", b"99999999", b"NaNxxxxx", b"-0000001", b"$(boom)x"])
def test_all_uwb_measurement_token_mutations_are_projection_invariant(sentinel: bytes):
    baseline = iter_text_timing_records([_line(b"12345678")])
    mutated = iter_text_timing_records([_line(sentinel)])
    assert baseline == mutated


def test_forbidden_tokens_never_reach_python_or_numpy_numeric_conversion(monkeypatch):
    forbidden = {b"86753091"}
    seen: list[object] = []
    real_int = builtins.int
    real_float = builtins.float
    real_asarray = np.asarray

    def guarded_int(value=0, *args, **kwargs):
        assert value not in forbidden
        seen.append(value)
        return real_int(value, *args, **kwargs)

    def guarded_float(value=0, *args, **kwargs):
        assert value not in forbidden
        seen.append(value)
        return real_float(value, *args, **kwargs)

    def guarded_asarray(value, *args, **kwargs):
        assert value not in forbidden
        seen.append(value)
        return real_asarray(value, *args, **kwargs)

    monkeypatch.setattr(builtins, "int", guarded_int)
    monkeypatch.setattr(builtins, "float", guarded_float)
    monkeypatch.setattr(np, "asarray", guarded_asarray)
    records = iter_text_timing_records([_line(next(iter(forbidden)))])
    assert records[0].strobe_timer2_us == 5_000_000
    assert seen


def test_stdout_stderr_and_safe_exception_never_echo_measurement_value(capsys):
    secret = b"73190517"
    iter_text_timing_records([_line(secret)])
    captured = capsys.readouterr()
    assert secret.decode() not in captured.out
    assert secret.decode() not in captured.err
    with pytest.raises(SelectiveTimingError) as caught:
        iter_text_timing_records([_line(secret).replace(b"strobe_us=5000000", b"strobe_us=BAD")])
    assert secret.decode() not in str(caught.value)


def test_only_allowlisted_timing_change_changes_semantic_projection():
    baseline = iter_text_timing_records([_line(b"12345678")])[0]
    changed = iter_text_timing_records([_line(b"12345678", strobe=b"5001000")])[0]
    assert _semantic(baseline) != _semantic(changed)
    assert changed.strobe_timer2_us - baseline.strobe_timer2_us == 1000


def test_host_arrival_text_is_not_returned_and_cannot_change_precision_fields():
    first = iter_text_timing_records([_line(b"12345678", prefix=b"100.0 200.0")])[0]
    second = iter_text_timing_records([_line(b"12345678", prefix=b"-99999.0 888888.0")])[0]
    assert _semantic(first) == _semantic(second)


def test_binary_opaque_measurement_payload_randomization_is_projection_invariant(tmp_path: Path):
    paths = []
    for index, opaque in enumerate((b"\x01", b"\xff\x7f\x00\x80", bytes(range(1, 127)))):
        path = tmp_path / f"mixed-{index}.cobs.bin"
        path.write_bytes(build_binary_fixture(
            node_id=0xC2CC, sweep=991, frame_us=8_000_100,
            strobe_us=8_000_000, flags=0xA8,
            opaque_measurement_bytes=opaque,
        ))
        paths.append(path)
    decoded = [iter_binary_timing_records(path) for path in paths]
    assert [_semantic(rows[0][0]) for rows in decoded] == [_semantic(decoded[0][0][0])] * len(decoded)
    for _, audit in decoded:
        assert audit.measurement_semantic_numeric_decodes == 0
        assert audit.measurement_array_materializations == 0
        assert audit.measurement_statistics_or_plots == 0


def test_uwb_payload_randomization_leaves_final_imu_pose_core_byte_identical(tmp_path: Path):
    mapping = {
        "BSFEC35": "forearm_left", "BSFB165": "forearm_right",
        "BSFAA61": "upper_arm_left", "BSF1120": "upper_arm_right",
        "BSF31CC": "torso", "BSFC2CC": "pelvis", "BSF44AD": "thigh_left",
        "BSF3C79": "thigh_right", "BSF6C53": "shank_left", "BSF8BC4": "shank_right",
    }
    actions = tuple(f"action_{index:02d}" for index in range(18))
    bundle = fit_joint_calibration(synthetic_calibration_rows(mapping, actions), mapping, actions)
    core_bytes = []
    for index, opaque in enumerate((b"range-A", b"NaN-negative-malicious", bytes(range(1, 255)))):
        path = tmp_path / f"mixed-pose-{index}.cobs.bin"
        path.write_bytes(build_binary_fixture(
            node_id=0xC2CC, sweep=12, frame_us=9_000_100,
            strobe_us=9_000_000, flags=0x88, opaque_measurement_bytes=opaque,
        ))
        timing, audit = iter_binary_timing_records(path)
        scheduled_ns = timing[0].strobe_timer2_us * 1000
        estimator = ContinuousArticulatedEstimator(bundle)
        frames = {node: frontend_frame(node, node_index, scheduled_ns)
                  for node_index, node in enumerate(sorted(mapping))}
        pose = estimator.update(scheduled_ns, frames)
        serialized = b"".join(
            np.ascontiguousarray(pose.segment_quaternions_W_S[segment], dtype="<f8").tobytes()
            for segment in sorted(pose.segment_quaternions_W_S)
        ) + np.ascontiguousarray(pose.segment_covariance_rad2, dtype="<f8").tobytes()
        core_bytes.append(serialized)
        assert audit.measurement_semantic_numeric_decodes == 0
    assert core_bytes[0] == core_bytes[1] == core_bytes[2]


def test_binary_reader_detects_boot_before_any_sort(tmp_path: Path):
    path = tmp_path / "reset.cobs.bin"
    path.write_bytes(b"".join([
        build_binary_fixture(node_id=0x1120, sweep=10, frame_us=100_100,
                             strobe_us=100_000, flags=0x80, opaque_measurement_bytes=b"A"),
        build_binary_fixture(node_id=0x1120, sweep=11, frame_us=1_100,
                             strobe_us=1_000, flags=0x80, opaque_measurement_bytes=b"B"),
    ]))
    records, audit = iter_binary_timing_records(path, chunk_bytes=19)
    assert [row.boot_epoch for row in records] == [0, 1]
    assert audit.boot_resets == 1
