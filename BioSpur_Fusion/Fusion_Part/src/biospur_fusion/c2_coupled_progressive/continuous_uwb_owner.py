"""Sole chronological owner for continuous Capture2 calibration events."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .continuous_frontend import (
    ActionInterval,
    ActionBoundary,
    CAPTURE2_PROTOCOL_SLOTS,
    ContinuousClockOwner,
    ContinuousEvent,
    ProtocolMarker,
    SourceBoundGap,
    validate_action_intervals,
    validate_event_clock,
    validate_source_gaps,
)


CONTINUOUS_UWB_OWNER_SCHEMA = "biospur.c2.continuous_uwb_owner.v1"


@dataclass(frozen=True)
class SubownerResult:
    fixed_parameter_changed: bool
    time_varying_state_changed: bool
    gap_covariance_grown: bool = False
    interpolated: bool = False
    state_reset: bool = False
    action_specific_prior_used: bool = False


@dataclass(frozen=True)
class PreparedSubownerUpdate:
    result: SubownerResult
    token: object


class ContinuousSubowner(Protocol):
    def clone(self) -> "ContinuousSubowner": ...

    def mutable_owner_tokens(self) -> frozenset[int]: ...

    def continuous_snapshot(self) -> object: ...

    def prepare_continuous_event(
        self, event: ContinuousEvent, *, commit_uwb: bool,
    ) -> PreparedSubownerUpdate: ...

    def commit_continuous_event(self, prepared: PreparedSubownerUpdate) -> None: ...

    def restore_continuous_snapshot(self, snapshot: object) -> None: ...


@dataclass(frozen=True)
class ContinuousOwnerSnapshot:
    schema: str
    action_index: int
    action_id: str | None
    action_intervals: tuple[ActionInterval, ...]
    source_gaps: tuple[SourceBoundGap, ...]
    action_boundaries: tuple[ActionBoundary, ...]
    protocol_markers: tuple[ProtocolMarker, ...]
    event_count: int
    fixed_parameter_revision: int
    time_varying_state_revision: int
    owner_revision: int
    last_availability_global_ns: int | None
    protocol_marker_and_gap_accounted: bool
    uwb_commit_enabled: bool


@dataclass(frozen=True)
class ContinuousUwbForkPair:
    without_uwb_commits: "Capture2ContinuousUwbCalibrationOwner"
    with_uwb_commits: "Capture2ContinuousUwbCalibrationOwner"


class Capture2ContinuousUwbCalibrationOwner:
    """Owns chronology while delegating estimator behavior to existing owners."""

    def __init__(
        self,
        *,
        clock_owner: ContinuousClockOwner,
        action_intervals: tuple[ActionInterval, ...],
        source_gaps: tuple[SourceBoundGap, ...],
        subowners: tuple[tuple[str, ContinuousSubowner], ...],
        uwb_commit_enabled: bool = True,
        fork_origin: str = "capture2-pre-00",
    ) -> None:
        names = tuple(name for name, _owner in subowners)
        if not names or any(not name for name in names) or len(names) != len(set(names)):
            raise ValueError("continuous subowners must be nonempty and uniquely named")
        validate_action_intervals(action_intervals)
        validate_source_gaps(source_gaps, action_intervals)
        self._clock_owner = clock_owner
        self._action_intervals = tuple(action_intervals)
        self._interval_by_index = {row.action_index: row for row in action_intervals}
        self._source_gaps = tuple(source_gaps)
        self._gap_by_id = {row.region_id: row for row in source_gaps}
        self._subowners = tuple(subowners)
        self._declared_mutable_tokens(self._subowners)
        self._uwb_commit_enabled = bool(uwb_commit_enabled)
        self._fork_origin = str(fork_origin)
        self._action_index = -1
        self._boundaries: list[ActionBoundary] = []
        self._protocol_markers: list[ProtocolMarker] = []
        self._event_ids: set[str] = set()
        self._event_count = 0
        self._fixed_revision = 0
        self._state_revision = 0
        self._owner_revision = 0
        self._last_boundary_common_ns: int | None = None
        self._last_boundary_availability_ns: int | None = None
        self._last_dispatch_key: tuple[int, int, int, str, str] | None = None
        self._timer_cursors: dict[tuple[str, str], int] = {}
        self._accounted_gap_regions: set[str] = set()

    @property
    def subowners(self) -> tuple[tuple[str, ContinuousSubowner], ...]:
        return self._subowners

    def snapshot(self) -> ContinuousOwnerSnapshot:
        return ContinuousOwnerSnapshot(
            CONTINUOUS_UWB_OWNER_SCHEMA,
            self._action_index,
            None if self._action_index < 0 else CAPTURE2_PROTOCOL_SLOTS[self._action_index].action_id,
            self._action_intervals,
            self._source_gaps,
            tuple(self._boundaries),
            tuple(self._protocol_markers),
            self._event_count,
            self._fixed_revision,
            self._state_revision,
            self._owner_revision,
            None if self._last_dispatch_key is None else self._last_dispatch_key[0],
            set(self._gap_by_id) == self._accounted_gap_regions,
            self._uwb_commit_enabled,
        )

    @staticmethod
    def _declared_mutable_tokens(
        subowners: tuple[tuple[str, ContinuousSubowner], ...],
    ) -> frozenset[int]:
        combined: set[int] = set()
        for _name, owner in subowners:
            tokens = owner.mutable_owner_tokens()
            if (
                type(tokens) is not frozenset
                or not tokens
                or any(type(token) is not int or token <= 0 for token in tokens)
                or combined.intersection(tokens)
            ):
                raise ValueError("mutable subowner identity/token set is invalid or aliased")
            combined.update(tokens)
        return frozenset(combined)

    def _validate_boundary_chronology(self, common_ns: int, availability_ns: int) -> None:
        if type(common_ns) is not int or type(availability_ns) is not int:
            raise ValueError("continuous chronology requires integer global ns")
        if common_ns < 0 or availability_ns < common_ns:
            raise ValueError("continuous availability precedes event time")
        if self._last_boundary_common_ns is not None and (
            common_ns <= self._last_boundary_common_ns
            or availability_ns <= self._last_boundary_availability_ns
        ):
            raise ValueError("action boundary time/availability reversed")

    def _source_cursor(self, event: ContinuousEvent) -> tuple[tuple[str, str], int] | None:
        if event.kind == "IMU":
            return (event.node_id, "IMU_TRIGGER"), event.imu_timer2.trigger_timer2_us
        if event.kind == "UWB":
            return (event.node_id, "UWB_STROBE"), event.uwb_timer2.strobe_timer2_us
        return None

    def _validate_event_chronology(self, event: ContinuousEvent) -> None:
        if self._last_dispatch_key is not None and event.dispatch_key <= self._last_dispatch_key:
            raise ValueError("event availability/tie-break order reversed or duplicated")
        cursor = self._source_cursor(event)
        if cursor is not None:
            key, tick = cursor
            if key in self._timer_cursors and tick <= self._timer_cursors[key]:
                raise ValueError("per-node source TIMER2 cursor reversed or duplicated")

    def enter_action(
        self, action_id: str, *, common_global_ns: int,
        availability_global_ns: int, host_time_label: str = "",
    ) -> ActionBoundary | None:
        expected_index = self._action_index + 1
        if expected_index >= len(CAPTURE2_PROTOCOL_SLOTS):
            raise ValueError("duplicate action or action after manifest end")
        expected = CAPTURE2_PROTOCOL_SLOTS[expected_index]
        if action_id != expected.action_id:
            raise ValueError("missing, duplicate, or reordered physical action")
        expected_boundary_ns = (
            self._interval_by_index[0].end_common_global_ns
            if expected_index == 1
            else self._interval_by_index[expected_index].start_common_global_ns
        )
        if common_global_ns != expected_boundary_ns:
            raise ValueError("action boundary does not match frozen interval start")
        self._validate_boundary_chronology(common_global_ns, availability_global_ns)
        if (
            self._last_dispatch_key is not None
            and availability_global_ns < self._last_dispatch_key[0]
        ):
            raise ValueError("action boundary registered behind dispatched availability")
        boundary = None
        if expected_index:
            boundary = ActionBoundary(
                expected_index,
                CAPTURE2_PROTOCOL_SLOTS[expected_index - 1].action_id,
                action_id,
                common_global_ns,
                availability_global_ns,
                host_time_label,
            )
            self._boundaries.append(boundary)
        self._action_index = expected_index
        if expected_index == 1:
            self._protocol_markers.append(ProtocolMarker(
                1, "01_neutral_sway", "OPERATOR_SKIPPED_EQUIVALENT_TO_00",
            ))
        self._last_boundary_common_ns = common_global_ns
        self._last_boundary_availability_ns = availability_global_ns
        self._owner_revision += 1
        return boundary

    def ingest(self, event: ContinuousEvent) -> tuple[SubownerResult, ...]:
        if self._action_index < 0:
            raise ValueError("continuous session must enter action 00 first")
        gap = None
        if event.region_id is None:
            if event.action_index > self._action_index:
                raise ValueError("event belongs to a missing/reordered action")
            if not CAPTURE2_PROTOCOL_SLOTS[event.action_index].acquired:
                raise ValueError("unacquired protocol slot owns no sensor or gap event")
            if not self._interval_by_index[event.action_index].contains(event.common_global_ns):
                raise ValueError("event label does not own its measurement/common-global time")
            if event.action_index < self._action_index and event.kind != "UWB":
                raise ValueError("only delayed prior-action UWB delivery is allowed")
        else:
            gap = self._gap_by_id.get(event.region_id)
            if gap is None:
                raise ValueError("event names an unknown source-bound gap")
            if event.kind == "GAP":
                if (
                    event.gap_start_global_ns != gap.start_common_global_ns
                    or event.common_global_ns != gap.end_common_global_ns
                ):
                    raise ValueError("elapsed-only event must account for the complete gap")
            elif not gap.contains(event.common_global_ns):
                raise ValueError("gap region does not own the sensor measurement time")
        if event.event_id in self._event_ids:
            raise ValueError("duplicate continuous event identity")
        validate_event_clock(event, self._clock_owner)
        self._validate_event_chronology(event)

        commit_uwb = self._uwb_commit_enabled if event.kind == "UWB" else False
        snapshots = tuple(owner.continuous_snapshot() for _name, owner in self._subowners)
        try:
            prepared = tuple(
                owner.prepare_continuous_event(event, commit_uwb=commit_uwb)
                for _name, owner in self._subowners
            )
            if any(type(item) is not PreparedSubownerUpdate for item in prepared):
                raise TypeError("continuous subowner returned an invalid preparation")
            results = tuple(item.result for item in prepared)
            if any(type(result) is not SubownerResult for result in results):
                raise TypeError("continuous subowner returned an invalid result")
            if any(result.interpolated or result.state_reset for result in results):
                raise ValueError("continuous subowner interpolated or reset persistent state")
            if gap is not None and any(
                result.fixed_parameter_changed or result.action_specific_prior_used
                for result in results
            ):
                raise ValueError("gap event cannot mutate fixed state or use action priors")
            if event.kind == "GAP" and any(
                result != SubownerResult(
                    fixed_parameter_changed=False,
                    time_varying_state_changed=True,
                    gap_covariance_grown=True,
                    interpolated=False,
                    state_reset=False,
                    action_specific_prior_used=False,
                )
                for result in results
            ):
                raise ValueError("gap must be time-varying covariance growth in every subowner")
            for (_name, owner), item in zip(self._subowners, prepared):
                owner.commit_continuous_event(item)
        except Exception:
            for (_name, owner), snapshot in reversed(tuple(zip(self._subowners, snapshots))):
                owner.restore_continuous_snapshot(snapshot)
            raise

        self._event_ids.add(event.event_id)
        self._event_count += 1
        self._last_dispatch_key = event.dispatch_key
        if gap is not None and event.kind == "GAP":
            self._accounted_gap_regions.add(gap.region_id)
        cursor = self._source_cursor(event)
        if cursor is not None:
            self._timer_cursors[cursor[0]] = cursor[1]
        if any(result.fixed_parameter_changed for result in results):
            self._fixed_revision += 1
        if any(result.time_varying_state_changed for result in results):
            self._state_revision += 1
        self._owner_revision += 1
        return results

    def finish(self) -> ContinuousOwnerSnapshot:
        if (
            self._action_index != 19
            or len(self._boundaries) != 19
            or self._protocol_markers != [ProtocolMarker(
                1, "01_neutral_sway", "OPERATOR_SKIPPED_EQUIVALENT_TO_00",
            )]
            or set(self._gap_by_id) != self._accounted_gap_regions
        ):
            raise ValueError("continuous session is missing physical actions/boundaries")
        return self.snapshot()

    def fork_ab(self) -> ContinuousUwbForkPair:
        if self._action_index != -1 or self._event_count or self._boundaries:
            raise ValueError("A/B fork is allowed only from one pre-00 state")

        seed_tokens = self._declared_mutable_tokens(self._subowners)

        def branch(commit: bool) -> Capture2ContinuousUwbCalibrationOwner:
            clones: list[tuple[str, ContinuousSubowner]] = []
            for name, owner in self._subowners:
                cloned = owner.clone()
                if cloned is owner:
                    raise ValueError("subowner clone aliases pre-00 state")
                clones.append((name, cloned))
            return Capture2ContinuousUwbCalibrationOwner(
                clock_owner=self._clock_owner,
                action_intervals=self._action_intervals,
                source_gaps=self._source_gaps,
                subowners=tuple(clones),
                uwb_commit_enabled=commit,
                fork_origin=self._fork_origin,
            )

        without = branch(False)
        with_uwb = branch(True)
        without_tokens = self._declared_mutable_tokens(without._subowners)
        with_tokens = self._declared_mutable_tokens(with_uwb._subowners)
        if (
            seed_tokens.intersection(without_tokens)
            or seed_tokens.intersection(with_tokens)
            or without_tokens.intersection(with_tokens)
        ):
            raise ValueError("seed/A/B branches share nested mutable ownership")
        return ContinuousUwbForkPair(without, with_uwb)
