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
from ota_timing_evidence import (classify_control, evaluate_reboot, registry_add,
                                 targeted_recovery_pass)
from qualify_ota_confirmation_timing import (EXPECTED as TIMING_EXPECTED,
    evaluate_inventory, qualification_summary, stable_gate_passes)

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

class TimingReadinessTests(unittest.TestCase):
    def lines(self, *, master_count="10", master_ready="10", names=None,
              unsubscribed=None, marker="dk-fusion-imu-relay-v36"):
        names = list(names or sorted(TIMING_EXPECTED))
        unsubscribed = set(unsubscribed or [])
        master = (f"FUSION_MASTER_STATUS marker={marker} count={master_count} "
                  f"ready={master_ready}")
        aggregate = f"FUSION_LIST count={master_count} ready={master_ready}"
        peers = [f"FUSION_PEER name={name} connected=1 "
                 f"subscribed={'0' if name in unsubscribed else '1'}" for name in names]
        pings = {name: {"text": f"PONG name={name}"} for name in TIMING_EXPECTED}
        return master, aggregate, peers, pings

    def evaluate(self, **changes):
        return evaluate_inventory(*self.lines(**changes))

    def test_ready_ten_count_not_ten_rejected(self):
        self.assertFalse(self.evaluate(master_count="9")["ok"])

    def test_ready_substring_rejected(self):
        self.assertFalse(self.evaluate(master_ready="100")["ok"])

    def test_wrong_ten_peer_set_rejected(self):
        names = sorted(TIMING_EXPECTED - {"BSF3C79"}) + ["BSF9999"]
        value = self.evaluate(names=names)
        self.assertFalse(value["ok"])
        self.assertEqual(value["unexpected_peers"], ["BSF9999"])

    def test_missing_or_unsubscribed_peer_rejected(self):
        self.assertFalse(self.evaluate(names=sorted(TIMING_EXPECTED)[:-1])["ok"])
        self.assertFalse(self.evaluate(unsubscribed={"BSF3C79"})["ok"])

    def test_unstable_readiness_rejected(self):
        values = [self.evaluate() for _ in range(9)] + [self.evaluate(master_ready="9")]
        self.assertFalse(stable_gate_passes(values))

    def test_exact_stable_ten_peer_set_accepted(self):
        self.assertTrue(stable_gate_passes([self.evaluate() for _ in range(10)]))

    def test_no_reboot_before_stability_gate(self):
        source = (TOOLS / "qualify_ota_confirmation_timing.py").read_text()
        gate = source.index("if not wait_stable(channel, args.ready_timeout_s")
        reboot_loop = source.index("for node in NODES:", gate)
        self.assertLess(gate, reboot_loop)
        self.assertFalse(stable_gate_passes([self.evaluate() for _ in range(9)]))

    def test_retry_failure_then_pong_is_reconnect_evidence(self):
        source = (TOOLS / "qualify_ota_confirmation_timing.py").read_text()
        self.assertIn('get("name") == node and disconnect', source)
        self.assertIn("reconnect = True", source)

    def test_partial_timing_samples_never_pass(self):
        samples = [{"node": node, "valid": True, "components_s": {
            "reboot_to_status": 1.0, "route_to_pong": .5, "status": .1}}
            for node in sorted(TIMING_EXPECTED)[:9]]
        self.assertEqual(qualification_summary(samples, 8.302, 5.777007)["gate"],
                         "BLOCKED")

class OfflineEvidenceTests(unittest.TestCase):
    def log(self, path, *, uptime=True, disconnect=True, confirmation=True):
        rows = [
            "1.000000 1.000000 FUSION_RX FUSION_REPLY proto=7 name=BSF1120 text=STATUS up_ms=90000",
            "2.000000 2.000000 FUSION_TX BSF1120 REBOOT",
            "2.100000 2.100000 FUSION_RX FUSION_REPLY proto=7 name=BSF1120 text=REBOOT QUEUED delay_ms=150",
        ]
        if disconnect: rows.append("3.000000 3.000000 FUSION_RX FUSION_DISCONNECTED name=BSF1120 reason=0x08")
        rows.append("4.000000 4.000000 FUSION_RX FUSION_REPLY proto=7 name=BSF1120 correlation=1 text=PONG name=BSF1120 fw=v44")
        if uptime: rows.append("5.000000 5.000000 FUSION_RX FUSION_REPLY proto=7 name=BSF1120 text=STATUS up_ms=1000")
        if confirmation: rows.append("6.000000 6.000000 FUSION_RX FUSION_REPLY proto=7 name=BSF1120 text=BOOT CONFIRM STATUS confirmed=1")
        path.write_text("\n".join(rows)+"\n"); return path

    def test_offline_salvage_complete_witnesses(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertEqual(evaluate_reboot(self.log(Path(td)/"x.log"),"BSF1120")["verdict"],
                             "VALID_SALVAGED")

    def test_refuse_missing_uptime_disconnect_confirmation(self):
        with tempfile.TemporaryDirectory() as td:
            verdict=evaluate_reboot(self.log(Path(td)/"x.log",uptime=False,
                disconnect=False,confirmation=False),"BSF1120")["verdict"]
            self.assertIn("DISCONNECT",verdict);self.assertIn("UPTIME",verdict);self.assertIn("CONFIRMATION",verdict)

    def test_raw_reply_wrong_correlation(self):
        self.assertEqual(classify_control(command_tx=True,tx_err=0,rejected=False,
            ctrl_before=1,ctrl_after=2,raw_reply=True,correlation_matches=False),
            "MASTER_CORRELATION_OR_HOST_FILTER_FAILURE")

    def test_tx_success_flat_ctrl_rx(self):
        self.assertEqual(classify_control(command_tx=True,tx_err=0,rejected=False,
            ctrl_before=7,ctrl_after=7,raw_reply=False,correlation_matches=False),
            "DOWNLINK_DID_NOT_REACH_B306")

    def test_tx_success_ctrl_rx_increases_without_reply(self):
        self.assertEqual(classify_control(command_tx=True,tx_err=0,rejected=False,
            ctrl_before=7,ctrl_after=8,raw_reply=False,correlation_matches=False),
            "B306_CONTROL_WORKER_OR_RESPONSE_FAILURE")

    def test_targeted_recovery_evidence(self):
        peers={node:{"connected":"1","subscribed":"1"} for node in TIMING_EXPECTED}
        self.assertTrue(targeted_recovery_pass({"peers":peers,"target_ping_successes":3,
            "target_status":True,"target_streaming":True,"other_peer_failures":0},set(TIMING_EXPECTED)))
        self.assertFalse(targeted_recovery_pass({"peers":peers,"target_ping_successes":2,
            "target_status":True,"target_streaming":True,"other_peer_failures":0},set(TIMING_EXPECTED)))

    def test_registry_rejects_mixed_configuration(self):
        base={"node":"BSF1120","master_firmware":"v36","b306_firmware":"v44",
              "tool_schema":"v2","configuration":{"spacing":"on"},"evidence_sha256":"a"*64}
        registry=registry_add({},base)
        mixed=dict(base,node="BSF31CC",b306_firmware="v46",evidence_sha256="b"*64)
        with self.assertRaisesRegex(ValueError,"mixed"):
            registry_add(registry,mixed)

if __name__=="__main__": unittest.main()
