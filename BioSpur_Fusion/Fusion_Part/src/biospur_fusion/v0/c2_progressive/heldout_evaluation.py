"""Frozen-state scientific heldout evaluation for Capture 2.

This owner reconstructs continuous six-axis orientation from exact sealed raw
ranges, but every calibration quantity is loaded from the immutable post-fresh
export.  Heldout motion may evolve evaluation-only VQF/QMT trajectory state;
geometry, segment frames, clock calibration, branch identities/weights, model
posteriors, thresholds, and fit information are never updated.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from types import MappingProxyType
from typing import Any, Mapping, Sequence
from uuid import uuid4

import numpy as np
from scipy.spatial.transform import Rotation

from .architecture_guard import ClassAGuardViolation, C2ExecutionGuard, ROOTED_EDGES
from .functional_geometry import AlignedPair, EDGE_SPECS, HINGE_EDGES
from .heading import PersistentHeadingOwner
from .orientation import (
    ContinuousVQFState,
    OrientedAction,
    assess_factor_rows,
    contiguous_span_ids,
)
from .orientation_uncertainty import physical_orientation_covariance
from .quaternion_contract import qmt_wxyz_to_scipy_active
from .range_reader import DecodedAction, DecodedEvaluationAction, EXPECTED_STEP_US, IMU_DTYPE
from .scientific_fk import (
    PhysicalTrajectoryCandidateAssessment,
    ScientificForwardKinematicsOwner,
    physical_input_binding_token,
)
from .segment_frames import EdgeConnectionVectors, SegmentFrameBranch
from .timebase import PairAlignment, PersistentPairClockState


EDGE_BY_NAME = {edge: (parent, child) for edge, parent, child in EDGE_SPECS}
EDGE_NAME_BY_ENDPOINTS = {(parent, child): edge for edge, parent, child in EDGE_SPECS}
SEGMENTS = tuple(dict.fromkeys(
    name for _, parent, child in EDGE_SPECS for name in (parent, child)
))


def _array_sha256(value: np.ndarray) -> str:
    array = np.ascontiguousarray(np.asarray(value))
    return sha256(array.view(np.uint8)).hexdigest()


def _semantic_sha256(value: Any) -> str:
    return sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    ).hexdigest()


@dataclass(frozen=True)
class FrozenHeldoutActionEvaluation:
    action: str
    chronological_index: int
    report: Mapping[str, Any]
    arrays: Mapping[str, np.ndarray]


def reconstruct_frozen_segment_frame_branches(
    manifest: Mapping[str, Any],
    arrays: Mapping[str, np.ndarray],
) -> tuple[SegmentFrameBranch, ...]:
    """Rebuild copy-only branch values from a hash-validated frozen export."""

    rows = tuple(manifest["structure"]["frame_branches"])
    branches: list[SegmentFrameBranch] = []
    for row in rows:
        branch_id = str(row["branch_id"])
        prefix = f"frames/{branch_id}"
        segment_from_sensor = {
            segment: np.asarray(arrays[f"{prefix}/segment_from_sensor/{segment}"], dtype=float).copy()
            for segment in SEGMENTS
        }
        sensor_from_segment = {
            segment: rotation.T.copy() for segment, rotation in segment_from_sensor.items()
        }
        frame_covariance = {
            segment: np.asarray(arrays[f"{prefix}/frame_covariance/{segment}"], dtype=float).copy()
            for segment in SEGMENTS
        }
        paired_covariance = {
            edge: np.asarray(
                arrays[f"{prefix}/paired_hinge_frame_covariance/{edge}"], dtype=float,
            ).copy()
            for edge in HINGE_EDGES
        }
        connections: dict[str, EdgeConnectionVectors] = {}
        for edge, parent, child in EDGE_SPECS:
            connections[edge] = EdgeConnectionVectors(
                edge=edge,
                parent=parent,
                child=child,
                parent_sensor_to_joint_m=np.asarray(
                    arrays[f"{prefix}/connection/{edge}/parent"], dtype=float,
                ).copy(),
                child_sensor_to_joint_m=np.asarray(
                    arrays[f"{prefix}/connection/{edge}/child"], dtype=float,
                ).copy(),
                covariance_m2=np.asarray(
                    arrays[f"{prefix}/connection/{edge}/covariance"], dtype=float,
                ).copy(),
            )
        branch = SegmentFrameBranch(
            branch_id=branch_id,
            axis_sign_by_edge={str(key): int(value) for key, value in row["axis_sign_by_edge"].items()},
            segment_from_sensor=MappingProxyType(segment_from_sensor),
            sensor_from_segment=MappingProxyType(sensor_from_segment),
            joint_frame_tangent_covariance_rad2=np.asarray(
                arrays[f"{prefix}/joint_frame_covariance"], dtype=float,
            ).copy(),
            frame_tangent_covariance_rad2=MappingProxyType(frame_covariance),
            paired_hinge_frame_tangent_covariance_rad2=MappingProxyType(paired_covariance),
            connection_vectors_by_edge=MappingProxyType(connections),
            prior_weight=float(row["prior_weight"]),
            wear_log_likelihood=float(row["wear_log_likelihood"]),
            wear_profile_log_likelihood=MappingProxyType({
                str(key): float(value)
                for key, value in row["wear_profile_log_likelihood"].items()
            }),
            wear_gross_wrong_hemisphere=bool(row["wear_gross_wrong_hemisphere"]),
            retained=bool(row["retained"]),
            report=MappingProxyType({
                "schema": "biospur-c2-reloaded-frozen-segment-frame-branch-v1",
                "source_manifest_structure_semantic_sha256": str(
                    manifest["structure_semantic_sha256"]
                ),
                "calibration_update_allowed": False,
            }),
        )
        for segment, rotation in branch.segment_from_sensor.items():
            if (
                rotation.shape != (3, 3)
                or not np.allclose(rotation.T @ rotation, np.eye(3), atol=1e-8)
                or not np.isclose(np.linalg.det(rotation), 1.0, atol=1e-8)
            ):
                raise RuntimeError(f"{branch_id}:{segment}: frozen frame is not proper SO(3)")
        branches.append(branch)
    if not branches or len({branch.branch_id for branch in branches}) != len(branches):
        raise RuntimeError("frozen branch reconstruction produced no branches or duplicate IDs")
    return tuple(branches)


def _heldout_oriented_action(
    full: OrientedAction,
    decoded: DecodedEvaluationAction,
) -> OrientedAction:
    """Select only heldout rows after the full-range quality owner has run."""

    fields: dict[str, dict[str, np.ndarray]] = {
        "time": {}, "boot": {}, "span": {}, "acc": {}, "gyro": {}, "quat": {},
        "gap_cov": {}, "bias": {}, "bias_sigma": {}, "rest": {},
    }
    audit: dict[str, Any] = {}
    for node, source in decoded.combined_action.rows_by_node.items():
        quality = assess_factor_rows(
            source["node_timer_us"], source["derived_boot_epoch"],
            source["acc_raw"], source["gyro_raw"],
        )
        retained_source = np.asarray(quality.retained_indices, dtype=np.int64)
        heldout_source = np.asarray(decoded.heldout_source_indices_by_node[node], dtype=np.int64)
        positions = np.flatnonzero(np.isin(retained_source, heldout_source, assume_unique=True))
        time = np.asarray(full.time_us_by_node[node][positions], dtype=np.int64)
        boot = np.asarray(full.derived_boot_epoch_by_node[node][positions], dtype=np.int64)
        fields["time"][node] = time
        fields["boot"][node] = boot
        fields["span"][node] = contiguous_span_ids(time, boot)
        fields["acc"][node] = np.asarray(full.acc_mps2_by_node[node][positions], dtype=float)
        fields["gyro"][node] = np.asarray(full.gyro_rads_by_node[node][positions], dtype=float)
        fields["quat"][node] = np.asarray(
            full.quat_world_sensor_wxyz_by_node[node][positions], dtype=float,
        )
        fields["gap_cov"][node] = np.asarray(
            full.gap_only_orientation_covariance_rad2_by_node[node][positions], dtype=float,
        )
        fields["bias"][node] = np.asarray(
            full.vqf_residual_bias_rad_s_by_node[node][positions], dtype=float,
        )
        fields["bias_sigma"][node] = np.asarray(
            full.vqf_residual_bias_sigma_rad_s_by_node[node][positions], dtype=float,
        )
        fields["rest"][node] = np.asarray(
            full.vqf_rest_detected_by_node[node][positions], dtype=bool,
        )
        audit[node] = {
            "combined_decoded_rows": int(len(source)),
            "quality_retained_rows": int(len(retained_source)),
            "heldout_decoded_source_rows": int(len(heldout_source)),
            "heldout_quality_retained_rows": int(len(positions)),
            "training_quality_retained_rows_entered_metric": False,
            "heldout_raw_interval": list(decoded.heldout_interval),
            "heldout_timer_sha256": _array_sha256(time),
            "heldout_quaternion_sha256": _array_sha256(fields["quat"][node]),
            "full_oriented_positions": positions.tolist(),
            "full_oriented_positions_sha256": _array_sha256(positions.astype(np.int64)),
        }
    return OrientedAction(
        action=full.action,
        chronological_index=full.chronological_index,
        time_us_by_node=MappingProxyType(fields["time"]),
        derived_boot_epoch_by_node=MappingProxyType(fields["boot"]),
        contiguous_span_id_by_node=MappingProxyType(fields["span"]),
        acc_mps2_by_node=MappingProxyType(fields["acc"]),
        gyro_rads_by_node=MappingProxyType(fields["gyro"]),
        quat_world_sensor_wxyz_by_node=MappingProxyType(fields["quat"]),
        gap_only_orientation_covariance_rad2_by_node=MappingProxyType(fields["gap_cov"]),
        vqf_residual_bias_rad_s_by_node=MappingProxyType(fields["bias"]),
        vqf_residual_bias_sigma_rad_s_by_node=MappingProxyType(fields["bias_sigma"]),
        vqf_rest_detected_by_node=MappingProxyType(fields["rest"]),
        audit=MappingProxyType({
            "schema": "biospur-c2-heldout-only-oriented-action-v1",
            "action": full.action,
            "chronological_index": int(full.chronological_index),
            "nodes": audit,
            "training_rows_entered_scientific_metric": False,
            "continuous_vqf_state_reconstructed_from_exact_training_prefix": True,
            "calibration_fit_or_parameter_update": False,
        }),
    )


class FrozenScientificHeldoutEvaluationOwner:
    """Scientific heldout prediction owner with immutable calibration inputs."""

    def __init__(
        self,
        *,
        settings: Mapping[str, Any],
        initial_stochastic_state: Mapping[str, Any],
        frozen_manifest: Mapping[str, Any],
        frozen_arrays: Mapping[str, np.ndarray],
    ) -> None:
        self.settings = settings
        registered = settings.get("heldout_evaluation", {})
        if registered.get("schema") != "biospur-c2-registered-frozen-heldout-evaluation-v1":
            raise RuntimeError("frozen heldout metric/criterion settings are not registered")
        self._criteria = registered
        self._manifest = frozen_manifest
        self._arrays = {name: np.asarray(value).copy() for name, value in frozen_arrays.items()}
        for value in self._arrays.values():
            value.setflags(write=False)
        authority = frozen_manifest["structure"].get("frozen_evaluation_authority", {})
        if (
            authority.get("schema") != "biospur-c2-frozen-heldout-owner-authority-v1"
            or authority.get("calibration_owner_mutable_reference_exported") is not False
            or authority.get("heldout_threshold_override_allowed") is not False
        ):
            raise RuntimeError("frozen export lacks the heldout evaluation owner authority")
        self._branches = reconstruct_frozen_segment_frame_branches(
            frozen_manifest, self._arrays,
        )
        self._branch_ids = tuple(str(value) for value in authority["branch_ids"])
        if tuple(branch.branch_id for branch in self._branches) != self._branch_ids:
            raise RuntimeError("frozen frame branch order differs from the authoritative posterior order")
        self._hard_support = np.asarray(
            self._arrays["frozen/branch_hard_support"], dtype=bool,
        )
        self._branch_weights = np.asarray(
            self._arrays["frozen/branch_weights"], dtype=float,
        )
        if (
            self._hard_support.shape != (len(self._branch_ids),)
            or self._branch_weights.shape != (len(self._branch_ids),)
            or not np.array_equal(self._hard_support, np.asarray(authority["hard_support_mask"], dtype=bool))
            or np.any(self._branch_weights < 0.0)
            or not np.isclose(np.sum(self._branch_weights), 1.0, atol=1e-10)
        ):
            raise RuntimeError("frozen branch support/weights are invalid or inconsistent")
        self._frozen_calibration_digest = self._calibration_digest()
        self._guard = C2ExecutionGuard(settings)
        self._guard.begin_capture("C2_FROZEN_HELDOUT_EVALUATION")
        self._orientation = ContinuousVQFState(
            initial_stochastic_state,
            execution_guard=self._guard,
            sample_period_s=float(settings["orientation"]["sample_period_s"]),
            unknown_boot_orientation_sigma_rad=float(
                settings["orientation"]["unknown_boot_orientation_sigma_rad"]
            ),
            unknown_unusable_episode_orientation_sigma_rad=float(
                settings["orientation"]["unknown_unusable_episode_orientation_sigma_rad"]
            ),
        )
        self._clock = PersistentPairClockState(
            maximum_abs_drift_ppm=float(settings["timing"]["maximum_abs_drift_ppm"]),
            jitter_floor_s=float(settings["timing"]["jitter_floor_s"]),
        )
        self._clock.restore(authority["pair_clock_checkpoint"])
        heading_prior = authority.get("heading_prior")
        if heading_prior is None or heading_prior.get("schema") != "biospur-c2-frozen-heading-evaluation-prior-v2":
            raise RuntimeError("frozen export lacks the final persistent heading prior")
        self._heading = PersistentHeadingOwner.from_frozen_evaluation_state(
            settings["heading"], self._branches,
            execution_guard=self._guard,
            frozen_edge_state=heading_prior["edge_state"],
        )
        self._owner_id = f"C2_FROZEN_HELDOUT_OWNER_{uuid4().hex}"
        self._binding_secret = uuid4().bytes
        self._fk = ScientificForwardKinematicsOwner(
            execution_guard=self._guard,
            physical_settings=settings["physical_candidates"],
            expected_runtime_owner_id=self._owner_id,
            runtime_binding_secret=self._binding_secret,
        )
        wear_rows = settings["segment_frames"]["wear_authority"]["rows"]
        self._node_by_segment = {
            str(row["body_segment"]): str(row["hardware_id"]) for row in wear_rows
        }
        if set(self._node_by_segment) != set(SEGMENTS):
            raise RuntimeError("heldout evaluator does not bind the exact ten hardware/segment identities")
        self._initial = initial_stochastic_state
        self._initial_semantic_sha256 = _semantic_sha256(initial_stochastic_state)
        expected_initial_sha = settings["execution_contract"][
            "initial_stochastic_state_semantic_sha256"
        ]
        if self._initial_semantic_sha256 != expected_initial_sha:
            raise RuntimeError("heldout evaluator initial stochastic state differs from registry")
        self._full_oriented_actions: list[OrientedAction] = []
        self._orientation_uncertainty_audits: list[Mapping[str, Any]] = []
        self._action_reports: list[Mapping[str, Any]] = []
        self._next_index = 0

    def _calibration_digest(self) -> str:
        bindings = {
            name: _array_sha256(value) for name, value in sorted(self._arrays.items())
        }
        return _semantic_sha256(bindings)

    def _frozen_pair(self, oriented: OrientedAction, edge: str) -> AlignedPair:
        parent, child = EDGE_BY_NAME[edge]
        parent_node = self._node_by_segment[parent]
        child_node = self._node_by_segment[child]
        parent_time = np.asarray(oriented.time_us_by_node[parent_node], dtype=np.int64)
        child_time = np.asarray(oriented.time_us_by_node[child_node], dtype=np.int64)
        if len(parent_time) < 3 or len(child_time) < 3:
            raise ValueError(f"{edge}: insufficient heldout orientation rows")
        reference_time_s = float(np.median(parent_time) * 1e-6)
        prediction = self._clock.predict(edge=edge, reference_time_s=reference_time_s)
        predicted_offset_us = float(prediction["predicted_offset_s"]) * 1e6
        tolerance_us = float(self.settings["timing"]["clock_match_tolerance_s"]) * 1e6
        parent_indices: list[int] = []
        child_indices: list[int] = []
        last_child = -1
        child_float = child_time.astype(float)
        for parent_index, parent_value in enumerate(parent_time.astype(float)):
            target = parent_value - predicted_offset_us
            insertion = int(np.searchsorted(child_float, target))
            options = [value for value in (insertion - 1, insertion) if last_child < value < len(child_time)]
            if not options:
                continue
            selected = min(options, key=lambda value: abs(child_float[value] - target))
            if abs(child_float[selected] - target) <= tolerance_us:
                parent_indices.append(parent_index)
                child_indices.append(selected)
                last_child = selected
        pi = np.asarray(parent_indices, dtype=np.int64)
        ci = np.asarray(child_indices, dtype=np.int64)
        if len(pi) < 3:
            raise ValueError(f"{edge}: frozen clock prediction leaves fewer than three matched rows")
        parent_boot = np.asarray(oriented.derived_boot_epoch_by_node[parent_node], dtype=np.int64)
        child_boot = np.asarray(oriented.derived_boot_epoch_by_node[child_node], dtype=np.int64)
        breaks = np.flatnonzero(
            (np.diff(parent_time[pi]) != EXPECTED_STEP_US)
            | (np.diff(child_time[ci]) != EXPECTED_STEP_US)
            | (np.diff(parent_boot[pi]) != 0)
            | (np.diff(child_boot[ci]) != 0)
            | (np.diff(pi) != 1)
            | (np.diff(ci) != 1)
        ) + 1
        boundaries = np.r_[0, breaks, len(pi)]
        minimum_rows = int(self.settings["timing"]["minimum_contiguous_span_rows"])
        raw_spans = [
            (int(left), int(right)) for left, right in zip(boundaries[:-1], boundaries[1:])
            if right - left >= max(3, minimum_rows)
        ]
        if not raw_spans:
            raise ValueError(f"{edge}: frozen clock matches have no eligible gap-safe span")
        keep = np.concatenate([np.arange(left, right) for left, right in raw_spans])
        pi = pi[keep]
        ci = ci[keep]
        spans: list[slice] = []
        cursor = 0
        for left, right in raw_spans:
            length = right - left
            spans.append(slice(cursor, cursor + length))
            cursor += length
        origin_offset_s = float((int(parent_time[0]) - int(child_time[0])) * 1e-6)
        lag_samples = int(round(
            (float(prediction["predicted_offset_s"]) - origin_offset_s)
            / float(self.settings["timing"]["sample_period_s"])
        ))
        alignment = PairAlignment(
            parent_indices=pi,
            child_indices=ci,
            lag_samples=lag_samples,
            report={
                "schema": "biospur-c2-frozen-clock-heldout-alignment-v1",
                "persistent_pair_clock_prediction": prediction,
                "heldout_local_lag_observation_consumed": False,
                "pair_clock_state_updated": False,
                "clock_match_tolerance_s": float(
                    self.settings["timing"]["clock_match_tolerance_s"]
                ),
                "lag_uncertainty_s": float(prediction["predicted_offset_sigma_s"]),
                "contiguous_span_count": len(spans),
                "contiguous_span_lengths": [span.stop - span.start for span in spans],
                "cross_gap_or_boot_rows_aligned": 0,
            },
        )
        return AlignedPair(
            edge=edge,
            action=oriented.action,
            parent_acc=np.asarray(oriented.acc_mps2_by_node[parent_node][pi], dtype=float),
            child_acc=np.asarray(oriented.acc_mps2_by_node[child_node][ci], dtype=float),
            parent_gyro=np.asarray(oriented.gyro_rads_by_node[parent_node][pi], dtype=float),
            child_gyro=np.asarray(oriented.gyro_rads_by_node[child_node][ci], dtype=float),
            parent_observed_time_s=np.asarray(parent_time[pi], dtype=float) * 1e-6,
            child_observed_time_s=np.asarray(child_time[ci], dtype=float) * 1e-6,
            parent_boot_epoch=np.asarray(
                oriented.derived_boot_epoch_by_node[parent_node][pi], dtype=np.int64,
            ),
            child_boot_epoch=np.asarray(
                oriented.derived_boot_epoch_by_node[child_node][ci], dtype=np.int64,
            ),
            alignment=alignment,
            contiguous_spans=tuple(spans),
            provenance=MappingProxyType({
                "schema": "biospur-c2-frozen-heldout-aligned-pair-v1",
                "edge": edge,
                "parent_node": parent_node,
                "child_node": child_node,
                "parent_indices_sha256": _array_sha256(pi),
                "child_indices_sha256": _array_sha256(ci),
                "calibration_clock_state_updated": False,
            }),
        )

    def _orientation_covariance(
        self,
        oriented: OrientedAction,
        *,
        segment: str,
        source_indices: np.ndarray,
        timing_sigma_s: np.ndarray,
    ) -> np.ndarray:
        node = self._node_by_segment[segment]
        heldout_indices = np.asarray(source_indices, dtype=np.int64)
        heldout_node_audit = oriented.audit["nodes"][node]
        full_positions = np.asarray(
            heldout_node_audit["full_oriented_positions"], dtype=np.int64,
        )
        if np.any(heldout_indices < 0) or np.any(heldout_indices >= len(full_positions)):
            raise IndexError("heldout covariance source index leaves the heldout oriented rows")
        full_source_indices = full_positions[heldout_indices]
        output, audit = physical_orientation_covariance(
            self._full_oriented_actions,
            current_action_index=oriented.chronological_index,
            segment=segment,
            node=node,
            source_indices=full_source_indices,
            timing_sigma_s=timing_sigma_s,
            initial_stochastic_state=self._initial,
            initial_stochastic_state_semantic_sha256=self._initial_semantic_sha256,
            orientation_settings=self.settings["orientation"],
            uncertainty_settings=self.settings["physical_candidates"][
                "orientation_uncertainty"
            ],
        )
        if audit.get("sparse_selected_rows_treated_as_contiguous_five_ms_samples") is not False:
            raise RuntimeError("heldout covariance did not consume full-sequence cumulative terms")
        self._orientation_uncertainty_audits.append({
            "schema": "biospur-c2-heldout-orientation-uncertainty-index-mapping-v1",
            "action": oriented.action,
            "chronological_index": int(oriented.chronological_index),
            "segment": segment,
            "hardware_id": node,
            "heldout_source_indices": heldout_indices.tolist(),
            "mapped_full_oriented_source_indices": full_source_indices.tolist(),
            "nonzero_full_sequence_offset_exercised": bool(
                len(full_source_indices)
                and not np.array_equal(full_source_indices, heldout_indices)
            ),
            "authoritative_audit": dict(audit),
        })
        return output

    def _heading_for_action(
        self,
        oriented: OrientedAction,
        pair_by_edge: Mapping[str, AlignedPair],
        *,
        base_time_s: np.ndarray,
    ) -> tuple[dict[str, Any], list[Mapping[str, Any]]]:
        trajectory: dict[str, Any] = {}
        span_reports: list[Mapping[str, Any]] = []
        supported = [
            branch for branch, retained in zip(self._branches, self._hard_support) if retained
        ]
        for branch in supported:
            branch_checkpoint = self._heading.checkpoint()
            branch_report_start = len(span_reports)
            for edge, _, _ in EDGE_SPECS:
                pair = pair_by_edge.get(edge)
                if pair is None:
                    report = self._heading.record_action_no_update(
                        branch_id=branch.branch_id,
                        edge=edge,
                        chronological_index=oriented.chronological_index,
                        action=oriented.action,
                        base_common_physical_time_s=base_time_s,
                        cause="HELDOUT_FROZEN_CLOCK_ALIGNMENT_LOCAL_NO_UPDATE",
                    )
                    span_reports.append(report)
                    continue
                parent, child = EDGE_BY_NAME[edge]
                parent_node = self._node_by_segment[parent]
                child_node = self._node_by_segment[child]
                edge_checkpoint = self._heading.checkpoint()
                try:
                    for span in pair.contiguous_spans:
                        pi = np.asarray(pair.alignment.parent_indices[span], dtype=np.int64)
                        ci = np.asarray(pair.alignment.child_indices[span], dtype=np.int64)
                        time_s = np.asarray(
                            oriented.time_us_by_node[parent_node][pi], dtype=float,
                        ) * 1e-6
                        binding = {
                            "schema": "biospur-c2-runtime-owned-heading-span-input-v1",
                            "runtime_owner_token": self._owner_id,
                            "edge": edge,
                            "chronological_index": int(oriented.chronological_index),
                            "action": oriented.action,
                            "parent_gyro_sha256": _array_sha256(
                                oriented.gyro_rads_by_node[parent_node][pi]
                            ),
                            "child_gyro_sha256": _array_sha256(
                                oriented.gyro_rads_by_node[child_node][ci]
                            ),
                            "parent_quaternion_wxyz_sha256": _array_sha256(
                                oriented.quat_world_sensor_wxyz_by_node[parent_node][pi]
                            ),
                            "child_quaternion_wxyz_sha256": _array_sha256(
                                oriented.quat_world_sensor_wxyz_by_node[child_node][ci]
                            ),
                            "common_physical_time_s_sha256": _array_sha256(time_s),
                            "selected_source_row_indices_sha256": _array_sha256(pi),
                            "frozen_heldout_owner": True,
                            "calibration_posterior_updated": False,
                        }
                        result = self._heading.process_span(
                            branch_id=branch.branch_id,
                            edge=edge,
                            chronological_index=oriented.chronological_index,
                            action=oriented.action,
                            parent_gyro_sensor=oriented.gyro_rads_by_node[parent_node][pi],
                            child_gyro_sensor=oriented.gyro_rads_by_node[child_node][ci],
                            parent_quaternion_world_sensor_wxyz=(
                                oriented.quat_world_sensor_wxyz_by_node[parent_node][pi]
                            ),
                            child_quaternion_world_sensor_wxyz=(
                                oriented.quat_world_sensor_wxyz_by_node[child_node][ci]
                            ),
                            common_physical_time_s=time_s,
                            selected_source_row_indices=pi,
                            owner_input_binding=binding,
                        )
                        span_reports.append(result.report)
                except ClassAGuardViolation:
                    raise
                except (ValueError, RuntimeError, AssertionError) as exc:
                    self._heading.restore(edge_checkpoint)
                    no_update = self._heading.record_action_no_update(
                        branch_id=branch.branch_id,
                        edge=edge,
                        chronological_index=oriented.chronological_index,
                        action=oriented.action,
                        base_common_physical_time_s=base_time_s,
                        cause="ORDINARY_HELDOUT_QMT_EDGE_FAILURE_ROLLED_BACK_LOCAL_NO_UPDATE",
                    )
                    span_reports.append({
                        **dict(no_update),
                        "ordinary_failure": {
                            "exception_type": type(exc).__name__,
                            "exception_message": str(exc),
                            "partial_evaluation_heading_state_rolled_back": True,
                        },
                    })
            try:
                trajectory[branch.branch_id] = self._heading.assemble_action_rooted_trajectory(
                    branch.branch_id,
                    chronological_index=oriented.chronological_index,
                    action=oriented.action,
                    base_common_physical_time_s=base_time_s,
                )
            except ClassAGuardViolation:
                raise
            except (ValueError, RuntimeError, AssertionError) as exc:
                self._heading.restore(branch_checkpoint)
                del span_reports[branch_report_start:]
                for edge, _, _ in EDGE_SPECS:
                    no_update = self._heading.record_action_no_update(
                        branch_id=branch.branch_id,
                        edge=edge,
                        chronological_index=oriented.chronological_index,
                        action=oriented.action,
                        base_common_physical_time_s=base_time_s,
                        cause="ORDINARY_HELDOUT_ROOTED_ASSEMBLY_FAILURE_BRANCH_ROLLBACK",
                    )
                    span_reports.append(dict(no_update))
                trajectory[branch.branch_id] = self._heading.assemble_action_rooted_trajectory(
                    branch.branch_id,
                    chronological_index=oriented.chronological_index,
                    action=oriented.action,
                    base_common_physical_time_s=base_time_s,
                )
                span_reports.append({
                    "schema": "biospur-c2-heldout-heading-branch-rollback-evidence-v1",
                    "branch_id": branch.branch_id,
                    "action": oriented.action,
                    "chronological_index": int(oriented.chronological_index),
                    "exception_type": type(exc).__name__,
                    "exception_message": str(exc),
                    "entire_branch_evaluation_heading_state_rolled_back": True,
                    "replacement": "EXPLICIT_NINE_EDGE_LOCAL_NO_UPDATE",
                })
        return trajectory, span_reports

    def _physical_inputs(
        self,
        oriented: OrientedAction,
        pair_by_edge: Mapping[str, AlignedPair],
        heading_by_branch: Mapping[str, Any],
    ) -> tuple[
        dict[str, Mapping[str, np.ndarray]],
        dict[str, Mapping[str, np.ndarray]],
        dict[str, Mapping[str, Any]],
        Mapping[str, Any],
    ]:
        root_node = self._node_by_segment["pelvis"]
        root_time = np.asarray(oriented.time_us_by_node[root_node], dtype=np.int64)
        quantiles = np.asarray(
            self.settings["physical_candidates"]["trajectory_sample_quantiles"], dtype=float,
        )
        candidates = np.unique(np.rint(quantiles * (len(root_time) - 1)).astype(np.int64))
        tolerance_s = float(
            self.settings["physical_candidates"]["maximum_pair_projection_time_error_s"]
        )
        segment_indices: dict[str, list[int]] = {segment: [] for segment in SEGMENTS}
        timing_variance: dict[str, list[float]] = {segment: [] for segment in SEGMENTS}
        accepted_root: list[int] = []
        rejected: list[dict[str, Any]] = []
        for root_index in candidates:
            current = {"pelvis": int(root_index)}
            current_timing = {
                "pelvis": float(self.settings["physical_candidates"]["root_clock_sigma_s"]) ** 2
            }
            reason: str | None = None
            for edge, parent, child in EDGE_SPECS:
                pair = pair_by_edge.get(edge)
                if pair is None:
                    reason = f"{edge}:NO_FROZEN_CLOCK_ALIGNMENT"
                    break
                parent_node = self._node_by_segment[parent]
                parent_source = np.asarray(pair.alignment.parent_indices, dtype=np.int64)
                child_source = np.asarray(pair.alignment.child_indices, dtype=np.int64)
                target = current[parent]
                parent_times = np.asarray(oriented.time_us_by_node[parent_node], dtype=np.int64)
                distances = np.abs(parent_times[parent_source].astype(float) - float(parent_times[target])) * 1e-6
                local = int(np.argmin(distances))
                if float(distances[local]) > tolerance_s:
                    reason = f"{edge}:PARENT_PROJECTION_OUTSIDE_REGISTERED_TOLERANCE"
                    break
                current[child] = int(child_source[local])
                current_timing[child] = current_timing[parent] + float(
                    pair.alignment.report["lag_uncertainty_s"]
                ) ** 2
            if reason is not None or set(current) != set(SEGMENTS):
                rejected.append({"root_source_index": int(root_index), "reason": reason})
                continue
            accepted_root.append(int(root_index))
            for segment in SEGMENTS:
                segment_indices[segment].append(current[segment])
                timing_variance[segment].append(current_timing[segment])
        if not accepted_root:
            raise ValueError("heldout action has no physical sample mapped through the frozen nine-edge clock tree")
        selected = {
            segment: np.asarray(values, dtype=np.int64) for segment, values in segment_indices.items()
        }
        base_time_s = root_time[np.asarray(accepted_root, dtype=np.int64)].astype(float) * 1e-6
        world_from_sensor: dict[str, np.ndarray] = {}
        covariance: dict[str, np.ndarray] = {}
        for segment in SEGMENTS:
            node = self._node_by_segment[segment]
            world_from_sensor[segment] = qmt_wxyz_to_scipy_active(
                oriented.quat_world_sensor_wxyz_by_node[node][selected[segment]]
            ).as_matrix()
            covariance[segment] = self._orientation_covariance(
                oriented,
                segment=segment,
                source_indices=selected[segment],
                timing_sigma_s=np.sqrt(np.asarray(timing_variance[segment], dtype=float)),
            )
        trajectories: dict[str, Mapping[str, np.ndarray]] = {}
        covariances: dict[str, Mapping[str, np.ndarray]] = {}
        bindings: dict[str, Mapping[str, Any]] = {}
        by_id = {branch.branch_id: branch for branch in self._branches}
        root_selection = np.asarray(accepted_root, dtype=np.int64)
        for branch_id, heading in heading_by_branch.items():
            branch = by_id[branch_id]
            trajectory: dict[str, np.ndarray] = {}
            total_covariance: dict[str, np.ndarray] = {}
            for segment in SEGMENTS:
                raw = np.einsum(
                    "nij,jk->nik", world_from_sensor[segment], branch.sensor_from_segment[segment],
                )
                global_delta = np.asarray(
                    heading.segment_global_delta_rad[segment], dtype=float,
                )[root_selection]
                global_variance = np.asarray(
                    heading.segment_global_variance_rad2[segment], dtype=float,
                )[root_selection]
                yaw = Rotation.from_rotvec(np.column_stack((
                    np.zeros(len(global_delta)), np.zeros(len(global_delta)), global_delta,
                ))).as_matrix()
                trajectory[segment] = np.einsum("nij,njk->nik", yaw, raw)
                heading_covariance = np.zeros_like(covariance[segment])
                heading_covariance[:, 2, 2] = global_variance
                total_covariance[segment] = covariance[segment] + heading_covariance
            binding = {
                "schema": "biospur-c2-runtime-owned-physical-prefix-input-v1",
                "runtime_owner_id": self._owner_id,
                "branch_id": branch_id,
                "chronological_index": int(oriented.chronological_index),
                "action": oriented.action,
                "base_common_physical_time_s": base_time_s.tolist(),
                "base_common_physical_time_s_sha256": _array_sha256(base_time_s),
                "source_indices_sha256": {
                    segment: _array_sha256(value) for segment, value in selected.items()
                },
                "world_from_segment_sha256": {
                    segment: _array_sha256(value) for segment, value in trajectory.items()
                },
                "orientation_covariance_sha256": {
                    segment: _array_sha256(value) for segment, value in total_covariance.items()
                },
                "rooted_qmt_trajectory_report": dict(heading.report),
                "rooted_qmt_trajectory_common_time_sha256": _array_sha256(
                    heading.common_physical_time_s
                ),
                "source": "OFFICIAL_QMT_ROOTED_PARENT_PLUS_CHILD_CORRECTED_FROZEN_HELDOUT_ORIENTED_ACTION",
                "raw_unqmt_orientation_allowed_to_drive_physical_gate": False,
                "qmt_branch_evidence_ingested_before_physical_gate": False,
                "future_episode_or_caller_pose_truth_used": False,
                "calibration_geometry_frame_branch_or_weight_updated": False,
            }
            payload_sha, token = physical_input_binding_token(self._binding_secret, binding)
            binding.update({
                "runtime_owner_binding_payload_sha256": payload_sha,
                "runtime_owner_token": token,
            })
            trajectories[branch_id] = MappingProxyType(trajectory)
            covariances[branch_id] = MappingProxyType(total_covariance)
            bindings[branch_id] = MappingProxyType(binding)
        return trajectories, covariances, bindings, {
            "schema": "biospur-c2-frozen-heldout-physical-input-audit-v1",
            "candidate_root_indices": candidates.tolist(),
            "accepted_root_indices": accepted_root,
            "rejected_root_samples": rejected,
            "training_rows_entered_physical_metric": False,
            "official_qmt_rooted_trajectory_required": True,
        }

    def evaluate_action(self, decoded: DecodedEvaluationAction) -> FrozenHeldoutActionEvaluation:
        if decoded.combined_action.chronological_index != self._next_index:
            raise RuntimeError("heldout evaluation actions must follow exact sealed chronology")
        expected_action = str(self.settings["execution_contract"]["chronological_actions"][self._next_index])
        if decoded.combined_action.action != expected_action:
            raise RuntimeError("heldout evaluation action differs from registered chronology")
        uncertainty_audit_start = len(self._orientation_uncertainty_audits)
        full_oriented = self._orientation.process(decoded.combined_action)
        self._full_oriented_actions.append(full_oriented)
        heldout = _heldout_oriented_action(full_oriented, decoded)
        self._next_index += 1
        root_node = self._node_by_segment["pelvis"]
        root_time = np.asarray(heldout.time_us_by_node[root_node], dtype=np.int64)
        root_boot = np.asarray(heldout.derived_boot_epoch_by_node[root_node], dtype=np.int64)
        base_grid_eligible = bool(
            len(root_time) >= 1
            and len(np.unique(root_boot)) == 1
            and np.all(np.diff(root_time) > 0)
        )
        pair_by_edge: dict[str, AlignedPair] = {}
        pair_failures: dict[str, Any] = {}
        if base_grid_eligible:
            for edge, _, _ in EDGE_SPECS:
                try:
                    pair_by_edge[edge] = self._frozen_pair(heldout, edge)
                except (ValueError, RuntimeError) as exc:
                    pair_failures[edge] = {
                        "exception_type": type(exc).__name__,
                        "exception_message": str(exc),
                        "ordinary_local_no_update": True,
                    }
        else:
            pair_failures = {
                edge: {
                    "exception_type": "UnknownBootOrUnusableRootGrid",
                    "exception_message": "No exact elapsed time was fabricated across a heldout root boot/reset boundary.",
                    "ordinary_local_no_update": True,
                }
                for edge, _, _ in EDGE_SPECS
            }
        arrays: dict[str, np.ndarray] = {}
        assessments: dict[str, PhysicalTrajectoryCandidateAssessment] = {}
        span_reports: list[Mapping[str, Any]] = []
        physical_audit: Mapping[str, Any] = {
            "status": "LOCAL_NO_UPDATE_UNRESOLVED",
            "reason": "ROOT_TIME_UNAVAILABLE" if not base_grid_eligible else "INCOMPLETE_FROZEN_CLOCK_TREE",
        }
        physical_branch_failures: dict[str, Any] = {}
        if base_grid_eligible:
            base_time_s = root_time.astype(float) * 1e-6
            heading, span_reports = self._heading_for_action(
                heldout, pair_by_edge, base_time_s=base_time_s,
            )
            try:
                trajectories, covariances, bindings, physical_audit = self._physical_inputs(
                    heldout, pair_by_edge, heading,
                )
            except (ValueError, RuntimeError) as exc:
                physical_audit = {
                    "status": "ORDINARY_POST_QMT_PHYSICAL_LOCAL_NO_UPDATE",
                    "exception_type": type(exc).__name__,
                    "exception_message": str(exc),
                    "calibration_state_updated": False,
                }
            else:
                by_id = {branch.branch_id: branch for branch in self._branches}
                for branch_id in trajectories:
                    guard_checkpoint = self._guard.checkpoint()
                    try:
                        assessment = self._fk.assess_prefix_trajectory(
                            frame_branch=by_id[branch_id],
                            world_from_segment_trajectory=trajectories[branch_id],
                            orientation_tangent_covariance_rad2=covariances[branch_id],
                            owner_input_binding=bindings[branch_id],
                        )
                    except ClassAGuardViolation:
                        raise
                    except (ValueError, RuntimeError, AssertionError) as exc:
                        self._guard.restore(guard_checkpoint)
                        physical_branch_failures[branch_id] = {
                            "exception_type": type(exc).__name__,
                            "exception_message": str(exc),
                            "ordinary_branch_local_no_update": True,
                            "guard_state_rolled_back": True,
                            "heading_and_frozen_calibration_state_changed": False,
                            "ik_rebase_repair_used": False,
                        }
                        continue
                    assessments[branch_id] = assessment
                    prefix = f"heldout/{self._next_index - 1:02d}/{branch_id}"
                    arrays[f"{prefix}/common_physical_time_s"] = np.asarray(
                        bindings[branch_id]["base_common_physical_time_s"], dtype=float,
                    )
                    for segment in SEGMENTS:
                        arrays[f"{prefix}/world_from_segment/{segment}"] = np.asarray(
                            trajectories[branch_id][segment], dtype=float,
                        )
                        arrays[f"{prefix}/orientation_covariance/{segment}"] = np.asarray(
                            covariances[branch_id][segment], dtype=float,
                        )
        else:
            for branch, retained in zip(self._branches, self._hard_support, strict=True):
                if not retained:
                    continue
                for edge, _, _ in EDGE_SPECS:
                    span_reports.append(self._heading.record_action_unknown_interval_no_update(
                        branch_id=branch.branch_id,
                        edge=edge,
                        chronological_index=heldout.chronological_index,
                        action=heldout.action,
                        cause="HELDOUT_ROOT_BOOT_RESET_OR_UNUSABLE_GRID_UNKNOWN_INTERVAL_LOCAL_NO_UPDATE",
                    ))
        supported_weight = float(np.sum(self._branch_weights[self._hard_support]))
        evaluated_mask = np.asarray([
            branch_id in assessments for branch_id in self._branch_ids
        ], dtype=bool)
        legal_mask = np.asarray([
            bool(assessments[branch_id].physically_legal) if branch_id in assessments else False
            for branch_id in self._branch_ids
        ], dtype=bool)
        evaluated_mass = float(np.sum(
            self._branch_weights[self._hard_support & evaluated_mask]
        ) / max(supported_weight, np.finfo(float).eps))
        legal_mass = float(np.sum(
            self._branch_weights[self._hard_support & legal_mask]
        ) / max(supported_weight, np.finfo(float).eps))
        nll = [
            float(row["prequential_nll_sum"])
            for row in span_reports if int(row.get("prequential_effective_epoch_count", 0)) > 0
        ]
        epochs = [
            int(row["prequential_effective_epoch_count"])
            for row in span_reports if int(row.get("prequential_effective_epoch_count", 0)) > 0
        ]
        coverage_rows = [
            (
                float(row["prequential_coverage_3sigma_fraction"]),
                int(row["prequential_effective_epoch_count"]),
            )
            for row in span_reports if int(row.get("prequential_effective_epoch_count", 0)) > 0
        ]
        effective_epochs = int(np.sum(epochs))
        maximum_closure = 0.0
        for assessment in assessments.values():
            for row in assessment.report["topology_samples"]:
                maximum_closure = max(
                    maximum_closure,
                    float(row.get("maximum_shared_joint_closure_error_m", 0.0)),
                )
        report = {
            "schema": "biospur-c2-frozen-scientific-heldout-action-evaluation-v1",
            "action": heldout.action,
            "chronological_index": int(heldout.chronological_index),
            "status": (
                "SCIENTIFIC_HELDOUT_ACTION_EVALUATED"
                if assessments else "ORDINARY_LOCAL_NO_UPDATE_NOT_EVALUABLE"
            ),
            "heldout_oriented_action_audit": dict(heldout.audit),
            "pair_clock_alignment_failures": pair_failures,
            "official_qmt_span_reports": span_reports,
            "physical_input_audit": dict(physical_audit),
            "physical_branch_failures": physical_branch_failures,
            "physical_assessments": {
                branch_id: {
                    "physically_legal": bool(value.physically_legal),
                    "rom_log_likelihood": float(value.rom_log_likelihood),
                    "bilateral_log_likelihood": float(value.bilateral_log_likelihood),
                    "gravity_log_likelihood": float(value.gravity_log_likelihood),
                    "soft_total_log_likelihood": float(value.soft_total_log_likelihood),
                    "hard_rejection_codes": list(value.report["hard_rejection_codes"]),
                    "report": dict(value.report),
                }
                for branch_id, value in assessments.items()
            },
            "orientation_uncertainty_index_mapping_audits": [
                dict(row)
                for row in self._orientation_uncertainty_audits[uncertainty_audit_start:]
            ],
            "metrics": {
                "supported_branch_weight_evaluated_fraction": evaluated_mass,
                "posterior_weighted_physical_legal_mass": legal_mass,
                "official_qmt_effective_epoch_count": effective_epochs,
                "official_qmt_prequential_nll_per_effective_epoch": (
                    None if not effective_epochs else float(np.sum(nll) / effective_epochs)
                ),
                "official_qmt_prequential_3sigma_coverage": (
                    None if not coverage_rows else float(
                        sum(value * count for value, count in coverage_rows)
                        / sum(count for _, count in coverage_rows)
                    )
                ),
                "maximum_shared_joint_closure_error_m": maximum_closure,
            },
            "frozen_branch_ids": list(self._branch_ids),
            "frozen_branch_weights": self._branch_weights.tolist(),
            "frozen_hard_support_mask": self._hard_support.tolist(),
            "branch_reweight_or_lock_performed": False,
            "geometry_frame_clock_calibration_or_threshold_updated": False,
            "training_rows_entered_scientific_metric": False,
            "calibration_digest_unchanged": self._calibration_digest()
            == self._frozen_calibration_digest,
        }
        self._action_reports.append(report)
        frozen_arrays = {name: np.asarray(value).copy() for name, value in arrays.items()}
        for value in frozen_arrays.values():
            value.setflags(write=False)
        return FrozenHeldoutActionEvaluation(
            action=heldout.action,
            chronological_index=heldout.chronological_index,
            report=MappingProxyType(report),
            arrays=MappingProxyType(frozen_arrays),
        )

    def finalize(self) -> Mapping[str, Any]:
        chronology = tuple(str(value) for value in self.settings["execution_contract"]["chronological_actions"])
        if self._next_index != len(chronology) or tuple(
            str(row["action"]) for row in self._action_reports
        ) != chronology:
            raise RuntimeError("heldout global verdict requires every exact sealed action")
        metrics = [row["metrics"] for row in self._action_reports]
        evaluable = [
            row for row in self._action_reports
            if row["status"] == "SCIENTIFIC_HELDOUT_ACTION_EVALUATED"
        ]
        evaluable_metrics = [row["metrics"] for row in evaluable]
        total_epochs = int(sum(row["official_qmt_effective_epoch_count"] for row in metrics))
        nll_numerator = sum(
            float(row["official_qmt_prequential_nll_per_effective_epoch"])
            * int(row["official_qmt_effective_epoch_count"])
            for row in metrics if row["official_qmt_prequential_nll_per_effective_epoch"] is not None
        )
        coverage_numerator = sum(
            float(row["official_qmt_prequential_3sigma_coverage"])
            * int(row["official_qmt_effective_epoch_count"])
            for row in metrics if row["official_qmt_prequential_3sigma_coverage"] is not None
        )
        aggregate = {
            "action_count": len(self._action_reports),
            "scientifically_evaluable_action_count": len(evaluable),
            "scientifically_evaluable_action_fraction": len(evaluable) / len(chronology),
            "minimum_supported_branch_weight_evaluated_fraction_across_evaluable_actions": (
                None if not evaluable_metrics else min(
                    row["supported_branch_weight_evaluated_fraction"]
                    for row in evaluable_metrics
                )
            ),
            "minimum_posterior_weighted_physical_legal_mass_across_evaluable_actions": (
                None if not evaluable_metrics else min(
                    row["posterior_weighted_physical_legal_mass"]
                    for row in evaluable_metrics
                )
            ),
            "official_qmt_effective_epoch_count": total_epochs,
            "official_qmt_prequential_nll_per_effective_epoch": (
                None if not total_epochs else nll_numerator / total_epochs
            ),
            "official_qmt_prequential_3sigma_coverage": (
                None if not total_epochs else coverage_numerator / total_epochs
            ),
            "maximum_shared_joint_closure_error_m": max(
                row["maximum_shared_joint_closure_error_m"] for row in metrics
            ),
            "calibration_digest_unchanged": self._calibration_digest()
            == self._frozen_calibration_digest,
        }
        criteria = self._criteria["global_verdict_criteria"]
        checks = {
            "exact_registered_action_count": aggregate["action_count"]
            == int(criteria["required_action_count"]),
            "minimum_scientifically_evaluable_action_fraction": (
                aggregate["scientifically_evaluable_action_fraction"]
                >= float(criteria["minimum_scientifically_evaluable_action_fraction"])
            ),
            "minimum_supported_branch_weight_evaluated_fraction": (
                aggregate[
                    "minimum_supported_branch_weight_evaluated_fraction_across_evaluable_actions"
                ] is not None
                and aggregate[
                    "minimum_supported_branch_weight_evaluated_fraction_across_evaluable_actions"
                ] >= float(criteria["minimum_supported_branch_weight_evaluated_fraction"])
            ),
            "minimum_posterior_weighted_physical_legal_mass": (
                aggregate[
                    "minimum_posterior_weighted_physical_legal_mass_across_evaluable_actions"
                ] is not None
                and aggregate[
                    "minimum_posterior_weighted_physical_legal_mass_across_evaluable_actions"
                ] >= float(criteria["minimum_posterior_weighted_physical_legal_mass"])
            ),
            "minimum_official_qmt_effective_epochs": total_epochs
            >= int(criteria["minimum_official_qmt_effective_epochs"]),
            "maximum_heading_prequential_nll": (
                aggregate["official_qmt_prequential_nll_per_effective_epoch"] is not None
                and aggregate["official_qmt_prequential_nll_per_effective_epoch"]
                <= float(criteria["maximum_heading_prequential_nll_per_effective_epoch"])
            ),
            "minimum_heading_prequential_3sigma_coverage": (
                aggregate["official_qmt_prequential_3sigma_coverage"] is not None
                and aggregate["official_qmt_prequential_3sigma_coverage"]
                >= float(criteria["minimum_heading_prequential_3sigma_coverage"])
            ),
            "maximum_shared_joint_closure_error": (
                aggregate["maximum_shared_joint_closure_error_m"]
                <= float(criteria["maximum_shared_joint_closure_error_m"])
            ),
            "frozen_calibration_state_unchanged": bool(
                aggregate["calibration_digest_unchanged"]
            ),
        }
        passed = bool(all(checks.values()))
        return {
            "schema": "biospur-c2-frozen-scientific-heldout-global-verdict-v1",
            "status": "PASS" if passed else "FAIL",
            "pass": passed,
            "aggregate_metrics": aggregate,
            "preregistered_criteria": dict(criteria),
            "criterion_checks": checks,
            "action_summaries": [
                {
                    "action": row["action"],
                    "chronological_index": row["chronological_index"],
                    "status": row["status"],
                    "metrics": dict(row["metrics"]),
                }
                for row in self._action_reports
            ],
            "calibration_fit_refit_branch_reweight_threshold_tuning_or_feedback_used": False,
            "input_health_diagnostic_is_scientific_metric": False,
            "official_qmt_rooted_nine_edge_and_direct_scientific_fk_required": True,
            "failure_is_preserved_scientific_evidence_not_a_pipeline_repair_trigger": True,
        }


def numeric_frozen_heldout_transaction_isolation_gate(
    settings: Mapping[str, Any],
    initial_stochastic_state: Mapping[str, Any],
    branches: Sequence[SegmentFrameBranch],
) -> Mapping[str, Any]:
    """Inject failures through the actual frozen-heldout catch/rollback paths."""

    if len(branches) < 2:
        raise ValueError("heldout transaction gate requires at least two retained frame branches")
    actions = tuple(
        str(value) for value in settings["execution_contract"]["chronological_actions"]
    )
    node_by_segment = {
        str(row["body_segment"]): str(row["hardware_id"])
        for row in settings["segment_frames"]["wear_authority"]["rows"]
    }
    if set(node_by_segment) != set(SEGMENTS):
        raise ValueError("heldout transaction fixture lacks exact ten-node identity")
    tested_branches = tuple(branches[:2])

    def new_heading_owner() -> tuple[FrozenScientificHeldoutEvaluationOwner, C2ExecutionGuard]:
        guard = C2ExecutionGuard(settings)
        guard.begin_capture("C2")
        seed = PersistentHeadingOwner(
            settings["heading"], (tested_branches[0],),
            execution_guard=guard,
            first_chronological_index=0,
        )
        heading = PersistentHeadingOwner.from_frozen_evaluation_state(
            settings["heading"], (tested_branches[0],),
            execution_guard=guard,
            frozen_edge_state=seed.frozen_evaluation_state()["edge_state"],
        )
        owner = object.__new__(FrozenScientificHeldoutEvaluationOwner)
        owner.settings = settings
        owner._branches = (tested_branches[0],)
        owner._hard_support = np.array([True], dtype=bool)
        owner._heading = heading
        owner._node_by_segment = node_by_segment
        owner._owner_id = "C2_SYNTHETIC_FROZEN_HELDOUT_QMT_TRANSACTION_OWNER"
        return owner, guard

    count = max(
        3,
        int(np.ceil(
            float(settings["heading"]["explicit_est_settings"]["windowTime"])
            / float(settings["orientation"]["sample_period_s"])
        )) + 2,
    )
    sample_period_s = float(settings["orientation"]["sample_period_s"])
    time_s = np.arange(count, dtype=float) * sample_period_s
    time_us = np.rint(time_s * 1e6).astype(np.int64) + 1_000_000
    phase = np.arange(count, dtype=float) * 0.031
    quaternion = np.tile(np.array([1.0, 0.0, 0.0, 0.0]), (count, 1))
    oriented = OrientedAction(
        action=actions[0],
        chronological_index=0,
        time_us_by_node={node: time_us.copy() for node in node_by_segment.values()},
        derived_boot_epoch_by_node={
            node: np.zeros(count, dtype=np.int64) for node in node_by_segment.values()
        },
        contiguous_span_id_by_node={
            node: np.zeros(count, dtype=np.int64) for node in node_by_segment.values()
        },
        acc_mps2_by_node={
            node: np.column_stack((
                0.05 * np.sin(phase),
                0.04 * np.cos(phase),
                np.full(count, 9.80665),
            ))
            for node in node_by_segment.values()
        },
        gyro_rads_by_node={
            node: np.column_stack((
                0.15 * np.sin(phase + 0.03 * node_index),
                0.45 * np.cos(phase - 0.02 * node_index),
                0.11 * np.sin(0.7 * phase + 0.01 * node_index),
            ))
            for node_index, node in enumerate(node_by_segment.values())
        },
        quat_world_sensor_wxyz_by_node={
            node: quaternion.copy() for node in node_by_segment.values()
        },
        gap_only_orientation_covariance_rad2_by_node={
            node: np.zeros((count, 3, 3), dtype=float)
            for node in node_by_segment.values()
        },
        vqf_residual_bias_rad_s_by_node={
            node: np.zeros((count, 3), dtype=float) for node in node_by_segment.values()
        },
        vqf_residual_bias_sigma_rad_s_by_node={
            node: np.full(count, 0.002, dtype=float) for node in node_by_segment.values()
        },
        vqf_rest_detected_by_node={
            node: np.zeros(count, dtype=bool) for node in node_by_segment.values()
        },
        audit={"synthetic_actual_heldout_heading_wrapper_fixture": True},
    )
    target_edge, parent_segment, child_segment = EDGE_SPECS[0]
    parent_node = node_by_segment[parent_segment]
    child_node = node_by_segment[child_segment]
    indices = np.arange(count, dtype=np.int64)
    pair = AlignedPair(
        edge=target_edge,
        action=actions[0],
        parent_acc=np.asarray(oriented.acc_mps2_by_node[parent_node]),
        child_acc=np.asarray(oriented.acc_mps2_by_node[child_node]),
        parent_gyro=np.asarray(oriented.gyro_rads_by_node[parent_node]),
        child_gyro=np.asarray(oriented.gyro_rads_by_node[child_node]),
        parent_observed_time_s=np.asarray(
            oriented.time_us_by_node[parent_node], dtype=float,
        ) * 1e-6,
        child_observed_time_s=np.asarray(
            oriented.time_us_by_node[child_node], dtype=float,
        ) * 1e-6,
        parent_boot_epoch=np.asarray(
            oriented.derived_boot_epoch_by_node[parent_node], dtype=np.int64,
        ),
        child_boot_epoch=np.asarray(
            oriented.derived_boot_epoch_by_node[child_node], dtype=np.int64,
        ),
        alignment=PairAlignment(
            parent_indices=indices,
            child_indices=indices,
            lag_samples=0,
            report={
                "lag_uncertainty_s": float(settings["timing"]["jitter_floor_s"]),
                "sample_period_s": sample_period_s,
            },
        ),
        contiguous_spans=(slice(0, count),),
        provenance=MappingProxyType({
            "schema": "biospur-c2-frozen-heldout-transaction-pair-fixture-v1",
        }),
    )
    base0 = time_s.copy()
    base1 = time_s + 20.0

    mutated_owner, _ = new_heading_owner()
    real_process_span = mutated_owner._heading.process_span
    injected_qmt_observed: dict[str, Any] = {
        "real_owner_call_completed": False,
        "qmt_executed": False,
        "official_estimation_epoch_count": 0,
    }

    def process_then_fail(**kwargs: Any) -> Any:
        result = real_process_span(**kwargs)
        injected_qmt_observed.update({
            "real_owner_call_completed": True,
            "qmt_executed": bool(result.report["qmt_executed"]),
            "official_estimation_epoch_count": int(
                result.report["official_estimation_epoch_count"]
            ),
        })
        if not injected_qmt_observed["qmt_executed"]:
            raise AssertionError("transaction mutation reached only a short/non-QMT span")
        raise RuntimeError("INJECTED_ORDINARY_FAILURE_AFTER_REAL_HELDOUT_QMT_OWNER_CALL")

    mutated_owner._heading.process_span = process_then_fail
    mutated_trajectory, mutated_reports = (
        FrozenScientificHeldoutEvaluationOwner._heading_for_action(
            mutated_owner,
            oriented,
            {target_edge: pair},
            base_time_s=base0,
        )
    )
    mutated_owner._heading.process_span = real_process_span
    mutated_after_action0 = _semantic_sha256(
        mutated_owner._heading.frozen_evaluation_state()
    )

    clean_owner, _ = new_heading_owner()
    clean_trajectory, _ = FrozenScientificHeldoutEvaluationOwner._heading_for_action(
        clean_owner,
        oriented,
        {},
        base_time_s=base0,
    )
    clean_after_action0 = _semantic_sha256(clean_owner._heading.frozen_evaluation_state())
    next_trajectory, next_reports = FrozenScientificHeldoutEvaluationOwner._heading_for_action(
        mutated_owner,
        OrientedAction(
            action=actions[1],
            chronological_index=1,
            time_us_by_node=oriented.time_us_by_node,
            derived_boot_epoch_by_node=oriented.derived_boot_epoch_by_node,
            contiguous_span_id_by_node=oriented.contiguous_span_id_by_node,
            acc_mps2_by_node=oriented.acc_mps2_by_node,
            gyro_rads_by_node=oriented.gyro_rads_by_node,
            quat_world_sensor_wxyz_by_node=oriented.quat_world_sensor_wxyz_by_node,
            gap_only_orientation_covariance_rad2_by_node=(
                oriented.gap_only_orientation_covariance_rad2_by_node
            ),
            vqf_residual_bias_rad_s_by_node=oriented.vqf_residual_bias_rad_s_by_node,
            vqf_residual_bias_sigma_rad_s_by_node=(
                oriented.vqf_residual_bias_sigma_rad_s_by_node
            ),
            vqf_rest_detected_by_node=oriented.vqf_rest_detected_by_node,
            audit={"synthetic_next_action_after_qmt_rollback": True},
        ),
        {},
        base_time_s=base1,
    )
    qmt_failure_rows = [
        row for row in mutated_reports
        if row.get("ordinary_failure", {}).get("exception_message")
        == "INJECTED_ORDINARY_FAILURE_AFTER_REAL_HELDOUT_QMT_OWNER_CALL"
    ]
    qmt_pass = bool(
        injected_qmt_observed["real_owner_call_completed"]
        and injected_qmt_observed["qmt_executed"]
        and injected_qmt_observed["official_estimation_epoch_count"] > 0
        and len(qmt_failure_rows) == 1
        and qmt_failure_rows[0]["ordinary_failure"][
            "partial_evaluation_heading_state_rolled_back"
        ]
        and mutated_after_action0 == clean_after_action0
        and set(mutated_trajectory) == set(clean_trajectory)
        and set(next_trajectory) == {tested_branches[0].branch_id}
        and len(next_reports) == len(EDGE_SPECS)
    )

    physical_guard = C2ExecutionGuard(settings)
    physical_guard.begin_capture("C2")
    owner = object.__new__(FrozenScientificHeldoutEvaluationOwner)
    owner.settings = settings
    owner._criteria = settings["heldout_evaluation"]
    owner._branches = tested_branches
    owner._branch_ids = tuple(branch.branch_id for branch in tested_branches)
    owner._hard_support = np.ones(len(tested_branches), dtype=bool)
    owner._branch_weights = np.full(len(tested_branches), 1.0 / len(tested_branches))
    owner._guard = physical_guard
    owner._owner_id = "C2_SYNTHETIC_FROZEN_HELDOUT_PHYSICAL_TRANSACTION_OWNER"
    owner._binding_secret = bytes(range(32))
    owner._fk = ScientificForwardKinematicsOwner(
        execution_guard=physical_guard,
        physical_settings=settings["physical_candidates"],
        expected_runtime_owner_id=owner._owner_id,
        runtime_binding_secret=owner._binding_secret,
    )
    owner._node_by_segment = node_by_segment
    owner._initial = initial_stochastic_state
    owner._initial_semantic_sha256 = str(
        settings["execution_contract"]["initial_stochastic_state_semantic_sha256"]
    )
    owner._full_oriented_actions = []
    owner._orientation_uncertainty_audits = []
    owner._action_reports = []
    owner._next_index = 0
    owner._arrays = {}
    owner._frozen_calibration_digest = owner._calibration_digest()

    rows_by_node: dict[str, np.ndarray] = {}
    fixture_count = 20
    for node_index, node in enumerate(node_by_segment.values()):
        rows = np.zeros(fixture_count, dtype=IMU_DTYPE)
        rows["node_timer_us"] = (
            50_000_000 + np.arange(fixture_count, dtype=np.uint64) * 5_000
        )
        rows["derived_boot_epoch"] = 0
        rows["imu_sample_sequence"] = np.arange(fixture_count, dtype=np.uint16)
        rows["acc_raw"][:, 2] = 2_048
        rows["gyro_raw"][:, 1] = (
            30.0 * np.sin(np.arange(fixture_count) * 0.2 + node_index * 0.01)
        ).astype(np.int16)
        rows["decode_acceptance_status"] = 1
        rows_by_node[node] = rows
    decoded_action = DecodedAction(
        action=actions[0],
        chronological_index=0,
        interval=(0, 1),
        rows_by_node=rows_by_node,
        access_audit={"synthetic_frozen_heldout_physical_transaction": True},
        decode_audit={"synthetic_frozen_heldout_physical_transaction": True},
    )
    decoded = DecodedEvaluationAction(
        combined_action=decoded_action,
        prefit_interval=(0, 0),
        heldout_interval=(0, 1),
        heldout_source_indices_by_node={
            node: np.arange(fixture_count, dtype=np.int64)
            for node in node_by_segment.values()
        },
        access_audit={"synthetic_frozen_heldout_physical_transaction": True},
        decode_audit={"synthetic_frozen_heldout_physical_transaction": True},
    )
    full_oriented_fixture = OrientedAction(
        action=actions[0],
        chronological_index=0,
        time_us_by_node={
            node: np.asarray(rows["node_timer_us"], dtype=np.int64)
            for node, rows in rows_by_node.items()
        },
        derived_boot_epoch_by_node={
            node: np.zeros(fixture_count, dtype=np.int64)
            for node in node_by_segment.values()
        },
        contiguous_span_id_by_node={
            node: np.zeros(fixture_count, dtype=np.int64)
            for node in node_by_segment.values()
        },
        acc_mps2_by_node={
            node: np.column_stack((
                np.zeros(fixture_count),
                np.zeros(fixture_count),
                np.full(fixture_count, 9.80665),
            ))
            for node in node_by_segment.values()
        },
        gyro_rads_by_node={
            node: np.zeros((fixture_count, 3), dtype=float)
            for node in node_by_segment.values()
        },
        quat_world_sensor_wxyz_by_node={
            node: np.tile(np.array([1.0, 0.0, 0.0, 0.0]), (fixture_count, 1))
            for node in node_by_segment.values()
        },
        gap_only_orientation_covariance_rad2_by_node={
            node: np.zeros((fixture_count, 3, 3), dtype=float)
            for node in node_by_segment.values()
        },
        vqf_residual_bias_rad_s_by_node={
            node: np.zeros((fixture_count, 3), dtype=float)
            for node in node_by_segment.values()
        },
        vqf_residual_bias_sigma_rad_s_by_node={
            node: np.full(fixture_count, 0.002)
            for node in node_by_segment.values()
        },
        vqf_rest_detected_by_node={
            node: np.zeros(fixture_count, dtype=bool)
            for node in node_by_segment.values()
        },
        audit={"synthetic_actual_evaluate_action_fixture": True},
    )

    class OrientationFixture:
        def process(self, _: DecodedAction) -> OrientedAction:
            return full_oriented_fixture

    owner._orientation = OrientationFixture()
    simple_indices = np.arange(fixture_count, dtype=np.int64)
    simple_pair = AlignedPair(
        edge="SYNTHETIC_REBOUND_PER_EDGE",
        action=actions[0],
        parent_acc=np.zeros((fixture_count, 3)),
        child_acc=np.zeros((fixture_count, 3)),
        parent_gyro=np.zeros((fixture_count, 3)),
        child_gyro=np.zeros((fixture_count, 3)),
        parent_observed_time_s=np.arange(fixture_count, dtype=float) * sample_period_s,
        child_observed_time_s=np.arange(fixture_count, dtype=float) * sample_period_s,
        parent_boot_epoch=np.zeros(fixture_count, dtype=np.int64),
        child_boot_epoch=np.zeros(fixture_count, dtype=np.int64),
        alignment=PairAlignment(
            parent_indices=simple_indices,
            child_indices=simple_indices,
            lag_samples=0,
            report={"lag_uncertainty_s": float(settings["timing"]["jitter_floor_s"])},
        ),
        contiguous_spans=(slice(0, fixture_count),),
        provenance=MappingProxyType({
            "schema": "biospur-c2-frozen-heldout-physical-transaction-pair-fixture-v1",
        }),
    )
    owner._frozen_pair = lambda oriented_action, edge: AlignedPair(
        edge=edge,
        action=oriented_action.action,
        parent_acc=simple_pair.parent_acc,
        child_acc=simple_pair.child_acc,
        parent_gyro=simple_pair.parent_gyro,
        child_gyro=simple_pair.child_gyro,
        parent_observed_time_s=simple_pair.parent_observed_time_s,
        child_observed_time_s=simple_pair.child_observed_time_s,
        parent_boot_epoch=simple_pair.parent_boot_epoch,
        child_boot_epoch=simple_pair.child_boot_epoch,
        alignment=simple_pair.alignment,
        contiguous_spans=simple_pair.contiguous_spans,
        provenance=simple_pair.provenance,
    )
    owner._heading_for_action = lambda oriented_action, pair_by_edge, base_time_s: (
        {branch.branch_id: object() for branch in tested_branches},
        [],
    )
    segment_names = tuple(SEGMENTS)
    rotation = {
        branch.branch_id: {
            segment: np.tile(np.eye(3), (3, 1, 1)) for segment in segment_names
        }
        for branch in tested_branches
    }
    covariance = {
        branch.branch_id: {
            segment: np.tile(np.eye(3) * np.deg2rad(8.0) ** 2, (3, 1, 1))
            for segment in segment_names
        }
        for branch in tested_branches
    }

    def binding_for(branch: SegmentFrameBranch) -> Mapping[str, Any]:
        trajectory = rotation[branch.branch_id]
        uncertainty = covariance[branch.branch_id]
        base = np.array([50.0, 50.05, 50.095], dtype=float)
        payload = {
            "schema": "biospur-c2-runtime-owned-physical-prefix-input-v1",
            "runtime_owner_id": owner._owner_id,
            "branch_id": branch.branch_id,
            "chronological_index": 0,
            "action": actions[0],
            "base_common_physical_time_s": base.tolist(),
            "base_common_physical_time_s_sha256": _array_sha256(base),
            "source_indices_sha256": {
                segment: _array_sha256(np.array([0, 10, 19], dtype=np.int64))
                for segment in segment_names
            },
            "world_from_segment_sha256": {
                segment: _array_sha256(value) for segment, value in trajectory.items()
            },
            "orientation_covariance_sha256": {
                segment: _array_sha256(value) for segment, value in uncertainty.items()
            },
            "rooted_qmt_trajectory_report": {
                "tree_semantics": "child_global = parent_global + time_varying_edge_deltaFilt",
            },
            "rooted_qmt_trajectory_common_time_sha256": _array_sha256(base),
            "source": "OFFICIAL_QMT_ROOTED_PARENT_PLUS_CHILD_CORRECTED_FROZEN_HELDOUT_ORIENTED_ACTION",
            "raw_unqmt_orientation_allowed_to_drive_physical_gate": False,
            "qmt_branch_evidence_ingested_before_physical_gate": False,
            "future_episode_or_caller_pose_truth_used": False,
            "calibration_geometry_frame_branch_or_weight_updated": False,
        }
        payload_sha, token = physical_input_binding_token(owner._binding_secret, payload)
        return {
            **payload,
            "runtime_owner_binding_payload_sha256": payload_sha,
            "runtime_owner_token": token,
        }

    bindings = {
        branch.branch_id: binding_for(branch) for branch in tested_branches
    }
    owner._physical_inputs = lambda oriented_action, pair_by_edge, heading_by_branch: (
        rotation,
        covariance,
        bindings,
        {
            "schema": "biospur-c2-frozen-heldout-physical-transaction-fixture-v1",
            "official_qmt_rooted_trajectory_required": True,
        },
    )
    real_assess = owner._fk.assess_prefix_trajectory
    injected_physical = {
        "real_owner_call_completed": False,
        "capture_id_corrupted_after_real_owner_call": False,
    }

    def assess_then_fail(**kwargs: Any) -> PhysicalTrajectoryCandidateAssessment:
        assessment = real_assess(**kwargs)
        if not injected_physical["real_owner_call_completed"]:
            injected_physical["real_owner_call_completed"] = True
            owner._guard.capture_id = "INJECTED_PARTIAL_PHYSICAL_BRANCH_STATE"
            injected_physical["capture_id_corrupted_after_real_owner_call"] = True
            raise RuntimeError(
                "INJECTED_ORDINARY_FAILURE_AFTER_REAL_HELDOUT_PHYSICAL_BRANCH_OWNER_CALL"
            )
        return assessment

    owner._fk.assess_prefix_trajectory = assess_then_fail
    physical_result = FrozenScientificHeldoutEvaluationOwner.evaluate_action(owner, decoded)
    first_branch_id = tested_branches[0].branch_id
    second_branch_id = tested_branches[1].branch_id
    physical_failure = physical_result.report["physical_branch_failures"].get(first_branch_id)
    physical_assessments = physical_result.report["physical_assessments"]
    physical_pass = bool(
        injected_physical["real_owner_call_completed"]
        and injected_physical["capture_id_corrupted_after_real_owner_call"]
        and physical_failure is not None
        and physical_failure["ordinary_branch_local_no_update"]
        and physical_failure["guard_state_rolled_back"]
        and physical_failure["exception_message"]
        == "INJECTED_ORDINARY_FAILURE_AFTER_REAL_HELDOUT_PHYSICAL_BRANCH_OWNER_CALL"
        and owner._guard.capture_id == "C2"
        and first_branch_id not in physical_assessments
        and second_branch_id in physical_assessments
    )
    return {
        "schema": "biospur-c2-frozen-heldout-ordinary-failure-transaction-gate-v2",
        "coverage_class": "EXECUTED_OWNER_LEVEL",
        "qmt": {
            **injected_qmt_observed,
            "product_path": (
                "FrozenScientificHeldoutEvaluationOwner._heading_for_action"
            ),
            "product_failure_record": qmt_failure_rows,
            "mutated_state_equals_clean_no_update_after_product_rollback": (
                mutated_after_action0 == clean_after_action0
            ),
            "next_action_continued_through_product_path": (
                set(next_trajectory) == {tested_branches[0].branch_id}
            ),
            "fixture_called_restore": False,
            "pass": qmt_pass,
        },
        "physical_branch": {
            **injected_physical,
            "product_path": "FrozenScientificHeldoutEvaluationOwner.evaluate_action",
            "product_failure_record": physical_failure,
            "product_guard_restored_exact_capture_identity": owner._guard.capture_id == "C2",
            "failed_branch_excluded": first_branch_id not in physical_assessments,
            "next_branch_continued": second_branch_id in physical_assessments,
            "fixture_called_restore": False,
            "pass": physical_pass,
        },
        "calibration_state_was_not_available_to_mutate": True,
        "ik_rebase_or_repair_used": False,
        "post_qmt_positive_coverage_claimed_by_this_transaction_fixture": False,
        "pass": bool(qmt_pass and physical_pass),
    }
