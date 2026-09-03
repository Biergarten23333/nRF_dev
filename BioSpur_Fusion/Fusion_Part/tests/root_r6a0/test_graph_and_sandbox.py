from __future__ import annotations

from biospur_fusion.root_r6a0.contracts import ActivationState
from biospur_fusion.root_r6a0.observability import capability_atlas, expected_nullspace


def test_graph_contract_and_shared_inverse(scenario):
    validation = scenario.graph_spec.validate()
    assert validation["pass"]
    assert validation["ik_is_same_graph_inverse"]
    assert scenario.graph_spec.inverse_problem == "MAP_OVER_THIS_GRAPH_USING_SHARED_BODYMODEL_FK"


def test_constraint_tiers_are_explicit(scenario):
    tiers = scenario.graph_spec.constraint_tiers
    assert tiers["A_exact_structural"] is ActivationState.ACTIVE_STRUCTURAL
    assert tiers["B_calibrated_invariants"] is ActivationState.ACTIVE_SYNTHETIC
    assert tiers["C_soft_anatomy"] is ActivationState.ACTIVE_SYNTHETIC
    assert tiers["D_contextual_biomechanics"] is ActivationState.DISABLED


def test_gauge_and_nullspace_are_explicit():
    directions = expected_nullspace(qualified_static_gauge=False, raw_uwb_available=False,
                                    calibration_resolved=False)
    assert {"global_translation_x", "global_translation_y", "global_translation_z", "global_yaw"} <= set(directions)
    atlas = capability_atlas()
    assert atlas["derived_endpoint_rule"]["directly_observed"] is False


def test_test_only_inverse_executes_same_graph(gates):
    inverse = gates["inverse_map_harness"]
    assert inverse["success"]
    assert inverse["same_body_model_and_factor_paths"]
    assert inverse["cost_reduced"]
    assert inverse["final_rms"] < 1e-8
    assert not inverse["production_promoted"]


def test_all_synthetic_gates_pass(gates):
    assert gates["pass"]
    assert all(gates["checks"].values())
    assert not gates["claims"]["real_world_accuracy_established"]
    assert not gates["claims"]["clinical_validity_established"]
    assert not gates["claims"]["production_authorized"]
