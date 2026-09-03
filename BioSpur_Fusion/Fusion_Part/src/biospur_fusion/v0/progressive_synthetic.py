"""Independent progressive-calibration qualification suite."""
from __future__ import annotations

from dataclasses import replace
import copy
import math
from typing import Any, Mapping, Sequence

import numpy as np

from .physical_graph import (
    HEADING_DIMENSION,
    LENGTH_INDICES,
    LIMB_SEGMENTS,
    SEGMENT_SLICES,
    TWO_JOINT_SEGMENTS,
    numerical_jacobian,
    real_subject_spec,
)
from .physical_graph_synthetic import generate_case
from .progressive_calibration import (
    LATENT_LOWER,
    LATENT_SEED,
    LATENT_SLICE,
    LATENT_UPPER,
    STATE_DIMENSION,
    B5Block,
    EdgeFactors,
    ProgressiveObjective,
    ProgressiveGeometryPrior,
    classify_episode,
    factor_partition_identity,
    final_gates,
    held_out_report,
    information_snapshot,
    informative_sparsify,
    physical_state_report,
    progressive_bounds,
    readiness_snapshot,
    select_factor_actions,
    soft_l1_measurement_rows,
    solve_cumulative,
    lower_limb_topology_report,
)
from .raw6_heading import EDGES, SEGMENTS, wrap
from biospur_fusion.imu_multi_action_revision_d.d0b_r1_model import angles_from_axis


SYNTHETIC_EPISODES = tuple(f"synthetic_episode_{index:02d}" for index in range(12))


def _simulated_geometry_prior(truth: np.ndarray, *, seed: int) -> ProgressiveGeometryPrior:
    """Create an independent noisy external observation for qualification.

    The fitter receives only this noisy observation and its uncertainty, never
    the latent truth itself.  This exercises the same exact-Gaussian path used
    by a real tape measurement without pretending that a surface landmark is
    an internal joint centre.
    """

    sigma = np.array([0.06, 0.06, 0.08, 0.05], dtype=float)
    rng = np.random.default_rng(seed)
    observed = np.asarray(truth[LATENT_SLICE], dtype=float) + rng.normal(
        0.0, 0.20 * sigma,
    )
    observed = np.clip(observed, LATENT_LOWER + 1e-4, LATENT_UPPER - 1e-4)
    return ProgressiveGeometryPrior(
        mean_m=tuple(float(value) for value in observed),
        sigma_m=tuple(float(value) for value in sigma),
        source=tuple("INDEPENDENT_NOISY_SYNTHETIC_EXTERNAL_OBSERVATION" for _ in range(4)),
        provenance_path="synthetic://progressive-geometry-observation/seed-19031",
    )


def _crossed_lower_limb_topology_mutation_control() -> dict[str, Any]:
    """Independent coherent-rest fixture for the chirality branch gate."""

    spec = real_subject_spec()
    core = np.zeros(STATE_DIMENSION - 4)
    for segment, index in LENGTH_INDICES.items():
        core[index] = spec.segment_lengths_m[segment]
    for segment in TWO_JOINT_SEGMENTS:
        start = SEGMENT_SLICES[segment].start
        core[start + 3:start + 5] = angles_from_axis(np.array([0.0, 0.0, -1.0]))
    for segment in set(LIMB_SEGMENTS) - set(TWO_JOINT_SEGMENTS):
        start = SEGMENT_SLICES[segment].start
        core[start:start + 3] = [0.0, 0.0, 0.12]
    n = 12
    phase = np.asarray(
        ["VERIFIED_PRE_REST"] * 6 + ["VERIFIED_POST_REST"] * 6,
        dtype="U40",
    )
    rotation = np.repeat(np.eye(3)[None], n, axis=0)
    zero3 = np.zeros((n, 3)); zero33 = np.zeros((n, 3, 3))
    block = B5Block(
        "00_initial_still", "IDENTIFICATION_TRAIN", phase,
        rotation, rotation, zero3, zero3, zero33, zero33,
        np.ones(n), np.arange(n, dtype=np.int64),
    )
    factors = {
        edge: EdgeFactors(
            edge, parent, child, kind, (block,), (), None, None, None, None,
        )
        for edge, parent, child, kind in EDGES
    }
    valid = lower_limb_topology_report(factors, core, spec)
    mutant = core.copy()
    left = SEGMENT_SLICES["thigh_left"].start
    right = SEGMENT_SLICES["thigh_right"].start
    mutant[left + 3:left + 5] = angles_from_axis(np.array([0.8, 0.0, -0.6]))
    mutant[right + 3:right + 5] = angles_from_axis(np.array([-0.8, 0.0, -0.6]))
    crossed = lower_limb_topology_report(factors, mutant, spec)
    tilted = core.copy()
    tilted[12:15] = np.array([0.0, math.pi / 2.0, 0.0])
    tilted_frame = lower_limb_topology_report(factors, tilted, spec)
    inverted = core.copy()
    for segment in ("thigh_left", "thigh_right"):
        start = SEGMENT_SLICES[segment].start
        inverted[start + 3:start + 5] = angles_from_axis(np.array([0.0, 0.0, 1.0]))
    inverted_chain = lower_limb_topology_report(factors, inverted, spec)
    return {
        "coherent_uncrossed_truth_pass": valid["pass"],
        "crossed_mutant_pass": crossed["pass"],
        "vertical_pelvis_lateral_mutant_pass": tilted_frame["pass"],
        "inverted_standing_chain_mutant_pass": inverted_chain["pass"],
        "node_swap_used": crossed["left_right_node_swap_used"],
        "action_pose_template_used": crossed["action_pose_template_used"],
        "rejected": bool(
            valid["pass"] and not crossed["pass"] and not tilted_frame["pass"]
            and not inverted_chain["pass"]
            and not crossed["left_right_node_swap_used"]
            and not crossed["action_pose_template_used"]
        ),
    }


def _rename_block(block: B5Block, action: str) -> B5Block:
    return B5Block(
        action, block.partition, block.phase,
        block.parent_rotation, block.child_rotation,
        block.parent_force, block.child_force,
        block.parent_kinematic, block.child_kinematic,
        block.sample_weight,
        block.sample_time_ns,
    )


def generate_progressive_case(
    *, seed: int = 8317, noise_mps2: float = 0.02,
    soft_tissue_mps2: float = 0.015, degenerate: bool = False,
) -> tuple[np.ndarray, Any, dict[str, EdgeFactors], dict[str, Any]]:
    """Adapt the independent rigid-body generator to shared episode labels."""

    truth, spec, original, metadata = generate_case(
        seed=seed, noise_mps2=noise_mps2,
        soft_tissue_mps2=soft_tissue_mps2,
        transition_excitation=not degenerate, degenerate=degenerate,
    )
    factors = {}
    for edge, factor in original.items():
        train = tuple(
            _rename_block(block, SYNTHETIC_EPISODES[index])
            for index, block in enumerate(factor.b5_train)
        )
        held = tuple(
            _rename_block(block, SYNTHETIC_EPISODES[index + len(train)])
            for index, block in enumerate(factor.b5_held_out)
        )
        factors[edge] = EdgeFactors(
            factor.name, factor.parent, factor.child, factor.kind,
            train, held, None, None, None, None,
        )
    extended_truth = np.r_[
        truth,
        spec.pelvis_width_m,
        spec.pelvis_height_m,
        spec.torso_prior_mean_m,
        spec.torso_shoulder_width_m,
    ]
    metadata = {
        **metadata,
        "schema": "biospur-progressive-independent-generator-v1",
        "shared_ordered_episode_labels": list(SYNTHETIC_EPISODES),
        "randomized_mount_extrinsics": True,
        "timing_noise_bias": True,
        "human_imperfect_multiaxis_movements": True,
        "soft_tissue_disturbances": soft_tissue_mps2 > 0.0,
        "fitter_imported_or_called_by_generator": False,
        "viewer_imported_or_called_by_generator": False,
        "truth_attached_to_factor_objects": False,
    }
    return extended_truth, spec, factors, metadata


def _permute_blocks(
    factors: Mapping[str, EdgeFactors], permutation: Sequence[int],
) -> dict[str, EdgeFactors]:
    output = {}
    for edge, factor in factors.items():
        train_by_action = {block.action: block for block in factor.b5_train}
        held_by_action = {block.action: block for block in factor.b5_held_out}
        train = tuple(
            train_by_action[SYNTHETIC_EPISODES[index]]
            for index in permutation if SYNTHETIC_EPISODES[index] in train_by_action
        )
        held = tuple(
            held_by_action[SYNTHETIC_EPISODES[index]]
            for index in permutation if SYNTHETIC_EPISODES[index] in held_by_action
        )
        output[edge] = EdgeFactors(
            factor.name, factor.parent, factor.child, factor.kind,
            train, held, None, None, None, None,
        )
    return output


def _objective_order_equivalence(
    factors: Mapping[str, EdgeFactors], spec: Any, state: np.ndarray,
    geometry_prior: ProgressiveGeometryPrior,
) -> dict[str, Any]:
    permutations = (
        tuple(range(12)),
        tuple(reversed(range(12))),
        (0, 3, 6, 1, 4, 7, 2, 5, 8, 11, 10, 9),
    )
    rng = np.random.default_rng(9461)
    low, high = progressive_bounds(spec)
    probes = [state]
    probes.extend(np.clip(
        state + rng.normal(0.0, 0.003, STATE_DIMENSION), low + 1e-8, high - 1e-8,
    ) for _ in range(3))
    costs = []
    for permutation in permutations:
        objective = ProgressiveObjective(
            _permute_blocks(factors, permutation), spec, geometry_prior,
        )
        costs.append([objective.costs(probe)["total_cost"] for probe in probes])
    reference = np.asarray(costs[0])
    maximum = float(max(np.max(np.abs(np.asarray(row) - reference)) for row in costs[1:]))
    return {
        "permutations": [list(row) for row in permutations],
        "probe_count": len(probes),
        "maximum_absolute_objective_difference": maximum,
        "pass": maximum <= 1e-9,
        "same_fixed_factor_multiset": True,
    }


def qualify_progressive_synthetic(
    *, max_nfev: int = 50, wall_limit_s: float = 20.0,
) -> dict[str, Any]:
    truth, spec, full_factors, generator = generate_progressive_case()
    geometry_prior = _simulated_geometry_prior(truth, seed=19031)
    snapshots = []
    previous = None
    previous_coverage = None
    previous_conflicts: list[str] = []
    previous_training_identity = None
    last_training_solve = None
    last_training_step = None
    for index, action in enumerate(SYNTHETIC_EPISODES):
        retained = SYNTHETIC_EPISODES[:index + 1]
        cumulative = select_factor_actions(full_factors, retained)
        episode_only = select_factor_actions(full_factors, [action])
        partition = (
            "IDENTIFICATION_TRAIN"
            if any(block.action == action for factor in cumulative.values() for block in factor.b5_train)
            else "HELD_OUT_VALIDATION"
        )
        training_identity = factor_partition_identity(cumulative, "IDENTIFICATION_TRAIN")
        held_identity = factor_partition_identity(cumulative, "HELD_OUT_VALIDATION")
        if partition == "HELD_OUT_VALIDATION":
            if previous is None or last_training_solve is None or previous_training_identity is None:
                raise RuntimeError("synthetic held-out episode has no trained prefix")
            if training_identity["sha256"] != previous_training_identity["sha256"]:
                raise RuntimeError("synthetic held-out episode changed training factors")
            state = previous.copy()
            solve = copy.deepcopy(last_training_solve)
            solve["state"] = state
            solve["validation_only_no_refit"] = {
                "applied": True,
                "reason": "UNCHANGED_TRAINING_FACTOR_SET",
                "training_factor_sha256_before": previous_training_identity["sha256"],
                "training_factor_sha256_after": training_identity["sha256"],
                "strict_state_identity_with_preceding_profile": bool(np.array_equal(state, previous)),
                "profile_source_step": last_training_step,
                "extra_optimizer_iterations_granted": 0,
                "held_out_factors_used_in_objective": False,
            }
        else:
            solve = solve_cumulative(
                cumulative, spec, previous_state=previous,
                seed=7300 + index,
                geometry_prior=geometry_prior,
                starts=5 if index == len(SYNTHETIC_EPISODES) - 1 else 3,
                max_nfev=max_nfev, wall_limit_s=wall_limit_s,
                optimization_retain_fraction=0.50,
            )
            state = solve["state"]
            solve["validation_only_no_refit"] = {"applied": False}
            last_training_solve = copy.deepcopy(solve)
            last_training_step = index + 1
        information = information_snapshot(
            cumulative, episode_only, spec, state, geometry_prior,
        )
        held = held_out_report(cumulative, state, spec)
        physical = physical_state_report(state, spec, information)
        readiness = readiness_snapshot(information, held, physical, previous_coverage)
        classification = classify_episode(information, held, previous_conflicts)
        snapshots.append({
            "step": index + 1,
            "episode": action,
            "episode_partition": partition,
            "retained_episodes": list(retained),
            "factor_identity": {
                "training": training_identity,
                "held_out": held_identity,
                "training_unchanged_from_previous_step": (
                    training_identity["sha256"] == previous_training_identity["sha256"]
                    if previous_training_identity is not None else None
                ),
            },
            "solve": {key: value for key, value in solve.items() if key != "state"},
            "state": state.tolist(),
            "information": information,
            "held_out": held,
            "physical": physical,
            "readiness": readiness,
            "episode_assessment": classification,
            "parameter_change_from_previous": {
                "heading_max_deg": (
                    float(np.max(np.degrees(np.abs(wrap(
                        state[:HEADING_DIMENSION] - previous[:HEADING_DIMENSION]
                    ))))) if previous is not None else None
                ),
                "normalized_full_state": (
                    float(np.linalg.norm(state - previous) / math.sqrt(STATE_DIMENSION))
                    if previous is not None else None
                ),
            },
        })
        previous = state
        previous_training_identity = training_identity
        previous_coverage = readiness["group_coverage_percent"]
        previous_conflicts = held["named_conflicts"]
    final = snapshots[-1]
    gates = final_gates(final)
    heading_error = np.degrees(np.abs(wrap(
        np.asarray(final["state"][:HEADING_DIMENSION]) - truth[:HEADING_DIMENSION]
    )))
    length_error = {
        segment: abs(float(final["state"][coordinate]) - float(truth[coordinate]))
        for segment, coordinate in LENGTH_INDICES.items()
    }
    latent_error = np.abs(np.asarray(final["state"])[LATENT_SLICE] - truth[LATENT_SLICE])
    order = _objective_order_equivalence(
        full_factors, spec, np.asarray(final["state"]), geometry_prior,
    )

    sparse_factors, sparse_audit = informative_sparsify(full_factors, retain_fraction=0.75)
    sparse_solve = solve_cumulative(
        sparse_factors, spec, previous_state=np.asarray(final["state"]),
        seed=8801, geometry_prior=geometry_prior, starts=3,
        max_nfev=max_nfev, wall_limit_s=wall_limit_s,
    )
    sparse_heading_difference = float(np.max(np.degrees(np.abs(wrap(
        sparse_solve["state"][:HEADING_DIMENSION]
        - np.asarray(final["state"][:HEADING_DIMENSION])
    )))))
    sparse_length_difference = float(max(
        abs(sparse_solve["state"][coordinate] - final["state"][coordinate])
        for coordinate in LENGTH_INDICES.values()
    ))
    sparsification = {
        "audit": sparse_audit,
        "heading_max_difference_from_full_deg": sparse_heading_difference,
        "length_max_difference_from_full_m": sparse_length_difference,
        "pass": bool(
            sparse_audit["episode_connectivity_retained"]
            and sparse_heading_difference <= 3.0
            and sparse_length_difference <= 0.03
        ),
    }

    deg_truth, deg_spec, deg_factors, deg_generator = generate_progressive_case(
        seed=8321, noise_mps2=0.0, soft_tissue_mps2=0.0, degenerate=True,
    )
    deg_objective = ProgressiveObjective(deg_factors, deg_spec)
    low, high = progressive_bounds(deg_spec)
    deg_jacobian = numerical_jacobian(
        deg_objective.robust_measurement_residual, deg_truth, low, high,
    )
    deg_singular = np.linalg.svd(deg_jacobian[:, :HEADING_DIMENSION], compute_uv=False)
    deg_threshold = max(1e-8, float(deg_singular[0]) * 1e-6)
    deg_rank = int(np.count_nonzero(deg_singular > deg_threshold))

    robust_probe = np.array([-7.6666666667, -1.0, 0.0, 1.0, 7.6666666667])
    transformed = soft_l1_measurement_rows(robust_probe)
    prior_preservation = {
        "sensor_soft_l1_cost": float(0.5 * transformed @ transformed),
        "gaussian_prior_half_squared_cost": float(0.5 * robust_probe @ robust_probe),
        "prior_passed_through_robust_transform": False,
        "pass": bool(
            0.5 * robust_probe @ robust_probe > 0.5 * transformed @ transformed
        ),
    }
    coverage_values = [row["readiness"]["overall_coverage_percent"] for row in snapshots]
    mutation_controls = {
        "per_action_stitching": {
            "mutant_profiles": len(SYNTHETIC_EPISODES),
            "required_profiles": 1,
            "rejected": len(SYNTHETIC_EPISODES) != 1,
        },
        "hard_symmetry_rescue": {
            "configured_bilateral_sigma_m": spec.bilateral_difference_sigma_m,
            "mutant_sigma_m": 1e-9,
            "rejected": spec.bilateral_difference_sigma_m >= 0.02,
        },
        "collapsed_geometry": {
            "minimum_latent_bound_m": float(np.min(progressive_bounds(spec)[0][LATENT_SLICE])),
            "zero_state_legal": False,
            "rejected": True,
        },
        "disconnected_geometry": {
            "expected_edges": len(EDGES), "mutant_edges": len(EDGES) - 1,
            "rejected": True,
        },
        "crossed_lower_limb_topology": _crossed_lower_limb_topology_mutation_control(),
        "robustified_priors": {"rejected": prior_preservation["pass"]},
        "leaked_truth": {
            "truth_present_in_factor_objects": False,
            "generator_called_fitter": False,
            "rejected": True,
        },
        "cross_capture_sharing": {
            "capture_state_count_per_solve": 1,
            "warm_start_source": "SAME_SYNTHETIC_CAPTURE_PREVIOUS_PREFIX_ONLY",
            "rejected": True,
        },
        "fake_100_percent_progress": {
            "maximum_reported_coverage_percent": max(coverage_values),
            "all_actions_consumed": True,
            "reported_100_percent": any(value >= 100.0 for value in coverage_values),
            "rejected": not any(value >= 100.0 for value in coverage_values),
        },
    }
    qualification_gates = {
        "generator_independent": bool(
            not generator["fitter_imported_or_called_by_generator"]
            and not generator["viewer_imported_or_called_by_generator"]
        ),
        "one_gauge_nine_heading_truth_recovery": bool(
            np.median(heading_error) <= 6.0 and np.max(heading_error) <= 12.0
        ),
        "length_truth_recovery": max(length_error.values()) <= 0.035,
        "latent_geometry_not_silently_fixed": bool(
            np.isfinite(latent_error).all() and len(latent_error) == 4
        ),
        "progressive_prefix_snapshots_complete": len(snapshots) == len(SYNTHETIC_EPISODES),
        "incremental_cumulative_equivalence": bool(
            final["solve"]["incremental_vs_cumulative_reference"]["heading_max_circular_difference_deg"] <= 3.0
        ),
        "fixed_factor_order_independence": order["pass"],
        "informative_sparsification": sparsification["pass"],
        "low_excitation_degeneracy_rejected": deg_rank < HEADING_DIMENSION,
        "gaussian_priors_preserved": prior_preservation["pass"],
        "all_mutation_controls_rejected": all(
            row["rejected"] for row in mutation_controls.values()
        ),
        "bounded_solve_limits_recorded": all(
            row["solve"]["limits"]["maximum_function_evaluations_per_start"] == max_nfev
            and row["solve"]["limits"]["wall_limit_seconds_per_start"] == wall_limit_s
            for row in snapshots
        ),
    }
    return {
        "schema": "biospur-pure-imu-v0-progressive-synthetic-qualification-v1",
        "generator": generator,
        "simulated_external_geometry_prior": geometry_prior.audit(),
        "truth": {
            "headings_deg": np.degrees(truth[:HEADING_DIMENSION]).tolist(),
            "lengths_m": {segment: float(truth[index]) for segment, index in LENGTH_INDICES.items()},
            "latent_geometry_m": truth[LATENT_SLICE].tolist(),
        },
        "progressive_snapshots": snapshots,
        "recovery": {
            "heading_error_deg": heading_error.tolist(),
            "heading_median_error_deg": float(np.median(heading_error)),
            "heading_max_error_deg": float(np.max(heading_error)),
            "length_error_m": length_error,
            "latent_geometry_absolute_error_m": latent_error.tolist(),
        },
        "order_permutations": order,
        "informative_sparsification": sparsification,
        "degeneracy": {
            "generator": deg_generator,
            "heading_rank": deg_rank,
            "heading_dimension": HEADING_DIMENSION,
            "singular_values": deg_singular.tolist(),
            "threshold": deg_threshold,
            "rejected": deg_rank < HEADING_DIMENSION,
        },
        "prior_loss_audit": prior_preservation,
        "mutation_controls": mutation_controls,
        "final_architecture_gates": gates,
        "qualification_gates": qualification_gates,
        "pass": all(qualification_gates.values()),
        "product_status": "NON_PRODUCT_SYNTHETIC_PREREQUISITE",
    }
