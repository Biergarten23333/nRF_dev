#!/usr/bin/env python3
"""Reboot-aware host classification for tag-owned sweep counters.

The tag-owned public sweep counter legitimately restarts when the tag
application boots.  A B306 may remain powered across that event and retain an
older ``last_sweep`` value, so its legacy telemetry can report a run of
``reorder`` values and one ``duplicate`` while the new tag counter catches up.

This module keeps that legacy device telemetry visible, but gives the PC data
path an independent generation-aware verdict.  A backward counter move is an
expected REBASE only when one of these facts is present:

* the host has recorded an explicit tag boot/join event; or
* the B306 node uptime itself moved backward, proving a node reboot.

Without one of those facts, the same backward move remains a reorder.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


UINT32_MODULUS = 1 << 32
UINT32_HALF_RANGE = 1 << 31


@dataclass
class SweepCounterState:
    last_sweep: int | None = None
    last_node_uptime_ms: int | None = None
    pending_rebase_reason: str | None = None
    generation: int = 0
    records: int = 0
    rebases: int = 0
    gaps: int = 0
    missing_sweeps: int = 0
    duplicates: int = 0
    reorders: int = 0
    events: list[dict[str, Any]] = field(default_factory=list)


class RebootAwareSweepCounter:
    """Classify per-node uint32 sweep counters without hiding real reorders."""

    def __init__(self) -> None:
        self._states: dict[str, SweepCounterState] = {}

    def _state(self, name: str) -> SweepCounterState:
        normalized = name.upper()
        return self._states.setdefault(normalized, SweepCounterState())

    def note_tag_boot_or_join(self, name: str, reason: str) -> None:
        """Arm exactly one expected rebase for the next record from ``name``."""
        if not reason.strip():
            raise ValueError("rebase reason must be non-empty")
        state = self._state(name)
        state.pending_rebase_reason = reason.strip()
        state.events.append(
            {
                "kind": "BOOT_OR_JOIN",
                "reason": reason.strip(),
                "after_sweep": state.last_sweep,
            }
        )

    def observe(
        self, name: str, sweep: int, node_uptime_ms: int | None = None
    ) -> str:
        if not 0 <= sweep < UINT32_MODULUS:
            raise ValueError(f"sweep outside uint32: {sweep}")
        if node_uptime_ms is not None and node_uptime_ms < 0:
            raise ValueError(f"negative node uptime: {node_uptime_ms}")

        state = self._state(name)
        uptime_restarted = (
            node_uptime_ms is not None
            and state.last_node_uptime_ms is not None
            and node_uptime_ms < state.last_node_uptime_ms
        )
        if uptime_restarted and state.pending_rebase_reason is None:
            state.pending_rebase_reason = "B306 node_uptime_ms restarted"

        if state.pending_rebase_reason is not None:
            reason = state.pending_rebase_reason
            state.pending_rebase_reason = None
            state.generation += 1
            state.rebases += 1
            state.records += 1
            state.last_sweep = sweep
            state.last_node_uptime_ms = node_uptime_ms
            state.events.append(
                {
                    "kind": "REBASE",
                    "reason": reason,
                    "sweep": sweep,
                    "node_uptime_ms": node_uptime_ms,
                    "generation": state.generation,
                }
            )
            return "REBASE"

        if state.last_sweep is None:
            state.records += 1
            state.last_sweep = sweep
            state.last_node_uptime_ms = node_uptime_ms
            return "BASELINE"

        delta = (sweep - state.last_sweep) & 0xFFFFFFFF
        if delta == 0:
            state.duplicates += 1
            verdict = "DUPLICATE"
        elif delta < UINT32_HALF_RANGE:
            if delta > 1:
                state.gaps += 1
                state.missing_sweeps += delta - 1
                verdict = "GAP"
            else:
                verdict = "FORWARD"
            state.last_sweep = sweep
        else:
            # A backward value without independent boot/join evidence is a
            # real reorder.  Do not move the high-water mark backward.
            state.reorders += 1
            verdict = "REORDER"

        state.records += 1
        state.last_node_uptime_ms = node_uptime_ms
        return verdict

    def snapshot(self) -> dict[str, dict[str, Any]]:
        return {
            name: asdict(state)
            for name, state in sorted(self._states.items())
        }


def reclassify_legacy_b306_delta(
    *,
    raw_reorder_delta: int,
    raw_duplicate_delta: int,
    host_state: dict[str, Any],
    qualifying_boot_or_join: bool,
) -> dict[str, Any]:
    """Separate legacy B306 catch-up debt from host-observed anomalies.

    The raw counters are never discarded.  They are reclassified only when an
    independently recorded boot/join exists and the PC-decoded sweep stream
    itself has no reorder or duplicate.
    """
    if raw_reorder_delta < 0 or raw_duplicate_delta < 0:
        raise ValueError("counter deltas must be non-negative")
    host_reorders = int(host_state.get("reorders", 0))
    host_duplicates = int(host_state.get("duplicates", 0))
    host_rebases = int(host_state.get("rebases", 0))
    expected = (
        qualifying_boot_or_join
        and host_rebases >= 1
        and host_reorders == 0
        and host_duplicates == 0
    )
    raw_anomaly = bool(raw_reorder_delta or raw_duplicate_delta)
    return {
        "raw_b306_reorder_delta": raw_reorder_delta,
        "raw_b306_duplicate_delta": raw_duplicate_delta,
        "classification": (
            "CLEAN"
            if not raw_anomaly
            else "EXPECTED_REBASE_DEBT"
            if expected
            else "ANOMALY"
        ),
        "effective_reorder": (
            0 if expected and raw_anomaly else raw_reorder_delta
        ),
        "effective_duplicate": (
            0 if expected and raw_anomaly else raw_duplicate_delta
        ),
        "host_rebases": host_rebases,
        "host_reorders": host_reorders,
        "host_duplicates": host_duplicates,
    }
