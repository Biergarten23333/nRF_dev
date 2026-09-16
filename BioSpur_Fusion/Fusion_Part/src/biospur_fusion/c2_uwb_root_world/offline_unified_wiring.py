"""Offline-only wiring of existing C2 range, trust, root, and U2 owners.

This module deliberately owns no estimator, clock fit, pose interpolation, or
measurement covariance calibration.  Callers provide sealed per-node clocks
and a strict-floor native-200 offset lookup.  The leave-node covariance is a
diagnostic runtime scale and is never a calibrated measurement ``R``.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Callable, Mapping, Sequence

import numpy as np
from biospur_fusion.c2_timing_contract import (
    MAXIMUM_POSE_AGE_NS,
    canonical_clock_global_ns,
)

from biospur_fusion.c2_uwb_calibration.adaptive_nodes import (
    AdaptiveNodeSelection,
    AdaptiveNodeTrustConfig,
    adaptive_root_minimum_std_m,
    select_trusted_body_nodes,
)
from biospur_fusion.c2_uwb_calibration.shared_root import (
    SharedRangeLink,
    SharedRootResult,
    solve_shared_root,
)
from biospur_fusion.c2_uwb_root_world.causal_update_guard import (
    CandidateKind,
    ReachabilityEnvelope,
)
from biospur_fusion.c2_uwb_root_world.causal_update_transaction import (
    TransactionResult,
    execute_causal_update_transaction,
)
from biospur_fusion.c2_uwb_root_world.u0 import UwbRow
from biospur_fusion.root_r3.estimator import CausalDelayedRootFilter
from biospur_fusion.root_r3.models import PositionObservation

U2_QUALIFICATION_SEAL_SHA256 = (
    "772483e908e55375c4195d89f096c44185e548e293d53adaa3d7e80a1ce5e39d"
)
ROOT_TRANSACTION_P99_DEBT_MS = 8.032351
ARTICULATED_TRANSACTION_P99_DEBT_MS = 350.311878
EPOCH_PERIOD_NS = 120_000_000


@dataclass(frozen=True)
class OfflineU3Boundary:
    execution_class: str = "OFFLINE_ONLY"
    online_status: str = "ONLINE_BLOCKED"
    calibrated_R: bool = False
    covariance_owner: str = "DIAGNOSTIC_NODE_COUNT_FLOOR_ONLY"
    scientific_pass: bool = False
    production_ready: bool = False
    u2_qualification_seal_sha256: str = U2_QUALIFICATION_SEAL_SHA256
    root_transaction_p99_debt_ms: float = ROOT_TRANSACTION_P99_DEBT_MS
    articulated_transaction_p99_debt_ms: float = ARTICULATED_TRANSACTION_P99_DEBT_MS


@dataclass(frozen=True)
class StrictFloorOffset:
    offset_world_m: np.ndarray
    pose_time_ns: int
    query_time_ns: float
    pose_age_ns: float
    pose_frame: int

    def __post_init__(self) -> None:
        offset = np.asarray(self.offset_world_m, dtype=float).copy()
        if offset.shape != (3,) or not np.isfinite(offset).all():
            raise ValueError("invalid strict-floor tag offset")
        if not (
            math.isfinite(float(self.query_time_ns))
            and math.isfinite(float(self.pose_age_ns))
            and 0.0 < float(self.pose_age_ns) <= MAXIMUM_POSE_AGE_NS
            and float(self.query_time_ns) - int(self.pose_time_ns)
            == float(self.pose_age_ns)
        ):
            raise ValueError("strict-floor pose is stale, noncausal, or inconsistent")
        offset.setflags(write=False)
        object.__setattr__(self, "offset_world_m", offset)


@dataclass(frozen=True)
class OfflineU3LinkAudit:
    node: str
    anchor: int
    link_time_ns: float
    pose_time_ns: int
    pose_age_ns: float
    pose_frame: int


@dataclass(frozen=True)
class OfflineU3GroupResult:
    measurement_time_s: float
    availability_time_s: float
    link_audit: tuple[OfflineU3LinkAudit, ...]
    selection: AdaptiveNodeSelection
    candidate: SharedRootResult
    covariance_m2: np.ndarray
    covariance_minimum_std_m: float
    transaction: TransactionResult
    candidate_solver_calls: int = 1
    transaction_calls: int = 1
    boundary: OfflineU3Boundary = OfflineU3Boundary()


def require_offline_mode(*, offline: bool, production: bool = False) -> None:
    """Fail before callers open input when an online/product run is requested."""
    if type(offline) is not bool or type(production) is not bool:
        raise ValueError("execution mode flags must be exact bool")
    if not offline or production:
        raise RuntimeError("U3_OFFLINE_ONLY_ONLINE_AND_PRODUCTION_BLOCKED")


def _valid_slots(row: UwbRow) -> tuple[int, ...]:
    if len(row.anchor_ids) != 8 or tuple(row.anchor_ids) != tuple(range(8)):
        raise ValueError("noncanonical anchor inventory")
    return tuple(
        slot for slot in range(8)
        if row.valid_mask & (1 << slot)
        and 0 < int(row.ranges_mm[slot]) < 0xFFFF
        and math.isfinite(float(row.t_round_us[slot]))
        and float(row.t_round_us[slot]) >= 0.0
    )


def group_epoch_times_ns(
    rows: Sequence[UwbRow], *, clocks: Mapping[str, object]
) -> tuple[np.ndarray, float, float]:
    """Return exact link epochs, their median measurement, and frame availability."""
    if not rows:
        raise ValueError("empty UWB group")
    nodes = tuple(str(row.node) for row in rows)
    if len(set(nodes)) != len(nodes):
        raise ValueError("duplicate node row in UWB group")
    epochs: list[float] = []
    availability: list[float] = []
    identities: set[tuple[str, int]] = set()
    for row in rows:
        if row.node not in clocks:
            raise ValueError("missing node clock")
        clock = clocks[row.node]
        availability.append(float(clock.link_time_ns(
            event_boot_epoch=row.boot, strobe_us=row.frame_us, t_round_us=0.0
        )))
        for slot in _valid_slots(row):
            identity = (str(row.node), slot)
            if identity in identities:
                raise ValueError("duplicate canonical node-anchor identity")
            identities.add(identity)
            epochs.append(float(clock.link_time_ns(
                event_boot_epoch=row.boot,
                strobe_us=row.strobe_us,
                t_round_us=float(row.t_round_us[slot]),
            )))
    if len(epochs) < 4:
        raise ValueError("fewer than four canonical links")
    values = np.asarray(epochs, dtype=float)
    if not np.isfinite(values).all():
        raise ValueError("nonfinite link epoch")
    return values, float(np.median(values)), float(max(availability))


def canonical_group_frame_lower_ns(
    rows: Sequence[UwbRow], *, clocks: Mapping[str, object]
) -> int:
    """Return the canonical integer-ns lower bound for group availability."""
    if not rows:
        raise ValueError("empty UWB group")
    return max(
        canonical_clock_global_ns(
            clocks[row.node].link_time_ns(
                event_boot_epoch=row.boot,
                strobe_us=row.frame_us,
                t_round_us=0.0,
            )
        )
        for row in rows
    )


def build_causal_links(
    rows: Sequence[UwbRow], *, clocks: Mapping[str, object],
    strict_floor_offset: Callable[[str, float], StrictFloorOffset],
    anchor_delay_m: Sequence[float], tag_delay_m: float,
    sigma_for_quality: Callable[[int], float],
) -> tuple[tuple[SharedRangeLink, ...], tuple[OfflineU3LinkAudit, ...], float, float]:
    epochs, measurement_ns, availability_ns = group_epoch_times_ns(rows, clocks=clocks)
    del epochs
    delay = np.asarray(anchor_delay_m, dtype=float)
    if delay.shape != (8,) or not np.isfinite(delay).all() or not math.isfinite(tag_delay_m):
        raise ValueError("invalid range calibration")
    links: list[SharedRangeLink] = []
    audits: list[OfflineU3LinkAudit] = []
    for row in sorted(rows, key=lambda item: str(item.node)):
        clock = clocks[row.node]
        for slot in _valid_slots(row):
            link_ns = float(clock.link_time_ns(
                event_boot_epoch=row.boot, strobe_us=row.strobe_us,
                t_round_us=float(row.t_round_us[slot])))
            pose = strict_floor_offset(str(row.node), link_ns)
            if pose.query_time_ns != link_ns:
                raise ValueError("pose lookup does not bind the link epoch")
            sigma = float(sigma_for_quality(int(row.quality[slot])))
            corrected = float(row.ranges_mm[slot]) / 1000.0 - delay[slot] - float(tag_delay_m)
            if not math.isfinite(sigma) or sigma <= 0.0:
                raise ValueError("invalid calibrated range sigma")
            # A corrupt/NLOS row must not abort the complete ten-node epoch
            # before adaptive node selection gets a chance to isolate it.
            # A non-positive delay-corrected range carries no physical range
            # information, so treat that link as unavailable.  The remaining
            # nodes can still constrain the shared skeleton/root and the
            # articulated owner propagates that result to the excluded node.
            if not math.isfinite(corrected) or corrected <= 0.0:
                continue
            links.append(SharedRangeLink(
                node=str(row.node), anchor=slot, range_m=corrected,
                tag_offset_world_m=pose.offset_world_m,
                link_dt_s=(link_ns - measurement_ns) * 1e-9,
                sigma_m=sigma,
            ))
            audits.append(OfflineU3LinkAudit(
                str(row.node), slot, link_ns, int(pose.pose_time_ns),
                float(pose.pose_age_ns), int(pose.pose_frame)))
    if len(links) < 4:
        raise ValueError("fewer than four physically admissible calibrated links")
    return tuple(links), tuple(audits), measurement_ns * 1e-9, availability_ns * 1e-9


def execute_offline_root_group(
    *, root: CausalDelayedRootFilter, rows: Sequence[UwbRow],
    clocks: Mapping[str, object], strict_floor_offset: Callable[[str, float], StrictFloorOffset],
    anchors_m: Mapping[int, np.ndarray] | np.ndarray,
    anchor_delay_m: Sequence[float], tag_delay_m: float,
    sigma_for_quality: Callable[[int], float], nominal_envelope: ReachabilityEnvelope,
    trust_config: AdaptiveNodeTrustConfig = AdaptiveNodeTrustConfig(),
    source_sequence: int = 0,
) -> OfflineU3GroupResult:
    """Execute the existing x/10 -> one root solve -> one U2 transaction chain."""
    links, audits, measurement_s, availability_s = build_causal_links(
        rows, clocks=clocks, strict_floor_offset=strict_floor_offset,
        anchor_delay_m=anchor_delay_m, tag_delay_m=tag_delay_m,
        sigma_for_quality=sigma_for_quality)
    token = root.publication_token()
    if token.time_s > availability_s:
        raise ValueError("root publication is from the future")
    initial = np.asarray(token.state.vector[:3], dtype=float)
    velocity = np.asarray(token.state.vector[3:6], dtype=float)
    selection = select_trusted_body_nodes(
        links, anchors_m=anchors_m, initial_root_m=initial,
        root_velocity_mps=velocity, total_nodes=10, config=trust_config)
    if not selection.trusted_links:
        raise RuntimeError("NO_TRUSTED_NODE")
    candidate = solve_shared_root(
        selection.trusted_links, anchors_m=anchors_m,
        initial_root_m=initial, root_velocity_mps=velocity)
    if not candidate.success or candidate.rank != 3 or not math.isfinite(candidate.condition):
        raise RuntimeError(f"ROOT_CANDIDATE_{candidate.reason}")
    minimum_std_m = adaptive_root_minimum_std_m(len(selection.trusted_nodes))
    covariance_m2 = np.eye(3, dtype=float) * minimum_std_m ** 2
    observation = PositionObservation(
        measurement_time_s=measurement_s, availability_time_s=availability_s,
        root_position_m=candidate.root_position_m,
        covariance_m2=covariance_m2,
        tag_id="C2_SHARED_ROOT_DIAGNOSTIC", anchors=candidate.anchors_used,
        quality_state="DIAGNOSTIC_NODE_COUNT_FLOOR_ONLY_NOT_CALIBRATED_R",
        source_sequence=int(source_sequence))
    transaction = execute_causal_update_transaction(
        root=root, observation=observation, kind=CandidateKind.ROOT_POSITION,
        nominal_envelope=nominal_envelope)
    return OfflineU3GroupResult(
        measurement_s, availability_s, audits, selection, candidate,
        covariance_m2, minimum_std_m, transaction, 1, 1)


def validate_epoch_cadence(measurement_times_ns: Sequence[float]) -> None:
    values = np.asarray(measurement_times_ns, dtype=float)
    if values.ndim != 1 or len(values) < 2 or not np.isfinite(values).all():
        raise ValueError("invalid epoch timeline")
    bucket = np.rint(values / EPOCH_PERIOD_NS).astype(np.int64)
    if np.any(np.diff(bucket) != 1):
        raise ValueError("UWB groups are not contiguous 8.33 Hz epochs")
