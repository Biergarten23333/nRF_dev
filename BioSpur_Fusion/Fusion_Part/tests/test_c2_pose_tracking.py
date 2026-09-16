import numpy as np
import pytest
import torch
from scipy.spatial.transform import Rotation

from test_c2_joint_kinematics import geometry
from biospur_fusion.c2_five_calibration.anatomy import JointModel, TORSO, PROXIMAL
from biospur_fusion.c2_five_calibration.geometry import OBSERVED, joints_from_global
from biospur_fusion.c2_five_calibration.solver import filtered, solve_pose
from biospur_fusion.c2_five_calibration.tracking import PoseTrackingPrior


def test_static_acceleration_ambiguity_is_visible_to_position_tracking():
    g = geometry()
    model = JointModel(g)
    base = torch.eye(3, dtype=torch.float64).repeat(30, 24, 1, 1)
    observed = base[:, OBSERVED].clone()
    parameters = torch.zeros(30, 9, dtype=torch.float64)
    parameters[:, 3:7] = .3
    prior = model.rotation(base, observed, parameters)
    parameters[:, 3] = 1.5
    alternative = model.rotation(base, observed, parameters)
    # Two stationary poses can have identical retained orientations and zero
    # acceleration while placing the elbow at different locations.
    torch.testing.assert_close(alternative[:, OBSERVED], prior[:, OBSERVED])
    for pose in (prior, alternative):
        assert filtered(joints_from_global(pose, g), 2).abs().max() < 1e-10
    tracking = PoseTrackingPrior(prior, g)
    original = tracking.losses(prior, g, lambda p: filtered(p, 1))
    changed = tracking.losses(alternative, g, lambda p: filtered(p, 1))
    assert original[0] == 0 and original[1] == 0
    assert changed[0] > 0 and changed[2] > .01


def test_tracking_has_finite_gradients():
    g = geometry()
    base = torch.eye(3, dtype=torch.float64).repeat(30, 24, 1, 1)
    observed = base[:, OBSERVED].clone()
    parameters = torch.full((30, 9), .1, dtype=torch.float64, requires_grad=True)
    pose = JointModel(g).rotation(base, observed, parameters)
    a, b, _ = PoseTrackingPrior(base, g).losses(pose, g, lambda p: filtered(p, 1))
    (a+b).backward()
    assert torch.isfinite(parameters.grad).all() and parameters.grad.abs().sum() > 0


@pytest.mark.parametrize('discarded_joint', OBSERVED + [3, 6, 13, 14])
def test_discarded_sensor_predictions_cannot_move_a_correct_proximal_pose(discarded_joint):
    g = geometry()
    n = 41
    t = np.arange(n) / 20
    base = torch.eye(3, dtype=torch.float64).repeat(n, 24, 1, 1)
    observed = base[:, OBSERVED].clone()
    parameters = torch.zeros(n, 9, dtype=torch.float64)
    parameters[:, 3:7] = .6
    truth = JointModel(g).rotation(base, observed, parameters)
    dirty = truth.numpy().copy()
    dirty[:, discarded_joint] = Rotation.from_rotvec(
        np.column_stack((.3*np.sin(t), .5*np.ones(n), .2*np.cos(t)))).as_matrix()
    original = dirty.copy()
    solved, report = solve_pose(dirty, observed.numpy(), np.zeros((n, 5, 3)),
        np.ones(n, bool), t, g, np.zeros((5, 3)), iterations=20)
    # All free-segment predictions and observations are already correct.
    # A discarded learned IMU rotation must not change their solved pose,
    # including through the position or velocity regularization target.
    assert report['history'][0]['loss'] < 1e-15
    np.testing.assert_allclose(solved, truth.numpy(), atol=1e-10)
    np.testing.assert_array_equal(dirty, original)


def test_conditioned_target_preserves_free_predictions_and_loss_gradients():
    g=geometry(); model=JointModel(g)
    n=41; phase=np.linspace(0,2*np.pi,n)
    base=torch.eye(3,dtype=torch.float64).repeat(n,24,1,1)
    observed=base[:,OBSERVED].clone()
    parameters=torch.full((n,9),.2,dtype=torch.float64,requires_grad=True)
    candidate=model.rotation(base,observed,parameters)
    perturbation=torch.tensor(Rotation.from_rotvec(np.column_stack(
        (.12*np.sin(phase),.35+.15*np.sin(phase),.08*np.cos(phase)))).as_matrix())
    def objective(prior):
        target=model.prior_target(prior,observed)
        p,v,_=PoseTrackingPrior(target,g).losses(candidate,g,lambda x:filtered(x,1))
        angular=((candidate[:,PROXIMAL+TORSO]-target[:,PROXIMAL+TORSO])/.5).square().mean()
        return torch.stack((p,v,angular))
    expected=objective(base)
    gradient=torch.autograd.grad(expected.sum(),parameters,retain_graph=True)[0]
    for joint in OBSERVED+[3,6,13,14]:
        dirty=base.clone();dirty[:,joint]=perturbation
        values=objective(dirty)
        torch.testing.assert_close(values,expected,atol=1e-14,rtol=0)
        actual=torch.autograd.grad(values.sum(),parameters,retain_graph=True)[0]
        torch.testing.assert_close(actual,gradient,atol=1e-14,rtol=0)
    for joint in [9]+PROXIMAL:
        changed=base.clone();changed[:,joint]=perturbation
        target=model.prior_target(changed,observed)
        assert (joints_from_global(target,g)-joints_from_global(base,g)).abs().max()>.01
