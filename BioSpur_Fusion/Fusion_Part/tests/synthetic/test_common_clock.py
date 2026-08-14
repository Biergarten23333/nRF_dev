from collections import Counter
import numpy as np
from biospur_fusion.time.common_clock import (
    ListenerPoll, UwbClockAnchor, _match_node, _robust_line, reconstruct_local_epochs,
)
import pytest


def test_whole_epoch_drift_and_arrival_jitter_do_not_set_measurement_time():
    rng = np.random.default_rng(4702); n = 1000; offset = 23120
    epoch = np.arange(n); timer = 3_000_000 + epoch * 120_001.8 + rng.normal(0, 40, n)
    host = 1000 + epoch * .12 + rng.normal(0, .015, n)
    anchors = [UwbClockAnchor("BSF0001", host[i], int(host[i]*1000), i, int(timer[i]+14000),
                              int(timer[i]), True, int((epoch[i] + offset - 1) & 15)) for i in range(n)]
    recovered, period = reconstruct_local_epochs(a.strobe_us for a in anchors)
    polls = [ListenerPoll("L", 0xB101, i & 255, int(i + offset), 13900.0, host[i] + rng.normal(0, .01))
             for i in range(n)]
    selected, margin, pairs, details = _match_node(anchors, recovered, polls, 0xB101)
    assert selected == offset and margin > 0 and len(pairs) == n
    assert details["mod16_agreement_fraction"] == 1.0
    assert abs(period - 120001.8) < 1.0


def test_clean_clock_residual_gate_and_outlier_accounting():
    x = np.arange(1000.0); y = 1000.015*x + 77
    y[123] += 2000; y[700] -= 1500
    slope, intercept, clean = _robust_line(x, y)
    residual = y - (slope*x + intercept); centred = residual - np.median(residual[clean])
    assert (~clean).sum() == 2
    assert np.percentile(abs(centred[clean]), 95) < 500 and max(abs(centred[clean])) < 1000


def test_timer_reversal_requires_an_explicit_boot_segment():
    with pytest.raises(ValueError, match="TIMER2 reversal"):
        reconstruct_local_epochs([100_000, 220_000, 12_000, 132_000])
