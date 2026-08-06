import unittest

from delivered_rate import delivered_rate


class DeliveredRateTest(unittest.TestCase):
    def test_host_window_is_not_poisoned_by_device_timestamp_gap(self):
        ts = [0, 120_000, 64_680_000, 64_800_000]
        got = delivered_rate(4, 0.48, ts, stream="uwb", max_rate_hz=1000 / 120)
        self.assertAlmostEqual(got.delivered_rate_hz, 8.3333333333)
        self.assertAlmostEqual(got.median_delta_us, 120_000)
        self.assertLess(got.endpoint_rate_hz, 0.1)

    def test_schedule_ceiling_is_flagged(self):
        got = delivered_rate(101, 10, range(0, 10_100_000, 100_000),
                             stream="uwb", max_rate_hz=1000 / 120)
        self.assertIn("IMPOSSIBLE_UWB_DELIVERY_RATE", got.flags)

    def test_even_small_schedule_excess_is_flagged(self):
        got = delivered_rate(30_001, 3600, (), stream="uwb", max_rate_hz=1000 / 120)
        self.assertIn("IMPOSSIBLE_UWB_DELIVERY_RATE", got.flags)

    def test_imu_grid(self):
        got = delivered_rate(120_000, 600, [0, 5_000, 10_000],
                             stream="imu", max_rate_hz=200)
        self.assertEqual(got.delivered_rate_hz, 200)
        self.assertEqual(got.median_delta_us, 5_000)


if __name__ == "__main__":
    unittest.main()
