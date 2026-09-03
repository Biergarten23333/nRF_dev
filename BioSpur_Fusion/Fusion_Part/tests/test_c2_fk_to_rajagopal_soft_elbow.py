import numpy as np
import pytest

from biospur_fusion.c2_fk_to_rajagopal_soft_elbow.candidate import (
    ELBOW_FRAME_BASIS,
    OUT_OF_PLANE_WEIGHT,
    SOURCE_MODEL_SHA256,
)
from biospur_fusion.c2_fk_to_rajagopal_soft_elbow.sensitivity import (
    WEIGHT_RATIOS,
    _profile_summary,
)
from biospur_fusion.c2_fk_to_rajagopal_soft_elbow.proxy_render import (
    BRANCHES,
    PROTOCOL_BINDING,
)


def test_source_and_diagnostic_weight_constants_are_frozen():
    assert SOURCE_MODEL_SHA256 == "7b66b82bc932e161f08405cc409b786774ff0038354c0e937adea0a5d02717ea"
    assert OUT_OF_PLANE_WEIGHT == pytest.approx(0.001, abs=0.0)


def test_basis_is_proper_and_preserves_pin_z_as_universal_x():
    assert np.linalg.det(ELBOW_FRAME_BASIS) == pytest.approx(1.0)
    assert ELBOW_FRAME_BASIS @ np.array([1.0, 0.0, 0.0]) == pytest.approx([0, 0, 1])
    assert ELBOW_FRAME_BASIS @ np.array([0.0, 1.0, 0.0]) == pytest.approx([1, 0, 0])


def test_soft_weight_is_the_conservative_engineering_ratio():
    assert OUT_OF_PLANE_WEIGHT == 0.001


def test_weight_sensitivity_schema_uses_flat_orientation_summary():
    result = {
        "out_of_plane_weight": 0.1,
        "passed": True,
        "max_off_target_crosstalk_rad": 0.01,
        "minimum_range_margin_rad": 0.5,
        "wall_s": 1.0,
        "rows": [
            {
                "solved_delta_rad": [0.03, 0.0, 0.04],
                "target_mask": [True, False, True],
            }
        ],
        "orientation_errors": {
            "overall_mean_rad": 0.001,
            "overall_p95_rad": 0.002,
            "overall_max_rad": 0.003,
        },
    }
    summary = _profile_summary(result)
    assert WEIGHT_RATIOS == (0.0, 0.01, 0.03, 0.1, 0.3, 1.0)
    assert summary["minimum_target_response_rad"] == pytest.approx(0.03)
    assert summary["orientation_error_p95_rad"] == pytest.approx(0.002)


def test_protocol_and_real_branch_provenance_are_explicit():
    assert PROTOCOL_BINDING["02"] == {
        "frozen_source_key": "01",
        "display_label": "02_t_pose",
    }
    assert BRANCHES["B0_no_prior"]["weight"] == 0.0
    assert BRANCHES["B001_weak_prior"]["weight"] == 0.001
