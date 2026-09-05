from __future__ import annotations

from dataclasses import fields
from pathlib import Path

import numpy as np

from biospur_fusion.root_r6a2a.shadow import corrected_body_model
from biospur_fusion.root_r6a1c.adapter import CORRECT_NODE_MAP
from biospur_fusion.root_r6a2a_r2.contracts import (
    EstimatorInput, FaultInjectionTruth, HealthManager, ObservationStatus,
)
from biospur_fusion.root_r6a2a_r2.estimator import make_initial_state
from biospur_fusion.root_r6a2a_r2.estimator import all_node_covariance_mapping
from biospur_fusion.root_r6a2a_r2.qualification import (
    DEVELOPMENT_SCENARIOS, authority_static_audit, rng_counterfactual, run_scenario,
)
from biospur_fusion.root_r6a2a_r2.synthetic import IndependentRng, generate_run


FUSION = Path(__file__).resolve().parents[2]


def _synthetic_body_model():
    return corrected_body_model(
        FUSION,
        identity_mapping=CORRECT_NODE_MAP,
        identity_provenance="SYNTHETIC_R6A2A_R2_TEST_MAP",
    )


def test_authority_types_are_disjoint() -> None:
    estimator_fields = {row.name for row in fields(EstimatorInput)}
    private_fields = {row.name for row in fields(FaultInjectionTruth)}
    assert not estimator_fields & private_fields
    assert authority_static_audit()["pass"]


def test_stable_rng_order_independent() -> None:
    rng = IndependentRng(1234)
    first = [rng.seed("uwb", tag, anchor) for tag, anchor in (("A", 1), ("B", 2))]
    second = [rng.seed("uwb", tag, anchor) for tag, anchor in reversed((("A", 1), ("B", 2)))]
    assert first == list(reversed(second))


def test_counterfactual_non_target_inputs_exact() -> None:
    assert rng_counterfactual(FUSION)["pass"]


def test_all_ten_covariance_mappings_reach_state() -> None:
    model = _synthetic_body_model()
    contract = all_node_covariance_mapping(model)
    assert contract["node_count"] == 10
    assert all(not row["discarded"] and row["cross_covariance_preserved"] for row in contract["nodes"].values())


def test_health_composition_order_invariant() -> None:
    rows = [
        ("imu_health", "BSFEC35", True, "bad imu", False),
        ("uwb_tag_health", "BSFEC35", False, "good uwb", False),
        ("uwb_link_health", "BSFEC35:2", True, "missing", False),
    ]
    first, second = HealthManager(), HealthManager()
    first.update_modalities(rows, 0.1)
    second.update_modalities(list(reversed(rows)), 0.1)
    assert first.snapshot() == second.snapshot()
    assert first.transitions() == second.transitions()


def test_expected_ledger_one_terminal_status_per_item() -> None:
    generated = generate_run(FUSION, DEVELOPMENT_SCENARIOS[0])
    result = run_scenario(FUSION, DEVELOPMENT_SCENARIOS[0])
    expected = sum(len(row.expected_schedule) for row in generated.inputs)
    assert len(result["accounting"]) == expected
    assert all(row["status"] in {status.value for status in ObservationStatus} for row in result["accounting"])


def test_persistent_configuration_window_is_truthful() -> None:
    scenario = next(row for row in DEVELOPMENT_SCENARIOS if row.scenario_id == "model_wrong_synthetic_lever")
    audit = generate_run(FUSION, scenario).fault_window_audit
    assert audit["persistent_physical_state"]
    assert audit["start_timestamp_s"] == 0.0
    assert audit["end_timestamp_s"] == scenario.duration_s
    assert audit["pre_window_equality"] == "NOT_APPLICABLE_PERSISTENT_CONFIGURATION"


def test_low_vertical_geometry_is_directional() -> None:
    scenario = next(row for row in DEVELOPMENT_SCENARIOS if row.scenario_id == "uwb_low_vertical_diversity")
    result = run_scenario(FUSION, scenario)
    assert result["metrics"]["weak_directional_inflation_max_m2"] > 0.0
    assert result["metrics"]["full_3d_observability_ever_disabled"]


def test_initial_root_covariance_matches_declared_axis_error_second_moments() -> None:
    generated = generate_run(FUSION, DEVELOPMENT_SCENARIOS[0])
    model = _synthetic_body_model()
    state, contract = make_initial_state(model, generated.truth_states[0])
    assert contract["root_position_axis_standard_deviations_m"] == [0.055, 0.035, 0.018]
    assert not contract["root_position_isotropic"]
    assert np.allclose(np.diag(state.covariance[:3, :3]), np.square([0.055, 0.035, 0.018]))
