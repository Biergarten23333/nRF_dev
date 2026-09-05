from pathlib import Path

import numpy as np

from biospur_fusion.c2_uwb_root_world.u0 import ClockModel, UwbRow, predict_state, solve_u0_row
from biospur_fusion.c2_uwb_root_world.calibration import CALIBRATION_ORDER, held_measurement_valid
from biospur_fusion.c2_uwb_root_world.run_calibration import DATASET, LAYOUT, PHYSICAL_DIRECTORY, ROOT


def test_clock_mapping_units():
    clock = ClockModel(0, 1000.0, 2e9, 1e4)
    assert clock.seconds(3e6) == 5.0


def test_predict_constant_velocity():
    x, p = predict_state(np.array([1, 2, 3, 4, 5, 6.0]), np.eye(6), 0.1, np.ones(3))
    np.testing.assert_allclose(x[:3], [1.4, 2.5, 3.6])
    assert np.linalg.eigvalsh(p).min() > 0


def test_exact_synthetic_range_row():
    anchors = np.array([[0, 0, 0], [4, 0, 0], [4, 3, 0], [0, 3, 0],
                        [0, 0, 2], [4, 0, 2], [4, 3, 2], [0, 3, 2]], float)
    truth = np.array([2.0, 1.0, 1.0])
    ranges = tuple(int(round(np.linalg.norm(a - truth) * 1000)) for a in anchors)
    row = UwbRow("BSFC2CC", 0, 1, 1, 1000000, 1000000, tuple(range(8)), ranges,
                 (2000,) * 8, (100,) * 8, 0xff)
    prior = np.array([2.01, 0.99, 1.01, 0, 0, 0], float)
    result = solve_u0_row(row, anchors_m=anchors, clock=ClockModel(0, 1000, 0, 1000),
                          predicted_state=prior, predicted_covariance=np.eye(6),
                          bias_m={}, sigma_history_m={}, sigma_bias_m={},
                          calibration_zero_uncertainty=True)
    assert result.success
    assert result.rank == 3
    assert np.linalg.norm(result.xyz_m - truth) < 0.01


def test_wrong_boot_fails_without_position():
    row = UwbRow("BSFC2CC", 1, 1, 1, 1, 1, tuple(range(8)), (1000,) * 8,
                 (10,) * 8, (100,) * 8, 0xff)
    result = solve_u0_row(row, anchors_m=np.zeros((8, 3)), clock=ClockModel(0, 1000, 0, 1),
                          predicted_state=np.zeros(6), predicted_covariance=np.eye(6),
                          bias_m={}, sigma_history_m={}, sigma_bias_m={})
    assert not result.success
    assert np.isnan(result.xyz_m).all()
    assert result.reason == "CLOCK_BOOT_UNAVAILABLE"


def test_strongly_indefinite_prior_fails_closed():
    anchors = np.array([[0, 0, 0], [4, 0, 0], [4, 3, 0], [0, 3, 0],
                        [0, 0, 2], [4, 0, 2], [4, 3, 2], [0, 3, 2]], float)
    row = UwbRow("BSFC2CC", 0, 1, 1, 1, 1, tuple(range(8)), (2500,) * 8,
                 (10,) * 8, (100,) * 8, 0xff)
    prior = np.eye(6); prior[0, 0] = -1.0
    result = solve_u0_row(row, anchors_m=anchors, clock=ClockModel(0, 1000, 0, 1),
                          predicted_state=np.zeros(6), predicted_covariance=prior,
                          bias_m={}, sigma_history_m={}, sigma_bias_m={},
                          calibration_zero_uncertainty=True)
    assert not result.success
    assert result.reason == "PREDICTED_COVARIANCE_REJECT"


def test_missing_final_uncertainty_fails_closed():
    row = UwbRow("BSFC2CC", 0, 1, 1, 1, 1, tuple(range(8)), (2500,) * 8,
                 (10,) * 8, (100,) * 8, 0xff)
    result = solve_u0_row(row, anchors_m=np.arange(24).reshape(8, 3),
                          clock=ClockModel(0, 1000, 0, 1), predicted_state=np.zeros(6),
                          predicted_covariance=np.eye(6), bias_m={}, sigma_history_m={}, sigma_bias_m={})
    assert not result.success
    assert result.reason == "CALIBRATION_TABLE_INCOMPLETE"


def test_nonfinite_solver_telemetry_fails_closed(monkeypatch):
    import biospur_fusion.c2_uwb_root_world.u0 as module

    class FakeResult:
        x = np.zeros(6); fun = np.zeros(14); jac = np.zeros((14, 6))
        status = 1; nfev = 1; cost = np.nan; optimality = np.nan; message = "nonfinite"

    monkeypatch.setattr(module, "least_squares", lambda *args, **kwargs: FakeResult())
    anchors = np.array([[0, 0, 0], [4, 0, 0], [4, 3, 0], [0, 3, 0],
                        [0, 0, 2], [4, 0, 2], [4, 3, 2], [0, 3, 2]], float)
    row = UwbRow("BSFC2CC", 0, 1, 1, 1, 1, tuple(range(8)), (2500,) * 8,
                 (10,) * 8, (100,) * 8, 0xff)
    result = solve_u0_row(row, anchors_m=anchors, clock=ClockModel(0, 1000, 0, 1),
                          predicted_state=np.ones(6), predicted_covariance=np.eye(6),
                          bias_m={}, sigma_history_m={}, sigma_bias_m={},
                          calibration_zero_uncertainty=True)
    assert not result.success


def test_normative_episode_keys_map_to_physical_directories():
    assert "03_pelvis_hula_circle" in CALIBRATION_ORDER
    assert "10_knee_left_seated" in CALIBRATION_ORDER
    assert PHYSICAL_DIRECTORY["03_pelvis_hula_circle"] == "03_pelvis_tilt_shift"
    assert PHYSICAL_DIRECTORY["10_knee_left_seated"] == "10_knee_left"


def test_calibration_paths_are_owned_by_fusion_part():
    assert ROOT.name == "Fusion_Part"
    assert DATASET.is_dir()
    assert LAYOUT.is_file()


def test_invalid_held_link_is_not_consumable():
    row = UwbRow("BSFC2CC", 0, 1, 1, 1, 1, tuple(range(8)),
                 (1000, 0, 1000, 1000, 1000, 1000, 1000, 1000),
                 (10,) * 8, (100,) * 8, 0xff)
    assert not held_measurement_valid(row, 1)
