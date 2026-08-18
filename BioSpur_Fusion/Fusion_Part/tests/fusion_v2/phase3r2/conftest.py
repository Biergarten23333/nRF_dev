from __future__ import annotations

import pytest


@pytest.fixture
def mapping():
    return {
        "BSFEC35": "forearm_left", "BSFB165": "forearm_right",
        "BSFAA61": "upper_arm_left", "BSF1120": "upper_arm_right",
        "BSF31CC": "torso", "BSFC2CC": "pelvis",
        "BSF44AD": "thigh_left", "BSF3C79": "thigh_right",
        "BSF6C53": "shank_left", "BSF8BC4": "shank_right",
    }


@pytest.fixture
def fit_actions():
    return (
        "00_initial_still", "02_t_pose", "03_pelvis_hula_circle",
        "04_shoulder_left", "05_shoulder_right", "06_elbow_left",
        "07_elbow_right", "08_hip_left", "09_hip_right",
        "10_knee_left_seated", "11_knee_right_seated", "12_heel_raise_left",
        "13_heel_raise_right", "14_trunk_flex_extend", "15_trunk_axial_rotation",
        "16_squat", "18_heel_to_butt_left", "19_heel_to_butt_right",
    )
