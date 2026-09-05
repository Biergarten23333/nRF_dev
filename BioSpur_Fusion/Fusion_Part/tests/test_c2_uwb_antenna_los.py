from __future__ import annotations

import numpy as np
import pytest

from biospur_fusion.c2_uwb_calibration.antenna_los import (
    outward_facing_reliability,
    horizontal_yaw_alignment,
    outward_facing_score,
    outward_normal_world,
    select_best_geometry,
)


def test_outward_facing_reliability_is_soft_bounded_and_monotone() -> None:
    values = [outward_facing_reliability(score) for score in (-1.0, 0.0, 1.0)]
    assert values == [0.25, 0.5, 0.75]
    assert values[0] < values[1] < values[2]


def test_minus_z_outward_and_plus_z_body_side_are_not_reversed() -> None:
    identity_wxyz = np.array([1.0, 0.0, 0.0, 0.0])
    normal = outward_normal_world("BSF31CC", identity_wxyz, np.eye(3))
    np.testing.assert_allclose(normal, [1.0, 0.0, 0.0], atol=1e-12)
    assert outward_facing_score([0, 0, 0], [2, 0, 0], normal) == pytest.approx(1.0)
    assert outward_facing_score([0, 0, 0], [-2, 0, 0], normal) == pytest.approx(-1.0)


def test_facing_abef_maps_forward_to_negative_layout_y() -> None:
    alignment = horizontal_yaw_alignment([1, 0, 0], [0, -1, 0])
    np.testing.assert_allclose(alignment @ [1, 0, 0], [0, -1, 0], atol=1e-12)
    assert np.linalg.det(alignment) == pytest.approx(1.0)


def test_link_selection_keeps_highest_scores_and_never_invents_four_links() -> None:
    scores = {anchor: float(anchor) for anchor in range(8)}
    assert select_best_geometry(range(8), scores, target_count=4) == (7, 6, 5, 4)
    assert select_best_geometry([0, 1, 2], scores, target_count=4) == ()
    with pytest.raises(ValueError, match="every valid anchor"):
        select_best_geometry([0, 1, 2, 3], {0: 1.0}, target_count=4)
