import torch
from biospur_fusion.c2_five_calibration.temporal_step import local_average_direction


def test_affine_motion_preserved_and_invalid_boundaries_not_crossed():
    p = torch.arange(30,dtype=torch.float64).reshape(10,3)
    valid = torch.ones(10,dtype=torch.bool)
    torch.testing.assert_close(local_average_direction(p,valid),torch.zeros_like(p))
    p[4] += 20
    valid[4] = False
    torch.testing.assert_close(local_average_direction(p,valid),torch.zeros_like(p))


def test_alternating_component_removed_without_leaving_coordinate_bounds():
    p = torch.tensor([[0.],[1.]]*8)
    d = local_average_direction(p,torch.ones(16,dtype=torch.bool))
    torch.testing.assert_close((p+d)[1:-1],torch.full((14,1),.5))
    assert (p+d).min() >= p.min() and (p+d).max() <= p.max()


def test_runtime_polish_scores_full_pose_objective_and_default_is_unchanged():
    import numpy as np
    from biospur_fusion.c2_five_calibration.solver import PoseObjective
    from biospur_fusion.c2_five_calibration.soft_observation import SoftObservationObjective
    from biospur_fusion.c2_five_calibration.optimization import optimize_pose
    from biospur_fusion.c2_five_calibration.body_feasibility import selection_key
    from test_c2_joint_kinematics import geometry
    r = np.tile(np.eye(3), (60,24,1,1))
    objective = SoftObservationObjective(PoseObjective(
        r,r[:,[0,18,19,4,5]],np.zeros((60,5,3)),np.ones(60,bool),np.arange(60)/20,geometry()))
    guess = objective.initial.clone()
    guess[:,3:7] = .5
    guess[1::2,9:] = .01
    lever = torch.zeros(5,3,dtype=torch.float64)
    original,_,default_audit = optimize_pose(objective,lever,guess,iterations=0,wall_limit_s=20)
    torch.testing.assert_close(original,guess,atol=0,rtol=0)
    assert default_audit['temporal_search'] is None
    selected,history,audit = optimize_pose(objective,lever,guess,iterations=0,wall_limit_s=20,temporal_polish=True)
    with torch.no_grad():
        _,before = objective.evaluate(guess,lever)
        _,after = objective.evaluate(selected,lever)
    assert selection_key(after['loss'],after['body_violation']) <= selection_key(before['loss'],before['body_violation'])
    assert history[-1]['phase'] == 'temporal_search'
    assert len(audit['temporal_search']['history']) == 4
