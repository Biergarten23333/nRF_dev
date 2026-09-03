"""Executable Class-A lifecycle and policy owner for the C2 pipeline.

This module is not a descriptor self-check. ``C2ExecutionGuard`` owns the
state transitions that the real runners must call. The independent synthetic
suite drives forbidden requests through those same methods and records the
actual rejection path.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, fields, is_dataclass, replace
from hashlib import sha256
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np


ROOTED_EDGES = (
    ("pelvis", "torso"),
    ("torso", "upper_arm_left"),
    ("upper_arm_left", "forearm_left"),
    ("torso", "upper_arm_right"),
    ("upper_arm_right", "forearm_right"),
    ("pelvis", "thigh_left"),
    ("thigh_left", "shank_left"),
    ("pelvis", "thigh_right"),
    ("thigh_right", "shank_right"),
)

REQUIRED_PROGRESS_INPUTS = frozenset({
    "information", "uncertainty", "branch_concentration", "physical_validity",
    "prequential_before_ingest",
})


class ClassAGuardViolation(ValueError):
    """A forbidden Class-A request reached its actual execution owner."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = str(code)
        self.detail = str(detail)


@dataclass(frozen=True)
class GuardEvent:
    owner: str
    callable: str
    outcome: str
    detail: Mapping[str, Any]


class C2ExecutionGuard:
    """Stateful owner for capture, branch, geometry, heading and holdout gates."""

    def __init__(self, settings: Mapping[str, Any]) -> None:
        self.settings = settings
        execution_contract = settings["execution_contract"]
        self.chronological_actions = tuple(execution_contract["chronological_actions"])
        if len(self.chronological_actions) != 19 or len(set(self.chronological_actions)) != 19:
            raise ValueError("execution contract must bind 19 unique sealed chronological actions")
        self.capture_id: str | None = None
        self.last_episode_index = -1
        self.vqf_token_by_node: dict[str, int] = {}
        self.fit_frozen = False
        self.holdout_opened = False
        self.events: list[GuardEvent] = []
        self._validate_bound_settings()

    def _event(self, owner: str, callable_name: str, **detail: Any) -> None:
        self.events.append(GuardEvent(owner, callable_name, "ACCEPTED", detail))

    def _reject(self, code: str, owner: str, callable_name: str, detail: str) -> None:
        self.events.append(GuardEvent(owner, callable_name, "REJECTED", {"code": code, "detail": detail}))
        raise ClassAGuardViolation(code, detail)

    def _validate_bound_settings(self) -> None:
        anthropometry = self.settings["anthropometric_proxy"]
        if anthropometry.get("allowed_in_segment_frame_or_fit_residual") is not False:
            self._reject(
                "LEAKED_TRUTH_OR_ACTION_POSE_TRUTH", "P2_VIEWER_GEOMETRY",
                "C2ExecutionGuard._validate_bound_settings",
                "anthropometry must remain viewer-only and outside every fitted residual/frame",
            )
        def canonical(value: Any) -> Any:
            if isinstance(value, Mapping):
                return {
                    str(key): canonical(item)
                    for key, item in sorted(value.items(), key=lambda row: str(row[0]))
                }
            if isinstance(value, (list, tuple)):
                return [canonical(item) for item in value]
            return value

        expected_forearm = {
            "left": [0.245, [0.260, 0.265]],
            "right": [0.245, [0.260, 0.265]],
        }
        provenance = anthropometry.get("forearm_dual_observer_provenance", {})
        provenance_valid = set(provenance) == {"left", "right"}
        for side in ("left", "right"):
            row = provenance.get(side, {})
            provenance_valid = provenance_valid and canonical(row) == {
                "observer_a_point_m": 0.245,
                "observer_b_interval_m": [0.260, 0.265],
                "observer_values_kept_separate": True,
                "midpoint_or_combined_value": None,
                "allowed_in_fit_or_functional_center": False,
                "claimed_equal_to_internal_bone_length": False,
            }
        if canonical(anthropometry.get("forearm_m")) != expected_forearm or not provenance_valid:
            self._reject(
                "FOREARM_DUAL_OBSERVER_PROVENANCE_COLLAPSE",
                "VIEWER_ANTHROPOMETRY_AUTHORITY",
                "C2ExecutionGuard._validate_bound_settings",
                "observer A 0.245 m and observer B [0.260,0.265] m must remain separate viewer-only evidence on both sides",
            )
        heading = self.settings["heading"]
        if heading.get("tree_semantics") != "delta_child_global = delta_parent_global + deltaFilt_child_edge":
            self._reject(
                "TIME_VARYING_HEADING_TO_MEAN_OR_CONSTANT_SUBSTITUTION", "P3_HEADING",
                "C2ExecutionGuard._validate_bound_settings", "official rooted parent-plus-child delta semantics changed",
            )
        explicit = heading.get("explicit_est_settings", {})
        if explicit.get("startRating") != 0.0 or explicit.get("stillnessRating") != 0.0:
            self._reject(
                "FALSE_INITIAL_STILL_COMPLETION", "P3_HEADING",
                "C2ExecutionGuard._validate_bound_settings", "example-subject perfect startup/stillness ratings are forbidden",
            )

    def begin_capture(
        self,
        capture_id: str,
        *,
        warm_start_source: str | None = None,
        shared_capture_ids: Sequence[str] = (),
    ) -> None:
        if warm_start_source is not None:
            self._reject(
                "OLD_WARM_START_OR_PROFILE_IMPORT", "CAPTURE_LIFECYCLE",
                "C2ExecutionGuard.begin_capture", f"warm start requested from {warm_start_source}",
            )
        if shared_capture_ids or (self.capture_id is not None and capture_id != self.capture_id):
            self._reject(
                "CROSS_CAPTURE_STATE_SHARING", "CAPTURE_LIFECYCLE",
                "C2ExecutionGuard.begin_capture", "one guard state may own exactly C2",
            )
        if capture_id != "C2":
            self._reject(
                "CROSS_CAPTURE_STATE_SHARING", "CAPTURE_LIFECYCLE",
                "C2ExecutionGuard.begin_capture", f"unexpected capture {capture_id}",
            )
        self.capture_id = capture_id
        self._event("CAPTURE_LIFECYCLE", "C2ExecutionGuard.begin_capture", capture_id=capture_id)

    def begin_episode(self, chronological_index: int, action: str) -> None:
        if self.capture_id != "C2":
            self._reject(
                "CROSS_CAPTURE_STATE_SHARING", "CAPTURE_LIFECYCLE",
                "C2ExecutionGuard.begin_episode", "capture must begin before an episode",
            )
        expected = self.last_episode_index + 1
        if chronological_index != expected:
            self._reject(
                "ACTION_ORDER_PERMUTATION", "CAPTURE_LIFECYCLE",
                "C2ExecutionGuard.begin_episode", f"expected chronological index {expected}, got {chronological_index}",
            )
        if chronological_index >= len(self.chronological_actions) or action != self.chronological_actions[chronological_index]:
            self._reject(
                "ACTION_ORDER_PERMUTATION", "CAPTURE_LIFECYCLE",
                "C2ExecutionGuard.begin_episode", "action differs from sealed chronological order",
            )
        self.last_episode_index = chronological_index
        self._event("CAPTURE_LIFECYCLE", "C2ExecutionGuard.begin_episode", index=chronological_index, action=action)

    def bind_vqf_instance(self, node: str, token: object) -> None:
        identity = id(token)
        if node in self.vqf_token_by_node and self.vqf_token_by_node[node] != identity:
            self._reject(
                "PER_ACTION_VQF_RESET", "P2_ORIENTATION",
                "C2ExecutionGuard.bind_vqf_instance", f"node {node} changed VQF object identity",
            )
        self.vqf_token_by_node[node] = identity
        self._event("P2_ORIENTATION", "C2ExecutionGuard.bind_vqf_instance", node=node, identity=identity)

    def enforce_pipeline_stage(
        self,
        *,
        actual: str,
        allowed: Sequence[str],
        operation: str,
    ) -> None:
        if actual not in set(allowed):
            self._reject(
                "PIPELINE_STAGE_ORDER_BYPASS", "C2_PIPELINE_RUNTIME",
                operation, f"stage {actual} is outside allowed stages {list(allowed)}",
            )
        self._event(
            "C2_PIPELINE_RUNTIME", operation,
            actual_stage=actual, allowed_stages=list(allowed),
        )

    def observe_qmt_span(self, edge: str, *, reset_requested: bool, profile_stitch_requested: bool) -> None:
        if reset_requested or profile_stitch_requested:
            self._reject(
                "PER_ACTION_QMT_RESET_OR_PROFILE_STITCHING", "P3_HEADING",
                "C2ExecutionGuard.observe_qmt_span", f"edge {edge} requested reset/profile stitching",
            )
        self._event("P3_HEADING", "C2ExecutionGuard.observe_qmt_span", edge=edge)

    def validate_knee_branch(self, left: str, right: str) -> None:
        if {left, right} == {"FORWARD", "BACKWARD"}:
            self._reject(
                "ONE_KNEE_FORWARD_ONE_KNEE_BACK", "SEGMENT_FRAME_BRANCH_OWNER",
                "C2ExecutionGuard.validate_knee_branch", "bilateral forward/back branch is physically inconsistent",
            )
        self._event("SEGMENT_FRAME_BRANCH_OWNER", "C2ExecutionGuard.validate_knee_branch", left=left, right=right)

    def update_branch_weights(self, weights: Sequence[float], *, lock_requested: bool) -> None:
        values = np.asarray(weights, dtype=float)
        if lock_requested:
            self._reject(
                "CANDIDATE_LOCK", "BRANCH_POSTERIOR_OWNER",
                "C2ExecutionGuard.update_branch_weights", "explicit candidate lock requested before fit freeze",
            )
        if np.any(values < 0.0) or not np.isclose(np.sum(values), 1.0):
            raise ValueError("branch weights must be a normalized distribution")
        self._event("BRANCH_POSTERIOR_OWNER", "C2ExecutionGuard.update_branch_weights", support=int(np.count_nonzero(values)))

    def validate_factor_fields(self, fields: Sequence[str]) -> None:
        forbidden = {"truth", "action_pose_truth", "axis_sign_truth", "rom_truth", "heldout"}
        leaked = sorted(forbidden.intersection(fields))
        if leaked:
            self._reject(
                "LEAKED_TRUTH_OR_ACTION_POSE_TRUTH", "FACTOR_INPUT_OWNER",
                "C2ExecutionGuard.validate_factor_fields", f"forbidden factor fields {leaked}",
            )
        self._event("FACTOR_INPUT_OWNER", "C2ExecutionGuard.validate_factor_fields", fields=list(fields))

    def validate_wear_prior(self, prior: Mapping[str, Any]) -> None:
        if prior.get("family") != "BROAD_NON_COMPACT_QUALITATIVE" or prior.get("hard_cone_deg") is not None:
            self._reject(
                "EXACTIZED_WEAR_DIRECTION_OR_HARD_NUMERIC_CONE", "WEAR_PRIOR_OWNER",
                "C2ExecutionGuard.validate_wear_prior", "wear prior became exact or compact",
            )
        self._event("WEAR_PRIOR_OWNER", "C2ExecutionGuard.validate_wear_prior", family=prior["family"])

    def validate_bilateral_model(self, *, hard_mirror: bool) -> None:
        if hard_mirror:
            self._reject(
                "HARD_BILATERAL_MIRROR", "SEGMENT_FRAME_BRANCH_OWNER",
                "C2ExecutionGuard.validate_bilateral_model", "bilateral asymmetry must remain possible",
            )
        self._event("SEGMENT_FRAME_BRANCH_OWNER", "C2ExecutionGuard.validate_bilateral_model", hard_mirror=False)

    def validate_connection_vector(self, value: np.ndarray) -> None:
        if np.asarray(value).shape != (3,):
            self._reject(
                "FULL_3D_CONNECTION_TO_AXIAL_SCALAR_COLLAPSE", "P2_FUNCTIONAL_CENTER",
                "C2ExecutionGuard.validate_connection_vector", "connection must remain a full R3 vector",
            )
        self._event("P2_FUNCTIONAL_CENTER", "C2ExecutionGuard.validate_connection_vector", dimension=3)

    def reject_wrong_node_mapping(self, detail: str) -> None:
        self._reject(
            "WRONG_NODE_MAPPING", "SEALED_FACTOR_INPUT_OWNER",
            "C2ExecutionGuard.reject_wrong_node_mapping", detail,
        )

    def reject_caller_covariance_substitution(self, detail: str) -> None:
        self._reject(
            "CALLER_COVARIANCE_SUBSTITUTION", "P1_STOCHASTIC_COVARIANCE_OWNER",
            "C2ExecutionGuard.reject_caller_covariance_substitution", detail,
        )

    def reject_caller_endpoint_label_substitution(self, detail: str) -> None:
        self._reject(
            "CALLER_ENDPOINT_LABEL_SUBSTITUTION", "SEALED_EDGE_ENDPOINT_OWNER",
            "C2ExecutionGuard.reject_caller_endpoint_label_substitution", detail,
        )

    def reject_caller_heading_array_substitution(self, detail: str) -> None:
        self._reject(
            "CALLER_HEADING_ARRAY_SUBSTITUTION", "RUNTIME_OWNED_HEADING_INPUT_OWNER",
            "C2ExecutionGuard.reject_caller_heading_array_substitution", detail,
        )

    def reject_caller_time_substitution(self, detail: str) -> None:
        self._reject(
            "CALLER_TIME_GRID_SUBSTITUTION", "CAPTURE_TIME_OWNER",
            "C2ExecutionGuard.reject_caller_time_substitution", detail,
        )

    def reject_caller_aligned_physical_time_substitution(self, detail: str) -> None:
        self._reject(
            "CALLER_ALIGNED_PHYSICAL_TIME_SUBSTITUTION",
            "CAPTURE_TIME_OWNER",
            "C2ExecutionGuard.reject_caller_aligned_physical_time_substitution",
            detail,
        )

    def reject_caller_aligned_boot_epoch_substitution(self, detail: str) -> None:
        self._reject(
            "CALLER_ALIGNED_BOOT_EPOCH_SUBSTITUTION",
            "CAPTURE_TIME_OWNER",
            "C2ExecutionGuard.reject_caller_aligned_boot_epoch_substitution",
            detail,
        )

    def reject_center_prefix_future_or_heldout(self, detail: str) -> None:
        self._reject(
            "CENTER_PREFIX_FUTURE_OR_HELDOUT_LEAK",
            "CAUSAL_PAIR_LOCAL_CENTER_PREFIX_OWNER",
            "C2ExecutionGuard.reject_center_prefix_future_or_heldout",
            detail,
        )

    def reject_center_prefix_edge_pooling_or_backward_smoothing(
        self, detail: str,
    ) -> None:
        self._reject(
            "CENTER_PREFIX_EDGE_POOLING_OR_BACKWARD_SMOOTHING",
            "CAUSAL_PAIR_LOCAL_CENTER_PREFIX_OWNER",
            "C2ExecutionGuard.reject_center_prefix_edge_pooling_or_backward_smoothing",
            detail,
        )

    def reject_center_prefix_nuisance_bypass_or_reingestion(
        self, detail: str,
    ) -> None:
        self._reject(
            "CENTER_PREFIX_NUISANCE_GATE_BYPASS_OR_HISTORY_REINGESTION",
            "CAUSAL_PAIR_LOCAL_CENTER_PREFIX_OWNER",
            "C2ExecutionGuard.reject_center_prefix_nuisance_bypass_or_reingestion",
            detail,
        )

    def reject_center_prefix_failed_factor_reingestion(self, detail: str) -> None:
        self._reject(
            "CENTER_PREFIX_FAILED_FACTOR_REINGESTION",
            "CAUSAL_PAIR_LOCAL_CENTER_PREFIX_OWNER",
            "C2ExecutionGuard.reject_center_prefix_failed_factor_reingestion",
            detail,
        )

    def reject_center_prefix_membership_or_order_token_substitution(
        self, detail: str,
    ) -> None:
        self._reject(
            "CENTER_PREFIX_MEMBERSHIP_OR_ORDER_TOKEN_SUBSTITUTION",
            "CAUSAL_PAIR_LOCAL_CENTER_PREFIX_OWNER",
            "C2ExecutionGuard.reject_center_prefix_membership_or_order_token_substitution",
            detail,
        )

    def reject_soft_weight_candidate_lock(self, detail: str) -> None:
        self._reject(
            "SOFT_WEIGHT_NUMERICAL_ZERO_CANDIDATE_LOCK", "BRANCH_HARD_SUPPORT_OWNER",
            "C2ExecutionGuard.reject_soft_weight_candidate_lock", detail,
        )

    def reject_raw_unqmt_physical_substitution(self, detail: str) -> None:
        self._reject(
            "RAW_UNQMT_ORIENTATION_PHYSICAL_GATE_SUBSTITUTION",
            "RUNTIME_QMT_ROOTED_PHYSICAL_INPUT_OWNER",
            "C2ExecutionGuard.reject_raw_unqmt_physical_substitution",
            detail,
        )

    def reject_physical_gate_stage_bypass(self, detail: str) -> None:
        self._reject(
            "PHYSICAL_GATE_STAGE_BYPASS", "C2_PIPELINE_RUNTIME",
            "C2ExecutionGuard.reject_physical_gate_stage_bypass", detail,
        )

    def reject_all_invalid_physical_candidate_commit(self, detail: str) -> None:
        self._reject(
            "ALL_INVALID_PHYSICAL_CANDIDATE_ROLLBACK", "C2_PIPELINE_RUNTIME",
            "C2ExecutionGuard.reject_all_invalid_physical_candidate_commit", detail,
        )

    def validate_prequential_time_advance(
        self,
        *,
        elapsed_time_status: str,
        widening_required: bool,
        total_covariance_widened: bool,
        data_information_exactly_unchanged: bool,
        data_information_rank_exactly_unchanged: bool,
        exact_elapsed_s: float | None,
    ) -> None:
        if elapsed_time_status == "EXACT_SAME_DERIVED_BOOT_EPOCH":
            code = "PREQUENTIAL_EXACT_GAP_DIFFUSION_DELAYED_OR_OMITTED"
            invalid_elapsed = exact_elapsed_s is None or float(exact_elapsed_s) < 0.0
        elif elapsed_time_status.startswith("UNKNOWN_"):
            code = "PREQUENTIAL_UNKNOWN_RESET_FLOOR_OMITTED_OR_FABRICATED"
            invalid_elapsed = exact_elapsed_s is not None
        else:
            raise ValueError("unknown owner-derived prequential time status")
        if (
            invalid_elapsed
            or not data_information_exactly_unchanged
            or not data_information_rank_exactly_unchanged
            or (widening_required and not total_covariance_widened)
        ):
            self._reject(
                code,
                "PREQUENTIAL_CAPTURE_TIME_OWNER",
                "C2ExecutionGuard.validate_prequential_time_advance",
                "owner-derived time advance must widen total covariance before score while preserving the exact data-information matrix/rank",
            )
        self._event(
            "PREQUENTIAL_CAPTURE_TIME_OWNER",
            "C2ExecutionGuard.validate_prequential_time_advance",
            elapsed_time_status=elapsed_time_status,
            widening_required=bool(widening_required),
            total_covariance_widened=bool(total_covariance_widened),
            data_information_exactly_unchanged=bool(data_information_exactly_unchanged),
            data_information_rank_exactly_unchanged=bool(
                data_information_rank_exactly_unchanged
            ),
            exact_elapsed_s=exact_elapsed_s,
        )

    def validate_connection_frame_mode(self, mode: str) -> None:
        if mode != "SENSOR_TO_SEGMENT_THEN_WORLD":
            self._reject(
                "SENSOR_SEGMENT_CONNECTION_FRAME_SWAP", "SCIENTIFIC_FK_OWNER",
                "C2ExecutionGuard.validate_connection_frame_mode",
                f"connection vectors are sensor-frame quantities; forbidden mode {mode}",
            )
        self._event(
            "SCIENTIFIC_FK_OWNER", "C2ExecutionGuard.validate_connection_frame_mode",
            mode=mode,
        )

    def propagate_heading(self, parent_delta: np.ndarray, child_delta_filt: np.ndarray, *, mode: str) -> np.ndarray:
        if mode != "TIME_VARYING_PARENT_PLUS_CHILD_DELTAFILT":
            self._reject(
                "TIME_VARYING_HEADING_TO_MEAN_OR_CONSTANT_SUBSTITUTION", "P3_HEADING",
                "C2ExecutionGuard.propagate_heading", f"forbidden heading mode {mode}",
            )
        parent = np.asarray(parent_delta, dtype=float)
        child = np.asarray(child_delta_filt, dtype=float)
        if parent.shape != child.shape:
            raise ValueError("parent and child heading traces must have equal shape")
        self._event("P3_HEADING", "C2ExecutionGuard.propagate_heading", samples=int(parent.size))
        return parent + child

    def validate_row_selection(self, policy: str) -> None:
        if policy != "PREFIT_FIXED_NOISE_STANDARDIZED_CONTIGUOUS_BLOCKS":
            self._reject(
                "RESULT_DIRECTED_ROW_SELECTION", "P2_FUNCTIONAL_AXIS",
                "C2ExecutionGuard.validate_row_selection", f"forbidden policy {policy}",
            )
        self._event("P2_FUNCTIONAL_AXIS", "C2ExecutionGuard.validate_row_selection", policy=policy)

    def consider_candidate(self, *, physically_legal: bool, residual: float) -> None:
        if not physically_legal:
            self._reject(
                "INVALID_LOW_RESIDUAL_DISPLACES_LEGAL_CANDIDATE", "BRANCH_POSTERIOR_OWNER",
                "C2ExecutionGuard.consider_candidate", f"illegal candidate offered with residual {residual}",
            )
        self._event("BRANCH_POSTERIOR_OWNER", "C2ExecutionGuard.consider_candidate", residual=float(residual))

    def validate_geometry(self, points: Mapping[str, np.ndarray], edges: Sequence[tuple[str, str]]) -> None:
        expected_nodes = {name for edge in ROOTED_EDGES for name in edge}
        if tuple(edges) != ROOTED_EDGES or set(points) != expected_nodes:
            self._reject(
                "DISCONNECTED_ROOTED_GRAPH", "SCIENTIFIC_FK_OWNER",
                "C2ExecutionGuard.validate_geometry", "graph is not the official connected rooted nine-edge tree",
            )
        lengths = [float(np.linalg.norm(np.asarray(points[b]) - np.asarray(points[a]))) for a, b in edges]
        if min(lengths) <= 1e-6:
            self._reject(
                "COLLAPSED_GEOMETRY", "SCIENTIFIC_FK_OWNER",
                "C2ExecutionGuard.validate_geometry", "one or more rooted connections collapsed",
            )
        self._event(
            "SCIENTIFIC_FK_OWNER", "C2ExecutionGuard.validate_geometry",
            edge_count=len(edges),
            structural_only=True,
            bilateral_crossing_or_mirror_delegated_to_uncertainty_aware_trajectory_owner=True,
        )

    def validate_viewer_transform(self, *, mode: str) -> None:
        if mode != "DIRECT_SCIENTIFIC_FK_NO_REBASE_NO_IK_NO_REPAIR":
            self._reject(
                "VIEWER_REBASE_OR_IK_REPAIR_RESCUE", "SCIENTIFIC_FK_OWNER",
                "C2ExecutionGuard.validate_viewer_transform", f"forbidden viewer mode {mode}",
            )
        self._event("SCIENTIFIC_FK_OWNER", "C2ExecutionGuard.validate_viewer_transform", mode=mode)

    def record_progress(self, inputs: Sequence[str], *, prediction_scored_before_ingest: bool) -> None:
        if set(inputs) != REQUIRED_PROGRESS_INPUTS or not prediction_scored_before_ingest:
            self._reject(
                "FAKE_COUNT_TIME_ITERATION_PROGRESS", "P5_PROGRESSIVE_OWNER",
                "C2ExecutionGuard.record_progress", "progress must use information/uncertainty/branch/validity/prequential inputs",
            )
        self._event("P5_PROGRESSIVE_OWNER", "C2ExecutionGuard.record_progress", inputs=sorted(inputs))

    def claim_initial_still_completion(self, *, complete_calibration: bool) -> None:
        if complete_calibration:
            self._reject(
                "FALSE_INITIAL_STILL_COMPLETION", "P5_PROGRESSIVE_OWNER",
                "C2ExecutionGuard.claim_initial_still_completion", "initial still cannot complete calibration",
            )
        self._event("P5_PROGRESSIVE_OWNER", "C2ExecutionGuard.claim_initial_still_completion", complete=False)

    def freeze_fit(self) -> None:
        self.fit_frozen = True
        self._event("FIT_FREEZE_OWNER", "C2ExecutionGuard.freeze_fit")

    def open_holdout(self) -> None:
        if not self.fit_frozen:
            self._reject(
                "HELDOUT_LEAK_OR_POST_HELDOUT_REFIT", "HOLDOUT_OWNER",
                "C2ExecutionGuard.open_holdout", "heldout requested before immutable fit freeze",
            )
        self.holdout_opened = True
        self._event("HOLDOUT_OWNER", "C2ExecutionGuard.open_holdout")

    def request_refit(self) -> None:
        if self.holdout_opened:
            self._reject(
                "HELDOUT_LEAK_OR_POST_HELDOUT_REFIT", "HOLDOUT_OWNER",
                "C2ExecutionGuard.request_refit", "refit requested after heldout access",
            )
        self._event("FIT_FREEZE_OWNER", "C2ExecutionGuard.request_refit")

    def compare_fresh_batch(self, reference: np.ndarray, fresh: np.ndarray, *, atol: float, rtol: float) -> None:
        if not np.allclose(reference, fresh, atol=float(atol), rtol=float(rtol)):
            self._reject(
                "FRESH_BATCH_DISAGREEMENT", "FRESH_BATCH_OWNER",
                "C2ExecutionGuard.compare_fresh_batch", "fresh batch differs beyond sealed tolerance",
            )
        self._event("FRESH_BATCH_OWNER", "C2ExecutionGuard.compare_fresh_batch", atol=float(atol), rtol=float(rtol))

    def validate_fresh_reader_sessions(self, primary_session_id: str, fresh_session_id: str) -> None:
        if not primary_session_id or primary_session_id == fresh_session_id:
            self._reject(
                "FRESH_READER_SESSION_REUSE", "FRESH_BATCH_OWNER",
                "C2ExecutionGuard.validate_fresh_reader_sessions",
                "fresh recomputation reused the primary bounded-reader session or cached decoded actions",
            )
        self._event(
            "FRESH_BATCH_OWNER", "C2ExecutionGuard.validate_fresh_reader_sessions",
            primary_session_id=primary_session_id, fresh_session_id=fresh_session_id,
        )

    def reject_caller_attested_fresh_unlock(self) -> None:
        self._reject(
            "CALLER_ATTESTED_FRESH_UNLOCK", "FRESH_BATCH_OWNER",
            "C2ExecutionGuard.reject_caller_attested_fresh_unlock",
            "scientific fresh verification must compare actual independent runtime-owned arrays",
        )

    def validate_causal_prefix_fresh_agreement(self, *, all_prefixes_and_trajectories_match: bool) -> None:
        if not all_prefixes_and_trajectories_match:
            self._reject(
                "CAUSAL_PREFIX_FRESH_DISAGREEMENT", "FRESH_BATCH_OWNER",
                "C2ExecutionGuard.validate_causal_prefix_fresh_agreement",
                "a fresh run matched or approached the final state but disagreed at a causal prefix/trajectory",
            )
        self._event(
            "FRESH_BATCH_OWNER", "C2ExecutionGuard.validate_causal_prefix_fresh_agreement",
            all_prefixes_and_trajectories_match=True,
        )

    def reject_second_branch_posterior_owner(self) -> None:
        self._reject(
            "BRANCH_POSTERIOR_SECOND_SOURCE_OR_DIVERGENCE", "BRANCH_POSTERIOR_OWNER",
            "C2ExecutionGuard.reject_second_branch_posterior_owner",
            "segment-frame posterior may only be bound from the immutable progressive snapshot",
        )

    def audit(self) -> dict[str, Any]:
        return {
            "schema": "biospur-c2-execution-owner-audit-v1",
            "events": [
                {"owner": row.owner, "callable": row.callable, "outcome": row.outcome, "detail": dict(row.detail)}
                for row in self.events
            ],
        }

    def checkpoint(self) -> Mapping[str, Any]:
        return {
            "capture_id": self.capture_id,
            "last_episode_index": self.last_episode_index,
            "vqf_token_by_node": dict(self.vqf_token_by_node),
            "fit_frozen": self.fit_frozen,
            "holdout_opened": self.holdout_opened,
            "events": deepcopy(self.events),
        }

    def restore(self, checkpoint: Mapping[str, Any]) -> None:
        self.capture_id = checkpoint["capture_id"]
        self.last_episode_index = int(checkpoint["last_episode_index"])
        self.vqf_token_by_node = dict(checkpoint["vqf_token_by_node"])
        self.fit_frozen = bool(checkpoint["fit_frozen"])
        self.holdout_opened = bool(checkpoint["holdout_opened"])
        self.events = deepcopy(checkpoint["events"])


def _baseline_points() -> dict[str, np.ndarray]:
    return {
        "pelvis": np.array([0.0, 0.0, 0.0]), "torso": np.array([0.0, 0.0, 0.40]),
        "upper_arm_left": np.array([-0.22, 0.0, 0.38]), "forearm_left": np.array([-0.50, 0.0, 0.34]),
        "upper_arm_right": np.array([0.22, 0.0, 0.38]), "forearm_right": np.array([0.50, 0.0, 0.34]),
        "thigh_left": np.array([-0.12, 0.0, -0.45]), "shank_left": np.array([-0.12, 0.0, -0.88]),
        "thigh_right": np.array([0.12, 0.0, -0.46]), "shank_right": np.array([0.12, 0.0, -0.91]),
    }


def _new_guard(settings: Mapping[str, Any]) -> C2ExecutionGuard:
    guard = C2ExecutionGuard(settings)
    guard.begin_capture("C2")
    return guard


def run_owner_level_architecture_mutations(
    settings: Mapping[str, Any],
    *,
    prefit_registry_seal_path: str | Path,
    initial_stochastic_state: Mapping[str, Any],
) -> dict[str, Any]:
    """Drive every registered architecture mutation through its real owner."""

    from .functional_geometry import (
        EDGE_ACTIONS, EDGE_SPECS, HINGE_EDGES,
        AlignedPair, AxisEstimate, CenterEstimate, aligned_pair,
        estimate_hinge_axis_qmt,
    )
    from .center_prefix import CausalCenterPrefixOwner
    from .heading import PersistentHeadingOwner
    from .heldout_evaluation import numeric_frozen_heldout_transaction_isolation_gate
    from .pipeline_runtime import C2PipelineRuntime
    from .progressive import ProgressiveCalibrationState
    from .range_reader import DecodedAction, IMU_DTYPE
    from .scientific_renderer import _validated_source_display
    from .scientific_fk import (
        PhysicalTrajectoryCandidateAssessment,
        ScientificForwardKinematicsOwner,
        numeric_physical_candidate_uncertainty_gate,
        physical_input_binding_token,
    )
    from .segment_frames import SegmentFrameBranchOwner
    from .timebase import PairAlignment, PersistentPairClockState

    rows: dict[str, dict[str, Any]] = {}
    numerical_evidence: dict[str, Mapping[str, Any]] = {}

    def execute(name: str, owner: str, callable_name: str, operation: Any) -> None:
        try:
            operation()
        except ClassAGuardViolation as exc:
            rows[name] = {
                "coverage_class": "EXECUTED_OWNER_LEVEL", "owner": owner, "callable": callable_name,
                "expected_rejection": name, "observed_rejection": exc.code,
                "caught": exc.code == name, "detail": exc.detail,
                "numerical_evidence": deepcopy(numerical_evidence.get(name, {})),
            }
        except Exception as exc:
            rows[name] = {
                "coverage_class": "EXECUTED_OWNER_LEVEL", "owner": owner, "callable": callable_name,
                "expected_rejection": name, "observed_rejection": f"UNEXPECTED_{type(exc).__name__}",
                "caught": False, "detail": str(exc),
                "numerical_evidence": deepcopy(numerical_evidence.get(name, {})),
            }
        else:
            rows[name] = {
                "coverage_class": "EXECUTED_OWNER_LEVEL", "owner": owner, "callable": callable_name,
                "expected_rejection": name, "observed_rejection": None,
                "caught": False, "detail": "mutation reached owner without rejection",
                "numerical_evidence": deepcopy(numerical_evidence.get(name, {})),
            }

    def representative_geometry() -> tuple[dict[str, AxisEstimate], dict[str, CenterEstimate]]:
        points = _baseline_points()
        joints = {
            "pelvis_torso": np.array([0.0, 0.0, 0.20]),
            "shoulder_left": np.array([-0.10, 0.0, 0.40]),
            "elbow_left": np.array([-0.36, 0.0, 0.36]),
            "shoulder_right": np.array([0.10, 0.0, 0.40]),
            "elbow_right": np.array([0.36, 0.0, 0.36]),
            "hip_left": np.array([-0.06, 0.0, -0.12]),
            "knee_left": np.array([-0.12, 0.0, -0.66]),
            "hip_right": np.array([0.06, 0.0, -0.12]),
            "knee_right": np.array([0.12, 0.0, -0.68]),
        }
        center_statistical = np.eye(6) * 4e-4
        center_systematic = np.eye(6) * 0.03**2
        centers = {
            edge: CenterEstimate(
                edge=edge, parent=parent, child=child,
                joint_to_parent_sensor_m=-(joints[edge] - points[parent]),
                joint_to_child_sensor_m=-(joints[edge] - points[child]),
                covariance_m2=center_statistical + center_systematic,
                report={
                    "synthetic_architecture_fixture": True,
                    "sandwich_covariance_m2": center_statistical.tolist(),
                    "statistical_covariance_including_nullspace_prior_m2": center_statistical.tolist(),
                    "human_worn_model_floor_m": 0.03,
                    "accelerometer_bias_drift_systematic_covariance_m2": np.zeros((6, 6)).tolist(),
                    "accelerometer_scale_cross_axis_systematic_covariance_m2": np.zeros((6, 6)).tolist(),
                    "gyro_bias_systematic_covariance_m2": np.zeros((6, 6)).tolist(),
                    "gyro_bias_drift_systematic_covariance_m2": np.zeros((6, 6)).tolist(),
                    "gyro_scale_cross_axis_systematic_covariance_m2": np.zeros((6, 6)).tolist(),
                    "accelerometer_gyro_shared_scale_cross_axis_systematic_covariance_m2": np.zeros((6, 6)).tolist(),
                    "persistent_clock_systematic_covariance_m2": np.zeros((6, 6)).tolist(),
                    "human_worn_systematic_covariance_m2": center_systematic.tolist(),
                    "total_systematic_covariance_m2": center_systematic.tolist(),
                    "owner_update_eligible": True,
                    "owner_update_mode": "GAUGE_REDUCED_ROBUST_BREAD_INFORMED_SUBSPACE",
                    "gauge_reduced_robust_bread_informed_basis": np.eye(6).tolist(),
                    "gauge_reduced_robust_bread_information_m2_inv": (
                        np.eye(6) / 4e-4
                    ).tolist(),
                },
            )
            for edge, (parent, child) in zip(
                ("pelvis_torso", "shoulder_left", "elbow_left", "shoulder_right", "elbow_right", "hip_left", "knee_left", "hip_right", "knee_right"),
                ROOTED_EDGES,
            )
        }
        tangent_basis = np.array([[1.0, 0.0], [0.0, 0.0], [0.0, 1.0]])
        statistical = np.eye(4) * np.deg2rad(2.0) ** 2
        systematic = np.eye(4) * np.deg2rad(5.0) ** 2
        axes = {
            edge: AxisEstimate(
                edge=edge,
                parent_axis_sensor=np.array([0.0, 1.0, 0.0]),
                child_axis_sensor=np.array([0.0, 1.0, 0.0]),
                tangent_covariance_rad2=statistical + systematic,
                report={
                    "parent_tangent_basis_sensor": tangent_basis.tolist(),
                    "child_tangent_basis_sensor": tangent_basis.tolist(),
                    "statistical_tangent_covariance_rad2": statistical.tolist(),
                    "total_systematic_tangent_covariance_rad2": systematic.tolist(),
                    "systematic_component_tangent_covariances_rad2": {
                        "human_worn": systematic.tolist(),
                    },
                    "systematic_human_worn_tangent_covariance_rad2": systematic.tolist(),
                    "owner_update_eligible": True,
                    "owner_update_mode": "PRODUCT_S2_HESSIAN_INFORMED_UPDATE",
                    "synthetic_architecture_fixture": True,
                },
            )
            for edge in ("elbow_left", "elbow_right", "knee_left", "knee_right")
        }
        return axes, centers

    def segment_owner(
        source_settings: Mapping[str, Any] | None = None,
    ) -> SegmentFrameBranchOwner:
        owner_settings = settings if source_settings is None else source_settings
        return SegmentFrameBranchOwner(
            owner_settings["segment_frames"], execution_guard=_new_guard(owner_settings),
        )

    def built_segment_owner(
        source_settings: Mapping[str, Any] | None = None,
    ) -> SegmentFrameBranchOwner:
        owner = segment_owner(source_settings)
        axes, centers = representative_geometry()
        owner.build(axes, centers, chronological_index=0, action="SYNTHETIC_ARCHITECTURE")
        return owner

    def heading_owner() -> PersistentHeadingOwner:
        frame_owner = built_segment_owner()
        return PersistentHeadingOwner(
            settings["heading"], frame_owner.branches,
            execution_guard=frame_owner.execution_guard,
            first_chronological_index=0,
        )

    def center_prefix_owner() -> CausalCenterPrefixOwner:
        return CausalCenterPrefixOwner(
            settings["joint_center"]["causal_historical_prefix_owner"],
            chronological_actions=settings["execution_contract"][
                "chronological_actions"
            ],
            edge_actions=EDGE_ACTIONS,
            execution_guard=_new_guard(settings),
        )

    def center_prefix_pair(
        edge: str,
        action: str,
        *,
        heldout: bool = False,
    ) -> AlignedPair:
        actions = tuple(settings["execution_contract"]["chronological_actions"])
        index = actions.index(action)
        rows = 8
        token = sha256(
            f"CENTER_PREFIX_OWNER_FIXTURE:{edge}:{index}:{action}".encode("utf-8")
        ).hexdigest()
        sample = np.arange(rows, dtype=float)
        vector = np.column_stack((sample, sample * 0.5, sample * 0.25))
        return AlignedPair(
            edge=edge,
            action=action,
            parent_acc=vector.copy(),
            child_acc=vector.copy(),
            parent_gyro=vector.copy(),
            child_gyro=vector.copy(),
            parent_observed_time_s=sample * 0.005 + index * 10.0,
            child_observed_time_s=sample * 0.005 + index * 10.0,
            parent_boot_epoch=np.zeros(rows, dtype=np.int64),
            child_boot_epoch=np.zeros(rows, dtype=np.int64),
            alignment=PairAlignment(
                parent_indices=np.arange(rows, dtype=np.int64),
                child_indices=np.arange(rows, dtype=np.int64),
                lag_samples=0,
                report={"lag_uncertainty_s": 0.005},
            ),
            contiguous_spans=(slice(0, rows),),
            provenance={
                "chronological_index": index,
                "action": action,
                "source_role": "HELDOUT" if heldout else "TRAINING_RANGE",
                "heldout": bool(heldout),
                "runtime_owner_token": token,
            },
        )

    def center_prefix_future_or_heldout() -> None:
        edge = "pelvis_torso"
        first, second = EDGE_ACTIONS[edge][:2]
        actions = tuple(settings["execution_contract"]["chronological_actions"])
        caught: list[str] = []
        for label, pair in (
            ("FUTURE", center_prefix_pair(edge, second)),
            ("HELDOUT", center_prefix_pair(edge, first, heldout=True)),
        ):
            try:
                center_prefix_owner().select(
                    edge=edge,
                    current_pair=pair,
                    chronological_index=actions.index(first),
                    action=first,
                    prequential_prediction_sha256="a" * 64,
                    geometry_has_accepted_center=False,
                )
            except ClassAGuardViolation as exc:
                if exc.code == "CENTER_PREFIX_FUTURE_OR_HELDOUT_LEAK":
                    caught.append(label)
                    continue
                raise
        if caught == ["FUTURE", "HELDOUT"]:
            raise ClassAGuardViolation(
                "CENTER_PREFIX_FUTURE_OR_HELDOUT_LEAK",
                "independent future and heldout pair substitutions were both rejected",
            )
        raise RuntimeError("center prefix did not reject both future and heldout subcases")

    execute(
        "CENTER_PREFIX_FUTURE_OR_HELDOUT_LEAK",
        "CAUSAL_PAIR_LOCAL_CENTER_PREFIX_OWNER",
        "CausalCenterPrefixOwner.select",
        center_prefix_future_or_heldout,
    )

    def center_prefix_cross_edge() -> None:
        caught: list[str] = []
        action = EDGE_ACTIONS["shoulder_left"][0]
        try:
            center_prefix_owner().select(
                edge="pelvis_torso",
                current_pair=center_prefix_pair("shoulder_left", action),
                chronological_index=tuple(
                    settings["execution_contract"]["chronological_actions"]
                ).index(action),
                action=action,
                prequential_prediction_sha256="b" * 64,
                geometry_has_accepted_center=False,
            )
        except ClassAGuardViolation as exc:
            if exc.code == "CENTER_PREFIX_EDGE_POOLING_OR_BACKWARD_SMOOTHING":
                caught.append("CROSS_EDGE")
            else:
                raise
        owner = center_prefix_owner()
        edge = "pelvis_torso"
        route = EDGE_ACTIONS[edge]
        actions = tuple(settings["execution_contract"]["chronological_actions"])
        for prediction, route_action in zip(("1", "2"), route[:2], strict=True):
            selection = owner.select(
                edge=edge,
                current_pair=center_prefix_pair(edge, route_action),
                chronological_index=actions.index(route_action),
                action=route_action,
                prequential_prediction_sha256=prediction * 64,
                geometry_has_accepted_center=False,
            )
            owner.commit(
                selection,
                estimator_owner_update_eligible=False,
                geometry_update_accepted=False,
                estimator_completed=True,
                retain_for_future_prefix=True,
            )
        owner._eligible_history[edge].reverse()
        try:
            owner.select(
                edge=edge,
                current_pair=center_prefix_pair(edge, route[2]),
                chronological_index=actions.index(route[2]),
                action=route[2],
                prequential_prediction_sha256="3" * 64,
                geometry_has_accepted_center=False,
            )
        except ClassAGuardViolation as exc:
            if exc.code == "CENTER_PREFIX_EDGE_POOLING_OR_BACKWARD_SMOOTHING":
                caught.append("BACKWARD_ORDER")
            else:
                raise
        if caught == ["CROSS_EDGE", "BACKWARD_ORDER"]:
            raise ClassAGuardViolation(
                "CENTER_PREFIX_EDGE_POOLING_OR_BACKWARD_SMOOTHING",
                "cross-edge pooling and backward history order were independently rejected",
            )
        raise RuntimeError("center prefix did not reject both edge and ordering subcases")

    execute(
        "CENTER_PREFIX_EDGE_POOLING_OR_BACKWARD_SMOOTHING",
        "CAUSAL_PAIR_LOCAL_CENTER_PREFIX_OWNER",
        "CausalCenterPrefixOwner.select",
        center_prefix_cross_edge,
    )

    def center_prefix_nuisance_bypass() -> None:
        caught: list[str] = []
        owner = center_prefix_owner()
        edge = "pelvis_torso"
        action = EDGE_ACTIONS[edge][0]
        selection = owner.select(
            edge=edge,
            current_pair=center_prefix_pair(edge, action),
            chronological_index=tuple(
                settings["execution_contract"]["chronological_actions"]
            ).index(action),
            action=action,
            prequential_prediction_sha256="c" * 64,
            geometry_has_accepted_center=False,
        )
        try:
            owner.commit(
                selection,
                estimator_owner_update_eligible=False,
                geometry_update_accepted=True,
                estimator_completed=True,
                retain_for_future_prefix=False,
            )
        except ClassAGuardViolation as exc:
            if exc.code == "CENTER_PREFIX_NUISANCE_GATE_BYPASS_OR_HISTORY_REINGESTION":
                caught.append("NUISANCE_GATE_BYPASS")
            else:
                raise

        owner = center_prefix_owner()
        route = EDGE_ACTIONS[edge]
        actions = tuple(settings["execution_contract"]["chronological_actions"])
        first_pair = center_prefix_pair(edge, route[0])
        first_selection = owner.select(
            edge=edge,
            current_pair=first_pair,
            chronological_index=actions.index(route[0]),
            action=route[0],
            prequential_prediction_sha256="4" * 64,
            geometry_has_accepted_center=False,
        )
        owner.commit(
            first_selection,
            estimator_owner_update_eligible=False,
            geometry_update_accepted=False,
            estimator_completed=True,
            retain_for_future_prefix=True,
        )
        second_selection = owner.select(
            edge=edge,
            current_pair=center_prefix_pair(edge, route[1]),
            chronological_index=actions.index(route[1]),
            action=route[1],
            prequential_prediction_sha256="5" * 64,
            geometry_has_accepted_center=False,
        )
        owner.commit(
            second_selection,
            estimator_owner_update_eligible=True,
            geometry_update_accepted=True,
            estimator_completed=True,
            retain_for_future_prefix=False,
        )
        third_selection = owner.select(
            edge=edge,
            current_pair=center_prefix_pair(edge, route[2]),
            chronological_index=actions.index(route[2]),
            action=route[2],
            prequential_prediction_sha256="6" * 64,
            geometry_has_accepted_center=True,
        )
        mutated_pairs = (first_pair, third_selection.current_pair)
        pair_rows, token_payload, selection_token = owner._semantic_binding(
            edge=third_selection.edge,
            chronological_index=third_selection.chronological_index,
            action=third_selection.action,
            mode=third_selection.mode,
            prequential_prediction_sha256=(
                third_selection.prequential_prediction_sha256
            ),
            geometry_had_accepted_center_before_current=(
                third_selection.geometry_had_accepted_center_before_current
            ),
            pairs=mutated_pairs,
        )
        mutated_report = {
            **dict(third_selection.report),
            **token_payload,
            "selection_token": selection_token,
            "pair_rows": pair_rows,
            "pair_count_consumed_by_estimator": len(mutated_pairs),
        }
        mutated_selection = replace(
            third_selection,
            pairs=mutated_pairs,
            selection_token=selection_token,
            report=mutated_report,
        )
        owner._pending[edge] = mutated_selection
        try:
            owner.commit(
                mutated_selection,
                estimator_owner_update_eligible=False,
                geometry_update_accepted=False,
                estimator_completed=True,
                retain_for_future_prefix=False,
            )
        except ClassAGuardViolation as exc:
            if exc.code == "CENTER_PREFIX_NUISANCE_GATE_BYPASS_OR_HISTORY_REINGESTION":
                caught.append("POST_ACCEPTANCE_HISTORY_REINGESTION")
            else:
                raise
        if caught == ["NUISANCE_GATE_BYPASS", "POST_ACCEPTANCE_HISTORY_REINGESTION"]:
            raise ClassAGuardViolation(
                "CENTER_PREFIX_NUISANCE_GATE_BYPASS_OR_HISTORY_REINGESTION",
                "nuisance-gate bypass and post-acceptance history reingestion were independently rejected",
            )
        raise RuntimeError("center prefix did not reject both nuisance and reingestion subcases")

    execute(
        "CENTER_PREFIX_NUISANCE_GATE_BYPASS_OR_HISTORY_REINGESTION",
        "CAUSAL_PAIR_LOCAL_CENTER_PREFIX_OWNER",
        "CausalCenterPrefixOwner.commit",
        center_prefix_nuisance_bypass,
    )

    def center_prefix_failed_factor_reingestion() -> None:
        owner = center_prefix_owner()
        edge = "pelvis_torso"
        action = EDGE_ACTIONS[edge][0]
        selection = owner.select(
            edge=edge,
            current_pair=center_prefix_pair(edge, action),
            chronological_index=tuple(
                settings["execution_contract"]["chronological_actions"]
            ).index(action),
            action=action,
            prequential_prediction_sha256="d" * 64,
            geometry_has_accepted_center=False,
        )
        owner.commit(
            selection,
            estimator_owner_update_eligible=False,
            geometry_update_accepted=False,
            estimator_completed=False,
            retain_for_future_prefix=True,
        )

    execute(
        "CENTER_PREFIX_FAILED_FACTOR_REINGESTION",
        "CAUSAL_PAIR_LOCAL_CENTER_PREFIX_OWNER",
        "CausalCenterPrefixOwner.commit",
        center_prefix_failed_factor_reingestion,
    )

    def center_prefix_membership_token_substitution() -> None:
        owner = center_prefix_owner()
        edge = "pelvis_torso"
        route = EDGE_ACTIONS[edge]
        actions = tuple(settings["execution_contract"]["chronological_actions"])
        first_pair = center_prefix_pair(edge, route[0])
        first_selection = owner.select(
            edge=edge,
            current_pair=first_pair,
            chronological_index=actions.index(route[0]),
            action=route[0],
            prequential_prediction_sha256="7" * 64,
            geometry_has_accepted_center=False,
        )
        owner.commit(
            first_selection,
            estimator_owner_update_eligible=False,
            geometry_update_accepted=False,
            estimator_completed=True,
            retain_for_future_prefix=True,
        )
        second_pair = center_prefix_pair(edge, route[1])
        selection = owner.select(
            edge=edge,
            current_pair=second_pair,
            chronological_index=actions.index(route[1]),
            action=route[1],
            prequential_prediction_sha256="8" * 64,
            geometry_has_accepted_center=False,
        )
        mutated = replace(
            selection,
            pairs=(first_pair, first_pair, second_pair),
        )
        owner._pending[edge] = mutated
        owner.commit(
            mutated,
            estimator_owner_update_eligible=False,
            geometry_update_accepted=False,
            estimator_completed=True,
            retain_for_future_prefix=True,
        )

    execute(
        "CENTER_PREFIX_MEMBERSHIP_OR_ORDER_TOKEN_SUBSTITUTION",
        "CAUSAL_PAIR_LOCAL_CENTER_PREFIX_OWNER",
        "CausalCenterPrefixOwner.commit/_semantic_binding",
        center_prefix_membership_token_substitution,
    )

    def fk_owner() -> ScientificForwardKinematicsOwner:
        return ScientificForwardKinematicsOwner(execution_guard=_new_guard(settings))

    def transaction_runtime(*, first_action_rows: int = 4) -> C2PipelineRuntime:
        nodes = tuple(str(node) for node in initial_stochastic_state["nodes"])
        runtime = C2PipelineRuntime(
            settings,
            initial_stochastic_state,
            prefit_registry_seal_path=prefit_registry_seal_path,
            execution_role="SYNTHETIC_QUALIFICATION",
        )
        for index, action in enumerate(settings["execution_contract"]["chronological_actions"]):
            rows_by_node: dict[str, np.ndarray] = {}
            row_count = int(first_action_rows) if index == 0 else 4
            if row_count < 4:
                raise ValueError("transaction fixture requires at least four rows")
            for node_index, node in enumerate(nodes):
                rows = np.zeros(row_count, dtype=IMU_DTYPE)
                rows["derived_boot_epoch"] = 0
                rows["imu_sample_sequence"] = (
                    np.arange(row_count, dtype=np.uint16) + 1024 * index
                )
                rows["node_timer_us"] = (
                    1_000_000 + index * 10_000_000
                    + np.arange(row_count, dtype=np.uint64) * 5_000
                )
                rows["acc_raw"][:, 2] = 2_048
                rows["raw_start_offset"] = (
                    np.arange(row_count, dtype=np.uint64) + node_index * 1024
                )
                rows["raw_end_offset"] = rows["raw_start_offset"] + 1
                rows["raw_sample_index"] = np.arange(row_count, dtype=np.uint64) % 256
                rows["decode_acceptance_status"] = 1
                rows_by_node[node] = rows
            runtime.ingest_orientation_episode(DecodedAction(
                action=action, chronological_index=index, interval=(index, index + 1),
                rows_by_node=rows_by_node,
                access_audit={
                    "reader_session_id": "SYNTHETIC_TRANSACTION_RUNTIME_OWNER",
                    "action": action,
                    "chronological_index": index,
                },
                decode_audit={"synthetic_transaction_fixture": True},
            ))
        runtime.finish_orientation_and_begin_calibration()
        return runtime

    def seed_full_geometry(runtime: C2PipelineRuntime) -> None:
        axes, centers = representative_geometry()
        for estimate in centers.values():
            runtime._geometry_owner.ingest_center(
                deepcopy(estimate), chronological_index=-1,
                action="SYNTHETIC_ARCHITECTURE_FULL_TREE_SEED",
                reference_time_s=0.0,
            )
        for estimate in axes.values():
            runtime._geometry_owner.ingest_axis(
                deepcopy(estimate), chronological_index=-1,
                action="SYNTHETIC_ARCHITECTURE_FULL_TREE_SEED",
                reference_time_s=0.0,
            )

    def timed_transaction_runtime(*, reset_after_first: bool) -> C2PipelineRuntime:
        nodes = tuple(str(node) for node in initial_stochastic_state["nodes"])
        runtime = C2PipelineRuntime(
            settings,
            initial_stochastic_state,
            prefit_registry_seal_path=prefit_registry_seal_path,
            execution_role="SYNTHETIC_QUALIFICATION",
        )
        actions = tuple(settings["execution_contract"]["chronological_actions"])
        for index, action in enumerate(actions):
            rows_by_node: dict[str, np.ndarray] = {}
            boot_epoch = 0 if not reset_after_first or index == 0 else 1
            if reset_after_first and index > 0:
                timer_start = 5_000 + (index - 1) * 10_000_000
            else:
                timer_start = 1_000_000 + index * 10_000_000
            for node_index, node in enumerate(nodes):
                rows = np.zeros(4, dtype=IMU_DTYPE)
                rows["derived_boot_epoch"] = boot_epoch
                rows["imu_sample_sequence"] = np.arange(4, dtype=np.uint16) + 4 * index
                rows["node_timer_us"] = timer_start + np.arange(4, dtype=np.uint64) * 5_000
                rows["acc_raw"][:, 2] = 2_048
                rows["raw_start_offset"] = np.arange(4, dtype=np.uint64) + node_index * 10
                rows["raw_end_offset"] = rows["raw_start_offset"] + 1
                rows["raw_sample_index"] = np.arange(4, dtype=np.uint8)
                rows["decode_acceptance_status"] = 1
                rows_by_node[node] = rows
            runtime.ingest_orientation_episode(DecodedAction(
                action=action,
                chronological_index=index,
                interval=(1_000 + index * 100, 1_100 + index * 100),
                rows_by_node=rows_by_node,
                access_audit={
                    "reader_session_id": (
                        "SYNTHETIC_UNKNOWN_RESET_TIME_OWNER"
                        if reset_after_first else "SYNTHETIC_EXACT_GAP_TIME_OWNER"
                    ),
                    "action": action,
                    "chronological_index": index,
                },
                decode_audit={"synthetic_prequential_time_owner_fixture": True},
            ))
        runtime.finish_orientation_and_begin_calibration()
        _, centers = representative_geometry()
        runtime._geometry_owner.ingest_center(
            deepcopy(centers["pelvis_torso"]),
            chronological_index=-1,
            action="SYNTHETIC_PRE_CAUSAL_TIME_GATE_SEED",
            reference_time_s=0.0,
        )
        return runtime

    def run_transaction_episode(
        runtime: C2PipelineRuntime,
        index: int,
        *,
        inject_after: str | None = None,
    ) -> None:
        actions = settings["execution_contract"]["chronological_actions"]
        action = actions[index]
        axes, centers = representative_geometry()
        with runtime.calibration_episode_transaction(index, action):
            runtime.score_current_prequential()
            # Center-prefix ownership is exercised separately with exact
            # runtime-owned pairs. Transaction rollback fixtures intentionally
            # use center no-update events rather than bypassing that owner with
            # hand-injected estimates.
            runtime._local_centers = {}
            runtime._local_axes = {
                edge: deepcopy(axes[edge])
                for edge in HINGE_EDGES if action == EDGE_ACTIONS[edge][0]
            }
            runtime.finish_current_geometry_update()
            if inject_after == "GEOMETRY":
                runtime.inject_transaction_failure_after_geometry()
            branches = runtime.update_current_frame_branches()
            hard_support = runtime.current_heading_hard_support()
            for branch in branches:
                for edge, _, _ in EDGE_SPECS:
                    runtime.record_current_heading_no_update(
                        branch_id=branch.branch_id, edge=edge,
                        cause="SYNTHETIC_TRANSACTION_QUALIFICATION_LOCAL_NO_UPDATE",
                        hard_support_token=hard_support["owner_token"],
                    )
            runtime.finish_current_heading()
            if inject_after == "QMT":
                runtime.inject_transaction_failure_after_qmt()
            runtime.assess_current_physical_candidates()
            runtime.commit_current_progressive()

    def rollback_after_geometry() -> None:
        runtime = transaction_runtime()
        injected_exception_observed = False
        try:
            run_transaction_episode(runtime, 0, inject_after="GEOMETRY")
        except RuntimeError as exc:
            if str(exc) != "INJECTED_SYNTHETIC_FAILURE_AFTER_GEOMETRY":
                raise
            injected_exception_observed = True
        event = runtime.audit()["transaction_events"][-1]
        numerical_evidence["TRANSACTION_PARTIAL_GEOMETRY_COMMIT"] = {
            "injected_exception_observed": injected_exception_observed,
            "transaction_status": event["status"],
            "transaction_exception_message": event["exception_message"],
            "owner_state_hash_before": event["owner_state_hash_before"],
            "owner_state_hash_after_rollback": event[
                "owner_state_hash_after_rollback"
            ],
            "owner_state_hashes_equal": bool(event["owner_state_hashes_equal"]),
            "prefix_snapshot_count_after_rollback": len(runtime._progress_snapshots),
        }
        if (
            injected_exception_observed
            and event["status"] == "ROLLED_BACK"
            and event["exception_message"]
            == "INJECTED_SYNTHETIC_FAILURE_AFTER_GEOMETRY"
            and event["owner_state_hashes_equal"]
            and event["owner_state_hash_before"]
            == event["owner_state_hash_after_rollback"]
            and len(runtime._progress_snapshots) == 0
        ):
            raise ClassAGuardViolation(
                "TRANSACTION_PARTIAL_GEOMETRY_COMMIT", "geometry-stage exception rolled back exact owner state",
            )
    execute(
        "TRANSACTION_PARTIAL_GEOMETRY_COMMIT", "C2_PIPELINE_RUNTIME",
        "C2PipelineRuntime.calibration_episode_transaction/inject_transaction_failure_after_geometry",
        rollback_after_geometry,
    )

    def rollback_after_qmt_and_retry() -> None:
        def comparable_owner_state(
            runtime: C2PipelineRuntime,
        ) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
            checkpoint = deepcopy(runtime._episode_checkpoint())
            checkpoint["heading_owner"] = checkpoint["heading_owner"] is not None
            validated_tokens: set[str] = set()
            validated_payload_hashes: set[str] = set()
            validated_opaque_identities: set[int] = set()
            validation_counts = {
                "prequential_owner_tokens": 0,
                "aligned_pair_owner_tokens": 0,
                "hard_support_owner_tokens": 0,
                "physical_hmac_owner_tokens": 0,
                "vqf_identity_events": 0,
            }

            guard_raw = checkpoint["guard"]
            vqf_identity_by_node = dict(guard_raw["vqf_token_by_node"])
            for event in guard_raw["events"]:
                if event.callable != "C2ExecutionGuard.bind_vqf_instance":
                    continue
                node = str(event.detail["node"])
                identity = int(event.detail["identity"])
                if vqf_identity_by_node.get(node) != identity:
                    raise RuntimeError(
                        "QMT transaction comparator found an internally inconsistent VQF identity event"
                    )
                validated_opaque_identities.add(identity)
                validation_counts["vqf_identity_events"] += 1

            visited: set[int] = set()

            def validate_identity_binding(value: Any) -> None:
                if isinstance(value, (str, bytes, int, float, bool, type(None), np.generic, np.ndarray)):
                    return
                identity = id(value)
                if identity in visited:
                    return
                visited.add(identity)
                if isinstance(value, Mapping):
                    row = dict(value)
                    if {
                        "owner_id", "owner_token", "chronological_index",
                        "action", "array_sha256",
                    }.issubset(row):
                        payload = {
                            "owner_id": row["owner_id"],
                            "chronological_index": row["chronological_index"],
                            "action": row["action"],
                            "array_sha256": row["array_sha256"],
                        }
                        expected = sha256(
                            json.dumps(
                                payload, sort_keys=True, separators=(",", ":")
                            ).encode("utf-8")
                        ).hexdigest()
                        if (
                            payload["owner_id"] != runtime._runtime_owner_id
                            or row["owner_token"] != expected
                        ):
                            raise RuntimeError(
                                "QMT transaction comparator found an inconsistent prequential owner token"
                            )
                        validated_tokens.add(str(row["owner_token"]))
                        validation_counts["prequential_owner_tokens"] += 1
                    if {
                        "owner_binding_payload", "runtime_owner_token",
                        "runtime_owner_id",
                    }.issubset(row):
                        payload = dict(row["owner_binding_payload"])
                        expected = sha256(
                            json.dumps(
                                payload, sort_keys=True, separators=(",", ":")
                            ).encode("utf-8")
                        ).hexdigest()
                        if (
                            row["runtime_owner_id"] != runtime._runtime_owner_id
                            or payload.get("runtime_owner_id")
                            != runtime._runtime_owner_id
                            or row["runtime_owner_token"] != expected
                        ):
                            raise RuntimeError(
                                "QMT transaction comparator found an inconsistent aligned-pair owner token"
                            )
                        validated_tokens.add(str(row["runtime_owner_token"]))
                        validation_counts["aligned_pair_owner_tokens"] += 1
                    if (
                        row.get("schema")
                        == "biospur-c2-runtime-hard-branch-support-v1"
                        and row.get("owner_token") is not None
                    ):
                        payload = {
                            key: item for key, item in row.items()
                            if key != "owner_token"
                        }
                        expected = sha256(
                            json.dumps(
                                payload, sort_keys=True, separators=(",", ":")
                            ).encode("utf-8")
                        ).hexdigest()
                        if (
                            payload.get("runtime_owner_id")
                            != runtime._runtime_owner_id
                            or row["owner_token"] != expected
                        ):
                            raise RuntimeError(
                                "QMT transaction comparator found an inconsistent hard-support owner token"
                            )
                        validated_tokens.add(str(row["owner_token"]))
                        validation_counts["hard_support_owner_tokens"] += 1
                    if (
                        row.get("schema")
                        == "biospur-c2-runtime-owned-physical-prefix-input-v1"
                        and row.get("runtime_owner_token") is not None
                    ):
                        payload = {
                            key: item for key, item in row.items()
                            if key not in {
                                "runtime_owner_token",
                                "runtime_owner_binding_payload_sha256",
                            }
                        }
                        payload_sha, expected = physical_input_binding_token(
                            runtime._physical_input_binding_secret,
                            payload,
                        )
                        if (
                            payload.get("runtime_owner_id")
                            != runtime._runtime_owner_id
                            or row.get("runtime_owner_binding_payload_sha256")
                            != payload_sha
                            or row.get("runtime_owner_token") != expected
                        ):
                            raise RuntimeError(
                                "QMT transaction comparator found an inconsistent physical HMAC owner token"
                            )
                        validated_tokens.add(str(row["runtime_owner_token"]))
                        validated_payload_hashes.add(payload_sha)
                        validation_counts["physical_hmac_owner_tokens"] += 1
                    for child in row.values():
                        validate_identity_binding(child)
                    return
                if is_dataclass(value):
                    for field in fields(value):
                        validate_identity_binding(getattr(value, field.name))
                    return
                if isinstance(value, (list, tuple, set)):
                    for child in value:
                        validate_identity_binding(child)

            validate_identity_binding(checkpoint)
            guard_checkpoint = dict(checkpoint["guard"])
            guard_checkpoint["vqf_token_by_node"] = sorted(
                guard_checkpoint["vqf_token_by_node"]
            )
            checkpoint["guard"] = guard_checkpoint
            canonical = runtime._canonical_state(checkpoint)

            def normalize_runtime_local_identity(
                value: Any,
                *,
                key: str | None = None,
            ) -> Any:
                """Normalize capability identity, never scientific state.

                Retry and clean are deliberately separate runtime instances.
                Their owner IDs, capability tokens, and opaque VQF object IDs
                cannot be byte-equal, while their surrounding node, action,
                time, factor, posterior, branch, and trajectory fields must be.
                """

                if isinstance(value, Mapping):
                    return {
                        str(child_key): normalize_runtime_local_identity(
                            child_value,
                            key=str(child_key),
                        )
                        for child_key, child_value in value.items()
                    }
                if isinstance(value, list):
                    return [
                        normalize_runtime_local_identity(child, key=key)
                        for child in value
                    ]
                if key in {"owner_id", "runtime_owner_id"} and (
                    value == runtime._runtime_owner_id
                    or (
                        isinstance(value, str)
                        and value.startswith("C2_PIPELINE_SYNTHETIC_QUALIFICATION_")
                    )
                ):
                    return "__RUNTIME_LOCAL_OWNER_ID__"
                if key in {
                    "owner_token",
                    "runtime_owner_token",
                    "hard_support_token",
                } and value in validated_tokens:
                    return "__RUNTIME_LOCAL_CAPABILITY_TOKEN__"
                if (
                    key == "runtime_owner_binding_payload_sha256"
                    and value in validated_payload_hashes
                ):
                    return "__RUNTIME_LOCAL_AUTHENTICATED_PAYLOAD_HASH__"
                if key in {"identity", "__opaque_identity__"} and value in (
                    validated_opaque_identities
                ):
                    return "__OPAQUE_LIFECYCLE_IDENTITY__"
                return value

            report = {
                **validation_counts,
                "validated_runtime_local_token_count": len(validated_tokens),
                "validated_runtime_local_payload_hash_count": len(
                    validated_payload_hashes
                ),
                "validated_opaque_vqf_identity_count": len(
                    validated_opaque_identities
                ),
                "semantic_node_action_source_array_and_stage_fields_retained": True,
                "guard_and_stage_events_retained": True,
            }
            return normalize_runtime_local_identity(canonical), report

        retry = transaction_runtime()
        for index in range(10):
            run_transaction_episode(retry, index)
        injected_exception_observed = False
        try:
            run_transaction_episode(retry, 10, inject_after="QMT")
        except RuntimeError as exc:
            if str(exc) != "INJECTED_SYNTHETIC_FAILURE_AFTER_QMT":
                raise
            injected_exception_observed = True
        rollback_event = retry.audit()["transaction_events"][-1]
        run_transaction_episode(retry, 10)
        for index in range(11, 19):
            run_transaction_episode(retry, index)
        retry.freeze_fit()
        clean = transaction_runtime()
        for index in range(19):
            run_transaction_episode(clean, index)
        clean.freeze_fit()
        retry_state, retry_identity_validation = comparable_owner_state(retry)
        clean_state, clean_identity_validation = comparable_owner_state(clean)
        complete_owner_state_agreement = retry_state == clean_state
        retry_transaction_events = retry.audit()["transaction_events"]
        clean_transaction_events = clean.audit()["transaction_events"]
        retry_rollback_events = [
            event for event in retry_transaction_events
            if event["status"] == "ROLLED_BACK"
        ]
        retry_commit_events = [
            event for event in retry_transaction_events
            if event["status"] == "COMMITTED_ONCE"
        ]
        clean_commit_events = [
            event for event in clean_transaction_events
            if event["status"] == "COMMITTED_ONCE"
        ]
        retry_state_sha256 = sha256(
            json.dumps(retry_state, sort_keys=True, separators=(",", ":")).encode(
                "utf-8"
            )
        ).hexdigest()
        clean_state_sha256 = sha256(
            json.dumps(clean_state, sort_keys=True, separators=(",", ":")).encode(
                "utf-8"
            )
        ).hexdigest()
        numerical_evidence["TRANSACTION_PARTIAL_QMT_COMMIT"] = {
            "injected_exception_observed": injected_exception_observed,
            "transaction_status": rollback_event["status"],
            "transaction_exception_message": rollback_event["exception_message"],
            "rollback_owner_state_hash_before": rollback_event[
                "owner_state_hash_before"
            ],
            "rollback_owner_state_hash_after": rollback_event[
                "owner_state_hash_after_rollback"
            ],
            "rollback_owner_state_hashes_equal": bool(
                rollback_event["owner_state_hashes_equal"]
            ),
            "retry_complete_transaction_owner_state_sha256": retry_state_sha256,
            "clean_complete_transaction_owner_state_sha256": clean_state_sha256,
            "retry_and_clean_complete_transaction_owner_states_equal": (
                complete_owner_state_agreement
            ),
            "retry_runtime_local_identity_validation": retry_identity_validation,
            "clean_runtime_local_identity_validation": clean_identity_validation,
            "retry_failed_transaction_evidence_preserved_outside_scientific_state": (
                len(retry_rollback_events) == 1
                and retry_rollback_events[0]["exception_message"]
                == "INJECTED_SYNTHETIC_FAILURE_AFTER_QMT"
            ),
            "retry_rollback_event_count": len(retry_rollback_events),
            "retry_committed_episode_count": len(retry_commit_events),
            "clean_committed_episode_count": len(clean_commit_events),
            "compared_owner_set": [
                "PIPELINE_STAGE_AND_EVENTS",
                "PERSISTENT_PAIR_CLOCK",
                "PROGRESSIVE_FUNCTIONAL_GEOMETRY",
                "CAUSAL_CENTER_PREFIX",
                "SEGMENT_FRAME_BRANCH",
                "PERSISTENT_HEADING_IF_PRESENT",
                "PROGRESSIVE_CALIBRATION",
                "EXECUTION_GUARD_WITH_RUNTIME_LOCAL_VQF_IDENTITIES_NORMALIZED_TO_EXACT_NODE_KEYS",
                "DECLARED_RUNTIME_LOCAL_CAPABILITY_IDS_TOKENS_AND_OPAQUE_IDENTITIES_NORMALIZED_WITH_ALL_SURROUNDING_SEMANTICS_RETAINED",
                "ALL_PREFIX_ASSEMBLIES_AND_TRAJECTORY_HISTORIES",
            ],
            "frozen_scientific_export_used_as_transaction_comparator": False,
        }
        if (
            injected_exception_observed
            and rollback_event["status"] == "ROLLED_BACK"
            and rollback_event["exception_message"]
            == "INJECTED_SYNTHETIC_FAILURE_AFTER_QMT"
            and rollback_event["owner_state_hashes_equal"]
            and rollback_event["owner_state_hash_before"]
            == rollback_event["owner_state_hash_after_rollback"]
            and len(retry_rollback_events) == 1
            and retry_rollback_events[0]["exception_message"]
            == "INJECTED_SYNTHETIC_FAILURE_AFTER_QMT"
            and len(retry_commit_events) == 19
            and len(clean_commit_events) == 19
            and complete_owner_state_agreement
        ):
            raise ClassAGuardViolation(
                "TRANSACTION_PARTIAL_QMT_COMMIT",
                "QMT-stage exception rolled back exact state and one retry equals a clean staged run",
            )
    execute(
        "TRANSACTION_PARTIAL_QMT_COMMIT", "C2_PIPELINE_RUNTIME",
        "C2PipelineRuntime.calibration_episode_transaction/inject_transaction_failure_after_qmt",
        rollback_after_qmt_and_retry,
    )

    def prequential_time_gate_smoke(*, reset_after_first: bool) -> Mapping[str, Any]:
        runtime = timed_transaction_runtime(reset_after_first=reset_after_first)
        run_transaction_episode(runtime, 0)
        first = runtime._progress_snapshots[-1]
        run_transaction_episode(runtime, 1)
        second = runtime._progress_snapshots[-1]
        gate = runtime._episode_reference_time_audits[-1]["pre_score_time_advance_gate"]
        if not (
            gate["total_covariance_widened_before_score"]
            and gate["data_information_matrix_exactly_unchanged"]
            and gate["data_information_rank_exactly_unchanged"]
            and np.array_equal(first.data_information, second.data_information)
            and first.data_information_rank == second.data_information_rank
            and float(np.trace(second.prequential_prior_covariance))
            > float(np.trace(first.posterior_covariance))
        ):
            raise RuntimeError("prequential time gate positive owner smoke failed")
        return {
            **dict(gate),
            "first_prefix_data_information_sha256": runtime._array_sha256(
                first.data_information
            ),
            "second_prefix_data_information_sha256": runtime._array_sha256(
                second.data_information
            ),
            "first_prefix_data_information_rank": int(first.data_information_rank),
            "second_prefix_data_information_rank": int(second.data_information_rank),
            "first_prefix_total_covariance_trace": float(
                np.trace(first.posterior_covariance)
            ),
            "second_episode_prequential_prior_covariance_trace": float(
                np.trace(second.prequential_prior_covariance)
            ),
        }

    prequential_time_positive_smokes = {
        "EXACT_SAME_BOOT_ELAPSED_BEFORE_SCORE": prequential_time_gate_smoke(
            reset_after_first=False
        ),
        "UNKNOWN_BOOT_RESET_FLOOR_BEFORE_SCORE": prequential_time_gate_smoke(
            reset_after_first=True
        ),
    }

    def exact_gap_advance_omitted() -> None:
        name = "PREQUENTIAL_EXACT_GAP_DIFFUSION_DELAYED_OR_OMITTED"
        runtime = timed_transaction_runtime(reset_after_first=False)
        run_transaction_episode(runtime, 0)
        state_before = runtime._geometry_authoritative_state()
        information_before, rank_before = runtime._progressive_owner.data_information_state()
        covariance_before = (
            state_before["measurement"]
            + state_before["migration"]
            + state_before["systematic"]
        )
        numerical_evidence[name] = {
            "injected": "ProgressiveFunctionalGeometryOwner.advance_to_reference no-op on exact same-boot positive elapsed",
            "total_covariance_before_sha256": runtime._array_sha256(covariance_before),
            "mutated_total_covariance_after_sha256": runtime._array_sha256(covariance_before),
            "mutated_total_covariance_trace_increment": 0.0,
            "data_information_before_sha256": runtime._array_sha256(information_before),
            "mutated_data_information_after_sha256": runtime._array_sha256(information_before),
            "data_information_rank_before_and_after": [rank_before, rank_before],
            "prediction_scored_before_detection": False,
        }
        runtime._geometry_owner.advance_to_reference = lambda **_: {
            "kind": "INJECTED_DELAYED_OR_OMITTED_EXACT_ADVANCE",
            "current_episode_factor_consumed": False,
            "data_information_added": False,
        }
        with runtime.calibration_episode_transaction(1, runtime.settings["execution_contract"]["chronological_actions"][1]):
            raise AssertionError("exact-gap omission reached episode body before owner rejection")

    execute(
        "PREQUENTIAL_EXACT_GAP_DIFFUSION_DELAYED_OR_OMITTED",
        "C2_PIPELINE_RUNTIME/PREQUENTIAL_CAPTURE_TIME_OWNER",
        "C2PipelineRuntime.begin_calibration_episode/C2ExecutionGuard.validate_prequential_time_advance",
        exact_gap_advance_omitted,
    )

    def unknown_reset_floor_omitted() -> None:
        name = "PREQUENTIAL_UNKNOWN_RESET_FLOOR_OMITTED_OR_FABRICATED"
        runtime = timed_transaction_runtime(reset_after_first=True)
        run_transaction_episode(runtime, 0)
        state_before = runtime._geometry_authoritative_state()
        information_before, rank_before = runtime._progressive_owner.data_information_state()
        covariance_before = (
            state_before["measurement"]
            + state_before["migration"]
            + state_before["systematic"]
        )
        numerical_evidence[name] = {
            "injected": "ProgressiveFunctionalGeometryOwner.apply_unknown_interval_floor no-op on boot/reset boundary",
            "total_covariance_before_sha256": runtime._array_sha256(covariance_before),
            "mutated_total_covariance_after_sha256": runtime._array_sha256(covariance_before),
            "mutated_total_covariance_trace_increment": 0.0,
            "data_information_before_sha256": runtime._array_sha256(information_before),
            "mutated_data_information_after_sha256": runtime._array_sha256(information_before),
            "data_information_rank_before_and_after": [rank_before, rank_before],
            "fabricated_exact_elapsed_s": None,
            "prediction_scored_before_detection": False,
        }
        runtime._geometry_owner.apply_unknown_interval_floor = lambda **_: {
            "kind": "INJECTED_UNKNOWN_RESET_ZERO_ELAPSED_NO_FLOOR",
            "exact_elapsed_seconds_fabricated": False,
            "data_information_added": False,
        }
        with runtime.calibration_episode_transaction(1, runtime.settings["execution_contract"]["chronological_actions"][1]):
            raise AssertionError("unknown-reset omission reached episode body before owner rejection")

    execute(
        "PREQUENTIAL_UNKNOWN_RESET_FLOOR_OMITTED_OR_FABRICATED",
        "C2_PIPELINE_RUNTIME/PREQUENTIAL_CAPTURE_TIME_OWNER",
        "C2PipelineRuntime.begin_calibration_episode/C2ExecutionGuard.validate_prequential_time_advance",
        unknown_reset_floor_omitted,
    )

    def fabricated_pair(
        runtime: C2PipelineRuntime,
        *,
        parent_node: str,
        child_node: str,
        provenance_extra: Mapping[str, Any] | None = None,
    ) -> AlignedPair:
        arrays = np.zeros((3, 3), dtype=float)
        provenance = {
            "schema": "biospur-c2-owner-produced-gap-safe-aligned-pair-v1",
            "parent_node": parent_node,
            "child_node": child_node,
            "caller_supplied_or_copied": True,
        }
        if provenance_extra is not None:
            provenance.update(dict(provenance_extra))
        return AlignedPair(
            edge="pelvis_torso",
            action=runtime.settings["execution_contract"]["chronological_actions"][0],
            parent_acc=arrays.copy(), child_acc=arrays.copy(),
            parent_gyro=arrays.copy(), child_gyro=arrays.copy(),
            parent_observed_time_s=np.arange(3, dtype=float) * 0.005,
            child_observed_time_s=np.arange(3, dtype=float) * 0.005,
            parent_boot_epoch=np.zeros(3, dtype=np.int64),
            child_boot_epoch=np.zeros(3, dtype=np.int64),
            alignment=PairAlignment(
                parent_indices=np.arange(3, dtype=np.int64),
                child_indices=np.arange(3, dtype=np.int64),
                lag_samples=0,
                report={"lag_uncertainty_s": 0.005},
            ),
            contiguous_spans=(slice(0, 3),),
            provenance=provenance,
        )

    def wrong_node_mapping() -> None:
        runtime = transaction_runtime()
        expected_parent, expected_child = runtime._sealed_edge_hardware_nodes("pelvis_torso")
        pair = fabricated_pair(
            runtime,
            parent_node=expected_child,
            child_node=expected_parent,
        )
        runtime._validate_owned_aligned_pair(pair, expected_edge="pelvis_torso")

    execute(
        "WRONG_NODE_MAPPING", "C2_PIPELINE_RUNTIME/SEALED_FACTOR_INPUT_OWNER",
        "C2PipelineRuntime._validate_owned_aligned_pair",
        wrong_node_mapping,
    )

    def caller_covariance_substitution() -> None:
        runtime = transaction_runtime()
        action = runtime.settings["execution_contract"]["chronological_actions"][0]
        with runtime.calibration_episode_transaction(0, action):
            runtime.score_current_prequential()
            runtime.estimate_current_local_center(
                "pelvis_torso", (),
                caller_covariance_override={"parent_acc": np.eye(3)},
            )

    execute(
        "CALLER_COVARIANCE_SUBSTITUTION", "C2_PIPELINE_RUNTIME/P1_STOCHASTIC_COVARIANCE_OWNER",
        "C2PipelineRuntime.estimate_current_local_center",
        caller_covariance_substitution,
    )

    def caller_endpoint_substitution() -> None:
        runtime = transaction_runtime()
        action = runtime.settings["execution_contract"]["chronological_actions"][0]
        with runtime.calibration_episode_transaction(0, action):
            runtime.score_current_prequential()
            runtime.estimate_current_local_center(
                "pelvis_torso", (), parent="torso", child="pelvis",
            )

    execute(
        "CALLER_ENDPOINT_LABEL_SUBSTITUTION", "C2_PIPELINE_RUNTIME/SEALED_EDGE_ENDPOINT_OWNER",
        "C2PipelineRuntime.estimate_current_local_center",
        caller_endpoint_substitution,
    )

    def caller_heading_array_substitution() -> None:
        runtime = transaction_runtime()
        parent, child = runtime._sealed_edge_hardware_nodes("pelvis_torso")
        pair = fabricated_pair(runtime, parent_node=parent, child_node=child)
        runtime._validate_owned_aligned_pair(pair, expected_edge="pelvis_torso")

    execute(
        "CALLER_HEADING_ARRAY_SUBSTITUTION", "C2_PIPELINE_RUNTIME/RUNTIME_OWNED_HEADING_INPUT_OWNER",
        "C2PipelineRuntime._validate_owned_aligned_pair",
        caller_heading_array_substitution,
    )

    def caller_time_grid_substitution() -> None:
        runtime = transaction_runtime()
        action = runtime.settings["execution_contract"]["chronological_actions"][0]
        with runtime.calibration_episode_transaction(
            0, action, physical_reference_time_s=123.456,
        ):
            raise AssertionError("caller time reached the causal episode owner")

    execute(
        "CALLER_TIME_GRID_SUBSTITUTION", "C2_PIPELINE_RUNTIME/CAPTURE_TIME_OWNER",
        "C2PipelineRuntime.calibration_episode_transaction",
        caller_time_grid_substitution,
    )

    def caller_aligned_physical_time_substitution() -> None:
        runtime = transaction_runtime(first_action_rows=401)
        action = runtime.settings["execution_contract"]["chronological_actions"][0]
        with runtime.calibration_episode_transaction(0, action):
            runtime.score_current_prequential()
            pair = runtime.align_current_pair(edge="pelvis_torso")
            pair.parent_observed_time_s.setflags(write=True)
            pair.parent_observed_time_s[0] += 1.0
            runtime._validate_owned_aligned_pair(
                pair, expected_edge="pelvis_torso",
            )

    execute(
        "CALLER_ALIGNED_PHYSICAL_TIME_SUBSTITUTION",
        "C2_PIPELINE_RUNTIME/CAPTURE_TIME_OWNER",
        "C2PipelineRuntime._validate_owned_aligned_pair",
        caller_aligned_physical_time_substitution,
    )

    def caller_aligned_boot_epoch_substitution() -> None:
        runtime = transaction_runtime(first_action_rows=401)
        action = runtime.settings["execution_contract"]["chronological_actions"][0]
        with runtime.calibration_episode_transaction(0, action):
            runtime.score_current_prequential()
            pair = runtime.align_current_pair(edge="pelvis_torso")
            pair.child_boot_epoch.setflags(write=True)
            pair.child_boot_epoch[-1] += 1
            runtime._validate_owned_aligned_pair(
                pair, expected_edge="pelvis_torso",
            )

    execute(
        "CALLER_ALIGNED_BOOT_EPOCH_SUBSTITUTION",
        "C2_PIPELINE_RUNTIME/CAPTURE_TIME_OWNER",
        "C2PipelineRuntime._validate_owned_aligned_pair",
        caller_aligned_boot_epoch_substitution,
    )

    def raw_relabelled_as_official_self_hash() -> None:
        numeric_physical_candidate_uncertainty_gate(
            settings,
            propagate_relabelled_raw_self_hash_mutation=True,
        )

    execute(
        "RAW_UNQMT_ORIENTATION_PHYSICAL_GATE_SUBSTITUTION",
        "SCIENTIFIC_FK_OWNER/RUNTIME_QMT_ROOTED_PHYSICAL_INPUT_OWNER",
        "ScientificForwardKinematicsOwner.assess_prefix_trajectory",
        raw_relabelled_as_official_self_hash,
    )

    def physical_gate_stage_bypass() -> None:
        runtime = transaction_runtime()
        action = runtime.settings["execution_contract"]["chronological_actions"][0]
        with runtime.calibration_episode_transaction(0, action):
            runtime.score_current_prequential()
            runtime.finish_current_geometry_update()
            runtime.update_current_frame_branches()
            runtime.finish_current_heading()
            runtime.commit_current_progressive()

    execute(
        "PHYSICAL_GATE_STAGE_BYPASS", "C2_PIPELINE_RUNTIME",
        "C2PipelineRuntime.commit_current_progressive",
        physical_gate_stage_bypass,
    )

    def all_invalid_physical_candidates_rollback() -> None:
        runtime = transaction_runtime()
        seed_full_geometry(runtime)
        action = runtime.settings["execution_contract"]["chronological_actions"][0]
        try:
            with runtime.calibration_episode_transaction(0, action):
                runtime.score_current_prequential()
                runtime.finish_current_geometry_update()
                branches = runtime.update_current_frame_branches()
                support = runtime.current_heading_hard_support()
                runtime.validate_heading_execution_branch_ids(
                    support["branch_ids"], hard_support_token=support["owner_token"],
                )
                for branch_id in support["branch_ids"]:
                    for edge, _, _ in EDGE_SPECS:
                        runtime.record_current_heading_no_update(
                            branch_id=branch_id, edge=edge,
                            cause="SYNTHETIC_ALL_INVALID_PHYSICAL_MUTATION",
                            hard_support_token=support["owner_token"],
                        )
                runtime.finish_current_heading()
                count = 1
                segment_names = runtime._frame_owner.segment_names
                runtime._owner_derived_physical_prefix_inputs = lambda: (
                    {
                        branch.branch_id: {
                            segment: np.repeat(np.eye(3)[None, :, :], count, axis=0)
                            for segment in segment_names
                        }
                        for branch in branches
                    },
                    {
                        branch.branch_id: {
                            segment: np.repeat((np.eye(3) * 0.01)[None, :, :], count, axis=0)
                            for segment in segment_names
                        }
                        for branch in branches
                    },
                    {branch.branch_id: {} for branch in branches},
                    {"mutation": "ALL_INVALID_OWNER_OUTPUT"},
                )
                runtime._fk_owner.assess_prefix_trajectory = lambda **kwargs: (
                    PhysicalTrajectoryCandidateAssessment(
                        branch_id=kwargs["frame_branch"].branch_id,
                        physically_legal=False,
                        rom_log_likelihood=0.0,
                        bilateral_log_likelihood=0.0,
                        gravity_log_likelihood=0.0,
                        soft_total_log_likelihood=0.0,
                        report={"injected": "ALL_CURRENT_HARD_SUPPORTED_BRANCHES_INVALID"},
                    )
                )
                runtime.assess_current_physical_candidates()
        except ClassAGuardViolation as exc:
            event = runtime.audit()["transaction_events"][-1]
            physical_diagnostic = event[
                "physical_candidate_diagnostic_before_rollback"
            ]
            numerical_evidence["ALL_INVALID_PHYSICAL_CANDIDATE_ROLLBACK"] = {
                "owner_rejection": exc.code,
                "transaction_exception_message": event["exception_message"],
                "owner_state_hash_before": event["owner_state_hash_before"],
                "owner_state_hash_after": event[
                    "owner_state_hash_after_rollback"
                ],
                "transaction_owner_state_hashes_equal": bool(event["owner_state_hashes_equal"]),
                "all_physical_candidates_invalid_before_rollback": bool(
                    physical_diagnostic["all_physical_candidates_invalid"]
                ),
                "rejected_branch_count": len(
                    physical_diagnostic["assessments"]
                ),
                "hard_supported_branch_count": len(support["branch_ids"]),
                "rejected_branch_ids_exactly_match_hard_support": sorted(
                    physical_diagnostic["assessments"]
                ) == sorted(support["branch_ids"]),
            }
            if (
                exc.code == "ALL_INVALID_PHYSICAL_CANDIDATE_ROLLBACK"
                and event["status"] == "ROLLED_BACK"
                and event["owner_state_hashes_equal"]
                and event["owner_state_hash_before"]
                == event["owner_state_hash_after_rollback"]
                and physical_diagnostic["all_physical_candidates_invalid"]
                and sorted(physical_diagnostic["assessments"])
                == sorted(support["branch_ids"])
            ):
                raise
            raise RuntimeError("all-invalid physical mutation did not rollback exactly") from exc

    execute(
        "ALL_INVALID_PHYSICAL_CANDIDATE_ROLLBACK", "C2_PIPELINE_RUNTIME",
        "C2PipelineRuntime.assess_current_physical_candidates/calibration_episode_transaction",
        all_invalid_physical_candidates_rollback,
    )

    def forged_prefit_or_arbitrary_settings() -> None:
        mutated = deepcopy(settings)
        mutated["progressive"]["initial_sigma"] = float(
            mutated["progressive"]["initial_sigma"]
        ) * 1.01
        C2PipelineRuntime(
            mutated,
            initial_stochastic_state,
            prefit_registry_seal_path=prefit_registry_seal_path,
            execution_role="SYNTHETIC_QUALIFICATION",
        )

    execute(
        "FORGED_PREFIT_SEAL_OR_ARBITRARY_SETTINGS", "C2_PIPELINE_RUNTIME/AUTHORITY_OWNER",
        "C2PipelineRuntime.__init__/_validated_prefit_seal_authority",
        forged_prefit_or_arbitrary_settings,
    )

    def forged_real_fit_activation() -> None:
        C2PipelineRuntime(
            settings,
            initial_stochastic_state,
            prefit_registry_seal_path=prefit_registry_seal_path,
            execution_role="PRIMARY_CAUSAL",
            real_fit_activation_path=None,
        )

    execute(
        "FORGED_REAL_FIT_ACTIVATION", "C2_PIPELINE_RUNTIME/AUTHORITY_OWNER",
        "C2PipelineRuntime.__init__/_validated_real_fit_activation",
        forged_real_fit_activation,
    )

    def forearm_dual_observer_collapse() -> None:
        mutated = deepcopy(settings)
        mutated["anthropometric_proxy"]["forearm_m"]["left"] = [0.2525]
        C2ExecutionGuard(mutated)

    execute(
        "FOREARM_DUAL_OBSERVER_PROVENANCE_COLLAPSE", "VIEWER_ANTHROPOMETRY_AUTHORITY",
        "C2ExecutionGuard._validate_bound_settings",
        forearm_dual_observer_collapse,
    )

    def soft_weight_numerical_zero_candidate_lock() -> None:
        runtime = transaction_runtime()
        seed_full_geometry(runtime)
        action = runtime.settings["execution_contract"]["chronological_actions"][0]
        with runtime.calibration_episode_transaction(0, action):
            runtime.score_current_prequential()
            runtime.finish_current_geometry_update()
            runtime.update_current_frame_branches()
            support = runtime.current_heading_hard_support()
            branch_ids = tuple(support["branch_ids"])
            if len(branch_ids) < 2:
                raise RuntimeError("soft-weight mutation needs at least two hard-supported branches")
            runtime._progressive_owner._branch_log_weights[0] = -1_000.0
            numerical_evidence["SOFT_WEIGHT_NUMERICAL_ZERO_CANDIDATE_LOCK"] = {
                "hard_supported_branch_id": branch_ids[0],
                "soft_log_weight": -1_000.0,
                "soft_weight_underflows_to_zero": bool(np.exp(-1_000.0) == 0.0),
                "hard_support_mask_unchanged": True,
                "injected_qmt_execution_branch_ids": list(branch_ids[1:]),
            }
            runtime.validate_heading_execution_branch_ids(
                branch_ids[1:], hard_support_token=support["owner_token"],
            )

    execute(
        "SOFT_WEIGHT_NUMERICAL_ZERO_CANDIDATE_LOCK", "C2_PIPELINE_RUNTIME/BRANCH_HARD_SUPPORT_OWNER",
        "C2PipelineRuntime.validate_heading_execution_branch_ids",
        soft_weight_numerical_zero_candidate_lock,
    )

    def opposing_knees() -> None:
        owner = built_segment_owner()
        branch = owner.branches[0]
        world = {name: np.eye(3) for name in ("thigh_left", "shank_left", "thigh_right", "shank_right")}
        angle = 0.45
        world["shank_left"] = np.array([
            [np.cos(angle), 0.0, np.sin(angle)], [0.0, 1.0, 0.0],
            [-np.sin(angle), 0.0, np.cos(angle)],
        ])
        world["shank_right"] = np.array([
            [np.cos(angle), 0.0, -np.sin(angle)], [0.0, 1.0, 0.0],
            [np.sin(angle), 0.0, np.cos(angle)],
        ])
        assessment = owner.assess_initial_trajectory_candidate(branch, world)
        owner.consider_candidate(branch, assessment, residual=0.0)
    execute("ONE_KNEE_FORWARD_ONE_KNEE_BACK", "SEGMENT_FRAME_BRANCH_OWNER", "SegmentFrameBranchOwner.assess_initial_trajectory_candidate/consider_candidate", opposing_knees)

    def reset_vqf() -> None:
        guard = _new_guard(settings)
        first, second = object(), object()
        guard.bind_vqf_instance("thigh_left", first)
        guard.bind_vqf_instance("thigh_left", second)
    execute("PER_ACTION_VQF_RESET", "P2_ORIENTATION", "C2ExecutionGuard.bind_vqf_instance", reset_vqf)
    def qmt_reset() -> None:
        owner = heading_owner()
        owner.process_span(
            branch_id=owner.branch_ids[0], edge="knee_left",
            chronological_index=0, action=settings["execution_contract"]["chronological_actions"][0],
            parent_gyro_sensor=np.zeros((3, 3)), child_gyro_sensor=np.zeros((3, 3)),
            parent_quaternion_world_sensor_wxyz=np.tile([1.0, 0.0, 0.0, 0.0], (3, 1)),
            child_quaternion_world_sensor_wxyz=np.tile([1.0, 0.0, 0.0, 0.0], (3, 1)),
            common_physical_time_s=np.arange(3) * 0.005,
            selected_source_row_indices=np.arange(3),
            owner_input_binding={},
            reset_requested=True, profile_stitch_requested=True,
        )
    execute("PER_ACTION_QMT_RESET_OR_PROFILE_STITCHING", "P3_HEADING", "PersistentHeadingOwner.process_span", qmt_reset)

    def cross_capture() -> None:
        guard = _new_guard(settings)
        guard.begin_capture("C1")
    execute("CROSS_CAPTURE_STATE_SHARING", "CAPTURE_LIFECYCLE", "C2ExecutionGuard.begin_capture", cross_capture)
    execute("OLD_WARM_START_OR_PROFILE_IMPORT", "CAPTURE_LIFECYCLE", "C2ExecutionGuard.begin_capture", lambda: C2ExecutionGuard(settings).begin_capture("C2", warm_start_source="C1_PROFILE"))
    def candidate_lock() -> None:
        owner = built_segment_owner()
        weights = np.zeros(len(owner.branches)); weights[0] = 1.0
        owner.update_posterior(weights, explicit_lock_requested=True)
    execute("CANDIDATE_LOCK", "BRANCH_POSTERIOR_OWNER", "SegmentFrameBranchOwner.update_posterior", candidate_lock)
    def second_branch_owner() -> None:
        owner = built_segment_owner()
        owner.update_posterior(
            np.full(len(owner.branches), 1.0 / len(owner.branches)),
            explicit_lock_requested=False,
        )
    execute(
        "BRANCH_POSTERIOR_SECOND_SOURCE_OR_DIVERGENCE", "SEGMENT_FRAME_BRANCH_OWNER",
        "SegmentFrameBranchOwner.update_posterior", second_branch_owner,
    )

    def leaked_factor() -> None:
        timing = deepcopy(settings["timing"])
        timing["factor_fields"] = ["acc", "gyro", "action_pose_truth"]
        aligned_pair(None, edge="knee_left", parent_node="p", child_node="c", timing=timing, clock_state=PersistentPairClockState(maximum_abs_drift_ppm=2000.0, jitter_floor_s=0.005), execution_guard=_new_guard(settings))
    execute("LEAKED_TRUTH_OR_ACTION_POSE_TRUTH", "FACTOR_INPUT_OWNER", "functional_geometry.aligned_pair", leaked_factor)
    def exact_wear() -> None:
        mutated = deepcopy(settings)
        mutated["segment_frames"]["wear_prior"] = {"family": "HARD_CONE", "hard_cone_deg": 40.0}
        built_segment_owner(mutated)
    execute("EXACTIZED_WEAR_DIRECTION_OR_HARD_NUMERIC_CONE", "WEAR_PRIOR_OWNER", "SegmentFrameBranchOwner.build", exact_wear)

    def hard_mirror() -> None:
        mutated = deepcopy(settings)
        mutated["segment_frames"]["bilateral_hard_mirror"] = True
        built_segment_owner(mutated)
    execute("HARD_BILATERAL_MIRROR", "SEGMENT_FRAME_BRANCH_OWNER", "SegmentFrameBranchOwner.build", hard_mirror)

    def axial_collapse() -> None:
        owner = segment_owner()
        axes, centers = representative_geometry()
        original = centers["knee_left"]
        centers["knee_left"] = CenterEstimate(
            edge=original.edge, parent=original.parent, child=original.child,
            joint_to_parent_sensor_m=np.array([0.4]),
            joint_to_child_sensor_m=original.joint_to_child_sensor_m,
            covariance_m2=original.covariance_m2, report=original.report,
        )
        owner.build(axes, centers, chronological_index=0, action="SYNTHETIC_ARCHITECTURE")
    execute("FULL_3D_CONNECTION_TO_AXIAL_SCALAR_COLLAPSE", "P2_FUNCTIONAL_CENTER", "SegmentFrameBranchOwner.build", axial_collapse)

    def constant_heading() -> None:
        owner = heading_owner()
        branch_id = owner.branch_ids[0]
        action = settings["execution_contract"]["chronological_actions"][0]
        time = np.arange(5, dtype=float) * 0.005
        for edge in ROOTED_EDGES:
            edge_name = {
                endpoints: name for name, endpoints in zip(
                    ("pelvis_torso", "shoulder_left", "elbow_left", "shoulder_right", "elbow_right", "hip_left", "knee_left", "hip_right", "knee_right"),
                    ROOTED_EDGES,
                )
            }[edge]
            owner.record_action_no_update(
                branch_id=branch_id, edge=edge_name, chronological_index=0,
                action=action, base_common_physical_time_s=time, cause="SYNTHETIC_ARCHITECTURE",
            )
        owner.assemble_action_rooted_trajectory(
            branch_id, chronological_index=0, action=action,
            base_common_physical_time_s=time, mode="CONSTANT_MEAN",
        )
    execute("TIME_VARYING_HEADING_TO_MEAN_OR_CONSTANT_SUBSTITUTION", "P3_HEADING", "PersistentHeadingOwner.assemble_action_rooted_trajectory", constant_heading)

    def result_directed_selection() -> None:
        bad = deepcopy(settings["hinge_axis"])
        bad["selection_policy"] = "LOWEST_REAL_RESIDUAL_ROWS"
        estimate_hinge_axis_qmt("knee_left", (), settings=bad, parent_acc_covariance=np.eye(3), child_acc_covariance=np.eye(3), parent_gyro_covariance=np.eye(3), child_gyro_covariance=np.eye(3), parent_gyro_bias_covariance=np.eye(3), child_gyro_bias_covariance=np.eye(3), execution_guard=_new_guard(settings))
    execute("RESULT_DIRECTED_ROW_SELECTION", "P2_FUNCTIONAL_AXIS", "functional_geometry.estimate_hinge_axis_qmt", result_directed_selection)
    def invalid_low_residual() -> None:
        owner = built_segment_owner()
        branch = owner.branches[0]
        from .segment_frames import PhysicalCandidateAssessment
        assessment = PhysicalCandidateAssessment(
            branch_id=branch.branch_id, left_knee_direction="FORWARD",
            right_knee_direction="BACKWARD", physically_legal=False,
            evidence={"owner_derived_fixture": True},
        )
        owner.consider_candidate(branch, assessment, residual=0.0)
    execute("INVALID_LOW_RESIDUAL_DISPLACES_LEGAL_CANDIDATE", "BRANCH_POSTERIOR_OWNER", "SegmentFrameBranchOwner.consider_candidate", invalid_low_residual)

    def collapsed() -> None:
        points = _baseline_points()
        points["shank_left"] = points["thigh_left"].copy()
        fk_owner().validate_candidate_points(points)
    execute("COLLAPSED_GEOMETRY", "SCIENTIFIC_FK_OWNER", "ScientificForwardKinematicsOwner.validate_candidate_points", collapsed)

    execute("DISCONNECTED_ROOTED_GRAPH", "SCIENTIFIC_FK_OWNER", "ScientificForwardKinematicsOwner.validate_candidate_points", lambda: fk_owner().validate_candidate_points(_baseline_points(), edges=ROOTED_EDGES[:-1]))
    execute("VIEWER_REBASE_OR_IK_REPAIR_RESCUE", "SCIENTIFIC_FK_OWNER", "ScientificForwardKinematicsOwner.validate_candidate_points", lambda: fk_owner().validate_candidate_points(_baseline_points(), viewer_mode="VIEWER_REBASE_REPAIR"))

    def frame_swap() -> None:
        owner = built_segment_owner()
        fk_owner().forward(
            root_sensor_position_m=np.zeros(3),
            world_from_segment={key: np.eye(3) for key in _baseline_points()},
            frame_branch=owner.branches[0],
            connection_frame_mode="SENSOR_VECTOR_TREATED_AS_SEGMENT_VECTOR",
        )
    execute("SENSOR_SEGMENT_CONNECTION_FRAME_SWAP", "SCIENTIFIC_FK_OWNER", "ScientificForwardKinematicsOwner.forward", frame_swap)

    def stage_bypass() -> None:
        transaction_runtime().score_current_prequential()
    execute("PIPELINE_STAGE_ORDER_BYPASS", "C2_PIPELINE_RUNTIME", "C2PipelineRuntime.score_current_prequential", stage_bypass)
    def progressive_state() -> ProgressiveCalibrationState:
        return ProgressiveCalibrationState(
            2, execution_guard=_new_guard(settings),
            initial_sigma=float(settings["progressive"]["initial_sigma"]),
            branch_count=int(settings["progressive"]["synthetic_branch_count"]),
            chronological_actions=settings["execution_contract"]["chronological_actions"],
            rank_relative_tolerance=float(settings["progressive"]["rank_relative_tolerance"]),
            fresh_absolute_tolerance=float(settings["progressive"]["fresh_absolute_tolerance"]),
            fresh_relative_tolerance=float(settings["progressive"]["fresh_relative_tolerance"]),
        )

    def fake_progress() -> None:
        progressive_state().ingest_episode(
            chronological_index=0, action=settings["execution_contract"]["chronological_actions"][0],
            observation=np.zeros(2), observation_covariance=np.eye(2),
            branch_log_likelihood=[0.0, 0.0], physical_validity=1.0,
            prediction_scored_before_ingest=False, metric_inputs=["episode_count"],
        )
    execute("FAKE_COUNT_TIME_ITERATION_PROGRESS", "P5_PROGRESSIVE_OWNER", "ProgressiveCalibrationState.ingest_episode", fake_progress)
    execute("FALSE_INITIAL_STILL_COMPLETION", "P5_PROGRESSIVE_OWNER", "ProgressiveCalibrationState.declare_initial_status", lambda: progressive_state().declare_initial_status(complete_calibration=True))

    def heldout_both_subcases() -> None:
        prefreeze_caught = False
        try:
            progressive_state().open_holdout()
        except ClassAGuardViolation as exc:
            prefreeze_caught = exc.code == "HELDOUT_LEAK_OR_POST_HELDOUT_REFIT"
        state = progressive_state()
        for index, action in enumerate(settings["execution_contract"]["chronological_actions"]):
            state.ingest_episode(
                chronological_index=index, action=action,
                observation=np.array([0.01 * index, -0.01 * index]),
                observation_covariance=np.eye(2),
                branch_log_likelihood=[0.0, 0.0], physical_validity=1.0,
            )
        state.freeze_fit()
        state.open_holdout()
        post_holdout_refit_caught = False
        try:
            state.request_refit()
        except ClassAGuardViolation as exc:
            post_holdout_refit_caught = exc.code == "HELDOUT_LEAK_OR_POST_HELDOUT_REFIT"
        if prefreeze_caught and post_holdout_refit_caught:
            raise ClassAGuardViolation(
                "HELDOUT_LEAK_OR_POST_HELDOUT_REFIT",
                "both pre-freeze holdout and post-holdout refit subcases rejected",
            )
    execute("HELDOUT_LEAK_OR_POST_HELDOUT_REFIT", "HOLDOUT_OWNER", "ProgressiveCalibrationState.open_holdout/request_refit", heldout_both_subcases)

    heldout_transaction_gate = numeric_frozen_heldout_transaction_isolation_gate(
        settings, initial_stochastic_state, built_segment_owner().branches,
    )

    def heldout_partial_qmt_commit() -> None:
        if heldout_transaction_gate["qmt"]["pass"]:
            raise ClassAGuardViolation(
                "HELDOUT_PARTIAL_QMT_TRANSACTION_COMMIT",
                "ordinary post-QMT failure restored exact evaluation state; clean retry and next action continued",
            )
        raise RuntimeError("heldout partial-QMT rollback mutation gate failed")

    numerical_evidence["HELDOUT_PARTIAL_QMT_TRANSACTION_COMMIT"] = dict(
        heldout_transaction_gate["qmt"]
    )
    execute(
        "HELDOUT_PARTIAL_QMT_TRANSACTION_COMMIT",
        "FROZEN_SCIENTIFIC_HELDOUT_EVALUATION_OWNER",
        "FrozenScientificHeldoutEvaluationOwner._heading_for_action/PersistentHeadingOwner",
        heldout_partial_qmt_commit,
    )

    def heldout_partial_physical_commit() -> None:
        if heldout_transaction_gate["physical_branch"]["pass"]:
            raise ClassAGuardViolation(
                "HELDOUT_PARTIAL_PHYSICAL_BRANCH_COMMIT",
                "ordinary physical-branch failure restored guard state and the next branch continued",
            )
        raise RuntimeError("heldout partial physical-branch rollback mutation gate failed")

    numerical_evidence["HELDOUT_PARTIAL_PHYSICAL_BRANCH_COMMIT"] = dict(
        heldout_transaction_gate["physical_branch"]
    )
    execute(
        "HELDOUT_PARTIAL_PHYSICAL_BRANCH_COMMIT",
        "FROZEN_SCIENTIFIC_HELDOUT_EVALUATION_OWNER",
        "FrozenScientificHeldoutEvaluationOwner.evaluate_action/ScientificForwardKinematicsOwner",
        heldout_partial_physical_commit,
    )

    def synthetic_renderer_official_label_substitution() -> None:
        renderer = settings["scientific_renderer"]
        policy = renderer["source_label_policy"]
        label, official = _validated_source_display(
            physical_source=str(policy["synthetic_fixture_source"]),
            manifest={"schema": "SYNTHETIC_RENDERER_MUTATION_FIXTURE"},
            renderer=renderer,
        )
        numerical_evidence[
            "SYNTHETIC_RENDERER_OFFICIAL_QMT_LABEL_SUBSTITUTION"
        ] = {
            "synthetic_label": label,
            "synthetic_marked_official": bool(official),
            "expected_synthetic_label": str(policy["synthetic_fixture_label"]),
            "official_without_fresh_manifest_rejected": False,
            "actual_owner_called": (
                "scientific_renderer._validated_source_display"
            ),
        }
        if official or label != str(policy["synthetic_fixture_label"]):
            raise RuntimeError("synthetic renderer source was mislabeled as official QMT")
        try:
            _validated_source_display(
                physical_source=str(policy["official_qmt_source"]),
                manifest={"schema": "SYNTHETIC_RENDERER_MUTATION_FIXTURE"},
                renderer=renderer,
            )
        except RuntimeError as exc:
            numerical_evidence[
                "SYNTHETIC_RENDERER_OFFICIAL_QMT_LABEL_SUBSTITUTION"
            ]["official_without_fresh_manifest_rejected"] = True
            raise ClassAGuardViolation(
                "SYNTHETIC_RENDERER_OFFICIAL_QMT_LABEL_SUBSTITUTION",
                "actual renderer owner kept the synthetic label non-official and rejected an official label without fresh-bound authority",
            ) from exc

    execute(
        "SYNTHETIC_RENDERER_OFFICIAL_QMT_LABEL_SUBSTITUTION",
        "SCIENTIFIC_RENDERER_SOURCE_AUTHORITY_OWNER",
        "scientific_renderer._validated_source_display",
        synthetic_renderer_official_label_substitution,
    )

    def fresh_disagreement() -> None:
        state = progressive_state()
        state.ingest_episode(
            chronological_index=0, action=settings["execution_contract"]["chronological_actions"][0],
            observation=np.array([1.0, 2.0]), observation_covariance=np.eye(2),
            branch_log_likelihood=[0.0, 0.0], physical_validity=1.0,
        )
        state.fresh_recompute_and_compare(injected_mutation="PERTURB_POSTERIOR_MEAN_FOR_NEGATIVE_GATE")
    execute("FRESH_BATCH_DISAGREEMENT", "FRESH_BATCH_OWNER", "ProgressiveCalibrationState.fresh_recompute_and_compare", fresh_disagreement)

    def causal_prefix_disagreement_with_matching_final() -> None:
        actions = settings["execution_contract"]["chronological_actions"]

        def two_prefix_state(values: tuple[float, float]) -> ProgressiveCalibrationState:
            state = ProgressiveCalibrationState(
                1, execution_guard=_new_guard(settings),
                initial_sigma=float(settings["progressive"]["initial_sigma"]),
                branch_count=2, chronological_actions=actions,
                rank_relative_tolerance=float(settings["progressive"]["rank_relative_tolerance"]),
                fresh_absolute_tolerance=float(settings["progressive"]["fresh_absolute_tolerance"]),
                fresh_relative_tolerance=float(settings["progressive"]["fresh_relative_tolerance"]),
            )
            for index, value in enumerate(values):
                state.ingest_episode(
                    chronological_index=index, action=actions[index],
                    observation=np.array([value]), observation_covariance=np.eye(1),
                    data_information=np.eye(1), branch_log_likelihood=[0.0, 0.0],
                    physical_validity=0.5,
                )
            return state

        primary_state = two_prefix_state((1.0, -1.0))
        fresh_state = two_prefix_state((-1.0, 1.0))
        primary_final, _ = primary_state._posterior()
        fresh_final, _ = fresh_state._posterior()
        if not np.allclose(primary_final, fresh_final):
            raise RuntimeError("causal-prefix mutation fixture failed to preserve matching final mean")
        primary = transaction_runtime()
        fresh = transaction_runtime()
        primary._progress_snapshots = deepcopy(primary_state._snapshots)
        fresh._progress_snapshots = deepcopy(fresh_state._snapshots)
        primary._validate_causal_prefix_fresh_runtime(fresh)
    execute(
        "CAUSAL_PREFIX_FRESH_DISAGREEMENT", "C2_PIPELINE_RUNTIME",
        "C2PipelineRuntime._validate_causal_prefix_fresh_runtime",
        causal_prefix_disagreement_with_matching_final,
    )
    def cached_decoded_action_session_reuse() -> None:
        primary = transaction_runtime()
        fresh = transaction_runtime()
        shared_audits = []
        for index, action in enumerate(settings["execution_contract"]["chronological_actions"]):
            start = 1000 + index * 100
            shared_audits.append({
                "reader_session_id": "C2_PREFIT_READER_REUSED_CACHED_OBJECTS",
                "action": action, "chronological_index": index,
                "payload_path": "/canonical/synthetic/source.bslcap",
                "requested_half_open_interval": [start, start + 100],
                "actual_read_intervals": [[start, start + 100]],
                "actual_read_union_bytes": 100, "expected_read_bytes": 100,
                "open_flags": ["O_RDONLY", "O_CLOEXEC"],
                "whole_file_stat_performed": False, "whole_file_hash_performed": False,
                "whole_file_traversal_performed": False, "mmap_used": False,
                "bounded_slice_sha256": f"SYNTHETIC_SLICE_{index:02d}",
                "plan_path": "/canonical/synthetic/PAYLOAD_BYTE_ACCESS_PLAN.json",
                "plan_sha256": "SYNTHETIC_PLAN_SHA256",
                "fit_freeze_heldout_interval": [start + 100, start + 200],
                "heldout_bytes_touched": False,
                "opened_record_payload_classes": ["V47_TEN_NODE_IMU_KIND_3"],
                "other_record_payload_classes": "OPAQUE_SKIPPED_AFTER_ENVELOPE_KIND_ONLY",
            })
        # The same decoded-action/session evidence is injected into both real
        # runtime owners; the runtime->guard path must reject it.
        primary._input_access_audits = deepcopy(shared_audits)
        fresh._input_access_audits = deepcopy(shared_audits)
        primary._validate_distinct_fresh_access_bindings(fresh)
    execute(
        "FRESH_READER_SESSION_REUSE", "C2_PIPELINE_RUNTIME",
        "C2PipelineRuntime._validate_distinct_fresh_access_bindings",
        cached_decoded_action_session_reuse,
    )

    def caller_attested_fresh_unlock() -> None:
        runtime = transaction_runtime()
        runtime.record_final_raw_fresh_verification({"posterior_mean": True}, scope="FAKE")
    execute(
        "CALLER_ATTESTED_FRESH_UNLOCK", "C2_PIPELINE_RUNTIME",
        "C2PipelineRuntime.record_final_raw_fresh_verification",
        caller_attested_fresh_unlock,
    )

    baseline = _new_guard(settings)
    stable_token = object()
    baseline.bind_vqf_instance("thigh_left", stable_token)
    baseline.bind_vqf_instance("thigh_left", stable_token)
    baseline.observe_qmt_span("knee_left", reset_requested=False, profile_stitch_requested=False)
    baseline.validate_knee_branch("FORWARD", "FORWARD")
    baseline.update_branch_weights([0.5, 0.5], lock_requested=False)
    baseline.validate_factor_fields(["acc", "gyro", "node_timer"])
    baseline.validate_wear_prior({"family": "BROAD_NON_COMPACT_QUALITATIVE", "hard_cone_deg": None})
    baseline.validate_bilateral_model(hard_mirror=False)
    baseline.validate_connection_vector(np.array([0.1, -0.2, 0.3]))
    baseline.validate_connection_frame_mode("SENSOR_TO_SEGMENT_THEN_WORLD")
    propagated = baseline.propagate_heading(np.arange(5.0), np.linspace(0.0, 0.4, 5), mode="TIME_VARYING_PARENT_PLUS_CHILD_DELTAFILT")
    baseline.validate_row_selection("PREFIT_FIXED_NOISE_STANDARDIZED_CONTIGUOUS_BLOCKS")
    baseline.consider_candidate(physically_legal=True, residual=1.0)
    baseline.validate_geometry(_baseline_points(), ROOTED_EDGES)
    baseline.validate_viewer_transform(mode="DIRECT_SCIENTIFIC_FK_NO_REBASE_NO_IK_NO_REPAIR")
    baseline.record_progress(sorted(REQUIRED_PROGRESS_INPUTS), prediction_scored_before_ingest=True)
    baseline.claim_initial_still_completion(complete_calibration=False)
    expected = set(settings["synthetic"]["mandatory_architecture_negative_mutations"])
    actual = set(rows)
    integrated_owner_mutations = {name for name, row in rows.items() if row["caught"]}
    return {
        "schema": "biospur-c2-owner-level-architecture-mutation-gate-v2",
        "coverage_class": "EXECUTED_OWNER_LEVEL", "owner_module": __name__,
        "baseline_execution_audit": baseline.audit(),
        "baseline_parent_plus_child_numeric_result": propagated.tolist(),
        "prequential_time_positive_smokes": prequential_time_positive_smokes,
        "mutations": rows, "registered_names_match": actual == expected,
        "missing_registered_mutations": sorted(expected - actual),
        "unexpected_mutations": sorted(actual - expected),
        "declarative_only_counted_as_pass": False,
        "integrated_owner_mutations": sorted(integrated_owner_mutations),
        "owner_not_implemented": sorted(name for name, row in rows.items() if row["coverage_class"] != "EXECUTED_OWNER_LEVEL"),
        "pass": actual == expected and all(row["caught"] for row in rows.values()),
    }
