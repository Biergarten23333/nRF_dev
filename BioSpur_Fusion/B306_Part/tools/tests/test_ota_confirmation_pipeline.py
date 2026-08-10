#!/usr/bin/env python3

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import sys
TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))

from fleet_ota_v46r2 import classify, durable_result, pristine_sdk
from ota_build_identity import build_manifest, register
from ota_confirmation import (BoardState, ConfirmationTimeout, ExpectedIdentity,
                              confirm_until_durable)


FWID = "a" * 64
SHA = "b" * 64
EXPECTED = ExpectedIdentity("BSF1120", FWID, SHA)


class Clock:
    def __init__(self): self.value = 0.0
    def now(self): return self.value
    def sleep(self, amount): self.value += amount


def runner(pings, statuses, prepare=lambda: None, deadline=20):
    clock = Clock()
    pi = iter(pings)
    si = iter(statuses)
    return confirm_until_durable(EXPECTED, lambda: next(pi), lambda: next(si), prepare,
                                 deadline_s=deadline, clock=clock.now,
                                 sleep=clock.sleep)


class ConfirmationTests(unittest.TestCase):
    def test_bridge_old_old_target_confirmed(self):
        pings = [RuntimeError("bridge_not_ready"),
                 "PONG name=BSF1120 fwid=old", "PONG name=BSF1120 fwid=old",
                 f"PONG name=BSF1120 fwid={FWID}"]
        def ping():
            value = pings.pop(0)
            if isinstance(value, Exception): raise value
            return value
        clock = Clock()
        state, samples = confirm_until_durable(
            EXPECTED, ping, lambda: "BOOT CONFIRM STATUS confirmed=1 required=0",
            lambda: None, deadline_s=20, clock=clock.now, sleep=clock.sleep)
        self.assertEqual(state, BoardState.TARGET_CONFIRMED)
        self.assertEqual(len(samples), 4)

    def test_old_until_deadline_has_diagnostics(self):
        clock = Clock()
        with self.assertRaisesRegex(ConfirmationTimeout, r"samples=3.*last_identity='old'"):
            confirm_until_durable(EXPECTED,
                lambda: "PONG name=BSF1120 fwid=old", lambda: "", lambda: None,
                deadline_s=3, clock=clock.now, sleep=clock.sleep)

    def test_already_confirmed(self):
        state, _ = runner([f"PONG name=BSF1120 fwid={FWID}"],
                          ["BOOT CONFIRM STATUS confirmed=1 required=0"])
        self.assertEqual(state, BoardState.TARGET_CONFIRMED)

    def test_prepare_commit_then_confirmed(self):
        called = []
        state, _ = runner([f"PONG name=BSF1120 fwid={FWID}"] * 2,
            ["BOOT CONFIRM STATUS confirmed=0 required=1",
             "BOOT CONFIRM STATUS confirmed=1 required=0"], lambda: called.append(1))
        self.assertEqual(state, BoardState.TARGET_CONFIRMED)
        self.assertEqual(called, [1])

    def test_unconfirmed_until_timeout(self):
        clock = Clock()
        with self.assertRaises(ConfirmationTimeout):
            confirm_until_durable(EXPECTED,
                lambda: f"PONG name=BSF1120 fwid={FWID}",
                lambda: "BOOT CONFIRM STATUS confirmed=0 required=1", lambda: None,
                deadline_s=2, clock=clock.now, sleep=clock.sleep)

    def test_rollback_after_target(self):
        state, _ = runner([f"PONG name=BSF1120 fwid={FWID}",
                           "PONG name=BSF1120 fwid=old"],
            ["BOOT CONFIRM STATUS confirmed=0 required=1"])
        self.assertEqual(state, BoardState.ROLLBACK_OBSERVED)

    def test_wrong_node_mismatch(self):
        state, _ = runner([f"PONG name=BSF9999 fwid={FWID}"], [])
        self.assertEqual(state, BoardState.TARGET_IDENTITY_MISMATCH)


class FleetTests(unittest.TestCase):
    def durable_file(self, root, *, fwid=FWID, confirmed="1"):
        path = root / "result.json"
        path.write_text(json.dumps({"status": "PASS", "board_state": "TARGET_CONFIRMED",
            "node": "BSF1120", "expected_fwid": fwid, "samples": [{
                "node": "BSF1120", "fwid": fwid,
                "boot_confirm": f"BOOT CONFIRM STATUS confirmed={confirmed}"}]}))
        return path

    def test_txn_error_plus_durable_pass(self):
        self.assertEqual(classify(2, True), "DURABLE_PASS_WITH_TXN_ERROR")

    def test_txn_zero_identity_mismatch(self):
        with tempfile.TemporaryDirectory() as td:
            path = self.durable_file(Path(td), fwid="c" * 64)
            self.assertFalse(durable_result(path, "BSF1120", FWID))
            self.assertEqual(classify(0, False), "UNKNOWN")

    def test_target_unconfirmed_is_never_success(self):
        self.assertEqual(classify(0, False, "TARGET_RUNNING_UNCONFIRMED"),
                         "TARGET_RUNNING_UNCONFIRMED")

    def test_sdk_reapplied_in_finally(self):
        ledger = {"sdk": {}}
        calls = []
        def required(action, expected=None): calls.append(action); return action
        with mock.patch("fleet_ota_v46r2.require_patch", side_effect=required):
            with self.assertRaises(KeyboardInterrupt):
                with pristine_sdk(ledger, lambda: None):
                    raise KeyboardInterrupt
        self.assertEqual(calls, ["revert", "apply", "verify"])
        self.assertTrue(ledger["sdk"]["restored"])

    def test_existing_evidence_is_not_deleted(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); old = root / "prior"; old.mkdir(); witness = old / "raw"
            witness.write_text("keep")
            (root / "new-run").mkdir(exist_ok=False)
            self.assertEqual(witness.read_text(), "keep")


class IdentityTests(unittest.TestCase):
    def test_different_payload_same_fwid_fails_closed(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); registry = root / "registry.json"
            one = root / "one.bin"; two = root / "two.bin"
            one.write_bytes(b"one"); two.write_bytes(b"two")
            m1 = build_manifest({"source": "same"}, one)
            m2 = build_manifest({"source": "same"}, two)
            register(m1, registry)
            with self.assertRaisesRegex(ValueError, "FWID collision"):
                register(m2, registry)

    def test_identical_inputs_are_reproducible(self):
        with tempfile.TemporaryDirectory() as td:
            image = Path(td) / "image.bin"; image.write_bytes(b"same")
            self.assertEqual(build_manifest({"config": "x"}, image),
                             build_manifest({"config": "x"}, image))

    def test_no_stale_confirmation_identity_default(self):
        source = (TOOLS / "confirm_b306_v32.py").read_text()
        self.assertIn('"--identity-manifest", required=True', source)
        self.assertNotIn("BSF_B306_MARKER", source)
        self.assertNotIn("B306_MARKER =", source)


if __name__ == "__main__":
    unittest.main()
