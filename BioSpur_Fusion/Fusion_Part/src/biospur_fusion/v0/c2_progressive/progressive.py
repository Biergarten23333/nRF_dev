"""Genuine chronological prequential state owner for C2 calibration."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from hashlib import sha256
import json
from typing import Any, Mapping, Sequence

import numpy as np

from .architecture_guard import C2ExecutionGuard, REQUIRED_PROGRESS_INPUTS


@dataclass(frozen=True)
class ProgressiveSnapshot:
    chronological_index: int
    action: str
    prediction_nll_before_ingest: float
    prequential_observed_rank: int
    prequential_status: str
    prequential_prior_mean: np.ndarray
    prequential_prior_covariance: np.ndarray
    prequential_prior_branch_weights: np.ndarray
    information_logdet: float
    data_information: np.ndarray
    data_information_rank: int
    data_information_nonzero_eigenvalues: np.ndarray
    data_information_pseudologdet: float | None
    uncertainty_trace: float
    branch_concentration: float
    branch_ids: tuple[str, ...]
    branch_weights: np.ndarray
    physical_validity: float
    posterior_mean: np.ndarray
    posterior_covariance: np.ndarray
    measurement_statistical_covariance: np.ndarray
    temporal_migration_covariance: np.ndarray
    shared_systematic_covariance: np.ndarray
    statistical_accumulator_mean: np.ndarray
    statistical_accumulator_covariance: np.ndarray


@dataclass(frozen=True)
class PrequentialPrediction:
    chronological_index: int
    action: str
    prior_mean: np.ndarray
    prior_covariance: np.ndarray
    prior_branch_weights: np.ndarray


class ProgressiveCalibrationState:
    """One persistent Gaussian/branch state; prefixes are snapshots, not refits."""

    def __init__(
        self,
        dimension: int,
        *,
        execution_guard: C2ExecutionGuard,
        initial_sigma: float,
        branch_count: int,
        chronological_actions: Sequence[str],
        rank_relative_tolerance: float,
        fresh_absolute_tolerance: float,
        fresh_relative_tolerance: float,
        initial_branch_weights: Sequence[float] | None = None,
        branch_ids: Sequence[str] | None = None,
        authoritative_sync_owner_id: str | None = None,
    ) -> None:
        if dimension <= 0 or initial_sigma <= 0.0 or branch_count < 2:
            raise ValueError("invalid progressive state dimensions")
        self.dimension = int(dimension)
        self.execution_guard = execution_guard
        self.initial_sigma = float(initial_sigma)
        self.chronological_actions = tuple(str(value) for value in chronological_actions)
        if len(self.chronological_actions) != 19 or len(set(self.chronological_actions)) != 19:
            raise ValueError("progressive owner requires 19 unique sealed actions")
        self.rank_relative_tolerance = float(rank_relative_tolerance)
        self.fresh_absolute_tolerance = float(fresh_absolute_tolerance)
        self.fresh_relative_tolerance = float(fresh_relative_tolerance)
        if not 0.0 < self.rank_relative_tolerance < 1.0:
            raise ValueError("progressive rank tolerance must be in (0,1)")
        self._prior_information = np.eye(dimension) / self.initial_sigma**2
        self._data_information = np.zeros((dimension, dimension), dtype=float)
        self._natural = np.zeros(dimension, dtype=float)
        if initial_branch_weights is None:
            branch_weights = np.full(branch_count, 1.0 / branch_count, dtype=float)
        else:
            branch_weights = np.asarray(initial_branch_weights, dtype=float)
            if branch_weights.shape != (branch_count,) or np.any(branch_weights < 0.0):
                raise ValueError("initial branch weights must be a nonnegative branch-count vector")
            if not np.isclose(np.sum(branch_weights), 1.0) or not np.any(branch_weights > 0.0):
                raise ValueError("initial branch weights must be normalized with nonzero support")
        self._initial_branch_weights = branch_weights.copy()
        self.branch_ids = tuple(
            str(value) for value in (
                branch_ids if branch_ids is not None
                else tuple(f"SYNTHETIC_BRANCH_{index:03d}" for index in range(branch_count))
            )
        )
        if len(self.branch_ids) != branch_count or len(set(self.branch_ids)) != branch_count:
            raise ValueError("progressive branch IDs must be unique and match branch count")
        with np.errstate(divide="ignore"):
            self._branch_log_weights = np.log(branch_weights)
        self._last_index = -1
        self._snapshots: list[ProgressiveSnapshot] = []
        self._fit_frozen = False
        self._sufficient_statistics: list[dict[str, Any]] = []
        self._pending_prediction: PrequentialPrediction | None = None
        self._authoritative_mean = np.zeros(dimension, dtype=float)
        self._authoritative_measurement_covariance = np.eye(dimension) * self.initial_sigma**2
        self._authoritative_migration_covariance = np.zeros((dimension, dimension), dtype=float)
        self._authoritative_systematic_covariance = np.zeros((dimension, dimension), dtype=float)
        self._authoritative_covariance = self._authoritative_measurement_covariance.copy()
        self._authoritative_sync_owner_id = authoritative_sync_owner_id
        self._authoritative_prior_sync_events: list[dict[str, Any]] = []

    @staticmethod
    def _array_sha256(value: np.ndarray) -> str:
        array = np.ascontiguousarray(np.asarray(value))
        header = json.dumps(
            {"dtype": str(array.dtype), "shape": list(array.shape)}, sort_keys=True,
        ).encode("utf-8")
        return sha256(header + array.tobytes()).hexdigest()

    def synchronize_authoritative_prior_from_geometry(
        self,
        *,
        chronological_index: int,
        action: str,
        authoritative_mean: np.ndarray,
        measurement_covariance: np.ndarray,
        temporal_migration_covariance: np.ndarray,
        shared_systematic_covariance: np.ndarray,
        owner_binding: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        """Advance the predictive owner state without adding data information."""

        if self._fit_frozen or self._pending_prediction is not None:
            raise RuntimeError("authoritative prior sync must precede the episode prediction")
        if chronological_index != self._last_index + 1 or action != self.chronological_actions[chronological_index]:
            raise ValueError("authoritative prior sync differs from sealed next chronology")
        values = (
            np.asarray(authoritative_mean, dtype=float),
            np.asarray(measurement_covariance, dtype=float),
            np.asarray(temporal_migration_covariance, dtype=float),
            np.asarray(shared_systematic_covariance, dtype=float),
        )
        mean, measurement, migration, systematic = values
        if mean.shape != (self.dimension,) or any(
            value.shape != (self.dimension, self.dimension) for value in values[1:]
        ):
            raise ValueError("authoritative prior sync shape mismatch")
        for label, value in zip(("measurement", "migration", "systematic"), values[1:]):
            if not np.isfinite(value).all() or not np.allclose(value, value.T, atol=1e-10):
                raise ValueError(f"authoritative prior sync {label} covariance is invalid")
            if float(np.min(np.linalg.eigvalsh(value))) < -1e-12:
                raise ValueError(f"authoritative prior sync {label} covariance is not PSD")
        if not np.isfinite(mean).all():
            raise ValueError("authoritative prior sync mean is nonfinite")
        hashes = {
            "mean": self._array_sha256(mean),
            "measurement": self._array_sha256(measurement),
            "migration": self._array_sha256(migration),
            "systematic": self._array_sha256(systematic),
        }
        token_payload = {
            "owner_id": self._authoritative_sync_owner_id,
            "chronological_index": int(chronological_index),
            "action": str(action),
            "array_sha256": hashes,
        }
        expected_token = sha256(
            json.dumps(token_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        if (
            self._authoritative_sync_owner_id is None
            or owner_binding.get("owner_id") != self._authoritative_sync_owner_id
            or owner_binding.get("array_sha256") != hashes
            or owner_binding.get("owner_token") != expected_token
        ):
            raise ValueError("authoritative prequential prior sync lacks the runtime geometry-owner token")
        before_information = self._data_information.copy()
        self._authoritative_mean = mean.copy()
        self._authoritative_measurement_covariance = measurement.copy()
        self._authoritative_migration_covariance = migration.copy()
        self._authoritative_systematic_covariance = systematic.copy()
        self._authoritative_covariance = measurement + migration + systematic
        if float(np.min(np.linalg.eigvalsh(self._authoritative_covariance))) <= 0.0:
            raise ValueError("authoritative prequential total covariance must be positive definite")
        if not np.array_equal(before_information, self._data_information):
            raise AssertionError("authoritative prior time advance altered data information")
        event = {
            "schema": "biospur-c2-authoritative-prequential-prior-sync-v1",
            **token_payload,
            "owner_token": expected_token,
            "data_information_added": False,
            "data_information_rank_before_and_after_equal": True,
            "total_uncertainty_trace": float(np.trace(self._authoritative_covariance)),
        }
        self._authoritative_prior_sync_events.append(event)
        return event

    def _posterior(self) -> tuple[np.ndarray, np.ndarray]:
        covariance = np.linalg.pinv(self._prior_information + self._data_information)
        return covariance @ self._natural, covariance

    def _information_audit(self, information: np.ndarray) -> tuple[int, np.ndarray, float | None]:
        eigenvalues = np.maximum(np.linalg.eigvalsh(np.asarray(information, dtype=float)), 0.0)
        maximum = float(np.max(eigenvalues)) if len(eigenvalues) else 0.0
        nonzero = eigenvalues[eigenvalues >= maximum * self.rank_relative_tolerance] if maximum > 0.0 else np.empty(0)
        pseudologdet = float(np.sum(np.log(nonzero))) if len(nonzero) else None
        return int(len(nonzero)), nonzero, pseudologdet

    def data_information_state(self) -> tuple[np.ndarray, int]:
        """Return a copy-only data-information state for the runtime time gate."""

        information = self._data_information.copy()
        rank, _, _ = self._information_audit(information)
        return information, rank

    def score_episode_before_ingest(
        self,
        *,
        chronological_index: int,
        action: str,
    ) -> PrequentialPrediction:
        if self._fit_frozen or self._pending_prediction is not None:
            raise RuntimeError("progressive prediction is frozen or another episode prediction is pending")
        if chronological_index != self._last_index + 1:
            raise ValueError("prequential prediction must follow sealed chronology")
        if action != self.chronological_actions[chronological_index]:
            raise ValueError("prequential prediction action differs from sealed chronology")
        maximum = float(np.max(self._branch_log_weights))
        prior_branch_weights = np.exp(self._branch_log_weights - maximum)
        prior_branch_weights /= np.sum(prior_branch_weights)
        prediction = PrequentialPrediction(
            chronological_index=int(chronological_index), action=str(action),
            prior_mean=self._authoritative_mean.copy(),
            prior_covariance=self._authoritative_covariance.copy(),
            prior_branch_weights=prior_branch_weights.copy(),
        )
        self._pending_prediction = prediction
        return prediction

    def ingest_episode(
        self,
        *,
        chronological_index: int,
        action: str,
        observation: np.ndarray,
        observation_covariance: np.ndarray,
        data_information: np.ndarray | None = None,
        observation_projection: np.ndarray | None = None,
        authoritative_posterior_mean: np.ndarray | None = None,
        authoritative_measurement_covariance: np.ndarray | None = None,
        authoritative_temporal_migration_covariance: np.ndarray | None = None,
        authoritative_shared_systematic_covariance: np.ndarray | None = None,
        branch_log_likelihood: Sequence[float],
        physical_validity: float,
        prediction_scored_before_ingest: bool = True,
        metric_inputs: Sequence[str] = tuple(sorted(REQUIRED_PROGRESS_INPUTS)),
        prequential_prediction: PrequentialPrediction | None = None,
    ) -> ProgressiveSnapshot:
        if self._fit_frozen:
            raise RuntimeError("progressive state is frozen")
        if chronological_index != self._last_index + 1:
            raise ValueError("progressive observations must be chronological")
        if chronological_index >= len(self.chronological_actions) or action != self.chronological_actions[chronological_index]:
            raise ValueError("progressive action differs from sealed chronology")
        value = np.asarray(observation, dtype=float)
        covariance = np.asarray(observation_covariance, dtype=float)
        branch_ll = np.asarray(branch_log_likelihood, dtype=float)
        if value.shape != (self.dimension,) or covariance.shape != (self.dimension, self.dimension):
            raise ValueError("progressive observation shape mismatch")
        if branch_ll.shape != self._branch_log_weights.shape:
            raise ValueError("progressive branch likelihood shape mismatch")
        if (
            not np.isfinite(value).all() or not np.isfinite(covariance).all()
            or np.isnan(branch_ll).any() or np.isposinf(branch_ll).any()
        ):
            raise ValueError("progressive input contains nonfinite values")
        if not np.allclose(covariance, covariance.T, atol=1e-10):
            raise ValueError("progressive observation covariance must be symmetric")
        if float(np.min(np.linalg.eigvalsh(covariance))) < -1e-12:
            raise ValueError("progressive observation covariance must be positive semidefinite")
        if not 0.0 <= float(physical_validity) <= 1.0:
            raise ValueError("progressive physical validity must be in [0,1]")
        if prequential_prediction is None:
            if self._pending_prediction is not None:
                raise RuntimeError("pending prequential prediction token must be consumed explicitly")
            prequential_prediction = self.score_episode_before_ingest(
                chronological_index=chronological_index,
                action=action,
            )
        if prequential_prediction is not self._pending_prediction:
            raise ValueError("prequential prediction token is not the pending owner-issued token")
        if (
            prequential_prediction.chronological_index != chronological_index
            or prequential_prediction.action != action
        ):
            raise ValueError("episode ingest differs from its frozen pre-update prediction token")
        observation_information = (
            np.linalg.pinv(covariance)
            if data_information is None else np.asarray(data_information, dtype=float)
        )
        if observation_information.shape != (self.dimension, self.dimension):
            raise ValueError("progressive data information shape mismatch")
        if not np.allclose(observation_information, observation_information.T, atol=1e-10):
            raise ValueError("progressive data information must be symmetric")
        if float(np.min(np.linalg.eigvalsh(observation_information))) < -1e-12:
            raise ValueError("progressive data information must be positive semidefinite")
        if observation_projection is None:
            information_eigenvalues, information_eigenvectors = np.linalg.eigh(observation_information)
            maximum = max(float(np.max(information_eigenvalues)), 0.0)
            keep = (
                (information_eigenvalues >= maximum * self.rank_relative_tolerance)
                & (information_eigenvalues > 0.0)
                if maximum > 0.0 else np.zeros(self.dimension, dtype=bool)
            )
            projection = information_eigenvectors[:, keep]
        else:
            projection = np.asarray(observation_projection, dtype=float)
        if projection.ndim != 2 or projection.shape[0] != self.dimension:
            raise ValueError("progressive observation projection must have shape (dimension, observed_rank)")
        if not np.isfinite(projection).all():
            raise ValueError("progressive observation projection contains nonfinite values")
        observed_rank = int(projection.shape[1])
        if observed_rank:
            if not np.allclose(projection.T @ projection, np.eye(observed_rank), atol=1e-8, rtol=1e-8):
                raise ValueError("progressive observation projection columns must be orthonormal")
            projector = projection @ projection.T
            if not np.allclose(
                observation_information,
                projector @ observation_information @ projector,
                atol=1e-9,
                rtol=1e-7,
            ):
                raise ValueError("data information has support outside the owner-issued observation projection")
            projected_information = projection.T @ observation_information @ projection
            projected_covariance = projection.T @ covariance @ projection
            if float(np.min(np.linalg.eigvalsh(projected_information))) <= 0.0:
                raise ValueError("projected data information must be positive definite")
            if float(np.min(np.linalg.eigvalsh(projected_covariance))) <= 0.0:
                raise ValueError("projected observation covariance must be positive definite")
            predictive = projection.T @ (
                prequential_prediction.prior_covariance + covariance
            ) @ projection
            residual = projection.T @ (value - prequential_prediction.prior_mean)
            sign, logdet = np.linalg.slogdet(predictive)
            if sign <= 0:
                raise ValueError("projected prequential covariance is not positive definite")
            prediction_nll = float(
                0.5 * (
                    observed_rank * np.log(2.0 * np.pi)
                    + logdet
                    + residual @ np.linalg.solve(predictive, residual)
                )
            )
            prequential_status = "OWNER_INFORMED_GAUGE_REDUCED_SUBSPACE"
        else:
            if not np.allclose(observation_information, 0.0, atol=1e-12, rtol=0.0):
                raise ValueError("rank-zero observation projection requires exactly zero data information")
            prediction_nll = 0.0
            prequential_status = "RANK_ZERO_LOCAL_NO_UPDATE_NO_GAUSSIAN_COORDINATES_SCORED"
        self.execution_guard.record_progress(
            metric_inputs, prediction_scored_before_ingest=prediction_scored_before_ingest,
        )
        self._data_information += observation_information
        self._natural += observation_information @ value
        accumulator_mean, accumulator_covariance = self._posterior()
        branch_support = np.isfinite(self._branch_log_weights)
        self._branch_log_weights += branch_ll
        maximum = float(np.max(self._branch_log_weights))
        if not np.isfinite(maximum):
            raise ValueError("progressive branch update eliminated every physical branch")
        branch_weights = np.exp(self._branch_log_weights - maximum)
        branch_weights /= np.sum(branch_weights)
        with np.errstate(divide="ignore"):
            self._branch_log_weights = np.where(branch_support, np.log(branch_weights), -np.inf)
        self.execution_guard.update_branch_weights(branch_weights, lock_requested=False)
        authoritative_values = (
            authoritative_posterior_mean,
            authoritative_measurement_covariance,
            authoritative_temporal_migration_covariance,
            authoritative_shared_systematic_covariance,
        )
        if all(item is None for item in authoritative_values):
            authoritative_mean = accumulator_mean.copy()
            measurement_covariance = accumulator_covariance.copy()
            migration_covariance = np.zeros_like(accumulator_covariance)
            systematic_covariance = np.zeros_like(accumulator_covariance)
        elif any(item is None for item in authoritative_values):
            raise ValueError("authoritative posterior mean and all three covariance components are atomic")
        else:
            authoritative_mean = np.asarray(authoritative_posterior_mean, dtype=float)
            measurement_covariance = np.asarray(authoritative_measurement_covariance, dtype=float)
            migration_covariance = np.asarray(authoritative_temporal_migration_covariance, dtype=float)
            systematic_covariance = np.asarray(authoritative_shared_systematic_covariance, dtype=float)
        if authoritative_mean.shape != (self.dimension,):
            raise ValueError("authoritative posterior mean shape mismatch")
        for label, component in (
            ("measurement", measurement_covariance),
            ("migration", migration_covariance),
            ("systematic", systematic_covariance),
        ):
            if component.shape != (self.dimension, self.dimension):
                raise ValueError(f"authoritative {label} covariance shape mismatch")
            if not np.isfinite(component).all() or not np.allclose(component, component.T, atol=1e-10):
                raise ValueError(f"authoritative {label} covariance must be finite and symmetric")
            if float(np.min(np.linalg.eigvalsh(component))) < -1e-12:
                raise ValueError(f"authoritative {label} covariance must be positive semidefinite")
        posterior_covariance = measurement_covariance + migration_covariance + systematic_covariance
        if float(np.min(np.linalg.eigvalsh(posterior_covariance))) <= 0.0:
            raise ValueError("authoritative total posterior covariance must be positive definite")
        self._authoritative_mean = authoritative_mean.copy()
        self._authoritative_measurement_covariance = measurement_covariance.copy()
        self._authoritative_migration_covariance = migration_covariance.copy()
        self._authoritative_systematic_covariance = systematic_covariance.copy()
        self._authoritative_covariance = posterior_covariance.copy()
        _, information_logdet = np.linalg.slogdet(self._prior_information + self._data_information)
        data_rank, data_eigenvalues, data_pseudologdet = self._information_audit(self._data_information)
        snapshot = ProgressiveSnapshot(
            chronological_index=int(chronological_index), action=str(action),
            prediction_nll_before_ingest=prediction_nll,
            prequential_observed_rank=observed_rank,
            prequential_status=prequential_status,
            prequential_prior_mean=prequential_prediction.prior_mean.copy(),
            prequential_prior_covariance=prequential_prediction.prior_covariance.copy(),
            prequential_prior_branch_weights=prequential_prediction.prior_branch_weights.copy(),
            information_logdet=float(information_logdet),
            data_information=self._data_information.copy(),
            data_information_rank=data_rank,
            data_information_nonzero_eigenvalues=data_eigenvalues.copy(),
            data_information_pseudologdet=data_pseudologdet,
            uncertainty_trace=float(np.trace(posterior_covariance)),
            branch_concentration=float(np.max(branch_weights)),
            branch_ids=self.branch_ids,
            branch_weights=branch_weights.copy(),
            physical_validity=float(physical_validity),
            posterior_mean=authoritative_mean.copy(),
            posterior_covariance=posterior_covariance.copy(),
            measurement_statistical_covariance=measurement_covariance.copy(),
            temporal_migration_covariance=migration_covariance.copy(),
            shared_systematic_covariance=systematic_covariance.copy(),
            statistical_accumulator_mean=accumulator_mean.copy(),
            statistical_accumulator_covariance=accumulator_covariance.copy(),
        )
        self._last_index = chronological_index
        self._pending_prediction = None
        self._snapshots.append(snapshot)
        self._sufficient_statistics.append({
            "chronological_index": int(chronological_index),
            "action": str(action),
            "observation": value.copy(),
            "observation_covariance": covariance.copy(),
            "data_information": observation_information.copy(),
            "observation_projection": projection.copy(),
            "branch_log_likelihood": branch_ll.copy(),
            "physical_validity": float(physical_validity),
        })
        if chronological_index == 0:
            self.declare_initial_status(complete_calibration=False)
        return snapshot

    def declare_initial_status(self, *, complete_calibration: bool) -> None:
        self.execution_guard.claim_initial_still_completion(
            complete_calibration=complete_calibration,
        )

    def freeze_fit(self) -> None:
        if len(self._sufficient_statistics) != len(self.chronological_actions):
            raise RuntimeError("fit freeze requires all 19 sealed episodes")
        self._fit_frozen = True
        self.execution_guard.freeze_fit()

    def open_holdout(self) -> None:
        self.execution_guard.open_holdout()

    def request_refit(self) -> None:
        self.execution_guard.request_refit()

    def _fresh_accumulate(self) -> dict[str, np.ndarray]:
        data_information = np.zeros_like(self._data_information)
        natural = np.zeros_like(self._natural)
        with np.errstate(divide="ignore"):
            branch_log_weights = np.log(self._initial_branch_weights)
        for row in self._sufficient_statistics:
            information = np.asarray(row["data_information"], dtype=float)
            data_information += information
            natural += information @ np.asarray(row["observation"], dtype=float)
            branch_log_weights += np.asarray(row["branch_log_likelihood"], dtype=float)
        posterior_covariance = np.linalg.pinv(self._prior_information + data_information)
        posterior_mean = posterior_covariance @ natural
        maximum = float(np.max(branch_log_weights))
        branch_weights = np.exp(branch_log_weights - maximum)
        branch_weights /= np.sum(branch_weights)
        return {
            "posterior_mean": posterior_mean,
            "posterior_covariance": posterior_covariance,
            "data_information": data_information,
            "branch_weights": branch_weights,
        }

    def fresh_recompute_and_compare(self, *, injected_mutation: str | None = None) -> dict[str, Any]:
        fresh = self._fresh_accumulate()
        if injected_mutation is not None:
            if injected_mutation != "PERTURB_POSTERIOR_MEAN_FOR_NEGATIVE_GATE":
                raise ValueError("unknown fresh-batch mutation")
            fresh["posterior_mean"] = fresh["posterior_mean"].copy()
            fresh["posterior_mean"][0] += 0.1
        reference_mean, reference_covariance = self._posterior()
        maximum = float(np.max(self._branch_log_weights))
        reference_branch = np.exp(self._branch_log_weights - maximum)
        reference_branch /= np.sum(reference_branch)
        comparisons = {
            "posterior_mean": np.allclose(reference_mean, fresh["posterior_mean"], atol=self.fresh_absolute_tolerance, rtol=self.fresh_relative_tolerance),
            "posterior_covariance": np.allclose(reference_covariance, fresh["posterior_covariance"], atol=self.fresh_absolute_tolerance, rtol=self.fresh_relative_tolerance),
            "data_information": np.allclose(self._data_information, fresh["data_information"], atol=self.fresh_absolute_tolerance, rtol=self.fresh_relative_tolerance),
            "branch_weights": np.allclose(reference_branch, fresh["branch_weights"], atol=self.fresh_absolute_tolerance, rtol=self.fresh_relative_tolerance),
        }
        self.execution_guard.compare_fresh_batch(
            np.asarray([1.0 if all(comparisons.values()) else 0.0]),
            np.asarray([1.0]),
            atol=self.fresh_absolute_tolerance,
            rtol=self.fresh_relative_tolerance,
        )
        return {
            "schema": "biospur-c2-progressive-fresh-recompute-v1",
            "scope": "SYNTHETIC_SUFFICIENT_STAT_REPLAY_ONLY_NOT_FINAL_RAW_RANGE_FRESH_BATCH",
            "independent_accumulator": True,
            "sufficient_statistic_count": len(self._sufficient_statistics),
            "comparisons": {key: bool(value) for key, value in comparisons.items()},
            "pass": bool(all(comparisons.values())),
        }

    def audit(self) -> dict[str, Any]:
        return {
            "schema": "biospur-c2-genuine-progressive-state-v1",
            "state_instances": 1,
            "episode_count": len(self._snapshots),
            "prediction_scored_before_each_ingest": True,
            "prefix_refits": 0,
            "fit_frozen": self._fit_frozen,
            "initial_branch_weights": self._initial_branch_weights.tolist(),
            "branch_ids": list(self.branch_ids),
            "branch_posterior_owner": "THIS_PROGRESSIVE_STATE_IS_THE_SINGLE_AUTHORITY",
            "fresh_recompute_scope": "SYNTHETIC_SUFFICIENT_STAT_REPLAY_ONLY;FINAL_P6_MUST_REREAD_SEALED_RAW_RANGES_AND_RERUN_ALL_FROZEN_OWNERS",
            "authoritative_prequential_prior_sync_events": deepcopy(
                self._authoritative_prior_sync_events
            ),
            "snapshots": [
                {
                    "chronological_index": row.chronological_index,
                    "action": row.action,
                    "prediction_nll_before_ingest": row.prediction_nll_before_ingest,
                    "prequential_observed_rank": row.prequential_observed_rank,
                    "prequential_status": row.prequential_status,
                    "prequential_prior_mean": row.prequential_prior_mean.tolist(),
                    "prequential_prior_covariance": row.prequential_prior_covariance.tolist(),
                    "prequential_prior_branch_weights": row.prequential_prior_branch_weights.tolist(),
                    "information_logdet": row.information_logdet,
                    "data_information": row.data_information.tolist(),
                    "data_information_rank": row.data_information_rank,
                    "data_information_nonzero_eigenvalues": row.data_information_nonzero_eigenvalues.tolist(),
                    "data_information_pseudologdet": row.data_information_pseudologdet,
                    "uncertainty_trace": row.uncertainty_trace,
                    "branch_concentration": row.branch_concentration,
                    "branch_ids": list(row.branch_ids),
                    "branch_weights": row.branch_weights.tolist(),
                    "physical_validity": row.physical_validity,
                    "posterior_mean": row.posterior_mean.tolist(),
                    "posterior_covariance": row.posterior_covariance.tolist(),
                    "measurement_statistical_covariance": row.measurement_statistical_covariance.tolist(),
                    "temporal_migration_covariance": row.temporal_migration_covariance.tolist(),
                    "shared_systematic_covariance": row.shared_systematic_covariance.tolist(),
                    "statistical_accumulator_mean": row.statistical_accumulator_mean.tolist(),
                    "statistical_accumulator_covariance": row.statistical_accumulator_covariance.tolist(),
                }
                for row in self._snapshots
            ],
        }

    def checkpoint(self) -> Mapping[str, Any]:
        return {
            "data_information": self._data_information.copy(),
            "natural": self._natural.copy(),
            "branch_log_weights": self._branch_log_weights.copy(),
            "last_index": self._last_index,
            "snapshots": deepcopy(self._snapshots),
            "fit_frozen": self._fit_frozen,
            "sufficient_statistics": deepcopy(self._sufficient_statistics),
            "pending_prediction": deepcopy(self._pending_prediction),
            "authoritative_mean": self._authoritative_mean.copy(),
            "authoritative_measurement_covariance": self._authoritative_measurement_covariance.copy(),
            "authoritative_migration_covariance": self._authoritative_migration_covariance.copy(),
            "authoritative_systematic_covariance": self._authoritative_systematic_covariance.copy(),
            "authoritative_covariance": self._authoritative_covariance.copy(),
            "authoritative_prior_sync_events": deepcopy(self._authoritative_prior_sync_events),
        }

    def restore(self, checkpoint: Mapping[str, Any]) -> None:
        self._data_information = np.asarray(checkpoint["data_information"], dtype=float).copy()
        self._natural = np.asarray(checkpoint["natural"], dtype=float).copy()
        self._branch_log_weights = np.asarray(checkpoint["branch_log_weights"], dtype=float).copy()
        self._last_index = int(checkpoint["last_index"])
        self._snapshots = deepcopy(checkpoint["snapshots"])
        self._fit_frozen = bool(checkpoint["fit_frozen"])
        self._sufficient_statistics = deepcopy(checkpoint["sufficient_statistics"])
        self._pending_prediction = deepcopy(checkpoint["pending_prediction"])
        self._authoritative_mean = np.asarray(checkpoint["authoritative_mean"], dtype=float).copy()
        self._authoritative_measurement_covariance = np.asarray(
            checkpoint["authoritative_measurement_covariance"], dtype=float,
        ).copy()
        self._authoritative_migration_covariance = np.asarray(
            checkpoint["authoritative_migration_covariance"], dtype=float,
        ).copy()
        self._authoritative_systematic_covariance = np.asarray(
            checkpoint["authoritative_systematic_covariance"], dtype=float,
        ).copy()
        self._authoritative_covariance = np.asarray(checkpoint["authoritative_covariance"], dtype=float).copy()
        self._authoritative_prior_sync_events = deepcopy(
            checkpoint["authoritative_prior_sync_events"]
        )
