from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from biospur_fusion.root_r6a0.math3d import so3_exp
from biospur_fusion.root_r6a2a.shadow import corrected_body_model
from biospur_fusion.root_r6a2b.layered_calibration import (
    COMMON_NINE, FAMILIES, CanonicalCalibrationAdapter, numerical_jacobian,
    proper_signed_permutations,
)
from biospur_fusion.root_r6a2b.real_profile import profile_checksum


FUSION = Path(__file__).resolve().parents[2]
RESULT = FUSION / "logs/root_r6a2b_r2_layered_real_calibration_20260826T143000Z"


def adapter() -> CanonicalCalibrationAdapter:
    rows = json.loads((
        FUSION / "logs/root_r6a2b_r1_calibration_first_20260826T093237Z/"
        "CALIBRATION_PARAMETER_PROVENANCE.json"
    ).read_text())["slots"]
    return CanonicalCalibrationAdapter(corrected_body_model(FUSION), rows)


def test_canonical_round_trip_is_exact() -> None:
    value = np.linspace(-0.4, 0.4, 114)
    current = adapter()
    assert np.array_equal(current.slots_to_vector(current.vector_to_slots(value)), value)


def test_parameter_inventory_has_no_duplicate_freedom() -> None:
    current = adapter()
    assert len(current.blocks) == len(current.by_slot) == 28
    assert current.dimension == 114
    assert len(set(current.rotation_coordinates()) & set(current.geometry_coordinates())) == 0
    assert set(current.rotation_coordinates()) | set(current.geometry_coordinates()) == set(range(114))


def test_so3_right_local_perturbation_composes_rotations() -> None:
    current = adapter()
    base = np.zeros(114); delta = np.zeros(114)
    base[:3] = [0.2, -0.1, 0.05]; delta[:3] = [-0.03, 0.04, 0.02]
    perturbed = current.right_local_perturb(base, delta)
    assert np.allclose(so3_exp(perturbed[:3]), so3_exp(base[:3]) @ so3_exp(delta[:3]), atol=1e-12)


def test_proper_signed_permutation_inventory() -> None:
    rows = proper_signed_permutations()
    assert len(rows) == 24
    assert len({label for label, _ in rows}) == 24
    for _, matrix in rows:
        assert np.array_equal(matrix @ matrix.T, np.eye(3))
        assert np.isclose(np.linalg.det(matrix), 1.0)


def test_hardware_families_are_isolated() -> None:
    assert FAMILIES["BSF31CC"] == "BSF31CC_V0_20_N5BL"
    assert all(FAMILIES[node] == "COMMON_NINE_V0_20_PCB17" for node in COMMON_NINE)
    assert "BSF31CC" not in COMMON_NINE


def test_fixed_child_and_world_views_add_no_coordinates() -> None:
    current = adapter()
    static = current.materialize_static(np.zeros(114))
    assert np.array_equal(static.vector("world_model_gauge", 6), np.zeros(6))
    for joint in current.model.joints:
        assert joint.child_offset_slot not in current.by_slot
        assert np.array_equal(static.vector(joint.child_offset_slot, 3), np.zeros(3))


def test_unresolved_downstream_rows_do_not_block_shared_fk() -> None:
    current = adapter()
    static = current.materialize_static(np.zeros(114))
    assert static.slot("anatomical_point:wrist_left").value is None
    assert static.slot("anatomical_point:ankle_right").value is None
    assert static.slot("imu_extrinsic:BSF31CC").value is not None


def test_prior_is_finite_and_explicit_for_every_coordinate() -> None:
    current = adapter()
    assert np.isfinite(current.prior_sigma).all()
    assert np.all(current.prior_sigma > 0.0)
    assert set(current.prior_provenance) == {"imu_extrinsic", "joint_parent", "joint_rest", "prior_mean"}


def test_numerical_jacobian_matches_closed_form() -> None:
    value = np.array([0.2, -0.3, 0.5])
    jacobian = numerical_jacobian(lambda x: np.array([x[0] ** 2 + 3 * x[1], np.sin(x[2])]), value)
    expected = np.array([[0.4, 3.0, 0.0], [0.0, 0.0, np.cos(0.5)]])
    assert np.allclose(jacobian, expected, rtol=1e-8, atol=1e-9)


def test_real_execution_used_native_time_and_no_heldout_payload() -> None:
    native = json.loads((RESULT / "NATIVE_TIME_PREINTEGRATION_AUDIT.json").read_text())
    access = json.loads((RESULT / "CALIBRATION_WINDOW_ACCESS_AUDIT.json").read_text())
    assert native["nominal_200hz_timing_substituted"] is False
    assert access["opened_only_exact_authorized_calibration_slices"] is True
    assert access["held_out_members_or_intervals_opened"] == []


def test_real_candidate_has_no_fabricated_values_and_replay_is_immutable() -> None:
    text = (RESULT / "DEVELOPMENT_ONLY_REAL_CALIBRATION_CANDIDATE.json").read_text()
    profile = json.loads(text)
    replay = json.loads((RESULT / "CALIBRATION_WINDOW_REPLAY_SUMMARY.json").read_text())
    assert "synthetic" not in text.lower()
    assert profile_checksum(profile) == profile["profile_checksum_sha256"]
    assert replay["static_profile_changed"] is False
    assert replay["static_profile_checksum_before_replay"] == replay["static_profile_checksum_after_replay"]


def test_shared_fk_operator_and_independent_recompute_are_exact() -> None:
    optimizer = json.loads((RESULT / "OPTIMIZER_EVIDENCE.json").read_text())
    verification = json.loads((RESULT / "INDEPENDENT_VERIFICATION.json").read_text())
    assert optimizer["shared_fk_real_calibration_solve_executed"] is True
    assert optimizer["geometry_layer"]["shared_fk_affine_equivalence_max_abs_m"] < 1e-10
    assert verification["verdict"] == "PASS"
    assert verification["recomputation"]["maximum_abs_vector_difference"] == 0.0
    assert verification["recomputation"]["maximum_abs_covariance_difference"] == 0.0
