#!/usr/bin/env python3
"""Run the bounded, root-only continuous Capture2 00--19 A/B diagnostic."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import importlib.metadata
import json
from pathlib import Path
import resource
import sys
import time

import numpy as np

from biospur_fusion.c2_uwb_root_world.continuous_full_session import (
    ContinuousUwbInstrumentation,
    FULL_SESSION_SCHEMA,
    evaluate_anti_drift_gates,
    load_continuous_session_inventory,
    maximum_adjacent_position_jump,
    summarize_root_trajectory,
    validate_result_schema,
)
from biospur_fusion.c2_uwb_root_world.continuous_root_ab import (
    ContinuousRootAB,
    PelvisContinuousVQF,
    admit_uwb_timestamp,
    bootstrap_action00_root,
    uwb_row_from_event,
)
from biospur_fusion.c2_uwb_root_world.pelvis_monotone_merge import (
    CompleteWindowBarrier,
    PelvisTwoStreamMonotoneMerge,
)
from biospur_fusion.c2_uwb_root_world.run_calibration import LAYOUT, _clock_models
from biospur_fusion.ingest.v47 import _imu_events, _uwb_event

try:
    from fusion_host_binary import decode_frame
except ImportError:
    from B306_Part.tools.fusion_host_binary import decode_frame


ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / (
    "datasets/phase2_calibration/"
    "phase2_targeted_calibration_20260817t130918z_capture_2_with_joint_label_c8645eb2/"
    "system/fusion_continuous/fusion_host_raw.cobs.bin"
)
CLOCK = ROOT / "logs/c2_uwb_beacon_clock_20260903_141552/CLOCK_TABLE_CALIBRATION_ONLY.json"
PELVIS = "BSFC2CC"
RAW_CONTAINER_SHA256_DECLARED = "74c1fdbbe7c302bc21b0665bff50137e84537946a347ea11133e1e6751c84268"
PREPARATION_LOOKBACK_NS = 5_100_000_000
MERGE_CAPACITY = 10_000
MAX_RECORDS = 750_000
MAX_DECODED_EVENTS = 4_000_000
MAX_SELECTED_PELVIS_EVENTS = 400_000
MAX_TRAJECTORY_SAMPLES = 300_000
MAX_OUTPUT_BYTES = 1 << 30
RUNTIME_MANIFEST_SCHEMA = "biospur.c2.continuous_root_ab.runtime_manifest.v1"
FORMAL_FREEZE_MANIFEST = RAW.parents[2] / "checksums/SHA256SUMS.txt"
FORMAL_FREEZE_MANIFEST_SHA256 = "c2682d5dad06feb4edea801ebc8981c324d20c2c0f515be9de33ce03ac743417"


def runtime_required_files() -> dict[str, Path]:
    package_record = lambda name: Path(importlib.metadata.distribution(name)._path) / "RECORD"
    required = {
        "full_runner": Path(__file__).resolve(),
        "continuous_full_session": ROOT / "src/biospur_fusion/c2_uwb_root_world/continuous_full_session.py",
        "continuous_root_ab": ROOT / "src/biospur_fusion/c2_uwb_root_world/continuous_root_ab.py",
        "pelvis_monotone_merge": ROOT / "src/biospur_fusion/c2_uwb_root_world/pelvis_monotone_merge.py",
        "run_calibration": ROOT / "src/biospur_fusion/c2_uwb_root_world/run_calibration.py",
        "beacon_clock": ROOT / "src/biospur_fusion/c2_uwb_root_world/beacon_clock.py",
        "tight_range": ROOT / "src/biospur_fusion/c2_uwb_root_world/tight_range.py",
        "u0": ROOT / "src/biospur_fusion/c2_uwb_root_world/u0.py",
        "calibration": ROOT / "src/biospur_fusion/c2_uwb_root_world/calibration.py",
        "shared_root": ROOT / "src/biospur_fusion/c2_uwb_calibration/shared_root.py",
        "root_estimator": ROOT / "src/biospur_fusion/root_r3/estimator.py",
        "root_models": ROOT / "src/biospur_fusion/root_r3/models.py",
        "ingest_events": ROOT / "src/biospur_fusion/ingest/events.py",
        "ingest_v47": ROOT / "src/biospur_fusion/ingest/v47.py",
        "continuous_frontend": ROOT / "src/biospur_fusion/c2_coupled_progressive/continuous_frontend.py",
        "common_clock": ROOT / "src/biospur_fusion/time/common_clock.py",
        "uwb_frontend": ROOT / "src/biospur_fusion/uwb/frontend.py",
        "uwb_canonical_t4": ROOT / "src/biospur_fusion/uwb/canonical_t4.py",
        "fusion_host_binary": ROOT.parent / "B306_Part/tools/fusion_host_binary.py",
        "clock_table": CLOCK,
        "anchor_layout": LAYOUT,
        "action_audit": ROOT / (
            "logs/c2_five_node_pure_imu_v2_20260906_102300/"
            "CALIBRATION_INPUT_AUDIT.json"
        ),
        "formal_freeze_manifest": FORMAL_FREEZE_MANIFEST,
        "python_interpreter": Path(sys.executable).resolve(),
        "qmt_distribution_record": package_record("qmt"),
        "numpy_distribution_record": package_record("numpy"),
        "scipy_distribution_record": package_record("scipy"),
    }
    already_bound = {path.resolve() for path in required.values()}
    local_roots = (ROOT / "src", ROOT.parent / "B306_Part/tools")
    for name, module in sorted(sys.modules.items()):
        module_file = getattr(module, "__file__", None)
        if module_file is None:
            continue
        candidate = Path(module_file).resolve()
        if candidate in already_bound or not any(
            candidate.is_relative_to(local_root) for local_root in local_roots
        ):
            continue
        required[f"loaded_module:{name}"] = candidate
        already_bound.add(candidate)
    return required


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(4 << 20):
            digest.update(block)
    return digest.hexdigest()


def _verify_runtime_manifest(path: Path) -> dict[str, object]:
    """Fail closed on the frozen runtime/input identity before RAW is opened."""

    manifest = json.loads(path.read_text())
    if manifest.get("schema") != RUNTIME_MANIFEST_SCHEMA:
        raise RuntimeError("runtime manifest schema mismatch")
    raw = manifest.get("raw_container", {})
    if Path(str(raw.get("path", ""))).resolve() != RAW.resolve():
        raise RuntimeError("runtime manifest names a different raw container")
    raw_stat = RAW.stat()
    if int(raw.get("size_bytes", -1)) != raw_stat.st_size:
        raise RuntimeError("raw container size differs from the preregistered identity")
    if raw.get("declared_sha256") != RAW_CONTAINER_SHA256_DECLARED:
        raise RuntimeError("raw container declared SHA-256 differs from the frozen identity")
    stat_identity = raw.get("stat_identity", {})
    observed_stat_identity = {
        "device": raw_stat.st_dev,
        "inode": raw_stat.st_ino,
        "size_bytes": raw_stat.st_size,
        "mtime_ns": raw_stat.st_mtime_ns,
    }
    if stat_identity != observed_stat_identity:
        raise RuntimeError("raw container filesystem identity changed after preregistration")
    if _sha256(FORMAL_FREEZE_MANIFEST) != FORMAL_FREEZE_MANIFEST_SHA256:
        raise RuntimeError("formal dataset freeze manifest hash mismatch")
    frozen_line = (
        f"{RAW_CONTAINER_SHA256_DECLARED}  "
        "system/fusion_continuous/fusion_host_raw.cobs.bin"
    )
    if frozen_line not in FORMAL_FREEZE_MANIFEST.read_text().splitlines():
        raise RuntimeError("formal dataset freeze manifest does not bind the raw container")
    rows = manifest.get("files")
    if not isinstance(rows, list) or not rows:
        raise RuntimeError("runtime manifest has no file inventory")
    required = {role: candidate.resolve() for role, candidate in runtime_required_files().items()}
    seen: dict[str, Path] = {}
    for row in rows:
        role = str(row.get("role", ""))
        candidate = Path(str(row["path"])).resolve()
        if candidate == RAW.resolve():
            raise RuntimeError("runtime manifest must not re-read the raw container")
        if role in seen:
            raise RuntimeError(f"duplicate runtime manifest role: {role}")
        seen[role] = candidate
        if not candidate.is_file():
            raise RuntimeError(f"runtime dependency missing: {candidate}")
        if _sha256(candidate) != row.get("sha256"):
            raise RuntimeError(f"runtime dependency hash mismatch: {candidate}")
    if seen != required:
        missing = sorted(set(required) - set(seen))
        extra = sorted(set(seen) - set(required))
        changed = sorted(role for role in set(seen) & set(required) if seen[role] != required[role])
        raise RuntimeError(
            f"runtime manifest inventory mismatch: missing={missing} extra={extra} changed={changed}"
        )
    return {
        "path": str(path.resolve()),
        "sha256": _sha256(path),
        "verified_files": len(seen),
    }


def _anchors() -> np.ndarray:
    rows = sorted(json.loads(LAYOUT.read_text())["anchors"], key=lambda row: int(row["id"]))
    if [int(row["id"]) for row in rows] != list(range(8)):
        raise RuntimeError("canonical A-H anchor inventory changed")
    return np.asarray(
        [[row["x_mm"], row["y_mm"], row["z_mm"]] for row in rows], dtype=float,
    ) / 1000.0


def _empty_region_counter() -> dict[str, object]:
    return {
        "pelvis_imu": 0,
        "pelvis_uwb": 0,
        "uwb_eligible": 0,
        "uwb_ineligible": 0,
        "uwb_bootstrap": 0,
        "uwb_accepted": 0,
        "uwb_rejected": 0,
        "uwb_reasons": Counter(),
        "trajectory_samples": 0,
    }


def _json_counter(value: dict[str, object]) -> dict[str, object]:
    result = dict(value)
    result["uwb_reasons"] = dict(sorted(result["uwb_reasons"].items()))
    return result


def _trajectory_for_region(
    indices: list[int], positions: np.ndarray, velocities: np.ndarray, anchors: np.ndarray,
) -> dict[str, object] | None:
    if not indices:
        return None
    selection = np.asarray(indices, dtype=int)
    return summarize_root_trajectory(positions[selection], velocities[selection], anchors)


def run(output: Path, runtime_manifest: Path) -> dict[str, object]:
    manifest_verification = _verify_runtime_manifest(runtime_manifest)
    if output.exists():
        raise FileExistsError(output)
    output.mkdir(parents=True)
    started = time.monotonic()
    inventory = load_continuous_session_inventory(ROOT)
    clocks = _clock_models(CLOCK)
    clock = clocks[PELVIS]
    clock_doc = json.loads(CLOCK.read_text())
    boots = {node: int(value["boot_epoch"]) for node, value in clock_doc["models"].items()}
    anchors = _anchors()
    vqf = PelvisContinuousVQF()
    ab: ContinuousRootAB | None = None
    uwb_instrumentation: ContinuousUwbInstrumentation | None = None
    merge = PelvisTwoStreamMonotoneMerge(
        node=PELVIS,
        boot_epoch=clock.boot_epoch,
        source_sha256=RAW_CONTAINER_SHA256_DECLARED,
        window_end_offset=inventory.stop_offset,
        capacity=MERGE_CAPACITY,
    )

    region_hashes = [hashlib.sha256() for _ in inventory.regions]
    whole_hash = hashlib.sha256()
    region_counts = [_empty_region_counter() for _ in inventory.regions]
    region_output_indices: list[list[int]] = [[] for _ in inventory.regions]
    admission_reasons: Counter[str] = Counter()
    decision_reasons: Counter[str] = Counter()
    entered_labels: list[dict[str, object]] = []
    root_label_cursor = 0
    label_cursor = 0
    records = decoded_events = selected_events = 0
    source_pelvis_imu = source_pelvis_uwb = 0
    bootstrap_count = 0
    pending = bytearray()
    pending_start = inventory.start_offset
    times: list[float] = []
    times_ns: list[int] = []
    positions_a: list[np.ndarray] = []
    positions_b: list[np.ndarray] = []
    velocities_a: list[np.ndarray] = []
    velocities_b: list[np.ndarray] = []

    def advance_labels(measurement_ns: int) -> None:
        nonlocal label_cursor, root_label_cursor
        while (
            label_cursor < len(inventory.labels)
            and inventory.labels[label_cursor].enter_ns <= measurement_ns
        ):
            label = inventory.labels[label_cursor]
            entered_labels.append({
                "index": label.index,
                "action_id": label.action_id,
                "marker_only": label.marker_only,
                "enter_ns": label.enter_ns,
            })
            label_cursor += 1
        if ab is not None:
            while root_label_cursor < len(entered_labels):
                label = entered_labels[root_label_cursor]
                ab.label_boundary(int(label["index"]), str(label["action_id"]))
                root_label_cursor += 1

    def dispatch(event, measurement_ns: int) -> None:
        nonlocal ab, uwb_instrumentation, root_label_cursor, bootstrap_count
        advance_labels(measurement_ns)
        region_index = inventory.region_index_for_ns(measurement_ns)
        counter = region_counts[region_index] if region_index is not None else None
        if event.record_type.value == "IMU":
            if counter is not None:
                counter["pelvis_imu"] += 1
            oriented = vqf.step(
                boot=event.boot_epoch,
                timer_us=int(event.node_timer_us),
                acc_raw=event.payload["acc_raw"],
                gyro_raw=event.payload["gyro_raw"],
                preparation=measurement_ns < inventory.start_ns,
            )
            if oriented is None or ab is None:
                return
            acceleration, rotation = oriented
            time_s = measurement_ns * 1e-9
            if time_s <= ab.a.time_s + 1e-12:
                return
            if len(times) >= MAX_TRAJECTORY_SAMPLES:
                raise RuntimeError("trajectory sample count exceeded the preregistered hard cap")
            ab.add_imu(
                time_s=time_s,
                force_sensor_mps2=acceleration,
                rotation_world_from_sensor=rotation,
            )
            output_index = len(times)
            times.append(time_s)
            times_ns.append(measurement_ns)
            positions_a.append(ab.a.position_m.copy())
            positions_b.append(ab.b.position_m.copy())
            velocities_a.append(ab.a.velocity_mps.copy())
            velocities_b.append(ab.b.velocity_mps.copy())
            if counter is not None:
                counter["trajectory_samples"] += 1
                region_output_indices[region_index].append(output_index)
            return

        if event.record_type.value != "UWB":
            raise TypeError("merged pelvis event is neither IMU nor UWB")
        if counter is not None:
            counter["pelvis_uwb"] += 1
        row = uwb_row_from_event(event)
        admission = admit_uwb_timestamp(row, clock)
        admission_reasons[admission.reason] += 1
        if not admission.eligible_for_range_solver:
            if counter is not None:
                counter["uwb_ineligible"] += 1
                counter["uwb_reasons"][admission.reason] += 1
            return
        if counter is not None:
            counter["uwb_eligible"] += 1
        if measurement_ns < inventory.start_ns:
            return
        if ab is None:
            bootstrap = bootstrap_action00_root(
                row,
                measurement_time_ns=measurement_ns,
                anchors_m=anchors,
                clock=clock,
            )
            ab = ContinuousRootAB(bootstrap)
            uwb_instrumentation = ContinuousUwbInstrumentation(
                maximum_committed_correction_m=(
                    ab.root_config.maximum_position_influence_m
                ),
            )
            bootstrap_count += 1
            decision_reasons["COMMON_DIAGNOSTIC_BOOTSTRAP"] += 1
            if counter is not None:
                counter["uwb_bootstrap"] += 1
                counter["uwb_reasons"]["COMMON_DIAGNOSTIC_BOOTSTRAP"] += 1
            advance_labels(measurement_ns)
            return
        decision = ab.add_uwb(
            row,
            measurement_time_ns=measurement_ns,
            anchors_m=anchors,
            clock=clock,
        )
        if decision is None:
            reason = "ELIGIBLE_TIMESTAMP_REJECTED_BEFORE_SOLVER_DECISION"
            accepted = False
        else:
            reason = decision.reason
            accepted = bool(decision.accepted)
        assert uwb_instrumentation is not None
        uwb_instrumentation.observe(
            time_ns=measurement_ns,
            sequence=row.sequence,
            sweep=row.sweep,
            committed=accepted,
            reason=reason,
            solver_accepted=False if decision is None else decision.solver_accepted,
            correction_norm_m=(
                0.0 if decision is None else float(
                    np.linalg.norm(decision.proposed_position_correction_m)
                )
            ),
            recovery_good_events=(
                0 if decision is None else decision.recovery_good_events
            ),
            recovery_required_events=ab.root_config.recovery_good_events,
        )
        decision_reasons[reason] += 1
        if counter is not None:
            counter["uwb_accepted" if accepted else "uwb_rejected"] += 1
            counter["uwb_reasons"][reason] += 1

    with RAW.open("rb") as source:
        source.seek(inventory.start_offset)
        cursor = inventory.start_offset
        while cursor < inventory.stop_offset:
            data = source.read(min(1 << 20, inventory.stop_offset - cursor))
            if not data:
                raise RuntimeError("full continuous source window ended early")
            whole_hash.update(data)
            data_stop = cursor + len(data)
            for index, region in enumerate(inventory.regions):
                left = max(cursor, region.start_offset)
                right = min(data_stop, region.stop_offset)
                if right > left:
                    region_hashes[index].update(data[left - cursor:right - cursor])
            pending.extend(data)
            cursor = data_stop
            while (boundary := pending.find(0)) >= 0:
                encoded = bytes(pending[:boundary])
                record_start = pending_start
                del pending[:boundary + 1]
                pending_start = record_start + boundary + 1
                if not encoded:
                    continue
                records += 1
                if records > MAX_RECORDS:
                    raise RuntimeError("record count exceeded the preregistered hard cap")
                frame = decode_frame(encoded)
                if frame.kind not in (1, 3):
                    continue
                boot = boots[frame.node_name]
                provenance = (records, record_start, pending_start, encoded)
                events = (
                    tuple(_imu_events(frame, boot, provenance))
                    if frame.kind == 3
                    else (_uwb_event(frame, boot, provenance),)
                )
                for event in events:
                    decoded_events += 1
                    if decoded_events > MAX_DECODED_EVENTS:
                        raise RuntimeError("decoded event count exceeded the preregistered hard cap")
                    if event.node_id != PELVIS:
                        continue
                    common_ns = int(round(clock.seconds(int(event.node_timer_us)) * 1e9))
                    progress_ns = common_ns
                    if event.record_type.value == "UWB":
                        admission = admit_uwb_timestamp(uwb_row_from_event(event), clock)
                        common_ns = admission.dispatch_measurement_ns
                        progress_ns = admission.stream_progress_ns
                        source_pelvis_uwb += 1
                    else:
                        source_pelvis_imu += 1
                    if (
                        progress_ns < inventory.start_ns - PREPARATION_LOOKBACK_NS
                        or progress_ns > inventory.stop_ns
                    ):
                        continue
                    if selected_events >= MAX_SELECTED_PELVIS_EVENTS:
                        raise RuntimeError("selected pelvis event count exceeded the preregistered hard cap")
                    selected_events += 1
                    for ready in merge.submit(
                        event,
                        measurement_time_ns=common_ns,
                        stream_progress_ns=progress_ns,
                    ):
                        dispatch(ready.event, ready.measurement_time_ns)
            if len(pending) > 4096:
                raise RuntimeError("bounded streaming record capacity exceeded")

    if pending:
        raise RuntimeError("full continuous byte bound ends inside a COBS record")
    for ready in merge.finish(CompleteWindowBarrier(
        RAW_CONTAINER_SHA256_DECLARED, inventory.stop_offset, True,
    )):
        dispatch(ready.event, ready.measurement_time_ns)
    advance_labels(inventory.stop_ns)

    if ab is None or uwb_instrumentation is None or bootstrap_count != 1 or len(times) < 100:
        raise RuntimeError("full continuous run lacks one qualified shared bootstrap/trajectory")
    if label_cursor != 20 or root_label_cursor != 20:
        raise RuntimeError("full continuous run did not enter all protocol labels")
    if not (selected_events == merge.submitted == merge.dispatched) or merge.pending:
        raise RuntimeError("full continuous merge did not conserve selected events")

    time_array = np.asarray(times, dtype=float)
    time_ns_array = np.asarray(times_ns, dtype=np.int64)
    pa = np.stack(positions_a)
    pb = np.stack(positions_b)
    va = np.stack(velocities_a)
    vb = np.stack(velocities_b)
    if len(time_array) > MAX_TRAJECTORY_SAMPLES:
        raise RuntimeError("trajectory sample count exceeded the preregistered hard cap")
    trajectory_uncompressed_bytes = sum(
        array.nbytes for array in (time_array, time_ns_array, pa, pb, va, vb, anchors)
    )
    if trajectory_uncompressed_bytes > MAX_OUTPUT_BYTES:
        raise RuntimeError("trajectory arrays exceed the preregistered output-byte cap")
    np.savez_compressed(
        output / "ROOT_AB_FULL.npz",
        time_s=time_array,
        time_ns=time_ns_array,
        root_a_world_m=pa,
        root_b_world_m=pb,
        velocity_a_world_mps=va,
        velocity_b_world_mps=vb,
        anchors_world_m=anchors,
    )

    metrics = ab.metrics()
    if metrics.a_uwb_commits != 0:
        raise RuntimeError("branch A received a forbidden post-bootstrap UWB commit")
    regions = []
    for index, region in enumerate(inventory.regions):
        digest = region_hashes[index].hexdigest()
        if region.expected_sha256 is not None and digest != region.expected_sha256:
            raise RuntimeError(f"authenticated action hash mismatch: {region.region_id}")
        indices = region_output_indices[index]
        regions.append({
            "ordinal": region.ordinal,
            "region_id": region.region_id,
            "kind": region.kind,
            "start_offset": region.start_offset,
            "stop_offset": region.stop_offset,
            "byte_count": region.byte_count,
            "start_ns": region.start_ns,
            "stop_ns": region.stop_ns,
            "sha256": digest,
            "expected_sha256": region.expected_sha256,
            "counts": _json_counter(region_counts[index]),
            "branch_a_trajectory": _trajectory_for_region(indices, pa, va, anchors),
            "branch_b_trajectory": _trajectory_for_region(indices, pb, vb, anchors),
        })

    branch_a_trajectory = summarize_root_trajectory(pa, va, anchors)
    branch_b_trajectory = summarize_root_trajectory(pb, vb, anchors)
    transactions = uwb_instrumentation.summary()
    maximum_b_jump = maximum_adjacent_position_jump(pb, time_ns_array)
    anti_drift_gates = evaluate_anti_drift_gates(
        branch_a=branch_a_trajectory,
        branch_b=branch_b_trajectory,
        transactions=transactions,
        maximum_adjacent_jump=maximum_b_jump,
    )
    maximum_committed = transactions["maximum_committed_correction"]
    if maximum_committed is not None and float(maximum_committed["norm_m"]) > ab.root_config.maximum_position_influence_m + 1e-12:
        raise RuntimeError("committed UWB correction invariant failed")

    result: dict[str, object] = {
        "schema": FULL_SESSION_SCHEMA,
        "status": "DIAGNOSTIC_PASS" if anti_drift_gates["passed"] else "DIAGNOSTIC_FAIL",
        "scientific_pass": False,
        "source": {
            "container": str(RAW),
            "declared_container_sha256_not_recomputed": RAW_CONTAINER_SHA256_DECLARED,
            "start_offset": inventory.start_offset,
            "stop_offset": inventory.stop_offset,
            "byte_count": inventory.stop_offset - inventory.start_offset,
            "window_sha256": whole_hash.hexdigest(),
            "action_audit": str(inventory.source_audit),
            "action_audit_sha256": inventory.source_audit_sha256,
        },
        "inventory": {
            "regions": len(inventory.regions),
            "action_regions": sum(row.kind == "ACTION" for row in inventory.regions),
            "gap_regions": sum(row.kind == "INTER_ACTION_GAP" for row in inventory.regions),
            "protocol_slots": len(inventory.labels),
            "acquired_actions": sum(not row.marker_only for row in inventory.labels),
            "marker_only_actions": sum(row.marker_only for row in inventory.labels),
        },
        "event_conservation": {
            "records": records,
            "decoded_measurement_events_all_nodes": decoded_events,
            "source_pelvis_imu_events": source_pelvis_imu,
            "source_pelvis_uwb_events": source_pelvis_uwb,
            "selected_pelvis_events": selected_events,
            "merge_submitted": merge.submitted,
            "merge_dispatched": merge.dispatched,
            "merge_pending": merge.pending,
        },
        "admission_reasons": dict(sorted(admission_reasons.items())),
        "branch_a": {
            "shared_bootstrap_count": bootstrap_count,
            "post_bootstrap_uwb_commits": metrics.a_uwb_commits,
            "trajectory": branch_a_trajectory,
        },
        "branch_b": {
            "shared_bootstrap_count": bootstrap_count,
            "uwb_accepted": metrics.b_uwb_accepted,
            "uwb_rejected": metrics.b_uwb_rejected,
            "decision_reasons": dict(sorted(decision_reasons.items())),
            "trajectory": branch_b_trajectory,
            "maximum_adjacent_position_jump": maximum_b_jump,
        },
        "uwb_transactions": transactions,
        "anti_drift_gates": anti_drift_gates,
        "vqf": {
            "instances": 1,
            "resets": 0,
            "samples": vqf.samples,
            "gap_count": len(vqf.gaps),
            "maximum_gap_us": max((right - left for left, right in vqf.gaps), default=0),
        },
        "merge": {
            "owner": "PelvisTwoStreamMonotoneMerge",
            "capacity": MERGE_CAPACITY,
            "submitted": merge.submitted,
            "dispatched": merge.dispatched,
            "pending_after_barrier": merge.pending,
        },
        "regions": regions,
        "protocol_labels": entered_labels,
        "runtime": {
            "runtime_manifest": manifest_verification,
            "internal_wall_s": time.monotonic() - started,
            "max_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
            "hard_caps": {
                "records": MAX_RECORDS,
                "decoded_events": MAX_DECODED_EVENTS,
                "selected_pelvis_events": MAX_SELECTED_PELVIS_EVENTS,
                "trajectory_samples": MAX_TRAJECTORY_SAMPLES,
                "output_bytes": MAX_OUTPUT_BYTES,
            },
            "trajectory_uncompressed_bytes": trajectory_uncompressed_bytes,
            "trajectory_npz_bytes": int((output / "ROOT_AB_FULL.npz").stat().st_size),
        },
    }
    validate_result_schema(result)
    total_output_bytes = 0
    for _ in range(8):
        result["runtime"]["total_output_bytes"] = total_output_bytes
        result_payload = json.dumps(result, indent=2) + "\n"
        updated = int((output / "ROOT_AB_FULL.npz").stat().st_size) + len(
            result_payload.encode("utf-8")
        )
        if updated == total_output_bytes:
            break
        total_output_bytes = updated
    else:
        raise RuntimeError("final output-byte accounting did not converge")
    if total_output_bytes > MAX_OUTPUT_BYTES:
        raise RuntimeError("final run output exceeds the preregistered output-byte cap")
    (output / "RESULT.json").write_text(result_payload)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--runtime-manifest", required=True, type=Path)
    args = parser.parse_args()
    final = run(args.output, args.runtime_manifest)
    print(json.dumps({
        "status": final["status"],
        "output": str(args.output / "RESULT.json"),
        "runtime": final["runtime"],
        "branch_a": final["branch_a"],
        "branch_b": final["branch_b"],
    }, indent=2))
