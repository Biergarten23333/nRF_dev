import numpy as np

from biospur_fusion.c2_fk_to_scaled_opensense.pipeline import (
    BODY_BY_SEGMENT,
    CALIBRATION_SLICE,
    C2_FROM_OPENSIM,
    FRAME_BY_SEGMENT,
    _robust_wxyz,
)


def test_official_imu_names_resolve_to_body_owners():
    assert {FRAME_BY_SEGMENT[key] for key in BODY_BY_SEGMENT} == {
        f"{body}_imu" for body in BODY_BY_SEGMENT.values()
    }


def test_calibration_window_is_fixed_central_351_rows():
    assert (CALIBRATION_SLICE.start, CALIBRATION_SLICE.stop) == (175, 526)


def test_robust_mean_is_quaternion_sign_invariant():
    rows = np.array([[1.0, 0.0, 0.0, 0.0], [-1.0, 0.0, 0.0, 0.0]])
    assert np.allclose(_robust_wxyz(rows), [1.0, 0.0, 0.0, 0.0])


def test_basis_inverse_is_proper_rotation():
    assert np.allclose(C2_FROM_OPENSIM.T @ C2_FROM_OPENSIM, np.eye(3))
    assert np.isclose(np.linalg.det(C2_FROM_OPENSIM), 1.0)
