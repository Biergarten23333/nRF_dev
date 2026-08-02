import unittest

from batch_g_overnight_core import (
    AliveBook,
    SnapshotHealth,
    active_cfg,
    composed_idle_cfg,
    tag_domain_rate_hz,
)


class BatchGOvernightCoreTests(unittest.TestCase):
    def test_backlog_burst_cannot_inflate_tag_domain_rate(self):
        # The same tag-domain endpoints are assigned radically different host
        # arrival shapes.  Host time is intentionally not accepted by the API.
        evenly_spaced_host_arrivals = [float(i) for i in range(101)]
        bunched_host_arrivals = [0.0] * 100 + [100.0]
        self.assertNotEqual(
            evenly_spaced_host_arrivals, bunched_host_arrivals
        )
        even = tag_domain_rate_hz(1000, 1100, 5000, 5100, 110_000)
        burst = tag_domain_rate_hz(1000, 1100, 5000, 5100, 110_000)
        self.assertEqual(even, burst)
        self.assertAlmostEqual(even, 100 / 11.0)

    def test_rate_handles_u32_wrap(self):
        rate = tag_domain_rate_hz(
            0xFFFFFFF0, 0x00000009, 0xFFFFFFFE, 0x00000003, 100_000
        )
        self.assertAlmostEqual(rate, 25 / 0.5)

    def test_cfg_builders_are_explicit_and_quarantined(self):
        active = active_cfg(10, 10, beacon_win_n=3)
        idle = composed_idle_cfg(10, 10, 11)
        self.assertIn("BEACON_SYNC=1", active)
        self.assertIn("BEACON_WIN_N=3", active)
        self.assertIn("DW_ANCHOR=0", active)
        self.assertIn("BEACON_SYNC=0", idle)
        self.assertIn("RUN=0 PMODE=3", idle)
        self.assertNotIn("DW_ANCHOR=1", active + idle)

    def test_alive_ledgers_close_per_connection_epoch(self):
        book = AliveBook(("BSFTEST",))
        book.connected("BSFTEST", 1.0, {"drop": 10})
        book.seen("BSFTEST", 2.0)
        book.disconnected("BSFTEST", 3.0, "battery", {"drop": 12})
        book.connected("BSFTEST", 4.0, {"drop": 20})
        snapshot = book.snapshot()["BSFTEST"]
        self.assertEqual(snapshot[0]["last_seen_monotonic"], 2.0)
        self.assertEqual(snapshot[0]["ledger_deltas"], {"drop": 2})
        self.assertIsNone(snapshot[1]["closed_monotonic"])

    def test_two_snapshot_misses_degrade_but_do_not_kill(self):
        health = SnapshotHealth()
        health.observe(False, True)
        self.assertFalse(health.degraded)
        health.observe(False, True)
        self.assertTrue(health.degraded)
        health.observe(True, True)
        self.assertFalse(health.degraded)


if __name__ == "__main__":
    unittest.main()
