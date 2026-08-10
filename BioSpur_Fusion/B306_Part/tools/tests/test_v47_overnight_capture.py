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

if __name__=='__main__':unittest.main()
