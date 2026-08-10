import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve()
MOD_PATH = HERE.parents[1] / "analyze_v47_causal.py"
SPEC = importlib.util.spec_from_file_location("v47causal", MOD_PATH)
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)


def healthy_stream(seconds=30.0):
    imu = [(i / 20.0, 10) for i in range(int(seconds * 20))]
    uwb = [i / 8.0 for i in range(int(seconds * 8))]
    return imu, uwb


class CausalAnalysisTests(unittest.TestCase):
    def test_wrap_safe_counter_delta(self):
        self.assertEqual(M.wrap_delta(0xFFFFFFFE, 3), 5)
        self.assertEqual(M.wrap_delta(10, 14), 4)

    def test_short_bursts_do_not_establish_recovery(self):
        gates = {n: {"imu_gate_hz": 150, "uwb_gate_hz": 6} for n in M.NODES}
        streams = {n: {"imu": [], "uwb": []} for n in M.NODES}
        # 49 ms and 207 ms islands contain both streams but cannot prove 2 s.
        streams["BSFEC35"] = {"imu": [(1.000, 10), (1.049, 10), (2.000, 10), (2.207, 10)],
                               "uwb": [1.000, 1.049, 2.000, 2.207]}
        segs = [
            {"event_id":"E1","node":"BSFEC35","onset_lower":0.0,"recovered_monotonic":1.0,"terminal_at_stop":False,"classification":"STEADY_STATE_HOST_WEDGE"},
            {"event_id":"E2","node":"BSFEC35","onset_lower":1.049,"recovered_monotonic":2.0,"terminal_at_stop":False,"classification":"STEADY_STATE_HOST_WEDGE"},
            {"event_id":"E3","node":"BSFEC35","onset_lower":2.207,"recovered_monotonic":None,"terminal_at_stop":True,"classification":"DEPLETION_OR_BROWNOUT"},
        ]
        eps, mapping, sensitivity = M.cluster_segments(segs, streams, gates)
        target = next(x for x in eps if x["node"] == "BSFEC35")
        self.assertEqual(target["segment_ids"], ["E1", "E2", "E3"])
        self.assertEqual(mapping["E1"], mapping["E3"])
        self.assertFalse(sensitivity[0]["checks"]["2"]["pass"])

    def test_sustained_recovery_splits_episode(self):
        imu, uwb = healthy_stream(30)
        streams = {n: {"imu": [], "uwb": []} for n in M.NODES}
        streams["BSFEC35"] = {"imu": imu, "uwb": uwb}
        gates = {n: {"imu_gate_hz": 150, "uwb_gate_hz": 6} for n in M.NODES}
        segs = [
            {"event_id":"E1","node":"BSFEC35","onset_lower":-1.0,"recovered_monotonic":0.0,"terminal_at_stop":False,"classification":"STEADY_STATE_HOST_WEDGE"},
            {"event_id":"E2","node":"BSFEC35","onset_lower":25.0,"recovered_monotonic":None,"terminal_at_stop":True,"classification":"STEADY_STATE_HOST_WEDGE"},
        ]
        eps, mapping, _ = M.cluster_segments(segs, streams, gates)
        self.assertNotEqual(mapping["E1"], mapping["E2"])
        self.assertEqual(sum(x["node"] == "BSFEC35" for x in eps), 2)

    def test_multiscale_windows_and_receiver_aggregation(self):
        times = {"R1": [x / 10 for x in range(0, 200)], "R2": [x / 10 for x in range(0, 200)]}
        row = M.air_metrics(times, 10.0, 12.0, 0.0, 20.0)
        self.assertEqual(row["windows"]["5"]["sum_pre"], 100)
        self.assertEqual(row["windows"]["5"]["sum_post"], 100)
        self.assertEqual(row["windows"]["5"]["status"], "COMPLETE")
        self.assertEqual(row["windows"]["20"]["status"], "INCOMPLETE")
        self.assertEqual(row["receiver_agreement"], 2)
        self.assertIn("1", row["rolling_rates"])
        self.assertIn("5", row["rolling_rates"])
        self.assertAlmostEqual(row["longest_air_gap_s_per_receiver"]["R1"], .1)

    def test_local_matched_control_finds_air_degradation(self):
        # Five receivers deliver 40 observations per 5 s control bin, then all
        # fall to 10 observations per bin for three consecutive bins.
        times = {}
        for receiver in ("R1", "R2", "R3", "R4", "R5"):
            control = [75 + i * .125 for i in range(200)]
            degraded = [100 + i * .5 for i in range(30)]
            times[receiver] = control + degraded
        result = M.first_air_degradation(times, 0.0, 120.0, 100.0)
        self.assertEqual(result["baseline_window"], [75.0, 100.0])
        self.assertEqual(result["first_degradation_monotonic"], 100.0)

    def test_dual_stream_exposure_rejects_single_stream_bins(self):
        streams = {n: {"imu": [], "uwb": []} for n in M.NODES}
        for n in M.NODES:
            streams[n]["imu"] = [(i / 20, 10) for i in range(12000)]
            streams[n]["uwb"] = [i / 8 for i in range(4800)]
        # Remove UWB from a 10-second interval on one node; IMU alone must not count.
        streams["BSFEC35"]["uwb"] = [t for t in streams["BSFEC35"]["uwb"] if not 100 <= t < 110]
        out = M.exposure_v2(streams, 0.0, 600.0, 600.0)
        self.assertLess(out["widths"]["1"]["nodes"]["BSFEC35"]["exposure_s"], 600.0)
        self.assertEqual(out["widths"]["10"]["nodes"]["BSFEC35"]["exposure_s"], 590.0)

    def test_malformed_line_accounting(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "f.log"
            p.write_text("bad FUSION_RX line\n1.0 2.0 FUSION_RX  pool0=1\n1.0 2.0 FUSION_RX FUSION_IMU name=BSFEC35\n")
            _, _, _, audit = M.scan_fusion(p, 0, 10, 0, 10)
            self.assertEqual(audit["accounting"]["bad_header"], 1)
            self.assertEqual(audit["accounting"]["continuation_line"], 1)
            self.assertEqual(audit["accounting"]["FUSION_IMU:missing_n"], 1)
            self.assertEqual(audit["malformed_total"], 2)

    def test_immutable_v1_archived_hashes(self):
        archive = HERE.parents[2] / "logs/v47_afternoon_20260810_160905/analysis"
        if not archive.exists():
            self.skipTest("verified archive not mounted")
        expected = {
            "EVENT_TIMELINE.json": "dfcb55ec7c7518adcf820ddcac80b38118b88dc23139f373e656e1fa50c9b3e8",
            "EXPOSURE.json": "d4ab92de45583c8acb42ba825f11040613a47293dbbdeb6ae0ffe5a43da4d9d8",
        }
        for name, digest in expected.items():
            self.assertEqual(M.sha256(archive / name), digest)


if __name__ == "__main__":
    unittest.main()
