"""Source-bound inventory and metrics for the continuous Capture2 root A/B run.

This module is deliberately independent of raw decoding and estimator state.
It owns the 19 acquired byte/time contracts, the 18 intervening regions, and
the inert 20-slot protocol-label ledger used by the full runner.
"""
from __future__ import annotations

from dataclasses import dataclass
import bisect
import hashlib
import json
import math
from pathlib import Path
import re
from typing import Iterable

import numpy as np

from biospur_fusion.c2_coupled_progressive.continuous_frontend import (
    CAPTURE2_PROTOCOL_SLOTS,
)


FULL_SESSION_SCHEMA = "biospur.c2.continuous_root_ab.full.v1"
FULL_SESSION_START_OFFSET = 213_648_544
FULL_SESSION_STOP_OFFSET = 294_033_922
FULL_SESSION_BYTE_COUNT = 80_385_378
ACTION_AUDIT_RELATIVE = Path(
    "logs/c2_five_node_pure_imu_v2_20260906_102300/"
    "CALIBRATION_INPUT_AUDIT.json"
)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class ContinuousRegion:
    ordinal: int
    region_id: str
    kind: str
    start_offset: int
    stop_offset: int
    start_ns: int
    stop_ns: int
    action_index: int | None
    action_id: str | None
    expected_sha256: str | None

    def __post_init__(self) -> None:
        if type(self.ordinal) is not int or self.ordinal < 0:
            raise ValueError("invalid continuous-region ordinal")
        if self.kind not in {"ACTION", "INTER_ACTION_GAP"}:
            raise ValueError("invalid continuous-region kind")
        if not self.region_id:
            raise ValueError("continuous region lacks identity")
        if (
            type(self.start_offset) is not int
            or type(self.stop_offset) is not int
            or self.start_offset < 0
            or self.stop_offset <= self.start_offset
        ):
            raise ValueError("invalid continuous-region byte interval")
        if (
            type(self.start_ns) is not int
            or type(self.stop_ns) is not int
            or self.start_ns < 0
            or self.stop_ns <= self.start_ns
        ):
            raise ValueError("invalid continuous-region time interval")
        if self.kind == "ACTION":
            if type(self.action_index) is not int or self.action_id is None:
                raise ValueError("action region lacks protocol identity")
            slot = CAPTURE2_PROTOCOL_SLOTS[self.action_index]
            if not slot.acquired or slot.action_id != self.action_id:
                raise ValueError("action region does not match acquired protocol slot")
            if self.expected_sha256 is None or _SHA256_RE.fullmatch(self.expected_sha256) is None:
                raise ValueError("action region lacks its authenticated slice SHA-256")
        elif any(value is not None for value in (
            self.action_index, self.action_id, self.expected_sha256,
        )):
            raise ValueError("gap region cannot claim action payload ownership")

    @property
    def byte_count(self) -> int:
        return self.stop_offset - self.start_offset

    def contains_ns(self, time_ns: int, *, final: bool = False) -> bool:
        if type(time_ns) is not int:
            return False
        if final:
            return self.start_ns <= time_ns <= self.stop_ns
        return self.start_ns <= time_ns < self.stop_ns


@dataclass(frozen=True)
class ProtocolLabel:
    index: int
    action_id: str
    marker_only: bool
    enter_ns: int


@dataclass(frozen=True)
class ContinuousSessionInventory:
    source_audit: Path
    source_audit_sha256: str
    regions: tuple[ContinuousRegion, ...]
    labels: tuple[ProtocolLabel, ...]

    def __post_init__(self) -> None:
        if _SHA256_RE.fullmatch(self.source_audit_sha256) is None:
            raise ValueError("invalid action-audit SHA-256")
        if len(self.regions) != 37:
            raise ValueError("continuous Capture2 run requires 19 actions plus 18 gaps")
        if len(self.labels) != 20:
            raise ValueError("continuous Capture2 run requires all 20 protocol labels")
        if self.regions[0].start_offset != FULL_SESSION_START_OFFSET:
            raise ValueError("continuous session start offset changed")
        if self.regions[-1].stop_offset != FULL_SESSION_STOP_OFFSET:
            raise ValueError("continuous session stop offset changed")
        if sum(row.byte_count for row in self.regions) != FULL_SESSION_BYTE_COUNT:
            raise ValueError("continuous session byte count changed")
        for expected, row in enumerate(self.regions):
            if row.ordinal != expected:
                raise ValueError("continuous regions are not canonically ordered")
        for left, right in zip(self.regions, self.regions[1:]):
            if left.stop_offset != right.start_offset:
                raise ValueError("continuous regions have a byte gap or overlap")
            if left.stop_ns != right.start_ns:
                raise ValueError("continuous regions have a time gap or overlap")
        if tuple(label.index for label in self.labels) != tuple(range(20)):
            raise ValueError("protocol labels are missing, duplicated, or reordered")
        if sum(label.marker_only for label in self.labels) != 1:
            raise ValueError("continuous protocol requires exactly one marker-only label")
        marker = self.labels[1]
        if marker.action_id != "01_neutral_sway" or not marker.marker_only:
            raise ValueError("slot 01 must remain the operator-skipped marker")

    @property
    def start_offset(self) -> int:
        return self.regions[0].start_offset

    @property
    def stop_offset(self) -> int:
        return self.regions[-1].stop_offset

    @property
    def start_ns(self) -> int:
        return self.regions[0].start_ns

    @property
    def stop_ns(self) -> int:
        return self.regions[-1].stop_ns

    def region_index_for_ns(self, time_ns: int) -> int | None:
        starts = tuple(row.start_ns for row in self.regions)
        index = bisect.bisect_right(starts, time_ns) - 1
        if index < 0 or index >= len(self.regions):
            return None
        return index if self.regions[index].contains_ns(
            time_ns, final=index == len(self.regions) - 1,
        ) else None


def _event_identity(time_ns: int, sequence: int, sweep: int) -> dict[str, int]:
    return {"time_ns": int(time_ns), "sequence": int(sequence), "sweep": int(sweep)}


class ContinuousUwbInstrumentation:
    """Observe committed UWB transactions and fail-closed recovery lifecycle."""

    def __init__(self, *, maximum_committed_correction_m: float) -> None:
        if not math.isfinite(maximum_committed_correction_m) or maximum_committed_correction_m <= 0.0:
            raise ValueError("UWB instrumentation requires a positive correction cap")
        self.maximum_committed_correction_m = float(maximum_committed_correction_m)
        self.transactions = 0
        self.committed = 0
        self.solver_accepted = 0
        self.maximum_committed: dict[str, object] | None = None
        self.episodes: list[dict[str, object]] = []
        self._open: dict[str, object] | None = None

    def _reset_open(self, event: dict[str, int], reason: str) -> None:
        if self._open is None:
            return
        self._open["reset_events"] = int(self._open["reset_events"]) + 1
        self._open["last_good_count"] = 0
        self._open["last_reset_event"] = event
        self._open["last_reset_reason"] = reason

    def observe(
        self, *, time_ns: int, sequence: int, sweep: int, committed: bool,
        reason: str, solver_accepted: bool, correction_norm_m: float,
        recovery_good_events: int, recovery_required_events: int,
    ) -> None:
        if (
            type(committed) is not bool or type(solver_accepted) is not bool or not reason
            or not math.isfinite(correction_norm_m) or correction_norm_m < 0.0
            or recovery_good_events < 0 or recovery_required_events < 1
        ):
            raise ValueError("invalid continuous UWB instrumentation event")
        event = _event_identity(time_ns, sequence, sweep)
        self.transactions += 1
        self.solver_accepted += int(solver_accepted)
        is_recovery = reason.startswith("REJECT_PROPOSED_STATE_UWB_RECOVERY_")
        if is_recovery:
            if self._open is not None and recovery_good_events <= int(self._open["last_good_count"]):
                self._reset_open(event, "RECOVERY_SEQUENCE_RESTARTED")
            if self._open is None:
                self._open = {
                    "start_event": event,
                    "required_good_events": int(recovery_required_events),
                    "solver_success_rows": 0,
                    "transaction_rejected_rows": 0,
                    "reset_events": 0,
                    "last_good_count": 0,
                    "outcome": "OPEN",
                }
            self._open["solver_success_rows"] = int(self._open["solver_success_rows"]) + int(solver_accepted)
            self._open["transaction_rejected_rows"] = int(self._open["transaction_rejected_rows"]) + 1
            self._open["last_good_count"] = int(recovery_good_events)
            return
        if committed:
            if correction_norm_m > self.maximum_committed_correction_m + 1e-12:
                raise RuntimeError("committed UWB correction exceeded the owned invariant")
            self.committed += 1
            if self.maximum_committed is None or correction_norm_m > float(self.maximum_committed["norm_m"]):
                self.maximum_committed = {"norm_m": float(correction_norm_m), "event": event}
            if self._open is not None:
                self._open["solver_success_rows"] = int(self._open["solver_success_rows"]) + int(solver_accepted)
                self._open["last_good_count"] = int(recovery_good_events)
                self._open["outcome"] = "COMPLETED"
                self._open["terminal_event"] = event
                self._open["terminal_reason"] = reason
                self.episodes.append(self._open)
                self._open = None
            return
        if self._open is not None:
            self._open["transaction_rejected_rows"] = int(self._open["transaction_rejected_rows"]) + 1
            self._reset_open(event, reason)

    def summary(self) -> dict[str, object]:
        completed = sum(row["outcome"] == "COMPLETED" for row in self.episodes)
        return {
            "transactions": self.transactions,
            "solver_accepted": self.solver_accepted,
            "committed": self.committed,
            "rejected": self.transactions - self.committed,
            "acceptance_ratio": float(self.committed / self.transactions) if self.transactions else 0.0,
            "maximum_committed_correction": self.maximum_committed,
            "committed_correction_limit_m": self.maximum_committed_correction_m,
            "recovery": {
                "started": len(self.episodes) + int(self._open is not None),
                "completed": completed,
                "open_at_end": self._open,
                "closed_episodes": self.episodes,
            },
        }


def maximum_adjacent_position_jump(positions_m: np.ndarray, times_ns: np.ndarray) -> dict[str, object]:
    positions = np.asarray(positions_m, dtype=float)
    times = np.asarray(times_ns)
    if (
        positions.ndim != 2 or positions.shape[1] != 3 or positions.shape[0] < 2
        or times.shape != (positions.shape[0],) or not np.isfinite(positions).all()
        or not np.issubdtype(times.dtype, np.integer) or np.any(np.diff(times) <= 0)
    ):
        raise ValueError("adjacent jump requires ordered integer-nanosecond Nx3 states")
    jumps = np.linalg.norm(np.diff(positions, axis=0), axis=1)
    before = int(np.argmax(jumps))
    return {
        "norm_m": float(jumps[before]),
        "before_index": before,
        "after_index": before + 1,
        "before_time_ns": int(times[before]),
        "after_time_ns": int(times[before + 1]),
    }


def evaluate_anti_drift_gates(
    *, branch_a: dict[str, object], branch_b: dict[str, object],
    transactions: dict[str, object], maximum_adjacent_jump: dict[str, object],
) -> dict[str, object]:
    """Evaluate fixed diagnostic delivery gates without altering estimation."""
    a_endpoint = float(branch_a["endpoint_displacement_m"])
    b_endpoint = float(branch_b["endpoint_displacement_m"])
    correction = transactions["maximum_committed_correction"]
    correction_norm = 0.0 if correction is None else float(correction["norm_m"])
    correction_limit = float(transactions["committed_correction_limit_m"])
    recovery = transactions["recovery"]
    gates = {
        "branch_b_endpoint_lt_1m": {"value": b_endpoint, "limit": 1.0, "passed": b_endpoint < 1.0},
        "branch_b_rms_lt_1m": {"value": float(branch_b["rms_displacement_m"]), "limit": 1.0, "passed": float(branch_b["rms_displacement_m"]) < 1.0},
        "branch_b_maximum_lt_5m": {"value": float(branch_b["maximum_displacement_m"]), "limit": 5.0, "passed": float(branch_b["maximum_displacement_m"]) < 5.0},
        "branch_b_endpoint_lt_1pct_branch_a": {"value": b_endpoint, "limit": 0.01 * a_endpoint, "passed": b_endpoint < 0.01 * a_endpoint},
        "branch_b_maximum_adjacent_jump_lt_0p10m": {"value": float(maximum_adjacent_jump["norm_m"]), "limit": 0.10, "passed": float(maximum_adjacent_jump["norm_m"]) < 0.10},
        "recovery_lifecycle_closed": {
            "started": int(recovery["started"]), "completed": int(recovery["completed"]),
            "open_at_end": recovery["open_at_end"],
            "passed": recovery["open_at_end"] is None and int(recovery["started"]) == int(recovery["completed"]),
        },
        "committed_correction_invariant": {"value": correction_norm, "limit": correction_limit, "passed": correction_norm <= correction_limit + 1e-12},
    }
    return {"passed": all(bool(row["passed"]) for row in gates.values()), "gates": gates}


def _seconds_to_ns(value: object) -> int:
    seconds = float(value)
    if not math.isfinite(seconds) or seconds < 0.0:
        raise ValueError("action audit contains an invalid common-global time")
    return int(round(seconds * 1e9))


def load_continuous_session_inventory(root: Path) -> ContinuousSessionInventory:
    audit_path = root / ACTION_AUDIT_RELATIVE
    payload = audit_path.read_bytes()
    audit = json.loads(payload)
    contracts = audit.get("contracts")
    if not isinstance(contracts, dict):
        raise ValueError("action audit lacks contracts")
    acquired = tuple(slot for slot in CAPTURE2_PROTOCOL_SLOTS if slot.acquired)
    if tuple(contracts) != tuple(slot.action_id for slot in acquired):
        raise ValueError("action audit inventory differs from the protocol owner")

    actions: list[ContinuousRegion] = []
    for slot in acquired:
        row = contracts[slot.action_id]
        actions.append(ContinuousRegion(
            ordinal=0,
            region_id=slot.action_id,
            kind="ACTION",
            start_offset=int(row["start"]),
            stop_offset=int(row["stop"]),
            start_ns=_seconds_to_ns(row["lo"]),
            stop_ns=_seconds_to_ns(row["hi"]),
            action_index=slot.index,
            action_id=slot.action_id,
            expected_sha256=str(row["slice_sha256"]),
        ))

    regions: list[ContinuousRegion] = []
    for index, action in enumerate(actions):
        regions.append(action)
        if index + 1 < len(actions):
            following = actions[index + 1]
            regions.append(ContinuousRegion(
                ordinal=0,
                region_id=f"GAP_{action.action_id}_TO_{following.action_id}",
                kind="INTER_ACTION_GAP",
                start_offset=action.stop_offset,
                stop_offset=following.start_offset,
                start_ns=action.stop_ns,
                stop_ns=following.start_ns,
                action_index=None,
                action_id=None,
                expected_sha256=None,
            ))
    regions = [ContinuousRegion(
        ordinal=index,
        region_id=row.region_id,
        kind=row.kind,
        start_offset=row.start_offset,
        stop_offset=row.stop_offset,
        start_ns=row.start_ns,
        stop_ns=row.stop_ns,
        action_index=row.action_index,
        action_id=row.action_id,
        expected_sha256=row.expected_sha256,
    ) for index, row in enumerate(regions)]

    action_by_index = {row.action_index: row for row in actions}
    labels: list[ProtocolLabel] = []
    for slot in CAPTURE2_PROTOCOL_SLOTS:
        if slot.index == 1:
            enter_ns = action_by_index[0].stop_ns
        else:
            enter_ns = action_by_index[slot.index].start_ns
        labels.append(ProtocolLabel(
            slot.index, slot.action_id, not slot.acquired, enter_ns,
        ))
    return ContinuousSessionInventory(
        source_audit=audit_path,
        source_audit_sha256=hashlib.sha256(payload).hexdigest(),
        regions=tuple(regions),
        labels=tuple(labels),
    )


def summarize_root_trajectory(
    positions_m: np.ndarray,
    velocities_mps: np.ndarray,
    anchors_m: np.ndarray,
) -> dict[str, object]:
    positions = np.asarray(positions_m, dtype=float)
    velocities = np.asarray(velocities_mps, dtype=float)
    anchors = np.asarray(anchors_m, dtype=float)
    if (
        positions.ndim != 2 or positions.shape[1] != 3 or positions.shape[0] == 0
        or velocities.shape != positions.shape or anchors.shape != (8, 3)
        or not np.isfinite(positions).all() or not np.isfinite(velocities).all()
        or not np.isfinite(anchors).all()
    ):
        raise ValueError("root trajectory summary requires finite Nx3 states and 8 anchors")
    displacement = positions - positions[0]
    displacement_norm = np.linalg.norm(displacement, axis=1)
    speeds = np.linalg.norm(velocities, axis=1)
    lower, upper = anchors.min(axis=0), anchors.max(axis=0)
    outside = np.any((positions < lower) | (positions > upper), axis=1)
    jumps = np.linalg.norm(np.diff(positions, axis=0), axis=1)
    return {
        "samples": int(positions.shape[0]),
        "endpoint_displacement_m": float(displacement_norm[-1]),
        "maximum_displacement_m": float(np.max(displacement_norm)),
        "rms_displacement_m": float(np.sqrt(np.mean(np.square(displacement_norm)))),
        "maximum_speed_mps": float(np.max(speeds)),
        "rms_speed_mps": float(np.sqrt(np.mean(np.square(speeds)))),
        "z_min_m": float(np.min(positions[:, 2])),
        "z_max_m": float(np.max(positions[:, 2])),
        "z_excursion_m": float(np.ptp(positions[:, 2])),
        "outside_anchor_volume_fraction": float(np.mean(outside)),
        "maximum_adjacent_position_jump_m": float(np.max(jumps)) if jumps.size else 0.0,
    }


def required_result_keys() -> tuple[str, ...]:
    return (
        "schema", "status", "scientific_pass", "source", "inventory",
        "event_conservation", "admission_reasons", "branch_a", "branch_b",
        "uwb_transactions", "anti_drift_gates", "vqf", "merge", "regions",
        "protocol_labels", "runtime",
    )


def validate_result_schema(result: dict[str, object]) -> None:
    missing = tuple(key for key in required_result_keys() if key not in result)
    if missing:
        raise ValueError(f"full-session result lacks required fields: {missing}")
    if result["schema"] != FULL_SESSION_SCHEMA:
        raise ValueError("full-session result schema changed")
    if result["scientific_pass"] is not False:
        raise ValueError("root-only diagnostic cannot claim scientific pass")
