import json

import numpy as np
import pytest

from biospur_fusion.root_r3.interfaces import load_frame_binding, strict_left_indices, yaw_rotation


def test_unqualified_frame_cannot_smuggle_rotation(tmp_path):
    path = tmp_path / "frame.json"
    path.write_text(json.dumps({"qualified": False, "R_N_from_V4": [[1, 0, 0], [0, 1, 0], [0, 0, 1]],
                                "reason": "BLOCKED"}))
    status = load_frame_binding(path)
    assert not status.qualified and status.rotation_navigation_from_v4 is None


def test_qualified_frame_must_be_proper(tmp_path):
    path = tmp_path / "frame.json"
    path.write_text(json.dumps({"qualified": True, "R_N_from_V4": [[1, 0, 0], [0, 1, 0], [0, 0, -1]],
                                "reason": "test"}))
    with pytest.raises(ValueError, match="proper"):
        load_frame_binding(path)


def test_strict_left_never_uses_future_sample():
    times = np.array([0.0, 0.1, 0.2])
    query = np.array([-0.1, 0.0, 0.05, 0.1, 0.19])
    indices = strict_left_indices(times, query)
    assert indices.tolist() == [-1, 0, 0, 1, 1]
    valid = indices >= 0
    assert np.all(times[indices[valid]] <= query[valid])


def test_heading_sensitivity_rotation_is_proper():
    for degrees in (-180, -90, 0, 45, 180):
        rotation = yaw_rotation(degrees)
        assert np.allclose(rotation.T @ rotation, np.eye(3))
        assert np.isclose(np.linalg.det(rotation), 1.0)
