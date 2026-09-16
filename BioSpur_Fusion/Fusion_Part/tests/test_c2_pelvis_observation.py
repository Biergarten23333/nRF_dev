import numpy as np
import torch
import pytest
from scipy.spatial.transform import Rotation
from biospur_fusion.c2_five_calibration.solver import PoseObjective
from biospur_fusion.c2_five_calibration.soft_observation import SoftObservationObjective
from biospur_fusion.c2_five_calibration.pelvis_observation import PelvisObservationObjective
from biospur_fusion.c2_five_calibration.geometry import joints_from_global
from test_c2_joint_kinematics import geometry


def fixture():
    g=geometry();r=np.tile(np.eye(3),(40,24,1,1));obs=r[:,[0,18,19,4,5]].copy()
    base=SoftObservationObjective(PoseObjective(r,obs,np.zeros((40,5,3)),np.ones(40,bool),np.arange(40)/20,g))
    return g,base,PelvisObservationObjective(base),torch.zeros(5,3,dtype=torch.float64)


def test_zero_root_residual_exactly_preserves_existing_energy_and_pose():
    g,base,new,lever=fixture()
    r,terms=base.evaluate(base.initial,lever,refresh_projection=True)
    rr,tt=new.evaluate(new.initial,lever,refresh_projection=True)
    torch.testing.assert_close(rr,r,atol=0,rtol=0)
    torch.testing.assert_close(tt['loss'],terms['loss'],atol=0,rtol=0)


def test_root_pose_moves_hips_without_rotating_measured_limbs_or_acceleration():
    g,base,new,lever=fixture();p=new.initial.clone();p[:,21:]=torch.tensor([.1,-.05,.03],dtype=torch.float64)
    measured=new.observed.clone();acc=new.acceleration.clone()
    r,terms=new.evaluate(p,lever)
    expected=torch.tensor(Rotation.from_rotvec([.1,-.05,.03]).as_matrix())
    torch.testing.assert_close(r[:,0],expected.expand(40,3,3),atol=1e-12,rtol=0)
    torch.testing.assert_close(r[:,[18,19,4,5]],measured[:,1:],atol=0,rtol=0)
    torch.testing.assert_close(new.observed,measured,atol=0,rtol=0)
    torch.testing.assert_close(new.acceleration,acc,atol=0,rtol=0)
    j=joints_from_global(r,g)
    torch.testing.assert_close(j[:,1],(expected@torch.tensor(g['rest_offsets_m'][1],dtype=torch.float64)).expand(40,3),atol=1e-12,rtol=0)
    recovered=new.parameters_from_rotation(r)
    rr,_=new.evaluate(recovered,lever)
    torch.testing.assert_close(rr,r,atol=1e-10,rtol=0)
    q=p.clone().requires_grad_();new.evaluate(q,lever)[1]['loss'].backward()
    assert torch.isfinite(q.grad).all() and q.grad[:,21:].abs().sum()>0


def test_root_reflection_rejected():
    _,_,new,_=fixture();r=torch.eye(3,dtype=torch.float64).repeat(40,24,1,1);r[:,0,0,0]=-1
    with pytest.raises(ValueError,match='proper'):new.parameters_from_rotation(r)


def test_exported_root_factors_preserve_full_energy():
    from biospur_fusion.c2_five_calibration.residual_blocks import ResidualBlocks
    _,_,new,lever=fixture();p=new.initial.clone();p[:,21]=torch.linspace(0,.1,len(p),dtype=p.dtype)
    blocks=ResidualBlocks();_,terms=new.evaluate(p,lever,residual_blocks=blocks)
    energy=sum(v.square().sum() for v in blocks.values())
    torch.testing.assert_close(energy,terms['loss'],atol=1e-12,rtol=1e-12)
    assert 'pelvis_orientation' in blocks and 'pelvis_orientation_smoothness' in blocks


@pytest.mark.parametrize('release_root', [False, True])
def test_solver_entry_preserves_warm_root_and_reports_actual_model(monkeypatch, release_root):
    from biospur_fusion.c2_five_calibration import soft_observation as module
    g,base,new,lever=fixture()
    objective=new if release_root else base
    p=objective.initial.clone()
    if release_root:p[:,21]=.08
    rotation,_=objective.evaluate(p,lever)
    def identity_optimizer(owner, levers, guess, **kwargs):
        assert guess.shape[1]==(24 if release_root else 21)
        return guess,[],{}
    monkeypatch.setattr(module,'optimize_pose',identity_optimizer)
    data=dict(prior=base.base.prior.numpy(),observed=base.observed.numpy(),
              acceleration=base.acceleration.numpy(),valid=np.ones(40,bool),time_s=np.arange(40)/20)
    result,audit=module.solve_soft_observations(data,g,lever,initial_rotation=rotation.numpy(),
                                               pelvis_observation=release_root)
    np.testing.assert_allclose(result,rotation.numpy(),atol=1e-10)
    assert audit['pelvis_joint_equals_tag_orientation']==(not release_root)
    assert ('pelvis gauge fixed' in audit['model'])==(not release_root)
    assert audit['pelvis_observation_residual_mean_deg']==pytest.approx(np.rad2deg(.08) if release_root else 0.)
