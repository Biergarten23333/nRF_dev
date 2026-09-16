#!/usr/bin/env python3
"""Build bounded real per-action ten-node raw-range shadow fact tables."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import math
from pathlib import Path
import resource
import time

import numpy as np
from scipy.stats import chi2

from biospur_fusion.c2_coupled_progressive.native200_publication_producer import (
    Native200PublicationProducer,
    accepted_pose_frame,
)
from biospur_fusion.c2_coupled_progressive.calibration_native200_archive import (
    CalibrationNative200Archive,
)
from biospur_fusion.c2_uwb_calibration.antenna_los import rotation_from_wxyz
from biospur_fusion.c2_uwb_calibration.direct_body_shadow_ab import (
    DirectNative200Clock,
    DirectPoseSnapshot,
    direct_shadow_evidence_batch,
)
from biospur_fusion.c2_uwb_calibration.frozen_body_proxy import (
    NODE_TO_PROXY_POINT,
)
from biospur_fusion.c2_uwb_root_world.action00_shadow_trace import (
    C2_BODY_NODES,
    NodeSweepInput,
    evaluate_body_epoch,
    group_rows_by_pelvis_epoch,
)
from biospur_fusion.c2_uwb_root_world.continuous_root_ab import (
    DiagnosticBootstrap,
    bootstrap_action00_root,
    raw_range_reference_ns,
)
from biospur_fusion.c2_uwb_root_world.production_root_admission_policy import (
    DiagnosticContinuitySpec,
    DiagnosticReanchorSpec,
    DiagnosticRootAdmissionMachine,
    DiagnosticRootAdmissionPolicy,
    DiagnosticStatisticalSpec,
    PreparedContinuityInput,
    PreparedRootAdmissionInput,
    PreparedStatisticalInput,
)
from biospur_fusion.c2_uwb_root_world.run_calibration import (
    DATASET,
    LAYOUT,
    PHYSICAL_DIRECTORY,
    _clock_models,
)
from biospur_fusion.c2_uwb_root_world.u0 import decode_uwb_only
from biospur_fusion.root_r3.estimator import RootFilterConfig, propagate_inertial
from biospur_fusion.root_r3.models import RootState


ROOT = Path(__file__).resolve().parents[1]
ACTION = "00_initial_still"
RAW = DATASET / "actions" / PHYSICAL_DIRECTORY[ACTION] / "rep_01/raw/fusion_host_raw.cobs.bin"
CLOCK = ROOT / "logs/c2_uwb_beacon_clock_20260903_141552/CLOCK_TABLE_CALIBRATION_ONLY.json"
PELVIS = "BSFC2CC"
TRANSITION_MODEL_SHA256 = hashlib.sha256(
    b"C2 diagnostic position-only continuity at 0,5,120ms"
).hexdigest()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _json_line(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _anchors() -> np.ndarray:
    rows = sorted(json.loads(LAYOUT.read_text())["anchors"], key=lambda row: int(row["id"]))
    if [int(row["id"]) for row in rows] != list(range(8)):
        raise RuntimeError("canonical A-H anchor inventory changed")
    return np.asarray([[row["x_mm"], row["y_mm"], row["z_mm"]] for row in rows], float) / 1000.0


def _diagnostic_policy(maximum_links: int) -> DiagnosticRootAdmissionPolicy:
    # This is an engineering shadow, not a production/scientific registry.
    false_admission_probability = 0.001
    statistical = DiagnosticStatisticalSpec(
        false_admission_probability,
        tuple(
            (dof, float(chi2.ppf(1.0 - false_admission_probability, dof)))
            for dof in range(1, maximum_links + 1)
        ),
        3,
    )
    continuity = DiagnosticContinuitySpec(
        (0.0, 0.005, 0.12),
        (0.05, 0.06, 0.25),
        TRANSITION_MODEL_SHA256,
    )
    reanchor = DiagnosticReanchorSpec(0.005, 0.12, 3, 0.25, 10, 4)
    return DiagnosticRootAdmissionPolicy(
        statistical, continuity, reanchor,
        "ACTION00_REAL_RAW_SHADOW_DIAGNOSTIC_NOT_PRODUCTION_R",
    )


def _state_at(
    query_s: float,
    times_s: np.ndarray,
    states: list[RootState],
    forces: np.ndarray,
    rotations: np.ndarray,
    config: RootFilterConfig,
) -> RootState:
    index = int(np.searchsorted(times_s, query_s, side="right")) - 1
    if index < 0 or index >= len(states):
        raise ValueError("query outside pure-IMU state support")
    state = states[index]
    if query_s <= state.time_s + 1e-12:
        return state
    return propagate_inertial(
        state, query_s, forces[index], rotations[index], config,
    )[0]


def _bounded_candidate(prediction: RootState, candidate: RootState, limit_m: float) -> RootState:
    delta = candidate.position_m - prediction.position_m
    norm = float(np.linalg.norm(delta))
    bounded = delta if norm <= limit_m else delta * (limit_m / norm)
    vector = prediction.vector.copy()
    vector[:3] += bounded
    return RootState(prediction.time_s, vector, prediction.covariance.copy())


def _continuity(policy, prediction: RootState, candidate: RootState) -> PreparedContinuityInput:
    delta = candidate.position_m - prediction.position_m
    return PreparedContinuityInput(
        policy.continuity.prediction_horizons_s,
        np.stack([delta] * len(policy.continuity.prediction_horizons_s)),
        policy.continuity.transition_model_sha256,
        policy.continuity.digest,
    )


def _link_record(link, evidence) -> dict:
    return {
        "anchor": int(link.anchor),
        "range_m": float(link.range_m),
        "quality_sigma_m": float(link.sigma_m),
        "information_weight": float(link.information_weight),
        "antenna_facing_score": float(evidence.own_facing_score),
        "torso_occlusion_severity": float(evidence.torso_severity),
        "limb_occlusion_severity": float(evidence.limb_severity),
        "large_body_weight": float(evidence.a_large_weight),
        "combined_body_weight": float(evidence.b_combined_weight),
        "link_dt_s": float(link.link_dt_s),
    }


def run(
    output: Path,
    *,
    action: str = ACTION,
    initial_root_m: np.ndarray | None = None,
) -> dict:
    output = output.resolve()
    if output.exists() or ROOT not in output.parents:
        raise ValueError("output must be a new directory under Fusion_Part")
    output.mkdir(parents=False)
    started = time.monotonic()
    if action not in PHYSICAL_DIRECTORY:
        raise ValueError("action is not in the acquired calibration inventory")
    producer = Native200PublicationProducer.from_sealed_archives()
    archive = CalibrationNative200Archive.from_sealed_archives()
    source = archive.actions[action]
    pelvis_clock = producer._clocks[ACTION]
    clocks = _clock_models(CLOCK)
    anchors = _anchors()
    raw = (
        DATASET / "actions" / PHYSICAL_DIRECTORY[action]
        / "rep_01/raw/fusion_host_raw.cobs.bin"
    )
    rows, decode = decode_uwb_only(raw)
    groups = group_rows_by_pelvis_epoch(rows, clocks)

    pose_global_ns = np.asarray([
        pelvis_clock.global_ns(int(timer)) for timer in source.time_us
    ], dtype=np.int64)
    pose_time_s = pose_global_ns.astype(float) * 1e-9
    pose_valid = np.logical_and.reduce([
        np.asarray(source.trajectory[segment]["mask"], dtype=bool)
        for segment in source.trajectory
    ])
    pose_clock = DirectNative200Clock(
        action=action,
        time_root_s=source.trajectory["pelvis"]["time_root_s"],
        source_pelvis_timer_us=source.time_us,
        source_contiguous_span_id=source.span,
        common_clock_a_ns_per_us=pelvis_clock.a_ns_per_us,
        common_clock_b_ns=pelvis_clock.b_ns,
        valid_mask=pose_valid,
    )
    rotations = np.asarray([
        producer._alignment @ rotation_from_wxyz(value)
        for value in source.sensor_quat_wxyz
    ])
    forces = np.asarray(source.calibrated_acc_mps2, dtype=float)

    eligible_groups = []
    for group in groups:
        references = []
        for row in group:
            try:
                raw_ns, _ = raw_range_reference_ns(row, clocks[row.node])
            except ValueError:
                continue
            references.append(raw_ns)
        if references and pose_global_ns[1] < min(references) and max(references) < pose_global_ns[-1]:
            eligible_groups.append(group)
    if not eligible_groups:
        raise RuntimeError("no Action00 body epochs overlap native-200 pose support")

    bootstrap = None
    bootstrap_attempts = []
    if action == ACTION:
        for row in (
            row for group in eligible_groups for row in group if row.node == PELVIS
        ):
            _, candidate_ns = raw_range_reference_ns(row, clocks[PELVIS])
            try:
                candidate = bootstrap_action00_root(
                    row, measurement_time_ns=candidate_ns,
                    anchors_m=anchors, clock=clocks[PELVIS],
                )
            except RuntimeError as error:
                bootstrap_attempts.append({
                    "sweep": int(row.sweep), "reference_ns": candidate_ns,
                    "accepted": False, "reason": str(error),
                })
                continue
            bootstrap_attempts.append({
                "sweep": int(row.sweep), "reference_ns": candidate_ns,
                "accepted": True, "reason": "EARLIEST_FIXED_GATE_PASS",
                "residual_rms_m": candidate.residual_rms_m,
            })
            bootstrap = candidate
            break
    else:
        if initial_root_m is None:
            raise ValueError("non-Action00 shadow trace requires the Action00 root gauge")
        initial = np.asarray(initial_root_m, dtype=float).reshape(3)
        if not np.isfinite(initial).all():
            raise ValueError("initial root gauge must be finite")
        first_reference_ns = min(
            raw_range_reference_ns(row, clocks[row.node])[0]
            for row in eligible_groups[0]
            if len([
                slot for slot in range(8)
                if row.valid_mask & (1 << slot)
                and 0 < row.ranges_mm[slot] < 0xFFFF
            ]) >= 4
        )
        covariance = np.eye(9)
        covariance[:3, :3] *= 0.25
        state = RootState(
            first_reference_ns * 1e-9,
            np.r_[initial, np.zeros(6)], covariance,
        )
        bootstrap = DiagnosticBootstrap(
            state, tuple(range(8)), 1.0, 0.0,
            "ACTION00_ROOT_GAUGE_SEED_FOR_SHADOW_SOLVER_ONLY",
        )
        bootstrap_attempts.append({
            "accepted": True,
            "reason": "ACTION00_ROOT_GAUGE_SEED_FOR_SHADOW_SOLVER_ONLY",
            "reference_ns": int(round(first_reference_ns)),
        })
    if bootstrap is None:
        raise RuntimeError("no causally earliest pelvis bootstrap passed the fixed gate")
    eligible_groups = [
        group for group in eligible_groups
        if np.median([
            raw_range_reference_ns(row, clocks[row.node])[0]
            for row in group
            if len([
                slot for slot in range(8)
                if row.valid_mask & (1 << slot)
                and 0 < row.ranges_mm[slot] < 0xFFFF
            ]) >= 4
        ]) * 1e-9 >= bootstrap.state.time_s
    ]
    config = RootFilterConfig()
    first_source_index = int(np.searchsorted(
        pose_time_s, bootstrap.state.time_s, side="left",
    ))
    states: list[RootState] = [bootstrap.state]
    supported_times_rows = [float(bootstrap.state.time_s)]
    supported_forces_rows = [forces[first_source_index]]
    supported_rotations_rows = [rotations[first_source_index]]
    state = bootstrap.state
    for index in range(first_source_index, len(pose_time_s)):
        time_s = pose_time_s[index]
        state = propagate_inertial(
            state, float(time_s), forces[index], rotations[index], config,
        )[0]
        states.append(state)
        supported_times_rows.append(float(time_s))
        supported_forces_rows.append(forces[index])
        supported_rotations_rows.append(rotations[index])
    supported_times = np.asarray(supported_times_rows, dtype=float)
    supported_forces = np.asarray(supported_forces_rows, dtype=float)
    supported_rotations = np.asarray(supported_rotations_rows, dtype=float)

    policy = _diagnostic_policy(80)
    machine = DiagnosticRootAdmissionMachine(policy, bootstrap.state)
    reason_counts = Counter()
    direct_counts = Counter()
    candidate_deltas = []
    facts_path = output / "BODY_EPOCH_FACTS.jsonl"
    written = 0
    solver_seed_root = bootstrap.state.position_m.copy()
    root_bounds = (anchors.min(axis=0) - 0.75, anchors.max(axis=0) + 0.75)
    with facts_path.open("w", encoding="utf-8") as stream:
        for sequence, group in enumerate(eligible_groups):
            row_references = {
                row.node: raw_range_reference_ns(row, clocks[row.node])[0]
                for row in group
                if len([
                    slot for slot in range(8)
                    if row.valid_mask & (1 << slot) and 0 < row.ranges_mm[slot] < 0xFFFF
                ]) >= 4
            }
            if not row_references:
                continue
            reference_ns = float(np.median(list(row_references.values())))
            prediction = _state_at(
                reference_ns * 1e-9, supported_times, states, supported_forces,
                supported_rotations, config,
            )
            sweep_inputs = []
            unavailable = {}
            for row in group:
                query_ns = row_references.get(row.node)
                if query_ns is None:
                    unavailable[row.node] = "FEWER_THAN_FOUR_VALID_LINKS"
                    continue
                try:
                    pose_index = pose_clock.strict_floor(query_ns)
                except ValueError as error:
                    unavailable[row.node] = str(error)
                    continue
                frame = pose_index.frame
                if frame < 1:
                    unavailable[row.node] = "NO_PREVIOUS_NATIVE200_FRAME"
                    continue
                _, points, normals = accepted_pose_frame(
                    source.trajectory, frame, producer._alignment, producer._geometry,
                )
                _, previous_points, _ = accepted_pose_frame(
                    source.trajectory, frame - 1, producer._alignment, producer._geometry,
                )
                offsets = {
                    node: points[name]
                    for node, name in NODE_TO_PROXY_POINT.items()
                }
                dt = (pose_global_ns[frame] - pose_global_ns[frame - 1]) * 1e-9
                offset_velocity = {
                    node: (offsets[node] - previous_points[name]) / dt
                    for node, name in NODE_TO_PROXY_POINT.items()
                }
                row_state = _state_at(
                    query_ns * 1e-9, supported_times, states,
                    supported_forces, supported_rotations, config,
                )
                snapshot = DirectPoseSnapshot(
                    action, frame, pose_index.pose_global_ns, query_ns,
                    pose_index.age_ns, row_state.position_m, offsets, normals, points,
                )
                evidence = direct_shadow_evidence_batch(
                    node=row.node, anchor_positions_world_m=anchors,
                    snapshot=snapshot, geometry=producer._geometry,
                )
                sweep_inputs.append(NodeSweepInput(
                    row, clocks[row.node], offsets[row.node],
                    offset_velocity[row.node], evidence,
                    pose_index.pose_global_ns, pose_index.age_ns,
                ))
            if not sweep_inputs:
                continue
            fact = evaluate_body_epoch(
                epoch_sequence=sequence, imu_prediction=prediction,
                sweeps=sweep_inputs, anchors_m=anchors,
                solver_seed_root_m=solver_seed_root,
                solver_seed_velocity_mps=np.zeros(3),
                root_bounds_m=root_bounds,
            )
            if (
                fact.shared_result is not None
                and fact.shared_result.success
                and len(fact.direct_nodes) >= 4
            ):
                solver_seed_root = fact.shared_result.root_position_m.copy()
            admission = None
            if fact.shared_result is not None and fact.shared_result.success and fact.direct_nodes:
                candidate_vector = prediction.vector.copy()
                candidate_vector[:3] = fact.shared_result.root_position_m
                candidate = RootState(
                    prediction.time_s, candidate_vector, prediction.covariance.copy(),
                )
                bounded = _bounded_candidate(
                    prediction, candidate, policy.continuity.maximum_position_effect_m[0],
                )
                direct_links = [
                    link for node_fact in fact.node_facts if node_fact.direct_usable
                    for link in node_fact.links
                ]
                effective_sigma = np.asarray([
                    link.sigma_m / math.sqrt(link.information_weight)
                    for link in direct_links
                ])
                statistical = PreparedStatisticalInput(
                    fact.shared_result.residuals_m,
                    np.diag(np.square(effective_sigma)),
                    len(direct_links), policy.statistical.false_admission_probability,
                    policy.statistical.threshold_for_dof(len(direct_links)),
                    fact.shared_result.rank, True, True, True,
                    policy.statistical.digest,
                )
                counts = {node: 0 for node in C2_BODY_NODES}
                for node_fact in fact.node_facts:
                    counts[node_fact.node] = node_fact.valid_links
                prepared_input = PreparedRootAdmissionInput(
                    f"action00-body-epoch-{sequence}", sequence,
                    prediction.time_s,
                    max(item.reference_time_s for item in fact.node_facts),
                    prediction, candidate, bounded, statistical,
                    _continuity(policy, prediction, candidate),
                    _continuity(policy, prediction, bounded),
                    C2_BODY_NODES, fact.direct_nodes, counts, True, policy.digest,
                )
                decision = machine.commit(machine.prepare(prepared_input))
                admission = {
                    "disposition": decision.disposition.value,
                    "reason": decision.reason,
                    "self_consistency_nis": decision.nis,
                    "self_consistency_threshold": statistical.nis_threshold,
                    "temporal_count": decision.temporal_count,
                    "direct_nodes": list(decision.direct_nodes),
                    "propagated_nodes": list(decision.propagated_nodes),
                }
                reason_counts[decision.reason] += 1
                candidate_deltas.append(float(np.linalg.norm(fact.candidate_delta_m)))
            direct_counts[len(fact.direct_nodes)] += 1
            node_rows = []
            for node_fact in fact.node_facts:
                node_rows.append({
                    "node": node_fact.node,
                    "sweep": node_fact.sweep,
                    "reference_time_s": node_fact.reference_time_s,
                    "valid_links": node_fact.valid_links,
                    "direct_usable": node_fact.direct_usable,
                    "direct_reason": node_fact.direct_reason,
                    "candidate_root_world_m": node_fact.result.root_position_m.tolist(),
                    "residual_rms_m": node_fact.residual_rms_m,
                    "rank": node_fact.result.rank,
                    "condition": node_fact.result.condition,
                    "links": [
                        _link_record(link, node_fact.evidence[link.anchor])
                        for link in node_fact.links
                    ],
                })
            stream.write(_json_line({
                "schema": "biospur.c2.calibration.raw_shadow.body_epoch.v1",
                "action": action,
                "epoch_sequence": sequence,
                "reference_time_s": fact.reference_time_s,
                "imu_prediction_root_world_m": prediction.position_m.tolist(),
                "imu_prediction_velocity_world_mps": prediction.velocity_mps.tolist(),
                "nodes_present": [row.node for row in group],
                "pose_or_link_unavailable": unavailable,
                "direct_nodes": list(fact.direct_nodes),
                "propagated_nodes": list(fact.propagated_nodes),
                "shared_candidate_root_world_m": (
                    None if fact.shared_result is None
                    else fact.shared_result.root_position_m.tolist()
                ),
                "candidate_delta_m": (
                    None if fact.candidate_delta_m is None
                    else fact.candidate_delta_m.tolist()
                ),
                "shared_residual_rms_m": (
                    None if fact.shared_result is None or not len(fact.shared_result.residuals_m)
                    else float(np.sqrt(np.mean(np.square(fact.shared_result.residuals_m))))
                ),
                "admission_shadow": admission,
                "nodes": node_rows,
            }) + "\n")
            written += 1

    result = {
        "schema": "biospur.c2.calibration.raw_shadow.result.v1",
        "status": "REAL_CALIBRATION_ACTION_SHADOW_COMPLETE",
        "action": action,
        "product_ready": False,
        "scientific_pass": False,
        "output_mutation": False,
        "raw_mutation": False,
        "clock_owner": str(CLOCK),
        "clock_owner_sha256": _sha256(CLOCK),
        "raw_path": str(raw),
        "raw_sha256": _sha256(raw),
        "layout_path": str(LAYOUT),
        "layout_sha256": _sha256(LAYOUT),
        "decode": decode.__dict__,
        "bootstrap_attempts": bootstrap_attempts,
        "body_epochs_written": written,
        "direct_node_count_histogram": dict(sorted(direct_counts.items())),
        "admission_reason_counts": dict(sorted(reason_counts.items())),
        "candidate_delta_norm_m": {
            "count": len(candidate_deltas),
            "median": float(np.median(candidate_deltas)) if candidate_deltas else None,
            "p95": float(np.quantile(candidate_deltas, 0.95)) if candidate_deltas else None,
            "maximum": max(candidate_deltas) if candidate_deltas else None,
        },
        "policy": {
            "digest": policy.digest,
            "provenance": policy.provenance,
            "diagnostic_only": True,
            "continuity_horizons_s": list(policy.continuity.prediction_horizons_s),
            "continuity_limits_m": list(policy.continuity.maximum_position_effect_m),
            "reanchor_consecutive_epochs": policy.reanchor.minimum_consecutive_credible_epochs,
            "statistical_false_admission_probability": policy.statistical.false_admission_probability,
        },
        "wall_s": time.monotonic() - started,
        "rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
    }
    (output / "RESULT.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--action", choices=tuple(PHYSICAL_DIRECTORY), default=ACTION)
    parser.add_argument("--initial-root", nargs=3, type=float)
    args = parser.parse_args()
    print(json.dumps(run(
        args.output,
        action=args.action,
        initial_root_m=(
            None if args.initial_root is None else np.asarray(args.initial_root)
        ),
    ), indent=2, sort_keys=True))
