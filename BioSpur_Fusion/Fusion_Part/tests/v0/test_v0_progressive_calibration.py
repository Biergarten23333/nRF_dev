from __future__ import annotations

import numpy as np
from scipy.spatial.transform import Rotation

import biospur_fusion.v0.progressive_calibration as progressive

from biospur_fusion.v0.progressive_calibration import (
    LATENT_SLICE,
    PARAMETER_GROUPS,
    REAL_SUBJECT_PROGRESSIVE_GEOMETRY_PRIOR,
    ProgressiveObjective,
    STATE_DIMENSION,
    factor_partition_identity,
    informative_sparsify,
    information_snapshot,
    physical_state_report,
    progressive_bounds,
    select_factor_actions,
    solve_cumulative,
    soft_l1_measurement_rows,
    lower_limb_topology_report,
    lower_limb_topology_residual,
)
from biospur_fusion.v0.physical_graph import (
    LENGTH_INDICES,
    LIMB_SEGMENTS,
    SEGMENT_SLICES,
    TWO_JOINT_SEGMENTS,
    real_subject_spec,
)
from biospur_fusion.v0.raw6_heading import B5Block, EDGES, EdgeFactors
from biospur_fusion.imu_multi_action_revision_d.d0b_r1_model import angles_from_axis
from biospur_fusion.v0.progressive_synthetic import (
    SYNTHETIC_EPISODES,
    generate_progressive_case,
)


def test_gaussian_prior_is_not_robustified_with_sensor_rows() -> None:
    values = np.array([-7.6666666667, -1.0, 0.0, 1.0, 7.6666666667])
    transformed = soft_l1_measurement_rows(values)
    assert np.isclose(
        0.5 * transformed @ transformed,
        np.sum(np.sqrt(1.0 + values * values) - 1.0),
    )
    assert 0.5 * values @ values > 0.5 * transformed @ transformed


def test_progressive_state_has_one_gauge_nine_headings_and_unprioritized_latents() -> None:
    truth, spec, factors, _ = generate_progressive_case()
    objective = ProgressiveObjective(factors, spec)
    assert STATE_DIMENSION == len(truth)
    assert len(PARAMETER_GROUPS["relative_headings"]) == 9
    assert objective.prior_residual(truth).shape[0] > 0
    low, high = progressive_bounds(spec)
    assert np.all(low[LATENT_SLICE] < truth[LATENT_SLICE])
    assert np.all(truth[LATENT_SLICE] < high[LATENT_SLICE])


def test_external_geometry_prior_is_exact_gaussian_and_auditable() -> None:
    truth, spec, factors, _ = generate_progressive_case()
    base = ProgressiveObjective(factors, spec)
    objective = ProgressiveObjective(
        factors, spec, REAL_SUBJECT_PROGRESSIVE_GEOMETRY_PRIOR,
    )
    state = truth.copy()
    state[LATENT_SLICE] = REAL_SUBJECT_PROGRESSIVE_GEOMETRY_PRIOR.mean_m
    base_rows = base.prior_residual(state)
    prior_rows = objective.prior_residual(state)
    assert prior_rows.shape[0] == base_rows.shape[0] + 4
    assert np.allclose(prior_rows[-4:], 0.0)
    state[LATENT_SLICE.start] += REAL_SUBJECT_PROGRESSIVE_GEOMETRY_PRIOR.sigma_m[0]
    shifted = objective.prior_residual(state)
    assert np.isclose(shifted[-4], 1.0)
    audit = REAL_SUBJECT_PROGRESSIVE_GEOMETRY_PRIOR.audit()
    assert audit["loss_class"] == "EXACT_GAUSSIAN_OUTSIDE_ROBUST_SENSOR_LOSS"
    assert audit["surface_measurements_relabelled_as_internal_truth"] is False


def test_prefix_selection_retains_exact_earlier_factor_blocks() -> None:
    _, _, factors, _ = generate_progressive_case()
    first = select_factor_actions(factors, SYNTHETIC_EPISODES[:3])
    second = select_factor_actions(factors, SYNTHETIC_EPISODES[:4])
    for edge in first:
        assert first[edge].b5_train == second[edge].b5_train[:len(first[edge].b5_train)]
        assert len(second[edge].b5_train) == len(first[edge].b5_train) + 1


def test_informative_sparsification_keeps_every_episode_phase_connectivity() -> None:
    _, _, factors, _ = generate_progressive_case()
    sparse, audit = informative_sparsify(factors, retain_fraction=0.55)
    assert audit["episode_connectivity_retained"] is True
    for edge in factors:
        assert len(sparse[edge].b5_train) == len(factors[edge].b5_train)
        assert len(sparse[edge].b5_held_out) == len(factors[edge].b5_held_out)
        assert all(
            len(sparse_block.phase) <= len(full_block.phase)
            for sparse_block, full_block in zip(sparse[edge].b5_train, factors[edge].b5_train)
        )


def test_failed_sparse_initializers_continue_on_full_cumulative_objective(
    monkeypatch,
) -> None:
    truth, spec, factors, _ = generate_progressive_case()
    prefix = select_factor_actions(factors, SYNTHETIC_EPISODES[:2])
    calls = []

    def fake_solve(objective, x0, low, high, sparsity, *, label, **limits):
        calls.append(label)
        finite = label == "FULL_CUMULATIVE_BATCH_REFERENCE"
        return {
            "label": label,
            "state": np.asarray(x0),
            "costs": objective.costs(np.asarray(x0)),
            "nfev": 1,
            "optimality": 0.0,
            "success": finite,
            "finite": finite,
            "trace": [],
            "message": "forced test outcome",
            "wall_seconds": 0.0,
        }

    monkeypatch.setattr(progressive, "_solve_once", fake_solve)
    result = solve_cumulative(
        prefix, spec, previous_state=truth, seed=1, starts=3,
        max_nfev=2, wall_limit_s=1.0, optimization_retain_fraction=0.5,
    )
    assert calls[-1] == "FULL_CUMULATIVE_BATCH_REFERENCE"
    assert result["all_sparse_initializers_failed"] is True
    assert result["continued_on_full_cumulative_objective_after_sparse_failure"] is True
    assert result["finite_start_count"] == 0


def test_adding_held_out_episode_preserves_exact_training_factor_identity() -> None:
    _, _, factors, _ = generate_progressive_case()
    train_prefix = select_factor_actions(factors, SYNTHETIC_EPISODES[:8])
    with_first_validation = select_factor_actions(factors, SYNTHETIC_EPISODES[:9])
    before = factor_partition_identity(train_prefix, "IDENTIFICATION_TRAIN")
    after = factor_partition_identity(with_first_validation, "IDENTIFICATION_TRAIN")
    held_before = factor_partition_identity(train_prefix, "HELD_OUT_VALIDATION")
    held_after = factor_partition_identity(with_first_validation, "HELD_OUT_VALIDATION")
    assert before["sha256"] == after["sha256"]
    assert held_before["sha256"] != held_after["sha256"]


def test_boundary_active_latent_geometry_is_not_promoted() -> None:
    truth, spec, factors, _ = generate_progressive_case()
    state = truth.copy()
    state[LATENT_SLICE] = progressive.LATENT_LOWER
    info = information_snapshot(factors, factors, spec, state)
    physical = physical_state_report(state, spec, info)
    assert physical["latent_geometry_data_only_observable"] is False
    assert any(
        row["active_lower_bound"]
        for row in physical["latent_geometry_coordinate_audit"].values()
    )


def _coherent_rest_topology_fixture() -> tuple[np.ndarray, object, dict[str, EdgeFactors]]:
    spec = real_subject_spec()
    state = np.zeros(progressive.CORE_STATE_DIMENSION)
    for segment, index in LENGTH_INDICES.items():
        state[index] = spec.segment_lengths_m[segment]
    down = np.array([0.0, 0.0, -1.0])
    for segment in TWO_JOINT_SEGMENTS:
        start = SEGMENT_SLICES[segment].start
        state[start + 3:start + 5] = angles_from_axis(down)
    for segment in set(LIMB_SEGMENTS) - set(TWO_JOINT_SEGMENTS):
        start = SEGMENT_SLICES[segment].start
        state[start:start + 3] = [0.0, 0.0, 0.12]

    n = 12
    phase = np.asarray(
        ["VERIFIED_PRE_REST"] * 6 + ["VERIFIED_POST_REST"] * 6,
        dtype="U40",
    )
    rotation = np.repeat(np.eye(3)[None], n, axis=0)
    zero3 = np.zeros((n, 3))
    zero33 = np.zeros((n, 3, 3))
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
    return state, spec, factors


def test_verified_rest_topology_rejects_crossed_leg_branch_without_node_swap() -> None:
    state, spec, factors = _coherent_rest_topology_fixture()
    accepted = lower_limb_topology_report(factors, state, spec)
    assert accepted["applicable"] is True
    assert accepted["pass"] is True

    crossed = state.copy()
    left = SEGMENT_SLICES["thigh_left"].start
    right = SEGMENT_SLICES["thigh_right"].start
    crossed[left + 3:left + 5] = angles_from_axis(np.array([0.8, 0.0, -0.6]))
    crossed[right + 3:right + 5] = angles_from_axis(np.array([-0.8, 0.0, -0.6]))
    rejected = lower_limb_topology_report(factors, crossed, spec)
    assert rejected["pass"] is False
    assert rejected["left_right_node_swap_used"] is False
    assert np.max(lower_limb_topology_residual(factors, crossed, spec)) > 0.0


def test_natural_standing_rejects_vertical_pelvis_lateral_axis() -> None:
    state, spec, factors = _coherent_rest_topology_fixture()
    tilted = state.copy()
    tilted[12:15] = Rotation.from_euler("y", 90.0, degrees=True).as_rotvec()
    rejected = lower_limb_topology_report(factors, tilted, spec)
    assert rejected["pass"] is False
    assert any(
        row["standing_frame_applicable"] and not row["standing_frame_pass"]
        for row in rejected["rows"]
    )
    assert np.max(lower_limb_topology_residual(factors, tilted, spec)) > 0.0


def test_natural_standing_rejects_inverted_thigh_chain() -> None:
    state, spec, factors = _coherent_rest_topology_fixture()
    inverted = state.copy()
    for segment in ("thigh_left", "thigh_right"):
        start = SEGMENT_SLICES[segment].start
        inverted[start + 3:start + 5] = angles_from_axis(np.array([0.0, 0.0, 1.0]))
    rejected = lower_limb_topology_report(factors, inverted, spec)
    assert rejected["pass"] is False
    assert any(
        row["standing_frame_applicable"] and not row["standing_vertical_pass"]
        for row in rejected["rows"]
    )
    assert np.max(lower_limb_topology_residual(factors, inverted, spec)) > 0.0
