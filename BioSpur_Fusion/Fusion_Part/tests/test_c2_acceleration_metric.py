import numpy as np
import torch
from biospur_fusion.c2_five_calibration.acceleration_metric import whiten_relative,metric_residual,MODEL


def test_eliminated_translation_likelihood_is_independent_of_reference():
    torch.manual_seed(81)
    errors=torch.randn(30,5,3,dtype=torch.float64,requires_grad=True)
    expected=(errors-errors.mean(1,keepdim=True)).square().sum()
    for anchor in range(5):
        others=[i for i in range(5) if i!=anchor]
        differences=errors[:,others]-errors[:,anchor:anchor+1]
        actual=whiten_relative(differences,node_axis=1).square().sum()
        torch.testing.assert_close(actual,expected)
    actual.backward();assert torch.isfinite(errors.grad).all()


def test_common_reference_error_not_counted_as_four_independent_measurements():
    shared=torch.ones(2,4,3,dtype=torch.float64)
    torch.testing.assert_close(whiten_relative(shared,node_axis=1).square().sum(),shared.square().sum()/5)
    contrasts=torch.randn(2,4,3,dtype=torch.float64);contrasts-=contrasts.mean(1,keepdim=True)
    torch.testing.assert_close(whiten_relative(contrasts,node_axis=1),contrasts)
    torch.testing.assert_close(metric_residual(shared,{},node_axis=1),shared)


def test_linear_lever_system_uses_same_metric_as_pose_objective():
    from test_c2_joint_kinematics import geometry
    from biospur_fusion.c2_five_calibration.solver import lever_system,acceleration_residual,valid_support
    from scipy.spatial.transform import Rotation
    t=np.arange(40)/20
    r=np.repeat(Rotation.from_euler('y',(.4*np.sin(t))[:,None]).as_matrix()[:,None],24,axis=1)
    a=np.random.default_rng(7).normal(size=(40,5,3));valid=np.ones(40,bool)
    g=geometry();g['acceleration_observation_model']=MODEL
    levers=torch.randn(5,3,dtype=torch.float64)*.02
    design,target=lever_system(r,a,valid,g)
    residual=acceleration_residual(torch.tensor(r),torch.tensor(a),g,levers)[valid_support(valid)]
    expected=metric_residual(residual,g,node_axis=2).numpy().reshape(-1)
    np.testing.assert_allclose(design@levers.numpy().reshape(-1)-target,expected,atol=1e-12)
