"""Offline sequential fixed-lag articulated estimator used for qualification."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Mapping

import numpy as np
from scipy.optimize import least_squares
from scipy.spatial.transform import Rotation

from .model import SEGMENTS, ArticulatedBodyModel, BodyState


@dataclass(frozen=True)
class PositionFactor:
    segment: str
    position_m: np.ndarray
    covariance_m2: np.ndarray


@dataclass(frozen=True)
class KnotResult:
    state: BodyState
    accepted: tuple[str, ...]
    rejected: tuple[str, ...]
    max_joint_residual_m: float
    covariance_pd: bool


class ArticulatedFixedLagEstimator:
    """One shared root/segment state; positions are never independent states."""
    def __init__(self, model: ArticulatedBodyModel, lag_s: float = 1.8,
                 nis_limit: float = 16.26623619623813):
        self.model = model; self.lag_s = float(lag_s); self.nis_limit = float(nis_limit)
        self.knots: deque[KnotResult] = deque()
        self.rejection_ledger: list[dict] = []

    @staticmethod
    def _pack(state: BodyState) -> np.ndarray:
        values = [np.asarray(state.pelvis_origin_m, float)]
        values.extend(Rotation.from_matrix(np.asarray(state.rotations_N_from_S[s])).as_rotvec() for s in SEGMENTS)
        return np.concatenate(values)

    @staticmethod
    def _unpack(time_s: float, vector: np.ndarray, velocity: np.ndarray) -> BodyState:
        rotations = {segment: Rotation.from_rotvec(vector[3 + 3*i:6 + 3*i]).as_matrix()
                     for i, segment in enumerate(SEGMENTS)}
        return BodyState(float(time_s), vector[:3].copy(), rotations, np.asarray(velocity, float).copy())

    def update(self, time_s: float, prior: BodyState, orientation_N_from_S: Mapping[str, np.ndarray],
               factors: tuple[PositionFactor, ...], motion_segments: frozenset[str] = frozenset()) -> KnotResult:
        x0 = self._pack(prior); predicted_antennas = self.model.antennas(prior)
        accepted = []; rejected = []
        for factor in factors:
            innovation = np.asarray(factor.position_m) - predicted_antennas[factor.segment]
            nis = float(innovation @ np.linalg.solve(factor.covariance_m2, innovation))
            # A moving segment receives a larger causal reach envelope, never a
            # post-update forgiveness. NIS remains pre-update.
            limit = self.nis_limit * (4.0 if factor.segment in motion_segments else 1.0)
            if np.isfinite(nis) and nis <= limit:
                accepted.append(factor)
                verdict = "ACCEPTED"
            else:
                rejected.append(factor.segment); verdict = "REJECT_NIS"
            self.rejection_ledger.append({"time_s": float(time_s), "segment": factor.segment,
                                          "pre_update_nis": nis, "verdict": verdict})

        prior_sigma_p = .08; prior_sigma_r = .10
        def residual(vector: np.ndarray) -> np.ndarray:
            candidate = self._unpack(time_s, vector, prior.pelvis_velocity_mps)
            terms = [(vector[:3] - x0[:3]) / prior_sigma_p]
            for i, segment in enumerate(SEGMENTS):
                target = Rotation.from_matrix(np.asarray(orientation_N_from_S[segment])).as_rotvec()
                terms.append((vector[3+3*i:6+3*i] - target) / prior_sigma_r)
            antennas = self.model.antennas(candidate)
            for factor in accepted:
                chol = np.linalg.cholesky(factor.covariance_m2)
                terms.append(np.linalg.solve(chol, antennas[factor.segment] - factor.position_m))
            return np.concatenate(terms)

        solution = least_squares(residual, x0, method="trf", loss="linear", max_nfev=20)
        dt = max(1e-6, float(time_s) - float(prior.time_s))
        velocity = (solution.x[:3] - prior.pelvis_origin_m) / dt
        state = self._unpack(time_s, solution.x, velocity)
        hessian = solution.jac.T @ solution.jac
        covariance_pd = True
        try:
            np.linalg.cholesky(hessian)
        except np.linalg.LinAlgError:
            covariance_pd = False
        max_joint = max(self.model.constraint_residuals(state).values(), default=0.0)
        result = KnotResult(state, tuple(f.segment for f in accepted), tuple(rejected), max_joint, covariance_pd)
        self.knots.append(result)
        while self.knots and time_s - self.knots[0].state.time_s > self.lag_s:
            self.knots.popleft()
        return result


def interpolate_bounded(times: np.ndarray, values: np.ndarray, query: float, max_gap_s: float) -> np.ndarray | None:
    """Linear rendering/association interpolation that refuses wide gaps."""
    times = np.asarray(times, float); values = np.asarray(values, float)
    right = int(np.searchsorted(times, query))
    if right == 0 or right == len(times):
        return None
    left = right - 1
    if times[right] - times[left] > max_gap_s:
        return None
    alpha = (query - times[left]) / (times[right] - times[left])
    return (1.0 - alpha) * values[left] + alpha * values[right]
