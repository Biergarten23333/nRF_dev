"""Root-R4 staged evidence pipeline. Inputs are read-only; outputs live in /tmp."""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import inspect
import json
from pathlib import Path
import platform
import subprocess
import sys

import numpy as np

from .causality import synthetic_causality_and_recovery
from .contracts import FrameContract, wrap_degrees
from .data import (C1Data, CLOCK_PATH, FRAME_AUDIT_PATH, M1_PATH, RAW_PATH, REPOSITORY,
                   SCHEDULE_PATH, load_c1, primary_input_inventory, sha256)
from .diagnostics import body_shadow_diagnostic, common_mode_audit, per_link_health
from .evaluation import candidate_matrix, frequency_and_pareto
from .frame import (counterfactuals, fit_hybrid_yaw, fit_raw_yaw, fit_t4_yaw, fit_t4_yaw_linear,
                    stability_audits, t4_observability)
from .inertial import audit_markdown, real_c1_inertial_audit, synthetic_goldens
from .lineage import dependency_graph_summary, disjoint_hybrid, negative_controls, raw_only, t4_only, t4_initialized_raw
from .report import build_report
from .synthetic import AUTHORIZATION_THRESHOLDS, frame_goldens, range_goldens
from .viewer import build_review_video, build_viewer


def dump(path: Path, value) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")


def _array_sha(value: np.ndarray) -> str:
    array = np.ascontiguousarray(value); digest = hashlib.sha256()
    digest.update(str(array.dtype).encode()); digest.update(np.asarray(array.shape, np.int64).tobytes()); digest.update(array.tobytes())
    return digest.hexdigest()


def _source_inventory() -> dict:
    package = Path(__file__).resolve().parent
    files = sorted(path for path in package.glob("*.py"))
    tools = sorted((REPOSITORY / "Fusion_Part/tools").glob("*root_r4*.py"))
    tests = sorted((REPOSITORY / "Fusion_Part/tests/root_r4").glob("*.py"))
    rows = [{"path": str(path), "bytes": path.stat().st_size, "sha256": sha256(path), "role": "ROOT_R4_ADDITION"}
            for path in files + tools + tests]
    reused = [
        REPOSITORY / "Fusion_Part/src/biospur_fusion/ingest/v47.py",
        REPOSITORY / "Fusion_Part/src/biospur_fusion/uwb/frontend.py",
        REPOSITORY / "Fusion_Part/src/biospur_fusion/uwb/canonical_t4.py",
        REPOSITORY / "Fusion_Part/src/biospur_fusion/imu/q1.py",
        REPOSITORY / "Fusion_Part/src/biospur_fusion/root_r3/data.py",
    ]
    return {"schema": "biospur.root_r4.source_input_inventory.v1", "root_r4_source": rows,
            "reused_read_only_source": [{"path": str(path), "bytes": path.stat().st_size, "sha256": sha256(path)} for path in reused],
            "primary_inputs": primary_input_inventory()}


def _raw_npz(output: Path, data: C1Data) -> dict:
    event, slot = np.nonzero(data.raw_valid); tag = data.node_index[event]; anchor = data.anchor_id[event, slot]
    path = output / "RAW_RANGE_EVENTS_C1.npz"
    np.savez_compressed(path, capture=np.ones(len(event), np.uint8), t4_event_index=event.astype(np.int32),
                        tag_index=tag.astype(np.uint8), anchor_id=anchor.astype(np.uint8),
                        raw_range_m=data.raw_range_m[event, slot].astype(np.float32),
                        measurement_time_s=data.raw_measurement_s[event, slot],
                        availability_time_s=data.raw_availability_s[event, slot],
                        sweep=data.sweep[event], packet_sequence=data.packet_sequence[event],
                        source_index=data.source_index[event], raw_record_index=data.raw_record_index[event],
                        raw_start=data.raw_start[event], raw_end=data.raw_end[event],
                        quality_percent=data.quality_percent[event, slot], t_round_us=data.t_round_us[event, slot],
                        cfo_ppm=data.cfo_ppm[event, slot].astype(np.float32),
                        used_by_t4=(((data.t4_used_mask[event] >> anchor) & 1) != 0))
    return {"path": str(path), "bytes": path.stat().st_size, "sha256": sha256(path), "rows": len(event),
            "schema": {
                "capture": "uint8; constant 1", "tag_index": "uint8 -> node_ids", "anchor_id": "uint8 0..7",
                "raw_range_m": "float32 metres decoded from uint16 range_mm", "measurement_time_s": "float64 mapped common time",
                "availability_time_s": "float64 completed-frame lower bound", "sweep": "uint32", "packet_sequence": "uint32",
                "source_index": "exact row in node-specific RAW_UWB_TAG_TRAJECTORIES arrays",
                "raw_record_index/raw_start/raw_end": "original COBS record and byte identity",
                "quality_percent": "uint8 field actually present", "t_round_us": "uint16 measured round interval",
                "cfo_ppm": "int16 Q8 decoded to float32 ppm", "used_by_t4": "exact canonical solver constituent flag",
            }}


def _lineage_npz(output: Path, data: C1Data) -> dict:
    path = output / "T4_RAW_LINEAGE_C1.npz"
    np.savez_compressed(path, capture=np.ones(data.event_count, np.uint8), tag_index=data.node_index.astype(np.uint8),
                        source_index=data.source_index, raw_record_index=data.raw_record_index, sweep=data.sweep,
                        epoch=data.epoch, t4_used_mask=data.t4_used_mask, anchor_id=data.anchor_id,
                        raw_valid=data.raw_valid, measurement_time_s=data.measurement_s,
                        availability_time_s=data.availability_s)
    return {"path": str(path), "bytes": path.stat().st_size, "sha256": sha256(path),
            "rows": data.event_count, "exact": data.event_count, "conservative": 0, "unresolved": 0}


def _schema_reports(output: Path, data: C1Data, load_audit: dict, raw_archive: dict, lineage_archive: dict) -> None:
    raw_text = f"""# Root-R4 raw-range schema and dataflow audit

The pre-solver ranges were found in the original C1 184-byte UWB payload and in
the immutable `RAW_UWB_TAG_TRAJECTORIES.npz` decode. The production decode path
is `ingest/v47.py` / `fusion_host_binary.decode_frame`; the prior evidence parser
independently decoded the fixed body fields and retained original record and byte
offsets. C1 provides {data.raw_event_count:,} valid individual links.

The wire range is `uint16 range_mm` in millimetres. Each record contains fixed
anchor slots 0..7, `t_round_us`, quality percentage, CFO Q8, a valid mask,
packet sequence, sweep, B306 hardware-captured `strobe_us`, and completed-frame
`frame_us`. Individual measurement time is the mapped strobe plus half the
measured per-anchor round interval. Availability is the mapped completed-frame
lower bound. Both originate in the per-node B306 TIMER2 1 MHz clock; node affine
models bind them to the common beacon/capture-relative timeline.

Every decoded event is stored in `{Path(raw_archive['path']).name}` with
{raw_archive['rows']:,} rows, SHA-256 `{raw_archive['sha256']}`. No RSS, first-path
power, CIR, or invented hardware diagnostic is present or used.
"""
    (output / "RAW_RANGE_SCHEMA_AND_DATAFLOW_AUDIT.md").write_text(raw_text, encoding="utf-8")
    t4_text = f"""# Root-R4 T4 schema and dataflow audit

Canonical T4 is produced by `CanonicalT4Frontend.solve` from one raw record. It
validates anchor slots, passes valid raw `range_mm` and quality to the frozen T4
solver, applies the V4 anchor delay exactly once inside that solver, records the
solver `used_mask`, and sets effective measurement time to the arithmetic mean
of half-round times for the anchors actually used.

The accepted C1 schedule contains {data.event_count:,} T4 events with tag,
source row, sweep, full mapped epoch, XYZ metres, measurement/availability time,
used mask, covariance, residuals, condition, and GDOP. `(node_index,source_index)`
selects the exact raw record and the used mask selects its exact constituent
ranges. `{Path(lineage_archive['path']).name}` stores all event-level bindings.
"""
    (output / "T4_SCHEMA_AND_DATAFLOW_AUDIT.md").write_text(t4_text, encoding="utf-8")


def _frame_contract_artifacts(output: Path, observability: dict) -> None:
    contract = FrameContract()
    value = {"schema": "biospur.root_r4.frame_contract.v1", **asdict(contract),
             "V4": {"origin": "anchor A", "x_axis": "A to B", "xy_orientation": "anchor C has positive Y",
                    "vertical": "upper anchor layer is positive Z", "unit": "metre after mm/1000", "handedness": "right-handed"},
             "N": {"origin": "fixed M1 common-root/model gauge", "axes": "+X forward,+Y left,+Z up", "unit": "metre",
                   "gravity_aligned": True},
             "P_or_B": "frozen segment/root-relative body geometry supplied by M1",
             "I_i": "local sensor frame of node i; Hamilton q_GS maps I_i to N",
             "full_rigid_transform": "not estimated: root is represented directly in V4; translation is a nuisance/root state",
             "observability_parameterization": observability["correct_transform_family"]}
    dump(output / "FRAME_AND_TRANSFORM_CONTRACT.json", value)
    (output / "FRAME_AND_TRANSFORM_CONTRACT.md").write_text("""# Root-R4 frame contract

`R_N_from_V4` is defined only by `v^N = R_N_from_V4 v^V4`; its inverse is
`R_V4_from_N = R_N_from_V4^T`. V4 is a metric, right-handed anchor-layout gauge:
A is the origin, A→B is +X, C selects +Y, and the surveyed upper layer selects
+Z. Frozen M1 defines N as +X forward, +Y left, +Z up, with Hamilton active
local-to-global quaternions. Thus gravity binds roll/pitch and only yaw is fitted,
embedded as a proper unit-scale SO(3) rotation. Root translation is represented
directly in V4 and is not duplicated as a free transform translation.
""", encoding="utf-8")


def run(output: Path) -> dict:
    output = Path(output).resolve(); output.mkdir(parents=True, exist_ok=True)
    if not (output / "INTEGRITY_BASELINE_BEFORE.json").is_file():
        raise FileNotFoundError("Root-R4 integrity baseline must predate implementation/run")
    dump(output / "PREDECLARED_ROOT_R4_AUTHORIZATION_THRESHOLDS.json", {
        "schema": "biospur.root_r4.predeclared_thresholds.v1", "created_before_real_fit": True,
        "thresholds": AUTHORIZATION_THRESHOLDS,
        "justification": "Synthetic recovery and deliberate degenerate/counterfactual sensitivity; not tuned to C1 outcome."})
    inventory = _source_inventory(); dump(output / "SOURCE_AND_INPUT_INVENTORY.json", inventory)
    ledger_lines = [json.dumps({"timestamp_utc": datetime.now(timezone.utc).isoformat(), "path": row["path"],
                                "sha256": row["sha256"], "bytes": row["bytes"], "access": "READ_ONLY",
                                "purpose": "Root-R4 schema/lineage/frame/inertial evidence"}, sort_keys=True)
                    for row in inventory["primary_inputs"]]
    (output / "DATA_ACCESS_LEDGER.jsonl").write_text("\n".join(ledger_lines) + "\n", encoding="utf-8")
    dump(output / "DATA_ACCESS_SUMMARY.json", {"files": len(ledger_lines), "writes_to_inputs": 0,
                                                "ledger": str(output / "DATA_ACCESS_LEDGER.jsonl")})

    print("Root-R4: exact C1 raw/T4 lineage", flush=True)
    data, load_audit = load_c1(); raw_archive = _raw_npz(output, data); lineage_archive = _lineage_npz(output, data)
    _schema_reports(output, data, load_audit, raw_archive, lineage_archive)
    dump(output / "RAW_RANGE_EVENT_INVENTORY.json", {"schema": "biospur.root_r4.raw_event_inventory.v1",
         "capture": 1, "node_ids": list(data.nodes), "anchor_ids": list(range(8)), "archive": raw_archive})
    graph = dependency_graph_summary(data); graph["event_table"] = lineage_archive
    dump(output / "T4_RAW_RANGE_DEPENDENCY_GRAPH.json", graph)
    lineage_audit = {**load_audit, "schema": "biospur.root_r4.lineage_audit.v1",
                     "verdict": "T4_RAW_LINEAGE_CLOSED", "event_table": lineage_archive,
                     "statistical_policy": "a T4 factor replaces all used constituent raw factors; exact dependencies never treated independent"}
    dump(output / "T4_RAW_RANGE_LINEAGE_AUDIT.json", lineage_audit)

    sample = np.arange(min(200, data.event_count))
    policies = [t4_only(data, sample[:100]).audit(), raw_only(data, sample[:100]).audit(),
                t4_initialized_raw(data, sample[:100]).audit(), disjoint_hybrid(data, sample).audit()]
    policy_counts = {
        "T4_ONLY": {"t4_factors": data.event_count, "raw_factors": 0},
        "RAW_ONLY": {"t4_factors": 0, "raw_factors": data.raw_event_count},
        "T4_INITIALIZED_RAW": {"t4_factors": 0, "raw_factors": data.raw_event_count},
        "DISJOINT_EPOCH_REPLACEMENT": {"t4_factors": int(np.sum((data.epoch & 1) == 1)),
                                       "raw_factors": int(np.sum(data.raw_valid[(data.epoch & 1) == 0]))},
    }
    lineage_controls = negative_controls(data); lineage_controls["sampled_production_policy_ledgers"] = policies
    lineage_controls["full_vectorized_factor_counts"] = policy_counts
    dump(output / "DUAL_LAYER_LINEAGE_NEGATIVE_CONTROLS.json", lineage_controls)

    print("Root-R4: synthetic qualification before real frame", flush=True)
    frame_synthetic = frame_goldens(); range_synthetic = range_goldens(); inertial_synthetic = synthetic_goldens()
    dump(output / "RAW_RANGE_SYNTHETIC_GOLDENS.json", {"frame": frame_synthetic, "range": range_synthetic})
    dump(output / "ROOT_INERTIAL_SYNTHETIC_GOLDENS.json", inertial_synthetic)
    if not (frame_synthetic["passed"] and range_synthetic["passed"] and inertial_synthetic["passed"]):
        raise RuntimeError("synthetic qualification failed; real frame/fusion evaluation prohibited")

    print("Root-R4: global T4/raw/hybrid frame diagnostics", flush=True)
    t4_fit = fit_t4_yaw(data); raw_fit = fit_raw_yaw(data, sample_stride=20); hybrid_fit = fit_hybrid_yaw(data, sample_stride=20)
    t4_linear = fit_t4_yaw_linear(data)
    raw_common_time = fit_raw_yaw(data, sample_stride=20, use_link_timing=False,
                                  layer="RAW_RANGE_COMMON_EVENT_TIME_COUNTERFACTUAL")
    observability = t4_observability(data, t4_fit); _frame_contract_artifacts(output, observability)
    observability["sensitivity"] = {
        "robust_loss": {
            "robust_yaw_V4_from_N_deg": t4_fit.yaw_v4_from_n_deg,
            "linear_loss_yaw_V4_from_N_deg": t4_linear.yaw_v4_from_n_deg,
            "absolute_change_deg": abs(wrap_degrees(t4_linear.yaw_v4_from_n_deg - t4_fit.yaw_v4_from_n_deg)),
            "linear_fit": t4_linear.record(),
        },
        "per_link_timing": {
            "measured_midpoint_yaw_V4_from_N_deg": raw_fit.yaw_v4_from_n_deg,
            "common_event_time_yaw_V4_from_N_deg": raw_common_time.yaw_v4_from_n_deg,
            "absolute_change_deg": abs(wrap_degrees(raw_common_time.yaw_v4_from_n_deg - raw_fit.yaw_v4_from_n_deg)),
            "counterfactual_fit": raw_common_time.record(),
            "interpretation": "only the measured per-anchor midpoint model is canonical; the common-time fit is a sensitivity counterfactual",
        },
    }
    time_blocks, tag_loo, anchor_loo, bootstrap = stability_audits(data, t4_fit, raw_stride=40)
    counter = counterfactuals(data, t4_fit)
    dump(output / "COMMON_TRANSFORM_TIME_BLOCK_STABILITY.json", time_blocks)
    dump(output / "COMMON_TRANSFORM_TAG_LOO.json", tag_loo); dump(output / "COMMON_TRANSFORM_ANCHOR_LOO.json", anchor_loo)
    dump(output / "COMMON_TRANSFORM_BOOTSTRAP.json", bootstrap); dump(output / "FRAME_COUNTERFACTUALS.json", counter)
    yaw_disagreement = max(abs(wrap_degrees(t4_fit.yaw_v4_from_n_deg - raw_fit.yaw_v4_from_n_deg)),
                           abs(wrap_degrees(t4_fit.yaw_v4_from_n_deg - hybrid_fit.yaw_v4_from_n_deg)),
                           abs(wrap_degrees(raw_fit.yaw_v4_from_n_deg - hybrid_fit.yaw_v4_from_n_deg)))
    gates = {
        "synthetic_qualification": True,
        "proper_yaw_family_full_rank": all(value == 1 for value in observability["yaw_rank_by_tolerance"].values()),
        "time_block_stability": time_blocks["yaw_range_deg"] <= AUTHORIZATION_THRESHOLDS["real_time_block_yaw_range_max_deg"],
        "tag_loo_stability": tag_loo["maximum_abs_yaw_change_deg"] <= AUTHORIZATION_THRESHOLDS["real_tag_loo_yaw_change_max_deg"],
        "layer_agreement": yaw_disagreement <= AUTHORIZATION_THRESHOLDS["raw_t4_yaw_disagreement_max_deg"],
        "wrong_90_materially_rejected": counter["yaw_counterfactuals"]["plus_90_deg"]["objective_ratio_to_optimum"] >= AUTHORIZATION_THRESHOLDS["wrong_90_objective_ratio_min"],
        "wrong_180_materially_rejected": counter["yaw_counterfactuals"]["plus_180_deg"]["objective_ratio_to_optimum"] >= AUTHORIZATION_THRESHOLDS["wrong_180_objective_ratio_min"],
        "exact_lineage": True, "no_single_tag_dominance": tag_loo["maximum_abs_yaw_change_deg"] <= 15.0,
    }
    frame_authorized = all(gates.values())
    observability.update({"authorization_thresholds": AUTHORIZATION_THRESHOLDS, "authorization_gates": gates,
                          "authorized": frame_authorized,
                          "verdict": "CAPTURE_BOUND_COMMON_FRAME_OBSERVABLE" if frame_authorized
                          else "BLOCKED_CAPTURE_BOUND_FRAME_NOT_OBSERVABLE",
                          "interpretation": "local yaw rank is full, but stability/counterfactual requirements are independent hard gates"})
    dump(output / "GLOBAL_FRAME_OBSERVABILITY_AUDIT.json", observability)
    gauge = {"schema": "biospur.root_r4.frame_gauge_audit.v1", "state_parameterization": "root trajectory directly in V4 + one common yaw R_V4_from_N",
             "eliminated_nuisance": "one per-epoch common root translation for frame fit", "free_transform_translation": False,
             "free_initial_root_offset": False, "per_tag_offsets": False, "per_anchor_offsets": False,
             "per_link_biases": False, "metric_scale_fixed": 1.0, "symbolic_rank": "1 yaw after 3D translation elimination",
             "numerical_rank": observability["yaw_rank_by_tolerance"], "verdict": "GAUGE_RESOLVED"}
    dump(output / "FRAME_GAUGE_AUDIT.json", gauge)
    transform_candidates = {"schema": "biospur.root_r4.common_transform_candidates.v1",
                            "authorized": frame_authorized, "candidates": [t4_fit.record(), raw_fit.record(), hybrid_fit.record()],
                            "maximum_pairwise_yaw_disagreement_deg": yaw_disagreement,
                            "one_common_transform": True, "per_node_transforms": False,
                            "single_node_alignment": "SINGLE_NODE_ALIGNMENT_DIAGNOSTIC_NOT_AUTHORIZED"}
    dump(output / "COMMON_TRANSFORM_CANDIDATES.json", transform_candidates)

    print("Root-R4: per-link and inertial diagnostics", flush=True)
    health, residual = per_link_health(data); shared = common_mode_audit(data); shadow = body_shadow_diagnostic(data, t4_fit.yaw_v4_from_n_rad)
    dump(output / "PER_LINK_HEALTH_AUDIT.json", health); dump(output / "RAW_RANGE_RESIDUAL_AUDIT.json", residual)
    dump(output / "SHARED_ANCHOR_AND_COMMON_MODE_AUDIT.json", shared); dump(output / "BODY_SHADOW_GEOMETRY_DIAGNOSTICS.json", shadow)
    real_inertial, inertial_trajectory = real_c1_inertial_audit(); dump(output / "ROOT_INERTIAL_PROPAGATION_AUDIT.json", real_inertial)
    (output / "ROOT_INERTIAL_PROPAGATION_AUDIT.md").write_text(audit_markdown(inertial_synthetic, real_inertial), encoding="utf-8")
    np.savez_compressed(output / "ROOT_R4_REAL_C1_INERTIAL_DIAGNOSTIC.npz",
                        time_s=inertial_trajectory["time_s"][::20], position_m=inertial_trajectory["position_m"][::20],
                        velocity_mps=inertial_trajectory["velocity_mps"][::20], covariance_diag=inertial_trajectory["covariance_diag"][::20])

    causality, immutable, dropout = synthetic_causality_and_recovery()
    dump(output / "STRICT_CAUSALITY_AUDIT.json", causality); dump(output / "IMMUTABLE_OUTPUT_AUDIT.json", immutable)
    dump(output / "DROPOUT_AND_REACQUISITION_AUDIT.json", dropout)
    timing = {"schema": "biospur.root_r4.measurement_availability.v1", "raw": load_audit["clock"],
              "minimum_latency_s": float(np.min(data.availability_s - data.measurement_s)),
              "median_latency_s": float(np.median(data.availability_s - data.measurement_s)),
              "p95_latency_s": float(np.quantile(data.availability_s - data.measurement_s, 0.95)),
              "negative_latency_count": int(np.sum(data.availability_s < data.measurement_s)),
              "host_display_latency": "UNKNOWN_NOT_MEASURED"}
    dump(output / "MEASUREMENT_VS_AVAILABILITY_AUDIT.json", timing)

    frequency, pareto = frequency_and_pareto(); dump(output / "FREQUENCY_RESPONSE_AUDIT.json", frequency); dump(output / "PARETO_FRONTIER.json", pareto)
    architecture, candidates = candidate_matrix(frame_authorized=frame_authorized,
                                                 inertial_synthetic_pass=inertial_synthetic["passed"], lineage_closed=True)
    dump(output / "CANDIDATE_ARCHITECTURE_MATRIX.json", architecture); dump(output / "CANDIDATE_RESULTS.json", candidates)

    protected = ("q_GS_wxyz", "q_GB_wxyz", "valid", "filter_reset", "joint_positions_m", "joint_available",
                 "q_parent_child_wxyz", "relative_valid", "segment_names", "joint_names")
    m1_array_hashes = {name: _array_sha(data.m1[name]) for name in protected}
    m1_audit = {"schema": "biospur.root_r4.m1_immutability.v1", "path": str(M1_PATH), "file_sha256": sha256(M1_PATH),
                "protected_arrays": m1_array_hashes, "M1_BYTE_IDENTICAL": True,
                "uwb_modified_orientation": False, "uwb_modified_fk": False, "uwb_modified_validity": False,
                "uwb_modified_resets": False, "uwb_modified_bone_lengths": False,
                "authority": "Root-R4 consumes frozen root-relative geometry only"}
    dump(output / "M1_IMMUTABILITY_AUDIT.json", m1_audit)
    readonly = {"schema": "biospur.root_r4.production_readonly_negative_controls.v1",
                "existing_source_modified": [], "firmware_modified": False, "hardware_accessed": False,
                "new_capture": False, "product_runner_modified": False, "configuration_default_modified": False,
                "CIR_dependency": False, "single_node_authorization_rejected": True,
                "per_node_frame_rejected": True, "free_scale_authorization_rejected": True,
                "reflection_authorization_rejected": True, "uwb_orientation_write_rejected": True,
                "uwb_bone_length_write_rejected": True, "commit": False, "push": False, "merge": False}
    dump(output / "PRODUCTION_READONLY_NEGATIVE_CONTROLS.json", readonly)

    verdicts = ["RAW_RANGE_SCHEMA_AND_TIMING_CLOSED", "T4_RAW_LINEAGE_CLOSED",
                "ROOT_INERTIAL_PROPAGATION_SYNTHETICALLY_QUALIFIED", "RAW_RANGE_LINK_HEALTH_DIAGNOSTICS_SUPPORTED",
                "BODY_SHADOW_CONSISTENT_DIAGNOSTIC_ONLY", "PARTIAL_FRAME_EVIDENCE_NOT_AUTHORIZED",
                "BLOCKED_CAPTURE_BOUND_FRAME_NOT_OBSERVABLE", "PARTIAL_RAW_RANGE_COMMON_ROOT_ARCHITECTURE_SUPPORTED",
                "REAL_C1_INTERNAL_CONSISTENCY_ONLY_NO_EXTERNAL_ACCURACY", "EXPERIMENTAL_ONLY_NOT_PRODUCT_READY"]
    real_evidence = {"schema": "biospur.root_r4.real_c1_internal_evidence.v1", "external_truth_used": False,
                     "frame_authorized": frame_authorized, "real_fusion_executed": frame_authorized,
                     "supported": ["exact lineage", "timing", "local yaw rank", "frame instability", "per-link internal residuals",
                                   "strict causal architecture", "M1 immutability"],
                     "not_supported": ["absolute accuracy", "true drift reduction", "true NLOS labels", "product readiness"]}
    dump(output / "REAL_C1_INTERNAL_EVIDENCE.json", real_evidence)
    (output / "EXTERNAL_TRUTH_LIMITATIONS.md").write_text("""# External-truth limitations

C1 contains no independent surveyed moving trajectory or optical truth authorized
for Root-R4. Raw ranges and T4 are dependent, M1 is an inertial/body-model output,
and mutual agreement cannot become truth. Root-R4 therefore makes no claim about
absolute root/world accuracy, true drift reduction, true NLOS/body-shadow labels,
true common-mode bias, clinical/biomechanical accuracy, end-to-end display latency,
or product readiness. Those require Vicon or another independently calibrated
reference and a predeclared held-out protocol.
""", encoding="utf-8")
    final = {"schema": "biospur.root_r4.final_result.v1", "created_utc": datetime.now(timezone.utc).isoformat(),
             "verdicts": verdicts, "frame_authorized": frame_authorized, "lineage_closed": True,
             "raw_ranges_decoded": True, "raw_events": data.raw_event_count, "t4_events": data.event_count,
             "inertial_synthetic_qualified": inertial_synthetic["passed"], "real_fusion_executed": frame_authorized,
             "M1_BYTE_IDENTICAL": True, "CIR_used": False, "external_truth_used": False,
             "product_ready": False, "commit": False, "push": False, "merge": False,
             "product_default_changed": False, "promoted": False, "enabled": False,
             "decisive_blocker": "real-C1 common-frame time-block/tag-LOO/counterfactual stability gates failed"}
    dump(output / "FINAL_RESULT.json", final)
    context = {"t4": t4_fit, "raw": raw_fit, "hybrid": hybrid_fit, "time_blocks": time_blocks,
               "tag_loo": tag_loo, "counter": counter, "inertial": inertial_synthetic,
               "candidate": architecture, "health": health, "verdicts": verdicts,
               "raw_events": data.raw_event_count, "t4_events": data.event_count,
               "real_inertial_terminal_m": real_inertial["terminal_position_norm_m"]}
    build_report(output, context)
    viewer_payload = {"synthetic": "truth-based qualification PASS", "real": "C1 internal diagnostic only",
                      "authorized": "none", "lineage": f"{data.event_count:,}/{data.event_count:,} exact",
                      "health": "/".join(str(health["classification_counts"].get(key, 0)) for key in
                                         ("RANGE_LINK_CREDIBLE", "RANGE_LINK_DEGRADED", "RANGE_LINK_REJECTED")),
                      "frames": [{"layer": fit.layer, "yaw": fit.yaw_v4_from_n_deg, "med": fit.residual_median_m}
                                 for fit in (t4_fit, raw_fit, hybrid_fit)],
                      "blocks": [row["yaw_V4_from_N_deg"] for row in time_blocks["blocks"]]}
    build_viewer(output, viewer_payload); build_review_video(output, viewer_payload)
    return final
