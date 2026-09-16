"""One-shot ten-node articulated A/B routing for the complete C2 session."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass, replace
from enum import Enum
import hashlib
import json

from .continuous_frontend import ContinuousEvent, ImuTimer2Fields
from .continuous_full_session_reader import (
    FullSessionContinuousReader,
    FullSessionEventTicket,
    FullSessionRecordTicket,
    FullSessionImuEventTicket,
    FullSessionUwbEventTicket,
)
from .continuous_group_epoch_owner import (
    ASSEMBLY_HORIZON_NS,
    ContinuousGroupEpochOwner,
    ContinuousAdmissionAudit,
    ContinuousGroupDiagnosticSnapshot,
    PreparedGroupDisposition,
    EPOCH_PERIOD_NS,
    MAX_PENDING_BUCKETS,
    OwnedNative200HistoryFrame,
    canonical_epoch_bucket,
)
from .contracts import NODE_TO_SEGMENT
from .continuous_session_initializer import (
    BootstrapPoseReadinessStatus,
    ContinuousSessionInitializer,
)
from biospur_fusion.c2_timing_contract import MAXIMUM_POSE_AGE_NS
from biospur_fusion.c2_uwb_root_world.full_session_body_pose import (
    FullSessionBodyPoseOwner,
    PELVIS_NODE,
    SESSION_ID,
)


@dataclass(frozen=True)
class DiagnosticRoutingMetrics:
    """Rollback-truthful routing counters excluded from scientific digests."""

    batched_records: int
    batched_frames: int
    scalar_frames: int
    unrouted_frames: int
    scalar_fallbacks: tuple[tuple[str, int], ...]


@dataclass(frozen=True)
class FullSessionTenNodeABAudit:
    events: int
    imu_events: int
    uwb_events: int
    native200_frames: int
    explicit_dropouts: int
    bootstrap_bucket: int | None
    prebootstrap_uwb_events: int
    prebootstrap_not_applied: int
    prebootstrap_duplicates: int
    preworld_uwb_groups: int
    preworld_pose_omissions: int
    deferred_pose_not_ready: int
    deferred_pose_retries: int
    deferred_pose_expired: int
    deferred_pose_finish_rejected: int
    bootstrap_measurement_rejected: int
    bootstrap_measurement_rejection_reasons: tuple[tuple[str, int], ...]
    bootstrap_uwb_availability_ns: int | None
    bootstrap_pose_readiness_availability_ns: int | None
    maximum_pose_age_ns: float
    a_counters: tuple[tuple[str, int], ...]
    b_counters: tuple[tuple[str, int], ...]
    event_chain_sha256: str
    diagnostic_routing_metrics: DiagnosticRoutingMetrics
    a_admission_journal: tuple[ContinuousAdmissionAudit, ...] = ()
    b_admission_journal: tuple[ContinuousAdmissionAudit, ...] = ()
    ab_transaction_journal: tuple["PairedABTransactionAudit", ...] = ()
    ab_transaction_total: int = 0


@dataclass(frozen=True)
class BranchTransactionDisposition:
    reason: str
    admission: ContinuousAdmissionAudit | None


@dataclass(frozen=True)
class PairedABTransactionAudit:
    provenance_digest: str
    a: BranchTransactionDisposition
    b: BranchTransactionDisposition

    def __post_init__(self) -> None:
        if len(self.provenance_digest) != 64:
            raise ValueError("invalid paired A/B provenance digest")


@dataclass(frozen=True)
class FullSessionTenNodeABPublication:
    a: ContinuousGroupDiagnosticSnapshot
    b: ContinuousGroupDiagnosticSnapshot


@dataclass(frozen=True)
class _DeferredBootstrapBucket:
    """A complete source-owned group awaiting a causal strict-past pose."""

    bucket: int
    events: tuple[ContinuousEvent, ...]
    event_digest: str
    uwb_availability_ns: int
    retries: int = 0


class _BootstrapInstallStatus(str, Enum):
    INSTALLED = "INSTALLED"
    RETRYABLE_POSE_NOT_READY = "RETRYABLE_POSE_NOT_READY"
    TERMINALLY_MISSED = "TERMINALLY_MISSED"
    MEASUREMENT_REJECTED = "MEASUREMENT_REJECTED"


@dataclass(frozen=True)
class _Native200RecordTransaction:
    """Coordinator-owned authority for one synchronous post-bootstrap IMU record."""

    owner_key: object
    ordinal: int
    raw_identity: tuple[int, int, int, str]
    a_owner: ContinuousGroupEpochOwner
    b_owner: ContinuousGroupEpochOwner
    a_capability: object
    b_capability: object


def _deferred_event_digest(events: tuple[ContinuousEvent, ...]) -> str:
    rows = []
    for event in events:
        payload = event.payload_owner
        rows.append({
            "event": (
                event.event_id, event.kind, event.common_global_ns,
                event.availability_global_ns, event.node_id, event.boot_epoch,
                event.clock_domain, event.clock_mapping_digest,
                event.clock_owner_sha256, event.clock_source_sha256,
            ),
            "row": asdict(payload) if hasattr(payload, "__dataclass_fields__") else repr(payload),
        })
    encoded = json.dumps(rows, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


class FullSessionTenNodeABCoordinator:
    """Route one authenticated stream without consulting action labels."""

    def __init__(self, *, body: FullSessionBodyPoseOwner,
                 initializer: ContinuousSessionInitializer) -> None:
        if type(body) is not FullSessionBodyPoseOwner:
            raise TypeError("ten-node coordinator requires the full-session body owner")
        if type(initializer) is not ContinuousSessionInitializer:
            raise TypeError("ten-node coordinator requires the full-session initializer")
        self._body = body
        self.__body_batch_authority = object()
        body.bind_record_batch_coordinator(self.__body_batch_authority)
        self._initializer = initializer
        self._a: ContinuousGroupEpochOwner | None = None
        self._b: ContinuousGroupEpochOwner | None = None
        self._pending: dict[int, dict[str, ContinuousEvent]] = {}
        self._deferred: _DeferredBootstrapBucket | None = None
        self._events = self._imus = self._uwbs = self._frames = self._dropouts = 0
        self._bootstrap_bucket: int | None = None
        self._bootstrap_watermark: int | None = None
        self._prebootstrap_uwb_events = 0
        self._prebootstrap_not_applied = 0
        self._prebootstrap_duplicates = 0
        self._preworld_uwb_groups = 0
        self._deferred_pose_not_ready = 0
        self._deferred_pose_retries = 0
        self._deferred_pose_expired = 0
        self._deferred_pose_finish_rejected = 0
        self._bootstrap_measurement_rejected = 0
        self._bootstrap_measurement_rejection_reasons: dict[str, int] = {}
        self._bootstrap_uwb_availability_ns: int | None = None
        self._bootstrap_pose_readiness_availability_ns: int | None = None
        self._last_pelvis_timer: int | None = None
        self._last_pelvis_ns: int | None = None
        self._chain = hashlib.sha256()
        self._ab_transaction_journal: tuple[PairedABTransactionAudit, ...] = ()
        self._ab_transaction_total = 0
        self._record_batch_ab_stage: list[PairedABTransactionAudit] | None = None
        self._batched_records = 0
        self._batched_frames = 0
        self._scalar_frames = 0
        self._unrouted_frames = 0
        self._scalar_fallbacks: dict[str, int] = {}
        self.__record_transaction_key = object()
        self.__group_record_authority = object()
        self.__record_transaction_ordinal = 0
        self.__active_record_transaction: _Native200RecordTransaction | None = None

    @staticmethod
    def _frame_event(frame: OwnedNative200HistoryFrame) -> ContinuousEvent:
        return ContinuousEvent(
            event_id=f"body:{frame.digest}", kind="IMU", action_index=-1,
            action_id=SESSION_ID, common_global_ns=frame.source_global_ns,
            availability_global_ns=round(frame.imu_sample.availability_time_s * 1e9),
            node_id=frame.node, boot_epoch=frame.boot_epoch,
            clock_domain="B306_TIMER2", clock_mapping_digest=frame.clock_mapping_digest,
            clock_owner_sha256=frame.clock_owner_sha256,
            clock_source_sha256=frame.clock_source_sha256,
            host_time_label="", payload_owner=frame,
            imu_timer2=ImuTimer2Fields(frame.timer2_base_us, frame.source_timer_us),
            region_id=SESSION_ID,
        )

    def _gap_event(self, frame: OwnedNative200HistoryFrame) -> ContinuousEvent:
        assert self._last_pelvis_ns is not None
        duration = (frame.source_global_ns - self._last_pelvis_ns) * 1e-9
        return ContinuousEvent(
            event_id=f"pelvis-dropout:{self._last_pelvis_timer}:{frame.source_timer_us}",
            kind="GAP", action_index=-1, action_id=SESSION_ID,
            common_global_ns=frame.source_global_ns,
            availability_global_ns=round(frame.imu_sample.availability_time_s * 1e9),
            node_id=PELVIS_NODE, boot_epoch=frame.boot_epoch,
            clock_domain="B306_TIMER2", clock_mapping_digest=frame.clock_mapping_digest,
            clock_owner_sha256=frame.clock_owner_sha256,
            clock_source_sha256=frame.clock_source_sha256,
            host_time_label="", payload_owner=None, region_id=SESSION_ID,
            gap_start_global_ns=self._last_pelvis_ns,
            gap_covariance_growth=duration,
        )

    def _validate_record_transaction(
        self, transaction: object, *,
        raw_identity: tuple[int, int, int, str] | None = None,
    ) -> _Native200RecordTransaction:
        if (type(transaction) is not _Native200RecordTransaction
                or transaction.owner_key is not self.__record_transaction_key
                or transaction is not self.__active_record_transaction
                or transaction.a_owner is not self._a
                or transaction.b_owner is not self._b
                or raw_identity is None
                or transaction.raw_identity != raw_identity):
            raise RuntimeError("stale or foreign native200 record transaction")
        return transaction

    def _begin_record_transaction(
        self, raw_identity: tuple[int, int, int, str],
    ) -> _Native200RecordTransaction:
        if self._a is None or self._b is None:
            raise RuntimeError("native200 record fast path requires initialized A/B owners")
        if self.__active_record_transaction is not None:
            raise RuntimeError("nested native200 record transaction")
        a_capability = self._a._issue_native200_record_capability(
            authority=self.__group_record_authority,
            raw_identity=raw_identity,
        )
        try:
            b_capability = self._b._issue_native200_record_capability(
                authority=self.__group_record_authority,
                raw_identity=raw_identity,
            )
        except BaseException:
            self._a._rollback_native200_record_capability(
                a_capability, authority=self.__group_record_authority,
            )
            raise
        self.__record_transaction_ordinal += 1
        transaction = _Native200RecordTransaction(
            self.__record_transaction_key, self.__record_transaction_ordinal,
            raw_identity, self._a, self._b, a_capability, b_capability,
        )
        self.__active_record_transaction = transaction
        return transaction

    def _rollback_record_transaction(
        self, transaction: _Native200RecordTransaction,
    ) -> tuple[BaseException, ...]:
        errors: list[BaseException] = []
        if self.__active_record_transaction is not transaction:
            return (RuntimeError("stale or foreign native200 record transaction"),)
        try:
            transaction.b_owner._rollback_native200_record_capability(
                transaction.b_capability, authority=self.__group_record_authority,
            )
        except BaseException as error:
            errors.append(error)
        try:
            transaction.a_owner._rollback_native200_record_capability(
                transaction.a_capability, authority=self.__group_record_authority,
            )
        except BaseException as error:
            errors.append(error)
        self.__active_record_transaction = None
        return tuple(errors)

    def _revoke_record_transaction(
        self, transaction: _Native200RecordTransaction,
    ) -> BaseException | None:
        """Compatibility shim for focused scalar-control fixtures."""
        errors = self._rollback_record_transaction(transaction)
        return None if not errors else errors[0]

    def _close_record_transaction(
        self, transaction: object, *, raw_identity: tuple[int, int, int, str],
        _retain_rollback: bool = False,
    ) -> None:
        active = self._validate_record_transaction(
            transaction, raw_identity=raw_identity,
        )
        active.b_owner._close_native200_record_capability(
            active.b_capability, authority=self.__group_record_authority,
        )
        active.a_owner._close_native200_record_capability(
            active.a_capability, authority=self.__group_record_authority,
        )
        if not _retain_rollback:
            self._finalize_record_transaction(
                transaction, raw_identity=raw_identity,
            )

    def _finalize_record_transaction(
        self, transaction: object, *, raw_identity: tuple[int, int, int, str],
    ) -> None:
        active = self._validate_record_transaction_finalization(
            transaction, raw_identity=raw_identity,
        )
        self._discard_record_transaction(active)

    def _validate_record_transaction_finalization(
        self, transaction: object, *, raw_identity: tuple[int, int, int, str],
    ) -> _Native200RecordTransaction:
        active = self._validate_record_transaction(
            transaction, raw_identity=raw_identity,
        )
        active.b_owner._validate_finalize_native200_record_capability(
            active.b_capability, authority=self.__group_record_authority,
            raw_identity=raw_identity,
        )
        active.a_owner._validate_finalize_native200_record_capability(
            active.a_capability, authority=self.__group_record_authority,
            raw_identity=raw_identity,
        )
        return active

    def _discard_record_transaction(
        self, transaction: _Native200RecordTransaction,
    ) -> None:
        transaction.b_owner._discard_native200_record_capability(
            transaction.b_capability,
        )
        transaction.a_owner._discard_native200_record_capability(
            transaction.a_capability,
        )
        self.__active_record_transaction = None

    def _atomic_ab(
        self, event: ContinuousEvent, *, uwb: bool,
        _record_transaction: object | None = None,
        _record_raw_identity: tuple[int, int, int, str] | None = None,
    ) -> None:
        assert self._a is not None and self._b is not None
        transaction = None
        if _record_transaction is not None:
            if uwb or event.kind not in ("IMU", "GAP"):
                raise RuntimeError("native200 record transaction requires IMU chronology")
            transaction = self._validate_record_transaction(
                _record_transaction, raw_identity=_record_raw_identity,
            )
        if transaction is not None:
            pa = self._a.prepare_continuous_event(
                event, commit_uwb=False,
                _record_capability=transaction.a_capability,
            )
            pb = self._b.prepare_continuous_event(
                event, commit_uwb=uwb,
                _record_capability=transaction.b_capability,
            )
            da = self._a.prepared_group_disposition(pa)
            db = self._b.prepared_group_disposition(pb)
            if da.provenance_digest != db.provenance_digest:
                raise RuntimeError("A/B raw-group provenance diverged")
            self._a.commit_continuous_event(pa)
            self._b.commit_continuous_event(pb)
            self._publish_ab_transaction(da, db)
            return

        before_a, before_b = (
            self._a.continuous_snapshot(), self._b.continuous_snapshot(),
        )
        before_journal = self._ab_transaction_journal
        before_total = self._ab_transaction_total
        before_stage = (
            None if self._record_batch_ab_stage is None
            else tuple(self._record_batch_ab_stage)
        )
        try:
            pa = self._a.prepare_continuous_event(
                event, commit_uwb=False,
            )
            pb = self._b.prepare_continuous_event(
                event, commit_uwb=uwb,
            )
            da = self._a.prepared_group_disposition(pa)
            db = self._b.prepared_group_disposition(pb)
            if da.provenance_digest != db.provenance_digest:
                raise RuntimeError("A/B raw-group provenance diverged")
            self._a.commit_continuous_event(pa)
            self._b.commit_continuous_event(pb)
            self._publish_ab_transaction(da, db)
        except BaseException:
            self._b.restore_continuous_snapshot(before_b)
            self._a.restore_continuous_snapshot(before_a)
            self._ab_transaction_journal = before_journal
            self._ab_transaction_total = before_total
            if before_stage is not None and self._record_batch_ab_stage is not None:
                self._record_batch_ab_stage[:] = before_stage
            raise

    def _atomic_ab_native200_batch(
        self, events: tuple[ContinuousEvent, ...], *,
        record_transaction: object,
        raw_identity: tuple[int, int, int, str],
    ) -> None:
        """Commit one boundary-free immutable IMU run to A then B."""

        transaction = self._validate_record_transaction(
            record_transaction, raw_identity=raw_identity,
        )
        prepared_a = transaction.a_owner.prepare_native200_batch(
            events, _record_capability=transaction.a_capability,
        )
        prepared_b = transaction.b_owner.prepare_native200_batch(
            events, _record_capability=transaction.b_capability,
        )
        transaction.a_owner.commit_native200_batch(prepared_a)
        transaction.b_owner.commit_native200_batch(prepared_b)

    def _atomic_ab_gap_native200(
        self, gap: ContinuousEvent, endpoint: ContinuousEvent, *,
        record_transaction: object | None = None,
        raw_identity: tuple[int, int, int, str] | None = None,
    ) -> None:
        """Commit one source gap and its first real IMU endpoint atomically."""

        assert self._a is not None and self._b is not None
        transaction = None
        if record_transaction is not None:
            transaction = self._validate_record_transaction(
                record_transaction, raw_identity=raw_identity,
            )
        before_a = before_b = None
        if transaction is None:
            before_a = self._a.continuous_snapshot()
            before_b = self._b.continuous_snapshot()
        try:
            pa = self._a.prepare_continuous_event(
                gap, commit_uwb=False,
                _record_capability=(
                    None if transaction is None else transaction.a_capability
                ),
                _gap_endpoint_event=endpoint,
            )
            pb = self._b.prepare_continuous_event(
                gap, commit_uwb=False,
                _record_capability=(
                    None if transaction is None else transaction.b_capability
                ),
                _gap_endpoint_event=endpoint,
            )
            da = self._a.prepared_group_disposition(pa)
            db = self._b.prepared_group_disposition(pb)
            if da.provenance_digest != db.provenance_digest:
                raise RuntimeError("A/B raw-group provenance diverged")
            self._a.commit_continuous_event(pa)
            self._b.commit_continuous_event(pb)
            self._publish_ab_transaction(da, db)
        except BaseException:
            if transaction is None:
                self._b.restore_continuous_snapshot(before_b)
                self._a.restore_continuous_snapshot(before_a)
            raise

    @staticmethod
    def _frame_raw_identity(
        frame: OwnedNative200HistoryFrame,
    ) -> tuple[int, int, int, str]:
        raw = frame.raw_provenance
        return (
            raw.record_index, raw.start_offset, raw.end_offset,
            raw.encoded_sha256,
        )

    def _accept_record_frames(
        self, frames: tuple[OwnedNative200HistoryFrame, ...], *,
        record_transaction: object,
        raw_identity: tuple[int, int, int, str],
    ) -> None:
        """Split one raw record at every continuity or UWB boundary."""

        run: list[OwnedNative200HistoryFrame] = []
        record_used_batch = False

        def record_scalar(count: int, reason: str) -> None:
            self._scalar_frames += count
            self._scalar_fallbacks[reason] = (
                self._scalar_fallbacks.get(reason, 0) + count
            )

        def flush() -> None:
            nonlocal record_used_batch
            if not run:
                return
            offset = 0
            while offset < len(run):
                suffix = run[offset:]
                events = tuple(self._frame_event(frame) for frame in suffix)
                class_a = self._a.native200_record_batch_classification(events)
                class_b = self._b.native200_record_batch_classification(events)
                if class_a == class_b == "batch_safe":
                    self._atomic_ab_native200_batch(
                        events, record_transaction=record_transaction,
                        raw_identity=raw_identity,
                    )
                    for frame in suffix:
                        self._last_pelvis_timer = frame.source_timer_us
                        self._last_pelvis_ns = frame.source_global_ns
                        self._frames += 1
                    self._batched_frames += len(suffix)
                    record_used_batch = True
                    break
                if (
                    "complete_pending" in (class_a, class_b)
                    and class_a in {"batch_safe", "complete_pending"}
                    and class_b in {"batch_safe", "complete_pending"}
                ):
                    # A has no drift channel while B may own a queued
                    # consensus correction.  Route one authenticated frame
                    # through both scalar transactions, then reclassify the
                    # untouched suffix so batching resumes immediately after
                    # B consumes the eligible observation.
                    self._accept_frame(
                        suffix[0], _record_transaction=record_transaction,
                        _record_raw_identity=raw_identity,
                        _routing_accounted=True,
                    )
                    record_scalar(1, "complete_pending")
                    offset += 1
                    continue
                reason = (
                    "A_B_disagreement" if class_a != class_b else class_a
                )
                if reason not in {
                    "complete_pending", "deadline_or_after",
                    "malformed_or_discontinuity", "A_B_disagreement",
                }:
                    reason = "other_fail_closed"
                for frame in suffix:
                    self._accept_frame(
                        frame, _record_transaction=record_transaction,
                        _record_raw_identity=raw_identity,
                        _routing_accounted=True,
                    )
                record_scalar(len(suffix), reason)
                break
            run.clear()

        for frame in frames:
            if type(frame) is not OwnedNative200HistoryFrame:
                raise TypeError("body owner emitted a foreign history frame")
            if self._frame_raw_identity(frame) != raw_identity:
                raise RuntimeError("body frame crossed its authenticated raw record")
            previous_timer = (
                run[-1].source_timer_us if run else self._last_pelvis_timer
            )
            if (
                previous_timer is not None
                and frame.source_timer_us - previous_timer != 5_000
            ):
                flush()
                if frame.source_timer_us - previous_timer > 5_000:
                    self._atomic_ab_gap_native200(
                        self._gap_event(frame), self._frame_event(frame),
                        record_transaction=record_transaction,
                        raw_identity=raw_identity,
                    )
                    self._dropouts += 1
                    self._last_pelvis_timer = frame.source_timer_us
                    self._last_pelvis_ns = frame.source_global_ns
                    self._frames += 1
                else:
                    self._accept_frame(
                        frame, _record_transaction=record_transaction,
                        _record_raw_identity=raw_identity,
                        _routing_accounted=True,
                    )
                record_scalar(1, "malformed_or_discontinuity")
                continue
            run.append(frame)
        flush()
        if record_used_batch:
            self._batched_records += 1

    @staticmethod
    def _terminal_disposition(
        owner: ContinuousGroupEpochOwner, prepared: PreparedGroupDisposition,
    ) -> BranchTransactionDisposition:
        if prepared.admission is None:
            return BranchTransactionDisposition(prepared.reason, None)
        journal = owner.admission_journal
        if not journal:
            raise RuntimeError("admission transaction lacks terminal audit")
        terminal = journal[-1]
        if (terminal.bucket != prepared.admission.bucket
                or terminal.packet_digest != prepared.admission.packet_digest
                or terminal.source_sequence != prepared.admission.source_sequence):
            raise RuntimeError("admission terminal audit identity diverged")
        return BranchTransactionDisposition(prepared.reason, terminal)

    def _publish_ab_transaction(
        self, a: PreparedGroupDisposition, b: PreparedGroupDisposition,
    ) -> None:
        provenance = a.provenance_digest
        if provenance is None:
            if b.provenance_digest is not None:
                raise RuntimeError("A/B raw-group provenance diverged")
            return
        published = PairedABTransactionAudit(
            provenance,
            self._terminal_disposition(self._a, a),
            self._terminal_disposition(self._b, b),
        )
        if self._record_batch_ab_stage is not None:
            self._record_batch_ab_stage.append(published)
        else:
            self._publish_ab_transactions((published,))

    def _publish_ab_transactions(
        self, rows: tuple[PairedABTransactionAudit, ...],
    ) -> None:
        self._ab_transaction_total += len(rows)
        self._ab_transaction_journal = (
            self._ab_transaction_journal + rows
        )[-64:]

    def _accept_frame(
        self, frame: OwnedNative200HistoryFrame,
        _record_transaction: object | None = None,
        _record_raw_identity: tuple[int, int, int, str] | None = None,
        _routing_accounted: bool = False,
    ) -> None:
        if self._a is None:
            self._initializer.accept_native200(frame)
            self._retry_deferred_after_frame(frame)
        else:
            if (self._last_pelvis_timer is not None
                    and frame.source_timer_us - self._last_pelvis_timer > 5_000):
                self._atomic_ab_gap_native200(
                    self._gap_event(frame), self._frame_event(frame),
                    record_transaction=_record_transaction,
                    raw_identity=_record_raw_identity,
                )
                self._dropouts += 1
                self._last_pelvis_timer = frame.source_timer_us
                self._last_pelvis_ns = frame.source_global_ns
                self._frames += 1
                if not _routing_accounted:
                    self._unrouted_frames += 1
                return
            if _record_transaction is None:
                self._atomic_ab(self._frame_event(frame), uwb=False)
            else:
                self._atomic_ab(
                    self._frame_event(frame), uwb=False,
                    _record_transaction=_record_transaction,
                    _record_raw_identity=_record_raw_identity,
                )
        self._last_pelvis_timer = frame.source_timer_us
        self._last_pelvis_ns = frame.source_global_ns
        self._frames += 1
        if not _routing_accounted:
            self._unrouted_frames += 1

    def _seal_deferred_rejection(
        self, *, expired: bool, measurement_rejected: bool = False,
    ) -> None:
        assert self._deferred is not None
        deferred = self._deferred
        self._prebootstrap_not_applied += len(deferred.events)
        self._bootstrap_watermark = (
            deferred.bucket if self._bootstrap_watermark is None
            else max(self._bootstrap_watermark, deferred.bucket)
        )
        if measurement_rejected:
            pass
        elif expired:
            self._deferred_pose_expired += 1
        else:
            self._deferred_pose_finish_rejected += 1
        self._deferred = None

    def _install_bootstrap(
        self,
        deferred: _DeferredBootstrapBucket,
        *, readiness_availability_ns: int,
    ) -> _BootstrapInstallStatus:
        if _deferred_event_digest(deferred.events) != deferred.event_digest:
            raise RuntimeError("deferred bootstrap event identity changed")
        readiness = self._initializer.bootstrap_pose_readiness(deferred.events)
        if readiness.status is BootstrapPoseReadinessStatus.RETRYABLE_POSE_NOT_READY:
            return _BootstrapInstallStatus.RETRYABLE_POSE_NOT_READY
        if readiness.status is BootstrapPoseReadinessStatus.TERMINALLY_MISSED:
            return _BootstrapInstallStatus.TERMINALLY_MISSED
        if readiness.status is not BootstrapPoseReadinessStatus.READY:
            raise RuntimeError("unknown bootstrap pose readiness status")
        outcome = self._initializer.prepare_first_group_outcome(deferred.events)
        if not outcome.accepted:
            self._bootstrap_measurement_rejected += 1
            self._bootstrap_measurement_rejection_reasons[outcome.reason] = (
                self._bootstrap_measurement_rejection_reasons.get(outcome.reason, 0) + 1
            )
            return _BootstrapInstallStatus.MEASUREMENT_REJECTED
        prepared = outcome.prepared
        assert prepared is not None
        installation = prepared.installation
        installation.a.bind_native200_record_coordinator(
            self.__group_record_authority,
        )
        installation.b.bind_native200_record_coordinator(
            self.__group_record_authority,
        )
        installed = self._initializer.commit_first_group(prepared)
        if (installed is not installation
                or installed.a is not installation.a
                or installed.b is not installation.b):
            raise RuntimeError("bootstrap installation owner identity changed")
        self._a, self._b = installed.a, installed.b
        self._bootstrap_bucket = deferred.bucket
        self._bootstrap_watermark = deferred.bucket
        self._bootstrap_uwb_availability_ns = deferred.uwb_availability_ns
        self._bootstrap_pose_readiness_availability_ns = readiness_availability_ns
        self._deferred = None
        for key, pending_rows in self._pending.items():
            if key != deferred.bucket:
                self._prebootstrap_not_applied += len(pending_rows)
        self._pending.clear()
        return _BootstrapInstallStatus.INSTALLED

    def _oldest_complete_pending(self) -> _DeferredBootstrapBucket | None:
        complete = sorted(
            key for key, rows in self._pending.items()
            if set(rows) == set(NODE_TO_SEGMENT)
        )
        if not complete:
            return None
        bucket = complete[0]
        rows = self._pending.pop(bucket)
        events = tuple(deepcopy(rows[node]) for node in sorted(rows))
        return _DeferredBootstrapBucket(
            bucket, events, _deferred_event_digest(events),
            max(event.availability_global_ns for event in events),
        )

    def _retry_deferred_after_frame(self, frame: OwnedNative200HistoryFrame) -> None:
        while self._a is None:
            if self._deferred is None:
                self._deferred = self._oldest_complete_pending()
                if self._deferred is None:
                    return
            deferred = self._deferred
            self._deferred_pose_retries += 1
            self._deferred = replace(deferred, retries=deferred.retries + 1)
            readiness_ns = round(frame.imu_sample.availability_time_s * 1e9)
            status = self._install_bootstrap(
                self._deferred, readiness_availability_ns=readiness_ns,
            )
            if status is _BootstrapInstallStatus.INSTALLED:
                return
            if status is _BootstrapInstallStatus.RETRYABLE_POSE_NOT_READY:
                return
            if status is _BootstrapInstallStatus.MEASUREMENT_REJECTED:
                self._seal_deferred_rejection(
                    expired=False, measurement_rejected=True,
                )
                continue
            if status is not _BootstrapInstallStatus.TERMINALLY_MISSED:
                raise RuntimeError("unknown bootstrap installation status")
            self._seal_deferred_rejection(expired=True)

    def _bootstrap_or_route_uwb(self, event: ContinuousEvent) -> None:
        if self._a is not None:
            self._atomic_ab(event, uwb=True)
            return
        self._prebootstrap_uwb_events += 1
        bucket = canonical_epoch_bucket(event.common_global_ns)
        if self._deferred is not None and bucket <= self._deferred.bucket:
            self._prebootstrap_duplicates += 1
            self._prebootstrap_not_applied += 1
            return
        expired = sorted(
            key for key in self._pending
            if event.availability_global_ns
            >= (key + 1) * EPOCH_PERIOD_NS + ASSEMBLY_HORIZON_NS
        )
        for key in expired:
            self._prebootstrap_not_applied += len(self._pending.pop(key))
            self._bootstrap_watermark = (
                key if self._bootstrap_watermark is None
                else max(self._bootstrap_watermark, key)
            )
        if self._bootstrap_watermark is not None and bucket <= self._bootstrap_watermark:
            self._prebootstrap_not_applied += 1
            return
        rows = self._pending.setdefault(bucket, {})
        if event.node_id in rows:
            self._prebootstrap_duplicates += 1
            self._prebootstrap_not_applied += 1
            return
        rows[event.node_id] = event
        pending_limit = MAX_PENDING_BUCKETS - int(self._deferred is not None)
        while len(self._pending) > pending_limit:
            oldest = min(self._pending)
            self._prebootstrap_not_applied += len(self._pending.pop(oldest))
            self._bootstrap_watermark = (
                oldest if self._bootstrap_watermark is None
                else max(self._bootstrap_watermark, oldest)
            )
        if set(rows) != set(NODE_TO_SEGMENT):
            return
        if self._deferred is not None:
            return
        self._deferred = self._oldest_complete_pending()
        assert self._deferred is not None
        status = self._install_bootstrap(
            self._deferred,
            readiness_availability_ns=self._deferred.uwb_availability_ns,
        )
        if status is _BootstrapInstallStatus.RETRYABLE_POSE_NOT_READY:
            self._deferred_pose_not_ready += 1
            self._preworld_uwb_groups += 1
        elif status in (
            _BootstrapInstallStatus.TERMINALLY_MISSED,
            _BootstrapInstallStatus.MEASUREMENT_REJECTED,
        ):
            self._seal_deferred_rejection(
                expired=status is _BootstrapInstallStatus.TERMINALLY_MISSED,
                measurement_rejected=(
                    status is _BootstrapInstallStatus.MEASUREMENT_REJECTED
                ),
            )

    def consume_ticket(self, ticket: FullSessionEventTicket) -> None:
        if type(ticket) is not FullSessionEventTicket:
            raise TypeError("ten-node coordinator requires a generic full-session ticket")
        child = ticket.dispatch()
        self._events += 1
        self._chain.update(bytes.fromhex(child.sensor_identity_digest))
        if type(child) is FullSessionImuEventTicket:
            produced = self._body.ingest_ticket(child)
            frames = () if produced is None else (
                produced if isinstance(produced, tuple) else (produced,)
            )
            for frame in frames:
                if type(frame) is not OwnedNative200HistoryFrame:
                    raise TypeError("body owner emitted a foreign history frame")
                self._accept_frame(frame)
            self._imus += 1
            return
        if type(child) is FullSessionUwbEventTicket:
            delivered: list[ContinuousEvent] = []
            child.deliver(delivered.append)
            if len(delivered) != 1:
                raise RuntimeError("UWB ticket did not deliver exactly once")
            self._bootstrap_or_route_uwb(delivered[0])
            self._uwbs += 1
            return
        raise TypeError("unknown full-session typed ticket")

    @staticmethod
    def _validate_record_batch(
        ticket: FullSessionRecordTicket, events: tuple[ContinuousEvent, ...],
    ) -> str:
        if (type(events) is not tuple or not 1 <= len(events) <= 16
                or any(type(event) is not ContinuousEvent for event in events)
                or len({event.kind for event in events}) != 1
                or len({event.node_id for event in events}) != 1
                or len(ticket.event_digests) != len(events)
                or len(ticket.sensor_identity_digests) != len(events)):
            raise ValueError("ten-node coordinator requires one homogeneous raw record")
        raw = [event.payload_owner.raw for event in events]
        if (any(value is None for value in raw)
                or len({(value.record_index, value.start_offset, value.end_offset,
                         value.encoded_sha256) for value in raw}) != 1
                or (raw[0].record_index, raw[0].start_offset, raw[0].end_offset,
                    raw[0].encoded_sha256) != ticket.raw_identity):
            raise ValueError("ten-node coordinator raw record identity is mixed")
        kind = events[0].kind
        if kind == "IMU":
            if tuple(value.sample_index for value in raw) != tuple(range(len(raw))):
                raise ValueError("ten-node coordinator IMU sample order is invalid")
        elif kind == "UWB":
            if len(events) != 1:
                raise ValueError("ten-node coordinator UWB record is not singular")
        else:
            raise ValueError("ten-node coordinator record kind is unsupported")
        return kind

    def consume_record_ticket(self, ticket: FullSessionRecordTicket) -> None:
        if type(ticket) is not FullSessionRecordTicket:
            raise TypeError("ten-node coordinator requires a full-session record ticket")

        def consume(events: tuple[ContinuousEvent, ...]) -> None:
            kind = self._validate_record_batch(ticket, events)
            snapshot = self._record_batch_snapshot() if kind == "IMU" else None
            record_transaction = None
            body_capability = None
            staged: tuple[PairedABTransactionAudit, ...] = ()
            if self._record_batch_ab_stage is not None:
                raise RuntimeError("nested record-batch A/B audit transaction")
            self._record_batch_ab_stage = []
            try:
                if kind == "IMU":
                    if type(self._body) is FullSessionBodyPoseOwner:
                        body_capability = self._body._issue_record_batch_capability(
                            authority=self.__body_batch_authority,
                            node=events[0].node_id,
                            raw_identity=ticket.raw_identity,
                        )
                    if (type(self._a) is ContinuousGroupEpochOwner
                            and type(self._b) is ContinuousGroupEpochOwner):
                        record_transaction = self._begin_record_transaction(
                            ticket.raw_identity,
                        )
                    emitted: list[OwnedNative200HistoryFrame] = []
                    if body_capability is None:
                        self._body.ingest_record_batch(
                            events, authority=self.__body_batch_authority,
                            consumer=emitted.append,
                        )
                    else:
                        self._body.ingest_record_batch(
                            events, authority=self.__body_batch_authority,
                            consumer=emitted.append,
                            _record_capability=body_capability,
                        )
                    if record_transaction is None:
                        for frame in emitted:
                            self._accept_frame(frame)
                    elif not all(
                        type(frame) is OwnedNative200HistoryFrame
                        for frame in emitted
                    ):
                        for frame in emitted:
                            self._accept_frame(
                                frame, _record_transaction=record_transaction,
                                _record_raw_identity=ticket.raw_identity,
                            )
                    else:
                        self._accept_record_frames(
                            tuple(emitted),
                            record_transaction=record_transaction,
                            raw_identity=ticket.raw_identity,
                        )
                    self._imus += len(events)
                else:
                    self._bootstrap_or_route_uwb(events[0])
                    self._uwbs += 1
                for digest in ticket.sensor_identity_digests:
                    self._chain.update(bytes.fromhex(digest))
                self._events += len(events)
                if record_transaction is not None:
                    self._close_record_transaction(
                        record_transaction, raw_identity=ticket.raw_identity,
                        _retain_rollback=True,
                    )
                if body_capability is not None:
                    self._body._close_record_batch_capability(
                        body_capability, authority=self.__body_batch_authority,
                    )
                if record_transaction is not None:
                    self._validate_record_transaction_finalization(
                        record_transaction, raw_identity=ticket.raw_identity,
                    )
                if body_capability is not None:
                    self._body._validate_finalize_record_batch_capability(
                        body_capability, authority=self.__body_batch_authority,
                        raw_identity=ticket.raw_identity,
                    )
                staged = tuple(self._record_batch_ab_stage)
                self._record_batch_ab_stage = None
                self._publish_ab_transactions(staged)
                if record_transaction is not None:
                    self._discard_record_transaction(record_transaction)
                if body_capability is not None:
                    self._body._discard_record_batch_capability(body_capability)
            except BaseException as primary_error:
                if self._record_batch_ab_stage is not None:
                    staged = tuple(self._record_batch_ab_stage)
                self._record_batch_ab_stage = None
                cleanup_errors: list[BaseException] = []
                if record_transaction is not None:
                    cleanup_errors.extend(
                        self._rollback_record_transaction(record_transaction)
                    )
                if body_capability is not None:
                    try:
                        self._body._rollback_record_batch_capability(
                            body_capability, authority=self.__body_batch_authority,
                        )
                    except BaseException as restore_error:
                        cleanup_errors.append(restore_error)
                if snapshot is not None:
                    try:
                        self._restore_record_batch_snapshot(snapshot)
                    except BaseException as restore_error:
                        cleanup_errors.append(restore_error)
                for cleanup_error in cleanup_errors:
                    primary_error.add_note(
                        f"record transaction cleanup also failed: {cleanup_error!r}"
                    )
                if staged:
                    digest = hashlib.sha256(json.dumps({
                        "schema": "C2_OUTER_RECORD_BATCH_ROLLBACK_V1",
                        "raw_identity": ticket.raw_identity,
                        "staged": [row.provenance_digest for row in staged],
                    }, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
                    rolled_back = BranchTransactionDisposition(
                        "OUTER_RECORD_BATCH_ROLLED_BACK", None,
                    )
                    try:
                        self._publish_ab_transactions((PairedABTransactionAudit(
                            digest, rolled_back, rolled_back,
                        ),))
                    except BaseException as audit_error:
                        primary_error.add_note(
                            f"record rollback audit also failed: {audit_error!r}"
                        )
                raise

        ticket.deliver(consume)

    def _record_batch_snapshot(self) -> object:
        initializer = (
            self._initializer._frames, self._initializer._preworld_pose_omissions,
            self._initializer._revision, self._initializer._located,
        )
        return (
            self._a, self._b,
            {bucket: dict(rows) for bucket, rows in self._pending.items()},
            self._deferred, self._events,
            self._imus, self._uwbs, self._frames, self._dropouts,
            self._bootstrap_bucket, self._bootstrap_watermark,
            self._prebootstrap_uwb_events, self._prebootstrap_not_applied,
            self._prebootstrap_duplicates, self._preworld_uwb_groups,
            self._deferred_pose_not_ready, self._deferred_pose_retries,
            self._deferred_pose_expired, self._deferred_pose_finish_rejected,
            self._bootstrap_measurement_rejected,
            dict(self._bootstrap_measurement_rejection_reasons),
            self._bootstrap_uwb_availability_ns,
            self._bootstrap_pose_readiness_availability_ns,
            self._last_pelvis_timer, self._last_pelvis_ns, self._chain.copy(),
            self._batched_records, self._batched_frames, self._scalar_frames,
            self._unrouted_frames,
            dict(self._scalar_fallbacks),
            self._ab_transaction_journal, self._ab_transaction_total,
            initializer,
        )

    def _restore_record_batch_snapshot(self, snapshot: object) -> None:
        (self._a, self._b, self._pending, self._deferred,
         self._events, self._imus, self._uwbs, self._frames, self._dropouts,
         self._bootstrap_bucket, self._bootstrap_watermark,
         self._prebootstrap_uwb_events, self._prebootstrap_not_applied,
         self._prebootstrap_duplicates, self._preworld_uwb_groups,
         self._deferred_pose_not_ready, self._deferred_pose_retries,
         self._deferred_pose_expired, self._deferred_pose_finish_rejected,
         self._bootstrap_measurement_rejected,
         self._bootstrap_measurement_rejection_reasons,
         self._bootstrap_uwb_availability_ns,
         self._bootstrap_pose_readiness_availability_ns,
         self._last_pelvis_timer, self._last_pelvis_ns, self._chain,
         self._batched_records, self._batched_frames, self._scalar_frames,
         self._unrouted_frames,
         self._scalar_fallbacks,
         self._ab_transaction_journal, self._ab_transaction_total,
         initializer) = snapshot
        (self._initializer._frames, self._initializer._preworld_pose_omissions,
         self._initializer._revision, self._initializer._located) = initializer

    def run(self, reader: FullSessionContinuousReader):
        if type(reader) is not FullSessionContinuousReader:
            raise TypeError("ten-node coordinator requires the exact one-shot reader")
        stream = reader.consume_record_batches(self.consume_record_ticket)
        for frame in self._body.finish():
            self._accept_frame(frame)
        if self._a is None or self._b is None:
            if self._deferred is not None:
                self._seal_deferred_rejection(expired=False)
            self._prebootstrap_not_applied += sum(
                len(rows) for rows in self._pending.values()
            )
            self._pending.clear()
            raise RuntimeError("full session ended without a complete ten-node bootstrap")
        if (self._prebootstrap_uwb_events
                != 10 + self._prebootstrap_not_applied):
            raise RuntimeError("prebootstrap UWB disposition does not conserve events")
        if stream.route_audit.event_count != self._events:
            raise RuntimeError("reader/coordinator event conservation mismatch")
        return stream, self.audit()

    def audit(self) -> FullSessionTenNodeABAudit:
        a = () if self._a is None else tuple(sorted(self._a.counters.items()))
        b = () if self._b is None else tuple(sorted(self._b.counters.items()))
        return FullSessionTenNodeABAudit(
            self._events, self._imus, self._uwbs, self._frames, self._dropouts,
            self._bootstrap_bucket, self._prebootstrap_uwb_events,
            self._prebootstrap_not_applied, self._prebootstrap_duplicates,
            self._preworld_uwb_groups,
            self._initializer.preworld_pose_omissions,
            self._deferred_pose_not_ready, self._deferred_pose_retries,
            self._deferred_pose_expired, self._deferred_pose_finish_rejected,
            self._bootstrap_measurement_rejected,
            tuple(sorted(self._bootstrap_measurement_rejection_reasons.items())),
            self._bootstrap_uwb_availability_ns,
            self._bootstrap_pose_readiness_availability_ns,
            MAXIMUM_POSE_AGE_NS,
            a, b, self._chain.hexdigest(),
            DiagnosticRoutingMetrics(
                self._batched_records, self._batched_frames,
                self._scalar_frames, self._unrouted_frames,
                tuple(sorted(self._scalar_fallbacks.items())),
            ),
            (() if self._a is None else self._a.admission_journal),
            (() if self._b is None else self._b.admission_journal),
            self._ab_transaction_journal,
            self._ab_transaction_total,
        )

    def diagnostic_publication(self) -> FullSessionTenNodeABPublication:
        if self._a is None or self._b is None:
            raise RuntimeError("ten-node A/B roots are not initialized")
        return FullSessionTenNodeABPublication(
            self._a.diagnostic_snapshot(), self._b.diagnostic_snapshot(),
        )

    def body_audit(self):
        """Return the body owner's immutable audit without exposing the owner."""
        return self._body.audit()
