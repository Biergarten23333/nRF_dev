"""Continuation must retain feasible poses and compare like-for-like scores."""
import numpy as np
import torch

from biospur_fusion.c2_five_calibration.anatomy import FLEXION
from biospur_fusion.c2_five_calibration.geometry import OBSERVED
from biospur_fusion.c2_five_calibration.optimization import optimize_pose
from biospur_fusion.c2_five_calibration.solver import PoseObjective
from test_c2_joint_kinematics import geometry


def test_each_phase_preserves_observations_bounds_and_its_best_iterate():
    previous_threads = torch.get_num_threads()
    torch.set_num_threads(1)
    try:
        n = 45
        prior = np.tile(np.eye(3), (n, 24, 1, 1))
        observed = np.tile(np.eye(3), (n, 5, 1, 1))
        acceleration = np.random.default_rng(925).normal(size=(n, 5, 3))*.1
        objective = PoseObjective(prior, observed, acceleration, np.ones(n, bool),
                                  np.arange(n)/20, geometry())
        levers = torch.zeros(5, 3, dtype=torch.float64)
        parameters, history, audit = optimize_pose(objective, levers,
            objective.initial, iterations=10, wall_limit_s=10)
        rotation, terms = objective.evaluate(parameters, levers)
        assert torch.equal(rotation[:, OBSERVED], objective.observed)
        assert torch.all(parameters[:, FLEXION] >= 0)
        assert torch.all(parameters[:, FLEXION] <= objective.model.maximum_bend)
        assert [row['phase'] for row in history] == ['slow', 'slow', 'combined', 'combined']
        assert history[1]['phase_loss'] <= history[0]['phase_loss']
        assert history[3]['phase_loss'] <= history[2]['phase_loss']
        assert history[-1]['loss'] == float(terms['loss'])
        assert audit['optimizer_steps'] == 15
        assert not audit['stage_scores_compared_across_objectives']
    finally:
        torch.set_num_threads(previous_threads)
