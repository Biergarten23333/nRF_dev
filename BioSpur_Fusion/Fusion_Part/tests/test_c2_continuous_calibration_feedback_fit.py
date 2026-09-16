from dataclasses import replace
import json

import numpy as np

from biospur_fusion.c2_coupled_progressive import estimator
from tools.run_c2_continuous_calibration_feedback_fit import (
    factor_input_identity, fit_or_reuse, parameter_document,
)


def _input():
    n = 100
    vector = np.tile([.1, .2, 9.8], (n, 1))
    span = estimator.AlignedSpan(
        0, "QA_ONLY", "hip_left", 0, "pelvis", "thigh_left",
        np.arange(n), np.arange(n), np.arange(n)*.005, np.arange(n)*.005,
        np.arange(n)*.005, vector, vector.copy(), vector.copy(), vector.copy(),
        np.tile([1., 0, 0, 0], (n, 1)), np.tile([1., 0, 0, 0], (n, 1)),
        .001, "COMMON_CLOCK_NATIVE_PAIRING_NOT_CORRELATION_PEAK", {})
    window = estimator.WindowFactor(0, "hip_left", 0, 0, n, .5, .1, .2, 1.)
    return span, (window,)


def test_factor_identity_uses_arrays_and_windows_not_side_label():
    span, windows = _input()
    baseline = factor_input_identity("center", span, windows)
    assert baseline == factor_input_identity("center", replace(span, qa_label="B"), windows)
    assert baseline == factor_input_identity("center", replace(span, parent_acc_mps2=span.parent_acc_mps2.copy()), windows)
    changed = span.parent_acc_mps2.copy()
    changed[3, 1] += .001
    assert baseline != factor_input_identity("center", replace(span, parent_acc_mps2=changed), windows)
    assert baseline != factor_input_identity("center", span, (replace(windows[0], weight=.6),))
    assert baseline != factor_input_identity("hinge", span, windows)


def test_exact_identity_cache_reuses_without_fit_and_parameter_shapes(tmp_path, monkeypatch):
    span, windows = _input()
    count = []
    def fake_fit(s, w):
        count.append(1)
        return estimator.CenterFactor(0, "QA_ONLY", "hip_left", 0, "PASS", .1,
                                      100, 6, 1., .2, np.ones(3)*.1, np.ones(3)*.2,
                                      np.ones(6)*.01, "PASS")
    monkeypatch.setattr(estimator, "center_factor", fake_fit)
    a, key, reused = fit_or_reuse("center", span, windows, tmp_path)
    b, key_b, reused_b = fit_or_reuse("center", replace(span, qa_label="B"), windows, tmp_path)
    assert not reused and reused_b and len(count) == 1 and key == key_b
    np.testing.assert_array_equal(a.parent_vector_sensor_m, b.parent_vector_sensor_m)
    state = estimator.prior_state()
    state.centers[b.edge].add(b)
    state.refresh_mounts_and_branches()
    saved = json.loads(json.dumps(estimator.jsonable(parameter_document(state)), allow_nan=False))
    assert len(saved["mounts"]) == 10 and len(saved["hinges"]) == 4
    for row in saved["mounts"].values():
        assert np.asarray(row["sensor_from_segment"]).shape == (3, 3)
        assert np.asarray(row["covariance_diagonal_rad2"]).shape == (3,)
    for row in saved["hinges"].values():
        assert np.asarray(row["parent_axis_sensor"]).shape == (3,)
    for row in saved["centers"].values():
        assert np.asarray(row["parent_sensor_m"]).shape == (3,)
        assert np.asarray(row["covariance_diagonal_m2"]).shape == (6,)
