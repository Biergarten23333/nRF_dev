from __future__ import annotations

from dataclasses import replace

import numpy as np
from scipy.integrate import quad
from scipy.special import ndtr

from biospur_fusion.c2_uwb_calibration.body_shadow_study import (
    BOOTSTRAP_REPLICATES,
    EVALUATION_CONTRACT,
    FULL_ROW_DTYPE,
    MODEL_CONTRACT,
    PAIR_ORDER,
    PAIR_REFERENCE,
    eligibility_gate,
    evaluate_full_study,
    evaluate_predictive_scores,
    exposure_cluster_gate,
    fit_nested_models,
    freeze_residualized_contrast,
    freeze_common_design,
    model_predictions,
    nuisance_scaled_bounds,
    paired_action_epoch_bootstrap,
    pair_coverage_gate,
    residualized_shadow_audit,
    residualized_exposure_contrast_audit,
    significant_negative_stratum_reversals,
    sum_zero_pair_effects,
    two_piece_normal_crps,
    two_piece_normal_nll,
    two_piece_normal_quantile,
    two_piece_normal_tail_moments,
    variance_only_increment_cannot_pass,
)


def _rows(repetitions: int = 3) -> np.ndarray:
    count = 19 * len(PAIR_ORDER) * repetitions
    rows = np.zeros(count, dtype=FULL_ROW_DTYPE)
    rng = np.random.default_rng(20260905)
    cursor = 0
    for action in range(19):
        for repetition in range(repetitions):
            for pair in range(len(PAIR_ORDER)):
                node, anchor = divmod(pair, 8)
                phase = 0.11 * action + 0.07 * repetition + 0.03 * pair
                own = 0.5 + 0.45 * np.sin(phase)
                torso = 0.5 + 0.45 * np.cos(1.7 * phase + 0.2)
                limb = 0.5 + 0.45 * np.sin(2.3 * phase - 0.4)
                predicted_range = 2.0 + 0.03 * pair + 0.01 * repetition + 0.05 * rng.normal()
                xyz = rng.normal(size=3)
                response = 0.01 * ((pair % 7) - 3) + 0.08 * own + 0.12 * torso + 0.15 * limb
                response += rng.normal(scale=0.005)
                rows[cursor] = (
                    action, action * 100 + repetition, node, anchor,
                    action * 100 + repetition, 2.5, 1e9 + cursor,
                    predicted_range - response, response, 3, 10.0, 3, 11.0, 1,
                    own, torso, limb, predicted_range,
                    xyz[0], xyz[1], xyz[2], 0.11 + 0.005 * abs(rng.normal()),
                )
                cursor += 1
    return rows


def _masks(rows: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    train = rows["action_index"] < 11
    return train, ~train


def test_model_contract_is_strictly_nested_location_only_and_bounded() -> None:
    assert "mode/location" in MODEL_CONTRACT["likelihood"]
    assert MODEL_CONTRACT["shadow_coefficient_count"] == 3
    assert MODEL_CONTRACT["shadow_bounds_m"] == [0.0, 1.0]
    assert "freezes every B0" in MODEL_CONTRACT["nesting_fit_owner"]
    assert "variance-only" in MODEL_CONTRACT["forbidden"]
    assert EVALUATION_CONTRACT["bootstrap"]["replicates"] == 1000


def test_train_only_gauge_and_nested_predictions_do_not_read_validation_response() -> None:
    rows = _rows()
    train, validation = _masks(rows)
    design = freeze_common_design(rows, train)
    fit = fit_nested_models(rows, train, design)
    changed = rows.copy()
    changed["signed_innovation_m"][validation] += 1000.0
    changed_design = freeze_common_design(changed, train)
    changed_fit = fit_nested_models(changed, train, changed_design)
    assert design.names == changed_design.names
    assert f"pair[{PAIR_REFERENCE}]" not in design.names
    assert np.array_equal(fit.nuisance_coefficients_m, changed_fit.nuisance_coefficients_m)
    assert fit.beta_own_m == changed_fit.beta_own_m
    assert fit.beta_torso_m == changed_fit.beta_torso_m
    assert fit.beta_limb_m == changed_fit.beta_limb_m
    assert fit.scale_m == changed_fit.scale_m
    changed_features = rows.copy()
    for name in (
        "own_inward_probability", "torso_exposure", "other_limb_exposure",
        "causal_predicted_range_m", "causal_tag_origin_x_m",
        "causal_tag_origin_y_m", "causal_tag_origin_z_m", "base_sigma_m",
    ):
        changed_features[name][validation] += 100.0
    feature_design = freeze_common_design(changed_features, train)
    feature_fit = fit_nested_models(changed_features, train, feature_design)
    assert np.array_equal(fit.nuisance_coefficients_m, feature_fit.nuisance_coefficients_m)
    assert fit.beta_own_m == feature_fit.beta_own_m
    assert fit.beta_torso_m == feature_fit.beta_torso_m
    assert fit.beta_limb_m == feature_fit.beta_limb_m
    assert fit.scale_m == feature_fit.scale_m
    predictions = model_predictions(rows, design, fit)
    reported_pairs = sum_zero_pair_effects(design, fit)
    assert set(reported_pairs) == set(PAIR_ORDER)
    assert abs(sum(reported_pairs.values())) < 1e-12
    lower, upper = nuisance_scaled_bounds(design)
    np.testing.assert_allclose(
        upper / design.design_column_rms, 5.0, atol=0.0, rtol=0.0
    )
    np.testing.assert_array_equal(lower, -upper)
    pair_zero_train = train & (rows["node_index"] == 0) & (rows["anchor"] == 0)
    reweighted_rows = np.concatenate((rows, rows[pair_zero_train], rows[pair_zero_train]))
    reweighted_train = reweighted_rows["action_index"] < 11
    reweighted_design = freeze_common_design(reweighted_rows, reweighted_train)
    assert not np.array_equal(
        design.design_column_rms[1:], reweighted_design.design_column_rms[1:]
    )
    _lower, reweighted_upper = nuisance_scaled_bounds(reweighted_design)
    np.testing.assert_allclose(
        reweighted_upper / reweighted_design.design_column_rms,
        5.0, atol=0.0, rtol=0.0,
    )
    np.testing.assert_allclose(
        predictions["B1"] - predictions["B0"],
        fit.beta_torso_m * rows["torso_exposure"],
        atol=1e-15,
        rtol=1e-15,
    )
    np.testing.assert_allclose(
        predictions["B2"] - predictions["B1"],
        fit.beta_limb_m * rows["other_limb_exposure"],
        atol=1e-15,
        rtol=1e-15,
    )


def test_design_and_residualized_shadow_rank_are_direct_data_gates() -> None:
    rows = _rows()
    train, _validation = _masks(rows)
    design = freeze_common_design(rows, train)
    assert design.rank == len(design.names)
    assert design.condition <= 1e4
    audit = residualized_shadow_audit(rows, train, design)
    assert audit["pass"] and audit["rank"] == 2
    assert [audit["direct_scaled_designs"][name]["rank"] for name in ("B0", "B1", "B2")] == [
        len(design.names) + 1,
        len(design.names) + 2,
        len(design.names) + 3,
    ]
    collapsed = rows.copy()
    collapsed["other_limb_exposure"] = collapsed["torso_exposure"]
    assert not residualized_shadow_audit(collapsed, train, design)["pass"]


def test_two_piece_likelihood_quantile_and_exact_crps_are_finite() -> None:
    response = np.array([-0.3, 0.0, 0.2, 1.0])
    location = np.array([-0.1, -0.1, 0.1, 0.4])
    nll = two_piece_normal_nll(response, location, 0.2)
    crps = two_piece_normal_crps(response, location, 0.2)
    q90 = two_piece_normal_quantile(location, 0.2, 0.90)
    assert np.all(np.isfinite(nll))
    assert np.all(np.isfinite(crps)) and np.all(crps >= 0.0)
    assert np.all(q90 > location)
    assert np.allclose(
        two_piece_normal_crps(response + 3.0, location + 3.0, 0.2), crps
    )
    survival, first = two_piece_normal_tail_moments(location, 0.2, 0.3)
    assert np.all((survival > 0.0) & (survival <= 1.0))
    assert np.all(first / survival >= 0.3)


def test_split_normal_crps_and_tail_moment_match_independent_quadrature() -> None:
    mu, scale, observed, threshold = 0.1, 0.2, 0.35, 0.3
    normalizer = np.sqrt(2.0 / np.pi) / (3.0 * scale)

    def density(value: float) -> float:
        side = scale if value < mu else 2.0 * scale
        return float(normalizer * np.exp(-0.5 * ((value - mu) / side) ** 2))

    expected_abs = sum(
        quad(
            lambda value: abs(value - observed) * density(value),
            lower, upper, epsabs=1e-12, epsrel=1e-12,
        )[0]
        for lower, upper in ((-np.inf, mu), (mu, observed), (observed, np.inf))
    )

    def cdf(value: float) -> float:
        if value < mu:
            return float((2.0 / 3.0) * ndtr((value - mu) / scale))
        return float(
            1.0 / 3.0
            + (4.0 / 3.0) * (ndtr((value - mu) / (2.0 * scale)) - 0.5)
        )

    pair_abs = 2.0 * sum(
        quad(
            lambda value: cdf(value) * (1.0 - cdf(value)),
            lower, upper, epsabs=1e-12, epsrel=1e-12,
        )[0]
        for lower, upper in ((-np.inf, mu), (mu, np.inf))
    )
    reference_crps = expected_abs - 0.5 * pair_abs
    actual_crps = two_piece_normal_crps(
        np.array([observed]), np.array([mu]), scale
    )[0]
    np.testing.assert_allclose(actual_crps, reference_crps, atol=2e-9, rtol=2e-9)
    survival, first = two_piece_normal_tail_moments(
        np.array([mu]), scale, threshold
    )
    reference_survival = quad(density, threshold, np.inf, epsabs=1e-12)[0]
    reference_first = quad(
        lambda value: value * density(value), threshold, np.inf, epsabs=1e-12
    )[0]
    np.testing.assert_allclose(survival[0], reference_survival, atol=1e-11)
    np.testing.assert_allclose(first[0], reference_first, atol=1e-11)


def test_hierarchical_paired_bootstrap_is_deterministic_and_action_sensitive() -> None:
    action = np.repeat(np.arange(8), 40)
    epoch = np.tile(np.arange(40), 8)
    delta = np.repeat(np.arange(8, dtype=float), 40)
    first = paired_action_epoch_bootstrap(delta, action, epoch)
    second = paired_action_epoch_bootstrap(delta, action, epoch)
    assert dict(first) == dict(second)
    assert first["replicates"] == BOOTSTRAP_REPLICATES
    assert first["lower95"] < first["upper95"]


def test_variance_only_increment_cannot_pass() -> None:
    prediction = np.arange(10, dtype=float)
    assert not variance_only_increment_cannot_pass(prediction, prediction.copy())
    changed = prediction.copy()
    changed[3] += 1e-12
    assert variance_only_increment_cannot_pass(prediction, changed)


def test_eligibility_gate_requires_every_action_node_fraction_and_zero_failures() -> None:
    attempts = {(action, node): 100 for action in range(19) for node in range(10)}
    eligible = {key: 95 for key in attempts}
    assert eligibility_gate(attempts, eligible, 0)["pass"]
    bad = dict(eligible)
    bad[(2, 3)] = 94
    assert not eligibility_gate(attempts, bad, 0)["pass"]
    assert not eligibility_gate(attempts, eligible, 1)["pass"]
    missing_attempt = dict(attempts)
    missing_attempt.pop((18, 9))
    assert not eligibility_gate(missing_attempt, eligible, 0)["pass"]


def test_pair_coverage_requires_train_action_block_row_and_validation_presence() -> None:
    rows = _rows(repetitions=130)
    train, validation = _masks(rows)
    assert pair_coverage_gate(rows, train, validation)["pass"]
    missing = validation & (rows["node_index"] == 0) & (rows["anchor"] == 0)
    reduced = rows[~missing]
    reduced_train, reduced_validation = _masks(reduced)
    assert not pair_coverage_gate(reduced, reduced_train, reduced_validation)["pass"]


def test_exposure_coverage_counts_distinct_epochs_not_link_rows() -> None:
    rows = _rows(repetitions=80)
    train, validation = _masks(rows)
    audit = exposure_cluster_gate(rows, train, validation)
    assert isinstance(audit["pass"], bool)
    one_epoch = rows.copy()
    one_epoch["source_epoch_index"][validation] = 1
    assert not exposure_cluster_gate(one_epoch, train, validation)["pass"]


def test_fixed_stratum_negative_reversal_detector() -> None:
    rows = _rows(repetitions=40)
    _train, validation = _masks(rows)
    facing_edges = (-np.inf, np.inf)
    range_edges = (-np.inf, np.inf)
    neutral = significant_negative_stratum_reversals(
        np.zeros(len(rows)), rows, validation,
        facing_edges=facing_edges, range_edges=range_edges,
        minimum_epoch_clusters=1,
        replicates=30,
    )
    negative = significant_negative_stratum_reversals(
        -np.ones(len(rows)), rows, validation,
        facing_edges=facing_edges, range_edges=range_edges,
        minimum_epoch_clusters=1,
        replicates=30,
    )
    assert neutral["pass"]
    assert not negative["pass"]
    undercovered = significant_negative_stratum_reversals(
        np.zeros(len(rows)), rows, validation,
        facing_edges=facing_edges, range_edges=range_edges,
        minimum_epoch_clusters=1_000_000,
        replicates=30,
    )
    assert undercovered["evaluated_strata"] == 0
    assert not undercovered["coverage_pass"]
    assert not undercovered["pass"]


def test_predictive_evaluation_uses_identical_rows_and_no_composite_rescue() -> None:
    rows = _rows(repetitions=5)
    train, validation = _masks(rows)
    design = freeze_common_design(rows, train)
    fit = fit_nested_models(rows, train, design)
    predictions = model_predictions(rows, design, fit)
    audit = evaluate_predictive_scores(
        rows, train, validation, predictions, fit, replicates=30
    )
    assert set(audit) == {
        "B1_minus_B0", "B2_minus_B1", "pass_without_contrast_or_reversal_gates"
    }
    malformed = dict(predictions)
    malformed["B2"] = malformed["B2"][:-1]
    try:
        evaluate_predictive_scores(
            rows, train, validation, malformed, fit, replicates=30
        )
    except ValueError as exc:
        assert "identical paired rows" in str(exc)
    else:
        raise AssertionError("mismatched model rows were accepted")


def test_residualized_contrast_owner_is_train_only_and_action_gated() -> None:
    rows = _rows(repetitions=40)
    train, validation = _masks(rows)
    design = freeze_common_design(rows, train)
    predictions = {"B0": np.zeros(len(rows)), "B1": np.zeros(len(rows))}
    torso = freeze_residualized_contrast(
        rows, train, design, feature_name="torso_exposure"
    )
    audit = residualized_exposure_contrast_audit(
        rows, validation, predictions, torso, replicates=30
    )
    assert audit["parent_model"] == "B0"
    changed = rows.copy()
    changed["torso_exposure"][validation] += 10.0
    changed_owner = freeze_residualized_contrast(
        changed, train, design, feature_name="torso_exposure"
    )
    assert np.array_equal(
        torso.residualizer_coefficients, changed_owner.residualizer_coefficients
    )


def test_full_evaluation_keeps_each_gate_independent() -> None:
    rows = _rows(repetitions=40)
    train, validation = _masks(rows)
    design = freeze_common_design(rows, train)
    fit = fit_nested_models(rows, train, design)
    audit = evaluate_full_study(
        rows, train, validation, design, fit, replicates=20
    )
    assert set(audit["gates"]) == {
        "paired_predictive_scores",
        "residualized_exposure_contrasts",
        "fixed_stratum_no_negative_reversal",
        "coefficients_finite_monotone_bounded",
        "no_occlusion_based_link_deletion",
        "anchor_geometry_unchanged",
    }
    assert audit["gates"]["no_occlusion_based_link_deletion"]
    assert audit["downstream_policy"]["occlusion_based_link_deletion"] is False
    assert audit["downstream_policy"]["covariance_inflation_applied"] is False
    assert audit["downstream_policy"]["covariance_mapping"] == (
        "DEFERRED_NO_SOLVER_INTEGRATION"
    )
