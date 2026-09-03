from copy import deepcopy
from dataclasses import replace
import itertools
from pathlib import Path
import time
import warnings

import numpy as np
import pytest
from scipy.spatial.transform import Rotation

from biospur_fusion.v0.c2_basis.contracts import C2_IDENTITY, load_config
from biospur_fusion.v0.c2_basis.factors import (
    append_factor_bundle,
    build_factor_bundle,
    factor_identity,
    fixed_geometry_noise_lever_bounds,
    propagated_joint_noise_sigma,
)
from biospur_fusion.v0.c2_basis import functional as functional_module
from biospur_fusion.v0.c2_basis import solver as solver_module
from biospur_fusion.v0.c2_basis.functional import FunctionalCandidate, HingeAxisEstimate
from biospur_fusion.v0.c2_basis.geometry import load_body_geometry
from biospur_fusion.v0.c2_basis.model import (
    CalibrationState,
    C2CalibrationObjective,
    summarize_held_out_covariance_blocks,
)
from biospur_fusion.v0.c2_basis.progressive import ProgressiveProfile
from biospur_fusion.v0.c2_basis.raw_frontend import estimate_stillness
from biospur_fusion.v0.c2_basis.synthetic_truth import generate_synthetic_truth
from biospur_fusion.v0.c2_basis.validation import (
    validate_basis_contract,
    validate_independent_synthetic_truth,
    validate_progressive_snapshot,
)
from biospur_fusion.v0.c2_basis.staging import BoundedStage, StageDeadlineExceeded
from biospur_fusion.v0.c2_basis.contracts import StageBudget
from biospur_fusion.v0.c2_basis.mounts import candidate_bank, wear_direction_report
from biospur_fusion.v0.raw6_heading import (
    b5_blocks,
    estimate_hinge_axes_qmt,
    qmt_edge_heading,
    qmt_selected_cross_denominator_report,
)
from biospur_fusion.v0.contracts import NODES
from biospur_fusion.v0.episode import PHASES, segment_five_phase_episode


ROOT = Path(__file__).resolve().parents[2]


def _stationary_reference_rows() -> dict[str, np.ndarray]:
    dtype = np.dtype([
        ("global_time_ns", "<i8"), ("status", "u1"),
        ("acc_raw", "<i2", (3,)), ("gyro_raw", "<i2", (3,)),
    ])
    times = np.arange(0, 12_000_000_000, 10_000_000, dtype=np.int64)
    result = {}
    for node in NODES:
        rows = np.zeros(len(times), dtype=dtype)
        rows["global_time_ns"] = times
        rows["status"] = 1
        rows["acc_raw"][:, 2] = 2048
        result[node] = rows
    return result


def _truth_functional(truth):
    parents = {
        "elbow_left": "upper_arm_left", "elbow_right": "upper_arm_right",
        "knee_left": "thigh_left", "knee_right": "thigh_right",
    }
    children = {
        "elbow_left": "forearm_left", "elbow_right": "forearm_right",
        "knee_left": "shank_left", "knee_right": "shank_right",
    }
    axes = {
        edge: HingeAxisEstimate(
            edge, parents[edge], children[edge], left, right,
            {
                "axis_uncertainty_sigma_rad": np.deg2rad(10.0),
                "parent_selected_excitation_qualified": True,
                "child_selected_excitation_qualified": True,
            },
            {},
        )
        for edge, (left, right) in truth.hinge_axes_sensor.items()
    }
    return FunctionalCandidate(
        "independent_truth_mount", "independent_truth_mount",
        {edge: (1, 1) for edge in axes}, truth.body_from_sensor, axes,
        {"hard_pass": True}, {}, {}, 1.0,
    )


def test_covariance_blocks_have_explicit_information_and_no_episode_row_normalization():
    truth = generate_synthetic_truth(n=200)
    bundle = build_factor_bundle(truth.episodes[:2], load_config(ROOT))
    assert bundle.covariance_information_audit
    for row in bundle.covariance_information_audit:
        assert 1.0 <= row["effective_sample_size"] <= row["rows"]
        assert 0.05 <= row["joint_center_sigma_mps2"] <= 2.0
        assert row["per_sample_information_weight"] > 0.0
        assert row["parent_kinematic_noise_lever_bound_m"] > 0.0
        assert row["child_kinematic_noise_lever_bound_m"] > 0.0
        assert row["noise_propagation"]["uses_dynamic_residual_or_jerk_as_noise"] is False
        assert "SENSOR_NOISE" in row["parent_noise_cov_source"]
    for edge in bundle.edges.values():
        assert all(block.covariance_block_id for block in (*edge.train, *edge.held_out))


def test_c2_stationary_reference_has_nonempty_temporal_transitions_without_motion_claim():
    config = load_config(ROOT)
    report = segment_five_phase_episode(
        _stationary_reference_rows(),
        action="synthetic_initial_still",
        action_kind="STATIONARY_REFERENCE",
        formal_start_global_ns=4_000_000_000,
        formal_stop_global_ns_exclusive=8_000_000_000,
        contract=config["episode_segmentation"],
        boundary_authority={"source": "SYNTHETIC_EXACT_COMPLETE_EPISODE"},
    )
    assert report["EPISODE_COMPLETENESS"] == "PASS"
    assert tuple(row["phase"] for row in report["phases"]) == PHASES
    assert all(row["duration_s"] > 0.0 for row in report["phases"])
    assert report["phases"][1]["duration_s"] >= 0.1
    assert report["phases"][3]["duration_s"] >= 0.1
    assert report["stationary_transition_semantics"] == (
        "TIME_PARTITION_ONLY_NO_MOTION_OR_POSE_CLAIM"
    )
    assert report["first_activity_global_time_ns"] is None
    assert report["last_activity_global_time_ns_exclusive"] is None


def test_negative_contract_mutations_fail_closed():
    config = load_config(ROOT)
    geometry = load_body_geometry(ROOT)
    assert validate_basis_contract(config, geometry)["pass"]
    robustified = deepcopy(config)
    robustified["hard_feasibility"]["physical_gate_is_optimizer_loss"] = True
    with pytest.raises(ValueError, match="robustified"):
        validate_basis_contract(robustified, geometry)
    zero_stationary_transition = deepcopy(config)
    zero_stationary_transition["episode_segmentation"][
        "minimum_stationary_transition_duration_s"
    ] = 0.0
    with pytest.raises(ValueError, match="stationary transitions"):
        validate_basis_contract(zero_stationary_transition, geometry)
    oversubscribed = deepcopy(config)
    oversubscribed["stages"]["FRESH_CUMULATIVE_BATCH"]["parallel_workers"] = 16
    with pytest.raises(ValueError, match="worker contract"):
        validate_basis_contract(oversubscribed, geometry)
    relaxed_wall = deepcopy(config)
    relaxed_wall["stages"]["PROGRESSIVE_PREFIX_UPDATE"]["wall_limit_s"] = 26.0
    with pytest.raises(ValueError, match="wall limits were relaxed"):
        validate_basis_contract(relaxed_wall, geometry)
    missing_pipeline_stage = deepcopy(config)
    del missing_pipeline_stage["bounded_pipeline"]["wall_limits_s"]["PROGRESSIVE"][
        "held_out_evaluation"
    ]
    with pytest.raises(ValueError, match="incomplete bounded pipeline"):
        validate_basis_contract(missing_pipeline_stage, geometry)
    leaked = generate_synthetic_truth(n=200)
    episode = replace(
        leaked.episodes[0], audit={**leaked.episodes[0].audit, "estimator_module_imported": True},
    )
    with pytest.raises(ValueError, match="leaked"):
        validate_independent_synthetic_truth(replace(leaked, episodes=(episode, *leaked.episodes[1:])))


def test_progressive_mutations_reject_stitching_fake_progress_and_initial_complete():
    base = {
        "single_persistent_profile": True,
        "per_action_profile_count": 0,
        "retained_episodes": ["00_initial_still"],
        "raw_row_count_used_as_progress": False,
        "action_count_used_as_readiness": False,
        "initial_still_complete_calibration": False,
        "step": 1,
        "ready": False,
    }
    assert validate_progressive_snapshot(base)["pass"]
    for key, value, match in (
        ("per_action_profile_count", 1, "stitching"),
        ("raw_row_count_used_as_progress", True, "raw row"),
        ("action_count_used_as_readiness", True, "action count"),
        ("initial_still_complete_calibration", True, "initial still"),
        ("ready", True, "initial-still"),
    ):
        mutated = {**base, key: value}
        with pytest.raises(ValueError, match=match):
            validate_progressive_snapshot(mutated)


def test_factor_and_held_out_stages_cancel_past_shared_deadline():
    truth = generate_synthetic_truth(n=200)
    config = load_config(ROOT)
    expired_factor = BoundedStage("FACTOR_CONSTRUCTION", 0.001)
    time.sleep(0.003)
    with pytest.raises(StageDeadlineExceeded, match="work cancelled"):
        build_factor_bundle(
            truth.episodes[:1], config, deadline=expired_factor,
        )
    bundle = build_factor_bundle(truth.episodes[:2], config)
    objective = C2CalibrationObjective(
        bundle, load_body_geometry(ROOT), _truth_functional(truth),
    )
    expired_held = BoundedStage("HELD_OUT_EVALUATION", 0.001)
    time.sleep(0.003)
    with pytest.raises(StageDeadlineExceeded, match="work cancelled"):
        objective.held_out_report(
            np.r_[truth.headings_rad, truth.axial_offsets_m], 2.5,
            deadline=expired_held,
        )
    assert expired_factor.cancelled
    assert expired_held.cancelled


def test_incremental_factors_equal_fresh_prefix_without_future_episode_access():
    truth = generate_synthetic_truth(n=200)
    config = load_config(ROOT)
    full = build_factor_bundle(truth.episodes[:3], config)
    incremental = None
    for episode in truth.episodes[:3]:
        incremental = append_factor_bundle(
            incremental, build_factor_bundle((episode,), config),
        )
    assert incremental is not None
    assert incremental.episode_order == full.episode_order
    assert factor_identity(incremental, "train") == factor_identity(full, "train")
    assert factor_identity(incremental, "held_out") == factor_identity(full, "held_out")
    assert incremental.construction_audit["future_episode_access"] is False


def test_vectorized_b5_kinematics_are_exact_scalar_cross_product_algebra():
    truth = generate_synthetic_truth(n=240)
    episode = truth.episodes[0]
    block = b5_blocks(
        "knee_left", (episode,), include_transitions=True, maximum_rows=None,
    )[0]
    selected_time = set(int(value) for value in block.sample_time_ns)
    rows = np.asarray([
        index for index, value in enumerate(episode.time_ns)
        if int(value) in selected_time
    ], dtype=int)
    dt = 1.0 / 50.0

    def scalar_skew(value):
        x, y, z = np.asarray(value, dtype=float)
        return np.array([[0.0, -z, y], [z, 0.0, -x], [-y, x, 0.0]])

    def scalar_kinematics(segment):
        gyro = episode.gyro[segment]
        alpha = np.gradient(gyro, dt, axis=0, edge_order=2)
        return np.asarray([
            scalar_skew(alpha[index])
            + scalar_skew(gyro[index]) @ scalar_skew(gyro[index])
            for index in rows
        ])

    assert np.array_equal(
        block.parent_kinematic, scalar_kinematics("thigh_left"),
    )
    assert np.array_equal(
        block.child_kinematic, scalar_kinematics("shank_left"),
    )


def test_unrelated_new_episode_reuses_hinge_axis_instead_of_repeating_qmt(monkeypatch):
    truth = generate_synthetic_truth(n=200)
    by_action = {episode.action: episode for episode in truth.episodes}
    calls = []

    def fake_axis(edge, episodes, **kwargs):
        calls.append((edge, tuple(episode.action for episode in episodes)))
        report = {
            "predeclared_relevant_actions": [episode.action for episode in episodes],
            "multistart_parent_axis_max_spread_deg": 0.0,
            "input_degeneracy": {"named_numerical_physical_conflicts": []},
        }
        return np.array([0.0, 1.0, 0.0]), np.array([0.0, 1.0, 0.0]), report

    monkeypatch.setattr(functional_module, "estimate_hinge_axes_qmt", fake_axis)
    monkeypatch.setattr(
        functional_module, "qmt_edge_heading", lambda *args, **kwargs: {"pass": True},
    )
    first = functional_module.estimate_hinge_baselines(
        (by_action["06_elbow_left"],), load_config(ROOT),
    )
    second = functional_module.estimate_hinge_baselines(
        (by_action["06_elbow_left"], by_action["17_final_still"]),
        load_config(ROOT), previous=first,
    )
    assert calls == [("elbow_left", ("06_elbow_left",))]
    assert second["elbow_left"].qmt_axis_report["incremental_cache"]["reused"] is True


def test_qmt_heading_correction_runs_only_predeclared_edge_actions(monkeypatch):
    truth = generate_synthetic_truth(n=300)
    by_action = {episode.action: episode for episode in truth.episodes}
    calls = []

    def fake_heading(parent_gyro, child_gyro, *args, **kwargs):
        calls.append(len(parent_gyro))
        n = len(parent_gyro)
        return None, np.zeros(n), np.zeros(n), np.ones(n), np.zeros(n, dtype=int)

    monkeypatch.setattr(
        "biospur_fusion.v0.raw6_heading.qmt.headingCorrection", fake_heading,
    )
    report = qmt_edge_heading(
        "elbow_left",
        (by_action["06_elbow_left"], by_action["17_final_still"]),
        np.array([0.0, 1.0, 0.0]),
        np.array([0.0, 1.0, 0.0]),
        intended_actions={"06_elbow_left"},
        axis_multistart_spread_deg=0.0,
        bounded_call=BoundedStage("QMT_HEADING", 2.0).run,
    )
    assert calls == [300]
    assert report["successful_action_count"] == 1
    assert report["action_estimate_spread_semantics"] == (
        "PREDECLARED_EDGE_ACTIONS_ONLY_NO_UNRELATED_ACTION_DIAGNOSTIC"
    )


def test_cached_functional_endpoint_rotations_are_algebraically_identical():
    truth = generate_synthetic_truth(n=200)
    functional = _truth_functional(truth)
    hinge_axes = {
        name: functional.hinge_axes[name]
        for name in ("elbow_left", "elbow_right")
    }
    branch = next(row for row in candidate_bank() if row.metadata_gate["hard_pass"])
    optimized = functional_module.functional_candidates(
        branch, hinge_axes, C2_IDENTITY,
        maximum_candidates=6,
    )
    node_to_segment = C2_IDENTITY
    reference = []
    sign_keys = tuple(
        (edge, endpoint) for edge in hinge_axes for endpoint in ("parent", "child")
    )
    for signs, fraction in itertools.product(
        itertools.product((-1, 1), repeat=len(sign_keys)), (0.0, 0.5, 1.0),
    ):
        endpoint_sign = dict(zip(sign_keys, signs))
        sign_map = {
            edge: (endpoint_sign[(edge, "parent")], endpoint_sign[(edge, "child")])
            for edge in hinge_axes
        }
        transforms = {
            segment: np.asarray(branch.body_from_sensor[node]).copy()
            for node, segment in node_to_segment.items()
        }
        corrections = {}
        remaining = {}
        for edge, estimate in hinge_axes.items():
            for endpoint, segment, axis in (
                ("parent", estimate.parent, estimate.parent_axis_sensor),
                ("child", estimate.child, estimate.child_axis_sensor),
            ):
                target = np.array([0.0, float(endpoint_sign[(edge, endpoint)]), 0.0])
                full = functional_module._minimal_alignment(transforms[segment] @ axis, target)
                full_rotvec = Rotation.from_matrix(full).as_rotvec()
                correction = Rotation.from_rotvec(float(fraction) * full_rotvec).as_matrix()
                transforms[segment] = correction @ transforms[segment]
                corrections[segment] = float(np.degrees(np.linalg.norm(full_rotvec)) * fraction)
                remaining[segment] = float(np.degrees(np.arccos(np.clip(
                    (transforms[segment] @ axis) @ target, -1.0, 1.0,
                ))))
        wear = wear_direction_report({
            node: transforms[segment] for node, segment in node_to_segment.items()
        })
        reference.append(FunctionalCandidate(
            branch.branch_id + f"_ALIGN{fraction:.1f}_" + "_".join(
                f"{edge}:p{'+' if sign_map[edge][0] > 0 else '-'}c{'+' if sign_map[edge][1] > 0 else '-'}"
                for edge in hinge_axes
            ),
            branch.branch_id, sign_map, transforms, hinge_axes, wear,
            corrections, remaining, float(fraction),
        ))
    reference.sort(key=lambda row: (
        not row.wear_report["hard_pass"],
        max(row.remaining_axis_misalignment_deg.values(), default=0.0),
        row.wear_report["soft_cost"],
        max(row.correction_angles_deg.values(), default=0.0),
    ))
    for actual, expected in zip(optimized, reference[:6]):
        assert actual.candidate_id == expected.candidate_id
        assert actual.wear_report == expected.wear_report
        assert actual.correction_angles_deg == pytest.approx(expected.correction_angles_deg)
        assert actual.remaining_axis_misalignment_deg == pytest.approx(
            expected.remaining_axis_misalignment_deg,
        )
        for segment in actual.body_from_sensor_by_segment:
            assert np.allclose(
                actual.body_from_sensor_by_segment[segment],
                expected.body_from_sensor_by_segment[segment], rtol=0.0, atol=1e-14,
            )


def test_hinge_soft_l1_score_is_axis_sign_invariant_at_fixed_mount():
    truth = generate_synthetic_truth(n=200)
    config = load_config(ROOT)
    functional = _truth_functional(truth)
    flipped_signs = dict(functional.axis_signs)
    parent_sign, child_sign = flipped_signs["knee_left"]
    flipped_signs["knee_left"] = (-parent_sign, child_sign)
    flipped = replace(functional, axis_signs=flipped_signs)
    bundle = build_factor_bundle(truth.episodes[:3], config)
    state = np.r_[truth.headings_rad, truth.axial_offsets_m]
    base_rows = C2CalibrationObjective(
        bundle, load_body_geometry(ROOT), functional,
    ).measurement_residual(state, "train")
    flipped_rows = C2CalibrationObjective(
        bundle, load_body_geometry(ROOT), flipped,
    ).measurement_residual(state, "train")
    base_score = np.sum(np.sqrt(1.0 + base_rows * base_rows) - 1.0)
    flipped_score = np.sum(np.sqrt(1.0 + flipped_rows * flipped_rows) - 1.0)
    assert base_score == pytest.approx(flipped_score, rel=0.0, abs=1e-12)
    assert not np.array_equal(base_rows, flipped_rows)


def test_qmt_selected_rows_are_mapped_and_isolated_parallel_row_does_not_gate(monkeypatch):
    truth = generate_synthetic_truth(n=200)
    source = next(
        episode for episode in truth.episodes
        if episode.action == "10_knee_left_seated"
    )
    gyro = {name: rows.copy() for name, rows in source.gyro.items()}
    gyro["shank_left"][0] = 0.0
    episode = replace(source, gyro=gyro)

    def fake_qmt(acc1, acc2, gyr1, gyr2, *, estSettings, debug):
        warnings.warn("invalid value encountered in divide", RuntimeWarning)
        sample_count = min(int(estSettings["dataSize"]), len(gyr1))
        report = {
            "optimVarsAxis": {"ftraj": np.array([2.0, 1.0])},
            "sampleSelectionVars": {
                "gyrSamples": np.arange(sample_count)[:, None],
                "accSamples": np.arange(sample_count)[:, None],
            },
        }
        return np.array([0.0, 1.0, 0.0]), np.array([0.0, 1.0, 0.0]), report

    monkeypatch.setattr(
        "biospur_fusion.v0.raw6_heading.qmt.jointAxisEstHingeOlsson", fake_qmt,
    )
    config = load_config(ROOT)
    _, _, report = estimate_hinge_axes_qmt(
        "knee_left",
        (episode,),
        maximum_samples=600,
        qmt_settings=config["bounded_pipeline"]["qmt_hinge_axis"],
        bounded_call=BoundedStage("FUNCTIONAL", 2.0).run,
    )
    degeneracy = report["input_degeneracy"]
    assert degeneracy["complete_episodes_retained_in_other_factors"] is True
    assert any("RUNTIME_NUMERICAL_WARNING" in row for row in degeneracy[
        "named_numerical_physical_conflicts"
    ])
    assert not any("MATERIAL_SELECTED" in row for row in degeneracy[
        "named_numerical_physical_conflicts"
    ])
    affected = degeneracy["endpoints"]["child"][
        "affected_preselection_600_rows_diagnostic_only"
    ]
    assert affected[0]["preselection_600_index"] == 0
    assert affected[0]["gyro_rad_s"] == [0.0, 0.0, 0.0]
    selected = degeneracy["qmt_selected_distribution_by_start"][0]
    assert selected["qmt_selected_gyro_source_rows"][0][
        "preselection_600_index"
    ] == 0
    child = selected["selected_denominator_audit"]["child"]
    assert child["qmt_selected_300_affected_count"] == 1
    assert child["distribution"]["material_selected_degeneracy"] is False
    assert child["historical_few_exact_rows_causal_claim"] == (
        "INVALID_POST_HOC_DESCRIPTIVE_ONLY"
    )
    assert report["qmt_official_sample_selection"]["enabled"] is True
    assert all(row["runtime_warnings"] for row in report["multistart"])


def test_preselection_exact_sentinel_not_selected_is_not_misattributed(monkeypatch):
    truth = generate_synthetic_truth(n=200)
    source = next(
        episode for episode in truth.episodes
        if episode.action == "10_knee_left_seated"
    )
    gyro = {name: rows.copy() for name, rows in source.gyro.items()}
    gyro["shank_left"][0] = 0.0
    episode = replace(source, gyro=gyro)

    def fake_qmt(acc1, acc2, gyr1, gyr2, *, estSettings, debug):
        stop = min(len(gyr1), int(estSettings["dataSize"]) + 1)
        selected = np.arange(1, stop, dtype=int)[:, None]
        report = {
            "optimVarsAxis": {"ftraj": np.array([2.0, 1.0])},
            "sampleSelectionVars": {
                "gyrSamples": selected,
                "accSamples": selected,
            },
        }
        return np.array([0.0, 1.0, 0.0]), np.array([0.0, 1.0, 0.0]), report

    monkeypatch.setattr(
        "biospur_fusion.v0.raw6_heading.qmt.jointAxisEstHingeOlsson", fake_qmt,
    )
    config = load_config(ROOT)
    _, _, report = estimate_hinge_axes_qmt(
        "knee_left", (episode,), maximum_samples=600,
        qmt_settings=config["bounded_pipeline"]["qmt_hinge_axis"],
        bounded_call=BoundedStage("FUNCTIONAL", 2.0).run,
    )
    diagnostic = report["input_degeneracy"]
    assert diagnostic["endpoints"]["child"][
        "preselection_600_exact_zero_gyro_count"
    ] == 1
    for start in diagnostic["qmt_selected_distribution_by_start"]:
        child = start["selected_denominator_audit"]["child"]
        assert child["qmt_selected_300_affected_count"] == 0
        assert start["qmt_selected_gyro_source_rows"][0][
            "preselection_600_index"
        ] == 1
        assert start["runtime_warnings"] == []


def test_qmt_selection_aware_distribution_mutations_are_capture_independent():
    contract = load_config(ROOT)["bounded_pipeline"]["qmt_hinge_axis"][
        "selection_aware_cross_denominator"
    ]
    gyro = np.tile(np.array([0.0, 1.0, 0.0]), (600, 1))
    gyro[:30] = np.array([1.0, 0.0, 0.0])
    axis = np.array([1.0, 0.0, 0.0])
    covariance = np.eye(3) * 0.003**2
    unused = qmt_selected_cross_denominator_report(
        gyro, axis, np.arange(300, 600), covariance, contract,
    )
    isolated = qmt_selected_cross_denominator_report(
        gyro, axis, np.r_[0, np.arange(300, 599)], covariance, contract,
    )
    material = qmt_selected_cross_denominator_report(
        gyro, axis, np.arange(300), covariance, contract,
    )
    assert unused["uninformative_near_axis_excited_rows"] == 0
    assert unused["material_selected_degeneracy"] is False
    assert isolated["uninformative_near_axis_excited_rows"] == 1
    assert isolated["material_selected_degeneracy"] is False
    assert material["uninformative_near_axis_excited_rows"] == 30
    assert material["material_selected_degeneracy"] is True
    assert material["threshold_provenance"] == contract["threshold_provenance"]


def test_stationary_jitter_and_quantization_do_not_require_exact_zero():
    rows = _stationary_reference_rows()
    for index, node in enumerate(NODES):
        rows[node]["gyro_raw"][:, 0] = np.resize(
            np.array([-1, 0, 1, 0], dtype=np.int16), len(rows[node]),
        )
        rows[node]["gyro_raw"][:, 1] = index % 2
    stillness = estimate_stillness(rows)
    for node in NODES:
        estimate = stillness.by_node[node]
        assert np.min(np.linalg.eigvalsh(estimate.gyro_cov_rad2_s2)) > 0.0
        assert np.min(np.linalg.eigvalsh(estimate.gyro_bias_cov_rad2_s2)) > 0.0
        assert estimate.gyro_quantization_variance_rad2_s2 > 0.0

    contract = load_config(ROOT)["bounded_pipeline"]["qmt_hinge_axis"][
        "selection_aware_cross_denominator"
    ]
    jitter = np.tile(np.array([0.001, 0.0015, -0.001]), (300, 1))
    report = qmt_selected_cross_denominator_report(
        jitter, np.array([1.0, 0.0, 0.0]), np.arange(300),
        np.eye(3) * 0.003**2, contract,
    )
    assert report["exact_zero_selected_rows"] == 0
    assert report["widespread_selected_low_excitation"] is True
    assert report["material_selected_degeneracy"] is True
    assert report["exact_zero_semantics"] == "NUMERICAL_SENTINEL_ONLY_NOT_PHYSICAL_GATE"


def test_qmt_selected_significance_is_covariance_scale_invariant():
    contract = load_config(ROOT)["bounded_pipeline"]["qmt_hinge_axis"][
        "selection_aware_cross_denominator"
    ]
    gyro = np.tile(np.array([0.2, 0.7, -0.1]), (300, 1))
    covariance = np.diag([0.01, 0.02, 0.03]) ** 2
    first = qmt_selected_cross_denominator_report(
        gyro, np.array([1.0, 0.0, 0.0]), np.arange(300), covariance, contract,
    )
    second = qmt_selected_cross_denominator_report(
        gyro * 5.0, np.array([1.0, 0.0, 0.0]), np.arange(300),
        covariance * 25.0, contract,
    )
    assert second["cross_noise_significance_quantile"] == pytest.approx(
        first["cross_noise_significance_quantile"], rel=0.0, abs=1e-12,
    )
    assert second["sufficiently_excited_selected_fraction"] == pytest.approx(
        first["sufficiently_excited_selected_fraction"], rel=0.0, abs=1e-12,
    )


def test_stage_d_selects_legal_candidate_over_lower_cost_invalid(monkeypatch):
    class DummyObjective:
        @staticmethod
        def bounds():
            return np.full(17, -1.0), np.full(17, 1.0)

        @staticmethod
        def residual_and_analytic_jacobian(value):
            raise AssertionError("focused mutation replaces numerical optimization")

        @staticmethod
        def feasibility_report(value, config):
            legal = bool(abs(float(value[0])) > 0.05)
            return {
                "pass": legal,
                "gates": {"standing_topology": legal},
                "kinematics": {},
            }

    def fake_least_squares(residual, start, low, high, budget, *, jacobian):
        state = np.asarray(start, dtype=float).copy()
        invalid_low_cost = bool(np.allclose(state[:9], 0.0))
        if not invalid_low_cost:
            state[0] = 0.1
        return {
            "finite": True, "success": True, "status": 1,
            "message": "focused physical-selection mutation",
            "state": state,
            "cost": 0.0 if invalid_low_cost else 1.0,
            "optimality": 0.0, "nfev": 1, "njev": 1,
            "elapsed_s": 0.0, "trace": [], "budget_exceeded": False,
        }

    monkeypatch.setattr(solver_module, "_least_squares", fake_least_squares)
    initial = CalibrationState(np.zeros(9), np.full(8, 0.05))
    result = solver_module._refine_all(
        DummyObjective(), initial,
        StageBudget("D_ALL_ACTION_REFINEMENT", 2, 2.0, 2, 1),
        seed=7, config={},
    )
    assert result["cost"] == 1.0
    assert result["physically_legal_runs"] == 1
    assert result["lower_cost_invalid_run_cannot_displace_legal_candidate"] is True
    assert result["physical_gate_is_optimizer_loss"] is False
    assert any(
        run.get("cost") == 0.0 and run.get("physical_candidate_pass") is False
        for run in result["runs"]
    )


def test_failed_progressive_snapshot_propagates_functional_numerical_conflict():
    truth = generate_synthetic_truth(n=200)
    profile = ProgressiveProfile("one-profile", truth.episodes[0].capture)
    profile.episodes.append(truth.episodes[0])
    conflict = "knee_left:QMT_ZERO_GYRO_JACOBIAN_DENOMINATOR"
    snapshot = profile._failed_snapshot(
        truth.episodes[0], _truth_functional(truth),
        {"named_numerical_physical_conflicts": [conflict]},
        "FUNCTIONAL_CANDIDATE_BANK", RuntimeError("bounded failure"), {},
    )
    assert snapshot["held_out"]["named_conflicts"] == [conflict]
    assert snapshot["held_out"]["pass"] is False


def test_cross_capture_profile_sharing_is_rejected_before_fitting():
    truth = generate_synthetic_truth(n=200)
    profile = ProgressiveProfile("one-profile", "EXPECTED_CAPTURE")
    with pytest.raises(ValueError, match="cross-capture"):
        profile.add_episode(
            truth.episodes[0], None, None, load_config(ROOT), seed=1,  # type: ignore[arg-type]
        )


def test_front_back_knee_split_rejects_even_when_left_right_order_is_valid():
    truth = generate_synthetic_truth(n=200)
    config = load_config(ROOT)
    bundle = build_factor_bundle(truth.episodes, config)
    functional = _truth_functional(truth)
    tilted = dict(truth.body_from_sensor)
    forward_tilt = Rotation.from_rotvec([0.0, np.radians(15.0), 0.0]).as_matrix()
    tilted["thigh_left"] = forward_tilt @ tilted["thigh_left"]
    tilted["shank_left"] = forward_tilt @ tilted["shank_left"]
    objective = C2CalibrationObjective(
        bundle, load_body_geometry(ROOT),
        replace(functional, body_from_sensor_by_segment=tilted),
    )
    state = np.r_[truth.headings_rad, truth.axial_offsets_m]
    report = objective.feasibility_report(state, config)
    assert report["gates"]["left_right_order"]
    assert not report["gates"]["knee_front_back_split"]
    assert not report["pass"]


def test_bad_held_out_covariance_block_cannot_be_hidden_by_pooled_good_block():
    report = summarize_held_out_covariance_blocks([
        {
            "edge": "knee_left", "action": "mutation",
            "covariance_block_id": "bad", "normalized": np.full(30, 3.0),
        },
        {
            "edge": "knee_left", "action": "mutation",
            "covariance_block_id": "good", "normalized": np.zeros(30),
        },
    ], threshold=2.5)
    assert report["maximum_individual_block_nrmse"] == 3.0
    assert report["maximum_pooled_edge_action_nrmse_secondary"] < 2.5
    assert report["pooled_named_conflicts_secondary"] == []
    assert report["named_conflicts"] == ["knee_left:mutation:bad"]
    assert report["pass"] is False


def test_geometry_derived_noise_bounds_and_energetic_systematic_error_reject():
    config = load_config(ROOT)
    bounds = fixed_geometry_noise_lever_bounds(config)
    assert set(bounds) == {
        "pelvis_torso", "shoulder_left", "elbow_left", "shoulder_right",
        "elbow_right", "hip_left", "knee_left", "hip_right", "knee_right",
    }
    expected_torso_shoulder = np.hypot(0.435 / 2.0, 0.16)
    assert bounds["shoulder_right"] == pytest.approx((expected_torso_shoulder, 0.33))
    energetic = np.tile(np.array([0.0, 5.0, 0.0]), (30, 1))
    sigma, audit = propagated_joint_noise_sigma(
        parent_acc_cov=np.eye(3) * 0.03**2,
        child_acc_cov=np.eye(3) * 0.03**2,
        parent_gyro_cov=np.eye(3) * 0.003**2,
        child_gyro_cov=np.eye(3) * 0.003**2,
        parent_gyro=energetic,
        child_gyro=energetic,
        parent_lever_bound_m=bounds["shoulder_right"][0],
        child_lever_bound_m=bounds["shoulder_right"][1],
        sample_period_s=0.02,
    )
    report = summarize_held_out_covariance_blocks([{
        "edge": "shoulder_right", "action": "energetic_mutation",
        "covariance_block_id": "systematic", "normalized": np.full(90, 0.5 / sigma),
    }], threshold=2.5)
    assert audit["uses_dynamic_residual_or_jerk_as_noise"] is False
    assert report["pass"] is False


def test_prepared_heading_cost_is_algebraically_identical_to_direct_objective():
    truth = generate_synthetic_truth(n=200)
    config = load_config(ROOT)
    objective = C2CalibrationObjective(
        build_factor_bundle(truth.episodes[:3], config),
        load_body_geometry(ROOT),
        _truth_functional(truth),
    )
    offsets = truth.axial_offsets_m
    for edge_name in objective.bundle.edges:
        prepared = objective.prepare_edge_heading_cost(edge_name, offsets)
        for delta in (-2.8, -0.7, 0.0, 1.3, 2.9):
            direct_rows = objective.edge_measurement_residual(
                edge_name, delta, offsets,
            )
            prepared_rows = prepared.residual(delta)
            assert prepared_rows.shape == direct_rows.shape
            assert np.allclose(prepared_rows, direct_rows, rtol=1e-12, atol=1e-12)
            assert prepared.soft_l1_cost(delta) == pytest.approx(
                objective.edge_soft_l1_cost(edge_name, delta, offsets),
                rel=1e-12, abs=1e-12,
            )
        assert prepared.audit()["measurement_rows_dropped"] is False


def test_stage_d_sparse_analytic_jacobian_matches_directional_difference():
    truth = generate_synthetic_truth(n=200)
    config = load_config(ROOT)
    objective = C2CalibrationObjective(
        build_factor_bundle(truth.episodes[:3], config),
        load_body_geometry(ROOT),
        _truth_functional(truth),
    )
    state = np.r_[truth.headings_rad, truth.axial_offsets_m]
    residual, jacobian = objective.residual_and_analytic_jacobian(state)
    assert np.allclose(residual, objective.residual(state), rtol=1e-12, atol=1e-12)
    rng = np.random.default_rng(20260829)
    direction = rng.normal(size=len(state))
    direction /= np.linalg.norm(direction)
    step = 1e-6
    numerical = (
        objective.residual(state + step * direction)
        - objective.residual(state - step * direction)
    ) / (2.0 * step)
    analytic = jacobian @ direction
    assert np.allclose(analytic, numerical, rtol=2e-5, atol=2e-7)
    assert jacobian.shape == (len(residual), len(state))
    assert jacobian.nnz > len(residual)
