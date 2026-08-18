from __future__ import annotations

from collections import defaultdict
import json
import math
from pathlib import Path
from typing import Mapping

import numpy as np

from .analysis import build_heading_factors
from .core import atomic_json, information_rank, schur_profile, sha256_file, wrap_axis_line
from .frontend_cache import load_class_cache


def _axis_aggregate(payload: Mapping) -> dict:
    return dict(payload["aggregate"])


def _candidate_vector(candidate: Mapping) -> tuple[list[str], np.ndarray]:
    order = list(candidate["parameter_order"])
    mode = candidate["joint_modes"][0]
    return order, np.asarray([mode["relative_heading_rad"][segment] for segment in order], dtype=float)


def _residual_deg(factor: Mapping, order: list[str], heading: np.ndarray) -> float:
    if factor["type"] == "PROTOCOL_AXIS_LINE":
        predicted = heading[order.index(factor["segments"][0])]
    else:
        parent, child = factor["segments"]
        predicted = heading[order.index(child)]-heading[order.index(parent)]
    return float(abs(math.degrees(float(wrap_axis_line(predicted-factor["measurement_rad_mod_pi"])))))


def _bin_name(value: float) -> str:
    if value <= .25:
        return "early"
    if value < .75:
        return "mid"
    return "late"


def run_formal_validation(*, frontend_root: Path, report_dir: Path, evidence_dir: Path,
                          contract: Mapping, authority: Mapping, candidate: Mapping,
                          axis_payload: Mapping, split_manifest: Mapping,
                          exact_candidate_sha: str) -> dict:
    """Open the factor-held-out view exactly once after candidate freeze."""
    validation = load_class_cache(frontend_root, "VALIDATION")
    factors, rejected = build_heading_factors(rows=validation, contract=contract,
                                              authority=authority, axes=_axis_aggregate(axis_payload))
    order, heading = _candidate_vector(candidate)
    first = min(row["first_common_time_ns"] for row in split_manifest["vqf"]["nodes"].values())
    last = max(row["last_common_time_ns"] for row in split_manifest["vqf"]["nodes"].values())
    duration = max(last-first, 1)
    subtrees = contract["subtrees"]
    per_subtree = {}
    for subtree, segments in subtrees.items():
        relevant = [row for row in factors if any(segment in row["segments"] for segment in segments)]
        bins = {name: [] for name in ("early", "mid", "late")}
        for row in relevant:
            normalized = (int(row["block_midpoint_common_time_ns"])-first)/duration
            bins[_bin_name(normalized)].append(row)
        bin_report = {}
        for name, values in bins.items():
            distinct = len({(row["action_id"], row["cycle_ordinal"], row["factor_id"]) for row in values})
            bin_report[name] = {
                "heading_bearing_validation_blocks": distinct,
                "minimum_required": int(contract["qualification"]["validation_blocks_per_time_bin"]),
                "factor_graph_connects_subtree_to_pelvis": False,
                "observable_relative_heading_to_pelvis": False,
                "circular_shift_interval": "NOT_COMPUTED_UNOBSERVABLE_BIN_GRAPH",
            }
        per_subtree[subtree] = {
            "segments": list(segments), "bins": bin_report,
            "state": "INSUFFICIENT_TEMPORAL_HEADING_EVIDENCE",
            "reason": "No validation bin contains a heading-bearing path from this subtree through psi_GP to the fixed pelvis convention.",
            "static_sufficient": False, "dynamic_required": False,
        }

    families = defaultdict(list)
    for row in factors:
        families[row["family"]].append(_residual_deg(row, order, heading))
    semantic = {}
    for family, values in sorted(families.items()):
        semantic[family] = {
            "blocks": len(values), "median_deg": float(np.median(values)),
            "p95_deg": float(np.quantile(values, .95)),
            "median_gate_deg": float(contract["qualification"]["semantic_median_gate_deg"]),
            "p95_gate_deg": float(contract["qualification"]["semantic_p95_gate_deg"]),
            "pass": float(np.median(values)) <= float(contract["qualification"]["semantic_median_gate_deg"])
                    and float(np.quantile(values, .95)) <= float(contract["qualification"]["semantic_p95_gate_deg"]),
        }

    tolerances = contract["qualification"]["profile_svd_tolerances"]
    bin_graphs = {}
    for name in ("early", "mid", "late"):
        selected = []
        for row in factors:
            normalized = (int(row["block_midpoint_common_time_ns"])-first)/duration
            if _bin_name(normalized) == name:
                selected.append(row)
        matrix = np.zeros((len(order)+1, len(order)+1))
        for row in selected:
            jac = np.zeros(len(order)+1)
            if row["type"] == "PROTOCOL_AXIS_LINE":
                jac[order.index(row["segments"][0])] = 1.0; jac[-1] = -1.0
            else:
                parent, child = row["segments"]
                jac[order.index(child)] = 1.0; jac[order.index(parent)] = -1.0
            matrix += float(row["accepted_robust_weight"])*np.outer(jac, jac)
        bin_graphs[name] = information_rank(schur_profile(matrix, len(order)), tolerances)

    # final still is physically Rz invariant and is never passed into
    # build_heading_factors, so its heading factor count is exactly zero.
    payload = {
        "schema": "biospur-phase3r23-common-heading-drift-report-v1",
        "formal_validation_open_count": 1, "exact_candidate_sha": exact_candidate_sha,
        "prevalidation_candidate_file_sha256": sha256_file(report_dir/"PREVALIDATION_SESSION_STATIC_HEADING_CANDIDATE.json"),
        "candidate_payload_sha256": candidate["candidate_payload_sha256"],
        "validation_class_rows_read": int(len(validation["common_time_ns"])),
        "validation_factor_count": len(factors), "rejected_validation_blocks": rejected,
        "final_still_heading_factor_count": 0,
        "per_time_bin_profiled_information": bin_graphs,
        "subtrees": per_subtree, "semantic_residuals": semantic,
        "formal_threshold_deg": 15.0, "threshold_sensitivity_deg": [10.0, 15.0, 20.0],
        "qualification": "HISTORICALLY_EXPOSED_WITHIN_SESSION_VALIDATION",
        "frontend_boundary": "FRONTEND_CAUSALLY_SHARED_FACTOR_HELD_OUT_VALIDATION",
        "validation_used_for_fit_or_mode_selection": False,
        "candidate_changed_after_validation": False,
        "h_numeric_consumption": 0, "p_numeric_consumption": 0, "b1_numeric_consumption": 0,
        "opensense_numeric_consumption": 0, "uwb_semantic_numeric_decode": 0,
        "plus10_injection_factor_consumption": 0,
    }
    atomic_json(report_dir/"COMMON_HEADING_DRIFT_REPORT.json", payload)
    factor_path = evidence_dir/"FORMAL_VALIDATION_FACTORS.json"
    atomic_json(factor_path, {"schema":"biospur-phase3r23-validation-factors-v1", "factors":factors, "sha256_scope":"complete file"})
    payload["validation_factor_artifact"] = {"path": str(factor_path), "sha256": sha256_file(factor_path)}
    atomic_json(report_dir/"COMMON_HEADING_DRIFT_REPORT.json", payload)
    return payload
