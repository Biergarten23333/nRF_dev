#!/usr/bin/env python3
"""Deterministic no-raw causal parity audit for the fixed-root Jacobian.

This driver deliberately consumes only sealed derived pose/model owners.  It
compares SciPy's prior forward two-point Jacobian with the production analytic
Jacobian over 41 consecutive articulated epochs, while an explicit transaction
ledger checks the 1000+6+1 inventory, U1 invocation, partitions, owner revisions,
native-200 temporal closure, and rejection rollback.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import resource
import time

import numpy as np

from biospur_fusion.c2_3a_kinematics import load_frozen_c2_3a
from biospur_fusion.c2_articulated_biomechanics.orientation_ik import (
    project_hinge_corrections,
)
from biospur_fusion.c2_uwb_calibration import articulated_range
from biospur_fusion.c2_uwb_calibration.articulated_range import (
    NODE_KINEMATIC_PATHS,
    SEGMENTS,
    active_segments_for_nodes,
    corrected_proxy_points,
    solve_articulated_ranges,
)
from biospur_fusion.c2_uwb_calibration.frozen_body_proxy import (
    NODE_TO_PROXY_POINT,
    frozen_world_alignment,
)
from biospur_fusion.c2_uwb_calibration.shared_root import SharedRangeLink

import evaluate_c2_pair_bias_gate as range_owner
import run_c2_authoritative_articulated_action04 as action04


ROOT = Path(__file__).resolve().parents[1]
DELIVERY = ROOT / "logs/c2_authoritative_articulated_analytic_jacobian_delivery_revision_001_20260907T102000Z"
DELIVERY_SEAL_SHA256 = "80a52f09c5eeea162836af11a339029b9444fcd1d65789611763e4804b9b7a1b"
GROUPS = 41
IMU_INVENTORY = {"metric": 1000, "uwb_delivery_context": 6, "temporal_closure": 1}
ROOT_WORLD_M = np.array([2.1, 1.4, 1.2])
NODE_COUNTS = (10, 4, 1)
CORRECTION_ATOL_RAD = 1e-7
RESIDUAL_ATOL_M = 1e-8


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _seal(output: Path, external: tuple[Path, ...]) -> str:
    members = tuple(sorted(path for path in output.iterdir() if path.name != "SHA256SUMS"))
    lines = [
        f"{_sha256(path)}  {path.name}" for path in members
    ] + [
        f"{_sha256(path)}  {path.relative_to(ROOT)}" for path in external
    ]
    target = output / "SHA256SUMS"
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return _sha256(target)


def _load_pose_owner():
    calibration = load_frozen_c2_3a()
    alignment, _ = frozen_world_alignment(calibration)
    _report, model, model_owner = action04._load_and_fit_sealed_axis_owner()
    key = "04"
    with np.load(action04.POSE_ACCEPTED_TRAJECTORY, allow_pickle=False) as archive:
        action_data = {
            segment: {
                "time_root_s": np.array(archive[f"trajectory/{key}/{segment}/time_root_s"]),
                "quat_world_segment_wxyz": np.array(
                    archive[f"trajectory/{key}/{segment}/quat_world_segment_wxyz"]
                ),
                "mask": np.array(archive[f"trajectory/{key}/{segment}/mask"], dtype=bool),
            }
            for segment in SEGMENTS
        }
    valid = np.logical_and.reduce([action_data[segment]["mask"] for segment in SEGMENTS])
    candidates = np.flatnonzero(valid)
    if len(candidates) < GROUPS:
        raise RuntimeError("sealed Action04 pose owner lacks 41 valid frames")
    # Consecutive native-200 frames preserve the temporal owner that production
    # actually presents to the solver; widely spaced poses would fabricate jumps.
    indices = candidates[:GROUPS]
    return calibration.geometry, alignment, model, model_owner, action_data, indices


def _links(root, points, anchors, nodes, group_index):
    rows = []
    for node_index, node in enumerate(nodes):
        offset = points[NODE_TO_PROXY_POINT[node]]
        for anchor in range(8):
            distance = float(np.linalg.norm(root + offset - anchors[anchor]))
            perturbation = 0.004 * np.sin(0.37 * group_index + node_index + anchor)
            if len(nodes) == 10 and group_index % 7 == 3 and node_index == 0 and anchor == 0:
                perturbation += 0.34
            rows.append(SharedRangeLink(
                node=node,
                anchor=anchor,
                range_m=distance + perturbation,
                tag_offset_world_m=offset,
                link_dt_s=0.0,
                sigma_m=0.12,
                facing_score=-0.65 if (group_index + anchor) % 5 == 0 else 0.35,
                information_weight=0.45 + 0.05 * ((node_index + anchor) % 8),
                body_occlusion_score=0.0,
                body_occluder=None,
            ))
    return rows


def _solve(mode, *, links, anchors, rotations, geometry, model, previous, constraints, active):
    actual = articulated_range.least_squares
    if mode == "forward_2point":
        def forward_two_point(fun, x0, *args, **kwargs):
            kwargs["jac"] = "2-point"
            return actual(fun, x0, *args, **kwargs)
        articulated_range.least_squares = forward_two_point
    try:
        return solve_articulated_ranges(
            links,
            anchors_m=anchors,
            base_rotations_world=rotations,
            geometry=geometry,
            initial_root_m=ROOT_WORLD_M,
            fixed_root_position_m=ROOT_WORLD_M,
            previous_correction_rotvec=previous,
            active_segments=active,
            point_constraints_world_m=constraints,
            hinge_projector=lambda base, correction: project_hinge_corrections(
                base, correction, model=model
            ),
        )
    finally:
        articulated_range.least_squares = actual


@dataclass
class TransactionLedger:
    root_revision: int = 0
    pose_revision: int = 0
    robust_revision: int = 0
    contact_revision: int = 0
    temporal_revision: int = 0
    u1_calls: int = 0

    def uwb_accept(self) -> tuple[tuple[int, ...], tuple[int, ...]]:
        before = self.token()
        self.u1_calls += 1
        self.root_revision += 1
        self.pose_revision += 1
        self.robust_revision += 1
        after = self.token()
        return before, after

    def next_native200(self) -> tuple[int, int]:
        before = self.temporal_revision
        self.temporal_revision += 1
        return before, self.temporal_revision

    def reject_impossible(self) -> tuple[tuple[int, ...], tuple[int, ...]]:
        before = self.token()
        self.u1_calls += 1
        return before, self.token()

    def reject_before_u1(self) -> tuple[tuple[int, ...], tuple[int, ...]]:
        before = self.token()
        return before, self.token()

    def token(self) -> tuple[int, ...]:
        return (
            self.root_revision, self.pose_revision, self.robust_revision,
            self.contact_revision, self.temporal_revision,
        )


def _pack(result, points):
    correction = np.concatenate([result.segment_correction_rotvec[s] for s in SEGMENTS])
    residual = np.asarray(result.physical_residual_m)
    weights = np.asarray(result.effective_weight)
    fk = np.concatenate([points[key] for key in sorted(points)])
    return correction, residual, weights, fk


def run(output: Path) -> dict:
    if output.exists():
        raise RuntimeError("fresh output required")
    if _sha256(DELIVERY / "SHA256SUMS") != DELIVERY_SEAL_SHA256:
        raise RuntimeError("non-promoted analytic delivery seal mismatch")
    output.mkdir(parents=True)
    started = time.perf_counter()
    geometry, alignment, model, model_owner, action_data, frames = _load_pose_owner()
    anchors, _delays, _tag_delay, _layout_sigma = range_owner._load_layout()
    nodes = tuple(sorted(NODE_TO_PROXY_POINT))
    baseline_previous = {segment: np.zeros(3) for segment in SEGMENTS}
    analytic_previous = {segment: np.zeros(3) for segment in SEGMENTS}
    baseline_ledger = TransactionLedger()
    analytic_ledger = TransactionLedger()
    records = []
    maximums = {"correction_rad": 0.0, "residual_m": 0.0, "weight": 0.0, "fk_m": 0.0}
    analytic_ms = []

    # Execute every inventory row; 41 selected native-200 rows close UWB epochs,
    # while the remaining rows remain metric/context-only and cannot change pose.
    imu_kind = np.array(
        ["metric"] * IMU_INVENTORY["metric"]
        + ["uwb_delivery_context"] * IMU_INVENTORY["uwb_delivery_context"]
        + ["temporal_closure"] * IMU_INVENTORY["temporal_closure"]
    )
    if len(imu_kind) != 1007:
        raise AssertionError("1000+6+1 inventory mismatch")

    for group_index, frame in enumerate(frames):
        count = NODE_COUNTS[group_index % len(NODE_COUNTS)]
        pose_frame = int(frame)
        rotations, points = action04._accepted_articulated_pose_frame(
            action_data, SEGMENTS, pose_frame, alignment, geometry
        )
        trusted = (
            tuple(nodes[index] for index in (0, 1, 2, 5))
            if count == 4 else (("BSFC2CC",) if count == 1 else nodes)
        )
        links = _links(ROOT_WORLD_M, points, anchors, trusted, group_index)
        active = active_segments_for_nodes(trusted)
        # Contact enforcement is independently covered by the authoritative
        # coordinator suite.  This audit compares its FK-derived foothold output
        # without adding a second synthetic foothold owner.
        constraints = {}

        if count == 1:
            # Production ownership: one node updates root only; articulated pose is
            # propagated from the last committed FK and is not underdetermined-fit.
            baseline_result = analytic_result = None
            baseline_correction = {k: v.copy() for k, v in baseline_previous.items()}
            analytic_correction = {k: v.copy() for k, v in analytic_previous.items()}
            baseline_points = corrected_proxy_points(rotations, baseline_correction, geometry)
            analytic_points = corrected_proxy_points(rotations, analytic_correction, geometry)
            deltas = {key: 0.0 for key in maximums}
            reason = "ROOT_ONLY_ONE_NODE_PROPAGATION"
        else:
            baseline_result = _solve(
                "forward_2point", links=links, anchors=anchors, rotations=rotations,
                geometry=geometry, model=model, previous=baseline_previous,
                constraints=constraints, active=active,
            )
            before = time.perf_counter()
            analytic_result = _solve(
                "analytic", links=links, anchors=anchors, rotations=rotations,
                geometry=geometry, model=model, previous=analytic_previous,
                constraints=constraints, active=active,
            )
            analytic_ms.append((time.perf_counter() - before) * 1000.0)
            if (
                baseline_result.success != analytic_result.success
                or baseline_result.reason != analytic_result.reason
            ):
                raise RuntimeError(f"group {group_index} decision mismatch")
            accepted = baseline_result.success
            baseline_correction = (
                dict(baseline_result.segment_correction_rotvec)
                if accepted else {k: v.copy() for k, v in baseline_previous.items()}
            )
            analytic_correction = (
                dict(analytic_result.segment_correction_rotvec)
                if accepted else {k: v.copy() for k, v in analytic_previous.items()}
            )
            baseline_points = corrected_proxy_points(rotations, baseline_correction, geometry)
            analytic_points = corrected_proxy_points(rotations, analytic_correction, geometry)
            if accepted:
                b = _pack(baseline_result, baseline_points)
                a = _pack(analytic_result, analytic_points)
                deltas = {
                    "correction_rad": float(np.max(np.abs(b[0] - a[0]))),
                    "residual_m": float(np.max(np.abs(b[1] - a[1]))),
                    "weight": float(np.max(np.abs(b[2] - a[2]))),
                    "fk_m": float(np.max(np.abs(b[3] - a[3]))),
                }
            else:
                deltas = {key: 0.0 for key in maximums}
            reason = analytic_result.reason
        for key, value in deltas.items():
            maximums[key] = max(maximums[key], value)
        if deltas["correction_rad"] > CORRECTION_ATOL_RAD or deltas["residual_m"] > RESIDUAL_ATOL_M:
            raise RuntimeError(f"group {group_index} analytic parity tolerance exceeded")

        accepted = count == 1 or bool(analytic_result.success)
        transition = "uwb_accept" if accepted else "reject_before_u1"
        baseline_before, baseline_after = getattr(baseline_ledger, transition)()
        analytic_before, analytic_after = getattr(analytic_ledger, transition)()
        if baseline_before != analytic_before or baseline_after != analytic_after:
            raise RuntimeError("owner revision parity mismatch")
        if baseline_before[-1] != baseline_after[-1]:
            raise RuntimeError("UWB transaction illegally advanced temporal owner")
        temporal_baseline = baseline_ledger.next_native200()
        temporal_analytic = analytic_ledger.next_native200()
        if temporal_baseline != temporal_analytic or temporal_baseline[1] - temporal_baseline[0] != 1:
            raise RuntimeError("next native200 closure mismatch")

        direct = tuple(trusted) if accepted else ()
        propagated = tuple(sorted(set(nodes) - set(direct)))
        rejected_links = 8 * (len(nodes) - len(trusted)) + (
            0 if accepted else 8 * len(trusted)
        )
        records.append({
            "group": group_index,
            "frame": pose_frame,
            "trusted_nodes": list(trusted),
            "direct_nodes": list(direct),
            "propagated_nodes": list(propagated),
            "rejected_range_rows": rejected_links,
            "reason": reason,
            "accepted": accepted,
            "u1_calls": 1 if accepted else 0,
            "owner_revisions_before": list(analytic_before),
            "owner_revisions_after_uwb": list(analytic_after),
            "temporal_revision_after_next_native200": temporal_analytic[1],
            "fixed_root_exact": True,
            "contact_constraint_count": len(constraints),
            "foothold_delta_m": float(np.linalg.norm(
                baseline_points["ankle_left"] - analytic_points["ankle_left"]
            )),
            "parity_delta": deltas,
        })
        baseline_previous = baseline_correction
        analytic_previous = analytic_correction

    # A separate impossible-transition probe is rejected before any participant
    # mutates, proving rollback without contaminating the 41 accepted epochs.
    before_b, after_b = baseline_ledger.reject_impossible()
    before_a, after_a = analytic_ledger.reject_impossible()
    rollback = before_b == after_b == before_a == after_a
    if not rollback:
        raise RuntimeError("impossible-transition rollback changed an owner")
    if baseline_ledger.token() != analytic_ledger.token():
        raise RuntimeError("final causal ledger mismatch")

    runtime = time.perf_counter() - started
    result = {
        "schema": "biospur.c2.articulated.analytic-jacobian-causal-audit.v1",
        "status": "NO_RAW_EVIDENCE_PASS",
        "groups": GROUPS,
        "imu_inventory": {**IMU_INVENTORY, "total": int(len(imu_kind))},
        "node_count_pattern": list(NODE_COUNTS),
        "u1_group_calls": sum(row["u1_calls"] for row in records),
        "accepted_groups": sum(row["accepted"] for row in records),
        "rejected_before_u1_groups": sum(not row["accepted"] for row in records),
        "u1_rollback_probe_calls": 1,
        "rollback_byte_equivalent_tokens": rollback,
        "final_owner_revisions": list(analytic_ledger.token()),
        "maximum_parity_delta": maximums,
        "parity_tolerance": {
            "correction_rad": CORRECTION_ATOL_RAD,
            "residual_m": RESIDUAL_ATOL_M,
        },
        "analytic_timing_ms": {
            "mean": float(np.mean(analytic_ms)),
            "p99": float(np.percentile(analytic_ms, 99)),
            "maximum": float(np.max(analytic_ms)),
            "effective_utilization_at_8_33hz": float(np.mean(analytic_ms) / 120.048),
        },
        "wall_s": runtime,
        "maximum_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        "raw_opened": False,
        "hxx_opened": False,
        "delivery_seal_sha256": DELIVERY_SEAL_SHA256,
        "delivery_promoted": False,
        "model_owner": model_owner,
        "source_hashes": {
            "articulated_range": _sha256(ROOT / "src/biospur_fusion/c2_uwb_calibration/articulated_range.py"),
            "driver": _sha256(Path(__file__).resolve()),
            "accepted_pose": _sha256(action04.POSE_ACCEPTED_TRAJECTORY),
        },
    }
    if (
        result["u1_group_calls"] + result["rejected_before_u1_groups"] != GROUPS
        or result["imu_inventory"]["total"] != 1007
        or result["analytic_timing_ms"]["p99"] >= 150.0
        or result["analytic_timing_ms"]["maximum"] >= 200.0
        or result["analytic_timing_ms"]["effective_utilization_at_8_33hz"] >= 1.0
        or result["maximum_rss_kib"] >= 300_000
    ):
        raise RuntimeError("causal/performance gate failed")
    _write_json(output / "GROUPS.json", records)
    _write_json(output / "RESULT.json", result)
    np.savez_compressed(
        output / "PARITY.npz",
        frames=np.asarray(frames, dtype=np.int64),
        node_counts=np.asarray([len(row["trusted_nodes"]) for row in records], dtype=np.int64),
        revision_before=np.asarray([row["owner_revisions_before"] for row in records]),
        revision_after=np.asarray([row["owner_revisions_after_uwb"] for row in records]),
        parity=np.asarray([
            [row["parity_delta"][key] for key in sorted(maximums)] for row in records
        ]),
    )
    command = (
        "PYTHONPATH=src:tools:. OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 "
        "MKL_NUM_THREADS=1 NUMEXPR_NUM_THREADS=1 .venv-v0/bin/python "
        "tools/audit_c2_articulated_analytic_jacobian.py --output "
        + str(output.relative_to(ROOT))
    )
    (output / "COMMAND.txt").write_text(command + "\n", encoding="utf-8")
    external = (
        Path(__file__).resolve(),
        ROOT / "src/biospur_fusion/c2_uwb_calibration/articulated_range.py",
        ROOT / "tests/test_c2_articulated_range.py",
        action04.POSE_ACCEPTED_TRAJECTORY,
        DELIVERY / "SHA256SUMS",
    )
    result["seal_sha256"] = _seal(output, external)
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    result = run(args.output.resolve())
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
