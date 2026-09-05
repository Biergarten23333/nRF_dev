from __future__ import annotations

import numpy as np
import pytest
import json

from biospur_fusion.c2_uwb_calibration.pair_bias import (
    PairBiasEstimate,
    aggregate_causal_pair_bias_tables,
    estimate_pair_bias,
    load_pair_bias_table,
    propagate_causal_pair_uncertainty,
    separate_fixed_bias_from_initial_pose_nlos,
    validate_complete_pair_bias_table,
)


def test_pair_bias_uses_robust_location_and_dispersion_once() -> None:
    values = np.r_[np.linspace(0.18, 0.22, 39), 5.0]
    estimate = estimate_pair_bias("node", 3, values, minimum_samples=30)
    assert estimate.node == "node"
    assert estimate.anchor == 3
    assert estimate.sample_count == 40
    assert estimate.bias_m == pytest.approx(np.median(values))
    assert estimate.robust_sigma_m == pytest.approx(
        1.4826 * np.median(np.abs(values - np.median(values)))
    )
    assert estimate.bias_m < 0.23


def test_causal_pair_bias_aggregation_is_episode_balanced_and_conservative() -> None:
    first = {
        ("node", anchor): PairBiasEstimate("node", anchor, 0.1, 0.02, 300)
        for anchor in range(8)
    }
    second = {
        ("node", anchor): PairBiasEstimate("node", anchor, 0.3, 0.04, 30)
        for anchor in range(8)
    }
    third = {
        ("node", anchor): PairBiasEstimate("node", anchor, 0.2, 0.03, 60)
        for anchor in range(8)
    }
    result = aggregate_causal_pair_bias_tables(
        [first, second, third], nodes=["node"]
    )
    estimate = result[("node", 0)]
    assert estimate.bias_m == pytest.approx(0.2)
    assert estimate.sample_count == 390
    assert estimate.robust_sigma_m > 0.03


def test_causal_pair_bias_rejects_empty_or_incomplete_history() -> None:
    with pytest.raises(ValueError, match="empty"):
        aggregate_causal_pair_bias_tables([], nodes=["node"])
    incomplete = {
        ("node", anchor): PairBiasEstimate("node", anchor, 0.0, 0.1, 30)
        for anchor in range(7)
    }
    with pytest.raises(ValueError, match="key mismatch"):
        aggregate_causal_pair_bias_tables([incomplete], nodes=["node"])


def test_causal_uncertainty_never_changes_fixed_bias_or_reduces_sigma() -> None:
    fixed = {
        ("node", anchor): PairBiasEstimate("node", anchor, 0.1, 0.02, 40)
        for anchor in range(8)
    }
    moved = {
        ("node", anchor): PairBiasEstimate("node", anchor, 0.4, 0.03, 30)
        for anchor in range(8)
    }
    propagated = propagate_causal_pair_uncertainty(
        fixed, [fixed, moved], nodes=["node"]
    )
    estimate = propagated[("node", 0)]
    assert estimate.bias_m == fixed[("node", 0)].bias_m
    assert estimate.robust_sigma_m >= fixed[("node", 0)].robust_sigma_m
    assert estimate.sample_count == 70


def test_pair_bias_rejects_insufficient_or_incomplete_ownership() -> None:
    with pytest.raises(ValueError, match="insufficient"):
        estimate_pair_bias("node", 0, [0.1] * 29)
    table = {
        ("node", anchor): PairBiasEstimate("node", anchor, 0.0, 0.1, 30)
        for anchor in range(8)
    }
    validate_complete_pair_bias_table(table, nodes=["node"])
    del table[("node", 7)]
    with pytest.raises(ValueError, match="key mismatch"):
        validate_complete_pair_bias_table(table, nodes=["node"])


def test_pair_bias_table_round_trip_and_schema_guard(tmp_path) -> None:
    path = tmp_path / "bias.json"
    document = {
        "schema": "biospur-c2-held-node-pair-bias-v1",
        "source_episode": "00_initial_still",
        "method": "single_median_of_residuals_from_other_nine_node_root",
        "estimates": [
            {
                "node": "node", "anchor": anchor, "bias_m": anchor / 100.0,
                "robust_sigma_m": 0.1, "sample_count": 30,
            }
            for anchor in range(8)
        ],
    }
    path.write_text(json.dumps(document))
    loaded = load_pair_bias_table(path, nodes=["node"])
    assert loaded[("node", 7)].bias_m == pytest.approx(0.07)
    document["schema"] = "wrong"
    path.write_text(json.dumps(document))
    with pytest.raises(ValueError, match="schema"):
        load_pair_bias_table(path, nodes=["node"])


def test_shadow_prequential_uncertainty_requires_explicit_diagnostic_loading(
    tmp_path,
) -> None:
    path = tmp_path / "prequential.json"
    document = {
        "schema": "biospur-c2-held-node-pair-bias-v1",
        "source_episode": "00_to_19_prequential_after_scoring",
        "method": "fixed_00_bias_plus_causal_cross_episode_uncertainty",
        "shadow_only": True,
        "estimates": [
            {
                "node": "node", "anchor": anchor, "bias_m": 0.1,
                "robust_sigma_m": 0.2, "sample_count": 100,
            }
            for anchor in range(8)
        ],
    }
    path.write_text(json.dumps(document))
    with pytest.raises(ValueError, match="not production-loadable"):
        load_pair_bias_table(path, nodes=["node"])
    loaded = load_pair_bias_table(path, nodes=["node"], allow_shadow=True)
    assert loaded[("node", 7)].bias_m == pytest.approx(0.1)
    document["shadow_only"] = False
    path.write_text(json.dumps(document))
    with pytest.raises(ValueError, match="shadow-only"):
        load_pair_bias_table(path, nodes=["node"], allow_shadow=True)


def test_large_initial_offset_is_uncertainty_not_fixed_subtraction() -> None:
    small = PairBiasEstimate("node", 0, 0.15, 0.04, 100)
    small_use = separate_fixed_bias_from_initial_pose_nlos(
        small, layout_sigma_m=0.0565
    )
    assert not small_use.initial_pose_nlos_state
    assert small_use.fixed_correction_m == pytest.approx(0.15)
    assert small_use.additional_sigma_m == pytest.approx(0.04)
    assert small_use.fixed_bias_limit_m == pytest.approx(0.20)

    large = PairBiasEstimate("node", 0, 2.0, 0.04, 100)
    large_use = separate_fixed_bias_from_initial_pose_nlos(
        large, layout_sigma_m=0.0565
    )
    assert large_use.initial_pose_nlos_state
    assert large_use.fixed_correction_m == 0.0
    assert large_use.additional_sigma_m == pytest.approx(np.hypot(0.04, 1.0))
