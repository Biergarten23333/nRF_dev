#!/usr/bin/env python3
import unittest

import numpy as np

from time_aligner import GRID_US, robust_linear_fit, unwrap_u32


class CounterTests(unittest.TestCase):
    def test_uint32_wrap(self):
        got, gap_events, missing = unwrap_u32([0xFFFFFFFE, 0xFFFFFFFF, 0, 1])
        np.testing.assert_array_equal(got, [0xFFFFFFFE, 0xFFFFFFFF, 0x100000000, 0x100000001])
        self.assertEqual(gap_events, 0)
        self.assertEqual(missing, 0)

    def test_gap_is_preserved(self):
        got, gap_events, missing = unwrap_u32([100, 101, 105, 106])
        np.testing.assert_array_equal(got, [100, 101, 105, 106])
        self.assertEqual(gap_events, 1)
        self.assertEqual(missing, 3)


class FitTests(unittest.TestCase):
    def test_known_ppm_with_outliers(self):
        rng = np.random.default_rng(20260801)
        ppm = 23.5
        x = np.arange(20_000, dtype=float)
        slope = GRID_US * (1.0 + ppm * 1e-6)
        y = 123_456_789.0 + slope * x + rng.normal(0.0, 40.0, x.size)
        y[::997] += 20_000.0
        _, recovered, _ = robust_linear_fit(x, y)
        recovered_ppm = (recovered / GRID_US - 1.0) * 1e6
        self.assertAlmostEqual(recovered_ppm, ppm, delta=0.1)


if __name__ == "__main__":
    unittest.main()
