import numpy as np
import pytest
import torch
from test_c2_shared_fit import fixture,geometry
from biospur_fusion.c2_five_calibration.solver import PoseObjective
from biospur_fusion.c2_five_calibration.soft_observation import SoftObservationObjective
from biospur_fusion.c2_five_calibration.runtime_information import RuntimeInformationObjective
from biospur_fusion.c2_five_calibration.residual_blocks import ResidualBlocks
from biospur_fusion.c2_five_calibration.optimization import _snapshot


def test_boundary_gradient_and_once_only_rows():
    torch.set_num_threads(1)
    actions,*_=fixture();q=next(iter(actions.values()))
    soft=SoftObservationObjective(PoseObjective(**q,geometry=geometry()))
    factor=dict(matrix=np.eye(210)*.3,residual=np.ones(210)*.01,
                point=soft.initial[:10].numpy().reshape(-1),constant=2.)
    off=RuntimeInformationObjective(soft,q['time_s'],q['time_s'][:10],factor,use_history=False)
    on=RuntimeInformationObjective(soft,q['time_s'],q['time_s'][:10],factor,use_history=True)
    p=soft.initial.clone().requires_grad_();levers=torch.zeros(5,3,dtype=torch.float64)
    _,a=off.evaluate(p,levers);_,b=on.evaluate(p,levers)
    grad=torch.autograd.grad(b['loss']-a['loss'],p)[0]
    torch.testing.assert_close(grad[:10],torch.full((10,21),.006,dtype=p.dtype))
    assert torch.count_nonzero(grad[10:])==0
    blocks=ResidualBlocks();soft.evaluate(p,levers,refresh_projection=True,projection_gap=False,residual_blocks=blocks)
    # Ten old single-frame rows and nine old pairs are consumed; all eleven-
    # sample stencils reach new time, including the first cross-boundary one.
    assert off.ownership['orientation']['excluded']==10*12
    assert off.ownership['pose_smoothness']['excluded']==9*9
    assert off.ownership['acceleration']['excluded']==0
    expected=sum(v.reshape(-1)[off.masks[n]].square().sum() for n,v in blocks.items())
    torch.testing.assert_close(a['loss'],expected)
    torch.testing.assert_close(a['acceleration_loss_by_scale'].mean(),a['acceleration_loss'])
    snap=_snapshot(b,0,'combined',float(b['loss'].detach()))
    np.testing.assert_allclose(sum(snap['energy_components'].values()),float(b['loss'].detach()),rtol=1e-12)
    with pytest.raises(ValueError,match='time correspondence'):
        RuntimeInformationObjective(soft,q['time_s'],q['time_s'][:10]+.001,factor,use_history=True)


def test_runtime_gap_process_is_accounted_without_reinstating_invalid_measurements():
    actions,*_=fixture();q=next(iter(actions.values()))
    q={k:np.concatenate((v,v),axis=0) for k,v in q.items()}
    q['time_s']=np.arange(len(q['valid']))*.05
    q['valid'][30]=False
    soft=SoftObservationObjective(PoseObjective(**q,geometry=geometry()))
    factor=dict(matrix=np.eye(210),residual=np.zeros(210),point=soft.initial[:10].numpy().reshape(-1),constant=0.)
    plain=RuntimeInformationObjective(soft,q['time_s'],q['time_s'][:10],factor,use_history=False)
    bridge=RuntimeInformationObjective(soft,q['time_s'],q['time_s'][:10],factor,use_history=False,bridge_missing_state=True)
    p=soft.initial.clone();p[31,0]+=.1
    levers=torch.zeros(5,3,dtype=torch.float64)
    _,a=plain.evaluate(p,levers);_,b=bridge.evaluate(p,levers)
    assert b['gap_process_loss']>0
    torch.testing.assert_close(b['loss']-a['loss'],b['gap_process_loss'])
    torch.testing.assert_close(a['acceleration_loss'],b['acceleration_loss'])
    torch.testing.assert_close(a['orientation_likelihood_loss'],b['orientation_likelihood_loss'])
    snapshot=_snapshot(b,0,'combined',float(b['loss']))
    np.testing.assert_allclose(sum(snapshot['energy_components'].values()),float(b['loss']),rtol=1e-12)
