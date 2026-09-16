"""Calibration surrogate must agree with re-projecting the same frozen prior."""
import numpy as np
import torch

from biospur_fusion.c2_five_calibration.shared_orientation import transport_heading
from biospur_fusion.c2_five_calibration.solver import PoseObjective
from test_c2_shared_fit import fixture
from test_c2_joint_kinematics import geometry


def test_candidate_projection_matches_fresh_geometric_objective():
    actions, *_ = fixture()
    q = next(iter(actions.values())); g = geometry()
    original = PoseObjective(**q, geometry=g)
    delta = torch.tensor([.1, -.08, .03, -.04], dtype=torch.float64)
    obs, acc = transport_heading(original.observed, original.acceleration, delta)
    fresh = PoseObjective(**{**q, 'observed':obs.numpy(), 'acceleration':acc.numpy()}, geometry=g)
    p = original.initial+.02
    lever = torch.zeros(5, 3, dtype=torch.float64)
    _, expected = fresh.evaluate(p, lever)
    _, actual = original.evaluate(p, lever, observed=obs, acceleration=acc, refresh_projection=True)
    torch.testing.assert_close(actual['loss'], expected['loss'], atol=1e-12, rtol=0)
    _, stale = original.evaluate(p, lever, observed=obs, acceleration=acc)
    assert abs(float(stale['loss']-expected['loss'])) > 1e-5


def test_projection_heading_gradient_matches_actual_energy_changes():
    actions, *_ = fixture()
    q = next(iter(actions.values()))
    original = PoseObjective(**q, geometry=geometry())
    p = original.initial+.02
    lever = torch.zeros(5, 3, dtype=torch.float64)

    def energy(delta):
        obs, acc = transport_heading(original.observed, original.acceleration, delta)
        return original.evaluate(p, lever, observed=obs, acceleration=acc,
                                 refresh_projection=True)[1]['loss']

    delta = torch.tensor([.1, -.08, .03, -.04], dtype=torch.float64, requires_grad=True)
    gradient = torch.autograd.grad(energy(delta), delta)[0].numpy()
    finite = []
    for j in range(4):
        step = torch.zeros(4, dtype=torch.float64); step[j] = 1e-6
        finite.append(float((energy(delta.detach()+step)-energy(delta.detach()-step))/(2e-6)))
    np.testing.assert_allclose(gradient, finite, atol=1e-7, rtol=1e-5)
