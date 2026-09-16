import json

import numpy as np
import pytest

from biospur_fusion.c2_coupled_progressive.continuous_calibration_inputs import (
    EffectivePelvisBiasHistory, build_continuous_factor_tape,
)
from biospur_fusion.c2_coupled_progressive.contracts import NODE_TO_SEGMENT


ORIGIN = 234836221471621


def _frontend(tmp_path, *, gap=False):
    n = 240
    ticks = np.arange(n)
    native = ticks * 5000
    common = ORIGIN + ticks * 5_000_100  # real clock scale is not nominal 5ms
    spans = np.zeros(n, dtype=np.int64)
    if gap:
        native[120:] += 10000
        common[120:] += 10_000_200
        spans[120:] = 1
    for i, node in enumerate(NODE_TO_SEGMENT):
        acc = np.column_stack([ticks * .001 + i, ticks * .002, np.full(n, 9.81)])
        np.savez(tmp_path / f"{node}.npz", time_us=native, boot_epoch=np.ones(n, dtype=np.int64),
                 common_global_ns=common + i * 1000, contiguous_span_id=spans,
                 acc_mps2=acc, gyro_rads=np.tile([.2, .1, .3], (n, 1)),
                 quat_vqf_sensor_wxyz=np.tile([1., 0, 0, 0], (n, 1)))
    (tmp_path / "RESULT.json").write_text(json.dumps({"regions": [
        {"kind": "ACTION", "region_id": "00"},
        {"kind": "INTER_ACTION_GAP", "region_id": "between"},
        {"kind": "ACTION", "region_id": "19"}]}))
    return common


def test_zero_correction_retains_exact_inputs_and_full_inter_action_coverage(tmp_path):
    times = _frontend(tmp_path)
    baseline = build_continuous_factor_tape(tmp_path)
    zero = EffectivePelvisBiasHistory(np.array([times[0]]), np.zeros((1, 3)), "zero-test")
    corrected = build_continuous_factor_tape(tmp_path, pelvis_bias=zero)
    assert len(baseline.episodes) == 1 and len(baseline.episodes[0].spans) == 9
    for a, b in zip(baseline.episodes[0].spans, corrected.episodes[0].spans):
        for name in ("parent_acc_mps2", "child_acc_mps2", "parent_gyro_rads", "child_gyro_rads",
                     "parent_quat_wxyz", "child_quat_wxyz", "parent_indices", "child_indices"):
            np.testing.assert_array_equal(getattr(a, name), getattr(b, name))
        assert len(a.parent_indices) == 240
        assert not a.parent_acc_mps2.flags.writeable
        assert np.median(np.diff(a.time_root_s)) == pytest.approx(.0050001, abs=1e-10)
        assert np.median(np.diff(a.parent_time_s)) == pytest.approx(.005)
    assert baseline.alignment_audit["source_sha256"] == corrected.alignment_audit["source_sha256"]
    assert not corrected.alignment_audit["inter_action_rows_excluded"]
    assert not corrected.alignment_audit["parameter_fit_executed"]


def test_pelvis_effective_bias_only_changes_connected_inputs_and_never_uses_future(tmp_path):
    times = _frontend(tmp_path)
    baseline = build_continuous_factor_tape(tmp_path)
    history = EffectivePelvisBiasHistory(
        np.array([times[40], times[120]]), np.array([[.3, .2, .1], [.6, .4, .2]]),
        "root-effective-sensor-ba", np.array([times[80], times[160]]))
    corrected = build_continuous_factor_tape(tmp_path, pelvis_bias=history)
    changed = []
    for a, b in zip(baseline.episodes[0].spans, corrected.episodes[0].spans):
        np.testing.assert_array_equal(a.child_acc_mps2, b.child_acc_mps2)
        np.testing.assert_array_equal(a.parent_quat_wxyz, b.parent_quat_wxyz)
        if a.parent_segment == "pelvis":
            changed.append(a.edge)
            np.testing.assert_array_equal(a.parent_acc_mps2[:80], b.parent_acc_mps2[:80])
            np.testing.assert_allclose(a.parent_acc_mps2[80:160] - b.parent_acc_mps2[80:160],
                                       np.tile([.3, .2, .1], (80, 1)))
            np.testing.assert_allclose(a.parent_acc_mps2[160:] - b.parent_acc_mps2[160:],
                                       np.tile([.6, .4, .2], (80, 1)))
        else:
            np.testing.assert_array_equal(a.parent_acc_mps2, b.parent_acc_mps2)
    assert set(changed) == {"pelvis_torso", "hip_left", "hip_right"}
    assert baseline.alignment_audit["source_sha256"] == corrected.alignment_audit["source_sha256"]


def test_true_native_gap_splits_spans_without_filling_or_rebasing(tmp_path):
    _frontend(tmp_path, gap=True)
    tape = build_continuous_factor_tape(tmp_path)
    spans = tape.episodes[0].spans
    assert len(spans) == 18
    for first, second in zip(spans[::2], spans[1::2]):
        assert len(first.parent_indices) == len(second.parent_indices) == 120
        assert first.parent_indices[-1] == 119 and second.parent_indices[0] == 120
        assert second.parent_time_s[0] - first.parent_time_s[-1] == pytest.approx(.015)
        assert second.time_root_s[0] - first.time_root_s[-1] == pytest.approx(.0150003, abs=1e-10)
    assert all(row["factor_rows"] == 240 for row in tape.alignment_audit["pairs"].values())


def test_bias_history_rejects_unavailable_past_or_nonfinite():
    with pytest.raises(ValueError):
        EffectivePelvisBiasHistory(np.array([10]), np.zeros((1, 3)), "test", np.array([9]))
    with pytest.raises(ValueError):
        EffectivePelvisBiasHistory(np.array([10]), np.array([[np.nan, 0, 0]]), "test")
