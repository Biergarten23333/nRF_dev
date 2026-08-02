import json
import sys
import unittest
from pathlib import Path


TOOLS = Path(__file__).resolve().parents[1]
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from fusion_session import SessionError
from v32_service_gate import classify_service, summarize_qos


class V32ServiceGateTests(unittest.TestCase):
    def test_thresholds(self):
        regime = dict(
            spacing="ON", spacing_us=5000,
            spacing_generation=7, current_generation=7,
        )
        self.assertEqual(classify_service(19.0, **regime), "FULL")
        self.assertEqual(classify_service(10.1, **regime), "HALF")
        self.assertEqual(classify_service(18.9, **regime), "DEGRADED")
        self.assertEqual(classify_service(7.9, **regime), "DEGRADED")

    def test_thresholds_refuse_wrong_regime(self):
        for regime in (
            dict(spacing="OFF", spacing_us=7500, spacing_generation=1, current_generation=1),
            dict(spacing="ON", spacing_us=7500, spacing_generation=1, current_generation=1),
            dict(spacing="ON", spacing_us=5000, spacing_generation=1, current_generation=2),
        ):
            with self.subTest(regime=regime), self.assertRaises(SessionError):
                classify_service(20.0, **regime)

    def test_kind7_window_rate(self):
        lines = [
            "FUSION_QOS name=BSF3C79 spacing=ON spacing_us=5000 spacing_generation=2 window_ms=1000 reports=10",
            "FUSION_QOS name=BSF3C79 spacing=ON spacing_us=5000 spacing_generation=2 window_ms=1000 reports=11",
            "FUSION_QOS name=BSFC2CC spacing=ON spacing_us=5000 spacing_generation=2 window_ms=1000 reports=20",
            "FUSION_QOS name=BSFC2CC spacing=ON spacing_us=5000 spacing_generation=2 window_ms=1000 reports=20",
        ]
        table = summarize_qos(
            lines, 10.0, current_generation=2,
            nodes=("BSF3C79", "BSFC2CC"),
        )
        self.assertEqual(table["BSF3C79"]["class"], "HALF")
        self.assertEqual(table["BSFC2CC"]["class"], "FULL")

    def test_kind7_refuses_stale_generation(self):
        lines = [
            "FUSION_QOS name=BSF3C79 spacing=ON spacing_us=5000 spacing_generation=1 window_ms=1000 reports=20",
        ]
        with self.assertRaises(SessionError):
            summarize_qos(
                lines, 10.0, current_generation=2, nodes=("BSF3C79",)
            )

    def test_d5_known_data_classifier(self):
        source = Path(
            "/mnt/nrf_ssd/nRF_dev/BioSpur_Fusion/"
            "UWB_Part/logs/v32_campaign_20260801/D5_ANALYSIS.json"
        )
        rows = json.loads(source.read_text(encoding="utf-8"))["rows"]
        observed = {
            row["bsf"]: classify_service(
                row["reports"] / row["alive_s"],
                spacing="ON", spacing_us=5000,
                spacing_generation=2, current_generation=2,
            )
            for row in rows
        }
        for row in rows:
            expected = "HALF" if row["class"] == "starved" else "FULL"
            self.assertEqual(observed[row["bsf"]], expected, row["bsf"])


if __name__ == "__main__":
    unittest.main()
