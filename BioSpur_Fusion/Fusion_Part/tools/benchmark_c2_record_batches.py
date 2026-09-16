#!/usr/bin/env python3
from __future__ import annotations

from collections import Counter
from dataclasses import replace
import hashlib
import json
import statistics
import time

import biospur_fusion.c2_coupled_progressive.continuous_full_session_reader as reader
from biospur_fusion.c2_coupled_progressive.continuous_frontend import (
    CONTINUOUS_FRONTEND_SCHEMA,
    ContinuousClockOwner,
    NodeClockBinding,
)
from biospur_fusion.ingest.events import RawByteProvenance
from test_c2_full_session_sequence_domains import _imu, _router, _uwb


CYCLES = 1_000
NODES = tuple(f"BSF{index:04X}" for index in range(10))
CALL_KEYS = ("event", "sensor", "structural", "generic", "imu_child",
             "uwb_child", "record")


def _serialized_calls(calls) -> dict[str, int]:
    return {key: int(calls.get(key, 0)) for key in CALL_KEYS}


def _counts_match(row: dict[str, object], *, events: int, records: int) -> bool:
    calls = row["calls"]
    return (
        calls["event"] == events
        and calls["sensor"] == events
        and calls["structural"] == (4 if row["mode"] == "legacy" else 2) * events
        and row["callbacks"] == (events if row["mode"] == "legacy" else records)
        and calls["generic"] == (events if row["mode"] == "legacy" else 0)
        and calls["imu_child"] + calls["uwb_child"]
            == (events if row["mode"] == "legacy" else 0)
        and calls["record"] == (0 if row["mode"] == "legacy" else records)
    )


def _clock() -> ContinuousClockOwner:
    return ContinuousClockOwner(CONTINUOUS_FRONTEND_SCHEMA, tuple(
        NodeClockBinding(node, 7, "B306_TIMER2", f"{index + 1:064x}",
                         1_000.0, 0.0, "2" * 64, "3" * 64)
        for index, node in enumerate(NODES)
    ))


def _records():
    imu_timer = {node: 10_000 for node in NODES}
    uwb_timer = {node: 10_000 for node in NODES}
    imu_sequence = Counter()
    record_index = 0
    for cycle in range(CYCLES):
        node = NODES[cycle % len(NODES)]
        for count in (10, 16):
            record_index += 1
            rows = []
            for sample in range(count):
                timer = imu_timer[node]
                sequence = imu_sequence[node] & 0xffff
                raw = RawByteProvenance(
                    record_index, record_index * 512, record_index * 512 + 511,
                    hashlib.sha256(f"record:{record_index}".encode()).hexdigest(), sample,
                )
                row = _imu(sequence, timer, sample)
                rows.append(replace(row, node_id=node, raw=raw))
                imu_timer[node] += 5_000
                imu_sequence[node] += 1
            yield tuple(rows)
        record_index += 1
        timer = uwb_timer[node]
        raw = RawByteProvenance(
            record_index, record_index * 512, record_index * 512 + 511,
            hashlib.sha256(f"record:{record_index}".encode()).hexdigest(), 0,
        )
        row = _uwb(cycle, cycle, timer=timer, frame_timer=timer + 1_000)
        yield (replace(row, node_id=node, raw=raw),)
        uwb_timer[node] += 10_000


def _run(mode: str) -> dict[str, object]:
    router = _router()
    router.clock_owner = _clock()
    calls = Counter()
    originals = {
        "event": reader._event_digest,
        "sensor": reader._sensor_identity_digest,
        "structural": reader._event_structural_identity,
        "generic": reader.FullSessionEventTicket.__init__,
        "imu_child": reader.FullSessionImuEventTicket.__init__,
        "uwb_child": reader.FullSessionUwbEventTicket.__init__,
        "record": reader.FullSessionRecordTicket.__init__,
    }

    def wrap_function(name):
        def wrapped(value):
            calls[name] += 1
            return originals[name](value)
        return wrapped

    def wrap_init(name):
        def wrapped(instance, *args, **kwargs):
            calls[name] += 1
            originals[name](instance, *args, **kwargs)
        return wrapped

    reader._event_digest = wrap_function("event")
    reader._sensor_identity_digest = wrap_function("sensor")
    reader._event_structural_identity = wrap_function("structural")
    reader.FullSessionEventTicket.__init__ = wrap_init("generic")
    reader.FullSessionImuEventTicket.__init__ = wrap_init("imu_child")
    reader.FullSessionUwbEventTicket.__init__ = wrap_init("uwb_child")
    reader.FullSessionRecordTicket.__init__ = wrap_init("record")
    owner = (reader._FullSessionDeliveryOwner(router) if mode == "legacy"
             else reader._FullSessionRecordDeliveryOwner(router))
    semantic = hashlib.sha256()
    route_seconds = delivery_seconds = 0.0
    records = events = callbacks = 0
    try:
        for rows in _records():
            started = time.perf_counter()
            routed = router.route_original_record(rows)
            route_seconds += time.perf_counter() - started
            records += 1
            events += len(routed)
            for event, digest, sensor_digest in zip(
                routed, router._pending_event_digests, router._pending_sensor_digests,
            ):
                semantic.update(event.event_id.encode())
                semantic.update(digest.encode())
                semantic.update(sensor_digest.encode())
            started = time.perf_counter()
            if mode == "legacy":
                for event in routed:
                    owner.issue(event).dispatch().deliver(lambda _event: None)
                    callbacks += 1
                owner.require_record_consumed()
            else:
                owner.issue().deliver(lambda _batch: None)
                owner.require_consumed()
                callbacks += 1
            delivery_seconds += time.perf_counter() - started
        audit = router.finish()
    finally:
        reader._event_digest = originals["event"]
        reader._sensor_identity_digest = originals["sensor"]
        reader._event_structural_identity = originals["structural"]
        reader.FullSessionEventTicket.__init__ = originals["generic"]
        reader.FullSessionImuEventTicket.__init__ = originals["imu_child"]
        reader.FullSessionUwbEventTicket.__init__ = originals["uwb_child"]
        reader.FullSessionRecordTicket.__init__ = originals["record"]
    return {
        "mode": mode, "route_s": route_seconds, "delivery_s": delivery_seconds,
        "total_s": route_seconds + delivery_seconds, "records": records,
        "events": events, "callbacks": callbacks, "calls": _serialized_calls(calls),
        "semantic_sha256": semantic.hexdigest(), "audit": audit,
    }


def _summary(rows, field):
    values = [float(row[field]) for row in rows]
    mean = statistics.mean(values)
    return {"samples": values, "median": statistics.median(values),
            "cv": statistics.pstdev(values) / mean}


def main() -> int:
    _run("legacy")
    _run("batch")
    measured = []
    for repetition in range(5):
        order = ("legacy", "batch") if repetition % 2 == 0 else ("batch", "legacy")
        measured.extend(_run(mode) for mode in order)
    legacy = [row for row in measured if row["mode"] == "legacy"]
    batch = [row for row in measured if row["mode"] == "batch"]
    baseline = legacy[0]
    semantics_equal = all(
        row["semantic_sha256"] == baseline["semantic_sha256"]
        and row["audit"] == baseline["audit"] for row in measured
    )
    events = int(baseline["events"]); records = int(baseline["records"])
    counts_ok = all(_counts_match(row, events=events, records=records)
                    for row in measured)
    summaries = {mode: {field: _summary(rows, field) for field in
                 ("route_s", "delivery_s", "total_s")}
                 for mode, rows in (("legacy", legacy), ("batch", batch))}
    delivery_ratio = (summaries["batch"]["delivery_s"]["median"] / events) / (
        summaries["legacy"]["delivery_s"]["median"] / events)
    total_ratio = summaries["batch"]["total_s"]["median"] / summaries["legacy"]["total_s"]["median"]
    cv_ok = all(value["cv"] <= .15 for mode in summaries.values() for value in mode.values())
    duration_ok = all(row["total_s"] >= .5 for row in measured)
    passed = (semantics_equal and counts_ok and cv_ok and duration_ok
              and delivery_ratio <= .5 and total_ratio <= .75)
    output = {
        "schema": "c2-record-batch-microbenchmark-v1", "cycles": CYCLES,
        "records": records, "events": events, "semantic_equal": semantics_equal,
        "semantic_sha256": baseline["semantic_sha256"], "counts_ok": counts_ok,
        "example_counts": {"legacy": legacy[0]["calls"], "batch": batch[0]["calls"]},
        "callbacks": {"legacy": legacy[0]["callbacks"], "batch": batch[0]["callbacks"]},
        "summaries": summaries, "delivery_per_event_ratio": delivery_ratio,
        "route_plus_delivery_ratio": total_ratio, "cv_ok": cv_ok,
        "duration_ok": duration_ok, "passed": passed,
    }
    print(json.dumps(output, sort_keys=True, separators=(",", ":")))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
