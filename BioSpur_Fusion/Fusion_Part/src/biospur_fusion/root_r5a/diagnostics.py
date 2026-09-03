"""Root-R4 reproduction, stability, timing, and bounded nuisance diagnostics."""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np

from biospur_fusion.root_r4.contracts import wrap_degrees
from biospur_fusion.root_r4.data import C1Data
from biospur_fusion.root_r4.frame import fit_hybrid_yaw, fit_raw_yaw, fit_t4_yaw

from .constants import (ROOT_R4_EXPECTED, TIME_OFFSET_GRID_S,
                        TIMING_DOMINANCE_MODE_RANGE_REDUCTION_MIN)
from .data import SupportDefinition, block_masks, data_at_offset
from .dynamic import block_cross_validation
from .profiles import (centered_t4, circular_profile, profile_set, profile_value,
                       raw_evaluator, raw_groups, t4_evaluator)
from .provenance import sha256


def reproduce_root_r4(root: Path, data: C1Data) -> dict:
    final = json.loads((root / "FINAL_RESULT.json").read_text())
    time = json.loads((root / "COMMON_TRANSFORM_TIME_BLOCK_STABILITY.json").read_text())
    tag = json.loads((root / "COMMON_TRANSFORM_TAG_LOO.json").read_text())
    candidates = json.loads((root / "COMMON_TRANSFORM_CANDIDATES.json").read_text())
    by_layer = {row["layer"]: row for row in candidates["candidates"]}
    artifact = {
        "historical_verdict": final["verdicts"][6],
        "five_block_yaw_range_deg": float(time["yaw_range_deg"]),
        "maximum_tag_loo_yaw_change_deg": float(tag["maximum_abs_yaw_change_deg"]),
        "t4_yaw_deg": float(by_layer["T4"]["yaw_V4_from_N_deg"]),
        "raw_yaw_deg": float(by_layer["RAW_RANGE"]["yaw_V4_from_N_deg"]),
        "hybrid_yaw_deg": float(by_layer["LINEAGE_SAFE_HYBRID"]["yaw_V4_from_N_deg"]),
        "maximum_layer_disagreement_deg": float(candidates["maximum_pairwise_yaw_disagreement_deg"]),
        "real_c1_fused_root_executed": bool(final["real_fusion_executed"]),
        "frame_authorized": bool(final["frame_authorized"]),
    }
    recomputed_t4 = fit_t4_yaw(data)
    recomputed_raw = fit_raw_yaw(data, sample_stride=20)
    recomputed_hybrid = fit_hybrid_yaw(data, sample_stride=20)
    recomputed = {"t4_yaw_deg": recomputed_t4.yaw_v4_from_n_deg,
                  "raw_yaw_deg": recomputed_raw.yaw_v4_from_n_deg,
                  "hybrid_yaw_deg": recomputed_hybrid.yaw_v4_from_n_deg}
    checks = {}
    for key, expected in ROOT_R4_EXPECTED.items():
        actual = artifact[key]
        checks[key] = actual == expected if isinstance(expected, (bool, str)) else round(float(actual), 3) == float(expected)
    checks["t4_numerically_recomputed"] = abs(recomputed["t4_yaw_deg"] - artifact["t4_yaw_deg"]) < 1e-9
    checks["raw_numerically_recomputed"] = abs(wrap_degrees(recomputed["raw_yaw_deg"] - artifact["raw_yaw_deg"])) < 0.002
    checks["hybrid_numerically_recomputed"] = abs(wrap_degrees(recomputed["hybrid_yaw_deg"] - artifact["hybrid_yaw_deg"])) < 0.002
    return {"schema": "biospur.root_r5a.root_r4_reproduction.v1", "artifact_values": artifact,
            "fresh_recomputation": recomputed, "checks": checks, "all_reproduced": all(checks.values()),
            "carried_forward_boundary": ["LOCAL_YAW_RANK_PRESENT_UNDER_REDUCED_FROZEN_MODEL",
              "BLOCKED_CAPTURE_BOUND_FRAME_NOT_PRACTICALLY_IDENTIFIABLE_UNDER_CURRENT_STATIC_FRAME_MODEL_AND_C1_EVIDENCE",
              "FRAME_MODEL_INCONSISTENCY_OR_OMITTED_NUISANCE_CAUSE_UNRESOLVED", "FRAME_NOT_AUTHORIZED",
              "REAL_C1_FUSION_NOT_EXECUTED"]}


def timing_semantics_audit(repository: Path, clock_path: Path) -> dict:
    sources = [repository / "B306_Part/include/biospur_link.h",
               repository / "UWB_Part/FREEZE_INTERFACE.md",
               repository / "UWB_Part/fusion-link/src/src/ss_twr_init.c",
               repository / "Fusion_Part/docs/TIME_CONTRACT_V1.md", clock_path]
    clock = json.loads(clock_path.read_text())
    return {
        "schema": "biospur.root_r5a.timing_semantics.v1",
        "sources": [{"path": str(path), "bytes": path.stat().st_size, "sha256": sha256(path)} for path in sources],
        "range_protocol_meaning": "SS-TWR range is the mean of distance at broadcast poll TX and response RX",
        "t_round_us_meaning": "masked DW1000 response-RX minus broadcast poll-TX, nearest-microsecond quantized",
        "measurement_equivalent_time": "B306 hardware strobe common poll epoch + measured t_round_us/2",
        "measurement_time_is_single_physical_instant": False,
        "measurement_time_semantics_status": "BOUNDED_MEASUREMENT_EQUIVALENT_MIDPOINT_NOT_EXACT_SINGLE_INSTANT",
        "uncertainty_components": {"t_round_midpoint_quantization_max_us": 0.25,
            "hardware_strobe_capture_max_us": 100.0,
            "common_clock_schedule_fit_max_us": 910.0,
            "C1_listener_relative_disagreement_max_us": 157.729,
            "frozen_M1_export_cadence_ms": 16.667},
        "predeclared_global_offset_bound_s": [-0.020, 0.020],
        "bound_derivation": "outward bound from same B306 clock plus strobe/mapping error, one 5 ms IMU sample, and one 16.667 ms M1 export interval; frozen before real offset profile",
        "availability_time": "mapped B306 completed-frame time; node-side lower bound",
        "master_arrival_ms": "DK/master receipt diagnostic, not host receive/parse completion",
        "host_receive_complete_captured": False, "host_parse_complete_captured": False,
        "NODE_CLOCK_CAUSALITY_SYNTHETICALLY_QUALIFIED": True,
        "HOST_DEPLOYMENT_CAUSALITY_NOT_YET_CLOSED": True,
        "clock_contract_schema": clock.get("schema"),
    }


def time_offset_profiles(data: C1Data, support: SupportDefinition) -> tuple[dict, dict[float, dict[str, list[dict]]]]:
    masks = block_masks(support); all_profiles = {}; rows = []
    for offset in TIME_OFFSET_GRID_S:
        shifted = data_at_offset(data, float(offset))
        t4 = profile_set(shifted, masks, "T4"); raw = profile_set(shifted, masks, "RAW")
        all_profiles[float(offset)] = {"T4": t4, "RAW": raw}
        row = {"offset_s": float(offset), "layers": {}}
        for layer, profiles in (("T4", t4), ("RAW", raw)):
            modes = [profile["global_mode_deg"] for profile in profiles[:5]]
            combined = profiles[-1]
            row["layers"][layer] = {"combined_mode_deg": combined["global_mode_deg"],
                "combined_objective": combined["objective_minimum"],
                "combined_interval_width_95_deg": combined["asymptotic_profile_interval_width_95_deg"],
                "block_mode_range_deg": float(np.ptp(np.unwrap(np.deg2rad(modes))) * 180 / math.pi),
                "block_modes_deg": modes, "support_samples": combined["support"]["samples"]}
        rows.append(row)
    baseline = next(row for row in rows if row["offset_s"] == 0.0)
    best = {}
    for layer in ("T4", "RAW"):
        candidate = min(rows, key=lambda row: row["layers"][layer]["combined_objective"])
        minimum_range = min(row["layers"][layer]["block_mode_range_deg"] for row in rows)
        base_range = baseline["layers"][layer]["block_mode_range_deg"]
        best[layer] = {"best_global_offset_s": candidate["offset_s"],
                       "best_combined_objective": candidate["layers"][layer]["combined_objective"],
                       "baseline_block_mode_range_deg": base_range, "minimum_block_mode_range_deg": minimum_range,
                       "range_reduction_fraction": (base_range - minimum_range) / max(base_range, 1e-12)}
    same_best = best["T4"]["best_global_offset_s"] == best["RAW"]["best_global_offset_s"]
    dominates = (same_best and all(row["range_reduction_fraction"] >= TIMING_DOMINANCE_MODE_RANGE_REDUCTION_MIN for row in best.values()) and
                 best["T4"]["best_global_offset_s"] not in (-0.020, 0.020))
    result = {"schema": "biospur.root_r5a.time_offset_profile.v1", "predeclared_bound_s": [-0.020, 0.020],
              "grid_s": TIME_OFFSET_GRID_S.tolist(), "rows": rows, "best": best,
              "support_change_reported": True, "single_global_offset_only": True,
              "free_per_block_offsets_authorized": False, "timing_error_dominates": bool(dominates)}
    return result, all_profiles


def motion_and_quality_metrics(data: C1Data, support: SupportDefinition) -> tuple[list[dict], list[dict]]:
    motion = []; quality = []
    for block, mask in enumerate(block_masks(support)):
        ids = np.flatnonzero(mask); speeds = []
        for tag in range(len(data.nodes)):
            rows = ids[data.node_index[ids] == tag]; rows = rows[np.argsort(data.measurement_s[rows])]
            if len(rows) > 1:
                dt = np.diff(data.measurement_s[rows]); dp = np.linalg.norm(np.diff(data.root_relative_n_m[rows], axis=0), axis=1)
                speeds.extend((dp[dt > 0] / dt[dt > 0]).tolist())
        xy = data.root_relative_n_m[ids, :2]; covariance = np.cov(xy.T); singular = np.linalg.svd(covariance, compute_uv=False)
        motion.append({"block": block, "start_s": float(support.block_edges_s[block]),
                       "stop_s": float(support.block_edges_s[block + 1]), "events": len(ids),
                       "median_fk_point_speed_mps": float(np.median(speeds)), "p95_fk_point_speed_mps": float(np.quantile(speeds, 0.95)),
                       "horizontal_excitation_trace_m2": float(np.trace(covariance)),
                       "horizontal_excitation_condition": float(singular[0] / max(singular[-1], 1e-12))})
        raw_valid = data.raw_valid[ids]; residual = np.abs(data.t4_residual_m[ids]); selected = residual[raw_valid & np.isfinite(residual)]
        quality_values = data.quality_percent[ids][raw_valid]
        quality.append({"block": block, "events": len(ids), "raw_valid_links": int(np.sum(raw_valid)),
                        "valid_link_fraction": float(np.mean(raw_valid)), "median_quality_percent": float(np.median(quality_values)),
                        "median_abs_canonical_residual_m": float(np.median(selected)),
                        "p95_abs_canonical_residual_m": float(np.quantile(selected, 0.95)),
                        "positive_tail_fraction_gt_0_25m": float(np.mean(data.t4_residual_m[ids][raw_valid] > 0.25))})
    return motion, quality


def loo_diagnostics(data: C1Data, support: SupportDefinition) -> tuple[dict, dict]:
    combined = support.matched_event_mask; tag_rows = []
    for tag, name in enumerate(data.nodes):
        mask = combined & (data.node_index != tag)
        t4 = circular_profile("T4", f"TAG_LOO_{name}", *t4_evaluator(data, mask))
        raw = circular_profile("RAW", f"TAG_LOO_{name}", *raw_evaluator(raw_groups(data, mask)))
        tag_rows.append({"omitted_tag": name, "t4_mode_deg": t4["global_mode_deg"],
                         "raw_mode_deg": raw["global_mode_deg"], "t4_interval_width_95_deg": t4["asymptotic_profile_interval_width_95_deg"],
                         "raw_interval_width_95_deg": raw["asymptotic_profile_interval_width_95_deg"]})
    anchor_rows = []
    groups = raw_groups(data, combined)
    for anchor in range(8):
        profile = circular_profile("RAW", f"ANCHOR_LOO_{anchor}", *raw_evaluator(groups, omit_anchor=anchor))
        anchor_rows.append({"omitted_anchor": anchor, "raw_mode_deg": profile["global_mode_deg"],
                            "raw_interval_width_95_deg": profile["asymptotic_profile_interval_width_95_deg"]})
    return ({"schema": "biospur.root_r5a.tag_loo.v1", "rows": tag_rows},
            {"schema": "biospur.root_r5a.anchor_loo.v1", "rows": anchor_rows,
             "t4_anchor_loo": "NOT_RECOMPUTED_T4_FULL_FUNCTIONAL_DEPENDENCY_NOT_PROVEN"})


def block_resampling(t4_profiles: list[dict], raw_profiles: list[dict]) -> dict:
    rng = np.random.default_rng(20260824); rows = []
    for iteration in range(200):
        selected = rng.integers(0, 5, 5); row = {"iteration": iteration, "selected_blocks": selected.tolist()}
        for layer, profiles in (("T4", t4_profiles), ("RAW", raw_profiles)):
            values = np.sum([np.asarray(profiles[int(index)]["normalized_delta_objective"]) for index in selected], axis=0)
            row[f"{layer.lower()}_mode_deg"] = float(np.arange(-180.0, 180.0)[int(np.argmin(values))])
        rows.append(row)
    return {"schema": "biospur.root_r5a.block_resampling.v1", "iterations": len(rows), "rows": rows,
            "summary": {layer: {"median_deg": float(np.median([row[f"{layer}_mode_deg"] for row in rows])),
                                  "p05_deg": float(np.quantile([row[f"{layer}_mode_deg"] for row in rows], 0.05)),
                                  "p95_deg": float(np.quantile([row[f"{layer}_mode_deg"] for row in rows], 0.95))}
                        for layer in ("t4", "raw")}}


def counterfactuals(t4_profiles: list[dict], raw_profiles: list[dict], times_s: np.ndarray) -> dict:
    layers = {}; shuffle = {}
    for layer, profiles in (("T4", t4_profiles), ("RAW", raw_profiles)):
        combined = profiles[-1]; mode = math.radians(combined["global_mode_deg"]); base = profile_value(combined, mode)
        candidates = {"inverse": -mode, "plus_90": mode + math.pi / 2, "minus_90": mode - math.pi / 2, "plus_180": mode + math.pi}
        layers[layer] = {name: {"yaw_deg": wrap_degrees(float(np.degrees(value))),
                                "objective_ratio": profile_value(combined, value) / max(base, 1e-12)}
                         for name, value in candidates.items()}
        rng = np.random.default_rng(5100 if layer == "T4" else 5200); trials = []
        for iteration in range(10):
            order = rng.permutation(5); shuffled = [profiles[int(index)] for index in order]
            result = block_cross_validation(f"{layer}_SHUFFLED", shuffled, times_s)
            trials.append({"order": order.tolist(), "dynamic_improvement": result["dynamic_held_block_improvement"],
                           "mean_ratio": result["reference_prior"]["mean_ratio"]})
        shuffle[layer] = trials
    return {"schema": "biospur.root_r5a.counterfactuals.v1", "layers": layers,
            "block_order_shuffle": shuffle, "free_per_block_yaw": {"classification": "OVERFIT_CEILING_NOT_AUTHORIZED",
                "training_profile_excess_objective": 0.0},
            "reflection": "NEGATIVE_CONTROL_ONLY_REJECTED_BY_PROPER_ROTATION_CONTRACT",
            "free_scale": "NEGATIVE_CONTROL_ONLY_NOT_AUTHORIZED",
            "raw_t4_duplicate_factor_mutation": "MUST_FAIL_EVENT_OWNERSHIP_LEDGER"}


def bounded_nuisance(data: C1Data, support: SupportDefinition, timing: dict, tag_loo: dict, anchor_loo: dict,
                     root_r4: Path) -> dict:
    x, y, _, _ = centered_t4(data, support.matched_event_mask)
    def rx(value):
        c, s = math.cos(value), math.sin(value); return np.asarray([[1, 0, 0], [0, c, -s], [0, s, c]])
    def ry(value):
        c, s = math.cos(value), math.sin(value); return np.asarray([[c, 0, s], [0, 1, 0], [-s, 0, c]])
    tilt = []
    for roll_deg in (-2.0, 0.0, 2.0):
        for pitch_deg in (-2.0, 0.0, 2.0):
            tilt_rotation = ry(math.radians(pitch_deg)) @ rx(math.radians(roll_deg))
            def objective(yaw):
                c, s = math.cos(yaw), math.sin(yaw); rz = np.asarray([[c, -s, 0], [s, c, 0], [0, 0, 1]])
                residual = np.linalg.norm(y - x @ (rz @ tilt_rotation).T, axis=1)
                absolute = np.abs(residual); delta = 0.25
                return float(np.mean(np.where(absolute <= delta, 0.5 * absolute**2, delta * (absolute - 0.5 * delta))))
            grid = np.deg2rad(np.arange(-180.0, 180.0)); values = np.asarray([objective(v) for v in grid]); index = int(np.argmin(values))
            tilt.append({"roll_deg": roll_deg, "pitch_deg": pitch_deg,
                         "yaw_mode_deg": float(np.arange(-180.0, 180.0)[index]), "objective": float(values[index])})
    link = json.loads((root_r4 / "PER_LINK_HEALTH_AUDIT.json").read_text())
    shadow = json.loads((root_r4 / "BODY_SHADOW_GEOMETRY_DIAGNOSTICS.json").read_text())
    return {"schema": "biospur.root_r5a.bounded_nuisance.v1", "small_proper_tilt": tilt,
            "tilt_bound_status": "DIAGNOSTIC_PLUS_MINUS_2_DEG_NOT_MEASURED_AUTHORIZATION",
            "lever_arm_sensitivity": "NOT_EVALUATED_NO_MEASURED_UNCERTAINTY_BOUND",
            "node_specific_structure": tag_loo, "anchor_specific_structure": anchor_loo,
            "link_health_classification_counts": link["classification_counts"],
            "body_shadow": shadow, "timing": timing["best"], "arbitrary_per_link_bias_states_added": False}
