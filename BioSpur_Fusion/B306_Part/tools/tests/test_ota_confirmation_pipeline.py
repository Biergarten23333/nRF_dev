#!/usr/bin/env python3
import hashlib, json, struct, tempfile, unittest
from pathlib import Path
from unittest import mock
import sys

TOOLS = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(TOOLS))
from fleet_ota_v46r2 import (classify, durable_result, pristine_sdk,
                            run_live_verifier)
from ota_build_identity import (finalize_identity, prepare_identity, register)
from ota_confirmation import (BoardState, ConfirmationTimeout, ExpectedIdentity,
                              confirm_until_durable)

FWID = "a" * 64; IMAGE_SHA = "b" * 64; SOURCE_FWID = "c" * 64
EXPECTED = ExpectedIdentity("BSF1120", FWID, IMAGE_SHA, SOURCE_FWID, "d" * 64)

class Clock:
    def __init__(self, value=0): self.value = float(value)
    def now(self): return self.value
    def sleep(self, amount): self.value += amount

def pong(fwid=FWID, image=IMAGE_SHA, node="BSF1120"):
    return f"PONG name={node} fwid={fwid} image_sha={image}"

def runner(pings, statuses, prepare=lambda: None, deadline=20, start=0):
    clock=Clock(start); pi=iter(pings); si=iter(statuses)
    return confirm_until_durable(EXPECTED, lambda: next(pi), lambda: next(si), prepare,
        absolute_deadline=deadline, clock=clock.now, sleep=clock.sleep)

class ConfirmationTests(unittest.TestCase):
    def test_bridge_old_unknown_target_confirmed(self):
        values=[RuntimeError("bridge_not_ready"), pong("old", "old"), pong("old", "old"), pong()]
        def query():
            value=values.pop(0)
            if isinstance(value, Exception): raise value
            return value
        clock=Clock(); state,samples=confirm_until_durable(EXPECTED, query,
            lambda:"BOOT CONFIRM STATUS confirmed=1 required=0", lambda:None,
            absolute_deadline=20, clock=clock.now, sleep=clock.sleep)
        self.assertEqual(state, BoardState.TARGET_CONFIRMED); self.assertEqual(len(samples),4)

    def test_old_confirmed_reachable(self):
        state,samples=runner([pong(SOURCE_FWID, "d"*64)],
            ["BOOT CONFIRM STATUS confirmed=1 required=0"])
        self.assertEqual(state, BoardState.OLD_CONFIRMED)
        self.assertNotEqual(state, BoardState.UNREACHABLE)
        self.assertEqual(samples[-1]["node"], "BSF1120")

    def test_absolute_deadline_includes_preconfirmer_time(self):
        clock=Clock(19.5)
        with self.assertRaises(ConfirmationTimeout):
            confirm_until_durable(EXPECTED, lambda:pong("old","old"), lambda:"",
                lambda:None, absolute_deadline=20, poll_s=1,
                clock=clock.now, sleep=clock.sleep)
        self.assertGreaterEqual(clock.now(),20)

    def test_payload_hash_participates_in_identity(self):
        clock=Clock()
        with self.assertRaises(ConfirmationTimeout):
            confirm_until_durable(EXPECTED, lambda:pong(FWID,"e"*64), lambda:"",
                lambda:None, absolute_deadline=2, clock=clock.now, sleep=clock.sleep)

    def test_prepare_commit_then_confirmed(self):
        called=[]; state,_=runner([pong(),pong()],
            ["BOOT CONFIRM STATUS confirmed=0 required=1",
             "BOOT CONFIRM STATUS confirmed=1 required=0"],lambda:called.append(1))
        self.assertEqual(state,BoardState.TARGET_CONFIRMED); self.assertEqual(called,[1])

    def test_target_unconfirmed_timeout(self):
        clock=Clock()
        with self.assertRaises(ConfirmationTimeout) as caught:
            confirm_until_durable(EXPECTED,lambda:pong(),
                lambda:"BOOT CONFIRM STATUS confirmed=0 required=1",lambda:None,
                absolute_deadline=2,clock=clock.now,sleep=clock.sleep)
        self.assertEqual(caught.exception.state,BoardState.TARGET_RUNNING_UNCONFIRMED)

    def test_rollback_after_target(self):
        state,_=runner([pong(),pong(SOURCE_FWID,"d"*64)],
            ["BOOT CONFIRM STATUS confirmed=0 required=1"])
        self.assertEqual(state,BoardState.ROLLBACK_OBSERVED)

    def test_wrong_node(self):
        state,_=runner([pong(node="BSF9999")],[])
        self.assertEqual(state,BoardState.TARGET_IDENTITY_MISMATCH)

class IdentityTests(unittest.TestCase):
    def inputs(self): return {"source_commit":"1"*40,"dirty_state_digest":"2"*64,
        "effective_configs":{"a":"b"},"sdk_patch_identity":"3"*64,"toolchain":"ncs2.8"}
    def payload(self,path,fwid,tail=b""):
        body=b"prefix"+fwid.encode()+tail
        header=struct.pack("<IIHHII8sI",0x96F3B83D,0,32,0,len(body),0,b"\0"*8,0)
        path.write_bytes(header+body); return path
    def test_prepare_build_finalize_binds_embedded_and_final_payload(self):
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/"signed.bin"; prepared=prepare_identity(self.inputs())
            self.payload(path,prepared["fwid"],b"final")
            manifest=finalize_identity(prepared,path)
            self.assertEqual(manifest["fwid"],prepared["fwid"])
            self.assertEqual(manifest["signed_payload_sha256"],hashlib.sha256(path.read_bytes()).hexdigest())
            self.assertEqual(manifest["payload_path"],str(path.resolve()))

    def test_provisional_or_wrong_fwid_payload_fails(self):
        with tempfile.TemporaryDirectory() as td:
            path=self.payload(Path(td)/"signed.bin","f"*64)
            with self.assertRaisesRegex(ValueError,"does not embed"):
                finalize_identity(prepare_identity(self.inputs()),path)

    def test_collision_fails_closed_and_reproducible_fwid(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td); prep=prepare_identity(self.inputs())
            one=self.payload(root/"one.bin",prep["fwid"],b"1")
            two=self.payload(root/"two.bin",prep["fwid"],b"2")
            reg=root/"registry.json"; register(finalize_identity(prep,one),reg)
            with self.assertRaisesRegex(ValueError,"collision"):
                register(finalize_identity(prep,two),reg)
            self.assertEqual(prep,prepare_identity(self.inputs()))

class FleetTests(unittest.TestCase):
    def test_txn_error_plus_durable(self): self.assertEqual(classify(2,True),"DURABLE_PASS_WITH_TXN_ERROR")
    def test_unconfirmed_never_success(self):
        self.assertEqual(classify(0,False,"TARGET_RUNNING_UNCONFIRMED"),"TARGET_RUNNING_UNCONFIRMED")
    def test_sdk_finally(self):
        ledger={"sdk":{}}; calls=[]
        with mock.patch("fleet_ota_v46r2.require_patch",side_effect=lambda a,expected=None:(calls.append(a) or a)):
            with self.assertRaises(KeyboardInterrupt):
                with pristine_sdk(ledger,lambda:None): raise KeyboardInterrupt
        self.assertEqual(calls,["revert","apply","verify"])
    def test_live_verifier_invokes_fresh_query_command(self):
        with tempfile.TemporaryDirectory() as td, mock.patch("fleet_ota_v46r2.subprocess.run") as run:
            run.return_value.returncode=0; out=Path(td)/"fresh"
            rc,path,error=run_live_verifier(["verify","{node}","{absolute_deadline}"],
                node="BSF1120",out_dir=out,identity_path=Path("manifest"),
                absolute_deadline=123.0,timeout_s=5)
            self.assertEqual(rc,0); self.assertIsNone(error); self.assertTrue(run.called)
            self.assertIn("123.0",run.call_args.args[0])
    def test_spacing_after_confirm_and_rescue_after_transaction(self):
        txn=(TOOLS/"v32_ota_board_transaction.py").read_text()
        self.assertGreater(txn.index("rebuild_spacing_after_confirm(args.out_dir"),
                           txn.index('board_state") != "TARGET_CONFIRMED"'))
        fleet=(TOOLS/"fleet_ota_v46r2.py").read_text()
        self.assertGreater(
            fleet.index("run_live_verifier(", fleet.index("completed = subprocess.run(command")),
            fleet.index("completed = subprocess.run(command"))
        self.assertIn('final_root = out / "final_live_verification"',fleet)
    def test_no_stale_defaults(self):
        source=(TOOLS/"confirm_b306_v32.py").read_text()
        self.assertIn('"--identity-manifest", required=True',source)
        self.assertIn('"--absolute-deadline", required=True',source)
        self.assertNotIn("BSF_B306_MARKER",source)

if __name__=="__main__": unittest.main()
