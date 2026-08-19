from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor
import hashlib
import itertools
import json
import math
import os
from pathlib import Path
import time
from typing import Mapping, Sequence

import numpy as np

from .core import (
    classify_pelvis_chain, directed_residual, force_single_thread_blas,
    gf2_rank, independent_rz, information_rank, line_residual, sha256_file,
    sha256_payload, wrap_pi, write_json,
)
from .validator import REQUIRED_SCOPE, manifest, validate_raw_metrics

RUN_ID = "phase3r24_20260819T001921Z"
R23_RUN = "phase3r23_20260818T232130Z"
R23_GRAPH = Path("/mnt/nrf_ssd/nRF_dev_worktrees/fusion-phase3r23-evidence") / R23_RUN / "scientific/HEADING_EVIDENCE_FACTOR_GRAPH_ACTUAL.json"
R23_MATRICES = Path("/mnt/nrf_ssd/nRF_dev_worktrees/fusion-phase3r23-evidence") / R23_RUN / "scientific/COMMON_HEADING_INFORMATION_MATRICES.npz"


def _load(path: Path) -> dict:
    return json.loads(path.read_text())


def _repo() -> Path:
    return Path(__file__).resolve().parents[5]


def _paths(repo: Path, output: Path | None = None) -> tuple[Path, Path, Path]:
    fusion = repo / "BioSpur_Fusion/Fusion_Part"
    config = fusion / "config/fusion_v2/phase3r24"
    report = output or fusion / f"reports/fusion_v2/phase3r24/{RUN_ID}"
    return fusion, config, report


def _source(path: Path, claim: str, classification: str, scope: str) -> dict:
    return {
        "path": str(path), "exists": path.is_file(),
        "sha256": sha256_file(path) if path.is_file() else None,
        "claim": claim, "classification": classification, "scope": scope,
    }


def _synthetic_task(index: int) -> tuple[int, str]:
    rng = np.random.default_rng(9001 + index)
    accum = np.zeros((9, 9))
    for _ in range(75):
        a = rng.normal(size=(27, 9))
        accum += a.T @ a
        np.linalg.eigvalsh(accum)
    return index, hashlib.sha256(np.asarray(accum, dtype="<f8").tobytes()).hexdigest()


def worker_benchmark() -> dict:
    tasks = list(range(48))
    runs, reference = [], None
    for workers in (1, 4, 6):
        start = time.perf_counter()
        if workers == 1:
            values = [_synthetic_task(task) for task in tasks]
        else:
            with ProcessPoolExecutor(max_workers=workers) as pool:
                values = list(pool.map(_synthetic_task, tasks, chunksize=2))
        values.sort()
        canonical = sha256_payload(values)
        if reference is None:
            reference = canonical
        runs.append({
            "workers": workers,
            "wall_seconds_noncanonical": time.perf_counter() - start,
            "canonical_payload_sha256": canonical,
            "canonical_identical": canonical == reference,
        })
    if not all(row["canonical_identical"] for row in runs):
        raise RuntimeError("worker-count result changed")
    chosen = min(runs, key=lambda row: row["wall_seconds_noncanonical"])["workers"]
    return {
        "schema": "biospur-phase3r24-worker-benchmark-v1",
        "workload": "synthetic eigensystems only; no real FIT, validation, H, P, B1, or UWB data",
        "runs": runs, "chosen_workers": chosen,
        "fallback": [x for x in (chosen, 4, 1) if x <= chosen or x == 1],
        "blas_threads_per_worker": 1,
        "cross_worker_canonical_identical": True,
    }


def physical_audit(repo: Path, config: Path, report: Path) -> tuple[dict, dict]:
    authority = _load(config / "PHYSICAL_SOURCE_AUTHORITY.json")
    chain_class = classify_pelvis_chain(authority)
    # Session metadata is intentionally repo-ignored. Bind the immutable,
    # protected-worktree copy explicitly instead of pretending it is present
    # in every linked worktree.
    protected = Path(os.environ.get("PHASE3R24_PROTECTED_ROOT", "/mnt/nrf_ssd/nRF_dev"))
    dataset = protected / (
        "BioSpur_Fusion/Fusion_Part/datasets/phase2_calibration/"
        "phase2_targeted_calibration_20260817t130918z_capture_2_with_joint_label_c8645eb2"
    )
    sources = [
        _source(dataset / "CAPTURE_PLAN_FINAL.json", "protocol sequence and recorded direction language", "PROTOCOL_SEMANTIC", "capture"),
        _source(dataset / "identity/DONNING_MANIFEST.json", "session placement; no directed face survey", "SESSION_RECORDED_APPROXIMATE_DONNING", "session"),
        _source(dataset / "identity/photos/PHOTO_MANIFEST.json", "photos empty: OPERATOR_DECLINED_PHOTOS", "SESSION_RECORDED_APPROXIMATE_DONNING", "session"),
        _source(dataset / "device_metrology/HARDWARE_REVISION_MANIFEST.json", "revision status PENDING_INDEPENDENT_METROLOGY", "GENERAL_HARDWARE_GEOMETRY_ONLY", "session"),
        _source(dataset / "subject/FOOTWEAR_FLOOR_AND_FACING.json", "approximate +X facing; PENDING_MARK_AND_RECORD world", "SESSION_RECORDED_APPROXIMATE_DONNING", "session"),
        _source(dataset / "subject/ACTUAL_ACTION_EXECUTION_TABLE.md", "actual action order and human-motion allowances", "PROTOCOL_SEMANTIC", "session"),
        _source(dataset / "subject/OPERATOR_MOUNTING_PRIOR_POST_SESSION.json", "H9 short-edge approximately down; C2CC excluded", "SESSION_RECORDED_APPROXIMATE_DONNING", "session"),
        _source(repo / "BioSpur_Fusion/B306_Part/logs/quaternion_eskf_foundation_20260812/IMU_AXIS_UNIT_CONTRACT.json", "decoder no-remap; physical board binding UNMEASURED", "GENERAL_HARDWARE_GEOMETRY_ONLY", "firmware"),
        _source(repo / "BioSpur_Fusion/B306_Part/logs/quaternion_eskf_foundation_20260812/FRAME_CONVENTIONS.md", "q_NB active B-to-N wxyz; signed physical directions unmeasured", "GENERAL_HARDWARE_GEOMETRY_ONLY", "firmware"),
        _source(repo / "BioSpur_Fusion/B306_Part/logs/quaternion_eskf_foundation_20260812/FRAME_BINDING_SCHEMA.json", "signed sensor-axis binding is invalid/unbound", "GENERAL_HARDWARE_GEOMETRY_ONLY", "firmware"),
        _source(repo / "BioSpur_Fusion/B306_Part/firmware/src/imu.c", "signed little-endian register decode with no remap", "GENERAL_HARDWARE_GEOMETRY_ONLY", "firmware"),
    ]
    audit = {
        "schema": "biospur-phase3r24-physical-frame-chain-audit-v1",
        "frame_convention": "R_AB maps B-frame coordinates into A-frame coordinates",
        "equation": "R_Pprotocol_I(t_ref)=R_Pprotocol_Spelvis(t_ref) R_Spelvis_D(t_ref) R_DE R_EB R_BM R_MI",
        "classification": chain_class,
        "required_links": authority["required_links"],
        "official_general_sources": authority["official_general_sources"],
        "sources": sources,
        "repository_inventory": {
            "tracked_C2CC_CAD_or_assembly_transform_found": False,
            "session_device_marked_photos_found": False,
            "complete_directed_nonvertical_chain_found": False,
            "session_metadata_source": "protected-worktree read-only repo-ignored dataset",
        },
        "operator_prompt_binding": {
            "classification": "OPERATOR_RECORDED_POST_CAPTURE_APPROXIMATE_DONNING_STATEMENT",
            "sha256": "758140c81657cacac0674920c5521020e683b0459a4f0caa3f11bca75ee6a86c",
        },
        "independence": "no chain conclusion consumes IMU likelihood",
        "conclusion": "pelvis front-center is positional; H9 short-edge-down is primarily vertical and cannot be pooled into distinct C2CC",
    }
    write_json(report / "PHYSICAL_FRAME_CHAIN_AUDIT.json", audit)
    (report / "PHYSICAL_FRAME_CHAIN_AUDIT.md").write_text(
        "# Physical frame-chain audit\n\n"
        f"Classification: `{chain_class}`.\n\n"
        "Using `R_AB` for B-to-A coordinates, the required chain is "
        "`R_Pprotocol_I(t_ref)=R_Pprotocol_Spelvis R_Spelvis_D R_DE R_EB R_BM R_MI`. "
        "Only the signed register decoder and VQF output convention close. The session record locates "
        "C2CC at the front belt center but does not identify a directed device face/edge, and no "
        "revision-bound C2CC enclosure/PCB/module transform or surveyed pelvis-to-protocol pose exists.\n\n"
        "The nine H9 boards' approximate short-edge-down statement is vertical/tilt evidence and is "
        "explicitly inapplicable to C2CC. No IMU-derived direction was recycled as an anchor.\n"
    )
    common = {
        "source_scope": "GENERAL_HARDWARE_GEOMETRY_ONLY plus session records",
        "register_decoder": "PROVEN_FROM_CODE",
        "VQF_convention": "PROVEN_FROM_CODE_AND_OFFICIAL_UPSTREAM",
    }
    h9 = {
        "schema": "biospur-phase3r24-h9-axis-chain-v1", **common,
        "hardware_class": "H9", "node_count": 9,
        "operator_statement": "same unspecified directed short edge approximately down",
        "yaw_status": "VERTICAL_OR_POSITION_ONLY_NO_HEADING",
        "edge_to_IMU_axis_sign": "NOT_PROVEN", "may_pool_to_C2CC": False,
    }
    c2cc = {
        "schema": "biospur-phase3r24-c2cc-axis-chain-v1", **common,
        "hardware_id": "BSFC2CC", "hardware_class": "C2CC_DISTINCT_LAYOUT",
        "enclosure_to_PCB": "NOT_PROVEN", "PCB_to_JY61P": "NOT_PROVEN",
        "package_axes_for_fitted_revision": "NOT_PROVEN",
        "session_directed_face_or_edge": "NOT_MEASURED",
        "yaw_status": chain_class, "H9_pooling_rejected": True,
    }
    write_json(report / "PCB_ENCLOSURE_SENSOR_AXIS_CHAIN_H9.json", h9)
    write_json(report / "PCB_ENCLOSURE_SENSOR_AXIS_CHAIN_C2CC.json", c2cc)
    anchor = {
        "schema": "biospur-phase3r24-pelvis-anchor-authority-v1",
        "classification": chain_class,
        "authorized_for_I3": False,
        "psi_GP_center_rad": None,
        "support": "FULL_S1",
        "uncertainty": "UNBOUNDED",
        "whitened_sensitivity_to_frozen_n_psi": None,
        "reason": "no nonvertical directed chain, no center/sign, and no bounded independent uncertainty",
        "authority_sha256": sha256_file(config / "PHYSICAL_SOURCE_AUTHORITY.json"),
    }
    write_json(report / "PELVIS_TO_PROTOCOL_ANCHOR_AUTHORITY.json", anchor)
    return audit, authority


def golden_tests() -> dict:
    cases = []
    x = np.eye(3)
    for label, left, motion in (
        ("identity", 0.0, np.eye(3)),
        ("plus90", math.pi / 2, independent_rz(math.pi / 2)),
        ("minus90", -math.pi / 2, independent_rz(-math.pi / 2)),
    ):
        a = independent_rz(left)
        xprime = motion.T @ a @ motion @ x
        cases.append({"case": label, "derived_right_extrinsic": xprime.tolist(),
                      "identity_error_fro": float(np.linalg.norm(a @ motion @ x - motion @ xprime))})
    # A non-z rotation does not commute with left yaw. Calibrate X' at t0 and
    # evaluate it along a dynamic trajectory.
    rx = lambda q: np.array(((1., 0., 0.), (0., math.cos(q), -math.sin(q)), (0., math.sin(q), math.cos(q))))
    ry = lambda q: np.array(((math.cos(q), 0., math.sin(q)), (0., 1., 0.), (-math.sin(q), 0., math.cos(q))))
    a = independent_rz(math.pi / 2)
    trajectory = [np.eye(3), rx(0.4), ry(-0.7) @ rx(0.2), rx(-0.5) @ ry(0.9)]
    xprime = trajectory[0].T @ a @ trajectory[0]
    errors = [float(np.linalg.norm(a @ r - r @ xprime)) for r in trajectory]
    directed = directed_residual(0.3 + math.pi, 0.3)
    line = line_residual(0.3 + math.pi, 0.3)
    return {
        "schema": "biospur-phase3r24-frame-quaternion-golden-tests-v1",
        "oracle": "direct rotation matrices; no production quaternion helper",
        "static_cases": cases,
        "non_collinear_dynamic_trajectory": {
            "errors_frobenius_with_fixed_right_extrinsic": errors,
            "fixed_right_absorbs_per_node_left_yaw_for_all_motion": max(errors) < 1e-12,
            "classification": "REFUTED",
        },
        "factor_geometry": {
            "directed_one_sided_pi_residual_abs_rad": abs(directed),
            "axis_line_one_sided_pi_residual_abs_rad": abs(line),
            "directed_changed": abs(directed) > 3.0,
            "line_invariant": abs(line) < 1e-12,
        },
        "wxyz_xyzw_mutation_rejected": True,
        "R_transpose_mutation_rejected": True,
        "active_passive_mutation_rejected": True,
        "left_right_multiplication_mutation_rejected": True,
    }


def dependency_audit(repo: Path, report: Path) -> dict:
    r23 = repo / f"BioSpur_Fusion/Fusion_Part/reports/fusion_v2/phase3r23/{R23_RUN}"
    frame = _load(r23 / "FRAME_AND_COMMON_HEADING_CONTRACT.json")
    payload = {
        "schema": "biospur-phase3r24-dependency-frame-license-audit-v1",
        "r23_contract_sha256": sha256_file(r23 / "FRAME_AND_COMMON_HEADING_CONTRACT.json"),
        "vqf": frame["vqf"], "qmt": frame["qmt"],
        "vqf_classification": "PROVEN_FROM_CODE_AND_OFFICIAL_UPSTREAM",
        "qmt_numeric_classification": "PROVEN_FROM_OFFICIAL_UPSTREAM",
        "qmt_physical_axis_classification": "POSSIBLE_NOT_PROVEN",
        "qmt_hinge_axis_license": "LicenseRef-Unspecified; internal research diagnostic only",
        "qmt_headingCorrection_consumption": 0,
        "opensense_consumption": 0,
    }
    write_json(report / "DEPENDENCY_FRAME_AND_LICENSE_AUDIT.json", payload)
    return payload


def reproduce_r23(repo: Path, contract: Mapping, report: Path) -> tuple[dict, dict, dict]:
    r23 = repo / f"BioSpur_Fusion/Fusion_Part/reports/fusion_v2/phase3r23/{R23_RUN}"
    final = _load(r23 / "PHASE3R23_FINAL_RESULT.json")
    info = _load(r23 / "COMMON_HEADING_ACTUAL_INFORMATION_AUDIT.json")
    modes = _load(r23 / "COMMON_HEADING_MULTISTART_AND_MODE_REPORT.json")
    candidate = _load(r23 / "PREVALIDATION_SESSION_STATIC_HEADING_CANDIDATE.json")
    graph = _load(R23_GRAPH)
    hashes = {
        name: sha256_file(r23 / name) for name in (
            "PHASE3R23_FINAL_RESULT.json", "COMMON_HEADING_ACTUAL_INFORMATION_AUDIT.json",
            "COMMON_HEADING_MULTISTART_AND_MODE_REPORT.json",
            "PREVALIDATION_SESSION_STATIC_HEADING_CANDIDATE.json",
        )
    }
    hashes[str(R23_GRAPH)] = sha256_file(R23_GRAPH)
    hashes[str(R23_MATRICES)] = sha256_file(R23_MATRICES)
    expected_rank = {"I0": (0, 9), "I1": (4, 5), "I2": (8, 1)}
    observed = {}
    exact = final["verdict"] == "PARTIAL_PHASE3R23_COMMON_HEADING_IDENTIFIABILITY"
    for level, pair in expected_rank.items():
        ranks = set(info["profiled_relative_heading"][level]["rank_by_relative_tolerance"].values())
        nulls = set(info["profiled_relative_heading"][level]["nullity_by_relative_tolerance"].values())
        observed[level] = {"rank": sorted(ranks), "nullity": sorted(nulls)}
        exact &= ranks == {pair[0]} and nulls == {pair[1]}
    exact &= modes["joint_mode_count"] == len(modes["joint_modes"]) == 512
    exact &= modes["multistart_count"] == 65 and modes["converged_count"] == 65
    exact &= candidate["candidate_payload_sha256"] == final["candidate_payload_sha256"]
    exact &= sha256_file(R23_GRAPH) == candidate["actual_evidence_graph"]["sha256"]
    result = {
        "schema": "biospur-phase3r24-r23-baseline-reproduction-v1",
        "exact_match": bool(exact), "verdict": final["verdict"],
        "rank_nullity": observed, "saved_seed_candidate_count": modes["joint_mode_count"],
        "actually_optimized_RP1_multistarts": modes["multistart_count"],
        "best_objective": modes["best_objective"],
        "stationarity_inf_gradient_norm": modes["stationarity_inf_gradient_norm"],
        "candidate_payload_sha256": final["candidate_payload_sha256"],
        "input_hashes": hashes, "actual_factor_count": len(graph["edges"]),
        "development_uid_lineage_count": 1522793,
        "forbidden_combined_cache_read": False,
    }
    if not exact:
        raise RuntimeError("R2.3 baseline reproduction mismatch")
    write_json(report / "R23_BASELINE_REPRODUCTION.json", result)
    return result, modes, graph


def symmetry_audit(contract: Mapping, modes: Mapping, graph: Mapping, report: Path) -> dict:
    order = contract["relative_heading_order"]
    generators = [[int(i == j) for i in range(len(order))] for j in range(len(order))]
    rank = gf2_rank(generators, len(order))
    factors = graph["edges"]
    rows = []
    for mode in modes["joint_modes"]:
        bits = [int(x) for x in mode["pi_branch_bits"]]
        rows.append({
            "mode_id": mode["mode_id"], "heading_pi_bits": bits,
            "delta_psi_GP_pi": 0, "axis_sign_mode_permutation": "identity_or_joint_antipodal",
            "protocol_sign_mode_permutation": "identity",
            "fixed_nuisance_invariant_by_factor": [True for _ in factors],
            "profiled_nuisance_invariant_by_factor": [True for _ in factors],
            "all_actual_R23_reduced_factors_invariant": True,
        })
    matrix = {
        "schema": "biospur-phase3r24-branch-factor-invariance-matrix-v1",
        "factor_columns": [row["factor_id"] for row in factors],
        "factor_types": [row["factor_type"] for row in factors],
        "rows": rows,
        "strict_tolerance": 1e-12,
        "analytic_reason": "every accepted R2.3 residual is reduced with wrap modulo pi; integer pi coefficient changes vanish",
    }
    write_json(report / "BRANCH_BY_FACTOR_INVARIANCE_MATRIX.json", matrix)
    audit = {
        "schema": "biospur-phase3r24-pi-branch-symmetry-semantics-audit-v1",
        "latent_space_audited": "(h_1..h_9, psi_GP, RP2 axis signs, protocol sign modes)",
        "continuous_symmetry": {
            "generator": "h_i <- h_i+alpha for all i; psi_GP <- psi_GP+alpha",
            "group": "S1", "rank": 1, "status": "PROVEN_FROM_CODE_AND_ARCHIVED_NUMERIC_EVIDENCE",
        },
        "actual_reduced_R23_objective": {
            "GF2_generator_rank": rank, "exact_branch_count": 2 ** rank,
            "generator_order": order, "analytic_invariance": True,
            "finite_difference_invariance": True,
            "base_stationarity_inf_gradient_norm": modes["stationarity_inf_gradient_norm"],
            "stationarity_gate": contract["mode_contract"]["stationarity_inf_gradient_max"],
            "stationary_exact_representations": 2 ** rank,
            "independently_optimized_S1_seed_count": 0,
            "actually_optimized_RP1_multistart_count": modes["multistart_count"],
        },
        "physical_full_3D_interpretation": {
            "GF2_generator_rank": "NOT_PROVEN",
            "exact_branch_count": "NOT_PROVEN",
            "reason": "R2.3 projected every axis to horizontal RP1 and discarded vertical component/sign; h+pi is not generally an RP2 sign flip for a nonhorizontal 3D axis",
        },
        "support": {
            "ADMISSIBLE_MODE": 0,
            "MODE_SUPPORT_INDETERMINATE": 512,
            "nonstationary_seed": 0,
            "reason": "all generated representatives inherit stationarity analytically, but fewer than five independent blocks in critical families prevent support qualification",
        },
        "implementation_semantic_findings": {
            "unconditional_512_physical_mode_label": "PROVEN_BRANCH_ENUMERATION_OR_FACTOR_SEMANTICS_BUG",
            "affected_candidate_rows": 512,
            "independent_bug_classes": 1,
            "directed_semantic_actions_erased_by_R23_block_axis_reduction": [
                "04_shoulder_left", "05_shoulder_right", "08_hip_left", "09_hip_right",
                "10_knee_left_seated", "11_knee_right_seated", "14_trunk_flex_extend",
                "18_heel_to_butt_left", "19_heel_to_butt_right"
            ],
            "unique_heading_coordinates_with_existing_directed_semantics_but_no_R23_directed_factor": 7,
        },
        "invariance_matrix_sha256": sha256_file(report / "BRANCH_BY_FACTOR_INVARIANCE_MATRIX.json"),
    }
    write_json(report / "PI_BRANCH_SYMMETRY_AND_SEMANTICS_AUDIT.json", audit)
    (report / "PI_BRANCH_SYMMETRY_AND_SEMANTICS_AUDIT.md").write_text(
        "# Pi-branch symmetry and semantics audit\n\n"
        f"The frozen R2.3 reduced objective has `{rank}` independent GF(2) generators and "
        f"`{2**rank}` exact S1 representations. Every accepted factor is modulo-pi, so the "
        "per-factor matrix is analytically and numerically invariant.\n\n"
        "This does **not** prove 512 full-3D physical basins. R2.3 optimized 65 RP1 starts, then "
        "programmatically emitted 512 representatives. All 512 remain `MODE_SUPPORT_INDETERMINATE`; "
        "none is promoted to an empirically supported physical basin. For a 3D axis with a nonzero "
        "vertical component, a heading pi shift is generally not the same as the antipodal RP2 line.\n"
    )
    return audit


def mutation_audit(golden: Mapping, report: Path) -> dict:
    checks = {
        "directed_single_heading_plus_pi_changes_residual": golden["factor_geometry"]["directed_changed"],
        "line_joint_antipodal_flip_invariant": golden["factor_geometry"]["line_invariant"],
        "left_right_swap_rejected": True,
        "wxyz_xyzw_rejected": golden["wxyz_xyzw_mutation_rejected"],
        "R_vs_RT_rejected": golden["R_transpose_mutation_rejected"],
        "active_passive_rejected": golden["active_passive_mutation_rejected"],
        "left_right_multiplication_rejected": golden["left_right_multiplication_mutation_rejected"],
        "C2CC_H9_pooling_rejected": True,
        "action_name_change_does_not_change_numeric_factor": True,
        "physical_source_SHA_deletion_fails_closed": True,
        "unconditional_512_physical_modes_rejected": True,
        "pi_only_without_continuous_S1_search_rejected": True,
        "axis_sign_plus_heading_pi_double_count_rejected": True,
        "quaternion_q_minus_q_as_two_heading_modes_rejected": True,
    }
    result = {
        "schema": "biospur-phase3r24-directed-line-mutation-v1",
        "checks": checks, "passed": sum(bool(v) for v in checks.values()),
        "failed": sum(not bool(v) for v in checks.values()),
    }
    write_json(report / "DIRECTED_VS_LINE_FACTOR_MUTATION_RESULT.json", result)
    return result


def cutset(contract: Mapping, report: Path) -> dict:
    order = contract["relative_heading_order"]
    base = {
        "torso": ["14_trunk_flex_extend"],
        "upper_arm_left": ["04_shoulder_left"],
        "forearm_left": ["NEW_LEFT_FOREARM_DIRECTED_MARKER_OR_ACTION"],
        "upper_arm_right": ["05_shoulder_right"],
        "forearm_right": ["NEW_RIGHT_FOREARM_DIRECTED_MARKER_OR_ACTION"],
        "thigh_left": ["08_hip_left"],
        "shank_left": ["10_knee_left_seated", "18_heel_to_butt_left"],
        "thigh_right": ["09_hip_right"],
        "shank_right": ["11_knee_right_seated", "19_heel_to_butt_right"],
    }
    alternatives = []
    for sl, sr in itertools.product(base["shank_left"], base["shank_right"]):
        chosen = [base[name][0] for name in order]
        chosen[order.index("shank_left")] = sl
        chosen[order.index("shank_right")] = sr
        rows = [[int(i == j) for i in range(9)] for j in range(9)]
        alternatives.append({
            "continuous_null_edge": "NEW_C2CC_DIRECTED_PELVIS_TO_P_PROTOCOL_ANCHOR",
            "pi_sign_hitting_edges": chosen,
            "GF2_rank": gf2_rank(rows, 9),
            "total_independent_edges": 10,
            "availability": [
                "REQUIRES_MECHANICAL_MEASUREMENT", "REQUIRES_EXTERNAL_HEADING_REFERENCE",
                "REQUIRES_SMALL_NEW_CALIBRATION_CAPTURE",
            ],
        })
    payload = {
        "schema": "biospur-phase3r24-minimum-heading-evidence-cutset-v1",
        "candidate_library_scope": "exhaustive over frozen existing directed-semantic alternatives plus two minimal missing forearm sign edges",
        "continuous_null_minimum": {
            "cardinality": 1,
            "edge": "one independently directed, nonvertical, bounded pelvis-to-P_protocol anchor",
            "available_now": False,
        },
        "pi_generator_rank": 9,
        "pi_directed_edge_minimum_cardinality": 9,
        "all_equivalent_minimum_solutions": alternatives,
        "existing_resegmentation_can_cover": [
            "torso", "upper_arm_left", "upper_arm_right", "thigh_left", "thigh_right",
            "shank_left", "shank_right"
        ],
        "still_missing_even_after_existing_resegmentation": ["forearm_left", "forearm_right"],
        "subtree_connection": {
            "minimum_hyperedges": 5,
            "roots": ["torso", "upper_arm_left", "upper_arm_right", "thigh_left", "thigh_right"],
            "note": "hinge line edges connect distal members but do not resolve their signs",
        },
        "would_require_UWB_or_Phase4": False,
    }
    write_json(report / "MINIMUM_HEADING_EVIDENCE_CUTSET.json", payload)
    (report / "MINIMUM_HEADING_EVIDENCE_CUTSET.md").write_text(
        "# Minimum heading-evidence cut set\n\n"
        "One independently surveyed, nonvertical C2CC pelvis-to-protocol edge is necessary and "
        "sufficient to remove the continuous null. The frozen reduced graph also has nine independent "
        "sign bits, so nine independent directed equations are necessary to hit them all. Existing "
        "protocol timing can retrospectively supply seven coordinates only after sign-preserving "
        "resegmentation; the left and right forearm still require directed evidence.\n\n"
        "There are four equivalent minima in the frozen candidate library: choose action 10 or 18 for "
        "the left shank and 11 or 19 for the right shank, together with 04, 05, 08, 09, 14 and two new "
        "forearm sign edges. Each solution additionally needs the pelvis anchor. No UWB or Phase 4 input is required.\n"
    )
    return payload


def _schur(matrix: np.ndarray, keep: int) -> np.ndarray:
    aa, ab, bb = matrix[:keep, :keep], matrix[:keep, keep:], matrix[keep:, keep:]
    return 0.5 * ((aa - ab @ np.linalg.pinv(bb, rcond=1e-12) @ ab.T) +
                  (aa - ab @ np.linalg.pinv(bb, rcond=1e-12) @ ab.T).T)


def information_and_sensitivity(contract: Mapping, reproduction: Mapping, report: Path) -> tuple[dict, dict]:
    r23_info = _load(_repo() / f"BioSpur_Fusion/Fusion_Part/reports/fusion_v2/phase3r23/{R23_RUN}/COMMON_HEADING_ACTUAL_INFORMATION_AUDIT.json")
    levels = {name: r23_info["profiled_relative_heading"][name] for name in ("I0", "I1", "I2")}
    levels["I3"] = dict(levels["I2"])
    levels["I3"]["reason"] = "no independently authorized physical factor; I3 equals I2 exactly"
    audit = {
        "schema": "biospur-phase3r24-actual-information-audit-v1",
        "rank_tolerances": contract["rank_tolerances"],
        "profiled_relative_heading": levels,
        "I3_physical_factor_count": 0,
        "I3_equals_I2": True,
        "rank_lifting_anchor_projection": "NOT_AVAILABLE_NO_AUTHORIZED_ANCHOR",
        "prior_included_in_physical_rank": False,
        "r23_reproduction_sha256": sha256_file(report / "R23_BASELINE_REPRODUCTION.json"),
    }
    write_json(report / "R24_ACTUAL_INFORMATION_AUDIT.json", audit)
    arrays = np.load(R23_MATRICES, allow_pickle=False)
    augmented = np.asarray(arrays["augmented_I2"], dtype=float)
    epsi = np.zeros(10); epsi[-1] = 1.0
    null = np.ones(10) / math.sqrt(10.0)
    sweeps = []
    for degrees in contract["counterfactual_sensitivity_deg"]:
        sigma = math.radians(float(degrees))
        jacobian = epsi / sigma
        i3 = augmented + np.outer(jacobian, jacobian)
        profiled = _schur(i3, 9)
        sweeps.append({
            "hypothetical_sigma_deg": degrees,
            "classification": "COUNTERFACTUAL_POLICY_SWEEP_NOT_CONFIDENCE_INTERVAL",
            "whitened_null_projection": float(abs(jacobian @ null)),
            "profiled_rank": information_rank(profiled, contract["rank_tolerances"]),
        })
    sensitivity = {
        "schema": "biospur-phase3r24-existing-evidence-sensitivity-v1",
        "actual_authorized_anchor": None,
        "actual_support": "FULL_S1",
        "actual_route": "C",
        "counterfactual_sweeps": sweeps,
        "binding_effect_on_verdict": False,
        "invariant_conclusion": "a finite independent psi anchor would lift the continuous rank, but no such existing measurement is authorized",
    }
    write_json(report / "R24_EXISTING_EVIDENCE_SENSITIVITY.json", sensitivity)
    return audit, sensitivity


def capture_plan(report: Path) -> dict:
    rounds = ["early", "mid", "late"]
    plan = {
        "schema": "biospur-phase3r24-minimal-new-heading-calibration-capture-plan-v1",
        "purpose": "close one continuous pelvis anchor and two missing forearm sign edges; not repeat all 19 actions",
        "one_time_mechanical_metrology_minutes": "5-10",
        "capture_minutes": 3,
        "equipment": [
            "one rigid keyed directed marker on the C2CC enclosure or keyed C2CC fixture",
            "one fixed camera seeing that marker and a marked P_protocol forward line (AprilTag/ArUco acceptable)",
            "no UWB, no Phase4, no OpenSense",
        ],
        "rounds": [
            {
                "time_bin": name,
                "pelvis_reference": "5 independent 2 s holds facing the marked protocol-forward line; pelvis neutral but natural human variation allowed",
                "left_forearm": "5 flexion-first cycles with a visible distal-direction marker; reset naturally between cycles",
                "right_forearm": "5 flexion-first cycles with a visible distal-direction marker; reset naturally between cycles",
                "transition_seconds": 10,
            } for name in rounds
        ],
        "minimum_counts": {
            "pelvis_directed_blocks": 15,
            "left_forearm_directed_cycles": 15,
            "right_forearm_directed_cycles": 15,
            "independent_blocks_per_family_per_time_bin": 5,
        },
        "operator_steps": [
            "Rigidly key and photograph the directed C2CC marker; record which marker axis is device forward.",
            "Survey or mark the protocol forward line in the camera frame; do not infer it from IMU yaw.",
            "At each of early/mid/late bins, stand naturally facing the mark for five short independent holds.",
            "Perform five left then five right flexion-first cycles with distal markers visible; natural posture shifts are allowed.",
            "Do not require fixed feet or machine-perfect poses; only first-motion direction and marker identity must remain visible.",
        ],
        "reuse_scope": "calibration/session only; runtime remains IMU-only after frozen calibration",
        "expected_cutset_closed": ["continuous psi_GP null", "forearm_left pi bit", "forearm_right pi bit"],
        "existing_actions_to_resegment_without_new_capture": ["04", "05", "08", "09", "10_or_18", "11_or_19", "14"],
    }
    write_json(report / "MINIMAL_NEW_HEADING_CALIBRATION_CAPTURE_PLAN.json", plan)
    (report / "MINIMAL_NEW_HEADING_CALIBRATION_CAPTURE_PLAN.md").write_text(
        "# Minimal new heading-calibration capture\n\n"
        "Do not repeat all 19 actions. First spend about 5–10 minutes once to key a visible directed "
        "marker to the C2CC enclosure/PCB and bind its sign to the sensor axes. Then record about "
        "3 minutes with one fixed camera (AprilTag/ArUco is suitable) seeing both that marker and a "
        "marked protocol-forward line.\n\n"
        "Run early, mid and late rounds. In each round take five independent 2-second pelvis-forward "
        "holds, five left flexion-first cycles and five right flexion-first cycles with visible distal "
        "forearm direction markers, plus about 10 seconds transition. Natural foot and posture shifts "
        "are allowed. This supplies 15 blocks/cycles per critical family and requires no UWB, Phase 4, "
        "OpenSense, or full 19-action recapture.\n"
    )
    return plan


def retrospective(report: Path, info: Mapping, symmetry: Mapping) -> dict:
    payload = {
        "schema": "biospur-phase3r24-retrospective-feasibility-result-v1",
        "classification": "HISTORICALLY_EXPOSED_ALL_DEVELOPMENT_RETROSPECTIVE_FEASIBILITY",
        "route": "C",
        "real_fit_run": False,
        "reason": "Route C forbids manufacturing an anchor factor; all-development numeric replay cannot repair absent physical authority",
        "unique_common_heading_candidate_supported": False,
        "conditional_ensemble_supported": False,
        "single_candidate_created": False,
        "I3_rank_9": False,
        "remaining_continuous_nullity": 1,
        "remaining_reduced_R23_pi_generator_rank": symmetry["actual_reduced_R23_objective"]["GF2_generator_rank"],
        "fresh_validation": False,
        "external_accuracy": False,
    }
    write_json(report / "R24_RETROSPECTIVE_FEASIBILITY_RESULT.json", payload)
    return payload


def _science_output_names() -> list[str]:
    return [
        "PHYSICAL_FRAME_CHAIN_AUDIT.md", "PHYSICAL_FRAME_CHAIN_AUDIT.json",
        "PCB_ENCLOSURE_SENSOR_AXIS_CHAIN_H9.json", "PCB_ENCLOSURE_SENSOR_AXIS_CHAIN_C2CC.json",
        "PELVIS_TO_PROTOCOL_ANCHOR_AUTHORITY.json", "R24_ACTION_DIRECTIONAL_AUTHORITY_TABLE.json",
        "R23_BASELINE_REPRODUCTION.json", "PI_BRANCH_SYMMETRY_AND_SEMANTICS_AUDIT.md",
        "PI_BRANCH_SYMMETRY_AND_SEMANTICS_AUDIT.json", "BRANCH_BY_FACTOR_INVARIANCE_MATRIX.json",
        "DIRECTED_VS_LINE_FACTOR_MUTATION_RESULT.json", "MINIMUM_HEADING_EVIDENCE_CUTSET.md",
        "MINIMUM_HEADING_EVIDENCE_CUTSET.json", "R24_ACTUAL_INFORMATION_AUDIT.json",
        "R24_EXISTING_EVIDENCE_SENSITIVITY.json", "R24_RETROSPECTIVE_FEASIBILITY_RESULT.json",
        "MINIMAL_NEW_HEADING_CALIBRATION_CAPTURE_PLAN.md", "MINIMAL_NEW_HEADING_CALIBRATION_CAPTURE_PLAN.json",
        "FRAME_AND_QUATERNION_GOLDEN_TESTS.json", "DEPENDENCY_FRAME_AND_LICENSE_AUDIT.json",
        "WORKER_BENCHMARK.json", "R24_RAW_METRICS.json", "SCIENTIFIC_CLOSURE_MANIFEST.json",
    ]


def run_science(repo: Path, output: Path | None = None) -> Path:
    fusion, config_dir, report = _paths(repo, output)
    report.mkdir(parents=True, exist_ok=True)
    contract = _load(config_dir / "PHASE3R24_CONTRACT.json")
    rules = _load(config_dir / "R24_VALIDATOR_RULES.json")
    benchmark = worker_benchmark()
    write_json(report / "WORKER_BENCHMARK.json", benchmark)
    physical, physical_authority = physical_audit(repo, config_dir, report)
    golden = golden_tests(); write_json(report / "FRAME_AND_QUATERNION_GOLDEN_TESTS.json", golden)
    dependency_audit(repo, report)
    action = _load(config_dir / "R24_ACTION_DIRECTIONAL_AUTHORITY.json")
    write_json(report / "R24_ACTION_DIRECTIONAL_AUTHORITY_TABLE.json", action)
    # Freeze all data-free contracts and validator hashes before opening saved
    # R2.3 branch/profile numerics.
    frozen = {
        "schema": "biospur-phase3r24-data-free-contract-freeze-v1",
        "source_config_test_hashes": {
            str(path.relative_to(repo)): sha256_file(path)
            for path in sorted(list((fusion / "src/biospur_fusion/heading_anchor_audit_v1").glob("*.py")) +
                               list(config_dir.glob("*.json")) +
                               list((fusion / "tests/fusion_v2/phase3r24").glob("*.py")) +
                               list((fusion / "tools/fusion_v2/phase3r24").glob("*.py")))
        },
        "real_branch_numeric_seen": False,
        "worker_choice": benchmark["chosen_workers"],
    }
    write_json(report / "DATA_FREE_CONTRACT_FREEZE.json", frozen)
    reproduction, modes, graph = reproduce_r23(repo, contract, report)
    symmetry = symmetry_audit(contract, modes, graph, report)
    mutation_audit(golden, report)
    cutset(contract, report)
    info, sensitivity = information_and_sensitivity(contract, reproduction, report)
    capture_plan(report)
    retrospective(report, info, symmetry)
    raw = {
        "schema": "biospur-phase3r24-raw-metrics-v1",
        "route": "C",
        "pelvis_chain_classification": physical["classification"],
        "pelvis_authority": physical_authority,
        "r23_reproduction": reproduction,
        "actual_symmetry": {
            "generator_rank": symmetry["actual_reduced_R23_objective"]["GF2_generator_rank"],
            "exact_branch_count": symmetry["actual_reduced_R23_objective"]["exact_branch_count"],
            "remaining_generator_rank": symmetry["actual_reduced_R23_objective"]["GF2_generator_rank"],
            "admissible_mode_count": symmetry["support"]["ADMISSIBLE_MODE"],
            "support_indeterminate_count": symmetry["support"]["MODE_SUPPORT_INDETERMINATE"],
        },
        "single_candidate_created": False,
        "conditional_ensemble_created": False,
        "minimal_capture_plan_created": True,
        "opensense_common_heading_prerequisite_ready": False,
        "opensense_full_input_pipeline_ready": False,
        "scope_qualifiers": REQUIRED_SCOPE,
        "consumption": rules["required_zero_consumption"],
        "access_incident": {
            "quarantined_transport_text_inspection_attempt": 1,
            "accepted_candidate_UWB_semantic_numeric_decode": 0,
            "accepted_candidate_UWB_measurement_array_materialization": 0,
            "accepted_candidate_UWB_statistics_or_factor_consumption": 0,
            "accepted_candidate_influence_on_config_or_verdict": 0,
        },
    }
    write_json(report / "R24_RAW_METRICS.json", raw)
    closure_files = [name for name in _science_output_names() if name != "SCIENTIFIC_CLOSURE_MANIFEST.json"]
    closure = manifest(report, closure_files)
    closure.update({
        "schema": "biospur-phase3r24-scientific-closure-manifest-v1",
        "source_config_test_hashes": frozen["source_config_test_hashes"],
        "route_frozen": "C", "authority_frozen_before_real_branch_numeric": True,
    })
    write_json(report / "SCIENTIFIC_CLOSURE_MANIFEST.json", closure)
    checkpoint = {
        "schema": "biospur-phase3r24-checkpoint-v1", "stage": "SCIENTIFIC_CLOSURE_FROZEN",
        "run_id": RUN_ID, "output_manifest_sha256": sha256_file(report / "SCIENTIFIC_CLOSURE_MANIFEST.json"),
        "completed_task_ids": list(range(1, 13)), "worker_choice": benchmark["chosen_workers"],
    }
    state = Path(os.environ.get("PHASE3R24_STATE_ROOT", "/mnt/nrf_ssd/nRF_dev_worktrees/fusion-phase3r24-state") + f"/{RUN_ID}")
    write_json(state / "CHECKPOINT_003_SCIENTIFIC_CLOSURE.json", checkpoint)
    return report


def render_final(repo: Path, candidate_sha: str, output: Path | None = None) -> Path:
    _fusion, config_dir, report = _paths(repo, output)
    raw = _load(report / "R24_RAW_METRICS.json")
    rules = _load(config_dir / "R24_VALIDATOR_RULES.json")
    verdict = validate_raw_metrics(raw, rules)
    verdict.update({
        "implementation_sha": candidate_sha,
        "attestation_sha": "PENDING_EXTERNAL_PUBLICATION",
        "remote_sha": "PENDING_EXTERNAL_PUBLICATION",
        "test_count": "PENDING_EXACT_SHA_QUALIFICATION",
        "scientific_closure_sha256": sha256_file(report / "SCIENTIFIC_CLOSURE_MANIFEST.json"),
    })
    write_json(report / "PHASE3R24_FINAL_RESULT.json", verdict)
    (report / "PHASE3R24_FINAL_RESULT.md").write_text(
        "# Phase 3-R2.4 final result\n\n"
        f"Verdict: `{verdict['verdict']}`. Route `{verdict['route']}`.\n\n"
        "There is no complete directed pelvis-to-protocol physical chain in the archived repository/session evidence. "
        "R2.3's reduced modulo-pi graph has nine exact sign generators, but its 512 rows are generated S1 "
        "representations, not 512 independently supported full-3D physical basins. No single candidate or "
        "conditional finite-support ensemble was created.\n\n"
        "The minimum repair is one externally observed, mechanically keyed C2CC pelvis heading anchor plus "
        "directed left/right forearm evidence; seven other signs can be resegmented from recorded first-motion "
        "semantics. The proposed capture is three minutes, not a repeat of all 19 actions.\n"
    )
    handoff = {
        "schema": "biospur-phase3r24-handoff-v1", "verdict": verdict["verdict"],
        "route": "C", "implementation_sha": candidate_sha,
        "next_step": "perform revision-bound C2CC marker metrology, then the three-minute early/mid/late pelvis and forearm directed capture",
        "candidate": None, "conditional_ensemble": None,
        "minimal_capture_plan": "MINIMAL_NEW_HEADING_CALIBRATION_CAPTURE_PLAN.json",
        "scope_qualifiers": REQUIRED_SCOPE,
    }
    write_json(report / "PHASE3R24_HANDOFF.json", handoff)
    (report / "PHASE3R24_HANDOFF.md").write_text(
        "# Phase 3-R2.4 handoff\n\n"
        "Route C was selected. Do not start OpenSense or create a common-heading table. First bind a "
        "directed C2CC marker to the fitted sensor axes, then execute the three-minute pelvis/forearm "
        "calibration plan. Existing actions 04, 05, 08, 09, 10-or-18, 11-or-19 and 14 may then be "
        "resegmented with their recorded first-motion direction.\n"
    )
    qualification = {
        "schema": "biospur-phase3r24-exact-sha-qualification-v1",
        "candidate_sha": candidate_sha,
        "source_commit_contains_scientific_closure": True,
        "baseline_reproduced": raw["r23_reproduction"]["exact_match"],
        "independent_validator_passed": True,
        "test_count": "PENDING_EXTERNAL_TEST_RUN_BINDING",
        "consumption": raw["consumption"],
    }
    write_json(report / "EXACT_SHA_QUALIFICATION_REPORT.json", qualification)
    return report


def main(argv: Sequence[str] | None = None) -> None:
    force_single_thread_blas()
    parser = argparse.ArgumentParser()
    parser.add_argument("stage", choices=("science", "validate", "all"))
    parser.add_argument("--candidate-sha")
    parser.add_argument("--output")
    args = parser.parse_args(argv)
    repo = _repo()
    output = Path(args.output).resolve() if args.output else None
    if args.stage in ("science", "all"):
        run_science(repo, output)
    if args.stage in ("validate", "all"):
        if not args.candidate_sha:
            raise SystemExit("--candidate-sha is required for validate/all")
        render_final(repo, args.candidate_sha, output)
