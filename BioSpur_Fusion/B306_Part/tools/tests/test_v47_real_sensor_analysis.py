import sys
import unittest
from pathlib import Path

import numpy as np

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))

from analyze_v47_real_sensor_static import (  # noqa: E402
    classify_synthetic_event,
    contiguous,
    overlapping_allan,
    robust_threshold,
    stable_sample_mask,
)
from v47_real_data_adapter import (  # noqa: E402
    IMU_DTYPE,
    UWB_DTYPE,
    imu_physical,
    sequence_gap_count,
)


class RealSensorAnalysisTests(unittest.TestCase):
    def test_imu_unit_conversion_source_contract(self):
        row = np.zeros(1, dtype=IMU_DTYPE)
        row["acc"] = (2048, -2048, 1024)
        row["gyro"] = (16384, -16384, 0)
        row["temp_raw"] = 2534
        acc, gyro, temp = imu_physical(row)
        np.testing.assert_allclose(acc[0], [1, -1, .5])
        np.testing.assert_allclose(gyro[0], [1000, -1000, 0])
        np.testing.assert_allclose(temp, [25.34])

    def test_timestamp_order_and_sequence_wrap(self):
        values = np.array([65534, 65535, 0, 1], dtype=np.uint16)
        self.assertEqual(sequence_gap_count(values, 65536), 0)
        self.assertEqual(sequence_gap_count(np.array([1, 3], dtype=np.uint16), 65536), 1)

    def test_uwb_eight_slots_and_invalid_slot_preserved(self):
        row = np.zeros(1, dtype=UWB_DTYPE)
        row["anchor_id"] = np.arange(8)
        row["range_mm"] = np.arange(100, 108)
        row["valid_mask"] = 0b01111111
        self.assertEqual(row["range_mm"].shape, (1, 8))
        self.assertEqual(row["range_mm"][0, 7], 107)
        self.assertFalse(bool(row["valid_mask"][0] & (1 << 7)))

    def test_static_segment_boundary_and_no_concatenation_for_allan(self):
        mask = np.array([0, 1, 1, 0, 1, 1, 1, 0], dtype=bool)
        self.assertEqual(contiguous(mask, 2), [(1, 3), (4, 7)])
        times = np.arange(8, dtype=float)
        np.testing.assert_array_equal(stable_sample_mask(times, mask), mask)
        result = overlapping_allan(np.arange(100, dtype=float), 10, 2)
        self.assertTrue(result)
        self.assertLessEqual(max(tau for tau, _ in result), 2)

    def test_synthetic_change_point_classes(self):
        self.assertEqual(classify_synthetic_event(7, 2, .1, 0), "TABLE_COMMON_MODE_VIBRATION")
        self.assertEqual(classify_synthetic_event(1, 80, 2, 5), "SINGLE_NODE_REPOSITION_OR_ROTATION")
        self.assertEqual(classify_synthetic_event(0, 1, .1, 1), "UWB_RF_VISIBILITY_CHANGE")

    def test_bsf6c53_low_listener_rate_is_not_motion_input(self):
        self.assertNotEqual(classify_synthetic_event(0, .7, .01, 0), "SINGLE_NODE_REPOSITION_OR_ROTATION")

    def test_robust_threshold_resists_single_outlier(self):
        values = np.r_[np.ones(100), 1000]
        self.assertLess(robust_threshold(values, floor=.5), 2)


if __name__ == "__main__":
    unittest.main()
