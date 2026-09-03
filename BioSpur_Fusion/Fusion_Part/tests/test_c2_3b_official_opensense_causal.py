from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import opensim as osim

from biospur_fusion.c2_3b_official_opensense.adapter import (
    BODY_BY_SEGMENT,
    IMU_FRAME_BY_SEGMENT,
    configure_opensim_log,
    sha256_file,
)
from biospur_fusion.c2_3b_official_opensense.causal_adapter import (
    BINDING_SHA256,
    INPUT_SHA256_BY_CAPTURE,
    ZERO_MODEL_SHA256,
    configure_deterministic_binding_model,
)


WORKSPACE = Path(__file__).resolve().parents[1]
PRIOR = WORKSPACE / "logs/c2_3b_official_opensense_20260902_094743"
EVIDENCE = WORKSPACE / "logs/c2_3b_official_opensense_causal_20260902_141252"


def _matrix(rotation) -> np.ndarray:
    return np.array(
        [[rotation.get(row, column) for column in range(3)] for row in range(3)]
    )


def test_causal_source_stays_thin_and_official_only():
    source = WORKSPACE / "src/biospur_fusion/c2_3b_official_opensense"
    text = "\n".join(path.read_text() for path in sorted(source.glob("*.py")))
    for forbidden in (
        "scipy.optimize",
        "least_squares",
        "c2_3b_imu_ik",
        "residual_jacobian",
    ):
        assert forbidden not in text


def test_precode_contract_and_immutable_inputs_are_hash_bound():
    binding_path = EVIDENCE / "precode_derivation/DETERMINISTIC_FRAME_BINDING.json"
    zero_model = PRIOR / "real_pilot_00_02/model/c2_official_configured.osim"
    assert sha256_file(binding_path) == BINDING_SHA256
    assert sha256_file(zero_model) == ZERO_MODEL_SHA256
    for capture, expected in INPUT_SHA256_BY_CAPTURE.items():
        source = (
            PRIOR
            / "real_pilot_00_02"
            / capture
            / "input/frozen_orientations.sto"
        )
        assert sha256_file(source) == expected


def test_v2_model_serializes_only_preregistered_proper_rotations(tmp_path):
    configure_opensim_log(tmp_path / "pretest.log")
    binding_path = EVIDENCE / "precode_derivation/DETERMINISTIC_FRAME_BINDING.json"
    zero_model = PRIOR / "real_pilot_00_02/model/c2_official_configured.osim"
    output_model = tmp_path / "v2.osim"
    result = configure_deterministic_binding_model(
        zero_model, binding_path, output_model, tmp_path / "opensim.log"
    )
    assert result["maximum_serialized_rotation_abs_error"] <= 1e-12
    assert result["maximum_serialized_translation_abs_m"] == 0.0

    binding = json.loads(binding_path.read_text())
    model = osim.Model(str(output_model))
    state = model.initSystem()
    for segment, body_name in BODY_BY_SEGMENT.items():
        frame = osim.PhysicalOffsetFrame.safeDownCast(
            model.findComponent(IMU_FRAME_BY_SEGMENT[segment])
        )
        assert frame.getParentFrame().getName() == body_name
        transform = frame.getOffsetTransform()
        expected = np.asarray(binding["segments"][segment]["R_B_from_F"])
        assert np.max(np.abs(_matrix(transform.R()) - expected)) <= 1e-12
        assert np.linalg.det(_matrix(transform.R())) > 0.999999999999
        assert [transform.p()[index] for index in range(3)] == [0.0, 0.0, 0.0]
    for name in ("pro_sup_l", "pro_sup_r"):
        coordinate = model.getCoordinateSet().get(name)
        assert coordinate.getLocked(state)
        assert coordinate.getValue(state) == 0.0
