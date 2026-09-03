import math

import pytest

from biospur_fusion.c2_fk_to_opensim_soft_elbow.soft_elbow import (
    OUT_OF_PLANE_SIGMA_RAD,
    OUT_OF_PLANE_WEIGHT,
    SOFT_COORDINATES,
)


def test_uncertainty_owned_weight_is_frozen():
    assert OUT_OF_PLANE_SIGMA_RAD == pytest.approx(0.553178986187994, abs=0.0)
    assert OUT_OF_PLANE_WEIGHT == pytest.approx(1.0 / 0.553178986187994**2)


def test_only_elbow_out_of_plane_coordinates_are_soft_referenced():
    assert SOFT_COORDINATES == ("elbow_left_ry", "elbow_right_ry")


def test_weight_is_broad_finite_information_not_a_hard_constraint():
    assert math.isfinite(OUT_OF_PLANE_WEIGHT)
    assert 1.0 < OUT_OF_PLANE_WEIGHT < 10.0
