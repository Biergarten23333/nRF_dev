from __future__ import annotations

import math

import numpy as np

from biospur_fusion.c2_fk_ik_diagnostic_biomechanics.pipeline import (
    _quat_conjugate,
    _quat_geodesic,
    _quat_multiply,
    _quat_to_matrix_rows,
    _rotation_error,
    generalized_relative_quaternions,
    hinge_diagnostic,
)


def _axis_quat(axis, angle):
    axis = np.asarray(axis, dtype=float)
    axis /= np.linalg.norm(axis)
    return np.array([math.cos(angle / 2.0), *(axis * math.sin(angle / 2.0))])


def test_active_hamilton_relative_round_trip():
    parent = _axis_quat([1.0, 2.0, -1.0], 0.4)
    relative = _axis_quat([0.0, 1.0, 0.0], -0.7)
    child = _quat_multiply(parent, relative)
    recovered = _quat_multiply(_quat_conjugate(parent), child)
    assert float(_quat_geodesic(_quat_multiply(_quat_conjugate(relative), recovered))) < 1e-14


def test_hinge_diagnostic_separates_line_and_out_of_line_motion():
    angles = np.linspace(0.0, 0.5, 21)
    hinge_rows = np.stack([_axis_quat([1.0, 0.0, 0.0], angle) for angle in angles])
    hinge = hinge_diagnostic(hinge_rows, np.array([1.0, 0.0, 0.0]), np.array([1.0, 0.0, 0.0]))
    assert hinge["hinge_energy_fraction"] > 1.0 - 1e-12
    off_rows = np.stack([_axis_quat([0.0, 1.0, 0.0], angle) for angle in angles])
    off = hinge_diagnostic(off_rows, np.array([1.0, 0.0, 0.0]), np.array([1.0, 0.0, 0.0]))
    assert off["hinge_energy_fraction"] < 1e-12


def test_relative_function_uses_parent_inverse_side():
    class Series:
        def __init__(self, q):
            self.quat_world_segment_wxyz = np.asarray(q)

    class Episode:
        pass

    episode = Episode()
    parent = _axis_quat([0.0, 0.0, 1.0], 0.3)
    relative = _axis_quat([1.0, 0.0, 0.0], 0.2)
    child = _quat_multiply(parent, relative)
    episode.segments = {"p": Series([parent]), "c": Series([child])}
    observed = generalized_relative_quaternions(episode, "p", "c")[0]
    error = _quat_geodesic(_quat_multiply(_quat_conjugate(relative), observed))
    assert float(error) < 1e-14


def test_rotation_error_is_stable_near_identity():
    angle = 1e-12
    matrix = _quat_to_matrix_rows(np.asarray([_axis_quat([0.0, 0.0, 1.0], angle)]))[0]
    assert abs(_rotation_error(np.eye(3), matrix) - angle) < 1e-15
