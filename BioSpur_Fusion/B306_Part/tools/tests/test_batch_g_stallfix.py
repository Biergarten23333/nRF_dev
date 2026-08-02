import sys
import unittest
from pathlib import Path


TOOLS = Path(__file__).resolve().parents[1]
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from batch_g_day_h3 import HARD_LEDGER, SLOT10
from batch_g_overnight import NODES
from batch_g_stallfix import (
    p1_beacon_query_gate,
    stability_gate,
    window_gate,
)


def measured_fixture() -> dict[str, object]:
    rows = {}
    for node in NODES:
        rows[node] = {
            "available": True,
            "tag_domain_rate_hz": 5.0 if node == SLOT10 else 9.09,
            "sweep_missing": 0,
            "sweep_duplicates": 0,
            "sweep_reorders": 0,
            "lock_before": "1",
            "lock_after": "1",
            "gen_before": "7",
            "gen_after": "7",
            "ledger_deltas": {key: 0 for key in HARD_LEDGER},
        }
    host = {
        "decoded_queue_drops": 0,
        "log_queue_drops": 0,
        "red_markers": 0,
        "reader_exceptions": 0,
    }
    listener = {"decoded": {"post_lines": ["LBSTAT;SLAVED;"]}}
    return {
        "nodes": rows,
        "capture": {
            "decoder_errors": 0,
            "malformed": [],
            "disconnects": [],
        },
        "field_status": {"sub_before": listener, "sub_after": listener},
        "dk_ledstat_before": "LEDSTAT queue=123",
        "dk_ledstat_capture_end": "LEDSTAT queue=123",
        "host_drain_before": dict(host),
        "host_drain_capture_end": dict(host),
    }


class WindowGateTests(unittest.TestCase):
    def test_slot10_rate_is_waived_but_window_passes(self):
        gate = window_gate(measured_fixture(), strict_rates=True)
        self.assertTrue(gate["pass"])
        self.assertFalse(gate["nodes"][SLOT10]["rate_gated"])

    def test_slot10_data_loss_is_not_waived(self):
        measured = measured_fixture()
        measured["nodes"][SLOT10]["sweep_missing"] = 1
        gate = window_gate(measured, strict_rates=True)
        self.assertFalse(gate["pass"])
        self.assertIn("sweep_missing=1", gate["nodes"][SLOT10]["reasons"])

    def test_dk_queue_delta_is_hard_failure(self):
        measured = measured_fixture()
        measured["dk_ledstat_capture_end"] = "LEDSTAT queue=124"
        gate = window_gate(measured, strict_rates=False)
        self.assertFalse(gate["pass"])
        self.assertEqual(gate["dk_queue_delta"], 1)

    def test_p1_ignores_fleet_ledger_but_not_host_loss(self):
        measured = measured_fixture()
        measured["nodes"][NODES[0]]["ledger_deltas"]["crc"] = 1
        self.assertTrue(stability_gate(measured)["pass"])
        measured["nodes"][NODES[0]]["sweep_missing"] = 1
        self.assertFalse(stability_gate(measured)["pass"])

    def test_p1_requires_five_decoded_beacon_queries(self):
        measured = measured_fixture()
        measured["consumer_actions"] = [
            {
                "label": f"query_{index}",
                "status": "COMPLETE",
                "result": {"returncode": 0, "decoded": {"ok": True}},
            }
            for index in range(5)
        ]
        self.assertTrue(p1_beacon_query_gate(measured)["pass"])
        measured["consumer_actions"][3]["result"]["decoded"] = None
        self.assertFalse(p1_beacon_query_gate(measured)["pass"])


if __name__ == "__main__":
    unittest.main()
