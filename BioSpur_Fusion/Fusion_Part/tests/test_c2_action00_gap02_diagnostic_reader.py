from __future__ import annotations

import hashlib
import json
from pathlib import Path
import struct

from fusion_host_binary import HostFrame, KIND_IMU, encode_frame
import pytest

import biospur_fusion.c2_coupled_progressive.action00_gap02_diagnostic_reader as reader_module
from biospur_fusion.c2_coupled_progressive.continuous_frontend import (
    CONTINUOUS_FRONTEND_SCHEMA,
    ContinuousClockOwner,
    NodeClockBinding,
    continuous_clock_owner_digest,
)
from biospur_fusion.c2_uwb_root_world.action00_gap02_diagnostic_plan import (
    Action00Gap02DiagnosticPlan,
    DiagnosticBranchContract,
)
from biospur_fusion.c2_uwb_root_world.continuous_full_session import ContinuousRegion


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _imu_frame(node: int, timer_us: int, sequence: int) -> bytes:
    payload = struct.pack("<BBHQh", 7, 2, sequence, timer_us - 5_000, 0)
    for index in range(2):
        payload += struct.pack(
            "<Hhhhhhh", 5_000 + 5_000 * index,
            10 + index, 20 + index, 2048, 1, 2, 3,
        )
    return encode_frame(HostFrame(KIND_IMU, node, sequence, 999, payload))


def _fixture(tmp_path: Path, monkeypatch):
    nodes = tuple(0x1000 + index for index in range(10))
    prefix = b"\0" + _imu_frame(0x9999, 50_000, 1)
    region_payloads = tuple(
        b"".join(_imu_frame(node, timer, 10 + index) for index, node in enumerate(nodes))
        for timer in (100_000, 200_000, 300_000)
    )
    suffix = b"NEVER_READ_AFTER_ACTION02"
    raw = tmp_path / "raw.cobs.bin"
    raw.write_bytes(prefix + b"".join(region_payloads) + suffix)
    source_stat = raw.stat()
    monkeypatch.setattr(reader_module, "SOURCE_STAT_IDENTITY", (
        source_stat.st_dev, source_stat.st_ino, source_stat.st_size,
        source_stat.st_mtime_ns,
    ))
    monkeypatch.setattr(reader_module, "SOURCE_SHA256", _sha(raw.read_bytes()))
    monkeypatch.setattr(reader_module, "RAW_RELATIVE", Path("raw.cobs.bin"))
    monkeypatch.setattr(reader_module, "GAP_SLICE_SHA256", _sha(region_payloads[1]))
    evidence = tmp_path / "gap-hash-evidence.json"
    evidence.write_text(json.dumps({
        "raw_container_sha256_declared_not_recomputed": _sha(raw.read_bytes()),
        "window_hashes": {
            "00_initial_still": _sha(region_payloads[0]),
            "UNASSIGNED_INTER_ACTION_GAP": _sha(region_payloads[1]),
            "02_t_pose": _sha(region_payloads[2]),
        },
    }, sort_keys=True), encoding="utf-8")
    monkeypatch.setattr(reader_module, "GAP_HASH_EVIDENCE_RELATIVE", evidence.relative_to(tmp_path))
    monkeypatch.setattr(reader_module, "GAP_HASH_EVIDENCE_SHA256", _sha(evidence.read_bytes()))

    class _Verifier:
        def __init__(self, **_kwargs):
            self.raw_path = raw

    monkeypatch.setattr(reader_module, "Action00EngineeringPolicyReader", _Verifier)
    bindings = tuple(
        NodeClockBinding(
            f"BSF{node:04X}", 4, "B306_TIMER2", _sha(f"map:{node}".encode()),
            1_000.0, 0.0, "b" * 64, "c" * 64,
        )
        for node in nodes
    )
    clock = ContinuousClockOwner(CONTINUOUS_FRONTEND_SCHEMA, bindings)
    offsets = [len(prefix)]
    for payload in region_payloads:
        offsets.append(offsets[-1] + len(payload))
    regions = (
        ContinuousRegion(0, "00_initial_still", "ACTION", offsets[0], offsets[1],
                         90_000_000, 150_000_000, 0, "00_initial_still",
                         _sha(region_payloads[0])),
        ContinuousRegion(1, "GAP_00_initial_still_TO_02_t_pose", "INTER_ACTION_GAP",
                         offsets[1], offsets[2], 150_000_000, 250_000_000,
                         None, None, None),
        ContinuousRegion(2, "02_t_pose", "ACTION", offsets[2], offsets[3],
                         250_000_000, 350_000_000, 2, "02_t_pose",
                         _sha(region_payloads[2])),
    )
    plan = Action00Gap02DiagnosticPlan(
        regions, "synthetic-audit", "d" * 64,
        continuous_clock_owner_digest(clock),
        tuple(sorted(binding.node_id for binding in bindings)),
        DiagnosticBranchContract(),
    )
    return raw, clock, plan, len(prefix), offsets[-1] - offsets[0], suffix


def test_one_fd_three_exact_windows_preserve_original_record_identity(
    tmp_path: Path, monkeypatch,
) -> None:
    raw, clock, plan, prefix_bytes, source_bytes, suffix = _fixture(tmp_path, monkeypatch)
    result = reader_module.Action00Gap02DiagnosticReader(
        root=tmp_path, plan=plan, clock_owner=clock, chunk_bytes=17,
    ).read()
    assert result.access_audit["opened_sources"] == 1
    assert result.access_audit["prefix_bytes_read"] == prefix_bytes
    assert result.access_audit["source_bytes_read"] == source_bytes
    assert result.access_audit["bytes_after_action02_read"] == 0
    assert result.access_audit["read_ahead_performed"] is False
    assert result.access_audit["region_nonempty_record_counts"] == {
        row.region_id: 10 for row in plan.regions
    }
    assert result.access_audit["region_decoded_record_groups"] == {
        row.region_id: 10 for row in plan.regions
    }
    assert result.access_audit["region_skipped_non_sensor_records"] == {
        row.region_id: 0 for row in plan.regions
    }
    assert len(result.events) == 60
    assert result.route_audit.event_count == 60
    assert result.route_audit.imu_dropout_edges == 20
    raw_ids = [event.payload_owner.raw.record_index for event in result.events]
    assert min(raw_ids) == 2
    assert max(raw_ids) == 31
    assert {record: raw_ids.count(record) for record in set(raw_ids)} == {
        record: 2 for record in set(raw_ids)
    }
    assert raw.read_bytes().endswith(suffix)


def test_reader_is_one_shot_and_hash_failure_is_fail_closed(tmp_path: Path, monkeypatch) -> None:
    _raw, clock, plan, _prefix, _source, _suffix = _fixture(tmp_path, monkeypatch)
    reader = reader_module.Action00Gap02DiagnosticReader(
        root=tmp_path, plan=plan, clock_owner=clock, chunk_bytes=13,
    )
    reader_module.GAP_SLICE_SHA256 = "f" * 64
    try:
        try:
            reader.read()
        except ValueError as error:
            assert "hash mismatch" in str(error)
        else:
            raise AssertionError("corrupt region hash was accepted")
        assert reader.last_attempt_audit["status"] == "FAILED_CLOSED"
        assert reader.last_attempt_audit["bytes_after_action02_read"] == 0
        try:
            reader.read()
        except RuntimeError as error:
            assert "one-shot" in str(error)
        else:
            raise AssertionError("reader accepted a second attempt")
    finally:
        monkeypatch.undo()


def test_reader_rejects_tampered_gap_hash_evidence_before_raw_open(
    tmp_path: Path, monkeypatch,
) -> None:
    _raw, clock, plan, _prefix, _source, _suffix = _fixture(tmp_path, monkeypatch)
    (tmp_path / reader_module.GAP_HASH_EVIDENCE_RELATIVE).write_text(
        '{"window_hashes":{}}', encoding="utf-8",
    )
    try:
        reader_module.Action00Gap02DiagnosticReader(
            root=tmp_path, plan=plan, clock_owner=clock, chunk_bytes=11,
        )
    except RuntimeError as error:
        assert "evidence identity mismatch" in str(error)
    else:
        raise AssertionError("tampered gap-hash evidence was accepted")


@pytest.mark.parametrize("tamper", ("container", "gap"))
def test_reader_rejects_internally_wrong_but_correctly_hashed_gap_evidence(
    tmp_path: Path, monkeypatch, tamper: str,
) -> None:
    _raw, clock, plan, _prefix, _source, _suffix = _fixture(tmp_path, monkeypatch)
    path = tmp_path / reader_module.GAP_HASH_EVIDENCE_RELATIVE
    document = json.loads(path.read_text(encoding="utf-8"))
    if tamper == "container":
        document["raw_container_sha256_declared_not_recomputed"] = "e" * 64
    else:
        document["window_hashes"]["UNASSIGNED_INTER_ACTION_GAP"] = "e" * 64
    path.write_text(json.dumps(document, sort_keys=True), encoding="utf-8")
    monkeypatch.setattr(reader_module, "GAP_HASH_EVIDENCE_SHA256", _sha(path.read_bytes()))
    try:
        reader_module.Action00Gap02DiagnosticReader(
            root=tmp_path, plan=plan, clock_owner=clock, chunk_bytes=11,
        )
    except RuntimeError as error:
        assert "does not bind the exact source regions" in str(error)
    else:
        raise AssertionError("internally false gap-hash evidence was accepted")
