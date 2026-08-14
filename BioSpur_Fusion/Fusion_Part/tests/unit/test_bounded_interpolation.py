import numpy as np
from biospur_fusion.body_graph.fixed_lag import interpolate_bounded


def test_never_interpolates_across_unbounded_gap():
    times = np.array([0.0, .1, 2.0]); values = np.c_[times, times]
    assert interpolate_bounded(times, values, 1.0, .2) is None
    assert np.allclose(interpolate_bounded(times, values, .05, .2), [.05, .05])
