import json
from pathlib import Path

import numpy as np

from biospur_fusion.imu_multi_action_revision_d.segmentation import (
    _bridge, _rotation_distance, _runs, run_segmentation_negative_controls,
)

ROOT = Path(__file__).resolve().parents[3]
CONFIG = ROOT / "Fusion_Part/config/imu_multi_action_revision_d"
CONTRACT = json.loads((CONFIG / "ACTION_BOUNDARY_CONTRACT.json").read_text())
SEMANTICS = json.loads((CONFIG / "TRUNK_PRODUCT_SEMANTICS.json").read_text())


def test_trunk_product_semantics_are_nonclinical_and_frozen_before_segmentation():
    assert not SEMANTICS["clinical_product"]
    assert "orthogonality" in SEMANTICS["claims"]
    assert "fitted_functional_axes" in SEMANTICS["segmentation_forbidden_inputs"]


def test_operator_label_is_outer_search_envelope_only():
    assert CONTRACT["LABEL_USAGE"] == "OUTER_SEARCH_ENVELOPE_ONLY"
    assert CONTRACT["frozen_before_real_signal_inspection"]
    assert not CONTRACT["repetition_semantics"]["protocol_count_is_hard_gate"]


def test_all_frozen_segmentation_negative_controls_pass():
    result = run_segmentation_negative_controls(CONTRACT)
    assert result["terminal_outcome"] == "PASS_SEGMENTATION_NEGATIVE_CONTROLS"
    assert all(result["controls"].values())
    assert not result["real_capture_accessed"]


def test_unbridgeable_invalid_gap_remains_split():
    mask = np.r_[np.ones(8, bool), np.zeros(5, bool), np.ones(8, bool)]
    valid = np.ones_like(mask); valid[8:13] = False
    bridged = _bridge(mask, valid, max_valid_gap=6, max_invalid_gap=2)
    assert len(_runs(bridged)) == 2


def test_short_valid_hysteresis_gap_is_bridgeable():
    mask = np.r_[np.ones(8, bool), np.zeros(2, bool), np.ones(8, bool)]
    valid = np.ones_like(mask)
    assert len(_runs(_bridge(mask, valid, max_valid_gap=3, max_invalid_gap=1))) == 1


def test_production_segmentation_source_has_no_solver_or_historical_endpoint_input():
    source = (ROOT / "Fusion_Part/src/biospur_fusion/imu_multi_action_revision_d/segmentation.py").read_text()
    assert "least_squares" not in source
    assert "ATTEMPT7" not in source.upper()
    assert "ATTEMPT8" not in source.upper()
    assert "EXPECTED_INITIAL" not in source and "EXPECTED_TPOSE" not in source


def test_nonfinite_rotations_remain_invalid_instead_of_entering_so3_svd():
    matrices = np.tile(np.eye(3), (3, 1, 1))
    matrices[1] = np.nan
    distance = _rotation_distance(np.eye(3), matrices)
    assert np.isfinite(distance[[0, 2]]).all()
    assert np.isnan(distance[1])
