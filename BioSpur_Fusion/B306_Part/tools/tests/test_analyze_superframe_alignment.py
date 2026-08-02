import json
import tempfile
import unittest
from pathlib import Path

from analyze_superframe_alignment import analyze


class SuperframeAlignmentTests(unittest.TestCase):
    def test_five_nodes_share_base_and_equal_index_residuals(self):
        nodes = tuple(f"BSF{index:04X}" for index in range(1, 6))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            raw = root / "raw.log"
            setup = root / "setup.json"
            slot_rows = {}
            lines = []
            for slot, node in enumerate(nodes):
                reply = (
                    "CFG tag=1 bs=BS0001 slot=0/10 mask=0x0001 "
                    "src=MASTER period=10 active=9 active_us=0 epoch=1 "
                    "gen=90 superframe_base=12345 sf_valid=1 run=1 "
                    "state=RUNNING"
                )
                slot_rows[node] = {
                    "slot": slot,
                    "cfg_stop_status": {"reply": {"text": reply}},
                }
            setup.write_text(json.dumps({
                "slots": {
                    "superframe_base": 12345,
                    "nodes": slot_rows,
                }
            }))
            for sweep in range(12345, 12445):
                for slot, node in enumerate(nodes):
                    timer = (
                        1_000_000 * (slot + 1)
                        + (sweep - 12345) * 100_000
                        + ((sweep + slot) % 3 - 1) * 10
                    )
                    master_ms = 50_000 + (sweep - 12345) * 100 + slot * 10
                    lines.append(
                        f"1.0 {sweep + slot / 10:.6f} FUSION_RX "
                        f"FUSION_UWB name={node} sweep={sweep} "
                        f"strobe_us={timer} master_ms={master_ms}\n"
                    )
            raw.write_text("".join(lines))
            result = analyze(raw, setup, nodes, None, None)
            self.assertTrue(result["pass"])
            self.assertEqual(
                result["parse"]["common_sweeps"], 100
            )
            self.assertTrue(
                result["superframe_base"]["all_equal"]
            )


if __name__ == "__main__":
    unittest.main()
