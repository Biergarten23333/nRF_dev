#!/usr/bin/env python3
"""One-shot no-raw attributed profile of the exact 41/1007 coordinator."""
from __future__ import annotations

import argparse
from collections import defaultdict
from contextlib import ExitStack, contextmanager
import hashlib
import json
from pathlib import Path
import resource
import time

import numpy as np

import audit_c2_articulated_analytic_authoritative as harness
import profile_c2_authoritative_articulated_coordinator as prior_profile
from biospur_fusion.c2_articulated_biomechanics.hinge_temporal import (
    _CausalHingeTemporalOwner,
)
from biospur_fusion.c2_uwb_calibration import articulated_range
from biospur_fusion.c2_uwb_calibration import causal_articulated_pose as pose_module
from biospur_fusion.c2_uwb_calibration.causal_articulated_pose import (
    CausalArticulatedPose,
)
from biospur_fusion.c2_uwb_root_world import authoritative_articulated_fusion as fusion
from biospur_fusion.c2_uwb_root_world import causal_update_transaction
from biospur_fusion.c2_uwb_root_world.owner_bound_async_worker import (
    CausalRobustSharedRootOwner,
)
from biospur_fusion.root_r3.estimator import CausalDelayedRootFilter


ROOT = Path(__file__).resolve().parents[1]
IMPLEMENTATION_SEAL = "3faa330becff2c2af761408046f83e6ebae9bf7299dfde053bc2fac8db7e6787"


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
    values = np.asarray(tuple(values), dtype=float)
    if not len(values):
        return {"count": 0, "mean_ms": 0.0, "p99_ms": 0.0,
                "maximum_ms": 0.0, "total_ms": 0.0}
    return {
        "count": int(len(values)), "mean_ms": float(values.mean()),
        "p99_ms": float(np.percentile(values, 99)),
        "maximum_ms": float(values.max()), "total_ms": float(values.sum()),
    }


@contextmanager
def _patch(target, name, factory):
    original = getattr(target, name)
    setattr(target, name, factory(original))
    try:
        yield
    finally:
        setattr(target, name, original)


def _semantic_digest(row) -> str:
    return harness._digest({key: value for key, value in row.items() if key != "service_ms"})


def _execute_uninstrumented(fixture):
    return harness._execute(
        "analytic", fixture[0], fixture[1], fixture[2], fixture[3],
        fixture[4], fixture[6], fixture[7],
    )


def _profile(fixture):
    stage = defaultdict(list)
    records = []
    native_sample = []
    imu_add = []
    active = {"record": None, "planned": None}

    def timed(label):
        def factory(original):
            def wrapped(*args, **kwargs):
                started = time.perf_counter_ns()
                try:
                    return original(*args, **kwargs)
                finally:
                    elapsed = (time.perf_counter_ns() - started) * 1e-6
                    stage[label].append(elapsed)
                    if active["record"] is not None:
                        active["record"][label] = (
                            active["record"].get(label, 0.0) + elapsed
                        )
            return wrapped
        return factory

    def admit_factory(original):
        def wrapped(self, packet, epoch):
            record = {
                "nodes": int(sum(row.valid_mask != 0 for row in packet.event.payload)),
                "contact": (
                    "two_foot" if len(epoch.point_constraints_world_m) == 2 else
                    "one_foot" if len(epoch.point_constraints_world_m) == 1 else
                    "swing"
                ),
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
                records.append(record)
                active["record"] = None
        return wrapped

    def planned_factory(original):
        def wrapped(*args, **kwargs):
            started = time.perf_counter_ns()
            active["planned"] = {
                "started": started, "pre_evaluator_ms": 0.0,
                "evaluator_ms": 0.0,
            }
            try:
                return original(*args, **kwargs)
            finally:
                elapsed = (time.perf_counter_ns() - started) * 1e-6
                stage["pose_certificate_total"].append(elapsed)
                if active["record"] is not None:
                    active["record"]["pose_certificate_total"] = elapsed
                planned = active["planned"]
                accounted = (
                    planned["pre_evaluator_ms"] + planned["evaluator_ms"]
                    + planned.get("q_extract_ms", 0.0)
                    + planned.get("temporal_preview_ms", 0.0)
                    + planned.get("digest_ms", 0.0)
                )
                remainder = elapsed - accounted
                stage["pose_derivative_and_evidence_remainder"].append(remainder)
                if active["record"] is not None:
                    active["record"]["pose_derivative_and_evidence_remainder"] = remainder
                active["planned"] = None
        return wrapped

    def evaluator_factory(original):
        def wrapped(*args, **kwargs):
            planned = active["planned"]
            started = time.perf_counter_ns()
            if planned is not None:
                planned["pre_evaluator_ms"] = (started - planned["started"]) * 1e-6
                stage["pose_scalar_schedule_so3"].append(planned["pre_evaluator_ms"])
            try:
                return original(*args, **kwargs)
            finally:
                elapsed = (time.perf_counter_ns() - started) * 1e-6
                stage["pose_batch_project_q_fk"].append(elapsed)
                if planned is not None:
                    planned["evaluator_ms"] = elapsed
                if active["record"] is not None:
                    active["record"]["pose_scalar_schedule_so3"] = planned["pre_evaluator_ms"]
                    active["record"]["pose_batch_project_q_fk"] = elapsed
        return wrapped

    def planned_subpart(label):
        def factory(original):
            def wrapped(*args, **kwargs):
                started = time.perf_counter_ns()
                try:
                    return original(*args, **kwargs)
                finally:
                    elapsed = (time.perf_counter_ns() - started) * 1e-6
                    stage[label].append(elapsed)
                    if active["planned"] is not None:
                        active["planned"][label.replace("pose_", "") + "_ms"] = (
                            active["planned"].get(label.replace("pose_", "") + "_ms", 0.0)
                            + elapsed
                        )
            return wrapped
        return factory

    def sample_factory(original):
        def wrapped(*args, **kwargs):
            started = time.perf_counter_ns()
            result = original(*args, **kwargs)
            native_sample.append((time.perf_counter_ns() - started) * 1e-6)
            return result
        return wrapped

    def imu_factory(original):
        def wrapped(*args, **kwargs):
            started = time.perf_counter_ns()
            result = original(*args, **kwargs)
            imu_add.append((time.perf_counter_ns() - started) * 1e-6)
            return result
        return wrapped

    with ExitStack() as stack:
        stack.enter_context(_patch(fusion.AuthoritativeArticulatedFusion, "admit", admit_factory))
        stack.enter_context(_patch(fusion.AuthoritativeArticulatedFusion, "add_imu", imu_factory))
        stack.enter_context(_patch(CausalArticulatedPose, "sample", sample_factory))
        stack.enter_context(_patch(CausalArticulatedPose, "_planned_hinge_transition", planned_factory))
        stack.enter_context(_patch(pose_module, "evaluate_hinge_projection_batch", evaluator_factory))
        stack.enter_context(_patch(pose_module, "extract_public_hinge_q_rad", planned_subpart("pose_q_extract")))
        stack.enter_context(_patch(_CausalHingeTemporalOwner, "_preview_from_snapshot", planned_subpart("pose_temporal_preview")))
        stack.enter_context(_patch(CausalArticulatedPose, "_planned_digest", planned_subpart("pose_digest")))
        stack.enter_context(_patch(fusion, "_prepare_dynamic_owner", timed("causal_prelink_packet")))
        stack.enter_context(_patch(CausalRobustSharedRootOwner, "prepare", timed("robust_prepare")))
        stack.enter_context(_patch(fusion, "solve_articulated_ranges", timed("articulated_solve_jacobian")))
        stack.enter_context(_patch(fusion, "execute_causal_update_transaction", timed("atomic_transaction_total")))
        stack.enter_context(_patch(fusion.AuthoritativeArticulatedFusion, "_published_result", timed("immutable_publication")))
        stack.enter_context(_patch(CausalDelayedRootFilter, "_prepare_position_rollback", timed("transaction_root_snapshot")))
        stack.enter_context(_patch(CausalArticulatedPose, "_prepare_install_rollback", timed("transaction_pose_snapshot")))
        stack.enter_context(_patch(CausalDelayedRootFilter, "_apply_prevalidated_position", timed("root_commit_apply")))
        stack.enter_context(_patch(CausalArticulatedPose, "_apply_prevalidated_install", timed("pose_commit_apply")))
        stack.enter_context(_patch(CausalRobustSharedRootOwner, "_apply_prevalidated_commit", timed("robust_commit_apply")))
        stack.enter_context(_patch(causal_update_transaction, "evaluate_candidate_transition", timed("u1_guard")))
        stack.enter_context(_patch(articulated_range, "corrected_proxy_points", timed("solver_fk_contact_audit")))
        stack.enter_context(_patch(fusion, "corrected_proxy_points", timed("coordinator_fk_contact_audit")))
        rows, state, u1, imu = _execute_uninstrumented(fixture)

    by_class = defaultdict(list)
    for record in records:
        key = f"nodes={record['nodes']};contact={record['contact']};accepted={str(record['accepted']).lower()}"
        by_class[key].append(record["engine_admit"])
    return rows, state, u1, imu, {
        "stage_timing_ms": {key: _summary(value) for key, value in sorted(stage.items())},
        "group_timing_ms": _summary(record["engine_admit"] for record in records),
        "by_group_class_ms": {key: _summary(value) for key, value in sorted(by_class.items())},
        "native_pose_sample_ms": _summary(native_sample),
        "imu_add_ms": _summary(imu_add),
        "groups_detail": records,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        raise RuntimeError("fresh output required")
    if _sha(ROOT / "logs/c2_exact_batched_transition_delivery_revision_002_20260907T211000Z/SHA256SUMS") != IMPLEMENTATION_SEAL:
        raise RuntimeError("implementation seal mismatch")
    fixture = prior_profile._load_fixture()
    rss = [{"stage": "fixture_loaded", "rss_kib": _rss_kib()}]
    started = time.perf_counter()
    baseline_rows, baseline_state, baseline_u1, baseline_imu = _execute_uninstrumented(fixture)
    rss.append({"stage": "uninstrumented_complete", "rss_kib": _rss_kib()})
    rows, state, u1, imu, attribution = _profile(fixture)
    rss.append({"stage": "attributed_complete", "rss_kib": _rss_kib()})
    if not (
        len(rows) == len(baseline_rows) == 41 and imu == baseline_imu == 1007
        and u1 == baseline_u1
        and harness._digest(state) == harness._digest(baseline_state)
        and all(_semantic_digest(a) == _semantic_digest(b)
                for a, b in zip(rows, baseline_rows))
    ):
        raise RuntimeError("profile observer changed authoritative semantics")
    baseline_timing = _summary(row["service_ms"] for row in baseline_rows)
    result = {
        "schema": "biospur.c2.transition-certificate-attributed-profile.v1",
        "status": "PROFILE_ONLY_HOLD",
        "wall_s": time.perf_counter() - started,
        "groups": 41, "imu_total": imu, "u1_calls": u1,
        "uninstrumented_baseline_ms": baseline_timing,
        "uninstrumented_utilization": baseline_timing["mean_ms"] / 120.048,
        "attributed_observer": attribution,
        "rss_milestones": rss,
        "peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        "semantic_digest": harness._digest([_semantic_digest(row) for row in rows]),
        "final_owner_digest": harness._digest(state),
        "observer_semantics_exact": True,
        "raw_opened": False, "hxx_opened": False,
        "implementation_seal_sha256": IMPLEMENTATION_SEAL,
        "source_hashes": {
            "orientation_ik": _sha(ROOT / "src/biospur_fusion/c2_articulated_biomechanics/orientation_ik.py"),
            "causal_pose": _sha(ROOT / "src/biospur_fusion/c2_uwb_calibration/causal_articulated_pose.py"),
            "harness": _sha(ROOT / "tools/audit_c2_articulated_analytic_authoritative.py"),
            "profile": _sha(Path(__file__).resolve()),
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
