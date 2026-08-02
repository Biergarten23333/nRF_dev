#!/usr/bin/env python3
"""Offline-only tests for listener_array_collector.py."""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).with_name("listener_array_collector.py")
SPEC = importlib.util.spec_from_file_location("listener_array_collector", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
collector = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = collector
SPEC.loader.exec_module(collector)


class ParserTests(unittest.TestCase):
    def test_lpd_full_extensions(self) -> None:
        line = (
            "LPD;1;255;255;1234;55;9;3;0xb103;0xffff;4294967280;-123;"
            "100;101;102;103;104;105;106;17;0xff;"
            "rcph=7;rxtofs=-8;ttcki=99;agc=5"
        )
        kind, fields = collector.parse_listener_line(line)
        self.assertEqual(kind, "LPD")
        self.assertEqual(fields["listener_t_ms"], 1234)
        self.assertEqual(fields["src"], 0xB103)
        self.assertEqual(fields["rx_ts_lo32"], 0xFFFFFFF0)
        self.assertEqual(fields["carrier_integrator"], -123)
        self.assertEqual(fields["rxtofs"], -8)
        self.assertEqual(fields["poll_mask"], 0xFF)

    def test_lrd_and_status(self) -> None:
        lrd = (
            "LRD;1;255;255;1235;66;9;7;0xa107;0xb103;1234;44;"
            "1;2;3;4;5;6;7;20;rcph=1;rxtofs=2;ttcki=3;agc=4"
        )
        kind, fields = collector.parse_listener_line(lrd)
        self.assertEqual(kind, "LRD")
        self.assertEqual(fields["anchor_id"], 7)
        self.assertEqual(fields["dst"], 0xB103)

        lstat = (
            "LSTAT;1;255;255;100;20;3;4;5;6;7;8;0x01020304;0xb103;"
            "0xffff;0xe0;9;10;11;12;evc_fcg=13;evc_fce=14;evc_ovr=15;evc_sto=16"
        )
        kind, fields = collector.parse_listener_line(lstat)
        self.assertEqual(kind, "LSTAT")
        self.assertEqual(fields["ring_drops"], 9)
        self.assertEqual(fields["self_recover"], 10)
        self.assertEqual(fields["evc_ovr"], 15)

    def test_cir_records(self) -> None:
        kind, fields = collector.parse_listener_line("LCIRD;1;8;48;2;A1B2")
        self.assertEqual(kind, "LCIRD")
        self.assertEqual(fields["hex"], "A1B2")
        kind, fields = collector.parse_listener_line("LCIRE;1;8;4064")
        self.assertEqual(kind, "LCIRE")
        self.assertEqual(fields["acc_len"], 4064)

    def test_recognized_malformed_line_fails(self) -> None:
        with self.assertRaises(collector.ParseError):
            collector.parse_listener_line("LPD;1;255")


class UnwrapTests(unittest.TestCase):
    def test_low32_wrap_crossing(self) -> None:
        unwrap = collector.TimestampUnwrapper()
        first = 0xFFFFFFF0
        out0 = unwrap.add(first, 1000)
        delta = round(collector.DW_TICKS_PER_MS)
        second = (first + delta) & 0xFFFFFFFF
        out1 = unwrap.add(second, 1001)
        self.assertEqual(out1["rx_unwrapped_ticks"] - out0["rx_unwrapped_ticks"], delta)
        self.assertLess(second, first)
        self.assertEqual(out1["lo32_extra_wraps"], 0)
        self.assertGreater(out1["unwrap_choice_margin_ns"], 60_000_000)

    def test_100_ms_gap_spans_more_than_one_lo32_period(self) -> None:
        unwrap = collector.TimestampUnwrapper()
        first = 123456
        out0 = unwrap.add(first, 2000)
        delta = round(100 * collector.DW_TICKS_PER_MS)
        second = (first + delta) & 0xFFFFFFFF
        out1 = unwrap.add(second, 2100)
        self.assertEqual(out1["rx_unwrapped_ticks"] - out0["rx_unwrapped_ticks"], delta)
        self.assertEqual(out1["lo32_extra_wraps"], 1)

    def test_new_segment_resets_continuity(self) -> None:
        unwrap = collector.TimestampUnwrapper()
        unwrap.add(10, 1)
        unwrap.new_segment()
        result = unwrap.add(20, 2)
        self.assertEqual(result["rx_segment"], 1)
        self.assertEqual(result["rx_unwrapped_ticks"], 20)


class AttributionTests(unittest.TestCase):
    def test_merged_index_never_infers_listener(self) -> None:
        l0 = collector.LISTENERS[0]
        l1 = collector.LISTENERS[1]
        line = (
            b"LPD;1;255;255;100;1;2;3;0xb103;0xffff;99;0;"
            b"1;2;3;4;5;6;7;17;0xff\n"
        )
        a = collector.make_archive_record(
            l0, 1, line, 10, 20, collector.TimestampUnwrapper()
        )
        b = collector.make_archive_record(
            l1, 1, line, 11, 21, collector.TimestampUnwrapper()
        )
        ia = collector.make_index_record(a)
        ib = collector.make_index_record(b)
        self.assertEqual(ia["listener_snr"], l0.snr)
        self.assertEqual(ib["listener_snr"], l1.snr)
        self.assertNotEqual(ia["listener_snr"], ib["listener_snr"])
        self.assertEqual(ia["source_record_index"], 1)
        self.assertEqual(ib["source_record_index"], 1)

    def test_roster_excludes_protected_probes(self) -> None:
        collector.validate_roster()
        self.assertTrue(
            set(listener.snr for listener in collector.LISTENERS).isdisjoint(
                collector.FORBIDDEN_SNRS
            )
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
