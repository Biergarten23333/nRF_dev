from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

import numpy as np

from . import so3
from .fk import articulated_fk
from .joints import JOINTS
from .types import CalibrationBundle, FactorLedgerRow, FrontendFrame, PoseTick, SEGMENTS


@dataclass(frozen=True, slots=True)
class EstimatorConfig:
    measurement_floor_sigma_rad: float = np.deg2rad(0.7)
    measurement_huber_rad: float = np.deg2rad(15.0)
    temporal_relative_sigma_rad: float = np.deg2rad(5.0)
    neutral_reference_sigma_rad: float = np.deg2rad(18.0)
    hinge_orthogonal_sigma_rad: float = np.deg2rad(15.0)
    rom_softness: float = 0.20
    maximum_frame_step_rad: float = np.deg2rad(12.0)
    iterations: int = 2
    line_search_minimum: float = 1.0 / 64.0
    information_jitter: float = 1e-9


@dataclass(frozen=True, slots=True)
class _Factor:
    name: str
    source_uids: tuple[str, ...]
    residual: np.ndarray
    jacobian: np.ndarray
    sqrt_information: np.ndarray
    jacobian_blocks: tuple[int, ...]


def _chol_solve(matrix: np.ndarray, rhs: np.ndarray) -> np.ndarray:
    lower = np.linalg.cholesky(matrix)
    return np.linalg.solve(lower.T, np.linalg.solve(lower, rhs))


class ContinuousArticulatedEstimator:
    """One causal 30-D articulated orientation state for an entire session.

    Calibration covariance is marginalized into the sensor likelihood.  It is
    deliberately absent from the factor-name set: uncertainty is not another
    independent observation.
    """

    def __init__(
        self,
        calibration: CalibrationBundle,
        *,
        config: EstimatorConfig | None = None,
        neutral_relative: Mapping[str, np.ndarray] | None = None,
        neutral_covariance: Mapping[str, np.ndarray] | None = None,
        functional_axes_child: Mapping[str, np.ndarray] | None = None,
        functional_axis_confidence: Mapping[str, float] | None = None,
    ):
        self.calibration = calibration
        self.config = config or EstimatorConfig()
        self.index = {segment: index for index, segment in enumerate(SEGMENTS)}
        self.q: dict[str, np.ndarray] | None = None
        self.previous_q: dict[str, np.ndarray] | None = None
        self.previous_measurements: dict[str, np.ndarray] | None = None
        self.previous_uids: dict[str, str] | None = None
        self.neutral_relative = {key: so3.normalize(value) for key, value in (neutral_relative or {}).items()}
        self.neutral_covariance = {key: np.asarray(value, float) for key, value in (neutral_covariance or {}).items()}
        self.functional_axes_child = {
            key: np.asarray(value, float) / np.linalg.norm(value)
            for key, value in (functional_axes_child or {}).items()
        }
        self.functional_axis_confidence = dict(functional_axis_confidence or {})
        self.covariance = np.eye(30) * np.deg2rad(20.0) ** 2
        self.factor_ledger: list[FactorLedgerRow] = []
        self.information_components: dict[str, np.ndarray] = {}
        self.last_time_ns: int | None = None
        self.action_boundary_reset_count = 0

    def notify_action_boundary(self, _: str) -> None:
        return None

    def _block(self, segment: str) -> slice:
        start = 3 * self.index[segment]
        return slice(start, start + 3)

    def _relative(self, orientations: Mapping[str, np.ndarray], parent: str, child: str) -> np.ndarray:
        return so3.between(orientations[parent], orientations[child])

    def _joint_jacobian(self, orientations: Mapping[str, np.ndarray], parent: str, child: str) -> np.ndarray:
        relative = self._relative(orientations, parent, child)
        jacobian = np.zeros((3, 30))
        jacobian[:, self._block(parent)] = -so3.matrix(relative).T
        jacobian[:, self._block(child)] = np.eye(3)
        return jacobian

    @staticmethod
    def _whitener(covariance: np.ndarray) -> np.ndarray:
        lower = np.linalg.cholesky(0.5 * (covariance + covariance.T))
        return np.linalg.solve(lower, np.eye(lower.shape[0]))

    def _factors(
        self,
        orientations: Mapping[str, np.ndarray],
        measured: Mapping[str, np.ndarray],
        covariance: Mapping[str, np.ndarray],
        uids: Mapping[str, str],
    ) -> list[_Factor]:
        factors: list[_Factor] = []
        for segment in SEGMENTS:
            residual = so3.log(so3.between(measured[segment], orientations[segment]))
            jacobian = np.zeros((3, 30)); jacobian[:, self._block(segment)] = np.eye(3)
            cov = covariance[segment] + np.eye(3) * self.config.measurement_floor_sigma_rad ** 2
            robust = min(1.0, self.config.measurement_huber_rad / max(float(np.linalg.norm(residual)), 1e-12))
            factors.append(_Factor(
                "raw_imu_orientation_likelihood", (uids[segment],), residual, jacobian,
                np.sqrt(robust) * self._whitener(cov), (self.index[segment],),
            ))

        for joint_index, spec in enumerate(JOINTS):
            relative = self._relative(orientations, spec.parent, spec.child)
            joint_jacobian = self._joint_jacobian(orientations, spec.parent, spec.child)
            source = (uids[spec.parent], uids[spec.child])
            blocks = (self.index[spec.parent], self.index[spec.child])
            if (self.previous_q is not None and self.previous_measurements is not None
                    and self.previous_uids is not None
                    and (uids[spec.parent] != self.previous_uids[spec.parent]
                         or uids[spec.child] != self.previous_uids[spec.child])):
                previous_state = self._relative(self.previous_q, spec.parent, spec.child)
                previous_measurement = self._relative(self.previous_measurements, spec.parent, spec.child)
                current_measurement = self._relative(measured, spec.parent, spec.child)
                observed_increment = so3.between(previous_measurement, current_measurement)
                predicted = so3.mul(previous_state, observed_increment)
                residual = so3.log(so3.between(predicted, relative))
                factors.append(_Factor(
                    "temporal_relative_motion", source, residual, joint_jacobian,
                    np.eye(3) / self.config.temporal_relative_sigma_rad, blocks,
                ))
            if spec.name in self.neutral_relative:
                residual = so3.log(so3.between(self.neutral_relative[spec.name], relative))
                cov = self.neutral_covariance.get(
                    spec.name, np.eye(3) * self.config.neutral_reference_sigma_rad ** 2
                )
                factors.append(_Factor(
                    "neutral_relative_pose_reference", source, residual, joint_jacobian,
                    self._whitener(cov), blocks,
                ))
            if spec.kind == "hinge" and spec.name in self.functional_axes_child:
                confidence = float(np.clip(self.functional_axis_confidence.get(spec.name, 0.0), 0.0, 1.0))
                if confidence > 0:
                    axis = self.functional_axes_child[spec.name]
                    projector = np.eye(3) - np.outer(axis, axis)
                    reference = self.neutral_relative.get(spec.name, np.array([1.0, 0.0, 0.0, 0.0]))
                    rotation_vector = so3.log(so3.between(reference, relative))
                    factors.append(_Factor(
                        "functional_axis_soft_constraint", source, projector @ rotation_vector,
                        projector @ joint_jacobian,
                        np.eye(3) * np.sqrt(confidence) / self.config.hinge_orthogonal_sigma_rad, blocks,
                    ))
            if spec.kind == "multi":
                rotation_vector = so3.log(relative)
                excess = np.maximum(np.abs(rotation_vector) - spec.rom_rad, 0.0)
                if np.any(excess > 0):
                    residual = np.sign(rotation_vector) * self.config.rom_softness * (excess / spec.rom_rad) ** 3
                    derivative = 3 * self.config.rom_softness * (excess / spec.rom_rad) ** 2 / spec.rom_rad
                    factors.append(_Factor(
                        "soft_rom_compliance", source, residual,
                        np.diag(derivative) @ joint_jacobian, np.eye(3), blocks,
                    ))
        return factors

    @staticmethod
    def _cost(factors: list[_Factor]) -> float:
        return float(sum(np.dot(f.sqrt_information @ f.residual, f.sqrt_information @ f.residual) for f in factors))

    def _normal(self, factors: list[_Factor]) -> tuple[np.ndarray, np.ndarray, dict[str, np.ndarray]]:
        information = np.eye(30) * self.config.information_jitter
        gradient = np.zeros(30)
        components: dict[str, np.ndarray] = {}
        for factor in factors:
            weighted_jacobian = factor.sqrt_information @ factor.jacobian
            weighted_residual = factor.sqrt_information @ factor.residual
            contribution = weighted_jacobian.T @ weighted_jacobian
            information += contribution
            gradient += weighted_jacobian.T @ weighted_residual
            components.setdefault(factor.name, np.zeros((30, 30)))
            components[factor.name] += contribution
        return information, gradient, components

    def update(self, scheduled_time_ns: int, frames_by_node: Mapping[str, FrontendFrame]) -> PoseTick:
        if self.last_time_ns is not None and scheduled_time_ns <= self.last_time_ns:
            raise ValueError("scheduled output must be strictly causal")
        if set(frames_by_node) != set(self.calibration.mapping):
            raise ValueError("ten mapped node inputs are required")
        measured: dict[str, np.ndarray] = {}
        covariance: dict[str, np.ndarray] = {}
        uids: dict[str, str] = {}
        ages: dict[str, int] = {}
        statuses: list[str] = []
        for node in sorted(frames_by_node):
            frame = frames_by_node[node]
            segment = self.calibration.mapping[node]
            age_ns = max(0, scheduled_time_ns - frame.sample_time_ns)
            measured[segment], covariance[segment] = (
                so3.normalize(so3.mul(frame.q_WI, self.calibration.by_node[node].q_I_S)),
                so3.compose_right_covariance(
                    self.calibration.by_node[node].q_I_S,
                    frame.covariance[:3, :3],
                    self.calibration.by_node[node].covariance_rad2,
                    self.calibration.by_node[node].cross_covariance_rad2,
                ),
            )
            # A stale orientation is a process prediction, not a fresh
            # likelihood.  Its tangent envelope grows with the entire gap.
            covariance[segment] += np.eye(3) * (0.05 * age_ns * 1e-9) ** 2
            uids[segment] = frame.sample_uid
            ages[node] = age_ns
            statuses.append(frame.status)
        if self.q is None:
            self.q = {segment: measured[segment].copy() for segment in SEGMENTS}

        accepted_factors: list[_Factor] = []
        accepted_step = 0.0
        for _ in range(self.config.iterations):
            factors = self._factors(self.q, measured, covariance, uids)
            information, gradient, _ = self._normal(factors)
            delta = -_chol_solve(information, gradient)
            max_step = max(float(np.linalg.norm(delta[self._block(segment)])) for segment in SEGMENTS)
            budget = self.config.maximum_frame_step_rad / max(self.config.iterations, 1)
            if max_step > budget:
                delta *= budget / max_step
            old_cost = self._cost(factors)
            scale = 1.0
            while scale >= self.config.line_search_minimum:
                candidate = {
                    segment: so3.apply_right(self.q[segment], scale * delta[self._block(segment)])
                    for segment in SEGMENTS
                }
                candidate_factors = self._factors(candidate, measured, covariance, uids)
                if self._cost(candidate_factors) <= old_cost + 1e-12:
                    self.q = candidate
                    accepted_factors = candidate_factors
                    accepted_step = scale * max_step
                    break
                scale *= 0.5
            if scale < self.config.line_search_minimum:
                accepted_factors = factors
                accepted_step = 0.0
                break

        information, _, components = self._normal(accepted_factors)
        self.covariance = _chol_solve(information, np.eye(30))
        self.covariance = 0.5 * (self.covariance + self.covariance.T)
        if float(np.linalg.eigvalsh(self.covariance)[0]) < -1e-10:
            raise FloatingPointError("conditional curvature covariance is not PSD")
        self.information_components = {key: value.copy() for key, value in components.items()}
        for factor in accepted_factors:
            self.factor_ledger.append(FactorLedgerRow(
                scheduled_time_ns, factor.name, factor.source_uids, True,
                float(np.linalg.norm(factor.residual)), factor.jacobian_blocks,
                float(np.linalg.norm(factor.sqrt_information, ord=2) ** 2), accepted_step,
            ))

        if "UNAVAILABLE" in statuses:
            status = "UNAVAILABLE"
        elif "REINITIALIZING" in statuses:
            status = "REINITIALIZING"
        elif "PREDICTED_DEGRADED" in statuses or max(ages.values()) > 250_000_000:
            status = "PREDICTED_DEGRADED"
        else:
            status = "FILTERED"
        usability = {
            "OUTPUT_PRESENT": True,
            "INTERNAL_UNCERTAINTY_GATE": bool(np.max(np.diag(self.covariance)) < np.deg2rad(30.0) ** 2),
            "TILT_CONDITIONALLY_USABLE": status == "FILTERED",
            "TWIST_UNRESOLVED": any(row.twist_status != "TWIST_IDENTIFIED" for row in self.calibration.by_node.values()),
            "JOINT_SEMANTIC_GATE_PASS": "NOT_EVALUATED_BY_RUNTIME",
            "DEGRADED_OR_PREDICTED": status != "FILTERED",
            "EXTERNAL_ACCURACY_UNVALIDATED": True,
        }
        tick = PoseTick(
            scheduled_time_ns, status, {key: value.copy() for key, value in self.q.items()},
            self.covariance.copy(), articulated_fk(self.q), dict(ages), usability=usability,
        )
        self.previous_q = {key: value.copy() for key, value in self.q.items()}
        self.previous_measurements = {key: value.copy() for key, value in measured.items()}
        self.previous_uids = dict(uids)
        self.last_time_ns = scheduled_time_ns
        return tick

    def actual_information_components(self) -> dict[str, np.ndarray]:
        return {key: value.copy() for key, value in self.information_components.items()}


def reject_posthoc_covariance_rewrite(*_: object, **__: object) -> None:
    raise RuntimeError("post-hoc covariance/availability rewrite is forbidden")
