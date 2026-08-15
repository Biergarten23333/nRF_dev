"""Real multi-knot articulated batch/fixed-lag smoother.

Unlike the legacy deque prototype, every update relinearizes and optimizes all
states currently inside the lag window.  Ten antenna observations share root,
segment rotations, frozen geometry and joint centres through forward
kinematics.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.optimize import least_squares
from scipy.spatial.transform import Rotation

from biospur_fusion.calibration.articulated_batch import (
    Geometry, HINGES, SEGMENTS, SEGMENT_INDEX, forward_antenna_positions,
)


STATE_WIDTH = 6 + 3 * len(SEGMENTS)


@dataclass(frozen=True)
class EstimatorSamples:
    time_ns: np.ndarray
    base_orientation_N_from_S: np.ndarray
    position_N_m: np.ndarray
    covariance_N_m2: np.ndarray
    valid_position: np.ndarray
    stationary: np.ndarray


@dataclass(frozen=True)
class BatchEstimate:
    state_vector: np.ndarray
    root_position_m: np.ndarray
    root_velocity_mps: np.ndarray
    rotations_N_from_S: np.ndarray
    antenna_position_m: np.ndarray
    accepted: tuple[tuple[int, int], ...]
    rejected: tuple[dict, ...]
    covariance_diagonal: np.ndarray
    covariance_pd: bool
    cost: float
    nfev: int


def _decode_state(vector: np.ndarray, count: int, base: np.ndarray):
    state = np.asarray(vector, float).reshape(count, STATE_WIDTH)
    position = state[:, :3]; velocity = state[:, 3:6]
    rotations = np.empty_like(base)
    for knot in range(count):
        for segment in range(len(SEGMENTS)):
            correction = Rotation.from_rotvec(state[knot, 6+3*segment:9+3*segment]).as_matrix()
            rotations[knot, segment] = base[knot, segment] @ correction
    return state, position, velocity, rotations


class ArticulatedBatchSmoother:
    def __init__(self, geometry: Geometry, *, nis_limit: float = 16.26623619623813):
        self.geometry = geometry; self.nis_limit = float(nis_limit)

    def initial_state(self, samples: EstimatorSamples) -> np.ndarray:
        count = len(samples.time_ns); state = np.zeros((count, STATE_WIDTH), float)
        relative = forward_antenna_positions(samples.base_orientation_N_from_S, self.geometry)
        pelvis = SEGMENT_INDEX["Pelvis"]
        for knot in range(count):
            if samples.valid_position[knot, pelvis]:
                state[knot, :3] = samples.position_N_m[knot, pelvis] - relative[knot, pelvis]
            elif knot:
                state[knot, :3] = state[knot - 1, :3]
        if count > 1:
            dt = np.maximum(1e-6, np.diff(samples.time_ns.astype(float)) / 1e9)
            state[1:, 3:6] = np.diff(state[:, :3], axis=0) / dt[:, None]
            state[0, 3:6] = state[1, 3:6]
        return state.ravel()

    def _gate(self, samples: EstimatorSamples, seed: np.ndarray) -> tuple[list[tuple[int, int]], list[dict]]:
        count = len(samples.time_ns)
        _, root, _, rotations = _decode_state(seed, count, samples.base_orientation_N_from_S)
        predicted = forward_antenna_positions(rotations, self.geometry) + root[:, None]
        accepted: list[tuple[int, int]] = []; rejected: list[dict] = []
        for knot in range(count):
            for segment in range(len(SEGMENTS)):
                if not samples.valid_position[knot, segment]:
                    continue
                innovation = samples.position_N_m[knot, segment] - predicted[knot, segment]
                covariance = samples.covariance_N_m2[knot, segment]
                nis = float(innovation @ np.linalg.solve(covariance, innovation))
                if np.isfinite(nis) and nis <= self.nis_limit:
                    accepted.append((knot, segment))
                else:
                    rejected.append({
                        "knot": knot, "time_ns": int(samples.time_ns[knot]),
                        "segment": SEGMENTS[segment], "pre_update_nis": nis,
                        "reason": "REJECT_NIS", "inserted_into_graph": False,
                    })
        return accepted, rejected

    def solve(self, samples: EstimatorSamples, initial: np.ndarray | None = None,
              *, max_nfev: int = 80) -> BatchEstimate:
        count = len(samples.time_ns)
        if count < 2:
            raise ValueError("articulated batch requires at least two knots")
        seed = self.initial_state(samples) if initial is None else np.asarray(initial, float).copy()
        accepted, rejected = self._gate(samples, seed)

        def residual(vector: np.ndarray) -> np.ndarray:
            state, root, velocity, rotations = _decode_state(
                vector, count, samples.base_orientation_N_from_S)
            antenna = forward_antenna_positions(rotations, self.geometry) + root[:, None]
            terms = [state[:, 6:].ravel() / .08]
            for knot in range(1, count):
                dt = (int(samples.time_ns[knot]) - int(samples.time_ns[knot - 1])) / 1e9
                terms.append((root[knot] - root[knot - 1] - velocity[knot - 1] * dt) / .04)
                terms.append((velocity[knot] - velocity[knot - 1]) / .35)
                for segment in range(len(SEGMENTS)):
                    measured_delta = (samples.base_orientation_N_from_S[knot - 1, segment].T
                                      @ samples.base_orientation_N_from_S[knot, segment])
                    estimated_delta = rotations[knot - 1, segment].T @ rotations[knot, segment]
                    terms.append(Rotation.from_matrix(measured_delta.T @ estimated_delta).as_rotvec() / .035)
                for parent, child, _ in HINGES:
                    ip = SEGMENT_INDEX[parent]; ic = SEGMENT_INDEX[child]
                    previous = rotations[knot - 1, ip].T @ rotations[knot - 1, ic]
                    current = rotations[knot, ip].T @ rotations[knot, ic]
                    hinge_delta = Rotation.from_matrix(previous.T @ current).as_rotvec()
                    terms.append(hinge_delta[[0, 2]] / .12)
            for knot, segment in accepted:
                covariance = samples.covariance_N_m2[knot, segment]
                terms.append(np.linalg.solve(np.linalg.cholesky(covariance),
                                             antenna[knot, segment] - samples.position_N_m[knot, segment]))
            for knot in np.flatnonzero(samples.stationary):
                terms.append(velocity[knot] / .02)
                if knot:
                    terms.append((root[knot] - root[knot - 1]) / .01)
            return np.concatenate(terms)

        result = least_squares(residual, seed, method="trf", loss="soft_l1", f_scale=2.0,
                               x_scale="jac", max_nfev=max_nfev)
        state, root, velocity, rotations = _decode_state(
            result.x, count, samples.base_orientation_N_from_S)
        antenna = forward_antenna_positions(rotations, self.geometry) + root[:, None]
        hessian = result.jac.T @ result.jac
        covariance_pd = True
        try:
            np.linalg.cholesky(hessian)
        except np.linalg.LinAlgError:
            covariance_pd = False
        covariance_diagonal = np.diag(np.linalg.pinv(hessian, rcond=1e-10))
        return BatchEstimate(
            result.x.copy(), root, velocity, rotations, antenna, tuple(accepted), tuple(rejected),
            covariance_diagonal, covariance_pd, float(result.cost), int(result.nfev),
        )


class GenuineFixedLagArticulatedEstimator:
    """Window manager that genuinely reoptimizes every retained historical knot."""
    def __init__(self, geometry: Geometry, lag_s: float = 1.8):
        self.smoother = ArticulatedBatchSmoother(geometry); self.lag_s = float(lag_s)
        self.samples: list[dict] = []; self.last: BatchEstimate | None = None

    def append(self, sample: dict, *, max_nfev: int = 50) -> BatchEstimate | None:
        self.samples.append(sample)
        newest = int(sample["time_ns"])
        self.samples = [row for row in self.samples if (newest - int(row["time_ns"])) / 1e9 <= self.lag_s]
        if len(self.samples) < 2:
            return None
        batch = EstimatorSamples(
            np.asarray([row["time_ns"] for row in self.samples], dtype=np.int64),
            np.asarray([row["base_orientation_N_from_S"] for row in self.samples]),
            np.asarray([row["position_N_m"] for row in self.samples]),
            np.asarray([row["covariance_N_m2"] for row in self.samples]),
            np.asarray([row["valid_position"] for row in self.samples], dtype=bool),
            np.asarray([row["stationary"] for row in self.samples], dtype=bool),
        )
        # A fresh seed is deliberate: every append relinearizes all factors and
        # all retained states, rather than freezing previous output estimates.
        self.last = self.smoother.solve(batch, max_nfev=max_nfev)
        return self.last
