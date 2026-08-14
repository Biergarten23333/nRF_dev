import copy
import csv
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from biospur_fusion.visualization.centerline_v1 import (
    DISCLAIMER,
    IDENTITY_MAP,
    NODES,
    VisualizationInputError,
    audit_replay_geometry,
    compile_visualization_inputs,
    read_csv,
    validate_gates,
)
from biospur_fusion.visualization.firewall_v1 import (
    FirewallError,
    VisualizationPayloadFirewall,
    calibration_preview_plan,
    evaluate_calibration_checks,
)
from biospur_fusion.visualization.renderer_v1 import (
    interpolate_for_rendering_only,
    validate_render_sequence,
)


ROOT = Path(__file__).resolve().parents[3]
V1 = ROOT / "Fusion_Part/config/visualization_centerline_v1"
V41 = ROOT / "Fusion_Part/config/body_calibration_v4_1"
DIRECT_VALUES = {
    "acromion_to_lateral_epicondyle_L": 305.0,
    "acromion_to_lateral_epicondyle_R": 307.0,
    "lateral_epicondyle_to_wrist_styloid_midpoint_L": 255.0,
    "lateral_epicondyle_to_wrist_styloid_midpoint_R": 256.0,
    "greater_trochanter_to_lateral_knee_landmark_L": 430.0,
    "greater_trochanter_to_lateral_knee_landmark_R": 432.0,
    "lateral_knee_landmark_to_malleolar_midpoint_L": 410.0,
    "lateral_knee_landmark_to_malleolar_midpoint_R": 411.0,
    "biacromial_breadth": 405.0,
    "ASIS_breadth": 240.0,
    "C7_to_mid_PSIS": 510.0,
    "pelvis_anterior_posterior_depth": 190.0,
}


def _copy_csv(source: Path) -> tuple[list[str], list[dict[str, str]]]:
    with source.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        return list(reader.fieldnames or []), list(reader)


def _write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, str]]) -> Path:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    return path


def synthetic_subject(tmp_path: Path, *, spread_mm: float = 1.0) -> Path:
    fields, rows = _copy_csv(V1 / "v47_visualization_subject_measurements.csv")
    for row in rows:
        if row["measurement_id"] not in DIRECT_VALUES:
            continue
        centre = DIRECT_VALUES[row["measurement_id"]]
        for index, delta in enumerate((-spread_mm / 2.0, 0.0, spread_mm / 2.0), 1):
            row[f"repeat_{index}_mm"] = str(centre + delta)
        row["instrument"] = "synthetic tape"
        row["instrument_resolution_mm"] = "1.0"
        row["actual_measurement_date"] = "2026-08-20"
        row["operator"] = "synthetic operator"
    return _write_csv(tmp_path / "subject.csv", fields, rows)


def synthetic_hardware(tmp_path: Path) -> Path:
    fields, rows = _copy_csv(V1 / "v47_visualization_hardware_measurements.csv")
    numeric = {
        "enclosure_outer_long_mm": 62.0,
        "enclosure_outer_short_mm": 38.0,
        "enclosure_outer_thickness_mm": 15.0,
        "U4_to_E_ANTENNA_END_SHORT_FACE_mm": 21.0,
        "U4_to_E_NONANTENNA_END_SHORT_FACE_mm": 41.0,
        "U4_to_E_POS_Y_LONG_SIDE_mm": 14.0,
        "U4_to_E_NEG_Y_LONG_SIDE_mm": 24.0,
        "PCB_top_plane_to_body_facing_face_mm": 4.0,
        "PCB_mechanical_play_long_mm": 0.5,
        "PCB_mechanical_play_short_mm": 0.4,
        "PCB_mechanical_play_thickness_mm": 0.3,
        "strap_width_mm": 30.0,
    }
    categorical = {
        "pcb_edge_toward_antenna_end": "B_POS_X",
        "pcb_x_edge_relation_to_enclosure_long_axis": "PARALLEL",
        "pcb_top_component_face_points": "AWAY_FROM_BODY",
        "identical_PCB_enclosure_registration": "YES",
    }
    for row in rows:
        name = row["measurement_id"]
        if name in numeric:
            centre = numeric[name]
            for index, delta in enumerate((-0.1, 0.0, 0.1), 1):
                row[f"repeat_{index}_mm"] = str(max(0.0, centre + delta))
            row["instrument"] = "synthetic caliper"
            row["instrument_resolution_mm"] = "0.1"
            row["actual_measurement_date"] = "2026-08-20"
            row["operator"] = "synthetic operator"
        if name in categorical:
            row["categorical_value"] = categorical[name]
            row["actual_measurement_date"] = "2026-08-20"
            row["operator"] = "synthetic operator"
        row["photo_references"] = "synthetic_hardware_faces.jpg"
    return _write_csv(tmp_path / "hardware.csv", fields, rows)


def synthetic_wearing(tmp_path: Path) -> Path:
    fields, rows = _copy_csv(V1 / "v47_shared_wearing_convention.csv")
    categorical = {
        "shared_capture_wearing_evidence_basis": "same strap convention on all nodes",
        "body_facing_enclosure_face": "BOTTOM",
        "enclosure_long_axis_rule": "SEGMENT_LONG_AXIS",
        "antenna_end_direction_rule": "DISTAL",
        "attachment_surface": "LATERAL",
        "attachment_convention": "ENCLOSURE_CENTERED_OVER_GRAPHICAL_LANDMARK",
        "likely_slip": "NONE_REMEMBERED",
    }
    for row in rows:
        name = row["field"]
        if name in categorical:
            row["categorical_value"] = categorical[name]
        if name == "shared_capture_wearing_evidence_basis":
            row["evidence_status"] = "OPERATOR_RECOLLECTION"
            row["evidence_reference"] = "synthetic operator shared-rule recollection"
    return _write_csv(tmp_path / "wearing.csv", fields, rows)


def _inputs(tmp_path: Path) -> tuple[list[dict[str, str]], list[dict[str, str]], list[dict[str, str]], dict, dict]:
    subject = read_csv(synthetic_subject(tmp_path))
    direct_hardware = read_csv(synthetic_hardware(tmp_path))
    wearing = read_csv(synthetic_wearing(tmp_path))
    hardware = json.loads((V41 / "input_preparation/HARDWARE_PROVENANCE.json").read_text())
    gates = json.loads((V1 / "visualization_gates_v1.json").read_text())
    return subject, direct_hardware, wearing, hardware, gates


def _compiled(tmp_path: Path) -> tuple[dict, dict]:
    subject, direct_hardware, wearing, hardware, gates = _inputs(tmp_path)
    return compile_visualization_inputs(subject, direct_hardware, wearing, hardware, gates), gates


def _passing_calibration_report() -> dict:
    return {
        "quotient_observability": {
            "axial_twist_removed": True,
            "minimum_retained_relative_singular_value": 1e-3,
            "finite_null_perturbations_complete": True,
            "all_estimated_placements_in_measurement_jacobian": True,
            "maximum_null_segment_axis_angular_change_rad": 0.0,
            "maximum_null_graphical_node_displacement_mm": 0.0,
            "maximum_null_antenna_displacement_mm": 0.0,
        },
        "geometry_audit": {
            "identity_map_fixed": True,
            "centerline_connected": True,
            "disconnected_frames": [],
            "maximum_bone_length_change_mm": 0.0,
        },
        "placement_posterior_profile": {
            "per_node": {
                node: {
                    "posterior_shift_sigma": 0.5,
                    "minimum_bound_clearance_fraction": 0.5,
                    "bound_hit_disclosed": False,
                }
                for node in NODES
            },
            "profile_maximum_graphical_node_displacement_mm": 1.0,
            "profile_maximum_segment_axis_angular_change_rad": 0.001,
            "profile_maximum_antenna_displacement_mm": 1.0,
        },
        "multistart": {
            "identical_residual_and_weighting": True,
            "maximum_relative_cost_difference": 1e-8,
            "maximum_segment_axis_angular_change_rad": 0.001,
            "maximum_graphical_node_displacement_mm": 1.0,
            "maximum_antenna_displacement_mm": 1.0,
        },
        "interleaved_sampling": {
            "identical_residual_and_weighting": True,
            "maximum_graphical_node_displacement_mm": 1.0,
            "maximum_placement_displacement_mm": 1.0,
            "maximum_antenna_displacement_mm": 1.0,
        },
        "action_removal": {
            "mandatory_action_dependence": {"reported_separately": True, "acceptance_leave_one_out": False},
            "optional_action_removal": {
                "pass": True,
                "identical_residual_and_weighting": True,
                "maximum_segment_axis_angular_change_rad": 0.001,
                "maximum_graphical_node_displacement_mm": 1.0,
                "maximum_antenna_displacement_mm": 1.0,
            },
        },
        "model_mismatch": {
            "normalized_residual_median": 1.0,
            "normalized_residual_p95": 2.0,
        },
    }


def test_visualization_subject_template_has_exactly_twelve_allowed_rows_and_no_scientific_extras():
    _, rows = _copy_csv(V1 / "v47_visualization_subject_measurements.csv")
    assert len(rows) == 12
    assert {row["measurement_id"] for row in rows} == set(DIRECT_VALUES)
    text = (V1 / "v47_visualization_subject_measurements.csv").read_text(encoding="utf-8").lower()
    assert "meskers" not in text
    assert "scapula" not in text
    assert "body_mass" not in text and "height" not in text
    assert "joint_centre" not in text and "joint_center" not in text
    required_columns = {
        "repeat_1_mm", "repeat_2_mm", "repeat_3_mm", "instrument",
        "instrument_resolution_mm", "actual_measurement_date", "operator", "notes",
    }
    assert required_columns <= set(rows[0])
    assert all(not row["actual_measurement_date"] for row in rows)
    _, hardware_rows = _copy_csv(V1 / "v47_visualization_hardware_measurements.csv")
    assert all(not row["actual_measurement_date"] for row in hardware_rows)


def test_blank_operator_templates_parse_to_blocked_without_payload_access():
    subject = read_csv(V1 / "v47_visualization_subject_measurements.csv")
    direct_hardware = read_csv(V1 / "v47_visualization_hardware_measurements.csv")
    wearing = read_csv(V1 / "v47_shared_wearing_convention.csv")
    hardware = json.loads((V41 / "input_preparation/HARDWARE_PROVENANCE.json").read_text())
    gates = json.loads((V1 / "visualization_gates_v1.json").read_text())
    result = compile_visualization_inputs(subject, direct_hardware, wearing, hardware, gates)
    assert result["product_separation"]["VISUALIZATION_CENTERLINE"] == "BLOCKED_OPERATOR_INPUTS_MISSING"
    assert result["geometry"]["status"] == "BLOCKED_OPERATOR_MEASUREMENTS_MISSING"
    assert result["shared_hardware"]["status"] == "BLOCKED_DIRECT_HARDWARE_MEASUREMENTS_MISSING"
    assert result["shared_wearing_and_placement"]["status"] == "BLOCKED_SHARED_WEARING_CONVENTION_MISSING"
    assert not any(result[name] for name in ("capture_payload_opened", "calibration_ledger_opened", "walk_opened", "final_still_opened"))


def test_actual_measurement_dates_are_operator_inputs_and_preserved_verbatim(tmp_path):
    result, _ = _compiled(tmp_path)
    direct = result["geometry"]["direct_measurements"]["values"]
    assert {row["actual_measurement_date"] for row in direct.values()} == {"2026-08-20"}
    hardware = result["shared_hardware"]["direct_hardware_measurements"]
    assert {row["actual_measurement_date"] for row in hardware.values()} == {"2026-08-20"}
    assert "2026-08-14" not in json.dumps(result)


def test_proxy_track_compiles_without_meskers_and_keeps_scientific_verdict(tmp_path):
    result, _ = _compiled(tmp_path)
    assert result["product_separation"] == {
        "SCIENTIFIC_CENTERLINE": "UNCHANGED_FROZEN_V4_1_BLOCKED",
        "VISUALIZATION_CENTERLINE": "INPUTS_READY_CALIBRATION_LEDGER_STILL_SEALED",
    }
    geometry = result["geometry"]["geometry"]
    assert geometry["Meskers_usage"].startswith("NOT_USED")
    assert set(geometry["rendering_lengths"]) == {
        f"rendering_{segment}_length_{side}"
        for segment in ("upper_arm", "forearm", "thigh", "shank")
        for side in ("L", "R")
    }
    assert all(
        not row["internal_anatomical_joint_centre_length"]
        for row in geometry["rendering_lengths"].values()
    )
    assert result["foot_rendering"]["status"] == "BLOCKED_SHOE_GEOMETRY_INCOMPLETE"
    assert result["foot_rendering"]["blocks_visualization_centerline"] is False
    assert not any(result[name] for name in ("capture_payload_opened", "calibration_ledger_opened", "walk_opened", "final_still_opened"))


def test_harrington_and_rf_priors_remain_population_derived_bounded_and_uncertain(tmp_path):
    result, _ = _compiled(tmp_path)
    hips = result["geometry"]["geometry"]["Harrington_hip_proxy"]
    assert hips["population_regression_derived"] is True
    assert all(value > 0 for value in hips["combined_standard_uncertainty_right_anterior_superior_mm"])
    shared = result["shared_hardware"]
    phase = shared["shared_PCB_to_enclosure"]["RF_phase_centre_prior_in_U4_board_frame_mm"]
    assert phase["exact_point"] is False
    assert all(value > 0 for value in shared["shared_PCB_to_enclosure"]["combined_standard_uncertainty_mm"])
    placement = result["shared_wearing_and_placement"]
    assert set(placement["placement_priors"]) == set(NODES)
    assert all("DEFAULT_ENGINEERING_PRIOR" in row["bounds_provenance"] for row in placement["placement_priors"].values())


def test_shared_registration_is_a_full_rigid_transform_not_mixed_frame_vector_addition(tmp_path):
    result, _ = _compiled(tmp_path)
    transform = result["shared_hardware"]["shared_PCB_to_enclosure"]
    assert [value for row in transform["R_E_from_B"] for value in row] == pytest.approx(
        [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0], abs=1e-12
    )
    assert transform["t_E_from_B_origin_mean_mm"] == pytest.approx([10.0, 5.0, -3.5])
    assert "enclosure_origin_to_RF_phase_centre_bounds_E_mm" in transform
    assert "phase_centre_to_enclosure_bounds_mm" not in transform
    assert "operator supplied no Euler angles" in transform["frame_convention"]["rotation"]


def test_changing_observable_edge_or_face_changes_transform_with_right_handed_signs(tmp_path):
    subject, direct_hardware, wearing, hardware, gates = _inputs(tmp_path)
    by_name = {row["measurement_id"]: row for row in direct_hardware}
    by_name["pcb_edge_toward_antenna_end"]["categorical_value"] = "B_POS_Y"
    by_name["pcb_x_edge_relation_to_enclosure_long_axis"]["categorical_value"] = "PERPENDICULAR"
    changed_edge = compile_visualization_inputs(subject, direct_hardware, wearing, hardware, gates)
    rotation = changed_edge["shared_hardware"]["shared_PCB_to_enclosure"]["R_E_from_B"]
    assert [value for row in rotation for value in row] == pytest.approx(
        [0.0, 1.0, 0.0, -1.0, 0.0, 0.0, 0.0, 0.0, 1.0]
    )

    direct_hardware = read_csv(synthetic_hardware(tmp_path))
    by_name = {row["measurement_id"]: row for row in direct_hardware}
    by_name["pcb_top_component_face_points"]["categorical_value"] = "TOWARD_BODY"
    changed_face = compile_visualization_inputs(subject, direct_hardware, wearing, hardware, gates)
    rotation = changed_face["shared_hardware"]["shared_PCB_to_enclosure"]["R_E_from_B"]
    assert [value for row in rotation for value in row] == pytest.approx(
        [1.0, 0.0, 0.0, 0.0, -1.0, 0.0, 0.0, 0.0, -1.0]
    )


def test_repeat_range_and_minimum_rendering_length_are_enforced(tmp_path):
    subject, direct_hardware, wearing, hardware, gates = _inputs(tmp_path)
    subject[0]["repeat_1_mm"] = "0.2"
    subject[0]["repeat_2_mm"] = "0.3"
    subject[0]["repeat_3_mm"] = "0.4"
    with pytest.raises(VisualizationInputError, match="minimum rendering length"):
        compile_visualization_inputs(subject, direct_hardware, wearing, hardware, gates)
    subject = read_csv(synthetic_subject(tmp_path, spread_mm=12.0))
    with pytest.raises(VisualizationInputError, match="repeat range"):
        compile_visualization_inputs(subject, direct_hardware, wearing, hardware, gates)


def test_shared_registration_requires_compact_evidence_and_override_evidence(tmp_path):
    subject, direct_hardware, wearing, hardware, gates = _inputs(tmp_path)
    next(row for row in wearing if row["field"] == "shared_capture_wearing_evidence_basis")["evidence_reference"] = ""
    blocked = compile_visualization_inputs(subject, direct_hardware, wearing, hardware, gates)
    assert "shared_capture_wearing_evidence_basis.evidence_reference" in (
        blocked["shared_wearing_and_placement"]["missing"]
    )

    wearing = read_csv(synthetic_wearing(tmp_path))
    marker = next(row for row in wearing if row["node"] == "BSF31CC")
    marker["categorical_value"] = "YES"
    with pytest.raises(VisualizationInputError, match="invalid evidence status"):
        compile_visualization_inputs(subject, direct_hardware, wearing, hardware, gates)


def test_default_engineering_bounds_cannot_be_labelled_capture_day_or_tightened(tmp_path):
    subject, direct_hardware, wearing, hardware, gates = _inputs(tmp_path)
    default = next(row for row in wearing if row["scope"] == "ENGINEERING_DEFAULT")
    default["evidence_status"] = "MEASURED_CAPTURE_DAY"
    with pytest.raises(VisualizationInputError, match="cannot be labelled MEASURED_CAPTURE_DAY"):
        compile_visualization_inputs(subject, direct_hardware, wearing, hardware, gates)
    wearing = read_csv(synthetic_wearing(tmp_path))
    default = next(row for row in wearing if row["scope"] == "ENGINEERING_DEFAULT")
    default["lower_bound"] = "-20"
    with pytest.raises(VisualizationInputError, match="must not be tightened"):
        compile_visualization_inputs(subject, direct_hardware, wearing, hardware, gates)


def test_geometry_replay_audit_detects_length_change_identity_swap_and_disconnect(tmp_path):
    result, gates = _compiled(tmp_path)
    geometry = result["geometry"]["geometry"]
    frame = copy.deepcopy(geometry["T_pose_proxy_points_right_anterior_superior_mm"])
    audit = audit_replay_geometry(geometry, [frame], IDENTITY_MAP, gates)
    assert audit["pass"]
    changed = copy.deepcopy(frame)
    changed["WristProxy_L"][0] -= 1.0
    assert not audit_replay_geometry(geometry, [changed], IDENTITY_MAP, gates)["pass"]
    swapped = dict(IDENTITY_MAP)
    swapped["BSFAA61"], swapped["BSF1120"] = swapped["BSF1120"], swapped["BSFAA61"]
    assert not audit_replay_geometry(geometry, [frame], swapped, gates)["pass"]
    disconnected = copy.deepcopy(frame)
    disconnected.pop("WristProxy_R")
    assert not audit_replay_geometry(geometry, [disconnected], IDENTITY_MAP, gates)["pass"]


@pytest.mark.parametrize(
    ("path", "value", "failure"),
    [
        (("quotient_observability", "minimum_retained_relative_singular_value"), 0.0, "QUOTIENT_OBSERVABILITY_FAIL"),
        (("quotient_observability", "maximum_null_segment_axis_angular_change_rad"), 1.0, "NULL_AXIS_NOT_INVARIANT"),
        (("quotient_observability", "maximum_null_graphical_node_displacement_mm"), 1.0, "NULL_GRAPHICAL_NODES_NOT_INVARIANT"),
        (("quotient_observability", "maximum_null_antenna_displacement_mm"), 1.0, "NULL_ANTENNA_PREDICTION_NOT_INVARIANT"),
        (("geometry_audit", "maximum_bone_length_change_mm"), 1.0, "BONE_LENGTH_CHANGED"),
        (("placement_posterior_profile", "profile_maximum_graphical_node_displacement_mm"), 20.0, "PLACEMENT_PROFILE_GEOMETRY_UNSTABLE"),
        (("multistart", "maximum_relative_cost_difference"), 1.0, "MULTISTART_COST_UNSTABLE"),
        (("multistart", "maximum_graphical_node_displacement_mm"), 20.0, "MULTISTART_GEOMETRY_UNSTABLE"),
        (("multistart", "maximum_segment_axis_angular_change_rad"), 1.0, "MULTISTART_AXIS_UNSTABLE"),
        (("multistart", "maximum_antenna_displacement_mm"), 20.0, "MULTISTART_ANTENNA_UNSTABLE"),
        (("interleaved_sampling", "maximum_graphical_node_displacement_mm"), 20.0, "INTERLEAVED_GEOMETRY_UNSTABLE"),
        (("interleaved_sampling", "maximum_placement_displacement_mm"), 20.0, "INTERLEAVED_PLACEMENT_UNSTABLE"),
        (("interleaved_sampling", "maximum_antenna_displacement_mm"), 20.0, "INTERLEAVED_ANTENNA_UNSTABLE"),
        (("model_mismatch", "normalized_residual_median"), 4.0, "MODEL_MISMATCH_MEDIAN_FAIL"),
        (("model_mismatch", "normalized_residual_p95"), 9.0, "MODEL_MISMATCH_P95_FAIL"),
    ],
)
def test_each_numeric_calibration_gate_changes_its_decision(tmp_path, path, value, failure):
    _, gates = _compiled(tmp_path)
    report = _passing_calibration_report()
    report[path[0]][path[1]] = value
    audit = evaluate_calibration_checks(report, gates)
    assert failure in audit["failures"]


def test_placement_shift_and_undisclosed_bound_gates_change_decision(tmp_path):
    _, gates = _compiled(tmp_path)
    report = _passing_calibration_report()
    report["placement_posterior_profile"]["per_node"]["BSF31CC"]["posterior_shift_sigma"] = 4.0
    assert "PLACEMENT_POSTERIOR_UNSTABLE:BSF31CC" in evaluate_calibration_checks(report, gates)["failures"]
    report = _passing_calibration_report()
    report["placement_posterior_profile"]["per_node"]["BSF31CC"]["minimum_bound_clearance_fraction"] = 0.0
    assert "PLACEMENT_BOUND_HIT_UNDISCLOSED:BSF31CC" in evaluate_calibration_checks(report, gates)["failures"]
    report = _passing_calibration_report()
    report["placement_posterior_profile"]["profile_maximum_segment_axis_angular_change_rad"] = 1.0
    assert "PLACEMENT_PROFILE_AXIS_UNSTABLE" in evaluate_calibration_checks(report, gates)["failures"]
    report = _passing_calibration_report()
    report["placement_posterior_profile"]["profile_maximum_antenna_displacement_mm"] = 20.0
    assert "PLACEMENT_PROFILE_ANTENNA_UNSTABLE" in evaluate_calibration_checks(report, gates)["failures"]


def test_action_removal_dependence_and_optional_stability_are_separate_gates(tmp_path):
    _, gates = _compiled(tmp_path)
    report = _passing_calibration_report()
    report["action_removal"]["mandatory_action_dependence"]["reported_separately"] = False
    assert "MANDATORY_ACTION_DEPENDENCE_NOT_REPORTED" in evaluate_calibration_checks(report, gates)["failures"]
    report = _passing_calibration_report()
    report["action_removal"]["optional_action_removal"]["pass"] = False
    assert "OPTIONAL_ACTION_REMOVAL_FAIL" in evaluate_calibration_checks(report, gates)["failures"]
    for field, failure in (
        ("maximum_segment_axis_angular_change_rad", "OPTIONAL_ACTION_AXIS_UNSTABLE"),
        ("maximum_graphical_node_displacement_mm", "OPTIONAL_ACTION_GEOMETRY_UNSTABLE"),
        ("maximum_antenna_displacement_mm", "OPTIONAL_ACTION_ANTENNA_UNSTABLE"),
    ):
        report = _passing_calibration_report()
        report["action_removal"]["optional_action_removal"][field] = 100.0
        assert failure in evaluate_calibration_checks(report, gates)["failures"]


def test_calibration_audit_requires_complete_jacobian_placement_and_residual_accounting(tmp_path):
    _, gates = _compiled(tmp_path)
    report = _passing_calibration_report()
    report["quotient_observability"]["finite_null_perturbations_complete"] = False
    assert "FINITE_NULL_PERTURBATIONS_INCOMPLETE" in evaluate_calibration_checks(report, gates)["failures"]
    report = _passing_calibration_report()
    report["quotient_observability"]["all_estimated_placements_in_measurement_jacobian"] = False
    assert "PLACEMENTS_MISSING_FROM_JACOBIAN" in evaluate_calibration_checks(report, gates)["failures"]
    report = _passing_calibration_report()
    report["placement_posterior_profile"]["per_node"].pop("BSF31CC")
    assert "PLACEMENT_POSTERIOR_ACCOUNTING_INCOMPLETE" in evaluate_calibration_checks(report, gates)["failures"]
    for section, expected in (
        ("multistart", "MULTISTART_RESIDUAL_CHANGED"),
        ("interleaved_sampling", "INTERLEAVED_RESIDUAL_CHANGED"),
    ):
        report = _passing_calibration_report()
        report[section]["identical_residual_and_weighting"] = False
        assert expected in evaluate_calibration_checks(report, gates)["failures"]
    report = _passing_calibration_report()
    report["action_removal"]["optional_action_removal"]["identical_residual_and_weighting"] = False
    assert "OPTIONAL_ACTION_RESIDUAL_CHANGED" in evaluate_calibration_checks(report, gates)["failures"]


@pytest.mark.parametrize(
    ("section", "name", "value"),
    [
        ("geometry_gates", "require_fixed_left_right_identity", False),
        ("geometry_gates", "require_connected_centerline", False),
        ("calibration_gates", "quotient_removes_axial_twist", False),
        ("calibration_gates", "placement_at_bound_requires_disclosure", False),
        ("calibration_gates", "mandatory_action_dependence_report_required", False),
        ("calibration_gates", "optional_action_removal_pass_required", False),
        ("rendering", "fixed_axes_required", False),
        ("rendering", "visual_interpolation_analysis_use", "ALLOWED"),
        ("rendering", "watermark_every_frame", False),
        ("firewall", "walk_open_count", 2),
        ("firewall", "final_still", "OPEN"),
    ],
)
def test_contract_gate_weakening_is_rejected(tmp_path, section, name, value):
    _, gates = _compiled(tmp_path)
    weakened = copy.deepcopy(gates)
    weakened[section][name] = value
    with pytest.raises(VisualizationInputError):
        validate_gates(weakened)


def test_firewall_enforces_calibration_preview_walk_once_and_final_still_sealed(tmp_path):
    _, gates = _compiled(tmp_path)
    gate_hash = json.dumps(gates, sort_keys=True)
    firewall = VisualizationPayloadFirewall(gate_hash)
    with pytest.raises(FirewallError, match="previews are accepted"):
        firewall.authorize_walk_once(gate_hash)
    firewall.authorize_calibration_ledger(gate_hash)
    audit = evaluate_calibration_checks(_passing_calibration_report(), gates)
    assert audit["pass"]
    firewall.record_calibration_audit(audit, gate_hash)
    firewall.accept_calibration_previews(gate_hash)
    event = firewall.authorize_walk_once(gate_hash)
    assert event["WALK_HELDOUT_STATUS"] == "CONSUMED_FOR_VISUALIZATION"
    with pytest.raises(FirewallError):
        firewall.authorize_walk_once(gate_hash)
    with pytest.raises(FirewallError, match="final_still remains sealed"):
        firewall.authorize_final_still(gate_hash)
    with pytest.raises(FirewallError, match="hash changed"):
        firewall.authorize_final_still(gate_hash + "changed")
    assert firewall.manifest()["FINAL_STILL_STATUS"] == "SEALED"


def test_preview_and_renderer_guards_use_real_time_fixed_axes_and_watermark(tmp_path):
    result, gates = _compiled(tmp_path)
    plan = calibration_preview_plan(["initial_still_attempt2", "t_pose"], True)
    assert plan["watermark_every_frame"] == DISCLAIMER
    with pytest.raises(FirewallError):
        calibration_preview_plan(["walk"], True)
    frame = result["geometry"]["geometry"]["T_pose_proxy_points_right_anterior_superior_mm"]
    axes = {"x": [-1000, 1000], "y": [-1000, 1000], "z": [-1200, 1200]}
    audit = validate_render_sequence([frame, frame], [1.0, 1.5], "t_pose", axes, gates)
    assert audit["watermark_every_frame"] == DISCLAIMER
    assert audit["fixed_axes_mm"] == axes
    rendered, times = interpolate_for_rendering_only([frame, frame], [1.0, 1.5], 30)
    assert times[0] == 1.0 and times[-1] == pytest.approx(1.5)
    assert rendered and set(rendered[0]) == set(frame)
    with pytest.raises(FirewallError):
        validate_render_sequence([frame], [1.0], "walk", axes, gates)


def test_input_tool_is_deterministic_and_stops_before_every_payload(tmp_path):
    subject = synthetic_subject(tmp_path)
    direct_hardware = synthetic_hardware(tmp_path)
    wearing = synthetic_wearing(tmp_path)
    tool = ROOT / "Fusion_Part/tools/run_visualization_centerline_v1.py"
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(ROOT / "Fusion_Part/src")
    outputs = []
    for name in ("a", "b"):
        output = tmp_path / name
        completed = subprocess.run(
            [
                sys.executable,
                str(tool),
                "--subject-measurements",
                str(subject),
                "--hardware-measurements",
                str(direct_hardware),
                "--wearing-convention",
                str(wearing),
                "--out",
                str(output),
            ],
            check=True,
            capture_output=True,
            text=True,
            env=environment,
        )
        stdout = json.loads(completed.stdout)
        assert stdout["VISUALIZATION_CENTERLINE"] == "INPUTS_READY_CALIBRATION_LEDGER_STILL_SEALED"
        outputs.append((output / "VISUALIZATION_INPUT_READINESS.json").read_bytes())
        report = json.loads(outputs[-1])
        assert report["immutable_binding_checks"] and all(report["immutable_binding_checks"].values())
        assert not any(report[name] for name in ("capture_payload_opened", "calibration_ledger_opened", "walk_opened", "final_still_opened"))
    assert outputs[0] == outputs[1]
