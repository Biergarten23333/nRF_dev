from __future__ import annotations

import numpy as np

from biospur_fusion.root_r4.frame import fit_centered_yaw, fit_t4_yaw, t4_observability
from biospur_fusion.root_r4.synthetic import frame_goldens


def test_real_lineage_exact(c1):
    data, audit = c1
    assert audit["exact_constituent_t4_events"] == data.event_count
    assert audit["conservative_envelope_t4_events"] == 0
    assert audit["unresolved_t4_events"] == 0
    assert audit["checks"]["t4_xyz_bit_exact"]
    assert audit["checks"]["t4_used_mask_exact"]


def test_real_raw_clock_causal(c1):
    data, _ = c1
    assert np.all(data.raw_measurement_s[data.raw_valid] <= data.raw_availability_s[data.raw_valid] + 1e-12)
    assert np.all(data.anchor_id == np.arange(8))
    assert data.raw_root_relative_n_m.shape == (data.event_count, 8, 3)
    event_geometry = np.all(np.isfinite(data.root_relative_n_m), axis=1)
    assert np.all(np.isfinite(data.raw_root_relative_n_m[event_geometry][data.raw_valid[event_geometry]]))
    assert np.sum(~event_geometry) == 8


def test_synthetic_frame_goldens_are_truth_based():
    result = frame_goldens()
    assert result["passed"]
    assert result["cases"]["clean_exact"]["yaw_error_deg"] < 0.1
    assert result["counterfactual_objective_ratios"]["wrong_90_ratio"] > 1.25
    assert result["cases"]["one_corrupted_tag"]["rejected_tag"] == 3
    assert result["cases"]["one_corrupted_tag"]["recovered_yaw_error_deg"] < 2.0
    assert result["wrong_anchor_identity"]["detected"]


def test_degenerate_yaw_has_zero_information():
    x = np.zeros((100, 3)); y = np.zeros((100, 3)); yaw, residual = fit_centered_yaw(x, y)
    assert yaw == 0.0
    assert np.sum(x[:, :2] ** 2) == 0.0
    assert np.all(residual == 0.0)


def test_real_frame_is_global_and_locally_ranked(c1):
    data, _ = c1; fit = fit_t4_yaw(data); audit = t4_observability(data, fit)
    assert audit["tags"] == 10
    assert audit["epochs"] > 9000
    assert all(rank == 1 for rank in audit["yaw_rank_by_tolerance"].values())
