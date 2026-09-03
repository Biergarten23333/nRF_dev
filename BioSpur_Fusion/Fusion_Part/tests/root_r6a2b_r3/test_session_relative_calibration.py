from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from biospur_fusion.root_r6a0.math3d import so3_exp, so3_log
from biospur_fusion.root_r6a2b.real_profile import profile_checksum
from biospur_fusion.root_r6a2b.session_relative_calibration import (
    HINGE_ACTION, JOINT_ACTIONS, REPORTING_LABEL, deferred_anthropometry_schema,
    session_joint_coordinates,
)


FUSION = Path(__file__).resolve().parents[2]
RESULT = FUSION / "logs/root_r6a2b_r3_session_relative_joint_calibration_20260826T160000Z"


def load(name: str):
    return json.loads((RESULT / name).read_text())


def test_declared_composition_reconstructs_relative_rotation() -> None:
    reference = so3_exp(np.array([.2, -.1, .05]))
    q = np.array([-.08, .12, .03])
    relative = reference @ so3_exp(q)
    recovered = session_joint_coordinates(reference, relative[None])[0]
    assert np.allclose(so3_exp(recovered), so3_exp(q), atol=1e-12)
    assert np.allclose(reference @ so3_exp(recovered), relative, atol=1e-12)


def test_constant_rotation_gauge_and_compensation_are_exact() -> None:
    rest = so3_exp(np.array([.1, .2, -.1]))
    q = so3_exp(np.array([-.2, .05, .12]))
    delta = so3_exp(np.deg2rad(np.array([1.0, 0.0, 0.0])))
    transformed_rest = rest @ delta
    transformed_q = delta.T @ q
    assert np.allclose(rest @ q, transformed_rest @ transformed_q, atol=1e-12)


def test_all_previous_joint_rest_columns_were_null_but_shared_fk_depends_on_them() -> None:
    trace = load("JOINT_REST_GAUGE_CAUSAL_TRACE.json")
    assert trace["cause"] == "MIXED"
    assert trace["before_data_rank"] == 87 and trace["before_data_nullity"] == 27
    assert len(trace["tested_slots"]) == 9
    assert trace["maximum_compensated_complete_output_change_linf"] < 1e-12
    for slot in trace["tested_slots"]:
        assert len(slot["coordinates"]) == 3
        for coordinate in slot["coordinates"]:
            assert len(coordinate["tests"]) == 2
            for row in coordinate["tests"]:
                assert row["r2_complete_data_jacobian_column_norm"] == 0.0
                assert row["shared_fk_fixed_dynamic_state_output_change_linf"] > 0.0
                assert row["shared_fk_compensated_output_change_linf"] < 1e-12


def test_session_reference_removes_coordinate_gauge_without_claiming_physical_information() -> None:
    reference = load("SESSION_RELATIVE_JOINT_REFERENCE.json")
    information = load("OBSERVABILITY_AND_COVARIANCE.json")
    assert reference["reference_name"] == "SESSION_RELATIVE_JOINT_REFERENCE"
    assert "physiological" in " ".join(reference["not_claimed"])
    assert information["before"]["data_nullity"] == 27
    assert information["after_session_reference_convention"]["coordinate_nullity"] == 0
    assert information["physical_information_without_coordinate_definition"]["nullity"] == 27
    assert information["posterior_covariance_zeroed_by_gauge_fix"] is False
    for row in reference["per_joint"].values():
        covariance = np.asarray(row["reference_covariance_rad2"])
        assert np.all(np.diag(covariance) > 0.0)


def test_every_repaired_joint_rest_coordinate_has_nonzero_objective_path() -> None:
    information = load("OBSERVABILITY_AND_COVARIANCE.json")
    norms = information["after_session_reference_convention"]["joint_rest_profiled_jacobian_column_norms"]
    assert set(norms) == set(JOINT_ACTIONS)
    assert all(np.all(np.asarray(value) > 0.0) for value in norms.values())


def test_hinges_are_soft_and_ball_joints_remain_three_dof() -> None:
    reference = load("SESSION_RELATIVE_JOINT_REFERENCE.json")
    objective = load("FUNCTIONAL_BIOMECHANICS_OBJECTIVE.json")
    assert objective["perfect_hinge_imposed"] is False
    assert objective["elbows_knees_off_axis_freedom_retained"] is True
    assert objective["shoulders_hips_three_dof_retained"] is True
    assert objective["by_residual_family"]["off_axis_soft"]["count"] > 0
    for joint, row in reference["per_joint"].items():
        if joint in HINGE_ACTION:
            assert row["dof_family"] == "SOFT_EFFECTIVE_HINGE_WITH_OFF_AXIS_FREEDOM"
            assert row["functional_axis"]["angular_dispersion_rad"] > 0.0
        else:
            assert row["dof_family"] == "THREE_ROTATIONAL_DOF"


def test_action_semantics_create_nonzero_biomechanical_residuals() -> None:
    objective = load("FUNCTIONAL_BIOMECHANICS_OBJECTIVE.json")
    expected = {action for actions in JOINT_ACTIONS.values() for action in actions}
    observed = set(objective["by_residual_family"]["joint_closure"]["by_action"])
    observed |= set(objective["by_residual_family"]["functional_axis"]["by_action"])
    assert expected <= observed
    for family in ("reference_pose", "functional_axis", "off_axis_soft",
                   "joint_closure", "temporal_consistency", "prior"):
        assert family in objective["by_residual_family"]
        assert objective["by_residual_family"][family]["count"] > 0


def test_biomechanics_ablation_changes_solution_and_uncertainty_without_retuning() -> None:
    ablation = load("BIOMECHANICS_FACTOR_ABLATION.json")
    assert ablation["identical_initialization"] is True
    assert ablation["retuning_between_runs"] is False
    assert ablation["parameter_vector_difference_l2"] > 0.0
    assert ablation["joint_rest_covariance_difference_frobenius"] > 0.0
    assert ablation["information"]["full_coordinate_rank_nullity"] == [114, 0]
    assert ablation["information"]["ablation_physical_rank_nullity"] == [87, 27]
    assert ablation["nonzero_interpretable_effect_observed"] is True


def test_bone_length_derivations_declare_frames_units_and_are_not_qualified() -> None:
    geometry = load("METRIC_GEOMETRY_CAUSAL_TRACE.json")
    assert geometry["status"] == "DIAGNOSED_PENDING_MEASUREMENTS"
    assert geometry["causal_finding"]["all_action_first_frames_identity"] is True
    for row in geometry["reported_bone_length_definitions"].values():
        assert row["units"] == "metres"
        assert "local segment frame" in row["coordinate_frame"]
        assert row["owning_calibration_slot"].startswith("joint_parent:")
        assert row["status"] == "DIAGNOSTIC_UNQUALIFIED_CONFUNDED_OFFSET_NORM"
    assert geometry["repair"]["metric_skeleton_qualified"] is False


def test_null_anthropometry_does_not_block_orientation_profile() -> None:
    schema = deferred_anthropometry_schema()
    profile = load("DEVELOPMENT_ONLY_REAL_CALIBRATION_CANDIDATE.json")
    assert schema["all_measurement_values_pending"] is True
    assert schema["pending_input_template"]["body_height"]["value_m"] is None
    assert schema["pending_input_template"]["footwear"]["heel_height_m"] is None
    assert profile["anthropometry"]["measured_values_entered"] is False
    assert profile["anthropometry"]["synthetic_values_entered"] is False
    assert profile["capability"]["session_relative_joint_coordinates"].startswith("AVAILABLE")
    bone_rows = [row for row in profile["slots"] if row["slot_id"].startswith("bone_length:")]
    assert all(row["value"] is None for row in bone_rows)


def test_replay_is_complete_immutable_and_not_clinically_labelled() -> None:
    replay = load("CALIBRATION_WINDOW_REPLAY_SUMMARY.json")
    profile = load("DEVELOPMENT_ONLY_REAL_CALIBRATION_CANDIDATE.json")
    access = load("CALIBRATION_WINDOW_ACCESS_AUDIT.json")
    assert replay["rows"] == 2005 and replay["window_count"] == 11
    assert replay["profile_mutated"] is False
    assert replay["clinical_angle_validation_claimed"] is False
    assert replay["profile_checksum_before_replay"] == replay["profile_checksum_after_replay"]
    assert profile_checksum(profile) == profile["profile_checksum_sha256"]
    assert access["held_out_members_or_intervals_opened"] == []
    assert access["Golf_or_Boxing_opened"] is False
    assert all(label.startswith("SESSION_RELATIVE_") for label in REPORTING_LABEL.values())


def test_required_visualizations_exist() -> None:
    for name in (
        "PARENT_CHILD_RELATIVE_ORIENTATIONS_FIXED_SCALE.png",
        "SESSION_RELATIVE_JOINT_TRAJECTORIES_FIXED_SCALE.png",
        "FUNCTIONAL_AXIS_DIRECTION_AND_DISPERSION.png",
        "REFERENCE_POSE_UNCERTAINTY.png",
        "ROOT_RELATIVE_SEGMENT_FRAMES.png",
        "DISCONTINUITY_SIGN_FLIP_FIXED_SCALE.png",
        "METRIC_GEOMETRY_DIAGNOSTIC_UNQUALIFIED.png",
    ):
        assert (RESULT / name).is_file()
