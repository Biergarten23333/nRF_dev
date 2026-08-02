import unittest

from capacity_ramp import RecordingAssembler, analyze_run, predictions_for
from validate_notify_fix_v30 import enqueue_p99_us


class NotifyFixV30Test(unittest.TestCase):
    def test_prediction_uses_configured_rates_and_batch(self):
        batch2 = predictions_for(5, "C", 600, None, 2, 200, 10, 2, 5)
        batch5 = predictions_for(5, "C", 600, None, 5, 200, 10, 2, 5)
        self.assertEqual(batch2["predicted_notifications_s"], 560)
        self.assertEqual(batch5["predicted_notifications_s"], 260)

    def test_enqueue_p99_reports_histogram_upper_bound(self):
        self.assertEqual(enqueue_p99_us([98, 2] + [0] * 9), 20)
        self.assertIsNone(enqueue_p99_us([0] * 10 + [1]))

    def test_binary_telemetry_without_record_id_counts_as_delivered(self):
        rows = [
            (
                1.0,
                "FUSION_TELEMETRY proto=7 name=BSF3C79 "
                "node_ms=100 notify_ok=1",
            )
        ]
        baseline = {
            name: {"notify_ok": "0", "master_rx": "0", "logger_drop": "0"}
            for name in ("BSF3C79", "BSF44AD", "BSF6C53", "BSF8BC4", "BSFC2CC")
        }
        final = {
            name: {"notify_ok": "0", "master_rx": "0", "logger_drop": "0"}
            for name in baseline
        }
        result = analyze_run(
            rows,
            RecordingAssembler(),
            baseline,
            final,
            1.0,
            0,
            "C",
            {"aggregate": {"count": "5", "ready": "5"}, "peers": {}},
            {"aggregate": {"count": "5", "ready": "5"}, "peers": {}},
            {"summary": {"cdc_drop_bytes": "0", "cdc_drop_records": "0"}},
            {"summary": {"cdc_drop_bytes": "0", "cdc_drop_records": "0"}},
            {
                "expected_notifications": 1.0,
                "pass_latency_p95_us": 100000.0,
                "pass_latency_max_us": 400000.0,
            },
        )
        self.assertEqual(result["aggregate"]["telemetry_records"], 1)
        self.assertEqual(result["aggregate"]["delivered_notifications"], 1)


if __name__ == "__main__":
    unittest.main()
