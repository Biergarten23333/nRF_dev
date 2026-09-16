"""Isolated U7A root-only real-time engineering coordinator.

This is a fixture-qualified scheduling owner, not a production runner.  It
retains the existing shared-root candidate, U1 guard, and atomic U2 root-only
transaction without enabling the articulated transaction path.
"""
from __future__ import annotations

from dataclasses import dataclass
from collections import deque
import math
import sys
import time
from typing import Callable, Mapping, Sequence

import numpy as np

from biospur_fusion.c2_uwb_calibration.adaptive_nodes import (
    AdaptiveNodeTrustConfig,
    adaptive_root_minimum_std_m,
    select_trusted_body_nodes,
)
from biospur_fusion.c2_uwb_calibration.shared_root import solve_shared_root
from biospur_fusion.c2_uwb_root_world import causal_update_transaction as u2
from biospur_fusion.c2_uwb_root_world.causal_update_guard import (
    CandidateKind,
    CausalContactTransitionEvidence,
    CausalImuActivitySummary,
    IndependentNodeConsensusEvidence,
    ReachabilityEnvelope,
)
from biospur_fusion.c2_uwb_root_world.offline_unified_wiring import (
    StrictFloorOffset,
    build_causal_links,
)
from biospur_fusion.c2_uwb_root_world.u0 import UwbRow
from biospur_fusion.root_r3.estimator import CausalDelayedRootFilter
from biospur_fusion.root_r3.models import ImuSample, PositionObservation


ROOT_UWB_SERVICE_INTERVAL_MS = 1000.0 / (10.0 * 8.33)


def _readonly(value: object, shape: tuple[int, ...]) -> np.ndarray:
    result = np.array(value, dtype=float, copy=True)
    if result.shape != shape or not np.isfinite(result).all():
        raise ValueError(f"expected finite array {shape}")
    result.setflags(write=False)
    return result


@dataclass(frozen=True)
class RootObservationUncertainty:
    h: np.ndarray
    r: np.ndarray
    s: np.ndarray
    innovation_m: np.ndarray
    nis: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "h", _readonly(self.h, (3, 9)))
        object.__setattr__(self, "r", _readonly(self.r, (3, 3)))
        object.__setattr__(self, "s", _readonly(self.s, (3, 3)))
        object.__setattr__(self, "innovation_m", _readonly(self.innovation_m, (3,)))
        if not math.isfinite(self.nis) or self.nis < 0.0:
            raise ValueError("invalid root observation NIS")


@dataclass(frozen=True)
class RootRealtimeGroupResult:
    candidate_reason: str
    candidate_root_m: np.ndarray
    candidate_rank: int
    candidate_condition: float
    transaction: u2.TransactionResult
    uncertainty: RootObservationUncertainty
    candidate_ms: float
    guard_ms: float
    transaction_ms: float
    root_uwb_total_ms: float
    candidate_calls: int
    guard_calls: int
    transaction_calls: int
    future_access: bool = False
    execution_class: str = "ENGINEERING_FIXTURE_ONLY"
    articulated_online_status: str = "OFFLINE_ONLY_ONLINE_BLOCKED"

    def __post_init__(self) -> None:
        object.__setattr__(self, "candidate_root_m", _readonly(self.candidate_root_m, (3,)))


class _RootPlanCapture:
    def __init__(self, owner: CausalDelayedRootFilter):
        self.owner = owner
        self.plan = None
        self.prepare_calls = 0

    def prepare_position(self, *args, **kwargs):
        self.prepare_calls += 1
        self.plan = self.owner.prepare_position(*args, **kwargs)
        return self.plan

    def __getattr__(self, name):
        return getattr(self.owner, name)


class RootOnlyRealtimeCoordinator:
    """Single-thread, monotonic ROOT-only event-loop owner."""

    def __init__(self, root: CausalDelayedRootFilter, *, queue_capacity: int = 64):
        if isinstance(queue_capacity, bool) or not 1 <= int(queue_capacity) <= 256:
            raise ValueError("fixed queue capacity is invalid")
        self.root = root
        self.queue_capacity = int(queue_capacity)
        self._queue: deque[tuple[float, str, object]] = deque()
        self._last_arrival_s = -math.inf
        self._last_imu_s = float(root.current_state.time_s)
        self._last_group_availability_s = -math.inf
        self.queue_high_watermark = 0
        self.imu_calls = 0
        self.group_calls = 0
        self.imu_timings_ms: list[float] = []
        self.uwb_timings_ms: list[float] = []

    def enqueue(self, availability_time_s: float, kind: str, payload: object) -> None:
        value = float(availability_time_s)
        if not math.isfinite(value) or value < self._last_arrival_s:
            raise ValueError("event availability reversed or nonfinite")
        if kind not in ("IMU", "UWB"):
            raise ValueError("unknown event kind")
        if len(self._queue) >= self.queue_capacity:
            raise OverflowError("fixed causal event queue overflow")
        self._queue.append((value, kind, payload))
        self._last_arrival_s = value
        self.queue_high_watermark = max(self.queue_high_watermark, len(self._queue))

    def dequeue(self) -> tuple[float, str, object]:
        if not self._queue:
            raise IndexError("empty causal event queue")
        return self._queue.popleft()

    @property
    def queued_events(self) -> int:
        return len(self._queue)

    def add_imu(self, sample: ImuSample) -> float:
        if sample.measurement_time_s != sample.availability_time_s:
            raise ValueError("native200 fixture requires same-time causal availability")
        if sample.measurement_time_s <= self._last_imu_s:
            raise ValueError("native200 sample is duplicate or reversed")
        started = time.perf_counter_ns()
        accepted = self.root.add_imu(sample)
        elapsed = (time.perf_counter_ns() - started) * 1e-6
        if not accepted:
            raise RuntimeError("authoritative root rejected in-order IMU sample")
        self._last_imu_s = sample.measurement_time_s
        self.imu_calls += 1
        self.imu_timings_ms.append(elapsed)
        return elapsed

    def process_group(
        self, *, rows: Sequence[UwbRow], clocks: Mapping[str, object],
        strict_floor_offset: Callable[[str, float], StrictFloorOffset],
        anchors_m: Mapping[int, np.ndarray] | np.ndarray,
        anchor_delay_m: Sequence[float], tag_delay_m: float,
        sigma_for_quality: Callable[[int], float],
        nominal_envelope: ReachabilityEnvelope,
        dynamic_envelope: ReachabilityEnvelope | None = None,
        activity: CausalImuActivitySummary | None = None,
        consensus: IndependentNodeConsensusEvidence | None = None,
        contact: CausalContactTransitionEvidence | None = None,
        trust_config: AdaptiveNodeTrustConfig = AdaptiveNodeTrustConfig(),
        source_sequence: int = 0,
    ) -> RootRealtimeGroupResult:
        candidate_started = time.perf_counter_ns()
        links, _audits, measurement_s, availability_s = build_causal_links(
            rows, clocks=clocks, strict_floor_offset=strict_floor_offset,
            anchor_delay_m=anchor_delay_m, tag_delay_m=tag_delay_m,
            sigma_for_quality=sigma_for_quality)
        if availability_s < self._last_group_availability_s:
            raise ValueError("UWB group availability reversed")
        if self.root.current_state.time_s > availability_s + 1e-12:
            raise ValueError("UWB group would consume a future root publication")
        token = self.root.publication_token()
        selection = select_trusted_body_nodes(
            links, anchors_m=anchors_m, initial_root_m=token.state.vector[:3],
            root_velocity_mps=token.state.vector[3:6], total_nodes=10,
            config=trust_config)
        if not selection.trusted_links:
            raise RuntimeError("NO_TRUSTED_NODE")
        candidate = solve_shared_root(
            selection.trusted_links, anchors_m=anchors_m,
            initial_root_m=token.state.vector[:3],
            root_velocity_mps=token.state.vector[3:6])
        if not candidate.success or candidate.rank != 3 or not math.isfinite(candidate.condition):
            raise RuntimeError(f"ROOT_CANDIDATE_{candidate.reason}")
        minimum_std = adaptive_root_minimum_std_m(len(selection.trusted_nodes))
        covariance = np.eye(3) * minimum_std ** 2
        observation = PositionObservation(
            measurement_time_s=measurement_s, availability_time_s=availability_s,
            root_position_m=candidate.root_position_m, covariance_m2=covariance,
            tag_id="C2_SHARED_ROOT_DIAGNOSTIC", anchors=candidate.anchors_used,
            quality_state="DIAGNOSTIC_NODE_COUNT_FLOOR_ONLY_NOT_CALIBRATED_R",
            source_sequence=int(source_sequence))
        tail_edge = self.root._make_edge(
            start_time_s=self.root.current_state.time_s,
            end_time_s=availability_s,
            force=self.root._last_force,
            rotation=self.root._last_rotation,
            input_owner="EPHEMERAL_AVAILABILITY_HELD_INPUT",
        ) if availability_s > self.root.current_state.time_s + 1e-12 else None
        located = self.root._state_at(measurement_s, tail_edge=tail_edge)
        if located is None:
            raise RuntimeError("root uncertainty audit lies outside fixed lag")
        delayed_state = located[1]
        candidate_ms = (time.perf_counter_ns() - candidate_started) * 1e-6

        capture = _RootPlanCapture(self.root)
        guard_calls = 0
        guard_started = 0
        guard_elapsed_ns = 0
        target_code = u2.evaluate_candidate_transition.__code__
        prior_profiler = sys.getprofile()

        def profiler(frame, event, _arg):
            nonlocal guard_calls, guard_started, guard_elapsed_ns
            if frame.f_code is target_code:
                if event == "call":
                    guard_calls += 1; guard_started = time.perf_counter_ns()
                elif event == "return":
                    guard_elapsed_ns += time.perf_counter_ns() - guard_started

        transaction_started = time.perf_counter_ns()
        sys.setprofile(profiler)
        try:
            transaction = u2.execute_causal_update_transaction(
                root=capture, observation=observation,
                kind=CandidateKind.ROOT_POSITION,
                nominal_envelope=nominal_envelope,
                dynamic_envelope=dynamic_envelope, activity=activity,
                consensus=consensus, contact=contact)
        finally:
            sys.setprofile(prior_profiler)
        transaction_ms = (time.perf_counter_ns() - transaction_started) * 1e-6
        if capture.prepare_calls != 1 or capture.plan is None or guard_calls != 1:
            raise RuntimeError("root prepare or U1 guard call cardinality changed")
        plan = capture.plan
        h = np.zeros((3, 9)); h[:, :3] = np.eye(3)
        r = np.asarray(observation.covariance_m2, dtype=float)
        p = np.asarray(delayed_state.covariance, dtype=float)
        innovation = np.asarray(plan.decision.innovation_m, dtype=float)
        s = h @ p @ h.T + r
        nis = float(innovation @ np.linalg.solve(s, innovation))
        if plan.decision.nis is not None and not math.isclose(
            nis, plan.decision.nis, rel_tol=1e-12, abs_tol=1e-12):
            raise RuntimeError("root NIS owner diverged")
        self._last_group_availability_s = availability_s
        self.group_calls += 1
        total_ms = candidate_ms + transaction_ms
        self.uwb_timings_ms.append(total_ms)
        return RootRealtimeGroupResult(
            candidate.reason, candidate.root_position_m, candidate.rank,
            candidate.condition, transaction,
            RootObservationUncertainty(h, r, s, innovation, nis),
            candidate_ms, guard_elapsed_ns * 1e-6,
            transaction_ms, total_ms, 1, guard_calls, 1)


@dataclass(frozen=True)
class QueueSimulation:
    utilization: float
    deadline_misses: int
    queue_high_watermark: int
    overflow: bool
    drained_to_zero: bool


def simulate_single_thread_service(
    arrivals_ms: Sequence[float], service_ms: Sequence[float],
    deadlines_ms: Sequence[float], *, capacity: int,
) -> QueueSimulation:
    arrivals = np.asarray(arrivals_ms, dtype=float)
    service = np.asarray(service_ms, dtype=float)
    deadlines = np.asarray(deadlines_ms, dtype=float)
    if arrivals.shape != service.shape or arrivals.shape != deadlines.shape or arrivals.ndim != 1:
        raise ValueError("service simulation shapes differ")
    if len(arrivals) == 0 or not np.isfinite(np.r_[arrivals, service, deadlines]).all():
        raise ValueError("service simulation is empty or nonfinite")
    if np.any(np.diff(arrivals) < 0) or np.any(service < 0) or np.any(deadlines <= arrivals):
        raise ValueError("service timeline is invalid")
    finish = -math.inf; misses = 0; high = 0; overflow = False
    completion: deque[float] = deque()
    for arrival, duration, deadline in zip(arrivals, service, deadlines):
        while completion and completion[0] <= arrival:
            completion.popleft()
        if len(completion) >= capacity:
            overflow = True; break
        start = max(float(arrival), finish)
        finish = start + float(duration)
        completion.append(finish)
        high = max(high, len(completion))
        misses += int(finish > deadline)
    span = max(1e-12, float(deadlines[-1] - arrivals[0]))
    return QueueSimulation(float(np.sum(service) / span), misses, high, overflow,
                           not overflow and bool(finish <= deadlines[-1]))
