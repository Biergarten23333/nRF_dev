import json
from pathlib import Path

import numpy as np
from scipy.spatial.transform import Rotation

from biospur_fusion.imu_multi_action_revision_d.r3c_activity import (
    lowest_activity_plateau,
    primary_activity,
    process_rate_floor,
    q2_through_synthetic_qualification,
)


ROOT = Path(__file__).resolve().parents[3]
CONTRACT = json.loads((ROOT / "Fusion_Part/config/imu_multi_action_revision_d_r3c/R3C_ACTIVITY_MODEL_CONTRACT.json").read_text())
Q2 = json.loads((ROOT / "Fusion_Part/config/imu_multi_action_engineering_preview_v1/gates_v1.json").read_text())["q2"]


def test_frozen_activity_threshold_and_floor_are_not_result_tuned():
    assert CONTRACT["active_bout"]["onset_activity_z"] == 4.0
    assert CONTRACT["normalization"]["production_floor_rad_s"] == 0.035
    assert CONTRACT["normalization"]["absolute_q2_covariance_as_rate_denominator"] is False


def test_process_floor_has_rate_units_and_one_dt_propagation():
    floor = process_rate_floor(np.array([np.nan, 0.02]), CONTRACT)
    assert np.isnan(floor[0])
    assert abs(floor[1] - 0.03) < 1e-15


def test_empirical_baseline_does_not_consume_absolute_covariance():
    n = 100
    t = np.arange(n, dtype=np.int64) * 20_000_000
    angle = 0.001 * np.sin(np.arange(n) * 0.2)
    parent = np.tile(np.eye(3), (n, 1, 1))
    child = Rotation.from_rotvec(np.c_[np.zeros(n), np.zeros(n), angle]).as_matrix()
    relative = np.einsum("nji,njk->nik", parent, child)
    activity = primary_activity(t, relative, np.ones(n, bool), CONTRACT)
    baseline = lowest_activity_plateau(activity, np.arange(n), CONTRACT)
    assert baseline is not None
    assert baseline["activity_scale_rad_s"] == 0.035


def test_raw_q2_synthetic_qualification_passes_all_controls():
    result = q2_through_synthetic_qualification(CONTRACT, Q2)
    assert result["pass"], {key: value for key, value in result["controls"].items() if not value}
    assert result["negative_controls"]["old_independent_absolute_covariance_scale_rad_s"] > 100.0
    assert result["absolute_yaw_uncertainty_variant"]["yaw360_covariance_trace_p50_rad2"] > result["absolute_yaw_uncertainty_variant"]["yaw180_covariance_trace_p50_rad2"]
