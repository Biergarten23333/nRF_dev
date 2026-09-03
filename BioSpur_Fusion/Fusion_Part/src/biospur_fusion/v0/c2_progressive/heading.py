"""Branch-bound official-QMT heading observations with persistent edge posteriors."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import hashlib
from typing import Any, Mapping, Sequence

import numpy as np
import qmt
from scipy.spatial.transform import Rotation

from .architecture_guard import C2ExecutionGuard, ROOTED_EDGES
from .functional_geometry import EDGE_SPECS, HINGE_EDGES
from .quaternion_contract import qmt_wxyz_to_scipy_active, scipy_active_to_qmt_wxyz
from .segment_frames import SegmentFrameBranch


EDGE_BY_NAME = {edge: (parent, child) for edge, parent, child in EDGE_SPECS}
EDGE_NAME_BY_ENDPOINTS = {(parent, child): edge for edge, parent, child in EDGE_SPECS}
NONHINGE_EDGES = tuple(edge for edge in EDGE_BY_NAME if edge not in HINGE_EDGES)
UNOBSERVED_NONHINGE_HEADING_S1_QUADRATURE_COUNT = 8


@dataclass(frozen=True)
class HeadingSpanResult:
    corrected_child_segment_quaternion_wxyz: np.ndarray
    official_qmt_corrected_child_segment_quaternion_wxyz: np.ndarray
    qmt_observation_delta_rad: np.ndarray
    persistent_delta_filt_rad: np.ndarray
    qmt_rating: np.ndarray
    qmt_state_out: np.ndarray
    posterior_variance_rad2: np.ndarray
    report: Mapping[str, Any]


@dataclass(frozen=True)
class HeadingTrajectoryResult:
    action: str
    chronological_index: int
    common_physical_time_s: np.ndarray
    edge_delta_filt_rad: Mapping[str, np.ndarray]
    edge_variance_rad2: Mapping[str, np.ndarray]
    edge_direct_observation_mask: Mapping[str, np.ndarray]
    segment_global_delta_rad: Mapping[str, np.ndarray]
    segment_global_variance_rad2: Mapping[str, np.ndarray]
    report: Mapping[str, Any]


def _array_sha256(value: np.ndarray) -> str:
    array = np.ascontiguousarray(np.asarray(value))
    return hashlib.sha256(array.view(np.uint8)).hexdigest()


def _debug_evidence(value: Any) -> Any:
    """Bound QMT debug output without serializing interpolator objects."""

    if isinstance(value, Mapping):
        return {str(key): _debug_evidence(item) for key, item in value.items()}
    if isinstance(value, np.ndarray):
        array = np.asarray(value)
        output: dict[str, Any] = {
            "type": "ndarray", "shape": list(array.shape), "dtype": str(array.dtype),
            "sha256_contiguous_bytes": _array_sha256(array),
        }
        if np.issubdtype(array.dtype, np.number) and array.size:
            finite = np.asarray(array, dtype=float)[np.isfinite(np.asarray(array, dtype=float))]
            output.update({
                "finite_count": int(len(finite)),
                "minimum": float(np.min(finite)) if len(finite) else None,
                "maximum": float(np.max(finite)) if len(finite) else None,
            })
        return output
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return {"type": type(value).__name__, "serialized": False}


def _wrap_near(value: float, reference: float) -> float:
    return float(reference + np.arctan2(np.sin(value - reference), np.cos(value - reference)))


def _factorized_unobserved_nonhinge_heading_support(
    edge_state: Mapping[tuple[str, str], Mapping[str, float | int]],
    *,
    branch_id: str,
) -> dict[str, list[dict[str, Any]]]:
    """Materialize full circular support when QMT supplied no observation.

    A zero-valued carried mean is a numeric coordinate, not heading evidence.
    Eight fixed offsets cover the complete circle for each unobserved 3-DoF
    edge independently. They are not combined into an 8**5 body-pose grid.
    """

    support: dict[str, list[dict[str, Any]]] = {}
    offsets = (
        np.arange(UNOBSERVED_NONHINGE_HEADING_S1_QUADRATURE_COUNT, dtype=float)
        * 2.0 * np.pi
        / float(UNOBSERVED_NONHINGE_HEADING_S1_QUADRATURE_COUNT)
    )
    for edge in NONHINGE_EDGES:
        key = (branch_id, edge)
        if key not in edge_state:
            raise ValueError(f"{branch_id}:{edge}: heading state is absent")
        state = edge_state[key]
        observation_count = int(state["observation_count"])
        if observation_count:
            continue
        carried_mean = float(state["delta_rad"])
        variance = float(state["variance_rad2"])
        if not np.isfinite(carried_mean) or not np.isfinite(variance) or variance <= 0.0:
            raise ValueError(f"{branch_id}:{edge}: unobserved heading state is invalid")
        support[edge] = [
            {
                "candidate_id": f"{edge}:UNOBSERVED_HEADING_S1_Q{index:02d}",
                "offset_from_carried_coordinate_rad": float(offset),
                "candidate_delta_rad": float(
                    np.arctan2(
                        np.sin(carried_mean + float(offset)),
                        np.cos(carried_mean + float(offset)),
                    )
                ),
                "normalized_factorized_weight": (
                    1.0 / float(UNOBSERVED_NONHINGE_HEADING_S1_QUADRATURE_COUNT)
                ),
                "carried_coordinate_delta_rad": carried_mean,
                "carried_variance_rad2": variance,
                "official_observation_count": 0,
                "official_qmt_or_local_likelihood_used": False,
                "retained": True,
            }
            for index, offset in enumerate(offsets)
        ]
    return support


def _qmt_compatible_local_time(
    common_physical_time_s: np.ndarray,
    *,
    data_rate_hz: float,
) -> tuple[np.ndarray, Mapping[str, Any]]:
    """Return QMT's row-equivalent local numeric time coordinate.

    QMT 0.2.4 requires its inferred input rate to be an exact integer
    multiple of ``dataRate``.  The owner-produced physical clock remains the
    authority for chronology, gap growth, assembly, and output timestamps;
    this local coordinate only avoids floating timer-rate modulo failure
    inside the official callable and never changes rows or observations.
    """

    physical = np.asarray(common_physical_time_s, dtype=float)
    if physical.ndim != 1 or len(physical) < 3:
        raise ValueError("QMT local time requires at least three physical rows")
    differences = np.diff(physical)
    if not np.isfinite(differences).all() or np.any(differences <= 0.0):
        raise ValueError("QMT local time requires finite increasing physical time")
    observed_dt = float(np.median(differences))
    observed_rate = 1.0 / observed_dt
    if not np.isfinite(data_rate_hz) or data_rate_hz <= 0.0:
        raise ValueError("QMT dataRate must be positive and finite")
    quantized_sample_period_us = int(np.rint(observed_dt * 1e6))
    if quantized_sample_period_us <= 0:
        raise ValueError("QMT physical sample period cannot quantize to integer microseconds")
    compatible_rate = 1e6 / float(quantized_sample_period_us)
    rate_ratio = compatible_rate / float(data_rate_hz)
    multiplier = int(np.rint(rate_ratio))
    if multiplier < 1 or not np.isclose(rate_ratio, multiplier, rtol=0.0, atol=1e-12):
        raise ValueError(
            "QMT integer-microsecond row rate is not divisible by registered dataRate"
        )
    local = (
        np.arange(len(physical), dtype=float)
        * float(quantized_sample_period_us)
        * 1e-6
    )
    normalized_physical = physical - physical[0]
    return local, {
        "schema": "biospur-c2-qmt-row-equivalent-local-time-v1",
        "role": "OFFICIAL_QMT_NUMERIC_COORDINATE_ONLY",
        "physical_time_remains_authoritative": True,
        "physical_time_sha256": _array_sha256(physical),
        "row_count": int(len(physical)),
        "rows_resampled_interpolated_or_reordered": False,
        "observed_median_sample_period_s": observed_dt,
        "observed_median_rate_hz": observed_rate,
        "quantized_sample_period_us": quantized_sample_period_us,
        "maximum_native_step_quantization_error_us": float(
            np.max(np.abs(differences * 1e6 - quantized_sample_period_us))
        ),
        "registered_qmt_data_rate_hz": float(data_rate_hz),
        "compatible_rate_multiplier": multiplier,
        "qmt_local_rate_hz": compatible_rate,
        "integer_microsecond_rate_exactly_divisible_by_data_rate": True,
        "maximum_absolute_local_coordinate_difference_s": float(
            np.max(np.abs(local - normalized_physical))
        ),
    }


class PersistentHeadingOwner:
    """One initialized-once circle posterior for every retained branch/edge."""

    def __init__(
        self,
        settings: Mapping[str, Any],
        retained_branches: Sequence[SegmentFrameBranch],
        *,
        execution_guard: C2ExecutionGuard,
        first_chronological_index: int = 0,
    ) -> None:
        if not retained_branches:
            raise ValueError("heading owner requires posterior segment-frame branches")
        self.settings = settings
        self.execution_guard = execution_guard
        self._branches = {branch.branch_id: branch for branch in retained_branches}
        if len(self._branches) != len(retained_branches):
            raise ValueError("retained segment-frame branch IDs must be unique")
        for branch in retained_branches:
            if set(branch.segment_from_sensor) != {name for edge in ROOTED_EDGES for name in edge}:
                raise ValueError("heading owner requires ten posterior SO(3) frames per branch")
        initial_variance = float(settings["persistent_filter"]["initial_relative_heading_sigma_rad"]) ** 2
        self._edge_state: dict[tuple[str, str], dict[str, float | int]] = {
            (branch.branch_id, edge): {
                "delta_rad": 0.0,
                "variance_rad2": initial_variance,
                "span_count": 0,
                "observation_count": 0,
            }
            for branch in retained_branches for edge in EDGE_BY_NAME
        }
        self._span_reports: list[Mapping[str, Any]] = []
        self._last_physical_time_s: dict[tuple[str, str], float] = {}
        self._last_action_index: dict[tuple[str, str], int] = {}
        self._span_records: dict[tuple[str, str, int], list[dict[str, Any]]] = {}
        self._action_start_prior: dict[tuple[str, str, int], dict[str, float | None]] = {}
        self._external_nonhinge_circular_prior_records: list[dict[str, Any]] = []
        self._external_nonhinge_circular_state: dict[
            tuple[str, str], dict[str, Any]
        ] = {}
        self._external_nonhinge_prefix_edge_identity: dict[
            tuple[int, str], tuple[str, str]
        ] = {}
        self._first_chronological_index = int(first_chronological_index)
        if not 0 <= self._first_chronological_index < len(self.execution_guard.chronological_actions):
            raise ValueError("heading first chronological index is outside sealed chronology")
        for state_key in self._edge_state:
            self._last_action_index[state_key] = self._first_chronological_index - 1

    def update_frame_branches(self, retained_branches: Sequence[SegmentFrameBranch]) -> None:
        updated = {branch.branch_id: branch for branch in retained_branches}
        if set(updated) != set(self._branches):
            raise ValueError("progressive frame update cannot create, remove, or relabel heading branch state")
        changed_segments_by_branch: dict[str, set[str]] = {}
        for branch_id, replacement in updated.items():
            previous = self._branches[branch_id]
            changed = {
                segment
                for segment in replacement.sensor_from_segment
                if not np.array_equal(
                    np.asarray(previous.sensor_from_segment[segment], dtype=float),
                    np.asarray(replacement.sensor_from_segment[segment], dtype=float),
                )
            }
            changed_segments_by_branch[branch_id] = changed
        unsafe = []
        for branch_id, changed_segments in changed_segments_by_branch.items():
            if not changed_segments:
                continue
            for edge, (parent, child) in EDGE_BY_NAME.items():
                if parent not in changed_segments and child not in changed_segments:
                    continue
                state = self._edge_state[(branch_id, edge)]
                if (
                    int(state["span_count"]) > 0
                    or int(state["observation_count"]) > 0
                    or (branch_id, edge) in self._external_nonhinge_circular_state
                ):
                    unsafe.append({
                        "branch_id": branch_id,
                        "edge": edge,
                        "changed_endpoint_segments": sorted(
                            {parent, child} & changed_segments
                        ),
                        "span_count": int(state["span_count"]),
                        "observation_count": int(state["observation_count"]),
                        "external_circular_state_present": (
                            (branch_id, edge)
                            in self._external_nonhinge_circular_state
                        ),
                    })
        if unsafe:
            raise ValueError(
                "segment-frame coordinates changed after heading state existed; "
                "use one coherent corrected-frame replay or an explicit "
                f"state-coordinate migration: {unsafe}"
            )
        self._branches = updated

    @classmethod
    def from_frozen_evaluation_state(
        cls,
        settings: Mapping[str, Any],
        retained_branches: Sequence[SegmentFrameBranch],
        *,
        execution_guard: C2ExecutionGuard,
        frozen_edge_state: Sequence[Mapping[str, Any]],
    ) -> "PersistentHeadingOwner":
        """Create an evaluation-only state copy from a frozen calibration prior.

        The returned owner may evolve trajectory state while processing
        heldout motion, but it has no reference to and cannot mutate the
        calibration owner from which ``frozen_edge_state`` was exported.
        """

        owner = cls(
            settings,
            retained_branches,
            execution_guard=execution_guard,
            first_chronological_index=0,
        )
        expected = {
            (branch.branch_id, edge)
            for branch in retained_branches for edge in EDGE_BY_NAME
        }
        observed = {
            (str(row.get("branch_id")), str(row.get("edge")))
            for row in frozen_edge_state
        }
        if observed != expected or len(frozen_edge_state) != len(expected):
            raise ValueError("frozen heading state does not bind every retained branch/edge exactly once")
        restored: dict[tuple[str, str], dict[str, float | int]] = {}
        for row in frozen_edge_state:
            branch_id = str(row["branch_id"])
            edge = str(row["edge"])
            delta = float(row["delta_rad"])
            variance = float(row["variance_rad2"])
            span_count = int(row["span_count"])
            observation_count = int(row["observation_count"])
            if (
                not np.isfinite(delta)
                or not np.isfinite(variance)
                or variance <= 0.0
                or span_count < 0
                or observation_count < 0
            ):
                raise ValueError("frozen heading state contains an invalid posterior row")
            restored[(branch_id, edge)] = {
                "delta_rad": delta,
                "variance_rad2": variance,
                "span_count": span_count,
                "observation_count": observation_count,
            }
        owner._edge_state = restored
        owner._last_physical_time_s = {}
        owner._last_action_index = {key: -1 for key in restored}
        owner._span_records = {}
        owner._span_reports = []
        owner._action_start_prior = {}
        return owner

    @property
    def branch_ids(self) -> tuple[str, ...]:
        return tuple(self._branches)

    def factorized_unobserved_nonhinge_support(
        self,
        branch_id: str,
    ) -> Mapping[str, Sequence[Mapping[str, Any]]]:
        if branch_id not in self._branches:
            raise ValueError("unobserved heading support requires a retained branch")
        support = _factorized_unobserved_nonhinge_heading_support(
            self._edge_state, branch_id=branch_id,
        )
        externally_bound = {
            str(record["edge"])
            for record in self._external_nonhinge_circular_prior_records
            if record["branch_id"] == branch_id
        }
        return {
            edge: rows for edge, rows in support.items()
            if edge not in externally_bound
        }

    def bind_external_nonhinge_circular_prior(
        self,
        *,
        branch_id: str,
        edge: str,
        chronological_index: int,
        action: str,
        delta_grid_rad: np.ndarray,
        posterior_weights: np.ndarray,
        owner_input_binding: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        """Bind one immutable full-S1 posterior at the scalar QMT boundary.

        The existing QMT owner stores one Gaussian coordinate per edge.  This
        adapter moment-matches the external circular posterior only for that
        narrow state interface.  The complete grid and weights remain the
        authoritative evidence and are hash-bound in the returned audit.  A
        numerically uniform circle carries the previous coordinate and widens
        variance; it never turns a zero coordinate or an S1 argmax into
        heading evidence.
        """

        if branch_id not in self._branches or edge not in NONHINGE_EDGES:
            raise ValueError("external circular prior requires one retained nonhinge edge")
        chronology = tuple(self.execution_guard.chronological_actions)
        if (
            chronological_index < 0
            or chronological_index >= len(chronology)
            or action != chronology[chronological_index]
        ):
            raise ValueError("external circular prior differs from sealed chronology")
        grid = np.asarray(delta_grid_rad, dtype=float)
        weights = np.asarray(posterior_weights, dtype=float)
        if (
            grid.ndim != 1
            or len(grid) < 8
            or weights.shape != grid.shape
            or not np.isfinite(grid).all()
            or not np.isfinite(weights).all()
            or np.any(weights < 0.0)
            or float(np.sum(weights)) <= 0.0
        ):
            raise ValueError("external circular prior arrays are invalid")
        if np.any(np.diff(grid) <= 0.0) or float(grid[-1] - grid[0]) >= 2.0 * np.pi:
            raise ValueError("external circular prior grid must be one ordered S1 coordinate")
        binding = dict(owner_input_binding)
        expected_binding = {
            "schema": "biospur-c2-external-nonhinge-circular-prior-input-v1",
            "branch_id": branch_id,
            "edge": edge,
            "chronological_index": int(chronological_index),
            "action": action,
            "delta_grid_rad_sha256": _array_sha256(grid),
            "posterior_weights_sha256": _array_sha256(weights),
        }
        for key, expected in expected_binding.items():
            if binding.get(key) != expected:
                raise ValueError(f"external circular prior binding differs at {key}")
        if not isinstance(binding.get("source_audit_sha256"), str) or len(
            binding["source_audit_sha256"]
        ) != 64:
            raise ValueError("external circular prior lacks its immutable source-audit hash")

        normalized = weights / float(np.sum(weights))
        circular_first_moment = np.sum(normalized * np.exp(1j * grid))
        resultant = float(np.abs(circular_first_moment))
        state = self._edge_state[(branch_id, edge)]
        previous_coordinate = float(state["delta_rad"])
        numerical_uniform_tolerance = 32.0 * np.finfo(float).eps
        coordinate_informed = resultant > numerical_uniform_tolerance
        coordinate = (
            float(np.angle(circular_first_moment))
            if coordinate_informed else previous_coordinate
        )
        matched_variance = float(
            -2.0 * np.log(max(resultant, np.finfo(float).tiny))
        )
        if not np.isfinite(matched_variance) or matched_variance <= 0.0:
            matched_variance = float(np.finfo(float).eps)
        prefix_edge_key = (int(chronological_index), edge)
        branch_independent_identity = (
            expected_binding["delta_grid_rad_sha256"],
            expected_binding["posterior_weights_sha256"],
        )
        existing_identity = self._external_nonhinge_prefix_edge_identity.setdefault(
            prefix_edge_key, branch_independent_identity,
        )
        if existing_identity != branch_independent_identity:
            raise ValueError(
                "external nonhinge posterior differs across retained hinge branches"
            )
        state_key = (branch_id, edge)
        previous_external = self._external_nonhinge_circular_state.get(state_key)
        if (
            previous_external is not None
            and int(previous_external["chronological_index"]) >= int(chronological_index)
        ):
            raise ValueError(
                "external cumulative posterior must bind exactly once in increasing chronology"
            )
        self._external_nonhinge_circular_state[state_key] = {
            "chronological_index": int(chronological_index),
            "coordinate_rad": coordinate,
            "variance_rad2": matched_variance,
            "delta_grid_rad_sha256": expected_binding["delta_grid_rad_sha256"],
            "posterior_weights_sha256": expected_binding["posterior_weights_sha256"],
            "source_audit_sha256": binding["source_audit_sha256"],
            "posterior_resultant": resultant,
        }
        combined_coordinate, combined_variance = self._combined_edge_state(
            branch_id, edge,
        )
        record = {
            "schema": "biospur-c2-external-nonhinge-circular-prior-binding-audit-v1",
            **expected_binding,
            "source_audit_sha256": binding["source_audit_sha256"],
            "normalized_weights_sha256": _array_sha256(normalized),
            "grid_cell_count": int(len(grid)),
            "posterior_resultant": resultant,
            "moment_matched_coordinate_rad": coordinate,
            "moment_matched_variance_rad2": matched_variance,
            "combined_output_coordinate_rad": combined_coordinate,
            "combined_output_variance_rad2": combined_variance,
            "coordinate_informed_by_external_posterior": coordinate_informed,
            "numerically_uniform_circle_carried_previous_coordinate": not coordinate_informed,
            "previous_coordinate_rad": previous_coordinate,
            "full_s1_grid_remains_authoritative": True,
            "scalar_gaussian_used_only_for_existing_qmt_filter_interface": True,
            "map_or_argmax_used": False,
            "zero_heading_asserted": False,
            "weak_resultant_maps_to_broad_variance": True,
            "external_prefix_posterior_is_cumulative_and_replaces_previous_external_factor": True,
            "external_posterior_multiplied_as_new_independent_evidence": False,
            "official_qmt_state_coordinate_before_and_after_rad": [
                float(state["delta_rad"]), float(state["delta_rad"]),
            ],
            "official_qmt_state_variance_before_and_after_rad2": [
                float(state["variance_rad2"]), float(state["variance_rad2"]),
            ],
            "official_qmt_observation_count_when_bound": int(
                state["observation_count"]
            ),
            "official_qmt_state_erased_or_double_counted": False,
            "branch_independent_grid_and_weight_hashes_enforced": True,
        }
        self._external_nonhinge_circular_prior_records.append(record)
        return dict(record)

    def _combined_edge_state(self, branch_id: str, edge: str) -> tuple[float, float]:
        """Return QMT plus the latest replace-only external circular factor."""

        official = self._edge_state[(branch_id, edge)]
        official_mean = float(official["delta_rad"])
        official_variance = float(official["variance_rad2"])
        external = self._external_nonhinge_circular_state.get((branch_id, edge))
        if external is None:
            return official_mean, official_variance
        return self._combine_official_and_external_values(
            official_mean=official_mean,
            official_variance=official_variance,
            official_observation_count=int(official["observation_count"]),
            external_mean=float(external["coordinate_rad"]),
            external_variance=float(external["variance_rad2"]),
        )

    @staticmethod
    def _combine_official_and_external_values(
        *,
        official_mean: float,
        official_variance: float,
        official_observation_count: int,
        external_mean: float,
        external_variance: float,
    ) -> tuple[float, float]:
        external_mean = _wrap_near(float(external_mean), float(official_mean))
        if int(official_observation_count) == 0:
            # QMT's broad carried coordinate is not an observation.  Retain
            # its uncertainty without pulling the external circular moment
            # toward an arbitrary zero coordinate.
            return external_mean, official_variance + external_variance
        official_precision = 1.0 / official_variance
        external_precision = 1.0 / external_variance
        combined_variance = 1.0 / (official_precision + external_precision)
        combined_mean = combined_variance * (
            official_precision * official_mean
            + external_precision * external_mean
        )
        return float(combined_mean), float(combined_variance)

    def derive_retrospective_rooted_trajectory(
        self,
        trajectory: HeadingTrajectoryResult,
        *,
        branch_id: str,
        external_prior_chronological_index: int,
    ) -> HeadingTrajectoryResult:
        """Apply a later frozen nonhinge prior to an earlier viewer trajectory.

        This owner is deliberately presentation-only: it does not mutate the
        persistent filter or any causal prefix metric.  It reuses the exact
        QMT-only coordinate/variance/count trace after the registered official
        rating/state filter, then combines the later circular factor row by
        row with the same zero-observation sum or observed precision rule as
        the online owner.  Raw QMT ``deltaFilt`` cannot bypass that filter.
        """

        if branch_id not in self._branches:
            raise ValueError("retrospective trajectory requires a retained branch")
        if external_prior_chronological_index <= trajectory.chronological_index:
            raise ValueError("retrospective trajectory requires a strictly later frozen prior")
        target_rows_by_edge = {
            str(record["edge"]): record
            for record in self._external_nonhinge_circular_prior_records
            if (
                record["branch_id"] == branch_id
                and int(record["chronological_index"])
                == int(external_prior_chronological_index)
            )
        }
        source_rows_by_edge = {
            str(record["edge"]): record
            for record in self._external_nonhinge_circular_prior_records
            if (
                record["branch_id"] == branch_id
                and int(record["chronological_index"])
                == int(trajectory.chronological_index)
            )
        }
        if (
            set(target_rows_by_edge) != set(NONHINGE_EDGES)
            or set(source_rows_by_edge) != set(NONHINGE_EDGES)
        ):
            raise ValueError("retrospective trajectory lacks all five frozen nonhinge priors")
        edge_delta = {
            edge: np.asarray(value, dtype=float).copy()
            for edge, value in trajectory.edge_delta_filt_rad.items()
        }
        edge_variance = {
            edge: np.asarray(value, dtype=float).copy()
            for edge, value in trajectory.edge_variance_rad2.items()
        }
        composition_audit: dict[str, Any] = {}
        common_time = np.asarray(trajectory.common_physical_time_s, dtype=float)
        for edge, target_record in target_rows_by_edge.items():
            source_record = source_rows_by_edge[edge]
            records = sorted(
                self._span_records.get(
                    (branch_id, edge, int(trajectory.chronological_index)), []
                ),
                key=lambda row: (
                    float(row["common_physical_time_s"][0])
                    if len(row["common_physical_time_s"]) else np.inf
                ),
            )
            official_trace = np.full(
                len(common_time),
                float(source_record["official_qmt_state_coordinate_before_and_after_rad"][0]),
            )
            official_variance_trace = np.full(
                len(common_time),
                float(source_record["official_qmt_state_variance_before_and_after_rad2"][0]),
            )
            official_count_trace = np.full(
                len(common_time),
                int(source_record["official_qmt_observation_count_when_bound"]),
                dtype=np.int64,
            )
            direct_official = np.zeros(len(common_time), dtype=bool)
            carried_value = float(official_trace[0])
            carried_variance = float(official_variance_trace[0])
            carried_count = int(official_count_trace[0])
            official_span_hashes: list[dict[str, str]] = []
            for span in records:
                span_time = np.asarray(span["common_physical_time_s"], dtype=float)
                filtered = np.asarray(
                    span.get("official_qmt_filtered_delta_rad", []), dtype=float,
                )
                filtered_variance = np.asarray(
                    span.get("official_qmt_filtered_variance_rad2", []), dtype=float,
                )
                filtered_count = np.asarray(
                    span.get("official_qmt_filtered_observation_count", []),
                    dtype=np.int64,
                )
                if (
                    len(span_time) == 0
                    or filtered.shape != span_time.shape
                    or filtered_variance.shape != span_time.shape
                    or filtered_count.shape != span_time.shape
                ):
                    continue
                official_span_hashes.append({
                    "coordinate": _array_sha256(filtered),
                    "variance": _array_sha256(filtered_variance),
                    "observation_count": _array_sha256(filtered_count),
                })
                before = common_time < float(span_time[0])
                if np.any(before) and not np.any(direct_official[before]):
                    official_trace[before] = carried_value
                    official_variance_trace[before] = carried_variance
                    official_count_trace[before] = carried_count
                inside = (
                    (common_time >= float(span_time[0]))
                    & (common_time <= float(span_time[-1]))
                )
                if np.any(inside):
                    official_trace[inside] = np.interp(
                        common_time[inside], span_time, filtered,
                    )
                    official_variance_trace[inside] = np.interp(
                        common_time[inside], span_time, filtered_variance,
                    )
                    indices = np.searchsorted(
                        span_time, common_time[inside], side="right",
                    ) - 1
                    official_count_trace[inside] = filtered_count[
                        np.clip(indices, 0, len(filtered_count) - 1)
                    ]
                    direct_official[inside] = True
                carried_value = float(filtered[-1])
                carried_variance = float(filtered_variance[-1])
                carried_count = int(filtered_count[-1])
                official_trace[common_time > float(span_time[-1])] = carried_value
                official_variance_trace[
                    common_time > float(span_time[-1])
                ] = carried_variance
                official_count_trace[
                    common_time > float(span_time[-1])
                ] = carried_count
            if not official_span_hashes:
                raise ValueError(
                    f"{edge}: retrospective trajectory lacks filtered QMT-only trace"
                )
            target_external_variance = float(
                target_record["moment_matched_variance_rad2"]
            )
            target_external_mean = float(
                target_record["moment_matched_coordinate_rad"]
            )
            composed = np.empty(len(common_time), dtype=float)
            composed_variance = np.empty(len(common_time), dtype=float)
            for index in range(len(common_time)):
                composed[index], composed_variance[index] = (
                    self._combine_official_and_external_values(
                        official_mean=float(official_trace[index]),
                        official_variance=float(official_variance_trace[index]),
                        official_observation_count=int(official_count_trace[index]),
                        external_mean=target_external_mean,
                        external_variance=target_external_variance,
                    )
                )
            edge_delta[edge] = composed
            edge_variance[edge] = composed_variance
            composition_audit[edge] = {
                "official_qmt_filtered_span_sha256": official_span_hashes,
                "official_filtered_coordinate_trace_sha256": _array_sha256(official_trace),
                "official_filtered_variance_trace_sha256": _array_sha256(
                    official_variance_trace
                ),
                "official_filtered_observation_count_trace_sha256": _array_sha256(
                    official_count_trace
                ),
                "composed_trace_sha256": _array_sha256(composed),
                "composed_variance_trace_sha256": _array_sha256(
                    composed_variance
                ),
                "direct_official_common_grid_rows": int(
                    np.count_nonzero(direct_official)
                ),
                "official_filtered_coordinate_range_rad": float(
                    np.ptp(np.unwrap(official_trace))
                ),
                "maximum_official_observation_count": int(
                    np.max(official_count_trace)
                ),
                "same_online_sum_or_precision_combination_used_row_by_row": True,
                "raw_official_deltafilt_bypassed_rating_state_filter": False,
                "variance_recovered_by_subtracting_source_external_variance": False,
            }
        global_delta = {
            "pelvis": np.asarray(
                trajectory.segment_global_delta_rad["pelvis"], dtype=float,
            ).copy(),
        }
        global_variance = {
            "pelvis": np.asarray(
                trajectory.segment_global_variance_rad2["pelvis"], dtype=float,
            ).copy(),
        }
        for parent, child in ROOTED_EDGES:
            edge = EDGE_NAME_BY_ENDPOINTS[(parent, child)]
            global_delta[child] = self.execution_guard.propagate_heading(
                global_delta[parent], edge_delta[edge],
                mode="TIME_VARYING_PARENT_PLUS_CHILD_DELTAFILT",
            )
            global_variance[child] = global_variance[parent] + edge_variance[edge]
        return HeadingTrajectoryResult(
            action=trajectory.action,
            chronological_index=trajectory.chronological_index,
            common_physical_time_s=np.asarray(
                trajectory.common_physical_time_s, dtype=float,
            ).copy(),
            edge_delta_filt_rad=edge_delta,
            edge_variance_rad2=edge_variance,
            edge_direct_observation_mask={
                edge: np.asarray(value, dtype=bool).copy()
                for edge, value in trajectory.edge_direct_observation_mask.items()
            },
            segment_global_delta_rad=global_delta,
            segment_global_variance_rad2=global_variance,
            report={
                **dict(trajectory.report),
                "schema": "biospur-c2-retrospective-external-nonhinge-prior-rooted-heading-trajectory-v1",
                "source_causal_chronological_index": int(trajectory.chronological_index),
                "external_prior_chronological_index": int(
                    external_prior_chronological_index
                ),
                "external_nonhinge_prior_bindings": {
                    edge: {
                        key: record[key]
                        for key in (
                            "delta_grid_rad_sha256", "posterior_weights_sha256",
                            "source_audit_sha256", "posterior_resultant",
                            "moment_matched_coordinate_rad",
                            "moment_matched_variance_rad2",
                        )
                    }
                    for edge, record in target_rows_by_edge.items()
                },
                "nonhinge_official_deltafilt_composition": composition_audit,
                "tree_semantics": "child_global = parent_global + time_varying_edge_deltaFilt",
                "official_hinge_deltafilt_recomputed_or_replaced": False,
                "official_nonhinge_filtered_trace_replaced_by_constant": False,
                "persistent_owner_state_mutated": False,
                "causal_progressive_metric": False,
                "pose_truth": False,
                "viewer_role": "RETROSPECTIVE_VIEWER_ONLY_NOT_CAUSAL_METRIC_OR_POSE_TRUTH",
                "unobserved_nonhinge_heading_edges": [],
                "moment_representative_physical_gate_eligible": True,
                "mean_rooted_trajectory_full_body_physical_gate_eligible": False,
                "full_s1_physical_branch_qualification": False,
            },
        )

    def grow_gap(self, branch_id: str, edge: str, gap_s: float) -> None:
        state = self._edge_state[(branch_id, edge)]
        diffusion = float(self.settings["persistent_filter"]["gap_diffusion_rad2_s"])
        state["variance_rad2"] = float(state["variance_rad2"]) + max(0.0, float(gap_s)) * diffusion

    def _qmt_settings(self, dof: int) -> dict[str, Any]:
        configured = dict(self.settings["explicit_est_settings"])
        configured["stillnessThreshold"] = configured.pop("stillnessThreshold_rad_s")
        configured["deltaRange"] = np.deg2rad(np.linspace(0.0, 359.0, 360))
        configured.pop("deltaRange_rad", None)
        constraints = configured.pop("constraint_by_dof")
        configured["constraint"] = constraints[str(dof)]
        return configured

    def _joint_from_branch(self, branch: SegmentFrameBranch, edge: str) -> tuple[np.ndarray, Mapping[str, Any]]:
        parent, _ = EDGE_BY_NAME[edge]
        if edge in HINGE_EDGES:
            # The signed posterior endpoint axis was used as +y when building
            # this branch's parent frame. Recompute it from that posterior
            # source rather than accepting an arbitrary caller joint vector.
            joint = np.array([0.0, 1.0, 0.0])
            joint_info: Mapping[str, Any] = {}
        else:
            joint = np.eye(3)
            registered = self.settings["joint_construction"]["joint_info_3d"]
            joint_info = {
                "convention": str(registered["convention"]),
                "angle_ranges": np.asarray(registered["angle_ranges_rad"], dtype=float),
            }
        if np.asarray(branch.segment_from_sensor[parent]).shape != (3, 3):
            raise ValueError(f"{edge}: posterior parent frame is not SO(3)")
        return joint, joint_info

    def process_span(
        self,
        *,
        branch_id: str,
        edge: str,
        chronological_index: int,
        action: str,
        parent_gyro_sensor: np.ndarray,
        child_gyro_sensor: np.ndarray,
        parent_quaternion_world_sensor_wxyz: np.ndarray,
        child_quaternion_world_sensor_wxyz: np.ndarray,
        common_physical_time_s: np.ndarray,
        selected_source_row_indices: np.ndarray,
        owner_input_binding: Mapping[str, Any],
        reset_requested: bool = False,
        profile_stitch_requested: bool = False,
    ) -> HeadingSpanResult:
        self.execution_guard.observe_qmt_span(
            edge, reset_requested=reset_requested,
            profile_stitch_requested=profile_stitch_requested,
        )
        if edge not in EDGE_BY_NAME or branch_id not in self._branches:
            raise ValueError("heading span must bind one retained branch and official edge")
        chronology = tuple(self.execution_guard.chronological_actions)
        if chronological_index < 0 or chronological_index >= len(chronology) or action != chronology[chronological_index]:
            raise ValueError("heading span action differs from sealed chronology")
        branch = self._branches[branch_id]
        parent, child = EDGE_BY_NAME[edge]
        arrays = [
            np.asarray(parent_gyro_sensor, dtype=float), np.asarray(child_gyro_sensor, dtype=float),
            np.asarray(parent_quaternion_world_sensor_wxyz, dtype=float),
            np.asarray(child_quaternion_world_sensor_wxyz, dtype=float),
        ]
        time = np.asarray(common_physical_time_s, dtype=float)
        selected = np.asarray(selected_source_row_indices, dtype=np.int64)
        if len({len(value) for value in arrays} | {len(time), len(selected)}) != 1 or len(time) < 3:
            raise ValueError("heading span arrays and selected-row evidence must share at least three rows")
        if not np.isfinite(time).all() or np.any(np.diff(time) <= 0.0):
            raise ValueError("heading span time must be finite and strictly increasing")
        if not np.allclose(np.diff(time), np.diff(time)[0], atol=1e-12, rtol=1e-8):
            raise ValueError("heading owner accepts one equidistant gap-safe span at a time")
        if not np.all(np.diff(selected) == 1):
            raise ValueError("selected source rows must prove one exact contiguous span with unit index increments")
        binding = dict(owner_input_binding)
        expected_binding = {
            "edge": edge,
            "chronological_index": int(chronological_index),
            "action": action,
            "parent_gyro_sha256": _array_sha256(arrays[0]),
            "child_gyro_sha256": _array_sha256(arrays[1]),
            "parent_quaternion_wxyz_sha256": _array_sha256(arrays[2]),
            "child_quaternion_wxyz_sha256": _array_sha256(arrays[3]),
            "common_physical_time_s_sha256": _array_sha256(time),
            "selected_source_row_indices_sha256": _array_sha256(selected),
        }
        if binding.get("schema") != "biospur-c2-runtime-owned-heading-span-input-v1":
            raise ValueError("heading input lacks the runtime-owned span-token schema")
        if not binding.get("runtime_owner_token") or any(binding.get(key) != value for key, value in expected_binding.items()):
            raise ValueError("heading input token/hash does not match the actual current span arrays")
        state_key = (branch_id, edge)
        previous_action_index = self._last_action_index.get(state_key, -1)
        if chronological_index < previous_action_index or chronological_index > previous_action_index + 1:
            raise ValueError("heading edge spans must advance through sealed actions without profile stitching")
        if state_key in self._last_physical_time_s and float(time[0]) <= self._last_physical_time_s[state_key]:
            raise ValueError("heading spans must be chronologically nonoverlapping on one common physical clock")
        action_key = (branch_id, edge, int(chronological_index))
        if action_key not in self._action_start_prior:
            current_mean, current_variance = self._combined_edge_state(
                branch_id, edge,
            )
            self._action_start_prior[action_key] = {
                "delta_rad": current_mean,
                "variance_rad2": current_variance,
                "reference_time_s": self._last_physical_time_s.get(state_key),
            }
        if state_key in self._last_physical_time_s:
            self.grow_gap(branch_id, edge, float(time[0]) - self._last_physical_time_s[state_key])

        parent_s_from_seg = np.asarray(branch.sensor_from_segment[parent], dtype=float)
        child_s_from_seg = np.asarray(branch.sensor_from_segment[child], dtype=float)
        parent_seg_from_s = parent_s_from_seg.T
        child_seg_from_s = child_s_from_seg.T
        parent_gyro = np.einsum("ij,nj->ni", parent_seg_from_s, arrays[0])
        child_gyro = np.einsum("ij,nj->ni", child_seg_from_s, arrays[1])
        parent_world_from_sensor = qmt_wxyz_to_scipy_active(arrays[2]).as_matrix()
        child_world_from_sensor = qmt_wxyz_to_scipy_active(arrays[3]).as_matrix()
        parent_world_from_segment = np.einsum("nij,jk->nik", parent_world_from_sensor, parent_s_from_seg)
        child_world_from_segment = np.einsum("nij,jk->nik", child_world_from_sensor, child_s_from_seg)
        # Use the already audited matrices for the actual frame composition.
        parent_quaternion_segment = scipy_active_to_qmt_wxyz(Rotation.from_matrix(parent_world_from_segment))
        child_quaternion_segment = scipy_active_to_qmt_wxyz(Rotation.from_matrix(child_world_from_segment))
        joint, joint_info = self._joint_from_branch(branch, edge)
        dof = 1 if np.asarray(joint).ndim == 1 else int(len(joint))
        configured = self._qmt_settings(dof)
        qmt_local_time, qmt_time_audit = _qmt_compatible_local_time(
            time,
            data_rate_hz=float(configured["dataRate"]),
        )
        state = self._edge_state[state_key]
        prior_before = dict(state)
        duration = float(time[-1] - time[0])
        minimum_duration = float(configured["windowTime"]) + float(np.diff(time)[0])
        qmt_executed = duration >= minimum_duration
        if qmt_executed:
            result = qmt.headingCorrection(
                np.ascontiguousarray(parent_gyro), np.ascontiguousarray(child_gyro),
                np.ascontiguousarray(parent_quaternion_segment), np.ascontiguousarray(child_quaternion_segment),
                qmt_local_time, joint, dict(joint_info), estSettings=configured,
                verbose=False, debug=True,
            )
            official_quat, delta, delta_filt, rating, state_out, debug = result
            raw = np.asarray(delta_filt, dtype=float).reshape(-1)
            raw_delta = np.asarray(delta, dtype=float).reshape(-1)
            ratings = np.clip(np.asarray(rating, dtype=float).reshape(-1), 0.0, 1.0)
            qmt_states = np.asarray(state_out, dtype=float).reshape(-1)
            debug_evidence = _debug_evidence(debug)
            official_quaternion_sha = _array_sha256(np.asarray(official_quat, dtype=float))
            official_indices = np.asarray(debug["starts"], dtype=int).reshape(-1)
            uninterpolated = debug["uninterpolated"]
            official_delta_filt = np.asarray(uninterpolated["deltaFilt"], dtype=float).reshape(-1)
            official_rating = np.clip(np.asarray(uninterpolated["rating"], dtype=float).reshape(-1), 0.0, 1.0)
            official_state = np.asarray(uninterpolated["stateOut"], dtype=int).reshape(-1)
            if not (
                len(official_indices) == len(official_delta_filt)
                == len(official_rating) == len(official_state)
            ):
                raise RuntimeError("official QMT estimation epoch evidence length mismatch")
        else:
            raw = np.full(len(time), float(state["delta_rad"]))
            raw_delta = raw.copy()
            ratings = np.zeros(len(time), dtype=float)
            qmt_states = np.zeros(len(time), dtype=float)
            debug_evidence = {"status": "SPAN_TOO_SHORT_LOCAL_NO_UPDATE", "required_duration_s": minimum_duration}
            official_quaternion_sha = None
            official_quat = child_quaternion_segment.copy()
            official_indices = np.empty(0, dtype=int)
            official_delta_filt = np.empty(0, dtype=float)
            official_rating = np.empty(0, dtype=float)
            official_state = np.empty(0, dtype=int)

        posterior = np.empty(len(time), dtype=float)
        posterior_variance = np.empty(len(time), dtype=float)
        official_filtered = np.empty(len(time), dtype=float)
        official_filtered_variance = np.empty(len(time), dtype=float)
        official_filtered_observation_count = np.empty(len(time), dtype=np.int64)
        filter_settings = self.settings["persistent_filter"]
        floor = float(filter_settings["rating_variance_floor_rad2"])
        scale = float(filter_settings["rating_variance_scale_rad2"])
        minimum_rating = float(filter_settings["minimum_effective_rating"])
        within_diffusion = float(filter_settings["within_span_diffusion_rad2_s"])
        state_multiplier = {
            int(key): float(value)
            for key, value in filter_settings["official_state_information_multiplier"].items()
        }
        if state_multiplier.get(2) != 0.0 or state_multiplier.get(3) != 0.0:
            raise ValueError("C2 low-information startup/stillness QMT states must contribute zero information")
        if edge in HINGE_EDGES:
            frame_covariance = np.asarray(
                branch.paired_hinge_frame_tangent_covariance_rad2[edge], dtype=float,
            )
            frame_uncertainty_variance = float(np.trace(frame_covariance))
        else:
            frame_uncertainty_variance = float(
                np.trace(np.asarray(branch.frame_tangent_covariance_rad2[parent], dtype=float))
                + np.trace(np.asarray(branch.frame_tangent_covariance_rad2[child], dtype=float))
            )
        frame_uncertainty_variance *= float(filter_settings["frame_tangent_variance_scale"])
        dt = float(np.diff(time)[0])
        official_by_row = {
            int(row): (float(observation), float(rating_value), int(state_value))
            for row, observation, rating_value, state_value in zip(
                official_indices, official_delta_filt, official_rating, official_state,
            )
        }
        update_count_by_state: dict[int, int] = {}
        effective_update_count = 0
        prequential_nll: list[float] = []
        prequential_standardized_residual: list[float] = []
        prequential_coverage_3sigma: list[bool] = []
        for index in range(len(time)):
            state["variance_rad2"] = float(state["variance_rad2"]) + within_diffusion * dt
            if index in official_by_row:
                observation, rating_value, official_state_value = official_by_row[index]
                update_count_by_state[official_state_value] = update_count_by_state.get(official_state_value, 0) + 1
                information_multiplier = state_multiplier.get(official_state_value, 0.0)
            else:
                observation = float(state["delta_rad"])
                rating_value = 0.0
                information_multiplier = 0.0
            observation_near = _wrap_near(observation, float(state["delta_rad"]))
            effective_rating = rating_value * information_multiplier
            if qmt_executed and effective_rating >= minimum_rating:
                observation_variance = floor + frame_uncertainty_variance + scale / max(effective_rating, minimum_rating)
                predictive_variance = float(state["variance_rad2"]) + observation_variance
                innovation = observation_near - float(state["delta_rad"])
                standardized = innovation / np.sqrt(predictive_variance)
                prequential_nll.append(float(
                    0.5 * (np.log(2.0 * np.pi * predictive_variance) + innovation**2 / predictive_variance)
                ))
                prequential_standardized_residual.append(float(standardized))
                prequential_coverage_3sigma.append(bool(abs(standardized) <= 3.0))
                gain = float(state["variance_rad2"]) / (float(state["variance_rad2"]) + observation_variance)
                state["delta_rad"] = float(state["delta_rad"]) + gain * (observation_near - float(state["delta_rad"]))
                state["variance_rad2"] = max(floor, (1.0 - gain) * float(state["variance_rad2"]))
                state["observation_count"] = int(state["observation_count"]) + 1
                effective_update_count += 1
            official_filtered[index] = float(state["delta_rad"])
            official_filtered_variance[index] = float(state["variance_rad2"])
            official_filtered_observation_count[index] = int(
                state["observation_count"]
            )
            posterior[index], posterior_variance[index] = self._combined_edge_state(
                branch_id, edge,
            )
        state["span_count"] = int(state["span_count"]) + 1
        self._last_physical_time_s[state_key] = float(time[-1])
        self._last_action_index[state_key] = int(chronological_index)
        corrected_child = np.asarray(qmt.qmult(
            qmt.quatFromAngleAxis(posterior, [0.0, 0.0, 1.0]),
            child_quaternion_segment,
        ), dtype=float)
        report = {
            "schema": "biospur-c2-persistent-official-qmt-heading-span-v2",
            "official_callable": "qmt.headingCorrection",
            "branch_id": branch_id,
            "edge": edge,
            "chronological_index": int(chronological_index),
            "action": action,
            "parent": parent,
            "child": child,
            "posterior_segment_frames_required": True,
            "caller_supplied_joint_or_joint_info_allowed": False,
            "joint_dof": dof,
            "joint_from_posterior_frame": np.asarray(joint).tolist(),
            "span_rows": len(time),
            "input_equidistant_and_gap_safe": True,
            "qmt_numeric_time_coordinate": qmt_time_audit,
            "selected_source_rows": {
                "count": int(len(selected)), "first": int(selected[0]), "last": int(selected[-1]),
                "sha256_int64_bytes": _array_sha256(selected),
                "time_sha256_float64_bytes": _array_sha256(time),
            },
            "runtime_owned_input_binding": binding,
            "caller_supplied_trajectory_arrays_allowed": False,
            "copied_future_or_relabeled_arrays_rejected": True,
            "qmt_executed": qmt_executed,
            "qmt_short_span_local_no_update": not qmt_executed,
            "qmt_debug_evidence": debug_evidence,
            "official_qmt_corrected_quaternion_sha256": official_quaternion_sha,
            "official_qmt_corrected_quaternion_preserved_in_result": True,
            "official_estimation_epoch_count": int(len(official_indices)),
            "official_estimation_epoch_indices": official_indices.tolist(),
            "persistent_filter_effective_update_count": effective_update_count,
            "external_nonhinge_circular_prior_active": (
                (branch_id, edge) in self._external_nonhinge_circular_state
            ),
            "external_cumulative_posterior_multiplied_per_action": False,
            "official_qmt_state_retained_separately_from_external_prior": True,
            "prequential_effective_epoch_count": len(prequential_nll),
            "prequential_nll_sum": float(np.sum(prequential_nll)),
            "prequential_nll_mean": (
                None if not prequential_nll else float(np.mean(prequential_nll))
            ),
            "prequential_standardized_residual": prequential_standardized_residual,
            "prequential_coverage_3sigma_fraction": (
                None if not prequential_coverage_3sigma
                else float(np.mean(prequential_coverage_3sigma))
            ),
            "prequential_metric_computed_before_each_evaluation_state_update": True,
            "official_state_epoch_count": {str(key): value for key, value in update_count_by_state.items()},
            "official_state_information_multiplier": {str(key): value for key, value in state_multiplier.items()},
            "interpolated_200hz_qmt_rows_used_as_independent_updates": False,
            "frame_tangent_uncertainty_variance_rad2": frame_uncertainty_variance,
            "startRating": configured["startRating"],
            "stillnessRating": configured["stillnessRating"],
            "persistent_prior_before": prior_before,
            "persistent_edge_state_after": dict(state),
            "qmt_internal_span_filter_is_observation_generator_not_persistent_gauge": True,
            "external_rating_weighted_circle_filter_consumed_qmt_delta_rating_and_state": True,
            "new_edge_gauge_created_after_owner_initialization": False,
            "profile_stitched": False,
        }
        self._span_reports.append(report)
        self._span_records.setdefault((branch_id, edge, int(chronological_index)), []).append({
            "record_type": "QMT_OBSERVATION_SPAN",
            "action": action,
            "common_physical_time_s": time.copy(),
            "persistent_delta_filt_rad": posterior.copy(),
            "official_qmt_filtered_delta_rad": official_filtered.copy(),
            "official_qmt_filtered_variance_rad2": official_filtered_variance.copy(),
            "official_qmt_filtered_observation_count": (
                official_filtered_observation_count.copy()
            ),
            "raw_official_delta_filt_rad_diagnostic_only": raw.copy(),
            "posterior_variance_rad2": posterior_variance.copy(),
            "selected_source_row_indices": selected.copy(),
            "official_estimation_rating": official_rating.copy(),
            "official_estimation_state": official_state.copy(),
        })
        return HeadingSpanResult(
            corrected_child_segment_quaternion_wxyz=corrected_child,
            official_qmt_corrected_child_segment_quaternion_wxyz=np.asarray(official_quat, dtype=float),
            qmt_observation_delta_rad=raw_delta,
            persistent_delta_filt_rad=posterior,
            qmt_rating=ratings,
            qmt_state_out=qmt_states,
            posterior_variance_rad2=posterior_variance,
            report=report,
        )

    def record_action_no_update(
        self,
        *,
        branch_id: str,
        edge: str,
        chronological_index: int,
        action: str,
        base_common_physical_time_s: np.ndarray,
        cause: str,
    ) -> Mapping[str, Any]:
        """Carry an edge through an ordinary unusable episode without reset."""

        self.execution_guard.observe_qmt_span(
            edge, reset_requested=False, profile_stitch_requested=False,
        )
        if branch_id not in self._branches or edge not in EDGE_BY_NAME:
            raise ValueError("heading no-update must bind one retained branch and official edge")
        chronology = tuple(self.execution_guard.chronological_actions)
        if chronological_index < 0 or chronological_index >= len(chronology) or action != chronology[chronological_index]:
            raise ValueError("heading no-update action differs from sealed chronology")
        time = np.asarray(base_common_physical_time_s, dtype=float)
        if len(time) < 1 or not np.isfinite(time).all() or np.any(np.diff(time) <= 0.0):
            raise ValueError("heading no-update requires a finite registered physical base grid")
        state_key = (branch_id, edge)
        previous_action_index = self._last_action_index[state_key]
        if chronological_index != previous_action_index + 1:
            raise ValueError("heading no-update must fill the next exact chronological edge/action")
        state = self._edge_state[state_key]
        action_key = (branch_id, edge, int(chronological_index))
        combined_mean, combined_variance = self._combined_edge_state(
            branch_id, edge,
        )
        self._action_start_prior[action_key] = {
            "delta_rad": combined_mean,
            "variance_rad2": combined_variance,
            "reference_time_s": self._last_physical_time_s.get(state_key),
        }
        reference = self._last_physical_time_s.get(state_key, float(time[0]))
        diffusion = float(self.settings["persistent_filter"]["gap_diffusion_rad2_s"])
        official_variance = np.asarray([
            float(state["variance_rad2"])
            + max(0.0, float(value) - float(reference)) * diffusion
            for value in time
        ])
        delta = np.empty(len(time), dtype=float)
        variance = np.empty(len(time), dtype=float)
        for index, value in enumerate(official_variance):
            state["variance_rad2"] = float(value)
            delta[index], variance[index] = self._combined_edge_state(
                branch_id, edge,
            )
        state["variance_rad2"] = float(official_variance[-1])
        state["span_count"] = int(state["span_count"]) + 1
        self._last_physical_time_s[state_key] = float(time[-1])
        self._last_action_index[state_key] = int(chronological_index)
        record = {
            "record_type": "LOCAL_NO_UPDATE",
            "action": action,
            "common_physical_time_s": time.copy(),
            "persistent_delta_filt_rad": delta,
            "posterior_variance_rad2": variance,
            "official_qmt_filtered_delta_rad": np.full(
                len(time), float(state["delta_rad"]), dtype=float,
            ),
            "official_qmt_filtered_variance_rad2": official_variance.copy(),
            "official_qmt_filtered_observation_count": np.full(
                len(time), int(state["observation_count"]), dtype=np.int64,
            ),
            "selected_source_row_indices": np.empty(0, dtype=np.int64),
            "official_estimation_rating": np.empty(0, dtype=float),
            "official_estimation_state": np.empty(0, dtype=int),
            "cause": str(cause),
        }
        self._span_records.setdefault(action_key, []).append(record)
        report = {
            "schema": "biospur-c2-persistent-heading-action-no-update-v1",
            "branch_id": branch_id, "edge": edge,
            "chronological_index": int(chronological_index), "action": action,
            "cause": str(cause),
            "qmt_called": False, "vqf_or_qmt_reset": False,
            "profile_stitched": False, "samples_invented": 0,
            "base_common_physical_time_sha256": _array_sha256(time),
            "covariance_increment_rad2": float(variance[-1] - variance[0]) if len(variance) > 1 else 0.0,
            "external_nonhinge_circular_prior_active": (
                (branch_id, edge) in self._external_nonhinge_circular_state
            ),
            "external_cumulative_posterior_multiplied_per_action": False,
            "official_qmt_state_retained_separately_from_external_prior": True,
        }
        self._span_reports.append(report)
        return report

    def record_action_unknown_interval_no_update(
        self,
        *,
        branch_id: str,
        edge: str,
        chronological_index: int,
        action: str,
        cause: str,
    ) -> Mapping[str, Any]:
        """Advance chronology across an unusable/reset action without fake time.

        No display grid or elapsed duration is invented.  The persistent mean
        is carried, a registered one-shot uncertainty floor is applied, and
        the next real action may continue on its own physical clock.
        """

        self.execution_guard.observe_qmt_span(
            edge, reset_requested=False, profile_stitch_requested=False,
        )
        if branch_id not in self._branches or edge not in EDGE_BY_NAME:
            raise ValueError("heading unknown-interval no-update must bind a retained branch/edge")
        chronology = tuple(self.execution_guard.chronological_actions)
        if (
            chronological_index < 0
            or chronological_index >= len(chronology)
            or action != chronology[chronological_index]
        ):
            raise ValueError("heading unknown-interval no-update action differs from sealed chronology")
        state_key = (branch_id, edge)
        if chronological_index != self._last_action_index[state_key] + 1:
            raise ValueError("heading unknown-interval no-update must fill the next exact action")
        state = self._edge_state[state_key]
        floor = float(
            self.settings["persistent_filter"]["unknown_interval_variance_floor_rad2"]
        )
        if not np.isfinite(floor) or floor <= 0.0:
            raise ValueError("registered unknown heading interval floor must be positive")
        action_key = (branch_id, edge, int(chronological_index))
        self._action_start_prior[action_key] = {
            "delta_rad": float(state["delta_rad"]),
            "variance_rad2": float(state["variance_rad2"]),
            "reference_time_s": None,
        }
        state["variance_rad2"] = float(state["variance_rad2"]) + floor
        state["span_count"] = int(state["span_count"]) + 1
        self._last_action_index[state_key] = int(chronological_index)
        self._last_physical_time_s.pop(state_key, None)
        record = {
            "record_type": "UNKNOWN_INTERVAL_LOCAL_NO_UPDATE",
            "action": action,
            "common_physical_time_s": np.empty(0, dtype=float),
            "persistent_delta_filt_rad": np.empty(0, dtype=float),
            "posterior_variance_rad2": np.empty(0, dtype=float),
            "selected_source_row_indices": np.empty(0, dtype=np.int64),
            "official_estimation_rating": np.empty(0, dtype=float),
            "official_estimation_state": np.empty(0, dtype=int),
            "cause": str(cause),
        }
        self._span_records.setdefault(action_key, []).append(record)
        report = {
            "schema": "biospur-c2-persistent-heading-unknown-interval-no-update-v1",
            "branch_id": branch_id,
            "edge": edge,
            "chronological_index": int(chronological_index),
            "action": action,
            "cause": str(cause),
            "qmt_called": False,
            "vqf_or_qmt_reset": False,
            "profile_stitched": False,
            "samples_invented": 0,
            "exact_elapsed_seconds_fabricated": False,
            "physical_display_grid_emitted": False,
            "unknown_interval_variance_floor_rad2": floor,
        }
        self._span_reports.append(report)
        return report

    def action_branch_log_evidence(
        self,
        *,
        branch_id: str,
        chronological_index: int,
    ) -> Mapping[str, Any]:
        """Derive registered branch evidence only from official regular-state ratings."""

        scale = float(self.settings["branch_evidence"]["rating_log1p_scale"])
        cap = int(self.settings["branch_evidence"]["effective_epoch_cap_per_edge"])
        edge_rows: dict[str, Any] = {}
        total = 0.0
        for edge in EDGE_BY_NAME:
            records = self._span_records.get((branch_id, edge, int(chronological_index)), [])
            ratings = np.concatenate([
                np.asarray(record["official_estimation_rating"], dtype=float)
                for record in records
            ]) if records else np.empty(0)
            states = np.concatenate([
                np.asarray(record["official_estimation_state"], dtype=int)
                for record in records
            ]) if records else np.empty(0, dtype=int)
            eligible = ratings[states == 1]
            contribution = float(
                np.mean(np.log1p(scale * eligible)) * min(len(eligible), cap) / max(cap, 1)
            ) if len(eligible) else 0.0
            total += contribution
            edge_rows[edge] = {
                "regular_official_epoch_count": int(len(eligible)),
                "contribution": contribution,
                "interpolated_rows_counted": 0,
                "startup_or_stillness_epochs_counted": 0,
            }
        return {
            "schema": "biospur-c2-official-qmt-branch-evidence-v1",
            "branch_id": branch_id,
            "chronological_index": int(chronological_index),
            "log_evidence": total,
            "formula": "SUM_EDGE_MEAN_LOG1P(SCALE*REGULAR_OFFICIAL_RATING)*MIN(EFFECTIVE_EPOCHS,CAP)/CAP",
            "edge_evidence": edge_rows,
            "caller_supplied": False,
        }

    def assemble_action_rooted_trajectory(
        self,
        branch_id: str,
        *,
        chronological_index: int,
        action: str,
        base_common_physical_time_s: np.ndarray | None = None,
        pelvis_global_delta_rad: np.ndarray | None = None,
        mode: str = "TIME_VARYING_PARENT_PLUS_CHILD_DELTAFILT",
    ) -> HeadingTrajectoryResult:
        if branch_id not in self._branches:
            raise ValueError("rooted heading trajectory requires one retained branch")
        chronology = tuple(self.execution_guard.chronological_actions)
        if chronological_index < 0 or chronological_index >= len(chronology) or action != chronology[chronological_index]:
            raise ValueError("rooted heading trajectory action differs from sealed chronology")
        records_by_edge = {
            edge: self._span_records.get((branch_id, edge, chronological_index), [])
            for edge in EDGE_BY_NAME
        }
        if any(not records for records in records_by_edge.values()):
            raise ValueError("each edge must record either QMT observation spans or an explicit local no-update")
        time_parts = [
            np.asarray(record["common_physical_time_s"], dtype=float)
            for records in records_by_edge.values() for record in records
        ]
        if base_common_physical_time_s is not None:
            base = np.asarray(base_common_physical_time_s, dtype=float)
            if not np.isfinite(base).all() or np.any(np.diff(base) <= 0.0):
                raise ValueError("base common physical time must be finite and strictly increasing")
            common_time = base.copy()
            common_grid_source = "SEALED_REGISTERED_BASE_PHYSICAL_TIME_GRID"
        else:
            common_time = np.unique(np.concatenate(time_parts))
            common_grid_source = "UNION_OF_PRESERVED_EDGE_SPAN_TIMES_DIAGNOSTIC_ONLY"
        if not np.isfinite(common_time).all() or np.any(np.diff(common_time) <= 0.0):
            raise RuntimeError("assembled common physical timestamp grid is invalid")
        diffusion = float(self.settings["persistent_filter"]["gap_diffusion_rad2_s"])
        edge_delta: dict[str, np.ndarray] = {}
        edge_variance: dict[str, np.ndarray] = {}
        edge_observed: dict[str, np.ndarray] = {}
        assembly_audit: dict[str, Any] = {}
        for edge, records in records_by_edge.items():
            records = sorted(records, key=lambda row: float(row["common_physical_time_s"][0]))
            for first, second in zip(records[:-1], records[1:]):
                if float(second["common_physical_time_s"][0]) <= float(first["common_physical_time_s"][-1]):
                    raise RuntimeError(f"{edge}: preserved heading spans overlap")
            action_prior = self._action_start_prior[(branch_id, edge, int(chronological_index))]
            mean = np.full(len(common_time), float(action_prior["delta_rad"]))
            variance = np.full(len(common_time), float(action_prior["variance_rad2"]))
            observed = np.zeros(len(common_time), dtype=bool)
            for index, current_time in enumerate(common_time):
                containing = [
                    record for record in records
                    if float(record["common_physical_time_s"][0]) <= current_time <= float(record["common_physical_time_s"][-1])
                ]
                if containing:
                    record = containing[0]
                    record_time = np.asarray(record["common_physical_time_s"], dtype=float)
                    mean[index] = float(np.interp(current_time, record_time, record["persistent_delta_filt_rad"]))
                    variance[index] = float(np.interp(current_time, record_time, record["posterior_variance_rad2"]))
                    observed[index] = bool(
                        record["record_type"] == "QMT_OBSERVATION_SPAN" and np.any(record_time == current_time)
                    )
                else:
                    preceding = [
                        record for record in records
                        if float(record["common_physical_time_s"][-1]) < float(current_time)
                    ]
                    if preceding:
                        prior_record = preceding[-1]
                        reference_time = float(prior_record["common_physical_time_s"][-1])
                        mean[index] = float(prior_record["persistent_delta_filt_rad"][-1])
                        variance[index] = float(prior_record["posterior_variance_rad2"][-1]) + (
                            float(current_time) - reference_time
                        ) * diffusion
                    else:
                        reference_time_value = action_prior["reference_time_s"]
                        reference_time = (
                            float(common_time[0]) if reference_time_value is None else float(reference_time_value)
                        )
                        mean[index] = float(action_prior["delta_rad"])
                        variance[index] = float(action_prior["variance_rad2"]) + max(
                            0.0, float(current_time) - reference_time,
                        ) * diffusion
            edge_delta[edge] = mean
            edge_variance[edge] = variance
            edge_observed[edge] = observed
            assembly_audit[edge] = {
                "span_count": len(records),
                "span_time_sha256": [_array_sha256(record["common_physical_time_s"]) for record in records],
                "direct_common_grid_rows": int(np.count_nonzero(observed)),
                "no_update_common_grid_rows": int(np.count_nonzero(~observed)),
                "gap_no_update_covariance_growth": True,
                "source_span_rows_discarded": 0,
                "leading_gap_initialized_from_action_start_carried_prior_not_future_span": True,
                "display_interpolation_counted_as_new_evidence": False,
            }
        count = len(common_time)
        root = np.zeros(count, dtype=float) if pelvis_global_delta_rad is None else np.asarray(pelvis_global_delta_rad, dtype=float)
        if root.shape != (count,):
            raise ValueError("pelvis heading gauge trace shape mismatch")
        global_delta = {"pelvis": root.copy()}
        global_variance = {"pelvis": np.zeros(count, dtype=float)}
        for parent, child in ROOTED_EDGES:
            edge = EDGE_NAME_BY_ENDPOINTS[(parent, child)]
            global_delta[child] = self.execution_guard.propagate_heading(
                global_delta[parent], edge_delta[edge], mode=mode,
            )
            global_variance[child] = global_variance[parent] + edge_variance[edge]
        if set(global_delta) != {name for edge in ROOTED_EDGES for name in edge}:
            raise RuntimeError("rooted heading propagation omitted a segment")
        unobserved_nonhinge_support = self.factorized_unobserved_nonhinge_support(
            branch_id
        )
        return HeadingTrajectoryResult(
            action=action,
            chronological_index=int(chronological_index),
            common_physical_time_s=common_time,
            edge_delta_filt_rad=edge_delta,
            edge_variance_rad2=edge_variance,
            edge_direct_observation_mask=edge_observed,
            segment_global_delta_rad=global_delta,
            segment_global_variance_rad2=global_variance,
            report={
                "schema": "biospur-c2-action-common-grid-rooted-heading-trajectory-v1",
                "branch_id": branch_id,
                "action": action,
                "chronological_index": int(chronological_index),
                "common_physical_time_sha256": _array_sha256(common_time),
                "common_physical_time_rows": len(common_time),
                "common_grid_source": common_grid_source,
                "all_source_span_rows_preserved_as_evidence_even_when_not_display_grid_rows": True,
                "edge_assembly": assembly_audit,
                "tree_semantics": "child_global = parent_global + time_varying_edge_deltaFilt",
                "global_variance_semantics": "CONSERVATIVE_PARENT_PLUS_EDGE_MARGINAL_VARIANCE",
                "unobserved_nonhinge_heading_edges": sorted(
                    unobserved_nonhinge_support
                ),
                "factorized_unobserved_nonhinge_heading_support": (
                    unobserved_nonhinge_support
                ),
                "unobserved_nonhinge_support_type": (
                    "FACTORIZED_FULL_S1_EIGHT_POINT_QUADRATURE"
                ),
                "unobserved_nonhinge_support_combined_as_8_POWER_5_pose_grid": False,
                "carried_zero_heading_coordinate_is_heading_evidence": False,
                "complete_data_supported_nonhinge_heading": not bool(
                    unobserved_nonhinge_support
                ),
                "mean_rooted_trajectory_full_body_physical_gate_eligible": not bool(
                    unobserved_nonhinge_support
                ),
            },
        )

    def audit(self) -> dict[str, Any]:
        return {
            "schema": "biospur-c2-persistent-heading-owner-audit-v2",
            "initialized_relative_edge_states": len(self._edge_state),
            "expected_relative_edge_states": len(self._branches) * len(EDGE_BY_NAME),
            "edge_state": {f"{branch}:{edge}": dict(value) for (branch, edge), value in self._edge_state.items()},
            "factorized_unobserved_nonhinge_heading_support": {
                branch_id: self.factorized_unobserved_nonhinge_support(branch_id)
                for branch_id in self._branches
            },
            "span_reports": list(self._span_reports),
            "external_nonhinge_circular_prior_records": deepcopy(
                self._external_nonhinge_circular_prior_records
            ),
            "external_nonhinge_circular_state": deepcopy(
                self._external_nonhinge_circular_state
            ),
            "per_action_profile_created": False,
            "rooted_nine_edge_propagation_owner": True,
            "tree_semantics": "child_global = parent_global + time_varying_edge_deltaFilt",
            "bespoke_global_solver": False,
        }

    def frozen_evaluation_state(self) -> Mapping[str, Any]:
        """Export only the final branch/edge circle priors, never live owners."""

        return {
            "schema": "biospur-c2-frozen-heading-evaluation-prior-v2",
            "branch_ids": list(self._branches),
            "edge_ids": list(EDGE_BY_NAME),
            "edge_state": [
                {"branch_id": branch, "edge": edge, **dict(value)}
                for (branch, edge), value in sorted(self._edge_state.items())
            ],
            "factorized_unobserved_nonhinge_heading_support": {
                branch_id: self.factorized_unobserved_nonhinge_support(branch_id)
                for branch_id in self._branches
            },
            "carried_zero_heading_coordinate_is_point_identified": False,
            "frame_update_coordinate_policy": (
                "UNCHANGED_FRAMES_OR_UNPROCESSED_AFFECTED_EDGES_ONLY;"
                "PROCESSED_EDGE_FRAME_CHANGE_REQUIRES_COHERENT_REPLAY_OR_"
                "EXPLICIT_STATE_COORDINATE_MIGRATION"
            ),
            "span_records_exported": False,
            "mutable_owner_reference_exported": False,
        }

    def checkpoint(self) -> Mapping[str, Any]:
        return {
            "branches": deepcopy(self._branches),
            "edge_state": deepcopy(self._edge_state),
            "span_reports": deepcopy(self._span_reports),
            "last_physical_time_s": deepcopy(self._last_physical_time_s),
            "last_action_index": deepcopy(self._last_action_index),
            "span_records": deepcopy(self._span_records),
            "action_start_prior": deepcopy(self._action_start_prior),
            "external_nonhinge_circular_prior_records": deepcopy(
                self._external_nonhinge_circular_prior_records
            ),
            "external_nonhinge_circular_state": deepcopy(
                self._external_nonhinge_circular_state
            ),
            "external_nonhinge_prefix_edge_identity": deepcopy(
                self._external_nonhinge_prefix_edge_identity
            ),
        }

    def restore(self, checkpoint: Mapping[str, Any]) -> None:
        self._branches = deepcopy(checkpoint["branches"])
        self._edge_state = deepcopy(checkpoint["edge_state"])
        self._span_reports = deepcopy(checkpoint["span_reports"])
        self._last_physical_time_s = deepcopy(checkpoint["last_physical_time_s"])
        self._last_action_index = deepcopy(checkpoint["last_action_index"])
        self._span_records = deepcopy(checkpoint["span_records"])
        self._action_start_prior = deepcopy(checkpoint["action_start_prior"])
        self._external_nonhinge_circular_prior_records = deepcopy(
            checkpoint["external_nonhinge_circular_prior_records"]
        )
        self._external_nonhinge_circular_state = deepcopy(
            checkpoint["external_nonhinge_circular_state"]
        )
        self._external_nonhinge_prefix_edge_identity = deepcopy(
            checkpoint["external_nonhinge_prefix_edge_identity"]
        )
