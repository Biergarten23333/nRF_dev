import unittest

from phase_c_long_validation import imu_timing


class ImuTimingTests(unittest.TestCase):
    def test_effective_rate_uses_sample_timeline(self):
        lines = [
            "FUSION_IMU base_us=1000 samples=0,1;5000,2",
            "FUSION_IMU base_us=11000 samples=0,3;5000,4",
        ]

        timing = imu_timing(lines)

        self.assertEqual(timing["timestamp_span_us"], 15000)
        self.assertEqual(timing["deadline_slots_skipped"], 0)
        self.assertEqual(timing["effective_sample_rate_hz"], 200.0)

    def test_skipped_deadline_reduces_effective_rate(self):
        lines = [
            "FUSION_IMU base_us=1000 samples=0,1;5000,2",
            "FUSION_IMU base_us=16000 samples=0,3;5000,4",
        ]

        timing = imu_timing(lines)

        self.assertEqual(timing["timestamp_span_us"], 20000)
        self.assertEqual(timing["deadline_slots_skipped"], 1)
        self.assertEqual(timing["effective_sample_rate_hz"], 150.0)


if __name__ == "__main__":
    unittest.main()
