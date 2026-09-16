"""Exact bounded Capture2 Action00 reader for engineering-policy issuance.

This owner adds one role to the existing streaming v47 decoder.  It never
discovers a range, decodes the prefix, or reads beyond the Action00 stop byte.
The prefix pass exists solely to preserve the full-container nonempty-COBS
record ordinal used by :class:`RawByteProvenance`.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import stat
from types import MappingProxyType
from typing import BinaryIO, Mapping

import numpy as np

from biospur_fusion.ingest.events import RecordType, TypedEvent
from biospur_fusion.v0.c2_progressive.range_reader import DecodedAction, IMU_DTYPE

from .continuous_frontend import ActionInterval, ContinuousClockOwner, ContinuousEvent
from .continuous_stage2_adapter import EventRegionOwner, adapt_verified_record
from .continuous_streaming_runner import AuthorizedByteWindow, IncrementalV47WindowDecoder


ROLE = "ACTION00_ENGINEERING_POLICY"
ACTION_ID = "00_initial_still"
ACTION_INDEX = 0
ACTION00_START_OFFSET = 213_648_544
ACTION00_STOP_OFFSET = 216_084_573
ACTION00_START_NS = 234_836_221_471_621
ACTION00_STOP_NS = 234_866_246_815_581
ACTION00_SLICE_SHA256 = "ee08b44c3383e74099dc80a5c92bfaef500fa6bb472bc774655aa5b845485d5b"
SOURCE_SHA256 = "74c1fdbbe7c302bc21b0665bff50137e84537946a347ea11133e1e6751c84268"
FORMAL_MANIFEST_SHA256 = "c2682d5dad06feb4edea801ebc8981c324d20c2c0f515be9de33ce03ac743417"
SOURCE_STAT_IDENTITY = (2097, 5_266_758, 305_368_868, 1_786_977_119_411_212_631)
RAW_RELATIVE = Path(
    "datasets/phase2_calibration/"
    "phase2_targeted_calibration_20260817t130918z_capture_2_with_joint_label_c8645eb2/"
    "system/fusion_continuous/fusion_host_raw.cobs.bin"
)
FORMAL_MANIFEST_RELATIVE = Path(
    "datasets/phase2_calibration/"
    "phase2_targeted_calibration_20260817t130918z_capture_2_with_joint_label_c8645eb2/"
    "checksums/SHA256SUMS.txt"
)
_MANIFEST_RAW_NAME = "system/fusion_continuous/fusion_host_raw.cobs.bin"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def count_nonempty_cobs_prefix(
    source: BinaryIO, *, stop_offset: int, chunk_bytes: int,
) -> tuple[int, int]:
    """Count canonical record ordinals without decoding any prefix payload.

    The source must start at byte zero.  A nonempty tail at ``stop_offset``
    proves the requested boundary cuts a record and is rejected.
    """
    if (
        type(stop_offset) is not int or stop_offset < 0
        or type(chunk_bytes) is not int or not 1 <= chunk_bytes <= 1 << 20
        or source.tell() != 0
    ):
        raise ValueError("invalid canonical COBS prefix pass")
    count = 0
    pending_nonempty = False
    consumed = 0
    while consumed < stop_offset:
        block = source.read(min(chunk_bytes, stop_offset - consumed))
        if not block:
            raise OSError("source ended inside ordinal prefix")
        consumed += len(block)
        for value in block:
            if value == 0:
                if pending_nonempty:
                    count += 1
                pending_nonempty = False
            else:
                pending_nonempty = True
    if pending_nonempty:
        raise ValueError("Action00 start is not a complete COBS-record boundary")
    return count, consumed


def _partition_identity(record: TypedEvent, common_ns: int) -> bytes:
    """Canonical identity used to prove envelope partition conservation."""
    raw = record.raw
    if raw is None:
        raise ValueError("decoded Action00 IMU lacks raw-byte provenance")
    return json.dumps([
        record.node_id, record.boot_epoch, record.sequence, record.node_timer_us,
        common_ns, raw.record_index, raw.sample_index, raw.start_offset,
        raw.end_offset, raw.encoded_sha256,
    ], separators=(",", ":"), ensure_ascii=True).encode("ascii") + b"\n"


def _new_partition_stats(clock_owner: ContinuousClockOwner) -> dict[str, dict[str, object]]:
    return {
        binding.node_id: {
            "count": 0, "digest": hashlib.sha256(),
            "min_common_ns": None, "max_common_ns": None,
        }
        for binding in clock_owner.bindings
    }


def _note_partition(
    stats: dict[str, dict[str, object]], record: TypedEvent, common_ns: int,
) -> None:
    row = stats[record.node_id]
    row["count"] = int(row["count"]) + 1
    digest = row["digest"]
    if not isinstance(digest, type(hashlib.sha256())):
        raise AssertionError("partition digest owner is invalid")
    digest.update(_partition_identity(record, common_ns))
    minimum = row["min_common_ns"]
    maximum = row["max_common_ns"]
    row["min_common_ns"] = common_ns if minimum is None else min(int(minimum), common_ns)
    row["max_common_ns"] = common_ns if maximum is None else max(int(maximum), common_ns)


def _finish_partition_stats(
    stats: dict[str, dict[str, object]],
) -> Mapping[str, Mapping[str, object]]:
    finished: dict[str, Mapping[str, object]] = {}
    for node, row in sorted(stats.items()):
        digest = row["digest"]
        if not isinstance(digest, type(hashlib.sha256())):
            raise AssertionError("partition digest owner is invalid")
        finished[node] = MappingProxyType({
            "count": int(row["count"]),
            "identity_sha256": digest.hexdigest(),
            "min_common_ns": row["min_common_ns"],
            "max_common_ns": row["max_common_ns"],
        })
    return MappingProxyType(finished)


@dataclass(frozen=True)
class Action00EngineeringDecode:
    role: str
    typed_events: tuple[TypedEvent, ...]
    continuous_imu_events: tuple[ContinuousEvent, ...]
    decoded_action: DecodedAction
    access_audit: Mapping[str, object]

    def __post_init__(self) -> None:
        if self.role != ROLE:
            raise ValueError("decoded Action00 result has a foreign role")


class Action00EngineeringPolicyReader:
    """One-shot full-Action00 canonical decoder with original event identity."""

    def __init__(self, *, root: Path, clock_owner: ContinuousClockOwner,
                 chunk_bytes: int = 1 << 20, maximum_events: int = 200_000) -> None:
        self.root = Path(root).resolve()
        self.raw_path = (self.root / RAW_RELATIVE).resolve()
        self.manifest_path = (self.root / FORMAL_MANIFEST_RELATIVE).resolve()
        if not self.raw_path.is_relative_to(self.root) or not self.manifest_path.is_relative_to(self.root):
            raise RuntimeError("Action00 source path escapes workspace")
        if type(clock_owner) is not ContinuousClockOwner or len(clock_owner.bindings) != 10:
            raise TypeError("Action00 reader requires the authoritative ten-node clock owner")
        if type(chunk_bytes) is not int or not 1 <= chunk_bytes <= 1 << 20:
            raise ValueError("invalid bounded read chunk size")
        if type(maximum_events) is not int or maximum_events <= 0:
            raise ValueError("invalid Action00 event cap")
        self.clock_owner = clock_owner
        self.chunk_bytes = chunk_bytes
        self.maximum_events = maximum_events
        self._consumed = False
        self.last_attempt_audit: Mapping[str, object] | None = None
        self._validate_source_owner()

    def _validate_source_owner(self) -> None:
        manifest_stat = self.manifest_path.lstat()
        if not stat.S_ISREG(manifest_stat.st_mode) or stat.S_ISLNK(manifest_stat.st_mode):
            raise RuntimeError("formal source manifest must be a regular non-symlink file")
        # Dataset manifests are user-owned evidence and may legitimately be
        # mode 0664.  Identity comes from one read-only, no-follow snapshot and
        # its registered digest, not from Unix write bits.
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(self.manifest_path, flags)
        with os.fdopen(fd, "rb", closefd=True) as handle:
            opened_before = os.fstat(handle.fileno())
            manifest_bytes = handle.read()
            opened_after = os.fstat(handle.fileno())
        if (
            self._stat_identity(manifest_stat) != self._stat_identity(opened_before)
            or self._stat_identity(opened_before) != self._stat_identity(opened_after)
        ):
            raise RuntimeError("formal source manifest changed during identity read")
        if hashlib.sha256(manifest_bytes).hexdigest() != FORMAL_MANIFEST_SHA256:
            raise RuntimeError("formal source manifest SHA-256 mismatch")
        matches = []
        for line in manifest_bytes.decode("utf-8").splitlines():
            fields = line.split(maxsplit=1)
            if len(fields) == 2 and fields[1].lstrip("*") == _MANIFEST_RAW_NAME:
                matches.append(fields[0])
        if matches != [SOURCE_SHA256]:
            raise RuntimeError("formal manifest does not uniquely bind the continuous source")
        raw_stat = self.raw_path.lstat()
        if not stat.S_ISREG(raw_stat.st_mode) or stat.S_ISLNK(raw_stat.st_mode):
            raise RuntimeError("continuous source must be a regular non-symlink file")
        if self._stat_identity(raw_stat) != SOURCE_STAT_IDENTITY:
            raise RuntimeError("continuous source stat identity differs from preregistration")

    @staticmethod
    def _stat_identity(value: os.stat_result) -> tuple[int, int, int, int]:
        return int(value.st_dev), int(value.st_ino), int(value.st_size), int(value.st_mtime_ns)

    def read(self) -> Action00EngineeringDecode:
        if self._consumed:
            raise RuntimeError("Action00 engineering reader is one-shot")
        self._consumed = True
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
        fd = os.open(self.raw_path, flags)
        prefix_count = prefix_bytes = slice_bytes = 0
        decoder: IncrementalV47WindowDecoder | None = None
        events: list[TypedEvent] = []
        try:
            with os.fdopen(fd, "rb", closefd=True) as source:
                if self._stat_identity(os.fstat(source.fileno())) != SOURCE_STAT_IDENTITY:
                    raise RuntimeError("continuous source changed between validation and open")
                prefix_count, prefix_bytes = count_nonempty_cobs_prefix(
                    source, stop_offset=ACTION00_START_OFFSET, chunk_bytes=self.chunk_bytes,
                )
                boots = {
                    f"{binding.node_id}:{kind}": binding.boot_epoch
                    for binding in self.clock_owner.bindings for kind in (1, 3)
                }
                authorization = AuthorizedByteWindow(
                    str(RAW_RELATIVE), SOURCE_SHA256, ACTION00_START_OFFSET,
                    ACTION00_STOP_OFFSET, prefix_count, ACTION00_SLICE_SHA256,
                    ACTION00_START_NS, ACTION00_STOP_NS, boots, 4096,
                )
                decoder = IncrementalV47WindowDecoder(
                    authorization, maximum_emitted_events=self.maximum_events,
                )
                cursor = ACTION00_START_OFFSET
                while cursor < ACTION00_STOP_OFFSET:
                    block = source.read(min(self.chunk_bytes, ACTION00_STOP_OFFSET - cursor))
                    if not block:
                        raise OSError("source ended inside Action00")
                    events.extend(decoder.feed(block, absolute_offset=cursor))
                    cursor += len(block)
                    slice_bytes += len(block)
                decoder.finish()
                if source.tell() != ACTION00_STOP_OFFSET:
                    raise AssertionError("Action00 reader crossed its hard stop")
                if self._stat_identity(os.fstat(source.fileno())) != SOURCE_STAT_IDENTITY:
                    raise RuntimeError("continuous source changed during bounded read")
        except BaseException as exc:
            self.last_attempt_audit = MappingProxyType({
                "status": "FAILED_CLOSED", "role": ROLE,
                "prefix_bytes_read": prefix_bytes, "action00_bytes_read": slice_bytes,
                "bytes_after_action00_read": 0, "exception_type": type(exc).__name__,
                "exception_message": str(exc),
            })
            raise

        try:
            interval = ActionInterval(
                ACTION_INDEX, ACTION_ID, ACTION00_START_NS, ACTION00_STOP_NS,
            )
            by_record: dict[tuple[int, int, int, str], int] = {}
            for record in events:
                if record.record_type is RecordType.IMU and record.raw is not None:
                    key = (
                        record.raw.record_index, record.raw.start_offset,
                        record.raw.end_offset, record.raw.encoded_sha256,
                    )
                    by_record[key] = max(by_record.get(key, 0), int(record.node_timer_us))
            continuous: list[ContinuousEvent] = []
            rows: dict[str, list[tuple[object, ...]]] = {
                binding.node_id: [] for binding in self.clock_owner.bindings
            }
            partition_stats = {
                "before_action": _new_partition_stats(self.clock_owner),
                "in_action": _new_partition_stats(self.clock_owner),
                "at_or_after_stop": _new_partition_stats(self.clock_owner),
            }
            decoded_imu_count = 0
            for record in events:
                if record.record_type is not RecordType.IMU:
                    continue
                if record.raw is None or record.node_id not in rows:
                    raise ValueError("decoded Action00 IMU lacks owned provenance/node")
                decoded_imu_count += 1
                key = (
                    record.raw.record_index, record.raw.start_offset,
                    record.raw.end_offset, record.raw.encoded_sha256,
                )
                binding = self.clock_owner.binding_for(record.node_id)
                common_ns = binding.global_ns(record.node_timer_us)
                if common_ns < ACTION00_START_NS:
                    partition = "before_action"
                elif common_ns >= ACTION00_STOP_NS:
                    partition = "at_or_after_stop"
                else:
                    partition = "in_action"
                _note_partition(partition_stats[partition], record, common_ns)
                if partition != "in_action":
                    continue
                availability = binding.global_ns(by_record[key])
                event = adapt_verified_record(
                    record, availability_global_ns=availability,
                    region_owner=EventRegionOwner(action=interval),
                    clock_owner=self.clock_owner,
                )
                continuous.append(event)
                rows[record.node_id].append((
                    record.boot_epoch, record.sequence, record.node_timer_us,
                    tuple(record.payload["acc_raw"]), tuple(record.payload["gyro_raw"]),
                    record.raw.start_offset, record.raw.end_offset,
                    record.raw.sample_index, 1,
                ))
            arrays = {node: np.asarray(values, dtype=IMU_DTYPE) for node, values in rows.items()}
            for array in arrays.values():
                array.setflags(write=False)
            counts = Counter(event.node_id for event in continuous)
            if set(counts) != set(rows) or any(value <= 0 for value in counts.values()):
                raise RuntimeError("Action00 time partition did not retain all ten node streams")
            finished_partitions = MappingProxyType({
                name: _finish_partition_stats(stats)
                for name, stats in partition_stats.items()
            })
            partition_total = sum(
                int(row["count"])
                for partition in finished_partitions.values()
                for row in partition.values()
            )
            if partition_total != decoded_imu_count or sum(counts.values()) != sum(
                int(row["count"])
                for row in finished_partitions["in_action"].values()
            ):
                raise RuntimeError("Action00 time partition did not conserve decoded IMU identity")
        except BaseException as exc:
            self.last_attempt_audit = MappingProxyType({
                "status": "FAILED_CLOSED_AFTER_BOUNDED_DECODE", "role": ROLE,
                "prefix_bytes_read": prefix_bytes, "action00_bytes_read": slice_bytes,
                "bytes_after_action00_read": 0, "exception_type": type(exc).__name__,
                "exception_message": str(exc),
            })
            raise
        access = MappingProxyType({
            "schema": "biospur.c2.action00_engineering_policy_read.v1",
            "status": "COMPLETE", "role": ROLE,
            "source_sha256_from_formal_manifest": SOURCE_SHA256,
            "source_stat_identity": list(SOURCE_STAT_IDENTITY),
            "formal_manifest_sha256": FORMAL_MANIFEST_SHA256,
            "prefix_interval": [0, ACTION00_START_OFFSET],
            "prefix_operation": "NONEMPTY_COBS_ORDINAL_COUNT_ONLY_NO_FRAME_DECODE",
            "prefix_record_count": prefix_count, "prefix_bytes_read": prefix_bytes,
            "requested_half_open_interval": [ACTION00_START_OFFSET, ACTION00_STOP_OFFSET],
            "actual_read_intervals": [[0, ACTION00_START_OFFSET],
                                      [ACTION00_START_OFFSET, ACTION00_STOP_OFFSET]],
            "action00_bytes_read": slice_bytes, "bytes_after_action00_read": 0,
            "bounded_slice_sha256": ACTION00_SLICE_SHA256,
            "whole_file_hash_performed": False, "read_ahead_performed": False,
            "decoded_event_count": len(events),
            "decoded_imu_count": decoded_imu_count,
            "decoded_imu_by_node": dict(sorted(counts.items())),
            "imu_time_partition_semantics": (
                "BEFORE_ACTION_LT_START__IN_ACTION_START_LE_T_LT_STOP__"
                "AT_OR_AFTER_STOP_T_GE_STOP"
            ),
            "imu_time_partitions": finished_partitions,
            "imu_time_partition_conserved": True,
            "maximum_pending_record_bytes": decoder.maximum_pending_bytes if decoder else None,
            "availability_semantics": "MAPPED_FINAL_SAMPLE_TIMER2_OF_ORIGINAL_IMU_BATCH",
        })
        decode_audit = MappingProxyType({
            "schema": "biospur.c2.action00_engineering_policy_decode.v1",
            "role": ROLE, "action": ACTION_ID, "chronological_index": ACTION_INDEX,
            "raw_half_open_interval": [ACTION00_START_OFFSET, ACTION00_STOP_OFFSET],
            "original_typed_event_identity_preserved": True,
            "continuous_event_identity_validated": True,
            "decoded_imu_count": decoded_imu_count,
            "decoded_imu_by_node": dict(sorted(counts.items())),
            "imu_time_partitions": finished_partitions,
            "imu_time_partition_conserved": True,
        })
        decoded = DecodedAction(
            ACTION_ID, ACTION_INDEX, (ACTION00_START_OFFSET, ACTION00_STOP_OFFSET),
            MappingProxyType(arrays), access, decode_audit,
        )
        self.last_attempt_audit = access
        return Action00EngineeringDecode(
            ROLE, tuple(events), tuple(continuous), decoded, access,
        )
