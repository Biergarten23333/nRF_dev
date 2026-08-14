import json
from pathlib import Path

from biospur_fusion.calibration.anthropometry import (
    REQUIRED_OFFSETS, REQUIRED_SCALARS, validate_anthropometry,
)


def test_repository_v47_anthropometry_fails_closed():
    repo_root = Path(__file__).resolve().parents[3]
    path = repo_root/"Fusion_Part/config/body_calibration_v4/v47_subject_anthropometry_v1.json"
    value, audit = validate_anthropometry(path)
    assert value is None and not audit["complete"]
    assert "shoe_condition" in audit["missing"]
    assert len(audit["missing"]) == 1 + len(REQUIRED_SCALARS) + len(REQUIRED_OFFSETS)


def test_complete_versioned_anthropometry_accepts_signed_hip_offset(tmp_path):
    measurements = {}
    for name in REQUIRED_SCALARS:
        measurements[name] = {
            "value": -.04 if name == "hip_vertical_offset_m" else .3,
            "uncertainty": .005, "units": "m", "landmark_definition": "fixture",
            "measurement_method": "synthetic tape", "status": "MEASURED"}
    offsets = {name: {"value": [0, 0, 0], "uncertainty": .003, "units": "m",
                      "landmark_definition": "fixture board frame",
                      "measurement_method": "synthetic survey", "status": "MEASURED"}
               for name in REQUIRED_OFFSETS}
    path = tmp_path/"anthropometry.json"
    path.write_text(json.dumps({"schema": "biospur-subject-anthropometry-v1", "subject_session": "x",
                                "shoe_condition": {"status": "BAREFOOT", "description": "fixture"},
                                "measurements": measurements, "sensor_to_landmark_offsets": offsets}))
    value, audit = validate_anthropometry(path)
    assert audit["complete"] and value is not None
    assert value.scalars_m["hip_vertical_offset_m"] == -.04
