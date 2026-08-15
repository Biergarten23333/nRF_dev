"""Synthetic-only firewall and solver-qualification helpers for S2."""
from __future__ import annotations

from dataclasses import replace
import hashlib
import json
import math
from typing import Any, Mapping

import numpy as np
from scipy.optimize import least_squares
from scipy.spatial.transform import Rotation

from biospur_fusion.imu_multi_action_v1.core import normalize, tangent_basis
from .human_synthetic import HumanSyntheticDataset, HumanSyntheticTruth
from .observability import FUNCTIONAL, S2UnifiedProblem


def canonical_hash(value: Any) -> str:
    def default(item):
        if isinstance(item, np.ndarray):
            return item.tolist()
        if isinstance(item, (np.integer, np.floating)):
            return item.item()
        raise TypeError(type(item).__name__)
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"),
                         allow_nan=False, default=default).encode()
    return hashlib.sha256(payload).hexdigest()


def randomized_truth(dataset: HumanSyntheticDataset, seed: int = 8801) -> HumanSyntheticDataset:
    rng = np.random.default_rng(seed)
    truth = dataset.truth
    random_axes = {key: normalize(rng.normal(size=3)) for key in truth.a_B}
    random_headings = {key: float(rng.uniform(-math.pi, math.pi)) for key in truth.initial_heading_rad}
    changed = replace(truth, a_B=random_axes, initial_heading_rad=random_headings,
                      scenario={**truth.scenario, "truth_mutation": "RANDOMIZED"})
    return replace(dataset, truth=changed)


def permuted_truth(dataset: HumanSyntheticDataset) -> HumanSyntheticDataset:
    truth = dataset.truth
    keys = list(truth.a_B)
    values = list(truth.a_B.values())[::-1]
    changed = replace(truth, a_B=dict(zip(keys, values)),
                      scenario={**truth.scenario, "truth_mutation": "PERMUTED"})
    return replace(dataset, truth=changed)


def initializer_payload(problem: S2UnifiedProblem) -> dict:
    init = problem.init
    return {
        "a_B": init.a_B,
        "transverse_B": init.transverse_B,
        "functional_parent_B": init.functional_parent_B,
        "functional_child_B": init.functional_child_B,
        "heading_rad": init.heading_rad,
        "gyro_bias_B_rad_s": init.gyro_bias_B_rad_s,
        "accel_bias_B_mps2": init.accel_bias_B_mps2,
    }


def firewall_snapshot(problem: S2UnifiedProblem, segmentation: Mapping[str, Any]) -> dict:
    value = np.zeros(problem.parameter_count)
    residual = problem.residual(value)
    jacobian = problem.numerical_jacobian(value, step=2e-6)
    # A deterministic local least-squares step.  The Jacobian is deliberately
    # held fixed so this test checks data dependency, not solver convergence.
    fit = least_squares(problem.residual, value, jac=lambda _: jacobian,
                        method="trf", max_nfev=2, ftol=1e-12, xtol=1e-12,
                        gtol=1e-12)
    return {
        "segmentation_sha256": canonical_hash(segmentation),
        "initializer_sha256": canonical_hash(initializer_payload(problem)),
        "residual_sha256": hashlib.sha256(residual.tobytes()).hexdigest(),
        "jacobian_sha256": hashlib.sha256(jacobian.tobytes()).hexdigest(),
        "fit_result_sha256": canonical_hash({
            "x": fit.x, "cost": float(fit.cost), "status": int(fit.status),
            "nfev": int(fit.nfev), "optimality": float(fit.optimality),
        }),
        "fit_x": fit.x.tolist(),
        "residual_l2": float(np.linalg.norm(residual)),
    }


def _frame(longitudinal: np.ndarray, transverse: np.ndarray) -> np.ndarray:
    z = normalize(longitudinal)
    y = normalize(np.asarray(transverse) - z * float(z @ transverse))
    x = normalize(np.cross(y, z))
    y = normalize(np.cross(z, x))
    return np.column_stack((x, y, z))


def inverse_tangent(base: np.ndarray, target: np.ndarray) -> np.ndarray:
    base = normalize(base); target = normalize(target)
    cross = np.cross(base, target)
    norm = float(np.linalg.norm(cross))
    angle = math.atan2(norm, float(np.clip(base @ target, -1.0, 1.0)))
    if norm < 1e-14:
        return np.zeros(2)
    return tangent_basis(base).T @ (cross / norm * angle)


def truth_parameter_vector(problem: S2UnifiedProblem,
                           truth: HumanSyntheticTruth) -> np.ndarray:
    value = np.zeros(problem.parameter_count)
    for segment in ("pelvis", "torso"):
        target_b = truth.transverse_B[segment]
        update = _frame(truth.a_B[segment], target_b) @ _frame(
            problem.init.a_B[segment], problem.init.transverse_B[segment]
        ).T
        value[problem.slices[f"frame:{segment}"]] = Rotation.from_matrix(update).as_rotvec()
    for segment in problem.dataset.node_to_segment.values():
        if segment not in ("pelvis", "torso"):
            value[problem.slices[f"axis:{segment}"]] = inverse_tangent(
                problem.init.a_B[segment], truth.a_B[segment]
            )
    for joint in FUNCTIONAL:
        targets = truth.hip_axis_B[joint] if joint.startswith("hip_") else truth.hinge_axis_B[joint]
        for role, target in zip(("parent", "child"), targets):
            base = (problem.init.functional_parent_B[joint] if role == "parent"
                    else problem.init.functional_child_B[joint])
            if float(base @ target) < 0:
                target = -target
            value[problem.slices[f"functional:{joint}:{role}"]] = inverse_tangent(base, target)
    for segment in problem.dataset.node_to_segment.values():
        if segment == "pelvis":
            continue
        delta = truth.initial_heading_rad[segment] - problem.init.heading_rad[segment]
        value[problem.slices[f"heading:{segment}"]] = (delta + math.pi) % (2 * math.pi) - math.pi
    return value


def huber_gradient(problem: S2UnifiedProblem, value: np.ndarray,
                   jacobian: np.ndarray, residual: np.ndarray) -> np.ndarray:
    scale = float(problem.gates["robust"]["f_scale"])
    psi = np.where(np.abs(residual) <= scale, residual,
                   scale * np.sign(residual))
    return jacobian.T @ psi
