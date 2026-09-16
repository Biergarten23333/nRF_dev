from __future__ import annotations

import hashlib
import io
from pathlib import Path
import struct

import numpy as np
import pytest

from fusion_host_binary import HostFrame, KIND_IMU, encode_frame

import biospur_fusion.c2_coupled_progressive.action00_engineering_reader as reader_module
from biospur_fusion.c2_coupled_progressive.action00_engineering_reader import (
    ACTION00_SLICE_SHA256,
    ACTION00_START_NS,
    ACTION00_START_OFFSET,
    ACTION00_STOP_NS,
    ACTION00_STOP_OFFSET,
    ROLE,
    count_nonempty_cobs_prefix,
)
from biospur_fusion.c2_coupled_progressive.continuous_frontend import (
    CONTINUOUS_FRONTEND_SCHEMA,
    ContinuousClockOwner,
    NodeClockBinding,
    validate_event_clock,
)
from biospur_fusion.c2_coupled_progressive.continuous_streaming_runner import (
    AuthorizedByteWindow,
    IncrementalV47WindowDecoder,
)
from biospur_fusion.c2_coupled_progressive.authenticated_vqf_tilt_join import (
    AuthenticatedVQFTiltClockJoin,
)
from biospur_fusion.ingest.v47 import iter_cobs_records
from biospur_fusion.v0.c2_progressive.orientation import ContinuousVQFState
from biospur_fusion.v0.c2_progressive.pipeline_runtime import _runtime_vqf_tilt_authority


class _Guard:
    capture_id = "C2"

    def bind_vqf_instance(self, node, instance):
        pass

    def begin_episode(self, index, action):
        pass


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _imu_frame(node: int, timer_us: int, sequence: int, *, samples: int = 2) -> bytes:
    base = timer_us - 5_000
    payload = struct.pack("<BBHQh", 7, samples, sequence, base, 0)
    for index in range(samples):
        payload += struct.pack(
            "<Hhhhhhh", 5_000 + 5_000 * index,
            100 + index, 200 + index, 2048, 1, 2, 3,
        )
    return encode_frame(HostFrame(KIND_IMU, node, sequence, 999, payload))


def test_role_constants_are_the_exact_full_action00_contract() -> None:
    assert ROLE == "ACTION00_ENGINEERING_POLICY"
    assert (ACTION00_START_OFFSET, ACTION00_STOP_OFFSET) == (213_648_544, 216_084_573)
    assert (ACTION00_START_NS, ACTION00_STOP_NS) == (
        234_836_221_471_621, 234_866_246_815_581,
    )
    assert ACTION00_SLICE_SHA256 == (
        "ee08b44c3383e74099dc80a5c92bfaef500fa6bb472bc774655aa5b845485d5b"
    )


def test_bounded_prefix_ordinal_matches_canonical_full_iterator(tmp_path: Path) -> None:
    prefix_records = (
        _imu_frame(0x1000, 100_000, 1),
        _imu_frame(0x1001, 100_000, 1),
        _imu_frame(0x1002, 100_000, 1),
    )
    prefix = b"\0" + prefix_records[0] + b"\0\0" + prefix_records[1] + prefix_records[2]
    window = _imu_frame(0x1003, 105_000, 2)
    path = tmp_path / "synthetic.cobs.bin"
    path.write_bytes(prefix + window)
    with path.open("rb") as source:
        count, consumed = count_nonempty_cobs_prefix(
            source, stop_offset=len(prefix), chunk_bytes=7,
        )
        assert source.tell() == len(prefix)
    full = list(iter_cobs_records(path, chunk_size=5))
    first_window_record = next(row for row in full if row[1] == len(prefix))
    assert count == 3
    assert consumed == len(prefix)
    assert first_window_record[0] == count + 1

    with io.BytesIO(prefix[:-1]) as incomplete:
        with pytest.raises(ValueError, match="boundary"):
            count_nonempty_cobs_prefix(
                incomplete, stop_offset=len(prefix) - 1, chunk_bytes=3,
            )


def test_original_typed_identity_is_chunk_invariant_and_hard_capped() -> None:
    nodes = tuple(0x1000 + index for index in range(10))
    payload = b"".join(
        _imu_frame(node, 100_000 + index * 100, 65000 + index)
        for index, node in enumerate(nodes)
    )
    boots = {f"BSF{node:04X}:3": 4 for node in nodes}
    auth = AuthorizedByteWindow(
        "synthetic", "a" * 64, 200, 200 + len(payload), 41,
        _sha(payload), 1, 2_000_000_000, boots, 4096,
    )

    def decode(width: int):
        decoder = IncrementalV47WindowDecoder(auth)
        emitted = []
        cursor = 0
        while cursor < len(payload):
            block = payload[cursor:cursor + width]
            emitted.extend(decoder.feed(block, absolute_offset=200 + cursor))
            cursor += len(block)
        decoder.finish()
        return tuple(emitted)

    one = decode(len(payload))
    chunked = decode(11)
    identity = lambda row: (
        row.node_id, row.boot_epoch, row.sequence, row.node_timer_us,
        row.raw.record_index, row.raw.sample_index,
        row.raw.start_offset, row.raw.end_offset, row.raw.encoded_sha256,
        row.payload["base_timer2_us"], row.payload["delta_us"],
        row.master_arrival_ms,
    )
    assert tuple(map(identity, one)) == tuple(map(identity, chunked))
    assert len(one) == 20
    assert {row.node_id for row in one} == {f"BSF{node:04X}" for node in nodes}
    assert one[0].raw.record_index == 42
    assert one[-1].raw.end_offset == auth.end_offset
    assert all(
        row.payload["base_timer2_us"] + row.payload["delta_us"] == row.node_timer_us
        for row in one
    )

    decoder = IncrementalV47WindowDecoder(auth)
    with pytest.raises(ValueError, match="exceeds authorized"):
        decoder.feed(payload + b"x", absolute_offset=auth.start_offset)
    assert decoder.emitted_events == 0


def test_authorization_rejects_foreign_or_reversed_role_range() -> None:
    with pytest.raises(ValueError, match="invalid authorized"):
        AuthorizedByteWindow(
            "synthetic", "a" * 64, 10, 10, 0, "b" * 64,
            0, 1, {"BSF1000:3": 0}, 256,
        )
    with pytest.raises(ValueError, match="invalid authorized"):
        AuthorizedByteWindow(
            "synthetic", "a" * 64, 10, 20, 0, "b" * 64,
            3, 2, {"BSF1000:3": 0}, 256,
        )


def _full_reader_fixture(tmp_path: Path, monkeypatch, *, node_count: int = 10,
                         bad_slice_hash: bool = False,
                         boundary_envelope: bool = False):
    prefix = b"\0" + _imu_frame(0x9999, 50_000, 1) + b"\0"
    nodes = tuple(0x1000 + index for index in range(node_count))
    if boundary_envelope:
        action = b"".join(
            _imu_frame(node, timer_us, 10 + index * 5 + frame_index)
            for index, node in enumerate(nodes)
            for frame_index, timer_us in enumerate(
                (80_000, 85_000, 100_000, 195_000, 205_000)
            )
        )
    else:
        action = b"".join(
            _imu_frame(node, 100_000 + index * 100, 10 + index)
            for index, node in enumerate(nodes)
        )
    suffix = b"DO_NOT_READ_THIS_SUFFIX"
    raw = prefix + action + suffix
    raw_relative = Path("capture/raw.cobs.bin")
    raw_path = tmp_path / raw_relative
    raw_path.parent.mkdir(parents=True)
    raw_path.write_bytes(raw)
    manifest_relative = Path("capture/SHA256SUMS.txt")
    manifest_path = tmp_path / manifest_relative
    source_sha = _sha(raw)
    manifest_path.write_text(f"{source_sha}  raw.cobs.bin\n", encoding="utf-8")
    # The real dataset manifest is mode 0664.  Its authority is the exact
    # registered digest read from one O_RDONLY/no-follow snapshot, not mode
    # bits, so the fixture deliberately remains writable.
    manifest_path.chmod(0o664)
    source_stat = raw_path.lstat()
    monkeypatch.setattr(reader_module, "RAW_RELATIVE", raw_relative)
    monkeypatch.setattr(reader_module, "FORMAL_MANIFEST_RELATIVE", manifest_relative)
    monkeypatch.setattr(reader_module, "_MANIFEST_RAW_NAME", "raw.cobs.bin")
    monkeypatch.setattr(reader_module, "FORMAL_MANIFEST_SHA256", _sha(manifest_path.read_bytes()))
    monkeypatch.setattr(reader_module, "SOURCE_SHA256", source_sha)
    monkeypatch.setattr(reader_module, "SOURCE_STAT_IDENTITY", (
        source_stat.st_dev, source_stat.st_ino, source_stat.st_size, source_stat.st_mtime_ns,
    ))
    monkeypatch.setattr(reader_module, "ACTION00_START_OFFSET", len(prefix))
    monkeypatch.setattr(reader_module, "ACTION00_STOP_OFFSET", len(prefix) + len(action))
    monkeypatch.setattr(reader_module, "ACTION00_START_NS", 90_000_000)
    monkeypatch.setattr(reader_module, "ACTION00_STOP_NS", 200_000_000)
    monkeypatch.setattr(
        reader_module, "ACTION00_SLICE_SHA256",
        "f" * 64 if bad_slice_hash else _sha(action),
    )
    bindings = tuple(
        NodeClockBinding(
            f"BSF{node:04X}", 4, "B306_TIMER2", _sha(f"mapping:{node}".encode()),
            1_000.0, 0.0, "b" * 64, "c" * 64,
        )
        for node in range(0x1000, 0x100A)
    )
    return raw_path, ContinuousClockOwner(CONTINUOUS_FRONTEND_SCHEMA, bindings), len(prefix), len(action)


def test_full_reader_preserves_all_identity_and_never_reads_suffix(tmp_path: Path, monkeypatch) -> None:
    raw_path, clocks, prefix_size, action_size = _full_reader_fixture(tmp_path, monkeypatch)
    reader = reader_module.Action00EngineeringPolicyReader(
        root=tmp_path, clock_owner=clocks, chunk_bytes=13,
    )
    result = reader.read()
    assert result.role == ROLE
    assert result.access_audit["prefix_bytes_read"] == prefix_size
    assert result.access_audit["action00_bytes_read"] == action_size
    assert result.access_audit["bytes_after_action00_read"] == 0
    assert result.access_audit["read_ahead_performed"] is False
    assert result.access_audit["source_stat_identity"] == list(reader_module.SOURCE_STAT_IDENTITY)
    assert len(result.continuous_imu_events) == 20
    assert set(result.access_audit["decoded_imu_by_node"]) == {
        f"BSF{node:04X}" for node in range(0x1000, 0x100A)
    }
    for event in result.continuous_imu_events:
        record = event.payload_owner
        validate_event_clock(event, clocks)
        assert event.event_id == (
            f"v47:{record.raw.record_index}:{record.raw.sample_index}:"
            f"{record.raw.start_offset}:{record.raw.end_offset}:{record.raw.encoded_sha256}"
        )
        assert event.imu_timer2.timer2_base_us == record.payload["base_timer2_us"]
        assert event.imu_timer2.trigger_timer2_us == (
            record.payload["base_timer2_us"] + record.payload["delta_us"]
        )
        same_record = [
            item for item in result.typed_events
            if item.raw.record_index == record.raw.record_index
            and item.record_type.value == "IMU"
        ]
        expected_availability = clocks.binding_for(record.node_id).global_ns(
            max(item.node_timer_us for item in same_record)
        )
        assert event.availability_global_ns == expected_availability
    assert result.continuous_imu_events[0].payload_owner.raw.record_index == 2
    assert raw_path.read_bytes().endswith(b"DO_NOT_READ_THIS_SUFFIX")
    with pytest.raises(RuntimeError, match="one-shot"):
        reader.read()


def test_full_reader_partitions_transport_envelope_at_exact_time_boundaries(
    tmp_path: Path, monkeypatch,
) -> None:
    _raw, clocks, _prefix, _action = _full_reader_fixture(
        tmp_path, monkeypatch, boundary_envelope=True,
    )
    result = reader_module.Action00EngineeringPolicyReader(
        root=tmp_path, clock_owner=clocks, chunk_bytes=17,
    ).read()

    assert len(result.typed_events) == 100
    assert len(result.continuous_imu_events) == 40
    assert all(
        reader_module.ACTION00_START_NS <= event.common_global_ns
        < reader_module.ACTION00_STOP_NS
        for event in result.continuous_imu_events
    )
    partitions = result.access_audit["imu_time_partitions"]
    assert result.access_audit["decoded_imu_count"] == 100
    assert result.access_audit["imu_time_partition_conserved"] is True
    for node in sorted(partitions["in_action"]):
        assert partitions["before_action"][node]["count"] == 3
        assert partitions["in_action"][node]["count"] == 4
        assert partitions["at_or_after_stop"][node]["count"] == 3
        assert partitions["in_action"][node]["min_common_ns"] == 90_000_000
        assert partitions["in_action"][node]["max_common_ns"] == 195_000_000
        assert partitions["at_or_after_stop"][node]["min_common_ns"] == 200_000_000
        assert len(partitions["before_action"][node]["identity_sha256"]) == 64
        assert len(partitions["at_or_after_stop"][node]["identity_sha256"]) == 64

    # The 195 ms sample inherits availability from the final 200 ms sample in
    # the same transport batch; filtering must not invent earlier availability.
    assert all(
        event.availability_global_ns == 200_000_000
        for event in result.continuous_imu_events
        if event.common_global_ns == 195_000_000
    )


def test_reader_orientation_join_accepts_shared_batch_final_availability(
    tmp_path: Path, monkeypatch,
) -> None:
    _raw, clocks, _prefix, _action = _full_reader_fixture(
        tmp_path, monkeypatch, boundary_envelope=True,
    )
    decoded = reader_module.Action00EngineeringPolicyReader(
        root=tmp_path, clock_owner=clocks, chunk_bytes=19,
    ).read()
    nodes = sorted(decoded.decoded_action.rows_by_node)
    initial = {"nodes": {node: {
        "gyro_bias_rad_s": [0.0, 0.0, 0.0],
        "gyro_bias_covariance_rad2_s2": (np.eye(3) * 1e-8).tolist(),
        "gyro_observation_covariance_rad2_s2": (np.eye(3) * 1e-7).tolist(),
        "accelerometer_norm_mps2": 9.80665,
        "accelerometer_observation_covariance_m2_s4": (np.eye(3) * 1e-4).tolist(),
    } for node in nodes}}
    settings = {
        "execution_contract": {"initial_stochastic_state_relative_path": "initial.json"},
    }
    authority, capability = _runtime_vqf_tilt_authority(
        seal_authority={
            "seal_sha256": "1" * 64,
            "qualified_source_hashes": {"initial.json": "2" * 64},
        },
        settings=settings,
        initial_semantic_sha256="3" * 64,
        settings_semantic_sha256="4" * 64,
    )
    state = ContinuousVQFState(
        initial, execution_guard=_Guard(), unknown_boot_orientation_sigma_rad=1.0,
        unknown_unusable_episode_orientation_sigma_rad=0.5,
        tilt_diagnostic_runtime_authority=authority,
        _tilt_provenance_capability=capability,
    )
    oriented = state.process(decoded.decoded_action)
    join = AuthenticatedVQFTiltClockJoin(
        clock_owner=clocks,
        diagnostic_provenance_digest=oriented.vqf_tilt_diagnostic_provenance.digest,
    )
    events = {
        (
            event.node_id, event.payload_owner.raw.start_offset,
            event.payload_owner.raw.end_offset, event.payload_owner.raw.sample_index,
        ): event
        for event in decoded.continuous_imu_events
    }
    joined = []
    for node in nodes:
        for index in range(len(oriented.time_us_by_node[node])):
            key = (
                node, int(oriented.raw_start_offset_by_node[node][index]),
                int(oriented.raw_end_offset_by_node[node][index]),
                int(oriented.raw_sample_index_by_node[node][index]),
            )
            joined.append(join.commit(join.prepare(
                oriented, event=events[key], index=index,
            )))
    assert len(joined) == 40
    shared = [
        row for row in joined
        if row.node == nodes[0] and row.timer2_us in (100_000, 105_000)
    ]
    assert [row.availability_global_ns for row in shared] == [105_000_000, 105_000_000]


def test_full_reader_partition_is_chunk_invariant(tmp_path: Path, monkeypatch) -> None:
    first_root = tmp_path / "first"
    _raw, clocks, _prefix, _action = _full_reader_fixture(
        first_root, monkeypatch, boundary_envelope=True,
    )
    first = reader_module.Action00EngineeringPolicyReader(
        root=first_root, clock_owner=clocks, chunk_bytes=7,
    ).read()
    first_identity = tuple(event.event_id for event in first.continuous_imu_events)
    first_partitions = {
        partition: {
            node: dict(row) for node, row in nodes.items()
        }
        for partition, nodes in first.access_audit["imu_time_partitions"].items()
    }

    second_root = tmp_path / "second"
    _raw, clocks, _prefix, _action = _full_reader_fixture(
        second_root, monkeypatch, boundary_envelope=True,
    )
    second = reader_module.Action00EngineeringPolicyReader(
        root=second_root, clock_owner=clocks, chunk_bytes=1 << 20,
    ).read()
    assert tuple(event.event_id for event in second.continuous_imu_events) == first_identity
    assert {
        partition: {node: dict(row) for node, row in nodes.items()}
        for partition, nodes in second.access_audit["imu_time_partitions"].items()
    } == first_partitions


def test_full_reader_fails_closed_for_hash_stat_and_postdecode(tmp_path: Path, monkeypatch) -> None:
    _raw, clocks, _prefix, _action = _full_reader_fixture(
        tmp_path, monkeypatch, bad_slice_hash=True,
    )
    reader = reader_module.Action00EngineeringPolicyReader(root=tmp_path, clock_owner=clocks)
    with pytest.raises(ValueError, match="hash mismatch"):
        reader.read()
    assert reader.last_attempt_audit["status"] == "FAILED_CLOSED"
    assert reader.last_attempt_audit["bytes_after_action00_read"] == 0

    other = tmp_path / "other"
    raw_path, clocks, _prefix, _action = _full_reader_fixture(other, monkeypatch)
    expected = reader_module.SOURCE_STAT_IDENTITY
    monkeypatch.setattr(reader_module, "SOURCE_STAT_IDENTITY", (
        expected[0], expected[1], expected[2] + 1, expected[3],
    ))
    with pytest.raises(RuntimeError, match="stat identity"):
        reader_module.Action00EngineeringPolicyReader(root=other, clock_owner=clocks)

    third = tmp_path / "third"
    _raw, clocks, _prefix, _action = _full_reader_fixture(
        third, monkeypatch, node_count=9,
    )
    reader = reader_module.Action00EngineeringPolicyReader(root=third, clock_owner=clocks)
    with pytest.raises(RuntimeError, match="retain all ten"):
        reader.read()
    assert reader.last_attempt_audit["status"] == "FAILED_CLOSED_AFTER_BOUNDED_DECODE"
    assert reader.last_attempt_audit["bytes_after_action00_read"] == 0
