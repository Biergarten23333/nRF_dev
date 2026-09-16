"""Source-contract tests for the controlled diagnostic A/B owner."""
import inspect

from biospur_fusion.c2_uwb_root_world.diagnostic_shared_root_ab import DiagnosticSharedRootABOwner


def test_owner_is_explicitly_nonpromotable_and_has_no_config_knobs():
    assert DiagnosticSharedRootABOwner.qualification == "DIAGNOSTIC_ROOT_ONLY_NON_PROMOTABLE"
    assert DiagnosticSharedRootABOwner.product_ready is False
    assert DiagnosticSharedRootABOwner.scientific_pass is False
    parameters = inspect.signature(DiagnosticSharedRootABOwner).parameters
    assert tuple(parameters) == ("static", "pose_source", "gauge")


def test_source_contract_contains_only_position_update_indices_and_identical_inertial_modes():
    source = inspect.getsource(DiagnosticSharedRootABOwner)
    assert "state_update_indices=(0, 1, 2)" in source
    assert source.count("RootTranslationEdgeMode.INERTIAL") >= 4
    assert "select_trusted_body_nodes" not in source
    assert "AUDIT_ONLY" in source
