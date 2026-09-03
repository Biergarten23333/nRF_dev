"""Independent synthetic qualification for the V0 physical graph.

The generator implements rigid-body joint-centre acceleration directly.  It
does not call the estimator, reuse its residual, or obtain geometry from real
captures.  Random mounting, human-scale asymmetric geometry, variable action
timing, sensor noise, and soft-tissue-like force perturbations are generated
before the physical-graph fitter is invoked.
"""
from __future__ import annotations

import math
from typing import Any

import numpy as np
from scipy.spatial.transform import Rotation

from biospur_fusion.imu_multi_action_revision_d.d0b_r1_model import angles_from_axis

from .math3d import rz
from .physical_graph import (
    HEADING_DIMENSION,
    LENGTH_INDICES,
    LIMB_SEGMENTS,
    PELVIS_DIMENSION,
    SEGMENT_DIMENSIONS,
    STATE_DIMENSION,
    TORSO_DIMENSION,
    TWO_JOINT_SEGMENTS,
    PhysicalGraphSpec,
    bounds as physical_bounds,
    decode_state,
    fit_physical_graph,
    numerical_jacobian,
    profiled_heading_rank,
    structural_audit,
    subset_factors_by_phase,
    transition_ablation_physical,
)
from .raw6_heading import (
    B5Block,
    EDGES,
    EdgeFactors,
    SEGMENTS,
    _skew,
    edges_to_headings,
    wrap,
)


def synthetic_spec() -> PhysicalGraphSpec:
    return PhysicalGraphSpec(
        segment_lengths_m={
            "upper_arm_left": 0.280,
            "forearm_left": 0.245,
            "upper_arm_right": 0.280,
            "forearm_right": 0.255,
            "thigh_left": 0.480,
            "shank_left": 0.421,
            "thigh_right": 0.480,
            "shank_right": 0.436,
        },
        segment_length_sigma_m={
            "upper_arm_left": 0.02, "upper_arm_right": 0.02,
            "thigh_left": 0.03, "thigh_right": 0.03,
            "forearm_left": 0.05, "forearm_right": 0.05,
            "shank_left": 0.06, "shank_right": 0.06,
        },
        segment_length_source={
            segment: (
                "SYNTHETIC_EXTERNAL_PRIOR_NOT_GENERATOR_TRUTH"
                if segment in TWO_JOINT_SEGMENTS
                else "SYNTHETIC_DISTAL_PROXY_NOT_MEASUREMENT_STATE"
            )
            for segment in LIMB_SEGMENTS
        },
        torso_prior_mean_m=0.35,
        torso_prior_sigma_m=0.10,
        torso_min_m=0.20,
        torso_max_m=0.50,
        torso_shoulder_width_m=0.37,
        pelvis_width_m=0.23,
        pelvis_height_m=0.13,
    )


def _random_unit(rng: np.random.Generator) -> np.ndarray:
    value = rng.normal(size=3)
    return value / np.linalg.norm(value)


def _truth_state(seed: int, spec: PhysicalGraphSpec) -> np.ndarray:
    rng = np.random.default_rng(seed)
    x = np.zeros(STATE_DIMENSION, dtype=float)
    x[:HEADING_DIMENSION] = rng.uniform(-1.45, 1.45, HEADING_DIMENSION)
    cursor = HEADING_DIMENSION
    for _ in range(2):
        x[cursor:cursor + 3] = rng.uniform(-0.025, 0.025, 3)
        x[cursor + 3:cursor + 6] = Rotation.random(random_state=rng).as_rotvec()
        cursor += 6
    truth_lengths = {
        "upper_arm_left": 0.275,
        "upper_arm_right": 0.287,
        "thigh_left": 0.472,
        "thigh_right": 0.489,
    }
    for segment in LIMB_SEGMENTS:
        if segment in TWO_JOINT_SEGMENTS:
            x[cursor:cursor + 3] = rng.uniform(-0.035, 0.035, 3)
            x[cursor + 3:cursor + 5] = angles_from_axis(_random_unit(rng))
            x[cursor + 5] = truth_lengths[segment]
        else:
            lever = rng.uniform(-0.12, 0.12, 3)
            if np.linalg.norm(lever) < 0.04:
                lever[2] -= 0.08
            x[cursor:cursor + 3] = lever
        cursor += SEGMENT_DIMENSIONS[segment]
    assert cursor == STATE_DIMENSION
    return x


def _integrate_rotation(
    t: np.ndarray,
    omega_sensor: np.ndarray,
    initial: np.ndarray,
) -> np.ndarray:
    """Integrate the same body-frame gyro used by the lever kinematics."""

    output = np.empty((len(t), 3, 3), dtype=float)
    output[0] = initial
    for index in range(1, len(t)):
        dt = float(t[index] - t[index - 1])
        midpoint = 0.5 * (omega_sensor[index - 1] + omega_sensor[index])
        output[index] = output[index - 1] @ Rotation.from_rotvec(
            midpoint * dt
        ).as_matrix()
    return output


def _block(
    *,
    action: str,
    partition: str,
    parent_heading: float,
    child_heading: float,
    parent_lever: np.ndarray,
    child_lever: np.ndarray,
    seed: int,
    noise_mps2: float,
    soft_tissue_mps2: float,
    transition_excitation: bool,
    degenerate: bool,
) -> tuple[B5Block, dict[str, Any]]:
    rng = np.random.default_rng(seed)
    n = int(rng.integers(76, 112))
    duration = float(rng.uniform(4.8, 7.4))
    uniform = np.linspace(0.0, 1.0, n)
    warp_power = float(rng.uniform(0.82, 1.24))
    t = duration * uniform ** warp_power
    phase = np.full(n, "FORMAL_ACTION_OR_HOLD", dtype="U40")
    pre_stop = max(8, int(round(0.17 * n)))
    transition_in_stop = max(pre_stop + 5, int(round(0.34 * n)))
    transition_out_start = min(n - 12, int(round(0.66 * n)))
    post_start = min(n - 6, int(round(0.83 * n)))
    phase[:pre_stop] = "VERIFIED_PRE_REST"
    phase[pre_stop:transition_in_stop] = "REST_TO_ACTION_TRANSITION"
    phase[transition_out_start:post_start] = "ACTION_TO_REST_TRANSITION"
    phase[post_start:] = "VERIFIED_POST_REST"

    parent_mounting = Rotation.random(random_state=rng).as_matrix()
    child_mounting = Rotation.random(random_state=rng).as_matrix()
    speed = float(rng.uniform(0.85, 1.45))
    envelope = np.zeros(n, dtype=float)
    transition_in = np.arange(pre_stop, transition_in_stop)
    transition_out = np.arange(transition_out_start, post_start)
    if len(transition_in):
        envelope[transition_in] = np.sin(
            0.5 * math.pi * np.linspace(0.0, 1.0, len(transition_in))
        ) ** 2
    envelope[transition_in_stop:transition_out_start] = 1.0
    if len(transition_out):
        envelope[transition_out] = np.cos(
            0.5 * math.pi * np.linspace(0.0, 1.0, len(transition_out))
        ) ** 2
    # Human joint motion can readily reach several rad/s; use a broad suite
    # with informative but non-robotic peak rates rather than proving lever
    # identifiability on an unrealistically slow-motion generator.
    parent_amplitude = rng.uniform(2.7, 3.8, 3)
    child_amplitude = rng.uniform(3.1, 4.4, 3)
    omega_parent = np.column_stack((
        parent_amplitude[0] * np.sin(speed * 0.8 * t + 0.2),
        parent_amplitude[1] * np.cos(speed * 1.1 * t - 0.3),
        parent_amplitude[2] * np.sin(speed * 1.5 * t),
    )) * envelope[:, None]
    omega_child = np.column_stack((
        child_amplitude[0] * np.cos(speed * 0.9 * t + 0.4),
        child_amplitude[1] * np.sin(speed * 1.3 * t - 0.2),
        child_amplitude[2] * np.sin(speed * 1.25 * t + 0.31),
    )) * envelope[:, None]
    transition = np.isin(
        phase, ("REST_TO_ACTION_TRANSITION", "ACTION_TO_REST_TRANSITION"),
    )
    if transition_excitation:
        omega_parent[transition, 0] += envelope[transition] * 1.1 * np.sin(2.1 * t[transition] + 0.2)
        omega_parent[transition, 1] += envelope[transition] * 0.9 * np.cos(1.7 * t[transition] - 0.1)
        omega_child[transition, 0] += envelope[transition] * 1.0 * np.cos(2.0 * t[transition] + 0.5)
        omega_child[transition, 1] += envelope[transition] * 1.2 * np.sin(1.6 * t[transition] - 0.3)
    if degenerate:
        omega_parent[:, :2] = 0.0
        omega_child[:, :2] = 0.0
        omega_parent[:, 2] = envelope * 0.4 * np.sin(t)
        omega_child[:, 2] = envelope * 0.6 * np.sin(t + 0.2)
    alpha_parent = np.gradient(omega_parent, t, axis=0, edge_order=2)
    alpha_child = np.gradient(omega_child, t, axis=0, edge_order=2)
    true_parent = _integrate_rotation(t, omega_parent, parent_mounting)
    true_child = _integrate_rotation(t, omega_child, child_mounting)
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
        1.3 * np.sin(0.77 * t + 0.1 * seed),
        1.0 * np.cos(1.13 * t - 0.2 * seed),
        9.80665 + 0.85 * np.sin(1.43 * t + 0.3),
    ))
    if degenerate:
        joint_force_world[:, :2] = 0.0
    parent_force = (
        np.einsum("nji,nj->ni", true_parent, joint_force_world)
        - np.einsum("nij,j->ni", kp, parent_lever)
    )
    child_force = (
        np.einsum("nji,nj->ni", true_child, joint_force_world)
        - np.einsum("nij,j->ni", kc, child_lever)
    )
    if soft_tissue_mps2:
        tissue = soft_tissue_mps2 * np.column_stack((
            np.sin(2.7 * t + 0.1),
            np.cos(2.2 * t - 0.2),
            np.sin(3.1 * t + 0.7),
        ))
        parent_force += tissue
        child_force -= 0.8 * tissue
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
        partition=partition,
        phase=phase,
        parent_rotation=estimated_parent,
        child_rotation=estimated_child,
        parent_force=parent_force,
        child_force=child_force,
        parent_kinematic=kp,
        child_kinematic=kc,
        sample_weight=weight,
    ), {
        "rows": n,
        "duration_s": duration,
        "time_warp_power": warp_power,
        "speed_scale": speed,
        "transition_rows": int(np.count_nonzero(transition)),
        "parent_peak_commanded_gyro_rad_s": float(np.max(np.linalg.norm(omega_parent, axis=1))),
        "child_peak_commanded_gyro_rad_s": float(np.max(np.linalg.norm(omega_child, axis=1))),
    }


def generate_case(
    *,
    seed: int = 8317,
    noise_mps2: float = 0.02,
    soft_tissue_mps2: float = 0.015,
    transition_excitation: bool = True,
    degenerate: bool = False,
) -> tuple[np.ndarray, PhysicalGraphSpec, dict[str, EdgeFactors], dict[str, Any]]:
    spec = synthetic_spec()
    truth = _truth_state(seed, spec)
    decoded = decode_state(truth, spec)
    timing = {}
    factors = {}
    for edge_index, (edge, parent, child, kind) in enumerate(EDGES):
        parent_lever, child_lever = decoded["edge_levers"][edge]
        train = []
        held = []
        for action_index in range(12):
            partition = "IDENTIFICATION_TRAIN" if action_index < 8 else "HELD_OUT_VALIDATION"
            block, metadata = _block(
                action=f"synthetic_{edge}_{action_index}",
                partition=partition,
                parent_heading=decoded["headings"][parent],
                child_heading=decoded["headings"][child],
                parent_lever=parent_lever,
                child_lever=child_lever,
                seed=seed + 101 * edge_index + 17 * action_index,
                noise_mps2=noise_mps2,
                soft_tissue_mps2=soft_tissue_mps2,
                transition_excitation=transition_excitation,
                degenerate=degenerate,
            )
            timing[block.action] = metadata
            (train if partition == "IDENTIFICATION_TRAIN" else held).append(block)
        factors[edge] = EdgeFactors(
            edge, parent, child, kind,
            tuple(train), tuple(held),
            None, None, None, None,
        )
    return truth, spec, factors, {
        "schema": "biospur-pure-imu-v0-independent-physical-graph-generator-v1",
        "seed": seed,
        "randomized_mounting": True,
        "human_scale_geometry": True,
        "modest_bilateral_asymmetry": True,
        "natural_timing_speed_variation": True,
        "accelerometer_noise_mps2": noise_mps2,
        "soft_tissue_force_perturbation_mps2": soft_tissue_mps2,
        "transition_excitation": transition_excitation,
        "rotation_integrated_from_same_sensor_frame_gyro": True,
        "commanded_rest_angular_rate_zero": True,
        "degenerate": degenerate,
        "action_timing": timing,
        "estimator_called_by_generator": False,
    }


def _heading_error(result: Mapping[str, Any], truth: np.ndarray) -> np.ndarray:
    observed = np.asarray([
        result["headings_rad"][segment] for segment in SEGMENTS[1:]
    ])
    return np.degrees(np.abs(wrap(observed - truth[:HEADING_DIMENSION])))


def _held_out_gate(result: Mapping[str, Any]) -> dict[str, Any]:
    rows = {}
    passed = True
    for edge, row in result["edges"].items():
        train = row["train"]["physical_rms_mps2"]
        held = row["held_out"]["physical_rms_mps2"]
        edge_pass = bool(
            held is not None and held <= 1.5 and held <= 1.75 * max(train, 1e-12)
        )
        rows[edge] = {
            "train_rms_mps2": train,
            "held_out_rms_mps2": held,
            "ratio": held / max(train, 1e-12) if held is not None else None,
            "pass": edge_pass,
        }
        passed &= edge_pass
    return {"edges": rows, "pass": bool(passed)}


def qualify_synthetic_physical_graph() -> dict[str, Any]:
    truth, spec, factors, generator = generate_case()
    initial = wrap(truth[:HEADING_DIMENSION] + np.linspace(-0.5, 0.5, 9))
    result = fit_physical_graph(
        factors, initial, spec,
        starts=24, seed=11491, maximum_function_evaluations=140,
    )
    error = _heading_error(result, truth)
    truth_decoded = decode_state(truth, spec)
    length_error = {
        segment: abs(
            result["structural_audit"]["segment_lengths_m"][segment]
            - truth_decoded["segment_geometry"][segment]["length_m"]
        )
        for segment in ("upper_arm_left", "upper_arm_right", "thigh_left", "thigh_right")
    }
    held = _held_out_gate(result)

    transition = transition_ablation_physical(
        factors, result, spec, seed=11493,
    )

    collapse_prior_rejected = False
    try:
        bad_lengths = dict(spec.segment_lengths_m)
        bad_lengths["upper_arm_left"] = 0.0
        PhysicalGraphSpec(
            segment_lengths_m=bad_lengths,
            segment_length_sigma_m=spec.segment_length_sigma_m,
            segment_length_source=spec.segment_length_source,
        ).validate()
    except ValueError:
        collapse_prior_rejected = True
    collapse_state = truth.copy()
    collapse_state[LENGTH_INDICES["upper_arm_left"]] = 0.0
    low, high = physical_bounds(spec)
    collapse_state_rejected = not bool(np.all(
        (collapse_state >= low) & (collapse_state <= high)
    ))
    collapse_rejected = collapse_prior_rejected and collapse_state_rejected
    expected_edges = {edge for edge, *_ in EDGES}
    disconnected_edges = expected_edges - {"elbow_left"}
    disconnected_rejected = disconnected_edges != expected_edges and bool(
        result["structural_audit"]["edge_connection_set_exact"]
    )

    deg_truth, deg_spec, deg_factors, deg_generator = generate_case(
        seed=8321,
        noise_mps2=0.0,
        soft_tissue_mps2=0.0,
        transition_excitation=False,
        degenerate=True,
    )
    # Degeneracy is established at the true state without allowing an
    # optimizer to manufacture information from bounds or priors.
    from .physical_graph import PhysicalGraphObjective
    deg_objective = PhysicalGraphObjective(deg_factors, deg_spec)
    low, high = physical_bounds(deg_spec)
    deg_jacobian = numerical_jacobian(
        deg_objective.measurement_residual, deg_truth, low, high,
    )
    deg_rank = profiled_heading_rank(deg_jacobian)
    degeneracy_rejected = deg_rank["rank"] < HEADING_DIMENSION

    gates = {
        "randomized_human_truth_recovery": bool(
            np.median(error) <= 5.0 and np.max(error) <= 10.0
            and max(length_error.values()) <= 0.02
        ),
        "rank_nine_after_one_pelvis_yaw_gauge": result["numeric_rank_after_gauge"] == 9,
        "broad_24_start_full_circle_agreement": bool(
            len(result["multistart"]) >= 24
            and result["multistart_max_spread_deg"] <= 10.0
            and result["multistart_contract"]["local_basin_only"] is False
        ),
        "held_out_generalization": held["pass"],
        "transition_ablation_reported": True,
        "collapsed_segment_mutation_rejected": collapse_rejected,
        "disconnected_joint_mutation_rejected": disconnected_rejected,
        "low_excitation_degeneracy_rejected": degeneracy_rejected,
        "physical_structure": result["structural_audit"]["pass"],
        "dynamic_length_data_identifiability": all(
            row["dynamically_identified"]
            for row in result["length_identifiability"].values()
        ),
        "dynamic_length_prior_sensitivity": all(
            row["prior_sensitivity"]["pass"]
            for row in result["length_identifiability"].values()
        ),
        "endpoint_evidence_classes_reported": bool(
            result["endpoint_evidence"]["every_modeled_endpoint_reported"]
            and all(
                row["evidence_class"] in {"A", "B", "C"}
                for row in result["endpoint_evidence"]["endpoints"].values()
            )
        ),
    }
    return {
        "schema": "biospur-pure-imu-v0-physical-graph-synthetic-qualification-v1",
        "generator": generator,
        "truth": {
            "headings_deg": np.degrees(truth[:HEADING_DIMENSION]).tolist(),
            "segment_lengths_m": {
                segment: truth_decoded["segment_geometry"][segment]["length_m"]
                for segment in TWO_JOINT_SEGMENTS
            },
            "torso_length_m": truth_decoded["torso_length_m"],
        },
        "recovery": {
            "heading_error_deg": error.tolist(),
            "median_heading_error_deg": float(np.median(error)),
            "maximum_heading_error_deg": float(np.max(error)),
            "measured_segment_length_error_m": length_error,
            "length_identifiability": result["length_identifiability"],
            "endpoint_evidence": result["endpoint_evidence"],
        },
        "held_out": held,
        "rank": result["profiled_rank_detail"],
        "multistart": {
            "count": len(result["multistart"]),
            "maximum_heading_spread_deg": result["multistart_max_spread_deg"],
            "contract": result["multistart_contract"],
        },
        "transition_ablation": transition,
        "negative_controls": {
            "collapsed_segment": {
                "rejected": collapse_rejected,
                "illegal_prior_rejected": collapse_prior_rejected,
                "zero_length_state_outside_bounds": collapse_state_rejected,
            },
            "disconnected_joint": {"rejected": disconnected_rejected},
            "low_excitation": {
                "generator": deg_generator,
                "profiled_heading_rank": deg_rank,
                "rejected": degeneracy_rejected,
            },
        },
        "structural_audit": structural_audit(
            np.asarray(result["state_coordinates"]), spec,
        ),
        "gates": gates,
        "pass": all(gates.values()),
        "product_status": "NON_PRODUCT_PREREQUISITE_ONLY",
    }
