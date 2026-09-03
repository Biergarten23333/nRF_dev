"""Independent synthetic qualification and mandatory mutation controls."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import math
import time
from typing import Any, Mapping, Sequence

import numpy as np
from scipy.spatial.transform import Rotation
from biospur_fusion.v0.raw6_heading import qmt_selected_cross_denominator_report

from .branches import FunctionalCandidateBank
from .calibration import center_refined_functional_selection
from .contracts import StageBudget, stage_budget
from .factors import (
    fixed_geometry_noise_lever_bounds,
    propagated_joint_noise_sigma,
)
from .geometry import BodyGeometry
from .model import C2CalibrationObjective, summarize_held_out_covariance_blocks, wrap
from .progressive import ProgressiveProfile
from .solver import compare_states, information_report, solve_staged
from .synthetic_truth import SyntheticTruth, TRUTH_EPISODES, generate_synthetic_truth
from .validation import (
    validate_basis_contract,
    validate_independent_synthetic_truth,
    validate_progressive_snapshot,
)
from .staging import BoundedStage, pipeline_wall_limit


def _budgets(config: Mapping[str, Any]) -> dict[str, StageBudget]:
    return {
        name: stage_budget(config, name)
        for name in (
            "B_FUNCTIONAL_MOUNTS_CONNECTIONS",
            "C_NINE_RELATIVE_HEADINGS",
            "D_ALL_ACTION_REFINEMENT",
        )
    }


def _mount_truth_error_deg(
    candidate, truth: SyntheticTruth,
) -> dict[str, float]:
    return {
        segment: float(np.degrees(Rotation.from_matrix(
            candidate.body_from_sensor_by_segment[segment]
            @ truth.body_from_sensor[segment].T
        ).magnitude()))
        for segment in truth.body_from_sensor
    }


def qualify_case(
    name: str,
    truth: SyntheticTruth,
    geometry: BodyGeometry,
    config: Mapping[str, Any],
    *,
    seed: int,
) -> tuple[dict[str, Any], C2CalibrationObjective, FunctionalCandidateBank]:
    started = time.monotonic()
    oracle = validate_independent_synthetic_truth(truth)
    selection, _, objective = center_refined_functional_selection(
        truth.episodes, geometry, config, seed=seed, budget_mode="FULL",
    )
    candidate = selection.candidate
    functional_bank = FunctionalCandidateBank(
        candidate.hinge_axes, (candidate,), (), candidate,
    )
    solved = solve_staged(
        objective, _budgets(config), config,
        previous_state=selection.seed_state, seed=seed + 100,
    )
    state = np.asarray(solved["state"], dtype=float)
    heading_error = np.degrees(np.abs(wrap(state[:9] - truth.headings_rad)))
    offset_error = np.abs(state[9:] - truth.axial_offsets_m)
    mount_error = _mount_truth_error_deg(candidate, truth)
    held_stage = BoundedStage(
        "HELD_OUT_EVALUATION",
        pipeline_wall_limit(config, "FULL", "held_out_evaluation"),
    )
    information = held_stage.run(
        "information_report", lambda: information_report(objective, state),
    )
    held_out = objective.held_out_report(
        state,
        float(config["acceptance"]["maximum_held_out_joint_center_nrmse"]),
        deadline=held_stage,
    )
    qmt_pass = all(
        estimate.qmt_axis_report.get("multistart_parent_axis_max_spread_deg") is not None
        and estimate.qmt_heading_report.get("qualification", {}).get("pass") is True
        and not estimate.qmt_axis_report["input_degeneracy"].get(
            "named_numerical_physical_conflicts", []
        )
        for estimate in functional_bank.hinge_axes.values()
    ) and len(functional_bank.hinge_axes) == 4
    qmt_selection_aware = {}
    for edge, estimate in functional_bank.hinge_axes.items():
        diagnostic = estimate.qmt_axis_report["input_degeneracy"]
        starts = diagnostic["qmt_selected_distribution_by_start"]
        qmt_selection_aware[edge] = {
            "predeclared_relevant_actions": diagnostic[
                "predeclared_relevant_actions"
            ],
            "start_count": len(starts),
            "multistart_final_costs": diagnostic["multistart_final_costs"],
            "multistart_parent_axis_max_spread_deg": diagnostic[
                "multistart_parent_axis_max_spread_deg"
            ],
            "maximum_uninformative_near_axis_fraction_among_excited": max(
                row["selected_denominator_audit"][role]["distribution"][
                    "uninformative_near_axis_fraction_among_excited"
                ]
                for row in starts for role in ("parent", "child")
            ),
            "minimum_cross_noise_significance_quantile": min(
                row["selected_denominator_audit"][role]["distribution"][
                    "cross_noise_significance_quantile"
                ]
                for row in starts for role in ("parent", "child")
            ),
            "minimum_effective_excited_rows": min(
                row["selected_denominator_audit"][role]["distribution"][
                    "effective_sufficiently_excited_rows"
                ]
                for row in starts for role in ("parent", "child")
            ),
            "material_selected_degeneracy_count": sum(
                row["selected_denominator_audit"][role]["distribution"][
                    "material_selected_degeneracy"
                ]
                for row in starts for role in ("parent", "child")
            ),
            "runtime_warning_count": sum(
                len(row["runtime_warnings"]) for row in starts
            ),
            "selected_distribution_multistart_gate": diagnostic[
                "selected_distribution_multistart_gate"
            ],
            "action_phase_excitation_contribution": starts[0][
                "action_phase_excitation_contribution"
            ],
        }
    expected_knee_actions = {
        "knee_left": ["10_knee_left_seated", "16_squat", "18_heel_to_butt_left"],
        "knee_right": ["11_knee_right_seated", "16_squat", "19_heel_to_butt_right"],
    }
    bilateral_knee_selection_pass = all(
        edge in qmt_selection_aware
        and qmt_selection_aware[edge]["predeclared_relevant_actions"] == actions
        and qmt_selection_aware[edge]["start_count"] == 3
        and not any(
            row["all_multistarts_material"]
            for row in qmt_selection_aware[edge][
                "selected_distribution_multistart_gate"
            ].values()
        )
        and qmt_selection_aware[edge]["runtime_warning_count"] == 0
        and all(np.isfinite(qmt_selection_aware[edge]["multistart_final_costs"]))
        and np.isfinite(qmt_selection_aware[edge][
            "multistart_parent_axis_max_spread_deg"
        ])
        for edge, actions in expected_knee_actions.items()
    )
    synthetic = config["synthetic_acceptance"]
    pipeline_stages = {
        **selection.audit["bounded_pipeline_stages"],
        "held_out_evaluation": held_stage.report(),
    }
    gates = {
        "independent_oracle": oracle["pass"],
        "all_four_qmt_functional_edges_qualified": qmt_pass,
        "heading_truth_error": float(np.max(heading_error))
        <= float(synthetic["maximum_heading_truth_error_deg"]),
        "mount_truth_error": max(mount_error.values())
        <= float(synthetic["maximum_mount_rotation_truth_error_deg"]),
        "offset_truth_error": float(np.max(offset_error))
        <= float(synthetic["maximum_axial_offset_truth_error_m"]),
        "gauge_reduced_rank": information["gauge_reduced_heading_rank"] == 9,
        "posterior_uncertainty": information["maximum_posterior_heading_sigma_deg"]
        <= float(config["acceptance"]["maximum_heading_posterior_sigma_deg"]),
        "branch_concentration": solved["stages"][1]["multistart_max_spread_deg"]
        <= float(config["acceptance"]["maximum_multistart_branch_spread_deg"]),
        "functional_mount_branch_concentration": selection.audit[
            "maximum_mount_branch_spread_deg"
        ] <= float(config["acceptance"]["maximum_functional_mount_branch_spread_deg"]),
        "held_out_prediction": held_out["pass"],
        "hard_physical_feasibility": solved["feasibility"]["pass"],
        "full_circle_sectors_per_edge": all(
            len(rows) == int(config["stages"]["C_NINE_RELATIVE_HEADINGS"]["multistarts"])
            for rows in solved["stages"][1]["edge_runs"].values()
        ),
        "all_pipeline_stages_bounded": all(
            row.get("within_wall_limit", row.get("complete", False))
            for row in pipeline_stages.values()
        ),
        "bilateral_knee_selection_aware_distribution_qualified": (
            bilateral_knee_selection_pass
        ),
    }
    report = {
        "schema": "biospur-c2-independent-synthetic-case-v1",
        "name": name,
        "seed": truth.seed,
        "episode_order": [episode.action for episode in truth.episodes],
        "noise": truth.noise,
        "oracle": oracle,
        "functional_bank": selection.audit,
        "center_refinement": selection.audit["center_refinement"],
        "center_branch_attempts": selection.audit["rejection_diagnostics"],
        "center_branch_trace": selection.audit["hypothesis_enumeration_trace"],
        "center_branch_wall_limit_s": selection.audit["hypothesis_enumeration_wall_limit_s"],
        "center_branch_elapsed_s": selection.audit["hypothesis_enumeration_elapsed_s"],
        "center_retained_candidate_count": selection.audit["retained_near_optimal_count"],
        "selected_functional_candidate": candidate.candidate_id,
        "heading_truth_error_deg": heading_error.tolist(),
        "maximum_heading_truth_error_deg": float(np.max(heading_error)),
        "mount_truth_error_deg": mount_error,
        "maximum_mount_truth_error_deg": max(mount_error.values()),
        "axial_offset_truth_error_m": offset_error.tolist(),
        "maximum_axial_offset_truth_error_m": float(np.max(offset_error)),
        "information": information,
        "held_out": held_out,
        "qmt_selection_aware_distribution_by_hinge": qmt_selection_aware,
        "bilateral_knee_selection_aware_comparison": {
            edge: qmt_selection_aware.get(edge) for edge in expected_knee_actions
        },
        "bounded_pipeline_stages": pipeline_stages,
        "feasibility": {key: value for key, value in solved["feasibility"].items() if key != "kinematics"},
        "stage_summary": [{
            key: value for key, value in stage.items()
            if key not in {"runs", "edge_runs", "state"}
        } for stage in solved["stages"]],
        "state": state.tolist(),
        "gates": gates,
        "pass": all(gates.values()),
        "elapsed_s": time.monotonic() - started,
    }
    return report, objective, functional_bank


def _expect_rejection(name: str, callback) -> dict[str, Any]:
    try:
        callback()
    except (ValueError, RuntimeError) as exc:
        return {"name": name, "rejected": True, "diagnostic": str(exc)}
    return {"name": name, "rejected": False, "diagnostic": "mutation was accepted"}


def mutation_controls(
    base_truth: SyntheticTruth,
    objective: C2CalibrationObjective,
    functional_bank: FunctionalCandidateBank,
    geometry: BodyGeometry,
    config: Mapping[str, Any],
) -> list[dict[str, Any]]:
    base_snapshot = {
        "single_persistent_profile": True,
        "per_action_profile_count": 0,
        "retained_episodes": ["00_initial_still"],
        "raw_row_count_used_as_progress": False,
        "action_count_used_as_readiness": False,
        "initial_still_complete_calibration": False,
        "step": 1,
        "ready": False,
    }
    controls = []
    selection_contract = config["bounded_pipeline"]["qmt_hinge_axis"][
        "selection_aware_cross_denominator"
    ]
    selection_gyro = np.tile(np.array([0.0, 1.0, 0.0]), (600, 1))
    selection_gyro[:30] = np.array([1.0, 0.0, 0.0])
    selection_axis = np.array([1.0, 0.0, 0.0])
    selection_covariance = np.eye(3) * 0.003**2
    unused_parallel = qmt_selected_cross_denominator_report(
        selection_gyro, selection_axis, np.arange(300, 600),
        selection_covariance, selection_contract,
    )
    isolated_selected_parallel = qmt_selected_cross_denominator_report(
        selection_gyro, selection_axis, np.r_[0, np.arange(300, 599)],
        selection_covariance, selection_contract,
    )
    materially_selected_parallel = qmt_selected_cross_denominator_report(
        selection_gyro, selection_axis, np.arange(300),
        selection_covariance, selection_contract,
    )
    controls.append({
        "name": "selection_aware_qmt_parallel_row_materiality",
        "rejected": bool(
            not unused_parallel["material_selected_degeneracy"]
            and not isolated_selected_parallel["material_selected_degeneracy"]
            and materially_selected_parallel["material_selected_degeneracy"]
        ),
        "diagnostic": {
            "unused_preselection_parallel_rows": unused_parallel,
            "one_isolated_selected_parallel_row": isolated_selected_parallel,
            "thirty_materially_selected_parallel_rows": materially_selected_parallel,
            "thresholds_derived_from_real_capture": False,
        },
    })
    controls.append(_expect_rejection(
        "unbounded_pipeline_stage_cannot_run_past_deadline",
        lambda: BoundedStage("MUTATED_STALL", 0.01).run(
            "deliberate_stall", lambda: time.sleep(0.05),
        ),
    ))
    dilution = summarize_held_out_covariance_blocks([
        {
            "edge": "knee_left", "action": "mutation",
            "covariance_block_id": "deliberately_bad", "normalized": np.full(30, 3.0),
        },
        {
            "edge": "knee_left", "action": "mutation",
            "covariance_block_id": "paired_good", "normalized": np.zeros(30),
        },
    ], float(config["acceptance"]["maximum_held_out_joint_center_nrmse"]))
    controls.append({
        "name": "bad_held_out_block_hidden_by_pooled_pair",
        "rejected": bool(
            dilution["pass"] is False
            and dilution["maximum_pooled_edge_action_nrmse_secondary"]
            <= dilution["threshold"]
            and "knee_left:mutation:deliberately_bad" in dilution["named_conflicts"]
        ),
        "diagnostic": dilution,
    })
    noise_levers = fixed_geometry_noise_lever_bounds(config)["shoulder_right"]
    energetic_rate = np.tile(np.array([0.0, 5.0, 0.0]), (30, 1))
    propagated_sigma, propagation = propagated_joint_noise_sigma(
        parent_acc_cov=np.eye(3) * 0.03**2,
        child_acc_cov=np.eye(3) * 0.03**2,
        parent_gyro_cov=np.eye(3) * 0.003**2,
        child_gyro_cov=np.eye(3) * 0.003**2,
        parent_gyro=energetic_rate,
        child_gyro=energetic_rate,
        parent_lever_bound_m=noise_levers[0],
        child_lever_bound_m=noise_levers[1],
        sample_period_s=1.0 / float(config["sampling"]["working_rate_hz"]),
    )
    systematic = summarize_held_out_covariance_blocks([{
        "edge": "shoulder_right",
        "action": "energetic_systematic_model_error_mutation",
        "covariance_block_id": "systematic_half_mps2",
        "normalized": np.full(90, 0.5 / propagated_sigma),
    }], float(config["acceptance"]["maximum_held_out_joint_center_nrmse"]))
    controls.append({
        "name": "energetic_motion_systematic_joint_error_not_normalized_away",
        "rejected": bool(systematic["pass"] is False),
        "diagnostic": {
            "systematic_error_mps2": 0.5,
            "propagated_sensor_noise_sigma_mps2": propagated_sigma,
            "normalized_nrmse": systematic["maximum_individual_block_nrmse"],
            "noise_propagation": propagation,
            "lever_bounds_m": noise_levers,
            "lever_bounds_depend_on_truth_fit_or_residual": False,
        },
    })
    controls.append(_expect_rejection("per_action_stitching", lambda: validate_progressive_snapshot({
        **base_snapshot, "per_action_profile_count": 1,
    })))
    controls.append(_expect_rejection("fake_raw_row_progress", lambda: validate_progressive_snapshot({
        **base_snapshot, "raw_row_count_used_as_progress": True,
    })))
    controls.append(_expect_rejection("initial_still_false_completion", lambda: validate_progressive_snapshot({
        **base_snapshot, "ready": True,
    })))
    leaked_episode = replace(
        base_truth.episodes[0],
        audit={**base_truth.episodes[0].audit, "estimator_module_imported": True},
    )
    controls.append(_expect_rejection("leaked_truth", lambda: validate_independent_synthetic_truth(
        replace(base_truth, episodes=(leaked_episode, *base_truth.episodes[1:])),
    )))
    robustified = deepcopy(config)
    robustified["hard_feasibility"]["physical_gate_is_optimizer_loss"] = True
    controls.append(_expect_rejection("robustified_physical_priors", lambda: validate_basis_contract(
        robustified, geometry,
    )))
    collapsed = dict(geometry.segments)
    collapsed["shank_left"] = replace(
        collapsed["shank_left"], value_m=0.0, lower_m=0.0,
    )
    controls.append(_expect_rejection("collapsed_geometry", lambda: replace(
        geometry, segments=collapsed,
    ).validate()))
    wrong_capture = replace(base_truth.episodes[0], capture="SYNTHETIC_OTHER_CAPTURE")
    profile = ProgressiveProfile("mutation-profile", base_truth.episodes[0].capture)
    controls.append(_expect_rejection("cross_capture_sharing", lambda: profile.add_episode(
        wrong_capture, functional_bank.selected, geometry, config, seed=1,
    )))

    tilted = dict(functional_bank.selected.body_from_sensor_by_segment)
    sagittal = Rotation.from_rotvec([0.0, math.radians(15.0), 0.0]).as_matrix()
    tilted["thigh_left"] = sagittal @ tilted["thigh_left"]
    tilted["shank_left"] = sagittal @ tilted["shank_left"]
    mutated_functional = replace(
        functional_bank.selected, body_from_sensor_by_segment=tilted,
    )
    mutated_objective = C2CalibrationObjective(
        objective.bundle, geometry, mutated_functional,
    )
    truth_state = np.r_[base_truth.headings_rad, base_truth.axial_offsets_m]
    physical = mutated_objective.feasibility_report(truth_state, config)
    controls.append({
        "name": "front_back_knee_split_with_valid_lr_order",
        "rejected": bool(
            physical["gates"]["left_right_order"]
            and not physical["gates"]["knee_front_back_split"]
            and not physical["pass"]
        ),
        "diagnostic": {
            "left_right_order": physical["gates"]["left_right_order"],
            "knee_front_back_split_gate": physical["gates"]["knee_front_back_split"],
            "knee_front_back_split_m": physical["knee_front_back_split_m"],
        },
    })
    return controls


def default_action_permutation(seed: int = 8123) -> tuple[str, ...]:
    rng = np.random.default_rng(seed)
    middle = list(TRUTH_EPISODES[1:-1])
    rng.shuffle(middle)
    return (TRUTH_EPISODES[0], *middle, TRUTH_EPISODES[-1])


def run_synthetic_qualification(
    geometry: BodyGeometry, config: Mapping[str, Any],
) -> dict[str, Any]:
    started = time.monotonic()
    cases = []
    objectives = []
    banks = []
    case_specs: Sequence[tuple[str, SyntheticTruth]] = (
        ("randomized_mounts_seed_20260829", generate_synthetic_truth(seed=20260829)),
        ("randomized_mounts_seed_20260830", generate_synthetic_truth(seed=20260830)),
        ("action_order_permutation", generate_synthetic_truth(
            seed=20260829, episode_order=default_action_permutation(),
        )),
    )
    for index, (name, truth) in enumerate(case_specs):
        report, objective, bank = qualify_case(
            name, truth, geometry, config, seed=9200 + index,
        )
        cases.append(report); objectives.append(objective); banks.append(bank)

    degenerate_truth = generate_synthetic_truth(
        seed=20260829, common_translation_excitation=False,
    )
    try:
        degenerate, _, _ = qualify_case(
            "negative_degenerate_no_common_translation",
            degenerate_truth, geometry, config, seed=9300,
        )
        degenerate_rejected = not degenerate["pass"]
        degenerate_diagnostic: Any = degenerate
    except (ValueError, RuntimeError) as exc:
        degenerate_rejected = True
        degenerate_diagnostic = {"structural_stop": str(exc)}

    controls = mutation_controls(
        case_specs[0][1], objectives[0], banks[0], geometry, config,
    )
    gates = {
        "all_positive_cases_pass": all(case["pass"] for case in cases),
        "action_order_permutation_passes": cases[2]["pass"],
        "degenerate_case_rejects": degenerate_rejected,
        "all_mutations_reject": all(control["rejected"] for control in controls),
    }
    return {
        "schema": "biospur-c2-independent-synthetic-qualification-v1",
        "basis_contract": validate_basis_contract(config, geometry),
        "cases": cases,
        "degenerate_case": degenerate_diagnostic,
        "mutation_controls": controls,
        "gates": gates,
        "pass": all(gates.values()),
        "elapsed_s": time.monotonic() - started,
    }
