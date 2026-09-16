#!/usr/bin/env python3
"""No-raw profile of the sealed authoritative articulated coordinator harness."""
from __future__ import annotations

import argparse
from collections import defaultdict
from contextlib import ExitStack, contextmanager
import hashlib
import json
import os
from pathlib import Path
import resource
import statistics
import time
import tracemalloc

import numpy as np

import audit_c2_articulated_analytic_authoritative as harness
from biospur_fusion.c2_uwb_calibration import articulated_range
from biospur_fusion.c2_uwb_calibration.causal_articulated_pose import (
    CausalArticulatedPose,
)
from biospur_fusion.c2_uwb_root_world import causal_update_transaction
from biospur_fusion.c2_uwb_root_world import authoritative_articulated_fusion as fusion
from biospur_fusion.c2_uwb_root_world.owner_bound_async_worker import (
    CausalRobustSharedRootOwner,
)
from biospur_fusion.root_r3.estimator import CausalDelayedRootFilter


ROOT = Path(__file__).resolve().parents[1]
HARNESS_SEAL = (
    "1cc4766d20e6cd6733897620fc49cc80abd1f60b7eb05ce1f229931d641abfe0"
)
DELIVERY_SEAL = (
    "80a52f09c5eeea162836af11a339029b9444fcd1d65789611763e4804b9b7a1b"
)


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _rss_kib() -> int:
    for line in Path("/proc/self/status").read_text().splitlines():
        if line.startswith("VmRSS:"):
            return int(line.split()[1])
    raise RuntimeError("VmRSS unavailable")


def _summary(values) -> dict[str, float | int]:
    sample = np.asarray(tuple(values), dtype=float)
    if not len(sample):
        return {"count": 0, "total_ms": 0.0, "mean_ms": 0.0,
                "p99_ms": 0.0, "maximum_ms": 0.0}
    return {
        "count": int(len(sample)),
        "total_ms": float(sample.sum()),
        "mean_ms": float(sample.mean()),
        "p99_ms": float(np.percentile(sample, 99)),
        "maximum_ms": float(sample.max()),
    }


def _load_fixture():
    static, template, _ = harness.packets()
    static = harness.replace(
        static,
        clocks={
            node: harness.replace(clock, last_timer_us=6_000_000)
            for node, clock in static.clocks.items()
        },
        digest="",
    )
    geometry, alignment, model, model_owner, data, frames = harness._pose_owner()
    template = harness.replace(
        template,
        b_shadow_owner=harness.replace(
            template.b_shadow_owner, geometry=geometry, digest=""
        ),
        digest="",
    )
    return static, template, geometry, alignment, model, model_owner, data, frames


def _verify_inputs() -> None:
    audit_seal = ROOT / (
        "logs/c2_authoritative_articulated_analytic_authoritative_audit_"
        "revision_006_20260907T130000Z/SHA256SUMS"
    )
    delivery_seal = ROOT / (
        "logs/c2_authoritative_articulated_analytic_jacobian_delivery_"
        "revision_001_20260907T102000Z/SHA256SUMS"
    )
    if _sha(audit_seal) != HARNESS_SEAL or _sha(delivery_seal) != DELIVERY_SEAL:
        raise RuntimeError("sealed profile input mismatch")


def _run_uninstrumented() -> dict:
    fixture = _load_fixture()
    rows, final_state, u1_calls, imu_count = harness._execute(
        "analytic", fixture[0], fixture[1], fixture[2], fixture[3],
        fixture[4], fixture[6], fixture[7],
    )
    service = [row["service_ms"] for row in rows]
    by_outcome = defaultdict(list)
    by_nodes = defaultdict(list)
    by_contact = defaultdict(list)
    by_joint_class = defaultdict(list)
    for row in rows:
        accepted = bool(row["result"]["accepted"])
        by_outcome["accepted" if accepted else "rejected"].append(row["service_ms"])
        by_nodes[str(row["node_count_requested"])].append(row["service_ms"])
        by_contact[row["contact_mode"]].append(row["service_ms"])
        key = f"nodes={row['node_count_requested']};contact={row['contact_mode']};" \
              f"accepted={str(accepted).lower()}"
        by_joint_class[key].append(row["service_ms"])
    return {
        "mode": "uninstrumented",
        "groups": len(rows), "imu_count": imu_count, "u1_calls": u1_calls,
        "timing_ms": _summary(service),
        "effective_utilization": float(statistics.mean(service) / 120.048),
        "by_outcome": {key: _summary(value) for key, value in sorted(by_outcome.items())},
        "by_node_count": {key: _summary(value) for key, value in sorted(by_nodes.items())},
        "by_contact": {key: _summary(value) for key, value in sorted(by_contact.items())},
        "by_joint_class": {
            key: _summary(value) for key, value in sorted(by_joint_class.items())
        },
        "final_owner_digest": harness._digest(final_state),
        "rss_kib": _rss_kib(),
        "peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
    }


@contextmanager
def _patch(target, name, replacement):
    original = getattr(target, name)
    setattr(target, name, replacement(original))
    try:
        yield
    finally:
        setattr(target, name, original)


def _run_timing() -> dict:
    fixture = _load_fixture()
    inclusive = defaultdict(list)
    active = {"record": None}
    engine_rows = []

    def timed(label):
        def factory(original):
            def wrapped(*args, **kwargs):
                started = time.perf_counter_ns()
                try:
                    return original(*args, **kwargs)
                finally:
                    elapsed = (time.perf_counter_ns() - started) * 1e-6
                    if active["record"] is not None:
                        inclusive[label].append(elapsed)
                        active["record"][label] = active["record"].get(label, 0.0) + elapsed
            return wrapped
        return factory

    def admit_factory(original):
        def wrapped(self, packet, epoch):
            if active["record"] is not None:
                raise RuntimeError("nested coordinator admission")
            requested = sum(any(row.valid_mask & (1 << anchor) for anchor in range(8))
                            for row in packet.event.payload)
            record = {
                "node_count_requested": int(requested),
                "contact_constraint_count": len(epoch.point_constraints_world_m),
            }
            active["record"] = record
            started = time.perf_counter_ns()
            try:
                result = original(self, packet, epoch)
                record["accepted"] = bool(result.accepted)
                record["reason"] = result.reason
                return result
            finally:
                record["engine_admit"] = (time.perf_counter_ns() - started) * 1e-6
                engine_rows.append(record)
                active["record"] = None
        return wrapped

    with ExitStack() as stack:
        stack.enter_context(_patch(fusion.AuthoritativeArticulatedFusion, "admit", admit_factory))
        stack.enter_context(_patch(fusion, "_prepare_dynamic_owner", timed("packet_prelink_validation")))
        stack.enter_context(_patch(CausalRobustSharedRootOwner, "prepare", timed("robust_root_prepare")))
        stack.enter_context(_patch(fusion, "solve_articulated_ranges", timed("articulated_solve_total")))
        stack.enter_context(_patch(fusion, "execute_causal_update_transaction", timed("atomic_transaction")))
        stack.enter_context(_patch(fusion.AuthoritativeArticulatedFusion, "_published_result", timed("immutable_publication")))
        stack.enter_context(_patch(articulated_range, "least_squares", timed("scipy_least_squares")))
        stack.enter_context(_patch(articulated_range, "corrected_proxy_points", timed("solver_fk_contact_calls")))
        stack.enter_context(_patch(fusion, "corrected_proxy_points", timed("coordinator_fk_publication_calls")))
        stack.enter_context(_patch(CausalDelayedRootFilter, "prepare_position", timed("transaction_root_prepare_position")))
        stack.enter_context(_patch(CausalDelayedRootFilter, "_prevalidate_position_plan", timed("transaction_root_prevalidate")))
        stack.enter_context(_patch(CausalDelayedRootFilter, "_prepare_position_rollback", timed("transaction_root_rollback_snapshot")))
        stack.enter_context(_patch(CausalDelayedRootFilter, "_apply_prevalidated_position", timed("transaction_root_apply")))
        stack.enter_context(_patch(CausalArticulatedPose, "prepare_guarded_install", timed("transaction_pose_prepare")))
        stack.enter_context(_patch(CausalArticulatedPose, "_prevalidate_install_plan", timed("transaction_pose_prevalidate")))
        stack.enter_context(_patch(CausalArticulatedPose, "_prepare_install_rollback", timed("transaction_pose_rollback_snapshot")))
        stack.enter_context(_patch(CausalArticulatedPose, "_apply_prevalidated_install", timed("transaction_pose_apply")))
        stack.enter_context(_patch(CausalRobustSharedRootOwner, "prevalidate_commit", timed("transaction_robust_prevalidate")))
        stack.enter_context(_patch(CausalRobustSharedRootOwner, "_apply_prevalidated_commit", timed("transaction_robust_apply")))
        stack.enter_context(_patch(causal_update_transaction, "evaluate_candidate_transition", timed("transaction_u1_guard")))
        rows, final_state, u1_calls, imu_count = harness._execute(
            "analytic", fixture[0], fixture[1], fixture[2], fixture[3],
            fixture[4], fixture[6], fixture[7],
        )

    top = (
        "packet_prelink_validation", "robust_root_prepare", "articulated_solve_total",
        "atomic_transaction", "immutable_publication",
    )
    for record in engine_rows:
        record["coordinator_remainder"] = record["engine_admit"] - sum(
            record.get(label, 0.0) for label in top
        )
        transaction_children = (
            "transaction_root_prepare_position", "transaction_root_prevalidate",
            "transaction_root_rollback_snapshot", "transaction_root_apply",
            "transaction_pose_prepare", "transaction_pose_prevalidate",
            "transaction_pose_rollback_snapshot", "transaction_pose_apply",
            "transaction_robust_prevalidate", "transaction_robust_apply",
            "transaction_u1_guard",
        )
        if "atomic_transaction" in record:
            record["transaction_remainder"] = record["atomic_transaction"] - sum(
                record.get(label, 0.0) for label in transaction_children
            )
    by_stage = {key: _summary(value) for key, value in sorted(inclusive.items())}
    by_stage["coordinator_remainder"] = _summary(
        record["coordinator_remainder"] for record in engine_rows
    )
    by_stage["transaction_remainder"] = _summary(
        record["transaction_remainder"]
        for record in engine_rows if "transaction_remainder" in record
    )
    by_stage["engine_admit"] = _summary(record["engine_admit"] for record in engine_rows)
    return {
        "mode": "timing_instrumented", "groups": len(rows),
        "imu_count": imu_count, "u1_calls": u1_calls,
        "stage_timing_ms": by_stage,
        "groups_detail": engine_rows,
        "nested_accounting_note": (
            "Only the five top-level stages plus coordinator_remainder are additive; "
            "SciPy and FK/contact rows are nested diagnostic subdivisions."
        ),
        "final_owner_digest": harness._digest(final_state),
        "rss_kib": _rss_kib(),
        "peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
    }


def _run_memory() -> dict:
    milestones = []

    def mark(name):
        current, peak = tracemalloc.get_traced_memory()
        milestones.append({
            "name": name, "traced_current_bytes": current,
            "traced_peak_bytes": peak, "rss_kib": _rss_kib(),
            "peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        })

    tracemalloc.start(25)
    mark("trace_start_after_imports")
    fixture = _load_fixture()
    mark("sealed_fixture_and_pose_loaded")
    rows, final_state, u1_calls, imu_count = harness._execute(
        "analytic", fixture[0], fixture[1], fixture[2], fixture[3],
        fixture[4], fixture[6], fixture[7],
    )
    mark("after_41_group_1007_imu_execution")
    snapshot = tracemalloc.take_snapshot()
    top = [str(statistic) for statistic in snapshot.statistics("traceback")[:20]]
    tracemalloc.stop()
    return {
        "mode": "memory_instrumented", "groups": len(rows),
        "imu_count": imu_count, "u1_calls": u1_calls,
        "milestones": milestones, "top_python_allocations": top,
        "final_owner_digest": harness._digest(final_state),
        "raw_709mb_status": "UNATTRIBUTED_NO_RAW_PROJECTION_ONLY",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("uninstrumented", "timing", "memory"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise RuntimeError("fresh output required")
    _verify_inputs()
    result = {
        "uninstrumented": _run_uninstrumented,
        "timing": _run_timing,
        "memory": _run_memory,
    }[args.mode]()
    result.update({
        "schema": "biospur.c2.authoritative-articulated-profile.v1",
        "production_source_changed": False,
        "raw_opened": False, "hxx_opened": False,
        "harness_sha256": _sha(ROOT / "tools/audit_c2_articulated_analytic_authoritative.py"),
        "analytic_source_sha256": _sha(
            ROOT / "src/biospur_fusion/c2_uwb_calibration/articulated_range.py"
        ),
    })
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
