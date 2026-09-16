"""Known identifiable and confounded cases for the diagnostic, not pose tests."""
import numpy as np
import torch

from probe_c2_five_heading_information import project_heading, position_operator
from biospur_fusion.c2_five_calibration.operators import multiscale


def test_projection_distinguishes_heading_from_nuisance():
    nuisance = np.array([[1., 0.], [0., 1.], [0., 0.], [0., 0.]])
    headings = np.array([[1., 0.], [0., 0.], [0., 2.], [0., 0.]])
    report, solution, residual = project_heading(nuisance, headings)
    np.testing.assert_allclose(report['surviving_fraction'], [0., 1.], atol=1e-12)
    np.testing.assert_allclose(nuisance@solution+headings, residual, atol=1e-12)


def test_sparse_derivative_has_production_scale_and_gap_order():
    rng = np.random.default_rng(4)
    positions = rng.normal(size=(61, 4, 3))
    good = np.ones(51, dtype=bool)
    good[13:17] = False
    sparse = position_operator(61, good)@positions.ravel()
    production = multiscale(torch.from_numpy(positions))[good].transpose(0, 1).reshape(-1).numpy()
    np.testing.assert_allclose(sparse, production, atol=1e-10)


def test_zero_injected_heading_does_not_manufacture_pose_change():
    from biospur_fusion.c2_five_calibration.anatomy import JointModel
    from probe_c2_five_heading_compensation import compensate
    from test_c2_joint_kinematics import geometry
    g = geometry()
    prior = torch.eye(3, dtype=torch.float64).repeat(31, 24, 1, 1)
    observed = prior[:, [0, 18, 19, 4, 5]].clone()
    parameters = torch.zeros(31, 9, dtype=torch.float64)
    parameters[:, 3:7] = .4
    parameters[:, 7:9] = .2
    model = JointModel(g)
    rotation = model.rotation(prior, observed, parameters).numpy()
    q = dict(prior=prior.numpy(), observed=observed.numpy(),
             acceleration=np.zeros((31, 5, 3)), valid=np.ones(31, bool),
             time_s=np.arange(31)/20)
    result = compensate(q, rotation, g, np.zeros((5, 3)), 0.)
    assert result['final_residual_change_rms_mps2'] < 1e-10
    assert result['max_root_relative_sensor_position_change_m'] < 1e-10
    assert result['existing_flexion_limits_satisfied']
