import unittest
from connection_epoch_health import ConnectionEpochLoss, StreamLiveness


class TestConnectionEpochLoss(unittest.TestCase):
    def test_first_snapshot_is_baseline_not_failure(self):
        x = ConnectionEpochLoss(); x.connected("BSF0001")
        self.assertEqual(x.observe("BSF0001", {"q_drop_uwb": 99, "q_drop_imu": 500}),
                         {"uwb": None, "imu": None})
        self.assertEqual(x.observe("BSF0001", {"q_drop_uwb": 99, "q_drop_imu": 502}),
                         {"uwb": 0, "imu": 2})

    def test_reconnect_rebaselines_cumulative_counters(self):
        x = ConnectionEpochLoss(); x.connected("BSF0001")
        x.observe("BSF0001", {"q_drop_uwb": 5, "q_drop_imu": 7})
        x.disconnected("BSF0001"); x.connected("BSF0001")
        self.assertEqual(x.observe("BSF0001", {"q_drop_uwb": 0, "q_drop_imu": 64}),
                         {"uwb": None, "imu": None})


class TestStreamLiveness(unittest.TestCase):
    def test_uwb_live_imu_dead_alarm_and_recovery(self):
        x = StreamLiveness(threshold_s=3.0); x.connected("BSF0001", 0.0)
        x.note("BSF0001", "uwb", 4.0)
        self.assertIsNotNone(x.check("BSF0001", 4.0))
        self.assertIsNone(x.check("BSF0001", 4.1))
        x.note("BSF0001", "imu", 4.2)
        self.assertIsNone(x.check("BSF0001", 4.2))

    def test_both_silent_is_not_single_stream_alarm(self):
        x = StreamLiveness(threshold_s=3.0); x.connected("BSF0001", 0.0)
        self.assertIsNone(x.check("BSF0001", 4.0))


if __name__ == "__main__": unittest.main()
