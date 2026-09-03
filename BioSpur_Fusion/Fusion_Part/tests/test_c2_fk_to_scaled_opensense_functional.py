import numpy as np

from biospur_fusion.c2_fk_to_scaled_opensense.functional_axis import (
    HINGE_DOMINANT_ACTIONS,
    MULTIDOF_UNRESOLVED_ACTIONS,
    C2_TO_OPENSIM,
    _mapping_name,
    _signed_permutations,
)


def test_global_basis_is_proper_rotation():
    np.testing.assert_allclose(C2_TO_OPENSIM.T @ C2_TO_OPENSIM, np.eye(3))
    assert np.linalg.det(C2_TO_OPENSIM) == 1.0


def test_candidate_set_is_exact_proper_signed_permutation_group():
    candidates = _signed_permutations()
    assert len(candidates) == 24
    assert len({_mapping_name(candidate) for candidate in candidates}) == 24
    for candidate in candidates:
        np.testing.assert_array_equal(candidate.T @ candidate, np.eye(3))
        assert np.linalg.det(candidate) == 1.0


def test_multidof_joints_fail_closed_without_second_named_axis():
    assert {item.episode for item in HINGE_DOMINANT_ACTIONS} == {"06", "07", "10", "11"}
    assert {item.episode for item in MULTIDOF_UNRESOLVED_ACTIONS} == {"04", "05", "08", "09"}
