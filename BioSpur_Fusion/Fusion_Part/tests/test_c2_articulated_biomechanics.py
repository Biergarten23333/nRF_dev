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
    project_hinge_corrections,
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


def test_public_projector_repairs_negative_incremental_hinge_update() -> None:
    base = {
        "upper_arm_right": np.eye(3),
        "forearm_right": Rotation.from_rotvec(
            np.radians([40.0, 0.0, 0.0])
        ).as_matrix(),
    }
    correction = {
        "upper_arm_right": np.zeros(3),
        # Propose enough opposite rotation to turn +40 degrees into -20.
        "forearm_right": np.radians(np.array([-60.0, 0.0, 0.0])),
    }
    joint = _joint()
    projected, metrics = project_hinge_corrections(
        base, correction, {joint.name: joint}
    )
    parent = Rotation.from_matrix([
        base[joint.parent] @ Rotation.from_rotvec(projected[joint.parent]).as_matrix()
    ])
    child = Rotation.from_matrix([
        base[joint.child] @ Rotation.from_rotvec(projected[joint.child]).as_matrix()
    ])
    coordinate = hinge_coordinate_deg(_wxyz(parent), _wxyz(child), joint)[0]

    assert metrics["pre_projection_below_rom_count"] == 1
    assert metrics["post_projection_all_inside_rom"]
    assert 0.0 <= coordinate <= joint.maximum_deg
    assert metrics["fk_direction_residual_maximum_deg"] <= 1e-6


def test_public_projector_reexpresses_stale_carry_on_changed_base() -> None:
    joint = _joint()
    first_base = {
        joint.parent: np.eye(3),
        joint.child: Rotation.from_rotvec(
            np.radians([40.0, 0.0, 0.0])
        ).as_matrix(),
    }
    raw = {
        joint.parent: np.zeros(3),
        joint.child: np.radians(np.array([-60.0, 0.0, 0.0])),
    }
    first, _ = project_hinge_corrections(first_base, raw, {joint.name: joint})
    # The native pose base has moved from +40 to +10 degrees.  Reusing the
    # old -20-degree right correction creates a negative bend; current-base
    # transport must re-express it and then be idempotent at this base.
    second_base = {
        joint.parent: np.eye(3),
        joint.child: Rotation.from_rotvec(
            np.radians([10.0, 0.0, 0.0])
        ).as_matrix(),
    }
    second, metrics = project_hinge_corrections(
        second_base, first, {joint.name: joint}
    )
    repeated, repeated_metrics = project_hinge_corrections(
        second_base, second, {joint.name: joint}
    )

    assert metrics["pre_projection_below_rom_count"] == 1
    assert metrics["post_projection_all_inside_rom"]
    assert repeated_metrics["post_projection_all_inside_rom"]
    for segment in second:
        np.testing.assert_allclose(repeated[segment], second[segment], atol=1e-12)
