import numpy as np
import pytest
import torch

from biospur_fusion.c2_five_calibration.sensor_bias import ConstantBiasProfile


def test_profile_matches_joint_least_squares_and_pose_gradient():
    rng = np.random.default_rng(81)
    design = rng.normal(size=(80, 15))
    # Include an unobservable bias direction; no artificial precision added.
    design[:, -1] = design[:, 0]
    pose = torch.tensor(rng.normal(size=(80, 3)), dtype=torch.float64)
    offset = torch.tensor(rng.normal(size=80), dtype=torch.float64)
    profile = ConstantBiasProfile(design)
    assert profile.rank == 14
    x = torch.tensor([.1, -.2, .3], dtype=torch.float64, requires_grad=True)
    residual = pose @ x + offset
    remaining, bias = profile.evaluate(residual)
    expected = np.linalg.lstsq(design, -residual.detach().numpy(), rcond=1e-10)[0]
    np.testing.assert_allclose(bias.detach(), expected, atol=1e-12)
    np.testing.assert_allclose(remaining.detach(), residual.detach().numpy() + design @ expected, atol=1e-12)
    assert torch.autograd.gradcheck(lambda q: profile.evaluate(pose @ q + offset)[0], (x,))


def test_zero_information_does_not_remove_pose_evidence():
    profile = ConstantBiasProfile(np.zeros((20, 15)))
    residual = torch.arange(20, dtype=torch.float64)
    remaining, bias = profile.evaluate(residual)
    assert profile.rank == 0
    assert torch.equal(remaining, residual)
    assert torch.equal(bias, torch.zeros(15, dtype=torch.float64))
    with pytest.raises(ValueError):
        profile.evaluate(residual[:-1])
