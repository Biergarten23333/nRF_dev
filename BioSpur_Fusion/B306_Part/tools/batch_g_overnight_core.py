#!/usr/bin/env python3
"""Pure bookkeeping primitives for the Batch-G overnight run."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Iterable


U32_MODULUS = 1 << 32


def u32_delta(first: int, last: int) -> int:
    return (last - first) % U32_MODULUS


def tag_domain_rate_hz(
    first_sweep: int,
    last_sweep: int,
    first_superframe: int,
    last_superframe: int,
    period_us: int,
) -> float:
    """Return successful sweeps per tag-domain elapsed second.

    Host timestamps are deliberately absent from this API, so USB/CDC backlog
    bunching cannot inflate or deflate the result.
    """
    if period_us not in (100_000, 110_000):
        raise ValueError("period_us must be 100000 or 110000")
    superframes = u32_delta(first_superframe, last_superframe)
    if superframes == 0:
        raise ValueError("tag-domain window has zero superframe span")
    sweeps = u32_delta(first_sweep, last_sweep)
    return sweeps / (superframes * period_us / 1_000_000.0)


def composed_idle_cfg(
    tag: int,
    slot: int,
    count: int,
    period_ms: int = 10,
) -> str:
    if not 1 <= tag <= 10:
        raise ValueError("tag must be in 1..10")
    if not 0 <= slot < count <= 11:
        raise ValueError("slot/count out of range")
    if period_ms != 10:
        raise ValueError("only the established 10 ms slot period is allowed")
    return (
        f"CFG TAG={tag} SLOT={slot} COUNT={count} PERIOD={period_ms} "
        "ACTIVE=9 EPOCH=5000 BEACON_SYNC=0 BEACON_WIN_N=1 "
        "DW_ANCHOR=0 RUN=0 PMODE=3"
    )


def active_cfg(
    tag: int,
    slot: int,
    count: int = 11,
    beacon_win_n: int = 1,
) -> str:
    if not 1 <= tag <= 10:
        raise ValueError("tag must be in 1..10")
    if not 0 <= slot < count <= 11:
        raise ValueError("slot/count out of range")
    if beacon_win_n not in (1, 3):
        raise ValueError("overnight run permits BEACON_WIN_N 1 or 3 only")
    return (
        f"CFG TAG={tag} SLOT={slot} COUNT={count} PERIOD=10 "
        "ACTIVE=9 EPOCH=5000 BEACON_SYNC=1 "
        f"BEACON_WIN_N={beacon_win_n} DW_ANCHOR=0 RUN=1 PMODE=0"
    )


@dataclass
class CfgTiming:
    node: str
    command: str
    dispatched_monotonic: float
    completed_monotonic: float | None = None
    retries: int = 0
    completion: str = "pending"
    reply: str | None = None

    @property
    def elapsed_s(self) -> float | None:
        if self.completed_monotonic is None:
            return None
        return self.completed_monotonic - self.dispatched_monotonic

    def as_dict(self) -> dict[str, object]:
        row = asdict(self)
        row["elapsed_s"] = self.elapsed_s
        return row


@dataclass
class AliveEpoch:
    opened_monotonic: float
    closed_monotonic: float | None = None
    last_seen_monotonic: float | None = None
    close_reason: str | None = None
    ledger_start: dict[str, int] = field(default_factory=dict)
    ledger_end: dict[str, int] = field(default_factory=dict)

    def ledger_deltas(self) -> dict[str, int]:
        return {
            key: u32_delta(value, self.ledger_end[key])
            for key, value in self.ledger_start.items()
            if key in self.ledger_end
        }

    def as_dict(self) -> dict[str, object]:
        row = asdict(self)
        row["ledger_deltas"] = self.ledger_deltas()
        return row


class AliveBook:
    """Track per-node connection epochs and close ledgers per alive window."""

    def __init__(self, nodes: Iterable[str]) -> None:
        self.epochs: dict[str, list[AliveEpoch]] = {
            node: [] for node in nodes
        }

    def connected(
        self,
        node: str,
        now: float,
        ledger: dict[str, int] | None = None,
    ) -> None:
        rows = self.epochs[node]
        if rows and rows[-1].closed_monotonic is None:
            rows[-1].last_seen_monotonic = now
            return
        rows.append(
            AliveEpoch(
                opened_monotonic=now,
                last_seen_monotonic=now,
                ledger_start=dict(ledger or {}),
            )
        )

    def seen(self, node: str, now: float) -> None:
        rows = self.epochs[node]
        if rows and rows[-1].closed_monotonic is None:
            rows[-1].last_seen_monotonic = now

    def disconnected(
        self,
        node: str,
        now: float,
        reason: str,
        ledger: dict[str, int] | None = None,
    ) -> None:
        rows = self.epochs[node]
        if not rows or rows[-1].closed_monotonic is not None:
            return
        row = rows[-1]
        row.closed_monotonic = now
        row.close_reason = reason
        row.ledger_end = dict(ledger or {})

    def is_alive(self, node: str) -> bool:
        rows = self.epochs[node]
        return bool(rows and rows[-1].closed_monotonic is None)

    def snapshot(self) -> dict[str, list[dict[str, object]]]:
        return {
            node: [epoch.as_dict() for epoch in rows]
            for node, rows in self.epochs.items()
        }


@dataclass
class SnapshotHealth:
    misses: int = 0
    degraded: bool = False

    def observe(self, reply_received: bool, connected: bool) -> None:
        if not connected:
            self.misses = 0
            self.degraded = False
        elif reply_received:
            self.misses = 0
            self.degraded = False
        else:
            self.misses += 1
            self.degraded = self.misses >= 2
