import numpy as np
from vqf import VQF

from tools.compare_c2_continuous_input_effect import (
    acceleration_stats,
    window_only_quaternions,
)


def test_no_omitted_records_is_identical():
    acc = np.tile([0.0, 0.0, 9.80665], (100, 1))
    gyro = np.tile([0.01, 0.02, 0.03], (100, 1))
    regions = [{"kind": "ACTION", "start_offset": 0, "stop_offset": 100}]
    selected, actual = window_only_quaternions(acc, gyro, regions, np.arange(100))
    expected = VQF(0.005, magDistRejectionEnabled=False).updateBatch(gyro, acc)["quat6D"]
    assert selected.all()
    np.testing.assert_array_equal(actual, expected)


def test_omitted_rows_are_explicit_and_labels_do_not_reset_state():
    acc = np.tile([0.0, 0.0, 9.80665], (100, 1))
    gyro = np.tile([0.0, 0.0, 0.5], (100, 1))
    regions = [
        {"kind": "ACTION", "start_offset": 0, "stop_offset": 30},
        {"kind": "INTER_ACTION_GAP", "start_offset": 30, "stop_offset": 70},
        {"kind": "ACTION", "start_offset": 70, "stop_offset": 100},
    ]
    selected, actual = window_only_quaternions(acc, gyro, regions, np.arange(100))
    assert selected.sum() == 60
    assert np.isnan(actual[~selected]).all()
    expected = VQF(0.005, magDistRejectionEnabled=False).updateBatch(
        np.ascontiguousarray(gyro[selected]), np.ascontiguousarray(acc[selected]),
    )["quat6D"]
    np.testing.assert_array_equal(actual[selected], expected)


def test_upright_specific_force_has_zero_linear_acceleration():
    result = acceleration_stats(np.tile([0.0, 0.0, 9.80665], (400, 1)),
                                np.tile([1.0, 0.0, 0.0, 0.0], (400, 1)))
    assert result["rms_linear_acceleration_mps2"] == 0
    assert result["nominal_5ms_velocity_increment_mps"] == [0, 0, 0]
