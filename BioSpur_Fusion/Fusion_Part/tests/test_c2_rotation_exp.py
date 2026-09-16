"""Performance-only exponential substitution: synthetic value/gradient parity."""
import numpy as np
import pytest
import torch
from scipy.spatial.transform import Rotation

from biospur_fusion.c2_five_calibration import anatomy
from biospur_fusion.c2_five_calibration.geometry import OBSERVED
from biospur_fusion.c2_five_calibration.shared_orientation import transport_heading
from biospur_fusion.c2_five_calibration.solver import PoseObjective, solve_pose
from test_c2_joint_kinematics import geometry


# Preserve the previous exact primitive as the independent numerical baseline.
def matrix_exp_baseline(delta):
    x,y,z=delta.unbind(-1); zero=torch.zeros_like(x)
    return torch.matrix_exp(torch.stack((zero,-z,y,z,zero,-x,-y,x,zero),-1).reshape(*delta.shape[:-1],3,3))


def full_first_jacobian(function,x):
    x=x.clone().requires_grad_();r=function(x)
    j=torch.stack([torch.autograd.grad(r[...,i,j].sum(),x,retain_graph=True)[0]
                   for i in range(3) for j in range(3)],-2)
    return r.detach(),j.detach()


@pytest.mark.parametrize('angle',[0.,1e-9,torch.pi,2*torch.pi,.371,4.123])
def test_float64_exp_and_first_jacobian_parity(angle):
    generator=torch.Generator().manual_seed(1741)
    axes=torch.cat((torch.eye(3,dtype=torch.float64),torch.randn(12,3,dtype=torch.float64,generator=generator)))
    x=angle*axes/axes.norm(dim=-1,keepdim=True)
    a,ja=full_first_jacobian(matrix_exp_baseline,x)
    b,jb=full_first_jacobian(anatomy.exp_rotation,x)
    for original,fast in ((a,b),(ja,jb)):
        assert torch.isfinite(fast).all()
        torch.testing.assert_close(fast,original,atol=1e-11,rtol=0)
    torch.testing.assert_close(b@b.transpose(-1,-2),torch.eye(3).double().expand_as(b),atol=1e-12,rtol=0)
    if angle==0.:
        expected=torch.tensor([[[0,0,0],[0,0,-1],[0,1,0]],[[0,0,1],[0,0,0],[-1,0,0]],
                               [[0,-1,0],[1,0,0],[0,0,0]]],dtype=torch.float64).reshape(3,9).T
        torch.testing.assert_close(jb,expected.expand_as(jb),atol=0,rtol=0)


def synthetic_inputs():
    g=geometry()
    # Existing geometry fixture, with the same noncollinear-rest cases as its tests.
    for joint,value in {4:[.07,-.48,.04],5:[-.07,-.48,.03],7:[-.01,-.43,-.01],8:[.01,-.43,-.01],
                        18:[.3175,.03,.02],19:[-.3175,.03,.02]}.items():g['rest_offsets_m'][joint]=value
    t=np.arange(80)/20
    frequencies=.13*(1+np.arange(24))
    vector=np.stack([.25*np.sin(t[:,None]*frequencies+p) for p in (.1,.7,1.3)],axis=-1)
    prior=Rotation.from_rotvec(vector.reshape(-1,3)).as_matrix().reshape(-1,24,3,3)
    observed=prior[:,OBSERVED].copy()
    acc=np.stack([np.sin(t[:,None]*(1+np.arange(5))*.3+p) for p in (.2,.5,.8)],axis=-1)*.15
    valid=np.ones(len(t),bool);valid[31]=False
    return dict(prior=prior,observed=observed,acceleration=acc,valid=valid,time_s=t,geometry=g)


def test_joint_model_rotation_and_parameter_gradient_parity(monkeypatch):
    args=synthetic_inputs();fast=anatomy.exp_rotation
    results=[]
    for primitive in (matrix_exp_baseline,fast):
        monkeypatch.setattr(anatomy,'exp_rotation',primitive)
        model=anatomy.JointModel(args['geometry'])
        prior=torch.tensor(args['prior']);observed=torch.tensor(args['observed'])
        p=torch.zeros(len(prior),9,dtype=torch.float64)
        p[:,3:7]=torch.linspace(.1,1.8,len(prior),dtype=torch.float64)[:,None]
        p[:,7:9]=torch.linspace(-2*torch.pi,2*torch.pi,len(prior),dtype=torch.float64)[:,None]
        p[:,0]=torch.linspace(0.,torch.pi,len(prior),dtype=torch.float64)
        p.requires_grad_();r=model.rotation(prior,observed,p)
        weights=torch.linspace(-.5,.8,r.numel(),dtype=r.dtype).reshape_as(r)
        gradient=torch.autograd.grad((r*weights).sum(),p)[0]
        assert torch.equal(r[:,OBSERVED],observed)
        results.append((r.detach(),gradient))
    for baseline,candidate in zip(*results):
        torch.testing.assert_close(candidate,baseline,atol=1e-11,rtol=0)
        assert torch.isfinite(candidate).all()


def test_full_objective_and_pose_lever_heading_gradients(monkeypatch):
    args=synthetic_inputs();fast=anatomy.exp_rotation;results=[]
    for primitive in (matrix_exp_baseline,fast):
        monkeypatch.setattr(anatomy,'exp_rotation',primitive)
        objective=PoseObjective(**args)
        phase=torch.arange(len(objective.prior),dtype=torch.float64)[:,None]/19
        p=(objective.initial+.03*torch.sin(phase+torch.arange(9))).requires_grad_()
        lever=torch.linspace(-.025,.025,15,dtype=torch.float64).reshape(5,3).requires_grad_()
        heading=torch.tensor([.02,-.03,.01,-.015],dtype=torch.float64,requires_grad=True)
        observed,acceleration=transport_heading(objective.observed,objective.acceleration,heading)
        rotation,terms=objective.evaluate(p,lever,observed=observed,acceleration=acceleration)
        gradients=torch.autograd.grad(terms['loss'],(p,lever,heading))
        assert all(torch.isfinite(v).all() and v.abs().max()>1e-8 for v in gradients)
        results.append(dict(rotation=rotation.detach(),terms={k:v.detach() for k,v in terms.items()},
                            gradients=[v.detach() for v in gradients]))
    a,b=results
    torch.testing.assert_close(b['rotation'],a['rotation'],atol=1e-11,rtol=0)
    for key in a['terms']:torch.testing.assert_close(b['terms'][key],a['terms'][key],atol=1e-9,rtol=1e-10)
    for old,new in zip(a['gradients'],b['gradients']):torch.testing.assert_close(new,old,atol=1e-9,rtol=1e-10)


def test_short_solver_result_and_objective_parity(monkeypatch):
    args=synthetic_inputs();fast=anatomy.exp_rotation;results=[]
    for primitive in (matrix_exp_baseline,fast):
        monkeypatch.setattr(anatomy,'exp_rotation',primitive)
        rotation,audit=solve_pose(**args,levers=np.zeros((5,3)),iterations=4,wall_limit_s=15.)
        assert audit['optimizer_steps']==6
        results.append((rotation,audit['history'][-1]['loss']))
    np.testing.assert_allclose(results[1][0],results[0][0],atol=1e-9,rtol=1e-10)
    np.testing.assert_allclose(results[1][1],results[0][1],atol=1e-9,rtol=1e-10)
