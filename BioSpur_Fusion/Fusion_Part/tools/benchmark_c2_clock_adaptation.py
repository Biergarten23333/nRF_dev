#!/usr/bin/env python3
from __future__ import annotations

from dataclasses import replace
import hashlib
import json
import statistics
import time

from biospur_fusion.c2_coupled_progressive.continuous_frontend import validate_event_clock
from biospur_fusion.c2_coupled_progressive.continuous_full_session_reader import (
    _event_digest, _event_structural_identity, _sensor_identity_digest,
)
from biospur_fusion.c2_coupled_progressive.continuous_stage2_adapter import (
    EventRegionOwner, FullSessionSourceEnvelope, adapt_verified_record,
    adapt_verified_record_batch,
)
from biospur_fusion.ingest.events import RecordType
from benchmark_c2_record_batches import _clock, _records


EXPECTED = {"scalar": {"binding": 57_000, "mapping": 59_000},
            "batch": {"binding": 3_000, "mapping": 28_000}}
MEASURED_PASSES = 4


class _CountingBinding:
    def __init__(self, binding, counter):
        self._binding = binding
        self._counter = counter

    def global_ns(self, timer_us):
        self._counter["mapping"] += 1
        return self._binding.global_ns(timer_us)

    def __getattr__(self, name):
        return getattr(self._binding, name)


class _CountingClock:
    def __init__(self):
        self._owner = _clock()
        self._counter = {"binding": 0, "mapping": 0}
        self._bindings = {row.node_id: _CountingBinding(row, self._counter)
                          for row in self._owner.bindings}

    def binding_for(self, node_id):
        self._counter["binding"] += 1
        return self._bindings[node_id]


def _prevalidate(rows):
    if not rows or any(row.raw is None for row in rows):
        raise TypeError("full-session source record is empty or unowned")
    raw_keys = {(row.raw.record_index, row.raw.start_offset, row.raw.end_offset,
                 row.raw.encoded_sha256) for row in rows}
    if len(raw_keys) != 1 or len({row.node_id for row in rows}) != 1:
        raise ValueError("full-session record identity is mixed")
    kinds = {row.record_type for row in rows}
    if kinds == {RecordType.IMU}:
        return
    if kinds == {RecordType.UWB} and len(rows) == 1:
        if type(rows[0].payload.get("frame_us")) is not int:
            raise ValueError("full-session UWB lacks frame availability")
        return
    raise ValueError("full-session record mixes sensor kinds")


def _one(mode, rows, clock, previous, region_owner):
    _prevalidate(rows)
    if previous is not None and type(previous) is not int:
        raise TypeError("previous global source availability must be integer nanoseconds")
    if mode == "batch":
        return adapt_verified_record_batch(
            rows, previous_global_source_availability_ns=previous,
            region_owner=region_owner,
            clock_owner=clock,
        )
    binding = clock.binding_for(rows[0].node_id)
    sensor_ready = (binding.global_ns(max(row.node_timer_us for row in rows))
                    if rows[0].record_type is RecordType.IMU
                    else binding.global_ns(rows[0].payload["frame_us"]))
    published = max(sensor_ready, sensor_ready if previous is None else previous)
    events = []
    for row in rows:
        event = adapt_verified_record(
            row, availability_global_ns=published,
            region_owner=region_owner,
            clock_owner=clock,
        )
        validate_event_clock(event, clock)
        events.append(event)
    return tuple(events), sensor_ready, published


def _run(mode, workload, clock, *, collect, one=_one):
    previous = None
    collected = []
    region_owner = EventRegionOwner(full_session=FullSessionSourceEnvelope())
    for rows in workload:
        events, sensor_ready, published = one(
            mode, rows, clock, previous, region_owner,
        )
        previous = published
        if collect:
            collected.append((
                events, tuple(_event_digest(event) for event in events),
                tuple(_sensor_identity_digest(event) for event in events),
                tuple(_event_structural_identity(event) for event in events),
                sensor_ready, published,
            ))
    return tuple(collected)


def _exception(mode, rows, previous=None):
    try:
        _one(mode, rows, _clock(), previous,
             EventRegionOwner(full_session=FullSessionSourceEnvelope()))
    except BaseException as error:
        return type(error).__name__, str(error)
    return None


def _semantic_precheck(workload):
    scalar_clock, batch_clock = _CountingClock(), _CountingClock()
    scalar = _run("scalar", workload, scalar_clock, collect=True)
    batch = _run("batch", workload, batch_clock, collect=True)
    if scalar != batch:
        raise AssertionError("scalar and batch semantic projections differ")
    if scalar_clock._counter != EXPECTED["scalar"] or batch_clock._counter != EXPECTED["batch"]:
        raise AssertionError("clock adaptation call counts differ from contract")
    imu = workload[0]
    uwb = workload[2]
    mixed_uwb = replace(uwb[0], raw=imu[0].raw)
    cases = (
        ((replace(imu[0], boot_epoch=8),), None),
        ((imu[0], replace(imu[1], node_id="BSF0001")), None),
        ((replace(imu[0], global_time_ns=1),), None),
        ((replace(imu[0], payload={**imu[0].payload, "base_timer2_us": None}),), None),
        ((replace(uwb[0], payload={**uwb[0].payload, "strobe_us": 0}),), None),
        ((replace(uwb[0], payload={**uwb[0].payload, "frame_us": "bad"}),), None),
        ((imu[0],), "bad"),
        ((imu[0], mixed_uwb), None),
    )
    malformed = []
    for rows, previous in cases:
        scalar_error = _exception("scalar", rows, previous)
        batch_error = _exception("batch", rows, previous)
        if scalar_error is None or scalar_error != batch_error:
            raise AssertionError(f"malformed semantic mismatch: {scalar_error!r} != {batch_error!r}")
        malformed.append(scalar_error)
    identity = hashlib.sha256()
    for row in scalar:
        identity.update(repr(row).encode())
    return {"semantic_equal": True, "semantic_sha256": identity.hexdigest(),
            "malformed": malformed, "scalar_calls": scalar_clock._counter,
            "batch_calls": batch_clock._counter}


def _summary(samples):
    mean = statistics.mean(samples)
    return {"samples_s": samples, "median_s": statistics.median(samples),
            "cv": statistics.pstdev(samples) / mean,
            "duration_ok": len(samples) == 5 and all(value >= .5 for value in samples)}


def _evaluate(measured):
    summaries = {mode: _summary(samples) for mode, samples in measured.items()}
    ratio = summaries["batch"]["median_s"] / summaries["scalar"]["median_s"]
    passed = (all(row["duration_ok"] and row["cv"] <= .15
                  for row in summaries.values()) and ratio <= .65)
    return summaries, ratio, passed


def _measure_interval(mode, workload, *, run=_run, clock_factory=_clock):
    started = time.perf_counter_ns()
    for _ in range(MEASURED_PASSES):
        run(mode, workload, clock_factory(), collect=False)
    return (time.perf_counter_ns() - started) * 1e-9


def main():
    workload = tuple(_records())
    if len(workload) != 3_000 or sum(len(row) for row in workload) != 27_000:
        raise AssertionError("synthetic workload shape changed")
    precheck = _semantic_precheck(workload)
    _run("scalar", workload, _clock(), collect=False)
    _run("batch", workload, _clock(), collect=False)
    measured = {"scalar": [], "batch": []}
    orders = []
    for repetition in range(5):
        order = ("scalar", "batch") if repetition % 2 == 0 else ("batch", "scalar")
        orders.append(order)
        for mode in order:
            measured[mode].append(_measure_interval(mode, workload))
    summaries, ratio, passed = _evaluate(measured)
    result = {"schema": "c2-clock-adaptation-benchmark-v1", "cycles": 1_000,
              "records": 3_000, "events": 27_000, "measured_passes": MEASURED_PASSES,
              "orders": orders,
              "precheck": precheck, "summaries": summaries,
              "batch_per_event_ratio": ratio, "passed": passed}
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
