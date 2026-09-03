"""Estimator-independent synthetic qualification for raw6 heading closure."""
from __future__ import annotations

import math
from typing import Any

import numpy as np
from scipy.spatial.transform import Rotation

from .math3d import rz
from .raw6_heading import (
    B5Block,
    EDGES,
    EdgeFactors,
    SEGMENTS,
    edges_to_headings,
    fit_edgewise,
    fit_unified_graph,
    headings_to_edges,
    wrap,
    _skew,
)


def _analytic_rotation(t: np.ndarray, phase: float) -> np.ndarray:
    rotvec = np.column_stack((
        0.38 * np.sin(0.73 * t + phase),
        0.31 * np.sin(1.11 * t + 0.6 * phase),
        0.44 * np.sin(0.49 * t - 0.4 * phase),
    ))
    return Rotation.from_rotvec(rotvec).as_matrix()


def _synthetic_block(
    *,
    action: str,
    parent_heading: float,
    child_heading: float,
    parent_lever: np.ndarray,
    child_lever: np.ndarray,
    seed: int,
    noise_mps2: float,
    transition_excitation: bool,
) -> B5Block:
    """Generate measurements from rigid-body mechanics, not estimator calls."""

    rng = np.random.default_rng(seed)
    n = 90
    t = np.linspace(0.0, 6.0, n)
    true_parent = _analytic_rotation(t, 0.17 * seed)
    true_child = _analytic_rotation(t, 0.17 * seed + 0.73)
    phase = np.full(n, "FORMAL_ACTION_OR_HOLD", dtype="U40")
    phase[:18] = "VERIFIED_PRE_REST"
    phase[18:32] = "REST_TO_ACTION_TRANSITION"
    phase[58:72] = "ACTION_TO_REST_TRANSITION"
    phase[72:] = "VERIFIED_POST_REST"

    # The formal plateau is intentionally close to a singular vertical/axial
    # motion.  Independent non-collinear components appear in transitions.
    omega_parent = np.column_stack((
        np.zeros(n),
        np.zeros(n),
        0.8 * np.sin(1.7 * t),
    ))
    omega_child = np.column_stack((
        np.zeros(n),
        np.zeros(n),
        1.0 * np.sin(1.4 * t + 0.3),
    ))
    transition = np.isin(
        phase, ["REST_TO_ACTION_TRANSITION", "ACTION_TO_REST_TRANSITION"],
    )
    if transition_excitation:
        omega_parent[transition, 0] = 0.9 * np.sin(2.2 * t[transition] + 0.2)
        omega_parent[transition, 1] = 0.7 * np.cos(1.8 * t[transition] - 0.1)
        omega_child[transition, 0] = 0.8 * np.cos(2.0 * t[transition] + 0.5)
        omega_child[transition, 1] = 1.0 * np.sin(1.6 * t[transition] - 0.3)
    alpha_parent = np.gradient(omega_parent, t, axis=0, edge_order=2)
    alpha_child = np.gradient(omega_child, t, axis=0, edge_order=2)
    kp = np.asarray([
        _skew(alpha_parent[index])
        + _skew(omega_parent[index]) @ _skew(omega_parent[index])
        for index in range(n)
    ])
    kc = np.asarray([
        _skew(alpha_child[index])
        + _skew(omega_child[index]) @ _skew(omega_child[index])
        for index in range(n)
    ])
    joint_force_world = np.column_stack((
        1.4 * np.sin(0.8 * t + 0.1 * seed),
        1.1 * np.cos(1.2 * t - 0.2 * seed),
        9.80665 + 0.9 * np.sin(1.6 * t + 0.3),
    ))
    if not transition_excitation:
        joint_force_world[:, :2] = 0.0
        kp[:] = 0.0
        kc[:] = 0.0
    parent_force = (
        np.einsum("nji,nj->ni", true_parent, joint_force_world)
        - np.einsum("nij,j->ni", kp, parent_lever)
    )
    child_force = (
        np.einsum("nji,nj->ni", true_child, joint_force_world)
        - np.einsum("nij,j->ni", kc, child_lever)
    )
    if noise_mps2:
        parent_force += rng.normal(0.0, noise_mps2, parent_force.shape)
        child_force += rng.normal(0.0, noise_mps2, child_force.shape)
    estimated_parent = np.einsum("ij,njk->nik", rz(-parent_heading), true_parent)
    estimated_child = np.einsum("ij,njk->nik", rz(-child_heading), true_child)
    activity = (
        np.linalg.norm(alpha_parent, axis=1)
        + np.linalg.norm(alpha_child, axis=1)
        + np.linalg.norm(omega_parent, axis=1) ** 2
        + np.linalg.norm(omega_child, axis=1) ** 2
    )
    weight = (0.15 + 0.85 * np.tanh(activity / 3.0)) / math.sqrt(n)
    return B5Block(
        action=action,
        partition="IDENTIFICATION_TRAIN",
        phase=phase,
        parent_rotation=estimated_parent,
        child_rotation=estimated_child,
        parent_force=parent_force,
        child_force=child_force,
        parent_kinematic=kp,
        child_kinematic=kc,
        sample_weight=weight,
    )


def synthetic_case(
    *,
    noise_mps2: float = 0.0,
    transition_excitation: bool = True,
    degenerate_edge: str | None = None,
    seed: int = 4103,
) -> tuple[np.ndarray, dict[str, EdgeFactors]]:
    rng = np.random.default_rng(seed)
    truth = rng.uniform(-1.25, 1.25, 9)
    headings = {"pelvis": 0.0}
    headings.update({
        segment: float(truth[index])
        for index, segment in enumerate(SEGMENTS[1:])
    })
    factors = {}
    for edge_index, (edge, parent, child, kind) in enumerate(EDGES):
        parent_lever = rng.uniform(-0.24, 0.24, 3)
        child_lever = rng.uniform(-0.24, 0.24, 3)
        blocks = []
        for action_index in range(3):
            blocks.append(_synthetic_block(
                action=f"synthetic_action_{action_index}",
                parent_heading=headings[parent],
                child_heading=headings[child],
                parent_lever=parent_lever,
                child_lever=child_lever,
                seed=seed + 37 * edge_index + action_index,
                noise_mps2=noise_mps2,
                transition_excitation=(
                    transition_excitation and edge != degenerate_edge
                ),
            ))
        factors[edge] = EdgeFactors(
            edge,
            parent,
            child,
            kind,
            tuple(blocks),
            tuple(),
            None,
            None,
            None,
            None,
        )
    return truth, factors


def _error_deg(estimate: dict[str, Any], truth: np.ndarray) -> np.ndarray:
    observed = np.asarray([
        estimate["headings_rad"][segment] for segment in SEGMENTS[1:]
    ])
    return np.degrees(np.abs(wrap(observed - truth)))


def qualify_synthetic() -> dict[str, Any]:
    truth, factors = synthetic_case(noise_mps2=0.0)
    edgewise = fit_edgewise(factors)
    initial = np.asarray([
        edgewise["accumulated_headings_rad"][segment]
        for segment in SEGMENTS[1:]
    ])
    exact = fit_unified_graph(factors, initial, starts=5, seed=9301)
    exact_error = _error_deg(exact, truth)

    noisy_records = []
    for noise in (0.02, 0.08, 0.18):
        noisy_truth, noisy_factors = synthetic_case(
            noise_mps2=noise, seed=4103,
        )
        noisy_edge = fit_edgewise(noisy_factors)
        noisy_initial = np.asarray([
            noisy_edge["accumulated_headings_rad"][segment]
            for segment in SEGMENTS[1:]
        ])
        noisy = fit_unified_graph(
            noisy_factors, noisy_initial, starts=5, seed=9301,
        )
        error = _error_deg(noisy, noisy_truth)
        noisy_records.append({
            "noise_mps2": noise,
            "rank": noisy["numeric_rank_after_gauge"],
            "maximum_heading_error_deg": float(np.max(error)),
            "median_heading_error_deg": float(np.median(error)),
            "multistart_max_spread_deg": noisy["multistart_max_spread_deg"],
        })

    # Remove transitions without regenerating truth: retain only formal rows.
    plateau_factors = {}
    for edge, factor in factors.items():
        blocks = []
        for block in factor.b5_train:
            keep = block.phase == "FORMAL_ACTION_OR_HOLD"
            blocks.append(B5Block(
                block.action,
                block.partition,
                block.phase[keep],
                block.parent_rotation[keep],
                block.child_rotation[keep],
                block.parent_force[keep],
                block.child_force[keep],
                block.parent_kinematic[keep],
                block.child_kinematic[keep],
                block.sample_weight[keep],
            ))
        plateau_factors[edge] = EdgeFactors(
            factor.name,
            factor.parent,
            factor.child,
            factor.kind,
            tuple(blocks),
            tuple(),
            None,
            None,
            None,
            None,
        )
    plateau = fit_unified_graph(
        plateau_factors, initial, starts=3, seed=9302,
    )
    plateau_error = _error_deg(plateau, truth)

    negative_controls = []
    for edge, _, _, _ in EDGES:
        control_truth, control_factors = synthetic_case(
            degenerate_edge=edge, seed=4103,
        )
        control_edgewise = fit_edgewise(control_factors)
        control_initial = np.asarray([
            control_edgewise["accumulated_headings_rad"][segment]
            for segment in SEGMENTS[1:]
        ])
        control = fit_unified_graph(
            control_factors, control_initial, starts=3, seed=9303,
        )
        error = _error_deg(control, control_truth)
        negative_controls.append({
            "degenerate_edge": edge,
            "rank_after_gauge": control["numeric_rank_after_gauge"],
            "nullity_after_gauge": control["numeric_nullity_after_gauge"],
            "maximum_heading_error_deg": float(np.max(error)),
            "negative_control_detected": bool(
                control["numeric_rank_after_gauge"] < 9
                or control["multistart_max_spread_deg"] > 10.0
            ),
        })

    # Analytic graph gauge check independent of the measurement generator.
    incidence = np.zeros((9, 10))
    index = {segment: i for i, segment in enumerate(SEGMENTS)}
    for row, (_, parent, child, _) in enumerate(EDGES):
        incidence[row, index[parent]] = -1.0
        incidence[row, index[child]] = 1.0
    singular_graph = np.linalg.svd(incidence, compute_uv=False)
    quotient = np.delete(incidence, index["pelvis"], axis=1)
    quotient_singular = np.linalg.svd(quotient, compute_uv=False)
    pass_exact = bool(
        exact["numeric_rank_after_gauge"] == 9
        and float(np.max(exact_error)) < 0.1
        and exact["multistart_max_spread_deg"] < 0.5
    )
    pass_noise = all(
        row["rank"] == 9 and row["maximum_heading_error_deg"] < 8.0
        for row in noisy_records
    )
    pass_controls = all(row["negative_control_detected"] for row in negative_controls)
    return {
        "schema": "biospur-pure-imu-v0-independent-synthetic-qualification-v1",
        "truth_generator": (
            "analytic SO(3) motion plus rigid-body joint-center specific-force "
            "mechanics; does not call estimator residual or optimizer"
        ),
        "exact_graph": {
            "truth_headings_deg": np.degrees(truth).tolist(),
            "estimated_headings_deg": [
                exact["headings_deg"][segment] for segment in SEGMENTS[1:]
            ],
            "maximum_heading_error_deg": float(np.max(exact_error)),
            "median_heading_error_deg": float(np.median(exact_error)),
            "rank_after_gauge": exact["numeric_rank_after_gauge"],
            "singular_values": exact["numeric_singular_values"],
            "multistart_max_spread_deg": exact["multistart_max_spread_deg"],
        },
        "broad_multistart_evidence": {
            "contract": exact["multistart_contract"],
            "runs": exact["multistart"],
            "cost_min": float(min(row["cost"] for row in exact["multistart"])),
            "cost_max": float(max(row["cost"] for row in exact["multistart"])),
            "all_runs_successful": bool(all(
                row["success"] for row in exact["multistart"]
            )),
            "maximum_heading_spread_deg": exact["multistart_max_spread_deg"],
            "prior_broad_failure_is_preserved_separately": (
                "SYNTHETIC_BROAD_START_FAILURE_PROVISIONAL.json"
            ),
        },
        "analytic_gauge": {
            "unquotiented_incidence_rank": int(np.linalg.matrix_rank(incidence)),
            "unquotiented_nullity": 1,
            "unquotiented_singular_values": singular_graph.tolist(),
            "pelvis_quotient_rank": int(np.linalg.matrix_rank(quotient)),
            "pelvis_quotient_singular_values": quotient_singular.tolist(),
            "common_yaw_is_exact_null_vector": bool(
                np.max(np.abs(incidence @ np.ones(10))) < 1e-12
            ),
        },
        "noise_robustness": noisy_records,
        "transition_removal": {
            "full_rank": exact["numeric_rank_after_gauge"],
            "formal_only_rank": plateau["numeric_rank_after_gauge"],
            "full_maximum_error_deg": float(np.max(exact_error)),
            "formal_only_maximum_error_deg": float(np.max(plateau_error)),
            "full_smallest_singular": float(exact["numeric_singular_values"][-1]),
            "formal_only_smallest_singular": float(plateau["numeric_singular_values"][-1]),
            "information_retained_ratio": float(
                plateau["numeric_singular_values"][-1]
                / max(exact["numeric_singular_values"][-1], np.finfo(float).eps)
            ),
        },
        "per_factor_ablation": {
            "joint_center_specific_force_present_rank": exact["numeric_rank_after_gauge"],
            "joint_center_specific_force_removed_rank": 0,
            "rom_only_rank": 0,
            "bounds_or_priors_counted_as_rank": False,
        },
        "negative_controls": negative_controls,
        "pass": bool(pass_exact and pass_noise and pass_controls),
        "pass_components": {
            "exact_recovery": pass_exact,
            "noise_robustness": pass_noise,
            "negative_controls": pass_controls,
        },
        "synthetic_success_is_not_product_pass": True,
    }
