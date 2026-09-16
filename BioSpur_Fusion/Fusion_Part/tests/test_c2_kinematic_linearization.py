import numpy as np
import torch
from test_c2_shared_fit import fixture,geometry
from biospur_fusion.c2_five_calibration.solver import PoseObjective
from biospur_fusion.c2_five_calibration.soft_observation import SoftObservationObjective
from biospur_fusion.c2_five_calibration.residual_blocks import ResidualBlocks
from biospur_fusion.c2_five_calibration.kinematic_linearization import stencil_matrix,temporal_pose_jacobians
from biospur_fusion.c2_five_calibration.operators import multiscale,filtered


def test_stencil_assembly_preserves_gaps_centres_scale_and_component_order():
    x=torch.randn(32,4,3,dtype=torch.float64);good=np.ones(22,bool);good[7:10]=False
    a=stencil_matrix(32,12,good,(11,5),2)@x.flatten().numpy()
    np.testing.assert_allclose(a,multiscale(x)[good].flatten(),atol=1e-12)
    b=stencil_matrix(32,12,good,(11,),1)@x.flatten().numpy()
    np.testing.assert_allclose(b,filtered(x,1)[good].flatten(),atol=1e-12)


def test_chain_rule_matches_direct_pose_derivatives_and_global_normalization():
    torch.set_num_threads(1);actions,*_=fixture();q=next(iter(actions.values()))
    obj=SoftObservationObjective(PoseObjective(**q,geometry=geometry()))
    p=obj.initial.clone();p[:,0]=torch.linspace(.01,.04,len(p));p[:,9]=.03
    lever=torch.full((5,3),.01,dtype=p.dtype)
    blocks=ResidualBlocks();obj.evaluate(p,lever,residual_blocks=blocks)
    global_counts={k:v*2 for k,v in blocks.mean_counts.items()}
    assembled,groups=temporal_pose_jacobians(obj,p,lever,mean_counts=global_counts)
    assert groups==81
    for name,J in assembled.items():
        def f(x):
            b=ResidualBlocks(global_mean_counts=global_counts);obj.evaluate(x,lever,residual_blocks=b);return b[name].flatten()
        direct=torch.autograd.functional.jacobian(f,p,vectorize=True).reshape(J.shape)
        np.testing.assert_allclose(J.toarray(),direct,atol=1e-10,rtol=1e-10)


def test_complete_export_has_all_owner_rows_and_correct_directional_derivative():
    from biospur_fusion.c2_five_calibration.kinematic_linearization import pose_residual_jacobians
    torch.set_num_threads(1);actions,*_=fixture();q=next(iter(actions.values()))
    obj=SoftObservationObjective(PoseObjective(**q,geometry=geometry()));p=obj.initial.clone()
    p[:,0]=torch.linspace(.01,.05,len(p));lever=torch.full((5,3),.01,dtype=p.dtype)
    matrices,residuals=pose_residual_jacobians(obj,p,lever)
    direction=torch.tensor(np.random.default_rng(7).normal(size=p.shape),dtype=p.dtype);direction/=direction.norm();eps=1e-6
    b={};obj.evaluate(p+eps*direction,lever,residual_blocks=b)
    c={};obj.evaluate(p-eps*direction,lever,residual_blocks=c)
    assert matrices.keys()==residuals.keys()==b.keys()
    for name,J in matrices.items():
        fd=((b[name]-c[name])/(2*eps)).detach().numpy().flatten()
        np.testing.assert_allclose(J@direction.numpy().flatten(),fd,atol=1e-7,rtol=1e-5)
