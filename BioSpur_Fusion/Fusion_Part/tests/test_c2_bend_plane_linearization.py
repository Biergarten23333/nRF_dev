import numpy as np
import pytest
from scipy.spatial.transform import Rotation

from biospur_fusion.c2_articulated_biomechanics.bend_plane import reconcile_hinge_bend_plane
from biospur_fusion.c2_articulated_biomechanics.bend_plane_linearization import linearize_bend_plane_projection
from biospur_fusion.c2_articulated_biomechanics.model import HingeJoint, DOWN, _rotation, _wxyz
from biospur_fusion.c2_uwb_calibration.articulated_range import SEGMENTS


JOINT = HingeJoint('knee', 'thigh_left', 'shank_left', '09', (1., 0., 0.),
                   (1., 0., 0.), (0., 0., 0., 1.), 1., 0., 120., 1, 1)
MODEL = {'knee': JOINT}


def pose(bend=.4):
    rotations = Rotation.from_rotvec(np.tile([.2, -.1, .3], (10, 1))).as_matrix()
    rotations[7] = rotations[6] @ Rotation.from_rotvec([0., bend, .1]).as_matrix()
    return rotations


def independent_projection(rotations, state=0.):
    output = rotations.copy()
    p, c, _ = reconcile_hinge_bend_plane(
        _wxyz(Rotation.from_matrix(rotations[6:7])),
        _wxyz(Rotation.from_matrix(rotations[7:8])), JOINT,
        initial_twist_rad=state)
    output[6] = _rotation(p).as_matrix()[0]
    output[7] = _rotation(c).as_matrix()[0]
    return output


def test_preserves_endpoints_natural_bend_and_physical_inputs():
    raw = pose()
    original = raw.copy()
    projected, jacobian, audit = linearize_bend_plane_projection(raw, SEGMENTS, MODEL)
    assert jacobian.shape == (30, 30)
    np.testing.assert_allclose(projected @ DOWN, raw @ DOWN, atol=1e-12)
    np.testing.assert_array_equal(raw, original)
    np.testing.assert_allclose(projected[7], raw[7], atol=1e-12)
    assert np.linalg.norm(projected[6] - raw[6]) > .1
    assert np.linalg.norm(projected[6] @ DOWN - projected[7] @ DOWN) > .2
    assert audit['knee']['physical_sensor_orientation_modified'] is False


def test_full_right_local_jacobian_matches_independent_dense_differences():
    raw = pose()
    projected, jacobian, _ = linearize_bend_plane_projection(raw, SEGMENTS, MODEL)
    epsilon = 3e-7
    dense = np.empty((30, 30))
    for column in range(30):
        perturbation = np.zeros((10, 3))
        perturbation.flat[column] = epsilon
        sides = []
        for sign in (1., -1.):
            trial = raw @ Rotation.from_rotvec(sign * perturbation).as_matrix()
            result = independent_projection(trial)
            sides.append(Rotation.from_matrix(projected.swapaxes(1, 2) @ result).as_rotvec().ravel())
        dense[:, column] = (sides[0] - sides[1]) / (2 * epsilon)
    np.testing.assert_allclose(jacobian, dense, atol=2e-8, rtol=2e-7)
    assert np.linalg.norm(jacobian[18:21, 21:24]) > .1
    np.testing.assert_allclose(jacobian[:18, :18], np.eye(18), atol=1e-12)


def test_near_extension_holds_explicit_state_without_zeroing_bend():
    raw = np.tile(np.eye(3), (10, 1, 1))
    raw[7] = Rotation.from_rotvec([0., .001, 0.]).as_matrix()
    state = {'knee': .7}
    projected, jacobian, audit = linearize_bend_plane_projection(
        raw, SEGMENTS, MODEL, initial_twist_rad=state)
    np.testing.assert_allclose(projected, independent_projection(raw, .7), atol=1e-12)
    np.testing.assert_allclose(projected @ DOWN, raw @ DOWN, atol=1e-12)
    np.testing.assert_allclose(jacobian[18:21, 21:24], 0., atol=1e-12)
    assert audit['knee']['linearization_branch'] == 'held_twist'
    assert audit['knee']['last_twist_rad'] == .7
    assert state == {'knee': .7}


@pytest.mark.parametrize('bend', [.5, 120.])
def test_branch_boundary_is_explicitly_rejected(bend):
    raw = np.tile(np.eye(3), (10, 1, 1))
    raw[7] = Rotation.from_rotvec([0., np.radians(bend), 0.]).as_matrix()
    with pytest.raises(ValueError, match='branch boundary'):
        linearize_bend_plane_projection(raw, SEGMENTS, MODEL)


def test_upper_rom_caps_and_empty_model_is_identity():
    raw = np.tile(np.eye(3), (10, 1, 1))
    raw[7] = Rotation.from_rotvec([0., np.radians(140), 0.]).as_matrix()
    projected, jacobian, audit = linearize_bend_plane_projection(raw, SEGMENTS, MODEL)
    bend = np.degrees(np.arccos((projected[6] @ DOWN) @ (projected[7] @ DOWN)))
    assert bend == pytest.approx(120.)
    assert np.isfinite(jacobian).all()
    assert audit['knee']['upper_rom_capped_rows'] == 1
    output, jacobian, audit = linearize_bend_plane_projection(raw, SEGMENTS, {})
    np.testing.assert_array_equal(output, raw)
    np.testing.assert_array_equal(jacobian, np.eye(30))
    assert audit == {}


def test_overlapping_pairs_are_not_silently_treated_as_independent():
    with pytest.raises(ValueError, match='disjoint'):
        linearize_bend_plane_projection(pose(), SEGMENTS, {'a': JOINT, 'b': JOINT})
