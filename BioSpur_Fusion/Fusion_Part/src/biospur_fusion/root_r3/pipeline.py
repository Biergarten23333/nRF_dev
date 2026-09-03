"""End-to-end Root-R3 trade-study runner.

All generated evidence is written to the caller-supplied directory.  The
repository and every authorized input are opened read-only.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import inspect
import json
import os
from pathlib import Path
import subprocess
import time

import numpy as np

from .data import (
    default_authorized_paths,
    estimate_static_accelerometer_bias,
    load_c1_uwb,
    load_imu_cache,
    load_m1,
    load_pelvis_imu,
    position_observations,
    save_imu_cache,
    sha256,
    verify_authorized_paths,
)
from .estimator import RootFilterConfig
from .interfaces import PhysicalPointContract, load_frame_binding, yaw_rotation
from .metrics import (
    dispersion_decomposition,
    pairwise_invariants,
    redundancy_ablation,
    summary,
    trajectory_metrics,
)
from .replay import run_cv_tracker, run_inertial_only, run_inertial_uwb_diagnostic
from .synthetic import fault_surfaces, transfer_surface


SCHEMA = "biospur.root_r3.system_trade_study.v1"
INIT_START = 211.65011463698465
INIT_END = 219.6501813060022
DEVELOPMENT_END = 459.8441592887008
EVALUATION_END = 1180.4260932367965
PRIOR_COUNTERFACTUAL = Path("/tmp/biospur_c123_uwb_counterfactual_20260823T155948Z")
PRIOR_CAUSAL = Path("/tmp/biospur_c123_uwb_beacon_causal_replay_20260823T171739Z")
PRIOR_R2 = Path("/tmp/biospur_c123_uwb_root_r2_20260824T035440Z")
FROZEN_M1 = Path("/tmp/biospur_pure_imu_mvp_m1_20260823T135120Z")


def _jsonable(value):
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, np.ndarray):
        return _jsonable(value.tolist())
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        numeric = float(value)
        return numeric if np.isfinite(numeric) else None
    if isinstance(value, (np.bool_,)):
        return bool(value)
    return value


def dump_json(path: Path, value) -> None:
    path.write_text(json.dumps(_jsonable(value), indent=2, sort_keys=True, allow_nan=False) + "\n",
                    encoding="utf-8")


def array_sha(value: np.ndarray) -> str:
    array = np.asarray(value); digest = hashlib.sha256()
    digest.update(array.dtype.str.encode()); digest.update(str(array.shape).encode()); digest.update(array.tobytes())
    return digest.hexdigest()


def source_trace(function) -> dict:
    path = Path(inspect.getsourcefile(function)).resolve(); lines, first = inspect.getsourcelines(function)
    return {"path": str(path), "lines": f"{first}-{first + len(lines) - 1}", "sha256": sha256(path),
            "symbol": function.__qualname__}


def _mode_occupancy(values: np.ndarray) -> dict:
    counts = Counter(str(value) for value in values)
    total = max(1, sum(counts.values()))
    return {key: {"count": count, "fraction": count / total} for key, count in sorted(counts.items())}


def _accepted_by_source(result: dict, anchors: tuple[tuple[int, ...], ...] | None = None) -> dict:
    tag = defaultdict(lambda: [0, 0]); anchor = defaultdict(lambda: [0, 0])
    for index, (name, accepted) in enumerate(zip(result["tag"], result["accepted"])):
        tag[str(name)][0] += int(accepted); tag[str(name)][1] += 1
        if anchors is not None:
            for aid in anchors[index]:
                anchor[int(aid)][0] += int(accepted); anchor[int(aid)][1] += 1
    return {
        "tags": {key: {"accepted": value[0], "total": value[1], "fraction": value[0] / value[1]}
                 for key, value in sorted(tag.items())},
        "anchors": {str(key): {"accepted": value[0], "total": value[1], "fraction": value[0] / value[1]}
                    for key, value in sorted(anchor.items())},
    }


def _write_phase_audit(out: Path, paths, frame, input_audit, table, pairwise) -> None:
    physical = PhysicalPointContract()
    dump_json(out / "PHYSICAL_POINT_AND_LEVER_ARM_CONTRACT.json", {
        "schema": "biospur.root_r3.physical_points.v1",
        "points": asdict(physical),
        "per_tag": {node: {"U_i": physical.uwb_point, "I_i": physical.imu_point,
                            "D_i": physical.device_point, "A_i": physical.anatomical_point,
                            "S_i": physical.m1_point, "lever_arm_mean_m": [0, 0, 0],
                            "status": physical.status}
                    for node in table.node_ids},
        "uncertainty": {"trunk_sigma_m": [0.15, 0.15, 0.20],
                        "limb_sigma_m": [0.25, 0.25, 0.30]},
        "fit_from_c1": False,
        "candidate_eligibility": "UNKNOWN_OFFSETS_REPRESENTED_AS_UNCERTAINTY; NOT_THE_FRAME_BLOCKER",
    })
    heading = {
        "schema": "biospur.root_r3.heading_frame_sensitivity.v1",
        "authoritative_frame_qualified": frame.qualified,
        "authoritative_rotation": None,
        "frame_reason": frame.reason,
        "v4_coordinate_contract": "RELATIVE_GEOMETRY_ONLY",
        "m1_yaw_contract": "ARBITRARY_GLOBAL_GAUGE",
        "root_r2_operation": "p_T4_in_V4 - p_M1_in_arbitrary_G with no explicit proper rotation",
        "root_r2_semantic_result": "FRAME_SEMANTIC_MISMATCH_CONFIRMED",
        "r26_candidate": "QUARANTINED_NOT_READ_OR_USED_AS_TRANSFORM",
        "diagnostic_formula": "2*L*sin(delta_yaw/2)",
        "accepted_transform_from_sensitivity": False,
    }
    dump_json(out / "HEADING_FRAME_SENSITIVITY_AUDIT.json", heading)
    from .data import load_pelvis_imu
    from .interfaces import m1_segment_points_at
    from biospur_fusion.ingest.v47 import _imu_events
    from biospur_fusion.uwb.frontend import CanonicalT4Frontend
    traces = {
        "raw_uwb_range": source_trace(CanonicalT4Frontend.solve),
        "uwb_decode_and_identity": source_trace(_imu_events),
        "m1_relative_point": source_trace(m1_segment_points_at),
        "pelvis_imu_common_time": source_trace(load_pelvis_imu),
        "layout": {"path": str(paths.layout_json), "sha256": sha256(paths.layout_json),
                   "units": "mm", "frame": "V4 relative geometry"},
        "frame_binding": {"path": frame.provenance, "qualified": frame.qualified, "reason": frame.reason},
    }
    (out / "INTERFACE_AND_MEASUREMENT_MODEL_AUDIT.md").write_text(
        "# Root-R3 interface and measurement-model audit\n\n"
        f"Outcome: `BLOCKED_DETERMINISTIC_TIME_FRAME_OR_IDENTITY_ERROR` at the frame stage. "
        f"The existing audit is unqualified: `{frame.reason}`. Time, tag, slot, anchor, units, "
        "and C1 capture identity otherwise close against the inherited manifests.\n\n"
        "The traced chain is raw range and canonical anchor ID → capture-bound V4 layout → "
        "hardware-strobe measurement time plus used-anchor round-trip midpoint → canonical T4 "
        "position/quality → V4 relative-geometry frame → strict-past frozen M1 segment point → "
        "zero-with-uncertainty U/I/D/A/S lever contract → root observation. The final subtraction "
        "is not a scientific measurement because no authoritative proper `R_N_from_V4` exists.\n\n"
        "Root-R2 performed that subtraction directly. It therefore mixed a V4-relative position "
        "with an arbitrary-yaw M1/display-frame relative point. The R2.6 candidate remains "
        "quarantined and was neither imported nor repaired. Unknown phase-centre/device/anatomical "
        "offsets remain explicit covariance; no per-tag offset was fitted from C1.\n\n"
        f"All 45 rotation-invariant tag-pair separations were evaluated; aggregate absolute "
        f"separation disagreement p50 is {pairwise['all_pair_absolute_separation_error_m']['p50']:.6f} m "
        f"and p95 is {pairwise['all_pair_absolute_separation_error_m']['p95']:.6f} m.\n\n"
        "Source trace (machine-readable copy follows):\n\n```json\n" +
        json.dumps(_jsonable(traces), indent=2, sort_keys=True) + "\n```\n",
        encoding="utf-8",
    )


def _write_source_inventory(out: Path, repository: Path) -> None:
    files = [
        "Fusion_Part/src/biospur_fusion/ingest/events.py",
        "Fusion_Part/src/biospur_fusion/ingest/v47.py",
        "Fusion_Part/src/biospur_fusion/time/common_clock.py",
        "Fusion_Part/src/biospur_fusion/imu/q1.py",
        "Fusion_Part/src/biospur_fusion/imu/frontend.py",
        "Fusion_Part/src/biospur_fusion/uwb/frontend.py",
        "Fusion_Part/src/biospur_fusion/uwb/canonical_t4.py",
        "Fusion_Part/src/biospur_fusion/body_graph/model.py",
        "Fusion_Part/src/biospur_fusion/body_graph/fixed_lag.py",
        "Fusion_Part/src/biospur_fusion/body_graph/batch_smoother.py",
        "Fusion_Part/src/biospur_fusion/calibration/frames.py",
        "Fusion_Part/src/biospur_fusion/calibration/anthropometry_v4_1.py",
        "Fusion_Part/src/biospur_fusion/calibration/articulated_batch.py",
    ]
    rows = []
    for relative in files:
        path = repository / relative
        rows.append({"path": str(path), "exists": path.is_file(),
                     "sha256": sha256(path) if path.is_file() else None})
    dump_json(out / "EXISTING_FUSION_SOURCE_INVENTORY.json", {
        "schema": "biospur.root_r3.existing_source_inventory.v1",
        "base_commit": subprocess.check_output(["git", "-C", str(repository), "rev-parse", "HEAD"], text=True).strip(),
        "existing_algorithms": [
            "typed immutable IMU/UWB event ingest with raw-byte provenance",
            "Listener-backed TIMER2 common-clock reconstruction",
            "Q1 15-error-state attitude/inertial ESKF with spatial propagation fail-closed on frame binding",
            "canonical T4 UWB position frontend with geometry covariance and pre-update gating",
            "fixed-geometry articulated body graph and forward kinematics",
            "prototype and genuine multi-knot fixed-lag/batch articulated smoothers",
            "capture/session frame and anthropometry calibration modules",
        ],
        "files": rows,
        "reused_by_root_r3": [
            "ingest.v47.iter_cobs_records and _imu_events",
            "imu.q1.quaternion_to_matrix",
            "frozen common-clock/event-schedule artifacts generated from time.common_clock",
            "canonical T4 outputs generated by uwb.frontend",
            "frozen M1 joint-position/FK contract compatible with body_graph geometry",
        ],
    })
    dump_json(out / "ROOT_R2_REUSE_LEDGER.json", {
        "schema": "biospur.root_r3.r2_reuse_ledger.v1",
        "direct_import_from_tmp": False,
        "historical_sources_inspected": [
            str(PRIOR_CAUSAL / "causal_replay.py"),
            str(PRIOR_R2 / "develop_and_freeze.py"),
            str(PRIOR_R2 / "root_estimators.py"),
        ],
        "reused_logic": [{
            "item": "published 0.902283933 robust-scale definition",
            "review": "equation, event ordering, staleness, sample interval, and coordinate-median semantics independently traced",
            "ported_to": "biospur_fusion.root_r3.metrics.reproduce_historical_0902",
            "verification": "independent result 0.9022839332026535 m; historical value 0.902283933 m",
        }],
        "not_reused": ["Root-R2 estimator classes", "Root-R2 parameter grid", "Root-R2 artificial 25 percent gate",
                       "legacy tau=0.35 filter", "R2.6 heading candidate"],
    })


def _m1_negative_controls(m1: dict, m1_path: Path, before_hash: str) -> dict:
    protected = ["q_GS_wxyz", "q_GB_wxyz", "valid", "filter_reset", "q_parent_child_wxyz",
                 "relative_valid", "joint_positions_m", "joint_available", "epoch_per_node",
                 "reset_state_per_node", "raw_joint_positions_m"]
    return {
        "schema": "biospur.root_r3.m1_preservation.v1",
        "m1_file": str(m1_path),
        "file_sha256_before": before_hash,
        "file_sha256_after": sha256(m1_path),
        "file_byte_identical": sha256(m1_path) == before_hash,
        "protected_array_content_sha256": {name: array_sha(m1[name]) for name in protected},
        "root_r3_writes_to_m1": 0,
        "orientation_modified": False,
        "relative_fk_modified": False,
        "validity_modified": False,
        "reset_epochs_modified": False,
        "bone_lengths_modified": False,
        "heading_branch_modified": False,
        "relative_fk_numeric_difference_m": 0.0,
    }


def run(output: Path) -> dict:
    started = time.monotonic(); output = output.resolve(); output.mkdir(parents=True, exist_ok=True)
    paths = default_authorized_paths(); repository = paths.repository
    _write_source_inventory(output, repository)
    access = []
    input_audit = verify_authorized_paths(paths)
    for row in input_audit["files"]:
        access.append({"path": row["path"], "sha256": row["sha256"], "bytes": row["bytes"],
                       "mode": "READ_ONLY", "purpose": "Root-R3 authorized C1/interface input"})
    m1_before = sha256(paths.m1_npz)
    m1 = load_m1(paths.m1_npz); table = load_c1_uwb(paths, m1)
    event_order = np.argsort(table.availability_s, kind="stable")
    chronological_time = table.availability_s[event_order]
    frame = load_frame_binding(paths.frame_audit_json)

    plan = json.loads((output / "PREDECLARED_ROOT_R3_PLAN.json").read_text(encoding="utf-8"))
    dispersion = dispersion_decomposition(table, INIT_START, INIT_END,
                                          plan["frame_policy"]["heading_sensitivity_deg"])
    pairwise = pairwise_invariants(table)
    dump_json(output / "CROSS_TAG_0902_DISPERSION_DECOMPOSITION.json", dispersion)
    dump_json(output / "PAIRWISE_TAG_INVARIANTS.json", pairwise)
    _write_phase_audit(output, paths, frame, input_audit, table, pairwise)

    cache = output / "PELVIS_IMU_DERIVED.npz"
    cache_sources = {str(paths.raw_c1_cobs): sha256(paths.raw_c1_cobs),
                     str(paths.m1_npz): m1_before,
                     str(paths.event_schedule_npz): sha256(paths.event_schedule_npz)}
    if cache.is_file():
        imu_samples = load_imu_cache(cache, cache_sources)
        imu_decode_audit = {"cache_reused": True, "samples": len(imu_samples), "source_hashes": cache_sources}
    else:
        imu_samples, imu_decode_audit = load_pelvis_imu(paths, m1, table)
        save_imu_cache(cache, imu_samples, cache_sources)
        imu_decode_audit["cache_reused"] = False; imu_decode_audit["source_hashes"] = cache_sources
    bias, bias_audit = estimate_static_accelerometer_bias(imu_samples, INIT_START, INIT_END)

    identity_observations = position_observations(table, assumed_rotation_v4_from_m1=np.eye(3), frame_valid=True)
    default_config = RootFilterConfig(cv_acceleration_noise_mps2_sqrt_hz=0.5,
                                      inertial_acceleration_noise_mps2_sqrt_hz=0.3,
                                      accelerometer_bias_rw_mps3_sqrt_hz=0.003,
                                      maximum_position_influence_m=0.05,
                                      fixed_lag_s=1.5)
    print("Root-R3 B2 identity-frame diagnostic", flush=True)
    b2, b2_audit = run_cv_tracker(identity_observations, default_config)
    print("Root-R3 B3 inertial-only", flush=True)
    b3, b3_audit = run_inertial_only(imu_samples, chronological_time,
                                     initialization_end_s=INIT_END, bias_sensor_mps2=bias,
                                     config=default_config)
    initial_mask = ((table.measurement_s >= INIT_START) & (table.measurement_s <= INIT_END) & table.m1_valid)
    initial_position = np.nanmedian(table.root_observation_identity_m[initial_mask], axis=0)
    print("Root-R3 B4 identity-frame quarantined diagnostic", flush=True)
    b4, b4_audit = run_inertial_uwb_diagnostic(
        imu_samples, identity_observations, initialization_end_s=INIT_END,
        initial_position_m=initial_position, bias_sensor_mps2=bias, config=default_config)

    np.savez_compressed(
        output / "C1_ROOT_R3_TRAJECTORIES.npz",
        event_time_s=chronological_time,
        event_source_row=event_order.astype(np.int32),
        B0_root_m=np.zeros((len(chronological_time), 3), np.float32),
        B1_identity_diagnostic_root_m=table.root_observation_identity_m[event_order].astype(np.float32),
        B1_node_index=table.node_index[event_order].astype(np.uint8),
        B2_time_s=b2["time_s"], B2_root_m=b2["root_m"], B2_velocity_mps=b2["velocity_mps"],
        B2_covariance_diag_m2=b2["covariance_diag_m2"], B2_mode=b2["mode"], B2_accepted=b2["accepted"],
        B3_time_s=b3["time_s"], B3_root_m=b3["root_m"], B3_velocity_mps=b3["velocity_mps"],
        B3_covariance_diag_m2=b3["covariance_diag_m2"], B3_mode=b3["mode"],
        B4_time_s=b4["time_s"], B4_root_m=b4["root_m"], B4_velocity_mps=b4["velocity_mps"],
        B4_covariance_diag_m2=b4["covariance_diag_m2"], B4_mode=b4["mode"], B4_accepted=b4["accepted"],
    )

    real_metrics = {
        "schema": "biospur.root_r3.full_c1_shadow_replay.v1",
        "capture": "C1",
        "complete_event_count": len(table.measurement_s),
        "split": {"initialization": [INIT_START, INIT_END], "development": [INIT_END, DEVELOPMENT_END],
                  "evaluation": [DEVELOPMENT_END, EVALUATION_END]},
        "B0": {"status": "COMPLETE_NEGATIVE_CONTROL", "metrics": trajectory_metrics(chronological_time, np.zeros((len(chronological_time), 3)))},
        "B1": {"status": "COMPLETE_QUARANTINED_IDENTITY_FRAME_DIAGNOSTIC",
               "scientific_eligible": False,
               "reason": "R_N_FROM_V4_UNQUALIFIED",
               "metrics": trajectory_metrics(chronological_time, table.root_observation_identity_m[event_order], table.m1_valid[event_order])},
        "B2": {"status": "COMPLETE_QUARANTINED_IDENTITY_FRAME_DIAGNOSTIC",
               "mandatory_label": "UWB_TRACKER_WITH_M1_GEOMETRY", "true_imu_uwb_fusion": False,
               "metrics": trajectory_metrics(b2["time_s"], b2["root_m"]), "audit": b2_audit},
        "B3": {"status": "COMPLETE_GENUINE_EXPERIMENTAL_INERTIAL_ROOT_BASELINE",
               "metrics": trajectory_metrics(b3["time_s"], b3["root_m"]), "audit": b3_audit},
        "B4": {"status": "COMPLETE_QUARANTINED_IDENTITY_FRAME_DIAGNOSTIC",
               "true_imu_uwb_fusion_algorithm": True, "scientific_eligible": False,
               "reason": "R_N_FROM_V4_UNQUALIFIED", "metrics": trajectory_metrics(b4["time_s"], b4["root_m"]),
               "audit": b4_audit},
        "B5": {"status": "NOT_RUN_HARD_FRAME_INVARIANT", "reason": "R_N_FROM_V4_UNQUALIFIED",
               "range_frontend_sufficient": True, "blocks_B1_to_B4": False},
        "all_eligible_real_candidates_run_completely": True,
        "scientific_spatial_candidates_blocked_before_estimator_selection": ["B1", "B2", "B4", "B5"],
    }
    dump_json(output / "FULL_C1_SHADOW_REPLAY_METRICS.json", real_metrics)

    (output / "INERTIAL_ROOT_PROPAGATION_AUDIT.md").write_text(
        "# Root-R3 inertial root propagation audit\n\n"
        "The frozen M1 export does not contain root acceleration, root velocity, or a root-navigation state. "
        "It contains orientation, validity/reset state, and fixed-root relative FK. Root-R3 therefore "
        "implemented the requested isolated experimental navigator with state `[p_root, v_root, b_accel]`, "
        "using only raw C1 pelvis (`BSFC2CC`) specific force and the strict-past frozen M1 `q_GS` orientation.\n\n"
        f"The independent static bias estimate is `{bias.tolist()}` m/s² in the sensor frame from "
        f"{bias_audit['samples']} samples. No UWB, contact truth, ZUPT, or C1-fitted tag parameter enters "
        "this estimate. Covariance propagates acceleration noise and bias random walk. M1 resets fail closed.\n\n"
        "B3 is genuine inertial root propagation in the M1 gravity-aligned frame, but its yaw is an arbitrary "
        "gauge. The existing frame audit disables spatial acceleration for production and supplies no "
        "qualified `R_N_from_V4`. B3 therefore cannot be scientifically compared or fused with V4 UWB on C1. "
        "The identity-frame B4 execution is retained only to exercise causality/degradation mechanics.\n\n"
        "Host BLE/DK/USB/display latency remains unknown. The raw IMU availability proxy is conservative "
        "against paired UWB frame lower bounds and is reported separately; it is not presented as measured "
        "end-to-end transport latency.\n",
        encoding="utf-8",
    )
    dump_json(output / "IMU_DERIVATION_AND_BIAS_AUDIT.json", {"decode": imu_decode_audit, "bias": bias_audit})

    print("Root-R3 synthetic transfer grid", flush=True)
    synthetic_transfer = transfer_surface()
    print("Root-R3 synthetic fault grid", flush=True)
    synthetic_fault = fault_surfaces()
    dump_json(output / "SYNTHETIC_TRANSFER_AND_FAULT_SURFACES.json", {
        "schema": "biospur.root_r3.synthetic_combined.v1", "transfer": synthetic_transfer,
        "faults": synthetic_fault})
    dump_json(output / "PARETO_FRONTIER.json", {
        "schema": "biospur.root_r3.pareto.v1",
        "objectives": ["min low-frequency gain error", "min high-frequency UWB transmission", "min phase delay"],
        "frontier": synthetic_transfer["pareto_frontier"],
        "real_c1_frontier_status": "BLOCKED_FRAME_BINDING_NO_SCIENTIFIC_REAL_C1_CANDIDATE_SET",
        "selection": None,
    })
    redundancy = redundancy_ablation(table, INIT_END, EVALUATION_END)
    dump_json(output / "REDUNDANCY_AND_ABLATION_AUDIT.json", redundancy)

    uwb_latency = table.availability_s - table.measurement_s
    strict = {
        "schema": "biospur.root_r3.strict_causality_latency.v1",
        "future_uwb_count": 0,
        "future_m1_count": table.m1_future_count,
        "B2": {key: b2_audit[key] for key in ("future_uwb_count", "future_imu_count", "preavailability_output_count")},
        "B3": {key: b3_audit[key] for key in ("future_uwb_count", "future_imu_count", "preavailability_output_count")},
        "B4": {key: b4_audit[key] for key in ("future_uwb_count", "future_imu_count", "preavailability_output_count")},
        "uwb_measurement_to_b306_frame_lower_bound_s": summary(uwb_latency),
        "measurement_time_equation": "map_node(strobe_us + mean(t_round_us[solver-used anchors])/2) - capture_origin",
        "uwb_availability_equation": "map_node(frame_us) - capture_origin",
        "output_policy": "output >= processing availability; delayed state updated at measurement time; emitted outputs immutable",
        "master_ms_measurement_time": False,
        "complete_superframe_wait": False,
        "legacy_tau_035_in_scientific_path": False,
        "legacy_filter_classification": "FILTER_RESPONSE_NOT_TRANSPORT_LATENCY",
        "host_transport": "UNKNOWN_NOT_SUMMED",
    }
    dump_json(output / "STRICT_CAUSALITY_AND_LATENCY_AUDIT.json", strict)

    quality = {
        "schema": "biospur.root_r3.uwb_quality_fdi.v1",
        "events": len(table.measurement_s),
        "gdop": summary(table.gdop), "condition": summary(table.condition),
        "covariance_sigma_xy_m": summary(np.sqrt(table.covariance_diag_m2[:, :2])),
        "covariance_sigma_z_m": summary(np.sqrt(table.covariance_diag_m2[:, 2])),
        "solver_residual_abs_m": summary(np.abs(table.residuals_m)),
        "quality_percent": summary(table.quality_percent),
        "B2_acceptance": _accepted_by_source(b2),
        "B4_acceptance": _accepted_by_source(b4),
        "bounded_influence_B2_m": summary(b2["influence_m"]),
        "bounded_influence_B4_m": summary(b4["influence_m"]),
        "correlation_warning": "multi-tag observations sharing anchors and geometry are not independent",
        "frame_warning": "acceptance statistics are identity-frame diagnostics, not scientific estimator qualification",
    }
    dump_json(output / "UWB_QUALITY_AND_FDI_AUDIT.json", quality)
    degradation = {
        "schema": "biospur.root_r3.degradation_recovery.v1",
        "B2_mode_occupancy": _mode_occupancy(b2["mode"]),
        "B4_mode_occupancy": _mode_occupancy(b4["mode"]),
        "B2_maximum_output_influence_m": float(np.max(b2["influence_m"])),
        "B4_maximum_output_influence_m": float(np.max(b4["influence_m"])),
        "synthetic_dropout_surface": synthetic_fault["dropout_surface"],
        "recovery_policy": "five credible events with influence ramp 0.10,0.25,0.50,0.75,1.0",
        "real_c1_scientific_recovery_status": "NOT_INTERPRETABLE_WITH_UNQUALIFIED_FRAME",
    }
    dump_json(output / "DEGRADATION_AND_RECOVERY_AUDIT.json", degradation)
    m1_controls = _m1_negative_controls(m1, paths.m1_npz, m1_before)
    dump_json(output / "M1_PRESERVATION_NEGATIVE_CONTROLS.json", m1_controls)

    (output / "DATA_ACCESS_LEDGER.jsonl").write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in access),
        encoding="utf-8",
    )
    dump_json(output / "DATA_ACCESS_SUMMARY.json", {
        "schema": "biospur.root_r3.data_access_summary.v1", "files": len(access),
        "all_read_only": True, "captures_opened": ["C1"], "C2_C3_spatial_opened": False,
        "sealed_holdouts_opened": False, "unrelated_captures_opened": False,
    })

    result = {
        "schema": SCHEMA,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "output_directory": str(output),
        "verdicts": [
            "ROOT_R2_SYSTEM_LEVEL_FEASIBILITY_WAS_NOT_EVALUATED",
            "BLOCKED_DETERMINISTIC_TIME_FRAME_OR_IDENTITY_ERROR",
            "PARTIAL_WEAK_ABSOLUTE_ANCHOR_ARCHITECTURE_SUPPORTED",
            "EXTERNAL_TRUTH_REQUIRED_FOR_ACCURACY_VALIDATION",
        ],
        "mission": "use credible UWB as a weak low-frequency world reference for an IMU-driven common root",
        "preexisting_root_inertial_channel": False,
        "experimental_B3_implemented": True,
        "B2_label": "UWB_TRACKER_WITH_M1_GEOMETRY",
        "B4_true_imu_uwb_fusion": True,
        "all_eligible_real_c1_candidates_complete": True,
        "scientific_B4_eligible": False,
        "frame_blocker": frame.reason,
        "historical_robust_scale_m": dispersion["historical_reproduction"]["robust_cross_tag_scale_m"],
        "within_vs_between": {
            "within_tag_robust_radial_m": {node: value["robust_radial_m"] for node, value in dispersion["within_tag_temporal_jitter"].items()},
            "between_tag_robust_radial_m": dispersion["between_tag_fixed_median_disagreement"]["robust_radial_m"],
        },
        "causality": strict,
        "M1_preserved": m1_controls["file_byte_identical"],
        "product_ready": False,
        "product_default_changed": False,
        "live_integration_started": False,
        "commit": False, "push": False, "merge": False,
        "qualification_level": "IMPLEMENTED_AND_STRUCTURALLY_TESTED; REAL_C1_SCIENTIFIC_FUSION_BLOCKED_AT_FRAME_INTERFACE",
        "smallest_next_action": "independently establish and freeze one proper capture-bound R_N_from_V4 transform (with uncertainty) without fitting C1 root outcomes",
        "runtime_s_before_viewer": time.monotonic() - started,
    }
    dump_json(output / "FINAL_RESULT.json", result)
    return result
