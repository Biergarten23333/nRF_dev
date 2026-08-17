from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]


def config():
    return json.loads((ROOT / "config/fusion_v2/phase2r/PHASE2R_PRODUCTION_CONFIG.json").read_text())


def test_production_selection_is_fail_closed():
    c = config()
    assert c["dataset_access"]["phase2_promoted_windows"] == 19
    assert c["dataset_access"]["recursive_glob"] is False
    assert c["dataset_access"]["holdout_numeric_access"] is False
    assert c["dataset_access"]["mapping_pretruth_access"] is False


def test_unsupported_factors_remain_structurally_disabled():
    c = config()["conditional_calibration"]
    assert c["dynamic_accelerometer_factor"] is False
    assert c["metric_uwb_factor"] is False
    assert c["phase1_orientation_factor"] is False
    assert c["full_extrinsic_freeze"] is False


def test_mounting_prior_cannot_pool_distinct_layout_or_guess_axis():
    c = config()["mounting_prior"]
    assert set(c["H9"]).isdisjoint(c["distinct_layout"])
    assert c["distinct_layout"] == ["BSFC2CC"]
    assert c["named_sensor_axis"] == "UNRESOLVED"
    assert c["hard_equality"] is False
    assert c["per_node_sigma_rad"] > 0
    assert c["use"] == "diagnostic_initializer_only"


def test_accelerometer_lineage_has_one_active_likelihood_layer():
    c = config()["conditional_calibration"]
    active = int(c["low_dynamic_specific_force_factor"]) + int(config()["association"]["mounting_prior_factor_count"] > 0)
    assert active == 1
