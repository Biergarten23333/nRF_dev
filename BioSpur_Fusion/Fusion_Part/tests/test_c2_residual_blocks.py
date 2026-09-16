"""Residual exports must preserve the live objective and its derivatives."""
import copy
import numpy as np
import pytest
import torch
from test_c2_shared_fit import fixture, geometry
from biospur_fusion.c2_five_calibration.solver import PoseObjective
from biospur_fusion.c2_five_calibration.soft_observation import SoftObservationObjective
from biospur_fusion.c2_five_calibration.residual_blocks import record_mean


@pytest.mark.parametrize('refresh,gap', [(False,False),(True,False),(True,True)])
def test_export_matches_energy_and_joint_gradients(refresh,gap):
    torch.set_num_threads(1)
    actions,*_=fixture();q=next(iter(actions.values()))
    base=PoseObjective(**q,geometry=geometry());obj=SoftObservationObjective(base)
    p=obj.initial.clone();p[:,0]=torch.linspace(.01,.08,len(p));p[:,9]=torch.linspace(.02,.05,len(p))
    p.requires_grad_();lever=torch.full((5,3),.01,dtype=torch.float64,requires_grad=True)
    blocks={}
    r,t=obj.evaluate(p,lever,refresh_projection=refresh,projection_gap=gap,residual_blocks=blocks)
    energy=sum(v.square().sum() for v in blocks.values())
    torch.testing.assert_close(energy,t['loss'],atol=1e-12,rtol=1e-12)
    g=torch.autograd.grad(energy,(p,lever),retain_graph=True)
    expected=torch.autograd.grad(t['loss'],(p,lever),retain_graph=True)
    for a,b in zip(g,expected):torch.testing.assert_close(a,b,atol=1e-10,rtol=1e-10)
    rr,tt=obj.evaluate(p,lever,refresh_projection=refresh,projection_gap=gap)
    torch.testing.assert_close(r,rr,atol=0,rtol=0)
    torch.testing.assert_close(t['loss'],tt['loss'],atol=0,rtol=0)
    assert blocks['acceleration'].ndim==4
    assert blocks['orientation'].shape==(len(p),4,3)


def test_protocol_cannot_silently_disappear():
    actions,*_=fixture();base=PoseObjective(**next(iter(actions.values())),geometry=geometry())
    obj=SoftObservationObjective(base,protocol=lambda p,r:p.square().mean())
    with pytest.raises(ValueError,match='incomplete'):
        obj.evaluate(obj.initial,torch.zeros(5,3),residual_blocks={})


def test_individual_rows_preserve_rank_at_zero_residual():
    x=torch.zeros(3,dtype=torch.float64,requires_grad=True)
    def export(x):
        b={};record_mean(b,'test',x,3.);return b['test']
    torch.testing.assert_close(torch.autograd.functional.jacobian(export,x),torch.eye(3,dtype=x.dtype))
    b={};record_mean(b,'test',x)
    with pytest.raises(ValueError,match='duplicate'):record_mean(b,'test',x)


def test_enabled_body_and_axial_blocks_match_live_energy():
    from test_c2_body_feasibility import rig
    actions,*_=fixture();q=next(iter(actions.values()));g=rig()
    g['forearm_axial_envelope']=dict(limit_deg=20.,scale_deg=10.,weight=.7)
    obj=SoftObservationObjective(PoseObjective(**q,geometry=g))
    p=obj.initial.clone();p[:,7:9]=.8;p.requires_grad_()
    blocks={};_,terms=obj.evaluate(p,torch.zeros(5,3,dtype=torch.float64),residual_blocks=blocks)
    energy=sum(v.square().sum() for v in blocks.values())
    assert blocks['forearm_axial'].square().sum()>0
    assert {'body_inner','body_outer','body_swing','body_twist','body_posterior'}<=blocks.keys()
    torch.testing.assert_close(energy,terms['loss'],atol=1e-12,rtol=1e-12)
    actual,=torch.autograd.grad(energy,p,retain_graph=True)
    expected,=torch.autograd.grad(terms['loss'],p)
    torch.testing.assert_close(actual,expected,atol=1e-10,rtol=1e-10)
