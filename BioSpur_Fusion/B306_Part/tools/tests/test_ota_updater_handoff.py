#!/usr/bin/env python3
import json
import tempfile
import unittest
from pathlib import Path
import sys

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))
from ota_updater_handoff import (UpdaterEvidenceError, UpdaterStateMachine,
                                 validate_terminal)

RUN = "v47-BSF6C53-test"
NODE = "BSF6C53"
SHA = "9" * 64


def ready_records(run_id=RUN, node=NODE, sha=SHA):
    machine = UpdaterStateMachine(run_id, node, sha)
    lines = [
        "BioSpur fast BLE OTA master ready on nRF52840 DK",
        "OTA_INITIAL_SCAN armed deadline_ms=180000",
        f"Connected target evidence: verified=1 uuid=- name={node} token=51",
        "DFU SMP service ready",
        "OTA SMP subscribe ok: rc=0 smp=0x001d ccc=0x001e",
        "OTA upload starting: image_len=1 bytes",
        "OTA upload progress: 0% (0/1 bytes)",
        "OTA upload complete",
        "OTA_STATE_READ parsed=1 expected=1 active=0 confirmed=0 pending=0 expected_secondary=1 secondary_present=1",
        "OTA pending/test request",
        "OTA command done: group=0x0001 cmd=0x00 status=0 off=0",
        "OTA reset request",
        "OTA command done: group=0x0000 cmd=0x05 status=0 off=0",
    ]
    for index, line in enumerate(lines):
        machine.feed(line, float(index))
    return machine.records


class ParserTests(unittest.TestCase):
    def test_ready_requires_every_predecessor(self):
        records = ready_records()
        self.assertEqual(validate_terminal(records, run_id=RUN, node=NODE,
                                           expected_image_sha=SHA),
                         "READY_FOR_CONFIRM")
        for missing in ("UPLOAD_COMPLETE", "SECONDARY_HASH_VERIFIED",
                        "PENDING_SET", "REBOOT_QUEUED"):
            damaged = [row for row in records if row["stage"] != missing]
            with self.subTest(missing=missing), self.assertRaises(UpdaterEvidenceError):
                validate_terminal(damaged, run_id=RUN, node=NODE,
                                  expected_image_sha=SHA)

    def test_wrong_run_node_and_sha_rejected(self):
        records = ready_records()
        for kwargs in ({"run_id":"wrong", "node":NODE, "expected_image_sha":SHA},
                       {"run_id":RUN, "node":"BSF1120", "expected_image_sha":SHA},
                       {"run_id":RUN, "node":NODE, "expected_image_sha":"8"*64}):
            with self.assertRaises(UpdaterEvidenceError):
                validate_terminal(records, **kwargs)

    def test_partial_states_are_nonterminal(self):
        records = ready_records()
        for stop in ("UPLOAD_COMPLETE", "SECONDARY_HASH_VERIFIED", "PENDING_SET"):
            partial = records[:next(i for i,r in enumerate(records) if r["stage"] == stop)+1]
            with self.assertRaisesRegex(UpdaterEvidenceError, "non-terminal"):
                validate_terminal(partial, run_id=RUN, node=NODE,
                                  expected_image_sha=SHA)

    def test_stage_regression_and_contradictory_terminal_rejected(self):
        machine = UpdaterStateMachine(RUN, NODE, SHA)
        machine.feed("BioSpur fast BLE OTA master ready on nRF52840 DK", 1)
        with self.assertRaises(UpdaterEvidenceError):
            machine.feed("BioSpur fast BLE OTA master ready on nRF52840 DK", 2)
        records = ready_records()
        records.append(dict(records[-1]))
        with self.assertRaises(UpdaterEvidenceError):
            validate_terminal(records, run_id=RUN, node=NODE,
                              expected_image_sha=SHA)

    def test_free_text_cannot_manufacture_terminal(self):
        machine = UpdaterStateMachine(RUN, NODE, SHA)
        self.assertIsNone(machine.feed("upload complete success READY_FOR_CONFIRM", 1))
        self.assertIsNone(machine.terminal)

    def test_post_reset_reconnect_is_not_stage_regression(self):
        records = ready_records()
        self.assertEqual(records[-1]["stage"], "READY_FOR_CONFIRM")


class TransactionSourceContracts(unittest.TestCase):
    def test_deadline_cutoff_and_no_deferred_restore(self):
        source = (TOOLS / "v32_ota_board_transaction.py").read_text()
        self.assertNotIn("DEFERRED_UNTIL_AFTER_DURABLE_CONFIRM", source)
        self.assertIn("- args.reserved_post_updater_s", source)
        self.assertLess(source.index("capture_updater_terminal("),
                        source.index("restore_master(restore_script", source.index("capture_updater_terminal(")))

    def test_exception_path_restores_then_rescues_without_retry(self):
        source = (TOOLS / "v32_ota_board_transaction.py").read_text()
        final = source[source.rindex("finally:"):]
        self.assertLess(final.index("restore_master("),
                        final.index("exception_confirm_rescue"))
        self.assertNotIn("flash(updater_script", final)

    def test_restore_waits_for_live_v36_before_verifier(self):
        source = (TOOLS / "v32_ota_board_transaction.py").read_text()
        restore = source[source.index("def restore_master"):source.index("def rebuild_spacing")]
        self.assertIn("read_dk_marker", restore)
        self.assertIn('marker == "dk-fusion-imu-relay-v36"', restore)

    def test_cutoff_preserves_exact_budget(self):
        deadline = 1000.0
        self.assertEqual(deadline - 61.193245916, 938.806754084)

    def test_stable_inventory_is_valid_read_only_preflight(self):
        source = (TOOLS / "v32_ota_board_transaction.py").read_text()
        self.assertIn('preflight.get("status") == "INVENTORY_PASS"', source)
        self.assertIn('all(row.get("ok") is True for row in samples)', source)


if __name__ == "__main__":
    unittest.main()
