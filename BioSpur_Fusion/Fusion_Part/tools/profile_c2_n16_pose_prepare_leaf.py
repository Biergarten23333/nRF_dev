#!/usr/bin/env python3
"""Leaf attribution for native-200 articulated-pose batch preparation.

This diagnostic uses only the authenticated synthetic N16 fixture.  It traces
the existing pose preparation method by source-line regions; it does not
replace, reorder, or otherwise reinterpret either hinge-projection pass.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import inspect
import json
import math
import os
from pathlib import Path
import resource
import statistics
import sys
import time
from typing import Any, Callable


SCHEMA = "biospur.c2.n16-pose-prepare-leaf-attribution.v1"
ATTEMPTS = 20
WARMUPS = 2
MINIMUM_ATTRIBUTION = 0.90
MAXIMUM_OUTPUT_BYTES = 5 << 20
STAGES = (
    "validation_copy",
    "current_projection",
    "current_fk",
    "previous_projection",
    "previous_fk",
    "freeze_digest_plan",
)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _percentile(values: list[float], quantile: float) -> float:
    ordered = sorted(values)
    return ordered[math.ceil(quantile * len(ordered)) - 1]


def _pose_source_path() -> Path:
    return (
        Path(__file__).resolve().parents[1]
        / "src/biospur_fusion/c2_uwb_calibration/causal_articulated_pose.py"
    )


def _stage_boundaries(function: Callable[..., object]) -> tuple[tuple[int, str], ...]:
    """Resolve the six preregistered source regions from exact code anchors."""

    lines, start = inspect.getsourcelines(function)
    anchors = {
        "current_projection": "projector = self.hinge_projector",
        "current_fk": "points_rows = [",
        "previous_projection": "previous_bases = {",
        "previous_fk": "prior_points_rows = [",
        "freeze_digest_plan": "prepared_rows = []",
    }
    resolved: dict[str, int] = {}
    for stage, anchor in anchors.items():
        matches = [
            start + offset for offset, line in enumerate(lines)
            if line.strip() == anchor
        ]
        if not matches:
            raise RuntimeError(f"pose leaf source anchor missing: {anchor}")
        # The first prior_points_rows block is the optimized previous-pass FK.
        resolved[stage] = matches[0]
    ordered = (
        (start, "validation_copy"),
        (resolved["current_projection"], "current_projection"),
        (resolved["current_fk"], "current_fk"),
        (resolved["previous_projection"], "previous_projection"),
        (resolved["previous_fk"], "previous_fk"),
        (resolved["freeze_digest_plan"], "freeze_digest_plan"),
    )
    if tuple(stage for _line, stage in ordered) != STAGES:
        raise RuntimeError("pose leaf stage order changed")
    if any(left[0] >= right[0] for left, right in zip(ordered, ordered[1:])):
        raise RuntimeError("pose leaf source anchors are not strictly ordered")
    return ordered


def _stage_for_line(
    boundaries: tuple[tuple[int, str], ...], lineno: int,
) -> str:
    selected = boundaries[0][1]
    for start, stage in boundaries:
        if lineno < start:
            break
        selected = stage
    return selected


class _PoseLeafTrace:
    """Attribute one exact target call while leaving nested code untouched."""

    def __init__(
        self, function: Callable[..., object], *, clock_ns=time.perf_counter_ns,
    ) -> None:
        self._code = function.__code__
        self.boundaries = _stage_boundaries(function)
        self._clock_ns = clock_ns
        self.exclusive_ns: Counter[str] = Counter()
        self.calls: Counter[str] = Counter()
        self._seen_stages: set[str] = set()
        self._last_ns: int | None = None
        self._last_line: int | None = None

    def __call__(self, frame, event: str, _arg):
        if frame.f_code is not self._code:
            return None
        now = self._clock_ns()
        if event == "call":
            self._last_ns = now
            self._last_line = frame.f_code.co_firstlineno
            return self
        if self._last_ns is not None and self._last_line is not None:
            elapsed = now - self._last_ns
            stage = _stage_for_line(self.boundaries, self._last_line)
            self.exclusive_ns[stage] += elapsed
            if stage not in self._seen_stages:
                self.calls[stage] += 1
                self._seen_stages.add(stage)
        self._last_ns = now
        if event == "line":
            self._last_line = frame.f_lineno
            return self
        if event in ("return", "exception"):
            self._last_line = None
            self._last_ns = None
        return self


class _PosePrepareObserver:
    """Collect exact plan digests, optionally with leaf attribution."""

    def __init__(self, *, instrument: bool) -> None:
        self.instrument = instrument
        self.stage_ns: Counter[str] = Counter()
        self.stage_calls: Counter[str] = Counter()
        self.plan_digests: list[tuple[str, str]] = []
        self.leaf_wall_ns = 0
        self._installed: list[tuple[object, object]] = []

    def install(self, label: str, pose: object) -> None:
        original = pose.prepare_native200_batch
        function = original.__func__

        def observed(*args, **kwargs):
            trace = _PoseLeafTrace(function) if self.instrument else None
            previous_trace = sys.gettrace()
            started = time.perf_counter_ns()
            if trace is not None:
                sys.settrace(trace)
            try:
                plan = original(*args, **kwargs)
            finally:
                if trace is not None:
                    sys.settrace(previous_trace)
                self.leaf_wall_ns += time.perf_counter_ns() - started
            self.plan_digests.append((label, plan.digest))
            if trace is not None:
                self.stage_ns.update(trace.exclusive_ns)
                self.stage_calls.update(trace.calls)
            return plan

        self._installed.append((pose, original))
        pose.prepare_native200_batch = observed

    def uninstall(self) -> None:
        while self._installed:
            pose, original = self._installed.pop()
            pose.prepare_native200_batch = original


def _validated_attribution(
    *, leaf_wall_s: float, stage_ns: Counter[str],
) -> tuple[float, float, float]:
    if not math.isfinite(leaf_wall_s) or leaf_wall_s <= 0.0:
        raise ValueError("profiled leaf wall must be finite and positive")
    attributed_s = sum(stage_ns.values()) * 1e-9
    unattributed_s = leaf_wall_s - attributed_s
    if attributed_s < 0.0 or unattributed_s < 0.0:
        raise RuntimeError("leaf timing escaped the measured pose interval")
    fraction = attributed_s / leaf_wall_s
    if fraction > 1.0:
        raise RuntimeError("leaf attribution exceeds pose interval")
    return attributed_s, unattributed_s, fraction


def _consume_once(*, instrument: bool) -> tuple[float, bytes, _PosePrepareObserver]:
    from test_c2_full_session_ten_node_ab import (
        _coordinator_state_bytes,
        _real_record_coordinator,
    )

    coordinator, _frames, ticket = _real_record_coordinator(16)
    observer = _PosePrepareObserver(instrument=instrument)
    observer.install("A", coordinator._a._composition.engine.pose)
    observer.install("B", coordinator._b._composition.engine.pose)
    started = time.perf_counter_ns()
    try:
        coordinator.consume_record_ticket(ticket)
    finally:
        elapsed_s = (time.perf_counter_ns() - started) * 1e-9
        observer.uninstall()
    return elapsed_s, _coordinator_state_bytes(coordinator), observer


def validate_bounded_result(
    result: dict[str, Any], *, expected_profile_sha256: str,
    expected_fixture_sha256: str, expected_pose_source_sha256: str,
) -> None:
    if result.get("status") != "NESTED_ATTRIBUTION_PASS":
        raise ValueError("nested N16 attribution did not pass")
    nested = result.get("nested", {})
    inertness = result.get("inertness", {})
    provenance = result.get("provenance", {})
    if not 0.90 <= nested.get("attribution_fraction", -1.0) <= 1.0:
        raise ValueError("nested attribution fraction is outside [0.90, 1.0]")
    if nested.get("unattributed_s", -1.0) < 0.0:
        raise ValueError("nested unattributed time is negative")
    if tuple(nested.get("dependency_order", ())) != STAGES:
        raise ValueError("two-pass dependency order changed")
    stage_s = nested.get("stage_s", {})
    stage_calls = nested.get("stage_calls", {})
    if set(stage_s) != set(STAGES) or set(stage_calls) != set(STAGES):
        raise ValueError("nested attribution stage set is incomplete")
    expected_calls = 2 * int(result.get("limits", {}).get("attempts", -1))
    if expected_calls != 2 * ATTEMPTS:
        raise ValueError("nested attribution attempt contract changed")
    for stage in STAGES:
        seconds = stage_s[stage]
        calls = stage_calls[stage]
        if not isinstance(seconds, (int, float)) or not math.isfinite(seconds):
            raise ValueError(f"nested attribution stage is nonfinite: {stage}")
        if seconds < 0.0:
            raise ValueError(f"nested attribution stage is negative: {stage}")
        if isinstance(calls, bool) or not isinstance(calls, int):
            raise ValueError(f"nested attribution call count is invalid: {stage}")
        if calls <= 0 or calls != expected_calls:
            raise ValueError(f"nested attribution call count differs: {stage}")
    if inertness.get("all_profiled_states_equal_controls") is not True:
        raise ValueError("nested profiler changed final owner bytes")
    if inertness.get("all_profiled_plan_digests_equal_controls") is not True:
        raise ValueError("nested profiler changed prepared plan digests")
    if result.get("limits", {}).get("raw_calibration_data") is not False:
        raise ValueError("nested profiler opened raw calibration data")
    expected = {
        "profile_sha256": expected_profile_sha256,
        "fixture_sha256": expected_fixture_sha256,
        "pose_source_sha256": expected_pose_source_sha256,
    }
    for key, digest in expected.items():
        if provenance.get(key) != digest:
            raise ValueError(f"nested profiler provenance mismatch: {key}")


def run() -> dict[str, Any]:
    started = time.monotonic()
    for _ in range(WARMUPS):
        _consume_once(instrument=False)
        _consume_once(instrument=True)
    control_record_s: list[float] = []
    profile_record_s: list[float] = []
    control_leaf_s: list[float] = []
    profile_leaf_s: list[float] = []
    stage_ns: Counter[str] = Counter()
    stage_calls: Counter[str] = Counter()
    state_digest: str | None = None
    plan_digest: str | None = None
    for _ in range(ATTEMPTS):
        control_s, control_state, control = _consume_once(instrument=False)
        profile_s, profile_state, profiled = _consume_once(instrument=True)
        if control_state != profile_state:
            raise RuntimeError("leaf profiler changed authoritative owner bytes")
        if control.plan_digests != profiled.plan_digests:
            raise RuntimeError("leaf profiler changed prepared plan digests")
        current_state_digest = hashlib.sha256(control_state).hexdigest()
        current_plan_digest = hashlib.sha256(
            json.dumps(control.plan_digests, separators=(",", ":")).encode()
        ).hexdigest()
        if state_digest is None:
            state_digest = current_state_digest
            plan_digest = current_plan_digest
        elif (current_state_digest, current_plan_digest) != (
            state_digest, plan_digest,
        ):
            raise RuntimeError("synthetic N16 leaf result is nondeterministic")
        control_record_s.append(control_s)
        profile_record_s.append(profile_s)
        control_leaf_s.append(control.leaf_wall_ns * 1e-9)
        profile_leaf_s.append(profiled.leaf_wall_ns * 1e-9)
        stage_ns.update(profiled.stage_ns)
        stage_calls.update(profiled.stage_calls)
    leaf_wall_s = sum(profile_leaf_s)
    attributed_s, unattributed_s, fraction = _validated_attribution(
        leaf_wall_s=leaf_wall_s, stage_ns=stage_ns,
    )
    return {
        "schema": SCHEMA,
        "status": (
            "NESTED_ATTRIBUTION_PASS"
            if fraction >= MINIMUM_ATTRIBUTION
            else "NESTED_ATTRIBUTION_FAIL"
        ),
        "optimization_proposal_allowed": fraction >= MINIMUM_ATTRIBUTION,
        "limits": {
            "raw_calibration_data": False,
            "attempts": ATTEMPTS,
            "warmups": WARMUPS,
            "minimum_attribution": MINIMUM_ATTRIBUTION,
            "maximum_output_bytes": MAXIMUM_OUTPUT_BYTES,
            "retry": False,
        },
        "control": {
            "record_median_s": statistics.median(control_record_s),
            "record_p95_s": _percentile(control_record_s, 0.95),
            "pose_prepare_median_s": statistics.median(control_leaf_s),
        },
        "profiled": {
            "record_median_s": statistics.median(profile_record_s),
            "record_p95_s": _percentile(profile_record_s, 0.95),
            "pose_prepare_median_s": statistics.median(profile_leaf_s),
        },
        "nested": {
            "leaf_wall_s": leaf_wall_s,
            "attributed_s": attributed_s,
            "unattributed_s": unattributed_s,
            "attribution_fraction": fraction,
            "dependency_order": STAGES,
            "stage_s": {
                key: stage_ns[key] * 1e-9 for key in STAGES
            },
            "stage_calls": {
                key: stage_calls[key] for key in STAGES
            },
        },
        "inertness": {
            "all_profiled_states_equal_controls": True,
            "all_profiled_plan_digests_equal_controls": True,
            "authoritative_state_sha256": state_digest,
            "prepared_plan_pair_sha256": plan_digest,
        },
        "provenance": {
            "profile_sha256": _sha(Path(__file__).resolve()),
            "fixture_sha256": _sha(
                Path(__file__).resolve().parents[1]
                / "tests/test_c2_full_session_ten_node_ab.py"
            ),
            "pose_source_sha256": _sha(_pose_source_path()),
        },
        "wall_s": time.monotonic() - started,
        "peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
    }


def _write_new(path: Path, result: dict[str, Any]) -> None:
    payload = (
        json.dumps(result, sort_keys=True, separators=(",", ":"), allow_nan=False)
        + "\n"
    ).encode()
    if len(payload) > MAXIMUM_OUTPUT_BYTES:
        raise RuntimeError("nested N16 profile evidence exceeds 5 MiB")
    descriptor = os.open(
        path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC, 0o444,
    )
    try:
        if os.write(descriptor, payload) != len(payload):
            raise RuntimeError("short nested profile evidence write")
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = run()
    _write_new(args.output, result)
    return 0 if result["status"] == "NESTED_ATTRIBUTION_PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
