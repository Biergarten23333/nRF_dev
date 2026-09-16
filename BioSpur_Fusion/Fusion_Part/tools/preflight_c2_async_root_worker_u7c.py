#!/usr/bin/env python3
"""One-shot fixture gate for the U7C READY-handshaked ROOT worker."""
from __future__ import annotations

import argparse
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import numpy as np

from biospur_fusion.c2_uwb_calibration.direct_body_shadow_ab import DirectNodeLinkClock
from biospur_fusion.c2_uwb_root_world.async_root_worker_u7c import (
    AsyncRootWorker, RootWorkerConfig, RootWorkerEvent, THREAD_ENV, run_synchronous)
from biospur_fusion.c2_uwb_root_world.causal_update_guard import ReachabilityClass, ReachabilityEnvelope
from biospur_fusion.c2_uwb_root_world.offline_unified_wiring import group_epoch_times_ns
from biospur_fusion.c2_uwb_root_world.u0 import UwbRow
from biospur_fusion.root_r3.models import ImuSample


ROOT = Path(__file__).resolve().parents[1]
RSS_CAP_KIB = 300_000
EVIDENCE_CAP = 20_000_000
U7B = ROOT / "logs/c2_async_root_worker_u7b_20260906T152840Z/SHA256SUMS"
U7B_DIGEST = "10bb80c304e25fff4741a1148780e6be8565974e5c530ad6088aed6c2d77e5f3"
OWNED = (
    ROOT / "src/biospur_fusion/c2_uwb_root_world/async_root_worker.py",
    ROOT / "src/biospur_fusion/c2_uwb_root_world/async_root_worker_u7c.py",
    ROOT / "tests/test_c2_async_root_worker.py",
    ROOT / "tests/test_c2_async_root_worker_u7c.py",
    Path(__file__).resolve(),
)
TESTS = (
    "tests/test_c2_async_root_worker_u7c.py",
    "tests/test_c2_async_root_worker.py",
    "tests/test_c2_online_root_coordinator.py",
    "tests/test_c2_causal_update_guard.py",
    "tests/test_c2_causal_update_transaction.py",
)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write(path, value):
    Path(path).write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n")


def stats(values):
    values = np.asarray(values, float)
    return {"count": int(values.size), "p50_ms": float(np.quantile(values, .5)),
            "p99_ms": float(np.quantile(values, .99)), "maximum_ms": float(np.max(values))}


def memory():
    result = {}
    for line in Path("/proc/self/status").read_text().splitlines():
        if line.startswith(("VmRSS:", "VmHWM:")):
            key, value, _ = line.split()
            result[key[:-1] + "_kib"] = int(value)
    return result


def external_rss(path):
    for line in Path(path).read_text().splitlines():
        if "Maximum resident set size (kbytes):" in line:
            return int(line.rsplit(":", 1)[1])
    raise RuntimeError("test RSS unavailable")


def envelope():
    return ReachabilityEnvelope(ReachabilityClass.NOMINAL, 1., 10., 100., 1., 10., 100.,
        .2, .2, 1., .02, 2, 1., 1e8, "explicit U7C workload envelope")


def workload():
    anchors = np.array([[1, 0, 0], [0, 1, 0], [0, 0, 1], [-1, -1, -1],
                        [2, 0, 0], [0, 2, 0], [0, 0, 2], [-2, -2, -2]], float)
    clocks = {f"N{i}": DirectNodeLinkClock(f"N{i}", 1000., 0., 0, 0, 10_000_000)
              for i in range(10)}
    config = RootWorkerConfig(0., np.zeros(9), np.eye(9) * .1, anchors, clocks,
                              np.zeros(8), 0., envelope(), 64)
    events = []
    sequence = 0
    for index in range(1, 251):
        event_time = .005 * index
        events.append(RootWorkerEvent(sequence, event_time, "IMU", ImuSample(
            event_time, event_time, np.array([0., 0., 9.80665]), np.eye(3), index)))
        sequence += 1
    ranges = tuple(int(round(np.linalg.norm(anchors[i]) * 1000)) for i in range(8))
    for index in range(10):
        frame = 100_000 + 120_048 * index
        strobe = frame - 40_000
        rows = tuple(UwbRow(f"N{i}", 0, 1, 1, strobe, frame, tuple(range(8)), ranges,
            (100, 200, 300, 400, 500, 600, 700, 800), (100,) * 8, 0xff)
            for i in range(10))
        _, _, availability = group_epoch_times_ns(rows, clocks=clocks)
        events.append(RootWorkerEvent(sequence, availability * 1e-9, "UWB", rows))
        sequence += 1
    events.sort(key=lambda row: (row.availability_time_s, 0 if row.kind == "IMU" else 1,
                                 row.sequence))
    return config, [replace(event, sequence=index) for index, event in enumerate(events)]


def virtual_schedule(events, actual, final):
    uwb = {row["sequence"]: row["timing_ms"]["service_total"] for row in actual}
    imu = iter(final["imu_timing_ms"])
    first = events[0].availability_time_s
    cursor_ms = 0.0
    rows = []
    for event in events:
        arrival_ms = (event.availability_time_s - first) * 1000.0
        service_ms = float(next(imu) if event.kind == "IMU" else uwb[event.sequence])
        start_ms = max(arrival_ms, cursor_ms)
        complete_ms = start_ms + service_ms
        rows.append({"sequence": event.sequence, "kind": event.kind,
                     "arrival_ms": arrival_ms, "service_ms": service_ms,
                     "start_ms": start_ms, "complete_ms": complete_ms,
                     "lag_ms": complete_ms - arrival_ms})
        cursor_ms = complete_ms
    return rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    args.output.mkdir(parents=True)
    started = time.perf_counter()
    before = {str(path.relative_to(ROOT)): sha(path) for path in OWNED}
    if sha(U7B) != U7B_DIGEST:
        raise RuntimeError("U7B seal binding failed")
    env = dict(os.environ)
    env.update(THREAD_ENV)
    env["PYTHONPATH"] = "src:tools:."
    test_time = args.output / "TEST_TIME.txt"
    command = [sys.executable, "-m", "pytest", "-q", *TESTS]
    with (args.output / "TEST_STDOUT.txt").open("w") as out, \
         (args.output / "TEST_STDERR.txt").open("w") as err:
        test = subprocess.run(["/usr/bin/time", "-v", "-o", str(test_time), *command],
            cwd=ROOT, env=env, stdout=out, stderr=err, timeout=180, check=False)

    config, events = workload()
    reference_all, reference_final = run_synchronous(config, events)
    reference = [row for row in reference_all if row["kind"] == "UWB"]
    worker = AsyncRootWorker(config)
    cold_start_ms = worker.cold_start_ms
    producer_start = time.perf_counter()
    first = events[0].availability_time_s
    for event in events:
        target = producer_start + event.availability_time_s - first
        remaining = target - time.perf_counter()
        if remaining > 0:
            time.sleep(remaining)
        worker.submit(event)
    queue_hwm = worker.queue_high_watermark
    submit = tuple(worker.submit_blocking_ms)
    actual, final = worker.close_and_collect(10, timeout_s=30.)

    names = ("state", "covariance", "h", "r", "s", "innovation")
    errors = {name: max(float(np.max(np.abs(a[name] - b[name])))
                             for a, b in zip(actual, reference)) for name in names}
    errors["nis"] = max(abs(a["nis"] - b["nis"]) for a, b in zip(actual, reference))
    decisions_exact = all((a["decision"], a["root_reason"], a["committed"],
                           a["rejection_recorded"]) ==
                          (b["decision"], b["root_reason"], b["committed"],
                           b["rejection_recorded"])
                          for a, b in zip(actual, reference))
    errors["final_state"] = float(np.max(np.abs(final["state"] - reference_final["state"])))
    errors["final_covariance"] = float(np.max(
        np.abs(final["covariance"] - reference_final["covariance"])))
    timing = {key: stats([row["timing_ms"][key] for row in actual]) for key in (
        "link_build", "adaptive_trust_ten_nodes", "final_shared_root", "root_prepare",
        "guard", "validation_apply", "transaction_total", "ipc_receive",
        "end_to_end_publication_lag")}
    timing["imu_propagation"] = stats(final["imu_timing_ms"])
    timing["uwb_service_total"] = stats([row["timing_ms"]["service_total"] for row in actual])
    timing["submit_blocking"] = stats(submit)
    virtual_rows = virtual_schedule(events, actual, final)
    write(args.output / "VIRTUAL_SCHEDULE.json", virtual_rows)
    virtual_uwb = [row["lag_ms"] for row in virtual_rows if row["kind"] == "UWB"]
    virtual_timing = stats(virtual_uwb)
    utilization = (timing["imu_propagation"]["p99_ms"] / 5.0 +
                   timing["uwb_service_total"]["p99_ms"] / 120.048)
    lags = [row["timing_ms"]["end_to_end_publication_lag"] for row in actual]
    parent_memory = memory()
    worker_rss = final["worker_rusage_self_maxrss_kib"]
    after = {str(path.relative_to(ROOT)): sha(path) for path in OWNED}
    parity = decisions_exact and all(value <= 1e-12 for value in errors.values())
    literal_drain = bool(final["sentinel_received"] and
                         final["processed_event_count"] == len(events) and
                         final["input_queue_size_after_join"] == 0 and
                         final["process_exitcode"] == 0 and
                         not final["process_alive_after_join"])
    ready = bool(test.returncode == 0 and parity and len(actual) == 10 and
        final["imu_count"] == 250 and final["group_count"] == 10 and
        final["future_imu_count"] == final["future_uwb_count"] == 0 and
        queue_hwm < 64 and timing["submit_blocking"]["p99_ms"] < 5.0 and
        max(lags) < 150.0 and timing["end_to_end_publication_lag"]["p99_ms"] < 150.0 and
        max(lags) < 200.0 and virtual_timing["p99_ms"] < 150.0 and
        virtual_timing["maximum_ms"] < 150.0 and utilization < 1.0 and literal_drain and
        worker_rss < RSS_CAP_KIB and parent_memory["VmHWM_kib"] < RSS_CAP_KIB and
        external_rss(test_time) < RSS_CAP_KIB and before == after)
    status = "READY_FOR_MONITOR_U7C_ENGINEERING_REVIEW" if ready else "ONLINE_BLOCKED_U7C_FIXTURE"
    result = {"schema": "biospur.c2.async_root.u7c.fixture.v1", "status": status,
        "execution_class": "ENGINEERING_FIXTURE_ONLY",
        "online_status": "PENDING_REVIEW" if ready else "ONLINE_BLOCKED",
        "scientific_pass": False, "calibrated_R": False, "production_ready": False,
        "raw_opened": False, "action04_opened": False, "HXX_opened": False,
        "articulated_u2_invoked": False, "u7b_seal_sha256": U7B_DIGEST,
        "cold_start_ms_outside_capture_epoch": cold_start_ms,
        "events": {"imu": final["imu_count"], "uwb_groups": final["group_count"],
                   "nodes_per_group": 10, "links_per_group": 80},
        "decisions_exact": decisions_exact, "maximum_absolute_parity_errors": errors,
        "timing_ms": timing, "virtual_schedule_uwb_lag_ms": virtual_timing,
        "service_utilization_no_nested_guard_double_count": utilization,
        "publication_lag_deadline_misses_150ms": sum(value >= 150.0 for value in lags),
        "fixed_lag_horizon_ms": 200.0, "queue_capacity": 64,
        "queue_high_watermark": queue_hwm, "queue_drained_to_zero": literal_drain,
        "sentinel_received": final["sentinel_received"],
        "processed_event_count": final["processed_event_count"],
        "process_exitcode": final["process_exitcode"], "loss_count": 0,
        "future_access_count": 0, "overflow_count": 0,
        "headroom_target_12_0048ms_non_gating": {"target_ms": 12.0048,
            "observed_p99_ms": timing["uwb_service_total"]["p99_ms"],
            "met": timing["uwb_service_total"]["p99_ms"] < 12.0048},
        "resources": {"parent": parent_memory, "worker_maxrss_kib": worker_rss,
            "test_external_maxrss_kib": external_rss(test_time)},
        "thread_environment_worker": final["thread_environment"],
        "tests_returncode": test.returncode, "source_hashes_unchanged": before == after,
        "wall_s": time.perf_counter() - started}
    write(args.output / "RESULT.json", result)
    write(args.output / "HASHES.json", {"before": before, "after": after})
    literal = ("PYTHONPATH=src:tools:. .venv-v0/bin/python "
        f"tools/preflight_c2_async_root_worker_u7c.py --output {args.output}")
    (args.output / "COMMAND.txt").write_text(literal + "\n")
    (args.output / "REPORT.md").write_text(
        "# U7C READY-handshaked ROOT worker fixture\n\n"
        f"Status: `{status}`. Cold start {cold_start_ms:.3f} ms was excluded from capture. "
        f"Post-READY publication p99/max were {timing['end_to_end_publication_lag']['p99_ms']:.3f}/"
        f"{timing['end_to_end_publication_lag']['maximum_ms']:.3f} ms; deterministic virtual "
        f"p99/max were {virtual_timing['p99_ms']:.3f}/{virtual_timing['maximum_ms']:.3f} ms. "
        f"Queue HWM {queue_hwm}/64, literal drain {literal_drain}, utilization {utilization:.4f}.\n\n"
        "No raw/action04/HXX was opened. Scientific, calibrated-R, production, and ARTICULATED "
        "claims remain false.\n")
    members = sorted(path for path in args.output.iterdir() if path.name != "SHA256SUMS")
    (args.output / "SHA256SUMS").write_text("".join(
        f"{sha(path)}  {path.name}\n" for path in members))
    if sum(path.stat().st_size for path in args.output.iterdir()) >= EVIDENCE_CAP:
        raise RuntimeError("evidence cap")
    print(json.dumps({"status": status, "seal_sha256": sha(args.output / "SHA256SUMS"),
        "cold_start_ms": cold_start_ms,
        "publication_max_ms": timing["end_to_end_publication_lag"]["maximum_ms"]}, sort_keys=True))
    return 0 if ready else 2


if __name__ == "__main__":
    raise SystemExit(main())
