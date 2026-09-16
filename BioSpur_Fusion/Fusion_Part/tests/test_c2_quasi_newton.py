import torch
import pytest
from test_c2_pelvis_observation import fixture
from biospur_fusion.c2_five_calibration.quasi_newton import optimize_quasi_newton


def test_real_pose_objective_keeps_zero_budget_and_refines_with_box_bounds():
    _,base,_,lever=fixture();p=base.initial.clone();p[:,3:7]=.5;p[1::2,9:]=.01
    selected,history,audit=optimize_quasi_newton(base,lever,p,iterations=0,wall_limit_s=10)
    torch.testing.assert_close(selected,p,atol=0,rtol=0)
    refined,history,audit=optimize_quasi_newton(base,lever,p,iterations=3,wall_limit_s=10,max_evaluations=8)
    assert audit['evaluations']<=8 and not audit['acceptance_claimed']
    assert torch.all(refined[:,3:7]>=0) and torch.all(refined[:,3:7]<=base.model.maximum_bend)
    assert audit['selected_energy']<=history[0]['loss']


def test_invalid_initial_bend_is_not_silently_clipped():
    _,base,_,lever=fixture();p=base.initial.clone();p[:,3]=-1
    with pytest.raises(ValueError,match='bounds'):optimize_quasi_newton(base,lever,p,iterations=1,wall_limit_s=10)
