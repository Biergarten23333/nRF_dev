import numpy as np
import pytest
import torch

from biospur_fusion.c2_five_calibration.temporal_torso import PoseVariables, TorsoBasis


def test_constant_and_linear_corrections_preserve_original_time():
    basis = TorsoBasis(201, 1.)
    t = torch.arange(201, dtype=torch.float64)/20
    expected = torch.stack((t*.02, t*0+.3, -.01*t), dim=-1)
    controls = basis.project(expected)
    torch.testing.assert_close(basis.expand(controls), expected, atol=1e-9, rtol=0)
    assert basis.matrix.shape[1] < 20


def test_basis_does_not_freeze_underlying_torso_motion():
    from biospur_fusion.c2_five_calibration.anatomy import JointModel, exp_rotation
    from test_c2_joint_kinematics import geometry
    t = torch.arange(81, dtype=torch.float64)/20
    prior = torch.eye(3, dtype=torch.float64).repeat(81, 24, 1, 1)
    motion = torch.stack((t*0, .5*torch.sin(3*t), t*0), dim=-1)
    prior[:, 9] = exp_rotation(motion)
    values = PoseVariables(torch.zeros(81, 9, dtype=torch.float64), 1.)
    observed = prior[:, [0, 18, 19, 4, 5]]
    output = JointModel(geometry()).rotation(prior, observed, values.value())
    torch.testing.assert_close(output[:, 9], prior[:, 9], atol=0, rtol=0)
    assert float((output[10, 9]-output[30, 9]).detach().abs().max()) > .1


def test_reduced_variables_receive_gradients_and_keep_flexion_limits():
    values = PoseVariables(torch.ones(61, 9, dtype=torch.float64)*.2, 1.)
    (values.value()*torch.arange(61)[:, None]).square().mean().backward()
    assert all(p.grad is not None and torch.isfinite(p.grad).all()
               and p.grad.abs().sum() > 0 for p in values.trainable)
    values.clamp_flexion(torch.tensor([.1, .1, .1, .1]))
    assert values.value()[:, 3:7].max() <= .10000001
    torch.testing.assert_close(values.value()[:, 7:], torch.full((61, 2), .2, dtype=torch.float64))


def test_default_preserves_full_pose_and_invalid_spacing_fails():
    x = torch.randn(61, 9, dtype=torch.float64)
    values = PoseVariables(x)
    torch.testing.assert_close(values.value(), x, atol=0, rtol=0)
    for spacing in (0., -1., np.nan):
        with pytest.raises(ValueError):
            PoseVariables(x, spacing)
