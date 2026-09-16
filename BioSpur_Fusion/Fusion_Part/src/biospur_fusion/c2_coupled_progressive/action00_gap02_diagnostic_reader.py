"""One-fd bounded reader for the diagnostic Action00--gap--Action02 prefix.

The three region hashes already exist as sealed evidence.  Keeping three
canonical decoder windows avoids inventing a combined-window digest while the
transport itself remains one contiguous, no-read-ahead file pass.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import itertools
import json
import os
from pathlib import Path
import stat
from types import MappingProxyType
from typing import Mapping

from biospur_fusion.ingest.events import TypedEvent
from biospur_fusion.c2_uwb_root_world.action00_gap02_diagnostic_plan import (
    Action00Gap02DiagnosticPlan,
    DiagnosticEventRouter,
    DiagnosticRouteAudit,
)

from .action00_engineering_reader import (
    Action00EngineeringPolicyReader,
    RAW_RELATIVE,
    SOURCE_SHA256,
    SOURCE_STAT_IDENTITY,
    count_nonempty_cobs_prefix,
)
from .continuous_frontend import ContinuousClockOwner, ContinuousEvent
from .continuous_streaming_runner import (
    AuthorizedByteWindow,
    IncrementalV47WindowDecoder,
)


ROLE = "ACTION00_GAP_ACTION02_DIAGNOSTIC_SOURCE"
GAP_SLICE_SHA256 = "ab25f31ee450a175320c0cfb04f789e966e6d3505e45a9b45a799974b52f627c"
GAP_HASH_EVIDENCE_RELATIVE = Path(
    "logs/c2_continuous_root_ab_final_short_pilot_20260908T023000Z/RESULT.json"
)
GAP_HASH_EVIDENCE_SHA256 = "947307d56bc95779f5847b0c0a26cbf75eb4f11b8e815518968ac4bc00e4605a"


def _raw_key(row: TypedEvent) -> tuple[int, int, int, str]:
    if row.raw is None:
        raise ValueError("bounded diagnostic decoder lost raw record identity")
    return (
        row.raw.record_index, row.raw.start_offset,
        row.raw.end_offset, row.raw.encoded_sha256,
    )


def _completed_nonempty_records(payload: bytes, pending: bool) -> tuple[int, bool]:
    count = 0
    for value in payload:
        if value == 0:
            if pending:
                count += 1
            pending = False
        else:
            pending = True
    return count, pending


@dataclass(frozen=True)
class Action00Gap02DiagnosticDecode:
    events: tuple[ContinuousEvent, ...]
    route_audit: DiagnosticRouteAudit
    access_audit: Mapping[str, object]
    role: str = ROLE

    def __post_init__(self) -> None:
        if self.role != ROLE or not self.events:
            raise ValueError("invalid Action00/gap/Action02 diagnostic decode")
        object.__setattr__(self, "access_audit", MappingProxyType(dict(self.access_audit)))


class Action00Gap02DiagnosticReader:
    """Read exactly one authenticated contiguous prefix, once, from one fd."""

    def __init__(
        self, *, root: Path, plan: Action00Gap02DiagnosticPlan,
        clock_owner: ContinuousClockOwner, chunk_bytes: int = 1 << 20,
        maximum_events: int = 400_000,
    ) -> None:
        if type(plan) is not Action00Gap02DiagnosticPlan:
            raise TypeError("diagnostic reader requires the typed source plan")
        if type(clock_owner) is not ContinuousClockOwner:
            raise TypeError("diagnostic reader requires the typed common clock owner")
        if type(chunk_bytes) is not int or not 1 <= chunk_bytes <= 1 << 20:
            raise ValueError("invalid diagnostic source chunk size")
        if type(maximum_events) is not int or maximum_events <= 0:
            raise ValueError("invalid diagnostic event capacity")
        # Reuse the existing formal-manifest/stat authority.  Construction of
        # this verifier performs no raw payload read.
        verifier = Action00EngineeringPolicyReader(
            root=root, clock_owner=clock_owner, chunk_bytes=chunk_bytes,
            maximum_events=maximum_events,
        )
        self.raw_path = verifier.raw_path
        self.plan = plan
        self.clock_owner = clock_owner
        self.chunk_bytes = chunk_bytes
        self.maximum_events = maximum_events
        self._consumed = False
        self.last_attempt_audit: Mapping[str, object] | None = None
        self._validate_gap_hash_evidence(Path(root).resolve())

    def _validate_gap_hash_evidence(self, root: Path) -> None:
        path = (root / GAP_HASH_EVIDENCE_RELATIVE).resolve()
        if not path.is_relative_to(root):
            raise RuntimeError("gap-hash evidence path escapes workspace")
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        try:
            before = os.fstat(descriptor)
            if not stat.S_ISREG(before.st_mode):
                raise RuntimeError("gap-hash evidence is not a regular file")
            with os.fdopen(descriptor, "rb", closefd=False) as stream:
                payload = stream.read()
            after = os.fstat(descriptor)
        finally:
            os.close(descriptor)
        if (
            self._stat_identity(before) != self._stat_identity(after)
            or len(payload) != before.st_size
            or hashlib.sha256(payload).hexdigest() != GAP_HASH_EVIDENCE_SHA256
        ):
            raise RuntimeError("gap-hash evidence identity mismatch")
        document = json.loads(payload)
        hashes = document.get("window_hashes")
        expected = {
            "00_initial_still": self.plan.regions[0].expected_sha256,
            "UNASSIGNED_INTER_ACTION_GAP": GAP_SLICE_SHA256,
            "02_t_pose": self.plan.regions[2].expected_sha256,
        }
        if hashes != expected or document.get("raw_container_sha256_declared_not_recomputed") != SOURCE_SHA256:
            raise RuntimeError("gap-hash evidence does not bind the exact source regions")

    @staticmethod
    def _stat_identity(value: os.stat_result) -> tuple[int, int, int, int]:
        return int(value.st_dev), int(value.st_ino), int(value.st_size), int(value.st_mtime_ns)

    def _window(self, region_index: int, first_record_index: int) -> AuthorizedByteWindow:
        region = self.plan.regions[region_index]
        expected = region.expected_sha256
        if region.kind == "INTER_ACTION_GAP":
            expected = GAP_SLICE_SHA256
        if expected is None:
            raise RuntimeError("diagnostic source region lacks an authenticated hash")
        boots = {
            f"{binding.node_id}:{kind}": binding.boot_epoch
            for binding in self.clock_owner.bindings for kind in (1, 3)
        }
        return AuthorizedByteWindow(
            str(RAW_RELATIVE), SOURCE_SHA256,
            region.start_offset, region.stop_offset, first_record_index,
            expected, region.start_ns, region.stop_ns, boots, 4096,
        )

    def read(self) -> Action00Gap02DiagnosticDecode:
        if self._consumed:
            raise RuntimeError("Action00/gap/Action02 diagnostic reader is one-shot")
        self._consumed = True
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(self.raw_path, flags)
        prefix_bytes = source_bytes = emitted = 0
        region_counts: dict[str, int] = {}
        region_group_counts: dict[str, int] = {}
        try:
            with os.fdopen(fd, "rb", closefd=True) as source:
                before = self._stat_identity(os.fstat(source.fileno()))
                if before != SOURCE_STAT_IDENTITY:
                    raise RuntimeError("diagnostic source changed before bounded read")
                first_record_index, prefix_bytes = count_nonempty_cobs_prefix(
                    source, stop_offset=self.plan.start_offset,
                    chunk_bytes=self.chunk_bytes,
                )
                router = DiagnosticEventRouter(self.plan, self.clock_owner)
                routed: list[ContinuousEvent] = []
                for region_index, region in enumerate(self.plan.regions):
                    if source.tell() != region.start_offset:
                        raise AssertionError("diagnostic source pass is not contiguous")
                    decoder = IncrementalV47WindowDecoder(
                        self._window(region_index, first_record_index),
                        maximum_emitted_events=self.maximum_events - emitted,
                    )
                    region_records = 0
                    region_groups = 0
                    pending = False
                    cursor = region.start_offset
                    while cursor < region.stop_offset:
                        block = source.read(min(self.chunk_bytes, region.stop_offset - cursor))
                        if not block:
                            raise OSError("diagnostic source ended inside authorized prefix")
                        produced = decoder.feed(block, absolute_offset=cursor)
                        completed, pending = _completed_nonempty_records(block, pending)
                        region_records += completed
                        cursor += len(block)
                        source_bytes += len(block)
                        emitted += len(produced)
                        for _key, group in itertools.groupby(produced, key=_raw_key):
                            routed.extend(router.route_original_record(tuple(group)))
                            region_groups += 1
                    decoder.finish()
                    if pending:
                        raise ValueError("diagnostic region boundary cuts a COBS record")
                    if source.tell() != region.stop_offset:
                        raise AssertionError("diagnostic reader crossed a region hard stop")
                    region_counts[region.region_id] = region_records
                    region_group_counts[region.region_id] = region_groups
                    first_record_index += region_records
                if source.tell() != self.plan.stop_offset:
                    raise AssertionError("diagnostic reader crossed its final hard stop")
                after = self._stat_identity(os.fstat(source.fileno()))
                if after != before:
                    raise RuntimeError("diagnostic source changed during bounded read")
                route_audit = router.finish()
        except BaseException as exc:
            self.last_attempt_audit = MappingProxyType({
                "status": "FAILED_CLOSED", "role": ROLE,
                "prefix_bytes_read": prefix_bytes,
                "source_bytes_read": source_bytes,
                "bytes_after_action02_read": 0,
                "exception_type": type(exc).__name__,
                "exception_message": str(exc),
            })
            raise
        audit = MappingProxyType({
            "status": "COMMITTED_ONCE", "role": ROLE,
            "source_sha256": SOURCE_SHA256,
            "source_stat_identity": list(SOURCE_STAT_IDENTITY),
            "source_byte_interval": [self.plan.start_offset, self.plan.stop_offset],
            "prefix_bytes_read": prefix_bytes,
            "source_bytes_read": source_bytes,
            "bytes_after_action02_read": 0,
            "opened_sources": 1,
            "read_ahead_performed": False,
            "region_nonempty_record_counts": MappingProxyType(dict(region_counts)),
            "region_decoded_record_groups": MappingProxyType(dict(region_group_counts)),
            "region_skipped_non_sensor_records": MappingProxyType({
                key: region_counts[key] - region_group_counts[key]
                for key in region_counts
            }),
            "emitted_typed_events": emitted,
            "routed_events": len(routed),
            "gap_hash_evidence": str(GAP_HASH_EVIDENCE_RELATIVE),
            "gap_hash_evidence_sha256": GAP_HASH_EVIDENCE_SHA256,
        })
        self.last_attempt_audit = audit
        return Action00Gap02DiagnosticDecode(tuple(routed), route_audit, audit)
