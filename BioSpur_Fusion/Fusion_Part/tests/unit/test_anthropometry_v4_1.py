import json
from pathlib import Path

from biospur_fusion.calibration.anthropometry_v4_1 import (
    DERIVED_SOLVER_REQUIRED,
    DIRECT_SOLVER_REQUIRED,
    NODE_TO_SEGMENT,
    RENDERING_REQUIRED,
    validate_anthropometry_v4_1,
)


def repository_input_path() -> Path:
    return (Path(__file__).resolve().parents[3]
            / "Fusion_Part/config/body_calibration_v4_1/v47_subject_inputs_v4_1.json")


def complete_solver_payload() -> dict:
    payload = json.loads(repository_input_path().read_text(encoding="utf-8"))
    for row in payload["A_DIRECT_SURFACE_MEASUREMENT"].values():
        row.update(status="MEASURED", raw_value=0.3, uncertainty=0.004, source="synthetic fixture")
    for name, row in payload["B_DERIVED_JOINT_CENTER"].items():
        row.update(
            status="DERIVED",
            value=-0.04 if name == "hip_joint_centre_vertical_offset" else 0.3,
            uncertainty=0.006,
            derivation_name="fixture-functional-joint-centre",
            derivation_version="1.0",
        )
    for row in payload["C_SENSOR_PLACEMENT"].values():
        row["pcb_phase_centre_to_enclosure"].update(
            status="PHOTO_DERIVED",
            value=[0.0, 0.0, 0.0],
            uncertainty=[0.001, 0.001, 0.001],
            source="synthetic CAD fixture",
        )
        row["capture_enclosure_to_landmark"].update(
            status="CALIBRATION_ESTIMATED",
            value=[0.0, 0.0, 0.0],
            uncertainty=[0.005, 0.005, 0.005],
            lower_bound=[-0.05, -0.05, -0.05],
            upper_bound=[0.05, 0.05, 0.05],
            source="synthetic strap-envelope fixture",
            estimate_as_nuisance=True,
        )
    return payload


def test_repository_historical_input_fails_before_solver_without_fabrication():
    value, audit = validate_anthropometry_v4_1(repository_input_path())
    assert value is None
    assert not audit["solver_complete"]
    assert len([name for name in audit["solver_missing"] if name.startswith("A_")]) == len(DIRECT_SOLVER_REQUIRED)
    assert len([name for name in audit["solver_missing"] if name.startswith("B_")]) == len(DERIVED_SOLVER_REQUIRED)
    assert len([name for name in audit["solver_missing"] if name.startswith("C_")]) == 2 * len(NODE_TO_SEGMENT)
    assert audit["foot_rendering"]["verdict"] == "BLOCKED_SHOE_GEOMETRY_INCOMPLETE"
    assert not audit["foot_rendering"]["blocks_centerline_solver"]


def test_rendering_only_missing_does_not_block_centerline_solver(tmp_path):
    payload = complete_solver_payload()
    path = tmp_path / "inputs.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    value, audit = validate_anthropometry_v4_1(path)
    assert value is not None and audit["solver_complete"]
    assert audit["foot_rendering"]["verdict"] == "BLOCKED_SHOE_GEOMETRY_INCOMPLETE"
    assert value.uncertainty_mode == "FIXED_INPUTS_NOT_PROPAGATED"
    assert "excludes" in audit["anthropometric_uncertainty"]["statement"]


def test_rendering_verdict_changes_without_changing_solver_readiness(tmp_path):
    payload = complete_solver_payload()
    before = tmp_path / "before.json"
    before.write_text(json.dumps(payload), encoding="utf-8")
    value_before, audit_before = validate_anthropometry_v4_1(before)
    for name in RENDERING_REQUIRED:
        payload["D_RENDERING_ONLY"][name].update(
            status="MEASURED",
            raw_value=0.1,
            uncertainty=0.003,
            shoe_condition="BAREFOOT",
            source="synthetic rendering fixture",
        )
    after = tmp_path / "after.json"
    after.write_text(json.dumps(payload), encoding="utf-8")
    value_after, audit_after = validate_anthropometry_v4_1(after)
    assert value_before is not None and value_after is not None
    assert audit_before["solver_complete"] == audit_after["solver_complete"]
    assert audit_after["foot_rendering"]["verdict"] == "PASS"


def test_internal_joint_centres_require_named_versioned_derivation(tmp_path):
    payload = complete_solver_payload()
    row = payload["B_DERIVED_JOINT_CENTER"]["hip_joint_centre_width"]
    row["derivation_name"] = None
    path = tmp_path / "invalid.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    value, audit = validate_anthropometry_v4_1(path)
    assert value is None
    assert "B_DERIVED_JOINT_CENTER.hip_joint_centre_width.derivation_provenance" in audit["solver_invalid"]
