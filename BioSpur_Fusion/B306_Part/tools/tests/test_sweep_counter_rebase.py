import importlib.util
import sys
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "sweep_counter_rebase.py"
SPEC = importlib.util.spec_from_file_location(
    "sweep_counter_rebase", MODULE_PATH
)
sweep_counter_rebase = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = sweep_counter_rebase
SPEC.loader.exec_module(sweep_counter_rebase)


class RebootAwareSweepCounterTest(unittest.TestCase):
    def test_backward_without_boot_fact_is_reorder(self):
        decoder = sweep_counter_rebase.RebootAwareSweepCounter()
        self.assertEqual(decoder.observe("BSF3C79", 100, 1000), "BASELINE")
        self.assertEqual(decoder.observe("BSF3C79", 1, 1100), "REORDER")
        state = decoder.snapshot()["BSF3C79"]
        self.assertEqual(state["reorders"], 1)
        self.assertEqual(state["rebases"], 0)

    def test_explicit_tag_boot_turns_restart_into_rebase(self):
        decoder = sweep_counter_rebase.RebootAwareSweepCounter()
        decoder.observe("BSF3C79", 100, 1000)
        decoder.note_tag_boot_or_join("BSF3C79", "relay7 OTA reboot")
        self.assertEqual(decoder.observe("BSF3C79", 1, 1100), "REBASE")
        self.assertEqual(decoder.observe("BSF3C79", 2, 1200), "FORWARD")
        state = decoder.snapshot()["BSF3C79"]
        self.assertEqual(state["rebases"], 1)
        self.assertEqual(state["reorders"], 0)
        self.assertEqual(state["duplicates"], 0)

    def test_b306_uptime_restart_is_independent_rebase_evidence(self):
        decoder = sweep_counter_rebase.RebootAwareSweepCounter()
        decoder.observe("BSF3C79", 100, 10000)
        self.assertEqual(decoder.observe("BSF3C79", 0, 50), "REBASE")
        state = decoder.snapshot()["BSF3C79"]
        self.assertEqual(state["rebases"], 1)
        self.assertEqual(state["reorders"], 0)

    def test_uint32_wrap_is_forward(self):
        decoder = sweep_counter_rebase.RebootAwareSweepCounter()
        decoder.observe("BSF3C79", 0xFFFFFFFF, 1000)
        self.assertEqual(decoder.observe("BSF3C79", 0, 1100), "FORWARD")
        self.assertEqual(decoder.snapshot()["BSF3C79"]["reorders"], 0)

    def test_legacy_delta_reclassifies_only_with_join_and_clean_host(self):
        clean = {
            "rebases": 1,
            "reorders": 0,
            "duplicates": 0,
        }
        result = sweep_counter_rebase.reclassify_legacy_b306_delta(
            raw_reorder_delta=644,
            raw_duplicate_delta=1,
            host_state=clean,
            qualifying_boot_or_join=True,
        )
        self.assertEqual(result["classification"], "EXPECTED_REBASE_DEBT")
        self.assertEqual(result["effective_reorder"], 0)
        self.assertEqual(result["effective_duplicate"], 0)

        result = sweep_counter_rebase.reclassify_legacy_b306_delta(
            raw_reorder_delta=644,
            raw_duplicate_delta=1,
            host_state=clean,
            qualifying_boot_or_join=False,
        )
        self.assertEqual(result["classification"], "ANOMALY")
        self.assertEqual(result["effective_reorder"], 644)


if __name__ == "__main__":
    unittest.main()
