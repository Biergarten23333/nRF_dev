"""C2-only functional selection, progressive updates, and fresh batch audit."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, replace
import math
import time
from typing import Any, Callable, Mapping, Sequence

import numpy as np
from scipy.spatial.transform import Rotation

from biospur_fusion.v0.raw6_heading import Raw6Episode

from .branches import FunctionalCandidateBank, build_functional_candidate_bank
from .contracts import CAPTURE_ID, C2_IDENTITY, EPISODE_SELECTION, StageBudget, stage_budget
from .factors import FactorBundle, build_factor_bundle
from .functional import (
    CenterRefinement,
    FunctionalCandidate,
    functional_candidates,
    profile_joint_centers,
    refine_candidate_from_joint_centers,
)
from .geometry import BodyGeometry
from .model import C2CalibrationObjective
from .mounts import candidate_bank
from .progressive import ProgressiveProfile
from .solver import compare_states, information_report, solve_staged
from .validation import validate_progressive_snapshot
from .staging import BoundedStage, pipeline_wall_limit


@dataclass(frozen=True)
class FunctionalSelection:
    candidate: FunctionalCandidate
    seed_state: np.ndarray | None
    audit: Mapping[str, Any]


@dataclass(frozen=True)
class CalibrationExecution:
    progressive_profile: ProgressiveProfile
    progressive_functional: FunctionalSelection
    progressive_objective: C2CalibrationObjective
    batch_functional: FunctionalSelection
    batch_objective: C2CalibrationObjective
    batch_result: Mapping[str, Any]
    comparison: Mapping[str, Any]
    report: Mapping[str, Any]


def _budgets(config: Mapping[str, Any], mode: str) -> dict[str, StageBudget]:
    if mode == "FULL":
        return {
            name: stage_budget(config, name)
            for name in (
                "B_FUNCTIONAL_MOUNTS_CONNECTIONS",
                "C_NINE_RELATIVE_HEADINGS",
                "D_ALL_ACTION_REFINEMENT",
            )
        }
    if mode == "BATCH":
        result = _budgets(config, "FULL")
        batch = stage_budget(config, "FRESH_CUMULATIVE_BATCH")
        result["D_ALL_ACTION_REFINEMENT"] = batch
        return result
    if mode == "PROGRESSIVE":
        row = stage_budget(config, "PROGRESSIVE_PREFIX_UPDATE")
        return {
            "B_FUNCTIONAL_MOUNTS_CONNECTIONS": StageBudget(
                row.name, row.max_iterations, row.wall_limit_s / 3.0, 1, 1,
            ),
            "C_NINE_RELATIVE_HEADINGS": StageBudget(
                row.name, row.max_iterations, row.wall_limit_s / 3.0,
                max(4, row.multistarts), 1,
            ),
            "D_ALL_ACTION_REFINEMENT": StageBudget(
                row.name, row.max_iterations, row.wall_limit_s / 3.0, 2,
                min(2, row.parallel_workers),
            ),
        }
    raise ValueError(mode)


def _mount_spread(
    selected: FunctionalCandidate, alternatives: Sequence[FunctionalCandidate],
) -> tuple[dict[str, float], float]:
    spread = {
        segment: max((float(np.degrees(Rotation.from_matrix(
            candidate.body_from_sensor_by_segment[segment]
            @ selected.body_from_sensor_by_segment[segment].T
        ).magnitude())) for candidate in alternatives), default=0.0)
        for segment in selected.body_from_sensor_by_segment
    }
    return spread, max(spread.values(), default=0.0)


def unrefined_functional_selection(bank: FunctionalCandidateBank) -> FunctionalSelection:
    audit = bank.audit()
    return FunctionalSelection(bank.selected, None, {
        **audit,
        "center_refined": False,
        "selection_uses_held_out": False,
    })


def center_refined_functional_selection(
    episodes: Sequence[Raw6Episode],
    geometry: BodyGeometry,
    config: Mapping[str, Any],
    *,
    seed: int,
    budget_mode: str,
    previous_axes: Mapping[str, Any] | None = None,
    prepared_bundle: FactorBundle | None = None,
) -> tuple[FunctionalSelection, FactorBundle, C2CalibrationObjective]:
    """Profile centers once, then evaluate all legal mount/sign hypotheses."""

    pipeline_audit: dict[str, Any] = {}
    if prepared_bundle is None:
        factor_stage = BoundedStage(
            "FACTOR_CONSTRUCTION",
            pipeline_wall_limit(config, budget_mode, "factor_construction"),
        )
        bundle = build_factor_bundle(
            episodes, config, deadline=factor_stage, budget_mode=budget_mode,
        )
        pipeline_audit["factor_construction"] = factor_stage.report()
    else:
        if tuple(prepared_bundle.episode_order) != tuple(
            episode.action for episode in episodes
        ):
            raise ValueError("prepared cumulative factors do not match episode prefix")
        bundle = prepared_bundle
        pipeline_audit["factor_construction"] = {
            "mode": "REUSE_PERSISTENT_INCREMENTAL_FACTOR_STATE",
            "future_episode_access": False,
            "complete": True,
            "source": prepared_bundle.construction_audit,
        }
    functional_stage = BoundedStage(
        "FUNCTIONAL_CANDIDATE_BANK_CONSTRUCTION",
        pipeline_wall_limit(config, budget_mode, "functional_candidate_bank"),
    )
    initial_bank = build_functional_candidate_bank(
        episodes,
        C2_IDENTITY,
        config,
        previous_axes=previous_axes,
        deadline=functional_stage,
        budget_mode=budget_mode,
    )
    pipeline_audit["functional_candidate_bank"] = functional_stage.report()
    objective_stage = BoundedStage(
        "OBJECTIVE_CONSTRUCTION",
        pipeline_wall_limit(config, budget_mode, "objective_construction"),
    )
    initial_objective = objective_stage.run(
        "prepare_initial_functional_objective",
        lambda: C2CalibrationObjective(bundle, geometry, initial_bank.selected),
    )
    pipeline_audit["objective_construction"] = objective_stage.report()
    initial_solve = solve_staged(
        initial_objective, _budgets(config, budget_mode), config, seed=seed,
    )
    profiled_centers = profile_joint_centers(bundle, initial_solve["state"])
    input_candidates: list[FunctionalCandidate] = []
    for mount_branch in candidate_bank():
        if mount_branch.metadata_gate["hard_pass"]:
            input_candidates.extend(functional_candidates(
                mount_branch,
                initial_bank.hinge_axes,
                C2_IDENTITY,
                maximum_candidates=256,
                alignment_fractions=(0.0,),
            ))
    attempts: Counter[str] = Counter()
    rows: list[tuple[float, CenterRefinement, C2CalibrationObjective, int]] = []
    center_started = time.monotonic()
    center_wall_limit_s = float(
        config["stages"]["B_FUNCTIONAL_MOUNTS_CONNECTIONS"]["wall_limit_s"]
    )
    center_trace = []
    refinement_cache: dict[tuple[Any, ...], CenterRefinement | str] = {}
    score_cache: dict[tuple[Any, ...], tuple[float, int]] = {}
    score_cache_hits = 0
    for candidate in input_candidates:
        if time.monotonic() - center_started > center_wall_limit_s:
            attempted = sum(attempts.values())
            raise RuntimeError(
                f"functional center hypothesis enumeration exceeded "
                f"{center_wall_limit_s:.1f}s; attempted={attempted}; "
                f"finite_legal={len(rows)}; refinement_cache_entries="
                f"{len(refinement_cache)}; unique_score_evaluations="
                f"{len(score_cache)}; score_cache_hits={score_cache_hits}; "
                f"trace_tail={center_trace[-3:]}"
            )
        try:
            qualified_signs = tuple(sorted(
                (edge, endpoint, candidate.axis_signs[edge][index])
                for edge, estimate in candidate.hinge_axes.items()
                for index, endpoint in enumerate(("parent", "child"))
                if bool(estimate.qmt_axis_report[
                    f"{endpoint}_selected_excitation_qualified"
                ])
            ))
            cache_key = (candidate.base_mount_branch, qualified_signs)
            cached = refinement_cache.get(cache_key)
            if isinstance(cached, str):
                raise RuntimeError(cached)
            if cached is None:
                try:
                    cached = refine_candidate_from_joint_centers(
                        candidate,
                        bundle,
                        geometry,
                        initial_solve["state"],
                        C2_IDENTITY,
                        config,
                        profiled_centers=profiled_centers,
                    )
                    refinement_cache[cache_key] = cached
                except (RuntimeError, ValueError) as exc:
                    refinement_cache[cache_key] = str(exc)
                    raise
            rebound_candidate = replace(
                cached.candidate,
                candidate_id=candidate.candidate_id + "_CENTER_PROFILED",
                axis_signs=candidate.axis_signs,
            )
            refinement = replace(cached, candidate=rebound_candidate)
            state = np.asarray(initial_solve["state"], dtype=float).copy()
            state[9:] = refinement.axial_offset_seed_m
            objective = initial_objective.rebind_functional(refinement.candidate)
            cached_score = score_cache.get(cache_key)
            if cached_score is None:
                train_residual = objective.measurement_residual(state, "train")
                scalar_rows = max(1, len(train_residual))
                score = float(np.sum(np.sqrt(1.0 + train_residual ** 2) - 1.0))
                score_cache[cache_key] = (score, scalar_rows)
            else:
                score, scalar_rows = cached_score
                score_cache_hits += 1
            if not np.isfinite(score):
                attempts["NONFINITE_TRAIN_SCORE"] += 1
                continue
            rows.append((float(score), refinement, objective, scalar_rows))
            attempts["PASS"] += 1
        except (RuntimeError, ValueError) as exc:
            attempts[str(exc)] += 1
        attempted = sum(attempts.values())
        if attempted == 1 or attempted % 128 == 0:
            center_trace.append({
                "attempted": attempted,
                "finite_legal": len(rows),
                "elapsed_s": time.monotonic() - center_started,
                "refinement_cache_entries": len(refinement_cache),
                "unique_score_evaluations": len(score_cache),
                "score_cache_hits": score_cache_hits,
            })
    if not rows:
        raise RuntimeError(f"all center-derived functional hypotheses rejected: {dict(attempts)}")
    rows.sort(key=lambda row: (
        row[0],
        max(row[1].candidate.correction_angles_deg.values(), default=0.0),
        row[1].candidate.wear_report["soft_cost"],
        row[1].candidate.candidate_id,
    ))
    best_score, best_refinement, best_objective, _ = rows[0]
    minimum_relative_likelihood = float(config["wear_direction"][
        "functional_branch_minimum_relative_likelihood"
    ])
    if not 0.0 < minimum_relative_likelihood < 1.0:
        raise ValueError("functional branch minimum relative likelihood must be in (0,1)")
    retention_delta = -math.log(minimum_relative_likelihood)
    retained_rows = [row for row in rows if row[0] <= best_score + retention_delta]
    retained_rows = retained_rows[: int(config["wear_direction"]["maximum_retained_functional_candidates"])]
    retained = [row[1].candidate for row in retained_rows]
    spread, maximum_spread = _mount_spread(best_refinement.candidate, retained)
    seed_state = np.asarray(initial_solve["state"], dtype=float).copy()
    seed_state[9:] = best_refinement.axial_offset_seed_m
    initial_bank_audit = initial_bank.audit()
    audit = {
        "schema": "biospur-c2-functional-hypothesis-selection-v1",
        "initial_functional_candidate_bank": initial_bank_audit,
        "named_numerical_physical_conflicts": initial_bank_audit[
            "named_numerical_physical_conflicts"
        ],
        "qmt_input_degeneracy_by_edge": initial_bank_audit[
            "qmt_input_degeneracy_by_edge"
        ],
        "center_refined": True,
        "input_hypothesis_count": len(input_candidates),
        "finite_legal_hypothesis_count": len(rows),
        "retained_near_optimal_count": len(retained_rows),
        "minimum_relative_likelihood": minimum_relative_likelihood,
        "retention_total_soft_l1_delta": retention_delta,
        "selected_candidate_id": best_refinement.candidate.candidate_id,
        "selected_train_soft_l1_total": best_score,
        "retained": [{
            "candidate_id": row[1].candidate.candidate_id,
            "train_soft_l1_total": row[0],
            "relative_likelihood_upper_bound": float(np.exp(-(row[0] - best_score))),
            "maximum_mount_correction_deg": max(
                row[1].candidate.correction_angles_deg.values(), default=0.0,
            ),
        } for row in retained_rows],
        "rejection_diagnostics": dict(attempts),
        "hypothesis_enumeration_wall_limit_s": center_wall_limit_s,
        "hypothesis_enumeration_elapsed_s": time.monotonic() - center_started,
        "hypothesis_enumeration_trace": center_trace,
        "immutable_factor_preparation_shared_across_hypotheses": True,
        "center_refinement_cache": {
            "input_hypotheses": len(input_candidates),
            "unique_excitation_observable_hypotheses": len(refinement_cache),
            "unique_train_score_evaluations": len(score_cache),
            "exact_train_score_cache_hits": score_cache_hits,
            "unobservable_sign_hypotheses_retained_with_equal_score": True,
            "cache_key_includes_every_excitation_observable_axis_sign": True,
            "hinge_soft_l1_is_invariant_to_axis_sign_at_fixed_mount": True,
        },
        "mount_branch_spread_deg_by_segment": spread,
        "maximum_mount_branch_spread_deg": maximum_spread,
        "center_refinement": best_refinement.report,
        "initial_stage_summary": [{
            key: value for key, value in stage.items()
            if key not in {"state", "runs", "edge_runs"}
        } for stage in initial_solve["stages"]],
        "selection_uses_held_out": False,
        "manual_pose_truth_factor": False,
        "action_label_metric_pose_truth_factor": False,
        "bounded_pipeline_stages": pipeline_audit,
        "held_out_rows_evaluated_during_functional_selection": False,
    }
    return FunctionalSelection(best_refinement.candidate, seed_state, audit), bundle, best_objective


def _mount_difference_deg(left: FunctionalCandidate, right: FunctionalCandidate) -> dict[str, float]:
    return {
        segment: float(np.degrees(Rotation.from_matrix(
            left.body_from_sensor_by_segment[segment]
            @ right.body_from_sensor_by_segment[segment].T
        ).magnitude()))
        for segment in left.body_from_sensor_by_segment
    }


def execute_progressive_and_batch(
    episodes: Sequence[Raw6Episode],
    geometry: BodyGeometry,
    config: Mapping[str, Any],
    *,
    seed: int = 20260829,
    progress_callback: Callable[[Mapping[str, Any]], None] | None = None,
) -> CalibrationExecution:
    if tuple(episode.action for episode in episodes) != tuple(row[0] for row in EPISODE_SELECTION):
        raise ValueError("real execution order differs from sealed C2 chronology")
    profile = ProgressiveProfile("C2_PERSISTENT_PROFILE_V1", "CAPTURE2")
    current_selection: FunctionalSelection | None = None
    previous_axes: Mapping[str, Any] | None = None
    refinement_steps = {11, 16, len(episodes)}
    for index, episode in enumerate(episodes, start=1):
        print(
            f"STAGE progressive prefix {index:02d}/{len(episodes)} "
            f"add {episode.action} begin",
            flush=True,
        )
        prefix = tuple(episodes[:index])
        functional_mode = "FULL" if index == len(episodes) else "PROGRESSIVE"
        functional_stage = BoundedStage(
            "FUNCTIONAL_CANDIDATE_BANK_CONSTRUCTION",
            pipeline_wall_limit(
                config, functional_mode, "functional_candidate_bank",
            ),
        )
        try:
            bank = build_functional_candidate_bank(
                prefix,
                C2_IDENTITY,
                config,
                previous_axes=previous_axes,
                deadline=functional_stage,
                budget_mode=functional_mode,
            )
            previous_axes = bank.hinge_axes
            current_selection = unrefined_functional_selection(bank)
        except (RuntimeError, ValueError) as exc:
            if current_selection is None:
                raise
            timeout_conflict = (
                f"{episode.action}:FUNCTIONAL_CANDIDATE_BANK_FAILED:"
                f"{type(exc).__name__}"
            )
            current_selection = FunctionalSelection(
                current_selection.candidate,
                current_selection.seed_state,
                {
                    **current_selection.audit,
                    "bounded_stage": functional_stage.report(complete=False),
                    "functional_bank_failure": str(exc),
                    "named_numerical_physical_conflicts": sorted(set((
                        *current_selection.audit.get(
                            "named_numerical_physical_conflicts", [],
                        ),
                        timeout_conflict,
                    ))),
                    "previous_functional_candidate_retained": True,
                    "failed_episode_deleted_or_downweighted": False,
                },
            )
        if previous_axes is not None and len(previous_axes) == 4 and index in refinement_steps:
            try:
                factor_preparation = profile.prepare_episode_factors(
                    episode,
                    config,
                    budget_mode=(
                        "FULL" if index == len(episodes) else "PROGRESSIVE"
                    ),
                )
                current_selection, _, _ = center_refined_functional_selection(
                    prefix, geometry, config, seed=seed + 1000 + index,
                    budget_mode=("FULL" if index == len(episodes) else "PROGRESSIVE"),
                    previous_axes=previous_axes,
                    prepared_bundle=profile.factor_bundle,
                )
                current_selection = FunctionalSelection(
                    current_selection.candidate,
                    current_selection.seed_state,
                    {
                        **current_selection.audit,
                        "progressive_factor_preparation": factor_preparation,
                    },
                )
            except (RuntimeError, ValueError) as exc:
                current_selection = FunctionalSelection(
                    current_selection.candidate,
                    None,
                    {**current_selection.audit, "center_refined": False,
                     "center_refinement_failure": str(exc),
                     "selection_uses_held_out": False},
                )
        snapshot = profile.add_episode(
            episode,
            current_selection.candidate,
            geometry,
            config,
            seed=seed + index,
            functional_branch_audit=current_selection.audit,
            final_full_update=index == len(episodes),
        )
        validate_progressive_snapshot(snapshot)
        if progress_callback is not None:
            progress_callback({
                "event": "PROGRESSIVE_SNAPSHOT_COMMITTED",
                "snapshot": snapshot,
            })
        print(
            f"STAGE progressive prefix {index:02d}/{len(episodes)} complete "
            f"ready={snapshot['ready']} readiness={snapshot['readiness_percent']:.2f}",
            flush=True,
        )
    if current_selection is None or profile.state is None:
        raise RuntimeError("progressive C2 profile did not produce a finite final state")
    if profile.factor_bundle is None:
        raise RuntimeError("persistent progressive factor state is absent")
    progressive_bundle = profile.factor_bundle
    progressive_objective_stage = BoundedStage(
        "OBJECTIVE_CONSTRUCTION",
        pipeline_wall_limit(config, "FULL", "objective_construction"),
    )
    progressive_objective = progressive_objective_stage.run(
        "prepare_progressive_final_objective",
        lambda: C2CalibrationObjective(
            progressive_bundle, geometry, current_selection.candidate,
        ),
    )

    print("STAGE fresh cumulative all-episode batch begin", flush=True)
    if progress_callback is not None:
        progress_callback({
            "event": "FRESH_CUMULATIVE_BATCH_BEGIN",
            "progressive_final_snapshot": profile.snapshots[-1],
        })
    try:
        batch_selection, batch_bundle, batch_objective = center_refined_functional_selection(
            episodes, geometry, config, seed=seed + 5000, budget_mode="BATCH",
        )
        batch_result = solve_staged(
            batch_objective,
            _budgets(config, "BATCH"),
            config,
            previous_state=batch_selection.seed_state,
            seed=seed + 6000,
        )
    except Exception as exc:
        if progress_callback is not None:
            progress_callback({
                "event": "FRESH_CUMULATIVE_BATCH_FAILED",
                "exception_type": type(exc).__name__,
                "exception": str(exc),
                "progressive_final_snapshot": profile.snapshots[-1],
            })
        raise
    print("STAGE fresh cumulative all-episode batch complete", flush=True)
    if progress_callback is not None:
        progress_callback({
            "event": "FRESH_CUMULATIVE_BATCH_COMPLETE",
            "state": np.asarray(batch_result["state"]).tolist(),
        })
    state_comparison = compare_states(profile.state, np.asarray(batch_result["state"]))
    mount_difference = _mount_difference_deg(
        current_selection.candidate, batch_selection.candidate,
    )
    acceptance = config["acceptance"]
    comparison = {
        **state_comparison,
        "mount_difference_deg_by_segment": mount_difference,
        "maximum_mount_difference_deg": max(mount_difference.values(), default=0.0),
        "heading_tolerance_deg": acceptance["maximum_progressive_batch_heading_difference_deg"],
        "mount_tolerance_deg": acceptance["maximum_progressive_batch_mount_difference_deg"],
    }
    comparison["pass"] = bool(
        comparison["maximum_heading_difference_deg"]
        <= float(comparison["heading_tolerance_deg"])
        and comparison["maximum_mount_difference_deg"]
        <= float(comparison["mount_tolerance_deg"])
    )
    batch_held_stage = BoundedStage(
        "HELD_OUT_EVALUATION",
        pipeline_wall_limit(config, "BATCH", "held_out_evaluation"),
    )
    information = batch_held_stage.run(
        "information_report",
        lambda: information_report(
            batch_objective, np.asarray(batch_result["state"]),
        ),
    )
    held = batch_objective.held_out_report(
        np.asarray(batch_result["state"]),
        float(acceptance["maximum_held_out_joint_center_nrmse"]),
        deadline=batch_held_stage,
    )
    batch_functional_conflicts = list(batch_selection.audit.get(
        "named_numerical_physical_conflicts", [],
    ))
    batch_sensor_conflicts = list(held["named_conflicts"])
    held = {
        **held,
        "sensor_prediction_named_conflicts": batch_sensor_conflicts,
        "named_numerical_physical_conflicts": batch_functional_conflicts,
        "named_conflicts": sorted(set((
            *batch_sensor_conflicts, *batch_functional_conflicts,
        ))),
        "pass": bool(held["pass"] and not batch_functional_conflicts),
    }
    report = {
        "schema": "biospur-c2-progressive-plus-fresh-batch-v1",
        "capture_id": CAPTURE_ID,
        "episode_count": len(episodes),
        "single_persistent_profile": True,
        "progressive_snapshots": profile.snapshots,
        "progressive_final_functional": current_selection.audit,
        "fresh_batch_functional": batch_selection.audit,
        "fresh_batch": {
            "state": np.asarray(batch_result["state"]).tolist(),
            "information": information,
            "held_out": held,
            "feasibility": {
                key: value for key, value in batch_result["feasibility"].items()
                if key != "kinematics"
            },
            "costs": batch_result["costs"],
            "stages": [{
                key: value for key, value in stage.items()
                if key not in {"state", "runs", "edge_runs"}
            } for stage in batch_result["stages"]],
            "bounded_pipeline_stages": {
                "progressive_final_objective": progressive_objective_stage.report(),
                "held_out_evaluation": batch_held_stage.report(),
                **batch_selection.audit.get("bounded_pipeline_stages", {}),
            },
        },
        "progressive_batch_comparison": comparison,
        "held_out_blocks_used_to_fit": False,
    }
    return CalibrationExecution(
        profile,
        current_selection,
        progressive_objective,
        batch_selection,
        batch_objective,
        batch_result,
        comparison,
        report,
    )
