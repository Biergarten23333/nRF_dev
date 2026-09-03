import numpy as np

from biospur_fusion.root_r3.viewer import _indices, _series


def test_viewer_downsample_is_deterministic_and_retains_extent():
    time_s = np.arange(1000, dtype=float)
    first = _indices(time_s, 37)
    second = _indices(time_s, 37)
    assert np.array_equal(first, second)
    assert first[0] == 0
    assert first[-1] == 999


def test_viewer_series_retains_rejection_and_mode_transition():
    time_s = np.arange(200, dtype=float)
    position = np.c_[time_s, np.zeros(200), np.zeros(200)]
    accepted = np.ones(200, bool); accepted[73] = False
    mode = np.full(200, "NOMINAL", dtype="U16"); mode[125:] = "RECOVERY"
    value = _series("x", "diagnostic", time_s, position, np.ones_like(position), mode, accepted, maximum=20)
    assert False in value["accepted"]
    assert "RECOVERY" in value["mode"]
