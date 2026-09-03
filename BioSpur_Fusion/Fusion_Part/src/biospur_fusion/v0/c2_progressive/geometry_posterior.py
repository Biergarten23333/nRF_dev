"""Causal manifold posterior for pair-local functional geometry."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Mapping

import numpy as np

from .functional_geometry import AxisEstimate, CenterEstimate, _tangent_basis


class AntipodalS2LogError(ValueError):
    """A unique tangent innovation does not exist at the S2 antipode."""


@dataclass
class _CenterState:
    mean: np.ndarray
    measurement_covariance_m2: np.ndarray
    migration_covariance_m2: np.ndarray
    systematic_covariance_m2: np.ndarray
    systematic_component_covariances_m2: dict[str, np.ndarray]
    parent: str
    child: str
    reference_time_s: float
    update_count: int = 0


@dataclass
class _AxisState:
    parent: np.ndarray
    child: np.ndarray
    parent_basis: np.ndarray
    child_basis: np.ndarray
    measurement_covariance_rad2: np.ndarray
    migration_covariance_rad2: np.ndarray
    systematic_covariance_rad2: np.ndarray
    systematic_component_covariances_rad2: dict[str, np.ndarray]
    gauge_parent: np.ndarray
    gauge_child: np.ndarray
    gauge_parent_basis: np.ndarray
    gauge_child_basis: np.ndarray
    reference_time_s: float
    update_count: int = 1


def _axis_systematic_component_union(
    existing: Mapping[str, np.ndarray],
    observed: Mapping[str, np.ndarray],
) -> tuple[dict[str, np.ndarray], dict[str, np.ndarray]]:
    """Zero-fill a causal component union without erasing old uncertainty."""

    names = sorted(set(existing) | set(observed))
    zeros = np.zeros((4, 4), dtype=float)
    return (
        {
            name: np.asarray(existing.get(name, zeros), dtype=float).copy()
            for name in names
        },
        {
            name: np.asarray(observed.get(name, zeros), dtype=float).copy()
            for name in names
        },
    )


def _sym_psd(value: np.ndarray, *, label: str) -> np.ndarray:
    matrix = 0.5 * (np.asarray(value, dtype=float) + np.asarray(value, dtype=float).T)
    eigenvalues, eigenvectors = np.linalg.eigh(matrix)
    if float(np.min(eigenvalues)) < -1e-9:
        raise ValueError(f"{label} is not positive semidefinite")
    return (eigenvectors * np.maximum(eigenvalues, 0.0)) @ eigenvectors.T


def _psd_upper_envelope(
    current: np.ndarray,
    candidate: np.ndarray,
    *,
    label: str,
) -> np.ndarray:
    """Return a PSD matrix that Loewner-dominates both inputs."""

    current_psd = _sym_psd(current, label=f"{label} current")
    candidate_psd = _sym_psd(candidate, label=f"{label} candidate")
    difference = 0.5 * (
        candidate_psd - current_psd + (candidate_psd - current_psd).T
    )
    values, vectors = np.linalg.eigh(difference)
    envelope = current_psd + (
        vectors * np.maximum(values, 0.0)
    ) @ vectors.T
    envelope = _sym_psd(envelope, label=f"{label} envelope")
    if (
        float(np.min(np.linalg.eigvalsh(envelope - current_psd))) < -1e-8
        or float(np.min(np.linalg.eigvalsh(envelope - candidate_psd))) < -1e-8
    ):
        raise RuntimeError(f"{label} failed to form a shared PSD upper envelope")
    return envelope


def _s2_log(base: np.ndarray, basis: np.ndarray, target: np.ndarray) -> np.ndarray:
    base = np.asarray(base, dtype=float) / np.linalg.norm(base)
    target = np.asarray(target, dtype=float) / np.linalg.norm(target)
    dot = float(np.clip(base @ target, -1.0, 1.0))
    angle = float(np.arccos(dot))
    tangent = target - dot * base
    norm = float(np.linalg.norm(tangent))
    if norm <= 1e-12:
        if dot < 0.0 or angle > 1e-6:
            raise AntipodalS2LogError("S2 logarithm is undefined at the antipode; zero innovation is forbidden")
        return np.zeros(2, dtype=float)
    return angle * (np.asarray(basis, dtype=float).T @ tangent) / norm


def _s2_retract(base: np.ndarray, basis: np.ndarray, tangent: np.ndarray) -> np.ndarray:
    coordinates = np.asarray(tangent, dtype=float)
    angle = float(np.linalg.norm(coordinates))
    if angle <= 1e-12:
        return np.asarray(base, dtype=float).copy()
    direction = np.asarray(basis, dtype=float) @ (coordinates / angle)
    value = np.cos(angle) * np.asarray(base, dtype=float) + np.sin(angle) * direction
    return value / np.linalg.norm(value)


def _product_log(
    base_parent: np.ndarray,
    base_child: np.ndarray,
    parent_basis: np.ndarray,
    child_basis: np.ndarray,
    target_parent: np.ndarray,
    target_child: np.ndarray,
) -> np.ndarray:
    return np.r_[
        _s2_log(base_parent, parent_basis, target_parent),
        _s2_log(base_child, child_basis, target_child),
    ]


def _product_retract(
    parent: np.ndarray,
    child: np.ndarray,
    parent_basis: np.ndarray,
    child_basis: np.ndarray,
    tangent: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    return (
        _s2_retract(parent, parent_basis, np.asarray(tangent)[:2]),
        _s2_retract(child, child_basis, np.asarray(tangent)[2:]),
    )


def _transport_jacobian(
    source_parent: np.ndarray,
    source_child: np.ndarray,
    source_parent_basis: np.ndarray,
    source_child_basis: np.ndarray,
    target_parent: np.ndarray,
    target_child: np.ndarray,
    target_parent_basis: np.ndarray,
    target_child_basis: np.ndarray,
    *,
    step_rad: float,
) -> np.ndarray:
    """Map a product-S2 tangent perturbation between two explicit charts."""

    jacobian = np.zeros((4, 4), dtype=float)
    for index in range(4):
        delta = np.zeros(4, dtype=float)
        delta[index] = step_rad
        positive = _product_retract(
            source_parent, source_child, source_parent_basis, source_child_basis, delta,
        )
        negative = _product_retract(
            source_parent, source_child, source_parent_basis, source_child_basis, -delta,
        )
        positive_log = _product_log(
            target_parent, target_child, target_parent_basis, target_child_basis,
            positive[0], positive[1],
        )
        negative_log = _product_log(
            target_parent, target_child, target_parent_basis, target_child_basis,
            negative[0], negative[1],
        )
        jacobian[:, index] = (positive_log - negative_log) / (2.0 * step_rad)
    return jacobian


class ProgressiveFunctionalGeometryOwner:
    """Fuse current-episode observations without shrinking shared model floors."""

    def __init__(self, settings: Mapping[str, Any]) -> None:
        self.settings = settings
        self._center_state: dict[str, _CenterState] = {}
        self._axis_state: dict[str, _AxisState] = {}
        self._events: list[dict[str, Any]] = []

    def _elapsed(self, previous: float, current: float) -> float:
        elapsed = float(current) - float(previous)
        if elapsed < 0.0:
            raise ValueError("progressive geometry reference time moved backward")
        return elapsed

    def apply_unknown_interval_floor(
        self,
        *,
        chronological_index: int,
        action: str,
        cause: str,
    ) -> Mapping[str, Any]:
        """Diffuse existing state without inventing an elapsed duration.

        A timer reset, boot transition, or entirely unobserved boundary makes
        elapsed seconds unidentified.  The registered migration floors are
        applied once to every existing edge, while the internal reference
        coordinate remains unchanged.  New edges have no prior state to
        diffuse and therefore receive no fabricated history.
        """

        center_increment = float(self.settings["center_unknown_interval_floor_m2"])
        axis_increment = float(self.settings["axis_unknown_interval_floor_rad2"])
        if center_increment <= 0.0 or axis_increment <= 0.0:
            raise ValueError("unknown-interval geometry floors must be positive")
        for state in self._center_state.values():
            state.migration_covariance_m2 = _sym_psd(
                state.migration_covariance_m2 + np.eye(6) * center_increment,
                label="center unknown-interval migration covariance",
            )
        for state in self._axis_state.values():
            state.migration_covariance_rad2 = _sym_psd(
                state.migration_covariance_rad2 + np.eye(4) * axis_increment,
                label="axis unknown-interval migration covariance",
            )
        event = {
            "kind": "UNKNOWN_INTERVAL_CONSERVATIVE_MIGRATION_FLOOR",
            "chronological_index": int(chronological_index),
            "action": str(action),
            "cause": str(cause),
            "center_edges_diffused": sorted(self._center_state),
            "axis_edges_diffused": sorted(self._axis_state),
            "center_increment_per_coordinate_m2": center_increment,
            "axis_increment_per_coordinate_rad2": axis_increment,
            "exact_elapsed_seconds_fabricated": False,
            "capture_terminated": False,
        }
        self._events.append(event)
        return event

    def advance_to_reference(
        self,
        *,
        chronological_index: int,
        action: str,
        reference_time_s: float,
    ) -> Mapping[str, Any]:
        """Advance every existing edge before the next prequential score."""

        center_increments: dict[str, float] = {}
        axis_increments: dict[str, float] = {}
        for edge, state in self._center_state.items():
            elapsed = self._elapsed(state.reference_time_s, reference_time_s)
            increment = elapsed * float(self.settings["center_temporal_diffusion_m2_s"])
            state.migration_covariance_m2 = _sym_psd(
                state.migration_covariance_m2 + np.eye(6) * increment,
                label="prequential center migration covariance",
            )
            state.reference_time_s = float(reference_time_s)
            center_increments[edge] = increment
        for edge, state in self._axis_state.items():
            elapsed = self._elapsed(state.reference_time_s, reference_time_s)
            increment = elapsed * float(self.settings["axis_temporal_diffusion_rad2_s"])
            state.migration_covariance_rad2 = _sym_psd(
                state.migration_covariance_rad2 + np.eye(4) * increment,
                label="prequential axis migration covariance",
            )
            state.reference_time_s = float(reference_time_s)
            axis_increments[edge] = increment
        event = {
            "kind": "PREQUENTIAL_REFERENCE_ADVANCE_BEFORE_CURRENT_FACTORS",
            "chronological_index": int(chronological_index),
            "action": str(action),
            "reference_time_s": float(reference_time_s),
            "center_increment_per_coordinate_m2": center_increments,
            "axis_increment_per_coordinate_rad2": axis_increments,
            "current_episode_factor_consumed": False,
            "data_information_added": False,
        }
        self._events.append(event)
        return event

    def ingest_center(
        self,
        estimate: CenterEstimate,
        *,
        chronological_index: int,
        action: str,
        reference_time_s: float,
    ) -> CenterEstimate | None:
        value = np.r_[estimate.joint_to_parent_sensor_m, estimate.joint_to_child_sensor_m]
        total = np.asarray(estimate.covariance_m2, dtype=float)
        sandwich = np.asarray(estimate.report["sandwich_covariance_m2"], dtype=float)
        informed_observation = np.asarray(
            estimate.report.get("informed_observation_covariance_m2", sandwich), dtype=float,
        )
        statistical = np.asarray(
            estimate.report["statistical_covariance_including_nullspace_prior_m2"], dtype=float,
        )
        floor_sigma = float(estimate.report["human_worn_model_floor_m"])
        systematic = _sym_psd(
            np.asarray(estimate.report["total_systematic_covariance_m2"], dtype=float),
            label="center total shared systematic covariance",
        )
        systematic_components = {
            "accelerometer_bias_drift": _sym_psd(
                np.asarray(
                    estimate.report[
                        "accelerometer_bias_drift_systematic_covariance_m2"
                    ], dtype=float,
                ),
                label="center accelerometer bias/drift shared systematic covariance",
            ),
            "accelerometer_scale_cross_axis": _sym_psd(
                np.asarray(
                    estimate.report[
                        "accelerometer_scale_cross_axis_systematic_covariance_m2"
                    ], dtype=float,
                ),
                label="center accelerometer scale/cross-axis shared systematic covariance",
            ),
            "gyro_bias": _sym_psd(
                np.asarray(
                    estimate.report["gyro_bias_systematic_covariance_m2"], dtype=float,
                ),
                label="center gyro-bias shared systematic covariance",
            ),
            "gyro_bias_drift": _sym_psd(
                np.asarray(
                    estimate.report[
                        "gyro_bias_drift_systematic_covariance_m2"
                    ], dtype=float,
                ),
                label="center gyro-bias-drift shared systematic covariance",
            ),
            "gyro_scale_cross_axis": _sym_psd(
                np.asarray(
                    estimate.report["gyro_scale_cross_axis_systematic_covariance_m2"],
                    dtype=float,
                ),
                label="center gyro scale/cross-axis shared systematic covariance",
            ),
            "accelerometer_gyro_shared_scale_cross_axis": _sym_psd(
                np.asarray(
                    estimate.report[
                        "accelerometer_gyro_shared_scale_cross_axis_systematic_covariance_m2"
                    ], dtype=float,
                ),
                label="center accelerometer-gyro shared scale/cross-axis systematic covariance",
            ),
            "persistent_pair_clock": _sym_psd(
                np.asarray(
                    estimate.report["persistent_clock_systematic_covariance_m2"],
                    dtype=float,
                ),
                label="center persistent-clock shared systematic covariance",
            ),
            "human_worn": _sym_psd(
                np.asarray(
                    estimate.report["human_worn_systematic_covariance_m2"], dtype=float,
                ),
                label="center human-worn shared systematic covariance",
            ),
        }
        if (
            value.shape != (6,)
            or total.shape != (6, 6)
            or statistical.shape != (6, 6)
            or sandwich.shape != (6, 6)
            or informed_observation.shape != (6, 6)
            or systematic.shape != (6, 6)
            or any(component.shape != (6, 6) for component in systematic_components.values())
        ):
            raise ValueError("causal center observation must remain six-dimensional")
        if not np.allclose(total, statistical + systematic, atol=1e-10, rtol=1e-7):
            raise ValueError("center estimator did not separate statistical and total shared systematic covariance")
        component_sum = sum(
            systematic_components.values(), start=np.zeros((6, 6), dtype=float),
        )
        if not np.allclose(systematic, component_sum, atol=1e-10, rtol=1e-7):
            raise ValueError("center estimator systematic decomposition does not sum to total")
        if not np.allclose(
            systematic_components["human_worn"],
            np.eye(6) * floor_sigma**2,
            atol=1e-12,
            rtol=1e-9,
        ):
            raise ValueError("center human-worn component differs from its reported floor")
        if not bool(estimate.report["owner_update_eligible"]):
            self.no_update(
                edge=estimate.edge, kind="CENTER", chronological_index=chronological_index,
                action=action, reference_time_s=reference_time_s,
                cause=str(estimate.report["owner_update_mode"]),
            )
            return None
        informed_basis = np.asarray(
            estimate.report["gauge_reduced_robust_bread_informed_basis"], dtype=float,
        )
        if informed_basis.ndim != 2 or informed_basis.shape[0] != 6 or informed_basis.shape[1] == 0:
            raise ValueError("eligible center update requires a nonempty robust-bread informed basis")
        if not np.allclose(informed_basis.T @ informed_basis, np.eye(informed_basis.shape[1]), atol=1e-8):
            raise ValueError("center informed basis must be orthonormal")
        observation_covariance = _sym_psd(
            informed_basis.T @ informed_observation @ informed_basis,
            label="center informed-subspace observation covariance",
        )
        if float(np.min(np.linalg.eigvalsh(observation_covariance))) <= 0.0:
            observation_covariance += np.eye(informed_basis.shape[1]) * float(
                self.settings["center_minimum_informed_variance_m2"]
            )
        if estimate.edge not in self._center_state:
            state = _CenterState(
                mean=np.zeros(6, dtype=float),
                measurement_covariance_m2=np.eye(6) * float(
                    self.settings["center_initial_unobserved_sigma_m"]
                ) ** 2,
                migration_covariance_m2=np.zeros((6, 6), dtype=float),
                systematic_covariance_m2=systematic,
                systematic_component_covariances_m2={
                    name: component.copy()
                    for name, component in systematic_components.items()
                },
                parent=estimate.parent, child=estimate.child,
                reference_time_s=float(reference_time_s),
            )
            self._center_state[estimate.edge] = state
        else:
            state = self._center_state[estimate.edge]
            if (state.parent, state.child) != (estimate.parent, estimate.child):
                raise ValueError("persistent center edge endpoints changed")
        elapsed = self._elapsed(state.reference_time_s, reference_time_s)
        migration_predicted = state.migration_covariance_m2 + np.eye(6) * (
            elapsed * float(self.settings["center_temporal_diffusion_m2_s"])
        )
        predicted = state.measurement_covariance_m2 + migration_predicted
        innovation = informed_basis.T @ predicted @ informed_basis + observation_covariance
        gain = predicted @ informed_basis @ np.linalg.pinv(innovation)
        state.mean = state.mean + gain @ (informed_basis.T @ (value - state.mean))
        identity = np.eye(6)
        attenuation = identity - gain @ informed_basis.T
        state.measurement_covariance_m2 = _sym_psd(
            attenuation @ state.measurement_covariance_m2 @ attenuation.T
            + gain @ observation_covariance @ gain.T,
            label="center posterior measurement covariance",
        )
        state.migration_covariance_m2 = _sym_psd(
            attenuation @ migration_predicted @ attenuation.T,
            label="center posterior temporal-migration covariance",
        )
        # Every component is shared/systematic.  A PSD upper envelope carries
        # it across episodes without adding it to repeatable data information.
        state.systematic_component_covariances_m2 = {
            name: _psd_upper_envelope(
                state.systematic_component_covariances_m2[name],
                systematic_components[name],
                label=f"center {name} shared systematic covariance",
            )
            for name in systematic_components
        }
        state.systematic_covariance_m2 = _sym_psd(
            sum(
                state.systematic_component_covariances_m2.values(),
                start=np.zeros((6, 6), dtype=float),
            ),
            label="center persistent total shared systematic covariance",
        )
        state.reference_time_s = float(reference_time_s)
        state.update_count += 1
        self._events.append({
            "kind": "CENTER_UPDATE", "edge": estimate.edge,
            "chronological_index": int(chronological_index), "action": action,
            "reference_time_s": float(reference_time_s),
            "shared_model_floor_added_as_independent_information": False,
            "shared_accelerometer_gyro_bias_drift_scale_cross_axis_clock_or_human_covariance_added_as_independent_information": False,
            "systematic_component_ownership": sorted(systematic_components),
            "informed_subspace_rank": int(informed_basis.shape[1]),
            "nullspace_updated": False,
            "update_count": state.update_count,
        })
        return self._center_estimate(estimate.edge, chronological_index, estimate.report)

    def _center_estimate(
        self,
        edge: str,
        chronological_index: int | None = None,
        latest_report: Mapping[str, Any] | None = None,
    ) -> CenterEstimate:
        state = self._center_state[edge]
        statistical = state.measurement_covariance_m2 + state.migration_covariance_m2
        total = statistical + state.systematic_covariance_m2
        return CenterEstimate(
            edge=edge, parent=state.parent, child=state.child,
            joint_to_parent_sensor_m=state.mean[:3].copy(),
            joint_to_child_sensor_m=state.mean[3:].copy(),
            covariance_m2=total,
            report={
                "schema": "biospur-c2-causal-progressive-center-posterior-v2",
                "updates_through_chronological_index": chronological_index,
                "update_count": state.update_count,
                "measurement_statistical_covariance_m2": state.measurement_covariance_m2.tolist(),
                "temporal_migration_covariance_m2": state.migration_covariance_m2.tolist(),
                "statistical_covariance_m2": statistical.tolist(),
                "systematic_shared_model_covariance_m2": state.systematic_covariance_m2.tolist(),
                "systematic_shared_component_covariances_m2": {
                    name: component.tolist()
                    for name, component in state.systematic_component_covariances_m2.items()
                },
                "total_covariance_m2": total.tolist(),
                "shared_model_floor_shrunk_by_episode_count": False,
                "shared_accelerometer_gyro_bias_drift_scale_cross_axis_clock_or_human_covariance_shrunk_by_episode_count": False,
                "temporal_diffusion_applied": True,
                "latest_local_estimate_report": None if latest_report is None else dict(latest_report),
                "future_episode_factor_count": 0,
            },
        )

    def ingest_axis(
        self,
        estimate: AxisEstimate,
        *,
        chronological_index: int,
        action: str,
        reference_time_s: float,
    ) -> AxisEstimate | None:
        observed_parent = np.asarray(estimate.parent_axis_sensor, dtype=float)
        observed_child = np.asarray(estimate.child_axis_sensor, dtype=float)
        observed_parent /= np.linalg.norm(observed_parent)
        observed_child /= np.linalg.norm(observed_child)
        statistical_local = _sym_psd(
            np.asarray(estimate.report["statistical_tangent_covariance_rad2"], dtype=float),
            label="axis local statistical covariance",
        )
        systematic_local = _sym_psd(
            np.asarray(
                estimate.report["total_systematic_tangent_covariance_rad2"],
                dtype=float,
            ),
            label="axis local total systematic covariance",
        )
        systematic_components_local = {
            str(name): _sym_psd(
                np.asarray(component, dtype=float),
                label=f"axis local {name} systematic covariance",
            )
            for name, component in estimate.report[
                "systematic_component_tangent_covariances_rad2"
            ].items()
        }
        if not systematic_components_local:
            raise ValueError("axis local systematic decomposition is empty")
        systematic_component_sum = sum(
            systematic_components_local.values(),
            start=np.zeros((4, 4), dtype=float),
        )
        if not np.allclose(
            systematic_local, systematic_component_sum, atol=1e-10, rtol=1e-7,
        ):
            raise ValueError("axis local systematic components do not sum to total")
        human_worn_local = np.asarray(
            estimate.report["systematic_human_worn_tangent_covariance_rad2"],
            dtype=float,
        )
        if not np.allclose(
            human_worn_local,
            systematic_components_local["human_worn"],
            atol=1e-10,
            rtol=1e-7,
        ):
            raise ValueError("axis human-worn component is not reported honestly")
        observed_parent_basis = np.asarray(estimate.report["parent_tangent_basis_sensor"], dtype=float)
        observed_child_basis = np.asarray(estimate.report["child_tangent_basis_sensor"], dtype=float)
        if not bool(estimate.report.get("owner_update_eligible", True)):
            self.no_update(
                edge=estimate.edge, kind="AXIS", chronological_index=chronological_index,
                action=action, reference_time_s=reference_time_s,
                cause=str(estimate.report.get("owner_update_mode", "LOCAL_NO_UPDATE_LOW_INFORMATION")),
            )
            return None
        step = float(self.settings["axis_transport_step_rad"])
        if estimate.edge not in self._axis_state:
            state = _AxisState(
                parent=observed_parent.copy(), child=observed_child.copy(),
                parent_basis=observed_parent_basis.copy(), child_basis=observed_child_basis.copy(),
                measurement_covariance_rad2=statistical_local,
                migration_covariance_rad2=np.zeros((4, 4), dtype=float),
                systematic_covariance_rad2=systematic_local,
                systematic_component_covariances_rad2={
                    name: component.copy()
                    for name, component in systematic_components_local.items()
                },
                gauge_parent=observed_parent.copy(), gauge_child=observed_child.copy(),
                gauge_parent_basis=observed_parent_basis.copy(), gauge_child_basis=observed_child_basis.copy(),
                reference_time_s=float(reference_time_s),
            )
            self._axis_state[estimate.edge] = state
        else:
            state = self._axis_state[estimate.edge]
            if float(observed_parent @ state.parent + observed_child @ state.child) < 0.0:
                observed_parent = -observed_parent
                observed_child = -observed_child
                observed_parent_basis = -observed_parent_basis
                observed_child_basis = -observed_child_basis
            elapsed = self._elapsed(state.reference_time_s, reference_time_s)
            migration_predicted = state.migration_covariance_rad2 + np.eye(4) * (
                elapsed * float(self.settings["axis_temporal_diffusion_rad2_s"])
            )
            predicted = state.measurement_covariance_rad2 + migration_predicted
            endpoint_dots = np.array([
                float(observed_parent @ state.parent), float(observed_child @ state.child),
            ])
            if np.any(endpoint_dots <= float(self.settings["axis_antipodal_cosine_threshold"])):
                state.migration_covariance_rad2 = migration_predicted + np.eye(4) * float(
                    self.settings["axis_antipodal_no_update_sigma_rad"]
                ) ** 2
                state.reference_time_s = float(reference_time_s)
                self._events.append({
                    "kind": "AXIS_ANTIPODAL_ILL_CONDITIONED_NO_UPDATE",
                    "edge": estimate.edge,
                    "chronological_index": int(chronological_index), "action": action,
                    "endpoint_dots_after_simultaneous_sign_alignment": endpoint_dots.tolist(),
                    "zero_innovation_substituted": False,
                    "covariance_inflated": True,
                    "branch_or_capture_terminated": False,
                })
                return None
            observation_to_state = _transport_jacobian(
                observed_parent, observed_child, observed_parent_basis, observed_child_basis,
                state.parent, state.child, state.parent_basis, state.child_basis,
                step_rad=step,
            )
            observation_covariance = _sym_psd(
                observation_to_state @ statistical_local @ observation_to_state.T,
                label="transported axis observation covariance",
            )
            residual = _product_log(
                state.parent, state.child, state.parent_basis, state.child_basis,
                observed_parent, observed_child,
            )
            innovation = predicted + observation_covariance
            gain = predicted @ np.linalg.pinv(innovation)
            correction = gain @ residual
            identity = np.eye(4)
            posterior_old_chart = _sym_psd(
                (identity - gain) @ predicted @ (identity - gain).T
                + gain @ observation_covariance @ gain.T,
                label="axis posterior covariance before relinearization",
            )
            new_parent, new_child = _product_retract(
                state.parent, state.child, state.parent_basis, state.child_basis, correction,
            )
            new_parent_basis = _tangent_basis(new_parent)
            new_child_basis = _tangent_basis(new_child)
            old_to_new = _transport_jacobian(
                state.parent, state.child, state.parent_basis, state.child_basis,
                new_parent, new_child, new_parent_basis, new_child_basis,
                step_rad=step,
            )
            attenuation = identity - gain
            measurement_old_chart = _sym_psd(
                attenuation @ state.measurement_covariance_rad2 @ attenuation.T
                + gain @ observation_covariance @ gain.T,
                label="axis posterior measurement covariance before relinearization",
            )
            migration_old_chart = _sym_psd(
                attenuation @ migration_predicted @ attenuation.T,
                label="axis posterior migration covariance before relinearization",
            )
            state.measurement_covariance_rad2 = _sym_psd(
                old_to_new @ measurement_old_chart @ old_to_new.T,
                label="axis measurement covariance after relinearization",
            )
            state.migration_covariance_rad2 = _sym_psd(
                old_to_new @ migration_old_chart @ old_to_new.T,
                label="axis migration covariance after relinearization",
            )
            local_to_new = old_to_new @ observation_to_state
            existing_components, observed_components = _axis_systematic_component_union(
                state.systematic_component_covariances_rad2,
                systematic_components_local,
            )
            state.systematic_component_covariances_rad2 = {
                name: _psd_upper_envelope(
                    old_to_new
                    @ existing_components[name]
                    @ old_to_new.T,
                    local_to_new
                    @ observed_components[name]
                    @ local_to_new.T,
                    label=f"axis {name} nonshrinking systematic covariance",
                )
                for name in sorted(existing_components)
            }
            state.systematic_covariance_rad2 = _sym_psd(
                sum(
                    state.systematic_component_covariances_rad2.values(),
                    start=np.zeros((4, 4), dtype=float),
                ),
                label="axis total nonshrinking systematic covariance",
            )
            state.parent = new_parent
            state.child = new_child
            state.parent_basis = new_parent_basis
            state.child_basis = new_child_basis
            state.reference_time_s = float(reference_time_s)
            state.update_count += 1
        self._events.append({
            "kind": "AXIS_MANIFOLD_UPDATE", "edge": estimate.edge,
            "chronological_index": int(chronological_index), "action": action,
            "reference_time_s": float(reference_time_s),
            "actual_local_tangent_bases_consumed": True,
            "covariance_parallel_transport_and_relinearization": True,
            "cross_endpoint_covariance_retained": True,
            "shared_axis_systematic_components_added_as_independent_information": False,
            "shared_axis_systematic_components_preserved": sorted(
                state.systematic_component_covariances_rad2
            ),
            "update_count": state.update_count,
        })
        return self._axis_estimate(estimate.edge, chronological_index, estimate.report)

    def _axis_estimate(
        self,
        edge: str,
        chronological_index: int | None = None,
        latest_report: Mapping[str, Any] | None = None,
    ) -> AxisEstimate:
        state = self._axis_state[edge]
        statistical = state.measurement_covariance_rad2 + state.migration_covariance_rad2
        total = statistical + state.systematic_covariance_rad2
        return AxisEstimate(
            edge=edge, parent_axis_sensor=state.parent.copy(), child_axis_sensor=state.child.copy(),
            tangent_covariance_rad2=total,
            report={
                "schema": "biospur-c2-causal-progressive-product-s2-axis-posterior-v2",
                "updates_through_chronological_index": chronological_index,
                "update_count": state.update_count,
                "measurement_statistical_tangent_covariance_rad2": state.measurement_covariance_rad2.tolist(),
                "temporal_migration_tangent_covariance_rad2": state.migration_covariance_rad2.tolist(),
                "statistical_tangent_covariance_rad2": statistical.tolist(),
                "total_systematic_tangent_covariance_rad2": (
                    state.systematic_covariance_rad2.tolist()
                ),
                "systematic_component_tangent_covariances_rad2": {
                    name: component.tolist()
                    for name, component in sorted(
                        state.systematic_component_covariances_rad2.items()
                    )
                },
                "systematic_human_worn_tangent_covariance_rad2": (
                    state.systematic_component_covariances_rad2["human_worn"].tolist()
                ),
                "parent_tangent_basis_sensor": state.parent_basis.tolist(),
                "child_tangent_basis_sensor": state.child_basis.tolist(),
                "shared_model_floor_shrunk_by_episode_count": False,
                "manifold_retraction_and_covariance_transport": True,
                "future_episode_factor_count": 0,
                "latest_local_estimate_report": None if latest_report is None else dict(latest_report),
            },
        )

    def no_update(
        self,
        *,
        edge: str,
        kind: str,
        chronological_index: int,
        action: str,
        reference_time_s: float,
        cause: str,
    ) -> None:
        if kind == "CENTER" and edge in self._center_state:
            state = self._center_state[edge]
            elapsed = self._elapsed(state.reference_time_s, reference_time_s)
            state.migration_covariance_m2 += np.eye(6) * (
                elapsed * float(self.settings["center_temporal_diffusion_m2_s"])
            )
            state.reference_time_s = float(reference_time_s)
            increment = elapsed * float(self.settings["center_temporal_diffusion_m2_s"])
        elif kind == "AXIS" and edge in self._axis_state:
            state = self._axis_state[edge]
            elapsed = self._elapsed(state.reference_time_s, reference_time_s)
            state.migration_covariance_rad2 += np.eye(4) * (
                elapsed * float(self.settings["axis_temporal_diffusion_rad2_s"])
            )
            state.reference_time_s = float(reference_time_s)
            increment = elapsed * float(self.settings["axis_temporal_diffusion_rad2_s"])
        elif kind in {"CENTER", "AXIS"}:
            increment = None
        else:
            raise ValueError("unknown progressive geometry no-update kind")
        self._events.append({
            "kind": f"{kind}_NO_UPDATE", "edge": edge,
            "chronological_index": int(chronological_index), "action": action,
            "reference_time_s": float(reference_time_s),
            "cause": cause, "temporal_diffusion_increment_per_coordinate": increment,
            "future_episode_factor_count": 0,
        })

    def posterior_centers(self) -> dict[str, CenterEstimate]:
        return {edge: self._center_estimate(edge) for edge in self._center_state}

    def posterior_axes(self) -> dict[str, AxisEstimate]:
        return {edge: self._axis_estimate(edge) for edge in self._axis_state}

    def axis_observation_in_fixed_gauge(
        self,
        estimate: AxisEstimate,
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray, bool]:
        """Return local observation mean/statistical/systematic covariance in its edge's immutable gauge chart."""

        state = self._axis_state[estimate.edge]
        parent = np.asarray(estimate.parent_axis_sensor, dtype=float)
        child = np.asarray(estimate.child_axis_sensor, dtype=float)
        parent /= np.linalg.norm(parent)
        child /= np.linalg.norm(child)
        parent_basis = np.asarray(estimate.report["parent_tangent_basis_sensor"], dtype=float)
        child_basis = np.asarray(estimate.report["child_tangent_basis_sensor"], dtype=float)
        if float(parent @ state.gauge_parent + child @ state.gauge_child) < 0.0:
            parent = -parent; child = -child
            parent_basis = -parent_basis; child_basis = -child_basis
        endpoint_dots = np.array([
            float(parent @ state.gauge_parent), float(child @ state.gauge_child),
        ])
        if np.any(endpoint_dots <= float(self.settings["axis_antipodal_cosine_threshold"])):
            broad = np.eye(4) * float(self.settings["axis_antipodal_no_update_sigma_rad"]) ** 2
            return np.zeros(4, dtype=float), broad, broad, False
        transport = _transport_jacobian(
            parent, child, parent_basis, child_basis,
            state.gauge_parent, state.gauge_child,
            state.gauge_parent_basis, state.gauge_child_basis,
            step_rad=float(self.settings["axis_transport_step_rad"]),
        )
        mean = _product_log(
            state.gauge_parent, state.gauge_child,
            state.gauge_parent_basis, state.gauge_child_basis,
            parent, child,
        )
        statistical = _sym_psd(
            transport @ np.asarray(estimate.report["statistical_tangent_covariance_rad2"], dtype=float) @ transport.T,
            label="fixed-gauge local axis statistical covariance",
        )
        systematic = _sym_psd(
            transport
            @ np.asarray(
                estimate.report["total_systematic_tangent_covariance_rad2"],
                dtype=float,
            )
            @ transport.T,
            label="fixed-gauge local axis systematic covariance",
        )
        return mean, statistical, systematic, True

    def posterior_axis_in_fixed_gauge(
        self,
        edge: str,
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, bool]:
        """Expose the current manifold posterior in its immutable diagnostic gauge."""

        state = self._axis_state[edge]
        parent = state.parent.copy()
        child = state.child.copy()
        parent_basis = state.parent_basis.copy()
        child_basis = state.child_basis.copy()
        if float(parent @ state.gauge_parent + child @ state.gauge_child) < 0.0:
            parent = -parent; child = -child
            parent_basis = -parent_basis; child_basis = -child_basis
        endpoint_dots = np.array([
            float(parent @ state.gauge_parent), float(child @ state.gauge_child),
        ])
        if np.any(endpoint_dots <= float(self.settings["axis_antipodal_cosine_threshold"])):
            broad = np.eye(4) * float(self.settings["axis_antipodal_no_update_sigma_rad"]) ** 2
            return np.zeros(4), broad, broad, broad, False
        transport = _transport_jacobian(
            parent, child, parent_basis, child_basis,
            state.gauge_parent, state.gauge_child,
            state.gauge_parent_basis, state.gauge_child_basis,
            step_rad=float(self.settings["axis_transport_step_rad"]),
        )
        mean = _product_log(
            state.gauge_parent, state.gauge_child,
            state.gauge_parent_basis, state.gauge_child_basis,
            parent, child,
        )
        measurement = _sym_psd(
            transport @ state.measurement_covariance_rad2 @ transport.T,
            label="fixed-gauge posterior axis measurement covariance",
        )
        migration = _sym_psd(
            transport @ state.migration_covariance_rad2 @ transport.T,
            label="fixed-gauge posterior axis migration covariance",
        )
        systematic = _sym_psd(
            transport @ state.systematic_covariance_rad2 @ transport.T,
            label="fixed-gauge posterior axis systematic covariance",
        )
        return mean, measurement, migration, systematic, True

    def audit(self) -> Mapping[str, Any]:
        return {
            "schema": "biospur-c2-causal-manifold-functional-geometry-owner-v2",
            "center_edges": sorted(self._center_state),
            "axis_edges": sorted(self._axis_state),
            "events": list(self._events),
            "batch_refits": 0,
            "future_episode_factor_reuse": False,
            "shared_model_floors_shrink_with_episode_count": False,
            "temporal_diffusion_enabled": True,
            "axis_chart": "PRODUCT_S2_RETRACTION_WITH_NUMERICAL_COVARIANCE_TRANSPORT_AND_RELINEARIZATION",
        }

    def checkpoint(self) -> Mapping[str, Any]:
        return {
            "center_state": deepcopy(self._center_state),
            "axis_state": deepcopy(self._axis_state),
            "events": deepcopy(self._events),
        }

    def restore(self, checkpoint: Mapping[str, Any]) -> None:
        self._center_state = deepcopy(checkpoint["center_state"])
        self._axis_state = deepcopy(checkpoint["axis_state"])
        self._events = deepcopy(checkpoint["events"])


def numeric_product_s2_transport_gate(*, step_rad: float) -> Mapping[str, Any]:
    """Known full-circle/non-cardinal chart transport oracle."""

    source_parent = np.array([0.31, 0.87, -0.38]); source_parent /= np.linalg.norm(source_parent)
    source_child = np.array([-0.52, 0.22, 0.825]); source_child /= np.linalg.norm(source_child)
    source_parent_basis = _tangent_basis(source_parent)
    source_child_basis = _tangent_basis(source_child)
    target_parent, target_child = _product_retract(
        source_parent, source_child, source_parent_basis, source_child_basis,
        np.array([0.7, -0.4, -0.6, 0.35]),
    )
    target_parent_basis = _tangent_basis(target_parent)
    target_child_basis = _tangent_basis(target_child)
    jacobian = _transport_jacobian(
        source_parent, source_child, source_parent_basis, source_child_basis,
        target_parent, target_child, target_parent_basis, target_child_basis,
        step_rad=step_rad,
    )
    covariance = np.array([
        [0.04, 0.01, 0.008, -0.004],
        [0.01, 0.03, 0.003, 0.006],
        [0.008, 0.003, 0.05, -0.012],
        [-0.004, 0.006, -0.012, 0.035],
    ])
    transported = _sym_psd(jacobian @ covariance @ jacobian.T, label="known transport covariance")
    round_trip = _transport_jacobian(
        target_parent, target_child, target_parent_basis, target_child_basis,
        source_parent, source_child, source_parent_basis, source_child_basis,
        step_rad=step_rad,
    )
    round_trip_error = float(np.linalg.norm(round_trip @ jacobian - np.eye(4)))
    return {
        "schema": "biospur-c2-product-s2-known-chart-transport-gate-v1",
        "transport_jacobian": jacobian.tolist(),
        "transported_covariance_rad2": transported.tolist(),
        "transported_cross_endpoint_block_norm": float(np.linalg.norm(transported[:2, 2:])),
        "minimum_transported_eigenvalue": float(np.min(np.linalg.eigvalsh(transported))),
        "local_round_trip_jacobian_error": round_trip_error,
        "pass": bool(
            np.min(np.linalg.eigvalsh(transported)) >= -1e-10
            and np.linalg.norm(transported[:2, 2:]) > 1e-6
            and round_trip_error < 5e-3
        ),
    }


def numeric_low_information_geometry_owner_gate(settings: Mapping[str, Any]) -> Mapping[str, Any]:
    """Exercise partial/rejected factor ownership without estimator-dictionary shortcuts."""

    center_floor_m = 0.03
    center_informed_basis = np.eye(6)[:, :3]
    center_sandwich = np.diag([4e-4, 5e-4, 6e-4, 0.0, 0.0, 0.0])
    center_statistical = center_sandwich + np.diag([0.0, 0.0, 0.0, 0.25, 0.25, 0.25])
    center_acc_bias_drift_systematic = np.diag(
        [3e-4, 4e-4, 2e-4, 4e-4, 3e-4, 2e-4]
    )
    center_acc_scale_systematic = np.diag(
        [2e-4, 3e-4, 1e-4, 2e-4, 1e-4, 3e-4]
    )
    center_gyro_bias_systematic = np.diag([1e-4, 2e-4, 3e-4, 1.5e-4, 2.5e-4, 3.5e-4])
    center_gyro_bias_drift_systematic = np.diag(
        [2e-5, 3e-5, 1e-5, 3e-5, 2e-5, 1e-5]
    )
    center_scale_cross_systematic = np.diag([2e-4, 1e-4, 2e-4, 1e-4, 2e-4, 1e-4])
    center_acc_gyro_shared_scale_systematic = np.diag(
        [1e-4, 2e-4, 1e-4, 2e-4, 1e-4, 2e-4]
    )
    center_clock_systematic = np.diag([5e-5, 8e-5, 6e-5, 7e-5, 4e-5, 9e-5])
    center_human_systematic = np.eye(6) * center_floor_m**2
    center_systematic = (
        center_acc_bias_drift_systematic
        + center_acc_scale_systematic
        + center_gyro_bias_systematic
        + center_gyro_bias_drift_systematic
        + center_scale_cross_systematic
        + center_acc_gyro_shared_scale_systematic
        + center_clock_systematic
        + center_human_systematic
    )

    def center_estimate(edge: str, *, eligible: bool) -> CenterEstimate:
        return CenterEstimate(
            edge=edge, parent="parent", child="child",
            joint_to_parent_sensor_m=np.array([0.1, -0.2, 0.3]),
            joint_to_child_sensor_m=np.array([-0.15, 0.25, -0.35]),
            covariance_m2=center_statistical + center_systematic,
            report={
                "sandwich_covariance_m2": center_sandwich.tolist(),
                "statistical_covariance_including_nullspace_prior_m2": center_statistical.tolist(),
                "human_worn_model_floor_m": center_floor_m,
                "accelerometer_bias_drift_systematic_covariance_m2": (
                    center_acc_bias_drift_systematic.tolist()
                ),
                "accelerometer_scale_cross_axis_systematic_covariance_m2": (
                    center_acc_scale_systematic.tolist()
                ),
                "gyro_bias_systematic_covariance_m2": center_gyro_bias_systematic.tolist(),
                "gyro_bias_drift_systematic_covariance_m2": (
                    center_gyro_bias_drift_systematic.tolist()
                ),
                "gyro_scale_cross_axis_systematic_covariance_m2": (
                    center_scale_cross_systematic.tolist()
                ),
                "accelerometer_gyro_shared_scale_cross_axis_systematic_covariance_m2": (
                    center_acc_gyro_shared_scale_systematic.tolist()
                ),
                "persistent_clock_systematic_covariance_m2": center_clock_systematic.tolist(),
                "human_worn_systematic_covariance_m2": center_human_systematic.tolist(),
                "total_systematic_covariance_m2": center_systematic.tolist(),
                "owner_update_eligible": eligible,
                "owner_update_mode": (
                    "GAUGE_REDUCED_ROBUST_BREAD_INFORMED_SUBSPACE"
                    if eligible else "LOCAL_NO_UPDATE_SOLVER_FAILURE_BOUNDARY_OR_ZERO_RANK"
                ),
                "gauge_reduced_robust_bread_informed_basis": center_informed_basis.tolist(),
            },
        )

    rejected_center_owner = ProgressiveFunctionalGeometryOwner(settings)
    rejected_center = rejected_center_owner.ingest_center(
        center_estimate("shoulder_left", eligible=False),
        chronological_index=0, action="SYNTHETIC_REJECTED_CENTER", reference_time_s=0.0,
    )
    rejected_center_state_absent = "shoulder_left" not in rejected_center_owner.posterior_centers()

    partial_center_owner = ProgressiveFunctionalGeometryOwner(settings)
    partial_center = partial_center_owner.ingest_center(
        center_estimate("knee_left", eligible=True),
        chronological_index=0, action="SYNTHETIC_PARTIAL_CENTER", reference_time_s=0.0,
    )
    assert partial_center is not None
    partial_measurement = np.asarray(
        partial_center.report["measurement_statistical_covariance_m2"], dtype=float,
    )
    null_basis = np.eye(6)[:, 3:]
    initial_null_variance = float(settings["center_initial_unobserved_sigma_m"]) ** 2
    observed_null_variances = np.linalg.eigvalsh(null_basis.T @ partial_measurement @ null_basis)
    center_nullspace_not_falsely_shrunk = bool(
        np.all(observed_null_variances >= initial_null_variance * (1.0 - 1e-9))
    )
    first_systematic_components = {
        name: np.asarray(value, dtype=float)
        for name, value in partial_center.report[
            "systematic_shared_component_covariances_m2"
        ].items()
    }
    repeated_center = partial_center_owner.ingest_center(
        center_estimate("knee_left", eligible=True),
        chronological_index=1,
        action="SYNTHETIC_REPEATED_SHARED_SYSTEMATIC_CENTER",
        reference_time_s=1.0,
    )
    assert repeated_center is not None
    repeated_systematic_components = {
        name: np.asarray(value, dtype=float)
        for name, value in repeated_center.report[
            "systematic_shared_component_covariances_m2"
        ].items()
    }
    expected_component_names = {
        "accelerometer_bias_drift",
        "accelerometer_scale_cross_axis",
        "accelerometer_gyro_shared_scale_cross_axis",
        "gyro_bias", "gyro_bias_drift", "gyro_scale_cross_axis",
        "persistent_pair_clock", "human_worn",
    }
    center_systematic_components_preserved = bool(
        set(first_systematic_components) == expected_component_names
        and set(repeated_systematic_components) == expected_component_names
        and all(
            np.allclose(
                repeated_systematic_components[name],
                first_systematic_components[name],
                atol=1e-12,
                rtol=0.0,
            )
            for name in expected_component_names
        )
    )
    repeated_total_systematic = np.asarray(
        repeated_center.report["systematic_shared_model_covariance_m2"], dtype=float,
    )
    center_systematic_total_equals_components = bool(np.allclose(
        repeated_total_systematic,
        sum(
            repeated_systematic_components.values(),
            start=np.zeros((6, 6), dtype=float),
        ),
        atol=1e-12,
        rtol=0.0,
    ))

    axis_direction = np.array([0.0, 1.0, 0.0])
    axis_basis = _tangent_basis(axis_direction)
    axis_statistical = np.eye(4) * np.deg2rad(2.0) ** 2
    axis_systematic_components = {
        "accelerometer_bias_drift": np.eye(4) * np.deg2rad(0.2) ** 2,
        "accelerometer_scale_cross_axis": np.eye(4) * np.deg2rad(0.3) ** 2,
        "accelerometer_gyro_shared_scale_cross_axis": np.eye(4) * np.deg2rad(0.4) ** 2,
        "gyro_bias": np.eye(4) * np.deg2rad(0.5) ** 2,
        "gyro_bias_drift": np.eye(4) * np.deg2rad(0.6) ** 2,
        "gyro_scale_cross_axis": np.eye(4) * np.deg2rad(0.7) ** 2,
        "persistent_pair_clock": np.eye(4) * np.deg2rad(0.8) ** 2,
        "human_worn": np.eye(4) * np.deg2rad(5.0) ** 2,
    }
    axis_systematic = sum(
        axis_systematic_components.values(), start=np.zeros((4, 4), dtype=float),
    )

    def axis_estimate(*, eligible: bool) -> AxisEstimate:
        return AxisEstimate(
            edge="knee_right",
            parent_axis_sensor=axis_direction.copy(), child_axis_sensor=axis_direction.copy(),
            tangent_covariance_rad2=axis_statistical + axis_systematic,
            report={
                "statistical_tangent_covariance_rad2": axis_statistical.tolist(),
                "total_systematic_tangent_covariance_rad2": axis_systematic.tolist(),
                "systematic_component_tangent_covariances_rad2": {
                    name: component.tolist()
                    for name, component in axis_systematic_components.items()
                },
                "systematic_human_worn_tangent_covariance_rad2": (
                    axis_systematic_components["human_worn"].tolist()
                ),
                "parent_tangent_basis_sensor": axis_basis.tolist(),
                "child_tangent_basis_sensor": axis_basis.tolist(),
                "owner_update_eligible": eligible,
                "owner_update_mode": (
                    "PRODUCT_S2_HESSIAN_INFORMED_UPDATE"
                    if eligible else "LOCAL_NO_UPDATE_LOW_EXCITATION_OR_RANK"
                ),
            },
        )

    axis_owner = ProgressiveFunctionalGeometryOwner(settings)
    rejected_axis = axis_owner.ingest_axis(
        axis_estimate(eligible=False),
        chronological_index=0, action="SYNTHETIC_ZERO_EXCITATION", reference_time_s=0.0,
    )
    rejected_axis_state_absent = "knee_right" not in axis_owner.posterior_axes()
    accepted_axis = axis_owner.ingest_axis(
        axis_estimate(eligible=True),
        chronological_index=1, action="SYNTHETIC_LATER_INFORMATIVE", reference_time_s=1.0,
    )
    later_episode_continued = accepted_axis is not None and "knee_right" in axis_owner.posterior_axes()
    assert accepted_axis is not None
    first_axis_components = {
        name: np.asarray(component, dtype=float)
        for name, component in accepted_axis.report[
            "systematic_component_tangent_covariances_rad2"
        ].items()
    }
    repeated_axis = axis_owner.ingest_axis(
        axis_estimate(eligible=True),
        chronological_index=2,
        action="SYNTHETIC_REPEATED_SHARED_SYSTEMATIC_AXIS",
        reference_time_s=2.0,
    )
    assert repeated_axis is not None
    repeated_axis_components = {
        name: np.asarray(component, dtype=float)
        for name, component in repeated_axis.report[
            "systematic_component_tangent_covariances_rad2"
        ].items()
    }
    axis_components_preserved = bool(
        set(first_axis_components) == set(repeated_axis_components)
        and all(
            float(np.min(np.linalg.eigvalsh(
                repeated_axis_components[name] - first_axis_components[name]
            ))) >= -1e-8
            for name in first_axis_components
        )
    )
    axis_component_sum_exact = bool(np.allclose(
        np.asarray(
            repeated_axis.report["total_systematic_tangent_covariance_rad2"],
            dtype=float,
        ),
        sum(
            repeated_axis_components.values(), start=np.zeros((4, 4), dtype=float),
        ),
        atol=1e-10,
        rtol=1e-7,
    ))
    return {
        "schema": "biospur-c2-low-information-geometry-owner-gate-v1",
        "coverage_class": "EXECUTED_OWNER_LEVEL",
        "rejected_rank_positive_center_returned_none": rejected_center is None,
        "rejected_rank_positive_center_state_absent": rejected_center_state_absent,
        "partial_center_informed_rank": int(center_informed_basis.shape[1]),
        "partial_center_nullspace_initial_variance_m2": initial_null_variance,
        "partial_center_nullspace_posterior_eigenvalues_m2": observed_null_variances.tolist(),
        "partial_center_nullspace_not_falsely_shrunk": center_nullspace_not_falsely_shrunk,
        "center_systematic_component_names": sorted(first_systematic_components),
        "center_systematic_components_preserved_without_episode_shrink": (
            center_systematic_components_preserved
        ),
        "center_systematic_total_equals_component_sum": (
            center_systematic_total_equals_components
        ),
        "center_shared_systematics_added_as_episode_information": False,
        "first_rejected_axis_returned_none": rejected_axis is None,
        "first_rejected_axis_state_absent": rejected_axis_state_absent,
        "later_informative_axis_episode_continued": later_episode_continued,
        "axis_systematic_component_names": sorted(first_axis_components),
        "axis_systematic_components_preserved_without_episode_shrink": (
            axis_components_preserved
        ),
        "axis_systematic_total_equals_component_sum": axis_component_sum_exact,
        "axis_shared_systematics_added_as_episode_information": False,
        "dictionary_presence_used_as_acceptance": False,
        "pass": bool(
            rejected_center is None
            and rejected_center_state_absent
            and center_nullspace_not_falsely_shrunk
            and center_systematic_components_preserved
            and center_systematic_total_equals_components
            and rejected_axis is None
            and rejected_axis_state_absent
            and later_episode_continued
            and axis_components_preserved
            and axis_component_sum_exact
        ),
    }


def numeric_product_s2_antipodal_gate(settings: Mapping[str, Any]) -> Mapping[str, Any]:
    """Prove that a one-endpoint full-circle antipode is not a zero innovation."""

    parent = np.array([0.31, 0.87, -0.38], dtype=float); parent /= np.linalg.norm(parent)
    child = np.array([-0.52, 0.22, 0.825], dtype=float); child /= np.linalg.norm(child)
    parent_basis = _tangent_basis(parent)
    child_basis = _tangent_basis(child)
    statistical = np.array([
        [0.020, 0.004, 0.003, -0.002],
        [0.004, 0.025, -0.001, 0.002],
        [0.003, -0.001, 0.018, 0.005],
        [-0.002, 0.002, 0.005, 0.030],
    ])
    systematic = np.eye(4) * np.deg2rad(5.0) ** 2

    def estimate(parent_axis: np.ndarray, child_axis: np.ndarray) -> AxisEstimate:
        return AxisEstimate(
            edge="knee_left",
            parent_axis_sensor=np.asarray(parent_axis, dtype=float),
            child_axis_sensor=np.asarray(child_axis, dtype=float),
            tangent_covariance_rad2=statistical + systematic,
            report={
                "statistical_tangent_covariance_rad2": statistical.tolist(),
                "total_systematic_tangent_covariance_rad2": systematic.tolist(),
                "systematic_component_tangent_covariances_rad2": {
                    "human_worn": systematic.tolist(),
                },
                "systematic_human_worn_tangent_covariance_rad2": systematic.tolist(),
                "parent_tangent_basis_sensor": _tangent_basis(parent_axis).tolist(),
                "child_tangent_basis_sensor": _tangent_basis(child_axis).tolist(),
            },
        )

    owner = ProgressiveFunctionalGeometryOwner(settings)
    owner.ingest_axis(
        estimate(parent, child), chronological_index=0, action="synthetic_initial", reference_time_s=0.0,
    )
    before = owner.posterior_axes()["knee_left"]
    direct_log_rejected = False
    try:
        _s2_log(parent, parent_basis, -parent)
    except AntipodalS2LogError:
        direct_log_rejected = True
    owner.ingest_axis(
        estimate(-parent, child), chronological_index=1, action="synthetic_full_circle", reference_time_s=1.0,
    )
    after = owner.posterior_axes()["knee_left"]
    _, _, _, fixed_gauge_valid = owner.axis_observation_in_fixed_gauge(estimate(-parent, child))
    before_statistical = np.asarray(before.report["statistical_tangent_covariance_rad2"], dtype=float)
    after_statistical = np.asarray(after.report["statistical_tangent_covariance_rad2"], dtype=float)
    covariance_increment = after_statistical - before_statistical
    events = owner.audit()["events"]
    antipodal_events = [row for row in events if row["kind"] == "AXIS_ANTIPODAL_ILL_CONDITIONED_NO_UPDATE"]
    return {
        "schema": "biospur-c2-product-s2-antipodal-no-update-gate-v1",
        "mutation": "AXIS_ANTIPODAL_ZERO_INNOVATION",
        "one_endpoint_exactly_antipodal_after_simultaneous_sign_alignment": True,
        "direct_antipodal_log_rejected": direct_log_rejected,
        "fixed_gauge_observation_accepted": bool(fixed_gauge_valid),
        "zero_innovation_substituted": any(bool(row["zero_innovation_substituted"]) for row in antipodal_events),
        "uncertainty_increment_minimum_eigenvalue_rad2": float(np.min(np.linalg.eigvalsh(covariance_increment))),
        "owner_continued_after_local_no_update": len(antipodal_events) == 1,
        "event": antipodal_events[0] if len(antipodal_events) == 1 else None,
        "pass": bool(
            direct_log_rejected
            and not fixed_gauge_valid
            and len(antipodal_events) == 1
            and not any(bool(row["zero_innovation_substituted"]) for row in antipodal_events)
            and float(np.min(np.linalg.eigvalsh(covariance_increment))) > 0.0
        ),
    }
