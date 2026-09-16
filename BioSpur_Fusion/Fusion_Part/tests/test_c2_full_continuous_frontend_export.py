import numpy as np
import pytest
from types import SimpleNamespace
from vqf import VQF

from tools.export_c2_full_continuous_frontend import orient_continuous, raw_region_index


def test_byte_region_ownership_does_not_reject_measurement_outside_label_time():
    regions = (SimpleNamespace(start_offset=100, stop_offset=200, start_ns=1000, stop_ns=2000),)
    raw = SimpleNamespace(start_offset=100, end_offset=120)
    event = SimpleNamespace(common_global_ns=999, raw=raw)
    assert raw_region_index(regions, (100,), event.raw) == 0


def test_all_samples_including_unlabelled_intervals_use_one_vqf():
    time = np.arange(30, dtype=np.int64) * 5000
    time[20:] += 10000  # A real source dropout, unlike a label boundary.
    acc_raw = np.tile([0, 0, 2048], (30, 1))
    gyro_raw = np.tile([20, 30, 40], (30, 1))
    acc, gyro, quat, spans = orient_continuous(time, np.zeros(30), acc_raw, gyro_raw, np.zeros(3))
    reference = VQF(0.005, magDistRejectionEnabled=False)
    expected = reference.updateBatch(gyro, acc)["quat6D"]
    np.testing.assert_allclose(quat, expected, atol=1e-14)
    assert len(quat) == 30
    assert np.array_equal(spans, np.r_[np.zeros(20), np.ones(10)])


def test_fixed_initial_bias_is_applied_in_si_units():
    bias = np.array([0.1, 0.2, 0.3])
    acc, gyro, _, _ = orient_continuous(np.arange(3) * 5000, np.zeros(3),
        np.tile([0, 0, 2048], (3, 1)), np.zeros((3, 3)), bias)
    np.testing.assert_allclose(gyro, np.tile(-bias, (3, 1)))
    np.testing.assert_allclose(acc[:, 2], 9.80665)


def test_boot_change_fails_instead_of_silent_reset():
    with pytest.raises(ValueError, match="single-boot"):
        orient_continuous([0, 5000], [0, 1], np.zeros((2, 3)), np.zeros((2, 3)), np.zeros(3))
