import csv
import importlib.util
import json
from argparse import Namespace
from pathlib import Path

import pytest


TOOL_PATH = (
    Path(__file__).resolve().parents[2]
    / "tools/prepare_body_calibration_v4_1_inputs.py"
)
SPEC = importlib.util.spec_from_file_location("prepare_body_calibration_v4_1_inputs", TOOL_PATH)
assert SPEC and SPEC.loader
prep = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(prep)

CONFIG = Path(__file__).resolve().parents[2] / "config/body_calibration_v4_1"
CAD = Path("/home/zekaixiao/Downloads/ProPrj_eFlake_Synapse_2026-08-13.epro")
DATASHEET = Path(
    "/home/zekaixiao/Documents/Datasheets/DW1000 EVK/"
    "DWM1001_DWM1001-DEV_MDEK1001_Sources_and_Docs_v11/"
    "DWM1001/Product_and_Design_Documents/DWM1001C_Datasheet.pdf"
)


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

SCAPULA_R = {
    "AC": (190.0, 20.0, 1490.0),
    "AA": (200.0, 0.0, 1500.0),
    "TS": (50.0, 0.0, 1450.0),
    "AI": (80.0, 0.0, 1100.0),
    "PC": (160.0, 50.0, 1450.0),
}


def _write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def synthetic_subject_csv(tmp_path: Path) -> Path:
    source = CONFIG / "v47_subject_measurement_form.csv"
    with source.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        fieldnames = list(reader.fieldnames or [])
        rows = list(reader)
    for row in rows:
        name = row["measurement_id"]
        row["instrument"] = "synthetic caliper"
        row["instrument_resolution_mm"] = "0.5"
        row["source_reference"] = "synthetic fixture"
        if name in DIRECT_VALUES:
            center = DIRECT_VALUES[name]
            for index, delta in enumerate((-0.5, 0.0, 0.5), 1):
                row[f"repeat_{index}_mm"] = str(center + delta)
        elif name.startswith("scapula_"):
            _, side, landmark, axis = name.split("_")
            coordinate = SCAPULA_R[landmark]["xyz".index(axis)]
            if side == "L" and axis == "x":
                coordinate *= -1.0
            translations = ((0.0, 0.0, 0.0), (2.0, -1.0, 3.0), (-1.0, 2.0, -2.0))
            for index, shift in enumerate(translations, 1):
                row[f"repeat_{index}_mm"] = str(coordinate + shift["xyz".index(axis)])
    output = tmp_path / source.name
    _write_csv(output, fieldnames, rows)
    return output


def synthetic_shoe_csv(tmp_path: Path) -> Path:
    source = CONFIG / "v47_shoe_measurement_form.csv"
    with source.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        fieldnames = list(reader.fieldnames or [])
        rows = list(reader)
    centers = {
        "foot_length_L": 265.0,
        "foot_length_R": 266.0,
        "floor_to_malleolar_midpoint_L": 92.0,
        "floor_to_malleolar_midpoint_R": 93.0,
        "rear_heel_stack_height_L": 70.0,
        "rear_heel_stack_height_R": 71.0,
        "forefoot_stack_height_L": 24.0,
        "forefoot_stack_height_R": 25.0,
    }
    for row in rows:
        name = row["measurement_id"]
        row["instrument"] = "synthetic caliper"
        row["instrument_resolution_mm"] = "0.5"
        row["shoe_identity"] = "synthetic capture shoe"
        row["photo_references"] = "synthetic_shoe.jpg"
        row["source_reference"] = "synthetic fixture"
        if name in centers:
            for index, delta in enumerate((-0.5, 0.0, 0.5), 1):
                row[f"repeat_{index}_mm"] = str(centers[name] + delta)
    output = tmp_path / source.name
    _write_csv(output, fieldnames, rows)
    return output


def synthetic_questionnaire(tmp_path: Path) -> Path:
    source = CONFIG / "v47_capture_placement_questionnaire.md"
    text = source.read_text(encoding="utf-8")
    text = text.replace("- Evidence status:", "- Evidence status: PHOTO_DERIVED")
    text = text.replace(
        "- Capture-day photo/video reference and visible scale:",
        "- Capture-day photo/video reference and visible scale: synthetic_capture_day.jpg; calibrated ruler visible",
    )
    output = tmp_path / source.name
    output.write_text(text, encoding="utf-8")
    return output


def test_repeated_measurements_are_retained_and_uncertainty_is_computed(tmp_path):
    rows = prep.read_csv(synthetic_subject_csv(tmp_path))
    summary = prep.summarize_subject(rows)
    value = summary["values"]["ASIS_breadth"]
    assert value["raw_repeats"] == [239.5, 240.0, 240.5]
    assert value["mean"] == pytest.approx(240.0)
    assert value["combined_measurement_standard_uncertainty"] > 0


def test_harrington_derivation_has_explicit_convention_and_propagation(tmp_path):
    direct = prep.summarize_subject(prep.read_csv(synthetic_subject_csv(tmp_path)))
    hips = prep.derive_hips(direct)
    assert hips["status"] == "DERIVED"
    assert hips["right_HJC_from_mid_ASIS_right_anterior_superior_mm"] == pytest.approx(
        [86.5, -55.5, -82.9]
    )
    assert hips["left_HJC_from_mid_ASIS_right_anterior_superior_mm"][0] == pytest.approx(-86.5)
    assert hips["combined_standard_uncertainty_mm"]["right"] > hips["measurement_only_standard_uncertainty_mm"]["right"]
    assert "mid-ASIS" in hips["derivation"]["joint_centre_convention"]


def test_meskers_derivation_is_bilateral_and_translation_invariant(tmp_path):
    rows = prep.read_csv(synthetic_subject_csv(tmp_path))
    shoulders = prep.derive_shoulders(rows)
    assert shoulders["status"] == "DERIVED"
    for side in ("L", "R"):
        value = shoulders["sides"][side]
        assert value["status"] == "DERIVED"
        local = [entry["local_GH_xyz_mm"] for entry in value["per_pass"]]
        assert local[0] == pytest.approx(local[1], abs=1e-9)
        assert local[0] == pytest.approx(local[2], abs=1e-9)
        assert all(item > 0 for item in value["combined_standard_uncertainty_local_xyz_mm"])
    assert shoulders["derived_scalars"]["shoulder_joint_centre_width"]["value_mm"] > 0


def test_surface_chords_are_not_relabelled_internal_lengths(tmp_path):
    rows = prep.read_csv(synthetic_subject_csv(tmp_path))
    direct = prep.summarize_subject(rows)
    derived = prep.derive_b_schema(direct, prep.derive_hips(direct), prep.derive_shoulders(rows))
    assert derived["v4_1_scalar_candidates"]["upper_arm_joint_centre_length_L"]["status"].startswith("BLOCKED")
    assert not derived["all_frozen_schema_scalars_ready"]


def test_shoe_elevation_is_paired_derivation_not_nominal_description(tmp_path):
    shoes = prep.summarize_shoes(prep.read_csv(synthetic_shoe_csv(tmp_path)))
    assert shoes["rendering_ready"]
    assert shoes["derived"]["heel_minus_forefoot_elevation_L"]["value_mm"] == pytest.approx(46.0)
    assert shoes["derived"]["heel_minus_forefoot_elevation_R"]["value_mm"] == pytest.approx(46.0)


def test_hardware_audit_preserves_nonzero_bound_and_blocks_missing_enclosure():
    if not CAD.is_file() or not DATASHEET.is_file():
        pytest.skip("authoritative local design evidence is unavailable")
    audit = prep.audit_shared_hardware(CAD, DATASHEET)
    assert audit["cad"]["sha256"] == prep.CAD_EXPECTED_SHA256
    assert audit["datasheet"]["sha256"] == prep.DWM_DATASHEET_EXPECTED_SHA256
    assert audit["status"] == "BLOCKED_SHARED_TRANSFORM_INCOMPLETE"
    assert audit["pcb_phase_centre_to_enclosure"] is None
    assert all(value > 0 for value in audit["rf_phase_centre_prior"]["one_sigma_uniform_whole_package_envelope_mm"])
    assert audit["assembly_tolerance_prior"]["one_sigma_mm"] is None


def test_input_guard_rejects_capture_like_path(tmp_path):
    forbidden = tmp_path / "logs" / "v47_subject_measurement_form.csv"
    forbidden.parent.mkdir()
    forbidden.write_text("x\n", encoding="utf-8")
    with pytest.raises(prep.InputError, match="forbidden"):
        prep.assert_safe_operator_input(forbidden)


def test_placement_status_cannot_exceed_its_evidence(tmp_path):
    source = CONFIG / "v47_capture_placement_questionnaire.md"
    text = source.read_text(encoding="utf-8").replace(
        "- Evidence status:", "- Evidence status: MEASURED_CAPTURE_DAY"
    )
    path = tmp_path / source.name
    path.write_text(text, encoding="utf-8")
    inventory = prep.placement_inventory(path)
    assert not inventory["all_nodes_have_allowed_status"]
    assert set(inventory["invalid_statuses"]) == set(inventory["node_statuses"])


def test_complete_synthetic_input_preparation_is_byte_deterministic(tmp_path):
    subject = synthetic_subject_csv(tmp_path)
    shoe = synthetic_shoe_csv(tmp_path)
    questionnaire = synthetic_questionnaire(tmp_path)
    common = dict(
        subject_csv=subject,
        shoe_csv=shoe,
        placement_questionnaire=questionnaire,
        cad_source=CAD if CAD.is_file() else None,
        dwm_datasheet=DATASHEET if DATASHEET.is_file() else None,
    )
    out_a, out_b = tmp_path / "a", tmp_path / "b"
    report_a = prep.prepare(Namespace(out=out_a, **common))
    report_b = prep.prepare(Namespace(out=out_b, **common))
    assert report_a == report_b
    assert {path.name: path.read_bytes() for path in out_a.iterdir()} == {
        path.name: path.read_bytes() for path in out_b.iterdir()
    }
    report = json.loads((out_a / "INPUT_PREPARATION_REPORT.json").read_text(encoding="utf-8"))
    assert report["calibration_opened"] is False
    assert report["held_out_opened"] is False
    assert report["raw_payload_opened"] is False
