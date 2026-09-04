from __future__ import annotations

import numpy as np

from biospur_fusion.c2_native200_calibration import load_native200_pose_reset_module
from biospur_fusion.c2_native200_calibration.legacy_adapter import transformed_source
from tools.build_c2_native200_ab_viewer import _comparison_audit


def test_native200_adapter_is_exact_and_time_equivalent() -> None:
    _, audit = transformed_source()
    assert audit["source_rate_hz"] == 20.0
    assert audit["target_rate_hz"] == 200.0
    assert audit["window_width_s"] == 2.0
    assert audit["window_stride_s"] == 1.0
    assert [row["count"] for row in audit["replacements"]] == [1, 3, 3]


def test_native200_windows_preserve_two_second_width_and_one_second_stride() -> None:
    native, _ = load_native200_pose_reset_module()
    starts = native._qmt_window_starts(7_001)
    assert starts[:3] == [0, 200, 400]
    assert starts[-1] == 6_601
    time_s = np.arange(400, dtype=float) * 0.005
    assert np.isclose(time_s[-1] - time_s[0] + 0.005, 2.0)


def test_ab_viewer_requires_same_geometry_identity_and_elapsed_time() -> None:
    left = {
        "jointNames": ["pelvis_center"],
        "lines": [],
        "episodes": [{"id": "00_initial_still", "time": [0.0, 0.05]}],
    }
    right = {
        "jointNames": ["pelvis_center"],
        "lines": [],
        "episodes": [{"id": "00_initial_still", "time": [0.0, 0.005, 0.01]}],
    }
    audit = _comparison_audit(left, right)
    assert audit["same_fk_display_geometry"] is True
    assert audit["same_elapsed_time_playhead"] is True
    assert audit["same_world_camera"] is True
    assert audit["viewer_interpolation"] is False
    assert audit["episodes"][0]["common_duration_s"] == 0.01
