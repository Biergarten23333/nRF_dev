#!/usr/bin/env python3
"""One-shot no-raw GIL/overlap probe for four frozen C2 hinge joints.

This tool does not integrate an executor into production.  It captures the exact
25-row correction schedule at the qualified authoritative no-raw boundary and
runs the current per-joint downstream operations in isolated private tasks.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import resource
import sys
import threading
import time
from typing import Any, Mapping

import numpy as np
from scipy.spatial.transform import Rotation

import audit_c2_exact_batched_transition as batch_audit
from biospur_fusion.c2_articulated_biomechanics.model import (
    DOWN,
    hinge_coordinate_deg,
    _minimal_alignment,
)
from biospur_fusion.c2_articulated_biomechanics.orientation_ik import (
    HINGE_ROM_TOLERANCE_DEG,
    _rotation,
    _unsigned_bend_deg,
    _wxyz,
    evaluate_hinge_projection_batch,
)
from biospur_fusion.c2_uwb_calibration import causal_articulated_pose as pose_module


ROOT = Path(__file__).resolve().parents[1]
MODES = (1, 2, 4)
WARMUP_REPETITIONS = 5
MEASURED_REPETITIONS = 40
NOOP_REPETITIONS = 40
THREAD_ENV = {
    "OMP_NUM_THREADS": "1",
    "OPENBLAS_NUM_THREADS": "1",
    "MKL_NUM_THREADS": "1",
    "NUMEXPR_NUM_THREADS": "1",
}
EXPECTED = {
    "orientation_ik": "01bd3de11b7df3dbff32f836a6a6b1526c9bd5f6ceafed726eee6f54a2805df0",
    "causal_pose": "6f389c64831163421608b3c026e2b2bb5d33f035e28fec7eb301a1556f2e856c",
    "restoration_seal": "59589ca546c7823acacccb482d0c1ca1ad78833e118a1b13f0268fa5ec71098c",
    "profile_seal": "a62474360fea8a284e0aee3d00a61cf977312cc56d6d8510f39a43f4d6617aa1",
}
FROZEN_DOWNSTREAM_P99_MS = 47.4966472
FROZEN_COORDINATOR_P99_MS = 162.275
REQUIRED_SAVING_MS = 13.332
TINY_WARMUP_REPETITIONS = 1
TINY_MEASURED_REPETITIONS = 2
TINY_NOOP_REPETITIONS = 2
FUTURE_FORMAL_OUTPUT = (
    ROOT / "logs/c2_joint_parallel_gil_overlap_microprobe_revision_003_20260908T010000Z"
)


class _Captured(RuntimeError):
    pass


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _canonical(value: Any) -> bytes:
    def convert(item: Any) -> Any:
        if isinstance(item, Mapping):
            return {str(key): convert(item[key]) for key in item}
        if isinstance(item, (list, tuple)):
            return [convert(value) for value in item]
        if isinstance(item, np.ndarray):
            array = np.asarray(item)
            return {
                "dtype": array.dtype.str,
                "shape": list(array.shape),
                "c_contiguous": bool(array.flags.c_contiguous),
                "bytes_hex": array.tobytes(order="C").hex(),
            }
        if isinstance(item, (np.integer, np.floating)):
            return item.item()
        return item

    return json.dumps(
        convert(value), ensure_ascii=True, separators=(",", ":")
    ).encode("ascii")


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _validate_sources() -> None:
    actual = {
        "orientation_ik": _sha(
            ROOT / "src/biospur_fusion/c2_articulated_biomechanics/orientation_ik.py"
        ),
        "causal_pose": _sha(
            ROOT / "src/biospur_fusion/c2_uwb_calibration/causal_articulated_pose.py"
        ),
        "restoration_seal": _sha(
            ROOT / "logs/c2_downstream_hinge_projection_kernel_restore_revision_001_20260907T230000Z/SHA256SUMS"
        ),
        "profile_seal": _sha(
            ROOT / "logs/c2_transition_certificate_attributed_profile_delivery_revision_001_20260907T214000Z/SHA256SUMS"
        ),
    }
    if actual != EXPECTED:
        raise RuntimeError(f"qualified source/seal mismatch: {actual}")
    if {name: os.environ.get(name) for name in THREAD_ENV} != THREAD_ENV:
        raise RuntimeError("thread environment must be pinned to one")


def _capture_workload():
    fixture = batch_audit._fixture()
    captured: dict[str, Any] = {}
    original = pose_module.evaluate_hinge_projection_batch

    def capture(base, corrections, model):
        captured["base"] = {
            key: np.asarray(value, dtype=float).reshape(3, 3).copy()
            for key, value in base.items()
        }
        captured["corrections"] = {
            key: np.asarray(value, dtype=float).reshape(-1, 3).copy()
            for key, value in corrections.items()
        }
        captured["model"] = dict(model)
        raise _Captured("qualified projection boundary captured")

    pose_module.evaluate_hinge_projection_batch = capture
    try:
        try:
            batch_audit.harness._execute(
                "analytic", fixture[0], fixture[1], fixture[2], fixture[3],
                fixture[4], fixture[6], fixture[7],
            )
        except _Captured:
            pass
        else:
            raise RuntimeError("qualified projection boundary was not reached")
    finally:
        pose_module.evaluate_hinge_projection_batch = original
    if len(next(iter(captured["corrections"].values()))) != 25:
        raise RuntimeError("captured workload is not the frozen 25-row schedule")
    if tuple(captured["model"]) != (
        "elbow_left", "elbow_right", "knee_left", "knee_right"
    ):
        raise RuntimeError("captured workload does not own the four frozen joints")
    parents = tuple(joint.parent for joint in captured["model"].values())
    children = tuple(joint.child for joint in captured["model"].values())
    if (
        len(set(parents)) != 4
        or len(set(children)) != 4
        or not set(parents).isdisjoint(children)
    ):
        raise RuntimeError("captured hinge model is not structurally independent")
    for collection in (captured["base"], captured["corrections"]):
        for array in collection.values():
            array.setflags(write=False)
    payload = {
        "base": captured["base"],
        "corrections": captured["corrections"],
        "model": {name: asdict(joint) for name, joint in captured["model"].items()},
    }
    return captured["base"], captured["corrections"], captured["model"], _digest(payload)


def _joint_task(
    base: Mapping[str, np.ndarray],
    corrections: Mapping[str, np.ndarray],
    absolute: Mapping[str, np.ndarray],
    name: str,
    joint: Any,
    start_gate: threading.Event,
    pre_delay_s: float = 0.0,
) -> dict[str, Any]:
    start_gate.wait()
    if pre_delay_s:
        time.sleep(pre_delay_s)
    wall_start = time.perf_counter_ns()
    cpu_start = time.thread_time_ns()

    parent_q = _wxyz(Rotation.from_matrix(absolute[joint.parent]))
    child_q = _wxyz(Rotation.from_matrix(absolute[joint.child]))
    count = len(parent_q)
    pre_signed = np.asarray([
        hinge_coordinate_deg(
            parent_q[index:index + 1], child_q[index:index + 1], joint
        )[0]
        for index in range(count)
    ])
    observed = _unsigned_bend_deg(_rotation(parent_q), _rotation(child_q))
    flexion = np.clip(observed, joint.minimum_deg, joint.maximum_deg)
    parent_rotation = _rotation(parent_q)
    child_rotation = _rotation(child_q)
    parent_down = parent_rotation.apply(DOWN)
    child_down = child_rotation.apply(DOWN)
    positive_axis = joint.positive_sign * np.asarray(joint.parent_axis)
    axis_world = np.stack([
        _rotation(parent_q[index:index + 1]).apply(positive_axis[None, :])[0]
        for index in range(count)
    ])
    target_rotvec = axis_world * np.radians(flexion)[:, None]
    target_down = np.stack([
        Rotation.from_rotvec(target_rotvec[index:index + 1]).apply(
            parent_down[index:index + 1]
        )[0]
        for index in range(count)
    ])
    child_projected = _minimal_alignment(child_down, target_down) * child_rotation
    child_projected_q = _wxyz(child_projected)
    child_absolute = _rotation(child_projected_q).as_matrix()
    child_correction = Rotation.from_matrix(np.matmul(
        base[joint.child].T[None, :, :], child_absolute
    )).as_rotvec()
    post_q = _wxyz(Rotation.from_matrix(child_absolute))
    post_signed = np.asarray([
        hinge_coordinate_deg(
            parent_q[index:index + 1], post_q[index:index + 1], joint
        )[0]
        for index in range(count)
    ])
    residual = np.asarray([
        np.degrees(np.arccos(np.clip(np.sum(
            child_projected[index:index + 1].apply(DOWN)
            * target_down[index:index + 1], axis=1
        ), -1.0, 1.0)))[0]
        for index in range(count)
    ])
    metrics = []
    for index in range(count):
        metrics.append({
            "pre_projection_signed_deg": float(pre_signed[index]),
            "post_projection_signed_deg": float(post_signed[index]),
            "pre_projection_below_rom": bool(pre_signed[index] < joint.minimum_deg),
            "pre_projection_above_rom": bool(pre_signed[index] > joint.maximum_deg),
            "post_projection_inside_rom": bool(
                joint.minimum_deg - HINGE_ROM_TOLERANCE_DEG
                <= post_signed[index]
                <= joint.maximum_deg + HINGE_ROM_TOLERANCE_DEG
            ),
            "flexion_deg": float(flexion[index]),
            "fk_direction_residual_deg": float(residual[index]),
            "observed_unsigned_bend_deg": float(observed[index]),
        })
    cpu_end = time.thread_time_ns()
    wall_end = time.perf_counter_ns()
    return {
        "name": name,
        "child": joint.child,
        "child_correction": child_correction,
        "metrics": metrics,
        "wall_start_ns": wall_start,
        "wall_end_ns": wall_end,
        "thread_cpu_ns": cpu_end - cpu_start,
        "thread_native_id": threading.get_native_id(),
    }


def _absolute(base, corrections):
    absolute = {}
    for segment in base:
        # Mirror the qualified evaluator's ownership boundary exactly: the
        # sealed schedule remains read-only, while SciPy receives one
        # solver-local writable C-contiguous copy.
        local = (
            np.asarray(corrections[segment], dtype=float)
            .reshape(-1, 3)
            .copy(order="C")
        )
        absolute[segment] = np.matmul(
            base[segment][None, :, :],
            Rotation.from_rotvec(local).as_matrix(),
        )
    return absolute


def _dry_materialization(expected_input_sha: str) -> dict[str, Any]:
    _validate_sources()
    base, corrections, model, input_digest = _capture_workload()
    if input_digest != expected_input_sha:
        raise RuntimeError("captured input digest mismatch in dry materialization")
    before = {
        "digest": _digest(corrections),
        "flags": {
            key: {
                "writeable": bool(value.flags.writeable),
                "c_contiguous": bool(value.flags.c_contiguous),
                "shape": list(value.shape),
                "dtype": value.dtype.str,
                "strides": list(value.strides),
            }
            for key, value in corrections.items()
        },
    }
    observed_by_mode = {
        workers: _absolute(base, corrections) for workers in MODES
    }
    observed = observed_by_mode[MODES[0]]
    writable_reference = _absolute(
        base,
        {
            key: np.asarray(value, dtype=float).copy(order="C")
            for key, value in corrections.items()
        },
    )
    after = {
        "digest": _digest(corrections),
        "flags": {
            key: {
                "writeable": bool(value.flags.writeable),
                "c_contiguous": bool(value.flags.c_contiguous),
                "shape": list(value.shape),
                "dtype": value.dtype.str,
                "strides": list(value.strides),
            }
            for key, value in corrections.items()
        },
    }
    reference_digest = _digest(writable_reference)
    exact = all(
        _digest(value) == reference_digest for value in observed_by_mode.values()
    )
    unchanged = before == after and all(
        value.flags.writeable is False for value in corrections.values()
    )
    if not exact or not unchanged:
        raise RuntimeError("dry materialization ownership/parity failure")
    return {
        "schema": "biospur.c2.joint-parallel-gil-overlap-probe.dry-materialization.v1",
        "status": "DRY_MATERIALIZATION_PASS",
        "input_sha256": input_digest,
        "schedule_sha256": before["digest"],
        "absolute_sha256": _digest(observed),
        "writable_reference_sha256": _digest(writable_reference),
        "output_exact": exact,
        "sealed_owner_unchanged": unchanged,
        "sealed_owner_flags": after["flags"],
        "tasks_submitted": 0,
        "modes_reaching_pre_task_absolute": list(MODES),
        "warmup_repetitions": 0,
        "measured_repetitions": 0,
        "noop_repetitions": 0,
        "executor_constructed": False,
        "raw_opened": False,
        "hxx_opened": False,
    }


def _merge(base_corrections, model, rows):
    corrections = {
        segment: np.asarray(value).copy() for segment, value in base_corrections.items()
    }
    by_name = {row["name"]: row for row in rows}
    metrics = [dict() for _ in range(len(next(iter(corrections.values()))))]
    for name, joint in model.items():
        row = by_name[name]
        corrections[joint.child] = row["child_correction"]
        for index, values in enumerate(row["metrics"]):
            metrics[index][name] = values
    projected = [
        {segment: corrections[segment][index].copy() for segment in corrections}
        for index in range(len(metrics))
    ]
    projections = []
    for joint_rows in metrics:
        projections.append({
            "joint": joint_rows,
            "pre_projection_below_rom_count": sum(
                row["pre_projection_below_rom"] for row in joint_rows.values()
            ),
            "pre_projection_above_rom_count": sum(
                row["pre_projection_above_rom"] for row in joint_rows.values()
            ),
            "post_projection_all_inside_rom": all(
                row["post_projection_inside_rom"] for row in joint_rows.values()
            ),
            "fk_direction_residual_maximum_deg": max(
                row["fk_direction_residual_deg"] for row in joint_rows.values()
            ),
        })
    return projected, projections


def _run_once(executor, workers, base, corrections, model, absolute, delays=None):
    gate = threading.Event()
    process_start = time.process_time_ns()
    wall_start = time.perf_counter_ns()
    futures = []
    for index, (name, joint) in enumerate(model.items()):
        delay = 0.0 if delays is None else delays[index]
        futures.append(executor.submit(
            _joint_task, base, corrections, absolute, name, joint, gate, delay
        ))
    gate.set()
    completion = []
    future_names = {future: name for future, name in zip(futures, model)}
    for future in as_completed(futures):
        completion.append(future_names[future])
    rows = [future.result() for future in futures]
    wall_end = time.perf_counter_ns()
    process_end = time.process_time_ns()
    starts = [row["wall_start_ns"] for row in rows]
    ends = [row["wall_end_ns"] for row in rows]
    task_wall = sum(end - start for start, end in zip(starts, ends))
    union = max(ends) - min(starts)
    return {
        "wall_ms": (wall_end - wall_start) * 1e-6,
        "process_cpu_ms": (process_end - process_start) * 1e-6,
        "thread_cpu_ms": sum(row["thread_cpu_ns"] for row in rows) * 1e-6,
        "task_union_ms": union * 1e-6,
        "task_wall_sum_ms": task_wall * 1e-6,
        "overlap_fraction": 0.0 if task_wall == 0 else 1.0 - union / task_wall,
        "mean_concurrency": 0.0 if union == 0 else task_wall / union,
        "thread_native_ids": sorted({row["thread_native_id"] for row in rows}),
        "completion_order": completion,
        "merged": _merge(corrections, model, rows),
    }


def _noop_once(executor):
    gate = threading.Event()

    def task():
        gate.wait()
        return threading.get_native_id()

    started = time.perf_counter_ns()
    futures = [executor.submit(task) for _ in range(4)]
    gate.set()
    for future in futures:
        future.result()
    return (time.perf_counter_ns() - started) * 1e-6


def _summary(values):
    array = np.asarray(tuple(values), dtype=float)
    if array.size == 0:
        raise ValueError("summary requires at least one value")
    return {
        "count": int(len(array)),
        "mean": float(np.mean(array)),
        "p50": float(np.percentile(array, 50)),
        "p99": float(np.percentile(array, 99)),
        "maximum": float(np.max(array)),
    }


def _mode(
    workers,
    base,
    corrections,
    model,
    reference_digest,
    input_digest,
    *,
    warmup_repetitions=WARMUP_REPETITIONS,
    measured_repetitions=MEASURED_REPETITIONS,
    noop_repetitions=NOOP_REPETITIONS,
):
    threads_before = sorted(thread.name for thread in threading.enumerate())
    construct_start = time.perf_counter_ns()
    executor = ThreadPoolExecutor(
        max_workers=workers, thread_name_prefix=f"c2-joint-overlap-{workers}"
    )
    construct_ms = (time.perf_counter_ns() - construct_start) * 1e-6
    try:
        absolute = _absolute(base, corrections)
        warm_started = time.perf_counter_ns()
        for _ in range(warmup_repetitions):
            row = _run_once(
                executor, workers, base, corrections, model, absolute
            )
            if _digest(row["merged"]) != reference_digest:
                raise RuntimeError("warmup output mismatch")
        warm_ms = (time.perf_counter_ns() - warm_started) * 1e-6

        records = []
        for _ in range(measured_repetitions):
            row = _run_once(
                executor, workers, base, corrections, model, absolute
            )
            if _digest(row["merged"]) != reference_digest:
                raise RuntimeError("measured output mismatch")
            if _digest({
                "base": base,
                "corrections": corrections,
                "model": {name: asdict(joint) for name, joint in model.items()},
            }) != input_digest:
                raise RuntimeError("immutable input changed")
            row.pop("merged")
            records.append(row)

        noops = [_noop_once(executor) for _ in range(noop_repetitions)]
        reverse = _run_once(
            executor, workers, base, corrections, model, absolute,
            delays=(0.003, 0.002, 0.001, 0.0),
        )
        if _digest(reverse["merged"]) != reference_digest:
            raise RuntimeError("completion-order output mismatch")
        reverse.pop("merged")
    finally:
        close_started = time.perf_counter_ns()
        executor.shutdown(wait=True, cancel_futures=True)
        close_ms = (time.perf_counter_ns() - close_started) * 1e-6
    leaked = sorted(
        thread.name for thread in threading.enumerate()
        if thread.name.startswith(f"c2-joint-overlap-{workers}")
    )
    if leaked:
        raise RuntimeError(f"worker thread leak: {leaked}")
    return {
        "workers": workers,
        "executor_construct_ms": construct_ms,
        "warmup_repetitions": warmup_repetitions,
        "warmup_total_ms": warm_ms,
        "measured_repetitions": measured_repetitions,
        "noop_repetitions": noop_repetitions,
        "wall_ms": _summary(row["wall_ms"] for row in records),
        "process_cpu_ms": _summary(row["process_cpu_ms"] for row in records),
        "thread_cpu_ms": _summary(row["thread_cpu_ms"] for row in records),
        "task_union_ms": _summary(row["task_union_ms"] for row in records),
        "task_wall_sum_ms": _summary(row["task_wall_sum_ms"] for row in records),
        "overlap_fraction": _summary(row["overlap_fraction"] for row in records),
        "mean_concurrency": _summary(row["mean_concurrency"] for row in records),
        "noop_submit_gather_ms": _summary(noops),
        "observed_thread_counts": sorted({
            len(row["thread_native_ids"]) for row in records
        }),
        "completion_orders": sorted({
            tuple(row["completion_order"]) for row in records
        }),
        "forced_reverse_completion_order": reverse["completion_order"],
        "forced_reverse_digest_exact": True,
        "executor_close_ms": close_ms,
        "threads_before": threads_before,
        "leaked_threads": leaked,
    }


def _write_tiny_evidence(output: Path, result: Mapping[str, Any]) -> str:
    output.mkdir(parents=True)
    members = {
        "RESULT.json": json.dumps(result, indent=2, sort_keys=True) + "\n",
        "COMMAND.txt": " ".join(sys.argv) + "\n",
        "VERIFY_CWD.txt": "Verify from this directory:\n\nsha256sum -c SHA256SUMS\n",
    }
    for name, content in members.items():
        output.joinpath(name).write_text(content)
    lines = [
        f"{_sha(output / name)}  {name}" for name in sorted(members)
    ]
    output.joinpath("SHA256SUMS").write_text("\n".join(lines) + "\n")
    for line in lines:
        expected, name = line.split("  ", 1)
        if _sha(output / name) != expected:
            raise RuntimeError(f"tiny evidence self-verification failed: {name}")
    return _sha(output / "SHA256SUMS")


def _tiny_run(expected_input_sha: str, output: Path) -> dict[str, Any]:
    if output.exists():
        raise RuntimeError("fresh tiny output required")
    if output.resolve() == FUTURE_FORMAL_OUTPUT.resolve():
        raise RuntimeError("tiny mode cannot write the future formal path")
    if "tiny" not in output.name.lower():
        raise RuntimeError("tiny mode requires a visibly tiny-only output path")
    _validate_sources()
    base, corrections, model, input_digest = _capture_workload()
    if input_digest != expected_input_sha:
        raise RuntimeError("captured input digest mismatch in tiny mode")
    reference_digest = _digest(
        evaluate_hinge_projection_batch(base, corrections, model)
    )
    modes = [
        _mode(
            workers,
            base,
            corrections,
            model,
            reference_digest,
            input_digest,
            warmup_repetitions=TINY_WARMUP_REPETITIONS,
            measured_repetitions=TINY_MEASURED_REPETITIONS,
            noop_repetitions=TINY_NOOP_REPETITIONS,
        )
        for workers in MODES
    ]
    if [row["workers"] for row in modes] != list(MODES):
        raise RuntimeError("tiny mode order mismatch")
    for row in modes:
        if (
            row["warmup_repetitions"] != TINY_WARMUP_REPETITIONS
            or row["measured_repetitions"] != TINY_MEASURED_REPETITIONS
            or row["noop_repetitions"] != TINY_NOOP_REPETITIONS
            or row["wall_ms"]["count"] != TINY_MEASURED_REPETITIONS
            or row["noop_submit_gather_ms"]["count"] != TINY_NOOP_REPETITIONS
            or row["leaked_threads"]
        ):
            raise RuntimeError("tiny mode inventory/cleanup mismatch")
    result = {
        "schema": "biospur.c2.joint-parallel-gil-overlap-probe.tiny-dry.v1",
        "status": "NON_PROMOTABLE_TINY_DRY_PASS",
        "formal_eligible": False,
        "formal_projection_evaluated": False,
        "pid": os.getpid(),
        "modes": modes,
        "mode_order": list(MODES),
        "warmup_repetitions": TINY_WARMUP_REPETITIONS,
        "measured_repetitions": TINY_MEASURED_REPETITIONS,
        "noop_repetitions": TINY_NOOP_REPETITIONS,
        "formal_counts": {
            "warmup_repetitions": WARMUP_REPETITIONS,
            "measured_repetitions": MEASURED_REPETITIONS,
            "noop_repetitions": NOOP_REPETITIONS,
        },
        "input_sha256": input_digest,
        "reference_output_sha256": reference_digest,
        "all_outputs_exact": True,
        "input_immutable": True,
        "threads_cleaned": True,
        "raw_opened": False,
        "hxx_opened": False,
        "production_edited": False,
    }
    seal = _write_tiny_evidence(output, result)
    return {**result, "evidence_seal_sha256": seal, "self_verified": True}


def _run(expected_input_sha: str, output: Path) -> dict[str, Any]:
    total_wall_start = time.perf_counter_ns()
    total_cpu_start = time.process_time_ns()
    if output.exists():
        raise RuntimeError("fresh output required")
    _validate_sources()
    pid = os.getpid()
    base, corrections, model, input_digest = _capture_workload()
    if input_digest != expected_input_sha:
        raise RuntimeError(
            f"captured input digest mismatch: {input_digest} != {expected_input_sha}"
        )
    reference = evaluate_hinge_projection_batch(base, corrections, model)
    reference_digest = _digest(reference)
    modes = [
        _mode(workers, base, corrections, model, reference_digest, input_digest)
        for workers in MODES
    ]
    by_workers = {row["workers"]: row for row in modes}
    one = by_workers[1]["wall_ms"]["p99"]
    four = by_workers[4]["wall_ms"]["p99"]
    measured_ratio = four / one
    projected_downstream = FROZEN_DOWNSTREAM_P99_MS * measured_ratio
    projected_saving = FROZEN_DOWNSTREAM_P99_MS - projected_downstream
    projected_coordinator = FROZEN_COORDINATOR_P99_MS - projected_saving
    architecture_sufficient = bool(projected_saving >= REQUIRED_SAVING_MS)
    result = {
        "schema": "biospur.c2.joint-parallel-gil-overlap-probe.v1",
        "status": (
            "EVIDENCE_ONLY_SUFFICIENT_HOLD" if architecture_sufficient
            else "BLOCKED_AT_JOINT_PARALLEL_MEASURED_SAVING"
        ),
        "pid": pid,
        "modes_predeclared": list(MODES),
        "warmup_repetitions": WARMUP_REPETITIONS,
        "measured_repetitions": MEASURED_REPETITIONS,
        "noop_repetitions": NOOP_REPETITIONS,
        "input_sha256": input_digest,
        "reference_output_sha256": reference_digest,
        "all_outputs_exact": True,
        "input_immutable": True,
        "modes": modes,
        "projection": {
            "worker1_p99_ms": one,
            "worker4_p99_ms": four,
            "measured_ratio": measured_ratio,
            "frozen_downstream_p99_ms": FROZEN_DOWNSTREAM_P99_MS,
            "projected_downstream_p99_ms": projected_downstream,
            "projected_saving_ms": projected_saving,
            "required_saving_ms": REQUIRED_SAVING_MS,
            "frozen_coordinator_p99_ms": FROZEN_COORDINATOR_P99_MS,
            "projected_coordinator_p99_ms": projected_coordinator,
            "architecture_sufficient": architecture_sufficient,
        },
        "total_wall_s": (time.perf_counter_ns() - total_wall_start) * 1e-9,
        "total_process_cpu_s": (time.process_time_ns() - total_cpu_start) * 1e-9,
        "peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        "raw_opened": False,
        "hxx_opened": False,
        "production_edited": False,
        "candidate_integrated": False,
        "source_hashes": {
            **EXPECTED,
            "probe": _sha(Path(__file__).resolve()),
        },
    }
    output.mkdir(parents=True)
    output.joinpath("RESULT.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n"
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--describe-input", action="store_true")
    parser.add_argument("--dry-materialization", action="store_true")
    parser.add_argument("--tiny-dry", action="store_true")
    parser.add_argument("--expected-input-sha256")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    _validate_sources()
    if sum((args.describe_input, args.dry_materialization, args.tiny_dry)) > 1:
        raise RuntimeError("choose one dry mode")
    if args.describe_input:
        if args.expected_input_sha256 is not None or args.output is not None:
            raise RuntimeError("describe-input does not accept run arguments")
        base, corrections, model, digest = _capture_workload()
        print(json.dumps({
            "input_sha256": digest,
            "samples": len(next(iter(corrections.values()))),
            "segments": len(corrections),
            "joints": list(model),
            "base_sha256": _digest(base),
            "schedule_sha256": _digest(corrections),
        }, sort_keys=True))
        return 0
    if args.dry_materialization:
        if args.expected_input_sha256 is None or args.output is not None:
            raise RuntimeError(
                "dry-materialization requires expected SHA and no output"
            )
        print(json.dumps(
            _dry_materialization(args.expected_input_sha256), sort_keys=True
        ))
        return 0
    if args.tiny_dry:
        if args.expected_input_sha256 is None or args.output is None:
            raise RuntimeError("tiny-dry requires expected SHA and output")
        result = _tiny_run(
            args.expected_input_sha256, args.output.resolve()
        )
        print(json.dumps(result, sort_keys=True))
        return 0
    if args.expected_input_sha256 is None or args.output is None:
        raise RuntimeError("run requires expected input SHA and output")
    result = _run(args.expected_input_sha256, args.output.resolve())
    print(json.dumps(result, sort_keys=True))
    return 0 if result["projection"]["architecture_sufficient"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
