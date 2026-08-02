#!/usr/bin/env python3
import unittest

import numpy as np

from time_aligner_v2 import (
    GRID_US,
    BoardData,
    classify_two_bands,
    master_integer_shifts,
    reconstruct_epoch_index,
)


class EpochReconstructionTests(unittest.TestCase):
    def test_performed_sweep_counter_replay(self):
        # Consecutive public sweeps occur only when work was performed.  Missing
        # beacon epochs must be recovered from 110/220 ms timestamp gaps.
        epochs_true = np.array([0, 1, 3, 4, 6, 8, 9, 10])
        timer = 7_000_000.0 + epochs_true * GRID_US * (1 + 15e-6)
        epochs, multiples, period, worst = reconstruct_epoch_index(timer)
        np.testing.assert_array_equal(epochs, epochs_true)
        np.testing.assert_array_equal(multiples, np.diff(epochs_true))
        self.assertAlmostEqual(period, GRID_US * (1 + 15e-6), delta=0.01)
        self.assertLess(worst, 0.01)


class BandTests(unittest.TestCase):
    def test_two_component_band(self):
        rng = np.random.default_rng(7)
        clean = rng.normal(0, 80, 9000)
        delayed = rng.normal(2300, 90, 1000)
        residual = np.r_[clean, delayed]
        clean_mask, delayed_mask, offset, lo, hi = classify_two_bands(residual)
        self.assertAlmostEqual(delayed_mask.mean(), 0.10, delta=0.005)
        self.assertAlmostEqual(offset, 2300, delta=10)
        self.assertLess(lo, 2300)
        self.assertGreater(hi, 2300)


class IntegerTests(unittest.TestCase):
    def _board(self, name, master_ms):
        return BoardData(name, [0, 1, 2], master_ms, [0, 1, 2], [0, 0, 0],
                         [0, 0, 0], [0, 0, 0], [], [], [], [], [])

    def test_integer_disambiguation_with_ble_delay(self):
        from types import SimpleNamespace
        boards = {
            "BSF3C79": self._board("BSF3C79", [1000, 1112, 1221]),
            "BSFTEST": self._board("BSFTEST", [1331, 1442, 1550]),
        }
        fits = {
            "BSF3C79": SimpleNamespace(epoch_index=np.array([0, 1, 2])),
            "BSFTEST": SimpleNamespace(epoch_index=np.array([0, 1, 2])),
        }
        out = master_integer_shifts(boards, fits, {"BSF3C79": 1, "BSFTEST": 2})
        self.assertEqual(out["nodes"]["BSFTEST"]["relative_integer"], 3)
        self.assertGreater(out["nodes"]["BSFTEST"]["safety_margin_us"], 40_000)


if __name__ == "__main__":
    unittest.main()
