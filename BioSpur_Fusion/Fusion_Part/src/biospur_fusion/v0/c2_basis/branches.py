"""Causal functional mount and sign branch management for one C2 profile."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

import numpy as np
from scipy.spatial.transform import Rotation

from biospur_fusion.v0.raw6_heading import Raw6Episode

from .functional import (
    FunctionalCandidate,
    HingeAxisEstimate,
    estimate_hinge_baselines,
    functional_candidates,
)
from .mounts import candidate_bank
from .staging import BoundedStage, pipeline_wall_limit


@dataclass(frozen=True)
class FunctionalCandidateBank:
    hinge_axes: Mapping[str, HingeAxisEstimate]
    retained: tuple[FunctionalCandidate, ...]
    rejected: tuple[Mapping[str, Any], ...]
    selected: FunctionalCandidate
    stage_audit: Mapping[str, Any] = field(default_factory=dict)

    def audit(self) -> dict[str, Any]:
        spread_by_segment = {
            segment: max((float(np.degrees(Rotation.from_matrix(
                candidate.body_from_sensor_by_segment[segment]
                @ self.selected.body_from_sensor_by_segment[segment].T
            ).magnitude())) for candidate in self.retained), default=0.0)
            for segment in self.selected.body_from_sensor_by_segment
        }
        numerical_conflicts = sorted({
            conflict
            for estimate in self.hinge_axes.values()
            for conflict in estimate.qmt_axis_report.get("input_degeneracy", {}).get(
                "named_numerical_physical_conflicts", []
            )
        })
        qmt_input_degeneracy = {
            edge: estimate.qmt_axis_report.get("input_degeneracy", {})
            for edge, estimate in self.hinge_axes.items()
        }
        return {
            "hinge_edges_available": sorted(self.hinge_axes),
            "retained_candidate_count": len(self.retained),
            "rejected_candidate_count": len(self.rejected),
            "selected_candidate_id": self.selected.candidate_id,
            "mount_branch_spread_deg_by_segment": spread_by_segment,
            "maximum_mount_branch_spread_deg": max(spread_by_segment.values(), default=0.0),
            "future_episode_access": False,
            "manual_pose_truth_factor": False,
            "qualitative_wear_is_exact_vector": False,
            "named_numerical_physical_conflicts": numerical_conflicts,
            "qmt_input_degeneracy_by_edge": qmt_input_degeneracy,
            "bounded_stage": dict(self.stage_audit),
        }


def build_functional_candidate_bank(
    episodes: Sequence[Raw6Episode],
    node_to_segment: Mapping[str, str],
    config: Mapping[str, Any],
    *,
    previous_axes: Mapping[str, HingeAxisEstimate] | None = None,
    deadline: BoundedStage | None = None,
    budget_mode: str = "FULL",
) -> FunctionalCandidateBank:
    """Estimate only axes observable in this cumulative chronological prefix."""

    stage = deadline or BoundedStage(
        "FUNCTIONAL_CANDIDATE_BANK_CONSTRUCTION",
        pipeline_wall_limit(config, budget_mode, "functional_candidate_bank"),
    )
    axes = estimate_hinge_baselines(
        episodes, config, previous=previous_axes, deadline=stage,
    )
    maximum_correction = float(config["wear_direction"]["maximum_final_mount_correction_deg"])
    maximum_retained = int(config["wear_direction"]["maximum_retained_functional_candidates"])
    legal: list[FunctionalCandidate] = []
    rejected: list[Mapping[str, Any]] = []
    seen: set[bytes] = set()
    for mount_branch in candidate_bank():
        stage.checkpoint(
            "mount_branch_begin", candidate_id=mount_branch.branch_id,
        )
        if not mount_branch.metadata_gate["hard_pass"]:
            rejected.append({
                "candidate_id": mount_branch.branch_id,
                "reason": "IMMUTABLE_WEAR_METADATA_GATE",
            })
            continue
        for candidate in functional_candidates(
            mount_branch, axes, node_to_segment, maximum_candidates=6,
        ):
            key_parts = [
                np.round(candidate.body_from_sensor_by_segment[name], 8).tobytes()
                for name in sorted(candidate.body_from_sensor_by_segment)
            ]
            key_parts.append(repr(sorted(candidate.axis_signs.items())).encode())
            key = b"".join(key_parts)
            if key in seen:
                continue
            seen.add(key)
            correction = max(candidate.correction_angles_deg.values(), default=0.0)
            if not candidate.wear_report["hard_pass"]:
                rejected.append({
                    "candidate_id": candidate.candidate_id,
                    "reason": "POST_FUNCTIONAL_WEAR_METADATA_GATE",
                })
            elif correction > maximum_correction:
                rejected.append({
                    "candidate_id": candidate.candidate_id,
                    "reason": "FUNCTIONAL_CORRECTION_BOUND",
                    "maximum_correction_deg": correction,
                })
            else:
                legal.append(candidate)
    legal.sort(key=lambda candidate: (
        max(candidate.remaining_axis_misalignment_deg.values(), default=0.0),
        candidate.wear_report["soft_cost"],
        max(candidate.correction_angles_deg.values(), default=0.0),
        candidate.candidate_id,
    ))
    retained = tuple(legal[:maximum_retained])
    if not retained:
        raise RuntimeError("functional calibration produced no legal mount/sign candidate")
    stage.checkpoint(
        "candidate_bank_complete", retained=len(retained), rejected=len(rejected),
    )
    return FunctionalCandidateBank(
        axes, retained, tuple(rejected), retained[0], stage.report(),
    )
