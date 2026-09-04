from __future__ import annotations

import numpy as np
from scipy.spatial.transform import Rotation

from biospur_fusion.c2_articulated_biomechanics.model import (
    HINGE_SPECS,
    DOWN,
    HingeJoint,
    _rotation,
    hinge_coordinate_deg,
)
from biospur_fusion.c2_articulated_biomechanics.orientation_ik import (
    reconstruct_distal_orientation,
    solve_hinge_flexion_deg,
)


def test_functional_actions_are_bound_to_the_matching_joint() -> None:
    assert HINGE_SPECS["elbow_left"][2] == "05"
    assert HINGE_SPECS["elbow_right"][2] == "06"
    assert HINGE_SPECS["knee_left"][2] == "09"
    assert HINGE_SPECS["knee_right"][2] == "10"


def _wxyz(rotation: Rotation) -> np.ndarray:
    q = rotation.as_quat()
    return np.c_[q[:, 3], q[:, :3]]


def _joint() -> HingeJoint:
    return HingeJoint(
        name="elbow_right",
        parent="upper_arm_right",
        child="forearm_right",
        functional_episode="06",
        parent_axis=(1.0, 0.0, 0.0),
        child_axis=(1.0, 0.0, 0.0),
        neutral_parent_from_child_xyzw=(0.0, 0.0, 0.0, 1.0),
        positive_sign=1.0,
        minimum_deg=-5.0,
        maximum_deg=150.0,
        neutral_frame_count=1,
        functional_frame_count=1,
    )


def test_hinge_coordinate_preserves_signed_flexion() -> None:
    parent = _wxyz(Rotation.identity(2))
    child = _wxyz(Rotation.from_rotvec(np.radians([[30.0, 0, 0], [-20.0, 0, 0]])))
    np.testing.assert_allclose(
        hinge_coordinate_deg(parent, child, _joint()), [30.0, -20.0], atol=1e-10
    )


def test_reconstruction_sets_fk_direction_and_preserves_axial_twist() -> None:
    parent = _wxyz(Rotation.identity(3))
    flexion = np.array([20.0, 55.0, 90.0])
    target = Rotation.from_rotvec(np.radians(np.c_[flexion, np.zeros((3, 2))]))
    # Add rotation about the child's own long axis.  Direction-only IK must
    # retain it while restoring the requested parent-to-child bend.
    child = _wxyz(target * Rotation.from_rotvec(
        np.c_[np.zeros((3, 2)), np.radians([15.0, -25.0, 40.0])]
    ))

    corrected, metrics = reconstruct_distal_orientation(
        parent, child, flexion, _joint()
    )

    expected_down = target.apply(DOWN)
    np.testing.assert_allclose(
        _rotation(corrected).apply(DOWN), expected_down, atol=1e-12
    )
    assert metrics["fk_direction_residual_maximum_deg"] <= 1e-6


def test_orientation_ik_uses_unsigned_bend_and_caps_rom() -> None:
    parent = _wxyz(Rotation.identity(3))
    child = _wxyz(Rotation.from_rotvec(
        np.radians([[30.0, 0.0, 0.0], [-40.0, 0.0, 0.0], [170.0, 0.0, 0.0]])
    ))

    flexion, metrics = solve_hinge_flexion_deg(parent, child, _joint())

    np.testing.assert_allclose(flexion, [30.0, 40.0, 150.0], atol=1e-10)
    assert metrics["above_rom_count"] == 1
