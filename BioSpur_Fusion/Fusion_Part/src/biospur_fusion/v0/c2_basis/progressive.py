"""One persistent chronological C2 profile with posterior-based readiness."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

import numpy as np

from biospur_fusion.v0.raw6_heading import Raw6Episode

from .contracts import StageBudget
from .factors import FactorBundle, append_factor_bundle, build_factor_bundle, factor_identity
from .functional import FunctionalCandidate
from .geometry import BodyGeometry
from .model import C2CalibrationObjective
from .solver import information_report, solve_staged
from .staging import BoundedStage, pipeline_wall_limit


@dataclass
class ProgressiveProfile:
    profile_id: str
    capture_id: str
    episodes: list[Raw6Episode] = field(default_factory=list)
    state: np.ndarray | None = None
    snapshots: list[dict[str, Any]] = field(default_factory=list)
    factor_bundle: FactorBundle | None = None

    def prepare_episode_factors(
        self,
        episode: Raw6Episode,
        config: Mapping[str, Any],
        *,
        budget_mode: str,
    ) -> Mapping[str, Any]:
        """Prepare only the next chronological episode for functional refinement."""

        expected = tuple(item.action for item in self.episodes)
        observed = (
            self.factor_bundle.episode_order if self.factor_bundle is not None else ()
        )
        if observed != expected:
            raise ValueError("persistent factor state is not aligned before preparation")
        if episode.action in observed:
            raise ValueError(f"factor episode already retained: {episode.action}")
        stage = BoundedStage(
            "FACTOR_CONSTRUCTION",
            pipeline_wall_limit(config, budget_mode, "factor_construction"),
        )
        increment = build_factor_bundle(
            (episode,), config, deadline=stage, budget_mode=budget_mode,
        )
        self.factor_bundle = append_factor_bundle(self.factor_bundle, increment)
        return {
            "mode": "PREPARED_NEXT_CHRONOLOGICAL_EPISODE",
            "future_episode_access": False,
            "episode": episode.action,
            "stage": stage.report(),
        }

    def add_episode(
        self,
        episode: Raw6Episode,
        functional: FunctionalCandidate,
        geometry: BodyGeometry,
        config: Mapping[str, Any],
        *,
        seed: int,
        functional_branch_audit: Mapping[str, Any] | None = None,
        final_full_update: bool = False,
    ) -> dict[str, Any]:
        if episode.capture != self.capture_id:
            raise ValueError(
                f"cross-capture sharing forbidden: {episode.capture} != {self.capture_id}"
            )
        if any(existing.action == episode.action for existing in self.episodes):
            raise ValueError(f"episode already retained: {episode.action}")
        self.episodes.append(episode)
        branch_audit = dict(functional_branch_audit or {})
        mode = "FULL" if final_full_update else "PROGRESSIVE"
        pipeline_stages: dict[str, Any] = {}
        try:
            completed = (
                len(self.factor_bundle.episode_order)
                if self.factor_bundle is not None else 0
            )
            if completed == len(self.episodes):
                pipeline_stages["factor_construction"] = {
                    "mode": "REUSE_PREPARED_NEXT_EPISODE_FACTORS",
                    "future_episode_access": False,
                    "complete": True,
                    "source": self.factor_bundle.construction_audit,
                }
            else:
                factor_stage = BoundedStage(
                    "FACTOR_CONSTRUCTION",
                    pipeline_wall_limit(config, mode, "factor_construction"),
                )
                for pending_episode in self.episodes[completed:]:
                    increment = build_factor_bundle(
                        (pending_episode,), config, deadline=factor_stage,
                        budget_mode=mode,
                    )
                    self.factor_bundle = append_factor_bundle(self.factor_bundle, increment)
                pipeline_stages["factor_construction"] = factor_stage.report()
            if self.factor_bundle is None:
                raise RuntimeError("progressive factor state was not initialized")
            bundle = self.factor_bundle
            objective_stage = BoundedStage(
                "OBJECTIVE_CONSTRUCTION",
                pipeline_wall_limit(config, mode, "objective_construction"),
            )
            objective = objective_stage.run(
                "prepare_cumulative_objective",
                lambda: C2CalibrationObjective(bundle, geometry, functional),
            )
            pipeline_stages["objective_construction"] = objective_stage.report()
        except (RuntimeError, ValueError) as exc:
            snapshot = self._failed_snapshot(
                episode, functional, branch_audit, "FACTOR_OR_OBJECTIVE", exc,
                pipeline_stages,
            )
            self.snapshots.append(snapshot)
            return snapshot
        if final_full_update:
            budgets = {
                name: StageBudget(
                    name,
                    int(config["stages"][name]["max_iterations"]),
                    float(config["stages"][name]["wall_limit_s"]),
                    int(config["stages"][name].get("multistarts", 1)),
                    int(config["stages"][name].get("parallel_workers", 1)),
                )
                for name in (
                    "B_FUNCTIONAL_MOUNTS_CONNECTIONS",
                    "C_NINE_RELATIVE_HEADINGS",
                    "D_ALL_ACTION_REFINEMENT",
                )
            }
        else:
            prefix = config["stages"]["PROGRESSIVE_PREFIX_UPDATE"]
            base_budget = StageBudget(
                "PROGRESSIVE_PREFIX_UPDATE",
                int(prefix["max_iterations"]),
                float(prefix["wall_limit_s"]) / 3.0,
                1,
                1,
            )
            budgets = {
                "B_FUNCTIONAL_MOUNTS_CONNECTIONS": base_budget,
                "C_NINE_RELATIVE_HEADINGS": StageBudget(
                    base_budget.name, base_budget.max_iterations,
                    base_budget.wall_limit_s, min(4, int(prefix.get("multistarts", 4))),
                    1,
                ),
                "D_ALL_ACTION_REFINEMENT": StageBudget(
                    base_budget.name, base_budget.max_iterations,
                    base_budget.wall_limit_s, 2,
                    min(2, int(prefix.get("parallel_workers", 2))),
                ),
            }
        try:
            solved = solve_staged(
                objective, budgets, config, previous_state=self.state, seed=seed,
            )
        except (RuntimeError, ValueError, FloatingPointError) as exc:
            snapshot = self._failed_snapshot(
                episode, functional, branch_audit, "BOUNDED_STAGED_SOLVE", exc,
                pipeline_stages,
            )
            self.snapshots.append(snapshot)
            return snapshot
        self.state = np.asarray(solved["state"], dtype=float)
        held_stage = BoundedStage(
            "HELD_OUT_EVALUATION",
            pipeline_wall_limit(config, mode, "held_out_evaluation"),
        )
        try:
            information = held_stage.run(
                "information_report", lambda: information_report(objective, self.state),
            )
            acceptance = config["acceptance"]
            held = objective.held_out_report(
                self.state,
                float(acceptance["maximum_held_out_joint_center_nrmse"]),
                deadline=held_stage,
            )
            sensor_prediction_conflicts = list(held["named_conflicts"])
            functional_conflicts = list(branch_audit.get(
                "named_numerical_physical_conflicts", [],
            ))
            held = {
                **held,
                "sensor_prediction_named_conflicts": sensor_prediction_conflicts,
                "named_numerical_physical_conflicts": functional_conflicts,
                "named_conflicts": sorted(set(
                    (*sensor_prediction_conflicts, *functional_conflicts)
                )),
                "pass": bool(held["pass"] and not functional_conflicts),
            }
            nominal = objective.initial_state().vector()
            nominal_held = objective.costs(
                nominal, held_out_deadline=held_stage,
            )["held_out_sensor_soft_l1"]
            fitted_held = objective.costs(
                self.state, held_out_deadline=held_stage,
            )["held_out_sensor_soft_l1"]
            training_factor_sha = held_stage.run(
                "training_factor_identity", lambda: factor_identity(bundle, "train"),
            )
            held_factor_sha = held_stage.run(
                "held_out_factor_identity", lambda: factor_identity(bundle, "held_out"),
            )
            pipeline_stages["held_out_evaluation"] = held_stage.report()
        except (RuntimeError, ValueError, FloatingPointError) as exc:
            pipeline_stages["held_out_evaluation"] = held_stage.report(complete=False)
            snapshot = self._failed_snapshot(
                episode, functional, branch_audit, "HELD_OUT_EVALUATION", exc,
                pipeline_stages,
            )
            self.snapshots.append(snapshot)
            return snapshot
        improvement = (
            (nominal_held - fitted_held) / nominal_held if nominal_held > 1e-12 else 0.0
        )
        rank_fraction = information["gauge_reduced_heading_rank"] / 9.0
        uncertainty_fraction = min(
            1.0,
            float(acceptance["maximum_heading_posterior_sigma_deg"])
            / max(information["maximum_posterior_heading_sigma_deg"], 1e-9),
        )
        conflict_factor = 1.0 / (1.0 + len(held["named_conflicts"]))
        physical_factor = 1.0 if solved["feasibility"]["pass"] else 0.25
        branch_spread = float(solved["stages"][1]["multistart_max_spread_deg"])
        branch_fraction = min(
            1.0,
            float(acceptance["maximum_multistart_branch_spread_deg"])
            / max(branch_spread, 1e-9),
        )
        mount_branch_spread = float(branch_audit.get(
            "maximum_mount_branch_spread_deg", 180.0,
        ))
        mount_branch_fraction = min(
            1.0,
            float(acceptance["maximum_functional_mount_branch_spread_deg"])
            / max(mount_branch_spread, 1e-9),
        )
        readiness = (
            100.0 * rank_fraction * uncertainty_fraction * branch_fraction
            * mount_branch_fraction * conflict_factor * physical_factor
        )
        if len(self.episodes) == 1:
            readiness = min(readiness, 10.0)
        previous_readiness = (
            self.snapshots[-1]["readiness_percent"] if self.snapshots else None
        )
        if previous_readiness is None:
            assessment = "INITIAL_LOW_INFORMATION"
        elif readiness > previous_readiness + 1.0:
            assessment = "POSTERIOR_INFORMATION_INCREASE"
        elif readiness < previous_readiness - 1.0:
            assessment = "CONFLICT_OR_UNCERTAINTY_DECREASED_READINESS"
        else:
            assessment = "REDUNDANT_OR_CONSISTENT_UPDATE"
        ready = bool(
            len(self.episodes) > 1
            and information["gauge_reduced_heading_rank"] == 9
            and information["maximum_posterior_heading_sigma_deg"] <= float(acceptance["maximum_heading_posterior_sigma_deg"])
            and held["pass"]
            and improvement >= float(acceptance["minimum_held_out_improvement_over_uncalibrated_fraction"])
            and solved["feasibility"]["pass"]
            and branch_spread <= float(acceptance["maximum_multistart_branch_spread_deg"])
            and mount_branch_spread <= float(acceptance["maximum_functional_mount_branch_spread_deg"])
            and functional.wear_report["hard_pass"]
            and max(functional.correction_angles_deg.values(), default=0.0)
            <= float(config["wear_direction"]["maximum_final_mount_correction_deg"])
        )
        snapshot = {
            "schema": "biospur-c2-genuine-progressive-snapshot-v1",
            "profile_id": self.profile_id,
            "capture_id": self.capture_id,
            "step": len(self.episodes),
            "added_episode": episode.action,
            "retained_episodes": [item.action for item in self.episodes],
            "single_persistent_profile": True,
            "per_action_profile_count": 0,
            "state": self.state.tolist(),
            "functional_candidate": {
                "candidate_id": functional.candidate_id,
                "base_mount_branch": functional.base_mount_branch,
                "axis_signs": functional.axis_signs,
                "alignment_fraction": functional.functional_alignment_fraction,
                "correction_angles_deg": functional.correction_angles_deg,
                "remaining_axis_misalignment_deg": functional.remaining_axis_misalignment_deg,
                "wear_hard_pass": functional.wear_report["hard_pass"],
                "branch_audit": branch_audit,
            },
            "training_factor_sha256": training_factor_sha,
            "held_out_factor_sha256": held_factor_sha,
            "information": information,
            "held_out": held,
            "held_out_improvement_over_uncalibrated_fraction": float(improvement),
            "feasibility": {key: value for key, value in solved["feasibility"].items() if key != "kinematics"},
            "solver": {key: value for key, value in solved.items() if key not in {"state", "feasibility"}},
            "readiness_percent": float(readiness),
            "ready": ready,
            "assessment": assessment,
            "raw_row_count_used_as_progress": False,
            "action_count_used_as_readiness": False,
            "initial_still_complete_calibration": False,
            "final_prefix_used_full_stage_budgets": bool(final_full_update),
            "bounded_pipeline_stages": pipeline_stages,
        }
        self.snapshots.append(snapshot)
        return snapshot

    def _failed_snapshot(
        self,
        episode: Raw6Episode,
        functional: FunctionalCandidate,
        branch_audit: Mapping[str, Any],
        stage: str,
        error: Exception,
        pipeline_stages: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        previous_readiness = self.snapshots[-1]["readiness_percent"] if self.snapshots else None
        return {
            "schema": "biospur-c2-genuine-progressive-snapshot-v1",
            "profile_id": self.profile_id,
            "capture_id": self.capture_id,
            "step": len(self.episodes),
            "added_episode": episode.action,
            "retained_episodes": [item.action for item in self.episodes],
            "single_persistent_profile": True,
            "per_action_profile_count": 0,
            "state": None if self.state is None else self.state.tolist(),
            "functional_candidate": {
                "candidate_id": functional.candidate_id,
                "base_mount_branch": functional.base_mount_branch,
                "branch_audit": dict(branch_audit),
            },
            "information": {
                "gauge_reduced_heading_rank": 0,
                "maximum_posterior_heading_sigma_deg": None,
                "diagnostic_not_posterior": True,
            },
            "held_out": {
                "pass": False,
                "named_conflicts": list(branch_audit.get(
                    "named_numerical_physical_conflicts", [],
                )),
                "named_numerical_physical_conflicts": list(branch_audit.get(
                    "named_numerical_physical_conflicts", [],
                )),
            },
            "feasibility": {"pass": False},
            "solver": {
                "failed_stage": stage,
                "diagnostic": str(error),
                "blind_rerun_performed": False,
            },
            "readiness_percent": 0.0,
            "previous_readiness_percent": previous_readiness,
            "ready": False,
            "assessment": "LOW_INFORMATION_OR_STRUCTURAL_CONFLICT_DIAGNOSED",
            "raw_row_count_used_as_progress": False,
            "action_count_used_as_readiness": False,
            "initial_still_complete_calibration": False,
            "bounded_pipeline_stages": dict(pipeline_stages or {}),
        }
