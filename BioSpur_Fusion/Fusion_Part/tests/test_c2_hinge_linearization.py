import numpy as np
import pytest
from scipy.spatial.transform import Rotation

from biospur_fusion.c2_articulated_biomechanics.hinge_linearization import linearize_hinge_projection
from biospur_fusion.c2_articulated_biomechanics.model import HingeJoint
from biospur_fusion.c2_articulated_biomechanics.orientation_ik import evaluate_hinge_projection_batch
from biospur_fusion.c2_uwb_root_world.articulated_joint_filter import ArticulatedJointFilter
from biospur_fusion.c2_uwb_calibration.articulated_range import SEGMENTS
from biospur_fusion.root_r3.models import RootState
from test_c2_articulated_range import _geometry


def model():
    return {name: HingeJoint(name, parent, child, 'test', (1.,0.,0.), (1.,0.,0.),
                            (0.,0.,0.,1.), 1., 0., upper, 1, 1)
            for name, parent, child, upper in [
                ('elbow_left','upper_arm_left','forearm_left',150.),
                ('elbow_right','upper_arm_right','forearm_right',150.),
                ('knee_left','thigh_left','shank_left',120.),
                ('knee_right','thigh_right','shank_right',120.)]}


def dense_jacobian(rotations, hinges):
    inputs = np.zeros((31,10,3)); inputs[1:] = np.eye(30).reshape(30,10,3)*1e-6
    rows = []
    for start in (0,16):
        values, _ = evaluate_hinge_projection_batch(dict(zip(SEGMENTS, rotations)),
            {name:inputs[start:start+16,i] for i,name in enumerate(SEGMENTS)}, hinges)
        rows.extend(values)
    corrections = np.array([[row[name] for name in SEGMENTS] for row in rows])
    projected = rotations[None] @ Rotation.from_rotvec(corrections.reshape(-1,3)).as_matrix().reshape(31,10,3,3)
    local = projected[0].swapaxes(1,2)[None] @ projected[1:]
    return Rotation.from_matrix(local.reshape(-1,3,3)).as_rotvec().reshape(30,30).T/1e-6


@pytest.mark.parametrize('bend', [0., 1e-6, .4, 2.0943951023931953, 2.6179938779914944, 2.9])
def test_sparse_matches_existing_dense_covariance_projection(bend):
    rotations = Rotation.from_rotvec(np.tile([.17,-.23,.31], (10,1))).as_matrix()
    rotations[[3,5,7,9]] = rotations[[3,5,7,9]] @ Rotation.from_rotvec([bend,0.,0.]).as_matrix()
    root = RootState(0., np.zeros(9), np.eye(9)*.01)
    owner = ArticulatedJointFilter(root, rotations, geometry=_geometry(), hinges=model(),
                                   pelvis_mount_sensor_from_segment=np.eye(3))
    before = owner.state.covariance.copy()
    projected, jacobian, audit = linearize_hinge_projection(rotations, SEGMENTS, model())
    np.testing.assert_allclose(jacobian, dense_jacobian(rotations, model()), atol=2e-8, rtol=1e-8)
    owner.project_hinges()
    transform = np.eye(39); transform[9:,9:] = jacobian
    expected = transform @ before @ transform.T + np.eye(39)*1e-12
    np.testing.assert_allclose(projected, owner.state.rotations, atol=1e-13, rtol=0)
    np.testing.assert_allclose(expected, owner.state.covariance, atol=1e-9, rtol=1e-8)
    assert audit['post_projection_all_inside_rom']


def test_shared_parent_uses_dense_fallback():
    hinges = model()
    hinges['elbow_right'] = HingeJoint('elbow_right','upper_arm_left','forearm_right',
        'shared-parent-fixture', (1.,0.,0.), (1.,0.,0.), (0.,0.,0.,1.), 1.,0.,150.,1,1)
    rotations = Rotation.from_rotvec(np.arange(30).reshape(10,3)*.013).as_matrix()
    _, jacobian, _ = linearize_hinge_projection(rotations, SEGMENTS, hinges)
    np.testing.assert_array_equal(jacobian, dense_jacobian(rotations, hinges))


def test_empty_and_invalid_input():
    rotations = np.tile(np.eye(3), (10,1,1))
    actual, jacobian, _ = linearize_hinge_projection(rotations, SEGMENTS, {})
    np.testing.assert_array_equal(actual, rotations)
    np.testing.assert_array_equal(jacobian, np.eye(30))
    with pytest.raises(ValueError):
        linearize_hinge_projection(rotations, SEGMENTS, model(), epsilon=0.)
