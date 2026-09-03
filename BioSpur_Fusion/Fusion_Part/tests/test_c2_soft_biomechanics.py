from __future__ import annotations

import math

import numpy as np

from biospur_fusion.c2_soft_biomechanics.pipeline import (
    _circular_mean,
    _proper_shortest_x_to_axis,
    _wrap_pi,
)
from biospur_fusion.c2_soft_biomechanics.report import _segment_distance_3d


def test_shortest_arc_is_proper_and_maps_x_to_axis():
    axis = np.array([0.31, -0.72, 0.62])
    axis /= np.linalg.norm(axis)
    rotation = _proper_shortest_x_to_axis(axis)
    assert np.allclose(rotation.T @ rotation, np.eye(3), atol=1e-12)
    assert math.isclose(float(np.linalg.det(rotation)), 1.0, abs_tol=1e-12)
    assert np.allclose(rotation @ np.array([1.0, 0.0, 0.0]), axis, atol=1e-12)


def test_same_frame_conjugation_preserves_rotation_and_neutral():
    axis = np.array([0.2, 0.7, -0.4])
    axis /= np.linalg.norm(axis)
    proper = _proper_shortest_x_to_axis(axis)
    theta = 0.63
    skew = np.array([[0.0, -axis[2], axis[1]], [axis[2], 0.0, -axis[0]], [-axis[1], axis[0], 0.0]])
    rotation = np.eye(3) + math.sin(theta) * skew + (1.0 - math.cos(theta)) * (skew @ skew)
    chart = proper.T @ rotation @ proper
    assert np.allclose(proper @ chart @ proper.T, rotation, atol=1e-12)
    assert np.allclose(proper @ np.eye(3) @ proper.T, np.eye(3), atol=1e-12)
    assert math.isclose(float(np.linalg.det(chart)), 1.0, abs_tol=1e-12)


def test_circular_mean_and_wrap_cross_pi_without_linear_branch_error():
    values = np.array([math.pi - 0.02, -math.pi + 0.02])
    mean = _circular_mean(values)
    residual = _wrap_pi(values - mean)
    assert np.max(np.abs(residual)) < 0.021


def test_segment_distance_distinguishes_3d_clearance_from_projected_crossing():
    horizontal = (np.array([-1.0, 0.0, 0.0]), np.array([1.0, 0.0, 0.0]))
    crossing = (np.array([0.0, -1.0, 0.0]), np.array([0.0, 1.0, 0.0]))
    elevated = (np.array([0.0, -1.0, 0.25]), np.array([0.0, 1.0, 0.25]))
    assert math.isclose(_segment_distance_3d(*horizontal, *crossing), 0.0, abs_tol=1e-14)
    assert math.isclose(_segment_distance_3d(*horizontal, *elevated), 0.25, abs_tol=1e-14)
