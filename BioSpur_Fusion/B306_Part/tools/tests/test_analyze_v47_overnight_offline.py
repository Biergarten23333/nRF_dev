import importlib.util
import unittest
from pathlib import Path

P=Path(__file__).parents[1]/"analyze_v47_overnight_offline.py"
S=importlib.util.spec_from_file_location("overnight",P);M=importlib.util.module_from_spec(S);S.loader.exec_module(M)

class TestOffline(unittest.TestCase):
    def test_field_parser_exact_tokens(self):
        self.assertEqual(M.fields(b"ready=10 count=9 ready_extra=100"),
                         {"ready":"10","count":"9","ready_extra":"100"})
    def test_gap_floor_excludes_short_gap(self):
        self.assertEqual(M.gaps([0,.05,.1,2.0],0,2.0,.05),[])
        self.assertTrue(M.gaps([0,.05,3.0],0,3.0,.05))
    def test_joint_segment_threshold(self):
        global_manifest={"t0_wall":"2026-01-01T00:00:00+00:00","t0_monotonic":0}
        M.MANIFEST=global_manifest
        s={n:{"imu":[(0,10),(25,10)],"uwb":[0,25]} for n in M.NODES}
        rows=M.joint_segments(s,0,25)
        self.assertTrue(all(x["wedge_threshold_met"] for x in rows))
    def test_two_second_recovery_rule(self):
        s={n:{"imu":[],"uwb":[]} for n in M.NODES}
        n=M.NODES[0];s[n]["imu"]=[(i/20,10) for i in range(40)];s[n]["uwb"]=[i/8 for i in range(16)]
        self.assertTrue(M.dual_recovery(s,n,0,2,2))
        self.assertFalse(M.dual_recovery(s,n,0,1.9,2))
    def test_twenty_second_wedge_boundary(self):
        M.MANIFEST={"t0_wall":"2026-01-01T00:00:00+00:00","t0_monotonic":0}
        s={n:{"imu":[(0,1),(20,1)],"uwb":[0,20]} for n in M.NODES}
        self.assertFalse(any(x["wedge_threshold_met"] for x in M.joint_segments(s,0,19.99)))
        self.assertTrue(all(x["wedge_threshold_met"] for x in M.joint_segments(s,0,20)))

if __name__=="__main__":unittest.main()
