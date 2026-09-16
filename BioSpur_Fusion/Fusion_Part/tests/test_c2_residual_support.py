import numpy as np
import pytest
import torch
from test_c2_shared_fit import fixture,geometry
from biospur_fusion.c2_five_calibration.solver import PoseObjective
from biospur_fusion.c2_five_calibration.soft_observation import SoftObservationObjective
from biospur_fusion.c2_five_calibration.residual_blocks import ResidualBlocks,record_mean
from biospur_fusion.c2_five_calibration.residual_support import pose_row_support


def test_overlap_preserves_global_energy_gradient_and_once_only_ownership():
    torch.set_num_threads(1)
    actions,*_=fixture();q=next(iter(actions.values()))
    q={k:np.concatenate([v]*3) for k,v in q.items()};q['time_s']=np.arange(90)/20
    q['valid'][65]=False
    obj=SoftObservationObjective(PoseObjective(**q,geometry=geometry()))
    p=obj.initial.clone();p[:,0]=torch.linspace(.01,.1,len(p));p[:,9]=.04;p.requires_grad_()
    lever=torch.full((5,3),.01,dtype=torch.float64,requires_grad=True)
    full=ResidualBlocks();obj.evaluate(p,lever,residual_blocks=full)
    expected=sum(v.square().sum() for v in full.values());actual=p.sum()*0.
    counts={k:0 for k in full}
    for lo,hi in [(0,45),(45,90)]:
        read=max(0,lo-10);subset={k:v[read:hi] for k,v in q.items()}
        from biospur_fusion.c2_five_calibration.pose_window import frozen_pose_window
        small=frozen_pose_window(obj,read,hi)
        blocks=ResidualBlocks(global_mean_counts=full.mean_counts)
        small.evaluate(p[read:hi],lever,residual_blocks=blocks)
        support=pose_row_support(blocks,subset['valid'],frame_offset=read)
        for name,v in blocks.items():
            mask=support[name].owned_by(lo,hi)
            actual=actual+v.reshape(-1)[mask].square().sum();counts[name]+=int(mask.sum())
    assert counts=={k:v.numel() for k,v in full.items()}
    torch.testing.assert_close(actual,expected,atol=1e-12,rtol=1e-12)
    got=torch.autograd.grad(actual,(p,lever),retain_graph=True)
    want=torch.autograd.grad(expected,(p,lever))
    for a,b in zip(got,want):torch.testing.assert_close(a,b,atol=1e-10,rtol=1e-10)


def test_structural_window_does_not_disappear_at_zero_derivative():
    valid=np.ones(30,bool)
    blocks={'acceleration':torch.zeros(20,2,4,3), 'orientation':torch.zeros(30,4,3)}
    support=pose_row_support(blocks,valid)
    assert np.all(support['acceleration'].stop-support['acceleration'].start==11)
    assert support['acceleration'].touches_history(10).sum()==10*2*4*3
    assert support['orientation'].owned_by(10,20).sum()==10*4*3
    with pytest.raises(ValueError,match='undeclared'):
        pose_row_support({'unknown':torch.zeros(30)},valid)


def test_chunk_cannot_silently_get_local_normalization():
    with pytest.raises(ValueError,match='missing whole-tape'):
        record_mean(ResidualBlocks(global_mean_counts={}), 'pose',torch.zeros(10))
    with pytest.raises(ValueError,match='cover'):
        record_mean(ResidualBlocks(global_mean_counts={'pose':3}), 'pose',torch.zeros(10))
