import sys
import unittest
from pathlib import Path

import numpy as np


TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))
import h2_autozero_validation as validation  # noqa: E402


class P1AnalysisTests(unittest.TestCase):
    def make_samples(self, gyro_scale: float) -> list[dict[str, int]]:
        samples = []
        rate_hz = 200
        duration_s = 40
        for index in range(rate_hz * duration_s):
            seconds = index / rate_hz
            if seconds < 3.0:
                angle_deg = 0.0
                rate_dps = 0.0
            elif seconds < 33.0:
                angle_deg = (seconds - 3.0) * 3.0
                rate_dps = 3.0 * gyro_scale
            else:
                angle_deg = 90.0
                rate_dps = 0.0
            angle = np.radians(angle_deg)
            accel = np.asarray([0.0, np.sin(angle), np.cos(angle)])
            # For this synthetic accel trajectory, the physical gyro axis is +X.
            gyro = np.asarray([rate_dps, 0.0, 0.0])
            samples.append(
                {
                    "seq": index & 0xFFFF,
                    "timer_us": index * 5000,
                    "ax": int(round(accel[0] / validation.ACC_SCALE_G)),
                    "ay": int(round(accel[1] / validation.ACC_SCALE_G)),
                    "az": int(round(accel[2] / validation.ACC_SCALE_G)),
                    "gx": int(round(gyro[0] / validation.GYRO_SCALE_DPS)),
                    "gy": 0,
                    "gz": 0,
                    "temp": 2800,
                }
            )
        return samples

    def telemetry(self, frames: int, node_ms: int) -> dict[str, str]:
        fields = {key: "0" for key in validation.ANOMALY_COUNTERS}
        fields.update({"frames": str(frames), "node_ms": str(node_ms)})
        return fields

    def test_tracks_ninety_degree_tilt(self):
        samples = self.make_samples(1.0)
        analysis, _ = validation.analyze_p1(
            samples,
            {
                "valid_records": 4000,
                "malformed_imu_lines": 0,
                "gap_events": 0,
                "missing_samples": 0,
            },
            self.telemetry(100, 0),
            self.telemetry(500, 40000),
        )
        self.assertEqual(analysis["verdict"], "GYRO_SURVIVES")
        self.assertAlmostEqual(analysis["accel_final_angle_deg"], 90.0, delta=0.2)
        self.assertAlmostEqual(
            analysis["gyro_bias_corrected_integral_deg"], 90.0, delta=0.5
        )

    def test_detects_eaten_gyro(self):
        samples = self.make_samples(0.0)
        analysis, _ = validation.analyze_p1(
            samples,
            {
                "valid_records": 4000,
                "malformed_imu_lines": 0,
                "gap_events": 0,
                "missing_samples": 0,
            },
            self.telemetry(100, 0),
            self.telemetry(500, 40000),
        )
        self.assertEqual(analysis["verdict"], "AUTOZERO_EATEN")
        self.assertAlmostEqual(
            analysis["gyro_bias_corrected_integral_deg"], 0.0, delta=0.01
        )


if __name__ == "__main__":
    unittest.main()

