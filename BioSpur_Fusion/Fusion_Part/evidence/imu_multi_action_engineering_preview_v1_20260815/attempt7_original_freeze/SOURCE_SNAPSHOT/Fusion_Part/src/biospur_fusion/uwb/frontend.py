"""Canonical UWB_TAG_T4 observations with geometry-derived covariance."""
from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

import numpy as np

from .canonical_t4 import (
    load_canonical_t4_solver,
    validate_anchor_slot_identity,
    validate_delay_ownership,
)


@dataclass(frozen=True)
class T4Observation:
    node_id: str
    sweep: int
    global_time_ns: int
    effective_time_ns: int
    temporal_extent_ns: int
    xyz_m: np.ndarray
    covariance_m2: np.ndarray
    anchors_used: tuple[int, ...]
    per_anchor_valid: tuple[bool, ...]
    residuals_m: Mapping[int, float]
    condition: float
    gdop: float
    acceptability: str
    source_sequence: int


def _covariance(layout, result, quality: Sequence[int], time_sigma_s: float,
                temporal_extent_s: float, speed_bound_mps: float = 3.0) -> tuple[np.ndarray, float, float]:
    position = np.array([result.x_mm, result.y_mm, result.z_mm], float)
    rows = []; variances = []
    used = sorted(aid for aid, value in result.used_by_anchor.items() if value)
    for aid in used:
        anchor = layout.anchors[aid]
        delta = position - np.array([anchor.x_mm, anchor.y_mm, anchor.z_mm])
        distance = float(np.linalg.norm(delta))
        if distance < 1.0:
            continue
        rows.append(delta / distance)
        q = max(1.0, float(quality[aid]))
        residual = abs(float(result.residuals_by_anchor.get(aid, 0.0)))
        sigma = max(10.0, float(anchor.sigma_mm) * math.sqrt(100.0 / q), residual / 2.0)
        variances.append(sigma * sigma)
    if len(rows) < 4:
        raise ValueError("T4 covariance has fewer than four independent anchors")
    jacobian = np.asarray(rows)
    information = jacobian.T @ np.diag(1.0 / np.asarray(variances)) @ jacobian
    condition = float(np.linalg.cond(information))
    if not math.isfinite(condition) or condition > 1e10:
        raise ValueError("T4 geometry is singular")
    covariance_mm2 = np.linalg.inv(information)
    dof = max(1, len(rows) - 3)
    residual_scale = max(1.0, sum(float(result.residuals_by_anchor.get(aid, 0.0)) ** 2 for aid in used) / dof / 2500.0)
    covariance_m2 = covariance_mm2 * residual_scale / 1e6
    temporal_sigma = math.sqrt(time_sigma_s ** 2 + (temporal_extent_s / math.sqrt(12.0)) ** 2)
    covariance_m2 += np.eye(3) * (speed_bound_mps * temporal_sigma) ** 2
    gdop = float(math.sqrt(np.trace(np.linalg.inv(jacobian.T @ jacobian))))
    return covariance_m2, condition, gdop


class CanonicalT4Frontend:
    def __init__(self, layout_path: Path):
        validate_delay_ownership(transport_applies_v4_delay=False, solver_applies_v4_delay=True)
        self.models, self.layout_io, self.c_solver = load_canonical_t4_solver()
        self.layout = self.layout_io.load_layout_json(layout_path)
        self.solvers = {}

    def solve(self, *, node_id: str, sweep: int, global_time_ns: int,
              global_time_sigma_ns: int, anchor_ids: Sequence[int], ranges_mm: Sequence[int],
              quality: Sequence[int], valid_mask: int, t_round_us: Sequence[int]) -> T4Observation | None:
        validate_anchor_slot_identity(anchor_ids)
        observations = []
        valid = []
        for slot, aid in enumerate(anchor_ids):
            ok = bool(valid_mask & (1 << slot)) and 0 < int(ranges_mm[slot]) < 0xFFFF
            valid.append(ok)
            if ok:
                observations.append(self.models.Observation(int(aid), float(ranges_mm[slot]), float(quality[slot]), "O"))
        frame = self.models.Frame(node_id, int(sweep), global_time_ns / 1e9, global_time_ns / 1e9,
                                  tuple(observations), None)
        solver = self.solvers.setdefault(node_id, self.c_solver.TagPositionSolver(
            self.layout, self.models.SolverConfig(method="T4")))
        result = solver.solve_frame(frame)
        if result is None:
            return None
        used = tuple(sorted(aid for aid, state in result.used_by_anchor.items() if state))
        offsets = np.asarray([float(t_round_us[aid]) * 0.5 for aid in used], float)
        centre_us = float(np.mean(offsets)) if offsets.size else 0.0
        extent_us = float(np.ptp(offsets)) if offsets.size else 0.0
        covariance, condition, gdop = _covariance(
            self.layout, result, quality, global_time_sigma_ns / 1e9, extent_us / 1e6)
        acceptable = "ACCEPTED" if len(used) >= 4 and condition < 1e8 else "REJECT_GEOMETRY"
        return T4Observation(
            node_id, int(sweep), int(global_time_ns), int(round(global_time_ns + centre_us * 1000.0)),
            int(round(extent_us * 1000.0)), np.array([result.x_mm, result.y_mm, result.z_mm]) / 1000.0,
            covariance, used, tuple(valid), {k: v / 1000.0 for k, v in result.residuals_by_anchor.items()},
            condition, gdop, acceptable, int(sweep),
        )


@dataclass(frozen=True)
class UpdateDecision:
    accepted: bool
    reason: str
    nis: float


def guarded_position_update(state: np.ndarray, covariance: np.ndarray, observation: np.ndarray,
                            observation_covariance: np.ndarray, *, nis_limit: float = 16.26623619623813,
                            external_gate: bool = True) -> tuple[np.ndarray, np.ndarray, UpdateDecision]:
    """Strict predict/NIS/gate/update ordering with byte-stable rejection."""
    x = np.asarray(state, float); p = np.asarray(covariance, float)
    z = np.asarray(observation, float); r = np.asarray(observation_covariance, float)
    h = np.zeros((3, x.size)); h[:, :3] = np.eye(3)
    innovation = z - h @ x
    s = h @ p @ h.T + r
    nis = float(innovation @ np.linalg.solve(s, innovation))
    if not external_gate:
        return x, p, UpdateDecision(False, "REJECT_EXTERNAL_CONSISTENCY", nis)
    if not math.isfinite(nis) or nis > nis_limit:
        return x, p, UpdateDecision(False, "REJECT_NIS", nis)
    gain = np.linalg.solve(s, h @ p).T
    updated = x + gain @ innovation
    identity = np.eye(x.size); kh = gain @ h
    updated_p = (identity - kh) @ p @ (identity - kh).T + gain @ r @ gain.T
    updated_p = 0.5 * (updated_p + updated_p.T)
    np.linalg.cholesky(updated_p)
    return updated, updated_p, UpdateDecision(True, "ACCEPTED", nis)
