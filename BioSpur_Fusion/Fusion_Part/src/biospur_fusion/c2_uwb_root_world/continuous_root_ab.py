"""Root-only continuous Capture2 IMU/UWB A/B diagnostic primitives.

This module deliberately owns no articulated pose, contact, floor, or display
state.  Action labels are metadata; the inertial state is capture-continuous.
"""
from __future__ import annotations

from dataclasses import dataclass
from copy import deepcopy
import math
from typing import Iterable

import numpy as np
import qmt
from scipy.spatial.transform import Rotation

from biospur_fusion.c2_uwb_calibration.shared_root import SharedRangeLink, solve_shared_root
from biospur_fusion.c2_uwb_root_world.tight_range import (
    RawRangeDecision,
    RawRangeUpdateConfig,
    prepare_raw_range_update,
    update_raw_ranges,
)
from biospur_fusion.c2_uwb_root_world.u0 import ClockModel, UwbRow
from biospur_fusion.root_r3.estimator import RootFilterConfig, propagate_inertial
from biospur_fusion.root_r3.models import RootState
from biospur_fusion.ingest.events import RecordType, TypedEvent


PELVIS_NODE = "BSFC2CC"
PELVIS_VQF_PREPARATION_SAMPLES = 100


@dataclass(frozen=True)
class DiagnosticBootstrap:
    state: RootState
    anchors_used: tuple[int, ...]
    condition: float
    residual_rms_m: float
    provenance: str = "FIRST_ELIGIBLE_ACTION00_RAW_RANGE_DIAGNOSTIC_NOT_SCIENTIFIC_R"


@dataclass(frozen=True)
class RootABMetrics:
    imu_samples: int
    missing_imu_intervals: int
    maximum_imu_gap_s: float
    a_uwb_commits: int
    b_uwb_accepted: int
    b_uwb_rejected: int


@dataclass(frozen=True)
class UwbTimestampAdmission:
    stream_progress_ns: int
    dispatch_measurement_ns: int
    raw_reference_ns: float | None
    eligible_for_range_solver: bool
    reason: str


@dataclass(frozen=True)
class ContinuousUwbTransactionDecision:
    """Outcome of one credible observation and its bounded state transaction.

    ``solver_position_correction_m`` is the unmodified position change proposed
    by the raw-range solver. ``proposed_position_correction_m`` is the bounded
    effective-gain change committed to the continuous state, and is zero when
    no state transaction was committed.  This distinction keeps measurement
    credibility separate from state-influence continuity.
    """

    accepted: bool
    reason: str
    solver_accepted: bool
    solver_reason: str
    proposed_position_correction_m: np.ndarray
    solver_position_correction_m: np.ndarray
    recovery_good_events: int
    recovery_required_events: int
    solver_decision: RawRangeDecision

    def __post_init__(self) -> None:
        arrays = []
        for value in (
            self.proposed_position_correction_m,
            self.solver_position_correction_m,
        ):
            array = np.asarray(value, dtype=float).copy()
            if array.shape != (3,) or not np.isfinite(array).all():
                raise ValueError("continuous UWB corrections must be finite 3-vectors")
            array.setflags(write=False)
            arrays.append(array)
        if (
            type(self.accepted) is not bool
            or type(self.solver_accepted) is not bool
            or not self.reason
            or not self.solver_reason
            or self.recovery_good_events < 0
            or self.recovery_required_events < 1
        ):
            raise ValueError("invalid continuous UWB transaction decision")
        object.__setattr__(self, "proposed_position_correction_m", arrays[0])
        object.__setattr__(self, "solver_position_correction_m", arrays[1])


class PelvisContinuousVQF:
    """One capture-lifetime native-200 VQF with one explicit heading gauge."""

    def __init__(self, *, sample_period_s: float = 0.005):
        if sample_period_s != 0.005:
            raise ValueError("Capture2 pelvis VQF requires the native 5 ms cadence")
        self._block = qmt.OriEstVQFBlock(sample_period_s)
        self._forward_preparation: list[np.ndarray] = []
        self._navigation_from_vqf: np.ndarray | None = None
        self._last_timer_us: int | None = None
        self._boot: int | None = None
        self.samples = 0
        self.gaps: list[tuple[int, int]] = []

    def clone(self) -> "PelvisContinuousVQF":
        """Clone the complete filter state for an atomic owner transaction."""

        candidate = PelvisContinuousVQF(sample_period_s=self._block.Ts)
        candidate._block.obj.state = deepcopy(self._block.obj.state)
        candidate._forward_preparation = [row.copy() for row in self._forward_preparation]
        candidate._navigation_from_vqf = (
            None if self._navigation_from_vqf is None
            else self._navigation_from_vqf.copy()
        )
        candidate._last_timer_us = self._last_timer_us
        candidate._boot = self._boot
        candidate.samples = self.samples
        candidate.gaps = list(self.gaps)
        return candidate

    def step(self, *, boot: int, timer_us: int, acc_raw: Iterable[int],
             gyro_raw: Iterable[int], preparation: bool = False) -> tuple[np.ndarray, np.ndarray] | None:
        if self._boot is None:
            self._boot = int(boot)
        if int(boot) != self._boot:
            raise ValueError("pelvis VQF boot changed inside continuous capture")
        if self._last_timer_us is not None:
            delta = int(timer_us) - self._last_timer_us
            if delta <= 0:
                raise ValueError("pelvis TIMER2 is duplicate or reordered")
            if delta != 5000:
                self.gaps.append((self._last_timer_us, int(timer_us)))
        acceleration = np.asarray(tuple(acc_raw), dtype=float) / 2048.0 * 9.80665
        gyroscope = np.deg2rad(np.asarray(tuple(gyro_raw), dtype=float) / 16.384)
        quaternion = np.asarray(self._block.step(gyroscope, acceleration, None), dtype=float)
        rotation_vqf = Rotation.from_quat(quaternion[[1, 2, 3, 0]]).as_matrix()
        self._last_timer_us = int(timer_us)
        self.samples += 1
        if preparation and self._navigation_from_vqf is None:
            self._forward_preparation.append(rotation_vqf @ np.array([0.0, 0.0, -1.0]))
            return None
        if self._navigation_from_vqf is None:
            if len(self._forward_preparation) < 100:
                raise RuntimeError("insufficient fixed preparation for heading gauge")
            forward = np.median(np.stack(self._forward_preparation), axis=0)
            if np.linalg.norm(forward[:2]) < 0.25:
                raise RuntimeError("pelvis minus-Z lacks stable horizontal heading")
            yaw = -math.pi / 2.0 - math.atan2(forward[1], forward[0])
            c, s = math.cos(yaw), math.sin(yaw)
            self._navigation_from_vqf = np.array([[c, -s, 0.0], [s, c, 0.0], [0.0, 0.0, 1.0]])
        return acceleration, self._navigation_from_vqf @ rotation_vqf


def _valid_slots(row: UwbRow) -> tuple[int, ...]:
    if tuple(row.anchor_ids) != tuple(range(8)):
        return ()
    return tuple(
        slot for slot in range(8)
        if row.valid_mask & (1 << slot) and 0 < row.ranges_mm[slot] < 0xFFFF
    )


def raw_range_reference_ns(row: UwbRow, clock: ClockModel) -> tuple[float, int]:
    """Return the raw median link epoch and its canonical rounded ns owner."""
    slots = _valid_slots(row)
    if len(slots) < 4:
        raise ValueError("raw range reference needs four canonical links")
    if row.boot != clock.boot_epoch:
        raise ValueError("raw range reference clock boot mismatch")
    epochs_ns = np.asarray([
        clock.a_ns_per_us * (row.strobe_us + 0.5 * row.t_round_us[slot])
        + clock.b_ns
        for slot in slots
    ], dtype=float)
    raw_reference_ns = float(np.median(epochs_ns))
    if not math.isfinite(raw_reference_ns) or raw_reference_ns < 0.0:
        raise ValueError("raw range reference is not a finite nonnegative epoch")
    return raw_reference_ns, int(round(raw_reference_ns))


def admit_uwb_timestamp(row: UwbRow, clock: ClockModel) -> UwbTimestampAdmission:
    """Separate unconditional UWB stream progress from solver admission."""
    if row.boot != clock.boot_epoch:
        raise ValueError("UWB timestamp clock boot mismatch")
    raw_progress_ns = clock.a_ns_per_us * row.strobe_us + clock.b_ns
    if not math.isfinite(raw_progress_ns) or raw_progress_ns < 0.0:
        raise ValueError("UWB strobe is not a finite nonnegative epoch")
    progress_ns = int(round(raw_progress_ns))
    if tuple(row.anchor_ids) != tuple(range(8)):
        return UwbTimestampAdmission(
            progress_ns, progress_ns, None, False,
            "ANCHOR_IDENTITY_MISMATCH_STROBE_WATERMARK_ONLY",
        )
    slots = tuple(
        slot for slot in range(8)
        if row.valid_mask & (1 << slot) and 0 < row.ranges_mm[slot] < 0xFFFF
    )
    if not slots:
        return UwbTimestampAdmission(
            progress_ns, progress_ns, None, False,
            "NO_VALID_LINK_STROBE_WATERMARK_ONLY",
        )
    if len(slots) < 4:
        return UwbTimestampAdmission(
            progress_ns, progress_ns, None, False,
            "FEWER_THAN_FOUR_VALID_LINKS_STROBE_WATERMARK_ONLY",
        )
    raw_reference_ns, reference_ns = raw_range_reference_ns(row, clock)
    if raw_reference_ns + 1e-9 < raw_progress_ns:
        raise ValueError("UWB range reference precedes its strobe progress")
    return UwbTimestampAdmission(
        progress_ns, reference_ns, raw_reference_ns, True, "ELIGIBLE_RANGE_REFERENCE",
    )


def uwb_row_from_event(event: TypedEvent) -> UwbRow:
    if event.record_type is not RecordType.UWB:
        raise TypeError("UWB adapter requires a UWB TypedEvent")
    value = event.payload
    return UwbRow(
        node=event.node_id, boot=event.boot_epoch,
        sequence=int(value["packet_sequence"]), sweep=int(value["sweep"]),
        strobe_us=int(value["strobe_us"]), frame_us=int(value["frame_us"]),
        anchor_ids=tuple(int(x) for x in value["anchor_id"]),
        ranges_mm=tuple(int(x) for x in value["range_mm"]),
        t_round_us=tuple(int(x) for x in value["t_round_us"]),
        quality=tuple(int(x) for x in value["quality_percent"]),
        valid_mask=int(value["valid_mask"]), identity=int(value["identity"]),
        node_ms=int(value["node_ms"]),
    )


def bootstrap_action00_root(
    row: UwbRow,
    *,
    measurement_time_ns: int,
    anchors_m: np.ndarray,
    clock: ClockModel,
    nominal_sigma_m: float = 0.12,
    maximum_condition: float = 1e8,
) -> DiagnosticBootstrap:
    """Use the existing shared-root solver without any position prior.

    The deterministic starts are the eight inset corners of the authenticated
    anchor volume.  Neither its centroid nor a literal body height is a start.
    Failed starts are local optimizer internals and are never returned.
    """
    anchors = np.asarray(anchors_m, dtype=float)
    if type(measurement_time_ns) is not int or measurement_time_ns < 0:
        raise ValueError("bootstrap requires its canonical integer-nanosecond epoch")
    if anchors.shape != (8, 3) or not np.isfinite(anchors).all():
        raise ValueError("anchors must be a finite canonical 8x3 layout")
    if row.boot != clock.boot_epoch:
        raise ValueError("bootstrap clock boot mismatch")
    slots = _valid_slots(row)
    if len(slots) < 4:
        raise ValueError("bootstrap needs at least four canonical links")
    epochs = np.asarray([
        clock.seconds(row.strobe_us + 0.5 * row.t_round_us[slot])
        for slot in slots
    ])
    reference = float(np.median(epochs))
    raw_reference_ns, rounded_reference_ns = raw_range_reference_ns(row, clock)
    canonical_reference = measurement_time_ns * 1e-9
    if (measurement_time_ns != rounded_reference_ns
            or abs(raw_reference_ns - measurement_time_ns) > 0.500001):
        raise ValueError("canonical bootstrap epoch is not the rounded raw median link epoch")
    links = tuple(
        SharedRangeLink(
            node=row.node,
            anchor=slot,
            range_m=float(row.ranges_mm[slot]) / 1000.0,
            tag_offset_world_m=np.zeros(3),
            link_dt_s=float(epochs[index] - reference),
            sigma_m=float(nominal_sigma_m) * math.sqrt(100.0 / max(float(row.quality[slot]), 1.0)),
        )
        for index, slot in enumerate(slots)
    )
    lower = anchors.min(axis=0)
    upper = anchors.max(axis=0)
    inset = np.minimum(0.15 * np.maximum(upper - lower, 1e-6), 0.25)
    starts = tuple(np.array([x, y, z], dtype=float) for x in (lower[0] + inset[0], upper[0] - inset[0])
                   for y in (lower[1] + inset[1], upper[1] - inset[1])
                   for z in (lower[2] + inset[2], upper[2] - inset[2]))
    accepted = []
    for start in starts:
        result = solve_shared_root(
            links, anchors_m=anchors, initial_root_m=start,
            maximum_condition=maximum_condition,
        )
        if result.success:
            accepted.append(result)
    if not accepted:
        raise RuntimeError("no prior-free Action00 raw-range bootstrap converged")
    accepted.sort(key=lambda result: (result.cost, tuple(result.root_position_m)))
    best = accepted[0]
    roots = np.stack([result.root_position_m for result in accepted])
    if float(np.max(np.linalg.norm(roots - best.root_position_m, axis=1))) > 0.05:
        raise RuntimeError("Action00 multistart bootstrap lacks consensus")
    residual_rms = float(np.sqrt(np.mean(np.square(best.residuals_m))))
    if not math.isfinite(residual_rms) or residual_rms > 0.50:
        raise RuntimeError("Action00 bootstrap residual gate failed")

    # Diagnostic-only local geometry covariance.  This is explicitly not a
    # calibrated UWB R matrix and therefore cannot support scientific_pass.
    positions = best.root_position_m[None, :]
    delta = positions - anchors[np.asarray(best.anchors_used, dtype=int)]
    jacobian = delta / np.linalg.norm(delta, axis=1)[:, None]
    information = jacobian.T @ jacobian / nominal_sigma_m**2
    position_covariance = np.linalg.inv(information)
    covariance = np.zeros((9, 9), dtype=float)
    covariance[:3, :3] = position_covariance + np.eye(3) * 1e-6
    covariance[3:6, 3:6] = np.eye(3)
    covariance[6:9, 6:9] = np.eye(3) * 0.25
    state = RootState(
        canonical_reference, np.r_[best.root_position_m, np.zeros(6)], covariance,
    )
    return DiagnosticBootstrap(state, best.anchors_used, best.condition, residual_rms)


def hold_mean_no_measurement_gap(
    state: RootState, target_time_s: float, config: RootFilterConfig,
) -> RootState:
    """Advance chronology and covariance without inventing mean motion."""
    dt = float(target_time_s) - state.time_s
    if not math.isfinite(dt) or dt <= 0.0:
        raise ValueError("gap target must be finite and later than root state")
    sigma2 = config.cv_acceleration_noise_mps2_sqrt_hz**2
    q = np.zeros((9, 9), dtype=float)
    q[:3, :3] = np.eye(3) * sigma2 * dt**3 / 3.0
    q[:3, 3:6] = q[3:6, :3] = np.eye(3) * sigma2 * dt**2 / 2.0
    q[3:6, 3:6] = np.eye(3) * sigma2 * dt
    q[6:9, 6:9] = np.eye(3) * config.covariance_floor
    return RootState(target_time_s, state.vector.copy(), state.covariance + q)


class ContinuousRootAB:
    """Continuous common-bootstrap A/B root state; action labels are inert."""

    def __init__(self, bootstrap: DiagnosticBootstrap, *,
                 root_config: RootFilterConfig = RootFilterConfig(),
                 range_config: RawRangeUpdateConfig = RawRangeUpdateConfig()):
        state = bootstrap.state
        self.a = RootState(state.time_s, state.vector.copy(), state.covariance.copy())
        self.b = RootState(state.time_s, state.vector.copy(), state.covariance.copy())
        self.root_config = root_config
        self.range_config = range_config
        self.last_imu_time_s: float | None = None
        self.imu_samples = 0
        self.missing_imu_intervals = 0
        self.maximum_imu_gap_s = 0.0
        self.a_uwb_commits = 0
        self.b_uwb_accepted = 0
        self.b_uwb_rejected = 0
        self._last_force = np.array([0.0, 0.0, 9.80665])
        self._last_rotation = np.eye(3)
        # The bootstrap is an accepted UWB root observation.  Subsequent
        # solver-success rows remain nominal while they arrive continuously;
        # a longer absence starts a new fail-closed recovery sequence.
        self._last_uwb_solver_success_time_s = float(state.time_s)
        self._uwb_recovery_good = int(root_config.recovery_good_events)

    def _uwb_transaction_decision(
        self,
        *,
        committed: bool,
        reason: str,
        solver_decision: RawRangeDecision,
        applied_correction: np.ndarray,
        solver_correction: np.ndarray,
    ) -> ContinuousUwbTransactionDecision:
        return ContinuousUwbTransactionDecision(
            accepted=bool(committed),
            reason=reason,
            solver_accepted=bool(solver_decision.accepted),
            solver_reason=solver_decision.reason,
            proposed_position_correction_m=applied_correction,
            solver_position_correction_m=solver_correction,
            recovery_good_events=self._uwb_recovery_good,
            recovery_required_events=self.root_config.recovery_good_events,
            solver_decision=solver_decision,
        )

    def label_boundary(self, _action_index: int, _action_id: str) -> None:
        """Intentionally inert: labels never reset physical state."""

    def add_imu(self, *, time_s: float, force_sensor_mps2: np.ndarray,
                rotation_world_from_sensor: np.ndarray) -> None:
        if time_s <= self.a.time_s + 1e-12:
            raise ValueError("IMU time is not strictly increasing")
        if self.last_imu_time_s is not None:
            dt = time_s - self.last_imu_time_s
            self.maximum_imu_gap_s = max(self.maximum_imu_gap_s, dt)
            if dt > 0.005001:
                self.missing_imu_intervals += max(1, int(round(dt / 0.005)) - 1)
                self.a = hold_mean_no_measurement_gap(self.a, time_s, self.root_config)
                self.b = hold_mean_no_measurement_gap(self.b, time_s, self.root_config)
                self.last_imu_time_s = time_s
                self._last_force = np.asarray(force_sensor_mps2, dtype=float).copy()
                self._last_rotation = np.asarray(rotation_world_from_sensor, dtype=float).copy()
                self.imu_samples += 1
                return
        self.a, _ = propagate_inertial(self.a, time_s, force_sensor_mps2,
                                       rotation_world_from_sensor, self.root_config)
        self.b, _ = propagate_inertial(self.b, time_s, force_sensor_mps2,
                                       rotation_world_from_sensor, self.root_config)
        self.last_imu_time_s = time_s
        self._last_force = np.asarray(force_sensor_mps2, dtype=float).copy()
        self._last_rotation = np.asarray(rotation_world_from_sensor, dtype=float).copy()
        self.imu_samples += 1

    def add_uwb(self, row: UwbRow, *, measurement_time_ns: int,
                anchors_m: np.ndarray, clock: ClockModel):
        if type(measurement_time_ns) is not int or measurement_time_ns < 0:
            raise ValueError("UWB update requires its canonical integer-nanosecond epoch")
        slots = _valid_slots(row)
        if len(slots) < 4:
            self.b_uwb_rejected += 1
            return None
        raw_reference_ns, rounded_reference_ns = raw_range_reference_ns(row, clock)
        canonical_reference = measurement_time_ns * 1e-9
        if (measurement_time_ns != rounded_reference_ns
                or abs(raw_reference_ns - measurement_time_ns) > 0.500001):
            raise ValueError("canonical UWB epoch is not the rounded raw median link epoch")
        if canonical_reference <= self.b.time_s + 1e-12:
            self.b_uwb_rejected += 1
            return None
        prediction, _ = propagate_inertial(
            self.b, canonical_reference, self._last_force, self._last_rotation, self.root_config,
        )
        prepared = prepare_raw_range_update(
            prediction,
            row,
            anchors_m=anchors_m,
            clock=clock,
            config=self.range_config,
        )
        candidate, solver_decision = update_raw_ranges(
            prediction,
            row,
            anchors_m=anchors_m,
            clock=clock,
            config=self.range_config,
            prepared=prepared,
        )
        candidate = RootState(
            canonical_reference, candidate.vector.copy(), candidate.covariance.copy(),
        )
        solver_correction = candidate.position_m - prediction.position_m
        zero_correction = np.zeros(3)

        # The prediction is temporary until a complete transaction commits.
        # A rejection therefore leaves state time, mean, covariance, and the
        # future IMU propagation path exactly as if this UWB event never ran.
        if not solver_decision.accepted:
            self._uwb_recovery_good = 0
            self.b_uwb_rejected += 1
            return self._uwb_transaction_decision(
                committed=False,
                reason=f"REJECT_SOLVER_{solver_decision.reason}",
                solver_decision=solver_decision,
                applied_correction=zero_correction,
                solver_correction=solver_correction,
            )

        had_gap = (
            canonical_reference - self._last_uwb_solver_success_time_s
            > self.root_config.uwb_dropout_s
        )
        self._uwb_recovery_good = (
            1 if had_gap else min(
                self.root_config.recovery_good_events,
                self._uwb_recovery_good + 1,
            )
        )
        self._last_uwb_solver_success_time_s = canonical_reference
        if self._uwb_recovery_good < self.root_config.recovery_good_events:
            self.b_uwb_rejected += 1
            return self._uwb_transaction_decision(
                committed=False,
                reason=(
                    "REJECT_PROPOSED_STATE_UWB_RECOVERY_"
                    f"{self._uwb_recovery_good}_OF_{self.root_config.recovery_good_events}"
                ),
                solver_decision=solver_decision,
                applied_correction=zero_correction,
                solver_correction=solver_correction,
            )

        correction_norm = float(np.linalg.norm(solver_correction))
        maximum = self.root_config.maximum_position_influence_m
        scale = (
            1.0
            if correction_norm <= maximum or correction_norm == 0.0
            else maximum / correction_norm
        )
        committed, bounded_decision = update_raw_ranges(
            prediction,
            row,
            anchors_m=anchors_m,
            clock=clock,
            correction_gain=scale,
            config=self.range_config,
            prepared=prepared,
        )
        if not bounded_decision.accepted:
            self._uwb_recovery_good = 0
            self.b_uwb_rejected += 1
            return self._uwb_transaction_decision(
                committed=False,
                reason=f"REJECT_BOUNDED_COMMIT_{bounded_decision.reason}",
                solver_decision=solver_decision,
                applied_correction=zero_correction,
                solver_correction=solver_correction,
            )
        committed = RootState(
            canonical_reference,
            committed.vector.copy(),
            committed.covariance.copy(),
        )
        applied_correction = committed.position_m - prediction.position_m
        if (
            np.linalg.norm(applied_correction)
            > self.root_config.maximum_position_influence_m + 1e-12
        ):
            raise RuntimeError("bounded raw-range commit exceeded position influence invariant")

        # The existing raw-range owner applies the same effective gain to all
        # nine channels and their covariance.  This is one atomic commit; no
        # external covariance interpolation or backlog exists here.
        self.b = committed
        self.b_uwb_accepted += 1
        return self._uwb_transaction_decision(
            committed=True,
            reason=(
                "ACCEPTED_COMMITTED_FULL_MEASUREMENT_INFLUENCE"
                if scale == 1.0
                else "ACCEPTED_COMMITTED_BOUNDED_MEASUREMENT_INFLUENCE"
            ),
            solver_decision=solver_decision,
            applied_correction=applied_correction,
            solver_correction=solver_correction,
        )

    def metrics(self) -> RootABMetrics:
        return RootABMetrics(
            self.imu_samples, self.missing_imu_intervals, self.maximum_imu_gap_s,
            self.a_uwb_commits, self.b_uwb_accepted, self.b_uwb_rejected,
        )
