import numpy as np

from biospur_fusion.c2_fk_to_scaled_opensense.radioulnar_fix import (
    FRAME_BODY_BY_SEGMENT_RADIUS,
    FRAME_BY_SEGMENT_RADIUS,
    PHASES_S,
    _rotation_angle,
)


def test_forearms_are_uniquely_owned_by_radius():
    assert FRAME_BODY_BY_SEGMENT_RADIUS["forearm_left"] == "radius_l"
    assert FRAME_BODY_BY_SEGMENT_RADIUS["forearm_right"] == "radius_r"
    assert FRAME_BY_SEGMENT_RADIUS["forearm_left"] == "radius_l_imu"
    assert FRAME_BY_SEGMENT_RADIUS["forearm_right"] == "radius_r_imu"
    assert len(set(FRAME_BY_SEGMENT_RADIUS.values())) == 10


def test_phases_are_full_nonoverlapping_half_open_intervals():
    assert PHASES_S == {
        "flexion": (0.0, 15.0),
        "pronation_supination": (15.0, 30.0),
    }


def test_rotation_angle_is_proper_so3_geodesic():
    angle = 0.31
    rotation = np.array(
        [
            [1.0, 0.0, 0.0],
            [0.0, np.cos(angle), -np.sin(angle)],
            [0.0, np.sin(angle), np.cos(angle)],
        ]
    )
    assert np.isclose(np.linalg.det(rotation), 1.0, atol=1e-15)
    assert np.isclose(_rotation_angle(np.eye(3), rotation), angle, atol=1e-15)
