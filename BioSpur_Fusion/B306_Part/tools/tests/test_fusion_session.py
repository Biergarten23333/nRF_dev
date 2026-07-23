import importlib.util
import sys
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "fusion_session.py"
SPEC = importlib.util.spec_from_file_location("fusion_session", MODULE_PATH)
fusion_session = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = fusion_session
SPEC.loader.exec_module(fusion_session)


class FusionSessionParserTest(unittest.TestCase):
    def test_reply_parser_preserves_text(self):
        reply = fusion_session.parse_reply(
            "FUSION_REPLY proto=2 master_ms=10 source=TAG "
            "correlation=42 text=CFG_OK TAG=1 LIVE=1"
        )
        self.assertEqual(reply.source, "TAG")
        self.assertEqual(reply.correlation, 42)
        self.assertEqual(reply.text, "CFG_OK TAG=1 LIVE=1")

    def test_imu_sequence_wrap_has_no_gap(self):
        lines = [
            "FUSION_IMU proto=2 master_ms=1 seq=65534 base_us=1 n=2 "
            "temp_raw=1 samples=-",
            "FUSION_IMU proto=2 master_ms=2 seq=0 base_us=2 n=2 "
            "temp_raw=1 samples=-",
        ]
        self.assertEqual(fusion_session.imu_sequence_gaps(lines), (0, 2))

    def test_sentinel_passes_clean_window(self):
        baseline = {
            "node_ms": "1000",
            "frames": "0",
            "rise_n": "0",
            "imu_rate": "200",
            "imu_batch": "2",
            "imu_active": "0",
            **{name: "0" for name in fusion_session.ANOMALY_COUNTERS},
        }
        final = {
            **baseline,
            "node_ms": "11000",
            "frames": "100",
            "rise_n": "100",
            "imu_active": "1",
        }
        lines = [
            f"FUSION_UWB proto=2 master_ms={i} verdict=healthy"
            for i in range(100)
        ]
        lines.extend(
            f"FUSION_IMU proto=2 master_ms={i} seq={i * 2} "
            f"base_us={i} n=2 temp_raw=1 samples=-"
            for i in range(1000)
        )
        result = fusion_session.evaluate_uwb_window(
            lines, baseline, final, 10.0, require_imu=True
        )
        self.assertTrue(result["pass"], result["reasons"])

    def test_sentinel_rejects_orphan_and_seq_gap(self):
        baseline = {
            "node_ms": "1000",
            "frames": "0",
            "rise_n": "0",
            "imu_rate": "200",
            "imu_batch": "2",
            "imu_active": "0",
            **{name: "0" for name in fusion_session.ANOMALY_COUNTERS},
        }
        final = {
            **baseline,
            "node_ms": "11000",
            "frames": "100",
            "rise_n": "100",
            "imu_active": "1",
            "orphan_frame": "1",
        }
        lines = [
            f"FUSION_UWB proto=2 master_ms={i} verdict=healthy"
            for i in range(100)
        ]
        lines.extend(
            (
                "FUSION_IMU proto=2 master_ms=1 seq=0 base_us=1 n=2 "
                "temp_raw=1 samples=-",
                "FUSION_IMU proto=2 master_ms=2 seq=4 base_us=2 n=2 "
                "temp_raw=1 samples=-",
            )
        )
        result = fusion_session.evaluate_uwb_window(
            lines, baseline, final, 10.0, require_imu=True
        )
        self.assertFalse(result["pass"])
        self.assertEqual(result["imu_seq_gaps"], 1)
        self.assertEqual(result["counter_deltas"]["orphan_frame"], 1)


if __name__ == "__main__":
    unittest.main()
