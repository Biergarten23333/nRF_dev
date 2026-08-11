import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

P=Path(__file__).parents[1]/'v47_afternoon_capture.py'
sys.path.insert(0,str(P.parent))
S=importlib.util.spec_from_file_location('capture',P);M=importlib.util.module_from_spec(S);S.loader.exec_module(M)

class OvernightCaptureTests(unittest.TestCase):
    def test_deduplicates_receivers_but_not_wrapped_sequence(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);(root/'listeners').mkdir();mapping={'BSF3C79':{'tag_short_address':'0xB101'}}
            rows=[]
            for key,t in [('LAE',1_000_000_000),('LBF',1_002_000_000),('LAE',1_120_000_000),('LAE',31_000_000_000)]:
                rows.append(json.dumps({'listener_key':key,'arrival_monotonic_ns':t,'kind':'LPD','parsed_ok':True,'fields':{'src':0xB101,'poll_seq':7}}))
            (root/'listeners'/'x.jsonl').write_text('\n'.join(rows)+'\n')
            out,errors=M.deduplicated_listener_rates(root,mapping,0,60_000_000_000)
            self.assertEqual(out['BSF3C79']['source_count'],3)
            self.assertEqual(errors,0)

    def test_only_five_poll_receivers_count(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);(root/'listeners').mkdir();mapping={'BSF3C79':{'tag_short_address':'0xB101'}}
            row={'listener_key':'LCG','arrival_monotonic_ns':1,'kind':'LPD','parsed_ok':True,'fields':{'src':0xB101,'poll_seq':1}}
            (root/'listeners'/'x.jsonl').write_text(json.dumps(row)+'\n')
            out,_=M.deduplicated_listener_rates(root,mapping,0,1_000_000_000)
            self.assertEqual(out['BSF3C79']['source_count'],0)

    def test_diagnostic_mode_is_explicit_in_source(self):
        source=P.read_text()
        self.assertIn("--diagnostic-ten-minute",source)
        self.assertIn("if fail and not a.diagnostic_ten_minute",source)
        self.assertIn("DIAGNOSTIC_TEN_MINUTES_COMPLETE",source)

    def test_bsf6c53_exemption_and_twelve_hour_semantics_are_explicit(self):
        source=P.read_text()
        self.assertIn("--bsf6c53-uwb-exempt",source)
        self.assertIn("minimum_checkpoint_hours':8",source)
        self.assertIn("hard_cap_hours':12",source)
        self.assertIn("RF_OR_RECEIVER_VISIBILITY",source)
        self.assertIn("'TAG_RESET_' not in x.get('line','')",source)

    def test_minimum_uninterruptible_window_prevents_smoke_autostop(self):
        source=P.read_text()
        self.assertIn("--minimum-uninterruptible-hours",source)
        self.assertIn("SMOKE_RECORDED_MINIMUM_CAPTURE_CONTINUES",source)
        self.assertIn("now>=t0+a.minimum_uninterruptible_hours*3600",source)

    def test_guard_capture_is_append_only_serial_and_non_periodic(self):
        source=P.read_text()
        self.assertIn("GuardSampler(NODES,root/'guard_evidence.jsonl')",source)
        self.assertIn("guard.start('t0_baseline',t0)",source)
        self.assertIn("'periodic_polling':False",source)
        self.assertIn("guard.start('host_anomaly',now)",source)
        self.assertIn("guard.start('best_effort_final')",source)

    def test_raw_collectors_precede_guard_and_stop_preserves_evidence(self):
        source=P.read_text()
        self.assertLess(source.index("listener_dir=root/'listener_capture'"),source.index("guard=GuardSampler"))
        self.assertLess(source.index("fusion_log=(root/'fusion_cdc.log')"),source.index("guard=GuardSampler"))
        self.assertIn("if stop:state['stop_reason']='OPERATOR_STOP'",source)
        self.assertIn("if ch:state['fusion_health_final']=ch.health_snapshot();ch.close()",source)

if __name__=='__main__':unittest.main()
