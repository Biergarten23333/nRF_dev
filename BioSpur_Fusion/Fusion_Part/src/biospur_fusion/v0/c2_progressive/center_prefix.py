"""Causal same-edge historical-prefix owner for pair-local joint centers."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from hashlib import sha256
import json
from typing import Any, Mapping, Sequence

from .functional_geometry import AlignedPair


@dataclass(frozen=True)
class CenterPrefixSelection:
    edge: str
    chronological_index: int
    action: str
    pairs: tuple[AlignedPair, ...]
    current_pair: AlignedPair
    mode: str
    prequential_prediction_sha256: str
    geometry_had_accepted_center_before_current: bool
    selection_token: str
    report: Mapping[str, Any]


class CausalCenterPrefixOwner:
    """Retain committed same-edge pairs until the first eligible center.

    Rejected early episodes remain immutable evidence but add no information.
    The first accepted center may consume the exact committed prefix once.  All
    later updates are current-action only, preventing historical double count.
    """

    def __init__(
        self,
        settings: Mapping[str, Any],
        *,
        chronological_actions: Sequence[str],
        edge_actions: Mapping[str, Sequence[str]],
        execution_guard: Any,
    ) -> None:
        expected = {
            "schema": "biospur-c2-causal-center-prefix-owner-settings-v1",
            "mode_before_first_accepted_center": (
                "SAME_EDGE_COMMITTED_HISTORICAL_PREFIX_PLUS_CURRENT_ACTION_AFTER_"
                "PREQUENTIAL_SCORE"
            ),
            "mode_after_first_accepted_center": (
                "CURRENT_ACTION_ONLY_NO_HISTORICAL_REINGESTION"
            ),
            "edge_pooling": "FORBIDDEN",
            "prefix_membership_and_order_binding": (
                "SHA256_OF_EXACT_ORDERED_PAIR_ACTION_INDEX_RUNTIME_TOKEN_BOOT_"
                "EPOCH_CONTIGUOUS_SPAN_AND_CLUSTER_IDENTITY_ROWS"
            ),
            "commit_token_enforcement": (
                "RECOMPUTE_EXACT_ORDERED_MEMBERSHIP_SHA256_AND_SELECTION_TOKEN_"
                "FROM_LIVE_SELECTION_FIELDS_AND_PAIRS_AT_COMMIT;STALE_REPORT_OR_"
                "TOKEN_REJECTED"
            ),
            "pair_cluster_correlation": (
                "PRESERVE_DISTINCT_PAIR_ACTION_CLOCK_GROUP_AND_COMPLETE_GAP_SAFE_"
                "BLOCK_CLUSTER_IDENTITIES;NEVER_TREAT_HISTORICAL_CONCATENATION_AS_IID"
            ),
            "ordinary_estimator_failure": (
                "TRANSACTION_ROLLBACK_THEN_EXPLICIT_CURRENT_PAIR_LOCAL_NO_UPDATE_"
                "COMMIT_ON_BOUNDED_RETRY;PAIR_ENTERS_IMMUTABLE_ZERO_INFORMATION_"
                "EVIDENCE_LEDGER_ONLY_AND_IS_EXCLUDED_FROM_LATER_ESTIMATOR_PREFIX"
            ),
            "successful_low_information_factor": (
                "MAY_ENTER_FUTURE_SAME_EDGE_ESTIMATOR_PREFIX_BECAUSE_ESTIMATOR_"
                "COMPLETED_AND_NO_INFORMATION_WAS_INGESTED"
            ),
            "transaction": "SELECTION_AND_COMMIT_ROLL_BACK_WITH_RUNTIME_OWNER_STATE",
        }
        if any(settings.get(key) != value for key, value in expected.items()):
            raise RuntimeError("causal center-prefix settings are not registered")
        self.settings = settings
        self.chronological_actions = tuple(str(value) for value in chronological_actions)
        if len(self.chronological_actions) != 19 or len(set(self.chronological_actions)) != 19:
            raise ValueError("center-prefix owner requires the exact unique 19-action chronology")
        action_index = {
            action: index for index, action in enumerate(self.chronological_actions)
        }
        self.edge_actions = {
            str(edge): tuple(str(action) for action in actions)
            for edge, actions in edge_actions.items()
        }
        if not self.edge_actions:
            raise ValueError("center-prefix owner requires nonempty edge routes")
        for edge, actions in self.edge_actions.items():
            if (
                not actions
                or len(set(actions)) != len(actions)
                or any(action not in action_index for action in actions)
                or tuple(sorted(actions, key=action_index.__getitem__)) != actions
            ):
                raise ValueError(f"{edge}: center-prefix action route is not chronological")
        self.execution_guard = execution_guard
        self._eligible_history: dict[str, list[AlignedPair]] = {
            edge: [] for edge in self.edge_actions
        }
        self._seen_evidence: dict[str, list[dict[str, Any]]] = {
            edge: [] for edge in self.edge_actions
        }
        self._accepted_edges: set[str] = set()
        self._pending: dict[str, CenterPrefixSelection] = {}
        self._events: list[dict[str, Any]] = []

    @staticmethod
    def _pair_identity(pair: AlignedPair) -> tuple[int, str, str]:
        provenance = dict(pair.provenance)
        return (
            int(provenance.get("chronological_index", -1)),
            str(pair.action),
            str(provenance.get("runtime_owner_token", "")),
        )

    @staticmethod
    def _is_sha256(value: str) -> bool:
        return len(value) == 64 and all(char in "0123456789abcdef" for char in value)

    def _reject_future_or_heldout(self, detail: str) -> None:
        self.execution_guard.reject_center_prefix_future_or_heldout(detail)

    def _reject_edge_or_order(self, detail: str) -> None:
        self.execution_guard.reject_center_prefix_edge_pooling_or_backward_smoothing(
            detail
        )

    def _semantic_binding(
        self,
        *,
        edge: str,
        chronological_index: int,
        action: str,
        mode: str,
        prequential_prediction_sha256: str,
        geometry_had_accepted_center_before_current: bool,
        pairs: Sequence[AlignedPair],
    ) -> tuple[list[dict[str, Any]], dict[str, Any], str]:
        pair_rows: list[dict[str, Any]] = []
        for estimator_pair_index, pair in enumerate(pairs):
            spans = [[span.start, span.stop] for span in pair.contiguous_spans]
            cluster_identity_rows = [
                {
                    "estimator_pair_index": int(estimator_pair_index),
                    "action": pair.action,
                    "aligned_contiguous_span_index": int(span_index),
                    "aligned_rows_half_open": span,
                }
                for span_index, span in enumerate(spans)
            ]
            pair_rows.append({
                "estimator_pair_index": int(estimator_pair_index),
                "chronological_index": self._pair_identity(pair)[0],
                "action": pair.action,
                "runtime_owner_token": self._pair_identity(pair)[2],
                "edge": pair.edge,
                "parent_boot_epoch_values": sorted({
                    int(value) for value in pair.parent_boot_epoch
                }),
                "child_boot_epoch_values": sorted({
                    int(value) for value in pair.child_boot_epoch
                }),
                "contiguous_span_half_open": spans,
                "pair_span_cluster_identity_sha256": sha256(
                    json.dumps(
                        cluster_identity_rows,
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode("utf-8")
                ).hexdigest(),
            })
        ordered_prefix_membership_sha256 = sha256(
            json.dumps(pair_rows, sort_keys=True, separators=(",", ":")).encode(
                "utf-8"
            )
        ).hexdigest()
        token_payload = {
            "edge": str(edge),
            "chronological_index": int(chronological_index),
            "action": str(action),
            "mode": str(mode),
            "prequential_prediction_sha256": str(prequential_prediction_sha256),
            "geometry_had_accepted_center_before_current": bool(
                geometry_had_accepted_center_before_current
            ),
            "pair_runtime_owner_tokens": [
                row["runtime_owner_token"] for row in pair_rows
            ],
            "ordered_prefix_membership_sha256": (
                ordered_prefix_membership_sha256
            ),
        }
        token = sha256(
            json.dumps(
                token_payload,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        return pair_rows, token_payload, token

    def select(
        self,
        *,
        edge: str,
        current_pair: AlignedPair,
        chronological_index: int,
        action: str,
        prequential_prediction_sha256: str,
        geometry_has_accepted_center: bool,
    ) -> CenterPrefixSelection:
        edge = str(edge)
        action = str(action)
        index = int(chronological_index)
        if edge not in self.edge_actions or current_pair.edge != edge:
            self._reject_edge_or_order("center prefix attempted cross-edge pooling")
        if index < 0 or index >= len(self.chronological_actions):
            self._reject_future_or_heldout("center prefix index leaves sealed training chronology")
        if self.chronological_actions[index] != action or action not in self.edge_actions[edge]:
            self._reject_edge_or_order("center prefix action is relabeled, out of route, or reordered")
        if edge in self._pending:
            self._reject_edge_or_order("center prefix selection duplicated before transactional commit")
        if not self._is_sha256(str(prequential_prediction_sha256)):
            self._reject_future_or_heldout("center prefix lacks pre-ingest prediction binding")
        provenance = dict(current_pair.provenance)
        pair_index = int(provenance.get("chronological_index", -1))
        source_role = str(provenance.get("source_role", "")).upper()
        if (
            pair_index != index
            or current_pair.action != action
            or provenance.get("action") != action
            or bool(provenance.get("heldout", False))
            or "HELDOUT" in source_role
        ):
            self._reject_future_or_heldout(
                "current center pair is future, heldout, or action/index substituted"
            )
        runtime_token = str(provenance.get("runtime_owner_token", ""))
        if not self._is_sha256(runtime_token):
            self._reject_future_or_heldout("current center pair lacks runtime owner token")
        accepted_before = edge in self._accepted_edges
        if bool(geometry_has_accepted_center) != accepted_before:
            self.execution_guard.reject_center_prefix_nuisance_bypass_or_reingestion(
                "center-prefix accepted state diverges from authoritative geometry owner"
            )
        history = tuple(self._eligible_history[edge])
        identities = [self._pair_identity(pair) for pair in history]
        if any(
            pair.edge != edge
            or pair.action not in self.edge_actions[edge]
            or pair_index_value >= index
            for pair, (pair_index_value, _, _) in zip(history, identities, strict=True)
        ):
            self._reject_future_or_heldout(
                "center prefix history contains a future action or incompatible edge"
            )
        if identities != sorted(identities, key=lambda row: row[0]):
            self._reject_edge_or_order("center prefix history was backward-smoothed or reordered")
        if len({row[0] for row in identities}) != len(identities):
            self._reject_edge_or_order("center prefix history duplicates an action index")
        if len({row[2] for row in identities}) != len(identities):
            self._reject_edge_or_order("center prefix history duplicates a pair owner token")
        if runtime_token in {row[2] for row in identities}:
            self._reject_edge_or_order("current center pair was already committed")
        if accepted_before:
            pairs = (current_pair,)
            mode = "CURRENT_ACTION_ONLY_NO_HISTORICAL_REINGESTION"
        else:
            pairs = (*history, current_pair)
            mode = (
                "SAME_EDGE_COMMITTED_HISTORICAL_PREFIX_PLUS_CURRENT_ACTION_AFTER_"
                "PREQUENTIAL_SCORE"
            )
        pair_rows, token_payload, token = self._semantic_binding(
            edge=edge,
            chronological_index=index,
            action=action,
            mode=mode,
            prequential_prediction_sha256=str(prequential_prediction_sha256),
            geometry_had_accepted_center_before_current=accepted_before,
            pairs=pairs,
        )
        report = {
            "schema": "biospur-c2-causal-center-prefix-selection-v1",
            **token_payload,
            "selection_token": token,
            "pair_rows": pair_rows,
            "historical_pair_count": len(history) if not accepted_before else 0,
            "current_pair_count": 1,
            "pair_count_consumed_by_estimator": len(pairs),
            "pair_objects_remain_distinct_at_estimator_boundary": len({id(pair) for pair in pairs}) == len(pairs),
            "per_action_pair_clock_group_and_block_cluster_correlation_preserved": True,
            "historical_rows_treated_as_iid_after_concatenation": False,
            "preingest_prediction_published_before_current_factor": True,
            "future_or_heldout_pair_count": 0,
            "cross_edge_pair_count": 0,
            "backward_smoothing_used": False,
            "unknown_boot_or_gap_repaired_or_concatenated": False,
            "same_registered_nuisance_and_refit_gates_required": True,
            "historical_information_reingested_after_first_acceptance": False,
            "authority_if_accepted": "PAIR_LOCAL_CENTER_ONLY",
            "segment_frame_qmt_rooted_renderer_or_skeleton_authorized": False,
        }
        selection = CenterPrefixSelection(
            edge=edge,
            chronological_index=index,
            action=action,
            pairs=tuple(pairs),
            current_pair=current_pair,
            mode=mode,
            prequential_prediction_sha256=str(prequential_prediction_sha256),
            geometry_had_accepted_center_before_current=accepted_before,
            selection_token=token,
            report=report,
        )
        self._pending[edge] = selection
        return selection

    def commit(
        self,
        selection: CenterPrefixSelection,
        *,
        estimator_owner_update_eligible: bool,
        geometry_update_accepted: bool,
        estimator_completed: bool,
        retain_for_future_prefix: bool,
    ) -> Mapping[str, Any]:
        pending = self._pending.get(selection.edge)
        if pending is not selection:
            self._reject_edge_or_order("center prefix commit is copied, stale, or duplicated")
        if not selection.pairs or selection.pairs[-1] is not selection.current_pair:
            self.execution_guard.reject_center_prefix_membership_or_order_token_substitution(
                "center prefix current pair is not the final exact ordered member"
            )
        pair_rows, token_payload, recomputed_token = self._semantic_binding(
            edge=selection.edge,
            chronological_index=selection.chronological_index,
            action=selection.action,
            mode=selection.mode,
            prequential_prediction_sha256=(
                selection.prequential_prediction_sha256
            ),
            geometry_had_accepted_center_before_current=(
                selection.geometry_had_accepted_center_before_current
            ),
            pairs=selection.pairs,
        )
        report = dict(selection.report)
        if (
            selection.selection_token != recomputed_token
            or report.get("selection_token") != recomputed_token
            or report.get("pair_rows") != pair_rows
            or any(report.get(key) != value for key, value in token_payload.items())
            or int(report.get("pair_count_consumed_by_estimator", -1))
            != len(selection.pairs)
        ):
            self.execution_guard.reject_center_prefix_membership_or_order_token_substitution(
                "center prefix membership/order or semantic token changed after selection"
            )
        if bool(estimator_owner_update_eligible) != bool(geometry_update_accepted):
            self.execution_guard.reject_center_prefix_nuisance_bypass_or_reingestion(
                "geometry acceptance bypassed or contradicted center nuisance/refit gate"
            )
        if estimator_owner_update_eligible and not estimator_completed:
            self.execution_guard.reject_center_prefix_nuisance_bypass_or_reingestion(
                "center factor cannot be eligible when its estimator did not complete"
            )
        if selection.geometry_had_accepted_center_before_current and len(selection.pairs) != 1:
            self.execution_guard.reject_center_prefix_nuisance_bypass_or_reingestion(
                "historical center information was reingested after first acceptance"
            )
        history = self._eligible_history[selection.edge]
        evidence = self._seen_evidence[selection.edge]
        current_identity = self._pair_identity(selection.current_pair)
        if any(
            (
                int(row["chronological_index"]),
                str(row["action"]),
                str(row["runtime_owner_token"]),
            ) == current_identity
            for row in evidence
        ):
            self._reject_edge_or_order("center prefix current pair committed twice")
        expected_retain = bool(
            estimator_completed
            and not geometry_update_accepted
            and not selection.geometry_had_accepted_center_before_current
        )
        if bool(retain_for_future_prefix) != expected_retain:
            self.execution_guard.reject_center_prefix_failed_factor_reingestion(
                "failed factor was requested for later estimator reuse or completed low-information factor was discarded"
            )
        evidence.append({
            "chronological_index": selection.chronological_index,
            "action": selection.action,
            "runtime_owner_token": current_identity[2],
            "selection_token": selection.selection_token,
            "prequential_prediction_sha256": (
                selection.prequential_prediction_sha256
            ),
            "estimator_completed": bool(estimator_completed),
            "estimator_owner_update_eligible": bool(estimator_owner_update_eligible),
            "geometry_update_accepted": bool(geometry_update_accepted),
            "retained_for_future_estimator_prefix": expected_retain,
            "information_contributed_at_commit": bool(geometry_update_accepted),
            "information_contribution_count": int(bool(geometry_update_accepted)),
            "information_contribution_selection_token": (
                selection.selection_token if geometry_update_accepted else None
            ),
        })
        historical_information_rows_marked = 0
        if geometry_update_accepted:
            for historical_pair in selection.pairs[:-1]:
                historical_identity = self._pair_identity(historical_pair)
                matched = [
                    row
                    for row in evidence
                    if (
                        int(row["chronological_index"]),
                        str(row["action"]),
                        str(row["runtime_owner_token"]),
                    ) == historical_identity
                ]
                if len(matched) != 1 or int(
                    matched[0]["information_contribution_count"]
                ) != 0:
                    self.execution_guard.reject_center_prefix_nuisance_bypass_or_reingestion(
                        "historical center pair information was missing or already counted"
                    )
                matched[0]["information_contributed_at_commit"] = True
                matched[0]["information_contribution_count"] = 1
                matched[0][
                    "information_contribution_selection_token"
                ] = selection.selection_token
                historical_information_rows_marked += 1
        if expected_retain:
            history.append(selection.current_pair)
        if geometry_update_accepted:
            self._accepted_edges.add(selection.edge)
        event = {
            "schema": "biospur-c2-causal-center-prefix-commit-v1",
            "selection_token": selection.selection_token,
            "edge": selection.edge,
            "chronological_index": selection.chronological_index,
            "action": selection.action,
            "mode": selection.mode,
            "estimator_owner_update_eligible": bool(
                estimator_owner_update_eligible
            ),
            "geometry_update_accepted": bool(geometry_update_accepted),
            "estimator_completed": bool(estimator_completed),
            "retained_for_future_estimator_prefix": expected_retain,
            "eligible_history_count": len(history),
            "immutable_seen_evidence_count": len(evidence),
            "explicit_failed_factor_reingested_later": False,
            "historical_pair_information_rows_marked_once": (
                historical_information_rows_marked
            ),
            "every_consumed_pair_information_contribution_count": 1 if geometry_update_accepted else 0,
            "first_accepted_prefix_information_ingested_once": bool(
                geometry_update_accepted
                and not selection.geometry_had_accepted_center_before_current
            ),
            "ordered_prefix_membership_sha256": selection.report[
                "ordered_prefix_membership_sha256"
            ],
            "accepted_prefix_pair_rows": (
                deepcopy(selection.report["pair_rows"])
                if geometry_update_accepted
                else []
            ),
            "accepted_prefix_pair_count": (
                len(selection.pairs) if geometry_update_accepted else 0
            ),
            "historical_rows_recounted_after_first_acceptance": False,
            "authority_if_accepted": "PAIR_LOCAL_CENTER_ONLY",
            "segment_frame_qmt_rooted_renderer_or_skeleton_authorized": False,
            "historical_information_reingested_after_first_acceptance": False,
        }
        self._events.append(event)
        del self._pending[selection.edge]
        return event

    def owns_historical_pair(self, pair: AlignedPair) -> bool:
        token = str(pair.provenance.get("runtime_owner_token", ""))
        return any(
            candidate is pair
            and str(candidate.provenance.get("runtime_owner_token", "")) == token
            for history in self._eligible_history.values()
            for candidate in history
        )

    def checkpoint(self) -> Mapping[str, Any]:
        return {
            "eligible_history": deepcopy(self._eligible_history),
            "seen_evidence": deepcopy(self._seen_evidence),
            "accepted_edges": set(self._accepted_edges),
            "pending": deepcopy(self._pending),
            "events": deepcopy(self._events),
        }

    def restore(self, checkpoint: Mapping[str, Any]) -> None:
        self._eligible_history = deepcopy(checkpoint["eligible_history"])
        self._seen_evidence = deepcopy(checkpoint["seen_evidence"])
        self._accepted_edges = set(checkpoint["accepted_edges"])
        self._pending = deepcopy(checkpoint["pending"])
        self._events = deepcopy(checkpoint["events"])

    def audit(self) -> Mapping[str, Any]:
        return {
            "schema": "biospur-c2-causal-center-prefix-owner-audit-v1",
            "accepted_edges": sorted(self._accepted_edges),
            "eligible_estimator_history": {
                edge: [
                    {
                        "chronological_index": self._pair_identity(pair)[0],
                        "action": pair.action,
                        "runtime_owner_token": self._pair_identity(pair)[2],
                    }
                    for pair in pairs
                ]
                for edge, pairs in self._eligible_history.items()
            },
            "immutable_seen_evidence": deepcopy(self._seen_evidence),
            "pending_edges": sorted(self._pending),
            "events": deepcopy(self._events),
        }
