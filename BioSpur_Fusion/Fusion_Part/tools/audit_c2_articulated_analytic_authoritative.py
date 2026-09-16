#!/usr/bin/env python3
"""No-raw matched Jacobian audit through the authoritative coordinator."""
from __future__ import annotations

import argparse
from dataclasses import replace
from functools import partial
import hashlib
import json
import os
import pickle
from pathlib import Path
import resource
import time

import numpy as np

from biospur_fusion.c2_3a_kinematics import load_frozen_c2_3a
from biospur_fusion.c2_articulated_biomechanics.orientation_ik import project_hinge_corrections
from biospur_fusion.c2_uwb_calibration import articulated_range
from biospur_fusion.c2_uwb_calibration.articulated_range import SEGMENTS, corrected_proxy_points
from biospur_fusion.c2_uwb_calibration.causal_articulated_pose import CausalArticulatedPose
from biospur_fusion.c2_uwb_calibration.frozen_body_proxy import NODE_TO_PROXY_POINT, frozen_world_alignment
from biospur_fusion.c2_uwb_root_world import causal_update_transaction
from biospur_fusion.c2_uwb_root_world.async_root_worker import RootWorkerEvent
from biospur_fusion.c2_uwb_root_world.authoritative_articulated_fusion import AuthoritativeArticulatedFusion
from biospur_fusion.c2_uwb_root_world.offline_unified_wiring import group_epoch_times_ns
from biospur_fusion.c2_timing_contract import canonical_clock_global_ns
from biospur_fusion.c2_uwb_root_world.owner_bound_async_worker import (
    BShadowGeometryOwner, BShadowSnapshotOwner, BoundGroupPacket, _prepare_dynamic_owner,
)
from biospur_fusion.c2_uwb_root_world.root_worker_owner_wiring import PoseTagLinkOwner
from biospur_fusion.root_r3.models import ImuSample

import run_c2_authoritative_articulated_action04 as action04
from test_c2_owner_bound_async_worker import packets


ROOT = Path(__file__).resolve().parents[1]
GROUPS = 41
IMU_TOTAL = 1007
COUNTS = (10, 4, 1)
DELIVERY_SEAL = "80a52f09c5eeea162836af11a339029b9444fcd1d65789611763e4804b9b7a1b"


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _digest(value) -> str:
    return hashlib.sha256(pickle.dumps(value, protocol=5)).hexdigest()


def _pose_owner():
    calibration = load_frozen_c2_3a()
    alignment, _ = frozen_world_alignment(calibration)
    _report, model, model_owner = action04._load_and_fit_sealed_axis_owner()
    with np.load(action04.POSE_ACCEPTED_TRAJECTORY, allow_pickle=False) as archive:
        data = {
            segment: {
                "quat_world_segment_wxyz": np.array(
                    archive[f"trajectory/04/{segment}/quat_world_segment_wxyz"]
                ),
                "mask": np.array(archive[f"trajectory/04/{segment}/mask"], dtype=bool),
            }
            for segment in SEGMENTS
        }
    valid = np.logical_and.reduce([data[s]["mask"] for s in SEGMENTS])
    frames = np.flatnonzero(valid)[:GROUPS]
    if len(frames) != GROUPS:
        raise RuntimeError("accepted Action04 owner lacks 41 consecutive valid frames")
    return calibration.geometry, alignment, model, model_owner, data, frames


def _frame(data, frame, alignment, geometry):
    return action04._accepted_articulated_pose_frame(
        data, SEGMENTS, int(frame), alignment, geometry
    )


def _packet(static, template, group_index, count, rotations, points):
    shift_us = group_index * 120_048
    selected = (
        set(static.clocks) if count == 10 else
        {sorted(static.clocks)[index] for index in (0, 1, 2, 5)} if count == 4 else
        {"BSFC2CC"}
    )
    root_time = static.initial_state.time_s + group_index * 0.120048
    root = static.initial_state.vector[:3] + (
        root_time - static.initial_state.time_s
    ) * static.initial_state.vector[3:6]
    rows = []
    for source in template.event.payload:
        offset = points[NODE_TO_PROXY_POINT[source.node]]
        ranges = tuple(
            int(round(1000.0 * (
                np.linalg.norm(root + offset - static.anchors_m[anchor])
                + static.anchor_delay_m[anchor] + static.tag_delay_m
            )))
            for anchor in range(8)
        )
        rows.append(replace(
            source,
            sequence=group_index + 1,
            sweep=group_index + 1,
            strobe_us=source.strobe_us + shift_us,
            frame_us=source.frame_us + shift_us,
            ranges_mm=ranges,
            valid_mask=255 if source.node in selected else 0,
        ))
    rows = tuple(rows)
    pose_links = []
    snapshots = []
    offsets = {
        node: points[NODE_TO_PROXY_POINT[node]].copy() for node in static.clocks
    }
    normals = {node: rotations[static.node_to_segment[node] if hasattr(static, "node_to_segment") else "pelvis"][:, 2] if False else np.array([1.0, 0.0, 0.0]) for node in static.clocks}
    for row in rows:
        clock = static.clocks[row.node]
        for anchor in range(8):
            query = clock.link_time_ns(
                event_boot_epoch=row.boot,
                strobe_us=row.strobe_us,
                t_round_us=row.t_round_us[anchor],
            )
            pose_ns = int(query // 5_000_000 * 5_000_000)
            pose_links.append(PoseTagLinkOwner(
                row.node, anchor, query, pose_ns, offsets[row.node], np.zeros(3),
                pose_ns // 5_000_000, int(group_index), "2" * 64,
            ))
        query = min(
            clock.link_time_ns(
                event_boot_epoch=row.boot,
                strobe_us=row.strobe_us,
                t_round_us=row.t_round_us[anchor],
            )
            for anchor in range(8)
        )
        pose_ns = int(query // 5_000_000 * 5_000_000)
        snapshots.append(BShadowSnapshotOwner(
            row.node, "04_shoulder_left", int(group_index), pose_ns, query,
            offsets, normals, points, "3" * 64,
        ))
    _, _, availability_ns = group_epoch_times_ns(rows, clocks=static.clocks)
    availability_ns = canonical_clock_global_ns(availability_ns)
    return BoundGroupPacket(
        static.digest,
        RootWorkerEvent(10_000 + group_index, availability_ns * 1e-9, "UWB", rows),
        tuple(pose_links), (), template.a_sigma_owner, template.b_sigma_owner,
        BShadowGeometryOwner(geometry=template.b_shadow_owner.geometry,
                             snapshots=tuple(snapshots),
                             provenance="NO_RAW_ACCEPTED_POSE_OWNER"),
        availability_global_ns=availability_ns,
    )


def _state(engine):
    root = engine.root.publication_token()
    pose = engine.pose.publication_token()
    robust_snapshot = engine.robust.snapshot()
    transition = engine.pose.transition_snapshot()
    return {
        "root_revision": root.revision,
        "root_digest": root.digest,
        "pose_revision": pose.revision,
        "pose_digest": pose.digest,
        "robust_revision": engine.robust.revision,
        "root_values": tuple(float(value) for value in np.concatenate(
            [root.state.vector, root.state.covariance.ravel()]
        )),
        "robust_digest": _digest(robust_snapshot),
        "robust_values": tuple(
            float(value)
            for _node, tracker in robust_snapshot[1]
            for values in tracker.values()
            for value in values
        ),
        "temporal_digest": _digest(transition),
        "pose_values": tuple(
            float(value)
            for owner in ("origin_correction", "target_correction")
            for segment in SEGMENTS
            for value in transition[owner][segment]
        ),
    }


def _result(result):
    return {
        "accepted": result.accepted,
        "reason": result.reason,
        "trusted": result.trusted_nodes,
        "direct": result.direct_nodes,
        "propagated": result.propagated_nodes,
        "root": result.root_position_m.copy(),
        "covariance": result.root_covariance_m2.copy(),
        "correction": np.concatenate([
            result.segment_correction_rotvec[s] for s in SEGMENTS
        ]),
        "nodes": np.concatenate([
            result.node_position_m[node] for node in sorted(result.node_position_m)
        ]),
        "foothold": result.maximum_foothold_residual_m,
        "transaction_reason": None if result.transaction is None else result.transaction.decision.reason.value,
    }


def _execute(mode, static, template, geometry, alignment, model, data, frames):
    projector = partial(project_hinge_corrections, model=model)
    pose = CausalArticulatedPose(
        action_start_s=0.0,
        action_stop_s=8.0,
        rotations_at_fraction=lambda fraction: {
            segment: np.eye(3) for segment in SEGMENTS
        },
        geometry=geometry,
        hinge_projector=projector,
    )
    pose.sample(0.0)
    pose.sample(0.005)
    pose.sample(0.010)
    engine = AuthoritativeArticulatedFusion(static_owner=static, pose=pose)
    actual_ls = articulated_range.least_squares
    if mode == "forward_2point":
        def forward(fun, x0, *args, **kwargs):
            kwargs["jac"] = "2-point"
            return actual_ls(fun, x0, *args, **kwargs)
        articulated_range.least_squares = forward
    actual_u1 = causal_update_transaction.evaluate_candidate_transition
    u1_calls = 0
    def observed_u1(*args, **kwargs):
        nonlocal u1_calls
        u1_calls += 1
        return actual_u1(*args, **kwargs)
    causal_update_transaction.evaluate_candidate_transition = observed_u1
    rows = []
    next_imu = 0.055
    imu_sequence = 0
    try:
        for group_index, frame_index in enumerate(frames):
            rotations, points = _frame(data, frame_index, alignment, geometry)
            count = COUNTS[group_index % 3]
            packet = _packet(static, template, group_index, count, rotations, points)
            while next_imu <= packet.event.availability_time_s + 1e-12:
                engine.add_imu(ImuSample(
                    next_imu, next_imu, np.array([0.0, 0.0, 9.80665]),
                    np.eye(3), imu_sequence,
                ))
                next_imu += 0.005
                imu_sequence += 1
            prepared = _prepare_dynamic_owner(engine.static, packet)
            plan = engine.robust.prepare(engine.static, engine.root, packet, prepared)
            previous = {
                key: value.copy()
                for key, value in engine.pose.transition_snapshot()["target_correction"].items()
            }
            prior_points = corrected_proxy_points(rotations, previous, geometry)
            contact_mode = ("swing", "left", "both")[group_index % 3]
            constraints = {}
            if contact_mode in {"left", "both"}:
                constraints["ankle_left"] = plan.candidate.root_position_m + prior_points["ankle_left"]
            if contact_mode == "both":
                constraints["ankle_right"] = plan.candidate.root_position_m + prior_points["ankle_right"]
            epoch = engine.epoch(
                measurement_time_s=plan.measurement_s,
                availability_time_s=plan.availability_s,
                previous_orientation_time_s=plan.measurement_s - 0.005,
                base_rotations_world=rotations,
                previous_correction_rotvec=previous,
                point_constraints_world_m=constraints,
                provenance="NO_RAW_AUTHORITATIVE_MATCHED_JACOBIAN_AUDIT",
            )
            state_before = _state(engine)
            u1_before = u1_calls
            started = time.perf_counter()
            result = engine.admit(packet, epoch)
            service_ms = (time.perf_counter() - started) * 1000.0
            state_after_uwb = _state(engine)
            pose.sample(packet.event.availability_time_s + 0.005)
            state_after_native = _state(engine)
            rows.append({
                "group": group_index,
                "frame": int(frame_index),
                "node_count_requested": count,
                "contact_mode": contact_mode,
                "constraint_count": len(constraints),
                "u1_calls": u1_calls - u1_before,
                "state_before": state_before,
                "state_after_uwb": state_after_uwb,
                "state_after_native200": state_after_native,
                "result": _result(result),
                "service_ms": service_ms,
            })
        # Process the declared inventory without using its surplus as pose evidence.
        while imu_sequence < IMU_TOTAL:
            engine.add_imu(ImuSample(
                next_imu, next_imu, np.array([0.0, 0.0, 9.80665]),
                np.eye(3), imu_sequence,
            ))
            next_imu += 0.005
            imu_sequence += 1
    finally:
        articulated_range.least_squares = actual_ls
        causal_update_transaction.evaluate_candidate_transition = actual_u1
    return rows, _state(engine), u1_calls, imu_sequence


def _compare(baseline, analytic):
    if len(baseline) != len(analytic) != GROUPS:
        raise RuntimeError("group cardinality mismatch")
    maxima = {"root": 0.0, "covariance": 0.0, "correction": 0.0,
              "nodes": 0.0, "foothold": 0.0}
    records = []
    for left, right in zip(baseline, analytic):
        for field in ("group", "frame", "node_count_requested", "contact_mode",
                      "constraint_count", "u1_calls"):
            if left[field] != right[field]:
                raise RuntimeError(f"group metadata mismatch: {field}")
        for field in ("accepted", "reason", "trusted", "direct", "propagated",
                      "transaction_reason"):
            if left["result"][field] != right["result"][field]:
                raise RuntimeError(f"published decision mismatch: {field}")
        for snapshot in ("state_before", "state_after_uwb", "state_after_native200"):
            for revision in ("root_revision", "pose_revision", "robust_revision"):
                if left[snapshot][revision] != right[snapshot][revision]:
                    raise RuntimeError(f"owner revision mismatch: {snapshot}/{revision}")
            for values, tolerance in (("root_values", 1e-10),
                                      ("robust_values", 1e-10),
                                      ("pose_values", 1e-7)):
                if not np.allclose(
                    left[snapshot][values], right[snapshot][values],
                    rtol=0.0, atol=tolerance,
                ):
                    raise RuntimeError(f"owner state mismatch: {snapshot}/{values}")
        for field, tolerance in (("root", 1e-10), ("covariance", 1e-10),
                                 ("correction", 1e-7), ("nodes", 1e-7)):
            delta = float(np.max(np.abs(left["result"][field] - right["result"][field])))
            maxima[field] = max(maxima[field], delta)
            if delta > tolerance:
                raise RuntimeError(f"published output mismatch: {field}")
        delta = abs(left["result"]["foothold"] - right["result"]["foothold"])
        maxima["foothold"] = max(maxima["foothold"], delta)
        if delta > 1e-8:
            raise RuntimeError("foothold residual mismatch")
        accepted = right["result"]["accepted"]
        before = right["state_before"]
        after = right["state_after_uwb"]
        expected = 1 if accepted else 0
        atomic = (
            after["root_revision"] - before["root_revision"] == expected
            and after["pose_revision"] - before["pose_revision"] == expected
            and after["robust_revision"] - before["robust_revision"] == expected
        )
        rollback = accepted or (
            after["root_digest"] == before["root_digest"]
            and after["pose_digest"] == before["pose_digest"]
            and after["robust_digest"] == before["robust_digest"]
        )
        if not atomic or not rollback:
            raise RuntimeError("authoritative atomic/rollback mismatch")
        records.append({
            "group": right["group"], "frame": right["frame"],
            "node_count_requested": right["node_count_requested"],
            "contact_mode": right["contact_mode"],
            "constraint_count": right["constraint_count"],
            "accepted": accepted, "reason": right["result"]["reason"],
            "trusted_nodes": list(right["result"]["trusted"]),
            "direct_nodes": list(right["result"]["direct"]),
            "propagated_nodes": list(right["result"]["propagated"]),
            "u1_calls": right["u1_calls"], "atomic": atomic,
            "rollback_unchanged": rollback,
            "maximum_foothold_residual_m": right["result"]["foothold"],
            "revisions": {
                "before": [before[k] for k in ("root_revision", "pose_revision", "robust_revision")],
                "after_uwb": [after[k] for k in ("root_revision", "pose_revision", "robust_revision")],
                "pose_after_native200": right["state_after_native200"]["pose_revision"],
            },
        })
    return maxima, records


def run(output: Path):
    if output.exists():
        raise RuntimeError("fresh output required")
    if _sha(ROOT / "logs/c2_authoritative_articulated_analytic_jacobian_delivery_revision_001_20260907T102000Z/SHA256SUMS") != DELIVERY_SEAL:
        raise RuntimeError("prior delivery seal mismatch")
    output.mkdir(parents=True)
    static, template, _ = packets()
    static = replace(
        static,
        clocks={
            node: replace(clock, last_timer_us=6_000_000)
            for node, clock in static.clocks.items()
        },
        digest="",
    )
    geometry, alignment, model, model_owner, data, frames = _pose_owner()
    # Rebind the packet geometry owner to the accepted trajectory geometry.
    template = replace(template, b_shadow_owner=replace(
        template.b_shadow_owner, geometry=geometry, digest=""
    ), digest="")
    baseline, baseline_final, baseline_u1, baseline_imu = _execute(
        "forward_2point", static, template, geometry, alignment, model, data, frames
    )
    analytic, analytic_final, analytic_u1, analytic_imu = _execute(
        "analytic", static, template, geometry, alignment, model, data, frames
    )
    maxima, records = _compare(baseline, analytic)
    final_revisions = ("root_revision", "pose_revision", "robust_revision")
    final_values = (("root_values", 1e-10), ("robust_values", 1e-10),
                    ("pose_values", 1e-7))
    if (
        any(baseline_final[key] != analytic_final[key] for key in final_revisions)
        or any(not np.allclose(baseline_final[key], analytic_final[key], rtol=0.0, atol=tol)
               for key, tol in final_values)
        or baseline_u1 != analytic_u1
        or baseline_imu != analytic_imu
    ):
        raise RuntimeError("final authoritative owner mismatch")
    timing = np.asarray([row["service_ms"] for row in analytic])
    result = {
        "schema": "biospur.c2.articulated.analytic-authoritative-audit.v1",
        "status": "NO_RAW_EVIDENCE_PASS",
        "groups": GROUPS,
        "imu_inventory": {"metric": 1000, "uwb_delivery_context": 6,
                          "temporal_closure": 1, "total": analytic_imu},
        "u1_calls": analytic_u1,
        "accepted_groups": sum(row["accepted"] for row in records),
        "rejected_groups": sum(not row["accepted"] for row in records),
        "one_node_groups": sum(row["node_count_requested"] == 1 for row in records),
        "one_foot_groups": sum(row["contact_mode"] == "left" for row in records),
        "two_foot_groups": sum(row["contact_mode"] == "both" for row in records),
        "swing_groups": sum(row["contact_mode"] == "swing" for row in records),
        "maximum_parity_delta": maxima,
        "timing_ms": {"mean": float(timing.mean()), "p99": float(np.percentile(timing, 99)),
                      "maximum": float(timing.max()),
                      "effective_utilization": float(timing.mean() / 120.048)},
        "maximum_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        "final_owner_state": analytic_final,
        "model_owner": model_owner,
        "implementation_sha256": _sha(ROOT / "src/biospur_fusion/c2_uwb_calibration/articulated_range.py"),
        "raw_opened": False, "hxx_opened": False,
    }
    output.joinpath("GROUPS.json").write_text(json.dumps(records, indent=2, sort_keys=True) + "\n")
    output.joinpath("RESULT.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    output.joinpath("COMMAND.txt").write_text(
        "PYTHONPATH=src:tools:tests:. OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 NUMEXPR_NUM_THREADS=1 .venv-v0/bin/python tools/audit_c2_articulated_analytic_authoritative.py --output "
        + str(output.relative_to(ROOT)) + "\n"
    )
    output.joinpath("VERIFY_CWD.txt").write_text(
        f"cd {output}\nsha256sum -c SHA256SUMS\n"
    )
    external = (
        Path(__file__).resolve(),
        ROOT / "src/biospur_fusion/c2_uwb_calibration/articulated_range.py",
        ROOT / "src/biospur_fusion/c2_uwb_root_world/authoritative_articulated_fusion.py",
        ROOT / "src/biospur_fusion/c2_uwb_root_world/causal_update_transaction.py",
        ROOT / "tests/test_c2_articulated_range.py",
        ROOT / "tests/test_c2_owner_bound_async_worker.py",
        action04.POSE_ACCEPTED_TRAJECTORY,
        ROOT / "logs/c2_authoritative_articulated_analytic_jacobian_delivery_revision_001_20260907T102000Z/SHA256SUMS",
    )
    members = sorted(path for path in output.iterdir() if path.name != "SHA256SUMS")
    lines = [
        f"{_sha(path)}  {os.path.relpath(path, output)}"
        for path in (*members, *external)
    ]
    output.joinpath("SHA256SUMS").write_text("\n".join(lines) + "\n")
    print(json.dumps(result, sort_keys=True))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    run(args.output.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
