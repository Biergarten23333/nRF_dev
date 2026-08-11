import json,sys,tempfile,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parents[1]))
from v47_guard_evidence import *

GOOD="V45 GUARD rcv=0 cause=0 frozen_ms=0 streak=0 max=3 latched=0 intent=0 unk_sreq=0 named_sreq=0 rr=00000001"
def master(node,text=GOOD):return f"FUSION_REPLY proto=7 name={node} master_ms=1 source=B306 correlation=0 text={text}"
class GuardTests(unittest.TestCase):
 def test_complete_and_explicit_zero(self):
  r=parse_master_reply(master("BSF3C79"),"BSF3C79");self.assertEqual(r["status"],"ok");self.assertEqual(r["values"]["rcv"],0)
 def test_truncated_is_missing_not_zero(self):
  r=parse_master_reply(master("BSF3C79","V45 GUARD rcv=2 cause="),"BSF3C79");self.assertEqual(r["status"],"malformed_response");self.assertNotIn("frozen_ms",r["values"])
 def test_wrong_node(self):self.assertEqual(parse_master_reply(master("BSF44AD"),"BSF3C79")["status"],"wrong_node")
 def test_timeout_isolated_and_collection_can_continue(self):
  with tempfile.TemporaryDirectory() as d:
   s=GuardSampler(("BSF3C79","BSF44AD"),Path(d)/"g.jsonl",timeout_s=1,stagger_s=0);sent=[];s.start("t0_baseline",0);s.tick(sent.append,0);s.tick(sent.append,2);s.tick(sent.append,2);self.assertEqual(len(sent),2);self.assertTrue(s.active)
 def test_unrelated_reply_does_not_consume_pending_guard(self):
  with tempfile.TemporaryDirectory() as d:
   s=GuardSampler(("BSF3C79",),Path(d)/"g.jsonl");s.start("t0_baseline",0);s.tick(lambda _x:None,0)
   self.assertIsNone(s.on_line("FUSION_REPLY proto=7 name=BSF3C79 text=PONG name=BSF3C79"));self.assertEqual(s.pending,"BSF3C79")
 def test_baseline_final_delta(self):
  self.assertEqual(delta(parse_master_reply(master("BSF3C79"),"BSF3C79"),parse_master_reply(master("BSF3C79",GOOD.replace("rcv=0","rcv=2")),"BSF3C79"))["rcv"],2)
 def test_no_mutating_command(self):
  for c in ("CORPSE ACK=1","REBOOT","V45 FORCE","IMU STOP"):
   with self.assertRaises(ValueError):safe_command(c)
  self.assertEqual(safe_command("V45 GUARD"),"V45 GUARD")
 def test_append_only_schema_and_no_corpse_ack(self):
  self.assertEqual(SCHEMA,"biospur-v47-guard-evidence-v1");self.assertNotIn("CORPSE ACK",COMMAND)
 def test_previous_capture_schema_needs_no_guard_file(self):
  old={"schema":"biospur-v47-afternoon-manifest-v1","commands_sent":[]}
  self.assertNotIn("guard_evidence",old)
 def test_canonical_identity_is_not_a_diagnostic_field(self):
  self.assertFalse(set(FIELDS)&{"fwid","marker","image_sha256"})
if __name__=="__main__":unittest.main()
