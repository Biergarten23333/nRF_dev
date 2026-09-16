#!/usr/bin/env python3
"""Attribute the synthetic authenticated N16 record path without raw data."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import math
import os
from pathlib import Path
import resource
import statistics
import time
from typing import Any

SCHEMA = "biospur.c2.n16-record-path-attribution.v1"
ATTEMPTS = 30
WARMUPS = 3
MINIMUM_ATTRIBUTION = 0.90
MAXIMUM_OUTPUT_BYTES = 5 << 20


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _percentile(values: list[float], quantile: float) -> float:
    ordered = sorted(values)
    return ordered[math.ceil(quantile * len(ordered)) - 1]


class _ExclusiveTimer:
    """Record mutually exclusive nested wall time for installed hooks."""

    def __init__(self, *, clock_ns=time.perf_counter_ns) -> None:
        self.exclusive_ns: Counter[str] = Counter()
        self.calls: Counter[str] = Counter()
        self._stack: list[list[int | str]] = []
        self._clock_ns = clock_ns
        self._installed: list[tuple[object, str, object]] = []

    def wrap(self, owner: object, name: str, category: str) -> None:
        original = getattr(owner, name)
        self._installed.append((owner, name, original))

        def measured(*args, **kwargs):
            row: list[int | str] = [category, self._clock_ns(), 0]
            self._stack.append(row)
            try:
                return original(*args, **kwargs)
            finally:
                ended = self._clock_ns()
                current = self._stack.pop()
                elapsed = ended - int(current[1])
                child = int(current[2])
                self.exclusive_ns[category] += elapsed - child
                self.calls[category] += 1
                if self._stack:
                    self._stack[-1][2] = int(self._stack[-1][2]) + elapsed

        setattr(owner, name, measured)

    def uninstall(self) -> None:
        """Restore every wrapped method before any out-of-window work."""

        if self._stack:
            raise RuntimeError("cannot uninstall active timing hooks")
        while self._installed:
            owner, name, original = self._installed.pop()
            setattr(owner, name, original)


def _validated_attribution(
    *, profiled_wall_s: float, exclusive_ns: Counter[str],
) -> tuple[float, float, float]:
    if not math.isfinite(profiled_wall_s) or profiled_wall_s <= 0.0:
        raise ValueError("profiled wall interval must be finite and positive")
    attributed_s = sum(exclusive_ns.values()) * 1e-9
    unattributed_s = profiled_wall_s - attributed_s
    if attributed_s < 0.0 or unattributed_s < 0.0:
        raise RuntimeError("exclusive timing escaped the profiled wall interval")
    fraction = attributed_s / profiled_wall_s
    if fraction > 1.0:
        raise RuntimeError("exclusive attribution exceeds profiled wall interval")
    return attributed_s, unattributed_s, fraction


def validate_bounded_result(
    result: dict[str, Any], *, expected_profile_sha256: str,
    expected_fixture_sha256: str,
) -> None:
    """Apply the wrapper's fail-closed result and provenance contract."""

    if result.get("status") != "ATTRIBUTION_PASS":
        raise ValueError("N16 attribution did not pass")
    profiled = result.get("profiled", {})
    control = result.get("control", {})
    inertness = result.get("inertness", {})
    provenance = result.get("provenance", {})
    if not 0.90 <= profiled.get("attribution_fraction", -1.0) <= 1.0:
        raise ValueError("N16 attribution fraction is outside [0.90, 1.0]")
    if profiled.get("unattributed_s", -1.0) < 0.0:
        raise ValueError("N16 unattributed wall time is negative")
    if control.get("p95_s", float("inf")) > 0.072:
        raise ValueError("N16 control p95 exceeds 72 ms")
    if control.get("p95_s", float("inf")) / 16 > 0.0045:
        raise ValueError("N16 per-frame p95 exceeds 4.5 ms")
    if inertness.get("all_profiled_states_equal_controls") is not True:
        raise ValueError("N16 profiling changed authoritative state")
    if result.get("limits", {}).get("raw_calibration_data") is not False:
        raise ValueError("N16 profile opened raw calibration data")
    if provenance.get("profile_sha256") != expected_profile_sha256:
        raise ValueError("N16 profiler provenance mismatch")
    if provenance.get("fixture_sha256") != expected_fixture_sha256:
        raise ValueError("N16 fixture provenance mismatch")


def _install(timer: _ExclusiveTimer, coordinator: object) -> None:
    left, right = coordinator._a, coordinator._b
    timer.wrap(coordinator, "_record_batch_snapshot", "outer_snapshot")
    timer.wrap(coordinator, "_begin_record_transaction", "record_begin_exclusive")
    timer.wrap(coordinator, "_accept_record_frames", "route_exclusive")
    timer.wrap(coordinator, "_atomic_ab_native200_batch", "ab_batch_exclusive")
    timer.wrap(coordinator, "_close_record_transaction", "record_close")
    timer.wrap(
        coordinator, "_validate_record_transaction_finalization",
        "record_prevalidate_final",
    )
    timer.wrap(coordinator, "_publish_ab_transactions", "audit_publish")
    timer.wrap(coordinator, "_discard_record_transaction", "record_discard")
    timer.wrap(coordinator._body, "ingest_record_batch", "body_ingest")
    for label, owner in (("a", left), ("b", right)):
        timer.wrap(owner, "_issue_native200_record_capability", f"{label}_capability_issue")
        timer.wrap(owner._composition, "snapshot", f"{label}_composition_snapshot")
        timer.wrap(
            owner, "native200_record_batch_classification",
            f"{label}_classification",
        )
        timer.wrap(owner, "prepare_native200_batch", f"{label}_owner_prepare")
        timer.wrap(owner._composition, "prepare_native200_batch", f"{label}_composition_prepare")
        timer.wrap(owner._composition.history, "prepare_native200_batch", f"{label}_history_prepare")
        timer.wrap(owner, "commit_native200_batch", f"{label}_owner_commit")
        timer.wrap(owner._composition, "commit_native200_batch", f"{label}_composition_commit")
        timer.wrap(owner._composition.history, "commit_native200_batch", f"{label}_history_commit")
        timer.wrap(owner._composition.engine, "add_imu", f"{label}_root_add_imu")


def _consume_once(*, instrument: bool) -> tuple[float, bytes, _ExclusiveTimer | None]:
    from test_c2_full_session_ten_node_ab import (
        _coordinator_state_bytes,
        _real_record_coordinator,
    )

    coordinator, _frames, ticket = _real_record_coordinator(16)
    timer = _ExclusiveTimer() if instrument else None
    if timer is not None:
        _install(timer, coordinator)
    started = time.perf_counter_ns()
    coordinator.consume_record_ticket(ticket)
    elapsed_s = (time.perf_counter_ns() - started) * 1e-9
    if timer is not None:
        timer.uninstall()
    return elapsed_s, _coordinator_state_bytes(coordinator), timer


def run() -> dict[str, Any]:
    started = time.monotonic()
    for _ in range(WARMUPS):
        _consume_once(instrument=False)
        _consume_once(instrument=True)
    controls: list[float] = []
    profiles: list[float] = []
    exclusive_ns: Counter[str] = Counter()
    calls: Counter[str] = Counter()
    state_digest: str | None = None
    for _ in range(ATTEMPTS):
        control_s, control_state, _ = _consume_once(instrument=False)
        profile_s, profile_state, timer = _consume_once(instrument=True)
        if control_state != profile_state:
            raise RuntimeError("profile hooks changed authoritative owner bytes")
        digest = hashlib.sha256(control_state).hexdigest()
        if state_digest is None:
            state_digest = digest
        elif digest != state_digest:
            raise RuntimeError("synthetic N16 owner result is nondeterministic")
        assert timer is not None
        controls.append(control_s)
        profiles.append(profile_s)
        exclusive_ns.update(timer.exclusive_ns)
        calls.update(timer.calls)
    profiled_wall_s = sum(profiles)
    attributed_s, unattributed_s, attribution = _validated_attribution(
        profiled_wall_s=profiled_wall_s, exclusive_ns=exclusive_ns,
    )
    overhead_s = max(0.0, statistics.median(profiles) - statistics.median(controls))
    return {
        "schema": SCHEMA,
        "status": "ATTRIBUTION_PASS" if attribution >= MINIMUM_ATTRIBUTION else "ATTRIBUTION_FAIL",
        "optimization_authorized": attribution >= MINIMUM_ATTRIBUTION,
        "limits": {
            "raw_calibration_data": False,
            "attempts": ATTEMPTS,
            "warmups": WARMUPS,
            "minimum_attribution": MINIMUM_ATTRIBUTION,
            "maximum_output_bytes": MAXIMUM_OUTPUT_BYTES,
            "retry": False,
        },
        "control": {
            "median_s": statistics.median(controls),
            "p95_s": _percentile(controls, 0.95),
            "samples_s": controls,
        },
        "profiled": {
            "median_s": statistics.median(profiles),
            "p95_s": _percentile(profiles, 0.95),
            "samples_s": profiles,
            "total_wall_s": profiled_wall_s,
            "attributed_exclusive_s": attributed_s,
            "attribution_fraction": attribution,
            "unattributed_s": unattributed_s,
            "exclusive_stage_s": {
                key: value * 1e-9 for key, value in sorted(exclusive_ns.items())
            },
            "stage_calls": dict(sorted(calls.items())),
            "median_timer_observer_overhead_s": overhead_s,
            "median_timer_observer_overhead_fraction": (
                overhead_s / statistics.median(controls)
            ),
        },
        "inertness": {
            "all_profiled_states_equal_controls": True,
            "authoritative_state_sha256": state_digest,
        },
        "provenance": {
            "profile_sha256": _sha(Path(__file__).resolve()),
            "fixture_sha256": _sha(
                Path(__file__).resolve().parents[1]
                / "tests/test_c2_full_session_ten_node_ab.py"
            ),
        },
        "wall_s": time.monotonic() - started,
        "peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
    }


def _write_new(path: Path, result: dict[str, Any]) -> None:
    payload = (json.dumps(result, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode()
    if len(payload) > MAXIMUM_OUTPUT_BYTES:
        raise RuntimeError("N16 profile evidence exceeds 5 MiB")
    descriptor = os.open(
        path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC, 0o444,
    )
    try:
        if os.write(descriptor, payload) != len(payload):
            raise RuntimeError("short N16 profile evidence write")
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = run()
    _write_new(args.output, result)
    return 0 if result["status"] == "ATTRIBUTION_PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
