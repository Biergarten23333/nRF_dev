"""Capture-wide Class-C sensor calibration posterior for C2.

The documented JY61P decode and unit conversion remain outside this owner.
This module estimates only small residual calibration nuisance around those
fixed units.  Its mixture is causal, per sensor, and never reset per action.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, replace
from hashlib import sha256
import json
from typing import TYPE_CHECKING, Any, Mapping

import numpy as np

if TYPE_CHECKING:
    from .functional_geometry import AxisEstimate, CenterEstimate


_ACC_BIAS = slice(0, 3)
_GYRO_BIAS = slice(3, 6)
_ACC_MATRIX = slice(6, 15)
_GYRO_MATRIX = slice(15, 24)
_DIMENSION = 24


def _canonical(value: Any) -> Any:
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, (np.floating, np.integer, np.bool_)):
        return value.item()
    if isinstance(value, Mapping):
        return {str(key): _canonical(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_canonical(item) for item in value]
    return value


def _semantic_sha(value: Any) -> str:
    return sha256(
        json.dumps(_canonical(value), sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _sym_psd(value: np.ndarray, *, floor: float = 0.0) -> np.ndarray:
    matrix = 0.5 * (np.asarray(value, dtype=float) + np.asarray(value, dtype=float).T)
    values, vectors = np.linalg.eigh(matrix)
    return (vectors * np.maximum(values, floor)) @ vectors.T


@dataclass
class _NodeMixture:
    means: np.ndarray
    covariances: np.ndarray
    log_weights: np.ndarray
    last_timer_us: int | None = None
    last_boot_epoch: int | None = None
    update_count: int = 0
    actions: tuple[str, ...] = ()
    pending_action: str | None = None
    pending_applied_mean: np.ndarray | None = None
    pending_prediction_token: str | None = None


class CaptureWideCalibrationPosterior:
    """Persistent per-sensor residual calibration mixture.

    Bias and small 3x3 scale/cross-axis residuals are represented in each
    component.  Rest/gravity-norm and VQF residual-bias observations update the
    mixture causally.  Unobserved directions remain broad and are marginalized
    by geometry consumers rather than becoming admission failures.
    """

    def __init__(
        self,
        initial_stochastic_state: Mapping[str, Any],
        settings: Mapping[str, Any],
        *,
        sample_period_s: float,
    ) -> None:
        self.settings = deepcopy(dict(settings))
        self.sample_period_s = float(sample_period_s)
        if self.settings.get("schema") != "biospur-c2-capture-wide-calibration-posterior-settings-v1":
            raise ValueError("capture-wide calibration posterior settings schema is invalid")
        if not bool(self.settings.get("fixed_decode_and_units", False)):
            raise ValueError("calibration posterior may not replace JY61P decode or units")
        if bool(self.settings.get("per_action_profiles_allowed", True)):
            raise ValueError("per-action calibration profiles are forbidden")
        weights = np.asarray(self.settings["initial_component_weights"], dtype=float)
        count = int(self.settings["mixture_component_count"])
        if weights.shape != (count,) or count != 5 or np.any(weights <= 0.0):
            raise ValueError("calibration mixture requires five positive pre-registered weights")
        weights /= np.sum(weights)
        acc_sigma = float(self.settings["accelerometer_scale_cross_axis_fraction_sigma"])
        gyro_sigma = float(self.settings["gyroscope_scale_cross_axis_fraction_sigma"])
        acc_bias_sigma = float(self.settings["accelerometer_bias_sigma_mps2"])
        self._prior_acc_matrix_variance = acc_sigma**2
        self._prior_gyro_matrix_variance = gyro_sigma**2
        self._states: dict[str, _NodeMixture] = {}
        self._events: list[dict[str, Any]] = []
        for node in tuple(initial_stochastic_state["nodes"]):
            row = initial_stochastic_state["nodes"][node]
            gyro_bias_cov = _sym_psd(
                np.asarray(row["gyro_bias_covariance_rad2_s2"], dtype=float),
                floor=1e-12,
            )
            means = np.zeros((count, _DIMENSION), dtype=float)
            isotropic = np.eye(3).reshape(-1) / np.sqrt(3.0)
            means[1, _ACC_MATRIX] = acc_sigma * isotropic
            means[2, _ACC_MATRIX] = -acc_sigma * isotropic
            means[3, _GYRO_MATRIX] = gyro_sigma * isotropic
            means[4, _GYRO_MATRIX] = -gyro_sigma * isotropic
            covariance = np.zeros((_DIMENSION, _DIMENSION), dtype=float)
            covariance[_ACC_BIAS, _ACC_BIAS] = np.eye(3) * acc_bias_sigma**2
            covariance[_GYRO_BIAS, _GYRO_BIAS] = gyro_bias_cov
            covariance[_ACC_MATRIX, _ACC_MATRIX] = np.eye(9) * acc_sigma**2
            covariance[_GYRO_MATRIX, _GYRO_MATRIX] = np.eye(9) * gyro_sigma**2
            self._states[str(node)] = _NodeMixture(
                means=means,
                covariances=np.repeat(covariance[None, :, :], count, axis=0),
                log_weights=np.log(weights),
            )

    @staticmethod
    def _normalize_weights(state: _NodeMixture, minimum_weight: float) -> np.ndarray:
        shifted = state.log_weights - float(np.max(state.log_weights))
        weights = np.exp(shifted)
        weights = np.maximum(weights, float(minimum_weight))
        weights /= np.sum(weights)
        state.log_weights = np.log(weights)
        return weights

    def _diffuse_to_episode(
        self,
        node: str,
        time_us: np.ndarray,
        boot_epoch: np.ndarray,
        *,
        action: str,
    ) -> Mapping[str, Any]:
        state = self._states[node]
        timer = np.asarray(time_us, dtype=np.int64)
        boot = np.asarray(boot_epoch, dtype=np.int64)
        if timer.shape != boot.shape or timer.ndim != 1:
            raise ValueError("calibration time and boot arrays must be aligned")
        elapsed_s: float | None = None
        unknown_boot = False
        if len(timer) and state.last_timer_us is not None:
            if int(boot[0]) == int(state.last_boot_epoch) and int(timer[0]) > int(state.last_timer_us):
                elapsed_s = max(
                    0.0,
                    (int(timer[0]) - int(state.last_timer_us)) * 1e-6
                    - self.sample_period_s,
                )
                acc_increment = (
                    float(self.settings["accelerometer_bias_drift_rate_sigma_mps3"])
                    * elapsed_s
                ) ** 2
                gyro_increment = (
                    float(self.settings["gyroscope_bias_drift_rate_sigma_rad_s2"])
                    * elapsed_s
                ) ** 2
            else:
                unknown_boot = True
                acc_increment = float(
                    self.settings["unknown_boot_bias_increment_sigma_mps2"]
                ) ** 2
                gyro_increment = float(
                    self.settings["unknown_boot_gyro_bias_increment_sigma_rad_s"]
                ) ** 2
            for covariance in state.covariances:
                covariance[_ACC_BIAS, _ACC_BIAS] += np.eye(3) * acc_increment
                covariance[_GYRO_BIAS, _GYRO_BIAS] += np.eye(3) * gyro_increment
        if len(timer):
            state.last_timer_us = int(timer[-1])
            state.last_boot_epoch = int(boot[-1])
        event = {
            "action": action,
            "node": node,
            "known_elapsed_s": elapsed_s,
            "unknown_boot_or_reset": unknown_boot,
            "exact_unknown_elapsed_fabricated": False,
        }
        self._events.append({"kind": "CALIBRATION_PRIOR_ADVANCE", **event})
        return event

    @staticmethod
    def _correct_component(
        values: np.ndarray,
        bias: np.ndarray,
        matrix_residual: np.ndarray,
        *,
        maximum_condition_number: float,
    ) -> np.ndarray:
        matrix = np.eye(3) + np.asarray(matrix_residual, dtype=float).reshape(3, 3)
        condition = float(np.linalg.cond(matrix))
        if not np.isfinite(condition) or condition > maximum_condition_number:
            raise FloatingPointError("residual calibration matrix is nonfinite or outside its small-error support")
        return (np.asarray(values, dtype=float) - bias[None, :]) @ np.linalg.inv(matrix).T

    def predict_and_correct(
        self,
        node: str,
        *,
        action: str,
        time_us: np.ndarray,
        boot_epoch: np.ndarray,
        accelerometer_mps2: np.ndarray,
        gyroscope_rad_s: np.ndarray,
    ) -> tuple[np.ndarray, np.ndarray, Mapping[str, Any]]:
        if node not in self._states:
            raise ValueError("calibration prediction requested for an unknown sensor")
        advance = self._diffuse_to_episode(
            node, time_us, boot_epoch, action=action,
        )
        state = self._states[node]
        weights = self._normalize_weights(
            state, float(self.settings["minimum_component_weight"]),
        )
        acc_components = []
        gyro_components = []
        for mean in state.means:
            acc_components.append(self._correct_component(
                accelerometer_mps2,
                mean[_ACC_BIAS],
                mean[_ACC_MATRIX],
                maximum_condition_number=float(
                    self.settings["maximum_calibration_matrix_condition_number"]
                ),
            ))
            gyro_components.append(self._correct_component(
                gyroscope_rad_s,
                mean[_GYRO_BIAS],
                mean[_GYRO_MATRIX],
                maximum_condition_number=float(
                    self.settings["maximum_calibration_matrix_condition_number"]
                ),
            ))
        acc_stack = np.asarray(acc_components)
        gyro_stack = np.asarray(gyro_components)
        acc = np.einsum("k,kni->ni", weights, acc_stack)
        gyro = np.einsum("k,kni->ni", weights, gyro_stack)
        applied_mean = np.einsum("k,kd->d", weights, state.means)
        predictive_snapshot = self.snapshot(node)
        prediction_body = {
            "schema": "biospur-c2-calibration-prior-prediction-v1",
            "node": node,
            "action": action,
            "component_weights_before_episode_update": weights.tolist(),
            "component_count": len(weights),
            "capture_wide_prior_advance": dict(advance),
            "predictive_calibration_posterior_semantic_sha256": (
                predictive_snapshot["semantic_sha256"]
            ),
            "fixed_jy61p_decode_and_units": True,
            "per_action_profile_or_reset": False,
            "latent_truth_consumed": False,
        }
        prediction_token = _semantic_sha({
            **prediction_body,
            "applied_mixture_mean": applied_mean.tolist(),
        })
        state.pending_action = action
        state.pending_applied_mean = applied_mean.copy()
        state.pending_prediction_token = prediction_token
        return acc, gyro, {
            **prediction_body,
            "applied_mixture_mean": applied_mean.tolist(),
            "predictive_calibration_posterior": predictive_snapshot,
            "prediction_token": prediction_token,
        }

    @staticmethod
    def _linear_update(
        mean: np.ndarray,
        covariance: np.ndarray,
        observation: np.ndarray,
        expected: np.ndarray,
        jacobian: np.ndarray,
        observation_covariance: np.ndarray,
    ) -> tuple[np.ndarray, np.ndarray, float]:
        innovation = np.asarray(observation, dtype=float) - np.asarray(expected, dtype=float)
        h = np.asarray(jacobian, dtype=float)
        r = _sym_psd(observation_covariance, floor=1e-12)
        s = _sym_psd(h @ covariance @ h.T + r, floor=1e-12)
        gain = covariance @ h.T @ np.linalg.inv(s)
        updated_mean = mean + gain @ innovation
        identity = np.eye(len(mean))
        attenuation = identity - gain @ h
        updated_covariance = _sym_psd(
            attenuation @ covariance @ attenuation.T + gain @ r @ gain.T,
            floor=1e-14,
        )
        log_likelihood = -0.5 * (
            float(innovation @ np.linalg.solve(s, innovation))
            + float(np.linalg.slogdet(s)[1])
            + len(innovation) * np.log(2.0 * np.pi)
        )
        return updated_mean, updated_covariance, log_likelihood

    def update_episode(
        self,
        node: str,
        *,
        action: str,
        corrected_accelerometer_mps2: np.ndarray,
        vqf_residual_bias_rad_s: np.ndarray,
        vqf_bias_sigma_rad_s: np.ndarray,
        vqf_rest_detected: np.ndarray,
    ) -> Mapping[str, Any]:
        state = self._states[node]
        if (
            state.pending_action != action
            or state.pending_applied_mean is None
            or state.pending_prediction_token is None
        ):
            raise RuntimeError("calibration update lacks its exact prior prediction token")
        applied_mean = state.pending_applied_mean.copy()
        prediction_token = str(state.pending_prediction_token)
        acc = np.asarray(corrected_accelerometer_mps2, dtype=float)
        residual_bias = np.asarray(vqf_residual_bias_rad_s, dtype=float)
        bias_sigma = np.asarray(vqf_bias_sigma_rad_s, dtype=float)
        rest = np.asarray(vqf_rest_detected, dtype=bool)
        if acc.shape != residual_bias.shape or bias_sigma.shape != (len(acc),) or rest.shape != (len(acc),):
            raise ValueError("calibration episode observations are not aligned")
        gyro_observation_available = bool(len(residual_bias))
        rest_indices = np.flatnonzero(rest)
        if action == "00_initial_still" and not len(rest_indices):
            rest_indices = np.arange(len(acc), dtype=int)
        cap = int(self.settings["maximum_rest_rows_per_episode"])
        if len(rest_indices) > cap:
            rest_indices = rest_indices[
                np.unique(np.rint(np.linspace(0, len(rest_indices) - 1, cap)).astype(int))
            ]
        for component in range(len(state.means)):
            mean = state.means[component]
            covariance = state.covariances[component]
            component_log_likelihood = 0.0
            if gyro_observation_available:
                observation = np.median(residual_bias, axis=0)
                h = np.zeros((3, _DIMENSION), dtype=float)
                h[:, _GYRO_BIAS] = np.eye(3)
                sigma = max(float(np.median(bias_sigma)), 1e-4)
                mean, covariance, likelihood = self._linear_update(
                    mean, covariance, observation,
                    mean[_GYRO_BIAS] - applied_mean[_GYRO_BIAS], h,
                    np.eye(3) * sigma**2,
                )
                component_log_likelihood += likelihood
            if len(rest_indices):
                selected = acc[rest_indices]
                representative = np.median(selected, axis=0)
                norm = float(np.linalg.norm(representative))
                if np.isfinite(norm) and norm > 1e-9:
                    unit = representative / norm
                    h = np.zeros((1, _DIMENSION), dtype=float)
                    h[0, _ACC_BIAS] = -unit
                    h[0, _ACC_MATRIX] = -np.outer(unit, representative).reshape(-1)
                    expected = np.array([
                        norm + float((h @ (mean - applied_mean)).item())
                    ])
                    observation = np.array([
                        float(self.settings["initial_still_gravity_mps2"])
                    ])
                    mean, covariance, likelihood = self._linear_update(
                        mean, covariance, observation, expected, h,
                        np.array([[float(
                            self.settings["initial_still_norm_observation_sigma_mps2"]
                        ) ** 2]]),
                    )
                    component_log_likelihood += likelihood
            state.means[component] = mean
            state.covariances[component] = covariance
            state.log_weights[component] += component_log_likelihood
        weights = self._normalize_weights(
            state, float(self.settings["minimum_component_weight"]),
        )
        state.update_count += 1
        state.actions = (*state.actions, action)
        state.pending_action = None
        state.pending_applied_mean = None
        state.pending_prediction_token = None
        snapshot = self.snapshot(node)
        self._events.append({
            "kind": "CALIBRATION_POSTERIOR_UPDATE",
            "node": node,
            "action": action,
            "update_count": state.update_count,
            "rest_rows_used": int(len(rest_indices)),
            "vqf_residual_bias_observation_used": gyro_observation_available,
            "prediction_token_consumed": prediction_token,
            "vqf_residual_equation": (
                "RESIDUAL_BIAS_EQUALS_COMPONENT_BIAS_MINUS_APPLIED_MIXTURE_BIAS"
            ),
            "gravity_norm_residual_equation": (
                "CORRECTED_NORM_PLUS_COMPONENT_MINUS_APPLIED_LINEAR_RESPONSE"
            ),
            "component_weights": weights.tolist(),
            "point_identifiability_required": False,
            "class_c_wide_posterior_terminates_capture": False,
        })
        return snapshot

    def snapshot(self, node: str) -> Mapping[str, Any]:
        state = self._states[node]
        weights = self._normalize_weights(
            state, float(self.settings["minimum_component_weight"]),
        )
        mean = np.einsum("k,kd->d", weights, state.means)
        within_component_covariance = np.zeros(
            (_DIMENSION, _DIMENSION), dtype=float,
        )
        between_component_covariance = np.zeros(
            (_DIMENSION, _DIMENSION), dtype=float,
        )
        for weight, component_mean, component_covariance in zip(
            weights, state.means, state.covariances, strict=True,
        ):
            delta = component_mean - mean
            within_component_covariance += weight * component_covariance
            between_component_covariance += weight * np.outer(delta, delta)
        within_component_covariance = _sym_psd(
            within_component_covariance, floor=0.0,
        )
        between_component_covariance = _sym_psd(
            between_component_covariance, floor=0.0,
        )
        covariance = within_component_covariance + between_component_covariance
        covariance = _sym_psd(covariance, floor=0.0)
        acc_variance_fraction = float(
            np.trace(covariance[_ACC_MATRIX, _ACC_MATRIX])
            / max(9.0 * self._prior_acc_matrix_variance, np.finfo(float).eps)
        )
        gyro_variance_fraction = float(
            np.trace(covariance[_GYRO_MATRIX, _GYRO_MATRIX])
            / max(9.0 * self._prior_gyro_matrix_variance, np.finfo(float).eps)
        )
        branches = [{
            "branch_index": int(index),
            "weight": float(weight),
            "accelerometer_bias_mps2": component_mean[_ACC_BIAS].tolist(),
            "gyroscope_residual_bias_rad_s": component_mean[_GYRO_BIAS].tolist(),
            "accelerometer_scale_cross_axis_residual": component_mean[_ACC_MATRIX].reshape(3, 3).tolist(),
            "gyroscope_scale_cross_axis_residual": component_mean[_GYRO_MATRIX].reshape(3, 3).tolist(),
            "parameter_covariance_diagonal": np.diag(component_covariance).tolist(),
        } for index, (weight, component_mean, component_covariance) in enumerate(
            zip(weights, state.means, state.covariances, strict=True)
        )]
        body = {
            "schema": "biospur-c2-capture-wide-sensor-calibration-posterior-v1",
            "node": node,
            "update_count": state.update_count,
            "actions_consumed": list(state.actions),
            "branches": branches,
            "mixture_mean": mean.tolist(),
            "mixture_covariance": covariance.tolist(),
            "within_component_covariance": within_component_covariance.tolist(),
            "between_component_covariance": between_component_covariance.tolist(),
            "accelerometer_scale_cross_axis_remaining_variance_fraction": acc_variance_fraction,
            "gyroscope_scale_cross_axis_remaining_variance_fraction": gyro_variance_fraction,
            "fixed_jy61p_decode_and_units": True,
            "capture_wide": True,
            "per_action_profile_or_reset": False,
            "point_identifiability_required": False,
            "latent_synthetic_truth_consumed": False,
        }
        return {**body, "semantic_sha256": _semantic_sha(body)}

    def audit(self) -> Mapping[str, Any]:
        return {
            "schema": "biospur-c2-capture-wide-calibration-posterior-audit-v1",
            "nodes": {node: self.snapshot(node) for node in sorted(self._states)},
            "events": deepcopy(self._events),
            "fixed_jy61p_decode_and_units": True,
            "per_action_profile_count": 0,
            "backward_smoothing": False,
        }


def _calibration_snapshot_scale(
    parent_snapshot: Mapping[str, Any],
    child_snapshot: Mapping[str, Any],
    key: str,
) -> float:
    values = [float(parent_snapshot[key]), float(child_snapshot[key])]
    if not np.isfinite(values).all():
        raise ValueError("calibration posterior variance fraction is nonfinite")
    return float(np.clip(max(values), 0.05, 4.0))


def marginalize_center_class_c(
    estimate: "CenterEstimate",
    *,
    parent_snapshot: Mapping[str, Any],
    child_snapshot: Mapping[str, Any],
) -> "CenterEstimate":
    """Convert only soft calibration admission failure into a broad factor."""

    report = deepcopy(dict(estimate.report))
    finite = bool(
        np.isfinite(estimate.joint_to_parent_sensor_m).all()
        and np.isfinite(estimate.joint_to_child_sensor_m).all()
        and np.isfinite(estimate.covariance_m2).all()
    )
    informed_basis = np.asarray(
        report.get("gauge_reduced_robust_bread_informed_basis", []), dtype=float,
    )
    structurally_safe = bool(
        finite
        and report.get("solver_success", False)
        and not report.get("boundary_guard_active", True)
        and report.get("multistart_basin_identifiable", False)
        and report.get("chronological_prefix_heldin_stability", {}).get("pass", False)
        and informed_basis.ndim == 2
        and informed_basis.shape[0] == 6
        and informed_basis.shape[1] > 0
        and int(report.get("gauge_reduced_rank", 0))
        == int(report.get("local_parameter_dimension", 6))
        and not report.get("unknown_boot_transition_pairs", False)
    )
    was_eligible = bool(report.get("owner_update_eligible", False))
    calibration_soft_failure = bool(
        not report.get("information_condition_eligible", False)
        or not report.get("coherent_nuisance_refit_audit", {}).get("pass", False)
    )
    promoted = bool(not was_eligible and structurally_safe and calibration_soft_failure)

    statistical = np.asarray(
        report["statistical_covariance_including_nullspace_prior_m2"], dtype=float,
    )
    components = {
        "accelerometer_bias_drift": np.asarray(
            report["accelerometer_bias_drift_systematic_covariance_m2"], dtype=float,
        ),
        "accelerometer_scale_cross_axis": np.asarray(
            report["accelerometer_scale_cross_axis_systematic_covariance_m2"], dtype=float,
        ),
        "gyro_bias": np.asarray(report["gyro_bias_systematic_covariance_m2"], dtype=float),
        "gyro_bias_drift": np.asarray(
            report["gyro_bias_drift_systematic_covariance_m2"], dtype=float,
        ),
        "gyro_scale_cross_axis": np.asarray(
            report["gyro_scale_cross_axis_systematic_covariance_m2"], dtype=float,
        ),
        "accelerometer_gyro_shared_scale_cross_axis": np.asarray(
            report["accelerometer_gyro_shared_scale_cross_axis_systematic_covariance_m2"], dtype=float,
        ),
        "persistent_pair_clock": np.asarray(
            report["persistent_clock_systematic_covariance_m2"], dtype=float,
        ),
        "human_worn": np.asarray(
            report["human_worn_systematic_covariance_m2"], dtype=float,
        ),
    }
    acc_scale = _calibration_snapshot_scale(
        parent_snapshot, child_snapshot,
        "accelerometer_scale_cross_axis_remaining_variance_fraction",
    )
    gyro_scale = _calibration_snapshot_scale(
        parent_snapshot, child_snapshot,
        "gyroscope_scale_cross_axis_remaining_variance_fraction",
    )
    components["accelerometer_scale_cross_axis"] *= acc_scale
    components["gyro_scale_cross_axis"] *= gyro_scale
    components["accelerometer_gyro_shared_scale_cross_axis"] *= max(acc_scale, gyro_scale)

    refit_solutions = []
    for component in report.get("coherent_nuisance_refit_audit", {}).get("components", []):
        for direction in component.get("direction_rows", []):
            for signed in direction.get("signed_refits", []):
                if (
                    signed.get("solution_m") is not None
                    and signed.get("solver_success", False)
                    and not signed.get("boundary_guard_active", False)
                ):
                    refit_solutions.append(np.asarray(signed["solution_m"], dtype=float))
    if len(refit_solutions) >= 2:
        refit_covariance = np.cov(np.asarray(refit_solutions).T, ddof=1)
        refit_covariance = _sym_psd(refit_covariance, floor=0.0)
    else:
        refit_covariance = np.zeros((6, 6), dtype=float)
    components["accelerometer_scale_cross_axis"] += refit_covariance
    systematic = _sym_psd(sum(components.values(), start=np.zeros((6, 6))), floor=0.0)
    total = _sym_psd(statistical + systematic, floor=0.0)
    report.update({
        "accelerometer_scale_cross_axis_systematic_covariance_m2": components["accelerometer_scale_cross_axis"].tolist(),
        "gyro_scale_cross_axis_systematic_covariance_m2": components["gyro_scale_cross_axis"].tolist(),
        "accelerometer_gyro_shared_scale_cross_axis_systematic_covariance_m2": components["accelerometer_gyro_shared_scale_cross_axis"].tolist(),
        "total_systematic_covariance_m2": systematic.tolist(),
        "sensor_calibration_clock_systematic_covariance_m2": (
            systematic - components["human_worn"]
        ).tolist(),
        "calibration_posterior_mixture_refit_covariance_m2": refit_covariance.tolist(),
        "calibration_posterior_parent_snapshot_sha256": parent_snapshot["semantic_sha256"],
        "calibration_posterior_child_snapshot_sha256": child_snapshot["semantic_sha256"],
        "calibration_posterior_accelerometer_variance_scale": acc_scale,
        "calibration_posterior_gyroscope_variance_scale": gyro_scale,
        "calibration_class_c_structurally_safe": structurally_safe,
        "calibration_class_c_soft_failure": calibration_soft_failure,
        "calibration_class_c_promoted_to_marginal_factor": promoted,
        "coherent_nuisance_refit_required_before_owner_update": False,
        "point_identifiability_of_every_calibration_nuisance_required": False,
        "owner_update_eligible": bool(was_eligible or promoted),
        "owner_update_mode": (
            "CAPTURE_WIDE_CALIBRATION_POSTERIOR_MARGINALIZED_CLASS_C"
            if promoted else report.get("owner_update_mode")
        ),
        "status": "POSTERIOR_CANDIDATE" if was_eligible or promoted else report.get("status"),
    })
    return replace(estimate, covariance_m2=total, report=report)


def marginalize_axis_class_c(
    estimate: "AxisEstimate",
    *,
    parent_snapshot: Mapping[str, Any],
    child_snapshot: Mapping[str, Any],
) -> "AxisEstimate":
    """Allow incomplete calibration point-identifiability to remain Class C."""

    report = deepcopy(dict(estimate.report))
    was_eligible = bool(report.get("owner_update_eligible", False))
    selection_support_rank_safe = bool(
        report.get("block_selection_status") == "NOISE_STANDARDIZED_THRESHOLD_MET"
        and int(report.get("input_rows_after_selection_before_cap", 0))
        >= int(report.get("minimum_selected_observed_rows", 1))
        and float(report.get("effective_support_rows", 0.0))
        >= float(report.get("minimum_effective_support_rows", np.inf))
        and int(report.get("hessian_informed_rank", 0)) == 4
        and report.get("exact_score_hessian_audit", {}).get("pass", False)
        and not report.get("initial_still_present", True)
        and np.isfinite(estimate.tangent_covariance_rad2).all()
    )
    incomplete_calibration = not bool(
        report.get("axis_calibration_nuisance_push_forward_complete", False)
    )
    nuisance_audit = report.get("axis_calibration_nuisance_audit", {})
    required_refit_rows = [
        row
        for row in nuisance_audit.values()
        if isinstance(row, Mapping)
        and "official_refit_linearization_validation" in row
    ]
    calibration_specific_refit_failure = bool(
        required_refit_rows
        and any(
            not row["official_refit_linearization_validation"].get(
                "all_rows_pass", False,
            )
            for row in required_refit_rows
        )
    )
    promoted = bool(
        not was_eligible
        and selection_support_rank_safe
        and incomplete_calibration
        and calibration_specific_refit_failure
    )
    statistical = np.asarray(
        report["statistical_tangent_covariance_rad2"], dtype=float,
    )
    components = {
        str(name): np.asarray(value, dtype=float)
        for name, value in report[
            "systematic_component_tangent_covariances_rad2"
        ].items()
    }
    # The calibration state uses mixed physical units.  Its dimensionless
    # scale/cross-axis variances cannot be relabelled as product-S2 tangent
    # variance.  Use only actual official QMT +/- refit displacements already
    # expressed in the estimator's four-dimensional tangent chart.
    official_refit_component_covariances: dict[str, np.ndarray] = {}
    official_refit_rows: list[dict[str, Any]] = []
    for name, row in nuisance_audit.items():
        if not isinstance(row, Mapping):
            continue
        validation = row.get("official_refit_linearization_validation", {})
        tangent_rows = []
        for validation_row in validation.get("rows", ()):
            tangent = validation_row.get(
                "official_refit_antithetic_tangent_response_rad"
            )
            if (
                tangent is None
                or float(validation_row.get("nuisance_scale", -1.0)) != 1.0
            ):
                continue
            tangent_array = np.asarray(tangent, dtype=float)
            if tangent_array.shape != (4,) or not np.isfinite(tangent_array).all():
                raise ValueError(
                    "official axis calibration refit tangent response is invalid"
                )
            tangent_rows.append(tangent_array)
            official_refit_rows.append({
                "component": str(name),
                "tangent_response_rad": tangent_array.tolist(),
                "required_for_owner_update": bool(
                    validation_row.get("required_for_owner_update", False)
                ),
                "linearization_gate_pass": bool(validation_row.get("pass", False)),
            })
        if tangent_rows:
            official_refit_component_covariances[str(name)] = _sym_psd(
                sum(
                    (np.outer(value, value) for value in tangent_rows),
                    start=np.zeros((4, 4), dtype=float),
                ) / len(tangent_rows),
                floor=0.0,
            )
    official_refit_covariance = _sym_psd(
        sum(
            official_refit_component_covariances.values(),
            start=np.zeros((4, 4), dtype=float),
        ),
        floor=0.0,
    )
    existing_calibration_covariance = _sym_psd(
        sum(
            (
                components[name]
                for name in official_refit_component_covariances
                if name in components
            ),
            start=np.zeros((4, 4), dtype=float),
        ),
        floor=0.0,
    )
    difference = 0.5 * (
        official_refit_covariance - existing_calibration_covariance
        + (official_refit_covariance - existing_calibration_covariance).T
    )
    values, vectors = np.linalg.eigh(difference)
    between_branch_covariance = _sym_psd(
        (vectors * np.maximum(values, 0.0)) @ vectors.T,
        floor=0.0,
    )
    promoted = bool(promoted and official_refit_rows)
    components[
        "calibration_posterior_official_refit_branch_envelope_increment"
    ] = between_branch_covariance
    systematic = _sym_psd(
        sum(components.values(), start=np.zeros((4, 4))), floor=0.0,
    )
    total = _sym_psd(statistical + systematic, floor=0.0)
    report.update({
        "calibration_posterior_parent_snapshot_sha256": parent_snapshot["semantic_sha256"],
        "calibration_posterior_child_snapshot_sha256": child_snapshot["semantic_sha256"],
        "calibration_class_c_selection_support_rank_safe": selection_support_rank_safe,
        "calibration_class_c_incomplete_point_identifiability": incomplete_calibration,
        "calibration_class_c_required_refit_failure": (
            calibration_specific_refit_failure
        ),
        "calibration_class_c_promoted_to_marginal_factor": promoted,
        "calibration_posterior_between_branch_tangent_covariance_rad2": (
            between_branch_covariance.tolist()
        ),
        "calibration_posterior_official_refit_branch_covariance_rad2": (
            official_refit_covariance.tolist()
        ),
        "calibration_posterior_official_refit_tangent_rows": official_refit_rows,
        "calibration_posterior_official_refit_component_covariances_rad2": {
            name: value.tolist()
            for name, value in official_refit_component_covariances.items()
        },
        "systematic_component_tangent_covariances_rad2": {
            name: value.tolist() for name, value in components.items()
        },
        "total_systematic_tangent_covariance_rad2": systematic.tolist(),
        "calibration_posterior_between_branch_covariance_added": bool(
            np.trace(between_branch_covariance) > np.finfo(float).eps
        ),
        "calibration_posterior_official_refit_covariance_retained_or_widened": bool(
            np.min(np.linalg.eigvalsh(
                existing_calibration_covariance
                + between_branch_covariance
                - official_refit_covariance
            )) >= -1e-12
        ),
        "raw_calibration_fraction_variance_relabelled_as_tangent_rad2": False,
        "axis_covariance_push_forward_units": (
            "OFFICIAL_QMT_PRODUCT_S2_TANGENT_RESPONSE_RADIANS_OUTER_PRODUCT"
        ),
        "point_identifiability_of_every_calibration_nuisance_required": False,
        "owner_update_eligible": bool(was_eligible or promoted),
        "owner_update_mode": (
            "OFFICIAL_QMT_WITH_CAPTURE_WIDE_CALIBRATION_POSTERIOR_MARGINALIZATION"
            if promoted else report.get("owner_update_mode")
        ),
    })
    return replace(estimate, tangent_covariance_rad2=total, report=report)
