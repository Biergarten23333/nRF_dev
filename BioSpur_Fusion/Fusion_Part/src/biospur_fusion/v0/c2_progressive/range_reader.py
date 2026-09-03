"""Minimal sealed-range Capture-2 IMU reader.

The reader has no discovery path and no dependency on the legacy dual-capture
loader.  It opens the canonical raw container with ``O_RDONLY``, seeks to one
predeclared half-open pre-fit range, and reads exactly that many bytes.  The
COBS envelope and CRC are checked for every record, but payloads other than the
v47 IMU kind are left opaque and are never interpreted.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
import binascii
import hashlib
import json
import os
from pathlib import Path
import struct
from types import MappingProxyType
from typing import Any, Mapping, Sequence
from uuid import uuid4

import numpy as np


HOST_ENVELOPE = struct.Struct("<HBBHHIQ")
IMU_HEADER = struct.Struct("<BBHQh")
IMU_SAMPLE = struct.Struct("<Hhhhhhh")
IMU_KIND = 3
IMU_VERSION = 7
EXPECTED_STEP_US = 5_000

IMU_DTYPE = np.dtype([
    ("derived_boot_epoch", "<u2"),
    ("imu_sample_sequence", "<u2"),
    ("node_timer_us", "<u8"),
    ("acc_raw", "<i2", (3,)),
    ("gyro_raw", "<i2", (3,)),
    ("raw_start_offset", "<u8"),
    ("raw_end_offset", "<u8"),
    ("raw_sample_index", "u1"),
    ("decode_acceptance_status", "u1"),
])


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _cobs_decode(encoded: bytes) -> bytes:
    output = bytearray()
    cursor = 0
    while cursor < len(encoded):
        code = encoded[cursor]
        cursor += 1
        if code == 0 or cursor + code - 1 > len(encoded):
            raise ValueError("invalid COBS code")
        output.extend(encoded[cursor:cursor + code - 1])
        cursor += code - 1
        if code != 0xFF and cursor < len(encoded):
            output.append(0)
    return bytes(output)


@dataclass
class _NodeContinuity:
    boot_epoch: int = 0
    last_timer_us: int | None = None
    last_sequence: int | None = None
    last_action_index: int | None = None
    samples: int = 0
    duplicate_or_nonmonotone: int = 0
    missing_sample_slots: int = 0
    inter_episode_gaps: list[dict[str, Any]] = field(default_factory=list)
    within_episode_gaps: list[dict[str, Any]] = field(default_factory=list)


class CaptureWideImuState:
    """One persistent timer/boot/sequence state per node for the whole capture."""

    def __init__(self, nodes: Sequence[str], *, expected_step_us: int = EXPECTED_STEP_US) -> None:
        self.expected_step_us = int(expected_step_us)
        self._nodes = {str(node): _NodeContinuity() for node in nodes}
        self.action_order: list[str] = []
        self.reset_count = 0

    @property
    def nodes(self) -> tuple[str, ...]:
        return tuple(self._nodes)

    def begin_action(self, action: str, chronological_index: int) -> None:
        if chronological_index != len(self.action_order):
            raise ValueError("actions must enter the capture-wide state in sealed order")
        self.action_order.append(str(action))

    def observe(
        self,
        *,
        node: str,
        action: str,
        action_index: int,
        sequence: int,
        timer_us: int,
    ) -> int:
        state = self._nodes[node]
        previous_timer = state.last_timer_us
        previous_action = state.last_action_index
        if previous_timer is not None and timer_us < previous_timer:
            state.boot_epoch += 1
        elif previous_timer is not None:
            dt_us = int(timer_us - previous_timer)
            if dt_us <= 0:
                state.duplicate_or_nonmonotone += 1
            else:
                missing = max(0, int(round(dt_us / self.expected_step_us)) - 1)
                if previous_action != action_index:
                    state.inter_episode_gaps.append({
                        "from_action_index": int(previous_action),
                        "to_action_index": int(action_index),
                        "to_action": str(action),
                        "gap_us": dt_us,
                        "policy": "NO_UPDATE_COVARIANCE_GROWTH;NOT_CONCATENATED",
                    })
                elif missing:
                    state.missing_sample_slots += missing
                    state.within_episode_gaps.append({
                        "action": str(action),
                        "action_index": int(action_index),
                        "gap_us": dt_us,
                        "missing_sample_slots": missing,
                    })
        if state.last_sequence is not None and previous_action == action_index:
            sequence_step = (int(sequence) - int(state.last_sequence)) & 0xFFFF
            if sequence_step == 0:
                state.duplicate_or_nonmonotone += 1
        state.last_timer_us = int(timer_us)
        state.last_sequence = int(sequence)
        state.last_action_index = int(action_index)
        state.samples += 1
        return state.boot_epoch

    def audit(self) -> dict[str, Any]:
        return {
            "schema": "biospur-c2-capture-wide-imu-continuity-v1",
            "node_state_count": len(self._nodes),
            "states_created_per_node": 1,
            "episode_reset_count": self.reset_count,
            "gaps_concatenated": False,
            "gap_policy": "NO_UPDATE_WITH_COVARIANCE_GROWTH",
            "expected_step_us": self.expected_step_us,
            "action_order": list(self.action_order),
            "nodes": {
                node: {
                    "boot_epoch_final": state.boot_epoch,
                    "samples": state.samples,
                    "duplicate_or_nonmonotone": state.duplicate_or_nonmonotone,
                    "missing_sample_slots_within_open_ranges_only": state.missing_sample_slots,
                    "sealed_inter_episode_duration_counted_as_missing_samples": False,
                    "inter_episode_gaps": list(state.inter_episode_gaps),
                    "within_episode_gaps": list(state.within_episode_gaps),
                }
                for node, state in self._nodes.items()
            },
        }


@dataclass(frozen=True)
class DecodedAction:
    action: str
    chronological_index: int
    interval: tuple[int, int]
    rows_by_node: Mapping[str, np.ndarray]
    access_audit: Mapping[str, Any]
    decode_audit: Mapping[str, Any]


@dataclass(frozen=True)
class DecodedEvaluationAction:
    """One continuous training+heldout decode with explicit heldout ownership.

    The training interval is reread only so the evaluation-only orientation
    frontend reaches the heldout boundary with one continuous VQF instance per
    node.  Scientific evaluation rows are the retained indices bound here;
    training rows cannot enter a heldout score or verdict.
    """

    combined_action: DecodedAction
    prefit_interval: tuple[int, int]
    heldout_interval: tuple[int, int]
    heldout_source_indices_by_node: Mapping[str, np.ndarray]
    access_audit: Mapping[str, Any]
    decode_audit: Mapping[str, Any]


class SealedPrefitRangeReader:
    """Read only the pre-fit intervals frozen in one immutable range plan."""

    def __init__(
        self,
        *,
        root: Path,
        plan_path: Path,
        expected_plan_sha256: str,
        nodes: Sequence[str],
    ) -> None:
        self.root = Path(root).resolve()
        self.plan_path = Path(plan_path).resolve()
        if self.plan_path.stat().st_mode & 0o222:
            raise RuntimeError("payload byte plan must be immutable")
        observed_plan_sha = _sha256_file(self.plan_path)
        if observed_plan_sha != expected_plan_sha256:
            raise RuntimeError("payload byte plan SHA-256 mismatch")
        self.plan_sha256 = observed_plan_sha
        self.reader_session_id = f"C2_PREFIT_READER_{uuid4().hex}"
        self.plan = json.loads(self.plan_path.read_text(encoding="utf-8"))
        if self.plan.get("schema") != "biospur-c2-main-payload-byte-access-plan-v1":
            raise RuntimeError("unexpected payload byte plan schema")
        if self.plan.get("prefit_policy") != "Only prefit_training_interval ranges may be decoded before fit freeze.":
            raise RuntimeError("prefit plan policy changed")
        self.raw_path = (self.root / self.plan["payload_file"]).resolve()
        if not self.raw_path.is_relative_to(self.root):
            raise RuntimeError("payload path escapes the canonical project")
        self.ranges = tuple(self.plan["ranges"])
        if len(self.ranges) != 19:
            raise RuntimeError("sealed prefit plan must contain exactly 19 actions")
        previous_stop = -1
        for expected_index, row in enumerate(self.ranges):
            if int(row["chronological_index"]) != expected_index:
                raise RuntimeError("sealed action chronology changed")
            start, stop = map(int, row["prefit_training_interval"])
            hold_start, hold_stop = map(int, row["fit_freeze_heldout_interval"])
            if not previous_stop < start < stop == hold_start < hold_stop:
                raise RuntimeError("invalid or overlapping sealed range plan")
            previous_stop = hold_stop
        self.nodes = tuple(str(node) for node in nodes)
        if len(self.nodes) != 10 or len(set(self.nodes)) != 10:
            raise RuntimeError("reader must bind exactly ten unique hardware IDs")
        self.state = CaptureWideImuState(self.nodes)
        self.last_read_attempt_audit: Mapping[str, Any] | None = None

    def _read_exact(
        self,
        start: int,
        stop: int,
        *,
        action: str,
        chronological_index: int,
        heldout_interval: tuple[int, int],
        prefit_interval: tuple[int, int] | None = None,
        interval_role: str = "PREFIT_TRAINING",
        heldout_bytes_touched: bool = False,
    ) -> tuple[bytes, dict[str, Any]]:
        if not 0 <= start < stop:
            raise ValueError("invalid half-open payload range")
        opened_utc = _utc_now()
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
        fd: int | None = None
        chunks: list[bytes] = []
        reads: list[list[int]] = []
        base_access = {
            "reader_session_id": self.reader_session_id,
            "action": action,
            "chronological_index": int(chronological_index),
            "opened_utc": opened_utc,
            "open_flags": ["O_RDONLY", "O_CLOEXEC"],
            "payload_path": str(self.raw_path),
            "requested_half_open_interval": [start, stop],
            "expected_read_bytes": stop - start,
            "plan_path": str(self.plan_path),
            "plan_sha256": self.plan_sha256,
            "fit_freeze_heldout_interval": list(heldout_interval),
            "prefit_training_interval": (
                None if prefit_interval is None else list(prefit_interval)
            ),
            "interval_role": str(interval_role),
            "heldout_bytes_touched": bool(heldout_bytes_touched),
            "whole_file_stat_performed": False,
            "whole_file_hash_performed": False,
            "whole_file_traversal_performed": False,
            "mmap_used": False,
            "opened_record_payload_classes": ["V47_TEN_NODE_IMU_KIND_3"],
            "other_record_payload_classes": "OPAQUE_SKIPPED_AFTER_ENVELOPE_KIND_ONLY",
        }
        self.last_read_attempt_audit = MappingProxyType({
            "status": "BOUNDED_READ_OPEN_RUNNING_NO_BYTES_YET",
            "access_audit": {
                **base_access,
                "actual_read_intervals": [],
                "actual_read_union_bytes": 0,
                "partial_bounded_slice_sha256": hashlib.sha256(b"").hexdigest(),
            },
            "decode_audit": None,
        })
        try:
            fd = os.open(self.raw_path, flags)
            reached = int(os.lseek(fd, start, os.SEEK_SET))
            if reached != start:
                raise OSError("bounded seek did not reach its exact start")
            cursor = start
            while cursor < stop:
                requested = min(1024 * 1024, stop - cursor)
                block = os.read(fd, requested)
                if not block:
                    raise OSError("short exact bounded read")
                next_cursor = cursor + len(block)
                if next_cursor > stop:
                    raise AssertionError("bounded reader crossed stop byte")
                reads.append([cursor, next_cursor])
                chunks.append(block)
                cursor = next_cursor
        except BaseException as exc:
            partial = b"".join(chunks)
            self.last_read_attempt_audit = MappingProxyType({
                "status": "BOUNDED_READ_FAILED_PARTIAL_EVIDENCE_PRESERVED",
                "access_audit": {
                    **base_access,
                    "closed_utc": _utc_now(),
                    "actual_read_intervals": [list(interval) for interval in reads],
                    "actual_read_union_bytes": int(len(partial)),
                    "partial_bounded_slice_sha256": hashlib.sha256(partial).hexdigest(),
                    "seek_operations": (
                        [{"whence": "SEEK_SET", "offset": start, "result": start}]
                        if fd is not None else []
                    ),
                },
                "decode_audit": None,
                "exception_type": type(exc).__name__,
                "exception_message": str(exc),
            })
            raise
        finally:
            if fd is not None:
                os.close(fd)
        payload = b"".join(chunks)
        if len(payload) != stop - start:
            raise OSError("exact bounded read byte count mismatch")
        return payload, {
            "opened_utc": opened_utc,
            "closed_utc": _utc_now(),
            **base_access,
            "actual_read_intervals": reads,
            "actual_read_union_bytes": int(sum(right - left for left, right in reads)),
            "seek_operations": [{"whence": "SEEK_SET", "offset": start, "result": start}],
            "bounded_slice_sha256": hashlib.sha256(payload).hexdigest(),
        }

    def read_action(self, chronological_index: int) -> DecodedAction:
        if chronological_index != len(self.state.action_order):
            raise RuntimeError("reader actions must be consumed once in sealed chronology")
        row = self.ranges[chronological_index]
        action = str(row["action"])
        start, stop = map(int, row["prefit_training_interval"])
        held_start, held_stop = map(int, row["fit_freeze_heldout_interval"])
        if stop != held_start:
            raise AssertionError("training/held-out boundary no longer abuts")
        self.state.begin_action(action, chronological_index)
        raw_bytes, access = self._read_exact(
            start,
            stop,
            action=action,
            chronological_index=chronological_index,
            heldout_interval=(held_start, held_stop),
            prefit_interval=(start, stop),
            interval_role="PREFIT_TRAINING",
            heldout_bytes_touched=False,
        )
        self.last_read_attempt_audit = MappingProxyType({
            "status": "BOUNDED_READ_COMPLETE_DECODE_RUNNING",
            "access_audit": access,
            "decode_audit": None,
        })
        try:
            rows_by_node, decode = self._decode_action(
                raw_bytes,
                raw_start=start,
                raw_stop=stop,
                action=action,
                action_index=chronological_index,
            )
        except BaseException as exc:
            self.last_read_attempt_audit = MappingProxyType({
                "status": "BOUNDED_READ_COMPLETE_DECODE_FAILED",
                "access_audit": access,
                "decode_audit": {
                    "exception_type": type(exc).__name__,
                    "exception_message": str(exc),
                },
            })
            raise
        self.last_read_attempt_audit = MappingProxyType({
            "status": "BOUNDED_READ_AND_DECODE_COMPLETE",
            "access_audit": access,
            "decode_audit": decode,
        })
        return DecodedAction(
            action=action,
            chronological_index=chronological_index,
            interval=(start, stop),
            rows_by_node=rows_by_node,
            access_audit=MappingProxyType(access),
            decode_audit=decode,
        )

    def _decode_action(
        self,
        payload: bytes,
        *,
        raw_start: int,
        raw_stop: int,
        action: str,
        action_index: int,
    ) -> tuple[dict[str, np.ndarray], dict[str, Any]]:
        rows: dict[str, list[tuple[Any, ...]]] = {node: [] for node in self.nodes}
        counts: Counter[str] = Counter()
        errors: Counter[str] = Counter()
        cursor = 0
        encoded_records = payload.split(b"\0")
        if encoded_records[-1] != b"":
            errors["range_did_not_end_on_cobs_delimiter"] += 1
        for encoded in encoded_records[:-1]:
            record_start = raw_start + cursor
            record_stop = record_start + len(encoded) + 1
            cursor += len(encoded) + 1
            if not encoded:
                counts["empty_delimiters"] += 1
                continue
            try:
                raw = _cobs_decode(encoded)
                if len(raw) < HOST_ENVELOPE.size + 2:
                    raise ValueError("short envelope")
                body = raw[:-2]
                expected_crc = struct.unpack_from("<H", raw, len(raw) - 2)[0]
                if binascii.crc_hqx(body, 0xFFFF) != expected_crc:
                    raise ValueError("CRC")
                magic, version, kind, node_id, length, host_envelope_sequence, _master_arrival_ignored = HOST_ENVELOPE.unpack_from(body)
                record_payload = memoryview(body)[HOST_ENVELOPE.size:]
                if magic != 0x5342 or version != 1 or len(record_payload) != length:
                    raise ValueError("host envelope contract")
                node = f"BSF{node_id:04X}"
                if kind != IMU_KIND:
                    counts[f"opaque_kind_{int(kind)}_skipped"] += 1
                    continue
                if node not in rows:
                    counts["non_authorized_node_imu_skipped"] += 1
                    continue
                if len(record_payload) < IMU_HEADER.size:
                    raise ValueError("short IMU payload")
                # The final header word is temperature.  It is structurally
                # unpacked only to validate the fixed v47 record shape, then
                # discarded at this boundary because temperature is outside
                # the run's allowed measurement fields.
                imu_version, sample_count, sequence, base_us, _temperature_ignored = IMU_HEADER.unpack_from(record_payload)
                if (
                    imu_version != IMU_VERSION
                    or not 1 <= sample_count <= 16
                    or len(record_payload) != IMU_HEADER.size + sample_count * IMU_SAMPLE.size
                ):
                    raise ValueError("v47 IMU payload contract")
                counts["imu_host_envelope_sequences_observed"] += 1
                for sample_index in range(sample_count):
                    delta, ax, ay, az, gx, gy, gz = IMU_SAMPLE.unpack_from(
                        record_payload, IMU_HEADER.size + sample_index * IMU_SAMPLE.size,
                    )
                    timer_us = int(base_us + delta)
                    sample_sequence = (int(sequence) + sample_index) & 0xFFFF
                    boot_epoch = self.state.observe(
                        node=node,
                        action=action,
                        action_index=action_index,
                        sequence=sample_sequence,
                        timer_us=timer_us,
                    )
                    rows[node].append((
                        boot_epoch,
                        sample_sequence,
                        timer_us,
                        (ax, ay, az),
                        (gx, gy, gz),
                        record_start,
                        record_stop,
                        sample_index,
                        1,
                    ))
                counts["imu_envelopes_decoded"] += 1
                counts["imu_samples_decoded"] += int(sample_count)
            except (ValueError, struct.error, IndexError) as exc:
                errors[f"{type(exc).__name__}:{exc}"] += 1
        if cursor != len(payload):
            errors["cobs_cursor_range_mismatch"] += 1
        output = {node: np.asarray(values, dtype=IMU_DTYPE) for node, values in rows.items()}
        return output, {
            "schema": "biospur-c2-minimal-prefit-imu-decode-v1",
            "action": action,
            "chronological_index": action_index,
            "raw_half_open_interval": [raw_start, raw_stop],
            "counts": dict(counts),
            "decode_errors": dict(errors),
            "nodes": {
                node: {
                    "rows": int(len(values)),
                    "first_timer_us": int(values["node_timer_us"][0]) if len(values) else None,
                    "last_timer_us": int(values["node_timer_us"][-1]) if len(values) else None,
                    "derived_boot_epochs": [int(value) for value in np.unique(values["derived_boot_epoch"])],
                    "strictly_increasing_within_boot": bool(all(
                        np.all(np.diff(values["node_timer_us"][values["derived_boot_epoch"] == boot]) > 0)
                        for boot in np.unique(values["derived_boot_epoch"])
                    )) if len(values) else False,
                }
                for node, values in output.items()
            },
            "decoded_measurement_fields": [
                "acc_raw", "gyro_raw", "node_timer_us",
                "derived_boot_epoch_from_timer_regression",
                "imu_sample_sequence_from_imu_header_sequence_plus_sample_index",
                "decode_acceptance_status",
            ],
            "device_carried_status_field_present": False,
            "decode_acceptance_status_semantics": "1=COBS_ENVELOPE_CRC_AND_V47_IMU_SHAPE_ACCEPTED;NOT_A_B306_CARRIED_STATUS",
            "derived_boot_epoch_semantics": "INCREMENTED_ONLY_ON_NODE_TIMER_REGRESSION;NOT_A_B306_CARRIED_BOOT_FIELD",
            "sequence_semantics": {
                "host_envelope_sequence": "STRUCTURALLY_DECODED_AND_KEPT_DISTINCT;NOT_STORED_AS_IMU_SAMPLE_SEQUENCE",
                "imu_sample_sequence": "IMU_HEADER_SEQUENCE_PLUS_SAMPLE_INDEX_MODULO_65536"
            },
            "magnetometer_decoded": False,
            "vendor_quaternion_or_euler_decoded": False,
            "uwb_or_spatial_payload_decoded": False,
            "non_imu_envelope_payload_interpreted": False,
        }


class SealedHeldoutRangeReader(SealedPrefitRangeReader):
    """Read exact held-out ranges only after an external frozen-state gate.

    This reader deliberately owns no calibration state and exposes no fit API.
    The qualified evaluator must validate the immutable post-fresh hold-out
    transition before constructing it.
    """

    def __init__(
        self,
        *,
        root: Path,
        plan_path: Path,
        expected_plan_sha256: str,
        nodes: Sequence[str],
    ) -> None:
        super().__init__(
            root=root,
            plan_path=plan_path,
            expected_plan_sha256=expected_plan_sha256,
            nodes=nodes,
        )
        self.reader_session_id = f"C2_HELDOUT_READER_{uuid4().hex}"

    def read_action(self, chronological_index: int) -> DecodedAction:
        if chronological_index != len(self.state.action_order):
            raise RuntimeError("held-out reader actions must be consumed once in sealed chronology")
        row = self.ranges[chronological_index]
        action = str(row["action"])
        prefit_start, prefit_stop = map(int, row["prefit_training_interval"])
        held_start, held_stop = map(int, row["fit_freeze_heldout_interval"])
        if prefit_stop != held_start:
            raise AssertionError("training/held-out boundary no longer abuts")
        self.state.begin_action(action, chronological_index)
        raw_bytes, access = self._read_exact(
            held_start,
            held_stop,
            action=action,
            chronological_index=chronological_index,
            heldout_interval=(held_start, held_stop),
            prefit_interval=(prefit_start, prefit_stop),
            interval_role="POST_FRESH_FROZEN_HELDOUT_EVALUATION",
            heldout_bytes_touched=True,
        )
        self.last_read_attempt_audit = MappingProxyType({
            "status": "BOUNDED_HELDOUT_READ_COMPLETE_DECODE_RUNNING",
            "access_audit": access,
            "decode_audit": None,
        })
        try:
            rows_by_node, decode = self._decode_action(
                raw_bytes,
                raw_start=held_start,
                raw_stop=held_stop,
                action=action,
                action_index=chronological_index,
            )
        except BaseException as exc:
            self.last_read_attempt_audit = MappingProxyType({
                "status": "BOUNDED_HELDOUT_READ_COMPLETE_DECODE_FAILED",
                "access_audit": access,
                "decode_audit": {
                    "exception_type": type(exc).__name__,
                    "exception_message": str(exc),
                },
            })
            raise
        decode = {
            **dict(decode),
            "decode_role": "POST_FRESH_FROZEN_HELDOUT_EVALUATION",
            "fit_or_owner_update_allowed": False,
        }
        self.last_read_attempt_audit = MappingProxyType({
            "status": "BOUNDED_HELDOUT_READ_AND_DECODE_COMPLETE",
            "access_audit": access,
            "decode_audit": decode,
        })
        return DecodedAction(
            action=action,
            chronological_index=chronological_index,
            interval=(held_start, held_stop),
            rows_by_node=rows_by_node,
            access_audit=MappingProxyType(access),
            decode_audit=MappingProxyType(decode),
        )


class SealedFrozenEvaluationRangeReader(SealedPrefitRangeReader):
    """Reread exact training then heldout ranges for frozen scientific evaluation.

    This reader owns no fit API.  Each action is decoded as two separately
    audited half-open ranges through one capture-wide timer/sequence state.
    The concatenated rows exist only to reconstruct continuous VQF state; the
    exact per-node heldout source indices are separately immutable.
    """

    def __init__(
        self,
        *,
        root: Path,
        plan_path: Path,
        expected_plan_sha256: str,
        nodes: Sequence[str],
    ) -> None:
        super().__init__(
            root=root,
            plan_path=plan_path,
            expected_plan_sha256=expected_plan_sha256,
            nodes=nodes,
        )
        self.reader_session_id = f"C2_FROZEN_EVALUATION_READER_{uuid4().hex}"

    def read_action(self, chronological_index: int) -> DecodedEvaluationAction:
        if chronological_index != len(self.state.action_order):
            raise RuntimeError(
                "frozen-evaluation reader actions must be consumed once in sealed chronology"
            )
        row = self.ranges[chronological_index]
        action = str(row["action"])
        prefit_start, prefit_stop = map(int, row["prefit_training_interval"])
        held_start, held_stop = map(int, row["fit_freeze_heldout_interval"])
        if prefit_stop != held_start:
            raise AssertionError("training/heldout boundary no longer abuts")
        self.state.begin_action(action, chronological_index)

        range_audits: dict[str, Any] = {}
        decode_audits: dict[str, Any] = {}
        rows_by_role: dict[str, Mapping[str, np.ndarray]] = {}
        specifications = (
            (
                "PREFIT_ORIENTATION_RECONSTRUCTION_ONLY",
                prefit_start,
                prefit_stop,
                False,
            ),
            (
                "HELDOUT_FROZEN_SCIENTIFIC_EVALUATION",
                held_start,
                held_stop,
                True,
            ),
        )
        for role, start, stop, heldout_touched in specifications:
            try:
                payload, access = self._read_exact(
                    start,
                    stop,
                    action=action,
                    chronological_index=chronological_index,
                    heldout_interval=(held_start, held_stop),
                    prefit_interval=(prefit_start, prefit_stop),
                    interval_role=role,
                    heldout_bytes_touched=heldout_touched,
                )
                rows, decode = self._decode_action(
                    payload,
                    raw_start=start,
                    raw_stop=stop,
                    action=action,
                    action_index=chronological_index,
                )
            except BaseException as exc:
                failed = dict(self.last_read_attempt_audit or {})
                self.last_read_attempt_audit = MappingProxyType({
                    "status": "FROZEN_EVALUATION_RANGE_OR_DECODE_FAILED",
                    "reader_session_id": self.reader_session_id,
                    "action": action,
                    "chronological_index": int(chronological_index),
                    "completed_range_audits": dict(range_audits),
                    "completed_decode_audits": dict(decode_audits),
                    "failed_interval_role": role,
                    "failed_attempt": failed,
                    "exception_type": type(exc).__name__,
                    "exception_message": str(exc),
                })
                raise
            range_audits[role] = access
            decode_audits[role] = {
                **dict(decode),
                "decode_role": role,
                "calibration_fit_or_parameter_update_allowed": False,
            }
            rows_by_role[role] = rows

        training_rows = rows_by_role["PREFIT_ORIENTATION_RECONSTRUCTION_ONLY"]
        heldout_rows = rows_by_role["HELDOUT_FROZEN_SCIENTIFIC_EVALUATION"]
        combined_rows: dict[str, np.ndarray] = {}
        heldout_indices: dict[str, np.ndarray] = {}
        for node in self.nodes:
            left = np.asarray(training_rows[node], dtype=IMU_DTYPE)
            right = np.asarray(heldout_rows[node], dtype=IMU_DTYPE)
            combined_rows[node] = np.concatenate((left, right))
            heldout_indices[node] = np.arange(
                len(left), len(left) + len(right), dtype=np.int64,
            )
            combined_rows[node].setflags(write=False)
            heldout_indices[node].setflags(write=False)
        combined_access = {
            "schema": "biospur-c2-frozen-evaluation-dual-range-access-v1",
            "reader_session_id": self.reader_session_id,
            "action": action,
            "chronological_index": int(chronological_index),
            "ranges": range_audits,
            "prefit_bytes_role": "CONTINUOUS_ORIENTATION_STATE_RECONSTRUCTION_ONLY",
            "heldout_bytes_role": "SCIENTIFIC_PREDICTION_AND_EVALUATION_ONLY",
            "whole_file_stat_hash_or_traversal_performed": False,
            "calibration_fit_or_parameter_update_allowed": False,
        }
        combined_decode = {
            "schema": "biospur-c2-frozen-evaluation-dual-range-decode-v1",
            "reader_session_id": self.reader_session_id,
            "action": action,
            "chronological_index": int(chronological_index),
            "range_decodes": decode_audits,
            "combined_rows_by_node": {
                node: int(len(combined_rows[node])) for node in self.nodes
            },
            "heldout_source_rows_by_node": {
                node: int(len(heldout_indices[node])) for node in self.nodes
            },
            "training_rows_allowed_in_scientific_metric": False,
        }
        self.last_read_attempt_audit = MappingProxyType({
            "status": "FROZEN_EVALUATION_TRAINING_AND_HELDOUT_DECODE_COMPLETE",
            "access_audit": combined_access,
            "decode_audit": combined_decode,
        })
        combined = DecodedAction(
            action=action,
            chronological_index=chronological_index,
            interval=(prefit_start, held_stop),
            rows_by_node=MappingProxyType(combined_rows),
            access_audit=MappingProxyType(combined_access),
            decode_audit=MappingProxyType(combined_decode),
        )
        return DecodedEvaluationAction(
            combined_action=combined,
            prefit_interval=(prefit_start, prefit_stop),
            heldout_interval=(held_start, held_stop),
            heldout_source_indices_by_node=MappingProxyType(heldout_indices),
            access_audit=MappingProxyType(combined_access),
            decode_audit=MappingProxyType(combined_decode),
        )
