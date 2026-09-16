#!/usr/bin/env python3
"""No-raw exact scalar/batch audit for the causal hinge certificate owner."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import resource
import time

import numpy as np

import audit_c2_articulated_analytic_authoritative as harness
from biospur_fusion.c2_articulated_biomechanics.orientation_ik import (
    evaluate_hinge_projection_batch,
    project_hinge_corrections,
)
from biospur_fusion.c2_articulated_biomechanics import orientation_ik
from biospur_fusion.c2_uwb_calibration import causal_articulated_pose as pose_module
from biospur_fusion.c2_uwb_calibration.causal_articulated_pose import (
    CausalArticulatedPose,
)


ROOT = Path(__file__).resolve().parents[1]
GROUPS = 41
IMU_TOTAL = 1007


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


def _fixture():
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


def _scalar_batch_oracle(base, correction_batch, model):
    projected = []
    projections = []
    for index in range(len(next(iter(correction_batch.values())))):
        row, metrics = project_hinge_corrections(
            base,
            {segment: value[index] for segment, value in correction_batch.items()},
            model,
        )
        projected.append(row)
        projections.append(metrics)
    return projected, projections


def _semantic_digest(row) -> str:
    return harness._digest({key: value for key, value in row.items() if key != "service_ms"})


def _summary(values) -> dict[str, float]:
    values = np.asarray(tuple(values), dtype=float)
    return {
        "count": int(len(values)),
        "mean_ms": float(np.mean(values)),
        "p99_ms": float(np.percentile(values, 99)),
        "maximum_ms": float(np.max(values)),
    }


def _execute_with(evaluator, fixture, certificate_times):
    def observed(*args, **kwargs):
        started = time.perf_counter_ns()
        try:
            return evaluator(*args, **kwargs)
        finally:
            certificate_times.append((time.perf_counter_ns() - started) * 1e-6)

    previous = pose_module.evaluate_hinge_projection_batch
    pose_module.evaluate_hinge_projection_batch = observed
    try:
        return harness._execute(
            "analytic", fixture[0], fixture[1], fixture[2], fixture[3],
            fixture[4], fixture[6], fixture[7],
        )
    finally:
        pose_module.evaluate_hinge_projection_batch = previous


def _parity() -> dict:
    fixture = _fixture()
    scalar_times = []
    scalar_rows, scalar_state, scalar_u1, scalar_imu = _execute_with(
        _scalar_batch_oracle, fixture, scalar_times
    )
    batch_times = []
    batch_rows, batch_state, batch_u1, batch_imu = _execute_with(
        evaluate_hinge_projection_batch, fixture, batch_times
    )
    if not (
        len(scalar_rows) == len(batch_rows) == GROUPS
        and scalar_u1 == batch_u1
        and scalar_imu == batch_imu == IMU_TOTAL
    ):
        raise RuntimeError("authoritative scalar/batch inventory mismatch")
    harness._compare(scalar_rows, batch_rows)
    mismatches = [
        index for index, (scalar, batch) in enumerate(zip(scalar_rows, batch_rows))
        if _semantic_digest(scalar) != _semantic_digest(batch)
    ]
    if mismatches or harness._digest(scalar_state) != harness._digest(batch_state):
        raise RuntimeError(f"authoritative scalar/batch byte mismatch: {mismatches}")
    group_timing = _summary(row["service_ms"] for row in batch_rows)
    return {
        "mode": "exact_parity",
        "groups": GROUPS,
        "imu_total": batch_imu,
        "u1_calls": batch_u1,
        "scalar_certificate_timing": _summary(scalar_times),
        "batch_certificate_timing": _summary(batch_times),
        "batch_group_timing": group_timing,
        "effective_utilization": group_timing["mean_ms"] / 120.048,
        "group_semantic_digest": harness._digest([
            _semantic_digest(row) for row in batch_rows
        ]),
        "final_owner_digest": harness._digest(batch_state),
        "accepted_groups": sum(row["result"]["accepted"] for row in batch_rows),
        "rejected_groups": sum(not row["result"]["accepted"] for row in batch_rows),
        "node_contact_outcomes": [
            {
                "group": row["group"],
                "nodes": row["node_count_requested"],
                "contact": row["contact_mode"],
                "accepted": row["result"]["accepted"],
                "reason": row["result"]["reason"],
            }
            for row in batch_rows
        ],
    }


def _memory() -> dict:
    milestones = [{"name": "process_after_imports", "rss_kib": _rss_kib()}]
    fixture = _fixture()
    milestones.append({"name": "sealed_fixture", "rss_kib": _rss_kib()})
    observed = {"capture": True, "hinge_calls": 0, "q_calls": 0}
    original_planned = CausalArticulatedPose._planned_hinge_transition
    original_evaluator = pose_module.evaluate_hinge_projection_batch
    original_hinge = orientation_ik.hinge_coordinate_deg
    original_extract = pose_module.extract_public_hinge_q_rad

    def planned(self, *args, **kwargs):
        if observed["capture"]:
            milestones.append({"name": "prepare_entry", "rss_kib": _rss_kib()})
        result = original_planned(self, *args, **kwargs)
        if observed["capture"]:
            milestones.append({
                "name": "evidence_constructed_and_temporaries_released",
                "rss_kib": _rss_kib(),
            })
            observed["capture"] = False
        return result

    def evaluator(*args, **kwargs):
        if observed["capture"]:
            milestones.append({
                "name": "scalar_byte_schedule_and_so3_inputs_ready",
                "rss_kib": _rss_kib(),
            })
        result = original_evaluator(*args, **kwargs)
        if observed["capture"]:
            milestones.append({
                "name": "pure_batch_projection_returned",
                "rss_kib": _rss_kib(),
            })
        return result

    def hinge(*args, **kwargs):
        if observed["capture"] and observed["hinge_calls"] in (0, 50, 100, 150):
            milestones.append({
                "name": f"per_joint_projection_{observed['hinge_calls'] // 50}_entry",
                "rss_kib": _rss_kib(),
            })
        observed["hinge_calls"] += 1
        return original_hinge(*args, **kwargs)

    def extract(*args, **kwargs):
        if observed["capture"] and observed["q_calls"] == 0:
            milestones.append({"name": "q_metrics_entry", "rss_kib": _rss_kib()})
        result = original_extract(*args, **kwargs)
        observed["q_calls"] += 1
        if observed["capture"] and observed["q_calls"] == 25:
            milestones.append({"name": "q_metrics_complete", "rss_kib": _rss_kib()})
        return result

    CausalArticulatedPose._planned_hinge_transition = planned
    pose_module.evaluate_hinge_projection_batch = evaluator
    orientation_ik.hinge_coordinate_deg = hinge
    pose_module.extract_public_hinge_q_rad = extract
    times = []
    try:
        rows, final_state, u1_calls, imu_count = harness._execute(
            "analytic", fixture[0], fixture[1], fixture[2], fixture[3],
            fixture[4], fixture[6], fixture[7],
        )
    finally:
        CausalArticulatedPose._planned_hinge_transition = original_planned
        pose_module.evaluate_hinge_projection_batch = original_evaluator
        orientation_ik.hinge_coordinate_deg = original_hinge
        pose_module.extract_public_hinge_q_rad = original_extract
    milestones.append({"name": "after_41_group_1007", "rss_kib": _rss_kib()})
    return {
        "mode": "low_overhead_rss",
        "groups": len(rows),
        "imu_total": imu_count,
        "u1_calls": u1_calls,
        "milestones": milestones,
        "peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        "final_owner_digest": harness._digest(final_state),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("parity", "memory"), required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        raise RuntimeError("fresh output required")
    result = _parity() if args.mode == "parity" else _memory()
    result.update({
        "schema": "biospur.c2.exact-batched-transition-audit.v1",
        "raw_opened": False,
        "hxx_opened": False,
        "orientation_ik_sha256": _sha(ROOT / "src/biospur_fusion/c2_articulated_biomechanics/orientation_ik.py"),
        "causal_pose_sha256": _sha(ROOT / "src/biospur_fusion/c2_uwb_calibration/causal_articulated_pose.py"),
        "harness_sha256": _sha(ROOT / "tools/audit_c2_articulated_analytic_authoritative.py"),
    })
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
