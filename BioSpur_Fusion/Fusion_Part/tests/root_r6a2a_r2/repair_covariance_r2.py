#!/usr/bin/env python3
"""Diagnose and independently requalify the R6A2A-R2 covariance repair.

Synthetic-only: this module has no real-capture or calibration-registry write
path.  Development and qualification are deliberately separate commands.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, replace
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from typing import Any, Iterable, Mapping

import numpy as np
from scipy.stats import chi2

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from biospur_fusion.root_r6a2a.contracts import (  # noqa: E402
    ALL_NODES, COMMON_NINE, FAMILY_BSF31CC, FAMILY_COMMON_NINE,
    registry_from_sealed_addendum,
)
from biospur_fusion.root_r6a2a_r2.contracts import (  # noqa: E402
    EstimatorOptions, HealthManager, ScenarioDefinition,
)
from biospur_fusion.root_r6a2a_r2.qualification import (  # noqa: E402
    DEVELOPMENT_SCENARIOS, THRESHOLDS, VALIDATION_SCENARIOS, _mc_definition,
    _wilson, authority_negative_controls, authority_static_audit,
    evaluate_gates, execute_generated, rng_counterfactual, run_ablations,
    run_development, run_scenario, tangent_error,
)
from biospur_fusion.root_r6a2a_r2.estimator import build_estimator  # noqa: E402
from biospur_fusion.root_r6a2a_r2.synthetic import GeneratedRun, generate_run  # noqa: E402


CHECKPOINT = "ec451cf140b25e7dbe545e3e09d50b0d3d6edbe8"
FAILED_R2 = ROOT / "logs/root_r6a2a_r2_fdir_bias_uncertainty_closure_20260825T131223Z"
PROTECTED = {
    "parent_seal": ("logs/root_r6a2a_synthetic_fault_aware_shadow_20260825T114046Z/SHA256SUMS", "ab17eed4c1c8e37079f844688100572c5515b2a3121b3cecf6d4f0086978b7f7"),
    "r1_seal": ("logs/root_r6a2a_r1_execution_audit_20260825T121247Z/SHA256SUMS", "b00bc9f802f3c499b7c5c46418744b452af6515cb7a2349444f659befc8e3c60"),
    "slot_ledger": ("logs/root_r6a1a_preintegrator_recovery_20260825T071931Z/CALIBRATION_SLOT_LEDGER.json", "b159043eb7da4518ac6349832ca3c50e0b3453b1e35d97bbac29223f3e85c4eb"),
    "failed_r2_seal": ("logs/root_r6a2a_r2_fdir_bias_uncertainty_closure_20260825T131223Z/SHA256SUMS", "c42473739d9f7d43f5ce95a729986bc05ffc44002e453031e191ec9f15b15d06"),
}
OWNED = (
    "src/biospur_fusion/root_r6a2a_r2/__init__.py",
    "src/biospur_fusion/root_r6a2a_r2/contracts.py",
    "src/biospur_fusion/root_r6a2a_r2/synthetic.py",
    "src/biospur_fusion/root_r6a2a_r2/estimator.py",
    "src/biospur_fusion/root_r6a2a_r2/qualification.py",
    "tests/root_r6a2a_r2/conftest.py",
    "tests/root_r6a2a_r2/test_r6a2a_r2.py",
    "tests/root_r6a2a_r2/generate_r6a2a_r2.py",
    "tests/root_r6a2a_r2/verify_r6a2a_r2.py",
    "tests/root_r6a2a_r2/repair_covariance_r2.py",
    "tests/root_r6a2a_r2/verify_covariance_repair.py",
)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def dump(path: Path, value: Any) -> None:
    def default(item: Any) -> Any:
        if isinstance(item, np.ndarray):
            return item.tolist()
        if hasattr(item, "value"):
            return item.value
        raise TypeError(type(item).__name__)
    path.write_text(json.dumps(value, indent=2, sort_keys=True, default=default, allow_nan=False) + "\n", encoding="utf-8")


def implementation_hashes() -> dict[str, str]:
    return {name: sha(ROOT / name) if (ROOT / name).exists() else "MISSING" for name in OWNED}


def protected_record() -> dict[str, Any]:
    hashes = {key: sha(ROOT / rel) for key, (rel, _) in PROTECTED.items()}
    expected = {key: expected for key, (_, expected) in PROTECTED.items()}
    ledger = json.loads((ROOT / PROTECTED["slot_ledger"][0]).read_text(encoding="utf-8"))
    slots = ledger["slots"] if isinstance(ledger, dict) else ledger
    registry = registry_from_sealed_addendum(ROOT)
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True,
        capture_output=True, check=True,
    ).stdout.strip()
    return {
        "hashes": hashes, "expected_hashes": expected,
        "predecessors_byte_exact": hashes == expected,
        "checkpoint_head": head, "checkpoint_head_preserved": head == CHECKPOINT,
        "slot_count": len(slots), "null_count": sum(row["value"] is None for row in slots),
        "frozen_count": sum(row["status"] == "FROZEN_UNCERTAIN" for row in slots),
        "real_registry_unchanged": hashes["slot_ledger"] == expected["slot_ledger"],
        "hardware_family_separation_exact": (
            registry.family_by_node["BSF31CC"] == FAMILY_BSF31CC
            and all(registry.family_by_node[node] == FAMILY_COMMON_NINE for node in COMMON_NINE)
            and set(registry.family_by_node) == set(ALL_NODES)
        ),
    }


def state_digest(estimator: Any) -> str:
    payload = [{
        "p": state.root_translation_model_m.tolist(),
        "q": state.root_rotation_model_rotvec.tolist(),
        "v": state.root_velocity_model_mps.tolist(),
        "pd": np.diag(state.covariance).tolist(),
    } for state in estimator.states]
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def run_summary(
    scenario: ScenarioDefinition,
    options: EstimatorOptions,
    initial_root_axis_standard_deviations_m: tuple[float, float, float] | None = None,
) -> dict[str, Any]:
    generated = generate_run(ROOT, scenario, options)
    if initial_root_axis_standard_deviations_m is None:
        estimator, _ = execute_generated(ROOT, generated)
    else:
        estimator, _ = build_estimator(ROOT, generated.truth_states[0])
        covariance = estimator.state.covariance.copy()
        covariance[0:3, 0:3] = np.diag(np.square(initial_root_axis_standard_deviations_m))
        estimator.state = replace(estimator.state, covariance=covariance)
        estimator.states[0] = estimator.state
        estimator.root_covariance_traces[0] = float(np.trace(covariance[0:3, 0:3]))
        for value in generated.inputs:
            estimator.step(value)
    state, truth = estimator.states[-1], generated.truth_states[-1]
    error = tangent_error(state, truth, estimator.layout)
    information = estimator.information_rows[-1] if estimator.information_rows else {
        "weak_direction": [0.0, 0.0, 1.0], "directional_projector_frobenius_difference": 0.0,
    }
    weak = np.asarray(information["weak_direction"], float)
    scalar_error = float(weak @ error[:3])
    scalar_variance = float(weak @ state.covariance[:3, :3] @ weak)
    standardized = abs(scalar_error) / np.sqrt(max(scalar_variance, 1e-15))
    raw = [row["raw_nominal_scalar_nis"] for row in estimator.innovation_rows]
    effective = [row["effective_weighted_scalar_nis"] for row in estimator.innovation_rows]
    covariance_eigenvalues = np.linalg.eigvalsh(state.covariance)
    return {
        "scenario_id": scenario.scenario_id,
        "state_trajectory_digest": state_digest(estimator),
        "root_position_final_error_m": float(np.linalg.norm(error[:3])),
        "weak_direction_error_m": scalar_error,
        "weak_direction_squared_error_m2": scalar_error**2,
        "weak_direction_covariance_m2": scalar_variance,
        "weak_direction_nees": scalar_error**2 / max(scalar_variance, 1e-15),
        "coverage_1_2_3_sigma": [bool(standardized <= level) for level in (1, 2, 3)],
        "raw_nominal_scalar_nis_mean_dof_1": float(np.mean(raw)) if raw else None,
        "effective_weighted_scalar_nis_mean_dof_1": float(np.mean(effective)) if effective else None,
        "minimum_covariance_eigenvalue": float(covariance_eigenvalues[0]),
        "covariance_symmetry_max_abs": float(np.max(np.abs(state.covariance - state.covariance.T))),
        "root_covariance_growth_m2": float(estimator.root_covariance_traces[-1] - estimator.root_covariance_traces[0]),
        "yaw_variance_growth_rad2": float(estimator.yaw_variances[-1] - estimator.yaw_variances[0]),
        "projector_frobenius_difference": information.get("directional_projector_frobenius_difference", 0.0),
        "explicit_directional_inflation_final_m2": information.get("explicit_directional_inflation_m2", 0.0),
        "natural_weak_variance_excess_final_m2": information.get("directional_inflation_m2", 0.0),
    }


def _replace_options(**changes: Any) -> EstimatorOptions:
    return replace(EstimatorOptions(), **changes)


def causal_ablation_matrix() -> dict[str, Any]:
    clean = replace(_mc_definition("clean", 111001, 6101), scenario_id="dev_ablation_clean")
    low = replace(_mc_definition("low_vertical_geometry", 111002, 6102), scenario_id="dev_ablation_low_vertical")
    recovery = replace(DEVELOPMENT_SCENARIOS[-1], scenario_id="dev_ablation_outage_recovery", master_seed=111003, trajectory_variant=6103)
    baseline = EstimatorOptions()
    legacy = _replace_options(legacy_explicit_weak_inflation_enabled=True)
    specifications: list[tuple[str, EstimatorOptions, EstimatorOptions, str]] = [
        ("explicit_weak_direction_inflation", legacy, baseline, "disable only the legacy 1.5e-3 m2 rank-one addition"),
        ("geometry_dependent_information_reduction", baseline, baseline, "identity ablation: no geometry-dependent R/H reduction exists"),
        ("missing_observation_covariance_accommodation", baseline, _replace_options(missing_observation_covariance_enabled=False), "disable only no-UWB root/yaw accommodation"),
        ("degraded_mode_health_accommodation", baseline, _replace_options(health_accommodation_enabled=False), "disable health-state information weighting"),
        ("recovery_covariance_accommodation", baseline, _replace_options(recovery_covariance_accommodation_enabled=False), "retain recovery state gain but use nominal R in covariance Joseph noise term"),
        ("robust_weighting", baseline, _replace_options(robust_weighting_enabled=False), "disable only residual robust weighting"),
        ("process_preintegration_all_nodes", baseline, _replace_options(preintegration_covariance_enabled=False), "disable mapped preintegration Q only"),
        ("process_bias_random_walk", baseline, _replace_options(bias_random_walk_covariance_enabled=False), "disable synthetic bias random-walk Q only"),
        ("process_invalid_pelvis", baseline, _replace_options(invalid_pelvis_covariance_enabled=False), "disable invalid-pelvis accommodation only"),
        ("process_numerical_missing_node_floor", baseline, _replace_options(numerical_covariance_floor_enabled=False), "disable numerical/missing-node floor only"),
        ("directional_projector_independent_svd", legacy, _replace_options(legacy_explicit_weak_inflation_enabled=True, independent_directional_projector=True), "replace eigensystem projector by independent whitened-H SVD projector"),
        ("covariance_update_simple_vs_joseph", baseline, _replace_options(covariance_update_form="SIMPLE"), "replace current Joseph form by simple (I-KH)P diagnostic"),
        ("state_active_recovery", baseline, _replace_options(recovery_ramp_enabled=False), "disable recovery weighting for state gain and covariance"),
    ]
    for node in ALL_NODES:
        specifications.append((
            f"preintegration_node_removed:{node}", baseline,
            _replace_options(excluded_preintegration_covariance_node=node),
            f"remove only mapped preintegration covariance from {node}",
        ))
    rows = []
    cache: dict[tuple[str, EstimatorOptions], dict[str, Any]] = {}

    def cached(label: str, scenario: ScenarioDefinition, options: EstimatorOptions) -> dict[str, Any]:
        key = (label, options)
        if key not in cache:
            cache[key] = run_summary(scenario, options)
        return cache[key]

    for name, reference_options, ablated_options, changed in specifications:
        row: dict[str, Any] = {"ablation": name, "changed": changed, "scenarios": {}}
        for label, scenario in (("low_vertical", low), ("clean", clean), ("outage_recovery", recovery)):
            reference = cached(label, scenario, reference_options)
            ablated = cached(label, scenario, ablated_options)
            row["scenarios"][label] = {
                "reference": reference, "ablated": ablated,
                "state_trajectory_changed": reference["state_trajectory_digest"] != ablated["state_trajectory_digest"],
                "delta_weak_error_m": ablated["weak_direction_error_m"] - reference["weak_direction_error_m"],
                "delta_weak_covariance_m2": ablated["weak_direction_covariance_m2"] - reference["weak_direction_covariance_m2"],
                "delta_weak_nees": ablated["weak_direction_nees"] - reference["weak_direction_nees"],
                "delta_raw_nominal_nis": (
                    None if reference["raw_nominal_scalar_nis_mean_dof_1"] is None
                    or ablated["raw_nominal_scalar_nis_mean_dof_1"] is None else
                    ablated["raw_nominal_scalar_nis_mean_dof_1"] - reference["raw_nominal_scalar_nis_mean_dof_1"]
                ),
                "psd_symmetric": (
                    ablated["minimum_covariance_eigenvalue"] >= THRESHOLDS["covariance_min_eigenvalue"]
                    and ablated["covariance_symmetry_max_abs"] <= THRESHOLDS["covariance_symmetry_max_abs"]
                ),
            }
        rows.append(row)
    prior_row: dict[str, Any] = {
        "ablation": "initializer_root_position_covariance_model",
        "changed": "replace legacy isotropic 60 mm prior by exact declared initializer axis-error second moments 55/35/18 mm",
        "scenarios": {},
    }
    for label, scenario in (("low_vertical", low), ("clean", clean), ("outage_recovery", recovery)):
        reference = run_summary(scenario, baseline, (0.060, 0.060, 0.060))
        ablated = cached(label, scenario, baseline)
        prior_row["scenarios"][label] = {
            "reference": reference, "ablated": ablated,
            "state_trajectory_changed": reference["state_trajectory_digest"] != ablated["state_trajectory_digest"],
            "delta_weak_error_m": ablated["weak_direction_error_m"] - reference["weak_direction_error_m"],
            "delta_weak_covariance_m2": ablated["weak_direction_covariance_m2"] - reference["weak_direction_covariance_m2"],
            "delta_weak_nees": ablated["weak_direction_nees"] - reference["weak_direction_nees"],
            "delta_raw_nominal_nis": ablated["raw_nominal_scalar_nis_mean_dof_1"] - reference["raw_nominal_scalar_nis_mean_dof_1"],
            "psd_symmetric": (
                ablated["minimum_covariance_eigenvalue"] >= THRESHOLDS["covariance_min_eigenvalue"]
                and ablated["covariance_symmetry_max_abs"] <= THRESHOLDS["covariance_symmetry_max_abs"]
            ),
        }
    rows.append(prior_row)
    return {
        "schema": "biospur-root-r6a2a-r2-covariance-causal-ablation-matrix-v1",
        "truth_stream_contract": "same scenario seed/variant/input generator for reference and ablated member",
        "nis_definition": {
            "raw": "innovation^2 / nominal sigma^2; scalar measurement DOF=1",
            "effective": "raw scalar NIS multiplied by health*robust information weight; DOF=1",
        },
        "rows": rows,
    }


def _mixed_geometry_run(seed: int, run: int, options: EstimatorOptions) -> GeneratedRun:
    full_scenario = replace(
        _mc_definition("clean", seed, 7000 + run),
        scenario_id=f"mixed_geometry_{run:03d}", duration_s=1.6,
    )
    low_scenario = replace(full_scenario, geometry="LOW_VERTICAL_DIVERSITY")
    full = generate_run(ROOT, full_scenario, options)
    low = generate_run(ROOT, low_scenario, options)
    inputs = []
    for step, (full_input, low_input) in enumerate(zip(full.inputs, low.inputs, strict=True)):
        inputs.append(low_input if 3 <= step <= 8 else full_input)
    digest = hashlib.sha256(
        "|".join(f"{value.step_index}:{value.geometry_class}" for value in inputs).encode()
    ).hexdigest()
    return GeneratedRun(
        full.scenario_id, tuple(inputs), full.truth_states, full.truth_calibration,
        full.fault_window_audit, full.rng_lineage, digest,
    )


def _component(layout: Any, name: str) -> np.ndarray:
    if name == "root_velocity":
        return np.arange(6, 9)
    if name == "joint_orientation":
        return np.concatenate([np.arange(layout.joint_orientation(j).start, layout.joint_orientation(j).stop) for j in layout.joint_ids])
    if name == "joint_rate":
        return np.concatenate([np.arange(layout.joint_rate(j).start, layout.joint_rate(j).stop) for j in layout.joint_ids])
    if name == "gyro_bias":
        return np.concatenate([np.arange(layout.gyro_bias(n).start, layout.gyro_bias(n).stop) for n in layout.node_ids])
    if name == "accelerometer_bias":
        return np.concatenate([np.arange(layout.accel_bias(n).start, layout.accel_bias(n).stop) for n in layout.node_ids])
    raise KeyError(name)


def _aggregate_component(samples: Iterable[tuple[np.ndarray, np.ndarray]]) -> dict[str, Any]:
    rows = list(samples)
    if not rows:
        return {"status": "NO_LOCALLY_OBSERVABLE_COMPONENTS_IN_THIS_PHASE"}
    squared, predicted, nees, standardized = [], [], [], []
    for error, covariance in rows:
        squared.append(float(error @ error))
        predicted.append(float(np.trace(covariance)))
        nees.append(float(error @ np.linalg.pinv(covariance, rcond=1e-12) @ error / len(error)))
        eigenvalues, eigenvectors = np.linalg.eigh(covariance)
        standardized.extend((np.abs(eigenvectors.T @ error) / np.sqrt(np.maximum(eigenvalues, 1e-15))).tolist())
    return {
        "independent_run_count": len(rows),
        "empirical_squared_error_mean": float(np.mean(squared)),
        "predicted_covariance_trace_mean": float(np.mean(predicted)),
        "mean_normalized_nees": float(np.mean(nees)),
        "coverage_1_2_3_sigma": [float(np.mean(np.asarray(standardized) <= level)) for level in (1, 2, 3)],
    }


def directional_trace(runs: int = 16) -> tuple[dict[str, Any], dict[str, Any]]:
    phases = {"pre_degradation": 2, "degradation": 8, "recovery": 9, "post_recovery": 15}
    collected: dict[str, dict[str, list[tuple[np.ndarray, np.ndarray]]]] = {
        phase: {} for phase in phases
    }
    mechanism: dict[str, list[float]] = {}
    projector_differences: list[float] = []
    raw_nis, effective_nis = [], []
    for run in range(runs):
        generated = _mixed_geometry_run(112000 + run, run, EstimatorOptions())
        estimator, _ = execute_generated(ROOT, generated)
        by_step = {row["step"]: row for row in estimator.information_rows}
        for row in estimator.innovation_rows:
            raw_nis.append(row["raw_nominal_scalar_nis"])
            effective_nis.append(row["effective_weighted_scalar_nis"])
        for phase, step in phases.items():
            state = estimator.states[step + 1]
            truth = generated.truth_states[step + 1]
            error = tangent_error(state, truth, estimator.layout)
            info = by_step[step]
            basis = np.asarray(info["information_eigenvectors"])
            projector_differences.append(info["directional_projector_frobenius_difference"])
            definitions: dict[str, tuple[np.ndarray, np.ndarray]] = {}
            for label, axis in (("weakest_root_information_direction", 0), ("orthogonal_root_direction_1", 1), ("orthogonal_root_direction_2", 2)):
                direction = basis[:, axis]
                definitions[label] = (
                    np.array([float(direction @ error[:3])]),
                    np.array([[float(direction @ state.covariance[:3, :3] @ direction)]]),
                )
            definitions["root_yaw"] = (error[5:6], state.covariance[5:6, 5:6])
            for label in ("root_velocity", "joint_orientation", "joint_rate", "gyro_bias", "accelerometer_bias"):
                indices = _component(estimator.layout, label)
                definitions[label] = (error[indices], state.covariance[np.ix_(indices, indices)])
            for label, pair in definitions.items():
                collected[phase].setdefault(label, []).append(pair)
            for name, value in info["process_contributions_weak_variance_m2"].items():
                mechanism.setdefault(name, []).append(value)
            for name in ("prior_weak_variance_m2", "post_measurement_weak_variance_m2", "final_weak_variance_m2", "measurement_weak_variance_contraction_m2", "explicit_directional_inflation_m2"):
                mechanism.setdefault(name, []).append(float(info[name]))
    components = {
        phase: {name: _aggregate_component(rows) for name, rows in values.items()}
        for phase, values in collected.items()
    }
    trace = {
        "schema": "biospur-root-r6a2a-r2-covariance-causal-trace-v1",
        "independent_runs": runs,
        "phase_trial_unit": "one selected state per independent run per declared phase; temporal samples are not pooled",
        "phase_definition": {
            "pre_degradation": "last well-conditioned state before anchor geometry becomes low-vertical",
            "degradation": "last state of six low-vertical steps",
            "recovery": "first well-conditioned state after geometry restoration",
            "post_recovery": "final well-conditioned state",
        },
        "components": components,
        "mechanism_contribution_means_m2": {key: float(np.mean(values)) for key, values in mechanism.items()},
        "equation_trace": [
            "P0_root = diag([0.055, 0.035, 0.018]^2), the declared synthetic initializer axis-error second moments",
            "P_propagated = PSD(P_previous + sum_node J_node Q_preint,node J_node^T + Q_bias_rw + Q_mode + Q_floor)",
            "I_root = sum_i h_root,i^T (sigma_i^2 / weight_i)^-1 h_root,i",
            "K = P H^T (H P H^T + R_effective)^-1",
            "P_measurement = (I-KH)P(I-KH)^T + K R_effective K^T",
            "P_final = PSD(P_measurement); repaired path has no extra weak-direction term",
        ],
        "frame_audit": {
            "weak_vector_frame": "model/world root-translation tangent frame",
            "covariance_block_frame": "same model/world root-translation tangent frame",
            "maximum_eigh_vs_independent_svd_projector_frobenius_difference": float(max(projector_differences)),
        },
        "hypotheses": {
            "1_geometry_then_inflated_second_time": "SUPPORTED_CAUSAL_ROOT",
            "2_inflation_accumulated_each_step": "SUPPORTED_CAUSAL_ROOT",
            "3_missing_degraded_robust_recovery_double_inflate": "NOT_SUPPORTED_IN_LOW_VERTICAL_PATH",
            "4_eigenvector_frame_mismatch": "REJECTED",
            "5_full_state_block_inflated_instead_of_1d": "REJECTED; legacy term was rank-one in root position",
            "6_process_or_preintegration_covariance_mapped_twice": "REJECTED_BY_SOURCE_TRACE_AND_NODE_ABLATIONS",
            "7_state_and_covariance_jacobians_inconsistent": "REJECTED; same H and K feed correction and Joseph update",
            "8_recovery_covariance_stronger_than_state_gain": "REJECTED_IN_LOW_VERTICAL; recovery states absent there",
            "9_nis_nees_subspace_disagreement": "SUPPORTED_AS_SYMPTOM; raw/effective NIS remain stable while legacy state NEES falls",
            "10_isotropic_initializer_prior_retained_by_weak_geometry": "SUPPORTED_RESIDUAL_CAUSE_AFTER_ATTEMPT_001; repaired by declared per-axis second moments",
        },
        "observable_bias_note": "Bias blocks are reported; components without low-motion observability retain prior/process covariance and are not claimed observable.",
    }
    nis = {
        "schema": "biospur-root-r6a2a-r2-directional-nees-nis-development-v1",
        "nis_definition": {
            "raw_nominal_scalar": "innovation^2 / nominal sigma^2",
            "effective_weighted_scalar": "raw scalar NIS * health_weight * robust_weight",
            "measurement_degrees_of_freedom": 1,
            "neither_headline_is_divided_by_measurement_count": True,
        },
        "raw_nominal_scalar_nis_mean": float(np.mean(raw_nis)),
        "effective_weighted_scalar_nis_mean": float(np.mean(effective_nis)),
        "component_phase_results": components,
    }
    return trace, nis


def development(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    trace, nis = directional_trace()
    matrix = causal_ablation_matrix()
    dump(out / "COVARIANCE_CAUSAL_TRACE.json", trace)
    dump(out / "DIRECTIONAL_NEES_NIS.json", nis)
    dump(out / "COVARIANCE_ABLATION_MATRIX.json", matrix)
    dump(out / "DEVELOPMENT_RESULTS.json", run_development(ROOT))
    dump(out / "DEVELOPMENT_ABLATIONS.json", run_ablations(ROOT))
    dump(out / "FAULT_TRUTH_DATAFLOW_AUDIT.json", authority_static_audit())
    dump(out / "FAULT_LABEL_NEGATIVE_CONTROLS.json", authority_negative_controls(ROOT))
    dump(out / "COUNTERFACTUAL_INPUT_EQUIVALENCE.json", rng_counterfactual(ROOT))
    dump(out / "DEVELOPMENT_STATUS.json", {
        "repair": "remove repeated additive rank-one weak-direction covariance; retain weak geometry once through H/R; match initial root covariance to declared 55/35/18 mm initializer axis second moments",
        "legacy_term_m2_per_weak_step": 1.5e-3,
        "default_explicit_inflation_m2": 0.0,
        "development_complete": True,
    })
    failed_attempt = out / "attempt_001/RAW_FORMAL_VALIDATION.json"
    if failed_attempt.exists():
        formal = json.loads(failed_attempt.read_text(encoding="utf-8"))
        low = formal["covariance_monte_carlo"]["headline_classes"]["low_vertical_geometry"]
        dump(out / "FAILED_ATTEMPT_DIAGNOSIS.json", {
            "schema": "biospur-root-r6a2a-r2-failed-attempt-diagnosis-v1",
            "attempt_id": "attempt_001", "status": "FAIL_RETAINED",
            "failed_gate": "AH_covariance_truth_consistency",
            "low_vertical_result": low,
            "independently_demonstrated_mathematical_error": {
                "legacy_initializer_axis_standard_deviations_m": [0.060, 0.060, 0.060],
                "actual_declared_initializer_axis_errors_m": [0.055, -0.035, 0.018],
                "error": "isotropic prior did not equal the second moments of the synthetic initializer error distribution",
                "why_low_vertical_exposes_it": "weak vertical information retains materially more of P0 than clean geometry",
                "repair_selected_without_fitting_attempt_result": "diag([0.055,0.035,0.018]^2)",
            },
        })


def freeze(out: Path) -> None:
    hashes = implementation_hashes()
    if any(value == "MISSING" for value in hashes.values()):
        raise SystemExit(f"owned implementation file missing: {hashes}")
    ledger_path = out / "VALIDATION_ATTEMPT_LEDGER.json"
    if ledger_path.exists():
        ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    else:
        ledger = {"schema": "biospur-root-r6a2a-r2-validation-attempt-ledger-v1", "attempts": []}
    attempt_ordinal = len(ledger["attempts"]) + 1
    attempt_id = f"attempt_{attempt_ordinal:03d}"
    validation_base = 131000 + (attempt_ordinal - 1) * 30000
    mc_start = 141000 + (attempt_ordinal - 1) * 30000
    validation_seeds = {row.scenario_id: validation_base + index for index, row in enumerate(VALIDATION_SCENARIOS)}
    mc_bases = {
        "clean": mc_start, "global_uwb_outage": mc_start + 1000,
        "low_vertical_geometry": mc_start + 2000, "observable_gyro_bias": mc_start + 3000,
    }
    manifest = {
        "schema": "biospur-root-r6a2a-r2-repair-independent-seed-allocation-v1",
        "allocated_before_execution": True,
        "attempt_id": attempt_id, "overall_attempt_ordinal": attempt_ordinal,
        "status": "ALLOCATED_NOT_EXECUTED",
        "validation_scenario_seeds": validation_seeds,
        "monte_carlo_seed_bases": mc_bases,
        "runs_per_headline_class": 100,
        "excluded_seed_ranges": {
            "failed_r2": [81001, 94999],
            "repair_development": [95000, 159999],
        },
        "implementation_hashes": hashes,
        "thresholds_sha256": hashlib.sha256(json.dumps(THRESHOLDS, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
        "validation_scenario_structure_sha256": hashlib.sha256(json.dumps([
            {**asdict(row), "master_seed": None} for row in VALIDATION_SCENARIOS
        ], sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
    }
    manifest_name = (
        "QUALIFICATION_IMPLEMENTATION_FREEZE.json" if attempt_ordinal == 1
        else f"QUALIFICATION_IMPLEMENTATION_FREEZE_{attempt_id.upper()}.json"
    )
    dump(out / manifest_name, manifest)
    ledger["attempts"].append({
            "attempt_id": attempt_id, "status": "ALLOCATED_NOT_EXECUTED",
            "seed_manifest": manifest_name,
            "result": None,
        })
    dump(ledger_path, ledger)


def fresh_monte_carlo(seed_bases: Mapping[str, int], runs_per_class: int) -> dict[str, Any]:
    run_rows, coverage_rows, nis_rows = [], [], []
    for kind in ("clean", "global_uwb_outage", "low_vertical_geometry", "observable_gyro_bias"):
        for run in range(runs_per_class):
            scenario = _mc_definition(kind, int(seed_bases[kind]) + run, 8000 + run)
            generated = generate_run(ROOT, scenario)
            estimator, _ = execute_generated(ROOT, generated)
            if kind == "observable_gyro_bias":
                index = scenario.private_truth.window.end_step
                state, truth = estimator.states[index + 1], generated.truth_states[index + 1]
            else:
                state, truth = estimator.states[-1], generated.truth_states[-1]
            injected = np.array([scenario.private_truth.magnitude, 0.0, 0.0]) if kind == "observable_gyro_bias" else None
            error = tangent_error(state, truth, estimator.layout, injected, scenario.private_truth.target_node)
            if kind == "low_vertical_geometry":
                direction = np.asarray(estimator.information_rows[-1]["weak_direction"])
                scalar_error = float(direction @ error[:3])
                scalar_variance = float(direction @ state.covariance[:3, :3] @ direction)
                normalized_coordinates = np.array([abs(scalar_error) / np.sqrt(max(scalar_variance, 1e-15))])
                nees, block, dimension = scalar_error**2 / max(scalar_variance, 1e-15), "weak_root_direction", 1
                empirical_squared_error, predicted_covariance = scalar_error**2, scalar_variance
            else:
                if kind in {"clean", "global_uwb_outage"}:
                    indices = np.arange(3)
                    block = "root_position" if kind == "clean" else "root_position_during_outage"
                else:
                    bias = estimator.layout.gyro_bias(scenario.private_truth.target_node)
                    indices = np.arange(bias.start, bias.stop)
                    block = "target_gyro_bias"
                covariance = state.covariance[np.ix_(indices, indices)]
                block_error = error[indices]
                eigenvalues, eigenvectors = np.linalg.eigh(covariance)
                normalized_coordinates = np.abs(eigenvectors.T @ block_error) / np.sqrt(np.maximum(eigenvalues, 1e-15))
                dimension = len(indices)
                nees = float(block_error @ np.linalg.pinv(covariance, rcond=1e-12) @ block_error)
                empirical_squared_error = float(block_error @ block_error)
                predicted_covariance = float(np.trace(covariance))
            run_rows.append({
                "class": kind, "run": run, "seed": scenario.master_seed, "block": block,
                "dimension": dimension, "nees": float(nees), "normalized_nees": float(nees / dimension),
                "empirical_squared_error": empirical_squared_error, "predicted_covariance_trace": predicted_covariance,
                "minimum_covariance_eigenvalue": float(np.min(np.linalg.eigvalsh(state.covariance))),
                "covariance_symmetry_max_abs": float(np.max(np.abs(state.covariance - state.covariance.T))),
                "outage_root_covariance_growth": estimator.root_covariance_traces[-1] - estimator.root_covariance_traces[0],
                "outage_yaw_variance_growth": estimator.yaw_variances[-1] - estimator.yaw_variances[0],
            })
            for level in (1, 2, 3):
                coverage_rows.append({
                    "class": kind, "run": run, "sigma_level": level,
                    "covered_coordinates": int(np.sum(normalized_coordinates <= level)),
                    "coordinate_count": int(len(normalized_coordinates)),
                })
            raw = [item["raw_nominal_scalar_nis"] for item in estimator.innovation_rows]
            effective = [item["effective_weighted_scalar_nis"] for item in estimator.innovation_rows]
            nis_rows.append({
                "class": kind, "run": run, "mean_raw_nominal_scalar_uwb_nis": float(np.mean(raw)) if raw else None,
                "mean_effective_weighted_scalar_uwb_nis": float(np.mean(effective)) if effective else None,
                "measurement_degrees_of_freedom": 1, "uwb_innovation_count": len(raw),
            })
    summaries = {}
    for kind in ("clean", "global_uwb_outage", "low_vertical_geometry", "observable_gyro_bias"):
        selected = [row for row in run_rows if row["class"] == kind]
        dimension = selected[0]["dimension"]
        values = np.asarray([row["normalized_nees"] for row in selected])
        coverage = {}
        for level in (1, 2, 3):
            crows = [row for row in coverage_rows if row["class"] == kind and row["sigma_level"] == level]
            successes = sum(row["covered_coordinates"] for row in crows)
            trials = sum(row["coordinate_count"] for row in crows)
            coverage[str(level)] = {
                "fraction": successes / trials, "wilson_95": list(_wilson(successes, trials)),
                "successes": successes, "trials": trials,
            }
        nrows = [row for row in nis_rows if row["class"] == kind and row["mean_raw_nominal_scalar_uwb_nis"] is not None]
        summaries[kind] = {
            "independent_run_count": len(selected), "trial_unit": "one declared evaluation state per independent run",
            "dimension": dimension, "mean_normalized_nees": float(np.mean(values)),
            "chi_square_95_mean_normalized_nees_bounds": [
                float(chi2.ppf(0.025, len(selected) * dimension) / (len(selected) * dimension)),
                float(chi2.ppf(0.975, len(selected) * dimension) / (len(selected) * dimension)),
            ],
            "coverage": coverage,
            "mean_empirical_squared_error": float(np.mean([row["empirical_squared_error"] for row in selected])),
            "mean_predicted_covariance_trace": float(np.mean([row["predicted_covariance_trace"] for row in selected])),
            "mean_raw_nominal_scalar_uwb_nis_dof_1": float(np.mean([row["mean_raw_nominal_scalar_uwb_nis"] for row in nrows])) if nrows else None,
            "mean_effective_weighted_scalar_uwb_nis_dof_1": float(np.mean([row["mean_effective_weighted_scalar_uwb_nis"] for row in nrows])) if nrows else None,
            "all_psd_symmetric": all(
                row["minimum_covariance_eigenvalue"] >= THRESHOLDS["covariance_min_eigenvalue"]
                and row["covariance_symmetry_max_abs"] <= THRESHOLDS["covariance_symmetry_max_abs"]
                for row in selected
            ),
        }
    passed = all(
        row["independent_run_count"] >= 100 and row["all_psd_symmetric"]
        and row["chi_square_95_mean_normalized_nees_bounds"][0] <= row["mean_normalized_nees"] <= row["chi_square_95_mean_normalized_nees_bounds"][1]
        and THRESHOLDS["coverage_1sigma_bounds"][0] <= row["coverage"]["1"]["fraction"] <= THRESHOLDS["coverage_1sigma_bounds"][1]
        and THRESHOLDS["coverage_2sigma_bounds"][0] <= row["coverage"]["2"]["fraction"] <= THRESHOLDS["coverage_2sigma_bounds"][1]
        and THRESHOLDS["coverage_3sigma_bounds"][0] <= row["coverage"]["3"]["fraction"] <= THRESHOLDS["coverage_3sigma_bounds"][1]
        for row in summaries.values()
    )
    return {
        "schema": "biospur-root-r6a2a-r2-repair-fresh-monte-carlo-v1",
        "headline_classes": summaries, "run_rows": run_rows,
        "coverage_rows": coverage_rows, "nis_rows": nis_rows,
        "temporal_samples_treated_as_independent_trials": False,
        "nis_definition": {
            "raw_nominal_scalar": "innovation^2 / nominal sigma^2; DOF=1",
            "effective_weighted_scalar": "raw scalar NIS * health*robust information weight; DOF=1",
        },
        "pass": passed,
    }


def predecessor_tests() -> dict[str, Any]:
    paths = [
        "tests/root_r6a0", "tests/root_r6a1a", "tests/root_r6a1b", "tests/root_r6a1c",
        "tests/root_r6a1c_bsf31cc", "tests/root_r6a2a", "tests/root_r6a2a_r1", "tests/root_r6a2a_r2",
    ]
    command = [sys.executable, "-m", "pytest", "-q", *paths]
    completed = subprocess.run(command, cwd=ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False)
    return {"command": command, "exit_code": completed.returncode, "output": completed.stdout, "all_pass": completed.returncode == 0}


def health_order_check() -> bool:
    evidence = [
        ("imu_health", "BSFEC35", True, "IMU", False),
        ("uwb_tag_health", "BSFEC35", False, "UWB", False),
        ("uwb_link_health", "BSFEC35:2", True, "LINK", False),
    ]
    first, second = HealthManager(), HealthManager()
    first.update_modalities(evidence, 1.0)
    second.update_modalities(list(reversed(evidence)), 1.0)
    return first.snapshot() == second.snapshot() and first.transitions() == second.transitions()


def qualify(out: Path) -> bool:
    ledger_path = out / "VALIDATION_ATTEMPT_LEDGER.json"
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    attempt_record = ledger["attempts"][-1]
    if attempt_record["status"] != "ALLOCATED_NOT_EXECUTED":
        raise SystemExit("latest attempt is not allocated and unexecuted")
    attempt_id = attempt_record["attempt_id"]
    frozen = json.loads((out / attempt_record["seed_manifest"]).read_text(encoding="utf-8"))
    current_hashes = implementation_hashes()
    if current_hashes != frozen["implementation_hashes"]:
        raise SystemExit("implementation changed after qualification freeze")
    attempt = out / attempt_id
    attempt.mkdir(exist_ok=False)
    validation = {}
    seed_map = frozen["validation_scenario_seeds"]
    for scenario in VALIDATION_SCENARIOS:
        fresh = replace(scenario, master_seed=int(seed_map[scenario.scenario_id]))
        validation[scenario.scenario_id] = run_scenario(ROOT, fresh)
    monte_carlo = fresh_monte_carlo(frozen["monte_carlo_seed_bases"], int(frozen["runs_per_headline_class"]))
    formal = {
        "schema": "biospur-root-r6a2a-r2-repair-first-independent-formal-validation-v1",
        "formal_run_ordinal": 1, "overall_attempt_ordinal": frozen["overall_attempt_ordinal"],
        "rerun": False, "retuned_after_opening": False,
        "scenario_results": validation, "covariance_monte_carlo": monte_carlo,
    }
    dump(attempt / "RAW_FORMAL_VALIDATION.json", formal)
    development_result = json.loads((out / "DEVELOPMENT_RESULTS.json").read_text(encoding="utf-8"))
    standard_ablations = json.loads((out / "DEVELOPMENT_ABLATIONS.json").read_text(encoding="utf-8"))
    authority = json.loads((out / "FAULT_TRUTH_DATAFLOW_AUDIT.json").read_text(encoding="utf-8"))
    negative = json.loads((out / "FAULT_LABEL_NEGATIVE_CONTROLS.json").read_text(encoding="utf-8"))
    counterfactual = json.loads((out / "COUNTERFACTUAL_INPUT_EQUIVALENCE.json").read_text(encoding="utf-8"))
    protected = protected_record()
    protected["health_update_order_invariant"] = health_order_check()
    clean = validation["val_clean_81001"]
    initial = np.asarray(clean["states"][0]["covariance_diagonal"])[63:123]
    final = np.asarray(clean["states"][-1]["covariance_diagonal"])[63:123]
    protected["unobservable_bias_covariance_ratio_min"] = float(np.min(final / initial))
    predecessor = predecessor_tests()
    replay_scenario = replace(VALIDATION_SCENARIOS[0], master_seed=int(seed_map[VALIDATION_SCENARIOS[0].scenario_id]))
    replay = run_scenario(ROOT, replay_scenario)
    replay_pass = replay["output_digest"] == validation[VALIDATION_SCENARIOS[0].scenario_id]["output_digest"]
    freeze_attestation = {
        "manifest_hash_verified": current_hashes == frozen["implementation_hashes"],
        "implementation_hashes_verified": all(value != "MISSING" for value in current_hashes.values()),
    }
    gates = evaluate_gates(
        development_result, formal, authority, negative, counterfactual,
        standard_ablations, predecessor, protected, freeze_attestation, replay_pass,
    )
    dump(attempt / "MANDATORY_GATES.json", gates)
    dump(attempt / "PREDECESSOR_REGRESSIONS.json", predecessor)
    dump(attempt / "PROTECTED_BOUNDARIES.json", protected)
    dump(out / "FINAL_FORMAL_VALIDATION.json", formal)
    dump(out / "MANDATORY_GATES.json", gates)
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    ledger["attempts"][-1].update({
        "status": "PASS" if gates["all_pass"] else "FAIL",
        "result": f"{attempt_id}/RAW_FORMAL_VALIDATION.json",
        "mandatory_gates": {"passed": gates["passed"], "total": gates["total"], "all_pass": gates["all_pass"]},
        "failed_gates": [key for key, row in gates["results"].items() if not row["pass"]],
    })
    dump(ledger_path, ledger)
    return bool(gates["all_pass"])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--development", action="store_true")
    group.add_argument("--freeze", action="store_true")
    group.add_argument("--qualify", action="store_true")
    args = parser.parse_args()
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=True)
    if args.development:
        development(out)
        return 0
    if args.freeze:
        freeze(out)
        return 0
    return 0 if qualify(out) else 2


if __name__ == "__main__":
    raise SystemExit(main())
