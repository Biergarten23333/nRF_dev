import importlib.util
import sys
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "fusion_session.py"
sys.path.insert(0, str(MODULE_PATH.parent))
SPEC = importlib.util.spec_from_file_location("fusion_session", MODULE_PATH)
fusion_session = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = fusion_session
SPEC.loader.exec_module(fusion_session)


class FusionSessionParserTest(unittest.TestCase):
    def test_rtt_transport_cli_uses_explicit_dk_probe(self):
        args = fusion_session.build_parser().parse_args(
            [
                "start",
                "--bsf",
                "BSF3C79",
                "--path",
                "master",
                "--transport",
                "rtt",
            ]
        )
        self.assertEqual(args.transport, "rtt")
        self.assertEqual(args.rtt_serial_number, 683234364)
        self.assertEqual(args.rtt_address, 0x20002100)

    def test_reply_parser_preserves_text(self):
        reply = fusion_session.parse_reply(
            "FUSION_REPLY proto=2 name=BSF3C79 master_ms=10 source=TAG "
            "correlation=42 text=CFG_OK TAG=1 LIVE=1"
        )
        self.assertEqual(reply.source, "TAG")
        self.assertEqual(reply.correlation, 42)
        self.assertEqual(reply.text, "CFG_OK TAG=1 LIVE=1")

    def test_multi_peer_records_are_filtered_by_target(self):
        class ScriptedChannel:
            def __init__(self):
                self.lines = iter(
                    (
                        "FUSION_TELEMETRY proto=4 name=BSF8BC4 node_ms=900",
                        "FUSION_REPLY proto=2 name=BSF8BC4 master_ms=10 "
                        "source=B306 correlation=1 text=STATUS wrong",
                        "FUSION_TELEMETRY proto=4 name=BSF3C79 node_ms=100",
                    )
                )

            def read(self, _deadline):
                return next(self.lines, None)

        controller = fusion_session.FusionController(
            ScriptedChannel(), "BSF3C79", 0.1, 1
        )
        fields = controller.wait_telemetry()
        self.assertEqual(fields["name"], "BSF3C79")
        self.assertEqual(fields["node_ms"], "100")
        self.assertEqual(controller.latest_telemetry["name"], "BSF3C79")

    def test_multi_peer_collect_excludes_other_nodes(self):
        class ScriptedChannel:
            def __init__(self):
                self.lines = [
                    "FUSION_UWB proto=2 name=BSF8BC4 verdict=healthy",
                    "FUSION_UWB proto=2 name=BSF3C79 verdict=healthy",
                ]

            def read(self, _deadline):
                if self.lines:
                    return self.lines.pop(0)
                return None

        controller = fusion_session.FusionController(
            ScriptedChannel(), "BSF3C79", 0.1, 1
        )
        lines = controller.collect(0.01)
        self.assertEqual(
            lines,
            ["FUSION_UWB proto=2 name=BSF3C79 verdict=healthy"],
        )

    def test_split_telemetry_is_returned_only_after_all_parts_merge(self):
        class ScriptedChannel:
            def __init__(self):
                self.lines = iter(
                    (
                        "FUSION_TELEMETRY proto=4 name=BSF3C79 node_ms=200 "
                        "part=1/2 record=BSF3C79-200 frames=9 crc=0",
                        "FUSION_COMMAND_TX target=BSF3C79 len=8 err=0 "
                        "line=BSF3C79 STATUS",
                        "FUSION_TELEMETRY proto=4 name=BSF3C79 node_ms=200 "
                        "part=2/2 record=BSF3C79-200 imu_pulls=40 "
                        "logger_drop=0",
                    )
                )

            def read(self, _deadline):
                return next(self.lines, None)

        controller = fusion_session.FusionController(
            ScriptedChannel(), "BSF3C79", 0.1, 1
        )
        fields = controller.wait_telemetry()
        self.assertEqual(fields["record"], "BSF3C79-200")
        self.assertEqual(fields["parts"], "2")
        self.assertEqual(fields["frames"], "9")
        self.assertEqual(fields["imu_pulls"], "40")
        self.assertEqual(fields["logger_drop"], "0")

    def test_imu_sequence_wrap_has_no_gap(self):
        lines = [
            "FUSION_IMU proto=2 master_ms=1 seq=65534 base_us=1 n=2 "
            "temp_raw=1 samples=-",
            "FUSION_IMU proto=2 master_ms=2 seq=0 base_us=2 n=2 "
            "temp_raw=1 samples=-",
        ]
        self.assertEqual(fusion_session.imu_sequence_gaps(lines), (0, 2))

    def test_sentinel_passes_clean_window(self):
        baseline = {
            "node_ms": "1000",
            "frames": "0",
            "rise_n": "0",
            "imu_rate": "200",
            "imu_batch": "2",
            "imu_active": "0",
            **{name: "0" for name in fusion_session.ANOMALY_COUNTERS},
        }
        final = {
            **baseline,
            "node_ms": "11000",
            "frames": "100",
            "rise_n": "100",
            "imu_active": "1",
        }
        lines = [
            f"FUSION_UWB proto=2 master_ms={i} verdict=healthy"
            for i in range(100)
        ]
        lines.extend(
            f"FUSION_IMU proto=2 master_ms={i} seq={i * 2} "
            f"base_us={i} n=2 temp_raw=1 samples=-"
            for i in range(1000)
        )
        result = fusion_session.evaluate_uwb_window(
            lines, baseline, final, 10.0, require_imu=True
        )
        self.assertTrue(result["pass"], result["reasons"])

    def test_sentinel_rejects_orphan_and_seq_gap(self):
        baseline = {
            "node_ms": "1000",
            "frames": "0",
            "rise_n": "0",
            "imu_rate": "200",
            "imu_batch": "2",
            "imu_active": "0",
            **{name: "0" for name in fusion_session.ANOMALY_COUNTERS},
        }
        final = {
            **baseline,
            "node_ms": "11000",
            "frames": "100",
            "rise_n": "100",
            "imu_active": "1",
            "orphan_frame": "1",
        }
        lines = [
            f"FUSION_UWB proto=2 master_ms={i} verdict=healthy"
            for i in range(100)
        ]
        lines.extend(
            (
                "FUSION_IMU proto=2 master_ms=1 seq=0 base_us=1 n=2 "
                "temp_raw=1 samples=-",
                "FUSION_IMU proto=2 master_ms=2 seq=4 base_us=2 n=2 "
                "temp_raw=1 samples=-",
            )
        )
        result = fusion_session.evaluate_uwb_window(
            lines, baseline, final, 10.0, require_imu=True
        )
        self.assertFalse(result["pass"])
        self.assertEqual(result["imu_seq_gaps"], 1)
        self.assertEqual(result["counter_deltas"]["orphan_frame"], 1)

    def test_master_tag_controller_rejects_cfg_stop_without_relay3_marker(self):
        class NeverUsedChannel:
            def send(self, _command):
                raise AssertionError("forbidden command reached the transport")

            def collect(self, _wait_s):
                return []

        controller = fusion_session.MasterTagController(NeverUsedChannel(), 1)
        for command in ("CFG_STOP", "cmd CFG_STOP", "cmd_all CFG_STOP"):
            with self.subTest(command=command):
                with self.assertRaisesRegex(
                    fusion_session.SessionError, "CFG_STOP is forbidden"
                ):
                    controller._send_collect(command, 0.0)

    def test_master_tag_controller_rejects_cfg_stop_even_with_new_marker(self):
        class NeverUsedChannel:
            def send(self, _command):
                raise AssertionError("forbidden command reached the transport")

            def collect(self, _wait_s):
                return []

        controller = fusion_session.MasterTagController(
            NeverUsedChannel(), 1, tag_marker="tag-fusion-link-v2-relay4"
        )
        with self.assertRaisesRegex(
            fusion_session.SessionError, "CFG_STOP is forbidden"
        ):
            controller._send_collect("cmd CFG_STOP", 0.0)

    def test_master_tag_clear_uses_mode_idle(self):
        class RecordingChannel:
            def __init__(self):
                self.commands = []

            def send(self, command):
                self.commands.append(command)

            def collect(self, _wait_s):
                return []

        channel = RecordingChannel()
        controller = fusion_session.MasterTagController(channel, 1)
        controller.clear()
        self.assertEqual(channel.commands, ["cmd_all MODE IDLE", "tdma clear"])
        self.assertNotIn("CFG_STOP", " ".join(channel.commands))


if __name__ == "__main__":
    unittest.main()
