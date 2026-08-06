#!/usr/bin/env python3
"""Shared delivered-rate arithmetic and physical invariants.

Delivery rate uses host observation time. Device timestamp deltas describe source
cadence and discontinuities; they must not silently redefine the host window.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from statistics import median
from typing import Iterable


@dataclass(frozen=True)
class RateResult:
    delivered: int
    host_span_s: float
    delivered_rate_hz: float
    endpoint_rate_hz: float | None
    median_delta_us: float | None
    cadence_rate_hz: float | None
    flags: tuple[str, ...]

    def json(self) -> dict:
        return asdict(self)


def delivered_rate(
    delivered: int,
    host_span_s: float,
    device_timestamps_us: Iterable[int],
    *,
    stream: str,
    max_rate_hz: float,
    tolerance: float = 0.0,
) -> RateResult:
    """Return host-window delivery rate plus diagnostic device-time rates."""
    ts = list(device_timestamps_us)
    rate = delivered / host_span_s if host_span_s > 0 else 0.0
    endpoint = None
    cadence = None
    med = None
    if len(ts) > 1 and ts[-1] > ts[0]:
        endpoint = (len(ts) - 1) * 1_000_000.0 / (ts[-1] - ts[0])
        deltas = [b - a for a, b in zip(ts, ts[1:]) if b > a]
        if deltas:
            med = median(deltas)
            cadence = 1_000_000.0 / med
    flags: list[str] = []
    ceiling = max_rate_hz * (1.0 + tolerance)
    if rate > ceiling:
        flags.append(f"IMPOSSIBLE_{stream.upper()}_DELIVERY_RATE")
    if cadence is not None and cadence > ceiling:
        flags.append(f"IMPOSSIBLE_{stream.upper()}_CADENCE")
    if stream.lower() == "imu" and delivered < 0:
        flags.append("INVALID_IMU_SAMPLE_COUNT")
    return RateResult(delivered, host_span_s, rate, endpoint, med, cadence, tuple(flags))
