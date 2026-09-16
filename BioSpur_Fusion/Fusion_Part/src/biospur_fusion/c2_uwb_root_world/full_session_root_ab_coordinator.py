"""One indivisible, label-free Capture2 pelvis-root A/B diagnostic owner."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math

import numpy as np

from biospur_fusion.c2_coupled_progressive.continuous_frontend import (
    ContinuousClockOwner,
    ContinuousEvent,
    continuous_clock_owner_digest,
    validate_event_clock,
)
from biospur_fusion.c2_coupled_progressive.continuous_full_session_reader import (
    FullSessionContinuousReader,
    FullSessionEventTicket,
    FullSessionImuEventTicket,
    FullSessionStreamAudit,
    FullSessionUwbEventTicket,
)
from biospur_fusion.c2_uwb_calibration.shared_root import SharedRangeLink, solve_shared_root
from biospur_fusion.ingest.events import EventStatus, RecordType, TypedEvent
from biospur_fusion.root_r3 import (
    CausalDelayedRootFilter,
    ImuSample,
    RootState,
    RootTranslationEdgeMode,
)

from .continuous_root_ab import PELVIS_NODE, raw_range_reference_ns, uwb_row_from_event
from .continuous_tight_root_owner import (
    AuditedC2TightRootRejection,
    C2TightRangeDelayedRootOwner,
    PreparedC2TightRootTransaction,
    PreparedC2TightRootRejection,
    SkippedC2TightRootEvent,
)
from .diagnostic_c2_static_owner import DiagnosticC2StaticOwner
from .full_session_pelvis_orientation import (
    AuditedFullSessionNonPelvisImu,
    FullSessionPelvisOrientationFrame,
    FullSessionPelvisOrientationOwner,
)
from .split_fusion import FixedLagDriftConfig, FixedLagRangeDriftCorrector
from .tight_range import _valid_slots
from .u0 import ClockModel


QUALIFICATION = "DIAGNOSTIC_COMPLETE_SESSION_ROOT_AB_NON_PROMOTABLE"
_NOMINAL_SIGMA_M = 0.12
_MAXIMUM_CONDITION = 1e8
_MAXIMUM_CONSENSUS_SPREAD_M = 0.05
_MAXIMUM_RESIDUAL_RMS_M = 0.50


def _sha(value: object) -> str:
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode()).hexdigest()


@dataclass(frozen=True)
class LabelFreePelvisBootstrap:
    state: RootState
    event_id: str
    anchors_used: tuple[int, ...]
    condition: float
    residual_rms_m: float
    owner_digest: str


class LabelFreePelvisBootstrapOwner:
    """Choose the first eligible post-gauge pelvis UWB without label access."""

    def __init__(self, static: DiagnosticC2StaticOwner,
                 clock_owner: ContinuousClockOwner) -> None:
        if type(static) is not DiagnosticC2StaticOwner:
            raise TypeError("bootstrap requires sealed diagnostic static owner")
        static.validate_integrity()
        self.static = static
        self.clock_owner = clock_owner
        self._resolved = False
        self.owner_digest = _sha({
            "schema": "biospur.c2.label_free_pelvis_bootstrap.v1",
            "static": static.digest,
            "clock": continuous_clock_owner_digest(clock_owner),
            "node": PELVIS_NODE,
            "sigma_m": _NOMINAL_SIGMA_M,
            "maximum_condition": _MAXIMUM_CONDITION,
            "maximum_consensus_spread_m": _MAXIMUM_CONSENSUS_SPREAD_M,
            "maximum_residual_rms_m": _MAXIMUM_RESIDUAL_RMS_M,
            "selection": "FIRST_CAUSALLY_ELIGIBLE_AFTER_GAUGE",
            "labels_used": False,
            "qualification": QUALIFICATION,
        })

    def consider(self, ticket: FullSessionUwbEventTicket, *,
                 gauge_availability_s: float | None) -> tuple[str, LabelFreePelvisBootstrap | None]:
        if type(ticket) is not FullSessionUwbEventTicket:
            raise TypeError("bootstrap requires authenticated UWB ticket")
        if self._resolved:
            raise RuntimeError("label-free bootstrap is already resolved")
        result: list[tuple[str, LabelFreePelvisBootstrap | None]] = []

        def consume(event: ContinuousEvent) -> None:
            validate_event_clock(event, self.clock_owner)
            record = event.payload_owner
            if (type(record) is not TypedEvent or record.record_type is not RecordType.UWB
                    or record.status is not EventStatus.DECODED or record.raw is None):
                raise ValueError("bootstrap UWB source identity is invalid")
            row = uwb_row_from_event(record)
            if (
                event.node_id != record.node_id
                or event.boot_epoch != record.boot_epoch
                or (record.global_time_ns is not None
                    and event.common_global_ns != record.global_time_ns)
                or record.node_timer_us != event.uwb_timer2.strobe_timer2_us
                or record.sequence != row.sweep
                or record.raw.sample_index != 0
            ):
                raise ValueError("bootstrap source event identity is inconsistent")
            if row.node != PELVIS_NODE:
                result.append(("PRE_BOOTSTRAP_NON_PELVIS_NOT_APPLIED", None)); return
            direct = self.static.clocks[PELVIS_NODE]
            if row.boot != direct.boot_epoch:
                raise ValueError("bootstrap pelvis boot differs from static clock")
            if (event.uwb_timer2 is None
                    or event.uwb_timer2.strobe_timer2_us != row.strobe_us
                    or event.uwb_timer2.frame_timer2_us != row.frame_us
                    or event.common_global_ns != int(round(
                        direct.a_ns_per_us * row.strobe_us + direct.b_ns))
                    or event.availability_global_ns < int(round(
                        direct.a_ns_per_us * row.frame_us + direct.b_ns))):
                raise ValueError("bootstrap event timing differs from static clock")
            clock = ClockModel(direct.boot_epoch, direct.a_ns_per_us, direct.b_ns, 0.0)
            raw_ns, reference_ns = raw_range_reference_ns(row, clock)
            if (gauge_availability_s is None
                    or reference_ns * 1e-9 < gauge_availability_s
                    or event.availability_global_ns * 1e-9 < gauge_availability_s):
                result.append(("PRE_BOOTSTRAP_BEFORE_GAUGE_NOT_APPLIED", None)); return
            slots = _valid_slots(row)
            if len(slots) < 4:
                result.append(("PRE_BOOTSTRAP_INELIGIBLE_LINKS_NOT_APPLIED", None)); return
            epochs = np.asarray([
                clock.seconds(row.strobe_us + 0.5 * row.t_round_us[slot])
                for slot in slots
            ])
            reference_s = reference_ns * 1e-9
            bias = self.static.anchor_delay_m + self.static.tag_delay_m
            links = tuple(SharedRangeLink(
                row.node, slot, row.ranges_mm[slot] / 1_000.0 - bias[slot],
                np.zeros(3), epochs[index] - reference_s,
                _NOMINAL_SIGMA_M * math.sqrt(100.0 / max(row.quality[slot], 1)),
            ) for index, slot in enumerate(slots))
            anchors = self.static.anchors_m
            lower, upper = anchors.min(0), anchors.max(0)
            inset = np.minimum(0.15 * np.maximum(upper - lower, 1e-6), 0.25)
            starts = tuple(np.array([x, y, z])
                for x in (lower[0] + inset[0], upper[0] - inset[0])
                for y in (lower[1] + inset[1], upper[1] - inset[1])
                for z in (lower[2] + inset[2], upper[2] - inset[2]))
            solved = [solve_shared_root(
                links, anchors_m=anchors, initial_root_m=start,
                maximum_condition=_MAXIMUM_CONDITION,
            ) for start in starts]
            accepted = sorted(
                (item for item in solved if item.success),
                key=lambda item: (item.cost, tuple(item.root_position_m)),
            )
            if not accepted:
                raise RuntimeError("label-free prior-free pelvis bootstrap did not converge")
            best = accepted[0]
            spread = max(float(np.linalg.norm(item.root_position_m - best.root_position_m))
                         for item in accepted)
            rms = float(np.sqrt(np.mean(np.square(best.residuals_m))))
            if spread > _MAXIMUM_CONSENSUS_SPREAD_M or rms > _MAXIMUM_RESIDUAL_RMS_M:
                raise RuntimeError("label-free pelvis bootstrap failed fixed geometry gates")
            ids = np.asarray(best.anchors_used, int)
            delta = best.root_position_m[None, :] - anchors[ids]
            h = delta / np.linalg.norm(delta, axis=1)[:, None]
            covariance = np.zeros((9, 9))
            covariance[:3, :3] = np.linalg.inv(h.T @ h / _NOMINAL_SIGMA_M**2) + np.eye(3) * 1e-6
            covariance[3:6, 3:6] = np.eye(3)
            covariance[6:9, 6:9] = np.eye(3) * 0.25
            state = RootState(reference_s, np.r_[best.root_position_m, np.zeros(6)], covariance)
            result.append(("BOOTSTRAP_ACCEPTED", LabelFreePelvisBootstrap(
                state, event.event_id, best.anchors_used, best.condition, rms,
                self.owner_digest,
            )))

        ticket.deliver(consume)
        if len(result) != 1:
            raise RuntimeError("bootstrap ticket did not deliver exactly once")
        if result[0][1] is not None:
            self._resolved = True
        return result[0]


@dataclass(frozen=True)
class SynchronizedRootABSample:
    measurement_time_s: float
    availability_time_s: float
    a_position_m: tuple[float, float, float]
    b_position_m: tuple[float, float, float]


@dataclass(frozen=True)
class FullSessionUwbAudit:
    event_id: str
    accepted: bool
    reason: str
    position_delta_m: tuple[float, float, float]
    velocity_delta_mps: tuple[float, float, float]


@dataclass(frozen=True)
class FullSessionRootABSummary:
    reader_audit: FullSessionStreamAudit
    bootstrap_event_id: str
    events_consumed: int
    prebootstrap_events: int
    imu_committed: int
    uwb_attempted: int
    uwb_accepted: int
    uwb_rejected: int
    uwb_reasons: tuple[tuple[str, int], ...]
    a_endpoint_m: tuple[float, float, float]
    b_endpoint_m: tuple[float, float, float]
    a_path_m: float
    b_path_m: float
    a_max_radius_m: float
    b_max_radius_m: float
    trajectory_sample_count: int
    trajectory_time_sha256: str
    uwb_position_correction_l1_m: float
    uwb_velocity_correction_l1_mps: float
    trajectory: tuple[SynchronizedRootABSample, ...]
    uwb_audit: tuple[FullSessionUwbAudit, ...]
    imu_source_chain_sha256: str
    nonpelvis_imu_audit: tuple[AuditedFullSessionNonPelvisImu, ...]
    qualification: str = QUALIFICATION
    product_ready: bool = False
    scientific_pass: bool = False


class FullSessionRootABCoordinator:
    """Stream-owned A/B coordinator; action labels are never inspected."""

    def __init__(self, *, static: DiagnosticC2StaticOwner,
                 clock_owner: ContinuousClockOwner) -> None:
        static.validate_integrity()
        self.static = static
        self.orientation = FullSessionPelvisOrientationOwner(clock_owner)
        self.bootstrap = LabelFreePelvisBootstrapOwner(static, clock_owner)
        self.a: CausalDelayedRootFilter | None = None
        self.b: CausalDelayedRootFilter | None = None
        self.tight: C2TightRangeDelayedRootOwner | None = None
        self._gauge_availability_s: float | None = None
        self._bootstrap_event_id: str | None = None
        self._prebootstrap = 0
        self._events_consumed = 0
        self._imu = 0
        self._uwb_attempted = 0
        self._uwb_accepted = 0
        self._reasons: dict[str, int] = {}
        self._imu_chain = hashlib.sha256()
        self._last_a: np.ndarray | None = None
        self._last_b: np.ndarray | None = None
        self._a_path = self._b_path = 0.0
        self._a_radius = self._b_radius = 0.0
        self._trajectory_times = hashlib.sha256()
        self._trajectory_count = 0
        self._trajectory: list[SynchronizedRootABSample] = []
        self._uwb_audit: list[FullSessionUwbAudit] = []
        self._nonpelvis_imu_audit: list[AuditedFullSessionNonPelvisImu] = []
        self._uwb_position_l1 = 0.0
        self._uwb_velocity_l1 = 0.0

    def _note_reason(self, reason: str) -> None:
        self._reasons[reason] = self._reasons.get(reason, 0) + 1

    def _commit_imu(self, frame: FullSessionPelvisOrientationFrame) -> None:
        assert self.a is not None and self.b is not None
        self.orientation.validate_owned_frame(frame)
        sample = ImuSample(
            frame.measurement_time_s, frame.availability_time_s,
            frame.specific_force_sensor_mps2, frame.rotation_world_from_sensor,
            frame.source_sequence,
        )
        pa = self.a.prepare_imu_transaction(
            sample, edge_mode=RootTranslationEdgeMode.INERTIAL,
            following_input_mode=RootTranslationEdgeMode.INERTIAL,
        )
        pb = self.b.prepare_imu_transaction(
            sample, edge_mode=RootTranslationEdgeMode.INERTIAL,
            following_input_mode=RootTranslationEdgeMode.INERTIAL,
        )
        self.a.prevalidate_prepared_imu(pa); self.b.prevalidate_prepared_imu(pb)
        a_done = b_done = False
        try:
            if not self.a.commit_prepared_imu(pa):
                raise RuntimeError("A rejected authenticated pelvis IMU")
            a_done = True
            if not self.b.commit_prepared_imu(pb):
                raise RuntimeError("B rejected authenticated pelvis IMU")
            b_done = True
        except BaseException:
            try: self.b.rollback_committed_prepared_imu(pb)
            except RuntimeError:
                if b_done: raise
            try: self.a.rollback_committed_prepared_imu(pa)
            except RuntimeError:
                if a_done: raise
            raise
        self._imu += 1
        self._imu_chain.update(bytes.fromhex(frame.digest))
        self._note_paths(frame)

    def _note_paths(self, frame: FullSessionPelvisOrientationFrame) -> None:
        assert self.a is not None and self.b is not None
        if (self.a.current_state.time_s != self.b.current_state.time_s
                or self.a.current_state.time_s != frame.measurement_time_s):
            raise RuntimeError("A/B trajectory publication axes differ")
        pa, pb = self.a.current_state.position_m.copy(), self.b.current_state.position_m.copy()
        if self._last_a is not None:
            self._a_path += float(np.linalg.norm(pa - self._last_a))
            self._b_path += float(np.linalg.norm(pb - self._last_b))
        self._last_a, self._last_b = pa, pb
        self._a_radius = max(self._a_radius, float(np.linalg.norm(pa)))
        self._b_radius = max(self._b_radius, float(np.linalg.norm(pb)))
        self._trajectory_times.update(json.dumps(
            [frame.measurement_time_s, frame.availability_time_s],
            separators=(",", ":"), allow_nan=False,
        ).encode())
        self._trajectory_count += 1
        self._trajectory.append(SynchronizedRootABSample(
            frame.measurement_time_s, frame.availability_time_s,
            tuple(float(x) for x in pa), tuple(float(x) for x in pb),
        ))

    def consume_ticket(self, ticket: FullSessionEventTicket) -> None:
        child = ticket.dispatch()
        self._events_consumed += 1
        if type(child) is FullSessionImuEventTicket:
            frame = self.orientation.ingest_ticket(child)
            if type(frame) is AuditedFullSessionNonPelvisImu:
                self._nonpelvis_imu_audit.append(frame)
                self._note_reason(frame.reason)
                return
            if frame is None:
                self._prebootstrap += self.a is None
                return
            if self._gauge_availability_s is None:
                self._gauge_availability_s = frame.availability_time_s
            if self.a is None:
                self._prebootstrap += 1
            else:
                self._commit_imu(frame)
            return
        if type(child) is not FullSessionUwbEventTicket:
            raise TypeError("full-session dispatch produced unknown child")
        if self.a is None:
            reason, bootstrap = self.bootstrap.consider(
                child, gauge_availability_s=self._gauge_availability_s,
            )
            if bootstrap is None:
                self._prebootstrap += 1; self._note_reason(reason); return
            self._bootstrap_event_id = bootstrap.event_id
            self.a = CausalDelayedRootFilter(bootstrap.state, self.static.root_config)
            self.b = CausalDelayedRootFilter(
                RootState(bootstrap.state.time_s, bootstrap.state.vector.copy(),
                          bootstrap.state.covariance.copy()), self.static.root_config,
            )
            self.tight = C2TightRangeDelayedRootOwner(
                root=self.b, drift=FixedLagRangeDriftCorrector(FixedLagDriftConfig()),
                static=self.static,
            )
            self._note_reason(reason); return
        assert self.tight is not None and self.b is not None and self.a is not None
        self._uwb_attempted += 1
        a_before = self.a.publication_token().digest
        prepared = self.tight.prepare(child, expected_root=self.b.publication_token())
        if type(prepared) is SkippedC2TightRootEvent:
            self._note_reason(prepared.reason)
            self._uwb_audit.append(FullSessionUwbAudit(
                prepared.event_id, False, prepared.reason,
                (0.0, 0.0, 0.0), (0.0, 0.0, 0.0),
            ))
        elif type(prepared) is PreparedC2TightRootTransaction:
            position, _drift = self.tight.commit(prepared)
            self._uwb_accepted += int(position.accepted)
            self._note_reason(position.reason)
            self._uwb_position_l1 += float(np.linalg.norm(
                prepared.measurement_applied_position_delta_m))
            self._uwb_velocity_l1 += float(np.linalg.norm(prepared.velocity_delta_mps))
            self._uwb_audit.append(FullSessionUwbAudit(
                prepared.event_id, bool(position.accepted), position.reason,
                tuple(float(x) for x in prepared.measurement_applied_position_delta_m),
                tuple(float(x) for x in prepared.velocity_delta_mps),
            ))
        elif type(prepared) is PreparedC2TightRootRejection:
            rejected = self.tight.commit(prepared)
            if type(rejected) is not AuditedC2TightRootRejection:
                raise RuntimeError("tight rejection did not return an audit result")
            self._note_reason(rejected.reason)
            self._uwb_audit.append(FullSessionUwbAudit(
                rejected.event_id, False, rejected.reason,
                (0.0, 0.0, 0.0), (0.0, 0.0, 0.0),
            ))
        else:
            raise RuntimeError("tight owner returned an unknown result")
        if self.a.publication_token().digest != a_before:
            raise RuntimeError("A root mutated during B-only UWB")

    def run(self, reader: FullSessionContinuousReader) -> FullSessionRootABSummary:
        if type(reader) is not FullSessionContinuousReader:
            raise TypeError("coordinator requires exact full-session reader")
        audit = reader.consume(self.consume_ticket)
        if audit.route_audit.event_count != self._events_consumed:
            raise RuntimeError("reader/coordinator event conservation mismatch")
        if self.a is None or self.b is None or self._bootstrap_event_id is None:
            raise RuntimeError("full-session stream ended without label-free bootstrap")
        for state in (self.a.current_state, self.b.current_state):
            if not np.isfinite(state.vector).all() or not np.isfinite(state.covariance).all():
                raise RuntimeError("full-session root trajectory is non-finite")
        return FullSessionRootABSummary(
            audit, self._bootstrap_event_id, self._events_consumed,
            self._prebootstrap, self._imu,
            self._uwb_attempted, self._uwb_accepted,
            self._uwb_attempted - self._uwb_accepted,
            tuple(sorted(self._reasons.items())),
            tuple(float(x) for x in self.a.current_state.position_m),
            tuple(float(x) for x in self.b.current_state.position_m),
            self._a_path, self._b_path, self._a_radius, self._b_radius,
            self._trajectory_count, self._trajectory_times.hexdigest(),
            self._uwb_position_l1, self._uwb_velocity_l1,
            tuple(self._trajectory), tuple(self._uwb_audit),
            self._imu_chain.hexdigest(),
            tuple(self._nonpelvis_imu_audit),
        )
