"""Synthetic prior-conditioned plane ambiguity; no pose-accuracy acceptance.

Parents are constructed independently with SciPy rotations and declared axes.
The two static objectives differ only in their missing-parent prior: this is
not a claim of two zero-loss solutions under one fixed learned prior.
"""
import json

import numpy as np
import pytest
import torch
from scipy.spatial.transform import Rotation

from biospur_fusion.c2_five_calibration.anatomy import PROXIMAL
from biospur_fusion.c2_five_calibration.geometry import OBSERVED
from biospur_fusion.c2_five_calibration.solver import PoseObjective, bend_cosines
from test_c2_joint_kinematics import geometry


FRAMES = 80
# Established synthetic rest offsets are collinear. Declare their directions
# and positive hinge axes independently, without reading JointModel axes.
DISTAL = np.array([[1., 0., 0.], [-1., 0., 0.], [0., -1., 0.], [0., -1., 0.]])
HINGE = np.array([[0., -1., 0.], [0., 1., 0.], [1., 0., 0.], [1., 0., 0.]])
BEND = np.array([np.pi / 2, np.pi / 2, .6, .7])


def fixture(psi, global_rotvec):
    """Independent static sensor frames and feasible missing-parent prior."""
    common = Rotation.from_rotvec(global_rotvec).as_matrix()
    observed = np.broadcast_to(common, (FRAMES, 5, 3, 3)).copy()
    prior = np.broadcast_to(common, (FRAMES, 24, 3, 3)).copy()
    psi = np.broadcast_to(np.asarray(psi), (FRAMES,))
    for limb, parent in enumerate(PROXIMAL):
        flex = Rotation.from_rotvec(-BEND[limb] * HINGE[limb]).as_matrix()
        twist = Rotation.from_rotvec(psi[:, None] * DISTAL[limb]).as_matrix() if limb < 2 else np.eye(3)
        prior[:, parent] = common @ twist @ flex
    objective = PoseObjective(prior, observed, np.zeros((FRAMES, 5, 3)),
                              np.ones(FRAMES, dtype=bool), np.arange(FRAMES) / 20,
                              geometry())
    return objective, prior


@pytest.mark.parametrize('global_rotvec', ([0., 0., 0.], [.31, -.47, .22]))
def test_opposite_parent_planes_are_feasible_at_separate_static_prior_seeds(global_rotvec):
    first, prior_a = fixture(.31, global_rotvec)
    second, prior_b = fixture(.31 + np.pi, global_rotvec)
    assert torch.equal(first.observed, second.observed)
    assert torch.equal(first.acceleration, second.acceleration)
    levers = torch.zeros((5, 3), dtype=torch.float64)
    poses, losses = [], []
    for objective, prior in ((first, prior_a), (second, prior_b)):
        pose, terms = objective.evaluate(objective.initial, levers)
        poses.append(pose)
        losses.append(float(terms['loss']))
        assert torch.equal(pose[:, OBSERVED], objective.observed)
        np.testing.assert_allclose(pose[:, PROXIMAL], prior[:, PROXIMAL], atol=2e-14, rtol=0)
        np.testing.assert_allclose(torch.acos(bend_cosines(pose, geometry())),
                                   np.broadcast_to(BEND, (FRAMES, 4)), atol=2e-14, rtol=0)
        assert torch.all(objective.initial[:, 3:7] > 0)
        assert torch.all(objective.initial[:, 3:7] < objective.model.maximum_bend)
        assert objective.seed_audit['unsupported_arm_plane_frames'] == [0, 0]
        # Declared numerical gate: only a constant static fixture can make
        # both the acceleration and self-prior tracking energy vanish.
        assert float(terms['loss']) < 1e-20
    directions = [(p[:, PROXIMAL[:2]] @ torch.tensor(DISTAL[:2])[..., None]).squeeze(-1)
                  for p in poses]
    np.testing.assert_allclose((directions[0] * directions[1]).sum(-1), -1., atol=2e-14, rtol=0)
    # Positive branch orientation itself, independently checked from axes.
    for pose in poses:
        u = (pose[:, PROXIMAL[:2]] @ torch.tensor(DISTAL[:2])[..., None]).squeeze(-1)
        v = (first.observed[:, 1:3] @ torch.tensor(DISTAL[:2])[..., None]).squeeze(-1)
        h = (pose[:, PROXIMAL[:2]] @ torch.tensor(HINGE[:2])[..., None]).squeeze(-1)
        np.testing.assert_allclose((torch.linalg.cross(u, v) * h).sum(-1), 1., atol=2e-14, rtol=0)
    # A fixed prior does discriminate: avoid claiming data-only ambiguity is
    # the same as equal minima of one complete, fixed learned-prior objective.
    opposite_parameters = first.parameters_from_rotation(poses[1])
    _, cross_terms = first.evaluate(opposite_parameters, levers)
    assert float(cross_terms['loss']) > 1e-3
    print(json.dumps(dict(case='static', global_rotvec=global_rotvec,
                          separate_seed_loss=losses,
                          opposite_plane_under_first_prior_loss=float(cross_terms['loss']),
                          arm_direction_separation_deg=180.)))


def test_correction_smoothness_is_zero_for_varying_seed_but_dynamic_total_is_not():
    time_s = np.arange(FRAMES) / 20
    objective, _ = fixture(.31 + .6 * np.sin(2 * np.pi * .7 * time_s), [.31, -.47, .22])
    parameters = objective.initial.clone()
    pose, terms = objective.evaluate(parameters, torch.zeros((5, 3), dtype=torch.float64))
    change = parameters - objective.initial
    # This is the exact existing correction-smoothness definition. It does
    # not penalize the time variation already present in the learned seed.
    smooth = ((change[1:] - change[:-1]) / .12).square().mean()
    assert float(smooth) == 0.
    actual_step = (parameters[1:, 7:9] - parameters[:-1, 7:9]).abs().max()
    assert float(actual_step) > .1
    assert float((pose[1:, PROXIMAL[:2]] - pose[:-1, PROXIMAL[:2]]).abs().max()) > .05
    assert torch.equal(pose[:, OBSERVED], objective.observed)
    # Zero acceleration is intentionally inconsistent with this moving
    # parent trajectory; the full physical objective must remain active.
    assert float(terms['acceleration_loss']) > 1e-4
    assert float(terms['loss']) > 1e-4
    torch.testing.assert_close(terms['loss'], terms['acceleration_loss'], atol=1e-12, rtol=1e-12)
    print(json.dumps(dict(case='varying_seed', correction_smooth_loss=float(smooth),
                          maximum_seed_twist_step_rad=float(actual_step),
                          acceleration_loss=float(terms['acceleration_loss']),
                          total_loss=float(terms['loss']))))
