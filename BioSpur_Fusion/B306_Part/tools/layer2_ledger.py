#!/usr/bin/env python3
"""Exact per-class accounting for protocol-7 Layer-2 captures."""

from __future__ import annotations

from typing import Mapping


CLASSES = ("imu", "uwb", "ctl")


def u32_delta(before: int, after: int) -> int:
    return (after - before) & 0xFFFFFFFF


def _value(snapshot: Mapping[str, object], name: str) -> int:
    value = snapshot[name]
    return int(value, 0) if isinstance(value, str) else int(value)


def ledger_between(
    before: Mapping[str, object],
    after: Mapping[str, object],
) -> dict[str, object]:
    """Balance B306 enqueues against B306 drops and DK BLE receipts.

    The DK appends its cumulative delivery counters to the exact host kind-6
    record carrying the B306 counter snapshot. Comparing two such records
    avoids an independently sampled window edge.
    """

    rows: dict[str, dict[str, int | bool]] = {}
    for queue_class in CLASSES:
        enqueued = u32_delta(
            _value(before, f"enq_{queue_class}"),
            _value(after, f"enq_{queue_class}"),
        )
        dropped = u32_delta(
            _value(before, f"q_drop_{queue_class}"),
            _value(after, f"q_drop_{queue_class}"),
        )
        delivered = u32_delta(
            _value(before, f"delivered_{queue_class}"),
            _value(after, f"delivered_{queue_class}"),
        )
        aborted = u32_delta(
            _value(before, f"abort_{queue_class}"),
            _value(after, f"abort_{queue_class}"),
        )
        residual = enqueued - dropped - delivered
        rows[queue_class] = {
            "enqueued": enqueued,
            "queue_dropped": dropped,
            "delivered_to_dk": delivered,
            "producer_aborted": aborted,
            "residual": residual,
            "balanced": residual == 0,
        }
    epoch_deferred = u32_delta(
        _value(before, "imu_epoch_defer_drop"),
        _value(after, "imu_epoch_defer_drop"),
    )
    return {
        "classes": rows,
        "imu_epoch_defer_drop": epoch_deferred,
        "balanced": all(bool(row["balanced"]) for row in rows.values()),
    }


def imu_missing_record_causes(
    before: Mapping[str, object],
    after: Mapping[str, object],
) -> dict[str, int]:
    return {
        "queue_drop": u32_delta(
            _value(before, "q_drop_imu"),
            _value(after, "q_drop_imu"),
        ),
        "producer_abort": u32_delta(
            _value(before, "abort_imu"),
            _value(after, "abort_imu"),
        ),
        "dk_epoch_defer": u32_delta(
            _value(before, "imu_epoch_defer_drop"),
            _value(after, "imu_epoch_defer_drop"),
        ),
    }
