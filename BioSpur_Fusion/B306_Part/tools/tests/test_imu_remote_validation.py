import sys
import tempfile
import unittest
from pathlib import Path


TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))
import imu_remote_validation as validation  # noqa: E402


class RawCaptureTests(unittest.TestCase):
    def test_salvages_complete_imu_record_with_interleaved_prefix(self):
        content = "\n".join(
            (
                "1 1 FUSION_RTT_RX FUSION_REPLY text=IMU START OK err=0",
                "2 2 FUSION_RTT_RX analyzer textFUSION_IMU "
                "seq=10 base_us=20 n=2 temp_raw=30 "
                "samples=0,1,2,3,4,5,6;5,1,2,3,4,5,6",
                "3 3 FUSION_RTT_RX FUSION_IMU "
                "seq=12 base_us=30 n=1 temp_raw=30 samples=0,1,2,3,4,5,6",
                "4 4 FUSION_RTT_RX FUSION_COMMAND_TX "
                "line=BSF3C79 IMU STOP",
            )
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "raw.log"
            path.write_text(content)
            lines = validation.capture_lines_from_raw(path, "BSF3C79")

        self.assertTrue(lines[0].startswith("HOST_RTT_CORRUPTION "))
        self.assertTrue(lines[1].startswith("FUSION_IMU "))
        self.assertEqual(
            validation.imu_sequence_audit(lines),
            {
                "valid_records": 2,
                "malformed_imu_lines": 0,
                "gap_events": 0,
                "missing_samples": 0,
            },
        )

    def test_counts_split_imu_record_as_missing(self):
        lines = [
            "FUSION_IMU seq=10 base_us=20 n=2 temp_raw=30 "
            "samples=0,1,2,3,4,5,6;5,1,2,3,4,5,6",
            "FUSION_IMU proto=2 mFUSION_HEALTH packets=1",
            "aster_ms=1 seq=12 base_us=30 n=2 temp_raw=30 "
            "samples=0,1,2,3,4,5,6;5,1,2,3,4,5,6",
            "FUSION_IMU seq=14 base_us=40 n=2 temp_raw=30 "
            "samples=0,1,2,3,4,5,6;5,1,2,3,4,5,6",
        ]
        self.assertEqual(
            validation.imu_sequence_audit(lines),
            {
                "valid_records": 2,
                "malformed_imu_lines": 1,
                "gap_events": 1,
                "missing_samples": 2,
            },
        )


if __name__ == "__main__":
    unittest.main()
