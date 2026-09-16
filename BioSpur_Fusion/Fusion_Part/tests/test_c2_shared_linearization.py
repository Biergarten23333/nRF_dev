import numpy as np
import torch
from test_c2_shared_fit import fixture,geometry
from biospur_fusion.c2_five_calibration.solver import PoseObjective
from biospur_fusion.c2_five_calibration.soft_observation import SoftObservationObjective
from biospur_fusion.c2_five_calibration.shared_linearization import shared_pose_jacobians
from biospur_fusion.c2_five_calibration.shared_orientation import transport_heading
from biospur_fusion.c2_five_calibration.residual_blocks import ResidualBlocks


def test_all_joint_columns_match_independent_directional_differences():
    torch.set_num_threads(1);actions,*_=fixture();q=next(iter(actions.values()))
    obj=SoftObservationObjective(PoseObjective(**q,geometry=geometry()));p=obj.initial.clone();p[:,9]=.01
    basis=np.zeros((len(p),4,8));t=np.linspace(0,1,len(p))
    for i in range(4):basis[:,i,2*i]=1-t;basis[:,i,2*i+1]=t
    h=torch.linspace(-.02,.03,8,dtype=p.dtype);lever=torch.full((5,3),.01,dtype=p.dtype)
    result=shared_pose_jacobians(obj,p,h,lever,basis,projection_gap=True)
    rng=np.random.default_rng(22);dp=torch.tensor(rng.normal(size=p.shape)*.1);dh=torch.tensor(rng.normal(size=8)*.1);dl=torch.tensor(rng.normal(size=(5,3))*.01)
    def f(p,h,l):
        obs,acc=transport_heading(obj.observed,obj.acceleration,torch.tensor(basis)@h)
        b=ResidualBlocks();obj.evaluate(p,l,observed=obs,acceleration=acc,refresh_projection=True,projection_gap=True,residual_blocks=b)
        return torch.cat([b[k].flatten() for k in sorted(b)]).detach().numpy()
    eps=1e-6;fd=(f(p+eps*dp,h+eps*dh,lever+eps*dl)-f(p-eps*dp,h-eps*dh,lever-eps*dl))/(2*eps)
    pred=result['pose']@dp.numpy().flatten()+result['heading']@dh.numpy()+result['levers']@dl.numpy().flatten()
    np.testing.assert_allclose(pred,fd,atol=1e-7,rtol=1e-5)
