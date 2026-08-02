import unittest

from B306_Part.tools.measure_imu_pull import (
    parse_episode_page,
    parse_publish_hist,
)
from B306_Part.tools.measure_notify_blocking import (
    minimum_circular_span,
    unwrap_low_words,
)


class MeasureImuPullV29ParserTest(unittest.TestCase):
    def test_episode_page_carries_publish_correlation(self):
        page = parse_episode_page(
            "IMU PULL EP p=0 first=0 n=1 total=1 drop=0 "
            "e=12345678:00007F3D:0006:0A0D:7F3D:1"
        )
        self.assertEqual(page["episodes"], [{
            "first_deadline_low_us": 0x12345678,
            "first_lateness_us": 0x7F3D,
            "consecutive_misses": 6,
            "recovery_lateness_us": 0x0A0D,
            "publish_duration_us": 0x7F3D,
            "publish_overlap": 1,
        }])

    def test_publish_histogram_page(self):
        page = parse_publish_hist(
            "IMU PUB HIST p=3 first=24 n=3 h=1,2,3"
        )
        self.assertEqual(page["hist"], [1, 2, 3])

    def test_deadline_low_words_unwrap(self):
        self.assertEqual(
            unwrap_low_words([0xFFFFFFF0, 0x00000010]),
            [0xFFFFFFF0, 0x100000010],
        )

    def test_circular_phase_span_handles_wrap(self):
        self.assertEqual(
            minimum_circular_span([49_900, 100, 200], 1.0),
            300,
        )


if __name__ == "__main__":
    unittest.main()
