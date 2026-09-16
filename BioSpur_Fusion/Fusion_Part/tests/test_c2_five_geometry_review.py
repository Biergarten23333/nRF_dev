"""Synthetic only: actual displayed joints can expose a mirrored bend."""
import numpy as np
import pytest
from scipy.spatial.transform import Rotation

from biospur_fusion.c2_coupled_progressive.output_coordinates import freeze_capture_wide_lateral_reflection
from tools.c2_five_geometry_review import (
    LIMBS, compare_display_geometry, C2_TO_SMPL_BODY,
    freeze_five_output_coordinates, five_fk_to_output,
)


def skeleton(bends=(90.,)):
    joints = np.zeros((len(bends), 13, 3))
    for limb, (a, b, c) in enumerate(LIMBS.values()):
        joints[:, a] = [limb * .4, limb * .1, 1.]
        joints[:, b] = joints[:, a] + [0., 0., -.3]
        angles = np.deg2rad(bends)
        joints[:, c] = joints[:, b] + .25 * np.column_stack((np.sin(angles),
                                                           np.zeros(len(angles)), -np.cos(angles)))
    return joints


def test_mirrored_ninety_degree_bend_has_opposite_oriented_plane():
    target = skeleton()
    mirrored = target.copy()
    mirrored[:, 3] = mirrored[:, 2] + [-.25, 0., 0.]
    result = compare_display_geometry(mirrored, target, np.array([True]))['limbs']
    left = result['elbow_left']
    assert left['unsigned_bend_error']['mean_deg'] < 1e-12
    assert left['oriented_plane_normal_error']['mean_deg'] == 180.
    assert left['proximal_direction_error']['mean_deg'] == 0.
    assert left['distal_direction_error']['mean_deg'] == 180.
    assert result['elbow_right']['oriented_plane_normal_error']['mean_deg'] == 0.


def test_common_rigid_rotation_and_translation_preserve_comparison():
    target = skeleton((35., 90., 130.))
    candidate = skeleton((45., 70., 110.))
    rotation = Rotation.from_rotvec([.7, -.4, .3]).as_matrix()
    valid = np.array([True, False, True])
    before = compare_display_geometry(candidate, target, valid)
    after = compare_display_geometry(candidate @ rotation.T + [1., -2., 3.],
                                     target @ rotation.T + [1., -2., 3.], valid)
    assert after['valid_frames'] == 2
    for name in LIMBS:
        for metric in ('proximal_direction_error', 'distal_direction_error',
                       'unsigned_bend_error', 'oriented_plane_normal_error'):
            for statistic in ('mean_deg', 'p95_deg', 'maximum_deg'):
                np.testing.assert_allclose(after['limbs'][name][metric][statistic],
                                           before['limbs'][name][metric][statistic], atol=1e-12)


def test_planes_near_straight_or_fully_folded_are_unavailable():
    target = skeleton((90., 90.))
    candidate = skeleton((5., 175.))
    result = compare_display_geometry(candidate, target, np.array([True, True]))
    assert result['plane_min_bend_deg'] == 10.
    assert result['acceptance_threshold_added'] is False
    for row in result['limbs'].values():
        assert row['compared_bone_frames'] == 2
        assert row['compared_plane_frames'] == 0
        assert row['excluded_weak_plane_frames'] == 2
        assert row['oriented_plane_normal_error']['mean_deg'] is None
        assert row['unsigned_bend_error']['mean_deg'] > 0.


def test_equal_displayed_geometry_has_exactly_zero_errors():
    joints = skeleton((30., 90., 145.))
    result = compare_display_geometry(joints, joints.copy(), np.ones(3, dtype=bool))
    for row in result['limbs'].values():
        assert row['compared_plane_frames'] == 3
        for metric in ('proximal_direction_error', 'distal_direction_error',
                       'unsigned_bend_error', 'oriented_plane_normal_error'):
            assert row[metric] == {'mean_deg': 0., 'p95_deg': 0., 'maximum_deg': 0.}


# Independent fixed basis declarations; no model/FK assets or recorded arrays.
W = np.array([[0., 1., 0.], [0., 0., 1.], [1., 0., 0.]])
B = np.array([[0., 1., 0.], [-1., 0., 0.], [0., 0., 1.]])
R10_WORLD = B.copy()


def mature_freeze(rotations):
    quaternion = Rotation.from_matrix(rotations).as_quat()[:, [3, 0, 1, 2]]
    trajectory = {'trajectory': {'00': {'pelvis': {'quat_world_segment_wxyz': quaternion}}}}
    convention = freeze_capture_wide_lateral_reflection(trajectory)
    return np.asarray(convention['matrix_world_output_from_internal']), trajectory


@pytest.mark.parametrize('yaw5,yaw10,tilt', [(0., 0., [0., 0., 0.]),
    (.8, -.4, [.12, -.18, 0.]), (-1.2, 1.1, [-.21, .15, 0.])])
def test_independent_native_bases_match_after_both_frozen_reflections(yaw5, yaw10, tilt):
    variation = np.array([-.03, .01, .05])
    local_tilt = Rotation.from_rotvec(tilt).as_matrix()
    own = Rotation.from_rotvec(np.column_stack([variation * 0, variation * 0, yaw5 + variation])).as_matrix() @ local_tilt
    other = Rotation.from_rotvec(np.column_stack([variation * 0, variation * 0, yaw10 + variation])).as_matrix() @ local_tilt
    q5 = W @ own @ W.T
    q10 = R10_WORLD.T @ other @ B
    convention = freeze_five_output_coordinates(q5, W)
    m5 = np.asarray(convention['matrix_world_output_from_internal'])
    m10, _ = mature_freeze(q10)
    np.testing.assert_allclose(C2_TO_SMPL_BODY, W @ B, atol=1e-15)
    np.testing.assert_allclose(W.T @ q5 @ C2_TO_SMPL_BODY, own @ B, atol=1e-15)
    left5 = (m5 @ W.T @ q5) @ np.array([1., 0., 0.])
    left10 = (R10_WORLD @ m10 @ q10) @ np.array([-1., 0., 0.])
    a, b = left10.mean(0), left5.mean(0)
    yaw = np.arctan2(b[1], b[0]) - np.arctan2(a[1], a[0])
    align = Rotation.from_rotvec([0., 0., yaw]).as_matrix()
    body = skeleton((30., 90., 130.))
    native5 = body @ own.swapaxes(-1, -2) @ W.T
    native10 = body @ other.swapaxes(-1, -2) @ R10_WORLD
    saved, time = native5.copy(), np.array([.1, .4, .8])
    saved_time = time.copy()
    actual = five_fk_to_output(native5, convention, source_space='SMPL_FK')
    reference = native10 @ m10.T @ R10_WORLD.T @ align.T
    np.testing.assert_allclose(actual, reference, atol=2e-15)
    # Both candidate and prior use the exact same boundary, across every frame.
    np.testing.assert_array_equal(actual, five_fk_to_output(native5.copy(), convention, source_space='SMPL_FK'))
    np.testing.assert_array_equal(native5, saved)
    np.testing.assert_array_equal(time, saved_time)
    for matrix in (C2_TO_SMPL_BODY, W, R10_WORLD, align):
        np.testing.assert_allclose(np.linalg.det(matrix), 1., atol=1e-15)
    for matrix in (m5, m10):
        np.testing.assert_allclose(matrix.T @ matrix, np.eye(3), atol=1e-15)
        np.testing.assert_allclose(matrix @ matrix, np.eye(3), atol=1e-15)
        np.testing.assert_allclose(matrix @ [0., 0., 1.], [0., 0., 1.], atol=1e-15)
        np.testing.assert_allclose(np.linalg.det(matrix), -1., atol=1e-15)
    np.testing.assert_allclose([np.linalg.det(m5 @ W.T), np.linalg.det(align @ R10_WORLD @ m10)], [-1., -1.], atol=1e-15)
    for start, middle, end in LIMBS.values():
        for a, b in ((start, middle), (middle, end)):
            np.testing.assert_allclose(np.linalg.norm(actual[:, b] - actual[:, a], axis=-1),
                                       np.linalg.norm(native5[:, b] - native5[:, a], axis=-1), atol=1e-15)
    rows = compare_display_geometry(actual, native5, np.ones(3, dtype=bool))['limbs']
    assert max(row['unsigned_bend_error']['maximum_deg'] for row in rows.values()) < 1e-12
    rows = compare_display_geometry(actual, reference, np.ones(3, dtype=bool))['limbs']
    assert max(row['oriented_plane_normal_error']['maximum_deg'] for row in rows.values()) < 1e-12


def test_old_one_sided_reflection_cannot_be_repaired_by_left_axis_yaw():
    body = skeleton()
    q5 = np.eye(3)[None]
    convention = freeze_five_output_coordinates(q5, W)
    native5 = body @ W.T
    m10, _ = mature_freeze(np.eye(3)[None])
    old_align = Rotation.from_rotvec([0., 0., np.pi]).as_matrix()
    native10 = body @ R10_WORLD
    old_reference = native10 @ m10.T @ R10_WORLD.T @ old_align.T
    old_candidate = native5 @ W
    assert np.max(np.abs(old_reference - old_candidate)) > .4
    row = compare_display_geometry(old_candidate, old_reference, np.array([True]))['limbs']['elbow_left']
    assert row['unsigned_bend_error']['mean_deg'] < 1e-12
    np.testing.assert_allclose(row['oriented_plane_normal_error']['mean_deg'], 180., atol=1e-12)
    correct = five_fk_to_output(native5, convention, source_space='SMPL_FK')
    np.testing.assert_allclose(correct, native10 @ m10.T @ R10_WORLD.T, atol=1e-15)


def test_normal_is_axial_and_declared_output_cannot_be_reflected_twice():
    convention = freeze_five_output_coordinates(np.eye(3)[None], W)
    matrix = np.asarray(convention['matrix_world_output_from_internal'])
    u, v = np.array([0., 0., -1.]), np.array([1., .3, 0.])
    normal = np.cross(u, v)
    np.testing.assert_allclose(np.cross(matrix @ u, matrix @ v), np.linalg.det(matrix) * matrix @ normal)
    assert not np.allclose(np.cross(matrix @ u, matrix @ v), matrix @ normal)
    with pytest.raises(ValueError, match='untransformed'):
        five_fk_to_output(skeleton(), convention, source_space='DISPLAY_OUTPUT')
    _, trajectory = mature_freeze(np.eye(3)[None])
    with pytest.raises(ValueError, match='already frozen'):
        freeze_capture_wide_lateral_reflection(trajectory)


def test_freeze_uses_all_finite_own_frames_and_fails_for_unobservable_axis():
    own = Rotation.from_rotvec([[0., 0., -.2], [0., 0., .6]]).as_matrix()
    q = W @ own @ W.T
    q = np.concatenate([q, np.full((1, 3, 3), np.nan)])
    convention = freeze_five_output_coordinates(q, W)
    assert convention['initial_frame_count'] == 3
    assert convention['finite_initial_frame_count'] == convention['reference_frame_count'] == 2
    expected, _ = mature_freeze(own @ B)
    np.testing.assert_allclose(convention['matrix_world_output_from_internal'], expected, atol=1e-15)
    first = freeze_five_output_coordinates(q[:1], W)
    assert not np.allclose(first['matrix_world_output_from_internal'], expected)
    with pytest.raises(ValueError, match='no finite'):
        freeze_five_output_coordinates(q[2:], W)
    vertical = Rotation.from_rotvec([0., np.pi/2, 0.]).as_matrix()[None]
    with pytest.raises(RuntimeError, match='not observable'):
        freeze_five_output_coordinates(W @ vertical @ C2_TO_SMPL_BODY.T, W)
    with pytest.raises(ValueError, match='proper'):
        freeze_five_output_coordinates(np.zeros((1, 3, 3)), W)
