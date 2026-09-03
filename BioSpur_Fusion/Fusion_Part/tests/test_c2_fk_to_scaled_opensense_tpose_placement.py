import math

import numpy as np

from biospur_fusion.c2_fk_to_scaled_opensense.tpose_placement import (
    C2_TO_OPENSIM,
    ROOT_GAUGE_FRAME_ORIENTATION_XYZ_RAD,
    TPOSE_DEFAULTS_RAD,
    _rotation_angle,
)


def test_tpose_protocol_is_bilateral_and_handed():
    assert TPOSE_DEFAULTS_RAD["pelvis_rotation"] == 0.0
    assert TPOSE_DEFAULTS_RAD["pelvis_tilt"] == 0.0
    assert TPOSE_DEFAULTS_RAD["pelvis_list"] == 0.0
    assert ROOT_GAUGE_FRAME_ORIENTATION_XYZ_RAD == (0.0, -math.pi / 2.0, 0.0)
    assert TPOSE_DEFAULTS_RAD["arm_add_l"] == math.pi / 2
    assert TPOSE_DEFAULTS_RAD["arm_add_r"] == math.pi / 2
    for name in (
        "arm_flex_l",
        "arm_flex_r",
        "arm_rot_l",
        "arm_rot_r",
        "elbow_flex_l",
        "elbow_flex_r",
        "pro_sup_l",
        "pro_sup_r",
    ):
        assert TPOSE_DEFAULTS_RAD[name] == 0.0


def test_c2_to_opensim_is_proper_basis_rotation():
    assert np.allclose(C2_TO_OPENSIM.T @ C2_TO_OPENSIM, np.eye(3), atol=1e-15)
    assert np.isclose(np.linalg.det(C2_TO_OPENSIM), 1.0, atol=1e-15)


def test_rotation_angle_identity():
    assert _rotation_angle(np.eye(3), np.eye(3)) == 0.0
